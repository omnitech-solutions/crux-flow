# audit-docs — output format example (reference)

An illustrative end-of-audit report. Read this when you want a worked example of
the DRIFT / BROKEN / WARNINGS layout the hand-off (pipeline step 6) produces.

## Output format example

```
Audit complete: 3 broken, 8 drift, 5 warnings. 7 auto-fixes applied, 1 recommended. 3 pending your decision.

DRIFT (auto-fixed):
- docs/index.md: research count was 24, actual 26 → updated.
- docs/index.md: adrs count was 6, actual 7 → updated.
- docs/index.md: _Last updated:_ was 2026-04-22 → set to 2026-05-26.
- docs/adrs/index.md: regenerated from frontmatter (1 missing row added).
- docs/promptbooks/index.md: regenerated; PB-0003 progress was 3/12, actual 4/12.
- docs/promptbooks/active/PB-0003-...md: total_prompts was missing → set to 12 (counted from body).
- docs/research/sources.md: 2 rows had `last_source_check` drift → synced from frontmatter.

DRIFT (recommend, not auto-fixed):
- docs/journal/index.md: `generate-journal-index.py --dry-run` reports drift (2026-05 row stale) → recommend `generate-journal-index.py`.

BROKEN (need your decision):
1. docs/adrs/ADR-NNNN: superseded_by: ADR-MMMM, but ADR-MMMM.supersedes does not list ADR-NNNN.
   Recommended: `transition-adr --repair ADR-NNNN ADR-MMMM` (writes both ends).
2. docs/research/raw/2026-04-02/lost-source/ exists but no source page references it.
   Recommended: re-ingest from this folder, or delete after manual review.
3. docs/code/ has 3 orphan pages (not in _meta/manifest.json).
   Recommended: run `extract-code-docs` to regenerate (will delete the orphans).

WARNINGS:
- [contradiction] markers: 4 across 3 research pages — schedule `refresh-research-synthesis`.
- [source updated] markers: 9 from 3 sources — refresh-synthesis recommended.
- ADR-NNNN cites BRIEF-storage-options which is still status: draft.
- PB-0002 has been in_progress 47 days with no advances — consider archiving or re-running.
- Journal 2026-04.md has 1 entry with malformed heading (line 412).
- It's been 14 writes per concern since last audit. Consider audits every ~10.
```
