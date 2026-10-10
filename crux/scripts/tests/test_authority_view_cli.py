"""The authority-view command: the shipped entry point skills call for authority
state and retained-evidence pruning. Every test drives the CLI as a subprocess."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "authority-view.py"
HOLDING = ("docs/promptbooks/runs/PB-0001-demo/evidence/implementation-cycles/"
           "retained-repositories")


def _tree(root: Path) -> None:
    (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: docs\n', encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs/manifest.yml").write_text(
        'schema_version: "5"\nconcerns_enabled: [adrs]\n', encoding="utf-8")


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args, "--repo-root", str(root)],
                          capture_output=True, text=True, timeout=300)


class AuthorityViewCliTests(unittest.TestCase):
    def test_retained_roots_lists_a_holding_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root)
            (root / "docs/promptbooks/runs/PB-0002-plain/evidence").mkdir(parents=True)
            self.assertEqual({"roots": []}, json.loads(_run(root, "retained-roots").stdout))
            (root / HOLDING).mkdir(parents=True)
            result = _run(root, "retained-roots")
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual({"roots": [HOLDING]}, json.loads(result.stdout))

    def test_retained_classifies_each_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root)
            (root / HOLDING / "repo").mkdir(parents=True)
            result = _run(root, "retained", f"{HOLDING}/repo", "docs/manifest.yml")
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(
                {"paths": [{"path": f"{HOLDING}/repo", "retained": True},
                           {"path": "docs/manifest.yml", "retained": False}]},
                json.loads(result.stdout))

    def test_a_traversal_path_is_a_refusal_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root)
            result = _run(root, "retained", "docs/../../etc")
            self.assertEqual(1, result.returncode, result.stderr)
            self.assertEqual({"authority": "none", "limit": "retained-evidence-layout-refused"},
                             json.loads(result.stdout))

    def test_state_of_a_tree_without_git_or_migration_is_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root)
            result = _run(root, "state")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual("original", report["state"])
            self.assertEqual([], report["publications"])

    def test_a_missing_tree_is_an_environment_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), "retained-roots")
            self.assertEqual(2, result.returncode)
            self.assertEqual("", result.stdout)
            self.assertIn("authority-view-unavailable", result.stderr)


if __name__ == "__main__":
    unittest.main()
