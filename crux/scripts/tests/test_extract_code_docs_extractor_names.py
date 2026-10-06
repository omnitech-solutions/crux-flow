"""An extractor name is a shipped module, checked before any import.

PB-0121 Prompt 11 fix round 1, Part B, finding F1. The dispatcher took each
extractor name from `code.extractors.<key>.extractor` in the target's own
manifest and joined it onto the shipped extractors directory unchecked: an
absolute value or a `../` segment made it import and execute an arbitrary
`.py` file, under `--dry-run` as well. The rule[^shipped]: the name must match
`^[a-z][a-z0-9_]*$` and name a regular, unlinked `<name>.py` directly inside the
shipped extractors directory, and any other selected name refuses the run
before the first extractor import.

"No import" is proved by two positive-controlled discriminators, never by the
absence of a marker alone: an armed hostile module that writes a marker when
imported directly, and a spy on `importlib.util.spec_from_file_location` that
records a call on every good-name run.

[^shipped]: rule:code-doc-extractor-name-is-a-shipped-module
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # crux repo
SCRIPT_PATH = REPO_ROOT / "crux" / "scripts" / "extract-code-docs.py"
SHIPPED_EXTRACTORS = REPO_ROOT / "crux" / "scripts" / "extractors"
_NAME_RE = re.compile(r"[a-z][a-z0-9_]*")

_HOSTILE_BODY = (
    "from pathlib import Path\n"
    "Path(__file__).with_suffix('.marker').write_text('imported', encoding='utf-8')\n"
    "def discover(repo_root, config):\n    return []\n"
    "def extract(unit):\n    return None\n"
)


def _load_dispatcher(path: Path = SCRIPT_PATH, modname: str = "extract_code_docs_dispatcher"):
    spec = importlib.util.spec_from_file_location(modname, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


def _run(cwd: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *argv],
        cwd=str(cwd), capture_output=True, text=True, check=False,
    )


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(os.path.realpath(self._tmp.name))
        (self.root / "docs").mkdir()
        (self.root / "src").mkdir()
        (self.root / "src" / "alpha.py").write_text("# Alpha.\nx = 1\n", encoding="utf-8")
        # The armed hostile module lives OUTSIDE the shipped directory.
        self.evil_dir = self.root / "outside"
        self.evil_dir.mkdir()
        self.evil = self.evil_dir / "evil.py"
        self.evil.write_text(_HOSTILE_BODY, encoding="utf-8")
        self.marker = self.evil.with_suffix(".marker")

    def _cfg(self, extractors_yaml: str) -> Path:
        cfg = self.root / "docs" / "manifest.yml"
        cfg.write_text('schema_version: "5"\ncode:\n  extractors:\n' + extractors_yaml, encoding="utf-8")
        return cfg

    def test_positive_control_hostile_module_marks_when_imported(self):
        spec = importlib.util.spec_from_file_location("hostile_probe", self.evil)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertTrue(self.marker.exists(), "the fixture is armed")
        self.marker.unlink()


class _InProcess(_Base):
    """Drive main() in-process with a spy on the import mechanism."""

    def setUp(self) -> None:
        super().setUp()
        self.mod = _load_dispatcher()

    def main(self, *argv: str) -> tuple[int, str, str, list[str]]:
        calls: list[str] = []
        real = importlib.util.spec_from_file_location

        def spy(name, location=None, *a, **kw):
            calls.append(str(location))
            return real(name, location, *a, **kw)

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(importlib.util, "spec_from_file_location", spy), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = self.mod.main(list(argv))
            except SystemExit as exc:  # pragma: no cover - capability exit
                rc = int(exc.code or 0)
        return rc, out.getvalue(), err.getvalue(), calls

    def assert_refused_before_import(self, cfg: Path, key_label: str, *extra: str) -> None:
        code = self.root / "docs" / "code"
        for lane in ("write", "dry-run"):
            with self.subTest(lane=lane):
                argv = ["--config", str(cfg), *extra]
                if lane == "dry-run":
                    argv.insert(0, "--dry-run")
                rc, out, err, calls = self.main(*argv)
                self.assertEqual(rc, 1, out + err)
                self.assertEqual(calls, [], f"no extractor may be imported; got {calls}")
                self.assertFalse(self.marker.exists(), "the hostile module ran")
                self.assertFalse(code.exists(), "no output may be written")
                self.assertNotIn("Traceback", err)
                if lane == "dry-run":
                    payload = json.loads(out)
                    self.assertIn("validation_errors", payload)
                    self.assertNotIn("drift", payload)
                    paths = " ".join(e["path"] for e in payload["validation_errors"])
                    self.assertIn(key_label, paths, payload)
                else:
                    self.assertEqual(out, "")
                    self.assertIn(key_label, err)
                    self.assertIn("shipped extractor", err)

    # -- positive control for the spy: a good name IS imported -------------
    def test_positive_control_a_good_name_is_imported(self):
        cfg = self._cfg('    fb:\n      extractor: fallback\n      glob: ["src/*.py"]\n')
        rc, out, err, calls = self.main("--dry-run", "--config", str(cfg))
        self.assertIn(rc, (0, 1), out + err)
        self.assertNotIn("validation_errors", out)
        self.assertTrue(any(c.endswith("fallback.py") for c in calls), calls)

    # -- names that must refuse ---------------------------------------------
    def test_absolute_name_refuses(self):
        cfg = self._cfg(f'    bad:\n      extractor: "{self.evil.with_suffix("")}"\n')
        self.assert_refused_before_import(cfg, "bad")

    def test_dot_dot_name_refuses(self):
        rel = os.path.relpath(self.evil.with_suffix(""), SHIPPED_EXTRACTORS)
        self.assertIn("..", rel)
        cfg = self._cfg(f'    bad:\n      extractor: "{rel}"\n')
        self.assert_refused_before_import(cfg, "bad")

    def test_pattern_failing_and_helper_names_refuse(self):
        for name in ("Python", "_py_load", "__init__", "__pycache__", "typedoc"):
            with self.subTest(name=name):
                cfg = self._cfg(f'    bad:\n      extractor: "{name}"\n      glob: ["src/*.py"]\n')
                self.assert_refused_before_import(cfg, "bad")

    def test_trailing_newline_refuses(self):
        cfg = self._cfg('    bad:\n      extractor: "fallback\\n"\n      glob: ["src/*.py"]\n')
        self.assert_refused_before_import(cfg, "bad")

    def test_null_and_non_string_values_refuse(self):
        # `fallback: {extractor: null}` used to fall back to the key and run.
        for value in ("null", "5", "[fallback]", '""'):
            with self.subTest(value=value):
                cfg = self._cfg(f'    fallback:\n      extractor: {value}\n      glob: ["src/*.py"]\n')
                self.assert_refused_before_import(cfg, "fallback")

    def test_non_string_key_standing_in_for_a_name_refuses(self):
        cfg = self._cfg('    7:\n      glob: ["src/*.py"]\n')
        self.assert_refused_before_import(cfg, "7")

    def test_bad_name_selected_after_a_good_one_prevents_every_import(self):
        cfg = self._cfg(
            '    a_good:\n      extractor: fallback\n      glob: ["src/*.py"]\n'
            f'    z_bad:\n      extractor: "{self.evil.with_suffix("")}"\n'
        )
        self.assert_refused_before_import(cfg, "z_bad")

    def test_link_and_directory_entries_in_a_copy_of_the_extractors_dir_refuse(self):
        copy = self.root / "installed_extractors"
        shutil.copytree(SHIPPED_EXTRACTORS, copy, ignore=shutil.ignore_patterns("__pycache__"))
        os.symlink(str(self.evil), copy / "linked.py")
        (copy / "dirmod.py").mkdir()
        self.mod._extractors_dir = lambda: copy
        # Positive control: the copy is live, a good name in it is imported.
        good = self._cfg('    fb:\n      extractor: fallback\n      glob: ["src/*.py"]\n')
        _rc, out, _err, calls = self.main("--dry-run", "--config", str(good))
        self.assertTrue(any(c == str(copy / "fallback.py") for c in calls), calls)
        for name in ("linked", "dirmod", "missing"):
            with self.subTest(name=name):
                cfg = self._cfg(f'    bad:\n      extractor: {name}\n      glob: ["src/*.py"]\n')
                self.assert_refused_before_import(cfg, "bad")

    # -- filtered run --------------------------------------------------------
    def test_filtered_run_ignores_a_bad_name_on_an_unselected_key(self):
        cfg = self._cfg(
            '    a_good:\n      extractor: fallback\n      glob: ["src/*.py"]\n'
            f'    z_bad:\n      extractor: "{self.evil.with_suffix("")}"\n'
        )
        rc, out, err, calls = self.main("--config", str(cfg), "--lang", "a_good")
        self.assertEqual(rc, 0, out + err)
        self.assertFalse(self.marker.exists())
        self.assertFalse(any("evil" in c for c in calls), calls)
        self.assertTrue(any(c.endswith("fallback.py") for c in calls), calls)


class EntryPointTests(_Base):
    """The same refusal through the real CLI, where the old code executed the
    hostile module in both lanes."""

    def test_absolute_and_dot_dot_names_refuse_through_the_cli(self):
        rel = os.path.relpath(self.evil.with_suffix(""), SHIPPED_EXTRACTORS)
        for value in (str(self.evil.with_suffix("")), rel):
            cfg = self._cfg(f'    bad:\n      extractor: "{value}"\n')
            for dry in (False, True):
                with self.subTest(value=value, dry=dry):
                    argv = ["--config", str(cfg)]
                    if dry:
                        argv.insert(0, "--dry-run")
                    r = _run(self.root, *argv)
                    self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                    self.assertFalse(self.marker.exists(), "the hostile module ran")
                    if dry:
                        self.assertIn("validation_errors", json.loads(r.stdout))
                    else:
                        self.assertEqual(r.stdout, "")


class HugeNameLabelIsBoundedTests(_Base):
    """A huge extractor value in the target manifest is quoted in the refusal
    only up to a bound, so it cannot flood stderr or the JSON payload."""

    HUGE = "A" * 20000

    def test_helper_label_is_truncated_with_an_ellipsis(self):
        mod = _load_dispatcher(modname="extract_code_docs_bound_probe")
        with self.assertRaises(mod.ExtractionRefusal) as ctx:
            mod.resolve_extractor_path(self.HUGE)
        self.assertLessEqual(len(ctx.exception.path), 100, ctx.exception.path[:200])
        self.assertTrue(ctx.exception.path.endswith("..."), ctx.exception.path[-20:])
        # Positive control: a short bad name is quoted whole.
        with self.assertRaises(mod.ExtractionRefusal) as ctx:
            mod.resolve_extractor_path("Python")
        self.assertEqual(ctx.exception.path, "'Python'")

    def test_huge_name_refusal_through_the_cli_is_bounded(self):
        cfg = self._cfg(f'    bad:\n      extractor: "{self.HUGE}"\n')
        for dry in (False, True):
            with self.subTest(dry=dry):
                argv = ["--config", str(cfg)]
                if dry:
                    argv.insert(0, "--dry-run")
                r = _run(self.root, *argv)
                self.assertEqual(r.returncode, 1, r.stdout[:500] + r.stderr[:500])
                channel = r.stdout if dry else r.stderr
                self.assertIn("code.extractors.bad", channel)
                self.assertLess(len(channel), 1000, channel[:300])
                if dry:
                    self.assertIn("validation_errors", json.loads(r.stdout))


class ShippedExtractorsLoadTests(unittest.TestCase):
    def test_every_pattern_matching_child_loads_through_a_symlinked_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "plugin_scripts"
            os.symlink(str(SCRIPT_PATH.parent), link)
            mod = _load_dispatcher(link / "extract-code-docs.py")
            names = sorted(
                p.stem for p in SHIPPED_EXTRACTORS.iterdir()
                if p.suffix == ".py" and _NAME_RE.fullmatch(p.stem)
            )
            self.assertIn("python", names)
            self.assertIn("fallback", names)
            for name in names:
                with self.subTest(name=name):
                    loaded = mod.load_extractor_module(name)
                    self.assertTrue(hasattr(loaded, "discover") and hasattr(loaded, "extract"))
                    self.assertEqual(
                        Path(loaded.__file__).resolve(), (SHIPPED_EXTRACTORS / f"{name}.py").resolve()
                    )


class ShadowModulesStayUnimportedTests(_Base):
    """Helper and dependency shadows in the target tree and the working
    directory are never imported by a Python-extractor run."""

    def test_shadow_helper_and_dependency_modules_stay_unimported(self):
        try:
            import griffe  # noqa: F401
        except ImportError:
            self.skipTest("griffe is not importable in this interpreter")
        cwd = self.root / "cwd"
        cwd.mkdir()
        shadows = []
        for where in (self.root, self.root / "src", cwd):
            for name in ("_py_load", "griffe"):
                p = where / f"{name}.py"
                p.write_text(
                    "from pathlib import Path\n"
                    "Path(__file__).with_suffix('.marker').write_text('x', encoding='utf-8')\n",
                    encoding="utf-8",
                )
                shadows.append(p)
        # Positive control: each shadow marks when imported directly.
        probe = shadows[0]
        spec = importlib.util.spec_from_file_location("shadow_probe", probe)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        self.assertTrue(probe.with_suffix(".marker").exists())
        probe.with_suffix(".marker").unlink()
        cfg = self._cfg('    python:\n      glob: ["src/alpha.py"]\n')
        r = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--config", str(cfg)],
            cwd=str(cwd), capture_output=True, text=True, check=False,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for p in shadows:
            self.assertFalse(p.with_suffix(".marker").exists(), f"{p} was imported")


if __name__ == "__main__":
    unittest.main()
