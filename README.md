# model-planner

A Claude Code plugin that sends each piece of delegated work to the cheapest Claude tier that can do it **properly** - and physically blocks heavy work from reaching a light model.

Only Haiku, Sonnet and Opus are used. Fable is switched off.

## What it does

| Part | What you get |
|---|---|
| **Four tier agents** | `light` (Haiku, low effort, read-only), `standard` (Sonnet, medium), `heavy` (Opus, high), `max` (Opus, extra-high). Each is pinned to its model and effort. |
| **Routing skill** | The rules Claude follows when deciding whether to delegate and to which tier. Loads only when Claude is about to delegate. |
| **Guard hook** | Runs on every delegation. Blocks heavy work from light tiers, blocks downgrading a pinned tier, blocks Fable. It is code, so it applies whether or not Claude follows the skill. |
| **`/model-planner:organize-skills`** | Groups all your installed skills by category into one index file and suggests which to list by name only. |
| **`/model-planner:handoff`** | Writes a handoff file so another AI tool can carry on if Claude runs out of usage. Also written automatically when a turn ends on a rate-limit error. |
| **`/model-planner:token-audit`** | Shows where delegated work went and walks through the biggest token savings. |
| **`/model-planner:context-audit`** | Measures what the current project loads at the start of every session (CLAUDE.md files, rules, imports, skill listing), shows where the weight is, and suggests safe ways to trim. Read-only. |
| **`/model-planner:doctor`** | Checks that the guard still works: runs it the way the hook runs it, checks agents, settings and your project's `.model-planner.json`, and with `--live` has real Claude Code attempt a bad dispatch. Read-only. |
| **`/model-planner:vet`** | Checks a third-party plugin or skills repo BEFORE you install it: hooks and the scripts they run, shell commands in skills, tools and permissions, MCP servers, binaries, install scripts, and text that tries to steer Claude. Static and read-only; it runs nothing from the plugin and asks before downloading anything. |

## Install

In Claude Code:

```
/plugin marketplace add ankur9112/claude-model-planner
/plugin install model-planner@model-planner
```

Needs `python3` on your PATH (standard library only - nothing to install).

To try it without installing, from a clone of this repo:

```bash
claude --plugin-dir .
```

## How the routing works

Claude Code cannot cheaply change the main session's model mid-task - doing so throws away the prompt cache. What it can do is hand work to subagents that each run on their own model and effort. So:

- **Your main session stays on the model you chose** (Opus is the sensible default). It plans, decides and checks.
- **Work it hands off goes to a tier agent.** Searching and test runs go to `light`; a well-specified edit goes to `standard`; anything complex or risky goes to `heavy`; architecture and migrations go to `max`.
- **When unsure, it routes up.**

### The safety model

Three layers, so one failing does not put heavy work on a light model:

1. **The skill** tells Claude what each tier may and may not take.
2. **The guard hook** checks every delegation in code:
   - Anything involving architecture, migrations, security, auth, secrets, payments, regulated data, concurrency, production risk, hard debugging or wide refactors is blocked from Haiku **and** Sonnet.
   - Any editing, deciding, reviewing, debugging or designing is blocked from Haiku, which is also read-only by its tool list.
   - A pinned tier cannot be overridden downwards.
   - The guard can only push work **up** a tier. A false alarm costs some tokens; it never lowers quality.
3. **The agents themselves** are told to stop and reply `ESCALATE:` when a task turns out harder than described, instead of straining.

The guard's rules are covered by tests: `python3 tests/test_guard.py`.

### Per-project danger words

The guard's built-in words are generic, and it cannot read your CLAUDE.md. So work that is risky only in *your* project (say, editing database security rules on a live site) can pass as routine. Fix that with a `.model-planner.json` file at the project root:

```json
{ "heavy_keywords": ["registrations", "firestore.rules", "firebase deploy"] }
```

- Matching is case-insensitive, whole-word and literal (not regular expressions). "firebase deploy" will not match "firebase deployment".
- A match blocks Haiku and Sonnet and sends the task to `heavy`. It never blocks `heavy` or `max`.
- The file is found from the working folder or any folder above it. At most 200 words, 60 characters each.
- If the file is invalid, it is ignored and the built-in rules still apply; the guard log records `project_config: invalid`.
- Without the file, the same tasks are allowed through. A test in `tests/test_guard.py` documents that.

### Using it with other plugins (for example Superpowers)

The guard looks at the `model` that *any* plugin sets on a delegation, not only ours. [Superpowers](https://github.com/obra/superpowers)' subagent-driven-development skill tells Claude to pick the cheapest suitable model per role and to always set the model explicitly, so its dispatches are visible to the guard.

- A cheap model for mechanical read-only work passes.
- A Haiku "implementer" is sent up to Sonnet (our light tier is read-only), costing one extra turn.
- A Sonnet reviewer of security, migration or authentication changes is sent to Opus.
- A delegation with no model set inherits your session model, and the guard cannot judge it.

**Tested only with simulated dispatches** shaped like that skill's published instructions. Superpowers was not installed or run. Check any other plugin with `/model-planner:vet` before installing it.

### What it deliberately does not do

- It does not change your session model or any setting.
- It does not touch delegations to other agents that carry no model override - those inherit your session model, which is the safe default.
- It does not force delegation. A subagent starts with an empty context and has to re-read everything, so delegating small or tightly linked work costs more, not less. Published measurements from a similar plugin ([AqueGen/model-routing](https://github.com/AqueGen/model-routing)) found routing saved about a sixth where delegation was already happening, and cost about a fifth more where it pushed delegation that would not otherwise have happened.

## Token settings on the agents

- **`light` starts without the project's CLAUDE.md** (`omitClaudeMd: true`) and stops after 20 turns (`maxTurns`). It is read-only, so it does not need the project's conventions, and its brief says to run only what it was given. In a measured test with a 115 KB CLAUDE.md, a light dispatch used about 6.2k tokens with the setting and about 33.1k without (four runs, consistent to within 1%). That is roughly 27k tokens saved per light dispatch on a file that size.
- **`standard` stops after 40 turns.** It keeps the project's CLAUDE.md, because it edits code and needs the conventions.
- **`heavy` and `max` have no turn cap** and keep the project's CLAUDE.md.
- **The catch:** `light` does not see your project's rules. The routing skill tells Claude to copy the relevant command and rules into its brief, and to keep any check that can "pass without proving anything" out of `light`. If you would rather `light` always saw CLAUDE.md, delete the `omitClaudeMd` line in `agents/light.md` and accept the extra tokens.
- `omitClaudeMd` needs Claude Code 2.1.271 or later. The caps are judgment values; change them in `agents/*.md`.

## What was tested

- `python3 tests/test_guard.py`, `tests/test_scripts.py`, `tests/test_doctor.py` and `tests/test_vet.py`: 93 tests on the guard rules (including per-project words and Superpowers-style dispatches), the hook protocol, the catalog, the handoff, the context audit, the agent settings, the doctor and the vet scanner.
- The vet tests use a deliberately malicious fixture plugin (download-and-run hooks, credential theft, hidden shell commands in skills, binaries, an outside symlink). They check each risk is flagged, that no file from the fixture is ever executed or changed, and that terminal escape codes in a hostile plugin cannot reach your screen. They also caught a real bug: a hook script written the standard quoted way was not recognised as hook-run, which would have hidden a high-risk finding.
- `vet` on plugins already on this machine: no high-risk findings. Two medium findings on installed design skills were false alarms (skills that tell Claude to *resist* "ignore previous instructions"), which is why the skill has Claude read around each finding.
- The doctor's tests deliberately break a copy of the plugin (a guard that silently does nothing, a guard that crashes, `python3` missing, a wrong model on a tier, a light agent that can edit files) and check each is reported as a FAIL. They also caught a bug in the doctor itself.
- `/model-planner:doctor --live` on a real install: reported healthy, and real Claude Code blocked a heavy task sent to the light tier.
- `claude plugin validate .` passes.
- Live, with the main session on Haiku: `standard` ran on Sonnet 5.5, `heavy` and `max` ran on Opus 5.5, and a dispatch of "design the authentication architecture for the payments service" to `light` was blocked by the guard.
- In that live run, each subagent used about 35k tokens just to start, before doing any work. That is why the routing rules say not to delegate small tasks.

Not tested: that each agent's effort level is applied (the test output does not report it), and the automatic handoff on a real usage limit.

## Honest limits

- **Routing covers delegated work only.** The main session's own usage is not routed. The biggest savings usually come from keeping context small - see `/model-planner:token-audit`.
- **The guard matches keywords.** It is tuned to err upward and will sometimes send a simple task to a higher tier. It cannot understand a task; the skill and the agents' own escalation cover what keywords miss.
- **Vet is a map of where to read, not a verdict.** It is keyword-based and static. It cannot see what a plugin's npm or pip dependencies or MCP servers do once running, can miss cleverly hidden code, and flags harmless text (for example a skill that warns Claude against "ignore previous instructions", or this repo's own `scripts/vet.py`, whose rule list contains the patterns it searches for). It never says a plugin is safe. It only reads folders you already have; the skill asks before cloning anything.
- **The doctor proves the guard works, not that your work is safe.** Without `--live` it simulates the hook by running the command from `hooks/hooks.json` in a shell; it cannot prove Claude Code itself loaded the hook. `--live` does prove that (it costs about half a cent and can be inconclusive if the test model skips the dispatch). It does not check your main-session model or whether a task was routed well.
- **The context audit gives estimates, from a partial view.** Token figures are bytes / 4. It sees instruction files, rules, imports and the personal and project skill listing, but not the system prompt, tool definitions, MCP tools, auto memory or plugin skills; `/context` in Claude Code shows the live total. Its "keep" and "candidate" tags come from headings alone and are hints, not judgments. It never edits anything.
- **Skills cannot be sorted into category folders.** Claude Code only loads skills from `<skills-dir>/<name>/SKILL.md`. The index file is the category structure; the skills stay put.
- **Claude cannot fail over to another provider on its own.** A subscription usage limit is shared by all Claude models, and Anthropic does not support running Claude Code against non-Claude models. The handoff file is the practical route. Whether the automatic handoff hook fires on a subscription limit, rather than an API rate limit, is unconfirmed.

## Settings

| Environment variable | Effect |
|---|---|
| `MODEL_PLANNER_GUARD=off` | Turns the guard off (delegations are still logged). |
| `MODEL_PLANNER_ALLOW_FABLE=on` | Lets delegations request Fable. |
| `MODEL_PLANNER_LOG=off` | Stops the decision log. |

The guard logs each decision to `~/.claude/model-planner/guard.log` - agent, model, decision and the matched signal names. It never logs prompt text.

To change a tier's model or effort, edit the frontmatter in `agents/<tier>.md`. If you change a model, update `AGENT_MODEL` in `scripts/guard.py` to match.

## Layout

```
.claude-plugin/   plugin.json, marketplace.json
agents/           light.md, standard.md, heavy.md, max.md
skills/           route/, organize-skills/, handoff/, token-audit/, context-audit/, doctor/, vet/
hooks/hooks.json  guard on Agent dispatches; handoff on rate-limit stop
scripts/          guard.py, skill_catalog.py, handoff.py, stats.py, context_audit.py, doctor.py, vet.py
tests/            test_guard.py, test_scripts.py, test_doctor.py, test_vet.py
```

## Sources and credit

Built on documented Claude Code features: [subagents](https://code.claude.com/docs/en/sub-agents) (model and effort pinning, model resolution order), [skills](https://code.claude.com/docs/en/skills), [hooks](https://code.claude.com/docs/en/hooks), [model configuration](https://code.claude.com/docs/en/model-config), [managing costs](https://code.claude.com/docs/en/costs) and Anthropic's [model selection guide](https://platform.claude.com/docs/en/about-claude/models/choosing-a-model).

Ideas borrowed from [AqueGen/model-routing](https://github.com/AqueGen/model-routing) (pinned tier agents, escalation, honest measurement) and [Rajvardhman05/claude-model-router](https://github.com/Rajvardhman05/claude-model-router) (route by delegation only; the verify, then effort, then model ladder). No code was copied from either.

## Licence

MIT
