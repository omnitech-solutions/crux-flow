"""swift_prune.py — the one prune rule every Swift walk shares (ADR-0129 clause 8).

ADR-0129 clause 8: "Every Swift walk and the Swift container discovery prune
this set, together with the core `_SKIP_DIRS` and the dot-directories. ...
Nothing inside it is read, hashed or named." That clause governs the
folder-synced walk ADR-0130 clause 9 adds, so "every file under that root"
means every file the Swift walk reaches. The Swift source walk in `swift.py`,
the folder-synced walk and every project reference in `swift_xcode.py` and
`swift_xcode_products.py`, and the workspace reader in `swift_xcinputs.py`
read their prune rule from here, so no two of them can disagree.

The rule reads repo-relative path components only, never an absolute path,
so a checkout that sits under a directory named `Pods` still derives. A
directory the rule prunes is:

* a core `_SKIP_DIRS` name (`build`, `node_modules`, `.git` and the rest);
* any name with a leading dot (`.build`, `.swiftpm`);
* a `DETECT_EXCLUDE` entry: the four Swift-local names and the two Xcode
  container suffixes (`*.xcodeproj`, `*.xcworkspace`), whose contents the
  walk lists without descending.

Imports only crux's own core module.
"""

from __future__ import annotations

from ..core import _SKIP_DIRS, _dir_excluded

#: Directory names and `*`-suffix patterns the core's marker scan and every
#: Swift walk never descend into: three vendored-dependency directories, one
#: Xcode build-output directory, and the two Xcode bundle suffixes. The core
#: reads it as `swift.DETECT_EXCLUDE`, which is this same object.
DETECT_EXCLUDE: frozenset[str] = frozenset(
    {"Pods", "Carthage", "DerivedData", "SourcePackages", "*.xcodeproj", "*.xcworkspace"}
)


#: The closed `unresolved-reference` details for a reference into a pruned
#: directory. None names the path: clause 8 names nothing inside the set.
SYNCED_ROOT_DETAIL = "a folder-synced root lies in a directory the Swift walk skips; nothing under it is read"
EXCEPTION_ENTRY_DETAIL = "an exception entry names a path in a directory the Swift walk skips; it is not read"
SOURCES_MEMBER_DETAIL = ("a Sources-phase file reference names a path in a directory the Swift walk "
                         "skips; it is not read")
LOCAL_REFERENCE_DETAIL = "a local package reference names a directory the Swift walk skips; no edge is drawn"
PATH_DEPENDENCY_DETAIL = ("a `.package(path:)` reference names a directory the Swift walk skips; "
                          "no dependency row renders")
XCCONFIG_INCLUDE_DETAIL = ("an xcconfig include names a path in a directory the Swift walk skips; "
                           "it is not read")


def skipped_name(name: str) -> bool:
    """True when `name` is a core `_SKIP_DIRS` name or starts with a dot. The
    Swift source walk drops such a directory without listing it."""
    return name in _SKIP_DIRS or name.startswith(".")


def excluded_name(name: str) -> bool:
    """True when `name` matches a `DETECT_EXCLUDE` entry by name or suffix.
    The Swift source walk lists such a directory without descending it."""
    return _dir_excluded(name, DETECT_EXCLUDE)


def pruned_name(name: str) -> bool:
    """True when a directory named `name` is outside every Swift walk."""
    return skipped_name(name) or excluded_name(name)


#: The Xcode container suffixes `DETECT_EXCLUDE` names (`.xcodeproj`,
#: `.xcworkspace`). The walk lists such a container without descending it, so
#: it is a container the walk reaches. Not `swift_xcode.BUNDLE_SUFFIXES`,
#: which names the resource and product bundles a folder-synced walk stops at.
_XCODE_CONTAINER_SUFFIXES = tuple(e[1:] for e in DETECT_EXCLUDE if e.startswith("*"))


def pruned_path(segments, *, container: bool = False) -> bool:
    """True when any repo-relative component of `segments` is pruned. A file
    or directory at such a path is never read, hashed or named.

    With `container`, the final component may be an Xcode container
    (`App.xcodeproj`): the path then names the container itself, which the
    walk reaches, and not a path inside it. A workspace reference names a
    container this way."""
    segs = tuple(segments)
    last = len(segs) - 1
    for i, s in enumerate(segs):
        if container and i == last and s.endswith(_XCODE_CONTAINER_SUFFIXES) and not skipped_name(s):
            continue
        if pruned_name(s):
            return True
    return False
