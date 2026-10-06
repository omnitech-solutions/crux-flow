#!/usr/bin/env python3
"""Every code-doc dispatcher command mention uses `uv run --no-config`.

Per ADR-0131 clause 16 (rule:code-doc-callers-invoke-through-uv), every
skill or gate that invokes `extract-code-docs.py`, and every command a
skill prints for the reader to run it, reads `uv run --no-config`. This
scans the distributed surfaces — `crux/skills/**/SKILL.md`,
`crux/templates/`, `crux/agents/` — for every mention of the dispatcher
used AS A COMMAND (inside a fenced code block, an inline code span, or a
Markdown table cell written as inline code) and fails on one that is not
immediately preceded, in the same code span, by the literal tokens
`uv run --no-config`.

Every line inside a fenced code block that names the dispatcher is treated
as a command, flags or not — a fence is where a reader copies a line to run
it. An inline code span (a backticked mention in prose or a table cell) is
treated as a command only when it carries a CLI flag (`--dry-run`,
`--config`, ...) or the words `uv run` — a bare filename reference (e.g. a
"Regenerator" column naming `scripts/extract-code-docs.py` with no flags
and no `uv run`) is not a command and is not scanned.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

SCAN_ROOTS = (
    REPO_ROOT / "crux" / "skills",
    REPO_ROOT / "crux" / "templates",
    REPO_ROOT / "crux" / "agents",
)

DISPATCHER_TOKEN = "extract-code-docs.py"
REQUIRED_PREFIX = "uv run --no-config"

_FLAG_RE = re.compile(r"--[a-zA-Z][\w-]*")
_COMPLIANT_RE = re.compile(
    r"uv run --no-config\s+.{0,120}?extract-code-docs\.py",
)
_FENCE_RE = re.compile(r"^\s*```")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")


def _iter_code_spans(text: str):
    """Yield (line_no, span_text, in_fence) for every code span/fenced line in text.

    A fenced block's line is its own span (the whole line minus the fence
    marker line itself), tagged `in_fence=True`. An unfenced line's inline
    `code` spans are each yielded separately with `in_fence=False`, so a
    table cell's backticked command is checked on its own.
    """
    in_fence = False
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        if _FENCE_RE.match(raw_line):
            in_fence = not in_fence
            continue
        if in_fence:
            yield lineno, raw_line, True
            continue
        for match in _INLINE_CODE_RE.finditer(raw_line):
            yield lineno, match.group(1), False


def find_violations(text: str, label: str) -> list[str]:
    """Return one message per dispatcher command mention lacking the prefix."""
    violations: list[str] = []
    for lineno, span, in_fence in _iter_code_spans(text):
        if DISPATCHER_TOKEN not in span:
            continue
        is_command = in_fence or "uv run" in span or _FLAG_RE.search(span) is not None
        if not is_command:
            continue
        if _COMPLIANT_RE.search(span):
            continue
        violations.append(f"{label}:{lineno}: {span.strip()}")
    return violations


def _iter_scanned_files():
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix == ".md":
                yield path


class CodeDocCallerPrefixTests(unittest.TestCase):
    """Scans distributed surfaces for bare (non-`uv run --no-config`) calls."""

    def test_positive_control_bare_invocation_is_flagged(self) -> None:
        """A synthetic bare invocation must be caught (proves the scan fires)."""
        text = "Run:\n\n```bash\nextract-code-docs.py --dry-run\n```\n"
        violations = find_violations(text, "synthetic")
        self.assertEqual(
            len(violations), 1,
            f"expected exactly one violation in the synthetic fixture, got {violations!r}",
        )

    def test_positive_control_compliant_invocation_is_not_flagged(self) -> None:
        """The prescribed form produces zero violations (rules out a vacuous scan)."""
        text = (
            'Run:\n\n```bash\nuv run --no-config '
            '"${CRUX_PLUGIN_ROOT}/scripts/extract-code-docs.py" --dry-run\n```\n'
        )
        violations = find_violations(text, "synthetic")
        self.assertEqual(violations, [])

    def test_positive_control_fenced_bare_invocation_is_flagged(self) -> None:
        """A fenced bare invocation, no flags and no `uv run`, must still be caught:
        a fence is where a reader copies a line to run it."""
        text = (
            'Run:\n\n```bash\n'
            '"${CRUX_PLUGIN_ROOT}/scripts/extract-code-docs.py"\n'
            '```\n'
        )
        violations = find_violations(text, "synthetic")
        self.assertEqual(
            len(violations), 1,
            f"expected exactly one violation in the synthetic fixture, got {violations!r}",
        )

    def test_bare_filename_reference_is_not_a_command(self) -> None:
        """A roster row naming the script (no flags, no `uv run`) is not scanned."""
        text = "| `docs/code/` | `scripts/extract-code-docs.py` | project |\n"
        violations = find_violations(text, "synthetic")
        self.assertEqual(violations, [])

    def test_every_dispatcher_command_mention_uses_uv_run_no_config(self) -> None:
        all_violations: list[str] = []
        for path in _iter_scanned_files():
            text = path.read_text(encoding="utf-8")
            all_violations.extend(
                find_violations(text, str(path.relative_to(REPO_ROOT)))
            )
        self.assertEqual(
            all_violations, [],
            "dispatcher command mention(s) missing the `uv run --no-config` "
            "prefix (ADR-0131 clause 16):\n" + "\n".join(all_violations),
        )

    def test_scan_is_not_vacuous(self) -> None:
        """The scan must reach at least the four named callers (non-vacuity)."""
        named_callers = {
            REPO_ROOT / "crux" / "skills" / "extract-code-docs" / "SKILL.md",
            REPO_ROOT / "crux" / "skills" / "verify-code-docs" / "SKILL.md",
            REPO_ROOT / "crux" / "skills" / "audit-docs" / "SKILL.md",
            REPO_ROOT / "crux" / "skills" / "check-drift" / "SKILL.md",
        }
        scanned = set(_iter_scanned_files())
        missing = named_callers - scanned
        self.assertEqual(
            missing, set(),
            f"named caller(s) not reached by the scan: {missing!r}",
        )
        # Each named caller must actually mention the dispatcher as a command
        # somewhere, or the scan would pass by finding nothing to check.
        mention_counts = {}
        for path in named_callers:
            text = path.read_text(encoding="utf-8")
            count = 0
            for lineno, span, in_fence in _iter_code_spans(text):
                if DISPATCHER_TOKEN not in span:
                    continue
                if in_fence or "uv run" in span or _FLAG_RE.search(span) is not None:
                    count += 1
            mention_counts[path] = count
        for path, count in mention_counts.items():
            self.assertGreater(
                count, 0,
                f"{path} carries no dispatcher command mention — scan would be vacuous",
            )


_SKILL_KEY_RE = re.compile(r"The key's `([a-z_]+)`")


class DocumentedSelectionKeyTests(unittest.TestCase):
    """PB-0121 fix round 1, finding M4: the selection key the
    extract-code-docs skill documents for the Python extractor is the key
    the extractor reads. A manifest written from the skill's text is run
    through the real dispatcher and must select the intended source."""

    SKILL = REPO_ROOT / "crux" / "skills" / "extract-code-docs" / "SKILL.md"
    SCRIPT = REPO_ROOT / "crux" / "scripts" / "extract-code-docs.py"

    def _documented_key(self) -> str:
        text = self.SKILL.read_text(encoding="utf-8")
        section = text.split("## The Python extractor", 1)[1]
        keys = _SKILL_KEY_RE.findall(section)
        self.assertEqual(len(keys), 1, keys)
        return keys[0]

    def _run_with_key(self, key: str, expect_exit: int = 0) -> Path | None:
        import subprocess
        import sys
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs").mkdir()
        (root / "a.py").write_text('"""A."""\n', encoding="utf-8")
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\ncode:\n  extractors:\n    python:\n'
            f'      extractor: python\n      {key}: ["a.py"]\n',
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), "--config", str(root / "docs" / "manifest.yml")],
            cwd=str(root), capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, expect_exit, proc.stderr)
        if expect_exit:
            self.assertIn(f"code.extractors.python.{key}", proc.stderr)
        return root / "docs" / "code" / "python" / "a.py.md"

    def test_a_manifest_written_from_the_skill_selects_the_source(self) -> None:
        page = self._run_with_key(self._documented_key())
        self.assertTrue(page.is_file(), "the documented key selected nothing")

    def test_positive_control_an_unread_key_selects_nothing(self) -> None:
        # The Python extractor refuses a key it does not read, so a wrong
        # selection key is a named refusal rather than an empty selection.
        page = self._run_with_key("not_a_selection_key", expect_exit=1)
        self.assertFalse(page.is_file())

    def test_elixir_fallback_and_template_use_the_same_key(self) -> None:
        template = (REPO_ROOT / "crux" / "templates" / "manifest.yml.tmpl").read_text(encoding="utf-8")
        self.assertIn("#   glob:", template)
        for name in ("python.py", "elixir.py", "fallback.py"):
            src = (REPO_ROOT / "crux" / "scripts" / "extractors" / name).read_text(encoding="utf-8")
            self.assertIn('config.get("glob")', src, name)
        self.assertEqual(self._documented_key(), "glob")

if __name__ == "__main__":
    unittest.main()
