"""An output root that holds Markdown must prove the dispatcher generated it.

PB-0121 Prompt 11 fix round 1, Part B, finding F2. A link `<docs_dir>/code ->
adrs`, or an explicit `--output-dir <docs_dir>/adrs`, is admitted by the marker
check (the root's parent carries a manifest). Before this rule a full run then
pruned every ADR it did not write and exited 0. The rule[^owned]: before any
directory creation, write or prune, a root holding a regular `*.md` file refuses
unless its `_meta/manifest.json` is a regular file inside it, reached without
following a link, that passes the metadata shape check.

Stdlib only. Every disposable tree is built under tempfile and driven through
the real entry point in a subprocess.

[^owned]: rule:code-doc-output-root-pruned-only-when-owned
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # crux repo
SCRIPT_PATH = REPO_ROOT / "crux" / "scripts" / "extract-code-docs.py"

_EXTRACTORS = (
    '    fb:\n      extractor: fallback\n      glob: ["src/*.py"]\n'
    '    fc:\n      extractor: fallback\n      glob: ["lib/*.py"]\n'
)

# The advice the refusal must never give: an override flag, hand-creating the
# ownership metadata, or deleting the files it protects.
_FORBIDDEN_ADVICE = ("--force", "force", "by hand", "create _meta", "rm ", "remove the", "delete the", "delete them")


def _run(cwd: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *argv],
        cwd=str(cwd), capture_output=True, text=True, check=False,
    )


def _make_tree(root: Path, docs: str = "docs") -> Path:
    """A crux tree with sources and three hand-written concern directories."""
    d = root / docs
    d.mkdir(parents=True, exist_ok=True)
    cfg = d / "manifest.yml"
    cfg.write_text('schema_version: "5"\ncode:\n  extractors:\n' + _EXTRACTORS, encoding="utf-8")
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "alpha.py").write_text("# Alpha module.\nx = 1\n", encoding="utf-8")
    (root / "lib").mkdir(exist_ok=True)
    (root / "lib" / "beta.py").write_text("# Beta module.\ny = 2\n", encoding="utf-8")
    (d / "adrs").mkdir(exist_ok=True)
    (d / "adrs" / "ADR-0001-first.md").write_text("# ADR-0001\n\nKeep me.\n", encoding="utf-8")
    (d / "adrs" / "ADR-0002-second.md").write_text("# ADR-0002\n\nKeep me too.\n", encoding="utf-8")
    (d / "adrs" / "index.md").write_text("# ADRs\n", encoding="utf-8")
    (d / "research").mkdir(exist_ok=True)
    (d / "research" / "topic.md").write_text("# Research\n", encoding="utf-8")
    (d / "journal").mkdir(exist_ok=True)
    (d / "journal" / "2026-09.md").write_text("# Journal\n", encoding="utf-8")
    return cfg


def _snapshot(directory: Path) -> dict[str, tuple[str, bytes | str]]:
    """Every entry below `directory`, by relative path, without following a link."""
    out: dict[str, tuple[str, bytes | str]] = {}
    for dirpath, dirnames, filenames in os.walk(directory, followlinks=False):
        for name in sorted(dirnames + filenames):
            p = Path(dirpath) / name
            rel = str(p.relative_to(directory))
            if p.is_symlink():
                out[rel] = ("link", os.readlink(p))
            elif p.is_dir():
                out[rel] = ("dir", "")
            else:
                out[rel] = ("file", p.read_bytes())
    return out


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # Resolve so macOS /var -> /private/var never makes a path differ.
        self.root = Path(os.path.realpath(self._tmp.name))
        self.cfg = _make_tree(self.root)
        self.docs = self.root / "docs"

    def assert_refused(self, target: Path, *argv: str, lang: bool = False) -> None:
        """Both lanes, full and filtered, twice: exit 1, the target byte-unchanged,
        no `_meta` created, the documented channel carrying the refusal."""
        before = _snapshot(target)
        for filtered in ((False, True) if not lang else (True,)):
            extra = ("--lang", "fb") if filtered else ()
            for attempt in (1, 2):  # a rerun still refuses
                with self.subTest(filtered=filtered, attempt=attempt, lane="write"):
                    r = _run(self.root, "--config", str(self.cfg), *argv, *extra)
                    self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                    self.assertEqual(r.stdout, "", "a write-mode refusal prints nothing on stdout")
                    self.assertIn("nothing was written or deleted", r.stderr.lower(), r.stderr)
                    self.assertNotIn("Traceback", r.stderr)
                    self.assertEqual(_snapshot(target), before, "the target must be byte-unchanged")
                    self.assertFalse((target / "_meta").exists() and "_meta" not in before)
                with self.subTest(filtered=filtered, attempt=attempt, lane="dry-run"):
                    r = _run(self.root, "--dry-run", "--config", str(self.cfg), *argv, *extra)
                    self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                    payload = json.loads(r.stdout)
                    self.assertIn("validation_errors", payload)
                    self.assertNotIn("drift", payload)
                    causes = " ".join(e["cause"] for e in payload["validation_errors"]).lower()
                    self.assertIn("nothing was written or deleted", causes)
                    self.assertEqual(_snapshot(target), before)


class UnownedRootRefusesTests(_Base):
    def test_relative_code_link_to_adrs_refuses(self):
        os.symlink("adrs", self.docs / "code")
        self.assert_refused(self.docs / "adrs")

    def test_output_dir_at_a_concern_directory_refuses(self):
        for concern in ("adrs", "research", "journal"):
            with self.subTest(concern=concern):
                self.assert_refused(
                    self.docs / concern, "--output-dir", str(self.docs / concern)
                )

    def test_relative_output_dir_spelling_refuses(self):
        self.assert_refused(self.docs / "adrs", "--output-dir", "docs/adrs")

    def test_root_whose_only_markdown_sits_in_a_subdirectory_refuses(self):
        notes = self.root / "docs" / "notes"
        (notes / "deep").mkdir(parents=True)
        (notes / "deep" / "page.md").write_text("# deep\n", encoding="utf-8")
        self.assert_refused(notes, "--output-dir", str(notes))

    def test_second_tree_concern_directory_refuses(self):
        foreign = self.root / "foreign"
        (foreign / "docs" / "adrs").mkdir(parents=True)
        (foreign / "docs" / "manifest.yml").write_text('schema_version: "5"\n', encoding="utf-8")
        (foreign / "docs" / "adrs" / "ADR-0009-theirs.md").write_text("# theirs\n", encoding="utf-8")
        self.assert_refused(foreign / "docs" / "adrs", "--output-dir", str(foreign / "docs" / "adrs"))

    def test_second_tree_link_to_adrs_refuses(self):
        foreign = self.root / "foreign"
        (foreign / "docs" / "adrs").mkdir(parents=True)
        (foreign / "docs" / "manifest.yml").write_text('schema_version: "5"\n', encoding="utf-8")
        (foreign / "docs" / "adrs" / "ADR-0009-theirs.md").write_text("# theirs\n", encoding="utf-8")
        os.symlink("adrs", foreign / "docs" / "code")
        self.assert_refused(foreign / "docs" / "adrs", "--output-dir", str(foreign / "docs" / "code"))


class OwnershipProofFailsTests(_Base):
    """A generated root whose proof is damaged refuses rather than pruning."""

    def _generated_root(self) -> Path:
        r = _run(self.root, "--config", str(self.cfg))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        code = self.docs / "code"
        self.assertTrue((code / "_meta" / "manifest.json").is_file())
        return code

    def test_meta_manifest_is_a_link(self):
        code = self._generated_root()
        real = self.root / "elsewhere.json"
        (code / "_meta" / "manifest.json").rename(real)
        os.symlink(str(real), code / "_meta" / "manifest.json")
        self.assert_refused(code)

    def test_meta_directory_is_a_link(self):
        code = self._generated_root()
        (code / "_meta").rename(self.root / "meta_elsewhere")
        os.symlink(str(self.root / "meta_elsewhere"), code / "_meta")
        self.assert_refused(code)

    def test_meta_manifest_is_garbage(self):
        code = self._generated_root()
        (code / "_meta" / "manifest.json").write_text("{not json", encoding="utf-8")
        self.assert_refused(code)

    def test_meta_manifest_fails_the_shape_check(self):
        code = self._generated_root()
        (code / "_meta" / "manifest.json").write_text("[1, 2]\n", encoding="utf-8")
        self.assert_refused(code)

    def test_meta_manifest_is_a_directory(self):
        code = self._generated_root()
        (code / "_meta" / "manifest.json").unlink()
        (code / "_meta" / "manifest.json").mkdir()
        self.assert_refused(code)

    def test_meta_absent(self):
        code = self._generated_root()
        (code / "_meta" / "manifest.json").unlink()
        (code / "_meta").rmdir()
        self.assert_refused(code)


class StrayMetadataWithoutPagesTests(_Base):
    """A root of hand-written Markdown with a stray `_meta/manifest.json` that
    carries no `pages` list proves nothing: it refuses rather than pruning.

    PB-0121 Prompt 11 fix round 2 (council decision A). `{}` and
    `{"extractors": {}}` both pass the metadata shape check, which only types a
    PRESENT `pages`; the ownership proof additionally requires `pages` to be a
    list. The positive control below is the same fixture with a `pages` list.
    """

    def _notes(self, meta: object) -> Path:
        notes = self.docs / "notes"
        (notes / "_meta").mkdir(parents=True)
        (notes / "hand.md").write_text("# written by hand\n", encoding="utf-8")
        (notes / "_meta" / "manifest.json").write_text(json.dumps(meta) + "\n", encoding="utf-8")
        return notes

    def _assert_pages_cause(self, notes: Path) -> None:
        r = _run(self.root, "--dry-run", "--config", str(self.cfg), "--output-dir", str(notes))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        causes = " ".join(e["cause"] for e in json.loads(r.stdout)["validation_errors"])
        self.assertIn("pages", causes, causes)
        self.assertIn("not a list", causes, causes)

    def test_empty_object_manifest_refuses(self):
        notes = self._notes({})
        self.assert_refused(notes, "--output-dir", str(notes))
        self._assert_pages_cause(notes)
        self.assertTrue((notes / "hand.md").is_file())

    def test_manifest_with_extractors_and_no_pages_refuses(self):
        notes = self._notes({"extractors": {}})
        self.assert_refused(notes, "--output-dir", str(notes))
        self._assert_pages_cause(notes)
        self.assertTrue((notes / "hand.md").is_file())

    def test_positive_control_legacy_manifest_with_a_pages_list_is_owned(self):
        for meta in ({"pages": [], "extractor_version": "1"}, {"pages": []}):
            with self.subTest(meta=meta):
                notes = self._notes(meta)
                r = _run(self.root, "--dry-run", "--config", str(self.cfg), "--output-dir", str(notes))
                self.assertNotIn("validation_errors", r.stdout, r.stdout + r.stderr)
                r = _run(self.root, "--config", str(self.cfg), "--output-dir", str(notes))
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertFalse((notes / "hand.md").exists(), "an owned root prunes as before")
                shutil.rmtree(notes)


class RefusalMessageTests(_Base):
    def test_message_names_roots_link_count_sample_and_gives_no_forbidden_advice(self):
        os.symlink("adrs", self.docs / "code")
        r = _run(self.root, "--config", str(self.cfg))
        self.assertEqual(r.returncode, 1, r.stderr)
        err = r.stderr
        self.assertIn(str(self.docs / "adrs"), err, "names the resolved root")
        self.assertIn(str(self.docs / "code"), err, "names the spelled root and the link on the path")
        self.assertIn("3 regular *.md", err, "counts every regular *.md file")
        self.assertIn("ADR-0001-first.md", err, "carries a sample of the paths")
        self.assertIn("nothing was written or deleted", err.lower())
        self.assertIn("--output-dir", err, "names the retarget remedy")
        lowered = err.lower()
        for phrase in _FORBIDDEN_ADVICE:
            self.assertNotIn(phrase, lowered, phrase)

    def test_sample_is_bounded(self):
        many = self.root / "docs" / "many"
        many.mkdir()
        for i in range(40):
            (many / f"page-{i:02d}.md").write_text("# p\n", encoding="utf-8")
        r = _run(self.root, "--config", str(self.cfg), "--output-dir", str(many))
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("40 regular *.md", r.stderr)
        self.assertNotIn("page-39.md", r.stderr, "the sample must be bounded, not the whole listing")


class OwnershipGreenControlTests(_Base):
    """Positive controls: the refusal is not a blanket refusal."""

    def test_custom_alt_root_absent_before_its_run_proceeds(self):
        bionic = self.root / "bionic"
        bionic.mkdir()
        (bionic / "manifest.yml").write_text('schema_version: "5"\n', encoding="utf-8")
        alt = bionic / "alt"
        r = _run(self.root, "--config", str(self.cfg), "--output-dir", str(alt))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((alt / "_meta" / "manifest.json").is_file())
        # And the now-owned root runs again clean.
        r = _run(self.root, "--config", str(self.cfg), "--output-dir", str(alt))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_empty_root_proceeds(self):
        (self.docs / "code").mkdir()
        r = _run(self.root, "--config", str(self.cfg))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.docs / "code" / "_meta" / "manifest.json").is_file())

    def test_link_to_a_second_trees_code_root_proceeds(self):
        other = self.root / "other"
        (other / "docs").mkdir(parents=True)
        (other / "docs" / "manifest.yml").write_text('schema_version: "5"\n', encoding="utf-8")
        os.symlink(str(other / "docs" / "code"), self.docs / "code")
        (other / "docs" / "code").mkdir()
        r = _run(self.root, "--config", str(self.cfg))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # The second run finds that root owned and proceeds again.
        r = _run(self.root, "--config", str(self.cfg))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((other / "docs" / "code" / "_meta" / "manifest.json").is_file())

    def test_full_run_on_an_owned_root_still_prunes_an_unexpected_page(self):
        r = _run(self.root, "--config", str(self.cfg))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        stray = self.docs / "code" / "stray.md"
        stray.write_text("# stray\n", encoding="utf-8")
        r = _run(self.root, "--config", str(self.cfg))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(stray.exists(), "a full run on an owned root prunes as before")

    def test_owned_root_link_into_adrs_leaves_the_target_unchanged(self):
        r = _run(self.root, "--config", str(self.cfg))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        os.symlink("../adrs", self.docs / "code" / "sub")
        before = _snapshot(self.docs / "adrs")
        r = _run(self.root, "--config", str(self.cfg))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(_snapshot(self.docs / "adrs"), before)

    def test_unowned_root_whose_only_markdown_lies_behind_a_link_proceeds(self):
        code = self.docs / "code"
        code.mkdir()
        os.symlink("../adrs", code / "sub")
        before = _snapshot(self.docs / "adrs")
        r = _run(self.root, "--config", str(self.cfg))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(_snapshot(self.docs / "adrs"), before)

    def test_metadata_in_the_oldest_dispatcher_shape_proves_ownership(self):
        code = self.docs / "code"
        (code / "_meta").mkdir(parents=True)
        (code / "old.md").write_text("# an old generated page\n", encoding="utf-8")
        (code / "_meta" / "manifest.json").write_text(
            json.dumps(
                {
                    "pages": [
                        {
                            "source_path": "src/old.py",
                            "doc_path": "old.md",
                            "sha256": "0" * 64,
                            "extractor_version": "1",
                        }
                    ],
                    "extractor_version": "1",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        r = _run(self.root, "--dry-run", "--config", str(self.cfg))
        self.assertNotIn("validation_errors", r.stdout, r.stdout + r.stderr)
        r = _run(self.root, "--config", str(self.cfg))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((code / "old.md").exists(), "the owned root's stale page is pruned")


if __name__ == "__main__":
    unittest.main()
