"""Unit 6 (ADR-0075 decision 7): positive fixture-run integration.

Runs the confined harness against the cycle-authored FastAPI, Flask, and Django
fixture apps under `crux/scripts/tests/fixtures/runtime_*`, asserting the routes
and models each capture yields. Framework-dependent tests skip (not pass) when
the framework is not importable in the child's interpreter — here the test
interpreter, which the child inherits via `sys.executable`.

Run the framework cases with the deps present:
    uv run --extra arch-runtime-test python3 -m pytest crux/scripts/tests/test_arch_runtime.py
"""

import importlib
import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]  # crux/scripts
sys.path.insert(0, str(SCRIPTS))
FIXTURES = str(Path(__file__).resolve().parent / "fixtures")

harness = importlib.import_module("crux.arch.runtime.harness")


def _have(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None


def _assert_import_only(tc, r) -> None:
    """Assert the introspection was IMPORT-ONLY: it bound no listening socket and
    never entered a serve loop. This is asserted via the confinement control, not
    inferred from speed — the audithook denies `socket.*`, so any `bind`/`listen`
    a serve loop attempted would raise a ConfinementError, surface a `socket`
    denial on the child's stderr, and yield NO capture. A clean capture with no
    socket denial is therefore positive evidence that no listening socket was
    opened (the harness itself never calls `uvicorn.run` / `app.run`)."""
    tc.assertEqual(r.reason, "ok")
    tc.assertNotIn("socket", r.child_stderr,
                   "import-only boundary breached: the target attempted a socket "
                   "operation (a serve loop / listening bind) during introspection")


class FastAPIPositiveTests(unittest.TestCase):
    @unittest.skipUnless(_have("fastapi"), "fastapi not importable — skip (not pass)")
    def test_instance_captures_routes(self):
        r = harness.run(app="runtime_fastapi.app:app", search_root=FIXTURES, timeout=30)
        self.assertTrue(r.ok, f"{r.reason}\n{r.child_stderr}")
        paths = {rt["path"] for rt in r.capture["routes"]}
        self.assertIn("/users", paths)
        self.assertIn("/users/{user_id}", paths)
        methods = {(rt["path"], rt["method"]) for rt in r.capture["routes"]}
        self.assertIn(("/users", "GET"), methods)
        self.assertIn(("/users", "POST"), methods)
        _assert_import_only(self, r)

    @unittest.skipUnless(_have("fastapi"), "fastapi not importable — skip (not pass)")
    def test_factory_captures_routes(self):
        r = harness.run(app="runtime_fastapi.app:create_app", search_root=FIXTURES, timeout=30)
        self.assertTrue(r.ok, f"{r.reason}\n{r.child_stderr}")
        paths = {rt["path"] for rt in r.capture["routes"]}
        self.assertIn("/factory-health", paths)
        self.assertIn("/factory-info", paths)

    @unittest.skipUnless(_have("fastapi"), "fastapi not importable — skip (not pass)")
    def test_factory_needing_args_is_honest_no_capture(self):
        r = harness.run(app="runtime_fastapi.app:needs_args", search_root=FIXTURES, timeout=30)
        self.assertFalse(r.ok)
        self.assertIn("not zero-argument", r.child_stderr)


class FlaskPositiveTests(unittest.TestCase):
    @unittest.skipUnless(_have("flask"), "flask not importable — skip (not pass)")
    def test_instance_captures_routes(self):
        r = harness.run(app="runtime_flask.app:app", search_root=FIXTURES, timeout=30)
        self.assertTrue(r.ok, f"{r.reason}\n{r.child_stderr}")
        methods = {(rt["path"], rt["method"]) for rt in r.capture["routes"]}
        self.assertIn(("/ping", "GET"), methods)
        self.assertIn(("/submit", "POST"), methods)
        self.assertIn(("/widgets/<int:wid>", "GET"), methods)
        _assert_import_only(self, r)

    @unittest.skipUnless(_have("flask"), "flask not importable — skip (not pass)")
    def test_factory_captures_routes(self):
        r = harness.run(app="runtime_flask.app:create_app", search_root=FIXTURES, timeout=30)
        self.assertTrue(r.ok, f"{r.reason}\n{r.child_stderr}")
        paths = {rt["path"] for rt in r.capture["routes"]}
        self.assertIn("/factory", paths)


class DjangoPositiveTests(unittest.TestCase):
    @unittest.skipUnless(_have("django"), "django not importable — skip (not pass)")
    def test_settings_captures_routes_and_models(self):
        r = harness.run(settings="runtime_django.settings", search_root=FIXTURES, timeout=30)
        self.assertTrue(r.ok, f"{r.reason}\n{r.child_stderr}")
        paths = {rt["path"] for rt in r.capture["routes"]}
        self.assertIn("health/", paths)
        self.assertIn("blog/articles/", paths)                 # include-resolved
        self.assertIn("blog/articles/<int:pk>/", paths)        # recursion + prefix
        # Django routes carry method: null.
        self.assertTrue(all(rt["method"] is None for rt in r.capture["routes"]))
        # The one model, with its table and nullable flags.
        article = next(m for m in r.capture["models"] if m["name"] == "Article")
        self.assertEqual(article["table"], "blog_article")
        fields = {f["name"]: f for f in article["fields"]}
        self.assertEqual(fields["title"]["type"], "CharField")
        self.assertFalse(fields["title"]["nullable"])
        self.assertTrue(fields["published"]["nullable"])


if __name__ == "__main__":
    unittest.main()
