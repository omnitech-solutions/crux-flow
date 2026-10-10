"""Canonical batch states use the same guarded source context throughout."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import yaml
from admission_source_io import SourceIO, SourceIORefusal
import test_survey_sheet as fixtures

SS = fixtures.SS


class GuardedSurveyTests(unittest.TestCase):
    def fixture(self, state):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        tree, obs = root / "docs", root / "docs/observations"
        obs.mkdir(parents=True)
        paths = SS.receipt_paths(obs, "SVY-0001")
        if state == "S0": return root, obs, paths
        receipt = fixtures._receipt()
        if state != "S1":
            receipt["rows"][0].update(record_id="OBS-0001", record_path="observations/OBS-0001-fact.md")
            receipt["records"] = ["OBS-0001"]
        if state in ("S3", "S5"):
            paths["staged"].mkdir(parents=True)
            for name in ("OBS-0001-fact.md", "index.md"):
                (paths["staged"] / name).write_text("Staged source.\n")
        if int(state[1:]) >= 4:
            receipt["completed"] = "2026-08-30"
        if int(state[1:]) >= 5:
            (obs / "OBS-0001-fact.md").write_text("Promoted source.\n")
        if int(state[1:]) >= 7:
            (tree / "log.md").write_text("## [2026-08-30] observation | survey batch SVY-0001\n")
        if int(state[1:]) >= 8:
            (tree / "journal").mkdir()
            (tree / "journal/2026-08.md").write_text("## [2026-08-30 12:00] review | survey sign-off SVY-0001\n")
            state_path = tree.joinpath(*SS.STATE_FILE_REL)
            state_path.parent.mkdir(parents=True)
            state_path.write_text(yaml.safe_dump(dict(candidates=[dict(id=fixtures.A1,
                state="observed" if state == "S8" else "ratified")])))
        paths["dir"].mkdir(parents=True, exist_ok=True)
        SS.write_receipt(paths["receipt"], receipt, contained_under=tree)
        return root, obs, paths

    def test_all_canonical_states_match_without_default_filesystem_reads(self):
        for state in SS.STATES:
            with self.subTest(state=state):
                root, obs, paths = self.fixture(state)
                self.assertEqual(SS.batch_state(paths["receipt"], obs), state)
                source_io = SourceIO(root)
                self.addCleanup(source_io.close)
                with mock.patch.object(Path, "read_text", side_effect=AssertionError("unguarded read")), \
                     mock.patch.object(Path, "is_file", side_effect=AssertionError("unguarded file probe")), \
                     mock.patch.object(Path, "is_dir", side_effect=AssertionError("unguarded directory probe")), \
                     mock.patch.object(Path, "exists", side_effect=AssertionError("unguarded existence probe")), \
                     mock.patch.object(Path, "resolve", side_effect=AssertionError("unguarded resolution")):
                    self.assertEqual(SS.batch_state(paths["receipt"], obs, _source_io=source_io), state)
                source_io.revalidate()

    def test_contained_receipt_alias_remains_refused_as_signed_artifact(self):
        root, obs, paths = self.fixture("S1")
        target = paths["receipt"].with_name("actual.yml")
        paths["receipt"].rename(target)
        paths["receipt"].symlink_to(target.name)
        source_io = SourceIO(root)
        self.addCleanup(source_io.close)
        with self.assertRaisesRegex(SS.SurveySheetError, "symlink"):
            SS.batch_state(paths["receipt"], obs, _source_io=source_io)

    def test_context_read_refusal_propagates_without_deriving_success(self):
        root, obs, paths = self.fixture("S1")
        source_io = SourceIO(root)
        self.addCleanup(source_io.close)
        with mock.patch.object(source_io, "read_text", side_effect=SourceIORefusal()):
            with self.assertRaises(SourceIORefusal):
                SS.batch_state(paths["receipt"], obs, _source_io=source_io)
