#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = ["griffelib==2.3.0"]
# ///
"""Extract documentation from source code per docs/manifest.yml.

Dispatcher: reads manifest.yml, loads per-language extractor plugins from
./extractors/, runs them, writes results under docs/code/.

Each plugin module under scripts/extractors/<name>.py exposes either the
legacy API (`EXTRACTOR_API` unset or `1`):

    def discover(repo_root: Path, config: dict) -> list[SourceUnit]: ...
    def extract(unit: SourceUnit) -> DocPage: ...

or API 2 (`EXTRACTOR_API = 2`), per ADR-0131 clause 13:

    def discover(repo_root: Path, config: dict, ctx: ExtractContext) -> list[SourceUnit]: ...
    def extract(unit: SourceUnit, ctx: ExtractContext) -> DocPage: ...
    def provenance(config: dict) -> dict: ...

The dispatcher:
  1. Parses manifest.yml at --config (default: docs/manifest.yml).
  2. For each enabled language under code.extractors, imports the matching
     module from scripts/extractors/<extractor-name>.py.
  3. Calls discover() then extract() on each unit.
  4. Writes pages to <output-dir>/<lang-namespace>/<unit>.md.
  5. Writes a fresh <output-dir>/_meta/manifest.json.

Determinism: alphabetical ordering everywhere; no timestamps in page body
(only in _meta/manifest.json).

Regenerative invariant: any pre-existing content under <output-dir> that is
NOT listed in the newly-built manifest is removed. Hand-edits never survive.
A file reachable only through a symlink is reported and never pruned — a
link's target is never this dispatcher's to delete (ADR-0131 clause 14 D1).

--dry-run renders every expected byte in memory and diffs it against disk
(ADR-0131 clause 14 D4). It writes nothing and exits 1 on drift, 0 clean.

Every content refusal (ADR-0131 clause 15) exits 1, names the path and the
cause, and leaves every output byte unchanged: on stderr outside --dry-run,
as a `validation_errors` JSON payload on stdout under --dry-run. A missing
capability (ADR-0131 clause 5, wired through `_check_capabilities`) exits 2.

An output root that holds a regular `*.md` file refuses before any write
unless its `_meta/manifest.json` proves this dispatcher generated it
(rule:code-doc-output-root-pruned-only-when-owned). An extractor name must
name a regular `<name>.py` shipped in `extractors/`, checked before any import
(rule:code-doc-extractor-name-is-a-shipped-module).
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

EXTRACTOR_VERSION = "1"

# ADR-0131 clause 5. The PEP 723 block above is the one declaration of the pin;
# this constant is what the in-process capability check compares against, and
# `GriffePinLockStepTests` (tools/tests/test_schema_invariants.py) holds it equal
# to the block, to pyproject.toml, to uv.lock and to the release-gate installs.
GRIFFE_VERSION = "2.3.0"
GRIFFE_REQUIREMENT = f"griffelib=={GRIFFE_VERSION}"

# The interpreter floor the block's `requires-python` declares. The canonical
# `uv run --no-config` invocation satisfies it before the script starts; this
# copy is the defence for a bare `python3` invocation.
PYTHON_FLOOR = (3, 13)


# ────────────────────────── shared data contracts ──────────────────────────


@dataclass
class SourceUnit:
    """One documentable unit discovered by an extractor.

    Fields:
        language: the extractor language key (e.g. "elixir").
        identifier: the unit name (e.g. "MyApp.MyModule" or "lib/foo/bar.ex").
                    Determines the output filename via path-safe transformation.
        source_path: relative path (from repo root) to the file backing this
                     unit, AS SELECTED (never the resolved spelling). Used for
                     the manifest provenance row.
        payload: arbitrary extractor-internal data carried into extract().
                 Not persisted; opaque to the dispatcher.
    """

    language: str
    identifier: str
    source_path: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class DocPage:
    """One rendered documentation page emitted by an extractor.

    Fields:
        title: the page's H1 (and the page's logical name).
        path: output path RELATIVE to --output-dir. The dispatcher writes the
              file at <output-dir>/<path>. Must end in .md.
        body: full markdown body. The dispatcher writes it verbatim; do NOT
              embed timestamps or other non-deterministic content here.
        source_path: relative path (from repo root) to the underlying source.
                     Carried into _meta/manifest.json.
        meta: extra metadata-row fields an extractor contributes (ADR-0131
              clause 13), merged into the row after `owner`,
              `source_path`, `doc_path`, `sha256`, `extractor_version`. A
              legacy extractor leaves this empty, so its row gains exactly
              `owner`.
    """

    title: str
    path: str
    body: str
    source_path: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractContext:
    """Per-run context handed to an API-2 extractor (ADR-0131 clause 1).

    Fields are resolved paths; `lang_key` is the configured manifest key
    (never the extractor module name, which can differ via `extractor:`).
    """

    repo_root: Path
    output_root: Path
    lang_key: str


class ExtractionRefusal(Exception):
    """A content-caused refusal (ADR-0131 clause 15), never a traceback.

    Constructed as `ExtractionRefusal(path, cause)`. Every refusal in the
    dispatcher's clause-15 list is reported through `_report_refusals`
    (ADR-0131 clause 15): stderr-only outside --dry-run, a `validation_errors` JSON
    payload on stdout under --dry-run. Existing non-content failures (config
    not found, --lang key not found, .crux/.bionic configuration error) are
    unaffected and keep their own inline reporting.
    """

    def __init__(self, path: str, cause: str) -> None:
        super().__init__(f"{path}: {cause}")
        self.path = path
        self.cause = cause


# ─────────────────────────────── argparse ──────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="extract-code-docs",
        description=(
            "Regenerate docs/code/ from in-source documentation. "
            "Reads docs/manifest.yml to decide which language extractors to run."
        ),
    )
    parser.add_argument(
        "--config",
        default=None,
        type=Path,
        help="Path to the docs manifest (default: <docs_dir>/manifest.yml, "
             "with docs_dir from the repo-root .crux per ADR-0032). An explicit "
             "path also supplies the default --output-dir, so one manifest owns "
             "both the sources scanned and the pages written.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        type=Path,
        help="Output directory (default: the code/ dir of the tree that owns "
             "--config -- i.e. <docs_dir>/code from the repo-root .crux when "
             "--config is not given, and <config's own dir>/code when it is).",
    )
    parser.add_argument(
        "--lang",
        default=None,
        help="Filter to one extractor by language key. Default: run all enabled. "
             "A --lang run writes or deletes only the selected key's pages, "
             "rows and provenance; every other owner's bytes are preserved.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render expected pages/index/metadata in memory and compare their "
             "bytes against disk. Writes nothing. Exit 1 on drift.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose logging to stderr.",
    )
    return parser.parse_args(argv)


# ─────────────────────── repo-root .crux config loader ────────────────────


def _load_crux_config():
    """Import the sibling crux_config module (hyphen-free, but scripts/ may not
    be on sys.path) and resolve the repo-root .crux per ADR-0032."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "crux_config", Path(__file__).resolve().parent / "crux_config.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    global CruxConfigError
    CruxConfigError = mod.CruxConfigError
    return mod.load_config()


class CruxConfigError(Exception):
    # Placeholder so `except CruxConfigError` resolves at module scope before
    # crux_config is loaded. _load_crux_config rebinds this global to the real
    # class BEFORE calling load_config(), and except-clause types are evaluated
    # at raise time — so the except always catches the real class. Do not
    # "clean up" the global rebind without restructuring the lazy load.
    pass


# ─────────────────────────── minimal YAML loader ──────────────────────────


def load_yaml(path: Path) -> dict:
    """Load manifest.yml. Uses PyYAML when available; otherwise a minimal
    hand-rolled parser sufficient for crux's flat-ish manifest format.
    """
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    except ImportError:
        return _parse_minimal_yaml(text)


def _parse_minimal_yaml(text: str) -> dict:
    """Tiny YAML subset: nested mappings via 2-space indent, scalars, and flow
    lists [a, b]. Enough for docs/manifest.yml. Comments stripped. No anchors,
    no multi-line strings, no block-style lists.
    """
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for raw in text.splitlines():
        # Strip trailing comments unless quoted.
        line = _strip_yaml_comment(raw).rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        if ":" not in content:
            continue
        key, _, value = content.partition(":")
        key = key.strip()
        value = value.strip()
        # Pop stack until current indent fits.
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else root
        if value == "":
            new: dict = {}
            if isinstance(parent, dict):
                parent[key] = new
            stack.append((indent, new))
        else:
            parsed = _parse_scalar(value)
            if isinstance(parent, dict):
                parent[key] = parsed
    return root


def _strip_yaml_comment(line: str) -> str:
    out = []
    in_str: str | None = None
    for ch in line:
        if in_str:
            out.append(ch)
            if ch == in_str:
                in_str = None
            continue
        if ch in ('"', "'"):
            in_str = ch
            out.append(ch)
            continue
        if ch == "#":
            break
        out.append(ch)
    return "".join(out)


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(p.strip()) for p in inner.split(",")]
    if value.lower() in ("null", "~", ""):
        return None
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    try:
        if "." not in value:
            return int(value)
        return float(value)
    except ValueError:
        return value


# ─────────────────────────── plugin discovery ─────────────────────────────


def _extractors_dir() -> Path:
    return Path(__file__).resolve().parent / "extractors"


# An extractor name is target content: it comes from the target repository's
# own manifest. It is a closed identifier naming a shipped module, never a
# path; a leading underscore marks a helper module no name can select.
EXTRACTOR_NAME_RE = re.compile(r"[a-z][a-z0-9_]*")


_LABEL_LIMIT = 80


def _bounded_label(text: str) -> str:
    """`text` cut to `_LABEL_LIMIT` characters plus `...` when longer, so a
    huge manifest value cannot flood stderr or the JSON payload."""
    return text if len(text) <= _LABEL_LIMIT else text[:_LABEL_LIMIT] + "..."


def resolve_extractor_path(name: Any) -> Path:
    """The shipped extractor file `name` selects, or an `ExtractionRefusal`.

    `name` must be a string matching `EXTRACTOR_NAME_RE` as a whole, and
    `<name>.py`, spelled exactly, must be a regular file and not a link
    directly inside the extractors directory located from this script's own
    installation. Nothing is imported here.
    """
    label = _sanitize_refusal_text(_bounded_label(repr(name)))
    if not isinstance(name, str) or EXTRACTOR_NAME_RE.fullmatch(name) is None:
        raise ExtractionRefusal(
            label,
            "is not a shipped extractor name (a lowercase identifier matching "
            "^[a-z][a-z0-9_]*$); nothing was imported or written",
        )
    directory = _extractors_dir()
    filename = f"{name}.py"
    try:
        present = filename in os.listdir(directory)
        st = os.lstat(directory / filename) if present else None
    except OSError:
        present, st = False, None
    if st is None or not stat.S_ISREG(st.st_mode):
        raise ExtractionRefusal(
            label,
            "names no shipped extractor module (no regular, unlinked "
            f"{filename} in the plugin's extractors directory); nothing was "
            "imported or written",
        )
    return directory / filename


def load_extractor_module(name: str):
    """Import the shipped extractor `name` selects, and no other file."""
    path = resolve_extractor_path(name)
    spec = importlib.util.spec_from_file_location(f"docs_suite_extractor_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load spec for {path}")
    module = importlib.util.module_from_spec(spec)
    # Make `from extractors.X import Y` style work by exposing the dispatcher
    # module so the plugin can reach SourceUnit/DocPage.
    sys.modules.setdefault("extract_code_docs_dispatcher", sys.modules[__name__])
    spec.loader.exec_module(module)
    return module


# ──────────────────── source containment helper (ADR-0131 clause 14 D6) ─────────────


def _is_inside(resolved: Path, repo_root_resolved: Path) -> bool:
    # `Path.is_relative_to` is stdlib since 3.9; PYTHON_FLOOR is 3.13, so no
    # pre-3.9 fallback branch is reachable and none is kept.
    return resolved.is_relative_to(repo_root_resolved)


def resolve_source(repo_root: Path, path: Path) -> Path:
    """Resolve `path` (repo-relative, as an extractor selected it) and refuse
    (ExtractionRefusal) when it does not resolve inside the resolved repo
    root, or is not a regular file (ADR-0131 clause 14 D6).

    `path` is never re-recorded from the return value — the caller's own
    `source_path` stays the spelling it selected.
    """
    repo_root_r = repo_root.resolve()
    full = path if path.is_absolute() else repo_root_r / path
    try:
        resolved = full.resolve(strict=True)
    except OSError as exc:
        raise ExtractionRefusal(str(path), f"cannot resolve source ({exc})") from exc
    if not _is_inside(resolved, repo_root_r):
        raise ExtractionRefusal(
            str(path), f"resolves outside the repository root ({resolved})"
        )
    if not resolved.is_file():
        raise ExtractionRefusal(str(path), "is not a regular file")
    return resolved


def read_source_bytes(repo_root: Path, resolved: Path, max_bytes: int | None = None) -> bytes:
    """Open the RESOLVED path with O_NOFOLLOW (where available) and
    fstat-check S_ISREG on the open descriptor, refusing when the source is
    no longer a regular file (ADR-0131 clause 14 D6). The live check here is the
    fstat alone: `resolved` is the same value `resolve_source` already
    tested against the repo root, so comparing it again would test nothing
    new. O_NOFOLLOW plus this fstat NARROW the window between
    `resolve_source` and this read -- a symlink planted at the leaf in
    between is refused by O_NOFOLLOW, and a regular file replaced by a
    directory or special file in between is caught by the fstat -- but do
    not CLOSE it: a symlink swapped for a same-named regular file after
    O_NOFOLLOW's own check still opens (the repo_root parameter is kept for
    that reason, in case a future caller wants a real re-resolve).

    `max_bytes`, when given, refuses a source whose fstat-reported size
    exceeds it (ADR-0131 clause 7's Python-extractor bound), before the read
    rather than after it -- the same refusal text `_py_load._check_size`
    already produces from `len(raw)`, since a regular file's fstat size and
    its read length agree. Left at the default (None) elixir and fallback
    both call with, so neither gains a new refusal.
    """
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(resolved, flags)
    except OSError as exc:
        raise ExtractionRefusal(str(resolved), f"cannot open source ({exc})") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ExtractionRefusal(str(resolved), "is no longer a regular file")
        if max_bytes is not None and st.st_size > max_bytes:
            # Named repo-relative, as the extractor's own post-read check
            # names it, so the refusal text does not depend on which check
            # fired first.
            try:
                named = resolved.relative_to(repo_root.resolve()).as_posix()
            except ValueError:
                named = str(resolved)
            raise ExtractionRefusal(
                named,
                f"source is {st.st_size} bytes, over the {max_bytes}-byte bound; "
                "narrow the glob to exclude it",
            )
        with os.fdopen(fd, "rb") as handle:
            fd = None  # fdopen took ownership; avoid a double-close below.
            return handle.read()
    finally:
        if fd is not None:
            os.close(fd)


def _read_text_no_follow(path: Path) -> str:
    """Open `path` with O_NOFOLLOW (where available) and read it as UTF-8
    text. Used for a preserved page's title read, so a page replaced
    by a symlink between the caller's lstat check and this read is refused
    by the open rather than followed to whatever the link now targets.
    """
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    with os.fdopen(fd, "r", encoding="utf-8") as handle:
        return handle.read()


# ─────────────────────────── manifest helpers ─────────────────────────────


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_existing_manifest(meta_path: Path) -> dict:
    """The on-disk metadata, or an empty one when it is absent or unparseable.

    Never follows a link: a symlinked or special `_meta/manifest.json`
    refuses with the destination pre-pass's own cause, because this read runs
    before that pre-pass and would otherwise read bytes from outside the
    output root.
    """
    try:
        st = meta_path.lstat()
    except FileNotFoundError:
        return {"pages": []}
    if not stat.S_ISREG(st.st_mode):
        raise ExtractionRefusal(
            str(meta_path), "destination is not a regular file (symlink or special file)"
        )
    try:
        text = _read_text_no_follow(meta_path)
    except OSError as exc:
        # A link swapped in after the lstat check: O_NOFOLLOW refuses it
        # (ELOOP on Linux and macOS, EMLINK on FreeBSD).
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            raise ExtractionRefusal(
                str(meta_path), "destination is not a regular file (symlink or special file)"
            ) from None
        raise
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"pages": []}


def build_manifest(
    pages: list[DocPage],
    owners: dict[str, str] | None = None,
    extractors_block: dict[str, dict] | None = None,
) -> dict:
    # NOTE: per-row `extracted_at` was intentionally removed — its presence
    # caused manifest.json to churn on every run (timestamp diff with no
    # content change), making "regenerate produces no diff" assertions
    # impossible. Provenance is recoverable via `git log` on the file.
    # The content-stable fields (source_path, doc_path, sha256,
    # extractor_version) are sufficient for drift detection.
    #
    # `owners` maps a page's `path` to its configured language key
    # (ADR-0131 clause 13). Passing none (the historical single
    # -arg call) omits the `owner` field entirely — a caller building a
    # bare-page manifest with no owner context, never a change of behavior
    # for that call shape.
    owners = owners or {}
    rows = []
    for page in sorted(pages, key=lambda p: (p.source_path, p.path)):
        row: dict[str, Any] = {}
        owner = owners.get(page.path)
        if owner is not None:
            row["owner"] = owner
        row["source_path"] = page.source_path
        row["doc_path"] = page.path
        row["sha256"] = sha256_text(page.body)
        row["extractor_version"] = EXTRACTOR_VERSION
        row.update(page.meta)
        rows.append(row)
    manifest: dict[str, Any] = {"pages": rows, "extractor_version": EXTRACTOR_VERSION}
    if extractors_block:
        manifest["extractors"] = extractors_block
    return manifest


def diff_manifests(old: dict, new: dict) -> dict:
    """Compare two manifest dicts. Returns added/changed/removed page paths."""
    old_map = {row["doc_path"]: row["sha256"] for row in old.get("pages", [])}
    new_map = {row["doc_path"]: row["sha256"] for row in new.get("pages", [])}
    added = sorted(set(new_map) - set(old_map))
    removed = sorted(set(old_map) - set(new_map))
    changed = sorted(p for p in set(old_map) & set(new_map) if old_map[p] != new_map[p])
    return {"added": added, "changed": changed, "removed": removed}


def _atomic_write_text(path: Path, body: str) -> None:
    """Atomic UTF-8 text write with no platform newline translation.

    Writes to <path>.tmp then os.replace()s into place. We write bytes
    directly so Python does NOT translate '\\n' → '\\r\\n' on Windows;
    that translation would shift the file's sha256 across platforms and
    break the byte-stable regenerative output invariant.

    [SECURITY:S5] Neither the target nor `<path>.tmp` may be a symlink. The
    tmp path is predictable and `Path.write_bytes` follows a link, so a
    pre-created link turns a regeneration into a write into someone else's
    file, while `os.replace` moves the tmp PATH and leaves the link standing.
    Checked explicitly for a readable error, then created O_NOFOLLOW|O_EXCL so
    the check is not a TOCTOU window. This helper has BEHAVIORAL parity with
    the one in validate-catalog.py — same guards, same order, same exception
    type — but the two are not byte-identical: each names its own subject in
    its messages ("generated content" here, "catalog contents" there). Treat
    the messages as the only licensed difference. A fail-closed sibling sweep
    found the same gap in both, plus in `_yaml_min.write_catalog_yaml`, and
    all three were fixed together. Change one, change all three.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    for label, candidate in (("target", path), ("temporary file", tmp)):
        if candidate.is_symlink():
            raise OSError(
                f"refusing to write {path}: the {label} {candidate} is a symlink — "
                "writing through it would put generated content in the link's target"
            )
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    except FileExistsError as exc:
        raise OSError(
            f"refusing to write {path}: the temporary file {tmp} already exists; "
            "remove it after checking what created it"
        ) from exc
    except OSError as exc:  # ELOOP from O_NOFOLLOW, or an unwritable directory
        raise OSError(f"refusing to write {path}: cannot create {tmp} ({exc})") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body.encode("utf-8"))
        os.replace(tmp, path)
    except Exception:
        # Clean up partial tmp before re-raising.
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


# ───────────────────── D1 destination pre-pass (ADR-0131 clause 14) ─────────────


def _validate_destination(output_root: Path, dest: Path) -> ExtractionRefusal | None:
    """Check one destination AND its temp path, one path component at a
    time with lstat, without following links (ADR-0131 clause 14 D1).

    Every ancestor between `output_root` and `dest` must be a directory or
    absent; the leaf must be a regular file or absent; the leaf's temporary
    write path (`<leaf>.tmp`) must be absent. Returns the first refusal
    found, or None. Called over EVERY destination before the first write —
    a collision discovered at the LAST destination in write order still
    refuses before the FIRST write happens.
    """
    try:
        rel_parts = dest.relative_to(output_root).parts
    except ValueError:
        return ExtractionRefusal(str(dest), "destination escapes the output root")
    if not rel_parts:
        return ExtractionRefusal(str(dest), "destination resolves to the output root itself")
    cur = output_root
    for part in rel_parts[:-1]:
        cur = cur / part
        try:
            st = cur.lstat()
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            return ExtractionRefusal(str(cur), f"cannot stat ancestor ({exc})")
        if not stat.S_ISDIR(st.st_mode):
            return ExtractionRefusal(
                str(cur), "ancestor is not a directory (symlink or special file)"
            )
    leaf = cur / rel_parts[-1]
    try:
        st = leaf.lstat()
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        return ExtractionRefusal(str(leaf), f"cannot stat destination ({exc})")
    else:
        if not stat.S_ISREG(st.st_mode):
            return ExtractionRefusal(
                str(leaf), "destination is not a regular file (symlink or special file)"
            )
    tmp = leaf.with_suffix(leaf.suffix + ".tmp")
    try:
        tmp.lstat()
    except FileNotFoundError:
        pass
    else:
        return ExtractionRefusal(
            str(tmp), "a temporary file already exists at this destination"
        )
    return None


def _validate_destinations(output_root: Path, destinations: list[Path]) -> list[ExtractionRefusal]:
    refusals: list[ExtractionRefusal] = []
    for dest in destinations:
        refusal = _validate_destination(output_root, dest)
        if refusal is not None:
            refusals.append(refusal)
    return refusals


# ────────────────────── refusal reporting (ADR-0131 clause 15) ───────────────────


_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f\x80-\x9f]")


def _sanitize_refusal_text(text: str) -> str:
    """Escape ASCII control characters (except `\\t`, which stderr and JSON
    both render safely) and the C1 range (0x80-0x9f) out of target-derived
    text before it reaches stderr or a JSON payload. `\\n` and `\\r`
    are escaped along with the rest: a metadata `doc_path` carrying a raw
    `\\n` could otherwise forge a second `extract-code-docs: ...` line on
    stderr, indistinguishable from a genuine second refusal.
    """
    return _CONTROL_CHAR_RE.sub(lambda m: f"\\x{ord(m.group()):02x}", text)


def _report_refusals(refusals: list[ExtractionRefusal], dry_run: bool) -> int:
    """The ONE reporting function every content refusal goes through.

    Outside --dry-run: each refusal's message on stderr, nothing on stdout.
    Under --dry-run: one `validation_errors` JSON payload on stdout, no
    `drift` key, no regenerator named as remedy. Always exits 1. Callers
    must not have written anything before calling this. Every `path` and
    `cause` is sanitized (`_sanitize_refusal_text`) before it reaches either
    channel, since both are target-derived and may carry raw control bytes.
    """
    if dry_run:
        payload = {
            "validation_errors": [
                {
                    "path": _sanitize_refusal_text(r.path),
                    "cause": _sanitize_refusal_text(r.cause),
                }
                for r in refusals
            ]
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for r in refusals:
            path = _sanitize_refusal_text(r.path)
            cause = _sanitize_refusal_text(r.cause)
            print(f"extract-code-docs: {path}: {cause}", file=sys.stderr)
    return 1


def _report_write_failure(exc: OSError) -> int:
    """Report an `OSError` raised mid-write (ADR-0131 clause 15: never a traceback).

    Everything up to this point either wrote its own bytes atomically
    (`_atomic_write_text`, write-tmp + `os.replace`) or is idempotent to
    rerun, so an OSError here -- a read-only destination directory, a full
    filesystem -- leaves the output tree possibly partially written, and a
    full rerun repairs it: every write this dispatcher performs is safe to
    redo from the same source tree.
    """
    path = _sanitize_refusal_text(str(getattr(exc, "filename", None) or exc))
    reason = _sanitize_refusal_text(exc.strerror or str(exc))
    print(
        f"extract-code-docs: write failed at {path} ({reason}, errno {exc.errno}); "
        "the output tree may be partially written -- a full rerun repairs it.",
        file=sys.stderr,
    )
    return 1


# ─────────────────────────────── pipeline ─────────────────────────────────


SAFE_PATH_RE = re.compile(r"[^A-Za-z0-9_./\-]")


def _safe_relpath(p: str) -> str:
    """Strip leading slashes; reject .. components."""
    p = p.lstrip("/")
    if any(part == ".." for part in p.split("/")):
        raise ValueError(f"unsafe doc path: {p!r}")
    return p


_META_MANIFEST_LABEL = "_meta/manifest.json"


def _validate_metadata_shape(manifest: Any) -> ExtractionRefusal | None:
    """Validate the parsed `_meta/manifest.json` as a whole before any field
    is read from it: it must be an object, a present `pages` must be a list,
    and a present `extractors` must be an object. Anything else is a refusal
    naming the metadata file, never a traceback."""
    if not isinstance(manifest, dict):
        return ExtractionRefusal(
            _META_MANIFEST_LABEL, f"metadata is not an object: {type(manifest).__name__}"
        )
    if "pages" in manifest and not isinstance(manifest["pages"], list):
        return ExtractionRefusal(
            _META_MANIFEST_LABEL, f"pages is not a list: {type(manifest['pages']).__name__}"
        )
    if "extractors" in manifest and not isinstance(manifest["extractors"], dict):
        return ExtractionRefusal(
            _META_MANIFEST_LABEL,
            f"extractors is not an object: {type(manifest['extractors']).__name__}",
        )
    return None


def _ownership_pages_failure(manifest: dict) -> str | None:
    """The ownership proof's one condition beyond the metadata shape check.

    Every manifest this dispatcher has written carries `pages` as a list, so
    a manifest without one proves nothing about who generated the root.
    Used only by `_ownership_proof_failure`; `_validate_metadata_shape`,
    which types only a PRESENT `pages`, is unchanged.
    """
    if not isinstance(manifest.get("pages"), list):
        return "its _meta/manifest.json pages is missing or not a list"
    return None


def _validate_metadata_row(row: Any) -> ExtractionRefusal | None:
    """Validate one `_meta/manifest.json` row before any code path reads or
    unlinks a file at its `doc_path`, and before the ownerless/prune
    scan treats its `owner` as a hashable, orderable string. A row
    must be a dict carrying a non-empty string `doc_path` and a non-empty
    string `source_path`, neither containing a NUL byte; the `doc_path`
    must be relative (never absolute), and every one of its `/`-separated
    parts must be non-empty and never `.` or `..`. A present `owner` must be
    a string -- ADR-0132 item 6 already treats an absent or empty owner as
    ownerless, and a non-string owner refuses through this same clause-15
    lane rather than reaching `sorted()` or a hash lookup keyed on it.
    Anything else is a refusal naming the metadata file and the offending
    row, never followed onto disk. This is the on-load half of the fix;
    `_owned_page_path` is the second, applied at each site that turns a
    validated row's `doc_path` into a filesystem path.
    """
    if not isinstance(row, dict):
        return ExtractionRefusal(_META_MANIFEST_LABEL, f"row is not an object: {row!r}")
    for field_name in ("doc_path", "source_path"):
        value = row.get(field_name)
        if not isinstance(value, str) or not value:
            return ExtractionRefusal(
                _META_MANIFEST_LABEL,
                f"row is missing a string {field_name}: {row!r}",
            )
        if "\x00" in value:
            return ExtractionRefusal(
                _META_MANIFEST_LABEL, f"row {field_name} contains a NUL byte: {row!r}"
            )
    doc_path = row["doc_path"]
    if Path(doc_path).is_absolute() or doc_path.startswith("/"):
        return ExtractionRefusal(_META_MANIFEST_LABEL, f"row doc_path {doc_path!r} is absolute")
    parts = doc_path.split("/")
    if any(part == ".." for part in parts):
        return ExtractionRefusal(
            _META_MANIFEST_LABEL, f"row doc_path {doc_path!r} contains a '..' component"
        )
    if any(part in ("", ".") for part in parts):
        return ExtractionRefusal(
            _META_MANIFEST_LABEL,
            f"row doc_path {doc_path!r} has an empty or '.' component",
        )
    owner = row.get("owner")
    if owner is not None and not isinstance(owner, str):
        return ExtractionRefusal(_META_MANIFEST_LABEL, f"row owner is not a string: {owner!r}")
    # `diff_manifests` reads `row["sha256"]` unconditionally (ADR-0131 clause
    # 14's byte-level drift comparison), so a row missing it -- or carrying
    # a non-string value -- refuses here, on load, rather than raising a
    # KeyError out of that comparison after the row already passed.
    sha256 = row.get("sha256")
    if not isinstance(sha256, str) or not sha256:
        return ExtractionRefusal(
            _META_MANIFEST_LABEL, f"row is missing a string sha256: {row!r}"
        )
    return None


def _owned_page_path(output_root: Path, doc_path: str) -> Path:
    """Turn a metadata row's (already-validated) `doc_path` into a
    filesystem path, refusing (ExtractionRefusal) anything `_safe_relpath`
    or the D1 destination pre-pass (`_validate_destination`) would not
    admit. The second of two layers: `_validate_metadata_row` refuses
    an unsafe row on load; this refuses again at the point of use, so a
    prune candidate or a preserved-page read is never built from a raw
    string join.
    """
    try:
        safe = _safe_relpath(doc_path)
    except ValueError as exc:
        raise ExtractionRefusal(doc_path, f"unsafe doc_path ({exc})") from exc
    candidate = output_root / safe
    refusal = _validate_destination(output_root, candidate)
    if refusal is not None:
        raise refusal
    return candidate


def _as_extraction_refusal(exc: Exception, path: str) -> ExtractionRefusal:
    """Wrap a non-refusal exception from an API-2 extractor call: name the
    path (or the language key when no source is known) and the exception
    class, never a traceback (ADR-0131 clause 1)."""
    return ExtractionRefusal(path, f"{type(exc).__name__}: {exc}")


def run_extractor(
    name: str,
    config: dict,
    repo_root: Path,
    verbose: bool,
    ctx: ExtractContext | None = None,
) -> list[DocPage]:
    module = load_extractor_module(name)
    if not hasattr(module, "discover") or not hasattr(module, "extract"):
        raise RuntimeError(f"extractor {name!r} missing discover/extract")
    api_version = getattr(module, "EXTRACTOR_API", 1)
    lang_key = ctx.lang_key if ctx is not None else name
    try:
        if api_version == 2:
            units: list[SourceUnit] = list(module.discover(repo_root, config, ctx))
        else:
            units = list(module.discover(repo_root, config))
    except ExtractionRefusal:
        raise
    except Exception as exc:
        if api_version == 2:
            raise _as_extraction_refusal(exc, lang_key) from exc
        raise
    if verbose:
        print(f"[{_sanitize_refusal_text(str(name))}] discovered {len(units)} unit(s)", file=sys.stderr)
    units.sort(key=lambda u: (u.source_path, u.identifier))
    pages: list[DocPage] = []
    for unit in units:
        try:
            if api_version == 2:
                page = module.extract(unit, ctx)
            else:
                page = module.extract(unit)
        except ExtractionRefusal:
            raise
        except Exception as exc:
            if api_version == 2:
                raise _as_extraction_refusal(exc, unit.source_path) from exc
            raise
        if page is None:
            continue
        if not isinstance(page, DocPage):
            # Allow extractors to return a duck-typed object.
            page = DocPage(
                title=getattr(page, "title", unit.identifier),
                path=getattr(page, "path"),
                body=getattr(page, "body"),
                source_path=getattr(page, "source_path", unit.source_path),
                meta=dict(getattr(page, "meta", {}) or {}),
            )
        page.path = _safe_relpath(page.path)
        pages.append(page)
    pages.sort(key=lambda p: p.path)
    return pages


def run_extractor_provenance(name: str, config: dict, lang_key: str) -> dict | None:
    """Call an API-2 extractor's `provenance(config)` (ADR-0131 clause 13).
    Returns None for a legacy extractor (no provenance block)."""
    module = load_extractor_module(name)
    if getattr(module, "EXTRACTOR_API", 1) != 2:
        return None
    if not hasattr(module, "provenance"):
        return None
    try:
        return dict(module.provenance(config))
    except ExtractionRefusal:
        raise
    except Exception as exc:
        raise _as_extraction_refusal(exc, lang_key) from exc


def write_pages(pages: list[DocPage], output_dir: Path, verbose: bool) -> None:
    """Write all pages and prune output-dir of stale files (regenerative).

    All writes are atomic (write-tmp + os.replace). Prune resolves the
    output_dir once and refuses to unlink any file reachable only through a
    symlink — a link found under docs/code, however it resolves, is reported
    on stderr and left exactly as it was (ADR-0131 clause 14 D1; amended from
    the earlier behavior of unlinking an in-tree link while preserving its
    outside target — a link's target is never this dispatcher's page to have
    written, so the link itself is never this dispatcher's to delete either).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_output = output_dir.resolve()
    # `kept` is normalized on resolved paths so the symmetry holds across
    # symlinks and relative components.
    kept: set[Path] = {
        (resolved_output / "_meta" / "manifest.json").resolve(),
        (resolved_output / "index.md").resolve(),
    }
    for page in pages:
        dest = output_dir / page.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(dest, page.body)
        kept.add(dest.resolve())
        if verbose:
            print(f"wrote {_sanitize_refusal_text(str(dest))}", file=sys.stderr)
    # Prune: every .md under output_dir not in kept is deleted (regenerative
    # invariant), EXCEPT a file reachable only through a symlink, which is
    # reported and left alone — never unlinked, whether it resolves inside or
    # outside the tree.
    for path in sorted(output_dir.rglob("*.md")):
        # The leaf's own type is checked FIRST, before the `kept` test.
        # A stray symlink whose target happens to resolve to a page this run
        # just wrote is still a symlink this run never created and never
        # owns; reporting it must not depend on where it points.
        if path.is_symlink():
            print(
                f"extract-code-docs: refusing to prune {_sanitize_refusal_text(str(path))} "
                f"(symlink; not indexed by this run)",
                file=sys.stderr,
            )
            continue
        try:
            leaf_st = path.lstat()
        except OSError:
            continue
        if not stat.S_ISREG(leaf_st.st_mode):
            # A special file (FIFO, socket, device) standing where a page
            # would be is never a page this run wrote, so it is never this
            # run's to delete either (ADR-0131 clause 14 D1) -- reported and
            # left alone, the same treatment a stray symlink gets above.
            print(
                f"extract-code-docs: refusing to prune {_sanitize_refusal_text(str(path))} "
                f"(not a regular file; not indexed by this run)",
                file=sys.stderr,
            )
            continue
        resolved = path.resolve()
        if resolved in kept:
            continue
        # Guard against a resolved path pointing OUTSIDE the output tree via
        # a symlinked ANCESTOR directory (the leaf-level checks above only
        # cover the leaf itself) — never unlink a file we don't own.
        inside = _is_inside(resolved, resolved_output)
        if not inside:
            print(
                f"extract-code-docs: refusing to prune {_sanitize_refusal_text(str(path))} "
                f"(resolves outside {_sanitize_refusal_text(str(resolved_output))})",
                file=sys.stderr,
            )
            continue
        if verbose:
            print(f"pruned {_sanitize_refusal_text(str(path))}", file=sys.stderr)
        path.unlink()
    _write_index(pages, output_dir)


def _index_link_text_and_dest(page: DocPage) -> tuple[str, str]:
    """ADR-0131 clause 10. A Python-owned page's link text is its escaped title as
    given, and its destination is percent-encoded; every other page renders
    `- [{title}]({doc_path})` exactly as today."""
    if page.meta.get("language") == "python":
        import urllib.parse

        return page.title, urllib.parse.quote(page.path, safe="/")
    return page.title, page.path


def _write_index(pages: list[DocPage], output_dir: Path) -> None:
    """Write a deterministic docs/code/index.md grouped by language namespace.

    Renders through `_render_index_text` rather than repeating its
    logic, so the write and dry-run paths render one index from one place.
    """
    _atomic_write_text(output_dir / "index.md", _render_index_text(pages))


def _classify_output_dir_entries(
    resolved_output: Path, known: set[str]
) -> tuple[list[str], list[str]]:
    """Scan `resolved_output` for `*.md` entries not in `known` (`index.md`
    is always exempt), classifying each by an `lstat` of the leaf rather
    than following it. Returns `(regular, non_regular)`: `regular` is
    a plain file this run does not know about; `non_regular` is a symlink
    or special file, which is never a byte this run would change, so it is
    reported as `unowned` rather than counted as drift-causing `unexpected`.
    This is the one scan shared by the full dry-run's `unexpected`/`unowned`
    split, the --lang dry-run's `unowned` list, and the --lang write lane's
    stray report (the same file set in all three, per review).

    The `*.md` scope is a stated decision, not an oversight: it
    matches the full-run prune scope in `write_pages`, which also scans
    only `*.md`. A stray non-`.md` file under the output root is reported
    by neither this scan nor a full-run prune.
    """
    regular: list[str] = []
    non_regular: list[str] = []
    for p in sorted(resolved_output.rglob("*.md")):
        try:
            rel = str(p.relative_to(resolved_output))
        except ValueError:
            continue
        if rel == "index.md" or rel in known:
            continue
        try:
            st = p.lstat()
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            regular.append(rel)
        else:
            non_regular.append(rel)
    return regular, non_regular


def _render_index_text(pages: list[DocPage]) -> str:
    """Render the index without touching disk, for --dry-run, the in-memory
    WritePlan, and `_write_index`, which is the one on-disk writer."""
    by_lang: dict[str, list[DocPage]] = {}
    for page in pages:
        ns = page.path.split("/", 1)[0] if "/" in page.path else "_root"
        by_lang.setdefault(ns, []).append(page)
    lines: list[str] = ["# Code documentation", "", "_Regenerated by `extract-code-docs`. Do not hand-edit._", ""]
    for lang in sorted(by_lang):
        rows = sorted(by_lang[lang], key=lambda p: p.path)
        lines.append(f"## {lang} ({len(rows)})")
        lines.append("")
        for page in rows:
            text, dest = _index_link_text_and_dest(page)
            lines.append(f"- [{text}]({dest})")
        lines.append("")
    return "\n".join(lines)


def write_meta(manifest: dict, output_dir: Path) -> None:
    meta_dir = output_dir / "_meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(
        meta_dir / "manifest.json",
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    )


# ──────────────────── capability hook (ADR-0131 clause 5) ────────────────────────


def _installed_griffe_version() -> str | None:
    """The installed griffelib version, or None when `griffe` does not import.

    The import is guarded so a run that never selects a Python key never needs
    griffe, and so a missing package reports rather than crashing.
    """
    try:
        import griffe  # noqa: F401
    except ImportError:
        return None
    import importlib.metadata

    try:
        return importlib.metadata.version("griffelib")
    except importlib.metadata.PackageNotFoundError:
        return None


def _selected_extractor_name(lang_key: Any, entry: dict) -> Any:
    """A present `extractor` value is the name, whatever its type; the key
    stands in only when the value is absent."""
    return entry["extractor"] if "extractor" in entry else lang_key


def _validate_extractor_names(
    selected: list[tuple[Any, Any]],
) -> tuple[list[ExtractionRefusal], set[Any]]:
    """Check every name the run selects before the first extractor import.

    Returns the refusals, one per bad name, and the keys whose name names a
    shipped module. A non-mapping entry is skipped by the run and so is not
    selected for import.
    """
    refusals: list[ExtractionRefusal] = []
    valid: set[Any] = set()
    for lang_key, entry in selected:
        if not isinstance(entry, dict):
            continue
        name = _selected_extractor_name(lang_key, entry)
        try:
            resolve_extractor_path(name)
        except ExtractionRefusal as refusal:
            refusals.append(
                ExtractionRefusal(
                    f"code.extractors.{lang_key}",
                    f"extractor {refusal.path} {refusal.cause}",
                )
            )
            continue
        valid.add(lang_key)
    return refusals, valid


# ─────────────────── output-root ownership (unowned-root refusal) ─────────────

_OWNERSHIP_SAMPLE = 5


def _regular_markdown_in(root: Path) -> list[str]:
    """Every regular `*.md` file below `root`, relative, found in the scope the
    full-run prune scans and without following a link."""
    found: list[str] = []
    try:
        candidates = sorted(root.rglob("*.md", recurse_symlinks=False))
    except OSError:
        return found
    for p in candidates:
        try:
            st = p.lstat()
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            found.append(str(p.relative_to(root)))
    return found


def _ownership_proof_failure(root: Path) -> str | None:
    """None when `root/_meta/manifest.json` proves ownership; else the cause.

    The proof is a regular file inside `root`, reached without following a
    link, whose JSON passes `_validate_metadata_shape` and carries `pages` as
    a list (`_ownership_pages_failure`).
    """
    meta_dir = root / "_meta"
    try:
        st = meta_dir.lstat()
    except FileNotFoundError:
        return "it has no _meta/manifest.json"
    except OSError as exc:
        return f"_meta could not be inspected ({exc.strerror})"
    if not stat.S_ISDIR(st.st_mode):
        return "its _meta is a link or not a directory"
    manifest = meta_dir / "manifest.json"
    try:
        st = manifest.lstat()
    except FileNotFoundError:
        return "it has no _meta/manifest.json"
    except OSError as exc:
        return f"_meta/manifest.json could not be inspected ({exc.strerror})"
    if not stat.S_ISREG(st.st_mode):
        return "its _meta/manifest.json is a link or not a regular file"
    try:
        parsed = json.loads(_read_text_no_follow(manifest))
    except (OSError, ValueError) as exc:
        return f"its _meta/manifest.json does not parse ({type(exc).__name__})"
    shape = _validate_metadata_shape(parsed)
    if shape is not None:
        return f"its _meta/manifest.json fails the metadata shape check ({shape.cause})"
    return _ownership_pages_failure(parsed)


def _ownership_refusal(
    spelled: Path, resolved: Path, links: list[str]
) -> ExtractionRefusal | None:
    """Refuse a resolved output root that holds Markdown and does not prove
    this dispatcher generated it. Runs after the marker check admits the
    root and before any directory creation, write or prune."""
    markdown = _regular_markdown_in(resolved)
    if not markdown:
        return None
    failure = _ownership_proof_failure(resolved)
    if failure is None:
        return None
    sample = ", ".join(markdown[:_OWNERSHIP_SAMPLE])
    more = f", and {len(markdown) - _OWNERSHIP_SAMPLE} more" if len(markdown) > _OWNERSHIP_SAMPLE else ""
    link_text = ", ".join(links) if links else "none"
    return ExtractionRefusal(
        str(resolved),
        f"output root holds {len(markdown)} regular *.md file(s) ({sample}{more}) "
        f"but does not prove this dispatcher generated it: {failure}. "
        f"Spelled root: {spelled}; resolved root: {resolved}; symlinks on the path: "
        f"{link_text}. Nothing was written or deleted. Retarget --output-dir or "
        f"the symlink, or use an empty directory or one this dispatcher generated.",
    )


def _check_capabilities(selected: list[tuple[str, dict]]) -> None:
    """The griffe capability check (ADR-0131 clause 5).

    Called after --lang selection and BEFORE output-root admission and any read
    of the output directory (ADR-0131 clause 5). It applies only when a SELECTED key's
    extractor type is `python`; a run selecting no Python key never imports
    griffe. A missing griffe or any version other than GRIFFE_VERSION exits 2
    with a message on stderr and nothing on stdout, dry-run or not. It never
    re-executes the process to obtain griffe: the PEP 723 block is how griffe
    arrives, and this check is only the defence for a bare `python3` run.
    """
    python_keys = sorted(
        key for key, entry in selected
        if isinstance(entry, dict) and _selected_extractor_name(key, entry) == "python"
    )
    if not python_keys:
        return None
    found = _installed_griffe_version()
    if found == GRIFFE_VERSION:
        return None
    have = "griffe is not importable" if found is None else f"griffelib {found} is installed"
    print(
        f"extract-code-docs: the Python extractor (key(s) {_sanitize_refusal_text(', '.join(map(str, python_keys)))}) needs "
        f"{GRIFFE_REQUIREMENT}, but {have}. Run the dispatcher as "
        "`uv run --no-config <plugin>/scripts/extract-code-docs.py`, which resolves the "
        "pinned version from the script's inline metadata.",
        file=sys.stderr,
    )
    raise SystemExit(2)


# ──────────────────────── D2: claiming-tree resolution ─────────────────────


def _claim_repo_root(resolved_config: Path) -> Path:
    """An explicit --config selects the sources of the nearest ancestor whose
    `.bionic.yml` `docs_dir` resolves to the manifest's own directory; absent
    such an ancestor, the manifest's grandparent is used (ADR-0131 clause 14
    D2). The root always comes from the invocation's --config, never from
    this script's own location.
    """
    manifest_dir = resolved_config.parent
    cur = manifest_dir
    while True:
        candidate = cur / ".bionic.yml"
        if candidate.is_file():
            try:
                layout = load_yaml(candidate)
            except Exception:
                layout = {}
            docs_dir = layout.get("docs_dir")
            if isinstance(docs_dir, str) and docs_dir:
                claimed = (cur / docs_dir).resolve()
                if claimed == manifest_dir.resolve():
                    return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return manifest_dir.parent


# ──────────────────────────────── main ────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    # ADR-0131 clause 5: before anything is parsed, read, written or pruned.
    if tuple(sys.version_info[:2]) < PYTHON_FLOOR:
        print(
            f"extract-code-docs: Python {PYTHON_FLOOR[0]}.{PYTHON_FLOOR[1]} or later is "
            f"required; this is {sys.version_info[0]}.{sys.version_info[1]}. Run it as "
            "`uv run --no-config <plugin>/scripts/extract-code-docs.py`.",
            file=sys.stderr,
        )
        return 2
    args = parse_args(argv)

    # Derived defaults come from the repo-root .crux (ADR-0032); an explicitly
    # passed flag wins unconditionally and is never recomputed.
    #
    # OWNERSHIP RULE. One manifest owns BOTH the sources and the generated
    # pages of one tree. The two defaults are therefore derived from ONE
    # config, never from two — a `--config` naming another repository's
    # manifest must take its output default from THAT manifest's tree, not
    # from the tree the command happened to run in.
    config_was_explicit = args.config is not None
    output_was_explicit = args.output_dir is not None
    crux_repo_root = None
    if not config_was_explicit:
        try:
            _cfg = _load_crux_config()
        except CruxConfigError as exc:
            print(f"extract-code-docs: .crux configuration error: {_sanitize_refusal_text(str(exc))}", file=sys.stderr)
            return 1
        crux_repo_root = _cfg.repo_root
        args.config = _cfg.docs_root / "manifest.yml"
        if not output_was_explicit:
            args.output_dir = _cfg.docs_root / "code"
    elif not output_was_explicit:
        # The explicit config's own docs root — `parent`, not `parent.parent /
        # docs_dir`, because the manifest LIVES at the docs root whatever its
        # depth. Resolved, so a relative and an absolute spelling of the same
        # config, and a symlinked config path, all land on one output tree.
        args.output_dir = args.config.resolve().parent / "code"

    if not args.config.is_file():
        print(f"extract-code-docs: config not found: {_sanitize_refusal_text(str(args.config))}", file=sys.stderr)
        return 1
    manifest = load_yaml(args.config)
    # Destructive-consumer marker check (ADR-0032 §1): this script PRUNES
    # under the output dir, so refuse to treat a tree without a readable
    # schema_version as a crux tree.
    if not isinstance(manifest.get("schema_version"), (str, int)):
        print(
            f"extract-code-docs: {_sanitize_refusal_text(str(args.config))} has no readable schema_version; refusing to operate on a non-crux tree",
            file=sys.stderr,
        )
        return 1

    code_cfg = (manifest.get("code") or {}).get("extractors") or {}
    if not isinstance(code_cfg, dict) or not code_cfg:
        # NOTHING WAS MEASURED, AND THE PAYLOAD MUST SAY SO ON STDOUT.
        payload = {
            "surface_absent": True,
            "drift": False,
            "reason": "no extractors configured under code.extractors",
            "added": 0, "changed": 0, "removed": 0, "pages_total": 0,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        print("extract-code-docs: no extractors configured under code.extractors.", file=sys.stderr)
        return 0

    # An explicitly passed --config always anchors repo_root via the claiming
    # walk (ADR-0131 clause 14 D2): the nearest ancestor whose `.bionic.yml`
    # docs_dir claims the manifest's own directory, else the grandparent.
    # Only a .crux-derived config uses the .crux repo root (which, unlike
    # parent.parent, survives a multi-segment docs_dir like meta/docs).
    resolved_config = args.config.resolve()
    if config_was_explicit or crux_repo_root is None:
        repo_root = _claim_repo_root(resolved_config)
    else:
        repo_root = crux_repo_root

    selected = sorted(code_cfg.items())
    if args.lang:
        selected = [(k, v) for k, v in selected if k == args.lang]
        if not selected:
            configured = ", ".join(repr(k) for k, _ in sorted(code_cfg.items())) or "none"
            print(
                f"extract-code-docs: --lang {_sanitize_refusal_text(repr(args.lang))} not found "
                f"in manifest (configured keys: {_sanitize_refusal_text(configured)})",
                file=sys.stderr,
            )
            return 1

    # Every extractor name the run selects is checked before the first
    # extractor import and before any output write. A bad name is reported
    # with every other refusal this invocation detects.
    name_refusals, valid_name_keys = _validate_extractor_names(selected)

    # ADR-0131 clause 5: after --lang selection, before output-root admission or
    # any read of the output directory. May exit 2 (SystemExit propagates).
    # A key whose name refused is never imported, so it needs no capability.
    _check_capabilities([(k, v) for k, v in selected if k in valid_name_keys])

    # ── D1: output-root admission ───────────────────────────────────────
    # Report every symlink in the SPELLED path (never following one) before
    # resolving. The report refuses nothing; the marker check below alone
    # admits or refuses.
    spelled_output = args.output_dir
    symlinked_components: list[str] = []
    prefix = Path()
    base_parts = spelled_output.parts
    for part in base_parts:
        prefix = prefix / part if str(prefix) else Path(part)
        try:
            if prefix.is_symlink():
                symlinked_components.append(str(prefix))
        except OSError:
            pass

    resolved_output = args.output_dir.resolve()
    if symlinked_components:
        print(
            f"extract-code-docs: output root path passes through symlink(s) "
            f"{_sanitize_refusal_text(', '.join(map(str, symlinked_components)))} "
            f"(resolved: {_sanitize_refusal_text(str(resolved_output))}).",
            file=sys.stderr,
        )

    # THE CHECK ABOVE VALIDATES THE CONFIG'S TREE. VALIDATE THE ONE THAT GETS
    # PRUNED. When the output dir was derived, the config IS that tree's
    # manifest by construction and the check above already covered it. An
    # explicit `--output-dir` can name a different tree, and that tree — the
    # actual destructive target — has to carry the marker on its own account.
    output_manifest = resolved_output.parent / "manifest.yml"
    if output_manifest != resolved_config:
        try:
            output_marker = load_yaml(output_manifest).get("schema_version")
        except OSError:
            output_marker = None
        except Exception as exc:  # malformed YAML in the destructive target
            return _report_refusals(
                name_refusals + [ExtractionRefusal(
                    str(output_manifest),
                    f"could not be read ({exc}); refusing to prune under {resolved_output}",
                )],
                args.dry_run,
            )
        if not isinstance(output_marker, (str, int)):
            if output_was_explicit:
                remedy = (
                    f"Pass an --output-dir whose parent holds a manifest.yml, or drop "
                    f"--output-dir to use {resolved_config.parent / 'code'}."
                )
                subject = f"--output-dir {resolved_output}"
            else:
                remedy = (
                    f"The default output dir {args.output_dir} resolves there, so it is "
                    f"most likely a symlink out of its own tree; point it back inside "
                    f"{resolved_config.parent} or pass an explicit --output-dir."
                )
                subject = f"the resolved output dir {resolved_output}"
            # This is the marker-check refusal in ADR-0131 clause 15's list
            # ("an output root the marker check does not admit"), so it goes
            # through ADR-0131 clause 15 exactly like every other content refusal:
            # stderr-only outside --dry-run, a validation_errors JSON payload
            # on stdout under --dry-run -- never stderr-only regardless of
            # the flag, which is what this dispatcher did before this fix.
            return _report_refusals(
                name_refusals + [ExtractionRefusal(
                    subject,
                    f"is not inside a crux tree ({output_manifest} is missing or "
                    f"carries no readable schema_version); refusing to write and "
                    f"prune there. {remedy}",
                )],
                args.dry_run,
            )
        # Two crux trees, deliberately combined. Permitted, but never silent:
        # the sources come from one and the prune lands in the other.
        print(
            f"extract-code-docs: sources from "
            f"{_sanitize_refusal_text(str(resolved_config.parent))}, writing and "
            f"pruning under {_sanitize_refusal_text(str(resolved_output))} (different tree).",
            file=sys.stderr,
        )

    # The marker check admitted the root. Ownership is a separate refusal
    # caused by the root's content: a root holding Markdown must prove this
    # dispatcher generated it before anything is created, written or pruned.
    # It runs, like the name check, before any extractor is imported.
    ownership_refusal = _ownership_refusal(spelled_output, resolved_output, symlinked_components)
    if name_refusals or ownership_refusal is not None:
        return _report_refusals(
            name_refusals + ([ownership_refusal] if ownership_refusal is not None else []),
            args.dry_run,
        )

    if args.verbose:
        print(
            f"extract-code-docs: repo_root={_sanitize_refusal_text(str(repo_root))} "
            f"output_dir={_sanitize_refusal_text(str(resolved_output))}",
            file=sys.stderr,
        )

    # ── discover + extract (D6 containment is inside each extractor) ────
    ctx_by_key: dict[str, ExtractContext] = {
        lang_key: ExtractContext(repo_root=repo_root, output_root=resolved_output, lang_key=lang_key)
        for lang_key, _entry in selected
    }
    pages_by_key: dict[str, list[DocPage]] = {}
    provenance_by_key: dict[str, dict] = {}
    refusals: list[ExtractionRefusal] = []
    for lang_key, entry in selected:
        if not isinstance(entry, dict):
            print(
                f"extract-code-docs: skipping {_sanitize_refusal_text(str(lang_key))!r} "
                f"(not a mapping)",
                file=sys.stderr,
            )
            continue
        extractor_name = _selected_extractor_name(lang_key, entry)
        try:
            pages = run_extractor(
                extractor_name, entry, repo_root, args.verbose, ctx_by_key[lang_key]
            )
            prov = run_extractor_provenance(extractor_name, entry, lang_key)
        except ExtractionRefusal as refusal:
            refusals.append(refusal)
            continue
        except FileNotFoundError as exc:
            print(f"extract-code-docs: {_sanitize_refusal_text(str(exc))}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(
                f"extract-code-docs: extractor "
                f"{_sanitize_refusal_text(str(extractor_name))!r} failed: "
                f"{_sanitize_refusal_text(str(exc))}",
                file=sys.stderr,
            )
            return 1
        pages_by_key[lang_key] = pages
        if prov is not None:
            provenance_by_key[lang_key] = prov

    if refusals:
        return _report_refusals(refusals, args.dry_run)

    new_pages: list[DocPage] = []
    owners: dict[str, str] = {}
    for lang_key, pages in pages_by_key.items():
        for page in pages:
            owners[page.path] = lang_key
        new_pages.extend(pages)
    new_pages.sort(key=lambda p: p.path)

    # ADR-0131 clause 14 D5: two outputs claiming one doc_path, within this run's own pages --
    # whatever their key. A same-key collision (two sources under one
    # extractor emitting the same doc_path) is refused exactly like a
    # cross-key one: `write_pages` writes one file per doc_path, so two
    # claimants of either shape would silently let the second write clobber
    # the first (rule code-doc-path-collision-refuses-before-write).
    dup_refusals: list[ExtractionRefusal] = []
    seen_paths: dict[str, str] = {}
    for lang_key, pages in pages_by_key.items():
        for page in pages:
            prior = seen_paths.get(page.path)
            if prior is not None:
                dup_refusals.append(
                    ExtractionRefusal(
                        page.path,
                        f"claimed by both {prior!r} and {lang_key!r}",
                    )
                )
            seen_paths[page.path] = lang_key

    meta_path = resolved_output / "_meta" / "manifest.json"
    try:
        old_manifest = read_existing_manifest(meta_path)
    except ExtractionRefusal as refusal:
        return _report_refusals([refusal], args.dry_run)
    # A metadata file that parses but has the wrong shape refuses through
    # the clause-15 lane before any field is read from it.
    manifest_shape_refusal = _validate_metadata_shape(old_manifest)
    if manifest_shape_refusal is not None:
        return _report_refusals([manifest_shape_refusal], args.dry_run)
    old_rows = old_manifest.get("pages", []) or []
    old_extractors_block = old_manifest.get("extractors") or {}

    # Validate every row's shape before it is used to read or unlink
    # anything, in EITHER lane (full or --lang), before any other pipeline
    # step touches it. A malformed row (not a dict; missing/non-string
    # doc_path or source_path; an absolute or `..`-carrying doc_path) is
    # refused through ADR-0131 clause 15 rather than joined onto a filesystem path.
    row_shape_refusals = [
        refusal for refusal in (_validate_metadata_row(row) for row in old_rows)
        if refusal is not None
    ]
    if row_shape_refusals:
        return _report_refusals(row_shape_refusals, args.dry_run)

    is_lang_scoped = bool(args.lang)
    preserved_rows: list[dict] = []
    preserved_pages: list[DocPage] = []
    prune_scope_doc_paths: set[str] | None = None  # None => prune everything unkept

    if is_lang_scoped:
        selected_key = selected[0][0]
        # ADR-0132 item 6: a row is ownerless when its owner
        # is absent, empty, or names a key not configured under
        # code.extractors in the manifest THIS invocation read (whatever
        # extractor that key uses) -- "configured" is independent of
        # --lang's own narrowing to `selected`. Metadata carrying ANY
        # ownerless row is ownerless: refuse before touching any row.
        configured_keys = set(code_cfg.keys())
        missing_owner_rows = [r for r in old_rows if not r.get("owner")]
        unconfigured_counts: dict[str, int] = {}
        for row in old_rows:
            owner = row.get("owner")
            if owner and owner not in configured_keys:
                unconfigured_counts[owner] = unconfigured_counts.get(owner, 0) + 1
        ownerless_refusals: list[ExtractionRefusal] = []
        if missing_owner_rows:
            ownerless_refusals.append(
                ExtractionRefusal(
                    str(meta_path),
                    f"{len(missing_owner_rows)} row(s) carry no owner; run a full "
                    "extract-code-docs (no --lang) first",
                )
            )
        for owner_key in sorted(unconfigured_counts):
            ownerless_refusals.append(
                ExtractionRefusal(
                    str(meta_path),
                    f"{unconfigured_counts[owner_key]} row(s) are owned by "
                    f"{owner_key!r}, which is not configured under "
                    "code.extractors; run a full extract-code-docs (no --lang) first",
                )
            )
        if ownerless_refusals:
            dup_refusals.extend(ownerless_refusals)
        else:
            for row in old_rows:
                if row.get("owner") == selected_key:
                    continue
                preserved_rows.append(row)
                # Second layer: the row already passed
                # `_validate_metadata_row`; `_owned_page_path` re-checks at
                # the point of use (and already refuses a symlink or special
                # leaf via the D1 destination check it shares with every
                # write destination), and the page itself is lstat'd again
                # (never `is_file()`, which follows a symlink) and read
                # O_NOFOLLOW so a page replaced by a symlink is reported,
                # never followed.
                try:
                    page_path = _owned_page_path(resolved_output, row["doc_path"])
                except ExtractionRefusal as refusal:
                    dup_refusals.append(
                        ExtractionRefusal(
                            row["doc_path"],
                            f"{refusal.cause}; run a full extract-code-docs "
                            "(no --lang) first",
                        )
                    )
                    continue
                try:
                    page_st = page_path.lstat()
                except FileNotFoundError:
                    dup_refusals.append(
                        ExtractionRefusal(
                            row["doc_path"],
                            "preserved owner's page file is missing; run a "
                            "full extract-code-docs (no --lang) first",
                        )
                    )
                    continue
                except OSError as exc:
                    dup_refusals.append(
                        ExtractionRefusal(row["doc_path"], f"cannot stat preserved page ({exc})")
                    )
                    continue
                if not stat.S_ISREG(page_st.st_mode):
                    dup_refusals.append(
                        ExtractionRefusal(
                            row["doc_path"],
                            "preserved owner's page is not a regular file; run a "
                            "full extract-code-docs (no --lang) first",
                        )
                    )
                    continue
                try:
                    body = _read_text_no_follow(page_path)
                except UnicodeDecodeError:
                    dup_refusals.append(
                        ExtractionRefusal(
                            row["doc_path"], "cannot read preserved page as UTF-8"
                        )
                    )
                    continue
                except OSError as exc:
                    dup_refusals.append(
                        ExtractionRefusal(row["doc_path"], f"cannot read preserved page ({exc})")
                    )
                    continue
                first_line = body.splitlines()[0] if body else ""
                if not first_line.startswith("# "):
                    dup_refusals.append(
                        ExtractionRefusal(
                            row["doc_path"],
                            "preserved page has no `# ` first line; run a "
                            "full extract-code-docs (no --lang) first",
                        )
                    )
                    continue
                title = first_line[2:]
                meta_extra = {
                    k: v
                    for k, v in row.items()
                    if k not in ("owner", "source_path", "doc_path", "sha256", "extractor_version")
                }
                preserved_pages.append(
                    DocPage(
                        title=title,
                        path=row["doc_path"],
                        body=body,
                        source_path=row["source_path"],
                        meta=meta_extra,
                    )
                )
        # ADR-0131 clause 14 D5, second half: a new page colliding with a preserved owner's page.
        preserved_doc_paths = {r["doc_path"] for r in preserved_rows}
        for page in new_pages:
            if page.path in preserved_doc_paths:
                dup_refusals.append(
                    ExtractionRefusal(page.path, "collides with a preserved owner's page")
                )
        # Only this key's OWN previously-owned pages are eligible for prune.
        prune_scope_doc_paths = {r["doc_path"] for r in old_rows if r.get("owner") == selected_key}

    if dup_refusals:
        return _report_refusals(dup_refusals, args.dry_run)

    final_pages = new_pages + preserved_pages
    final_pages.sort(key=lambda p: p.path)

    extractors_block = dict(old_extractors_block) if is_lang_scoped else {}
    for lang_key, prov in provenance_by_key.items():
        extractors_block[lang_key] = prov

    if is_lang_scoped:
        # ADR-0131 clause 14 D3: a --lang run preserves every other owner's bytes, which
        # includes its METADATA ROW, not only its page file. Recomputing a
        # preserved row from `final_pages` would fold in whatever currently
        # sits on disk under that path (e.g. an unrelated owner's page
        # hand-edited between runs), silently changing a row this run never
        # selected. The selected key's own rows are freshly computed; every
        # other row is carried over from the old manifest byte-for-byte.
        selected_rows = build_manifest(new_pages, owners=owners, extractors_block=None)["pages"]
        # Same order as build_manifest's full lane: two owners may share a
        # source_path, so doc_path breaks the tie.
        all_rows = sorted(selected_rows + preserved_rows,
                          key=lambda r: (r["source_path"], r["doc_path"]))
        new_manifest: dict[str, Any] = {"pages": all_rows, "extractor_version": EXTRACTOR_VERSION}
        if extractors_block:
            new_manifest["extractors"] = extractors_block
    else:
        new_manifest = build_manifest(final_pages, owners=owners, extractors_block=extractors_block or None)

    # D1 destination pre-pass: validate EVERY destination + temp path BEFORE
    # the first mkdir/write of this run -- and, per ADR-0131 clause 15, before this
    # run's --dry-run report too. A D1-failing destination is one of clause
    # 15's twelve dispatcher-owned refusals, so a --dry-run over it must
    # report the validation_errors payload, never ordinary page/index drift
    # computed by following the very link the write-mode lane refuses.
    destinations = [resolved_output / p.path for p in new_pages]
    destinations.append(resolved_output / "index.md")
    destinations.append(meta_path)
    dest_refusals = _validate_destinations(resolved_output, destinations)
    if dest_refusals:
        return _report_refusals(dest_refusals, args.dry_run)

    if args.dry_run:
        # ADR-0131 clause 14 D4: byte-level dry run. Render expected bytes in memory; compare
        # with bytes on disk. Legacy payload keys are computed from the
        # manifest diff exactly as before.
        diff = diff_manifests(old_manifest, new_manifest)
        rendered_pages = {p.path: p.body for p in final_pages}
        edited: list[str] = []
        missing: list[str] = []
        for path_str, body in rendered_pages.items():
            disk_path = resolved_output / path_str
            if not disk_path.is_file():
                missing.append(path_str)
                continue
            try:
                on_disk = disk_path.read_bytes()
            except OSError:
                missing.append(path_str)
                continue
            if on_disk != body.encode("utf-8"):
                edited.append(path_str)
        # A stray non-regular entry (symlink, FIFO, ...) is never a byte
        # the paired write would change, so it is reported under `unowned`
        # (never counted as drift) exactly as the write lane leaves it
        # untouched -- drift means exactly "the paired write without
        # --dry-run would change a byte."
        unexpected: list[str] = []
        unowned: list[str] = []
        if is_lang_scoped:
            known = set(rendered_pages)
            regular, non_regular = _classify_output_dir_entries(resolved_output, known)
            unowned = regular + non_regular
        else:
            kept_set = set(rendered_pages)
            unexpected, unowned = _classify_output_dir_entries(resolved_output, kept_set)

        expected_index = _render_index_text(final_pages)
        index_path = resolved_output / "index.md"
        index_on_disk = index_path.read_bytes() if index_path.is_file() else None
        index_drift = index_on_disk != expected_index.encode("utf-8")

        expected_meta_bytes = (json.dumps(new_manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        meta_on_disk = meta_path.read_bytes() if meta_path.is_file() else None
        metadata_drift = meta_on_disk != expected_meta_bytes

        gaps = sum(len((row.get("gaps") or [])) for row in new_manifest["pages"])

        drift = bool(edited or missing or unexpected or index_drift or metadata_drift)
        summary = {
            "added": len(diff["added"]),
            "changed": len(diff["changed"]),
            "removed": len(diff["removed"]),
            "pages_total": len(new_manifest["pages"]),
            "detail": diff,
            "drift": drift,
            "pages": {"edited": sorted(edited), "missing": sorted(missing), "unexpected": sorted(unexpected)},
            "index_drift": index_drift,
            "metadata_drift": metadata_drift,
            "unowned": sorted(unowned),
            "gaps": gaps,
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 1 if drift else 0

    # The D1 destination pre-pass already ran above, before this branch and
    # before the --dry-run report, over the same destination list.
    #
    # An OSError raised anywhere in this write lane (a read-only
    # destination directory, a full filesystem) is reported as a clean
    # refusal rather than a traceback -- every write here is atomic or
    # idempotent to redo, so the tree may be partially written and a full
    # rerun repairs it (`_report_write_failure`).
    try:
        if not is_lang_scoped:
            write_pages(new_pages, args.output_dir, args.verbose)
            write_meta(new_manifest, args.output_dir)
        else:
            # ADR-0131 clause 14 D3: owner-scoped write. Only this key's pages are written; only
            # this key's previously-owned pages whose source vanished are
            # pruned; every other owner's bytes are untouched.
            resolved_output.mkdir(parents=True, exist_ok=True)
            for page in new_pages:
                dest = resolved_output / page.path
                dest.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write_text(dest, page.body)
            new_doc_paths = {p.path for p in new_pages}
            to_prune = (prune_scope_doc_paths or set()) - new_doc_paths
            for doc_path in sorted(to_prune):
                # Second layer: doc_path already passed
                # `_validate_metadata_row` above, so this never refuses in
                # practice; it is the same guard every other doc_path-to-
                # filesystem-path site uses, so a future change to either
                # check keeps them in lock-step.
                try:
                    victim = _owned_page_path(resolved_output, doc_path)
                except ExtractionRefusal as refusal:
                    print(
                        f"extract-code-docs: refusing to prune "
                        f"{_sanitize_refusal_text(refusal.path)} "
                        f"({_sanitize_refusal_text(refusal.cause)})",
                        file=sys.stderr,
                    )
                    continue
                if victim.is_symlink():
                    print(
                        f"extract-code-docs: refusing to prune "
                        f"{_sanitize_refusal_text(str(victim))} "
                        f"(symlink; not indexed by this run)",
                        file=sys.stderr,
                    )
                    continue
                try:
                    victim_st = victim.lstat()
                except FileNotFoundError:
                    continue
                # Twin of the same guard in write_pages' full-run prune
                # loop: a special file (FIFO, socket, device) standing at
                # an owned doc_path is never a page this run wrote, so it
                # is reported and left alone rather than silently skipped
                # by a bare `is_file()`.
                if not stat.S_ISREG(victim_st.st_mode):
                    print(
                        f"extract-code-docs: refusing to prune "
                        f"{_sanitize_refusal_text(str(victim))} "
                        f"(not a regular file; not indexed by this run)",
                        file=sys.stderr,
                    )
                    continue
                victim.unlink()
            # Report strays (files with no owning row in the final set)
            # without pruning them. Same scan as the --lang dry-run's
            # `unowned` list (`_classify_output_dir_entries`), so both
            # lanes see the same file set.
            final_doc_paths = {p.path for p in final_pages}
            stray_regular, stray_non_regular = _classify_output_dir_entries(
                resolved_output, final_doc_paths
            )
            for rel in sorted(stray_regular + stray_non_regular):
                print(
                    f"extract-code-docs: {_sanitize_refusal_text(rel)} is not "
                    f"indexed by this --lang run (a full extract-code-docs run "
                    f"will prune it if it is stale).",
                    file=sys.stderr,
                )
            _atomic_write_text(resolved_output / "index.md", _render_index_text(final_pages))
            write_meta(new_manifest, resolved_output)
    except OSError as exc:
        return _report_write_failure(exc)
    diff = diff_manifests(old_manifest, new_manifest)

    if provenance_by_key:
        # The ` <N> gap(s).` suffix is present whenever a
        # selected key is API-2, INCLUDING N=0 -- never folded back to a
        # bare "." just because this run found no gaps.
        gaps = sum(len((row.get("gaps") or [])) for row in new_manifest["pages"])
        suffix = f" {gaps} gap(s)."
    else:
        suffix = "."
    print(
        f"extract-code-docs: wrote {len(new_pages)} page(s) "
        f"({len(diff['added'])} added, {len(diff['changed'])} changed, "
        f"{len(diff['removed'])} removed)" + suffix
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
