#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0,<7"]
# ///
"""write-review-report.py - the reviewer-report writer.

    uv run write-review-report.py <run-RUN-NNN.yaml> --prompt N \\
        (--range BASE..END | --path P [--path P ...]) \\
        (--verdict TEXT | --verdict-file FILE) \\
        [--finding TEXT ...] [--finding-file FILE ...] [--reviewer TEXT] [--book BOOK]

Writes one reviewer report under ``<run_dir>/reviews/``. The gate check at advance
(`advance-run.py`) accepts that report as the evidence of an independent review. A
council record never stands in for it, and this report never satisfies a council gate:
``reviews/`` sits beside ``council/``, which the council gate discovers.

What the writer binds and checks. Every refusal precedes the write.

1. The run snapshot, which must exist, sit inside a repository and parse (an environment
   fault otherwise). Then the book, resolved and verified by content hash against the run
   (``council_records.resolve_book``), and the prompt, which must exist in the run. The
   report carries ``book.id``, ``content_hash``, ``run_id`` and ``prompt``;
   ``reviewer_role`` is always ``reviewer``.
2. The subject. Each ``--path`` must be tracked, not a symlink, and unchanged in HEAD, the
   index and the working tree; its sha256 is read from HEAD. Each end of ``--range`` must
   resolve to a commit, and the report records both ends as full commit ids (40 hex digits in
   a SHA-1 repository, 64 in a SHA-256 repository), so an abbreviated id or a ref such as
   ``HEAD~1`` is recorded as the commit it named.
3. The text inputs. ``--verdict-file`` and each ``--finding-file`` keep model-written text
   out of the shell: the path must name a regular file, not a symlink, holding UTF-8; one
   trailing newline is removed and the rest is kept verbatim. Findings from files follow the
   ``--finding`` values. A missing, symlinked, non-regular or non-UTF-8 file is refused.
4. The secret scan over the verdict, each finding and the reviewer, by key shape only,
   whether the text came from argv or a file. A refusal names the field and the shape, never
   the value. A secret with no known shape passes.
5. The reviewer-report schema. The writer emits ``format_version`` ``"2"``, whose range admits
   64-hex ends; ``council_records.reviewer_report_errors`` chooses the schema by format.
6. The subject again with ``council_gate.subject_problems``, the implementation the gate
   runs. A range is checked only here.

The file is created with ``O_EXCL`` and ``O_NOFOLLOW`` relative to a directory descriptor
opened on ``reviews/`` with ``O_NOFOLLOW``, so an existing file is never replaced and a
swap of ``reviews/`` after the check is not followed. A swap of an ancestor of the run
directory between the check and the open is followed, and the report then lands where the
swapped ancestor points. No false pass follows: the gate refuses a path reached through a
symlink and reads only reports inside the repository.
A failed write removes the partial file. A run snapshot or ``reviews/`` reached through a
symlink, or a ``reviews/`` outside the repository, is refused.

What it warns about. A range whose base is not the run's ``base_commit`` is still written.
The stdout JSON then carries a line in ``warnings`` naming both commits. A run snapshot with
no ``base_commit`` warns too. The prescribed range is ``<the run's base_commit>..<HEAD at
dispatch>``. The gate check does not compare the base, and a scoped review may review a
narrower range, so the writer is no stricter than the gate. ``warnings`` is an empty list
when nothing warns.

What it does not do. It cannot tell whether a review happened: the verdict and findings
are whatever the caller passes. It does not classify the prompt; the gate check at
advance decides which gate a prompt is.

Exit codes. 0: a report was written; one JSON line on stdout carries its repo-relative
path. 1: the report was refused and nothing was written; the JSON line lists the problems.
2: an environment fault: the run is outside a repository, the run snapshot is missing,
unreadable or not parseable YAML, the output cannot be created or written, or the usage is
wrong. ``run-council.py`` classes the same three run faults as exit 2.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import stat
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402  (sys.path insert before import)

import council_gate as cg  # noqa: E402
import council_records as cr  # noqa: E402
import crux_env  # noqa: E402
import secret_scan as ss  # noqa: E402

RECORD_TYPE = "reviewer-report"
_ATTEMPTS = 8
KEY_NAME = "OPENROUTER_API_KEY"


class Refused(Exception):
    """The report cannot be written; `problems` lists why."""

    def __init__(self, *problems: str):
        super().__init__("; ".join(problems))
        self.problems = list(problems)


class Fault(Exception):
    """An environment fault: nothing can be judged or written."""


_SKIP_WARNED = False


def _exact() -> list[str]:
    """The gateway key as a list of one, for the exact-value scan; empty when none can be read.

    The key is read the way run-council.py reads it. An unparseable crux env file yields no key
    and prints nothing of the file: the parser's exception carries the raw offending line, which
    can be the key itself, so its text is never shown. The skip is announced once per process on
    stderr, however many scans read the key. The shape scan still runs without the key."""
    global _SKIP_WARNED
    try:
        key = crux_env.get(KEY_NAME)
    except Exception:  # noqa: BLE001 - whatever crux_env raises, its text is never shown
        if not _SKIP_WARNED:
            _SKIP_WARNED = True
            print("write-review-report: the exact-key scan was skipped: the crux env file could not "
                  "be parsed; the shape scan still runs", file=sys.stderr)
        return []
    return [key] if key else []


WITHHELD_MESSAGE = "write-review-report: a message was withheld because it matched the secret scan"


def _safe(message: str) -> str:
    """`message`, or a fixed line when it matches a key shape or the gateway key. Argparse and a
    refusal echo argument values, so every printed message passes through here."""
    return WITHHELD_MESSAGE if ss.scan_text(message, exact=_exact()) else message


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        print(_safe(f"write-review-report: usage error: {message}"), file=sys.stderr)
        raise SystemExit(2)


_EPILOG = """\
exit codes:
  0 a report was written; one JSON line on stdout carries its repository-relative path
  1 the report was refused and nothing was written; the JSON line lists the problems
  2 an environment fault: the run is outside a repository, the run snapshot is missing or
    unreadable, the output cannot be written, or the usage is wrong
"""


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="write-review-report.py", description="Write one reviewer report.",
                epilog=_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run", help="the run snapshot, run-RUN-NNN.yaml")
    p.add_argument("--prompt", type=int, required=True,
                   help="the prompt number the report is bound to")
    subject = p.add_mutually_exclusive_group(required=True)
    subject.add_argument("--range", dest="range_", metavar="BASE..END",
                         help="review a commit range, BASE..END; BASE should be the run's "
                              "base_commit (the writer warns otherwise)")
    subject.add_argument("--path", action="append", dest="paths", metavar="P",
                         help="review a tracked file, unchanged in HEAD, the index and the working "
                              "tree; repeatable")
    verdict = p.add_mutually_exclusive_group(required=True)
    verdict.add_argument("--verdict", help="the verdict text")
    verdict.add_argument("--verdict-file", metavar="FILE",
                         help="read the verdict from a regular file, not a symlink, holding UTF-8; "
                              "one trailing newline is removed")
    p.add_argument("--finding", action="append", dest="findings", default=[],
                   help="one finding as text; repeatable, in order, before the --finding-file values")
    p.add_argument("--finding-file", action="append", dest="finding_files", default=[],
                   metavar="FILE",
                   help="read one finding from a regular file, not a symlink, holding UTF-8; "
                        "repeatable")
    p.add_argument("--reviewer",
                   help="free text; the writer records it and never checks it")
    p.add_argument("--book", help="the book file when the run layout does not find it")
    return p


def _stamp() -> tuple[str, str]:
    """(file-name stamp, written_at) from one UTC reading, to the microsecond."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%S%fZ"), now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _bind(run_arg: str, book_arg: str | None, prompt: int) -> tuple[Path, dict, dict, Path]:
    """(run path, run, book, repo). Raises `Refused` or `Fault`."""
    run_path = Path(os.path.abspath(run_arg))
    # Two walks, as run-council.py makes. The raw form reads `link/..` through `link`, which the
    # normalised `abspath` form has already dropped. The `abspath` form is the one opened below, so
    # it is walked too: a raw walk that stops at a dangling link held outside every repository
    # never reaches an in-repository link the normalised path then goes through.
    raw = Path(os.path.join(os.getcwd(), run_arg))
    if cr.reached_through_symlink(raw) or cr.reached_through_symlink(run_path):
        raise Refused(f"{run_arg}: the run snapshot is reached through a symlink")
    if not run_path.is_file():
        raise Fault("the run snapshot does not exist")
    repo = cr.repo_root(run_path.parent)
    if repo is None:
        raise Fault("the run snapshot is not inside a git repository")
    try:
        run = yaml.safe_load(run_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise Fault(f"the run snapshot cannot be read ({type(exc).__name__})") from None
    if not isinstance(run, dict) or not isinstance(run.get("run_id"), str):
        raise Refused("the run snapshot carries no run_id")
    try:
        resolved = cr.resolve_book(run_path, book_arg)
    except cr.BookResolutionError as exc:
        raise Refused(f"the book cannot be bound: {exc.message}") from None
    if not isinstance(run.get("book_id"), str):
        raise Refused("the run snapshot carries no book_id")
    numbers = {p.get("n") for p in run.get("prompts") or [] if isinstance(p, dict)}
    if prompt not in numbers:
        raise Refused(f"prompt {prompt} is not a prompt of run {run['run_id']}")
    return run_path, run, resolved.book, repo


def _env_file_identities() -> set[tuple[int, int]]:
    """The (st_dev, st_ino) of each crux env file that exists: `~/.crux/env` and, when CRUX_HOME is
    set, `$CRUX_HOME/env`."""
    out = set()
    homes = [Path(os.path.expanduser("~")) / ".crux"]
    if os.environ.get("CRUX_HOME"):
        homes.append(Path(os.environ["CRUX_HOME"]))
    for env_file in (home / "env" for home in homes):
        try:
            st = os.stat(env_file)
        except OSError:
            continue
        out.add((st.st_dev, st.st_ino))
    return out


def _read_text_file(given: str, field: str) -> str:
    """The text of a `--verdict-file` or `--finding-file`, less one trailing newline.

    The path is taken as given, not normalised, so `link/..` is read through `link`. A path whose
    real path lies under the crux home, or whose name is an env file, is refused before it is
    opened: a file the caller names is never read from where the gateway key lives. After the
    open, a file whose device and inode match a crux env file is refused too, which catches a hard
    link and a file swapped in between the check and the open. Raises `Refused` naming the field
    and the path, never the content."""
    path = Path(os.path.join(os.getcwd(), given))
    location = ss.check_secret_location(path)
    if location == "crux-home":
        raise Refused(f"{field} file {given}: lies under the crux home")
    if location == "env-file":
        raise Refused(f"{field} file {given}: is an env file")
    if path.is_symlink():
        raise Refused(f"{field} file {given}: is a symlink")
    try:
        # O_NONBLOCK: opening a FIFO for reading would otherwise wait for a writer.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise Refused(f"{field} file {given}: does not exist") from None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise Refused(f"{field} file {given}: is a symlink") from None
        raise Refused(f"{field} file {given}: cannot be opened ({type(exc).__name__})") from None
    try:
        st = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise Refused(f"{field} file {given}: is not a regular file")
    if (st.st_dev, st.st_ino) in _env_file_identities():
        # The opened file IS a crux env file: a hard link to it, or a file swapped in after the
        # location check above.
        os.close(fd)
        raise Refused(f"{field} file {given}: is the crux env file")
    with os.fdopen(fd, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise Refused(f"{field} file {given}: is not UTF-8") from None
    return text[:-1] if text.endswith("\n") else text


def _texts(args: argparse.Namespace) -> tuple[str, list[str]]:
    """(verdict, findings) from argv and files. Raises `Refused`."""
    verdict = args.verdict if args.verdict is not None else _read_text_file(args.verdict_file, "verdict")
    findings = list(args.findings)
    findings += [_read_text_file(f, "finding") for f in args.finding_files]
    return verdict, findings


def _resolve_range(given: str, repo: Path) -> tuple[str, str]:
    """(full base, full end) of `given`. Raises `Refused`."""
    base, sep, end = given.partition("..")
    if not sep or not base or not end or end.startswith("."):
        raise Refused(f"{given}: the range is not <base>..<end>")
    resolved = []
    for name in (base, end):
        sha = cg._rev_commit(repo, name)
        if sha is None:
            raise Refused(f"{given}: {name} is not a commit, or names more than one")
        resolved.append(sha)
    return resolved[0], resolved[1]


def _base_warnings(run: dict, base: str, repo: Path) -> list[str]:
    """A warning when the range base is not the run's `base_commit`."""
    recorded = run.get("base_commit")
    if not isinstance(recorded, str) or not recorded:
        return [f"the run snapshot records no base_commit, so the range base {base} cannot be "
                "compared with it"]
    run_base = cg._rev_commit(repo, recorded)
    if run_base is None:
        return [f"the run's base_commit {recorded} is not a commit, so the range base {base} "
                "cannot be compared with it"]
    if run_base != base:
        return [f"the range base {base} is not the run's base_commit {run_base}; the prescribed "
                "range is <the run's base_commit>..<HEAD at dispatch>"]
    return []


def _subject(args: argparse.Namespace, repo: Path) -> tuple[dict, list[str]]:
    """The subject block and the problems found while building it."""
    if args.range_ is not None:
        base, end = _resolve_range(args.range_, repo)
        return {"form": "commit-range", "range": f"{base}..{end}"}, []
    items: list[dict] = []
    problems: list[str] = []
    for given in args.paths:
        try:
            _, rel = cr.resolve_in_repo(repo, os.path.abspath(given))
            st = cr.path_state(repo, rel)
        except cr.PathRefused as exc:
            problems.append(f"{given}: {exc.message}")
            continue
        if st.head is None:
            problems.append(f"{rel}: has no committed content in HEAD")
            continue
        items.append({"path": rel, "sha256": st.head})
    return {"form": "paths", "paths": items}, problems


def build(args: argparse.Namespace) -> tuple[dict, Path, Path, list[str]]:
    """The validated report, its output directory, the repository and the warnings.
    Raises `Refused`."""
    run_path, run, _book, repo = _bind(args.run, args.book, args.prompt)
    subject, problems = _subject(args, repo)
    if problems:
        raise Refused(*problems)
    verdict, findings = _texts(args)
    fields: dict = {"verdict": verdict, "findings": findings}
    if args.reviewer is not None:
        fields["reviewer"] = args.reviewer
    hits = ss.scan_fields(fields, exact=_exact())
    if hits:
        raise Refused(*[f"{field}: matched the secret scan ({shape})" for field, shape in hits])
    doc = {"record_type": RECORD_TYPE, "format_version": "2", "written_at": _stamp()[1],
           "book": {"id": run["book_id"],
                    "content_hash": run["book_content_hash"]},
           "run_id": run["run_id"], "prompt": args.prompt, "reviewer_role": "reviewer",
           "subject": subject, "verdict": verdict, "findings": findings}
    if args.reviewer is not None:
        doc["reviewer"] = args.reviewer
    errors = cr.reviewer_report_errors(doc)
    if errors:
        raise Refused(*[f"the report fails its schema: {e}" for e in errors[:5]])
    problems = cg.subject_problems(repo, subject)
    if problems:
        raise Refused(*problems)
    warnings = []
    if subject["form"] == "commit-range":
        warnings = _base_warnings(run, subject["range"].partition("..")[0], repo)
    return doc, run_path.parent, repo, warnings


def _reviews_dir(run_dir: Path, repo: Path) -> Path:
    reviews = run_dir / "reviews"
    if reviews.is_symlink() or cr.reached_through_symlink(reviews):
        raise Refused("reviews/ is a symlink or sits behind one")
    try:
        reviews.mkdir(exist_ok=True)
    except OSError as exc:
        raise Fault(f"reviews/ cannot be created ({type(exc).__name__})") from None
    if reviews.is_symlink() or cr.reached_through_symlink(reviews):
        raise Refused("reviews/ is a symlink or sits behind one")
    try:
        cr.resolve_in_repo(repo, reviews)
    except cr.PathRefused as exc:
        raise Refused(f"reviews/: {exc.message}") from None
    return reviews


def write(doc: dict, run_dir: Path, repo: Path) -> Path:
    """Write `doc` under `<run_dir>/reviews/`, never replacing a file. Raises `Refused` or `Fault`.

    The create is exclusive (``O_EXCL``), not the content write: a failed write unlinks the
    partial file."""
    reviews = _reviews_dir(run_dir, repo)
    data = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        dfd = os.open(reviews, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise Refused("reviews/ is a symlink or not a directory") from None
        raise Fault(f"reviews/ cannot be opened ({type(exc).__name__})") from None
    try:
        for _ in range(_ATTEMPTS):
            name = f"{doc['run_id']}-p{doc['prompt']}-{_stamp()[0]}-{uuid.uuid4().hex[:8]}.json"
            target = reviews / name
            if cr.reached_through_symlink(target):
                raise Refused(f"{name}: the output path is reached through a symlink")
            try:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644,
                             dir_fd=dfd)
            except FileExistsError:
                continue
            except OSError as exc:
                raise Fault(f"{name}: cannot be created ({type(exc).__name__})") from None
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
            except OSError as exc:
                try:
                    os.unlink(name, dir_fd=dfd)
                except OSError:
                    pass
                raise Fault(f"{name}: cannot be written ({type(exc).__name__})") from None
            return target
    finally:
        os.close(dfd)
    raise Refused("no unused report file name was found; nothing was written")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        doc, run_dir, repo, warnings = build(args)
        target = write(doc, run_dir, repo)
    except Refused as exc:
        # A problem can echo an argument (a --path, a file name); a matching one is withheld.
        print(json.dumps({"status": "refused", "problems": [_safe(p) for p in exc.problems]}))
        return 1
    except Fault as exc:
        print(_safe(f"write-review-report: {exc}"), file=sys.stderr)
        return 2
    try:
        rel = cr.resolve_in_repo(repo, target)[1]
    except cr.PathRefused:
        rel = str(target)
    print(json.dumps({"status": "written", "path": rel, "prompt": doc["prompt"],
                      "run_id": doc["run_id"], "subject": doc["subject"]["form"],
                      "warnings": warnings}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
