# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""advance-run.py — advance a structured `.yaml` promptbook run snapshot by one prompt.

The vendored counterpart to `validate-promptbook.py` for the `run-promptbook`
advance path. Hand-editing a run snapshot across many advances is error-prone,
and a naive re-emit silently drops the run-level trailing fields
(`notes` / `pr_draft` / `summary`) (the Issue-3 hazard). This script never re-emits
the document. It splices each value an advance or abandonment changes into the
original text and appends any key that was absent, so every other byte — comments,
quoting, indentation, timestamp spelling, line endings, and the trailing fields —
stays as it was. A snapshot it cannot splice safely is refused with nothing written.

Two modes.

`--outcome` (advance) mutates ONLY the element whose `n == current_prompt`: sets its
`state`/`started`/`completed`/`result`/`artifacts`, then moves the pointer to the
next `pending` element (set `running`) or, if none remain, marks the run `completed`.
`book_content_hash` and every prior element are left untouched.

`--abandon` (abandon) is a RUN-level act, not a per-prompt one (ADR-0077 clause 5(b),
which deleted the per-prompt `blocked_confirmed` flag). It sets run `status: abandoned`
plus an `abandonment` record, and leaves every prompt element's state untouched — a
prompt left `running` or `pending` is the evidence that the run was abandoned mid-flight.
`abandonment.kind: deliberate` is what makes the book archive-eligible while it holds a
non-terminal prompt; the `abandoned` a later run-start writes over a stale run carries
`kind: superseded` and confers no eligibility. An abandonment is recorded when it is
taken and never retrofitted, so this refuses a run that is already terminal.

Neither terminal status — `completed` nor `abandoned` — nulls the book's `current_run`
pointer. Only a book's current run can authorize its archive (ADR-0077 clause 5(b)), so
the pointer survives both terminal paths; `archive-promptbook` is the sole writer that
nulls it, at archival. This script nulls only `current_prompt` on completion.

Both modes pin `base_commit`. It is written once at run start and never rewritten, and
it is the one value the `patch` tier's archive check reads out of the run — so moving it
forward shrinks the diff that check proves. This script refuses to write a snapshot whose
`base_commit` differs from the value in the snapshot's own committed version. The pin
binds from the snapshot's first commit onward; before that there is no committed record
to compare against and the guard makes no claim.

This is the writer end of the pin. `check-blast-radius.py` refuses the same divergence
at the archive gate, since a hand-edited snapshot can be committed without passing
through this script. Both call `base_commit_pin.divergence` — one implementation, so
the two ends cannot disagree about what the committed record is.

New-format `.yaml` only (a `.yaml` without `format_version`, or a `.md` snapshot, is
refused with the pinned recovery route). It does NOT schema-validate
the snapshot against `run.schema.json` (that is `audit-docs`' job); it guards only the
fields it mutates.

Usage:
    uv run advance-run.py <run-RUN-NNN.yaml> --outcome done|skipped|blocked \
        [--result "one line"] [--artifacts docs/a.md,docs/b.md] \
        [--book <active-book.yaml>]
    uv run advance-run.py <run-RUN-NNN.yaml> --abandon --reason "one line" \
        [--book <active-book.yaml>]

Exit codes (crux convention): 0 clean; 1 usage/validation error (JSON on stdout);
non-zero with empty stdout = crash (stderr).
"""
from __future__ import annotations

import argparse
import copy
import datetime
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write("advance-run.py requires PyYAML — run via `uv run` (PEP 723 supplies it).\n")
    raise SystemExit(2)

# The pin on `base_commit` has two enforcers — this writer and the archive-time
# containment check — so it has one implementation, imported by both. See
# `base_commit_pin.py` for the no-claim lane and the honest limit.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from base_commit_pin import divergence  # noqa: E402  (sys.path insert before import)
from _yaml_min import (  # noqa: E402  (sys.path insert before import)
    CatalogYamlError,
    _normalize,
    _walk_for_duplicate_keys,
    load_yaml,
)

TERMINAL = ("done", "skipped", "blocked")

# The ONLY line this script may rewrite in a book. Anchored and narrowly scoped on
# purpose: an unanchored `current_prompt:` match would also hit a nested or
# commented occurrence. `current_run` is deliberately absent — see the writer below.
BOOK_CURRENT_PROMPT_RE = r"^current_prompt: .*$"


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fail(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(json.dumps({"error": msg}))
    raise SystemExit(1)


def _legacy_refusal(path: Path) -> "NoReturn":  # type: ignore[name-defined]
    _fail(
        f"legacy Markdown promptbook execution is unavailable for {path}. "
        "If every remaining prompt can be truthfully completed, use public "
        "Crux v3.23.2 (tag v3.23.2, commit "
        "08ee30ec2f1d1b4b0ce970f2e1582bb4f83cd20d) on a copy to finish "
        "and archive the Markdown run, then convert its book before its run, "
        "validate the YAML, and upgrade. An already archived eligible run can "
        "be converted there. The tag has no verified Markdown abandon route. "
        "If the run cannot truthfully finish, it remains in_progress and "
        "non-retryable on this version: preserve its original bytes and state "
        "as readable history, and start separate YAML work."
    )


# A top-level key this writer may append in plain style. Anything else is quoted.
_PLAIN_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _emit(value) -> str:
    """One value as single-line YAML flow text. Strings are always double-quoted
    by PyYAML's own emitter, which escapes every character a plain or single-quoted
    scalar could misread (line breaks, U+2028, NEL, quotes, backslashes)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        import math
        if not math.isfinite(value):
            _fail("refusing to write a non-finite number")
        return repr(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        out = yaml.safe_dump(value, default_style='"', allow_unicode=True, width=2**31 - 1)
        if out.endswith("\n"):
            out = out[:-1]
        if "\n" in out or "\r" in out or not (out.startswith('"') and out.endswith('"')):
            _fail("refusing to write: a changed value did not emit as one double-quoted "
                  "line; nothing was written")
        return out
    if isinstance(value, list):
        return "[" + ", ".join(_emit(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_emit(k)}: {_emit(v)}" for k, v in value.items()) + "}"
    _fail(f"refusing to write: cannot write a value of type {type(value).__name__}; "
          "nothing was written")


def _emit_key(key) -> str:
    if isinstance(key, str) and _PLAIN_KEY_RE.fullmatch(key):
        return key
    return _emit(key)


def _path_text(path: tuple) -> str:
    return "".join(f"[{p}]" if isinstance(p, int) else (f".{p}" if i else str(p))
                   for i, p in enumerate(path))


def _changed_paths(before: dict, after: dict, prefix: tuple = ()) -> list[tuple[str, tuple]]:
    """The edits that turn `before` into `after`, as ``("set" | "add", path)``.

    Mappings recurse, and so do equal-length lists of mappings, so a changed prompt
    field names its own path. A removed key is refused: this writer only changes and
    adds values."""
    for key in before:
        if key not in after:
            _fail(f"refusing to write: the update removes {_path_text(prefix + (key,))}; "
                  "only values may change; nothing was written")
    ops: list[tuple[str, tuple]] = []
    for key, value in after.items():
        path = prefix + (key,)
        if key not in before:
            ops.append(("add", path))
            continue
        old = before[key]
        if old == value:
            continue
        if path == ("flow",) and isinstance(value, dict) and value.get("schema_version") == "2":
            ops.append(("set", path))
        elif isinstance(old, dict) and isinstance(value, dict):
            ops.extend(_changed_paths(old, value, path))
        elif (isinstance(old, list) and isinstance(value, list) and len(old) == len(value)
              and all(isinstance(x, dict) for x in old + value)):
            for i, (o, v) in enumerate(zip(old, value)):
                if o != v:
                    ops.extend(_changed_paths(o, v, path + (i,)))
        else:
            ops.append(("set", path))
    return ops


def _locate(root, path: tuple):
    """The key node and value node at `path` in a composed document."""
    key_node, node = None, root
    for step in path:
        if isinstance(step, int):
            key_node, node = None, node.value[step]
            continue
        for k, v in node.value:
            if k.value == step:
                key_node, node = k, v
                break
        else:  # pragma: no cover - the path came from the same document
            _fail(f"refusing to write: {_path_text(path)} is not in the snapshot text; "
                  "nothing was written")
    return key_node, node


def _is_block_collection(node) -> bool:
    return isinstance(node, (yaml.SequenceNode, yaml.MappingNode)) and not node.flow_style


def _span(text: str, key_node, node) -> tuple[int, int]:
    """The ``[start, end)`` text span a new value replaces.

    A scalar or a flow collection spans its own node. A block collection starts
    after its key's colon and ends at its last descendant's content, because the
    collection's own end mark runs past any comment that follows it. Trailing
    whitespace is trimmed, so the line break after the value stays in place."""
    if _is_block_collection(node):
        start = text.index(":", key_node.end_mark.index) + 1
        last = node
        while _is_block_collection(last) and last.value:
            last = last.value[-1] if isinstance(last, yaml.SequenceNode) else last.value[-1][1]
        end = last.end_mark.index
    else:
        start, end = node.start_mark.index, node.end_mark.index
    while end > start and text[end - 1] in " \t\r\n":
        end -= 1
    return start, end


def _block_scalars(node):
    if isinstance(node, yaml.ScalarNode):
        if node.style in ("|", ">"):
            yield node
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            yield from _block_scalars(item)
    elif isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            yield from _block_scalars(k)
            yield from _block_scalars(v)


def _terminators_are_uniform(text: str) -> bool:
    return "\r" not in text or text.count("\r") == text.count("\r\n") == text.count("\n")


def _splice(text: str, before: dict, after: dict) -> str:
    """Return `text` with only the values that differ between `before` and `after`
    rewritten, and any key `after` adds appended at the end of the file.

    Every byte outside the replaced spans is kept. A shape this cannot splice
    without losing text, or without changing a value nobody asked to change, is
    refused with exit 1 and JSON on stdout, before any file is written: mixed line
    endings, an anchor or alias, a duplicate key, a comment inside a replaced
    value or on a replaced block scalar's header line, a removed key, a key added
    below the top level, an unsupported value type, and any YAML error. The last
    guard is semantic: the result must re-read as exactly `after`."""
    if not _terminators_are_uniform(text):
        _fail("refusing to write: the snapshot mixes line endings (CRLF and LF); "
              "nothing was written")
    eol = "\r\n" if "\r\n" in text else "\n"
    try:
        for event in yaml.parse(text):
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
                _fail("refusing to write: the snapshot uses an anchor or alias, which a "
                      "value splice cannot keep consistent; nothing was written")
        root = yaml.compose(text)
        try:
            _walk_for_duplicate_keys(root, yaml)
        except CatalogYamlError as exc:
            _fail(f"refusing to write: the snapshot has a {exc}; nothing was written")
        covered = bytearray(len(text))
        for tok in yaml.scan(text):
            s, e = tok.start_mark.index, tok.end_mark.index
            covered[s:e] = b"\x01" * (e - s)

        edits: list[tuple[int, int, str]] = []
        appends: list[str] = []
        for op, path in _changed_paths(before, after):
            if op == "add":
                if len(path) > 1:
                    _fail(f"refusing to write: the update adds the nested key "
                          f"{_path_text(path)}; only a top-level key can be appended; "
                          "nothing was written")
                appends.append(path[0])
                continue
            key_node, node = _locate(root, path)
            for scalar in _block_scalars(node):
                i = scalar.start_mark.index
                line_end = text.find("\n", i)
                if "#" in text[i:len(text) if line_end < 0 else line_end]:
                    _fail(f"refusing to write: {_path_text(path)} has a comment on a "
                          "block-scalar header line, which replacing the value would "
                          "drop; nothing was written")
            start, end = _span(text, key_node, node)
            for i in range(start, end):
                if not covered[i] and not text[i].isspace():
                    _fail(f"refusing to write: {_path_text(path)} has a comment inside "
                          "the value being replaced, which the replacement would drop; "
                          "nothing was written")
            value = after
            for step in path:
                value = value[step]
            new = _emit(value)
            if start > 0 and text[start - 1] == ":":
                new = " " + new
            edits.append((start, end, new))
    except yaml.YAMLError as exc:
        _fail(f"refusing to write: the snapshot does not parse as YAML "
              f"({type(exc).__name__}); nothing was written")

    out = text
    for start, end, new in sorted(edits, reverse=True):
        out = out[:start] + new + out[end:]
    if appends:
        lines: list[str] = []
        for key in appends:
            value = after[key]
            if isinstance(value, dict) and value:
                lines.append(f"{_emit_key(key)}:")
                lines.extend(f"  {_emit_key(k)}: {_emit(v)}" for k, v in value.items())
            else:
                lines.append(f"{_emit_key(key)}: {_emit(value)}")
        if out and not out.endswith("\n"):
            out += eol
        out += eol.join(lines) + eol

    try:
        reread = load_yaml(out)
    except yaml.YAMLError:
        reread = None
    if reread != _normalize(after) or not _terminators_are_uniform(out):
        _fail("refusing to write: postcondition failed — the spliced snapshot does not "
              "re-read as the intended document; nothing was written")
    return out


def _check_base_commit_pin(run: dict, run_path: Path) -> None:
    """Refuse to write a snapshot whose ``base_commit`` diverges from its committed
    record. ``base_commit`` is written once at run start and never rewritten.

    It is the one value the `patch` tier's archive check reads out of the run, so
    moving it forward shrinks the diff that check proves — which is precisely what an
    overshooting run is motivated to do. This mirrors the never-retrofit guard on
    `abandonment`: a value recorded once is not rewritten by this tool.

    This is the WRITER end of a two-ended pin. `check-blast-radius.py` refuses the
    same divergence at the archive gate, because a hand-edited snapshot can be
    committed without ever passing through this tool. Both ends call
    `base_commit_pin.divergence`, so neither can drift from the other's idea of the
    committed record.

    HONEST LIMIT: the pin binds from the snapshot's first commit onward. In the
    window between run start and that commit there is no committed record, so nothing
    holds the value, and this returns without a claim."""
    diverged = divergence(run, run_path)
    if diverged is None:
        return
    pinned, live = diverged
    _fail(
        f"base_commit is written once at run start and never rewritten, but this "
        f"snapshot's value diverges from its committed record (committed {pinned!r}, "
        f"on disk {live!r}). It is the evidence source the patch tier's archive check "
        f"reads, so moving it shrinks the diff that check proves. Restore the committed "
        f"value, or abandon the run and author a successor book."
    )


def _ensure_trailing_fields(run: dict) -> dict:
    """Existing notes/pr_draft/summary survive because `_splice()` rewrites only the
    values an advance or abandonment changes; this only ensures the keys EXIST
    (absent → "", appended at the end of the file), so a fresh snapshot that never
    populated them stays schema-clean."""
    for k in ("notes", "pr_draft", "summary"):
        run.setdefault(k, "")
    return run


def abandon(run: dict, reason: str) -> dict:
    """Record a DELIBERATE abandonment of the run (a run-level act; the per-prompt
    archive-eligibility flag is retired). Sets the run-level
    `status`/`abandonment`/`completed_at`/`current_prompt` and touches no prompt
    element — the non-terminal prompt left behind is the evidence.

    Refuses to retrofit: an abandonment is recorded at the moment it is taken, so a
    run that is already `completed` or `abandoned`, or that already carries an
    `abandonment` record, is rejected.

    EVERY guard runs before the first mutation, matching ``advance()``. That order is
    load-bearing here in a way it is not elsewhere: because an abandonment is never
    retrofitted, a `status: abandoned` that reached disk from a half-applied call
    would make the retrofit guard refuse every later attempt, and the run could then
    archive by neither path. A validation that runs after the write is not a
    validation."""
    if not reason or not reason.strip():
        _fail("--abandon requires a non-empty --reason")
    if not isinstance(run.get("prompts"), list) or not run["prompts"]:
        _fail("malformed run snapshot: missing or empty 'prompts' list")
    if run.get("abandonment") is not None:
        _fail("run already carries an 'abandonment' record; an abandonment is never retrofitted")
    status = run.get("status")
    if status in ("completed", "abandoned"):
        _fail(f"run status is already terminal ({status!r}); nothing to abandon")

    now = _now()
    run["status"] = "abandoned"
    run["completed_at"] = now
    run["current_prompt"] = None
    run["abandonment"] = {"kind": "deliberate", "at": now, "reason": reason.strip()}
    return _ensure_trailing_fields(run)


def advance(run: dict, outcome: str, result: str, artifacts: list[str]) -> dict:
    """Advance the run document in memory (does no filesystem I/O — that is main()'s
    job — but may _fail() with a JSON error + SystemExit on bad input). Returns the
    mutated run document. Does NOT schema-validate against run.schema.json (audit does
    that separately); it guards only the keys it touches."""
    if "flow" in run:
        from crux.flow.records import guard_advance
        guard_advance(run, outcome, result)
    if outcome not in TERMINAL:
        _fail(f"--outcome must be one of {TERMINAL}")
    if not isinstance(run.get("prompts"), list) or not run["prompts"]:
        _fail("malformed run snapshot: missing or empty 'prompts' list")

    cur = run.get("current_prompt")
    if cur is None:
        _fail("run has current_prompt: null (already completed); nothing to advance")

    prompts = run["prompts"]
    try:
        el = next(p for p in prompts if p["n"] == cur)
    except StopIteration:
        _fail(f"no prompt element with n == current_prompt ({cur})")

    now = _now()
    if el.get("started") is None:
        el["started"] = now
    el["state"] = outcome
    el["completed"] = now
    el["result"] = result
    el["artifacts"] = artifacts

    nxt = next((p for p in prompts if p["state"] == "pending"), None)
    if nxt is not None:
        nxt["state"] = "running"
        if nxt.get("started") is None:
            nxt["started"] = now
        run["current_prompt"] = nxt["n"]
    else:
        run["status"] = "completed"
        run["completed_at"] = now
        run["current_prompt"] = None

    return _ensure_trailing_fields(run)


def _preflight_patch_completion(book_path: Path, run_path: Path) -> None:
    """Refuse a patch's final advance before it can become unarchivable."""
    try:
        repo = subprocess.run(["git", "-C", str(book_path.parent), "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        root = Path(repo).resolve()
        resolved_book = book_path.resolve()
        resolved_run = run_path.resolve()
        if not resolved_book.is_relative_to(root) or not resolved_run.is_relative_to(root):
            _fail("patch completion preflight: book and run must be inside the same repository")
        if resolved_book.parent.name != "active" or resolved_book.parent.parent.name != "promptbooks":
            _fail("patch completion preflight: book is outside the active promptbook directory")
        docs = resolved_book.parent.parent.parent.relative_to(root)
        command = [sys.executable, str(Path(__file__).with_name("check-blast-radius.py")),
                   "--repo-root", str(root), "--docs-dir", str(docs),
                   "--book", str(resolved_book), "--run", str(resolved_run)]
        process = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        try:
            status = process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
            _fail("patch completion preflight timed out; run remains active")
    except (OSError, ValueError, subprocess.SubprocessError):
        _fail("patch completion preflight unavailable; run remains active")
    if status != 0:
        _fail("patch containment failed before completion; run remains active; inspect check-blast-radius.py output")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Advance a .yaml promptbook run snapshot by one prompt, "
                    "or record a deliberate abandonment of the run.")
    ap.add_argument("run", help="path to run-RUN-NNN.yaml")
    ap.add_argument("--outcome", choices=TERMINAL, help="advance mode: the current prompt's terminal state")
    ap.add_argument("--result", default="")
    ap.add_argument("--artifacts", default="", help="comma-separated docs/ paths")
    ap.add_argument("--abandon", action="store_true",
                    help="abandon mode: record a deliberate abandonment of the RUN (needs --reason)")
    ap.add_argument("--reason", default="", help="one-line reason for --abandon")
    ap.add_argument("--book", default=None, help="active book .yaml — its current_prompt pointer is updated too")
    args = ap.parse_args(argv)

    if args.abandon and args.outcome:
        _fail("--abandon and --outcome are mutually exclusive (abandonment is a run-level act)")
    if not args.abandon and not args.outcome:
        _fail("one of --outcome or --abandon is required")

    run_path = Path(args.run)
    if not run_path.is_file():
        _fail(f"run snapshot not found: {run_path}")
    if run_path.suffix != ".yaml":
        if run_path.suffix == ".md":
            _legacy_refusal(run_path)
        _fail(f"only .yaml run snapshots are supported: {run_path}")

    if args.book and Path(args.book).suffix == ".md":
        _legacy_refusal(Path(args.book))

    # Bytes in, bytes out: text-mode I/O would translate CRLF line endings.
    try:
        text = run_path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        _fail(f"run snapshot is not UTF-8 text: {run_path}")
    try:
        run = load_yaml(text)
    except yaml.YAMLError as exc:
        _fail(f"run snapshot does not parse as YAML ({type(exc).__name__}): {run_path}")
    if not isinstance(run, dict) or "format_version" not in run:
        _fail("malformed new-format run snapshot: missing top-level format_version")
    if "flow" in run:
        from crux.flow.common import FlowError
        from crux.flow.records import advance_file
        try:
            if args.abandon:
                raise FlowError("fork cancellation requires crux-flow run stop CANCELLED; history is not silently abandoned")
            answer = advance_file(run_path, outcome=args.outcome, result=args.result,
                                  artifacts=[a for a in args.artifacts.split(",") if a],
                                  book_path=Path(args.book) if args.book else None)
            print(json.dumps(answer))
            return 0
        except (FlowError, OSError) as exc:
            print(json.dumps({"error": str(exc)}))
            return 1
    original = copy.deepcopy(run)

    # Both modes write, so both are pinned. Checked before either mutates.
    _check_base_commit_pin(run, run_path)

    # `--book` is VALIDATED BEFORE THE RUN SNAPSHOT IS WRITTEN. A validation that
    # runs after the write is not a validation: a refusal would leave the run
    # advanced while the book's pointer stayed stale, and re-running the same
    # command would then mark `done` a prompt that was never executed. `abandon()`
    # states the same rule for its own guards.
    book_path = None
    book_text = ""
    pre_current_run = None
    if args.book:
        book_path = Path(args.book)
        if not book_path.is_file():
            _fail(f"--book not found: {book_path}")
        book_text = book_path.read_text()
        try:
            pre_write_book = yaml.safe_load(book_text)
        except yaml.YAMLError as exc:
            _fail(f"--book does not parse as YAML ({type(exc).__name__}); only a "
                  f"new-format .yaml book may be passed to --book: {book_path}")
        if not isinstance(pre_write_book, dict):
            _fail(f"--book does not parse as a YAML mapping: {book_path}")
        pre_current_run = pre_write_book.get("current_run")
        n_matches = len(re.findall(BOOK_CURRENT_PROMPT_RE, book_text, flags=re.M))
        if n_matches != 1:
            _fail(
                f"--book has {n_matches} lines matching '{BOOK_CURRENT_PROMPT_RE}' "
                f"(expected exactly 1); refusing to rewrite an ambiguous book: {book_path}"
            )

    if args.abandon:
        run = abandon(run, args.reason)
    else:
        artifacts = [a for a in (s.strip() for s in args.artifacts.split(",")) if a]
        run = advance(run, args.outcome, args.result, artifacts)
    if run.get("status") == "completed" and book_path is None and run_path.parent.parent.name == "runs":
        active = run_path.parent.parent.parent / "active" / f"{run_path.parent.name}.yaml"
        if active.is_symlink():
            _fail("completing run points to a symlinked active book")
        if active.is_file():
            try:
                discovered = yaml.safe_load(active.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, yaml.YAMLError):
                _fail("completing run's active book cannot be read")
            if isinstance(discovered, dict) and discovered.get("cycle_kind") == "patch":
                _fail("patch completion requires --book so containment can be checked before writing")
    if (run.get("status") == "completed" and book_path is not None
            and pre_write_book.get("cycle_kind") == "patch"):
        _preflight_patch_completion(book_path, run_path)

    # Two preconditions on the `--book` rewrite, BOTH checked before either write.
    # A precondition that fires below the writes has already advanced the run and
    # rewritten a book it then refuses — the same ordering defect the `--book`
    # validation above was hoisted to close.
    if book_path is not None:
        cp = run["current_prompt"]
        if not (cp is None or (isinstance(cp, int) and not isinstance(cp, bool))):
            _fail(
                f"run's current_prompt is {type(cp).__name__}, not an int or null; "
                f"refusing to interpolate it into the book rewrite: {run_path}"
            )
        # A precondition on PRE-EXISTING book state: a book whose `current_run` is
        # null or malformed cannot authorize the archive the completing advance is
        # handing it, so the advance is refused rather than half-applied.
        if cp is None and run["status"] == "completed":
            if not isinstance(pre_current_run, str) or not re.match(r"^RUN-\d{3}$", pre_current_run):
                _fail(
                    f"--book's current_run does not match ^RUN-\\d{{3}}$ on a "
                    f"completing advance (got {pre_current_run!r}): {book_path}"
                )

    # The splice and its postcondition run before EITHER write, so a refusal leaves
    # the run and the book exactly as they were.
    new_text = _splice(text, original, run)
    run_path.write_bytes(new_text.encode("utf-8"))

    if book_path is not None:
        # Neither a completed nor an abandoned run nulls the book's `current_run`
        # pointer — only a book's current run can authorize its archive (ADR-0077
        # clause 5(b)). `archive-promptbook` is the sole writer that nulls
        # `current_run`, and it does so at archival. This writer only ever moves
        # `current_prompt`.
        replacement = ("current_prompt: null" if run["current_prompt"] is None
                        else f"current_prompt: {run['current_prompt']}")
        # A `lambda` replacement, never a string. `re.sub` processes backreferences
        # in a string replacement, so a `\1` or `\g<0>` reaching `replacement`
        # would splice matched book text back into the book. The type check above
        # bounds the VALUE to an int or null; this bounds the MECHANISM.
        book_text = re.sub(BOOK_CURRENT_PROMPT_RE, lambda _m: replacement, book_text,
                           count=1, flags=re.M)
        book_path.write_text(book_text)

        try:
            post_write_book = yaml.safe_load(book_path.read_text())
        except yaml.YAMLError as exc:
            _fail(f"--book no longer parses as YAML after write "
                  f"({type(exc).__name__}); the rewrite corrupted it: {book_path}")
        if not isinstance(post_write_book, dict):
            _fail(f"--book no longer parses as a YAML mapping after write: {book_path}")
        post_current_run = post_write_book.get("current_run")
        if post_current_run != pre_current_run:
            _fail(
                f"--book's current_run pointer changed during a write that must not "
                f"touch it (was {pre_current_run!r}, now {post_current_run!r}): {book_path}"
            )
    done = sum(1 for p in run["prompts"] if p["state"] in TERMINAL)
    payload = {
        "run": str(run_path),
        "current_prompt": run["current_prompt"],
        "status": run["status"],
        "terminal": f"{done}/{len(run['prompts'])}",
    }
    if run.get("abandonment"):
        payload["abandonment_kind"] = run["abandonment"]["kind"]
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
