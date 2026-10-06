"""Unit 5 (ADR-0075 decision 6): advisory re-entry, outside `arch/`.

The advisory lands in `<docs_dir>/inbox/`, marked `runtime_sourced: true`, with
provenance, no in-body clock, and no absolute paths. It writes NOTHING under
`arch/` and never touches the spine.
"""

import importlib
import re
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
reentry = importlib.import_module("crux.arch.runtime.reentry")

_CAPTURE = {
    "routes": [
        {"method": "GET", "path": "/users", "name": "list_users"},
        {"method": None, "path": "blog/articles/", "name": "articles"},
    ],
    "models": [
        {"name": "Article", "table": "blog_article",
         "fields": [{"name": "title", "type": "CharField", "nullable": False},
                    {"name": "published", "type": "BooleanField", "nullable": True}]},
    ],
}

_ISO_CLOCK = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}")


class WriteAdvisoryTests(unittest.TestCase):
    def setUp(self):
        self.docs = Path(tempfile.mkdtemp())

    def test_writes_into_inbox_only(self):
        p = reentry.write_advisory(_CAPTURE, docs_dir=self.docs,
                                   target="runtime_django.settings", framework="django")
        self.assertEqual(p.parent.resolve(), (self.docs / "inbox").resolve())
        self.assertFalse((self.docs / "arch").exists(), "advisory must not create arch/")

    def test_frontmatter_marks_runtime_sourced(self):
        p = reentry.write_advisory(_CAPTURE, docs_dir=self.docs,
                                   target="x:app", framework="fastapi")
        text = p.read_text()
        self.assertIn("runtime_sourced: true", text)
        self.assertIn("provenance: arch-runtime-introspection", text)

    def test_no_in_body_clock(self):
        p = reentry.write_advisory(_CAPTURE, docs_dir=self.docs, target="x:app", framework="app")
        self.assertIsNone(_ISO_CLOCK.search(p.read_text()), "advisory carries an in-body clock")

    def test_no_absolute_paths(self):
        p = reentry.write_advisory(_CAPTURE, docs_dir=self.docs,
                                   target="/Users/somebody/secret/app.py:app", framework="app")
        text = p.read_text()
        self.assertNotIn("/Users/", text, "advisory leaked an absolute path")

    def test_values_are_scrubbed(self):
        hostile = {"routes": [{"method": "GET", "path": "/a|b`c\nd", "name": "n"}], "models": []}
        p = reentry.write_advisory(hostile, docs_dir=self.docs, target="x:app", framework="app")
        text = p.read_text()
        # newline and backtick neutralized; pipe escaped so the table is intact.
        self.assertNotIn("/a|b`c", text)

    def test_renders_routes_and_models(self):
        p = reentry.write_advisory(_CAPTURE, docs_dir=self.docs, target="x", framework="django")
        text = p.read_text()
        self.assertIn("blog_article", text)
        self.assertIn("published", text)
        self.assertIn("| list_users |", text)

    def test_refuses_to_write_outside_inbox(self):
        with self.assertRaises(ValueError):
            reentry._assert_in_inbox(self.docs / "arch" / "x.md", self.docs)

    def test_foldback_candidate_written_to_inbox(self):
        p = reentry.write_foldback_candidate(_CAPTURE, docs_dir=self.docs)
        self.assertEqual(p.parent.resolve(), (self.docs / "inbox").resolve())
        self.assertTrue(p.name.endswith(".json"))


if __name__ == "__main__":
    unittest.main()
