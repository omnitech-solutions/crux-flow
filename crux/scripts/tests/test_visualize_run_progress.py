"""Tests for visualize-run-progress.py (ADR-0025).

Covers the progress model (counts/percentage), the book-joined `module_tag` with
its hash-mismatch `—` fallback, byte-stability + no-timestamps of the Markdown
artifact, control-byte sanitization (ANSI-injection defense), Markdown cell
escaping, color gating, and the CLI (explicit path, PB-id resolution, --markdown
artifact write, not-found + malformed-input exit codes).
"""

import importlib.util
import io
import os
import tempfile
import unittest
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path

# Guard: skip entire module when PyYAML is absent.  The skip also prevents
# validate-promptbook's PyYAML re-exec lane from exec-replacing the unittest
# process — keep any main() invocation behind this guard.
try:
    import yaml  # noqa: F401
except ImportError as exc:
    raise unittest.SkipTest(f"PyYAML unavailable (uv lane required): {exc}")

_SCRIPTS = Path(__file__).resolve().parent.parent


def _load(mod_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(mod_name, _SCRIPTS / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


viz = _load("_viz_t", "visualize-run-progress.py")

_BOOK = '''format_version: "1"
id: PB-9001
title: "Test book"
status: active
created_at: 2026-06-01
total_prompts: 2
current_run: RUN-001
current_prompt: 2
forked_from: null
tags: [test]
goal: |
  test goal
strategy: |
  test strategy
prompts:
  - n: 1
    title: "First prompt: do a thing"
    purpose: |
      p1
    prompt: |
      do
    expected_output: |
      out
    module_tag: adr-1
  - n: 2
    title: "Second prompt"
    purpose: |
      p2
    prompt: |
      do2
    expected_output: |
      out2
    module_tag: dev-1
'''

_BOOK_HASH = viz.compute_book_hash(viz.load_yaml(_BOOK))


def _run_text(book_hash: str, p1_state: str = "done", p2_state: str = "running") -> str:
    return f'''format_version: "1"
run_id: RUN-001
book_id: PB-9001
book_content_hash: "{book_hash}"
started_at: "2026-06-01T00:00:00Z"
completed_at: null
status: in_progress
current_prompt: 2
prompts:
  - n: 1
    title: "First prompt: do a thing"
    state: {p1_state}
    started: "2026-06-01T00:00:00Z"
    completed: "2026-06-01T00:01:00Z"
    result: "ok"
    artifacts: []
  - n: 2
    title: "Second prompt"
    state: {p2_state}
    started: "2026-06-01T00:01:00Z"
    completed: null
    result: ""
    artifacts: []
'''


def _model(rows, status="in_progress", tag_joined=True):
    counts = Counter(r["state"] for r in rows)
    total = len(rows)
    terminal = sum(counts.get(s, 0) for s in viz.TERMINAL_STATES)
    return {
        "run_id": "RUN-001", "book_id": "PB-9001", "status": status, "rows": rows,
        "counts": counts, "total": total, "terminal": terminal,
        "done": counts.get("done", 0), "pct": round(100 * terminal / total) if total else 0,
        "tag_joined": tag_joined,
    }


class ProgressModelTests(unittest.TestCase):
    def setUp(self):
        _td = tempfile.TemporaryDirectory()
        self.addCleanup(_td.cleanup)
        self.tmp = Path(_td.name)
        active = self.tmp / "docs/promptbooks/active"
        self.runs = self.tmp / "docs/promptbooks/runs/PB-9001-test"
        active.mkdir(parents=True)
        self.runs.mkdir(parents=True)
        # ADR-0059 discovery recognizes a tree by its manifest, not by the presence
        # of a concern directory. A fixture without one is not a crux tree.
        (self.tmp / "docs" / "manifest.yml").write_text(
            'schema_version: "4"\nconcerns_enabled:\n  - promptbooks\n', encoding="utf-8"
        )
        self.book = active / "PB-9001-test.yaml"
        self.book.write_text(_BOOK, encoding="utf-8")
        self.run = self.runs / "run-RUN-001.yaml"
        self.run.write_text(_run_text(_BOOK_HASH), encoding="utf-8")

    def test_counts_and_percentage(self):
        m = viz.build_model(self.run, self.book)
        self.assertEqual(m["total"], 2)
        self.assertEqual(m["done"], 1)
        self.assertEqual(m["counts"]["running"], 1)
        self.assertEqual(m["terminal"], 1)
        self.assertEqual(m["pct"], 50)  # 1 terminal of 2

    def test_module_tag_joined_when_hash_matches(self):
        m = viz.build_model(self.run, self.book)
        self.assertTrue(m["tag_joined"])
        self.assertEqual([r["module_tag"] for r in m["rows"]], ["adr-1", "dev-1"])

    def test_module_tag_dash_on_hash_mismatch(self):
        self.run.write_text(_run_text("sha256:" + "0" * 64), encoding="utf-8")
        m = viz.build_model(self.run, self.book)
        self.assertFalse(m["tag_joined"])
        self.assertEqual({r["module_tag"] for r in m["rows"]}, {"—"})

    def test_build_model_markdown_byte_stable(self):
        m = viz.build_model(self.run, self.book)
        self.assertEqual(viz.render_markdown(m), viz.render_markdown(m))


class RendererTests(unittest.TestCase):
    def _rows(self):
        return [
            {"n": 1, "title": "alpha", "module_tag": "adr-1", "state": "done"},
            {"n": 2, "title": "beta", "module_tag": "dev-1", "state": "running"},
        ]

    def test_markdown_no_timestamps(self):
        md = viz.render_markdown(_model(self._rows()))
        self.assertNotRegex(md, r"\d{4}-\d{2}-\d{2}", "artifact body must carry no date")
        self.assertNotRegex(md, r"\d{2}:\d{2}", "artifact body must carry no time")

    def test_markdown_names_the_surviving_status_owner(self):
        md = viz.render_markdown(_model(self._rows()))
        self.assertIn("`run-promptbook` status", md)
        self.assertNotIn("`visualize-run-progress` skill", md)
        self.assertNotIn("[[adrs/", md, "consumer artifacts cannot link to dev-repo ADRs")

    def test_markdown_escapes_pipe(self):
        rows = [{"n": 1, "title": "has | a pipe", "module_tag": "—", "state": "done"}]
        md = viz.render_markdown(_model(rows, tag_joined=False))
        self.assertIn(r"\|", md)
        self.assertNotIn("has | a pipe", md)

    def test_markdown_collapses_newline_in_cell(self):
        rows = [{"n": 1, "title": "line1\nline2", "module_tag": "—", "state": "done"}]
        md = viz.render_markdown(_model(rows, tag_joined=False))
        self.assertIn("line1 line2", md)

    def test_md_cell_sanitizes_and_escapes(self):
        out = viz._md_cell("a|b\nc\x1bd")
        self.assertIn(r"\|", out)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\n", out)

    def test_sanitize_strips_control_bytes(self):
        clean = viz._sanitize("title\x1b[31mRED\x1b[0m\x07bell")
        self.assertNotIn("\x1b", clean)
        self.assertNotIn("\x07", clean)
        self.assertIn("RED", clean)

    def test_terminal_color_gating(self):
        m = _model(self._rows())
        self.assertIn("\033[", viz.render_terminal(m, color=True))
        self.assertNotIn("\033[", viz.render_terminal(m, color=False))

    def test_terminal_strips_injected_escape(self):
        rows = [{"n": 1, "title": "evil\x1b[2Jclear", "module_tag": "—", "state": "done"}]
        out = viz.render_terminal(_model(rows, tag_joined=False), color=False)
        self.assertNotIn("\x1b", out)
        self.assertIn("evil", out)


class CliTests(unittest.TestCase):
    def setUp(self):
        # The ADR-0032 resolve-once cache is per-process; these tests run
        # main() in-process against a fresh temp repo each, so reset it
        # (same pattern as crux_env._reset_cache()).
        viz._DOCS_ROOT_CACHE = None
        self.addCleanup(lambda: setattr(viz, "_DOCS_ROOT_CACHE", None))
        _td = tempfile.TemporaryDirectory()
        self.addCleanup(_td.cleanup)
        self.tmp = Path(_td.name)
        active = self.tmp / "docs/promptbooks/active"
        runs = self.tmp / "docs/promptbooks/runs/PB-9001-test"
        active.mkdir(parents=True)
        runs.mkdir(parents=True)
        (self.tmp / "docs" / "manifest.yml").write_text(
            'schema_version: "4"\nconcerns_enabled:\n  - promptbooks\n', encoding="utf-8"
        )
        (active / "PB-9001-test.yaml").write_text(_BOOK, encoding="utf-8")
        self.run = runs / "run-RUN-001.yaml"
        self.run.write_text(_run_text(_BOOK_HASH), encoding="utf-8")
        cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, cwd)

    def _main(self, args):
        """Run viz.main() with stdout captured so progress output doesn't
        pollute the test-runner output stream."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            return viz.main(args)

    def test_cli_explicit_path_terminal(self):
        self.assertEqual(self._main([str(self.run), "--no-color"]), 0)

    def test_cli_pb_id_resolution(self):
        self.assertEqual(self._main(["PB-9001", "--no-color"]), 0)

    def test_terminal_render_changes_no_files(self):
        before = {p.relative_to(self.tmp): p.read_bytes() for p in self.tmp.rglob("*") if p.is_file()}
        self.assertEqual(self._main(["PB-9001", "--no-color"]), 0)
        after = {p.relative_to(self.tmp): p.read_bytes() for p in self.tmp.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_cli_markdown_writes_sibling_artifact(self):
        self.assertEqual(self._main(["PB-9001", "--markdown"]), 0)
        artifact = self.run.with_name("run-RUN-001-progress.md")
        self.assertTrue(artifact.exists())
        self.assertIn("Run progress", artifact.read_text(encoding="utf-8"))

    def test_cli_markdown_only_writes_artifact_so_skill_can_log_once(self):
        before = {p.relative_to(self.tmp): p.read_bytes() for p in self.tmp.rglob("*") if p.is_file()}
        self.assertEqual(self._main(["PB-9001", "--markdown"]), 0)
        after = {p.relative_to(self.tmp): p.read_bytes() for p in self.tmp.rglob("*") if p.is_file()}
        new = set(after) - set(before)
        self.assertEqual(new, {self.run.relative_to(self.tmp).with_name("run-RUN-001-progress.md")})
        self.assertEqual({path: after[path] for path in before}, before)

    def test_pb_resolution_falls_back_to_highest_when_current_run_null(self):
        book = self.tmp / "docs/promptbooks/active/PB-9001-test.yaml"
        book.write_text(_BOOK.replace("current_run: RUN-001", "current_run: null"), encoding="utf-8")
        (self.tmp / "docs/promptbooks/runs/PB-9001-test/run-RUN-002.yaml").write_text(
            _run_text(_BOOK_HASH), encoding="utf-8")
        run_path, _ = viz.resolve("PB-9001", None)
        self.assertEqual(run_path.name, "run-RUN-002.yaml")

    def test_cli_malformed_yaml_unparseable_exits_1(self):
        bad = self.tmp / "broken.yaml"
        bad.write_text('format_version: "1"\nprompts: [unclosed\n', encoding="utf-8")
        self.assertEqual(self._main([str(bad), "--no-color"]), 1)

    def test_cli_not_found_exits_1(self):
        self.assertEqual(self._main(["PB-9999", "--no-color"]), 1)

    def test_cli_malformed_yaml_no_format_version_exits_1(self):
        bad = self.tmp / "bad.yaml"
        bad.write_text("run_id: RUN-001\nprompts: []\n", encoding="utf-8")
        self.assertEqual(self._main([str(bad), "--no-color"]), 1)


if __name__ == "__main__":
    unittest.main()
