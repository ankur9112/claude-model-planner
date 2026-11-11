---
name: organize-skills
description: Group every installed skill by category into one index file, and suggest which skills to list by name only to save context. Run after installing or removing skills.
disable-model-invocation: true
argument-hint: "[categories to quieten, comma-separated]"
allowed-tools: Bash(python3 *)
---

# Organize skills

Claude Code only finds skills at `<skills-dir>/<name>/SKILL.md`. Sorting them into category folders would stop them loading, so this builds an index instead and leaves every skill where it is.

## Steps

1. Build the index:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/skill_catalog.py"
   ```

   If the user passed categories in `$ARGUMENTS`, add `--quiet "$ARGUMENTS"`.

2. Show the user the category summary the script printed, and say where the index was written (`~/.claude/model-planner/skill-catalog.md`).

3. Point out any skill that looks misfiled. The grouping is keyword-based, so check it against each skill's description rather than trusting it.

4. If the user wants to cut the per-turn cost of the skill listing, explain the option and let them choose:
   - Every listed skill's description is loaded into context on every turn. Setting a skill to **name-only** keeps it invocable but drops its description from the listing.
   - Good candidates are whole categories the user rarely needs in most sessions.
   - Re-run step 1 with `--quiet <category keys>` to print a ready `skillOverrides` block.
   - The user applies it themselves: the `/skills` menu (highlight a skill, press Space to cycle its state, Esc to save), or by pasting the block into their settings file.
   - `skillOverrides` affects personal and project skills only. Skills that come from a plugin are managed through `/plugin` instead.

Do not edit any settings file or move any skill as part of this command unless the user explicitly asks for that specific change.
