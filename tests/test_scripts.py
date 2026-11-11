#!/usr/bin/env python3
"""Tests for the catalog and handoff scripts. Run: python3 tests/test_scripts.py

Everything runs against temporary folders with HOME redirected, so nothing on
the real machine is read or written.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")


def run(script, args, home, stdin=""):
    env = dict(os.environ)
    env["HOME"] = home
    p = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, script)] + args,
        input=stdin.encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    return p.returncode, p.stdout.decode(), p.stderr.decode()


def make_skill(base, name, description, extra=""):
    d = os.path.join(base, name)
    os.makedirs(d)
    with open(os.path.join(d, "SKILL.md"), "w") as fh:
        fh.write("---\nname: %s\ndescription: %s\n%s---\n\nBody\n" % (name, description, extra))
    return d


class Catalog(unittest.TestCase):
    def test_groups_dedupes_and_leaves_skills_untouched(self):
        with tempfile.TemporaryDirectory() as home:
            agents = os.path.join(home, ".agents", "skills")
            personal = os.path.join(home, ".claude", "skills")
            os.makedirs(agents)
            os.makedirs(personal)
            real = make_skill(agents, "animate", "Build an animation with the right easing and motion")
            os.symlink(real, os.path.join(personal, "animate"))  # same skill via symlink
            make_skill(personal, "minimalist-ui", "Clean editorial-style interfaces and frontend design")
            make_skill(personal, "pdf-tools", "Read, merge and split PDF files", "disable-model-invocation: true\n")
            make_skill(os.path.join(personal, ".trash"), "old", "should be ignored")
            before = sorted(os.listdir(personal))

            project = os.path.join(home, "proj")
            os.makedirs(project)
            out = os.path.join(home, "catalog.md")
            code, stdout, stderr = run(
                "skill_catalog.py", ["--out", out, "--project", project, "--quiet", "design-ui,animation-motion"], home
            )
            self.assertEqual(code, 0, stderr)
            text = open(out).read()

            self.assertEqual(text.count("`animate`"), 1, "symlinked skill must appear once")
            self.assertIn("## Animation and motion", text)
            self.assertIn("## Design and frontend UI", text)
            self.assertIn("## Documents and office files", text)
            self.assertIn("`pdf-tools` (manual only)", text)
            self.assertNotIn("should be ignored", text)
            self.assertEqual(sorted(os.listdir(personal)), before, "skills folder must be unchanged")

            block = json.loads(stdout[stdout.index("{"):])
            self.assertEqual(block, {"skillOverrides": {"animate": "name-only", "minimalist-ui": "name-only"}})
            self.assertFalse(os.path.exists(os.path.join(home, ".claude", "settings.json")), "must not write settings")

    def test_empty_machine(self):
        with tempfile.TemporaryDirectory() as home:
            code, stdout, _ = run("skill_catalog.py", ["--out", os.path.join(home, "c.md"), "--project", home], home)
            self.assertEqual(code, 0)
            self.assertIn("0 skills", open(os.path.join(home, "c.md")).read())


class Handoff(unittest.TestCase):
    def _transcript(self, home):
        path = os.path.join(home, "t.jsonl")
        rows = [
            {"type": "user", "message": {"content": "Add a dark mode toggle to the settings page"}},
            {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "secret thoughts"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Toggle added in Settings.tsx; tests not run yet."}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "tool output noise"}]}},
            {"type": "user", "message": {"content": [{"type": "text", "text": "<system-reminder>ignore me</system-reminder>"}]}},
            {"type": "user", "isSidechain": True, "message": {"content": "subagent brief, not the user"}},
        ]
        with open(path, "w") as fh:
            fh.write("this line is not json\n")
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        return path

    def test_auto_mode_from_hook_input(self):
        with tempfile.TemporaryDirectory() as home:
            project = os.path.join(home, "my project")
            os.makedirs(project)
            payload = json.dumps({"hook_event_name": "StopFailure", "cwd": project, "transcript_path": self._transcript(home)})
            code, stdout, stderr = run("handoff.py", ["--auto"], home, stdin=payload)
            self.assertEqual(code, 0, stderr)
            latest = os.path.join(home, ".claude", "model-planner", "handoffs", "latest.md")
            text = open(latest).read()
            self.assertIn("Add a dark mode toggle", text)
            self.assertIn("Toggle added in Settings.tsx", text)
            self.assertIn(project, text)
            for leaked in ("secret thoughts", "tool output noise", "ignore me", "subagent brief"):
                self.assertNotIn(leaked, text)

    def test_manual_mode_with_summary(self):
        with tempfile.TemporaryDirectory() as home:
            code, stdout, stderr = run("handoff.py", ["--cwd", home, "--summary-stdin"], home, stdin="Goal: ship the toggle.\nNext: run tests.")
            self.assertEqual(code, 0, stderr)
            self.assertIn("Handoff written:", stdout)
            text = open(os.path.join(home, ".claude", "model-planner", "handoffs", "latest.md")).read()
            self.assertIn("Goal: ship the toggle.", text)

    def test_auto_mode_survives_garbage_input(self):
        with tempfile.TemporaryDirectory() as home:
            code, _, _ = run("handoff.py", ["--auto"], home, stdin="not json")
            self.assertEqual(code, 0)


class Stats(unittest.TestCase):
    def test_no_log_and_with_log(self):
        with tempfile.TemporaryDirectory() as home:
            code, stdout, _ = run("stats.py", [], home)
            self.assertEqual(code, 0)
            self.assertIn("No delegations logged", stdout)

            # Let the real guard write the log, then read it back.
            for agent, prompt in (("model-planner:light", "List files under src/"), ("model-planner:light", "Design the auth architecture")):
                payload = json.dumps({"tool_name": "Agent", "tool_input": {"subagent_type": agent, "prompt": prompt}})
                run("guard.py", [], home, stdin=payload)
            log = open(os.path.join(home, ".claude", "model-planner", "guard.log")).read()
            self.assertNotIn("Design the auth", log, "prompt text must never be logged")
            code, stdout, _ = run("stats.py", [], home)
            self.assertIn("allowed 1, blocked 1", stdout)


if __name__ == "__main__":
    unittest.main(verbosity=1)
