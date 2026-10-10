"""The read-only order audit for approvals issued under profile 3.

Profile 3 replays council evidence in stamp order. `audit-order` rebuilds the gate scope of each
profile-3 binding, orders its records by the commits that introduced them at the binding's proof
commit, and reports whether the two orders agree. It never refuses, writes or changes a replay.

* D1: PB-0141 RUN-001 (the real tree) reports `agree`. Mutation: audit at HEAD instead of the proof
  commit, or compare against nothing -> the outcome or the proof commit differs.
* D2: a profile-3 close whose records were committed in the opposite order to their stamps reports
  `contradict`, while the shared historical verifier still validates the binding. Mutation: let the
  audit change a replay -> the verifier refuses or the outcome is not `contradict`.
* D3: a shallow clone reports `uncomparable` with `shallow-or-unavailable`.
* D4: a record modified after its commit reports `uncomparable` with `invalid-path-history`.
* No audit name appears in the gate-contract bytes of profile 2, 3 or 4, and their digests hold.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dev_surface import require_dev_surface  # noqa: E402
import _council_gate_support as sup  # noqa: E402
import implementation_approval as ap  # noqa: E402
from test_implementation_cycles import WriterFixture  # noqa: E402

import yaml  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = sup.SCRIPTS
CLI = SCRIPTS / "implementation-decisions.py"
PB0141 = "bionic/promptbooks/runs/PB-0141-implementation-migration-pilot/run-RUN-001.yaml"
DIGESTS = {"2": "c44a85a6160a9fb5ddc6ac938ce7b15d462c9d40b2808d2db92699ad15b8f4d2",
           "3": "62a4760de1e477fb13c78f9162c49c4da620b5ace304ca72ce6fc45496d16d23",
           "4": "b1bda66c8667b1c9a8bbaa39c47d58fd55245b718e6d025f71f7221d47035167"}
AUDIT_NAMES = (b"audit_order", b"audit-order", b"AuditOrder", b"order_audit")

WRAPPER = '''
import runpy, sys
sys.path.insert(0, {scripts!r})
import implementation_approval as ap
import council_gate as cg
ap.CONTRACT_VERSION = "3"
cg.evaluate_gate = lambda *a, **k: cg.Verdict("pass")
sys.argv = ["advance-run.py", *sys.argv[1:]]
runpy.run_path({script!r}, run_name="__main__")
'''


def audit(root: Path, run: Path | None = None) -> tuple[int, dict, str]:
    cmd = [sys.executable, str(CLI), "audit-order", "--repo-root", str(root)]
    if run is not None:
        cmd += ["--run", str(run)]
    done = subprocess.run(cmd, capture_output=True, env=sup.scrubbed_env())
    out = done.stdout.decode()
    return done.returncode, (json.loads(out) if out.strip() else {}), done.stderr.decode()


class ProfileThree:
    """A real writer close that retains contract 3, built over the synthetic fixture."""

    def __init__(self, temp: Path, *, rollback: bool = False, modified: bool = False):
        self.f = f = WriterFixture(temp / "repo")
        receipt = f.receipt.relative_to(f.root).as_posix()
        if modified:
            doc = json.loads(f.receipt.read_bytes())
            f.receipt.write_text(json.dumps(doc, indent=1))
            sup.git(f.root, "add", "-A")
            sup.git(f.root, "commit", "-q", "-m", "synthetic edit of a committed council record")
        if rollback:
            # The converged record keeps its early commit but takes the later stamp; the blocking
            # record is committed after it and takes the earlier stamp. Stamp order says the
            # converged record is last; committed order says the blocking one is.
            doc = json.loads(f.receipt.read_bytes())
            earlier = json.loads(json.dumps(doc))
            earlier.update(round=1, written_at=sup.ts(1))
            earlier["seats"][0]["decision"] = "REQUEST_CHANGES"
            earlier["seats"][0]["findings"] = [{"id": "openai_top:F1", "dimension": "Correctness",
                "safety_adjacent": False, "kind": "blocking", "text": "synthetic blocker"}]
            doc.update(round=2, written_at=sup.ts(2))
            f.receipt.write_text(json.dumps(doc))
            blocker = f.council / "earlier-1.json"
            blocker.write_text(json.dumps(earlier))
            f.artifacts += "," + blocker.relative_to(f.root).as_posix()
            sup.commit_all(f.root, "synthetic rolled-back stamp")
        wrapper = temp / "close-under-profile-three.py"
        wrapper.write_text(WRAPPER.format(scripts=str(SCRIPTS), script=str(SCRIPTS / "advance-run.py")))
        done = subprocess.run([sys.executable, str(wrapper), str(f.run_path), "--outcome", "done",
            "--artifacts", f.artifacts, "--book", str(f.book_path), "--implementation-revision", f.rel],
            capture_output=True, env=sup.scrubbed_env())
        if done.returncode:
            raise AssertionError(done.stdout + done.stderr)
        sup.commit_all(f.root, "synthetic profile-three close")
        self.receipt = receipt

    def context_version(self) -> str:
        run = yaml.safe_load(self.f.run_path.read_bytes())
        ref = run["implementation_bindings"][0]["context"]
        return json.loads((self.f.root / ref["path"]).read_bytes())["contract"]["version"]

    def validates(self) -> bool:
        ap.validate_historical_implementation_binding(self.f.root, self.f.run_path, slot=self.f.slot,
            revision_path=self.f.rel, revision_sha256=ap.cr.sha256_file(self.f.path))
        return True

    def proof_commit(self) -> str:
        return ap.validate_historical_implementation_binding(self.f.root, self.f.run_path, slot=self.f.slot,
            revision_path=self.f.rel, revision_sha256=ap.cr.sha256_file(self.f.path)).proof_commit


class AuditBase(unittest.TestCase):
    def build(self, **kwargs) -> ProfileThree:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return ProfileThree(Path(temp.name), **kwargs)


class RealTree(unittest.TestCase):
    def test_d1_pb0141_run_001_agrees_at_its_proof_commit(self):
        require_dev_surface(self, REPO / PB0141, "PB-0141 RUN-001")
        code, report, err = audit(REPO, REPO / PB0141)
        self.assertEqual(code, 0, err)
        rows = report["bindings"]
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual((row["slot"], row["binding_kind"], row["outcome"]),
                         ("implementation-1", "migration", "agree"), row)
        self.assertEqual(row["run"], PB0141)
        proof = ap.validate_historical_migration_binding(REPO, REPO / PB0141, slot="implementation-1",
            batch_path="bionic/adrs/migrations/implementation-pilot-001.yaml",
            batch_sha256="2dba2caee1f625fd0f96b68f6a43cb7039eb3d5114bcc6f2cf1c678edd3973af").proof_commit
        self.assertEqual(row["proof_commit"], proof)
        self.assertEqual(report["authority"], "none")

    def test_the_whole_tree_audit_lists_the_pilot_run_and_exits_zero(self):
        require_dev_surface(self, REPO / PB0141, "PB-0141 RUN-001")
        code, report, err = audit(REPO)
        self.assertEqual(code, 0, err)
        self.assertIn(PB0141, {row["run"] for row in report["bindings"]})


class Fixtures(AuditBase):
    def test_a_profile_three_fixture_with_consistent_history_agrees(self):
        built = self.build()
        self.assertEqual(built.context_version(), "3")
        code, report, err = audit(built.f.root, built.f.run_path)
        self.assertEqual(code, 0, err)
        self.assertEqual([r["outcome"] for r in report["bindings"]], ["agree"], report)
        self.assertEqual(report["bindings"][0]["proof_commit"], built.proof_commit())

    def test_d2_a_rolled_back_clock_contradicts_while_the_approval_still_validates(self):
        built = self.build(rollback=True)
        self.assertEqual(built.context_version(), "3")
        self.assertTrue(built.validates())
        before = built.f.run_path.read_bytes()
        code, report, err = audit(built.f.root, built.f.run_path)
        self.assertEqual(code, 0, err)
        row = report["bindings"][0]
        self.assertEqual(row["outcome"], "contradict", report)
        self.assertEqual(row["proof_commit"], built.proof_commit())
        self.assertIn(built.receipt, json.dumps(row["records"]))
        self.assertEqual(built.f.run_path.read_bytes(), before)
        self.assertTrue(built.validates())
        self.assertEqual(sup.git(built.f.root, "status", "--porcelain").strip(), "")

    def test_d3_a_shallow_clone_is_uncomparable(self):
        built = self.build()
        shallow = Path(built.f.root.parent) / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth", "1", built.f.root.as_uri(), str(shallow)],
                       check=True, capture_output=True, env=sup.scrubbed_env())
        run = shallow / built.f.run_path.relative_to(built.f.root)
        code, report, err = audit(shallow, run)
        self.assertEqual(code, 0, err)
        row = report["bindings"][0]
        self.assertEqual((row["outcome"], row["reason"]), ("uncomparable", "shallow-or-unavailable"), report)

    def test_d4_a_record_modified_after_its_commit_is_uncomparable(self):
        built = self.build(modified=True)
        self.assertTrue(built.validates())
        code, report, err = audit(built.f.root, built.f.run_path)
        self.assertEqual(code, 0, err)
        row = report["bindings"][0]
        self.assertEqual((row["outcome"], row["reason"]), ("uncomparable", "invalid-path-history"), report)
        self.assertIn(built.receipt, json.dumps(row["records"]))
        self.assertTrue(built.validates())

    def test_a_profile_four_close_is_skipped_not_audited(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        f = WriterFixture(Path(temp.name) / "repo")
        done = f.advance("--implementation-revision", f.rel)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        sup.commit_all(f.root, "synthetic profile-four close")
        code, report, err = audit(f.root, f.run_path)
        self.assertEqual(code, 0, err)
        self.assertEqual(report["bindings"], [])
        self.assertEqual([(s["slot"], s["contract"]) for s in report["skipped"]],
                         [(f.slot, "4")], report)


class Invocation(unittest.TestCase):
    def test_a_directory_that_is_not_a_repository_exits_two(self):
        with tempfile.TemporaryDirectory() as empty:
            code, report, err = audit(Path(empty))
        self.assertEqual(code, 2, err)
        self.assertEqual(report, {})
        self.assertIn("audit-order: not a", err)


class NoAuditNameInAnyProfile(unittest.TestCase):
    def test_gate_contract_bytes_name_no_audit_and_keep_their_digests(self):
        for version, digest in DIGESTS.items():
            with self.subTest(version=version):
                content = ap.gate_contract_bytes(version)
                self.assertEqual(hashlib.sha256(content).hexdigest(), digest)
                for name in AUDIT_NAMES:
                    self.assertNotIn(name, content)


if __name__ == "__main__":
    unittest.main()
