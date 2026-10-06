---
name: recover-decisions
description: "Mine code for load-bearing decisions without governing ADRs. Record candidates for human disposition; never create ADRs."
metadata:
  tags: "arch, decision-recovery, adr, provenance"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "recover decisions | mine decisions from the code | what load-bearing decisions have no ADR"
---

# recover-decisions

Mine the load-bearing decisions latent in a codebase into ratifiable candidates. The machine proposes; a human ratifies (via `transition-decision`) — the same split as `recover-invariants`. This skill authors **no ADR**.

## Pipeline

1. **Bounded, module-by-module scan.** Partition the tree by the arch `module-graph`; scan one partition at a time (never the whole repo in one context). Exclude generated / vendored / test paths, honor `.gitignore` + a `recover_ignore` list, and consider **direct** dependencies only.
2. **Extract candidates.** For each latent decision, determine its **structural anchor** — the code construct it targets: an import (`anchor_kind: external-dependency`), a module edge (`module-boundary`), an exported symbol or route (`api-contract`), or a config key (`system-of-record` / `cross-cutting-policy`). Canonicalize it (`crux.arch.recover.canonical_anchor`) and compute the id (`candidate_id(anchor_kind, canonical_anchor)`). Record a `path:line-range` **source reference only — never a code excerpt** — and a **redaction-scanned** one-line statement (no secret literal). Set **`domain`** on every candidate: the one-token subject area the rule governs, such as `authentication` or `background-jobs`. The batch review sheet seeds its `proposed_domain` cell from this field. A candidate mined without one still scaffolds at exit 0, and the sign-off then refuses the whole batch until a human writes the domain on the sheet.
3. **Coverage check.** A candidate is emitted only if **uncovered**: no Accepted ADR already carries a matching `(anchor_kind, canonical_anchor)` (via its `recovered_id`/anchor metadata) or a mechanical tag match. The check is deliberately loose — the human filters false positives at ratification.
4. **Read recorded observations, and deduplicate by anchor.** Before proposing any candidate, read every recorded observation (`read_recorded_observations`) under the observations concern — this skill only ever reads that directory. A recorded `anchor_id` whose rule text and evidence are unchanged proposes no candidate (`emit_candidate` returns `recorded`). A recorded `anchor_id` carrying **changed** rule text or evidence opens a **successor candidate** in `observed` on that same `anchor_id`, keyed by `successor_id` (`<anchor_id>+<n>`, where `n` is a monotonic per-anchor counter the state file holds — anchor-derived, with no rule prose in it), carrying `predecessor_id` — the record itself is not touched. At most one successor is open per anchor: a re-mine that finds the claim changed again before a human has disposed the open one refreshes that row instead of opening a second. A recorded `anchor_id` absent from the mined anchor set writes a **stale-anchor signal** to this skill's own candidate state file only (`StateFile.signal_stale_anchors`); it is a signal, never a transition, and a human retires the record through `transition-observation`.
5. **Upsert into the state file.** Add each uncovered candidate (and each successor candidate) to `<docs_dir>/arch/_recovered/state.yml` via `StateFile.upsert_observed` (a rejected/deferred id stays suppressed). Then `prune(present_anchor_ids)` drops rejected rows whose anchor is no longer in the code.
6. **Log.** Write a `recover` op to `<docs_dir>/log.md`: `recover-decisions emitted N candidates`. This op **never ratifies** — only `transition-decision` writes an `adr` op.

## Rules

- **Never author or accept an ADR.** This skill also writes no observation file in any state. Emission is machine; recording is a human act via `transition-decision` (for a decision) or `transition-observation` (for an observation). There is no code path from this skill to `propose-adr`, and no code path from this skill to any observation-file write — the writer boundary applies here exactly as it does everywhere else.
- **The observations directory is read-only to this skill, on every path.** It is read once, to deduplicate against recorded anchors, and never written.
- **No code excerpts, ever** — evidence is `path:line-range`; the statement is redaction-scanned. A secret must never reach `state.yml`.
- Identity keys on the **structural anchor**, never the LLM statement — so re-scans with paraphrased wording produce the same id and stay deduplicated.

## The survey sequence

On a tree with few or no ADRs, this skill is the middle of a five-step survey: derive-arch → recover-decisions → ratify as observation → summarize-adrs → compile-doctrine. The sequence ends with a doctrine built from observations and writes **no ADR**. It is deliberately attended: this skill and `transition-decision ratify <id> --as observation` are human-invoked, so no machine path sets any state past `observed`. `survey.py` runs the two scripted ends and reports the human step in the middle; it never writes an observation file or an ADR, and its payload carries a `boundary` block proving that per run.

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/survey.py" --repo-root . --phase derive    # derive-arch.py
# run this skill (mine candidates into <docs_dir>/arch/_recovered/state.yml)
uv run "${CRUX_PLUGIN_ROOT}/scripts/survey.py" --repo-root . --phase mine      # READ-ONLY: counts + the human command per candidate; exit 1 while one is pending
# a human ratifies each candidate with `transition-decision ratify <id> --as observation`
uv run "${CRUX_PLUGIN_ROOT}/scripts/survey.py" --repo-root . --phase project   # summarize-adrs.py, then compile-doctrine.py
uv run "${CRUX_PLUGIN_ROOT}/scripts/survey.py" --repo-root . --phase verify    # READ-ONLY postconditions: no ADR written, descriptive-only evidenced doctrine
```

`--phase verify` reads the produced artifacts back — the ADR-record count under `<docs_dir>/adrs/`, and each doctrine domain's authority and `path:line-range` evidence — and reports `{adr_files, doctrine_entries, descriptive_entries, prescriptive_entries, entries_without_evidence, clean}`. It exits 0 when no ADR was written and every entry is descriptive and evidenced, 1 with the findings otherwise, and 2 when `--phase project` has not run.

## See also

- `transition-decision` — the human ratify/reject/defer gate.
- `crux/scripts/crux/arch/recover.py` — the pure classifier, identity, and state-file store.
- The decision-recovery decision this implements (see the ADR log).
