"""The live proof's exit-code contract, exercised with injected faults.

The script under test is `openrouter_live_proof.py`, which is deliberately NOT
named `test_*` because it spends money. This file IS named `test_*` and spends
nothing: every call is intercepted before it reaches the network.

The property under test is the 1/2 split. Exit 1 means the thing being proved
failed. Exit 2 means the proof could not be attempted. An operator reads exit 1
as "the seats stopped resolving to three distinct providers" and acts on it, so
anything that never observed the seats must not be able to produce it.

The gap this pins: a transport fault — laptop offline, DNS down, TLS refused —
escaped every handler and surfaced as an unhandled `httpx.ConnectError`,
exiting 1 with a traceback. The most common way to run this script wrong
reported the most alarming possible result.

The one case that MUST stay exit 1 is asserted alongside: phase 2's deadline
breach is the second assertion failing, not an environment problem, even though
`httpx.ReadTimeout` is itself a subclass of `httpx.TransportError`. Only
`ReadTimeout` is held back that way. A connect-side timeout never sent the
request, so it measured no deadline and exits 2 with the rest of transport.

The synthetic green-path tests redirect stdout: without that they print the
"PROVED: …" banner into CI logs, which reads as a live success where every call
is mocked.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

try:
    import httpx
    HAVE_HTTPX = True
except ImportError:
    HAVE_HTTPX = False


def _load_proof():
    """Import the non-`test_`-named script by path.

    `importlib` rather than a plain import because the filename is chosen to be
    invisible to `unittest discover` and `pytest`, which is the whole point of
    it — reaching it from a collected test has to be explicit.
    """
    path = TESTS_DIR / "openrouter_live_proof.py"
    spec = importlib.util.spec_from_file_location("openrouter_live_proof", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["openrouter_live_proof"] = mod
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class LiveProofExitContractTests(unittest.TestCase):
    def setUp(self):
        self.proof = _load_proof()
        # Both gates open, so main() reaches the phase calls and the fault
        # injected there is what decides the exit code.
        env = mock.patch.dict("os.environ", {"CRUX_ALLOW_LIVE": "1"})
        env.start()
        self.addCleanup(env.stop)
        key = mock.patch.object(self.proof, "get", return_value="sk-test")
        key.start()
        self.addCleanup(key.stop)

    def _run_with_phase1(self, exc):
        with mock.patch.object(self.proof, "_phase1_distinct_providers", side_effect=exc):
            return self.proof.main()

    # ── transport faults are exit 2 ────────────────────────────────────────
    def test_connect_error_is_an_environment_refusal(self):
        self.assertEqual(self._run_with_phase1(httpx.ConnectError("offline")), 2)

    def test_dns_and_tls_faults_are_environment_refusals(self):
        for exc in (httpx.ConnectError("Name or service not known"),
                    httpx.ReadError("TLS handshake failed"),
                    httpx.WriteError("broken pipe"),
                    httpx.PoolTimeout("pool exhausted"),
                    httpx.ConnectTimeout("connect timed out"),
                    httpx.ReadTimeout("read timed out")):
            with self.subTest(exc=type(exc).__name__):
                self.assertEqual(self._run_with_phase1(exc), 2)

    def test_a_transport_fault_prints_to_stderr_and_names_the_class(self):
        import io
        buf = io.StringIO()
        with mock.patch.object(sys, "stderr", buf):
            code = self._run_with_phase1(httpx.ConnectError("offline"))
        self.assertEqual(code, 2)
        self.assertIn("ConnectError", buf.getvalue())
        self.assertIn("not a failed proof", buf.getvalue())

    # ── the real failures stay exit 1 ──────────────────────────────────────
    def test_non_distinct_providers_is_still_a_failed_proof(self):
        one_host = {"openai": "azure", "anthropic": "azure", "gemini": "azure"}
        with mock.patch.object(self.proof, "_phase1_distinct_providers",
                               return_value=one_host):
            self.assertEqual(self.proof.main(), 1)

    def test_an_unreported_serving_provider_is_a_failed_proof(self):
        """A `None` in the observed set means the response carried no provider
        field, so distinctness was never established — not proved by default."""
        missing = {"openai": "openai", "anthropic": None, "gemini": "google"}
        with mock.patch.object(self.proof, "_phase1_distinct_providers",
                               return_value=missing):
            self.assertEqual(self.proof.main(), 1)

    def test_a_phase2_deadline_breach_is_a_failed_proof_not_an_environment_refusal(self):
        """The deliberate exception to the transport rule.

        `httpx.ReadTimeout` IS an `httpx.TransportError`, so a handler written
        one layer too high would swallow the deadline breach into exit 2 and the
        second assertion could never fail.
        """
        distinct = {"openai": "openai", "anthropic": "anthropic", "gemini": "google"}
        with mock.patch.object(self.proof, "_phase1_distinct_providers",
                               return_value=distinct), \
             mock.patch.object(self.proof, "_phase2_full_size_within_deadline",
                               return_value=False), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.proof.main(), 1)

    def test_both_phases_green_is_exit_zero(self):
        distinct = {"openai": "openai", "anthropic": "anthropic", "gemini": "google"}
        with mock.patch.object(self.proof, "_phase1_distinct_providers",
                               return_value=distinct), \
             mock.patch.object(self.proof, "_phase2_full_size_within_deadline",
                               return_value=True), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.proof.main(), 0)

    # ── gateway faults keep their existing exit 2 ──────────────────────────
    def test_gateway_faults_remain_environment_refusals(self):
        from crux.core import llm_caller
        for exc in (llm_caller.GatewayInsufficientCreditError("no credit", 402),
                    llm_caller.GatewayAuthError("bad key", 401),
                    llm_caller.GatewayUpstreamError("upstream", 503),
                    llm_caller.ModelRefusedError("declined")):
            with self.subTest(exc=type(exc).__name__):
                self.assertEqual(self._run_with_phase1(exc), 2)

    # ── the gates themselves ───────────────────────────────────────────────
    def test_a_closed_live_gate_refuses_before_any_call(self):
        with mock.patch.dict("os.environ", {"CRUX_ALLOW_LIVE": "0"}), \
             mock.patch.object(self.proof, "_phase1_distinct_providers") as phase1:
            self.assertEqual(self.proof.main(), 2)
        phase1.assert_not_called()

    def test_a_missing_key_refuses_before_any_call(self):
        with mock.patch.object(self.proof, "get", return_value=None), \
             mock.patch.object(self.proof, "_phase1_distinct_providers") as phase1:
            self.assertEqual(self.proof.main(), 2)
        phase1.assert_not_called()


if __name__ == "__main__":
    unittest.main()
