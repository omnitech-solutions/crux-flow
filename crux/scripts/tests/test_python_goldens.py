"""Golden-byte tests for the Python extractor (ADR-0131; PB-0121 dev-1
developer E3's slice), against the hand-authored fixtures under
`crux/scripts/tests/fixtures/python_code_docs/`.

Every fixture set's `expected/<scenario>/` tree was committed BEFORE the
renderer (`_py_page.py`, `render_page`) existed, per ADR-0131 clause 8: "The
independently authored expected pages are committed ... before the renderer
is implemented. Once committed they are the source of truth for this shape."
These tests read that tree as a golden set and run the REAL dispatcher CLI
(never a stub) over the REAL, installed `extractors/` directory, so a
regression in the renderer's byte-for-byte output shows up here rather than
only in the stub-backed CLI tests in `test_python_extractor.py`.

Stdlib only. Every disposable tree is built under `tempfile`; the fixtures
read from the repository ship inside `crux/` and need no dev-surface guard.
"""

from __future__ import annotations

import difflib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # crux-internal repo root
SCRIPT_PATH = REPO_ROOT / "crux" / "scripts" / "extract-code-docs.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "python_code_docs"


def _glob_yaml(glob) -> str:
    if isinstance(glob, str):
        glob = [glob]
    return "[" + ", ".join(f'"{g}"' for g in glob) + "]"


def _manifest_yaml(*, key: str, glob, include_private: bool) -> str:
    return (
        'schema_version: "5"\n'
        "code:\n  extractors:\n"
        f"    {key}:\n"
        "      extractor: python\n"
        f"      include_private: {'true' if include_private else 'false'}\n"
        f"      glob: {_glob_yaml(glob)}\n"
    )


class _GoldenScenarioTests(unittest.TestCase):
    """Shared scaffolding: copy a set's src/ verbatim as the repo root, run
    the real dispatcher CLI, compare the output tree byte-for-byte against
    the set's expected/<scenario>/ tree."""

    def _run_over_src(self, src_dir: Path, *, glob, include_private: bool, key: str = "python") -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        shutil.copytree(src_dir, root, dirs_exist_ok=True)
        (root / ".bionic.yml").write_text(
            'config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8"
        )
        (root / "bionic").mkdir(exist_ok=True)
        (root / "bionic" / "manifest.yml").write_text(
            _manifest_yaml(key=key, glob=glob, include_private=include_private),
            encoding="utf-8",
        )
        config = root / "bionic" / "manifest.yml"
        proc = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--config", str(config)],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return root / "bionic" / "code"

    def _assert_matches_expected(self, output_root: Path, expected_dir: Path) -> None:
        expected_files = {
            str(p.relative_to(expected_dir)): p
            for p in sorted(expected_dir.rglob("*")) if p.is_file()
        }
        self.assertTrue(expected_files, f"no expected files found under {expected_dir}")
        for rel, expected_path in sorted(expected_files.items()):
            with self.subTest(page=rel):
                actual_path = output_root / rel
                self.assertTrue(actual_path.is_file(), f"missing output file: {rel}")
                expected_bytes = expected_path.read_bytes()
                actual_bytes = actual_path.read_bytes()
                if actual_bytes != expected_bytes:
                    diff = "\n".join(
                        difflib.unified_diff(
                            expected_bytes.decode("utf-8", "replace").splitlines(),
                            actual_bytes.decode("utf-8", "replace").splitlines(),
                            fromfile=f"expected/{rel}",
                            tofile=f"actual/{rel}",
                            lineterm="",
                        )
                    )
                    self.fail(f"byte mismatch for {rel}:\n{diff}")


class RealSetGoldenTests(_GoldenScenarioTests):
    _SET = FIXTURES / "real"
    _GLOB = ["**/*.py"]

    def test_all_members(self):
        output_root = self._run_over_src(self._SET / "src", glob=self._GLOB, include_private=True)
        self._assert_matches_expected(output_root, self._SET / "expected" / "all-members")

    def test_private_excluded(self):
        output_root = self._run_over_src(self._SET / "src", glob=self._GLOB, include_private=False)
        self._assert_matches_expected(output_root, self._SET / "expected" / "private-excluded")


class SyntheticSetGoldenTests(_GoldenScenarioTests):
    _SET = FIXTURES / "synthetic"

    def test_base(self):
        glob = ["members.py", "overloads.py", "pep695.py", "pkg/**/*.py"]
        output_root = self._run_over_src(self._SET / "src", glob=glob, include_private=True)
        self._assert_matches_expected(output_root, self._SET / "expected" / "base")

    def test_stubs_separate(self):
        glob = ["stub_only.pyi", "stubbed.py", "stubbed.pyi"]
        output_root = self._run_over_src(self._SET / "src", glob=glob, include_private=True)
        self._assert_matches_expected(output_root, self._SET / "expected" / "stubs-separate")


class NestedSetGoldenTests(_GoldenScenarioTests):
    def test_all_included(self):
        set_dir = FIXTURES / "nested"
        output_root = self._run_over_src(set_dir / "src", glob=["**/*.py"], include_private=True)
        self._assert_matches_expected(output_root, set_dir / "expected" / "all-included")


class DocstringsSetGoldenTests(_GoldenScenarioTests):
    """`crlf_source.py` is committed LF (git would normalize CRLF away on
    checkout); converted to CRLF bytes at test time before the run, per
    `docstrings/README.md`'s "CRLF test-time conversion" section."""

    def test_all_included(self):
        set_dir = FIXTURES / "docstrings"
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        src_copy = Path(tmp.name) / "src"
        shutil.copytree(set_dir / "src", src_copy)
        crlf_path = src_copy / "crlf_source.py"
        original = crlf_path.read_bytes()
        crlf_path.write_bytes(original.replace(b"\n", b"\r\n"))

        output_root = self._run_over_src(src_copy, glob=["**/*.py"], include_private=True)
        self._assert_matches_expected(output_root, set_dir / "expected" / "all-included")


class HostileStringsSetGoldenTests(_GoldenScenarioTests):
    _SET = FIXTURES / "hostile_strings"

    def test_all_included(self):
        output_root = self._run_over_src(self._SET / "src", glob=["**/*.py"], include_private=True)
        self._assert_matches_expected(output_root, self._SET / "expected" / "all-included")

    def test_hostile_names_that_cannot_be_committed_as_files(self):
        """Names whose bytes (a line break, CR, or ESC) cannot be committed
        as a git path. Built at test time from `cases.json`'s Python-string-
        literal-escaped spelling; nothing here is executed.

        Only `source_repo_relative_path_escaped` is a Python-string-literal
        escape needing `unicode_escape` decoding, to build the real file
        with the real (uncommittable) byte in its name. `expected_h1` and
        `expected_index_line` are already the page's own FINAL rendered
        text (R3's visible-escape form is itself backslash-n, backslash-r,
        etc., as two literal characters) -- decoding them again would turn
        that visible `\\n` back into a real newline and corrupt the
        comparison.
        """
        cases_path = self._SET / "expected" / "hostile-names" / "cases.json"
        cases = json.loads(cases_path.read_text(encoding="utf-8"))["cases"]

        def _decode(escaped: str) -> str:
            return escaped.encode().decode("unicode_escape")

        for case in cases:
            with self.subTest(description=case["description"]):
                tmp = tempfile.TemporaryDirectory()
                self.addCleanup(tmp.cleanup)
                root = Path(tmp.name)
                rel_path = _decode(case["source_repo_relative_path_escaped"])
                source_path = root / rel_path
                source_path.parent.mkdir(parents=True, exist_ok=True)
                source_path.write_text("x = 1\n", encoding="utf-8")
                output_root = self._run_over_src(root, glob=["**/*.py"], include_private=True)

                doc_paths = list((output_root / "python").rglob("*.md"))
                self.assertEqual(len(doc_paths), 1, doc_paths)
                page_text = doc_paths[0].read_text(encoding="utf-8")
                h1_line = page_text.splitlines()[0]
                self.assertEqual(h1_line, case["expected_h1"])

                index_text = (output_root / "index.md").read_text(encoding="utf-8")
                self.assertIn(case["expected_index_line"], index_text.splitlines())


class BindingsSetGoldenTests(_GoldenScenarioTests):
    def test_all_included(self):
        set_dir = FIXTURES / "bindings"
        output_root = self._run_over_src(set_dir / "src", glob=["**/*.py"], include_private=True)
        self._assert_matches_expected(output_root, set_dir / "expected" / "all-included")


class ExportsSetGoldenTests(_GoldenScenarioTests):
    def test_all_included(self):
        set_dir = FIXTURES / "exports"
        output_root = self._run_over_src(set_dir / "src", glob=["**/*.py"], include_private=True)
        self._assert_matches_expected(output_root, set_dir / "expected" / "all-included")


class HostileLoadingSetGoldenTests(_GoldenScenarioTests):
    def test_all_included(self):
        set_dir = FIXTURES / "hostile_loading"
        output_root = self._run_over_src(set_dir / "src", glob=["**/*.py"], include_private=True)
        self._assert_matches_expected(output_root, set_dir / "expected" / "all-included")


class SrcLayoutSetGoldenTests(_GoldenScenarioTests):
    """A package one level below the fixture's own repository root
    (`src/mypkg/...`, not `mypkg/...` at the root). PB-0121 Prompt 8 fix
    round, finding M1: exported/redundant aliases in a package not at the
    repository root must resolve, not render as an unresolved-export gap."""

    def test_all_included(self):
        set_dir = FIXTURES / "srclayout"
        output_root = self._run_over_src(set_dir / "src", glob=["src/**/*.py"], include_private=True)
        self._assert_matches_expected(output_root, set_dir / "expected" / "all-included")


class SrcAbsoluteSetGoldenTests(_GoldenScenarioTests):
    """A `src/`-layout package re-exporting through absolute imports by its
    installable name (`from acme.core.values import value as value` inside
    `src/acme/`). PB-0121 Prompt 8 fix round 2."""

    def test_all_included(self):
        set_dir = FIXTURES / "srcabsolute"
        output_root = self._run_over_src(set_dir / "src", glob=["src/**/*.py"], include_private=True)
        self._assert_matches_expected(output_root, set_dir / "expected" / "all-included")


class CrossVersionByteIdentityTests(unittest.TestCase):
    """Reruns are byte-identical on Python 3.13 and 3.14 (book Evidence).
    Runs the real `real/` set under both pinned interpreters via
    `uv run --no-project --python <ver> --with griffelib==2.3.0`, skipping
    with a clear reason when `uv` or either interpreter is unavailable."""

    def _run_under(self, py_version: str, root: Path, config: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                "uv", "run", "--no-project", "--python", py_version,
                "--with", "griffelib==2.3.0",
                str(SCRIPT_PATH), "--config", str(config),
            ],
            capture_output=True, text=True,
        )

    def test_3_13_and_3_14_produce_identical_bytes(self):
        if shutil.which("uv") is None:
            self.skipTest("uv is not installed")
        for version in ("3.13", "3.14"):
            probe = subprocess.run(
                ["uv", "python", "find", "--no-project", version],
                capture_output=True, text=True,
            )
            if probe.returncode != 0 or not probe.stdout.strip():
                self.skipTest(f"Python {version} is not available via uv")

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root_a = Path(tmp.name) / "a"
        root_b = Path(tmp.name) / "b"
        set_dir = FIXTURES / "real"
        for root in (root_a, root_b):
            shutil.copytree(set_dir / "src", root)
            (root / ".bionic.yml").write_text(
                'config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8"
            )
            (root / "bionic").mkdir(exist_ok=True)
            (root / "bionic" / "manifest.yml").write_text(
                _manifest_yaml(key="python", glob=["**/*.py"], include_private=True),
                encoding="utf-8",
            )

        proc_a = self._run_under("3.13", root_a, root_a / "bionic" / "manifest.yml")
        proc_b = self._run_under("3.14", root_b, root_b / "bionic" / "manifest.yml")
        self.assertEqual(proc_a.returncode, 0, proc_a.stderr)
        self.assertEqual(proc_b.returncode, 0, proc_b.stderr)

        out_a = root_a / "bionic" / "code"
        out_b = root_b / "bionic" / "code"
        files_a = {str(p.relative_to(out_a)): p.read_bytes() for p in sorted(out_a.rglob("*")) if p.is_file()}
        files_b = {str(p.relative_to(out_b)): p.read_bytes() for p in sorted(out_b.rglob("*")) if p.is_file()}
        self.assertEqual(set(files_a), set(files_b))
        for rel, content_a in files_a.items():
            with self.subTest(page=rel):
                self.assertEqual(content_a, files_b[rel])


if __name__ == "__main__":
    unittest.main()
