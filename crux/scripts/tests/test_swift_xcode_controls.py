"""test_swift_xcode_controls.py -- the Xcode reader's positive controls
(ADR-0130 clause 17). Two positive controls that need a monkeypatch
rather than an `expected.yml` fixture, so they live here instead of under
`crux/scripts/tests/fixtures/xcodeproj/`:

1. `test_visit_once_regression_loses_a_row` -- the once-only-on-lookups
   positive control ADR-0130 clause 17 asks for: monkeypatch the reader so
   a synced root's membership lookup no longer obeys descent's visit-once
   rule (only the FIRST owning target of a shared root gets its default
   members), then run the `27-shared-root-two-targets` fixture and assert
   the SECOND owner's row is now missing. Proves the fixture's own
   assertion is sensitive to a real regression in this class, rather than
   vacuously green regardless of what the reader does.

2. `test_diamond_visit_counter_is_linear_in_depth` -- calls
   `swift_xcode.descend()` directly (no CLI, no fixture file) against a
   synthetic diamond group graph (A -> {B, C}, B -> D, C -> D) built as a
   `PbxprojDocument` in memory, with `objects.get` wrapped to COUNT calls
   per id. Asserts D is visited (added to `group_dirs`) exactly once, and
   that the total `.get()` call count for D's id is bounded (2: one probe
   per incoming edge B and C, not exponential in the diamond's depth) --
   i.e. the descent's `visited` set is doing its job.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]  # crux/scripts
sys.path.insert(0, str(SCRIPTS))

import tempfile  # noqa: E402

from crux.arch.packs import swift_xcode  # noqa: E402
from crux.arch.packs import swift  # noqa: E402
from crux.arch.packs.swift_pbxproj import PbxprojDocument  # noqa: E402

import xcode_fixture_harness as h  # noqa: E402

FIXTURE_DIR = h.FIXTURES_ROOT / "27-shared-root-two-targets"

#: The exact substring the fixture's own `expected.yml` requires per owning
#: target (see `27-shared-root-two-targets/expected.yml`) -- reused here so
#: the in-process control checks the SAME assertion the subprocess harness
#: checks, rather than a paraphrase of it.
_ROW_TARGET_A = "| `Shared/Common.swift` | `TargetA` | folder-synced root Shared | — |"
_ROW_TARGET_B = "| `Shared/Common.swift` | `TargetB` | folder-synced root Shared | — |"


def _render_module_graph(root):
    """extract_module_graph runs IN-PROCESS (not through the subprocess-
    isolated `xcode_fixture_harness.run_case`, whose child process imports
    its own fresh `swift_xcode` module and so never sees a monkeypatch
    applied in this process). That is the right tool for the subprocess
    case (the audit hook needs a real child), but it defeats a monkeypatch-
    based positive control by construction, so these two tests call the
    extractor directly instead."""
    body, _sources = swift.extract_module_graph(root, "bionic")
    return body


class VisitOnceRegressionTests(unittest.TestCase):
    def test_control_fixture_passes_unpatched(self):
        """Sanity: both owning targets render their row against the real
        reader before the monkeypatch below corrupts it (proves the failure
        we induce is caused by the patch, not a pre-existing fixture
        defect)."""
        with tempfile.TemporaryDirectory() as t:
            root = Path(t) / "case"
            root.mkdir()
            import shutil
            for item in FIXTURE_DIR.iterdir():
                if item.name == "expected.yml":
                    continue
                dest = root / item.name
                if item.is_dir():
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)
            body = _render_module_graph(root)
            self.assertIn(_ROW_TARGET_A, body)
            self.assertIn(_ROW_TARGET_B, body)

    def test_visit_once_regression_loses_a_row(self):
        """Patch `swift_xcode.read_projects` to only ever emit membership
        rows for the FIRST owning target of a shared synced root (a
        "lookups aren't visit-once" regression: as if a cache keyed only on
        the root id, not on (root id, owner id), served the first owner's
        row for every owner). The two-owner fixture must then lose the
        SECOND target's row -- proving the fixture's own row assertion is
        sensitive to this exact regression class, not vacuously green
        regardless of what the reader does."""
        real_read_projects = swift_xcode.read_projects

        def patched(root, reads, manifests, workspace_facts=None, collection=None):
            result = real_read_projects(root, reads, manifests, workspace_facts, collection)
            memberships = result["memberships"]
            if not memberships:
                return result
            first_target = memberships[0]["target"]
            corrupted = tuple(m for m in memberships if m["target"] == first_target)
            return {**result, "memberships": corrupted}

        swift_xcode.read_projects = patched
        try:
            with tempfile.TemporaryDirectory() as t:
                root = Path(t) / "case"
                root.mkdir()
                import shutil
                for item in FIXTURE_DIR.iterdir():
                    if item.name == "expected.yml":
                        continue
                    dest = root / item.name
                    if item.is_dir():
                        shutil.copytree(item, dest)
                    else:
                        shutil.copy2(item, dest)
                body = _render_module_graph(root)
        finally:
            swift_xcode.read_projects = real_read_projects

        self.assertIn(_ROW_TARGET_A, body)
        self.assertNotIn(_ROW_TARGET_B, body)


class _CountingObjects(dict):
    """A plain dict subclass that records every `.get(key)` call by key, so
    a test can assert HOW MANY TIMES a diamond join's id was looked up
    during one descent, without instrumenting `descend()` itself."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.get_counts: dict = {}

    def get(self, key, default=None):
        self.get_counts[key] = self.get_counts.get(key, 0) + 1
        return super().get(key, default)


class DiamondVisitCounterTests(unittest.TestCase):
    def test_diamond_visit_counter_is_linear_in_depth(self):
        """A -> {B, C}; B -> D; C -> D (a one-level diamond join at D).
        `descend()` must resolve D exactly once (it appears once in
        `group_dirs`) and must not re-walk D's own subtree once for each
        incoming edge -- the `.get()` call count for D's id stays bounded
        (linear in the number of incoming edges: 2, not exponential in
        depth) rather than growing with the number of paths that reach it."""
        objects = _CountingObjects({
            "PRJ": {"isa": "PBXProject", "mainGroup": "A"},
            "A": {"isa": "PBXGroup", "sourceTree": "<group>", "path": "A",
                  "children": ["B", "C"]},
            "B": {"isa": "PBXGroup", "sourceTree": "<group>", "path": "B",
                  "children": ["D"]},
            "C": {"isa": "PBXGroup", "sourceTree": "<group>", "path": "C",
                  "children": ["D"]},
            "D": {"isa": "PBXGroup", "sourceTree": "<group>", "path": "D",
                  "children": ["E"]},
            "E": {"isa": "PBXFileReference", "sourceTree": "<group>", "path": "Leaf.swift"},
        })
        doc = PbxprojDocument(root_id="PRJ", objects=objects)

        descent = swift_xcode.descend(doc, ())

        # D resolves exactly once, at the path A/B/D (B sorts before C, and
        # a group visited a second time via C is recorded as unresolved,
        # never re-walked into a second `group_dirs` entry).
        self.assertEqual(descent.group_dirs.get("D"), ("A", "B", "D"))
        self.assertIn("D", descent.unresolved_ids)
        self.assertEqual(swift_xcode.segs_to_posix(descent.file_segments.get("E")),
                          "A/B/D/Leaf.swift")

        # D's id is looked up once per INCOMING EDGE (B's child list and C's
        # child list each name it once) -- not once per path through the
        # diamond's full depth, and not walked recursively a second time
        # once already visited.
        self.assertLessEqual(objects.get_counts.get("D", 0), 2)


if __name__ == "__main__":
    unittest.main()
