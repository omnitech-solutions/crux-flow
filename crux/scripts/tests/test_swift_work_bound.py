"""The Xcode project readers' per-concern work bound
(`swift_xcinputs.MAX_WORK_UNITS`): ADR-0129 clauses 2 and 7 (no content
makes the derive exit 2 or hang, and a scan limit renders `scan-cap`) and
ADR-0130 clause 2.

Each amplifier test counts logical work units from the input -- listings,
path checks, item evaluations, reuses, joins -- through
`CollectionBudget.work_spent`, never time or syscalls, and pins what one
more item costs under the six charge rules (`swift_xcinputs.MAX_WORK_UNITS`
names them L1 to L6). A trip test drives a hostile shape past a patched
limit and checks the one `scan-cap` line and what still renders. Every
fixture is written fresh here.

The per-item constants follow ruling C: a path check of K repo-relative
segments charges `l2_units(K)` = 1 + K(K + 1)/2 (L2 quadratic), and a memo
reuse charges 1 plus the units its first computation spent outside L1, L4
and L6 (L6 excludes L1/L4/L6). For K = 1 the two L2 rules agree (2), so a
constant built only from one-segment checks did not move.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _timing  # noqa: E402
from test_swift_xcode import _mkfile, _obj, _serialize, _synced_objects  # noqa: E402

from crux.arch.packs import swift  # noqa: E402
from crux.arch.packs import swift_xcinputs as xi  # noqa: E402
from crux.arch.packs import swift_xcode as sx  # noqa: E402

import test_swift_xcode as _tx  # noqa: E402  (a module: its test classes are not collected here)

APP = "com.apple.product-type.application"


def _work_text(limit=None):
    """The work bound's detail at `limit`, the real bound by default."""
    return xi.WORK_BOUND_TEMPLATE.format(limit=xi.MAX_WORK_UNITS if limit is None else limit)
SCRIPTS = Path(__file__).resolve().parent.parent


def _pbxproj_text(objects):
    doc_map = {"archiveVersion": "1", "objectVersion": "56", "rootObject": "PROJ", "objects": objects}
    return "// !$*UTF8*$!\n" + _serialize(doc_map) + "\n"


def _write_project(root, objects, bundle="X.xcodeproj"):
    raw = _pbxproj_text(objects).encode("utf-8")
    _mkfile(root / bundle / "project.pbxproj", raw)
    return raw


def _read(root, objects, limit=None, manifests=(), workspace_facts=None):
    """`read_projects` over one bundle `X.xcodeproj` holding `objects`, with a
    fresh budget whose work limit is `limit` when one is given. Returns the
    result and the budget."""
    raw = _write_project(root, objects)
    reads = SimpleNamespace(bundles=[("X.xcodeproj", True, True)], refusals={},
                            files={"X.xcodeproj/project.pbxproj": raw})
    budget = xi.CollectionBudget()
    if limit is not None:
        budget.work_limit = limit
    return sx.read_projects(root, reads, list(manifests), workspace_facts, budget), budget


def _spent(build, n, limit=None):
    """`build(root, n)` -> objects, in a fresh checkout; the work it spends."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        objects = build(root, n)
        _result, budget = _read(root, objects, limit)
    return budget.work_spent


def _spent_excluded(build, n):
    """`_spent`, with the part of it charged through `charge_excluded`."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        objects = build(root, n)
        _result, budget = _read(root, objects)
    return budget.work_spent, budget.work_excluded


def _work_lines(result):
    return [r for r in result["residuals"] if r[3].startswith("the Xcode project readers pass")]


def _classic_objects(files, group_path="Src", file_path="missing.swift", targets=1):
    """One group at `group_path` holding one file reference `F` at
    `file_path`; one Sources phase listing its build file `files` times,
    shared by `targets` targets."""
    o = {}
    o["PROJ"] = _obj("PBXProject", mainGroup="MAIN", targets=["T%d" % i for i in range(targets)])
    o["MAIN"] = _obj("PBXGroup", sourceTree="<group>", children=["G"])
    o["G"] = _obj("PBXGroup", sourceTree="<group>", path=group_path, children=["F"])
    o["F"] = _obj("PBXFileReference", sourceTree="<group>", path=file_path)
    o["B"] = _obj("PBXBuildFile", fileRef="F")
    o["SP"] = _obj("PBXSourcesBuildPhase", files=["B"] * files)
    for i in range(targets):
        o["T%d" % i] = _obj("PBXNativeTarget", name="A%d" % i, productType=APP, buildPhases=["SP"])
    return o


class CollectionBudgetWorkTests(unittest.TestCase):
    """The work bound, the third bound inside `CollectionBudget`."""

    def test_the_constants_are_pinned(self):
        self.assertEqual(xi.MAX_WORK_UNITS, 4_194_304)
        self.assertEqual(xi.WORK_BOUND_TEMPLATE,
                         "the Xcode project readers pass the per-concern work bound of {limit} units; "
                         "later project references are not read")

    def test_work_admits_up_to_the_limit_and_refuses_past_it(self):
        budget = xi.CollectionBudget()
        budget.work_limit = 10
        self.assertTrue(budget.work(4))
        self.assertTrue(budget.work(6))
        self.assertEqual((budget.work_spent, budget.work_tripped), (10, False))
        self.assertFalse(budget.work(1))
        self.assertEqual((budget.work_spent, budget.work_tripped), (10, True))

    def test_a_refusal_is_monotone(self):
        budget = xi.CollectionBudget()
        budget.work_limit = 10
        self.assertFalse(budget.work(11))
        self.assertFalse(budget.work(0))
        self.assertFalse(budget.work(1))
        self.assertEqual(budget.work_spent, 0)

    def test_the_line_names_the_root_and_no_range(self):
        budget = xi.CollectionBudget()
        self.assertIsNone(budget.work_line())
        budget.work_limit = 0
        budget.work(1)
        self.assertEqual(budget.work_line(),
                         ("scan-cap", ".", None, xi.WORK_BOUND_TEMPLATE.format(limit=0)))
        self.assertIn("4194304 units", _work_text())

    def test_the_three_bounds_trip_alone(self):
        budget = xi.CollectionBudget()
        budget.work_limit = 0
        budget.work(1)
        self.assertIsNone(budget.detail_line())
        self.assertIsNone(budget.membership_line())
        self.assertTrue(budget.detail("p", None, "x"))
        self.assertTrue(budget.membership("p", None))

    def test_charge_raises_only_on_a_refusal(self):
        budget = xi.CollectionBudget()
        budget.work_limit = 2
        xi.charge(budget, 2)
        xi.charge(None, 10 ** 9)
        with self.assertRaises(xi.WorkBoundExceeded):
            xi.charge(budget, 1)


def _members_objects(p, group_path="Src"):
    """One group at `group_path` holding P file references `f0000.swift`,
    ..., each with one build file in one Sources phase of target `T`."""
    o = {}
    o["PROJ"] = _obj("PBXProject", mainGroup="MAIN", targets=["T"])
    o["MAIN"] = _obj("PBXGroup", sourceTree="<group>", children=["G"])
    o["G"] = _obj("PBXGroup", sourceTree="<group>", path=group_path, children=["F%d" % i for i in range(p)])
    for i in range(p):
        o["F%d" % i] = _obj("PBXFileReference", sourceTree="<group>", path="f%04d.swift" % i)
        o["B%d" % i] = _obj("PBXBuildFile", fileRef="F%d" % i)
    o["SP"] = _obj("PBXSourcesBuildPhase", files=["B%d" % i for i in range(p)])
    o["T"] = _obj("PBXNativeTarget", name="App", productType=APP, buildPhases=["SP"])
    return o


def _members_tree(root, p, group_path="Src"):
    for i in range(p):
        _mkfile(root / group_path / ("f%04d.swift" % i))


class ClassicChargeTests(unittest.TestCase):
    """The classic route: what one more member, one
    more listed entry, one more directory level and one more repeat cost.
    A file name `f0000.swift` is 11 characters."""

    def test_one_more_member_of_one_directory(self):
        """One more member `Src/fNNNN.swift` costs 26: descent charges its
        parent 1 (L3) and its 15-character path (L5); the build file charges
        1 (L3) and four path checks of `Src`, one segment, at 1 + 1 each
        (L2: symlink walk, listed-name walk, containment, leaf walk); and
        `Src`, listed once for the call, holds one more entry (L1)."""
        def build(root, p):
            _members_tree(root, p)
            return _members_objects(p)
        self.assertEqual(_spent(build, 80) - _spent(build, 40), 40 * (1 + 15 + 1 + 4 * 2 + 1))

    def test_l1_a_directory_is_charged_its_entries_once_per_call(self):
        """Twenty members of `Src` read it once: one more entry costs one
        unit, not one per member (L1)."""
        def build(root, d):
            _members_tree(root, 20)
            for i in range(d):
                _mkfile(root / "Src" / ("x%d.txt" % i))
            return _members_objects(20)
        self.assertEqual(_spent(build, 60) - _spent(build, 30), 30)

    def test_a_path_check_charges_its_quadratic_units(self):
        """A member K segments deep costs 2K + 11 + 4 * l2_units(K) + 3: its
        2K + 11-character path (L5), four path checks of its K-segment parent
        at 1 + K(K + 1)/2 (L2), and 1 + 1 + 1 for its parent's child, its
        build file and its listed entry. Work grows with K and never with the
        checkout's absolute depth."""
        def at_depth(k):
            path = "/".join(["d"] * k)

            def build(root, p):
                _members_tree(root, p, path)
                return _members_objects(p, group_path=path)
            return _spent(build, 40) - _spent(build, 20)
        for k in (1, 3, 7):
            self.assertEqual(at_depth(k), 20 * (2 * k + 11 + 4 * (1 + k * (k + 1) // 2) + 3), k)

    def test_l6_a_repeated_build_file_charges_the_first_reading_again(self):
        """A build file naming a reference already read reuses its verdict
        and charges 1 (L3) plus 1 and the units that first reading spent
        outside its listings (L6): its four path checks of `Src` (8). The
        root's listing and `Src`'s are not performed again, and are never
        charged again, however many entries `Src` holds."""
        def build(n):
            def objects(root, m):
                for i in range(n):
                    _mkfile(root / "Src" / ("f%d.txt" % i))
                return _classic_objects(m)
            return objects
        for n in (5, 60):
            self.assertEqual(_spent(build(n), 400) - _spent(build(n), 200), 200 * (1 + 1 + 8), n)

    def test_targets_sharing_a_phase_charge_each_build_file_again(self):
        """T targets sharing one Sources phase of 25 build files naming one
        file in a directory of N entries: one more target costs
        25 * (L3 + 1 + L2) = 25 * (1 + 1 + 8), each build file charged its
        evaluation and 1 plus the kept verdict's four path checks of `Src`
        (L3, L6). The directory's listing is charged once for the call,
        never once per target: the cost is the same for N = 3 and N = 60."""
        def build(n):
            def objects(root, t):
                for i in range(n):
                    _mkfile(root / "Src" / ("f%d.txt" % i))
                return _classic_objects(25, targets=t)
            return objects
        for n in (3, 60):
            self.assertEqual(_spent(build(n), 8) - _spent(build(n), 4), 4 * 25 * (1 + 1 + 8), n)
        # Each reuse is charged through `charge_excluded`: 1 + 8 of the
        # 10 units per build file is excluded, the L3 unit is not.
        (s8, e8), (s4, e4) = _spent_excluded(build(3), 8), _spent_excluded(build(3), 4)
        self.assertEqual((e8 - e4, (s8 - e8) - (s4 - e4)), (4 * 25 * (1 + 8), 4 * 25 * 1))


class SyncedChargeTests(unittest.TestCase):
    """The folder-synced route: `/Localized/` entries, directory entries,
    roots naming one directory, build-phase exception sets and default rows
    per owner. The
    root is `S`, one segment; its exception set `E` is targeted at `T0`."""

    def test_one_more_localized_entry(self):
        """One more `/Localized/L/x.swift` entry over D empty `.lproj`
        directories costs 37 + 53D: 1 for the entry (L3); its two classifying
        joins, `S/L` (3) and `S/L/_/x.swift` (13), and the resolver's `S/L`
        (3) (L5); three path checks of `S/L` at l2_units(2) = 4 (L2); the
        kept `.lproj` names, charged 1 plus the 4 units their first reading
        spent outside its listing of D entries (L6); and per `.lproj` 1 (L3),
        its 24-character path (L5) and four path checks of its 3-segment
        parent at l2_units(3) = 7 (L2)."""
        def build(root, e):
            for i in range(4):
                (root / "S" / "L" / ("l%05d.lproj" % i)).mkdir(parents=True)
            return _synced_objects(["/Localized/L/x.swift"] * e)
        self.assertEqual(_spent(build, 40) - _spent(build, 20), 20 * (37 + 53 * 4))

    def test_one_more_directory_entry_reuses_its_scan(self):
        """One more entry `sub`, a directory holding two `.swift` files and
        other files, costs 1 (L3), its 5-character join (L5), three path
        checks of `S/sub` at l2_units(2) = 4 (L2), the kept scan's length 2
        (L4), and each file's 14-character path `S/sub/aN.swift` (L5)."""
        def build(root, e):
            for i in range(2):
                _mkfile(root / "S" / "sub" / ("a%d.swift" % i))
            for i in range(30):
                _mkfile(root / "S" / "sub" / ("x%d.txt" % i))
            return _synced_objects(["sub"] * e)
        self.assertEqual(_spent(build, 40) - _spent(build, 20), 20 * (1 + 5 + 12 + 2 + 2 * 14))

    def test_one_more_root_naming_one_directory(self):
        """One more root `S` owned by the one target costs 1 for its parent's
        child and 1 for its 1-character path in descent; its symlink verdict
        (2) and three path checks (6) at 1 + 1 (L2); the kept scan's length 3
        (L4); each file's 10-character path `S/aN.swift` (L5); and each
        default row 1 (L3)."""
        def build(root, r):
            for i in range(3):
                _mkfile(root / "S" / ("a%d.swift" % i))
            o = {}
            o["PROJ"] = _obj("PBXProject", mainGroup="MAIN", targets=["T"])
            o["MAIN"] = _obj("PBXGroup", sourceTree="<group>", children=["SR%d" % i for i in range(r)])
            for i in range(r):
                o["SR%d" % i] = _obj("PBXFileSystemSynchronizedRootGroup", sourceTree="<group>", path="S")
            o["T"] = _obj("PBXNativeTarget", name="App", productType=APP,
                          fileSystemSynchronizedGroups=["SR%d" % i for i in range(r)])
            return o
        self.assertEqual(_spent(build, 40) - _spent(build, 20), 20 * (2 + 2 + 6 + 3 + 3 * 10 + 3))

    def test_l1_a_scanned_directory_is_charged_its_entries_once(self):
        """One more entry in the scanned root costs one unit (L1)."""
        def build(root, d):
            _mkfile(root / "S" / "Own.swift")
            for i in range(d):
                _mkfile(root / "S" / ("x%d.txt" % i))
            return _synced_objects([])
        self.assertEqual(_spent(build, 60) - _spent(build, 30), 30)

    def test_a_dangling_build_phase_set_charges_each_entry_once(self):
        """A build-phase set whose `buildPhase` names no owner withholds each
        entry from every owner: one more entry costs 1 (L3) and its
        16-character path `S/aNNNNNNN.swift` (L5), however many owners share
        the root; one more owner costs only its test in the set's owner scan
        (L3)."""
        def build(e, t):
            def objects(root, _n):
                (root / "S").mkdir()
                o = _synced_objects(["a%07d.swift" % i for i in range(e)],
                                    exc_isa=sx._BUILD_PHASE_EXCEPTION_SET_ISA, owners=t,
                                    exc_fields={"buildPhase": "NOPE"})
                return o
            return _spent(objects, 0)
        self.assertEqual(build(80, 30) - build(40, 30), 40 * (1 + 16))
        self.assertEqual(build(40, 60) - build(40, 30), 30)

    def test_a_set_listed_again_charges_its_read(self):
        """An exception set a root lists N times is read N times: each read
        costs 1 (L3) and its entries' charges, even for a set with none."""
        def build(root, n):
            (root / "S").mkdir()
            o = _synced_objects([])
            o["SR"]["exceptions"] = ["E"] * n
            return o
        self.assertEqual(_spent(build, 400) - _spent(build, 200), 200)

    def test_one_more_owner_charges_each_default_row(self):
        """One more owner of a root of five files costs five default-row
        evaluations (L3), nothing per list scan."""
        def build(root, t):
            for i in range(5):
                _mkfile(root / "S" / ("a%d.swift" % i))
            return _synced_objects([], owners=t)
        self.assertEqual(_spent(build, 40) - _spent(build, 20), 20 * 5)

    def test_a_retyped_key_charges_the_path_it_builds(self):
        """One more `explicitFileTypes` key `kNNNN.swift` costs its
        13-character path `S/kNNNN.swift` (L5)."""
        def build(root, n):
            _mkfile(root / "S" / "Own.swift")
            return _synced_objects([], exc_fields=None) | {
                "SR": _obj("PBXFileSystemSynchronizedRootGroup", sourceTree="<group>", path="S",
                           exceptions=["E"],
                           explicitFileTypes={"k%04d.swift" % i: "text" for i in range(n)})}
        self.assertEqual(_spent(build, 40) - _spent(build, 20), 20 * 13)

    def test_a_symlinked_root_charges_its_symlink_walk_and_containment(self):
        """One more root naming a symlinked directory costs 2 in descent and
        its symlink walk and containment check at 1 + 1 (L2); it is never
        listed."""
        def build(root, r):
            (root / "Real").mkdir()
            os.symlink("Real", root / "S")
            o = {}
            o["PROJ"] = _obj("PBXProject", mainGroup="MAIN", targets=["T"])
            o["MAIN"] = _obj("PBXGroup", sourceTree="<group>", children=["SR%d" % i for i in range(r)])
            for i in range(r):
                o["SR%d" % i] = _obj("PBXFileSystemSynchronizedRootGroup", sourceTree="<group>", path="S")
            o["T"] = _obj("PBXNativeTarget", name="App", productType=APP,
                          fileSystemSynchronizedGroups=["SR%d" % i for i in range(r)])
            return o
        self.assertEqual(_spent(build, 40) - _spent(build, 20), 20 * (2 + 2 + 2))


class ClassicSymlinkChargeTests(unittest.TestCase):

    def test_a_symlinked_member_charges_its_containment_check(self):
        """A member that is a symlink inside the checkout costs its
        containment check of 2 segments, l2_units(2) = 4 (L2), on top of the
        26 a regular member costs (`ClassicChargeTests`)."""
        def build(root, p):
            _mkfile(root / "Real.swift")
            for i in range(p):
                (root / "Src").mkdir(exist_ok=True)
                os.symlink("../Real.swift", root / "Src" / ("f%04d.swift" % i))
            return _members_objects(p)
        self.assertEqual(_spent(build, 40) - _spent(build, 20), 20 * (26 + 4))


class ProjectPassChargeTests(unittest.TestCase):
    """Each project read, target-dependency pass and product pass charges 1
    (L3); each dependency and each product dependency 1."""

    def test_one_more_project_charges_its_three_passes(self):
        def spent(n):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                o = {"PROJ": _obj("PBXProject", mainGroup="MAIN", targets=[]),
                     "MAIN": _obj("PBXGroup", sourceTree="<group>", children=[])}
                files, bundles = {}, []
                for i in range(n):
                    bundle = "P%03d.xcodeproj" % i
                    files[bundle + "/project.pbxproj"] = _write_project(root, o, bundle)
                    bundles.append((bundle, True, True))
                budget = xi.CollectionBudget()
                sx.read_projects(root, SimpleNamespace(bundles=bundles, refusals={}, files=files), [], None, budget)
            return budget.work_spent
        self.assertEqual(spent(20) - spent(10), 10 * 3)

    def test_one_more_target_dependency_charges_one(self):
        def build(root, d):
            o = {"PROJ": _obj("PBXProject", mainGroup="MAIN", targets=["T"]),
                 "MAIN": _obj("PBXGroup", sourceTree="<group>", children=[]),
                 "T": _obj("PBXNativeTarget", name="App", productType=APP,
                           dependencies=["NOPE%d" % i for i in range(d)])}
            return o
        self.assertEqual(_spent(build, 40) - _spent(build, 20), 20)

    def test_a_cross_project_dependency_charges_each_container_it_visits(self):
        """A dependency on a target of the second of two referenced projects
        costs 1 (L3) plus the two containers its search visits (L3)."""
        def spent(d):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                other = {"PROJ": _obj("PBXProject", mainGroup="MAIN", targets=["OT"]),
                         "MAIN": _obj("PBXGroup", sourceTree="<group>", children=[]),
                         "OT": _obj("PBXNativeTarget", name="Lib", productType=APP)}
                a = {"PROJ": _obj("PBXProject", mainGroup="MAIN", targets=["T"]),
                     "MAIN": _obj("PBXGroup", sourceTree="<group>", children=["FB", "FC"]),
                     "FB": _obj("PBXFileReference", sourceTree="<group>", path="B.xcodeproj"),
                     "FC": _obj("PBXFileReference", sourceTree="<group>", path="C.xcodeproj"),
                     "T": _obj("PBXNativeTarget", name="App", productType=APP,
                               dependencies=["D%d" % i for i in range(d)])}
                for i in range(d):
                    a["D%d" % i] = _obj("PBXTargetDependency", targetProxy="X%d" % i)
                    a["X%d" % i] = _obj("PBXContainerItemProxy", containerPortal="FC", remoteGlobalIDString="OT")
                files = {}
                for bundle, objects in (("A.xcodeproj", a), ("B.xcodeproj", other), ("C.xcodeproj", other)):
                    files[bundle + "/project.pbxproj"] = _write_project(root, objects, bundle)
                budget = xi.CollectionBudget()
                reads = SimpleNamespace(bundles=[(b[:-len("/project.pbxproj")], True, True) for b in files],
                                        refusals={}, files=files)
                result = sx.read_projects(root, reads, [], None, budget)
            self.assertEqual(len([e for e in result["target_deps"] if e["to_container"] == "C.xcodeproj"]), d)
            return budget.work_spent
        self.assertEqual(spent(20) - spent(10), 10 * (1 + 2))


def _workspace(root, body, name="W.xcworkspace"):
    raw = ('<?xml version="1.0" encoding="UTF-8"?>\n<Workspace version="1.0">\n%s</Workspace>\n' % body).encode()
    _mkfile(root / name / "contents.xcworkspacedata", raw)
    return raw


class WorkspaceChargeTests(unittest.TestCase):
    """The workspace reader keeps no memo, so each reference pays its
    own checks, and a listing it performs charges its entries (L1)."""

    def _spent(self, n, d, body=None, limit=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for i in range(d):
                _mkfile(root / ("z%04d.txt" % i))
            refs = body or "".join('<FileRef location="group:P%04d.xcodeproj"></FileRef>\n' % i for i in range(n))
            raw = _workspace(root, refs)
            budget = xi.CollectionBudget()
            if limit is not None:
                budget.work_limit = limit
            facts = xi.read_workspace(root, "W.xcworkspace", raw, budget)
        return budget, facts

    def test_one_more_reference_charges_its_checks_and_the_root_listing(self):
        """One more reference `PNNNN.xcodeproj` costs 1 (L3), its
        15-character path (L5), its containment check, symlink walk and
        listed-name walk at 1 + 1 (L2), and the root's full listing, 10 files
        and the workspace bundle (L1)."""
        per = 1 + 15 + 2 + 2 + 2 + 11
        self.assertEqual(self._spent(40, 10)[0].work_spent - self._spent(20, 10)[0].work_spent, 20 * per)

    def test_one_more_root_entry_costs_one_unit_per_reference(self):
        """N references each list the root in full: one more entry there
        costs N units, not one (N x D, bounded by the budget)."""
        self.assertEqual(self._spent(25, 30)[0].work_spent - self._spent(25, 10)[0].work_spent, 25 * 20)

    def test_one_more_group_charges_itself_and_its_path(self):
        body = lambda n: "".join('<Group location="group:g%04d"></Group>\n' % i for i in range(n))
        self.assertEqual(self._spent(0, 0, body(40))[0].work_spent - self._spent(0, 0, body(20))[0].work_spent,
                         20 * (1 + 5))

    def test_a_refused_walk_keeps_what_it_read_and_stops(self):
        budget, facts = self._spent(400, 10, limit=500)
        self.assertTrue(budget.work_tripped)
        self.assertLessEqual(budget.work_spent, 500)
        # Each missing reference renders its own line until the bound stops
        # the walk; none renders `project-unreadable`.
        read = len(facts.residuals)
        self.assertEqual(read, 500 // 33)
        self.assertTrue(all(r.klass == "missing-input" for r in facts.residuals))
        # Positive control: the same workspace unbounded reads every reference.
        self.assertEqual(len(self._spent(400, 10)[1].residuals), 400)

    def test_a_workspace_the_bound_stops_renders_the_line_through_the_concern(self):
        """The concern's one budget reaches the workspace reader: a workspace
        that exhausts it renders the work line, from the project reader that
        runs after it, even with no project bundle, and no `malformed`."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            _workspace(root, "".join('<FileRef location="group:P%04d.xcodeproj"></FileRef>\n' % i
                                     for i in range(300)))
            _mkfile(root / "Package.swift", b'let package = Package(name: "M", targets: [.target(name: "Core")])\n')
            with mock.patch.object(xi, "MAX_WORK_UNITS", 2000):
                graph = _graph(root)
            unbounded = _graph(root)
        self.assertEqual(graph.count(_work_text(2000)), 1)
        self.assertNotIn("malformed", graph)
        self.assertLess(graph.count("missing-input"), 300)
        self.assertGreater(graph.count("missing-input"), 0)
        # Positive control: unbounded, every reference renders its line.
        self.assertEqual(unbounded.count("missing-input"), 300)
        self.assertNotIn("per-concern work bound", unbounded)

    def test_a_long_group_location_is_charged_by_its_characters(self):
        """A group location of L characters makes every
        reference under it an L-character path, charged per reference (L5)."""
        body = ('<Group location="group:%s">\n' % ("x" * 20000)
                + '<FileRef location="group:a.xcodeproj"></FileRef>\n' * 300 + "</Group>\n")
        budget, facts = self._spent(0, 0, body=body, limit=200_000)
        self.assertTrue(budget.work_tripped)
        self.assertLessEqual(len(facts.residuals), 200_000 // 20000)


class ProductChargeTests(unittest.TestCase):
    """The product pass: a remote reference read once however many
    product dependencies name it, and the unlinked-product candidate
    search."""

    @staticmethod
    def _local(n):
        o = {"PROJ": _obj("PBXProject", mainGroup="MAIN", targets=["T"]),
             "MAIN": _obj("PBXGroup", sourceTree="<group>", children=[]),
             "L": _obj("XCLocalSwiftPackageReference", relativePath="Pkg"),
             "T": _obj("PBXNativeTarget", name="App", productType=APP,
                       packageProductDependencies=["P%d" % i for i in range(n)])}
        for i in range(n):
            o["P%d" % i] = _obj("XCSwiftPackageProductDependency", productName="Kit", package="L")
        return o

    def test_a_product_naming_a_kept_reference_charges_its_first_reading(self):
        """Each product dependency costs 1 (L3) and, for a local reference
        already read, 1 plus the units that reading spent outside its
        listing (L6): the 3-character join `Pkg`, its containment check and
        symlink walk at l2_units(1) = 2, and the listed-name walk of
        `Pkg/Package.swift` at l2_units(2) = 4. The root's listing is not
        charged again."""
        self.assertEqual(_spent(lambda root, n: self._local(n), 40) - _spent(lambda root, n: self._local(n), 20),
                         20 * (1 + 1 + 3 + 2 + 2 + 4))
        # The reuse, 1 + 11, is charged through `charge_excluded`.
        (s40, e40), (s20, e20) = (_spent_excluded(lambda root, n: self._local(n), 40),
                                  _spent_excluded(lambda root, n: self._local(n), 20))
        self.assertEqual((e40 - e20, (s40 - e40) - (s20 - e20)), (20 * 12, 20 * 1))

    def test_one_more_local_reference_object_charges_its_join_and_containment(self):
        def build(root, n):
            o = self._local(0)
            for i in range(n):
                o["L%d" % i] = _obj("XCLocalSwiftPackageReference", relativePath="Pkg")
            return o
        self.assertEqual(_spent(build, 40) - _spent(build, 20), 20 * (3 + 2))

    def test_one_more_manifest_charges_each_root_it_is_tested_against(self):
        """An unlinked product's candidate search tests each manifest
        against each of the project's 6 roots (L3); a manifest under a root
        is a candidate, and the unresolved line lists it (1 more)."""
        def spent(m, under):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                o = {"PROJ": _obj("PBXProject", mainGroup="MAIN", targets=["T"]),
                     "MAIN": _obj("PBXGroup", sourceTree="<group>", children=["G%d" % i for i in range(6)]),
                     "P": _obj("XCSwiftPackageProductDependency", productName="Kit"),
                     "T": _obj("PBXNativeTarget", name="App", productType=APP, packageProductDependencies=["P"])}
                for i in range(6):
                    o["G%d" % i] = _obj("PBXGroup", sourceTree="<group>", path="g%d" % i, children=[])
                manifests = [SimpleNamespace(directory=("g0/m%d" if under else "m%d") % i,
                                             path=("g0/m%d" if under else "m%d") % i + "/Package.swift",
                                             products=[], targets=[]) for i in range(m)]
                _result, budget = _read(root, o, manifests=manifests)
            return budget.work_spent
        self.assertEqual(spent(40, False) - spent(20, False), 20 * 6)
        self.assertEqual(spent(40, True) - spent(20, True), 20 * (6 + 1))


def _graph(root):
    return swift.extract_module_graph(root, "bionic")[0]


class TripRenderingTests(unittest.TestCase):
    """The work line is an ordinary `## Residuals` bullet,
    deduplicated, sorted and charged to the output bound; when the output
    bound trips first the work line drops and the output-cap line renders
    last. The prefix rule is untouched."""

    LIMIT = 12_000

    def _tree(self, root, p=300):
        """P members `Src/fNNNN.swift` interleaved in one Sources phase with P
        build files naming missing `Src/mNNNN.swift`: descent reads whole
        within `LIMIT`, and the classic read stops part-way."""
        _members_tree(root, p)
        o = {}
        o["PROJ"] = _obj("PBXProject", mainGroup="MAIN", targets=["T"])
        o["MAIN"] = _obj("PBXGroup", sourceTree="<group>", children=["G"])
        o["G"] = _obj("PBXGroup", sourceTree="<group>", path="Src",
                      children=["F%d" % i for i in range(p)] + ["M%d" % i for i in range(p)])
        files = []
        for i in range(p):
            o["F%d" % i] = _obj("PBXFileReference", sourceTree="<group>", path="f%04d.swift" % i)
            o["M%d" % i] = _obj("PBXFileReference", sourceTree="<group>", path="m%04d.swift" % i)
            o["B%d" % i] = _obj("PBXBuildFile", fileRef="F%d" % i)
            o["C%d" % i] = _obj("PBXBuildFile", fileRef="M%d" % i)
            files += ["B%d" % i, "C%d" % i]
        o["SP"] = _obj("PBXSourcesBuildPhase", files=files)
        o["T"] = _obj("PBXNativeTarget", name="App", productType=APP, buildPhases=["SP"])
        _write_project(root, o)

    @staticmethod
    def _bullets(text):
        section = text.split("## Residuals", 1)[1] if "## Residuals" in text else ""
        return [line for line in section.split("\n") if line.startswith("- `")]

    def test_the_work_line_renders_once_among_the_residuals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self._tree(root)
            with mock.patch.object(xi, "MAX_WORK_UNITS", self.LIMIT):
                graph = _graph(root)
                api = swift.extract_api_surface(root, "bionic")[0]
        line = "- `scan-cap` `.` lines — — " + xi.WORK_BOUND_TEMPLATE.format(limit=self.LIMIT)
        self.assertEqual(graph.count(line), 1)
        self.assertIn(line, self._bullets(graph))
        # A read-level class: the other concern that runs the reader renders it.
        self.assertEqual(api.count(line), 1)
        # Rows read before the refusal render; no reference renders `malformed`.
        self.assertIn("`Src/f0000.swift`", graph)
        self.assertNotIn("malformed", graph)
        # Rows read after the refusal do not: the last members are unread.
        self.assertNotIn("`Src/f0299.swift`", graph)
        # The work line sorts first: its path is the checkout root.
        self.assertEqual(self._bullets(graph)[0], line)

    def test_an_output_cap_that_trips_first_drops_the_work_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self._tree(root)
            with mock.patch.object(xi, "MAX_WORK_UNITS", self.LIMIT):
                full = _graph(root)
                with mock.patch.object(swift, "_MAX_CONCERN_BYTES", 2000):
                    capped = _graph(root)
        work = xi.WORK_BOUND_TEMPLATE.format(limit=self.LIMIT)
        self.assertIn(work, full)
        self.assertNotIn(work, capped)
        bullets = self._bullets(capped)
        self.assertTrue(bullets[-1].startswith("- `scan-cap` `.` lines — — the module-graph rows pass"), bullets[-1:])

    def test_a_residual_cut_keeps_a_prefix_that_holds_the_work_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self._tree(root)
            with mock.patch.object(xi, "MAX_WORK_UNITS", self.LIMIT):
                full = self._bullets(_graph(root))
                tables = len(_graph(root).split("## Residuals", 1)[0].encode()) + 1
                with mock.patch.object(swift, "_MAX_CONCERN_BYTES", tables + 600):
                    cut = self._bullets(_graph(root))
        self.assertLess(len(cut) - 1, len(full))
        # Every bullet but the last is a prefix of the uncut order, and the
        # work line, sorting on path `.`, is inside it.
        self.assertEqual(cut[:-1], full[:len(cut) - 1])
        self.assertTrue(any("per-concern work bound" in b for b in cut[:-1]))
        self.assertIn("rows pass the per-concern output bound", cut[-1])


class OrderAndDepthIndependenceTests(unittest.TestCase):
    """Units never come from syscalls, time, listing order
    or the absolute root."""

    def _run(self, depth, reverse=False, limit=None):
        real = os.scandir

        class Reversed:
            def __init__(self, path):
                with real(path) as it:
                    self.entries = list(it)[::-1]

            def __iter__(self):
                return iter(self.entries)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve().joinpath(*(["deeper"] * depth))
            root.mkdir(parents=True, exist_ok=True)
            objects = _tx.ReaderOrderIndependenceTests._tree(None, root)
            if reverse:
                with mock.patch("os.scandir", Reversed):
                    return _read(root, objects, limit)
            return _read(root, objects, limit)

    def test_two_root_depths_and_a_reversed_listing_spend_the_same_work(self):
        runs = [self._run(0), self._run(6), self._run(3, reverse=True)]
        self.assertEqual(len({b.work_spent for _r, b in runs}), 1, [b.work_spent for _r, b in runs])
        self.assertEqual(runs[0][0], runs[1][0])
        self.assertEqual(runs[0][0], runs[2][0])
        # Not vacuous: the tree reads every charged route.
        self.assertGreater(runs[0][1].work_spent, 100)

    def test_a_trip_at_a_patched_limit_is_the_same_at_every_depth_and_order(self):
        full = self._run(0)[1].work_spent
        limit = full // 2
        runs = [self._run(0, limit=limit), self._run(5, limit=limit), self._run(2, reverse=True, limit=limit)]
        for result, budget in runs:
            self.assertTrue(budget.work_tripped)
            self.assertEqual(len(_work_lines(result)), 1)
        self.assertEqual(runs[0][0], runs[1][0])
        self.assertEqual(runs[0][0], runs[2][0])
        self.assertEqual(len({b.work_spent for _r, b in runs}), 1)


_REVERSED_CLI = """
import os, runpy, sys
real = os.scandir
class Reversed:
    def __init__(self, path='.'):
        with real(path) as it:
            self.entries = iter(list(it)[::-1])
    def __iter__(self):
        return self
    def __next__(self):
        return next(self.entries)
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False
    def close(self):
        pass
os.scandir = Reversed
sys.argv = [sys.argv[1], "--repo-root", sys.argv[2]]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


class CliDeterminismTests(unittest.TestCase):
    """Two derives through the real CLI under different `PYTHONHASHSEED`
    values, and one under a reversed `os.scandir`, write byte-identical
    architecture files for a checkout that trips the real bound."""

    def test_a_tripping_checkout_derives_the_same_bytes(self):
        outputs = []
        for seed, reverse in (("1", False), ("4242", False), ("7", True)):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: bionic\n')
                _mkfile(root / "bionic" / "manifest.yml", b'schema_version: "5"\nadr:\n  next_number: 1\n')
                _write_project(root, DescentChargeTests._chain(3000))
                env = dict(os.environ, PYTHONHASHSEED=seed)
                driver = str(SCRIPTS / "derive-arch.py")
                argv = ([sys.executable, "-c", _REVERSED_CLI, driver, str(root)] if reverse
                        else [sys.executable, driver, "--repo-root", str(root)])
                proc = subprocess.run(argv, capture_output=True, text=True, timeout=300, env=env)
                self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
                outputs.append({p.name: p.read_bytes() for p in sorted((root / "bionic" / "arch").glob("*.md"))})
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0], outputs[2])
        self.assertIn(_work_text(), outputs[0]["module-graph.md"].decode("utf-8"))


class LongSegmentTests(unittest.TestCase):
    """A path segment of L characters is charged L each time
    a join repeats it, so a root named by one long segment cannot make each
    of E exception entries hold its own copy."""

    def test_entries_under_a_long_root_stop_at_the_bound(self):
        length, entries = 100_000, 500
        calls = []
        real = sx._classify_exception_entry

        def spy(*args, **kwargs):
            calls.append(1)
            return real(*args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            o = _synced_objects(["a%05d.swift" % i for i in range(entries)],
                                exc_isa=sx._BUILD_PHASE_EXCEPTION_SET_ISA, exc_fields={"buildPhase": "NOPE"},
                                root_path="x" * length)
            with mock.patch.object(sx, "_classify_exception_entry", spy):
                result, budget = _read(root, o)
        self.assertTrue(budget.work_tripped)
        self.assertEqual(len(_work_lines(result)), 1)
        self.assertLessEqual(len(calls), xi.MAX_WORK_UNITS // length + 1)


class LinearTimeTests(unittest.TestCase):
    """One timing check through the shared thread-time helper. A
    build file listed M times is read once per reference, so reading M of
    them grows linearly in M."""

    def test_repeated_build_files_read_in_linear_time(self):
        def seconds(m):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                for i in range(50):
                    _mkfile(root / "Src" / ("f%d.txt" % i))
                raw = _write_project(root, _classic_objects(m))
                reads = SimpleNamespace(bundles=[("X.xcodeproj", True, True)], refusals={},
                                        files={"X.xcodeproj/project.pbxproj": raw})
                start = _timing.clock()
                sx.read_projects(root, reads, [], None, xi.CollectionBudget())
                return _timing.clock() - start
        _timing.assert_grows_linearly(self, seconds, 8000, "a repeated build file")


class DescentChargeTests(unittest.TestCase):
    """Descent charges each group's children (L3) and each joined
    path's characters (L5), so a chain of nested groups, whose paths grow
    with depth, is bounded."""

    @staticmethod
    def _chain(n, segment="a"):
        o = {}
        o["PROJ"] = _obj("PBXProject", mainGroup="MAIN", targets=["T"])
        o["MAIN"] = _obj("PBXGroup", sourceTree="<group>", children=["G0"] if n else [])
        o["T"] = _obj("PBXNativeTarget", name="App", productType=APP)
        for i in range(n):
            o["G%d" % i] = _obj("PBXGroup", sourceTree="<group>", path=segment,
                                children=["G%d" % (i + 1)] if i + 1 < n else [])
        return o

    def test_the_nth_group_of_a_chain_charges_its_path(self):
        """Group i of a chain of one-letter paths names a path of i
        segments, 2i - 1 characters, and its parent charged 1 for it: one
        more group at depth n + 1 costs 2(n + 1)."""
        spent = [_spent(lambda root, n: self._chain(n), n) for n in (10, 11, 30, 31)]
        self.assertEqual(spent[1] - spent[0], 2 * 11)
        self.assertEqual(spent[3] - spent[2], 2 * 31)

    def test_a_long_segment_is_charged_by_its_characters(self):
        """A path segment of L characters costs L each time a join repeats
        it, so a chain that repeats one long segment is bounded by its
        characters, not by its segment count (L5 counts characters, not
        segments)."""
        def spent(length):
            return _spent(lambda root, n: self._chain(n, "x" * length), 4)
        # Group i names i segments of `length` characters and i - 1
        # separators: sum over i = 1..4 of (i * length + i - 1), plus the four
        # children charges.
        for length in (1, 50):
            self.assertEqual(spent(length) - _spent(lambda root, n: self._chain(0), 0),
                             sum(i * length + i - 1 for i in range(1, 5)) + 4, length)

    def test_a_chain_past_the_bound_renders_the_line_and_its_target_row(self):
        """ADR-0129 clause 10: past the bound attribution moves, never an
        entity. The target row renders; a refused descent reads no
        membership and renders no `malformed` line."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            result, budget = _read(root, self._chain(300), limit=5000)
        self.assertTrue(budget.work_tripped)
        self.assertLessEqual(budget.work_spent, 5000)
        self.assertEqual(len(_work_lines(result)), 1)
        self.assertEqual([t["name"] for t in result["targets"]], ["App"])
        self.assertFalse([r for r in result["residuals"] if r[3] == "malformed"])
        # Positive control: without the patched limit the same chain reads
        # whole and renders no work line.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            result, budget = _read(root, self._chain(300))
        self.assertFalse(budget.work_tripped)
        self.assertEqual(_work_lines(result), [])



def _tri(k):
    """Ruling C's L2 charge for a path of `k` repo-relative segments,
    written out here rather than read from `swift_xcinputs.l2_units`, so a
    wrong helper cannot agree with itself."""
    return 1 + k * (k + 1) // 2


class L2UnitsTests(unittest.TestCase):
    """Ruling C, L2: a path check of K repo-relative segments charges
    1 + K(K + 1)/2 before the check runs, memo hit or miss. The kernel
    resolves K prefixes of 1 to K segments, so a linear charge let a deep
    reference repeat quadratic work under a bound it never reached."""

    def test_the_units_are_one_plus_the_triangle_number(self):
        self.assertEqual([xi.l2_units(k) for k in (0, 1, 2, 3, 10)], [1, 2, 4, 7, 56])
        self.assertEqual(xi.l2_units(2895), 4_191_961)
        self.assertEqual(xi.l2_units(2896), 4_194_857)
        self.assertLessEqual(xi.l2_units(2895), xi.MAX_WORK_UNITS)
        self.assertGreater(xi.l2_units(2896), xi.MAX_WORK_UNITS)

    @staticmethod
    def _charges(root, k):
        """What each of the six L2 sites charges for one check of a
        K-segment path, from a fresh budget: the path `d/d/...` names
        nothing, so no site lists a directory past the root. `_classify_ref`
        names `out/d/...`, whose first segment is a symlink leaving the
        checkout, so it stops at its containment check."""
        from crux.arch.packs import swift_xcode_products as sp
        segs = ("d",) * k
        out = ("out",) + ("d",) * (k - 1)
        sites = {
            "listed_names": lambda b: xi.listed_names(root, segs, budget=b),
            "directory_entries": lambda b: xi.directory_entries(root, segs, budget=b),
            "has_symlink_component": lambda b: xi.has_symlink_component(root, segs, budget=b),
            "_classify_ref": lambda b: xi._classify_ref(root, "/".join(out), b),
            "_contained_charged": lambda b: sx._contained_charged(root, segs, b),
            "products._safe_contained": lambda b: sp._safe_contained(root, root.joinpath(*segs), b, k),
        }
        spent = {}
        for name, call in sites.items():
            budget = xi.CollectionBudget()
            call(budget)
            spent[name] = budget.work_spent
        return spent

    def test_each_of_the_six_sites_charges_the_quadratic_units(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp).resolve()
            os.symlink(outside, root / "out")
            root_entries = len(os.listdir(root))
            for k in (1, 2, 5, 40):
                spent = self._charges(root, k)
                # `listed_names` also lists the root (L1) before it finds `d`
                # missing; every other site lists nothing.
                spent["listed_names"] -= root_entries
                self.assertEqual(spent, dict.fromkeys(spent, _tri(k)), k)

    def test_the_root_itself_charges_one(self):
        from crux.arch.packs import swift_xcode_products as sp
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for call in (lambda b: xi.has_symlink_component(root, (), budget=b),
                         lambda b: sx._contained_charged(root, (), b),
                         lambda b: sp._safe_contained(root, root, b, 0)):
                budget = xi.CollectionBudget()
                call(budget)
                self.assertEqual(budget.work_spent, 1)


class DeepCheckRefusalTests(unittest.TestCase):
    """Ruling C: a check of K >= 2896 segments charges more than the whole
    bound, so it refuses BEFORE the check runs. A spy on each check proves
    it never ran; at K = 2895 the same spy sees the check (positive
    control)."""

    def _spied(self, root, k, target, attr, call):
        spy = mock.MagicMock(wraps=getattr(target, attr))
        budget = xi.CollectionBudget()
        with mock.patch.object(target, attr, spy):
            try:
                call(budget, k)
                refused = False
            except xi.WorkBoundExceeded:
                refused = True
        return spy.call_count, refused, budget

    def _cases(self, root):
        from crux.arch.packs import swift_xcode_products as sp
        segs = lambda k: ("d",) * k
        return {
            "_classify_ref": (xi, "contained", lambda b, k: xi._classify_ref(root, "/".join(segs(k)), b)),
            "_contained_charged": (sx, "contained", lambda b, k: sx._contained_charged(root, segs(k), b)),
            "products._safe_contained": (sp, "contained",
                                         lambda b, k: sp._safe_contained(root, root.joinpath(*segs(k)), b, k)),
            "has_symlink_component": (os, "lstat",
                                      lambda b, k: xi.has_symlink_component(root, segs(k), budget=b)),
            "listed_names": (os, "scandir", lambda b, k: xi.listed_names(root, segs(k), budget=b)),
            "directory_entries": (os, "scandir", lambda b, k: xi.directory_entries(root, segs(k), budget=b)),
        }

    def test_a_check_of_2896_segments_refuses_before_it_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for name, (target, attr, call) in self._cases(root).items():
                calls, refused, budget = self._spied(root, 2896, target, attr, call)
                self.assertEqual((calls, refused, budget.work_tripped, budget.work_spent), (0, True, True, 0), name)
                # Positive control: one segment fewer fits, and the check runs.
                calls, _refused, budget = self._spied(root, 2895, target, attr, call)
                self.assertGreaterEqual(calls, 1, name)
                self.assertGreaterEqual(budget.work_spent, 4_191_961, name)


class ExcludedChargeTests(unittest.TestCase):
    """Ruling C, L6 option (b): a memo reuse charges 1 plus the units its
    first computation spent outside every listing (L1), reused scan (L4)
    and reuse (L6), so a listing or a nested reuse is never charged twice."""

    def test_charge_excluded_counts_only_what_it_charged(self):
        budget = xi.CollectionBudget()
        budget.work_limit = 10
        xi.charge_excluded(budget, 4)
        xi.charge(budget, 3)
        self.assertEqual((budget.work_spent, budget.work_excluded), (7, 4))
        with self.assertRaises(xi.WorkBoundExceeded):
            xi.charge_excluded(budget, 4)
        self.assertEqual((budget.work_spent, budget.work_excluded), (7, 4))
        xi.charge_excluded(None, 10 ** 9)

    def test_a_reuse_charges_one_plus_the_first_builds_own_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            budget = xi.CollectionBudget()
            memo = xi.DirectoryVerdicts(root, budget)

            def inner():
                xi.charge(budget, 5)
                xi.charge_excluded(budget, 100)
                return "inner"

            def outer():
                xi.charge(budget, 7)
                xi.reuse_derived(memo, root, ("a",), "k1", inner)
                xi.charge_excluded(budget, 50)
                return "outer"

            xi.reuse_derived(memo, root, ("a",), "k1", inner)
            self.assertEqual(budget.work_spent, 105)
            xi.reuse_derived(memo, root, ("a",), "k1", inner)
            self.assertEqual(budget.work_spent, 105 + 1 + 5)
            # A first computation that nests a reuse (6) and a listing (50)
            # records only its own 7 units.
            xi.reuse_derived(memo, root, ("b",), "k2", outer)
            self.assertEqual(budget.work_spent, 111 + 7 + 6 + 50)
            xi.reuse_derived(memo, root, ("b",), "k2", outer)
            self.assertEqual(budget.work_spent, 174 + 1 + 7)
            # L4: a reused scan charges its length, once per reuse.
            xi.reuse_derived(memo, root, ("c",), "k3", lambda: (1, 2, 3), reuse_cost=len)
            xi.reuse_derived(memo, root, ("c",), "k3", lambda: (1, 2, 3), reuse_cost=len)
            self.assertEqual(budget.work_spent, 182 + 3)
            self.assertEqual(budget.work_excluded, 100 + 6 + 6 + 50 + 8 + 3)
            # A first computation that nests an L4 reuse records none of it.
            def scans():
                xi.charge(budget, 2)
                xi.reuse_derived(memo, root, ("c",), "k3", lambda: (1, 2, 3), reuse_cost=len)
                return "scans"
            xi.reuse_derived(memo, root, ("e",), "k5", scans)
            xi.reuse_derived(memo, root, ("e",), "k5", scans)
            self.assertEqual(budget.work_spent, 185 + 2 + 3 + 1 + 2)

    def test_a_listing_inside_a_first_computation_is_never_recorded(self):
        """Each of the three L1 listing sites charges through
        `charge_excluded`, so a build that lists a directory records only its
        path checks, and a reuse charges 1 plus those, never the listing:
        `listed_names` without a memo (`_listed_names_live`),
        `directory_entries` without a memo (`_listing`) and the folder-synced
        walk (`_sorted_entries`)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for i in range(30):
                _mkfile(root / "Src" / ("f%02d.swift" % i))
            builds = {
                "_listed_names_live": (lambda b: xi.listed_names(root, ("Src", "f00.swift"), budget=b), _tri(2)),
                "_listing": (lambda b: xi.directory_entries(root, ("Src",), budget=b), _tri(1)),
                "_sorted_entries": (lambda b: sx._sorted_entries(root / "Src", b), 0),
            }
            for name, (call, own) in builds.items():
                budget = xi.CollectionBudget()
                memo = xi.DirectoryVerdicts(root, budget)
                xi.reuse_derived(memo, root, ("Src",), name, lambda: call(budget))
                first = budget.work_spent
                self.assertGreaterEqual(first, own + 30, name)
                xi.reuse_derived(memo, root, ("Src",), name, lambda: call(budget))
                self.assertEqual(budget.work_spent - first, 1 + own, name)


class ContainedParityTests(unittest.TestCase):
    """`swift_xcinputs.contained` gives `core._contained`'s verdict. The
    Swift pack's path checks call it in place of the core's, whose
    `Path.is_relative_to` is quadratic in the path's depth."""

    def test_contained_agrees_with_the_core_on_every_shape(self):
        from crux.arch import core
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            base = Path(tmp).resolve()
            root = base / "repo"
            (root / "real").mkdir(parents=True)
            os.symlink("real", root / "in")
            os.symlink(outside, root / "out")
            os.symlink("nope", root / "dangling")
            os.symlink(os.path.join(outside, "nope"), root / "dangling-out")
            os.symlink("loop", root / "loop")
            os.symlink(root, base / "alias")
            cases = {
                "the root itself": (root, root, True),
                "a child": (root, root / "real" / "x.swift", True),
                "a missing child": (root, root / "a" / "b", True),
                "a `..` escape": (root, root / ".." / "x", False),
                "a `..` that stays inside": (root, root / "real" / ".." / "real", True),
                "a symlink staying in the repo": (root, root / "in" / "x", True),
                "a symlink leaving it": (root, root / "out" / "x", False),
                "a dangling symlink": (root, root / "dangling", True),
                "a dangling symlink leaving it": (root, root / "dangling-out", False),
                "a symlink loop": (root, root / "loop", None),
                "a NUL byte": (root, root / "a\0b", False),
                "the root named through a symlink": (base / "alias", root / "real", True),
                "a sibling sharing the root's prefix": (root, base / "repo-2" / "x", False),
            }
            for name, (r, p, expected) in cases.items():
                got = xi.contained(r, p)
                try:
                    core_verdict = core._contained(r, p)
                except ValueError:
                    # The core raises for a NUL byte; every Swift caller
                    # wraps it as not contained.
                    core_verdict = False
                self.assertEqual(got, core_verdict, name)
                if expected is not None:
                    self.assertIs(got, expected, name)


class RelStrParityTests(unittest.TestCase):
    """`swift._rel_str` slices the root's prefix off in time linear in the
    path, and gives what `Path.relative_to` gives on every path the walk
    yields."""

    @staticmethod
    def _old(root, p):
        return Path(p).relative_to(Path(root)).as_posix()

    def test_every_walked_path_matches_relative_to(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            _mkfile(root.joinpath(*(["d"] * 150)) / "Deep.swift")
            _mkfile(root / "A" / "a b" / "ü.swift")
            _mkfile(root / "B.swift")
            seen = 0
            for dirp, _rel_dir, _normal, _excluded, filenames in swift._swift_walk(root):
                for p in ([] if dirp == root else [dirp]) + [dirp / f for f in filenames]:
                    self.assertEqual(swift._rel_str(root, p), self._old(root, p), p)
                    seen += 1
            self.assertGreater(seen, 150)
            # Outside the fast path the fallback answers as before: a root
            # string with a trailing separator, and a path outside the root.
            self.assertEqual(swift._rel_str(str(root) + os.sep, root / "B.swift"), "B.swift")
            with self.assertRaises(ValueError):
                swift._rel_str(root, root.parent / "elsewhere")


if __name__ == "__main__":
    unittest.main()
