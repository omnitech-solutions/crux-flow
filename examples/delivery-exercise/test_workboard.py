from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from workboard import load_items, main, summarize


class WorkboardBaselineTests(unittest.TestCase):
    def test_summary(self):
        self.assertEqual(summarize([{"status": "done"}, {"status": "active"}]),
                         {"completed": 1, "active": 1})

    def test_cli(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "board.json"
            path.write_text(json.dumps([{"status": "done"}]), encoding="utf-8")
            self.assertEqual(load_items(path), [{"status": "done"}])
            self.assertEqual(main(["--input", str(path)]), 0)


if __name__ == "__main__":
    unittest.main()
