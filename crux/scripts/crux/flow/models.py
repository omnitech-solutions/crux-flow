from __future__ import annotations

import copy
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import models_catalog

from .common import FlowError, canonical, decode, digest, identity, integer, mapping, model_id, text, utc_now
from . import managed, materialize, policy, provenance
from .processes import execute

SOURCES={'openrouter':'https://openrouter.ai/api/v1/models','claude':'https://platform.claude.com/docs/en/models/overview.md','codex':'installed:codex-debug-models-bundled'}
NATIVE_SOURCE={'claude':'claude','codex':'codex','opencode':'openrouter','omp':'openrouter','api':'openrouter'}


def _price(value,scale=1) -> str | None:
    try:
        n=Decimal(str(value))*scale
        return f'{n:.6f}' if n.is_finite() and n>=0 else None
    except (InvalidOperation,ValueError,TypeError): return None


def _tokens(value) -> int | None:
    if type(value) is int: return integer(value,low=1,high=100_000_000)
    if isinstance(value,str):
        match=re.fullmatch(r'\s*([0-9]+(?:\.[0-9]+)?)\s*([KM]?)\s*(?:tokens)?\s*',value,re.I)
        if match: return int(Decimal(match[1])*{'':1,'k':1000,'m':1000000}[match[2].lower()])
    return None


def normalize(provider: str,payload: dict,*,source: str,host_version: str | None=None) -> dict:
    if provider not in SOURCES or source!=SOURCES[provider]: raise FlowError('unsupported model discovery source')
    mapping(payload); rows={}
    if provider=='claude':
        markdown=text(payload.get('markdown'),maximum=1_000_000)
        lines=[line for line in markdown.splitlines() if line.lstrip().startswith('|')]
        table={}
        for line in lines:
            cells=[re.sub(r'\[([^]]+)\]\([^)]*\)',r'\1',x).strip() for x in line.strip().strip('|').split('|')]
            if cells: table[cells[0].strip('* ').lower()]=cells[1:]
        ids=table.get('claude api id',[])
        for i,cell in enumerate(ids):
            matches=re.findall(r'`(claude-[a-z0-9-]+)`',cell)
            if len(matches)!=1: continue
            key=matches[0]
            def cell_for(label):
                values=table.get(label,[])
                return values[i] if i<len(values) else None
            row=_base(key)
            row['capabilities']=['text-input','text-output']
            row['context_tokens']=_tokens(cell_for('context window')); row['max_output_tokens']=_tokens(cell_for('max output'))
            price=cell_for('pricing') or ''
            rates=re.findall(r'\$([0-9.]+)',price)
            if len(rates)==2:
                row['pricing']['input']=_price(rates[0]); row['pricing']['output']=_price(rates[1])
            rows[key]=row
    else:
        values=payload.get('data' if provider=='openrouter' else 'models',[])
        if not isinstance(values,list) or len(values)>10000: raise FlowError('invalid model catalog')
        for item in values:
            item=mapping(item); key=item.get('id') if provider=='openrouter' else item.get('slug',item.get('id'))
            if not isinstance(key,str) or key.startswith('~'): continue
            model_id(key); row=_base(key)
            if provider=='openrouter':
                architecture=mapping(item.get('architecture',{})); parameters=item.get('supported_parameters',[])
                if not isinstance(parameters,list): raise FlowError('invalid capability list')
                for modality in architecture.get('input_modalities',[]):
                    if modality in {'text','image','audio','video'}: row['capabilities'].append(modality+'-input')
                for modality in architecture.get('output_modalities',[]):
                    if modality in {'text','image','audio'}: row['capabilities'].append(modality+'-output')
                if 'tools' in parameters: row['capabilities'].append('tools')
                pricing=mapping(item.get('pricing',{}))
                row['pricing']['input']=_price(pricing.get('prompt'),1_000_000); row['pricing']['output']=_price(pricing.get('completion'),1_000_000)
                row['context_tokens']=_tokens(item.get('context_length'))
                row['max_output_tokens']=_tokens(mapping(item.get('top_provider',{})).get('max_completion_tokens'))
            else:
                efforts=item.get('supported_reasoning_levels',[])
                if not isinstance(efforts,list): raise FlowError('invalid bundled reasoning metadata')
                row['supported_efforts']=[x['effort'] for x in efforts if isinstance(x,dict) and x.get('effort') in policy.EFFORTS['codex']]
                row['context_tokens']=_tokens(item.get('context_window'))
                row['capabilities']=['text-input','text-output']
            if key in rows: raise FlowError('duplicate model identity')
            rows[key]=row
    value={'schema_version':1,'provider':provider,'source':source,'observed_at':utc_now(),'host_version':host_version,'models':rows}
    value['digest']=identity(value)
    return value


def _base(key: str) -> dict:
    return {'id':key,'capabilities':[],'supported_efforts':[],'context_tokens':None,'max_output_tokens':None,
            'pricing':{'input':None,'output':None,'unit':'USD per million tokens'},'entitlement':'unobserved','runtime_loaded':'unobserved'}


def discover(provider: str,*,executable: str | None=None,runner=execute,fetch=None) -> dict:
    if provider not in SOURCES: raise FlowError('unsupported discovery provider')
    if provider=='codex':
        import shutil
        exe=executable or shutil.which('codex')
        if not exe: raise FlowError('Codex executable unavailable for supported bundled discovery')
        with tempfile.TemporaryDirectory(prefix='crux-model-discovery-') as temp:
            home=Path(temp); (home/'.codex').mkdir()
            env={'PATH':os.environ.get('PATH',''),'HOME':temp,'CODEX_HOME':str(home/'.codex'),'LANG':'C.UTF-8'}
            version=runner([exe,'--version'],cwd=home,env=env,timeout=8,limit=10000)
            result=runner([exe,'debug','models','--bundled'],cwd=home,env=env,timeout=15,limit=2_000_000)
            if version.status!='ok' or result.status!='ok': raise FlowError('supported Codex bundled model discovery failed')
            return normalize(provider,mapping(decode(result.stdout)),source=SOURCES[provider],host_version=version.stdout.decode().strip()[:100])
    url=SOURCES[provider]
    try:
        if fetch is None:
            request=Request(url,headers={'User-Agent':'crux-flow-model-discovery/0.2'})
            with urlopen(request,timeout=15) as response:
                if urlparse(response.geturl()).hostname!=urlparse(url).hostname: raise FlowError('cross-host discovery redirect refused')
                raw=response.read(2_000_001)
        else: raw=fetch(url)
        if not isinstance(raw,bytes) or len(raw)>2_000_000: raise FlowError('model discovery exceeds its byte limit')
        payload={'markdown':raw.decode('utf-8')} if provider=='claude' else json.loads(raw)
        return normalize(provider,payload,source=url)
    except (OSError,ValueError,UnicodeError) as exc:
        raise FlowError('public model discovery failed; active configuration unchanged') from exc


def _validate_discovery(data: dict) -> None:
    mapping(data,allowed={'schema_version','provider','source','observed_at','host_version','models','digest'},required={'schema_version','provider','source','observed_at','host_version','models','digest'})
    if data['provider'] not in SOURCES or data['source']!=SOURCES[data['provider']] or data['schema_version']!=1:
        raise FlowError('invalid discovery provenance')
    if identity({k:v for k,v in data.items() if k!='digest'})!=data['digest']: raise FlowError('discovery digest mismatch')
    for key,row in mapping(data['models']).items():
        model_id(key); mapping(row,required={'id','capabilities','supported_efforts','pricing','context_tokens','max_output_tokens'})
        if row['id']!=key or not isinstance(row['capabilities'],list) or not isinstance(row['supported_efforts'],list): raise FlowError('invalid discovered model metadata')
        if row['pricing']['unit']!='USD per million tokens': raise FlowError('unknown price units')


def _scope(plugin: Path,repo: Path,home: Path,scope: str) -> tuple[Path,str]:
    if scope == 'source':
        if managed.read_file(plugin, 'release.json') is not None:
            raise FlowError('installed payloads are immutable; use project or user scope, or an editable source checkout')
        return plugin.resolve(), 'catalog/flow-bindings.json'
    if scope in {'project','user'}: return (repo if scope=='project' else home).resolve(),'.crux-flow/model-bindings.json'
    raise FlowError('unknown model activation scope')


def _guard_inputs(plugin: Path,repo: Path,home: Path) -> dict:
    return {'source':provenance.runtime_digest(plugin),'project_config':digest(managed.read_file(repo,policy.CONFIG)),
            'personal_config':digest(managed.read_file(home,policy.CONFIG)),
            'project_bindings':digest(managed.read_file(repo,'.crux-flow/model-bindings.json')),
            'personal_bindings':digest(managed.read_file(home,'.crux-flow/model-bindings.json'))}


def _assignment(change: dict,discovery: dict,required: list[str]) -> tuple[str,str,str,dict]:
    mapping(change,allowed={'host','kind','name','model','effort'},required={'host','kind','name','model'})
    host=change['host']; kind=change['kind']; name=text(change['name'],maximum=100)
    if host not in NATIVE_SOURCE or NATIVE_SOURCE[host]!=discovery['provider']: raise FlowError('discovery does not establish identity for selected host')
    if kind not in {'level','role'} or (host=='api' and kind!='role'): raise FlowError('invalid assignment boundary')
    selected=model_id(change['model']); row=discovery['models'].get(selected)
    if row is None: raise FlowError('selected model absent from discovery')
    if set(required)-set(row['capabilities']): raise FlowError('required capability is absent or unknown')
    effort=change.get('effort')
    if effort is not None and effort not in row['supported_efforts']: raise FlowError('requested effort is not supported by discovered facts')
    assignment={'model':('openrouter/'+selected if host in {'omp','opencode'} else selected)}
    if effort is not None: assignment['effort']=effort
    return host,kind,name,assignment


def propose(plugin: Path,repo: Path,home: Path,*,scope: str,changes: list[dict],discovery: dict,required_capabilities: list[str] | None=None) -> dict:
    _validate_discovery(discovery)
    if not isinstance(changes,list) or not 1<=len(changes)<=100: raise FlowError('proposal needs a bounded assignment change set')
    required=required_capabilities or []
    if not isinstance(required,list) or any(not isinstance(x,str) for x in required): raise FlowError('invalid required capabilities')
    root,target=_scope(plugin,repo,home,scope)
    raw=managed.read_file(root,target)
    candidate=mapping(decode(raw)) if raw else {'schema_version':1,'native':{},'api':{}}
    candidate=copy.deepcopy(candidate)
    touched=set()
    for change in changes:
        host,kind,name,assignment=_assignment(change,discovery,required)
        if (host,kind,name) in touched: raise FlowError('duplicate assignment change')
        touched.add((host,kind,name))
        if host=='api': candidate['api'][name]=assignment
        else: candidate['native'].setdefault(host,{'levels':{},'roles':{}})[kind+'s' if kind=='role' else 'levels'][name]=assignment
    catalog=models_catalog.load(plugin/'catalog/models.yml',plugin/'agents')
    diff=[]
    for host in sorted({x['host'] for x in changes}-{'api'}):
        for mode in ('aggressive','balanced','thorough'):
            old=policy.resolve(plugin,repo,home,host,{'mode':mode},catalog=catalog)
            new=policy.resolve(plugin,repo,home,host,{'mode':mode},bindings=candidate,catalog=catalog,binding_scope=scope)
            for role,before in old['roles'].items():
                after=new['roles'][role]
                if (before['model'],before['effort'])!=(after['model'],after['effort']):
                    diff.append({'host':host,'mode':mode,'role':role,'before':before,'after':after})
    if any(x['host']=='api' for x in changes):
        old=policy.resolve(plugin,repo,home,'codex',catalog=catalog)['api']
        new=policy.resolve(plugin,repo,home,'codex',bindings=candidate,catalog=catalog,binding_scope=scope)['api']
        for role,before in old['roles'].items():
            if before!=new['roles'][role]: diff.append({'host':'api','mode':'fork','role':role,'before':before,'after':new['roles'][role]})
    value={'schema_version':1,'scope':scope,'root':str(root),'repo':str(repo.resolve()),'home':str(home.resolve()),'plugin':str(plugin.resolve()),
           'target':target,'base_digest':digest(raw),'guards':_guard_inputs(plugin,repo,home),'changes':copy.deepcopy(changes),
           'required_capabilities':required,'discovery':discovery,'candidate':candidate,'effective_diff':diff,
           'activation':'staged','runtime_loaded':'unobserved','pins':'preserved'}
    value['proposal_digest']=identity(value)
    return value


def authorize_unattended(proposal: dict,allow: dict,root: Path) -> bool:
    required={'enabled','scope','approved_model_ids','vendors','required_capabilities','max_input_usd_per_million','max_output_usd_per_million','pin_behavior','data_retention_approval'}
    mapping(allow,allowed=required,required=required)
    if allow['enabled'] is not True or allow['scope']!=str(root.resolve()) or allow['pin_behavior']!='preserve' or allow['data_retention_approval']!='exact-approved-models':
        raise FlowError('unattended activation lacks scoped explicit approval')
    if proposal['discovery']['provider']!='openrouter': raise FlowError('native entitlement and billing are not established for unattended activation')
    for change in proposal['changes']:
        key=change['model']; row=proposal['discovery']['models'][key]
        if key not in allow['approved_model_ids'] or key.split('/')[0] not in allow['vendors']: raise FlowError('model or vendor not preapproved')
        if set(allow['required_capabilities'])-set(row['capabilities']): raise FlowError('unattended capability requirement not met')
        for field,limit in [('input','max_input_usd_per_million'),('output','max_output_usd_per_million')]:
            actual=_price(row['pricing'][field]); ceiling=_price(allow[limit])
            if actual is None or ceiling is None or Decimal(actual)>Decimal(ceiling): raise FlowError('unknown or excessive unattended cost')
    return True


def apply(plugin: Path,repo: Path,home: Path,proposal: dict,*,authorized: bool,current_discovery: dict,unattended: dict | None=None) -> dict:
    root,target=_scope(plugin,repo,home,proposal.get('scope'))
    if authorized is not True:
        if unattended is None: raise FlowError('exact proposal authorization required')
        authorize_unattended(proposal,unattended,root)
    if proposal.get('proposal_digest')!=identity({k:v for k,v in proposal.items() if k!='proposal_digest'}): raise FlowError('proposal changed after staging')
    _validate_discovery(current_discovery)
    for change in proposal['changes']:
        _assignment(change,current_discovery,proposal['required_capabilities'])
        if current_discovery['models'][change['model']]!=proposal['discovery']['models'][change['model']]: raise FlowError('material discovery changed; stage a fresh proposal')
    fresh=propose(plugin,repo,home,scope=proposal['scope'],changes=proposal['changes'],discovery=proposal['discovery'],required_capabilities=proposal['required_capabilities'])
    if fresh['proposal_digest']!=proposal['proposal_digest']: raise FlowError('stale model proposal')
    changes={target:canonical(proposal['candidate'])}; controls={}
    if proposal['scope']=='user':
        from . import hosts
        for host in sorted({x['host'] for x in proposal['changes']}-{'api'}):
            effective=policy.resolve(plugin,repo,home,host,bindings=proposal['candidate'],binding_scope='user')
            generated=hosts.roles(plugin,effective)
            changes.update({f'.crux-flow/staged-model-projections/{host}/{name}':raw for name,raw in generated['files'].items()})
            controls[host]={'activation':'deferred to next selected project materialization','runtime_loaded':'unobserved'}
    elif proposal['scope']=='project':
        for host in sorted({x['host'] for x in proposal['changes']}-{'api'}):
            effective=policy.resolve(plugin,repo,home,host,bindings=proposal['candidate'],binding_scope=proposal['scope'])
            project_root,outputs,report=materialize.updates(plugin,effective,repo=repo,home=home,scope=proposal['scope'])
            if project_root!=root: raise FlowError('cross-scope projection requires a separate reviewed operation')
            changes.update(outputs); controls[host]=report
    result=managed.apply(managed.plan(root,changes,owner='model-maintenance'))
    return {**result,'activation':'source-applied; build a release before propagation' if proposal['scope']=='source' else 'user-bindings-and-staged-projections-applied; materialize the selected project before its next session' if proposal['scope']=='user' else 'bindings-and-projections-applied',
            'runtime_loaded':'unobserved','hosts':controls,'proposal_digest':proposal['proposal_digest']}


def rollback(root: Path,transaction: str) -> dict:
    return managed.rollback(root,transaction)
