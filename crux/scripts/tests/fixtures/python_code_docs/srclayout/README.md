# `srclayout/` — a package not at the repository root (PB-0121 Prompt 8 fix round, finding M1)

Hand-authored per the top `README.md`'s format spec and rendering primitives (R1–R7).
Nothing here imports or runs Griffe, `ast`, or any renderer; every page was written by
hand against the sources below, never against renderer output. This set exists
independently of, and was authored without looking at, the M1 code fix (the renderer's
handling of exported/redundant aliases when the exporting package sits under a `src/`
subdirectory rather than at the repository root).

## Sources

A three-module package at `src/mypkg/` — **one level below the fixture's repository
root**, not at it — mirroring a real-world `src/`-layout project (`<repo>/src/<package>/...`).
The fixture's own `src/` directory is the repository root a test passes via
`--repo-root`; the package lives at `src/mypkg/` *inside* that root, so every page's
repo-relative source path carries the `src/` prefix, exactly as `real/`'s pages carry
`crux/scripts/...` for a source that isn't at the repository root either.

| Source | Exercises |
|---|---|
| `src/mypkg/__init__.py` | A static `__all__` (`["f", "Thing", "greet"]`) naming a redundant-alias import (`f`, via `from ._impl import f as f`) and a plain import exported only through `__all__` with no `as` clause (`Thing`, via `from ._impl import Thing`) — both targets resolving to `src/mypkg/_impl.py`, a module that is itself not at the repository root. A third import, `from ._impl import _hidden`, is not in `__all__` and not a redundant alias, so it renders no member (the negative case). A fourth import, `from .sub import helper as helper`, is a redundant alias resolving to a subpackage (`src/mypkg/sub/__init__.py`) rather than a sibling module — exercising a relative import crossing into a nested package, independent of `__all__` (clause 11's alias route is independent of the `__all__` route). A module-level documented function (`greet`) and a class with a method (`Greeter.say`) round out the ordinary member shapes. |
| `src/mypkg/_impl.py` | The implementation module the package's aliases resolve into: a public function (`f`), a class with a method (`Thing.value`), and a private, non-exported function (`_hidden`) that still renders on this module's own page (visibility is per-name, not per export status). |
| `src/mypkg/sub/__init__.py` | The subpackage the relative alias import in `mypkg/__init__.py` resolves into: one public, documented function (`helper`). |

## Why this set is independent of the M1 fix

Per the assignment, M1 is: when a package is not at the repository root, every
exported/redundant alias in it rendered as an "unresolved export" gap before the M1
fix, instead of a resolved alias. The renderer's export-resolution step did not account for
the package's own path prefix. This set's expected pages were written by hand against
this fixture's source files and the top `README.md`'s format spec — never by running the renderer,
Griffe, or any program that generates a page from a source, per the top `README.md`'s
opening contract. The three aliases here (`f`, `Thing`, `helper`) are all authored as
**resolved** alias members (an `alias` kind, a signature fence carrying the import
statement, no gap bullet) because that is what the source and the spec together say a
correct run produces; nothing here was adjusted to match, or was ever compared against,
anything the renderer actually emitted.

## Manifest configuration a test must use

```json
{"extractor": "python", "include_private": true, "glob": ["src/**/*.py"]}
```

with `src/` (this set's own top-level directory) as the fixture's repository root
(`--repo-root srclayout/src`). The glob selects every `.py` file under `src/`,
relative to that root. The repository root itself holds no `.py` file.

## Repo-relative path naming (why pages are titled `src/mypkg/...`, not `mypkg/...`)

Per the top README's page contract, a page's H1 and `doc_path` are the *repo-relative*
source path — relative to the run's `--repo-root`, not relative to the package's own
top-level directory. `real/`'s pages are titled `crux/scripts/authoring_scope.py`, not
`authoring_scope.py`, for the same reason: the repository root there is `real/src/`, and
the source sits several directories below it. This set applies the identical rule: with
`--repo-root srclayout/src`, the file at `srclayout/src/src/mypkg/__init__.py` has
repo-relative path `src/mypkg/__init__.py`, so its H1 is `# src/mypkg/\_\_init\_\_.py`
and its `doc_path` is `python/src/mypkg/__init__.py.md` — the inner `src/` segment is
part of the path, not stripped or treated as the root.

## What this set does NOT cover

- No `private-excluded` scenario — clause 4's exclusion-by-scope behavior is already
  fixtured generically in `real/private-excluded`; this set's own concern (a package not
  at the repository root) is unrelated to privacy. `src/mypkg/_impl.py`'s `_hidden`
  function is the one private member here, exercised once under `include_private: true`
  to prove it renders on its own module's page regardless of its non-exported status.
- No wildcard import, no unresolvable `__all__`, no unresolved-export gap — `exports/`
  already covers every gap kind; this set's sole purpose is a resolved alias whose
  target sits under a non-root package path, per finding M1.
- No duplicate-binding or rebinding fixtures (clause 12) — `bindings/` covers those.
- No hostile-string fixtures (clause 10) — `hostile_strings/` covers those.
- No dispatcher, renderer, parser adapter or test module. Nothing here imports or runs
  `griffe`.
