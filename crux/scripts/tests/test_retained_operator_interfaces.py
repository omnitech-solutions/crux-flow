"""Retired skill routes keep their shipped operator interfaces."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest


PLUGIN = Path(__file__).resolve().parents[2]
RETIRED = (
    "task-planner",
    "author-runbook",
    "visualize-run-progress",
    "trace-runtime-ops",
    "semantic-bridge",
    "agent-identity",
    "serve-llm",
)


class RetainedOperatorInterfaceTests(unittest.TestCase):
    def test_retired_entries_have_shipped_interface_routes(self):
        for name in RETIRED:
            with self.subTest(name=name):
                self.assertFalse((PLUGIN / "skills" / name).exists())

        runtime = (PLUGIN / "box" / "runtime-apis.md").read_text()
        operator = (PLUGIN / "box" / "operator-services.md").read_text()
        self.assertIn("scripts/crux/core/tracer.py", runtime)
        self.assertIn("crux.semantic_bridge", runtime)
        self.assertIn("crux.identity", runtime)
        self.assertIn("scripts/crux/runbook/__main__.py", operator)
        self.assertIn("crux.server.crux_server:app", operator)

        caller = (PLUGIN / "skills" / "call-llm" / "SKILL.md").read_text()
        lineage = (PLUGIN / "skills" / "link-adr-graph" / "SKILL.md").read_text()
        self.assertIn("box/runtime-apis.md", caller)
        self.assertNotIn("visualize-run-progress` references", lineage)

    def test_runtime_driver_runs_outside_project(self):
        runtime = (PLUGIN / "box" / "runtime-apis.md").read_text()
        driver = runtime.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime_apis.py"
            path.write_text(driver + "\n")
            env = dict(os.environ, CRUX_PLUGIN_ROOT=str(PLUGIN))
            result = subprocess.run(
                ["uv", "run", "--no-project", str(path)],
                cwd=tmp,
                env=env,
                text=True,
                capture_output=True,
                timeout=45,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("runtime APIs importable", result.stdout)


if __name__ == "__main__":
    unittest.main()
