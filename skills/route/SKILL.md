---
name: route
description: Choose the right Claude tier (Haiku / Sonnet / Opus) and effort for a piece of work. Load BEFORE spawning any subagent or parallel worker, when a delegated result came back weak or with ESCALATE, and when the user asks which model or effort to use.
---

# Model planner: routing rules

Goal: spend the fewest tokens that still get the work done **properly**. Saving tokens never justifies a worse result. Only three model families are used here: Haiku, Sonnet, Opus. **Never request Fable.**

## The three rules that override everything else

1. **When unsure, route up.** A tier too high costs some tokens. A tier too low costs a wrong result, a redo, and the user's trust.
2. **Route by delegation only.** Never try to change the main session's model mid-task; that discards the prompt cache and the reasoning so far. Routing happens when work is handed to a subagent.
3. **Do not delegate just to use a cheaper model.** A subagent starts with an empty context and must re-read everything. Merely starting one costs tens of thousands of tokens of setup (about 35k measured on a typical install, before it does any work). If the work is a few tool calls, or each step depends on the previous one, or it already fits in this context, do it here. Batch related light work into one dispatch rather than several.

## When delegating does pay off

- Bulk reading: wide file sweeps, long logs, big test output. The bulk stays in the subagent; only a short report comes back.
- Genuinely independent pieces that can run in parallel.
- A well-specified task that needs none of this conversation's context.

## Tiers

| Agent | Model / effort | Give it | Never give it |
|---|---|---|---|
| `model-planner:light` | Haiku / low, read-only | Find, list, grep, read logs, run a given test or build command and report, summarise long output, extract fields | Any edit. Any decision, diagnosis, review or design |
| `model-planner:standard` | Sonnet / medium | One well-scoped edit or bug fix with a clear spec, tests for existing code, a small feature in a known place, everyday documents, simple data analysis | Architecture, migrations, security or auth, payments, concurrency, unclear root causes, wide refactors |
| `model-planner:heavy` | Opus / high | Multi-file features, tricky debugging, root-cause analysis, refactors, code or security review, anything touching auth, payments, data, concurrency or production | - |
| `model-planner:max` | Opus / xhigh | Architecture and system design, major migrations, deep research carried to a finished deliverable, problems `heavy` could not crack | Routine work (it is the most expensive tier) |

Use the light tier only when **all** of these hold: read-only, mechanical, and a wrong answer would be obvious when it comes back. If any one fails, go up.

A clear spec does not make a task light. Migrations, performance work and security changes need `heavy` even when fully specified.

## How to dispatch

- Use the agent name as `subagent_type` and **do not pass a `model` override** - each agent is already pinned. Never override a tier downwards.
- Write a self-contained brief: the goal, the exact files or area, what "done" looks like, how to verify, and what to leave alone. The subagent sees none of this conversation.
- Ask for a compact report (conclusions and `path:line`), not file dumps.
- **`light` cannot see the project's CLAUDE.md** (it is set to start without it, to save tokens). You can. So put into its brief the exact command to run and every project rule that bears on it: prerequisites such as an emulator that must be running, forbidden commands, known environment traps.
- **If CLAUDE.md warns that a check can pass without proving anything** (for example tests that pass because of an environment quirk), do not hand that check to `light`. Run it yourself, or use `standard`, which does see CLAUDE.md. Treat any `light` pass as a claim to confirm, not a result.

## Project danger words

The guard only knows generic risk words (security, migration, payments, and so on). It cannot read CLAUDE.md, so it does not know what is dangerous *in this project*. A project can list its own words in a `.model-planner.json` file at its root:

```json
{ "heavy_keywords": ["registrations", "firestore.rules", "firebase deploy"] }
```

Any delegation whose description or brief contains one of those words is blocked from Haiku and Sonnet and sent to `heavy`. When you start work in a project whose CLAUDE.md describes live systems, real user data, payments or deploys, and no such file exists, tell the user once and offer to create it from the words that CLAUDE.md treats as critical. Do not create it without their yes.

## The guard

A hook checks every Agent dispatch. If it denies one with a `model-planner guard:` message, the task was judged too heavy for the tier you picked. **Re-dispatch to the tier it names, or do the work yourself. Do not reword the task to get past it.** The guard only ever pushes work up.

## When a result is not good enough

Climb in this order and stop at the first rung that works:

1. **Let it verify** - give the subagent a test, build or script to check its own work.
2. **Raise effort one level** on the same tier (pass `effort` on the dispatch). Effort buys more checking, not skill the model lacks.
3. **Move up one tier.**

A reply starting with `ESCALATE:` means the subagent hit its limit. Go straight to the next tier up and pass along what it found. Never send the same task back to the same tier unchanged.

## Checking delegated work

A subagent's report is a claim, not proof. Before telling the user something is done, check the part that matters: read the diff, or run the test. Check light-tier findings before building a decision on them.

## Advising the user on the session model

You cannot change the session model; the user does. Say one line, once, only on a clear mismatch:

- Architecture, migration or long unattended work on Sonnet or Haiku: suggest Opus.
- A long run of simple, well-scoped edits on Opus at high effort: suggest Sonnet, or a lower effort.
- The session itself is running on Fable: mention that this setup avoids Fable and suggest `/model opus`.

## Skills by category

If `~/.claude/model-planner/skill-catalog.md` exists and the task falls into a category of work (design, animation, documents and so on), read that file to find the matching skills, then invoke the ones that fit by name. Read it only when needed.
