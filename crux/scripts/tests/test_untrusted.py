"""Tests for `untrusted.py` — the one bound-and-redact rendering every reader
of the observations concern and of a survey surface uses before an untrusted
value reaches an output channel.

Shape of this suite: **byte-parity first, then the hostile cases.** The parity
cases are the load-bearing ones. `redact` replaced ~130 `!r` and bare
interpolations across eight modules, and it may only do that if a benign value
renders EXACTLY as it did before — otherwise the routing is a message rewrite
wearing a security fix's clothes, and every existing refusal test that quotes a
message is a false green waiting to flip.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


U = _load("untrusted")


class ByteParityTests(unittest.TestCase):
    """A benign value renders exactly as `!r` / `str()` did before routing."""

    BENIGN = ["", "ratify", "SVY-0001", "a b c", "it's", 'say "hi"',
              "0123456789abcdef", "storage/records", "café", "日本語",
              "a" * U.LIMIT]

    def test_quoted_matches_repr_for_every_benign_string(self):
        for value in self.BENIGN:
            with self.subTest(value=value):
                self.assertEqual(U.redact(value), repr(value))

    def test_unquoted_matches_str_for_every_benign_string(self):
        for value in self.BENIGN:
            with self.subTest(value=value):
                self.assertEqual(U.redact(value, quoted=False), value)

    def test_quoted_matches_repr_for_benign_non_strings(self):
        for value in [None, 1, 0, True, 3.5, ["a", "b"], {"k": "v"}, ("x",)]:
            with self.subTest(value=value):
                self.assertEqual(U.redact(value), repr(value))

    def test_unquoted_matches_str_for_benign_non_strings(self):
        for value in [None, 1, True, 3.5, Path("a/b")]:
            with self.subTest(value=value):
                self.assertEqual(U.redact(value, quoted=False), str(value))


class BoundTests(unittest.TestCase):
    """The bound is on the RENDERED value, and it is visible in the output."""

    def test_a_long_value_is_truncated_to_the_limit(self):
        out = U.redact("x" * 10_000)
        self.assertLessEqual(len(out), U.LIMIT + 80)
        self.assertIn("x" * 20, out)
        self.assertNotIn("x" * (U.LIMIT + 1), out)

    def test_truncation_names_the_true_length_so_it_cannot_pass_as_whole(self):
        out = U.redact("x" * 10_000)
        self.assertIn("10000", out)
        self.assertIn("truncated", out)

    def test_a_value_exactly_at_the_limit_is_not_marked(self):
        self.assertEqual(U.redact("x" * U.LIMIT), repr("x" * U.LIMIT))

    def test_the_bound_applies_to_non_strings_too(self):
        out = U.redact(["y" * 10_000], quoted=False)
        self.assertLess(len(out), 400)
        self.assertIn("truncated", out)

    def test_an_explicit_limit_overrides_the_default(self):
        self.assertIn("truncated", U.redact("abcdef", limit=3))
        self.assertEqual(U.redact("abc", limit=3), repr("abc"))


class RedactionTests(unittest.TestCase):
    """Every character that can forge structure on an output channel is
    replaced, and the replacement is counted in the output."""

    HOSTILE = {
        "ansi": "\x1b[2J\x1b[31mERROR",
        "newline": "ok\nCHK-OBS-FAKE: everything is fine",
        "carriage_return": "ok\rfine",
        "tab": "a\tb",
        "nul": "a\x00b",
        "bidi_override": "a‮b",
        "zero_width": "a​b",
        "line_separator": "a b",
        "nbsp": "a b",
        "surrogate": "a\ud800b",
        "private_use": "ab",
    }

    def test_every_hostile_character_is_replaced(self):
        for name, value in self.HOSTILE.items():
            with self.subTest(name=name):
                out = U.redact(value)
                for ch in value:
                    if not ch.isprintable():
                        self.assertNotIn(ch, out,
                                         f"{name}: {ch!r} survived redaction")

    def test_a_newline_cannot_forge_a_second_line(self):
        out = U.redact(self.HOSTILE["newline"], quoted=False)
        self.assertEqual(out.count("\n"), 0)

    def test_redaction_is_counted_in_the_output(self):
        out = U.redact("a\x1b\x1b\x1bb")
        self.assertIn("3", out)
        self.assertIn("redacted", out)

    def test_a_clean_value_carries_no_note(self):
        self.assertNotIn("redacted", U.redact("perfectly ordinary"))

    def test_redaction_survives_the_repr_round_trip_without_double_escaping(self):
        """The replacement is a PRINTABLE character, so `repr` does not escape
        it a second time — a `\\x1b` spelled as a literal backslash would."""
        out = U.redact("a\x1bb")
        self.assertNotIn("\\\\", out)

    def test_a_hostile_value_is_both_redacted_and_bounded(self):
        out = U.redact("\x1b" * 10_000)
        self.assertIn("redacted", out)
        self.assertIn("truncated", out)

    def test_a_nested_string_inside_a_non_string_is_redacted(self):
        out = U.redact({"k": "a\x1bb"})
        self.assertNotIn("\x1b", out)


class ObjectTests(unittest.TestCase):
    """A value whose own `repr` raises must not take the refusal down with it —
    the reader is already on its error path when it calls this."""

    def test_a_raising_repr_degrades_rather_than_propagates(self):
        class Hostile:
            def __repr__(self):
                raise RuntimeError("boom")

            def __str__(self):
                raise RuntimeError("boom")

        out = U.redact(Hostile())
        self.assertIn("unrenderable", out)
        self.assertNotIn("boom", out)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()


class ParseProblemTests(unittest.TestCase):
    """The second leg of the same mechanism: a PARSER's message is never
    quoted, at any length.

    `redact` bounds a value; bounding is the wrong answer for a YAML parse
    error, because PyYAML's `str(exc)` embeds `Mark.get_snippet()` — the
    offending source LINE, verbatim — and 120 bytes of a file the reader does
    not own is still 120 bytes of a file the reader does not own. The position
    is kept because line and column are integers and hold no content.
    """

    class _Mark:
        line = 41
        column = 6

    class _Exc(Exception):
        def __init__(self):
            super().__init__("while scanning: found character '\\t' at "
                             "SECRET_TOKEN=hunter2")
            self.problem_mark = ParseProblemTests._Mark()

    def test_no_byte_of_the_parser_message_survives(self):
        out = U.parse_problem("observation frontmatter", "OBS-0001-x.md",
                              self._Exc())
        self.assertNotIn("SECRET_TOKEN", out)
        self.assertNotIn("hunter2", out)
        self.assertNotIn("while scanning", out)

    def test_the_position_is_reported_one_based(self):
        out = U.parse_problem("candidate state file", "state.yml", self._Exc())
        self.assertIn("line 42", out)
        self.assertIn("column 7", out)

    def test_the_subject_and_the_name_both_appear(self):
        out = U.parse_problem("candidate state file", "state.yml", self._Exc())
        self.assertIn("candidate state file", out)
        self.assertIn("state.yml", out)

    def test_a_hostile_filename_is_routed_through_redact(self):
        out = U.parse_problem("observation frontmatter",
                              "OBS-0001-\x1b[2K" + "z" * 500 + ".md",
                              self._Exc())
        self.assertNotIn("\x1b", out)
        self.assertIn("truncated", out)

    def test_a_parser_without_a_mark_still_yields_a_message(self):
        out = U.parse_problem("review sheet", "sheet.yml", ValueError("nope"))
        self.assertNotIn("nope", out)
        self.assertIn("sheet.yml", out)

    def test_a_mark_with_non_integer_position_is_dropped_not_quoted(self):
        class Weird(Exception):
            pass

        exc = Weird()
        exc.problem_mark = type("M", (), {"line": "1\nCHK-FAKE: ok",
                                          "column": 0})()
        out = U.parse_problem("review sheet", "sheet.yml", exc)
        self.assertNotIn("CHK-FAKE", out)
        self.assertEqual(out.count("\n"), 0)


class MessageLimitTests(unittest.TestCase):
    """Two bounds, because there are two kinds of thing being bounded.

    `LIMIT` bounds one VALUE — a cell, an id, a filename. `MESSAGE_LIMIT`
    bounds a whole composed refusal being re-quoted by an outer handler, whose
    untrusted parts are already routed and whose remaining bytes are this
    project's own prose. Routing a composed message at `LIMIT` cut a real
    two-sentence containment refusal in half and made its own test fail —
    which is how the two bounds came to be named separately.
    """

    def test_the_message_bound_is_far_wider_than_the_value_bound(self):
        self.assertGreater(U.MESSAGE_LIMIT, U.LIMIT * 10)

    def test_a_real_multi_sentence_refusal_survives_intact(self):
        message = ("docs_dir 'escape' resolves to /tmp/x/victim-tree, which "
                   "is not contained under the repo root /tmp/x — a symlinked "
                   "or escaping tree dir is refused")
        self.assertEqual(U.redact(message, quoted=False, limit=U.MESSAGE_LIMIT),
                         message)

    def test_the_message_bound_still_stops_a_ten_megabyte_exception(self):
        out = U.redact(RuntimeError("x" * 10_000_000), quoted=False,
                       limit=U.MESSAGE_LIMIT)
        self.assertLess(len(out), U.MESSAGE_LIMIT + 80)
        self.assertIn("truncated", out)

    def test_the_message_bound_still_redacts(self):
        out = U.redact("ok\nCHK-OBS-FAKE: fine", quoted=False,
                       limit=U.MESSAGE_LIMIT)
        self.assertEqual(out.count("\n"), 0)
