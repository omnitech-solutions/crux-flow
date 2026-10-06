"""Regression tests for the LLM router's per-model request shaping.

Every model resolves through one OpenAI-compatible gateway at OpenRouter
(ADR-0087), so the shaping rules are now properties of ONE request builder
rather than of three per-provider branches. What is locked in here:

1. Reasoning-effort pinning — the Opus 5.5 registry entries share one API model
   while councils use xhigh and release documents use high effort. Fable's
   retained high and medium entries and `gpt-6.1-sol-low` use the same pattern.
   The pin rides the gateway's top-level `reasoning_effort` field, so no call
   depends on a provider-specific response branch.
2. `supports_temperature: false` — the reject-set models (Fable 5.1 x2,
   Sonnet 5.5, Opus 5.5 x2, GPT-6 Astra, Sol, Sol-low and Luna) reject the
   `temperature` param; the builder must omit it for them and keep sending it
   for models that accept it (Haiku 4.5). NOTE: Sonnet 5.5 REJECTS
   temperature — a behavior change from the retired Sonnet 4.6, which
   accepted it.
3. One address for every text model — every `type: text` entry posts to the same
   `/chat/completions` URL under `Authorization: Bearer`. The per-endpoint
   `openai_endpoint` split is gone; a second URL appearing here would mean a
   second inference path survived the consolidation.
4. Per-seat serving-provider pinning — the three council seats carry
   `provider.only`, the control that keeps them on three distinct serving hosts
   (the response-integrity bound the decision records).

NOTE: the GPT-5.5 and GPT-5.5-pro identifiers that briefly existed on an
incoming branch were intentionally REMOVED as part of the GPT-5.6 baseline
switch — a deliberate compatibility break. They are never aliased to any
GPT-5.6 SKU and do not appear anywhere in this suite. The GPT-5.6 keys
(`gpt-5.6-sol`, `gpt-5.6-sol-low`, `gpt-5.6-terra`, `gpt-5.6-luna`) and
`gpt-image-2` were in turn REMOVED by the GPT-6 switch, the same deliberate
compatibility break: they are never aliased, and GPT-6 has no Terra. They
appear in this suite only as the removed keys `RemovedGpt56KeysTests` asserts
raise. `claude-opus-5` and `claude-sonnet-5` were removed the same way, with
the router roles nothing read; they appear only as the retired keys
`UnreadRoleDeletionTests` asserts are gone.

Run under uv (httpx required): uv run python3 -m unittest discover crux/scripts/tests
"""

import os
import sys
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

# Registry entries whose API rejects a non-default temperature. jev-1.13 also
# rejects one and is covered by its own suite.
TEMPERATURE_REJECT_SET = (
    'claude-fable-5.1', 'claude-fable-5.1-medium',
    'claude-sonnet-5.5', 'gpt-6.1-sol', 'gpt-6.1-sol-low',
    'gpt-6-luna', 'gpt-6-astra', 'claude-opus-5.5', 'claude-opus-5.5-xhigh',
)

# Serving-provider slugs read from each seat's OpenRouter endpoints data
# (Astra verified 2026-09-04). Three seats, three distinct hosts.
COUNCIL_SEAT_SERVING_PROVIDERS = {
    'gpt-6-astra': ['openai'],
    'claude-opus-5.5-xhigh': ['anthropic'],
    'gemini-3.1-pro-preview': ['google-ai-studio'],
}


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class LlmRouterRegistryTests(unittest.TestCase):
    """Config-level invariants — no network, no API keys."""

    @classmethod
    def setUpClass(cls):
        from crux.core import llm_caller
        cls.llm = llm_caller
        cls.cfg = llm_caller.load_router_config()

    def test_every_role_resolves_to_a_registered_model(self):
        registry = set(self.cfg['models'])
        for role, value in self.cfg['model_roles'].items():
            if role.startswith('_'):
                continue
            names = value if isinstance(value, list) else [value]
            for name in names:
                self.assertIn(name, registry, f"model_roles.{role} -> {name} not in registry")

    def test_fable_entries_share_sku_with_distinct_effort(self):
        # claude-fable-5.1 and claude-fable-5.1-medium are both ACTIVE (not
        # disabled) in the GPT-6 baseline lineup and share one api_string
        # but pin different reasoning efforts (high vs medium).
        high = self.llm.get_model_config('claude-fable-5.1')
        medium = self.llm.get_model_config('claude-fable-5.1-medium')
        self.assertEqual(high.api_string, 'anthropic/claude-fable-5.1')
        self.assertEqual(medium.api_string, 'anthropic/claude-fable-5.1')
        self.assertEqual(high.effort, 'high')
        self.assertEqual(medium.effort, 'medium')

    def test_sol_low_shares_sku_with_low_effort(self):
        # The fast-lane arbiter is Sol pinned to low effort (effort-alias, same
        # SKU) — the OpenAI analogue of the claude-fable-5.1 / -medium pattern.
        sol = self.llm.get_model_config('gpt-6.1-sol')
        sol_low = self.llm.get_model_config('gpt-6.1-sol-low')
        self.assertEqual(sol.api_string, 'openai/gpt-6.1-sol')
        self.assertEqual(sol_low.api_string, 'openai/gpt-6.1-sol')
        self.assertEqual(sol_low.effort, 'low')
        self.assertIsNone(sol.effort)

    def test_temperature_support_flags_match_live_verification(self):
        accepts = {'claude-haiku-4-5-20251001'}
        for name in TEMPERATURE_REJECT_SET:
            self.assertFalse(
                self.llm.get_model_config(name).supports_temperature,
                f"{name} must have supports_temperature: false (API rejects the param)")
        for name in accepts:
            self.assertTrue(self.llm.get_model_config(name).supports_temperature)

    def test_every_model_addresses_the_one_gateway(self):
        """Verbatim-slug pinning: every api_string is a namespaced OpenRouter id
        under one base_url. A bare slug here would be a model the gateway cannot
        resolve; a second base_url would be a surviving second inference path."""
        for name in self.cfg['models']:
            cfg = self.llm.get_model_config(name)
            with self.subTest(model=name):
                self.assertEqual(cfg.base_url, 'https://openrouter.ai/api/v1')
                self.assertIn('/', cfg.api_string,
                              f"{name}: api_string must be a namespaced OpenRouter id")

    def test_providers_table_is_exactly_openrouter(self):
        """ADR-0087 postcondition: one gateway means one providers entry.

        A second entry would be a surviving second inference path — the exact
        fragmentation the consolidation removed. Pinned as an equality, not a
        membership check, so ADDING a provider fails here rather than passing
        because openrouter is still present beside it.
        """
        self.assertEqual(list(self.cfg['providers']), ['openrouter'])

    def test_council_seats_pin_their_serving_providers(self):
        for name, expected in COUNCIL_SEAT_SERVING_PROVIDERS.items():
            with self.subTest(model=name):
                self.assertEqual(list(self.llm.get_model_config(name).serving_providers),
                                 expected)

    def test_council_seats_resolve_to_distinct_serving_providers(self):
        """The diversity control. Three seats collapsing onto one host would
        undo the vendor diversity the three-seat council exists to buy."""
        hosts = [tuple(self.llm.get_model_config(n).serving_providers)
                 for n in COUNCIL_SEAT_SERVING_PROVIDERS]
        self.assertEqual(len(set(hosts)), 3, f"seats share a serving provider: {hosts}")

    def test_council_weight_covers_weighted_seats(self):
        # Weighted council seats resolve against the GPT-6 baseline lineup:
        # anthropic_top -> claude-opus-5.5-xhigh (1.5), openai_top ->
        # gpt-6-astra (1.4, replacing the stale/removed gpt-5.5), google_top ->
        # gemini-3.1-pro-preview (1.3).
        from crux.council.council import _get_model_weight
        self.assertEqual(_get_model_weight('claude-opus-5.5-xhigh'), 1.5)
        self.assertEqual(_get_model_weight('gpt-6-astra'), 1.4)
        self.assertEqual(_get_model_weight('gemini-3.1-pro-preview'), 1.3)


# The five keys the GPT-6 switch removed, and the two the GPT-6.1 Sol switch
# removed. A deliberate compatibility break: none is aliased, and a caller
# naming one gets `ValueError: Unknown model`.
REMOVED_OPENAI_KEYS = (
    'gpt-5.6-sol', 'gpt-5.6-sol-low', 'gpt-5.6-terra', 'gpt-5.6-luna',
    'gpt-image-2', 'gpt-6-sol', 'gpt-6-sol-low',
)


def _role_names(value):
    return value if isinstance(value, list) else [value]


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class Gpt6RoleAndRegistryTests(unittest.TestCase):
    """The GPT-6 switch re-points roles and never renames a key.

    The default council's OpenAI member resolves to `gpt-6.1-sol`, because GPT-6
    has no Terra and no Terra workload moves to Luna.
    """

    @classmethod
    def setUpClass(cls):
        from crux.core import llm_caller
        cls.llm = llm_caller
        cls.cfg = llm_caller.load_router_config()

    # (a) the moved role slot
    def test_list_roles_carry_gpt_6_1_sol_first(self):
        expected = {
            'council_default': ['gpt-6.1-sol', 'gemini-3.1-pro-preview',
                                'claude-opus-5.5-xhigh'],
        }
        for role, models in expected.items():
            with self.subTest(role=role):
                self.assertEqual(list(self.llm.get_default_models(role)), models)

    def test_openai_top_stays_on_astra(self):
        self.assertEqual(self.llm.get_default_model('openai_top'), 'gpt-6-astra')

    # (b) nothing moves to Luna
    def test_no_role_resolves_to_gpt_6_luna(self):
        for role, value in self.cfg['model_roles'].items():
            if role.startswith('_'):
                continue
            with self.subTest(role=role):
                self.assertNotIn('gpt-6-luna', _role_names(value))

    # (c) the removed keys are gone, not aliased
    def test_removed_keys_raise_unknown_model(self):
        for key in REMOVED_OPENAI_KEYS:
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, 'Unknown model'):
                    self.llm.get_model_config(key)

    def test_no_role_names_a_removed_key(self):
        for role, value in self.cfg['model_roles'].items():
            if role.startswith('_'):
                continue
            for key in REMOVED_OPENAI_KEYS:
                with self.subTest(role=role, key=key):
                    self.assertNotIn(key, _role_names(value))

    # (d) every OpenAI entry names its own slug: no key redirects to another model
    def test_every_openai_entry_names_its_own_slug(self):
        openai_keys = [k for k, v in self.cfg['models'].items()
                       if v['api_string'].startswith('openai/')]
        self.assertIn('gpt-6.1-sol', openai_keys)  # the filter selects something
        for key in openai_keys:
            with self.subTest(key=key):
                self.assertEqual(self.cfg['models'][key]['api_string'],
                                 'openai/' + key.removesuffix('-low'))

    # (e) the new entries carry the verified metadata
    def _assert_entry(self, key, expected, absent=()):
        entry = self.llm.get_model_info(key)
        for field, value in expected.items():
            with self.subTest(key=key, field=field):
                self.assertEqual(entry.get(field), value)
        for field in absent:
            with self.subTest(key=key, absent=field):
                self.assertNotIn(field, entry)

    def test_gpt_6_1_sol_entry_metadata(self):
        self._assert_entry('gpt-6.1-sol', {
            'api_string': 'openai/gpt-6.1-sol',
            'serving_providers': ['openai'],
            'display_name': 'GPT-6.1 Sol',
            'supports_temperature': False,
            'type': 'text',
            'tier': 'frontier_max',
            'latency_profile': 'slow',
            'context_window': 1050000,
            'max_output_tokens': 128000,
            'cost': {'input_per_1m': 2.0, 'output_per_1m': 10.0,
                     'cached_input_per_1m': 0.1},
        }, absent=('effort',))
        cfg = self.llm.get_model_config('gpt-6.1-sol')
        self.assertEqual(cfg.serving_providers, ('openai',))
        self.assertIsNone(cfg.effort)

    def test_gpt_6_1_sol_low_entry_metadata(self):
        self._assert_entry('gpt-6.1-sol-low', {
            'api_string': 'openai/gpt-6.1-sol',
            'display_name': 'GPT-6.1 Sol (low effort)',
            'effort': 'low',
            'supports_temperature': False,
            'type': 'text',
            'tier': 'frontier_max',
            'context_window': 1050000,
            'max_output_tokens': 128000,
            'cost': {'input_per_1m': 2.0, 'output_per_1m': 10.0,
                     'cached_input_per_1m': 0.1},
        }, absent=('serving_providers',))

    def test_gpt_6_luna_entry_metadata(self):
        self._assert_entry('gpt-6-luna', {
            'api_string': 'openai/gpt-6-luna',
            'display_name': 'GPT-6 Luna',
            'supports_temperature': False,
            'type': 'text',
            'tier': 'fast',
            'context_window': 1050000,
            'max_output_tokens': 128000,
            'cost': {'input_per_1m': 0.1, 'output_per_1m': 0.5,
                     'cached_input_per_1m': 0.01},
        }, absent=('effort', 'serving_providers'))

    def test_gpt_image_2_5_sunburst_entry_metadata(self):
        self._assert_entry('gpt-image-2.5-sunburst', {
            'api_string': 'openai/gpt-image-2.5-sunburst',
            'display_name': 'GPT Image 2.5 Sunburst',
            'type': 'image_generation',
            'tier': 'image_gen',
            'supports_temperature': True,
            'context_window': 400000,
            'max_output_tokens': 360000,
            'cost': {'input_per_1m': 8.0, 'output_per_1m': 8.0,
                     'cached_input_per_1m': 2.0},
            'judge_eligible': False,
        }, absent=('output_formats', 'max_resolution', 'latency_profile',
                   'effort', 'serving_providers'))

    def test_no_entry_pins_reasoning_effort_none(self):
        # GPT-6 allows function calling on Chat Completions only at effort
        # `none`; crux sends no tools, so no entry may pin `none` to unlock it.
        for key, entry in self.cfg['models'].items():
            with self.subTest(key=key):
                self.assertNotEqual(entry.get('effort'), 'none')


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class OpusRoutingTests(unittest.TestCase):
    """Councils use Opus 5.5 at xhigh; release documents retain high effort."""

    @classmethod
    def setUpClass(cls):
        from crux.core import llm_caller
        cls.llm = llm_caller
        cls.cfg = llm_caller.load_router_config()

    def test_release_docs_role_resolves_to_sonnet_5_5(self):
        self.assertEqual(self.llm.get_default_model('release_docs'), 'claude-sonnet-5.5')

    def test_anthropic_top_routes_to_xhigh_opus(self):
        self.assertEqual(self.llm.get_default_model('anthropic_top'), 'claude-opus-5.5-xhigh')
        self.assertEqual(self.llm.get_default_model('council_arbiter'), 'claude-opus-5.5-xhigh')

    def test_sync_council_default_anthropic_seat_uses_xhigh_opus(self):
        self.assertEqual(
            list(self.llm.get_default_models('council_default')),
            ['gpt-6.1-sol', 'gemini-3.1-pro-preview', 'claude-opus-5.5-xhigh'],
        )

    def test_weighted_vote_follows_the_repointed_role(self):
        from crux.council.council import _get_model_weight, MODEL_WEIGHTS
        self.assertEqual(MODEL_WEIGHTS, {
            'anthropic_top': 1.5, 'google_top': 1.3, 'openai_top': 1.4,
        })
        self.assertEqual(_get_model_weight('claude-fable-5.1'), 1.0)
        self.assertEqual(_get_model_weight('claude-opus-5.5-xhigh'), 1.5)

    def test_async_text_and_visual_councils_use_xhigh_opus(self):
        from crux.council.async_council import AsyncCouncilConfig
        self.assertEqual(AsyncCouncilConfig().anthropic_model, 'claude-opus-5.5-xhigh')

    def test_no_medium_effort_opus_5_5_entry_exists(self):
        self.assertNotIn('claude-opus-5.5-medium', self.cfg['models'])

    def test_opus_5_5_registry_entry_fields(self):
        cfg = self.llm.get_model_config('claude-opus-5.5')
        self.assertEqual(cfg.api_string, 'anthropic/claude-opus-5.5')
        self.assertEqual(cfg.effort, 'high')
        self.assertEqual(list(cfg.serving_providers), ['anthropic'])
        self.assertFalse(cfg.supports_temperature)

    def test_xhigh_variant_shares_the_opus_sku_and_provider(self):
        cfg = self.llm.get_model_config('claude-opus-5.5-xhigh')
        self.assertEqual(cfg.api_string, 'anthropic/claude-opus-5.5')
        self.assertEqual(cfg.effort, 'xhigh')
        self.assertEqual(list(cfg.serving_providers), ['anthropic'])
        self.assertFalse(cfg.supports_temperature)

    def test_release_docs_sends_no_temperature(self):
        # generate-public-docs.py passes temperature=0.3; Sonnet 5.5 rejects a
        # non-default temperature, so the resolved entry must drop it.
        self.assertFalse(self.llm.get_model_config(self.llm.get_default_model('release_docs')).supports_temperature)


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class SonnetRoutingTests(unittest.TestCase):
    """claude-sonnet-5.5 is registered from the live OpenRouter catalog, and
    release_docs is the one role that names it. anthropic_balanced (and the call_claude_sonnet
    convenience helper, which resolves that role despite its name) resolve to
    claude-opus-5.5, the high-effort Opus 5.5 entry.

    Uses the same fake-HTTP-client pattern as `LlmRouterPayloadTests` (no
    network) so the convenience-helper assertion (4) can observe the emitted
    request without a live call.
    """

    def setUp(self):
        from crux.core import llm_caller
        self.llm = llm_caller
        self.cfg = llm_caller.load_router_config()
        self.captured = []
        captured = self.captured

        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {'choices': [{'finish_reason': 'stop',
                                     'message': {'content': 'ok'}}]}

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def post(self, url, headers=None, json=None, **kwargs):
                captured.append((url, headers or {}, json))
                return FakeResponse()

        self._real_client = llm_caller.httpx.Client
        llm_caller.httpx.Client = FakeClient
        self._env_backup = os.environ.get('OPENROUTER_API_KEY')
        os.environ['OPENROUTER_API_KEY'] = 'sk-or-test-dummy'

    def tearDown(self):
        self.llm.httpx.Client = self._real_client
        if self._env_backup is None:
            os.environ.pop('OPENROUTER_API_KEY', None)
        else:
            os.environ['OPENROUTER_API_KEY'] = self._env_backup

    # (2) registry entry: exact key set and values, so the omitted keys
    # (capabilities, tier, judge_eligible, judge_tier, can_judge,
    # routing_hints, latency_profile, effort, serving_providers,
    # data_collection) are proven absent, not merely unchecked.
    def test_sonnet_5_5_entry_key_set_and_values(self):
        entry = self.llm.get_model_info('claude-sonnet-5.5')
        self.assertEqual(
            set(entry.keys()),
            {'provider', 'api_string', 'display_name', '_note',
             'supports_temperature', 'type', 'context_window',
             'max_output_tokens', 'cost'})
        self.assertEqual(entry['provider'], 'openrouter')
        self.assertEqual(entry['api_string'], 'anthropic/claude-sonnet-5.5')
        self.assertEqual(entry['display_name'], 'Claude Sonnet 5.5')
        self.assertEqual(entry['type'], 'text')
        self.assertFalse(entry['supports_temperature'])
        self.assertEqual(entry['context_window'], 1000000)
        self.assertEqual(entry['max_output_tokens'], 128000)
        self.assertEqual(entry['cost'], {'input_per_1m': 2.0, 'output_per_1m': 10.0,
                                          'cached_input_per_1m': 0.2})

    # (3) built request. `claude-sonnet-5.5` is already in TEMPERATURE_REJECT_SET,
    # so `LlmRouterPayloadTests.test_reject_set_models_omit_temperature` and
    # `.test_accepting_model_still_sends_temperature` (haiku) are the paired
    # positive control proving the same builder DOES emit `temperature` for an
    # accepting model — the omission here is a real branch outcome, not a typo
    # that would pass regardless. Likewise `test_effort_pinned_entries_send_
    # reasoning_effort` is the positive control proving the builder emits
    # `reasoning_effort` when an entry pins one; this entry pins none, so its
    # absence is the branch's other side. `tools`/`tool_choice`/`top_p`/`top_k`
    # have no positive control anywhere in this suite because
    # `build_gateway_request` never constructs them for ANY model (confirmed by
    # reading the function) — the same architectural-absence shape already
    # accepted for `test_gpt_6_payloads_send_no_tool_fields`.
    def test_sonnet_5_5_built_request_omits_disallowed_fields(self):
        cfg = self.llm.get_model_config('claude-sonnet-5.5')
        # temperature is passed explicitly to prove the builder drops it even
        # when the caller asks for one — not merely because none was supplied.
        url, headers, payload = self.llm.build_gateway_request(
            cfg, 'hi', max_tokens=None, temperature=0.9)
        self.assertEqual(url, 'https://openrouter.ai/api/v1/chat/completions')
        self.assertEqual(payload['model'], 'anthropic/claude-sonnet-5.5')
        self.assertEqual(payload['max_tokens'], 128000)
        self.assertNotIn('temperature', payload)
        self.assertNotIn('reasoning_effort', payload)
        self.assertNotIn('tools', payload)
        self.assertNotIn('tool_choice', payload)
        self.assertNotIn('top_p', payload)
        self.assertNotIn('top_k', payload)
        self.assertEqual(payload['provider'], {'data_collection': 'deny'})
        self.assertNotIn('only', payload['provider'])

    # (4) anthropic_balanced moves to the high-effort Opus 5.5 entry, and
    # call_claude_sonnet follows it because it resolves that role by name.
    def test_anthropic_balanced_and_call_claude_sonnet_use_high_opus_5_5(self):
        self.assertEqual(self.llm.get_default_model('anthropic_balanced'), 'claude-opus-5.5')
        cfg = self.llm.get_model_config('claude-opus-5.5')
        self.assertEqual(cfg.effort, 'high')
        self.assertEqual(cfg.api_string, 'anthropic/claude-opus-5.5')
        self.captured.clear()
        self.llm.call_claude_sonnet('Question?')
        payload = self.captured[-1][2]
        self.assertEqual(payload['model'], 'anthropic/claude-opus-5.5')
        self.assertEqual(payload['reasoning_effort'], 'high')

    def test_role_named_anthropic_balanced_helper_matches_legacy_alias(self):
        self.captured.clear()
        self.llm.call_anthropic_balanced('Question?')
        balanced = self.captured[-1][2]
        self.llm.call_claude_sonnet('Question?')
        self.assertEqual(self.captured[-1][2], balanced)


# The twelve roles no crux code or shipped doc reads, deleted by owner
# directive on 2026-09-28. None is aliased to a surviving role.
DELETED_ROLES = (
    'anthropic_fast', 'openai_chat', 'council_code', 'code_review', 'vision',
    'think_shallow', 'think_medium', 'think_deep', 'plan', 'reflect',
    'recursive_improve', 'anthropic_council',
)

# Every surviving role and the model it resolves to. council_default is the
# one list role left.
KEPT_ROLES = {
    'google_top': 'gemini-3.1-pro-preview',
    'google_fast': 'gemini-3.5-flash',
    'anthropic_top': 'claude-opus-5.5-xhigh',
    'anthropic_balanced': 'claude-opus-5.5',
    'openai_top': 'gpt-6-astra',
    'council_default': ['gpt-6.1-sol', 'gemini-3.1-pro-preview', 'claude-opus-5.5-xhigh'],
    'council_arbiter': 'claude-opus-5.5-xhigh',
    'release_docs': 'claude-sonnet-5.5',
}

# The two registry keys retired with the unread roles. Neither is aliased; a
# caller naming one gets `ValueError: Unknown model`.
RETIRED_ANTHROPIC_KEYS = ('claude-opus-5', 'claude-sonnet-5')

# The weighted-vote weight of every default council member, sync and async,
# as it stood before the deletions. Deleting anthropic_council must move none.
COUNCIL_MEMBER_WEIGHTS = {
    'gpt-6.1-sol': 1.0,
    'gemini-3.1-pro-preview': 1.3,
    'claude-opus-5.5-xhigh': 1.5,
    'gpt-6-astra': 1.4,
}


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class UnreadRoleDeletionTests(unittest.TestCase):
    """model_roles carries only read roles, and the retired keys are gone."""

    @classmethod
    def setUpClass(cls):
        from crux.core import llm_caller
        cls.llm = llm_caller
        cls.cfg = llm_caller.load_router_config()

    # (a) every deleted role is unknown to both resolvers
    def test_deleted_roles_raise_unknown_role(self):
        for role in DELETED_ROLES:
            with self.subTest(role=role):
                with self.assertRaisesRegex(ValueError, 'Unknown model role'):
                    self.llm.get_default_model(role)
                with self.assertRaisesRegex(ValueError, 'Unknown model role'):
                    self.llm.get_default_models(role)

    def test_kept_roles_still_resolve(self):
        # POSITIVE CONTROL for (a): the same resolvers answer every kept role,
        # so the raise above is about the deleted names, not a broken lookup.
        for role, expected in KEPT_ROLES.items():
            with self.subTest(role=role):
                if isinstance(expected, list):
                    self.assertEqual(self.llm.get_default_models(role), expected)
                else:
                    self.assertEqual(self.llm.get_default_model(role), expected)

    # (e) the role set is exactly the kept set, and each resolves to a key
    def test_model_roles_hold_exactly_the_kept_roles(self):
        roles = {k for k in self.cfg['model_roles'] if not k.startswith('_')}
        self.assertEqual(roles, set(KEPT_ROLES))
        registry = set(self.cfg['models'])
        for role in roles:
            for name in _role_names(self.cfg['model_roles'][role]):
                with self.subTest(role=role, model=name):
                    self.assertIn(name, registry)

    def test_default_councils_seat_three_providers(self):
        from crux.council.async_council import AsyncCouncilConfig

        def vendor(key):
            return self.llm.get_model_config(key).api_string.split('/', 1)[0]

        sync_members = self.llm.get_default_models('council_default')
        self.assertEqual({vendor(k) for k in sync_members},
                         {'openai', 'google', 'anthropic'})
        config = AsyncCouncilConfig()
        async_seats = [config.openai_model, config.anthropic_model, config.gemini_model]
        self.assertEqual({vendor(k) for k in async_seats},
                         {'openai', 'google', 'anthropic'})

    # (c) the retired keys are gone from the registry listing
    def test_retired_keys_absent_from_list_available_models(self):
        available = self.llm.list_available_models()
        # POSITIVE CONTROL: the listing carries the surviving Anthropic keys,
        # so the absence below is not an empty or broken listing.
        self.assertIn('claude-opus-5.5', available)
        self.assertIn('claude-sonnet-5.5', available)
        for key in RETIRED_ANTHROPIC_KEYS:
            with self.subTest(key=key):
                self.assertNotIn(key, available)

    def test_retired_keys_raise_unknown_model(self):
        # POSITIVE CONTROL: a surviving key resolves through the same call.
        self.assertEqual(self.llm.get_model_config('claude-opus-5.5').api_string,
                         'anthropic/claude-opus-5.5')
        for key in RETIRED_ANTHROPIC_KEYS:
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, 'Unknown model'):
                    self.llm.get_model_config(key)

    def test_no_role_names_a_retired_key(self):
        referenced = set()
        for role, value in self.cfg['model_roles'].items():
            if not role.startswith('_'):
                referenced |= set(_role_names(value))
        # POSITIVE CONTROL: the scan finds a model the roles do name.
        self.assertIn('claude-opus-5.5-xhigh', referenced)
        for key in RETIRED_ANTHROPIC_KEYS:
            with self.subTest(key=key):
                self.assertNotIn(key, referenced)

    # (d) weights: the table loses anthropic_council and no member moves
    def test_model_weights_hold_exactly_the_three_top_roles(self):
        from crux.council.council import MODEL_WEIGHTS
        self.assertEqual(MODEL_WEIGHTS, {
            'anthropic_top': 1.5, 'google_top': 1.3, 'openai_top': 1.4,
        })

    def test_every_default_council_member_keeps_its_weight(self):
        from crux.council.async_council import AsyncCouncilConfig
        from crux.council.council import _get_model_weight
        config = AsyncCouncilConfig()
        members = list(self.llm.get_default_models('council_default')) + [
            config.openai_model, config.anthropic_model, config.gemini_model]
        self.assertEqual(set(members), set(COUNCIL_MEMBER_WEIGHTS))
        for model in members:
            with self.subTest(model=model):
                self.assertEqual(_get_model_weight(model), COUNCIL_MEMBER_WEIGHTS[model])

    # (f) the kept Fable entry is owner-parked with no role
    def test_fable_note_records_owner_parked_entry_with_no_role(self):
        # POSITIVE CONTROL: the entry survives the role deletion unchanged in
        # the fields a caller reads, so the note checks below read a live entry.
        entry = self.cfg['models']['claude-fable-5.1']
        self.assertEqual(entry['effort'], 'high')
        self.assertEqual(entry['api_string'], 'anthropic/claude-fable-5.1')
        note = entry['_note']
        self.assertNotIn('think_deep', note)
        self.assertIn('parked with no role by owner decision', note)


class RouterRoleReaderTests(unittest.TestCase):
    """Every `model_roles` key has a reader in `crux/` outside the tests.

    A role nothing reads is dead configuration. The scan parses every `.py`
    file under `crux/` except `crux/scripts/tests/` and recognises three
    reader forms:

    1. a call to `get_default_model` or `get_default_models`, by bare name or
       as an attribute, whose first argument is a string literal naming the role;
    2. a key of a module-level `MODEL_WEIGHTS` dict literal (the council
       weight table, whose keys are roles);
    3. a call to one of those two functions whose first argument is a
       module-level name bound to a string literal in the same file
       (`MODEL_ROLE = "google_fast"` in `transcribe-video.py`).

    Registry entries (`models`) are not roles and are addressed by key.
    `_comment` is a note, not a role. `release_docs` is exempt by name: only
    dev-repo release tooling (`tools/generate-public-docs.py`) reads it, the
    staged release copy does not ship that tool, and the release-docs role
    rule requires the role to exist. The scan reads nothing outside `crux/`,
    so it runs unchanged in the staged copy.
    """

    CONFIG_PATH = SCRIPTS_DIR / 'crux' / '_config' / 'llm_router_config.json'
    CRUX_ROOT = SCRIPTS_DIR.parent
    TESTS_DIR = SCRIPTS_DIR / 'tests'
    READER_FUNCTIONS = ('get_default_model', 'get_default_models')
    EXEMPT_ROLES = {
        'release_docs': (
            'read only by dev-repo release tooling, tools/generate-public-docs.py, '
            'which the staged release copy does not ship'
        ),
    }
    NOT_A_ROLE_KEYS = ('_comment',)

    # ---- the scan and the check, callable on injected inputs ----

    @classmethod
    def read_sources(cls):
        """Map each non-test `.py` path under `crux/` to its source text."""
        sources = {}
        for path in sorted(cls.CRUX_ROOT.rglob('*.py')):
            if cls.TESTS_DIR in path.parents:
                continue
            sources[str(path.relative_to(cls.CRUX_ROOT))] = path.read_text(encoding='utf-8')
        return sources

    @classmethod
    def scan_readers(cls, sources):
        """Return {role: [reader description, ...]} found in `sources`."""
        import ast
        readers = {}

        def note(role, where):
            readers.setdefault(role, []).append(where)

        for name, text in sources.items():
            tree = ast.parse(text, filename=name)
            constants = {}
            for node in tree.body:
                targets, value = [], None
                if isinstance(node, ast.Assign):
                    targets, value = node.targets, node.value
                elif isinstance(node, ast.AnnAssign) and node.value is not None:
                    targets, value = [node.target], node.value
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        constants[target.id] = value.value
                    if target.id == 'MODEL_WEIGHTS' and isinstance(value, ast.Dict):
                        for key in value.keys:
                            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                                note(key.value, f'{name}: MODEL_WEIGHTS key')
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                func = node.func
                called = func.id if isinstance(func, ast.Name) else (
                    func.attr if isinstance(func, ast.Attribute) else None)
                if called not in cls.READER_FUNCTIONS:
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    note(first.value, f'{name}:{node.lineno}: {called} literal')
                elif isinstance(first, ast.Name) and first.id in constants:
                    note(constants[first.id], f'{name}:{node.lineno}: {called} via {first.id}')
        return readers

    @classmethod
    def unread_roles(cls, cfg, readers, exempt):
        """Return the sorted roles of `cfg` with no reader and no exemption."""
        roles = [key for key in cfg['model_roles'] if key not in cls.NOT_A_ROLE_KEYS]
        return sorted(role for role in roles if role not in readers and role not in exempt)

    @classmethod
    def load_config(cls):
        import json
        return json.loads(cls.CONFIG_PATH.read_text(encoding='utf-8'))

    # ---- the gate on the real tree ----

    def test_every_role_has_a_reader_in_crux(self):
        cfg = self.load_config()
        readers = self.scan_readers(self.read_sources())
        roles = [k for k in cfg['model_roles'] if k not in self.NOT_A_ROLE_KEYS]
        # Vacuity guards: the gate measured something.
        self.assertGreaterEqual(len(roles), 1)
        self.assertGreaterEqual(sum(len(v) for v in readers.values()), 1)
        missing = self.unread_roles(cfg, readers, self.EXEMPT_ROLES)
        self.assertEqual(
            missing, [],
            f'model_roles with no reader in crux/ (roles checked {len(roles)}, '
            f'roles with a reader {len([r for r in roles if r in readers])}): {missing}')

    def test_exemptions_name_real_roles_and_a_reason(self):
        cfg = self.load_config()
        for role, reason in self.EXEMPT_ROLES.items():
            with self.subTest(role=role):
                self.assertIn(role, cfg['model_roles'])
                self.assertTrue(reason.strip())

    def test_registry_entry_is_a_key_and_not_a_role(self):
        cfg = self.load_config()
        self.assertIn('claude-sonnet-5.5', cfg['models'])
        self.assertNotIn('claude-sonnet-5.5', cfg['model_roles'])

    def test_comment_key_is_not_a_role(self):
        cfg = self.load_config()
        self.assertIn('_comment', cfg['model_roles'])
        cfg['model_roles']['_comment'] = 'note'
        self.assertNotIn('_comment', self.unread_roles(cfg, {}, {}))

    # ---- positive controls: the gate turns red on seeded faults ----

    def test_control_seeded_unread_role_is_red(self):
        cfg = self.load_config()
        cfg['model_roles']['seeded_unread_role'] = 'claude-sonnet-5.5'
        readers = self.scan_readers(self.read_sources())
        self.assertEqual(
            self.unread_roles(cfg, readers, self.EXEMPT_ROLES), ['seeded_unread_role'])

    def test_control_removed_reader_is_red(self):
        cfg = self.load_config()
        sources = self.read_sources()
        needle = 'get_default_model("anthropic_balanced")'
        holders = [n for n, t in sources.items() if needle in t]
        self.assertTrue(holders, 'fixture premise: a reader of anthropic_balanced exists')
        for name in holders:
            sources[name] = sources[name].replace(needle, 'None')
        readers = self.scan_readers(sources)
        self.assertEqual(
            self.unread_roles(cfg, readers, self.EXEMPT_ROLES), ['anthropic_balanced'])

    def test_control_release_docs_exemption_is_load_bearing(self):
        cfg = self.load_config()
        readers = self.scan_readers(self.read_sources())
        self.assertNotIn('release_docs', readers)
        self.assertEqual(self.unread_roles(cfg, readers, {}), ['release_docs'])

    def test_control_each_reader_form_is_recognised(self):
        sources = {
            'a.py': 'get_default_model("r_call")\nx.get_default_models("r_attr")\n',
            'b.py': 'MODEL_WEIGHTS = {"r_weight": 1.0}\n',
            'c.py': 'ROLE = "r_const"\nget_default_model(ROLE)\n',
        }
        self.assertEqual(
            sorted(self.scan_readers(sources)), ['r_attr', 'r_call', 'r_const', 'r_weight'])


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class LlmRouterPayloadTests(unittest.TestCase):
    """Request-payload shaping via a fake HTTP client — no network."""

    def setUp(self):
        from crux.core import llm_caller
        self.llm = llm_caller
        self.captured = []
        captured = self.captured

        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {'choices': [{'finish_reason': 'stop',
                                     'message': {'content': 'ok'}}]}

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def post(self, url, headers=None, json=None, **kwargs):
                captured.append((url, headers or {}, json))
                return FakeResponse()

        self._real_client = llm_caller.httpx.Client
        llm_caller.httpx.Client = FakeClient
        self._env_backup = os.environ.get('OPENROUTER_API_KEY')
        os.environ['OPENROUTER_API_KEY'] = 'sk-or-test-dummy'

    def tearDown(self):
        self.llm.httpx.Client = self._real_client
        if self._env_backup is None:
            os.environ.pop('OPENROUTER_API_KEY', None)
        else:
            os.environ['OPENROUTER_API_KEY'] = self._env_backup

    def _request(self, model):
        self.captured.clear()
        self._last_return = self.llm.call_model(model, 'hi', max_tokens=64)
        return self.captured[0]

    def _payload(self, model):
        return self._request(model)[2]

    # -- address + auth ------------------------------------------------------

    def test_every_model_posts_to_one_gateway_url(self):
        # Non-text entries are refused before any request is built; see
        # test_image_entries_are_refused_before_any_request.
        cfg = self.llm.load_router_config()
        urls = set()
        for model in self.llm.list_available_models():
            if cfg['models'][model].get('type') not in self.llm.TEXT_MODEL_TYPES:
                continue
            urls.add(self._request(model)[0])
        self.assertEqual(urls, {'https://openrouter.ai/api/v1/chat/completions'},
                         "every model must post to the one gateway endpoint")

    def test_auth_is_bearer_and_key_never_reaches_the_url(self):
        url, headers, _ = self._request('claude-opus-5.5')
        self.assertEqual(headers.get('Authorization'), 'Bearer sk-or-test-dummy')
        self.assertNotIn('sk-or-test-dummy', url)

    # -- reasoning effort ----------------------------------------------------

    def test_effort_pinned_entries_send_reasoning_effort(self):
        for model, effort in (('claude-fable-5.1', 'high'),
                              ('claude-fable-5.1-medium', 'medium'),
                              ('gpt-6.1-sol-low', 'low'),
                              ('claude-opus-5.5', 'high'),
                              ('claude-opus-5.5-xhigh', 'xhigh')):
            with self.subTest(model=model):
                payload = self._payload(model)
                self.assertEqual(payload.get('reasoning_effort'), effort)

    def test_unpinned_entries_omit_reasoning_effort(self):
        """Plain Sol and Sonnet 5.5 pin no effort — the server default stands,
        and an emitted field would silently override it."""
        for model in ('gpt-6.1-sol', 'claude-sonnet-5.5'):
            with self.subTest(model=model):
                self.assertNotIn('reasoning_effort', self._payload(model))

    def test_effort_aliases_send_the_same_model_id(self):
        self.assertEqual(self._payload('gpt-6.1-sol')['model'], 'openai/gpt-6.1-sol')
        self.assertEqual(self._payload('gpt-6.1-sol-low')['model'], 'openai/gpt-6.1-sol')

    # -- temperature ---------------------------------------------------------

    def test_reject_set_models_omit_temperature(self):
        for model in TEMPERATURE_REJECT_SET:
            with self.subTest(model=model):
                self.assertNotIn('temperature', self._payload(model))

    def test_accepting_model_still_sends_temperature(self):
        self.assertIn('temperature', self._payload('claude-haiku-4-5-20251001'))

    def test_opus_5_5_built_request_pins_high_effort_anthropic_no_temperature(self):
        payload = self._payload('claude-opus-5.5')
        self.assertEqual(payload.get('reasoning_effort'), 'high')
        self.assertNotIn('temperature', payload)

    def test_default_council_opus_request_pins_xhigh_and_anthropic_provider(self):
        payload = self._payload('claude-opus-5.5-xhigh')
        self.assertEqual(payload['model'], 'anthropic/claude-opus-5.5')
        self.assertEqual(payload['reasoning_effort'], 'xhigh')
        self.assertEqual(payload['provider'], {'only': ['anthropic'], 'data_collection': 'deny'})
        self.assertNotIn('temperature', payload)

    def test_sync_member_and_arbiter_emit_xhigh_opus_requests(self):
        from crux.council import council
        council.council_vote('Question?', tracer=mock.Mock())
        self.assertEqual(len(self.captured), 4)
        member_payload = self.captured[2][2]
        arbiter_payload = self.captured[3][2]
        for payload in (member_payload, arbiter_payload):
            self.assertEqual(payload['model'], 'anthropic/claude-opus-5.5')
            self.assertEqual(payload['reasoning_effort'], 'xhigh')
            self.assertEqual(payload['provider'],
                             {'only': ['anthropic'], 'data_collection': 'deny'})

    def test_opus_convenience_helper_emits_opus_5_5_request(self):
        self.llm.call_claude_opus('Question?')
        payload = self.captured[-1][2]
        self.assertEqual(payload['model'], 'anthropic/claude-opus-5.5')
        self.assertEqual(payload['reasoning_effort'], 'xhigh')

    # -- serving-provider pinning -------------------------------------------

    def test_council_seats_send_provider_only(self):
        for model, expected in COUNCIL_SEAT_SERVING_PROVIDERS.items():
            with self.subTest(model=model):
                provider = self._payload(model).get('provider')
                self.assertEqual(provider, {'only': expected, 'data_collection': 'deny'})

    def test_non_seat_models_send_no_host_pin_but_still_deny_collection(self):
        """A non-seat model has no serving-provider set, and that is the ONLY
        half it omits.

        This assertion used to read `assertNotIn('provider', ...)`, which pinned
        a payload carrying no retention control at all: `data_collection` lived
        inside the `provider` object that only a pinned entry emitted, so every
        unpinned model — most of the registry — routed with collection allowed.
        The host pin is per-seat; the denial is universal.
        """
        provider = self._payload('gemini-3.5-flash').get('provider')
        self.assertEqual(provider, {'data_collection': 'deny'})

    # -- GPT-6 payloads (f) and host/retention controls ---------------------

    def test_gpt_6_payloads_send_no_tool_fields(self):
        """GPT-6.1 Sol supports Chat Completions only without tool calling, and
        Luna allows function calling there only at reasoning effort `none`. The
        gateway sends no tools, so neither restriction reaches a call path; a
        tool field appearing here would break that."""
        for model in ('gpt-6.1-sol', 'gpt-6.1-sol-low', 'gpt-6-luna'):
            payload = self._payload(model)
            for field in ('tools', 'tool_choice', 'functions'):
                with self.subTest(model=model, field=field):
                    self.assertNotIn(field, payload)

    def test_gpt_6_effort_fields(self):
        self.assertNotIn('reasoning_effort', self._payload('gpt-6.1-sol'))
        self.assertNotIn('reasoning_effort', self._payload('gpt-6-luna'))
        self.assertEqual(self._payload('gpt-6.1-sol-low').get('reasoning_effort'), 'low')

    def test_gpt_6_1_sol_pins_the_openai_host_and_denies_collection(self):
        self.assertEqual(self._payload('gpt-6.1-sol').get('provider'),
                         {'only': ['openai'], 'data_collection': 'deny'})

    def test_other_gpt_6_entries_deny_collection_without_a_host_pin(self):
        # gpt-image-2.5-sunburst left this list: the text path now refuses it
        # before any request exists to carry a provider object.
        for model in ('gpt-6.1-sol-low', 'gpt-6-luna'):
            with self.subTest(model=model):
                self.assertEqual(self._payload(model).get('provider'),
                                 {'data_collection': 'deny'})

    # -- body shape + return -------------------------------------------------

    def test_messages_carry_system_and_user_roles(self):
        self.captured.clear()
        self.llm.call_model('claude-opus-5.5', 'hi', system='Be terse.', max_tokens=64)
        payload = self.captured[0][2]
        self.assertEqual(payload['messages'],
                         [{'role': 'system', 'content': 'Be terse.'},
                          {'role': 'user', 'content': 'hi'}])
        self.assertEqual(payload['max_tokens'], 64)

    def test_response_text_is_surfaced(self):
        self._request('gpt-6.1-sol')
        self.assertEqual(self._last_return, 'ok')

    # -- text-only call path refuses non-text entries ------------------------

    def _non_text_models(self):
        cfg = self.llm.load_router_config()
        return {k: v.get('type') for k, v in cfg['models'].items()
                if v.get('type') != 'text'}

    def test_image_entries_are_refused_before_any_request(self):
        """The text path returns only `message.content`, so an image entry
        either answers empty (image-only output) or loses its image. Every
        image entry must be refused by name and type, and nothing is posted."""
        image_models = {k: t for k, t in self._non_text_models().items()
                        if t and t.startswith('image_')}
        self.assertEqual(set(image_models), {
            'gpt-image-2.5-sunburst', 'gemini-3-pro-image-preview',
            'gemini-3.1-flash-image-preview', 'gemini-2.5-flash-image'})
        for model, model_type in image_models.items():
            with self.subTest(model=model):
                self.captured.clear()
                with self.assertRaises(self.llm.NotATextModelError) as ctx:
                    self.llm.call_model(model, 'hi', max_tokens=64)
                message = str(ctx.exception)
                self.assertIn(model, message)
                self.assertIn(model_type, message)
                self.assertIn('image generation has no crux caller', message)
                self.assertEqual(self.captured, [], 'a refused entry posted a request')

    def test_every_text_entry_still_builds_a_request(self):
        cfg = self.llm.load_router_config()
        text_models = [k for k, v in cfg['models'].items() if v.get('type') == 'text']
        self.assertTrue(text_models)
        for model in text_models:
            with self.subTest(model=model):
                url, _, payload = self._request(model)
                self.assertEqual(url, 'https://openrouter.ai/api/v1/chat/completions')
                self.assertEqual(self._last_return, 'ok')


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class GatewayTimeoutTests(unittest.TestCase):
    """`gateway_timeout_seconds` reads the registry override and is memoized.

    The scalar is cached like the model-roles map and cleared by the same
    `invalidate_model_cache`, so a config edit only takes effect after that
    clear. Each test clears the cache around its mocked config so it neither
    reads a value another test memoized nor leaves one behind.
    """

    def setUp(self):
        from crux.core import llm_caller
        self.llm = llm_caller
        self.llm.gateway_timeout_seconds.cache_clear()
        self.addCleanup(self.llm.gateway_timeout_seconds.cache_clear)

    def test_registry_override_is_read(self):
        with mock.patch.object(self.llm, 'load_router_config',
                               return_value={'request_timeout_seconds': 42}):
            self.llm.gateway_timeout_seconds.cache_clear()
            self.assertEqual(self.llm.gateway_timeout_seconds(), 42.0)

    def test_absent_override_falls_back_to_the_default(self):
        with mock.patch.object(self.llm, 'load_router_config', return_value={}):
            self.llm.gateway_timeout_seconds.cache_clear()
            self.assertEqual(self.llm.gateway_timeout_seconds(),
                             self.llm.DEFAULT_TIMEOUT_SECONDS)

    def test_cache_clear_invalidates_a_stale_value(self):
        """Without the clear, a config edit is invisible; with it, it lands."""
        with mock.patch.object(self.llm, 'load_router_config',
                               return_value={'request_timeout_seconds': 10}):
            self.llm.gateway_timeout_seconds.cache_clear()
            self.assertEqual(self.llm.gateway_timeout_seconds(), 10.0)
        with mock.patch.object(self.llm, 'load_router_config',
                               return_value={'request_timeout_seconds': 99}):
            # The memoized 10 persists until the cache is cleared.
            self.assertEqual(self.llm.gateway_timeout_seconds(), 10.0)
            self.llm.invalidate_model_cache()
            self.assertEqual(self.llm.gateway_timeout_seconds(), 99.0)


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class ParseGatewayResponseTests(unittest.TestCase):
    """`parse_gateway_response` degrades a malformed body, never crashes on it.

    A missing or non-object `choices[0]` is not a refusal and must not raise:
    the caller (transcribe-video's main()) catches ValueError/GatewayError, not
    AttributeError, so an uncaught `.get` on a non-dict would escape as a
    traceback instead of the reported empty completion.
    """

    def setUp(self):
        from crux.core import llm_caller
        self.llm = llm_caller

    def test_empty_choices_returns_empty_string(self):
        self.assertEqual(self.llm.parse_gateway_response({'choices': []}, 'm'), "")
        self.assertEqual(self.llm.parse_gateway_response({}, 'm'), "")

    def test_non_dict_choice_returns_empty_not_attributeerror(self):
        for body in ({'choices': ['oops']}, {'choices': [None]}, {'choices': [42]}):
            with self.subTest(body=body):
                self.assertEqual(self.llm.parse_gateway_response(body, 'm'), "")

    def test_a_well_formed_choice_still_surfaces_its_content(self):
        body = {'choices': [{'finish_reason': 'stop',
                             'message': {'content': 'hello'}}]}
        self.assertEqual(self.llm.parse_gateway_response(body, 'm'), 'hello')


if __name__ == '__main__':
    unittest.main()
