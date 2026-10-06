---
name: transition-decision
description: "Apply a human ratify, reject, or defer verdict to a recovered decision candidate; ratification creates a Proposed ADR or observation."
disable-model-invocation: true
metadata:
  tags: "arch, decision-recovery, adr, observations, ratification"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "ratify decision <id> | reject decision <id> | defer decision <id> | ratify decision <id> as observation"
---

# transition-decision

The human gate for decision recovery. `recover-decisions` proposes `observed` candidates; this skill dispositions them. It is the sole writer of the `adr` op on the recovery path — and, on the observation terminal, the sole writer of the `observation` op on that same path.

Ratify has two terminals under one gate (amends the earlier rule that ratify always creates an ADR, per `docs/AGENTS.md` §17.2): the ADR path (default, unchanged from before) and the observation path (`--as observation`). Both are human-invoked; neither is reachable from `recover-decisions` itself.

## The pipeline

### 0. Resolve per-repo configuration
Run `python3 "${CRUX_PLUGIN_ROOT}/scripts/bionic-config.py"` (compat: `crux-config.py`) from the repo root; use the resolved `docs_dir` wherever this skill says `docs/`, and accept prefixed ids. On exit 1, STOP and surface the error.

This step runs BEFORE every operation below, including the `state.yml` read that starts all three, because `<docs_dir>` is the root of every path this skill touches: the candidate state file, the ADR concern, the observations concern, `manifest.yml`, and two indexes. Without it the loader's textual `docs_dir` refusal and its symlink-containment check never run, and `artifact_prefix` stays unresolved even though step 3 of the observation terminal performs a dual-form prefix scan per `docs/AGENTS.md` §14.3. The observation terminal is the reason this is load-bearing here rather than inherited: the ADR terminal delegates its scaffolding to `propose-adr`, which resolves the config itself, while the observation terminal inlines the scaffolding and so must resolve it here.

## Operations

- **`ratify <id> [--as adr|observation]`** — the candidate is real and worth recording. Default `--as adr`.
  - **`--as adr`** (default) — the candidate is a real, worth-recording decision.
    1. Read the candidate from `<docs_dir>/arch/_recovered/state.yml`.
    2. **Idempotency first:** call `find_ratified_adr(adrs_dir, id)`. If a Proposed ADR already carries `recovered_id: <id>`, do not create another — just complete the state write (recovery from a crash between the two writes).
    3. Otherwise invoke `propose-adr` writing `provenance: recovered` and **`recovered_id: <id>`** into the new ADR's frontmatter; the candidate's source reference is reference material for the human-authored body.
    4. Set the candidate row `state: ratified, adr_id: ADR-NNNN` and save. Write the `adr` op.
  - **`--as observation`** — the candidate is a fact worth recording, but nobody decided it: it belongs in the `observations` concern, not the ADR concern (see `docs/AGENTS.md` §17). This is the **mined ramp**; `propose-observation` is the sibling reconstructed ramp for a human-read fact, and both write the same record shape.
    1. Read the candidate from `<docs_dir>/arch/_recovered/state.yml`.
    2. **Idempotency first:** call `find_ratified_observation(observations_dir, id)` — it correlates on `anchor_id`. For a plain candidate the id already IS `candidate_id(anchor_kind, canonical_anchor)`, the same value the record's `anchor_id` carries, so the check is exact. If a record already carries that `anchor_id`, do not create a second — just complete the candidate state write (crash recovery between the two writes). **For a successor candidate, pass the third argument.** A successor's key is `<anchor_id>+<n>`, so the anchor is recoverable from it, but two records sit on that anchor while the predecessor is still live. Pass the `predecessor_id` the candidate row carries — `find_ratified_observation(observations_dir, id, predecessor_id)` — and the check is exact there too. Called without it, a successor correlates onto its PREDECESSOR's record and the successor is never created.
    3. Otherwise allocate the next `OBS-NNNN` from `observation.next_number` in `<docs_dir>/manifest.yml` (monotonic, never reused, dual-form prefix scan per `docs/AGENTS.md` §14.3), and scaffold the record from `${CRUX_PLUGIN_ROOT}/templates/OBS-template.md`, writing `status: observed` and **`provenance: recovered`** (the mined ramp's value — contrast `propose-observation`, the reconstructed ramp, which writes `provenance: reconstructed`). Carry the candidate's `anchor_id`, its `rule`, and its `evidence` onto the record **verbatim** — the `rule` into the record's `governs` entry, the `evidence` into the record's `evidence`. Verbatim matters: `recover-decisions` decides whether a re-mine has found something new by comparing `claim_digest(rule, evidence)` against the recorded claim, so a record that paraphrases or reformats the candidate it came from is not round-trip stable — the very next re-mine of unchanged code would read the difference as a changed claim and open a successor candidate against the record just created. The canonical frontmatter contract — field set, types, required/optional — is `docs/AGENTS.md` §17.1; this skill does not restate it, and neither does the template it fills in from. `evidence` is `path:line-range` only, repo-relative, never a code excerpt, in the frontmatter and in the body.
    4. Add the concern index row under `<docs_dir>/observations/`, and bump the `docs/index.md` `## Observations (N)` rollup.
    5. Set the candidate row `state: ratified, observation_id: OBS-NNNN` and save (the observation-path analog of `adr_id: ADR-NNNN`). Write the **`observation`** op to `docs/log.md` — NOT the `adr` op; the record is not an ADR.
- **`reject <id>`** — not worth an ADR or an observation. Set `state: rejected`. The id stays suppressed on future scans until its anchor leaves the code (then `recover-decisions` prunes it).
- **`defer <id> [--until YYYY-MM-DD]`** — revisit later. Set `state: deferred, defer_until: <date>`. Re-surfaces only after `defer_until`.

## The writer boundary and the successor path (`docs/AGENTS.md` §17.2)

This terminal is human-invoked, exactly as the ADR terminal is. On `--as observation` it writes `status: observed` and nothing else — no machine path sets any state past `observed`. `transition-observation` is the **only single-record route** past `observed`, to `ratified`, `rejected`, `retired`, or `decided`, and the batch sign-off (`survey-signoff`) is the **only batch route**. Both are human-invoked. `recover-decisions` itself creates neither an ADR nor an observation on any path; it writes only its own candidate state file, including the stale-anchor SIGNAL (`StateFile.signal_stale_anchors`) and any successor candidate.

A ratified record's claim is immutable. A re-mine that finds a recorded `anchor_id` carrying changed rule text or changed evidence does not touch the record — it opens a **successor candidate**, in `observed`, on that same `anchor_id` (`emit_candidate` / `successor_id`). At most one successor is open per anchor: a later re-mine that finds the claim changed again refreshes that row rather than opening a second, so the pending set stays bounded by the anchors themselves. Ratifying the successor and retiring its predecessor is a `transition-observation` act, never an edit here.

## Rules

- **`ratify` is the only ADR- or observation-creating path in recovery.** The `recover-decisions` extraction code cannot create either.
- **Idempotent ratify, both terminals.** Never create a second ADR for a candidate that already has one (`recovered_id` is the correlation key); never create a second observation record for a candidate whose `anchor_id` is already recorded (`find_ratified_observation` is the correlation check). The check covers both candidate forms — a successor needs its `predecessor_id` passed, per step 2.
- `--as observation` writes `status: observed` and `provenance: recovered` — never `ratified` and never `provenance: reconstructed` (that value belongs to `propose-observation`).
- Never hand-edit `state.yml` outside this skill; it is written single-writer via atomic replace, fail-closed on a parse error.

## Red flags — STOP and reconsider

- About to write `status: ratified` on the observation record because the user said "ratify". This skill's observation terminal writes `observed`; ratification of the record itself is a separate act through `transition-observation`.
- About to write `provenance: reconstructed` on this path. `--as observation` is the mined ramp; its value is always `recovered`.
- About to skip `find_ratified_observation` and scaffold a duplicate record for an `anchor_id` already on disk.
- About to call `find_ratified_observation` on a successor candidate WITHOUT its `predecessor_id`. That call answers a different question — "does the predecessor have a record?" — and its answer is always yes.
- About to start any operation without step 0. Every path below is rooted at `<docs_dir>`; resolving it after the first read is resolving it too late.
- About to restate the §17.1 field list here "for convenience". Point at `docs/AGENTS.md` §17.1 and the template; do not copy the keyset.

## See also

- `recover-decisions` — emits the candidates this skill dispositions, and the source of `find_ratified_observation`, `emit_candidate`, `successor_id`, and `StateFile.signal_stale_anchors`.
- `propose-adr` — invoked by `ratify --as adr` to create the Proposed ADR.
- `propose-observation` — the sibling reconstructed on-ramp for the observations concern; writes the same record shape from a human-read fact rather than a mined candidate.
- `transition-observation` — the human gate that moves an observation record past `observed`; this skill never reaches past it.
- `docs/AGENTS.md` §17 — the observations-concern contract (§17.1 frontmatter schema, §17.2 lifecycle, writer boundary, and immutable claim).
- The decision-recovery decision this implements, and its later amendment adding the observation terminal (see the ADR log).
