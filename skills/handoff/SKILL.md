---
name: handoff
description: Write a handoff file so another AI tool can continue this work if Claude runs out of usage. Run it when usage is getting low, or before stepping away from long work.
disable-model-invocation: true
allowed-tools: Bash(python3 *)
---

# Handoff

Claude cannot switch itself to another provider when the usage limit is reached, and a subscription limit is shared by every Claude model. What does work is leaving a clear handoff that any other assistant can pick up.

## Steps

1. Write a summary of the current work. Be concrete - the reader has none of this conversation:
   - **Goal:** what the user is trying to achieve, in their words where possible.
   - **Done:** what is finished and verified, with file paths.
   - **In progress:** what is half-done, and exactly where it stops.
   - **Next steps:** an ordered list, specific enough to act on.
   - **Decisions and constraints:** choices already made, and things the user said not to do.
   - **How to verify:** the commands or checks that show the work is correct.
   - **Unverified:** anything you believe but did not check.

2. Save it with the script, passing the summary on stdin:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" --cwd "$PWD" --summary-stdin <<'SUMMARY'
   (the summary from step 1)
   SUMMARY
   ```

3. Tell the user the file path the script printed, which other AI tools it found on this machine, and the one-line instruction for continuing: open the other tool in the project folder and tell it to read the handoff file and continue.

## What happens automatically

The plugin also registers a hook for turns that end on a rate-limit error. It writes a handoff with no model involved, built from the last messages in the transcript and the git state, to `~/.claude/model-planner/handoffs/latest.md`. That automatic file is rougher than the one from this command, and whether the hook fires on a subscription usage limit (as opposed to an API rate limit) has not been confirmed - so run this command before usage runs out when you can.
