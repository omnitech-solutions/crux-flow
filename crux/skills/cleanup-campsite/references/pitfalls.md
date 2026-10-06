# cleanup-campsite — rationalization table and common mistakes (reference)

Reinforcement for cleanup-campsite. The "Red flags" list in SKILL.md is the primary
stop-list; read this for the fuller excuse→reality mapping and the
recurring-mistake catalog.

## Rationalization table

| Excuse | Reality |
|---|---|
| "The user clearly wants their README fixed — I'll just edit it for them." | Cleanup never edits prose surfaces. Surface it as a `whats_next.md` suggestion; the user decides. The line exists so cleanup is safe to run unsupervised. |
| "audit-docs already catches this — I'll skip the cleanup check to avoid duplication." | If a finding belongs to audit (graph integrity), audit owns it. If it belongs to cleanup (process state), cleanup owns it. The boundary is sharp: don't second-guess. |
| "This is the user's first session in weeks; let me bypass the dismissal list and surface everything." | Dismissals are durable. Bypassing them silently breaks the user's expressed preference. Surface a P3 housekeeping suggestion to re-evaluate the dismissals if you suspect they're stale. |
| "Three P1 findings are decision-blocking; I'll auto-trigger run-promptbook / log-work / archive-promptbook." | Cleanup proposes; it does not execute. The `proposed action` names the next skill; the user (or Claude under explicit follow-up) invokes it. |
| "The `cleanup-campsite` op entry in `docs/log.md` is verbose; I'll skip the body." | The body's "Top-3 P1 ids" line is grep-friendly. Drop it and audit-docs loses the signal. |
| "The user dismissed this id once; if it changes character, they'd want to see it again." | The dismissal is keyed to the id, not the finding's substance. If a scan rule materially changes a finding's character, the rule must emit a different id (the user gets a fresh, undismissed finding). Don't silently un-dismiss based on perceived change. |
| "Scan rules feel slow; let me parallelize the file reads." | The 18 rules read mostly disjoint file sets and rarely take > 1 second total. Parallelism is YAGNI. |
| "CLN-RETRO-1 fired, but the user just ran a retrospective — they shouldn't have to dismiss it." | If the retrospective ran, its `learning | Retrospective:` journal heading resets the window. CLN-RETRO-1 will auto-clear on the next cleanup run once the heading is present. No dismissal needed. |

## Common mistakes

- **Forgetting to preserve `dismissed_last_seen` counters across runs.** Each dismissal's counter tracks staleness; resetting it on every run defeats the 30-day stale-dismissal housekeeping suggestion.
- **Writing findings for the CURRENT month under CLN-JR-2.** Only closed months trigger that rule. The current month is always "in progress."
- **Citing wiki-links that don't resolve in `proposed action` text.** Always verify wiki-link targets exist before citing them.
- **Treating an empty `docs/whats_next.md` as missing.** A zero-findings file is a valid state; the next run reads it normally.
- **Using a different stable-id format per rule.** Every rule emits `cleanup-<rule-id>-<artifact-slug>` exactly. Slug field is rule-specific but the shape is constant.
- **Forgetting that `dismissed:` MAY be hand-edited.** Users add and remove entries; cleanup must round-trip the list verbatim (modulo `dismissed_last_seen` increments).
- **Skipping the audit-recency check (`CLN-AUD-1`).** It IS a CLN-* scan rule and a finding cleanup must surface. Its id is `cleanup-CLN-AUD-1-recency` (standard `cleanup-<rule-id>-<artifact-slug>` shape — no special-case exemption needed). It checks the log for audit recency but never invokes `audit-docs`.

