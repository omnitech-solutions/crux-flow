"""Swift stack pack (ADR-0129/ADR-0130) — the two non-pbxproj Xcode project
inputs: the standalone workspace document and the xcconfig line grammar.

Both readers import only the Python standard library and crux's own
modules (ADR-0130 clause 1): `swift_prune` for the excluded-directory rule,
the core's `_cell`, which escapes a rendered name, and the core's read bound
`_MAX_FILE_BYTES`, from which `MAX_DETAIL_BYTES` is derived. `contained` is
the real-path containment check, the verdict `core._contained` gives in time
linear in the path, that refuses a workspace reference whose resolved path,
following any symlinked component, escapes the checkout root, before that
reference is ever stat'd. Neither reader runs, follows,
evaluates or resolves anything a project file names: a workspace reference is
classified and, when it names a `.xcodeproj` directory or a directory holding
`Package.swift`, recorded as a container this workspace follows (never
opened); an xcconfig `#include`/`#include?` line is reported and never
followed, so its output does not depend on whether the target exists on disk.

The two readers read no file themselves: the caller performs the file read
through the core's safe-read contract (ADR-0130 clause 2) and hands this
module the bytes already read, plus the repo-relative path that read came
from. Nothing in this module reads a file's bytes. `_classify_ref` resolves
and stats a path to classify a reference, and refuses containment before
doing even that. `listed_names` lists directories and `has_symlink_component`
`lstat`s path prefixes. `DirectoryVerdicts` and `CollectionBudget` hold state
that one derive updates.

The reader functions are `read_workspace` and `scan_xcconfig`. Their return
types, `WorkspaceFacts` and `XcconfigFacts`, each carry a tuple of `Residual`
records, which `swift.py` folds into `ProjectFacts` and the module-graph
renderer. `listed_names` and `has_symlink_component` are shared path checks
the other Xcode readers call too, with the `DirectoryVerdicts` memo that
one `swift_xcode.read_projects` call holds. `CollectionBudget`
(`MAX_DETAIL_BYTES`, `MAX_MEMBERSHIPS`, `MAX_WORK_UNITS`) is the
collection-time bound the Swift pack's concerns share; the Xcode readers
charge its work bound through `charge`, and stop at `WorkBoundExceeded`.
This module renders no Markdown; its only text is the fixed residual
details and the three bound lines.
"""

from __future__ import annotations

import os
import re
import stat
import xml.parsers.expat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from ..core import _MAX_FILE_BYTES, _cell
from . import swift_prune

#: The nesting bound: 64 levels are read and a 65th is refused, checked
#: before the descent that would open it (ADR-0130 clauses 2 and 11). A
#: `<Workspace>` element sits at level 1, and the 65th nested element is
#: refused before it is recorded.
_MAX_NESTING = 64


@dataclass(frozen=True)
class Residual:
    """One classified condition read from a workspace or xcconfig file.

    `klass` is a residual class from the closed ADR-0129 clause 7 / ADR-0130
    clause 14 vocabulary — this module never invents one. `path` is the
    declaring file's repo-relative POSIX path. `lines` is the 1-based
    `(start, end)` line range of the evidence, or `None` when no location
    applies (a whole-file refusal). `detail` is a short, closed-vocabulary
    token or a path/key read straight off the checkout — never exception
    text, a host path, or a build-setting value. Rendering this into a
    Markdown bullet is the caller's job, per the closed residual template
    ADR-0130 clause 14 fixes.
    """

    klass: str
    path: str
    lines: tuple[int, int] | None
    detail: str


# ═══════════════════════════ path normalisation ══════════════════════════════
# Shared by both readers (ADR-0130 clause 3): lexical join and `.`/`..`
# resolution ONLY, with no filesystem access — an absolute path, or one that
# leaves the checkout after normalisation, is refused before any path named by
# a project, workspace or xcconfig file ever reaches `open()`.


#: A build-setting reference in any of its three spellings: `$(NAME)`,
#: `${NAME}`, or a bare `$` followed by a letter or underscore (`$SRCROOT`).
#: A literal `$` followed by anything else (`$1.swift`, `Price$.swift`, a
#: trailing `a$`) is not one.
_BUILD_SETTING_REFERENCE = re.compile(r"\$[({A-Za-z_]")

#: What `resolve_project_dir` returns for a `projectDirPath` that holds a
#: build-setting reference or is not a string: the project directory is
#: unresolved, distinct from `None` (an escape). Shared by `swift_xcode` and `swift_xcode_products`.
UNRESOLVED_DIR = "<unresolved project directory>"


def never_resolved(value) -> bool:
    """True when a path a project, workspace or xcconfig file names holds a
    build-setting reference: `$(VAR)`, `${VAR}` or a bare `$VAR`. ADR-0130
    clause 3 never resolves such a segment, and it is classified before any
    lexical join, so a following `..` cannot cancel it. Whether the path
    renders a line depends on its position: a Sources-phase member, a
    target's folder-synced root and a build-file exception set's entry
    render `unresolved-reference`; a workspace location, an xcconfig
    `#include`, a `projectDirPath` and a local package reference render
    nothing. A non-string value is never a reference; each caller classifies
    its type on its own terms.

    Only Xcode-reader path positions call this. A `.package(path:)` in
    `Package.swift` does not: SwiftPM expands nothing there. Nor does any
    xcconfig setting value or `shellScript`, which no reader resolves."""
    return isinstance(value, str) and _BUILD_SETTING_REFERENCE.search(value) is not None


def _normalize_relative(base: str, value: str, budget=None) -> str | None:
    """Lexically join `value` onto the POSIX, repo-relative directory `base`
    (`""` meaning the checkout root) and resolve `.`/`..` segments.

    Returns `None` when `value` is absolute, or when a `..` segment would
    walk past the checkout root — the two `path-escape` conditions ADR-0130
    clause 3 names. A contained `..` (one that stays under the root after
    resolution) is a normal, followed reference (ADR-0130 clause 3).

    With a work budget, the joined path's length is charged (L5) before the
    path string is built: its characters, separators included, since the
    result is a string.
    """
    if PurePosixPath(value).is_absolute():
        return None
    parts: list[str] = [p for p in base.split("/") if p] if base else []
    for segment in PurePosixPath(value).parts:
        if segment in (".", ""):
            continue
        if segment == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(segment)
    if budget is not None:
        charge(budget, sum(len(part) for part in parts) + max(len(parts) - 1, 0))
    return "/".join(parts)


#: `DirectoryVerdicts` node verdicts for one prefix's `lstat`.
_LINK, _DIR, _OTHER = "link", "dir", "other"

#: A kept listing for a verified directory that could not be listed.
_UNLISTABLE = MappingProxyType({})


class _PrefixNode:
    """One prefix of a reference under the checkout root: its `lstat`
    verdict, its directory's listing, and its child prefixes by segment. A
    trie, so a walk costs one dictionary step per segment where a tuple key
    would hash every segment before it. The listing maps each name to the
    `os.DirEntry` one full `scandir` returned, so the parent check of
    `listed_names` and the leaf check of `directory_entries` read one
    listing of the directory."""

    __slots__ = ("mode", "names", "listed", "children")

    def __init__(self):
        self.mode = None
        self.names = None
        self.listed = False
        self.children = {}

    def child(self, seg):
        node = self.children.get(seg)
        if node is None:
            node = self.children[seg] = _PrefixNode()
        return node


class DirectoryVerdicts:
    """The directory memo of ONE `swift_xcode.read_projects` call (ADR-0130
    clauses 2 and 3: a listing is read only after real-path containment,
    and no verdict outlives the call). It holds, per prefix of the
    checkout root, the `lstat` verdict `has_symlink_component` read and the
    names `listed_names` read. `read_projects` builds one, passes it to its
    classic, folder-synced and package-reference checks, and drops it on
    return: no verdict outlives that call, and nothing here is a module
    global. The workspace reader and `swift.py`'s path-dependency checks
    never take one, and read the live filesystem every time.

    Both checks keep what they read only for a reference whose every
    segment `_plain_segment` accepts; a reference holding any other segment
    reads live. A listing is kept only for the root itself or for a prefix
    whose own `lstat` verdict,
    read first by `has_symlink_component`, showed a real directory -- never a
    symlink, so nothing listed through a symlink is ever kept, and no check
    changes order. `lstat` still resolves each prefix from the root once, so
    one reference costs what `PATH_MAX` bounds, and every later reference
    under the same directory costs one step per segment.

    `derived` holds what a reader computes from one directory's contents and
    reuses within the call, so a directory many references name is read
    once (ADR-0129 clause 2: no content makes the derive hang or run out of
    memory): the `.swift` files a
    folder-synced walk found under a base and its `explicitFolders`, and the
    `.lproj` directories a `/Localized/` entry's `<dir>` holds. It is keyed
    by the repo-relative segments, never by an absolute path, and it is
    dropped with the memo. Containment is never kept here: each reference's
    containment check stays live.

    `budget` is the call's `CollectionBudget`, or None. Every path check
    that takes this memo charges its work bound (`MAX_WORK_UNITS`)."""

    __slots__ = ("root", "_top", "derived", "budget")

    def __init__(self, root, budget=None):
        self.root = root
        self._top = _PrefixNode()
        self.derived = {}
        self.budget = budget

    def _usable(self, root, segments):
        return self.root == root and all(_plain_segment(seg) for seg in segments)


def reuse_derived(verdicts, root, segments, key, build, reuse_cost=None):
    """`build()`'s result for the directory `segments` names, computed once
    per `read_projects` call and reused after: E `/Localized/` or directory
    exception entries, or R folder-synced roots, naming one directory read it
    once, not E or R times (ADR-0129 clause 2). `key` names what `build`
    computes from that directory. Without a
    memo, or for a reference holding a segment `_plain_segment` refuses,
    `build` runs every time. A result is reused only within the call: the
    memo is dropped on return.

    A reuse charges the memo's work budget BEFORE the result is handed out,
    through `charge_excluded`: `reuse_cost(result)` units when the caller
    names a cost (L4, a scan's length), else 1 plus the units the first
    `build()` spent outside every listing and reuse charge (L6). A listing
    the first `build()` performed is not performed again, so a reuse never
    charges it again. A `build()` the bound refused part-way keeps
    nothing."""
    segments = tuple(segments)
    if verdicts is None or not verdicts._usable(root, segments):
        return build()
    memo_key = (key, segments)
    found = verdicts.derived.get(memo_key)
    budget = verdicts.budget
    if found is None:
        before = (budget.work_spent - budget.work_excluded) if budget is not None else 0
        result = build()
        units = (budget.work_spent - budget.work_excluded - before) if budget is not None else 0
        verdicts.derived[memo_key] = (result, units)
        return result
    result, units = found
    charge_excluded(budget, reuse_cost(result) if reuse_cost is not None else 1 + units)
    return result


#: The residual detail text one concern's collection composes from names it
#: reads: the same number as the Swift pack's per-concern output bound
#: (`swift._MAX_CONCERN_BYTES`, the core's 2 MiB read bound less 256 KiB).
#: A detail that repeats a name once per list item (a target name per
#: dependency, a synced root per exception entry, every candidate directory
#: per unlinked product) otherwise grows with the product of two inputs,
#: and it is held in memory before the output bound can drop a line
#: (ADR-0129 clause 2; ADR-0130 clause 2). This module cannot import
#: `swift`, so the two constants are written twice, and
#: `test_swift_pack.CollectionBoundConstantTests` pins them equal.
#:
#: The bound charges the UTF-8 bytes of the names a detail repeats, before
#: `_cell` escapes them. Escaping doubles a backslash or a pipe and turns a
#: backtick into a two-byte character, so the detail text held can reach
#: twice the bytes charged.
MAX_DETAIL_BYTES = _MAX_FILE_BYTES - 256 * 1024

#: The Xcode membership records one concern's `read_projects` call keeps:
#: 50,000 bounds the count of (file, target, route) records held in memory,
#: and so the membership rows the concern renders from them. A folder-synced
#: root yields one record per file per owning target, so without a bound the
#: records grow with the product of the two. The largest count in the pinned
#: corpus (NetNewsWire) is about 400, 125 times below the bound. Past the
#: bound later memberships are not read, which moves attribution only
#: (ADR-0129 clause 10).
MAX_MEMBERSHIPS = 50_000

#: The logical work units the Xcode project readers may spend in one
#: concern's derive (ADR-0129 clauses 2 and 7: no content makes the derive
#: exit 2 or hang, and a scan limit renders `scan-cap`):
#: `swift_xcode.read_projects` and all it
#: calls, and `read_workspace`. Never the core walk, the Swift-source walk or
#: `swift_generators`. A unit is counted from the input, never from a
#: syscall, a clock or the absolute root:
#:
#: - L1: a listing the reader performs charges its entry count once, after
#:   the listing completes;
#: - L2: a containment check, a symlink walk or a listed-name walk charges
#:   `l2_units(K)`, 1 + K(K + 1)/2 for a path of K repo-relative segments,
#:   memo hit or miss, before the check runs;
#: - L3: each (reader, item) evaluation charges 1;
#: - L4: a reused `.swift` scan charges its length;
#: - L5: a lexical join charges the length of the path it builds;
#: - L6: any other reader result served from a memo charges 1 plus the
#:   units its first computation spent outside L1, L4 and L6.
#:
#: NetNewsWire spends about 20.8k units per concern (20,829 measured in the
#: module-graph and api-surface concerns); 2^22 leaves about 200 times that
#: (201x). Past the bound later project references are not read, which moves
#: attribution only (ADR-0129 clause 10).
MAX_WORK_UNITS = 4_194_304

#: Past the detail bound only the details that repeat a name are refused;
#: a residual with a fixed detail is still recorded.
DETAIL_BOUND_TEMPLATE = ("the residual details pass the per-concern collection bound of {limit} "
                         "bytes; later residual details that repeat a name are not recorded")
MEMBERSHIP_BOUND_TEMPLATE = ("the Xcode membership records pass the per-concern bound of {limit}; "
                             "later memberships are not read")
WORK_BOUND_TEMPLATE = ("the Xcode project readers pass the per-concern work bound of {limit} units; "
                       "later project references are not read")


class WorkBoundExceeded(Exception):
    """Raised by `charge` when the work bound refuses a unit of work. Each
    Xcode reader catches it at its own boundary, keeps the rows and residuals
    it collected before the refused reference, and stops. It never renders
    as `malformed`: every boundary that catches `Exception` catches this
    first."""


def charge(budget, units):
    """Charge `units` of logical work to `budget` BEFORE the work it pays
    for, and raise `WorkBoundExceeded` when the bound refuses it. Without a
    budget nothing is charged."""
    if budget is not None and not budget.work(units):
        raise WorkBoundExceeded()


def charge_excluded(budget, units):
    """`charge`, for units that a memo reuse never charges again: a listing
    (L1), a reused scan (L4) and a reuse (L6). On success the units are also
    counted in `work_excluded`, so a first computation records only the
    units it spent outside them (L6)."""
    charge(budget, units)
    if budget is not None:
        budget.work_excluded += units


def l2_units(k):
    """The units one path check charges (L2) for a path of `k`
    repo-relative segments: 1 + k(k + 1)/2. The absolute root prefix never
    counts, so the root itself (k = 0) charges 1.

    The kernel resolves a path of k segments from the root: a symlink walk
    or listed-name walk touches k prefixes of 1 to k segments, and
    `os.path.realpath` walks k components, each a string one segment longer.
    So a check costs time quadratic in k, and a linear charge let a deep
    reference repeat that cost under a bound it never reached. At k = 2896
    the charge is 4,194,857, past `MAX_WORK_UNITS`, so no check that deep
    ever runs."""
    return 1 + k * (k + 1) // 2


def contained(root, p) -> bool:
    """`core._contained`'s verdict, reached in time linear in the path: True
    when `p`, fully resolved, is the resolved checkout root or lies under
    it. Both resolve through `os.path.realpath`, as `Path.resolve()` does,
    and the prefix test compares strings. `Path.is_relative_to` builds every
    parent of the resolved path as a `Path` to find the root among them,
    which costs time quadratic in the path's depth, and one containment
    check per reference repeats that cost per reference. The check stays
    live for every reference (ADR-0130 clause 3), and the work bound charges
    it `l2_units` of the path's repo-relative segments. A path that cannot be resolved is not
    contained, as in the core: an unreadable component (`OSError`), a symlink loop before Python
    3.13 (`RuntimeError`), and an embedded NUL byte (`ValueError`)."""
    try:
        resolved = os.path.realpath(p)
        root_resolved = os.path.realpath(root)
    except (OSError, RuntimeError, ValueError):
        return False
    if resolved == root_resolved:
        return True
    prefix = root_resolved if root_resolved.endswith(os.sep) else root_resolved + os.sep
    return resolved.startswith(prefix)


def _budget_of(verdicts, budget):
    """The work budget a path check charges: the one passed, else the one
    the `DirectoryVerdicts` memo carries, else none."""
    if budget is not None:
        return budget
    return verdicts.budget if verdicts is not None else None


class CollectionBudget:
    """The collection-time bounds of ONE concern's derive: the residual
    detail text composed from names (`MAX_DETAIL_BYTES`), the Xcode
    membership records kept (`MAX_MEMBERSHIPS`), and the logical work the
    Xcode project readers spend (`MAX_WORK_UNITS`). The caller builds one per
    extractor call and never stores it, so two derives count alike. Each
    bound trips alone and renders its own `scan-cap` line.

    The detail and membership bounds stop at the first refusal and remember
    where they stopped, `(repo-relative path, raw line span)`, for their
    lines. `detail` is asked BEFORE a detail is composed, with the name parts
    it would repeat, so a refused detail is never built. It charges their
    UTF-8 bytes. `work` is asked before each unit of reader work; its line
    names the checkout root and no range."""

    __slots__ = ("detail_limit", "detail_spent", "detail_stop",
                 "membership_limit", "memberships", "membership_stop",
                 "work_limit", "work_spent", "work_tripped", "work_excluded")

    def __init__(self):
        self.detail_limit = MAX_DETAIL_BYTES
        self.detail_spent = 0
        self.detail_stop = None
        self.membership_limit = MAX_MEMBERSHIPS
        self.memberships = 0
        self.membership_stop = None
        self.work_limit = MAX_WORK_UNITS
        self.work_spent = 0
        self.work_tripped = False
        # The part of `work_spent` charged through `charge_excluded`: the
        # listings (L1), reused scans (L4) and reuses (L6) that a memo
        # reuse never charges again.
        self.work_excluded = 0

    def work(self, units) -> bool:
        """Charge `units` of reader work, and return True when they fit.
        Monotone: once the bound has refused, every later charge refuses."""
        if self.work_tripped:
            return False
        if self.work_spent + units > self.work_limit:
            self.work_tripped = True
            return False
        self.work_spent += units
        return True

    def work_line(self):
        """`(class, path, span, detail)` for a tripped work bound, else None."""
        if not self.work_tripped:
            return None
        return ("scan-cap", ".", None, WORK_BOUND_TEMPLATE.format(limit=self.work_limit))

    def detail(self, path, span, *parts) -> bool:
        if self.detail_stop is not None:
            return False
        # UTF-8 bytes, the unit `DETAIL_BOUND_TEMPLATE` names and the
        # per-concern output bound charges.
        cost = sum(len(part) if part.isascii() else len(part.encode("utf-8", "surrogatepass"))
                   for part in parts)
        if self.detail_spent + cost > self.detail_limit:
            self.detail_stop = (path, span)
            return False
        self.detail_spent += cost
        return True

    def membership(self, path, span) -> bool:
        if self.membership_stop is not None:
            return False
        if self.memberships >= self.membership_limit:
            self.membership_stop = (path, span)
            return False
        self.memberships += 1
        return True

    def membership_full(self) -> bool:
        return self.membership_stop is not None

    def detail_line(self):
        """`(class, path, span, detail)` for a tripped detail bound, else None."""
        if self.detail_stop is None:
            return None
        path, span = self.detail_stop
        return ("scan-cap", path, span, DETAIL_BOUND_TEMPLATE.format(limit=self.detail_limit))

    def membership_line(self):
        """`(class, path, span, detail)` for a tripped membership bound, else None."""
        if self.membership_stop is None:
            return None
        path, span = self.membership_stop
        return ("scan-cap", path, span, MEMBERSHIP_BOUND_TEMPLATE.format(limit=self.membership_limit))


def _span_key(span):
    """A residual's line location as the renderer merges it: a bare line,
    a `(start, end)` span, a list of spans, or `None`, each as the sorted
    tuple of distinct `(start, end)` spans `swift._merge_ranges` yields."""
    if span is None:
        return ()
    if isinstance(span, int):
        return ((span, span),)
    if isinstance(span, tuple):
        return (span,)
    return tuple(sorted(set(tuple(r) for r in span)))


class ResidualSink(list):
    """A residual list that keeps the first of each line the renderer would
    render, and drops the rest as they arrive. Every distinct line still
    renders (ADR-0129 clause 7).

    The key is the full render key: class, path, merged line span and
    detail. `swift._render_residual_block` deduplicates on that same key, so
    the rendered lines are the same by construction. A reader that visits one
    shared object once per referrer -- an exception set listed by R roots, a
    Sources phase listed by T targets -- appends the same line once per
    visit. Without this the list holds a copy per visit, R times N entries,
    before the renderer drops them. Two lines that differ in any part of the
    key are both kept: the key is never an object, field or index.

    One sink belongs to one buffer. A reader's buffer that is discarded, when
    its project renders `malformed`, discards its keys with it."""

    __slots__ = ("_seen",)

    def __init__(self, residuals=()):
        super().__init__()
        self._seen = set()
        self.extend(residuals)

    def append(self, residual):
        klass, path, span, detail = residual
        key = (klass, path, _span_key(span), detail)
        if key in self._seen:
            return
        self._seen.add(key)
        super().append(residual)

    def extend(self, residuals):
        for residual in residuals:
            self.append(residual)

    def __iadd__(self, residuals):
        self.extend(residuals)
        return self

    def insert(self, index, residual):
        raise TypeError("a ResidualSink keeps arrival order; append instead")


def _prefix_string(root, segments, n):
    """The string `has_symlink_component`'s walk hands `lstat` for the first
    `n` segments: `root` joined by `pathlib` with the first, then each
    further one joined with `os.sep`."""
    return str(Path(root) / segments[0]) + "".join(os.sep + seg for seg in segments[1:n])


def listed_names(root: Path, segments, verdicts: "DirectoryVerdicts | None" = None, budget=None) -> bool:
    """True iff every component of `segments` appears BYTE FOR BYTE in its
    parent directory's listing (ADR-0130 clause 3). A path is never
    trusted as written: a case-folding host would open `app.xcodeproj` as
    `App.xcodeproj` where a case-sensitive host finds nothing, so the
    rendered output would depend on the host, and the committed goldens
    could not compare byte for byte across hosts. This checks names
    only; each caller applies its own symlink and file-type rules.

    `verdicts` is the `DirectoryVerdicts` of one `read_projects` call, or
    `None`. With `None` every directory is listed live. With a memo, the
    root and each directory `has_symlink_component` already found real are
    listed once for the call; every other directory is listed live.

    The walk charges the work budget (`budget`, else the memo's)
    `l2_units` of the reference's segments (L2), memo hit or miss, and each
    listing it performs its entry count (L1)."""
    segments = tuple(segments)
    budget = _budget_of(verdicts, budget)
    charge(budget, l2_units(len(segments)))
    if verdicts is None or not verdicts._usable(root, segments):
        return _listed_names_live(root, segments, budget)
    node = verdicts._top
    for i, seg in enumerate(segments):
        if not node.listed:
            names = _listing(root, segments, i, budget)
            if node is verdicts._top or node.mode == _DIR:
                node.names, node.listed = names, True
        else:
            names = node.names
        if seg not in names:
            return False
        node = node.child(seg)
    return True


def _listing(root, segments, n, budget=None):
    """The directory the first `n` segments name, as a mapping from each
    name to its `os.DirEntry`, from one full `scandir`; `_UNLISTABLE` when it
    cannot be listed. A `DirEntry` answers `is_file(follow_symlinks=False)`
    and `is_symlink()` from the listing itself where the host reports the
    entry type, and never follows a symlink. The completed listing charges
    `budget` its entry count (L1) before it is used."""
    path = root if n == 0 else _prefix_string(root, segments, n)
    try:
        with os.scandir(path) as entries:
            names = MappingProxyType({e.name: e for e in entries})
    except OSError:
        return _UNLISTABLE
    charge_excluded(budget, len(names))
    return names


def directory_entries(root: Path, segments, verdicts: "DirectoryVerdicts | None" = None, budget=None):
    """The listing of the directory `segments` names under `root`: a mapping
    from each name to its `os.DirEntry`, or an empty mapping when it cannot
    be listed. The caller has already found the directory real and contained
    (`swift_xcode._real_contained_dir`); this lists, and checks nothing.

    With a `DirectoryVerdicts` memo the listing is kept, on the same terms
    `listed_names` keeps one: only for the checkout root or a prefix whose
    `lstat` verdict showed a real directory. So P members of one directory
    cost one listing for the call, where one `scandir` per member made P
    members cost P listings of P entries (ADR-0129 clause 2). Without
    one, or for a reference holding a segment `_plain_segment` refuses, the
    directory is listed live.

    The walk to the directory's memo node charges the work budget
    `l2_units` of the segments (L2), memo hit or miss, and a listing it
    performs its entry count (L1)."""
    segments = tuple(segments)
    budget = _budget_of(verdicts, budget)
    charge(budget, l2_units(len(segments)))
    if verdicts is None or not verdicts._usable(root, segments):
        return _listing(root, segments, len(segments), budget)
    node = verdicts._top
    for seg in segments:
        node = node.child(seg)
    if node.listed:
        return node.names
    names = _listing(root, segments, len(segments), budget)
    if node is verdicts._top or node.mode == _DIR:
        node.names, node.listed = names, True
    return names


def _listed_names_live(root: Path, segments, budget=None) -> bool:
    """`listed_names` without a memo: each parent directory is listed in
    full, and the completed listing charges `budget` its entry count (L1)
    before its names are compared. A listing is never cut short at the first
    match, so what it charges never depends on the order the host lists in.

    For a reference whose every segment `_plain_segment` accepts, each
    parent is the prefix string `has_symlink_component`'s walk builds, one
    segment longer per step, so the walk's own work is linear in the
    components. Any other reference walks `pathlib` joins, as before."""
    plain = all(_plain_segment(seg) for seg in segments)
    current = root
    for i, seg in enumerate(segments):
        try:
            with os.scandir(current) as entries:
                names = {e.name for e in entries}
        except OSError:
            return False
        charge_excluded(budget, len(names))
        if seg not in names:
            return False
        if plain:
            current = str(Path(root) / seg) if i == 0 else current + os.sep + seg
        else:
            current = current / seg
    return True


def _plain_segment(seg) -> bool:
    """A segment the incremental walk and the memo accept: not empty, `.`
    or `..`, and holding no `/` or NUL. For such a segment the incremental
    walk builds the same prefix strings the path walk builds."""
    return isinstance(seg, str) and seg not in ("", ".", "..") and "/" not in seg and "\0" not in seg


def _path_walk_has_symlink(root: Path, segments) -> bool:
    """The `pathlib` walk: `lstat` of each prefix, each rebuilt as a `Path`
    from every segment before it. `has_symlink_component` uses it only for a
    reference holding a segment `_plain_segment` refuses."""
    current = root
    for seg in segments:
        current = current / seg
        try:
            if current.is_symlink():
                return True
        except OSError:
            return False
    return False


def has_symlink_component(root: Path, segments, verdicts: "DirectoryVerdicts | None" = None,
                          budget=None) -> bool:
    """True iff any component from `root` through `segments`, the final one
    included, is itself a symlink. ADR-0130 clause 3: no directory symlink
    is descended. Each component the walk reaches is read with `lstat`, one
    call per component, so the check never follows a symlink and never
    reads outside the checkout. Every Swift
    reader that lists a directory a project file names calls this first, so
    none of them lists through a symlink.

    Without a memo, the walk extends one prefix string by one segment per
    step and `lstat`s each prefix once, so its own work is linear in the
    components. It hands `lstat` the same strings, in the same order, as a
    `pathlib` walk, and
    stops where that walk's verdict is settled: at a symlink (True), and at
    a component that cannot be read or is not a directory (False), because
    every longer prefix then fails as well. Rebuilding each prefix as a
    `Path` from all its segments made one reference quadratic (ADR-0130
    clause 2). The kernel still resolves each prefix from `root`, which
    `PATH_MAX` bounds.

    No verdict outlives one `read_projects` call. `verdicts` is that call's
    `DirectoryVerdicts`, or `None`. With `None` each call reads the live
    filesystem. With a memo, each prefix is `lstat`ed once for the call,
    with the same string and the same verdict rules; `listed_names` then
    keeps a listing only for a prefix this walk found a real directory. The
    memo walk rebuilds each prefix it has not seen from all its segments
    (`_prefix_string`), so a reference whose prefixes are all new costs
    string work quadratic in its components, which `PATH_MAX` bounds.

    Each walk charges the work budget (`budget`, else the memo's)
    `l2_units` of the reference's segments (L2), memo hit or miss, before it
    starts."""
    segments = tuple(segments)
    charge(_budget_of(verdicts, budget), l2_units(len(segments)))
    if not all(_plain_segment(seg) for seg in segments):
        return _path_walk_has_symlink(root, segments)
    if verdicts is not None and verdicts._usable(root, segments):
        return _memo_walk_has_symlink(root, segments, verdicts)
    current = None
    for seg in segments:
        current = str(Path(root) / seg) if current is None else current + os.sep + seg
        try:
            mode = os.lstat(current).st_mode
        except (OSError, ValueError):
            return False
        if stat.S_ISLNK(mode):
            return True
        if not stat.S_ISDIR(mode):
            return False
    return False


def _memo_walk_has_symlink(root, segments, verdicts) -> bool:
    """`has_symlink_component`'s walk through a memo: a prefix with no
    verdict yet is `lstat`ed with the string the live walk hands `lstat`,
    and its verdict kept. The same three verdicts stop the walk: a symlink
    (True), and a component that cannot be read or is not a directory
    (False)."""
    node = verdicts._top
    for i, seg in enumerate(segments):
        node = node.child(seg)
        if node.mode is None:
            try:
                mode = os.lstat(_prefix_string(root, segments, i + 1)).st_mode
            except (OSError, ValueError):
                node.mode = _OTHER
            else:
                node.mode = _LINK if stat.S_ISLNK(mode) else _DIR if stat.S_ISDIR(mode) else _OTHER
        if node.mode == _LINK:
            return True
        if node.mode != _DIR:
            return False
    return False


# ══════════════════════ the workspace reader (clause 11) ═════════════════════


@dataclass(frozen=True)
class WorkspaceRef:
    """One container this workspace follows: a `.xcodeproj` reference or a
    directory holding `Package.swift`. `kind` is `"xcodeproject"` or
    `"package"`; `path` is the referenced container's repo-relative POSIX
    path, read from the `group:`/`container:` location grammar."""

    kind: str
    path: str


@dataclass(frozen=True)
class WorkspaceFacts:
    """One standalone `*.xcworkspace`'s classified facts. `path` is the
    `contents.xcworkspacedata` file's repo-relative path. `refs` are the
    containers this workspace follows, sorted by `(kind, path)`. `residuals`
    covers every `project-unreadable`, `path-escape`, `unresolved-reference`
    and `missing-input` condition ADR-0130 clause 11 names, sorted."""

    path: str
    refs: tuple[WorkspaceRef, ...]
    residuals: tuple[Residual, ...]


class _WorkspaceRefused(Exception):
    """Internal signal carrying one `project-unreadable` refusal kind."""

    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(kind)


def _parse_workspace_xml(raw: bytes) -> dict:
    """Parse workspace XML into `{"tag", "attrs", "children", "line"}` nodes.

    Every DTD and entity declaration — internal, external, parameter or
    general — is refused before any expansion, via
    `StartDoctypeDeclHandler`, `EntityDeclHandler` and
    `ExternalEntityRefHandler`, all three wired to raise before doing
    anything else; `SetParamEntityParsing(XML_PARAM_ENTITY_PARSING_NEVER)`
    additionally disables parameter-entity parsing outright, so a
    `%pe;`-only document never reaches even the DOCTYPE handler. Element
    nesting past `_MAX_NESTING` is refused mid-parse, before the offending
    element is recorded. Any other document expat rejects, or whose root
    holds anything but exactly one `Workspace` element, is `malformed`.
    """
    parser = xml.parsers.expat.ParserCreate()
    parser.SetParamEntityParsing(xml.parsers.expat.XML_PARAM_ENTITY_PARSING_NEVER)

    def _refuse_entity(*_a, **_k):
        raise _WorkspaceRefused("entity-declaration")

    parser.StartDoctypeDeclHandler = _refuse_entity
    parser.EntityDeclHandler = _refuse_entity
    parser.UnparsedEntityDeclHandler = _refuse_entity
    parser.ExternalEntityRefHandler = _refuse_entity

    root = {"tag": None, "attrs": {}, "children": [], "line": None}
    stack = [root]

    def _start(tag, attrs):
        if len(stack) > _MAX_NESTING:
            raise _WorkspaceRefused("too-deep")
        node = {"tag": tag, "attrs": dict(attrs), "children": [],
                "line": parser.CurrentLineNumber}
        stack[-1]["children"].append(node)
        stack.append(node)

    def _end(_tag):
        stack.pop()

    parser.StartElementHandler = _start
    parser.EndElementHandler = _end

    try:
        parser.Parse(raw, True)
    except _WorkspaceRefused:
        raise
    except xml.parsers.expat.ExpatError as exc:
        raise _WorkspaceRefused("malformed") from exc

    if len(root["children"]) != 1 or root["children"][0]["tag"] != "Workspace":
        raise _WorkspaceRefused("malformed")
    return root["children"][0]


def _resolve_scheme(location: str, group_base: str, workspace_dir: str,
                    budget=None) -> tuple[str, str | None]:
    """`(disposition, rel)` for one `Location` attribute value.

    `disposition` is one of `"ok"`, `"path-escape"`, `"unresolved-reference"`,
    or `"never-resolved"` for a location holding a build-setting reference
    (`never_resolved`), which renders nothing and is never followed (ADR-0130 clause 3).
    `rel` carries the normalised repo-relative path only when `disposition`
    is `"ok"`. `self:` belongs to an embedded workspace, which this reader
    never reads — this reader reads standalone workspaces only, so `self:`
    always renders `unresolved-reference` here (ADR-0130 clause 11).
    """
    if never_resolved(location):
        # ADR-0130 clause 3: classified before any join; renders nothing.
        return "never-resolved", None
    kind, sep, value = location.partition(":")
    if not sep:
        return "unresolved-reference", None
    if kind in ("absolute", "developer"):
        return "path-escape", None
    if kind == "self":
        return "unresolved-reference", None
    if kind == "group":
        base = group_base
    elif kind == "container":
        base = workspace_dir
    else:
        return "unresolved-reference", None
    rel = _normalize_relative(base, value, budget)
    if rel is None:
        return "path-escape", None
    return "ok", rel


#: `_classify_ref`'s four outcomes. `"escape"` means the resolved, real
#: path — following any symlinked component — sits outside the checkout
#: root; `"symlinked"` means the path is contained but a component of it is
#: a symlink, which is never descended (ADR-0130 clause 3); `"missing"`
#: means nothing at all sits at the (contained) path; `"ok"` means the path
#: is safe to stat further, whether or not it turns out to be a followed form.
_REF_ESCAPE = "escape"
_REF_MISSING = "missing"
_REF_SYMLINKED = "symlinked"
_REF_OK = "ok"

#: The closed `unresolved-reference` detail for a contained workspace
#: reference with a symlinked component (ADR-0130 clause 3). It names no
#: path: the symlink's target can lie in a directory ADR-0129 clause 8
#: names nothing inside.
SYMLINKED_WORKSPACE_REFERENCE_DETAIL = ("a workspace file reference names a symlinked path; "
                                        "it is not descended")


def _classify_ref(root: Path, rel: str, budget=None) -> tuple[WorkspaceRef | None, str]:
    """`(ref, status)` for a lexically-contained reference.

    `status` is `_REF_ESCAPE`, `_REF_SYMLINKED`, `_REF_MISSING` or `_REF_OK`.
    Containment is checked FIRST, through `contained`'s real-path
    resolution, before any `exists`/`is_dir`/`is_file` stat: `rel` was
    already lexically normalised by `_normalize_relative` (no `..` past the
    root, never absolute), but a component of it can still be a SYMLINK on
    disk whose target resolves outside the checkout — `group:../Evil.xcodeproj`
    pointing an intermediate or final component at a directory outside root
    is exactly that case, and it must never be stat'd, let alone followed,
    before the containment check runs. `_REF_MISSING` is the "reference
    naming nothing" case — nothing at all sits at `rel`, once containment is
    confirmed. `ref` is `None` whenever the reader does not follow this
    reference (it exists, is contained, but is neither a `.xcodeproj`
    directory nor a directory holding `Package.swift`) — that case renders
    no residual: this reader is silent about a reference it was never asked
    to follow.

    Every check charges `budget`, the work bound (`MAX_WORK_UNITS`): the
    containment check, the
    symlink walk and each listed-name walk `l2_units` of the reference's
    segments (L2), before the check runs, and each directory listing its
    entry count (L1). This reader keeps no memo, so each reference pays for
    its own checks.
    """
    p = root / rel if rel else root
    segments = tuple(rel.split("/")) if rel else ()
    charge(budget, l2_units(len(segments)))
    if not contained(root, p):
        return None, _REF_ESCAPE
    # `Path.exists()` swallows only ENOENT-like errors; a name the host
    # cannot represent (ENAMETOOLONG) or a component it refuses to stat
    # raises. Content must never exit 2 (ADR-0130 clause 2), and nothing is
    # readable at such a path, so it is the "reference naming nothing" case.
    # ADR-0130 clause 3: a contained reference with a symlinked component is
    # neither listed nor followed. The escape check above keeps precedence,
    # so a symlink leaving the checkout still renders `path-escape`.
    if has_symlink_component(root, segments, budget=budget):
        return None, _REF_SYMLINKED
    try:
        if not listed_names(root, segments, budget=budget) or not p.exists():
            return None, _REF_MISSING
        if rel.endswith(".xcodeproj") and p.is_dir():
            return WorkspaceRef(kind="xcodeproject", path=rel), _REF_OK
        # The manifest is matched by name and never followed as a symlink,
        # the same rule the local-package-reference guard applies.
        if (p.is_dir() and listed_names(root, segments + ("Package.swift",), budget=budget)
                and (p / "Package.swift").is_file(follow_symlinks=False)):
            return WorkspaceRef(kind="package", path=rel), _REF_OK
    except OSError:
        return None, _REF_MISSING
    return None, _REF_OK


def _loc_lines(line: int | None) -> tuple[int, int] | None:
    return (line, line) if line else None


def _walk(node: dict, group_base: str, workspace_dir: str, root: Path, contents_rel: str,
          refs: list, residuals: list, budget=None) -> None:
    """Every `Group` and `FileRef` under `node`, in document order. Each is
    one reference, charged 1 before it is read (L3), and each appends its
    ref or residual only after every check it charges has run, so a
    reference the work bound refuses adds nothing."""
    for child in node["children"]:
        tag = child["tag"]
        line = child.get("line")
        if tag in ("Group", "FileRef"):
            charge(budget, 1)
        if tag == "Group":
            location = child["attrs"].get("location", "container:")
            disposition, rel = _resolve_scheme(location, group_base, workspace_dir, budget)
            if disposition == "ok":
                _walk(child, rel, workspace_dir, root, contents_rel, refs, residuals, budget)
            elif disposition == "never-resolved":
                # ADR-0130 clause 3: never resolved, renders nothing, and its
                # descendants stay unresolved, never rebased.
                continue
            else:
                # Closed token only (ADR-0130 clauses 3 and 14): never the
                # location value as written — it can be a host path
                # (`absolute:/Users/...`) that clause 14 forbids on any
                # residual line.
                residuals.append(Residual(disposition, contents_rel, _loc_lines(line),
                                           "workspace group location"))
                # The descendants of an unresolved or refused group stay
                # unresolved (ADR-0130 clause 3): they are simply not walked,
                # never rebased onto another ancestor.
        elif tag == "FileRef":
            location = child["attrs"].get("location", "")
            disposition, rel = _resolve_scheme(location, group_base, workspace_dir, budget)
            if disposition == "never-resolved":
                # ADR-0130 clause 3: never followed, never a container, and
                # renders nothing outside a Sources phase.
                continue
            if disposition != "ok":
                # Closed token only (ADR-0130 clauses 3 and 14) — see the Group branch above
                # for why the location value is never echoed here either.
                residuals.append(Residual(disposition, contents_rel, _loc_lines(line),
                                           "workspace file reference"))
                continue
            if swift_prune.pruned_path(rel.split("/") if rel else (), container=True):
                # ADR-0130 clause 11: a project or package in the excluded set
                # renders nothing, and ADR-0129 clause 8 names nothing inside
                # it. Refused by name, before any filesystem access.
                continue
            ref, status = _classify_ref(root, rel, budget)
            if status == _REF_ESCAPE:
                residuals.append(Residual("path-escape", contents_rel, _loc_lines(line),
                                           "workspace file reference"))
            elif status == _REF_SYMLINKED:
                residuals.append(Residual("unresolved-reference", contents_rel, _loc_lines(line),
                                           SYMLINKED_WORKSPACE_REFERENCE_DETAIL))
            elif status == _REF_MISSING:
                residuals.append(Residual("missing-input", contents_rel, _loc_lines(line),
                                           f"workspace reference '{_cell(rel)}'"))
            elif ref is not None:
                refs.append(ref)
            # Exists but is neither a `.xcodeproj` nor a package directory:
            # not followed, silently (clause 11's "follows only" scope).
        # Any other element tag is outside this reader's grammar and is
        # skipped without comment — this reader recognises Group and FileRef.


def read_workspace(root: Path, workspace_rel: str, raw: bytes, budget=None) -> WorkspaceFacts:
    """Read one standalone `*.xcworkspace`'s `contents.xcworkspacedata`.

    `workspace_rel` is the `.xcworkspace` bundle's repo-relative POSIX
    path, such as `App.xcworkspace` or `ios/App.xcworkspace`. Xcode resolves
    `group:` and `container:` locations against the directory that HOLDS the
    bundle, never inside the bundle, so that directory is the base here.
    `raw` is that file's already safely-read bytes — this function performs
    no read of its own,
    and touches the filesystem only to test whether a resolved, contained
    reference exists and, if so, whether it is a `.xcodeproj` directory or a
    directory holding `Package.swift` (`_classify_ref`). Returns every
    container this workspace follows plus every classified condition
    ADR-0130 clause 11 names.

    `budget` is the calling concern's `CollectionBudget`, or None. The walk
    charges its work bound (`MAX_WORK_UNITS`). When the bound refuses, the
    walk stops: the references read before it keep their refs and residuals, and
    this workspace's later references, like every later project reference,
    are not read. The budget's one `scan-cap` line is rendered by the
    project reader, which runs after this one on the same budget. The parse
    itself is not charged: it is linear in the file, which the core's read
    bound holds to 2 MB.
    """
    contents_rel = f"{workspace_rel}/contents.xcworkspacedata" if workspace_rel else "contents.xcworkspacedata"
    try:
        ws_node = _parse_workspace_xml(raw)
    except _WorkspaceRefused as exc:
        return WorkspaceFacts(
            path=contents_rel, refs=(),
            residuals=(Residual("project-unreadable", contents_rel, None, exc.kind),),
        )
    refs: list[WorkspaceRef] = []
    residuals: list[Residual] = []
    base = str(PurePosixPath(workspace_rel).parent) if workspace_rel else ""
    base = "" if base == "." else base
    try:
        _walk(ws_node, base, base, root, contents_rel, refs, residuals, budget)
    except WorkBoundExceeded:
        pass
    refs.sort(key=lambda r: (r.kind, r.path))
    residuals.sort(key=lambda r: (r.klass, r.lines or (0, 0), r.detail))
    return WorkspaceFacts(path=contents_rel, refs=tuple(refs), residuals=tuple(residuals))


# ═══════════════════════ the xcconfig grammar (clause 12) ═════════════════════

_BLANK_RE = re.compile(r"^\s*$")
_COMMENT_RE = re.compile(r"^\s*//")
_INCLUDE_RE = re.compile(r'^\s*#include(\?)?\s+"([^"]*)"\s*(?://.*)?$')
_SETTING_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)((?:\[[^\]]*\])*)\s*=.*$")

#: Keys that are always conditional (they express a file-name inclusion or
#: exclusion condition), whether or not they carry a bracketed condition
#: (ADR-0130 clause 12).
_ALWAYS_CONDITIONAL_KEYS = frozenset({"EXCLUDED_SOURCE_FILE_NAMES", "INCLUDED_SOURCE_FILE_NAMES"})


@dataclass(frozen=True)
class XcconfigFacts:
    """One xcconfig file's classified facts: `path` is its repo-relative
    POSIX path, `residuals` every `xcconfig-include`, `path-escape`,
    `conditional-setting`, `unsupported-project-form` and
    `project-unreadable` line the total grammar of ADR-0130 clause 12
    produces, sorted by `(lines, klass)`. An ordinary, unconditioned setting
    line renders no residual — it is accepted silently."""

    path: str
    residuals: tuple[Residual, ...]


def scan_xcconfig(rel: str, raw: bytes) -> XcconfigFacts:
    """The total xcconfig line grammar (ADR-0130 clause 12).

    `rel` is the file's repo-relative POSIX path; `raw` is its
    already safely-read bytes (the caller performs the read at the core's
    2 MB bound). This function touches no filesystem: an `#include`/
    `#include?` target is classified purely by lexical normalisation
    against `rel`'s directory, so the residuals it renders are identical
    whether or not the target exists on disk. Every line is one of: blank,
    a `//` comment, `#include`/`#include?`, a setting (with or without
    bracketed conditions and a trailing comment), or `unsupported-project-
    form`. Invalid UTF-8 in the whole file renders one whole-file
    `project-unreadable` residual, `undecodable`, and no per-line scan.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return XcconfigFacts(rel, (Residual("project-unreadable", rel, None, "undecodable"),))

    base_dir = str(PurePosixPath(rel).parent)
    base_dir = "" if base_dir in (".", "") else base_dir

    residuals: list[Residual] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _BLANK_RE.match(line) or _COMMENT_RE.match(line):
            continue
        m = _INCLUDE_RE.match(line)
        if m:
            optional, target = bool(m.group(1)), m.group(2)
            tag = "#include?" if optional else "#include"
            if never_resolved(target):
                # ADR-0130 clause 3: a build-setting reference is never resolved and,
                # outside a Sources phase, renders nothing. Classified before
                # the join, so a following `..` cannot cancel it.
                continue
            included = _normalize_relative(base_dir, target)
            if included is None:
                # Closed token only (ADR-0130 clauses 3 and 14): never the `target` as
                # written — an escaping `#include` can name an arbitrary
                # host path (`../../../SharedXcodeSettings/...`), which
                # clause 14 forbids on any residual line. The successful,
                # CONTAINED `#include` branch below keeps echoing `included`
                # — a path already resolved and verified inside the
                # checkout, not a value as written.
                residuals.append(Residual("path-escape", rel, (lineno, lineno), tag))
            elif swift_prune.pruned_path(included.split("/")[:-1]):
                # ADR-0129 clause 8: nothing inside a pruned directory is
                # named. An include whose directory is in or under one is
                # refused lexically, with a detail that names no path.
                residuals.append(Residual("unresolved-reference", rel, (lineno, lineno),
                                           swift_prune.XCCONFIG_INCLUDE_DETAIL))
            else:
                residuals.append(Residual("xcconfig-include", rel, (lineno, lineno),
                                           f"{tag} '{_cell(included)}'"))
            continue
        m = _SETTING_RE.match(line)
        if m:
            key, conditions = m.group(1), m.group(2)
            if conditions or key in _ALWAYS_CONDITIONAL_KEYS:
                residuals.append(Residual("conditional-setting", rel, (lineno, lineno),
                                           _cell(f"{key}{conditions}")))
            continue
        residuals.append(Residual("unsupported-project-form", rel, (lineno, lineno),
                                   "line outside the xcconfig grammar"))

    residuals.sort(key=lambda r: (r.lines or (0, 0), r.klass))
    return XcconfigFacts(rel, tuple(residuals))
