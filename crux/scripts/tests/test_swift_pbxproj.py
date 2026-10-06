"""Tests for `crux.arch.packs.swift_pbxproj` (ADR-0130 clauses 2, 4 and 14).

Three parts, in the order the grammar is built:

  * The total, iterative OpenStep grammar: the `// !$*UTF8*$!` header and
    strict UTF-8, comment skipping, quoted/unquoted tokens, dicts and arrays,
    the 64-level nesting bound checked before each descent, refusal of
    duplicate keys, merge-conflict markers, XML and binary plists, and a line
    span per object and per value plus a line per list item.
  * The document model: `isa`/`rootObject`/`objects`, a missing
    `objects` or `rootObject` -> `malformed`, an `objectVersion` outside the
    fixture-pinned set {56, 76, 77} -> `unsupported-project-form` (reading
    proceeds per ADR-0130 clause 4), the four of the seven
    `project-unreadable` kinds this module can itself produce, and the per-file parse boundary that
    catches every exception — `RecursionError` and `MemoryError` included —
    and classifies it `malformed`.
  * A stdlib-only import check: `swift_pbxproj.py`'s own top-level imports,
    outside the standard library, must be empty.

Every fixture is a short OpenStep-syntax string written fresh here — never a
file on disk, and never copied from any checkout. `read_pbxproj` takes bytes
in and returns a model out; it never touches the filesystem.
"""

from __future__ import annotations

import ast
import importlib
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]      # crux/scripts
sys.path.insert(0, str(SCRIPTS))

swift_pbxproj = importlib.import_module("crux.arch.packs.swift_pbxproj")

HEADER = "// !$*UTF8*$!\n"

CACHE = Path(__file__).resolve().parent / "arch-corpus" / ".cache"
NETNEWSWIRE_PBXPROJ = CACHE / "netnewswire" / "NetNewsWire.xcodeproj" / "project.pbxproj"
FETCH_HINT = (
    "run `uv run python3 crux/scripts/tests/arch-corpus/fetch.py` (or clone "
    "NetNewsWire at its pinned SHA into "
    "crux/scripts/tests/arch-corpus/.cache/netnewswire/) to enable this "
    "parse-smoke check"
)


def _wrap(body: str, object_version: str = "56") -> bytes:
    """A minimal, valid document: the header, one object, objectVersion."""
    text = (
        HEADER
        + "// !$*FILE_ENCODING*$!\n"
        + "{\n"
        + "\tarchiveVersion = 1;\n"
        + "\tclasses = {\n"
        + "\t};\n"
        + f"\tobjectVersion = {object_version};\n"
        + "\tobjects = {\n"
        + body
        + "\t};\n"
        + "\trootObject = ROOT000000000000000001 /* Project object */;\n"
        + "}\n"
    )
    return text.encode("utf-8")


_ONE_OBJECT_BODY = (
    "\t\tROOT000000000000000001 /* Project object */ = {\n"
    "\t\t\tisa = PBXProject;\n"
    "\t\t\tattributes = {\n"
    "\t\t\t};\n"
    "\t\t};\n"
)


class GrammarTests(unittest.TestCase):
    """The OpenStep tokenizer/parser and its refusals (ADR-0130 clause 4)."""

    def test_valid_document_parses_to_a_document(self):
        result = swift_pbxproj.read_pbxproj(_wrap(_ONE_OBJECT_BODY))
        self.assertIsInstance(result, swift_pbxproj.PbxprojDocument)
        self.assertEqual(result.root_id, "ROOT000000000000000001")
        self.assertIn("ROOT000000000000000001", result.objects)
        self.assertEqual(result.objects["ROOT000000000000000001"]["isa"], "PBXProject")

    def test_missing_utf8_header_is_unsupported_format(self):
        raw = _wrap(_ONE_OBJECT_BODY).decode("utf-8")
        raw = raw.replace(HEADER, "", 1)
        result = swift_pbxproj.read_pbxproj(raw.encode("utf-8"))
        self.assertIsInstance(result, swift_pbxproj.PbxprojRefusal)
        self.assertEqual(result.residual_class, "project-unreadable")
        self.assertEqual(result.kind, "unsupported-format")

    def test_binary_plist_magic_is_unsupported_format(self):
        result = swift_pbxproj.read_pbxproj(b"bplist00" + b"\x00" * 32)
        self.assertIsInstance(result, swift_pbxproj.PbxprojRefusal)
        self.assertEqual(result.residual_class, "project-unreadable")
        self.assertEqual(result.kind, "unsupported-format")

    def test_xml_plist_is_unsupported_format(self):
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            "<plist version=\"1.0\"></plist>\n"
        )
        result = swift_pbxproj.read_pbxproj(xml.encode("utf-8"))
        self.assertIsInstance(result, swift_pbxproj.PbxprojRefusal)
        self.assertEqual(result.residual_class, "project-unreadable")
        self.assertEqual(result.kind, "unsupported-format")

    def test_invalid_utf8_is_undecodable(self):
        result = swift_pbxproj.read_pbxproj(HEADER.encode("utf-8") + b"\xff\xfe{")
        self.assertIsInstance(result, swift_pbxproj.PbxprojRefusal)
        self.assertEqual(result.residual_class, "project-unreadable")
        self.assertEqual(result.kind, "undecodable")

    def test_duplicate_key_is_malformed(self):
        body = (
            "\t\tROOT000000000000000001 /* Project object */ = {\n"
            "\t\t\tisa = PBXProject;\n"
            "\t\t\tisa = PBXProject;\n"
            "\t\t};\n"
        )
        result = swift_pbxproj.read_pbxproj(_wrap(body))
        self.assertIsInstance(result, swift_pbxproj.PbxprojRefusal)
        self.assertEqual(result.residual_class, "project-unreadable")
        self.assertEqual(result.kind, "malformed")

    def test_merge_conflict_marker_is_malformed(self):
        body = (
            "\t\tROOT000000000000000001 /* Project object */ = {\n"
            "<<<<<<< HEAD\n"
            "\t\t\tisa = PBXProject;\n"
            "=======\n"
            "\t\t\tisa = PBXProject;\n"
            ">>>>>>> branch\n"
            "\t\t};\n"
        )
        result = swift_pbxproj.read_pbxproj(_wrap(body))
        self.assertIsInstance(result, swift_pbxproj.PbxprojRefusal)
        self.assertEqual(result.residual_class, "project-unreadable")
        self.assertEqual(result.kind, "malformed")

    def test_comment_text_never_becomes_a_name(self):
        # The `/* Project object */` comment beside the id must never leak
        # into a parsed value.
        result = swift_pbxproj.read_pbxproj(_wrap(_ONE_OBJECT_BODY))
        self.assertIsInstance(result, swift_pbxproj.PbxprojDocument)
        for v in result.objects["ROOT000000000000000001"].values():
            self.assertNotIn("Project object", str(v))

    def test_quoted_string_escapes(self):
        body = (
            '\t\tROOT000000000000000001 = {\n'
            '\t\t\tisa = PBXProject;\n'
            '\t\t\tname = "line1\\nline2\\ttabbed";\n'
            "\t\t};\n"
        )
        result = swift_pbxproj.read_pbxproj(_wrap(body))
        self.assertIsInstance(result, swift_pbxproj.PbxprojDocument)
        self.assertEqual(
            result.objects["ROOT000000000000000001"]["name"], "line1\nline2\ttabbed"
        )

    def _nested(self, depth: int) -> str:
        """`depth` dict levels nested directly inside the object dict's own
        frame. Total stack depth at the leaf is `3 + depth`: the root
        document dict (1), the `objects` map (2), this object's own dict
        (3), plus `depth` further nested dicts."""
        open_braces = "".join(f"k{i} = {{\n" for i in range(depth))
        close_braces = "".join("};\n" for _ in range(depth))
        body = (
            "\t\tROOT000000000000000001 = {\n"
            "\t\t\tisa = PBXProject;\n"
            f"{open_braces}"
            "leafKey = leafValue;\n"
            f"{close_braces}"
            "\t\t};\n"
        )
        return body

    def test_nesting_at_63_is_admitted(self):
        result = swift_pbxproj.read_pbxproj(_wrap(self._nested(60)))
        self.assertIsInstance(result, swift_pbxproj.PbxprojDocument)

    def test_nesting_at_64_is_admitted(self):
        result = swift_pbxproj.read_pbxproj(_wrap(self._nested(61)))
        self.assertIsInstance(result, swift_pbxproj.PbxprojDocument)

    def test_nesting_at_65_is_refused_too_deep(self):
        result = swift_pbxproj.read_pbxproj(_wrap(self._nested(62)))
        self.assertIsInstance(result, swift_pbxproj.PbxprojRefusal)
        self.assertEqual(result.residual_class, "project-unreadable")
        self.assertEqual(result.kind, "too-deep")

    def test_crlf_input_line_numbers(self):
        body = (
            "\t\tROOT000000000000000001 = {\n"
            "\t\t\tisa = PBXProject;\n"
            "\t\t\tname = value;\n"
            "\t\t};\n"
        )
        raw = _wrap(body).replace(b"\n", b"\r\n")
        result = swift_pbxproj.read_pbxproj(raw)
        self.assertIsInstance(result, swift_pbxproj.PbxprojDocument)
        start, end = result.object_spans["ROOT000000000000000001"]
        # `_wrap`'s boilerplate (header, FILE_ENCODING comment, archiveVersion,
        # classes, objectVersion, the `objects = {` opener) is 8 lines, so the
        # object's own line is 9 and its close is 12 — CRLF must not double it.
        self.assertEqual(start, 9)
        self.assertEqual(end, 12)


class SurrogateEscapeGrammarTests(unittest.TestCase):
    """ADR-0130 clause 4 (strict UTF-8; any other grammar failure is
    `malformed`): a `\\U` escape is one UTF-16 code unit. A high
    surrogate followed at once by a `\\U` low surrogate is one scalar; any
    other surrogate is a grammar failure, `malformed`. The CLI cases are in
    `test_swift_surrogate_escapes.py`."""

    @staticmethod
    def _named(escaped: str):
        body = (
            "\t\tROOT000000000000000001 /* Project object */ = {\n"
            "\t\t\tisa = PBXProject;\n"
            f'\t\t\tname = "N{escaped}";\n'
            "\t\t};\n"
        )
        return swift_pbxproj.read_pbxproj(_wrap(body))

    def test_a_pair_decodes_to_one_scalar(self):
        result = self._named("\\UD83D\\UDE00")
        self.assertIsInstance(result, swift_pbxproj.PbxprojDocument)
        name = result.objects["ROOT000000000000000001"]["name"]
        self.assertEqual(name, "N\U0001F600")
        name.encode("utf-8")

    def test_the_pair_bounds_decode(self):
        result = self._named("\\UDBFF\\UDFFF\\UD800\\UDC00")
        self.assertEqual(result.objects["ROOT000000000000000001"]["name"], "N\U0010FFFF\U00010000")

    def test_each_lone_surrogate_is_malformed(self):
        for escaped in ("\\UD800", "\\UDBFF", "\\UDC00", "\\UDFFF", "\\UD800\\U0041",
                        "\\UD800x", "\\UDE00\\UD83D", "\\UD800\\UD800", "\\UD800\\n"):
            with self.subTest(escaped=escaped):
                result = self._named(escaped)
                self.assertIsInstance(result, swift_pbxproj.PbxprojRefusal)
                self.assertEqual((result.residual_class, result.kind),
                                 ("project-unreadable", "malformed"))

    def test_a_non_surrogate_escape_either_side_of_the_block_decodes(self):
        result = self._named("\\UD7FF\\UE000")
        self.assertEqual(result.objects["ROOT000000000000000001"]["name"], "N\ud7ff\ue000")


class DocumentModelTests(unittest.TestCase):
    """isa/rootObject/objects, objectVersion, and the closed kind map
    (ADR-0130 clauses 4 and 14)."""

    def test_object_span_covers_the_id_to_close(self):
        result = swift_pbxproj.read_pbxproj(_wrap(_ONE_OBJECT_BODY))
        start, end = result.object_spans["ROOT000000000000000001"]
        self.assertLess(start, end)

    def test_value_line_recorded_for_a_field(self):
        result = swift_pbxproj.read_pbxproj(_wrap(_ONE_OBJECT_BODY))
        line = result.value_lines[("ROOT000000000000000001", "isa")]
        self.assertIsInstance(line, int)

    def test_list_item_lines_recorded(self):
        body = (
            "\t\tROOT000000000000000001 = {\n"
            "\t\t\tisa = PBXProject;\n"
            "\t\t\ttargets = (\n"
            "\t\t\t\tAAA000000000000000000001 /* T1 */,\n"
            "\t\t\t\tBBB000000000000000000002 /* T2 */,\n"
            "\t\t\t);\n"
            "\t\t};\n"
        )
        result = swift_pbxproj.read_pbxproj(_wrap(body))
        self.assertIsInstance(result, swift_pbxproj.PbxprojDocument)
        lines = result.list_item_lines[("ROOT000000000000000001", "targets")]
        self.assertEqual(len(lines), 2)
        self.assertLess(lines[0], lines[1])

    def test_missing_objects_key_is_malformed(self):
        text = (
            HEADER
            + "{\n"
            + "\tarchiveVersion = 1;\n"
            + "\tobjectVersion = 56;\n"
            + "\trootObject = ROOT000000000000000001;\n"
            + "}\n"
        )
        result = swift_pbxproj.read_pbxproj(text.encode("utf-8"))
        self.assertIsInstance(result, swift_pbxproj.PbxprojRefusal)
        self.assertEqual(result.residual_class, "project-unreadable")
        self.assertEqual(result.kind, "malformed")

    def test_missing_root_object_key_is_malformed(self):
        text = (
            HEADER
            + "{\n"
            + "\tarchiveVersion = 1;\n"
            + "\tobjectVersion = 56;\n"
            + "\tobjects = {\n"
            + _ONE_OBJECT_BODY
            + "\t};\n"
            + "}\n"
        )
        result = swift_pbxproj.read_pbxproj(text.encode("utf-8"))
        self.assertIsInstance(result, swift_pbxproj.PbxprojRefusal)
        self.assertEqual(result.residual_class, "project-unreadable")
        self.assertEqual(result.kind, "malformed")

    def test_object_version_56_is_supported(self):
        result = swift_pbxproj.read_pbxproj(_wrap(_ONE_OBJECT_BODY, object_version="56"))
        self.assertIsInstance(result, swift_pbxproj.PbxprojDocument)
        self.assertFalse(result.unsupported_object_version)

    def test_object_version_76_is_supported(self):
        result = swift_pbxproj.read_pbxproj(_wrap(_ONE_OBJECT_BODY, object_version="76"))
        self.assertIsInstance(result, swift_pbxproj.PbxprojDocument)
        self.assertFalse(result.unsupported_object_version)

    def test_object_version_77_is_supported(self):
        result = swift_pbxproj.read_pbxproj(_wrap(_ONE_OBJECT_BODY, object_version="77"))
        self.assertIsInstance(result, swift_pbxproj.PbxprojDocument)
        self.assertFalse(result.unsupported_object_version)

    def test_object_version_outside_pinned_set_is_unsupported_project_form_but_reads(self):
        result = swift_pbxproj.read_pbxproj(_wrap(_ONE_OBJECT_BODY, object_version="46"))
        self.assertIsInstance(result, swift_pbxproj.PbxprojDocument)
        self.assertTrue(result.unsupported_object_version)
        # Reading proceeds per ADR-0130 clause 4 — the object is still there.
        self.assertIn("ROOT000000000000000001", result.objects)

    def test_recursion_error_during_parse_is_malformed(self):
        # Positive control: WITHOUT the per-file boundary's catch-all, a
        # seeded RecursionError propagates instead of classifying. We prove
        # the boundary catches it by making the tokenizer itself blow the
        # stack via a monkeypatched recursion bomb, and asserting the module
        # still returns a classified refusal rather than raising.
        import unittest.mock as mock

        def _bomb(*a, **kw):
            def recurse(n):
                return recurse(n + 1)
            return recurse(0)

        with mock.patch.object(swift_pbxproj, "_tokenize", side_effect=_bomb):
            result = swift_pbxproj.read_pbxproj(_wrap(_ONE_OBJECT_BODY))
        self.assertIsInstance(result, swift_pbxproj.PbxprojRefusal)
        self.assertEqual(result.residual_class, "project-unreadable")
        self.assertEqual(result.kind, "malformed")

    def test_python_recursion_actually_raises_recursion_error(self):
        # Not a positive control for this module: this proves only that
        # UNBOUNDED PYTHON RECURSION raises `RecursionError` at all (a
        # sanity check on the interpreter's own recursion limit, not on
        # this module). The real positive control for `read_pbxproj`'s
        # boundary is `test_recursion_error_during_parse_is_malformed`
        # above and the mock-based tests beside it, which seed the error
        # THROUGH the module's own guarded call path.
        def recurse(n):
            return recurse(n + 1)

        with self.assertRaises(RecursionError):
            recurse(0)

    def test_memory_error_during_parse_is_malformed(self):
        import unittest.mock as mock

        def _bomb(*a, **kw):
            raise MemoryError("seeded")

        with mock.patch.object(swift_pbxproj, "_tokenize", side_effect=_bomb):
            result = swift_pbxproj.read_pbxproj(_wrap(_ONE_OBJECT_BODY))
        self.assertIsInstance(result, swift_pbxproj.PbxprojRefusal)
        self.assertEqual(result.residual_class, "project-unreadable")
        self.assertEqual(result.kind, "malformed")

    def test_closed_kind_set(self):
        # This module can itself only ever emit four of the seven closed
        # kinds — `not-regular` and `read-failed` come from the caller's
        # safe-read boundary (core._safe_read_bytes), and `entity-declaration`
        # is the workspace XML reader's kind (ADR-0130 clause 11), never this grammar's.
        self.assertEqual(
            swift_pbxproj.PBXPROJ_KINDS,
            frozenset({"undecodable", "unsupported-format", "malformed", "too-deep"}),
        )


class StdlibOnlyImportTests(unittest.TestCase):
    """A stdlib-only import check: a planted third-party import turns it red."""

    def test_top_level_imports_are_stdlib_or_crux_own(self):
        source = (SCRIPTS / "crux" / "arch" / "packs" / "swift_pbxproj.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module.split(".")[0]] if node.module else []
            else:
                continue
            for name in names:
                if name in sys.stdlib_module_names or name == "__future__":
                    continue
                offenders.append(name)
        self.assertEqual(offenders, [], f"non-stdlib top-level imports: {offenders}")

    def test_module_never_imports_swift_dot_py(self):
        source = (SCRIPTS / "crux" / "arch" / "packs" / "swift_pbxproj.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("packs.swift import", source)
        self.assertNotIn("packs import swift\n", source)
        self.assertNotRegex(source, r"from \.\s*import\s+swift\b")


class NetNewsWireParseSmokeTests(unittest.TestCase):
    """The real NetNewsWire `project.pbxproj` (MIT, pinned SHA), read as a
    parse-smoke check only — never copied into a fixture. Skipped when the
    cache is absent, per the same `FETCH_HINT` convention `test_arch_corpus.py`
    uses for its own cache-dependent tests."""

    @classmethod
    def setUpClass(cls):
        if not NETNEWSWIRE_PBXPROJ.exists():
            raise unittest.SkipTest(f"NetNewsWire cache absent — {FETCH_HINT}")

    def test_netnewswire_pbxproj_parses_with_no_refusal(self):
        raw = NETNEWSWIRE_PBXPROJ.read_bytes()
        result = swift_pbxproj.read_pbxproj(raw)
        self.assertIsInstance(
            result, swift_pbxproj.PbxprojDocument,
            f"expected a document, got a refusal: {result!r}",
        )
        self.assertFalse(result.unsupported_object_version)
        self.assertGreater(len(result.objects), 0)
        # Recorded for the report, not asserted to an exact figure: the
        # object count is evidence a real, large project.pbxproj parsed
        # whole, not a golden this unit owns.
        self._object_count = len(result.objects)


class NestedListItemLineTests(unittest.TestCase):
    """ADR-0130 clauses 2 and 14, list items: a list nested directly
    in a list is ONE item of the outer list. Its own strings are not items of
    the outer list, so they never enter the outer list's line index; they
    shifted every later item's line by one per nested string."""

    BODY = (
        "\t\tROOT000000000000000001 = {\n"      # line 5
        "\t\t\tisa = PBXProject;\n"            # 6
        "\t\t\tf = (\n"                        # 7
        "\t\t\t\t(\n"                          # 8  item 0 opens
        "\t\t\t\t\ta,\n"                      # 9
        "\t\t\t\t\t(\n"                       # 10
        "\t\t\t\t\t\tb,\n"                   # 11
        "\t\t\t\t\t),\n"                      # 12
        "\t\t\t\t),\n"                         # 13
        "\t\t\t\tc,\n"                         # 14 item 1
        "\t\t\t\t{\n"                          # 15 item 2 opens
        "\t\t\t\t\tk = (\n"                   # 16
        "\t\t\t\t\t\td,\n"                   # 17
        "\t\t\t\t\t);\n"                      # 18
        "\t\t\t\t},\n"                         # 19
        "\t\t\t\te,\n"                         # 20 item 3
        "\t\t\t);\n"
        "\t\t};\n"
    )

    def test_each_outer_item_has_exactly_its_own_line(self):
        result = swift_pbxproj.read_pbxproj(_wrap(self.BODY))
        self.assertIsInstance(result, swift_pbxproj.PbxprojDocument)
        items = result.objects["ROOT000000000000000001"]["f"]
        self.assertEqual(items, [["a", ["b"]], "c", {"k": ["d"]}, "e"])
        lines = result.list_item_lines[("ROOT000000000000000001", "f")]
        start = result.object_spans["ROOT000000000000000001"][0]
        self.assertEqual([line - start for line in lines], [3, 9, 10, 15])

    def test_first_item_line_finds_each_item(self):
        result = swift_pbxproj.read_pbxproj(_wrap(self.BODY))
        start = result.object_spans["ROOT000000000000000001"][0]
        found = [result.first_item_line("ROOT000000000000000001", "f", item) - start
                 for item in (["a", ["b"]], "c", {"k": ["d"]}, "e")]
        self.assertEqual(found, [3, 9, 10, 15])
        self.assertIsNone(result.first_item_line("ROOT000000000000000001", "f", "a"))

    def test_a_field_that_is_not_a_list_indexes_no_item(self):
        """`first_item_line` never iterates a string's characters or a
        dictionary's keys as items."""
        body = ("\t\tROOT000000000000000001 = {\n\t\t\tisa = PBXProject;\n"
                "\t\t\tf = \"abc\";\n\t\t\tg = {\n\t\t\t\ta = b;\n\t\t\t};\n\t\t};\n")
        result = swift_pbxproj.read_pbxproj(_wrap(body))
        self.assertIsNone(result.first_item_line("ROOT000000000000000001", "f", "a"))
        self.assertIsNone(result.first_item_line("ROOT000000000000000001", "g", "a"))


_T4_BODY = (
    "\t\tROOT000000000000000001 /* Project object */ = {\n"
    "\t\t\tisa = PBXProject;\n"
    "\t\t\tmainGroup = (MAIN);\n"
    "\t\t\tname = App;\n"
    "\t\t\tempty = \"\";\n"
    "\t\t\tlisted = ();\n"
    "\t\t\tkeyed = {MAIN = x;};\n"
    "\t\t\tnone = {};\n"
    "\t\t\tsettings = {A = B;};\n"
    "\t\t};\n"
    "\t\tMAIN = {\n"
    "\t\t\tisa = (PBXGroup);\n"
    "\t\t};\n"
)


class RootObjectTests(unittest.TestCase):
    """ADR-0130 clauses 4 and 14: a `rootObject`
    that names no object, or an object that is not a `PBXProject`, is the
    same missing-rootObject condition as an absent one: `malformed`."""

    def _read(self, root_id, body):
        text = (HEADER + "{\n\tarchiveVersion = 1;\n\tobjectVersion = 56;\n\tobjects = {\n" + body
                + "\t};\n\trootObject = %s;\n}\n" % root_id)
        return swift_pbxproj.read_pbxproj(text.encode("utf-8"))

    def test_a_root_object_naming_no_project_is_malformed(self):
        body = (_ONE_OBJECT_BODY + "\t\tMAIN = {\n\t\t\tisa = PBXGroup;\n\t\t};\n"
                "\t\tODD = {\n\t\t\tisa = (PBXProject);\n\t\t};\n\t\tBARE = {\n\t\t};\n")
        for root_id in ("DANGLING", "MAIN", "ODD", "BARE"):
            with self.subTest(root_id=root_id):
                result = self._read(root_id, body)
                self.assertEqual(result, swift_pbxproj.PbxprojRefusal("project-unreadable", "malformed"))

    def test_control_a_root_object_naming_a_project_reads(self):
        result = self._read("ROOT000000000000000001", _ONE_OBJECT_BODY)
        self.assertIsInstance(result, swift_pbxproj.PbxprojDocument)


class FieldTypeHelperTests(unittest.TestCase):
    """ADR-0130 clauses 2 and 14: the four typed reads both Xcode readers
    use. A value of another type is a dangling id or one
    `unsupported-project-form` line, and it never raises."""

    ROOT = "ROOT000000000000000001"

    def setUp(self):
        self.doc = swift_pbxproj.read_pbxproj(_wrap(_T4_BODY))
        self.assertIsInstance(self.doc, swift_pbxproj.PbxprojDocument)

    def test_id_field(self):
        doc, root = self.doc, self.ROOT
        self.assertEqual(swift_pbxproj.id_field(doc, root, "name"), "App")
        self.assertEqual(swift_pbxproj.id_field(doc, root, "empty"), "")
        self.assertIsNone(swift_pbxproj.id_field(doc, root, "absent"))
        self.assertIsNone(swift_pbxproj.id_field(doc, "NO-SUCH-OBJECT", "name"))
        for field_name in ("mainGroup", "listed", "keyed", "none"):
            with self.subTest(field=field_name):
                self.assertIs(swift_pbxproj.id_field(doc, root, field_name), swift_pbxproj.NOT_AN_ID)

    def test_not_an_id_is_hashable_and_never_a_key(self):
        marker = swift_pbxproj.NOT_AN_ID
        self.assertIsNone(self.doc.objects.get(marker))
        self.assertNotIn(marker, self.doc.objects)
        self.assertNotIn(marker, {"MAIN", self.ROOT})
        self.assertTrue(marker)
        self.assertNotEqual(marker, "")

    def test_isa_of(self):
        self.assertEqual(swift_pbxproj.isa_of(self.doc.objects[self.ROOT]), "PBXProject")
        self.assertIsNone(swift_pbxproj.isa_of(self.doc.objects["MAIN"]))
        self.assertIsNone(swift_pbxproj.isa_of({}))
        self.assertIsNone(swift_pbxproj.isa_of(None))
        self.assertIsNone(swift_pbxproj.isa_of(["PBXGroup"]))

    def test_dict_field(self):
        detail = "an exception set's platformFiltersByRelativePath field is not a dictionary and is not applied"
        doc, root = self.doc, self.ROOT
        self.assertEqual(swift_pbxproj.dict_field(doc, root, "settings", "p", []), {"A": "B"})
        for field_name in ("none", "absent"):
            with self.subTest(field=field_name):
                residuals = []
                self.assertEqual(swift_pbxproj.dict_field(doc, root, field_name, "p", residuals), {})
                self.assertEqual(residuals, [])
        # A field of another type, named by the detail's key: renders one
        # line at its own value line.
        for field_name in ("name", "empty", "listed", "mainGroup"):
            with self.subTest(field=field_name):
                residuals = []
                with mock.patch.dict(swift_pbxproj.NON_DICT_FIELD_DETAILS, {field_name: detail}):
                    self.assertEqual(swift_pbxproj.dict_field(doc, root, field_name, "p", residuals), {})
                line = doc.value_lines[(root, field_name)]
                self.assertEqual(residuals, [("unsupported-project-form", "p", line, detail)])

    def test_dict_field_renders_once_per_seen_set(self):
        seen, residuals = set(), []
        with mock.patch.dict(swift_pbxproj.NON_DICT_FIELD_DETAILS, {"name": "d"}):
            for _ in range(3):
                swift_pbxproj.dict_field(self.doc, self.ROOT, "name", "p", residuals, seen)
            self.assertEqual(len(residuals), 1)
            swift_pbxproj.dict_field(self.doc, self.ROOT, "name", "p", residuals)
            self.assertEqual(len(residuals), 2)

    def test_string_field(self):
        detail = "a target's name field is not a string and is not read"
        doc, root = self.doc, self.ROOT
        residuals = []
        self.assertEqual(swift_pbxproj.string_field(doc, root, "name", "p", residuals), "App")
        self.assertEqual(swift_pbxproj.string_field(doc, root, "empty", "p", residuals), "")
        self.assertIsNone(swift_pbxproj.string_field(doc, root, "absent", "p", residuals))
        self.assertEqual(residuals, [])
        for field_name in ("mainGroup", "listed", "keyed", "none"):
            with self.subTest(field=field_name):
                residuals = []
                with mock.patch.dict(swift_pbxproj.NON_STRING_FIELD_DETAILS, {field_name: detail}):
                    self.assertIsNone(swift_pbxproj.string_field(doc, root, field_name, "p", residuals))
                line = doc.value_lines[(root, field_name)]
                self.assertEqual(residuals, [("unsupported-project-form", "p", line, detail)])
        # Without a residual list the read is typed and renders nothing
        # (`productType`, whose caller renders its own line).
        self.assertIsNone(swift_pbxproj.string_field(doc, root, "listed", "p", None))

    def test_string_field_renders_once_per_seen_set(self):
        seen, residuals = set(), []
        with mock.patch.dict(swift_pbxproj.NON_STRING_FIELD_DETAILS, {"listed": "d"}):
            for _ in range(3):
                swift_pbxproj.string_field(self.doc, self.ROOT, "listed", "p", residuals, seen)
        self.assertEqual(len(residuals), 1)

    def test_a_missing_value_line_falls_back_to_the_object_first_line(self):
        doc = swift_pbxproj.PbxprojDocument(
            root_id="R", objects={"R": {"isa": "PBXProject", "name": ["x"], "buildSettings": "y"}},
            object_spans={"R": (7, 9)})
        residuals = []
        swift_pbxproj.string_field(doc, "R", "name", "p", residuals)
        swift_pbxproj.dict_field(doc, "R", "buildSettings", "p", residuals)
        self.assertEqual([r[2] for r in residuals], [7, 7])


if __name__ == "__main__":
    unittest.main()
