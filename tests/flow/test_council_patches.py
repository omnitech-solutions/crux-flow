"""The fork's changes to upstream's async council (`async_council.py`) and the in-flight run rule.

A run freezes the model configuration it will call (`flow.effective_policy.api.roles[*].config`). Upgrading the vendored router
(upstream 3.27 moved it 4.0.0 to 4.2.0 and added ModelConfig fields) must never strand such a run: it keeps what it froze, and
the council attempt records that the shipped router differs.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from test_records import PLUGIN, m, project, start  # noqa: F401  (project is a fixture)

from crux.core.llm_caller import CONFIG_PATH, ModelConfig
from crux.council.async_council import AsyncCouncil, AsyncCouncilConfig


def cfg(name: str) -> ModelConfig:
    return ModelConfig(api_string='vendor/'+name, display_name=name, context_window=1000, max_output_tokens=500, base_url='https://x.test/v1',
                       auth_header='Authorization', auth_prefix='Bearer ', name=name)


SEATS = {'openai': cfg('a'), 'anthropic': cfg('b'), 'gemini': cfg('c')}


def test_frozen_model_configurations_must_be_a_complete_valid_set():
    with pytest.raises(ValueError, match='invalid frozen council model configurations'):
        AsyncCouncilConfig(openai_model='a', anthropic_model='b', gemini_model='c', resolved_models={'openai': cfg('a')})
    with pytest.raises(ValueError, match='cannot seat a gate council'):
        AsyncCouncilConfig(gate=True, council_kind='plan', resolved_models=SEATS)


def test_a_moved_router_is_refused_when_a_digest_is_demanded():
    with pytest.raises(ValueError, match='API router changed since the run policy was frozen'):
        AsyncCouncilConfig(openai_model='a', anthropic_model='b', gemini_model='c', expected_router_digest='0'*64)


@pytest.mark.parametrize('field', ['overall_timeout_seconds', 'overall_deadline_epoch'])
@pytest.mark.parametrize('bad', [0, -1, float('inf'), True, 'x'])
def test_overall_budgets_must_be_positive_finite_numbers(field, bad):
    with pytest.raises(ValueError):
        AsyncCouncilConfig(openai_model='a', anthropic_model='b', gemini_model='c', **{field: bad})


def test_the_seat_uses_its_frozen_configuration():
    council = AsyncCouncil.__new__(AsyncCouncil)
    council.config = AsyncCouncilConfig(openai_model='a', anthropic_model='b', gemini_model='c', resolved_models=SEATS)
    assert council._config_for_seat('anthropic') is SEATS['anthropic']


def test_the_overall_deadline_is_consumed_by_a_retry_not_reset():
    council = AsyncCouncil.__new__(AsyncCouncil)
    council._gate = False
    council.config = AsyncCouncilConfig(openai_model='a', anthropic_model='b', gemini_model='c', overall_timeout_seconds=0.2, timeout_seconds=30)
    calls = []

    async def stuck(attempt):
        calls.append(1)
        await asyncio.sleep(30)

    seat = asyncio.run(council._run_seat(stuck))
    assert isinstance(seat.error, asyncio.TimeoutError) and len(calls) == 1      # the retry would have outlived the whole-call budget


def frozen_old_run(project, router_digest: str) -> Path:
    """A thorough run whose frozen API roles were written by the previous vendored upstream: no served-model fields, older router digest."""
    path = Path(start(project, mode='thorough')['run']); run = yaml.safe_load(path.read_text())
    old = {k: v for k, v in asdict(cfg('a')).items() if k not in {'accepted_served_models', 'accepted_served_providers'}}
    roles = {r: {'model': 'vendor/'+n, 'config': {**old, 'name': n, 'api_string': 'vendor/'+n}} for r, n in (('openai_top', 'a'), ('anthropic_top', 'b'), ('google_top', 'c'))}
    flow = run['flow']; flow['effective_policy']['api'] = {'roles': roles, 'base_registry_digest': router_digest, 'observed_at_runtime': 'unobserved'}
    flow['policy_digest'] = m('common').identity(flow['effective_policy'])
    path.write_text(yaml.safe_dump(run, sort_keys=False))
    return path


def test_a_run_frozen_before_the_router_moved_still_seats_its_council(project):
    old_digest = 'ab'*32
    path = frozen_old_run(project, old_digest)
    config = m('api_calls').council_config(path, invocation='review', justification='material disagreement on the contract', authorized=True)
    assert config.expected_router_digest is None                                  # not refused: the run keeps what it froze
    assert set(config.resolved_models) == {'openai', 'anthropic', 'gemini'}
    assert config.resolved_models['openai'].accepted_served_models is None        # fields added after the freeze take their defaults
    note = [e for e in m('records').load(path)['flow']['events'] if e.get('kind') == 'council'][-1]['justification']
    assert 'differs from shipped router' in note and 'seated from the configuration this run froze' in note


def test_a_run_frozen_against_the_current_router_demands_it_unchanged(project):
    import hashlib
    current = hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest()
    path = frozen_old_run(project, current)
    config = m('api_calls').council_config(path, invocation='review', justification='material disagreement on the contract', authorized=True)
    assert config.expected_router_digest == current
