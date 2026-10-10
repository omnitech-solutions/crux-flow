"""The served-provider match on a gate council seat, and its order against the retry.

Every vote-yielding reply of a gate seat must report a served model in its entry's
accepted set and then a served provider in its entry's accepted_served_providers set,
by exact equality. A reply the retry rules classify as retryable keeps that label and
its one retry first; the two checks are enforced only on a reply that would otherwise
yield a vote. A mismatch errors the seat, is never retried, and nothing replaces it.

The ten fixtures the decision names are the classes below marked F1-F10. Every
refusal or error case is paired with a control on the same seam that does yield a
vote, so a pass cannot come from a fixture that never reached the check.

No network and no key: every seat talks to an `httpx.MockTransport` that counts
requests, and the gateway key is a dummy environment value. The council runs against
the REAL shipped registry, or a temporary copy of it, so nothing here reads a
dev-only surface.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import sys
import tempfile
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
        CouncilAssignmentRefused,
    )
    from crux.core import llm_caller
    from crux.core.llm_caller import (
        GatewayReply,
        ModelConfig,
        ServedModelMismatchError,
        ServedProviderMismatchError,
        check_served_provider,
    )

DUMMY_KEY = "sk-or-test-dummy"
REAL_REGISTRY = SCRIPTS / "crux" / "_config" / "llm_router_config.json"
#: The seat, its requested model, and the provider label the round-1 record observed.
SEATS = {
    "openai": ("openai/gpt-6-astra", "OpenAI"),
    "anthropic": ("anthropic/claude-opus-5.5", "Anthropic"),
    "gemini": ("google/gemini-3.1-pro-preview", "Google AI Studio"),
}
#: Each seat's `serving_providers` pin slug, as the request sends it.
PIN_SLUG = {"openai": "openai", "anthropic": "anthropic", "gemini": "google-ai-studio"}
GOOD_VOTE = json.dumps({"decision": "APPROVE", "confidence": 0.9, "reasoning": "ok",
                        "findings": []})
OMIT = object()


def body(content=GOOD_VOTE, *, model, provider, finish="stop", gen_id="gen-1"):
    """A gateway reply. `OMIT` drops the `model` or `provider` key."""
    out = {"id": gen_id, "choices": [{"message": {"content": content}, "finish_reason": finish}]}
    if model is not OMIT:
        out["model"] = model
    if provider is not OMIT:
        out["provider"] = provider
    return out


class Sequence:
    """An `httpx.MockTransport` that answers request N with `replies[N-1]` and counts."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request):
        self.requests.append(json.loads(request.content))
        reply = self.replies[min(len(self.requests), len(self.replies)) - 1]
        if isinstance(reply, int):
            return httpx.Response(reply, json={"error": {"message": "x"}})
        return httpx.Response(200, json=reply)


def gate_vote(transport, seat="openai"):
    with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
        council = AsyncCouncil(AsyncCouncilConfig(gate=True, council_kind="adr",
                                                  transport=transport.transport,
                                                  timeout_seconds=5.0))
        return asyncio.run(council._call_seat_async(seat, "q"))


def good(seat="openai", **over):
    model, provider = SEATS[seat]
    return body(**{"model": model, "provider": provider, **over})


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class CheckServedProviderTests(unittest.TestCase):
    def cfg(self, providers):
        return ModelConfig(api_string="g/m", display_name="d", context_window=1,
                           max_output_tokens=1, base_url="u", auth_header="a",
                           auth_prefix="b", accepted_served_models=("g/m",),
                           accepted_served_providers=providers)

    def reply(self, provider):
        return GatewayReply("", "stop", False, served_model="g/m", served_provider=provider)

    def test_an_exact_match_passes(self):
        self.assertIsNone(check_served_provider(self.cfg(("Google AI Studio",)),
                                                self.reply("Google AI Studio")))

    def test_near_matches_and_pin_slugs_are_refused(self):
        cfg = self.cfg(("Google AI Studio",))
        for provider in ("google-ai-studio", "google ai studio", "Google AI studio",
                         "Google AI Studio ", " Google AI Studio", "Google-AI-Studio", "Google"):
            with self.subTest(provider=provider), self.assertRaises(ServedProviderMismatchError):
                check_served_provider(cfg, self.reply(provider))

    def test_an_absent_provider_is_refused(self):
        with self.assertRaises(ServedProviderMismatchError):
            check_served_provider(self.cfg(("OpenAI",)), self.reply(None))

    def test_an_undeclared_or_empty_set_is_refused_even_on_a_plausible_label(self):
        for providers in (None, ()):
            with self.subTest(providers=providers), self.assertRaises(ServedProviderMismatchError):
                check_served_provider(self.cfg(providers), self.reply("OpenAI"))

    def test_the_error_is_its_own_class_and_never_echoes_the_reply(self):
        self.assertFalse(issubclass(ServedProviderMismatchError, ValueError))
        self.assertFalse(issubclass(ServedProviderMismatchError, ServedModelMismatchError))
        with self.assertRaises(ServedProviderMismatchError) as caught:
            check_served_provider(self.cfg(("OpenAI",)), self.reply("evil sk-or-secret"))
        self.assertNotIn("evil", str(caught.exception))
        self.assertNotIn("sk-or", str(caught.exception))

    def test_the_fault_label_is_its_own_and_is_not_retryable(self):
        self.assertEqual(AsyncCouncil._ERROR_LABELS["ServedProviderMismatchError"],
                         "served-provider-mismatch")
        self.assertEqual(AsyncCouncil._fault_label(ServedProviderMismatchError("x")),
                         "served-provider-mismatch")
        self.assertNotIn("served-provider-mismatch", _RETRYABLE_FAULT_LABELS)
        self.assertNotIn("served-model", _RETRYABLE_FAULT_LABELS)
        # Positive control: the retryable set is the one the retry rules name.
        self.assertEqual(_RETRYABLE_FAULT_LABELS,
                         frozenset({"timeout", "provider", "malformed-response", "truncated"}))


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class F1SeededLabelTests(unittest.TestCase):
    def test_each_seeded_label_passes_and_records_both_matches(self):
        for seat in SEATS:
            with self.subTest(seat=seat):
                transport = Sequence(good(seat))
                vote = gate_vote(transport, seat)
                self.assertFalse(vote.errored)
                self.assertEqual(len(transport.requests), 1)
                self.assertEqual(vote.served_provider, SEATS[seat][1])
                self.assertEqual(len(vote.attempts), 1)
                self.assertTrue(vote.attempts[0]["served_model_match"])
                self.assertTrue(vote.attempts[0]["served_provider_match"])


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class F2PinSlugTests(unittest.TestCase):
    def test_a_pin_slug_reported_as_the_provider_errors_the_seat(self):
        for seat in SEATS:
            with self.subTest(seat=seat):
                transport = Sequence(good(seat, provider=PIN_SLUG[seat]))
                vote = gate_vote(transport, seat)
                self.assertTrue(vote.errored)
                self.assertEqual(vote.fault_label, "served-provider-mismatch")
                self.assertEqual(len(transport.requests), 1)
                self.assertEqual(vote.served_provider, PIN_SLUG[seat])
                self.assertFalse(vote.attempts[0]["served_provider_match"])
                self.assertTrue(vote.attempts[0]["served_model_match"])


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class F3AbsentProviderTests(unittest.TestCase):
    def test_an_absent_provider_errors_with_no_retry(self):
        transport = Sequence(good(provider=OMIT), good())
        vote = gate_vote(transport)
        self.assertTrue(vote.errored)
        self.assertEqual(vote.fault_label, "served-provider-mismatch")
        self.assertEqual(len(transport.requests), 1, "never retried, though the second reply is good")
        self.assertFalse(vote.retried)
        self.assertIsNone(vote.served_provider)
        self.assertIsNone(vote.attempts[0]["served_provider"])

    def test_a_provider_that_is_not_a_printable_string_reads_as_absent(self):
        for bad in (7, None, ["OpenAI"], "Open\nAI", ""):
            with self.subTest(bad=bad):
                vote = gate_vote(Sequence(good(provider=bad)))
                self.assertEqual(vote.fault_label, "served-provider-mismatch")


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class F4OutsideSetTests(unittest.TestCase):
    def test_an_outside_provider_on_a_stop_reply_errors_with_no_retry_and_no_substitute(self):
        transport = Sequence(good(provider="Azure"), good())
        vote = gate_vote(transport)
        self.assertTrue(vote.errored)
        self.assertEqual(vote.fault_label, "served-provider-mismatch")
        self.assertEqual(len(transport.requests), 1)
        self.assertFalse(vote.retried)
        self.assertEqual((vote.decision, vote.confidence, vote.findings),
                         ("DEFER_TO_HUMAN", 0.0, []))
        self.assertEqual(vote.registry_key, "gpt-6-astra")
        self.assertEqual(vote.requested_model, "openai/gpt-6-astra")
        self.assertEqual(transport.requests[0]["model"], "openai/gpt-6-astra")

    def test_control_the_same_reply_with_the_seeded_provider_is_a_vote(self):
        transport = Sequence(good(), good(provider="Azure"))
        vote = gate_vote(transport)
        self.assertFalse(vote.errored)
        self.assertEqual(vote.decision, "APPROVE")
        self.assertEqual(len(transport.requests), 1)

    def test_a_whole_council_with_one_outside_provider_keeps_two_seats_and_three_requests(self):
        def answer(request):
            payload = json.loads(request.content)
            seat = next(s for s, (m, _) in SEATS.items() if m == payload["model"])
            reply = good(seat, provider="Azure") if seat == "anthropic" else good(seat)
            return httpx.Response(200, json=reply)
        requests = []

        def handler(request):
            requests.append(request)
            return answer(request)
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
            council = AsyncCouncil(AsyncCouncilConfig(
                gate=True, council_kind="adr", transport=httpx.MockTransport(handler),
                timeout_seconds=5.0))
            result = asyncio.run(council.deliberate("q"))
        self.assertEqual(len(requests), 3, "no substitute seat is called")
        by_seat = {v.provider: v for v in result.votes}
        self.assertEqual(by_seat["anthropic"].fault_label, "served-provider-mismatch")
        self.assertFalse(by_seat["openai"].errored)
        self.assertFalse(by_seat["gemini"].errored)
        self.assertEqual(result.final_recommendation["errored_seats"], "1/3")


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class F5RetriedSeatTests(unittest.TestCase):
    def test_a_retried_seat_whose_second_reply_is_outside_the_set_errors(self):
        for first, label in ((503, "provider"), (good(content="not json"), "malformed-response")):
            with self.subTest(first=label):
                transport = Sequence(first, good(provider="Azure"))
                vote = gate_vote(transport)
                self.assertEqual(len(transport.requests), 2)
                self.assertTrue(vote.errored)
                self.assertEqual(vote.fault_label, "served-provider-mismatch")
                self.assertTrue(vote.retried)
                self.assertEqual(vote.first_fault_label, label)

    def test_control_the_same_retry_with_the_seeded_provider_recovers(self):
        transport = Sequence(503, good())
        vote = gate_vote(transport)
        self.assertEqual(len(transport.requests), 2)
        self.assertFalse(vote.errored)
        self.assertTrue(vote.recovered)
        self.assertEqual(vote.first_fault_label, "provider")


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class F6TruncatedOutsideTests(unittest.TestCase):
    def test_a_truncated_reply_outside_the_set_keeps_truncated_and_is_retried_once(self):
        transport = Sequence(good(provider="Azure", finish="length"),
                             good(finish="length"))
        vote = gate_vote(transport)
        self.assertEqual(len(transport.requests), 2)
        self.assertTrue(vote.errored)
        self.assertEqual(vote.fault_label, "truncated")
        self.assertTrue(vote.retried)
        self.assertEqual((vote.first_fault_label, vote.first_finish_reason), ("truncated", "length"))
        first = vote.attempts[0]
        self.assertEqual((first["fault_label"], first["finish_reason"], first["served_provider"],
                          first["served_provider_match"]), ("truncated", "length", "Azure", False))


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class F7TruncatedThenOutsideTests(unittest.TestCase):
    def test_the_retrys_outside_reply_errors_once_and_keeps_the_three_markers(self):
        transport = Sequence(good(provider="Azure", finish="length"),
                             good(provider="Azure"), good())
        vote = gate_vote(transport)
        self.assertEqual(len(transport.requests), 2, "not retried again")
        self.assertTrue(vote.errored)
        self.assertEqual(vote.fault_label, "served-provider-mismatch")
        self.assertTrue(vote.retried)
        self.assertEqual((vote.first_fault_label, vote.first_finish_reason), ("truncated", "length"))
        self.assertEqual([a["fault_label"] for a in vote.attempts],
                         ["truncated", "served-provider-mismatch"])


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class F8TruncatedThenInSetTests(unittest.TestCase):
    def test_an_in_set_retry_yields_a_recovered_vote(self):
        transport = Sequence(good(provider="Azure", finish="length"), good())
        vote = gate_vote(transport)
        self.assertEqual(len(transport.requests), 2)
        self.assertFalse(vote.errored)
        self.assertEqual(vote.decision, "APPROVE")
        self.assertTrue(vote.recovered)
        self.assertTrue(vote.retried)
        self.assertEqual((vote.first_fault_label, vote.first_finish_reason), ("truncated", "length"))
        self.assertEqual(vote.served_provider, "OpenAI")

    def test_each_attempt_records_its_own_provenance_and_match_results(self):
        transport = Sequence(good(provider="Azure", finish="length", gen_id="gen-a"),
                             good(gen_id="gen-b"))
        vote = gate_vote(transport)
        self.assertEqual(vote.attempts, [
            {"attempt": 1, "replied": True, "served_model": "openai/gpt-6-astra",
             "served_provider": "Azure", "generation_id": "gen-a", "finish_reason": "length",
             "fault_label": "truncated", "served_model_match": True,
             "served_provider_match": False},
            {"attempt": 2, "replied": True, "served_model": "openai/gpt-6-astra",
             "served_provider": "OpenAI", "generation_id": "gen-b", "finish_reason": "stop",
             "fault_label": None, "served_model_match": True, "served_provider_match": True},
        ])
        self.assertEqual(vote.generation_id, "gen-b")


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class F9BothChecksFailTests(unittest.TestCase):
    def test_a_reply_failing_both_checks_carries_the_served_model_fault(self):
        transport = Sequence(good(model="openai/other", provider="Azure"), good())
        vote = gate_vote(transport)
        self.assertEqual(vote.fault_label, "served-model")
        self.assertEqual(len(transport.requests), 1)
        self.assertFalse(vote.retried)
        self.assertEqual((vote.attempts[0]["served_model_match"],
                          vote.attempts[0]["served_provider_match"]), (False, False))

    def test_control_each_check_alone_names_its_own_fault(self):
        self.assertEqual(gate_vote(Sequence(good(model="openai/other"))).fault_label,
                         "served-model")
        self.assertEqual(gate_vote(Sequence(good(provider="Azure"))).fault_label,
                         "served-provider-mismatch")


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class GateMalformedResponseTests(unittest.TestCase):
    """A 200 body that does not decode keeps `malformed-response` and its retry."""

    class Raw:
        def __init__(self, *bodies):
            self.bodies = list(bodies)
            self.requests = 0
            self.transport = httpx.MockTransport(self._handle)

        def _handle(self, request):
            self.requests += 1
            item = self.bodies[min(self.requests, len(self.bodies)) - 1]
            if isinstance(item, bytes):
                return httpx.Response(200, content=item, headers={"content-type": "text/html"})
            return httpx.Response(200, json=item)

    def test_an_undecodable_body_is_retried_once_and_the_retry_recovers(self):
        raw = self.Raw(b"<html>bad gateway</html>", good())
        vote = gate_vote(raw)
        self.assertEqual(raw.requests, 2)
        self.assertFalse(vote.errored)
        self.assertTrue(vote.recovered)
        self.assertEqual(vote.first_fault_label, "malformed-response")

    def test_an_undecodable_body_twice_errors_malformed_response(self):
        raw = self.Raw(b"<html>bad gateway</html>")
        vote = gate_vote(raw)
        self.assertEqual(raw.requests, 2)
        self.assertEqual(vote.fault_label, "malformed-response")

    def test_a_decoded_reply_with_no_model_is_an_absent_served_model_with_no_retry(self):
        raw = self.Raw(good(model=OMIT), good())
        vote = gate_vote(raw)
        self.assertEqual(raw.requests, 1)
        self.assertEqual(vote.fault_label, "served-model")


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class NoVoteFromAMismatchTests(unittest.TestCase):
    """The invariant over every combination: a vote only from a reply matching both checks."""

    MODELS = {"ok": "openai/gpt-6-astra", "wrong": "openai/other", "absent": OMIT}
    PROVIDERS = {"ok": "OpenAI", "wrong": "Azure", "absent": OMIT}

    def test_a_vote_is_taken_only_when_both_checks_pass(self):
        votes = 0
        for (m, model), (p, provider), finish in itertools.product(
                self.MODELS.items(), self.PROVIDERS.items(), ("stop", "length")):
            with self.subTest(model=m, provider=p, finish=finish):
                transport = Sequence(body(model=model, provider=provider, finish=finish))
                vote = gate_vote(transport)
                if (m, p, finish) == ("ok", "ok", "stop"):
                    self.assertFalse(vote.errored)  # the positive control
                    votes += 1
                else:
                    self.assertTrue(vote.errored)
                    self.assertEqual(vote.decision, "DEFER_TO_HUMAN")
                    if finish == "length":
                        self.assertEqual(vote.fault_label, "truncated")
                        self.assertEqual(len(transport.requests), 2)
                    else:
                        self.assertEqual(vote.fault_label,
                                         "served-model" if m != "ok" else "served-provider-mismatch")
                        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(votes, 1)

    def test_outside_gate_mode_the_provider_is_recorded_and_never_enforced(self):
        transport = Sequence(good(provider="Azure"))
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
            council = AsyncCouncil(AsyncCouncilConfig(transport=transport.transport,
                                                      timeout_seconds=5.0))
            vote = asyncio.run(council._call_seat_async("openai", "q"))
        self.assertFalse(vote.errored)
        self.assertEqual(vote.served_provider, "Azure")
        self.assertIsNone(vote.attempts[0]["served_provider_match"])


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class F10RefusedBeforeSpendTests(unittest.TestCase):
    """An entry with a served-model set but no served-provider set, or an empty one,
    refuses the council before any gateway call."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "registry.json"
        self.original = llm_caller.CONFIG_PATH
        self.addCleanup(self._restore)
        env = mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY})
        env.start()
        self.addCleanup(env.stop)

    def _restore(self):
        llm_caller.CONFIG_PATH = self.original
        llm_caller.invalidate_model_cache()

    def write(self, mutate=None):
        config = json.loads(REAL_REGISTRY.read_text())
        if mutate:
            mutate(config["models"]["claude-opus-5.5-xhigh"])
        self.path.write_text(json.dumps(config))
        llm_caller.CONFIG_PATH = self.path
        llm_caller.invalidate_model_cache()

    def counting(self):
        counter = {"n": 0}

        def handler(request):
            counter["n"] += 1
            model = json.loads(request.content)["model"]
            seat = next(s for s, (m, _) in SEATS.items() if m == model)
            return httpx.Response(200, json=good(seat))
        return counter, httpx.MockTransport(handler)

    def deliberate(self):
        counter, transport = self.counting()
        council = AsyncCouncil(AsyncCouncilConfig(gate=True, council_kind="adr",
                                                  transport=transport, timeout_seconds=5.0))
        result = asyncio.run(council.deliberate("q"))
        return counter["n"], result

    def test_control_the_shipped_entry_runs_three_seats(self):
        self.write()
        n, result = self.deliberate()
        self.assertEqual(n, 3)
        self.assertEqual(result.final_recommendation["errored_seats"], "0/3")

    def test_no_provider_set_or_an_empty_one_refuses_before_any_call(self):
        cases = {
            "absent": lambda entry: entry.pop("accepted_served_providers"),
            "empty": lambda entry: entry.__setitem__("accepted_served_providers", []),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                self.write(mutate)
                self.assertEqual(llm_caller.get_model_config("claude-opus-5.5-xhigh")
                                 .accepted_served_models, ("anthropic/claude-opus-5.5",))
                counter, transport = self.counting()
                with self.assertRaises(CouncilAssignmentRefused) as caught:
                    AsyncCouncilConfig(gate=True, council_kind="adr", transport=transport)
                self.assertEqual(caught.exception.code, "refused-assignment")
                self.assertEqual(caught.exception.names, ["anthropic_top", "claude-opus-5.5-xhigh"])
                self.assertEqual(counter["n"], 0)

    def test_a_set_removed_after_construction_is_refused_at_deliberate(self):
        self.write()
        counter, transport = self.counting()
        council = AsyncCouncil(AsyncCouncilConfig(gate=True, council_kind="adr",
                                                  transport=transport, timeout_seconds=5.0))
        self.write(lambda entry: entry.__setitem__("accepted_served_providers", []))
        with self.assertRaises(CouncilAssignmentRefused):
            asyncio.run(council.deliberate("q"))
        self.assertEqual(counter["n"], 0)


if __name__ == "__main__":
    unittest.main()
