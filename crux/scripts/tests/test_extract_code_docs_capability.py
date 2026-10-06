"""The code-doc dispatcher's interpreter floor and griffe capability check.

ADR-0131 clause 5 (rule:griffe-is-declared-in-the-dispatcher-metadata,
rule:code-doc-capability-mismatch-exits-2, rule:griffe-runs-only-at-the-pinned-version)
and the council's Q2 scoping of the check:

- a run on Python older than 3.13 exits 2, whatever the tree configures;
- the griffe import and version check runs only when the SELECTED keys (after
  --lang) include a key whose extractor type is `python`;
- each exit 2 writes its message to stderr, leaves stdout empty, and leaves every
  output byte unchanged, including under --dry-run;
- a run that selects no Python key never imports griffe;
- the dispatcher starts no second process to repair its environment.

Griffe is made absent or wrong-versioned by running the dispatcher with `-S` (no
site-packages) and, for the wrong-version case, a fake `griffe` package plus a
`griffelib-9.9.9.dist-info` built in a temporary directory on PYTHONPATH. The fake
package writes a marker file when imported, which is the positive control that the
absence assertions below can see an import when one happens.

Stdlib only. Every tree is disposable; nothing outside `crux/` is read.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "extract-code-docs.py"


def _load_dispatcher():
    spec = importlib.util.spec_from_file_location("extract_code_docs_dispatcher", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["extract_code_docs_dispatcher"] = mod
    spec.loader.exec_module(mod)
    return mod


def _tree(root: Path) -> Path:
    """A crux tree configuring a Python key and a fallback key, with prior output."""
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text('"""A module."""\n', encoding="utf-8")
    (root / "src" / "b.sh").write_text("# a shell script\n", encoding="utf-8")
    (root / "bionic" / "code" / "fallback" / "src").mkdir(parents=True)
    (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8")
    (root / "bionic" / "manifest.yml").write_text(
        'schema_version: "5"\n'
        "code:\n"
        "  extractors:\n"
        "    python:\n"
        "      extractor: python\n"
        '      glob: "src/**/*.py"\n'
        "    shell:\n"
        "      extractor: fallback\n"
        '      glob: "src/**/*.sh"\n',
        encoding="utf-8",
    )
    # Prior output a refusal must leave byte-unchanged.
    (root / "bionic" / "code" / "index.md").write_text("# prior index\n", encoding="utf-8")
    (root / "bionic" / "code" / "fallback" / "src" / "stale.sh.md").write_text(
        "# stale\n", encoding="utf-8")
    # The prior output is generated output, so it carries the ownership
    # metadata a populated root needs before any run may write there.
    (root / "bionic" / "code" / "_meta").mkdir()
    (root / "bionic" / "code" / "_meta" / "manifest.json").write_text(
        '{"pages": []}\n', encoding="utf-8")
    return root / "bionic" / "manifest.yml"


def _snapshot(root: Path) -> dict[str, bytes]:
    out = root / "bionic" / "code"
    return {str(p.relative_to(out)): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


def _fake_griffe(site: Path, version: str, marker: Path) -> None:
    """A `griffe` package that records its import, reported as griffelib `version`."""
    pkg = site / "griffe"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        "import pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text('imported')\n",
        encoding="utf-8",
    )
    dist = site / f"griffelib-{version}.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: griffelib\nVersion: {version}\n", encoding="utf-8")


def _run_isolated(config: Path, *argv: str, site: Path | None = None):
    """Run the dispatcher with no site-packages; `site` (if given) is the only import root."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    if site is not None:
        env["PYTHONPATH"] = str(site)
    return subprocess.run(
        [sys.executable, "-B", "-S", str(SCRIPT_PATH), "--config", str(config), *argv],
        capture_output=True, text=True, env=env, cwd=str(config.parent.parent),
    )


class GriffeCapabilityScopeTests(unittest.TestCase):
    """Council Q2: the check follows the SELECTED keys' extractor type."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "repo"
        self.config = _tree(self.root)
        self.site = self.base / "site"
        self.marker = self.base / "griffe-imported"

    def _assert_exit_2_untouched(self, proc, before):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(proc.stdout, "", "a capability exit prints nothing on stdout")
        self.assertIn("griffelib", proc.stderr)
        self.assertEqual(_snapshot(self.root), before, "every output byte stays unchanged")

    def test_a_fallback_only_selection_succeeds_with_griffe_absent(self):
        proc = _run_isolated(self.config, "--lang", "shell")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((self.root / "bionic" / "code" / "fallback" / "src" / "b.sh.md").is_file())

    def test_a_fallback_only_selection_succeeds_at_the_wrong_version_without_importing(self):
        _fake_griffe(self.site, "9.9.9", self.marker)
        proc = _run_isolated(self.config, "--lang", "shell", site=self.site)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.marker.exists(), "a run selecting no Python key never imports griffe")

    def test_lang_python_dry_run_at_the_wrong_version_exits_2(self):
        _fake_griffe(self.site, "9.9.9", self.marker)
        before = _snapshot(self.root)
        proc = _run_isolated(self.config, "--lang", "python", "--dry-run", site=self.site)
        self._assert_exit_2_untouched(proc, before)
        self.assertIn("9.9.9", proc.stderr)
        # Positive control for the no-import assertion above: the same fake package
        # IS imported when a Python key is selected, so the marker can appear.
        self.assertTrue(self.marker.exists())

    def test_an_unfiltered_multi_language_run_exits_2_at_the_wrong_version(self):
        _fake_griffe(self.site, "9.9.9", self.marker)
        before = _snapshot(self.root)
        proc = _run_isolated(self.config, site=self.site)
        self._assert_exit_2_untouched(proc, before)

    def test_a_python_selection_with_griffe_absent_exits_2(self):
        before = _snapshot(self.root)
        proc = _run_isolated(self.config, "--dry-run")
        self._assert_exit_2_untouched(proc, before)

    def test_positive_control_the_project_environment_passes_the_check(self):
        mod = _load_dispatcher()
        self.assertIsNone(mod._check_capabilities([("python", {"extractor": "python"})]))

    def test_the_check_keys_on_the_extractor_type_not_the_language_key(self):
        mod = _load_dispatcher()
        with mock.patch.object(mod, "_installed_griffe_version", return_value="9.9.9"):
            with self.assertRaises(SystemExit) as ctx:
                mod._check_capabilities([("py", {"extractor": "python"})])
            self.assertEqual(ctx.exception.code, 2)
            # A key named `python` whose extractor is fallback is not a Python key.
            self.assertIsNone(mod._check_capabilities([("python", {"extractor": "fallback"})]))


class NoReExecutionTests(unittest.TestCase):
    """rule:griffe-is-declared-in-the-dispatcher-metadata: no second process."""

    def test_a_capability_mismatch_starts_no_process(self):
        mod = _load_dispatcher()
        with tempfile.TemporaryDirectory() as tmp:
            config = _tree(Path(tmp) / "repo")
            spawned: list[str] = []

            def forbid(name):
                def _f(*a, **k):
                    spawned.append(name)
                    raise AssertionError(f"the dispatcher called {name}")
                return _f

            patches = [mock.patch.object(os, n, forbid(n)) for n in
                       ("execv", "execve", "execvp", "execvpe", "execl", "execlp",
                        "posix_spawn", "posix_spawnp", "system")
                       if hasattr(os, n)]
            patches += [mock.patch.object(subprocess, "Popen", forbid("subprocess.Popen")),
                        mock.patch.object(mod, "_installed_griffe_version",
                                          return_value="9.9.9")]
            for p in patches:
                p.start()
            try:
                with self.assertRaises(SystemExit) as ctx:
                    mod.main(["--config", str(config), "--dry-run"])
            finally:
                for p in reversed(patches):
                    p.stop()
            self.assertEqual(ctx.exception.code, 2)
            self.assertEqual(spawned, [])

    def test_the_dispatcher_source_names_no_process_primitive(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8")
        for needle in ("subprocess", "os.exec", "execv", "posix_spawn", "os.system"):
            self.assertNotIn(needle, text, f"the dispatcher must not call {needle}")


class InterpreterFloorTests(unittest.TestCase):
    """A run on Python older than 3.13 exits 2 before any output is read."""

    @staticmethod
    def _old_interpreter() -> str | None:
        uv = shutil.which("uv")
        if uv:
            proc = subprocess.run([uv, "python", "find", "--no-project", "3.12"],
                                  capture_output=True, text=True)
            path = proc.stdout.strip()
            if proc.returncode == 0 and path:
                return path
        for candidate in ("/usr/bin/python3",):
            if Path(candidate).is_file():
                proc = subprocess.run(
                    [candidate, "-c", "import sys; print(sys.version_info[:2] < (3, 13))"],
                    capture_output=True, text=True)
                if proc.stdout.strip() == "True":
                    return candidate
        return None

    def test_an_old_interpreter_exits_2_with_output_unchanged(self):
        old = self._old_interpreter()
        if old is None:
            self.skipTest("no Python older than 3.13 is installed on this machine")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            config = _tree(root)
            before = _snapshot(root)
            env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
            proc = subprocess.run(
                [old, "-B", "-S", str(SCRIPT_PATH), "--config", str(config), "--lang", "shell",
                 "--dry-run"],
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertEqual(proc.stdout, "")
            self.assertIn("3.13", proc.stderr)
            self.assertEqual(_snapshot(root), before)

    def test_the_floor_is_checked_in_process_before_anything_else(self):
        mod = _load_dispatcher()
        with tempfile.TemporaryDirectory() as tmp:
            config = _tree(Path(tmp) / "repo")
            with mock.patch.object(mod.sys, "version_info", (3, 12, 9, "final", 0)), \
                 mock.patch.object(mod, "load_yaml", side_effect=AssertionError("read")):
                self.assertEqual(mod.main(["--config", str(config), "--lang", "shell"]), 2)


if __name__ == "__main__":
    unittest.main()
