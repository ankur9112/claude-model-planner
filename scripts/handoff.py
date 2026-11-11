#!/usr/bin/env python3
"""Write a handoff file so another AI tool can continue when Claude is out of usage.

Two ways to run it:

  Automatic   python3 handoff.py --auto
              Used by the StopFailure(rate_limit) hook. Reads the hook JSON on
              stdin (cwd, transcript_path) and builds the handoff from the
              transcript and git state. No model is involved.

  Manual      python3 handoff.py --cwd DIR --summary-stdin  < summary.txt
              Used by the /model-planner:handoff skill while Claude still has
              usage left; Claude supplies a proper summary on stdin.

The file is written under ~/.claude/model-planner/handoffs/ and stays on this
machine. Standard library only; works on Python 3.8+.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
OUT_DIR = os.path.join(HOME, ".claude", "model-planner", "handoffs")
TAIL_BYTES = 3000000
OTHER_TOOLS = [
    ("codex", "OpenAI Codex CLI"),
    ("gemini", "Google Gemini CLI"),
    ("opencode", "opencode"),
    ("aider", "aider"),
    ("ollama", "Ollama (local models)"),
    ("lms", "LM Studio CLI (local models)"),
]
NOISE_PREFIXES = ("<system-reminder", "<local-command", "<command-", "<task-notification", "[Request interrupted")


def _texts(message):
    """Plain text pieces of a transcript message, ignoring tool calls and results."""
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return [content]
    out = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                out.append(block["text"])
    return out


def read_transcript(path, max_user=3, max_assistant=2):
    users, assistants = [], []
    if not path or not os.path.isfile(path):
        return users, assistants
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - TAIL_BYTES))
            raw = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return users, assistants
    for line in raw.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # includes the partial first line of the tail
        if not isinstance(entry, dict) or entry.get("isSidechain"):
            continue
        kind = entry.get("type")
        if kind not in ("user", "assistant"):
            continue
        texts = [t.strip() for t in _texts(entry.get("message")) if t and t.strip()]
        texts = [t for t in texts if not t.startswith(NOISE_PREFIXES)]
        if not texts:
            continue
        (users if kind == "user" else assistants).append("\n".join(texts))
    return users[-max_user:], assistants[-max_assistant:]


def git(cwd, *args):
    try:
        p = subprocess.run(
            ["git", "-C", cwd] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=8,
        )
        return p.stdout.decode("utf-8", errors="replace").strip() if p.returncode == 0 else ""
    except Exception:
        return ""


def clip(text, limit):
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "\n… (cut)"


def lines_head(text, n):
    rows = text.splitlines()
    extra = "\n… (%d more)" % (len(rows) - n) if len(rows) > n else ""
    return "\n".join(rows[:n]) + extra


def build(cwd, summary, users, assistants, auto):
    out = ["# Handoff: continue this work", ""]
    out.append("Written %s%s." % (time.strftime("%Y-%m-%d %H:%M"), " automatically, after Claude hit a usage limit" if auto else ""))
    out += ["", "**Project folder:** `%s`" % cwd, ""]
    out += [
        "## For the assistant picking this up",
        "",
        "You are continuing work another AI assistant started. Read this file, then inspect the project",
        "folder before changing anything. Do not assume a step was finished unless the files show it.",
        "Ask before anything irreversible (deleting data, force-pushing, deploying, sending messages).",
        "",
    ]
    if summary:
        out += ["## Summary from the previous assistant", "", summary.strip(), ""]
    if users:
        out += ["## What the user asked most recently", ""]
        for u in users:
            out += ["> " + clip(u, 2000).replace("\n", "\n> "), ""]
    if assistants:
        out += ["## Where the previous assistant got to (its last messages)", ""]
        for a in assistants:
            out += [clip(a, 4000), ""]

    branch = git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    if branch:
        status = git(cwd, "status", "--short")
        stat = git(cwd, "diff", "--stat")
        log = git(cwd, "log", "--oneline", "-5")
        out += ["## Git state", "", "Branch: `%s`" % branch, ""]
        if status:
            out += ["Uncommitted changes:", "```", lines_head(status, 40), "```", ""]
        else:
            out += ["Working tree clean.", ""]
        if stat:
            out += ["```", lines_head(stat, 30), "```", ""]
        if log:
            out += ["Recent commits:", "```", log, "```", ""]
    else:
        out += ["## Git state", "", "Not a git repository (or git unavailable).", ""]
    return "\n".join(out)


def other_tools():
    found = []
    for cmd, label in OTHER_TOOLS:
        path = shutil.which(cmd)
        if not path and cmd == "lms":
            candidate = os.path.join(HOME, ".lmstudio", "bin", "lms")
            path = candidate if os.path.isfile(candidate) else None
        if path:
            found.append((cmd, label))
    return found


def main():
    ap = argparse.ArgumentParser(description="Write a handoff file for another AI tool.")
    ap.add_argument("--auto", action="store_true", help="hook mode: read hook JSON on stdin")
    ap.add_argument("--cwd", default=os.getcwd())
    ap.add_argument("--transcript", default="")
    ap.add_argument("--summary-stdin", action="store_true", help="read a summary from stdin")
    args = ap.parse_args()

    cwd, transcript, summary = args.cwd, args.transcript, ""
    if args.auto:
        try:
            payload = json.load(sys.stdin)
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            cwd = payload.get("cwd") or cwd
            transcript = payload.get("transcript_path") or transcript
    elif args.summary_stdin:
        summary = sys.stdin.read()

    cwd = os.path.abspath(os.path.expanduser(cwd))
    users, assistants = read_transcript(transcript)
    text = build(cwd, summary, users, assistants, args.auto)

    os.makedirs(OUT_DIR, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", os.path.basename(cwd) or "project").strip("-")[:40] or "project"
    path = os.path.join(OUT_DIR, "%s-%s.md" % (time.strftime("%Y%m%d-%H%M%S"), slug))
    with open(path, "w") as fh:
        fh.write(text)
    with open(os.path.join(OUT_DIR, "latest.md"), "w") as fh:
        fh.write(text)

    print("Handoff written: %s" % path)
    print("Also copied to:  %s" % os.path.join(OUT_DIR, "latest.md"))
    tools = other_tools()
    if tools:
        print("Other AI tools found on this machine: %s" % ", ".join("%s (%s)" % (c, l) for c, l in tools))
    else:
        print("No other AI command-line tools were found on this machine.")
    print('To continue: open another AI tool in the project folder and tell it: "Read %s and continue the work."' % path)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # a hook must never break the session
        sys.stderr.write("handoff.py: %s\n" % exc)
        sys.exit(0)
