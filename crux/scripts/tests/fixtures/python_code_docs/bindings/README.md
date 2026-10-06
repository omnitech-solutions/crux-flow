# `bindings/` — duplicate bindings and rebindings (ADR-0131 clause 12)

Hand-authored fixture set for [[adrs/ADR-0131-extract-python-code-docs-with-griffe-and-scope-dis]]
clause 12: duplicate `def`/`class` bindings of one name in one scope, and rebindings of a
`def`/`class` name by a later non-`def`/`class` binding operation. Written against the top-level
`README.md`'s rendering primitives and page contract, never by running the renderer, Griffe, or
any program that generates a page from a source. Verified only with stdlib `ast` (to confirm line
numbers and `ast.unparse` output) and `grep -n`; nothing here imports or runs `griffe`.

## Manifest configuration a test must use

`{"extractor": "python", "include_private": true, "glob": ["**/*.py"]}`, with `src/` as the
repository root — one scenario, `all-included`, per the assignment ("One scenario (include_private
true)"). No `private-excluded` counterpart is fixtured here: clause 4's exclusion-by-scope is
already covered generically by `real/private-excluded`, and every name in this set is public
anyway (private-visibility interaction with a duplicate/rebinding member is orthogonal to clause
12 and out of this set's scope).

## Sources and what each exercises

| Source | Exercises |
|---|---|
| `module_duplicates.py` | Duplicate `def` bindings at module scope across every branch-context token the metadata schema names: `if`/`else` (`choice`), `try`/`except` (`parser`), unconditional (`loader`), `elif` (`route`), `match`/`case` (`handler`). |
| `scope_duplicates.py` | A class-scope duplicate method (`Router.handle`, unconditional) and a function-scope duplicate nested def (`outer.<locals>.inner`, unconditional), plus an `@overload` chain (`parse`) and a `@property`/`@x.setter` accessor chain (`Router.path`) that are **not** duplicates and carry no gap note. |
| `module_rebindings.py` | Module-scope rebindings of a `def` by a later non-`def`/`class` operation: an assignment (`config`, renders as `attribute`) and a redundant-alias import (`helper`, renders as `alias`, both with a gap note); a non-exported import (`unused`, which renders no member, see "No-retained-member rebindings" below) and a `for`-loop target (`counter`, where Griffe builds no replacement member at all, so the original `def` stays documented, see "The `for`-loop-target rebinding leaves the `def` in place" below). |
| `class_rebindings.py` | The same two rendering rebindings (assignment, redundant-alias import) at class scope (`Widget.render`, `Widget.draw`), matching the module-scope shape. |
| `function_rebindings.py` | The same two rebindings at function scope (`make_config.<locals>.loader`, `make_config.<locals>.reader`), where clause 3 keeps the nested `def` documented instead of replacing it (see "Function-scope rebinding wording" below). |
| `helper.py`, `draw.py` | Small sibling modules (a docstring, one function each), added so `module_rebindings.py`'s `import helper as helper` and `class_rebindings.py`'s `import draw as draw` resolve inside the selection (DL-2, 2026-09-25, dev-lead): a redundant-alias import is an export in its own right (top-level README, "Redundant-alias imports are exports"), and an export whose target does not resolve renders as the unresolved-export gap bullet instead of an alias member — without these two modules, the rebinding-by-import cases this set exists to exercise would fall onto that other branch instead. |

Five case sources, each under 60 lines, each case exercised once, per the assignment's size
bound, plus the two small resolution-target sources named above.

## ADR-silent decisions (set-level)

**1. `elif` and `match`/`case` branch-context tokens.** The metadata `branch_context` enum
(top-level README) is `"unconditional" | "if" | "try" | "match" | null` — it has no separate
`elif` token. Python's own `ast` module represents an `elif` clause as a nested `If` node in the
outer `If`'s `orelse`, so `route`'s duplicate (an `if`/`elif` pair) is recorded and rendered with
branch context `if`, identical to a plain `if`/`else` pair. `handler`'s duplicate (a `match`/`case`
pair) uses `match`. Rationale: the enum is closed and `ast` gives no separate node kind for
`elif`, so reusing `if` is the only reading that keeps the enum's four literal values exhaustive.

**2. The "try-import-then-define" idiom uses two `def` statements, not an import and a `def`.**
Clause 12 defines a duplicate binding as "two or more `def` or `class` statements" binding one
name; an `import` is neither, so an `import` in `try` paired with a `def` in `except` is not a
duplicate binding. An `import` in `try` followed by a `def` in `except` is not a rebinding
either: clause 12 requires the later operation to be the non-`def`. Neither clause-12 rule fits
that shape, so `parser` uses two `def` statements — a shape the phrase "the try-import-then-define
idiom" names only for its familiar branch-context flavor (guarding a name across `try`/`except`),
not literally requiring an import statement. The rebinding sources (`module_rebindings.py`,
`class_rebindings.py`, `function_rebindings.py`) already exercise every required
import-rebinds-a-`def` shape the assignment separately enumerates, at every scope the assignment
asks for, without a `try`/`except` variant — that combination is not in this set's case list.

**3. No-retained-member rebindings (`unused`).** Clause 11 renders an imported name only
when it is exported (a static `__all__` or a redundant-alias import `import x as x` / `from m
import y as y`); `import unused` (no `as` clause repeating the name) is neither, so `unused`'s
alias never renders as a page member — Griffe still builds the `Alias` member here (measured with
`griffe.visit`), the renderer just declines to show a non-exported one under clause 11. Clause 12
nonetheless requires the gap note to render "In every scope," so this set fixes one additional
rendering rule the top-level README does not state: when the rebinding's own result renders no
member, the gap note stands alone as a bullet line — not under `## Exports` (reserved for the
three export-gap kinds), and not attached to any heading — positioned in the page's member order
at the rebinding operation's own source line, using the grammar:

the grammar the top-level README's gap-grammar table now carries as its "no retained
member" row (promoted from this set's own draft):

```
- Gap: `<qualified name>` at <branch context> (<original kind>, line <N>) is rebound by <rebinding kind> at line <M>, which renders no member (<reason>); the original <original kind> is not reconstructed.
```

`<reason>` is a short parenthetical (`not exported` for a non-exported import).

**3a. The `for`-loop-target rebinding leaves the `def` in place (corrected by independent
adjudication, PB-0121 dev-1 developer G).** This README first read `counter`'s `for`-loop-target
rebinding as a third no-retained-member case, on the theory that Griffe's static visitor builds no
member at all for a bare `for` target. Measured directly with `griffe.visit` (no extensions, no
inspection) over `module_rebindings.py`: `mod.members["counter"]` is still `Function('counter', 25,
26)` — the ORIGINAL `def`, unreplaced. Unlike an assignment or an import, a `for` statement's
target never calls Griffe's member-binding path at all, at any scope, so there is no rebinding
member to render and nothing shadows the `def`. That is the same shape decision 4 describes for
function scope — the original `def` stays documented and the rebinding itself builds no member —
so `counter` renders as an ordinary `function` member (`## `counter``, its own signature and
docstring) and carries decision 4's "stays documented above" gap note, not a no-retained-member
bullet. The metadata schema's `retained_kind` example list ("the rebinding operation's own kind —
assignment, import, for_target, etc.") still applies to the *gap note's* `retained_kind` field
(recording `for_target` there), which is a separate question from whether the page renders a
member — it does, and that member is the retained `def`, not a new one for the `for_target`
operation.

**4. Function-scope rebinding wording (also used by decision 3a's module-scope `for`-target
case).** The top-level README's Rebinding grammar module/class-scope row —
"...is rebound by `<rebinding kind>` at line `<M>` and is not reconstructed; the rebinding is
documented above." — describes module/class scope, where Griffe replaces the shadowed `def`/`class`
with the rebinding's own attribute/alias member, so what is "documented above" the note *is* the
rebinding. At function scope, clause 3 says the extension "builds members for `def` and `class`
statements only," so the nested `def` stays documented and the rebinding itself builds no member —
that row's sentence would misdescribe this (nothing is "not reconstructed" here; the
original nested `def` *is* what is reconstructed and shown). This set fixed the mirrored wording
for function scope, now the top-level README's own second Rebinding row, keeping the same
two-clause shape with the roles swapped:

```
_Gap: `<qualified name>` at <branch context> (<original kind>, line <N>) is rebound by <rebinding kind> at line <M> and stays documented above; the rebinding is not reconstructed._
```

**5. Rebinding-kind prose is a bare noun, no article.** "assignment", "import", "for-loop target" —
matching the plain substitution the fixed Rebinding grammar's `<rebinding kind>` slot implies (no
article appears around `<rebinding kind>` in the fixed sentence), and kept identical across the
rendering and no-retained-member grammars above for one consistent vocabulary.

**6. A rebinding-by-assignment attribute's signature is `name = ast.unparse(value)`.** This follows
R7's general "renders as `ast.unparse` of its expression" rule and the top-level README's own
"Exported aliases" precedent (`renamed = helper`). A string-literal value (`config`, `Widget.render`)
unparses with single quotes, `ast.unparse`'s own default for a `Constant` string with no embedded
quote — verified with `uv run python3 -c 'import ast; print(ast.unparse(ast.parse("\"x\"", mode="eval").body))'`
style checks against each literal used here, never by running the renderer.

## What this source set does NOT cover

No `include_private: false` scenario (see "Manifest configuration" above), no `nested/`-style
edge cases outside duplicates/rebindings (deep nesting, decorated nested defs — those are
`nested/`'s scope, not this set's), and no export-gap fixtures (`exports/`'s scope). The
non-exported-import "no retained member" rendering rule this README fixes (decision 3) is a
genuine gap in the top-level README's grammar table; it is flagged in this unit's report to the
dev-lead as a candidate for promotion into the shared spec if another set needs the same shape.
