#!/usr/bin/env python3
"""Tests for the guard. Run: python3 tests/test_guard.py

The property that matters most: no heavy task is ever allowed onto a light model.
"""
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import guard  # noqa: E402

LIGHT = "model-planner:light"
STANDARD = "model-planner:standard"
HEAVY = "model-planner:heavy"
MAX = "model-planner:max"


def run(agent, prompt, model=None, description=""):
    ti = {"subagent_type": agent, "prompt": prompt, "description": description}
    if model:
        ti["model"] = model
    return guard.classify(ti)


# Tasks that must never run below the heavy tier.
HEAVY_TASKS = [
    "Design the authentication architecture for the payments service",
    "Migrate the users table to the new schema and backfill existing rows",
    "Find the root cause of the intermittent deadlock in the job queue",
    "Review this diff for security vulnerabilities",
    "Refactor the data layer across the entire codebase",
    "Fix the race condition in the cache invalidation path",
    "Rotate the API keys and update how credentials are stored",
    "Plan the rollback for last night's production incident",
    "Implement OAuth login with JWT refresh",
    "Work out why checkout double-charges some customers",
    "Rewrite the whole sync engine",
    "Assess GDPR compliance of our data retention",
]

# Judgment or write work that must never run on haiku.
NOT_FOR_LIGHT = [
    "Fix the failing test in utils_test.py",
    "Implement a helper that formats dates",
    "Review the pull request",
    "Debug why the build fails",
    "Decide which library we should use",
    "Edit README.md to add an install section",
    "Refactor this function",
    "Explain why this approach is better",
    "Plan the next sprint's work",
    "Commit and push the changes",
    "Optimise the query",
    "Write a script that renames the files",
]

# Mechanical work that the light tier should be allowed to take.
FINE_FOR_LIGHT = [
    "Find every file that imports lodash and list them with line numbers",
    "Run npm test and report which tests fail. Do not fix anything.",
    "Grep the logs directory for ERROR lines from today and summarise the counts",
    "List all TODO comments under src/",
    "Read package.json and report the installed version of react",
    "Count the lines in each file under scripts/",
    "Locate where the login handler is defined and return path:line. Never edit files.",
]


class HeavyNeverGoesLight(unittest.TestCase):
    def test_heavy_tasks_blocked_on_light_agent(self):
        for task in HEAVY_TASKS:
            r = run(LIGHT, task)
            self.assertEqual(r["decision"], "deny", task)
            self.assertEqual(r["send_to"], "heavy", task)

    def test_heavy_tasks_blocked_on_standard_agent(self):
        for task in HEAVY_TASKS:
            r = run(STANDARD, task)
            self.assertEqual(r["decision"], "deny", task)
            self.assertEqual(r["send_to"], "heavy", task)

    def test_heavy_tasks_blocked_with_haiku_or_sonnet_override_on_any_agent(self):
        for task in HEAVY_TASKS:
            for agent in ("general-purpose", "Explore", "some-plugin:worker"):
                for model in ("haiku", "sonnet", "claude-haiku-5-5", "claude-sonnet-5-5"):
                    r = run(agent, task, model=model)
                    self.assertEqual(r["decision"], "deny", "%s / %s / %s" % (task, agent, model))

    def test_signal_in_description_only_still_blocks(self):
        r = run(LIGHT, "Look at src/session.ts", description="Security review")
        self.assertEqual(r["decision"], "deny")

    def test_judgment_and_write_work_blocked_on_light(self):
        for task in NOT_FOR_LIGHT:
            r = run(LIGHT, task)
            self.assertEqual(r["decision"], "deny", task)
            self.assertIn(r["send_to"], ("standard", "heavy"), task)

    def test_negation_does_not_hide_a_later_real_request(self):
        r = run(LIGHT, "Do not stop until you fix the bug in parser.py")
        self.assertEqual(r["decision"], "deny")
        r = run(LIGHT, "Don't summarise - instead redesign the module")
        self.assertEqual(r["decision"], "deny")
        for task in ("Rearchitect the plugin loader", "Reimplement the parser", "Overhaul the settings page", "Rework the cache layer"):
            self.assertEqual(run(LIGHT, task)["decision"], "deny", task)


class DowngradesBlocked(unittest.TestCase):
    def test_pinned_tier_cannot_be_downgraded(self):
        for agent, model in ((HEAVY, "sonnet"), (HEAVY, "haiku"), (MAX, "sonnet"), (MAX, "haiku"), (STANDARD, "haiku"), ("heavy", "haiku")):
            r = run(agent, "List the files in src/", model=model)
            self.assertEqual(r["decision"], "deny", "%s -> %s" % (agent, model))
            self.assertIn("downgrade-of-pinned-tier", r["signals"])

    def test_upgrades_allowed(self):
        self.assertEqual(run(STANDARD, "Fix the typo in the banner text", model="opus")["decision"], "allow")
        self.assertEqual(run(LIGHT, "Design the schema", model="opus")["decision"], "allow")
        self.assertEqual(run(MAX, "Design the architecture", model="opus")["decision"], "allow")

    def test_fable_is_never_dispatched(self):
        for agent in (LIGHT, STANDARD, HEAVY, MAX, "general-purpose"):
            for model in ("fable", "claude-fable-5-2"):
                r = run(agent, "List the files in src/", model=model)
                self.assertEqual(r["decision"], "deny", "%s / %s" % (agent, model))
                self.assertEqual(r["send_to"], "max")

    def test_no_shipped_agent_is_pinned_to_fable(self):
        self.assertNotIn("fable", guard.AGENT_MODEL.values())
        agents_dir = os.path.join(ROOT, "agents")
        for name in os.listdir(agents_dir):
            with open(os.path.join(agents_dir, name)) as fh:
                head = fh.read().split("---")[1]
            self.assertNotIn("fable", head, name)
            self.assertRegex(head, r"\nmodel: (haiku|sonnet|opus)\n", name)


class AllowedPaths(unittest.TestCase):
    def test_mechanical_work_allowed_on_light(self):
        for task in FINE_FOR_LIGHT:
            self.assertEqual(run(LIGHT, task)["decision"], "allow", task)

    def test_scoped_work_allowed_on_standard(self):
        for task in (
            "Fix the off-by-one error in paginate() in src/list.ts and run its tests",
            "Add a --verbose flag to the CLI as described in the spec below",
            "Write unit tests for formatDate()",
        ):
            self.assertEqual(run(STANDARD, task)["decision"], "allow", task)

    def test_heavy_and_max_never_blocked(self):
        for task in HEAVY_TASKS + NOT_FOR_LIGHT + FINE_FOR_LIGHT:
            self.assertEqual(run(HEAVY, task)["decision"], "allow", task)
            self.assertEqual(run(MAX, task)["decision"], "allow", task)

    def test_unpinned_agents_without_override_untouched(self):
        # They inherit the session model; the guard has no opinion.
        for task in HEAVY_TASKS:
            r = run("general-purpose", task)
            self.assertEqual(r["decision"], "allow", task)
            self.assertIsNone(r["effective"])

    def test_malformed_input_does_not_crash(self):
        for bad in (None, [], "x", {}, {"prompt": None, "model": 5, "subagent_type": 7}):
            self.assertEqual(guard.classify(bad)["decision"], "allow")


class HookProtocol(unittest.TestCase):
    """Run the script the way Claude Code does: JSON on stdin, decision on stdout."""

    def _call(self, payload, env_extra=None):
        env = dict(os.environ)
        env["MODEL_PLANNER_LOG"] = "off"
        env.update(env_extra or {})
        p = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "guard.py")],
            input=payload.encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        return p.returncode, p.stdout.decode().strip()

    def _payload(self, agent, prompt, model=None):
        ti = {"subagent_type": agent, "prompt": prompt, "description": ""}
        if model:
            ti["model"] = model
        return json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": ti})

    def test_deny_output_shape(self):
        code, out = self._call(self._payload(LIGHT, "Design the auth architecture"))
        self.assertEqual(code, 0)
        hso = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "PreToolUse")
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertIn("model-planner:heavy", hso["permissionDecisionReason"])

    def test_allow_is_silent(self):
        code, out = self._call(self._payload(LIGHT, "List files under src/"))
        self.assertEqual((code, out), (0, ""))

    def test_other_tools_and_garbage_ignored(self):
        code, out = self._call(json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}))
        self.assertEqual((code, out), (0, ""))
        code, out = self._call("not json")
        self.assertEqual((code, out), (0, ""))

    def test_guard_off_env(self):
        code, out = self._call(self._payload(LIGHT, "Design the auth architecture"), {"MODEL_PLANNER_GUARD": "off"})
        self.assertEqual((code, out), (0, ""))


if __name__ == "__main__":
    unittest.main(verbosity=1)
