"""Served-model provenance: parse, check, and the gate seat that enforces it.

No network and no key: every seat talks to an `httpx.MockTransport` whose handler
counts requests, and the gateway key is a dummy environment value. The council
runs against the REAL shipped registry (`crux/scripts/crux/_config`), which
crosses the sync boundary, so nothing here reads a dev-only surface.
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS = REPO_ROOT / "crux" / "scripts"

try:
    sys.path.insert(0, str(SCRIPTS))
    import httpx
    import crux.council.async_council  # noqa: F401
    import crux.core.llm_caller  # noqa: F401
    HAVE_COUNCIL = True
except Exception:  # router deps unavailable
    HAVE_COUNCIL = False

if HAVE_COUNCIL:
    # Outside the guard: a missing name is a failure, never a skip.
    from crux.council.async_council import (
        _RETRYABLE_FAULT_LABELS,
        AsyncCouncil,
        AsyncCouncilConfig,
        AsyncVisualCouncil,
    )
    from crux.core.llm_caller import (
        GatewayReply,
        ModelConfig,
        ServedModelMismatchError,
        check_served,
        parse_gateway_reply,
        reply_provenance,
    )

DUMMY_KEY = "sk-or-test-dummy"
SERVED = {  # the registry's api_strings, as the gateway reports them
    "openai/gpt-6-astra": "OpenAI",
    "anthropic/claude-opus-5.5": "Anthropic",
    "google/gemini-3.1-pro-preview": "Google AI Studio",
}
GOOD_VOTE = json.dumps({"decision": "APPROVE", "confidence": 0.9, "reasoning": "ok",
                        "findings": []})
GOOD_VISION = json.dumps({"passed": True, "confidence": 0.9, "observations": "clean",
                          "anomalies": []})


def reply_body(content, model="__echo__", requested=None, provider="OpenAI",
               gen_id="gen-123", with_model=True):
    body = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "provider": provider, "id": gen_id}
    if with_model:
        body["model"] = requested if model == "__echo__" else model
    return body


class CountingTransport:
    """An `httpx.MockTransport` that counts requests and answers from a function."""

    def __init__(self, answer):
        self.requests = []
        self._answer = answer
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request):
        payload = json.loads(request.content)
        self.requests.append(payload)
        status, body = self._answer(payload, len(self.requests))
        return httpx.Response(status, json=body)


def gate_council(transport, kind="adr"):
    return AsyncCouncil(AsyncCouncilConfig(gate=True, council_kind=kind,
                                           transport=transport.transport,
                                           timeout_seconds=5.0))


def run(coro):
    with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
        return asyncio.run(coro)


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class ParseProvenanceTests(unittest.TestCase):
    def body(self, **extra):
        data = {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]}
        data.update(extra)
        return data

    def test_present_values_are_kept(self):
        reply = parse_gateway_reply(
            self.body(model="google/gemini-3.1-pro-preview", provider="Google AI Studio",
                      id="gen-abc"), "m")
        self.assertEqual(reply.served_model, "google/gemini-3.1-pro-preview")
        self.assertEqual(reply.served_provider, "Google AI Studio")
        self.assertEqual(reply.generation_id, "gen-abc")

    def test_absent_values_are_none(self):
        reply = parse_gateway_reply(self.body(), "m")
        self.assertEqual((reply.served_model, reply.served_provider, reply.generation_id),
                         (None, None, None))

    def test_a_value_that_is_not_a_string_is_none(self):
        for bad in (7, 1.5, True, ["a"], {"a": 1}, None):
            with self.subTest(bad=bad):
                reply = parse_gateway_reply(self.body(model=bad, provider=bad, id=bad), "m")
                self.assertIsNone(reply.served_model)
                self.assertIsNone(reply.served_provider)
                self.assertIsNone(reply.generation_id)

    def test_a_str_subclass_is_none(self):
        class Sub(str):
            pass
        self.assertIsNone(parse_gateway_reply(self.body(model=Sub("a/b")), "m").served_model)

    def test_length_bounds_are_one_to_two_hundred(self):
        self.assertEqual(parse_gateway_reply(self.body(model="a" * 200), "m").served_model,
                         "a" * 200)
        self.assertIsNone(parse_gateway_reply(self.body(model="a" * 201), "m").served_model)
        self.assertIsNone(parse_gateway_reply(self.body(model=""), "m").served_model)

    def test_non_printable_values_are_none(self):
        for bad in ("a\nb", "a\tb", "café", "a\x00b", "\x7f"):
            with self.subTest(bad=bad):
                self.assertIsNone(parse_gateway_reply(self.body(model=bad), "m").served_model)

    def test_a_body_with_no_choices_still_carries_provenance(self):
        reply = parse_gateway_reply({"model": "m/x", "provider": "P", "id": "g1",
                                     "choices": []}, "m")
        self.assertEqual((reply.content, reply.served_model, reply.served_provider,
                          reply.generation_id), ("", "m/x", "P", "g1"))
        reply = parse_gateway_reply({"model": "m/x"}, "m")
        self.assertEqual(reply.served_model, "m/x")

    def test_a_non_object_choice_still_carries_provenance(self):
        reply = parse_gateway_reply({"choices": ["x"], "model": "m/x"}, "m")
        self.assertEqual(reply.served_model, "m/x")

    def test_positional_construction_still_works(self):
        reply = GatewayReply("t", "stop", False)
        self.assertEqual((reply.served_model, reply.served_provider, reply.generation_id),
                         (None, None, None))


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class CheckServedTests(unittest.TestCase):
    def cfg(self, accepted):
        return ModelConfig(api_string="g/m", display_name="d", context_window=1,
                           max_output_tokens=1, base_url="u", auth_header="a",
                           auth_prefix="b", accepted_served_models=accepted)

    def reply(self, served):
        return GatewayReply("", "stop", False, served_model=served)

    def test_an_exact_match_passes(self):
        self.assertIsNone(check_served(self.cfg(("g/m",)), self.reply("g/m")))

    def test_near_matches_are_refused(self):
        cfg = self.cfg(("g/m",))
        for served in ("g/m-20260903", "G/M", "g/m ", "g/", "g/m:free", "gm"):
            with self.subTest(served=served), self.assertRaises(ServedModelMismatchError):
                check_served(cfg, self.reply(served))

    def test_absent_served_model_is_refused(self):
        with self.assertRaises(ServedModelMismatchError):
            check_served(self.cfg(("g/m",)), self.reply(None))

    def test_an_entry_with_no_declared_set_is_refused_even_on_a_match(self):
        with self.assertRaises(ServedModelMismatchError):
            check_served(self.cfg(None), self.reply("g/m"))

    def test_an_empty_declared_set_declares_nothing_and_is_refused(self):
        with self.assertRaises(ServedModelMismatchError) as caught:
            check_served(self.cfg(()), self.reply("g/m"))
        self.assertIn("declares no accepted served models", str(caught.exception))

    def test_the_error_is_not_a_value_error_and_never_echoes_the_served_value(self):
        self.assertFalse(issubclass(ServedModelMismatchError, ValueError))
        with self.assertRaises(ServedModelMismatchError) as caught:
            check_served(self.cfg(("g/m",)), self.reply("evil/sk-or-secret-looking"))
        self.assertNotIn("evil", str(caught.exception))
        self.assertNotIn("sk-or", str(caught.exception))


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class GateSeatServedModelTests(unittest.TestCase):
    """The shipped registry, a gate council, and a transport that lies on demand."""

    def matching(self, payload, n):
        return 200, reply_body(GOOD_VOTE, requested=payload["model"],
                               provider=SERVED.get(payload["model"], "X"))

    def test_a_matching_served_model_responds_with_provenance(self):
        transport = CountingTransport(self.matching)
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
            council = gate_council(transport)
            vote = asyncio.run(council._call_seat_async("openai", "q"))
        self.assertFalse(vote.errored)
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(vote.served_model, "openai/gpt-6-astra")
        self.assertEqual(vote.served_provider, "OpenAI")
        self.assertEqual(vote.generation_id, "gen-123")

    def test_a_mismatched_served_model_errors_the_seat_once_and_substitutes_nothing(self):
        def answer(payload, n):
            return 200, reply_body(GOOD_VOTE, model="openai/gpt-6-astra-20260903")
        transport = CountingTransport(answer)
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
            vote = asyncio.run(gate_council(transport)._call_seat_async("openai", "q"))
        self.assertTrue(vote.errored)
        self.assertEqual(vote.fault_label, "served-model")
        self.assertEqual(len(transport.requests), 1, "a served-model fault is never retried")
        self.assertEqual(vote.served_model, "openai/gpt-6-astra-20260903")
        self.assertEqual(vote.requested_model, "openai/gpt-6-astra")
        self.assertEqual(vote.registry_key, "gpt-6-astra")
        self.assertEqual(vote.decision, "DEFER_TO_HUMAN")
        self.assertEqual(vote.confidence, 0.0)
        self.assertEqual(vote.findings, [])

    def test_an_absent_served_model_errors_the_seat(self):
        def answer(payload, n):
            return 200, reply_body(GOOD_VOTE, with_model=False)
        transport = CountingTransport(answer)
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
            vote = asyncio.run(gate_council(transport)._call_seat_async("openai", "q"))
        self.assertTrue(vote.errored)
        self.assertEqual(vote.fault_label, "served-model")
        self.assertIsNone(vote.served_model)
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(vote.generation_id, "gen-123", "the reply's id is still recorded")

    def test_the_request_count_observes_a_retry_when_the_fault_is_retryable(self):
        # Positive control for the "called exactly once" assertions above: the same
        # counter reads 2 for a malformed reply, which the seat does retry.
        def answer(payload, n):
            return 200, reply_body("not json at all", requested=payload["model"])
        transport = CountingTransport(answer)
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
            vote = asyncio.run(gate_council(transport)._call_seat_async("openai", "q"))
        self.assertEqual(vote.fault_label, "malformed-response")
        self.assertEqual(len(transport.requests), 2)

    def test_the_served_model_label_is_not_retryable(self):
        self.assertNotIn("served-model", _RETRYABLE_FAULT_LABELS)
        self.assertIn("malformed-response", _RETRYABLE_FAULT_LABELS)
        self.assertEqual(AsyncCouncil._ERROR_LABELS["ServedModelMismatchError"],
                         "served-model")

    def test_a_truncated_reply_with_a_wrong_served_model_keeps_truncated_and_its_retry(self):
        # ADR-0141 classification and retry come first: a truncated reply yields no
        # vote, so its served model is recorded and not enforced.
        def answer(payload, n):
            body = reply_body(GOOD_VOTE, model="other/model")
            body["choices"][0]["finish_reason"] = "length"
            return 200, body
        transport = CountingTransport(answer)
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
            vote = asyncio.run(gate_council(transport)._call_seat_async("openai", "q"))
        self.assertEqual(vote.fault_label, "truncated")
        self.assertEqual(len(transport.requests), 2)
        self.assertTrue(vote.retried)
        self.assertEqual((vote.first_fault_label, vote.first_finish_reason), ("truncated", "length"))
        self.assertEqual(vote.finish_reason, "length")
        self.assertEqual([a["served_model_match"] for a in vote.attempts], [False, False])

    def test_a_non_gate_council_records_provenance_and_does_not_error_without_a_model(self):
        # The scope decision: only gate councils verify. Older callers and their
        # fakes send replies with no `model` field.
        def answer(payload, n):
            return 200, reply_body(GOOD_VOTE, with_model=False)
        transport = CountingTransport(answer)
        config = AsyncCouncilConfig(transport=transport.transport, timeout_seconds=5.0)
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
            vote = asyncio.run(AsyncCouncil(config)._call_seat_async("openai", "q"))
        self.assertFalse(vote.errored)
        self.assertIsNone(vote.served_model)
        self.assertEqual(vote.generation_id, "gen-123")

    def test_a_non_gate_council_records_a_mismatched_served_model_without_erroring(self):
        def answer(payload, n):
            return 200, reply_body(GOOD_VOTE, model="somewhere/else")
        transport = CountingTransport(answer)
        config = AsyncCouncilConfig(transport=transport.transport, timeout_seconds=5.0)
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
            vote = asyncio.run(AsyncCouncil(config)._call_seat_async("openai", "q"))
        self.assertFalse(vote.errored)
        self.assertEqual(vote.served_model, "somewhere/else")


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class VisualSeatServedModelTests(unittest.TestCase):
    def visual(self, answer, gate):
        transport = CountingTransport(answer)
        config = AsyncCouncilConfig(gate=gate, council_kind="adr" if gate else None,
                                    transport=transport.transport, timeout_seconds=5.0)
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
            council = AsyncVisualCouncil(config)
            results = asyncio.run(council.analyze_image("aGk=", "probe"))
        return transport, results

    def test_a_visual_seat_in_gate_mode_errors_on_a_mismatch(self):
        def answer(payload, n):
            served = ("openai/gpt-6-astra-other" if payload["model"].startswith("openai/")
                      else payload["model"])
            return 200, reply_body(GOOD_VISION, model=served,
                                   provider=SERVED.get(payload["model"], "X"))
        transport, results = self.visual(answer, gate=True)
        by_name = {r.model_name: r for r in results}
        bad, good = by_name["OpenAI-vision"], by_name["Claude-vision"]
        self.assertTrue(bad.errored)
        self.assertEqual(bad.fault_label, "served-model")
        self.assertEqual(bad.served_model, "openai/gpt-6-astra-other")
        self.assertFalse(good.errored)  # positive control: a matching seat answers
        self.assertEqual(good.served_model, "anthropic/claude-opus-5.5")
        self.assertEqual(len(transport.requests), 2, "one request per seat, no retry")

    def test_the_same_mismatch_outside_gate_mode_is_recorded_not_errored(self):
        def answer(payload, n):
            return 200, reply_body(GOOD_VISION, model="somewhere/else")
        _, results = self.visual(answer, gate=False)
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertFalse(r.errored)
            self.assertEqual(r.served_model, "somewhere/else")
            self.assertEqual(r.generation_id, "gen-123")


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class MalformedReplyServedModelTests(unittest.TestCase):
    """A malformed reply keeps ADR-0141's `malformed-response` and its one retry,
    whatever served model it reports: it yields no vote, so the served-model check
    is not enforced on it. The retry's reply is checked like any other.

    The first attempt carries a wrong, charset-valid served model AND a message the
    content parse refuses. The second attempt is correct, or wrong as stated.
    """

    BAD_MESSAGES = {
        "message-not-an-object": "notadict",
        "content-not-text": {"content": 5},
        "content-a-list": {"content": ["a"]},
    }

    def sequence(self, message, first_model, second_model=None):
        def answer(payload, n):
            if n == 1:
                body = reply_body(GOOD_VOTE, model=first_model)
                body["choices"][0]["message"] = message
                return 200, body
            if second_model is not None:
                return 200, reply_body(GOOD_VOTE, model=second_model)
            return 200, reply_body(GOOD_VOTE, requested=payload["model"])
        return CountingTransport(answer)

    def test_a_wrong_served_model_on_a_malformed_reply_keeps_its_retry_and_recovers(self):
        for name, message in self.BAD_MESSAGES.items():
            with self.subTest(case=name):
                transport = self.sequence(message, "evil/other")
                with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
                    vote = asyncio.run(gate_council(transport)._call_seat_async("openai", "q"))
                self.assertEqual(len(transport.requests), 2)
                self.assertFalse(vote.errored)
                self.assertTrue(vote.retried)
                self.assertTrue(vote.recovered)
                self.assertEqual(vote.first_fault_label, "malformed-response")
                self.assertEqual(vote.served_model, "openai/gpt-6-astra")
                self.assertEqual([a["served_model"] for a in vote.attempts],
                                 ["evil/other", "openai/gpt-6-astra"])
                self.assertEqual([a["fault_label"] for a in vote.attempts],
                                 ["malformed-response", None])

    def test_a_wrong_served_model_on_the_retrys_well_formed_reply_errors_the_seat(self):
        for name, message in self.BAD_MESSAGES.items():
            with self.subTest(case=name):
                transport = self.sequence(message, "evil/other", second_model="evil/again")
                with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
                    vote = asyncio.run(gate_council(transport)._call_seat_async("openai", "q"))
                self.assertEqual(len(transport.requests), 2, "never a third call")
                self.assertTrue(vote.errored)
                self.assertEqual(vote.fault_label, "served-model")
                self.assertTrue(vote.retried)
                self.assertEqual(vote.first_fault_label, "malformed-response")
                self.assertEqual(vote.served_model, "evil/again")
                self.assertEqual(vote.decision, "DEFER_TO_HUMAN")

    def test_the_same_malformed_reply_with_the_right_model_is_retried_and_recovers(self):
        # Control: the served model on the malformed reply changes nothing.
        for name, message in self.BAD_MESSAGES.items():
            with self.subTest(case=name):
                transport = self.sequence(message, "openai/gpt-6-astra")
                with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
                    vote = asyncio.run(gate_council(transport)._call_seat_async("openai", "q"))
                self.assertEqual(len(transport.requests), 2)
                self.assertFalse(vote.errored)
                self.assertTrue(vote.retried)
                self.assertEqual(vote.served_model, "openai/gpt-6-astra")

    def test_outside_gate_mode_a_malformed_reply_is_still_retried(self):
        transport = self.sequence("notadict", "somewhere/else")
        config = AsyncCouncilConfig(transport=transport.transport, timeout_seconds=5.0)
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
            vote = asyncio.run(AsyncCouncil(config)._call_seat_async("openai", "q"))
        self.assertEqual(len(transport.requests), 2)
        self.assertFalse(vote.errored)

    def test_a_malformed_reply_with_no_served_model_keeps_malformed_response_and_its_retry(self):
        def answer(payload, n):
            body = reply_body(GOOD_VOTE, with_model=False)
            body["choices"][0]["message"] = "notadict"
            return 200, body
        transport = CountingTransport(answer)
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
            vote = asyncio.run(gate_council(transport)._call_seat_async("openai", "q"))
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(vote.fault_label, "malformed-response")
        self.assertEqual(vote.first_fault_label, "malformed-response")

    def test_reply_provenance_is_what_parse_gateway_reply_reports(self):
        body = {"choices": [{"message": {"content": "x"}}], "model": "a/b",
                "provider": "P", "id": "g1"}
        self.assertEqual(reply_provenance(body), ("a/b", "P", "g1"))
        reply = parse_gateway_reply(body, "m")
        self.assertEqual(reply_provenance(body),
                         (reply.served_model, reply.served_provider, reply.generation_id))
        self.assertEqual(reply_provenance({"model": 7, "provider": "a\nb", "id": ""}),
                         (None, None, None))
        # A body whose message is malformed still yields its provenance.
        self.assertEqual(reply_provenance({"choices": [{"message": "x"}], "model": "a/b"}),
                         ("a/b", None, None))


class RawTransport:
    """Counts requests and answers each with raw bytes, never re-encoded as JSON."""

    def __init__(self, content, content_type="text/html"):
        self.requests = 0
        self._content, self._type = content, content_type
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request):
        self.requests += 1
        return httpx.Response(200, content=self._content,
                              headers={"content-type": self._type})


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class BrokenBodyServedModelTests(unittest.TestCase):
    """A body that cannot yield a vote keeps the label the reply parse gives it in a
    gate council, as outside one: the served-model check is never enforced on it."""

    BROKEN_CHOICES = {
        "choices-an-int": {"choices": 5, "model": "evil/other"},
        "choices-an-object": {"choices": {"a": 1}, "model": "evil/other"},
        "choices-an-int-no-model": {"choices": 5},
        "top-level-array": [1, 2],
    }
    OLD_LABELS = {  # what the same bodies read as outside gate mode
        "choices-an-int": "client-config",
        "choices-an-object": "unknown",
        "choices-an-int-no-model": "client-config",
        "top-level-array": "unknown",
    }

    def seat(self, body, gate):
        transport = CountingTransport(lambda payload, n: (200, body))
        if gate:
            council = gate_council(transport)
        else:
            council = AsyncCouncil(AsyncCouncilConfig(transport=transport.transport,
                                                      timeout_seconds=5.0))
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
            vote = asyncio.run(council._call_seat_async("openai", "q"))
        return vote, transport

    def test_a_dict_body_with_broken_choices_keeps_its_parse_label_in_gate_mode(self):
        for name, body in self.BROKEN_CHOICES.items():
            with self.subTest(case=name):
                vote, transport = self.seat(body, gate=True)
                self.assertTrue(vote.errored)
                self.assertEqual(vote.fault_label, self.OLD_LABELS[name])
                self.assertNotEqual(vote.fault_label, "served-model")
                self.assertEqual(len(transport.requests), 1)
                self.assertFalse(vote.retried)

    def test_the_same_bodies_keep_their_old_labels_outside_gate_mode(self):
        # The same labels outside gate mode: gate mode adds no classification of its own.
        for name, body in self.BROKEN_CHOICES.items():
            with self.subTest(case=name):
                vote, _ = self.seat(body, gate=False)
                self.assertTrue(vote.errored)
                self.assertEqual(vote.fault_label, self.OLD_LABELS[name])

    def test_an_accepted_served_model_with_broken_choices_keeps_its_parse_label_in_gate_mode(self):
        # The served-model label is the served-model check's verdict, not a blanket
        # label for every gate parse fault: an accepted model keeps the parse label.
        cases = {
            "choices-an-int": ({"choices": 5, "model": "openai/gpt-6-astra"}, "client-config"),
            "choices-an-object": ({"choices": {"a": 1}, "model": "openai/gpt-6-astra"}, "unknown"),
        }
        for name, (body, label) in cases.items():
            with self.subTest(case=name):
                vote, transport = self.seat(body, gate=True)
                self.assertTrue(vote.errored)
                self.assertEqual(vote.fault_label, label)
                self.assertEqual(len(transport.requests), 1)
                self.assertEqual(vote.served_model, "openai/gpt-6-astra")

    def test_a_body_that_is_not_json_is_malformed_response_and_retried_once_in_gate_mode(self):
        raw = RawTransport(b"<html>bad gateway page</html>")
        council = AsyncCouncil(AsyncCouncilConfig(gate=True, council_kind="adr",
                                                  transport=raw.transport,
                                                  timeout_seconds=5.0))
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
            vote = asyncio.run(council._call_seat_async("openai", "q"))
        self.assertEqual(vote.fault_label, "malformed-response")
        self.assertEqual(raw.requests, 2)
        self.assertTrue(vote.retried)
        self.assertEqual(vote.first_fault_label, "malformed-response")
        self.assertIsNone(vote.served_model)
        self.assertEqual([a["replied"] for a in vote.attempts], [False, False])

    def test_a_body_that_is_not_json_is_still_retried_outside_gate_mode(self):
        raw = RawTransport(b"<html>bad gateway page</html>")
        council = AsyncCouncil(AsyncCouncilConfig(transport=raw.transport,
                                                  timeout_seconds=5.0))
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
            vote = asyncio.run(council._call_seat_async("openai", "q"))
        self.assertEqual(raw.requests, 2)
        self.assertTrue(vote.errored)
        self.assertTrue(vote.retried)
        self.assertNotEqual(vote.fault_label, "served-model")


if __name__ == "__main__":
    unittest.main()
