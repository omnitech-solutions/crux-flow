"""Smoke tests for the `crux-infrastructure` bundle
(PB-0004 Prompt 6).

Verifies that the two infrastructure skills' modules import cleanly and
that legacy identifiers (MEESEEKS_*, tools_core.*, API_CONFIG) do not
appear in the source files.
Also verifies the FastAPI app instantiates with the expected routes
without binding a real port, that the spawner targets `.crux-runtime/`
+ crux's runtime directory (not the repo-root `.crux` config-file
namespace per ADR-0035 §4), and that the spawner refuses to deploy over
a target path it did not create.

DOES NOT make any API calls, bind ports, or write into other repos.

Stdlib unittest only.
"""

from __future__ import annotations

import inspect
import re
import sys
import unittest
from pathlib import Path

try:
    import httpx  # noqa: F401
except ImportError as exc:
    raise unittest.SkipTest(f"LLM SDK deps unavailable (uv lane required): {exc}")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # crux repo root
SCRIPTS_DIR = REPO_ROOT / "crux" / "scripts"
PACKAGE_DIR = SCRIPTS_DIR / "crux"
SKILLS_DIR = REPO_ROOT / "crux" / "skills"


def _ensure_on_path():
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))


class TestCruxInfrastructureImports(unittest.TestCase):
    """The new crux-infrastructure packages import cleanly."""

    @classmethod
    def setUpClass(cls):
        _ensure_on_path()

    def test_import_spawner(self):
        from crux.spawner import (  # noqa: F401
            RepoAnalysis,
            RepoAnalyzer,
            Spawner,
            SpawnTargetConflictError,
            spawn_crux,
        )

        self.assertTrue(callable(spawn_crux))
        self.assertTrue(inspect.isclass(Spawner))
        self.assertTrue(inspect.isclass(RepoAnalyzer))
        self.assertTrue(inspect.isclass(RepoAnalysis))
        self.assertTrue(inspect.isclass(SpawnTargetConflictError))

    def test_spawner_targets_crux_runtime_dir(self):
        """Spawner must default to `.crux-runtime/` (not `.crux/`, which is
        the repo-root config FILE per ADR-0032/ADR-0035)."""
        from crux.spawner import Spawner

        spawner = Spawner(target_repo=REPO_ROOT, analyze_repo=False)
        # Default crux_dir is .crux-runtime
        self.assertEqual(spawner.crux_dir, ".crux-runtime")
        # target_path resolves to <repo>/.crux-runtime/
        self.assertEqual(spawner.target_path, (REPO_ROOT / ".crux-runtime").resolve())

    def test_spawn_crux_default_dir_is_crux_runtime(self):
        """The convenience function and CLI --dir default match the class."""
        import inspect as _inspect

        from crux.spawner import spawn_crux

        sig = _inspect.signature(spawn_crux)
        self.assertEqual(sig.parameters["crux_dir"].default, ".crux-runtime")

    def test_spawner_runtime_source_is_crux(self):
        """Spawner.SOURCE_RUNTIME_DIR must point at crux's runtime."""
        from crux.spawner.crux_spawner import (
            CRUX_PLUGIN_ROOT,
            Spawner,
        )

        self.assertEqual(Spawner.SOURCE_RUNTIME_DIR, "scripts/crux")
        runtime_path = CRUX_PLUGIN_ROOT / Spawner.SOURCE_RUNTIME_DIR
        self.assertTrue(
            runtime_path.exists(),
            f"Expected runtime source at {runtime_path}",
        )
        # Sanity: this is crux's own runtime — must contain core/
        self.assertTrue((runtime_path / "core").is_dir())

    def test_import_server(self):
        # The server import requires FastAPI (declared as the `infrastructure`
        # optional-dependency). If the env wasn't synced with that extra, this
        # test should skip rather than fail loudly.
        try:
            from crux.server import app  # noqa: F401
        except ImportError as exc:
            self.skipTest(f"crux-infrastructure extra not installed: {exc}")
        self.assertIsNotNone(app)

    def test_server_app_has_expected_routes(self):
        try:
            from crux.server import app
        except ImportError as exc:
            self.skipTest(f"crux-infrastructure extra not installed: {exc}")

        # Routes are FastAPI route objects with `.path` attributes.
        paths = {getattr(route, "path", None) for route in app.routes}
        for expected in ("/health", "/models", "/chat"):
            self.assertIn(expected, paths, f"missing route: {expected}")

    def test_server_health_endpoint_via_testclient(self):
        try:
            from crux.server import app
            from fastapi.testclient import TestClient
        except ImportError as exc:
            self.skipTest(f"crux-infrastructure extra not installed: {exc}")

        client = TestClient(app)
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_testclient_binds_httpx2(self):
        """Starlette's TestClient prefers `httpx2` and falls back to plain
        `httpx` with a StarletteDeprecationWarning when it is missing. The
        `infrastructure` extra declares `httpx2`, so the client must bind to
        it. The module check is deterministic; a warning capture would depend
        on which test imported `starlette.testclient` first."""
        try:
            import fastapi  # noqa: F401
            import starlette.testclient as starlette_testclient
        except ImportError as exc:
            self.skipTest(f"crux-infrastructure extra not installed: {exc}")

        self.assertEqual(
            starlette_testclient.httpx.__name__,
            "httpx2",
            msg=(
                "FastAPI is installed without httpx2. Re-sync the environment "
                "with the extra that provided FastAPI: `uv sync --extra "
                "infrastructure` or `uv sync --extra arch-runtime-test`."
            ),
        )

    def test_server_chat_refuses_an_image_model(self):
        """`/chat` returns only text, so an image model must be an error, not
        an empty 200. The refusal fires before any credential read or request."""
        try:
            from crux.server import app
            from fastapi.testclient import TestClient
        except ImportError as exc:
            self.skipTest(f"crux-infrastructure extra not installed: {exc}")

        client = TestClient(app)
        resp = client.post("/chat", json={"message": "draw a cat",
                                          "model": "gpt-image-2.5-sunburst"})
        self.assertNotEqual(resp.status_code, 200)
        detail = resp.json().get("detail", "")
        self.assertIn("gpt-image-2.5-sunburst", detail)
        self.assertIn("image_generation", detail)


class TestRenameInvariants(unittest.TestCase):
    """Source files must not retain legacy identifiers.

    (Name-level content policy is enforced separately by
    check-public-release-content.py.)
    """

    @classmethod
    def setUpClass(cls):
        _ensure_on_path()

    CHECKED_FILES = [
        "spawner/__init__.py",
        "spawner/__main__.py",
        "spawner/crux_spawner.py",
        "server/__init__.py",
        "server/crux_server.py",
        "runbook/__init__.py",
        "runbook/__main__.py",
        "runbook/runbook.py",
    ]

    BAD_PATTERNS = [
        # No legacy env var names.
        re.compile(r"\bMEESEEKS_[A-Z_]+"),
        # No legacy dotenv API path.
        re.compile(r"\bAPI_CONFIG\.env\b"),
        # No legacy import-root references.
        re.compile(r"\btools_core\b"),
        # No references to the decommissioned spin engine.
        re.compile(r"\bcrux\.spin\b"),
    ]

    def test_no_stale_legacy_identifiers_in_python(self):
        for rel in self.CHECKED_FILES:
            path = PACKAGE_DIR / rel
            self.assertTrue(path.exists(), f"missing source file: {path}")
            txt = path.read_text()
            for pattern in self.BAD_PATTERNS:
                m = pattern.search(txt)
                self.assertIsNone(
                    m,
                    f"{rel}: forbidden identifier {pattern.pattern!r} matched: " f"{m.group(0) if m else ''}",
                )


class TestSkillFilesPresent(unittest.TestCase):
    """The runtime installer skill remains available."""

    EXPECTED_SKILLS = [
        "install-runtime",
    ]

    def test_all_skills_present(self):
        for name in self.EXPECTED_SKILLS:
            skill_path = SKILLS_DIR / name / "SKILL.md"
            self.assertTrue(skill_path.exists(), f"missing {skill_path}")

    def test_skill_frontmatter_has_required_fields(self):
        # owner/version/status were pruned from the metadata contract by
        # ADR-0092; the remaining required keys are name/description/tags/bundles
        # (+ risk_level under metadata).
        literal_keys = [
            "name:",
            "description:",
            "tags:",
            "bundles:",
            "risk_level:",
        ]
        for name in self.EXPECTED_SKILLS:
            skill_path = SKILLS_DIR / name / "SKILL.md"
            body = skill_path.read_text()
            for key in literal_keys:
                self.assertIn(key, body, f"{name}/SKILL.md missing {key!r}")

    def test_spawner_skill_documents_crux_runtime_dir(self):
        """install-runtime SKILL.md must reference `.crux-runtime/`
        (per ADR-0035 §4)."""
        body = (SKILLS_DIR / "install-runtime" / "SKILL.md").read_text()
        self.assertIn(".crux-runtime/", body)


class TestSpawnerTargetRefusal(unittest.TestCase):
    """The spawner refuses to deploy over anything it did not create
    (ADR-0035 §4)."""

    @classmethod
    def setUpClass(cls):
        _ensure_on_path()

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _spawner(self, crux_dir=".crux-runtime"):
        from crux.spawner import Spawner

        return Spawner(target_repo=self.repo, crux_dir=crux_dir, analyze_repo=False)

    def test_target_path_is_file_refused(self):
        """A FILE at the target path (e.g. a `.crux` config file targeted
        via --dir) is a named conflict, never overwritten."""
        from crux.spawner import SpawnTargetConflictError

        (self.repo / ".crux").write_text('config_version: "1"\n')
        spawner = self._spawner(crux_dir=".crux")
        with self.assertRaises(SpawnTargetConflictError) as ctx:
            spawner.spawn()
        self.assertIn("FILE", str(ctx.exception))
        # The config file must be untouched.
        self.assertTrue((self.repo / ".crux").is_file())

    def test_foreign_nonempty_dir_refused_with_migration_hint(self):
        from crux.spawner import SpawnTargetConflictError

        target = self.repo / ".crux-runtime"
        target.mkdir()
        (target / "something-else.txt").write_text("not ours\n")
        with self.assertRaises(SpawnTargetConflictError) as ctx:
            self._spawner().spawn()
        self.assertIn("move it aside", str(ctx.exception))
        # The foreign content must be untouched.
        self.assertEqual((target / "something-else.txt").read_text(), "not ours\n")

    def test_empty_dir_and_respawn_allowed(self):
        """An empty pre-existing dir is fine; a re-spawn over our own
        marker-bearing tree is the in-place upgrade path."""
        from crux.spawner import Spawner

        (self.repo / ".crux-runtime").mkdir()
        spawner = self._spawner()
        spawned = spawner.spawn()
        self.assertTrue((spawned / Spawner.SPAWNER_MARKER).exists())
        # Re-spawn over our own tree must not raise.
        self._spawner().spawn()

    def test_legacy_spawned_tree_without_marker_allowed(self):
        """Pre-marker spawned trees (AGENTS.md + scripts/crux/) are ours."""
        target = self.repo / ".crux-runtime"
        (target / "scripts" / "crux").mkdir(parents=True)
        (target / "AGENTS.md").write_text("# AGENTS.md — crux for x\n")
        self._spawner()._refuse_foreign_target()  # must not raise

    def test_generated_agents_md_calls_crux_env_a_cli(self):
        """The generated AGENTS.md must call crux-env a CLI, not a skill."""
        spawned = self._spawner().spawn()
        body = (spawned / "AGENTS.md").read_text()
        self.assertIn("`crux-env` CLI", body)
        self.assertNotIn("`crux-env` skill", body)

    def test_cli_dir_default_is_crux_runtime(self):
        """The CLI --dir default matches the class/function default."""
        src = (PACKAGE_DIR / "spawner" / "crux_spawner.py").read_text()
        self.assertIn('default=".crux-runtime"', src)
        self.assertNotRegex(src, r'default="\.crux"')


    def test_symlink_target_refused(self):
        """A symlinked target would redirect writes outside the declared
        directory; the refusal fires before any write."""
        from crux.spawner import SpawnTargetConflictError

        real = self.repo / "elsewhere"
        real.mkdir()
        (self.repo / ".crux-runtime").symlink_to(real)
        with self.assertRaises(SpawnTargetConflictError) as ctx:
            self._spawner().spawn()
        self.assertIn("SYMLINK", str(ctx.exception))
        self.assertEqual(list(real.iterdir()), [])

    def test_escaping_crux_dir_refused(self):
        """An absolute or ..-traversing crux_dir relocates the write
        boundary; the constructor refuses."""
        from crux.spawner import SpawnTargetConflictError

        with self.assertRaises(SpawnTargetConflictError):
            self._spawner(crux_dir="../outside")
        with self.assertRaises(SpawnTargetConflictError):
            self._spawner(crux_dir="/tmp/abs-target")
        with self.assertRaises(SpawnTargetConflictError):
            self._spawner(crux_dir="sub/../../outside")
        with self.assertRaises(SpawnTargetConflictError):
            self._spawner(crux_dir=".")

    def test_spawned_tree_includes_crux_env(self):
        """The substrate imports `crux_env` at module load; a spawned tree
        without scripts/crux_env.py cannot `import crux.*` at all."""
        spawner = self._spawner()
        spawner.spawn()
        self.assertTrue(
            (self.repo / ".crux-runtime" / "scripts" / "crux_env.py").is_file()
        )
        self.assertTrue(
            (self.repo / ".crux-runtime" / "scripts" / "crux" / "__init__.py").is_file()
        )

class TestLegacySetupNotShipped(unittest.TestCase):
    """A legacy setup skill is intentionally NOT shipped."""

    def test_no_crux_setup_skill(self):
        self.assertFalse(
            (SKILLS_DIR / "crux-setup").exists(),
            "crux-setup should NOT exist — superseded by crux-env",
        )

    def test_no_crux_setup_module(self):
        self.assertFalse(
            (PACKAGE_DIR / "setup").exists(),
            "crux/setup/ should NOT exist — superseded by crux_env",
        )


if __name__ == "__main__":
    unittest.main()
