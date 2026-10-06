"""ADR-0077 clause 2 — the archive-time blast-radius containment check.

The ADR accepted the `patch` tier conditionally: "If no source proves which paths a
run changed, clause 2 is unmet and the tier does not ship." These tests exercise the
source that was chosen — git — against a throwaway repository, so the proof is real
rather than asserted.

Each test builds its own git repo in a temp dir with the ambient git config
neutralized, so the result does not depend on the developer's ~/.gitconfig.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = REPO_ROOT / "crux" / "scripts" / "check-blast-radius.py"

try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False

HAVE_GIT = shutil.which("git") is not None

PIN = REPO_ROOT / "crux" / "scripts" / "base_commit_pin.py"

if HAVE_YAML and SCRIPT.is_file():
    _spec = importlib.util.spec_from_file_location("check_blast_radius", SCRIPT)
    _cbr = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_cbr)
    # Loaded by path, like the script under test: the tests run without crux/scripts on
    # sys.path, in the dev tree and in the staged artifact alike.
    _pspec = importlib.util.spec_from_file_location("base_commit_pin_under_test", PIN)
    _PIN = importlib.util.module_from_spec(_pspec)
    _pspec.loader.exec_module(_PIN)
    HAVE = True
else:
    HAVE = False


def _git(cwd: Path, *args: str) -> None:
    env = dict(os.environ)
    env.update({
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    })
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True, env=env)


def _write(root: Path, rel: str, text: str = "x\n") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@unittest.skipUnless(HAVE, "check-blast-radius.py or PyYAML unavailable")
class PurePredicateTests(unittest.TestCase):
    """The containment and exclusion predicates, isolated from git."""

    def test_exact_path_is_covered(self):
        self.assertTrue(_cbr.covered_by("crux/scripts/a.py", "crux/scripts/a.py"))

    def test_directory_prefix_covers_a_nested_file(self):
        self.assertTrue(_cbr.covered_by("crux/scripts/sub/a.py", "crux/scripts"))
        self.assertTrue(_cbr.covered_by("crux/scripts/sub/a.py", "crux/scripts/"))

    def test_a_sibling_with_a_shared_string_prefix_is_not_covered(self):
        # The whole reason containment compares SEGMENTS: a raw startswith would
        # let this through and silently widen every declaration.
        self.assertFalse(_cbr.covered_by("crux/scriptsX/a.py", "crux/scripts"))
        self.assertFalse(_cbr.covered_by("crux/scripts-old/a.py", "crux/scripts"))

    def test_a_parent_is_not_covered_by_its_child(self):
        self.assertFalse(_cbr.covered_by("crux/a.py", "crux/scripts"))

    def test_bookkeeping_paths_are_excluded(self):
        for p in ("bionic/promptbooks/runs/PB-1-x/run-RUN-001.yaml",
                  "bionic/promptbooks/index.md",
                  "bionic/promptbooks/active/PB-1-x.yaml",
                  "bionic/journal/2026-08.md",
                  "bionic/arch/overview.md",
                  "bionic/log.md", "bionic/index.md",
                  "bionic/manifest.yml", "bionic/whats_next.md"):
            with self.subTest(path=p):
                self.assertTrue(_cbr.is_bookkeeping(p, "bionic", "PB-1-x"))

    def test_another_books_promptbook_surfaces_are_not_excluded(self):
        # The exclusion set covers what the machinery writes for THIS book. A
        # blanket promptbooks/ exclusion also covered every OTHER book — including
        # archived ones, which are immutable — so a patch could rewrite another
        # book's plan or an archived record and the check would never see it.
        for p in ("bionic/promptbooks/active/PB-2-y.yaml",
                  "bionic/promptbooks/archive/PB-3-z.yaml",
                  "bionic/promptbooks/runs/PB-2-y/run-RUN-001.yaml",
                  "bionic/promptbooks/legacy/archive/PB-4-w.md"):
            with self.subTest(path=p):
                self.assertFalse(_cbr.is_bookkeeping(p, "bionic", "PB-1-x"))

    def test_the_running_books_own_archive_destination_is_excluded(self):
        # Archival moves the book from active/ to archive/; both spellings of the
        # running book's own file are the machinery's write, not the run's change.
        self.assertTrue(_cbr.is_bookkeeping("bionic/promptbooks/archive/PB-1-x.yaml",
                                            "bionic", "PB-1-x"))

    def test_a_book_stem_prefix_does_not_widen_the_exclusion(self):
        # `PB-1-x` must not exclude `PB-1-xtra`'s surfaces by string prefix.
        self.assertFalse(_cbr.is_bookkeeping("bionic/promptbooks/runs/PB-1-xtra/run-RUN-001.yaml",
                                             "bionic", "PB-1-x"))
        self.assertFalse(_cbr.is_bookkeeping("bionic/promptbooks/active/PB-1-xtra.yaml",
                                             "bionic", "PB-1-x"))

    def test_an_adr_write_is_not_excluded(self):
        # Clause 2: work that needs an ADR is not a patch, so an ADR path must
        # reach the containment test and fail it.
        self.assertFalse(_cbr.is_bookkeeping("bionic/adrs/ADR-0099-x.md", "bionic", "PB-1-x"))

    def test_the_docs_root_itself_and_outside_paths_are_not_excluded(self):
        self.assertFalse(_cbr.is_bookkeeping("bionic", "bionic", "PB-1-x"))
        self.assertFalse(_cbr.is_bookkeeping("crux/scripts/a.py", "bionic", "PB-1-x"))
        self.assertFalse(_cbr.is_bookkeeping("bionicX/log.md", "bionic", "PB-1-x"))

    def test_the_exclusion_set_follows_a_relocated_docs_dir(self):
        self.assertTrue(_cbr.is_bookkeeping("docs/log.md", "docs", "PB-1-x"))
        self.assertFalse(_cbr.is_bookkeeping("bionic/log.md", "docs", "PB-1-x"))


@unittest.skipUnless(HAVE and HAVE_GIT, "check-blast-radius.py, PyYAML or git unavailable")
class BlastRadiusCheckTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name).resolve()
        _git(self.root, "init", "-q", "-b", "main")
        _write(self.root, "crux/scripts/target.py", "original\n")
        _write(self.root, "crux/scripts/other.py", "original\n")
        _write(self.root, "bionic/log.md", "log\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "base")
        self.base = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()
        self.addCleanup(self._td.cleanup)

    def _docs(self, blast_radius=("crux/scripts/target.py",), base=None,
              cycle_kind="patch", hash_override=None) -> tuple[Path, Path]:
        # The book and run live at their real paths, under the promptbooks subtree
        # that the bookkeeping exclusion set covers — so this fixture also proves
        # the check does not trip over its own inputs.
        import yaml as _y
        book = self.root / "bionic/promptbooks/active/PB-9102-x.yaml"
        run = self.root / "bionic/promptbooks/runs/PB-9102-x/run-RUN-001.yaml"
        book.parent.mkdir(parents=True, exist_ok=True)
        run.parent.mkdir(parents=True, exist_ok=True)
        bdoc = {"format_version": "1", "id": "PB-9102", "cycle_kind": cycle_kind}
        if blast_radius is not None:
            bdoc["blast_radius"] = list(blast_radius)
        book.write_text(_y.safe_dump(bdoc))
        # The run is BOUND to the book by book_content_hash. The fixture computes
        # the real hash with the same function the writer uses, so a test that
        # moves the plan afterwards reproduces a genuine mid-run edit.
        rdoc = {"format_version": "1", "run_id": "RUN-001", "book_id": "PB-9102",
                "book_content_hash": (_cbr.compute_book_hash(bdoc) if hash_override is None
                                      else hash_override),
                "base_commit": self.base if base is None else base}
        run.write_text(_y.safe_dump(rdoc))
        return book, run

    def _widen(self, book: Path, entries) -> None:
        """Rewrite the book's declaration WITHOUT touching the run's stored hash —
        exactly what a mid-run edit to the plan looks like on disk."""
        import yaml as _y
        doc = _y.safe_load(book.read_text())
        doc["blast_radius"] = list(entries)
        book.write_text(_y.safe_dump(doc))

    def _check(self, **kw):
        book, run = self._docs(**kw)
        return _cbr.check(book, run, self.root, "bionic")

    def test_a_change_inside_the_declared_radius_is_clean(self):
        _write(self.root, "crux/scripts/target.py", "changed\n")
        code, payload = self._check()
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["clean"])
        self.assertIn("crux/scripts/target.py", payload["changed"])

    def test_an_undeclared_tracked_change_is_a_finding(self):
        _write(self.root, "crux/scripts/other.py", "changed\n")
        code, payload = self._check()
        self.assertEqual(code, 1)
        self.assertEqual(payload["undeclared"], ["crux/scripts/other.py"])
        self.assertIsNone(payload["error"])

    def test_an_undeclared_untracked_file_is_a_finding(self):
        # A brand-new file never reaches `git diff`; the untracked sweep is what
        # catches it, and without that the check would pass on wholly new work.
        _write(self.root, "crux/scripts/brand-new.py", "new\n")
        code, payload = self._check()
        self.assertEqual(code, 1)
        self.assertIn("crux/scripts/brand-new.py", payload["undeclared"])

    def test_a_staged_change_is_seen(self):
        _write(self.root, "crux/scripts/other.py", "changed\n")
        _git(self.root, "add", "crux/scripts/other.py")
        code, payload = self._check()
        self.assertEqual(code, 1)
        self.assertIn("crux/scripts/other.py", payload["undeclared"])

    def test_a_committed_change_after_the_base_is_seen(self):
        _write(self.root, "crux/scripts/other.py", "changed\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "later")
        code, payload = self._check()
        self.assertEqual(code, 1)
        self.assertIn("crux/scripts/other.py", payload["undeclared"])

    def test_bookkeeping_writes_do_not_count_as_changed_paths(self):
        _write(self.root, "bionic/log.md", "appended\n")
        _write(self.root, "bionic/promptbooks/index.md", "regenerated\n")
        code, payload = self._check()
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["undeclared"], [])

    def test_an_adr_write_fails_the_check(self):
        _write(self.root, "bionic/adrs/ADR-0099-x.md", "decision\n")
        code, payload = self._check()
        self.assertEqual(code, 1)
        self.assertIn("bionic/adrs/ADR-0099-x.md", payload["undeclared"])

    def test_a_directory_declaration_covers_nested_changes(self):
        _write(self.root, "crux/scripts/sub/deep.py", "new\n")
        code, payload = self._check(blast_radius=("crux/scripts",))
        self.assertEqual(code, 0, payload)

    def test_a_sibling_directory_is_not_covered(self):
        _write(self.root, "crux/scriptsX/a.py", "new\n")
        code, payload = self._check(blast_radius=("crux/scripts",))
        self.assertEqual(code, 1)
        self.assertIn("crux/scriptsX/a.py", payload["undeclared"])

    def test_a_missing_base_commit_refuses(self):
        code, payload = self._check(base="")
        self.assertEqual(code, 1)
        self.assertIn("base_commit", payload["error"])

    def test_a_malformed_base_commit_refuses(self):
        for bad in ("HEAD", "--upload-pack=evil", "z" * 40, "abc"):
            with self.subTest(base=bad):
                code, payload = self._check(base=bad)
                self.assertEqual(code, 1)
                self.assertIn("base_commit", payload["error"])

    def test_a_non_patch_book_refuses(self):
        code, payload = self._check(cycle_kind="adr")
        self.assertEqual(code, 1)
        self.assertIn("patch", payload["error"])

    def test_a_missing_blast_radius_refuses(self):
        code, payload = self._check(blast_radius=None)
        self.assertEqual(code, 1)
        self.assertIn("blast_radius", payload["error"])

    def test_a_non_repo_relative_declaration_refuses(self):
        for bad in ("/etc", "../outside", "~/x"):
            with self.subTest(entry=bad):
                code, payload = self._check(blast_radius=(bad,))
                self.assertEqual(code, 1)
                self.assertIn("repo-relative", payload["error"])

    def test_a_missing_document_refuses(self):
        book, run = self._docs()
        run.unlink()
        code, payload = _cbr.check(book, run, self.root, "bionic")
        self.assertEqual(code, 1)
        self.assertIn("not found", payload["error"])

    def test_an_unknown_base_commit_is_a_capability_error(self):
        # Fail-closed and in the ENVIRONMENT lane: an unproven radius is not a
        # passed one, and this is not a verdict about the document.
        book, run = self._docs(base="0" * 40)
        with self.assertRaises(_cbr.CapabilityError):
            _cbr.check(book, run, self.root, "bionic")

    def test_not_a_git_work_tree_is_a_capability_error(self):
        with tempfile.TemporaryDirectory() as td:
            book, run = self._docs()
            with self.assertRaises(_cbr.CapabilityError):
                _cbr.check(book, run, Path(td).resolve(), "bionic")

    def test_a_repo_root_below_the_work_tree_top_is_refused(self):
        # git reports paths relative to the work-tree TOP; a declaration is
        # relative to the repo root. Comparing across those two frames is
        # meaningless, so it must refuse rather than silently mis-compare.
        book, run = self._docs()
        sub = self.root / "crux"
        with self.assertRaises(_cbr.CapabilityError) as cm:
            _cbr.check(book, run, sub, "bionic")
        self.assertIn("work-tree top", str(cm.exception))

    def test_a_path_needing_git_quoting_is_matched_not_phantom_reported(self):
        # git's default output QUOTES a path with non-ASCII bytes
        # ("cr\303\274x/a.py"). A quoted path matches no declaration, so the
        # check would invent an undeclared path on a tree that never overstepped.
        # The -z/NUL output is what prevents that.
        _write(self.root, "crux/scripts/n\u00e4me.py", "new\n")
        code, payload = self._check(blast_radius=("crux/scripts",))
        self.assertEqual(code, 0, payload)
        self.assertIn("crux/scripts/n\u00e4me.py", payload["changed"])

    def test_a_declaration_widened_mid_run_is_refused_at_archive(self):
        # The freeze claim was "blast_radius sits inside the frozen-plan subset, so
        # widening it moves book_content_hash and CHK-PB-BIND fires". True, but
        # CHK-PB-BIND is an audit rule that nothing on the archive path runs — and
        # THIS check read the live declaration. So widening the radius mid-run
        # turned a refusal into a pass, at the one gate that consumes the freeze.
        book, run = self._docs(blast_radius=("crux/scripts/target.py",))
        _write(self.root, "crux/scripts/other.py", "changed\n")
        # Before widening: a real finding.
        code, _ = _cbr.check(book, run, self.root, "bionic")
        self.assertEqual(code, 1)
        # Widen the declaration to cover the overshoot, leaving the run's hash.
        self._widen(book, ("crux/scripts",))
        code, payload = _cbr.check(book, run, self.root, "bionic")
        self.assertEqual(code, 1, payload)
        self.assertIn("book_content_hash", payload["error"])

    def test_narrowing_the_declaration_mid_run_is_refused_too(self):
        # The guard is on the binding, not on the direction of the edit: the plan
        # is frozen for the run's life, either way.
        book, run = self._docs(blast_radius=("crux/scripts",))
        self._widen(book, ("crux/scripts/target.py",))
        code, payload = _cbr.check(book, run, self.root, "bionic")
        self.assertEqual(code, 1, payload)
        self.assertIn("book_content_hash", payload["error"])

    def test_an_unbound_run_is_refused(self):
        # Fail-closed: book_content_hash is a required run field, so its absence
        # means the declaration cannot be proved frozen, not that it is.
        import yaml as _y
        book, run = self._docs()
        doc = _y.safe_load(run.read_text())
        del doc["book_content_hash"]
        run.write_text(_y.safe_dump(doc))
        code, payload = _cbr.check(book, run, self.root, "bionic")
        self.assertEqual(code, 1)
        self.assertIn("book_content_hash", payload["error"])

    def test_a_matching_hash_does_not_refuse(self):
        _write(self.root, "crux/scripts/target.py", "changed\n")
        code, payload = self._check()
        self.assertEqual(code, 0, payload)

    def test_a_rename_out_of_an_undeclared_path_is_not_collapsed(self):
        # git's rename detection reports ONLY the destination, so
        # `git mv secret/x.py crux/scripts/x.py` presented one path — a declared
        # one — while deleting an undeclared file. The deletion was invisible.
        _write(self.root, "secret/x.py", "secret\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "add secret")
        base = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        self.base = base
        book, run = self._docs(blast_radius=("crux/scripts",))
        _git(self.root, "mv", "secret/x.py", "crux/scripts/x.py")
        code, payload = _cbr.check(book, run, self.root, "bionic")
        self.assertEqual(code, 1, payload)
        self.assertIn("secret/x.py", payload["undeclared"])

    def test_a_rename_wholly_inside_the_radius_still_passes(self):
        # The fix must not turn every in-radius rename into a finding.
        book, run = self._docs(blast_radius=("crux/scripts",))
        _git(self.root, "mv", "crux/scripts/target.py", "crux/scripts/renamed.py")
        code, payload = _cbr.check(book, run, self.root, "bionic")
        self.assertEqual(code, 0, payload)
        self.assertIn("crux/scripts/target.py", payload["changed"])
        self.assertIn("crux/scripts/renamed.py", payload["changed"])

    def test_another_books_plan_is_a_finding(self):
        # The narrowed exclusion set in action, end to end.
        _write(self.root, "bionic/promptbooks/active/PB-9999-other.yaml", "tampered\n")
        code, payload = self._check()
        self.assertEqual(code, 1)
        self.assertIn("bionic/promptbooks/active/PB-9999-other.yaml", payload["undeclared"])

    def test_a_repo_root_declaration_is_refused_not_silently_all_covering(self):
        # A radius of "." would cover every path, so containment could never
        # fail — the tier's guard would be decorative.
        for bad in (".", "./"):
            with self.subTest(entry=bad):
                _write(self.root, "crux/scripts/other.py", "changed\n")
                code, payload = self._check(blast_radius=(bad,))
                self.assertEqual(code, 1)
                self.assertIn("repository root", payload["error"])


@unittest.skipUnless(HAVE and HAVE_GIT, "check-blast-radius.py, PyYAML or git unavailable")
class BaseCommitPinAtTheGateTests(unittest.TestCase):
    """`base_commit` is the one value this check reads out of the run, and the pin on
    it has to hold at BOTH ends of the run.

    `advance-run.py` refuses to write a divergent value, but a run that overshoots can
    commit its work, hand-edit `base_commit` forward, and archive without ever calling
    that writer. The gate then compared the declaration against a diff from the NEW
    base — an empty one — and passed. So the pin now binds here too, and §11.C's
    "pinned rather than trusted" is true at the gate that consumes it.

    The no-claim lane is asserted alongside the refusal on purpose: a snapshot with no
    committed version yet has nothing holding its value, and refusing it would refuse
    every run during its first prompt."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name).resolve()
        _git(self.root, "init", "-q", "-b", "main")
        _write(self.root, "crux/scripts/target.py", "original\n")
        _write(self.root, "crux/scripts/other.py", "original\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "base")
        self.base = self._head()
        self.book = self.root / "bionic/promptbooks/active/PB-9103-x.yaml"
        self.run = self.root / "bionic/promptbooks/runs/PB-9103-x/run-RUN-001.yaml"
        self.book.parent.mkdir(parents=True, exist_ok=True)
        self.run.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(self._td.cleanup)

    def _head(self) -> str:
        return subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()

    def _write_docs(self, base: str) -> None:
        import yaml as _y
        bdoc = {"format_version": "1", "id": "PB-9103", "cycle_kind": "patch",
                "blast_radius": ["crux/scripts/target.py"]}
        self.book.write_text(_y.safe_dump(bdoc))
        self.run.write_text(_y.safe_dump(
            {"format_version": "1", "run_id": "RUN-001", "book_id": "PB-9103",
             "book_content_hash": _cbr.compute_book_hash(bdoc),
             "base_commit": base}))

    def _set_base(self, value) -> None:
        """Hand-edit `base_commit` on disk, leaving the committed version alone —
        exactly what tampering looks like from the filesystem."""
        import yaml as _y
        doc = _y.safe_load(self.run.read_text())
        doc["base_commit"] = value
        self.run.write_text(_y.safe_dump(doc))

    def _check(self):
        return _cbr.check(self.book, self.run, self.root, "bionic")

    def test_a_base_commit_advanced_past_its_committed_record_is_refused(self):
        # The end-to-end bypass, reproduced: overshoot, commit, then move the pin
        # forward so the diff the gate draws is empty.
        self._write_docs(self.base)
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "run started")
        _write(self.root, "crux/scripts/other.py", "overshoot\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "overshoot committed")
        overshot_head = self._head()

        # Untampered, the overshoot is a finding.
        code, payload = self._check()
        self.assertEqual(code, 1, payload)
        self.assertIn("crux/scripts/other.py", payload["undeclared"])

        # Tampered forward: the diff from the new base is empty, so before the pin
        # bound here this returned exit 0 with `changed: []`.
        self._set_base(overshot_head)
        code, payload = self._check()
        self.assertEqual(code, 1, payload)
        self.assertIn("base_commit", payload["error"])
        self.assertEqual(payload["undeclared"], [])

    def test_a_base_commit_moved_backward_is_refused_too(self):
        # The guard is on the divergence, not on the direction: a value written once
        # is not rewritten, and a backward move widens the diff rather than shrinking
        # it. Both are the same broken claim about when the run started.
        self._write_docs(self.base)
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "run started")
        _write(self.root, "crux/scripts/target.py", "changed\n")
        self._set_base("b" * 40)
        code, payload = self._check()
        self.assertEqual(code, 1, payload)
        self.assertIn("base_commit", payload["error"])

    def test_an_uncommitted_snapshot_passes(self):
        # THE NO-CLAIM LANE. The snapshot has never been committed, so there is no
        # record to compare against. Refusing here would refuse every honest run
        # before its first commit — a false positive, not a catch.
        self._write_docs(self.base)
        _write(self.root, "crux/scripts/target.py", "changed\n")
        code, payload = self._check()
        self.assertEqual(code, 0, payload)
        self.assertIn("crux/scripts/target.py", payload["changed"])

    def test_a_committed_snapshot_whose_value_agrees_passes(self):
        self._write_docs(self.base)
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "run started")
        _write(self.root, "crux/scripts/target.py", "changed\n")
        code, payload = self._check()
        self.assertEqual(code, 0, payload)

    def test_a_commit_boundary_acquired_after_the_fact_is_refused(self):
        # A run that started outside a git work tree records `base_commit: null` and
        # cannot archive as completed. Supplying a value afterwards would manufacture
        # the boundary it never had, so a committed null and "no committed record"
        # must not be conflated.
        self._write_docs(self.base)
        self._set_base(None)
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "run started outside a work tree")
        self._set_base(self.base)
        code, payload = self._check()
        self.assertEqual(code, 1, payload)
        self.assertIn("base_commit", payload["error"])


@unittest.skipUnless(HAVE and HAVE_GIT, "check-blast-radius.py, PyYAML or git unavailable")
class CrossFrameHostingTests(unittest.TestCase):
    """The pin and the diff must be drawn from the SAME repository.

    `changed_paths` already refuses a `--repo-root` below the work-tree top, because a
    diff and a declaration from two frames cannot be compared. The pin needed the same
    care: `base_commit_pin` reads the committed record through the snapshot's own
    directory, so a snapshot hosted in a DECOY repository is pinned against a record
    that repository controls. It agrees with itself, and the gate still drew its diff
    from the real tree — from the advanced base, which is empty.

    These tests build both repositories and assert the refusal AND its reason: the
    decoy's pin would have agreed, so the frame guard is what closes this and not the
    pin."""

    def setUp(self):
        self._real = tempfile.TemporaryDirectory()
        self._decoy = tempfile.TemporaryDirectory()
        self.root = Path(self._real.name).resolve()
        self.decoy = Path(self._decoy.name).resolve()
        self.addCleanup(self._real.cleanup)
        self.addCleanup(self._decoy.cleanup)

        # The real repository: a base commit, then an OVERSHOOT committed on top of it.
        _git(self.root, "init", "-q", "-b", "main")
        _write(self.root, "crux/scripts/target.py", "original\n")
        _write(self.root, "crux/scripts/other.py", "original\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "base")
        self.base = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()
        _write(self.root, "crux/scripts/other.py", "overshoot\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "overshoot committed")
        self.overshot_head = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()

        # The book stays in the real tree, so the frozen-hash binding is genuine.
        import yaml as _y
        self.bdoc = {"format_version": "1", "id": "PB-9104", "cycle_kind": "patch",
                     "blast_radius": ["crux/scripts/target.py"]}
        self.book = self.root / "bionic/promptbooks/active/PB-9104-x.yaml"
        self.book.parent.mkdir(parents=True, exist_ok=True)
        self.book.write_text(_y.safe_dump(self.bdoc))

        # The DECOY repository hosts the snapshot, with `base_commit` already advanced
        # to the overshot head and COMMITTED at that value — so the decoy's own record
        # agrees with what is on disk.
        self.rdoc = {"format_version": "1", "run_id": "RUN-001", "book_id": "PB-9104",
                     "book_content_hash": _cbr.compute_book_hash(self.bdoc),
                     "base_commit": self.overshot_head}
        _git(self.decoy, "init", "-q", "-b", "main")
        self.run = self.decoy / "bionic/promptbooks/runs/PB-9104-x/run-RUN-001.yaml"
        self.run.parent.mkdir(parents=True, exist_ok=True)
        self.run.write_text(_y.safe_dump(self.rdoc))
        _git(self.decoy, "add", "-A")
        _git(self.decoy, "commit", "-q", "-m", "snapshot hosted in a decoy repo")

    def test_a_snapshot_hosted_in_another_repo_is_refused(self):
        with self.assertRaises(_cbr.CapabilityError) as cm:
            _cbr.check(self.book, self.run, self.root, "bionic")
        message = str(cm.exception)
        self.assertIn("outside --repo-root", message)
        self.assertIn("run", message)

    def test_the_decoy_repos_pin_would_have_agreed(self):
        # Pins the REASON the guard is the closure rather than the pin. The decoy repo
        # holds a committed record equal to the value on disk, so `divergence` reports
        # nothing — the pin is neutralized, not triggered.
        self.assertIsNone(_PIN.divergence(self.rdoc, self.run))

    def test_a_book_hosted_in_another_repo_is_refused_too(self):
        # Same frame, other document: a declaration read from outside the tree whose
        # diff is being proved is a declaration about a different repository.
        import yaml as _y
        decoy_book = self.decoy / "bionic/promptbooks/active/PB-9104-x.yaml"
        decoy_book.parent.mkdir(parents=True, exist_ok=True)
        decoy_book.write_text(_y.safe_dump(self.bdoc))
        in_frame_run = self.root / "bionic/promptbooks/runs/PB-9104-x/run-RUN-001.yaml"
        in_frame_run.parent.mkdir(parents=True, exist_ok=True)
        in_frame_run.write_text(_y.safe_dump(self.rdoc))
        with self.assertRaises(_cbr.CapabilityError) as cm:
            _cbr.check(decoy_book, in_frame_run, self.root, "bionic")
        self.assertIn("book", str(cm.exception))

    def test_the_same_run_inside_the_frame_still_reaches_a_verdict(self):
        # The positive control, and what the decoy bought. In frame, with the honest
        # base, the gate sees the overshoot and reports it — an exit-1 DOCUMENT verdict.
        # The decoy's only purpose was to make `base_commit: <overshot head>` survive
        # the pin, which turns that same diff empty.
        import yaml as _y
        in_frame = self.root / "bionic/promptbooks/runs/PB-9104-x/run-RUN-001.yaml"
        in_frame.parent.mkdir(parents=True, exist_ok=True)
        in_frame.write_text(_y.safe_dump({**self.rdoc, "base_commit": self.base}))
        code, payload = _cbr.check(self.book, in_frame, self.root, "bionic")
        self.assertEqual(code, 1, payload)
        self.assertIn("crux/scripts/other.py", payload["undeclared"])

        # And with the advanced base, in frame, the diff really is empty — so nothing
        # but the frame guard stood between the decoy and a clean pass.
        in_frame.write_text(_y.safe_dump(self.rdoc))
        code, payload = _cbr.check(self.book, in_frame, self.root, "bionic")
        self.assertEqual(code, 0, payload)


@unittest.skipUnless(HAVE and HAVE_GIT, "check-blast-radius.py, PyYAML or git unavailable")
class ExitLaneTests(unittest.TestCase):
    """The three exit lanes must stay distinguishable through the real CLI: a
    caller that conflates exit 1 with exit 2 would wave a real failure through."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name).resolve()
        _git(self.root, "init", "-q", "-b", "main")
        _write(self.root, "a.py")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "base")
        self.base = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"],
                                   capture_output=True, text=True, check=True).stdout.strip()
        import yaml as _y
        self.book = self.root / "bionic/promptbooks/active/PB-0001-x.yaml"
        self.run = self.root / "bionic/promptbooks/runs/PB-0001-x/run-RUN-001.yaml"
        self.book.parent.mkdir(parents=True, exist_ok=True)
        self.run.parent.mkdir(parents=True, exist_ok=True)
        bdoc = {"format_version": "1", "id": "PB-1", "cycle_kind": "patch",
                "blast_radius": ["a.py"]}
        self.book.write_text(_y.safe_dump(bdoc))
        self.run.write_text(_y.safe_dump(
            {"format_version": "1", "run_id": "RUN-001", "book_id": "PB-1",
             "book_content_hash": _cbr.compute_book_hash(bdoc),
             "base_commit": self.base}))
        self.addCleanup(self._td.cleanup)

    def _cli(self, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--book", str(self.book), "--run", str(self.run),
             "--repo-root", str(self.root), *extra],
            capture_output=True, text=True)

    def test_clean_run_exits_zero_with_json_on_stdout(self):
        _write(self.root, "a.py", "changed\n")
        proc = self._cli()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["clean"])

    def test_findings_exit_one_with_json_on_stdout(self):
        _write(self.root, "b.py", "new\n")
        proc = self._cli()
        self.assertEqual(proc.returncode, 1)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["clean"])
        self.assertEqual(payload["undeclared"], ["b.py"])

    def test_capability_error_exits_two_with_empty_stdout(self):
        with tempfile.TemporaryDirectory() as td:
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--book", str(self.book), "--run", str(self.run),
                 "--repo-root", td],
                capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertTrue(proc.stderr.strip())


if __name__ == "__main__":
    unittest.main()
