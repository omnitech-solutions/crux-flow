"""Dispatcher refusals and stderr hygiene for extract-code-docs: metadata rows
without a hash, duplicate doc paths, sanitized progress lines, the source size
bound, mid-write failures, a symlinked metadata manifest, an unknown --lang
key, an unreadable fallback source, and an unreadable preserved page.

Stdlib only. Every disposable tree is built under tempfile.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # crux repo
SCRIPT_PATH = REPO_ROOT / "crux" / "scripts" / "extract-code-docs.py"


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
        cwd=str(cwd), capture_output=True, text=True, check=False,
    )


def _write_manifest(root: Path, extractors_yaml: str) -> Path:
    (root / "docs").mkdir(parents=True, exist_ok=True)
    cfg = root / "docs" / "manifest.yml"
    cfg.write_text(
        'schema_version: "5"\ncode:\n  extractors:\n' + extractors_yaml,
        encoding="utf-8",
    )
    return cfg


class MetadataRowMissingSha256Tests(unittest.TestCase):
    """A `_meta/manifest.json` row lacking `sha256` refuses through the
    validation-error report, before any write, instead
    of raising a `KeyError` out of `diff_manifests`."""

    def _tree(self) -> tuple[tempfile.TemporaryDirectory, Path, Path]:
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "lib").mkdir(parents=True)
        (root / "lib" / "foo.ex").write_text("defmodule Foo do\nend\n", encoding="utf-8")
        cfg = _write_manifest(
            root,
            '    elixir:\n      extractor: elixir\n      glob: ["lib/**/*.ex"]\n',
        )
        return tmp, root, cfg

    def _corrupt_meta_without_sha256(self, root: Path) -> Path:
        meta = root / "docs" / "code" / "_meta" / "manifest.json"
        data = json.loads(meta.read_text(encoding="utf-8"))
        for row in data["pages"]:
            del row["sha256"]
        meta.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return meta

    def test_missing_sha256_refuses_before_write_not_a_traceback(self):
        tmp, root, cfg = self._tree()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(_run(root, "--config", str(cfg)).returncode, 0)
        meta = self._corrupt_meta_without_sha256(root)
        before = meta.read_bytes()
        r = _run(root, "--config", str(cfg))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("sha256", r.stderr)
        self.assertEqual(meta.read_bytes(), before, "the existing metadata must be byte-unchanged")

    def test_missing_sha256_refuses_under_dry_run_too(self):
        tmp, root, cfg = self._tree()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(_run(root, "--config", str(cfg)).returncode, 0)
        self._corrupt_meta_without_sha256(root)
        r = _run(root, "--dry-run", "--config", str(cfg))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        payload = json.loads(r.stdout)
        self.assertIn("validation_errors", payload)
        self.assertTrue(
            any("sha256" in e["cause"] for e in payload["validation_errors"]), payload
        )

    def test_positive_control_a_row_with_sha256_is_admitted(self):
        # Proves the new check is not vacuous: a well-formed row with a
        # string sha256 passes `_validate_metadata_row` clean.
        row = {"doc_path": "a/b.md", "source_path": "a/b.ex", "sha256": "deadbeef"}
        self.assertIsNone(dispatcher._validate_metadata_row(row))

    def test_a_non_string_sha256_also_refuses(self):
        row = {"doc_path": "a/b.md", "source_path": "a/b.ex", "sha256": 123}
        refusal = dispatcher._validate_metadata_row(row)
        self.assertIsNotNone(refusal)
        self.assertIn("sha256", refusal.cause)


class SameKeyDocPathCollisionTests(unittest.TestCase):
    """Two outputs claiming one doc_path refuse before any write,
    whatever their key -- including the same key (rule
    code-doc-path-collision-refuses-before-write)."""

    def test_two_elixir_modules_of_the_same_name_refuse_before_write(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "lib" / "a").mkdir(parents=True)
        (root / "lib" / "b").mkdir(parents=True)
        (root / "lib" / "a" / "foo.ex").write_text("defmodule Foo do\nend\n", encoding="utf-8")
        (root / "lib" / "b" / "foo.ex").write_text("defmodule Foo do\nend\n", encoding="utf-8")
        cfg = _write_manifest(
            root,
            '    elixir:\n      extractor: elixir\n      glob: ["lib/**/*.ex"]\n',
        )
        code_dir = root / "docs" / "code"
        r = _run(root, "--config", str(cfg))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(code_dir.exists(), "no output must be written on a same-key collision")

    def test_positive_control_two_distinct_module_names_do_not_collide(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "lib").mkdir(parents=True)
        (root / "lib" / "foo.ex").write_text("defmodule Foo do\nend\n", encoding="utf-8")
        (root / "lib" / "bar.ex").write_text("defmodule Bar do\nend\n", encoding="utf-8")
        cfg = _write_manifest(
            root,
            '    elixir:\n      extractor: elixir\n      glob: ["lib/**/*.ex"]\n',
        )
        r = _run(root, "--config", str(cfg))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_legacy_fixture_has_no_collision_and_stays_byte_identical(self):
        import shutil

        fixture_root = Path(__file__).resolve().parent / "fixtures" / "code_docs_legacy"
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = Path(tmp.name) / "tree"
        shutil.copytree(fixture_root, work)
        code_dir = work / "docs" / "code"
        before = {
            str(p.relative_to(code_dir)): p.read_bytes()
            for p in sorted(code_dir.rglob("*")) if p.is_file()
        }
        cfg = work / "docs" / "manifest.yml"
        r = _run(work, "--config", str(cfg))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        after = {
            str(p.relative_to(code_dir)): p.read_bytes()
            for p in sorted(code_dir.rglob("*")) if p.is_file()
        }
        # Every page and the index keep their exact bytes. The metadata file
        # is excluded: a rerun adds each row's `owner` field.
        self.assertEqual(set(after), set(before))
        for rel, content in before.items():
            if rel == "_meta/manifest.json":
                continue
            with self.subTest(page=rel):
                self.assertEqual(after[rel], content)


class StderrSanitationTests(unittest.TestCase):
    """Progress and report lines carrying a path or name go through
    `_sanitize_refusal_text` (or a shared sanitizer), so a hostile filename
    cannot forge a second stderr line."""

    def test_verbose_write_line_carries_no_raw_esc_or_lf_from_the_name(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        hostile = "a\x1bBAD\x0aextract-code-docs: forged.py"
        (root / "scripts").mkdir(parents=True)
        (root / "scripts" / f"{hostile}.py").write_text("# header\n", encoding="utf-8")
        cfg = _write_manifest(
            root,
            '    fb:\n      extractor: fallback\n      glob: ["scripts/**/*.py"]\n',
        )
        r = _run(root, "--verbose", "--config", str(cfg))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        raw_esc = "\x1b"
        raw_lf_marker = "\x1bBAD\nextract-code-docs: forged"
        self.assertNotIn(raw_esc, r.stderr)
        self.assertNotIn(raw_lf_marker, r.stderr)
        # The sanitized escape form IS expected to appear.
        self.assertIn("\\x1b", r.stderr)

    def test_verbose_root_line_carries_no_raw_esc_from_the_repo_path(self):
        """The partner half: an operator-side path (the repository directory
        itself) passes through the same sanitizer as a target-derived name."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "repo\x1b[31mX"
        (root / "scripts").mkdir(parents=True)
        (root / "scripts" / "a.py").write_text("# header\n", encoding="utf-8")
        cfg = _write_manifest(
            root,
            '    fb:\n      extractor: fallback\n      glob: ["scripts/**/*.py"]\n',
        )
        r = _run(root, "--verbose", "--config", str(cfg))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("repo_root=", r.stderr)
        self.assertNotIn("\x1b", r.stderr)
        self.assertIn("\\x1b", r.stderr)

    def test_prune_outside_refusal_line_carries_no_raw_esc_from_the_output_path(self):
        """The prune refusal for a page that resolves outside the output tree
        names the resolved output directory through the same sanitizer. The
        outside verdict is forced, because reaching it needs a symlinked
        ancestor that the prune walk does not descend on this interpreter."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        output_dir = Path(tmp.name) / "code\x1b[31mX"
        output_dir.mkdir()
        (output_dir / "stray.md").write_text("# stray\n", encoding="utf-8")
        err = StringIO()
        with mock.patch.object(dispatcher, "_is_inside", return_value=False), \
                redirect_stderr(err):
            dispatcher.write_pages([], output_dir, verbose=False)
        line = next(ln for ln in err.getvalue().splitlines() if "resolves outside" in ln)
        self.assertNotIn("\x1b", line)
        self.assertIn("\\x1b", line.split("resolves outside", 1)[1])
        self.assertTrue((output_dir / "stray.md").is_file(), "a refused prune deletes nothing")


class ReadSourceBytesSizeBoundTests(unittest.TestCase):
    """`read_source_bytes` refuses a source over its `max_bytes` bound
    using the fstat it already took, before reading the file in full --
    a caller opting in (the Python extractor path) passes the bound; the
    default stays None, so an elixir/fallback caller passing nothing sees
    no new refusal."""

    MAX = 2 * 1024 * 1024

    def test_oversize_file_refuses_without_a_full_read(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        big = root / "big.bin"
        big.write_bytes(b"\0" * (self.MAX + 1))
        resolved = dispatcher.resolve_source(root, Path("big.bin"))
        with self.assertRaises(dispatcher.ExtractionRefusal) as ctx:
            dispatcher.read_source_bytes(root, resolved, max_bytes=self.MAX)
        self.assertIn(str(self.MAX), ctx.exception.cause)
        self.assertIn("bound", ctx.exception.cause)

    def test_positive_control_a_file_at_the_bound_is_read(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        ok = root / "ok.bin"
        ok.write_bytes(b"\0" * self.MAX)
        resolved = dispatcher.resolve_source(root, Path("ok.bin"))
        data = dispatcher.read_source_bytes(root, resolved, max_bytes=self.MAX)
        self.assertEqual(len(data), self.MAX)

    def test_no_bound_given_reads_an_oversize_file_unchanged(self):
        # The elixir/fallback call shape: no `max_bytes` at all. Behavior
        # must be exactly what it was before this fix.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        big = root / "big.bin"
        big.write_bytes(b"\0" * (self.MAX + 1))
        resolved = dispatcher.resolve_source(root, Path("big.bin"))
        data = dispatcher.read_source_bytes(root, resolved)
        self.assertEqual(len(data), self.MAX + 1)


class MidWriteOSErrorTests(unittest.TestCase):
    """An OSError raised mid-write (a read-only destination
    subdirectory) is reported as a clean refusal, never a traceback."""

    @unittest.skipIf(os.name != "posix", "POSIX permission bits only")
    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root bypasses permission bits")
    def test_a_readonly_output_subdir_refuses_cleanly(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "lib").mkdir(parents=True)
        (root / "lib" / "foo.ex").write_text("defmodule Foo do\nend\n", encoding="utf-8")
        cfg = _write_manifest(
            root,
            '    elixir:\n      extractor: elixir\n      glob: ["lib/**/*.ex"]\n',
        )
        code_dir = root / "docs" / "code"
        code_dir.mkdir(parents=True)
        # Pre-create the output subdir the elixir page lands in
        # (docs/code/elixir/Foo.md), then strip write permission on it.
        victim_dir = code_dir / "elixir"
        victim_dir.mkdir(parents=True)
        os.chmod(victim_dir, 0o555)
        self.addCleanup(lambda: os.chmod(victim_dir, 0o755))
        r = _run(root, "--config", str(cfg))
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("errno", r.stderr)
        self.assertIn("partially written", r.stderr)
        self.assertIn("rerun", r.stderr)


class ManifestSymlinkNotFollowedTests(unittest.TestCase):
    """`read_existing_manifest` never follows a symlinked (or otherwise
    non-regular) `_meta/manifest.json` -- it refuses the same way the
    pre-pass refuses any other unsafe destination."""

    def test_symlinked_manifest_json_is_not_followed(self):
        parent = tempfile.TemporaryDirectory()
        self.addCleanup(parent.cleanup)
        root = Path(parent.name) / "repo"
        root.mkdir()
        outside = Path(parent.name) / "outside-secret.json"
        outside.write_text(json.dumps({"pages": [{"doc_path": "x", "source_path": "y",
                                                   "sha256": "z"}]}), encoding="utf-8")
        meta_dir = root / "docs" / "code" / "_meta"
        meta_dir.mkdir(parents=True)
        link = meta_dir / "manifest.json"
        link.symlink_to(outside)
        with self.assertRaises(dispatcher.ExtractionRefusal) as ctx:
            dispatcher.read_existing_manifest(link)
        self.assertIn("not a regular file", ctx.exception.cause)

    def test_link_swapped_in_after_the_lstat_check_is_not_followed(self):
        # Simulates the race: the lstat check sees a regular file, and a link
        # replaces it before the read. The read itself must refuse the link.
        parent = tempfile.TemporaryDirectory()
        self.addCleanup(parent.cleanup)
        outside = Path(parent.name) / "outside-secret.json"
        outside.write_text(json.dumps({"pages": [{"doc_path": "leaked", "source_path": "y",
                                                   "sha256": "z"}]}), encoding="utf-8")
        meta_dir = Path(parent.name) / "docs" / "code" / "_meta"
        meta_dir.mkdir(parents=True)
        link = meta_dir / "manifest.json"
        link.symlink_to(outside)
        regular_stat = outside.lstat()
        real_lstat = Path.lstat

        def fake_lstat(self, *args, **kwargs):
            if self == link:
                return regular_stat
            return real_lstat(self, *args, **kwargs)

        with mock.patch.object(Path, "lstat", fake_lstat):
            with self.assertRaises(dispatcher.ExtractionRefusal) as ctx:
                dispatcher.read_existing_manifest(link)
        self.assertIn("not a regular file", ctx.exception.cause)

    def test_cli_refuses_a_symlinked_manifest_before_any_write(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "lib").mkdir(parents=True)
        (root / "lib" / "foo.ex").write_text("defmodule Foo do\nend\n", encoding="utf-8")
        cfg = _write_manifest(root, '    elixir:\n      extractor: elixir\n      glob: ["lib/**/*.ex"]\n')
        outside = root / "outside.json"
        outside.write_text('{"pages": []}', encoding="utf-8")
        meta_dir = root / "docs" / "code" / "_meta"
        meta_dir.mkdir(parents=True)
        (meta_dir / "manifest.json").symlink_to(outside)
        r = _run(root, "--config", str(cfg))
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("not a regular file", r.stderr)
        self.assertFalse((root / "docs" / "code" / "elixir").exists())
        self.assertEqual(outside.read_text(encoding="utf-8"), '{"pages": []}')

    def test_positive_control_a_regular_manifest_json_is_read(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        meta_dir = root / "docs" / "code" / "_meta"
        meta_dir.mkdir(parents=True)
        meta = meta_dir / "manifest.json"
        meta.write_text(json.dumps({"pages": [{"doc_path": "x", "source_path": "y",
                                                "sha256": "z"}]}), encoding="utf-8")
        result = dispatcher.read_existing_manifest(meta)
        self.assertEqual(result["pages"][0]["doc_path"], "x")


class LangNotFoundListsConfiguredKeysTests(unittest.TestCase):
    """`--lang X not found` names the configured keys, so the failure
    is actionable rather than a bare rejection."""

    def test_lang_not_found_lists_configured_keys(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "lib").mkdir(parents=True)
        (root / "lib" / "foo.ex").write_text("defmodule Foo do\nend\n", encoding="utf-8")
        cfg = _write_manifest(
            root,
            '    elixir:\n      extractor: elixir\n      glob: ["lib/**/*.ex"]\n'
            '    fb:\n      extractor: fallback\n      glob: ["lib/**/*.ex"]\n',
        )
        r = _run(root, "--config", str(cfg), "--lang", "nope")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("nope", r.stderr)
        self.assertIn("elixir", r.stderr)
        self.assertIn("fb", r.stderr)


class PreservedPageUnreadableTests(unittest.TestCase):
    """A --lang run that must carry another owner's page forward refuses
    cleanly, in both modes and before any write, when that page is a regular
    file it cannot open. The missing, untitled, non-UTF-8 and symlinked
    preserved-page lanes are covered in test_extract_code_docs_repairs.py."""

    def _tree(self) -> tuple[Path, Path, Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "src_py").mkdir()
        (root / "src_sh").mkdir()
        (root / "src_py" / "one.py").write_text("# one\n", encoding="utf-8")
        (root / "src_sh" / "two.sh").write_text("# two\n", encoding="utf-8")
        cfg = _write_manifest(
            root,
            '    py:\n      extractor: fallback\n      glob: "src_py/*.py"\n'
            '    sh:\n      extractor: fallback\n      glob: "src_sh/*.sh"\n',
        )
        r = _run(root, "--config", str(cfg))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        code = root / "docs" / "code"
        return root, cfg, code

    @unittest.skipIf(os.name != "posix", "POSIX permission bits only")
    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root bypasses permission bits")
    def test_unreadable_preserved_page_refuses_in_both_modes_before_any_write(self):
        root, cfg, code = self._tree()
        page = code / "fallback" / "src_sh" / "two.sh.md"
        meta_before = (code / "_meta" / "manifest.json").read_bytes()
        os.chmod(page, 0o000)
        self.addCleanup(lambda: os.chmod(page, 0o644))

        r_write = _run(root, "--config", str(cfg), "--lang", "py")
        self.assertEqual(r_write.returncode, 1, r_write.stdout + r_write.stderr)
        self.assertEqual(r_write.stdout, "")
        self.assertNotIn("Traceback", r_write.stderr)
        self.assertIn("cannot read preserved page (", r_write.stderr)
        self.assertEqual((code / "_meta" / "manifest.json").read_bytes(), meta_before)

        r_dry = _run(root, "--dry-run", "--config", str(cfg), "--lang", "py")
        self.assertEqual(r_dry.returncode, 1, r_dry.stdout + r_dry.stderr)
        self.assertNotIn("Traceback", r_dry.stderr)
        payload = json.loads(r_dry.stdout)
        self.assertNotIn("drift", payload)
        causes = " ".join(e["cause"] for e in payload["validation_errors"])
        self.assertIn("cannot read preserved page (", causes)

    def test_positive_control_a_readable_preserved_page_is_carried_forward(self):
        root, cfg, code = self._tree()
        page = code / "fallback" / "src_sh" / "two.sh.md"
        before = page.read_bytes()
        r = _run(root, "--config", str(cfg), "--lang", "py")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(page.read_bytes(), before)


class FallbackExtractorEACCESTests(unittest.TestCase):
    """The fallback extractor reading an unreadable file refuses
    cleanly through `ExtractionRefusal`, never a traceback."""

    @unittest.skipIf(os.name != "posix", "POSIX permission bits only")
    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root bypasses permission bits")
    def test_unreadable_source_refuses_cleanly(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "scripts").mkdir(parents=True)
        victim = root / "scripts" / "victim.py"
        victim.write_text("# header\n", encoding="utf-8")
        os.chmod(victim, 0o000)
        self.addCleanup(lambda: os.chmod(victim, 0o644))
        cfg = _write_manifest(
            root,
            '    fb:\n      extractor: fallback\n      glob: ["scripts/**/*.py"]\n',
        )
        r = _run(root, "--config", str(cfg))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("victim.py", r.stderr)

    def test_positive_control_a_readable_source_is_extracted(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "scripts").mkdir(parents=True)
        (root / "scripts" / "ok.py").write_text("# header\n", encoding="utf-8")
        cfg = _write_manifest(
            root,
            '    fb:\n      extractor: fallback\n      glob: ["scripts/**/*.py"]\n',
        )
        r = _run(root, "--config", str(cfg))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
