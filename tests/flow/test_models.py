from __future__ import annotations

import copy
import importlib
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[2]; PLUGIN=ROOT/'crux'


def api():
    try: return importlib.import_module('crux.flow.models')
    except ModuleNotFoundError: pytest.fail('model maintenance must use shared resolution and transactions')


def discovery():
    return api().normalize('openrouter',{'data':[{'id':'z-ai/test-model','architecture':{'input_modalities':['text'],'output_modalities':['text']},'supported_parameters':['tools','reasoning'],'context_length':128000,'top_provider':{'max_completion_tokens':8192},'pricing':{'prompt':'0.000001','completion':'0.000002'}}]},source='https://openrouter.ai/api/v1/models')


def change(): return {'host':'omp','kind':'level','name':'standard','model':'z-ai/test-model'}


def propose(tmp_path,**kwargs):
    return api().propose(PLUGIN,tmp_path,tmp_path/'home',scope='project',changes=[change()],discovery=discovery(),required_capabilities=['text-input','text-output','tools'],**kwargs)


def test_discovery_price_units_and_unknown_are_explicit():
    data=discovery(); row=data['models']['z-ai/test-model']
    assert row['pricing']['input']=='1.000000' and row['pricing']['output']=='2.000000'
    assert row['pricing']['unit']=='USD per million tokens'
    assert row['entitlement']=='unobserved'
    bad=api().normalize('openrouter',{'data':[{'id':'z-ai/unknown','pricing':{'prompt':'-1'}}]},source='https://openrouter.ai/api/v1/models')
    assert bad['models']['z-ai/unknown']['pricing']['input'] is None


def test_refresh_proposal_is_readonly_and_lists_actual_mode_differences(tmp_path):
    p=propose(tmp_path)
    assert list(tmp_path.rglob('*'))==[]
    assert p['changes'][0]['host']=='omp' and p['effective_diff']
    assert not any(x['mode']=='upstream' for x in p['effective_diff'])


def test_apply_materializes_managed_roles_and_rollback_restores(tmp_path):
    p=propose(tmp_path)
    before=(PLUGIN/'catalog/models.yml').read_bytes()
    result=api().apply(PLUGIN,tmp_path,tmp_path/'home',p,authorized=True,current_discovery=discovery())
    binding=tmp_path/'.crux-flow/model-bindings.json'
    assert binding.is_file()
    role=tmp_path/'.omp/agents/crux-flow-developer.md'
    assert role.is_file() and 'openrouter/z-ai/test-model' in role.read_text()
    assert result['runtime_loaded']=='unobserved'
    assert (PLUGIN/'catalog/models.yml').read_bytes()==before
    api().rollback(tmp_path,result['transaction_id'])
    assert not binding.exists() and not role.exists()


def test_stale_config_and_modified_proposal_refuse_before_writes(tmp_path):
    p=propose(tmp_path)
    (tmp_path/'.crux-flow.yml').write_text('config_version: "1"\nmode: balanced\n')
    with pytest.raises(ValueError): api().apply(PLUGIN,tmp_path,tmp_path/'home',p,authorized=True,current_discovery=discovery())
    (tmp_path/'.crux-flow.yml').unlink()
    p['changes'][0]['model']='z-ai/other'
    with pytest.raises(ValueError): api().apply(PLUGIN,tmp_path,tmp_path/'home',p,authorized=True,current_discovery=discovery())
    assert not (tmp_path/'.crux-flow/model-bindings.json').exists()


def test_explicit_pin_survives_mode_tier_refresh(tmp_path):
    (tmp_path/'.crux-flow.yml').write_text('config_version: "1"\nroles:\n  developer:\n    omp:\n      model: openrouter/z-ai/user-pin\n')
    p=propose(tmp_path)
    api().apply(PLUGIN,tmp_path,tmp_path/'home',p,authorized=True,current_discovery=discovery())
    assert 'openrouter/z-ai/user-pin' in (tmp_path/'.omp/agents/crux-flow-developer.md').read_text()


def test_removed_model_and_capability_loss_refuse(tmp_path):
    p=propose(tmp_path)
    gone=api().normalize('openrouter',{'data':[]},source='https://openrouter.ai/api/v1/models')
    with pytest.raises(ValueError): api().apply(PLUGIN,tmp_path,tmp_path/'home',p,authorized=True,current_discovery=gone)
    p=discovery(); p['models']['z-ai/test-model']['capabilities']=[]
    with pytest.raises(ValueError): api().propose(PLUGIN,tmp_path,tmp_path/'home',scope='project',changes=[change()],discovery=p,required_capabilities=['tools'])


def test_generated_file_conflict_prevents_binding_activation(tmp_path):
    path=tmp_path/'.omp/agents/crux-flow-developer.md'; path.parent.mkdir(parents=True); path.write_text('foreign')
    p=propose(tmp_path)
    with pytest.raises(ValueError): api().apply(PLUGIN,tmp_path,tmp_path/'home',p,authorized=True,current_discovery=discovery())
    assert path.read_text()=='foreign' and not (tmp_path/'.crux-flow/model-bindings.json').exists()


def test_unattended_exact_approval_cost_and_scope(tmp_path):
    p=propose(tmp_path)
    allow={'enabled':True,'scope':str(tmp_path),'approved_model_ids':['z-ai/test-model'],'vendors':['z-ai'],'required_capabilities':['tools'],'max_input_usd_per_million':'2','max_output_usd_per_million':'3','pin_behavior':'preserve','data_retention_approval':'exact-approved-models'}
    assert api().authorize_unattended(p,allow,tmp_path) is True
    for field,value in [('enabled',False),('scope','/other'),('approved_model_ids',[]),('max_input_usd_per_million','0'),('data_retention_approval','unknown')]:
        denied=dict(allow); denied[field]=value
        with pytest.raises(ValueError): api().authorize_unattended(p,denied,tmp_path)


def test_native_ids_do_not_need_api_registry_identity(tmp_path):
    d=api().normalize('codex',{'models':[{'slug':'native-new','supported_reasoning_levels':[{'effort':'medium'}]}]},source='installed:codex-debug-models-bundled',host_version='0.160.0')
    p=api().propose(PLUGIN,tmp_path,tmp_path/'home',scope='project',changes=[{'host':'codex','kind':'level','name':'standard','model':'native-new','effort':'medium'}],discovery=d)
    assert any(row['after']['model']=='native-new' for row in p['effective_diff'])
    with pytest.raises(ValueError):
        api().propose(PLUGIN,tmp_path,tmp_path/'home',scope='project',changes=[{'host':'codex','kind':'level','name':'standard','model':'native-new','effort':'ultra'}],discovery=d)


def test_rollback_refuses_user_edits(tmp_path):
    result=api().apply(PLUGIN,tmp_path,tmp_path/'home',propose(tmp_path),authorized=True,current_discovery=discovery())
    role=tmp_path/'.omp/agents/crux-flow-developer.md'; role.write_text('edited')
    with pytest.raises(ValueError): api().rollback(tmp_path,result['transaction_id'])
    assert role.read_text()=='edited'


def test_claude_document_ids_are_exact_not_fabricated():
    d=api().normalize('claude',{'markdown':'## Models\n| Claude API ID | `claude-example-1` |\n| Context window | 128K tokens |\n| Max output | 8K tokens |'},source='https://platform.claude.com/docs/en/models/overview.md',host_version='2.1.288')
    assert 'claude-example-1' in d['models']
    assert d['models']['claude-example-1']['supported_efforts']==[]


def test_no_authorization_no_activation(tmp_path):
    with pytest.raises(ValueError): api().apply(PLUGIN,tmp_path,tmp_path/'home',propose(tmp_path),authorized=False,current_discovery=discovery())
    assert list(tmp_path.rglob('*'))==[]
