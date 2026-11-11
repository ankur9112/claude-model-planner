---
name: vet
description: Check a plugin or skills repo for risky hooks, scripts and instructions BEFORE you install it. Read-only; never runs the code it inspects. Use it on anything third-party.
disable-model-invocation: true
argument-hint: "[folder path or owner/repo]"
allowed-tools: Bash(python3 *)
---

# Vet before you install

A plugin can run commands on your machine through hooks, and a user-scope plugin's hooks run in **every** project you open, including live ones. This reads a plugin's files and shows where to look. It never runs anything from the plugin and never installs it.

## Steps

1. Find the target in `$ARGUMENTS`: a folder already on disk, or a GitHub `owner/repo`. If neither was given, ask.

2. **If it is a GitHub repo, ask before downloading.** Name the repo and say that the clone only downloads files and nothing from it will be run. Only after a clear yes, clone into a brand-new empty folder and scan that:

   ```bash
   VET_DIR="$(mktemp -d)" && git clone --depth 1 --no-recurse-submodules "https://github.com/OWNER/REPO.git" "$VET_DIR/repo"
   ```

   Never run, import or open anything from the clone, and never run its install steps.

3. Scan the folder:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/vet.py" "<folder>"
   ```

   If the shell says `python3` is not found, tell the user; the scan cannot run without it.

4. Read the report with the user, in plain English. For each HIGH and each MEDIUM finding, open the flagged file and read the lines around it before judging, because the scan is keyword-based and some findings are harmless. Say clearly which findings are real concerns and which are false alarms, and why.

5. Give a recommendation: **install**, **install after reading X**, or **do not install**. Never say a plugin is "safe": the scan cannot prove that. Mention anything that applies:
   - hooks that run on every prompt or every tool use
   - a very large number of skills or agents, which adds context cost to every session
   - tools or permissions that go beyond what the plugin claims to do

6. Do not install the plugin, edit settings, or delete the downloaded copy unless the user asks. Tell them where the copy is.
