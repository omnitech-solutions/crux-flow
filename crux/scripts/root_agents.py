#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""root_agents.py — init-docs step 9: create, append to, or leave the repo-root AGENTS.md.

Skill prose cannot guarantee what a syscall guarantees, so this helper does the
file work of step 9 and step 9 keeps every sentence the user reads.

  plan      classify the root from its directory entries; write nothing
  apply     do what the classification allows, then print what happened
  rollback  delete the file a run created, only while it is still that file
            and only when the record names `<repo-root>/AGENTS.md`

The rules it carries:

  * Presence is judged from directory entries, never from a path probe, so the
    outcome is the same on a filesystem that folds case and on one that does not.
    Names match through `instruction_migration.is_instruction_name`, which
    lowercases; the two modules classify the same entries.
  * A root with no `AGENTS.md` or `CLAUDE.md` in any letter case gets a new
    `AGENTS.md`: the pointer line, one blank line, the objectives block. The file
    is opened O_EXCL | O_NOFOLLOW, so an entry that appeared after inspection, a
    live symlink or a dangling one is never replaced or followed.
  * An exact `AGENTS.md` that is a regular UTF-8 file gets each missing block
    appended, opened O_NOFOLLOW, after `fstat` confirms a regular file. The
    exception is an `AGENTS.md` carrying an untracked or ignored `CLAUDE.md` the
    migration left in place: the next migration would set a changed `AGENTS.md`
    aside, so nothing is appended and the `CLAUDE.md` is reported.
  * Any other entry named for the instruction files is reported with a remedy
    code from a closed set. Step 9 maps each code to its sentence.
  * The rollback record is created O_EXCL | O_NOFOLLOW at mode 0600 before the
    root is touched, so a path that already exists, a link among them, is refused
    and one record always belongs to one apply run. It says whether that run
    created the file, and it names the file by its absolute path.
  * Rollback deletes the created file only when the record names
    `<repo-root>/AGENTS.md` for the root it is given and `lstat` still shows a
    regular file with the recorded device, inode, modification time and content
    hash. The window between that check and the unlink is documented, not closed:
    this is a local tool run by the repository's owner.

Both blocks come from step 9 of the shipped init-docs SKILL.md, so the create and
the append share one source. A step 9 that no longer holds exactly two
```markdown fences is refused with exit 2 before any byte is written.

Exit codes: 0 done with nothing to report, 1 at least one entry reported,
2 capability error (message on stderr).
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
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "instruction_migration", _HERE / "instruction_migration.py")
im = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("instruction_migration", im)
_spec.loader.exec_module(im)

CapabilityError = im.CapabilityError

TARGET = im.CANONICAL  # "AGENTS.md"
DEFAULT_SKILL = _HERE.parent / "skills" / "init-docs" / "SKILL.md"

#: Every remedy code a report can carry. Step 9 holds one sentence per code.
REMEDY_CODES = frozenset({
    "migrate",
    "migrate-set-aside",
    "track-then-migrate",
    "unlist-then-migrate",
    "track-and-unlist-then-migrate",
    "resolve-family-then-migrate",
    "fix-agents-then-migrate",
    "not-applicable:symlink",
    "not-applicable:not-regular-file",
    "not-applicable:no-git",
    "replace-with-regular-file",
    "make-readable-or-reencode",
    "make-writable",
    "appeared-during-run",
    "resolve-partner-then-migrate",
    "not-applicable:carried",
})


# ------------------------------------------------------------------ classifying


@dataclass(frozen=True)
class Entry:
    name: str
    kind: str                # regular | symlink | directory | other
    text_state: str = "ok"   # ok | unreadable | not-utf8 (an exact regular AGENTS.md only)


@dataclass(frozen=True)
class Report:
    entry: str
    kind: str
    code: str


@dataclass(frozen=True)
class Decision:
    action: str              # create | append | none
    reports: list


def root_role(name: str) -> str | None:
    """The part a root entry plays, or None when it plays none.

    `CLAUDE.local.md` is a personal override: it neither blocks the create nor
    gets reported, so it plays no part here.
    """
    if not im.is_instruction_name(name):
        return None
    low = name.lower()
    if low == "claude.local.md":
        return None
    if low == "claude.md":
        return "claude"
    return "agents-exact" if name == TARGET else "agents-variant"


def _kind(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "regular"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    return "other"


def _text_state(path: Path) -> str:
    """Can this regular file be read as strict UTF-8? Never follows a link."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return "unreadable"
    try:
        data = _read_all(fd)
    except OSError:
        return "unreadable"
    finally:
        os.close(fd)
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "not-utf8"
    return "ok"


def inspect_root(root: Path) -> list[Entry]:
    root = Path(root)
    entries = []
    with os.scandir(root) as it:
        names = sorted(e.name for e in it)
    for name in names:
        path = root / name
        try:
            kind = _kind(os.lstat(path).st_mode)
        except OSError:
            continue
        state = "ok"
        if root_role(name) == "agents-exact" and kind == "regular":
            state = _text_state(path)
        entries.append(Entry(name, kind, state))
    return entries


def _tracked(root: Path):
    try:
        return frozenset(im.git_tracked_files(root))
    except CapabilityError:
        return None


def _legacy_code(e: Entry, others: list, agents_blocked: bool, tracked, denylist,
                 carried: frozenset = frozenset()) -> str:
    """The remedy for one legacy entry, from what the migration plan does.

    Every remedy that names the migration must lose nothing and publish nothing
    when it is followed. The migration never writes over an entry: where a
    CLAUDE.md-family entry wins the root, an AGENTS.md-family entry beside it
    whose bytes differ is set aside under a reported name. It refuses the root,
    changing nothing, when an entry it would read or set aside is a link, is not
    a regular file, or is named by the denylist, and when one family holds two
    spellings. An entry beside such a partner is told to resolve that partner.
    The migration acts only where git tracks an instruction file, so an untracked
    entry with no tracked partner is told to track itself first.
    """
    if e.kind not in ("regular", "symlink"):
        return "not-applicable:not-regular-file"
    if e.kind == "symlink":
        return "not-applicable:symlink"
    if tracked is None:
        return "not-applicable:no-git"
    is_claude = root_role(e.name) == "claude"
    family = [o for o in others if o.name.lower() == e.name.lower() and o.kind == "regular"]
    if len(family) > 1:
        return "resolve-family-then-migrate"
    if agents_blocked:
        return "fix-agents-then-migrate"
    partners = [o for o in others if o is not e and root_role(o.name) is not None
                and (root_role(o.name) == "claude") != is_claude]

    def blocks(o: Entry) -> bool:
        # A linked or listed CLAUDE.md is excluded rather than read, so only a
        # CLAUDE.md-family entry of another kind refuses the root.
        if root_role(o.name) == "claude":
            return o.kind not in ("regular", "symlink") and o.name not in denylist
        return o.kind != "regular" or o.name in denylist

    # Two partners are two spellings of one family, which the migration refuses.
    if any(blocks(o) for o in partners) or len(partners) > 1:
        return "resolve-partner-then-migrate"

    def managed(o: Entry) -> bool:
        return o.kind == "regular" and o.name in tracked and o.name not in denylist

    winners = [o for o in partners if root_role(o.name) == "claude"
               and o.kind == "regular" and o.name not in denylist]
    set_aside = bool(partners) if is_claude else bool(winners)
    # The migration already ran: AGENTS.md holds this winner's result and the
    # migration leaves an untracked or ignored winner where it is, so a rerun
    # changes nothing.
    if e.name in carried:
        return "not-applicable:carried"
    untracked, listed = e.name not in tracked, e.name in denylist
    if untracked and listed:
        return "track-and-unlist-then-migrate"
    if untracked:
        # A tracked partner puts the root in the migration, which then reads this
        # entry where it stands; no git add is needed, and none is advised.
        if set_aside and any(managed(o) for o in partners):
            return "migrate-set-aside"
        return "track-then-migrate"
    if listed:
        return "unlist-then-migrate"
    return "migrate-set-aside" if set_aside else "migrate"


def decide(entries, tracked=None, denylist=frozenset(),
           carried: frozenset = frozenset()) -> Decision:
    """The root-state table and its per-entry composition, over injected entries.

    Pure: it touches no filesystem, so a case-folding volume and a case-sensitive
    one produce the same decision from the same entries. `tracked` is the set of
    git-tracked root names, or None when git could not list them. `carried` names
    the CLAUDE.md-family entries whose content the root AGENTS.md already carries.
    """
    roled = [(e, root_role(e.name)) for e in entries]
    roled = [(e, r) for e, r in roled if r is not None]
    exact = next((e for e, r in roled if r == "agents-exact"), None)
    legacy = sorted((e for e, r in roled if r in ("claude", "agents-variant")),
                    key=lambda e: e.name)
    reports: list[Report] = []
    agents_blocked = exact is not None and (exact.kind != "regular" or exact.text_state != "ok")

    if exact is None:
        action = "none" if legacy else "create"
    elif exact.kind != "regular":
        action = "none"
        reports.append(Report(exact.name, exact.kind, "replace-with-regular-file"))
    elif exact.text_state != "ok":
        action = "none"
        reports.append(Report(exact.name, exact.kind, "make-readable-or-reencode"))
    else:
        action = "append"

    others = [e for e, _ in roled]
    for e in legacy:
        reports.append(Report(e.name, e.kind,
                              _legacy_code(e, others, agents_blocked, tracked, denylist, carried)))
    # A carried winner stays where it is, and the next migration sets aside an
    # AGENTS.md whose bytes differ from the result it computes from that winner.
    # An append would start that loop, so the carried report stands in for it.
    if action == "append" and any(r.code == "not-applicable:carried" for r in reports):
        action = "none"
    return Decision(action, reports)


def _carried(root: Path, denylist) -> frozenset:
    """The root CLAUDE.md-family entry whose content AGENTS.md already carries.

    The root scope is finished when its migration plan has no refusal, names an
    untracked or ignored winner the migration leaves in place, and holds no
    working-tree step. Any
    failure to plan means no entry is carried, so the codes fall back.
    """
    try:
        ctx = im._Ctx(root, im.RealFS(), im.Git(root), set(denylist))
        sp = im.plan_scope(ctx, ".")
    except (im.CapabilityError, im.GitError, OSError):
        return frozenset()
    # Only an untracked or ignored winner: that is what the carried sentence says,
    # and a tracked winner a blocked scope leaves in place has a next step.
    if (sp is None or sp.refusals or sp.winner is None or sp.winner.index is not None
            or not sp.winner_left or any(step.kind != "index" for step in sp.steps)):
        return frozenset()
    return frozenset({sp.winner.name})


def plan_root(root: Path) -> Decision:
    root = Path(root)
    denylist = frozenset(im.load_denylist(root))
    return decide(inspect_root(root), _tracked(root), denylist, _carried(root, denylist))


# --------------------------------------------------------------------- the blocks


@dataclass(frozen=True)
class Blocks:
    pointer: str
    objectives: str


_FENCE = re.compile(r"^```markdown\n(.*?)\n```$", re.DOTALL | re.MULTILINE)


def load_blocks(skill_path: Path, docs_dir: str) -> Blocks:
    """Read the two blocks from step 9 of the skill and substitute the tree name."""
    if not docs_dir or any(c in docs_dir for c in "\r\n`"):
        raise CapabilityError(f"unusable docs directory name: {docs_dir!r}")
    try:
        text = Path(skill_path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CapabilityError(f"cannot read the init-docs skill: {exc}") from exc
    start = text.find("### 9. ")
    if start < 0:
        raise CapabilityError("the skill has no step 9")
    end = text.find("\n### 10.", start)
    step9 = text[start:end if end >= 0 else len(text)]
    fences = _FENCE.findall(step9)
    if len(fences) != 2:
        raise CapabilityError(
            f"step 9 holds {len(fences)} markdown fences, not the 2 blocks it must carry")
    pointer, objectives = fences
    if "\n" in pointer or "${DOCS_DIR}/AGENTS.md" not in pointer:
        raise CapabilityError("step 9's first markdown fence is not the one-line pointer")
    if (not objectives.startswith("Read `${DOCS_DIR}/objectives.md`")
            or "\n\n[^objectives]: " not in objectives):
        raise CapabilityError("step 9's second markdown fence is not the objectives block")
    sub = lambda s: s.replace("${DOCS_DIR}", docs_dir)  # noqa: E731
    return Blocks(sub(pointer), sub(objectives))


# ------------------------------------------------------------------------ writing


def _need_nofollow() -> int:
    flag = getattr(os, "O_NOFOLLOW", 0)
    if not flag:
        raise CapabilityError("this platform has no O_NOFOLLOW, so no write can be made safe")
    return flag


def _read_all(fd: int) -> bytes:
    chunks = []
    while True:
        chunk = os.read(fd, 1 << 16)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def created_bytes(blocks: Blocks) -> bytes:
    return (blocks.pointer + "\n\n" + blocks.objectives + "\n").encode("utf-8")


def create_exclusive(root: Path, blocks: Blocks) -> dict:
    """Create AGENTS.md only where no entry exists at this moment.

    O_EXCL refuses an existing entry of any kind, including a symlink, live or
    dangling. FileExistsError is the refusal; nothing is written or followed.
    """
    nofollow = _need_nofollow()
    path = Path(root).resolve() / TARGET
    data = created_bytes(blocks)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o644)
    st = os.fstat(fd)
    try:
        _write_all(fd, data)
        st = os.fstat(fd)
    except BaseException:
        # A half-written file this call created is removed, and only that file.
        try:
            if os.lstat(path).st_ino == st.st_ino:
                os.unlink(path)
        except OSError:
            pass
        raise
    finally:
        os.close(fd)
    return {"created": True, "path": str(path), "dev": st.st_dev, "ino": st.st_ino,
            "mtime_ns": st.st_mtime_ns, "sha256": hashlib.sha256(data).hexdigest()}


def append_nofollow(root: Path, blocks: Blocks, docs_dir: str) -> dict:
    """Append each block the file lacks, without following a link."""
    nofollow = _need_nofollow()
    path = Path(root) / TARGET
    flags = os.O_RDWR | os.O_APPEND | nofollow | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, getattr(errno, "EMLINK", -1), errno.EISDIR):
            return {"outcome": "refused", "code": "replace-with-regular-file"}
        if exc.errno in (errno.EACCES, errno.EPERM):
            # A file that opens for reading only needs write permission; saying
            # it cannot be read as UTF-8 would be false.
            try:
                os.close(os.open(path, os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0)))
            except OSError:
                return {"outcome": "refused", "code": "make-readable-or-reencode"}
            return {"outcome": "refused", "code": "make-writable"}
        raise
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return {"outcome": "refused", "code": "replace-with-regular-file"}
        try:
            existing = _read_all(fd).decode("utf-8")
        except UnicodeDecodeError:
            return {"outcome": "refused", "code": "make-readable-or-reencode"}
        parts, wrote = [], []
        if f"{docs_dir}/AGENTS.md" not in existing:
            parts.append(blocks.pointer)
            wrote.append("pointer")
        if "objectives.md" not in existing:
            parts.append(blocks.objectives)
            wrote.append("objectives")
        if not parts:
            return {"outcome": "unchanged", "wrote": []}
        if existing == "":
            lead = ""
        else:
            lead = ("" if existing.endswith("\n") else "\n") + "\n"
        try:
            _write_all(fd, (lead + "\n\n".join(parts) + "\n").encode("utf-8"))
        except OSError as exc:
            raise CapabilityError(
                f"appending to {TARGET} failed after the write began: {exc}; the file "
                "may end in a partial block, so restore its recorded prior content"
            ) from exc
        return {"outcome": "appended", "wrote": wrote}
    finally:
        os.close(fd)


_RECORD_FIELDS = {"path": str, "dev": int, "ino": int, "mtime_ns": int, "sha256": str}


def _check_record(record, root: Path) -> None:
    """Refuse a record that is not one this helper wrote for this root."""
    if not isinstance(record, dict) or not isinstance(record.get("created"), bool):
        raise CapabilityError("unusable rollback record: no created flag")
    if not record["created"]:
        return
    for key, kind in _RECORD_FIELDS.items():
        if type(record.get(key)) is not kind:
            raise CapabilityError(f"unusable rollback record: no {key}")
    expected = str(Path(root).resolve() / TARGET)
    if record["path"] != expected:
        raise CapabilityError(
            f"the rollback record names {record['path']}, not {expected}; "
            "it belongs to another root or another run, so nothing was deleted")


def _identity(path: Path, record: dict) -> str | None:
    """None while `path` is still the recorded file, else why it is not."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "absent"
    if not stat.S_ISREG(st.st_mode):
        return "not-regular"
    if (st.st_dev, st.st_ino) != (record["dev"], record["ino"]):
        return "replaced"
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0))
        try:
            digest = hashlib.sha256(_read_all(fd)).hexdigest()
        finally:
            os.close(fd)
    except OSError:
        return "unreadable"
    if digest != record["sha256"]:
        return "edited"
    if st.st_mtime_ns != record["mtime_ns"]:
        return "replaced"
    return None


def _matches_identity(path: Path, record: dict) -> bool:
    return _identity(path, record) is None


def rollback_created(record: dict, root: Path) -> dict:
    """Delete the created file only while it is still the file this run created,
    and only when the record names this root's AGENTS.md."""
    _check_record(record, root)
    if not record["created"]:
        return {"outcome": "nothing-created"}
    path = Path(record["path"])
    why = _identity(path, record)
    if why == "absent":
        return {"outcome": "absent"}
    if why is not None:
        return {"outcome": "kept", "reason": why}
    os.unlink(path)
    return {"outcome": "deleted"}


def _open_record(path: Path) -> int:
    """Create the record file, refusing any entry already at the path.

    O_EXCL refuses an existing entry of any kind, and O_NOFOLLOW a link, so a
    record left by an earlier run, or planted, is never reused or written through.
    """
    try:
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _need_nofollow(), 0o600)
    except FileExistsError as exc:
        raise CapabilityError(
            f"the rollback record {path} already exists; name a new path in a "
            "fresh private directory, so the record belongs to this run only") from exc
    except OSError as exc:
        raise CapabilityError(f"cannot create the rollback record {path}: {exc}") from exc


def _write_record(fd: int, record: dict) -> None:
    _write_all(fd, json.dumps(record, sort_keys=True).encode("utf-8"))


# --------------------------------------------------------------------------- CLI


def _report_rows(reports) -> list[dict]:
    return [{"entry": r.entry, "kind": r.kind, "code": r.code} for r in reports]


def _ok(result: dict) -> int:
    return 0 if not result["reports"] and result["outcome"] in (
        "planned-create", "planned-append", "created", "appended", "unchanged") else 1


def _cmd_plan(args) -> tuple[int, dict]:
    d = plan_root(args.repo_root)
    result = {"action": d.action, "outcome": f"planned-{d.action}" if d.action != "none"
              else "none", "reports": _report_rows(d.reports)}
    return _ok(result), result


def _cmd_apply(args) -> tuple[int, dict]:
    # The record exists before the root is touched, or the run stops here.
    record_fd = _open_record(args.record) if args.record else None
    try:
        return _apply(args, record_fd)
    finally:
        if record_fd is not None:
            os.close(record_fd)


def _apply(args, record_fd) -> tuple[int, dict]:
    root = args.repo_root
    d = plan_root(root)
    reports = list(d.reports)
    result = {"action": d.action, "outcome": "none", "wrote": [], "reports": []}
    created = None
    if d.action == "create":
        blocks = load_blocks(args.skill, args.docs_dir)
        try:
            created = create_exclusive(root, blocks)
        except FileExistsError:
            try:
                kind = _kind(os.lstat(root / TARGET).st_mode)
            except OSError:
                kind = "other"
            result["outcome"] = "refused"
            reports.append(Report(TARGET, kind, "appeared-during-run"))
        else:
            result.update(outcome="created", wrote=["pointer", "objectives"], record=created)
    if record_fd is not None:
        try:
            _write_record(record_fd, created or {"created": False})
        except OSError as exc:
            if created is None:
                raise CapabilityError(
                    f"could not write the rollback record {args.record}: {exc}") from exc
            # A created file with no record can never be rolled back, and exit 2
            # tells step 9 that nothing reached the root. Undo the create through
            # the same guard the rollback verb uses.
            undone = rollback_created(created, root)["outcome"]
            raise CapabilityError(
                f"could not write the rollback record {args.record}: {exc}; "
                f"the created {TARGET} was {undone}") from exc
    if d.action == "append":
        blocks = load_blocks(args.skill, args.docs_dir)
        outcome = append_nofollow(root, blocks, args.docs_dir)
        result["outcome"] = outcome["outcome"]
        result["wrote"] = outcome.get("wrote", [])
        if outcome["outcome"] == "refused":
            # Name the entry the append found, not the one inspection saw: a link or
            # a directory can replace the file in between.
            try:
                kind = _kind(os.lstat(root / TARGET).st_mode)
            except OSError:
                kind = "other"
            reports.insert(0, Report(TARGET, kind, outcome["code"]))
    result["reports"] = _report_rows(reports)
    return _ok(result), result


def _cmd_rollback(args) -> tuple[int, dict]:
    record_path = Path(args.record)
    if not record_path.exists() and not record_path.is_symlink():
        return 0, {"outcome": "nothing-recorded"}
    try:
        fd = os.open(record_path, os.O_RDONLY | _need_nofollow() | getattr(os, "O_NONBLOCK", 0))
        try:
            record = json.loads(_read_all(fd).decode("utf-8"))
        finally:
            os.close(fd)
    except (OSError, ValueError) as exc:
        raise CapabilityError(f"unusable rollback record: {exc}") from exc
    result = rollback_created(record, args.repo_root)
    return (1 if result["outcome"] == "kept" else 0), result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="init-docs step 9: the repo-root AGENTS.md.")
    sub = parser.add_subparsers(dest="verb", required=True)
    for verb in ("plan", "apply"):
        p = sub.add_parser(verb)
        p.add_argument("--repo-root", type=Path, required=True)
        p.add_argument("--docs-dir", required=True)
        p.add_argument("--skill", type=Path, default=DEFAULT_SKILL)
        if verb == "apply":
            p.add_argument("--record", type=Path, default=None)
    p = sub.add_parser("rollback")
    p.add_argument("--repo-root", type=Path, required=True)
    p.add_argument("--record", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if not args.repo_root.is_dir():
            raise CapabilityError(f"{args.repo_root} is not a directory")
        code, result = {"plan": _cmd_plan, "apply": _cmd_apply,
                        "rollback": _cmd_rollback}[args.verb](args)
    except (CapabilityError, OSError) as exc:
        print(f"root_agents: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
