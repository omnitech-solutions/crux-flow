#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27",
# ]
# ///
"""
runbook.py - Generate a crux-aware autonomous prompt list from a single goal.

Default is a long autonomous runbook (typically 50-100 prompts). The generated
runbook embeds the 3-testable-hypotheses discipline (hypothesis → test → reflect,
with confidence gates), confidence checkpoints, council sanity checks, and
code-review cycles.

Implementation notes:
- Generated runbooks instruct the executing agent to run the hypothesis loop
  discipline itself: state 3 testable hypotheses, test them, self-reflect, and
  apply the confidence gates. They reference `crux.council` import paths.
- The traces-dir env var is `CRUX_TRACES_DIR`.
- The `--ai-plan` path is minimal: it calls Gemini directly with a built-in
  prompt and degrades cleanly to template-only on any failure (missing key,
  network, parse error).

Usage:
    python -m crux.runbook "Your goal"
    python -m crux.runbook --file goal.md
    python -m crux.runbook "Goal" --template-only --target-prompts 60
    python -m crux.runbook "Goal" --ai-plan --target-prompts 75
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Defensive: make the bundled package importable when this file is run as a
# plain script (`uv run …/scripts/crux/runbook/runbook.py`) rather than as
# `python -m crux.runbook`. The package root is `scripts/` — two levels up —
# the *sibling* of the entry scripts (ADR-0035 §2). Survives -P /
# PYTHONSAFEPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

MIN_PROMPTS = 10
MAX_PROMPTS = 100
DEFAULT_TARGET_PROMPTS = 75


@dataclass(frozen=True)
class PromptBlock:
    title: str
    body: str


def _clamp_target_prompts(n: int) -> int:
    return max(MIN_PROMPTS, min(MAX_PROMPTS, n))


def _read_goal(args: argparse.Namespace) -> str:
    if args.file:
        goal_path = Path(args.file)
        if not goal_path.exists():
            raise SystemExit(f"File not found: {args.file}")
        goal = goal_path.read_text().strip()
        if not goal:
            raise SystemExit(f"File is empty: {args.file}")
        return goal

    if args.goal:
        goal = args.goal.strip()
        if goal:
            return goal

    raise SystemExit("Error: Must provide a goal or --file")


def _default_output_path(timestamp: str) -> Path:
    return Path("logs") / "prompt_lists" / f"{timestamp}_prompt_list.md"


def render_markdown(
    *,
    goal: str,
    prompts: List[PromptBlock],
    mode: str,
    created_at: str,
) -> str:
    header = f"""# Crux-Aware Prompt List

**Goal:** {goal}
**Mode:** {mode}
**Generated:** {created_at}
**Total Prompts:** {len(prompts)}

---

## How to use (AUTONOMOUS)
This prompt list is designed to be **hands-off / autonomous**.

Recommended usage:
- Paste the entire file into a Cursor agent (or attach it as context) and say:
  **"Execute PROMPT #1 through PROMPT #{len(prompts)} in order. Do not ask me questions unless you are truly blocked. Keep going until you finish."**
- Each prompt is an **imperative instruction** for the agent to execute.
- Prompts embed crux-aware cycles (implement -> review -> apply -> re-review -> validate) so you can walk away for 2-4 hours.

Crux primitives to use during execution:
- **Co-locate traces with the run**: `export CRUX_TRACES_DIR="logs/agent_runs/<RUN_ID>/traces"`
- **Hypothesis loop discipline**: state 3 testable hypotheses, test each one, self-reflect, apply confidence gates (≥85% ship; ≥70% ship with caveats; ≥50% independent check; <50% stop and escalate)
- **Code review / council**: `from crux.council import council_vote`

---
"""

    parts: List[str] = [header]
    for idx, prompt in enumerate(prompts, start=1):
        parts.append(f"## PROMPT #{idx}: {prompt.title}\n\n{prompt.body}\n")
        if idx != len(prompts):
            parts.append("\n---\n")
    return "\n".join(parts).rstrip() + "\n"


def _prompt_bootstrap(goal: str) -> List[PromptBlock]:
    return [
        PromptBlock(
            title="AUTONOMY SETUP (no questions, write artifacts)",
            body=f"""You are an autonomous Cursor coding agent executing this runbook end-to-end.

GOAL:
{goal}

RULES:
- Do not ask the user questions unless truly blocked.
- Make safe assumptions and proceed.
- Persist artifacts to disk so the user can inspect results later.

ACTION:
1) Choose a RUN_ID (timestamp format `YYYYMMDD_HHMMSS`).
2) Create `logs/agent_runs/<RUN_ID>/` and subfolders:
   - `logs/agent_runs/<RUN_ID>/traces/` (semantic tracer markdown logs for council/review)
3) Export trace colocation so council/review traces land inside this run folder:
   - `export CRUX_TRACES_DIR="logs/agent_runs/<RUN_ID>/traces"`
4) Write `00_goal.md` containing: goal summary, constraints, done-criteria, risks.

OUTPUT (write files, then continue automatically):
- `logs/agent_runs/<RUN_ID>/00_goal.md`""",
        ),
        PromptBlock(
            title="HYPOTHESIS LOOP (1 pass): generate hypotheses you will actually test",
            body=f"""GOAL:
{goal}

ACTION:
Run the 3-testable-hypotheses discipline for this goal:

1. State exactly 3 testable hypotheses about how to accomplish the goal.
   Each hypothesis must include: the claim, the test method, and the expected outcome.
2. Test each hypothesis (run code, inspect files, check constraints — do not skip).
3. Self-reflect: which hypotheses held? which failed? what did you learn?
4. Apply confidence gates:
   - ≥85%: ship it
   - ≥70%: ship with caveats noted in the log
   - ≥50%: get an independent check before proceeding
   - <50%: stop and escalate to the user

Then write `01_hypotheses.md` with:
- the 3 hypotheses
- the test results for each
- your self-reflection
- your confidence rating and gate decision

OUTPUT (write file, then continue):
- `logs/agent_runs/<RUN_ID>/01_hypotheses.md`""",
        ),
        PromptBlock(
            title="TASK PLAN (make it executable and crux-aware)",
            body=f"""GOAL:
{goal}

ACTION:
Create an execution checklist of tasks. Each task MUST include the cycle:
Implement -> CruxReview -> ApplyFindings -> VerifyApplied -> ReReview+Validate.

OUTPUT:
- Write `02_execution_checklist.md` to `logs/agent_runs/<RUN_ID>/`.
- Continue automatically.""",
        ),
    ]


def _padding_prompts(goal: str) -> List[PromptBlock]:
    return [
        PromptBlock(
            title="CHECKPOINT: update progress log (autonomous)",
            body=f"""GOAL:
{goal}

ACTION:
Update `logs/agent_runs/<RUN_ID>/progress.md` with:
- what you just did
- what you will do next
- any risks discovered

OUTPUT:
- Append to `logs/agent_runs/<RUN_ID>/progress.md`
- Continue automatically""",
        ),
        PromptBlock(
            title="CHECKPOINT: council sanity check (autonomous)",
            body=f"""GOAL:
{goal}

ACTION:
Run a council sanity check on your current plan and progress.

```bash
python - <<'PY'
from crux.council import council_vote

question = "Given the goal and current progress, what are the top 3 risks and the next best step?"
context = "GOAL:\\n{goal}\\n\\nPROGRESS:\\n(Briefly summarize what you have done so far and what remains.)"
result = council_vote(question, context=context)
print(result.synthesis)
print("\\nFINAL:\\n" + result.final_decision)
PY
```

OUTPUT:
- Save to `logs/agent_runs/<RUN_ID>/checkpoint_council.md`
- Continue automatically""",
        ),
        PromptBlock(
            title="CHECKPOINT: deterministic constraints pass (autonomous)",
            body=f"""GOAL:
{goal}

ACTION:
Write down the *deterministic* constraints implied by the goal (things you can check without judgment).
Examples: word count, required phrase, required file exists, tests pass.

OUTPUT:
- `logs/agent_runs/<RUN_ID>/checkpoint_constraints.md`
- Continue automatically""",
        ),
        PromptBlock(
            title="CHECKPOINT: artifact inventory (autonomous)",
            body=f"""GOAL:
{goal}

ACTION:
List all artifacts created so far (files, logs, outputs) and where they live.

OUTPUT:
- `logs/agent_runs/<RUN_ID>/checkpoint_artifacts.md`
- Continue automatically""",
        ),
    ]


def _cycle_prompts(goal: str, cycle_index: int) -> List[PromptBlock]:
    cycle_tag = f"CYCLE {cycle_index}"
    tag_lower = cycle_tag.lower().replace(" ", "_")
    return [
        PromptBlock(
            title=f"{cycle_tag} - IMPLEMENT (autonomous)",
            body=f"""GOAL:
{goal}

ACTION:
1) Choose the next highest-priority item from `logs/agent_runs/<RUN_ID>/02_execution_checklist.md`.
2) Run the 3-testable-hypotheses discipline for that task:
   - State exactly 3 testable hypotheses about the best approach.
   - Test each hypothesis (run code, inspect, check constraints).
   - Self-reflect and apply confidence gates (≥85% ship it; ≥70% ship with caveats; ≥50% get an independent check; <50% stop and escalate).
3) Implement the task (code changes + tests).
4) Record which files you changed.

OUTPUT (write file, then continue):
- `logs/agent_runs/<RUN_ID>/{tag_lower}_01_implement.md`""",
        ),
        PromptBlock(
            title=f"{cycle_tag} - CRUX REVIEW (code OR deliverable review)",
            body=f"""GOAL:
{goal}

ACTION:
Run a crux review appropriate to what you produced.

IF YOU CHANGED CODE OR PRODUCED A NON-CODE DELIVERABLE:
- Use crux Council Vote to critique it against the goal constraints and quality rubric.

```bash
python - <<'PY'
from crux.council import council_vote

deliverable_text = \"\"\"PASTE_DELIVERABLE_OR_DIFF_HERE\"\"\"
question = "Review this deliverable for the stated goal. List failures vs constraints and give concrete improvements."
context = f"GOAL:\\n{goal}\\n\\nDELIVERABLE:\\n{{deliverable_text}}"
result = council_vote(question, context=context)
print(result.synthesis)
print("\\nFINAL:\\n" + result.final_decision)
PY
```

OUTPUT (write file, then continue):
- `logs/agent_runs/<RUN_ID>/{tag_lower}_02_review.md`
- A checklist of CRITICAL/HIGH findings (inline in that file)""",
        ),
        PromptBlock(
            title=f"{cycle_tag} - APPLY FINDINGS (implement recommendations)",
            body=f"""GOAL:
{goal}

ACTION:
Implement all CRITICAL/HIGH recommendations from the previous review.
If you cannot implement a recommendation, document exactly why.

OUTPUT:
- `logs/agent_runs/<RUN_ID>/{tag_lower}_03_apply.md` (checklist with statuses)
- Continue automatically""",
        ),
        PromptBlock(
            title=f"{cycle_tag} - VERIFY APPLIED (did you catch them all?)",
            body=f"""GOAL:
{goal}

ACTION:
Verify every CRITICAL/HIGH item is actually addressed.
- Compare the review output vs your fixes
- If anything remains, fix it now

OUTPUT:
- `logs/agent_runs/<RUN_ID>/{tag_lower}_04_verify_applied.md`
- Continue automatically""",
        ),
        PromptBlock(
            title=f"{cycle_tag} - RE-REVIEW + VALIDATE (golden pass)",
            body=f"""GOAL:
{goal}

ACTION:
1) Re-run crux review and confirm CRITICAL/HIGH issues are gone.
2) Run validations appropriate to what you produced:
   - If code: run the relevant test suite (e.g., `pytest -q`, `npm test`, etc.)
   - If non-code deliverable: run deterministic checks for the goal constraints and re-run council review.
3) Update a progress log.

OUTPUT:
- `logs/agent_runs/<RUN_ID>/{tag_lower}_05_rereview.md`
- `logs/agent_runs/<RUN_ID>/{tag_lower}_06_validate.md`
- Append status to `logs/agent_runs/<RUN_ID>/progress.md`
- Continue automatically""",
        ),
    ]


def _final_prompts(goal: str) -> List[PromptBlock]:
    return [
        PromptBlock(
            title="Full-system validation checkpoint",
            body=f"""GOAL:
{goal}

ACTION:
Do a full validation sweep appropriate to the work performed.
- If code changes were made: run the relevant test suite(s) (e.g., `pytest -q`, `npm test`, etc.).
- If the output is a non-code deliverable: run deterministic constraint checks and re-run council review for quality.

OUTPUT (write file, then continue):
- `logs/agent_runs/<RUN_ID>/90_full_validation.md` (commands/checks + pass/fail + known issues)""",
        ),
        PromptBlock(
            title="Final crux review + fix pass",
            body=f"""GOAL:
{goal}

ACTION:
Run a final crux review pass:
- If code: do a bounded directory-level review and address remaining CRITICAL/HIGH issues.
- If non-code deliverable: run council review on the final deliverable and address remaining CRITICAL/HIGH issues.

OUTPUT:
- `logs/agent_runs/<RUN_ID>/91_final_review.md` (final review + fixes + anything intentionally not changed)""",
        ),
        PromptBlock(
            title="Handoff summary (what changed, how to run, how to verify)",
            body=f"""GOAL:
{goal}

ACTION:
Write a handoff summary for a teammate.

OUTPUT:
- `logs/agent_runs/<RUN_ID>/92_handoff.md`""",
        ),
        PromptBlock(
            title="Self-reflection (leave breadcrumbs for future agents)",
            body=f"""GOAL:
{goal}

ACTION:
Leave breadcrumbs for future agents.

OUTPUT (write file, then stop):
- `logs/agent_runs/<RUN_ID>/93_self_reflection.json`

JSON shape:
```json
{{
  "future_hints": [{{"context": "...", "hint": "...", "importance": "critical|useful|minor"}}],
  "patterns_noticed": ["..."],
  "warnings": ["..."],
  "incomplete_work": [{{"what": "...", "where_left_off": "...", "next_step": "..."}}]
}}
```""",
        ),
    ]


def generate_template_only_prompts(goal: str, target_prompts: int) -> List[PromptBlock]:
    target_prompts = _clamp_target_prompts(target_prompts)

    prompts: List[PromptBlock] = []
    prompts.extend(_prompt_bootstrap(goal))

    final_blocks = _final_prompts(goal)
    reserved_final = len(final_blocks)

    target_before_final = target_prompts - reserved_final
    padding = _padding_prompts(goal)

    cycle_index = 1
    while True:
        cycle_blocks = _cycle_prompts(goal, cycle_index)
        if len(prompts) + len(cycle_blocks) > target_before_final:
            break
        prompts.extend(cycle_blocks)
        cycle_index += 1

    pad_i = 0
    while len(prompts) < target_before_final:
        prompts.append(padding[pad_i % len(padding)])
        pad_i += 1

    prompts.extend(final_blocks)
    return prompts[:target_prompts]


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Extract a JSON object from an LLM response, handling markdown code blocks."""
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if json_match:
        candidate = json_match.group(1).strip()
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None

    return None


def _order_tasks(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    tasks_raw = plan.get("tasks", [])
    if not isinstance(tasks_raw, list):
        return []

    tasks: List[Dict[str, Any]] = [t for t in tasks_raw if isinstance(t, dict)]
    tasks_by_id: Dict[str, Dict[str, Any]] = {}
    ordered_ids: List[str] = []

    for t in tasks:
        tid = t.get("id")
        if isinstance(tid, str) and tid:
            tasks_by_id[tid] = t

    critical_path = plan.get("critical_path", [])
    if isinstance(critical_path, list):
        for tid in critical_path:
            if isinstance(tid, str) and tid in tasks_by_id and tid not in ordered_ids:
                ordered_ids.append(tid)

    for t in tasks:
        tid = t.get("id")
        if isinstance(tid, str) and tid and tid in tasks_by_id and tid not in ordered_ids:
            ordered_ids.append(tid)

    return [tasks_by_id[tid] for tid in ordered_ids]


def _task_blocks(goal: str, task: Dict[str, Any]) -> List[PromptBlock]:
    tid = task.get("id", "T?")
    title = task.get("title", "Untitled task")
    description = task.get("description", "")
    deps = task.get("dependencies", [])
    files = task.get("files_affected", []) or task.get("files_involved", [])
    done = task.get("done_criteria", "") or " ".join(task.get("acceptance_criteria", []))
    effort = task.get("effort", "") or str(task.get("estimated_hours", ""))
    priority = task.get("priority", "")

    deps_str = ", ".join(deps) if isinstance(deps, list) else str(deps)
    files_str = ", ".join(files) if isinstance(files, list) else str(files)

    return [
        PromptBlock(
            title=f"Task {tid} - IMPLEMENT (autonomous)",
            body=f"""GOAL:
{goal}

TASK:
{tid}: {title}

DETAILS:
- Description: {description}
- Dependencies: {deps_str}
- Files affected (expected): {files_str}
- Done criteria: {done}
- Priority/Effort: {priority} / {effort}

ACTION:
1) Run the 3-testable-hypotheses discipline for this task:
   - State exactly 3 testable hypotheses about the best approach.
   - Test each hypothesis (run code, inspect, check constraints).
   - Self-reflect and apply confidence gates (≥85% ship it; ≥70% ship with caveats; ≥50% get an independent check; <50% stop and escalate).
2) Implement the task now (code + tests).
3) Record changed files.

OUTPUT (write file, then continue):
- `logs/agent_runs/<RUN_ID>/{tid}_01_implement.md`""",
        ),
        PromptBlock(
            title=f"Task {tid} - CRUX REVIEW",
            body=f"""GOAL:
{goal}

ACTION:
Run a crux council review on what this task produced.

```bash
python - <<'PY'
from crux.council import council_vote

deliverable_text = \"\"\"PASTE_DELIVERABLE_OR_DIFF_HERE\"\"\"
question = "Review this deliverable for task {tid} of the stated goal. List failures vs constraints and give concrete improvements."
context = f"GOAL:\\n{goal}\\n\\nDELIVERABLE:\\n{{deliverable_text}}"
result = council_vote(question, context=context)
print(result.synthesis)
print("\\nFINAL:\\n" + result.final_decision)
PY
```

OUTPUT:
- Save reviewer output to `logs/agent_runs/<RUN_ID>/{tid}_02_review.md`
- Extract CRITICAL/HIGH checklist in that file
- Continue automatically""",
        ),
        PromptBlock(
            title=f"Task {tid} - APPLY FINDINGS (implement recommendations)",
            body=f"""GOAL:
{goal}

ACTION:
Apply all CRITICAL/HIGH recommendations from the previous review.
If something cannot be implemented, document why.

OUTPUT:
- `logs/agent_runs/<RUN_ID>/{tid}_03_apply.md` (checklist + status)
- Continue automatically""",
        ),
        PromptBlock(
            title=f"Task {tid} - VERIFY APPLIED",
            body=f"""GOAL:
{goal}

ACTION:
Verify every CRITICAL/HIGH item is addressed.
- Compare `.../{tid}_02_review.md` and your fixes
- Fix anything that remains

OUTPUT:
- `logs/agent_runs/<RUN_ID>/{tid}_04_verify_applied.md`
- Continue automatically""",
        ),
        PromptBlock(
            title=f"Task {tid} - RE-REVIEW + VALIDATE (golden pass)",
            body=f"""GOAL:
{goal}

ACTION:
1) Re-run crux review and confirm CRITICAL/HIGH issues are gone.
2) Run validations appropriate to what you produced.

OUTPUT:
- `logs/agent_runs/<RUN_ID>/{tid}_05_rereview.md`
- `logs/agent_runs/<RUN_ID>/{tid}_06_validate.md`
- Append status to `logs/agent_runs/<RUN_ID>/progress.md`
- Continue automatically""",
        ),
    ]


def generate_ai_plan_prompts(goal: str, target_prompts: int) -> List[PromptBlock]:
    """
    Generate prompts tailored to `goal` using an LLM-produced task plan.

    On any failure, fall back to template-only and prepend a warning prompt.
    """
    target_prompts = _clamp_target_prompts(target_prompts)

    try:
        # Absolute import (not `from ..core.llm_caller import …`) so the
        # --ai-plan path also works when this file is executed as a plain
        # script, where `__package__` is unset and relative imports fail.
        from crux.core.llm_caller import call_gemini_pro
    except Exception as e:  # pragma: no cover
        warning = PromptBlock(
            title="AI-plan unavailable (LLM import failed) - fallback to template-only",
            body=f"Could not import call_gemini_pro: {e}",
        )
        prompts = generate_template_only_prompts(goal, target_prompts)
        prompts.insert(0, warning)
        return prompts[:target_prompts]

    system = "You are a crux task planner. Output valid JSON only."
    prompt = f"""You are decomposing a goal into a structured JSON task plan
that an autonomous Cursor agent will execute hands-off.

Hard requirements:
- No human decision points
- Every task must be verifiable and produce artifacts/logs
- Use crux primitives (hypothesis discipline, council, tracer) where useful
- Do NOT rely on a static repo summary; the agent will inspect the repo live
- The goal may be NON-CODE (writing, research, specs)

GOAL:
{goal}

Output JSON shape:
```json
{{
  "tasks": [
    {{
      "id": "T1",
      "title": "...",
      "description": "...",
      "priority": "critical|high|medium|low",
      "dependencies": [],
      "effort": "S|M|L",
      "files_affected": ["..."],
      "done_criteria": "..."
    }}
  ],
  "critical_path": ["T1", "T2", "..."],
  "key_risks": ["..."]
}}
```
"""

    try:
        response = call_gemini_pro(prompt, system=system)
        plan = _extract_json(response)
        if not plan:
            raise ValueError("Could not parse JSON task plan from model response.")
    except Exception as e:
        warning = PromptBlock(
            title="AI-plan failed - fallback to template-only",
            body=f"""AI-plan generation failed, so we're falling back to the deterministic scaffold.

Failure:
{e}""",
        )
        prompts = generate_template_only_prompts(goal, target_prompts)
        prompts.insert(0, warning)
        return prompts[:target_prompts]

    ordered_tasks = _order_tasks(plan)

    prompts: List[PromptBlock] = []
    prompts.extend(_prompt_bootstrap(goal))
    prompts.append(
        PromptBlock(
            title="AI-produced task plan (AUTONOMOUS EXECUTION INPUT)",
            body=f"""GOAL:
{goal}

You are executing autonomously.

ACTION:
1) Save this JSON to `logs/agent_runs/<RUN_ID>/10_ai_plan.json`.
2) Decide an execution order (respect dependencies; follow critical_path when present).
3) Write that order to `logs/agent_runs/<RUN_ID>/11_task_order.md`.
4) Start executing Task T1 immediately.

```json
{json.dumps(plan, indent=2)}
```""",
        )
    )

    final_blocks = _final_prompts(goal)
    reserved_final = len(final_blocks)
    per_task_blocks = 5

    reserved_initial = len(prompts)
    available = max(0, target_prompts - reserved_initial - reserved_final)
    max_tasks = available // per_task_blocks if per_task_blocks > 0 else 0
    tasks_to_include = ordered_tasks[:max_tasks]

    for task in tasks_to_include:
        prompts.extend(_task_blocks(goal, task))

    target_before_final = target_prompts - reserved_final
    padding = _padding_prompts(goal)

    cycle_index = 1
    while True:
        cycle_blocks = _cycle_prompts(goal, cycle_index)
        if len(prompts) + len(cycle_blocks) > target_before_final:
            break
        prompts.extend(cycle_blocks)
        cycle_index += 1

    pad_i = 0
    while len(prompts) < target_before_final:
        prompts.append(padding[pad_i % len(padding)])
        pad_i += 1

    prompts.extend(final_blocks)
    return prompts[:target_prompts]


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a crux-aware prompt list (10-100 prompts) from a goal.")
    parser.add_argument("goal", nargs="?", help="The goal / prime directive")
    parser.add_argument("--file", "-f", help="Read the goal from a file")
    parser.add_argument(
        "--target-prompts",
        "-n",
        type=int,
        default=DEFAULT_TARGET_PROMPTS,
        help=f"Target number of prompts (clamped to {MIN_PROMPTS}-{MAX_PROMPTS}). Default: {DEFAULT_TARGET_PROMPTS}",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output markdown file path. Default: logs/prompt_lists/{timestamp}_prompt_list.md",
    )
    parser.add_argument(
        "--template-only",
        action="store_true",
        help="Generate deterministically with no LLM calls (always available).",
    )
    parser.add_argument(
        "--ai-plan",
        action="store_true",
        help="Use a top-tier LLM to tailor the prompt list to the goal (falls back if unavailable).",
    )

    args = parser.parse_args()

    if args.template_only and args.ai_plan:
        raise SystemExit("Choose either --template-only or --ai-plan (not both).")

    goal = _read_goal(args)
    target_prompts = _clamp_target_prompts(args.target_prompts)

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(args.output) if args.output else _default_output_path(timestamp)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.ai_plan:
        mode = "ai-plan"
        prompts = generate_ai_plan_prompts(goal, target_prompts)
    else:
        mode = "template-only"
        prompts = generate_template_only_prompts(goal, target_prompts)

    content = render_markdown(goal=goal, prompts=prompts, mode=mode, created_at=created_at)
    output_path.write_text(content)

    print("Crux Prompt List Generator")
    print("=" * 60)
    print(f"Goal: {goal[:80]}{'...' if len(goal) > 80 else ''}")
    print(f"Mode: {mode}")
    print(f"Prompts: {len(prompts)}")
    print(f"Output: {output_path}")
    if mode == "template-only":
        implicit_default = (not args.ai_plan) and (not args.template_only)
        print()
        if implicit_default:
            print("NOTICE: You did not pass --ai-plan, so you got the generic template-only scaffold.")
            print("In practice, --ai-plan typically produces goal-tailored prompts (recommended).")
        else:
            print("NOTICE: You selected --template-only (deterministic, no API calls).")
            print("If you wanted goal-tailored prompts, rerun with --ai-plan.")


if __name__ == "__main__":
    main()
