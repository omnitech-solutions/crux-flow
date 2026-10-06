"""CLI-level tests for the Python extractor entry point (ADR-0131; PB-0121
dev-1 developer E3's slice: `crux/scripts/extractors/python.py`, joining the
dispatcher, `_py_load.py` and `_py_page.py`).

Stdlib only. Every disposable tree is built under `tempfile`; the only
fixtures read from the repository are the committed synthetic parse-failure
sources and the `hostile_loading/src/` tree under
`crux/scripts/tests/fixtures/python_code_docs/`, which ship inside `crux/`
and need no dev-surface guard (`_dev_surface.require_dev_surface` is not
needed here: nothing outside `crux/` is read).

## Why most tests here use a copied plugin tree with a stub renderer

`_py_page.py` (the page renderer, `render_page`) is a parallel PB-0121 dev-1
unit, written by the dev-lead, under this file's own assigned ownership
boundary (not this developer's slice). `python.py` loads it lazily (only
inside `extract()`), so `discover()` and `provenance()` work with no
stand-in at all -- those tests run against the real, installed `extractors/`
directory.

Every test that needs `extract()` to succeed (write a real page, inspect a
metadata row, prove rerun idempotency, or exercise the two-Python-key
provenance-preservation regression) instead runs the dispatcher against a
**copied** plugin tree (`extract-code-docs.py` + `extractors/`, verbatim)
that carries one addition: a deterministic, stdlib-only stand-in
`_py_page.render_page`, written once as `_STUB_PY_PAGE_SOURCE` below. This
never edits the real `extractors/` directory (out of this unit's ownership)
and lets the CLI-level contract -- routing `include_private`, the metadata
row shape, provenance-block equality, idempotency, the two-key regression --
be proven independently of the real renderer's own byte-for-byte output,
which `test_python_goldens.py` covers instead.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # crux-internal repo root
SCRIPTS_DIR = REPO_ROOT / "crux" / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "extract-code-docs.py"
EXTRACTORS_DIR = SCRIPTS_DIR / "extractors"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "python_code_docs"
HOSTILE_SRC = FIXTURES / "hostile_loading" / "src"
SYNTAX_SRC = FIXTURES / "synthetic" / "src" / "syntax"


# ─────────────────────── loading the real modules in-process ──────────────


_EXTRACTOR_SIBLING_MODULE_NAMES = (
    "extract_code_docs_dispatcher",
    "crux_py_load",
    "crux_py_locals",
    "crux_py_render",
    "crux_py_page",
)


def _purge_cached_extractor_modules() -> None:
    """`python.py` and `_py_load.py` cache their sibling loads in
    `sys.modules` under fixed names (see each file's own
    `_load_sibling_by_path`). Loading a fresh dispatcher without purging
    those names would leave `_py_load.py`'s `ExtractionRefusal` reference
    pointing at a STALE dispatcher's class object -- same name, different
    identity, so `assertRaises(dispatcher.ExtractionRefusal)` would never
    match it. Every fresh load in this file purges first."""
    for name in _EXTRACTOR_SIBLING_MODULE_NAMES:
        sys.modules.pop(name, None)


def _load_dispatcher():
    _purge_cached_extractor_modules()
    spec = importlib.util.spec_from_file_location("extract_code_docs_dispatcher", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["extract_code_docs_dispatcher"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_python_extractor(dispatcher):
    spec = importlib.util.spec_from_file_location(
        "crux_python_extractor_under_test", EXTRACTORS_DIR / "python.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ────────────────────────── the test-local stand-in renderer ──────────────


_STUB_PY_PAGE_SOURCE = '''\
"""Deterministic stand-in for the real renderer (PB-0121 dev-1 test-only
module; never committed to the real extractors/ directory). Reads only the
already-loaded source text with a plain regex scan -- never Griffe, never
`ast` -- because proving the composing extractor's own contract (routing
include_private, the metadata row shape, provenance, idempotency) needs no
real member extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_DEF_RE = re.compile(r"^(?:async\\s+)?def\\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
_CLASS_RE = re.compile(r"^class\\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
_GAP_RE = re.compile(r"^# STUB-GAP: (.+)$", re.M)


@dataclass(frozen=True)
class PageResult:
    doc_path: str
    title: str
    body: str
    gaps: list


def _is_private(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def render_page(source, selection, *, include_private):
    names = sorted(set(_DEF_RE.findall(source.text)) | set(_CLASS_RE.findall(source.text)))
    shown = [n for n in names if include_private or not _is_private(n)]
    excluded = len(names) - len(shown)
    lines = [f"# {source.rel_path}", ""]
    lines.append(
        f"stub-render module={source.module.path} is_stub={source.is_stub} "
        f"include_private={include_private}"
    )
    if excluded:
        lines.append(f"excluded={excluded}")
    for name in shown:
        lines.append(f"- {name}")
    body = "\\n".join(lines).rstrip() + "\\n"
    gaps = [
        {"kind": "stub_gap", "note": note}
        for note in _GAP_RE.findall(source.text)
    ]
    doc_path = f"python/{source.rel_path}.md"
    return PageResult(doc_path=doc_path, title=source.rel_path, body=body, gaps=gaps)
'''


def _copy_plugin_tree(
    dest: Path,
    *,
    stub_py_page: bool = True,
    edit_constants: dict[str, dict[str, str]] | None = None,
) -> Path:
    """Copy `extract-code-docs.py` + `extractors/` into `dest`, verbatim.

    `stub_py_page` drops in the deterministic stand-in above (see module
    docstring for why). `edit_constants` rewrites named constants in named
    sibling files -- `{"filename.py": {"OLD_LITERAL": "NEW_LITERAL"}}` -- for
    the clause-13 two-Python-key regression, never a production toggle: a
    fresh, disposable copy of the whole plugin tree with one literal edited.
    Returns the path to the copied `extract-code-docs.py`.
    """
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SCRIPT_PATH, dest / "extract-code-docs.py")
    extractors_dest = dest / "extractors"
    shutil.copytree(
        EXTRACTORS_DIR, extractors_dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    if stub_py_page:
        (extractors_dest / "_py_page.py").write_text(_STUB_PY_PAGE_SOURCE, encoding="utf-8")
    for filename, replacements in (edit_constants or {}).items():
        path = extractors_dest / filename
        text = path.read_text(encoding="utf-8")
        for old, new in replacements.items():
            assert old in text, f"{old!r} not found in {filename}"
            text = text.replace(old, new, 1)
        path.write_text(text, encoding="utf-8")
    return dest / "extract-code-docs.py"


def _run(script: Path, *argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *argv],
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
    )


def _glob_yaml(glob) -> str:
    if isinstance(glob, str):
        glob = [glob]
    return "[" + ", ".join(f'"{g}"' for g in glob) + "]"


def _manifest_yaml(*, keys: dict[str, dict]) -> str:
    """`keys` maps a manifest key to `{"glob": ..., "include_private": bool}`."""
    lines = ['schema_version: "5"', "code:", "  extractors:"]
    for key, cfg in keys.items():
        lines.append(f"    {key}:")
        lines.append("      extractor: python")
        if "glob" in cfg:
            lines.append(f'      glob: {_glob_yaml(cfg["glob"])}')
        if "include_private" in cfg:
            lines.append(f'      include_private: {"true" if cfg["include_private"] else "false"}')
    return "\n".join(lines) + "\n"


def _fingerprint(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


class _DisposableCliTree(unittest.TestCase):
    """Shared scaffolding for the CLI-level (stub-renderer) test classes."""

    def _make_tree(self, *, keys: dict[str, dict], sources: dict[str, str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs").mkdir()
        for rel, text in sources.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (root / "docs" / "manifest.yml").write_text(_manifest_yaml(keys=keys), encoding="utf-8")
        return root

    def _copied_script(self, **kwargs) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return _copy_plugin_tree(Path(tmp.name) / "plugin", **kwargs)


class CopyPluginTreeTests(unittest.TestCase):
    """`_copy_plugin_tree` copies the extractor sources and no interpreter cache.

    A `__pycache__` in the source is written by whatever imported the extractors
    last, and a concurrent import can create or rename a `*.pyc` in it while the
    copy walks it. The copy exercises no bytecode, so cache files are excluded.
    """

    def _dest(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name) / "plugin"

    def test_cache_files_in_the_source_are_not_copied(self):
        src_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(src_tmp.cleanup)
        src = Path(src_tmp.name) / "extractors"
        (src / "__pycache__").mkdir(parents=True)
        (src / "a.py").write_text("A = 1\n", encoding="utf-8")
        (src / "__pycache__" / "a.cpython-313.pyc").write_bytes(b"\x00pyc")
        (src / "stray.pyc").write_bytes(b"\x00pyc")
        with mock.patch(f"{__name__}.EXTRACTORS_DIR", src):
            script = _copy_plugin_tree(self._dest(), stub_py_page=False)
        copied = sorted(
            str(p.relative_to(script.parent / "extractors"))
            for p in (script.parent / "extractors").rglob("*")
        )
        self.assertEqual(copied, ["a.py"])

    def test_the_real_extractor_sources_are_copied_byte_equal(self):
        script = _copy_plugin_tree(self._dest(), stub_py_page=False)

        def sources(root: Path) -> dict[str, bytes]:
            return {
                str(p.relative_to(root)): p.read_bytes()
                for p in sorted(root.rglob("*"))
                if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
            }

        expected = sources(EXTRACTORS_DIR)
        self.assertIn("python.py", expected)
        self.assertEqual(sources(script.parent / "extractors"), expected)


# ═══════════════════════════ discovery (in-process) ════════════════════════


class DiscoverUnitTests(unittest.TestCase):
    """`discover()` called directly, in-process, against the REAL
    `python.py` -- no `_py_page` involved, so no stub is needed here."""

    def setUp(self):
        self.dispatcher = _load_dispatcher()
        self.py = _load_python_extractor(self.dispatcher)

    def _tmp_repo(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def _ctx(self, repo_root: Path, output_root: Path | None = None):
        return self.dispatcher.ExtractContext(
            repo_root=repo_root,
            output_root=(output_root or (repo_root / "out")).resolve(),
            lang_key="python",
        )

    def test_an_oversized_source_refuses_before_its_bytes_are_read(self):
        """The 2 MiB bound (ADR-0131 clause 7) is checked from the descriptor's
        fstat size, so an oversized source is never read into memory. A sparse
        file stands in for a large one; `os.fdopen` failing proves no read."""
        from unittest import mock

        repo = self._tmp_repo()
        big = repo / "big.py"
        with big.open("wb") as handle:
            handle.truncate(3 * 1024 * 1024)
        with mock.patch.object(self.dispatcher.os, "fdopen",
                               side_effect=AssertionError("source bytes were read")):
            with self.assertRaises(self.dispatcher.ExtractionRefusal) as ctx:
                self.py.discover(repo, {"glob": "*.py"}, self._ctx(repo))
        self.assertEqual(ctx.exception.path, "big.py")
        self.assertEqual(
            ctx.exception.cause,
            "source is 3145728 bytes, over the 2097152-byte bound; narrow the glob to exclude it",
        )

    def test_glob_accepts_a_string_or_a_list(self):
        repo = self._tmp_repo()
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
        (repo / "b.py").write_text("y = 2\n", encoding="utf-8")
        units_str = self.py.discover(repo, {"glob": "*.py"}, self._ctx(repo))
        units_list = self.py.discover(repo, {"glob": ["*.py"]}, self._ctx(repo))
        self.assertEqual(
            sorted(u.source_path for u in units_str),
            sorted(u.source_path for u in units_list),
        )
        self.assertEqual(sorted(u.source_path for u in units_str), ["a.py", "b.py"])

    def test_only_py_and_pyi_files_are_selected(self):
        repo = self._tmp_repo()
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
        (repo / "a.pyi").write_text("x: int\n", encoding="utf-8")
        (repo / "notes.txt").write_text("not python\n", encoding="utf-8")
        units = self.py.discover(repo, {"glob": "*"}, self._ctx(repo))
        self.assertEqual(sorted(u.source_path for u in units), ["a.py", "a.pyi"])

    def test_pyi_and_py_load_into_separate_buckets(self):
        repo = self._tmp_repo()
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
        (repo / "a.pyi").write_text("x: int\n", encoding="utf-8")
        units = self.py.discover(repo, {"glob": "*"}, self._ctx(repo))
        by_path = {u.source_path: u for u in units}
        self.assertFalse(by_path["a.py"].payload["source"].is_stub)
        self.assertTrue(by_path["a.pyi"].payload["source"].is_stub)
        # One shared LoadedSelection object, not two independent loads.
        self.assertIs(by_path["a.py"].payload["selection"], by_path["a.pyi"].payload["selection"])

    def test_output_root_is_excluded_even_when_the_glob_covers_it(self):
        repo = self._tmp_repo()
        output_root = repo / "docs" / "code"
        (output_root / "stray").mkdir(parents=True)
        (output_root / "stray" / "leftover.py").write_text("z = 1\n", encoding="utf-8")
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
        units = self.py.discover(repo, {"glob": "**/*.py"}, self._ctx(repo, output_root))
        self.assertEqual([u.source_path for u in units], ["a.py"])

    def test_deterministic_order_across_repeated_calls(self):
        repo = self._tmp_repo()
        for name in ("z.py", "m.py", "a.py"):
            (repo / name).write_text("v = 1\n", encoding="utf-8")
        first = [u.source_path for u in self.py.discover(repo, {"glob": "*.py"}, self._ctx(repo))]
        second = [u.source_path for u in self.py.discover(repo, {"glob": "*.py"}, self._ctx(repo))]
        self.assertEqual(first, second)
        self.assertEqual(first, sorted(first))

    def test_source_path_is_repo_relative_as_selected(self):
        repo = self._tmp_repo()
        (repo / "pkg").mkdir()
        (repo / "pkg" / "mod.py").write_text("v = 1\n", encoding="utf-8")
        units = self.py.discover(repo, {"glob": "**/*.py"}, self._ctx(repo))
        self.assertEqual([u.source_path for u in units], ["pkg/mod.py"])

    def test_include_private_default_true_when_absent(self):
        repo = self._tmp_repo()
        (repo / "a.py").write_text("v = 1\n", encoding="utf-8")
        units = self.py.discover(repo, {"glob": "*.py"}, self._ctx(repo))
        self.assertTrue(units[0].payload["include_private"])

    def test_include_private_non_boolean_refuses_naming_the_key(self):
        repo = self._tmp_repo()
        (repo / "a.py").write_text("v = 1\n", encoding="utf-8")
        with self.assertRaises(self.dispatcher.ExtractionRefusal) as ctx:
            self.py.discover(repo, {"glob": "*.py", "include_private": "yes"}, self._ctx(repo))
        self.assertEqual(ctx.exception.path, "code.extractors.python.include_private")
        self.assertIn("boolean", ctx.exception.cause)

    def test_an_unrecognized_key_refuses_naming_the_manifest_path(self):
        """`options: {...}` (the wrong, nested shape) refuses instead of
        being silently ignored, which used to let `include_private` default
        `True` unnoticed."""
        repo = self._tmp_repo()
        (repo / "a.py").write_text("v = 1\n", encoding="utf-8")
        with self.assertRaises(self.dispatcher.ExtractionRefusal) as ctx:
            self.py.discover(
                repo, {"glob": "*.py", "options": {"include_private": False}}, self._ctx(repo)
            )
        self.assertEqual(ctx.exception.path, "code.extractors.python.options")
        self.assertIn("unrecognized key", ctx.exception.cause)

    def test_symlink_outside_repo_refuses(self):
        repo = self._tmp_repo()
        outside_dir = tempfile.TemporaryDirectory()
        self.addCleanup(outside_dir.cleanup)
        outside_file = Path(outside_dir.name) / "secret.py"
        outside_file.write_text("secret = 1\n", encoding="utf-8")
        try:
            (repo / "link.py").symlink_to(outside_file)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")
        with self.assertRaises(self.dispatcher.ExtractionRefusal) as ctx:
            self.py.discover(repo, {"glob": "*.py"}, self._ctx(repo))
        self.assertIn("outside the repository root", ctx.exception.cause)

    def test_positive_control_an_in_repo_source_discovers_cleanly(self):
        repo = self._tmp_repo()
        (repo / "a.py").write_text("v = 1\n", encoding="utf-8")
        units = self.py.discover(repo, {"glob": "*.py"}, self._ctx(repo))
        self.assertEqual(len(units), 1)

    def test_check_to_read_race_refuses(self):
        """A source that stops being a regular file between resolve_source's
        check and read_source_bytes's read refuses (D6), never silently
        skipped or read stale."""
        repo = self._tmp_repo()
        (repo / "a.py").write_text("v = 1\n", encoding="utf-8")
        real_read = self.py.read_source_bytes
        calls = {"n": 0}

        def flaky_read(root, resolved, max_bytes=None):
            calls["n"] += 1
            raise self.dispatcher.ExtractionRefusal(str(resolved), "is no longer a regular file")

        with mock.patch.object(self.py, "read_source_bytes", side_effect=flaky_read):
            with self.assertRaises(self.dispatcher.ExtractionRefusal) as ctx:
                self.py.discover(repo, {"glob": "*.py"}, self._ctx(repo))
        self.assertEqual(calls["n"], 1)
        self.assertIn("no longer a regular file", ctx.exception.cause)
        # Positive control: the real read_source_bytes succeeds on the same file.
        resolved = self.py.resolve_source(repo, Path("a.py"))
        self.assertEqual(real_read(repo, resolved), b"v = 1\n")

    def test_a_parse_failure_refuses_the_whole_discover_call(self):
        repo = self._tmp_repo()
        shutil.copy2(SYNTAX_SRC / "broken.py", repo / "broken.py")
        with self.assertRaises(self.dispatcher.ExtractionRefusal) as ctx:
            self.py.discover(repo, {"glob": "*.py"}, self._ctx(repo))
        self.assertEqual(ctx.exception.path, "broken.py")

    def test_the_3_14_only_file_refuses_identically_regardless_of_host_interpreter(self):
        repo = self._tmp_repo()
        shutil.copy2(SYNTAX_SRC / "py314_only.py", repo / "py314_only.py")
        with self.assertRaises(self.dispatcher.ExtractionRefusal) as ctx:
            self.py.discover(repo, {"glob": "*.py"}, self._ctx(repo))
        self.assertEqual(ctx.exception.path, "py314_only.py")

    def test_an_oversized_source_refuses(self):
        repo = self._tmp_repo()
        big = repo / "big.py"
        # Just over the loader's 2 MiB bound; built at test time, not committed.
        big.write_text("x = 1  # " + ("a" * (2 * 1024 * 1024 + 10)) + "\n", encoding="utf-8")
        with self.assertRaises(self.dispatcher.ExtractionRefusal) as ctx:
            self.py.discover(repo, {"glob": "*.py"}, self._ctx(repo))
        self.assertEqual(ctx.exception.path, "big.py")
        self.assertIn("bytes", ctx.exception.cause)

    def test_provenance_has_no_interpreter_version_and_matches_the_pin(self):
        result = self.py.provenance({"include_private": False})
        self.assertEqual(result["backend"], self.dispatcher.GRIFFE_REQUIREMENT)
        self.assertEqual(result["grammar"], "3.13")
        self.assertFalse(result["include_private"])
        # "3.13" is the fixed grammar, deliberately -- the ban is on the HOST
        # interpreter's own version, checked against every OTHER field.
        for key, value in result.items():
            if key == "grammar":
                continue
            self.assertNotIn(f"{sys.version_info[0]}.{sys.version_info[1]}", str(value))

    def test_provenance_include_private_defaults_true(self):
        result = self.py.provenance({})
        self.assertTrue(result["include_private"])

    def test_provenance_include_private_non_boolean_refuses(self):
        with self.assertRaises(self.dispatcher.ExtractionRefusal) as ctx:
            self.py.provenance({"include_private": 1})
        self.assertEqual(ctx.exception.path, "include_private")


# ═══════════════════════ CLI-level tests (stub renderer) ═══════════════════


class CliRoundtripTests(_DisposableCliTree):
    def test_full_write_then_rerun_is_byte_identical_and_dry_run_is_clean(self):
        script = self._copied_script()
        root = self._make_tree(
            keys={"py": {"glob": "**/*.py", "include_private": True}},
            sources={"src/a.py": '"""A."""\ndef pub():\n    pass\n\ndef _priv():\n    pass\n'},
        )
        r1 = _run(script, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r1.returncode, 0, r1.stderr)
        before = _fingerprint(root / "docs" / "code")
        self.assertTrue(before, "the run must have written something")

        r2 = _run(script, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r2.returncode, 0, r2.stderr)
        after = _fingerprint(root / "docs" / "code")
        self.assertEqual(before, after, "an unchanged tree reruns byte-identical")

        r3 = _run(script, "--dry-run", "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r3.returncode, 0, r3.stdout)
        payload = json.loads(r3.stdout)
        self.assertFalse(payload["drift"])
        self.assertFalse(payload["metadata_drift"])

    def test_include_private_false_omits_private_declarations_true_includes_them(self):
        script = self._copied_script()
        source = "def pub():\n    pass\n\ndef _priv():\n    pass\n"
        root_true = self._make_tree(
            keys={"py": {"glob": "**/*.py", "include_private": True}},
            sources={"src/a.py": source},
        )
        root_false = self._make_tree(
            keys={"py": {"glob": "**/*.py", "include_private": False}},
            sources={"src/a.py": source},
        )
        r_true = _run(script, "--config", str(root_true / "docs" / "manifest.yml"))
        r_false = _run(script, "--config", str(root_false / "docs" / "manifest.yml"))
        self.assertEqual(r_true.returncode, 0, r_true.stderr)
        self.assertEqual(r_false.returncode, 0, r_false.stderr)
        page_true = (root_true / "docs" / "code" / "python" / "src" / "a.py.md").read_text()
        page_false = (root_false / "docs" / "code" / "python" / "src" / "a.py.md").read_text()
        self.assertIn("- _priv", page_true)
        self.assertNotIn("- _priv", page_false)
        self.assertNotEqual(page_true, page_false)

    def test_rows_carry_language_source_sha256_gaps_and_owner(self):
        script = self._copied_script()
        source = "# STUB-GAP: something worth naming\ndef pub():\n    pass\n"
        root = self._make_tree(
            keys={"py": {"glob": "**/*.py", "include_private": True}},
            sources={"src/a.py": source},
        )
        r = _run(script, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        manifest = json.loads((root / "docs" / "code" / "_meta" / "manifest.json").read_text())
        rows = [row for row in manifest["pages"] if row["doc_path"] == "python/src/a.py.md"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["owner"], "py")
        self.assertEqual(row["language"], "python")
        self.assertEqual(row["source_sha256"], hashlib.sha256(source.encode("utf-8")).hexdigest())
        self.assertEqual(row["gaps"], [{"kind": "stub_gap", "note": "something worth naming"}])

    def test_extractors_block_equals_provenance_for_the_configured_key(self):
        script = self._copied_script()
        root = self._make_tree(
            keys={"py": {"glob": "**/*.py", "include_private": False}},
            sources={"src/a.py": "x = 1\n"},
        )
        r = _run(script, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        manifest = json.loads((root / "docs" / "code" / "_meta" / "manifest.json").read_text())

        dispatcher = _load_dispatcher()
        py = _load_python_extractor(dispatcher)
        expected = py.provenance({"include_private": False})
        self.assertEqual(manifest["extractors"]["py"], expected)


class CliParseFailureTests(_DisposableCliTree):
    """Clause 7: a selected source that fails to parse refuses the WHOLE run and
    leaves every existing output byte unchanged, in both refusal lanes."""

    def _populated(self):
        script = self._copied_script()
        root = self._make_tree(
            keys={"py": {"glob": "**/*.py", "include_private": True}},
            sources={"src/a.py": '"""A."""\ndef pub():\n    pass\n'},
        )
        r = _run(script, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        return script, root

    def test_a_parse_failure_refuses_a_write_with_output_unchanged(self):
        script, root = self._populated()
        before = _fingerprint(root / "docs" / "code")
        (root / "src" / "b.py").write_text("def broken(:\n", encoding="utf-8")
        r = _run(script, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("src/b.py", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(_fingerprint(root / "docs" / "code"), before)

    def test_a_parse_failure_under_dry_run_is_a_validation_error(self):
        script, root = self._populated()
        before = _fingerprint(root / "docs" / "code")
        (root / "src" / "b.py").write_text("def broken(:\n", encoding="utf-8")
        r = _run(script, "--dry-run", "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 1, r.stderr)
        payload = json.loads(r.stdout)
        self.assertNotIn("drift", payload)
        self.assertTrue(any("src/b.py" in row["path"] for row in payload["validation_errors"]))
        self.assertEqual(_fingerprint(root / "docs" / "code"), before)

    def test_positive_control_the_same_edit_without_the_syntax_error_writes(self):
        script, root = self._populated()
        before = _fingerprint(root / "docs" / "code")
        (root / "src" / "b.py").write_text("def fine():\n    pass\n", encoding="utf-8")
        r = _run(script, "--config", str(root / "docs" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotEqual(_fingerprint(root / "docs" / "code"), before)


class TwoPythonKeyRegressionTests(_DisposableCliTree):
    """Clause 13: a --lang write rewrites only the selected key's provenance
    block; the other key's block AND rows stay byte-unchanged, and a
    following --dry-run --lang <other> reports its now-stale block as
    metadata drift."""

    def test_lang_write_preserves_the_other_keys_provenance_and_rows(self):
        script = self._copied_script()
        root = self._make_tree(
            keys={
                "a": {"glob": "src_a/**/*.py", "include_private": True},
                "b": {"glob": "src_b/**/*.py", "include_private": True},
            },
            sources={
                "src_a/one.py": "def a_fn():\n    pass\n",
                "src_b/two.py": "def b_fn():\n    pass\n",
            },
        )
        config = root / "docs" / "manifest.yml"
        r1 = _run(script, "--config", str(config))
        self.assertEqual(r1.returncode, 0, r1.stderr)
        meta_path = root / "docs" / "code" / "_meta" / "manifest.json"
        before = json.loads(meta_path.read_text())
        b_page = root / "docs" / "code" / "python" / "src_b" / "two.py.md"
        b_before_bytes = b_page.read_bytes()

        # A fresh copy of the plugin tree with RENDERER_VERSION and
        # EXTENSION_VERSION edited -- never a production toggle.
        script2 = self._copied_script(
            edit_constants={
                "_py_render.py": {'RENDERER_VERSION = "1"': 'RENDERER_VERSION = "2"'},
                "_py_locals.py": {'EXTENSION_VERSION = "1"': 'EXTENSION_VERSION = "2"'},
            },
        )
        r2 = _run(script2, "--config", str(config), "--lang", "a")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        after = json.loads(meta_path.read_text())

        self.assertEqual(after["extractors"]["a"]["renderer_version"], "2")
        self.assertEqual(after["extractors"]["a"]["extension_version"], "2")
        self.assertEqual(after["extractors"]["b"], before["extractors"]["b"],
                          "b's provenance block must stay byte-unchanged")
        self.assertEqual(b_page.read_bytes(), b_before_bytes,
                          "b's page bytes must stay unchanged by an a-only write")

        b_rows_before = sorted(
            (row for row in before["pages"] if row["owner"] == "b"),
            key=lambda row: row["doc_path"],
        )
        b_rows_after = sorted(
            (row for row in after["pages"] if row["owner"] == "b"),
            key=lambda row: row["doc_path"],
        )
        self.assertEqual(b_rows_after, b_rows_before,
                          "b's metadata rows (clause 13's 'rows') must stay byte-unchanged")

        # Positive control (false-green-test-guard): the assertion above
        # would catch a real mutation of b's rows. This developer's slice
        # cannot mutate the dispatcher to produce that RED without widening
        # scope past python.py/_py_load.py, so the control mutates a copy
        # of the "after" manifest's own b rows and proves the comparison
        # rejects it -- never assumed to fail, always observed to fail.
        mutated_b_rows = copy.deepcopy(b_rows_after)
        mutated_b_rows[0]["source_sha256"] = "0" * 64
        with self.assertRaises(AssertionError):
            self.assertEqual(mutated_b_rows, b_rows_before)

        r3 = _run(script2, "--dry-run", "--config", str(config), "--lang", "b")
        self.assertEqual(r3.returncode, 1, r3.stdout)
        payload = json.loads(r3.stdout)
        self.assertTrue(payload["metadata_drift"], "b's stale block must show as drift under its own key")


class HostileLoadingCliTests(_DisposableCliTree):
    """Import-time side-effect fixtures: the extractor must never import or
    execute the target code (ADR-0131 clause 1)."""

    _MARKERS = (
        "h1.EXECUTED",
        "h2.EXECUTED",
        "h3.EXECUTED",
        "h4.EXECUTED",
        "h5-meta.EXECUTED",
        "h5-body.EXECUTED",
        "h6.EXECUTED",
        "h7.EXECUTED",
        "h9.EXECUTED",
        "h10.EXECUTED",
        "hostile_pkg-init.EXECUTED",
        "hostile_pkg-sub.EXECUTED",
    )

    def test_no_hostile_marker_appears_after_a_real_extractor_run(self):
        script = self._copied_script()
        root = self._make_tree(
            keys={"py": {"glob": "**/*.py", "include_private": True}},
            sources={},
        )
        shutil.copytree(HOSTILE_SRC, root / "src", dirs_exist_ok=True)
        marker_dir = tempfile.TemporaryDirectory()
        self.addCleanup(marker_dir.cleanup)
        env_marker_dir = Path(marker_dir.name)

        import os

        env = {**os.environ, "CRUX_HOSTILE_MARKER_DIR": str(env_marker_dir)}
        proc = subprocess.run(
            [sys.executable, str(script), "--config", str(root / "docs" / "manifest.yml")],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for name in self._MARKERS:
            self.assertFalse((env_marker_dir / name).exists(), f"{name} must never be written")
        # The extractor did select and page every hostile source, including
        # the compiled-extension-shaped sibling's true .py file.
        manifest = json.loads((root / "docs" / "code" / "_meta" / "manifest.json").read_text())
        doc_paths = {row["doc_path"] for row in manifest["pages"]}
        self.assertIn("python/src/h9_compiled_extension.py.md", doc_paths)
        self.assertNotIn(
            "python/src/h9_compiled_extension.cpython-313-darwin.so.md", doc_paths,
            "the compiled-extension-shaped sibling is never selected",
        )

    def test_positive_controls_each_fixture_can_write_its_own_marker(self):
        """The negative assertion above is paired with proof each fixture
        genuinely writes its marker when actually imported -- never assumed
        (false-green-test-guard)."""
        marker_dir = tempfile.TemporaryDirectory()
        self.addCleanup(marker_dir.cleanup)
        env_marker_dir = Path(marker_dir.name)
        script = (
            "import sys, os\n"
            f"sys.path.insert(0, {str(HOSTILE_SRC)!r})\n"
            f"os.environ['CRUX_HOSTILE_MARKER_DIR'] = {str(env_marker_dir)!r}\n"
            "import h1_import_time_write\n"
            "try:\n"
            "    import h2_missing_dependency\n"
            "except ImportError:\n"
            "    pass\n"
            "else:\n"
            "    raise AssertionError('h2 must raise ImportError')\n"
            "import h3_decorator_side_effect\n"
            "import h4_default_argument\n"
            "import h5_class_body\n"
            "import h6_dynamic_all\n"
            "assert h6_dynamic_all.__all__ == ['exported'], h6_dynamic_all.__all__\n"
            "import h7_module_getattr\n"
            "try:\n"
            "    h7_module_getattr.anything\n"
            "except AttributeError:\n"
            "    pass\n"
            "import importlib.util as ilu\n"
            "spec = ilu.spec_from_file_location(\n"
            "    'h9_compiled_extension_direct',\n"
            f"    {str(HOSTILE_SRC / 'h9_compiled_extension.py')!r},\n"
            ")\n"
            "h9_mod = ilu.module_from_spec(spec)\n"
            "spec.loader.exec_module(h9_mod)\n"
            "import hostile_pkg.sub\n"
        )
        proc = subprocess.run([sys.executable, "-B", "-c", script], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for name in self._MARKERS:
            if name == "h10.EXECUTED":
                continue  # exercised separately below (registers a real Griffe extension)
            self.assertTrue((env_marker_dir / name).exists(), f"{name} must exist: positive control")

    def test_positive_control_h10_writes_its_marker_when_imported(self):
        marker_dir = tempfile.TemporaryDirectory()
        self.addCleanup(marker_dir.cleanup)
        env_marker_dir = Path(marker_dir.name)
        script = (
            "import sys, os\n"
            f"sys.path.insert(0, {str(HOSTILE_SRC)!r})\n"
            f"os.environ['CRUX_HOSTILE_MARKER_DIR'] = {str(env_marker_dir)!r}\n"
            "try:\n"
            "    import h10_extension_registration\n"
            "except Exception:\n"
            "    pass\n"
        )
        proc = subprocess.run([sys.executable, "-B", "-c", script], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((env_marker_dir / "h10.EXECUTED").exists())


if __name__ == "__main__":
    unittest.main()
