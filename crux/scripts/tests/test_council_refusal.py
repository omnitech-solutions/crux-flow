"""A model safety refusal is reported as a refusal, not a JSON parse failure.

A model can decline a request: the gateway returns HTTP 200 with
``finish_reason: "content_filter"`` (or a passed-through
``native_finish_reason: "refusal"``) and empty content, rather than raising.
Before this guard a refusal produced an empty body, fell through to the JSON
parse, and surfaced as ``ValueError("Could not parse JSON response from …")`` —
an environment-looking error for what is actually a policy decline.

Both paths degrade the seat to an error vote excluded from aggregation
(ADR-0054), so this is a DIAGNOSTIC fix, not a correctness one: the assertions
below pin (a) that a refusal is named as a refusal, and (b) that the ADR-0054
degradation is preserved either way.

The transport moved to the one OpenRouter gateway (ADR-0087), so the fake is now
an ``httpx.AsyncClient`` rather than a provider SDK — the requirement is
unchanged, only the wire field the decline arrives in.

No network: ``httpx.AsyncClient`` is monkeypatched with a fake.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS = REPO_ROOT / "crux" / "scripts"

try:
    sys.path.insert(0, str(SCRIPTS))
    from crux.council.async_council import AsyncCouncil, AsyncCouncilConfig
    HAVE_COUNCIL = True
except Exception:  # router deps unavailable
    HAVE_COUNCIL = False


class _FakeResponse:
    def __init__(self, body):
        self.status_code = 200
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


class _FakeAsyncClient:
    """Mimics the async context manager returned by httpx.AsyncClient()."""

    def __init__(self, body):
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None, **kwargs):
        return _FakeResponse(self._body)


def _run_seat(body):
    """Drive _call_seat_async against a fake gateway; return the vote."""
    import crux.council.async_council as ac

    council = AsyncCouncil.__new__(AsyncCouncil)
    council.config = AsyncCouncilConfig()
    council.gateway_key = "test-key-not-used"
    council._seat_models = {"anthropic": council.config.anthropic_model}
    council._retry = lambda fn: fn

    with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test-dummy"}), \
         mock.patch.object(ac.httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(body)):
        return asyncio.run(council._call_seat_async("anthropic", "probe prompt"))


def _vote_body(finish_reason="stop", content="", native=None):
    choice = {"finish_reason": finish_reason, "message": {"content": content}}
    if native is not None:
        choice["native_finish_reason"] = native
    return {"choices": [choice]}


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class CouncilRefusalDiagnosticTests(unittest.TestCase):
    def test_refusal_is_named_a_refusal_not_a_parse_failure(self):
        """The whole point: the reason must say 'refusal', never 'parse'."""
        v = _run_seat(_vote_body(finish_reason="content_filter"))
        blob = " ".join(
            str(x) for x in (v.reasoning, getattr(v, "dissenting_points", None))
        ).lower()
        self.assertIn("refus", blob, "a decline must be reported as a refusal")
        self.assertNotIn(
            "could not parse json", blob,
            "a policy decline must not be misreported as a malformed response",
        )

    def test_native_refusal_signal_is_also_named_a_refusal(self):
        v = _run_seat(_vote_body(finish_reason="stop", native="refusal"))
        blob = " ".join(str(x) for x in (v.reasoning, v.dissenting_points)).lower()
        self.assertIn("refus", blob)
        self.assertNotIn("could not parse json", blob)

    def test_refusal_still_degrades_to_an_excluded_error_seat(self):
        """ADR-0054 behaviour is preserved — this fix is diagnostic only."""
        v = _run_seat(_vote_body(finish_reason="content_filter"))
        self.assertTrue(
            getattr(v, "errored", False),
            "a refused seat must remain an errored seat so aggregation excludes it",
        )

    def test_normal_completion_is_unaffected(self):
        """A non-refusal response still parses into a real vote."""
        body = '{"decision": "APPROVE", "confidence": 0.9, "dissents": [], "reasoning": "ok"}'
        v = _run_seat(_vote_body(content=body))
        self.assertFalse(getattr(v, "errored", False))
        self.assertEqual(v.decision, "APPROVE")
        self.assertEqual(v.provider, "anthropic")


if __name__ == "__main__":
    unittest.main()
