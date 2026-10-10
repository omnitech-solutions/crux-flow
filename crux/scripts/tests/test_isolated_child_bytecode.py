"""Isolated (-I) child interpreters must not write bytecode into the plugin tree.

`-I` makes a child ignore PYTHONDONTWRITEBYTECODE, so a child spawned from a staged
release tree compiled `crux/scripts/__pycache__/` into it and broke the release
byte-identity gate. Each test copies the vendored scripts, drives the real spawn
path from a parent that itself writes no bytecode, and asserts the copy holds no
`__pycache__`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent


def _copy_scripts(root: Path) -> Path:
    target = root / "crux" / "scripts"
    shutil.copytree(SCRIPTS, target, ignore=shutil.ignore_patterns("tests", "__pycache__", "*.pyc"))
    return target


def _caches(tree: Path) -> list[str]:
    return sorted(str(p.relative_to(tree)) for p in tree.rglob("__pycache__"))


class IsolatedChildBytecodeTests(unittest.TestCase):
    def _drive(self, body: str) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="crux-isolated-child-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        scripts = _copy_scripts(tmp)
        repo = tmp / "repo"
        repo.mkdir()
        self.assertEqual(_caches(scripts), [])
        driver = textwrap.dedent("""
            import sys
            from pathlib import Path
            sys.path.insert(0, sys.argv[1])
            repo = Path(sys.argv[2])
        """) + textwrap.dedent(body)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", CRUX_HOME=str(tmp / "home"))
        proc = subprocess.run([sys.executable, "-B", "-c", driver, str(scripts), str(repo)],
                              capture_output=True, text=True, timeout=180, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertIn("spawned", proc.stdout)
        return scripts

    def test_migration_revision_reader_child_writes_no_bytecode(self):
        scripts = self._drive("""
            import implementation_migration as m
            try:
                m._read_revision(repo, "HEAD")
            except Exception:
                pass
            print("spawned")
        """)
        self.assertEqual(_caches(scripts), [])

    def test_historical_worker_child_writes_no_bytecode(self):
        scripts = self._drive("""
            import implementation_approval as a
            binding = {"revision": {"path": "fixture", "sha256": "a" * 64}}
            try:
                a._historical_worker(repo, repo / "run.yaml", binding, migration=False)
            except Exception:
                pass
            print("spawned")
        """)
        self.assertEqual(_caches(scripts), [])


if __name__ == "__main__":
    unittest.main()
