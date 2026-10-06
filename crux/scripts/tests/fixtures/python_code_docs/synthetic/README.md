# `synthetic/` — member-shape and stub-separation fixtures

Hand-authored per the top `README.md`'s format spec and rendering primitives (R1–R7).
Nothing here imports or runs Griffe, `ast`, or any renderer; every page was written by
hand against the sources below.

## Sources and scenarios

Two independent scenarios select two disjoint slices of `src/`, per the top README's
manifest table.

### `base` (`include_private: true`)

| Source | Exercises |
|---|---|
| `members.py` | Every module- and class-scope member shape the kind vocabulary names except an alias: a documented attribute (`LIMIT`), an undocumented attribute (`RATIO`, `square`), an abstract method (`Base.run`), a class method (`Base.make`), a static method (`Base.version`), a `functools.cached_property` (`Base.expensive`), a `@property`/`@x.setter` accessor pair collapsed into one entry (`Base.name`), a class nested in a class (`Base.Inner`), a function whose only content is a locally-defined, returned class (`factory`, `factory.<locals>.Local`, `factory.<locals>.Local.method`), and a docstring carrying a `#` heading line and an embedded fence (`hazard`) — proving the sized-fence rule (R1) widens past the docstring's own longest run rather than colliding with it. |
| `overloads.py` | An `@overload` chain (two variants plus the implementation) collapsing into one member entry, one signature fence listing all three `def` lines, and a line span covering the first variant's first line through the implementation's last line. |
| `pep695.py` | PEP 695 syntax: a `type X = ...` statement (kind `type alias`), a generic function (`first[T]`), and a generic class (`Box[T]`) — each rendering its bracketed type parameters after the name, per R7. |
| `pkg/__init__.py` | A static `__all__` naming a redundant-alias import (`public_fn`, via `from ._impl import public_fn as public_fn`), a plain import exported only through `__all__` and carrying no `as` clause (`Thing`, via `from ._impl import Thing`), and a same-module assignment re-export (`renamed = helper`) — which renders as kind `attribute`, not `alias`, per the Kind vocabulary table (it is a plain assignment statement, not an `import`). Three more imports render no member at all, per clause 11's "a non-exported import renders nothing": `from os.path import join`, `from ._impl import helper`, and `from . import _impl as impl_module` — none is in `__all__`, and the third's bound name (`impl_module`) does not match its imported name (`_impl`), so it is not a redundant alias either. |
| `pkg/_impl.py` | The implementation module the package's aliases resolve into: a private-by-filename (not private-by-name) module carrying three ordinary public members, proving that a module's own file name never affects its members' visibility. |
| `pkg/star.py` | A module whose only content is a wildcard import (`from ._impl import *`), rendering the wildcard-import gap as the sole content of `## Exports`, with no name list and no member of its own. |

### `stubs-separate` (`include_private: true`)

| Source | Exercises |
|---|---|
| `stub_only.pyi` | A `.pyi` stub with no same-stem `.py` runtime module — clause 11's "a stub-only module still gets a page" — rendered from its own stub-only signature (`timeout: float = ...`, the stub ellipsis default rendered verbatim per R7). |
| `stubbed.py` | The runtime half of a stub/source pair, loaded and paged separately from its `.pyi` (clause 11: "the adapter loads stubs separately, because Griffe merges a stub into its source by default"). Carries a private module-level attribute (`_CACHE`) the stub does not declare, since a stub is not required to mirror every runtime-only name. |
| `stubbed.pyi` | The stub half of the same pair: its own page, `stubbed.pyi.md`, sitting beside `stubbed.py.md` — same base name, distinct pages, per clause 11's "`foo.pyi.md` sits beside `foo.py.md`". Its `area` signature carries type annotations the runtime module's own signature omits, proving the two pages are rendered from two independently loaded modules, not one merged view. Its module docstring is absent (a stub file with no leading string literal), rendering no docstring line at all, per the top README's "Module docstring absence" rule — this page is the one committed proof of that rule with `include_private: true`. |

`syntax/broken.py` and `syntax/py314_only.py` are not selected by either scenario; per
the top README's Layout section, they are parse-failure and interpreter-grammar
fixtures reserved for clause 7 (a dispatcher-refusal unit outside this set's scope), and
no `expected/` page exists for either.

## Manifest configuration a test must use

- `base`: `{"extractor": "python", "include_private": true, "glob": ["members.py", "overloads.py", "pep695.py", "pkg/**/*.py"]}`, with `src/` as the fixture's repository root.
- `stubs-separate`: `{"extractor": "python", "include_private": true, "glob": ["stub_only.pyi", "stubbed.py", "stubbed.pyi"]}`, with `src/` as the fixture's repository root.

Both match the top README's manifest table rows for `synthetic`.

## What this set does NOT cover

- No `private-excluded` scenario for either sub-scenario — clause 4's exclusion-by-scope
  behavior is already fixtured generically in `real/private-excluded`, per the top
  README's note for `nested/all-included`, which applies equally here: this set's own
  clause (11, and the general member-shape census) is unrelated to privacy, and its one
  private member (`stubbed.py`'s `_CACHE`) is exercised once, under `include_private:
  true`, to prove clause 4 leaves it visible when private members are included.
- No duplicate-binding or rebinding fixtures (clause 12) — `bindings/` covers those.
- No hostile-string fixtures (clause 10) — `hostile_strings/` covers those.
- No dispatcher, renderer, parser adapter or test module. Nothing here imports or runs
  `griffe`.
