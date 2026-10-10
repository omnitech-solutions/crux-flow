"""Issue 3: advance-run.py advances a .yaml run snapshot and preserves the
run-level trailing fields (notes / pr_draft / summary) across every advance.

Imports the vendored `advance-run.py` by path (it's a hyphenated script, so
importlib rather than a normal import). Runs under the uv lane (PyYAML).
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS = REPO_ROOT / "crux" / "scripts"
ADVANCE = SCRIPTS / "advance-run.py"
VALIDATE = SCRIPTS / "validate-promptbook.py"
PROGRESS = SCRIPTS / "visualize-run-progress.py"
CHECK_INDEX = SCRIPTS / "check-promptbook-index.py"
FIXTURES = REPO_ROOT / "crux" / "scripts" / "tests" / "fixtures"
HAND_FORMATTED = FIXTURES / "run-hand-formatted.yaml"

sys.path.insert(0, str(SCRIPTS))
from _yaml_min import load_yaml  # noqa: E402  (sys.path insert before import)

try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False

if HAVE_YAML and ADVANCE.is_file():
    _spec = importlib.util.spec_from_file_location("advance_run", ADVANCE)
    _ar = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_ar)
    _vspec = importlib.util.spec_from_file_location("_vp_for_advance", VALIDATE)
    _vp = importlib.util.module_from_spec(_vspec)
    _vspec.loader.exec_module(_vp)
    HAVE = True
else:
    HAVE = False


def _cli(args):
    """Invoke advance-run.py as a subprocess (the real CLI surface)."""
    return subprocess.run([sys.executable, str(ADVANCE), *args],
                          capture_output=True, text=True)


def _scrubbed_environ() -> dict[str, str]:
    """`os.environ` without the variables that redirect git to another repository, so an
    exported GIT_DIR never makes a fixture commit land in a repository the test did not build."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from council_records import GIT_REDIRECT_VARS
    return {k: v for k, v in os.environ.items() if k not in GIT_REDIRECT_VARS}


def _git(cwd: Path, *args: str) -> None:
    """Run git with the ambient config and repository redirects neutralized, so a result never
    depends on the developer's ~/.gitconfig or an exported GIT_DIR."""
    env = _scrubbed_environ()
    env.update({
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    })
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True, env=env)


def _yaml_text(doc) -> str:
    """Serialize a test document. A fixture builder only; the script under test
    never re-serializes a whole snapshot."""
    import yaml
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


# `--outcome` resolves the run's book and verifies `book_content_hash` before it
# writes, so every fixture run names a book that hashes to its own value. The
# plan-bearing keys are `id`, `title`, `goal`, `prompts` and a few more; the
# pointer keys (`current_run`, `current_prompt`) are outside the hash, so one bare
# book text serves every test that only needs a resolvable book.
BARE_BOOK_TEXT = "id: PB-9001\ncurrent_run: RUN-001\ncurrent_prompt: 1\n"


def _book_hash(book_text: str) -> str:
    return _vp.compute_book_hash(load_yaml(book_text))


def _rebind(run_text: str, book_text: str) -> str:
    """`run_text` with its `book_content_hash` line naming `book_text`'s hash."""
    import re
    out, n = re.subn(r"(?m)^book_content_hash: [^\r\n]*",
                     f"book_content_hash: '{_book_hash(book_text)}'", run_text)
    assert n == 1, "the fixture run must carry exactly one book_content_hash line"
    return out


def _layout_run(root: Path, run_text: str, book_text: str = BARE_BOOK_TEXT,
                name: str = "PB-9001-x") -> tuple[Path, Path]:
    """A run snapshot and its book at the real layout, so no `--book` is needed.
    Returns ``(run_path, book_path)``; the run's hash is bound to the book."""
    runs = root / "promptbooks" / "runs" / name
    active = root / "promptbooks" / "active"
    runs.mkdir(parents=True, exist_ok=True)
    active.mkdir(parents=True, exist_ok=True)
    book = active / f"{name}.yaml"
    book.write_bytes(book_text.encode("utf-8"))
    run = runs / "run-RUN-001.yaml"
    run.write_bytes(_rebind(run_text, book_text).encode("utf-8"))
    return run, book


def _write_and_reload(doc: dict, *args: str) -> dict:
    """Write `doc`, run the real `main()` over it with `args`, and reload the file
    it wrote. The reload is what the trailing-field hazard is about."""
    import yaml
    with tempfile.TemporaryDirectory() as td:
        path, _ = _layout_run(Path(td), _yaml_text(doc))
        with contextlib.redirect_stdout(io.StringIO()):
            _ar.main([str(path), *args])
        return yaml.safe_load(path.read_text())


def _run_doc(n_prompts=3):
    return {
        "format_version": "1",
        "run_id": "RUN-001",
        "book_id": "PB-9001",
        "book_content_hash": _book_hash(BARE_BOOK_TEXT),
        "started_at": "2026-07-22T00:00:00Z",
        "completed_at": None,
        "status": "in_progress",
        "current_prompt": 1,
        "prompts": [
            {"n": i, "title": f"P{i}", "state": ("running" if i == 1 else "pending"),
             "started": None, "completed": None, "result": "", "artifacts": []}
            for i in range(1, n_prompts + 1)
        ],
        "notes": "IMPORTANT NOTES", "pr_draft": "PR DRAFT BODY", "summary": "",
    }


@unittest.skipUnless(HAVE, "advance-run.py or PyYAML unavailable")
class AdvanceRunTests(unittest.TestCase):
    def test_advance_moves_pointer_and_records(self):
        r = _ar.advance(_run_doc(), "done", "did the thing", ["docs/x.md"])
        p1 = r["prompts"][0]
        self.assertEqual(p1["state"], "done")
        self.assertEqual(p1["result"], "did the thing")
        self.assertEqual(p1["artifacts"], ["docs/x.md"])
        self.assertIsNotNone(p1["completed"])
        self.assertEqual(r["current_prompt"], 2)
        self.assertEqual(r["prompts"][1]["state"], "running")

    def test_trailing_fields_preserved(self):
        # THE Issue-3 hazard: notes / pr_draft / summary must survive.
        r = _ar.advance(_run_doc(), "done", "", [])
        self.assertEqual(r["notes"], "IMPORTANT NOTES")
        self.assertEqual(r["pr_draft"], "PR DRAFT BODY")
        self.assertIn("summary", r)

    def test_dump_reload_preserves_trailing_fields(self):
        # The REAL Issue-3 hazard is the write dropping keys — exercise
        # write -> reparse, not just the in-memory mutation.
        reloaded = _write_and_reload(_run_doc(), "--outcome", "done", "--result", "x")
        self.assertEqual(reloaded["notes"], "IMPORTANT NOTES")
        self.assertEqual(reloaded["pr_draft"], "PR DRAFT BODY")
        self.assertIn("summary", reloaded)

    def test_dump_reload_defaults_absent_trailing_fields(self):
        bare = _run_doc()
        for k in ("notes", "pr_draft", "summary"):
            bare.pop(k, None)
        reloaded = _write_and_reload(bare, "--outcome", "done")
        self.assertEqual(reloaded["notes"], "")
        self.assertEqual(reloaded["pr_draft"], "")
        self.assertEqual(reloaded["summary"], "")

    def test_last_prompt_completes_run(self):
        doc = _run_doc(1)
        r = _ar.advance(doc, "done", "", [])
        self.assertEqual(r["status"], "completed")
        self.assertIsNone(r["current_prompt"])
        self.assertIsNotNone(r["completed_at"])

    def test_blocked_writes_no_per_prompt_flag(self):
        # ADR-0077 clause 5(b) deleted `blocked_confirmed`. `blocked` now means
        # blocked, full stop; nothing extra is written on the element, and
        # run.schema.json (additionalProperties: false) would reject it if it were.
        r = _ar.advance(_run_doc(), "blocked", "stuck", [])
        self.assertEqual(r["prompts"][0]["state"], "blocked")
        self.assertNotIn("blocked_confirmed", r["prompts"][0])

    def test_blocked_confirmed_flag_is_gone_from_the_cli(self):
        proc = _cli(["--help"])
        self.assertNotIn("--blocked-confirmed", proc.stdout)

    def test_malformed_snapshot_fails_cleanly(self):
        # A .yaml with format_version but no prompts list -> clean _fail (SystemExit 1),
        # not an uncaught KeyError.
        with self.assertRaises(SystemExit) as cm:
            _ar.advance({"format_version": "1", "current_prompt": 1}, "done", "", [])
        self.assertEqual(cm.exception.code, 1)

    def test_prior_elements_untouched(self):
        doc = _run_doc(3)
        _ar.advance(doc, "done", "one", [])          # p1 -> done, p2 running
        r = _ar.advance(doc, "skipped", "two", [])   # p2 -> skipped, p3 running
        self.assertEqual(r["prompts"][0]["state"], "done")     # p1 unchanged
        self.assertEqual(r["prompts"][0]["result"], "one")
        self.assertEqual(r["prompts"][1]["state"], "skipped")
        self.assertEqual(r["current_prompt"], 3)


@unittest.skipUnless(HAVE, "advance-run.py or PyYAML unavailable")
class AbandonRunTests(unittest.TestCase):
    """ADR-0077 clause 5(b): abandonment is a RUN-level act. `abandonment.kind:
    deliberate` is the archive-eligibility signal that replaced the per-prompt
    `blocked_confirmed` flag."""

    def test_abandon_sets_run_level_record(self):
        r = _ar.abandon(_run_doc(), "the approach was wrong")
        self.assertEqual(r["status"], "abandoned")
        self.assertIsNone(r["current_prompt"])
        self.assertIsNotNone(r["completed_at"])
        self.assertEqual(r["abandonment"]["kind"], "deliberate")
        self.assertEqual(r["abandonment"]["reason"], "the approach was wrong")
        self.assertTrue(r["abandonment"]["at"])

    def test_abandon_leaves_prompt_states_untouched(self):
        # The non-terminal prompt left behind IS the evidence the run was
        # abandoned mid-flight; rewriting it would erase that.
        doc = _run_doc(3)
        r = _ar.abandon(doc, "stopping here")
        self.assertEqual(r["prompts"][0]["state"], "running")
        self.assertEqual(r["prompts"][1]["state"], "pending")
        self.assertEqual(r["prompts"][2]["state"], "pending")

    def test_abandon_preserves_trailing_fields_through_a_dump_reload(self):
        reloaded = _write_and_reload(_run_doc(), "--abandon", "--reason", "stopping")
        self.assertEqual(reloaded["notes"], "IMPORTANT NOTES")
        self.assertEqual(reloaded["pr_draft"], "PR DRAFT BODY")
        self.assertEqual(reloaded["abandonment"]["kind"], "deliberate")

    def test_abandon_requires_a_reason(self):
        for reason in ("", "   "):
            with self.assertRaises(SystemExit) as cm:
                _ar.abandon(_run_doc(), reason)
            self.assertEqual(cm.exception.code, 1)

    def test_abandon_refuses_to_retrofit_onto_a_terminal_run(self):
        for status in ("completed", "abandoned"):
            doc = _run_doc()
            doc["status"] = status
            with self.assertRaises(SystemExit) as cm:
                _ar.abandon(doc, "too late")
            self.assertEqual(cm.exception.code, 1)

    def test_abandon_refuses_when_a_record_already_exists(self):
        doc = _run_doc()
        doc["abandonment"] = {"kind": "superseded", "at": "2026-01-01T00:00:00Z", "reason": "x"}
        with self.assertRaises(SystemExit) as cm:
            _ar.abandon(doc, "again")
        self.assertEqual(cm.exception.code, 1)

    def test_abandoned_run_validates_against_the_run_schema(self):
        doc = _write_and_reload(_run_doc(), "--abandon", "--reason", "stopping")
        errors: list[dict] = []
        _vp.validate(doc, _vp.load_schema(_vp.RUN_SCHEMA), "#", "#", errors, "<t>")
        self.assertEqual(errors, [])


@unittest.skipUnless(HAVE, "advance-run.py or PyYAML unavailable")
class AbandonCliTests(unittest.TestCase):
    """The CLI's mode gate, and the book-pointer rule that keeps a terminal run
    reachable: only a book's CURRENT run can authorize its archive (ADR-0077 clause
    5(b)), so `current_run` survives BOTH a completion and an abandonment — only
    `current_prompt` is nulled. `current_run` is nulled by exactly one writer,
    `archive-promptbook`, at archival."""

    def _write_run(self, tmp: Path) -> Path:
        path = tmp / "run-RUN-001.yaml"
        path.write_text(_yaml_text(_run_doc(3)))
        return path

    def test_abandon_and_outcome_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as td:
            run = self._write_run(Path(td))
            proc = _cli([str(run), "--abandon", "--reason", "x", "--outcome", "done"])
            self.assertEqual(proc.returncode, 1)
            self.assertIn("mutually exclusive", proc.stdout)

    def test_neither_mode_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            run = self._write_run(Path(td))
            proc = _cli([str(run)])
            self.assertEqual(proc.returncode, 1)
            self.assertIn("required", proc.stdout)

    def test_markdown_run_refuses_both_modes_before_any_write_with_recovery_route(self):
        import json
        for mode in (["--outcome", "done"], ["--abandon", "--reason", "stopping"]):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as td:
                tmp = Path(td)
                run = tmp / "run-RUN-001.md"
                run_bytes = b"---\nstatus: in_progress\n---\n## Prompt 1\n"
                run.write_bytes(run_bytes)
                book = tmp / "PB-9001-x.yaml"
                book_bytes = b"id: PB-9001\ncurrent_run: RUN-001\ncurrent_prompt: 1\n"
                book.write_bytes(book_bytes)
                proc = _cli([str(run), *mode, "--book", str(book)])
                self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)
                error = json.loads(proc.stdout)["error"]
                self.assertIn(str(run), error)
                self.assertIn("v3.23.2", error)
                self.assertIn("finish", error)
                self.assertIn("non-retryable", error)
                self.assertNotIn("finish or abandon", error)
                self.assertEqual(run.read_bytes(), run_bytes)
                self.assertEqual(book.read_bytes(), book_bytes)

    def test_markdown_book_refuses_before_yaml_run_write_with_recovery_route(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = self._write_run(tmp)
            run_bytes = run.read_bytes()
            book = tmp / "PB-9001-x.md"
            book_bytes = b"---\nid: PB-9001\ncurrent_run: RUN-001\n---\n"
            book.write_bytes(book_bytes)
            proc = _cli([str(run), "--outcome", "done", "--book", str(book)])
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            error = json.loads(proc.stdout)["error"]
            self.assertIn(str(book), error)
            self.assertIn("v3.23.2", error)
            self.assertEqual(run.read_bytes(), run_bytes)
            self.assertEqual(book.read_bytes(), book_bytes)

    def test_stranded_markdown_remains_readable_while_yaml_run_advances(self):
        """A stranded live Markdown record must not freeze unrelated YAML work."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: bionic\n')
            docs = root / "bionic"
            books = docs / "promptbooks"
            active = books / "active"
            old_runs = books / "runs" / "PB-9002-stranded"
            new_runs = books / "runs" / "PB-9001-new"
            for directory in (active, old_runs, new_runs):
                directory.mkdir(parents=True)
            (docs / "manifest.yml").write_text(
                'schema_version: "5"\nconcerns_enabled: [promptbooks]\n')
            old_book = active / "PB-9002-stranded.md"
            old_book.write_text(
                "---\nid: PB-9002\ntitle: Stranded\nstatus: active\n"
                "current_run: RUN-001\ncurrent_prompt: 1\n---\n"
                "## Prompts\n### Prompt 1 — unfinished\n")
            old_run = old_runs / "run-RUN-001.md"
            old_run.write_text(
                "---\nrun_id: RUN-001\nbook_id: PB-9002\n"
                "status: in_progress\ncurrent_prompt: 1\n---\n"
                "## Prompt 1 — unfinished\n- **State:** running\n")
            old_book_bytes, old_run_bytes = old_book.read_bytes(), old_run.read_bytes()

            new_book = active / "PB-9001-new.yaml"
            # A cycle book grandfathered at run start has no gate prompt, so this advance is
            # the unclassified path the test is about: the Markdown strand, not a gate. The
            # flag lies outside the content hash, so it binds only through the book at the
            # run's base_commit; the repository and the commit pin it.
            new_book.write_text((FIXTURES / "promptbook-valid.yaml").read_text().replace(
                "cycle_kind: adr\n", "cycle_kind: adr\ncycle_grandfathered: true\ngrandfather_reason: fixture\n", 1))
            git_env = {**_scrubbed_environ(), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
            for cmd in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "start"]):
                subprocess.run(["git", "-C", str(root), *cmd], check=True, env=git_env, capture_output=True)
            base = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, env=git_env,
                                  capture_output=True, text=True).stdout.strip()
            new_run = new_runs / "run-RUN-001.yaml"
            run_text = _rebind((FIXTURES / "run-valid.yaml").read_text(), new_book.read_text())
            run_text = run_text.replace("current_prompt: 2\n", f"current_prompt: 2\nbase_commit: '{base}'\n", 1)
            new_run.write_bytes(run_text.encode("utf-8"))
            (books / "index.md").write_text(
                "# Promptbooks\n\n_Last updated: 2026-09-27_\n\n"
                "## Active (2)\n\n"
                "| id | title | status | current_run | progress | tags | created_at |\n"
                "|---|---|---|---|---|---|---|\n"
                "| [[promptbooks/PB-9001-new]] | New | active | RUN-001 | 1/13 | x | 2026-09-27 |\n"
                "| [[promptbooks/PB-9002-stranded]] | Old | active | RUN-001 | 0/1 | x | 2026-09-27 |\n"
                "## Recent runs (last 20)\n\n## Archived (0)\n")
            (docs / "index.md").write_text("# Tree\n\n## Promptbooks (2 active, 0 archived)\n")

            refusal = _cli([str(old_run), "--outcome", "done", "--book", str(old_book)])
            self.assertEqual(refusal.returncode, 1, refusal.stdout + refusal.stderr)
            self.assertIn("non-retryable", refusal.stdout)
            advance = _cli([str(new_run), "--outcome", "done", "--book", str(new_book)])
            self.assertEqual(advance.returncode, 0, advance.stdout + advance.stderr)
            for kind, path in (("promptbook", new_book), ("run", new_run)):
                validation = subprocess.run(
                    [sys.executable, str(VALIDATE), "--kind", kind, str(path)],
                    cwd=root, capture_output=True, text=True)
                self.assertEqual(validation.returncode, 0, validation.stdout + validation.stderr)
            for path in (old_run, new_run):
                status = subprocess.run(
                    [sys.executable, str(PROGRESS), str(path), "--no-color"],
                    cwd=root, capture_output=True, text=True)
                self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
            index = subprocess.run(
                [sys.executable, str(CHECK_INDEX), "--root", str(root)],
                capture_output=True, text=True)
            self.assertEqual(index.returncode, 0, index.stdout + index.stderr)
            self.assertEqual(old_book.read_bytes(), old_book_bytes)
            self.assertEqual(old_run.read_bytes(), old_run_bytes)

    def test_abandon_keeps_the_books_current_run_pointer(self):
        import yaml
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = self._write_run(tmp)
            book = tmp / "PB-9001-x.yaml"
            book.write_text("id: PB-9001\ncurrent_run: RUN-001\ncurrent_prompt: 1\n")
            proc = _cli([str(run), "--abandon", "--reason", "stopping", "--book", str(book)])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            text = book.read_text()
            self.assertIn("current_run: RUN-001", text)
            self.assertIn("current_prompt: null", text)
            self.assertEqual(yaml.safe_load(run.read_text())["abandonment"]["kind"], "deliberate")

    def test_completed_run_keeps_the_books_current_run_pointer(self):
        # Contract (a): a completing advance nulls ONLY current_prompt. current_run
        # stays pointed at the run — the same rule that already holds for abandon —
        # and is nulled by exactly one writer, archive-promptbook, at archival.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            path = tmp / "run-RUN-001.yaml"
            path.write_text(_yaml_text(_run_doc(1)))
            book = tmp / "PB-9001-x.yaml"
            book.write_text("id: PB-9001\ncurrent_run: RUN-001\ncurrent_prompt: 1\n")
            proc = _cli([str(path), "--outcome", "done", "--book", str(book)])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            text = book.read_text()
            self.assertIn("current_run: RUN-001", text)
            self.assertIn("current_prompt: null", text)


@unittest.skipUnless(HAVE, "advance-run.py or PyYAML unavailable")
class BookRewriteValidationBackstopTests(unittest.TestCase):
    """F2: the --book pointer writer mutates raw text via re.sub, so it needs its own
    validation backstop — an exactly-one-match assertion before writing, and a
    post-write re-parse. A duplicate `current_prompt:` key must fail LOUDLY rather
    than be rewritten twice or silently not at all; a lookalike key must not be
    matched by the anchored pattern; and a book that is not parseable YAML must get
    the documented `{"error": ...}` exit-1 contract rather than a traceback.

    Ordering is part of the contract: every check that CAN run before the run
    snapshot is written does, so a refusal never advances the run. Two checks are
    inherently post-write — they re-read what the rewrite produced — and each has
    its own refusal test below, because a guard no test enters is a guard that can
    regress unnoticed."""

    def _write_run(self, tmp: Path) -> Path:
        path = tmp / "run-RUN-001.yaml"
        path.write_text(_yaml_text(_run_doc(1)))
        return path

    def test_duplicate_current_prompt_key_refuses_the_rewrite(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = self._write_run(tmp)
            before = run.read_text()
            book = tmp / "PB-9001-x.yaml"
            book_text = (
                "id: PB-9001\n"
                "current_run: RUN-001\n"
                "current_prompt: 1\n"
                "current_prompt: 1\n"
            )
            book.write_text(book_text)
            proc = _cli([str(run), "--outcome", "done", "--book", str(book)])
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("2 lines matching", proc.stdout)
            self.assertIn("expected exactly 1", proc.stdout)
            # The book is untouched — no double-rewrite, no silent no-op.
            self.assertEqual(book.read_text(), book_text)
            # And NEITHER is the run. The --book validation runs BEFORE the run
            # snapshot is written, so a refusal leaves the run exactly as it was.
            # Were it the other way round, this refusal would strand the run one
            # prompt ahead of the book, and re-running the same command would mark
            # `done` a prompt that was never executed.
            self.assertEqual(run.read_text(), before)

    def test_lookalike_key_is_not_matched_by_the_anchored_pattern(self):
        # `current_prompt_backup:` is not `current_prompt:` — the anchored pattern
        # `^current_prompt: .*$` requires the literal key, so a lookalike key must
        # survive the rewrite untouched, and the real key is still the sole match.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = self._write_run(tmp)
            book = tmp / "PB-9001-x.yaml"
            book.write_text(
                "id: PB-9001\n"
                "current_run: RUN-001\n"
                "current_prompt_backup: 99\n"
                "current_prompt: 1\n"
            )
            proc = _cli([str(run), "--outcome", "done", "--book", str(book)])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            text = book.read_text()
            self.assertIn("current_prompt_backup: 99", text)
            self.assertIn("current_prompt: null", text)
            self.assertIn("current_run: RUN-001", text)

    def test_a_well_formed_book_satisfies_every_rewrite_guard(self):
        # POSITIVE CONTROL for the refusal tests below: a well-formed book clears
        # every guard — the pre-write current_prompt type check, the pre-write
        # ^RUN-\d{3}$ pointer precondition, and both post-write re-parse checks.
        # It enters NONE of the refusal branches; each of those has its own test.
        # (The former name claimed a branch this test never reaches.)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = self._write_run(tmp)
            book = tmp / "PB-9001-x.yaml"
            book.write_text("id: PB-9001\ncurrent_run: RUN-001\ncurrent_prompt: 1\n")
            proc = _cli([str(run), "--outcome", "done", "--book", str(book)])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            import yaml
            doc = yaml.safe_load(book.read_text())
            self.assertEqual(doc["current_run"], "RUN-001")
            self.assertIsNone(doc["current_prompt"])

    def test_unparseable_book_fails_the_json_contract_and_leaves_the_run(self):
        # A book that is not YAML must produce the documented {"error": ...} exit-1
        # payload, NOT an uncaught traceback — and, being a pre-write check, must
        # leave the run snapshot untouched.
        import json
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = self._write_run(tmp)
            before = run.read_text()
            book = tmp / "PB-9001-x.yaml"
            book.write_text("id: PB-9001\ncurrent_run: RUN-001\ncurrent_prompt: 1\n"
                            "bad: [unclosed\n")
            proc = _cli([str(run), "--outcome", "done", "--book", str(book)])
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertIn("does not parse as YAML", payload["error"])
            self.assertEqual(run.read_text(), before)

    def test_a_non_int_current_prompt_is_refused_before_either_write(self):
        # The run snapshot is attacker-controlled input, and `current_prompt` is
        # taken from it and interpolated into the book rewrite. A `n:` carrying a
        # newline injects a top-level key into the book, which PyYAML's
        # last-duplicate-wins then makes authoritative. Refused on TYPE, before
        # either file is written.
        import json
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            book_text = ("id: PB-9001\ngoal: the real goal\n"
                         "current_run: RUN-001\ncurrent_prompt: 1\n")
            doc = _run_doc(2)
            doc["book_content_hash"] = _book_hash(book_text)
            doc["prompts"][1]["n"] = "2\ngoal: PWNED-INJECTED-KEY"
            run = tmp / "run-RUN-001.yaml"
            run.write_text(_yaml_text(doc))
            run_before = run.read_text()
            book = tmp / "PB-9001-x.yaml"
            book.write_text(book_text)
            proc = _cli([str(run), "--outcome", "done", "--book", str(book)])
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertIn("not an int or null", payload["error"])
            # Neither file moved, and the injected key never reached the book.
            self.assertEqual(book.read_text(), book_text)
            self.assertNotIn("PWNED-INJECTED-KEY", book.read_text())
            self.assertEqual(run.read_text(), run_before)

    def test_a_null_current_run_is_refused_before_either_write(self):
        # A book whose `current_run` is null cannot authorize the archive that a
        # completing advance hands it. This precondition is on PRE-EXISTING book
        # state, so it must fire before the writes: below them it would leave the
        # run `completed` and the book's `current_prompt` rewritten, then refuse.
        import json
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = tmp / "run-RUN-001.yaml"
            run.write_text(_yaml_text(_run_doc(1)))
            run_before = run.read_text()
            book = tmp / "PB-9001-x.yaml"
            book_text = "id: PB-9001\ncurrent_run: null\ncurrent_prompt: 1\n"
            book.write_text(book_text)
            proc = _cli([str(run), "--outcome", "done", "--book", str(book)])
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertIn("does not match ^RUN-", payload["error"])
            self.assertEqual(book.read_text(), book_text)
            self.assertEqual(run.read_text(), run_before)

    def test_post_write_parse_failure_gets_the_json_contract_not_a_traceback(self):
        # POST-WRITE branch 1, driven through the real CLI. The anchor `&p` lives
        # ON the line the rewrite replaces, so the alias `*p` below it dangles once
        # the line is gone and the rewritten book no longer parses. An unguarded
        # re-parse raises here — exit 1 with EMPTY stdout, which this repo's
        # exit-code convention reads as a crash rather than a finding.
        import json
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = tmp / "run-RUN-001.yaml"
            run.write_text(_yaml_text(_run_doc(1)))
            book = tmp / "PB-9001-x.yaml"
            book.write_text("id: PB-9001\ncurrent_run: RUN-001\n"
                            "current_prompt: &p 1\nnote: *p\n")
            proc = _cli([str(run), "--outcome", "done", "--book", str(book)])
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)
            self.assertTrue(proc.stdout.strip(), "exit 1 with empty stdout reads as a crash")
            payload = json.loads(proc.stdout)
            self.assertIn("no longer parses as YAML after write", payload["error"])

    def test_post_write_pointer_change_is_refused(self):
        # POST-WRITE branch 2, driven through the real CLI. `current_prompt`'s value
        # opens a multi-line FLOW sequence, so the second `current_run:` is nested
        # inside it and invisible to the pre-write parse. Replacing the opening line
        # closes the flow early and promotes that key to top level, where
        # last-duplicate-wins makes it the pointer — the exact silent-repoint this
        # guard exists to catch.
        import json
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = tmp / "run-RUN-001.yaml"
            run.write_text(_yaml_text(_run_doc(1)))
            book = tmp / "PB-9001-x.yaml"
            book.write_text("id: PB-9001\ncurrent_run: RUN-042\n"
                            "current_prompt: [1,\ncurrent_run: RUN-999]\n")
            proc = _cli([str(run), "--outcome", "done", "--book", str(book)])
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertIn("pointer changed during a write", payload["error"])
            self.assertIn("RUN-042", payload["error"])
            self.assertIn("RUN-999", payload["error"])

    def test_post_write_non_mapping_is_refused(self):
        # POST-WRITE branch 3. Stated honestly: this branch has NO CLI-reachable
        # vector. The pre-write check already proved the book is a mapping, and the
        # rewrite substitutes one column-0 line with `current_prompt: <int|null>`,
        # which yields either a mapping or a parse error (branch 1) — never a
        # sequence or a scalar. It is a backstop against a future rewrite that is
        # not line-local, so it is driven at `main()` with the substitution itself
        # replaced. The sibling branches above are driven through the real CLI.
        import io
        import contextlib
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = tmp / "run-RUN-001.yaml"
            run.write_text(_yaml_text(_run_doc(1)))
            book = tmp / "PB-9001-x.yaml"
            book.write_text("id: PB-9001\ncurrent_run: RUN-001\ncurrent_prompt: 1\n")
            buf = io.StringIO()
            with mock.patch.object(_ar.re, "sub", lambda *a, **k: "- not\n- a mapping\n"):
                with contextlib.redirect_stdout(buf):
                    with self.assertRaises(SystemExit) as ctx:
                        _ar.main([str(run), "--outcome", "done", "--book", str(book)])
            self.assertEqual(ctx.exception.code, 1)
            payload = __import__("json").loads(buf.getvalue())
            self.assertIn("no longer parses as a YAML mapping after write",
                          payload["error"])


@unittest.skipUnless(HAVE, "advance-run.py or PyYAML unavailable")
class AbandonValidatesBeforeWritingTests(unittest.TestCase):
    """Abandonment validates the document BEFORE it mutates it.

    An abandonment is recorded once and never retrofitted, so a half-applied one is
    unrecoverable: if `status: abandoned` reaches disk and the invocation then dies,
    the retrofit guard refuses every later attempt and the run can never archive by
    either path. Ordering is therefore the whole fix — the same order `advance()`
    already used."""

    def _malformed(self, tmp: Path) -> Path:
        """A snapshot with every key the CLI's own preamble checks, and no `prompts`.
        This is the shape that crashed AFTER the write."""
        path = tmp / "run-RUN-001.yaml"
        path.write_text(
            "format_version: '1'\n"
            "run_id: RUN-001\n"
            "book_id: PB-9001\n"
            "status: in_progress\n"
            "current_prompt: 1\n"
        )
        return path

    def test_abandon_on_a_run_missing_prompts_refuses_before_writing(self):
        import yaml
        with tempfile.TemporaryDirectory() as td:
            run = self._malformed(Path(td))
            before = run.read_text()
            proc = _cli([str(run), "--abandon", "--reason", "stopping"])
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            # A document verdict on stdout, not a traceback on stderr.
            self.assertIn("prompts", proc.stdout)
            self.assertNotIn("Traceback", proc.stderr)
            # And — the point of the test — the file is untouched, so the run is
            # still abandonable once the snapshot is repaired.
            self.assertEqual(run.read_text(), before)
            doc = yaml.safe_load(run.read_text())
            self.assertEqual(doc["status"], "in_progress")
            self.assertNotIn("abandonment", doc)

    def test_abandon_in_memory_refuses_a_run_missing_prompts(self):
        with self.assertRaises(SystemExit) as ctx:
            _ar.abandon({"status": "in_progress"}, "stopping")
        self.assertEqual(ctx.exception.code, 1)


@unittest.skipUnless(HAVE and shutil.which("git") is not None,
                     "advance-run.py, PyYAML or git unavailable")
class BaseCommitPinTests(unittest.TestCase):
    """`base_commit` is written once at run start and never rewritten.

    It is the one value the `patch` tier's archive check reads out of the run, so
    advancing it forward shrinks the diff that check proves — the exact move an
    overshooting run is motivated to make. Nothing pinned it, so this pins it against
    the snapshot's own committed record, mirroring abandonment's never-retrofit guard.

    The pin's honest limit is also asserted below: between run start and the
    snapshot's first commit there is no committed record, so there is nothing to
    compare against and the guard is inert."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name).resolve()
        self.addCleanup(self._td.cleanup)
        _git(self.root, "init", "-q", "-b", "main")
        doc = _run_doc(3)
        doc["base_commit"] = "a" * 40
        self.run_path, _ = _layout_run(self.root, _yaml_text(doc))

    def _commit(self):
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "snapshot")

    def _rewrite_base(self, value: str):
        import yaml
        doc = yaml.safe_load(self.run_path.read_text())
        doc["base_commit"] = value
        self.run_path.write_text(_yaml_text(doc))

    def test_a_base_commit_advanced_past_its_committed_record_is_refused(self):
        import yaml
        self._commit()
        self._rewrite_base("b" * 40)
        proc = _cli([str(self.run_path), "--outcome", "done"])
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("base_commit", proc.stdout)
        # Refused before the write: the advance did not land.
        doc = yaml.safe_load(self.run_path.read_text())
        self.assertEqual(doc["prompts"][0]["state"], "running")

    def test_the_abandon_path_is_pinned_too(self):
        self._commit()
        self._rewrite_base("b" * 40)
        proc = _cli([str(self.run_path), "--abandon", "--reason", "stopping"])
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("base_commit", proc.stdout)

    def test_an_unchanged_base_commit_advances_normally(self):
        self._commit()
        proc = _cli([str(self.run_path), "--outcome", "done"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_removing_the_base_commit_entirely_is_also_refused(self):
        import yaml
        self._commit()
        doc = yaml.safe_load(self.run_path.read_text())
        del doc["base_commit"]
        self.run_path.write_text(_yaml_text(doc))
        proc = _cli([str(self.run_path), "--outcome", "done"])
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("base_commit", proc.stdout)

    def test_adding_a_base_commit_where_the_record_had_none_is_refused(self):
        # The retrofit direction: a run that started outside a git work tree wrote
        # `base_commit: null`, and a later hand-edit supplies one to manufacture an
        # evidence source. "No committed record" and "a committed null" are
        # different states and must not be conflated.
        import yaml
        doc = yaml.safe_load(self.run_path.read_text())
        doc["base_commit"] = None
        self.run_path.write_text(_yaml_text(doc))
        self._commit()
        self._rewrite_base("c" * 40)
        proc = _cli([str(self.run_path), "--outcome", "done"])
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("base_commit", proc.stdout)

    def test_the_pin_is_inert_on_an_uncommitted_snapshot(self):
        # Stated as a test so the limit is recorded, not implied: a snapshot with no
        # committed version has nothing to compare against, and the guard must not
        # invent a verdict from the absence of evidence.
        self._rewrite_base("b" * 40)
        proc = _cli([str(self.run_path), "--outcome", "done"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_the_pin_is_inert_outside_a_git_work_tree(self):
        with tempfile.TemporaryDirectory() as td:
            doc = _run_doc(3)
            doc["base_commit"] = "a" * 40
            path, _ = _layout_run(Path(td), _yaml_text(doc))
            proc = _cli([str(path), "--outcome", "done"])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


FIXED_NOW = "2026-09-28T12:00:00Z"
BOOK_TEXT = "id: PB-9999\ncurrent_run: RUN-001\ncurrent_prompt: 1\n"

# Lines of the hand-formatted fixture that no advance or abandonment touches.
# Each must survive every write verbatim.
SURVIVING_LINES = (
    "# Hand-formatted run snapshot for advance-run.py tests. Every advance must keep this line.",
    'format_version: "1"',
    "started_at: 2026-09-27T09:11:00Z   # unquoted timestamp with an inline comment",
    "  # A comment between prompt items.",
    "notes: |",
    "  First line of the notes block.",
    "    An indented second line.",
)


def _path_diff(a, b, prefix=()):
    """The semantic difference between two loaded documents, as a set of paths.

    A changed value is its path tuple; a key only `b` has is ``("+", *path)`` and a
    key only `a` has is ``("-", *path)``. Equal-length lists of mappings are compared
    element by element, so a prompt element's changed field names its own path."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = set()
        for k in b:
            if k not in a:
                out.add(("+",) + prefix + (k,))
            else:
                out |= _path_diff(a[k], b[k], prefix + (k,))
        for k in a:
            if k not in b:
                out.add(("-",) + prefix + (k,))
        return out
    if (isinstance(a, list) and isinstance(b, list) and len(a) == len(b)
            and all(isinstance(x, dict) for x in a + b)):
        out = set()
        for i, (x, y) in enumerate(zip(a, b)):
            out |= _path_diff(x, y, prefix + (i,))
        return out
    return set() if a == b else {prefix}


def _value_span(text: str, path: tuple) -> tuple[int, int]:
    """The ``[start, end)`` character span of the value at `path`: from just after
    its key's colon to the end of its last descendant's content, trailing
    whitespace trimmed. Written independently of the script's own span code, so the
    byte check below does not grade the script against itself."""
    import yaml
    node = yaml.compose(text)
    key = None
    for step in path:
        if isinstance(step, int):
            node, key = node.value[step], None
            continue
        for k, v in node.value:
            if k.value == step:
                key, node = k, v
                break
        else:
            raise AssertionError(f"path {path!r} is not in the document")
    start = text.index(":", key.end_mark.index) + 1
    last = node
    while (isinstance(last, (yaml.SequenceNode, yaml.MappingNode))
           and not last.flow_style and last.value):
        last = last.value[-1] if isinstance(last, yaml.SequenceNode) else last.value[-1][1]
    end = last.end_mark.index
    while end > start and text[end - 1] in " \t\r\n":
        end -= 1
    return start, end


def _outside(text: str, spans: list[tuple[int, int]]) -> list[str]:
    """The segments of `text` outside `spans` (sorted, disjoint)."""
    segs, pos = [], 0
    for s, e in spans:
        segs.append(text[pos:s])
        pos = e
    segs.append(text[pos:])
    return segs


@unittest.skipUnless(HAVE, "advance-run.py or PyYAML unavailable")
class SpliceWriterTests(unittest.TestCase):
    """An advance or an abandonment rewrites only the values it changes.

    Run snapshots are append-only: only state, timestamp, result and artifacts
    fields update. Comments, quoting, indentation and timestamp spelling everywhere
    else must survive byte for byte, so a run's diff shows the advance and nothing
    else. Every test drives the real `main()` over the hand-formatted fixture."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.base = HAND_FORMATTED.read_bytes().decode("utf-8")

    # -- harness ---------------------------------------------------------------

    def _files(self, text: str | None = None) -> tuple[Path, Path]:
        return _layout_run(self.tmp, self.base if text is None else text, BOOK_TEXT, "PB-9999-x")

    def _main(self, args: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        with mock.patch.object(_ar, "_now", return_value=FIXED_NOW), \
                contextlib.redirect_stdout(buf):
            try:
                code = _ar.main(args)
            except SystemExit as exc:
                code = exc.code
        return code, buf.getvalue()

    def _step(self, run: Path, book: Path, *args: str) -> tuple[str, str]:
        before = run.read_bytes().decode("utf-8")
        code, out = self._main([str(run), *args, "--book", str(book)])
        self.assertEqual(code, 0, out)
        return before, run.read_bytes().decode("utf-8")

    def _mid_run(self) -> tuple[str, str]:
        run, book = self._files()
        return self._step(run, book, "--outcome", "done", "--result", "verify done")

    def _completing(self) -> list[tuple[str, str]]:
        run, book = self._files()
        return [
            self._step(run, book, "--outcome", "done", "--result", "p1"),
            self._step(run, book, "--outcome", "done", "--result", "p2",
                       "--artifacts", "docs/p2.md"),
            self._step(run, book, "--outcome", "done", "--result", "p3",
                       "--artifacts", "docs/p3.md"),
        ]

    def _abandon(self) -> tuple[str, str]:
        run, book = self._files()
        return self._step(run, book, "--abandon", "--reason", "stopping here")

    def _assert_only_spans_changed(self, before: str, after: str, paths: set) -> None:
        """Mask the changed value spans on both sides; everything else must be
        byte-identical, apart from keys appended at the end of the file."""
        changed = sorted((p for p in paths if p[0] not in ("+", "-")),
                         key=lambda p: _value_span(before, p)[0])
        b_segs = _outside(before, [_value_span(before, p) for p in changed])
        a_segs = _outside(after, [_value_span(after, p) for p in changed])
        self.assertEqual(a_segs[:-1], b_segs[:-1],
                         "bytes outside the changed value spans moved")
        if any(p[0] == "+" for p in paths):
            self.assertTrue(a_segs[-1].startswith(b_segs[-1]),
                            "the file tail changed beyond an end-of-file append")
        else:
            self.assertEqual(a_segs[-1], b_segs[-1], "the file tail changed")
        lines = after.splitlines()
        for line in SURVIVING_LINES:
            self.assertIn(line, lines, f"line lost: {line!r}")

    MID_RUN_PATHS = {
        ("current_prompt",),
        ("prompts", 0, "state"), ("prompts", 0, "completed"), ("prompts", 0, "result"),
        ("prompts", 1, "state"), ("prompts", 1, "started"),
        ("+", "summary"),
    }

    # -- (a) the semantic diff is exactly the intended field set ---------------

    def test_mid_run_advance_changes_exactly_the_intended_fields(self):
        before, after = self._mid_run()
        self.assertEqual(_path_diff(load_yaml(before), load_yaml(after)), self.MID_RUN_PATHS)
        doc = load_yaml(after)
        self.assertEqual(doc["prompts"][0]["completed"], FIXED_NOW)
        self.assertEqual(doc["prompts"][0]["result"], "verify done")
        self.assertEqual(doc["prompts"][1]["started"], FIXED_NOW)
        self.assertEqual(doc["summary"], "")

    def test_completing_advance_changes_exactly_the_intended_fields(self):
        steps = self._completing()
        expected = [
            {("current_prompt",), ("prompts", 0, "state"), ("prompts", 0, "completed"),
             ("prompts", 0, "result"), ("prompts", 1, "state"), ("prompts", 1, "started"),
             ("+", "summary")},
            {("current_prompt",), ("prompts", 1, "state"), ("prompts", 1, "completed"),
             ("prompts", 1, "result"), ("prompts", 1, "artifacts"), ("prompts", 2, "state")},
            {("status",), ("completed_at",), ("current_prompt",), ("prompts", 2, "state"),
             ("prompts", 2, "completed"), ("prompts", 2, "result"), ("prompts", 2, "artifacts")},
        ]
        for i, ((before, after), want) in enumerate(zip(steps, expected), start=1):
            with self.subTest(advance=i):
                self.assertEqual(_path_diff(load_yaml(before), load_yaml(after)), want)
        final = load_yaml(steps[-1][1])
        self.assertEqual(final["status"], "completed")
        self.assertIsNone(final["current_prompt"])
        self.assertEqual(final["prompts"][1]["artifacts"], ["docs/p2.md"])
        self.assertEqual(final["prompts"][2]["artifacts"], ["docs/p3.md"])

    def test_abandon_changes_exactly_the_intended_fields(self):
        before, after = self._abandon()
        self.assertEqual(
            _path_diff(load_yaml(before), load_yaml(after)),
            {("status",), ("completed_at",), ("current_prompt",),
             ("+", "abandonment"), ("+", "summary")})
        self.assertEqual(load_yaml(after)["abandonment"],
                         {"kind": "deliberate", "at": FIXED_NOW, "reason": "stopping here"})

    # -- (b) with the changed spans masked, the file is byte-identical ---------

    def test_mid_run_advance_keeps_every_other_byte(self):
        before, after = self._mid_run()
        self._assert_only_spans_changed(before, after, self.MID_RUN_PATHS)

    def test_completing_advance_keeps_every_other_byte(self):
        for i, (before, after) in enumerate(self._completing(), start=1):
            with self.subTest(advance=i):
                self._assert_only_spans_changed(
                    before, after, _path_diff(load_yaml(before), load_yaml(after)))

    def test_abandon_keeps_every_other_byte(self):
        before, after = self._abandon()
        self._assert_only_spans_changed(
            before, after, _path_diff(load_yaml(before), load_yaml(after)))

    def test_byte_check_rejects_a_whole_document_re_emit(self):
        # Positive control for the byte check above: a whole-document re-emit of
        # the same values must fail it, or the check proves nothing.
        rewritten = _yaml_text(load_yaml(self.base))
        with self.assertRaises(AssertionError):
            self._assert_only_spans_changed(self.base, rewritten, self.MID_RUN_PATHS)

    # -- (c) every result validates against the run schema ---------------------

    def test_every_result_validates_as_a_run(self):
        results = [self._mid_run()[1], *(a for _, a in self._completing()), self._abandon()[1]]
        for i, text in enumerate(results):
            with self.subTest(result=i):
                path = self.tmp / f"result-{i}.yaml"
                path.write_bytes(text.encode("utf-8"))
                proc = subprocess.run(
                    [sys.executable, str(VALIDATE), "--kind", "run", str(path)],
                    capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    # -- (d) a postcondition refusal writes nothing ----------------------------

    def test_a_failed_postcondition_refuses_and_writes_nothing(self):
        run, book = self._files()
        run_bytes, book_bytes = run.read_bytes(), book.read_bytes()
        with mock.patch.object(_ar, "_emit", return_value='"WRONG"'):
            code, out = self._main([str(run), "--outcome", "done", "--result", "x",
                                    "--book", str(book)])
        self.assertEqual(code, 1, out)
        self.assertIn("postcondition", json.loads(out)["error"])
        self.assertEqual(run.read_bytes(), run_bytes)
        self.assertEqual(book.read_bytes(), book_bytes)
        # Positive control: the same command without the fault writes both files,
        # so the unchanged bytes above are the refusal's doing.
        code, out = self._main([str(run), "--outcome", "done", "--result", "x",
                                "--book", str(book)])
        self.assertEqual(code, 0, out)
        self.assertNotEqual(run.read_bytes(), run_bytes)
        self.assertNotEqual(book.read_bytes(), book_bytes)

    # -- one refusal per unsupported shape -------------------------------------

    def _variant(self, old: str, new: str) -> str:
        text = self.base.replace(old, new, 1)
        self.assertNotEqual(text, self.base, f"variant edit did not land: {old!r}")
        return text

    def _assert_refused(self, text: str, args: list[str], needle: str) -> None:
        run, book = self._files(text)
        run_bytes, book_bytes = run.read_bytes(), book.read_bytes()
        code, out = self._main([str(run), *args, "--book", str(book)])
        self.assertEqual(code, 1, out)
        self.assertIn(needle, json.loads(out)["error"])
        self.assertEqual(run.read_bytes(), run_bytes)
        self.assertEqual(book.read_bytes(), book_bytes)

    def test_unsupported_shapes_are_refused_and_write_nothing(self):
        advance = ["--outcome", "done", "--result", "new"]
        cases = {
            "a comment inside a replaced block list": (
                self._variant("    artifacts: []\n",
                              "    artifacts:\n      - docs/x.md\n"
                              "      # inside the list\n      - docs/y.md\n"),
                advance, "comment inside"),
            "a comment on a block-scalar header": (
                self._variant('    result: ""\n',
                              "    result: |  # a header comment\n      old text\n"),
                advance, "block-scalar header"),
            "an anchor": (
                self._variant('    title: "Verify', '    title: &t "Verify'),
                advance, "anchor"),
            "a duplicate top-level key": (
                self.base + 'pr_draft: ""\n', advance, "duplicate"),
            "mixed line terminators": (
                self.base.replace("\n", "\r\n", 1), advance, "line ending"),
            "a nested added key": (
                self._variant("    completed: null\n", ""), advance, "nested"),
        }
        for name, (text, args, needle) in cases.items():
            with self.subTest(shape=name):
                self._assert_refused(text, args, needle)

    def test_a_refusal_reaches_the_real_command_line(self):
        # The same refusal through the installed entry point, not only main().
        run, _ = self._files(self.base + 'pr_draft: ""\n')
        run_bytes = run.read_bytes()
        proc = _cli([str(run), "--outcome", "done"])
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn("duplicate", json.loads(proc.stdout)["error"])
        self.assertEqual(run.read_bytes(), run_bytes)

    # -- line endings, keep-chomped block scalars, and the emitter -------------

    def test_crlf_line_endings_are_preserved(self):
        crlf = self.base.replace("\n", "\r\n")
        run, book = self._files(crlf)
        before, after = self._step(run, book, "--outcome", "done", "--result", "verify done")
        self.assertEqual(after.count("\r\n"), after.count("\n"))
        self.assertEqual(after.count("\r"), after.count("\n"))
        self.assertEqual(_path_diff(load_yaml(before), load_yaml(after)), self.MID_RUN_PATHS)
        self._assert_only_spans_changed(before, after, self.MID_RUN_PATHS)

    def test_a_final_keep_block_scalar_survives_an_append(self):
        text = self.base + "summary: |+\n  kept text\n\n"
        run, book = self._files(text)
        before, after = self._step(run, book, "--abandon", "--reason", "stopping")
        self.assertEqual(load_yaml(after)["summary"], "kept text\n\n")
        self.assertIn("summary: |+\n  kept text\n\nabandonment:\n", after)
        self._assert_only_spans_changed(
            before, after, _path_diff(load_yaml(before), load_yaml(after)))

    def test_a_final_keep_block_scalar_without_a_newline_is_refused(self):
        # Appending after it would add a newline to its value, so the
        # postcondition refuses rather than change a value nobody asked to change.
        self._assert_refused(self.base + "summary: |+\n  kept text",
                             ["--abandon", "--reason", "stopping"], "postcondition")

    def test_the_emitter_round_trips_awkward_characters(self):
        awkward = 'astral \U0001D11E, ls  , nel \x85, nl \n, quote ", backslash \\ end'
        run, book = self._files()
        before, after = self._step(run, book, "--outcome", "done", "--result", awkward)
        self.assertEqual(load_yaml(after)["prompts"][0]["result"], awkward)
        self._assert_only_spans_changed(
            before, after, _path_diff(load_yaml(before), load_yaml(after)))


if __name__ == "__main__":
    unittest.main()
