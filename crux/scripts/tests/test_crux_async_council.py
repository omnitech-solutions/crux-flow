"""Regression tests for AsyncCouncil's seat request shaping over the gateway.

Every seat now reaches its model through the one OpenRouter gateway on
``httpx.AsyncClient``, built by the SAME request builder the sync router uses
(ADR-0087). The three lazy-imported provider SDKs are gone, and with them the
fake-openai-module mechanism these tests used to rely on.

What is locked in:

1. The council and the router emit ONE request shape — same URL, same
   ``Authorization: Bearer`` header, same ``reasoning_effort`` pin. A seat that
   drifted back to a bespoke payload fails here.
2. A seat asks for JSON via ``response_format`` and never sends ``temperature``.
3. Seat identity stays the vendor namespace: ``CouncilVote.provider`` is
   ``openai`` / ``anthropic`` / ``gemini``, which is what error votes and
   aggregation key on.
4. The vision seat sends the image as an OpenAI-compatible ``image_url``
   content part whose value is the nested ``{"url": data_url}`` object.

Assertions are on the RETURNED CouncilVote / VisualVoteResult (not merely "no
exception") — the per-seat try/except would otherwise swallow a broken fix into
a DEFER error vote and masquerade as a pass.

Run under uv (httpx required): uv run python3 crux/scripts/tests/test_crux_async_council.py
"""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

try:
    import httpx  # noqa: F401  (async_council → llm_caller imports httpx)
    HAVE_HTTPX = True
except ImportError:
    HAVE_HTTPX = False

GATEWAY_URL = "https://openrouter.ai/api/v1/chat/completions"


class _FakeResponse:
    def __init__(self, body):
        self.status_code = 200
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


class _FakeAsyncClient:
    """Records the one request it is given and replays a canned body."""

    def __init__(self, rec, body, **kwargs):
        self._rec = rec
        self._body = body
        rec["client_kwargs"] = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None, **kwargs):
        self._rec["url"] = url
        self._rec["headers"] = headers or {}
        self._rec["payload"] = json
        return _FakeResponse(self._body)


def _content_body(text):
    return {"choices": [{"finish_reason": "stop", "message": {"content": text}}]}


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class AsyncCouncilSeatRequestTests(unittest.TestCase):
    def setUp(self):
        from crux.council import async_council
        self.async_council = async_council
        self.rec = {}

    def _council(self, cls_name="AsyncCouncil", **seat_models):
        # Build the instance without __init__ (which probes the key); set only
        # the attributes the seat methods touch.
        cls = getattr(self.async_council, cls_name)
        ac = cls.__new__(cls)
        ac.config = self.async_council.AsyncCouncilConfig(**{
            f"{seat}_model": model for seat, model in seat_models.items()
        })
        ac.gateway_key = "sk-or-test-dummy"
        ac._seat_models = {
            "openai": ac.config.openai_model,
            "anthropic": ac.config.anthropic_model,
            "gemini": ac.config.gemini_model,
        }
        ac._retry = lambda fn: fn  # passthrough (real shim is a no-op)
        return ac

    def _patched(self, body):
        rec = self.rec
        return (
            mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test-dummy"}),
            mock.patch.object(self.async_council.httpx, "AsyncClient",
                              lambda **kw: _FakeAsyncClient(rec, body, **kw)),
        )

    def _run(self, coro_factory, body):
        env_patch, client_patch = self._patched(body)
        with env_patch, client_patch:
            return asyncio.run(coro_factory())

    def test_text_seat_hits_the_gateway_and_forwards_effort(self):
        ac = self._council(openai="gpt-6.1-sol-low")  # same SKU, pins effort: low
        vote = self._run(
            lambda: ac._call_seat_async("openai", "Question?", "Be terse."),
            _content_body('{"decision": "APPROVE", "confidence": 0.9, '
                          '"dissents": [], "reasoning": "ok"}'),
        )

        self.assertEqual(self.rec["url"], GATEWAY_URL)
        self.assertEqual(self.rec["headers"].get("Authorization"), "Bearer sk-or-test-dummy")
        payload = self.rec["payload"]
        self.assertEqual(payload["model"], "openai/gpt-6.1-sol")   # verbatim id
        self.assertEqual(payload["reasoning_effort"], "low")        # effort forwarded
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertNotIn("temperature", payload)                    # reasoning model
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][0]["content"], "Be terse.")

        # Assert on the RETURNED vote (a swallowed failure would be errored).
        self.assertFalse(vote.errored)
        self.assertEqual(vote.decision, "APPROVE")
        self.assertEqual(vote.confidence, 0.9)
        self.assertEqual(vote.provider, "openai")
        self.assertEqual(vote.model, "OpenAI/gpt-6.1-sol-low")

    def test_seat_pins_its_serving_provider(self):
        """The response-integrity control: each seat names the host it will
        accept, so the three seats cannot silently collapse onto one."""
        ac = self._council()
        self._run(
            lambda: ac._call_seat_async("anthropic", "Question?"),
            _content_body('{"decision": "REJECT", "confidence": 0.4, '
                          '"dissents": [{"point": "no"}], "reasoning": "nope"}'),
        )
        self.assertEqual(self.rec["payload"]["provider"],
                         {"only": ["anthropic"], "data_collection": "deny"})
        self.assertEqual(self.rec["payload"]["model"], "anthropic/claude-opus-5.5")
        self.assertEqual(self.rec["payload"]["reasoning_effort"], "xhigh")

    def test_default_openai_seat_calls_astra(self):
        ac = self._council()
        vote = self._run(
            lambda: ac._call_seat_async("openai", "Question?"),
            _content_body('{"decision": "APPROVE", "confidence": 0.9, '
                          '"dissents": [], "reasoning": "ok"}'),
        )
        self.assertEqual(self.rec["payload"]["model"], "openai/gpt-6-astra")
        self.assertEqual(self.rec["payload"]["provider"],
                         {"only": ["openai"], "data_collection": "deny"})
        self.assertNotIn("temperature", self.rec["payload"])
        self.assertFalse(vote.errored)
        self.assertEqual(vote.model, "OpenAI/gpt-6-astra")

    def test_seat_identity_is_the_vendor_namespace_not_the_gateway(self):
        ac = self._council(gemini="gemini-3.1-pro-preview")
        vote = self._run(
            lambda: ac._call_seat_async("gemini", "Question?"),
            _content_body('{"decision": "REJECT", "confidence": 0.4, '
                          '"dissents": [{"point": "no"}], "reasoning": "nope"}'),
        )
        self.assertEqual(vote.provider, "gemini")
        self.assertEqual(vote.decision, "REJECT")
        self.assertEqual(vote.dissenting_points, ["no"])

    def test_client_timeout_comes_from_the_council_config(self):
        ac = self._council(openai="gpt-6.1-sol")
        ac.config.timeout_seconds = 42.0
        self._run(
            lambda: ac._call_seat_async("openai", "Question?"),
            _content_body('{"decision": "APPROVE", "confidence": 0.7, '
                          '"dissents": [], "reasoning": "ok"}'),
        )
        self.assertEqual(self.rec["client_kwargs"].get("timeout"), 42.0)

    def test_unparseable_body_degrades_to_an_errored_seat(self):
        ac = self._council(openai="gpt-6.1-sol")
        vote = self._run(
            lambda: ac._call_seat_async("openai", "Question?"),
            _content_body("not json at all"),
        )
        self.assertTrue(vote.errored)
        self.assertEqual(vote.decision, "DEFER_TO_HUMAN")
        self.assertEqual(vote.provider, "openai")

    def test_vision_seat_sends_a_nested_image_url_part(self):
        ac = self._council(cls_name="AsyncVisualCouncil")
        result = self._run(
            lambda: ac._analyze_seat_async("openai", "OpenAI-vision", "aGVsbG8=", "Inspect."),
            _content_body('{"passed": true, "confidence": 0.8, '
                          '"observations": "clean", "anomalies": []}'),
        )

        self.assertEqual(self.rec["url"], GATEWAY_URL)
        self.assertEqual(self.rec["payload"]["model"], "openai/gpt-6-astra")
        content = self.rec["payload"]["messages"][0]["content"]
        self.assertEqual({part["type"] for part in content}, {"text", "image_url"})
        img = next(p for p in content if p["type"] == "image_url")
        # image_url is the NESTED {"url": ...} object — the chat-completions
        # shape, which is the only shape the gateway speaks.
        self.assertTrue(img["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertNotIn("reasoning_effort", self.rec["payload"])  # gateway default effort
        self.assertTrue(result.passed)
        self.assertEqual(result.model_name, "OpenAI-vision")

    def test_default_anthropic_visual_seat_emits_xhigh_opus_request(self):
        ac = self._council(cls_name="AsyncVisualCouncil")
        result = self._run(
            lambda: ac._analyze_seat_async("anthropic", "Anthropic-vision", "aGVsbG8=", "Inspect."),
            _content_body('{"passed": true, "confidence": 0.8, '
                          '"observations": "clean", "anomalies": []}'),
        )
        payload = self.rec["payload"]
        self.assertEqual(payload["model"], "anthropic/claude-opus-5.5")
        self.assertEqual(payload["reasoning_effort"], "xhigh")
        self.assertEqual(payload["provider"],
                         {"only": ["anthropic"], "data_collection": "deny"})
        self.assertTrue(result.passed)


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class AsyncCouncilSeatAvailabilityTests(unittest.TestCase):
    """Seat construction is gated on the one key, not on three."""

    def test_seats_exist_when_the_gateway_key_is_present(self):
        from crux.council.async_council import AsyncCouncil

        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test-dummy"}):
            council = AsyncCouncil()
        self.assertEqual(council.available_providers, ["openai", "anthropic", "gemini"])


if __name__ == '__main__':
    unittest.main()
