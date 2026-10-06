"""Tests for the Elixir extractor fallback parser.

Covers the regression fixed in this pass: heredoc-close detection used
str.startswith on three-quote strings, which terminated the heredoc on
any inner body line that happened to begin with three quotes (e.g. a
line like three-quote-followed-by-foo). The fix requires an EXACT
three-quote close (after stripping).

Stdlib only (unittest, tempfile, importlib, pathlib, io, sys).
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DISPATCHER_PATH = REPO_ROOT / "crux" / "scripts" / "extract-code-docs.py"
ELIXIR_PATH = REPO_ROOT / "crux" / "scripts" / "extractors" / "elixir.py"


def _load_dispatcher():
    spec = importlib.util.spec_from_file_location("extract_code_docs_dispatcher", DISPATCHER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["extract_code_docs_dispatcher"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_elixir():
    # Dispatcher must be registered before the extractor is loaded; the
    # extractor reaches into sys.modules['extract_code_docs_dispatcher']
    # for SourceUnit / DocPage.
    _load_dispatcher()
    spec = importlib.util.spec_from_file_location("crux_extractor_elixir", ELIXIR_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


elixir = _load_elixir()


def _module_block(source: str) -> dict:
    """Parse source via the regex fallback and return the first module block."""
    blocks = elixir._parse_elixir_modules(source)
    if not blocks:
        raise AssertionError("no module blocks parsed")
    return blocks[0]


class HeredocCloseTests(unittest.TestCase):
    def test_inner_line_starting_with_triple_quote_does_not_close_moduledoc(self):
        """An inner body line of `\"\"\"foo` (not bare `\"\"\"`) MUST NOT
        terminate the moduledoc heredoc.

        The pre-fix parser called this body line a close marker and
        truncated the moduledoc to just the first line ("intro line").
        """
        src = (
            "\n".join(
                [
                    "defmodule Sample do",
                    '  @moduledoc """',
                    "  intro line",
                    '  """foo',  # NOT a heredoc terminator — has trailing characters
                    "  outro line",
                    '  """',
                    "  def hello, do: :world",
                    "end",
                ]
            )
            + "\n"
        )
        block = _module_block(src)
        moduledoc = block["moduledoc"]
        self.assertIn("intro line", moduledoc)
        self.assertIn('"""foo', moduledoc)
        self.assertIn("outro line", moduledoc)

    def test_bare_triple_quote_closes_moduledoc(self):
        """A bare `\"\"\"` line (whitespace allowed) closes the heredoc."""
        src = (
            "\n".join(
                [
                    "defmodule Sample do",
                    '  @moduledoc """',
                    "  hello",
                    '  """',
                    "  def x, do: :ok",
                    "end",
                ]
            )
            + "\n"
        )
        block = _module_block(src)
        self.assertEqual(block["moduledoc"], "hello")

    def test_inner_triple_quote_with_trailing_chars_in_doc(self):
        """Same fix applies to @doc heredocs."""
        src = (
            "\n".join(
                [
                    "defmodule Sample do",
                    '  @moduledoc "short"',
                    '  @doc """',
                    "  example:",
                    '  """quoted',  # NOT a close — content
                    "  end_of_example",
                    '  """',
                    "  def x, do: :ok",
                    "end",
                ]
            )
            + "\n"
        )
        block = _module_block(src)
        self.assertEqual(len(block["functions"]), 1)
        fn = block["functions"][0]
        self.assertIn("example:", fn["doc"])
        self.assertIn('"""quoted', fn["doc"])
        self.assertIn("end_of_example", fn["doc"])

    def test_unterminated_heredoc_warns_to_stderr(self):
        """When the heredoc never closes we capture whatever we collected
        and emit a clear stderr warning so the user can fix their source."""
        src = (
            "\n".join(
                [
                    "defmodule Sample do",
                    '  @moduledoc """',
                    "  this never closes",
                    "  def x, do: :ok",
                    "end",
                ]
            )
            + "\n"
        )
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            block = _module_block(src)
        self.assertIn("unterminated", buf.getvalue().lower())
        # Whatever we DID collect should still surface (graceful degradation).
        self.assertIn("this never closes", block["moduledoc"])


if __name__ == "__main__":
    unittest.main()
