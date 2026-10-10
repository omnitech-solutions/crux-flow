---
name: council
description: "Convene a three-provider council to judge a decision (\"run the council\", \"ask the council\"); inside a cycle run, the council runner writes the council record that is the gate's evidence."
context: fork
model: opus
metadata:
  tags: "verification, multi-model, council, anti-hallucination"
  bundles: "crux-verification, crux-docs"
  risk_level: "medium"
  triggers: "run the council | convene the council | ask the council | get a multi-model opinion"
  requires_env: "OPENROUTER_API_KEY"
---

# Crux Council Vote (Async-First)

> **Execution context:** this skill runs in a forked subagent and returns a summary to the caller. The fork does not see the main-thread conversation, so pass any needed context explicitly at invocation.

<!-- BEGIN GENERATED: runtime-compat -->
## Runtime compatibility

This skill is portable across Claude Code, Codex, and OpenCode. This section overrides platform-specific labels below.

- Before running a command that uses `CRUX_PLUGIN_ROOT`, set it to the installed plugin root. In Claude Code, use the value of `CLAUDE_PLUGIN_ROOT`. In Codex and OpenCode, derive it from the absolute path of this selected `SKILL.md`: the plugin root is the parent of its `skills/` directory. In a source checkout, use the checkout `crux/` directory.
- For project-local skills, use `.claude/skills` in Claude Code, `.agents/skills` in Codex, and `.opencode/skills` in OpenCode, which also reads the singular `.opencode/skill`. Set `CRUX_LOCAL_SKILLS_DIR` to that path before following any command below that uses it.
- Translate Claude Code tool labels such as `Agent`, `Read`, `Write`, `Bash`, `WebSearch`, and `WebFetch` to the matching capability in the current session. Codex names its own capabilities; OpenCode uses the lowercase forms `subagent`, `read`, `edit`, `shell`, `websearch`, and `webfetch`, where `edit` covers both `Edit` and `Write`. Do not attempt to invoke the Claude Code labels as literal commands on another host.
- Install the generated role agents before delegating: `install-codex-agents` in Codex, `install-opencode-agents` in OpenCode. Codex names them `crux_architect`, `crux_brainstormer`, `crux_commander`, `crux_dev_lead`, `crux_developer`, `crux_historian`, `crux_librarian`, `crux_night_gardener`, `crux_reviewer`, and `crux_wayfinder`; OpenCode uses the bare role names `architect`, `brainstormer`, `commander`, `dev-lead`, `developer`, `historian`, `librarian`, `night-gardener`, `reviewer`, and `wayfinder`. If a required role or capability is unavailable, report that truthfully instead of claiming it ran.
- Argument placeholders such as `$adr` and `$book` bind only in Claude Code. On a host without argument binding they are unset — take the value from the user's phrase. The "Fields OpenCode ignores" section of `OPENCODE_GUIDE.md` names the invocation-control fields OpenCode ignores.
<!-- END GENERATED: runtime-compat -->


## Gate council or ad-hoc council

Council deliberation runs only through the council runner, and its council record is the only evidence a council gate accepts.[^council] Independent review is a reviewer's examination, and its reviewer report is the only evidence an independent-review gate accepts.[^review] Neither satisfies the other's gate.

Inside a promptbook run, a council gate uses the council runner, `run-council.py`. Run it as a background command:

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/run-council.py" <run-RUN-NNN.yaml> [--prompt N] --round N \
  --question <question file> --subject <subject file> [--subject <subject file> ...] \
  [--book <book file>] [--retain-subjects] [--max-tokens 64000] [--timeout 600]
```

Finish any open Git merge or other sequence before council. Both book formats
use the current attempt-aware gate. An implementation module uses council kind
`implementation`, including in a combined architectural book; `combined` is never
a council kind. Verify and patch retain their existing kinds and dimensions.
Select an exact formal revision with `--implementation-revision <decision-path>`
or a migration batch with `--migration-batch <batch-path>`. These selectors are
mutually exclusive. Also pass that exact path as `--subject` with `--retain-subjects`.
Keep a batch's role, slot and digest distinct from a revision. Record each subject's
write-time witness before committing it; never attribute dirty bytes after a refusal.
New formal closes retain context three/profile four. Historical context two/profile three
and context one/profile two remain immutable and replay-only, never selected from the book format.

`--prompt` is optional and defaults to the run's current prompt. The run, `--round`, `--question` and at least one `--subject` are required.

`--round` is the number of council records with outcome `ran` already in this module (in a patch book, this prompt), plus one. In an adr module a valid adjudicator refutation record fills place 3. A could-not-run record takes no place, so after you fix its cause you reconvene with the same number. `run-council.py` refuses a mismatch before any call: exit 1, a JSON error on stdout, `"record": null`, and no record written.

The council runner commits the attempt record and the council record itself. Before any council request it commits an attempt record that claims the round. The claim begins a council attempt, one execution of the round, which is distinct from a seat's calls below. A round that began never vanishes: until its own council record resolves it, the gate stops on the open attempt.[^attempt] It verifies that the committed council record holds the bytes it computed.[^match] The gate check (`advance-run.py`) reads only a committed record. Do not commit, edit or delete either record yourself.

The council runner exits with one of three codes. A council runner that ends without an exit code, with a signal or with a code other than 0, 1 and 2 stopped before it reported. Treat it as an exit 2 that names a claimed attempt, and follow the steps under `2` with `--prompt <n>`; never convene another round until recovery reports.[^recover]

- `0`: it wrote, committed and verified a council record that ran.
- `1`: it committed a council record the gate check stops on, or it refused the call and wrote no council record. The record is a could-not-run record, whose action is DEFER_TO_HUMAN, or a council record that ran but whose full write the secret scan refused: advance `--outcome blocked` with it attached. A refusal prints `"record": null` and leaves nothing to attach. `"refused": "round"` means correct `--round` and run again. `"refused": "prompt"` means `--prompt` is not the run's current prompt: run again with the current prompt or omit `--prompt`. `"refused": "preflight"` means a question or subject failed the input checks before any request: apply each cause's `repair` and run again at the same round. `retype` means correct the path. `commit-run-work` means commit the subject, the run's own witnessed work, with `run-work-witness.py commit <run> --path <path>`. The third preflight refusal at one convening prompt commits a `preflight-retries-spent` record instead, an escalation-loop stop.[^defer] `"refused": "attempt-open"` means an open attempt holds the module: recover it, never claim past it.
- `2`: no council record was committed. Exit 2 never means nothing was written. When stderr names `timeout`, or names outside work the commit moved, the owner's remedy comes before recovery: report a contradicted-premise stop for the owner. The owner first restores the set-aside work (`git stash list`, or a pre-commit framework's backup patch) and then removes a stale `index.lock` in the git directory. Start recovery only after the owner reports both steps done. When stderr names a claimed attempt, run the process check, probe the lock with `run-council.py --recover <run> --prompt <n> --probe`, and once no live council runner holds it, run `run-council.py --recover <run> --prompt <n>`. Pass `--prompt`: without it recovery cannot recognise a record whose pending copy is already removed, and it reports `nothing-open`. Recovery makes no council request and commits or recognises only the original council record.[^recover] Route each recovery result by the recovery table in `run-promptbook`'s `references/gates.md`. Never convene another round over a claimed attempt. When stderr names no attempt, the prompt cannot advance, and the conductor reports why, as a contradicted-premise stop for the owner.

A step that writes a file it later passes as a subject records a run-work witness after each write: `run-work-witness.py record <run> --prompt N --path <path>`. Never record a witness to repair a preflight refusal: a subject with no witness taken at its write goes to the owner. `run-promptbook`'s `references/gates.md` describes how a conductor issues and advances a gate prompt, repairs a preflight refusal and recovers an open attempt.

The async driver below serves an ad-hoc council only, such as a design question outside a run. It satisfies no promptbook council gate. Outside a run (an ad-hoc council, the retrospective, the night gardener) council deliberation holds by prose only. No native agent and no reviewer ever casts a seat's vote.[^council] A council that cannot run defers to a human.[^defer]

The council runner records the requested and the served model for each seat. Two gate-mode fault labels, `served-model` and `served-provider-mismatch`, mark a seat whose served model or served provider is absent or outside its registry entry's accepted set. The council runner never retries such a seat and never replaces its model.[^served]

## When to Use
- Choosing between architectural approaches
- Evaluating tradeoffs (cost vs accuracy, speed vs correctness)
- Any decision where multi-model consensus reduces risk
- Before committing to a design pattern or library choice

## When NOT to Use
- **Factual lookups** ("what's the default timeout?") — a council of three models burns three API calls to confirm one fact. Use `call-llm` for a single answer.
- **Obvious single answers** — if the choice is clear, a council just rubber-stamps it at 3x the cost and latency.
- **Tight loops** — never put a council call inside a per-step or per-item loop; the parallel-but-multi-model cost compounds fast. Use `call-llm` for in-loop reasoning and reserve the council for the one decision that frames the loop.

A good council question is a genuine *fork* — two or more defensible approaches where multiple perspectives change the outcome. If there's no fork, skip it.

## File Locations
```
crux/scripts/crux/council/
├── __init__.py
├── async_council.py   ← USE THIS for an ad-hoc council
└── council.py         ← sync fallback only
```

## Exports
```
From council __init__:
  council_vote, quick_council, get_opinion, synthesize_opinions,
  VotingMethod, Opinion, CouncilDecision, DEFAULT_COUNCIL, MODEL_WEIGHTS,
  AsyncCouncil, AsyncVisualCouncil, AsyncCouncilConfig, VisualVoteResult,
  create_async_council, create_visual_council
```

## Running it (PEP 723 driver under `uv`, NOT bare `python3`)

The council needs `httpx`, the HTTP client — **NOT** present in the bare
system `python3`, so running there dies with `ModuleNotFoundError: No module named 'httpx'`. The
canonical pattern: write your driver to a temp `.py` file that
starts with the standard PEP 723 header, then run it with **`uv`**, which
provisions the dependencies into a cached, isolated environment:

```python
# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.27"]
# ///
import sys
sys.path.insert(0, "${CRUX_PLUGIN_ROOT}/scripts")

from crux.council import create_async_council
# ... your driver code (see Usage below)
```

```bash
d=$(mktemp -d) && uv run "$d/driver.py"
```

Each call to a seat has its own deadline, 600 s by default. A seat that fails with a
`timeout`, `provider`, `malformed-response` or `truncated` fault is called once more, so the
worst case per seat is two deadlines, about 20 minutes. A Claude Code foreground shell call
stops at 10 minutes, so **run the driver as a background command** and read its output when it
exits. A shell that kills the driver first loses every seat's answer. `AsyncCouncilConfig`
takes `max_retries` 0 (no retry) or 1 (the default); any other value is refused when the
configuration is built.

Write the driver into a private per-run directory as above, never a fixed shared
path like `/tmp/driver.py` — a predictable name in a world-writable directory is a
symlink hazard.

When writing the temp file, substitute `${CRUX_PLUGIN_ROOT}` (crux's portable
plugin-root name — in Claude Code, the value of `CLAUDE_PLUGIN_ROOT`; in Codex,
derived from this `SKILL.md`'s path per the Runtime compatibility note above)
with its actual value; in a source checkout, where the
variable is unset, substitute the checkout's `crux/` directory. No
`PYTHONPATH`, no extras, no surrounding project needed — the inline metadata
makes `uv` ignore any enclosing project.

**Without `uv` installed this fails at the shell** — `command not found: uv`
(exit 127); the script never executes. Remediation: install uv
(https://docs.astral.sh/uv/). API keys are read from `~/.crux/env` via
`crux_env`. One key backs every seat: `crux-env set OPENROUTER_API_KEY …`.

## ALWAYS Use Async for an Ad-Hoc Council

```python
import asyncio

from crux.council import create_async_council, AsyncCouncilConfig

async def decide(question: str, context: str):
    council = create_async_council()

    decision = await council.deliberate(
        prompt=f"Question: {question}\n\nContext: {context}",
        system="You are a technical architecture council member.",
    )

    # deliberate() returns a CouncilDeliberation (crux/scripts/crux/core/data_classes.py):
    #   votes, consensus, consensus_confidence, key_agreements, key_disagreements,
    #   final_recommendation, dissent_count, confidence_adjustment (+ .has_critical_dissent).
    # final_recommendation keys: action, reason, degraded, errored_seats, off_scale,
    #   conditioned, conditions, nits, nit_items.
    print(f"Consensus: {decision.consensus}")              # e.g. UNANIMOUS_APPROVE
    # Consensus labels: UNANIMOUS_APPROVE, UNANIMOUS_REJECT, MAJORITY_APPROVE, MAJORITY_REJECT,
    # SPLIT, NO_QUORUM, and UNANIMOUS_<TOKEN> when every responding seat returned the same
    # other decision. UNANIMOUS_OFF_SCALE replaces it when the token is not 1-40 characters of
    # A-Z and underscore starting with a letter, contains APPROVE or REJECT, or is AUTO_EXECUTE
    # or EXECUTE_WITH_MONITORING.
    # Only UNANIMOUS_APPROVE and MAJORITY_APPROVE can route to an execute action; every other
    # label routes to DEFER_TO_HUMAN or ABORT. Unanimity on REVISE is agreement, not approval.
    print(decision.final_recommendation["off_scale"])      # ["<provider>:<decision>", ...] for
                                                            # responding seats outside the known
                                                            # scale; each entry is bounded and
                                                            # its unprintable characters replaced
    print(f"Confidence: {decision.consensus_confidence}")

    # A conditioned approval: a responding seat returned APPROVE_WITH_CONDITIONS. The label
    # can still read UNANIMOUS_APPROVE or MAJORITY_APPROVE, so read the flag. The route is EXECUTE_WITH_MONITORING at
    # most, never AUTO_EXECUTE. The flag fires only when the prompt puts APPROVE_WITH_CONDITIONS
    # on the decision scale (the built-in envelope does not).
    fr = decision.final_recommendation
    if fr["conditioned"]:                                   # decision.conditioned is the same flag
        for seat, points in fr["conditions"].items():       # keyed by seat; untrusted model text, carried unchanged
            print(f"  [condition] {seat}: {points}")
    # Nits: a responding seat returned APPROVE_WITH_NITS. Nits are reported and never change
    # the route. Only conditions block.
    if fr["nits"]:                                          # decision.nits is the same flag
        for seat, points in fr["nit_items"].items():
            print(f"  [nit] {seat}: {points}")
    # Both flags read False, with empty items, when no responding seat returned them. An
    # errored seat never contributes.

    if decision.dissent_count:
        print(f"{decision.dissent_count} dissent(s) — srde output is evidence only")
        for v in decision.votes:                            # each vote is a CouncilVote
            for d in v.dissenting_points:                   # NOTE: dissenting_points, not dissent_points
                print(f"  [{v.provider}/{v.model}] {d}")
        # See srde skill

    # Each vote also records how its seat fared. finish_reason is the reply's finish reason
    # from a closed set (stop, length, content_filter, tool_calls, error, other, missing; None
    # when the seat got no reply, such as a timeout). fault_label names an errored seat's cause
    # (one of timeout, provider, malformed-response, truncated, auth, rate-limit, client-config,
    # refused, insufficient-credit, unexpected-status or unreachable, or unknown). A gate council
    # adds served-model and served-provider-mismatch, which are never retried. retried marks
    # a seat called a second time; recovered marks a seat that answered on that second call;
    # first_fault_label and first_finish_reason describe its first call.
    # attempts lists each call to the seat with what its reply reported.
    for v in decision.votes:
        print(v.provider, v.finish_reason, v.fault_label, v.retried, v.recovered)

    return decision

result = asyncio.run(decide(
    question="Should we use approach A or B?",
    context="Full context including constraints and requirements",
))
```

### Async with Custom Config

```python
config = AsyncCouncilConfig(...)  # customize models, weights, etc.
council = create_async_council(config=config)
decision = await council.deliberate(prompt="...", system="...")
```

### Sync Deliberation (from async council)

```python
council = create_async_council()
decision = council.deliberate_sync(prompt="...", system="...")
```

### Visual Council (image analysis)

```python
from crux.council import create_visual_council

visual_council = create_visual_council()
votes = await visual_council.analyze_image(
    image_b64="<base64-encoded-image>",
    prompt="Does this dashboard render correctly?",
)
# Or sync:
votes = visual_council.analyze_sync(image_b64="...", prompt="...")
```

The visual council retries and labels a seat as the text council does. Its result for a seat
that errored reads `passed=False` at confidence 0.0 and carries `errored=True`. Skip errored
results when you count failed checks: an errored seat is a missing check, not a failed one.

## Sync Fallback (only if async is impossible)

```python
from crux.council import council_vote, quick_council, get_opinion, VotingMethod
from crux.core.llm_caller import get_default_model

# Full council with options
decision = council_vote(
    question="Should we use approach A or B?",
    context="Full context here",
    voting_method=VotingMethod.ARBITER,   # models and arbiter default to the registry's assignment
    tracer=None,  # optional Tracer instance
)

# Quick one-liner (returns string)
answer = quick_council("Should we cache at the API or DB layer?")

# Get a single model's opinion
opinion = get_opinion(get_default_model("council_arbiter"), "question", context="...")
```

## Model Config

The router registry assigns each seat by role: `openai_top`, `anthropic_top` and `google_top`. The sync council reads `council_default` and `council_arbiter`. Read the assignments in `${CRUX_PLUGIN_ROOT}/scripts/crux/_config/llm_router_config.json`; this skill names no model.[^seats]

The router reaches every provider through the OpenRouter gateway.

API keys are read via `crux_env.require(...)` from `~/.crux/env` (managed by `crux-env`).

## Rules
- ALWAYS prefer async for an ad-hoc council and for agent teams (parallel teammates = concurrent calls)
- ALWAYS check `dissent_count` (and each vote's `dissenting_points`) — never just read `consensus`
- ALWAYS read `final_recommendation["conditioned"]` and `["nits"]`: a conditioned approval can read `UNANIMOUS_APPROVE` or `MAJORITY_APPROVE` but never routes to `AUTO_EXECUTE`
- ALWAYS run an ad-hoc council driver in the background (inside a cycle run, the council gate uses `run-council.py`): the worst case per seat is about 20 minutes
- ALWAYS provide rich context — models deliberate better with specifics
- Treat srde output as evidence: it never settles a gate and never runs on the ADR path (see srde skill)[^srde]
- Register results on Semantic Bridge for cross-teammate visibility

[^council]: rule:council-is-never-harness-native, rule:council-gate-needs-a-runner-record
[^review]: rule:review-gate-needs-a-reviewer-report
[^seats]: rule:every-seat-assesses-five-dimensions, rule:council-assignment-is-validated-before-spend
[^served]: rule:seat-records-requested-and-served-model, rule:served-provider-matches-a-declared-set
[^defer]: rule:only-a-preflight-refusal-is-retried
[^attempt]: rule:an-open-council-attempt-stops-the-gate
[^match]: rule:council-evidence-matches-its-attempt
[^recover]: rule:recovery-never-deliberates-again
[^srde]: rule:de-wire-srde-from-adr-path, rule:blocking-finding-classification
