#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Write one journal or log-only request with replayable, per-file writes.

The caller owns the narrative and keeps its timestamped request for retry.
The writer never runs version control: every file it touches stays modified and
uncommitted. Each result reports `recorded` (surfaces holding the requested
content on disk) and `written` (surfaces this run changed). A partial result
lists the recorded surfaces when a later stage fails.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bionic_config import BionicConfigError, load_config  # noqa: E402
from journal_index import (CATEGORIES, ENTRY_RE, derive_row, find_unclosed_fence,
                           render_index, unknown_category_headings)  # noqa: E402
from md_fences import closes_fence, fence_marker, split_lines  # noqa: E402


GENERATOR = Path(__file__).with_name("generate-journal-index.py")
HEADER = "# Journal — {month}\n\n_Append-only. Newest entries at the top._\n\n"
LOG_HEADING = re.compile(r"^## \[[0-9]{4}-[0-9]{2}-[0-9]{2}\] [^\n]+$")
REF = re.compile(r"(?:\[\[[^\[\]\n]+\]\]|rule:[a-z][a-z0-9-]*)\Z")


class Refusal(Exception):
    """Input or on-disk state is unsafe before mutation."""


def _regular_read(path: Path, *, missing_ok: bool = True) -> bytes | None:
    if path.is_symlink():
        raise Refusal(f"{path.name} is a symlink")
    if not path.exists():
        if missing_ok:
            return None
        raise Refusal(f"{path.name} is missing")
    if not path.is_file():
        raise Refusal(f"{path.name} is not a regular file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        return handle.read()


def _text(raw: bytes | None, name: str) -> str:
    if raw is None:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Refusal(f"{name} is not UTF-8: {exc}") from exc


def _timestamp(value: str) -> tuple[str, str, str, str]:
    try:
        instant = datetime.fromisoformat(value)
    except ValueError as exc:
        raise Refusal("--at must be an ISO8601 timestamp") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise Refusal("--at requires an explicit local offset")
    minute = instant.replace(second=0, microsecond=0)
    return (minute.strftime("%Y-%m-%d %H:%M"), minute.strftime("%Y-%m"),
            minute.strftime("%Y-%m-%d"), minute.isoformat(timespec="minutes"))


def _canonical_ops(agents: Path) -> re.Pattern[str]:
    source = _text(_regular_read(agents, missing_ok=False), "AGENTS.md")
    section = source.split("#### Op-enum regex (canonical", 1)
    if len(section) != 2:
        raise Refusal("AGENTS.md has no canonical log-operation block")
    current = section[1].split("**Current-writer regex**", 1)
    if len(current) != 2:
        raise Refusal("AGENTS.md has no current-writer regex")
    match = re.search(r"```\s*\n([^`]+)```", current[1])
    if not match:
        raise Refusal("AGENTS.md current-writer regex is malformed")
    try:
        # The canonical expression ends in a significant space after `|`.
        return re.compile(match.group(1).splitlines()[0].lstrip(" \t"))
    except re.error as exc:
        raise Refusal("AGENTS.md current-writer regex is invalid") from exc


def _body(raw: str, mode: str, refs: str | None) -> tuple[str, str]:
    if "\r" in raw or "\x00" in raw:
        raise Refusal("body contains a carriage return or NUL")
    lines = [line.rstrip(" \t") for line in split_lines(raw)]
    while lines and not lines[-1]:
        lines.pop()
    if not lines or not any(lines):
        raise Refusal("body requires at least one nonempty line")
    if mode == "journal" and not 1 <= len(lines) <= 10:
        raise Refusal("journal body requires 1–10 lines")
    if mode == "log-only" and len(lines) != 1:
        raise Refusal("log-only body requires one line")
    if any(line.startswith("## [") for line in lines):
        raise Refusal("body contains a reserved entry heading")
    if find_unclosed_fence("\n".join(lines)) is not None:
        raise Refusal("body leaves a Markdown fence open")
    friction = [i for i, line in enumerate(lines) if line.startswith("Friction:")]
    if len(friction) > 1 or any(lines[i].split(":", 1)[1].strip().lower() in ("", "none", "none.") for i in friction):
        raise Refusal("Friction line is empty, repeated, or says none")
    refs_tokens = refs.split() if refs else []
    if refs is not None and not refs_tokens:
        raise Refusal("--refs is empty")
    if any(not REF.fullmatch(token) for token in refs_tokens):
        raise Refusal("--refs requires wiki-links or rule citations")
    if mode == "journal":
        if any(line.startswith("Refs:") for line in lines):
            raise Refusal("put references in --refs, not the body")
        if refs_tokens:
            lines.append("Refs: " + " ".join(refs_tokens))
    return "\n".join(lines), " ".join(refs_tokens)


def _entry_ranges(text: str, pattern: re.Pattern[str]) -> list[tuple[str, str]]:
    """Return `(heading, exact block)` for headings outside fenced content."""
    # Preserve the exact bytes while using md_fences' newline-only boundary.
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]] + [parts[-1]]
    found: list[tuple[str, str]] = []
    fence: tuple[str, int] | None = None
    start: int | None = None
    heading = ""
    for pos, raw_line in enumerate(lines):
        line = raw_line.removesuffix("\n").removesuffix("\r")
        marker = fence_marker(line)
        if fence is not None:
            if closes_fence(marker, fence):
                fence = None
            continue
        if marker is not None:
            fence = (marker[0], marker[1])
            continue
        if pattern.match(line):
            if start is not None:
                found.append((heading, "".join(lines[start:pos])))
            start, heading = pos, line
    if start is not None:
        found.append((heading, "".join(lines[start:])))
    return found


def _log_insertion(text: str) -> tuple[str, str]:
    """Split a headed log after its preamble, or a headerless log at byte zero."""
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]] + [parts[-1]]
    offset = 0
    headers: list[int] = []
    first_entry: int | None = None
    fence: tuple[str, int] | None = None
    for raw_line in lines:
        line = raw_line.removesuffix("\n").removesuffix("\r")
        marker = fence_marker(line)
        if fence is not None:
            if closes_fence(marker, fence):
                fence = None
        elif marker is not None:
            fence = (marker[0], marker[1])
        else:
            if line == "# Operations log":
                headers.append(offset)
            if first_entry is None and LOG_HEADING.match(line):
                first_entry = offset
        offset += len(raw_line)
    if not headers:
        return "", text
    if headers != [0]:
        raise Refusal("operations log header is displaced or repeated; restore one header before entries without changing their bytes")
    split_at = first_entry if first_entry is not None else len(text)
    preamble = text[:split_at]
    if not preamble.endswith("\n"):
        preamble += "\n\n"
    return preamble, text[split_at:]


def _month_insertion(text: str, month: str) -> tuple[str, str]:
    """Keep the existing month preamble above the first journal entry."""
    title = f"# Journal — {month}\n\n"
    if not text.startswith(title):
        raise Refusal("monthly journal lacks a recognized header")
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]] + [parts[-1]]
    offset = 0
    first_entry: int | None = None
    fence: tuple[str, int] | None = None
    for raw_line in lines:
        line = raw_line.removesuffix("\n").removesuffix("\r")
        marker = fence_marker(line)
        if fence is not None:
            if closes_fence(marker, fence):
                fence = None
        elif marker is not None:
            fence = (marker[0], marker[1])
        elif ENTRY_RE.match(line):
            first_entry = offset
            break
        offset += len(raw_line)
    split_at = first_entry if first_entry is not None else len(text)
    preamble = text[:split_at]
    if not preamble.endswith("\n\n"):
        preamble += "\n" if preamble.endswith("\n") else "\n\n"
    return preamble, text[split_at:]


def _exact_record(existing: str, heading: str, candidate: str,
                  pattern: re.Pattern[str], minute: str | None = None,
                  log_mode: str | None = None) -> bool:
    for old_heading, block in _entry_ranges(existing, pattern):
        if old_heading != heading:
            continue
        if minute is not None:
            lines = split_lines(block)
            field_index = 3 if log_mode == "log-only" else 2
            if len(lines) <= field_index:
                continue
            field = lines[field_index]
            field_pattern = (r"Recorded at (?P<minute>[^ .]+)\.(?: Refs: .*)?"
                             if log_mode == "log-only" else
                             r"Entry in `[^`\n]+` at (?P<minute>[^ .]+)\.(?: Refs: .*)?")
            matched = re.fullmatch(field_pattern, field)
            if matched is None:
                continue
            recorded_minute = matched.group("minute")
            if recorded_minute[:16] == minute[:16] and recorded_minute != minute:
                raise Refusal("the same civil minute and heading already carry a different UTC offset")
            if recorded_minute != minute:
                continue
        if block == candidate:
            return True
        raise Refusal("the same recorded minute and heading already carry different content")
    return False


def _read_at(directory_fd: int, name: str) -> tuple[bytes | None, int | None]:
    """Read a regular target without following its final component."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    except FileNotFoundError:
        return None, None
    with os.fdopen(fd, "rb") as handle:
        details = os.fstat(handle.fileno())
        if not stat.S_ISREG(details.st_mode):
            raise Refusal(f"{name} is not a regular file")
        return handle.read(), stat.S_IMODE(details.st_mode)


def _atomic_replace(path: Path, want: bytes, have: bytes | None,
                    tree: Path, root: Path) -> None:
    """Replace through pinned, no-follow directory handles.

    The pathname is checked again just before the descriptor-relative rename.
    A swapped symlink can no longer redirect the temporary or final write.
    """
    try:
        tree_parts = tree.relative_to(root).parts
        parent_parts = path.parent.relative_to(tree).parts
    except ValueError as exc:
        raise Refusal(f"{path.name} is outside the repository tree") from exc
    if not tree_parts or parent_parts not in ((), ("journal",)):
        raise Refusal(f"{path.name} has an unsafe parent")

    opened: list[int] = []
    links: list[tuple[int, str, int]] = []
    temporary: str | None = None
    try:
        first = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened.append(first)
        parent_fd = first
        for component in (*root.parts[1:], *tree_parts, *parent_parts):
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=parent_fd)
            opened.append(child)
            links.append((parent_fd, component, child))
            parent_fd = child

        def check_binding() -> None:
            for ancestor_fd, component, child_fd in links:
                name_stat = os.stat(component, dir_fd=ancestor_fd, follow_symlinks=False)
                fd_stat = os.fstat(child_fd)
                if (not stat.S_ISDIR(name_stat.st_mode) or
                        (name_stat.st_dev, name_stat.st_ino) !=
                        (fd_stat.st_dev, fd_stat.st_ino)):
                    raise Refusal(f"{component} directory changed during this write")

        check_binding()
        current, old_mode = _read_at(parent_fd, path.name)
        if current != have:
            raise Refusal(f"{path.name} changed during this write")
        temp_name = ".write-journal-" + secrets.token_hex(12)
        temp_fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                          0o644, dir_fd=parent_fd)
        temporary = temp_name
        with os.fdopen(temp_fd, "wb") as handle:
            handle.write(want)
            if old_mode is not None:
                os.fchmod(handle.fileno(), old_mode)
        check_binding()
        if _read_at(parent_fd, path.name)[0] != have:
            raise Refusal(f"{path.name} changed during this write")
        os.replace(temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        temporary = None
    finally:
        if temporary is not None and opened:
            try:
                os.unlink(temporary, dir_fd=opened[-1])
            except FileNotFoundError:
                pass
        for fd in reversed(opened):
            os.close(fd)


def _preflight(root: Path, month: str, prospective: str) -> None:
    result = subprocess.run(["uv", "run", "--no-config", str(GENERATOR),
                             "--check-stdin", "--month", month, "--repo-root", str(root)],
                            input=prospective, text=True, capture_output=True)
    if result.returncode:
        detail = result.stdout.strip() or result.stderr.strip()
        raise Refusal(f"prospective journal index check failed: {detail}")


def _render_index(tree: Path, config_docs_dir: str) -> tuple[bytes | None, bytes]:
    """Use the regenerator's own walk and the shared derivation primitives."""
    spec = importlib.util.spec_from_file_location("journal_generator", GENERATOR)
    assert spec and spec.loader
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    journal_dir = tree / "journal"
    month_texts, errors = generator._walk_journal_dir(journal_dir, f"{config_docs_dir}/journal")
    if errors:
        raise Refusal(f"journal index input changed: {errors}")
    rows = []
    for month in sorted(month_texts, reverse=True):
        month_text = month_texts[month][0]
        if find_unclosed_fence(month_text) is not None or unknown_category_headings(month_text):
            raise Refusal("journal index input became malformed")
        rows.append(derive_row(month, month_text))
    path = journal_dir / "index.md"
    old = _regular_read(path)
    # The generator owns output-path validation as well as month-file validation.
    generator._validate_output_index(path, tree, f"{config_docs_dir}/journal/index.md")
    dates = [row["last"] for row in rows if row["last"] != "—"]
    return old, render_index(rows, max(dates) if dates else "—").encode("utf-8")


def _result(status: str, mode: str, timestamp: str | None, warnings: list[str],
            recorded: list[str], pending: list[str], written: list[str], error: str | None) -> int:
    print(json.dumps({"status": status, "mode": mode, "timestamp": timestamp,
                      "warnings": warnings, "recorded": recorded, "pending": pending,
                      "written": written, "error": error}, sort_keys=True))
    return 0 if status == "complete" else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--mode", required=True, choices=("journal", "log-only"))
    ap.add_argument("--at", required=True)
    ap.add_argument("--category", required=True)
    ap.add_argument("--subject", required=True)
    bodies = ap.add_mutually_exclusive_group(required=True)
    bodies.add_argument("--body-file")
    bodies.add_argument("--body-stdin", action="store_true")
    ap.add_argument("--refs")
    ap.add_argument("--log-op")
    args = ap.parse_args(argv)

    warnings: list[str] = []
    recorded: list[str] = []
    written: list[str] = []
    timestamp: str | None = None
    surfaces = ["log.md"] if args.mode == "log-only" else ["journal/{month}.md", "journal/index.md", "log.md"]
    try:
        record_minute, month, today, timestamp = _timestamp(args.at)
        surfaces = [path.replace("{month}", month) for path in surfaces]
        category = args.category if args.category in CATEGORIES else "misc"
        if category != args.category:
            warnings.append(f"unknown category {args.category!r}; using misc")
        if not args.subject or args.subject != args.subject.strip() or "\n" in args.subject or "\r" in args.subject or "|" in args.subject or "\x00" in args.subject:
            raise Refusal("subject must be one nonempty line without a pipe")
        root = Path(args.repo_root).resolve()
        config = load_config(root, require_tree=True)
        tree = config.docs_root
        journal_dir = tree / "journal"
        if args.mode == "journal":
            if journal_dir.is_symlink() or not journal_dir.is_dir() or journal_dir.resolve() != tree / "journal":
                raise Refusal("journal directory is missing or unsafe")
        op = "journal" if args.mode == "journal" else args.log_op
        canonical = _canonical_ops(tree / "AGENTS.md")
        if not op or not canonical.fullmatch(f"## [{today}] {op} | "):
            raise Refusal("log operation is missing or outside the canonical enum")
        if args.body_stdin:
            raw_body = sys.stdin.read()
        else:
            raw_body = _text(_regular_read(Path(args.body_file), missing_ok=False), "body file")
        body, refs = _body(raw_body, args.mode, args.refs)
        log_path = tree / "log.md"
        old_log = _regular_read(log_path)
        log_text = _text(old_log, "log.md")
        log_preamble, log_entries = _log_insertion(log_text)
        if args.mode == "journal":
            month_path = journal_dir / f"{month}.md"
            old_month = _regular_read(month_path)
            month_text = _text(old_month, month_path.name) if old_month is not None else HEADER.format(month=month)
            preamble, prior_entries = _month_insertion(month_text, month)
            heading = f"## [{record_minute}] {category} | {args.subject}"
            entry = f"{heading}\n\n{body}\n\n"
            month_done = _exact_record(month_text, heading, entry, ENTRY_RE)
            if month_done:
                prospective = month_text
            else:
                prospective = preamble + entry + prior_entries
            log_heading = f"## [{today}] journal | {category}: {args.subject}"
            log_body = f"Entry in `{config.docs_dir}/journal/{month}.md` at {timestamp}."
            if refs:
                log_body += f" Refs: {refs}"
        else:
            prospective = None
            log_heading = f"## [{today}] {op} | {args.subject}"
            log_body = f"{body}\nRecorded at {timestamp}."
            if refs:
                log_body += f" Refs: {refs}"
        log_entry = f"{log_heading}\n\n{log_body}\n\n"
        log_done = _exact_record(log_text, log_heading, log_entry, LOG_HEADING,
                                 timestamp, args.mode)
        if args.mode == "journal" and month_done:
            recorded.append(surfaces[0])
            if not log_done:
                warnings.append("offset unverified: the month entry has no offset and no matching log record confirms the supplied offset")
        if args.mode == "journal":
            _preflight(root, month, prospective)
        if log_done:
            recorded.append("log.md")
        if args.mode == "journal" and not month_done:
            _atomic_replace(month_path, prospective.encode("utf-8"), old_month, tree, root)
            written.append(surfaces[0])
            recorded.append(surfaces[0])
        if args.mode == "journal":
            old_index, wanted_index = _render_index(tree, config.docs_dir)
            if old_index == wanted_index:
                recorded.append(surfaces[1])
            else:
                _atomic_replace(tree / "journal" / "index.md", wanted_index, old_index, tree, root)
                written.append(surfaces[1])
                recorded.append(surfaces[1])
        if not log_done:
            _atomic_replace(log_path, (log_preamble + log_entry + log_entries).encode("utf-8"),
                            old_log, tree, root)
            written.append("log.md")
            recorded.append("log.md")
        return _result("complete", args.mode, timestamp, warnings,
                       surfaces, [], written, None)
    except (Refusal, BionicConfigError, OSError, UnicodeDecodeError, ValueError) as exc:
        pending = [surface for surface in surfaces if surface not in recorded]
        status = "partial" if recorded else "refused"
        return _result(status, args.mode, timestamp, warnings,
                       recorded, pending, written, str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
