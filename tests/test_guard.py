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


PROJECT_WORDS = ["registrations", "firestore.rules", "firebase deploy"]
PROJECT_TASKS = [
    "Edit firestore.rules so the client can read the data",
    "Show registrations to the page",
    "Run Firebase Deploy to publish the build",
]


class ProjectKeywords(unittest.TestCase):
    def test_project_words_block_sonnet_and_haiku(self):
        for task in PROJECT_TASKS:
            for agent in (LIGHT, STANDARD):
                r = guard.classify({"subagent_type": agent, "prompt": task}, PROJECT_WORDS)
                self.assertEqual(r["decision"], "deny", "%s / %s" % (agent, task))
                self.assertEqual(r["send_to"], "heavy")
                self.assertTrue(any(s.startswith("project:") for s in r["signals"]))
            r = guard.classify({"subagent_type": "general-purpose", "prompt": task, "model": "sonnet"}, PROJECT_WORDS)
            self.assertEqual(r["decision"], "deny")

    def test_without_the_words_the_same_tasks_slip_through(self):
        # Documents why the file exists: the built-in rules cannot know these words.
        for task in PROJECT_TASKS:
            self.assertEqual(guard.classify({"subagent_type": STANDARD, "prompt": task})["decision"], "allow", task)

    def test_heavy_and_max_never_blocked_by_project_words(self):
        for task in PROJECT_TASKS:
            for agent in (HEAVY, MAX):
                self.assertEqual(guard.classify({"subagent_type": agent, "prompt": task}, PROJECT_WORDS)["decision"], "allow")

    def test_matching_is_whole_word_and_literal(self):
        ok = ["Rename the file firestoreXrules", "Mention preregistrations in the readme", "Add a firebase deployment note"]
        for task in ok:
            self.assertEqual(guard.classify({"subagent_type": STANDARD, "prompt": task}, PROJECT_WORDS)["decision"], "allow", task)

    def test_unrelated_work_still_allowed(self):
        r = guard.classify({"subagent_type": STANDARD, "prompt": "Add a loading spinner to the landing page button"}, PROJECT_WORDS)
        self.assertEqual(r["decision"], "allow")


class ProjectConfigFile(unittest.TestCase):
    def _hook(self, cwd, agent, prompt):
        env = dict(os.environ, MODEL_PLANNER_LOG="off")
        payload = json.dumps({"tool_name": "Agent", "cwd": cwd, "tool_input": {"subagent_type": agent, "prompt": prompt}})
        p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "guard.py")], input=payload.encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        return p.returncode, p.stdout.decode().strip()

    def _project(self, base, content, name=".model-planner.json"):
        proj = os.path.join(base, "proj")
        sub = os.path.join(proj, "src", "deep")
        os.makedirs(sub)
        if content is not None:
            with open(os.path.join(proj, name), "w") as fh:
                fh.write(content)
        return proj, sub

    def test_config_found_from_a_subfolder(self):
        import tempfile
        with tempfile.TemporaryDirectory() as base:
            proj, sub = self._project(base, json.dumps({"heavy_keywords": ["Registrations", "  firebase   deploy "]}))
            for cwd in (proj, sub):
                code, out = self._hook(cwd, STANDARD, "Show registrations in the admin page")
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny", cwd)
            code, out = self._hook(sub, STANDARD, "Run firebase deploy now")
            self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")
            code, out = self._hook(sub, STANDARD, "Add a spinner to the button")
            self.assertEqual((code, out), (0, ""))

    def test_no_file_or_bad_file_falls_back_to_builtin_rules_without_crashing(self):
        import tempfile
        bad = [None, "not json", "[]", '{"heavy_keywords": "registrations"}', '{"heavy_keywords": [1, null, {"a": 1}, ""]}', "x" * 30000]
        for content in bad:
            with tempfile.TemporaryDirectory() as base:
                proj, _ = self._project(base, content)
                code, out = self._hook(proj, STANDARD, "Show registrations in the admin page")
                self.assertEqual((code, out), (0, ""), repr(content)[:40])  # ignored: built-ins only
                code, out = self._hook(proj, STANDARD, "Design the authentication architecture")
                self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_status_is_reported_and_keywords_capped(self):
        import tempfile
        with tempfile.TemporaryDirectory() as base:
            proj, _ = self._project(base, json.dumps({"heavy_keywords": ["w%d" % i for i in range(500)] + ["x" * 100]}))
            words, status = guard.load_project_keywords(proj)
            self.assertEqual(status, "ok")
            self.assertEqual(len(words), guard.CONFIG_MAX_KEYWORDS)
            self.assertNotIn("x" * 100, words)
        self.assertEqual(guard.load_project_keywords(None), ([], "none"))
        with tempfile.TemporaryDirectory() as base:
            proj, _ = self._project(base, "not json")
            self.assertEqual(guard.load_project_keywords(proj), ([], "invalid"))


class ThirdPartyDispatch(unittest.TestCase):
    """Dispatches shaped like those of another workflow plugin.

    Superpowers' subagent-driven-development skill says to pick the cheapest
    model per role and to "always specify the model explicitly" on each dispatch
    (read from its published skill text; Superpowers was NOT installed or run for
    these tests). So the guard sees a `model` on an unpinned agent and can check it.
    These are simulated payloads that document the intended interplay.
    """

    def test_cheap_model_is_allowed_for_mechanical_read_only_work(self):
        r = guard.classify({"subagent_type": "general-purpose", "model": "haiku", "prompt": "List every file that imports the date helper and report the paths"})
        self.assertEqual(r["decision"], "allow")

    def test_haiku_implementer_is_sent_up_one_tier_not_blocked_outright(self):
        # Superpowers suggests a cheap model for mechanical implementation. Our light tier is
        # read-only, so the guard asks for Sonnet instead: one extra turn, safer result.
        r = guard.classify({"subagent_type": "general-purpose", "model": "haiku", "prompt": "Implement the isolated function parse_date in utils.py exactly per the spec"})
        self.assertEqual(r["decision"], "deny")
        self.assertEqual(r["send_to"], "standard")
        self.assertIn("model-planner:standard", r["reason"])

    def test_sonnet_implementer_for_ordinary_work_passes(self):
        r = guard.classify({"subagent_type": "general-purpose", "model": "sonnet", "prompt": "Implement the settings page toggle exactly as the plan describes"})
        self.assertEqual(r["decision"], "allow")

    def test_sonnet_reviewer_of_risky_code_is_sent_to_heavy(self):
        for prompt in (
            "Review the diff for security problems and race conditions",
            "Review the migration of the users table",
            "Review the change to the authentication flow",
        ):
            r = guard.classify({"subagent_type": "general-purpose", "model": "sonnet", "prompt": prompt})
            self.assertEqual(r["decision"], "deny", prompt)
            self.assertEqual(r["send_to"], "heavy", prompt)

    def test_full_model_ids_are_understood(self):
        r = guard.classify({"subagent_type": "other-plugin:worker", "model": "claude-haiku-5-5", "prompt": "Design the payments architecture"})
        self.assertEqual(r["decision"], "deny")

    def test_architecture_work_on_opus_is_never_blocked(self):
        r = guard.classify({"subagent_type": "general-purpose", "model": "opus", "prompt": "Design the authentication architecture for the payments service"})
        self.assertEqual(r["decision"], "allow")

    def test_no_model_given_means_the_guard_stays_out_of_it(self):
        # An unpinned dispatch with no model inherits the session model; the guard cannot judge it.
        r = guard.classify({"subagent_type": "general-purpose", "prompt": "Design the authentication architecture for the payments service"})
        self.assertEqual(r["decision"], "allow")
        self.assertIsNone(r["effective"])


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
