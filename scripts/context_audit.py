#!/usr/bin/env python3
"""Measure what a project loads into context at the start of every session.

Read-only: it never edits, moves or deletes anything and prints headings and
sizes only, never the text of your instructions.

What it covers (all of this loads at launch, per the Claude Code memory docs):
  - CLAUDE.md, .claude/CLAUDE.md and CLAUDE.local.md in the project folder and
    in every folder above it, plus ~/.claude/CLAUDE.md and the managed-policy file
  - rule files under .claude/rules/ and ~/.claude/rules/ that have no `paths:`
    setting (rules WITH `paths:` load only when matching files are touched)
  - files pulled in with @path imports (up to four hops; imports load together
    with the file that names them and do not reduce its cost)
  - the skill listing for personal and project skills

What it cannot see: the system prompt, tool definitions, MCP tools, auto memory
and plugin skills. Run /context in Claude Code for the live total.

Token figures are estimates (bytes / 4), good for comparing, not for billing.

Usage: python3 context_audit.py [--project DIR] [--top N]
Standard library only; works on Python 3.8+.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import skill_catalog  # noqa: E402  (same folder; reuses its skill finder)

HOME = os.path.expanduser("~")
LINE_GUIDANCE = 200  # "target under 200 lines per CLAUDE.md file" - Claude Code memory docs
IMPORT_MAX_DEPTH = 4  # "maximum depth of four hops" - Claude Code memory docs
SKILL_DESC_CAP = 1536  # per-skill listing cap - Claude Code skills docs
BIG_DOC_TOKENS = 10000  # my threshold for "costly if read whole", not from the docs
BIG_SECTION_TOKENS = 5000  # my threshold for calling out a single oversized block
MANAGED = [
    "/Library/Application Support/ClaudeCode/CLAUDE.md",
    "/etc/claude-code/CLAUDE.md",
]
# Headings that suggest reference material (candidates to move out of CLAUDE.md).
REFERENCE_HINT = re.compile(
    r"matrix|catalog|catalogue|inventory|history|changelog|log\b|glossary|appendix|reference|"
    r"measured|examples?|tokens|palette|table|index|decision",
    re.I,
)
# Headings that suggest rules Claude must always see: never proposed for moving.
RULE_HINT = re.compile(
    r"non-?negotiable|rules?\b|never|must|safety|security|stack|conventions?|"
    r"working agreement|read order|commands?|verification|live|deploy",
    re.I,
)


def tokens(text):
    return len(text.encode("utf-8", errors="replace")) // 4


def read(path):
    try:
        with open(path, errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def strip_code(text):
    """Remove fenced blocks and inline code spans (imports inside them are literal)."""
    out, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            out.append(re.sub(r"`[^`]*`", "", line))
    return "\n".join(out)


IMPORT_RE = re.compile(r"(?<![\w@.`])@((?:\\ |[~\w./-])+)")


def find_imports(text, base_dir):
    found = []
    for m in IMPORT_RE.finditer(strip_code(text)):
        raw = m.group(1).replace("\\ ", " ").rstrip(".,;:)")
        if "/" not in raw and not re.search(r"\.\w+$", raw):
            continue  # a bare @word is a mention, not a path
        path = os.path.expanduser(raw)
        if not os.path.isabs(path):
            path = os.path.join(base_dir, path)
        path = os.path.normpath(path)
        if os.path.isfile(path):
            found.append(path)
    return found


def rule_scoped_by_paths(text):
    """True if the rule file has `paths:` in its frontmatter (loads on demand)."""
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    return end != -1 and re.search(r"(?m)^paths\s*:", text[3:end]) is not None


def ancestors(project):
    d = os.path.abspath(project)
    chain = []
    while True:
        chain.append(d)
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return list(reversed(chain))  # filesystem root first, project last


def collect(project):
    """Return (launch_files, on_demand_rules). Each launch file: dict(path, kind, via)."""
    launch, on_demand, seen = [], [], set()

    def add(path, kind, via=None, depth=0):
        real = os.path.realpath(path)
        if real in seen:
            return
        text = read(path)
        if text is None:
            return
        seen.add(real)
        launch.append({"path": path, "kind": kind, "via": via, "text": text})
        if depth < IMPORT_MAX_DEPTH:
            for imp in find_imports(text, os.path.dirname(path)):
                add(imp, "import", via=path, depth=depth + 1)

    for p in MANAGED:
        if os.path.isfile(p):
            add(p, "managed policy")
    add(os.path.join(HOME, ".claude", "CLAUDE.md"), "user CLAUDE.md")

    def add_rules(rules_dir, label):
        if not os.path.isdir(rules_dir):
            return
        for dirpath, _, files in os.walk(rules_dir, followlinks=True):
            for name in sorted(files):
                if not name.endswith(".md"):
                    continue
                path = os.path.join(dirpath, name)
                text = read(path)
                if text is None:
                    continue
                if rule_scoped_by_paths(text):
                    on_demand.append({"path": path, "tokens": tokens(text)})
                else:
                    add(path, label)

    add_rules(os.path.join(HOME, ".claude", "rules"), "user rule")
    for d in ancestors(project):
        for rel, kind in (("CLAUDE.md", "CLAUDE.md"), (os.path.join(".claude", "CLAUDE.md"), ".claude/CLAUDE.md"), ("CLAUDE.local.md", "CLAUDE.local.md")):
            path = os.path.join(d, rel)
            if os.path.isfile(path) and os.path.realpath(path) != os.path.realpath(os.path.join(HOME, ".claude", "CLAUDE.md")):
                add(path, kind)
        add_rules(os.path.join(d, ".claude", "rules"), "project rule")
    return launch, on_demand


def sections(text):
    """Split markdown into (heading, tokens) by #, ## and ### headings, ignoring code fences."""
    parts, current, buf, fenced = [], "(top of file)", [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and re.match(r"^#{1,3}\s+\S", line):
            body = "\n".join(buf)
            parts.append((current, tokens(body), body))
            current, buf = line.strip().lstrip("#").strip(), []
        buf.append(line)
    body = "\n".join(buf)
    parts.append((current, tokens(body), body))
    return [(h, t, b) for h, t, b in parts if t > 0]


ITEM_START = re.compile(r"^ {0,3}(\d+[.)]|[-*])\s")


def split_items(body):
    """Top-level numbered or bulleted items in a section."""
    items, cur = [], None
    for line in body.splitlines():
        if ITEM_START.match(line):
            if cur is not None:
                items.append("\n".join(cur))
            cur = [line]
        elif cur is not None:
            cur.append(line)
    if cur is not None:
        items.append("\n".join(cur))
    return items


def short(path):
    p = path
    if p.startswith(HOME):
        p = "~" + p[len(HOME):]
    return p


def main():
    ap = argparse.ArgumentParser(description="Measure what a project loads at session start (read-only).")
    ap.add_argument("--project", default=os.getcwd())
    ap.add_argument("--top", type=int, default=8, help="sections to list per oversized file")
    args = ap.parse_args()
    project = os.path.abspath(os.path.expanduser(args.project))

    launch, on_demand = collect(project)
    out = ["Context audit for %s" % short(project), "(token figures are estimates: bytes / 4)", ""]

    total = 0
    out.append("LOADED AT EVERY SESSION START")
    if not launch:
        out.append("  (no instruction files found)")
    for f in launch:
        t = tokens(f["text"])
        total += t
        lines = f["text"].count("\n") + 1
        flag = ""
        if f["kind"] in ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md", "user CLAUDE.md") and lines > LINE_GUIDANCE:
            flag = "   <- %d lines over the %d-line guidance" % (lines - LINE_GUIDANCE, LINE_GUIDANCE)
        via = "  (imported by %s)" % short(f["via"]) if f["via"] else ""
        out.append("  %7d tok  %5d lines  %-16s %s%s%s" % (t, lines, f["kind"], short(f["path"]), via, flag))
    out.append("  %7d tok  total instruction text" % total)
    out.append("")

    # Skill listing (personal + project skills only).
    skills, seen = [], set()
    for root in (os.path.join(HOME, ".claude", "skills"), os.path.join(HOME, ".agents", "skills"), os.path.join(project, ".claude", "skills")):
        for s in skill_catalog.find_skills(root, "x"):
            if s["real"] not in seen:
                seen.add(s["real"])
                skills.append(s)
    costs = sorted(((tokens((s["name"] + " " + s["description"])[:SKILL_DESC_CAP]), s["name"]) for s in skills), reverse=True)
    out.append("SKILL LISTING (personal and project skills, descriptions capped at %d chars)" % SKILL_DESC_CAP)
    out.append("  %7d tok  %d skills. Largest: %s" % (sum(c for c, _ in costs), len(skills), ", ".join("%s %d" % (n, c) for c, n in costs[:4]) or "none"))
    out.append("  (plugin skills are not counted here; /skill-doctor shows every skill's cost and use)")
    out.append("")

    # Oversized instruction files: where the weight is.
    findings = []
    for f in launch:
        if f["kind"] not in ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md", "user CLAUDE.md"):
            continue
        lines = f["text"].count("\n") + 1
        if lines <= LINE_GUIDANCE:
            continue
        secs = sorted(sections(f["text"]), key=lambda x: -x[1])[: args.top]
        file_t = max(tokens(f["text"]), 1)
        out.append("WHERE THE WEIGHT IS IN %s" % short(f["path"]))
        movable, blocks = 0, []
        for h, t, body in secs:
            huge = t >= BIG_SECTION_TOKENS and t >= 0.25 * file_t
            if RULE_HINT.search(h):
                tag = "keep: reads like standing rules" + (", but it is the largest block" if huge else "")
            elif REFERENCE_HINT.search(h):
                tag = "candidate: reads like reference material"
                movable += t
            else:
                tag = "review"
            out.append("  %6d tok  %3d%%  %-60s [%s]" % (t, round(100.0 * t / file_t), h[:60], tag))
            if huge:
                items = split_items(body)
                if len(items) >= 5:
                    sizes = sorted(tokens(i) for i in items)
                    out.append("             -> %d items, averaging %d tok each, longest %d tok" % (len(items), sum(sizes) // len(sizes), sizes[-1]))
                    blocks.append((h, t, len(items)))
                else:
                    blocks.append((h, t, 0))
        findings.append((f["path"], lines, movable, file_t, blocks))
        out.append("")

    big = []
    for name in sorted(os.listdir(project)) if os.path.isdir(project) else []:
        path = os.path.join(project, name)
        if name.lower().endswith(".md") and os.path.isfile(path) and os.path.realpath(path) not in {os.path.realpath(f["path"]) for f in launch}:
            text = read(path)
            if text and tokens(text) >= BIG_DOC_TOKENS:
                big.append((tokens(text), name))
    if big:
        out.append("LARGE DOCS IN THE PROJECT ROOT (not loaded automatically; costly if a session reads them whole)")
        for t, name in sorted(big, reverse=True):
            out.append("  %7d tok  %s" % (t, name))
        out.append("")

    if on_demand:
        out.append("PATH-SCOPED RULES (load only when matching files are touched; not counted above)")
        for r in on_demand:
            out.append("  %7d tok  %s" % (r["tokens"], short(r["path"])))
        out.append("")

    out.append("WHAT TO DO")
    if findings:
        for path, lines, movable, file_t, blocks in findings:
            out.append("  - %s is %d lines (guidance: under %d)." % (short(path), lines, LINE_GUIDANCE))
            if movable:
                out.append("    About %d tok (%d%%) sits in sections that read like reference material." % (movable, round(100.0 * movable / file_t)))
            for h, t, n_items in blocks:
                what = "a list of %d items" % n_items if n_items else "one block"
                out.append("    One section ('%s') is %d%% of the file and is %s." % (h[:50], round(100.0 * t / file_t), what))
                out.append("    If those are long rules, a safe pattern is: keep a one-line statement of each rule in CLAUDE.md, and move each rule's longer explanation")
                out.append("    into a path-scoped rule file for the area it covers. That needs you to review every rule; this tool cannot judge them.")
            out.append("    Options, safest first:")
            out.append("      1. Move reference sections into .claude/rules/<topic>.md with a `paths:` setting, so they load only when matching files are touched.")
            out.append("      2. Move them into a docs file and mention it by name (in backticks). Do NOT use @import: imports load at launch and save nothing.")
            out.append("      3. Leave standing rules and safety rules in CLAUDE.md.")
            out.append("    This tool only suggests. Read each section before moving it; the tags above come from headings alone.")
    else:
        out.append("  - No instruction file is over the %d-line guidance." % LINE_GUIDANCE)
    if big:
        out.append("  - Keep an eye on the large docs above: a session told to read one whole pays for all of it. A short state file with a pointer to details is cheaper.")
    out.append("  - Run /context in Claude Code for the live total (system prompt, tools, MCP, memory) that this script cannot see.")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
