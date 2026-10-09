from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import re
import shutil
from typing import Any

import codex_agents
import models_catalog
import opencode_agents
import yaml

from .common import FlowError, HOSTS, decode, mapping, model_id, text
from .managed import read_file
from .processes import execute

TOOL_MAP={'Read':'read','Grep':'grep','Glob':'glob','Edit':'edit','Write':'write','Bash':'bash','TodoWrite':'todo','WebSearch':'web_search'}
ACTION_MAP={'read':'read','grep':'grep','glob':'glob','list':'list','edit':'edit','shell':'bash','subagent':'task','todowrite':'todowrite','webfetch':'webfetch','websearch':'websearch','skill':'skill'}


def _parts(raw: bytes) -> tuple[dict,str,str]:
    content=raw.decode('utf-8')
    pieces=content.split('---',2)
    if len(pieces)!=3 or pieces[0].strip():
        raise FlowError('source role or skill needs frontmatter')
    return mapping(decode(pieces[1])),pieces[2],pieces[1]


def _markdown(head: dict,body: str) -> bytes:
    return ('---\n'+yaml.safe_dump(head,sort_keys=False,allow_unicode=True)+'---\n'+body.lstrip()).encode()


def _instructions(plugin: Path, role: str, policy: dict) -> str:
    data=mapping(decode(read_file(plugin,'catalog/flow-roles.json') or b''))
    purpose=text(data.get(role),maximum=4000)
    return ('# Crux Flow '+role+'\n\nPrimary session owns orchestration. '+purpose+'\n\n'
            'When delegated a unit of an active run, do not start another run; execute only that unit and return its evidence. '
            'For a genuinely new top-level code-change request, use the flow skill and the shared crux-flow CLI before implementation. '
            'Read-only requests create no run. Follow the frozen effective policy for active work. '
            'Use existing Crux knowledge, numbering and run-promptbook records, not a second state store. '
            'Do not start a council, forge a helper or add a review merely because a capability exists. '
            'Do not delegate from an unsupported child context. Preserve host/admin restrictions.\n\n'
            f'Rendered mode: {policy["mode"]}; generated configuration, runtime loading unobserved. '
            'The parent gives you Outcome, Evidence, Constraint and the permitted write set. '
            'Report findings and evidence to that parent; do not claim the entire run is complete after your unit.\n')


def roles(plugin: Path, policy: dict, *, engine: Path | None=None) -> dict:
    host=policy['host']; upstream=policy['mode']=='upstream'
    if host not in HOSTS:
        raise FlowError('unsupported host projection')
    files={}; unsupported={}
    catalog=models_catalog.load(plugin/"catalog/models.yml",plugin/"agents")
    for path in sorted((plugin/'agents').glob('*.md')):
        raw=read_file(plugin,path.relative_to(plugin).as_posix())
        if raw is None: raise FlowError('role source disappeared')
        head,body,fm=_parts(raw); name=head['name']
        if not upstream and name=='commander': continue
        assignment=policy['roles'][name]
        source=codex_agents.parse_source(path)
        if not upstream:
            source=replace(source,body=_instructions(plugin,name,policy),skills=('flow',))
            body=source.body
        unsupported[name]=[key for key in ('memory','isolation','maxTurns','effort','skills') if key in head]
        if host=='codex':
            runtime=replace(catalog.resolve(name).codex,model=assignment['model'],reasoning_effort=assignment['effort'])
            installed_engine = engine or (plugin if read_file(plugin, 'release.json') is not None else None)
            projected_skills = (installed_engine / 'skills' if upstream else installed_engine.parent / 'skills') if installed_engine else None
            validated_skills = plugin / 'skills' if upstream or projected_skills is not None else None
            rendered=codex_agents.render_agent(source,runtime,skill_root=validated_skills,name_prefix='crux_' if upstream else 'crux_flow_',projected_skill_root=projected_skills)
            files[f'crux-flow-{name}.toml']=rendered.encode()
        elif host=='claude':
            projected={key:head[key] for key in ('description','tools','disallowedTools','maxTurns','memory','isolation') if key in head}
            if isinstance(projected.get('tools'), str):
                projected['tools'] = re.sub(r'Agent\(([^)]+)\)', lambda match: 'Agent(' + ', '.join('crux-flow-' + part.strip() for part in match[1].split(',')) + ')', projected['tools'])
            projected['name']='crux-flow-'+name; projected['model']=assignment['model']
            if assignment['effort'] is not None: projected['effort']=assignment['effort']
            elif 'effort' in head: projected['effort']=head['effort']
            projected['skills']=head.get('skills',[]) if upstream else ['flow']
            unsupported[name]=[]
            files[f'crux-flow-{name}.md']=_markdown(projected,body)
        elif host=='opencode':
            native=mapping(decode(opencode_agents.transform(name,fm,assignment['model'])))
            for rule in native['permissions']:
                if rule['action']=='subagent' and rule['resource']!='*':
                    rule['resource']='crux-flow-'+rule['resource']
            if 'name' in native or 'metadata' in native:
                raise FlowError('unsafe OpenCode V2 frontmatter')
            files[f'crux-flow-{name}.md']=_markdown(native,body)
        else:
            tools=[]; spawns=[]; missing=[]
            for tool in sorted(source.tools):
                spawn=re.fullmatch(r'Agent\(([^)]+)\)',tool)
                if spawn: spawns.append('crux-flow-'+spawn[1])
                elif tool in TOOL_MAP: tools.append(TOOL_MAP[tool])
                else: missing.append(tool)
            if spawns: tools.append('task')
            projected={'name':'crux-flow-'+name,'description':head['description'],'model':assignment['model'],
                       'tools':sorted(set(tools)),'spawns':sorted(spawns)}
            unsupported[name]=sorted(set(unsupported[name]+missing))
            files[f'crux-flow-{name}.md']=_markdown(projected,body)
    return {'files':files,'unsupported':unsupported,'runtime_loaded':'unobserved',
            'enforcement':{'native_delegation':'cooperative','permissions':'host-specific projection; runtime verification required'}}


def skills(plugin: Path, host: str, *, engine: Path | None=None) -> dict[str,bytes]:
    if host not in HOSTS: raise FlowError('unsupported host')
    output={}
    for path in sorted((plugin/'skills').rglob('*')):
        if path.is_dir() or path.name==".gitkeep": continue
        relative=path.relative_to(plugin/'skills')
        raw=read_file(plugin,path.relative_to(plugin).as_posix())
        if raw is None: raise FlowError('skill source disappeared')
        if path.name=='SKILL.md':
            head,body,_=_parts(raw)
            body=re.sub(r'(?<![\w/-])/crux:(?=[a-z])','/crux-flow:',body)   # the fork's skills live in the crux-flow plugin namespace
            body=re.sub(r'<!-- BEGIN GENERATED: runtime-compat -->.*?<!-- END GENERATED: runtime-compat -->','',body,flags=re.S)
            if engine is None:
                location=('The installed plugin contains engine/ with the canonical Crux runtime. '
                          'For Claude set CRUX_PLUGIN_ROOT to ${CLAUDE_PLUGIN_ROOT}/engine. '
                          'Other hosts derive the plugin root from this selected skills/<name>/SKILL.md, then use engine/. '
                          'The CLI is at the plugin root bin/crux-flow, not inside engine/.')
            else:
                location=f'Canonical installed Crux engine: `{engine}`. Set CRUX_PLUGIN_ROOT to that exact path. The CLI is `{engine.parent}/bin/crux-flow`.'
            policy=('New code-change requests must enter the flow skill. Read-only questions stay read-only. '
                    'Existing upstream-mode records retain their original selected procedure; a Flow policy never waives human or security approval. '
                    'Use crux_flow_* roles in Codex and crux-flow-* roles in the other hosts. '
                    'Do not run upstream role installers over managed fork roles. Generated roles require the next compatible host session.\n\n')
            body='## Installed runtime and routing\n\n'+location+'\n\n'+policy+body
            raw=_markdown(head,body)
        output[relative.as_posix()]=raw
    return output


def paths(host: str,scope: str,*,repo: Path,home: Path,host_home: Path | None=None) -> dict[str,Path]:
    if host not in HOSTS or scope not in {'user','project'}: raise FlowError('unsupported installation scope')
    if scope=='project': base=repo/{'claude':'.claude','codex':'.codex','opencode':'.opencode','omp':'.omp'}[host]
    else: base=host_home or home/{'claude':'.claude','codex':'.codex','opencode':'.config/opencode','omp':'.omp/agent'}[host]
    skill_base=(repo/'.agents/skills' if scope=='project' else base/'skills') if host=='codex' else base/'skills'
    return {'base':base,'agents':base/'agents','skills':skill_base}


def environment(host: str,home: Path,*,host_home: Path | None=None) -> dict[str,str]:
    env=dict(os.environ)
    env['HOME']=str(home)
    if host=='codex': env['CODEX_HOME']=str(host_home or home/'.codex')
    elif host=='claude':
        # Claude Code finds its login by the configuration directory it is told about: naming even its own default
        # directory makes it look for a separate login, and a model turn then ends "Not logged in". The variable is
        # therefore set only to redirect Claude away from its default, or to keep one the caller already set.
        target=host_home or home/'.claude'
        if 'CLAUDE_CONFIG_DIR' in env or target!=Path.home()/'.claude': env['CLAUDE_CONFIG_DIR']=str(target)
    elif host=='opencode': env['XDG_CONFIG_HOME']=str((host_home.parent if host_home else home/'.config'))
    elif host=='omp': env['PI_CODING_AGENT_DIR']=str(host_home or home/'.omp/agent')
    else: raise FlowError('unsupported host')
    return env


def turn(host: str,exe: str,*,repo: Path,home: Path,model: str,effort: str | None,prompt: str,host_home: Path | None=None) -> tuple[list[str],dict[str,str]]:
    model_id(model)
    if host not in HOSTS: raise FlowError('unsupported host')
    if effort is not None and host not in {'codex','claude'}: raise FlowError('host has no supported effort projection')
    command={'codex':[exe,'exec','--json','--model',model,'--cd',str(repo)],
             'claude':[exe,'--print','--verbose','--output-format','stream-json','--model',model],
             'opencode':[exe,'run','--format','json','--model',model],
             'omp':[exe,'--print','--mode=json','--model='+model,'--cwd='+str(repo)]}[host]
    if effort is not None:
        from .policy import EFFORTS
        if effort not in EFFORTS[host]: raise FlowError('unsupported host effort')
        command.extend(['--config',f'model_reasoning_effort="{effort}"'] if host=='codex' else ['--effort',effort])
    command.extend(['-'] if host=='codex' else [prompt] if host in {'opencode','omp'} else [])
    return command,environment(host,home,host_home=host_home)


def probe(host: str,*,home: Path,repo: Path,executable: str | None=None,runner=execute) -> dict:
    if host not in HOSTS: raise FlowError('unsupported host')
    exe=executable or shutil.which(host)
    if exe is None: return {'host':host,'present':False,'runtime_loaded':'unobserved'}
    env={'PATH':os.environ.get('PATH',''),'HOME':str(home),'LANG':'C.UTF-8'}
    results={}
    for key,args in [('version',['--version']),('help',['--help'])]:
        result=runner([exe,*args],cwd=repo,env=env,timeout=8,limit=100_000)
        if result.status!='ok': raise FlowError(f'{host} capability probe failed: {key}')
        results[key]=result.stdout.decode('utf-8',errors='replace')
    match=re.search(r'(?<![\w.])v?(\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.-]+)?)\b',results['version'])
    if match is None: raise FlowError('host version is not recognizable')
    return {'host':host,'present':True,'executable':exe,'version':match[1],
            'plugin_commands_advertised':'plugin' in results['help'],'runtime_loaded':'unobserved'}


def detect(executables: dict[str,str] | None=None) -> dict[str,str]:
    """Installed supported hosts, in a stable order. Absence is not an error."""
    available=executables if executables is not None else {h:p for h in HOSTS if (p:=shutil.which(h))}
    return {h:available[h] for h in HOSTS if h in available}
