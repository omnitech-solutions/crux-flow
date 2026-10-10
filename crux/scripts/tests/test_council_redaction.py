"""Adversarial tests for AsyncCouncil._redact_error (PB-0052, ADR-0054 follow-on).

PLACEMENT IS DELIBERATE. These live OUTSIDE `test_council_aggregation.py`'s
`AggregationTests`, which `bionic/invariants/council_aggregation.md` names as
INV-0001's executable check. A redaction failure landing in that class would set
`reconciliation.yml` `last_result: fail` -> CHK-INV-FAILING -> BROKEN against a
ratified pin, falsely reporting that *aggregation excludes errored seats* is
violated. This file has NO `reconciliation.yml` entry (adding one would be
creating a pin).

WHY THE OLD TEST DID NOT CATCH THE BUG. The previous
`test_error_vote_redacts_urls_and_tokens` drew its inputs from the
implementation's own two regexes, so it certified the guarantee it was meant to
test: a straw-man redactor hardcoding those two literal tokens passed all its
assertions while leaking every real credential shape. The corpus below is
adversarial by construction — it is built from real credential shapes, not from
the implementation.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crux.council.async_council import AsyncCouncil

# Synthetic credential-shaped strings. NONE is a live secret: these are canonical
# vendor-documentation placeholders and invented values.
SECRET_CORPUS = [
    "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "AKIAIOSFODNN7EXAMPLE",
    "postgresql://admin:hunter2@db.internal:5432/prod",
    "redis://:s3cr3tp4ss@cache.internal:6379/0",
    "mongodb+srv://user:p%40ssw0rd@cluster0.mongodb.net/test",
    "apikey=hunter2",
    "password: correct-horse-battery-staple",
    "Authorization: Basic dXNlcjpwYXNzd29yZA==",
    "sk-abc123XYZ",
    "sk-ant-api03-AbCdEf_GhIjKl-MnOpQr",
    "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
    "/Users/alice/.crux/env",
    "~/.crux/secrets/service-account.json",
    "api.openai.com:443/v1/chat/completions",
    "svc-account@my-project.iam.gserviceaccount.com",
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig",
    "ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxx\nOPENAI_API_KEY=sk-proj-yyyyyyyy",
    "x" * 500 + " trailing-secret-tail-value",
]

# Tokens that must never appear in any output, in any case.
FORBIDDEN_FRAGMENTS = [
    "wJalrXUtnFEMI", "AKIAIOSFODNN7EXAMPLE", "hunter2", "s3cr3tp4ss", "p%40ssw0rd",
    "correct-horse", "dXNlcjpwYXNzd29yZA", "sk-abc123XYZ", "sk-ant-api03",
    "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345", "/Users/alice", ".crux/env",
    "service-account.json", "api.openai.com", "gserviceaccount.com", "eyJhbGci",
    "sk-proj-yyyyyyyy", "trailing-secret-tail-value",
]

ALLOWED_LABELS = {
    "timeout", "auth", "rate-limit", "unreachable", "provider",
    "client-config", "malformed-response", "unknown",
    # ADR-0087: HTTP 402 earns its own label because its operator action differs
    # from every other failure — add credits; retrying fixes nothing.
    "insufficient-credit",
    # ADR-0087: a non-2xx outside 400-599 (a 3xx or a 1xx); the action is to
    # report the status, not to change the payload or the credential.
    "unexpected-status",
    # A safety decline: the model answered and refused, so nothing failed.
    "refused",
    # A reply that stopped on its output budget (finish reason "length").
    "truncated",
    # A gate council's reply named a served model the registry entry does not accept.
    "served-model",
    # A gate council's reply named a served provider the registry entry does not accept.
    "served-provider-mismatch",
}


def _label_of(out: str) -> str:
    return out.split(" (HTTP ")[0]


class RedactionNonLeakageTests(unittest.TestCase):
    """The ABSOLUTE property: no substring of the input reaches the output."""

    def test_exception_payloads_never_leak(self):
        for payload in SECRET_CORPUS:
            with self.subTest(payload=payload[:40]):
                out = AsyncCouncil._redact_error(RuntimeError(payload))
                for frag in FORBIDDEN_FRAGMENTS:
                    self.assertNotIn(frag, out, f"leaked {frag!r} via exception payload")

    def test_str_branch_never_leaks(self):
        """C6: the `BaseException | str` signature is the one place the absolute
        guarantee could be silently violated during implementation (e.g. a
        `label = arg if isinstance(arg, str)` fallback). Assert ZERO substring
        overlap, not merely that the exception path is clean."""
        for payload in SECRET_CORPUS:
            with self.subTest(payload=payload[:40]):
                out = AsyncCouncil._redact_error(payload)
                self.assertEqual(out, "unknown", "an arbitrary str must degrade to 'unknown'")
                for frag in FORBIDDEN_FRAGMENTS:
                    self.assertNotIn(frag, out)

    def test_output_is_always_from_the_closed_vocabulary(self):
        for payload in SECRET_CORPUS:
            out = AsyncCouncil._redact_error(RuntimeError(payload))
            self.assertIn(_label_of(out), ALLOWED_LABELS)

    def test_error_vote_surfaces_do_not_leak(self):
        """The vote's reasoning AND dissenting_points are both persisted."""
        council = AsyncCouncil.__new__(AsyncCouncil)  # no API keys needed
        for payload in SECRET_CORPUS:
            vote = AsyncCouncil._create_error_vote(council, "openai", RuntimeError(payload))
            blob = vote.reasoning + " " + " ".join(vote.dissenting_points)
            for frag in FORBIDDEN_FRAGMENTS:
                self.assertNotIn(frag, blob, f"leaked {frag!r} onto a persisted vote field")


class RedactionSpoofingTests(unittest.TestCase):
    """C5: a spoofed __name__ is a FIDELITY risk, never a leakage one."""

    def test_credential_shaped_class_name_cannot_leak(self):
        for name in ("AKIAIOSFODNN7EXAMPLE", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"):
            cls = type(name, (Exception,), {})
            out = AsyncCouncil._redact_error(cls("boom"))
            self.assertEqual(out, "unknown")
            self.assertNotIn(name, out)

    def test_writable_dunder_name_cannot_leak(self):
        class Weird(Exception):
            pass

        Weird.__name__ = "sk-ant-api03-SECRETVALUE"
        out = AsyncCouncil._redact_error(Weird("boom"))
        self.assertNotIn("SECRETVALUE", out)
        self.assertIn(_label_of(out), ALLOWED_LABELS)


class RedactionStatusTests(unittest.TestCase):
    """C3/C4: the int must be an exact built-in int, and attribute reads guarded."""

    def test_hostile_int_subclass_is_rejected(self):
        class Evil(int):
            def __str__(self):  # pragma: no cover - must never be reached
                return "LEAKED-SECRET"

            __repr__ = __str__

            def __format__(self, spec):  # pragma: no cover
                return "LEAKED-SECRET"

        err = RuntimeError("boom")
        err.status_code = Evil(429)
        out = AsyncCouncil._redact_error(err)
        self.assertNotIn("LEAKED", out)
        self.assertEqual(out, "unknown", "an int SUBCLASS must not be accepted as a status")

    def test_raising_property_degrades_to_status_omitted(self):
        class Boom(Exception):
            @property
            def status_code(self):
                raise ValueError("secret-in-property-hunter2")

        out = AsyncCouncil._redact_error(Boom("x"))
        self.assertNotIn("hunter2", out)
        self.assertIn(_label_of(out), ALLOWED_LABELS)

    def test_out_of_range_status_is_dropped(self):
        for bad in (0, 99, 600, 99999, -429):
            err = RuntimeError("boom")
            err.status_code = bad
            self.assertNotIn("HTTP", AsyncCouncil._redact_error(err))

    def test_valid_status_is_kept(self):
        err = RuntimeError("boom")
        err.status_code = 429
        self.assertEqual(AsyncCouncil._redact_error(err), "unknown (HTTP 429)")

    def test_status_read_from_nested_response(self):
        class Resp:
            status_code = 503

        err = RuntimeError("boom")
        err.response = Resp()
        self.assertEqual(AsyncCouncil._redact_error(err), "unknown (HTTP 503)")


class RedactionClassificationTests(unittest.TestCase):
    """Diagnostic value: the label must still discriminate the failure modes the
    error vote exists to carry. Over-redaction is the opposite failure mode."""

    def test_timeout_classifies(self):
        self.assertEqual(AsyncCouncil._redact_error(TimeoutError("x")), "timeout")

    def test_legacy_timeout_literal_classifies(self):
        """C2: the deliberate_async timeout path passes the literal "Timeout"
        and can never supply a type, so it needs a closed exact mapping."""
        self.assertEqual(AsyncCouncil._redact_error("Timeout"), "timeout")

    def test_subclass_classifies_via_mro(self):
        class MyTimeout(TimeoutError):
            pass

        self.assertEqual(AsyncCouncil._redact_error(MyTimeout("x")), "timeout")

    def test_distinct_modes_are_distinguishable(self):
        cases = {
            "AuthenticationError": "auth",
            "RateLimitError": "rate-limit",
            "APIConnectionError": "unreachable",
            "InternalServerError": "provider",
            "BadRequestError": "client-config",
            "APIResponseValidationError": "malformed-response",
            "ModelRefusedError": "refused",
            # The gateway classes carry a status_code, so they are exercised
            # against the real classes below rather than by name here.
        }
        seen = set()
        for cls_name, expected in cases.items():
            cls = type(cls_name, (Exception,), {})
            got = AsyncCouncil._redact_error(cls("x"))
            self.assertEqual(got, expected)
            seen.add(got)
        self.assertEqual(len(seen), len(cases), "labels must not collapse together")

    def test_allowed_labels_are_exactly_the_label_table_plus_unknown(self):
        """The allowed set is pinned as a SET against the source of truth, so a
        label added to `_ERROR_LABELS` without a test update fails here."""
        self.assertEqual(ALLOWED_LABELS,
                         set(AsyncCouncil._ERROR_LABELS.values()) | {"unknown"})
        self.assertIn("truncated", ALLOWED_LABELS)

    def test_truncated_and_malformed_response_classify_by_their_own_classes(self):
        from crux.core.llm_caller import MalformedResponseError, ResponseTruncatedError

        for err, expected in ((ResponseTruncatedError("x"), "truncated"),
                              (MalformedResponseError("x"), "malformed-response")):
            with self.subTest(error=type(err).__name__):
                got = AsyncCouncil._redact_error(err)
                self.assertEqual(got, expected)
                self.assertIn(_label_of(got), ALLOWED_LABELS)

    def test_confidence_rejected_error_classifies_as_malformed_response(self):
        """A bool / non-numeric / out-of-range confidence at ingest IS a
        malformed response: the dedicated class earns the existing label via
        the MRO walk, and the label vocabulary stays closed (a new class row,
        never a new label)."""
        from crux.core.llm_caller import ConfidenceRejectedError

        got = AsyncCouncil._redact_error(ConfidenceRejectedError("x"))
        self.assertEqual(got, "malformed-response")
        self.assertIn(_label_of(got), ALLOWED_LABELS)

    def test_gateway_classes_classify_distinctly(self):
        """ADR-0087: a 402 wants credits, a 5xx wants a wait, a 524 wants a
        longer deadline. Three operator actions, so three labels — collapsing
        them would report a non-retryable spend failure as something to retry.

        Driven by the REAL classes, not name-spoofed stand-ins: these carry a
        `status_code`, and the label is only half of what the vote surfaces.
        """
        from crux.core.llm_caller import (
            GatewayInsufficientCreditError,
            GatewayTimeoutError,
            GatewayUpstreamError,
        )

        cases = [
            (GatewayInsufficientCreditError("no credit", 402), "insufficient-credit (HTTP 402)"),
            (GatewayUpstreamError("overloaded", 529), "provider (HTTP 529)"),
            (GatewayTimeoutError("timed out", 524), "timeout (HTTP 524)"),
        ]
        seen = set()
        for err, expected in cases:
            with self.subTest(error=type(err).__name__):
                got = AsyncCouncil._redact_error(err)
                self.assertEqual(got, expected)
                self.assertIn(_label_of(got), ALLOWED_LABELS)
            seen.add(got)
        self.assertEqual(len(seen), len(cases), "gateway labels must not collapse together")

    def test_unlisted_gateway_subclass_lands_on_the_backstop(self):
        """The `GatewayError`: "unexpected-status" row is the MRO backstop.

        `_redact_error` walks `__mro__` and takes the first table hit, so a
        novel GatewayError subclass with no dedicated row still resolves to
        "unexpected-status" instead of regressing to "unknown". Delete the row
        and this test fails — the gap mutation-proven before had no test.
        """
        from crux.core.llm_caller import GatewayError

        class NovelGatewayFault(GatewayError):
            pass

        err = NovelGatewayFault("a third-party fault", 503)
        self.assertEqual(AsyncCouncil._redact_error(err), "unexpected-status (HTTP 503)")
        # Out-of-range status drops to the bare label (100-599 guard).
        self.assertEqual(
            AsyncCouncil._redact_error(NovelGatewayFault("plain", 600)),
            "unexpected-status",
        )

    def test_gateway_error_message_cannot_leak_through_the_label(self):
        """The gateway's own message may quote upstream text. Only the closed
        label and the validated status may reach a persisted vote."""
        from crux.core.llm_caller import GatewayUpstreamError

        err = GatewayUpstreamError("upstream said sk-ant-LEAKTOKEN", 503)
        self.assertEqual(AsyncCouncil._redact_error(err), "provider (HTTP 503)")

        council = AsyncCouncil.__new__(AsyncCouncil)  # no ctor / no keys needed
        vote = AsyncCouncil._create_error_vote(council, "openai", err)
        blob = " ".join(str(x) for x in (vote.model, vote.reasoning, vote.dissenting_points))
        self.assertNotIn("LEAKTOKEN", blob)

    def test_refusal_message_cannot_leak_through_the_label(self):
        """A refusal message is operator-supplied text; only the closed-vocabulary
        label may surface. Pins the non-leakage guarantee for the newest label."""
        from crux.core.llm_caller import ModelRefusedError

        err = ModelRefusedError("declined; SENSITIVE-sk-ant-LEAKTOKEN")
        self.assertEqual(AsyncCouncil._redact_error(err), "refused")

        council = AsyncCouncil.__new__(AsyncCouncil)  # no ctor / no keys needed
        vote = AsyncCouncil._create_error_vote(council, "anthropic", err)
        blob = " ".join(str(x) for x in (vote.model, vote.reasoning, vote.dissenting_points))
        self.assertNotIn("LEAKTOKEN", blob)
        self.assertNotIn("SENSITIVE", blob)
        self.assertIn("refused", blob)

    def test_bare_exception_is_unknown_not_empty(self):
        """A bare TimeoutError() used to yield reasoning of 'Error calling x: '
        — zero signal. Every path must now produce a non-empty label."""
        self.assertTrue(AsyncCouncil._redact_error(Exception()))


class SyncCouncilLeakTests(unittest.TestCase):
    """council.py:245 — the highest-severity path: raw exception text flowed into
    a git-TRACKED logs/semantic_tracers/ artifact AND into a third-party arbiter
    prompt. Both PB-0052 councils ruled it in scope for this release."""

    def test_sync_council_source_does_not_interpolate_raw_exception(self):
        src = (Path(__file__).resolve().parents[1] / "crux" / "council" / "council.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(
            'position=f"Failed to get opinion: {e}"',
            src,
            "council.py must not interpolate the raw exception into Opinion.position",
        )
        self.assertIn("_redact_error(e)", src)


if __name__ == "__main__":
    unittest.main()
