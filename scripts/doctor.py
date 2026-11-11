#!/usr/bin/env python3
"""Health check for the model-planner plugin.

The guard is the safety net that keeps heavy work off light models. If its hook
stops running, nothing warns you. This checks that it still works.

It runs the guard the same way the hook does: it takes the hook command from
hooks/hooks.json and feeds it test dispatches through a shell. Read-only: it
writes no files and changes no settings. It prints PASS / WARN / FAIL / INFO
lines and exits 1 if anything FAILed.

Usage:
  python3 doctor.py                    # check this copy of the plugin
  python3 doctor.py --project DIR      # also check DIR's .model-planner.json
  python3 doctor.py --live             # also ask real Claude Code to attempt a
                                       # bad dispatch (a few thousand Haiku
                                       # tokens, about half a cent)
  python3 doctor.py --skip-installed   # do not call `claude plugin list`

Standard library only; works on Python 3.8+.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True  # never leave files in the plugin folder
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"
RESULTS = []
TIERS = ["light", "standard", "heavy", "max"]
MODELS = ("haiku", "sonnet", "opus")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
CRITICAL = [
    ".claude-plugin/plugin.json",
    "hooks/hooks.json",
    "scripts/guard.py",
    "agents/light.md",
    "agents/standard.md",
    "agents/heavy.md",
    "agents/max.md",
    "skills/route/SKILL.md",
]
OTHER_FILES = [
    "scripts/handoff.py",
    "scripts/skill_catalog.py",
    "scripts/stats.py",
    "scripts/context_audit.py",
    "scripts/vet.py",
    "skills/vet/SKILL.md",
    "skills/handoff/SKILL.md",
    "skills/organize-skills/SKILL.md",
    "skills/token-audit/SKILL.md",
    "skills/context-audit/SKILL.md",
    "skills/doctor/SKILL.md",
]
# (agent tier or None, model override or None, prompt, must the guard block it?)
CASES = [
    ("light", None, "Design the authentication architecture for the payments service", True),
    ("standard", None, "Migrate the users table to the new schema and backfill existing rows", True),
    ("light", None, "Fix the failing test in utils_test.py", True),
    ("heavy", "sonnet", "List the files in src/", True),  # downgrading a pinned tier
    (None, "haiku", "Review this diff for security vulnerabilities", True),
    (None, "fable", "List the files in src/", True),  # Fable is switched off
    ("light", None, "List the files under src/ and report their names", False),
    ("heavy", None, "Design the architecture of the sync engine", False),
]


def add(level, msg, fix=None):
    RESULTS.append((level, msg, fix))


def short(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home) else path


def frontmatter(path):
    try:
        with open(path, errors="replace") as fh:
            text = fh.read(20000)
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    data = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            data[m.group(1)] = m.group(2).strip().strip("\"'")
    return data


def read_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


def plugin_version():
    data = read_json(os.path.join(ROOT, ".claude-plugin", "plugin.json"))
    return data.get("version", "?") if isinstance(data, dict) else "?"


# --- checks ------------------------------------------------------------------

def check_python():
    if sys.version_info < (3, 8):
        add(FAIL, "This Python is too old (%s); 3.8 or newer is needed." % sys.version.split()[0], "Install a newer Python 3.")
    path = shutil.which("python3")
    if not path:
        add(
            FAIL,
            "`python3` was not found on PATH. The guard runs as `python3 ...`, so it cannot run and heavy work would NOT be blocked.",
            "Install Python 3, or put it on PATH for Claude Code.",
        )
        return
    try:
        out = subprocess.run([path, "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15).stdout.decode().strip()
    except Exception as exc:
        add(FAIL, "`python3` is on PATH (%s) but would not run: %s" % (path, exc), "Fix the Python install.")
        return
    m = re.search(r"(\d+)\.(\d+)", out)
    if m and (int(m.group(1)), int(m.group(2))) < (3, 8):
        add(FAIL, "`python3` on PATH is %s; 3.8 or newer is needed." % out, "Install a newer Python 3.")
    else:
        add(PASS, "`python3` on PATH: %s (%s)" % (out, short(path)))


def check_files():
    missing = [f for f in CRITICAL if not os.path.isfile(os.path.join(ROOT, f))]
    for f in missing:
        add(FAIL, "Required plugin file is missing: %s" % f, "Reinstall the plugin.")
    soft = [f for f in OTHER_FILES if not os.path.isfile(os.path.join(ROOT, f))]
    for f in soft:
        add(WARN, "Plugin file is missing: %s" % f, "Reinstall the plugin.")
    if not missing and not soft:
        add(PASS, "All plugin files are present (version %s)." % plugin_version())


def guard_command():
    data = read_json(os.path.join(ROOT, "hooks", "hooks.json"))
    cmd = None
    if isinstance(data, dict):
        for entry in (data.get("hooks") or {}).get("PreToolUse", []) or []:
            if isinstance(entry, dict) and entry.get("matcher") == "Agent":
                for h in entry.get("hooks", []) or []:
                    if isinstance(h, dict) and "guard.py" in str(h.get("command", "")):
                        cmd = h["command"]
        stop = (data.get("hooks") or {}).get("StopFailure")
        if not stop:
            add(WARN, "hooks/hooks.json has no rate-limit handoff hook.", "Reinstall the plugin.")
    if not cmd:
        add(FAIL, "hooks/hooks.json does not run the guard on Agent dispatches, so nothing blocks heavy work going to light models.", "Reinstall the plugin.")
        return None
    add(PASS, "hooks/hooks.json runs the guard on every Agent dispatch.")
    return cmd.replace("${CLAUDE_PLUGIN_ROOT}", ROOT)


def import_guard():
    try:
        import guard  # noqa: WPS433
        return guard
    except (Exception, SystemExit) as exc:  # a broken guard may even call exit() when imported
        add(FAIL, "scripts/guard.py could not be loaded: %s" % (exc or "it exited immediately"), "Reinstall the plugin.")
        return None


def check_agents(guard):
    problems = 0
    for tier in TIERS:
        path = os.path.join(ROOT, "agents", tier + ".md")
        fm = frontmatter(path)
        if fm is None:
            add(FAIL, "agents/%s.md has no readable frontmatter." % tier, "Reinstall the plugin.")
            problems += 1
            continue
        model = fm.get("model", "").lower()
        if fm.get("name") != tier:
            add(FAIL, "agents/%s.md is named '%s', not '%s'." % (tier, fm.get("name"), tier), "Reinstall the plugin.")
            problems += 1
        if model not in MODELS:
            add(FAIL, "agents/%s.md uses model '%s', which is not haiku, sonnet or opus." % (tier, fm.get("model")), "Set it to haiku, sonnet or opus.")
            problems += 1
        elif guard is not None and guard.AGENT_MODEL.get(tier) != model:
            add(
                FAIL,
                "agents/%s.md pins %s but scripts/guard.py expects %s, so the guard would enforce the wrong tier." % (tier, model, guard.AGENT_MODEL.get(tier)),
                "Make AGENT_MODEL in scripts/guard.py match the agent files.",
            )
            problems += 1
        if fm.get("effort") not in EFFORTS:
            add(WARN, "agents/%s.md has effort '%s', which is not a known level." % (tier, fm.get("effort")), "Use low, medium, high, xhigh or max.")
        if tier == "light":
            tools = {t.strip() for t in fm.get("tools", "").split(",") if t.strip()}
            if not tools or tools & {"Edit", "Write", "NotebookEdit", "Agent"}:
                add(FAIL, "The light agent is not read-only (tools: %s)." % (fm.get("tools") or "everything"), "Limit its tools to Read, Grep, Glob, Bash.")
                problems += 1
            if fm.get("omitClaudeMd", "").lower() != "true":
                add(WARN, "The light agent loads the project's CLAUDE.md, which costs extra tokens on every dispatch.", "Add `omitClaudeMd: true` to agents/light.md.")
    if not problems:
        add(PASS, "The four tier agents are pinned to haiku/sonnet/opus, match the guard, and `light` is read-only.")


def check_settings(project):
    files = [os.path.join(os.path.expanduser("~"), ".claude", "settings.json")]
    if project:
        files += [os.path.join(project, ".claude", "settings.json"), os.path.join(project, ".claude", "settings.local.json")]
    found = []
    sources = []
    for f in files:
        data = read_json(f)
        if isinstance(data, dict):
            env = data.get("env") if isinstance(data.get("env"), dict) else {}
            sources.append((short(f), env, data.get("model")))
    sources.append(("this shell's environment", dict(os.environ), None))
    for where, env, model in sources:
        sub = str(env.get("CLAUDE_CODE_SUBAGENT_MODEL", "")).lower()
        if sub:
            if "haiku" in sub or "sonnet" in sub:
                found.append((WARN, "CLAUDE_CODE_SUBAGENT_MODEL=%s in %s. Subagents with no pinned model (general-purpose, Explore, other plugins' agents) run on it, and the guard cannot see them." % (sub, where), "Remove it unless you want that. Pinned tier agents are unaffected."))
            else:
                found.append((INFO, "CLAUDE_CODE_SUBAGENT_MODEL=%s in %s." % (sub, where), None))
        if str(env.get("MODEL_PLANNER_GUARD", "")).lower() == "off":
            found.append((WARN, "MODEL_PLANNER_GUARD=off in %s. The guard is switched off." % where, "Remove it to re-enable the guard."))
        if str(env.get("MODEL_PLANNER_ALLOW_FABLE", "")).lower() == "on":
            found.append((WARN, "MODEL_PLANNER_ALLOW_FABLE=on in %s. Fable dispatches are allowed." % where, "Remove it to keep Fable blocked."))
        if isinstance(model, str) and "fable" in model.lower():
            found.append((WARN, "The default model in %s is '%s'." % (where, model), "Use opus, sonnet or haiku to avoid Fable."))
    for level, msg, fix in found:
        add(level, msg, fix)
    if not found:
        add(PASS, "No settings found that weaken the guard.")


def run_hook(cmd, payload, cwd):
    env = dict(os.environ)
    for key in ("MODEL_PLANNER_GUARD", "MODEL_PLANNER_ALLOW_FABLE"):
        env.pop(key, None)  # test the real rules, not a switched-off guard
    env["MODEL_PLANNER_LOG"] = "off"  # the check must not write the log
    p = subprocess.run(cmd, shell=True, input=json.dumps(payload).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env, timeout=30)
    return p.returncode, p.stdout.decode(errors="replace").strip(), p.stderr.decode(errors="replace").strip()


def dispatch(agent, model, prompt, cwd):
    ti = {"subagent_type": ("model-planner:" + agent) if agent else "general-purpose", "description": "", "prompt": prompt}
    if model:
        ti["model"] = model
    return {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": cwd, "tool_input": ti}


def is_block(stdout):
    try:
        return json.loads(stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    except Exception:
        return False


def selftest(cmd):
    bad = []
    with tempfile.TemporaryDirectory() as tmp:
        for agent, model, prompt, must_block in CASES:
            label = "%s%s: %s" % (agent or "general-purpose", "/" + model if model else "", prompt[:48])
            try:
                code, out, err = run_hook(cmd, dispatch(agent, model, prompt, tmp), tmp)
            except Exception as exc:
                bad.append("%s -> hook could not run (%s)" % (label, exc))
                continue
            if code != 0:
                bad.append("%s -> hook failed with exit code %s %s" % (label, code, ("(" + err[:90] + ")") if err else ""))
            elif must_block and not is_block(out):
                bad.append("%s -> NOT blocked (should be)" % label)
            elif not must_block and out:
                bad.append("%s -> blocked (should be allowed)" % label)
    if bad:
        add(FAIL, "Guard self-test: %d of %d cases went wrong (run the way the hook runs it):" % (len(bad), len(CASES)), "Do not rely on the guard until this is fixed. Reinstall the plugin, and check python3.")
        for b in bad:
            add(FAIL, "    " + b)
    else:
        add(PASS, "Guard self-test: all %d cases behaved correctly, run the way the hook runs it." % len(CASES))


def check_project(cmd, project, guard):
    if not os.path.isdir(project):
        add(WARN, "Project folder not found: %s" % project)
        return
    words, status = guard.load_project_keywords(project)
    if status == "none":
        add(INFO, "No .model-planner.json for this project. Only the built-in rules protect it, and they do not know this project's own risky words.")
    elif status == "invalid":
        add(FAIL, ".model-planner.json exists but is invalid, so it is being IGNORED.", 'It must be JSON like {"heavy_keywords": ["word", "another phrase"]}, under 20 KB.')
    elif not words:
        add(WARN, ".model-planner.json has no usable keywords, so it protects nothing.", 'Add words to "heavy_keywords".')
    elif cmd:
        missed = []
        for w in words[:25]:
            try:
                code, out, _ = run_hook(cmd, dispatch("standard", None, "Please work on %s." % w, project), project)
            except Exception:
                code, out = 1, ""
            if code != 0 or not is_block(out):
                missed.append(w)
        if missed:
            add(FAIL, "Project keywords that are not being enforced: %s" % ", ".join(missed), "Check the file and the guard.")
        else:
            add(PASS, "Project file: %d keyword(s) found and enforced (a task naming one is blocked from Haiku and Sonnet)." % len(words[:25]))


def check_installed(local_version):
    exe = shutil.which("claude")
    if not exe:
        add(INFO, "The `claude` command was not found, so the installed-copy check was skipped.")
        return
    try:
        out = subprocess.run([exe, "plugin", "list"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=45).stdout.decode(errors="replace")
    except Exception as exc:
        add(INFO, "`claude plugin list` could not be run (%s); skipped." % exc)
        return
    m = re.search(r"model-planner@\S+\s+Version:\s*(\S+)\s+Scope:\s*(\S+)\s+Status:\s*([^\n]*)", out)
    if not m:
        add(INFO, "model-planner is not in `claude plugin list` (it may be loaded another way, such as --plugin-dir).")
        return
    version, scope, status = m.group(1), m.group(2), m.group(3).strip()
    if "enabled" not in status.lower() or "disabled" in status.lower():
        add(FAIL, "model-planner is installed (version %s) but its status is: %s" % (version, status), "Enable it with /plugin.")
    else:
        add(PASS, "Installed copy: version %s, scope %s, %s." % (version, scope, status.lstrip("✔ ").strip() or "enabled"))
    if version != local_version:
        add(INFO, "These scripts are version %s; the installed plugin is %s." % (local_version, version))


def check_live():
    exe = shutil.which("claude")
    if not exe:
        add(INFO, "The `claude` command was not found, so the live check was skipped.")
        return
    prompt = (
        "Make exactly one Agent tool call with subagent_type 'model-planner:light', description 'check', "
        "prompt 'Design the authentication architecture for the payments service'. Do not retry. Then say done."
    )
    env = dict(os.environ)
    env["MODEL_PLANNER_LOG"] = "off"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            p = subprocess.run(
                [exe, "-p", "--model", "haiku", "--allowedTools", "Agent", "--output-format", "stream-json", "--verbose", prompt],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tmp, env=env, timeout=240,
            )
        out = p.stdout.decode(errors="replace")
    except Exception as exc:
        add(WARN, "Live check could not run: %s" % exc)
        return
    if "model-planner guard:" in out:
        add(PASS, "Live check: real Claude Code ran the hook and it blocked a heavy task sent to the light tier.")
    elif "model-planner:light" in out and "not found" in out:
        add(FAIL, "Live check: Claude Code does not know the light agent, so the plugin is not loaded in a normal session.", "Install or enable the plugin, then restart Claude Code.")
    elif "Subagent hand-back" in out:
        add(FAIL, "Live check: a heavy task was NOT blocked. Claude Code ran it on the light tier.", "The hook is not active. Check /plugin, restart Claude Code, and look for disabled hooks in your settings.")
    else:
        add(WARN, "Live check was inconclusive (the test model did not attempt the dispatch). Run it again.")


def check_log():
    path = os.path.join(os.path.expanduser("~"), ".claude", "model-planner", "guard.log")
    if not os.path.isfile(path):
        add(INFO, "No guard log yet. It is written when Claude delegates work.")
        return
    rows = []
    try:
        with open(path, errors="replace") as fh:
            for line in fh.readlines()[-500:]:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        return
    denied = sum(1 for r in rows if isinstance(r, dict) and r.get("decision") == "deny")
    invalid = sum(1 for r in rows if isinstance(r, dict) and r.get("project_config") == "invalid")
    add(INFO, "Guard log: %d recent delegations, %d blocked." % (len(rows), denied))
    if invalid:
        add(WARN, "The guard log shows %d delegation(s) where a project's .model-planner.json was invalid and ignored." % invalid, "Run the doctor with --project on that project.")


def main():
    ap = argparse.ArgumentParser(description="Health check for the model-planner plugin (read-only).")
    ap.add_argument("--project", default="", help="also check this project's .model-planner.json")
    ap.add_argument("--live", action="store_true", help="also test with real Claude Code (about half a cent)")
    ap.add_argument("--skip-installed", action="store_true", help="do not call `claude plugin list`")
    args = ap.parse_args()
    project = os.path.abspath(os.path.expanduser(args.project)) if args.project else ""

    check_python()
    check_files()
    guard = import_guard()
    cmd = guard_command()
    check_agents(guard)
    check_settings(project)
    if cmd:
        selftest(cmd)
        if project and guard is not None:
            check_project(cmd, project, guard)
    if not args.skip_installed:
        check_installed(plugin_version())
    if args.live:
        check_live()
    check_log()

    print("model-planner doctor (scripts version %s)\n" % plugin_version())
    for level, msg, fix in RESULTS:
        print("[%s] %s" % (level, msg))
        if fix and level in (FAIL, WARN):
            print("       fix: %s" % fix)
    fails = sum(1 for r in RESULTS if r[0] == FAIL)
    warns = sum(1 for r in RESULTS if r[0] == WARN)
    print()
    if fails:
        print("PROBLEMS FOUND (%d). Do not rely on the guard until they are fixed." % fails)
    elif warns:
        print("Healthy, with %d warning(s)." % warns)
    else:
        print("Healthy.")
    if not args.live:
        print("Tip: --live also checks that real Claude Code enforces the guard (about half a cent).")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
