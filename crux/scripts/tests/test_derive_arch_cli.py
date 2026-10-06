"""Tests for derive-arch.py CLI wiring of `arch_decision_index_mode` (ADR-0062,
Fix A / PB-0069 Prompt 6).

The deriver CLI must thread the resolved `.bionic.yml` `arch_decision_index_mode`
config key through to `derive`/`dry_run` BY KEYWORD, for both the --dry-run and
write paths, defaulting to "complete" when the key is absent (no silent change
in behavior — ADR-0062 AC-6).

Imports the vendored `derive-arch.py` by path (a hyphenated script, so
importlib rather than a normal import — mirrors `test_advance_run.py`) and
monkeypatches its `derive`/`dry_run` module-level names with capturing stubs,
so this test locks the CLI's wiring without depending on the real deriver's
(heavier) behavior.
"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = REPO_ROOT / "crux" / "scripts" / "derive-arch.py"

_spec = importlib.util.spec_from_file_location("derive_arch_cli", SCRIPT)
_da = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_da)


class _Capture:
    """Stub replacing `derive`/`dry_run`; records the call it received."""

    def __init__(self, retval):
        self.retval = retval
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.retval


class DeriveArchCLIModeTests(unittest.TestCase):
    """Fix A: `arch_decision_index_mode` reaches `derive`/`dry_run` as a keyword."""

    def _tree(self, body: str | None) -> Path:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        if body is not None:
            (root / ".bionic.yml").write_text(body, encoding="utf-8")
        (root / "bionic").mkdir()
        return root

    def _patch(self):
        dry_stub = _Capture([])
        derive_stub = _Capture([])
        orig_dry, orig_derive = _da.dry_run, _da.derive
        _da.dry_run = dry_stub
        _da.derive = derive_stub

        def _restore():
            _da.dry_run = orig_dry
            _da.derive = orig_derive

        self.addCleanup(_restore)
        return dry_stub, derive_stub

    def test_dry_run_threads_curated_mode_as_keyword(self):
        root = self._tree(
            'config_version: "1"\ndocs_dir: bionic\narch_decision_index_mode: curated\n'
        )
        dry_stub, derive_stub = self._patch()
        rc = _da.main(["--dry-run", "--repo-root", str(root)])
        self.assertEqual(rc, 0)
        self.assertEqual(len(dry_stub.calls), 1)
        self.assertEqual(len(derive_stub.calls), 0)
        _args, kwargs = dry_stub.calls[0]
        self.assertIn("decision_index_mode", kwargs, "mode must be passed by keyword")
        self.assertEqual(kwargs["decision_index_mode"], "curated")

    def test_write_threads_curated_mode_as_keyword(self):
        root = self._tree(
            'config_version: "1"\ndocs_dir: bionic\narch_decision_index_mode: curated\n'
        )
        dry_stub, derive_stub = self._patch()
        rc = _da.main(["--repo-root", str(root)])
        self.assertEqual(rc, 0)
        self.assertEqual(len(derive_stub.calls), 1)
        self.assertEqual(len(dry_stub.calls), 0)
        _args, kwargs = derive_stub.calls[0]
        self.assertIn("decision_index_mode", kwargs, "mode must be passed by keyword")
        self.assertEqual(kwargs["decision_index_mode"], "curated")

    def test_no_key_defaults_to_complete_on_dry_run(self):
        root = self._tree('config_version: "1"\ndocs_dir: bionic\n')
        dry_stub, _derive_stub = self._patch()
        rc = _da.main(["--dry-run", "--repo-root", str(root)])
        self.assertEqual(rc, 0)
        _args, kwargs = dry_stub.calls[0]
        self.assertEqual(kwargs["decision_index_mode"], "complete")

    def test_no_key_defaults_to_complete_on_write(self):
        root = self._tree('config_version: "1"\ndocs_dir: bionic\n')
        _dry_stub, derive_stub = self._patch()
        rc = _da.main(["--repo-root", str(root)])
        self.assertEqual(rc, 0)
        _args, kwargs = derive_stub.calls[0]
        self.assertEqual(kwargs["decision_index_mode"], "complete")

    def test_no_config_file_defaults_to_complete(self):
        root = self._tree(None)  # no .bionic.yml at all -> discovery -> "complete"
        dry_stub, _derive_stub = self._patch()
        rc = _da.main(["--dry-run", "--repo-root", str(root)])
        self.assertEqual(rc, 0)
        _args, kwargs = dry_stub.calls[0]
        self.assertEqual(kwargs["decision_index_mode"], "complete")


if __name__ == "__main__":
    unittest.main(verbosity=2)
