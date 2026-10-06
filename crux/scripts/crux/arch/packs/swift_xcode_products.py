"""swift_xcode_products.py — package-product dependency resolution over a
parsed `project.pbxproj` document (ADR-0130 clause 7).

Scope of this module: given the parsed projects `swift_xcode.read_projects`
already built (each exposing `.bundle_rel`, `.pbxproj_rel`, `.doc`,
`.descent`, `.targets_by_id`) and the manifests the Swift-source scan
already parsed, this module resolves every `XCSwiftPackageProductDependency`
a target names and every project-level `XCRemoteSwiftPackageReference`, into
`product_deps` and `dependencies` rows matching `swift.ProjectFacts`'s own
field shapes (ADR-0130 clause 7; ADR-0129 clause 10's consumer interface):

- A remote reference renders a dependency row: `from` the declaring target,
  `dependency`/`location` the stripped `repositoryURL`, `requirement` its
  kind and value, `declared at` the target's `packageProductDependencies`
  list-item line. An unknown requirement key renders `unsupported-project-form`,
  and so does a present `repositoryURL` that is not a string, whose row
  renders location `—`.
- A project-level `XCRemoteSwiftPackageReference` renders one dependency row
  unconditionally (whether or not any product dependency uses it): `from`
  the literal `project`, `dependency`/`location` the stripped URL,
  `declared at` the reference object's own span.
- A local reference (`XCLocalSwiftPackageReference`) resolves `relativePath`
  under ADR-0130 clause 3: a build-setting reference (`never_resolved`) or a
  non-string value is never resolved and renders nothing, since a package
  reference is in no Sources phase, and so does every reference under a
  project whose `projectDirPath` is `UNRESOLVED_DIR`. An absent
  `relativePath` renders `unresolved-reference`, and an empty one names the
  project directory, as `.` does. An escaping path renders `path-escape`, a
  contained path with no `Package.swift` renders `missing-input`, a product
  the container does not declare renders `unresolved-reference`, and a
  plugin-kind product renders a dependency row instead of an edge.
- An unlinked product (no `package` field) resolves against the containers
  the project reaches -- under its groups and synced roots (attached or
  not), its local references, and its enclosing standalone workspace when
  `workspace_facts` names one -- matching literal LIBRARY products byte for
  byte. Exactly one match draws an edge; zero or several render
  `unresolved-reference` naming the candidates by repo-relative path.
- Each residual detail that repeats a name or a path renders only while the
  concern's `CollectionBudget` admits it (`_admit`).
- A project's `packageReferences` or a target's `packageProductDependencies`
  that is present but not a list renders one `unsupported-project-form` and
  is not read (ADR-0130 clauses 2 and 14). A `packageReferences` item that
  is not a string renders one `unresolved-reference` at its item line.
- ADR-0130 clauses 2, 3 and 14: a `package` that is not a string is a
  dangling id (`swift_pbxproj.id_field`), a `productName` that is not a
  string renders `—` and one `unsupported-project-form`
  (`swift_pbxproj.string_field`), a non-string `isa` is an unrecognised
  `isa` (`swift_pbxproj.isa_of`), and a `projectDirPath` that is not a
  string is never resolved.

Imports only the standard library and crux's own modules (ADR-0130 clause
1): `crux.arch.core`, `swift_prune`, `swift_pbxproj` for the shared
typed field reads (`list_field`, `id_field`, `string_field` and
`isa_of`), and from `swift_xcinputs` the shared path checks `listed_names`,
`has_symlink_component` and `never_resolved` with the `UNRESOLVED_DIR`
marker. Never imports `swift.py` -- the `manifests` this module reads are
`swift.ManifestFacts` instances the caller (`swift_xcode.read_projects`)
already holds, consumed here as plain data (`.directory`, `.products`)
rather than as an import. Never imports `swift_xcode.py` either: that
module is this one's sole caller, and importing it back would form a
cycle -- the handful of helpers both modules need are small, pure and
duplicated here rather than shared: `_lexical_join`, `_list_item_line`,
`_resolve_project_dir` (a copy of `swift_xcode.resolve_project_dir`),
`_segs_to_posix` (a copy of `swift_xcode.segs_to_posix`), `_safe_contained`
and `_admit` (a copy of `swift_xcode._admit`). The work bound
(`swift_xcinputs.MAX_WORK_UNITS`) reaches this module as the caller's
`CollectionBudget`, charged through `swift_xcinputs.charge`.
"""

from __future__ import annotations

from ..core import _cell
from . import swift_pbxproj, swift_prune
from .swift_xcinputs import (UNRESOLVED_DIR, ResidualSink, WorkBoundExceeded, charge, charge_excluded,
                             contained, has_symlink_component, l2_units, listed_names, never_resolved)

_REMOTE_REF_ISA = "XCRemoteSwiftPackageReference"
_LOCAL_REF_ISA = "XCLocalSwiftPackageReference"
_PRODUCT_DEP_ISA = "XCSwiftPackageProductDependency"

#: The closed set of requirement kinds the fixtures pin, and the value
#: key(s) each carries (ADR-0130 clause 7). A `kind` outside this set, or an
#: extra key beyond `kind` + the expected value key(s), renders
#: `unsupported-project-form`.
_REQUIREMENT_VALUE_KEYS = {
    "exactVersion": ("version",),
    "upToNextMajorVersion": ("minimumVersion",),
    "upToNextMinorVersion": ("minimumVersion",),
    "range": ("minimumVersion", "maximumVersion"),
    "branch": ("branch",),
    "revision": ("revision",),
}


def _strip_location(loc: str) -> str:
    """Duplicate of `swift.py::_strip_location`, body for body
    (`rule:swift-dependency-location-strips-credentials`): strip userinfo,
    query and fragment from a location string using str methods only, never
    a regex over untrusted content. `swift.py`'s docstring states the cuts;
    `StripLocationParityTests` holds the two copies to one output."""
    s = str(loc)
    head, rest = "", s
    sep = s.find("://")
    if sep != -1 and _is_uri_scheme(s[:sep]):
        head, rest = s[: sep + 3], s[sep + 3 :]
    ends = [i for i in (rest.find("?"), rest.find("#")) if i != -1]
    end = min(ends) if ends else len(rest)
    start = rest.rfind("@") + 1
    return head + (rest[start:end] if start <= end else "")


#: ADR-0130 clause 2, items: the detail for a `packageReferences` item that is
#: not an object id -- not a string, or a string naming no object. It names no
#: reference, and a non-string item is never used as a key.
NON_STRING_PACKAGE_REFERENCE_ITEM_DETAIL = ("a project's packageReferences entry is not an object id "
                                            "and is not read")


#: The `unsupported-project-form` detail for a present `repositoryURL` that is
#: not a string (ADR-0130 clause 7). It names no part of the value: a list or a
#: dictionary can carry a credential, and its Python repr is neither escaped
#: nor stable.
NON_STRING_REPOSITORY_LOCATION_DETAIL = "package repository location is not a string"


def _repository_location(value, path, span, residuals):
    """The stripped location for a remote reference's `repositoryURL`. A
    present value that is not a string names no location: it appends one
    `unsupported-project-form` residual and returns `""`, which renders `—`
    (ADR-0129:153). Its type decides, never its truthiness. An absent or
    empty-string value returns `""` and appends nothing, as before. Both read
    sites -- the project's `packageReferences` row and a target's remote
    product -- call this, so the two can never disagree."""
    if value is not None and not isinstance(value, str):
        residuals.append(("unsupported-project-form", path, span, NON_STRING_REPOSITORY_LOCATION_DETAIL))
        return ""
    return _strip_location(value or "")


def _is_uri_scheme(prefix: str) -> bool:
    """True when `prefix` is an RFC 3986 scheme: `ALPHA *( ALPHA / DIGIT /
    "+" / "-" / "." )`, ASCII only."""
    return (prefix.isascii() and prefix[:1].isalpha()
            and all(c.isalnum() or c in "+-." for c in prefix))


def _lexical_join(base, path_field, budget=None):
    """The twin of `swift_xcode._lexical_join`: with a work budget, the
    joined path's length is charged (L5) before its tuple is built."""
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


def _resolve_project_dir(project_obj, project_repo_dir, budget=None):
    pdp = project_obj.get("projectDirPath") if project_obj else None
    # The twin of `swift_xcode.resolve_project_dir`: a value that is not a
    # string is never resolved (ADR-0130 clause 3).
    if pdp is not None and not isinstance(pdp, str):
        return UNRESOLVED_DIR
    if not pdp:
        return project_repo_dir
    if never_resolved(pdp):
        return UNRESOLVED_DIR
    if pdp.startswith("/"):
        return None
    return _lexical_join(project_repo_dir, pdp, budget)


def _segs_to_posix(segs):
    return "/".join(segs) if segs else "."


def _safe_contained(root, p, budget=None, segments=0):
    """`swift_xcinputs.contained` (the verdict `core._contained` gives, in
    time linear in the path) wrapped against `ValueError`, so no project
    content exits 2 (ADR-0130 clause 2) -- duplicated from `swift_xcode.py` per this
    module's own docstring (small, pure helpers are duplicated rather than
    imported back across the one-way `swift_xcode.py` -> `swift_xcode_products.py`
    boundary). `Path.resolve()` raises `ValueError`, not `OSError`, for a
    path segment carrying an embedded NUL byte (e.g. inside a
    `relativePath`); treated as not-contained, the fail-closed verdict a
    real escape gets. With a work budget, the check charges `l2_units` of
    the path's repo-relative `segments` first (L2)."""
    charge(budget, l2_units(segments))
    try:
        return contained(root, p)
    except ValueError:
        return False


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


def _requirement_text(req, path, span, residuals, collection=None):
    """The rendered `"<kind> <value>"` cell for a `requirement` dict, or
    `None`. Appends an `unsupported-project-form` residual for an
    unrecognised kind or an extra key beyond the kind's expected value
    key(s) (ADR-0130 clause 7). Those two details repeat a name the file
    holds, so each is charged to `collection` before it is built, and past
    the bound it is not recorded (`swift_xcinputs.MAX_DETAIL_BYTES`)."""
    if not isinstance(req, dict):
        if req is not None:
            residuals.append((
                "unsupported-project-form", path, span,
                "package requirement is not a property-list dictionary"))
        return None
    kind = req.get("kind")
    if not isinstance(kind, str):
        # ADR-0130 clause 2, last bullet: a type-confused `kind`
        # (a list or dict in the fixtures) is unhashable and would raise
        # `TypeError` from the dict lookup below -- checked BEFORE that use.
        # A non-string kind renders a closed detail: its value is content,
        # and a Python repr of it would be neither escaped nor stable.
        residuals.append((
            "unsupported-project-form", path, span,
            "package requirement kind is not a string"))
        return "—"
    expected = _REQUIREMENT_VALUE_KEYS.get(kind)
    if expected is None:
        if _admit(collection, path, span, kind):
            residuals.append((
                "unsupported-project-form", path, span,
                f"package requirement kind '{_cell(kind)}' is outside the tested set"))
        return str(kind) if kind else "—"
    known = {"kind"} | set(expected)
    extra = sorted(set(req.keys()) - known)
    if extra and _admit(collection, path, span, extra[0]):
        residuals.append((
            "unsupported-project-form", path, span,
            f"package requirement carries an unrecognised key '{_cell(extra[0])}'"))
    # The value half of the `kind` guard above: a present value that is not
    # a string renders `—` with one closed detail, never a Python repr.
    shapeless = [k for k in expected if k in req and not isinstance(req[k], str)]
    if shapeless:
        residuals.append((
            "unsupported-project-form", path, span,
            "package requirement value is not a string"))

    def _value(key, default):
        v = req.get(key, default)
        return v if isinstance(v, str) else "—"

    if kind == "range":
        lo = _value("minimumVersion", "—")
        hi = _value("maximumVersion", "—")
        return f"{kind} {lo}..<{hi}"
    value = _value(expected[0], None) if expected[0] in req else None
    return f"{kind} {value}" if value else kind


def _reachable_roots(doc, descent):
    """Repo-relative directories the project reaches through a NAMED group
    or synced root -- every `descent.group_dirs` entry except a main group
    that names no path of its own. Such a main group adds no directory: it
    is only the project directory, and treating it as a root would make
    every manifest beside or under the project a candidate (the alternative
    ADR-0130 rejects). The rule follows what the main group names, never
    where the project sits, so a project at the checkout root and one at
    `ios/App.xcodeproj` are treated alike."""
    main_group_id = swift_pbxproj.id_field(doc, doc.root_id, "mainGroup")
    main_group = doc.objects.get(main_group_id) if isinstance(main_group_id, str) else None
    main_path = main_group.get("path") if isinstance(main_group, dict) else None
    main_names_no_path = main_path in (None, "", ".")
    roots = set()
    for oid, segs in descent.group_dirs.items():
        if oid == main_group_id and main_names_no_path:
            continue
        roots.add(_segs_to_posix(segs))
    return roots


def _under_any_root(directory, roots):
    """True when `directory` is one of `roots` or lies under one. The
    caller passes `roots` in sorted order (an ordered dictionary), never a
    set, so the work one call does never depends on the hash seed."""
    if directory in roots:
        return True
    return any(r not in (".", "") and directory.startswith(r + "/") for r in roots)


def _workspace_dirs(workspace_facts, bundle_rel):
    """Package containers an ENCLOSING standalone workspace names (ADR-0130
    clause 7: "those its enclosing standalone workspace names").
    `workspace_facts` is the tuple of `swift_xcinputs.WorkspaceFacts` that
    `swift._workspace_facts_list` builds -- each entry's `.refs` are the
    containers that ONE workspace follows. A workspace "encloses" `bundle_rel`
    when its own refs name that project (an `xcodeproject`-kind ref whose
    `path` equals `bundle_rel`); only THAT workspace's `package`-kind refs
    become candidates -- a sibling workspace that never names this project
    contributes nothing, so an unrelated workspace's packages can never leak
    into this project's unlinked-product resolution. `None` or an empty
    tuple (workspace wiring absent) contributes nothing."""
    dirs: set = set()
    for wf in workspace_facts or ():
        if not any(r.kind == "xcodeproject" and r.path == bundle_rel for r in wf.refs):
            continue
        dirs.update(r.path for r in wf.refs if r.kind == "package")
    return dirs


def _library_products(mf):
    return {p["name"] for p in mf.products if p.get("kind") == "library"}


def _project_local_ref_dirs(root, doc, project_dir, budget=None):
    """Every contained directory a project-level `XCLocalSwiftPackageReference`
    names (ADR-0130 clause 7, "those its local references name"), regardless
    of whether a product dependency actually uses it. Each join and each
    containment check charges `budget` (L5, L2)."""
    dirs = set()
    if project_dir is None or project_dir is UNRESOLVED_DIR:
        return dirs
    for obj in doc.objects.values():
        if swift_pbxproj.isa_of(obj) != _LOCAL_REF_ISA:
            continue
        rel = obj.get("relativePath")
        # An empty `relativePath` names the project directory, as `.` does.
        if not isinstance(rel, str) or rel.startswith("/") or never_resolved(rel):
            continue
        joined = _lexical_join(project_dir, rel, budget)
        if joined is None or swift_prune.pruned_path(joined):
            continue
        candidate = root.joinpath(*joined) if joined else root
        if not _safe_contained(root, candidate, budget, len(joined)):
            continue
        dirs.add(_segs_to_posix(joined))
    return dirs


#: The closed `unresolved-reference` detail for a contained local package
#: reference with a symlinked component (ADR-0130 clause 3). It names no
#: path: the symlink's target can lie in a directory ADR-0129 clause 8 names
#: nothing inside.
SYMLINKED_LOCAL_REFERENCE_DETAIL = ("a local package reference names a symlinked directory; "
                                    "it is not descended and no edge is drawn")


def _local_reference_verdict(root, project_dir, pkg_obj, verdicts=None, budget=None):
    """What one `XCLocalSwiftPackageReference` resolves to, independent of
    the product that names it: `("silent",)`, `("no-path",)`, `("escape",)`,
    `("pruned",)`, `("symlink",)` or `("container", container_rel, is_pkg)`.
    The checks run in ADR-0130 clause 3's order: every lexical refusal
    before any filesystem access. The join and each path check charge
    `budget` (L5, L2)."""
    rel = pkg_obj.get("relativePath")
    if rel is not None and (not isinstance(rel, str) or never_resolved(rel)):
        # ADR-0130 clause 3, which clause 7 applies to `relativePath`: a
        # build-setting reference is never resolved, and renders
        # `unresolved-reference` only in a Sources phase. A package reference
        # is in no Sources phase, so it renders nothing: no residual, no row
        # and no edge. A non-string value (a list or a dict, empty or not) is
        # classified the same way; its truthiness never routes it to
        # `path-escape`.
        return ("silent",)
    if project_dir is UNRESOLVED_DIR:
        # The project's `projectDirPath` is not a string or holds a
        # build-setting reference (`UNRESOLVED_DIR`), so no reference under
        # it resolves. It is in no Sources phase: nothing
        # renders (ADR-0130 clause 3).
        return ("silent",)
    if rel is None:
        # The reference names no path at all, so it cannot escape.
        return ("no-path",)
    # An empty `relativePath` names the project directory, exactly as `.`
    # does: `_lexical_join` below returns the project directory for both.
    if project_dir is None or rel.startswith("/"):
        return ("escape",)
    joined = _lexical_join(project_dir, rel, budget)
    if joined is None:
        return ("escape",)
    if swift_prune.pruned_path(joined):
        # ADR-0129 clause 8, refused by name before any filesystem access.
        return ("pruned",)
    candidate_dir = root.joinpath(*joined) if joined else root
    if not _safe_contained(root, candidate_dir, budget, len(joined)):
        return ("escape",)
    if has_symlink_component(root, joined, verdicts, budget):
        # ADR-0130 clause 3: no directory symlink is descended. The escape
        # check above keeps precedence for a symlink that leaves the checkout.
        return ("symlink",)
    container_rel = _segs_to_posix(joined)
    try:
        # ADR-0130 clause 3: `follow_symlinks=False`
        # so a `Package.swift` reached only through a symlink is refused,
        # the same treatment every sibling membership guard in
        # `swift_xcode.py` gives a matched filesystem entry.
        # Matched byte for byte against each listing as well (ADR-0130
        # clause 3), so a case-folding host cannot find `Vendor/Core` through
        # `vendor/core` where a case-sensitive host finds nothing.
        is_pkg = (listed_names(root, joined + ("Package.swift",), verdicts, budget)
                  and (candidate_dir / "Package.swift").is_file(follow_symlinks=False))
    except (OSError, ValueError):
        is_pkg = False
    return ("container", container_rel, is_pkg)


def _resolve_local_reference(root, doc, project_dir, pkg_obj, package_id, product_name,
                              target_name, pbxproj_rel, dep_loc, manifests_by_dir, residuals,
                              verdicts=None, collection=None, memo=None):
    """One `(product_deps row | None)` for a product dependency whose
    `package` names an `XCLocalSwiftPackageReference` (ADR-0130 clauses 3, 7).
    Every refusal returns `None`. Most refusals also append one residual.
    Three refusals append nothing, because a package reference sits in no
    Sources phase (ADR-0130 clause 3): a `relativePath` that is not a
    string, a `relativePath` that `never_resolved` matches (`$(`, `${` or
    `$` then a letter or `_`), and a project whose `projectDirPath` is
    `UNRESOLVED_DIR` (not a string, or one `never_resolved` matches). An
    escaping `projectDirPath` renders `path-escape`. The missing-`Package.swift` and
    no-such-product refusals repeat the container name in their detail, so
    each appends its residual only while `collection` admits the detail.
    Past that bound it appends nothing, and the budget's one `scan-cap` line
    marks where details stop.

    `memo` (one dict per project) keeps each reference's verdict
    (`_local_reference_verdict`), keyed by `package_id`, so every product
    naming the reference shares one container string and the filesystem is
    read once. A detail that repeats the container is charged to
    `collection` before it is built. The first read charges the work its
    path checks spend, and a reuse charges 1 plus the units that read spent
    outside any listing or reuse (L6)."""
    ref_span = doc.object_spans.get(package_id)
    if memo is not None and package_id in memo:
        verdict, units = memo[package_id]
        charge_excluded(collection, 1 + units)
    else:
        before = (collection.work_spent - collection.work_excluded) if collection is not None else 0
        verdict = _local_reference_verdict(root, project_dir, pkg_obj, verdicts, collection)
        if memo is not None:
            memo[package_id] = (verdict, (collection.work_spent - collection.work_excluded - before)
                                if collection is not None else 0)
    kind = verdict[0]
    if kind == "silent":
        return None
    # The no-path and escape details repeat the product name, so each is
    # charged before it is built (`swift_xcinputs.MAX_DETAIL_BYTES`).
    if kind == "no-path":
        if _admit(collection, pbxproj_rel, ref_span, product_name):
            residuals.append((
                "unresolved-reference", pbxproj_rel, ref_span,
                f"local package reference for product '{_cell(product_name)}' carries no relativePath"))
        return None
    if kind == "escape":
        if _admit(collection, pbxproj_rel, ref_span, product_name):
            residuals.append((
                "path-escape", pbxproj_rel, ref_span,
                f"local package reference for product '{_cell(product_name)}' names a path outside the checkout"))
        return None
    if kind == "pruned":
        residuals.append(("unresolved-reference", pbxproj_rel, ref_span,
                          swift_prune.LOCAL_REFERENCE_DETAIL))
        return None
    if kind == "symlink":
        residuals.append(("unresolved-reference", pbxproj_rel, ref_span,
                          SYMLINKED_LOCAL_REFERENCE_DETAIL))
        return None
    _kind, container_rel, is_pkg = verdict
    if not is_pkg:
        if _admit(collection, pbxproj_rel, ref_span, container_rel):
            residuals.append((
                "missing-input", pbxproj_rel, ref_span,
                f"local package reference '{_cell(container_rel)}' holds no Package.swift"))
        return None
    mf = manifests_by_dir.get(container_rel)
    match = next((prod for prod in (mf.products if mf else []) if prod["name"] == product_name), None)
    if match is None:
        if _admit(collection, pbxproj_rel, dep_loc, container_rel, product_name):
            residuals.append((
                "unresolved-reference", pbxproj_rel, dep_loc,
                f"local package reference '{_cell(container_rel)}' declares no product '{_cell(product_name)}'"))
        return None
    if match.get("kind") == "plugin":
        return {
            "from": target_name, "container": None, "kind": "plugin product",
            "product": product_name, "location": container_rel, "requirement": None,
            "path": pbxproj_rel, "loc": dep_loc, "edge_to": None,
        }
    return {
        "from": target_name, "container": None, "kind": "local product",
        "product": product_name, "location": container_rel, "requirement": None,
        "path": pbxproj_rel, "loc": dep_loc, "edge_to": (mf.path, product_name),
    }


def _read_remote(ref_id, ref_obj, span, pbxproj_rel, memo, collection):
    """`(location, requirement, residual lines)` of one
    `XCRemoteSwiftPackageReference`, read once per project and kept in `memo`
    by object id. A `packageReferences` list naming one reference N times, or
    N product dependencies naming it, share one location string and one
    requirement cell instead of building N copies (ADR-0129 clause 2: no
    content makes the derive run out of memory)."""
    found = memo.get(ref_id)
    if found is None:
        read: list = []
        location = _repository_location(ref_obj.get("repositoryURL"), pbxproj_rel, span, read)
        requirement = _requirement_text(ref_obj.get("requirement"), pbxproj_rel, span, read, collection)
        found = memo[ref_id] = (location, requirement, tuple(read))
    return found


def _admit(collection, path, span, *parts):
    """Charge the residual detail about to repeat `parts` to the caller's
    `swift_xcinputs.CollectionBudget`, and return True when it fits. From
    the first refusal on it returns False. Always True without a budget."""
    return collection is None or collection.detail(path, span, *parts)


def resolve_product_dependencies(root, projects, manifests, workspace_facts=None, verdicts=None,
                                 collection=None):
    """`projects` -- the parsed projects `swift_xcode.read_projects` already
    built (each exposing `.bundle_rel`, `.pbxproj_rel`, `.doc`, `.descent`,
    `.targets_by_id`). Returns `(product_deps, dependencies, residuals)`,
    each a plain list matching `swift.ProjectFacts`'s field shapes. `loc`
    values are RAW (a bare line int, a `(start, end)` tuple, or `None`) --
    the caller normalises them through its own `_membership_loc`/
    `_residual_ranges`, the same treatment every other `read_projects` row
    gets. `verdicts` is the caller's `swift_xcinputs.DirectoryVerdicts`
    memo, passed to the local-reference path checks (ADR-0130 clauses 2
    and 3);
    `None` reads the live filesystem.

    `collection` is the caller's `swift_xcinputs.CollectionBudget`: every
    residual detail that repeats a target name, a product name, a package
    directory, a requirement name or the candidate list is charged to it
    before it is built. The `packageReferences` rows and the product rows
    read each package reference once per project (`remote_memo`,
    `local_memo`) and share what it resolves to: its location and
    requirement, or its local container.

    The same budget bounds this pass's work (`MAX_WORK_UNITS`): each project
    read charges 1 unit first, each product dependency 1, each (manifest, root)
    test of the candidate search 1, and each candidate listed in a detail 1
    (L3); the joins and path checks charge theirs. When the bound refuses,
    the rows and lines read before stand, the refused dependency adds none,
    and no later project is read."""
    product_deps: list = []
    dependencies: list = []
    residuals: list = []

    manifests_by_dir = {mf.directory: mf for mf in manifests}

    for p in projects:
        # Buffered per project and committed only when the whole project
        # resolved, so a failure part-way leaves only its `malformed` line.
        p_product_deps: list = []
        p_dependencies: list = []
        # A reference D product dependencies name re-appends its lines once
        # per product: the sink keeps one per render key, and every
        # distinct line still renders (ADR-0129 clause 7).
        p_residuals = ResidualSink()
        try:
            charge(collection, 1)
            doc = p.doc
            project_obj = doc.objects.get(doc.root_id, {}) or {}
            parent = () if "/" not in p.bundle_rel else tuple(p.bundle_rel.rsplit("/", 1)[0].split("/"))
            project_dir = _resolve_project_dir(project_obj, parent, collection)
            # Each remote reference is read once per project, by the
            # `packageReferences` rows and the product rows alike, and what
            # it resolves to is shared: its location, its requirement and
            # its residual lines.
            remote_memo: dict = {}

            for ref_id in swift_pbxproj.list_field(doc, doc.root_id, "packageReferences",
                                                   p.pbxproj_rel, p_residuals):
                # ADR-0130 clause 2: an item that is not a string, or a
                # string naming no object, is not an object id. An item
                # naming a local package reference, or any other kind of
                # object, renders nothing here.
                ref_obj = doc.objects.get(ref_id) if isinstance(ref_id, str) else None
                if ref_obj is None:
                    p_residuals.append((
                        "unresolved-reference", p.pbxproj_rel,
                        _list_item_line(doc, doc.root_id, "packageReferences", ref_id),
                        NON_STRING_PACKAGE_REFERENCE_ITEM_DETAIL))
                    continue
                if swift_pbxproj.isa_of(ref_obj) != _REMOTE_REF_ISA:
                    continue
                span = doc.object_spans.get(ref_id)
                location, requirement, read = _read_remote(ref_id, ref_obj, span, p.pbxproj_rel, remote_memo,
                                                           collection)
                p_residuals.extend(read)
                p_dependencies.append({
                    "location": location, "requirement": requirement,
                    "path": p.pbxproj_rel, "loc": span,
                })

            roots = _reachable_roots(doc, p.descent)
            local_ref_dirs = _project_local_ref_dirs(root, doc, project_dir, collection)
            workspace_dirs = _workspace_dirs(workspace_facts, p.bundle_rel)
            all_roots = roots | local_ref_dirs | workspace_dirs
            # Sorted once here, so each manifest's walk runs in one order.
            all_roots = dict.fromkeys(sorted(all_roots))
            candidates = []
            for mf in manifests:
                # One unit per (manifest, root) the search may test.
                charge(collection, len(all_roots))
                if _under_any_root(mf.directory, all_roots):
                    candidates.append(mf)
            # Library product name -> the candidates declaring it, in
            # candidate order: one pass, not one per product dependency.
            by_library: dict = {}
            for mf in candidates:
                for name in sorted(_library_products(mf)):
                    by_library.setdefault(name, []).append(mf)
            # The rendered candidate list, built once per distinct match set.
            listed: dict = {}
            local_memo: dict = {}
            # A product dependency two targets name renders its
            # non-string `productName` line once.
            seen_fields: set = set()

            for tid, row in p.targets_by_id.items():
                target_name = row["name"]
                for dep_id in swift_pbxproj.list_field(doc, tid, "packageProductDependencies",
                                                       p.pbxproj_rel, p_residuals):
                    charge(collection, 1)
                    dep_loc = _list_item_line(doc, tid, "packageProductDependencies", dep_id)
                    if not isinstance(dep_id, str):
                        if _admit(collection, p.pbxproj_rel, dep_loc, target_name):
                            p_residuals.append((
                                "unresolved-reference", p.pbxproj_rel, dep_loc,
                                f"target '{_cell(target_name)}' names a dangling package product dependency"))
                        continue
                    dep_obj = doc.objects.get(dep_id)
                    if swift_pbxproj.isa_of(dep_obj) != _PRODUCT_DEP_ISA:
                        if _admit(collection, p.pbxproj_rel, dep_loc, target_name):
                            p_residuals.append((
                                "unresolved-reference", p.pbxproj_rel, dep_loc,
                                f"target '{_cell(target_name)}' names a dangling package product dependency"))
                        continue
                    # ADR-0130 clauses 2 and 14: a `productName` that is not a
                    # string renders `—` and one line; a `package` that is
                    # not a string is a dangling id.
                    product_name = swift_pbxproj.string_field(
                        doc, dep_id, "productName", p.pbxproj_rel, p_residuals, seen_fields)
                    if not product_name:
                        product_name = "—"
                    package_id = swift_pbxproj.id_field(doc, dep_id, "package")

                    if package_id is not None:
                        pkg_obj = doc.objects.get(package_id) if isinstance(package_id, str) else None
                        if not isinstance(pkg_obj, dict):
                            if _admit(collection, p.pbxproj_rel, dep_loc, target_name, product_name):
                                p_residuals.append((
                                    "unresolved-reference", p.pbxproj_rel, dep_loc,
                                    f"target '{_cell(target_name)}' names a dangling package reference "
                                    f"for product '{_cell(product_name)}'"))
                            continue
                        isa = swift_pbxproj.isa_of(pkg_obj)
                        if isa == _REMOTE_REF_ISA:
                            # Read once per reference; a later product naming
                            # it re-appends the same residual tuples, so the
                            # lines are unchanged and no text is rebuilt.
                            location, requirement, read = _read_remote(
                                package_id, pkg_obj, doc.object_spans.get(package_id), p.pbxproj_rel,
                                remote_memo, collection)
                            p_residuals.extend(read)
                            p_product_deps.append({
                                "from": target_name, "container": p.bundle_rel, "kind": "remote product",
                                "product": product_name, "location": location, "requirement": requirement,
                                "path": p.pbxproj_rel, "loc": dep_loc, "edge_to": None,
                            })
                            continue
                        if isa == _LOCAL_REF_ISA:
                            result = _resolve_local_reference(
                                root, doc, project_dir, pkg_obj, package_id, product_name,
                                target_name, p.pbxproj_rel, dep_loc, manifests_by_dir, p_residuals,
                                verdicts=verdicts, collection=collection, memo=local_memo)
                            if result is not None:
                                result["container"] = p.bundle_rel
                                p_product_deps.append(result)
                            continue
                        if _admit(collection, p.pbxproj_rel, dep_loc, target_name):
                            p_residuals.append((
                                "unresolved-reference", p.pbxproj_rel, dep_loc,
                                f"target '{_cell(target_name)}' names a package product dependency whose package "
                                "reference is not supported"))
                        continue

                    # Unlinked: no `package` field. Candidates are the containers
                    # the project reaches, matched against literal LIBRARY
                    # products byte for byte (ADR-0130 clause 7).
                    matches = by_library.get(product_name, [])
                    if len(matches) == 1:
                        mf = matches[0]
                        p_product_deps.append({
                            "from": target_name, "container": p.bundle_rel, "kind": "unlinked product",
                            "product": product_name, "location": None, "requirement": None,
                            "path": p.pbxproj_rel, "loc": dep_loc, "edge_to": (mf.path, product_name),
                        })
                    else:
                        # One list per distinct match set, shared by every
                        # product that renders it; each detail that repeats
                        # it is charged first.
                        key = product_name if matches else None
                        if key not in listed:
                            charge(collection, len(matches) if matches else len(candidates))
                            dirs = sorted(mf.directory for mf in (matches if matches else candidates))
                            listed[key] = f": {', '.join(_cell(d) for d in dirs)}" if dirs else ""
                        suffix = listed[key]
                        if _admit(collection, p.pbxproj_rel, dep_loc, product_name, target_name, suffix):
                            p_residuals.append((
                                "unresolved-reference", p.pbxproj_rel, dep_loc,
                                f"package product '{_cell(product_name)}' of target '{_cell(target_name)}' resolves to "
                                f"{len(matches)} candidate containers" + suffix))
            product_deps.extend(p_product_deps)
            dependencies.extend(p_dependencies)
            residuals.extend(p_residuals)
        except WorkBoundExceeded:
            # Past the work bound (`MAX_WORK_UNITS`) the rows and lines read
            # before the refusal stand, and no later project is read.
            product_deps.extend(p_product_deps)
            dependencies.extend(p_dependencies)
            residuals.extend(p_residuals)
            break
        except Exception:
            # ADR-0130 clause 2, last bullet: the per-project
            # boundary in `swift_xcode.py::read_projects` covers the first
            # parse loop and its own target-dependency pass, but this
            # module's package-product resolution runs once for every
            # parsed project with NO boundary of its own. One bad value
            # renders one residual for THIS project only, and resolution
            # continues with the next.
            residuals.append(("project-unreadable", p.pbxproj_rel, None, "malformed"))
            continue

    return product_deps, dependencies, residuals
