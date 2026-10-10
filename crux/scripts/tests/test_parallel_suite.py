"""Self-tests for `parallel_suite.py`, the parallel plugin-suite driver.

The driver runs the suite across spawned worker processes and fails closed on
any ledger discrepancy. These tests cover four layers:

- the recorder and the summary, run in-process against `TextTestRunner`;
- `reconcile`, the pure ledger, with one synthetic case per fail-closed branch;
- the cut into units, the measured weights and the bin assignment;
- the timings file and its writer;
- the affinity list;
- the whole driver, run in subprocesses with `--workers 2` over synthetic trees
  built in temporary directories.

Every tree is synthetic and lives under a temporary directory. The one read of
the real tree is the affinity-completeness check over this directory, which
the staged artifact carries.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import venv
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
DRIVER = HERE / "parallel_suite.py"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import parallel_suite as ps  # noqa: E402

# A driver run over a synthetic tree takes a few seconds. The deadlines below
# only bound a hung run; no assertion reads elapsed time.
RUN_TIMEOUT = 300
PROCESS_DEADLINE = 120

_RAN_RE = re.compile(r"^Ran (\d+) tests? in \d+\.\d{3}s$", re.M)


def _mask_time(text: str) -> str:
    return re.sub(r"(Ran \d+ tests?) in \d+\.\d{3}s", r"\1 in T", text)


# ---------------------------------------------------------------------------
# The recorder and the summary
# ---------------------------------------------------------------------------


def _outcome_cases():
    """TestCase classes, one per outcome class. Built in a function so
    discovery of this module never collects them."""

    class Pass(unittest.TestCase):
        def test_ok(self):
            pass

    class Fail(unittest.TestCase):
        def test_fail(self):
            self.assertEqual(1, 2)

    class Error(unittest.TestCase):
        def test_error(self):
            raise RuntimeError("boom")

    class Skip(unittest.TestCase):
        def test_skip(self):
            self.skipTest("skipped for a reason")

    class XFail(unittest.TestCase):
        @unittest.expectedFailure
        def test_xfail(self):
            self.assertEqual(1, 2)

    class UXSuccess(unittest.TestCase):
        @unittest.expectedFailure
        def test_uxsuccess(self):
            pass

    class SubFail(unittest.TestCase):
        def test_sub(self):
            for i in range(2):
                with self.subTest(i=i):
                    self.assertEqual(i, 0)

    class SubSkipAll(unittest.TestCase):
        def test_sub_skip(self):
            for i in range(2):
                with self.subTest(i=i):
                    self.skipTest("every subtest skips")

    class ClassSkip(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            raise unittest.SkipTest("class skipped")

        def test_never(self):
            pass

    class ClassError(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            raise RuntimeError("class setup broke")

        def test_never(self):
            pass

    class Documented(unittest.TestCase):
        def test_doc(self):
            """First docstring line shows in the description."""
            self.fail("documented failure")

    return {c.__name__: c for c in (Pass, Fail, Error, Skip, XFail, UXSuccess, SubFail,
                                    SubSkipAll, ClassSkip, ClassError, Documented)}


def _children(classes):
    loader = unittest.TestLoader()
    return [loader.loadTestsFromTestCase(c) for c in classes]


def _driver_text(classes):
    """Run `classes` through the driver's own path; return (summary text, exit code, records)."""
    children = _children(classes)
    manifest = ps.build_manifest(unittest.TestSuite(_children(classes)))
    positions = tuple(range(len(children)))
    bins = [ps.Bin(0, len(children), positions)]
    with mock.patch.object(sys, "stderr", io.StringIO()):
        result = ps.run_positions(children, positions, 0)
    payload = ps.result_payload(result, 0, manifest, list(sys.path), positions)
    summary = ps.reconcile(manifest, bins, {0: payload}, list(sys.path))
    return ps.format_summary(summary, 0.0), ps.exit_code(summary), summary.records


def _runner_text(classes):
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=1).run(
        unittest.TestSuite(_children(classes)))
    out = stream.getvalue()
    # Drop the progress line of dots; the driver prints no progress.
    return out[out.index("\n"):], 0 if result.wasSuccessful() else 1


class RecorderSummaryTests(unittest.TestCase):
    """The recorder records every outcome class, and the summary and exit
    code match TextTestRunner's for the same tests."""

    def setUp(self):
        self.cases = _outcome_cases()

    def _assert_same_as_runner(self, names):
        classes = [self.cases[n] for n in names]
        text, code, _ = _driver_text(classes)
        want_text, want_code = _runner_text(classes)
        self.assertEqual(_mask_time(text), _mask_time(want_text))
        self.assertEqual(code, want_code)
        return text

    def test_every_outcome_class_is_recorded(self):
        _, _, records = _driver_text(list(self.cases.values()))
        seen = {(r["kind"], r["outcome"]) for r in records}
        for pair in [("test", "ok"), ("test", "fail"), ("test", "error"), ("test", "skip"),
                     ("test", "xfail"), ("test", "uxsuccess"), ("test", "none"),
                     ("subtest", "ok"), ("subtest", "fail"), ("subtest", "skip"),
                     ("holder", "skip"), ("holder", "error")]:
            self.assertIn(pair, seen)
        holders = sorted(r["id"] for r in records if r["kind"] == "holder")
        self.assertEqual(len(holders), 2)
        self.assertTrue(holders[0].startswith("setUpClass ("))

    def test_summary_all_pass_matches_runner(self):
        text = self._assert_same_as_runner(["Pass"])
        self.assertTrue(text.endswith("\nOK\n"))

    def test_summary_pass_and_skip_matches_runner(self):
        text = self._assert_same_as_runner(["Pass", "Skip", "ClassSkip", "SubSkipAll"])
        self.assertIn("OK (skipped=", text)

    def test_summary_every_outcome_matches_runner(self):
        text = self._assert_same_as_runner(sorted(self.cases))
        self.assertIn("FAILED (failures=", text)
        self.assertIn("unexpected successes=1", text)
        self.assertIn("First docstring line shows in the description.", text)

    def test_an_unexpected_success_alone_fails_the_exit_code(self):
        text = self._assert_same_as_runner(["UXSuccess"])
        self.assertIn("FAILED (unexpected successes=1)", text)
        self.assertEqual(_driver_text([self.cases["UXSuccess"]])[1], 1)

    def test_expected_failure_alone_passes(self):
        text = self._assert_same_as_runner(["XFail"])
        self.assertIn("OK (expected failures=1)", text)


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------

MANIFEST = [
    ("mod_a", ("mod_a.A.test_1", "mod_a.A.test_2", "mod_a.C.test_1", "mod_a.AB.test_1")),
    ("mod_b", ("mod_b.B.test_1",)),
]
BINS = [ps.Bin(0, 4, (0,)), ps.Bin(1, 1, (1,))]
SYS_PATH = ["/cwd", "/stdlib"]


def _rec(test_id, kind="test", outcome="ok", reason="", params=None):
    return {"id": test_id, "kind": kind, "outcome": outcome, "reason": reason,
            "params": params, "duration": None, "bin": None, "pid": 1, "detail": None}


def _payload(index, records, tests_run=None, **extra):
    payload = {
        "bin": index,
        "manifest": [tuple(entry) for entry in MANIFEST],
        "sys_path": list(SYS_PATH),
        "records": records,
        "tests_run": sum(1 for r in records if r["kind"] == "test") if tests_run is None else tests_run,
        "failures": [],
        "errors": [],
        "skipped": 0,
        "expected_failures": 0,
        "unexpected": [],
    }
    payload.update(extra)
    return payload


def _clean_bin0():
    return [_rec("mod_a.A.test_1"), _rec("mod_a.A.test_2", outcome="fail"),
            _rec("mod_a.C.test_1"), _rec("mod_a.AB.test_1")]


def _clean_payloads():
    return {
        0: _payload(0, _clean_bin0(), failures=[("mod_a.A.test_2", "Traceback", 0)]),
        1: _payload(1, [_rec("mod_b.B.test_1", outcome="skip", reason="why")], skipped=1),
    }


class LedgerTests(unittest.TestCase):
    """`reconcile` accepts a consistent ledger and fails closed, naming
    the bin and the id, on each discrepancy branch."""

    def _refused(self, payloads, manifest=MANIFEST, bins=BINS, sys_path=SYS_PATH):
        with self.assertRaises(ps.LedgerError) as caught:
            ps.reconcile(manifest, bins, payloads, sys_path)
        return caught.exception

    # -- positive controls ---------------------------------------------------

    def test_a_consistent_ledger_reconciles(self):
        summary = ps.reconcile(MANIFEST, BINS, _clean_payloads(), SYS_PATH)
        self.assertEqual(summary.tests_run, 5)
        self.assertEqual(len(summary.failures), 1)
        self.assertEqual(summary.skipped, 1)
        self.assertEqual(ps.exit_code(summary), 1)
        self.assertEqual(len(summary.records), 5)

    def test_a_setupclass_holder_covers_its_class(self):
        payloads = _clean_payloads()
        records = [_rec("setUpClass (mod_a.A)", kind="holder", outcome="error"),
                   _rec("mod_a.C.test_1"), _rec("mod_a.AB.test_1")]
        payloads[0] = _payload(0, records, errors=[("setUpClass (mod_a.A)", "Traceback", 0)])
        summary = ps.reconcile(MANIFEST, BINS, payloads, SYS_PATH)
        self.assertEqual(summary.tests_run, 3)
        self.assertEqual(len(summary.errors), 1)

    def test_a_setupmodule_holder_covers_its_module(self):
        payloads = _clean_payloads()
        payloads[0] = _payload(0, [_rec("setUpModule (mod_a)", kind="holder", outcome="error")],
                               errors=[("setUpModule (mod_a)", "Traceback", 0)])
        summary = ps.reconcile(MANIFEST, BINS, payloads, SYS_PATH)
        self.assertEqual(summary.tests_run, 1)

    def test_a_failed_import_counts_as_one_error(self):
        manifest = [("test_broken", ("unittest.loader._FailedTest.test_broken",))]
        bins = [ps.Bin(0, 1, (0,))]
        payload = _payload(0, [_rec("unittest.loader._FailedTest.test_broken", outcome="error")],
                           errors=[("test_broken (unittest.loader._FailedTest.test_broken)", "ImportError", 0)])
        payload["manifest"] = manifest
        summary = ps.reconcile(manifest, bins, {0: payload}, SYS_PATH)
        self.assertEqual(len(summary.errors), 1)
        self.assertEqual(summary.tests_run, 1)

    def test_a_module_skip_the_manifest_holds_is_covered(self):
        manifest = [("test_gone", ("unittest.loader.ModuleSkipped.test_gone",))]
        bins = [ps.Bin(0, 1, (0,))]
        payload = _payload(0, [_rec("unittest.loader.ModuleSkipped.test_gone", outcome="skip",
                                    reason="module skipped")], skipped=1)
        payload["manifest"] = manifest
        summary = ps.reconcile(manifest, bins, {0: payload}, SYS_PATH)
        self.assertEqual(summary.skipped, 1)

    # -- the fail-closed branches ---------------------------------------------

    def test_branch1_a_bin_without_a_payload_is_refused(self):
        payloads = _clean_payloads()
        del payloads[1]
        err = self._refused(payloads)
        self.assertEqual((err.branch, err.bin), ("missing-record", 1))

    def test_branch1_a_started_test_without_a_record_is_refused(self):
        payloads = _clean_payloads()
        payloads[1]["tests_run"] = 2
        err = self._refused(payloads)
        self.assertEqual((err.branch, err.bin), ("missing-record", 1))

    def test_branch2_a_duplicate_record_is_refused(self):
        payloads = _clean_payloads()
        payloads[0] = _payload(0, _clean_bin0() + [_rec("mod_a.C.test_1")])
        err = self._refused(payloads)
        self.assertEqual((err.branch, err.bin, err.test_id), ("duplicate-record", 0, "mod_a.C.test_1"))

    def test_branch2_more_records_than_started_tests_is_refused(self):
        # Every id is recorded once, so only the count check sees the excess.
        payloads = _clean_payloads()
        payloads[0] = _payload(0, _clean_bin0(), tests_run=3)
        err = self._refused(payloads)
        self.assertEqual((err.branch, err.bin), ("duplicate-record", 0))
        self.assertIn("recorded 4 tests and started 3", str(err))

    def test_branch3_a_record_outside_the_bin_is_refused(self):
        payloads = _clean_payloads()
        payloads[0] = _payload(0, _clean_bin0() + [_rec("mod_b.B.test_1")])
        err = self._refused(payloads)
        self.assertEqual((err.branch, err.bin, err.test_id), ("foreign-record", 0, "mod_b.B.test_1"))

    def test_branch3_a_holder_outside_the_bin_is_refused(self):
        payloads = _clean_payloads()
        holder = "setUpClass (mod_b.B)"
        payloads[0] = _payload(0, _clean_bin0() + [_rec(holder, kind="holder", outcome="error")])
        err = self._refused(payloads)
        self.assertEqual((err.branch, err.bin, err.test_id), ("foreign-record", 0, holder))

    def test_branch4_a_malformed_payload_is_refused(self):
        for label, breaker in [
            ("not a dict", lambda p: ["records"]),
            ("missing key", lambda p: {k: v for k, v in p.items() if k != "records"}),
            ("wrong bin", lambda p: dict(p, bin=7)),
            ("bad kind", lambda p: dict(p, records=[dict(r, kind="weird") for r in p["records"]])),
            ("bad outcome", lambda p: dict(p, records=[dict(r, outcome="meh") for r in p["records"]])),
            ("record not a dict", lambda p: dict(p, records=["mod_b.B.test_1"])),
            ("tests_run not int", lambda p: dict(p, tests_run="1")),
            ("failure without a position", lambda p: dict(p, failures=[("d", "t")])),
            ("error with a negative position", lambda p: dict(p, errors=[("d", "t", -1)])),
            ("unexpected without a position", lambda p: dict(p, unexpected=["d"])),
            # Bin 1 holds position 1 only. A row must name one of its bin's positions.
            ("failure with no position", lambda p: dict(p, failures=[("d", "t", None)])),
            ("failure at another bin's position", lambda p: dict(p, failures=[("d", "t", 0)])),
            ("error outside the manifest", lambda p: dict(p, errors=[("d", "t", 99)])),
            ("unexpected at another bin's position", lambda p: dict(p, unexpected=[("d", 0)])),
        ]:
            with self.subTest(label=label):
                payloads = _clean_payloads()
                payloads[1] = breaker(payloads[1])
                err = self._refused(payloads)
                self.assertEqual((err.branch, err.bin), ("malformed-payload", 1))

    def test_branch4_a_payload_row_takes_its_position_from_its_own_bin(self):
        cases = _outcome_cases()
        classes = [cases["Pass"], cases["Fail"]]
        children = _children(classes)
        manifest = ps.build_manifest(unittest.TestSuite(_children(classes)))
        with mock.patch.object(sys, "stderr", io.StringIO()):
            result = ps.run_positions(children, (1,), 0)
        self.assertEqual(len(result.failures), 1)
        # Positive control: the bin that holds the failing test maps its row.
        held = ps.result_payload(result, 0, manifest, list(sys.path), (1,))
        self.assertEqual(held["failures"][0][2], 1)
        # A bin that does not hold it maps nothing, and the ledger refuses the row.
        foreign = ps.result_payload(result, 0, manifest, list(sys.path), (0,))
        self.assertIsNone(foreign["failures"][0][2])
        bins = [ps.Bin(0, 1, (0,)), ps.Bin(1, 1, (1,))]
        with mock.patch.object(sys, "stderr", io.StringIO()):
            idle = ps.run_positions(children, (), 1)
        empty = ps.result_payload(idle, 1, manifest, list(sys.path), (1,))
        with self.assertRaises(ps.LedgerError) as caught:
            ps.reconcile(manifest, bins, {0: foreign, 1: empty}, list(sys.path))
        self.assertEqual((caught.exception.branch, caught.exception.bin), ("malformed-payload", 0))

    def test_branch5_a_different_worker_manifest_is_refused(self):
        payloads = _clean_payloads()
        manifest = [tuple(e) for e in MANIFEST]
        manifest[1] = ("mod_b", ("mod_b.B.test_1", "mod_b.B.test_extra"))
        payloads[1]["manifest"] = manifest
        err = self._refused(payloads)
        self.assertEqual((err.branch, err.bin), ("manifest-mismatch", 1))
        self.assertIn("mod_b", str(err))

    def test_branch5_a_reordered_worker_manifest_is_refused(self):
        payloads = _clean_payloads()
        payloads[1]["manifest"] = [tuple(e) for e in reversed(MANIFEST)]
        err = self._refused(payloads)
        self.assertEqual((err.branch, err.bin), ("manifest-mismatch", 1))

    def test_branch6_a_different_worker_sys_path_is_refused(self):
        payloads = _clean_payloads()
        payloads[0]["sys_path"] = ["/cwd", "/extra", "/stdlib"]
        err = self._refused(payloads)
        self.assertEqual((err.branch, err.bin), ("sys-path-mismatch", 0))

    def test_branch7_an_id_neither_started_nor_covered_is_refused(self):
        payloads = _clean_payloads()
        payloads[0] = _payload(0, _clean_bin0()[:3])
        err = self._refused(payloads)
        self.assertEqual((err.branch, err.bin, err.test_id), ("unaccounted-id", 0, "mod_a.AB.test_1"))

    def test_branch7_a_class_holder_covers_no_other_class(self):
        payloads = _clean_payloads()
        records = [_rec("setUpClass (mod_a.A)", kind="holder", outcome="error"), _rec("mod_a.C.test_1")]
        payloads[0] = _payload(0, records)
        err = self._refused(payloads)
        # `mod_a.AB` shares the prefix `mod_a.A` but is another class.
        self.assertEqual((err.branch, err.test_id), ("unaccounted-id", "mod_a.AB.test_1"))

    def test_branch7_a_teardown_holder_covers_nothing(self):
        payloads = _clean_payloads()
        records = [_rec("tearDownClass (mod_a.A)", kind="holder", outcome="error"),
                   _rec("mod_a.C.test_1"), _rec("mod_a.AB.test_1")]
        payloads[0] = _payload(0, records)
        err = self._refused(payloads)
        self.assertEqual((err.branch, err.test_id), ("unaccounted-id", "mod_a.A.test_1"))

    def test_branch8_zero_tests_is_refused(self):
        err = self._refused({}, manifest=[], bins=[])
        self.assertEqual(err.branch, "zero-tests")

    def test_branch8_modules_without_tests_are_refused(self):
        manifest = [("test_empty", ())]
        payload = _payload(0, [])
        payload["manifest"] = manifest
        err = self._refused({0: payload}, manifest=manifest, bins=[ps.Bin(0, 1, (0,))])
        self.assertEqual(err.branch, "zero-tests")

    # -- the bins cover every manifest position exactly once --------------------

    def test_bin_coverage_a_position_in_no_bin_is_refused(self):
        payloads = _clean_payloads()
        del payloads[1]
        err = self._refused(payloads, bins=[ps.Bin(0, 4, (0,))])
        self.assertEqual((err.branch, err.module), ("bin-coverage", "mod_b"))
        self.assertIn("position 1 is in no bin", str(err))

    def test_bin_coverage_a_position_in_two_bins_is_refused(self):
        payloads = _clean_payloads()
        payloads[2] = _payload(2, [_rec("mod_b.B.test_1", outcome="skip", reason="why")], skipped=1)
        bins = BINS + [ps.Bin(2, 1, (1,))]
        err = self._refused(payloads, bins=bins)
        self.assertEqual((err.branch, err.module), ("bin-coverage", "mod_b"))
        self.assertIn("position 1 is in bins 1, 2", str(err))

    def test_bin_coverage_a_position_outside_the_manifest_is_refused(self):
        err = self._refused(_clean_payloads(), bins=[ps.Bin(0, 4, (0,)), ps.Bin(1, 1, (1, 5))])
        self.assertEqual((err.branch, err.bin), ("bin-coverage", 1))
        self.assertIn("position 5 is outside the manifest of 2 units", str(err))


class _FakeConn:
    def __init__(self, messages):
        self.messages = list(messages)

    def poll(self):
        return bool(self.messages)

    def recv(self):
        return self.messages.pop(0)


class MessageReaderTests(unittest.TestCase):
    """The coordinator reads `begin` and `result` messages and refuses
    any other message from a worker."""

    def test_begin_then_result_is_read(self):
        current, payloads = {}, {}
        done = ps._drain(_FakeConn([("begin", "test_x"), ("result", {"bin": 3})]),
                         ps.Bin(3, 1, (0,)), None, current, payloads)
        self.assertTrue(done)
        self.assertEqual((current, payloads), ({3: "test_x"}, {3: {"bin": 3}}))

    def test_an_unknown_worker_message_is_refused(self):
        with self.assertRaises(ps.LedgerError) as caught:
            ps._drain(_FakeConn([("begin", "test_x"), ("gossip", 1)]),
                      ps.Bin(3, 1, (0,)), None, {}, {})
        err = caught.exception
        self.assertEqual((err.branch, err.bin, err.module), ("malformed-payload", 3, "test_x"))


# ---------------------------------------------------------------------------
# The manifest, the bins and the affinity list
# ---------------------------------------------------------------------------

_CALL_RE = re.compile(r"\bderive_one" + r"\s*\(")
_DEF_RE = re.compile(r"^\s*def derive_one\b", re.M)
_PRUNE = {".cache", "fixtures", "__pycache__"}


def _affinity_gaps(tests_dir: Path, affinity) -> list[str]:
    """Every name in `affinity` must be a module in `tests_dir`, and every
    module that calls the corpus derive must sit in an affinity group."""
    grouped = {name for group in affinity for name in group}
    gaps = [f"missing module {name}.py" for name in sorted(grouped)
            if not (tests_dir / f"{name}.py").is_file()]
    for root, dirs, files in os.walk(tests_dir):
        dirs[:] = sorted(d for d in dirs if d not in _PRUNE and not d.startswith("."))
        for fname in sorted(files):
            if not fname.endswith(".py"):
                continue
            path = Path(root) / fname
            text = path.read_text(encoding="utf-8", errors="replace")
            if not _CALL_RE.search(text) or _DEF_RE.search(text):
                continue
            rel = path.relative_to(tests_dir)
            if len(rel.parts) != 1 or path.stem not in grouped:
                gaps.append(f"ungrouped caller {rel.as_posix()}")
    return gaps


def _cls(name, tests=1, body=""):
    """Source for one TestCase class with `tests` passing tests."""
    methods = "".join("    def test_%d(self):\n        pass\n" % i for i in range(tests)) or "    pass\n"
    return "class %s(unittest.TestCase):\n%s%s\n" % (name, body, methods)


def _module(*classes, extra=""):
    return "import unittest\n\n" + extra + "\n".join(classes)


@contextlib.contextmanager
def _discovered(files, affinity=ps.AFFINITY):
    """Discover a synthetic tree in this process and yield (units, suite).
    The modules and the import path it added are removed afterwards."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = _write_tree(Path(tmp), files)
        saved_path = list(sys.path)
        before = set(sys.modules)
        try:
            suite = ps.discover(str(tree), "test*.py", None)
            yield ps.build_units(suite, affinity), suite
        finally:
            sys.path[:] = saved_path
            for name in set(sys.modules) - before:
                sys.modules.pop(name, None)


SPLIT_FILES = {"test_unitcut_split.py": _module(_cls("Alpha", 2), _cls("Beta"), _cls("Gamma"))}


class UnitCutTests(unittest.TestCase):
    """The cut of a discovered suite into scheduling units."""

    def _names(self, files, affinity=ps.AFFINITY):
        with _discovered(files, affinity) as (units, _):
            return [unit.name for unit in units]

    def test_a_module_splits_into_one_unit_per_class(self):
        with _discovered(SPLIT_FILES) as (units, _):
            self.assertEqual([(u.module, u.name, u.ids) for u in units], [
                ("test_unitcut_split", "test_unitcut_split.Alpha",
                 ("test_unitcut_split.Alpha.test_0", "test_unitcut_split.Alpha.test_1")),
                ("test_unitcut_split", "test_unitcut_split.Beta", ("test_unitcut_split.Beta.test_0",)),
                ("test_unitcut_split", "test_unitcut_split.Gamma", ("test_unitcut_split.Gamma.test_0",)),
            ])

    def test_the_classes_of_a_split_module_can_land_in_different_bins(self):
        with _discovered(SPLIT_FILES) as (units, _):
            bins = ps.assign_bins(units, 2, (), {})
        holding = [b.index for b in bins for p in b.positions if units[p].module == "test_unitcut_split"]
        self.assertEqual(sorted(set(holding)), [0, 1])

    def test_a_module_hazard_keeps_the_module_one_unit(self):
        hazards = {
            "setUpModule": "def setUpModule():\n    pass\n\n",
            "tearDownModule": "def tearDownModule():\n    pass\n\n",
            "load_tests": "def load_tests(loader, tests, pattern):\n    return tests\n\n",
        }
        for label, extra in hazards.items():
            with self.subTest(label):
                files = {"test_unitcut_hazard.py": _module(_cls("A"), _cls("B"), extra=extra)}
                self.assertEqual(self._names(files), ["test_unitcut_hazard"])
        with self.subTest("addModuleCleanup"):
            cleanup = ("    @classmethod\n    def setUpClass(cls):\n"
                       "        unittest.addModule" + "Cleanup(lambda: None)\n\n")
            files = {"test_unitcut_hazard.py": _module(_cls("A", body=cleanup), _cls("B"))}
            self.assertEqual(self._names(files), ["test_unitcut_hazard"])
        with self.subTest("positive control: the same module without the hazard splits"):
            files = {"test_unitcut_hazard.py": _module(_cls("A"), _cls("B"))}
            self.assertEqual(self._names(files), ["test_unitcut_hazard.A", "test_unitcut_hazard.B"])

    def test_one_class_is_one_unit_named_for_the_module(self):
        files = {"test_unitcut_single.py": _module(_cls("Only", 3))}
        self.assertEqual(self._names(files), ["test_unitcut_single"])

    def test_an_affinity_module_stays_one_unit(self):
        files = {"test_unitcut_split.py": SPLIT_FILES["test_unitcut_split.py"],
                 "test_unitcut_pinned.py": _module(_cls("A"), _cls("B"))}
        names = self._names(files, (("test_unitcut_pinned", "test_other"),))
        self.assertEqual(names, ["test_unitcut_pinned", "test_unitcut_split.Alpha",
                                 "test_unitcut_split.Beta", "test_unitcut_split.Gamma"])

    def test_the_shipped_affinity_list_is_the_default_of_the_cut_and_the_bins(self):
        for function in (ps.build_units, ps.assign_bins):
            self.assertIs(inspect.signature(function).parameters["affinity"].default, ps.AFFINITY)

    def test_the_units_of_an_affinity_module_share_a_task_even_when_whole(self):
        files = {"test_unitcut_pinned.py": _module(_cls("A"), _cls("B")),
                 "test_unitcut_pinned2.py": _module(_cls("C"), _cls("D")),
                 "test_unitcut_split.py": SPLIT_FILES["test_unitcut_split.py"]}
        group = (("test_unitcut_pinned", "test_unitcut_pinned2"),)
        with _discovered(files, group) as (units, _):
            pinned = {i for i, u in enumerate(units) if u.module in group[0]}
            self.assertEqual(len(pinned), 2)
            for workers in range(1, 6):
                bins = ps.assign_bins(units, workers, group, {})
                self.assertEqual(len([b for b in bins if pinned & set(b.positions)]), 1)

    def test_failed_imports_and_skips_stay_one_unit(self):
        files = {
            "test_unitcut_broken.py": "import nothing_by_this_name_exists\n",
            "test_unitcut_skipped.py": "import unittest\nraise unittest.SkipTest('whole module')\n",
            "test_unitcut_ok.py": _module(_cls("A"), _cls("B")),
        }
        with _discovered(files) as (units, _):
            self.assertEqual([u.name for u in units], [
                "test_unitcut_broken", "test_unitcut_ok.A", "test_unitcut_ok.B", "test_unitcut_skipped"])
            self.assertEqual(units[0].ids, ("unittest.loader._FailedTest.test_unitcut_broken",))

    def test_an_imported_class_splits_off_with_the_timings_key_of_its_own_module(self):
        helper = "import unittest\n\nclass Shared(unittest.TestCase):\n    def test_shared(self):\n        pass\n"
        files = {"unitcut_helper.py": helper,
                 "test_unitcut_foreign.py": "from unitcut_helper import Shared\n" + _module(_cls("Local"))}
        with _discovered(files) as (units, _):
            self.assertEqual([(u.name, u.key, u.ids) for u in units], [
                ("test_unitcut_foreign.Local", "test_unitcut_foreign.Local",
                 ("test_unitcut_foreign.Local.test_0",)),
                ("test_unitcut_foreign.Shared", "unitcut_helper.Shared",
                 ("unitcut_helper.Shared.test_shared",)),
            ])

    def test_an_imported_class_whose_module_has_a_module_fixture_keeps_the_module_one_unit(self):
        helper = ("import unittest\n\ndef setUpModule():\n    pass\n\n"
                  "class Shared(unittest.TestCase):\n    def test_shared(self):\n        pass\n")
        files = {"unitcut_helper.py": helper,
                 "test_unitcut_foreign.py": "from unitcut_helper import Shared\n" + _module(_cls("Local"))}
        self.assertEqual(self._names(files), ["test_unitcut_foreign"])

    def test_empty_classes_are_not_units(self):
        files = {"test_unitcut_empty.py": _module(_cls("A"), _cls("B"), _cls("Empty", 0))}
        with _discovered(files) as (units, _):
            self.assertEqual([u.name for u in units], ["test_unitcut_empty.A", "test_unitcut_empty.B"])

    def test_the_units_hold_exactly_the_discovered_tests_in_order(self):
        files = dict(SPLIT_FILES)
        files.update({
            "test_unitcut_alias.py": _module(_cls("A", 2), "Again = A\n", _cls("B")),
            "test_unitcut_fixture.py": _module(_cls("A"), _cls("B"),
                                               extra="def setUpModule():\n    pass\n\n"),
            "test_unitcut_broken.py": "import nothing_by_this_name_exists\n",
            "test_unitcut_empty.py": "import unittest\n",
        })
        with _discovered(files) as (units, suite):
            discovered = [leaf.id() for leaf in ps._leaves(suite)]
            self.assertEqual([i for u in units for i in u.ids], discovered)
            self.assertGreater(len(units), len(list(suite)))
            self.assertEqual(ps.unit_manifest(units), [(u.name, u.ids) for u in units])
            # The units reference the discovered tests themselves, so a worker runs them.
            self.assertEqual([leaf for u in units for leaf in ps._leaves(u.suite)],
                             list(ps._leaves(suite)))


def _unit(module, cls=None):
    name = module if cls is None else "%s.%s" % (module, cls)
    key = module.rpartition(".")[2] + ("" if cls is None else "." + cls)
    return ps.Unit(module, name, (), None, key)


class MeasuredWeightTests(unittest.TestCase):
    """A unit weighs its timings entry; the median stands in for a missing one."""

    TIMINGS = {"test_a.X": 5.0, "test_a.Y": 1.0, "test_a_b.Z": 100.0}

    def _weights(self, units, timings=None):
        return ps.unit_weights(units, self.TIMINGS if timings is None else timings)

    def test_a_class_unit_weighs_its_entry(self):
        self.assertEqual(self._weights([_unit("test_a", "X"), _unit("test_a", "Y")]), [5.0, 1.0])

    def test_a_unit_without_an_entry_weighs_the_median_of_all_entries(self):
        self.assertEqual(self._weights([_unit("test_a", "Unknown")]), [5.0])
        self.assertEqual(self._weights([_unit("test_new")]), [5.0])
        self.assertEqual(self._weights([_unit("test_a", "Unknown")], {"a.X": 1.0, "a.Y": 2.0}), [1.5])

    def test_a_module_unit_weighs_the_sum_of_its_class_entries(self):
        # `test_a_b.Z` shares a prefix with `test_a` and is not one of its classes.
        self.assertEqual(self._weights([_unit("test_a")]), [6.0])

    def test_entries_are_read_by_the_short_module_name(self):
        self.assertEqual(self._weights([_unit("pkg.test_a", "X"), _unit("pkg.test_a")]), [5.0, 6.0])

    def test_no_timings_weigh_one_and_a_zero_entry_weighs_the_floor(self):
        self.assertEqual(self._weights([_unit("test_a", "X")], {}), [1])
        self.assertEqual(self._weights([_unit("test_a", "X")], {"test_a.X": 0.0}), [ps.MIN_WEIGHT])

    def test_weights_decide_the_bins_from_the_file_not_from_a_module_table(self):
        units = [_unit("test_a", "X"), _unit("test_a", "Y"), _unit("test_a", "W")]
        heavy_w = ps.assign_bins(units, 2, (), {"test_a.W": 9.0, "test_a.X": 1.0, "test_a.Y": 1.0})
        self.assertEqual(heavy_w, [ps.Bin(0, 9.0, (2,)), ps.Bin(1, 2.0, (0, 1))])
        heavy_x = ps.assign_bins(units, 2, (), {"test_a.W": 1.0, "test_a.X": 9.0, "test_a.Y": 1.0})
        self.assertEqual(heavy_x, [ps.Bin(0, 9.0, (0,)), ps.Bin(1, 2.0, (1, 2))])


class BinningTests(unittest.TestCase):
    """LPT assignment is deterministic, loses no unit, and keeps each affinity
    group in one bin."""

    MODULES = ["test_a", "test_heavy", "test_g1", "test_b", "test_g2", "test_c", "test_d", "test_g3"]
    AFFINITY = (("test_g1", "test_g2", "test_g3"),)
    TIMINGS = {"test_heavy.H": 10, "test_g1.G": 2, "test_g3.G": 2}

    def _units(self, modules=None):
        return [_unit(m, "H" if m == "test_heavy" else "G" if m in ("test_g1", "test_g3") else None)
                for m in modules or self.MODULES]

    def _assign(self, workers, modules=None):
        return ps.assign_bins(self._units(modules), workers, self.AFFINITY, self.TIMINGS)

    def test_assignment_is_deterministic(self):
        first = self._assign(3)
        for _ in range(5):
            self.assertEqual(self._assign(3), first)
        # Heaviest task first, ties to the lowest bin, discovery order inside a bin.
        # Units with no entry weigh the median, 2: the group weighs 6 and each other unit 2.
        self.assertEqual(first, [ps.Bin(0, 10.0, (1,)),
                                 ps.Bin(1, 8.0, (2, 4, 6, 7)),
                                 ps.Bin(2, 6.0, (0, 3, 5))])

    def test_no_unit_is_lost_and_each_bin_runs_in_discovery_order(self):
        for workers in range(1, 10):
            with self.subTest(workers=workers):
                bins = self._assign(workers)
                positions = [p for b in bins for p in b.positions]
                self.assertEqual(sorted(positions), list(range(len(self.MODULES))))
                for b in bins:
                    self.assertEqual(list(b.positions), sorted(b.positions))

    def test_the_affinity_group_shares_one_bin_at_every_worker_count(self):
        group = {self.MODULES.index(n) for n in self.AFFINITY[0]}
        for workers in range(1, 10):
            with self.subTest(workers=workers):
                holding = [b.index for b in self._assign(workers) if group & set(b.positions)]
                self.assertEqual(len(holding), 1)

    def test_every_unit_of_an_affinity_module_joins_the_group_task(self):
        units = [_unit("test_g1", "A"), _unit("test_g1", "B"), _unit("test_x"), _unit("test_g2")]
        for workers in range(1, 6):
            bins = ps.assign_bins(units, workers, self.AFFINITY, {})
            self.assertEqual(len([b for b in bins if {0, 1, 3} & set(b.positions)]), 1)

    def test_the_real_affinity_group_shares_one_bin(self):
        modules = ["test_x"] + list(ps.AFFINITY[0]) + ["test_derive_arch", "test_y"]
        group = set(range(1, 1 + len(ps.AFFINITY[0])))
        for workers in range(1, 10):
            bins = ps.assign_bins([_unit(m) for m in modules], workers)
            self.assertEqual(len([b for b in bins if group & set(b.positions)]), 1)

    def test_overlapping_affinity_groups_are_refused(self):
        units = [_unit("a"), _unit("b"), _unit("c")]
        with self.assertRaises(ps.LedgerError) as caught:
            ps.assign_bins(units, 3, affinity=(("a", "b"), ("b", "c")))
        err = caught.exception
        self.assertEqual((err.branch, err.module), ("affinity-overlap", "b"))
        self.assertIn("affinity groups 0 and 1", str(err))
        # Positive control: disjoint groups over the same names are accepted.
        bins = ps.assign_bins(units, 3, affinity=(("a", "b"), ("c",)))
        self.assertEqual(sorted(p for b in bins for p in b.positions), [0, 1, 2])

    def test_affinity_and_weights_match_the_short_module_name(self):
        # Discovery under a top-level directory names modules `pkg.test_x`.
        units = [_unit("pkg." + m, c) for m, c in [("test_a", None), ("test_g1", None), ("test_b", None),
                                                    ("test_g2", None), ("test_heavy", "H")]]
        timings = {"test_heavy.H": 10, "test_g1.A": 2, "test_g2.B": 1}
        bins = ps.assign_bins(units, 3, (("test_g1", "test_g2"),), timings)
        # test_a and test_b have no entry and weigh the median, 2.
        self.assertEqual(bins, [ps.Bin(0, 10.0, (4,)), ps.Bin(1, 3.0, (1, 3)), ps.Bin(2, 4.0, (0, 2))])

    def test_bins_never_exceed_the_task_count(self):
        self.assertEqual(len(self._assign(50)), 6)
        self.assertEqual(ps.assign_bins([], 4), [])

    def test_changing_weights_changes_the_bins_and_never_the_units(self):
        with _discovered(SPLIT_FILES) as (units, _):
            # Beta has no entry and weighs the median, 25.5.
            by_gamma = ps.assign_bins(units, 2, (), {"test_unitcut_split.Gamma": 50.0,
                                                     "test_unitcut_split.Alpha": 1.0})
            by_alpha = ps.assign_bins(units, 2, (), {"test_unitcut_split.Gamma": 1.0,
                                                     "test_unitcut_split.Alpha": 50.0})
            before = ps.unit_manifest(units)
        self.assertNotEqual(by_gamma, by_alpha)
        for bins in (by_gamma, by_alpha):
            self.assertEqual(sorted(p for b in bins for p in b.positions), list(range(len(before))))
        self.assertEqual([b.positions for b in by_gamma], [(2,), (0, 1)])
        self.assertEqual([b.positions for b in by_alpha], [(0,), (1, 2)])

    def test_the_cap_is_the_cpus_this_process_may_use(self):
        # An affinity mask narrower than the host: os.cpu_count counts the host.
        with mock.patch.object(ps.os, "cpu_count", return_value=16), \
                mock.patch.object(ps.os, "process_cpu_count", return_value=2):
            self.assertEqual(ps.effective_workers(None), 2)
            self.assertEqual(ps.effective_workers(8), 2)

    def test_the_worker_count_is_capped_by_the_cpu_count(self):
        with mock.patch.object(ps.os, "process_cpu_count", return_value=4):
            self.assertEqual(ps.effective_workers(None), min(ps.DEFAULT_WORKERS, 4))
            self.assertEqual(ps.effective_workers(8), 4)
            self.assertEqual(ps.effective_workers(2), 2)
        with mock.patch.object(ps.os, "process_cpu_count", return_value=None):
            self.assertEqual(ps.effective_workers(3), 1)

    def test_the_shipped_defaults(self):
        self.assertEqual(ps.DEFAULT_PATTERN, "test*.py")
        self.assertIsInstance(ps.DEFAULT_WORKERS, int)
        self.assertGreaterEqual(ps.DEFAULT_WORKERS, 1)
        self.assertFalse(hasattr(ps, "HEAVY"), "the hand-kept weight table is replaced by the timings file")

    def test_the_manifest_is_one_entry_per_module_in_discovery_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            (tree / "test_one.py").write_text(
                "import unittest\nclass T(unittest.TestCase):\n    def test_x(self): pass\n"
                "    def test_y(self): pass\n")
            (tree / "test_two.py").write_text("import nothing_by_this_name_exists\n")
            (tree / "test_zero.py").write_text("import unittest\n")
            saved = list(sys.path)
            try:
                manifest = ps.build_manifest(ps.discover(str(tree), "test*.py", None))
            finally:
                sys.path[:] = saved
                for name in ("test_one", "test_two", "test_zero"):
                    sys.modules.pop(name, None)
        self.assertEqual(manifest, [
            ("test_one", ("test_one.T.test_x", "test_one.T.test_y")),
            ("test_two", ("unittest.loader._FailedTest.test_two",)),
            ("test_zero", ()),
        ])


def _records(*rows):
    """JSONL text of `--records` rows given as (id, kind, duration)."""
    return "".join(json.dumps({"id": i, "kind": k, "duration": d, "bin": 0, "outcome": "ok"}) + "\n"
                   for i, k, d in rows)


RECORD_ROWS = [
    ("test_a.X.test_1", "test", 1.004),
    ("test_a.X.test_2", "test", 2.0),
    ("test_a.X.test_2", "subtest", 99.0),
    ("test_a.Y.test_1", "test", 0.5),
    ("test_a.Y.test_2", "test", None),
    ("setUpClass (test_a.Y)", "holder", None),
    ("pkg.test_b.Z.test_1", "test", 0.004),
    ("unittest.loader._FailedTest.test_broken", "test", 0.1),
]


class TimingsFileTests(unittest.TestCase):
    """The timings reader, the writer and the committed file."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def _file(self, text, name="timings.json"):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_the_writer_sums_test_durations_by_module_and_class(self):
        path = self._file(_records(*RECORD_ROWS), "records.jsonl")
        self.assertEqual(ps.timings_from_records(path), {
            "loader._FailedTest": 0.1, "test_a.X": 3.0, "test_a.Y": 0.5, "test_b.Z": 0.0})

    def test_class_setup_contributes_to_weight_without_changing_test_duration(self):
        clock = [0.0]

        class CostlyFixture(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                clock[0] += 20.0

            def test_work(self):
                clock[0] += 1.0

        child = unittest.defaultTestLoader.loadTestsFromTestCase(CostlyFixture)
        with mock.patch.object(ps.time, "perf_counter", side_effect=lambda: clock[0]):
            result = ps.run_positions([child], (0,), 0)
        self.assertEqual(result.testsRun, 1)
        record = result.records[0]
        self.assertEqual(record["duration"], 1.0)
        self.assertEqual(record["fixture_duration"], 20.0)
        path = self.dir / "records.jsonl"
        result.write_jsonl(path)
        self.assertEqual(sum(ps.timings_from_records(path).values()), 21.0)

    def test_the_writer_is_deterministic_whatever_the_record_order(self):
        forward = self._file(_records(*RECORD_ROWS), "forward.jsonl")
        backward = self._file(_records(*reversed(RECORD_ROWS)), "backward.jsonl")
        out_a, out_b = self.dir / "a.json", self.dir / "b.json"
        self.assertEqual(ps.write_timings(forward, out_a), 4)
        ps.write_timings(backward, out_b)
        self.assertEqual(out_a.read_bytes(), out_b.read_bytes())
        text = out_a.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("}\n"))
        keys = list(json.loads(text))
        self.assertEqual(keys, sorted(keys))
        # The bytes are the reader's input: reading and formatting them changes nothing.
        self.assertEqual(ps.format_timings(ps.read_timings(out_a)), text)

    def test_the_writer_refuses_records_without_a_timed_test(self):
        path = self._file(_records(("test_a.X.test_1", "test", None), ("test_a.X.test_1", "subtest", 3.0)))
        with self.assertRaises(ps.LedgerError) as caught:
            ps.write_timings(path, self.dir / "out.json")
        self.assertEqual(caught.exception.branch, "malformed-records")
        self.assertFalse((self.dir / "out.json").exists())
        bad = self._file("{not json\n", "bad.jsonl")
        with self.assertRaises(ps.LedgerError):
            ps.timings_from_records(bad)

    def test_a_malformed_file_is_refused(self):
        cases = {
            "not json": "{nope",
            "not an object": "[1, 2]",
            "a string value": '{"a.B": "slow"}',
            "a boolean value": '{"a.B": true}',
            "a negative value": '{"a.B": -1}',
            "a null value": '{"a.B": null}',
            "a non-finite value": '{"a.B": NaN}',
        }
        for label, text in cases.items():
            with self.subTest(label), self.assertRaises(ps.LedgerError) as caught:
                ps.read_timings(self._file(text))
            self.assertEqual(caught.exception.branch, "malformed-timings")
        with self.subTest("positive control"):
            self.assertEqual(ps.read_timings(self._file('{"a.B": 1, "c.D": 2.5}')), {"a.B": 1, "c.D": 2.5})

    def test_a_missing_default_file_gives_uniform_weights_and_says_so(self):
        gone = str(self.dir / "absent.json")
        with mock.patch.object(ps, "DEFAULT_TIMINGS", gone):
            timings, note = ps.load_timings(None)
        self.assertEqual(timings, {})
        self.assertIn("every unit weighs 1", note)
        self.assertIn(gone, note)

    def test_a_missing_explicit_file_is_refused(self):
        with self.assertRaises(ps.LedgerError) as caught:
            ps.load_timings(str(self.dir / "absent.json"))
        self.assertEqual(caught.exception.branch, "malformed-timings")

    def test_a_malformed_default_file_is_refused(self):
        bad = self._file("[]")
        with mock.patch.object(ps, "DEFAULT_TIMINGS", str(bad)), self.assertRaises(ps.LedgerError):
            ps.load_timings(None)

    def test_the_committed_file_is_valid_and_in_the_writers_format(self):
        committed = Path(ps.DEFAULT_TIMINGS)
        self.assertEqual(committed.parent, HERE)
        timings = ps.read_timings(committed)
        self.assertTrue(timings)
        self.assertEqual(committed.read_text(encoding="utf-8"), ps.format_timings(timings))
        for key in timings:
            self.assertEqual(len(key.split(".")), 2, key)


class AffinityCompletenessTests(unittest.TestCase):
    """The affinity list names real modules, and every module that calls the
    corpus derive sits in an affinity group."""

    def test_the_real_tree_has_no_gap(self):
        self.assertEqual(_affinity_gaps(HERE, ps.AFFINITY), [])
        # The scan is not vacuous: the known callers are found.
        callers = {p.stem for p in HERE.glob("test_*.py")
                   if _CALL_RE.search(p.read_text(encoding="utf-8", errors="replace"))}
        self.assertTrue(callers)
        self.assertLessEqual(callers, {n for g in ps.AFFINITY for n in g})

    def test_positive_control_an_ungrouped_caller_is_a_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            (tree / "test_grouped.py").write_text("x = 1\n")
            (tree / "test_rogue.py").write_text("import c\nc.derive_one" + "('n', '/r')\n")
            (tree / "derive_corpus.py").write_text("def derive_" + "one(n, r):\n    pass\nderive_one" + "(1, 2)\n")
            gaps = _affinity_gaps(tree, (("test_grouped", "test_missing"),))
        self.assertEqual(gaps, ["missing module test_missing.py", "ungrouped caller test_rogue.py"])


# ---------------------------------------------------------------------------
# The driver end to end, over synthetic trees
# ---------------------------------------------------------------------------

MIXED_TREE = {
    "test_a_basic.py": """
        import unittest

        class Basic(unittest.TestCase):
            def test_pass(self):
                pass

            def test_skip(self):
                self.skipTest("reason a")

            @unittest.expectedFailure
            def test_xfail(self):
                self.assertEqual(1, 2)

            @unittest.expectedFailure
            def test_uxsuccess(self):
                pass
    """,
    "test_b_broken.py": """
        import unittest

        class Broken(unittest.TestCase):
            def test_fail(self):
                self.assertEqual(1, 2)

            def test_error(self):
                raise RuntimeError("boom")

            def test_subtests(self):
                for i in range(3):
                    with self.subTest(i=i):
                        self.assertNotEqual(i, 1)

            def test_subtests_all_skip(self):
                for i in range(2):
                    with self.subTest(i=i):
                        self.skipTest("every subtest skips")
    """,
    "test_c_classes.py": """
        import unittest

        class SkippedClass(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise unittest.SkipTest("class skip reason")

            def test_never(self):
                pass

        class BrokenClass(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise RuntimeError("class setup broke")

            def test_never(self):
                pass

        class FineClass(unittest.TestCase):
            def test_fine(self):
                pass
    """,
    "test_d_module.py": """
        import unittest

        def setUpModule():
            raise RuntimeError("module setup broke")

        class InBrokenModule(unittest.TestCase):
            def test_one(self):
                pass

            def test_two(self):
                pass
    """,
    "test_e_import.py": """
        import a_module_that_does_not_exist_anywhere
    """,
    "test_f_modskip.py": """
        import unittest
        raise unittest.SkipTest("module skipped at import")
    """,
    "test_g_syspath.py": """
        import json
        import os
        import sys
        import unittest

        # Unguarded, as many real test modules do: every import adds an entry.
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_extra"))

        class DumpPath(unittest.TestCase):
            def test_dump(self):
                out = os.environ.get("PS_SYSPATH_DIR")
                if out:
                    with open(os.path.join(out, "syspath-%d.json" % os.getpid()), "w") as fh:
                        json.dump(sys.path, fh)
    """,
    "_extra/crux/__init__.py": "",
}

STDIN_TREE = {
    "test_stdin.py": """
        import os
        import unittest

        class Stdin(unittest.TestCase):
            def test_fd0_is_devnull(self):
                self.assertTrue(os.path.samestat(os.fstat(0), os.stat(os.devnull)))
    """,
    "test_other.py": """
        import unittest

        class Other(unittest.TestCase):
            def test_ok(self):
                pass
    """,
}

WRITER_TREE = {
    "test_writer.py": """
        import os
        import unittest

        class Writer(unittest.TestCase):
            def test_writes_into_cwd(self):
                with open(os.path.join(os.getcwd(), "residue.txt"), "w") as fh:
                    fh.write("x")
    """,
}

CRASH_TREE = {
    "test_crash.py": """
        import os
        import unittest

        class Crash(unittest.TestCase):
            def test_exit(self):
                os._exit(3)
    """,
    "test_fine.py": """
        import unittest

        class Fine(unittest.TestCase):
            def test_ok(self):
                pass
    """,
}

LATE_EXIT_TREE = {
    "test_late_exit.py": """
        import atexit
        import multiprocessing
        import os
        import unittest

        class LateExit(unittest.TestCase):
            def test_register_exit(self):
                # Runs when the worker process shuts down, after its payload.
                if multiprocessing.parent_process() is not None:
                    atexit.register(os._exit, 4)
    """,
    "test_fine.py": CRASH_TREE["test_fine.py"],
}

MISMATCH_TREE = {
    "test_mp.py": """
        import multiprocessing
        import unittest

        class Varies(unittest.TestCase):
            def test_base(self):
                pass

        if multiprocessing.parent_process() is not None:
            def test_only_in_a_worker(self):
                pass
            Varies.test_only_in_a_worker = test_only_in_a_worker
    """,
    "test_fine.py": CRASH_TREE["test_fine.py"],
}

FORK_HOLD_TREE = {
    "test_fork_hold.py": """
        import os
        import time
        import unittest

        class ForkHold(unittest.TestCase):
            def test_fork_then_exit(self):
                pid_dir = os.environ["PS_PID_DIR"]
                if os.fork() == 0:
                    # The forked child keeps every inherited descriptor except
                    # stdio, so the worker's pipe and sentinel stay open after
                    # the worker exits.
                    fd = os.open(os.devnull, os.O_RDWR)
                    for target in (0, 1, 2):
                        os.dup2(fd, target)
                    with open(os.path.join(pid_dir, str(os.getpid())), "w") as fh:
                        fh.write("x")
                    time.sleep(300)
                    os._exit(0)
                deadline = time.monotonic() + 60
                while not os.listdir(pid_dir) and time.monotonic() < deadline:
                    time.sleep(0.05)
                os._exit(3)
    """,
    "test_fine.py": CRASH_TREE["test_fine.py"],
}

# The coordinator sends itself SIGTERM while discovery imports this module. The
# unittest loader turns an exception raised during an import into a failed-import
# test, so a handler that raised here would be swallowed.
TERM_ON_IMPORT_TREE = {
    "test_term_on_import.py": """
        import multiprocessing
        import os
        import signal
        import unittest

        if multiprocessing.parent_process() is None:
            os.kill(os.getpid(), signal.SIGTERM)

        class Fine(unittest.TestCase):
            def test_ok(self):
                pass
    """,
}

# A worker's test sends SIGHUP to the coordinator, its parent process.
HUP_FROM_WORKER_TREE = {
    "test_hup_parent.py": """
        import multiprocessing
        import os
        import signal
        import unittest

        class HangUp(unittest.TestCase):
            def test_hup_the_coordinator(self):
                if multiprocessing.parent_process() is not None:
                    os.kill(os.getppid(), signal.SIGHUP)
    """,
    "test_fine.py": CRASH_TREE["test_fine.py"],
}

SLEEP_TREE = {
    name: """
        import os
        import time
        import unittest

        class Sleeper(unittest.TestCase):
            def test_sleep(self):
                with open(os.path.join(os.environ["PS_PID_DIR"], str(os.getpid())), "w") as fh:
                    fh.write("x")
                time.sleep(600)
    """
    for name in ("test_sleep_a.py", "test_sleep_b.py")
}

# Each worker's test starts a child process, records its pid, and waits. The
# child outlives the worker unless the driver stops the worker's process group.
_CHILD_THEN_SLEEP = """
    import os
    import subprocess
    import sys
    import time
    import unittest

    class StartsAChild(unittest.TestCase):
        def test_child_then_sleep(self):
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"],
                                     stdin=subprocess.DEVNULL)
            with open(os.path.join(os.environ["PS_PID_DIR"], "child-%d" % child.pid), "w") as fh:
                fh.write("x")
            time.sleep(600)
"""
ORPHAN_TREE = {name: _CHILD_THEN_SLEEP for name in ("test_orphan_a.py", "test_orphan_b.py")}

# Bin 0's worker exits once the harness writes the `go` file, so the run fails
# closed while bin 1's worker and its child still run. The harness writes `go`
# only after its positive control has seen the child alive; exiting as soon as
# the child existed let the stop race the control.
ORPHAN_ON_CRASH_TREE = {
    "test_a_crash.py": """
        import os
        import time
        import unittest

        class Crash(unittest.TestCase):
            def test_exit_once_the_harness_says_go(self):
                go = os.path.join(os.environ["PS_PID_DIR"], "go")
                deadline = time.monotonic() + 60
                while not os.path.exists(go) and time.monotonic() < deadline:
                    time.sleep(0.05)
                os._exit(3)
    """,
    "test_b_child.py": _CHILD_THEN_SLEEP,
}

# A package discovered under a top-level directory: its modules are named
# `ps_pkg.test_*`, and the real affinity group sits among them.
PACKAGE_TREE = {"ps_pkg/__init__.py": ""}
PACKAGE_TREE.update({
    "ps_pkg/%s.py" % name: CRASH_TREE["test_fine.py"]
    for name in ("test_arch_corpus", "test_arch_pack_acceptance", "test_other", "test_swift_app_gate")
})

# Five units of weight 1 over two workers: bin 0 runs a, c and d's DSetup, bin 1
# runs b and d's D, so merging in bin order would put c's blocks before b's.
_ORDER_MODULE = """
    import unittest

    class {cls}(unittest.TestCase):
        def test_error(self):
            raise RuntimeError("{cls} broke")

        def test_fail(self):
            self.assertEqual("{cls}", "other")
{extra}"""
_ORDER_UXSUCCESS = """
        @unittest.expectedFailure
        def test_uxsuccess(self):
            pass
"""
ORDER_TREE = {
    "test_a_order.py": _ORDER_MODULE.format(cls="A", extra="""
    class ASub(unittest.TestCase):
        def test_sub(self):
            with self.subTest(k=1):
                self.fail("subtest a broke")

    def tearDownModule():
        raise RuntimeError("module a teardown broke")
"""),
    "test_b_order.py": _ORDER_MODULE.format(cls="B", extra=_ORDER_UXSUCCESS),
    "test_c_order.py": _ORDER_MODULE.format(cls="C", extra=_ORDER_UXSUCCESS),
    "test_d_order.py": _ORDER_MODULE.format(cls="D", extra="""
    class DSetup(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            raise RuntimeError("class d setup broke")

        def test_never(self):
            pass
"""),
}

_BLOCK_RE = re.compile(r"^(?:ERROR|FAIL|UNEXPECTED SUCCESS): .*$", re.M)

SERIAL_RECORDER = """
import importlib.util, os, sys, unittest
driver, start, out = sys.argv[1:4]
spec = importlib.util.spec_from_file_location("parallel_suite_under_test", driver)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sys.path[0] = os.getcwd()
suite = unittest.TestLoader().discover(start)
result = module.CanonicalRecorder(sys.stderr, True, 1)
result.startTestRun()
suite(result)
result.stopTestRun()
result.write_jsonl(out)
"""


def _write_tree(root: Path, files: dict) -> Path:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
    return root


def _listing(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def _canonical(path: Path) -> list[tuple]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return sorted(tuple(str(r.get(f)) for f in ps.CANONICAL_FIELDS) for r in rows)


def _status_lines(stderr: str) -> tuple[str, str]:
    lines = stderr.rstrip("\n").splitlines()
    ran = [line for line in lines if _RAN_RE.match(line)]
    return (_mask_time(ran[-1]) if ran else "", lines[-1] if lines else "")


class DriverEndToEndTests(unittest.TestCase):
    """The driver, run as a subprocess with two workers."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(os.path.realpath(tmp.name))
        self.cwd = self.root / "cwd"
        self.cwd.mkdir()

    def _tree(self, files, name="tree"):
        return _write_tree(self.root / name, files)

    def _run(self, tree, *extra, stdin=subprocess.DEVNULL, env=None):
        return subprocess.run(
            [sys.executable, str(DRIVER), str(tree), "--workers", "2", *extra],
            cwd=self.cwd, stdin=stdin, capture_output=True, text=True,
            timeout=RUN_TIMEOUT, env=env, preexec_fn=_default_signals())

    def _env(self, **extra):
        env = dict(os.environ)
        # Keep the parent's import roots when a fixture changes the child cwd.
        # An empty PYTHONPATH entry names that launch directory too.
        if "PYTHONPATH" in env:
            env["PYTHONPATH"] = os.pathsep.join(
                os.path.abspath(path) for path in env["PYTHONPATH"].split(os.pathsep)
            )
        env.update(extra)
        return env

    # -- parity in miniature ---------------------------------------------------

    def test_records_summary_and_exit_code_match_the_serial_run(self):
        tree = self._tree(MIXED_TREE)
        driver_paths = self.root / "paths-driver"
        serial_paths = self.root / "paths-serial"
        recorder_paths = self.root / "paths-recorder"
        for d in (driver_paths, serial_paths, recorder_paths):
            d.mkdir()
        records = self.root / "driver.jsonl"
        before = _listing(self.cwd)

        driver = self._run(tree, "--records", str(records),
                           env=self._env(PS_SYSPATH_DIR=str(driver_paths)))
        serial = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", str(tree)],
            cwd=self.cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=RUN_TIMEOUT, env=self._env(PS_SYSPATH_DIR=str(serial_paths)))
        serial_records = self.root / "serial.jsonl"
        recorder = subprocess.run(
            [sys.executable, "-c", SERIAL_RECORDER, str(DRIVER), str(tree), str(serial_records)],
            cwd=self.cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=RUN_TIMEOUT, env=self._env(PS_SYSPATH_DIR=str(recorder_paths)))

        self.assertEqual(recorder.returncode, 0, recorder.stderr)
        self.assertEqual(driver.returncode, 1, driver.stderr)
        self.assertEqual(serial.returncode, 1, serial.stderr)
        # The same records, outcome for outcome and reason for reason.
        self.assertEqual(_canonical(records), _canonical(serial_records))
        # The same summary lines as `python -m unittest discover`.
        self.assertEqual(_status_lines(driver.stderr), _status_lines(serial.stderr))
        self.assertTrue(_status_lines(serial.stderr)[1].startswith("FAILED ("))
        # Every outcome class reached the records.
        kinds = {(r[1], r[2]) for r in _canonical(records)}
        for pair in [("test", "ok"), ("test", "fail"), ("test", "error"), ("test", "skip"),
                     ("test", "xfail"), ("test", "uxsuccess"), ("subtest", "fail"),
                     ("subtest", "skip"), ("holder", "skip"), ("holder", "error")]:
            self.assertIn(pair, kinds)
        ids = {r[0] for r in _canonical(records)}
        self.assertIn("unittest.loader._FailedTest.test_e_import", ids)
        self.assertIn("unittest.loader.ModuleSkipped.test_f_modskip", ids)
        self.assertIn("setUpModule (test_d_module)", ids)
        self.assertIn("setUpClass (test_c_classes.BrokenClass)", ids)
        # A worker sees the sys.path a serial run sees, despite the unguarded insert.
        dumps = {}
        for label, d in (("driver", driver_paths), ("serial", serial_paths)):
            files = sorted(d.iterdir())
            self.assertEqual(len(files), 1, (label, files))
            dumps[label] = json.loads(files[0].read_text())
        self.assertEqual(dumps["driver"], dumps["serial"])
        self.assertEqual(dumps["serial"].count(str(tree / "_extra")), 1)
        # The driver wrote nothing into its cwd; --records was its one file.
        self.assertEqual(_listing(self.cwd), before)
        self.assertTrue(records.is_file())

    def test_under_safe_path_a_worker_sees_the_serial_sys_path(self):
        files = {k: v for k, v in MIXED_TREE.items() if k.startswith(("test_g_", "_extra/"))}
        tree = self._tree(files)
        dumps = {}
        for label, argv in (("driver", [str(DRIVER), str(tree), "--workers", "2"]),
                            ("serial", ["-m", "unittest", "discover", "-s", str(tree)])):
            out = self.root / ("paths-" + label)
            out.mkdir()
            run = subprocess.run([sys.executable, "-P", *argv], cwd=self.cwd,
                                 stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                 timeout=RUN_TIMEOUT, env=self._env(PS_SYSPATH_DIR=str(out)))
            self.assertEqual(run.returncode, 0, run.stderr)
            (dump,) = out.iterdir()
            dumps[label] = json.loads(dump.read_text())
        self.assertEqual(dumps["driver"], dumps["serial"])
        self.assertNotIn(str(self.cwd), dumps["serial"])

    def test_child_import_roots_keep_the_parent_launch_directory(self):
        external = str(self.root / "external")
        inherited = os.pathsep.join(("", "crux/scripts", external))
        with mock.patch.dict(os.environ, {"PYTHONPATH": inherited}):
            actual = self._env()["PYTHONPATH"].split(os.pathsep)
        self.assertEqual(actual, [os.getcwd(), os.path.abspath("crux/scripts"), external])

    def test_the_header_names_the_run(self):
        tree = self._tree(MIXED_TREE)
        run = self._run(tree)
        err = run.stderr
        self.assertIn("parallel_suite: python %s" % sys.version.split()[0], err)
        self.assertIn("parallel_suite: executable %s" % sys.executable, err)
        self.assertIn("parallel_suite: prefix %s" % sys.prefix, err)
        self.assertIn("parallel_suite: crux %s" % (tree / "_extra" / "crux" / "__init__.py"), err)
        self.assertRegex(err, r"parallel_suite: discovered 16 tests in \d+ units")
        self.assertRegex(err, r"parallel_suite: timings .*suite_timings\.json \(\d+ entries\)")
        self.assertRegex(err, r"parallel_suite: workers 2 \(2 bins; requested 2, process_cpu_count \d+\)")
        bins = re.findall(r"^parallel_suite: bin (\d+) weight ([\d.]+): (.+)$", err, re.M)
        self.assertEqual([b[0] for b in bins], ["0", "1"])
        # A unit is a module or `module.Class`; every module appears at least once.
        modules = sorted({m.split(".")[0] for b in bins for m in b[2].split()})
        self.assertEqual(modules, sorted(Path(f).stem for f in MIXED_TREE if f.startswith("test_")))

    def test_the_header_says_when_crux_is_not_importable(self):
        tree = self._tree(STDIN_TREE)
        # `-S` drops site-packages, where a project environment can expose
        # `crux`; the spawned workers inherit the flag. The staged gate puts
        # `crux` on PYTHONPATH, so that goes too.
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        run = subprocess.run(
            [sys.executable, "-S", str(DRIVER), str(tree), "--workers", "2"],
            cwd=self.cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=RUN_TIMEOUT, env=env)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("parallel_suite: crux not importable", run.stderr)

    def test_a_worker_reads_stdin_from_devnull(self):
        tree = self._tree(STDIN_TREE)
        run = self._run(tree, stdin=subprocess.PIPE)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(_status_lines(run.stderr), ("Ran 2 tests in T", "OK"))
        # Positive control: the same test fails when fd 0 is the pipe.
        serial = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", str(tree)],
            cwd=self.cwd, stdin=subprocess.PIPE, capture_output=True, text=True,
            timeout=RUN_TIMEOUT)
        self.assertEqual(serial.returncode, 1, serial.stderr)
        self.assertIn("test_fd0_is_devnull", serial.stderr)

    def test_positive_control_a_test_that_writes_into_cwd_is_seen(self):
        tree = self._tree(WRITER_TREE)
        before = _listing(self.cwd)
        run = self._run(tree)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(_listing(self.cwd), before + ["residue.txt"])

    def test_an_empty_tree_exits_2(self):
        tree = self.root / "empty"
        tree.mkdir()
        run = self._run(tree)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertIn("zero-tests", run.stderr)
        self.assertNotIn("Ran ", run.stderr)

    def test_a_worker_that_dies_mid_test_exits_2_naming_the_module(self):
        tree = self._tree(CRASH_TREE)
        run = self._run(tree)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertRegex(run.stderr, r"FAILED CLOSED: bin \d+ \(module test_crash\)")
        self.assertIn("exit code 3 before its payload", run.stderr)
        self.assertNotIn("Ran ", run.stderr)

    def test_a_worker_that_exits_non_zero_after_its_payload_exits_2(self):
        tree = self._tree(LATE_EXIT_TREE)
        run = self._run(tree)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertRegex(run.stderr, r"FAILED CLOSED: bin \d+ \(module test_late_exit\)")
        self.assertIn("exit code 4 after its payload", run.stderr)
        self.assertNotIn("Ran ", run.stderr)

    def test_a_worker_that_exits_while_its_pipe_stays_open_exits_2(self):
        tree = self._tree(FORK_HOLD_TREE)
        pid_dir = self.root / "pids"
        pid_dir.mkdir()
        err_path = self.root / "stderr.txt"
        with open(err_path, "w") as err_file:
            # stderr goes to a file: the forked child holds pipes open, and a
            # capture pipe would keep this test waiting on it.
            proc = subprocess.Popen(
                [sys.executable, str(DRIVER), str(tree), "--workers", "2"],
                cwd=self.cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=err_file, start_new_session=True,
                env=self._env(PS_PID_DIR=str(pid_dir)), preexec_fn=_default_signals())
        self.addCleanup(_kill_group, proc)
        proc.wait(timeout=PROCESS_DEADLINE)
        err = err_path.read_text()
        self.assertEqual(proc.returncode, 2, err)
        self.assertEqual(len(list(pid_dir.iterdir())), 1, "the forked child must exist")
        self.assertRegex(err, r"FAILED CLOSED: bin \d+ \(module test_fork_hold\)")
        self.assertIn("exit code 3 before its payload", err)

    def test_an_exception_inside_the_driver_exits_2(self):
        tree = self._tree(STDIN_TREE)
        # Load the driver, make one of its steps raise, and run its main().
        code = textwrap.dedent("""
            import importlib.util, sys
            spec = importlib.util.spec_from_file_location("ps_raises", sys.argv[1])
            ps = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(ps)
            def broken(*args, **kwargs):
                raise RuntimeError("assign_bins broke")
            ps.assign_bins = broken
            sys.exit(ps.main(sys.argv[2:]))
        """)
        run = subprocess.run(
            [sys.executable, "-c", code, str(DRIVER), str(tree), "--workers", "2"],
            cwd=self.cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=RUN_TIMEOUT, preexec_fn=_default_signals())
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertIn("RuntimeError: assign_bins broke", run.stderr)
        self.assertIn("parallel_suite: FAILED CLOSED: the driver raised", run.stderr)
        self.assertNotIn("Ran ", run.stderr)

    def test_a_manifest_that_differs_in_a_worker_exits_2(self):
        tree = self._tree(MISMATCH_TREE)
        run = self._run(tree)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertRegex(run.stderr, r"FAILED CLOSED: bin \d+ .*manifest-mismatch")
        self.assertIn("test_mp", run.stderr)
        self.assertNotIn("Ran ", run.stderr)

    def test_under_a_top_level_directory_the_affinity_group_shares_one_bin(self):
        tree = self._tree(PACKAGE_TREE)
        empty = self.root / "empty-timings.json"
        empty.write_text("{}\n", encoding="utf-8")
        run = self._run(tree / "ps_pkg", "-t", str(tree), "--timings", str(empty))
        self.assertEqual(run.returncode, 0, run.stderr)
        # Five modules: the package's own `__init__` is one, with no tests.
        self.assertIn("parallel_suite: discovered 4 tests in 5 units", run.stderr)
        bins = re.findall(r"^parallel_suite: bin (\d+) weight ([\d.]+): (.+)$", run.stderr, re.M)
        self.assertEqual(len(bins), 2, run.stderr)
        group = {"ps_pkg." + name for name in ps.AFFINITY[0]}
        holding = [b for b in bins if group & set(b[2].split())]
        self.assertEqual(len(holding), 1, bins)
        self.assertLessEqual(group, set(holding[0][2].split()))
        # With no timings entry for ps_pkg, each unit weighs 1 and the group sums its members.
        self.assertEqual(float(holding[0][1]), float(len(ps.AFFINITY[0])))

    # -- units and measured weights --------------------------------------------

    WIDE_TREE = {
        "test_wide.py": _module(_cls("A", 2), _cls("B", 2), _cls("C", 2)),
        "test_fixture.py": _module(_cls("F1", 2), _cls("F2", 2),
                                   extra="def setUpModule():\n    pass\n\n"),
    }

    @staticmethod
    def _header_bins(stderr):
        rows = re.findall(r"^parallel_suite: bin (\d+) weight [\d.]+: (.+)$", stderr, re.M)
        return {int(index): units.split() for index, units in rows}

    def _timings(self, name, data):
        path = self.root / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_the_classes_of_a_split_module_run_in_different_bins(self):
        tree = self._tree(self.WIDE_TREE)
        timings = self._timings("t.json", {"test_wide.A": 10, "test_wide.B": 10, "test_wide.C": 1,
                                           "test_fixture.F1": 1, "test_fixture.F2": 1})
        run = self._run(tree, "--timings", str(timings))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("Ran 10 tests", run.stderr)
        self.assertIn("discovered 10 tests in 4 units", run.stderr)
        bins = self._header_bins(run.stderr)
        self.assertEqual(bins, {0: ["test_fixture", "test_wide.A"], 1: ["test_wide.B", "test_wide.C"]})

    def test_changing_the_weights_changes_the_bins_and_never_the_tests(self):
        tree = self._tree(self.WIDE_TREE)
        first = self._timings("a.json", {"test_wide.A": 50, "test_wide.B": 1, "test_wide.C": 1})
        second = self._timings("b.json", {"test_wide.A": 1, "test_wide.B": 1, "test_wide.C": 50})
        results = []
        for label, timings in (("first", first), ("second", second)):
            records = self.root / (label + ".jsonl")
            run = self._run(tree, "--timings", str(timings), "--records", str(records))
            self.assertEqual(run.returncode, 0, run.stderr)
            results.append((self._header_bins(run.stderr), _canonical(records), _status_lines(run.stderr)))
        (bins_a, records_a, status_a), (bins_b, records_b, status_b) = results
        self.assertNotEqual(bins_a, bins_b)
        self.assertEqual(records_a, records_b)
        self.assertEqual(status_a, status_b)
        self.assertEqual(len(records_a), 10)

    def test_the_units_of_a_split_module_match_the_serial_run(self):
        files = {"test_cls.py": _module(
            _cls("Plain", 2),
            _cls("Fixture", 2, body=("    @classmethod\n    def setUpClass(cls):\n        pass\n\n"
                                     "    @classmethod\n    def tearDownClass(cls):\n        pass\n\n")),
            _cls("Broken", 1, body=("    @classmethod\n    def setUpClass(cls):\n"
                                    "        raise RuntimeError('class setup broke')\n\n")),
        )}
        tree = self._tree(files)
        records = self.root / "driver.jsonl"
        serial_records = self.root / "serial.jsonl"
        driver = self._run(tree, "--records", str(records))
        serial = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", str(tree)],
            cwd=self.cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=RUN_TIMEOUT)
        recorder = subprocess.run(
            [sys.executable, "-c", SERIAL_RECORDER, str(DRIVER), str(tree), str(serial_records)],
            cwd=self.cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=RUN_TIMEOUT)
        self.assertEqual(recorder.returncode, 0, recorder.stderr)
        self.assertEqual((driver.returncode, serial.returncode), (1, 1), driver.stderr)
        self.assertIn("discovered 5 tests in 3 units", driver.stderr)
        self.assertEqual(_canonical(records), _canonical(serial_records))
        self.assertEqual(_status_lines(driver.stderr), _status_lines(serial.stderr))
        self.assertEqual(_BLOCK_RE.findall(driver.stderr), _BLOCK_RE.findall(serial.stderr))
        self.assertIn("setUpClass (test_cls.Broken)", {r[0] for r in _canonical(records)})

    def test_a_package_module_in_the_affinity_group_stays_whole(self):
        two = _module(_cls("A"), _cls("B"))
        files = {"ps_pkg/__init__.py": ""}
        files.update({"ps_pkg/%s.py" % name: two
                      for name in ("test_arch_corpus", "test_swift_app_gate", "test_other")})
        tree = self._tree(files)
        run = self._run(tree / "ps_pkg", "-t", str(tree))
        self.assertEqual(run.returncode, 0, run.stderr)
        units = sorted(u for us in self._header_bins(run.stderr).values() for u in us)
        # `ps_pkg` is the package's own `__init__`, a unit with no tests.
        self.assertEqual(units, ["ps_pkg", "ps_pkg.test_arch_corpus", "ps_pkg.test_other.A",
                                 "ps_pkg.test_other.B", "ps_pkg.test_swift_app_gate"])

    def test_a_malformed_timings_file_exits_2_before_discovery(self):
        tree = self._tree(self.WIDE_TREE)
        bad = self.root / "bad.json"
        bad.write_text('{"test_wide.A": "slow"}', encoding="utf-8")
        run = self._run(tree, "--timings", str(bad))
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertIn("FAILED CLOSED", run.stderr)
        self.assertIn("malformed-timings", run.stderr)
        self.assertNotIn("discovered", run.stderr)
        self.assertNotIn("Ran ", run.stderr)

    def test_a_missing_timings_file_that_was_named_exits_2(self):
        tree = self._tree(self.WIDE_TREE)
        run = self._run(tree, "--timings", str(self.root / "absent.json"))
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertIn("malformed-timings", run.stderr)
        self.assertNotIn("Ran ", run.stderr)

    def test_write_timings_regenerates_the_file_from_a_records_run(self):
        tree = self._tree(self.WIDE_TREE)
        records = self.root / "run.jsonl"
        run = self._run(tree, "--records", str(records))
        self.assertEqual(run.returncode, 0, run.stderr)
        outputs = []
        for name in ("one.json", "two.json"):
            out = self.root / name
            write = subprocess.run(
                [sys.executable, str(DRIVER), "--write-timings", str(records), "--timings-out", str(out)],
                cwd=self.cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=RUN_TIMEOUT)
            self.assertEqual(write.returncode, 0, write.stderr)
            self.assertIn("wrote 5 timings entries", write.stderr)
            outputs.append(out.read_bytes())
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(sorted(json.loads(outputs[0])), [
            "test_fixture.F1", "test_fixture.F2", "test_wide.A", "test_wide.B", "test_wide.C"])
        # The file it wrote is the file a run reads.
        again = self._run(tree, "--timings", str(self.root / "one.json"))
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("one.json (5 entries)", again.stderr)

    def test_write_timings_with_unusable_records_exits_2_and_writes_nothing(self):
        empty = self.root / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        out = self.root / "out.json"
        write = subprocess.run(
            [sys.executable, str(DRIVER), "--write-timings", str(empty), "--timings-out", str(out)],
            cwd=self.cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=RUN_TIMEOUT)
        self.assertEqual(write.returncode, 2, write.stderr)
        self.assertIn("FAILED CLOSED", write.stderr)
        self.assertFalse(out.exists())

    def test_failure_blocks_print_in_discovery_order(self):
        tree = self._tree(ORDER_TREE)
        # An empty timings file makes every unit weigh 1, so the bins below do not
        # depend on the committed timings.
        uniform = self._timings("uniform.json", {})
        driver = self._run(tree, "--timings", str(uniform))
        serial = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", str(tree)],
            cwd=self.cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=RUN_TIMEOUT)
        self.assertEqual(driver.returncode, 1, driver.stderr)
        self.assertEqual(serial.returncode, 1, serial.stderr)
        # Positive control: the bins interleave, so bin order is not discovery order.
        bins = re.findall(r"^parallel_suite: bin (\d+) weight [\d.]+: (.+)$", driver.stderr, re.M)
        self.assertEqual(bins, [("0", "test_a_order test_c_order test_d_order.DSetup"),
                                ("1", "test_b_order test_d_order.D")])
        blocks = _BLOCK_RE.findall(serial.stderr)
        self.assertEqual([b.split(":")[0] for b in blocks],
                         ["ERROR"] * 6 + ["FAIL"] * 5 + ["UNEXPECTED SUCCESS"] * 2)
        # The subtest failure's block sits with its module's blocks, before b's.
        fails = [b for b in blocks if b.startswith("FAIL")]
        self.assertTrue(fails[1].startswith("FAIL: test_sub (test_a_order.ASub.test_sub) (k=1)"), fails)
        self.assertEqual(_BLOCK_RE.findall(driver.stderr), blocks)
        self.assertEqual(_status_lines(driver.stderr), _status_lines(serial.stderr))

    def test_a_signal_during_discovery_stops_the_run_before_any_worker(self):
        tree = self._tree(TERM_ON_IMPORT_TREE)
        run = self._run(tree)
        self.assertEqual(run.returncode, 128 + signal.SIGTERM, run.stderr)
        self.assertIn("interrupted by signal %d" % signal.SIGTERM, run.stderr)
        self.assertNotIn("parallel_suite: bin ", run.stderr)
        self.assertNotIn("Ran ", run.stderr)

    def _run_hup_tree(self, ignore_hup):
        tree = self._tree(HUP_FROM_WORKER_TREE)
        return subprocess.run(
            [sys.executable, str(DRIVER), str(tree), "--workers", "2"],
            cwd=self.cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=RUN_TIMEOUT,
            preexec_fn=_default_signals(ignored=(signal.SIGHUP,) if ignore_hup else ()))

    def test_an_inherited_ignored_signal_stays_ignored(self):
        # The `nohup` shape: SIGHUP arrives, and the run finishes as the serial run would.
        run = self._run_hup_tree(ignore_hup=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(_status_lines(run.stderr), ("Ran 2 tests in T", "OK"))

    def test_positive_control_the_worker_sighup_reaches_the_coordinator(self):
        run = self._run_hup_tree(ignore_hup=False)
        self.assertEqual(run.returncode, 128 + signal.SIGHUP, run.stderr)
        self.assertIn("interrupted by signal %d" % signal.SIGHUP, run.stderr)

    def test_the_signal_fixtures_do_not_inherit_an_ignored_signal(self):
        # The run under `nohup`, or with SIGTERM ignored. The driver keeps a
        # signal it inherits as ignored, so a fixture that relies on a signal
        # must give its child that signal's default action.
        with self.subTest(signal="SIGHUP"), _ignoring(signal.SIGHUP):
            run = self._run_hup_tree(ignore_hup=False)
            self.assertEqual(run.returncode, 128 + signal.SIGHUP, run.stderr)
        with self.subTest(signal="SIGTERM"), _ignoring(signal.SIGTERM):
            run = self._run(self._tree(TERM_ON_IMPORT_TREE, name="term"))
            self.assertEqual(run.returncode, 128 + signal.SIGTERM, run.stderr)

    def test_sigterm_to_the_coordinator_leaves_no_worker_alive(self):
        tree = self._tree(SLEEP_TREE)
        pid_dir = self.root / "pids"
        pid_dir.mkdir()
        proc = subprocess.Popen(
            [sys.executable, str(DRIVER), str(tree), "--workers", "2"],
            cwd=self.cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, text=True, start_new_session=True,
            env=self._env(PS_PID_DIR=str(pid_dir)), preexec_fn=_default_signals())
        try:
            deadline = time.monotonic() + PROCESS_DEADLINE
            while len(list(pid_dir.iterdir())) < 2 and time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            pids = sorted(int(p.name) for p in pid_dir.iterdir())
            self.assertEqual(len(pids), 2, "both workers must reach their test")
            # Positive control: both pids are live workers before the signal.
            for pid in pids:
                self.assertTrue(_alive(pid), pid)
            proc.send_signal(signal.SIGTERM)
            _, err = proc.communicate(timeout=PROCESS_DEADLINE)
            self.assertEqual(proc.returncode, 128 + signal.SIGTERM)
            self.assertIn("interrupted by signal", err)
            self.assertIn("parallel_suite: worker for bin 0 was running test_sleep_a", err)
            self.assertIn("parallel_suite: worker for bin 1 was running test_sleep_b", err)
            deadline = time.monotonic() + PROCESS_DEADLINE
            while any(_alive(pid) for pid in pids) and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertEqual([pid for pid in pids if _alive(pid)], [])
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            if proc.poll() is None:
                proc.wait(timeout=PROCESS_DEADLINE)
            if proc.stderr is not None:
                proc.stderr.close()

    def _stop_run(self, files, stop):
        """Run the driver over `files` until every test child exists, call
        `stop(proc)`, and return (exit code, stderr, child pids still alive)."""
        tree = self._tree(files)
        pid_dir = self.root / "pids"
        pid_dir.mkdir()
        err_path = self.root / "stderr.txt"
        with open(err_path, "w") as err_file:
            # stderr goes to a file: a child that outlives its worker holds
            # the worker's stderr open, and a capture pipe would wait on it.
            proc = subprocess.Popen(
                [sys.executable, str(DRIVER), str(tree), "--workers", "2"],
                cwd=self.cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=err_file, start_new_session=True,
                env=self._env(PS_PID_DIR=str(pid_dir)), preexec_fn=_default_signals())
        wanted = sum(1 for body in files.values() if body is _CHILD_THEN_SLEEP)
        children = []
        try:
            deadline = time.monotonic() + PROCESS_DEADLINE
            while len(list(pid_dir.glob("child-*"))) < wanted and time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            children = sorted(int(p.name.split("-")[1]) for p in pid_dir.glob("child-*"))
            self.assertEqual(len(children), wanted, "every test must start its child")
            # Positive control: every child is alive before the stop.
            for pid in children:
                self.assertTrue(_alive(pid), pid)
            stop(proc)
            proc.wait(timeout=PROCESS_DEADLINE)
            deadline = time.monotonic() + PROCESS_DEADLINE
            while any(_alive(pid) for pid in children) and time.monotonic() < deadline:
                time.sleep(0.1)
            return proc.returncode, err_path.read_text(), [pid for pid in children if _alive(pid)]
        finally:
            for pid in children:
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            _kill_group(proc)

    def test_sigterm_to_the_coordinator_stops_the_processes_tests_started(self):
        code, err, alive = self._stop_run(ORPHAN_TREE, lambda proc: proc.send_signal(signal.SIGTERM))
        self.assertEqual(alive, [], err)
        self.assertEqual(code, 128 + signal.SIGTERM, err)
        self.assertIn("parallel_suite: worker for bin 0 was running test_orphan_a", err)

    def test_a_fail_closed_stop_stops_the_processes_tests_started(self):
        go = self.root / "pids" / "go"
        code, err, alive = self._stop_run(ORPHAN_ON_CRASH_TREE, lambda proc: go.touch())
        self.assertEqual(alive, [], err)
        self.assertEqual(code, 2, err)
        self.assertRegex(err, r"FAILED CLOSED: bin 0 \(module test_a_crash\)")
        self.assertIn("parallel_suite: worker for bin 1 was running test_b_child", err)


# The signals the driver handles. A child inherits the caller's disposition of
# each, and the driver keeps a signal it inherits as ignored.
_DRIVER_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def _default_signals(ignored=()):
    """A preexec_fn that gives the child the default action for every driver
    signal except those in `ignored`, which it ignores. Without it, a run
    under `nohup` or with SIGTERM ignored changes what a fixture observes."""
    def preexec():
        for signum in _DRIVER_SIGNALS:
            signal.signal(signum, signal.SIG_IGN if signum in ignored else signal.SIG_DFL)
    return preexec


@contextlib.contextmanager
def _ignoring(signum):
    """Ignore `signum` in this process for the duration, as `nohup` does."""
    previous = signal.signal(signum, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signum, previous)


def _kill_group(proc) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    if proc.poll() is None:
        proc.wait(timeout=PROCESS_DEADLINE)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# -- the interpreter preflight -------------------------------------------------------------
#
# A bare `uv run <this driver>` builds an isolated interpreter from the driver's PEP 723 block,
# which declares no dependency. Discovery then failed on an import, and the run exited 1 (the
# test-failure lane) for a fault in the environment. The driver now probes the interpreter its
# workers use, before discovery, and exits 2 naming the working command.

SUITE_COMMAND = "uv run python3 crux/scripts/tests/parallel_suite.py crux/scripts/tests"
REPO = HERE.parents[2]


def _clean_env() -> dict:
    env = dict(os.environ)
    for key in ("PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME"):
        env.pop(key, None)
    return env


def _make_venv(parent: Path, name: str) -> Path:
    """A stdlib venv with no pip and no third-party package; returns its interpreter."""
    target = parent / name
    venv.create(target, with_pip=False, symlinks=True)
    return target / "bin" / "python3"


def _provide(python: Path, parent: Path, packages: dict) -> None:
    """Make `packages` (import name -> package directory) importable from `python` and nothing
    else: a `.pth` file names a directory of symlinks, so no network and no other module of the
    running environment becomes visible."""
    extra = parent / f"{python.parent.parent.name}-extra"
    extra.mkdir()
    for name, source in packages.items():
        (extra / name).symlink_to(source, target_is_directory=True)
    purelib = subprocess.run(
        [str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        capture_output=True, text=True, check=True, env=_clean_env()).stdout.strip()
    (Path(purelib) / "extra.pth").write_text(f"{extra}\n", encoding="utf-8")


def _can_import(python, module: str, *flags: str) -> bool:
    return subprocess.run([str(python), *flags, "-c", f"import {module}"], capture_output=True,
                          env=_clean_env()).returncode == 0


def third_party_imports(entries, scripts_dir: Path, count_guarded: bool = True,
                        local_dirs: tuple[Path, ...] = ()) -> dict:
    """Map each third-party module imported at module level to the `path:line` sites.

    Reads `entries` and, transitively, every module under `scripts_dir` (or its `tests/`) that
    they import. Imports inside a function or a class body are not read. A module is third party
    when it is neither stdlib nor resolvable under `scripts_dir` or the
    explicitly supplied repository helper directories in `local_dirs`.

    An import is guarded when it sits under a module-level `if`, or in the body, a handler or
    the `else` of a `try` that catches ImportError (a handler naming ImportError,
    ModuleNotFoundError, Exception or BaseException, or a bare `except`). A `finally` body
    always runs and is not guarded. With `count_guarded` false, a guarded import is optional:
    it is not reported, and a guarded local module is not followed. The suite's inventory
    passes false, because its tests skip where such a module is absent.
    """
    import ast

    bases = [scripts_dir, scripts_dir / "tests", *local_dirs]
    sites: dict = {}
    seen: set = set()

    def resolve(dotted: str, extra: tuple = ()) -> list:
        files = []
        for base in bases:
            path = base
            names = dotted.split(".")
            for i, part in enumerate(names):
                if (path / part).is_dir():
                    path = path / part
                    if (path / "__init__.py").is_file():
                        files.append(path / "__init__.py")
                elif (path / f"{part}.py").is_file():
                    files.append(path / f"{part}.py")
                    path = None
                    break
                else:
                    path = None
                    break
            if path is not None:
                for name in extra:
                    if (path / f"{name}.py").is_file():
                        files.append(path / f"{name}.py")
            if files:
                return files
        return files

    import_guards = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}

    def catches_import_error(node) -> bool:
        for handler in node.handlers:
            if handler.type is None:
                return True
            kinds = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
            if any(isinstance(k, ast.Name) and k.id in import_guards for k in kinds):
                return True
        return False

    def statements(body, guarded=False):
        for node in body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                yield node, guarded
            elif isinstance(node, ast.Try):
                inner = guarded or catches_import_error(node)
                yield from statements(node.body, inner)
                for handler in node.handlers:
                    yield from statements(handler.body, inner)
                yield from statements(node.orelse, inner)
                yield from statements(node.finalbody, guarded)
            elif isinstance(node, ast.If):
                yield from statements(node.body, True)
                yield from statements(node.orelse, True)

    def visit(path: Path) -> None:
        if path in seen:
            return
        seen.add(path)
        for node, guarded in statements(ast.parse(path.read_text(encoding="utf-8")).body):
            if guarded and not count_guarded:
                continue
            if isinstance(node, ast.Import):
                targets = [(a.name, ()) for a in node.names]
            elif node.level == 0 and node.module:
                targets = [(node.module, tuple(a.name for a in node.names))]
            else:
                continue
            for dotted, names in targets:
                top = dotted.split(".")[0]
                found = resolve(dotted, names)
                if found:
                    for f in found:
                        visit(f)
                elif top not in sys.stdlib_module_names and top != "__future__":
                    sites.setdefault(top, []).append(f"{path}:{node.lineno}")

    for entry in entries:
        visit(Path(entry))
    return {k: sorted(v) for k, v in sorted(sites.items())}


class PluginSuiteInterpreterTests(unittest.TestCase):
    """The driver refuses, at exit 2 and before discovery, an interpreter that cannot import the
    plugin suite's third-party modules.

    Hermetic fixture: a stdlib `venv.create(with_pip=False)` interpreter reproduces the isolated
    interpreter with no network. By hand: `python3 -c "import venv; venv.create('/tmp/bare',
    with_pip=False)"` and then `/tmp/bare/bin/python3 crux/scripts/tests/parallel_suite.py
    crux/scripts/tests -p test_arch_facts.py` from the repo root. The narrow pattern keeps the
    unfixed run short.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        parent = Path(cls._tmp.name)
        cls.bare = _make_venv(parent, "bare")
        cls.yaml_only = _make_venv(parent, "yaml-only")
        import importlib.util as _iu
        spec = _iu.find_spec("yaml")
        if spec is None:
            raise unittest.SkipTest("the running interpreter has no yaml to provision")
        _provide(cls.yaml_only, parent, {"yaml": Path(spec.origin).parent})

    def _run(self, python, *argv, cwd=None):
        return subprocess.run([str(python), str(DRIVER), *argv], cwd=cwd or REPO,
                              capture_output=True, text=True, env=_clean_env(),
                              timeout=RUN_TIMEOUT)

    def _assert_refused(self, proc, missing: str):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertIn("parallel_suite: FAILED CLOSED", proc.stderr)
        self.assertIn(SUITE_COMMAND, proc.stderr)
        self.assertIn(f"cannot import: {missing}.", proc.stderr)
        self.assertIsNone(_RAN_RE.search(proc.stderr), "discovery or a run began before the refusal")

    def test_the_fixtures_are_what_they_claim(self):
        """Control: the bare venv imports none of the suite's modules; the yaml-only venv has
        yaml alone."""
        for module in ps.PLUGIN_SUITE_MODULES:
            self.assertFalse(_can_import(self.bare, module), module)
        self.assertTrue(_can_import(self.yaml_only, "yaml"))
        self.assertFalse(_can_import(self.yaml_only, "httpx"))
        self.assertFalse(_can_import(self.yaml_only, "griffe"))

    def test_an_interpreter_without_the_suite_dependencies_is_refused(self):
        proc = self._run(self.bare, str(HERE), "-p", "test_arch_facts.py")
        self._assert_refused(proc, ", ".join(ps.PLUGIN_SUITE_MODULES))
        self.assertIn(str(self.bare), proc.stderr)

    def test_a_partly_provisioned_interpreter_names_only_the_missing_modules(self):
        proc = self._run(self.yaml_only, str(HERE), "-p", "test_arch_facts.py")
        self._assert_refused(proc, "httpx, griffe")

    def test_the_relative_and_dot_slash_forms_target_the_own_tests_directory(self):
        for form in ("crux/scripts/tests", "./crux/scripts/tests", "crux/scripts/tests/"):
            with self.subTest(form=form):
                proc = self._run(self.bare, form, "-p", "test_arch_facts.py")
                self._assert_refused(proc, ", ".join(ps.PLUGIN_SUITE_MODULES))
        with self.subTest(form="."):
            proc = self._run(self.bare, ".", "-p", "test_arch_facts.py", cwd=HERE)
            self._assert_refused(proc, ", ".join(ps.PLUGIN_SUITE_MODULES))

    def test_a_symlinked_route_to_the_own_tests_directory_is_recognised(self):
        link = Path(self._tmp.name) / "tests-link"
        if not link.exists():
            link.symlink_to(HERE, target_is_directory=True)
        proc = self._run(self.bare, str(link), "-p", "test_arch_facts.py")
        self._assert_refused(proc, ", ".join(ps.PLUGIN_SUITE_MODULES))

    def test_a_foreign_start_directory_is_not_probed(self):
        """The scoping control: the driver's own toy-tree tests run under interpreters that lack
        the plugin suite's modules, and must keep running."""
        tree = _write_tree(Path(self._tmp.name) / "toy", {
            "test_toy.py": """
                import unittest
                class T(unittest.TestCase):
                    def test_ok(self):
                        self.assertTrue(True)
            """})
        proc = self._run(self.bare, str(tree), "--workers", "1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("FAILED CLOSED", proc.stderr)

    def test_help_and_argument_errors_keep_their_exit_codes(self):
        helped = self._run(self.bare, "--help")
        self.assertEqual(helped.returncode, 0)
        self.assertIn("usage:", helped.stdout)
        bad = self._run(self.bare, str(HERE), "--workers", "0")
        self.assertEqual(bad.returncode, 2)
        self.assertIn("usage:", bad.stderr)
        self.assertNotIn("FAILED CLOSED", bad.stderr)

    def test_the_probe_argv_is_the_one_the_workers_are_spawned_with(self):
        """Discovery runs in the coordinator; workers are spawned children that inherit the
        coordinator's interpreter flags. The probe takes the spawn context's own argv prefix."""
        import multiprocessing.spawn as mp_spawn
        command = mp_spawn.get_command_line()
        prefix = command[:command.index("-c")]
        self.assertEqual(ps._worker_interpreter_argv(), prefix)
        seen = {}

        def fake(argv, **kwargs):
            seen["argv"], seen["kwargs"] = list(argv), kwargs
            return subprocess.CompletedProcess(argv, 0, stdout=ps._PROBE_MARKER + "\n",
                                               stderr="")

        with mock.patch.object(ps.subprocess, "run", side_effect=fake):
            self.assertEqual(ps._missing_modules(("yaml",)), [])
        self.assertEqual(seen["argv"][:len(prefix)], prefix)
        self.assertEqual(seen["argv"][len(prefix)], "-c")

    def test_an_interpreter_flag_that_hides_site_packages_reaches_the_probe(self):
        """`-S` reaches the workers, so a coordinator run under it has workers with no
        site-packages. The probe must see what the workers see."""
        if _can_import(sys.executable, "yaml", "-S"):
            self.skipTest("-S does not hide yaml from this interpreter")
        proc = subprocess.run([sys.executable, "-S", str(DRIVER), str(HERE), "-p",
                               "test_arch_facts.py"], cwd=REPO, capture_output=True, text=True,
                              timeout=RUN_TIMEOUT)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("parallel_suite: FAILED CLOSED", proc.stderr)
        self.assertIn("yaml", proc.stderr)

    def test_a_capable_interpreter_passes_the_probe(self):
        """Positive control for the refusals above: the running interpreter can host the suite,
        so the probe reports nothing missing."""
        self.assertEqual(ps._missing_modules(ps.PLUGIN_SUITE_MODULES), [])

    def test_startup_output_is_not_read_as_a_missing_module(self):
        """An interpreter that prints at startup (here a sitecustomize) is still capable: only
        the probe's marked result line names missing modules."""
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "sitecustomize.py").write_text(
                "import sys\nsys.stdout.write('startup-noise\\n')\n", encoding="utf-8")
            env = dict(os.environ, PYTHONPATH=tmp)
            noisy = subprocess.run([sys.executable, "-c", "pass"], capture_output=True,
                                   text=True, env=env).stdout
            self.assertIn("startup-noise", noisy, "the fixture does not print at startup")
            with mock.patch.dict(os.environ, {"PYTHONPATH": tmp}):
                self.assertEqual(ps._missing_modules(ps.PLUGIN_SUITE_MODULES), [])


class SuiteDependencyInventoryTests(unittest.TestCase):
    """The probe set is the inventory of third-party modules the discovered suite imports at
    module level. A test that gains a new one would otherwise pass the probe and fail at import
    under an interpreter that lacks it. Modules the tests skip on when absent (the tree-sitter
    grammars) are imported inside test bodies and are not part of the inventory."""

    def test_every_module_level_third_party_import_of_the_suite_is_probed(self):
        entries = sorted(HERE.glob("test_*.py"))
        self.assertGreater(len(entries), 50)
        found = third_party_imports(entries, HERE.parent, count_guarded=False,
                                    local_dirs=(REPO / "tools" / "tests",))
        missing = sorted(set(found) - set(ps.PLUGIN_SUITE_MODULES))
        self.assertEqual(missing, [], "suite imports missing from PLUGIN_SUITE_MODULES: "
                         + "; ".join(f"{m} at {found[m][:2]}" for m in missing))

    def test_the_probe_set_names_the_measured_suite_dependencies(self):
        """Measured by running discovery in a stdlib venv: yaml (2 modules), griffe (4) and httpx
        (41, through the eager `crux` package import)."""
        self.assertEqual(set(ps.PLUGIN_SUITE_MODULES), {"yaml", "httpx", "griffe"})

    def test_the_scan_finds_a_seeded_import(self):
        """Positive control: the scanner reports imports it should report, guarded or not, and
        follows a local module and a package `__init__`."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        scripts = Path(tmp.name)
        (scripts / "pkg").mkdir()
        (scripts / "pkg" / "__init__.py").write_text("import from_init\n", encoding="utf-8")
        (scripts / "entry.py").write_text("import helper\nimport seeded_pkg\nimport pkg.sub\n"
                                          "import os\n", encoding="utf-8")
        (scripts / "pkg" / "sub.py").write_text("def f():\n    import inside_function\n",
                                                encoding="utf-8")
        (scripts / "helper.py").write_text("try:\n    import guarded_seed\nexcept ImportError:\n"
                                           "    pass\n", encoding="utf-8")
        found = third_party_imports([scripts / "entry.py"], scripts)
        self.assertEqual(sorted(found), ["from_init", "guarded_seed", "seeded_pkg"])

    def test_repository_helpers_are_followed_without_hiding_their_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            scripts = Path(tmp) / "scripts"
            helpers = Path(tmp) / "tools" / "tests"
            scripts.mkdir()
            helpers.mkdir(parents=True)
            entry = scripts / "entry.py"
            entry.write_text("import repository_helper\nimport absent_dependency\n")
            (helpers / "repository_helper.py").write_text("import helper_dependency\n")
            found = third_party_imports([entry], scripts, local_dirs=(helpers,))
            self.assertEqual(sorted(found), ["absent_dependency", "helper_dependency"])

    def test_a_guarded_module_level_import_is_not_a_hard_import(self):
        """An import the module guards with `try`/`except ImportError`, or places under a
        module-level `if`, is optional: its tests skip where it is absent, so the probe must not
        refuse the interpreter for it. A `try` that does not catch ImportError guards nothing,
        and a `finally` body always runs."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        scripts = Path(tmp.name)
        (scripts / "entry.py").write_text(
            "import hard_seed\n"
            "import optional_helper\n"
            "try:\n    import guarded_by_importerror\n"
            "except ImportError:\n    import fallback_in_handler\n"
            "else:\n    import guarded_in_else\n"
            "finally:\n    import hard_in_finally\n"
            "try:\n    import guarded_by_tuple\nexcept (ValueError, ModuleNotFoundError):\n    pass\n"
            "try:\n    import guarded_by_exception\nexcept Exception:\n    pass\n"
            "try:\n    import local_optional\nexcept ImportError:\n    pass\n"
            "try:\n    import hard_not_guarded\nexcept ValueError:\n    pass\n"
            "if True:\n    import guarded_by_if\nelse:\n    import guarded_by_else\n",
            encoding="utf-8")
        (scripts / "optional_helper.py").write_text("import hard_via_helper\n", encoding="utf-8")
        (scripts / "local_optional.py").write_text("import reached_only_when_guarded\n",
                                                  encoding="utf-8")
        found = third_party_imports([scripts / "entry.py"], scripts, count_guarded=False)
        self.assertEqual(sorted(found), ["hard_in_finally", "hard_not_guarded", "hard_seed",
                                         "hard_via_helper"])


if __name__ == "__main__":
    unittest.main()
