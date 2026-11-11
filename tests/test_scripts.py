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


def snapshot(root):
    """Every path under root with its size and mtime, to prove nothing was touched."""
    snap = {}
    for dirpath, dirnames, files in os.walk(root):
        for name in dirnames + files:
            p = os.path.join(dirpath, name)
            st = os.stat(p)
            snap[p] = (st.st_size, st.st_mtime_ns)
    return snap


class ContextAudit(unittest.TestCase):
    def _build(self, base):
        home = os.path.join(base, "home")
        parent = os.path.join(base, "work")
        proj = os.path.join(parent, "proj")
        os.makedirs(os.path.join(home, ".claude"))
        os.makedirs(os.path.join(proj, "docs"))
        os.makedirs(os.path.join(proj, ".claude", "rules"))
        with open(os.path.join(home, ".claude", "CLAUDE.md"), "w") as fh:
            fh.write("# Personal\nBe brief.\n")
        with open(os.path.join(parent, "CLAUDE.md"), "w") as fh:
            fh.write("# Parent folder rules\nShared across projects.\n")
        body = ["# Project", "See @docs/api.md and @missing.md, mail a@b.com, literal `@docs/skip.md`.", ""]
        body += ["## Non-negotiable rules"] + ["Never deploy on Friday. SECRET-SENTINEL-TEXT"] * 60
        body += ["", "## Brand colour matrix"] + ["Row of colour data."] * 200
        with open(os.path.join(proj, "CLAUDE.md"), "w") as fh:
            fh.write("\n".join(body))
        with open(os.path.join(proj, "docs", "api.md"), "w") as fh:
            fh.write("# API\nImports the project file back: @../CLAUDE.md\n" + "endpoint\n" * 50)
        with open(os.path.join(proj, "docs", "skip.md"), "w") as fh:
            fh.write("# Skip\nmust not load\n")
        with open(os.path.join(proj, ".claude", "rules", "always.md"), "w") as fh:
            fh.write("Always run the linter.\n")
        with open(os.path.join(proj, ".claude", "rules", "api-only.md"), "w") as fh:
            fh.write("---\npaths:\n  - src/api/**\n---\nAPI rules only.\n")
        with open(os.path.join(proj, "HUGE.md"), "w") as fh:
            fh.write("filler line of text\n" * 3000)
        return home, proj

    def _run(self, home, proj):
        return run("context_audit.py", ["--project", proj], home)

    def test_report_scope_and_safety(self):
        with tempfile.TemporaryDirectory() as base:
            home, proj = self._build(base)
            before = snapshot(base)
            code, out, err = self._run(home, proj)
            self.assertEqual(code, 0, err)
            self.assertEqual(snapshot(base), before, "audit must be read-only")

            # Loaded at launch: user file, ancestor file, project file, import, always-on rule.
            for needle in ("~/.claude/CLAUDE.md", "work/CLAUDE.md", "proj/CLAUDE.md", "docs/api.md", "rules/always.md"):
                self.assertIn(needle, out)
            self.assertIn("(imported by", out)
            # Not loaded: missing import, code-span import, email, path-scoped rule (listed separately).
            self.assertNotIn("missing.md", out)
            self.assertNotIn("skip.md", out)
            self.assertIn("PATH-SCOPED RULES", out)
            self.assertIn("api-only.md", out)
            launch_part = out.split("PATH-SCOPED RULES")[0]
            self.assertNotIn("api-only.md", launch_part)
            # Oversized file diagnosed with the right tags.
            self.assertIn("lines over the 200-line guidance", out)
            self.assertRegex(out, r"Brand colour matrix.*\[candidate: reads like reference material\]")
            self.assertRegex(out, r"Non-negotiable rules.*\[keep: reads like standing rules\]")
            self.assertIn("HUGE.md", out.split("LARGE DOCS")[1])
            self.assertIn("Do NOT use @import", out)
            # Headings and sizes only - never instruction text.
            self.assertNotIn("SECRET-SENTINEL-TEXT", out)
            self.assertNotIn("Never deploy on Friday", out)

    def test_one_huge_list_of_rules_is_called_out_with_item_stats(self):
        with tempfile.TemporaryDirectory() as base:
            home = os.path.join(base, "home")
            proj = os.path.join(base, "p")
            os.makedirs(os.path.join(home, ".claude"))
            os.makedirs(proj)
            rules = "\n".join("%d. **Rule %d.** %s" % (i, i, "Explanation sentence about the rule. " * 40) for i in range(1, 21))
            with open(os.path.join(proj, "CLAUDE.md"), "w") as fh:
                fh.write("# P\n" + "Intro line.\n" * 210 + "\n## Rules of the house\n" + rules + "\n## Stack\nPython.\n")
            before = snapshot(base)
            code, out, err = self._run(home, proj)
            self.assertEqual(code, 0, err)
            self.assertEqual(snapshot(base), before)
            self.assertIn("but it is the largest block", out)
            self.assertRegex(out, r"-> 20 items, averaging \d+ tok each, longest \d+ tok")
            self.assertIn("a list of 20 items", out)
            self.assertIn("one-line statement of each rule", out)
            self.assertNotIn("Explanation sentence", out)

    def test_import_cycle_terminates_and_counts_each_file_once(self):
        with tempfile.TemporaryDirectory() as base:
            home, proj = self._build(base)
            code, out, _ = self._run(home, proj)
            self.assertEqual(code, 0)
            self.assertEqual(out.count("docs/api.md"), 1)

    def test_empty_project_and_missing_home_files(self):
        with tempfile.TemporaryDirectory() as base:
            home = os.path.join(base, "home")
            proj = os.path.join(base, "empty")
            os.makedirs(home)
            os.makedirs(proj)
            code, out, err = self._run(home, proj)
            self.assertEqual(code, 0, err)
            self.assertIn("no instruction files found", out)
            self.assertIn("No instruction file is over", out)


class AgentSettings(unittest.TestCase):
    def _head(self, name):
        with open(os.path.join(ROOT, "agents", name + ".md")) as fh:
            return fh.read().split("---")[1]

    def test_light_agent_is_lean_and_capped(self):
        head = self._head("light")
        self.assertIn("omitClaudeMd: true", head)
        self.assertRegex(head, r"maxTurns: \d+")
        self.assertIn("model: haiku", head)
        self.assertIn("tools: Read, Grep, Glob, Bash", head)  # still read-only by tool list

    def test_standard_is_capped_but_keeps_project_instructions(self):
        head = self._head("standard")
        self.assertRegex(head, r"maxTurns: \d+")
        self.assertNotIn("omitClaudeMd", head)  # it needs the project's conventions

    def test_heavy_and_max_are_not_capped_and_keep_instructions(self):
        for name in ("heavy", "max"):
            head = self._head(name)
            self.assertNotIn("maxTurns", head)
            self.assertNotIn("omitClaudeMd", head)


if __name__ == "__main__":
    unittest.main(verbosity=1)
