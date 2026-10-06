# Python code-doc fixtures — format spec

This tree is the committed, hand-authored source of truth for the shape of a Python
code-doc page, written before the renderer exists, per [[adrs/ADR-0131-extract-python-code-docs-with-griffe-and-scope-dis]]
clause 8: "The independently authored expected pages are committed under
`crux/scripts/tests/fixtures/python_code_docs/` before the renderer is implemented.
Once committed they are the source of truth for this shape, and this clause states
the invariants they must hold." Every page under `*/expected/` was written by hand
against this spec and the rendering primitives below, never by running the renderer,
Griffe, or any program that generates a page from a source. A future test module reads
this tree as its golden set; this tree does not read the renderer.

No test lives here. No implementation lives here. This file and its siblings are data.

## Layout

```
python_code_docs/
  README.md                    this file
  real/
    SOURCES.sha256              sha256 of each src/ file, `<hash>  ./<repo-relative path>`
    src/...                     6 real Crux sources, copied verbatim, repo-relative paths preserved
    expected/
      all-members/              include_private: true  (scenario A)
        index.md
        python/...
      private-excluded/         include_private: false (scenario B)
        index.md
        python/...
  synthetic/
    src/...
    expected/
      base/                     include_private: true, one page per source
        index.md
        python/...
      stubs-separate/           the two-source stub scenario (clause 11)
        index.md
        python/...
  nested/
    src/...
    expected/
      all-included/             include_private: true — every nested-declaration edge case
        index.md
        python/...
  docstrings/
    src/...
    expected/
      all-included/
        index.md
        python/...
  hostile_strings/
    src/...
    expected/
      all-included/
        index.md
        python/...
      hostile-names/
        cases.json               expected H1 + index line for names that cannot be committed as filenames
  bindings/
    src/...
    expected/
      all-included/
        index.md
        python/...
  exports/
    src/...
    expected/
      all-included/
        index.md
        python/...
  hostile_loading/
    src/...                      the 8 planning hostile fixtures + 2 new (compiled extension, third-party extension)
    expected/
      all-included/
        index.md
        python/...
  srclayout/
    src/...                      a package one level below the fixture's own repository root
    expected/
      all-included/
        index.md
        python/...
  srcabsolute/
    src/...                      a src/-layout package re-exporting by its installable name
    expected/
      all-included/
        index.md
        python/...
```

Every `expected/<scenario>/` directory is a complete, self-contained manifest run: an
`index.md` plus a `python/` tree mirroring `src/`'s repo-relative layout, one page per
selected source at `python/<repo-relative source path>.md`.

## Sets

Each set directory owns its own README documenting its sources, scenarios, manifest
configuration and any set-level ADR-silent decision. All ten sets carry one.

- [`real/`](real/README.md) — real Crux modules.
- [`synthetic/`](synthetic/README.md) — synthetic member/overload/PEP-695 coverage and
  the stub-separation scenario.
- [`hostile_loading/`](hostile_loading/README.md) — import-time side-effect fixtures.
- [`nested/`](nested/README.md) — nested-declaration edge cases (clause 3).
- [`docstrings/`](docstrings/README.md) — docstring-normalization edge cases.
- [`hostile_strings/`](hostile_strings/README.md) — target-derived strings that could
  forge Markdown structure.
- [`bindings/`](bindings/README.md) — duplicate bindings and rebindings (clause 12).
- [`exports/`](exports/README.md) — aliases and `__all__` (clause 11).
- [`srclayout/`](srclayout/README.md) — a package one level below the fixture's own
  repository root, proving alias/export resolution when the exporting module is not
  at the repository root (PB-0121 Prompt 8 fix round, finding M1).
- [`srcabsolute/`](srcabsolute/README.md) — a `src/`-layout package re-exporting
  through absolute imports by its installable name (PB-0121 Prompt 8 fix round 2).

## Manifest configuration a test must use to render each set

| Set | `extractor` | glob | `include_private` | notes |
|---|---|---|---|---|
| `real` / `all-members` | `python` | `**/*.py` | `true` | `src/` acts as the repository root |
| `real` / `private-excluded` | `python` | `**/*.py` | `false` | same sources as `all-members` |
| `synthetic` / `base` | `python` | `members.py`, `overloads.py`, `pep695.py`, `pkg/**/*.py` | `true` | excludes `stub_only.pyi`, `stubbed.py`, `stubbed.pyi` and `syntax/*.py` — see note below |
| `synthetic` / `stubs-separate` | `python` | `stub_only.pyi`, `stubbed.py`, `stubbed.pyi` | `true` | `.pyi` and `.py` load and render separately (clause 11) |
| `nested` / `all-included` | `python` | `**/*.py` | `true` | nested-declaration edge cases only; `include_private: false` is not fixtured here because clause 4 already covers exclusion-by-scope generically in `real`/`private-excluded` |
| `docstrings` / `all-included` | `python` | `**/*.py` | `true` | docstring-normalization edge cases |
| `hostile_strings` / `all-included` | `python` | `**/*.py` | `true` | target-derived strings that could forge Markdown structure |
| `bindings` / `all-included` | `python` | `**/*.py` | `true` | duplicate bindings and rebindings (clause 12) |
| `exports` / `all-included` | `python` | `**/*.py` | `true` | aliases and `__all__` (clause 11) |
| `hostile_loading` / `all-included` | `python` | `**/*.py` | `true` | import-time side-effect fixtures (Decision §1) |
| `srclayout` / `all-included` | `python` | `src/**/*.py` | `true` | a package not at the repository root (finding M1) |
| `srcabsolute` / `all-included` | `python` | `src/**/*.py` | `true` | absolute re-exports by installable name under `src/` |

`synthetic/src/syntax/broken.py` and `synthetic/src/syntax/py314_only.py` are not selected
by any scenario in this tree. They are parse-failure and interpreter-grammar fixtures for
ADR clause 7 (resource bounds and parse refusal), which is a dispatcher-refusal behavior
outside this unit's assigned clauses (3, 4, 8, 9, 10, 11, 12). They are kept in `src/` as a
pointer for whichever unit picks up clause 7's fixtures, rather than deleted, since they
were already planning artifacts; no `expected/` page exists for them and none should be
authored here.

A language key for every scenario above is `{"extractor": "python", "include_private": <bool>, "glob": [<the globs column>]}`. A test treats `src/` as the fixture's repository root in one of two ways. Through the CLI, it copies `src/` into a temporary directory, writes a `.bionic.yml` there naming a `docs_dir`, and writes that directory's `manifest.yml` with the scenario's language key. It then runs the dispatcher with `--config <tmp>/<docs_dir>/manifest.yml`; the repository root and the output root both derive from that one config. `test_python_goldens.py` takes this path. In process, it calls `load_selection` and `render_page` directly, as `test_python_page.py` does. Neither way runs inside this fixture tree.

## Rendering primitives (fixed before implementation; recorded verbatim)

R1. Sized fence. `fence(text, info)` = "`" * n + info + "\n" + text + "\n" + "`" * n, where n = max(3, longest run of consecutive backticks in text + 1). Text inside a fence is rendered verbatim (no escaping). Docstrings use info `text`; signatures and the PEP 723 block use info `python` and `toml` respectively.

R2. Docstring normalization, applied before R1: replace "\r\n" with "\n"; apply `inspect.cleandoc`; strip trailing whitespace from every line; strip trailing whitespace from the whole text. A docstring that is empty after normalization renders as the line `_Empty docstring._` instead of a fence. A member with no docstring renders the line `_Undocumented._`.

R3. Visible escapes, applied to every target-derived string rendered OUTSIDE a fence: "\n" -> the two characters `\n`, "\r" -> `\r`, "\t" -> `\t`, any other character in U+0000–U+001F, U+007F or U+0080–U+009F -> `\xNN` (two lowercase hex digits), U+2028 -> the six characters `\u2028`, U+2029 -> the six characters `\u2029`. Nothing else is changed by R3.

R4. Inline Markdown escape (H1 text and index link text): backslash-escape each of these characters: backslash, backtick, `*`, `_`, `[`, `]`, `<`, `>`, `#`, `!`, `&`, `|`, `~`. Apply R4 first, then R3 (so the backslashes R3 introduces are not doubled). Example: `crux/scripts/authoring_scope.py` -> `crux/scripts/authoring\_scope.py`.

R5. Code span (qualified names in member headings, `__all__` entries, target-derived parts of a gap note): apply R3 to the string first; then n = longest backtick run in the result + 1 (minimum 1); if the result begins or ends with a backtick or a space, pad one space inside each delimiter; output "`"*n + [pad] + s + [pad] + "`"*n. A string that spans lines is never passed to R5 un-escaped: R3 has already made it one line.

R5a. Empty-string code span (implementation ruling). Where a code span is required
(R5) and the target-derived string is empty (after R3, still empty — R3 never
introduces characters into an empty string), the span renders as the fixed
text `_(empty string)_` instead of a pair of code-span delimiters wrapping
nothing.

R6. Index link destination for a Python page: `urllib.parse.quote(doc_path, safe="/")`.

R7. Signatures (renderer and hand-authored goldens agree on this text): each annotation, default value, base class, keyword argument, decorator and return annotation renders as `ast.unparse` of its expression, except that a string-literal annotation renders as `ast.unparse` of the expression the string contains (unquoted), and variadic parameters (`*args`, `**kwargs`) carry no default. Decorators render one per line as `@<expr>` above the `def`/`class` line. PEP 695 type parameters render in brackets after the name. The `def`/`class` line itself renders without its trailing colon (`def f0()`, never `def f0():`). The name in a member heading and in its signature line is the qualified name relative to its own module, with no module path or source path before it.

## Page contract (ADR clause 8, restated as the fixtures' contract)

In order, a page carries:

1. An H1 naming the repo-relative source path, with R4 then R3 applied to the path string, e.g. `# crux/scripts/authoring\_scope.py`.
2. The module docstring, rendered per R1/R2 (or `_Empty docstring._` / no line at all — see "Module docstring absence" below).
3. `## Script metadata` — present only when the file carries a PEP 723 `# /// script` block; the TOML content, with the `# ` prefix and the delimiter lines removed, renders in a sized fence (R1) with info `toml`.
4. `## Exports` — present when the module has a static `__all__` Griffe can evaluate, listing every exported name as a comma-separated line of R5 code spans in `__all__`'s own order (not sorted); **or** when the module has no such list but carries an export-related gap (a wildcard import, an unevaluable `__all__`, or an unresolved export) worth naming, in which case the heading hosts only the gap bullet(s) — the two are not mutually exclusive: a module with both a static `__all__` and a wildcard import carries the name list, then the gap bullet(s), under the one heading. **ADR-silent decision**: clause 8 names `## Exports` only for the "static `__all__`" case; this spec widens it to also host a gap with nothing else to attach to, rather than inventing a second heading, because a page with exactly one wildcard import and no `__all__` still needs clause 11's gap named *somewhere*, and a dedicated ad hoc heading per gap kind would multiply headings clause 8 never lists.
5. The members, ordered by source start line. A member's start line is the line of its first decorator, or of the statement itself when it has none (ADR clause 3's rule for nested declarations too — a decorated nested `def` never sorts at its `def` line, always its first decorator's line).

Member headings are backticked qualified names (R5 applied to the qualified name string):
H2 at top level, one level deeper per nesting level, clamped at H6 (level-6 and beyond
render as H6 regardless of true nesting depth). Each member carries, in order: a fenced
signature (R1, info `python`, built per R7), then one italic line naming its kind,
visibility and line span (`_<kind> · <visibility> · <line span>_`), then its docstring
(R1/R2, or `_Undocumented._`/`_Empty docstring._`).

An accessor chain (a `@property` getter plus its `@x.setter`/`@x.deleter`) and an
`@overload` chain (one or more `@overload`-decorated defs plus the implementation)
collapse into one entry: one heading, one signature fence listing every decorated
variant's `def` line followed by the implementation's `def` line (each preceded by its
own decorators), one italic line whose line span covers the first variant's first line
through the last line of the implementation, and the docstring of whichever member in
the chain carries one (the implementation's docstring wins a conflict; if only a variant
has one, that one renders).

A member without a docstring renders `_Undocumented._`. Nothing is synthesized: no
member kind ever gets prose the target's own source does not carry, including an alias
or import member, which is never given a docstring (Python attaches none to an `import`
or assignment statement) and therefore always renders `_Undocumented._`.

### Module docstring absence

**ADR-silent decision.** The ADR specifies how a docstring renders when present but is
silent on a *module* with none. The fixtures fix this: a module with no docstring at all
renders no line and no `_Undocumented._` marker between the H1 and the next section —
the H1 is followed by a single blank line and then directly by `## Script metadata` (if
present) or the first member heading. `_Undocumented._` is reserved for *members*, where
clause 8 uses it explicitly; a module is not a member. Rationale: the ADR text
introduces `_Undocumented._` only in the member-rendering sentence, and applying it to
the page header would visually claim "documentation was expected and is missing" for
every undocumented `__init__.py`, which is common and not a defect.

## Kind vocabulary (ADR-silent decision — reconciled to one fixed list)

The planning drafts used an inconsistent mix of terms. This is the one list a page may
use in its italic line, fixed here because the ADR names no vocabulary of its own:

| Kind | Used for |
|---|---|
| `attribute` | A module- or class-body simple assignment, annotated assignment, or `#:`/trailing-string-documented assignment, that Griffe classifies as an attribute (not discovered via `self.x` in a method body). |
| `instance attribute` | An assignment to `self.<name>` (or another first-parameter name) inside a method body, which Griffe classifies as an attribute of the instance rather than the class. |
| `class` | A `class` statement at module or class scope with no function ancestor. |
| `nested class` | A `class` statement with a function ancestor (clause 3). |
| `method` | A `def` inside a `class` body with no other decorator changing its kind. |
| `class method` | A `def` decorated `@classmethod`. |
| `static method` | A `def` decorated `@staticmethod`. |
| `property` | A `def` decorated `@property`, `@functools.cached_property`, or collapsed into an accessor chain per the page contract above. |
| `function` | A `def` at module scope with no function ancestor. |
| `nested function` | A `def` with a function ancestor, itself not `async` (clause 3). |
| `async function` | An `async def` at module scope with no function ancestor. |
| `nested async function` | An `async def` with a function ancestor (clause 3). |
| `async method` | An `async def` inside a `class` body. |
| `alias` | An imported name that renders under clause 11 (a static `__all__` export or a redundant-alias import). A same-module name-to-name rebinding assignment (`renamed = helper`) is *not* an alias — it renders as `attribute`, since it is a plain assignment statement, not an `import`; only `import`-derived bindings get the `alias` kind. |
| `type alias` | A PEP 695 `type X = ...` statement. |

Rationale for the reconciliation: the drafts' term list already covered every shape the
corpus census found; the one addition this spec makes is separating `attribute` from
`instance attribute` (both appeared in the drafts, undistinguished by any written rule)
along the line Griffe itself draws — a class-body/module-body binding versus a
`self.`-body binding inside a method. No "instance method" kind exists; a plain method
is named `method`, matching the drafts' own usage.

## Visibility words

Exactly two words appear in the italic line's visibility slot: `public` and `private`.
A name is private when it starts with `_` and is not a dunder name (`__x__`), per ADR
clause 4. `include_private: false` never renders a private member at all, so no page in
this tree shows a `private` visibility tag paired with `include_private: false` — the
tag exists only in `include_private: true` scenarios (`all-members`, `base`,
`all-included`, `stubs-separate`), where it documents members `private-excluded` proves
absent by omission.

**ADR-silent decision.** Clause 8's italic line names "kind, visibility and line span" —
three fields. The planning drafts added a fourth, ad hoc `exported` marker on alias
members. This spec drops it: under clause 11 an alias renders on the page *only* when it
is exported, so the marker was true of every alias member that could ever appear and
carried no information. Dropping it also removes a fourth field clause 8 never
described.

## Line-span format

`line N` for a one-line member (start line equals end line); `lines A–B` (en dash,
U+2013, not a hyphen) otherwise. Both `A` and `B` are 1-indexed source line numbers in
the file as committed (LF line endings; a CRLF-source fixture's line numbers are counted
after CRLF→LF normalization, since that normalization happens before any line is
counted — see `docstrings/`).

## Exported aliases

An exported alias's signature fence renders the import or assignment statement exactly
as written (R7 does not apply to a plain `import`/`from` statement — there is no
expression to `ast.unparse`; the fence carries the statement's own source text,
CRLF-normalized like any other fenced text but otherwise verbatim). A `from X import Y as Y`
redundant alias and a `from X import Y as Z` renamed import both render this way; only
the *name bound by the statement* (`Y` or `Z`) becomes the heading and the `## Exports`
entry. `renamed = helper` (a plain assignment re-exporting a name under a new one) is
not an alias — it is a plain assignment statement, not an `import` — so it renders as
kind `attribute`, signature `renamed = helper`, per R7 (a `Name` expression unparses to
`helper`). See the Kind vocabulary table below for the same rule.

## Redundant-alias imports are exports (2026-09-25, ADR-silent decision)

A redundant-alias import (`import x as x`, `from m import y as y`) is an export in
its own right, independent of `__all__` — clause 11 names it as a second, separate
export route. When its target resolves inside the selection it renders as an
`alias` member exactly as described above. When its target does **not** resolve
inside the selection, it renders as the unresolved-export gap bullet under
`## Exports` (`- Gap: \`<name>\` (line <N>) is exported but does not resolve inside
the selection.`) and renders no member of its own — the same fate as a `__all__`
export whose target does not resolve. `bindings/src/helper.py` and
`bindings/src/draw.py` exist so `import helper as helper` and `import draw as draw`
resolve inside `bindings/`'s selection, keeping those two rebinding-by-import cases
on the resolved, member-rendering branch of this rule.

## Instance attribute signature (2026-09-25, ADR-silent decision)

An `instance attribute`'s signature fence carries the assignment statement's own target
expression, unparsed, not a bare member name: `self.status_code = 200` renders as
`self.status_code = 200`, never `status_code = 200`. Rationale: R7 and "Exported
aliases" above both fix the same principle for every other assignment-derived
signature — the fence renders the statement as written (or its `ast.unparse`), never a
synthesized member name — and clause 12 renders what Griffe and the Crux extension
build *for the name*, which is silent on stripping the receiver from the signature
text. Measured against `griffe.visit` on `real/src/crux/scripts/tests/test_council_refusal.py`:
Griffe records the member name as `status_code`, but the statement Griffe attributes the
member to is `self.status_code = 200`; the heading uses the bare name (`` `_FakeResponse.status_code` ``)
and the signature fence uses the full assignment, exactly as every other attribute's
fence already does.

## Where and in what grammar gap notes render

A gap note is a single italicized line, R5-escaping every target-derived token inside it
(a qualified name, a line number is not target-derived and is not R5-escaped, a kind
word is a fixed vocabulary term and is not R5-escaped). It renders **on the page**,
immediately after the italic kind/visibility/line-span line of the member the note is
about, before that member's docstring — so a duplicate-binding or rebinding note sits
between the retained member's metadata line and its docstring. A wildcard import, an
unresolvable `__all__`, or an unresolved export — none of which has a retained member of
its own — renders directly under `## Exports` as its own bullet line, since there is no
member heading to attach it to.

Grammar, by gap kind — the ONE table every set's README refers to rather than restating
(each row is one line, ADR-silent — the ADR states *what* a note names, not its exact
wording; this spec fixes the wording so every fixture agrees):

| Gap kind | Grammar |
|---|---|
| Duplicate binding | `_Gap: \`<qualified name>\` at <branch context> rebinds \`<shadowed kind>\` (line <N>); \`<retained kind>\` (line <N>) is documented._` |
| Rebinding, module/class scope (Griffe replaces the shadowed `def`/`class` with the rebinding's own member) | `_Gap: \`<qualified name>\` at <branch context> (<original kind>, line <N>) is rebound by <rebinding kind> at line <M> and is not reconstructed; the rebinding is documented above._` |
| Rebinding, def/class that stays documented (function scope, and a for-target Griffe does not replace) | `_Gap: \`<qualified name>\` at <branch context> (<original kind>, line <N>) is rebound by <rebinding kind> at line <M> and stays documented above; the rebinding is not reconstructed._` |
| Rebinding, no retained member (the rebinding renders nothing, e.g. a non-exported import) | `- Gap: \`<qualified name>\` at <branch context> (<original kind>, line <N>) is rebound by <rebinding kind> at line <M>, which renders no member (not exported); the original <original kind> is not reconstructed.` |
| Wildcard import | `- Gap: \`<statement text>\` (line <N>) is a wildcard import; its names are not expanded.` |
| Unresolvable `__all__` | `- Gap: \`__all__\` (line <N>) is not statically resolvable; no export list is rendered.` |
| Unresolved export (incl. cyclic alias, and a redundant-alias import whose target does not resolve inside the selection) | `- Gap: \`<name>\` (line <N>) is exported but does not resolve inside the selection.` |

The last four (bulleted, under `## Exports` except the no-retained-member rebinding row,
which stands alone at the rebinding's own source position since it belongs to no
heading) use `-` because they render as line items, not member metadata; the first three
(italicized prose) sit inside a member's block. `<branch context>` uses the same
vocabulary as the duplicate-binding row (`unconditional`, `if`, `try`, `match`).

## Metadata `gaps` row entry shape

**ADR-silent decision.** The ADR requires gaps be "recorded in the metadata" and
"compared by `--dry-run`" but names no schema. This spec fixes one JSON-serializable
dict per gap, appended to a `"gaps"` array on that page's `_meta/manifest.json` row:

```json
{
  "kind": "duplicate_binding" | "rebinding" | "wildcard_import" | "unresolvable_all" | "unresolved_export",
  "qualified_name": "<the qualified name the gap is about, or the statement text for a wildcard import>",
  "line": <int>,
  "shadowed_kind": "<kind vocabulary term, or null when not applicable>",
  "shadowed_line": <int or null>,
  "retained_kind": "<kind vocabulary term, or null when not applicable>",
  "retained_line": <int or null>,
  "branch_context": "unconditional" | "if" | "try" | "match" | null
}
```

`shadowed_kind`/`shadowed_line` name the earlier, shadowed or rebound `def`/`class`;
`retained_kind`/`retained_line` name what the page documents instead (the later
binding, or the rebinding operation's own kind — `assignment`, `import`, `for_target`,
etc. — when the ADR's rebinding clause applies). `branch_context` is `null` for every
gap kind except duplicate binding and rebinding, where it is always one of the three
listed strings or `"unconditional"`.

## `include_private` and the visible declaration count

`private-excluded`'s pages each carry a line directly under `## Script metadata` (or, when there
is none, directly under the module docstring / H1) reading:

`_Private declarations not rendered: <N> (include_private is false)._`

where `<N>` counts every private-named member and every function-local declaration
(regardless of name) that `all-members` documents and this scenario excludes. This
counts the outermost excluded declaration only: members inside an excluded
declaration are never counted, whatever their name.
**ADR-silent decision**: the ADR requires the *exclusion*, not a visible count; the
count line is this spec's way of making an exclusion testable from the page's own bytes
alone (a `--dry-run`-style comparison can assert the count rather than diffing member
lists). If a page excludes nothing, the line is omitted entirely (never rendered as
`_...: 0...._`).

## How each run is configured (recap)

Every scenario in the table above is one dispatcher run over that scenario's `src/` as
repo root, selecting one Python language key with the shown `glob`/`include_private`,
writing to a disposable output root. No scenario configures more than one Python key at
once; the multi-key `--lang` scoping fixtures (D3/Q3) belong to the dispatcher's own
test unit, not this fixture tree, and are out of this unit's scope per the assignment
(U0a–U0c cover only clauses 3, 4, 8, 9, 10, 11, 12).

## Blank-line layout and the trailing newline

- One blank line separates the H1 from the module docstring block (or from the first
  following section when there is no module docstring).
- One blank line separates every top-level section (`## Script metadata`, `## Exports`)
  and every member block from what follows it. This includes inside `## Exports`
  itself, between the `__all__` name-list line and a gap bullet that follows it under
  the same heading (corrected by independent adjudication, PB-0121 dev-1 developer G:
  `exports/expected/all-included/python/pkg/__init__.py.md`'s name list, then a blank
  line, then its wildcard-import gap bullet is the form every set follows).
- Inside a member block: one blank line between the signature fence and the italic
  metadata line; one blank line between the italic metadata line (or a gap note, when
  present) and the docstring block.
- A fence's opening and closing delimiter lines carry no blank line between them and the
  text they wrap (the fence markers are adjacent to the first/last content line, per R1's
  own construction: `"\n" + text + "\n"`).
- Every page ends with exactly one trailing newline after its last content line — no
  trailing blank line.

`index.md`'s exact byte rule (checked against `crux/scripts/extract-code-docs.py`'s
`_write_index`): the file is

```
# Code documentation

_Regenerated by `extract-code-docs`. Do not hand-edit._

## python (<count>)

- [<title>](<destination>)
...one line per page, sorted by doc_path...

```

i.e. header line, blank, italic regen notice, blank, `## python (<count>)` heading,
blank, then one `- [title](destination)` line per page sorted by `doc_path` ascending,
each line terminated by `\n` and *not* followed by a blank line between entries — the
file ends with the last entry line followed by a single `\n` and no further blank line.
`<title>` is the R4-then-R3-escaped repo-relative source path (identical string to that
page's H1 text); `<destination>` is R6 applied to the page's `doc_path`.

## Hostile-loading fixtures

Twelve Python sources under `hostile_loading/src/`: the 8 fixtures from planning
(`h1_import_time_write.py` through `h7_module_getattr.py`, plus `hostile_pkg/`), two new
ones this unit adds — `h9_compiled_extension.py` (a source file sitting beside a
same-stem compiled-extension-shaped file, `h9_compiled_extension.cpython-313-darwin.so`,
which the `**/*.py` glob never selects and which carries no page) and
`h10_extension_registration.py` (a module that attempts, at import time, to register a
third-party Griffe extension via `griffe.extensions` entry-point machinery) — plus
`_hostile_marker.py`, a shared, non-hostile helper module every one of the ten hostile
fixtures imports. `_hostile_marker.marker_path(fixture_file, name)` builds the marker
path from `os.environ["CRUX_HOSTILE_MARKER_DIR"]` (falling back, only when that variable
is unset, to a file named `name` dropped beside `fixture_file`) — never a fixed absolute
path, so a test can point the marker directory at a disposable tempdir and assert its
absence after a run that must not import any of the ten fixtures. `_hostile_marker.py`
is selected by the same glob and is itself documented on the page contract like any other
module (its own import has no side effect, so it carries no marker of its own).

The positive control for each fixture — the proof its code *does* run when a test
actually imports it (not through the extractor, which must never import it) — is:

| Fixture | Positive control |
|---|---|
| `h1_import_time_write.py` | `importlib.import_module` it directly; assert the marker file named in its own top-level `open(...)` call now exists. |
| `h2_missing_dependency.py` | import it directly inside a `try/except ImportError`; assert the `ImportError` is raised (proving the top-level `import <missing package>` really executed) rather than assuming it. |
| `h3_decorator_side_effect.py` | import it directly; assert the marker the class decorator wrote at class-definition time exists, distinct from any marker a method call would write. |
| `h4_default_argument.py` | import it directly; assert the marker written by the default-argument expression (evaluated once, at `def` time) exists. |
| `h5_class_body.py` | import it directly; assert the marker written by a bare statement in the class body (executed at class-definition time, not at instantiation) exists. |
| `h6_dynamic_all.py` | import it directly; assert the marker the `__all__ = compute_all()` call wrote exists, and that `mod.__all__` is the computed list (proving the call ran, not a static list). |
| `h7_module_getattr.py` | import it directly, then access an attribute through module `__getattr__`; assert the getattr-side marker exists only after the access, never at import alone. |
| `hostile_pkg/` (`__init__.py`, `sub.py`) | import `hostile_pkg.sub` directly; assert both the package `__init__` marker and the submodule marker exist, proving package import triggers `__init__` execution. |
| `h9_compiled_extension.py` | import it directly; assert its marker exists — the *extractor* must select and page the `.py` file while never touching the compiled-shaped sibling, which a second assertion (sibling file's own marker, never written) checks. |
| `h10_extension_registration.py` | import it directly; assert the marker its `griffe.extensions.load(...)` call attempts write exists, and separately assert that a Griffe `load()` call made *by the extractor's own harness* over this fixture's directory never sees that extension registered (proving the extractor's Griffe session loads no extension this fixture tries to add). |

A future test suite runs the *extractor* (once it exists) over `hostile_loading/src/`
and asserts every one of these ten marker files is **absent** after the run — the
negative half of the same fixtures the table above gives a positive control for, per
this project's `false-green-test-guard` convention (an absence assertion is paired with
a positive control proving the fixture is capable of producing the marker at all).

## Stub-name and hostile-name fixtures that cannot be committed as files

`hostile_strings/expected/hostile-names/cases.json` holds cases whose *file name* (not
file content) would need to contain a line break, which a git-committed path cannot
carry. Structure:

```json
{
  "cases": [
    {
      "description": "filename containing a line break",
      "source_repo_relative_path_escaped": "weird\\nname.py",
      "expected_h1": "# weird\\nname.py",
      "expected_index_line": "- [weird\\nname.py](python/weird%0Aname.py.md)"
    }
  ]
}
```

`source_repo_relative_path_escaped` is a Python-string-literal-escaped spelling of the
repo-relative path the case is about (never the raw bytes); a test that exercises this
case builds the real file at test-run time, in a disposable tempdir, from that escaped
literal (e.g. `path_escaped.encode().decode("unicode_escape")`), runs the extractor over
it, and compares the real H1/index line against `expected_h1`/`expected_index_line`
decoded the same way. This file is data; nothing here is executed by this unit.

## Scope narrowing inside `real/` (unilateral decision, documented)

The planning drafts selected seven real Crux sources. Two are dropped from this
tree's `src/` (and `SOURCES.sha256`) entirely: `crux/scripts/crux/council/async_council.py`
(807 lines) and `crux/scripts/crux/arch/packs/swift_pbxproj.py` (445 lines). **Rationale:**
this unit's contract is that every expected page is authored and verified by hand against
the source, member by member, line span by line span; hand-verifying two files of that size
byte-for-byte, inside the bounded effort available for this pass, could not be done
honestly. The five remaining planning sources (`authoring_scope.py`, `base_commit_pin.py`,
`crux-config.py`, `generate-codex-agents.py`, `tools/jev/answers.py`) exercise most of the
shapes the book Evidence names for the "real Crux modules" leg: private and undocumented
members (`base_commit_pin.py`, `crux-config.py`), a nested function
(`base_commit_pin.py`'s `committed_base_commit.<locals>._git`), a property
(`tools/jev/answers.py`'s `NoulAnswer.is_uncertain`), a static `__all__` export
(`authoring_scope.py`, `tools/jev/answers.py`), and a PEP 723 script-metadata block
(`crux-config.py`, `generate-codex-agents.py`). What they did **not** exercise from real
code was an async function/method. A sixth source,
`crux/scripts/tests/test_council_refusal.py` (128 lines), was added for this: it is the
smallest of the six `crux/scripts` files carrying `async def` outside
`async_council.py` (807 lines, already excluded above for size — no non-test source
under `crux/scripts` has an `async def` at all). It contributes async methods on a fake
async-context-manager class, plus private module-level classes and functions
(`_FakeResponse`, `_FakeAsyncClient`, `_run_seat`, `_vote_body`). `real/` still does not
exercise a duplicate binding/rebinding in real Crux source — the `bindings/` and
`docstrings/` sets carry that. See [`real/README.md`](real/README.md) for the full source
table, the manifest configuration, and this addition's two set-level ADR-silent
decisions (a plain try/except double assignment is not a clause-12 duplicate binding; an
excluded private declaration's own-public children are not double-counted).
**Open item:** re-adding the two size-dropped files (or two similarly-sized
real files) is a follow-up if full "real corpus" breadth at that size is wanted; it was not
completed in this pass.

`real/expected/private-excluded/` renders all six sources, matching what a real
`include_private: false` run over `all-members`'s selection produces: pages+index. Three
sources carry a private or function-local declaration to exclude (`crux-config.py`: 3
private attributes; `base_commit_pin.py`: 1 private nested function;
`test_council_refusal.py`: 4 private module-level declarations) and so carry the
`_Private declarations not rendered: <N> (include_private is false)._` count line. The
other three (`authoring_scope.py`, `generate-codex-agents.py`, `tools/jev/answers.py`)
have no private-named or function-local declaration, so their `include_private: false`
page is byte-identical to their `include_private: true` page and carries no count line
(the rule: "if a page excludes nothing, the line is omitted entirely"). This tree commits
those three identical copies rather than omitting them, because a scenario's `expected/`
directory is defined above as "a complete, self-contained manifest run" — every page a
real run over that scenario's selection would render — and a partial scenario would leave
a test with no golden to diff against for three of the six rendered pages.

## What this unit does NOT cover (named per the assignment's narrowing)

- No renderer, dispatcher, parser adapter or test module. Nothing here imports or runs
  `griffe`.
- No `--lang` multi-key scoping, no output-containment (D1/D6), no `--dry-run` byte
  comparison logic, no golden-byte regression for the Elixir/fallback extractors — those
  are dispatcher-repair units (D1–D6) outside clauses 3/4/8/9/10/11/12.
- `nested-excluded` (Q1 option (b), rejected 3–0) and `stubs-merged` (Q6 option (b),
  rejected 3–0) are retired: their draft directories are removed from this tree, not
  kept as dead fixtures, because the council decided against both and a rejected shape
  in the golden set would mislead a future reader into thinking it is still a live
  configuration.
