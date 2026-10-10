from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import models_catalog

from .common import FlowError, HOSTS, canonical, decode, identity, integer, mapping, model_id, digest, text
from .managed import read_file

CONFIG = '.crux-flow.yml'
FIELDS = {'config_version','mode','models','budget_minutes','roles','technology'}
PREFERENCES = FIELDS - {'technology'}   # technology guidance is project data, never part of the effective policy
RISKS = ('read-only','writes','destructive','external')
EFFORTS = {'claude': {'low','medium','high','xhigh','max'}, 'codex': {'low','medium','high','xhigh','max','ultra'}}


def read_config(path: Path) -> dict[str, Any]:
    raw = read_file(path.parent, path.name)
    if raw is None:
        return {}
    data = mapping(decode(raw,maximum=100_000),allowed=FIELDS,required={'config_version'})
    if data['config_version'] != '1':
        raise FlowError('config_version must be the string "1"')
    if 'mode' in data and data['mode'] not in {'aggressive','rapid','balanced','thorough','upstream'}:
        raise FlowError('unknown execution mode')
    if 'models' in data and data['models'] not in {'economical','balanced','strong','upstream'}:
        raise FlowError('unknown model profile')
    if 'budget_minutes' in data:
        integer(data['budget_minutes'],low=1,high=1440)
    for role, pins in mapping(data.get('roles',{})).items():
        mapping(pins)
        if set(pins) & set(HOSTS):
            if set(pins) - set(HOSTS):
                raise FlowError('mixed host-scoped and unscoped model pin')
            for host,pin in pins.items():
                _pin(pin,host)
        else:
            _pin(pins,None)
    if 'technology' in data:
        _technology(data['technology'])
    return data


def _names(value: Any, label: str) -> list[str]:
    if not isinstance(value,list) or any(not isinstance(item,str) or not item.strip() for item in value) or len(set(value))!=len(value):
        raise FlowError(f'technology {label} must be a list of distinct non-empty strings')
    return value


def _technology(value: Any) -> dict:
    """Shape only. Whether an id is in the catalog or a path exists is the technology check's finding, not a parse error."""
    from .managed import relative
    section=mapping(value,allowed={'router','owner','map','preload','exclude','never','layers'},required={'owner','layers'})
    if section['owner'] not in {'flow','project'}: raise FlowError('technology owner must be flow or project')
    if re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',section.get('router','technology-references')) is None or len(section.get('router',''))>64:
        raise FlowError('invalid technology router name')
    if 'map' in section: relative(text(section['map'],maximum=400,label='technology map path'))
    _names(section.get('preload',[]),'preload'); _names(section.get('never',[]),'never')
    for reason in mapping(section.get('exclude',{})).values(): text(reason,maximum=400,label='technology exclusion reason')
    if not isinstance(section['layers'],list) or not section['layers']: raise FlowError('technology needs at least one layer')
    seen=set()
    for layer in section['layers']:
        mapping(layer,allowed={'id','paths','technologies','read','rules','commands','never'},required={'id','paths'})
        if not isinstance(layer['id'],str) or re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',layer['id']) is None or layer['id'] in seen:
            raise FlowError('technology layer ids must be distinct lowercase names')
        seen.add(layer['id'])
        if not _names(layer['paths'],'layer paths'): raise FlowError('a technology layer needs at least one path')
        _names(layer.get('technologies',[]),'layer technologies'); _names(layer.get('never',[]),'layer never-list')
        read=mapping(layer.get('read',{}),allowed={'project','captures'})
        for name in [*layer['paths'],*_names(read.get('project',[]),'project references'),*_names(read.get('captures',[]),'captures'),*_names(layer.get('rules',[]),'rules')]:
            relative(name)
        commands=layer.get('commands',[])
        if not isinstance(commands,list): raise FlowError('technology commands must be a list')
        ids=set()
        for command in commands:
            mapping(command,allowed={'id','cwd','argv','risk','requires'},required={'id','cwd','argv','risk'})
            if not isinstance(command['id'],str) or re.fullmatch(r'[a-z0-9]+(?:[:-][a-z0-9]+)*',command['id']) is None or command['id'] in ids:
                raise FlowError('technology command ids must be distinct lowercase names within a layer')
            ids.add(command['id'])
            if command['cwd']!='.': relative(text(command['cwd'],maximum=400,label='command working directory'))
            if not isinstance(command['argv'],list) or not command['argv'] or any(not isinstance(part,str) or not part or '\x00' in part for part in command['argv']):
                raise FlowError('a technology command needs an argument vector of non-empty strings')
            if command['risk'] not in RISKS: raise FlowError('unknown technology command risk')
            _names(command.get('requires',[]),'command prerequisites')
    return section


def _pin(value: Any, host: str | None) -> dict:
    row=mapping(value,allowed={'model','effort'})
    if 'model' in row:
        model_id(row['model'])
    if 'effort' in row:
        if host in ('opencode','omp') or row['effort'] not in EFFORTS.get(host or 'codex',set()):
            raise FlowError('unsupported reasoning effort')
    return row


def load_definition(plugin: Path) -> dict:
    raw=read_file(plugin,'catalog/flow-policy.json')
    if raw is None:
        raise FlowError('fork policy data missing')
    data=mapping(decode(raw),allowed={'schema_version','identity','aliases','modes'},required={'schema_version','identity','aliases','modes'})
    modes = mapping(data['modes'])
    if type(data['schema_version']) is not int or data['schema_version'] != 1 or set(modes) != {'aggressive','balanced','thorough','upstream'}:
        raise FlowError('invalid shipped execution policies')
    if data['aliases'] != {'rapid':'aggressive'}:
        raise FlowError('unsupported or cyclic mode alias')
    identity_fields = {'distribution','marketplace','managed_prefix','version','policy_revision','projection_version',
                       'upstream_repository','upstream_commit','upstream_version'}
    metadata = mapping(data['identity'], allowed=identity_fields, required=identity_fields)
    if any(not isinstance(value, str) or not value for value in metadata.values()):
        raise FlowError('invalid release identity')
    if re.fullmatch(r'[0-9a-f]{40}', metadata['upstream_commit']) is None:
        raise FlowError('invalid upstream baseline identity')
    workflow_fields = {'elapsed_minutes','delegates','reviewers','repair_cycles','council_rounds',
                       'model_profile','verification','documentation','executor'}
    interaction_fields = {'progress','narrate_intent','ask_before_routine_decisions','repeat_known_context',
                          'report_speculation','partial_completion_returns_control'}
    for name, row in modes.items():
        fields = workflow_fields | ({'interaction'} if name != 'upstream' else set())
        mapping(row, allowed=fields, required=fields)
        if row['model_profile'] not in {'economical','balanced','strong','upstream'}:
            raise FlowError('unsupported model profile')
        for key in ('verification','documentation','executor'):
            if not isinstance(row[key], str) or not row[key].strip():
                raise FlowError('invalid workflow policy description')
        for key in ('elapsed_minutes','delegates','reviewers','repair_cycles','council_rounds'):
            if name == 'upstream':
                if row[key] is not None:
                    raise FlowError('upstream defaults must preserve the selected upstream workflow')
            else:
                integer(row[key], low=1 if key == 'elapsed_minutes' else 0, high=1440)
        if name != 'upstream':
            interaction = mapping(row['interaction'], allowed=interaction_fields, required=interaction_fields)
            if interaction['progress'] not in {'milestones','detailed-checkpoints'}:
                raise FlowError('invalid progress policy')
            if any(type(interaction[key]) is not bool for key in interaction_fields - {'progress'}):
                raise FlowError('invalid interaction policy')
    return data


def load_bindings(plugin: Path, repo: Path, home: Path, *, supplied: dict | None = None, binding_scope: str = "invocation") -> dict:
    merged: dict = {'schema_version':1,'native':{},'api':{}}
    if binding_scope not in {'source','project','user','invocation'}: raise FlowError('invalid binding scope')
    sources = [('source',plugin,'catalog/flow-bindings.json'), ('user',home,'.crux-flow/model-bindings.json'), ('project',repo,'.crux-flow/model-bindings.json')]
    for scope,root,name in sources:
        if supplied is not None and scope==binding_scope:
            _merge_bindings(merged,supplied)
            continue
        raw=read_file(root,name)
        if raw is not None: _merge_bindings(merged,mapping(decode(raw,maximum=1_000_000)))
    if supplied is not None and binding_scope=='invocation': _merge_bindings(merged,supplied)
    return merged


def _merge_bindings(target: dict, source: dict) -> None:
    mapping(source,allowed={'schema_version','native','api'},required={'schema_version','native','api'})
    if source['schema_version'] != 1:
        raise FlowError('invalid fork assignment version')
    for host, groups in mapping(source['native']).items():
        if host not in HOSTS:
            raise FlowError('unknown assignment host')
        mapping(groups,allowed={'levels','roles'})
        destination=target['native'].setdefault(host,{'levels':{},'roles':{}})
        for group, entries in groups.items():
            for key, assignment in mapping(entries).items():
                destination[group][key]=copy.deepcopy(_pin(assignment,host))
    for role, value in mapping(source['api']).items():
        values=value if isinstance(value,list) else [value]
        if not values:
            raise FlowError('empty API role assignment')
        for row in values:
            mapping(row,allowed={'model','effort'},required={'model'})
            model_id(row['model'])
            if '/' not in row['model']:
                raise FlowError('API model requires an exact provider/model identifier')
            if row.get('effort') is not None and row['effort'] not in EFFORTS['codex']:
                raise FlowError('unsupported API effort')
        target['api'][role]=copy.deepcopy(value)


def _api(plugin: Path, overlay: dict, upstream: bool) -> dict:
    raw=read_file(plugin,'scripts/crux/_config/llm_router_config.json')
    if raw is None:
        raise FlowError('upstream API registry missing')
    registry=mapping(decode(raw),required={'models','model_roles'})
    from dataclasses import asdict
    from crux.core.llm_caller import get_model_config
    result={}
    def entry(key, effort=None):
        cfg=get_model_config(key,registry=registry)
        if cfg.model_type!='text': raise FlowError('API role requires a text-capable canonical model')
        if effort is not None: cfg.effort=effort
        return {'model':model_id(cfg.api_string),'effort':cfg.effort,'provider':registry['models'][key]['provider'],
                'registry_key':key,'config':asdict(cfg)}
    for role,keys in registry['model_roles'].items():
        if role.startswith('_'): continue
        resolved=[entry(k) for k in (keys if isinstance(keys,list) else [keys])]
        result[role]=resolved if isinstance(keys,list) else resolved[0]
    if not upstream:
        if overlay['api'].keys()-result.keys(): raise FlowError('unknown API role in fork override')
        for role,value in overlay['api'].items():
            changed=[]
            original=result[role] if isinstance(result[role],list) else [result[role]]
            for row in (value if isinstance(value,list) else [value]):
                keys=[k for k,v in registry['models'].items() if v['api_string']==row['model'] and v.get('type')=='text']
                if not keys: raise FlowError('API identity is not in the canonical router; register its verified facts there before selecting it')
                key=next((k for k in keys if registry['models'][k].get('effort')==row.get('effort')),keys[0])
                selected=entry(key,row.get('effort'))
                if selected['config']['data_collection']=='allow' and any(x['config']['data_collection']=='deny' for x in original):
                    raise FlowError('model assignment cannot widen the existing data-retention restriction')
                changed.append(selected)
            result[role]=changed if isinstance(value,list) else changed[0]
    return {'roles':result,'base_registry_digest':digest(raw),'observed_at_runtime':'unobserved'}


def resolve(plugin: Path, repo: Path, home: Path, host: str, invocation: dict | None = None,
            *, bindings: dict | None = None, catalog: models_catalog.ModelsCatalog | None = None, binding_scope: str = "invocation", preferences: dict | None=None) -> dict:
    if host not in HOSTS:
        raise FlowError('unknown coding host')
    definition=load_definition(plugin)
    if preferences is not None:
        mapping(preferences,allowed={'personal','project','invocation','bindings'},required={'personal','project','invocation','bindings'})
    personal=copy.deepcopy(preferences['personal']) if preferences else read_config(home/CONFIG)
    project=copy.deepcopy(preferences['project']) if preferences else read_config(repo/CONFIG)
    if 'technology' in personal: raise FlowError('technology guidance belongs to a project configuration')
    project.pop('technology',None)   # kept out of the effective policy, so its edits never touch a frozen run
    explicit=copy.deepcopy(invocation if invocation is not None else preferences['invocation'] if preferences else {})
    mapping(explicit,allowed=PREFERENCES-{'config_version'})
    sources={}
    def choose(key: str, fallback: Any) -> Any:
        for label,config in [('invocation',explicit),('project',project),('personal',personal)]:
            if key in config:
                sources[key]=label
                return config[key]
        sources[key]='shipped'
        return fallback
    requested=choose('mode','aggressive')
    mode=definition['aliases'].get(requested,requested)
    if mode not in definition['modes']:
        raise FlowError('unknown execution mode')
    workflow=copy.deepcopy(definition['modes'][mode])
    profile=choose('models',workflow['model_profile'])
    if profile not in {'economical','balanced','strong','upstream'}:
        raise FlowError('unknown model profile')
    if mode=='upstream' and sources['models']!='invocation':
        profile='upstream'; sources['models']='upstream-preserved'
    budget=choose('budget_minutes',workflow['elapsed_minutes'])
    if budget is not None:
        integer(budget,low=1,high=1440)
    catalog=catalog or models_catalog.load(plugin/'catalog/models.yml',plugin/'agents')
    overlay=copy.deepcopy(preferences['bindings']) if preferences else load_bindings(plugin,repo,home,supplied=bindings,binding_scope=binding_scope)
    host_bindings=overlay['native'].get(host,{'levels':{},'roles':{}})
    if host_bindings['levels'].keys()-catalog.levels.keys() or host_bindings['roles'].keys()-catalog.agents.keys():
        raise FlowError('unknown catalog level or role in fork assignments')
    roles={}
    for role in catalog.agents:
        base=catalog.resolve(role)
        level=catalog.level_of(role)
        if mode!='upstream':
            if profile=='economical' and role!='reviewer': level='standard'
            elif profile=='strong': level='apex'
        selected=catalog.levels[level]
        model=base.codex.model if host=='codex' else base.claude if host=='claude' else base.opencode
        effort=base.codex.reasoning_effort if host=='codex' else None
        if mode!='upstream' and profile!='upstream':
            model=selected.codex.model if host=='codex' else selected.claude if host=='claude' else catalog.aliases[selected.opencode]
            if host in EFFORTS:
                effort=('xhigh' if role=='reviewer' else 'high') if profile=='strong' else ('high' if role=='reviewer' else 'medium')
        assignment={'model':model,'effort':effort,'source':'upstream' if mode=='upstream' else f'profile:{profile}','pinned':False}
        if mode!='upstream':
            for cell in (host_bindings['levels'].get(level,{}),host_bindings['roles'].get(role,{})):
                if cell:
                    assignment.update(cell); assignment['source']='fork-bindings'
        pin_configs=[('personal',personal),('project',project),('invocation',explicit)]
        if mode=='upstream': pin_configs=[('invocation',explicit)]
        for label, config in pin_configs:
            all_roles=mapping(config.get('roles',{}))
            if all_roles.keys()-catalog.agents.keys():
                raise FlowError('unknown pinned role')
            pin=mapping(all_roles.get(role,{}))
            if pin.keys() & set(HOSTS): pin=mapping(pin.get(host,{}))
            if pin:
                assignment.update(_pin(pin,host)); assignment['source']=label+':pin'; assignment['pinned']=True
        model_id(assignment['model'])
        if assignment['effort'] is not None and host in EFFORTS and assignment['effort'] not in EFFORTS[host]:
            raise FlowError('unsupported effective effort')
        roles[role]=assignment
    warnings=[]
    if roles['developer']['model']==roles['reviewer']['model'] and roles['developer']['effort']==roles['reviewer']['effort']:
        warnings.append('developer and reviewer resolve to the same model and effort; review still requires independent execution')
    if mode!='upstream':
        warnings.append('model competence, account entitlement and live loading are unobserved; catalog assignments are not runtime proof')
    value={'mode':mode,'models':profile,'host':host,'budget_minutes':budget,'workflow':workflow,'roles':roles,
           'api':_api(plugin,overlay,mode=='upstream'),'sources':sources,'identity':definition['identity'],
           'resolution_inputs':{'personal':personal,'project':project,'invocation':explicit,'bindings':overlay},
           'observed_at_runtime':'unobserved','warnings':warnings,
           'enforcement':{'owned_process_deadline':'enforced','native_delegation':'cooperative','desktop_autostart':'unsupported'}}
    if host=='omp': value['omp_aliases']={k:roles[r]['model'] for k,r in [('default','developer'),('smol','wayfinder'),('slow','reviewer')]}
    return json.loads(canonical(value))
