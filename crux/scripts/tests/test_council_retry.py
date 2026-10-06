"""Council seat retry, finish reasons, and the truncated / malformed-response labels.

No network and no key: `httpx.AsyncClient` is replaced by a scripted fake, the
same seam `test_council_ingest.py` and `test_council_refusal.py` use, and the
gateway key is a dummy environment value. Each fake call is counted, so a test
can say how many times a seat was called.

Timeouts are tens of milliseconds. A "hang" step sleeps far past its seat
deadline and is cancelled by it, so no test waits on a real 600 s deadline.
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
    import crux.council.async_council as ac
    from crux.council.async_council import (
        AsyncCouncil,
        AsyncCouncilConfig,
        AsyncVisualCouncil,
    )
    from crux.core import llm_caller
    HAVE_COUNCIL = True
except Exception:  # router deps unavailable
    HAVE_COUNCIL = False

#: Every attempt's deadline in these tests. Small enough to wait out, large
#: enough that a loaded machine does not time out an attempt that should answer.
TIMEOUT = 0.3

GOOD_VOTE = json.dumps({"decision": "APPROVE", "confidence": 0.9,
                        "dissents": [], "reasoning": "ok"})
GOOD_VISION = json.dumps({"passed": True, "confidence": 0.9,
                          "observations": "clean", "anomalies": []})


class Reply:
    """A scripted gateway response: an HTTP status and a JSON body."""

    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}

    def json(self):
        return self._body

    def raise_for_status(self):
        return None


def body(content, finish="stop", native=None, omit_finish=False):
    choice = {"message": {"content": content}}
    if not omit_finish:
        choice["finish_reason"] = finish
    if native is not None:
        choice["native_finish_reason"] = native
    return {"choices": [choice]}


def ok(content=GOOD_VOTE, finish="stop"):
    return Reply(200, body(content, finish))


class Hang:
    """A step that never answers within any deadline the tests use."""


class Slow:
    """A step that answers after `seconds`, still inside one fresh deadline."""

    def __init__(self, seconds, step):
        self.seconds = seconds
        self.step = step


class Transport:
    """Scripted `httpx.AsyncClient` factory. Steps are consumed per call, in
    order, and the last step repeats once the script runs out."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = 0

    def __call__(self, **kwargs):
        return _Client(self)

    def next_step(self):
        step = self.steps[min(self.calls, len(self.steps) - 1)]
        self.calls += 1
        return step


class _Client:
    def __init__(self, transport):
        self._transport = transport

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None, **kwargs):
        step = self._transport.next_step()
        if isinstance(step, Slow):
            await asyncio.sleep(step.seconds)
            step = step.step
        if isinstance(step, Hang):
            await asyncio.sleep(3)
        if isinstance(step, BaseException):
            raise step
        return step


def make_council(cls=AsyncCouncil, seats=("openai",), max_retries=1, timeout=TIMEOUT):
    council = cls.__new__(cls)
    council.config = AsyncCouncilConfig(timeout_seconds=timeout, max_retries=max_retries)
    council.gateway_key = "test-key-not-used"
    council._seat_models = {s: getattr(council.config, f"{s}_model") for s in council._SEAT_ORDER}
    council._retry = lambda fn: fn
    council.available_providers = list(seats)
    return council


def run_seat(steps, max_retries=1, timeout=TIMEOUT, seat="openai"):
    """Drive one text seat through a scripted transport; return (vote, transport)."""
    transport = Transport(steps)
    council = make_council(seats=(seat,), max_retries=max_retries, timeout=timeout)
    with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test-dummy"}), \
            mock.patch.object(ac.httpx, "AsyncClient", transport):
        vote = asyncio.run(council._call_seat_async(seat, "probe prompt"))
    return vote, transport


def run_vision(steps, max_retries=1, timeout=TIMEOUT):
    transport = Transport(steps)
    council = make_council(AsyncVisualCouncil, seats=("openai",),
                           max_retries=max_retries, timeout=timeout)
    with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test-dummy"}), \
            mock.patch.object(ac.httpx, "AsyncClient", transport):
        result = asyncio.run(council._analyze_seat_async(
            "openai", "OpenAI-vision", "aGk=", "probe"))
    return result, transport


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable - run under uv")
class RetryOnceTests(unittest.TestCase):
    """A seat is called once more after a retryable fault label, under a fresh deadline."""

    def test_timeout_then_answer_is_recovered_with_one_vote(self):
        vote, transport = run_seat([Hang(), ok()])
        self.assertEqual(transport.calls, 2)
        self.assertFalse(vote.errored)
        self.assertEqual(vote.decision, "APPROVE")
        self.assertTrue(vote.recovered)
        self.assertEqual(vote.first_fault_label, "timeout")
        self.assertIsNone(vote.first_finish_reason)  # a timeout carries no reply
        self.assertEqual(vote.finish_reason, "stop")
        self.assertIsNone(vote.fault_label)

    def test_a_seat_that_answers_first_time_is_not_marked(self):
        vote, transport = run_seat([ok()])
        self.assertEqual(transport.calls, 1)
        self.assertFalse(vote.recovered)
        self.assertFalse(vote.retried)
        self.assertIsNone(vote.first_fault_label)
        self.assertEqual(vote.finish_reason, "stop")

    def test_deliberate_yields_exactly_one_vote_per_seat_after_a_retry(self):
        council = make_council(seats=("openai",))
        transport = Transport([Hang(), ok()])
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test-dummy"}), \
                mock.patch.object(ac.httpx, "AsyncClient", transport):
            result = asyncio.run(council.deliberate("probe prompt"))
        self.assertEqual(len(result.votes), 1)
        self.assertEqual(transport.calls, 2)
        self.assertTrue(result.votes[0].recovered)

    def test_three_seats_each_retry_once_and_stay_three_votes(self):
        council = make_council(seats=("openai", "anthropic", "gemini"))
        counts = {}

        class PerModel(Transport):
            def __call__(self, **kw):
                return _PerModelClient(self)

        class _PerModelClient(_Client):
            async def post(self, url, headers=None, json=None, **kwargs):
                model = json["model"]
                n = counts.get(model, 0)
                counts[model] = n + 1
                if n == 0:
                    return Reply(503, {})
                return ok()

        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test-dummy"}), \
                mock.patch.object(ac.httpx, "AsyncClient", PerModel([])):
            result = asyncio.run(council.deliberate("probe prompt"))
        self.assertEqual(len(result.votes), 3)
        self.assertEqual(sorted(counts.values()), [2, 2, 2])
        self.assertTrue(all(v.recovered and not v.errored for v in result.votes))
        self.assertEqual(result.errored_seats, "0/3")

    def test_provider_fault_twice_is_one_errored_vote_naming_the_first_fault(self):
        vote, transport = run_seat([Reply(503, {}), Reply(500, {})])
        self.assertEqual(transport.calls, 2)
        self.assertTrue(vote.errored)
        self.assertTrue(vote.retried)
        self.assertFalse(vote.recovered)
        self.assertEqual(vote.fault_label, "provider")
        self.assertEqual(vote.first_fault_label, "provider")
        self.assertIn("provider", vote.reasoning)

    def test_truncated_then_timeout_keeps_the_first_finish_reason(self):
        vote, transport = run_seat([ok(GOOD_VOTE, finish="length"), Hang()])
        self.assertEqual(transport.calls, 2)
        self.assertTrue(vote.errored)
        self.assertEqual(vote.fault_label, "timeout")
        self.assertIsNone(vote.finish_reason)  # the last attempt got no reply
        self.assertEqual(vote.first_fault_label, "truncated")
        self.assertEqual(vote.first_finish_reason, "length")
        self.assertTrue(vote.retried)

    def test_a_reply_that_stops_on_its_budget_can_recover_on_the_retry(self):
        vote, transport = run_seat([ok(GOOD_VOTE, finish="length"), ok()])
        self.assertFalse(vote.errored)
        self.assertTrue(vote.recovered)
        self.assertEqual(vote.first_fault_label, "truncated")
        self.assertEqual(vote.first_finish_reason, "length")
        self.assertEqual(vote.finish_reason, "stop")

    def test_every_retryable_label_is_retried_exactly_once(self):
        cases = {
            "timeout": Hang(),
            "provider": Reply(502, {}),
            "malformed-response": ok("not json at all"),
            "truncated": ok(GOOD_VOTE, finish="length"),
        }
        for label, step in cases.items():
            with self.subTest(label=label):
                vote, transport = run_seat([step])
                self.assertEqual(transport.calls, 2, "one retry, never two")
                self.assertTrue(vote.errored)
                self.assertEqual(vote.fault_label, label)
                self.assertEqual(vote.first_fault_label, label)

    def test_a_gateway_timeout_status_is_retried_as_a_timeout(self):
        vote, transport = run_seat([Reply(524, {})])
        self.assertEqual(transport.calls, 2)
        self.assertEqual(vote.fault_label, "timeout")

    def test_labels_that_are_not_retryable_are_called_once(self):
        cases = {
            "auth": Reply(401, {}),
            "rate-limit": Reply(429, {}),
            "insufficient-credit": Reply(402, {}),
            "client-config": Reply(400, {}),
            "unexpected-status": Reply(302, {}),
            "unreachable": httpx.ConnectError("no route"),
            "refused": ok("", finish="content_filter"),
            "unknown": KeyError("odd"),
        }
        for label, step in cases.items():
            with self.subTest(label=label):
                vote, transport = run_seat([step])
                self.assertEqual(transport.calls, 1)
                self.assertTrue(vote.errored)
                self.assertEqual(vote.fault_label, label)
                self.assertFalse(vote.retried)
                self.assertIsNone(vote.first_fault_label)

    def test_a_configuration_error_is_not_retried(self):
        for exc in (ValueError("Unknown model: nope"), TypeError("bad")):
            with self.subTest(exc=type(exc).__name__):
                council = make_council()
                with mock.patch.object(ac, "get_model_config", side_effect=exc) as cfg:
                    vote = asyncio.run(council._call_seat_async("openai", "probe"))
                self.assertEqual(cfg.call_count, 1)
                self.assertEqual(vote.fault_label, "client-config")
                self.assertFalse(vote.retried)

    def test_max_retries_zero_calls_once(self):
        vote, transport = run_seat([Reply(503, {}), ok()], max_retries=0)
        self.assertEqual(transport.calls, 1)
        self.assertTrue(vote.errored)
        self.assertFalse(vote.retried)
        self.assertEqual(vote.fault_label, "provider")

    def test_a_parsed_vote_is_never_asked_again(self):
        vote, transport = run_seat([ok(json.dumps(
            {"decision": "MAYBE", "confidence": 0.4, "dissents": [], "reasoning": ""}))])
        self.assertEqual(transport.calls, 1)
        self.assertEqual(vote.decision, "MAYBE")


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable - run under uv")
class MaxRetriesConfigTests(unittest.TestCase):
    def test_default_is_one(self):
        self.assertEqual(AsyncCouncilConfig().max_retries, 1)

    def test_zero_and_one_are_accepted(self):
        for value in (0, 1):
            with self.subTest(value=value):
                self.assertEqual(AsyncCouncilConfig(max_retries=value).max_retries, value)

    def test_any_other_value_is_refused_when_the_config_is_built(self):
        for value in (2, 3, -1, 100, True, False, 1.0, 0.0, "1", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    AsyncCouncilConfig(max_retries=value)


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable - run under uv")
class FreshDeadlineTests(unittest.TestCase):
    """Each attempt runs under its own full per-seat deadline."""

    def test_a_timeout_is_retried_under_a_new_full_deadline(self):
        # Attempt 1 hangs and is cancelled at TIMEOUT. Attempt 2 needs 0.5 x TIMEOUT.
        vote, transport = run_seat([Hang(), Slow(TIMEOUT * 0.5, ok())])
        self.assertFalse(vote.errored)
        self.assertTrue(vote.recovered)

    def test_two_slow_attempts_each_get_the_full_deadline(self):
        # 0.7 x TIMEOUT then a 503, then 0.7 x TIMEOUT then an answer: 1.4 x
        # TIMEOUT in all, more than one deadline, less than two.
        vote, transport = run_seat([Slow(TIMEOUT * 0.7, Reply(503, {})),
                                    Slow(TIMEOUT * 0.7, ok())])
        self.assertEqual(transport.calls, 2)
        self.assertFalse(vote.errored)
        self.assertTrue(vote.recovered)

    def test_positive_control_a_shared_deadline_cannot_recover_that_scenario(self):
        """The same script under ONE deadline over the whole seat times out.

        This is what `deliberate` did before the deadline moved into the seat:
        it proves the test above discriminates a fresh deadline from a shared one.
        """
        council = make_council()
        transport = Transport([Slow(TIMEOUT * 0.7, Reply(503, {})),
                               Slow(TIMEOUT * 0.7, ok())])
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test-dummy"}), \
                mock.patch.object(ac.httpx, "AsyncClient", transport):
            with self.assertRaises(asyncio.TimeoutError):
                asyncio.run(asyncio.wait_for(
                    council._call_seat_async("openai", "probe"), timeout=TIMEOUT))

    def test_the_httpx_client_timeout_is_the_full_seat_deadline_on_each_attempt(self):
        seen = []

        def factory(**kwargs):
            seen.append(kwargs.get("timeout"))
            return _Client(Transport([Reply(503, {})]))

        council = make_council(timeout=123.0)
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test-dummy"}), \
                mock.patch.object(ac.httpx, "AsyncClient", factory):
            asyncio.run(council._call_seat_async("openai", "probe"))
        self.assertEqual(seen, [123.0, 123.0])


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable - run under uv")
class FinishReasonAndLabelTests(unittest.TestCase):
    """The finish reason is recorded, and truncation and malformation have their own labels."""

    def test_length_finish_with_valid_json_is_truncated_not_a_vote(self):
        vote, _ = run_seat([ok(GOOD_VOTE, finish="length")], max_retries=0)
        self.assertTrue(vote.errored)
        self.assertEqual(vote.fault_label, "truncated")
        self.assertEqual(vote.finish_reason, "length")
        self.assertIn("truncated", vote.reasoning)

    def test_positive_control_the_same_json_on_stop_is_a_vote(self):
        vote, _ = run_seat([ok(GOOD_VOTE, finish="stop")], max_retries=0)
        self.assertFalse(vote.errored)
        self.assertEqual(vote.decision, "APPROVE")

    def test_length_finish_with_empty_content_is_truncated(self):
        vote, _ = run_seat([ok("", finish="length")], max_retries=0)
        self.assertEqual(vote.fault_label, "truncated")

    def test_stop_finish_with_empty_content_is_malformed_response_not_client_config(self):
        vote, _ = run_seat([ok("", finish="stop")], max_retries=0)
        self.assertTrue(vote.errored)
        self.assertEqual(vote.fault_label, "malformed-response")
        self.assertIn("malformed-response", vote.reasoning)
        self.assertNotIn("client-config", vote.reasoning)
        self.assertEqual(vote.finish_reason, "stop")

    def test_unparseable_text_is_malformed_response(self):
        for finish in ("stop", "error", "tool_calls"):
            with self.subTest(finish=finish):
                vote, _ = run_seat([ok("no braces here", finish=finish)], max_retries=0)
                self.assertEqual(vote.fault_label, "malformed-response")
                self.assertEqual(vote.finish_reason, finish)

    def test_a_reply_with_no_choices_is_malformed_response_with_a_missing_finish(self):
        vote, _ = run_seat([Reply(200, {"choices": []})], max_retries=0)
        self.assertEqual(vote.fault_label, "malformed-response")
        self.assertEqual(vote.finish_reason, "missing")

    def test_a_wrong_shaped_reply_body_is_malformed_response_and_retried_once(self):
        shapes = {
            "list content": ok([{"type": "text", "text": GOOD_VOTE}]),
            "dict content": ok({"text": GOOD_VOTE}),
            "non-dict message": Reply(200, {"choices": [
                {"message": "oops", "finish_reason": "stop"}]}),
            "list message": Reply(200, {"choices": [
                {"message": [GOOD_VOTE], "finish_reason": "stop"}]}),
            "non-dict choice": Reply(200, {"choices": ["oops"]}),
        }
        for name, step in shapes.items():
            with self.subTest(shape=name):
                vote, transport = run_seat([step], max_retries=0)
                self.assertTrue(vote.errored)
                self.assertEqual(vote.fault_label, "malformed-response")
                self.assertNotIn("unknown", vote.reasoning)
                self.assertEqual(transport.calls, 1)
                vote, transport = run_seat([step, ok()], max_retries=1)
                self.assertFalse(vote.errored, "the second reply is a real vote")
                self.assertTrue(vote.retried)
                self.assertEqual(vote.first_fault_label, "malformed-response")
                self.assertEqual(transport.calls, 2)

    def test_positive_control_a_string_content_reply_is_a_vote_on_the_first_call(self):
        vote, transport = run_seat([ok()], max_retries=1)
        self.assertFalse(vote.errored)
        self.assertEqual(transport.calls, 1)

    def test_a_reply_whose_json_parses_but_is_not_an_object_is_malformed_response(self):
        # First `{` to last `}` cannot decode to a non-object on real input, so
        # the parse step is replaced: this pins the guard behind that slice.
        with mock.patch.object(ac.json, "loads", return_value=[1, 2]):
            with self.assertRaises(llm_caller.MalformedResponseError) as caught:
                AsyncCouncil._reply_object("{}")
        self.assertEqual(AsyncCouncil._redact_error(caught.exception), "malformed-response")
        with self.assertRaises(llm_caller.MalformedResponseError):
            AsyncCouncil._reply_object("no braces")  # control: an unpatched bad reply raises too
        self.assertEqual(AsyncCouncil._reply_object('{"a": 1}'), {"a": 1})

    def test_a_json_object_that_cannot_be_built_into_a_vote_is_malformed_response(self):
        bad = {
            "dissents null": '{"decision": "APPROVE", "confidence": 0.9, "dissents": null}',
            "dissents of numbers": '{"decision": "APPROVE", "confidence": 0.9, "dissents": [1]}',
            "dissents a string": '{"decision": "APPROVE", "confidence": 0.9, "dissents": "x"}',
            "confidence rejected": '{"decision": "APPROVE", "confidence": 99}',
        }
        for name, content in bad.items():
            with self.subTest(case=name):
                vote, _ = run_seat([ok(content)], max_retries=0)
                self.assertTrue(vote.errored)
                self.assertEqual(vote.fault_label, "malformed-response")
                self.assertNotIn("client-config", vote.reasoning)
                self.assertNotIn("unknown", vote.reasoning)

    def test_a_missing_field_keeps_its_default(self):
        vote, _ = run_seat([ok('{"decision": "APPROVE"}')], max_retries=0)
        self.assertFalse(vote.errored)
        self.assertEqual(vote.confidence, 0.5)

    def test_refusal_stays_refused_and_records_its_finish(self):
        vote, transport = run_seat([ok("", finish="content_filter")])
        self.assertEqual(transport.calls, 1)
        self.assertEqual(vote.fault_label, "refused")
        self.assertEqual(vote.finish_reason, "content_filter")

    def test_a_native_refusal_stays_refused_before_the_parse(self):
        step = Reply(200, body(GOOD_VOTE, finish="stop", native="refusal"))
        vote, _ = run_seat([step], max_retries=0)
        self.assertEqual(vote.fault_label, "refused")

    def test_positive_control_other_configuration_errors_keep_client_config(self):
        # An unknown model id and a bad data_collection value both raise
        # ValueError from get_model_config; neither is a malformed reply.
        for message in ("Unknown model: nope. Available: []",
                        "model 'x' sets data_collection='Deny', which is not one of"):
            with self.subTest(message=message):
                council = make_council()
                with mock.patch.object(ac, "get_model_config", side_effect=ValueError(message)):
                    vote = asyncio.run(council._call_seat_async("openai", "probe"))
                self.assertEqual(vote.fault_label, "client-config")
                self.assertIsNone(vote.finish_reason)

    def test_positive_control_a_real_unknown_model_id_is_client_config(self):
        council = make_council()
        council._seat_models["openai"] = "no-such-model-id"
        vote = asyncio.run(council._call_seat_async("openai", "probe"))
        self.assertEqual(vote.fault_label, "client-config")
        self.assertFalse(vote.retried)

    def test_a_non_text_model_is_client_config(self):
        council = make_council()
        with mock.patch.object(ac, "get_model_config",
                               side_effect=llm_caller.NotATextModelError("image model")):
            vote = asyncio.run(council._call_seat_async("openai", "probe"))
        self.assertEqual(vote.fault_label, "client-config")

    def test_finish_vocabulary_is_a_pinned_closed_set(self):
        self.assertEqual(
            set(ac._FINISH_REASONS),
            {"stop", "length", "content_filter", "tool_calls", "error", "other", "missing"},
        )
        self.assertEqual(ac._FINISH_OTHER, "other")
        self.assertEqual(ac._FINISH_MISSING, "missing")
        self.assertNotEqual(ac._FINISH_OTHER, ac._FINISH_MISSING)

    def test_every_recognised_finish_is_recorded_as_itself(self):
        for finish in ("stop", "content_filter", "tool_calls", "error"):
            with self.subTest(finish=finish):
                vote, _ = run_seat([ok("", finish=finish)], max_retries=0)
                self.assertEqual(vote.finish_reason, finish)

    def test_an_unrecognised_finish_records_the_catch_all_and_never_its_text(self):
        marker = "leaky-provider-token-77"
        for raw in (marker, "STOP", "", 7, {"a": marker}):
            with self.subTest(raw=raw):
                vote, _ = run_seat([ok(GOOD_VOTE, finish=raw)], max_retries=0)
                self.assertEqual(vote.finish_reason, "other")
                self.assertNotIn(marker, repr(vote))

    def test_a_reply_with_no_finish_reason_records_missing(self):
        vote, _ = run_seat([Reply(200, body(GOOD_VOTE, omit_finish=True))], max_retries=0)
        self.assertFalse(vote.errored)
        self.assertEqual(vote.finish_reason, "missing")

    def test_a_seat_that_got_no_reply_records_no_finish_reason(self):
        vote, _ = run_seat([httpx.ConnectError("no route")], max_retries=0)
        self.assertIsNone(vote.finish_reason)

    def test_new_exceptions_are_not_value_errors_and_carry_no_reply_text(self):
        from crux.core.llm_caller import MalformedResponseError, ResponseTruncatedError
        for cls in (MalformedResponseError, ResponseTruncatedError):
            self.assertFalse(issubclass(cls, ValueError))
        self.assertEqual(AsyncCouncil._redact_error(ResponseTruncatedError("x")), "truncated")
        self.assertEqual(AsyncCouncil._redact_error(MalformedResponseError("x")),
                         "malformed-response")

    def test_the_sync_parse_keeps_its_signature_and_ignores_length(self):
        data = body("half a vote", finish="length")
        self.assertEqual(llm_caller.parse_gateway_response(data, "m"), "half a vote")
        reply = llm_caller.parse_gateway_reply(data, "m")
        self.assertEqual((reply.content, reply.finish_reason, reply.refused),
                         ("half a vote", "length", False))

    def test_the_sync_parse_still_raises_on_refusal(self):
        with self.assertRaises(llm_caller.ModelRefusedError):
            llm_caller.parse_gateway_response(body("", finish="content_filter"), "m")

    def test_the_error_vote_defaults_leave_the_new_fields_empty(self):
        from crux.core.data_classes import CouncilVote
        vote = CouncilVote(model="m", provider="p", decision="APPROVE",
                           reasoning="", confidence=0.5)
        self.assertEqual(
            (vote.fault_label, vote.finish_reason, vote.recovered, vote.retried,
             vote.first_fault_label, vote.first_finish_reason),
            (None, None, False, False, None, None))


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable - run under uv")
class VisualSeatTests(unittest.TestCase):
    """The visual council retries, labels and records finish reasons as the text council does."""

    def test_timeout_then_answer_is_recovered(self):
        result, transport = run_vision([Hang(), ok(GOOD_VISION)])
        self.assertEqual(transport.calls, 2)
        self.assertFalse(result.errored)
        self.assertTrue(result.passed)
        self.assertTrue(result.recovered)
        self.assertEqual(result.first_fault_label, "timeout")
        self.assertEqual(result.finish_reason, "stop")

    def test_a_first_time_answer_is_not_errored_or_marked(self):
        result, transport = run_vision([ok(GOOD_VISION)])
        self.assertEqual(transport.calls, 1)
        self.assertFalse(result.errored)
        self.assertFalse(result.recovered)
        self.assertFalse(result.retried)

    def test_provider_fault_twice_is_one_errored_result(self):
        result, transport = run_vision([Reply(503, {}), Reply(500, {})])
        self.assertEqual(transport.calls, 2)
        self.assertTrue(result.errored)
        self.assertFalse(result.passed)
        self.assertEqual(result.confidence, 0.0)
        self.assertTrue(result.retried)
        self.assertEqual(result.fault_label, "provider")
        self.assertEqual(result.first_fault_label, "provider")

    def test_truncated_then_timeout_keeps_the_first_finish_reason(self):
        result, _ = run_vision([ok(GOOD_VISION, finish="length"), Hang()])
        self.assertEqual(result.fault_label, "timeout")
        self.assertEqual(result.first_fault_label, "truncated")
        self.assertEqual(result.first_finish_reason, "length")

    def test_labels_that_are_not_retryable_are_called_once(self):
        cases = {"auth": Reply(401, {}), "rate-limit": Reply(429, {}),
                 "client-config": Reply(400, {}), "refused": ok("", finish="content_filter")}
        for label, step in cases.items():
            with self.subTest(label=label):
                result, transport = run_vision([step])
                self.assertEqual(transport.calls, 1)
                self.assertEqual(result.fault_label, label)
                self.assertTrue(result.errored)

    def test_max_retries_zero_calls_once(self):
        result, transport = run_vision([Reply(503, {}), ok(GOOD_VISION)], max_retries=0)
        self.assertEqual(transport.calls, 1)
        self.assertTrue(result.errored)

    def test_empty_content_and_a_wrong_shaped_reply_are_malformed_response(self):
        for content in ("", '{"passed": true, "confidence": 99}'):
            with self.subTest(content=content):
                result, _ = run_vision([ok(content)], max_retries=0)
                self.assertEqual(result.fault_label, "malformed-response")
                self.assertTrue(result.errored)

    def test_fresh_deadline_per_attempt(self):
        result, _ = run_vision([Slow(TIMEOUT * 0.7, Reply(503, {})),
                                Slow(TIMEOUT * 0.7, ok(GOOD_VISION))])
        self.assertFalse(result.errored)
        self.assertTrue(result.recovered)

    def test_an_errored_result_is_told_apart_from_a_failed_check(self):
        """A failed check is `passed: false` from a seat that answered. An errored
        seat also reads `passed: false`, so only the marker separates them."""
        failed_check, _ = run_vision([ok(json.dumps(
            {"passed": False, "confidence": 0.9, "observations": "broken", "anomalies": []}))])
        errored, _ = run_vision([Reply(401, {})])
        self.assertFalse(failed_check.passed)
        self.assertFalse(failed_check.errored)
        self.assertFalse(errored.passed)
        self.assertTrue(errored.errored)
        counted = [r for r in (failed_check, errored) if not r.errored and not r.passed]
        self.assertEqual(counted, [failed_check])

    def test_a_gathered_exception_becomes_an_errored_result(self):
        council = make_council(AsyncVisualCouncil, seats=("openai",))

        async def boom(seat, label, image_b64, prompt):
            raise RuntimeError("seat exploded before its own handler ran")

        council._analyze_seat_async = boom
        results = asyncio.run(council.analyze_image("aGk=", "probe"))
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].errored)


SKILLS = REPO_ROOT / "crux" / "skills"


def _driver_source() -> str:
    """The PEP 723 driver template inside the run-adr-council skill."""
    text = (SKILLS / "run-adr-council" / "SKILL.md").read_text(encoding="utf-8")
    block = text.split("**`driver.py`**", 1)[1].split("```python\n", 1)[1].split("\n```", 1)[0]
    return block


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable - run under uv")
class SkillProseAndDriverTests(unittest.TestCase):
    """The skill prose states the worst case and the driver prints what the votes now carry."""

    def test_both_skills_state_the_worst_case_and_direct_a_background_run(self):
        for name in ("council", "run-adr-council"):
            with self.subTest(skill=name):
                text = " ".join((SKILLS / name / "SKILL.md").read_text(encoding="utf-8").split())
                self.assertIn("about 20 minutes", text)
                self.assertIn("run the driver as a background command", text)
                self.assertNotIn("about ten minutes", text)

    def test_both_skills_name_the_conditioned_and_nit_reports(self):
        for name in ("council", "run-adr-council"):
            with self.subTest(skill=name):
                text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
                for token in ("conditioned", "nit_items", "AUTO_EXECUTE"):
                    self.assertIn(token, text)

    def _run_driver(self, votes):
        import contextlib
        import io
        import tempfile

        council = make_council(seats=("openai", "anthropic", "gemini"))
        result = council._aggregate_votes(votes)
        source = _driver_source().replace(
            'sys.path.insert(0, "${CRUX_PLUGIN_ROOT}/scripts")', "pass")
        with tempfile.TemporaryDirectory() as d:
            Path(d, "prompt.txt").write_text("question", encoding="utf-8")
            fake = mock.Mock()
            fake.deliberate_sync.return_value = result
            out = io.StringIO()
            with mock.patch.object(ac, "create_async_council", return_value=fake), \
                    contextlib.redirect_stdout(out):
                exec(compile(source, "driver.py", "exec"),
                     {"__file__": str(Path(d, "driver.py")), "__name__": "driver"})
        return out.getvalue()

    def test_the_driver_prints_finish_fault_and_retry_markers_and_the_reports(self):
        from crux.core.data_classes import CouncilVote

        votes = [
            CouncilVote(model="a/m", provider="a", decision="APPROVE_WITH_CONDITIONS",
                        reasoning="r", confidence=0.9, dissenting_points=["fix parser"],
                        finish_reason="stop", recovered=True, retried=True,
                        first_fault_label="timeout"),
            CouncilVote(model="b/m", provider="b", decision="APPROVE_WITH_NITS",
                        reasoning="r", confidence=0.9, dissenting_points=["rename x"],
                        finish_reason="stop"),
            CouncilVote(model="c/ERROR", provider="c", decision="DEFER_TO_HUMAN",
                        reasoning="r", confidence=0.0, errored=True, fault_label="truncated",
                        finish_reason="length", retried=True, first_fault_label="truncated",
                        first_finish_reason="length"),
        ]
        out = self._run_driver(votes)
        self.assertIn("Conditioned: True", out)
        self.assertIn("Nits: True", out)
        self.assertIn("[condition] a: fix parser", out)
        self.assertIn("[nit] b: rename x", out)
        self.assertIn("finish: stop  fault: None  retried: True  recovered: True"
                      "  first fault: timeout", out)
        self.assertIn("finish: length  fault: truncated  retried: True  recovered: False"
                      "  first fault: truncated  first finish: length", out)
        self.assertIn("Action: EXECUTE_WITH_MONITORING", out)

    def test_positive_control_the_driver_prints_no_report_lines_when_there_are_none(self):
        from crux.core.data_classes import CouncilVote

        votes = [CouncilVote(model=f"{p}/m", provider=p, decision="APPROVE", reasoning="r",
                             confidence=0.9, finish_reason="stop") for p in "abc"]
        out = self._run_driver(votes)
        self.assertIn("Conditioned: False", out)
        self.assertNotIn("[condition]", out)
        self.assertNotIn("[nit]", out)
        self.assertIn("Action: AUTO_EXECUTE", out)


if __name__ == "__main__":
    unittest.main()
