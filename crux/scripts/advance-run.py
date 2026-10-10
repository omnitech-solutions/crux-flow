# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0,<7", "httpx>=0.27,<1"]
# ///
"""advance-run.py — advance a structured `.yaml` promptbook run snapshot by one prompt.

The vendored counterpart to `validate-promptbook.py` for the `run-promptbook`
advance path. Hand-editing a run snapshot across many advances is error-prone,
and a naive re-emit silently drops the run-level trailing fields
(`notes` / `pr_draft` / `summary`) (the Issue-3 hazard). This script never re-emits
the document. It splices each value an advance or abandonment changes into the
original text and appends any key that was absent, so every other byte — comments,
quoting, indentation, timestamp spelling, line endings, and the trailing fields —
stays as it was, with one exception. A route and a re-advance of a blocked prompt each append
a dated entry to `notes`. A literal block (`|`, `|-`, `|+`) or an empty quoted value takes the
entry in a block and keeps every earlier byte. Any other `notes` shape (plain, folded or quoted
text) cannot take the entry in place, and rewriting it would re-encode the earlier notes, so the
advance is refused with nothing written: rewrite `notes` as a literal block and run it again.
A `notes: null` holds no earlier byte and takes the entry as one quoted line. A snapshot it
cannot splice safely is refused with nothing written.

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
`base_commit` differs from the value in the snapshot's own committed version. Before the
snapshot's first commit there is no committed record to compare against and the guard
makes no claim. The committed version it compares against is the one at HEAD, so this
pin holds only against an uncommitted edit: a rewrite committed to HEAD passes it, and
the patch tier's archive check then reads the rewritten value. The gate check below
reads `base_commit` from history instead.

This is the writer end of the pin. `check-blast-radius.py` refuses the same divergence
at the archive gate, since a hand-edited snapshot can reach that gate without passing
through this script. It too compares against HEAD, so it refuses an uncommitted edit only. Both call `base_commit_pin.divergence` — one implementation, so
the two ends cannot disagree about what the committed record is.

The gate check. Every `--outcome` needs a resolvable book whose content hash matches the
run's `book_content_hash`, gate prompt or not. It resolves the book (`--book`, else
`<docs>/promptbooks/active/<dir>.yaml`, else `.../archive/<dir>.yaml`); an unresolvable or
mismatched book refuses the advance with nothing written. `--abandon` needs no book.

`cycle_grandfathered` and `cycle_kind` lie outside the content hash, so an in-flight edit to
either moves no hash. The advance reads both from the book as it stood at the run's
`base_commit` (`council_gate.start_fields`) and refuses, with nothing written, when either now
differs. That `base_commit` is the value in the snapshot's first committed version that records
one, and an advance or `--gate-info` whose live value differs from it is refused, so a rewrite of
`base_commit` alone, committed or not, cannot re-bind the fields to a later commit where the book
carries `cycle_grandfathered`.
When that book cannot be read (no `base_commit`, no book at that commit, a book with another
content hash, or a committed version of the snapshot that does not parse), classification
ignores both fields and reads only the hash-bound `module_tag` prefix and `phase`, so a
module-tagged gate prompt stays a gate. Every `--outcome` result's `gate` object carries
`cycle_fields`: `run-start` or `unbound`.

What this check does not hold, and no prose can enforce (committed tampering with a run
record lies outside the local-tool threat model):

* An edit to `base_commit` made before the snapshot's first commit, or while no committed
  version records the field, is read as the run-start value.
* A history rewrite that replaces the snapshot's first committed version replaces the
  reference the check reads.
* A committed rewrite of `base_commit` together with `run_id` (or with `book_content_hash`,
  the book edited to match) re-keys the run: every earlier version at the path names
  another run and is skipped, so the rewrite reads as the run's first version.
* A committed copy of the snapshot at a new path (`run-RUN-002.yaml`) has a history that
  starts at the copy, so the copy's value is its first version.
* A `git mv` and a rewrite in one commit does the same, because history is read without
  rename following.
* The same class is open at the gate itself. This script judges only the current prompt's
  evidence, so a committed hand-edit that marks a gate prompt `done`, or moves
  `current_prompt` past it, is never checked by any script.

It then classifies the current prompt by position (`council_gate.classify`). At a gate prompt
the evidence is judged, never the `--result` text:

* `council` and `module-close` prompts admit only committed council records that name
  `run-council.py` as their writer (a self-asserted field), bound to this book, run and module,
  whose seats match the current router registry assignment, with the deciding record attached.
  An uncommitted record refuses the advance with nothing written. The council runner commits
  attempt and council records itself, so for one of those the refusal names recovery
  (`run-council.py --recover`); the conductor commits an uncommitted refutation or
  owner-exception record before the advance. A record committed in HEAD under the run's
  `council/` directory that is missing or modified in the working tree refuses too (except a
  format-2 council record still present but altered, which names a committed attempt: its
  attempt stays open and stops at 4), and so does
  a `.json` path under that directory named in the run snapshot that is missing. A permanent stop (a held, misnumbered or tied round, a scan-refused round, an
  ARCHITECTURAL vote) therefore holds once its record is committed, and clears only by committed
  tampering outside the local-tool threat model, which no script holds against: a committed
  rewrite of its record; a committed deletion, when no prompt's current artifacts name the
  record; or a switch to a branch without the stop. A re-advance of the blocked prompt replaces
  that prompt's artifacts and keeps the old list only in its notes, which the gate does not read.
  Council attempts close the discard window for format-2 councils: the council runner commits
  an attempt record before any council request, and an open committed attempt in scope stops
  the gate at 4 before any record decides, with its attempt record attached to `--outcome
  blocked`. A held council record deleted before its first commit therefore leaves its attempt
  open, and a fresh round at the same number never passes over it. A format-2 record whose seal
  fails, or whose bytes differ from its pending copy, is no evidence and takes no place. A
  deciding `preflight-retries-spent` record stops at 1 and a `preflight-needs-owner` record at 4;
  neither takes a place. A void-attempt owner record closes an attempt only while no council
  record naming it survives in the working tree or in HEAD and no pending copy names it. What still escapes: a format-1 record was written
  without a claim, so the window stays open for it, and the gate admits one only in a run whose
  base_commit (as the run's history pins it) predates format version 2; a void-attempt is
  owner-asserted, since no vendored writer proves its authorship; deleting the preflight
  diagnostics log buys more retries, never a pass; committed tampering lies outside the
  local-tool threat model. The gate never verifies that the owner cleared a could-not-run or
  preflight stop: a later converged round at the same number passes whether or not the owner
  acted. Advancing the record `blocked` records the stop in the run snapshot; the wait for the
  owner holds by prose. The remedy for a permanent stop is to
  abandon the run and author a successor book. A could-not-run stop clears when a later round
  converges, and an unauthorized round or a spent round bound clears once the owner commits an
  owner-exception record for that place and the round in it converges.
* `independent-review` prompts admit only reviewer reports whose reviewed paths are clean.
* `done` is allowed only on a `pass` verdict, and `skipped` is refused.
* At an independent-review gate, `blocked` with a valid reviewer report advances the prompt
  `blocked` and moves the pointer. At a council gate, `blocked` with a `pass` verdict is refused.
* `blocked` needs `--artifacts`. A `stop` verdict writes the prompt `blocked` and keeps
  `current_prompt` on it. A `route` verdict moves the run to the module's ordinal-3 prompt
  (a patch `verify` phase writes nothing). A `refuse` verdict writes nothing.
* A prompt that is not a gate advances as before, unless, in a format-two run, a closed `adr-*`
  module's ADR is still pending acceptance (see `_prior_adr_closes_or_fail`).

Every `--outcome` result carries a `gate` object. A gate prompt whose text carries the
withdrawn council alternative also carries `withdrawn` and `correction_notice`; the book is
never rewritten. Only an explicit `--book` is rewritten, and only its `current_prompt` line.

`--gate-info [--prompt N]` is read-only: it prints the class, module, ordinal, phase and the
evidence a prompt requires, plus the correction notice when its text carries the withdrawn
alternative, plus `adr_acceptance_pending` (null in a format-one run). It writes nothing anywhere.

Symlinks. The run snapshot and an explicit `--book` are refused, with nothing written, when
opening the path as given would follow a symlink at its leaf, or a symlink held in a directory
inside a git repository (`council_records.reached_through_symlink`, the check run-council.py
makes on the same two paths). The repository is found from the directory that holds each
link, never from the link's target, so an in-repository link to a directory outside every
repository, or to another repository's root, is refused, and `link/..` is walked through the
link. A link held outside every repository (macOS `/var`) is followed. When git cannot judge a
holding directory, its link is refused.

New-format `.yaml` only (a `.yaml` without `format_version`, or a `.md` snapshot, is
refused with the pinned recovery route). It does NOT schema-validate
the snapshot against `run.schema.json` (that is `audit-docs`' job); it guards only the
fields it mutates.

Usage:
    # Every --outcome needs a resolvable book whose content hash matches the run's.
    uv run advance-run.py <run-RUN-NNN.yaml> --outcome done|skipped|blocked \
        [--result "one line"] [--artifacts docs/a.md,docs/b.md] \
        [--book <active-book.yaml>]
    uv run advance-run.py <run-RUN-NNN.yaml> --abandon --reason "one line" \
        [--book <active-book.yaml>]
    uv run advance-run.py <run-RUN-NNN.yaml> --gate-info [--prompt N] [--book <book.yaml>]

Exit codes (crux convention): 0 clean; 1 usage/validation/gate refusal (JSON on stdout);
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
import stat
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

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
import bionic_config  # noqa: E402  (sys.path insert before import)
import council_gate  # noqa: E402  (sys.path insert before import)
import council_records  # noqa: E402
import implementation_approval  # noqa: E402
from council_alternative import CORRECTION_NOTICE, find_withdrawn_alternative  # noqa: E402
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


def _fail(msg: str, **extra) -> NoReturn:
    print(json.dumps({"error": msg, **extra}))
    raise SystemExit(1)


def _legacy_refusal(path: Path) -> NoReturn:
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


def _block_lines(text: str, start: int, eol: str):
    """Yield ``(line, line_end, next_start, had_eol)`` for each line from `start`."""
    pos = start
    while pos <= len(text):
        nl = text.find(eol, pos)
        line_end = len(text) if nl < 0 else nl
        nxt = len(text) if nl < 0 else nl + len(eol)
        yield text[pos:line_end], line_end, nxt, nl >= 0
        if nl < 0:
            return
        pos = nxt


def _notes_block_edit(text: str, key_node, node, old, new, eol: str):
    """A text-level edit that appends to the run's `notes`, or None for the generic rewrite.

    The generic rewrite emits one double-quoted line, which wrecks run notes that are often
    hundreds of lines. Two shapes keep their layout instead. A literal block scalar (`|`, `|-`,
    `|+`) takes the new lines at its own indentation, after its last content line (after its
    trailing blank lines for `|+`), so every earlier byte stays. An empty quoted scalar becomes a
    new `|` block. Any other shape, and any value whose appended text a literal block cannot
    carry, returns None. The caller's postcondition still re-reads the whole document."""
    if not (isinstance(old, str) and isinstance(new, str) and isinstance(node, yaml.ScalarNode)
            and new.startswith(old) and len(new) > len(old)):
        return None
    suffix = new[len(old):]
    col = key_node.start_mark.column
    if old == "":
        if node.style not in ("'", '"') or new.endswith("\n\n") or not new.endswith("\n") \
                or new[0] in " \n\t":
            return None
        end = node.end_mark.index
        rest = text[end:text.find("\n", end) if text.find("\n", end) >= 0 else len(text)]
        if rest.strip():
            return None  # a comment follows the value on its line
        ind = " " * (col + 2)
        body = "".join(eol + (ind + l if l else "") for l in new[:-1].split("\n"))
        return node.start_mark.index, end, "|" + body
    if node.style != "|":
        return None
    i = node.start_mark.index
    header_end = text.find(eol, i)
    if header_end < 0:
        return None
    header = text[i:header_end].split("#", 1)[0]
    chomp = "strip" if "-" in header else "keep" if "+" in header else "clip"
    digits = [c for c in header if c.isdigit()]
    indent = col + int(digits[0]) if digits else None
    lines = list(_block_lines(text, header_end + len(eol), eol))
    if indent is None:
        first = next((ln for ln, *_ in lines if ln.strip()), None)
        if first is None:
            return None
        indent = len(first) - len(first.lstrip(" "))
    if indent <= col:
        return None
    last_end = last_next = block_end = None
    for line, line_end, nxt, had_eol in lines:
        if not had_eol and line == "":
            continue  # the terminal empty segment after a final line terminator is no line
        # A whitespace-only line is content when it reaches past the block indent; the others
        # are blank lines, which a literal block keeps as newlines.
        if line.strip() or len(line) > indent:
            if len(line) - len(line.lstrip(" ")) < indent:
                break
            last_end, last_next = line_end, (nxt if had_eol else None)
        block_end = nxt if had_eol else None
    if last_end is None:
        return None
    ind = " " * indent

    def rows(body: str) -> str:
        return "".join((ind + l if l else "") + eol for l in body.split("\n"))

    if chomp == "strip":
        if old.endswith("\n") or not suffix.startswith("\n") or suffix.endswith("\n"):
            return None
        added = "".join(eol + (ind + l if l else "") for l in suffix[1:].split("\n"))
        return last_end, last_end, added
    if not suffix.endswith("\n") or suffix.endswith("\n\n"):
        return None
    if last_next is None:
        # The last content line ends the file with no line terminator, so the block's value has
        # no final newline either; the entry follows on new lines and the file then ends in one.
        if old.endswith("\n") or not suffix.startswith("\n"):
            return None
        return last_end, last_end, "".join(eol + (ind + l if l else "") for l in suffix[1:-1].split("\n")) + eol
    if not old.endswith("\n"):
        return None
    at = last_next if chomp == "clip" else block_end
    if at is None:
        return None
    return at, at, rows(suffix[:-1])


def _notes_shape(text: str) -> str:
    """How the snapshot's `notes` scalar is written: `empty` (an empty quoted scalar), a literal
    block's chomping (`clip`, `strip`, `keep`) or `other`."""
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        return "other"
    if not isinstance(root, yaml.MappingNode):
        return "other"
    for k, v in root.value:
        if k.value == "notes" and isinstance(v, yaml.ScalarNode):
            if v.style == "|":
                header = text[v.start_mark.index:text.find("\n", v.start_mark.index)].split("#", 1)[0]
                return "strip" if "-" in header else "keep" if "+" in header else "clip"
            return "empty" if v.value == "" and v.style in ("'", '"') else "other"
    return "other"


def append_run_note(run: dict, entry: str, shape: str) -> None:
    """Append one note entry (no trailing newline) to `run["notes"]`, shaped so the text-level
    append in `_notes_block_edit` reproduces exactly this value. Does no I/O."""
    old = run.get("notes")
    old = old if isinstance(old, str) else ""
    if old == "":
        run["notes"] = entry + "\n"
    elif shape in ("clip", "keep", "empty"):
        # `empty` described the original text; once an entry is in memory the value is a block.
        run["notes"] = old + "\n" + entry + "\n"
    else:
        run["notes"] = old + "\n\n" + entry


def _splice(text: str, before: dict, after: dict) -> str:
    """Return `text` with only the values that differ between `before` and `after`
    rewritten, and any key `after` adds appended at the end of the file.

    Every byte outside the replaced spans is kept. A shape this cannot splice
    without losing text, or without changing a value nobody asked to change, is
    refused with exit 1 and JSON on stdout, before any file is written: mixed line
    endings, an anchor or alias, a duplicate key, a comment inside a replaced
    value or on a replaced block scalar's header line, a removed key, a key added
    below the top level, an unsupported value type, a `notes` append that cannot be made
    in place, and any YAML error. The last guard is semantic: the result must re-read as
    exactly `after`."""
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
        used_block = False
        for op, path in _changed_paths(before, after):
            if op == "add":
                if len(path) > 1:
                    _fail(f"refusing to write: the update adds the nested key "
                          f"{_path_text(path)}; only a top-level key can be appended; "
                          "nothing was written")
                appends.append(path[0])
                continue
            key_node, node = _locate(root, path)
            if path == ("notes",) and before.get("notes") is not None:
                # Notes stay byte-unchanged: the entry goes in place, or nothing is written.
                appended = _notes_block_edit(text, key_node, node, before.get("notes"), after.get("notes"), eol)
                if appended is None:
                    _fail("refusing to write: the run's notes cannot take the entry in place (only a "
                          "literal block `|`, `|-`, `|+` or an empty quoted value can), and rewriting "
                          "them would re-encode the earlier notes; rewrite notes as a literal block and "
                          "run the advance again; nothing was written")
                edits.append(appended)
                used_block = True
                continue
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
        what = " (the in-place notes append)" if used_block else ""
        _fail(f"refusing to write: postcondition failed{what} — the spliced snapshot does not "
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
    same divergence at the archive gate, because a hand-edited snapshot can reach that
    gate without ever passing through this tool. Both ends call
    `base_commit_pin.divergence`, so neither can drift from the other's idea of the
    committed record.

    HONEST LIMIT: the pin compares against the snapshot's version at HEAD, so it holds
    only against an uncommitted edit. A rewrite committed to HEAD becomes the committed
    record and passes. In the window between run start and the snapshot's first commit
    there is no committed record, so nothing holds the value, and this returns without
    a claim."""
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


def _element(run: dict, n):
    try:
        return next(p for p in run["prompts"] if p["n"] == n)
    except StopIteration:
        _fail(f"no prompt element with n == {n}")


def block_in_place(run: dict, result: str, artifacts: list[str]) -> dict:
    """Record the current prompt `blocked` with its evidence and keep `current_prompt`
    on it. The run stays `in_progress`. Does no I/O."""
    el = _element(run, run.get("current_prompt"))
    now = _now()
    if el.get("started") is None:
        el["started"] = now
    el["state"] = "blocked"
    el["completed"] = now
    el["result"] = result
    el["artifacts"] = artifacts
    return _ensure_trailing_fields(run)


def keep_blocked_evidence(run: dict, outcome: str, result: str, artifacts: list[str],
                          notes_shape: str) -> None:
    """Before an advance overwrites the current prompt's result and artifacts, append one dated
    entry to the run's `notes` when that prompt is already `blocked` and holds a result or
    artifacts (a stopped
    gate re-advanced). A prompt that is not `blocked`, or holds neither, adds nothing. Every
    string is JSON-encoded, as in `_route_note`, and the append is `append_run_note`. Does no I/O."""
    cur = run.get("current_prompt")
    el = next((p for p in run.get("prompts") or [] if isinstance(p, dict) and p.get("n") == cur), None)
    if el is None or el.get("state") != "blocked":
        return
    prev_result, prev_artifacts = el.get("result", ""), list(el.get("artifacts") or [])
    if not prev_result and not prev_artifacts:
        return
    entry = "\n".join([
        f"[{_now()}] re-advance of blocked prompt {cur} with outcome {json.dumps(outcome)}",
        f"  previous state: {json.dumps(el.get('state'))}",
        f"  previous result: {json.dumps(prev_result)}",
        f"  previous artifacts: {json.dumps(prev_artifacts)}",
        f"  new result: {json.dumps(result)}",
        f"  new artifacts: {json.dumps(artifacts)}"])
    append_run_note(run, entry, notes_shape)


def _route_note(now: str, cur, gate, nxt_n, result, artifacts, verdict, prior: list[tuple]) -> str:
    """One note entry for a route. Every string is JSON-encoded (ASCII), so the entry is one
    safe line per field in a literal block, whatever the caller's text holds."""
    v = verdict.to_dict() if verdict is not None else {}
    lines = [f"[{now}] route from prompt {cur} ({gate.cls}) to prompt {nxt_n}",
             f"  result: {json.dumps(result)}",
             f"  artifacts: {json.dumps(artifacts)}",
             f"  gate: verdict={json.dumps(v.get('verdict'))} stops={json.dumps(v.get('stops'))} "
             f"deciding_record={json.dumps(v.get('deciding_record'))}"]
    for n, res, arts in prior:
        lines.append(f"  prompt {n} previous result: {json.dumps(res)} artifacts: {json.dumps(arts)}")
    return "\n".join(lines)


def route_to_ordinal3(run: dict, gate, result: str, artifacts: list[str], verdict=None,
                      notes_shape: str = "other") -> dict:
    """Move the run back to the module's ordinal-3 prompt for another round.

    From ordinal 2 the current prompt is recorded `blocked` with its evidence. From
    ordinal 4 it returns to `pending`, so advancing ordinal 3 lands on it again.

    A route resets prompts, so it first appends one dated entry to the run's `notes`: the
    routing prompt, this invocation's result and artifacts, the gate verdict, and the result and
    artifacts the reset discards (the ordinal-3 prompt's, and the ordinal-4 prompt's when the
    route starts there). Ordinal 3 then returns to `running` with its result and artifacts
    cleared; its next advance writes its own. Does no I/O."""
    if gate.ordinal3 is None:
        _fail("the module has no ordinal-3 prompt to route to; nothing was written")
    _ensure_trailing_fields(run)
    cur = run["current_prompt"]
    el = _element(run, cur)
    nxt = _element(run, gate.ordinal3)
    now = _now()
    prior = [(nxt["n"], nxt.get("result", ""), list(nxt.get("artifacts") or []))]
    if gate.cls == "module-close":
        prior.append((el["n"], el.get("result", ""), list(el.get("artifacts") or [])))
    append_run_note(run, _route_note(now, cur, gate, nxt["n"], result, artifacts, verdict, prior),
                    notes_shape)
    if gate.cls == "module-close":
        el["state"] = "pending"
        el["completed"] = None
        el["result"] = ""
        el["artifacts"] = []
    else:
        if el.get("started") is None:
            el["started"] = now
        el["state"] = "blocked"
        el["completed"] = now
        el["result"] = result
        el["artifacts"] = artifacts
    nxt["state"] = "running"
    if nxt.get("started") is None:
        nxt["started"] = now
    nxt["completed"] = None
    nxt["result"] = ""
    nxt["artifacts"] = []
    run["current_prompt"] = nxt["n"]
    return run


def _refuse_symlinked(path: Path, what: str) -> None:
    """Refuse `path` when opening it, exactly as given, would follow a symlink at its leaf or
    one held inside a git repository (`council_records.reached_through_symlink`, the check
    run-council.py makes on the same two paths)."""
    if council_records.reached_through_symlink(path):
        _fail(f"{what} {path} is reached through a symlink, at its leaf or inside a repository; "
              "it is never read or written through one; when the checkout itself is reached through "
              f"a symlink, name the {what} by its physical path and retry; nothing was written")


def _start_fields_or_fail(run: dict, run_path: Path, resolved):
    """The run-start values of the unbound fields, refusing when the live book moved either."""
    start = council_gate.start_fields(run, run_path, resolved.path)
    if start.base_rewritten:
        _fail(f"the run's base_commit {run.get('base_commit')!r} differs from {start.base!r}, the value "
              "in the snapshot's first committed version that records one; base_commit is written once at run start and "
              "never rewritten, and the gate reads the book's unbound fields at that value; restore it, "
              "or abandon the run and start a new one; nothing was written")
    moved = ([] if resolved.book.get("format_version") == "2" else
             council_gate.unbound_field_changes(resolved.book, start))
    if moved:
        _fail(f"the book's {', '.join(moved)} differs from its value at run start (the book at "
              f"base_commit {run.get('base_commit')}); the field lies outside the content hash, so an "
              "in-flight edit cannot change how a prompt is gated; restore it, or abandon the run and "
              "start a new one; nothing was written", moved_fields=moved)
    return start


def _format_two_or_fail(run, book):
    """Strict schema and positional shape even without legacy start history."""
    try:
        if run.get("format_version") != "2" or book.get("format_version") != "2":
            raise implementation_approval.Refused("format-binding-refused")
        validator = council_records._validator()
        errors = []
        for doc, schema in ((book, validator.PROMPTBOOK_SCHEMA), (run, validator.RUN_SCHEMA)):
            validator.validate(doc, validator.load_schema(schema), "#", "#", errors, "cycle")
            validator.post_schema_pass(doc, errors, "cycle")
        if errors:
            raise implementation_approval.Refused("format-two-schema-refused")
        validator.validate_format_two_run(run, book)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        _fail(getattr(exc, "code", "format-two-validation-refused") + "; nothing was written")


def _prior_implementation_closes_or_fail(run, book, run_path):
    """DONE alone never establishes a prior formal close."""
    declarations = {item["slot"]: item for item in book["implementation_slots"]}
    for container, subject_key in (("implementation_bindings", "revision"), ("migration_bindings", "batch")):
        for binding in run.get(container, []):
            try:
                declaration = declarations[binding["slot"]]
                repo = council_records.repo_root(run_path)
                if subject_key == "batch":
                    proof = implementation_approval.validate_historical_migration_binding(repo, run_path,
                        slot=binding["slot"], batch_path=binding["batch"]["path"],
                        batch_sha256=binding["batch"]["sha256"])
                    implementation_approval._migration_subject_constraints(repo, run_path,
                        slot=binding["slot"], batch_path=binding["batch"]["path"],
                        batch_sha256=binding["batch"]["sha256"])
                else:
                    proof = implementation_approval.validate_implementation_binding(repo, run_path,
                        slot=binding["slot"], revision_path=binding["revision"]["path"],
                        revision_sha256=binding["revision"]["sha256"], historical_revision=True)
                implementation_approval.require(proof.binding == binding, "binding-entry-mismatch")
            except (implementation_approval.Refused, KeyError, TypeError) as exc:
                _fail(getattr(exc, "code", "binding-declaration-refused") + "; nothing was written")
    for declaration in book["implementation_slots"]:
        slot = declaration["slot"]
        members = [p["n"] for p in book["prompts"] if
                   (p.get("phase") == "verify" if slot == "patch" else p.get("module_tag") == slot)]
        close = next((p for p in run["prompts"] if p["n"] == members[-1]), None)
        if close is None or close.get("state") != "done":
            continue
        migration = "migration_batch" in declaration
        bindings = [b for b in run["migration_bindings" if migration else "implementation_bindings"]
                    if b["slot"] == slot]
        if not bindings:
            _fail("formal DONE has no successful close binding; nothing was written")


# An ADR file path under any `adrs/` directory, its identifier carrying the optional artifact
# prefix (an upper-case lead such as `PB-`).
_ADR_PATH = re.compile(r"^(?!/)(?:[^/]+/)*adrs/(?:archive/)?(?P<id>(?:[A-Z][A-Z0-9]*-)?ADR-\d{4})-[^/]+\.md$")
_ADR_READ_LIMIT = 1 << 20
_ABSENT = object()


def _read_in_repo(repo, rel):
    """The bytes of `rel` under `repo`; `_ABSENT` when a component does not exist; None when the
    path is refused: absolute, a `..` or empty segment, a symlink at any component below the repo
    root, a non-regular leaf, or unreadable. Never follows a symlink and never leaves the repo."""
    if repo is None or not isinstance(rel, str) or not rel or "\x00" in rel or rel.startswith("/"):
        return None
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    cur = Path(repo)
    for part in parts:
        cur = cur / part
        try:
            mode = os.lstat(cur).st_mode
        except FileNotFoundError:
            return _ABSENT
        except OSError:
            return None
        if stat.S_ISLNK(mode):
            return None
    if not stat.S_ISREG(mode):
        return None
    try:
        fd = os.open(cur, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            return stream.read(_ADR_READ_LIMIT)
    except OSError:
        return None


_ADR_CLEARING_STATUSES = frozenset({"accepted", "deprecated", "superseded"})


def _adr_status(repo, rel):
    """The `status:` of the ADR at `rel` (else the same basename under `adrs/archive/`), or
    "unreadable". A path that exists but is refused is never retried under the archive."""
    candidates = [rel]
    head, _, base = rel.rpartition("/")
    if not head.endswith("/archive"):
        candidates.append(f"{head}/archive/{base}")
    for candidate in candidates:
        data = _read_in_repo(repo, candidate)
        if data is _ABSENT:
            continue
        if data is None:
            return "unreadable"
        try:
            text = data.decode("utf-8")
            if not (text.startswith("---\n") or text.startswith("---\r\n")):
                return "unreadable"
            front = load_yaml(re.split(r"^---\s*$", text, maxsplit=2, flags=re.M)[1])
        except (UnicodeDecodeError, IndexError, CatalogYamlError, yaml.YAMLError, ValueError):
            return "unreadable"
        status = front.get("status") if isinstance(front, dict) else None
        return status.strip() if isinstance(status, str) and status.strip() else "unreadable"
    return "unreadable"


def _record_subjects(repo, rel):
    """The ADR paths a committed council or refutation record names as subjects. The artifact is
    resolved as the council gate resolves it, so a `./` or an absolute spelling reads the same
    record the gate accepted."""
    if repo is None:
        return []
    try:
        _, rel = council_records.resolve_in_repo(repo, rel)
    except council_records.PathRefused:
        return []
    data = _read_in_repo(repo, rel)
    if not isinstance(data, bytes):
        return []
    try:
        doc = json.loads(data)
    except ValueError:
        return []
    if not isinstance(doc, dict) or doc.get("record_type") not in ("council-record", "refutation-record"):
        return []
    subjects = doc.get("subjects")
    if not isinstance(subjects, list):
        return []
    return [s["path"] for s in subjects
            if isinstance(s, dict) and isinstance(s.get("path"), str) and _ADR_PATH.match(s["path"])]


def _tree_adr_path(repo):
    """The pattern a path must match to name an ADR: an ADR file directly under
    `<docs_dir>/adrs/` or `<docs_dir>/adrs/archive/`. When the layout config cannot be read, an
    ADR file under any `adrs/` directory matches."""
    try:
        docs_dir = bionic_config.load_config(repo).docs_dir
    except (bionic_config.BionicConfigError, OSError, ValueError, TypeError):
        return _ADR_PATH
    prefix = re.escape(os.path.normpath(docs_dir).strip("/"))
    return re.compile(rf"^{prefix}/adrs/(?:archive/)?(?P<id>(?:[A-Z][A-Z0-9]*-)?ADR-\d{{4}})-[^/]+\.md$")


def _named_adr(repo, text, pattern):
    """`(id, repo-relative path)` when `text`, resolved as the council gate resolves an artifact
    (`council_records.resolve_in_repo`, so a `./` or an absolute spelling counts), is an ADR file
    path that `pattern` matches; None for anything else, including an identifier in free text."""
    if repo is None or not isinstance(text, str):
        return None
    try:
        _, rel = council_records.resolve_in_repo(repo, text)
    except (council_records.PathRefused, OSError, ValueError):
        return None
    found = pattern.match(rel)
    return (found.group("id"), rel) if found else None


def _adr_acceptance_pending(run, book, run_dir, repo):
    """None for a format-one run. Otherwise one entry per ADR of a closed `adr-*` module (its
    fourth prompt is `done`) whose status is not Accepted, Deprecated or Superseded, or is
    unreadable, or that cannot be identified.

    Subjects are the ADR paths the module's council and refutation records (ordinals 2-4) name;
    the deciding subjects are those of the records attached at the close (ordinal 4). The module
    names an ADR only through an artifact (ordinals 1-4) or a run-work witness entry for its
    prompts whose path is an ADR file under the tree's `adrs/` (`_named_adr`). The candidates are
    the named ADRs, each read from every subject path that carries its identifier and from the
    named path when no subject does; with none named, every ADR subject; with no ADR subject, one
    `unidentified` entry. Each entry carries a `remedy`. `transition-adr` goes to a Proposed ADR
    only when it is the one deciding subject the module names. Every other entry takes `owner`: an
    ADR named only by the module, a second decided ADR, a subjects fallback, an unidentified or
    unreadable ADR, and any other status."""
    if run.get("format_version") != "2":
        return None
    states = {p["n"]: p for p in run.get("prompts") or []}
    witness = None
    if repo is not None:
        try:
            witness = _read_in_repo(repo, (Path(run_dir) / "run-work-witness.json").relative_to(repo).as_posix())
        except ValueError:
            witness = None
    try:
        entries = json.loads(witness).get("entries", []) if isinstance(witness, bytes) else []
    except (ValueError, AttributeError):
        entries = []
    if not isinstance(entries, list):
        entries = []
    pending = []
    pattern = _tree_adr_path(repo)
    tags = sorted({p["module_tag"] for p in book["prompts"]
                   if str(p.get("module_tag") or "").startswith("adr-")})
    for tag in tags:
        members = [p["n"] for p in book["prompts"] if p.get("module_tag") == tag]
        close = states.get(members[-1])
        if close is None or close.get("state") != "done":
            continue
        arts = {n: [a for a in (states.get(n, {}).get("artifacts") or []) if isinstance(a, str)]
                for n in members}
        subjects = []
        for n in members[1:4]:
            for art in arts[n]:
                if art.endswith(".json"):
                    subjects += [s for s in _record_subjects(repo, art) if s not in subjects]
        deciding = []
        for art in arts[members[-1]]:
            if art.endswith(".json"):
                deciding += [s for s in _record_subjects(repo, art) if s not in deciding]
        named = {}
        for n in members[:4]:
            for art in arts[n]:
                hit = _named_adr(repo, art, pattern)
                if hit:
                    named.setdefault(*hit)
        for entry in entries:
            if isinstance(entry, dict) and entry.get("prompt") in members:
                hit = _named_adr(repo, entry.get("path"), pattern)
                if hit:
                    named.setdefault(*hit)
        by_id = {}
        for s in subjects:
            by_id.setdefault(_ADR_PATH.match(s).group("id"), []).append(s)
        # The ADRs the module's deciding record carries and the module names.
        decided = [s for s in deciding if _ADR_PATH.match(s).group("id") in named]
        if named:
            # The module's own ADRs, whether or not a council subject names them.
            candidates = []
            for i in sorted(named):
                if i in by_id:
                    candidates += [(s, "subjects-and-module") for s in by_id[i]]
                else:
                    candidates.append((named[i], "module"))
        elif subjects:
            candidates = [(s, "subjects") for s in subjects]
        else:
            pending.append({"module_tag": tag, "path": None, "status": "unidentified",
                            "identified_by": "subjects", "remedy": "owner"})
            continue
        for path, how in candidates:
            status = _adr_status(repo, path)
            if status == "unreadable" or status.lower() not in _ADR_CLEARING_STATUSES:
                # Only the one Proposed ADR the council decided and the module named.
                remedy = ("transition-adr" if status.lower() == "proposed" and how == "subjects-and-module"
                          and decided == [path] else "owner")
                pending.append({"module_tag": tag, "path": path, "status": status,
                                "identified_by": how, "remedy": remedy})
    return pending


def _prior_adr_closes_or_fail(run, book, run_path):
    """Refuse every advance but `--abandon` while a closed adr module's ADR is not Accepted,
    Deprecated or Superseded, or is unreadable or unidentified. `blocked` and `skipped` are refused
    too: at a prompt that is not a gate each outcome moves the pointer, and a run with no pending
    prompt left reaches `completed` with the ADR still Proposed."""
    pending = _adr_acceptance_pending(run, book, Path(run_path).resolve().parent,
                                      council_records.repo_root(run_path))
    if pending:
        listing = "; ".join(f"{e['module_tag']}: {e['path'] or 'no ADR named'}, status {e['status']}, "
                            f"remedy {e['remedy']}" for e in pending)
        steps = []
        if any(e["remedy"] == "transition-adr" for e in pending):
            steps.append("Accept each ADR marked transition-adr with transition-adr, only if "
                         "transition-adr has not yet run for this module; if it already ran, stop for "
                         "the owner.")
        if any(e["remedy"] == "owner" for e in pending):
            steps.append("Report every entry marked owner to the owner, who restores an unreadable "
                         "file or decides.")
        _fail("a closed adr module's ADR is not Accepted, Deprecated or Superseded, or is unreadable or "
              f"unidentified ({listing}). {' '.join(steps)} Otherwise abandon the run. Nothing was written.",
              adr_acceptance_pending=pending)


def _publish_format_two(run_path, content, before, retained):
    """One atomic run publication couples DONE and binding; cleanup only owned files."""
    repo = council_records.repo_root(run_path)
    owned = []
    temporary = run_path.with_name(run_path.name + ".advance-tmp")
    owns_temporary = False
    published = False
    try:
        for rel, data in retained.items():
            path, _ = council_records.resolve_in_repo(repo, rel)
            _refuse_symlinked(path, "retained gate context")
            if path.exists():
                if not path.is_file() or path.read_bytes() != data:
                    _fail("immutable gate-context collision; nothing was written")
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                owned.append(path)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        with temporary.open("xb") as stream:
            owns_temporary = True
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if run_path.read_bytes() != before:
            _fail("run changed during close preparation; nothing was written")
        os.replace(temporary, run_path)
        owns_temporary = False
        published = True
    except (OSError, council_records.RecordError):
        _fail("atomic run publication refused; nothing was written")
    finally:
        if owns_temporary:
            temporary.unlink(missing_ok=True)
        if not published:
            for path in owned:
                path.unlink(missing_ok=True)


def _resolve_book_or_fail(run_path: Path, book_arg):
    try:
        return council_records.resolve_book(run_path, book_arg)
    except council_records.RecordError as exc:
        _fail(f"cannot resolve the run's book: {exc}; nothing was written")


def _prompt_text(prompt: dict) -> str:
    return "\n".join(str(prompt.get(k) or "") for k in ("title", "purpose", "prompt", "expected_output"))


def _withdrawn_fields(book: dict, n) -> dict:
    prompt = next((p for p in book.get("prompts") or [] if isinstance(p, dict) and p.get("n") == n), None)
    found = find_withdrawn_alternative(_prompt_text(prompt)) if prompt else []
    return {"withdrawn": found, "correction_notice": CORRECTION_NOTICE if found else None}


_COUNCIL_EVIDENCE = ("a committed council record that names run-council.py as its writer (a self-asserted "
                     "field), bound to this book, run and module; only the seats of the record that decides "
                     "(the deciding council record, or the council record a deciding refutation names) "
                     "are checked against the current router registry assignment; attach the deciding "
                     "record, council or refutation, committed, with --artifacts; an open council "
                     "attempt in scope (an attempt record no committed, sealed council record resolves "
                     "and no owner void-attempt record voids) stops the gate at 4 before any record "
                     "decides: attach its attempt record with --outcome blocked")

_REQUIRES = {
    "council": _COUNCIL_EVIDENCE + "; the record committed last decides: it converges, or in an adr "
               "module a refutation record shows every blocking finding refuted",
    "module-close": _COUNCIL_EVIDENCE + "; the deciding record is the one committed last: a council "
                    "record, or in an adr module a refutation record; a module whose council ran before "
                    "records existed closes only on a round convened at module close with run-council.py",
    "independent-review": "a reviewer report for this book, run and prompt, attached with --artifacts, "
                          "whose reviewed paths are clean (HEAD, index and working tree agree); an "
                          "attached artifact that is not a .json reviewer report is refused",
    "internal-review": "nothing: an internal review is not a gate",
    "unclassified": "nothing: this prompt is not a gate",
}


def gate_info(run: dict, run_path: Path, args) -> int:
    """The read-only `--gate-info` query. Writes nothing anywhere."""
    resolved = _resolve_book_or_fail(run_path, args.book)
    if run.get("format_version") == "2" or resolved.book.get("format_version") == "2":
        _format_two_or_fail(run, resolved.book)
    n = args.prompt if args.prompt is not None else run.get("current_prompt")
    if n is None:
        _fail("run has current_prompt: null; pass --prompt N")
    prompt = next((p for p in resolved.book.get("prompts") or [] if isinstance(p, dict) and p.get("n") == n), None)
    if prompt is None:
        _fail(f"the book has no prompt {n}")
    start = _start_fields_or_fail(run, run_path, resolved)
    gate = council_gate.classify(resolved.book, n, start)
    print(json.dumps({
        "prompt": n, "class": gate.cls, "module_tag": gate.module_tag, "ordinal": gate.ordinal,
        "phase": gate.phase, "cycle_fields": "run-start" if start.known else "unbound",
        "requires": _REQUIRES[gate.cls], **_withdrawn_fields(resolved.book, n),
        "adr_acceptance_pending": _adr_acceptance_pending(
            run, resolved.book, Path(run_path).resolve().parent, council_records.repo_root(run_path)),
    }))
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Advance a .yaml promptbook run snapshot by one prompt, "
                    "or record a deliberate abandonment of the run.")
    ap.add_argument("run", help="path to run-RUN-NNN.yaml")
    ap.add_argument("--outcome", choices=TERMINAL,
                    help="advance mode: the current prompt's terminal state; every --outcome needs a "
                         "resolvable book whose content hash matches the run's book_content_hash")
    ap.add_argument("--result", default="")
    ap.add_argument("--artifacts", default="", help="comma-separated docs/ paths")
    ap.add_argument("--abandon", action="store_true",
                    help="abandon mode: record a deliberate abandonment of the RUN (needs --reason)")
    ap.add_argument("--reason", default="", help="one-line reason for --abandon")
    ap.add_argument("--book", default=None, help="active book .yaml — its current_prompt pointer is updated too")
    ap.add_argument("--gate-info", action="store_true",
                    help="read-only: print the gate class and the evidence a prompt requires")
    ap.add_argument("--prompt", type=int, default=None, help="prompt n for --gate-info (default: the current prompt)")
    selection = ap.add_mutually_exclusive_group()
    selection.add_argument("--implementation-revision", default=None,
                    help="exact retained implementation revision at a declared successful close")
    selection.add_argument("--migration-batch", default=None,
                          help="exact migration batch at its declared successful close")
    args = ap.parse_args(argv)

    if args.abandon and args.outcome:
        _fail("--abandon and --outcome are mutually exclusive (abandonment is a run-level act)")
    if args.gate_info and (args.abandon or args.outcome):
        _fail("--gate-info is read-only and cannot be combined with --outcome or --abandon")
    if not args.abandon and not args.outcome and not args.gate_info:
        _fail("one of --outcome, --abandon or --gate-info is required")
    if args.prompt is not None and not args.gate_info:
        _fail("--prompt is only valid with --gate-info")

    run_path = Path(args.run)
    if not run_path.is_file():
        _fail(f"run snapshot not found: {run_path}")
    if run_path.suffix != ".yaml":
        if run_path.suffix == ".md":
            _legacy_refusal(run_path)
        _fail(f"only .yaml run snapshots are supported: {run_path}")

    if args.book and Path(args.book).suffix == ".md":
        _legacy_refusal(Path(args.book))

    # Before anything is read: a run snapshot or an explicit book is never reached through a symlink.
    _refuse_symlinked(run_path, "run snapshot")
    if args.book:
        _refuse_symlinked(Path(args.book), "--book")

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
    if run.get("format_version") not in ("1", "2"):
        _fail("unsupported live run format; nothing was written")
    version_two = run.get("format_version") == "2"
    if (args.implementation_revision or args.migration_batch) and (not version_two or args.abandon or args.gate_info):
        _fail("implementation revision is only selected at a format-two close; nothing was written")
    if "flow" in run:
        # Flow's own runs: the fork's acceptance gate is the writer, never upstream's.
        # Flow runs are not council-gated cycle runs, so the selectors and --gate-info are refused by name.
        from crux.flow.common import FlowError
        from crux.flow.records import advance_file
        try:
            if args.abandon:
                raise FlowError("fork cancellation requires crux-flow run stop CANCELLED; history is not silently abandoned")
            if args.gate_info or args.implementation_revision or args.migration_batch:
                raise FlowError("a Flow run has no council gate: --gate-info and the implementation selectors apply to upstream cycle runs only")
            answer = advance_file(run_path, outcome=args.outcome, result=args.result,
                                  artifacts=[a for a in args.artifacts.split(",") if a],
                                  book_path=Path(args.book) if args.book else None)
            print(json.dumps(answer))
            return 0
        except (FlowError, OSError) as exc:
            print(json.dumps({"error": str(exc)}))
            return 1
    original = copy.deepcopy(run)

    if args.gate_info:
        return gate_info(run, run_path, args)

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
        if "format_version" in pre_write_book and pre_write_book["format_version"] != run["format_version"]:
            _fail("unsupported live book format; nothing was written")
        pre_current_run = pre_write_book.get("current_run")
        n_matches = len(re.findall(BOOK_CURRENT_PROMPT_RE, book_text, flags=re.M))
        if n_matches != 1:
            _fail(
                f"--book has {n_matches} lines matching '{BOOK_CURRENT_PROMPT_RE}' "
                f"(expected exactly 1); refusing to rewrite an ambiguous book: {book_path}"
            )

    gate_out: dict = {}
    gate_extra: dict = {}
    retained = {}
    if args.abandon:
        if version_two:
            resolved = _resolve_book_or_fail(run_path, args.book)
            _format_two_or_fail(run, resolved.book)
        run = abandon(run, args.reason)
    else:
        artifacts = [a for a in (s.strip() for s in args.artifacts.split(",")) if a]
        # The book is resolved and hash-verified before anything is written.
        resolved = _resolve_book_or_fail(run_path, args.book)
        if "format_version" in resolved.book and resolved.book["format_version"] != run["format_version"]:
            _fail("unsupported live book format; nothing was written")
        if version_two:
            _format_two_or_fail(run, resolved.book)
            try:
                implementation_approval.full_history(council_records.repo_root(run_path))
            except implementation_approval.Refused as exc:
                _fail(exc.code + "; nothing was written")
            _prior_implementation_closes_or_fail(run, resolved.book, run_path)
            _prior_adr_closes_or_fail(run, resolved.book, run_path)
        cur = run.get("current_prompt")
        start = _start_fields_or_fail(run, run_path, resolved)
        keep_blocked_evidence(run, args.outcome, args.result, artifacts, _notes_shape(text))
        gate = council_gate.classify(resolved.book, cur, start)
        if version_two:
            current = next((p for p in run["prompts"] if p["n"] == cur), None)
            if current is None or current.get("state") not in ("running", "blocked"):
                _fail("current prompt is already terminal; nothing was written")
        slot = "patch" if gate.module_kind == "patch" else gate.module_tag
        formal_close = version_two and any(s["slot"] == slot for s in resolved.book["implementation_slots"]) and (
            gate.cls == "module-close" or (gate.module_kind == "patch" and gate.phase == "verify"))
        if (args.implementation_revision or args.migration_batch) and not formal_close:
            _fail("implementation revision is not at a declared close; nothing was written")
        gate_out = {**gate.to_dict(), "cycle_fields": "run-start" if start.known else "unbound"}
        if gate.cls in council_gate.GATE_CLASSES:
            gate_extra = _withdrawn_fields(resolved.book, cur)
        if gate.cls not in council_gate.GATE_CLASSES:
            run = advance(run, args.outcome, args.result, artifacts)
        else:
            if args.outcome == "skipped":
                _fail("a gate prompt cannot be skipped", gate=gate_out, **gate_extra)
            if args.outcome == "blocked" and not artifacts:
                _fail("--outcome blocked at a gate prompt needs --artifacts naming the evidence",
                      gate=gate_out, **gate_extra)
            verdict = council_gate.evaluate_gate(
                gate, run, run_path, council_records.repo_root(run_path), artifacts, args.outcome,
                start=start)
            gate_out = {**gate_out, **verdict.to_dict()}
            reason = "; ".join(verdict.reasons) or verdict.verdict
            if args.outcome == "done":
                if verdict.verdict != "pass":
                    _fail(f"the gate does not pass ({verdict.verdict}): {reason}; nothing was written",
                          gate=gate_out, **gate_extra)
                if formal_close:
                    declaration = next(s for s in resolved.book["implementation_slots"] if s["slot"] == slot)
                    migration = "migration_batch" in declaration
                    selected = args.migration_batch if migration else args.implementation_revision
                    if not selected or (args.implementation_revision if migration else args.migration_batch):
                        _fail("declared subject selector required at close; nothing was written")
                    try:
                        prepare = (implementation_approval.prepare_migration_close if migration else
                                   implementation_approval.prepare_implementation_close)
                        binding, retained = prepare(council_records.repo_root(run_path), run_path,
                                                    gate, selected, artifacts)
                    except (implementation_approval.Refused, council_records.RecordError, OSError,
                            ValueError, TypeError, KeyError) as exc:
                        _fail(getattr(exc, "code", "implementation-close-evidence-invalid") + "; nothing was written")
                    run["migration_bindings" if migration else "implementation_bindings"].append(binding)
                run = advance(run, "done", args.result, artifacts)
            elif verdict.verdict == "refuse":
                _fail(f"the gate refused the evidence: {reason}; nothing was written",
                      gate=gate_out, **gate_extra)
            elif (verdict.verdict == "pass" and gate.cls == "independent-review"
                  and args.outcome == "blocked"):
                # A blocked review is not a named stop: the valid report is its evidence.
                run = advance(run, "blocked", args.result, artifacts)
            elif verdict.verdict == "pass":
                _fail("the gate passes; advance the prompt with --outcome done; nothing was written",
                      gate=gate_out, **gate_extra)
            elif verdict.verdict == "stop":
                run = block_in_place(run, args.result, artifacts)
            elif gate.module_kind == "patch":
                done = sum(1 for p in run["prompts"] if p["state"] in TERMINAL)
                print(json.dumps({"run": str(run_path), "current_prompt": run["current_prompt"],
                                  "status": run["status"], "terminal": f"{done}/{len(run['prompts'])}",
                                  "written": False, "result": args.result, "artifacts": artifacts,
                                  "gate": gate_out, **gate_extra}))
                return 0
            else:
                run = route_to_ordinal3(run, gate, args.result, artifacts, verdict, _notes_shape(text))
    if (run.get("status") == "completed" and not args.abandon
            and resolved.book.get("cycle_kind") == "patch"):
        # Fork correctness exception: refuse a patch's final advance that could not be archived.
        if book_path is None:
            _fail("patch completion requires --book so containment can be checked before writing")
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
    if version_two:
        _publish_format_two(run_path, new_text.encode("utf-8"), text.encode("utf-8"), retained)
    else:
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
        try:
            book_path.write_text(book_text)
        except OSError:
            if version_two:
                _fail("run publication succeeded; book pointer write refused; reconcile the pointer from the run",
                      run_written=True, book_written=False)
            raise

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
    if gate_out:
        payload["gate"] = gate_out
        payload.update(gate_extra)
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    # Repeated read-only git queries inside one invocation are answered from memory
    # while the repository state they read is byte-identical; see git_read_cache.
    import git_read_cache  # noqa: E402
    with git_read_cache.scope():
        status = main(sys.argv[1:])
    raise SystemExit(status)
