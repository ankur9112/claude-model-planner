---
name: context-audit
description: Measure what this project loads into context at the start of every session (CLAUDE.md files, rules, imports, skill listing) and show where the weight is. Read-only; only suggests changes.
disable-model-invocation: true
allowed-tools: Bash(python3 *)
---

# Context audit

Every session starts with the project's instruction files already in context, and they ride along on every request. This measures them. It changes nothing.

## Steps

1. Run the audit on the current project:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/context_audit.py" --project "$PWD"
   ```

2. Give the user the result in plain English, biggest cost first:
   - the total, and which files make it up
   - any file over the 200-line guidance, and the sections carrying the most weight
   - large docs that a session might read whole

3. Be honest about what it cannot see. Token figures are estimates (bytes / 4), and the audit excludes the system prompt, tool definitions, MCP tools, auto memory and plugin skills. Tell the user to run `/context` for the live total.

4. If the user wants to cut a file down, work in this order and stop at each step for their approval:
   - Read the sections the audit flagged. The tags come from headings only, so judge each by its content.
   - Propose the exact move for each section: into a path-scoped rule (`.claude/rules/<topic>.md` with a `paths:` list in its frontmatter, which loads only when matching files are touched), or into a docs file mentioned by name.
   - Never propose `@import` as a saving. Imports load at launch together with the file that names them.
   - Never move or delete standing rules, safety rules, or anything about live systems, deploys, or data without the user's explicit yes for that specific section.
   - Make the change only after approval, then re-run the audit and report the before and after.

Do not edit any instruction file as part of simply running this command.
