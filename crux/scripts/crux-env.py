#!/usr/bin/env python3
"""CLI for managing the crux runtime-state directory (~/.crux/).

Implements the CLI surface locked in docs/AGENTS.md §13 per
ADR-0002-rename-to-crux-and-manage-env-and-secrets (Accepted 2026-05-26),
fulfilling promptbook PB-0002 (crux runtime-state implementation).

The script manages:
  * ~/.crux/env          — KEY=value file (0600).
  * ~/.crux/required.yml — per-project required/optional env-var manifest (0600).
  * ~/.crux/secrets/     — directory for file-form credentials (0700).
  * ~/.crux/log/crux-env.log — append-only op log (0600). NO VALUES.

Subcommands:
  init                 Create the tree with correct modes. Idempotent.
  check [--project N]  Validate required keys present. JSON on stdout if missing.
  load                 Print `export KEY="value"` lines for shell sourcing.
  get KEY              Print one value to stdout.
  set KEY VALUE        Add or update a key. Logs without value.
  rm KEY               Remove a key. Logs.
  list [--project N]   Print required vs present keys per project. Never values.

CRUX_HOME env var overrides ~/.crux (used by tests).

Exit codes match the crux convention:
  0  — clean.
  1  — validation failure or user error. JSON on stdout where applicable.
  >0 with empty stdout — crash; surface stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# The env-file parser lives in the importable module `crux_env` so that
# the CLI and the module stay in byte-for-byte lock-step. Conformance test:
# same `env` fixture → same dict.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from crux_env import (  # noqa: E402  (sys.path insert before import)
    KEY_RE,
    encode_value,
    parse_env_line,
    parse_env_text,
)

# ──────────────────────────── constants ───────────────────────────────────

DIR_MODE = 0o700
FILE_MODE = 0o600

LOG_OPS = {"init", "set", "rm", "rotate", "check"}


# ──────────────────── secure file-open helpers ────────────────────────────
#
# Per ADR-0002 §13.1, env/required/log files must be mode 0600 — including
# the moment between create and first chmod. open(..., "w") respects umask
# (typically 0644) and only chmod-after creates a world-readable window.
# These helpers use os.open with explicit mode bits so the file NEVER
# exists at a permissive mode.


def _secure_open_write(path: Path):
    """Open `path` for writing (truncate) with mode 0600 from creation.

    Returns a text-mode file object. The post-write `os.chmod(FILE_MODE)`
    is retained at call sites as belt-and-braces.
    """
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    return os.fdopen(fd, "w", encoding="utf-8")


def _secure_open_append(path: Path):
    """Open `path` for append with mode 0600 from creation.

    Returns a text-mode file object. If the file already exists, its mode
    is left untouched by the open (kernel only honors mode on create).
    """
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, FILE_MODE)
    return os.fdopen(fd, "a", encoding="utf-8")


def _secure_write_text(path: Path, text: str) -> None:
    """Equivalent to `path.write_text(text)` but file is 0600 from creation."""
    with _secure_open_write(path) as fh:
        fh.write(text)


# ───────────────────────── path resolution ────────────────────────────────


def crux_home() -> Path:
    """Return the crux home directory.

    Honors $CRUX_HOME if set (non-empty), else ~/.crux.
    """
    override = os.environ.get("CRUX_HOME")
    if override:
        return Path(override)
    return Path(os.path.expanduser("~/.crux"))


def env_path() -> Path:
    return crux_home() / "env"


def required_path() -> Path:
    return crux_home() / "required.yml"


def secrets_dir() -> Path:
    return crux_home() / "secrets"


def log_dir() -> Path:
    return crux_home() / "log"


def log_path() -> Path:
    return log_dir() / "crux-env.log"


# ───────────────────── minimal YAML loader (required.yml) ─────────────────
#
# Schema is constrained (per §13.3):
#   projects:
#     <name>:
#       required: [list of KEY strings]
#       optional: [list of KEY strings]
#
# Hand-rolled — do not depend on PyYAML.


def load_required_yml(text: str) -> dict[str, dict[str, list[str]]]:
    """Parse required.yml and return {project: {"required": [...], "optional": [...]}}.

    Raises ValueError on schema violations.
    """
    lines = text.splitlines()
    i = 0
    n = len(lines)

    # Find `projects:` at indent 0.
    projects_start: int | None = None
    while i < n:
        raw = _strip_yaml_comment(lines[i]).rstrip()
        if not raw.strip():
            i += 1
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0 and raw.strip().startswith("projects:"):
            after = raw.strip()[len("projects:") :].strip()
            if after and after != "{}":
                raise ValueError("required.yml: 'projects:' must be followed by a block mapping")
            projects_start = i + 1
            break
        i += 1
    if projects_start is None:
        # An empty/comment-only file is treated as no projects.
        return {}

    projects: dict[str, dict[str, list[str]]] = {}
    i = projects_start
    while i < n:
        raw = _strip_yaml_comment(lines[i]).rstrip()
        if not raw.strip():
            i += 1
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0:
            # End of `projects:` block.
            break
        # Expect "<indent>  <name>:" — first sub-indent level.
        content = raw.strip()
        if not content.endswith(":"):
            # Could be "projects: {}" inline; allow `{}` as empty mapping.
            if content == "{}":
                i += 1
                continue
            raise ValueError(f"required.yml: expected '<project>:' at line {i + 1}, got {content!r}")
        project_name = content[:-1].strip()
        if not project_name:
            raise ValueError(f"required.yml: empty project name at line {i + 1}")
        project_indent = indent

        # Walk this project's body.
        body: dict[str, list[str]] = {"required": [], "optional": []}
        i += 1
        while i < n:
            sub_raw = _strip_yaml_comment(lines[i]).rstrip()
            if not sub_raw.strip():
                i += 1
                continue
            sub_indent = len(sub_raw) - len(sub_raw.lstrip(" "))
            if sub_indent <= project_indent:
                break
            sub_content = sub_raw.strip()
            if not sub_content.endswith(":"):
                # Could be `required: []` inline.
                if ":" in sub_content:
                    fld, _, value = sub_content.partition(":")
                    fld = fld.strip()
                    value = value.strip()
                    if fld not in ("required", "optional"):
                        raise ValueError(f"required.yml: unknown field {fld!r} in project {project_name!r}")
                    if value == "[]" or value == "":
                        body[fld] = []
                        i += 1
                        continue
                    if value.startswith("[") and value.endswith("]"):
                        inner = value[1:-1].strip()
                        items = [p.strip().strip("'\"") for p in inner.split(",") if p.strip()]
                        body[fld] = items
                        i += 1
                        continue
                raise ValueError(f"required.yml: expected 'required:' or 'optional:' at line {i + 1}")
            field = sub_content[:-1].strip()
            if field not in ("required", "optional"):
                raise ValueError(f"required.yml: unknown field {field!r} in project {project_name!r}")
            field_indent = sub_indent
            # Collect list items.
            items: list[str] = []
            i += 1
            while i < n:
                item_raw = _strip_yaml_comment(lines[i]).rstrip()
                if not item_raw.strip():
                    i += 1
                    continue
                item_indent = len(item_raw) - len(item_raw.lstrip(" "))
                if item_indent <= field_indent:
                    break
                item_content = item_raw.strip()
                if not item_content.startswith("- "):
                    if item_content == "-":
                        i += 1
                        continue
                    raise ValueError(f"required.yml: expected list item at line {i + 1}, got {item_content!r}")
                key = item_content[2:].strip().strip("'\"")
                if not KEY_RE.match(key):
                    raise ValueError(
                        f"required.yml: invalid key {key!r} in project " f"{project_name!r} (line {i + 1})"
                    )
                items.append(key)
                i += 1
            body[field] = items
        projects[project_name] = {
            "required": body.get("required", []),
            "optional": body.get("optional", []),
        }
    return projects


def _strip_yaml_comment(line: str) -> str:
    """Strip a `#` to end-of-line comment, respecting quoted strings."""
    out: list[str] = []
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


# ───────────────────────────── logging ────────────────────────────────────


def log_op(op: str, key: str = "-", reason: str = "") -> None:
    """Append one line to ~/.crux/log/crux-env.log.

    Format: "YYYY-MM-DD HH:MM:SS  <op>  <KEY>  [reason]"
    Two-space field separator. Reason omitted if empty. NEVER write values.
    """
    if op not in LOG_OPS:
        raise ValueError(f"log_op: unknown op {op!r}")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts}  {op}  {key}"
    if reason:
        line += f"  {reason}"
    line += "\n"
    log_dir().mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(log_dir(), DIR_MODE)
    except OSError:
        pass
    lp = log_path()
    # Open with O_CREAT | O_APPEND | mode 0600 so the file never exists at
    # a world-readable mode (was vulnerable when `lp.touch()` preceded chmod).
    with _secure_open_append(lp) as fh:
        fh.write(line)
    try:
        os.chmod(lp, FILE_MODE)
    except OSError:
        pass


# ────────────────────────── env file I/O ──────────────────────────────────


def read_env_file() -> tuple[list[str], dict[str, str]]:
    """Read ~/.crux/env and return (raw_lines, parsed_dict).

    The raw_lines list preserves the file verbatim (including blank/comment
    lines) for line-oriented rewrite. parsed_dict is the same data as
    parse_env_text().
    """
    p = env_path()
    if not p.exists():
        return [], {}
    text = p.read_text(encoding="utf-8")
    raw_lines = text.splitlines(keepends=True)
    parsed = parse_env_text(text)
    return raw_lines, parsed


def write_env_file(lines: list[str]) -> None:
    """Atomically write env file lines and re-chmod 0600.

    The tmp file is created at mode 0600 so it never exists world-readable;
    after os.replace, the destination inherits that mode. The trailing chmod
    is kept as defense-in-depth (and SC-1: unlink tmp on failure).
    """
    p = env_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, DIR_MODE)
    except OSError:
        pass
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        with _secure_open_write(tmp) as fh:
            fh.writelines(lines)
        os.replace(tmp, p)
    except Exception:
        # Clean up partial tmp before re-raising (SC-1).
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    try:
        os.chmod(p, FILE_MODE)
    except OSError:
        pass


# ─────────────────────────── subcommands ──────────────────────────────────


def cmd_init(_args: argparse.Namespace) -> int:
    home = crux_home()
    home.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(home, DIR_MODE)
    except OSError as exc:
        print(f"crux-env: warning: chmod {home}: {exc}", file=sys.stderr)

    # env file — empty with a created-on header comment.
    # _secure_write_text creates at 0600 so there is no world-readable window.
    ep = env_path()
    if not ep.exists():
        today = datetime.now().strftime("%Y-%m-%d")
        _secure_write_text(ep, f"# Created {today}\n")
    try:
        os.chmod(ep, FILE_MODE)
    except OSError:
        pass

    # required.yml — minimum stub.
    rp = required_path()
    if not rp.exists():
        _secure_write_text(rp, "projects: {}\n")
    try:
        os.chmod(rp, FILE_MODE)
    except OSError:
        pass

    # secrets/ directory.
    sd = secrets_dir()
    sd.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(sd, DIR_MODE)
    except OSError:
        pass

    # log/ directory.
    ld = log_dir()
    ld.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(ld, DIR_MODE)
    except OSError:
        pass

    log_op("init", key="-", reason="")
    print(f"crux-env: initialized {home}", file=sys.stderr)
    return 0


def _load_required() -> dict[str, dict[str, list[str]]]:
    """Read and parse required.yml. Returns {} if the file does not exist.

    Raises ValueError if the file exists but cannot be parsed — callers MUST
    catch this and emit a JSON error on stdout per the exit-code convention.
    """
    rp = required_path()
    if not rp.exists():
        return {}
    return load_required_yml(rp.read_text(encoding="utf-8"))


# One-line remediation for the unknown_project payload (first-run UX —
# a project absent from required.yml is usually "you haven't declared it
# yet", not an error in the invocation).
_UNKNOWN_PROJECT_HINT = "add the project to ~/.crux/required.yml or run without --project"


def _emit_required_parse_error(exc: Exception) -> int:
    """Emit a structured JSON error payload for an unparseable required.yml.

    Returns 1 so the caller can `return _emit_required_parse_error(exc)`.
    """
    payload = {
        "error": "required.yml parse failure",
        "detail": str(exc),
    }
    print(json.dumps(payload, indent=2))
    return 1


def cmd_check(args: argparse.Namespace) -> int:
    try:
        projects = _load_required()
    except ValueError as exc:
        return _emit_required_parse_error(exc)
    _, env_map = read_env_file()

    if args.project:
        if args.project not in projects:
            payload = {
                "error": "unknown_project",
                "project": args.project,
                "known_projects": sorted(projects.keys()),
                # Additive first-run UX hint (existing keys are locked per
                # docs/AGENTS.md §13.6 and must never be renamed).
                "hint": _UNKNOWN_PROJECT_HINT,
            }
            print(json.dumps(payload, indent=2))
            return 1
        target = {args.project: projects[args.project]}
    else:
        target = projects

    missing_report: list[dict[str, Any]] = []
    declared: set[str] = set()
    for proj, body in target.items():
        proj_missing = [k for k in body.get("required", []) if not env_map.get(k)]  # missing or empty string
        declared.update(body.get("required", []))
        declared.update(body.get("optional", []))
        if proj_missing:
            missing_report.append({"project": proj, "keys": proj_missing})

    # Compute `extra`: keys present in env but not declared anywhere across
    # ALL projects (not just the target). This matches "extra" intuition.
    all_declared: set[str] = set()
    for body in projects.values():
        all_declared.update(body.get("required", []))
        all_declared.update(body.get("optional", []))
    extra = sorted(set(env_map.keys()) - all_declared)

    if missing_report:
        all_missing_keys = sorted({k for m in missing_report for k in m["keys"]})
        log_op("check", key="-", reason=",".join(all_missing_keys))
        print(json.dumps({"missing": missing_report, "extra": extra}, indent=2))
        return 1

    # Success.
    total_required = sum(len(body.get("required", [])) for body in target.values())
    if args.project:
        msg = f"All {total_required} required key(s) present for project " f"{args.project!r}."
    else:
        msg = f"All {total_required} required key(s) present across " f"{len(target)} project(s)."
    log_op("check", key="-", reason="ok")
    print(msg, file=sys.stderr)
    if extra:
        print(
            f"crux-env: note: {len(extra)} undeclared key(s) in env: " f"{', '.join(extra)}",
            file=sys.stderr,
        )
    return 0


def cmd_load(_args: argparse.Namespace) -> int:
    _, env_map = read_env_file()
    for key in sorted(env_map.keys()):
        value = env_map[key]
        # shlex.quote produces shell-safe single-quoted form when needed.
        print(f"export {key}={shlex.quote(value)}")
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    key = args.key
    if not KEY_RE.match(key):
        print(f"crux-env: invalid key {key!r}", file=sys.stderr)
        return 1
    _, env_map = read_env_file()
    value = env_map.get(key)
    if not value:
        print(f"crux-env: key {key!r} missing or empty", file=sys.stderr)
        return 1
    # Print value with a trailing newline (convenient for shell capture
    # via `$(crux-env get FOO)` which strips trailing newlines anyway).
    print(value)
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    key = args.key
    value = args.value
    if not KEY_RE.match(key):
        print(f"crux-env: invalid key {key!r}", file=sys.stderr)
        return 1

    # Empty values aren't stored — the env file format treats `KEY=` and a
    # missing key the same way (see crux_env._lookup). Refuse explicitly
    # so users don't think they've recorded an "intentionally empty" value.
    if value == "":
        print(
            f"crux-env: refusing to set empty value for {key}; " f"use 'crux-env rm {key}' to remove it",
            file=sys.stderr,
        )
        return 1

    ep = env_path()
    if not ep.exists():
        # Initialize on the fly with a header so the file is well-formed.
        # _secure_write_text creates at 0600 from the moment of creation.
        ep.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(ep.parent, DIR_MODE)
        except OSError:
            pass
        today = datetime.now().strftime("%Y-%m-%d")
        _secure_write_text(ep, f"# Created {today}\n")
        try:
            os.chmod(ep, FILE_MODE)
        except OSError:
            pass

    raw = ep.read_text(encoding="utf-8")
    lines = raw.splitlines(keepends=True)
    encoded = encode_value(value)
    new_line = f"{key}={encoded}\n"

    # Find existing KEY=... line (skip comments/blank).
    replaced = False
    for idx, ln in enumerate(lines):
        try:
            parsed = parse_env_line(ln.rstrip("\n"), lineno=idx + 1)
        except ValueError:
            continue
        if parsed is None:
            continue
        existing_key, _ = parsed
        if existing_key == key:
            # Preserve any trailing newline state.
            if not ln.endswith("\n"):
                lines[idx] = new_line.rstrip("\n")
            else:
                lines[idx] = new_line
            replaced = True
            break

    if not replaced:
        # Append, ensuring the file ends with a newline first.
        if lines and not lines[-1].endswith("\n"):
            lines[-1] = lines[-1] + "\n"
        lines.append(new_line)

    write_env_file(lines)
    # Per AGENTS.md §13.4: replacing an existing value is logged as `rotate`
    # so audit trails distinguish a value swap from a remove-then-add.
    op = "rotate" if replaced else "set"
    log_op(op, key=key, reason="")
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    key = args.key
    if not KEY_RE.match(key):
        print(f"crux-env: invalid key {key!r}", file=sys.stderr)
        return 1
    ep = env_path()
    if not ep.exists():
        print(f"crux-env: env file does not exist", file=sys.stderr)
        return 1
    raw = ep.read_text(encoding="utf-8")
    lines = raw.splitlines(keepends=True)
    new_lines: list[str] = []
    removed = False
    for idx, ln in enumerate(lines):
        try:
            parsed = parse_env_line(ln.rstrip("\n"), lineno=idx + 1)
        except ValueError:
            new_lines.append(ln)
            continue
        if parsed is None:
            new_lines.append(ln)
            continue
        existing_key, _ = parsed
        if existing_key == key and not removed:
            removed = True
            continue
        new_lines.append(ln)
    if not removed:
        print(f"crux-env: key {key!r} not present", file=sys.stderr)
        return 1
    write_env_file(new_lines)
    log_op("rm", key=key, reason="")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    try:
        projects = _load_required()
    except ValueError as exc:
        return _emit_required_parse_error(exc)
    _, env_map = read_env_file()

    if args.project:
        if args.project not in projects:
            payload = {
                "error": "unknown_project",
                "project": args.project,
                "known_projects": sorted(projects.keys()),
                # Additive first-run UX hint (existing keys are locked per
                # docs/AGENTS.md §13.6 and must never be renamed).
                "hint": _UNKNOWN_PROJECT_HINT,
            }
            print(json.dumps(payload, indent=2))
            return 1
        target = {args.project: projects[args.project]}
    else:
        target = projects

    if not target:
        print("crux-env: no projects declared in required.yml.", file=sys.stderr)
        return 0

    for proj_name in sorted(target.keys()):
        body = target[proj_name]
        required = body.get("required", [])
        optional = body.get("optional", [])
        print(f"[{proj_name}]")
        all_keys = [(k, "required") for k in required] + [(k, "optional") for k in optional]
        if not all_keys:
            print("  (no keys declared)")
            continue
        # Aligned columns: key (left-padded) | required/optional | mark.
        width = max(len(k) for k, _ in all_keys)
        for k, kind in all_keys:
            present = bool(env_map.get(k))
            mark = "[present]" if present else "[missing]"
            print(f"  {k.ljust(width)}  {kind:<8}  {mark}")
        print()
    return 0


# ─────────────────────────────── argparse ─────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crux-env",
        description=(
            "Manage ~/.crux/ runtime state (env vars, required.yml, "
            "secrets, op log). Per docs/AGENTS.md §13 and ADR-0002."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    sub.add_parser("init", help="Create ~/.crux/ tree with correct modes (idempotent).")

    p_check = sub.add_parser(
        "check",
        help="Validate required keys are present and non-empty. JSON on stdout if missing.",
    )
    p_check.add_argument("--project", help="Limit check to a single project.")

    sub.add_parser("load", help='Print `export KEY="value"` lines to stdout.')

    p_get = sub.add_parser("get", help="Print one value to stdout. Exit 1 if missing.")
    p_get.add_argument("key", help="Env var key (must match ^[A-Z][A-Z0-9_]*$).")

    p_set = sub.add_parser("set", help="Add or update a key. Logs without value.")
    p_set.add_argument("key", help="Env var key (must match ^[A-Z][A-Z0-9_]*$).")
    p_set.add_argument("value", help="Value to store. Will be quoted if needed.")

    p_rm = sub.add_parser("rm", help="Remove a key from env. Logs.")
    p_rm.add_argument("key", help="Env var key to remove.")

    p_list = sub.add_parser(
        "list",
        help="Print required vs present keys per project. Never prints values.",
    )
    p_list.add_argument("--project", help="Limit listing to a single project.")

    return parser


DISPATCH = {
    "init": cmd_init,
    "check": cmd_check,
    "load": cmd_load,
    "get": cmd_get,
    "set": cmd_set,
    "rm": cmd_rm,
    "list": cmd_list,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = DISPATCH[args.command]
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
