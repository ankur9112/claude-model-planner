---
name: doctor
description: Check that the model-planner guard still works - the safety net that keeps heavy work off light models. Run it after installing or updating, and any time you wonder if the guard is active.
disable-model-invocation: true
argument-hint: "[--live]"
allowed-tools: Bash(python3 *)
---

# Doctor

The guard is the safety net that keeps heavy work off light models. If its hook stops running, nothing warns you. This checks that it still works. It is read-only: it changes no file and no setting.

## Steps

1. Run the check for the current project. Add `--live` only if the user passed it in `$ARGUMENTS` (it asks real Claude Code to attempt a bad dispatch, costing about half a cent):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py" --project "$PWD" $ARGUMENTS
   ```

   If the shell says `python3` is not found, that is itself the main finding: the guard runs as `python3` too, so it cannot run either. Say so plainly.

2. Report the result in plain English, problems first:
   - **FAIL** means the guard cannot be relied on. Say exactly that, name each failure, and give its fix.
   - **WARN** means something weakens the guard or wastes tokens. Explain what it changes.
   - **INFO** needs no action.
   - If everything passed, say so in one line, and mention that `--live` also confirms real Claude Code enforces the guard.

3. Do not edit settings, hooks, agents or any project file to fix a finding. Tell the user the fix and let them decide.
