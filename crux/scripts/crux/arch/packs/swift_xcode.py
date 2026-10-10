"""swift_xcode.py — group descent, path resolution, targets, target
dependencies, and `.swift` membership over a parsed `project.pbxproj`
document (ADR-0130 clauses 2, 3, 5, 6, 8, 9, 10 and 12).

Given a `swift_pbxproj.PbxprojDocument` and the checkout root, this module
resolves group descent and repo-relative paths (clauses 2 and 3). It renders
target rows with their pinned product-type kind (clause 5) and
target-to-target dependency edges (clause 6). It resolves `.swift` file
membership through the folder-synced route with its exception sets (clause
9) and the classic `PBXSourcesBuildPhase` route (clause 8), verified against
a real directory listing (clause 3). Build-setting conditionals are read and
reported as `conditional-setting` lines, never applied (clause 12).

`read_projects` is the entry point `swift.py`'s `_project_facts` calls. It
reads every `*.xcodeproj` bundle a `ProjectReads` census names, parses each
`project.pbxproj`, and merges every container's targets, target
dependencies, package-product dependencies (delegated to
`swift_xcode_products.resolve_product_dependencies`) and memberships into
one result. Targets, target dependencies, memberships and residuals are
sorted; product dependencies keep their resolution order. It returns PLAIN records (dicts and tuples matching
`swift.ProjectFacts`'s own field shapes) rather than importing
`swift.ProjectFacts` itself, keeping the dependency one-way: `swift.py`
imports this module, never the reverse. `swift.py` reads workspaces,
xcconfig files and generator manifests through `swift_xcinputs` and
`swift_generators`, and passes the workspace facts in.

Imports only the standard library and crux's own modules (ADR-0130 clause
1): `crux.arch.core`, `swift_pbxproj`, `swift_prune`, `swift_xcinputs` and
`swift_xcode_products`. Never imports `swift.py` -- `swift.py` is the
consumer, not a dependency of this reader.

Every reader here charges the calling concern's work bound
(`swift_xcinputs.MAX_WORK_UNITS`; ADR-0129 clauses 2 and 7) before each
unit of work, and stops where it refuses: the rows and lines already read
stand, and no later project reference is read.

Every membership row is attributed through a route READ from the project
file, never guessed from a directory or file name (ADR-0130 clause 10). Every
filesystem read used to enumerate a folder-synced root goes through real-path
containment first: no directory symlink is ever descended, and a symlinked
file is never treated as a member.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..core import _cell, _retained_source_filter
from . import swift_pbxproj, swift_prune
from .swift_xcinputs import (UNRESOLVED_DIR, CollectionBudget, DirectoryVerdicts, ResidualSink,
                             WorkBoundExceeded, charge, charge_excluded, contained,
                             directory_entries, has_symlink_component, l2_units, listed_names,
                             never_resolved, reuse_derived)
from .swift_xcode_products import resolve_product_dependencies


def _admit(collection, path, span, *parts):
    """Charge the residual detail about to repeat `parts` to the caller's
    `CollectionBudget`, and return True when it fits. From the first refusal
    on it returns False. Always True without a budget."""
    return collection is None or collection.detail(path, span, *parts)


def _admit_member(collection, path, span):
    """Count one more membership record against the caller's
    `CollectionBudget`, and return True when it fits. From the first refusal
    on it returns False. Always True without a budget."""
    return collection is None or collection.membership(path, span)


def _members_full(collection):
    """True once the caller's `CollectionBudget` has refused a membership
    record. A scan that only feeds membership records is skipped from then
    on. Always False without a budget."""
    return collection is not None and collection.membership_full()


def _work_budget(verdicts):
    """The work budget the `DirectoryVerdicts` memo of one `read_projects`
    call carries, or None."""
    return verdicts.budget if verdicts is not None else None


def _contained_charged(root, rel_segments, budget):
    """`_safe_contained` for the path `rel_segments` names under `root`,
    after charging `budget` `l2_units` of the segments (L2). The absolute
    root prefix is never counted."""
    charge(budget, l2_units(len(rel_segments)))
    return _safe_contained(root, root.joinpath(*rel_segments) if rel_segments else root)

# ── ADR-0130 clause 5: the pinned product-type map ─────────────────────────
_PRODUCT_TYPE_MAP = {}
_PRODUCT_TYPE_MAP["com.apple.product-type.application"] = "app"
_PRODUCT_TYPE_MAP["com.apple.product-type.application.on-demand-install-capable"] = "app"
_PRODUCT_TYPE_MAP["com.apple.product-type.application.watchapp2"] = "app"
_PRODUCT_TYPE_MAP["com.apple.product-type.application.watchapp2-container"] = "app"
_PRODUCT_TYPE_MAP["com.apple.product-type.application.messages"] = "app"
_PRODUCT_TYPE_MAP["com.apple.product-type.app-extension"] = "extension"
_PRODUCT_TYPE_MAP["com.apple.product-type.app-extension.messages"] = "extension"
_PRODUCT_TYPE_MAP["com.apple.product-type.app-extension.messages-sticker-pack"] = "extension"
_PRODUCT_TYPE_MAP["com.apple.product-type.app-extension.intents-service"] = "extension"
_PRODUCT_TYPE_MAP["com.apple.product-type.app-extension.intents-ui"] = "extension"
_PRODUCT_TYPE_MAP["com.apple.product-type.watchkit2-extension"] = "extension"
_PRODUCT_TYPE_MAP["com.apple.product-type.tv-app-extension"] = "extension"
_PRODUCT_TYPE_MAP["com.apple.product-type.extensionkit-extension"] = "extension"
_PRODUCT_TYPE_MAP["com.apple.product-type.bundle.unit-test"] = "test"
_PRODUCT_TYPE_MAP["com.apple.product-type.bundle.ui-testing"] = "test"
_PRODUCT_TYPE_MAP["com.apple.product-type.framework"] = "library"
_PRODUCT_TYPE_MAP["com.apple.product-type.framework.static"] = "library"
_PRODUCT_TYPE_MAP["com.apple.product-type.library.static"] = "library"
_PRODUCT_TYPE_MAP["com.apple.product-type.library.dynamic"] = "library"
_PRODUCT_TYPE_MAP["com.apple.product-type.xcframework"] = "library"
_PRODUCT_TYPE_MAP["com.apple.product-type.tool"] = "executable"

_GROUP_ISAS = frozenset(["PBXGroup", "PBXVariantGroup", "PBXFileSystemSynchronizedRootGroup"])
_SYNCED_ROOT_ISA = "PBXFileSystemSynchronizedRootGroup"
_VARIANT_GROUP_ISA = "PBXVariantGroup"
_BUILD_PHASE_EXCEPTION_SET_ISA = "PBXFileSystemSynchronizedGroupBuildPhaseMembershipExceptionSet"
_BUILD_FILE_EXCEPTION_SET_ISA = "PBXFileSystemSynchronizedBuildFileExceptionSet"
_TARGET_ISAS = frozenset(["PBXNativeTarget", "PBXAggregateTarget", "PBXLegacyTarget"])
BUNDLE_SUFFIXES = frozenset([".xcassets", ".bundle", ".framework", ".xcdatamodeld", ".playground", ".docc", ".xctest", ".appex", ".app", ".lproj"])
_RECOGNISED_SOURCE_TREES = frozenset(["<group>", "<absolute>", "SOURCE_ROOT", "BUILT_PRODUCTS_DIR"])

#: The `unresolved-reference` detail for a `membershipExceptions` entry that
#: names a build-setting reference (`never_resolved`). It names no path and no
#: variable: the entry is classified whole, before any join, so no part of it
#: was resolved (ADR-0130 clauses 3 and 9). `_classify_exception_entry` gives
#: the entries of both exception-set kinds this classification, so a
#: build-phase set's entry renders this line too, and withholds nothing.
NEVER_RESOLVED_EXCEPTION_ENTRY_DETAIL = ("an exception entry names a build-setting reference; "
                                         "it is never resolved and changes no membership")

#: The `path-escape` detail for a `membershipExceptions` entry that leaves the
#: checkout: an absolute entry, one a `..` carries out, or one through a
#: symlinked component that resolves outside (ADR-0130 clause 3). `%s` is the
#: synced root's repo path.
EXCEPTION_ESCAPE_DETAIL = "an exception entry on %s escapes the checkout"

#: The `path-escape` detail for a project file reference that leaves the
#: checkout, shared by `escape_residuals` (a lexical escape) and
#: `classic_memberships` (a symlinked component that resolves outside).
REFERENCE_ESCAPE_DETAIL = "a project file reference escapes the checkout after path normalisation"

#: The set-level `unsupported-project-form` detail of a build-phase membership
#: exception set (ADR-0130 clause 9). The set's own semantics are not
#: interpreted, but each `.swift` entry it names is withheld from its owner's
#: default rows. `%s` is the synced root's repo path.
BUILD_PHASE_SET_DETAIL = ("a build-phase membership exception set on %s is not interpreted; "
                          "the rows it names are withheld")

#: ADR-0130 clauses 2 and 14: the fixed detail each of the eleven
#: list fields renders when it holds a value that is not a list. Shared with
#: `swift_xcode_products` through `swift_pbxproj`.
NON_LIST_FIELD_DETAILS = swift_pbxproj.NON_LIST_FIELD_DETAILS

#: ADR-0130 clause 2, items: the `unresolved-reference` detail for an item of
#: `targets` or `buildPhases` that is not an object id -- not a string, or a
#: string naming no object. An item naming an object of another `isa` renders
#: nothing. `children`, `files`, `exceptions`, `dependencies`,
#: `fileSystemSynchronizedGroups` and `packageProductDependencies` reuse the
#: detail their dangling id already renders.
NON_STRING_TARGET_ITEM_DETAIL = "a project's targets entry is not an object id and is not read"
NON_STRING_BUILD_PHASE_ITEM_DETAIL = "a target's buildPhases entry is not an object id and is not read"

#: ADR-0130 clause 14, list items: the `unsupported-project-form` detail for
#: an item of a path list that is not a string. It is not applied.
NON_STRING_EXCEPTION_ENTRY_DETAIL = "an exception entry is not a string; it is not applied"
NON_STRING_EXPLICIT_FOLDER_DETAIL = "an explicitFolders entry is not a string; it is not applied"

#: ADR-0130 clause 2 (a dangling id renders `unresolved-reference`): the
#: `unresolved-reference` detail for a
#: `mainGroup` that names no group object -- absent, `""`, not a string,
#: dangling, or naming an object of another kind -- while the project
#: directory resolved. Nothing is descended.
MAIN_GROUP_DETAIL = "the project's mainGroup names no group object; no group is descended"

#: ADR-0130 clause 14: the fixed details a dictionary field or a
#: display string field renders when it holds another type. `objectVersion`'s
#: detail also renders when the field is absent. `swift_xcode_products` shares
#: the string details through `swift_pbxproj.string_field`.
NON_DICT_FIELD_DETAILS = swift_pbxproj.NON_DICT_FIELD_DETAILS
NON_STRING_FIELD_DETAILS = swift_pbxproj.NON_STRING_FIELD_DETAILS

_id_field = swift_pbxproj.id_field
_isa_of = swift_pbxproj.isa_of


@dataclass(frozen=True)
class Descent:
    """The result of one descent from mainGroup (ADR-0130 clause 2)."""
    group_dirs: dict = field(default_factory=dict)
    file_segments: dict = field(default_factory=dict)
    variant_children: dict = field(default_factory=dict)
    synced_roots: frozenset = frozenset()
    none_ids: frozenset = frozenset()
    unresolved_var_ids: frozenset = frozenset()
    escape_ids: frozenset = frozenset()
    unresolved_ids: frozenset = frozenset()
    unresolved_entries: tuple = ()
    non_list_children: tuple = ()
    main_group_missing: bool = False


def _lexical_join(base, path_field, budget=None):
    """`path_field` joined lexically onto the segments `base`, with `.` and
    `..` resolved, or None when a `..` leaves the checkout.

    With a work budget, the joined path's length is charged (L5) before its
    tuple is built: the characters of the POSIX path it names, separators
    included. That count is at least the tuple's slots, so held path tuples
    grow only as far as the bound admits. It also counts what a segment
    holds, so a path that repeats one very long segment is charged for it
    each time it is joined, and every string later built from the tuple is
    bounded by what was charged."""
    segs = list(base)
    for part in path_field.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not segs:
                return None
            segs.pop()
        else:
            segs.append(part)
    if budget is not None:
        charge(budget, sum(map(len, segs)) + max(len(segs) - 1, 0))
    return tuple(segs)


def resolve_project_dir(pbx_project, project_repo_dir, budget=None):
    """The project directory as repo-relative segments: the bundle's own
    directory joined with a contained `projectDirPath`, or `None` when
    `projectDirPath` is absolute or leaves the checkout (ADR-0130 clause 3).
    A `projectDirPath` holding a build-setting reference (`never_resolved`)
    returns `UNRESOLVED_DIR` before any join, so a following `..` cannot
    cancel the segment. So does a `projectDirPath` that is not a string (a
    list or a dictionary, empty or not): it names no directory and is never
    resolved (ADR-0130 clause 3). Its type decides:
    `()` and `{}` are not read as absent."""
    pdp = pbx_project.get("projectDirPath") if pbx_project else None
    if pdp is not None and not isinstance(pdp, str):
        return UNRESOLVED_DIR
    if not pdp:
        return project_repo_dir
    if never_resolved(pdp):
        return UNRESOLVED_DIR
    if pdp.startswith("/"):
        return None
    return _lexical_join(project_repo_dir, pdp, budget)


def _resolve_ref(obj, group_parent, project_dir, budget=None):
    source_tree = obj.get("sourceTree")
    path_field = obj.get("path")
    # The OpenStep grammar admits a list or a dictionary wherever a string
    # can sit. A `path` or `sourceTree` holding one names no location, so the
    # reference is never resolved, the way ADR-0130 clause 3 treats a custom
    # source tree. Raising here instead would collapse the whole project to
    # one `malformed` line and drop every target row (clauses 2 and 5).
    if not isinstance(path_field, (str, type(None))) or not isinstance(source_tree, (str, type(None))):
        return ("unresolved-var", None)
    if source_tree == "BUILT_PRODUCTS_DIR":
        return ("none", None)
    if never_resolved(path_field):
        return ("unresolved-var", None)
    if source_tree == "<absolute>":
        return ("escape", None)
    if path_field and path_field.startswith("/"):
        return ("escape", None)
    if source_tree not in _RECOGNISED_SOURCE_TREES and source_tree is not None:
        return ("unresolved-var", None)
    if source_tree == "SOURCE_ROOT":
        if project_dir is None:
            return ("escape", None)
        if not path_field:
            return ("path", project_dir)
        joined = _lexical_join(project_dir, path_field, budget)
        return ("escape", None) if joined is None else ("path", joined)
    if not path_field:
        return ("path", group_parent)
    joined = _lexical_join(group_parent, path_field, budget)
    return ("escape", None) if joined is None else ("path", joined)


def descend(doc, project_repo_dir, collection=None):
    """ADR-0130 clause 2/3: group descent follows `children` from
    `mainGroup` and visits each object at most once. Walked ITERATIVELY (an
    explicit stack, never Python recursion) so a pathologically deep group
    chain -- thousands of nested groups -- can never raise `RecursionError`;
    there is no per-file content that makes the deriver exit 2 (clause 2,
    last bullet). The stack preserves the same pre-order, depth-first
    visitation a recursive walk would produce: a group's children are pushed
    in REVERSED order so the first child pops (and is fully exhausted)
    before the second.

    The main group resolves against `resolve_project_dir(project_obj,
    project_repo_dir)` -- the `.xcodeproj` bundle's OWN directory (never the
    checkout root) joined with a contained `projectDirPath` (ADR-0130
    clause 3): Xcode resolves the main group, and every
    `<group>`-tree default-path reference under it, against the project
    directory, not the checkout root. A project nested at `ios/App.xcodeproj`
    must render `ios/Shared/Foo.swift`, never a root-level decoy at
    `Shared/Foo.swift`. When `projectDirPath` escapes the checkout,
    `resolve_project_dir` returns `None`; the project's own object id is
    recorded in `escape_ids` (so `escape_residuals` renders one `path-escape`
    naming the project file itself) and NOTHING is descended -- guessing the
    checkout root as a substitute base is exactly the guess clause 10
    forbids. When `projectDirPath` holds a build-setting reference
    (`never_resolved`) or is not a string, `resolve_project_dir` returns
    `UNRESOLVED_DIR`: nothing is descended and
    nothing is recorded, so the field renders nothing and each Sources-phase
    entry falls through to `classic_memberships`' own `unresolved-reference`.

    Every encounter that lands "unresolved" -- a cycle, an object reached a
    second time, or a dangling id -- is recorded in `unresolved_entries` as
    `(referrer_id, field_name, child_id)`, the referencing list-item that
    caused it (`descent_unresolved_residuals` renders one
    `unresolved-reference` bullet per entry from this list). A child item
    that is not a string names no object and is recorded the same way,
    never used as a key (ADR-0130 clauses 2 and 14).

    A resolved group whose `children` is present but not a list is not
    descended: its id is recorded in `non_list_children`, and
    `descent_unresolved_residuals` renders its one `unsupported-project-form`
    line. A string's characters are never child ids.

    ADR-0130 clause 2 (a dangling id renders `unresolved-reference`): when
    the project directory resolved, a
    `mainGroup` that names no group object -- absent, `""`, not a string,
    dangling, or naming an object whose `isa` is not a group's -- is not
    descended and sets `main_group_missing`, which
    `descent_unresolved_residuals` renders as one line. Nothing is
    descended, so every Sources entry and synced lookup renders its own
    existing unresolved line.

    `collection` is the calling concern's `CollectionBudget`, or None. The
    walk charges its work bound (`MAX_WORK_UNITS`): each group's children,
    one unit per (group, child) before they are pushed (L3), and each path
    joined, its length (L5). A refusal raises `WorkBoundExceeded` out of this
    function and no partial descent is returned."""
    objects = doc.objects
    project_obj = objects.get(doc.root_id, {})
    project_dir = resolve_project_dir(project_obj, project_repo_dir, collection)
    main_group_id = _id_field(doc, doc.root_id, "mainGroup")
    main_group_missing = False

    group_dirs = {}
    file_segments = {}
    variant_children = {}
    synced_roots = set()
    none_ids = set()
    unresolved_var_ids = set()
    escape_ids = set()
    unresolved_ids = set()
    unresolved_entries = []
    non_list_children = []
    visited = set()

    stack = []
    if project_dir is UNRESOLVED_DIR:
        pass
    elif project_dir is None:
        escape_ids.add(doc.root_id)
    elif _isa_of(objects.get(main_group_id)) in _GROUP_ISAS:
        stack.append((main_group_id, project_dir, None, None))
    else:
        main_group_missing = True

    while stack:
        obj_id, parent, referrer_id, field_name = stack.pop()
        if obj_id in visited:
            unresolved_ids.add(obj_id)
            if referrer_id is not None:
                unresolved_entries.append((referrer_id, field_name, obj_id))
            continue
        visited.add(obj_id)
        obj = objects.get(obj_id)
        if obj is None:
            unresolved_ids.add(obj_id)
            if referrer_id is not None:
                unresolved_entries.append((referrer_id, field_name, obj_id))
            continue
        isa = _isa_of(obj)
        if isa in _GROUP_ISAS:
            kind, segs = _resolve_ref(obj, parent, project_dir, collection)
            if kind == "path":
                group_dirs[obj_id] = segs
                if isa == _SYNCED_ROOT_ISA:
                    synced_roots.add(obj_id)
                    continue
                children = obj.get("children")
                if children is None:
                    children = []
                elif not isinstance(children, list):
                    non_list_children.append(obj_id)
                    children = []
                if isa == _VARIANT_GROUP_ISA:
                    variant_children[obj_id] = [c for c in children if isinstance(c, str)]
                charge(collection, len(children))
                for child_id in reversed(children):
                    if not isinstance(child_id, str):
                        unresolved_entries.append((obj_id, "children", child_id))
                        continue
                    stack.append((child_id, segs, obj_id, "children"))
            elif kind == "escape":
                escape_ids.add(obj_id)
            elif kind == "none":
                none_ids.add(obj_id)
            else:
                unresolved_var_ids.add(obj_id)
        elif isa == "PBXFileReference":
            kind, segs = _resolve_ref(obj, parent, project_dir, collection)
            if kind == "path":
                file_segments[obj_id] = segs
            elif kind == "escape":
                escape_ids.add(obj_id)
            elif kind == "none":
                none_ids.add(obj_id)
            else:
                unresolved_var_ids.add(obj_id)

    return Descent(
        group_dirs=group_dirs, file_segments=file_segments,
        variant_children=variant_children, synced_roots=frozenset(synced_roots),
        none_ids=frozenset(none_ids), unresolved_var_ids=frozenset(unresolved_var_ids),
        escape_ids=frozenset(escape_ids), unresolved_ids=frozenset(unresolved_ids),
        unresolved_entries=tuple(unresolved_entries),
        non_list_children=tuple(non_list_children),
        main_group_missing=main_group_missing,
    )


def descent_unresolved_residuals(doc, descent, pbxproj_path):
    """ADR-0130 clause 2: one `unresolved-reference` residual per entry
    in `descent.unresolved_entries` -- a cycle, a second reach, or a
    dangling child -- at the referencing list-item line, whether or not a
    Sources build phase also names the same object id. Rendered
    unconditionally here so a diamond or a cycle with NO build file
    pointing at the shared/cyclic object still renders. This function is
    that line's only source: `classic_memberships` skips a candidate id
    already in `descent.unresolved_ids`, so a build file naming the same id
    never doubles the count.

    It also renders the one `unsupported-project-form` line of each group in
    `descent.non_list_children` (ADR-0130 clause 14), and the one
    `unresolved-reference` of a `mainGroup` that names no group object, at
    the field's own line or the project object's first line (ADR-0130
    clause 2)."""
    residuals = []
    if descent.main_group_missing:
        residuals.append(("unresolved-reference", pbxproj_path,
                          swift_pbxproj.field_line(doc, doc.root_id, "mainGroup"), MAIN_GROUP_DETAIL))
    for obj_id in descent.non_list_children:
        residuals.append(swift_pbxproj.non_list_residual(doc, obj_id, "children", pbxproj_path))
    for referrer_id, field_name, child_id in descent.unresolved_entries:
        loc = _list_item_line(doc, referrer_id, field_name, child_id)
        detail = ("a %s entry references an object reached a second time, "
                   "or not at all, by descent") % (_cell(field_name),)
        residuals.append(("unresolved-reference", pbxproj_path, loc, detail))
    return tuple(residuals)


def segs_to_posix(segs):
    """Repo-relative segments as a POSIX path, `.` for the checkout root."""
    return "/".join(segs) if segs else "."


def _join_charged(base, rel, budget):
    """`rel` joined onto the POSIX path `base` (`.` for the checkout root),
    after charging `budget` the joined path's length in characters (L5): a
    reader that builds one path per listed file or per key pays for what it
    builds and holds."""
    if base == ".":
        charge(budget, len(rel))
        return rel
    charge(budget, len(base) + 1 + len(rel))
    return base + "/" + rel


def _map_product_type(product_type):
    # `product_type` is `swift_pbxproj.string_field`'s typed read: a value
    # that is not a string arrives as `None` and names no product type.
    if not product_type:
        return "unmapped", "—"
    final_segment = product_type.rsplit(".", 1)[-1]
    mapped = _PRODUCT_TYPE_MAP.get(product_type)
    if mapped is None:
        return "unmapped", final_segment
    return mapped, final_segment


def target_rows(doc, pbxproj_path, container_name, collection=None):
    """Every native, aggregate and legacy target as a row with its pinned
    kind and product type (ADR-0130 clause 5). Returns `(rows, rows by
    target id, residuals)`; an unmapped product type adds one
    `unsupported-project-form` residual. A `targets` value that is not a list
    renders its one line and names no target, and an item that is not a
    string renders one `unresolved-reference` (ADR-0130 clauses 2 and 14).

    ADR-0130 clause 14: a `name` that is not a string renders `—` and
    one `unsupported-project-form` at its own line, once per target however
    often `targets` lists it. A `productType` that is not a string names no
    product type: kind `unmapped`, product type `—`, and the existing
    unmapped line. A non-string `isa` is an unrecognised `isa`.

    ADR-0130 clause 2: a string item that names no object is a dangling id.
    It renders the same line as an item that is not a string, at its item
    line. An item naming an object of another `isa` renders nothing.

    ADR-0130 clause 10: a target's identity is its container and its object
    id, never its name. A target listed twice has one row. Each row carries
    its object id as `target_id`, and every membership row, dependency edge
    and dedup key downstream is keyed by it. A target displays its name, or
    `—` when it has none. When two or more targets in this container share
    that display, each renders `<display> (object <id>)` instead, so no two
    of them merge into one row or one graph node. The id is part of the
    display, so it is escaped and charged wherever the name is.

    The unmapped product-type detail repeats the display, so it is charged
    to `collection` (`CollectionBudget.detail`) before it is built; past the
    bound the row stands and the line is not recorded
    (`swift_xcinputs.MAX_DETAIL_BYTES`)."""
    objects = doc.objects
    targets = []
    targets_by_id = {}
    # A `targets` list naming one dangling id N times appends one line.
    residuals = ResidualSink()
    seen_fields = set()
    read = []
    for tid in swift_pbxproj.list_field(doc, doc.root_id, "targets", pbxproj_path, residuals):
        obj = objects.get(tid) if isinstance(tid, str) else None
        if obj is None:
            residuals.append(("unresolved-reference", pbxproj_path,
                              _list_item_line(doc, doc.root_id, "targets", tid),
                              NON_STRING_TARGET_ITEM_DETAIL))
            continue
        if _isa_of(obj) not in _TARGET_ISAS or tid in targets_by_id:
            continue
        name = swift_pbxproj.string_field(doc, tid, "name", pbxproj_path, residuals, seen_fields) or "—"
        targets_by_id[tid] = None
        read.append((tid, name))
    sharing = {}
    for _tid, name in read:
        sharing[name] = sharing.get(name, 0) + 1
    for tid, name in read:
        display = name if sharing[name] == 1 else "%s (object %s)" % (name, tid)
        kind, product_type_cell = _map_product_type(
            swift_pbxproj.string_field(doc, tid, "productType", pbxproj_path, None))
        span = doc.object_spans.get(tid)
        row = {}
        row["name"] = display
        row["target_id"] = tid
        row["container"] = container_name
        row["kind"] = kind
        row["product_type"] = product_type_cell
        row["path"] = pbxproj_path
        row["loc"] = span
        row["conditional"] = False
        targets.append(row)
        targets_by_id[tid] = row
        if kind == "unmapped" and _admit(collection, pbxproj_path, span, display):
            detail = "target '%s' names an unmapped product type" % (_cell(display),)
            residuals.append(("unsupported-project-form", pbxproj_path, span, detail))
    return tuple(targets), targets_by_id, tuple(residuals)


def target_dependencies(doc, container_name, targets_by_id, pbxproj_path, other_containers=None,
                        collection=None):
    """ADR-0130 clause 6. No id this function uses as a dict key or in a
    comparison can raise, so no project content exits 2 (ADR-0130 clause 2).
    `dep_id` is checked `isinstance(..., str)` before any lookup. `target`,
    `targetProxy`, `containerPortal` and `remoteGlobalIDString` are read
    through `swift_pbxproj.id_field`, which returns `NOT_AN_ID` for a value
    that is not a string. `NOT_AN_ID` is hashable, equals only itself and
    names no object, so a `target` or `remoteGlobalIDString` holding it
    flows as an id value and renders exactly what a dangling string renders
    at the same point: "names a dangling target" when nothing contradicts it,
    and "does not resolve" when the proxy resolves elsewhere. A
    `targetProxy` or `containerPortal` is checked `isinstance(..., str)`
    before it keys a mapping. A type-confused pbxproj value (a list or dict
    in place of an object id) renders one `unresolved-reference` for that
    dependency and never raises `TypeError`/`AttributeError` out of this
    function. A `dependencies` value that is not a list renders its one line
    and is not read (ADR-0130 clause 2).

    An edge names both targets by display and carries both object ids
    (`from_id`, `to_id`): a target's identity is its container and its id
    (ADR-0130 clause 10), so an edge between two nameless targets is drawn.

    Every residual detail that repeats the target's name is charged to
    `collection` before it is built (`CollectionBudget.detail`).

    Each (target, dependency) evaluation, and each other container a
    cross-project dependency's search visits, charges the work bound 1 unit
    first (L3 of `MAX_WORK_UNITS`). When it refuses, the edges and lines of the
    dependencies read before stand, the refused dependency adds none, and
    no later dependency is read."""
    objects = doc.objects
    edges = []
    residuals = ResidualSink()
    try:
        for tid, row in targets_by_id.items():
            dep_ids = swift_pbxproj.list_field(doc, tid, "dependencies", pbxproj_path, residuals)
            for dep_id in dep_ids:
                charge(collection, 1)
                if not isinstance(dep_id, str):
                    if _admit(collection, pbxproj_path, None, row["name"]):
                        detail = "dangling dependency reference from '%s'" % (_cell(row["name"]),)
                        residuals.append(("unresolved-reference", pbxproj_path, None, detail))
                    continue
                dep_obj = objects.get(dep_id)
                dep_span = doc.object_spans.get(dep_id)
                if dep_obj is None or _isa_of(dep_obj) != "PBXTargetDependency":
                    if _admit(collection, pbxproj_path, dep_span, row["name"]):
                        detail = "dangling dependency reference from '%s'" % (_cell(row["name"]),)
                        residuals.append(("unresolved-reference", pbxproj_path, dep_span, detail))
                    continue
                # `target` and `remoteGlobalIDString` may be `NOT_AN_ID`. It
                # flows as an id value: it misses every lookup, so each branch
                # below renders what a dangling string renders there.
                direct_target = _id_field(doc, dep_id, "target")
                proxy_id = _id_field(doc, dep_id, "targetProxy")

                resolved_target_id = None
                resolved_container = container_name
                failed = False
                if proxy_id is not None:
                    # ADR-0130 clause 6: a present `targetProxy` that is not a
                    # string, names no object, or names an object that is not a
                    # `PBXContainerItemProxy` is a dangling id. It cannot
                    # corroborate `target`, so the dependency does not resolve.
                    # An absent `targetProxy` is not dangling: `target` alone
                    # decides.
                    proxy_obj = objects.get(proxy_id) if isinstance(proxy_id, str) else None
                    if _isa_of(proxy_obj) != "PBXContainerItemProxy":
                        failed = True
                    else:
                        container_portal = _id_field(doc, proxy_id, "containerPortal")
                        remote_gid = _id_field(doc, proxy_id, "remoteGlobalIDString")
                        if remote_gid is None:
                            failed = True
                        elif container_portal == doc.root_id or container_portal is None:
                            resolved_target_id = remote_gid
                            resolved_container = container_name
                        else:
                            other = (other_containers or {}).get(container_portal) \
                                if isinstance(container_portal, str) else None
                            if other is None:
                                failed = True
                            else:
                                other_obj = other["objects"].get(remote_gid)
                                if other_obj is not None and _isa_of(other_obj) == "PBXNativeTarget":
                                    resolved_target_id = remote_gid
                                    resolved_container = other["container_name"]
                                else:
                                    failed = True
                        if not failed and direct_target is not None and resolved_target_id != direct_target:
                            failed = True
                elif direct_target is not None:
                    resolved_target_id = direct_target
                    resolved_container = container_name
                else:
                    failed = True

                if failed or resolved_target_id is None:
                    if _admit(collection, pbxproj_path, dep_span, row["name"]):
                        detail = "dependency of '%s' does not resolve" % (_cell(row["name"]),)
                        residuals.append(("unresolved-reference", pbxproj_path, dep_span, detail))
                    continue

                if resolved_container == container_name:
                    target_row = targets_by_id.get(resolved_target_id)
                else:
                    other_for_name = None
                    for v in (other_containers or {}).values():
                        charge(collection, 1)
                        if v["container_name"] == resolved_container:
                            other_for_name = v
                            break
                    target_row = None
                    if other_for_name is not None:
                        target_row = other_for_name["targets_by_id"].get(resolved_target_id)
                if target_row is None:
                    if _admit(collection, pbxproj_path, dep_span, row["name"]):
                        detail = "dependency of '%s' names a dangling target" % (_cell(row["name"]),)
                        residuals.append(("unresolved-reference", pbxproj_path, dep_span, detail))
                    continue
                edge = {}
                edge["from"] = row["name"]
                edge["to"] = target_row["name"]
                edge["from_id"] = tid
                edge["to_id"] = resolved_target_id
                # `container` is the declaring project, `to_container` the one
                # holding the target; they differ only for a cross-project proxy.
                edge["container"] = container_name
                edge["to_container"] = resolved_container
                edges.append(edge)
    except WorkBoundExceeded:
        pass
    return tuple(edges), tuple(residuals)


def escape_residuals(doc, descent, pbxproj_path):
    """ADR-0130 clause 3: every reference in
    `descent.escape_ids` -- an `<absolute>` sourceTree, an absolute path, or
    a path that leaves the checkout after lexical normalisation -- renders
    one `path-escape` residual naming the declaring file's repo-relative
    path and the reference's own line range. The detail is closed and never
    carries the path as written or its target.

    `descent.none_ids` (`BUILT_PRODUCTS_DIR`) renders nothing here, per
    clause 3 -- a product is never a member. `descent.unresolved_var_ids`
    (`SDKROOT`, `DEVELOPER_DIR`, a custom source tree, a build-setting
    reference, a `path` or `sourceTree` that is not a string) also renders
    nothing here. Such an id renders `unresolved-reference` only when a
    Sources build phase names it (`classic_memberships`) or a target's
    `fileSystemSynchronizedGroups` names it (`_unresolved_synced_lookups`).
    An id in `descent.unresolved_ids` renders through
    `descent_unresolved_residuals`."""
    residuals = []
    for obj_id in sorted(descent.escape_ids):
        span = doc.object_spans.get(obj_id)
        residuals.append(("path-escape", pbxproj_path, span, REFERENCE_ESCAPE_DETAIL))
    return tuple(residuals)


#: ADR-0130 clause 12: the two build-setting keys that gate
#: membership without ever changing it -- read and reported, never applied.
_CONDITIONAL_EXCLUDE_KEYS = frozenset(["EXCLUDED_SOURCE_FILE_NAMES", "INCLUDED_SOURCE_FILE_NAMES"])


def build_setting_residuals(doc, pbxproj_path):
    """ADR-0130 clause 12: the `buildSettings` dictionary of every
    `XCBuildConfiguration` object in the document is read; nothing is merged,
    evaluated or applied, and no setting VALUE ever renders. Each
    conditioned key (`KEY[sdk=...]`, `KEY[arch=...]`, `KEY[config=...]`, any
    bracketed condition) renders one `conditional-setting` residual at its
    own pbxproj value line, naming the key (which already carries its own
    condition). So does each `EXCLUDED_SOURCE_FILE_NAMES` or
    `INCLUDED_SOURCE_FILE_NAMES` key, bracketed or not -- it never changes a
    membership row; `classic_memberships` and `synced_memberships` read
    membership only through their own routes (Sources phases and synced
    roots/exception sets) and never consult `buildSettings`.

    `baseConfigurationReference` is never followed here or anywhere else
    in this module: this function inspects only the `buildSettings`
    mapping's own keys. The referenced file is read only as an ordinary
    `.xcconfig` input of the module-graph walk.

    A `buildSettings` that is not a dictionary renders one
    `unsupported-project-form` and is not read: a string's characters and a
    list's items are never setting names (ADR-0130 clause 14)."""
    residuals = []
    for obj_id, obj in doc.objects.items():
        if _isa_of(obj) != "XCBuildConfiguration":
            continue
        settings = swift_pbxproj.dict_field(doc, obj_id, "buildSettings", pbxproj_path, residuals)
        for key in settings:
            base_key = key.split("[", 1)[0]
            conditioned = "[" in key
            if not conditioned and base_key not in _CONDITIONAL_EXCLUDE_KEYS:
                continue
            loc = doc.value_lines.get((obj_id, "buildSettings", key))
            detail = "build setting '%s' is conditioned and never applied" % (_cell(key),)
            residuals.append(("conditional-setting", pbxproj_path, loc, detail))
    return tuple(residuals)


#: ADR-0130 clause 3's symlink-component check, shared with the workspace
#: reader and the local-package-reference resolver in `swift_xcinputs`.
_has_symlink_component = has_symlink_component


def _safe_contained(root, p):
    """The real-path containment check (`swift_xcinputs.contained`, the
    verdict `core._contained` gives, in time linear in the path), never
    raising, so no project content exits 2 (ADR-0130 clause 2): a path
    segment carrying an embedded NUL byte (e.g. a NUL inside a
    `relativePath` or a group `path` field) makes resolution raise
    `ValueError`, and it is treated as not-contained, the same fail-closed
    verdict a real escape gets: a value that cannot even be resolved is
    never guessed into containment."""
    try:
        return contained(root, p)
    except ValueError:
        return False


def _synced_root_symlink_verdict(root, rel_segments, verdicts=None):
    """ADR-0130 clause 3: a directory is
    enumerated only after real-path containment, and no directory symlink
    is descended. Returns `"escape"` when the root (or a symlinked
    component of it) resolves outside the checkout, `"symlinked"` when it
    resolves inside the checkout but is (or sits under) a symlink and so is
    never descended, or `None` when no component is a symlink at all. Both
    checks charge the memo's work budget (L2)."""
    if not rel_segments or not _has_symlink_component(root, rel_segments, verdicts):
        return None
    return "escape" if not _contained_charged(root, rel_segments, _work_budget(verdicts)) else "symlinked"


def _real_contained_dir(root, rel_segments, verdicts=None):
    """ADR-0130 clause 3: `(verdict, directory)` for the directory at
    `rel_segments`. The verdict is `"dir"` with the real directory, or one of
    `"escape"`, `"symlinked"` and `"missing"` with `None`.

    A path with a symlinked component gets `_synced_root_symlink_verdict`'s
    answer, from one memoised symlink-component check and then one
    `_safe_contained`: `"escape"` when it resolves outside the checkout,
    which the caller renders as `path-escape` ("a symlinked component that
    escapes also renders `path-escape`"), else `"symlinked"`. Neither is
    ever listed or descended. A path whose components do not all appear in
    their parents' listings, or that is not a directory, is `"missing"`.
    A contained symlink and a missing path keep the caller's
    `unresolved-reference` line."""
    # Every directory component must also appear byte for byte in its
    # parent's listing (`listed_names`): opening the path as written would
    # let a case-folding host match `sources` to `Sources` where a Linux
    # host matches nothing, and the rows would then depend on the host.
    # `verdicts` is the `DirectoryVerdicts` memo of one `read_projects` call:
    # the symlink check runs first, so `listed_names` keeps only the checkout
    # root's listing or a listing of a directory that check found real. The
    # containment and directory checks below stay live.
    if rel_segments:
        linked = _synced_root_symlink_verdict(root, rel_segments, verdicts)
        if linked is not None:
            return linked, None
        if not listed_names(root, rel_segments, verdicts):
            return "missing", None
    candidate = root.joinpath(*rel_segments) if rel_segments else root
    if not _contained_charged(root, rel_segments, _work_budget(verdicts)):
        return "missing", None
    try:
        if not candidate.is_dir():
            return "missing", None
    except OSError:
        return "missing", None
    return "dir", candidate


def _list_item_line(doc, obj_id, field_name, wanted_id):
    """The line of `wanted_id`'s first occurrence in the list field, or the
    object's first line when the list does not hold it. The document indexes
    each field once, so asking once per item is linear in the list."""
    obj = doc.objects.get(obj_id)
    if obj is None:
        return None
    line = doc.first_item_line(obj_id, field_name, wanted_id)
    if line is not None:
        return line
    span = doc.object_spans.get(obj_id, (None, None))
    return span[0]


def _sorted_entries(dir_path, budget=None):
    """`dir_path`'s entries sorted by name, or none when it cannot be listed.
    The completed listing charges `budget` its entry count (L1) before it is
    walked."""
    try:
        with os.scandir(dir_path) as entries:
            listed = sorted(entries, key=lambda e: e.name)
    except OSError:
        return []
    charge_excluded(budget, len(listed))
    return listed


def _scan_swift_files(base, root, explicit_folders, budget=None):
    """Every `.swift` file under the directory `base`, as a path relative to
    `base`, in the order a depth-first walk sorted by name reaches it.

    The walk holds an explicit stack of directory iterators, never Python
    recursion, so a deep directory chain cannot raise `RecursionError`. It
    descends no symlink, and does not descend a directory whose name ends in
    one of `BUNDLE_SUFFIXES` (an asset catalog, a product or resource bundle,
    a `.lproj`) or an `explicitFolders` entry (ADR-0130 clause 9). It prunes
    every directory `swift_prune` prunes (ADR-0129 clause 8). The caller has
    already refused a `base` that lies in a pruned directory, by name. Each
    directory it lists charges `budget` its entry count (L1).

    `base`'s parts under the resolved root come from `os.path.realpath` and a
    string prefix, the same resolution `Path.resolve()` and
    `Path.relative_to` give, in time linear in the path: `relative_to`
    builds every parent of `base` as a `Path`, which is quadratic in the
    path's depth (the same fix as `swift_xcinputs.contained`). A `base`
    outside the root raises `ValueError`, as `relative_to` does."""
    held = _retained_source_filter(root)
    if held(base):
        return
    root_r = os.path.realpath(root)
    base_r = os.path.realpath(base)
    if base_r == root_r:
        base_rel = ()
    elif base_r.startswith(root_r.rstrip(os.sep) + os.sep):
        base_rel = tuple(base_r[len(root_r.rstrip(os.sep)) + 1:].split(os.sep))
    else:
        raise ValueError("the scanned directory lies outside the checkout")
    stack = [(iter(_sorted_entries(base, budget)), base_rel)]
    while stack:
        entries, rel_parts = stack[-1]
        entry = next(entries, None)
        if entry is None:
            stack.pop()
            continue
        try:
            if entry.is_symlink():
                continue
            if held(entry.path):
                continue
            child_rel = rel_parts + (entry.name,)
            if entry.is_dir(follow_symlinks=False):
                if swift_prune.pruned_name(entry.name):
                    continue
                if any(entry.name.endswith(sfx) for sfx in BUNDLE_SUFFIXES):
                    continue
                if "/".join(child_rel[len(base_rel):]) in explicit_folders:
                    continue
                stack.append((iter(_sorted_entries(entry.path, budget)), child_rel))
            elif entry.is_file(follow_symlinks=False) and entry.name.endswith(".swift"):
                yield "/".join(child_rel[len(base_rel):])
        except OSError:
            continue


def _swift_files(base_dir, root, base_segs, explicit_folders, verdicts=None):
    """`_scan_swift_files` of the real, contained directory `base_dir`, which
    `base_segs` names, as a tuple. With the `DirectoryVerdicts` memo of one
    `read_projects` call, the walk of one base and one `explicitFolders` set
    runs once for the call and is reused after (ADR-0129 clause 2): R
    folder-synced roots naming one directory, or E exception
    entries naming one directory, walk it once. The key is the repo-relative
    segments and the `explicitFolders` set, never an absolute path. Each
    caller checks containment and symlinks live before it asks.

    The first walk charges the memo's work budget for each listing (L1);
    each reuse charges the result's length (L4)."""
    budget = _work_budget(verdicts)
    return reuse_derived(verdicts, root, base_segs, ("swift", explicit_folders),
                         lambda: tuple(_scan_swift_files(base_dir, root, explicit_folders, budget)),
                         reuse_cost=len)


def _lproj_names(root, dir_segs, verdicts=None):
    """The names of the `.lproj` directories directly under the real,
    contained directory `dir_segs` names, sorted, from its one listing
    (`directory_entries`): a real directory, never a symlink. None at all
    when an entry's type cannot be read, as when the listing fails. With a
    memo, one `<dir>` is filtered once per call however many `/Localized/`
    entries name it (ADR-0129 clause 2). A reuse charges 1 plus the work
    the first build spent outside its listing (L6)."""
    def build():
        entries = directory_entries(root, dir_segs, verdicts)
        try:
            return tuple(name for name in sorted(entries)
                         if name.endswith(".lproj") and not entries[name].is_symlink()
                         and entries[name].is_dir(follow_symlinks=False))
        except OSError:
            return ()
    return reuse_derived(verdicts, root, dir_segs, ("lproj",), build)


def _localized_dir_segs(root_dir_segs, entry, budget=None):
    """The `<dir>` segments a `/Localized/<dir>/<name>` exception entry names,
    joined lexically onto the synced root, or None when it names no `<dir>`
    or escapes the checkout."""
    body = entry[len("/Localized/"):]
    if "/" not in body:
        return None
    return _lexical_join(root_dir_segs, body.split("/", 1)[0], budget)


#: The classifications `_classify_exception_entry` renders at the entry's own
#: line. An entry classified this way withholds nothing and adds nothing.
_LINE_ENTRY_KINDS = frozenset(["non-string", "never-resolved", "escape", "pruned"])


def _classify_exception_entry(entry, root_dir_segs, budget=None):
    """ADR-0130 clauses 3 and 9: the one classification of a
    `membershipExceptions` entry, shared by the build-file and the
    build-phase exception-set loops of `synced_memberships`. It touches no
    filesystem. Returns `(kind, value)`, decided in this order:

    1. `("non-string", None)`: the entry is not a string;
    2. `("never-resolved", None)`: it names a build-setting reference, so it
       is classified whole before any join and a following `..` cannot
       cancel the reference;
    3. `("localized", dir_segs)` for the `/Localized/<dir>/<name>` form,
       which is Xcode's own and not an absolute path. `dir_segs` is `None`
       when it names no `<dir>`. Its `<dir>` join and its `<name>` join are
       held to steps 4 and 6 first;
    4. `("escape", None)`: a leading `/`;
    5. `("escape", None)`: a `..` carries the lexical join out of the
       checkout;
    6. `("pruned", None)`: the joined path lies in a directory every Swift
       walk skips (ADR-0129 clause 8);
    7. `("path", rel_segs)`: the lexically normalised repo segments.

    Each kind in `_LINE_ENTRY_KINDS` renders one line at the entry's item
    line (`_exception_entry_line`), and changes no membership. Each lexical
    join charges `budget` its length (L5)."""
    if not isinstance(entry, str):
        return "non-string", None
    if never_resolved(entry):
        return "never-resolved", None
    if entry.startswith("/Localized/"):
        body = entry[len("/Localized/"):]
        if "/" not in body:
            return "localized", None
        dir_segs = _localized_dir_segs(root_dir_segs, entry, budget)
        # Each `.lproj` directory is one segment under `<dir>`, so one
        # placeholder segment decides whether `<name>` leaves the checkout.
        if dir_segs is None or _lexical_join(dir_segs + ("_",), body.split("/", 1)[1], budget) is None:
            return "escape", None
        if swift_prune.pruned_path(dir_segs):
            return "pruned", None
        return "localized", dir_segs
    if entry.startswith("/"):
        # `_lexical_join` treats a leading "/" as an empty first segment and
        # drops it, which would resolve an absolute entry as if it were
        # relative to the synced root.
        return "escape", None
    rel_segs = _lexical_join(root_dir_segs, entry, budget)
    if rel_segs is None:
        return "escape", None
    if swift_prune.pruned_path(rel_segs):
        return "pruned", None
    return "path", rel_segs


def _exception_entry_line(kind, pbxproj_path, loc, root_repo_path, collection):
    """The one residual line of an entry `_classify_exception_entry` put in
    `_LINE_ENTRY_KINDS`, or `None` when the detail bound refuses the escape
    detail, which repeats the root's path."""
    if kind == "non-string":
        return ("unsupported-project-form", pbxproj_path, loc, NON_STRING_EXCEPTION_ENTRY_DETAIL)
    if kind == "never-resolved":
        # It writes no `conditional-setting` line either, even when
        # `platformFiltersByRelativePath` names it.
        return ("unresolved-reference", pbxproj_path, loc, NEVER_RESOLVED_EXCEPTION_ENTRY_DETAIL)
    if kind == "pruned":
        return ("unresolved-reference", pbxproj_path, loc, swift_prune.EXCEPTION_ENTRY_DETAIL)
    if not _admit(collection, pbxproj_path, loc, root_repo_path):
        return None
    return ("path-escape", pbxproj_path, loc, EXCEPTION_ESCAPE_DETAIL % (_cell(root_repo_path),))


def _resolve_localized_entry(root, root_dir_segs, entry, verdicts=None):
    """ADR-0130 clauses 3 and 9: the `/Localized/<dir>/<name>` exception
    form is routed through the SAME checks `_classic_member_verdict`
    already applies to classic Sources-phase members -- a lexical join
    first (never a raw filesystem stat of the path as written), then
    `_real_contained_dir` for the `<dir>` component (real-path
    containment, no directory symlink ever descended) and
    `_classic_member_verdict` for the final file inside each `.lproj`
    (byte-exact name match against the directory's own listing, and
    `is_file(follow_symlinks=False)` so a symlinked file is refused).

    Returns `(escaped, files)`. `escaped` is True when `<dir>` has a
    symlinked component that resolves outside the checkout; nothing is then
    listed, and the caller renders `path-escape`.

    Each (entry, `.lproj`) evaluation charges the memo's work budget 1 unit
    first (L3), and each join its length (L5)."""
    budget = _work_budget(verdicts)
    body = entry[len("/Localized/"):]
    if "/" not in body:
        return False, []
    name = body.split("/", 1)[1]
    dir_segs = _localized_dir_segs(root_dir_segs, entry, budget)
    if dir_segs is None:
        return False, []
    verdict, candidate_dir = _real_contained_dir(root, dir_segs, verdicts)
    if verdict == "escape":
        return True, []
    if candidate_dir is None:
        return False, []
    results = []
    for lproj in _lproj_names(root, dir_segs, verdicts):
        charge(budget, 1)
        name_segs = _lexical_join(dir_segs + (lproj,), name, budget)
        if name_segs is None or swift_prune.pruned_path(name_segs):
            continue
        if _classic_member_verdict(root, name_segs, verdicts) == "member":
            results.append(segs_to_posix(name_segs))
    return False, results


def _unresolved_synced_lookups(doc, descent, pbxproj_path, targets_by_id):
    """ADR-0130 clause 2: a target's `fileSystemSynchronizedGroups` entry is a
    lookup. One that names no folder-synced root descent resolved renders one
    `unresolved-reference` at its list-item line: a dangling id, an object of
    another kind, or a root under an unresolved group or project directory.
    An id descent already renders elsewhere is skipped, as
    `classic_memberships` skips it: a cycle or second reach
    (`descent_unresolved_residuals`), an escape (`escape_residuals`) and a
    `BUILT_PRODUCTS_DIR` reference, which renders nothing.

    A field that is present but not a list names no root. It renders the one
    `unsupported-project-form` line `swift_pbxproj.list_field` gives each of
    the eleven list fields (ADR-0130 clauses 2 and 14), at the field's own
    line. Its
    type decides, never its truthiness, and it is never iterated: a string's
    characters are not ids, and one lookup per character rescanned the field
    for its line. This is the field's only reader that renders it."""
    residuals = []
    handled = descent.unresolved_ids | descent.escape_ids | descent.none_ids
    for tid, row in targets_by_id.items():
        detail = "target '%s' names a folder-synced root that descent did not resolve" % (
            _cell(row["name"]),)
        root_ids = swift_pbxproj.list_field(doc, tid, "fileSystemSynchronizedGroups", pbxproj_path, residuals)
        for root_id in root_ids:
            if isinstance(root_id, str) and (root_id in descent.synced_roots or root_id in handled):
                continue
            loc = _list_item_line(doc, tid, "fileSystemSynchronizedGroups", root_id)
            residuals.append(("unresolved-reference", pbxproj_path, loc, detail))
    return residuals


def synced_memberships(doc, root, descent, pbxproj_path, targets_by_id, container_name, verdicts=None,
                       collection=None):
    """`.swift` membership through each folder-synced root a target owns, with
    its exception sets applied (ADR-0130 clause 9). Returns `(memberships,
    residuals)`.

    ADR-0130 clauses 2 and 14: a root's `exceptions` or `explicitFolders`,
    and an exception set's `membershipExceptions`, that is present but not a
    list renders its one `unsupported-project-form` line and applies
    nothing, so the root's default rows stand. An item of `exceptions` that is not a
    string, names no object, or names an object that is no exception set
    renders the dangling-set line at its item line, and no entries are read.
    A non-string item of `explicitFolders` renders one
    `unsupported-project-form` at its item line and is not applied. A
    build-file set whose `target` names no native target has no entries
    read.

    Every entry of either exception-set kind is classified first by
    `_classify_exception_entry` (ADR-0130 clauses 3 and 9), on each read:
    a non-string entry, a build-setting reference, an escape and a pruned
    path each render one line at the entry's item line and change no
    membership. A build-phase set's own semantics are not interpreted: it
    renders one set-level line, and each contained `.swift` entry withholds
    its lexically normalised path from the owning target's default rows.

    `collection` (a `CollectionBudget`) bounds two things this reader would
    otherwise grow with a product. Every residual detail that repeats the
    root's path per exception set, per entry or per file is charged before
    it is built; the one symlinked-root line per root is not. Every
    membership record is counted before it is kept. Once the bound has
    refused a record, no further record is built, and neither a root's
    default scan nor an exception directory entry's scan runs. A file or
    `/Localized/` entry is still resolved against its directory's listing,
    so its unresolved line still renders while the detail bound admits it.

    `collection` also bounds the reader's work (`MAX_WORK_UNITS`): each
    exception set per (root, set), each build-phase set's owner scan per
    (set, owner), each exception entry per (root, set, entry) and each
    default row per (owner, file) charges 1 unit before it is read (L3), and
    the path checks, listings, scans and joins it runs charge theirs. When the bound refuses, the rows
    and lines read before stand, the refused entry or row adds none, and no
    later root is read: a root whose exception entries were cut short
    renders no default rows, since an unread entry could withhold one."""
    objects = doc.objects
    memberships = []
    # An exception set R roots list is read R times, and each read appends
    # the same entry lines: the sink keeps one per render key, and every
    # distinct line still renders (ADR-0129 clause 7).
    residuals = ResidualSink(_unresolved_synced_lookups(doc, descent, pbxproj_path, targets_by_id))
    # An exception set two roots list is read twice; its non-list line renders once.
    seen_fields = set()

    # One inverted ownership map for the project, so T owners of R roots
    # cost T + R, not T times R (ADR-0129 clause 2):
    # root id -> the targets whose `fileSystemSynchronizedGroups` names it,
    # in target order. Testing `root_id in groups` per root and target was
    # R times T list scans. Only a list names roots: `in` over a string
    # matches a substring, and `_unresolved_synced_lookups` renders a
    # non-list value as its one `unsupported-project-form` line instead. An
    # item that is not a string names no root.
    owners_by_root = {}
    for tid in targets_by_id:
        groups = objects[tid].get("fileSystemSynchronizedGroups")
        if not isinstance(groups, list):
            continue
        for gid in groups:
            if isinstance(gid, str):
                owners_by_root.setdefault(gid, {})[tid] = None

    try:
        for root_id in sorted(descent.synced_roots):
            root_obj = objects[root_id]
            root_dir_segs = descent.group_dirs[root_id]
            root_repo_path = segs_to_posix(root_dir_segs)
            # One route text per root, shared by every default row it yields.
            root_route = "folder-synced root %s" % (root_repo_path,)

            # An ordered set: iterated in target order, never in hash order.
            owner_ids = owners_by_root.get(root_id, {})

            # Both root fields are read once, here, whatever follows: a present
            # value that is not a list renders its one line and applies nothing.
            exc_ids = swift_pbxproj.list_field(doc, root_id, "exceptions", pbxproj_path, residuals)
            explicit_items = swift_pbxproj.list_field(doc, root_id, "explicitFolders", pbxproj_path, residuals)
            explicit_lines = doc.list_item_lines.get((root_id, "explicitFolders"), [])
            folders = set()
            for idx, item in enumerate(explicit_items):
                if isinstance(item, str):
                    folders.add(item)
                    continue
                loc = explicit_lines[idx] if idx < len(explicit_lines) else doc.object_spans.get(root_id)
                residuals.append(("unsupported-project-form", pbxproj_path, loc, NON_STRING_EXPLICIT_FOLDER_DETAIL))
            explicit_folders = frozenset(folders)

            if swift_prune.pruned_path(root_dir_segs):
                # ADR-0129 clause 8 governs this walk: a root in a directory every
                # Swift walk skips is refused by name, before any filesystem
                # access, and names no path. A root no target owns, with no
                # exception set, claims no membership and renders no prune line;
                # the field lines above still render.
                if owner_ids or exc_ids:
                    residuals.append(("unresolved-reference", pbxproj_path, doc.object_spans.get(root_id),
                                      swift_prune.SYNCED_ROOT_DETAIL))
                continue

            verdict = _synced_root_symlink_verdict(root, root_dir_segs, verdicts)
            if verdict is not None:
                loc = doc.object_spans.get(root_id)
                if verdict == "escape":
                    detail = "a folder-synced root is a symlinked directory that escapes the checkout"
                    residuals.append(("path-escape", pbxproj_path, loc, detail))
                else:
                    detail = "%s is a symlinked directory and is not descended" % (_cell(root_repo_path),)
                    residuals.append(("unresolved-reference", pbxproj_path, loc, detail))
                continue

            # ADR-0130 clause 14: only a dictionary retypes a file; any
            # other type renders its one line and withholds nothing.
            explicit_file_types = swift_pbxproj.dict_field(doc, root_id, "explicitFileTypes", pbxproj_path,
                                                           residuals)
            retyped_swift = sorted(set(p for p in explicit_file_types if p.endswith(".swift")))
            for p in retyped_swift:
                loc = doc.object_spans.get(root_id)
                if _admit(collection, pbxproj_path, loc, root_repo_path, p):
                    detail = "%s/%s is retyped by explicitFileTypes and withheld from membership" % (_cell(root_repo_path), _cell(p))
                    residuals.append(("unsupported-project-form", pbxproj_path, loc, detail))

            # A symlinked root has already been refused above, so the verdict
            # here is never `escape`.
            _verdict, base_dir = _real_contained_dir(root, root_dir_segs, verdicts)
            default_members = set()
            # Only an owner's default rows read the scan: a root no target owns,
            # or one read after the membership bound is full, lists nothing.
            if base_dir is not None and owner_ids and not _members_full(collection):
                for rel in _swift_files(base_dir, root, root_dir_segs, explicit_folders, verdicts):
                    default_members.add(_join_charged(root_repo_path, rel, collection))
            retyped_full = set()
            for p in retyped_swift:
                retyped_full.add(_join_charged(root_repo_path, p, collection))
            default_members -= retyped_full

            excluded_by_target = {}
            # ADR-0130 clause 9: a build-phase membership exception set
            # withholds the rows it names for ITS OWN target only -- never from
            # another owner (a set on target A must never withhold target B's
            # default rows). One root-wide map, path -> the owners it is withheld
            # from, holds each path once however many owners share the set; a
            # copy per owner was E entries times T owners (ADR-0129 clause
            # 2). A set whose `buildPhase` resolves to no owner withholds from
            # every owner: its paths go to `withheld_from_all`.
            withheld_from = {}
            withheld_from_all = set()
            # ADR-0130 clause 9: `platformFiltersByRelativePath` on an
            # owner's own exception set marks a DEFAULT (non-exception) row
            # conditional too -- Xcode's map lists filtered MEMBERS, not only
            # membership-exception entries.
            platform_filters_by_target = {}

            for exc_id in exc_ids:
                # L3 of `MAX_WORK_UNITS`: one unit per (root, exception set)
                # read, so a set listed N times costs N even when it holds no
                # entry.
                charge(collection, 1)
                # ADR-0130 clause 2: an item that is not a string, names no
                # object, or names an object that is no exception set names no
                # exception set. Each renders the dangling-set line at its own
                # item line, as a `dependencies` item does, and a non-string item
                # is never used as a key.
                exc_obj = objects.get(exc_id) if isinstance(exc_id, str) else None
                isa = _isa_of(exc_obj)
                if isa not in (_BUILD_PHASE_EXCEPTION_SET_ISA, _BUILD_FILE_EXCEPTION_SET_ISA):
                    loc = _list_item_line(doc, root_id, "exceptions", exc_id)
                    if _admit(collection, pbxproj_path, loc, root_repo_path):
                        detail = "a dangling exception set on %s" % (_cell(root_repo_path),)
                        residuals.append(("unresolved-reference", pbxproj_path, loc, detail))
                    continue
                # ADR-0130 clause 9: a `target` that is not a string is a
                # dangling id (`NOT_AN_ID` names no target and owns no root), and
                # a `platformFiltersByRelativePath` that is not a dictionary
                # renders its one line and conditions nothing.
                exc_target_id = _id_field(doc, exc_id, "target")
                entries = swift_pbxproj.list_field(doc, exc_id, "membershipExceptions", pbxproj_path,
                                                   residuals, seen_fields)
                entry_lines = doc.list_item_lines.get((exc_id, "membershipExceptions"), [])
                platform_filters = swift_pbxproj.dict_field(doc, exc_id, "platformFiltersByRelativePath",
                                                            pbxproj_path, residuals, seen_fields)

                if isa == _BUILD_PHASE_EXCEPTION_SET_ISA:
                    loc = doc.object_spans.get(exc_id)
                    if _admit(collection, pbxproj_path, loc, root_repo_path):
                        detail = BUILD_PHASE_SET_DETAIL % (_cell(root_repo_path),)
                        residuals.append(("unsupported-project-form", pbxproj_path, loc, detail))
                    # The set names its target through `buildPhase` (the target
                    # whose `buildPhases` lists that phase) or a `target` key. When
                    # neither resolves to an owner, the rows are withheld from
                    # every owner: withholding claims no membership, a guess would.
                    # ADR-0130 clause 14: `buildPhase` is matched as a whole
                    # element of a target's `buildPhases` LIST. A value of any
                    # other type makes that target no phase owner: `in` over a
                    # string is a substring test.
                    phase_id = _id_field(doc, exc_id, "buildPhase")
                    # Built in target order, never in hash order. One unit
                    # per (set, owner) the scan tests (L3 of `MAX_WORK_UNITS`).
                    charge(collection, len(owner_ids))
                    phase_owners = {}
                    for tid in owner_ids:
                        target_phases = (objects.get(tid) or {}).get("buildPhases")
                        if isinstance(phase_id, str) and isinstance(target_phases, list) and phase_id in target_phases:
                            phase_owners[tid] = None
                    if not phase_owners and exc_target_id in owner_ids:
                        phase_owners = {exc_target_id: None}
                    withheld_owners = frozenset(phase_owners)
                    if not phase_owners:
                        # Neither `buildPhase` nor the `target` key resolves to
                        # an owner: the rows are withheld from every owner (a
                        # guess would claim membership this exception set never
                        # named), and the dangling `buildPhase` itself renders
                        # its own `unresolved-reference` residual (ADR-0130
                        # clause 9) beside the generic `unsupported-project-form`
                        # line above.
                        dangling_loc = doc.object_spans.get(exc_id)
                        if _admit(collection, pbxproj_path, dangling_loc, root_repo_path):
                            dangling_detail = ("a build-phase membership exception set on %s names a "
                                                "buildPhase that resolves to no owning target") % (_cell(root_repo_path),)
                            residuals.append(("unresolved-reference", pbxproj_path, dangling_loc, dangling_detail))
                        withheld_owners = None
                    for idx, entry in enumerate(entries):
                        charge(collection, 1)
                        loc = entry_lines[idx] if idx < len(entry_lines) else doc.object_spans.get(exc_id)
                        kind, value = _classify_exception_entry(entry, root_dir_segs, collection)
                        if kind in _LINE_ENTRY_KINDS:
                            line = _exception_entry_line(kind, pbxproj_path, loc, root_repo_path, collection)
                            if line is not None:
                                residuals.append(line)
                            continue
                        # A contained `.swift` entry withholds its lexically
                        # normalised repo path, with no filesystem access. A
                        # `/Localized/` entry names `.lproj` content, which is
                        # never a member, so it withholds nothing.
                        if kind == "path" and value and value[-1].endswith(".swift"):
                            full = segs_to_posix(value)
                            if withheld_owners is None:
                                withheld_from_all.add(full)
                                continue
                            prior = withheld_from.get(full)
                            if prior is None:
                                withheld_from[full] = withheld_owners
                            elif not withheld_owners <= prior:
                                withheld_from[full] = prior | withheld_owners
                    continue

                exc_target_row = targets_by_id.get(exc_target_id)
                if exc_target_row is None:
                    loc = doc.object_spans.get(exc_id)
                    if _admit(collection, pbxproj_path, loc, root_repo_path):
                        detail = "an exception set on %s names no native target" % (_cell(root_repo_path),)
                        residuals.append(("unresolved-reference", pbxproj_path, loc, detail))
                    continue
                owns_root = exc_target_id in owner_ids
                if owns_root and platform_filters:
                    platform_filters_by_target.setdefault(exc_target_id, {}).update(platform_filters)

                for idx, entry in enumerate(entries):
                    charge(collection, 1)
                    if idx < len(entry_lines):
                        loc = entry_lines[idx]
                    else:
                        loc = doc.object_spans.get(exc_id)
                    kind, value = _classify_exception_entry(entry, root_dir_segs, collection)
                    if kind in _LINE_ENTRY_KINDS:
                        line = _exception_entry_line(kind, pbxproj_path, loc, root_repo_path, collection)
                        if line is not None:
                            residuals.append(line)
                        continue
                    if kind == "localized":
                        escaped, resolved = _resolve_localized_entry(root, root_dir_segs, entry, verdicts)
                        if escaped:
                            line = _exception_entry_line("escape", pbxproj_path, loc, root_repo_path, collection)
                            if line is not None:
                                residuals.append(line)
                            continue
                        if not resolved:
                            if _admit(collection, pbxproj_path, loc, root_repo_path):
                                detail = "a /Localized/ exception entry on %s resolves to no file" % (_cell(root_repo_path),)
                                residuals.append(("unresolved-reference", pbxproj_path, loc, detail))
                            continue
                        files = resolved
                    else:
                        # ADR-0130 clauses 3 and 9: the classic-member checks
                        # apply: `_real_contained_dir` for a directory entry
                        # (real-path containment, no directory symlink ever
                        # descended) and `_classic_member_verdict` for a file
                        # entry (byte-exact name match against the parent's own
                        # listing, `is_file(follow_symlinks=False)`). Neither ever
                        # stats the path as written. A symlinked component that
                        # resolves outside the checkout renders `path-escape`.
                        rel_segs = value
                        rel = segs_to_posix(rel_segs)
                        dir_verdict, dir_candidate = _real_contained_dir(root, rel_segs, verdicts)
                        member = None
                        if dir_verdict != "escape" and dir_candidate is None:
                            member = _classic_member_verdict(root, rel_segs, verdicts)
                        if dir_verdict == "escape" or member == "escape":
                            line = _exception_entry_line("escape", pbxproj_path, loc, root_repo_path, collection)
                            if line is not None:
                                residuals.append(line)
                            continue
                        if dir_candidate is not None:
                            files = []
                            # The listing feeds membership records only; once the
                            # bound is full there is nothing left to feed.
                            if not _members_full(collection):
                                for sub in _swift_files(dir_candidate, root, rel_segs, frozenset(), verdicts):
                                    files.append(_join_charged(rel, sub, collection))
                        elif member == "member":
                            files = [rel]
                        else:
                            if _admit(collection, pbxproj_path, loc, root_repo_path):
                                detail = "an exception entry on %s resolves to no file" % (_cell(root_repo_path),)
                                residuals.append(("unresolved-reference", pbxproj_path, loc, detail))
                            continue

                    conditional = entry in platform_filters
                    if conditional and _admit(collection, pbxproj_path, loc, root_repo_path, entry):
                        detail = "%s carries a platformFiltersByRelativePath condition" % (
                            _cell((root_repo_path if root_repo_path == "." else root_repo_path + "/") + entry),)
                        residuals.append(("conditional-setting", pbxproj_path, loc, detail))
                    for f in files:
                        if not f.endswith(".swift"):
                            continue
                        if not _admit_member(collection, pbxproj_path, loc):
                            break
                        if owns_root:
                            route = "excluded by an exception set"
                        else:
                            route = "added by an exception set"
                        m = {}
                        m["file"] = f
                        m["target"] = exc_target_row["name"]
                        m["target_id"] = exc_target_id
                        m["container"] = container_name
                        m["route"] = route
                        m["root"] = root_repo_path
                        m["loc"] = loc
                        m["conditional"] = conditional
                        memberships.append(m)
                        if owns_root:
                            excluded_by_target.setdefault(exc_target_id, set()).add(f)

            # Sorted once per root, not once per owner (ADR-0129 clause 2).
            members_sorted = sorted(default_members)
            for tid in sorted(owner_ids, key=lambda t: (targets_by_id[t]["name"], t)):
                # Once the membership bound has refused a record, no owner's
                # default rows are read.
                if _members_full(collection):
                    break
                target_row = targets_by_id[tid]
                excluded = excluded_by_target.get(tid, set())
                target_platform_filters = platform_filters_by_target.get(tid, {})
                loc = _list_item_line(doc, tid, "fileSystemSynchronizedGroups", root_id)
                for f in members_sorted:
                    charge(collection, 1)
                    if f in excluded or f in withheld_from_all or tid in withheld_from.get(f, ()):
                        continue
                    if not _admit_member(collection, pbxproj_path, loc):
                        break
                    # `platformFiltersByRelativePath` keys are relative to
                    # the synced root, the same convention `membershipExceptions`
                    # entries use.
                    rel_entry = f[len(root_repo_path) + 1:] if root_repo_path != "." else f
                    conditional = rel_entry in target_platform_filters
                    if conditional and _admit(collection, pbxproj_path, loc, f):
                        detail = "%s carries a platformFiltersByRelativePath condition" % (_cell(f),)
                        residuals.append(("conditional-setting", pbxproj_path, loc, detail))
                    m = {}
                    m["file"] = f
                    m["target"] = target_row["name"]
                    m["target_id"] = tid
                    m["container"] = container_name
                    m["route"] = root_route
                    m["conditional"] = conditional
                    m["root"] = root_repo_path
                    m["loc"] = loc
                    memberships.append(m)

    except WorkBoundExceeded:
        pass
    return tuple(memberships), tuple(residuals)


def _classic_member_verdict(root, rel_segments, verdicts=None):
    """ADR-0130 clause 3: a classic member path is matched byte for byte
    against the names its parent directory's real listing returns, never
    opened or stat-ed at the path as written -- so a host that folds case
    cannot make a mismatched name match. The parent directory is enumerated
    only after real-path containment, and the matched entry is a member
    only when `lstat` reports a regular file (a symlink is refused).

    Returns `"member"`, `"escape"` when the parent directory has a symlinked
    component, or the matched entry is itself a symlink, that resolves
    outside the checkout (the caller renders `path-escape`), or `"absent"`
    for every other refusal. A symlinked entry is never opened: only its
    real path is compared with the checkout."""
    if not rel_segments:
        return "absent"
    parent_segs, name = rel_segments[:-1], rel_segments[-1]
    verdict, parent_dir = _real_contained_dir(root, parent_segs, verdicts)
    if verdict == "escape":
        return "escape"
    if parent_dir is None:
        return "absent"
    # The parent's one listing for the call (ADR-0129 clause 2): P
    # members of one directory cost one `scandir` and one dictionary lookup
    # each, where a `scandir` per member cost P listings of P entries. The
    # containment checks above and the escape check below stay live.
    entry = directory_entries(root, parent_segs, verdicts).get(name)
    if entry is None:
        return "absent"
    try:
        if entry.is_file(follow_symlinks=False):
            return "member"
        if entry.is_symlink() and not _contained_charged(root, rel_segments, _work_budget(verdicts)):
            return "escape"
    except OSError:
        pass
    return "absent"


def classic_memberships(doc, root, descent, pbxproj_path, targets_by_id, container_name, verdicts=None,
                        collection=None):
    """`.swift` membership through each target's `PBXSourcesBuildPhase` build
    files (ADR-0130 clause 8). Returns `(memberships, residuals)`.

    ADR-0130 clauses 2 and 14: a target's `buildPhases` or a phase's `files`
    that is present but not a list renders its one `unsupported-project-form`
    line and is not read; this is the only reader of `buildPhases` that
    renders it. An item of either that is not a string renders one
    `unresolved-reference` at its item line and is never used as a key.

    `collection` (a `CollectionBudget`) charges every residual detail that
    repeats the target's name before it is built, and counts every
    membership record before it is kept. It also bounds the reader's work
    (`MAX_WORK_UNITS`): each build file per (target, phase, file) charges 1 unit
    before it is read (L3), and its path checks charge theirs. When the
    bound refuses, the rows and lines read before stand, the refused build
    file adds no row, and no later build file is read."""
    objects = doc.objects
    memberships = []
    # A Sources phase T targets list is read T times, and each read appends
    # the same build-file lines: the sink keeps one per render key, and
    # every distinct line still renders (ADR-0129 clause 7).
    residuals = ResidualSink()
    # A phase two targets list is read twice; its non-list line renders once.
    seen_fields = set()
    # Built once: every unresolved build file below consults it.
    handled = descent.unresolved_ids | descent.escape_ids | descent.none_ids
    # File references whose symlinked escape has rendered its one line.
    escaped_refs = set()
    # Each file reference's POSIX path and prune verdict, built once for the
    # call: a build file listed M times would otherwise rebuild a path M
    # times from one tuple, which descent charged once (L5). Its
    # member verdict is kept too, with the work units its path checks
    # spent outside any listing or reuse, and a later build file naming the
    # same reference charges 1 plus those units (L6): identical references
    # are read once per call, each is still charged, and a listing is never
    # charged twice.
    paths_of = {}
    budget = _work_budget(verdicts)

    try:
        for tid, row in targets_by_id.items():
            phase_ids = []
            for pid in swift_pbxproj.list_field(doc, tid, "buildPhases", pbxproj_path, residuals):
                # ADR-0130 clause 2: an item that is not a string, or a string
                # naming no object, is not an object id. An item naming another
                # kind of phase is a legitimate phase and renders nothing.
                if not isinstance(pid, str) or pid not in objects:
                    residuals.append(("unresolved-reference", pbxproj_path,
                                      _list_item_line(doc, tid, "buildPhases", pid),
                                      NON_STRING_BUILD_PHASE_ITEM_DETAIL))
                    continue
                if _isa_of(objects.get(pid)) == "PBXSourcesBuildPhase":
                    phase_ids.append(pid)
            for phase_id in phase_ids:
                file_ids = swift_pbxproj.list_field(doc, phase_id, "files", pbxproj_path, residuals, seen_fields)
                lines = doc.list_item_lines.get((phase_id, "files"), [])
                for idx, bf_id in enumerate(file_ids):
                    charge(collection, 1)
                    if idx < len(lines):
                        loc = lines[idx]
                    else:
                        loc = doc.object_spans.get(phase_id)
                    bf_obj = objects.get(bf_id) if isinstance(bf_id, str) else None
                    if bf_obj is None or _isa_of(bf_obj) != "PBXBuildFile":
                        if _admit(collection, pbxproj_path, loc, row["name"]):
                            detail = "a dangling build file in '%s's Sources phase" % (_cell(row["name"]),)
                            residuals.append(("unresolved-reference", pbxproj_path, loc, detail))
                        continue
                    # ADR-0130 clause 2: a `fileRef` that is not a string
                    # is `NOT_AN_ID`, which no lookup finds: the dangling line
                    # below renders for it, after its platform-filter line.
                    fref_id = _id_field(doc, bf_id, "fileRef")
                    conditional = bool(bf_obj.get("platformFilter") or bf_obj.get("platformFilters"))
                    if conditional and _admit(collection, pbxproj_path, loc, row["name"]):
                        detail = "a build file in '%s's Sources phase carries a platform filter" % (_cell(row["name"]),)
                        residuals.append(("conditional-setting", pbxproj_path, loc, detail))
                    fref_obj = objects.get(fref_id) if fref_id else None
                    if fref_obj is None:
                        if _admit(collection, pbxproj_path, loc, row["name"]):
                            detail = "a dangling file reference in '%s's Sources phase" % (_cell(row["name"]),)
                            residuals.append(("unresolved-reference", pbxproj_path, loc, detail))
                        continue
                    candidates = []
                    if _isa_of(fref_obj) == _VARIANT_GROUP_ISA:
                        for cid in descent.variant_children.get(fref_id, []):
                            candidates.append(cid)
                    else:
                        candidates.append(fref_id)
                    any_resolved = False
                    for cid in candidates:
                        rel = descent.file_segments.get(cid)
                        if rel is None:
                            continue
                        any_resolved = True
                        known = paths_of.get(cid)
                        if known is None:
                            known = paths_of[cid] = [segs_to_posix(rel), swift_prune.pruned_path(rel), None, 0]
                        rel_path, pruned, verdict, units = known
                        if rel_path.endswith(".swift"):
                            if pruned:
                                residuals.append(("unresolved-reference", pbxproj_path, loc,
                                                  swift_prune.SOURCES_MEMBER_DETAIL))
                                continue
                            if verdict is None:
                                before = (budget.work_spent - budget.work_excluded) if budget is not None else 0
                                verdict = _classic_member_verdict(root, rel, verdicts)
                                known[2] = verdict
                                known[3] = ((budget.work_spent - budget.work_excluded - before)
                                            if budget is not None else 0)
                            else:
                                charge_excluded(budget, 1 + units)
                            if verdict == "escape":
                                # ADR-0130 clause 3: a symlinked component that
                                # escapes renders `path-escape`, once per file
                                # reference at its own span, as a lexical escape
                                # does (`escape_residuals`).
                                if cid not in escaped_refs:
                                    escaped_refs.add(cid)
                                    residuals.append(("path-escape", pbxproj_path, doc.object_spans.get(cid),
                                                      REFERENCE_ESCAPE_DETAIL))
                                continue
                            if verdict != "member":
                                if _admit(collection, pbxproj_path, loc, row["name"], rel_path):
                                    detail = ("a file reference in '%s's Sources phase names %s, "
                                              "which no directory listing matches byte for byte "
                                              "as a regular file") % (_cell(row["name"]), _cell(rel_path))
                                    residuals.append(("unresolved-reference", pbxproj_path, loc, detail))
                                continue
                            if not _admit_member(collection, pbxproj_path, loc):
                                continue
                            m = {}
                            m["file"] = rel_path
                            m["target"] = row["name"]
                            m["target_id"] = tid
                            m["container"] = container_name
                            m["route"] = "classic Sources build phase"
                            m["root"] = None
                            m["loc"] = loc
                            m["conditional"] = conditional
                            memberships.append(m)
                    if not any_resolved:
                        # A cycle, a second reach or a dangling id already
                        # renders at its own list-item line
                        # (`descent_unresolved_residuals`), an escape renders
                        # `path-escape` (`escape_residuals`), and a
                        # `BUILT_PRODUCTS_DIR` product renders nothing (clause
                        # 3). Every other unresolved lookup -- a build-setting
                        # reference or a custom source tree, a reference in no group, a child
                        # of an unresolved group -- renders one
                        # `unresolved-reference` here (clauses 2 and 3), so a
                        # membership never vanishes without a line. A resolved
                        # variant group with no children names nothing.
                        rendered_elsewhere = any(cid in handled for cid in candidates + [fref_id])
                        empty_variant = not candidates and fref_id in descent.group_dirs
                        if not rendered_elsewhere and not empty_variant and _admit(
                                collection, pbxproj_path, loc, row["name"]):
                            detail = "a file reference in '%s's Sources phase did not resolve" % (_cell(row["name"]),)
                            residuals.append(("unresolved-reference", pbxproj_path, loc, detail))

    except WorkBoundExceeded:
        pass
    return tuple(memberships), tuple(residuals)



# ═════════════════════════════ read_projects ═══════════════════════════════
# ADR-0130 clauses 1, 2, 4, 6 and 14; ADR-0129 clause 10's consumer
# interface. The one entry point
# `swift.py`'s `_project_facts` calls. Reads every `*.xcodeproj` bundle a
# `ProjectReads` census (`swift.ProjectReads`, duck-typed here so this module
# never imports `swift.py`) names, parses each `project.pbxproj`, and merges
# every container's targets, target dependencies and `.swift` memberships
# into one sorted result -- a plain dict matching `swift.ProjectFacts`'s own
# field shapes, never that class itself.

#: `_safe_read_bytes` refusal reasons that map straight onto a
#: `project-unreadable` kind (ADR-0130 clause 14); `oversize` renders its own
#: class instead (handled separately, never through this map).
#: `escape` joins `not-regular`: the walk descends no directory symlink, so a
#: walk-reached input escapes only through a symlink at its own path.
_REFUSAL_TO_KIND = {"not-regular": "not-regular", "escape": "not-regular",
                    "read-failed": "read-failed"}


class _ParsedProject:
    __slots__ = ("bundle_rel", "pbxproj_rel", "doc", "descent", "targets_by_id", "target_rows")

    def __init__(self, bundle_rel, pbxproj_rel, doc, descent, targets_by_id, target_rows):
        self.bundle_rel = bundle_rel
        self.pbxproj_rel = pbxproj_rel
        self.doc = doc
        self.descent = descent
        self.targets_by_id = targets_by_id
        self.target_rows = target_rows


def _membership_loc(loc):
    """Normalise a `swift_xcode` row's `loc` (a bare line int, a `(start,
    end)` span, or `None`) into the `(start, end)` tuple `swift.py`'s
    `_loc_cell` renders -- and, for a residual's `ranges` field, into the
    `[(start, end)]` list `_render_residual_block` merges."""
    if loc is None:
        return None
    if isinstance(loc, tuple):
        return loc
    return (loc, loc)


def _residual_ranges(loc):
    span = _membership_loc(loc)
    return None if span is None else [span]


#: The same normalisation for `swift.py`, which renders the detail bound's
#: `scan-cap` line from the raw span `CollectionBudget` kept.
residual_ranges = _residual_ranges


def read_projects(root, reads, manifests, workspace_facts=None, collection=None):
    """`swift.ProjectReads` + the checkout root -> a plain dict matching
    `swift.ProjectFacts`'s field shapes (`targets`, `target_deps`,
    `product_deps`, `dependencies`, `memberships`, `residuals`).

    `product_deps` and `dependencies` are populated by
    `swift_xcode_products.resolve_product_dependencies`, called once below
    after every project is parsed. `workspace_facts` is the tuple of
    `swift_xcinputs.WorkspaceFacts` that `swift._workspace_facts_list`
    builds. It passes through to that call, so unlinked-product resolution
    reaches an enclosing standalone workspace's package refs (ADR-0130
    clause 7).

    A pbxproj-less bundle renders `missing-input` naming the bundle's
    repo-relative path (clause 1). A present but non-regular
    `project.pbxproj`, or one `_scan` already refused, renders
    `project-unreadable` with the refusal's kind, or `oversize` when that
    was the refusal. Every other refusal reason `_scan` can record is folded
    to `read-failed`, the catch-all OS-error kind, rather than invented as a
    new class. Every residual line carries the declaring file's
    repo-relative path and a closed detail -- never a host path, setting
    value or exception text.

    `collection` is the calling concern's `swift_xcinputs.CollectionBudget`
    (the per-call residual detail and membership bounds). This function
    renders the membership bound's `scan-cap` line; the detail bound's line
    belongs to the caller, which charges the same budget afterwards. Called
    without one, it builds its own and renders both lines.

    The same budget bounds the readers' work (`MAX_WORK_UNITS`):
    each project read, each target-dependency pass and each product pass
    charges 1 unit first, and every reader below charges its own items,
    path checks, listings and joins. This function renders the work bound's
    `scan-cap` line beside the membership bound's. When the bound refuses,
    the rows and lines already collected render, the project being read
    keeps its target rows (never charged: they are linear in the project
    file, which its read bound holds) and the rows of the references read
    before the refusal, and no later project reference is read. A project
    whose group descent was refused renders no membership, since its paths
    are unknown.
    """
    targets: list = []
    target_deps: list = []
    product_deps: list = []
    dependencies: list = []
    memberships: list = []
    # The call's residuals, one per render key; every distinct line still
    # renders (ADR-0129 clause 7). A project's
    # buffer below commits into it only when the project succeeded.
    residuals = ResidualSink()
    parsed: list = []
    # ADR-0130 clauses 2 and 3: the directory memo of THIS call, and only this
    # call. It is dropped on return, so a later derive in the same process
    # reads the filesystem as it is then.
    own_collection = collection is None
    if own_collection:
        collection = CollectionBudget()
    verdicts = DirectoryVerdicts(root, collection)

    for bundle_rel, present, regular in reads.bundles:
        pbxproj_rel = f"{bundle_rel}/project.pbxproj"
        if not present:
            residuals.append((
                "missing-input", bundle_rel, None,
                f"the Xcode project bundle '{_cell(bundle_rel)}' holds no project.pbxproj"))
            continue
        if not regular:
            residuals.append(("project-unreadable", pbxproj_rel, None, "not-regular"))
            continue
        refusal_reason = reads.refusals.get(pbxproj_rel)
        if refusal_reason == "oversize":
            residuals.append((
                "oversize", pbxproj_rel, None,
                "project.pbxproj exceeds its dedicated read bound and is not read"))
            continue
        if refusal_reason is not None:
            kind = _REFUSAL_TO_KIND.get(refusal_reason, "read-failed")
            residuals.append(("project-unreadable", pbxproj_rel, None, kind))
            continue
        raw = reads.files.get(pbxproj_rel)
        if raw is None:
            # Neither refused nor read: the aggregate scan cap stopped before
            # reaching it. `reads.residuals` already carries the `scan-cap`
            # line for this concern; nothing further to render here.
            continue
        if not collection.work(1):
            # Past the work bound (`MAX_WORK_UNITS`) no later project is
            # read. Its one `scan-cap` line renders below.
            break

        # Buffered per project and committed only when every step succeeded:
        # a project that fails part-way renders its one `malformed` line and
        # nothing else, never half its rows beside that line.
        p_targets: list = []
        p_members: list = []
        p_res = ResidualSink()
        try:
            parsed_doc = swift_pbxproj.read_pbxproj(raw)
            if isinstance(parsed_doc, swift_pbxproj.PbxprojRefusal):
                residuals.append(("project-unreadable", pbxproj_rel, None, parsed_doc.kind))
                continue

            doc = parsed_doc
            if doc.unsupported_object_version:
                span = doc.object_spans.get(doc.root_id)
                # ADR-0130 clause 14: an absent or non-string
                # `objectVersion` has no value to name.
                if doc.object_version is None:
                    version_detail = NON_STRING_FIELD_DETAILS["objectVersion"]
                else:
                    version_detail = f"objectVersion '{_cell(doc.object_version)}' is outside the tested set"
                p_res.append(("unsupported-project-form", pbxproj_rel, _residual_ranges(span), version_detail))

            for klass, path, span, detail in build_setting_residuals(doc, pbxproj_rel):
                p_res.append((klass, path, _residual_ranges(span), detail))

            # Target rows are read before descent and never charged, so a
            # project the work bound stops part-way still renders them.
            rows, targets_by_id, target_res = target_rows(doc, pbxproj_rel, bundle_rel, collection=collection)
            p_targets.extend(rows)
            for klass, path, span, detail in target_res:
                p_res.append((klass, path, _residual_ranges(span), detail))

            parent = () if "/" not in bundle_rel else tuple(bundle_rel.rsplit("/", 1)[0].split("/"))
            try:
                descent = descend(doc, parent, collection)
            except WorkBoundExceeded:
                # A partial descent names some paths and not others, and
                # every membership read from it would guess. None is read.
                descent = None
            if descent is not None:
                for klass, path, span, detail in descent_unresolved_residuals(doc, descent, pbxproj_rel):
                    p_res.append((klass, path, _residual_ranges(span), detail))
                for klass, path, span, detail in escape_residuals(doc, descent, pbxproj_rel):
                    p_res.append((klass, path, _residual_ranges(span), detail))

                # Each reader keeps what it read before a refusal and stops.
                # A refused classic read leaves the folder-synced read unrun.
                classic_m, classic_res = classic_memberships(doc, root, descent, pbxproj_rel, targets_by_id,
                                                             bundle_rel, verdicts=verdicts, collection=collection)
                synced_m, synced_res = ((), ())
                if not collection.work_tripped:
                    synced_m, synced_res = synced_memberships(doc, root, descent, pbxproj_rel, targets_by_id,
                                                              bundle_rel, verdicts=verdicts, collection=collection)
                for m in classic_m + synced_m:
                    m = dict(m)
                    m["loc"] = _membership_loc(m["loc"])
                    m["path"] = pbxproj_rel
                    p_members.append(m)
                for klass, path, span, detail in classic_res + synced_res:
                    p_res.append((klass, path, _residual_ranges(span), detail))
                parsed.append(_ParsedProject(bundle_rel, pbxproj_rel, doc, descent, targets_by_id, rows))

            targets.extend(p_targets)
            memberships.extend(p_members)
            residuals.extend(p_res)
            if collection.work_tripped:
                break
        except WorkBoundExceeded:
            # Every reader above keeps its own refusal; this boundary only
            # makes sure a refusal can never render as `malformed`.
            break
        except Exception:
            # ADR-0130 clause 2, last bullet: no content of a project
            # file ever makes the deriver exit 2. Every exception a
            # per-file step raises -- RecursionError and MemoryError
            # included -- is caught at THIS file's boundary; the bundle
            # renders one `project-unreadable`/`malformed` residual and the
            # derive continues with the next bundle. Reached only when a
            # pathological form defeats a narrower guard upstream (the
            # iterative `descend()` above already removes the unbounded-
            # recursion case for group nesting specifically).
            residuals.append(("project-unreadable", pbxproj_rel, None, "malformed"))
            continue

    by_container = {p.bundle_rel: p for p in parsed}
    for p in parsed:
        if not collection.work(1):
            break
        # ADR-0130 clause 2, last bullet: the per-project boundary above
        # covers only the first parse loop. This SECOND loop reads `target_dependencies` -- which
        # itself guards every type-confused id (see that function's own
        # docstring) -- but a fresh boundary here means no future content
        # shape reached from this loop can ever exit the deriver either:
        # one bundle's malformed dependency graph renders one residual and
        # target-dependency resolution continues with the next project.
        try:
            other_containers = {}
            for fid, segs in p.descent.file_segments.items():
                candidate_rel = segs_to_posix(segs)
                other = by_container.get(candidate_rel)
                if other is not None and other is not p:
                    other_containers[fid] = {
                        "objects": other.doc.objects,
                        "container_name": other.bundle_rel,
                        "targets_by_id": other.targets_by_id,
                    }
            edges, dep_res = target_dependencies(
                p.doc, p.bundle_rel, p.targets_by_id, p.pbxproj_rel, other_containers=other_containers,
                collection=collection)
            target_deps.extend(edges)
            for klass, path, span, detail in dep_res:
                residuals.append((klass, path, _residual_ranges(span), detail))
        except Exception:
            residuals.append(("project-unreadable", p.pbxproj_rel, None, "malformed"))
            continue

    # Package-product dependency resolution (ADR-0130 clause 7) -- remote
    # references, project-level XCRemoteSwiftPackageReference rows, local
    # references and unlinked-product candidate matching. `resolve_product_dependencies`
    # never imports `swift_xcode.py` (this module), so it is safe to call
    # from here without forming an import cycle.
    raw_product_deps, raw_dependencies, prod_res = resolve_product_dependencies(
        root, parsed, manifests, workspace_facts, verdicts=verdicts, collection=collection)
    for pd in raw_product_deps:
        pd = dict(pd)
        pd["loc"] = _membership_loc(pd["loc"])
        product_deps.append(pd)
    for d in raw_dependencies:
        d = dict(d)
        d["loc"] = _membership_loc(d["loc"])
        dependencies.append(d)
    for klass, path, span, detail in prod_res:
        residuals.append((klass, path, _residual_ranges(span), detail))
    bound_lines = [collection.membership_line(), collection.work_line()]
    if own_collection:
        bound_lines.append(collection.detail_line())
    for line in bound_lines:
        if line is not None:
            klass, path, span, detail = line
            residuals.append((klass, path, _residual_ranges(span), detail))

    targets.sort(key=lambda r: (r["container"], r["path"], r["loc"] or (0, 0), r["name"], r["target_id"]))
    target_deps.sort(key=lambda e: (e["container"], e["from"], e["to_container"], e["to"],
                                    e["from_id"], e["to_id"]))
    # ADR-0130 clause 10: one row per file, target and route. A target is
    # identified by its container and its object id, never by its name, so
    # two targets sharing a name never merge. Two build files naming one file
    # reference in one target collapse to the row at the first declaring
    # line, and that row is conditional only when every build file naming it
    # is.
    merged: dict = {}
    for m in sorted(memberships, key=lambda m: (m["file"], m["container"], m["target"], m["target_id"],
                                                m["route"], m["loc"] or (0, 0))):
        key = (m["file"], m["container"], m["target_id"], m["route"])
        if key in merged:
            merged[key]["conditional"] = merged[key]["conditional"] and m["conditional"]
        else:
            merged[key] = dict(m)
    memberships[:] = merged.values()
    memberships.sort(key=lambda m: (m["file"], m["target"], m["route"], m["container"], m["target_id"]))
    residuals.sort(key=lambda r: (r[1], r[0], r[2][0] if r[2] else (0, 0), r[3]))

    return {
        "targets": tuple(targets),
        "target_deps": tuple(target_deps),
        "product_deps": tuple(product_deps),
        "dependencies": tuple(dependencies),
        "memberships": tuple(memberships),
        "residuals": tuple(residuals),
    }
