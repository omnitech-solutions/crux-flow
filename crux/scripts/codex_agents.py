"""Generate Codex-native subagent TOML from crux's role definitions."""

from __future__ import annotations

import importlib.util
import os
import re
import stat
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

class _BrokenInstall(Exception):
    """Stands in for `models_catalog.SpecViolation` when that module is absent.

    Only bound when `models_catalog.py` could not be loaded at all, which means
    the install is missing half of a pair that ships together. Nothing catches
    this name specifically — it exists so `SpecViolation` is always importable
    from this module, which is what lets the failure be reported at first use
    instead of crashing the installer's own import statement.
    """


class _DeferredCatalogFault:
    """A `models_catalog` stand-in that raises on first real use.

    Every attribute access raises, carrying the original import error as the
    cause. `SpecViolation` is the one attribute served normally, so a caller
    that catches `models_catalog.SpecViolation` still has a class to catch.
    """

    def __init__(self, path: Path, cause: BaseException) -> None:
        self._path = path
        self._cause = cause
        self.SpecViolation = _BrokenInstall

    def __getattr__(self, name: str) -> Any:
        raise _BrokenInstall(
            f"cannot load {self._path}: {self._cause} — this file ships beside "
            "codex_agents.py and the install is missing it"
        ) from self._cause


# Import the shared catalog reader by name when scripts/ is on sys.path (the
# regenerator and the installer both put it there), and by file location
# otherwise. Mirrors how validate-promptbook.py reaches _yaml_min.
#
# THIS BLOCK MUST NOT RAISE. `install-codex-agents` imports names from this
# module at module level and turns exactly one exception type into structured
# exit-2 JSON. An exception raised while THIS module executes happens before
# that import completes, so no `except SpecViolation` can catch it: the
# installer dies with a traceback and exit 1. `spec_from_file_location` on an
# absent `models_catalog.py` raised FileNotFoundError from `exec_module` and
# did exactly that. The failure is DEFERRED to first use instead.
#
# Only the staged release artifact can see this; the dev tree always has the
# file on the path.
try:  # pragma: no cover - one branch per caller's sys.path
    import models_catalog
except ImportError:  # pragma: no cover
    models_catalog = None

if models_catalog is None:
    _CATALOG_PATH = Path(__file__).resolve().parent / "models_catalog.py"
    _spec = importlib.util.spec_from_file_location("models_catalog", _CATALOG_PATH)
    if _spec is not None and _spec.loader is not None and _CATALOG_PATH.is_file():
        models_catalog = importlib.util.module_from_spec(_spec)
        # Register before exec: `dataclasses` resolves a class's own module out
        # of sys.modules while processing it, and an unregistered module makes
        # that lookup return None.
        sys.modules.setdefault("models_catalog", models_catalog)
        try:
            _spec.loader.exec_module(models_catalog)
        except Exception as _exc:  # noqa: BLE001 - re-raised on use, not here
            sys.modules.pop("models_catalog", None)
            models_catalog = _DeferredCatalogFault(_CATALOG_PATH, _exc)
    else:
        models_catalog = _DeferredCatalogFault(
            _CATALOG_PATH, FileNotFoundError(str(_CATALOG_PATH))
        )


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = PLUGIN_ROOT / "agents"
OUTPUT_PREFIX = "crux-"

# Re-exported, not redefined. `install-codex-agents` catches exactly one
# exception type and turns it into structured exit-2 JSON; a second type would
# escape as a traceback, so a catalog fault and a source fault must be the same
# class.
SpecViolation = models_catalog.SpecViolation


# TOML basic-string escapes (TOML v1.0.0 §String). json.dumps is NOT a correct
# TOML encoder: with ensure_ascii it emits UTF-16 surrogate pairs
# (`\uD83D\uDE00`) for astral-plane codepoints, which TOML rejects (an escape
# must be a Unicode *scalar* value). TOML's own escapes are `\uXXXX` (BMP) and
# `\UXXXXXXXX` (astral) — so we encode basic strings ourselves, fail-closed.
_TOML_BASIC_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _toml_basic_string(value: str) -> str:
    """Encode a Python str as a TOML basic string (quoted, correctly escaped).

    Emits printable characters (incl. non-ASCII, incl. astral-plane) literally
    as UTF-8, escapes the TOML control set, and escapes remaining control
    characters as `\\uXXXX` — never a lone/paired surrogate, so the output is
    always valid TOML.
    """
    out = ['"']
    for ch in value:
        if ch in _TOML_BASIC_ESCAPES:
            out.append(_TOML_BASIC_ESCAPES[ch])
        elif ch < " " or ch == "\x7f":  # other C0 controls + DEL
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)  # printable BMP + astral, emitted as literal UTF-8
    out.append('"')
    return "".join(out)


# Provenance (ADR-0046 clause 6a, as narrowed by ADR-0071 §7). The Codex model
# slugs are an external contract owned by OpenAI, not crux. They no longer live
# in this module. A level row provides the default `codex:` cell. An agent row
# may provide a complete `codex:` override with `model`, `reasoning_effort`, and
# dated `verified` and `source` fields. The catalog resolves the override first,
# then the level fallback. Codex custom-agent `model = …` requires a concrete
# catalog slug — a bare `gpt-5.6` is a crux-router-only alias and would not
# resolve — and rule V5(a) refuses any slug absent from the router's `models`
# registry. No live-network validation is ever performed against this contract
# (ADR-0046 clause 6): re-verify by reading the current docs or the `codex`
# catalog, never by calling the service.


@dataclass(frozen=True)
class SourceAgent:
    name: str
    description: str
    tools: frozenset[str]
    body: str
    skills: tuple[str, ...] = ()


@dataclass
class PinnedDirectory:
    """An open agent directory whose identity remains stable across path swaps."""

    path: Path
    _fd: int

    def duplicate_fd(self) -> int:
        return os.dup(self._fd)

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def __enter__(self) -> "PinnedDirectory":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


def _frontmatter_field(frontmatter: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}:[ \t]*(.+)$", frontmatter, re.MULTILINE)
    if not match:
        raise SpecViolation(f"missing {name}: frontmatter field")
    value = match.group(1).strip()
    if not value:
        raise SpecViolation(f"empty {name}: frontmatter field")
    return value


def _frontmatter_skills(frontmatter: str, path: Path) -> tuple[str, ...]:
    """Read the explicit, compact ``skills: [a, b]`` source declaration."""
    declarations = re.findall(r"(?m)^skills:[ \t]*(.*)$", frontmatter)
    if len(declarations) > 1:
        raise SpecViolation(f"{path}: duplicate top-level skills key")
    value = _strip_yaml_comment(_frontmatter_field(frontmatter, "skills")).strip()
    if not (value.startswith("[") and value.endswith("]")):
        raise SpecViolation(f"{path}: skills must use a bracketed list")
    inner = value[1:-1].strip()
    tokens = tuple(token.strip() for token in inner.split(",")) if inner else ()
    if any(not token for token in tokens):
        raise SpecViolation(f"{path}: skills list contains an empty element")
    skills = tuple(_unquote_skill_name(token) for token in tokens)
    if any(not re.fullmatch(r"[a-z0-9][a-z0-9-]*", skill) for skill in skills):
        raise SpecViolation(f"{path}: invalid skills declaration")
    if len(set(skills)) != len(skills):
        raise SpecViolation(f"{path}: duplicate skill declaration")
    return tuple(sorted(skills))


def _unquote_skill_name(token: str) -> str:
    """Accept the YAML single- and double-quoted forms for a skill name."""
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ('"', "'"):
        if token[0] == "'":
            return token[1:-1].replace("''", "'")
        return token[1:-1]
    return token


def _strip_yaml_comment(value: str) -> str:
    """Remove a YAML comment while preserving hashes inside quoted strings."""
    out: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(value):
        char = value[index]
        if quote == '"' and char == "\\" and index + 1 < len(value):
            out.extend((char, value[index + 1]))
            index += 2
            continue
        if quote == "'" and char == "'" and index + 1 < len(value) and value[index + 1] == "'":
            out.extend((char, value[index + 1]))
            index += 2
            continue
        if quote is not None:
            out.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in ('"', "'"):
            quote = char
            out.append(char)
        elif char == "#":
            break
        else:
            out.append(char)
        index += 1
    return "".join(out)


def parse_source(path: Path) -> SourceAgent:
    """Parse the stable source-agent contract used by the Claude projection.

    **[SECURITY:S5] A symlinked source leaf is refused.** Byte-for-byte the
    same reasoning as `opencode_agents.parse_source`: this read is where a
    `crux/agents/*.md` file's content enters a generated Codex agent file, so
    following a planted link would render out-of-tree content into
    `.codex/agents/crux-<role>.toml`. Checked before `read_text` so a dangling
    link is a `SpecViolation` rather than a `FileNotFoundError` traceback.
    Change one, change both.
    """
    if path.is_symlink():
        raise SpecViolation(
            f"{path}: source agent is a symlink — refusing to follow it, because its "
            "target's content would be rendered into the generated agent file"
        )
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(?P<frontmatter>.*?)\n---\n(?P<body>.*)\Z", text, re.DOTALL)
    if not match:
        raise SpecViolation(f"{path}: missing frontmatter block")

    frontmatter = match.group("frontmatter")
    name = _frontmatter_field(frontmatter, "name")
    if name != path.stem:
        raise SpecViolation(f"{path}: name {name!r} does not match filename")

    tools = frozenset(token.strip() for token in _frontmatter_field(frontmatter, "tools").split(",") if token.strip())
    has_edit, has_write = "Edit" in tools, "Write" in tools
    if has_edit != has_write:
        raise SpecViolation(f"{path}: Edit and Write must be granted together")

    body = match.group("body").strip()
    if not body:
        raise SpecViolation(f"{path}: empty agent body")

    return SourceAgent(
        name=name,
        description=_frontmatter_field(frontmatter, "description"),
        tools=tools,
        body=body,
        skills=_frontmatter_skills(frontmatter, path),
    )


def codex_agent_name(source_name: str) -> str:
    """Namespace generated agents so target projects' own roles do not collide."""
    return f"crux_{source_name.replace('-', '_')}"


def codex_agent_filename(source_name: str) -> str:
    return f"{OUTPUT_PREFIX}{source_name}.toml"


# ADR-0092 item 3 projection (Codex, faithful-or-drop): the new agent fields all
# DROP on Codex — maxTurns (no per-subagent turn cap; only the global
# `agents.max_concurrent_threads_per_session`), effort (`model_reasoning_effort`
# resolves from a complete agent override when present, otherwise from the
# models.yml level fallback, so the frontmatter field is Claude-only-effective
# and validate-catalog.py warns when it is set), skills (the portable projection
# omits installation-specific skill paths; installed
# artifacts bind them through `skills.config` below), memory, isolation, and
# disallowedTools (Codex exposes only the coarse `sandbox_mode`, no faithful
# deny target). None is rendered below.
# The Claude dispatch tool `Agent` (renamed from `Task` per ADR-0092 item 4) and
# its restricted `Agent(role)` form project to the binding prose directive
# below: Codex has no spawn tool, so delegation returns to the parent.
def _developer_instructions(source: SourceAgent) -> str:
    return f"""You are the Codex projection of Crux's {source.name} role.

Codex compatibility rules:
- The source contract below may name Claude Code tools. Treat those names as
  capabilities, not literal commands: use only the Codex tools available to this
  session.
- Your configured sandbox is a default boundary. Parent-session overrides can be
  broader, so the source role restrictions remain binding even when Codex permits
  more actions.
- Do not spawn subagents. Codex's default agent depth lets the parent agent
  spawn this direct child but prevents nested delegation. Return any proposed
  delegation to the parent agent instead.
- Use a Crux skill only when it is available in the current Codex session. Report a
  missing capability instead of claiming the skill or another agent was invoked.

{source.body}

Codex execution override (binding): The role contract above originated in Claude
Code and may instruct you to use `Agent` or to delegate to another role. In this
direct child session, do not invoke `Agent` and do not spawn another agent. Return
the proposed handoff, role, and required context to the parent agent, which owns
all Codex delegation at the default agent depth.
"""


def _skill_config(source: SourceAgent, skill_root: Path) -> list[Path]:
    """Resolve declared skills to contained installed resources, fail-closed."""
    try:
        root = skill_root.resolve(strict=True)
    except OSError as exc:
        raise SpecViolation(f"cannot resolve skills root {skill_root}: {exc}") from exc
    if not root.is_dir():
        raise SpecViolation(f"skills root is not a directory: {root}")
    resources: list[Path] = []
    for skill in source.skills:
        candidate = root / skill / "SKILL.md"
        try:
            resource = candidate.resolve(strict=True)
        except OSError as exc:
            raise SpecViolation(f"{source.name}: missing skill resource {candidate}: {exc}") from exc
        try:
            contained = resource.is_relative_to(root)
        except AttributeError:  # pragma: no cover - Python >=3.11
            contained = str(resource).startswith(f"{root}{os.sep}")
        if not contained or not resource.is_file():
            raise SpecViolation(f"{source.name}: skill resource escapes or is not a file: {candidate}")
        resources.append(resource)
    return resources


def render_agent(source: SourceAgent, runtime: Any, *, skill_root: Path | None = None, name_prefix: str = "crux_", projected_skill_root: Path | None = None) -> str:
    """Render one Codex custom-agent TOML file.

    `runtime` is the catalog's already-resolved Codex cell (a
    `models_catalog.CodexRuntime`). The catalog selects a complete agent
    override first, then the level fallback. This function performs no lookup.
    """
    # Provenance (ADR-0046 clause 6a): the field set below (name, description,
    # model, model_reasoning_effort, sandbox_mode, developer_instructions) and
    # the sandbox_mode enum (`read-only` / `workspace-write`) are per the Codex
    # custom-agent TOML schema, an external contract owned by OpenAI, verified
    # as of 2026-07-09. No live-network validation is ever performed against
    # this contract (ADR-0046 clause 6). The catalog resolves the model/effort
    # pair from a complete agent override or the level fallback; see the
    # provenance note at the top of this module.
    # Sandbox mapping rationale (ADR-0046 clause 7 / render contract): the crux
    # agent contract grants Edit and Write together (parse_source enforces
    # has_edit == has_write), so filesystem-mutating capability is fully
    # captured by the presence of `Edit` alone. A role that can Edit/Write maps
    # to Codex `workspace-write`; a role without it maps to the least-privilege
    # `read-only`. Read/Bash/WebFetch etc. do not widen the sandbox — only the
    # Edit/Write pair does.
    if not re.fullmatch(r"[a-z][a-z0-9_]*_", name_prefix):
        raise SpecViolation("invalid generated agent namespace")
    sandbox_mode = "workspace-write" if "Edit" in source.tools else "read-only"
    values = {
        "name": name_prefix + source.name.replace("-", "_"),
        "description": source.description,
        "model": runtime.model,
        "model_reasoning_effort": runtime.reasoning_effort,
        "sandbox_mode": sandbox_mode,
        "developer_instructions": _developer_instructions(source),
    }
    rendered = "\n".join(f"{key} = {_toml_basic_string(value)}" for key, value in values.items()) + "\n"
    if skill_root is None:
        return rendered
    resources = _skill_config(source, skill_root)
    for resource in resources:
        if projected_skill_root is not None:
            resource = projected_skill_root / resource.relative_to(skill_root.resolve())
        rendered += "\n[[skills.config]]\n"
        rendered += f"path = {_toml_basic_string(str(resource))}\n"
        rendered += "enabled = true\n"
    return rendered


def generate(
    source_dir: Path = SOURCE_DIR,
    catalog: Any | None = None,
    *,
    skill_root: Path | None = None,
) -> dict[str, str]:
    """Return generated Codex agent files keyed by their output filename.

    Loads the catalog first. `models_catalog.load()` runs the schema, roster
    and reference-graph rules fail-closed before returning, so an agent with no
    roster entry — and a roster entry with no agent file — both raise here,
    before any caller reaches `write()`. The reverse check this function used
    to perform by hand is that loader's roster rule; keeping a copy would be a
    second place encoding the same fact.
    """
    paths = sorted(source_dir.glob("*.md"))
    if not paths:
        raise SpecViolation(f"no source agents under {source_dir}")
    if catalog is None:
        catalog = models_catalog.load(agents_dir=source_dir)

    generated: dict[str, str] = {}
    for path in paths:
        source = parse_source(path)
        filename = codex_agent_filename(source.name)
        if filename in generated:
            raise SpecViolation(f"duplicate generated filename {filename!r}")
        generated[filename] = render_agent(source, catalog.resolve(source.name).codex, skill_root=skill_root)
    return generated


def _is_contained(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def pin_directory(output_dir: Path, *, containment_root: Path | None = None) -> PinnedDirectory:
    """Create and pin ``output_dir`` before a managed read or write.

    A contained directory symlink remains valid. Its resolved target is opened
    beneath a pinned selected root, one component at a time with
    ``O_NOFOLLOW``. Later replacement of the visible output path or one of its
    parents therefore cannot redirect the descriptor used by drift validation
    or atomic replacement.
    """
    if (output_dir.is_symlink() or output_dir.exists()) and not output_dir.is_dir():
        raise SpecViolation(f"refusing to write: {output_dir} exists and is not a directory")

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    nofollow_flags = flags | getattr(os, "O_NOFOLLOW", 0)
    if containment_root is None:
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            resolved = output_dir.resolve()
            directory_fd = os.open(resolved, nofollow_flags)
        except OSError as exc:
            raise SpecViolation(f"cannot create or open {output_dir}: {exc}") from exc
        return PinnedDirectory(resolved, directory_fd)

    try:
        containment_root.mkdir(parents=True, exist_ok=True)
        resolved_root = containment_root.resolve()
        resolved_output = output_dir.resolve()
    except OSError as exc:
        raise SpecViolation(f"cannot resolve agent target {output_dir}: {exc}") from exc
    if not _is_contained(resolved_output, resolved_root):
        raise SpecViolation(
            f"refusing to write: {output_dir} resolves outside selected root {resolved_root}"
        )
    try:
        root_fd = os.open(resolved_root, nofollow_flags)
    except OSError as exc:
        raise SpecViolation(f"cannot open selected root {resolved_root}: {exc}") from exc
    current_fd = root_fd
    try:
        for component in resolved_output.relative_to(resolved_root).parts:
            try:
                os.mkdir(component, dir_fd=current_fd)
            except FileExistsError:
                pass
            next_fd = os.open(component, nofollow_flags, dir_fd=current_fd)
            if current_fd != root_fd:
                os.close(current_fd)
            current_fd = next_fd
    except OSError as exc:
        if current_fd != root_fd:
            os.close(current_fd)
        os.close(root_fd)
        raise SpecViolation(f"cannot create or open {output_dir}: {exc}") from exc
    if current_fd != root_fd:
        os.close(root_fd)
    return PinnedDirectory(resolved_output, current_fd)


def _directory_fd(output_dir: Path | PinnedDirectory) -> int | None:
    if isinstance(output_dir, PinnedDirectory):
        return output_dir.duplicate_fd()
    if not output_dir.is_dir():
        return None
    try:
        return os.open(output_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError as exc:
        raise SpecViolation(f"cannot open {output_dir}: {exc}") from exc


def _managed_names(output_dir: Path | PinnedDirectory) -> list[str]:
    """List managed leaves without opening them or following leaf links."""
    directory_fd = _directory_fd(output_dir)
    if directory_fd is None:
        return []
    try:
        entries = os.listdir(directory_fd)
    except OSError as exc:
        raise SpecViolation(f"cannot list {output_dir}: {exc}") from exc
    finally:
        os.close(directory_fd)
    return sorted(
        name for name in entries
        if name.startswith(OUTPUT_PREFIX) and name.endswith(".toml")
    )


def read_managed_file(output_dir: Path | PinnedDirectory, name: str) -> tuple[str, str | None, str | None]:
    """Read one leaf safely as ``(status, text, reason)``.

    The descriptor is opened nonblocking and with ``O_NOFOLLOW`` where the
    platform provides it. A FIFO swapped in after ``lstat`` therefore cannot
    stall health, while a link is never followed.
    """
    try:
        directory_fd = _directory_fd(output_dir)
    except SpecViolation as exc:
        return "unsafe", None, f"cannot open agent directory: {exc}"
    if directory_fd is None:
        return "missing", None, None
    try:
        try:
            before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return "missing", None, None
        except OSError as exc:
            return "unsafe", None, str(exc)
        if stat.S_ISLNK(before.st_mode):
            return "unsafe", None, "managed agent file is a symlink"
        if not stat.S_ISREG(before.st_mode):
            return "unsafe", None, "managed agent file is not a regular file"
        leaf_flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            leaf_fd = os.open(name, leaf_flags, dir_fd=directory_fd)
        except OSError as exc:
            return "unsafe", None, str(exc)
        try:
            after = os.fstat(leaf_fd)
            if not stat.S_ISREG(after.st_mode):
                return "unsafe", None, "managed agent file changed to a nonregular file"
            chunks = []
            while True:
                chunk = os.read(leaf_fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(leaf_fd)
    finally:
        os.close(directory_fd)
    try:
        return "regular", b"".join(chunks).decode("utf-8"), None
    except UnicodeDecodeError as exc:
        return "malformed", None, f"invalid UTF-8: {exc}"


def safe_diff(output_dir: Path | PinnedDirectory, generated: dict[str, str]) -> tuple[list[str], list[str], list[str], dict[str, tuple[str, str | None]]]:
    """Return drift and reportable unsafe/malformed managed entries without reading them unsafely."""
    on_disk: dict[str, str] = {}
    problems: dict[str, tuple[str, str | None]] = {}
    names = _managed_names(output_dir)
    for name in names:
        status, text, reason = read_managed_file(output_dir, name)
        if status == "regular":
            assert text is not None
            on_disk[name] = text
        else:
            problems[name] = (status, reason)
    present = set(names)
    added = sorted(set(generated) - present)
    changed = sorted(
        name for name in set(generated) & present
        if name in problems or generated[name] != on_disk.get(name)
    )
    removed = sorted(present - set(generated))
    return added, changed, removed, problems


def diff(output_dir: Path | PinnedDirectory, generated: dict[str, str]) -> tuple[list[str], list[str], list[str]]:
    """Return drift for a strict installer operation, refusing unsafe leaves."""
    added, changed, removed, problems = safe_diff(output_dir, generated)
    if problems:
        details = ", ".join(f"{name} ({reason or status})" for name, (status, reason) in sorted(problems.items()))
        raise SpecViolation(f"refusing to diff through unsafe or malformed crux-*.toml leaf(s): {details}")
    return added, changed, removed


def _refuse_leaf_symlinks(output_dir: Path | PinnedDirectory, names: list[str]) -> None:
    """Fail-closed if any leaf crux-*.toml this run would write/remove is a symlink.

    Security (ADR-0046 clause 7, S5): `write()` is shared by BOTH the installer
    and the direct generator. `Path.write_text` follows a symlink and would
    clobber the link's target — an in-repo OR out-of-repo victim — even though
    the directory-level containment check (installer `_containment_error`)
    passed, because that check permits a *contained* `.codex/agents/` symlink
    but says nothing about an individual leaf file being a link. So before any
    write, refuse every leaf that is itself a symlink, regardless of whether its
    target is contained or escaping, and regardless of `--force`. This is not
    symlink prohibition at the directory level (ADR-0046 keeps the contained
    directory-symlink allowance) — it is leaf-file clobber prevention. `unlink`
    on a stale link is safe (it removes the link, not the target); we refuse it
    here anyway so the failure mode is uniform and diagnosable rather than
    silently deleting a user-authored crux-*.toml symlink.

    This check is redundant TODAY — `write()` calls `diff()` first, and `diff()`
    already refuses a symlinked crux-*.toml leaf — but it is retained
    DELIBERATELY as a standalone last-line defense: were that earlier refusal
    ever narrowed or removed by a future edit, this check must still
    independently refuse a symlinked leaf. Do not delete it as "dead"/redundant.
    """
    offenders = []
    for name in names:
        status, _, reason = read_managed_file(output_dir, name)
        if status not in {"regular", "missing"}:
            offenders.append(f"{name} ({reason or status})")
    if offenders:
        raise SpecViolation(
            "refusing to write through unsafe crux-*.toml leaf(s): " + ", ".join(offenders)
        )


def write(output_dir: Path | PinnedDirectory, generated: dict[str, str], *, remove_stale: bool = True) -> tuple[list[str], list[str]]:
    """Write Crux-managed files without touching a project's unrelated agents.

    Refuses fail-closed (SpecViolation) before writing if any leaf crux-*.toml
    it would create/overwrite/remove is a symlink, so neither the installer nor
    the direct generator can clobber a link target. Returns (written, removed);
    `removed` is empty when `remove_stale=False`.
    """
    if not isinstance(output_dir, PinnedDirectory):
        with pin_directory(output_dir) as pinned:
            return write(pinned, generated, remove_stale=remove_stale)

    added, changed, removed = diff(output_dir, generated)
    stale = removed if remove_stale else []
    # Check writes AND stale removals up front — nothing is written until every
    # affected leaf is proven not to be a symlink (fail-closed, no partial run).
    written = sorted(added + changed)
    _refuse_leaf_symlinks(output_dir, written + stale)
    try:
        directory_fd = output_dir.duplicate_fd()
    except OSError as exc:
        raise SpecViolation(f"cannot open {output_dir}: {exc}") from exc
    try:
        for name in written:
            temporary = f".{name}.{uuid.uuid4().hex}.tmp"
            try:
                temporary_fd = os.open(
                    temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory_fd
                )
                with os.fdopen(temporary_fd, "wb") as handle:
                    handle.write(generated[name].encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
                # os.replace swaps the directory entry. It never writes through
                # a hardlink or a leaf symlink substituted after preflight.
                os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            except OSError as exc:
                try:
                    os.unlink(temporary, dir_fd=directory_fd)
                except OSError:
                    pass
                raise SpecViolation(f"cannot atomically replace {name}: {exc}") from exc
        for name in stale:
            try:
                os.unlink(name, dir_fd=directory_fd)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise SpecViolation(f"cannot remove stale {name}: {exc}") from exc
    finally:
        os.close(directory_fd)
    return written, stale
