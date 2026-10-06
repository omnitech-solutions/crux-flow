"""Smoke tests for the ported `crux-core` bundle (PB-0004 Prompt 1).

Verifies that the newly ported subpackages import cleanly and expose the
public surface declared in their `__init__.py` files.

DOES NOT make any API calls. Imports + instance construction only.

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
    import yaml  # noqa: F401  (several crux.* modules import yaml at module level)
except ImportError as exc:
    raise unittest.SkipTest(f"LLM SDK deps unavailable (uv lane required): {exc}")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # crux repo root
SCRIPTS_DIR = REPO_ROOT / "crux" / "scripts"
PACKAGE_DIR = SCRIPTS_DIR / "crux"


def _ensure_on_path():
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))


class TestCruxCoreImports(unittest.TestCase):
    """The new crux-core packages import cleanly."""

    @classmethod
    def setUpClass(cls):
        _ensure_on_path()

    def test_import_llm(self):
        # call-llm: re-export of core via crux.llm. `Provider` is gone —
        # one gateway, no per-provider dispatch (ADR-0087).
        from crux.llm import ModelConfig, call_gateway, call_model  # noqa: F401

        self.assertTrue(callable(call_model))
        self.assertTrue(callable(call_gateway))
        self.assertTrue(inspect.isclass(ModelConfig))

    def test_import_core_tracer(self):
        from crux.core import Tracer, get_tracer, log_reasoning  # noqa: F401

        self.assertTrue(inspect.isclass(Tracer))
        self.assertTrue(callable(get_tracer))
        self.assertTrue(callable(log_reasoning))

    def test_import_srde(self):
        from crux.srde import (  # noqa: F401
            ContextResolver,
            PatternResolver,
            SelfResolvingDissentEngine,
            create_srde,
        )

        self.assertTrue(inspect.isclass(SelfResolvingDissentEngine))
        srde = create_srde()
        self.assertIsInstance(srde, SelfResolvingDissentEngine)
        stats = srde.get_stats()
        self.assertIn("total_attempts", stats)

    def test_import_semantic_bridge(self):
        from crux.semantic_bridge import (  # noqa: F401
            BridgeStatistics,
            SemanticBridge,
            create_semantic_bridge,
        )

        bridge = create_semantic_bridge()
        self.assertIsInstance(bridge, SemanticBridge)
        report = bridge.get_coverage_report()
        self.assertIn("total_probes", report)

    def test_import_identity(self):
        from crux.identity import (  # noqa: F401
            DEFAULT_REGISTRY_DIR,
            Identity,
            KnowledgeStore,
            Learner,
            get_or_create_identity,
        )

        self.assertTrue(inspect.isclass(Identity))
        self.assertTrue(inspect.isclass(KnowledgeStore))
        self.assertTrue(callable(get_or_create_identity))

    def test_import_task_planner(self):
        from crux.task_planner import (  # noqa: F401
            Task,
            TaskPlan,
            TaskPriority,
            TaskStatus,
            create_fallback_plan,
            decompose_goal,
            load_plan,
            save_plan,
        )

        self.assertTrue(inspect.isclass(Task))
        plan = create_fallback_plan("test goal")
        self.assertIsInstance(plan, TaskPlan)
        self.assertGreaterEqual(len(plan.tasks), 1)

    def test_import_runbook(self):
        from crux.runbook import (  # noqa: F401
            DEFAULT_TARGET_PROMPTS,
            PromptBlock,
            generate_template_only_prompts,
            render_markdown,
        )

        prompts = generate_template_only_prompts("test goal", 10)
        self.assertEqual(len(prompts), 10)


class TestRenameInvariants(unittest.TestCase):
    """Source files must not retain legacy identifiers.

    (Name-level content policy is enforced separately by
    check-public-release-content.py.)
    """

    @classmethod
    def setUpClass(cls):
        _ensure_on_path()

    CHECKED_FILES = [
        "srde/srde.py",
        "srde/__init__.py",
        "semantic_bridge/bridge.py",
        "semantic_bridge/__init__.py",
        "identity/identity.py",
        "identity/__init__.py",
        "task_planner/task_planner.py",
        "task_planner/__init__.py",
        "runbook/runbook.py",
        "runbook/__init__.py",
    ]

    BAD_PATTERNS = [
        # No legacy env var names.
        re.compile(r"\bMEESEEKS_[A-Z_]+"),
        # No legacy dotenv API path.
        re.compile(r"\bAPI_CONFIG\.env\b"),
        # No legacy import-root references.
        re.compile(r"\btools_core\b"),
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


class TestTracerEnvVar(unittest.TestCase):
    """core/tracer.py must recognize the CRUX_TRACES_DIR env var.

    Ported from the deleted test_crux_spin.py (G7 — TestIdentifierSurface).
    This is a source-level assertion: the env var name must appear in
    tracer.py so that the module actually honours it (vs silently ignoring it).
    """

    def test_tracer_module_recognizes_crux_traces_dir(self):
        tracer_src = (PACKAGE_DIR / "core" / "tracer.py").read_text(encoding="utf-8")
        self.assertIn(
            "CRUX_TRACES_DIR",
            tracer_src,
            "core/tracer.py must recognize CRUX_TRACES_DIR.",
        )


class TestGatewayKeyTransport(unittest.TestCase):
    """The gateway API key travels in the Authorization header, never the URL
    query string — httpx error text embeds the full URL, and council consumers
    persist those error strings, so a query-string key leaks into logs on any
    non-2xx (security review S2, PB-0028).

    The property predates the gateway and must survive it: the transport moved
    from three per-provider clients to one, and this is the assertion that says
    the move did not put a credential back in a URL.
    """

    def test_key_in_header_not_url(self):
        import os
        from unittest import mock

        from crux.core import llm_caller

        recorded = {}

        class _FakeResponse:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"finish_reason": "stop",
                                     "message": {"content": "ok"}}]}

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, url, json=None, headers=None, **kw):
                recorded["url"] = url
                recorded["headers"] = headers or {}
                return _FakeResponse()

        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key-123"}):
            with mock.patch.object(llm_caller.httpx, "Client", _FakeClient):
                out = llm_caller.call_model("gemini-3.1-pro-preview", "hi")
        self.assertEqual(out, "ok")
        self.assertNotIn("key=", recorded["url"])
        self.assertNotIn("test-key-123", recorded["url"])
        self.assertEqual(recorded["headers"].get("Authorization"), "Bearer test-key-123")


if __name__ == "__main__":
    unittest.main()
