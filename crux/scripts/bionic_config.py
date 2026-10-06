#!/usr/bin/env python3
"""bionic_config.py — loader for the repo-root `.bionic.yml` layout config.

ADR-0044: `.bionic.yml` is the layout source of truth and supersedes the
legacy `.crux` file (ADR-0032), which is still read for back-compat. This is
the canonical module; `crux_config.py` is a thin back-compat shim re-exporting
from here.

Per ADR-0044 (superseding ADR-0032), a repository MAY commit a `.bionic.yml`
file at its root to configure per-repo layout; a legacy `.crux` is still read
for back-compat. Legacy example (a `.crux` naming the historical default):

    # .crux — committed per-project crux configuration (config_version 1)
    config_version: "1"
    docs_dir: docs             # repo-root-relative docs tree location
    artifact_prefix: ""        # optional id prefix: CRX -> CRX-PB-0040

This module is the SINGLE implementation of `.bionic.yml`/`.crux` parsing +
validation. The CLI (`bionic-config.py`, with `crux-config.py` as a back-compat
delegator) and every script consumer import from here so the textual +
containment validation always runs; prose skills shell out to the CLI rather
than reading the YAML ad hoc.

Resolution contract (ADR-0044 §3, as amended by ADR-0059):
- Repo root only — no upward walk, no `git rev-parse`.
- Absent both files -> content-validated bare-directory discovery, then the
  default `bionic`; the resolved source is reported as "discovery:<dir>".
- Malformed file or failed validation -> raise BionicConfigError (callers
  exit 1) — never fall back to defaults.
- Unknown top-level keys are ignored (forward compat).
- Precedence (consumer-enforced): an explicit CLI path flag overrides the
  value derived from this config; consumers load the config only to fill
  flags the user left unset. This module has no flag awareness of its own.

Naming note: this is the repo-root `.crux` FILE (committed project config) —
distinct from the user-home `~/.crux/` DIRECTORY (uncommitted secrets, per
ADR-0002 / crux_env.py).
"""

from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath

try:  # plain import works when scripts/ is on sys.path (CLI invocation)
    from _yaml_min import load_yaml
except ImportError:  # package-context import (e.g. the test suite)
    import importlib.util as _ilu

    _spec = _ilu.spec_from_file_location(
        "_yaml_min", Path(__file__).resolve().parent / "_yaml_min.py"
    )
    _yaml_min = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_yaml_min)
    load_yaml = _yaml_min.load_yaml

try:  # plain import works when scripts/ is on sys.path (CLI invocation)
    from untrusted import parse_problem, redact
except ImportError:  # by-path load (check_observations / check_invariants)
    import importlib.util as _ilu

    _spec = _ilu.spec_from_file_location(
        "untrusted", Path(__file__).resolve().parent / "untrusted.py"
    )
    _untrusted = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_untrusted)
    parse_problem = _untrusted.parse_problem
    redact = _untrusted.redact

BIONIC_CONFIG_FILENAME = ".bionic.yml"  # ADR-0044: the layout source of truth
CONFIG_FILENAME = ".crux"  # ADR-0032: superseded by .bionic.yml, read as legacy
DEFAULT_DOCS_DIR = "bionic"  # ADR-0059: bionic/ IS the tree; discovery protects legacy docs/ trees
LEGACY_DOCS_DIR = "docs"  # the pre-ADR-0059 default, still discovered and honored
SUPPORTED_CONFIG_VERSIONS = ("1",)

# 2-10 chars total: long enough for org codes (JIRA-style), short enough that
# prefixed filenames stay well under the ~60-char slug budget (AGENTS.md §9).
PREFIX_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
# Reserved artifact type tokens — `PB-PB-0040` is human-hostile (ADR-0032 §1).
RESERVED_PREFIXES = frozenset({"PB", "ADR", "RUN", "BRIEF"})
# Execution-adjacent roots a docs tree must never land in (ADR-0032 §1):
# .git is VCS metadata; .github/workflows (and the editor/agent config dirs
# below) execute committed content on open/clone/CI. Only the FIRST segment
# matters: these tools read their config exclusively from repo-root-level
# dirs, so a nested foo/.github is inert. Membership is checked casefolded
# (macOS/Windows filesystems are case-insensitive: .GITHUB IS .github) and
# re-checked against the RESOLVED path (a committed in-repo symlink could
# otherwise route past the textual check).
DENYLISTED_FIRST_SEGMENTS = frozenset(
    {".git", ".github", ".claude", ".vscode", ".husky", ".githooks", ".idea", ".cursor"}
)


class BionicConfigError(Exception):
    """Raised on any `.crux` parse or validation failure. Fail-loud: callers
    must surface this (exit 1), never fall back to defaults."""


class BionicConfig:
    """Resolved per-repo configuration.

    `docs_root` is resolved exactly once at load time (ADR-0032 symlink
    policy); consumers MUST operate on it rather than re-resolving the raw
    `docs_dir` string per operation.

    Deliberately NOT a dataclass: consumers import this module via
    path-based sibling loaders that don't register it in ``sys.modules``,
    and Python 3.14's dataclass machinery requires the defining module to be
    importable from there.
    """

    __slots__ = (
        "config_version", "docs_dir", "artifact_prefix", "repo_root", "docs_root",
        "source", "arch_stack", "arch_extractors", "arch_decision_index_mode",
    )

    def __init__(
        self,
        config_version: str,
        docs_dir: str,
        artifact_prefix: str,
        repo_root: Path,  # resolved
        docs_root: Path,  # resolved once, containment-checked
        source: str,  # ".bionic.yml" | ".crux" | "discovery:<dir>" | combined forms
        arch_stack: str | None = None,          # ADR-0066: arch stack-pack pin
        arch_extractors: dict | None = None,    # ADR-0066: per-concern override map
        arch_decision_index_mode: str = "complete",  # ADR-0062: decision-index curation mode
    ) -> None:
        self.config_version = config_version
        self.docs_dir = docs_dir
        self.artifact_prefix = artifact_prefix
        self.repo_root = repo_root
        self.docs_root = docs_root
        self.source = source
        self.arch_stack = arch_stack
        self.arch_extractors = arch_extractors if arch_extractors is not None else {}
        self.arch_decision_index_mode = arch_decision_index_mode

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return (
            f"BionicConfig(config_version={self.config_version!r}, docs_dir={self.docs_dir!r}, "
            f"artifact_prefix={self.artifact_prefix!r}, source={self.source!r})"
        )

    def prefixed(self, base_id: str) -> str:
        """Apply the configured prefix to a bare artifact id.

        prefixed("PB-0040") -> "CRX-PB-0040" under prefix CRX, or "PB-0040"
        when no prefix is configured. The prefix layer is orthogonal to
        number allocation (ADR-0032 §3).

        `base_id` MUST be a bare id (starting with its type token) — passing
        an already-prefixed id would mint a double-prefixed spelling that no
        schema accepts, so it is rejected here rather than propagated.
        """
        if not re.match(r"^(?:PB|ADR|RUN|BRIEF)-", base_id):
            raise ValueError(f"prefixed() requires a bare artifact id (got {base_id!r})")
        if self.artifact_prefix:
            return f"{self.artifact_prefix}-{base_id}"
        return base_id


def _validate_docs_dir_text(value: str) -> None:
    """ADR-0032 §1 textual layer — raw-string rules, no filesystem access."""
    if not isinstance(value, str):
        raise BionicConfigError(f"docs_dir must be a string, got {type(value).__name__}")
    if not value or not value.strip() or value != value.strip():
        raise BionicConfigError("docs_dir must be a non-empty string without surrounding whitespace")
    if value.startswith("/") or value.startswith("~"):
        raise BionicConfigError(f"docs_dir must be repo-root-relative (got {value!r}); absolute and ~-leading paths are rejected")
    if PureWindowsPath(value).is_absolute() or re.match(r"^[A-Za-z]:", value):
        raise BionicConfigError(f"docs_dir must be repo-root-relative (got {value!r}); Windows drive/UNC paths are rejected")
    if "\\" in value:
        raise BionicConfigError(f"docs_dir must use '/' separators only (got {value!r})")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise BionicConfigError("docs_dir contains control characters")
    if any(ch.isspace() for ch in value):
        raise BionicConfigError(f"docs_dir must not contain whitespace (got {value!r})")
    segments = value.split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        raise BionicConfigError(f"docs_dir must not contain empty, '.', or '..' segments or a trailing slash (got {value!r})")
    for seg in segments:
        if not re.match(r"^[A-Za-z0-9_.][A-Za-z0-9._-]*$", seg):
            raise BionicConfigError(
                f"docs_dir segment {seg!r} must match ^[A-Za-z0-9_.][A-Za-z0-9._-]*$; "
                "shell metacharacters, leading dashes, and non-ASCII are rejected "
                "(the value flows into shell-composed filesystem operations)"
            )
    if segments[0].casefold() in DENYLISTED_FIRST_SEGMENTS:
        raise BionicConfigError(f"docs_dir must not live under {segments[0]}/ (execution-adjacent directory)")


def validate_docs_dir_override(root: Path, docs_dir: str) -> Path:
    """Validate an operator-supplied `docs_dir` override and return `root / docs_dir`.

    The one implementation behind every CLI `--docs-dir` flag that goes on to
    WRITE. A flag value is exactly as attacker-influenceable as a committed
    `.bionic.yml` value, so it takes the same verdict — one value, one answer,
    whichever door it arrives through. `survey.py` learned that once
    (`--docs-dir .github` was accepted where the identical configured value was
    refused); `scaffold-survey-sheet.py` and `signoff-survey.py` shipped the
    plain join `root / docs_dir` with no check at all, and `--docs-dir
    ../victim-tree` wrote a sheet and moved a counter outside the repository
    root. Their own write guards could not catch it: every write is
    `contained_under=tree` and the traversed path IS `tree`, so the check was
    self-referential.

    Both legs of `load_config`'s docs_dir handling, in the same order:

      * the TEXTUAL layer — `_validate_docs_dir_text`, unmodified and shared
        rather than re-implemented, so a nested `docs/architecture` and a
        leading-dot segment stay legal exactly as the config validator makes
        them legal, and the per-segment grammar plus the execution-adjacent
        first-segment denylist apply here too;
      * the RESOLVED layer — the join must resolve to a PROPER subdirectory of
        the resolved root, and the denylist is re-applied to the resolved first
        segment, so an in-repo symlink cannot route the value past the textual
        check into `.github/` or out of the repository entirely.

    The RETURNED path is the plain join, not the resolved one: containment is
    the check, not an input to the value, matching
    `summaries_projection.resolve_tree`, so callers that key maps or compute
    display paths on the joined spelling are unaffected.

    Existence is NOT checked here — a caller that needs the tree to be present
    says so itself, with the refusal its own exit lanes describe.

    Raises BionicConfigError.
    """
    _validate_docs_dir_text(docs_dir)
    root_resolved = Path(root).resolve()
    joined = Path(root) / docs_dir
    target = joined.resolve()
    if target == root_resolved or not target.is_relative_to(root_resolved):
        raise BionicConfigError(
            f"docs_dir {docs_dir!r} resolves to {target}, which is not contained "
            f"under the repository root {root_resolved} — a symlinked or escaping "
            "tree dir is refused"
        )
    resolved_first = target.relative_to(root_resolved).parts[0]
    if resolved_first.casefold() in DENYLISTED_FIRST_SEGMENTS:
        raise BionicConfigError(
            f"docs_dir {docs_dir!r} resolves into {resolved_first}/ (execution-adjacent directory)"
        )
    return joined


def _validate_prefix(value: str | None) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise BionicConfigError(f"artifact_prefix must be a string, got {type(value).__name__}")
    if value == "":
        return ""
    if not PREFIX_PATTERN.match(value):
        raise BionicConfigError(f"artifact_prefix must match ^[A-Z][A-Z0-9]{{1,9}}$ (got {value!r})")
    if value in RESERVED_PREFIXES:
        raise BionicConfigError(f"artifact_prefix must not be a reserved artifact type token (got {value!r})")
    return value


# ADR-0066: `arch_stack` is a stack-pack name (a lowercase token); the deriver,
# not this loader, decides whether the named pack is registered (a pin naming an
# unregistered pack is a fail-closed error at derive time). This layer only
# rejects shapes that could not be a pack name at all.
_ARCH_STACK_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def _validate_arch_stack(value) -> str | None:
    """ADR-0066: validate the optional `arch_stack` pin. None/absent -> None."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise BionicConfigError(f"arch_stack must be a string, got {type(value).__name__}")
    if not _ARCH_STACK_PATTERN.match(value):
        raise BionicConfigError(
            f"arch_stack must match ^[a-z][a-z0-9_-]{{0,31}}$ (got {value!r})"
        )
    return value


_ARCH_DECISION_INDEX_MODES = frozenset({"complete", "curated"})


def _validate_arch_decision_index_mode(value) -> str:
    """ADR-0062: validate the optional `arch_decision_index_mode` key.

    Fail-closed: None/absent -> "complete" (the default; ADR-0062 AC-6
    requires no silent change in behavior for repos that do not opt in). A
    closed allowlist of exactly two literals — stricter than `arch_stack`'s
    regex, which is correct here because the value space IS exactly two
    literals, not an open namespace of pack names.
    """
    if value is None:
        return "complete"
    if not isinstance(value, str):
        raise BionicConfigError(
            f"arch_decision_index_mode must be a string, got {type(value).__name__}"
        )
    if value not in _ARCH_DECISION_INDEX_MODES:
        raise BionicConfigError(
            f"arch_decision_index_mode must be one of {sorted(_ARCH_DECISION_INDEX_MODES)} (got {value!r})"
        )
    return value


# ADR-0096 clause 10 retired `arch_confidence_threshold` along with the grade
# scale it thresholded. There is no validator here any more and no fail-closed
# branch: a tree that still sets the key falls through to the forward-compat
# valve below ("Unknown keys: ignored by design"), which is the §14.1 contract
# for exactly this case. Refusing a key a previous version instructed users to
# write would turn an upgrade into an outage over a value that no longer feeds
# anything.


def _validate_arch_extractors(value) -> dict:
    """ADR-0066: validate the optional `arch_extractors` per-concern override map.

    A mapping of concern name -> "rel/path/module.py:function". This layer checks
    only that it is a flat string->string mapping; the deriver applies the
    fail-closed path/import/signature guard when (and only when) overrides are
    opted in via CRUX_ARCH_ALLOW_OVERRIDES=1.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise BionicConfigError(
            f"arch_extractors must be a mapping, got {type(value).__name__}"
        )
    for k, v in value.items():
        if not isinstance(k, str) or not k:
            raise BionicConfigError(f"arch_extractors keys must be non-empty strings (got {k!r})")
        if not isinstance(v, str) or not v.strip():
            raise BionicConfigError(
                f"arch_extractors[{k!r}] must be a non-empty string 'rel/path.py:function' (got {v!r})"
            )
    return dict(value)


def _select_config_file(root_raw: Path) -> tuple[Path | None, str]:
    """ADR-0044 §3 precedence: `.bionic.yml` > legacy `.crux` > convention.

    Returns (path_to_read, source_label). `path_to_read` is None when no
    config file is present (source="defaults". Note: load_config overwrites this when no config file exists; the CLI emits "discovery:<dir>"). A present-but-broken file
    (directory, or dangling symlink) is fail-loud per its own tier — it is
    intended config, so we never silently fall through to a lower tier.
    """
    for filename in (BIONIC_CONFIG_FILENAME, CONFIG_FILENAME):
        cfg_path = root_raw / filename
        if cfg_path.is_dir():
            raise BionicConfigError(f"{cfg_path} is a directory; expected a YAML file")
        if cfg_path.is_symlink() and not cfg_path.exists():
            # A committed-but-dangling symlink is intended config, not absence;
            # silently falling through here is the split-the-tree failure the
            # fail-loud rule prohibits.
            raise BionicConfigError(f"{cfg_path} is a dangling symlink; expected a YAML file")
        if cfg_path.exists():
            return cfg_path, filename
    return None, "defaults"


def _read_and_validate_config(cfg_path: Path, source: str) -> tuple[str, str, str, str | None, dict, str]:
    """Parse + validate one config file (shared by `.bionic.yml` and `.crux`).

    ADR-0044 §2: the two files share the same keys, grammar, and validation —
    only the discovery/precedence differs. Returns (config_version, docs_dir,
    artifact_prefix, arch_stack, arch_extractors, arch_decision_index_mode).
    Raises BionicConfigError on any failure.
    """
    text = cfg_path.read_text(encoding="utf-8")
    if len(text) > 65536:
        raise BionicConfigError(f"{cfg_path} exceeds 64 KiB; not a plausible config file")
    for lineno, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0]
        if "&" in code or "*" in code:
            # The grammar never needs YAML anchors/aliases, and pyyaml's
            # safe_load (the load_yaml fast path) expands alias bombs
            # exponentially — reject the tokens outright.
            raise BionicConfigError(
                f"{cfg_path}:{lineno}: YAML anchors/aliases ('&'/'*') are not supported in {source}"
            )
    try:
        raw = load_yaml(text)
    except Exception as exc:  # ValueError/YamlCapabilityError (fallback) or pyyaml YAMLError
        # repr-wrap: a parse error can embed raw file bytes (incl. terminal
        # escapes) from an attacker-influenced committed file.
        raise BionicConfigError(
            parse_problem("config file", cfg_path.name, exc)) from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise BionicConfigError(f"{source} must be a YAML mapping at top level, got {type(raw).__name__}")

    if "config_version" not in raw:
        raise BionicConfigError(f"{source} is missing the required config_version key")
    config_version = raw["config_version"]
    if not isinstance(config_version, str):
        raise BionicConfigError(
            f'config_version must be a quoted string (got {redact(config_version)}); '
            'write config_version: "1"'
        )
    if config_version not in SUPPORTED_CONFIG_VERSIONS:
        raise BionicConfigError(
            f"unsupported config_version {redact(config_version)}; this plugin "
            f"supports {list(SUPPORTED_CONFIG_VERSIONS)}"
        )

    # ADR-0059 precedence clause 3: a config present but carrying no `docs_dir`
    # falls through to discovery and does NOT take the new default. A keyless or
    # hand-truncated config beside an established `docs/` tree must not resolve
    # to a `bionic/` that does not exist.
    docs_dir = raw.get("docs_dir")
    if docs_dir is None:
        docs_dir = None  # sentinel: caller runs discovery
    prefix = _validate_prefix(raw.get("artifact_prefix"))
    arch_stack = _validate_arch_stack(raw.get("arch_stack"))  # ADR-0066
    arch_extractors = _validate_arch_extractors(raw.get("arch_extractors"))  # ADR-0066
    arch_decision_index_mode = _validate_arch_decision_index_mode(
        raw.get("arch_decision_index_mode")
    )  # ADR-0062
    # Unknown keys: ignored by design (forward compat valve).
    return (
        config_version, docs_dir, prefix, arch_stack, arch_extractors,
        arch_decision_index_mode,
    )



# ─────────────────── ADR-0059 bare-directory discovery ────────────────────


def _is_crux_manifest(path: Path) -> bool:
    """True iff `path` is a manifest belonging to a crux tree.

    ADR-0059 precedence clause 4: a manifest counts only if it parses AND
    carries BOTH `schema_version` and a `concerns_enabled` list. The pair is the
    discriminator. `schema_version` alone is a generic key another tool's
    `docs/manifest.yml` could plausibly carry, and a false ambiguity against a
    real tree is exactly what the both-valid refusal exists to avoid.

    Parsing is deliberately shallow — a line scan, not a YAML load — because
    this module is stdlib-only and discovery must not acquire a PyYAML
    dependency. A malformed manifest simply does not count.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    has_version = re.search(r"^schema_version\s*:", text, re.MULTILINE) is not None
    has_concerns = re.search(r"^concerns_enabled\s*:", text, re.MULTILINE) is not None
    return has_version and has_concerns


def _parse_migration_marker(marker: Path) -> str:
    """Read the shape written by the tagged 4→5 migrator, without changing it.

    Its writer emits source, step, then inventory lines. A lone ``source:``
    line is not a recovery receipt and must not resolve two real trees.
    """
    if marker.is_symlink():
        raise BionicConfigError(f"invalid migration marker {marker}: symlink")
    try:
        lines = marker.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise BionicConfigError(f"invalid migration marker {marker}: unreadable") from exc
    if (len(lines) < 3
            or not re.fullmatch(r"source: [^\s]+", lines[0])
            or not re.fullmatch(r"step: [1-6]", lines[1])
            or lines[2] != "inventory:"
            or any(not re.fullmatch(r"  - .+", line) for line in lines[3:])):
        raise BionicConfigError(f"invalid migration marker {marker}: malformed tagged receipt")
    source = lines[0].removeprefix("source: ")
    try:
        _validate_docs_dir_text(source)
    except BionicConfigError as exc:
        raise BionicConfigError(f"invalid migration marker {marker}: {exc}") from exc
    return source


def _read_migration_marker(root: Path) -> str | None:
    """Return a recorded source, refusing malformed or conflicting receipts."""
    found: list[str] = []
    for candidate in (DEFAULT_DOCS_DIR, LEGACY_DOCS_DIR):
        marker = root / candidate / ".migrating"
        if marker.exists() or marker.is_symlink():
            found.append(_parse_migration_marker(marker))
    if len(found) > 1:
        raise BionicConfigError("multiple migration markers found; refusing to choose a source")
    return found[0] if found else None


def discover_docs_dir(root: Path) -> str:
    """Bare-directory discovery (ADR-0044 §3, specified there and implemented here).

    Evaluation order matters and is the one round-2 council review corrected:
    the both-valid refusal is evaluated BEFORE any single-tree selection, so
    discovery can never silently prefer one real tree while the migrate rung
    calls the same state ambiguous.

      4a. both valid  -> refuse, unless a migration marker accounts for it
      4b. exactly one -> that directory
      4c. none        -> DEFAULT_DOCS_DIR (bionic)
    """
    valid = [d for d in (LEGACY_DOCS_DIR, DEFAULT_DOCS_DIR)
             if _is_crux_manifest(root / d / "manifest.yml")]
    marked = _read_migration_marker(root)

    if len(valid) > 1:
        if marked in valid:
            # An in-flight migration: resolution returns the recorded source —
            # the tree the migration has not yet finished leaving, which is the
            # same directory the manifest-last ordering keeps authoritative.
            return marked
        raise BionicConfigError(
            "two crux trees found: "
            + " and ".join(f"{d}/manifest.yml" for d in valid)
            + ". Refusing to guess which is authoritative. A valid partial "
            "migration must be resumed or abandoned with crux v3.23.2; "
            "otherwise reconcile the trees or name the tree explicitly in "
            ".bionic.yml (docs_dir:)."
        )
    if len(valid) == 1:
        # The legacy tree wins when it is the only one — an established docs/
        # tree keeps working with zero config and no migration.
        return valid[0]
    return DEFAULT_DOCS_DIR

def load_config(repo_root: str | Path | None = None, *, require_tree: bool = False) -> BionicConfig:
    """Load and validate the repo-root layout config.

    ADR-0044 §3 precedence as amended by ADR-0059: `.bionic.yml` carrying
    `docs_dir` wins, then a legacy `.crux` carrying it, then a config present
    but keyless falls through to discovery, then bare-directory discovery over
    valid crux manifests. Any parse/validation failure -> BionicConfigError. The
    containment check (resolve-then-contain, proper subdirectory) runs on EVERY
    call, before any consumer reads or writes under the tree.

    `require_tree` implements ADR-0059 precedence clause 1's refusal: when True,
    a resolved `docs_dir` that does not exist or holds no valid crux manifest
    raises rather than being trusted and failing somewhere downstream. It
    defaults to False because `init-docs` legitimately resolves config for a
    tree it is about to create — bootstrap is the one caller for which "the
    tree is not there yet" is the expected state. Every caller that READS the
    tree passes True.
    """
    root_raw = Path(repo_root) if repo_root is not None else Path.cwd()
    if not root_raw.is_dir():
        raise BionicConfigError(f"repo root is not a directory: {root_raw}")
    root = root_raw.resolve()

    cfg_path, source = _select_config_file(root_raw)
    if cfg_path is None:
        config_version = SUPPORTED_CONFIG_VERSIONS[-1]
        docs_dir = discover_docs_dir(root_raw)
        prefix = ""
        arch_stack = None
        arch_extractors: dict = {}
        arch_decision_index_mode = "complete"
        source = f"discovery:{docs_dir}"
    else:
        (
            config_version,
            docs_dir,
            prefix,
            arch_stack,
            arch_extractors,
            arch_decision_index_mode,
        ) = _read_and_validate_config(cfg_path, source)
        if docs_dir is None:
            # Clause 3: config present, `docs_dir` absent -> discovery, never the
            # new default. The config still supplies config_version and prefix.
            docs_dir = discover_docs_dir(root_raw)
            source = f"{source}+discovery:{docs_dir}"

    _validate_docs_dir_text(docs_dir)

    # Containment layer (ADR-0032 §1): resolve once, require a PROPER
    # subdirectory of the resolved repo root. A docs_dir that is or traverses
    # a symlink is permitted iff its resolved target stays under the root;
    # resolving to the root itself is rejected (scripts that prune under the
    # docs tree must never treat the whole repo as the tree).
    target = (root_raw / docs_dir).resolve()
    if target == root or not target.is_relative_to(root):
        raise BionicConfigError(
            f"docs_dir {docs_dir!r} resolves to {target}, which is not a proper subdirectory of the repo root {root}"
        )
    # Re-apply the execution-adjacent denylist to the RESOLVED location: a
    # committed in-repo symlink (docs_dir: x, x -> .github/workflows) passes
    # the textual check and repo-root containment but must not land there.
    resolved_first = target.relative_to(root).parts[0]
    if resolved_first.casefold() in DENYLISTED_FIRST_SEGMENTS:
        raise BionicConfigError(
            f"docs_dir {docs_dir!r} resolves into {resolved_first}/ (execution-adjacent directory)"
        )

    if require_tree:
        # ADR-0059 clause 1: a config that points at nothing is a wrong layout,
        # and a wrong layout must be visible at the point of resolution.
        if not target.is_dir():
            raise BionicConfigError(
                f"docs_dir {docs_dir!r} resolves to {target}, which does not exist. "
                "The configured tree is missing; run init-docs, fix .bionic.yml, or "
                "finish the migration that moved it."
            )
        if not _is_crux_manifest(target / "manifest.yml"):
            raise BionicConfigError(
                f"docs_dir {docs_dir!r} resolves to {target}, which holds no valid crux "
                "manifest (a manifest.yml carrying schema_version and concerns_enabled). "
                "Refusing to operate on a directory that is not a crux tree."
            )

    return BionicConfig(
        config_version=config_version,
        docs_dir=docs_dir,
        artifact_prefix=prefix,
        repo_root=root,
        docs_root=target,
        source=source,
        arch_stack=arch_stack,
        arch_extractors=arch_extractors,
        arch_decision_index_mode=arch_decision_index_mode,
    )



# ───────────────────── ADR-0059 schema-version gate ───────────────────────

SUPPORTED_SCHEMA_VERSION = "5"  # ADR-0059: bionic/ tree + unified invariants concern
KNOWN_OLDER_SCHEMA_VERSIONS = ("2", "3", "4")
RECOVERY_TAG = "v3.23.2"
RECOVERY_COMMIT = "08ee30ec2f1d1b4b0ce970f2e1582bb4f83cd20d"


class SchemaVersionError(BionicConfigError):
    """The tree's schema_version is outside this plugin's supported range."""


def read_schema_version(docs_root: Path) -> str | None:
    """Return the tree's `schema_version`, or None when it cannot be read.

    Shallow line-scan for the same reason `_is_crux_manifest` uses one: this
    module stays stdlib-only, so the gate cannot require PyYAML.
    """
    try:
        text = (docs_root / "manifest.yml").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    m = re.search(r"""^schema_version\s*:\s*["']?([^"'\s#]+)["']?""", text, re.MULTILINE)
    return m.group(1) if m else None


def require_schema_version(docs_root: Path) -> str:
    """Refuse unless the tree is at the supported schema version (ADR-0137).

    Every command that reads the tree calls this before reading, writing, or
    reporting. An in-flight marker is old migration state even if its manifest
    already says 5. Keep the marker intact for the tagged recovery tool.
    """
    docs_root = Path(docs_root)
    marker_paths = [docs_root / ".migrating"]
    if docs_root.name in (DEFAULT_DOCS_DIR, LEGACY_DOCS_DIR):
        sibling = (LEGACY_DOCS_DIR if docs_root.name == DEFAULT_DOCS_DIR
                   else DEFAULT_DOCS_DIR)
        marker_paths.append(docs_root.parent / sibling / ".migrating")
    for marker in marker_paths:
        if marker.exists() or marker.is_symlink():
            _parse_migration_marker(marker)
            raise SchemaVersionError(
                f"in-flight migration marker {marker} remains. Use crux "
                f"{RECOVERY_TAG} ({RECOVERY_COMMIT}) to resume or abandon "
                "that migration on a copy before using this version."
            )
    found = read_schema_version(docs_root)
    if found == SUPPORTED_SCHEMA_VERSION:
        return found
    if found is None:
        raise SchemaVersionError(
            f"{docs_root}/manifest.yml is missing or carries no schema_version; "
            "this does not look like a crux tree."
        )
    if found in KNOWN_OLDER_SCHEMA_VERSIONS:
        raise SchemaVersionError(
            f"tree schema_version is {found!r}; this plugin requires "
            f"{SUPPORTED_SCHEMA_VERSION!r}. Recover schemas 2–4 with crux "
            f"{RECOVERY_TAG} ({RECOVERY_COMMIT}) on a copy, then validate and "
            "upgrade. This version cannot migrate that tree."
        )
    raise SchemaVersionError(
        f"tree schema_version is {found!r}, which this plugin does not recognize "
        f"(supported: {SUPPORTED_SCHEMA_VERSION!r}). Inspect the tree and align "
        "plugin versions before proceeding; no migration route is known."
    )


def resolve_tree_name(root: Path | str = ".") -> str:
    """Return the tree directory name for `root`.

    The wrapper vendored scripts use instead of hardcoding a literal directory.
    Per the schema's denotation clause a literal `docs/` in prose DENOTES the
    resolved tree; this is that resolution in code.

    **It raises.** An earlier version swallowed every `BionicConfigError` and
    returned the default, which an independent review rejected unanimously: that
    discarded the both-valid refusal this module exists to make, and redirected
    an established `docs/` tree to `bionic/` whenever config was malformed. A
    caller that then WROTE would have written into the wrong tree. A resolution
    failure is not a detail a convenience wrapper may absorb — every fallback
    here pointed the same direction, toward the new default, which is the
    direction that loses data.

    The one tolerated fallback is the genuinely empty case: no config and no
    tree, where `load_config` already returns the greenfield default without
    raising. Nothing is swallowed to reach it.
    """
    return load_config(root).docs_dir


# Back-compat aliases (ADR-0044): the pre-rename names still resolve.
CruxConfig = BionicConfig
CruxConfigError = BionicConfigError
