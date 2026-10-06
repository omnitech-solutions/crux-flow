"""Unit 1 (ADR-0075 decision 3): the capture protocol — the shared contract.

The parent side (`parse_and_validate`) is the trust boundary. These tests lock
its closed-schema strictness, its `json.loads`-only parse, the post-parse caps,
the deterministic ordering, and the `derive._cell` re-escape. `serialize`
(child side) is locked to emit exactly what the parent re-derives.
"""

import importlib
import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]  # crux/scripts
sys.path.insert(0, str(SCRIPTS))

capture = importlib.import_module("crux.arch.runtime.capture")
D = importlib.import_module("crux.arch.derive")


class SerializeTests(unittest.TestCase):
    def test_serialize_orders_and_is_byte_stable(self):
        routes = [
            {"method": "POST", "path": "/b", "name": "b_post"},
            {"method": "GET", "path": "/a", "name": "a_get"},
            {"method": "GET", "path": "/b", "name": "b_get"},
        ]
        models = [
            {"name": "Zebra", "table": "zebras", "fields": [
                {"name": "z", "type": "int", "nullable": False},
                {"name": "a", "type": "str", "nullable": True}]},
            {"name": "Apple", "table": "apples", "fields": []},
        ]
        out1 = capture.serialize(routes, models)
        out2 = capture.serialize(list(reversed(routes)), list(reversed(models)))
        self.assertEqual(out1, out2, "serialize is not order-independent / byte-stable")
        doc = json.loads(out1)
        self.assertEqual([r["path"] for r in doc["routes"]], ["/a", "/b", "/b"])
        # within same path, method sorts ascending
        self.assertEqual(doc["routes"][1]["method"], "GET")
        self.assertEqual(doc["routes"][2]["method"], "POST")
        self.assertEqual([m["name"] for m in doc["models"]], ["Apple", "Zebra"])
        self.assertEqual([f["name"] for f in doc["models"][1]["fields"]], ["a", "z"])

    def test_serialize_roundtrips_through_parse(self):
        routes = [{"method": None, "path": "/x", "name": "x"}]
        models = [{"name": "M", "table": "m", "fields": [{"name": "id", "type": "int", "nullable": False}]}]
        out = capture.serialize(routes, models)
        parsed = capture.parse_and_validate(out)
        self.assertEqual(parsed["routes"], [{"method": None, "path": "/x", "name": "x"}])
        self.assertEqual(parsed["models"][0]["fields"][0], {"name": "id", "type": "int", "nullable": False})


class ParseStrictnessTests(unittest.TestCase):
    def _valid(self):
        return {"routes": [{"method": "GET", "path": "/", "name": "root"}],
                "models": [{"name": "M", "table": "m",
                            "fields": [{"name": "id", "type": "int", "nullable": False}]}]}

    def test_accepts_valid(self):
        parsed = capture.parse_and_validate(json.dumps(self._valid()))
        self.assertEqual(parsed["routes"][0]["path"], "/")

    def test_rejects_non_json(self):
        with self.assertRaises(capture.CaptureError):
            capture.parse_and_validate("not json at all {")

    def test_rejects_non_object_root(self):
        with self.assertRaises(capture.CaptureError):
            capture.parse_and_validate("[1, 2, 3]")

    def test_rejects_unknown_top_key(self):
        doc = self._valid()
        doc["evil"] = "payload"
        with self.assertRaises(capture.CaptureError):
            capture.parse_and_validate(json.dumps(doc))

    def test_rejects_unknown_route_key(self):
        doc = self._valid()
        doc["routes"][0]["handler"] = "os.system"
        with self.assertRaises(capture.CaptureError):
            capture.parse_and_validate(json.dumps(doc))

    def test_rejects_unknown_field_key(self):
        doc = self._valid()
        doc["models"][0]["fields"][0]["default"] = "; rm -rf /"
        with self.assertRaises(capture.CaptureError):
            capture.parse_and_validate(json.dumps(doc))

    def test_rejects_wrong_typed_method(self):
        doc = self._valid()
        doc["routes"][0]["method"] = 42
        with self.assertRaises(capture.CaptureError):
            capture.parse_and_validate(json.dumps(doc))

    def test_rejects_wrong_typed_nullable(self):
        doc = self._valid()
        doc["models"][0]["fields"][0]["nullable"] = "yes"
        with self.assertRaises(capture.CaptureError):
            capture.parse_and_validate(json.dumps(doc))

    def test_json_loads_only_no_eval(self):
        # A pickle/eval payload is simply invalid JSON — rejected, never executed.
        with self.assertRaises(capture.CaptureError):
            capture.parse_and_validate("__import__('os').system('echo pwned')")


class CapsAndEscapeTests(unittest.TestCase):
    def test_route_count_capped(self):
        big = {"routes": [{"method": "GET", "path": f"/r{i:05d}", "name": f"n{i}"}
                          for i in range(capture.MAX_ROUTES + 500)],
               "models": []}
        parsed = capture.parse_and_validate(json.dumps(big))
        self.assertEqual(len(parsed["routes"]), capture.MAX_ROUTES)

    def test_model_count_capped(self):
        big = {"routes": [],
               "models": [{"name": f"M{i:05d}", "table": f"t{i}", "fields": []}
                          for i in range(capture.MAX_MODELS + 100)]}
        parsed = capture.parse_and_validate(json.dumps(big))
        self.assertEqual(len(parsed["models"]), capture.MAX_MODELS)

    def test_fields_capped(self):
        doc = {"routes": [], "models": [{"name": "M", "table": "m",
               "fields": [{"name": f"f{i:04d}", "type": "int", "nullable": False}
                          for i in range(capture.MAX_FIELDS + 50)]}]}
        parsed = capture.parse_and_validate(json.dumps(doc))
        self.assertEqual(len(parsed["models"][0]["fields"]), capture.MAX_FIELDS)

    def test_string_length_capped(self):
        doc = {"routes": [{"method": "GET", "path": "/" + "a" * 5000, "name": "n"}], "models": []}
        parsed = capture.parse_and_validate(json.dumps(doc))
        self.assertLessEqual(len(parsed["routes"][0]["path"]), capture.MAX_STR)

    def test_cell_reescape_neutralizes_table_breakers(self):
        # A forged value with pipe/backtick/newline must be neutralized by _cell.
        doc = {"routes": [{"method": "GET", "path": "/a|b`c\nd", "name": "n"}], "models": []}
        parsed = capture.parse_and_validate(json.dumps(doc))
        path = parsed["routes"][0]["path"]
        self.assertEqual(path, D._cell("/a|b`c\nd"))
        self.assertNotIn("\n", path)
        self.assertNotIn("`", path)


if __name__ == "__main__":
    unittest.main()
