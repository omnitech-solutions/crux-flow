"""Gate council seats against the REAL shipped registry.

The Google seat requests `google/gemini-3.1-pro-preview` pinned to
`google-ai-studio`; every vote records role, registry key, requested model and
what the gateway reported serving. No network and no key: an
`httpx.MockTransport` answers, and the gateway key is a dummy.
"""
from __future__ import annotations

import asyncio
import json
import re
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
        AsyncCouncil,
        AsyncCouncilConfig,
        gate_vote_instruction,
    )
    from crux.core.llm_caller import provider_namespace

DUMMY_KEY = "sk-or-test-dummy"
DISPLAY = {"openai": "OpenAI", "anthropic": "Anthropic", "google": "Google AI Studio"}


def vote_json(**over):
    data = {"decision": "APPROVE", "confidence": 0.9, "reasoning": "covered",
            "findings": []}
    data.update(over)
    return json.dumps(data)


class Gateway:
    """Answers each request with a reply that echoes the requested model."""

    def __init__(self, content=None, per_seat=None):
        self.requests = []
        self.headers = []
        self._content = content if content is not None else vote_json()
        self._per_seat = per_seat or {}
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request):
        payload = json.loads(request.content)
        self.requests.append(payload)
        self.headers.append(dict(request.headers))
        model = payload["model"]
        namespace = model.split("/")[0]
        content = self._per_seat.get(namespace, self._content)
        return httpx.Response(200, json={
            "id": f"gen-{namespace}-1", "model": model, "provider": DISPLAY[namespace],
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        })

    def request_for(self, namespace):
        return next(p for p in self.requests if p["model"].startswith(namespace + "/"))


def gate_council(gateway, kind="adr"):
    return AsyncCouncil(AsyncCouncilConfig(gate=True, council_kind=kind,
                                           transport=gateway.transport,
                                           timeout_seconds=5.0))


def seat_vote(gateway, seat="gemini", kind="adr", prompt="Is this sound?"):
    with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
        return asyncio.run(gate_council(gateway, kind)._call_seat_async(seat, prompt))


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class GoogleSeatRequestTests(unittest.TestCase):
    def test_the_google_seat_requests_the_pinned_gemini_model_on_google_ai_studio(self):
        gateway = Gateway()
        vote = seat_vote(gateway, "gemini")
        self.assertEqual(len(gateway.requests), 1)
        payload = gateway.requests[0]
        self.assertEqual(payload["model"], "google/gemini-3.1-pro-preview")
        self.assertEqual(payload["provider"]["only"], ["google-ai-studio"])
        self.assertEqual(payload["provider"]["data_collection"], "deny")
        self.assertFalse(vote.errored)

    def test_the_vote_records_the_request_and_what_the_gateway_reported(self):
        vote = seat_vote(Gateway(), "gemini")
        self.assertEqual(vote.role, "google_top")
        self.assertEqual(vote.registry_key, "gemini-3.1-pro-preview")
        self.assertEqual(vote.requested_model, "google/gemini-3.1-pro-preview")
        self.assertEqual(vote.served_model, "google/gemini-3.1-pro-preview")
        self.assertEqual(vote.served_provider, "Google AI Studio")
        self.assertEqual(vote.generation_id, "gen-google-1")

    def test_the_other_two_seats_record_their_own_roles(self):
        for seat, role, key, model in (
                ("openai", "openai_top", "gpt-6-astra", "openai/gpt-6-astra"),
                ("anthropic", "anthropic_top", "claude-opus-5.5-xhigh",
                 "anthropic/claude-opus-5.5")):
            with self.subTest(seat=seat):
                vote = seat_vote(Gateway(), seat)
                self.assertEqual((vote.role, vote.registry_key, vote.requested_model),
                                 (role, key, model))
                self.assertEqual(vote.served_model, model)
                self.assertFalse(vote.errored)

    def test_the_three_seats_request_three_distinct_namespaces(self):
        gateway = Gateway()
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
            result = asyncio.run(gate_council(gateway).deliberate("Is this sound?"))
        self.assertEqual(len(result.votes), 3)
        namespaces = {provider_namespace(v.requested_model) for v in result.votes}
        self.assertEqual(namespaces, {"openai", "anthropic", "google"})
        for vote in result.votes:
            with self.subTest(seat=vote.provider):
                self.assertFalse(vote.errored)
                for name in ("role", "registry_key", "requested_model", "served_model",
                             "served_provider", "generation_id"):
                    self.assertIsNotNone(getattr(vote, name), name)
                self.assertEqual(vote.served_model, vote.requested_model)

    def test_the_gateway_key_appears_in_no_vote_field(self):
        gateway = Gateway()
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
            result = asyncio.run(gate_council(gateway).deliberate("Is this sound?"))
        # Positive control: the key does travel, in the request header.
        self.assertTrue(any(DUMMY_KEY in v for h in gateway.headers for v in h.values()))
        for vote in result.votes:
            self.assertNotIn(DUMMY_KEY, repr(vote))
        self.assertNotIn(DUMMY_KEY, repr(result))


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class GateInstructionTests(unittest.TestCase):
    def test_verify_adds_architectural_and_adr_and_patch_do_not(self):
        self.assertIn("ARCHITECTURAL", gate_vote_instruction("verify"))
        self.assertNotIn("ARCHITECTURAL", gate_vote_instruction("adr"))
        self.assertNotIn("ARCHITECTURAL", gate_vote_instruction("patch"))

    def test_every_kind_names_the_four_tokens_and_the_tagging_rules(self):
        for kind in ("adr", "verify", "patch"):
            text = gate_vote_instruction(kind)
            with self.subTest(kind=kind):
                for token in ("APPROVE", "APPROVE_WITH_NITS", "REQUEST_CHANGES", "REJECT",
                              "confidence", "reasoning", "findings", "safety_adjacent",
                              "blocking", "nit", "Security", "no other token"):
                    self.assertIn(token, text)

    def test_an_unknown_kind_is_refused(self):
        for kind in ("", "council", None, "ADR"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                gate_vote_instruction(kind)

    def test_a_gate_config_needs_a_known_kind(self):
        for kind in (None, "", "other"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                AsyncCouncilConfig(gate=True, council_kind=kind)
        # Positive control: a known kind constructs.
        self.assertTrue(AsyncCouncilConfig(gate=True, council_kind="adr").gate)

    def test_the_gate_request_carries_its_instruction_and_a_plain_one_carries_the_old_one(self):
        gateway = Gateway()
        seat_vote(gateway, "gemini", kind="verify", prompt="QUESTION")
        sent = gateway.requests[0]["messages"][-1]["content"]
        self.assertTrue(sent.startswith("QUESTION"))
        self.assertTrue(sent.endswith(gate_vote_instruction("verify")))
        self.assertNotIn(AsyncCouncil._VOTE_JSON_INSTRUCTION, sent)
        plain = Gateway()
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
            council = AsyncCouncil(AsyncCouncilConfig(transport=plain.transport))
            asyncio.run(council._call_seat_async("gemini", "QUESTION"))
        self.assertTrue(plain.requests[0]["messages"][-1]["content"]
                        .endswith(AsyncCouncil._VOTE_JSON_INSTRUCTION))

    def test_a_config_mutated_after_construction_cannot_change_the_instruction_sent(self):
        gateway = Gateway()
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
            council = gate_council(gateway, kind="adr")
            council.config.council_kind = "verify"
            asyncio.run(council._call_seat_async("gemini", "QUESTION"))
        sent = gateway.requests[0]["messages"][-1]["content"]
        self.assertTrue(sent.endswith(gate_vote_instruction("adr")))
        self.assertNotIn("ARCHITECTURAL", sent)
        # Positive control: the kinds differ, so the assertion above can fail.
        self.assertNotEqual(gate_vote_instruction("adr"), gate_vote_instruction("verify"))


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class GateFindingsParseTests(unittest.TestCase):
    def test_findings_get_seat_qualified_ids_and_keep_their_tags(self):
        findings = [
            {"id": "F1", "dimension": "Security", "safety_adjacent": True,
             "kind": "blocking", "text": "leaks a path"},
            {"id": "n.2-a", "dimension": "Clarity", "safety_adjacent": False,
             "kind": "nit", "text": "wording"},
        ]
        vote = seat_vote(Gateway(content=vote_json(findings=findings)), "gemini")
        self.assertEqual([f["id"] for f in vote.findings],
                         ["google_top:F1", "google_top:n.2-a"])
        self.assertEqual(vote.findings[0], {
            "id": "google_top:F1", "dimension": "Security", "safety_adjacent": True,
            "kind": "blocking", "text": "leaks a path"})
        self.assertEqual(vote.dissenting_points, ["leaks a path", "wording"])

    def test_the_same_local_id_on_two_seats_stays_distinct(self):
        findings = [{"id": "F1", "dimension": "Clarity", "safety_adjacent": False,
                     "kind": "nit", "text": "t"}]
        gateway = Gateway(content=vote_json(findings=findings))
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
            result = asyncio.run(gate_council(gateway).deliberate("q"))
        ids = [f["id"] for v in result.votes for f in v.findings]
        self.assertEqual(sorted(ids), ["anthropic_top:F1", "google_top:F1", "openai_top:F1"])

    def test_a_missing_or_mistyped_tag_is_none(self):
        findings = [
            {"id": "F1", "text": "bare"},
            {"id": "F2", "dimension": 5, "safety_adjacent": "true", "kind": "major",
             "text": 7},
            {"id": "F3", "dimension": "Security", "safety_adjacent": 1, "kind": ["blocking"],
             "text": "x"},
        ]
        vote = seat_vote(Gateway(content=vote_json(findings=findings)), "gemini")
        self.assertFalse(vote.errored)
        for finding in vote.findings:
            self.assertIsNone(finding["safety_adjacent"], finding)
            self.assertIsNone(finding["kind"], finding)
        self.assertIsNone(vote.findings[0]["dimension"])
        self.assertIsNone(vote.findings[1]["dimension"])
        self.assertIsNone(vote.findings[1]["text"])
        self.assertEqual(set(vote.findings[0]), {"id", "dimension", "safety_adjacent",
                                                 "kind", "text"})

    def test_a_bad_or_repeated_id_is_replaced_or_suffixed(self):
        findings = [{"id": "has space"}, {"id": "../x"}, {"id": "A"}, {"id": "A"},
                    {"id": "A" * 65}, {}]
        vote = seat_vote(Gateway(content=vote_json(findings=findings)), "gemini")
        ids = [f["id"] for f in vote.findings]
        self.assertEqual(ids, ["google_top:F1", "google_top:F2", "google_top:A",
                               "google_top:A-2", "google_top:F5", "google_top:F6"])
        self.assertEqual(len(set(ids)), len(ids))
        # Positive control: a 64-character id is kept.
        vote = seat_vote(Gateway(content=vote_json(findings=[{"id": "B" * 64}])), "gemini")
        self.assertEqual(vote.findings[0]["id"], "google_top:" + "B" * 64)

    def schema_id_pattern(self):
        schema = json.loads((SCRIPTS.parent / "schemas" / "council-record.schema.json")
                            .read_text())
        found = []

        def walk(node):
            if isinstance(node, dict):
                pattern = node.get("pattern")
                if isinstance(pattern, str) and pattern.startswith("^(openai_top|anthropic_top"):
                    found.append(pattern)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        walk(schema)
        self.assertEqual(len(found), 1, "the record schema names one finding-id pattern")
        return re.compile(found[0])

    def test_every_produced_id_matches_the_record_schema_pattern(self):
        # S3: a de-duplicated id `<role>:<local>-<n>` with a 63- or 64-character
        # local id passed the 64-character limit and failed the record schema.
        pattern = self.schema_id_pattern()
        ids = (["A" * 64] * 3 + ["B" * 63] * 12 + ["C" * 62] * 2 + ["D" * 64 + "x"]
               + ["A" * 62 + "-2"] + ["E"] * 11 + [""] * 2 + ["F" * 64])
        vote = seat_vote(Gateway(content=vote_json(findings=[{"id": i} for i in ids])),
                         "gemini")
        self.assertFalse(vote.errored)
        produced = [f["id"] for f in vote.findings]
        self.assertEqual(len(produced), len(ids))
        self.assertEqual(len(set(produced)), len(produced), "ids stay distinct")
        for fid in produced:
            with self.subTest(id=fid):
                self.assertRegex(fid, pattern)
        # Positive control: the pattern refuses an id one character too long, so the
        # loop above can fail.
        self.assertIsNone(pattern.fullmatch("google_top:" + "A" * 65))
        self.assertIsNotNone(pattern.fullmatch("google_top:" + "A" * 64))

    def test_a_duplicate_id_keeps_its_prefix_when_truncated(self):
        vote = seat_vote(Gateway(content=vote_json(findings=[
            {"id": "A" * 64}, {"id": "A" * 64}, {"id": "A" * 64}])), "gemini")
        ids = [f["id"] for f in vote.findings]
        self.assertEqual(ids[0], "google_top:" + "A" * 64)
        self.assertEqual(ids[1], "google_top:" + "A" * 62 + "-2")
        self.assertEqual(ids[2], "google_top:" + "A" * 62 + "-3")

    def test_absent_findings_are_an_empty_list(self):
        content = json.dumps({"decision": "APPROVE", "confidence": 0.9, "reasoning": "r"})
        vote = seat_vote(Gateway(content=content), "gemini")
        self.assertFalse(vote.errored)
        self.assertEqual(vote.findings, [])

    def test_malformed_findings_error_the_seat(self):
        for bad in ("a string", {"id": "F1"}, ["not an object"], [1], [None]):
            with self.subTest(findings=bad):
                vote = seat_vote(Gateway(content=vote_json(findings=bad)), "gemini")
                self.assertTrue(vote.errored)
                self.assertEqual(vote.fault_label, "malformed-response")

    def test_the_decision_is_kept_as_given_and_absent_defers(self):
        for decision in ("REQUEST_CHANGES", "ARCHITECTURAL", "APPROVE_WITH_NITS", "bogus"):
            with self.subTest(decision=decision):
                vote = seat_vote(Gateway(content=vote_json(decision=decision)), "gemini")
                self.assertEqual(vote.decision, decision)
        content = json.dumps({"confidence": 0.9, "reasoning": "r", "findings": []})
        self.assertEqual(seat_vote(Gateway(content=content), "gemini").decision,
                         "DEFER_TO_HUMAN")

    def test_a_non_string_decision_errors_the_seat(self):
        for bad in (5, None, ["APPROVE"], {"a": 1}):
            with self.subTest(decision=bad):
                vote = seat_vote(Gateway(content=vote_json(decision=bad)), "gemini")
                self.assertTrue(vote.errored)
                self.assertEqual(vote.fault_label, "malformed-response")

    def test_a_full_deliberation_aggregates_unchanged(self):
        gateway = Gateway(content=vote_json(findings=[
            {"id": "F1", "dimension": "Clarity", "safety_adjacent": False, "kind": "nit",
             "text": "t"}]))
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
            result = asyncio.run(gate_council(gateway).deliberate("q"))
        self.assertEqual(result.consensus, "UNANIMOUS_APPROVE")
        self.assertEqual(result.dissent_count, 3)



@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class EscapedSeatFaultTests(unittest.TestCase):
    """An exception that escapes a seat's own wrapper becomes that seat's errored vote, carrying
    the seat's role and registry key. A cancellation is never a vote: it propagates."""

    def deliberate_with(self, gateway, raise_for_gemini):
        council = gate_council(gateway)
        original = AsyncCouncil._call_seat_async

        async def call(self_, seat, prompt, system=None):
            if seat == "gemini" and raise_for_gemini is not None:
                raise raise_for_gemini
            return await original(self_, seat, prompt, system)

        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}), \
                mock.patch.object(AsyncCouncil, "_call_seat_async", call):
            return asyncio.run(council.deliberate("Is this sound?"))

    def test_an_escaped_exception_errors_the_seat_with_its_role_and_registry_key(self):
        result = self.deliberate_with(Gateway(), RuntimeError("escaped the wrapper"))
        google = next(v for v in result.votes if v.provider == "gemini")
        self.assertTrue(google.errored)
        self.assertEqual((google.role, google.registry_key), ("google_top", "gemini-3.1-pro-preview"))
        self.assertEqual(sorted(v.role for v in result.votes), ["anthropic_top", "google_top", "openai_top"])
        # Positive control: the same council with no escaped fault seats the same roles, unerrored.
        clean = self.deliberate_with(Gateway(), None)
        self.assertEqual([v.errored for v in clean.votes], [False, False, False])
        self.assertEqual(sorted(v.role for v in clean.votes), ["anthropic_top", "google_top", "openai_top"])

    def test_a_cancelled_seat_propagates_and_is_never_appended_as_a_vote(self):
        with self.assertRaises(asyncio.CancelledError):
            self.deliberate_with(Gateway(), asyncio.CancelledError())


if __name__ == "__main__":
    unittest.main()
