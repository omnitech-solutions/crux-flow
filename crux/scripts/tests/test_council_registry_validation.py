"""Registry tombstones, accepted served-model and served-provider sets, and gate-mode seat refusal.

The first class reads the real shipped registry. The rest build a temporary
registry from it (patching `llm_caller.CONFIG_PATH`), so each refusal is paired
with a positive control on the same registry: refusals send zero requests, and
the unmodified registry sends exactly one. No network, no real key.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
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
        AsyncCouncilConfig,
        AsyncVisualCouncil,
        CouncilAssignmentRefused,
        create_async_council,
        validate_gate_assignment,
    )
    from crux.core import llm_caller

DUMMY_KEY = "sk-or-test-dummy"
REAL_REGISTRY = SCRIPTS / "crux" / "_config" / "llm_router_config.json"
PINNED_TEXT = ("gpt-6-astra", "gpt-6.1-sol", "claude-fable-5.1", "claude-opus-5.5",
               "claude-opus-5.5-xhigh", "gemini-3.1-pro-preview")
#: The served-provider seeds, one per entry the round-1 council record seated, each the
#: label that record observed for the entry's serving_providers pin.
PROVIDER_SEEDS = {"gpt-6-astra": ["OpenAI"], "claude-opus-5.5-xhigh": ["Anthropic"],
                  "gemini-3.1-pro-preview": ["Google AI Studio"]}
#: What the gateway reports as `provider` for each vendor namespace.
DISPLAY = {"openai": "OpenAI", "anthropic": "Anthropic", "google": "Google AI Studio"}


def real_registry():
    return json.loads(REAL_REGISTRY.read_text())


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class ShippedRegistryTests(unittest.TestCase):
    def setUp(self):
        self.config = real_registry()

    def test_the_version_names_this_schema_change(self):
        self.assertEqual(self.config["version"], "4.2.0")

    def test_the_provider_sets_are_exactly_the_observed_seeds(self):
        declared = {k: v["accepted_served_providers"] for k, v in self.config["models"].items()
                    if "accepted_served_providers" in v}
        self.assertEqual(declared, PROVIDER_SEEDS)

    def test_each_seed_names_the_host_its_pin_names(self):
        # ADR-0145 Decision 4's host criterion, for the three seeds: the pin slug
        # and the label name the same host.
        hosts = {"openai": "OpenAI", "anthropic": "Anthropic", "google-ai-studio": "Google AI Studio"}
        for key, labels in PROVIDER_SEEDS.items():
            with self.subTest(key=key):
                pin = self.config["models"][key]["serving_providers"]
                self.assertEqual(labels, [hosts[slug] for slug in pin])

    def test_the_changelog_cites_the_observing_record(self):
        latest = self.config["_changelog"].split(" | ")[0]
        self.assertIn("4.2.0", latest)
        self.assertIn("bionic/promptbooks/runs/PB-0136-separate-council-deliberation-from-independent/"
                      "council/RUN-001-p2-r1.json", latest)

    def test_the_unobserved_pinned_entries_are_not_council_eligible(self):
        # Fail-closed: a pinned entry with no observed label declares no set.
        for key in sorted(set(PINNED_TEXT) - set(PROVIDER_SEEDS)):
            with self.subTest(key=key):
                self.assertEqual(llm_caller.council_eligibility(key),
                                 (False, "no-accepted-provider-set"))
                self.assertIsNone(llm_caller.get_model_config(key).accepted_served_providers)
        for key in PROVIDER_SEEDS:  # positive control
            with self.subTest(key=key):
                self.assertEqual(llm_caller.council_eligibility(key), (True, "ok"))

    def test_an_unseeded_entry_still_serves_a_non_gate_caller(self):
        # The provider set governs council eligibility only: the request a non-gate
        # caller builds for an unseeded entry is the one it built before.
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY}):
            for key in ("gpt-6.1-sol", "claude-fable-5.1", "claude-opus-5.5"):
                with self.subTest(key=key):
                    cfg = llm_caller.get_model_config(key)
                    _, _, payload = llm_caller.build_gateway_request(cfg, "q")
                    self.assertEqual(payload["model"], cfg.api_string)
                    self.assertEqual(payload["provider"]["only"],
                                     self.config["models"][key]["serving_providers"])
                    self.assertNotIn("accepted_served_providers", json.dumps(payload))

    def test_every_retired_key_is_absent_from_models(self):
        retired = self.config["retired_models"]
        self.assertTrue(retired)
        self.assertEqual(len(retired), len(set(retired)))
        for key in ("gpt-6-sol", "gpt-6-sol-low", "claude-opus-5", "claude-sonnet-5",
                    "gpt-5.6-sol", "gpt-5.6-sol-low", "gpt-5.6-terra", "gpt-5.6-luna"):
            self.assertIn(key, retired)
        for key in retired:
            with self.subTest(key=key):
                self.assertNotIn(key, self.config["models"])
        self.assertFalse(any("image" in key for key in retired))

    def test_every_pinned_text_entry_accepts_exactly_its_api_string(self):
        pinned = [k for k, v in self.config["models"].items()
                  if v.get("type") == "text" and v.get("serving_providers")]
        self.assertEqual(sorted(pinned), sorted(PINNED_TEXT))
        for key in pinned:
            with self.subTest(key=key):
                entry = self.config["models"][key]
                self.assertEqual(entry["accepted_served_models"], [entry["api_string"]])

    def test_every_other_entry_declares_no_set(self):
        for key, entry in self.config["models"].items():
            if key not in PINNED_TEXT:
                with self.subTest(key=key):
                    self.assertNotIn("accepted_served_models", entry)

    def test_the_loader_reads_the_sets_and_the_tombstones(self):
        self.assertEqual(llm_caller.get_model_config("gemini-3.1-pro-preview")
                         .accepted_served_models, ("google/gemini-3.1-pro-preview",))
        self.assertEqual(llm_caller.get_model_config("gemini-3.1-pro-preview")
                         .accepted_served_providers, ("Google AI Studio",))
        self.assertIsNone(llm_caller.get_model_config("claude-sonnet-5.5")
                          .accepted_served_providers)
        self.assertIsNone(llm_caller.get_model_config("claude-sonnet-5.5")
                          .accepted_served_models)
        self.assertEqual(llm_caller.retired_models(), frozenset(self.config["retired_models"]))

    def test_the_three_gate_roles_are_eligible_today(self):
        self.assertEqual(validate_gate_assignment(), {
            "openai_top": "gpt-6-astra", "anthropic_top": "claude-opus-5.5-xhigh",
            "google_top": "gemini-3.1-pro-preview"})

    #: Every role as the 4.1.0 registry holds it, copied from the file. A role that
    #: moves fails this test.
    PINNED_ROLES = {
        "google_top": "gemini-3.1-pro-preview",
        "google_fast": "gemini-3.5-flash",
        "anthropic_top": "claude-opus-5.5-xhigh",
        "anthropic_balanced": "claude-opus-5.5",
        "openai_top": "gpt-6-astra",
        "council_default": ["gpt-6.1-sol", "gemini-3.1-pro-preview", "claude-opus-5.5-xhigh"],
        "council_arbiter": "claude-opus-5.5-xhigh",
        "release_docs": "claude-sonnet-5.5",
    }
    #: Each non-text entry: api_string, type, tier, and the first 16 hex digits of
    #: the sha256 of its `json.dumps(entry, sort_keys=True)`. Any edit to an image
    #: or decisions entry changes its digest.
    PINNED_NON_TEXT = {
        "gpt-image-2.5-sunburst": ("openai/gpt-image-2.5-sunburst", "image_generation",
                                   "image_gen", "e632d83fb3372fa8"),
        "gemini-3-pro-image-preview": ("google/gemini-3-pro-image-preview",
                                       "image_generation_and_editing", "image_gen",
                                       "c1eac5be2037ee6b"),
        "gemini-3.1-flash-image-preview": ("google/gemini-3.1-flash-image-preview",
                                           "image_generation", "image_gen_fast",
                                           "aa0f66ec543dc8b5"),
        "gemini-2.5-flash-image": ("google/gemini-2.5-flash-image", "image_generation",
                                   "image_gen_legacy", "bd925df30581a8c4"),
        "jev-1.13": ("typesafe/jev-1.13", "decisions", None, "703469c46c60ff65"),
    }

    def test_the_roles_are_the_pinned_ones(self):
        roles = {k: v for k, v in self.config["model_roles"].items() if k != "_comment"}
        self.assertEqual(roles, self.PINNED_ROLES)

    def test_the_council_weights_are_the_pinned_ones(self):
        from crux.council.council import MODEL_WEIGHTS
        self.assertEqual(MODEL_WEIGHTS, {"anthropic_top": 1.5, "google_top": 1.3,
                                         "openai_top": 1.4})

    def test_the_non_text_entries_are_the_pinned_ones(self):
        actual = {k for k, v in self.config["models"].items() if v.get("type") != "text"}
        self.assertEqual(actual, set(self.PINNED_NON_TEXT))
        for key, (api_string, kind, tier, digest) in self.PINNED_NON_TEXT.items():
            with self.subTest(key=key):
                entry = self.config["models"][key]
                self.assertEqual((entry["api_string"], entry["type"], entry.get("tier")),
                                 (api_string, kind, tier))
                self.assertEqual(
                    hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()[:16],
                    digest)
                self.assertNotIn("accepted_served_models", entry)
                self.assertNotIn("accepted_served_providers", entry)

    def test_the_pin_detects_an_edit(self):
        # Positive control: the digest the pin compares is sensitive to an edit.
        entry = copy.deepcopy(self.config["models"]["gpt-image-2.5-sunburst"])
        entry["tier"] = "image_gen_fast"
        digest = hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()[:16]
        self.assertNotEqual(digest, self.PINNED_NON_TEXT["gpt-image-2.5-sunburst"][3])


class TempRegistry(unittest.TestCase):
    """A temporary copy of the real registry that tests mutate and the module reads."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "registry.json"
        self.original = llm_caller.CONFIG_PATH
        self.addCleanup(self._restore)
        self.write(real_registry())
        env = mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY_KEY})
        env.start()
        self.addCleanup(env.stop)

    def _restore(self):
        llm_caller.CONFIG_PATH = self.original
        llm_caller.invalidate_model_cache()

    def write(self, config):
        self.path.write_text(json.dumps(config))
        llm_caller.CONFIG_PATH = self.path
        llm_caller.invalidate_model_cache()


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class EligibilityTests(TempRegistry):
    def registry(self):
        config = real_registry()
        config["models"]["no-pin"] = {**config["models"]["gpt-6-astra"],
                                      "serving_providers": []}
        config["models"]["no-pin"].pop("serving_providers")
        config["models"]["no-set"] = {**config["models"]["gpt-6-astra"]}
        config["models"]["no-set"].pop("accepted_served_models")
        config["models"]["an-image"] = {**config["models"]["gpt-6-astra"],
                                        "type": "image_generation"}
        config["models"]["empty-set"] = {**config["models"]["gpt-6-astra"],
                                         "accepted_served_models": []}
        config["models"]["no-provider-set"] = {**config["models"]["gpt-6-astra"]}
        config["models"]["no-provider-set"].pop("accepted_served_providers")
        config["models"]["empty-provider-set"] = {**config["models"]["gpt-6-astra"],
                                                  "accepted_served_providers": []}
        config["retired_models"] = config["retired_models"] + ["gone-key", "both-key"]
        config["models"]["both-key"] = {**config["models"]["gpt-6-astra"]}
        return config

    def test_each_reason(self):
        self.write(self.registry())
        cases = {
            "gpt-6-astra": (True, "ok"),
            # An empty set declares nothing: the entry is unverified (ADR-0144, ADR-0145 F7).
            "empty-set": (False, "no-accepted-set"),
            "no-provider-set": (False, "no-accepted-provider-set"),
            "empty-provider-set": (False, "no-accepted-provider-set"),
            "gone-key": (False, "retired"),
            "gpt-6-sol": (False, "retired"),
            "both-key": (False, "retired"),  # tombstone wins even over a models entry
            "never-existed": (False, "unknown"),
            "an-image": (False, "not-text"),
            "no-pin": (False, "no-serving-pin"),
            "claude-sonnet-5.5": (False, "no-serving-pin"),
            "no-set": (False, "no-accepted-set"),
        }
        for key, expected in cases.items():
            with self.subTest(key=key):
                result = llm_caller.council_eligibility(key)
                self.assertEqual((result.eligible, result.reason), expected)

    def malformed_registry(self):
        config = real_registry()
        base = config["models"]["gpt-6-astra"]
        bad = {
            "str-set": ("accepted_served_models", "openai/gpt-6-astra"),
            "int-in-set": ("accepted_served_models", [1]),
            "dict-set": ("accepted_served_models", {"a": 1}),
            "str-provider-set": ("accepted_served_providers", "OpenAI"),
            "int-in-provider-set": ("accepted_served_providers", [1]),
            "dict-provider-set": ("accepted_served_providers", {"a": 1}),
            "str-pin": ("serving_providers", "openai"),
            "int-in-pin": ("serving_providers", [1]),
            "dict-pin": ("serving_providers", {"a": 1}),
            "bad-collection": ("data_collection", "sometimes"),
        }
        for key, (field, value) in bad.items():
            config["models"][key] = {**base, field: value}
        config["models"]["not-an-object"] = "gpt-6-astra"
        config["models"]["list-type"] = {**base, "type": ["text"]}
        return config

    def test_a_malformed_entry_is_not_eligible_and_never_raises(self):
        self.write(self.malformed_registry())
        cases = {
            "gpt-6-astra": (True, "ok"),  # positive control on the same registry
            "str-set": (False, "malformed-accepted-set"),
            "int-in-set": (False, "malformed-accepted-set"),
            "dict-set": (False, "malformed-accepted-set"),
            "str-provider-set": (False, "malformed-accepted-provider-set"),
            "int-in-provider-set": (False, "malformed-accepted-provider-set"),
            "dict-provider-set": (False, "malformed-accepted-provider-set"),
            "str-pin": (False, "malformed-serving-pin"),
            "int-in-pin": (False, "malformed-serving-pin"),
            "dict-pin": (False, "malformed-serving-pin"),
            "bad-collection": (False, "malformed-entry"),
            "not-an-object": (False, "malformed-entry"),
            "list-type": (False, "malformed-entry"),
        }
        for key, expected in cases.items():
            with self.subTest(key=key):
                result = llm_caller.council_eligibility(key)
                self.assertEqual((result.eligible, result.reason), expected)

    def test_a_non_string_api_string_is_malformed_never_a_crash(self):
        config = real_registry()
        for key, value in {"int-api": 7, "list-api": ["openai/x"], "none-api": None,
                           "no-slash-api": "noslash"}.items():
            config["models"][key] = {**config["models"]["gpt-6-astra"], "api_string": value}
        self.write(config)
        self.assertEqual(llm_caller.council_eligibility("gpt-6-astra").reason, "ok")
        for key in ("int-api", "list-api", "none-api", "no-slash-api"):
            with self.subTest(key=key):
                result = llm_caller.council_eligibility(key)
                self.assertFalse(result.eligible)
                self.assertEqual(result.reason, "malformed-entry")

    def test_an_eligible_key_is_one_the_seat_can_load(self):
        # The invariant the malformed reasons protect: eligible means loadable.
        self.write(self.malformed_registry())
        for key in self.malformed_registry()["models"]:
            with self.subTest(key=key):
                if llm_caller.council_eligibility(key).eligible:
                    llm_caller.get_model_config(key)

    def test_a_retired_key_is_never_aliased(self):
        self.write(self.registry())
        self.assertEqual(llm_caller.council_eligibility("gpt-6-sol").reason, "retired")
        self.assertNotEqual(llm_caller.council_eligibility("gpt-6.1-sol").reason, "retired")

    def test_a_missing_retired_list_is_the_empty_set(self):
        config = real_registry()
        del config["retired_models"]
        self.write(config)
        self.assertEqual(llm_caller.retired_models(), frozenset())
        self.assertEqual(llm_caller.council_eligibility("gpt-6-sol").reason, "unknown")

    def test_a_malformed_retired_list_raises(self):
        for bad in ("gpt-6-sol", [1], [["a"]], {"a": 1}):
            config = real_registry()
            config["retired_models"] = bad
            self.write(config)
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                llm_caller.retired_models()

    def test_a_malformed_accepted_set_raises_on_load(self):
        for field in ("accepted_served_models", "accepted_served_providers"):
            for bad in ("openai/gpt-6-astra", [1], [None], {"a": 1}, 5):
                config = real_registry()
                config["models"]["gpt-6-astra"][field] = bad
                self.write(config)
                with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                    llm_caller.get_model_config("gpt-6-astra")
        # Positive control: a list of strings loads as a tuple. An empty list loads,
        # and declares no set.
        self.write(real_registry())
        cfg = llm_caller.get_model_config("gpt-6-astra")
        self.assertEqual((cfg.accepted_served_models, cfg.accepted_served_providers),
                         (("openai/gpt-6-astra",), ("OpenAI",)))
        config = real_registry()
        config["models"]["gpt-6-astra"]["accepted_served_models"] = []
        config["models"]["gpt-6-astra"]["accepted_served_providers"] = []
        self.write(config)
        cfg = llm_caller.get_model_config("gpt-6-astra")
        self.assertIsNone(cfg.accepted_served_models)
        self.assertIsNone(cfg.accepted_served_providers)

    def test_the_cache_is_cleared_by_invalidate(self):
        self.assertIn("gpt-6-sol", llm_caller.retired_models())
        config = real_registry()
        config["retired_models"] = []
        self.write(config)  # write() invalidates
        self.assertEqual(llm_caller.retired_models(), frozenset())

    def test_provider_namespace(self):
        self.assertEqual(llm_caller.provider_namespace("openai/gpt-6-astra"), "openai")
        self.assertEqual(llm_caller.provider_namespace("a/b/c"), "a")
        for bad in ("no-slash", "/x", "", "/"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                llm_caller.provider_namespace(bad)


class Counter:
    def __init__(self):
        self.requests = 0
        self.header_values = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request):
        self.requests += 1
        self.header_values.extend(request.headers.values())
        payload = json.loads(request.content)
        model = payload["model"]
        return httpx.Response(200, json={
            "id": "gen-1", "model": model, "provider": DISPLAY[model.split("/")[0]],
            "choices": [{"message": {"content": json.dumps(
                {"decision": "APPROVE", "confidence": 0.9, "reasoning": "r",
                 "findings": []})}, "finish_reason": "stop"}],
        })


def gate_config(counter, **extra):
    return AsyncCouncilConfig(gate=True, council_kind="adr", transport=counter.transport,
                              timeout_seconds=5.0, **extra)


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class GateRefusalTests(TempRegistry):
    """Each refusal sends zero requests; the paired control sends exactly one."""

    def point(self, role, key):
        def mutate(config):
            config["model_roles"][role] = key
        return mutate

    def cases(self):
        def no_set(config):
            del config["models"]["gpt-6-astra"]["accepted_served_models"]

        def empty_set(config):
            config["models"]["gpt-6-astra"]["accepted_served_models"] = []

        def no_provider_set(config):
            del config["models"]["claude-opus-5.5-xhigh"]["accepted_served_providers"]

        def empty_provider_set(config):
            config["models"]["gemini-3.1-pro-preview"]["accepted_served_providers"] = []

        def two_roles_one_namespace(config):
            # gpt-6.1-sol declares no provider set; give it one so the namespace
            # rule, not eligibility, is what refuses.
            config["models"]["gpt-6.1-sol"]["accepted_served_providers"] = ["OpenAI"]
            config["model_roles"]["anthropic_top"] = "gpt-6.1-sol"

        def unseeded_seat(config):
            config["model_roles"]["openai_top"] = "gpt-6.1-sol"

        def missing_role(config):
            del config["model_roles"]["google_top"]

        def non_string_role(config):
            config["model_roles"]["google_top"] = ["gemini-3.1-pro-preview"]

        def tombstone_present_in_models(config):
            config["retired_models"].append("gpt-6-astra")

        def image_seat(config):
            config["model_roles"]["openai_top"] = "gpt-image-2.5-sunburst"

        def no_pin(config):
            config["model_roles"]["anthropic_top"] = "claude-sonnet-5.5"

        def int_api_string(config):
            config["models"]["gpt-6-astra"]["api_string"] = 7

        def list_api_string(config):
            config["models"]["gpt-6-astra"]["api_string"] = ["openai/gpt-6-astra"]

        return {
            "tombstoned-key": (self.point("anthropic_top", "claude-opus-5"),
                               "refused-assignment", ["anthropic_top", "claude-opus-5"]),
            "tombstoned-key-still-in-models": (tombstone_present_in_models,
                                               "refused-assignment", ["gpt-6-astra"]),
            "no-declared-set": (no_set, "refused-assignment", ["gpt-6-astra"]),
            "empty-declared-set": (empty_set, "refused-assignment", ["gpt-6-astra"]),
            "model-set-but-no-provider-set": (no_provider_set, "refused-assignment",
                                              ["anthropic_top", "claude-opus-5.5-xhigh"]),
            "model-set-but-empty-provider-set": (empty_provider_set, "refused-assignment",
                                                 ["google_top", "gemini-3.1-pro-preview"]),
            "unseeded-seat": (unseeded_seat, "refused-assignment", ["openai_top", "gpt-6.1-sol"]),
            "unknown-key": (self.point("openai_top", "no-such-model"),
                            "refused-assignment", ["no-such-model"]),
            "two-roles-one-namespace": (two_roles_one_namespace,
                                        "fewer-than-three-providers", ["gpt-6.1-sol"]),
            "missing-role": (missing_role, "refused-assignment", ["google_top"]),
            "non-string-role": (non_string_role, "refused-assignment", ["google_top"]),
            "image-seat": (image_seat, "refused-assignment", ["gpt-image-2.5-sunburst"]),
            "no-serving-pin": (no_pin, "refused-assignment", ["claude-sonnet-5.5"]),
            "int-api-string": (int_api_string, "refused-assignment", ["gpt-6-astra"]),
            "list-api-string": (list_api_string, "refused-assignment", ["gpt-6-astra"]),
        }

    def one_request_when_valid(self):
        counter = Counter()
        council = create_async_council(gate_config(counter))
        vote = asyncio.run(council._call_seat_async("openai", "q"))
        self.assertFalse(vote.errored)
        return counter.requests

    def test_each_refusal_sends_nothing_and_the_valid_registry_sends_one(self):
        for name, (mutate, code, names) in self.cases().items():
            with self.subTest(case=name):
                self.write(real_registry())
                self.assertEqual(self.one_request_when_valid(), 1)  # positive control
                config = real_registry()
                mutate(config)
                self.write(config)
                counter = Counter()
                with self.assertRaises(CouncilAssignmentRefused) as caught:
                    asyncio.run(create_async_council(gate_config(counter))
                                .deliberate("q"))
                self.assertEqual(caught.exception.code, code)
                for expected in names:
                    self.assertIn(expected, caught.exception.names)
                self.assertEqual(counter.requests, 0)

    def test_the_refusal_is_raised_by_the_config_before_any_council_exists(self):
        config = real_registry()
        config["model_roles"]["anthropic_top"] = "claude-opus-5"
        self.write(config)
        counter = Counter()
        with self.assertRaises(CouncilAssignmentRefused) as caught:
            gate_config(counter)
        self.assertEqual(caught.exception.code, "refused-assignment")
        self.assertIn("claude-opus-5", caught.exception.names)
        self.assertEqual(counter.requests, 0)

    def test_the_validation_function_refuses_the_same_assignments(self):
        for name, (mutate, code, _) in self.cases().items():
            with self.subTest(case=name):
                config = real_registry()
                mutate(config)
                self.write(config)
                with self.assertRaises(CouncilAssignmentRefused) as caught:
                    validate_gate_assignment()
                self.assertEqual(caught.exception.code, code)

    def test_a_seat_override_is_refused_by_field_name(self):
        for field in ("openai_model", "anthropic_model", "gemini_model"):
            with self.subTest(field=field):
                counter = Counter()
                # The override itself names a valid, eligible key: it is refused
                # because gate seats come only from the roles.
                with self.assertRaises(CouncilAssignmentRefused) as caught:
                    gate_config(counter, **{field: "gpt-6-astra"})
                self.assertEqual(caught.exception.code, "refused-assignment")
                self.assertEqual(caught.exception.names, [field])
                self.assertEqual(counter.requests, 0)
        # Positive control: the same override outside gate mode is accepted.
        counter = Counter()
        config = AsyncCouncilConfig(openai_model="gpt-6-astra", transport=counter.transport)
        self.assertEqual(config.openai_model, "gpt-6-astra")

    def test_a_non_gate_council_still_seats_a_retired_or_unpinned_role_it_was_given(self):
        # Gate mode is the only mode that refuses; the plain council is unchanged.
        config = real_registry()
        config["model_roles"]["anthropic_top"] = "claude-sonnet-5.5"
        self.write(config)
        counter = Counter()
        plain = AsyncCouncilConfig(transport=counter.transport)
        self.assertEqual(plain.anthropic_model, "claude-sonnet-5.5")

    def test_the_refusal_names_no_secret_and_no_model_output(self):
        # Positive control first: the capture mechanism (the request headers of a
        # valid council) does see the key, so its absence below is a finding.
        counter = Counter()
        valid = create_async_council(gate_config(counter))
        asyncio.run(valid._call_seat_async("openai", "q"))
        self.assertEqual(counter.requests, 1)
        self.assertIn(DUMMY_KEY, " ".join(counter.header_values))
        config = real_registry()
        config["model_roles"]["openai_top"] = "no-such-model"
        self.write(config)
        with self.assertRaises(CouncilAssignmentRefused) as caught:
            gate_config(Counter())
        self.assertNotIn(DUMMY_KEY, str(caught.exception))
        self.assertNotIn(DUMMY_KEY, " ".join(caught.exception.names))
        self.assertNotIn(DUMMY_KEY, repr(caught.exception))
        self.assertIn(caught.exception.code, CouncilAssignmentRefused.CODES)

    def test_a_malformed_registry_entry_refuses_the_council_before_any_request(self):
        # S2: a string accepted set or serving pin used to pass eligibility, then
        # error the seat after the other two had spent.
        for field, value in (("accepted_served_models", "openai/gpt-6-astra"),
                             ("accepted_served_providers", "OpenAI"),
                             ("accepted_served_providers", [1]),
                             ("serving_providers", "openai"),
                             ("serving_providers", [1]),
                             ("data_collection", "sometimes")):
            with self.subTest(field=field, value=value):
                self.write(real_registry())
                self.assertEqual(self.one_request_when_valid(), 1)  # positive control
                config = real_registry()
                config["models"]["gpt-6-astra"][field] = value
                self.write(config)
                counter = Counter()
                with self.assertRaises(CouncilAssignmentRefused) as caught:
                    asyncio.run(create_async_council(gate_config(counter)).deliberate("q"))
                self.assertEqual(caught.exception.code, "refused-assignment")
                self.assertIn("gpt-6-astra", caught.exception.names)
                self.assertEqual(counter.requests, 0)

    def test_the_gate_flag_must_be_a_bool(self):
        # B2: `if self.gate:` accepted 1 and "yes", and the runtime checks, which
        # read `is True`, then verified nothing.
        for bad in (1, "yes", "True", 1.0, [True], {"a": 1}, 0, ""):
            with self.subTest(gate=bad):
                counter = Counter()
                with self.assertRaises(ValueError):
                    AsyncCouncilConfig(gate=bad, council_kind="adr",
                                       transport=counter.transport)
                self.assertEqual(counter.requests, 0)
        # Positive control: True constructs and runs one verified seat.
        counter = Counter()
        council = create_async_council(AsyncCouncilConfig(
            gate=True, council_kind="adr", transport=counter.transport, timeout_seconds=5.0))
        self.assertFalse(asyncio.run(council._call_seat_async("openai", "q")).errored)
        self.assertEqual(counter.requests, 1)

    def test_gate_mode_is_read_the_same_way_everywhere(self):
        # Paired-construct sweep for B2: a config mutated to a truthy non-bool after
        # construction is refused by the council, never run as an unchecked non-gate.
        counter = Counter()
        config = gate_config(counter)
        config.gate = 1
        with self.assertRaises(CouncilAssignmentRefused):
            create_async_council(config)
        self.assertEqual(counter.requests, 0)
        counter = Counter()
        council = create_async_council(gate_config(counter))
        council.config.gate = 1
        with self.assertRaises(CouncilAssignmentRefused):
            asyncio.run(council.deliberate("q"))
        self.assertEqual(counter.requests, 0)

    def test_a_seat_edited_after_the_config_was_built_is_refused(self):
        # S1: validation ran only in __post_init__, and the config is mutable.
        counter = Counter()
        config = gate_config(counter)
        config.openai_model = "gpt-6.1-sol"  # a second OpenAI seat
        with self.assertRaises(CouncilAssignmentRefused) as caught:
            asyncio.run(create_async_council(config).deliberate("q"))
        self.assertEqual(caught.exception.code, "refused-assignment")
        self.assertIn("openai_model", caught.exception.names)
        self.assertEqual(counter.requests, 0)
        # Positive control: the untouched config seats three providers and sends three.
        counter = Counter()
        result = asyncio.run(create_async_council(gate_config(counter)).deliberate("q"))
        self.assertEqual((counter.requests, len(result.votes)), (3, 3))

    def test_a_seat_edited_after_the_council_was_built_is_refused_at_deliberate(self):
        counter = Counter()
        council = create_async_council(gate_config(counter))
        council.config.anthropic_model = "claude-opus-5"
        council._seat_models["anthropic"] = "claude-opus-5"
        with self.assertRaises(CouncilAssignmentRefused):
            asyncio.run(council.deliberate("q"))
        self.assertEqual(counter.requests, 0)

    def test_a_registry_edit_after_the_council_was_built_is_refused_at_deliberate(self):
        counter = Counter()
        council = create_async_council(gate_config(counter))
        config = real_registry()
        config["model_roles"]["anthropic_top"] = "claude-opus-5"
        self.write(config)
        with self.assertRaises(CouncilAssignmentRefused):
            asyncio.run(council.deliberate("q"))
        self.assertEqual(counter.requests, 0)
        # Positive control: with the registry restored the same council runs.
        self.write(real_registry())
        result = asyncio.run(council.deliberate("q"))
        self.assertEqual((counter.requests, len(result.votes)), (3, 3))

    def test_a_non_gate_config_set_to_gate_afterwards_is_validated(self):
        config = real_registry()
        config["model_roles"]["anthropic_top"] = "claude-sonnet-5.5"  # no serving pin
        self.write(config)
        counter = Counter()
        plain = AsyncCouncilConfig(transport=counter.transport, timeout_seconds=5.0)
        plain.gate, plain.council_kind = True, "adr"
        with self.assertRaises(CouncilAssignmentRefused):
            asyncio.run(create_async_council(plain).deliberate("q"))
        self.assertEqual(counter.requests, 0)
        # Positive control: on the valid registry the same late flip is a valid gate council.
        self.write(real_registry())
        counter = Counter()
        plain = AsyncCouncilConfig(transport=counter.transport, timeout_seconds=5.0)
        plain.gate, plain.council_kind = True, "adr"
        result = asyncio.run(create_async_council(plain).deliberate("q"))
        self.assertEqual((counter.requests, len(result.votes)), (3, 3))

    def test_the_gate_flag_changed_after_construction_is_refused(self):
        for before, after in ((True, False), (False, True)):
            with self.subTest(before=before, after=after):
                counter = Counter()
                kind = "adr"
                config = AsyncCouncilConfig(gate=before, council_kind=kind,
                                            transport=counter.transport, timeout_seconds=5.0)
                council = create_async_council(config)
                config.gate = after
                with self.assertRaises(CouncilAssignmentRefused):
                    asyncio.run(council.deliberate("q"))
                self.assertEqual(counter.requests, 0)
        # Positive control: an unchanged flag runs, in both modes.
        for gate in (True, False):
            counter = Counter()
            config = AsyncCouncilConfig(gate=gate, council_kind="adr",
                                        transport=counter.transport, timeout_seconds=5.0)
            result = asyncio.run(create_async_council(config).deliberate("q"))
            self.assertEqual((counter.requests, len(result.votes)), (3, 3))

    def test_role_is_recorded_only_for_a_gate_seat(self):
        # N2: outside gate mode a caller may seat any key, so the slot's role would
        # name a model the seat did not run. The role is None there.
        counter = Counter()
        plain = AsyncCouncilConfig(openai_model="gpt-6.1-sol", transport=counter.transport,
                                   timeout_seconds=5.0)
        vote = asyncio.run(create_async_council(plain)._call_seat_async("openai", "q"))
        self.assertIsNone(vote.role)
        self.assertEqual(vote.registry_key, "gpt-6.1-sol")
        # Positive control: the gate seat on the same slot records its role.
        gate_vote = asyncio.run(create_async_council(gate_config(Counter()))
                                ._call_seat_async("openai", "q"))
        self.assertEqual((gate_vote.role, gate_vote.registry_key),
                         ("openai_top", "gpt-6-astra"))

    def test_the_visual_entry_refuses_a_seat_edited_after_construction(self):
        counter = Counter()
        config = gate_config(counter)
        config.openai_model = "gpt-6.1-sol"
        with self.assertRaises(CouncilAssignmentRefused):
            asyncio.run(AsyncVisualCouncil(config).analyze_image("aGk=", "probe"))
        self.assertEqual(counter.requests, 0)
        counter = Counter()  # positive control
        asyncio.run(AsyncVisualCouncil(gate_config(counter)).analyze_image("aGk=", "p"))
        self.assertEqual(counter.requests, 2)

    def test_the_resolution_pins_the_seat_keys_into_the_config(self):
        counter = Counter()
        config = gate_config(counter)
        self.assertEqual((config.openai_model, config.anthropic_model, config.gemini_model),
                         ("gpt-6-astra", "claude-opus-5.5-xhigh", "gemini-3.1-pro-preview"))



@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable — run under uv")
class MalformedTombstoneListTests(TempRegistry):
    def test_a_malformed_retired_models_list_refuses_the_assignment_naming_the_registry(self):
        counter = Counter()
        self.assertTrue(gate_config(counter).gate)  # positive control: the real registry seats
        for name, value in (("non-string entry", [1]), ("not a list", "gpt-4")):
            with self.subTest(case=name):
                config = real_registry()
                config["retired_models"] = value
                self.write(config)
                with self.assertRaises(CouncilAssignmentRefused) as cm:
                    gate_config(counter)
                self.assertEqual((cm.exception.code, cm.exception.names), ("refused-assignment", ["registry"]))
        self.assertEqual(counter.requests, 0)


if __name__ == "__main__":
    unittest.main()
