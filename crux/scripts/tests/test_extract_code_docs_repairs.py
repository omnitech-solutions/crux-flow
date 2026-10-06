"""Regressions for ADR-0131 clauses 13-15 and the dispatcher<->extractor
contract C1-C8, developer A's slice (PB-0121 dev-1): the dispatcher repairs
D1-D6, the owner field / per-key provenance, WritePlan collisions, --lang
owner scoping, and byte-level --dry-run drift.

Stdlib only. Every disposable tree is built under tempfile; the only
fixture read from the repository is the committed golden-byte tree under
crux/scripts/tests/fixtures/code_docs_legacy/, which ships inside crux/ and
needs no dev-surface guard.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # crux repo
SCRIPT_PATH = REPO_ROOT / "crux" / "scripts" / "extract-code-docs.py"
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "code_docs_legacy"


def _load_dispatcher():
    spec = importlib.util.spec_from_file_location("extract_code_docs_dispatcher", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["extract_code_docs_dispatcher"] = mod
    spec.loader.exec_module(mod)
    return mod


dispatcher = _load_dispatcher()


def _run(cwd: Path, *argv: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *argv],
        cwd=str(cwd), capture_output=True, text=True,
    )


def _fingerprint(root: Path) -> dict[str, str]:
    return {
        str(f.relative_to(root)): dispatcher.sha256_text(f.read_text(encoding="utf-8"))
        for f in sorted(root.rglob("*")) if f.is_file()
    }


class GoldenByteRegressionTests(unittest.TestCase):
    """ADR-0131 clause 14 condition (b)/(c): pages and full-run index bytes
    are byte-identical to the pre-change dispatcher; `owner` is the only
    field an Elixir or fallback row gains.
    """

    def test_full_run_over_the_legacy_fixture_changes_only_the_owner_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "tree"
            shutil.copytree(FIXTURE_ROOT, work)
            expected_code = work / "docs" / "code"
            expected_index = (expected_code / "index.md").read_bytes()
            expected_pages = {
                p: (expected_code / p).read_bytes()
                for p in (
                    "elixir/App/Foo.md",
                    "elixir/App/Nested/Bar.md",
                    "fallback/scripts/a.py.md",
                    "fallback/scripts/b.sh.md",
                )
            }
            old_manifest = json.loads((expected_code / "_meta" / "manifest.json").read_text())
            shutil.rmtree(expected_code)

            r = _run(work, "--config", str(work / "docs" / "manifest.yml"))
            self.assertEqual(r.returncode, 0, r.stderr)

            self.assertEqual((expected_code / "index.md").read_bytes(), expected_index)
            for rel, body in expected_pages.items():
                self.assertEqual((expected_code / rel).read_bytes(), body, rel)

            new_manifest = json.loads((expected_code / "_meta" / "manifest.json").read_text())
            old_by_doc = {row["doc_path"]: row for row in old_manifest["pages"]}
            new_by_doc = {row["doc_path"]: row for row in new_manifest["pages"]}
            self.assertEqual(set(old_by_doc), set(new_by_doc))
            for doc_path, old_row in old_by_doc.items():
                new_row = new_by_doc[doc_path]
                self.assertNotIn("owner", old_row)
                self.assertIn("owner", new_row)
                without_owner = {k: v for k, v in new_row.items() if k != "owner"}
                self.assertEqual(old_row, without_owner, doc_path)
            self.assertEqual(new_by_doc["elixir/App/Foo.md"]["owner"], "elixir")
            self.assertEqual(new_by_doc["fallback/scripts/a.py.md"]["owner"], "fallback_py")
            self.assertEqual(new_by_doc["fallback/scripts/b.sh.md"]["owner"], "fallback_sh")
            self.assertNotIn("extractors", new_manifest, "no API-2 key is configured")


class RefusalLaneTests(unittest.TestCase):
    """Contract C1: every content refusal reports through one function."""

    def test_write_mode_refusal_reports_on_stderr_only_and_exits_1(self):
        # That a refusal also writes nothing is shown through the real CLI by
        # DamagedMetadataManifestTests, which fingerprints the tree.
        refusal = dispatcher.ExtractionRefusal("some/path.ex", "is not a regular file")
        import contextlib
        import io

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dispatcher._report_refusals([refusal], dry_run=False)
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("some/path.ex", err.getvalue())
        self.assertIn("is not a regular file", err.getvalue())

    def test_dry_run_refusal_is_one_json_payload_on_stdout(self):
        refusal = dispatcher.ExtractionRefusal("some/path.ex", "is not a regular file")
        import contextlib
        import io

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dispatcher._report_refusals([refusal], dry_run=True)
        self.assertEqual(code, 1)
        self.assertEqual(err.getvalue(), "")
        payload = json.loads(out.getvalue())
        self.assertEqual(
            payload,
            {"validation_errors": [{"path": "some/path.ex", "cause": "is not a regular file"}]},
        )
        self.assertNotIn("drift", payload)

    def _tree_with_bad_source(self, use_missing: bool) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs").mkdir()
        (root / "src").mkdir()
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n    any:\n      extractor: fallback\n'
            '      glob: "src/*.py"\n',
            encoding="utf-8",
        )
        if not use_missing:
            (root / "src" / "a.py").write_text('# header\nprint(1)\n', encoding="utf-8")
        return root

    def test_source_containment_refusal_under_dry_run(self):
        """A symlink to a file outside the repo refuses via the JSON lane,
        including under --dry-run (positive control: an in-repo file runs
        clean under the same invocation shape)."""
        root = self._tree_with_bad_source(use_missing=False)
        outside_dir = tempfile.TemporaryDirectory()
        self.addCleanup(outside_dir.cleanup)
        outside_file = Path(outside_dir.name) / "secret.py"
        outside_file.write_text("# secret\n", encoding="utf-8")
        try:
            (root / "src" / "b.py").symlink_to(outside_file)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")

        r = _run(root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 1, r.stdout)
        payload = json.loads(r.stdout)
        self.assertIn("validation_errors", payload)
        self.assertNotIn("drift", payload)
        causes = " ".join(e["cause"] for e in payload["validation_errors"])
        self.assertIn("outside the repository root", causes)
        self.assertFalse((root / "docs" / "code").exists(), "dry-run must write nothing")

    def test_positive_control_in_repo_source_runs_clean(self):
        root = self._tree_with_bad_source(use_missing=False)
        r = _run(root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"))
        self.assertIn(r.returncode, (0, 1))
        payload = json.loads(r.stdout)
        self.assertNotIn("validation_errors", payload)


class D6PerExtractorContainmentTests(unittest.TestCase):
    """ADR-0131 clause 14 D6 / contract C2, exercised per extractor via the
    real CLI: elixir's regex path, fallback, and a stub API-2 extractor.
    Each refuses a source symlinked outside the repo, before any write, in
    both lanes; a TOCTOU source (regular file that becomes a directory
    between resolve_source and read_source_bytes) refuses the same way,
    driven in-process; an in-repo symlink yields page/row bytes identical
    to the pre-change dispatcher except `owner`.
    """

    def _outside_file(self) -> Path:
        outside_dir = tempfile.TemporaryDirectory()
        self.addCleanup(outside_dir.cleanup)
        outside_file = Path(outside_dir.name) / "secret.txt"
        outside_file.write_text("# secret\n", encoding="utf-8")
        return outside_file

    def _elixir_tree(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs").mkdir()
        (root / "lib").mkdir()
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n    elixir:\n      extractor: elixir\n'
            '      glob: "lib/*.ex"\n',
            encoding="utf-8",
        )
        (root / "lib" / "keep.ex").write_text(
            'defmodule Keep do\n  @moduledoc "kept"\nend\n', encoding="utf-8",
        )
        return root

    def test_elixir_regex_path_refuses_symlink_outside_repo_both_lanes(self):
        root = self._elixir_tree()
        outside = self._outside_file()
        try:
            (root / "lib" / "bad.ex").symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")

        for extra in ((), ("--dry-run",)):
            r = _run(root, *extra, "--config", str(root / "docs" / "manifest.yml"))
            self.assertEqual(r.returncode, 1, (extra, r.stdout, r.stderr))
            self.assertFalse((root / "docs" / "code").exists(), "no write before the refusal")
            if extra:
                payload = json.loads(r.stdout)
                causes = " ".join(e["cause"] for e in payload["validation_errors"])
            else:
                self.assertEqual(r.stdout, "")
                causes = r.stderr
            self.assertIn("outside the repository root", causes)

    def _fallback_tree(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs").mkdir()
        (root / "src").mkdir()
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n    any:\n      extractor: fallback\n'
            '      glob: "src/*.py"\n',
            encoding="utf-8",
        )
        (root / "src" / "keep.py").write_text("# kept\n", encoding="utf-8")
        return root

    def test_fallback_refuses_symlink_outside_repo_both_lanes(self):
        root = self._fallback_tree()
        outside = self._outside_file()
        try:
            (root / "src" / "bad.py").symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")

        for extra in ((), ("--dry-run",)):
            r = _run(root, *extra, "--config", str(root / "docs" / "manifest.yml"))
            self.assertEqual(r.returncode, 1, (extra, r.stdout, r.stderr))
            self.assertFalse((root / "docs" / "code").exists(), "no write before the refusal")
            if extra:
                payload = json.loads(r.stdout)
                causes = " ".join(e["cause"] for e in payload["validation_errors"])
            else:
                self.assertEqual(r.stdout, "")
                causes = r.stderr
            self.assertIn("outside the repository root", causes)

    def test_stub_api2_extractor_refuses_symlink_outside_repo_via_resolve_and_read(self):
        """A stub API-2 module that calls resolve_source/read_source_bytes
        itself (standing in for the Python extractor) refuses the same way,
        driven in-process through main() with load_extractor_module patched."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs").mkdir()
        (root / "src").mkdir()
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n    stub:\n      extractor: stub\n',
            encoding="utf-8",
        )
        outside = self._outside_file()
        bad_src = root / "src" / "bad.src"
        try:
            bad_src.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")

        mod = _load_dispatcher()

        def make_stub():
            m = types.ModuleType("stub_api2_toctou")
            m.EXTRACTOR_API = 2

            def discover(repo_root, config, ctx):
                return [mod.SourceUnit(language=ctx.lang_key, identifier="x", source_path="src/bad.src")]

            def extract(unit, ctx):
                resolved = mod.resolve_source(ctx.repo_root, Path(unit.source_path))
                data = mod.read_source_bytes(ctx.repo_root, resolved)
                return mod.DocPage(title="x", path="stub/x.md", body=data.decode("utf-8"),
                                    source_path=unit.source_path, meta={})

            m.discover = discover
            m.extract = extract
            m.provenance = lambda config: {}
            return m

        stub = make_stub()
        mod.load_extractor_module = lambda name: stub
        # A test double stands in for the shipped-module check: the stub is
        # not a file in the plugin's extractors directory.
        mod.resolve_extractor_path = lambda name: Path(str(name))
        code = mod.main(["--config", str(root / "docs" / "manifest.yml")])
        self.assertEqual(code, 1)
        self.assertFalse((root / "docs" / "code").exists())

    def test_toctou_source_becomes_a_directory_between_check_and_read(self):
        """Driven in-process: monkeypatch resolve_source to succeed, then
        replace the regular file with a directory before read_source_bytes
        runs. This exercises the fstat re-check inside read_source_bytes,
        which resolve_source alone cannot catch."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        src = root / "victim.py"
        src.write_text("# victim\n", encoding="utf-8")
        mod = _load_dispatcher()
        resolved = mod.resolve_source(root, Path("victim.py"))
        src.unlink()
        src.mkdir()
        with self.assertRaises(mod.ExtractionRefusal) as ctx:
            mod.read_source_bytes(root, resolved)
        self.assertIn("no longer a regular file", ctx.exception.cause)

    def test_positive_control_toctou_helper_reads_a_stable_regular_file(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        src = root / "stable.py"
        src.write_text("# stable\n", encoding="utf-8")
        mod = _load_dispatcher()
        resolved = mod.resolve_source(root, Path("stable.py"))
        self.assertEqual(mod.read_source_bytes(root, resolved), b"# stable\n")

    def test_in_repo_symlinked_source_yields_identical_bytes_except_owner(self):
        """An in-repo symlink whose target is elsewhere in the same repo (so
        it resolves inside the repo root and is admitted) must produce a
        page byte-identical to the same content read from a direct file at
        the same selected path -- containment never changes the recorded
        spelling (contract C2) or the body, only that a row carries `owner`.
        """
        direct_root = self._fallback_tree()
        r = _run(direct_root, "--config", str(direct_root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        direct_body = (direct_root / "docs" / "code" / "fallback" / "src" / "keep.py.md").read_bytes()

        linked_root = self._fallback_tree()
        (linked_root / "elsewhere").mkdir()
        target = linked_root / "elsewhere" / "real.py"
        target.write_text("# kept\n", encoding="utf-8")
        (linked_root / "src" / "keep.py").unlink()
        try:
            (linked_root / "src" / "keep.py").symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")
        r2 = _run(linked_root, "--config", str(linked_root / "docs" / "manifest.yml"))
        self.assertEqual(r2.returncode, 0, r2.stderr)
        linked_body = (linked_root / "docs" / "code" / "fallback" / "src" / "keep.py.md").read_bytes()

        self.assertEqual(direct_body, linked_body)
        manifest = json.loads(
            (linked_root / "docs" / "code" / "_meta" / "manifest.json").read_text()
        )
        by_doc = {row["doc_path"]: row for row in manifest["pages"]}
        self.assertEqual(by_doc["fallback/src/keep.py.md"]["source_path"], "src/keep.py")
        self.assertIn("owner", by_doc["fallback/src/keep.py.md"])


class D6RealPathToctouTests(unittest.TestCase):
    """ADR-0131 clause 14 D6 / contract C2, item 3: the check-to-read race
    driven through fallback's and elixir's OWN discover/extract call sites
    -- not the bare helper functions D6PerExtractorContainmentTests drives
    directly. `read_source_bytes` is monkeypatched on the loaded dispatcher
    module (which fallback.py and elixir.py both reach through their own
    `_dispatch` reference, resolved dynamically at call time) to mutate the
    source into a directory or a FIFO right before delegating to the real
    function, so the real fstat re-check inside `read_source_bytes` is what
    refuses -- in both lanes, before any write.
    """

    def _mutate_to_directory(self, resolved: Path) -> None:
        resolved.unlink()
        resolved.mkdir()

    def _mutate_to_fifo(self, resolved: Path) -> None:
        resolved.unlink()
        os.mkfifo(resolved)
        # A blocking open(O_RDONLY) on a FIFO waits for a writer; open the
        # other end from a background thread so read_source_bytes' open call
        # unblocks (and hits the fstat re-check) instead of hanging the test.
        def _open_writer():
            try:
                fd = os.open(str(resolved), os.O_WRONLY)
                os.close(fd)
            except OSError:
                pass
        threading.Thread(target=_open_writer, daemon=True).start()

    def _patch_read_source_bytes(self, mod, mutate):
        original = mod.read_source_bytes

        def patched(repo_root, resolved):
            mutate(resolved)
            return original(repo_root, resolved)

        mod.read_source_bytes = patched

    def _elixir_tree(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs").mkdir()
        (root / "lib").mkdir()
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n    elixir:\n      extractor: elixir\n'
            '      glob: "lib/*.ex"\n',
            encoding="utf-8",
        )
        (root / "lib" / "victim.ex").write_text(
            'defmodule Victim do\n  @moduledoc "v"\nend\n', encoding="utf-8",
        )
        return root

    def _fallback_tree(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs").mkdir()
        (root / "src").mkdir()
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n    any:\n      extractor: fallback\n'
            '      glob: "src/*.py"\n',
            encoding="utf-8",
        )
        (root / "src" / "victim.py").write_text("# victim\n", encoding="utf-8")
        return root

    def test_positive_control_elixir_unmutated_tree_writes_normally(self):
        root = self._elixir_tree()
        mod = _load_dispatcher()
        code = mod.main(["--config", str(root / "docs" / "manifest.yml")])
        self.assertEqual(code, 0)
        self.assertTrue((root / "docs" / "code").exists())

    def test_positive_control_fallback_unmutated_tree_writes_normally(self):
        root = self._fallback_tree()
        mod = _load_dispatcher()
        code = mod.main(["--config", str(root / "docs" / "manifest.yml")])
        self.assertEqual(code, 0)
        self.assertTrue((root / "docs" / "code").exists())

    def test_elixir_real_discover_path_toctou_refuses_before_any_write_both_lanes(self):
        for mutate_name, mutate in (
            ("directory", self._mutate_to_directory), ("fifo", self._mutate_to_fifo),
        ):
            for dry_run in (False, True):
                with self.subTest(mutate=mutate_name, dry_run=dry_run):
                    root = self._elixir_tree()
                    mod = _load_dispatcher()
                    self._patch_read_source_bytes(mod, mutate)
                    argv = ["--config", str(root / "docs" / "manifest.yml")]
                    if dry_run:
                        argv.append("--dry-run")
                    code = mod.main(argv)
                    self.assertEqual(code, 1)
                    self.assertFalse((root / "docs" / "code").exists(), "no write before the refusal")

    def test_fallback_real_extract_path_toctou_refuses_before_any_write_both_lanes(self):
        for mutate_name, mutate in (
            ("directory", self._mutate_to_directory), ("fifo", self._mutate_to_fifo),
        ):
            for dry_run in (False, True):
                with self.subTest(mutate=mutate_name, dry_run=dry_run):
                    root = self._fallback_tree()
                    mod = _load_dispatcher()
                    self._patch_read_source_bytes(mod, mutate)
                    argv = ["--config", str(root / "docs" / "manifest.yml")]
                    if dry_run:
                        argv.append("--dry-run")
                    code = mod.main(argv)
                    self.assertEqual(code, 1)
                    self.assertFalse((root / "docs" / "code").exists(), "no write before the refusal")


class OutputRootSymlinkReportTests(unittest.TestCase):
    """ADR-0131 clause 14 D1 output root."""

    def _victim(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "victim"
        (root / "docs" / "code").mkdir(parents=True)
        (root / "src").mkdir()
        (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: docs\n', encoding="utf-8")
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n    any:\n      extractor: fallback\n'
            '      glob: "src/*.py"\n',
            encoding="utf-8",
        )
        (root / "src" / "a.py").write_text('# header\n', encoding="utf-8")
        return root

    def test_code_dir_linked_outside_every_crux_tree_refuses_untouched(self):
        root = self._victim()
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        outside_dir = Path(outside.name) / "populated"
        outside_dir.mkdir()
        precious = outside_dir / "DoNotTouch.md"
        precious.write_text("# precious\n", encoding="utf-8")
        shutil.rmtree(root / "docs" / "code")
        try:
            (root / "docs" / "code").symlink_to(outside_dir)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")

        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual(precious.read_text(encoding="utf-8"), "# precious\n")
        self.assertEqual(sorted(p.name for p in outside_dir.iterdir()), ["DoNotTouch.md"])

    def test_code_dir_linked_into_a_second_crux_tree_is_admitted_and_reported(self):
        root = self._victim()
        tmp2 = tempfile.TemporaryDirectory()
        self.addCleanup(tmp2.cleanup)
        second = Path(tmp2.name) / "second"
        (second / "docs").mkdir(parents=True)
        (second / "docs" / "manifest.yml").write_text('schema_version: "5"\n', encoding="utf-8")
        second_code = second / "docs" / "code"
        second_code.mkdir()
        shutil.rmtree(root / "docs" / "code")
        try:
            (root / "docs" / "code").symlink_to(second_code)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")

        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("symlink", r.stderr)
        self.assertTrue((second_code / "fallback" / "src" / "a.py.md").is_file())


class MarkerRefusalDryRunLaneTests(unittest.TestCase):
    """Contract C1 / ADR-0131 clause 15: the marker check ("an output root
    the marker check does not admit") is in the twelve-refusal list, so a
    --dry-run run reports it as a validation_errors JSON payload on stdout,
    never stderr-only. ADR-mandated fix: before this fix the dispatcher
    printed straight to stderr regardless of --dry-run (RED observed by
    disabling the fix -- see the report)."""

    def _victim(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "victim"
        (root / "bionic" / "code").mkdir(parents=True)
        (root / "src").mkdir()
        (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8")
        (root / "bionic" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n    any:\n      extractor: fallback\n'
            '      glob: "src/*.py"\n',
            encoding="utf-8",
        )
        (root / "src" / "a.py").write_text('"""a."""\n', encoding="utf-8")
        return root

    def test_marker_refused_output_root_under_dry_run_is_a_json_payload(self):
        root = self._victim()
        scratch = root.parent / "scratch" / "code"
        r = _run(root, "--dry-run", "--output-dir", str(scratch))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual(r.stdout.strip()[:1], "{", "stdout must carry the JSON payload, not be empty")
        payload = json.loads(r.stdout)
        self.assertIn("validation_errors", payload)
        self.assertNotIn("drift", payload)
        causes = " ".join(e["cause"] for e in payload["validation_errors"])
        self.assertIn("not inside a crux tree", causes)
        self.assertFalse(scratch.exists(), "dry-run must write nothing")

    def test_positive_control_write_mode_stays_stderr_only(self):
        """Same case, without --dry-run: stderr-only, exit 1, empty stdout."""
        root = self._victim()
        scratch = root.parent / "scratch" / "code"
        r = _run(root, "--output-dir", str(scratch))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual(r.stdout, "")
        self.assertIn("not inside a crux tree", r.stderr)
        self.assertFalse(scratch.exists())


class DestinationPrePassTests(unittest.TestCase):
    """ADR-0131 clause 14 D1 destinations + link-refusing writes."""

    def _tree(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs" / "code").mkdir(parents=True)
        (root / "src").mkdir()
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n'
            '    a:\n      extractor: fallback\n      glob: "src/a.py"\n'
            '    z:\n      extractor: fallback\n      glob: "src/z.py"\n',
            encoding="utf-8",
        )
        (root / "src" / "a.py").write_text("# a\n", encoding="utf-8")
        (root / "src" / "z.py").write_text("# z\n", encoding="utf-8")
        return root

    def test_late_temp_file_collision_refuses_before_the_first_write(self):
        """A stale temp file at the destination written LAST in sort order
        (fallback/src/z.py.md sorts after fallback/src/a.py.md) refuses the
        whole run in the pre-pass, with every earlier destination untouched.
        """
        root = self._tree()
        code = root / "docs" / "code"
        stale_tmp = code / "fallback" / "src" / "z.py.md.tmp"
        stale_tmp.parent.mkdir(parents=True)
        stale_tmp.write_text("stale", encoding="utf-8")

        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("temporary file already exists", r.stderr)
        self.assertFalse((code / "fallback" / "src" / "a.py.md").exists(),
                          "no destination may be written once the pre-pass refuses")
        self.assertTrue(stale_tmp.is_file(), "the stale temp file itself is untouched")


class D1DestinationEdgeCaseTests(unittest.TestCase):
    """ADR-0131 clause 14 D1 destinations: a special file or a regular file
    standing where a directory is needed refuses; a symlink as a destination
    LEAF refuses; a stray symlink and FIFO under the output root that are
    NOT destinations are reported and neither followed nor deleted."""

    def _tree(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs" / "code").mkdir(parents=True)
        (root / "src").mkdir()
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n    any:\n      extractor: fallback\n'
            '      glob: "src/*.py"\n',
            encoding="utf-8",
        )
        (root / "src" / "a.py").write_text("# a\n", encoding="utf-8")
        return root

    def test_regular_file_standing_where_a_directory_is_needed_refuses(self):
        """`fallback/src/` must be a directory to hold `a.py.md`; a plain
        file already sitting there refuses instead of being clobbered."""
        root = self._tree()
        code = root / "docs" / "code"
        (code / "fallback").mkdir()
        (code / "fallback" / "src").write_text("not a directory\n", encoding="utf-8")

        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("not a directory", r.stderr)
        self.assertEqual(
            (code / "fallback" / "src").read_text(encoding="utf-8"), "not a directory\n",
        )

    def test_fifo_standing_where_a_directory_is_needed_refuses(self):
        root = self._tree()
        code = root / "docs" / "code"
        (code / "fallback").mkdir()
        fifo = code / "fallback" / "src"
        try:
            os.mkfifo(fifo)
        except (AttributeError, OSError):
            self.skipTest("mkfifo not supported on this platform")

        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("not a directory", r.stderr)
        self.assertTrue(stat.S_ISFIFO(fifo.lstat().st_mode), "the FIFO must be left exactly as it was")

    def test_symlink_as_a_page_destination_leaf_refuses(self):
        root = self._tree()
        code = root / "docs" / "code"
        (code / "fallback" / "src").mkdir(parents=True)
        victim = tempfile.TemporaryDirectory()
        self.addCleanup(victim.cleanup)
        victim_file = Path(victim.name) / "victim.md"
        victim_file.write_text("do not touch\n", encoding="utf-8")
        leaf = code / "fallback" / "src" / "a.py.md"
        try:
            leaf.symlink_to(victim_file)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")

        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("symlink", r.stderr)
        self.assertEqual(victim_file.read_text(encoding="utf-8"), "do not touch\n")
        self.assertTrue(leaf.is_symlink(), "the destination symlink must be left standing")

    def test_symlink_as_the_index_destination_leaf_refuses(self):
        root = self._tree()
        code = root / "docs" / "code"
        victim = tempfile.TemporaryDirectory()
        self.addCleanup(victim.cleanup)
        victim_file = Path(victim.name) / "victim.md"
        victim_file.write_text("do not touch\n", encoding="utf-8")
        try:
            (code / "index.md").symlink_to(victim_file)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")

        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual(victim_file.read_text(encoding="utf-8"), "do not touch\n")
        self.assertFalse((code / "fallback").exists(), "no page write may precede the refusal")

    def test_symlink_destination_leaf_refuses_under_dry_run_too(self):
        """Contract C1: a D1-failing destination is one of the twelve
        dispatcher-owned refusals, so --dry-run must report it as a
        validation_errors payload, never as ordinary page drift computed by
        following the very symlink the write-mode lane refuses."""
        root = self._tree()
        code = root / "docs" / "code"
        (code / "fallback" / "src").mkdir(parents=True)
        victim = tempfile.TemporaryDirectory()
        self.addCleanup(victim.cleanup)
        victim_file = Path(victim.name) / "victim.md"
        victim_file.write_text("do not touch\n", encoding="utf-8")
        leaf = code / "fallback" / "src" / "a.py.md"
        try:
            leaf.symlink_to(victim_file)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")

        r = _run(root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 1, r.stdout)
        payload = json.loads(r.stdout)
        self.assertIn("validation_errors", payload, "must not fall through to ordinary drift")
        self.assertNotIn("drift", payload)
        self.assertEqual(victim_file.read_text(encoding="utf-8"), "do not touch\n")
        self.assertTrue(leaf.is_symlink())

    def test_stray_symlink_and_fifo_under_output_root_are_reported_not_touched(self):
        """A full run reports (never follows or deletes) a stray symlink and
        a stray FIFO that are not among its own destinations."""
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        code = root / "docs" / "code"

        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        outside_file = Path(outside.name) / "outside.md"
        outside_file.write_text("outside\n", encoding="utf-8")
        stray_link = code / "stray_link.md"
        try:
            stray_link.symlink_to(outside_file)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")
        stray_fifo = code / "stray_fifo.md"
        try:
            os.mkfifo(stray_fifo)
        except (AttributeError, OSError):
            self.skipTest("mkfifo not supported on this platform")

        r2 = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("stray_link.md", r2.stderr)
        self.assertTrue(stray_link.is_symlink(), "the stray symlink must not be deleted")
        self.assertEqual(outside_file.read_text(encoding="utf-8"), "outside\n")
        self.assertTrue(stat.S_ISFIFO(stray_fifo.lstat().st_mode), "the stray FIFO must not be deleted")


class D1AncestorSymlinkTests(unittest.TestCase):
    """ADR-0131 clause 14 D1, the original reproduction: a symlinked
    ANCESTOR destination directory one level below the output root --
    `<output root>/fallback -> <external populated dir>` -- not the output
    root itself (OutputRootSymlinkReportTests) and not a leaf
    (D1DestinationEdgeCaseTests). RED observed against the pre-repair
    dispatcher at f0606fb: run from a scratch copy, it followed the link and
    wrote `src/a.py.md` into the external directory (exit 0, the external
    directory gained a new `src/` entry). Current code refuses before any
    write, in both lanes."""

    def _tree(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs" / "code").mkdir(parents=True)
        (root / "src").mkdir()
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n    any:\n      extractor: fallback\n'
            '      glob: "src/*.py"\n',
            encoding="utf-8",
        )
        (root / "src" / "a.py").write_text("# a\n", encoding="utf-8")
        return root

    def _victim_dir(self) -> tuple[Path, Path]:
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        outside_dir = Path(outside.name) / "populated"
        outside_dir.mkdir()
        precious = outside_dir / "DoNotTouch.md"
        precious.write_text("# precious\n", encoding="utf-8")
        return outside_dir, precious

    def test_symlinked_ancestor_refuses_in_write_mode_untouched(self):
        root = self._tree()
        code = root / "docs" / "code"
        outside_dir, precious = self._victim_dir()
        try:
            (code / "fallback").symlink_to(outside_dir)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")

        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("ancestor", r.stderr)
        self.assertEqual(precious.read_text(encoding="utf-8"), "# precious\n")
        self.assertEqual(sorted(p.name for p in outside_dir.iterdir()), ["DoNotTouch.md"])
        self.assertTrue((code / "fallback").is_symlink(), "the ancestor symlink itself must be left standing")

    def test_symlinked_ancestor_refuses_under_dry_run_untouched(self):
        root = self._tree()
        code = root / "docs" / "code"
        outside_dir, precious = self._victim_dir()
        try:
            (code / "fallback").symlink_to(outside_dir)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")

        r = _run(root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 1, r.stdout)
        payload = json.loads(r.stdout)
        self.assertIn("validation_errors", payload)
        self.assertNotIn("drift", payload)
        self.assertEqual(precious.read_text(encoding="utf-8"), "# precious\n")
        self.assertEqual(sorted(p.name for p in outside_dir.iterdir()), ["DoNotTouch.md"])
        self.assertTrue((code / "fallback").is_symlink())


class D2ClaimingTreeTests(unittest.TestCase):
    """ADR-0131 clause 14 D2: explicit --config claims sources via the
    nearest ancestor whose .bionic.yml docs_dir resolves to the manifest's
    own directory, including a nested docs_dir like meta/docs."""

    def test_nested_docs_dir_is_claimed_not_the_grandparent(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        docs = root / "meta" / "docs"
        docs.mkdir(parents=True)
        (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: meta/docs\n', encoding="utf-8")
        (root / "src").mkdir()
        (root / "src" / "a.py").write_text("# a\n", encoding="utf-8")
        (docs / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n    any:\n      extractor: fallback\n'
            '      glob: "src/*.py"\n',
            encoding="utf-8",
        )
        r = _run(root, "--config", str(docs / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((docs / "code" / "fallback" / "src" / "a.py.md").is_file())

    def _nested_tree(self) -> tuple[Path, Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "repo"
        docs = root / "meta" / "docs"
        docs.mkdir(parents=True)
        (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: meta/docs\n', encoding="utf-8")
        (root / "src").mkdir()
        (root / "src" / "a.py").write_text("# a\n", encoding="utf-8")
        (docs / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n    any:\n      extractor: fallback\n'
            '      glob: "src/*.py"\n',
            encoding="utf-8",
        )
        return root, docs

    def test_implicit_relative_absolute_and_symlinked_spellings_match(self):
        """RED observed against the pre-repair dispatcher at f0606fb: given
        this same nested `docs_dir: meta/docs` fixture and an explicit
        relative --config, it wrote 0 pages (it took the manifest's
        grandparent as the source root, landing on `repo/meta` instead of
        `repo`, so `src/*.py` matched nothing). Current code's claiming walk
        makes every spelling of one manifest -- implicit (no --config, via
        the repo-root .bionic.yml), relative, absolute, and symlinked --
        resolve the same source root and produce byte-identical output
        trees and --dry-run payloads."""
        spellings: dict[str, tuple[Path, Path, list[str]]] = {}
        for spelling in ("implicit", "relative", "absolute", "symlinked"):
            root, docs = self._nested_tree()
            manifest = docs / "manifest.yml"
            if spelling == "implicit":
                argv: list[str] = []
            elif spelling == "relative":
                argv = ["--config", os.path.relpath(manifest, root)]
            elif spelling == "absolute":
                argv = ["--config", str(manifest.resolve())]
            else:
                link = root / "manifest_link.yml"
                try:
                    link.symlink_to(manifest)
                except (OSError, NotImplementedError):
                    self.skipTest("symlinks not supported on this platform")
                argv = ["--config", str(link)]
            spellings[spelling] = (root, docs, argv)

        for spelling, (root, docs, argv) in spellings.items():
            r = _run(root, *argv)
            self.assertEqual(r.returncode, 0, (spelling, r.stderr))

        fingerprints = {
            spelling: _fingerprint(docs / "code") for spelling, (_, docs, _) in spellings.items()
        }
        baseline = fingerprints["implicit"]
        for spelling, fp in fingerprints.items():
            self.assertEqual(fp, baseline, spelling)

        payloads = {}
        for spelling, (root, docs, argv) in spellings.items():
            r2 = _run(root, "--dry-run", *argv)
            self.assertEqual(r2.returncode, 0, (spelling, r2.stdout))
            payloads[spelling] = json.loads(r2.stdout)
        baseline_payload = payloads["implicit"]
        for spelling, payload in payloads.items():
            self.assertEqual(payload, baseline_payload, spelling)

    def test_grandparent_fallback_for_a_manifest_no_bionic_yml_claims(self):
        """A cross-repository --config: the target manifest's own repo has
        no .bionic.yml anywhere, so the claiming walk finds no claimant and
        falls back to the manifest's grandparent (its directory's parent),
        which is the target repo's own root -- resolved from --config, never
        from the invoking cwd."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        target = Path(tmp.name) / "target_repo"
        (target / "docs").mkdir(parents=True)
        (target / "src").mkdir()
        (target / "src" / "a.py").write_text("# a\n", encoding="utf-8")
        (target / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n    any:\n      extractor: fallback\n'
            '      glob: "src/*.py"\n',
            encoding="utf-8",
        )
        other_cwd = tempfile.TemporaryDirectory()
        self.addCleanup(other_cwd.cleanup)

        r = _run(Path(other_cwd.name), "--config", str(target / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((target / "docs" / "code" / "fallback" / "src" / "a.py.md").is_file())


def _make_stub_api2_module(doc_path: str, title: str = "Stub"):
    """A minimal EXTRACTOR_API=2 extractor module (contract C3), built
    in-process (never written to the real extractors/ dir)."""
    mod = types.ModuleType(f"stub_api2_{doc_path.replace('/', '_')}")
    mod.EXTRACTOR_API = 2

    def discover(repo_root, config, ctx):
        return [dispatcher.SourceUnit(language=ctx.lang_key, identifier="x", source_path="stub.src")]

    def extract(unit, ctx):
        return dispatcher.DocPage(
            title=title, path=doc_path, body=f"# {title}\n", source_path=unit.source_path,
            meta={},
        )

    def provenance(config):
        return {}

    mod.discover = discover
    mod.extract = extract
    mod.provenance = provenance
    return mod


class D5CollisionTests(unittest.TestCase):
    def test_two_keys_claiming_one_doc_path_refuse_before_any_write(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs" / "code").mkdir(parents=True)
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n'
            '    a:\n      extractor: stub_a\n'
            '    b:\n      extractor: stub_b\n',
            encoding="utf-8",
        )
        mod = _load_dispatcher()
        stub_a = _make_stub_api2_module("collide/Same.md", title="A")
        stub_b = _make_stub_api2_module("collide/Same.md", title="B")

        def fake_load(name):
            return {"stub_a": stub_a, "stub_b": stub_b}[name]

        mod.load_extractor_module = fake_load
        # A test double stands in for the shipped-module check: the stub is
        # not a file in the plugin's extractors directory.
        mod.resolve_extractor_path = lambda name: Path(str(name))
        code = mod.main(["--config", str(root / "docs" / "manifest.yml")])
        self.assertEqual(code, 1)
        self.assertFalse((root / "docs" / "code" / "collide").exists())


class LangOwnerScopingTests(unittest.TestCase):
    """ADR-0131 clause 14 D3 / council Q3."""

    def _tree(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs").mkdir()
        (root / "src_py").mkdir()
        (root / "src_sh").mkdir()
        (root / "src_py" / "one.py").write_text("# one\n", encoding="utf-8")
        (root / "src_sh" / "two.sh").write_text("# two\n", encoding="utf-8")
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n'
            '    py:\n      extractor: fallback\n      glob: "src_py/*.py"\n'
            '    sh:\n      extractor: fallback\n      glob: "src_sh/*.sh"\n',
            encoding="utf-8",
        )
        return root

    def test_lang_run_leaves_the_other_shared_extractor_key_intact(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        code = root / "docs" / "code"
        before_sh = (code / "fallback" / "src_sh" / "two.sh.md").read_bytes()
        before_index = (code / "index.md").read_bytes()

        (root / "src_py" / "one.py").write_text("# one changed\n", encoding="utf-8")
        r2 = _run(root, "--config", str(root / "docs" / "manifest.yml"), "--lang", "py")
        self.assertEqual(r2.returncode, 0, r2.stderr)

        self.assertEqual((code / "fallback" / "src_sh" / "two.sh.md").read_bytes(), before_sh)
        manifest = json.loads((code / "_meta" / "manifest.json").read_text())
        by_doc = {row["doc_path"]: row for row in manifest["pages"]}
        self.assertEqual(by_doc["fallback/src_sh/two.sh.md"]["owner"], "sh")
        self.assertEqual(by_doc["fallback/src_py/one.py.md"]["owner"], "py")
        self.assertIn("two.sh.md", (code / "index.md").read_text())
        self.assertIn("one.py.md", (code / "index.md").read_text())
        # Neither page's title or doc_path changed (only one.py's body did),
        # so the rendered index text is unaffected -- the --lang write still
        # regenerates it from the full post-run page set (written+preserved).
        self.assertEqual((code / "index.md").read_bytes(), before_index)

    def test_ownerless_metadata_refuses_a_lang_write(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        meta_path = root / "docs" / "code" / "_meta" / "manifest.json"
        manifest = json.loads(meta_path.read_text())
        for row in manifest["pages"]:
            row.pop("owner", None)
        meta_path.write_text(json.dumps(manifest), encoding="utf-8")

        r2 = _run(root, "--config", str(root / "docs" / "manifest.yml"), "--lang", "py")
        self.assertEqual(r2.returncode, 1, r2.stdout)
        self.assertIn("no owner", r2.stderr)
        self.assertIn("full extract-code-docs", r2.stderr)

    def test_dry_run_lang_reports_unowned_without_counting_as_drift(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        stray = root / "docs" / "code" / "stray.md"
        stray.write_text("# stray\n", encoding="utf-8")

        r2 = _run(root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"), "--lang", "py")
        payload = json.loads(r2.stdout)
        self.assertIn("stray.md", payload["unowned"])
        self.assertFalse(payload["drift"], "an unowned file must not count as drift under --lang")


class LangScopingAdditionalTests(unittest.TestCase):
    """ADR-0131 clause 14 D3/index, council Q3: the remaining --lang cases
    not yet covered by LangOwnerScopingTests."""

    def _tree(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs").mkdir()
        (root / "src_py").mkdir()
        (root / "src_sh").mkdir()
        (root / "src_py" / "one.py").write_text("# one\n", encoding="utf-8")
        (root / "src_sh" / "two.sh").write_text("# two\n", encoding="utf-8")
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n'
            '    py:\n      extractor: fallback\n      glob: "src_py/*.py"\n'
            '    sh:\n      extractor: fallback\n      glob: "src_sh/*.sh"\n',
            encoding="utf-8",
        )
        return root

    def test_preserved_row_with_missing_page_file_refuses_naming_a_full_run(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        code = root / "docs" / "code"
        (code / "fallback" / "src_sh" / "two.sh.md").unlink()

        meta_before = (code / "_meta" / "manifest.json").read_bytes()
        r2 = _run(root, "--config", str(root / "docs" / "manifest.yml"), "--lang", "py")
        self.assertEqual(r2.returncode, 1, r2.stdout)
        self.assertIn("page file is missing", r2.stderr)
        self.assertIn("full extract-code-docs", r2.stderr)
        self.assertEqual((code / "_meta" / "manifest.json").read_bytes(), meta_before,
                          "no write may precede the refusal")

    def test_preserved_page_without_h1_first_line_refuses_naming_a_full_run(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        code = root / "docs" / "code"
        page = code / "fallback" / "src_sh" / "two.sh.md"
        page.write_text("not a heading\nbody\n", encoding="utf-8")

        r2 = _run(root, "--config", str(root / "docs" / "manifest.yml"), "--lang", "py")
        self.assertEqual(r2.returncode, 1, r2.stdout)
        self.assertIn("no `# ` first line", r2.stderr)
        self.assertIn("full extract-code-docs", r2.stderr)

    def test_lang_run_prunes_its_own_page_whose_source_vanished_and_it_leaves_the_index(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        (root / "src_py" / "one.py").unlink()

        r2 = _run(root, "--config", str(root / "docs" / "manifest.yml"), "--lang", "py")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        code = root / "docs" / "code"
        self.assertFalse((code / "fallback" / "src_py" / "one.py.md").exists(),
                          "the vanished source's own page must be pruned")
        self.assertNotIn("one.py.md", (code / "index.md").read_text(encoding="utf-8"))
        self.assertIn("two.sh.md", (code / "index.md").read_text(encoding="utf-8"))

    def test_stray_file_is_reported_not_indexed_not_pruned_then_pruned_by_a_full_run(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        code = root / "docs" / "code"
        stray = code / "fallback" / "src_py" / "stray.md"
        stray.write_text("# stray\n", encoding="utf-8")

        r2 = _run(root, "--config", str(root / "docs" / "manifest.yml"), "--lang", "py")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("not indexed", r2.stderr)
        self.assertNotIn("stray.md", (code / "index.md").read_text(encoding="utf-8"))
        self.assertTrue(stray.is_file(), "a --lang run must not prune a stray it does not own")

        r3 = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r3.returncode, 0, r3.stderr)
        self.assertFalse(stray.exists(), "a full run prunes the stray")

    def test_lang_write_stray_report_and_dry_run_unowned_list_are_the_same_set(self):
        """Item 4 paired check: the write-mode --lang stray report and the
        --dry-run --lang `unowned` list both come from
        `_classify_output_dir_entries` over the same known-doc-path set
        (`final_pages`), so planting one regular stray and one non-regular
        (symlink) stray at identical paths in two copies of the same
        starting tree, then running the paired write and dry-run, must name
        exactly the same two files in both places."""
        root_a = self._tree()
        r = _run(root_a, "--config", str(root_a / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        code_a = root_a / "docs" / "code"
        (code_a / "fallback" / "src_py" / "stray.md").write_text("# stray\n", encoding="utf-8")
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        outside_file = Path(outside.name) / "outside.md"
        outside_file.write_text("outside\n", encoding="utf-8")
        try:
            (code_a / "fallback" / "src_py" / "stray_link.md").symlink_to(outside_file)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")

        root_b = self._tree()
        rb = _run(root_b, "--config", str(root_b / "docs" / "manifest.yml"))
        self.assertEqual(rb.returncode, 0, rb.stderr)
        code_b = root_b / "docs" / "code"
        (code_b / "fallback" / "src_py" / "stray.md").write_text("# stray\n", encoding="utf-8")
        (code_b / "fallback" / "src_py" / "stray_link.md").symlink_to(outside_file)

        expected = {"fallback/src_py/stray.md", "fallback/src_py/stray_link.md"}

        r_dry = _run(root_a, "--config", str(root_a / "docs" / "manifest.yml"), "--lang", "py", "--dry-run")
        self.assertEqual(r_dry.returncode, 0, r_dry.stdout)
        payload = json.loads(r_dry.stdout)
        self.assertEqual(set(payload["unowned"]), expected)

        r_write = _run(root_b, "--config", str(root_b / "docs" / "manifest.yml"), "--lang", "py")
        self.assertEqual(r_write.returncode, 0, r_write.stderr)
        for name in expected:
            self.assertIn(name, r_write.stderr)
        self.assertTrue((code_a / "fallback" / "src_py" / "stray.md").is_file(), "dry-run writes/prunes nothing")
        self.assertTrue((code_b / "fallback" / "src_py" / "stray.md").is_file(), "a --lang write must not prune an unowned stray")

    def test_lang_prune_reports_a_special_file_at_an_owned_path_instead_of_silently_skipping(self):
        """Paired-construct sweep finding: write_pages' full-run prune loop
        reports (never deletes) a stray special file; the --lang prune loop
        must do the same at an owned doc_path whose file became a FIFO,
        instead of a bare `is_file()` check that skips it silently."""
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        code = root / "docs" / "code"
        owned = code / "fallback" / "src_py" / "one.py.md"
        owned.unlink()
        try:
            os.mkfifo(owned)
        except (AttributeError, OSError):
            self.skipTest("mkfifo not supported on this platform")
        (root / "src_py" / "one.py").unlink()  # source vanished -> eligible for prune

        r2 = _run(root, "--config", str(root / "docs" / "manifest.yml"), "--lang", "py")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("not a regular file", r2.stderr)
        self.assertTrue(stat.S_ISFIFO(owned.lstat().st_mode), "the FIFO must not be deleted")

    def test_two_api2_stub_keys_preserve_each_others_provenance_block(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs").mkdir()
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n'
            '    a:\n      extractor: stub_a\n'
            '    b:\n      extractor: stub_b\n',
            encoding="utf-8",
        )
        mod = _load_dispatcher()

        def make_stub(key, doc_path, prov):
            m = types.ModuleType(f"stub_api2_{key}")
            m.EXTRACTOR_API = 2

            def discover(repo_root, config, ctx):
                return [mod.SourceUnit(language=ctx.lang_key, identifier=key, source_path=f"stub-{key}.src")]

            def extract(unit, ctx):
                return mod.DocPage(title=key, path=doc_path, body=f"# {key}\n",
                                    source_path=unit.source_path, meta={})

            m.discover = discover
            m.extract = extract
            m.provenance = lambda config: dict(prov)
            return m

        stub_a = make_stub("a", "a/Page.md", {"v": 1})
        stub_b = make_stub("b", "b/Page.md", {"v": 2})

        def fake_load(name):
            return {"stub_a": stub_a, "stub_b": stub_b}[name]

        mod.load_extractor_module = fake_load
        # A test double stands in for the shipped-module check: the stub is
        # not a file in the plugin's extractors directory.
        mod.resolve_extractor_path = lambda name: Path(str(name))
        code_ret = mod.main(["--config", str(root / "docs" / "manifest.yml")])
        self.assertEqual(code_ret, 0)
        meta_path = root / "docs" / "code" / "_meta" / "manifest.json"
        before = json.loads(meta_path.read_text())
        self.assertEqual(before["extractors"], {"a": {"v": 1}, "b": {"v": 2}})

        stub_b.extract = lambda unit, ctx: mod.DocPage(
            title="b2", path="b/Page.md", body="# b2\n", source_path=unit.source_path, meta={},
        )
        stub_b.provenance = lambda config: {"v": 3}
        mod2 = _load_dispatcher()
        mod2.load_extractor_module = fake_load
        # A test double stands in for the shipped-module check: the stub is
        # not a file in the plugin's extractors directory.
        mod2.resolve_extractor_path = lambda name: Path(str(name))
        code_ret2 = mod2.main(["--config", str(root / "docs" / "manifest.yml"), "--lang", "b"])
        self.assertEqual(code_ret2, 0)
        after = json.loads(meta_path.read_text())
        self.assertEqual(after["extractors"], {"a": {"v": 1}, "b": {"v": 3}},
                          "key a's block must be preserved byte-unchanged while b's is rewritten")

    def test_d5_collision_against_a_preserved_owners_page_under_lang(self):
        """A --lang run's own new page claiming a doc_path already owned by
        a preserved (non-selected) key's row refuses before any write."""
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        preserved_doc_path = "fallback/src_sh/two.sh.md"
        code = root / "docs" / "code"
        self.assertTrue((code / preserved_doc_path).is_file())
        before_bytes = (code / preserved_doc_path).read_bytes()

        mod = _load_dispatcher()
        stub = _make_stub_api2_module(preserved_doc_path, title="Colliding")

        # Swap "py"'s extractor to a stub that claims sh's already-preserved
        # doc_path, via a fresh manifest naming the stub.
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n'
            '    py:\n      extractor: stub_collider\n'
            '    sh:\n      extractor: fallback\n      glob: "src_sh/*.sh"\n',
            encoding="utf-8",
        )
        mod.load_extractor_module = lambda name: stub
        # A test double stands in for the shipped-module check: the stub is
        # not a file in the plugin's extractors directory.
        mod.resolve_extractor_path = lambda name: Path(str(name))
        ret = mod.main(["--config", str(root / "docs" / "manifest.yml"), "--lang", "py"])
        self.assertEqual(ret, 1)
        self.assertEqual(
            (code / preserved_doc_path).read_bytes(), before_bytes,
            "the preserved owner's page must be untouched by the refused write",
        )


class DryRunDriftTests(unittest.TestCase):
    def _tree(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs").mkdir()
        (root / "src").mkdir()
        (root / "src" / "a.py").write_text("# a\n", encoding="utf-8")
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n    any:\n      extractor: fallback\n'
            '      glob: "src/*.py"\n',
            encoding="utf-8",
        )
        return root

    def test_clean_full_dry_run_after_a_real_run(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        r2 = _run(root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r2.returncode, 0, r2.stdout)
        payload = json.loads(r2.stdout)
        self.assertFalse(payload["drift"])
        self.assertFalse(payload["index_drift"])
        self.assertFalse(payload["metadata_drift"])
        self.assertEqual(payload["pages"], {"edited": [], "missing": [], "unexpected": []})

    def test_edited_page_on_disk_is_reported_and_counts_as_drift(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        page = root / "docs" / "code" / "fallback" / "src" / "a.py.md"
        page.write_text(page.read_text(encoding="utf-8") + "hand-edited\n", encoding="utf-8")

        r2 = _run(root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r2.returncode, 1, r2.stdout)
        payload = json.loads(r2.stdout)
        self.assertTrue(payload["drift"])
        self.assertIn("fallback/src/a.py.md", payload["pages"]["edited"])
        self.assertIn("hand-edited", page.read_text(encoding="utf-8"), "dry-run writes nothing")

    def test_unexpected_page_counts_as_drift_on_a_full_dry_run(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        stray = root / "docs" / "code" / "fallback" / "stray.md"
        stray.write_text("# stray\n", encoding="utf-8")

        r2 = _run(root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"))
        payload = json.loads(r2.stdout)
        self.assertTrue(payload["drift"])
        self.assertIn("fallback/stray.md", payload["pages"]["unexpected"])
        self.assertTrue(stray.is_file(), "dry-run writes/prunes nothing")


class DryRunDriftAdditionalTests(unittest.TestCase):
    """ADR-0131 clause 14 D4: the remaining --dry-run cases, plus the
    paired rule 'drift exactly when the same invocation without --dry-run
    would change a byte' -- for each drift case, the write changes bytes;
    for the clean case, it does not."""

    def _tree(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs").mkdir()
        (root / "src").mkdir()
        (root / "src" / "a.py").write_text("# a\n", encoding="utf-8")
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n    any:\n      extractor: fallback\n'
            '      glob: "src/*.py"\n',
            encoding="utf-8",
        )
        return root

    def _lang_tree(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs").mkdir()
        (root / "src_py").mkdir()
        (root / "src_sh").mkdir()
        (root / "src_py" / "one.py").write_text("# one\n", encoding="utf-8")
        (root / "src_sh" / "two.sh").write_text("# two\n", encoding="utf-8")
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n'
            '    py:\n      extractor: fallback\n      glob: "src_py/*.py"\n'
            '    sh:\n      extractor: fallback\n      glob: "src_sh/*.sh"\n',
            encoding="utf-8",
        )
        return root

    def test_missing_page_is_reported_and_the_paired_write_creates_it(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        page = root / "docs" / "code" / "fallback" / "src" / "a.py.md"
        page.unlink()

        r2 = _run(root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r2.returncode, 1, r2.stdout)
        payload = json.loads(r2.stdout)
        self.assertTrue(payload["drift"])
        self.assertIn("fallback/src/a.py.md", payload["pages"]["missing"])
        self.assertFalse(page.exists(), "dry-run must write nothing")

        r3 = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r3.returncode, 0, r3.stderr)
        self.assertTrue(page.is_file(), "the paired write must actually create the missing page")

    def test_deleted_index_is_reported_and_the_paired_write_recreates_it(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        index_path = root / "docs" / "code" / "index.md"
        index_path.unlink()

        r2 = _run(root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r2.returncode, 1, r2.stdout)
        payload = json.loads(r2.stdout)
        self.assertTrue(payload["drift"])
        self.assertTrue(payload["index_drift"])
        self.assertFalse(index_path.exists(), "dry-run must write nothing")

        r3 = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r3.returncode, 0, r3.stderr)
        self.assertTrue(index_path.is_file())

    def test_edited_metadata_field_is_reported_and_the_paired_write_fixes_it(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        meta_path = root / "docs" / "code" / "_meta" / "manifest.json"
        manifest = json.loads(meta_path.read_text())
        manifest["pages"][0]["sha256"] = "0" * 64
        edited_bytes = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        meta_path.write_text(edited_bytes, encoding="utf-8")

        r2 = _run(root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r2.returncode, 1, r2.stdout)
        payload = json.loads(r2.stdout)
        self.assertTrue(payload["drift"])
        self.assertTrue(payload["metadata_drift"])
        self.assertEqual(meta_path.read_text(encoding="utf-8"), edited_bytes, "dry-run must write nothing")

        r3 = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r3.returncode, 0, r3.stderr)
        self.assertNotEqual(meta_path.read_text(encoding="utf-8"), edited_bytes,
                             "the paired write must actually change the metadata bytes")

    def test_clean_tree_paired_write_changes_no_byte_full_and_lang(self):
        root = self._lang_tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        code = root / "docs" / "code"
        before = {
            str(f.relative_to(code)): f.read_bytes()
            for f in sorted(code.rglob("*")) if f.is_file()
        }

        r2 = _run(root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r2.returncode, 0, r2.stdout)
        self.assertFalse(json.loads(r2.stdout)["drift"])
        r3 = _run(root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"), "--lang", "py")
        self.assertEqual(r3.returncode, 0, r3.stdout)
        self.assertFalse(json.loads(r3.stdout)["drift"])

        # Paired write: a clean tree's real run changes no byte.
        r4 = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r4.returncode, 0, r4.stderr)
        after = {
            str(f.relative_to(code)): f.read_bytes()
            for f in sorted(code.rglob("*")) if f.is_file()
        }
        self.assertEqual(before, after, "a clean tree's real run must change no byte")

    def test_dry_run_lang_reports_only_its_own_key_edit_never_the_others(self):
        """--dry-run --lang K reports another key's edited page as nothing
        -- not K's drift, not even an entry -- because it belongs to a key
        this run never touches."""
        root = self._lang_tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        code = root / "docs" / "code"
        sh_page = code / "fallback" / "src_sh" / "two.sh.md"
        sh_page.write_text(sh_page.read_text(encoding="utf-8") + "hand-edited\n", encoding="utf-8")

        r2 = _run(root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"), "--lang", "py")
        self.assertEqual(r2.returncode, 0, r2.stdout)
        payload = json.loads(r2.stdout)
        self.assertFalse(payload["drift"], "another key's edit must not count as this run's drift")
        self.assertNotIn("fallback/src_sh/two.sh.md", payload["pages"]["edited"])
        self.assertNotIn("fallback/src_sh/two.sh.md", payload.get("unowned", []),
                          "sh owns this page; it is not unowned even though sh was not selected")
        self.assertIn("hand-edited", sh_page.read_text(encoding="utf-8"), "dry-run writes nothing")


class RefusalLaneCompletenessTests(unittest.TestCase):
    """ADR-0131 clause 15 / contract C1: dispatcher-owned refusals not yet
    exercised under --dry-run, plus the API-2 exception-wrapping shape
    (an exception class name in the cause, never a traceback) for
    RecursionError specifically."""

    def test_existing_temp_file_refusal_reports_as_json_under_dry_run(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs" / "code").mkdir(parents=True)
        (root / "src").mkdir()
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n    any:\n      extractor: fallback\n'
            '      glob: "src/*.py"\n',
            encoding="utf-8",
        )
        (root / "src" / "a.py").write_text("# a\n", encoding="utf-8")
        stale_tmp = root / "docs" / "code" / "fallback" / "src" / "a.py.md.tmp"
        stale_tmp.parent.mkdir(parents=True)
        stale_tmp.write_text("stale", encoding="utf-8")

        r = _run(root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 1, r.stdout)
        payload = json.loads(r.stdout)
        self.assertIn("validation_errors", payload)
        self.assertNotIn("drift", payload)
        causes = " ".join(e["cause"] for e in payload["validation_errors"])
        self.assertIn("temporary file already exists", causes)
        self.assertEqual(stale_tmp.read_text(encoding="utf-8"), "stale", "dry-run writes nothing")

    def test_ownerless_lang_write_refusal_reports_as_json_under_dry_run(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs").mkdir()
        (root / "src").mkdir()
        (root / "src" / "a.py").write_text("# a\n", encoding="utf-8")
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n    any:\n      extractor: fallback\n'
            '      glob: "src/*.py"\n',
            encoding="utf-8",
        )
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        meta_path = root / "docs" / "code" / "_meta" / "manifest.json"
        manifest = json.loads(meta_path.read_text())
        for row in manifest["pages"]:
            row.pop("owner", None)
        meta_path.write_text(json.dumps(manifest), encoding="utf-8")

        r2 = _run(root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"), "--lang", "any")
        self.assertEqual(r2.returncode, 1, r2.stdout)
        payload = json.loads(r2.stdout)
        self.assertIn("validation_errors", payload)
        self.assertNotIn("drift", payload)
        causes = " ".join(e["cause"] for e in payload["validation_errors"])
        self.assertIn("no owner", causes)
        self.assertIn("full extract-code-docs", causes)

    def test_api2_recursion_error_wraps_as_exception_class_name_never_a_traceback(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs").mkdir()
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n    py:\n      extractor: stub\n',
            encoding="utf-8",
        )
        mod = _load_dispatcher()
        stub = types.ModuleType("stub_recursion")
        stub.EXTRACTOR_API = 2

        def discover(repo_root, config, ctx):
            raise RecursionError("maximum recursion depth exceeded")

        stub.discover = discover
        stub.extract = lambda unit, ctx: None
        stub.provenance = lambda config: {}
        mod.load_extractor_module = lambda name: stub
        # A test double stands in for the shipped-module check: the stub is
        # not a file in the plugin's extractors directory.
        mod.resolve_extractor_path = lambda name: Path(str(name))

        import contextlib
        import io

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = mod.main(["--config", str(root / "docs" / "manifest.yml")])
        self.assertEqual(code, 1)
        self.assertIn("RecursionError", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        self.assertFalse((root / "docs" / "code").exists())

    def test_api2_recursion_error_wraps_as_json_under_dry_run(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs").mkdir()
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n    py:\n      extractor: stub\n',
            encoding="utf-8",
        )
        mod = _load_dispatcher()
        stub = types.ModuleType("stub_recursion_dry")
        stub.EXTRACTOR_API = 2
        stub.discover = lambda repo_root, config, ctx: (_ for _ in ()).throw(RecursionError("deep"))
        stub.extract = lambda unit, ctx: None
        stub.provenance = lambda config: {}
        mod.load_extractor_module = lambda name: stub
        # A test double stands in for the shipped-module check: the stub is
        # not a file in the plugin's extractors directory.
        mod.resolve_extractor_path = lambda name: Path(str(name))

        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = mod.main(["--dry-run", "--config", str(root / "docs" / "manifest.yml")])
        self.assertEqual(code, 1)
        payload = json.loads(out.getvalue())
        self.assertIn("validation_errors", payload)
        self.assertNotIn("drift", payload)
        causes = " ".join(e["cause"] for e in payload["validation_errors"])
        self.assertIn("RecursionError", causes)
        self.assertNotIn("Traceback", causes)


class CapabilityHookOrderingTests(unittest.TestCase):
    """Contract C8: _check_capabilities runs before output-root admission
    and any read of the output directory."""

    def test_capability_exit_leaves_every_output_byte_unchanged(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs" / "code").mkdir(parents=True)
        (root / "src").mkdir()
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n    any:\n      extractor: fallback\n'
            '      glob: "src/*.py"\n',
            encoding="utf-8",
        )
        (root / "src" / "a.py").write_text("# a\n", encoding="utf-8")
        canary = root / "docs" / "code" / "canary.md"
        canary.write_text("# canary\n", encoding="utf-8")

        mod = _load_dispatcher()
        mod._check_capabilities = lambda selected: (_ for _ in ()).throw(SystemExit(2))
        with self.assertRaises(SystemExit) as ctx:
            mod.main(["--config", str(root / "docs" / "manifest.yml")])
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(canary.read_text(encoding="utf-8"), "# canary\n")
        self.assertFalse((root / "docs" / "code" / "fallback").exists())


class ReviewFindingsTests(unittest.TestCase):
    """Independent-reviewer findings F1, F2, F4, F5, F6 and ADR-0132 item 6
    (F3), PB-0121 dev-1 developer A3. Each malicious metadata row is crafted
    at test time; no hostile fixture is committed."""

    def _tree(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs").mkdir()
        (root / "src_py").mkdir()
        (root / "src_sh").mkdir()
        (root / "src_py" / "one.py").write_text("# one\n", encoding="utf-8")
        (root / "src_sh" / "two.sh").write_text("# two\n", encoding="utf-8")
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n'
            '    py:\n      extractor: fallback\n      glob: "src_py/*.py"\n'
            '    sh:\n      extractor: fallback\n      glob: "src_sh/*.sh"\n',
            encoding="utf-8",
        )
        return root

    def _poison_row(self, root: Path, doc_path: str, owner: str = "py") -> None:
        meta_path = root / "docs" / "code" / "_meta" / "manifest.json"
        m = json.loads(meta_path.read_text(encoding="utf-8"))
        m["pages"].append({
            "owner": owner, "source_path": "src_py/ghost.py", "doc_path": doc_path,
            "sha256": "0" * 64, "extractor_version": "1",
        })
        meta_path.write_text(json.dumps(m), encoding="utf-8")

    def _outside_path(self, name: str) -> Path:
        """R-A3: a fixed-name file outside `root` (e.g. an absolute
        doc_path's target, or a stray symlink's target) belongs in its own
        disposable TemporaryDirectory, never at `root.parent` -- `root`'s
        own TemporaryDirectory.cleanup() never reaches its parent, which is
        the shared OS temp root, so a fixed name written there leaks across
        every test run and every other process using that root (the
        `_outside_file` pattern above, generalized to a caller-chosen name).
        """
        outside_dir = tempfile.TemporaryDirectory()
        self.addCleanup(outside_dir.cleanup)
        return Path(outside_dir.name) / name

    # ---- F1: an absolute or `..` doc_path in a metadata row ----

    def test_f1_absolute_doc_path_row_refuses_lang_write_bytes_unchanged(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        victim = self._outside_path("victim.md")
        victim.write_text("# precious\n", encoding="utf-8")
        self._poison_row(root, str(victim))

        r2 = _run(root, "--config", str(root / "docs" / "manifest.yml"), "--lang", "py")
        self.assertEqual(r2.returncode, 1, r2.stdout)
        self.assertEqual(r2.stdout, "")
        self.assertIn("is absolute", r2.stderr)
        self.assertEqual(victim.read_text(encoding="utf-8"), "# precious\n",
                          "an absolute doc_path in a row must never be unlinked")

    def test_f1_absolute_doc_path_row_refuses_lang_dry_run_as_json(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        victim = self._outside_path("victim.md")
        victim.write_text("# precious\n", encoding="utf-8")
        self._poison_row(root, str(victim))

        r2 = _run(root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"), "--lang", "py")
        self.assertEqual(r2.returncode, 1, r2.stdout)
        payload = json.loads(r2.stdout)
        self.assertIn("validation_errors", payload)
        self.assertNotIn("drift", payload)
        self.assertEqual(victim.read_text(encoding="utf-8"), "# precious\n")

    def test_f1_dotdot_doc_path_row_refuses_lang_write_bytes_unchanged(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        victim = root / "docs" / "victim.md"
        victim.write_text("# precious\n", encoding="utf-8")
        self._poison_row(root, "../victim.md")

        r2 = _run(root, "--config", str(root / "docs" / "manifest.yml"), "--lang", "py")
        self.assertEqual(r2.returncode, 1, r2.stdout)
        self.assertIn("'..' component", r2.stderr)
        self.assertEqual(victim.read_text(encoding="utf-8"), "# precious\n")

    def test_f1_dotdot_doc_path_row_refuses_full_write_bytes_unchanged(self):
        """A malformed row is not tolerated by a full run either -- fail
        closed rather than trust unvalidated metadata in EITHER lane."""
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        victim = root / "docs" / "victim.md"
        victim.write_text("# precious\n", encoding="utf-8")
        self._poison_row(root, "../victim.md")
        before = (root / "docs" / "code" / "fallback" / "src_py" / "one.py.md").read_bytes()

        r2 = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r2.returncode, 1, r2.stdout)
        self.assertIn("'..' component", r2.stderr)
        self.assertEqual(victim.read_text(encoding="utf-8"), "# precious\n")
        self.assertEqual(
            (root / "docs" / "code" / "fallback" / "src_py" / "one.py.md").read_bytes(), before,
        )

    def test_f1_dotdot_doc_path_row_refuses_full_dry_run_as_json(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        victim = root / "docs" / "victim.md"
        victim.write_text("# precious\n", encoding="utf-8")
        self._poison_row(root, "../victim.md")

        r2 = _run(root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r2.returncode, 1, r2.stdout)
        payload = json.loads(r2.stdout)
        self.assertIn("validation_errors", payload)
        self.assertNotIn("drift", payload)
        self.assertEqual(victim.read_text(encoding="utf-8"), "# precious\n")

    # ---- R-A1: a malformed row must refuse, never crash with a traceback ----

    _RA1_SHAPES = [
        ("dot_doc_path", {"doc_path": "."}),
        ("nul_doc_path", {"doc_path": "src_py\x00evil.md"}),
        ("list_owner", {"doc_path": "src_py/ghost.md", "owner": ["py"]}),
    ]

    def _poison_row_overrides(self, root: Path, overrides: dict) -> None:
        meta_path = root / "docs" / "code" / "_meta" / "manifest.json"
        m = json.loads(meta_path.read_text(encoding="utf-8"))
        row = {
            "owner": "py", "source_path": "src_py/ghost.py",
            "doc_path": "src_py/ghost.md", "sha256": "0" * 64,
            "extractor_version": "1",
        }
        row.update(overrides)
        m["pages"].append(row)
        meta_path.write_text(json.dumps(m), encoding="utf-8")

    def test_ra1_malformed_rows_refuse_lang_write_no_traceback(self):
        for label, overrides in self._RA1_SHAPES:
            with self.subTest(label):
                root = self._tree()
                r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
                self.assertEqual(r.returncode, 0, r.stderr)
                self._poison_row_overrides(root, overrides)

                r2 = _run(root, "--config", str(root / "docs" / "manifest.yml"), "--lang", "py")
                self.assertEqual(r2.returncode, 1, r2.stdout)
                self.assertEqual(r2.stdout, "")
                self.assertNotIn("Traceback", r2.stderr)

    def test_ra1_malformed_rows_refuse_lang_dry_run_as_json_no_traceback(self):
        for label, overrides in self._RA1_SHAPES:
            with self.subTest(label):
                root = self._tree()
                r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
                self.assertEqual(r.returncode, 0, r.stderr)
                self._poison_row_overrides(root, overrides)

                r2 = _run(
                    root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"), "--lang", "py"
                )
                self.assertEqual(r2.returncode, 1, r2.stdout)
                self.assertNotIn("Traceback", r2.stderr)
                payload = json.loads(r2.stdout)
                self.assertIn("validation_errors", payload)
                self.assertNotIn("drift", payload)

    def test_ra1_int_owner_beside_a_str_owner_refuses_lang_write_no_traceback(self):
        # A str-owner row already sits ownerless once "sh" is deconfigured;
        # adding an int-owner row alongside it mixes key types under
        # sorted(unconfigured_counts) unless the row validator refuses the
        # int owner first.
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self._deconfigure_sh(root)
        self._poison_row_overrides(root, {"doc_path": "src_py/ghost2.md", "owner": 7})

        r2 = _run(root, "--config", str(root / "docs" / "manifest.yml"), "--lang", "py")
        self.assertEqual(r2.returncode, 1, r2.stdout)
        self.assertEqual(r2.stdout, "")
        self.assertNotIn("Traceback", r2.stderr)

    def test_ra1_int_owner_beside_a_str_owner_refuses_lang_dry_run_as_json_no_traceback(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self._deconfigure_sh(root)
        self._poison_row_overrides(root, {"doc_path": "src_py/ghost2.md", "owner": 7})

        r2 = _run(
            root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"), "--lang", "py"
        )
        self.assertEqual(r2.returncode, 1, r2.stdout)
        self.assertNotIn("Traceback", r2.stderr)
        payload = json.loads(r2.stdout)
        self.assertIn("validation_errors", payload)
        self.assertNotIn("drift", payload)

    # ---- F2: a stray symlink under the output root is not drift ----

    def test_f2_stray_symlink_dry_run_reports_unowned_never_drift_paired_with_write(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        code = root / "docs" / "code"
        outside = self._outside_path("o.md")
        outside.write_text("outside\n", encoding="utf-8")
        try:
            (code / "stray_link.md").symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")
        before = (code / "fallback" / "src_py" / "one.py.md").read_bytes()

        r2 = _run(root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r2.returncode, 0, r2.stdout)
        payload = json.loads(r2.stdout)
        self.assertFalse(payload["drift"])
        self.assertNotIn("stray_link.md", payload["pages"]["unexpected"])
        self.assertIn("stray_link.md", payload["unowned"])

        r3 = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r3.returncode, 0, r3.stderr)
        self.assertEqual((code / "fallback" / "src_py" / "one.py.md").read_bytes(), before,
                          "the paired write changes no byte")
        self.assertTrue((code / "stray_link.md").is_symlink(), "the write lane leaves the link untouched")

        r4 = _run(root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(json.loads(r4.stdout)["drift"], False,
                          "dry-run after the paired write must still report no drift")

    # ---- F3 / ADR-0132 item 6: a row's owner names an unconfigured key ----

    def _deconfigure_sh(self, root: Path) -> None:
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n'
            '    py:\n      extractor: fallback\n      glob: "src_py/*.py"\n',
            encoding="utf-8",
        )

    def test_f3_row_owner_names_unconfigured_key_refuses_lang_write(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self._deconfigure_sh(root)
        code = root / "docs" / "code"
        before = (code / "_meta" / "manifest.json").read_bytes()

        r2 = _run(root, "--config", str(root / "docs" / "manifest.yml"), "--lang", "py")
        self.assertEqual(r2.returncode, 1, r2.stdout)
        self.assertEqual(r2.stdout, "")
        self.assertIn("'sh'", r2.stderr)
        self.assertIn("not configured", r2.stderr)
        self.assertIn("full extract-code-docs", r2.stderr)
        # R-A5 / ADR-0132 item 6: the refusal names the affected row count,
        # not just the unconfigured owner key. Exactly one row (two.sh.md)
        # is owned by "sh" in this fixture.
        self.assertIn("1 row(s)", r2.stderr)
        self.assertEqual((code / "_meta" / "manifest.json").read_bytes(), before)

    def test_f3_row_owner_names_unconfigured_key_refuses_lang_dry_run_as_json(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self._deconfigure_sh(root)

        r2 = _run(root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"), "--lang", "py")
        self.assertEqual(r2.returncode, 1, r2.stdout)
        payload = json.loads(r2.stdout)
        self.assertIn("validation_errors", payload)
        self.assertNotIn("drift", payload)
        causes = " ".join(e["cause"] for e in payload["validation_errors"])
        self.assertIn("'sh'", causes)
        self.assertIn("not configured", causes)
        self.assertIn("1 row(s)", causes)

    def test_f3_full_run_cleans_the_tree_and_a_following_lang_run_proceeds(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self._deconfigure_sh(root)

        r_full = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r_full.returncode, 0, r_full.stderr)
        code = root / "docs" / "code"
        self.assertFalse((code / "fallback" / "src_sh" / "two.sh.md").exists(),
                          "the unconfigured key's page is pruned by a full run")
        manifest = json.loads((code / "_meta" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual({row["owner"] for row in manifest["pages"]}, {"py"})

        r_lang = _run(root, "--config", str(root / "docs" / "manifest.yml"), "--lang", "py")
        self.assertEqual(r_lang.returncode, 0, r_lang.stderr)

    # ---- F4: a preserved page replaced by a symlink must not be followed ----

    def test_f4_preserved_page_symlinked_to_outside_refuses_lang_write(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        code = root / "docs" / "code"
        page = code / "fallback" / "src_sh" / "two.sh.md"
        outside = self._outside_path("o.md")
        outside.write_text("# outside title\nsecret body\n", encoding="utf-8")
        page.unlink()
        try:
            page.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")

        r2 = _run(root, "--config", str(root / "docs" / "manifest.yml"), "--lang", "py")
        self.assertEqual(r2.returncode, 1, r2.stdout)
        self.assertNotIn("outside title", (code / "index.md").read_text(encoding="utf-8"))
        self.assertIn("full extract-code-docs", r2.stderr)
        self.assertTrue(page.is_symlink(), "the symlink itself is left exactly as it was")

    # ---- R-A2: a preserved page that is not UTF-8 must refuse, not crash ----

    def test_ra2_preserved_page_not_utf8_refuses_lang_in_both_modes(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        code = root / "docs" / "code"
        page = code / "fallback" / "src_sh" / "two.sh.md"
        page.write_bytes(b"\xff\xfe not valid utf-8\n")
        before = (code / "_meta" / "manifest.json").read_bytes()

        r_write = _run(root, "--config", str(root / "docs" / "manifest.yml"), "--lang", "py")
        self.assertEqual(r_write.returncode, 1, r_write.stdout)
        self.assertEqual(r_write.stdout, "")
        self.assertNotIn("Traceback", r_write.stderr)
        self.assertIn("cannot read preserved page as UTF-8", r_write.stderr)
        self.assertEqual((code / "_meta" / "manifest.json").read_bytes(), before)

        r_dry = _run(
            root, "--dry-run", "--config", str(root / "docs" / "manifest.yml"), "--lang", "py"
        )
        self.assertEqual(r_dry.returncode, 1, r_dry.stdout)
        self.assertNotIn("Traceback", r_dry.stderr)
        payload = json.loads(r_dry.stdout)
        self.assertIn("validation_errors", payload)
        self.assertNotIn("drift", payload)
        causes = " ".join(e["cause"] for e in payload["validation_errors"])
        self.assertIn("cannot read preserved page as UTF-8", causes)

    # ---- F5: a metadata row missing doc_path must not crash ----

    def test_f5_row_missing_doc_path_refuses_lang_write_no_traceback(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        meta_path = root / "docs" / "code" / "_meta" / "manifest.json"
        meta_path.write_text(json.dumps({"pages": [{"owner": "sh"}], "extractor_version": "1"}),
                              encoding="utf-8")

        r2 = _run(root, "--config", str(root / "docs" / "manifest.yml"), "--lang", "py")
        self.assertEqual(r2.returncode, 1, r2.stdout)
        self.assertNotIn("Traceback", r2.stderr)
        self.assertIn("doc_path", r2.stderr)

    def test_f5_row_missing_doc_path_refuses_full_write_no_traceback(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        meta_path = root / "docs" / "code" / "_meta" / "manifest.json"
        meta_path.write_text(json.dumps({"pages": [{"owner": "sh"}], "extractor_version": "1"}),
                              encoding="utf-8")

        r2 = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r2.returncode, 1, r2.stdout)
        self.assertNotIn("Traceback", r2.stderr)
        self.assertIn("doc_path", r2.stderr)

    # ---- F6: a stray symlink resolving to a KEPT page must still be reported ----

    def test_f6_stray_symlink_to_a_kept_page_is_reported_full_write(self):
        root = self._tree()
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        code = root / "docs" / "code"
        target = code / "fallback" / "src_py" / "one.py.md"
        alias = code / "alias.md"
        try:
            alias.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")

        r2 = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertTrue(alias.is_symlink(), "the link is never deleted")
        self.assertIn("alias.md", r2.stderr)
        self.assertIn("symlink", r2.stderr)


class GapSuffixZeroTests(unittest.TestCase):
    """Review NIT on contract C7: the ` <N> gap(s).` suffix is present
    whenever a selected key is API-2, including N=0 -- never folded back to
    a bare final `.` just because a run found no gaps."""

    def test_api2_key_with_zero_gaps_still_prints_the_suffix(self):
        import contextlib

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs").mkdir()
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'code:\n  extractors:\n    a:\n      extractor: stub_zero_gaps\n',
            encoding="utf-8",
        )
        mod = _load_dispatcher()

        def make_stub():
            m = types.ModuleType("stub_zero_gaps")
            m.EXTRACTOR_API = 2

            def discover(repo_root, config, ctx):
                return [mod.SourceUnit(language=ctx.lang_key, identifier="a", source_path="stub-a.src")]

            def extract(unit, ctx):
                return mod.DocPage(
                    title="a", path="a/Page.md", body="# a\n",
                    source_path=unit.source_path, meta={"gaps": []},
                )

            m.discover = discover
            m.extract = extract
            m.provenance = lambda config: {}
            return m

        stub = make_stub()
        mod.load_extractor_module = lambda name: stub
        # A test double stands in for the shipped-module check: the stub is
        # not a file in the plugin's extractors directory.
        mod.resolve_extractor_path = lambda name: Path(str(name))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = mod.main(["--config", str(root / "docs" / "manifest.yml")])
        self.assertEqual(code, 0)
        self.assertIn("0 gap(s).", out.getvalue())


class ControlCharacterSanitizationTests(unittest.TestCase):
    """Review NIT: a control character in a refusal's `path`/`cause` (e.g.
    an ANSI escape smuggled through a hostile path or exception message)
    must not reach stderr or the JSON payload raw."""

    def test_control_char_in_refusal_is_escaped_on_stderr(self):
        mod = _load_dispatcher()
        refusal = mod.ExtractionRefusal("evil\x1b[31mpath", "bad\x07cause")
        import contextlib

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = mod._report_refusals([refusal], dry_run=False)
        self.assertEqual(code, 1)
        self.assertNotIn("\x1b", err.getvalue())
        self.assertNotIn("\x07", err.getvalue())
        self.assertIn("\\x1b", err.getvalue())
        self.assertIn("\\x07", err.getvalue())

    def test_control_char_in_refusal_is_escaped_in_json_payload(self):
        mod = _load_dispatcher()
        refusal = mod.ExtractionRefusal("evil\x1b[31mpath", "bad\x07cause")
        import contextlib

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = mod._report_refusals([refusal], dry_run=True)
        self.assertEqual(code, 1)
        raw = out.getvalue()
        self.assertNotIn("\x1b", raw)
        self.assertNotIn("\x07", raw)
        payload = json.loads(raw)
        self.assertIn("\\x1b", payload["validation_errors"][0]["path"])

    def test_ra4_newline_in_refusal_cannot_forge_a_second_stderr_line(self):
        mod = _load_dispatcher()
        # A metadata doc_path carrying "\n" followed by text shaped like the
        # reporter's own prefix would, unescaped, render as a second line
        # indistinguishable from a genuine second refusal.
        refusal = mod.ExtractionRefusal(
            "real/path", "forged\nextract-code-docs: fake: attack"
        )
        import contextlib

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = mod._report_refusals([refusal], dry_run=False)
        self.assertEqual(code, 1)
        raw = err.getvalue()
        self.assertNotIn("\n", raw.rstrip("\n"), "one refusal must render as one line")
        lines = [ln for ln in raw.splitlines() if ln]
        self.assertEqual(len(lines), 1)

    def test_ra4_carriage_return_and_c1_control_are_escaped(self):
        mod = _load_dispatcher()
        refusal = mod.ExtractionRefusal("evil\rpath", "bad\x9bcause")
        import contextlib

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = mod._report_refusals([refusal], dry_run=False)
        self.assertEqual(code, 1)
        raw = err.getvalue()
        self.assertNotIn("\r", raw)
        self.assertNotIn("\x9b", raw)
        self.assertIn("\\x0d", raw)
        self.assertIn("\\x9b", raw)


def _elixir_and_fallback_tree(test: unittest.TestCase) -> Path:
    """Two configured keys selecting the same source file: `ex` (elixir,
    page `elixir/App/Foo.md`) and `fb` (fallback, page `fallback/lib/foo.ex.md`)."""
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    root = Path(tmp.name)
    (root / "docs").mkdir()
    (root / "lib").mkdir()
    (root / "docs" / "manifest.yml").write_text(
        'schema_version: "5"\n'
        "code:\n  extractors:\n"
        '    ex:\n      extractor: elixir\n      glob: "lib/*.ex"\n'
        '    fb:\n      extractor: fallback\n      glob: "lib/*.ex"\n',
        encoding="utf-8",
    )
    (root / "lib" / "foo.ex").write_text(
        'defmodule App.Foo do\n  @moduledoc "Foo."\n  def a, do: 1\nend\n', encoding="utf-8"
    )
    return root


class DamagedMetadataManifestTests(unittest.TestCase):
    """PB-0121 fix round 1, finding M3: a `_meta/manifest.json` that parses
    but is not an object, or whose `pages` is not a list, refuses through
    the clause-15 lane in both lanes and leaves the tree byte-unchanged."""

    def _assert_refuses(self, meta_text: str, cause_fragment: str) -> None:
        root = _elixir_and_fallback_tree(self)
        config = str(root / "docs" / "manifest.yml")
        first = _run(root, "--config", config)
        self.assertEqual(first.returncode, 0, first.stderr)
        meta = root / "docs" / "code" / "_meta" / "manifest.json"
        meta.write_text(meta_text, encoding="utf-8")
        before = _fingerprint(root)
        for extra in ((), ("--dry-run",)):
            with self.subTest(lane=extra or "write"):
                r = _run(root, *extra, "--config", config)
                self.assertEqual(r.returncode, 1, (r.stdout, r.stderr))
                self.assertNotIn("Traceback", r.stderr)
                if extra:
                    self.assertEqual(r.stderr, "")
                    payload = json.loads(r.stdout)
                    self.assertNotIn("drift", payload)
                    causes = " ".join(e["cause"] for e in payload["validation_errors"])
                else:
                    self.assertEqual(r.stdout, "")
                    causes = r.stderr
                self.assertIn(cause_fragment, causes)
                self.assertEqual(_fingerprint(root), before)

    def test_null_manifest_refuses(self):
        self._assert_refuses("null\n", "is not an object")

    def test_list_manifest_refuses(self):
        self._assert_refuses("[]\n", "is not an object")

    def test_non_list_pages_refuses(self):
        self._assert_refuses('{"pages": {"a": 1}}\n', "pages is not a list")

    def test_non_object_extractors_refuses(self):
        self._assert_refuses('{"pages": [], "extractors": [1]}\n', "extractors is not an object")

    def test_positive_control_an_intact_manifest_reruns_clean(self):
        root = _elixir_and_fallback_tree(self)
        config = str(root / "docs" / "manifest.yml")
        self.assertEqual(_run(root, "--config", config).returncode, 0)
        r = _run(root, "--dry-run", "--config", config)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse(json.loads(r.stdout)["drift"])


class SharedSourceRowOrderTests(unittest.TestCase):
    """PB-0121 fix round 1, finding S2: when two owners share a source_path
    the --lang lane and the full lane order metadata rows the same way, so
    a full --dry-run after a --lang run reports no metadata drift."""

    def test_lang_run_then_full_dry_run_reports_no_drift(self):
        root = _elixir_and_fallback_tree(self)
        config = str(root / "docs" / "manifest.yml")
        self.assertEqual(_run(root, "--config", config).returncode, 0)
        lang = _run(root, "--config", config, "--lang", "fb")
        self.assertEqual(lang.returncode, 0, lang.stderr)
        r = _run(root, "--dry-run", "--config", config)
        payload = json.loads(r.stdout)
        self.assertFalse(payload["metadata_drift"], payload)
        self.assertFalse(payload["drift"], payload)
        self.assertEqual(r.returncode, 0)

    def test_positive_control_a_reordered_manifest_is_metadata_drift(self):
        root = _elixir_and_fallback_tree(self)
        config = str(root / "docs" / "manifest.yml")
        self.assertEqual(_run(root, "--config", config).returncode, 0)
        meta = root / "docs" / "code" / "_meta" / "manifest.json"
        data = json.loads(meta.read_text(encoding="utf-8"))
        self.assertEqual(len(data["pages"]), 2)
        data["pages"].reverse()
        meta.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        payload = json.loads(_run(root, "--dry-run", "--config", config).stdout)
        self.assertTrue(payload["metadata_drift"], payload)


class HelpTextCitesNoDecisionNumberTests(unittest.TestCase):
    """The --lang and --dry-run help text states the behaviour without an
    ADR number (writing rule 7). The positive control seeds its own parser,
    so it does not depend on any live option's help text."""

    _SEEDED_HELP = "Seeded control help, per ADR-0032."

    def _captured_option_help(self, build_and_parse) -> dict[str, str]:
        """Each option's help string, read from the one parser that
        `build_and_parse` hands to `parse_args`."""
        captured: list[argparse.ArgumentParser] = []

        def _capture(parser, *_args, **_kwargs):
            captured.append(parser)
            return argparse.Namespace()

        with mock.patch.object(argparse.ArgumentParser, "parse_args", _capture):
            build_and_parse()
        self.assertEqual(len(captured), 1)
        return {opt: action.help or "" for action in captured[0]._actions
                for opt in action.option_strings}

    def _option_help(self) -> dict[str, str]:
        """Each option's help string, read from the parser `parse_args` builds."""
        return self._captured_option_help(lambda: dispatcher.parse_args([]))

    def test_lang_and_dry_run_help_name_no_adr(self):
        helps = self._option_help()
        for option in ("--lang", "--dry-run"):
            with self.subTest(option=option):
                self.assertTrue(helps[option])
                self.assertIsNone(re.search(r"ADR-\d{4}", helps[option]), helps[option])
        self.assertIn("every other owner's bytes are preserved", " ".join(helps["--lang"].split()))
        self.assertIn("Writes nothing", helps["--dry-run"])

    def test_positive_control_the_scan_sees_a_seeded_adr_number(self):
        def _build_and_parse():
            parser = argparse.ArgumentParser(prog="seeded-control")
            parser.add_argument("--seeded", help=self._SEEDED_HELP)
            parser.parse_args([])

        helps = self._captured_option_help(_build_and_parse)
        self.assertIsNotNone(re.search(r"ADR-\d{4}", helps["--seeded"]), helps["--seeded"])


class PackageBelowRepoRootTests(unittest.TestCase):
    """PB-0121 fix round 1, finding M1, through the real CLI: an export in a
    package that sits below the repository root (`src/pkg/`) resolves inside
    the selection, and a missing target there is still a named gap."""

    def _run_src_layout(self, init_text: str) -> tuple[subprocess.CompletedProcess, Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        pkg = root / "src" / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(init_text, encoding="utf-8")
        (pkg / "_impl.py").write_text("def f():\n    pass\n\n\nclass Thing:\n    pass\n",
                                      encoding="utf-8")
        (root / "docs").mkdir()
        (root / "docs" / "manifest.yml").write_text(
            'schema_version: "5"\ncode:\n  extractors:\n    python:\n'
            '      extractor: python\n      glob: ["src/**/*.py"]\n',
            encoding="utf-8",
        )
        r = _run(root, "--config", str(root / "docs" / "manifest.yml"))
        return r, root / "docs" / "code" / "python" / "src" / "pkg" / "__init__.py.md"

    def test_exports_in_a_src_layout_package_resolve(self):
        r, page = self._run_src_layout(
            '__all__ = ["Thing"]\nfrom ._impl import f as f\nfrom ._impl import Thing\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("0 gap(s)", r.stdout)
        body = page.read_text(encoding="utf-8")
        self.assertNotIn("does not resolve", body)
        self.assertIn("## `f`", body)
        self.assertIn("## `Thing`", body)

    def test_positive_control_a_missing_target_below_the_root_is_a_gap(self):
        r, page = self._run_src_layout("from ._impl import gone as gone\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("`gone` (line 1) is exported but does not resolve",
                      page.read_text(encoding="utf-8"))

    def test_an_absolute_re_export_by_installable_name_resolves(self):
        # Fix round 2: `pkg` is the installable name of src/pkg/.
        r, page = self._run_src_layout(
            '__all__ = ["Thing"]\nfrom pkg._impl import f as f\nfrom pkg._impl import Thing\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("0 gap(s)", r.stdout)
        body = page.read_text(encoding="utf-8")
        self.assertNotIn("does not resolve", body)
        self.assertIn("```python\nfrom pkg._impl import f as f\n```", body)
        self.assertIn("## `Thing`", body)

    def test_positive_control_an_absolute_re_export_of_a_missing_module_is_a_gap(self):
        r, page = self._run_src_layout("from pkg.gone import f as f\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("`f` (line 1) is exported but does not resolve",
                      page.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
