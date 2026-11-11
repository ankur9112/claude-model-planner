#!/usr/bin/env python3
"""Static pre-install check for a Claude Code plugin (or skills) folder.

Point it at a folder you have already downloaded. It READS files and prints a
report. It never runs anything from that folder, never follows links outside
it, never downloads anything, and never writes or changes any file.

What it looks at:
  - size, and how much skill/agent text would be added to every session
  - hooks (they run commands automatically) and the scripts those hooks call
  - skill instructions that run shell commands when the skill loads
  - agents' tools, MCP servers, bin/ executables, compiled binaries, symlinks
  - install-time scripts in package.json
  - text that tries to steer Claude against the user

This is a heuristic, keyword-based scan. "Nothing found" is NOT a guarantee of
safety, and some findings will be harmless. It tells you where to look.

Usage: python3 vet.py PATH [--max-per-rule N]
Exit code: 0 normally, 1 if any HIGH finding, 2 if PATH is not a folder.
Standard library only; works on Python 3.8+.
"""
import argparse
import json
import os
import re
import sys

sys.dont_write_bytecode = True

MAX_FILE = 400000
MAX_FILES = 20000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
SCRIPT_EXT = {".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".ps1", ".bat", ".cmd"}
TEXT_EXT = SCRIPT_EXT | {".md", ".json", ".txt", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".html", ".css", ".xml", ""}
BINARY_EXT = {".exe", ".dll", ".so", ".dylib", ".node", ".bin", ".class", ".jar", ".pyc", ".wasm"}
MAGICS = (b"\x7fELF", b"MZ", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe")
EVERY_TURN = {"UserPromptSubmit", "PreToolUse", "PostToolUse", "SessionStart", "Stop"}
SKILL_DESC_CAP = 1536
LARGE_COLLECTION = 50
NPM_INSTALL_SCRIPTS = {"preinstall", "install", "postinstall", "prepare", "prepublish"}
RESERVED_NAME = re.compile(r"^(claude-|anthropic-|anthropics-|cc-plugin-)|official", re.I)
HIGH, MEDIUM, INFO = "HIGH", "MEDIUM", "INFO"

# (level, rule, regex, meaning) - applied to text that RUNS: hook commands,
# scripts, bin/ files and shell blocks inside skills.
RUN_RULES = [
    (HIGH, "pipe-to-shell", r"\b(curl|wget)\b[^\n|;]*\|\s*(sudo\s+)?((ba|z|da)?sh|python3?|node|perl|ruby)\b", "downloads code and runs it straight away"),
    (HIGH, "persistence", r">>?\s*[~\w$/.{}-]*\.(zshrc|bashrc|zprofile|bash_profile|profile)\b|\bcrontab\b|\blaunchctl\b|LaunchAgents|systemctl\s+(enable|--user)|\bdefaults\s+write\b", "changes startup or persistence settings"),
    (HIGH, "credential-access", r"\.ssh/|id_rsa|id_ed25519|\.aws/|\.gnupg|\.netrc|\.git-credentials|\.npmrc|Keychains?\b|security\s+find-(generic|internet)-password|\.config/gh/|credentials\.json", "touches credential or key files"),
    (HIGH, "decoded-code", r"base64\s+(-d|--decode|-D)[^\n]*\|\s*(ba|z)?sh|atob\([^)]*\)[^\n]*(eval|Function)|eval\(\s*(atob|base64|Buffer\.from)", "decodes hidden code and runs it"),
    (MEDIUM, "network", r"\b(curl|wget|nc|ncat|scp|rsync|ssh|sftp)\s|requests\.(get|post|put)|\burllib|http\.client|\bfetch\(|XMLHttpRequest|socket\.(socket|create_connection)|\baxios\b|Invoke-WebRequest", "contacts the network"),
    (MEDIUM, "destructive", r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s|\bmkfs\b|\bdd\s+if=|shutil\.rmtree|\bos\.remove\(|\bunlink\(|Remove-Item\s+-Recurse", "deletes files"),
    (MEDIUM, "privilege", r"\bsudo\b|\bchown\b|\bchmod\s+(-R\s+)?[0-7]*7[0-7]{0,2}\b|\bsetuid\b", "uses elevated privileges or loosens file permissions"),
    (MEDIUM, "runtime-install", r"\bnpx\s+(-y|--yes)\b|\bnpm\s+(i|install)\b|\bpip3?\s+install\b|\bbrew\s+install\b|\bgo\s+install\b|\bcargo\s+install\b", "installs other software when it runs"),
    (MEDIUM, "git-write", r"\bgit\s+(push|reset\s+--hard|clean\s+-f)\b|\bgh\s+(pr|repo|release)\s+(create|merge|delete)", "changes git history or remotes"),
    (MEDIUM, "env-secrets", r"\bprintenv\b|\benv\s*\||\$\{?[A-Z_]*(TOKEN|SECRET|PASSWORD|API_KEY)[A-Z_]*\}?|\.env\b", "reads environment variables or env files that may hold secrets"),
    (MEDIUM, "shell-exec", r"subprocess\.[a-z_]+\([^)]*shell\s*=\s*True|\bos\.system\(|child_process|\bexecSync\(", "builds and runs shell commands at run time"),
    (MEDIUM, "eval-use", r"\beval\b\s*[\(\"'$`]|\bexec\(", "evaluates text as code"),
    (MEDIUM, "long-blob", r"[A-Za-z0-9+/=]{300,}", "contains a long encoded blob"),
    (MEDIUM, "outside-write", r">>?\s*(/etc/|/usr/|/Library/|~/)|\bcp\s+[^\n]*\s~/", "writes outside the project"),
]
PROSE_RULES = [
    (MEDIUM, "prompt-injection", r"ignore\s+(all\s+|any\s+)?(previous|prior|above|earlier)\s+(instructions|rules|messages)|(do\s+not|don't|never)\s+(tell|inform|mention|reveal)[^.\n]{0,30}\buser\b|without\s+(telling|informing|notifying)\s+the\s+user|\bsecretly\b|hidden\s+instruction|disregard\s+(the\s+)?(system|safety)|bypass\s+(the\s+)?(safety|permission|guard)|exfiltrat", "text that tries to steer Claude against the user"),
]
README_RULES = [
    (INFO, "readme-install-script", r"\b(curl|wget)\b[^\n|;]*\|\s*(sudo\s+)?((ba|z|da)?sh|python3?|node)\b", "the README tells you to download and run a script"),
]
RUN_COMPILED = [(lv, rule, re.compile(rx, re.I), why) for lv, rule, rx, why in RUN_RULES]
PROSE_COMPILED = [(lv, rule, re.compile(rx, re.I), why) for lv, rule, rx, why in PROSE_RULES]
README_COMPILED = [(lv, rule, re.compile(rx, re.I), why) for lv, rule, rx, why in README_RULES]
INLINE_BANG = re.compile(r"!`([^`\n]+)`")
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class Report:
    def __init__(self):
        self.findings = []
        self.facts = {}

    def add(self, level, rule, where, line, text, why):
        self.findings.append({"level": level, "rule": rule, "where": where, "line": line, "text": text, "why": why})


def clean(text, limit=110):
    text = CONTROL.sub("?", " ".join(str(text).split()))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def frontmatter(text):
    if not text.startswith("---"):
        return {}, ""
    end = text.find("\n---", 3)
    if end == -1:
        return {}, ""
    block = text[3:end]
    data = {}
    for line in block.splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            data[m.group(1)] = m.group(2).strip().strip("\"'")
    return data, block


def read_text(path):
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_FILE + 1)
    except OSError:
        return None, False
    if len(data) > MAX_FILE:
        return None, True  # too large to scan
    if b"\x00" in data[:2048]:
        return None, False
    return data.decode("utf-8", errors="replace"), False


def level_for(level, wired, is_test):
    if is_test:
        return INFO
    if not wired:
        return MEDIUM if level == HIGH else INFO
    return level


def scan_lines(rep, rules, text, where, wired=True, is_test=False, first_line=1):
    for i, line in enumerate(text.splitlines(), first_line):
        seg = line[:5000]
        for level, rule, rx, why in rules:
            if rx.search(seg):
                rep.add(level_for(level, wired, is_test), rule, where, i, seg, why)


def iter_hooks(obj):
    if not isinstance(obj, dict):
        return
    for event, entries in obj.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict):
                for h in entry.get("hooks", []) or []:
                    if isinstance(h, dict):
                        yield event, entry.get("matcher"), h


def load_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


def hook_maps(root, plugin_json):
    """Yield (source, event_map) for hooks/hooks.json and the manifest's hooks key."""
    data = load_json(os.path.join(root, "hooks", "hooks.json"))
    if isinstance(data, dict):
        yield "hooks/hooks.json", data["hooks"] if isinstance(data.get("hooks"), dict) else data
    value = plugin_json.get("hooks") if isinstance(plugin_json, dict) else None
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, dict):
            yield ".claude-plugin/plugin.json (inline hooks)", item
        elif isinstance(item, str) and not os.path.isabs(item) and ".." not in item:
            data = load_json(os.path.join(root, item))
            if isinstance(data, dict):
                yield item, data["hooks"] if isinstance(data.get("hooks"), dict) else data


def resolve_script(root, command):
    refs = []
    # Handles "${CLAUDE_PLUGIN_ROOT}"/x (the documented, quoted form), ${...}/x and $CLAUDE_PLUGIN_ROOT/x.
    for m in re.finditer(r"\$\{?CLAUDE_PLUGIN_ROOT\}?[\"']?/?([^\s\"'|;&]+)", command):
        rel = m.group(1)
        if ".." not in rel:
            refs.append(os.path.join(root, rel))
    return refs


def vet(root, max_per_rule):
    rep = Report()
    root = os.path.realpath(root)
    facts = rep.facts
    facts.update(files=0, bytes=0, skills=0, agents=0, commands=0, mcp=0, hooks=0, bin=0, binaries=0, symlinks=0, skipped_large=0, listing_tokens=0, stopped=False)

    plugin_json = load_json(os.path.join(root, ".claude-plugin", "plugin.json"))
    market = load_json(os.path.join(root, ".claude-plugin", "marketplace.json"))
    if isinstance(plugin_json, dict):
        facts["name"] = clean(plugin_json.get("name", "?"), 60)
        facts["version"] = clean(plugin_json.get("version", "?"), 30)
        author = plugin_json.get("author")
        facts["author"] = clean(author.get("name") if isinstance(author, dict) else author or "?", 60)
        facts["license"] = clean(plugin_json.get("license", "not stated"), 30)
        if RESERVED_NAME.search(str(plugin_json.get("name", ""))):
            rep.add(MEDIUM, "name-lookalike", ".claude-plugin/plugin.json", 0, plugin_json.get("name"), "the name looks like an Anthropic or official one (Claude Code reserves such names)")
    if isinstance(market, dict) and isinstance(market.get("plugins"), list):
        facts["marketplace_plugins"] = [clean(p.get("name", "?"), 40) for p in market["plugins"] if isinstance(p, dict)][:30]

    hook_script_paths = set()
    hook_lines = []
    for source, emap in hook_maps(root, plugin_json):
        for event, matcher, h in iter_hooks(emap):
            facts["hooks"] += 1
            cmd = h.get("command") or h.get("url") or "(%s hook)" % h.get("type", "?")
            tag = " on EVERY %s" % event if event in EVERY_TURN and not matcher else ""
            hook_lines.append("%s%s%s: %s" % (event, " [%s]" % matcher if matcher else "", tag, clean(cmd, 90)))
            if h.get("command"):
                scan_lines(rep, RUN_COMPILED, str(h["command"]), "hook in %s (%s)" % (source, event), wired=True)
                for ref in resolve_script(root, str(h["command"])):
                    hook_script_paths.add(os.path.realpath(ref))
            if h.get("type") == "http" or str(h.get("url", "")).startswith("http"):
                rep.add(MEDIUM, "network", "hook in %s (%s)" % (source, event), 0, h.get("url"), "a hook sends data to a web address")

    # MCP servers
    mcp_maps = []
    for path in (os.path.join(root, ".mcp.json"),):
        data = load_json(path)
        if isinstance(data, dict):
            mcp_maps.append((".mcp.json", data.get("mcpServers") if isinstance(data.get("mcpServers"), dict) else data))
    if isinstance(plugin_json, dict) and isinstance(plugin_json.get("mcpServers"), dict):
        mcp_maps.append((".claude-plugin/plugin.json", plugin_json["mcpServers"]))
    mcp_lines = []
    for source, servers in mcp_maps:
        for name, cfg in servers.items():
            if not isinstance(cfg, dict):
                continue
            facts["mcp"] += 1
            cmd = " ".join([str(cfg.get("command", ""))] + [str(a) for a in cfg.get("args", []) if isinstance(a, (str, int))])
            url = str(cfg.get("url", ""))
            mcp_lines.append("%s: %s" % (clean(name, 30), clean(cmd.strip() or url, 90)))
            if re.search(r"\b(npx|uvx|bunx|pipx)\b", cmd) and not re.search(r"@\d|==\d|@v\d", cmd):
                rep.add(MEDIUM, "unpinned-mcp", source, 0, cmd, "an MCP server is fetched at launch with no pinned version")
            if url.startswith("http://") and not re.match(r"http://(localhost|127\.0\.0\.1|\[::1\])", url):
                rep.add(MEDIUM, "plain-http", source, 0, url, "an MCP server uses unencrypted http")
            scan_lines(rep, RUN_COMPILED, cmd, "MCP server '%s' in %s" % (clean(name, 30), source), wired=True)

    # walk the tree
    real_root_prefix = root + os.sep
    count = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        rel_dir = os.path.relpath(dirpath, root)
        parts = [] if rel_dir == "." else rel_dir.split(os.sep)
        for d in list(dirnames):
            p = os.path.join(dirpath, d)
            if os.path.islink(p):
                facts["symlinks"] += 1
                target = os.path.realpath(p)
                if not (target + os.sep).startswith(real_root_prefix):
                    rep.add(MEDIUM, "symlink-outside", os.path.relpath(p, root), 0, target, "a link points outside the folder")
                dirnames.remove(d)
        for name in sorted(filenames):
            count += 1
            if count > MAX_FILES:
                facts["stopped"] = True
                break
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            ext = os.path.splitext(name)[1].lower()
            lowparts = [x.lower() for x in parts]
            is_test = any(x in ("tests", "test", "__tests__", "fixtures", "examples") for x in lowparts)
            if os.path.islink(path):
                facts["symlinks"] += 1
                target = os.path.realpath(path)
                if not (target + os.sep).startswith(real_root_prefix):
                    rep.add(MEDIUM, "symlink-outside", rel, 0, target, "a link points outside the folder")
                continue
            try:
                facts["bytes"] += os.path.getsize(path)
            except OSError:
                continue
            facts["files"] += 1
            in_bin = bool(parts) and parts[0] == "bin"
            if in_bin:
                facts["bin"] += 1

            if ext in BINARY_EXT or ext not in TEXT_EXT:
                try:
                    with open(path, "rb") as fh:
                        head = fh.read(8)
                except OSError:
                    head = b""
                if ext in BINARY_EXT or head.startswith(MAGICS):
                    facts["binaries"] += 1
                    rep.add(level_for(MEDIUM, True, False), "binary", rel, 0, name, "compiled code that cannot be read here")
                continue

            text, too_large = read_text(path)
            if too_large:
                facts["skipped_large"] += 1
                continue
            if text is None:
                facts["binaries"] += 1
                rep.add(MEDIUM, "binary", rel, 0, name, "not text; cannot be read here")
                continue

            # Anything inside a hooks/ folder is treated as hook-run too: better a false alarm than a missed one.
            wired = os.path.realpath(path) in hook_script_paths or in_bin or "hooks" in lowparts
            if ext in SCRIPT_EXT or in_bin:
                scan_lines(rep, RUN_COMPILED, text, rel + (" (run by a hook)" if wired else ""), wired=wired, is_test=is_test)
            if name == "package.json":
                pj = load_json(path)
                scripts = pj.get("scripts") if isinstance(pj, dict) else None
                if isinstance(scripts, dict):
                    for key in NPM_INSTALL_SCRIPTS & set(scripts):
                        rep.add(level_for(MEDIUM, True, is_test), "npm-install-script", rel, 0, "%s: %s" % (key, scripts[key]), "runs automatically on npm install")
                        scan_lines(rep, RUN_COMPILED, str(scripts[key]), rel + " (npm %s)" % key, wired=True, is_test=is_test)
            if name.upper().startswith("README") and ext == ".md":
                scan_lines(rep, README_COMPILED, text, rel, wired=True, is_test=is_test)

            is_skill = name == "SKILL.md"
            is_agent = ext == ".md" and "agents" in lowparts and not is_skill
            is_command = ext == ".md" and "commands" in lowparts and not is_skill
            is_instructions = name in ("CLAUDE.md", "AGENTS.md") or ("rules" in lowparts and ext == ".md")
            if not (is_skill or is_agent or is_command or is_instructions):
                continue

            fm, fm_block = frontmatter(text)
            if is_skill:
                facts["skills"] += 1
                desc = (fm.get("name", "") + " " + fm.get("description", ""))[:SKILL_DESC_CAP]
                facts["listing_tokens"] += len(desc) // 4
                tools = fm.get("allowed-tools", "")
                if re.search(r"(?<![\w(])Bash(?![\w(])", tools) or "Bash(*)" in tools:
                    rep.add(level_for(MEDIUM, True, is_test), "broad-shell", rel, 0, "allowed-tools: " + tools, "pre-approves every shell command while the skill runs")
            if is_agent:
                facts["agents"] += 1
                facts["listing_tokens"] += len((fm.get("name", "") + " " + fm.get("description", ""))[:SKILL_DESC_CAP]) // 4
                if not fm.get("tools"):
                    rep.add(level_for(INFO, True, is_test), "agent-all-tools", rel, 0, "no tools line", "this agent gets every tool, including shell and file writes")
                if fm.get("permissionMode", "") in ("bypassPermissions", "auto"):
                    rep.add(level_for(MEDIUM, True, is_test), "agent-skips-prompts", rel, 0, "permissionMode: " + fm.get("permissionMode", ""), "asks to skip permission prompts (Claude Code ignores this for plugin agents)")
            if is_command:
                facts["commands"] += 1
            if re.search(r"(?m)^hooks:", fm_block):
                rep.add(INFO, "frontmatter-hooks", rel, 0, "hooks: ...", "defines hooks in its frontmatter; read them, they are not parsed here")
            if is_test:
                continue
            body_start = text.find("\n---", 3) + 4 if text.startswith("---") else 0
            body = text[body_start:] if body_start > 0 else text
            first_line = text[:body_start].count("\n") + 1 if body_start > 0 else 1
            scan_lines(rep, PROSE_COMPILED, body, rel, wired=True, first_line=first_line)
            if is_skill:
                in_bang = False
                for i, line in enumerate(body.splitlines(), first_line):
                    if line.strip().startswith("```!"):
                        in_bang = True
                        continue
                    if in_bang and line.strip().startswith("```"):
                        in_bang = False
                        continue
                    cmds = [line] if in_bang else INLINE_BANG.findall(line)
                    for c in cmds:
                        rep.add(INFO, "skill-shell-block", rel, i, c, "this skill runs a shell command when it loads")
                        scan_lines(rep, RUN_COMPILED, c, rel + " (runs when the skill loads)", wired=True, first_line=i)
        if facts["stopped"]:
            break

    if facts["skills"] + facts["agents"] >= LARGE_COLLECTION:
        rep.add(MEDIUM, "large-collection", "(whole folder)", 0, "%d skills/agents" % (facts["skills"] + facts["agents"]), "adds about %d tokens of skill and agent listing to every session once installed" % facts["listing_tokens"])
    rep.hook_lines = hook_lines
    rep.mcp_lines = mcp_lines
    return rep


def render(rep, root, max_per_rule):
    f = rep.facts
    out = ["Vet report for %s" % clean(root, 150), "(static scan: files were read, nothing was run, nothing was changed)", ""]
    out.append("WHAT IT IS")
    if "name" in f:
        out.append("  plugin: %s  version %s  author %s  licence %s" % (f["name"], f["version"], f["author"], f["license"]))
    else:
        out.append("  no .claude-plugin/plugin.json found (a skills-only folder, or not a plugin)")
    if f.get("marketplace_plugins"):
        out.append("  marketplace listing %d plugin(s): %s" % (len(f["marketplace_plugins"]), ", ".join(f["marketplace_plugins"][:10])))
    out.append("  %d files, %.1f MB; %d skills, %d agents, %d commands, %d MCP servers, %d hooks, %d bin files, %d binaries, %d symlinks" % (
        f["files"], f["bytes"] / 1e6, f["skills"], f["agents"], f["commands"], f["mcp"], f["hooks"], f["bin"], f["binaries"], f["symlinks"]))
    out.append("  adds about %d tokens of skill/agent listing to every session if installed" % f["listing_tokens"])
    if f["skipped_large"]:
        out.append("  %d file(s) over 400 KB were NOT scanned" % f["skipped_large"])
    if f["stopped"]:
        out.append("  stopped after %d files; the rest were NOT scanned" % MAX_FILES)
    if rep.hook_lines:
        out += ["", "HOOKS (these run commands automatically; installed at user scope they run in EVERY project, including live ones)"]
        out += ["  " + h for h in rep.hook_lines[:15]]
        if len(rep.hook_lines) > 15:
            out.append("  (+%d more)" % (len(rep.hook_lines) - 15))
    if rep.mcp_lines:
        out += ["", "MCP SERVERS"] + ["  " + m for m in rep.mcp_lines[:10]]

    order = {HIGH: 0, MEDIUM: 1, INFO: 2}
    counts = {HIGH: 0, MEDIUM: 0, INFO: 0}
    groups = {}
    for fd in rep.findings:
        counts[fd["level"]] += 1
        groups.setdefault((fd["level"], fd["rule"]), []).append(fd)
    out += ["", "FINDINGS  (HIGH %d, MEDIUM %d, INFO %d)" % (counts[HIGH], counts[MEDIUM], counts[INFO])]
    if not groups:
        out.append("  none")
    for (level, rule), items in sorted(groups.items(), key=lambda kv: (order[kv[0][0]], kv[0][1])):
        out.append("  [%s] %s: %s (%d)" % (level, rule, items[0]["why"], len(items)))
        for fd in items[:max_per_rule]:
            loc = "%s%s" % (clean(fd["where"], 70), ":%d" % fd["line"] if fd["line"] else "")
            out.append("      %s  -> %s" % (loc, clean(fd["text"])))
        if len(items) > max_per_rule:
            out.append("      (+%d more)" % (len(items) - max_per_rule))

    out.append("")
    if counts[HIGH]:
        out.append("VERDICT: REVIEW BEFORE INSTALLING. %d high-risk finding(s). Read each flagged file before deciding." % counts[HIGH])
    elif counts[MEDIUM]:
        out.append("VERDICT: Read the MEDIUM findings first, especially anything run by a hook, before installing.")
    else:
        out.append("VERDICT: Nothing alarming found by these checks. That is not a guarantee; see the limits below.")
    out += [
        "",
        "LIMITS: keyword-based and static. It runs nothing, follows no links, and cannot see what npm/pip dependencies or MCP servers",
        "do once running. It can miss cleverly hidden code and can flag harmless text. Treat it as a map of where to read.",
    ]
    return "\n".join(out), counts


def main():
    ap = argparse.ArgumentParser(description="Static pre-install check for a plugin folder (read-only).")
    ap.add_argument("path")
    ap.add_argument("--max-per-rule", type=int, default=5)
    args = ap.parse_args()
    root = os.path.abspath(os.path.expanduser(args.path))
    if not os.path.isdir(root):
        print("Not a folder: %s" % clean(args.path, 150))
        return 2
    rep = vet(root, args.max_per_rule)
    text, counts = render(rep, root, args.max_per_rule)
    print(text)
    return 1 if counts[HIGH] else 0


if __name__ == "__main__":
    sys.exit(main())
