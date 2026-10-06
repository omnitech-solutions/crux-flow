"""Tests for crux/scripts/extractors/_py_render.py (U15a, ADR-0131 clauses 9-10).

Stdlib only (unittest, importlib, random, string, urllib.parse). Loaded by
path, matching how the composing Python extractor will load it (ADR-0131 /
PB-0121 dev-1 module-loading note).
"""

from __future__ import annotations

import importlib.util
import random
import string
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
RENDER_PATH = REPO_ROOT / "crux" / "scripts" / "extractors" / "_py_render.py"


def _load_render():
    spec = importlib.util.spec_from_file_location("crux_py_render_t", RENDER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["crux_py_render_t"] = mod
    spec.loader.exec_module(mod)
    return mod


render = _load_render()


class FenceTests(unittest.TestCase):
    def test_backtick_run_lengths_0_to_5(self):
        for run in range(0, 6):
            text = "x" * 2 + "`" * run + "y" * 2
            expected_n = max(3, run + 1)
            out = render.fence(text, "text")
            self.assertTrue(out.startswith("`" * expected_n))
            self.assertFalse(out.startswith("`" * (expected_n + 1)))
            lines = out.split("\n")
            self.assertEqual(lines[0], "`" * expected_n + "text")
            self.assertEqual(lines[-1], "`" * expected_n)
            self.assertEqual("\n".join(lines[1:-1]), text)

    def test_docstring_containing_backtick_and_tilde_fences_and_four_run(self):
        text = "```\ncode\n```\n~~~\nmore\n~~~\n````fourbackticks"
        out = render.fence(text, "text")
        n = max(3, 4 + 1)
        self.assertTrue(out.startswith("`" * n + "text"))
        self.assertTrue(out.endswith("`" * n))

    def test_info_string_placement(self):
        out = render.fence("abc", "python")
        self.assertEqual(out.splitlines()[0], "```python")


class DocstringNormalizationTests(unittest.TestCase):
    def test_crlf_normalized(self):
        self.assertEqual(render.normalize_docstring("a\r\nb\r\n"), "a\nb")

    def test_cleandoc_dedent(self):
        text = "    line one\n    line two\n"
        self.assertEqual(render.normalize_docstring(text), "line one\nline two")

    def test_trailing_whitespace_stripped_per_line_and_whole(self):
        text = "line one   \nline two\t\n   \n"
        out = render.normalize_docstring(text)
        for line in out.splitlines():
            self.assertEqual(line, line.rstrip())
        self.assertEqual(out, out.rstrip())

    def test_render_docstring_empty(self):
        self.assertEqual(render.render_docstring("   \n  "), "_Empty docstring._")
        self.assertEqual(render.render_docstring(""), "_Empty docstring._")

    def test_render_docstring_none_is_undocumented(self):
        self.assertEqual(render.render_docstring(None), "_Undocumented._")

    def test_render_docstring_normal_renders_fenced(self):
        out = render.render_docstring("hello world")
        self.assertTrue(out.startswith("```text\n"))
        self.assertIn("hello world", out)


class VisibleEscapesTests(unittest.TestCase):
    def test_newline_carriage_return_tab(self):
        self.assertEqual(render.visible_escapes("\n"), "\\n")
        self.assertEqual(render.visible_escapes("\r"), "\\r")
        self.assertEqual(render.visible_escapes("\t"), "\\t")

    def test_esc_and_del(self):
        self.assertEqual(render.visible_escapes("\x1b"), "\\x1b")
        self.assertEqual(render.visible_escapes("\x7f"), "\\x7f")

    def test_c1_range(self):
        self.assertEqual(render.visible_escapes("\x80"), "\\x80")
        self.assertEqual(render.visible_escapes("\x9f"), "\\x9f")

    def test_line_and_paragraph_separator(self):
        # Six characters each: backslash, u, 2, 0, 2, 8/9 - the literal
        # escape-sequence text, never the raw invisible byte.
        self.assertEqual(render.visible_escapes(" "), "\\u2028")
        self.assertEqual(render.visible_escapes(" "), "\\u2029")

    def test_ordinary_text_unchanged(self):
        self.assertEqual(render.visible_escapes("hello WORLD 123"), "hello WORLD 123")


class MdEscapeInlineTests(unittest.TestCase):
    METACHARS = "\\`*_[]<>#!&|~"

    def test_every_listed_metacharacter_escaped(self):
        for ch in self.METACHARS:
            out = render.md_escape_inline(ch)
            self.assertEqual(out, "\\" + ch)

    def test_bracket_paren_sequence_broken(self):
        # "]" is escaped so the link-closing token can no longer parse as
        # Markdown, even though "(" (not a listed metacharacter) is untouched
        # and the literal substring "](" still appears in the escaped text.
        out = render.md_escape_inline("text](more")
        self.assertIn("\\](", out)

    def test_html_comment_close_broken(self):
        # ">" is escaped so "-->" can no longer close an HTML comment, even
        # though "-" (not a listed metacharacter) is untouched.
        out = render.md_escape_inline("end-->done")
        self.assertIn("--\\>", out)

    def test_r4_then_r3_order_no_doubled_backslash(self):
        # A literal backslash from R3's "\n" marker must not be re-escaped by
        # R4; R4 runs first on the raw newline character, then R3 escapes it.
        out = render.md_escape_inline("a\nb")
        self.assertEqual(out, "a\\nb")

    def test_authoring_scope_example(self):
        self.assertEqual(
            render.md_escape_inline("crux/scripts/authoring_scope.py"),
            "crux/scripts/authoring\\_scope.py",
        )


class RendererVersionTests(unittest.TestCase):
    def test_renderer_version_is_exposed(self):
        self.assertEqual(render.RENDERER_VERSION, "1")


class CodeSpanTests(unittest.TestCase):
    def test_empty_string_renders_as_the_named_fallback(self):
        # R5a (dev-lead decision): two adjacent backticks are not a code
        # span in CommonMark, so an empty target-derived string renders as
        # this italic fallback line instead of an empty code span.
        self.assertEqual(render.code_span(""), "_(empty string)_")


    def test_embedded_backticks(self):
        out = render.code_span("a`b``c")
        n = 2 + 1
        self.assertTrue(out.startswith("`" * n))
        self.assertTrue(out.endswith("`" * n))
        self.assertNotIn("`" * (n + 1), out)

    def test_leading_backtick_padded(self):
        out = render.code_span("`x")
        self.assertTrue(out.startswith("`` "))

    def test_trailing_backtick_padded(self):
        out = render.code_span("x`")
        self.assertTrue(out.endswith(" ``"))

    def test_leading_space_padded(self):
        out = render.code_span(" x")
        self.assertTrue(out.startswith("` "))

    def test_trailing_space_padded(self):
        out = render.code_span("x ")
        self.assertTrue(out.endswith(" `"))

    def test_no_padding_for_plain_text(self):
        out = render.code_span("plain")
        self.assertEqual(out, "`plain`")

    def test_control_char_escaped_before_measuring_fence(self):
        out = render.code_span("a\nb")
        self.assertNotIn("\n", out)
        self.assertIn("\\n", out)


class LinkDestinationTests(unittest.TestCase):
    def test_space_encoded(self):
        self.assertIn("%20", render.link_destination("a b.md"))

    def test_bracket_paren_encoded(self):
        out = render.link_destination("a](b.md")
        self.assertIn("%5D", out)
        self.assertIn("%28", out)

    def test_hash_encoded(self):
        self.assertIn("%23", render.link_destination("a#b.md"))

    def test_newline_encoded(self):
        self.assertIn("%0A", render.link_destination("a\nb.md"))

    def test_non_ascii_encoded(self):
        out = render.link_destination("café.md")
        self.assertNotIn("é", out)

    def test_slash_left_unencoded(self):
        out = render.link_destination("a/b/c.md")
        self.assertIn("a/b/c.md", out)


class PropertyFenceRoundTripTests(unittest.TestCase):
    ALPHABET = "`[]()<!-->#\n\r\x1b abcXYZ"

    def test_fence_round_trips_1000_hostile_strings(self):
        rng = random.Random(20260925)
        for _ in range(1000):
            length = rng.randint(0, 40)
            text = "".join(rng.choice(self.ALPHABET) for _ in range(length))
            out = render.fence(text, "text")
            lines = out.split("\n")
            n = 0
            for ch in lines[0]:
                if ch == "`":
                    n += 1
                else:
                    break
            body = "\n".join(lines[1:-1])
            self.assertEqual(body, text)
            self.assertEqual(lines[-1], "`" * n)

    def test_md_escape_inline_and_code_span_never_emit_raw_control_or_linebreak(self):
        rng = random.Random(20260925)
        control_and_meta = "`[]()<!-->#\n\r\x1b\x7f" + string.ascii_letters
        for _ in range(1000):
            length = rng.randint(0, 40)
            text = "".join(rng.choice(control_and_meta) for _ in range(length))
            for fn in (render.md_escape_inline, render.code_span):
                out = fn(text)
                for ch in out:
                    self.assertFalse(
                        ch == "\n" or ch == "\r" or (ord(ch) < 0x20) or ord(ch) == 0x7F,
                        msg=f"{fn.__name__} emitted raw control/line-break char {ch!r} for input {text!r}",
                    )


if __name__ == "__main__":
    unittest.main()
