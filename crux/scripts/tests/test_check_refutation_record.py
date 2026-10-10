"""`check-refutation-record.py`: the read-only pre-commit check of a refutation record.

Each test builds a temporary repository (`_council_gate_support.Env`) holding a committed council
record and a refutation record, and drives the checker through its command line. Each test names
the slip it refuses. Nothing reads the documentation tree.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _council_gate_support as sup  # noqa: E402

SCRIPT = sup.SCRIPTS / "check-refutation-record.py"


class _Check(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name).resolve()
        self.env = sup.Env(self.tmp / "repo")
        self.repo = self.env.root
        cdoc = self.env.council_doc(round=2, written_at=sup.ts(2),
                                    decisions=("REQUEST_CHANGES", "APPROVE", "APPROVE"),
                                    findings={"openai_top": [("F1", "Correctness", True, "blocking")]})
        self.cpath = self.env.write("council-r2.json", cdoc, commit=True)
        self.cdoc = cdoc

    def refutation(self, **kw) -> Path:
        kw.setdefault("written_at", sup.ts(5))
        doc = self.env.refutation_doc(self.cpath, **kw)
        return self.env.write("refutation-r2.json", doc, commit=False)

    def run_check(self, record: Path | str, cwd: Path | None = None, run: Path | None = None):
        return subprocess.run([sys.executable, str(SCRIPT), str(run or self.env.run_path),
                               "--record", str(record)],
                              capture_output=True, text=True, env=sup.scrubbed_env(),
                              cwd=str(cwd or self.repo))

    def codes(self, r) -> list[str]:
        return [f["code"] for f in json.loads(r.stdout)["findings"]]


class CheckRefutationRecordTests(_Check):
    def test_valid_record_passes(self):
        """Mutation: any check that rejects a valid record turns this red."""
        r = self.run_check(self.refutation())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["status"], "clean")
        self.assertEqual(out["council_record"], self.env.rel(self.cpath))

    def test_schema_invalid_record(self):
        """Mutation: dropping the schema check."""
        path = self.refutation()
        doc = json.loads(path.read_text())
        del doc["recorder_role"]
        path.write_text(json.dumps(doc))
        r = self.run_check(path)
        self.assertEqual(r.returncode, 1)
        self.assertIn("schema", self.codes(r))

    def test_unparseable_record(self):
        path = self.repo / "docs" / "bad.json"
        path.write_text("{not json")
        r = self.run_check(path)
        self.assertEqual(r.returncode, 1)
        self.assertIn("unparseable", self.codes(r))

    def test_council_record_uncommitted(self):
        """Mutation: not requiring HEAD, index and working tree to hold the same bytes."""
        self.cpath.write_text(self.cpath.read_text() + " ")
        path = self.refutation()  # names the edited bytes, so only the cleanliness can fail
        r = self.run_check(path)
        self.assertEqual(r.returncode, 1)
        self.assertIn("council-record-not-committed", self.codes(r))

    def test_council_record_sha_mismatch(self):
        """Mutation: not comparing the named sha256."""
        path = self.refutation()
        doc = json.loads(path.read_text())
        doc["council_record"]["sha256"] = "0" * 64
        path.write_text(json.dumps(doc))
        r = self.run_check(path)
        self.assertEqual(r.returncode, 1)
        self.assertIn("council-record-hash-mismatch", self.codes(r))

    def test_stamp_equal_to_council_record(self):
        """Mutation: using >= instead of > on the stamps."""
        r = self.run_check(self.refutation(written_at=self.cdoc["written_at"]))
        self.assertEqual(r.returncode, 1)
        self.assertIn("stamp-not-after-council-record", self.codes(r))

    def test_stamp_earlier_than_council_record(self):
        r = self.run_check(self.refutation(written_at=sup.ts(1)))
        self.assertEqual(r.returncode, 1)
        self.assertIn("stamp-not-after-council-record", self.codes(r))

    def test_wrong_run_binding(self):
        """Mutation: not comparing the record's run binding to the run snapshot."""
        path = self.refutation()
        doc = json.loads(path.read_text())
        doc["run_id"] = "RUN-002"
        path.write_text(json.dumps(doc))
        r = self.run_check(path)
        self.assertEqual(r.returncode, 1)
        self.assertIn("run-binding", self.codes(r))

    def test_wrong_book_hash(self):
        path = self.refutation()
        doc = json.loads(path.read_text())
        doc["book"]["content_hash"] = "sha256:" + "1" * 64
        path.write_text(json.dumps(doc))
        r = self.run_check(path)
        self.assertEqual(r.returncode, 1)
        self.assertIn("run-binding", self.codes(r))

    def test_council_record_other_module(self):
        """Mutation: not comparing the council record's module to the refutation's."""
        r = self.run_check(self.refutation(module_tag="adr-2"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("module-mismatch", self.codes(r))

    def test_symlinked_record_refused(self):
        """Mutation: following a symlinked refutation path."""
        real = self.refutation()
        link = real.parent / "link.json"
        link.symlink_to(real)
        r = self.run_check(link)
        self.assertEqual(r.returncode, 1)
        self.assertIn("record-path-refused", self.codes(r))

    def test_symlinked_council_record_refused(self):
        """Mutation: reading a council record that is a symlink."""
        path = self.refutation()
        moved = self.cpath.parent / "council-real.json"
        self.cpath.rename(moved)
        self.cpath.symlink_to(moved)
        r = self.run_check(path)
        self.assertEqual(r.returncode, 1)
        self.assertIn("council-record-not-committed", self.codes(r))

    def test_outside_repository_is_environment_error(self):
        """Mutation: judging a record when no repository holds the run."""
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "run-RUN-001.yaml").write_text("run_id: RUN-001\n")
        rec = outside / "r.json"
        rec.write_text("{}")
        r = self.run_check(rec, cwd=outside, run=outside / "run-RUN-001.yaml")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertTrue(r.stderr.strip())

    def test_committed_refutation_is_noted_not_failed(self):
        """A committed refutation record is final; the checker says so and still passes."""
        path = self.refutation()
        self.env.commit_records(path)
        r = self.run_check(path)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertTrue(any("final" in w for w in json.loads(r.stdout)["warnings"]))

    def test_checker_writes_nothing(self):
        """Mutation: any write or commit by the checker."""
        path = self.refutation()
        before = sup.git(self.repo, "status", "--porcelain")
        self.run_check(path)
        self.assertEqual(sup.git(self.repo, "status", "--porcelain"), before)


class EnvironmentFaultTests(_Check):
    def test_unreadable_git_state_is_an_environment_fault_not_a_traceback(self):
        """A council-records read error exits 2 with a message, never a traceback on exit 1."""
        import importlib.util
        from unittest import mock
        import council_records as cr
        spec = importlib.util.spec_from_file_location("check_refutation_record", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        record = self.refutation()
        with mock.patch.object(mod.cr, "path_state", side_effect=cr.RecordError("x", "unreadable state")), \
                mock.patch("sys.stdout"), mock.patch("sys.stderr"), \
                mock.patch("os.getcwd", return_value=str(self.repo)):
            code = mod.main([str(self.env.run_path), "--record", str(record)])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
