"""untrusted.py — the one bound-and-redact rendering for an untrusted value.

THE problem this closes, stated as a class rather than as its instances:

    a value read out of a file under the observations concern or out of a
    survey surface reaches an output channel — stderr, a log line, a filename,
    a Markdown table cell, a YAML key — at whatever length and with whatever
    bytes the file's author chose.

Three rounds of this cycle each fixed the next INSTANCE. `recover
.frontmatter_problem` stopped quoting PyYAML's source snippet; `summaries_
projection.observation_paths` grew a containment leg; the receipt `digest`
went onto stderr unbounded. Each fix was correct and each left the class open,
because the leak is not a property of any one reader — it is a property of
`f"{value!r}"` over a value nobody bounded.

`redact` is that bound, in one place. The three channel hazards it answers:

  * LENGTH. A 10 MB `digest:` cell in a receipt became a 10 MB line on stderr
    and, through `check_observations`, a 10 MB finding in a JSON envelope. The
    bound is on the RENDERED value and the true length is named beside it, so
    a truncated value can never be mistaken for a whole one.
  * STRUCTURE. A newline in a `domain:` cell forged a second line in the work
    journal and a second row in the doctrine table; `\x1b[2J` cleared the
    reader's terminal; a Cf bidi override reversed the sentence that reported
    the refusal. Every non-printable character is replaced, so no cell can
    manufacture structure on any channel.
  * IDENTITY. A confusable or zero-width character made two distinct values
    render identically, so a refusal naming the offending cell named a cell
    the human could not find.

What it deliberately does NOT do: channel ESCAPING. A Markdown table cell
still needs its `|` escaped, and a filename still needs its allowlist — those
are properties of the channel, not of the value, and they compose ON TOP of
this (`doctrine_projection._md_escape(redact(...))`, `survey_sheet.slugify`).
Folding them in here would give one function two contracts and leave a caller
unable to say which it wanted.

Byte-parity is the property that made the routing safe. For a benign value,
`redact(v)` is `repr(v)` and `redact(v, quoted=False)` is `str(v)`, exactly —
so replacing `{v!r}` with `{redact(v)}` at ~130 sites changed no message any
existing test asserts on, and changed every message a hostile file controls.
"""
from __future__ import annotations

import ast
import builtins

# The rendered-value bound, in characters. 120 is chosen against the widest
# LEGITIMATE value any enrolled reader quotes — a `record_path` under a
# prefixed tree, at ~70 — with room to spare, so the bound never fires on a
# real refusal and always fires on a manufactured one.
LIMIT = 120

# The bound for a COMPOSED REFUSAL an outer handler is re-quoting — a
# `SurveySheetError` reaching `main`'s stderr write, say. Its untrusted parts
# are already routed at `LIMIT` by the code that built it, and the rest is
# this project's own prose, so the value bound is the wrong instrument: it cut
# a real two-sentence containment refusal in half. What is still worth having
# at that boundary is the redaction (any unrouted path loses its control
# characters here) and a ceiling that no legitimate refusal approaches.
MESSAGE_LIMIT = 4000

# The replacement for a character that cannot appear on an output channel.
# U+FFFD is PRINTABLE, which is the point: a backslash escape (`\x1b`) would
# be escaped a second time by the `repr` below and render as `\\x1b`, and a
# reader cannot tell that from a value that really contained a backslash.
REPLACEMENT = "�"


def _render(value, quoted: bool) -> str:
    """The value's own text, before any bound or redaction.

    A `repr` that raises must not take the refusal down with it: every caller
    is ALREADY on its error path, and an exception here would convert a
    document verdict (exit 1, findings on stdout) into a traceback (non-zero,
    empty stdout), which the crux exit convention reserves for a crash.
    """
    if isinstance(value, str):
        return value
    try:
        return repr(value) if quoted else str(value)
    except Exception:
        return f"<unrenderable value of type {type(value).__name__}>"


def redact(value, *, quoted: bool = True, limit: int = LIMIT) -> str:
    """One untrusted value, bounded and redacted, ready for an output channel.

    `quoted=True` is the `!r` replacement and `quoted=False` the bare-`{}`
    one; both are byte-identical to what they replace for any value that is
    short and wholly printable.

    The bound is applied to the RENDERED text and the redaction count reports
    what was replaced WITHIN the shown prefix — the two notes are independent
    and both appear when both fired.
    """
    raw = _render(value, quoted)
    if raw.startswith("<unrenderable value of type "):
        return raw

    total = len(raw)
    head = raw[:limit]
    replaced = sum(1 for ch in head if not ch.isprintable())
    if replaced:
        head = "".join(ch if ch.isprintable() else REPLACEMENT for ch in head)

    notes = []
    if replaced:
        notes.append(f"{replaced} unprintable "
                     f"character{'' if replaced == 1 else 's'} redacted")
    if total > limit:
        notes.append(f"truncated from {total} characters")

    # `repr` is applied to the REDACTED head, so a benign string round-trips
    # to exactly `repr(value)` and a hostile one can no longer smuggle a
    # quote-closing byte out of the quotes.
    body = repr(head) if (quoted and isinstance(value, str)) else head
    return f"{body} [{'; '.join(notes)}]" if notes else body


def parse_problem(subject: str, name: str, exc: Exception) -> str:
    """The refusal for a document that would not parse, quoting NO byte of it.

    The second leg of the same mechanism, and the one place it differs from
    `redact`. PyYAML's `str(exc)` embeds `Mark.get_snippet()` — the offending
    source LINE, verbatim — so a reader that walks files it does not own
    copied a line of an arbitrary file onto stderr, and did so BEFORE any
    containment leg could refuse the file. Bounding that line is not a fix:
    120 bytes of someone else's file is still someone else's file. So the
    parser's message is dropped entirely and the POSITION is kept in its
    place, because a line and a column are integers and hold no content.

    The position is accepted only when both halves really are integers. A
    parser is not obliged to hand back a `Mark`, and a `problem_mark` carrying
    strings would put an attacker's text where the reader expects a number.

    `subject` is caller prose and reaches the message unredacted; `name` is a
    filename off disk and goes through `redact`.
    """
    mark = getattr(exc, "problem_mark", None)
    line = getattr(mark, "line", None)
    column = getattr(mark, "column", None)
    where = ""
    if isinstance(line, int) and isinstance(column, int) \
            and not isinstance(line, bool) and not isinstance(column, bool):
        where = f" at line {line + 1}, column {column + 1}"
    return (f"corrupt {subject} in {redact(name, quoted=False)}{where}: the "
            "document is not readable YAML (its content is not quoted here)")


# ── the binding gate (a helper call that resolves to nothing) ──────────
#
# What stood here was an ENROLLMENT gate: a roster naming every reader of the
# observations concern and of a survey surface, with a per-reader verdict, and
# five rules comparing that roster to the code. It was removed rather than
# finished, and the reasoning is worth keeping because it is the same reasoning
# that put `redact` in one file.
#
# The roster it read (`untrusted-roster.yml`) never existed, nothing imported
# its rules, and no test drove them. Wiring it meant authoring 68 reader rows
# across 13 modules, of which only 21 call a helper — so 47 rows would have
# carried a `safe` or `deferred` verdict whose `because` is a SECURITY CLAIM
# about a function that reads a file this project does not own. Writing 47 such
# claims without auditing 47 functions would have moved the untruth out of the
# machinery and into the data, and a roster of unverified claims reads as
# coverage exactly the way inert machinery does. So the roster went.
#
# What replaced it needs no roster, and therefore makes no claim it cannot
# check. The defect it catches is the one that actually fired: `bionic_config`
# CALLED `redact` and `parse_problem` without IMPORTING either, and nine tests
# died on `NameError`. The old `bypassed` rule pointed the other way — it fired
# when an enrolled reader STOPPED calling a helper — so it could not have seen
# this. A call that resolves to no binding is not a judgement about whether a
# reader ought to be routed; it is a fact about the module's own source, and a
# fact is what a gate can hold.

HELPER_NAMES = frozenset({"redact", "parse_problem"})


def scan_scripts(scripts_dir) -> dict[str, str]:
    """`{path relative to `scripts_dir`: source}` for every non-test module
    under it. Separated from the rule so a test can hand `binding_problems`
    doctored source and drive every direction without touching the tree."""
    from pathlib import Path as _P
    root = _P(scripts_dir)
    out = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith("tests/") or "/tests/" in rel:
            continue
        out[rel] = path.read_text(encoding="utf-8")
    return out


# ── the scope model ───────────────────────────────────────────────────────
#
# The first cut of this walked the whole module with `ast.walk` and collected
# every bound name into one flat set, so a binding ANYWHERE satisfied a call
# ANYWHERE. Six shapes passed that gate and raised `NameError` when run: a
# class attribute read from a method, a comprehension target read after the
# comprehension, a name `del`'d before the call, an `if TYPE_CHECKING:` import,
# a function-local import read from a sibling function, and a `try:` import
# whose `except ImportError:` handler rebinds nothing. The last of those is
# `bionic_config.py:57-68` with its rebind deleted — the real file binds on
# both paths and must stay clean, so the gate has to tell the two apart.
#
# So names are resolved the way Python resolves them: per lexical scope, along
# the chain the call actually sees.

_BUILTIN_NAMES = frozenset(dir(builtins))

# Statements after which control cannot reach the end of a block. Used for one
# question only: whether an `except` handler can fall through to the code after
# the `try`, because a handler that re-raises never leaves a name unbound for
# anyone to read.
_TERMINATORS = (ast.Raise, ast.Return, ast.Continue, ast.Break)
# ... and the calls that end the process. This repo's CLIs end an import
# fallback with `sys.exit(2)` (the crux convention for an environment
# problem), which reaches no code after the `try` any more than a `raise` does.
_EXIT_CALLS = frozenset({"exit", "_exit", "abort"})


def _terminating_call(stmt) -> bool:
    """`sys.exit(...)`, `os._exit(...)`, or a bare `exit(...)` statement."""
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return False
    func = stmt.value.func
    name = getattr(func, "attr", None) or getattr(func, "id", None)
    return name in _EXIT_CALLS


def _falls_through(body: list) -> bool:
    """Whether control can reach the end of `body`."""
    if not body:
        return True
    last = body[-1]
    return not (isinstance(last, _TERMINATORS) or _terminating_call(last))


def _is_type_checking_test(test) -> bool:
    """`if TYPE_CHECKING:` / `if typing.TYPE_CHECKING:` — a block that binds
    names for a type checker and never executes."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _globals_subscript_name(target):
    """`globals()["redact"]` -> `"redact"`, else `None`.

    The one traceable form of a namespace mutation. Anything else done with
    `globals()` opens the module scope instead (see `_Scope.opaque`).
    """
    if not isinstance(target, ast.Subscript):
        return None
    value = target.value
    if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
            and value.func.id == "globals"):
        return None
    key = target.slice
    if isinstance(key, ast.Constant) and isinstance(key.value, str):
        return key.value
    return None


class _Scope:
    """One lexical scope and the names it binds, with lines.

    Lines are kept for exactly one comparison: a binding is live only if it
    happens after the last `del` of the same name in the same scope. Nothing
    else here is flow-sensitive, deliberately — see `binding_problems`.
    """

    __slots__ = ("kind", "parent", "bind_lines", "del_lines",
                 "declared_global", "declared_nonlocal", "opaque")

    def __init__(self, kind: str, parent):
        self.kind = kind          # module | function | class | comprehension
        self.parent = parent
        self.bind_lines: dict[str, int] = {}
        self.del_lines: dict[str, int] = {}
        self.declared_global: set[str] = set()
        self.declared_nonlocal: set[str] = set()
        # True when something put names into this scope that the source does
        # not spell out — a star-import, or an untraceable `globals()` use.
        self.opaque = False

    def bind(self, name: str, lineno: int) -> None:
        if lineno > self.bind_lines.get(name, -1):
            self.bind_lines[name] = lineno

    def unbind(self, name: str, lineno: int) -> None:
        if lineno > self.del_lines.get(name, -1):
            self.del_lines[name] = lineno

    def live(self, name: str) -> bool:
        bound = self.bind_lines.get(name)
        if bound is None:
            return False
        deleted = self.del_lines.get(name)
        return deleted is None or bound > deleted


class _ScopeAnalysis:
    """The module's scope tree, plus every bare helper call and the scope it
    sits in. Built in one pass; resolution happens afterwards, so a call may
    legitimately be satisfied by a binding written below it."""

    def __init__(self, tree):
        self.module = _Scope("module", None)
        self.calls: list[tuple[str, int, _Scope]] = []
        self._visit_body(tree.body, self.module, False)

    # ── resolution ────────────────────────────────────────────────────────

    def resolves(self, name: str, scope: _Scope) -> bool:
        """Whether `name` is visible from `scope`, by Python's own rules."""
        start = scope
        if name in scope.declared_global:
            start = self.module
        elif name in scope.declared_nonlocal and scope.parent is not None:
            start = scope.parent

        chain = []
        cursor = start
        while cursor is not None:
            chain.append(cursor)
            cursor = cursor.parent

        for depth, entry in enumerate(chain):
            # A class body's names are not in the lexical chain of anything
            # nested inside it — only of code written directly in the body.
            if depth and entry.kind == "class":
                continue
            if entry.opaque or entry.live(name):
                return True
        return name in _BUILTIN_NAMES

    # ── the walk ──────────────────────────────────────────────────────────

    def _branch_bindings(self, stmts: list, kind: str) -> set[str]:
        """The names `stmts` would bind, computed in a throwaway scope.

        Used only to answer the `try` question: which names does every path
        out of this statement bind? Nothing it finds reaches the real tree.
        """
        probe = _ScopeAnalysis.__new__(_ScopeAnalysis)
        probe.module = _Scope(kind, None)
        probe.calls = []
        probe._visit_body(stmts, probe.module, False)
        return set(probe.module.bind_lines)

    def _visit_body(self, stmts, scope: _Scope, conditional: bool) -> None:
        for stmt in stmts:
            self._visit(stmt, scope, conditional)

    def _bind_target(self, node, scope: _Scope, conditional: bool) -> None:
        """Bind every `Store` name inside an assignment target."""
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                self._store(sub.id, sub.lineno, scope, conditional)

    def _store(self, name, lineno, scope: _Scope, conditional: bool) -> None:
        if conditional:
            return
        target = scope
        if name in scope.declared_global:
            target = self.module
        elif name in scope.declared_nonlocal and scope.parent is not None:
            target = scope.parent
        target.bind(name, lineno)

    def _visit_arguments(self, args, scope: _Scope, inner: _Scope,
                         lineno: int, conditional: bool) -> None:
        """Defaults and annotations are evaluated in the ENCLOSING scope; the
        parameter names bind in the function's own scope."""
        defaults = list(args.defaults) + [d for d in args.kw_defaults if d]
        for default in defaults:
            self._visit(default, scope, conditional)
        every = (list(args.posonlyargs) + list(args.args)
                 + list(args.kwonlyargs) + [args.vararg, args.kwarg])
        for arg in every:
            if arg is None:
                continue
            if arg.annotation is not None:
                self._visit(arg.annotation, scope, conditional)
            inner.bind(arg.arg, lineno)

    def _visit(self, node, scope: _Scope, conditional: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                self._visit(dec, scope, conditional)
            if node.returns is not None:
                self._visit(node.returns, scope, conditional)
            inner = _Scope("function", scope)
            self._visit_arguments(node.args, scope, inner, node.lineno,
                                  conditional)
            self._store(node.name, node.lineno, scope, conditional)
            self._visit_body(node.body, inner, False)
            return

        if isinstance(node, ast.Lambda):
            inner = _Scope("function", scope)
            self._visit_arguments(node.args, scope, inner, node.lineno,
                                  conditional)
            self._visit(node.body, inner, False)
            return

        if isinstance(node, ast.ClassDef):
            for dec in node.decorator_list:
                self._visit(dec, scope, conditional)
            for base in list(node.bases) + [k.value for k in node.keywords]:
                self._visit(base, scope, conditional)
            self._store(node.name, node.lineno, scope, conditional)
            inner = _Scope("class", scope)
            self._visit_body(node.body, inner, False)
            return

        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp,
                             ast.DictComp)):
            gens = node.generators
            # Only the OUTERMOST iterable is evaluated in the enclosing scope.
            if gens:
                self._visit(gens[0].iter, scope, conditional)
            inner = _Scope("comprehension", scope)
            for index, gen in enumerate(gens):
                if index:
                    self._visit(gen.iter, inner, False)
                self._bind_target(gen.target, inner, False)
                for cond in gen.ifs:
                    self._visit(cond, inner, False)
            if isinstance(node, ast.DictComp):
                self._visit(node.key, inner, False)
                self._visit(node.value, inner, False)
            else:
                self._visit(node.elt, inner, False)
            return

        if isinstance(node, ast.ImportFrom):
            if any(alias.name == "*" for alias in node.names):
                # A star-import puts names here that the source does not name.
                if not conditional:
                    scope.opaque = True
                return
            for alias in node.names:
                self._store(alias.asname or alias.name, node.lineno, scope,
                            conditional)
            return

        if isinstance(node, ast.Import):
            for alias in node.names:
                self._store(alias.asname or alias.name.split(".")[0],
                            node.lineno, scope, conditional)
            return

        if isinstance(node, ast.Global):
            scope.declared_global.update(node.names)
            return

        if isinstance(node, ast.Nonlocal):
            scope.declared_nonlocal.update(node.names)
            return

        if isinstance(node, ast.Try) or (
                hasattr(ast, "TryStar") and isinstance(node, ast.TryStar)):
            self._visit_try(node, scope, conditional)
            return

        if isinstance(node, ast.If) and _is_type_checking_test(node.test):
            self._visit(node.test, scope, conditional)
            self._visit_body(node.body, scope, True)     # never runs
            self._visit_body(node.orelse, scope, conditional)
            return

        if isinstance(node, ast.Assign):
            for target in node.targets:
                injected = _globals_subscript_name(target)
                if injected is not None:
                    if not conditional:
                        self.module.bind(injected, node.lineno)
                    continue
                self._visit(target, scope, conditional)
            self._visit(node.value, scope, conditional)
            return

        if isinstance(node, ast.ExceptHandler):
            if node.type is not None:
                self._visit(node.type, scope, conditional)
            if node.name:
                self._store(node.name, node.lineno, scope, conditional)
            self._visit_body(node.body, scope, conditional)
            return

        if isinstance(node, (ast.MatchAs, ast.MatchStar)):
            if node.name:
                self._store(node.name, node.lineno, scope, conditional)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            self._store(node.rest, node.lineno, scope, conditional)

        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Store):
                self._store(node.id, node.lineno, scope, conditional)
            elif isinstance(node.ctx, ast.Del) and not conditional:
                scope.unbind(node.id, node.lineno)
            return

        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id in HELPER_NAMES:
                    self.calls.append((func.id, node.lineno, scope))
                elif func.id == "globals":
                    # An untraceable namespace mutation. The traceable form,
                    # `globals()["x"] = ...`, is consumed above and never
                    # reaches here.
                    self.module.opaque = True

        for child in ast.iter_child_nodes(node):
            self._visit(child, scope, conditional)

    def _visit_try(self, node, scope: _Scope, conditional: bool) -> None:
        """A `try` binds a name for its successors only when EVERY path out of
        it binds that name.

        This is the `bionic_config` import-fallback idiom and the one place the
        gate is flow-sensitive. `try: from untrusted import redact` /
        `except ImportError: pass` leaves `redact` unbound on the path that
        matters; the same block with `redact = _untrusted.redact` in the
        handler binds it on both. A handler that cannot fall through (it
        re-raises or returns) is excluded from the intersection — nothing
        after the `try` runs on that path.
        """
        kind = scope.kind
        guaranteed = (self._branch_bindings(node.body, kind)
                      | self._branch_bindings(node.orelse, kind))
        for handler in node.handlers:
            if _falls_through(handler.body):
                guaranteed &= self._branch_bindings(handler.body, kind)
        guaranteed |= self._branch_bindings(node.finalbody, kind)

        # Everything inside the branches is walked as CONDITIONAL, so only the
        # names computed above land as bindings. The walk still happens: calls
        # and nested scopes inside the branches are part of the module.
        self._visit_body(node.body, scope, True)
        for handler in node.handlers:
            self._visit(handler, scope, True)
        self._visit_body(node.orelse, scope, True)
        # `finally` runs on every path, so its bindings are not conditional.
        self._visit_body(node.finalbody, scope, conditional)

        if not conditional:
            for name in guaranteed:
                scope.bind(name, node.lineno)


def _bare_helper_calls(tree) -> dict[str, int]:
    """`{helper name: first line it is called as a BARE name}`.

    A qualified call (`_untrusted.redact(...)`, `mod.redact(...)`) is excluded:
    it resolves through the object, so it cannot raise `NameError` for the
    helper's own name and the rule below has nothing to say about it.
    """
    out: dict[str, int] = {}
    for name, lineno, _scope in _ScopeAnalysis(tree).calls:
        if lineno < out.get(name, 1 << 30):
            out[name] = lineno
    return out


def _unresolved_helper_calls(tree) -> dict[str, int]:
    """`{helper name: first line it is called where no binding is visible}`."""
    analysis = _ScopeAnalysis(tree)
    out: dict[str, int] = {}
    for name, lineno, scope in analysis.calls:
        if analysis.resolves(name, scope):
            continue
        if lineno < out.get(name, 1 << 30):
            out[name] = lineno
    return out


def binding_problems(sources: dict[str, str]) -> list[str]:
    """Every bare helper call that no VISIBLE binding of that name can satisfy.

    The claim is exactly this and no more. For each `redact(...)` or
    `parse_problem(...)` written as a BARE name, the module's own AST is walked
    into lexical scopes — module, class, function, lambda, comprehension — and
    the call is reported when no binding of that name is visible from the scope
    the call sits in, following Python's scoping rules: a class body's names
    are invisible to code nested inside it, and a comprehension's targets are
    invisible outside it. Four binding forms are read as NOT binding, because
    at runtime they do not: one whose every occurrence in a scope precedes a
    `del` of the same name there, one inside an `if TYPE_CHECKING:` body, one
    inside a `try`/`except` branch that some fall-through handler leaves
    unbound, and one in a scope the call site cannot see.

    Four things it does NOT claim:

      * It is not otherwise flow-sensitive. Within a visible scope, a binding
        written after the call, or on only one arm of an `if`, still satisfies
        the call. Reporting those would report working code.
      * It does not model dynamic namespaces. A star-import, or any use of
        `globals()` other than `globals()["name"] = ...`, marks the scope
        unknowable and every call in the module then resolves. This is a
        deliberate blind spot: `crux_config.py` really does populate itself
        with `globals().update(...)`, and a gate that reported it would be
        switched off before it caught anything.
      * It says nothing about WHAT the name is bound to. `redact = str`
        satisfies it.
      * It says nothing about qualified calls (`_untrusted.redact(...)`),
        which cannot raise `NameError` for the helper's own name.

    A module whose source will not parse is reported too, under `unparseable:`.
    Skipping it would let a syntax error switch the gate off for that file,
    which is the failure mode every gate in this project is written against.
    """
    problems: list[str] = []
    for rel, src in sorted(sources.items()):
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            problems.append(f"unparseable: {rel} cannot be scanned for helper "
                            f"bindings ({parse_problem('module', rel, exc)})")
            continue
        for name, lineno in sorted(_unresolved_helper_calls(tree).items()):
            problems.append(
                f"unbound: {rel}:{lineno} calls `{name}` and no binding of "
                f"that name is visible from the scope the call sits in — the "
                f"call raises NameError wherever this module actually runs")
    return sorted(problems)
