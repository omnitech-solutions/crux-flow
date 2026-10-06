# `nested` fixture set

Hand-authored per the top `README.md`'s format spec, exercising ADR-0131 clauses 3
and 4 (function-local declarations and visibility) for nested `def`/`class`
statements. Nothing here imports or runs Griffe; every page was written by reading
the source and applying the rendering primitives (R1–R7) and page contract by hand.

## Sources

Six sources under `src/`, each under 60 lines, each case exercised once:

| Source | Case(s) exercised |
|---|---|
| `overload_chain.py` | A nested `@overload` chain inside a function, collapsed into one member (clause 3, page contract). |
| `decorated_and_async.py` | A decorated nested `def` (start line = its first decorator's line) and an async nested `def`. |
| `class_in_def_in_class.py` | A class nested in a `def` nested in a class — four heading levels (H2/H3/H4/H5). |
| `branches.py` | Nested `def`s placed under `if`/`else`, `try`, `with` and `for` inside one function. |
| `deep_nesting.py` | Six levels of nested `def`s; the fourth level (`f4`) lands exactly on the H6 clamp and the fifth (`f5`, true depth 5, would be H7) also renders H6. |
| `duplicate_and_rebind.py` | A nested `def` shadowed by a later same-name nested `def` (unconditional duplicate binding, clause 12 gap note) and a nested `def` rebound by a later assignment (clause 12 rebinding gap note). |

## Scenario

Only `all-included` (`include_private: true`) is fixtured here. **Manifest configuration** a test must use to render it: `{"extractor": "python", "include_private": true, "glob": ["**/*.py"]}`, with `nested/src` as the repository root.

## ADR-silent decision (set-level): no `private-excluded` scenario in this set

**This departs from the dispatch's own case list**, which named a second
`include_private: false` scenario for this set ("every function-local declaration
excluded by scope, and private names excluded"). The top `README.md`'s own
Layout section and manifest table — already committed, owned by the dev-lead, and
not editable by this unit — fix `nested/all-included` as the set's **only**
scenario, with this rationale recorded there: "`include_private: false` is not
fixtured here because clause 4 already covers exclusion-by-scope generically in
`real/private-excluded`." That decision was already made and committed before this
unit's work began (this unit fast-forwarded onto it), and it directly settles the
same question the dispatch raised. Per this unit's own hard rule ("Do not edit the
top README.md or any other set"), the top-level decision governs: this set carries
one scenario, `all-included`. No `nested/private-excluded/` directory is created.

The `all-included` pages still exercise every visibility-relevant shape the case
list asked for: `duplicate_and_rebind.py`'s `helper`/`worker` are public names (no
`private-excluded` page needed to prove the private-name exclusion rule itself,
since `real/private-excluded` already fixtures that generically), and every nested
declaration in this set is `public`, which is sufficient to prove clause 3's
placement, start-line and heading rules independent of clause 4's exclusion —
clause 4's own exclusion behavior is proven once, in `real/`, rather than
re-proven per set.

## Notes on rendering choices

- Every source file name contains at least one underscore; every H1 and index
  link title applies R4 (which escapes `_` among its metacharacter set) before R3,
  per the top README's worked example (`authoring_scope.py` → `authoring\_scope.py`).
  Member headings (H2–H6, R5 code spans) do **not** apply R4 — only R3 — so a
  qualified name like `` `dispatch.<locals>.handler_if` `` renders with its
  underscore unescaped inside the backticks.
- `duplicate_and_rebind.py`'s duplicate-binding note follows the top README's
  grammar exactly, backtick-wrapping both the qualified name and the two kind
  words. Its rebinding note (corrected by independent adjudication, PB-0121
  dev-1 developer G) uses `bindings/README.md` decision 4's function-scope
  wording — "...and stays documented above; the rebinding is not
  reconstructed" — not the top README's literal module/class-scope template
  ("...and is not reconstructed; the rebinding is documented above"): `worker`
  is a nested `def`, and clause 3's extension builds members for `def` and
  `class` statements only, so the assignment `worker = helper` builds no
  replacement member and the original nested `def` is what stays documented,
  the same shape `bindings/function_rebindings.py` exercises. The rebinding
  note still backtick-wraps only the qualified name, not the kind words, per
  the literal template text.
- Qualified names use Python's own `__qualname__` convention: `<locals>` is
  inserted only where the *immediately* enclosing scope is a function, not
  merely because some ancestor scope is a function — confirmed against a live
  interpreter (`f().value.__qualname__ == "f.<locals>.Inner.value"`) before
  hand-computing `class_in_def_in_class.py`'s and `deep_nesting.py`'s qualified
  names. This is consistent with, and a sharper reading of, ADR-0131 clause 3's
  "`outer.<locals>.inner`" example.
- Line spans were hand-computed from the committed source, then cross-checked
  with a throwaway, uncommitted stdlib `ast`-based script (never Griffe) that
  walks each function/class body — including through `if`/`try`/`with`/`for`
  compound-statement bodies without opening a new qualname scope — and prints
  each nested `def`/`class`'s qualified name and line span. The script was not
  committed; it is a verification aid only, per this unit's hard rules.
