#!/usr/bin/env python3
"""model-planner guard: a PreToolUse hook on the Agent tool.

It has exactly one job: stop heavy work from being dispatched to a light model.
It can only ever push a dispatch UP a tier (by denying it with a reason).
It never lowers a tier, never rewrites a dispatch, and never touches dispatches
whose model it cannot determine (those inherit the session model).

Reads the hook JSON on stdin, prints a deny decision on stdout when needed.
Standard library only; works on Python 3.8+.

A project can add its own heavy-work words in a .model-planner.json file
({"heavy_keywords": ["registrations", "firebase deploy"]}) at or above the
working directory; those block Haiku and Sonnet the same way the built-in
signals do.

It also refuses any dispatch that asks for the Fable model, which this setup
does not use (it needs paid credits).

Environment:
  MODEL_PLANNER_GUARD=off         disable the guard (dispatches are still logged)
  MODEL_PLANNER_ALLOW_FABLE=on    permit dispatches that request Fable
  MODEL_PLANNER_LOG=off     disable the decision log
"""
import json
import os
import re
import sys
import time

RANK = {"haiku": 1, "sonnet": 2, "opus": 3, "fable": 4}
TIER_OF_MODEL = {"haiku": "light", "sonnet": "standard", "opus": "heavy", "fable": "max"}
# Tier agents shipped by this plugin and the model each is pinned to.
AGENT_MODEL = {"light": "haiku", "standard": "sonnet", "heavy": "opus", "max": "opus"}
PLUGIN_PREFIX = "model-planner:"

LOG_DIR = os.path.join(os.path.expanduser("~"), ".claude", "model-planner")
LOG_FILE = os.path.join(LOG_DIR, "guard.log")
LOG_MAX_BYTES = 1000000

# Optional per-project file with extra words that mark work as heavy in that project.
CONFIG_NAME = ".model-planner.json"
CONFIG_MAX_BYTES = 20000
CONFIG_MAX_KEYWORDS = 200
CONFIG_MAX_KEYWORD_LEN = 60
CONFIG_MAX_LEVELS = 12

# --- Signals -----------------------------------------------------------------
# Each entry: (label, regex). Matched against lower-cased description + prompt
# after negated phrases ("do not fix ...") have been removed.

# Work that needs at least the HEAVY tier (opus). Blocks haiku AND sonnet.
NEEDS_HEAVY = [
    ("architecture", r"\b(re-?)?architect\w*|\bsystem design\b|\bdesign (the|a|an|our|its)\b[^.\n]{0,40}\b(system|architecture|schema|api|protocol|data model|database)\b"),
    ("migration", r"\bmigrat\w*|\bschema change\w*|\bbackfill\w*"),
    ("security", r"\bsecurity\b|\bvulnerab\w*|\bexploit\w*|\bthreat model\w*|\binjection\b|\bxss\b|\bcsrf\b"),
    ("auth", r"\bauthenticat\w*|\bauthori[sz]\w*|\boauth\w*|\bjwt\b|\bsso\b|\bsession hijack\w*"),
    ("crypto-secrets", r"\bencrypt\w*|\bdecrypt\w*|\bcryptograph\w*|\bsecrets?\b|\bcredentials?\b|\bpasswords?\b|\bapi keys?\b|\bprivate keys?\b"),
    ("payments", r"\bpayments?\b|\bbilling\b|\bcheckout\b|\binvoic\w*|\brefunds?\b|\bstripe\b"),
    ("regulated", r"\bpii\b|\bgdpr\b|\bhipaa\b|\bcompliance\b|\blegal\b|\bmedical\b|\bclinical\b|\bfinancial\b|\btax\b"),
    ("concurrency", r"\bconcurren\w*|\brace conditions?\b|\bdeadlock\w*|\bthread[- ]safe\w*|\bmutex\w*|\block[- ]free\b|\bdistributed\b|\bconsensus\b|\btransaction isolation\b"),
    ("production-risk", r"\bproduction\b|\bprod\b|\bdata loss\b|\birreversibl\w*|\bdestructive\b|\bdrop (table|database|column)\b|\btruncate\b|\bforce[- ]push\w*|\brollback\b|\bincident\b|\boutage\b"),
    ("hard-debugging", r"\broot[- ]cause\w*|\bmemory leak\w*|\bintermittent\w*|\bflaky\b|\bheisenbug\w*|\bcannot reproduce\b|\bcan't reproduce\b"),
    ("large-refactor", r"\brefactor\w*[^.\n]{0,60}\b(across|entire|whole|large|major|codebase|multiple|multi-file|every)\b|\b(large|major|big|sweeping|codebase-wide)\b[^.\n]{0,20}\brefactor\w*|\brewrite (the|this|our|entire|whole)\b"),
    ("wide-scope", r"\bmulti-file\b|\bacross the (codebase|repo|repository|project)\b|\bend-to-end\b"),
]

# Judgment work: haiku must never get it; it goes to HEAVY.
LIGHT_NEEDS_HEAVY = [
    ("design", r"\b(re-?)?design\w*"),
    ("review", r"\breview\w*|\baudit\w*|\bcritique\w*"),
    ("debugging", r"\bdebug\w*|\bdiagnos\w*|\btroubleshoot\w*|\bwhy (does|is|are|did|do|doesn't|isn't|won't)\b"),
    ("planning", r"\bplan\b|\bplanning\b|\bstrategy\b|\broadmap\b"),
    ("decision", r"\bdecide\w*|\bdecision\w*|\btrade-?offs?\b|\bevaluat\w*|\bassess\w*|\brecommend\w*|\bpropos\w*|\bjudg\w*"),
    ("refactor", r"\brefactor\w*|\boptimi[sz]\w*|\brestructur\w*|\brework\w*|\boverhaul\w*|\bre-?think\w*"),
    ("reasoning", r"\bprove\b|\bproof\b|\bderive\b|\bresearch\b"),
]

# Anything that writes or builds: haiku is read-only here; it goes to STANDARD.
LIGHT_NEEDS_STANDARD = [
    ("writes-code", r"\bfix\w*|\b(re-?)?implement\w*|\b(re-?)?build\b|\bpatch\w*|\bbuild a\b|\bbuild the\b"),
    ("writes-files", r"\b(edit|modify|change|rewrite|create|write|add|remove|delete|rename|update|replace|insert|generate|draft)\b"),
    ("changes-state", r"\b(install|uninstall|commit|push|deploy|merge|rebase|publish|release|upgrade|downgrade)\b"),
    ("analysis", r"\banaly[sz]\w*|\binterpret\w*|\bexplain\w*|\bcompare\b"),
]

# "do not fix anything", "never edit files", "without changing code" -> drop the
# negation and the next two words so a prohibition is not read as a request.
# The window is deliberately short: "do not stop until you fix X" keeps "fix".
NEGATION = re.compile(
    r"\b(do not|don't|dont|does not|doesn't|never|without|must not|should not|shouldn't|no need to|not to)\s+(\w+[- ]?){1,2}"
)


def _norm_model(value):
    """Map a model alias or full id to haiku/sonnet/opus/fable, or None."""
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    for family in ("haiku", "sonnet", "opus", "fable"):
        if family in v:
            return family
    if v == "best":
        return "opus"
    return None


def _own_agent(subagent_type):
    """Return light/standard/heavy/max if this is one of our tier agents."""
    if not isinstance(subagent_type, str):
        return None
    s = subagent_type.strip().lower()
    if s.startswith(PLUGIN_PREFIX):
        s = s[len(PLUGIN_PREFIX):]
    return s if s in AGENT_MODEL else None


def _signals(text, table):
    return [label for label, pattern in table if re.search(pattern, text)]


def load_project_keywords(cwd):
    """Extra heavy-work keywords from the nearest .model-planner.json at or above cwd.

    Returns (keywords, status) with status "none" (no file), "ok", or "invalid"
    (file found but unusable; ignored). Keywords are plain words or phrases,
    matched case-insensitively as whole words - never as regular expressions.
    """
    if not isinstance(cwd, str) or not cwd:
        return [], "none"
    d = os.path.abspath(cwd)
    for _ in range(CONFIG_MAX_LEVELS):
        path = os.path.join(d, CONFIG_NAME)
        if os.path.isfile(path):
            try:
                if os.path.getsize(path) > CONFIG_MAX_BYTES:
                    return [], "invalid"
                with open(path) as fh:
                    data = json.load(fh)
            except Exception:
                return [], "invalid"
            words = data.get("heavy_keywords") if isinstance(data, dict) else None
            if not isinstance(words, list):
                return [], "invalid"
            clean = []
            for w in words[:CONFIG_MAX_KEYWORDS]:
                if isinstance(w, str):
                    w = " ".join(w.lower().split())
                    if 1 <= len(w) <= CONFIG_MAX_KEYWORD_LEN:
                        clean.append(w)
            return clean, "ok"
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return [], "none"


def _project_signals(text, words):
    hits = []
    for w in words or []:
        if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", text):
            hits.append("project:" + w)
    return hits


def classify(tool_input, extra_heavy=None):
    """Pure decision function. Returns a dict:
    {"decision": "allow"|"deny", "effective": model|None, "signals": [...],
     "send_to": tier|None, "reason": str}
    """
    if not isinstance(tool_input, dict):
        tool_input = {}
    subagent_type = tool_input.get("subagent_type")
    own = _own_agent(subagent_type)
    requested = _norm_model(tool_input.get("model"))
    pinned = AGENT_MODEL.get(own) if own else None

    result = {"decision": "allow", "effective": None, "signals": [], "send_to": None, "reason": ""}

    # Rule 0: Fable is switched off in this setup (it needs paid credits).
    if requested == "fable" and os.environ.get("MODEL_PLANNER_ALLOW_FABLE", "").lower() != "on":
        result.update(
            decision="deny",
            effective="fable",
            signals=["fable-disabled"],
            send_to="max",
            reason=(
                "model-planner guard: the Fable model is switched off in this setup (it needs paid "
                "credits). Dispatch to '%smax' (Opus at extra-high effort) with no model override instead."
                % PLUGIN_PREFIX
            ),
        )
        return result

    # Rule 1: never downgrade one of our pinned tier agents with model=...
    if pinned and requested and RANK[requested] < RANK[pinned]:
        result.update(
            decision="deny",
            effective=requested,
            signals=["downgrade-of-pinned-tier"],
            send_to=own,
            reason=(
                "model-planner guard: '%s' is pinned to %s and must not be downgraded to %s. "
                "Dispatch it without a model override, or - only if the task is genuinely lighter - "
                "pick the lower tier agent explicitly so its own rules apply."
                % (subagent_type, pinned, requested)
            ),
        )
        return result

    effective = requested or pinned
    result["effective"] = effective
    if effective is None:
        # Unpinned agent with no override: it inherits the session model. Not ours to judge.
        return result

    raw = "%s\n%s" % (tool_input.get("description") or "", tool_input.get("prompt") or "")
    text = NEGATION.sub(" ", raw.lower())
    rank = RANK[effective]

    heavy = _signals(text, NEEDS_HEAVY) + _project_signals(text, extra_heavy)
    if rank <= RANK["sonnet"] and heavy:
        result.update(decision="deny", signals=heavy, send_to="heavy")
    elif rank == RANK["haiku"]:
        judgment = _signals(text, LIGHT_NEEDS_HEAVY)
        writes = _signals(text, LIGHT_NEEDS_STANDARD)
        if judgment:
            result.update(decision="deny", signals=judgment + writes, send_to="heavy")
        elif writes:
            result.update(decision="deny", signals=writes, send_to="standard")

    if result["decision"] == "deny":
        result["reason"] = (
            "model-planner guard: this task is too heavy for the %s tier (%s). Signals: %s. "
            "Re-dispatch it to the '%s%s' agent, or do it in the main session. "
            "Do not reword the task to get past this guard - if the guard is wrong, a higher tier "
            "only costs tokens, while a lower tier risks a wrong result."
            % (
                TIER_OF_MODEL[effective],
                effective,
                ", ".join(result["signals"]),
                PLUGIN_PREFIX,
                result["send_to"],
            )
        )
    return result


def _log(entry):
    if os.environ.get("MODEL_PLANNER_LOG", "").lower() == "off":
        return
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
            os.replace(LOG_FILE, LOG_FILE + ".1")
        with open(LOG_FILE, "a") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # logging must never break a dispatch


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # unreadable input: stay out of the way
    if not isinstance(payload, dict) or payload.get("tool_name") != "Agent":
        return 0
    tool_input = payload.get("tool_input") or {}

    extra, cfg_status = load_project_keywords(payload.get("cwd"))
    result = classify(tool_input, extra)
    guard_off = os.environ.get("MODEL_PLANNER_GUARD", "").lower() == "off"
    # The log records routing facts only - never the prompt text.
    _log(
        {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "agent": tool_input.get("subagent_type"),
            "model_override": tool_input.get("model"),
            "effective": result["effective"],
            "decision": "allow(guard-off)" if (guard_off and result["decision"] == "deny") else result["decision"],
            "send_to": result["send_to"],
            "signals": result["signals"],
            "project_config": cfg_status,
        }
    )
    if result["decision"] == "deny" and not guard_off:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": result["reason"],
                    }
                }
            )
        )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
