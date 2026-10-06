"""Test suite for read-news.py (ADR-0040 §2).

Covers:
  (a) Key-absence → exit 2 + env_not_configured JSON envelope.
  (b) max-results cap > 20 → exit 1 + usage_error JSON envelope.
  (c) Success path → query echoed, results passed through, key NOT in output.
  (d) Non-2xx → exit 1 api_error, key NOT in output.
  (e) PEP 723 block presence (the §10.A scan picks it up automatically via the
      uv-run shebang test; this test verifies the block has the required
      content independently).
  (f) Capability lane: httpx absent → exit 2 + capability_error JSON envelope.
  (g) Scrub-boundary: key straddling the 500-char truncation in api_error body.
  (h) Top-level-catch: unexpected exception → exit 1 + internal envelope, no
      traceback, no raw key.

Uses stdlib unittest + subprocess isolation for the key-absence lane (follows
the CruxEnvTestCase pattern from test_crux_env.py).  For HTTP tests, uses
httpx.MockTransport when available, guarded by the HAVE_HTTPX skip-unless
pattern from test_crux_llm_router.py.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent          # crux/scripts/
READ_NEWS = SCRIPTS_DIR / "read-news.py"

# ---------------------------------------------------------------------------
# httpx availability guard (mirrors test_crux_llm_router.py pattern)
# ---------------------------------------------------------------------------
try:
    import httpx
    HAVE_HTTPX = True
except ImportError:
    HAVE_HTTPX = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_script(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run read-news.py with the given args in an isolated environment."""
    full_env = os.environ.copy()
    # Strip any real Perplexity key from the environment to keep tests isolated.
    full_env.pop("PERPLEXITY_API_KEY", None)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(READ_NEWS), *args],
        capture_output=True,
        text=True,
        env=full_env,
    )


class ReadNewsBase(unittest.TestCase):
    """Base class: each test gets a fresh CRUX_HOME tempdir with no key set."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="crux-read-news-test-")
        self.env = {"CRUX_HOME": self.tmpdir}

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# (a) Key-absence lane: exit 2 + env_not_configured envelope
# ---------------------------------------------------------------------------

class TestKeyAbsence(ReadNewsBase):
    """Absent PERPLEXITY_API_KEY → exit 2 with env_not_configured JSON."""

    def test_exit_code_is_2_when_key_absent(self):
        result = run_script("--query", "test query", env=self.env)
        self.assertEqual(
            result.returncode, 2,
            f"expected exit 2, got {result.returncode}; "
            f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )

    def test_stdout_contains_env_not_configured_json(self):
        result = run_script("--query", "test query", env=self.env)
        self.assertEqual(result.returncode, 2)
        data = json.loads(result.stdout)
        self.assertEqual(
            data.get("error"),
            "env_not_configured",
            f"expected error=env_not_configured, got {data!r}",
        )
        self.assertIn("PERPLEXITY_API_KEY", data.get("missing", []))

    def test_stderr_contains_remediation_with_crux_env_set(self):
        result = run_script("--query", "test query", env=self.env)
        self.assertEqual(result.returncode, 2)
        self.assertIn("crux-env.py", result.stderr)
        self.assertIn("PERPLEXITY_API_KEY", result.stderr)


# ---------------------------------------------------------------------------
# (b) max-results cap rejection > 20 → exit 1 + usage_error JSON envelope
# ---------------------------------------------------------------------------

class TestMaxResultsCap(ReadNewsBase):
    """--max-results > 20 → exit 1 + usage_error JSON envelope (cap is a contract).

    Exit-code convention (per repo):
      exit 2 = environment / capability problem (caller should fall back)
      exit 1 = real failure WITH a JSON envelope (caller should inspect it)
    The cap is a usage error — the caller passed a bad argument — so exit 1
    with a JSON envelope is correct; exit 2 would signal a fall-back when none
    is warranted.
    """

    def test_max_results_21_exit_code_is_1(self):
        result = run_script("--query", "any query", "--max-results", "21", env=self.env)
        self.assertEqual(
            result.returncode, 1,
            f"expected exit 1 for --max-results 21 (usage_error), got {result.returncode}; "
            f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )

    def test_max_results_21_has_usage_error_json_envelope(self):
        result = run_script("--query", "any query", "--max-results", "21", env=self.env)
        self.assertEqual(result.returncode, 1)
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail(
                f"expected JSON on stdout for usage_error, got {result.stdout!r}"
            )
        self.assertEqual(
            data.get("error"),
            "usage_error",
            f"expected error=usage_error, got {data!r}",
        )
        self.assertIn(
            "max_results",
            str(data.get("detail", "")),
            f"expected 'max_results' in detail, got {data!r}",
        )

    def test_max_results_20_is_accepted_before_key_check(self):
        # The cap check happens before key check; a missing key yields exit 2
        # for a different reason.  What matters: we do NOT get the usage-error
        # path; we get env_not_configured instead.
        result = run_script("--query", "any query", "--max-results", "20", env=self.env)
        # Should reach the key-check stage → exit 2 env_not_configured
        data = json.loads(result.stdout)
        self.assertEqual(data.get("error"), "env_not_configured")

    def test_max_results_1_is_accepted_before_key_check(self):
        result = run_script("--query", "q", "--max-results", "1", env=self.env)
        data = json.loads(result.stdout)
        self.assertEqual(data.get("error"), "env_not_configured")

    def test_max_results_error_does_not_contain_key(self):
        # Plant a key in the env just in case; the cap check fires before the
        # key is read, so the key must never appear in output.
        env_with_key = {**self.env, "PERPLEXITY_API_KEY": "sk-test-secret-1234"}
        result = run_script("--query", "q", "--max-results", "99", env=env_with_key)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("sk-test-secret-1234", result.stdout)
        self.assertNotIn("sk-test-secret-1234", result.stderr)


# ---------------------------------------------------------------------------
# (c) + (d) HTTP-level tests — require httpx
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class TestHTTPSuccess(ReadNewsBase):
    """Success path with a mocked transport: query echoed, results forwarded."""

    TEST_KEY = "pplx-test-key-success-00000000"
    FAKE_RESULTS = [
        {
            "title": "News headline one",
            "url": "https://example.com/one",
            "snippet": "Short snippet one.",
            "date": "2026-06-11",
        },
        {
            "title": "News headline two",
            "url": "https://example.com/two",
            "snippet": "Short snippet two.",
            "date": "2026-06-10",
        },
    ]
    FAKE_RESPONSE = {
        "results": FAKE_RESULTS,
    }

    def _run_with_mock(self, *, query: str, max_results: int = 5) -> subprocess.CompletedProcess:
        """
        Run read-news.py in a subprocess with the test key in the environment.
        The script uses httpx directly, so we can't inject a MockTransport
        across subprocess boundaries.  Instead we use a real subprocess with
        a monkey-patching shim via PYTHONSTARTUP env override to test the
        observable contract: key must not appear in output, query must be echoed.

        For deeper structural testing we test the importable internals directly.
        """
        env = {
            **self.env,
            "PERPLEXITY_API_KEY": self.TEST_KEY,
        }
        return run_script("--query", query, "--max-results", str(max_results), env=env)

    def test_key_absent_from_stdout_and_stderr_on_success_mock(self):
        """
        Import-level test: patch httpx.Client inside the loaded module and
        verify the output contract (query echoed, key never appears).

        This test DIRECTLY imports read_news (by injecting scripts/ onto
        sys.path) so it can monkey-patch the HTTP call without a subprocess.
        """
        sys.path.insert(0, str(SCRIPTS_DIR))
        import importlib.util

        spec = importlib.util.spec_from_file_location("read_news", READ_NEWS)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Build a fake httpx.Response and fake httpx.Client.
        fake_response_obj = httpx.Response(
            200,
            json=self.FAKE_RESPONSE,
            request=httpx.Request("POST", "https://api.perplexity.ai/search"),
        )

        captured: dict = {}

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, url, *, json=None, headers=None, timeout=None):
                captured["url"] = url
                captured["json"] = json
                captured["headers"] = headers or {}
                return fake_response_obj

        import io
        import contextlib

        out_buf = io.StringIO()
        err_buf = io.StringIO()

        # Override environment
        old_env = os.environ.copy()
        os.environ["CRUX_HOME"] = self.tmpdir
        os.environ["PERPLEXITY_API_KEY"] = self.TEST_KEY
        # Reload crux_env module to pick up the new CRUX_HOME / key
        import crux_env  # noqa: F401 — must be importable after sys.path insert above
        crux_env._reset_cache()

        try:
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                # Patch httpx.Client
                original_client = mod.httpx.Client
                mod.httpx.Client = FakeClient
                try:
                    rc = mod.main(["--query", "my test query", "--max-results", "5"])
                finally:
                    mod.httpx.Client = original_client
        finally:
            # Restore environment
            os.environ.clear()
            os.environ.update(old_env)
            crux_env._reset_cache()

        self.assertEqual(rc, 0, f"expected exit 0, got {rc}; stderr={err_buf.getvalue()!r}")

        stdout_text = out_buf.getvalue()
        stderr_text = err_buf.getvalue()

        # Contract: key must NEVER appear in output
        self.assertNotIn(self.TEST_KEY, stdout_text, "API key leaked in stdout")
        self.assertNotIn(self.TEST_KEY, stderr_text, "API key leaked in stderr")

        # Contract: query must be echoed
        data = json.loads(stdout_text)
        self.assertEqual(data["query"], "my test query")
        self.assertEqual(data["max_results"], 5)

        # Contract: results passed through
        self.assertEqual(data["results"], self.FAKE_RESULTS)

        # Contract: the key header was sent (implicitly — captured headers must
        # contain an Authorization key with the Bearer token, but the test
        # MUST NOT assert the token value here since that would re-embed the key).
        self.assertIn("Authorization", captured["headers"])
        auth = captured["headers"]["Authorization"]
        self.assertTrue(auth.startswith("Bearer "), f"expected Bearer auth, got {auth[:20]!r}")

    def test_request_body_contains_query_and_params(self):
        """The POST body must carry query, max_results, search_context_size."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        import importlib.util

        spec = importlib.util.spec_from_file_location("read_news_body", READ_NEWS)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        fake_response_obj = httpx.Response(
            200,
            json=self.FAKE_RESPONSE,
            request=httpx.Request("POST", "https://api.perplexity.ai/search"),
        )
        captured: dict = {}

        class FakeClient:
            def __init__(self, *a, **kw):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def post(self, url, *, json=None, headers=None, timeout=None):
                captured["json"] = json
                return fake_response_obj

        import io, contextlib
        old_env = os.environ.copy()
        os.environ["CRUX_HOME"] = self.tmpdir
        os.environ["PERPLEXITY_API_KEY"] = self.TEST_KEY
        import crux_env
        crux_env._reset_cache()

        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                original_client = mod.httpx.Client
                mod.httpx.Client = FakeClient
                try:
                    mod.main(["--query", "elixir news", "--max-results", "3",
                              "--search-context-size", "medium"])
                finally:
                    mod.httpx.Client = original_client
        finally:
            os.environ.clear()
            os.environ.update(old_env)
            crux_env._reset_cache()

        body = captured.get("json", {})
        self.assertEqual(body.get("query"), "elixir news")
        self.assertEqual(body.get("max_results"), 3)
        self.assertEqual(body.get("search_context_size"), "medium")


class TestSinceFilter(ReadNewsBase):
    """`--since` keeps results by publication `date` and ignores `last_updated`.

    The regression: a page published in 2023 and re-crawled last night ranks
    as fresh and carries a fresh `last_updated`. Four nights of curated
    `site:` queries returned nothing new while the sources' feeds held thirty
    new posts. The filter reads `date` only.
    """

    TEST_KEY = "pplx-test-key-since-000000000000"
    FAKE_RESULTS = [
        # New by publication date; the crawl stamp is older than the cut.
        {"title": "new", "url": "https://example.com/new",
         "snippet": "s", "date": "2026-09-11", "last_updated": "2026-09-01"},
        # Old by publication date; the crawl stamp is newer than the cut. This
        # is the false-fresh shape, and it must be dropped.
        {"title": "old-recrawled", "url": "https://example.com/old",
         "snippet": "s", "date": "2023-11-03", "last_updated": "2026-09-13"},
        # Undated: cannot be shown to be new, so it is dropped.
        {"title": "undated", "url": "https://example.com/tag",
         "snippet": "s", "date": None, "last_updated": "2026-09-15"},
        # Published on the cut day itself: kept (on or after).
        {"title": "on-the-day", "url": "https://example.com/day",
         "snippet": "s", "date": "2026-09-06", "last_updated": "2026-09-06"},
    ]

    def test_bad_since_is_a_usage_error_before_the_key_check(self):
        cp = run_script("--query", "q", "--since", "2026-13-01", env=self.env)
        self.assertEqual(cp.returncode, 1)
        self.assertEqual(json.loads(cp.stdout)["error"], "usage_error")

    def test_good_since_reaches_the_key_check(self):
        # No key in the isolated env: a well-formed --since gets past validation
        # and the run stops at the key lookup, exit 2, as any other run would.
        cp = run_script("--query", "q", "--since", "2026-09-06", env=self.env)
        self.assertEqual(cp.returncode, 2)
        self.assertEqual(json.loads(cp.stdout)["error"], "env_not_configured")

    @unittest.skipUnless(HAVE_HTTPX, "httpx not installed")
    def test_since_keeps_by_date_and_ignores_last_updated(self):
        sys.path.insert(0, str(SCRIPTS_DIR))
        import importlib.util
        import io
        import contextlib

        spec = importlib.util.spec_from_file_location("read_news_since", READ_NEWS)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        fake_response_obj = httpx.Response(
            200,
            json={"results": self.FAKE_RESULTS},
            request=httpx.Request("POST", "https://api.perplexity.ai/search"),
        )

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, url, *, json=None, headers=None, timeout=None):
                return fake_response_obj

        out_buf = io.StringIO()
        err_buf = io.StringIO()
        old_env = os.environ.copy()
        os.environ["CRUX_HOME"] = self.tmpdir
        os.environ["PERPLEXITY_API_KEY"] = self.TEST_KEY
        import crux_env  # noqa: F401
        crux_env._reset_cache()
        try:
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                original_client = mod.httpx.Client
                mod.httpx.Client = FakeClient
                try:
                    rc = mod.main(["--query", "q", "--since", "2026-09-06"])
                finally:
                    mod.httpx.Client = original_client
        finally:
            os.environ.clear()
            os.environ.update(old_env)
            crux_env._reset_cache()

        self.assertEqual(rc, 0, err_buf.getvalue())
        data = json.loads(out_buf.getvalue())
        self.assertEqual(data["since"], "2026-09-06")
        self.assertEqual(data["dropped_by_since"], 2)
        # The two reasons are counted apart: one stale, one undated.
        self.assertEqual(data["dropped_older"], 1)
        self.assertEqual(data["dropped_undated"], 1)
        self.assertEqual([r["title"] for r in data["results"]], ["new", "on-the-day"])
        # Positive control for the regression this filter exists to close: the
        # re-crawled old page is dropped DESPITE carrying a fresh crawl stamp.
        # An earlier version of this control read `max(last_updated)`, which is
        # the UNDATED fixture's stamp, so it asserted about the wrong item and
        # passed for the wrong reason.
        old_recrawled = next(r for r in self.FAKE_RESULTS if r["title"] == "old-recrawled")
        self.assertGreater(old_recrawled["last_updated"], "2026-09-06")
        self.assertNotIn("old-recrawled", [r["title"] for r in data["results"]])
        self.assertNotIn(self.TEST_KEY, out_buf.getvalue())

    def test_filter_since_unit(self):
        sys.path.insert(0, str(SCRIPTS_DIR))
        import importlib.util
        from datetime import date as _date

        spec = importlib.util.spec_from_file_location("read_news_since_unit", READ_NEWS)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        kept, dropped = mod._filter_since(self.FAKE_RESULTS, _date(2026, 9, 6))
        self.assertEqual([r["title"] for r in kept], ["new", "on-the-day"])
        # The census, not a single total: `older` and `undated` are different
        # claims about why an item is absent.
        self.assertEqual(dropped, {"older": 1, "undated": 1})
        # Malformed dates and non-mapping items drop rather than raise.
        kept, dropped = mod._filter_since(
            [{"date": "2026-99-99"}, "not-a-mapping", {"date": "2026-09-07"}], _date(2026, 9, 6))
        self.assertEqual((len(kept), dropped), (1, {"older": 0, "undated": 2}))
        self.assertEqual(mod._filter_since("not-a-list", _date(2026, 9, 6)),
                         ([], {"older": 0, "undated": 0}))
        # A datetime-shaped `date` is a real publication day, not an undated item.
        kept, dropped = mod._filter_since(
            [{"date": "2026-09-07T10:00:00Z"}, {"date": "2026-09-01T10:00:00+00:00"}],
            _date(2026, 9, 6))
        self.assertEqual((len(kept), dropped), (1, {"older": 1, "undated": 0}))


class TestHTTPError(ReadNewsBase):
    """Non-2xx API response → exit 1 api_error, key must not appear."""

    TEST_KEY = "pplx-test-key-error-00000000"
    QUERY = "latest python releases"

    def _run_module_with_status(self, status_code: int, body: str) -> tuple[int, str, str]:
        """
        Load and run read_news.main with a fake client that returns the given
        HTTP status code. Returns (returncode, stdout_text, stderr_text).
        """
        sys.path.insert(0, str(SCRIPTS_DIR))
        import importlib.util, io, contextlib

        spec = importlib.util.spec_from_file_location(
            f"read_news_err_{status_code}", READ_NEWS
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        fake_response_obj = httpx.Response(
            status_code,
            text=body,
            request=httpx.Request("POST", "https://api.perplexity.ai/search"),
        )

        class FakeClient:
            def __init__(self, *a, **kw):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def post(self, url, *, json=None, headers=None, timeout=None):
                return fake_response_obj

        out_buf = io.StringIO()
        err_buf = io.StringIO()

        old_env = os.environ.copy()
        os.environ["CRUX_HOME"] = self.tmpdir
        os.environ["PERPLEXITY_API_KEY"] = self.TEST_KEY
        import crux_env
        crux_env._reset_cache()

        try:
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                original = mod.httpx.Client
                mod.httpx.Client = FakeClient
                try:
                    rc = mod.main(["--query", self.QUERY, "--max-results", "5"])
                finally:
                    mod.httpx.Client = original
        finally:
            os.environ.clear()
            os.environ.update(old_env)
            crux_env._reset_cache()

        return rc, out_buf.getvalue(), err_buf.getvalue()

    def test_non_2xx_exit_1(self):
        rc, stdout, stderr = self._run_module_with_status(401, '{"error": "Unauthorized"}')
        self.assertEqual(rc, 1, f"expected exit 1 on 401; stdout={stdout!r} stderr={stderr!r}")

    def test_non_2xx_api_error_envelope(self):
        rc, stdout, stderr = self._run_module_with_status(401, '{"error": "Unauthorized"}')
        data = json.loads(stdout)
        self.assertEqual(data.get("error"), "api_error")
        self.assertEqual(data.get("status"), 401)

    def test_non_2xx_key_not_in_output(self):
        rc, stdout, stderr = self._run_module_with_status(
            403, f"forbidden for key {self.TEST_KEY}"
        )
        self.assertNotIn(self.TEST_KEY, stdout, "API key leaked in stdout on error")
        self.assertNotIn(self.TEST_KEY, stderr, "API key leaked in stderr on error")

    def test_500_error(self):
        rc, stdout, stderr = self._run_module_with_status(500, "Internal server error")
        self.assertEqual(rc, 1)
        data = json.loads(stdout)
        self.assertEqual(data.get("error"), "api_error")
        self.assertEqual(data.get("status"), 500)
        self.assertNotIn(self.TEST_KEY, stdout)
        self.assertNotIn(self.TEST_KEY, stderr)


# ---------------------------------------------------------------------------
# (f) Capability lane: httpx absent → exit 2 + capability_error JSON envelope
# ---------------------------------------------------------------------------

class TestCapabilityLane(ReadNewsBase):
    """httpx absent → exit 2 with capability_error JSON envelope on stdout.

    We simulate httpx absence by injecting a PYTHONPATH that shadows the real
    httpx with an unimportable stub directory — the cleanest mechanism that
    works across subprocess boundaries without modifying the source under test.
    """

    def _make_no_httpx_path(self) -> str:
        """Create a directory that, when prepended to PYTHONPATH, hides httpx."""
        # Create a sub-directory named 'httpx' that is a package with no
        # importable content — importing it raises ImportError by design.
        stub_dir = os.path.join(self.tmpdir, "no_httpx_stub")
        os.makedirs(stub_dir, exist_ok=True)
        httpx_stub_dir = os.path.join(stub_dir, "httpx")
        os.makedirs(httpx_stub_dir, exist_ok=True)
        # An __init__.py that raises ImportError simulates a broken/absent install.
        with open(os.path.join(httpx_stub_dir, "__init__.py"), "w") as f:
            f.write("raise ImportError('httpx stub — simulating absent package')\n")
        return stub_dir

    def test_httpx_absent_exit_code_is_2(self):
        stub = self._make_no_httpx_path()
        old_path = os.environ.get("PYTHONPATH", "")
        env = {
            **self.env,
            "PYTHONPATH": stub + ((":" + old_path) if old_path else ""),
            # Provide a key so we definitely reach the httpx check
            "PERPLEXITY_API_KEY": "pplx-cap-test-absent-httpx",
        }
        result = run_script("--query", "test", env=env)
        self.assertEqual(
            result.returncode, 2,
            f"expected exit 2 for absent httpx, got {result.returncode}; "
            f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )

    def test_httpx_absent_capability_error_json_envelope(self):
        stub = self._make_no_httpx_path()
        old_path = os.environ.get("PYTHONPATH", "")
        env = {
            **self.env,
            "PYTHONPATH": stub + ((":" + old_path) if old_path else ""),
            "PERPLEXITY_API_KEY": "pplx-cap-test-absent-httpx",
        }
        result = run_script("--query", "test", env=env)
        self.assertEqual(result.returncode, 2)
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail(
                f"expected JSON on stdout for capability_error, got {result.stdout!r}"
            )
        self.assertEqual(
            data.get("error"),
            "capability_error",
            f"expected error=capability_error, got {data!r}",
        )
        self.assertIn(
            "httpx",
            data.get("missing", []),
            f"expected 'httpx' in missing list, got {data!r}",
        )

    def test_httpx_absent_stderr_contains_install_remediation(self):
        stub = self._make_no_httpx_path()
        old_path = os.environ.get("PYTHONPATH", "")
        env = {
            **self.env,
            "PYTHONPATH": stub + ((":" + old_path) if old_path else ""),
            "PERPLEXITY_API_KEY": "pplx-cap-test-absent-httpx",
        }
        result = run_script("--query", "test", env=env)
        self.assertEqual(result.returncode, 2)
        # stderr must carry the install-uv remediation hint
        self.assertIn(
            "uv",
            result.stderr,
            f"expected uv remediation in stderr, got {result.stderr!r}",
        )


# ---------------------------------------------------------------------------
# (g) Scrub-boundary: key straddling the 500-char truncation in api_error body
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class TestScrubBoundary(ReadNewsBase):
    """Key straddling the 500-char truncation boundary must not leak.

    If a body contains the key near position 500, scrub-then-truncate (correct)
    emits '[REDACTED]' safely; truncate-then-scrub (the old bug) may leave a
    fragment of the key in the output when the truncation splits the key value.
    """

    TEST_KEY = "pplx-scrub-boundary-test-key-xyzzy"

    def _run_with_key_at_boundary(self, key_start: int) -> tuple[int, str, str]:
        """
        Load and run read_news.main with a fake 4xx response whose body has the
        key starting at *key_start* offset inside a 600-char body.
        """
        sys.path.insert(0, str(SCRIPTS_DIR))
        import importlib.util, io, contextlib

        spec = importlib.util.spec_from_file_location(
            f"read_news_scrub_{key_start}", READ_NEWS
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Build a body where the key appears at position key_start.
        # Pad before and after to hit > 500 chars total.
        padding_before = "x" * key_start
        padding_after = "y" * (600 - key_start - len(self.TEST_KEY))
        body = padding_before + self.TEST_KEY + padding_after
        assert len(body) > 500, "body must be > 500 chars to test truncation"

        fake_response_obj = httpx.Response(
            403,
            text=body,
            request=httpx.Request("POST", "https://api.perplexity.ai/search"),
        )

        class FakeClient:
            def __init__(self, *a, **kw):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def post(self, url, *, json=None, headers=None, timeout=None):
                return fake_response_obj

        out_buf = io.StringIO()
        err_buf = io.StringIO()

        old_env = os.environ.copy()
        os.environ["CRUX_HOME"] = self.tmpdir
        os.environ["PERPLEXITY_API_KEY"] = self.TEST_KEY
        import crux_env
        crux_env._reset_cache()

        try:
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                original = mod.httpx.Client
                mod.httpx.Client = FakeClient
                try:
                    rc = mod.main(["--query", "boundary test", "--max-results", "5"])
                finally:
                    mod.httpx.Client = original
        finally:
            os.environ.clear()
            os.environ.update(old_env)
            crux_env._reset_cache()

        return rc, out_buf.getvalue(), err_buf.getvalue()

    def _no_key_fragment(self, text: str, key: str, min_len: int = 8) -> bool:
        """Return True if no contiguous substring of key (len >= min_len) appears in text."""
        for start in range(len(key) - min_len + 1):
            for end in range(start + min_len, len(key) + 1):
                if key[start:end] in text:
                    return False
        return True

    def test_key_straddling_boundary_not_in_stdout(self):
        """Key starting at offset 490 straddles the 500-char truncation point."""
        _rc, stdout, _stderr = self._run_with_key_at_boundary(key_start=490)
        self.assertTrue(
            self._no_key_fragment(stdout, self.TEST_KEY),
            f"key fragment (>=8 chars) leaked in stdout: {stdout!r}",
        )

    def test_key_straddling_boundary_not_in_stderr(self):
        _rc, _stdout, stderr = self._run_with_key_at_boundary(key_start=490)
        self.assertTrue(
            self._no_key_fragment(stderr, self.TEST_KEY),
            f"key fragment (>=8 chars) leaked in stderr: {stderr!r}",
        )

    def test_key_at_exactly_500_not_in_output(self):
        """Key starting at exactly offset 500 — entirely in the truncated tail."""
        _rc, stdout, stderr = self._run_with_key_at_boundary(key_start=500)
        self.assertTrue(
            self._no_key_fragment(stdout, self.TEST_KEY),
            f"key fragment leaked in stdout: {stdout!r}",
        )
        self.assertTrue(
            self._no_key_fragment(stderr, self.TEST_KEY),
            f"key fragment leaked in stderr: {stderr!r}",
        )


# ---------------------------------------------------------------------------
# (h) Top-level-catch: unexpected exception → exit 1 + internal envelope
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class TestTopLevelCatch(ReadNewsBase):
    """Unexpected exception inside main → exit 1 + internal JSON envelope.

    The top-level wrapper must catch anything that escapes main(), emit
    {"error": "internal", "detail": "<scrubbed str(e), 500 chars>"} to stdout,
    a scrubbed single-line message to stderr, and exit 1.  No raw traceback text
    and no raw key fragment must appear.
    """

    TEST_KEY = "pplx-toplevel-catch-test-key-99999"

    def _run_with_bomb(self) -> tuple[int, str, str]:
        """
        Load read_news and patch httpx.Client so its __enter__ raises an
        unexpected (non-httpx) exception carrying the key in its message.
        This exercises the top-level catch path without needing subprocess tricks.
        """
        sys.path.insert(0, str(SCRIPTS_DIR))
        import importlib.util, io, contextlib

        spec = importlib.util.spec_from_file_location("read_news_bomb", READ_NEWS)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        class BombClient:
            def __init__(self, *a, **kw):
                pass
            def __enter__(self):
                raise RuntimeError(
                    f"unexpected internal error — key={self.TEST_KEY} leaked"
                )
            def __exit__(self, *a):
                return False

        # Bind TEST_KEY into BombClient's __enter__ via closure
        test_key = self.TEST_KEY

        class BombClientBound:
            def __init__(self, *a, **kw):
                pass
            def __enter__(self):
                raise RuntimeError(
                    f"unexpected internal error — key={test_key} leaked"
                )
            def __exit__(self, *a):
                return False

        out_buf = io.StringIO()
        err_buf = io.StringIO()

        old_env = os.environ.copy()
        os.environ["CRUX_HOME"] = self.tmpdir
        os.environ["PERPLEXITY_API_KEY"] = self.TEST_KEY
        import crux_env
        crux_env._reset_cache()

        try:
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                original = mod.httpx.Client
                mod.httpx.Client = BombClientBound
                # Call the module-level entry point (which wraps main()).
                # If __name__ guard is used, we need to invoke main() through
                # the wrapper directly.  We test by calling main() — the
                # top-level catch must be inside main() or its wrapper.
                try:
                    rc = mod.main(["--query", "bomb test", "--max-results", "5"])
                finally:
                    mod.httpx.Client = original
        finally:
            os.environ.clear()
            os.environ.update(old_env)
            crux_env._reset_cache()

        return rc, out_buf.getvalue(), err_buf.getvalue()

    def test_unexpected_exception_exit_code_is_1(self):
        rc, stdout, stderr = self._run_with_bomb()
        self.assertEqual(
            rc, 1,
            f"expected exit 1 on unexpected exception, got {rc}; "
            f"stdout={stdout!r} stderr={stderr!r}",
        )

    def test_unexpected_exception_internal_json_envelope(self):
        _rc, stdout, _stderr = self._run_with_bomb()
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            self.fail(f"expected JSON on stdout for internal error, got {stdout!r}")
        self.assertEqual(
            data.get("error"),
            "internal",
            f"expected error=internal, got {data!r}",
        )
        self.assertIn(
            "detail",
            data,
            f"expected 'detail' key in internal envelope, got {data!r}",
        )

    def test_unexpected_exception_no_traceback_in_stdout(self):
        _rc, stdout, _stderr = self._run_with_bomb()
        self.assertNotIn(
            "Traceback",
            stdout,
            f"raw traceback leaked in stdout: {stdout!r}",
        )
        self.assertNotIn(
            "RuntimeError",
            stdout,
            f"raw exception type leaked in stdout: {stdout!r}",
        )

    def test_unexpected_exception_no_raw_key_in_output(self):
        _rc, stdout, stderr = self._run_with_bomb()
        self.assertNotIn(
            self.TEST_KEY,
            stdout,
            f"raw key leaked in stdout on internal error: {stdout!r}",
        )
        self.assertNotIn(
            self.TEST_KEY,
            stderr,
            f"raw key leaked in stderr on internal error: {stderr!r}",
        )


# ---------------------------------------------------------------------------
# (e) PEP 723 block presence
# ---------------------------------------------------------------------------

class TestPEP723Block(unittest.TestCase):
    """read-news.py carries the required PEP 723 block."""

    def _extract_block(self) -> str | None:
        """Return the raw text of the # /// script block, or None."""
        text = READ_NEWS.read_text(encoding="utf-8")
        PEP723_BLOCK_RE = re.compile(
            r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$"
        )
        matches = [m for m in PEP723_BLOCK_RE.finditer(text) if m.group("type") == "script"]
        if not matches:
            return None
        return matches[0].group("content")

    def test_script_exists(self):
        self.assertTrue(READ_NEWS.is_file(), f"read-news.py missing at {READ_NEWS}")

    def test_pep723_block_present(self):
        block = self._extract_block()
        self.assertIsNotNone(
            block,
            "read-news.py must carry a PEP 723 `# /// script` block "
            "(ADR-0040 §2 + ADR-0035 §2)",
        )

    def test_pep723_declares_httpx(self):
        block = self._extract_block()
        self.assertIsNotNone(block)
        block_text = "\n".join(
            line[2:] if line.startswith("# ") else line.lstrip("#")
            for line in block.splitlines()
        )
        self.assertRegex(
            block_text,
            r'"httpx[>=<~!]',
            "read-news.py PEP 723 block must declare httpx",
        )

    def test_pep723_declares_requires_python(self):
        block = self._extract_block()
        self.assertIsNotNone(block)
        block_text = "\n".join(
            line[2:] if line.startswith("# ") else line.lstrip("#")
            for line in block.splitlines()
        )
        self.assertIn(
            "requires-python",
            block_text,
            "read-news.py PEP 723 block must declare requires-python",
        )


if __name__ == "__main__":
    unittest.main()
