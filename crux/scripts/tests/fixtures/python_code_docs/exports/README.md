# `exports/` — ADR-0131 clause 11 fixtures

Hand-authored, per the top `README.md`'s contract. Nothing here imports or runs
Griffe, `ast`, or any renderer; every page was written by hand against the
rendering primitives R1–R7 and the page contract in the parent `README.md`.

## Sources

A six-module package, `src/pkg/`, each module under 25 lines:

| Source | Exercises |
|---|---|
| `pkg/__init__.py` | a static `__all__` listing a defined function (`greet`), a class (`Widget`), and a plain imported name (`util_helper`); a redundant-alias import (`from .util import shared as shared`); a plain non-exported import (`from .util import _private_helper`, the negative case); a wildcard import (`from .util import *`, a gap); the combined-heading rule (one module carrying both a name list and a gap bullet under one `## Exports` heading, per the parent README's clause-8 note). |
| `pkg/util.py` | a bare `import x as x` redundant alias (`import pkg as pkg`, resolving to the package's own `__init__.py`, which is inside the selection) rendered with no `## Exports` heading at all, since this module carries neither a static `__all__` nor a gap — only clause 11's alias-rendering rule, independent of that heading. Also backs `pkg/__init__.py`'s exported/non-exported imports (`util_helper`, `shared`, `_private_helper`). |
| `pkg/dynamic.py` | an `__all__` Griffe 2.3.0 cannot evaluate statically (`__all__ = compute_all()`), rendering the unresolvable-`__all__` gap with no name list. |
| `pkg/unresolved.py` | an exported name whose target sits outside the selection (`from os import path as path`, listed in `__all__`), rendering the unresolved-export gap. |
| `pkg/cycle_a.py`, `pkg/cycle_b.py` | a cyclic alias pair: `cycle_a.a_name` aliases `cycle_b.b_name`, which aliases `cycle_a.a_name` back, each exported through a static one-item `__all__`. Both resolve to the unresolved-export gap (clause 11: "a cycle of aliases"). |

No `.pyi` stub-only module and no dual `.py`/`.pyi` module are fixtured here:
`synthetic/expected/stubs-separate/` already carries both (the top `README.md`
lists this under "check `synthetic/` first; do not duplicate"), and the
assignment's own text names that check.

## Scenario

One scenario, `all-included` (`include_private: true`), per the assignment
("One scenario (include_private true)."). No `private-excluded` scenario is
fixtured here: clause 4's exclusion-by-scope behavior is already generically
covered by `real/expected/private-excluded/` per the parent README's note on
`nested/all-included`, and this set's sources carry only one private-named
member (`pkg/util.py`'s `_private_helper`) — not enough to justify a second,
mostly-duplicate scenario tree in a set whose own clause (11) is unrelated to
privacy.

## Manifest configuration a test must use

```json
{"extractor": "python", "include_private": true, "glob": ["**/*.py"]}
```

matching the parent README's table row for `exports` / `all-included`.

## ADR-silent decisions (set-level)

1. **A bare `import x as x` resolving to the package's own `__init__.py`.**
   Clause 11 names `import x as x` as a redundant-alias form but every natural
   example of it (`import sys as sys`, `import os as os`, …) targets a module
   outside any project's own selection, which would make it an *unresolved*
   export instead of the intended positive case. This set resolves it inside
   the selection by importing the package's own top-level name (`import pkg as
   pkg`) from a sibling module (`pkg/util.py`), since the top package is part
   of the same selection. Rationale: the assignment asks for a working,
   resolved example of this exact syntactic form, and self-import of one's own
   top-level package name is syntactically valid Python that Griffe's static
   pass resolves without executing anything.
2. **An unresolved-export gap suppresses the member heading, matching the
   wildcard/unresolvable-`__all__` cases.** The parent README's "Where and in
   what grammar gap notes render" section states unambiguously, in its closing
   sentence, that "the last three [gap kinds, including unresolved export]
   ... are peers of the `## Exports` list, not member metadata" — settling an
   apparent tension with an earlier sentence in the same section that could be
   read as attaching an unresolved-export gap note to a retained member. This
   set follows the closing, more specific sentence: `pkg/unresolved.py`,
   `pkg/cycle_a.py` and `pkg/cycle_b.py` render their unresolved export as a
   bare `## Exports` bullet only, with no separate member heading for `path`,
   `a_name` or `b_name`.
3. **A module with a redundant-alias export but no static `__all__` and no gap
   carries no `## Exports` heading.** Clause 8 (as restated by the parent
   README) names exactly two triggers for the heading: a static, evaluable
   `__all__`, or an export-related gap. `pkg/util.py`'s `import pkg as pkg` is
   exported (clause 11's redundant-alias route) but that module has neither an
   `__all__` nor a gap, so its page carries the `pkg` member heading with no
   `## Exports` section above it — member rendering and the `## Exports`
   heading are independent per clause 11 and clause 8 respectively.

## What this set does NOT cover

- No stub (`.pyi`) fixtures — see "Sources" above.
- No `private-excluded` scenario — see "Scenario" above.
- No duplicate-binding or rebinding fixtures (clause 12) — out of this set's
  assigned clause (11); `bindings/` covers those.
- No dispatcher, renderer, parser adapter or test module. Nothing here
  imports or runs `griffe`.
