"""Fixture builders shared by the council-gate tests.

`Env` builds a temporary git repository holding a cycle book, a run snapshot at the
real layout (`docs/promptbooks/{active,runs}/...`), one committed subject file and a
`council/` directory, then builds valid records from the committed fixtures under
`fixtures/council_records/`. Every builder returns a record that passes its schema, so
a test that refuses one changes exactly one thing.

Reads only `crux/` and committed fixtures. Never reads the documentation tree.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
RECORDS = Path(__file__).resolve().parent / "fixtures" / "council_records"
sys.path.insert(0, str(SCRIPTS))

import yaml  # noqa: E402

_spec = importlib.util.spec_from_file_location("_vp_for_gate_support", SCRIPTS / "validate-promptbook.py")
vp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vp)

ROLES = ("openai_top", "anthropic_top", "google_top")
REGISTRY_PATH = SCRIPTS / "crux" / "_config" / "llm_router_config.json"
BOOK_ID = "PB-0999"
RUN_ID = "RUN-001"
SUBJECT = "docs/adrs/ADR-0001-fixture.md"


def scrubbed_env() -> dict[str, str]:
    """`os.environ` without the variables that redirect git to another repository
    (`council_records.GIT_REDIRECT_VARS`). Fixture git calls and CLI subprocesses use it, so an
    exported GIT_DIR never makes a test commit into, or read from, a repository it did not build."""
    import council_records
    return {k: v for k, v in os.environ.items() if k not in council_records.GIT_REDIRECT_VARS}


def isolated_git_config(tmp: Path) -> dict[str, str]:
    """Environment updates that isolate a git child from the developer's machine configuration.

    `GIT_CONFIG_GLOBAL` points at a file under `tmp`, written here, that carries an identity and
    turns commit and tag signing off; `GIT_CONFIG_NOSYSTEM=1` drops the system file. Nothing on the
    machine is read or changed. A test that drives the council runner, whose commits honour the
    user's git configuration, applies these so a developer's `commit.gpgsign`, hooks path or
    fsmonitor never reaches the test repository."""
    tmp = Path(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    cfg = tmp / "isolated.gitconfig"
    cfg.write_text("[user]\n\tname = t\n\temail = t@example.invalid\n"
                   "[commit]\n\tgpgsign = false\n[tag]\n\tgpgsign = false\n"
                   "[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    return {"GIT_CONFIG_GLOBAL": str(cfg), "GIT_CONFIG_NOSYSTEM": "1"}


def registry() -> dict:
    """The live router registry, read as the gate reads it."""
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def seat_values(role: str, reg: dict | None = None) -> dict:
    """What the current registry assigns the seat `role`: key, requested ID, namespace, and the
    served model and provider it accepts. Records built from it stay admissible when the registry
    moves a seat to a newer model."""
    reg = reg or registry()
    key = reg["model_roles"][role]
    entry = reg["models"][key]
    return {"registry_key": key, "requested_model": entry["api_string"],
            "provider_namespace": entry["api_string"].split("/", 1)[0],
            "served_model": entry["accepted_served_models"][0],
            "served_provider": entry["accepted_served_providers"][0]}


def git(cwd: Path, *args: str) -> str:
    env = scrubbed_env()
    env.update({
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    })
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise AssertionError(f"git {args} failed: {r.stderr}")
    return r.stdout


def init_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    git(root, "init", "-q", "-b", "main")
    # Detached auto-gc repacks loose objects after a commit and can delete one mid-copytree.
    git(root, "config", "--local", "gc.auto", "0")
    git(root, "config", "--local", "maintenance.auto", "false")
    return root


def _commit_all_plain(root: Path, msg: str = "c") -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)


def _replay_with(root: Path, rel: str, blob: str | None) -> None:
    """Rewrite every commit from the one that added `rel` through HEAD so that `rel` holds `blob`
    there, or is absent when `blob` is None. The working tree is untouched."""
    added = git(root, "log", "--diff-filter=A", "--format=%H", "--", rel).split()
    if not added:
        return
    has_parent = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "--quiet", added[-1] + "^"],
                                capture_output=True, env=scrubbed_env()).returncode == 0
    chain = git(root, "rev-list", "--reverse", "--ancestry-path",
                added[-1] + "^..HEAD" if has_parent else "HEAD").split()
    parent = git(root, "rev-parse", added[-1] + "^").strip() if has_parent else None
    index = root / ".git" / "replay-index"
    for commit in chain:
        env = scrubbed_env()
        env["GIT_INDEX_FILE"] = str(index)
        env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})

        def run(*args, stdin=None):
            r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=env,
                               input=stdin)
            if r.returncode:
                raise AssertionError(f"git {args} failed: {r.stderr}")
            return r.stdout
        run("read-tree", commit)
        if run("ls-files", "--", rel).strip():
            if blob is None:
                run("update-index", "--force-remove", "--", rel)
            else:
                run("update-index", "--cacheinfo", f"100644,{blob},{rel}")
        tree = run("write-tree").strip()
        meta = run("log", "-1", "--format=%an%n%ae%n%aI%n%cn%n%ce%n%cI", commit).split("\n")
        body = run("log", "-1", "--format=%B", commit)
        env.update({"GIT_AUTHOR_NAME": meta[0], "GIT_AUTHOR_EMAIL": meta[1], "GIT_AUTHOR_DATE": meta[2],
                    "GIT_COMMITTER_NAME": meta[3], "GIT_COMMITTER_EMAIL": meta[4],
                    "GIT_COMMITTER_DATE": meta[5]})
        args = ["commit-tree", tree] + (["-p", parent] if parent else [])
        parent = run(*args, stdin=body).strip()
    index.unlink(missing_ok=True)
    git(root, "update-ref", "HEAD", parent)
    git(root, "reset", "-q", "--mixed", parent)


def commit_all(root: Path, msg: str = "c", reintroduce: tuple = ()) -> None:
    """`commit_all`, except history keeps one introducing commit for each council record.

    A modified committed council record replaces its blob in the commit that introduced it, and
    every later commit is replayed on top. A path in `reintroduce` leaves the earlier commits and
    joins this commit, so a fixture can make a record the newest one. A fixture that builds a
    synthetic record and rewrites it before the close then keeps one introducing commit, as the
    council runner's single commit of a record does. A test of a record modified after its commit
    makes that commit itself, with `Env.commit_records` after an unrelated commit, or with `git`."""
    root = Path(root)
    if subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "--quiet", "HEAD"],
                      capture_output=True, env=scrubbed_env()).returncode:
        return _commit_all_plain(root, msg)
    changed = git(root, "diff", "--name-only", "HEAD").split("\n")
    records = [p for p in changed if "/council/" in p and p.endswith(".json") and not p.endswith(".attempt.json")
               and (root / p).is_file()]
    for rel in records:
        _replay_with(root, rel, git(root, "hash-object", "-w", "--", rel).strip())
    for rel in reintroduce:
        _replay_with(root, rel, None)
    if git(root, "status", "--porcelain").strip():
        _commit_all_plain(root, msg)


def index_blob(root: Path, name: bytes, content: bytes) -> None:
    """Stage `content` at the raw path `name` in the index only, never in the working tree.

    A name with a non-UTF-8 byte can sit in a tree on APFS, which refuses it as a file name."""
    env = scrubbed_env()
    blob = subprocess.run(["git", "-C", str(root), "hash-object", "-w", "--stdin"], input=content,
                          capture_output=True, check=True, env=env).stdout.decode().strip()
    subprocess.run([b"git", b"-C", os.fsencode(root), b"update-index", b"--add", b"--cacheinfo",
                    b"100644," + blob.encode() + b"," + name], capture_output=True, check=True, env=env)


def range_with_a_staged_change(root: Path, name: bytes) -> str:
    """Commit `name` as the only change of a range, then stage a change to it that no review saw.
    Returns the range `<base>..<end>`."""
    base = git(root, "rev-parse", "HEAD").strip()
    index_blob(root, name, b"reviewed v1\n")
    git(root, "commit", "-q", "-m", "range end")
    end = git(root, "rev-parse", "HEAD").strip()
    index_blob(root, name, b"UNREVIEWED v2\n")
    listing = subprocess.run(["git", "-C", str(root), "diff", "--name-only", "-z", base, end],
                             capture_output=True, check=True, env=scrubbed_env()).stdout
    assert listing.split(b"\x00")[:1] == [name], f"the fixture did not commit {name!r}: {listing!r}"
    return f"{base}..{end}"


def ts(i: int, micro: int = 1) -> str:
    return f"2026-10-01T12:{i:02d}:00.{micro:06d}Z"


def fixture(name: str) -> dict:
    return json.loads((RECORDS / name).read_text(encoding="utf-8"))


def make_book(kind: str = "adr", prompt_text: dict | None = None, **extra) -> dict:
    """A 13-prompt cycle book (adr or verify) or a 5-prompt patch book."""
    prompt_text = prompt_text or {}

    def prompt(n, title, tag=None, phase=None):
        p = {"n": n, "title": title, "purpose": "do it", "prompt": prompt_text.get(n, "do the work"),
             "expected_output": "done"}
        if tag:
            p["module_tag"] = tag
        if phase:
            p["phase"] = phase
        return p

    book = {"format_version": "1", "id": BOOK_ID, "title": "Fixture", "tags": ["cycle"],
            "goal": "g", "strategy": "s"}
    if kind == "patch":
        phases = ("verify", "plan", "implement", "review", "summary")
        book.update(cycle_kind="patch", total_prompts=5, blast_radius=["src/x.py"],
                    prompts=[prompt(i + 1, f"{ph} phase", phase=ph) for i, ph in enumerate(phases)])
    else:
        fam = kind
        layout = [(None, 1)] + [(f"{fam}-1", 4), ("dev-1", 4), ("review-1", 3)] + [(None, 1)]
        prompts, n = [], 0
        for tag, count in layout:
            for _ in range(count):
                n += 1
                prompts.append(prompt(n, f"{tag or 'untagged'} #{n}", tag))
        book.update(cycle_kind=kind, total_prompts=n, prompts=prompts,
                    modules={"adrs" if kind == "adr" else "verify": 1, "dev_loops": 1, "review_cycles": 1})
    book.update(extra)
    return book


# The dimension names each formal council kind's question carries.
_QUESTION_DIMENSIONS = {
    "implementation": "Completeness, Correctness, Consistency, Clarity, Security",
    "verify": "Evidence/Reproduction, Root-cause correctness, Approach soundness and minimality, "
              "Consistency, Security",
    "patch": "Evidence/Reproduction, Root-cause correctness, Approach soundness and minimality, "
             "Blast-radius proportion, Security",
}


def select_question(root: Path, doc: dict) -> dict:
    """Give a record carrying a retained formal subject a real question that selects it.

    An implementation close reads the sealed question and requires that it named the bound
    subject, so this writes the question file into the repository at `root` and seals its path
    and SHA256 in `doc`. Call it again after changing that subject's digest. A record with no
    retained subject, or of a kind with no formal question, keeps its template question."""
    kind = doc.get("council_kind")
    retained = [s for s in doc.get("subjects", []) if isinstance(s, dict) and s.get("retained_copy")]
    if not retained or kind not in _QUESTION_DIMENSIONS:
        return doc
    subject = retained[0]
    text = (f"Review the selected Implementation Decision {subject['path']} at SHA256 {subject['sha256']}. "
            f"Assess {_QUESTION_DIMENSIONS[kind]}. Assess architectural conflict of the selected approach.\n")
    data = text.encode()
    digest = hashlib.sha256(data).hexdigest()
    path = Path(root) / "council-questions" / f"question-{digest[:16]}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    doc["question"] = {"path": path.relative_to(root).as_posix(), "sha256": digest}
    return doc


class Env:
    """A temp git repo with a book, a run snapshot and one committed subject file."""

    def __init__(self, root: Path, kind: str = "adr", book: dict | None = None, commit: bool = True,
                 autocommit: bool = False, base: bool = True):
        #: When true, `write` commits each record it writes under `council/` (the gate reads only
        #: committed records). A test of an uncommitted record passes `commit=False` to `write`.
        self.autocommit = autocommit
        #: The run's `base_commit`, or None. With `base=True` (the default, with `commit=True`) the
        #: fixture commit becomes the run's base: the snapshot is rewritten with `base_commit` and
        #: committed again, so the run-start fields are known and a format-1 record is admissible
        #: (the gate refuses one in a run whose base is unknown). A test that builds its own base
        #: history passes `base=False`.
        self.base_commit: str | None = None
        self.root = init_repo(root)
        self.kind = kind
        self.book = book or make_book(kind)
        self.docs = self.root / "docs"
        self.name = f"{BOOK_ID}-fixture"
        self.run_dir = self.docs / "promptbooks" / "runs" / self.name
        self.council = self.run_dir / "council"
        self.council.mkdir(parents=True)
        self.book_path = self.docs / "promptbooks" / "active" / f"{self.name}.yaml"
        self.book_path.parent.mkdir(parents=True)
        self.run_path = self.run_dir / f"run-{RUN_ID}.yaml"
        self.subject = self.root / SUBJECT
        self.subject.parent.mkdir(parents=True)
        self.subject.write_text("# subject v1\n")
        self.write_book_and_run(current=2 if kind != "patch" else 1)
        if commit:
            commit_all(self.root, "fixture")
            if base:
                self.base_commit = git(self.root, "rev-parse", "HEAD").strip()
                self.set_run(2 if kind != "patch" else 1)
                commit_all(self.root, "run base_commit")

    # -- book and run --------------------------------------------------------

    def write_book_and_run(self, current: int = 2, states: dict | None = None) -> None:
        text = yaml.safe_dump({**self.book, "current_run": RUN_ID, "current_prompt": current},
                              sort_keys=False, allow_unicode=True)
        self.book_path.write_text(text)
        self.hash = vp.compute_book_hash(vp.load_yaml(text))
        self.set_run(current, states)

    def set_run(self, current: int, states: dict | None = None) -> None:
        """Rewrite the run snapshot: prompts before `current` done, `current` running, the rest pending."""
        states = states or {}
        prompts = []
        for p in self.book["prompts"]:
            n = p["n"]
            state = states.get(n, "done" if n < current else "running" if n == current else "pending")
            prompts.append({"n": n, "title": p["title"], "state": state,
                            "started": None if state == "pending" else "2026-10-01T00:00:00Z",
                            "completed": "2026-10-01T00:00:00Z" if state in ("done", "blocked", "skipped") else None,
                            "result": "", "artifacts": []})
        self.run = {"format_version": "1", "run_id": RUN_ID, "book_id": BOOK_ID,
                    "book_content_hash": self.hash, "started_at": "2026-10-01T00:00:00Z",
                    "completed_at": None, "status": "in_progress", "current_prompt": current,
                    "prompts": prompts, "notes": "", "pr_draft": "", "summary": ""}
        if getattr(self, "base_commit", None):
            self.run["base_commit"] = self.base_commit
        self.run_path.write_text(yaml.safe_dump(self.run, sort_keys=False))

    def load_run(self) -> dict:
        return yaml.safe_load(self.run_path.read_text())

    # -- records -------------------------------------------------------------

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def write(self, name: str, doc: dict, folder: Path | None = None, commit: bool | None = None) -> Path:
        path = (folder or self.council) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=2) + "\n")
        in_council = path.resolve().parent == self.council.resolve()
        if commit if commit is not None else (self.autocommit and in_council):
            self.commit_records(path)
        return path

    def commit_records(self, *paths: Path, msg: str = "record") -> None:
        """Commit exactly `paths` (default: nothing else is touched). A path whose bytes already
        match HEAD commits nothing."""
        rels = [self.rel(p) for p in paths]
        git(self.root, "add", "--", *rels)
        if subprocess.run(["git", "-C", str(self.root), "diff", "--cached", "--quiet", "--", *rels],
                          env=scrubbed_env()).returncode != 0:
            # Records are ordered by the commit that introduced them. A test that tries several
            # variants of one record rewrites its path while HEAD is still the commit that added
            # it; amending keeps one introducing commit, as a single committed record has.
            added = [r for r in rels if git(self.root, "diff-tree", "--no-commit-id", "--name-status", "-r",
                                            "HEAD", "--", r).startswith("A")]
            if added and len(added) == len(rels):
                git(self.root, "commit", "-q", "--amend", "--no-edit", "--", *rels)
            else:
                git(self.root, "commit", "-q", "-m", msg, "--", *rels)

    def write_sealed(self, name: str, doc: dict, folder: Path | None = None, commit: bool | None = None) -> Path:
        """Seal `doc` and write its canonical bytes, as the council runner does. Commits as `write`."""
        import council_records as cr
        path = (folder or self.council) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(cr.canonical_bytes(cr.sealed(doc)))
        in_council = path.resolve().parent == self.council.resolve()
        if commit if commit is not None else (self.autocommit and in_council):
            self.commit_records(path)
        return path

    def attempt_doc(self, *, module_tag: str | None = "adr-1", prompt: int = 2, round: int = 1,
                    ordinal: int = 1, attempt_id: str | None = None, written_at: str | None = None,
                    subjects: list | None = None, kind: str | None = None) -> dict:
        """A valid, unsealed council attempt record bound to this book and run. `write_sealed` seals
        it; `council_v2_doc` builds the council record that resolves it."""
        import uuid
        doc = fixture("council-attempt.json")
        self.bind(doc)
        doc["attempt_id"] = attempt_id or uuid.uuid4().hex
        doc["written_at"] = written_at or ts(round, 0)
        doc["binding"] = {"prompt": prompt, "module_tag": module_tag}
        doc["council_kind"] = kind or ("patch" if module_tag is None else module_tag.split("-")[0])
        doc["round"] = round
        doc["ordinal"] = ordinal
        doc["subjects"] = subjects if subjects is not None else [{"path": SUBJECT, "sha256": self.sha(SUBJECT)}]
        doc["seal"] = None
        return doc

    def attempt_name(self, *, module_tag: str | None = "adr-1", prompt: int = 2, round: int = 1,
                     ordinal: int = 1) -> str:
        import council_records as cr
        return cr.attempt_file_name(RUN_ID, module_tag, prompt, round, ordinal)

    def council_v2_doc(self, attempt_path: Path, **kw) -> dict:
        """A valid, unsealed format-2 council record that names the attempt at `attempt_path` and
        carries its book, run, binding, round, question, subjects and registry. Keyword arguments
        pass to `council_doc`; a preflight record is built with `preflight_doc` instead."""
        import council_records as cr
        data = attempt_path.read_bytes()
        att = json.loads(data)
        kw.setdefault("module_tag", att["binding"]["module_tag"])
        kw.setdefault("prompt", att["binding"]["prompt"])
        kw.setdefault("round", att["round"])
        kw.setdefault("kind", att["council_kind"])
        doc = self.council_doc(**kw)
        doc["format_version"] = "2"
        doc["question"] = copy.deepcopy(att["question"])
        doc["registry"] = copy.deepcopy(att["registry"])
        doc["subjects"] = [{"path": s["path"], "sha256": s["sha256"], "retained_copy": None}
                           for s in att["subjects"]]
        doc["attempt"] = {"path": self.rel(attempt_path), "sha256": cr.sha256_bytes(data),
                          "attempt_id": att["attempt_id"]}
        doc["seal"] = None
        return doc

    def preflight_doc(self, code: str = "preflight-needs-owner", *, module_tag: str | None = "adr-1",
                      prompt: int = 2, round: int = 1, names: list | None = None,
                      written_at: str | None = None) -> dict:
        """A valid, unsealed format-2 preflight could-not-run record: no attempt, a preflight code."""
        doc = self.council_doc(module_tag=module_tag, prompt=prompt, round=round, outcome="could-not-run",
                               written_at=written_at)
        doc["format_version"] = "2"
        doc["refusal_reason"] = {"code": code, "names": list(names or ["subjects[0].path"])}
        doc["attempt"] = None
        doc["seal"] = None
        return doc

    def void_doc(self, attempt_path: Path, *, module_tag: str | None = "adr-1",
                 prompt: int | None = None) -> dict:
        """A valid owner exception voiding the attempt at `attempt_path`, by path and sha256."""
        import hashlib
        doc = fixture("owner-exception-void-attempt.json")
        self.bind(doc)
        doc["module_tag"] = module_tag
        doc["prompt"] = prompt
        doc["void_attempt"] = {"path": self.rel(attempt_path),
                               "sha256": hashlib.sha256(attempt_path.read_bytes()).hexdigest()}
        return doc

    def bind(self, doc: dict) -> dict:
        doc["book"] = {"id": BOOK_ID, "content_hash": self.hash}
        doc["run_id"] = RUN_ID
        return doc

    def council_doc(self, *, module_tag: str | None = "adr-1", prompt: int = 2, round: int = 1,
                    written_at: str | None = None, decisions=("APPROVE", "APPROVE", "APPROVE"),
                    findings: dict | None = None, errored: tuple = (), kind: str | None = None,
                    outcome: str = "ran", refusal_reason: dict | None = None,
                    subjects: list | None = None) -> dict:
        """A valid council record. `decisions` run in role order; `findings` maps a role to a
        list of ``(suffix, dimension, safety_adjacent, kind)``; `errored` lists roles that errored."""
        if outcome == "could-not-run":
            doc = fixture("council-record-could-not-run.json")
        else:
            doc = fixture("council-record-ran.json")
            reg = registry()
            for seat, decision in zip(doc["seats"], decisions):
                self._seat_from_registry(seat, reg)
                seat["decision"] = decision
                seat["findings"] = []
            for role, items in (findings or {}).items():
                seat = next(s for s in doc["seats"] if s["role"] == role)
                seat["findings"] = [{"id": f"{role}:{suffix}", "dimension": dim, "safety_adjacent": safe,
                                     "kind": fkind, "text": "t"} for suffix, dim, safe, fkind in items]
            for role in errored:
                seat = next(s for s in doc["seats"] if s["role"] == role)
                doc["errored_seats"].append({"role": role, "registry_key": seat["registry_key"],
                                             "requested_model": seat["requested_model"],
                                             "fault_label": "timeout"})
                seat.update(errored=True, decision=None, served_model=None, served_provider=None,
                            generation_id=None, fault_label="timeout", finish_reason=None,
                            confidence=None, reasoning=None, findings=[],
                            attempts=[{"attempt": 1, "replied": False, "served_model": None,
                                       "served_provider": None, "generation_id": None,
                                       "finish_reason": None, "fault_label": "timeout",
                                       "served_model_match": None, "served_provider_match": None}])
                doc["degraded"] = True
        self.bind(doc)
        doc["binding"] = {"prompt": prompt, "module_tag": module_tag}
        doc["council_kind"] = kind or ("patch" if module_tag is None else module_tag.split("-")[0])
        doc["round"] = round
        doc["written_at"] = written_at or ts(round)
        if outcome == "ran" and refusal_reason is not None:
            doc["refusal_reason"] = refusal_reason
        doc["subjects"] = subjects if subjects is not None else [
            {"path": SUBJECT, "sha256": self.sha(SUBJECT), "retained_copy": None}]
        if getattr(self, "root", None) is not None:
            select_question(self.root, doc)
        return doc

    @staticmethod
    def _seat_from_registry(seat: dict, reg: dict) -> None:
        """Set a seat's key, requested and served identity, and its first attempt, from the registry."""
        v = seat_values(seat["role"], reg)
        seat.update(registry_key=v["registry_key"], requested_model=v["requested_model"],
                    provider_namespace=v["provider_namespace"], served_model=v["served_model"],
                    served_provider=v["served_provider"])
        for attempt in seat.get("attempts", []):
            if attempt.get("replied"):
                attempt.update(served_model=v["served_model"], served_provider=v["served_provider"])

    def sha(self, rel: str) -> str:
        import hashlib
        return hashlib.sha256((self.root / rel).read_bytes()).hexdigest()

    def refutation_doc(self, council_path: Path, *, recorder: str = "conductor-1", role: str = "conductor",
                       entries: list | None = None, written_at: str, module_tag: str = "adr-1",
                       subjects: list | None = None, council_doc: dict | None = None) -> dict:
        """A valid refutation record naming `council_path`. `entries` is a list of
        ``(finding_id, check_token, expect)``; the default refutes every blocking finding."""
        import hashlib
        cdoc = council_doc or json.loads(council_path.read_text())
        if entries is None:
            entries = [(f["id"], "exit:0", "exit:0") for s in cdoc["seats"] for f in s["findings"]
                       if f["kind"] != "nit" or f["dimension"] in (None, "Security")
                       or f["safety_adjacent"] is not False] or [("openai_top:F1", "exit:0", "exit:0")]
        out_entries = []
        for fid, token, expect in entries:
            art = self.root / "docs" / "checks" / f"{fid.replace(':', '-')}.txt"
            art.parent.mkdir(parents=True, exist_ok=True)
            art.write_text(f"{token}\n")
            derived = ("refuted" if token == expect else "inconclusive" if token == "verdict:INCONCLUSIVE"
                       else "confirmed")
            out_entries.append({"finding_id": fid, "check": "uv run check", "expect": expect,
                                "result_token": token, "artifact": self.rel(art), "result": derived})
        doc = {"record_type": "refutation-record", "format_version": "1", "written_at": written_at,
               "module_tag": module_tag, "council_record": {
                   "path": self.rel(council_path),
                   "sha256": hashlib.sha256(council_path.read_bytes()).hexdigest()},
               "recorder": recorder, "recorder_role": role,
               "subjects": subjects if subjects is not None else
               [{"path": s["path"], "sha256": s["sha256"]} for s in cdoc["subjects"]],
               "entries": out_entries}
        return self.bind(doc)

    def owner_doc(self, *, kind: str = "adr-round-3-council", round: int = 3,
                  module_tag: str | None = "adr-1", prompt: int | None = None) -> dict:
        doc = fixture("owner-exception.json")
        self.bind(doc)
        doc["module_tag"] = module_tag
        doc["prompt"] = prompt
        doc["place"] = {"kind": kind, "round": round}
        return doc

    def report_doc(self, *, prompt: int, paths: list[str] | None = None, range_: str | None = None,
                   written_at: str | None = None, verdict: str = "APPROVE") -> dict:
        doc = fixture("reviewer-report-paths.json")
        self.bind(doc)
        doc["prompt"] = prompt
        doc["written_at"] = written_at or ts(30)
        doc["verdict"] = verdict
        if range_ is not None:
            doc["subject"] = {"form": "commit-range", "range": range_}
        else:
            doc["subject"] = {"form": "paths", "paths": [
                {"path": p, "sha256": self.sha(p)} for p in (paths or [SUBJECT])]}
        return doc

    def args_for(self, *paths: Path) -> list[str]:
        return [self.rel(p) for p in paths]


def deepcopy(x):
    return copy.deepcopy(x)
