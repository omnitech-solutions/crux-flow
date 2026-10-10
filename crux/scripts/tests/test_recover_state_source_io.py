"""StateFile uses the caller's guarded source transport without another parser."""
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from admission_source_io import SourceIO, SourceIORefusal
from crux.arch.recover import StateFile


class StateFileSourceIOTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()/"repo";self.root.mkdir()
        self.path=self.root/"docs/arch/_recovered/state.yml"
        self.path.parent.mkdir(parents=True)

    def test_guarded_read_preserves_rows_stale_anchors_and_successor_counters(self):
        self.path.write_text("candidates:\n- id: anchor+2\n  status: pending\n"
            "stale_anchors:\n  anchor: {obs_id: OBS-0001, status: observed}\n"
            "successor_counters: {anchor: 2}\n")
        expected=StateFile(self.path,contained_under=self.root/"docs")
        with closing(SourceIO(self.root)) as source_io, \
                mock.patch.object(Path,"exists",side_effect=AssertionError("unguarded exists")), \
                mock.patch.object(Path,"read_text",side_effect=AssertionError("unguarded read")):
            guarded=StateFile(self.path,contained_under=self.root/"docs",_source_io=source_io)
            source_io.revalidate()
        self.assertEqual(guarded.rows,expected.rows)
        self.assertEqual(guarded.stale_anchors,expected.stale_anchors)
        self.assertEqual(guarded.successor_counters,expected.successor_counters)

    def test_guarded_absence_preserves_defaults(self):
        with closing(SourceIO(self.root)) as source_io:
            state=StateFile(self.path,_source_io=source_io)
        self.assertEqual((state.rows,state.stale_anchors,state.successor_counters),({},{},{}))

    def test_transport_refusal_is_not_absence_or_parse_error(self):
        outside=Path(self.temp.name)/"outside.yml"
        outside.write_text("candidates: []\n")
        self.path.symlink_to(outside)
        with closing(SourceIO(self.root)) as source_io, \
                mock.patch.object(Path,"read_text",side_effect=AssertionError("unguarded read")):
            with self.assertRaises(SourceIORefusal):StateFile(self.path,_source_io=source_io)

    def test_canonical_parse_failure_stays_bounded_and_matches_default(self):
        self.path.write_text("candidates: [UNTRUSTED_PRIVATE_MARKER\n")
        with self.assertRaises(ValueError) as default:StateFile(self.path)
        with closing(SourceIO(self.root)) as source_io:
            with self.assertRaises(ValueError) as guarded:StateFile(self.path,_source_io=source_io)
        self.assertEqual(str(guarded.exception),str(default.exception))
        self.assertNotIn("UNTRUSTED_PRIVATE_MARKER",str(guarded.exception))


if __name__=="__main__":unittest.main()
