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

Before source discovery, prune retained demonstration holdings: list the holdings once with `uv run "${CRUX_PLUGIN_ROOT}/scripts/authority-view.py" retained-roots --repo-root <repo-root>` and skip each listed root during recursive walks; test one path with `uv run "${CRUX_PLUGIN_ROOT}/scripts/authority-view.py" retained --repo-root <repo-root> <path>` (exit 0, `retained` true or false). Never read held configuration or import held code. Apply this exclusion to extraction, coverage and the present-anchor roster. Held anchors contribute neither coverage nor stale-anchor signals. A layout refusal stops the scan before state mutation. The holding contract is `docs/AGENTS.md` §3; direct concern globs need no recursive scan.

1. **Bounded, module-by-module scan.** Partition the tree by the arch `module-graph`; scan one partition at a time (never the whole repo in one context). Exclude generated / vendored / test paths, honor `.gitignore` + a `recover_ignore` list, and consider **direct** dependencies only.
2. **Extract candidates.** For each latent decision, determine its **structural anchor** — the code construct it targets: an import (`anchor_kind: external-dependency`), a module edge (`module-boundary`), an exported symbol or route (`api-contract`), or a config key (`system-of-record` / `cross-cutting-policy`). Canonicalize it (`crux.arch.recover.canonical_anchor`) and compute the id (`candidate_id(anchor_kind, canonical_anchor)`). Record a `path:line-range` **source reference only — never a code excerpt** — and a **redaction-scanned** one-line statement (no secret literal). Set **`domain`** on every candidate: the one-token subject area the rule governs, such as `authentication` or `background-jobs`. The batch review sheet seeds its `proposed_domain` cell from this field. A candidate mined without one still scaffolds at exit 0, and the sign-off then refuses the whole batch until a human writes the domain on the sheet.
3. **Coverage check.** A candidate is emitted only if **uncovered**: no Accepted ADR already carries a matching `(anchor_kind, canonical_anchor)` (via its `recovered_id`/anchor metadata) or a mechanical tag match. The check is deliberately loose — the human filters false positives at ratification.
4. **Read canonical recorded observations.** Call `read_recorded_observations(observations_dir, repo_root=repo_root, docs_dir=docs_dir)` for the selected tree. Pass the explicit project root and selected tree to `find_ratified_adr` and `find_ratified_observation` too. A supplied recorded map supports correlation; it proves no source approval. Unchanged rule text and evidence propose no candidate. Changed claims open one successor per anchor, carrying `predecessor_id`. The checked route derives its key with `successor_id(anchor_id, n)`, using the state's monotonic per-anchor counter. Refreshing an open successor changes no recorded observation.
5. **Publish checked candidate state.** Retain the full pruned mined roster, including covered anchors, with source references on every row. Call `checked_emit_candidates(repo_root, state, recorded, uncovered_candidates, present_candidates=full_mined_roster, docs_dir=docs_dir)` before `StateFile.save()`. This route checks the whole source roster and canonical correlation before changing caller state. It derives present anchors from validated rows, then stages emission, pruning and stale signals privately. It calls `StateFile.signal_stale_anchors` on that private state using validated present anchors. An absent observed or ratified anchor produces a stale signal; only a human transitions its record. A late refusal preserves rows, successor counters, stale signals and files. Historical reasoning, typed implementation records and held evidence cannot supply candidates or present coverage. Separate factual evidence outside a migrated clause remains admissible. Save and log only after the checked route succeeds. Use `checked_emit_candidate` for a single emission without pruning or stale recalculation. Direct `emit_candidate` and `StateFile.upsert_observed` are low-level operations, not the production miner's source boundary.
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
