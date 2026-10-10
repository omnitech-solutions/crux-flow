#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0,<7"]
# ///
"""run-work-witness.py - the run-work witness writer.

    uv run crux/scripts/run-work-witness.py record <run-RUN-NNN.yaml> --prompt N --path P
    uv run crux/scripts/run-work-witness.py commit <run-RUN-NNN.yaml> --path P [--book B]

A run-work witness records that a run wrote a file it later passes as a council subject: the
repository-relative path and the sha256 of the bytes the run wrote. Witnesses live in
`<run_dir>/run-work-witness.json` (shape: `crux/schemas/run-work-witness.schema.json`), never under
`council/`, and are never council evidence. The council runner's preflight reads them to decide
whether a dirty subject is the run's own work, which the conductor may commit.

`record` appends `{path, sha256, prompt, written_at}` to the witness, creating it bound to the run's
book id, book content hash and run id. Each step that writes a file it will pass as a council
subject runs it right after the write. The witness is validated against its schema and replaced
atomically, under an exclusive `flock` on the run directory held for the whole read-modify-write,
so two concurrent records both land; where `fcntl` is absent `record` exits 2. A symlinked witness,
a path outside the repository, a path at or under the git directory (`git-dir`), a symlinked path,
a path in the crux home or an env file, a path whose text matches the secret scan (`secret-scan`;
the path is never echoed), a path under the run's `council/` directory (`council-path`)
and a prompt the run does not hold are refused. Never record a witness
to repair a council preflight refusal: a subject with no witness taken at its write goes to the
owner. Every run of a book shares the run directory, so a witness bound to
another run of the same book is the last run's: `record` starts this run's witness in its place and
reports the replaced run as `replaced_run_id`. A witness bound to another book is refused.

`commit` refuses a witness bound to another book or run, as `record` does, and re-checks that the
working-tree bytes of P equal the latest witness entry for P. When HEAD and the index already hold
those bytes at P it commits nothing and reports `{"result": "already-committed"}`. When the index
holds a staged version of P that is neither HEAD's nor the witnessed bytes it refuses `mixed`,
because a path-limited commit would replace that staged version. Otherwise it commits exactly P
through the runner-commit contract (`council_commit.commit_owned`) with the message
`crux run work: <book> <run> prompt <n>`. Hooks run, the user's git configuration holds, and every
change outside P stays as it was. Before any of that, `commit` re-checks that P is a run-work
candidate by the council runner's own reading (`council_gate.run_work_candidates`, with the book
bound as `run-council.py` binds it; a book that cannot be bound is exit 2) and refuses `unattributed`
otherwise (pass `--book` when the run layout does not find the book, as the council runner takes
it), and refuses `council-path` for any P under the run's `council/` directory, as `record` does. A timeout
(`"code": "timeout"`) can leave `index.lock` behind, P staged, and outside paths or `refs/stash`
moved. A `hook-or-commit-failed` refusal can also leave outside paths moved, when a hook set the
work aside and failed before its restore. The refusal then carries `moved` and `staged` when they
are not empty. When it carries `moved`, or its code is `timeout`, the owner restores
the set-aside work first (`git stash list`; a pre-commit framework keeps a backup patch under its
cache directory), then removes a stale `index.lock`, then runs recovery.

The writer does not commit the witness file. The council runner's diagnostics commit, which follows
each council record commit, carries `run-work-witness.json`.

Paths are read relative to the working directory.

Exit codes: 0 done (JSON on stdout); 1 refusal, with JSON on stdout naming it (`refused`); 2
environment or capability error, message on stderr. No value from the crux env file is printed.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import stat
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows: `record` refuses, as the council runner does
    fcntl = None  # type: ignore[assignment]

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import yaml  # noqa: F401, E402  (declared; guards the minimal-parser fallback of validate-promptbook)
except ImportError:
    sys.stderr.write("run-work-witness.py requires PyYAML - run via `uv run` (PEP 723 supplies it).\n")
    raise SystemExit(2)

import council_commit  # noqa: E402  (attribute-style calls, so a test shim can replace one)
import council_gate  # noqa: E402
import council_records as cr  # noqa: E402
import secret_scan as ss  # noqa: E402


class Refusal(Exception):
    def __init__(self, payload: dict):
        self.payload = payload
        super().__init__(payload.get("refused", "refused"))


class EnvError(Exception):
    """An environment or capability fault: exit 2, message on stderr."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _load_run(run_arg: str) -> tuple[Path, dict, Path]:
    run_path = Path(run_arg)
    if cr.reached_through_symlink(run_path):
        raise Refusal({"refused": "symlink", "what": "run snapshot"})
    try:
        vp = cr._validator()
        run = vp.load_yaml(run_path.read_bytes().decode("utf-8"))
    except Exception as exc:  # unreadable, unparseable, or no YAML capability
        raise EnvError(f"the run snapshot cannot be read ({type(exc).__name__})") from None
    if not isinstance(run, dict):
        raise EnvError("the run snapshot is not a mapping")
    for key in ("run_id", "book_id", "book_content_hash"):
        if not isinstance(run.get(key), str):
            raise EnvError(f"the run snapshot carries no {key}")
    run_dir = run_path.absolute().parent
    repo = cr.repo_root(run_dir)
    if repo is None:
        raise EnvError("the run snapshot is not inside a git repository")
    return run_dir, run, repo


def _resolve_subject(repo: Path, path_arg: str) -> str:
    """The repository-relative POSIX path of `path_arg`, or a refusal naming its code."""
    cand = Path(path_arg)
    absolute = cand if cand.is_absolute() else Path.cwd() / cand
    code = ss.check_input_path(absolute, repo)
    if code is not None:
        raise Refusal({"refused": code, "path": path_arg})
    try:
        resolved, rel = cr.resolve_in_repo(repo, absolute)
    except cr.PathRefused as exc:
        raise Refusal({"refused": "outside-repo", "path": path_arg, "why": exc.message}) from None
    if council_commit.git_internal_path(rel) or _under_git_dir(repo, Path(resolved)):
        raise Refusal({"refused": "git-dir", "path": path_arg})
    return rel


def _under_git_dir(repo: Path, path: Path) -> bool:
    """True when `path` is the repository's git directory or lies under it, compared by
    directory identity so a case-variant spelling on a case-insensitive volume is caught."""
    gd = cr.absolute_git_dir(repo)
    if gd is None:
        return True
    try:
        gd_stat = os.stat(gd)
    except OSError:
        return True
    for anc in (path, *path.parents):
        try:
            st = os.stat(anc)
        except OSError:
            continue
        if (st.st_dev, st.st_ino) == (gd_stat.st_dev, gd_stat.st_ino):
            return True
    return False


def _read_regular(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise Refusal({"refused": "missing"})
        return fh.read()


def _read_witness(run_dir: Path) -> dict | None:
    try:
        return council_commit.read_witness(run_dir)
    except council_commit.WitnessInvalid as exc:
        raise Refusal({"refused": "witness-invalid", "why": str(exc)}) from None


def _write_atomic(run_dir: Path, data: bytes) -> None:
    """Replace the witness through a temporary sibling created with O_EXCL|O_NOFOLLOW, flushed
    before the rename and with its directory after it."""
    target = run_dir / council_commit.WITNESS_FILE
    tmp = run_dir / f".{council_commit.WITNESS_FILE}.{uuid.uuid4().hex}.tmp"
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if os.path.islink(target):
            raise Refusal({"refused": "witness-invalid", "why": "the witness file is a symlink"})
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    dfd = os.open(run_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


@contextlib.contextmanager
def _locked(run_dir: Path):
    """Hold an exclusive `flock` on the run directory. Fails closed (exit 2) without `fcntl`."""
    if fcntl is None:
        raise EnvError("the witness write needs fcntl, which this platform lacks")
    fd = os.open(run_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # closing the descriptor releases the lock


def _check_binding(doc: dict, run: dict) -> None:
    """Refuse a witness bound to another book, book content or run than the run snapshot's."""
    book = {"id": run["book_id"], "content_hash": run["book_content_hash"]}
    if doc["book"] != book or doc["run_id"] != run["run_id"]:
        raise Refusal({"refused": "witness-binding", "why": "the witness is bound to another book or run"})


def cmd_record(args: argparse.Namespace) -> dict:
    run_dir, run, repo = _load_run(args.run)
    prompts = {p.get("n") for p in run.get("prompts") or [] if isinstance(p, dict)}
    if args.prompt < 1 or args.prompt not in prompts:
        raise Refusal({"refused": "prompt", "prompt": args.prompt})
    rel = _resolve_subject(repo, args.path)
    if ss.scan_text(rel):
        raise Refusal({"refused": "secret-scan"})
    if _under_council(run_dir, repo, rel):
        # `commit` always refuses a council path, so a witness for one would only make the runner
        # offer a repair that cannot be carried out.
        raise Refusal({"refused": "council-path", "path": rel})
    data = _read_regular(repo / rel)
    with _locked(run_dir):
        return _record_locked(run_dir, run, repo, rel, data, args.prompt)


def _record_locked(run_dir: Path, run: dict, repo: Path, rel: str, data: bytes, prompt: int) -> dict:
    book = {"id": run["book_id"], "content_hash": run["book_content_hash"]}
    doc = _read_witness(run_dir)
    replaced = None
    if doc is not None and doc["book"]["id"] == run["book_id"] and doc["run_id"] != run["run_id"]:
        # Every run of a book shares the run directory. The witness another run left is never this
        # run's evidence, so this run starts its own in its place and names the run it replaced.
        replaced, doc = doc["run_id"], None
    if doc is None:
        doc = {"record_type": "run-work-witness", "format_version": "1", "book": book,
               "run_id": run["run_id"], "entries": []}
    else:
        _check_binding(doc, run)
    entry = {"path": rel, "sha256": cr.sha256_bytes(data), "prompt": prompt, "written_at": _now()}
    doc["entries"].append(entry)
    errors = council_commit.witness_errors(doc)
    if errors:
        raise Refusal({"refused": "witness-invalid", "why": errors[0]})
    _write_atomic(run_dir, (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    out = {"recorded": entry, "witness": (run_dir / council_commit.WITNESS_FILE).relative_to(repo).as_posix()}
    if replaced is not None:
        out["replaced_run_id"] = replaced
    return out


def _under_council(run_dir: Path, repo: Path, rel: str) -> bool:
    """True when `rel` lies under the run's `council/` directory (case-folded, so a case-variant
    spelling on a case-insensitive volume is caught)."""
    try:
        council = (run_dir.resolve() / "council").relative_to(repo.resolve()).as_posix()
    except ValueError:
        return False
    return (rel.casefold() + "/").startswith(council.casefold() + "/")


def _candidates(run_arg: str, run_dir: Path, run: dict, repo: Path, book_arg: str | None = None) -> set[str]:
    """The run-work candidates by the council runner's reading, with the book bound as the runner
    binds it: from the run layout, or from `--book` when given. A book that cannot be bound, or one
    reached through a symlink, is an environment fault."""
    run_abs = run_dir / Path(run_arg).name
    book_abs = None
    if book_arg:
        book_abs = Path(book_arg).absolute()
        if cr.reached_through_symlink(book_abs) or cr.reached_through_symlink(Path(book_arg)):
            raise EnvError("the book is reached through a symlink; name it by its physical path")
    try:
        resolved = cr.resolve_book(run_abs, book_abs)
    except cr.RecordError as exc:
        raise EnvError(f"the book cannot be bound to the run: {exc.message}") from None
    return council_gate.run_work_candidates(run, run_abs, resolved.path, repo)


def cmd_commit(args: argparse.Namespace) -> dict:
    run_dir, run, repo = _load_run(args.run)
    rel = _resolve_subject(repo, args.path)
    if _under_council(run_dir, repo, rel):
        raise Refusal({"refused": "council-path", "path": rel})
    if rel not in _candidates(args.run, run_dir, run, repo, args.book):
        raise Refusal({"refused": "unattributed", "path": rel})
    doc = _read_witness(run_dir)
    if doc is not None:
        _check_binding(doc, run)
    entries = [e for e in (doc or {}).get("entries", []) if e["path"] == rel]
    if not entries:
        raise Refusal({"refused": "unwitnessed", "path": rel})
    latest = entries[-1]
    data = _read_regular(repo / rel)
    if cr.sha256_bytes(data) != latest["sha256"]:
        raise Refusal({"refused": "witness-mismatch", "path": rel,
                       "why": "the working-tree bytes differ from the latest witness entry"})
    try:
        state = council_commit.path_state(repo, rel)
    except council_commit.CommitRefused as exc:
        raise Refusal({"refused": "commit", "code": exc.code, "path": rel, "detail": exc.detail}) from None
    if state.head == data and state.index == data and not state.unmerged:
        return {"result": "already-committed", "path": rel, "sha256": latest["sha256"]}
    if state.holds_other_version(data):
        raise Refusal({"refused": "mixed", "path": rel})
    message = f"crux run work: {run['book_id']} {run['run_id']} prompt {latest['prompt']}"
    try:
        sha = council_commit.commit_owned(repo, {rel: data}, message)
    except council_commit.CommitRefused as exc:
        payload = {"refused": "commit", "code": exc.code, "path": rel, "detail": exc.detail}
        if exc.moved:
            payload["moved"] = list(exc.moved)
        if exc.staged:
            payload["staged"] = list(exc.staged)
        raise Refusal(payload) from None
    except ValueError as exc:
        raise Refusal({"refused": "invalid", "path": rel, "why": str(exc)}) from None
    return {"committed": rel, "commit": sha, "sha256": latest["sha256"], "prompt": latest["prompt"]}


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # argparse echoes argv values; scan before printing
        print(f"run-work-witness: {self.prog}: error: {council_commit.scan_output(message)}", file=sys.stderr)
        raise SystemExit(2)


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="run-work-witness.py", description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog="Exit codes: 0 done; 1 refusal (JSON on stdout); "
                                       "2 environment or capability error (stderr).")
    sub = p.add_subparsers(dest="command", required=True, parser_class=_Parser)
    rec = sub.add_parser("record", help="append a witness entry for a file the run just wrote")
    rec.add_argument("run")
    rec.add_argument("--prompt", type=int, required=True)
    rec.add_argument("--path", required=True)
    com = sub.add_parser("commit", help="commit exactly a witnessed path, re-checked against its witness")
    com.add_argument("run")
    com.add_argument("--path", required=True)
    com.add_argument("--book", help="the book file when the run layout does not find it (as run-council.py --book)")
    return p


def _emit(payload: dict) -> None:
    """Print `payload` as JSON. When the text matches a key shape it is withheld by label, and the
    printed object keeps only the fixed `refused` code beside the label."""
    text = json.dumps(payload, sort_keys=True)
    scanned = council_commit.scan_output(text)
    if scanned != text:
        kept = {"refused": payload["refused"]} if "refused" in payload else {}
        text = json.dumps({**kept, "withheld": scanned}, sort_keys=True)
    print(text)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = cmd_record(args) if args.command == "record" else cmd_commit(args)
    except Refusal as exc:
        _emit(exc.payload)
        return 1
    except council_commit.EnvUnreadable as exc:
        print(f"run-work-witness: {exc}", file=sys.stderr)
        return 2
    except (EnvError, OSError) as exc:
        text = str(exc) if isinstance(exc, EnvError) else f"{type(exc).__name__} during the witness write"
        print(f"run-work-witness: {council_commit.scan_output(text)}", file=sys.stderr)
        return 2
    _emit(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
