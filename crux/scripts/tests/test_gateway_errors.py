"""The gateway's failure taxonomy, end to end (ADR-0087).

Two properties, each with its own operator consequence:

1. **A gateway HTTP status becomes a typed exception and then a distinct error
   label on the seat's vote.** 402 (insufficient credit) is NOT retryable —
   retrying spends nothing and fixes nothing — while 5xx / 529 and 524 are. If
   those three collapsed into one label, an operator would read a spend failure
   as something to wait out. The chain asserted here is the real one:
   ``raise_for_gateway_status`` -> the typed class -> ``_redact_error`` -> the
   persisted vote.

2. **A missing key means zero seats, not a crash.** One key now backs every
   seat, so its absence degrades all three at once. That must land on the
   fail-closed floor — ``NO_QUORUM`` and ``DEFER_TO_HUMAN`` — which is exactly
   the outcome a shared-key auth failure produces in production. The council
   reads the key with ``get``, never ``require``, for this reason.

No network anywhere: statuses come from a fake response object, and the
below-quorum case points ``CRUX_HOME`` at an empty directory.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

try:
    import httpx  # noqa: F401
    HAVE_HTTPX = True
except ImportError:
    HAVE_HTTPX = False


class _FakeResponse:
    """Minimal stand-in for httpx.Response as raise_for_gateway_status reads it."""

    def __init__(self, status_code):
        self.status_code = status_code

    def raise_for_status(self):
        # httpx raises on EVERY non-2xx, not only on >=400. The old `>= 400`
        # threshold made this fake disagree with the thing it stands in for, and
        # the disagreement was load-bearing: a 302 returned None here, so a test
        # asserting "302 is left alone" passed while the real client would have
        # raised httpx.HTTPStatusError and reported `unknown`. Mirror httpx, so
        # the fake cannot certify behaviour httpx does not have.
        if not 200 <= self.status_code <= 299:
            raise AssertionError(
                f"HTTP {self.status_code} fell through to raise_for_status — "
                "it must be mapped onto a typed gateway exception"
            )
        return None


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class GatewayStatusMappingTests(unittest.TestCase):
    def setUp(self):
        from crux.core import llm_caller
        self.llm = llm_caller

    def test_402_is_non_retryable_insufficient_credit(self):
        with self.assertRaises(self.llm.GatewayInsufficientCreditError) as ctx:
            self.llm.raise_for_gateway_status(_FakeResponse(402))
        self.assertEqual(ctx.exception.status_code, 402)
        self.assertIs(type(ctx.exception.status_code), int)

    def test_524_is_a_timeout(self):
        with self.assertRaises(self.llm.GatewayTimeoutError) as ctx:
            self.llm.raise_for_gateway_status(_FakeResponse(524))
        self.assertEqual(ctx.exception.status_code, 524)

    def test_retryable_upstream_statuses_map_to_one_class(self):
        for status in (500, 502, 503, 529):
            with self.subTest(status=status):
                with self.assertRaises(self.llm.GatewayUpstreamError) as ctx:
                    self.llm.raise_for_gateway_status(_FakeResponse(status))
                self.assertEqual(ctx.exception.status_code, status)

    def test_credit_failure_is_not_an_upstream_failure(self):
        """The distinction is the whole point: a subclass relationship here
        would let a retry loop treat 402 as retryable."""
        self.assertFalse(issubclass(self.llm.GatewayInsufficientCreditError,
                                    self.llm.GatewayUpstreamError))
        self.assertTrue(issubclass(self.llm.GatewayInsufficientCreditError,
                                   self.llm.GatewayError))

    def test_auth_statuses_map_to_one_class(self):
        """401 and 403 both mean "fix the credential", so they share a class —
        and the class exists so the label is `auth`, not `unknown`."""
        for status in (401, 403):
            with self.subTest(status=status):
                with self.assertRaises(self.llm.GatewayAuthError) as ctx:
                    self.llm.raise_for_gateway_status(_FakeResponse(status))
                self.assertEqual(ctx.exception.status_code, status)

    def test_429_is_a_rate_limit(self):
        with self.assertRaises(self.llm.GatewayRateLimitError) as ctx:
            self.llm.raise_for_gateway_status(_FakeResponse(429))
        self.assertEqual(ctx.exception.status_code, 429)

    def test_408_is_a_timeout_not_a_client_error(self):
        """A request timeout wants a longer deadline, not a request fix — so it
        joins 524 in the timeout family rather than the 4xx catch-all."""
        with self.assertRaises(self.llm.GatewayTimeoutError) as ctx:
            self.llm.raise_for_gateway_status(_FakeResponse(408))
        self.assertEqual(ctx.exception.status_code, 408)

    def test_remaining_4xx_map_to_client_error(self):
        for status in (400, 404, 422, 409):
            with self.subTest(status=status):
                with self.assertRaises(self.llm.GatewayClientError) as ctx:
                    self.llm.raise_for_gateway_status(_FakeResponse(status))
                self.assertEqual(ctx.exception.status_code, status)

    def test_every_4xx_and_5xx_class_is_a_gateway_error(self):
        """One base class is what lets a caller catch the whole family — the
        live proof's exit contract depends on it."""
        for cls in (self.llm.GatewayAuthError, self.llm.GatewayRateLimitError,
                    self.llm.GatewayClientError, self.llm.GatewayTimeoutError,
                    self.llm.GatewayUpstreamError,
                    self.llm.GatewayInsufficientCreditError):
            with self.subTest(cls=cls.__name__):
                self.assertTrue(issubclass(cls, self.llm.GatewayError))

    def test_auth_and_rate_limit_are_not_client_config_failures(self):
        """A bad key and a throttle are not "your request is malformed" — a
        subclass relationship would let one label swallow the others."""
        self.assertFalse(issubclass(self.llm.GatewayAuthError,
                                    self.llm.GatewayClientError))
        self.assertFalse(issubclass(self.llm.GatewayRateLimitError,
                                    self.llm.GatewayClientError))

    def test_success_is_left_alone(self):
        for status in (200, 201, 204, 299):
            with self.subTest(status=status):
                self.assertIsNone(self.llm.raise_for_gateway_status(_FakeResponse(status)))

    def test_a_redirect_is_typed_not_left_alone(self):
        """302 is not success, and the client does not follow it.

        This assertion used to read `assertIsNone(...302...)`, which was the
        FAKE's behaviour being pinned rather than the client's: httpx raises on
        a 302, so the real path reported `unknown` while the test called it
        'left alone'. A 3xx is now typed like every other non-2xx.
        """
        for status in (301, 302, 307, 308):
            with self.subTest(status=status):
                with self.assertRaises(self.llm.GatewayUnexpectedStatusError) as ctx:
                    self.llm.raise_for_gateway_status(_FakeResponse(status))
                self.assertEqual(ctx.exception.status_code, status)

    def test_an_informational_status_is_typed(self):
        for status in (100, 101):
            with self.subTest(status=status):
                with self.assertRaises(self.llm.GatewayUnexpectedStatusError) as ctx:
                    self.llm.raise_for_gateway_status(_FakeResponse(status))
                self.assertEqual(ctx.exception.status_code, status)

    def test_an_unexpected_status_is_a_gateway_error(self):
        """Membership of the GatewayError hierarchy, and what it buys.

        The council's label table keys on the class NAMES it meets walking
        `type(error).__mro__`, not on GatewayError itself. Membership earns
        two things instead. It lets a caller catch the whole family in one
        `except GatewayError`, which is how every consumer of this module
        handles gateway faults. And it puts `GatewayError` in this class's
        MRO, which is what makes the table's backstop row apply here — so a
        3xx or 1xx is labelled even before anyone adds a row naming it.
        """
        self.assertTrue(issubclass(self.llm.GatewayUnexpectedStatusError,
                                   self.llm.GatewayError))

    def test_no_status_falls_through_to_raise_for_status(self):
        """`raise_for_status` is the last resort, and no non-2xx status may
        reach it: httpx.HTTPStatusError is in no label table, so anything that
        gets there is reported to the operator as `unknown`.

        The sweep is the whole 100-599 space, not a hand-picked list. The two
        ranges the hand-picked list omitted — 1xx and 3xx — were exactly the two
        that fell through.
        """
        for status in range(100, 600):
            if 200 <= status <= 299:
                continue
            with self.subTest(status=status):
                # _FakeResponse.raise_for_status raises AssertionError on non-2xx,
                # so a fall-through surfaces as AssertionError, not GatewayError.
                with self.assertRaises(self.llm.GatewayError):
                    self.llm.raise_for_gateway_status(_FakeResponse(status))


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class DataCollectionDenyTests(unittest.TestCase):
    """`provider.data_collection` is a property of every request.

    It used to ride inside the `provider` object, and that object was emitted
    only when the entry pinned a serving-provider set. So the retention control
    the decision requires held for the three council seats and for nothing else:
    every unpinned model — the router's own default path included — sent no
    `provider` object at all and routed with data collection allowed. The pin and
    the denial are separate concerns and are now emitted separately.
    """

    def setUp(self):
        from crux.core import llm_caller
        self.llm = llm_caller

    def _cfg(self, **over):
        base = dict(
            api_string="vendor/model", display_name="m", context_window=1000,
            max_output_tokens=100, base_url="https://gw.example/api/v1",
            auth_header="Authorization", auth_prefix="Bearer ",
        )
        base.update(over)
        return self.llm.ModelConfig(**base)

    def _payload(self, cfg):
        with mock.patch.object(self.llm, "require", return_value=("k",)):
            _url, _headers, payload = self.llm.build_gateway_request(cfg, "hi")
        return payload

    def test_default_is_deny(self):
        self.assertEqual(self.llm.ModelConfig.__dataclass_fields__["data_collection"].default,
                         "deny")

    def test_an_unpinned_model_still_denies_data_collection(self):
        payload = self._payload(self._cfg())
        self.assertEqual(payload["provider"]["data_collection"], "deny")

    def test_an_unpinned_model_emits_no_only_key(self):
        """`only` is the host pin and must not appear without one: an empty
        allowlist would pin the request to zero serving providers."""
        payload = self._payload(self._cfg())
        self.assertNotIn("only", payload["provider"])

    def test_a_pinned_model_carries_both(self):
        payload = self._payload(self._cfg(serving_providers=("anthropic",)))
        self.assertEqual(payload["provider"],
                         {"data_collection": "deny", "only": ["anthropic"]})

    def test_the_registry_can_override_for_an_unroutable_model(self):
        """The escape hatch: a model served only by endpoints that decline
        zero-retention is unroutable under deny, and the override says so on
        that one entry rather than by dropping the control everywhere."""
        payload = self._payload(self._cfg(data_collection="allow"))
        self.assertEqual(payload["provider"]["data_collection"], "allow")

    def _config_for(self, **entry_over):
        entry = {
            "api_string": "vendor/model", "display_name": "m", "provider": "openrouter",
        }
        entry.update(entry_over)
        providers = {"openrouter": {
            "base_url": "https://gw.example/api/v1",
            "auth_header": "Authorization", "auth_prefix": "Bearer ",
        }}
        with mock.patch.object(
            self.llm, "load_router_config",
            return_value={"models": {"m": entry}, "providers": providers},
        ):
            return self.llm.get_model_config("m")

    def test_an_absent_registry_key_reads_as_deny(self):
        self.assertEqual(self._config_for().data_collection, "deny")

    def test_an_unrecognized_retention_value_is_refused(self):
        """A typo in this key must not route the request with retention allowed.

        The value is passed through to the gateway verbatim. The gateway does
        not recognize `"Deny"` or `"denied"`, so the denial is dropped and the
        request routes with data collection allowed — silently, because a
        misspelled denial looks like a denial in the registry file. The typo
        that reads most like success is the one that fails open, so this fails
        closed instead.
        """
        for bogus in ("Deny", "denied", "none", "false", "", "DENY"):
            with self.subTest(value=bogus):
                with self.assertRaises(ValueError) as ctx:
                    self._config_for(data_collection=bogus)
                message = str(ctx.exception)
                self.assertIn("data_collection", message)
                self.assertIn("retention", message)

    def test_the_allow_escape_hatch_survives_the_validation(self):
        """Validation must refuse typos without closing the documented hatch.

        A model served only by endpoints that decline zero-retention is
        unroutable under deny, and `"allow"` on that one entry is the
        reviewable way to say so.
        """
        self.assertEqual(self._config_for(data_collection="allow").data_collection,
                         "allow")


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class GatewayErrorLabelTests(unittest.TestCase):
    """The label an operator actually reads, on the vote that is persisted."""

    def setUp(self):
        from crux.council.async_council import AsyncCouncil
        from crux.core import llm_caller
        self.AsyncCouncil = AsyncCouncil
        self.llm = llm_caller

    def _label_for(self, status):
        try:
            self.llm.raise_for_gateway_status(_FakeResponse(status))
        except self.llm.GatewayError as err:
            return self.AsyncCouncil._redact_error(err)
        self.fail(f"HTTP {status} did not raise a GatewayError")

    #: Every label an operator can read off a gateway status, and the status
    #: that produces it. Seven distinct operator actions: add credit, wait,
    #: lengthen the deadline, retry later, fix the credential, fix the request,
    #: report the status.
    STATUS_LABELS = (
        (402, "insufficient-credit (HTTP 402)"),
        (529, "provider (HTTP 529)"),
        (524, "timeout (HTTP 524)"),
        (408, "timeout (HTTP 408)"),
        (401, "auth (HTTP 401)"),
        (403, "auth (HTTP 403)"),
        (429, "rate-limit (HTTP 429)"),
        (400, "client-config (HTTP 400)"),
        (302, "unexpected-status (HTTP 302)"),
        (100, "unexpected-status (HTTP 100)"),
    )

    def test_status_to_label(self):
        for status, expected in self.STATUS_LABELS:
            with self.subTest(status=status):
                self.assertEqual(self._label_for(status), expected)

    def test_no_gateway_status_reads_as_unknown(self):
        """The regression this guards: mapping only 402/524/5xx and delegating
        the rest to `response.raise_for_status()` left 401/403/429 and the
        remaining 4xx arriving as httpx.HTTPStatusError — a class in no label
        table — so every one of them reported `unknown`."""
        for status, expected in self.STATUS_LABELS:
            with self.subTest(status=status):
                self.assertNotIn("unknown", self._label_for(status))
                self.assertTrue(expected.split(" ")[0])

    def test_labels_reach_the_persisted_vote(self):
        council = self.AsyncCouncil.__new__(self.AsyncCouncil)  # no keys needed
        for status, expected in self.STATUS_LABELS:
            with self.subTest(status=status):
                try:
                    self.llm.raise_for_gateway_status(_FakeResponse(status))
                except self.llm.GatewayError as err:
                    vote = self.AsyncCouncil._create_error_vote(council, "openai", err)
                self.assertIn(expected, vote.reasoning)
                self.assertTrue(vote.errored)

    def test_new_classes_do_not_leak_their_message(self):
        """The new classes join a closed vocabulary, so a credential echoed in
        an upstream message must not survive into the label. Gateways do echo
        them: a 401 body routinely quotes the key it rejected."""
        token = "sk-or-v1-FAKE0000111122223333444455556666"
        cases = ((self.llm.GatewayAuthError, 401, "auth (HTTP 401)"),
                 (self.llm.GatewayRateLimitError, 429, "rate-limit (HTTP 429)"),
                 (self.llm.GatewayClientError, 400, "client-config (HTTP 400)"))
        for cls, status, expected in cases:
            with self.subTest(cls=cls.__name__):
                err = cls(f"gateway rejected key {token} at "
                          f"https://openrouter.ai/api/v1?key={token}", status)
                label = self.AsyncCouncil._redact_error(err)
                self.assertEqual(label, expected)
                self.assertNotIn(token, label)
                self.assertNotIn("sk-or", label)
                self.assertNotIn("openrouter.ai", label)

    #: The labels this taxonomy can produce from an HTTP status. Named here so
    #: the sweep below asserts membership of a closed set rather than merely
    #: the absence of one bad string: a typo'd label would clear an
    #: `assertNotEqual("unknown")` and still reach the operator as noise.
    STATUS_LABEL_VOCABULARY = frozenset({
        "insufficient-credit", "timeout", "auth", "rate-limit",
        "provider", "client-config", "unexpected-status",
    })

    #: The whole label set `_ERROR_LABELS` maps onto, pinned as a SET rather
    #: than a count: a count survives one label swapped for another. "unknown"
    #: is the fallback for an unmatched class and has no row of its own.
    ERROR_LABEL_VOCABULARY = frozenset({
        "refused", "insufficient-credit", "timeout", "auth", "rate-limit",
        "unreachable", "provider", "client-config", "malformed-response",
        "unexpected-status", "truncated",
    })

    def test_error_label_set_is_pinned(self):
        self.assertEqual(set(self.AsyncCouncil._ERROR_LABELS.values()),
                         self.ERROR_LABEL_VOCABULARY)
        self.assertIn("truncated", self.ERROR_LABEL_VOCABULARY)
        self.assertEqual(self.AsyncCouncil._fault_label(KeyError("odd")), "unknown")

    def test_no_status_reads_as_unknown_at_the_consumer(self):
        """The same 100-599 sweep, one layer further down.

        `test_no_status_falls_through_to_raise_for_status` sweeps this range
        through `raise_for_gateway_status` and proves a `GatewayError` is
        raised. That is the PRODUCER, and it is not the thing an operator
        reads. `_redact_error` labels by walking `type(error).__mro__` against
        a fixed table, so a correctly-typed exception whose class name has no
        row still reports `unknown` — the typing looks done and the operator
        learns nothing.

        Typing and labelling are therefore two claims and need two proofs.
        This is the second one: every non-2xx status, end to end, reaches the
        operator as a vocabulary label carrying its status number.
        """
        for status in range(100, 600):
            if 200 <= status <= 299:
                continue
            with self.subTest(status=status):
                out = self._label_for(status)
                label, _, suffix = out.partition(" (HTTP ")
                self.assertNotEqual(label, "unknown",
                                    f"HTTP {status} reaches the operator as 'unknown'")
                self.assertIn(label, self.STATUS_LABEL_VOCABULARY)
                self.assertEqual(suffix, f"{status})",
                                 f"HTTP {status} lost its status number")


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class MissingKeyMidRunTests(unittest.TestCase):
    """A key that disappears AFTER the seats are built is an auth failure.

    The below-quorum path covers a key missing at construction — zero seats,
    no calls. This is the other half: the key was present when the council
    counted its seats and is gone when they call. `require` then raises
    `EnvNotConfigured` inside each seat, and unless that class name is in the
    label table every seat reports `unknown` — the least actionable label there
    is, for the single most actionable failure.

    No network: `build_gateway_request` resolves the key before it builds a
    request, so the failure lands before any socket is opened.
    """

    def _deliberate_with_key_removed_mid_run(self):
        import crux_env
        from crux.council.async_council import AsyncCouncil

        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ)
            env["CRUX_HOME"] = home  # empty: no ~/.crux/env to fall back to
            env["OPENROUTER_API_KEY"] = "sk-or-v1-PRESENT-AT-INIT"
            with mock.patch.dict(os.environ, env, clear=True):
                crux_env._reset_cache()
                try:
                    council = AsyncCouncil()
                    self.assertEqual(len(council.available_providers), 3,
                                     "a present key must seat all three")
                    # The key vanishes between seating and calling.
                    del os.environ["OPENROUTER_API_KEY"]
                    crux_env._reset_cache()
                    return council.deliberate_sync("Should we ship?")
                finally:
                    crux_env._reset_cache()

    def test_every_seat_reports_auth_and_the_council_defers(self):
        result = self._deliberate_with_key_removed_mid_run()
        self.assertEqual(len(result.votes), 3)
        for vote in result.votes:
            with self.subTest(provider=vote.provider):
                self.assertTrue(vote.errored)
                self.assertIn("auth", vote.reasoning)
                self.assertNotIn("unknown", vote.reasoning)
        self.assertEqual(result.consensus, "NO_QUORUM")
        self.assertEqual(result.final_recommendation["action"], "DEFER_TO_HUMAN")
        self.assertEqual(result.final_recommendation["errored_seats"], "3/3")

    def test_the_remediation_text_never_reaches_the_vote(self):
        """`EnvNotConfigured` carries a remediation message naming the key and
        the command to set it. Useful in a terminal; not something to persist
        into a deliberation surface."""
        result = self._deliberate_with_key_removed_mid_run()
        for vote in result.votes:
            self.assertNotIn("crux-env", vote.reasoning)
            self.assertNotIn("OPENROUTER_API_KEY", vote.reasoning)


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class ErrorVoteSeatIdentityTests(unittest.TestCase):
    """An errored seat carries the SAME provider key a responding seat does.

    `provider` is what aggregation counts and what a consumer joins on, so
    "OpenAI" on the error path and "openai" on the success path is two identities
    for one seat — a consumer grouping votes by provider sees four seats where
    there are three.
    """

    def _council(self, timeout=30.0):
        from crux.council.async_council import AsyncCouncil, AsyncCouncilConfig
        council = AsyncCouncil.__new__(AsyncCouncil)
        council.config = AsyncCouncilConfig(timeout_seconds=timeout)
        council._seat_models = {
            "openai": council.config.openai_model,
            "anthropic": council.config.anthropic_model,
            "gemini": council.config.gemini_model,
        }
        council.available_providers = list(AsyncCouncil._SEAT_ORDER)
        council._retry = lambda fn: fn
        return council

    def test_timed_out_seats_keep_the_lowercase_seat_key(self):
        # The deadline lives in the seat wrapper, so the hang is at the gateway
        # post, under it. A stub over the whole seat would carry no deadline.
        async def _hang(cfg, prompt, system=None, response_format=None, attempt=None):
            await asyncio.sleep(60)

        council = self._council(timeout=0.05)
        council._post_seat = _hang
        result = council.deliberate_sync("Should we ship?")

        self.assertEqual([v.provider for v in result.votes],
                         ["openai", "anthropic", "gemini"])
        for vote in result.votes:
            self.assertEqual(vote.model, f"{vote.provider}/ERROR")
            self.assertIn("timeout", vote.reasoning)

    def test_gathered_exceptions_keep_the_lowercase_seat_key(self):
        async def _raise(seat, prompt, system=None):
            raise RuntimeError("seat exploded before its own handler ran")

        council = self._council()
        council._call_seat_async = _raise
        result = council.deliberate_sync("Should we ship?")

        self.assertEqual([v.provider for v in result.votes],
                         ["openai", "anthropic", "gemini"])
        for vote in result.votes:
            self.assertEqual(vote.model, f"{vote.provider}/ERROR")

    def test_a_partial_seat_set_stays_aligned(self):
        """The seat list is filtered by availability, so any parallel list must
        be filtered by the same pass — an index built over the unfiltered order
        misattributes the failure to the wrong vendor."""
        async def _hang(cfg, prompt, system=None, response_format=None, attempt=None):
            await asyncio.sleep(60)

        council = self._council(timeout=0.05)
        council.available_providers = ["anthropic", "gemini"]
        council._post_seat = _hang
        result = council.deliberate_sync("Should we ship?")

        self.assertEqual([v.provider for v in result.votes],
                         ["anthropic", "gemini"])


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class PerSeatDeadlineTests(unittest.TestCase):
    """One slow seat costs only that seat; the seats that answered still count.

    Positive control: ErrorVoteSeatIdentityTests above (every seat hangs, every
    seat becomes a timeout-labelled errored vote) stays green under the same fix.
    """

    # Fast seats return at once and slow seats sleep 60 s, so 1 s separates them
    # with room for a loaded machine running the suite across several workers.
    def _council(self, cls_name="AsyncCouncil", timeout=1.0, seats=None):
        from crux.council import async_council
        cls = getattr(async_council, cls_name)
        council = cls.__new__(cls)
        # max_retries=0: these tests are about the per-seat deadline, and one
        # attempt per seat keeps the wait at one deadline. The retry has its own
        # tests in test_council_retry.py.
        council.config = async_council.AsyncCouncilConfig(timeout_seconds=timeout, max_retries=0)
        council._seat_models = {
            "openai": council.config.openai_model,
            "anthropic": council.config.anthropic_model,
            "gemini": council.config.gemini_model,
        }
        council.available_providers = list(seats or cls._SEAT_ORDER)
        council._retry = lambda fn: fn
        return council

    def test_default_timeout_is_600_seconds(self):
        from crux.council.async_council import AsyncCouncilConfig
        self.assertEqual(AsyncCouncilConfig().timeout_seconds, 600.0)

    _VOTE = ('{"decision": "APPROVE", "confidence": 0.8, "dissents": [], '
             '"reasoning": ""}')

    def test_one_slow_seat_does_not_erase_the_responding_seats(self):
        # The deadline lives in the seat wrapper, so the seats are slow or fast at
        # the gateway post, under it. The slow seat is told apart by its model.
        council = self._council()
        slow_model = council._seat_models["anthropic"]

        async def _post(cfg, prompt, system=None, response_format=None, attempt=None):
            if cfg.name == slow_model:
                await asyncio.sleep(60)
            return self._VOTE

        council._post_seat = _post
        result = council.deliberate_sync("Should we ship?")

        self.assertEqual([v.provider for v in result.votes], ["openai", "anthropic", "gemini"])
        errored = [v.provider for v in result.votes if v.errored]
        self.assertEqual(errored, ["anthropic"])
        slow = next(v for v in result.votes if v.provider == "anthropic")
        self.assertIn("timeout", slow.reasoning)
        self.assertEqual(result.consensus, "UNANIMOUS_APPROVE")
        self.assertEqual(result.errored_seats, "1/3")
        self.assertTrue(result.final_recommendation["degraded"])
        self.assertEqual(result.final_recommendation["action"], "EXECUTE_WITH_MONITORING")

    def test_all_seats_hang_is_no_quorum_within_the_deadline(self):
        async def _hang(cfg, prompt, system=None, response_format=None, attempt=None):
            await asyncio.sleep(60)

        council = self._council()
        council._post_seat = _hang
        result = council.deliberate_sync("Should we ship?")
        self.assertEqual(result.consensus, "NO_QUORUM")
        self.assertEqual(result.errored_seats, "3/3")
        self.assertEqual(result.final_recommendation["off_scale"], [])

    def test_no_provider_path_carries_off_scale(self):
        council = self._council(seats=[])
        result = council.deliberate_sync("Should we ship?")
        self.assertEqual(result.consensus, "NO_QUORUM")
        self.assertEqual(result.final_recommendation["off_scale"], [])

    def test_visual_council_keeps_the_fast_seat_when_one_is_slow(self):
        council = self._council(cls_name="AsyncVisualCouncil", seats=["openai", "anthropic"])
        slow_model = council._seat_models["openai"]

        async def _post(cfg, prompt, system=None, response_format=None, attempt=None):
            if cfg.name == slow_model:
                await asyncio.sleep(60)
            return ('{"passed": true, "confidence": 0.9, "observations": "clean", '
                    '"anomalies": []}')

        council._post_seat = _post
        results = council.analyze_sync("aGVsbG8=", "Inspect.")

        by_name = {r.model_name: r for r in results}
        self.assertEqual(set(by_name), {"OpenAI-vision", "Claude-vision"})
        self.assertFalse(by_name["OpenAI-vision"].passed)
        self.assertIn("timeout", by_name["OpenAI-vision"].observations)
        self.assertTrue(by_name["OpenAI-vision"].errored)
        self.assertTrue(by_name["Claude-vision"].passed)
        self.assertFalse(by_name["Claude-vision"].errored)
        self.assertEqual(by_name["Claude-vision"].observations, "clean")

    def test_http_client_timeout_is_not_below_the_seat_deadline(self):
        seen = {}

        class _Client:
            def __init__(self, **kwargs):
                seen.update(kwargs)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, *a, **kw):
                raise httpx.ReadTimeout("slow")

        from crux.council import async_council
        council = self._council(timeout=600.0)
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test-dummy"}), \
                mock.patch.object(async_council.httpx, "AsyncClient", _Client):
            vote = asyncio.run(council._call_seat_async("openai", "Question?"))
        self.assertGreaterEqual(seen["timeout"], council.config.timeout_seconds)
        self.assertGreaterEqual(seen["timeout"], 600.0)
        self.assertTrue(vote.errored)

    def test_transport_timeouts_carry_the_timeout_label(self):
        from crux.council import async_council
        errors = [httpx.ReadTimeout("x"), httpx.ConnectTimeout("x"),
                  asyncio.TimeoutError(), TimeoutError("x")]
        for exc in errors:
            with self.subTest(exc=type(exc).__name__):
                class _Client:
                    def __init__(self, **kwargs):
                        pass

                    async def __aenter__(self):
                        return self

                    async def __aexit__(self, *e):
                        return False

                    async def post(self, *a, _exc=exc, **kw):
                        raise _exc

                council = self._council(timeout=30.0)
                with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test-dummy"}), \
                        mock.patch.object(async_council.httpx, "AsyncClient", _Client):
                    vote = asyncio.run(council._call_seat_async("openai", "Question?"))
                self.assertTrue(vote.errored)
                self.assertIn("timeout", vote.reasoning)


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class BelowQuorumWithoutKeyTests(unittest.TestCase):
    """One key backs every seat, so its absence must be the fail-closed floor."""

    def _deliberate_without_key(self):
        import crux_env
        from crux.council.async_council import AsyncCouncil

        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ)
            env.pop("OPENROUTER_API_KEY", None)
            env["CRUX_HOME"] = home  # empty: no ~/.crux/env to fall back to
            with mock.patch.dict(os.environ, env, clear=True):
                crux_env._reset_cache()
                try:
                    council = AsyncCouncil()
                    self.assertEqual(council.available_providers, [],
                                     "no key must mean no seats")
                    return council.deliberate_sync("Should we ship?")
                finally:
                    crux_env._reset_cache()

    def test_missing_key_defers_to_a_human_without_raising(self):
        result = self._deliberate_without_key()
        self.assertEqual(result.consensus, "NO_QUORUM")
        self.assertEqual(result.final_recommendation["action"], "DEFER_TO_HUMAN")
        self.assertEqual(result.votes, [])


if __name__ == "__main__":
    unittest.main()
