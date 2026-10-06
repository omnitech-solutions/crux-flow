from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from crux.core.llm_caller import ModelConfig, call_gateway
from crux.council.async_council import AsyncCouncil, AsyncCouncilConfig

from .common import FlowError, mapping, text
from . import records


def _selected(run: dict,role: str) -> ModelConfig:
    row=run['flow']['effective_policy']['api']['roles'].get(role)
    if not isinstance(row,dict): raise FlowError('select one configured API role, not a council roster')
    value=mapping(row['config'],allowed={f.name for f in fields(ModelConfig)},required={f.name for f in fields(ModelConfig)})
    cfg=ModelConfig(**value)
    if cfg.api_string!=row['model'] or cfg.data_collection not in {'allow','deny'}: raise FlowError('frozen API binding is inconsistent')
    return cfg


def call_role(path: Path,role: str,prompt: str,*,authorized: bool,gateway=call_gateway) -> str:
    if authorized is not True: raise FlowError('API execution requires explicit usage authorization')
    run=records.load(path); records._active(run); records._source(run)
    cfg=_selected(run,role); left=records.view(run)['remaining_seconds']
    timeout=min(600.0,left) if left is not None else 600.0
    records.attempt(path,kind='generation',invocation='api-role:'+text(role,maximum=100),justification='authorized configured API helper')
    return gateway(cfg,prompt,timeout=max(0.001,timeout),max_tokens=min(cfg.max_output_tokens,32000),temperature=None)


def council_config(path: Path,*,invocation: str,justification: str,authorized: bool) -> AsyncCouncilConfig:
    if authorized is not True: raise FlowError('council execution requires explicit usage authorization')
    text(justification)
    run=records.load(path); records._active(run); records._source(run)
    binding=run['flow']['effective_policy']['api']
    seats={'openai':'openai_top','anthropic':'anthropic_top','gemini':'google_top'}
    selected={seat:_selected(run,role) for seat,role in seats.items()}
    remaining=records.view(run)['remaining_seconds']
    deadline=(run['flow']['started_epoch_ms']/1000+run['flow']['effective_policy']['budget_minutes']*60)
    config=AsyncCouncilConfig(openai_model=selected['openai'].name,anthropic_model=selected['anthropic'].name,
          gemini_model=selected['gemini'].name,resolved_models=selected,expected_router_digest=binding['base_registry_digest'],
          overall_timeout_seconds=remaining,overall_deadline_epoch=deadline,timeout_seconds=min(600.0,remaining),
          max_tokens=min(32000,*(cfg.max_output_tokens for cfg in selected.values())),max_retries=1)
    records.attempt(path,kind='council',invocation=invocation,justification=justification)
    return config


def deliberate(path: Path,prompt: str,*,invocation: str,justification: str,authorized: bool):
    config=council_config(path,invocation=invocation,justification=justification,authorized=authorized)
    return AsyncCouncil(config).deliberate_sync(prompt)
