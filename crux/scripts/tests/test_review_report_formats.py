"""Reviewer-report format 2: SHA-256 range reports, and the dispatch that reads both formats.

Format 1 (`reviewer-report.schema.json`) accepts a range of 7 to 40 hex digits per end and is
never widened: the policy profile that hashes it was issued with exactly those bytes. Format 2
(`reviewer-report-format-2.schema.json`) accepts 7 to 64. The new names `reviewer_report_errors`
and `load_reviewer_report` in `council_records.py` dispatch on `format_version`.

Reads only `crux/`, fixtures built in a temp directory, and (through `require_dev_surface`) the
committed reports of this repository's own runs. Needs a git that can create a SHA-256 repository
for the C1 and C2 tests; they skip with the installed version named when it cannot.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import _council_gate_support as sup  # noqa: E402
from _council_gate_support import SCRIPTS, SUBJECT, Env, commit_all, git, scrubbed_env  # noqa: E402
from _dev_surface import REPO_ROOT, require_dev_surface  # noqa: E402

import council_gate as cg  # noqa: E402
import council_records as cr  # noqa: E402
import implementation_approval as approval  # noqa: E402
import implementation_decisions as ids  # noqa: E402

WRITER = SCRIPTS / "write-review-report.py"
SCHEMAS = SCRIPTS.parent / "schemas"
FORMAT_1_SCHEMA = SCHEMAS / "reviewer-report.schema.json"
FORMAT_2_SCHEMA = SCHEMAS / "reviewer-report-format-2.schema.json"
REVIEW_GATE_PROMPT = 10  # the fixture book's independent-review gate (see test_write_review_report)
#: sha256 of reviewer-report.schema.json at the book's base_commit cfb27cd59018cb8bc91f9585335918d07c2c73e2.
FORMAT_1_SCHEMA_SHA256 = "dc4e7020f29a9d596228d86a7231c22c8ab1bbd812528f453650d5963a8f5f83"
GATE_CONTRACT_SHA256 = {
    "2": "c44a85a6160a9fb5ddc6ac938ce7b15d462c9d40b2808d2db92699ad15b8f4d2",
    "3": "62a4760de1e477fb13c78f9162c49c4da620b5ace304ca72ce6fc45496d16d23",
}


def _sha256_init(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    git(root, "init", "-q", "-b", "main", "--object-format=sha256")
    return root


def _sha256_supported() -> str | None:
    """None when this git can create a SHA-256 repository, else the reason."""
    with tempfile.TemporaryDirectory() as d:
        r = subprocess.run(["git", "init", "-q", "--object-format=sha256", d], capture_output=True,
                           text=True, env=scrubbed_env())
        version = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip()
        if r.returncode != 0:
            return f"{version} cannot create a SHA-256 repository: {r.stderr.strip()}"
        shown = subprocess.run(["git", "-C", d, "rev-parse", "--show-object-format"],
                               capture_output=True, text=True, env=scrubbed_env()).stdout.strip()
        return None if shown == "sha256" else f"{version} created a {shown!r} repository"


SKIP_SHA256 = _sha256_supported()


def run_writer(env: Env, *args: str) -> tuple[int, dict | None, str]:
    r = subprocess.run([sys.executable, str(WRITER), str(env.run_path), *args], capture_output=True,
                       text=True, cwd=str(env.root))
    line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    return r.returncode, (json.loads(line) if line else None), r.stderr


def make_env(root: Path, object_format: str) -> Env:
    if object_format == "sha256":
        with mock.patch.object(sup, "init_repo", _sha256_init):
            env = Env(root)
        shown = git(env.root, "rev-parse", "--show-object-format").strip()
        assert shown == "sha256", shown
        return env
    return Env(root)


def add_second_commit(env: Env, name: str = "docs/other.md") -> str:
    base = git(env.root, "rev-parse", "HEAD").strip()
    (env.root / name).write_text("# other\n")
    commit_all(env.root, "second")
    return base


class RangeReportEndToEnd(unittest.TestCase):
    """C1: a range report is written and gated in SHA-1 and SHA-256 repositories alike."""

    def check(self, object_format: str, id_length: int, format_version: str | None) -> None:
        with tempfile.TemporaryDirectory() as d:
            env = make_env(Path(d) / "repo", object_format)
            base = add_second_commit(env)
            self.assertEqual(len(base), id_length)
            code, out, err = run_writer(env, "--prompt", str(REVIEW_GATE_PROMPT),
                                        "--range", f"{base}..HEAD", "--verdict", "APPROVE")
            self.assertEqual(code, 0, (out, err))
            doc = json.loads((env.root / out["path"]).read_text())
            if format_version is not None:
                self.assertEqual(doc["format_version"], format_version)
            ends = doc["subject"]["range"].split("..")
            self.assertEqual([len(e) for e in ends], [id_length, id_length])
            gate = cg.classify(env.book, REVIEW_GATE_PROMPT)
            self.assertEqual(gate.cls, "independent-review", gate)
            verdict = cg.evaluate_review_gate(gate, env.load_run(), env.root, [out["path"]], "done")
            self.assertEqual(verdict.verdict, "pass", verdict)

    @unittest.skipIf(SKIP_SHA256, SKIP_SHA256)
    def test_c1_sha256_range_report_is_written_and_passes_the_review_gate(self):
        self.check("sha256", 64, "2")

    def test_c1_control_sha1_range_report_is_written_and_passes_the_review_gate(self):
        self.check("sha1", 40, None)


@unittest.skipIf(SKIP_SHA256, SKIP_SHA256)
class ResultReviewCoverage(unittest.TestCase):
    """C2: an implementation result whose review is a format-2 SHA-256 range report."""

    def test_c2_sha256_range_report_covers_an_implementation_result(self):
        with tempfile.TemporaryDirectory() as d:
            env = make_env(Path(d) / "repo", "sha256")
            base = git(env.root, "rev-parse", "HEAD").strip()
            (env.root / "decision.md").write_text("# decision\n")
            (env.root / "widget.txt").write_text("widget\n")
            commit_all(env.root, "delivery")
            end = git(env.root, "rev-parse", "HEAD").strip()
            doc = env.report_doc(prompt=13, range_=f"{base}..{end}")
            doc["format_version"] = "2"
            report = env.run_dir / "reviews" / "r.json"
            report.parent.mkdir()
            report.write_text(json.dumps(doc))
            commit_all(env.root, "report")
            ref = lambda p: {"path": env.rel(p), "sha256": cr.sha256_file(p)}  # noqa: E731
            result = {"decision": ref(env.root / "decision.md"), "annotations": [],
                      "sources": [{"path": "widget.txt", "delivered": ids.source_hash(env.root, end, "widget.txt")}],
                      "reviews": [ref(report)]}
            ids._review_coverage(env.root, result)  # raises Refused when the report is not accepted

    def test_c2_format_1_report_is_still_read(self):
        with tempfile.TemporaryDirectory() as d:
            env = make_env(Path(d) / "repo", "sha1")
            doc = env.report_doc(prompt=13)
            report = env.run_dir / "reviews" / "r.json"
            report.parent.mkdir()
            report.write_text(json.dumps(doc))
            commit_all(env.root, "report")
            ref = lambda p: {"path": env.rel(p), "sha256": cr.sha256_file(p)}  # noqa: E731
            subject = env.root / SUBJECT
            result = {"decision": ref(subject), "annotations": [], "sources": [], "reviews": [ref(report)]}
            ids._review_coverage(env.root, result)


class CommittedReports(unittest.TestCase):
    def test_c2_every_committed_report_validates_through_the_dispatcher(self):
        runs = REPO_ROOT / "bionic" / "promptbooks" / "runs"
        require_dev_surface(self, runs, "bionic/promptbooks/runs")
        reports = sorted(runs.glob("*/reviews/*.json"))
        self.assertTrue(reports, "no committed reviewer report found: the test would be vacuous")
        # The dispatcher's format-2 entry is the test's own schema. Within this test, only this
        # pin catches a mis-pointed table entry: a valid report passes any schema at least as
        # permissive. `FormatDispatch` covers the same mutants behaviourally.
        self.assertEqual(Path(cr.REVIEWER_REPORT_SCHEMAS["2"]).resolve(), FORMAT_2_SCHEMA.resolve())
        seen = set()
        for path in reports:
            doc = json.loads(path.read_text())
            version = doc.get("format_version")
            self.assertIn(version, {"1", "2"}, f"{path}: unknown or missing format_version")
            seen.add(version)
            if version == "1":
                # A format-1 report still validates against the format-1 schema directly, not only
                # through the dispatcher.
                self.assertEqual(cr.schema_errors(doc, "reviewer-report"), [], path)
            else:
                # A format-2 report validates against its own schema directly, not only through the
                # dispatcher.
                vp = cr._validator()
                errors: list[dict] = []
                vp.validate(doc, vp.load_schema(FORMAT_2_SCHEMA), "#", "#", errors,
                            "reviewer-report")
                self.assertEqual(errors, [], path)
            self.assertEqual(cr.reviewer_report_errors(doc), [], path)
            self.assertEqual(cr.load_reviewer_report(path).record_type, "reviewer-report", path)
        self.assertEqual(seen, {"1", "2"}, "the committed reports no longer exercise both formats")


def sha256_range_report() -> dict:
    doc = sup.fixture("reviewer-report-paths.json")
    doc["subject"] = {"form": "commit-range", "range": "a" * 64 + ".." + "b" * 64}
    return doc


class ProfileThreeUnchanged(unittest.TestCase):
    def test_c3_format_1_schema_still_refuses_a_64_hex_range(self):
        errors = cr.schema_errors(sha256_range_report(), "reviewer-report")
        self.assertTrue(any(e.startswith("#/subject/range") for e in errors), errors)

    def test_c3_format_1_schema_bytes_are_those_of_the_base_commit(self):
        self.assertEqual(hashlib.sha256(FORMAT_1_SCHEMA.read_bytes()).hexdigest(), FORMAT_1_SCHEMA_SHA256)

    def test_c3_issued_gate_contract_digests_are_unchanged(self):
        for version, digest in GATE_CONTRACT_SHA256.items():
            with self.subTest(version=version):
                self.assertEqual(hashlib.sha256(approval.gate_contract_bytes(version)).hexdigest(), digest)

    def test_c3_the_format_2_path_is_in_no_issued_contract(self):
        for version in GATE_CONTRACT_SHA256:
            with self.subTest(version=version):
                contract = approval.production_contract_bytes(version)
                self.assertNotIn(b"reviewer-report-format-2", contract)
                self.assertNotIn(b"reviewer_report_errors", contract)

    def test_c3_profile_four_binds_no_format_2_schema(self):
        if "4" not in approval.SUPPORTED_PROFILES:
            self.skipTest("profile 4 is not in implementation_approval.SUPPORTED_PROFILES in this "
                          "tree; the dev-lead enables this test at integration")
        contract = approval.production_contract_bytes("4")
        self.assertNotIn(b"reviewer-report-format-2", contract)
        self.assertNotIn(b"reviewer_report_errors", contract)
        before = hashlib.sha256(approval.gate_contract_bytes("4")).hexdigest()
        original = FORMAT_2_SCHEMA.read_bytes()
        try:
            FORMAT_2_SCHEMA.write_bytes(original + b"\n")
            after = hashlib.sha256(approval.gate_contract_bytes("4")).hexdigest()
        finally:
            FORMAT_2_SCHEMA.write_bytes(original)
        self.assertEqual(before, after, "editing the format-2 schema moved the profile-4 digest")


class FormatDispatch(unittest.TestCase):
    def doc(self, version: str | None, rng: str | None = None) -> dict:
        doc = sup.fixture("reviewer-report-paths.json")
        if rng is not None:
            doc["subject"] = {"form": "commit-range", "range": rng}
        if version is None:
            doc.pop("format_version")
        else:
            doc["format_version"] = version
        return doc

    def test_c4_format_1_schema_refuses_a_format_2_report_with_a_sha256_range(self):
        doc = self.doc("2", "a" * 64 + ".." + "b" * 64)
        self.assertTrue(cr.schema_errors(doc, "reviewer-report"))
        self.assertEqual(cr.reviewer_report_errors(doc), [])  # control: the dispatcher accepts it

    def test_c4_format_2_refuses_a_non_hex_or_overlong_range(self):
        for bad in ("A" * 40 + ".." + "b" * 40, "main..HEAD", "a" * 65 + ".." + "b" * 40, "a" * 6 + ".." + "b" * 40):
            with self.subTest(bad=bad):
                self.assertTrue(cr.reviewer_report_errors(self.doc("2", bad)), bad)

    def test_c4_format_1_through_the_dispatcher_equals_the_format_1_schema(self):
        for rng in (None, "a" * 40 + ".." + "b" * 40, "a" * 64 + ".." + "b" * 64):
            doc = self.doc("1", rng)
            self.assertEqual(cr.reviewer_report_errors(doc), cr.schema_errors(doc, "reviewer-report"))

    def test_c4_missing_or_unknown_format_version_is_refused(self):
        for version in (None, "3", "", 2, "1.0"):
            with self.subTest(version=version):
                errors = cr.reviewer_report_errors(self.doc(version))
                self.assertTrue(any("format_version" in e for e in errors), errors)

    def test_c4_a_non_object_is_refused(self):
        self.assertTrue(cr.reviewer_report_errors([]))

    def test_c4_format_2_refuses_an_unknown_property(self):
        doc = self.doc("2")
        doc["extra"] = 1
        self.assertTrue(cr.reviewer_report_errors(doc))

    def test_format_2_schema_uses_only_engine_keywords(self):
        vp = cr._validator()
        schema = json.loads(FORMAT_2_SCHEMA.read_text())

        def keys(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    yield k
                    if k not in ("properties",):
                        yield from keys(v)
                    else:
                        for sub in v.values():
                            yield from keys(sub)
            elif isinstance(node, list):
                for v in node:
                    yield from keys(v)

        allowed = set(vp.IMPLEMENTED_KEYWORDS) | {"$schema", "title", "description", "$comment"}
        self.assertEqual(sorted(set(keys(schema)) - allowed), [])
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(schema["properties"]["format_version"], {"const": "2"})

    def test_load_reviewer_report_loads_both_formats_and_refuses_a_bad_one(self):
        with tempfile.TemporaryDirectory() as d:
            for name, doc in (("one.json", self.doc("1")), ("two.json", self.doc("2", "a" * 64 + ".." + "b" * 64))):
                p = Path(d) / name
                p.write_text(json.dumps(doc))
                rec = cr.load_reviewer_report(p)
                self.assertEqual((rec.record_type, rec.doc), ("reviewer-report", doc))
            bad = Path(d) / "bad.json"
            bad.write_text(json.dumps(self.doc("2", "main..HEAD")))
            with self.assertRaises(cr.InvalidRecord):
                cr.load_reviewer_report(bad)
            other = Path(d) / "other.json"
            other.write_text(json.dumps({"record_type": "something-else"}))
            self.assertIsNone(cr.load_reviewer_report(other))


# --- C5: the reader inventory -------------------------------------------------------------------

#: Modules allowed to call `load_record` or `schema_errors` on a reviewer report directly: the
#: module that owns them, and the frozen policy kernels profile 2 and 3 reach.
FROZEN = {"council_records.py", "council_records_v2.py", "council_history_v2.py", "council_history_v3.py",
          "council_schema_v2.py"}


def direct_readers(source: str) -> list[int]:
    """Lines calling `load_record`, or `schema_errors` on the reviewer-report type, in `source`."""
    lines = []
    tree = ast.parse(source)
    # A module-level `RECORD_TYPE` names the record type its module reads; only the
    # reviewer-report value makes a `schema_errors(doc, RECORD_TYPE)` call a report reader.
    record_type = next((n.value.value for n in tree.body if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == "RECORD_TYPE" for t in n.targets)
                        and isinstance(n.value, ast.Constant)), None)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name == "load_record":
            lines.append(node.lineno)
        elif name == "schema_errors" and any(
                (isinstance(a, ast.Constant) and a.value == "reviewer-report")
                or (isinstance(a, ast.Name) and a.id == "RECORD_TYPE" and record_type in (None, "reviewer-report"))
                for a in node.args):
            lines.append(node.lineno)
    return lines


class ReaderInventory(unittest.TestCase):
    READERS = {
        "crux/scripts/write-review-report.py": "cr.reviewer_report_errors",
        "crux/scripts/council_gate.py": "cr.load_reviewer_report",
        "crux/scripts/implementation_decisions.py": "cr.reviewer_report_errors",
        "tools/exercise-implementation-cycles.py": "cr.load_reviewer_report",
    }

    def sources(self):
        for base in (REPO_ROOT / "crux" / "scripts", REPO_ROOT / "tools"):
            if not base.exists():
                continue
            for path in sorted(base.glob("*.py")):
                if path.name not in FROZEN:
                    yield path

    def test_c5_no_reader_bypasses_the_dispatcher(self):
        tools = REPO_ROOT / "tools"
        require_dev_surface(self, tools, "tools/")
        scanned = 0
        for path in self.sources():
            scanned += 1
            self.assertEqual(direct_readers(path.read_text(encoding="utf-8")), [], path.name)
        self.assertGreater(scanned, 20)

    def test_c5_every_known_reader_uses_the_dispatcher(self):
        require_dev_surface(self, REPO_ROOT / "tools", "tools/")
        for rel, call in self.READERS.items():
            with self.subTest(reader=rel):
                self.assertIn(call + "(", (REPO_ROOT / rel).read_text(encoding="utf-8"))

    def test_c5_positive_control_a_reverted_reader_is_flagged(self):
        require_dev_surface(self, REPO_ROOT / "tools", "tools/")
        for rel, call in self.READERS.items():
            with self.subTest(reader=rel):
                source = (REPO_ROOT / rel).read_text(encoding="utf-8")
                self.assertEqual(direct_readers(source), [])
                reverted = source.replace(
                    call + "(", "cr.load_record(" if "load_reviewer_report" in call
                    else 'cr.schema_errors("reviewer-report", ')
                self.assertNotEqual(reverted, source)
                self.assertTrue(direct_readers(reverted), rel)


if __name__ == "__main__":
    unittest.main()
