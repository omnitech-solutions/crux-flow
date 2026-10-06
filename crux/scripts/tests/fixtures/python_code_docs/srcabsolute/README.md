# `srcabsolute/` — absolute re-exports of an installable package (PB-0121 Prompt 8 fix round 2)

Hand-authored per the top `README.md`'s format spec and rendering primitives (R1–R7).
Nothing here imports or runs Griffe, `ast`, or any renderer; every page was written by
hand against the sources below, never against renderer output. This set exists
independently of, and was authored without looking at, the resolver code change,
unit regressions, docs, or golden test-class wiring for the behaviour it fixtures — a
developer running "extract docs" on a src/-layout package that re-exports through
absolute imports by its installable name gets the alias rendered, not a false
unresolved-export gap.

## Sources

A six-module package at `src/acme/` — **one level below the fixture's repository
root**, not at it — mirroring `srclayout/`'s own `src/mypkg/` layout, but exercising
absolute imports (`from acme.x.y import ...`) rather than relative ones
(`from .x import ...`).

| Source | Exercises |
|---|---|
| `src/acme/__init__.py` | An absolute redundant-alias re-export by installable name (`value`, via `from acme.core.values import value as value`) — case 1. An absolute import exported only through a static `__all__`, no `as` clause (`Widget`, via `from acme.core import Widget`, with `"Widget"` in `__all__`) — case 2. The first hop of an all-absolute multi-hop chain (`shared_value`, via `from acme.relay import shared_value as shared_value`, continued in `acme/relay.py`) — case 3. The first, absolute hop of a mixed chain whose second hop is a relative import (`merged`, via `from acme.subpkg import merged as merged`, continued in `acme/subpkg/__init__.py`) — case 4. Two positive controls that must remain gaps: an absolute redundant alias to a module not in the selection (`nope`, via `from acme.missing import nope as nope`) and a stdlib redundant alias (`json`, via `import json as json`) — case 5. A locally defined, documented function (`greet`) rounds out the ordinary member shapes and is also named in `__all__`. |
| `src/acme/core/__init__.py` | The target of case 2's absolute import: a documented `Widget` class with a documented method (`spin`). |
| `src/acme/core/values.py` | The ultimate, concrete target both case 1 (`value`) and case 3's chain (`shared_value`) resolve to. Also carries this set's one private, undocumented member (`_scale`), which plays both roles the assignment asks for in one declaration. |
| `src/acme/relay.py` | The second, absolute hop of case 3's chain: `from acme.core.values import shared_value as shared_value`, re-exporting the name `acme/__init__.py` re-exports from it in turn. Carries no static `__all__` and no gap, so its own page has no `## Exports` heading (clause 8/clause 11 are independent, matching `exports/pkg/util.py`'s bare-alias-no-heading case). |
| `src/acme/subpkg/__init__.py` | The second, relative hop of case 4's mixed chain: `from .impl import merged as merged`, continuing the chain `acme/__init__.py` starts with an absolute import. Also carries no `## Exports` heading, for the same reason as `relay.py`. |
| `src/acme/subpkg/impl.py` | The concrete target case 4's chain resolves to: a documented `merged` function. |

## Why this set is independent of the fix it targets

Per the assignment, the fixed behaviour is: an absolute import re-exporting a name by
its installable package name (rather than a relative import) previously rendered as an
"unresolved export" gap even when its target sits inside the selection, because the
resolver matched a target only against path-derived names (`src.acme.core.values`),
which an absolute import by installable name (`acme.core.values`) never equals when the
package sits under `src/`. This set's expected pages were written by hand against this
fixture's source files and the top `README.md`'s format spec. The four resolvable cases here (`value`,
`Widget`, `shared_value`, `merged`) are all authored as **resolved** `alias` members (an
`alias` kind, a signature fence carrying the import statement, no gap bullet, per the
top README's "Redundant-alias imports are exports" and "Exported aliases" sections)
because that is what the source and the spec together say a correct run produces;
nothing here was adjusted to match, or was ever compared against, anything the resolver
or renderer actually emitted. The two remaining cases (`nope`, `json`) are authored as
the existing unresolved-export gap bullet, exactly as `exports/pkg/unresolved.py`
already fixtures, because their targets genuinely sit outside the selection — the fix
this set targets does not, and must not, change that outcome.

## Manifest configuration a test must use

```json
{"extractor": "python", "include_private": true, "glob": ["src/**/*.py"]}
```

with `src/` (this set's own top-level directory) as the fixture's repository root
(`--repo-root srcabsolute/src`). The glob selects every `.py` file under `src/`,
relative to that root. The repository root itself holds no `.py` file. The
installable package name (`acme`) resolves against the parent directory of its
top-of-chain package — here, the inner `src/` directory at
`srcabsolute/src/src/` — so `acme.core.values` names the file at
`srcabsolute/src/src/acme/core/values.py`.

## Repo-relative path naming (why pages are titled `src/acme/...`, not `acme/...`)

Per the top README's page contract, a page's H1 and `doc_path` are the *repo-relative*
source path — relative to the run's `--repo-root`, not relative to the package's own
top-level directory or its installable name. This set applies the identical rule
`srclayout/` does: with `--repo-root srcabsolute/src`, the file at
`srcabsolute/src/src/acme/__init__.py` has repo-relative path `src/acme/__init__.py`, so
its H1 is `# src/acme/\_\_init\_\_.py` and its `doc_path` is
`python/src/acme/__init__.py.md` — the inner `src/` segment is part of the path, not
stripped or treated as the root, even though the *import statements themselves* name
the package by its installable name (`acme`, no `src/` prefix) rather than its
repo-relative path.

## What this set does NOT cover

- No ambiguity across multiple roots, and no namespace-package fixtures — those belong
  to unit regressions elsewhere, per the assignment's own narrowing of this slice to the
  independent expected-page authoring only.
- No `private-excluded` scenario — clause 4's exclusion-by-scope behavior is already
  fixtured generically in `real/private-excluded`; this set's own concern (absolute
  re-export resolution) is unrelated to privacy. `src/acme/core/values.py`'s `_scale`
  function is the one private member here, exercised once under `include_private: true`
  to prove it renders on its own module's page regardless of its non-exported status.
- No wildcard import and no unresolvable `__all__` — `exports/` already covers those gap
  kinds; this set's sole purpose is the absolute-import resolution behaviour (resolved
  and unresolved) named by cases 1–5 above.
- No duplicate-binding or rebinding fixtures (clause 12) — `bindings/` covers those.
- No hostile-string fixtures (clause 10) — `hostile_strings/` covers those.
- No resolver code, dispatcher, renderer or parser adapter. Nothing here imports or runs
  `griffe`. The one-class wiring that registers this set lives in
  `crux/scripts/tests/test_python_goldens.py`, not in this directory.
