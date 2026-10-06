# `real/` — real Crux modules

Per the top-level [format spec](../README.md), this set's `expected/` pages are
hand-authored against the sources below, never rendered by a program.

## Sources

Six real, current Crux sources, copied at their committed bytes into `src/` at
their repo-relative path, hashed in `SOURCES.sha256`:

| Source | What it exercises |
|---|---|
| `crux/scripts/authoring_scope.py` | A static `__all__` export. |
| `crux/scripts/base_commit_pin.py` | A private, function-local declaration (`committed_base_commit.<locals>._git`, a nested function). |
| `crux/scripts/crux-config.py` | Private module-level attributes; a PEP 723 script-metadata block. |
| `crux/scripts/generate-codex-agents.py` | A PEP 723 script-metadata block. |
| `tools/jev/answers.py` | A `@property`; a static `__all__` export. |
| `crux/scripts/tests/test_council_refusal.py` | Async functions (async methods on a fake async-context-manager class); private module-level classes and functions; a decorated class with a base class. |

`crux/scripts/tests/test_council_refusal.py` was added to give the Evidence's
"real Crux modules covering async functions" leg an actual real-source
instance — `real/` previously had none. It was found via
`grep -l "async def" crux/scripts -r --include="*.py"`, which returns six
files: five test modules (128–572 lines) and
`crux/scripts/crux/council/async_council.py` (807 lines, already excluded
below for size). No file in `crux/scripts` outside test modules has an
`async def` at all — `async_council.py` is the sole non-test source with one.
`test_council_refusal.py`, at 128 lines, is the smallest of the six and the
only one this pass could hand-verify member-by-member inside the bounded
effort available; it also carries private module-level classes/functions
(`_FakeResponse`, `_FakeAsyncClient`, `_run_seat`, `_vote_body`), giving the
`private-excluded` scenario a fifth source with something to exclude.

Two sources from the planning drafts are dropped entirely (not in `src/` or
`SOURCES.sha256`): `crux/scripts/crux/council/async_council.py` (807 lines)
and `crux/scripts/crux/arch/packs/swift_pbxproj.py` (445 lines) — see the top
README's "Scope narrowing inside `real/`" section for the rationale (hand
member-by-member verification of files that size could not be done honestly
inside this pass's bounded effort).

## Scenarios

| Scenario | `include_private` | What it proves |
|---|---|---|
| `all-members` | `true` | Every declaration `real/src/` carries, rendered per the page contract. |
| `private-excluded` | `false` | The same run with private-named and function-local declarations excluded, each carrying the `_Private declarations not rendered: <N> (include_private is false)._` count line where it excludes something. |

## Manifest configuration a test must use

```json
{"extractor": "python", "include_private": true, "glob": ["**/*.py"]}
```

for `all-members`, and the same with `"include_private": false` for
`private-excluded`. `src/` is the fixture's repository root (`--repo-root
real/src`); no other language key is configured in either run.

## ADR-silent decisions (set-level)

**Try/except double assignment is not a clause-12 duplicate binding.**
`test_council_refusal.py` assigns `HAVE_COUNCIL` once in a `try` body (line 35,
value `True`) and once in its `except` handler (line 37, value `False`).
Clause 12 defines a duplicate binding as "two or more `def` or `class`
statements that bind one name in one scope" — neither assignment here is a
`def` or `class` statement, so clause 12's gap-note machinery does not apply.
This fixture instead treats `HAVE_COUNCIL` as a single `attribute` member.
**Resolved (2026-09-25) by measurement**: an earlier draft of this fixture
inferred, by analogy with clause 12's "last binding in source order" rule for
`def`/`class` statements, that Griffe would keep the `except` handler's
binding (line 37, value `False`) and flagged that inference as uncertain.
Running `griffe.visit` on this file (`uv run python3 -c '...'`, per the
adjudication brief) shows the opposite: Griffe keeps the `try` body's binding
(line 35, value `True`), not the textually-last one. A `try`/`except` is not
sequential reassignment to Griffe's visitor the way two sibling statements
are; the module member ends up carrying the `try` body's value regardless of
the `except` handler's own assignment. The page renders that member per
clause 12 ("the page renders the member Griffe 2.3.0 and the Crux extension
build for the name").

**A private declaration's own-public children are not double-counted.**
`_FakeResponse` and `_FakeAsyncClient` (both private classes) each contain
methods whose own names are not private (plain names or dunders, which the
visibility rule exempts) and which are not function-local declarations
either. The `include_private: false` count line counts "every private-named
member and every function-local declaration ... that all-members documents
and this scenario excludes" (top README, "`include_private` and the visible
declaration count"): read literally, a method that is neither private-named
nor function-local does not itself satisfy either counted criterion, even
though it disappears from the page as a side effect of its parent class being
excluded. This fixture counts only the outermost triggering declaration in
each such case: `_FakeResponse` (1), `_FakeAsyncClient` (1), `_run_seat` (1),
`_vote_body` (1) — total 4 — not its methods. This matches the existing
`base_commit_pin.py` fixture's precedent (`_git`, the one excluded
declaration, is counted once; its caller `committed_base_commit` is public and
not excluded, so there was no existing precedent for the reverse case this
file adds — an excluded class with non-private children).

## Both scenarios render all six pages

Per the top README's rule that a scenario's `expected/` directory is "a
complete, self-contained manifest run," `private-excluded/` carries pages for
all six sources, not only the ones that exclude something. `authoring_scope.py`,
`generate-codex-agents.py`, and `tools/jev/answers.py.md` have no private-named
or function-local declaration, so their `private-excluded` page is
byte-identical to their `all-members` page and carries no count line.
