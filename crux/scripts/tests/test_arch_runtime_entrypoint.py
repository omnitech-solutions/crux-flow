"""Entry-point bootstrap regression (ADR-0075, PB-0072 Finding 1).

The `crux/scripts/crux/arch/runtime/__main__.py` executor declares PEP 723
`dependencies = []` and MUST run stdlib-only. A prior absolute package import
(`from crux.arch.runtime import gate, harness, reentry`) executed the top `crux`
package `__init__`, whose council → llm_caller chain does `import httpx`, so the
executor crashed with `ModuleNotFoundError: No module named 'httpx'` in any
genuinely stdlib-only environment. The dev `.venv` carries httpx (a base dep),
which MASKED the crash — every runtime test passed there.

These tests run `__main__.py --help` in a GENUINELY stdlib-only child: a
subprocess launched via `uv run --no-project` (which builds an ephemeral env
from the script's own `dependencies = []`, ignoring the surrounding project)
with `VIRTUAL_ENV` / `PYTHONPATH` / `PYTHONHOME` SCRUBBED from the child env, so
the dev venv's httpx cannot leak in. `test_env_is_genuinely_stdlib_only` proves
the scrub worked (httpx is NOT importable in that child), so `test_help_*` is
non-vacuous: it fails if the entry point ever re-acquires a third-party import.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]  # crux/scripts
MAIN = SCRIPTS / "crux" / "arch" / "runtime" / "__main__.py"

# Vars that could put the dev venv's site-packages (and thus httpx) on the
# child's import path. Scrubbing them forces `uv run --no-project` to build a
# clean ephemeral env from the script's PEP 723 `dependencies = []`.
_LEAK_VARS = ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "UV_PROJECT_ENVIRONMENT")


def _scrubbed_env() -> dict:
    return {k: v for k, v in os.environ.items() if k not in _LEAK_VARS}


@unittest.skipUnless(shutil.which("uv"), "uv not on PATH — skip (not pass)")
class EntrypointStdlibOnlyTests(unittest.TestCase):
    def _run(self, args, timeout=180):
        return subprocess.run(
            ["uv", "run", "--no-project", *args],
            capture_output=True, text=True, env=_scrubbed_env(), timeout=timeout,
        )

    def test_env_is_genuinely_stdlib_only(self):
        """Guard against a vacuous pass: prove httpx is NOT importable in the
        stdlib-only child the help test uses. If this fails, the scrub leaked and
        the help assertion would prove nothing."""
        probe = textwrap.dedent(
            """\
            # /// script
            # requires-python = ">=3.12"
            # dependencies = []
            # ///
            import importlib.util, sys
            sys.exit(0 if importlib.util.find_spec("httpx") is None else 3)
            """
        )
        with tempfile.TemporaryDirectory() as d:
            probe_path = Path(d) / "probe.py"
            probe_path.write_text(probe, encoding="utf-8")
            proc = self._run([str(probe_path)])
        self.assertEqual(
            proc.returncode, 0,
            "httpx was importable in the supposedly stdlib-only child — the env "
            f"scrub leaked, so the help test would be vacuous.\n{proc.stderr}",
        )

    def test_help_runs_under_stdlib_only_env(self):
        """The acceptance gate: `__main__.py --help` exits 0 and prints usage in
        the stdlib-only child, proving the entry-point bootstrap requires no
        third-party dependency (no httpx via the `crux` package __init__)."""
        proc = self._run([str(MAIN), "--help"])
        self.assertEqual(
            proc.returncode, 0,
            f"--help did not exit 0 in a stdlib-only env.\nstdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}",
        )
        self.assertIn("escalate-arch-runtime", proc.stdout)
        self.assertNotIn("ModuleNotFoundError", proc.stderr)


if __name__ == "__main__":
    unittest.main()
