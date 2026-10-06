# doctrine — current beliefs, per governs domain

_Fixture projection. Regenerated in a real tree; hand-written here._

## fixture-alpha — backfilled

| handle | citation | rule | source ADR | source_status | disposition | basis |
|--------|----------|------|------------|---------------|-------------|-------|
| ADR-0001/alpha-one | rule:alpha-one | The first fixture rule. | ADR-0001 | Accepted | decided | not-run-bound |
| ADR-0001/alpha-two | rule:alpha-two | The second fixture rule. | ADR-0001 | Accepted | decided | not-run-bound |

## fixture-beta — no-applicable-invariant · authority: descriptive

| handle | citation | rule | source ADR | source_status | disposition | basis |
|--------|----------|------|------------|---------------|-------------|-------|
| ADR-0002/beta-one | rule:beta-one | A run-bound fixture rule. | ADR-0002 | Accepted | decided | run-bound |
| ADR-0002/beta-two | rule:beta-two | A paper fixture rule. | ADR-0002 | Accepted | decided | not-run-bound |
| OBS-0001/observed-thing | rule:observed-thing | An observed fixture fact. | OBS-0001 | ratified | observed | evidence-resolves |

_Observed evidence:_

| observation | evidence | resolves |
|-------------|----------|----------|
| OBS-0001/observed-thing | crux/scripts/carve.py:1-3 | yes |

## Exempt ADRs (1)

_Cohort ADRs excused from carrying a governs block, with the recorded reason._

- ADR-0003 — (no reason recorded)

## Provenance

- `schema`: `3`
- `tool`: `compile-doctrine.py`
