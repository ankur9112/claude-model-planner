#!/usr/bin/env python3
"""Build a category index of installed skills.

Claude Code only discovers skills at <skills-dir>/<name>/SKILL.md, so skills
cannot be physically sorted into category folders without breaking them. This
script leaves every skill where it is and writes one index file that groups
them by category, for Claude to read on demand.

It never moves, edits or deletes a skill, and never edits settings.

Usage:
  python3 skill_catalog.py                       # write ~/.claude/model-planner/skill-catalog.md
  python3 skill_catalog.py --out FILE            # write somewhere else
  python3 skill_catalog.py --root DIR            # scan an extra skills directory (repeatable)
  python3 skill_catalog.py --quiet design-ui,animation-motion
        # also print a skillOverrides block that lists those categories by name only

Standard library only; works on Python 3.8+.
"""
import argparse
import json
import os
import re
import sys
import time

HOME = os.path.expanduser("~")
DEFAULT_OUT = os.path.join(HOME, ".claude", "model-planner", "skill-catalog.md")
MAX_DEPTH = 3
SKIP_DIRS = {".trash", ".git", "node_modules"}

# (category, title, keyword regex). A skill goes to the category with the most hits.
CATEGORIES = [
    ("animation-motion", "Animation and motion", r"animat\w*|motion|transition\w*|easing|micro-interaction\w*"),
    ("image-generation", "Image generation", r"imagegen|image[- ]generation|generat\w* images?|mockups?|logo\w*|brand[- ]?kit|art-direct\w*"),
    ("design-ui", "Design and frontend UI", r"design\w*|\bui\b|frontend|landing pages?|interface\w*|visual\w*|minimalist|redesign\w*|typograph\w*|layout\w*|taste|polish"),
    ("documents-office", "Documents and office files", r"docx|pdf|pptx|xlsx|spreadsheet\w*|slides?|presentation\w*|word document\w*|google (docs|sheets|slides|drive)"),
    ("data-viz", "Charts and data visualisation", r"charts?\b|\bplots?\b|dashboard\w*|data ?viz|data visuali[sz]ation"),
    ("code-quality", "Code quality and review", r"code review|review the|simplif\w*|security review|lint\w*|refactor\w*|bugs?\b|tests?\b"),
    ("research-knowledge", "Research and knowledge", r"research\w*|knowledge graph|graphify|literature|sources|memory"),
    ("automation-config", "Automation and configuration", r"schedul\w*|recurring|cron|hooks?\b|settings\.json|keybinding\w*|permission\w*|config\w*"),
    ("skills-plugins", "Skills and plugins", r"skills?\b|plugins?\b|\bmods?\b"),
    ("browser-computer", "Browser and computer control", r"browser|chrome|computer use|desktop control|screenshot\w*"),
]
OTHER = ("other", "Other")


def parse_frontmatter(text):
    """Minimal YAML frontmatter reader: top-level `key: value`, plus folded or
    indented continuation lines. Enough for name/description."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    data, key = {}, None
    for line in text[3:end].splitlines():
        if not line.strip():
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m and not line.startswith((" ", "\t")):
            key, value = m.group(1), m.group(2).strip()
            if value in (">", "|", ">-", "|-", ">+", "|+"):
                value = ""
            data[key] = value.strip("\"'")
        elif key and line.startswith((" ", "\t")):
            data[key] = (data[key] + " " + line.strip()).strip()
    return data


def find_skills(root, source):
    found = []
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        return found
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        depth = dirpath[len(root):].count(os.sep)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if depth >= MAX_DEPTH:
            dirnames[:] = []
        if "SKILL.md" in filenames:
            path = os.path.join(dirpath, "SKILL.md")
            try:
                with open(path, errors="replace") as fh:
                    meta = parse_frontmatter(fh.read(20000))
            except OSError:
                continue
            rel = os.path.relpath(dirpath, root)
            found.append(
                {
                    "name": meta.get("name") or os.path.basename(dirpath),
                    "description": meta.get("description", ""),
                    "path": path,
                    "real": os.path.realpath(path),
                    "source": "synced" if rel.split(os.sep)[0] == "synced" else source,
                    "manual_only": meta.get("disable-model-invocation", "").lower() in ("true", "yes", "on", "1"),
                }
            )
            dirnames[:] = []  # a skill folder's own subfolders are its resources
    return found


def categorise(skill):
    text = (skill["name"].replace("-", " ") + " " + skill["description"]).lower()
    best, best_hits = OTHER, 0
    for key, title, pattern in CATEGORIES:
        hits = len(re.findall(pattern, text))
        # The name is the strongest hint, and its first word most of all
        # ("imagegen-frontend-web" is image generation, not frontend).
        words = skill["name"].lower().replace("_", "-").split("-")
        hits += 2 * len(re.findall(pattern, " ".join(words)))
        if words and re.fullmatch(pattern, words[0]):
            hits += 10
        if hits > best_hits:
            best, best_hits = (key, title), hits
    return best


def shorten(text, limit=160):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def main():
    ap = argparse.ArgumentParser(description="Build a category index of installed skills.")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--root", action="append", default=[], help="extra skills directory to scan")
    ap.add_argument("--quiet", default="", help="comma-separated category keys to suggest as name-only")
    ap.add_argument("--project", default=os.getcwd(), help="project directory (default: current)")
    args = ap.parse_args()

    roots = [
        (os.path.join(HOME, ".claude", "skills"), "personal"),
        (os.path.join(HOME, ".agents", "skills"), "personal"),
        (os.path.join(args.project, ".claude", "skills"), "project"),
    ] + [(r, "extra") for r in args.root]

    skills, seen = [], set()
    for root, source in roots:
        for s in find_skills(root, source):
            if s["real"] in seen:
                continue  # same skill reached through a symlink
            seen.add(s["real"])
            skills.append(s)

    groups = {}
    for s in skills:
        key, title = categorise(s)
        s["category"] = key
        groups.setdefault((key, title), []).append(s)

    lines = [
        "# Skill catalog",
        "",
        "Generated %s by model-planner. %d skills in %d categories." % (time.strftime("%Y-%m-%d %H:%M"), len(skills), len(groups)),
        "Skills stay where they are installed; this file only groups them. Invoke a skill by its name.",
        "",
    ]
    order = [(k, t) for k, t, _ in CATEGORIES] + [OTHER]
    for key, title in order:
        members = groups.get((key, title))
        if not members:
            continue
        lines += ["## %s (`%s`)" % (title, key), "", "| Skill | Source | What it is for |", "|---|---|---|"]
        for s in sorted(members, key=lambda x: x["name"]):
            note = " (manual only)" if s["manual_only"] else ""
            lines.append("| `%s`%s | %s | %s |" % (s["name"], note, s["source"], shorten(s["description"]).replace("|", "/")))
        lines.append("")

    out = os.path.abspath(os.path.expanduser(args.out))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        fh.write("\n".join(lines))

    print("Wrote %s" % out)
    for key, title in order:
        members = groups.get((key, title))
        if members:
            print("  %-20s %2d  %s" % (key, len(members), ", ".join(sorted(m["name"] for m in members))))

    quiet = [q.strip() for q in args.quiet.split(",") if q.strip()]
    if quiet:
        # skillOverrides applies to personal and project skills only (not plugin skills).
        names = sorted(s["name"] for s in skills if s["category"] in quiet and s["source"] in ("personal", "project"))
        print("\nSuggested settings block (nothing was changed). These skills would be listed by name only;")
        print("Claude can still invoke them. Apply it yourself via the /skills menu or your settings file:\n")
        print(json.dumps({"skillOverrides": {n: "name-only" for n in names}}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
