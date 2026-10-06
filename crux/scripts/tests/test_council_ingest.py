"""Adversarial ingest tests for model-output confidence and JSON extraction.

Two fail-closed properties of the council's seat ingest, exercised end-to-end
through a fake gateway (no network: ``httpx.AsyncClient`` is monkeypatched -
the same seam ``test_council_refusal.py`` uses):

SEC-1 (confidence validation). The vote parser used to read
``confidence=data.get("confidence", 0.5)`` verbatim. A hostile float like 99.0
flips AUTO_EXECUTE on the mean; a string like "high" raises a TypeError later
out of ``_aggregate_votes`` via ``_extract_key_agreements``, crashing the whole
deliberation and defeating the fail-closed NO_QUORUM floor of ADR-0054; and a
JSON ``true``/``false`` coerced through ``float()`` to 1.0/0.0, because a bool
is an int. The validator now rejects all three at ingest by raising the
dedicated ``ConfidenceRejectedError`` — the seat wrapper's closed-vocabulary
redaction maps that class to the ``malformed-response`` label, so the value
degrades to an error vote, never a verdict and never a crash.

SEC-2 (extraction complexity). The old extraction pattern
``re.search(r"\\{[\\s\\S]*\\}", ...)`` was quadratic — but ONLY when open
braces repeat: at each attempted start position its scan ran to end-of-
string, so a body like ``"{" * 131000`` (no close) cost ~33s here, synchronously
inside the event loop, where ``asyncio.wait_for`` cannot cancel it. Extraction
is now ``str.find``/``str.rfind`` slicing, semantics-identical (first ``{`` to
last ``}``), linear both ways. The bounded cost is asserted below on that
repeated-open-brace payload.
"""
from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS = REPO_ROOT / "crux" / "scripts"

try:
    sys.path.insert(0, str(SCRIPTS))
    from crux.council.async_council import (
        AsyncCouncil,
        AsyncCouncilConfig,
        AsyncVisualCouncil,
    )
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


def _vote_body(content):
    return {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}


def _run_council_seat(body):
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


def _vision_vote(payload):
    """Build the vision-seat body for a completed content string."""
    return _vote_body(payload)


def _run_vision_seat(body):
    """Drive _analyze_seat_async against a fake gateway; return the result."""
    import crux.council.async_council as ac

    council = AsyncVisualCouncil.__new__(AsyncVisualCouncil)
    council.config = AsyncCouncilConfig()
    council.gateway_key = "test-key-not-used"
    council._seat_models = {"openai": council.config.openai_model}
    council._retry = lambda fn: fn

    with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test-dummy"}), \
         mock.patch.object(ac.httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(body)):
        return asyncio.run(council._analyze_seat_async("openai", "OpenAI-vision", "aGk=", "probe"))


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class IngestLabelVocabularyTests(unittest.TestCase):
    """Every way a reply fails ingest lands on a label in the closed set."""

    #: The label set and the finish set, pinned as SETS. `test_council_retry.py`
    #: drives each label through a seat; this pins the vocabulary they draw from.
    LABELS = {
        "refused", "insufficient-credit", "timeout", "auth", "rate-limit",
        "unreachable", "provider", "client-config", "malformed-response",
        "unexpected-status", "truncated",
    }

    def test_the_label_table_holds_exactly_the_pinned_set(self):
        self.assertEqual(set(AsyncCouncil._ERROR_LABELS.values()), self.LABELS)

    def test_ingest_failures_carry_a_label_from_the_set_and_never_client_config(self):
        cases = {
            "not json": _vote_body("no braces"),
            "empty": _vote_body(""),
            "dissents null": _vote_body('{"decision": "APPROVE", "dissents": null}'),
            "confidence": _vote_body('{"decision": "APPROVE", "confidence": 99}'),
            "length": {"choices": [{"finish_reason": "length",
                                    "message": {"content": '{"decision": "APPROVE"}'}}]},
        }
        seen = set()
        for name, body in cases.items():
            with self.subTest(case=name):
                vote = _run_council_seat(body)
                self.assertTrue(vote.errored)
                self.assertIn(vote.fault_label, self.LABELS)
                self.assertNotEqual(vote.fault_label, "client-config")
                seen.add(vote.fault_label)
        self.assertEqual(seen, {"malformed-response", "truncated"})

    def test_the_finish_vocabulary_is_the_pinned_set(self):
        import crux.council.async_council as ac

        self.assertEqual(set(ac._FINISH_REASONS),
                         {"stop", "length", "content_filter", "tool_calls", "error",
                          "other", "missing"})


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class ConfidenceValidationTests(unittest.TestCase):
    """SEC-1: hostile confidence values must be an error vote, never a crash."""

    def _assert_error_vote(self, raw_confidence, what):
        payload = (
            '{"decision": "APPROVE", "confidence": %s, '
            '"dissents": [], "reasoning": "ok"}' % raw_confidence
        )
        vote = _run_council_seat(_vote_body(payload))
        self.assertTrue(getattr(vote, "errored", False),
                        f"{what}: {raw_confidence} must degrade to an error vote")
        self.assertEqual(vote.decision, "DEFER_TO_HUMAN")

    def test_hostile_float_99_is_an_error_vote(self):
        self._assert_error_vote("99.0", "out-of-range float")

    def test_hostile_float_negative_is_an_error_vote(self):
        self._assert_error_vote("-50.0", "out-of-range float")

    def test_string_confidence_is_an_error_vote_not_a_typeerror(self):
        self._assert_error_vote('"high"', "non-numeric string")

    def test_bool_true_confidence_is_an_error_vote(self):
        # A bool is an int: True used to coerce through float() to 1.0 and
        # pass the range check. It must fail closed instead.
        self._assert_error_vote("true", "JSON bool true")

    def test_bool_false_confidence_is_an_error_vote(self):
        self._assert_error_vote("false", "JSON bool false")

    def test_validated_confidence_rejects_bools_directly(self):
        from crux.core.llm_caller import ConfidenceRejectedError, _validated_confidence

        for raw in (True, False):
            with self.assertRaises(ConfidenceRejectedError,
                                   msg=f"{raw!r} must fail closed"):
                _validated_confidence(raw)

    def test_confidence_rejected_error_is_a_value_error(self):
        # The sync council's parse fallback catches ValueError; subclassing
        # keeps that degradation path working unchanged.
        from crux.core.llm_caller import ConfidenceRejectedError

        self.assertTrue(issubclass(ConfidenceRejectedError, ValueError))

    def test_deliberate_survives_a_hostile_confidence(self):
        """The whole defence of the ingest gate: model output must never crash
        deliberate() out of _aggregate_votes/_extract_key_agreements."""
        import crux.council.async_council as ac

        council = AsyncCouncil.__new__(AsyncCouncil)
        council.config = AsyncCouncilConfig()
        council.gateway_key = "test-key-not-used"
        council._seat_models = {s: getattr(council.config, f"{s}_model") for s in council._SEAT_ORDER}
        council._retry = lambda fn: fn
        council.available_providers = list(council._SEAT_ORDER)

        for value in ("99.0", '"high"'):
            body = _vote_body(
                '{"decision": "APPROVE", "confidence": %s, "dissents": [], "reasoning": "ok"}' % value
            )
            with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test-dummy"}), \
                 mock.patch.object(ac.httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(body)):
                result = asyncio.run(council.deliberate("probe prompt"))
            self.assertIsNotNone(result)
            self.assertNotEqual(
                result.final_recommendation["action"], "AUTO_EXECUTE",
                f"hostile confidence {value!r} must not flip AUTO_EXECUTE",
            )

    def test_numeric_string_confidence_coerces(self):
        """Coercion accepts a numeric string - rejected only when not numeric."""
        payload = '{"decision": "APPROVE", "confidence": "0.9", "dissents": [], "reasoning": "ok"}'
        vote = _run_council_seat(_vote_body(payload))
        self.assertFalse(getattr(vote, "errored", False))
        self.assertAlmostEqual(vote.confidence, 0.9)

    def test_vision_seat_validates_confidence_too(self):
        raw = '{"passed": true, "confidence": 99.0, "observations": "ok", "anomalies": []}'
        result = _run_vision_seat(_vision_vote(raw))
        self.assertEqual(result.confidence, 0.0)
        self.assertIn("Error:", result.observations)


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class JsonExtractionComplexityTests(unittest.TestCase):
    """SEC-2: first-brace-to-last-brace extraction in bounded time."""

    def test_128k_body_extracts_in_bounded_time(self):
        """A valid object must still extract fast: bounded cost + parse success.
        The quadratic pin lives in `test_repeated_open_braces_are_the_quadratic_trigger`."""
        payload = (
            'pre {"decision": "APPROVE", "confidence": 0.9, '
            '"reasoning": "' + "a" * 131000 + '"} tail'
        )
        started = time.monotonic()
        vote = _run_council_seat(_vote_body(payload))
        self.assertLess(time.monotonic() - started, 1.0,
                        "extraction must be bounded, not quadratic")
        self.assertEqual(vote.decision, "APPROVE")

    def test_repeated_open_braces_are_the_quadratic_trigger(self):
        """The audit case: 131000 open braces, no close — under the old regex
        each attempted start position rescanned to end-of-string (quadratic);
        the slice below refuses fast. A single-brace payload provably passed
        against the OLD implementation too, so it pinned nothing."""
        started = time.monotonic()
        vote = _run_council_seat(_vote_body("{" * 131000))
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertTrue(getattr(vote, "errored", False))

    def test_existing_extraction_semantics_hold(self):
        """Enclosing noise may sit outside the braces - slice takes first { to last }."""
        body = 'noise pre {"decision": "APPROVE", "confidence": 0.9} post'
        vote = _run_council_seat(_vote_body(body))
        self.assertFalse(getattr(vote, "errored", False))
        self.assertEqual(vote.decision, "APPROVE")


if __name__ == "__main__":
    unittest.main()
