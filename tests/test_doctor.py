#!/usr/bin/env python3
"""Tests for the doctor. Run: python3 tests/test_doctor.py

Each test works on a throwaway copy of the plugin with HOME redirected, so the
real machine's settings and plugin folder are never read or changed.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def copy_plugin(base):
    dest = os.path.join(base, "plugin")
    shutil.copytree(ROOT, dest, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    return dest


def replace_in(path, old, new):
    with open(path) as fh:
        text = fh.read()
    assert old in text, (path, old)
    with open(path, "w") as fh:
        fh.write(text.replace(old, new))


def snapshot(root):
    snap = {}
    for dirpath, dirnames, files in os.walk(root):
        for name in dirnames + files:
            p = os.path.join(dirpath, name)
            st = os.stat(p)
            snap[p] = (st.st_size, st.st_mtime_ns)
    return snap


class DoctorBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = self._tmp.name
        self.home = os.path.join(self.base, "home")
        os.makedirs(os.path.join(self.home, ".claude"))
        self.plugin = copy_plugin(self.base)

    def tearDown(self):
        self._tmp.cleanup()

    def run_doctor(self, args=None, env=None, path=None, skip_installed=True):
        e = dict(os.environ)
        e["HOME"] = self.home
        for key in ("MODEL_PLANNER_GUARD", "MODEL_PLANNER_ALLOW_FABLE", "CLAUDE_CODE_SUBAGENT_MODEL"):
            e.pop(key, None)
        e.update(env or {})
        if path is not None:
            e["PATH"] = path
        cmd = [sys.executable, os.path.join(self.plugin, "scripts", "doctor.py")]
        if skip_installed:
            cmd.append("--skip-installed")
        p = subprocess.run(cmd + (args or []), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=e, timeout=120)
        return p.returncode, p.stdout.decode()

    def fake_claude(self, live_text="", list_text=""):
        bindir = os.path.join(self.base, "bin")
        os.makedirs(bindir, exist_ok=True)
        script = os.path.join(bindir, "claude")
        with open(script, "w") as fh:
            fh.write('#!/bin/sh\nif [ "$1" = "plugin" ]; then printf \'%s\\n\' "$FAKE_LIST"; else printf \'%s\\n\' "$FAKE_LIVE"; fi\n')
        os.chmod(script, 0o755)
        return {"FAKE_LIVE": live_text, "FAKE_LIST": list_text, "PATH": bindir + os.pathsep + os.environ["PATH"]}


class Healthy(DoctorBase):
    def test_healthy_install_passes_and_is_read_only(self):
        before = (snapshot(self.plugin), snapshot(self.home))
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        self.assertNotIn("[FAIL]", out)
        self.assertIn("[PASS] Guard self-test: all 8 cases", out)
        self.assertIn("[PASS] The four tier agents", out)
        self.assertIn("Healthy", out)
        self.assertEqual((snapshot(self.plugin), snapshot(self.home)), before, "doctor must not write anything")


class BrokenGuardIsCaught(DoctorBase):
    def test_guard_that_silently_does_nothing(self):
        with open(os.path.join(self.plugin, "scripts", "guard.py"), "w") as fh:
            fh.write("import sys\nsys.exit(0)\n")
        code, out = self.run_doctor()
        self.assertEqual(code, 1, out)
        self.assertIn("NOT blocked (should be)", out)
        self.assertIn("PROBLEMS FOUND", out)

    def test_guard_that_crashes(self):
        with open(os.path.join(self.plugin, "scripts", "guard.py"), "w") as fh:
            fh.write("raise SystemExit(3)\n")
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertIn("hook failed with exit code 3", out)

    def test_guard_missing(self):
        os.remove(os.path.join(self.plugin, "scripts", "guard.py"))
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertIn("Required plugin file is missing: scripts/guard.py", out)

    def test_python3_missing_from_path(self):
        empty = os.path.join(self.base, "emptybin")
        os.makedirs(empty)
        code, out = self.run_doctor(path=empty)
        self.assertEqual(code, 1)
        self.assertIn("`python3` was not found on PATH", out)
        self.assertIn("Guard self-test", out)
        self.assertRegex(out, r"\[FAIL\] Guard self-test: \d of 8")

    def test_hooks_json_without_guard(self):
        with open(os.path.join(self.plugin, "hooks", "hooks.json"), "w") as fh:
            json.dump({"hooks": {}}, fh)
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertIn("does not run the guard", out)


class AgentDriftIsCaught(DoctorBase):
    def test_agent_model_drift_from_guard(self):
        replace_in(os.path.join(self.plugin, "agents", "heavy.md"), "model: opus", "model: sonnet")
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertIn("pins sonnet but scripts/guard.py expects opus", out)

    def test_fable_in_an_agent(self):
        replace_in(os.path.join(self.plugin, "agents", "max.md"), "model: opus", "model: fable")
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertIn("not haiku, sonnet or opus", out)

    def test_light_agent_must_be_read_only(self):
        replace_in(os.path.join(self.plugin, "agents", "light.md"), "tools: Read, Grep, Glob, Bash", "tools: Read, Grep, Glob, Bash, Edit")
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertIn("The light agent is not read-only", out)

    def test_light_agent_without_tools_line_inherits_everything(self):
        replace_in(os.path.join(self.plugin, "agents", "light.md"), "tools: Read, Grep, Glob, Bash\n", "")
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertIn("not read-only (tools: everything)", out)

    def test_light_without_omit_claude_md_is_only_a_warning(self):
        replace_in(os.path.join(self.plugin, "agents", "light.md"), "omitClaudeMd: true\n", "")
        code, out = self.run_doctor()
        self.assertEqual(code, 0)
        self.assertIn("[WARN] The light agent loads the project's CLAUDE.md", out)


class SettingsWarnings(DoctorBase):
    def test_settings_that_weaken_the_guard(self):
        with open(os.path.join(self.home, ".claude", "settings.json"), "w") as fh:
            json.dump({"model": "claude-fable-5-2", "env": {"CLAUDE_CODE_SUBAGENT_MODEL": "haiku", "MODEL_PLANNER_GUARD": "off"}}, fh)
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)  # warnings, not failures
        self.assertIn("CLAUDE_CODE_SUBAGENT_MODEL=haiku", out)
        self.assertIn("MODEL_PLANNER_GUARD=off", out)
        self.assertIn("The default model", out)
        self.assertIn("[PASS] Guard self-test: all 8 cases", out)  # self-test ignores the off switch

    def test_guard_off_in_shell_environment(self):
        code, out = self.run_doctor(env={"MODEL_PLANNER_GUARD": "off", "MODEL_PLANNER_ALLOW_FABLE": "on"})
        self.assertEqual(code, 0)
        self.assertIn("MODEL_PLANNER_GUARD=off in this shell's environment", out)
        self.assertIn("MODEL_PLANNER_ALLOW_FABLE=on", out)
        self.assertIn("[PASS] Guard self-test: all 8 cases", out)


class ProjectConfig(DoctorBase):
    def _project(self, content):
        proj = os.path.join(self.base, "proj")
        os.makedirs(proj)
        if content is not None:
            with open(os.path.join(proj, ".model-planner.json"), "w") as fh:
                fh.write(content)
        return proj

    def test_valid_file_is_enforced(self):
        proj = self._project(json.dumps({"heavy_keywords": ["registrations", "firebase deploy"]}))
        code, out = self.run_doctor(["--project", proj])
        self.assertEqual(code, 0, out)
        self.assertIn("[PASS] Project file: 2 keyword(s) found and enforced", out)

    def test_invalid_file_fails(self):
        proj = self._project("{ not json")
        code, out = self.run_doctor(["--project", proj])
        self.assertEqual(code, 1)
        self.assertIn("is invalid, so it is being IGNORED", out)

    def test_empty_keywords_warn(self):
        proj = self._project(json.dumps({"heavy_keywords": ["", "   "]}))
        code, out = self.run_doctor(["--project", proj])
        self.assertEqual(code, 0)
        self.assertIn("no usable keywords", out)

    def test_no_file_is_info_and_project_is_untouched(self):
        proj = self._project(None)
        before = snapshot(proj)
        code, out = self.run_doctor(["--project", proj])
        self.assertEqual(code, 0)
        self.assertIn("[INFO] No .model-planner.json for this project", out)
        self.assertEqual(snapshot(proj), before)

    def test_project_read_only_even_with_file(self):
        proj = self._project(json.dumps({"heavy_keywords": ["registrations"]}))
        before = snapshot(proj)
        self.run_doctor(["--project", proj])
        self.assertEqual(snapshot(proj), before)


class InstalledAndLive(DoctorBase):
    LISTED = "  ❯ model-planner@model-planner\n    Version: 9.9.9\n    Scope: user\n    Status: ✔ enabled\n"
    DISABLED = "  ❯ model-planner@model-planner\n    Version: 9.9.9\n    Scope: user\n    Status: ✘ disabled\n"

    def test_installed_copy_reported(self):
        env = self.fake_claude(list_text=self.LISTED)
        code, out = self.run_doctor(env=env, path=env["PATH"], skip_installed=False)
        self.assertEqual(code, 0, out)
        self.assertIn("[PASS] Installed copy: version 9.9.9, scope user, enabled", out)
        self.assertIn("installed plugin is 9.9.9", out)

    def test_disabled_plugin_fails(self):
        env = self.fake_claude(list_text=self.DISABLED)
        code, out = self.run_doctor(env=env, path=env["PATH"], skip_installed=False)
        self.assertEqual(code, 1)
        self.assertIn("installed (version 9.9.9) but its status is", out)

    def test_not_listed_is_info(self):
        env = self.fake_claude(list_text="nothing here")
        code, out = self.run_doctor(env=env, path=env["PATH"], skip_installed=False)
        self.assertEqual(code, 0)
        self.assertIn("not in `claude plugin list`", out)

    def test_live_pass_when_guard_text_appears(self):
        env = self.fake_claude(live_text="PreToolUse:Agent hook error: model-planner guard: too heavy for the light tier")
        code, out = self.run_doctor(["--live"], env=env, path=env["PATH"])
        self.assertEqual(code, 0, out)
        self.assertIn("[PASS] Live check: real Claude Code ran the hook", out)

    def test_live_fail_when_task_was_not_blocked(self):
        env = self.fake_claude(live_text="[Subagent hand-back] The text below is the final report")
        code, out = self.run_doctor(["--live"], env=env, path=env["PATH"])
        self.assertEqual(code, 1)
        self.assertIn("was NOT blocked", out)

    def test_live_fail_when_plugin_not_loaded(self):
        env = self.fake_claude(live_text="Agent type 'model-planner:light' not found. Available agents: general-purpose")
        code, out = self.run_doctor(["--live"], env=env, path=env["PATH"])
        self.assertEqual(code, 1)
        self.assertIn("does not know the light agent", out)

    def test_live_inconclusive_is_a_warning_not_a_pass(self):
        env = self.fake_claude(live_text="I cannot help with that.")
        code, out = self.run_doctor(["--live"], env=env, path=env["PATH"])
        self.assertEqual(code, 0)
        self.assertIn("[WARN] Live check was inconclusive", out)
        self.assertNotIn("[PASS] Live check", out)

    def test_live_not_run_without_the_flag(self):
        env = self.fake_claude(live_text="model-planner guard:")
        code, out = self.run_doctor(env=env, path=env["PATH"])
        self.assertNotIn("Live check", out)


class SkillFile(unittest.TestCase):
    def test_skill_is_manual_and_runs_the_script(self):
        with open(os.path.join(ROOT, "skills", "doctor", "SKILL.md")) as fh:
            text = fh.read()
        self.assertIn("name: doctor", text)
        self.assertIn("disable-model-invocation: true", text)
        self.assertIn('scripts/doctor.py" --project "$PWD"', text)


if __name__ == "__main__":
    unittest.main(verbosity=1)
