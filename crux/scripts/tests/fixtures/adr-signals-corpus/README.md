# adr-signals-corpus

Two miniature repo roots read by `crux/scripts/tests/test_adr_signals.py`.
SEVEN of the eight signals have one fixture that trips them (`trips/`) and one
that does not (`quiet/`). `schema_growth` is the exception and reads
`unmeasurable` in both roots, because neither is a work tree; its computed
cases build their own throwaway repository in the test's temporary directory,
as the row below and the closing section both record. `release_cadence`'s
optional history leg is the same: `trips/` trips the changelog legs alone and
reports a null `prep_commits`. Nothing here is a real record; the ids and slugs
are fictitious.

| signal | `trips/` | `quiet/` |
|---|---|---|
| `amendment_fan_in` | ADR-0001 is amended by ADR-0002 and superseded-by-name in ADR-0003 | every count is 0 |
| `carve_out_count` | manifest key + doctrine roster (deduped to 1) + a 2-name `EXEMPT_THINGS` literal = 3 | no exempt key, no roster, no literal = 0 |
| `paper_only` | ADR-0001 is all `not-run-bound` (true); ADR-0003 carries no row (null) | ADR-0001 carries a `run-bound` row (false) |
| `dormancy_days` | ADR-0002 is named by a two-id log entry, so it carries a day count | ADR-0001 is named ONLY by a four-id roster entry, so it is null |
| `friction_citations` | `manifest.yml` records `journal.friction_line_from: 2026-02-14`; three `Friction:` lines at or after that date are counted, one of them dated ON it, one before it is truncated out, one is contentless and skipped, and one sits under a `### ` heading and inside `journal/index.md` — both excluded | `manifest.yml` carries no `journal` key, so the reader has no adoption date and reads `unmeasurable` |
| `release_cadence` | `CHANGELOG.md` carries three dated version headings — 0.1.0, 0.2.0 and 0.3.0 — so the intervals read 5 and 15 days | no `CHANGELOG.md`, so the release record does not exist and the verdict is `unmeasurable` |
| `schema_growth` | `unmeasurable` in both roots: neither is a work tree, so the HEAD leg cannot run — the computed cases build their own throwaway repository in the test's temp directory | `unmeasurable` — same reason |
| `gate_count` | `AGENTS.md` carries a two-row regenerator roster under the four-column header row | no `AGENTS.md`, so the header row this signal reads does not exist |

`trips/bionic/adrs/archive/ADR-0009-archived-thing.md` exists so the archive
exclusion has something to exclude: it amends ADR-0001 and must contribute
nothing to that ADR's fan-in.

`trips/bionic/journal/2026-02.md` carries a `2026-02-14` entry dated exactly on
the recorded adoption date, so the friction boundary is exercised AT the
boundary: without it, reading the boundary as `>` rather than `>=` left the
whole test module green. That entry names no ADR id, so it moves no dormancy
count, and `journal/index.md` counts it.

`trips/bionic/journal/2026-02.md`'s `2026-02-11` entry feeds `DormancyDaysTests`,
whose `ADR-0002 == 9` assertion depends on that entry naming
`[[adrs/ADR-0002]]` on that exact date. An editor changing the friction
fixture must keep that entry, its date, and its `[[adrs/ADR-0002]]` reference
intact, and must add no later entry naming ADR-0001 or ADR-0002.

## Why the forge log is `forge-log.src.md`

`adr-signals.py` reads the forge log at `<root>/.claude/skills/forge-log.md`,
and `tools/sync_stage.py`'s `STAGE_IGNORE_NAMES` strips every `.claude`
directory from the staged public artifact. A committed `.claude/` under this
corpus would therefore exist in the dev checkout and vanish at release, and the
friction-citation counts asserted against it would false-fail the staged test
gate. Each root ships the body as `forge-log.src.md` instead, and
`test_adr_signals.setUpModule` materializes both roots into a temp directory
with `.claude/skills/forge-log.md` written from that file.

## Why no repository is committed here

`schema_growth` and `release_cadence`'s optional leg read the repository's own
history. This corpus commits no repository of its own, for two reasons. A
nested `.git` directory inside a checkout is not a fixture a clone reproduces
faithfully, and the containment check would resolve the ENCLOSING checkout's
work tree rather than the fixture root, so every leg would read crux's own
history instead. Each computed case therefore builds its own throwaway
repository in the test's temp directory — an init, a few commits and a tag —
guarded by `unittest.skipUnless(shutil.which(...))` on that leg alone. Every
`unmeasurable` case needs no repository and is never skipped.

No test reads the ambient repository's own history, changelog or repo-root
`AGENTS.md`. `tools/sync_stage.py` sets `ALLOWLIST_DIRS = ("crux",)`, so the
repo-root `AGENTS.md` and `CHANGELOG.md` are absent from the staged public
artifact, and such a test would false-fail the staged sync gate.

## Why `trips/` carries both instruction filenames

`trips/AGENTS.md` is the roster host. It is what `gate_count` reads, because the
repository's regenerative-outputs roster lives in the canonical instruction file.

`trips/CLAUDE.md` keeps its name on purpose, and its purpose is now different. It is
the corpus's **legacy** instruction file: the shape instruction discovery must classify
and never mutate. The repository's `.bionic.yml` names its path in
`instruction_migration_denylist`, so a migration run over this checkout reports it with
a named disposition and leaves its bytes alone. Renaming it would delete that coverage
and silently change a fixture, which is why both names sit here rather than one.
