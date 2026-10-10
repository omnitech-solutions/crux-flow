"""The canonical evidence grammar and containment consume guarded transport."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from admission_source_io import SourceIO, SourceIORefusal
import observation_evidence as evidence


class GuardedEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "source.py").write_text("x = 1\n")
        self.io = SourceIO(self.root)
        self.addCleanup(self.io.close)

    def test_file_alias_and_absence_match_canonical_result_without_raw_path_probes(self):
        (self.root / "alias.py").symlink_to("source.py")
        for name in ("source.py", "alias.py", "missing.py"):
            with self.subTest(name=name):
                expected = evidence.evidence_path_resolves(self.root, name)
                with mock.patch.object(Path, "resolve", side_effect=AssertionError("unguarded resolve")), \
                     mock.patch.object(Path, "is_file", side_effect=AssertionError("unguarded probe")):
                    self.assertEqual(evidence.evidence_path_resolves(self.root, name, _source_io=self.io), expected)
        self.io.revalidate()

    def test_entry_grammar_precedes_all_transport_calls(self):
        for entry in ("../source.py:1-1", "/source.py:1-1", "source.py", "~source.py:1-1"):
            with self.subTest(entry=entry), mock.patch.object(self.io, "resolve", side_effect=AssertionError("grammar must precede IO")):
                self.assertFalse(evidence.evidence_entry_resolves(self.root, entry, _source_io=self.io))
        self.assertTrue(evidence.evidence_entry_resolves(self.root, "source.py:1-1", _source_io=self.io))
        self.assertFalse(evidence.evidence_entry_resolves(None, "source.py:1-1", _source_io=self.io))

    def test_transport_refusal_propagates_and_canonical_concern_containment_remains(self):
        concern = self.root / "observations"
        concern.mkdir()
        (concern / "alias.py").symlink_to("../source.py")
        self.assertIsNone(evidence.resolve_contained(concern, "alias.py", _source_io=self.io))
        with mock.patch.object(self.io, "resolve", side_effect=SourceIORefusal()):
            with self.assertRaises(SourceIORefusal):
                evidence.evidence_entry_resolves(self.root, "source.py:1-1", _source_io=self.io)
