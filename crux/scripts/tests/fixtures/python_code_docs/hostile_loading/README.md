# `hostile_loading/` — import-time side-effect fixtures

Hand-authored per [[adrs/ADR-0131-extract-python-code-docs-with-griffe-and-scope-dis]]
Decision §1: "The extractor reads target files as source text. It never imports,
executes or installs target code or its dependencies... Hostile fixtures cover a
module whose import would run code, a compiled extension module beside a source, and
an attempt to load a third-party extension. A positive control shows each fixture's
code runs when imported." Nothing here imports or runs Griffe, `ast`, or a renderer;
every page under `expected/` was written by hand against the source below, never by
running the extractor.

## Sources

Twelve Python sources render pages; two files carry no page.

| Source | Hazard, if imported |
|---|---|
| `_hostile_marker.py` | None — the shared, non-hostile helper every hostile fixture uses to locate its marker file. Documented on the page contract like any other module. |
| `h1_import_time_write.py` | A top-level statement writes a marker file. |
| `h2_missing_dependency.py` | A top-level statement writes a marker, then imports a package that does not exist, then raises. |
| `h3_decorator_side_effect.py` | A decorator writes a marker when the decorated function is defined. |
| `h4_default_argument.py` | A default-argument expression writes a marker once, at `def` time. |
| `h5_class_body.py` | A metaclass `__new__` and a bare class-body statement each write their own marker at class-definition time. |
| `h6_dynamic_all.py` | `__all__ = _names()` calls a function that writes a marker and returns the export list dynamically. |
| `h7_module_getattr.py` | A module-level `__getattr__` writes a marker only when a missing attribute is actually accessed, never at import alone. |
| `h9_compiled_extension.py` | A top-level statement writes a marker, exactly like `h1`. Selected and paged normally; see "The compiled-extension sibling" below for the file it sits beside. |
| `h10_extension_registration.py` | A top-level statement writes a marker, then imports `griffe` and calls `griffe.extensions.load("crux_hostile_fake_extension")`, attempting to register a third-party Griffe extension. |
| `hostile_pkg/__init__.py` | A top-level statement writes a marker, then imports its own submodule (triggering `hostile_pkg/sub.py`'s import-time marker too). |
| `hostile_pkg/sub.py` | A top-level statement writes a marker; imported only as a side effect of `hostile_pkg/__init__.py`'s own import, never selected as a top-level fixture on its own (it is still selected and paged by the `**/*.py` glob, since selection is static path matching, independent of any package's own import graph). |

## The compiled-extension sibling

`h9_compiled_extension.cpython-313-darwin.so` sits beside `h9_compiled_extension.py`,
same stem, different suffix. It is **not a real compiled extension** — it is an inert
text placeholder: ASCII bytes stating its own purpose, carrying no executable code and
no Python C-API symbol. Its only job is to prove that the `**/*.py` glob excludes it by
suffix alone, before Griffe or the extractor ever inspects a byte of its content. It
gets no page, because the glob never selects it, not because the extractor recognized
and rejected a real compiled module. It carries no marker-writing code at all — it
could not write one even if opened — so the "sibling file's own marker, never written"
half of `h9_compiled_extension.py`'s positive control (top README, "Hostile-loading
fixtures") is true of this file by construction, not by any behavior a test observes.

## `_hostile_marker.marker_path`'s environment fallback

`_hostile_marker.marker_path(fixture_file, name)` builds the marker path from
`os.environ["CRUX_HOSTILE_MARKER_DIR"]`. Only when that variable is unset does it fall
back to `Path(fixture_file).with_name(name)` — a file named `name` dropped beside
`fixture_file` itself. A test that runs the extractor over this set's `src/` and wants
to assert every marker's absence must set `CRUX_HOSTILE_MARKER_DIR` to a disposable
tempdir first; leaving it unset would, on any accidental import, write markers directly
into this committed fixture tree rather than somewhere disposable.

## Manifest configuration a test must use

`{"extractor": "python", "include_private": true, "glob": ["**/*.py"]}`, run with
`src/` as the fixture's repository root, against the one scenario
`expected/all-included/`. `src/h9_compiled_extension.cpython-313-darwin.so` is present
in `src/` throughout — it is not moved aside for the run — since its exclusion by the
glob, not its absence, is what the fixture proves.

## Positive controls

See the top README's "Hostile-loading fixtures" section for the full ten-row table (one
row per marker-writing fixture, `_hostile_marker.py` excluded since it writes no marker
of its own) naming each fixture's positive control — the proof that its code *does* run
when a test imports it directly, never through the extractor. This set's own two new
fixtures (`h9_compiled_extension.py`, `h10_extension_registration.py`) are included in
that one table rather than restated here, per the top README's "the ONE table every
set's README refers to rather than restating" convention for shared tables.

## What this set does NOT cover

- No dispatcher, renderer, parser adapter or test module. Nothing here imports or runs
  `griffe`.
- No `private-excluded` scenario — every source here is public by name; clause 4's
  exclusion-by-scope behavior is fixtured generically in `real/private-excluded`.
- The negative assertion itself (that a real run over this `src/` writes none of the
  ten markers) is a future test module's job, not this fixture tree's — this directory
  is data only.
