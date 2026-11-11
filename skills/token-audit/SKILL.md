---
name: token-audit
description: Review where tokens are going and what would cut usage most, using the routing log and Claude Code's own usage reports.
disable-model-invocation: true
allowed-tools: Bash(python3 *)
---

# Token audit

Work through this in order. Report findings as a short ranked list - biggest saving first - and do not change any setting without the user saying yes to that specific change.

## 1. What the routing log shows

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/stats.py" --days 7
```

Read it honestly: it covers delegated work only. If almost nothing is delegated, routing is not where the savings are - look at context size instead (steps 2 and 3).

## 2. Ask the user to run these and share what they show

You cannot run these yourself; they are the user's commands.

- `/usage` - the plan-usage breakdown and its recommendations (each row names a behaviour behind 10% or more of recent usage).
- `/context` - what is filling the context window right now.
- `/skill-doctor` - what each skill costs per turn and how often it is used.

## 3. The checklist (from the Claude Code cost docs)

- **Clear between unrelated tasks.** `/clear` is free; a long session resends its whole history on every request.
- **Model and effort for the session.** Opus for planning and hard work; Sonnet for a long run of simple edits. Lower effort for simple work - thinking is billed as output.
- **Do not set `CLAUDE_CODE_SUBAGENT_MODEL`.** It silently changes the model of every unpinned subagent, which can put heavy work on a light model. Pinned tier agents are the safe way.
- **Keep CLAUDE.md short** (under about 200 lines). Move workflow-specific instructions into skills, which load only when used.
- **Trim the skill listing.** Run `/model-planner:organize-skills` and set rarely used categories to name-only.
- **Turn off MCP servers you are not using** (`/mcp`). Prefer command-line tools such as `gh` where they exist.
- **Delegate bulk reading** (logs, test output, wide searches) to `model-planner:light` so it never enters the main context.
- **Write specific prompts** and give a way to verify the result. Vague requests cause broad scanning and rework.
- **Use plan mode for big tasks** so a wrong direction is caught before it is built.
- **Usage limit reached?** Switching Claude models does not help on a subscription - the limit is shared. Options: wait for the reset (Claude Code can continue automatically after it), or `/model-planner:handoff` to continue in another tool.

## 4. Optional third-party tools

Mention these only if the findings point at the problem each one solves. They are separate projects; the user installs them if they choose to, and the savings each project advertises are its own claims.

| Problem seen in the audit | Tool | Install (from its README) |
|---|---|---|
| Command output (tests, builds, git) is flooding context | RTK - `rtk-ai/rtk` | `brew install rtk` then `rtk init -g` |
| Replies are long and wordy | Caveman - `JuliusBrussee/caveman` | `claude plugin marketplace add JuliusBrussee/caveman && claude plugin install caveman@caveman` |
| Large tool results (browser, logs) fill context | context-mode - `mksglu/context-mode` | `claude plugin marketplace add mksglu/context-mode` then `claude plugin install context-mode@context-mode` |
| Want to spend another provider's quota on some tasks | Codex plugin - `openai/codex-plugin-cc` | `/plugin marketplace add openai/codex-plugin-cc` then `/plugin install codex@openai-codex` |

Each extra layer is one more thing that can break, and compression tools can drop detail. Suggest one at a time and re-measure.
