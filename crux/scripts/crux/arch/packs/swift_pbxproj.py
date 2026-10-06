"""swift_pbxproj.py — the `project.pbxproj` reader (ADR-0130 clauses 1, 2, 4
and 14).

Public API:

    read_pbxproj(raw: bytes) -> PbxprojRead
    list_field(doc, obj_id, field_name, pbxproj_path, residuals, seen=None)
    id_field(doc, obj_id, field_name) -> str | None | NOT_AN_ID
    dict_field(doc, obj_id, field_name, pbxproj_path, residuals, seen=None)
    string_field(doc, obj_id, field_name, pbxproj_path, residuals, seen=None)
    isa_of(obj) -> str | None
    non_list_residual(doc, obj_id, field_name, pbxproj_path)
    field_line(doc, obj_id, field_name) -> int | None
    PbxprojDocument.first_item_line(obj_id, field_name, wanted) -> int | None
    NOT_AN_ID, NON_LIST_FIELD_DETAILS, NON_DICT_FIELD_DETAILS,
    NON_STRING_FIELD_DETAILS

Both Xcode readers read ten of the eleven list fields `NON_LIST_FIELD_DETAILS`
names through `list_field` (ADR-0130 clauses 2 and 14): a present value that
is not a list renders one `unsupported-project-form` line and is never
iterated. Group
descent reads `children` itself and renders the same line through
`non_list_residual`. `id_field`, `dict_field`, `string_field` and `isa_of` are
the typed reads of the scalar-id, dictionary and display-string fields
(ADR-0130 clauses 2 and 14). A non-string id is `NOT_AN_ID`, which names no
object, so it renders as a dangling id does. A dictionary or display string
field of another type renders one `unsupported-project-form` line.

A `rootObject` that is absent, not a string, names no object, or names an
object that is not a `PBXProject` refuses the read as `malformed`.

`PbxprojRead` is either a `PbxprojDocument` — the root object id, an
`objects` mapping from object id to its field dict, a line span per
top-level object (`(start_line, end_line)` from `"ID = {"` to `"};"`), a
per-value line and a per-list-item line, both keyed by a path tuple (for an
object's fields, rooted at the object id) — or a `PbxprojRefusal(residual_class, kind)` where
`residual_class` is `"project-unreadable"` and `kind` is one of the four
closed refusal kinds this module can itself produce: `undecodable`,
`unsupported-format`, `malformed`, `too-deep`. ADR-0130 clause 14 names three
other `project-unreadable` kinds. `not-regular` and `read-failed` belong to
the safe-read boundary (`crux.arch.core._safe_read_bytes`, ADR-0130 clause
2), because this module never touches the filesystem. `entity-declaration`
belongs to the workspace XML reader (`swift_xcinputs`, ADR-0130 clause 11). A caller composes this
module's pure grammar with `_safe_read_bytes` to get the full per-file
boundary the clause describes: `_safe_read_bytes`'s OS-level refusals map to
`not-regular`/`read-failed`, and this module's own catch-all (below) covers
every exception a parse step can raise, `RecursionError` and `MemoryError`
included, as `malformed`.

An `objectVersion` outside the fixture-pinned set `{56, 76, 77}` does NOT
refuse the read (ADR-0130 clause 4: "reading proceeds"). It sets
`PbxprojDocument.unsupported_object_version = True`, which the caller
renders as one `unsupported-project-form` line; the document — objects,
spans, everything — is still returned intact.

The grammar is TOTAL and ITERATIVE: every byte input returns a document or a
classified refusal, and no nested structure is walked by Python's own call
stack. An explicit stack of parse frames stands in for recursion, so a
hostile deeply-nested fixture is refused by the 64-level bound of ADR-0130
clause 2 (checked BEFORE each descent, i.e. before a new frame is pushed)
rather than by `RecursionError` — and if something in a parse step still raises anyway
(`RecursionError`, `MemoryError`, or anything else), `read_pbxproj`'s own
try/except is the final backstop, classifying it `malformed` rather than
letting it escape.

Names come from FIELDS, never from `/* ... */` comments: a comment is
whitespace to this tokenizer, skipped before any token is produced, and
never captures its text into a value. A duplicate key at any dict level, a
merge-conflict marker (`<`, which opens no construct this grammar
recognises, so a conflict marker's leading `<<<<<<<` fails as an ordinary
unexpected-token error), an XML or binary property list, and a missing
`// !$*UTF8*$!` header are each refused before or during the parse, per
ADR-0130 clause 4.

Imports only the standard library. Never imports `swift.py` — `swift.py`
imports this module's read function instead, keeping the dependency one way.

An unquoted token admits `/` as an ordinary character (`_UNQUOTED_CHARS`):
`Foo//bar` with neither operand quoted tokenizes as the single
unquoted string `"Foo//bar"`, not as `Foo` followed by a comment. Xcode's own
writer QUOTES any value carrying a `/` that isn't a path-like unquoted token
it chose to emit bare, so this shape is not one real `.pbxproj` output is
expected to produce; it is named here as what the grammar itself does with
it, not as a refusal this module performs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: The closed set of `project-unreadable` kinds this module can itself
#: produce (see module docstring for the other three).
PBXPROJ_KINDS = frozenset({"undecodable", "unsupported-format", "malformed", "too-deep"})

#: `objectVersion` values the committed fixtures pin (ADR-0130 clause 4).
_SUPPORTED_OBJECT_VERSIONS = frozenset({"56", "76", "77"})

_UTF8_HEADER = "// !$*UTF8*$!"

#: The nesting bound: 64 levels are read and a 65th is refused, checked
#: before the descent that would open it (ADR-0130 clause 2).
_MAX_NESTING = 64

_UNQUOTED_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_$+/:.-"
)
_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f",
    "v": "\v", '"': '"', "'": "'", "\\": "\\",
}


#: Tags for the hashable stand-in `_item_key` builds for a list or dict item.
#: A private object never equals any parsed value, so a stand-in can equal
#: only another stand-in of the same shape.
_LIST_KEY = object()
_DICT_KEY = object()
_OTHER_KEY = object()


def _item_key(value):
    """A hashable stand-in for a parsed value that equals another value's
    stand-in exactly when the two values are `==`. A string (every id Xcode
    writes) is its own key. A list or dict item, which the grammar admits
    wherever a string can sit, becomes a tagged tuple: a list's holds a
    tuple of its items' keys, and a dict's holds a frozenset of its
    `(key, item key)` pairs."""
    try:
        hash(value)
        return value
    except TypeError:
        pass
    if isinstance(value, (list, tuple)):
        return (_LIST_KEY, type(value) is tuple, tuple(_item_key(v) for v in value))
    if isinstance(value, dict):
        return (_DICT_KEY, frozenset((k, _item_key(v)) for k, v in value.items()))
    # The grammar yields only strings, lists and dicts; any other unhashable
    # value can equal only itself here.
    return (_OTHER_KEY, id(value))


@dataclass(frozen=True)
class PbxprojDocument:
    """A successfully parsed `project.pbxproj`."""

    root_id: str
    objects: dict = field(default_factory=dict)
    object_spans: dict = field(default_factory=dict)          # id -> (start_line, end_line)
    value_lines: dict = field(default_factory=dict)           # path tuple -> line
    list_item_lines: dict = field(default_factory=dict)       # path tuple -> [line, ...]
    object_version: str | None = None
    unsupported_object_version: bool = False
    # (obj_id, field) -> {item key: first line}; filled by `first_item_line`.
    # A cache of this document's own fields, never of filesystem state, so it
    # lives and dies with the document and takes no part in equality.
    _first_item_lines: dict = field(default_factory=dict, compare=False, repr=False)

    def first_item_line(self, obj_id, field_name, wanted):
        """The line of the first item of `objects[obj_id][field_name]` that
        equals `wanted`, counting only items that have a recorded line, or
        None when no such item exists. The field's index is built once, in
        one pass, so a caller that asks once per item spends time linear in
        the list, not quadratic (ADR-0130 clause 2)."""
        key = (obj_id, field_name)
        index = self._first_item_lines.get(key)
        if index is None:
            index = {}
            obj = self.objects.get(obj_id)
            items = obj.get(field_name) if obj is not None else None
            # Only a list has items: a string's characters and a
            # dictionary's keys are never items (ADR-0130 clause 14).
            if not isinstance(items, list):
                items = []
            for line, item in zip(self.list_item_lines.get(key, []), items):
                index.setdefault(_item_key(item), line)
            self._first_item_lines[key] = index
        return index.get(_item_key(wanted))


#: ADR-0130 clauses 2 and 14: the eleven fields the Xcode reader reads as
#: lists, each with the fixed `unsupported-project-form` detail it renders
#: when the field is present and holds any other type. The value is never
#: iterated and nothing is applied from it. A detail
#: names the field only, never its value, a name or a path.
NON_LIST_FIELD_CLASS = "unsupported-project-form"
NON_LIST_FIELD_DETAILS = {
    "targets": "the project's targets field is not a list and is not read",
    "packageReferences": "the project's packageReferences field is not a list and is not read",
    "children": "a group's children field is not a list and is not descended",
    "dependencies": "a target's dependencies field is not a list and is not read",
    "buildPhases": "a target's buildPhases field is not a list and is not read",
    "fileSystemSynchronizedGroups": "a target's fileSystemSynchronizedGroups field is not a list and is not read",
    "packageProductDependencies": "a target's packageProductDependencies field is not a list and is not read",
    "files": "a build phase's files field is not a list and is not read",
    "exceptions": "a folder-synced root's exceptions field is not a list and is not applied",
    "explicitFolders": "a folder-synced root's explicitFolders field is not a list and is not applied",
    "membershipExceptions": "an exception set's membershipExceptions field is not a list and is not applied",
}


def non_list_residual(doc, obj_id, field_name, pbxproj_path):
    """The one `unsupported-project-form` residual for `objects[obj_id]`'s
    `field_name` holding a value that is not a list: at the field's own
    value line, or the object's first line when none was recorded."""
    line = doc.value_lines.get((obj_id, field_name))
    if line is None:
        line = doc.object_spans.get(obj_id, (None, None))[0]
    return (NON_LIST_FIELD_CLASS, pbxproj_path, line, NON_LIST_FIELD_DETAILS[field_name])


def list_field(doc, obj_id, field_name, pbxproj_path, residuals, seen=None):
    """The items of the list field `objects[obj_id][field_name]`, for one of
    the eleven fields `NON_LIST_FIELD_DETAILS` names (ADR-0130 clauses 2
    and 14).

    An absent field has no items and renders nothing, and so does `()`. A
    present field of any other type -- a string, a dictionary, `""` or `{}`:
    its type decides, never its truthiness -- has no items either. It
    appends one `non_list_residual` to `residuals` and is never iterated or
    tested with `in`, so no character of a string and no key of a dictionary
    is ever read as an id or a path. `seen`, a set the caller holds for one
    pass, keeps a field that pass reaches twice to one line: an exception set
    two roots list, or a build phase two targets list."""
    obj = doc.objects.get(obj_id)
    value = obj.get(field_name) if isinstance(obj, dict) else None
    if value is None:
        return ()
    if isinstance(value, list):
        return value
    key = (obj_id, field_name)
    if seen is None or key not in seen:
        if seen is not None:
            seen.add(key)
        residuals.append(non_list_residual(doc, obj_id, field_name, pbxproj_path))
    return ()


# ── ADR-0130 clauses 2 and 14: typed reads of scalar and dictionary fields ──
#
# The grammar admits a list or a dictionary wherever a string can sit. Each
# helper below reads one field by its TYPE, never its truthiness, so no
# caller ever hashes, iterates, `in`-tests or `.update`s a value of the
# wrong type (ADR-0130 clause 2: no content exits 2 or collapses a project).


class _NotAnId:
    """The type of `NOT_AN_ID`: hashable by identity, equal only to itself,
    so it is never a key of `objects` or of any id-keyed mapping."""

    __slots__ = ()

    def __repr__(self):
        return "NOT_AN_ID"


#: What `id_field` returns for a present id field that is not a string. A
#: lookup with it finds nothing, so every site renders it as it renders a
#: dangling string id: same class, detail and location (ADR-0130 clauses 2
#: and 6). No site tests for it by identity.
NOT_AN_ID = _NotAnId()


def _field_value(doc, obj_id, field_name):
    obj = doc.objects.get(obj_id) if isinstance(obj_id, str) else None
    return obj.get(field_name) if isinstance(obj, dict) else None


def field_line(doc, obj_id, field_name):
    """The field's own value line, or the object's first line when none
    was recorded."""
    line = doc.value_lines.get((obj_id, field_name))
    if line is None:
        line = doc.object_spans.get(obj_id, (None, None))[0]
    return line


def _render_once(residuals, seen, key, residual):
    if seen is None or key not in seen:
        if seen is not None:
            seen.add(key)
        residuals.append(residual)


def id_field(doc, obj_id, field_name):
    """An object-id field of `objects[obj_id]`: the string, `None` when the
    field is absent, or `NOT_AN_ID` when it holds any other type (ADR-0130
    clauses 2 and 6: a dangling id renders `unresolved-reference`). A
    non-string id finds no object, so the caller's dangling-id branch
    renders it, with no exception."""
    value = _field_value(doc, obj_id, field_name)
    if value is None or isinstance(value, str):
        return value
    return NOT_AN_ID


def isa_of(obj):
    """An object's `isa` when it is a string, else `None`: a non-string
    `isa` is an unrecognised `isa` at every site, with no line of its own
    (ADR-0130 clause 4: an object is interpreted by its `isa`)."""
    isa = obj.get("isa") if isinstance(obj, dict) else None
    return isa if isinstance(isa, str) else None


#: ADR-0130 clauses 2 and 14 (a construct the reader does not interpret
#: renders `unsupported-project-form` and is never applied): the three
#: dictionary fields the Xcode reader reads, each with the fixed
#: `unsupported-project-form` detail it renders when present with any other
#: type. `requirement` has its own detail in `swift_xcode_products`.
NON_DICT_FIELD_DETAILS = {
    "buildSettings": "a build configuration's buildSettings field is not a dictionary and is not read",
    "explicitFileTypes": "a folder-synced root's explicitFileTypes field is not a dictionary and is not applied",
    "platformFiltersByRelativePath": ("an exception set's platformFiltersByRelativePath field is not a "
                                      "dictionary and is not applied"),
}


def dict_field(doc, obj_id, field_name, pbxproj_path, residuals, seen=None):
    """The dictionary field `objects[obj_id][field_name]`, or `{}`.

    Absent and `{}` render nothing. Any other type -- a string, a list, `""`
    or `()`: its type decides, never its truthiness -- renders one
    `unsupported-project-form` with `NON_DICT_FIELD_DETAILS[field_name]` at
    the field's value line and returns `{}`, so nothing is read or applied
    and the value is never iterated, `in`-tested or merged. `seen`, a set
    the caller holds for one pass, keeps a field that pass reaches twice to
    one line (ADR-0130 clause 14)."""
    value = _field_value(doc, obj_id, field_name)
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    _render_once(residuals, seen, (obj_id, field_name), (
        NON_LIST_FIELD_CLASS, pbxproj_path, field_line(doc, obj_id, field_name),
        NON_DICT_FIELD_DETAILS[field_name]))
    return {}


#: ADR-0130 clause 14: the display string fields whose cell renders `—`
#: with one `unsupported-project-form` when they hold another type.
#: `objectVersion` sits outside `objects`; `read_projects` renders its detail
#: for an absent or non-string value.
NON_STRING_FIELD_DETAILS = {
    "name": "a target's name field is not a string and is not read",
    "productName": "a package product dependency's productName field is not a string and is not read",
    "objectVersion": "objectVersion is absent or not a string, which is outside the tested set",
}


def string_field(doc, obj_id, field_name, pbxproj_path, residuals, seen=None):
    """The string field `objects[obj_id][field_name]`, or `None` when it is
    absent or holds another type. A present non-string value appends one
    `unsupported-project-form` with `NON_STRING_FIELD_DETAILS[field_name]`
    at the field's value line, deduplicated by `seen` like `dict_field`.
    With `residuals=None` the read is typed and renders nothing: the caller
    renders its own existing line (`productType`, whose non-string value
    renders the unmapped product-type line)."""
    value = _field_value(doc, obj_id, field_name)
    if value is None or isinstance(value, str):
        return value
    if residuals is not None:
        _render_once(residuals, seen, (obj_id, field_name), (
            NON_LIST_FIELD_CLASS, pbxproj_path, field_line(doc, obj_id, field_name),
            NON_STRING_FIELD_DETAILS[field_name]))
    return None


@dataclass(frozen=True)
class PbxprojRefusal:
    """A refused read. `residual_class` is always `"project-unreadable"` for
    what this module itself can classify; `kind` is one of `PBXPROJ_KINDS`."""

    residual_class: str
    kind: str


class _GrammarError(Exception):
    """Internal control-flow only: a parse step failed. Never escapes
    `read_pbxproj`. `kind` is `"malformed"` unless overridden (e.g. `"too-deep"`)."""

    def __init__(self, message: str, kind: str = "malformed"):
        super().__init__(message)
        self.kind = kind


# ── tokenizer ────────────────────────────────────────────────────────────────
#
# Token shapes: ("STR", value, line), ("{", None, line), ("}", None, line),
# ("(", None, line), (")", None, line), ("=", None, line), (";", None, line),
# (",", None, line), ("EOF", None, line).

def _u_escape(text: str, j: int) -> int:
    """The UTF-16 code unit of the `\\U` escape whose `U` sits at `text[j]`.

    The four characters after the `U` must be hex digits, or the escape is a
    grammar failure. A surrogate code unit is returned as-is; `_tokenize`
    pairs it or refuses it, so no lone surrogate reaches a decoded string."""
    hexd = text[j + 1:j + 5]
    if len(hexd) != 4 or not all(h in "0123456789abcdefABCDEF" for h in hexd):
        raise _GrammarError("bad \\U escape")
    return int(hexd, 16)


def _tokenize(text: str) -> list:
    tokens = []
    i, n, line = 0, len(text), 1

    def advance(count: int) -> None:
        nonlocal i, line
        line += text.count("\n", i, i + count)
        i += count

    while i < n:
        c = text[i]
        if c == "\n":
            advance(1)
            continue
        if c in " \t\r\f\v":
            advance(1)
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise _GrammarError("unterminated /* comment")
            advance(end + 2 - i)
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            advance((n if end < 0 else end) - i)
            continue
        if c in "{}()=;,":
            tokens.append((c, None, line))
            advance(1)
            continue
        if c == '"':
            start_line = line
            j = i + 1
            out = []
            while True:
                if j >= n:
                    raise _GrammarError("unterminated string")
                ch = text[j]
                if ch == "\n":
                    line += 1
                if ch == '"':
                    j += 1
                    break
                if ch == "\\":
                    j += 1
                    if j >= n:
                        raise _GrammarError("unterminated escape")
                    e = text[j]
                    if e == "U":
                        v = _u_escape(text, j)
                        j += 5
                        if 0xDC00 <= v <= 0xDFFF:
                            raise _GrammarError("lone surrogate escape")
                        if 0xD800 <= v <= 0xDBFF:
                            # A `\U` escape is one UTF-16 code unit: a high
                            # surrogate must be followed at once by a `\U`
                            # low surrogate, and the pair is one scalar.
                            if text[j:j + 2] != "\\U":
                                raise _GrammarError("lone surrogate escape")
                            w = _u_escape(text, j + 1)
                            if not 0xDC00 <= w <= 0xDFFF:
                                raise _GrammarError("lone surrogate escape")
                            v = 0x10000 + ((v - 0xD800) << 10) + (w - 0xDC00)
                            j += 6
                        out.append(chr(v))
                        continue
                    if e in "01234567":
                        k = j
                        while k < j + 3 and k < n and text[k] in "01234567":
                            k += 1
                        out.append(chr(int(text[j:k], 8)))
                        j = k
                        continue
                    out.append(_ESCAPES.get(e, e))
                    j += 1
                    continue
                out.append(ch)
                j += 1
            tokens.append(("STR", "".join(out), start_line))
            i = j
            continue
        if c in _UNQUOTED_CHARS:
            j = i
            while j < n and text[j] in _UNQUOTED_CHARS:
                j += 1
            tokens.append(("STR", text[i:j], line))
            advance(j - i)
            continue
        raise _GrammarError(f"unexpected character {c!r} at line {line}")
    tokens.append(("EOF", None, line))
    return tokens


# ── iterative parser ─────────────────────────────────────────────────────────
#
# An explicit stack of frames stands in for recursion. Each frame is either a
# dict-in-progress or a list-in-progress, with the path its OWN container
# will be recorded under: `()` for the document root, `(key,)` for a
# top-level field (the objects map is `("objects",)`), and inside `objects`
# a path rooted at the object id, where an int is a list item's index.

class _Frame:
    __slots__ = (
        "kind", "container", "start_line", "path", "is_objects_map",
        "pending_key", "pending_key_line",
    )

    def __init__(self, kind: str, start_line: int, path: tuple, is_objects_map: bool = False):
        self.kind = kind                       # "dict" | "list"
        self.container = {} if kind == "dict" else []
        self.start_line = start_line
        self.path = path
        self.is_objects_map = is_objects_map
        self.pending_key = None
        self.pending_key_line = None


def _parse(tokens: list):
    """Iteratively parse `tokens` into (value, object_spans, value_lines,
    list_item_lines). Raises `_GrammarError` on any grammar failure,
    including a nesting breach (`kind="too-deep"`).

    An explicit `stack` of `_Frame`s stands in for recursion: opening a `{`
    or `(` pushes a frame (refusing first if the push would breach the
    64-level bound), and closing it pops the frame and ATTACHES its
    finished container to whichever frame is now on top — the dict field
    or list slot that was waiting for it. A dict value's trailing `;` and a
    list item's trailing `,` are consumed at that attach point, not at the
    moment the value's open bracket was seen, because a container value's
    own content has to be parsed first. A `)` is only checked there; the
    list consumes it when it closes.
    """
    object_spans: dict = {}
    value_lines: dict = {}
    list_item_lines: dict = {}
    pos = 0
    stack: list = []

    def cur():
        return tokens[pos]

    def advance():
        nonlocal pos
        pos += 1

    def expect(kind):
        t = cur()
        if t[0] != kind:
            raise _GrammarError(f"expected {kind!r} at line {t[2]}, found {t[0]!r}")
        advance()
        return t

    def open_container(kind: str, start_line: int):
        if len(stack) >= _MAX_NESTING:
            raise _GrammarError("nesting past the bound", kind="too-deep")
        parent = stack[-1] if stack else None
        if parent is None:
            path, is_objects_map = (), False
        elif parent.kind == "dict":
            key = parent.pending_key
            path = (key,) if parent.is_objects_map else parent.path + (key,)
            is_objects_map = parent.path == () and key == "objects" and not parent.is_objects_map
        else:
            # A container nested directly inside a list is ONE item of that
            # list: its path is the list's path plus its item index (an int,
            # which no dict key can equal). Its own strings are items of the
            # nested container, never of the outer list, so they never enter
            # the outer list's `list_item_lines` and shift every later
            # item's line (ADR-0130 clause 2).
            path, is_objects_map = parent.path + (len(parent.container),), False
        stack.append(_Frame(kind, start_line, path, is_objects_map=is_objects_map))

    def close_container(close_line: int) -> _Frame:
        frame = stack.pop()
        if not frame.is_objects_map and len(frame.path) == 1 and stack and stack[-1].is_objects_map:
            object_spans[frame.path[0]] = (frame.start_line, close_line)
        return frame

    def finish_value(value, start_line: int) -> None:
        """Attach a just-closed container's value to whichever frame is now
        on top, and consume the trailing `;` (dict) or `,` (list). A list's
        closing `)` is checked here and consumed when the list closes."""
        parent = stack[-1]
        if parent.kind == "dict":
            key = parent.pending_key
            parent.container[key] = value
            value_lines[parent.path + (key,)] = parent.pending_key_line
            parent.pending_key = None
            parent.pending_key_line = None
            expect(";")
        else:
            parent.container.append(value)
            list_item_lines.setdefault(parent.path, []).append(start_line)
            _comma_or_close()

    def _comma_or_close() -> None:
        t = cur()
        if t[0] == ",":
            advance()
        elif t[0] != ")":
            raise _GrammarError(f"expected ',' or ')' at line {t[2]}, found {t[0]!r}")

    root_kind, _, root_line = cur()
    if root_kind not in ("{", "("):
        raise _GrammarError("document root is not a dict or array")
    advance()
    open_container("dict" if root_kind == "{" else "list", root_line)

    result = None
    while stack:
        frame = stack[-1]
        if frame.kind == "dict":
            if frame.pending_key is None:
                t = cur()
                if t[0] == "}":
                    advance()
                    closed = close_container(t[2])
                    if not stack:
                        result = closed.container
                        break
                    finish_value(closed.container, closed.start_line)
                    continue
                key_tok = expect("STR")
                if key_tok[1] in frame.container:
                    raise _GrammarError(f"duplicate key {key_tok[1]!r} at line {key_tok[2]}")
                frame.pending_key = key_tok[1]
                frame.pending_key_line = key_tok[2]
                expect("=")
                continue
            t = cur()
            if t[0] == "STR":
                advance()
                key = frame.pending_key
                frame.container[key] = t[1]
                value_lines[frame.path + (key,)] = frame.pending_key_line
                frame.pending_key = None
                frame.pending_key_line = None
                expect(";")
                continue
            if t[0] in ("{", "("):
                advance()
                open_container("dict" if t[0] == "{" else "list", t[2])
                continue
            raise _GrammarError(f"unexpected token {t[0]!r} at line {t[2]}")
        else:  # list
            t = cur()
            if t[0] == ")":
                advance()
                closed = close_container(t[2])
                if not stack:
                    result = closed.container
                    break
                finish_value(closed.container, closed.start_line)
                continue
            if t[0] == "STR":
                advance()
                frame.container.append(t[1])
                list_item_lines.setdefault(frame.path, []).append(t[2])
                _comma_or_close()
                continue
            if t[0] in ("{", "("):
                advance()
                open_container("dict" if t[0] == "{" else "list", t[2])
                continue
            raise _GrammarError(f"unexpected token {t[0]!r} at line {t[2]}")

    if cur()[0] != "EOF":
        raise _GrammarError(f"trailing content at line {cur()[2]}")
    return result, object_spans, value_lines, list_item_lines


def _looks_binary_plist(raw: bytes) -> bool:
    return raw.startswith(b"bplist")


def _looks_xml_plist(text: str) -> bool:
    head = text.lstrip()[:64]
    return head.startswith("<?xml") or head.startswith("<!DOCTYPE plist") or head.startswith("<plist")


def read_pbxproj(raw: bytes) -> "PbxprojDocument | PbxprojRefusal":
    """Parse `raw` bytes as a `project.pbxproj`. Total: always returns a
    `PbxprojDocument` or a `PbxprojRefusal`, never raises. See the module
    docstring for the full contract."""
    try:
        if _looks_binary_plist(raw):
            return PbxprojRefusal("project-unreadable", "unsupported-format")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return PbxprojRefusal("project-unreadable", "undecodable")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if _looks_xml_plist(text):
            return PbxprojRefusal("project-unreadable", "unsupported-format")
        first_line = text.split("\n", 1)[0].rstrip()
        if first_line != _UTF8_HEADER:
            return PbxprojRefusal("project-unreadable", "unsupported-format")

        tokens = _tokenize(text)
        value, object_spans, value_lines, list_item_lines = _parse(tokens)

        if not isinstance(value, dict):
            return PbxprojRefusal("project-unreadable", "malformed")
        objects = value.get("objects")
        root_id = value.get("rootObject")
        if not isinstance(objects, dict) or not objects:
            return PbxprojRefusal("project-unreadable", "malformed")
        if not isinstance(root_id, str) or not root_id:
            return PbxprojRefusal("project-unreadable", "malformed")
        for obj in objects.values():
            if not isinstance(obj, dict):
                return PbxprojRefusal("project-unreadable", "malformed")
        # ADR-0130 clause 14 (`malformed` covers a missing `rootObject`): a
        # `rootObject` that names no object, or names one that is not a
        # `PBXProject`, is the same missing-rootObject condition as an
        # absent one.
        if isa_of(objects.get(root_id)) != "PBXProject":
            return PbxprojRefusal("project-unreadable", "malformed")

        object_version = value.get("objectVersion")
        unsupported_version = (
            not isinstance(object_version, str) or object_version not in _SUPPORTED_OBJECT_VERSIONS
        )

        return PbxprojDocument(
            root_id=root_id,
            objects=objects,
            object_spans=object_spans,
            value_lines=value_lines,
            list_item_lines=list_item_lines,
            object_version=object_version if isinstance(object_version, str) else None,
            unsupported_object_version=unsupported_version,
        )
    except _GrammarError as exc:
        return PbxprojRefusal("project-unreadable", exc.kind)
    except (RecursionError, MemoryError):
        return PbxprojRefusal("project-unreadable", "malformed")
    except Exception:
        # The catch-all backstop (module docstring): any other parse-step
        # exception is content, never a capability failure, so it classifies
        # rather than escapes.
        return PbxprojRefusal("project-unreadable", "malformed")
