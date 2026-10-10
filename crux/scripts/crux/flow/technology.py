"""Technology guidance: one shipped catalog, one project section, one generated router region.

The catalog says which technologies exist and how a manifest declares them. The project's
`.crux-flow.yml` `technology` section says which layers the repository has, what to read for each and
which commands it owns. This module renders the project router's region from the two plus the versions
the manifests declare, and checks that everything the section names is real. Nothing here runs project
code, calls a model or reads the network (the one opt-in exception is `freshness`).
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import bionic_config

from .common import FlowError, canonical, decode, digest, identity, mapping, text
from . import managed, policy

CATALOG='catalog/flow-technology.json'
TEMPLATE='templates/technology-router.SKILL.md.tmpl'
RECEIPT='.crux-flow/receipts/technology.json'
BEGIN='<!-- BEGIN GENERATED: technology-routes -->'; END='<!-- END GENERATED: technology-routes -->'
NOTES_BEGIN='<!-- BEGIN PROJECT: notes -->'; NOTES_END='<!-- END PROJECT: notes -->'
SKILLS='.agents/skills'                                   # Codex and OpenCode read it; the router is authored here once
COPIES={'claude':'.claude/skills','omp':'.omp/skills'}    # hosts that read only their own project skill folder
SKILL_ROOTS=(SKILLS,'.claude/skills','.opencode/skills','.omp/skills')
NO_PRELOAD=('opencode','omp')                             # no role field that injects a skill at start
SECTIONS=('dependencies','devDependencies','peerDependencies','optionalDependencies')
PRUNED={'node_modules','dist','build','out','coverage','vendor','target','__pycache__'}
MANAGERS={'pnpm','npm','yarn'}; BUILTINS={'install','exec','dlx','test','add','remove','update','audit','pack','publish','why','list'}
LIFECYCLES={'active','moved','deprecated'}
DESCRIPTION_LIMIT=1024; BODY_LINES=80; MAX_FILES=200_000


# ── catalog ──────────────────────────────────────────────────────────────────────────────────────

def load_catalog(plugin: Path) -> dict:
    raw=managed.read_file(plugin,CATALOG)
    if raw is None: raise FlowError('technology catalog missing')
    data=mapping(decode(raw),allowed={'schema_version','families','technologies','references'},required={'schema_version','families','technologies'})
    if type(data['schema_version']) is not int or data['schema_version']!=1: raise FlowError('invalid technology catalog version')
    for purpose in mapping(data['families']).values(): text(purpose,maximum=400)
    for key,row in mapping(data['technologies']).items():
        if re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',key) is None: raise FlowError('invalid technology id')
        mapping(row,allowed={'name','family','detect','terms','docs','sources'},required={'name','family','detect','terms','docs'})
        text(row['name'],maximum=80)
        if row['family'] not in data['families']: raise FlowError('technology names an unknown reference family')
        detect=mapping(row['detect'],allowed={'packages','files','pattern'})
        if not (detect.get('packages') or detect.get('files')) or ('pattern' in detect and not detect.get('files')):
            raise FlowError('a technology is detected by package names or by files, and a pattern needs files')
        for name in [*detect.get('packages',[]),*detect.get('files',[]),*row['terms']]: text(name,maximum=200)
        if 'pattern' in detect: re.compile(text(detect['pattern'],maximum=400))
        if not row['terms']: raise FlowError('a technology needs at least one trigger term')
        if urlparse(text(row['docs'],maximum=400)).scheme!='https': raise FlowError('official documentation must be an https URL')
        for source in row.get('sources',[]):
            fields={'repository','path','publisher','licence','first_party','lifecycle','compatibility'}
            mapping(source,allowed=fields|{'ref','replaced_by'},required=fields)
            if re.fullmatch(r'[A-Za-z0-9._-]+/[A-Za-z0-9._-]+',source['repository']) is None: raise FlowError('invalid upstream repository')
            text(source['licence'],maximum=200,label='upstream licence'); text(source['publisher'],maximum=120)
            if type(source['first_party']) is not bool or source['lifecycle'] not in LIFECYCLES: raise FlowError('invalid upstream source facts')
            if (source['lifecycle']=='active')==('replaced_by' in source): raise FlowError('a moved or deprecated source names its replacement; an active one does not')
            # Unknown compatibility is recorded as unknown. A wildcard would claim every version was checked.
            if text(source['compatibility'],maximum=120) in {'*','any','all'}: raise FlowError('compatibility is a stated range or "unknown", never a wildcard')
    # A reference family a project routes to by choice: its page is the project's own, so nothing detects it,
    # nothing is fetched for it and no project is obliged to route or exclude it.
    for key,row in mapping(data.setdefault('references',{})).items():
        if re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',key) is None or key in data['technologies']: raise FlowError('invalid reference id')
        mapping(row,allowed={'name','family','terms','purpose'},required={'name','family','terms','purpose'})
        text(row['name'],maximum=80); text(row['purpose'],maximum=600)
        if row['family'] not in data['families']: raise FlowError('reference names an unknown reference family')
        if not isinstance(row['terms'],list) or not row['terms']: raise FlowError('a reference needs at least one trigger term')
        for name in row['terms']: text(name,maximum=200)
    return data


# ── what the repository declares ─────────────────────────────────────────────────────────────────

def _files(repo: Path) -> list[str]:
    found=[]
    for base,folders,names in os.walk(repo,followlinks=False):
        folders[:]=sorted(f for f in folders if f not in PRUNED and not f.startswith('.'))
        for name in sorted(names):
            path=Path(base)/name
            if path.is_symlink(): continue
            found.append(path.relative_to(repo).as_posix())
            if len(found)>MAX_FILES: raise FlowError('repository is too large to inspect for technologies')
    return found


def _matches(path: str,pattern: str) -> bool:
    return PurePosixPath(path).match(pattern) if '/' not in pattern else PurePosixPath(path).full_match(pattern)


def _read(repo: Path,path: str,*,limit: int=1_000_000) -> str:
    try: return (repo/path).read_bytes()[:limit].decode('utf-8',errors='replace')
    except OSError: return ''


def detect(repo: Path,catalog: dict,files: list[str] | None=None) -> dict[str,str]:
    """Catalog ids the manifests declare, each with its declared version string. Reads files; executes nothing."""
    files=_files(repo) if files is None else files
    workspace=next((decode(_read(repo,p)) for p in files if p=='pnpm-workspace.yaml'),None) or {}
    pins={'':workspace.get('catalog') or {},**(workspace.get('catalogs') or {})}
    declared: dict[str,set[str]]={}
    for path in files:
        if PurePosixPath(path).name!='package.json': continue
        try: manifest=json.loads(_read(repo,path))
        except ValueError: continue
        for section in SECTIONS:
            for name,spec in (manifest.get(section) or {}).items() if isinstance(manifest,dict) else ():
                if not isinstance(spec,str): continue
                if spec.startswith('catalog:'): spec=(pins.get(spec[8:]) or {}).get(name,'')
                declared.setdefault(name,set()).add(spec if isinstance(spec,str) else '')
    result={}
    for key,row in catalog['technologies'].items():
        rule=row['detect']; versions: set[str]=set(); present=False
        for name in rule.get('packages',[]):
            if name in declared: present=True; versions|=declared[name]
        pattern=re.compile(rule['pattern']) if 'pattern' in rule else None
        for path in files:
            if not any(_matches(path,glob) for glob in rule.get('files',[])): continue
            if pattern is None: present=True; continue
            for match in pattern.finditer(_read(repo,path)):
                present=True
                if match.groups(): versions.add(match[1])
        versions={v for v in versions if v and not v.startswith(('workspace:','file:','link:'))}
        if present: result[key]=' / '.join(sorted(versions)) if versions else 'unknown'
    return result


# ── rendering ────────────────────────────────────────────────────────────────────────────────────

def section(repo: Path) -> dict | None:
    return policy.read_config(repo/policy.CONFIG).get('technology')


def _cell(value: str) -> str:
    return value.replace('|','\\|').replace('\n',' ') or '-'


def _paths(values: list[str]) -> str:
    return ', '.join(f'`{v}`' for v in values)


def _command(command: dict) -> str:
    notes=[command['risk'],*(f'needs {need}' for need in command.get('requires',[]))]
    return f'`{command["id"]}`: `{shlex.join(command["argv"])}` in `{command["cwd"]}` ({"; ".join(notes)})'


def _stamp(name: str,version: str) -> str:
    return f'{name} `{version}`'


def region(config: dict,catalog: dict,detected: dict[str,str]) -> str:
    known=catalog['technologies']
    lines=['| Layer | Paths | Technologies (declared version) | Read, in this order | Repository rules | Commands | Never |','|---|---|---|---|---|---|---|']
    for layer in config['layers']:
        ids=[i for i in layer.get('technologies',[]) if i in known]
        read=layer.get('read',{})
        order=[f'{label} {_paths(values)}' for label,values in (('project',read.get('project',[])),('pinned captures',read.get('captures',[]))) if values]
        if ids: order.append('official docs at the declared version '+', '.join(known[i]['docs'] for i in ids))
        pages=[f'{catalog["references"][i]["name"]} (project page)' for i in layer.get('references',[]) if i in catalog.get('references',{})]
        cells=[f'`{layer["id"]}`',_paths(layer['paths']),'; '.join([*(_stamp(known[i]['name'],detected.get(i,'not declared')) for i in ids),*pages]),
               '; then '.join(order),_paths(layer.get('rules',[])),'; '.join(_command(c) for c in layer.get('commands',[])),'; '.join(layer.get('never',[]))]
        lines.append('| '+' | '.join(_cell(c) for c in cells)+' |')
    # The repository-wide never-list is enforced by the check and deliberately not printed: a router that names a
    # forbidden technology teaches a reader its name.
    tail=[]
    if config.get('exclude'): tail.append('Declared but deliberately not routed: '+'; '.join(f'{known[i]["name"] if i in known else i} ({why})' for i,why in config['exclude'].items())+'.')
    body='\n'.join(lines+['']+[line+'\n' for line in tail])
    return '\n'+body+f'\nRoutes digest: `tr-{identity(body)[:12]}`\n'


def description(config: dict,catalog: dict) -> str:
    """Written for triggering: what it does, when to use it, then the terms a request is likely to contain."""
    known=catalog['technologies']; names=[]
    for layer in config['layers']:
        for i in layer.get('technologies',[]):
            if i in known and known[i]['terms'][0] not in names: names.append(known[i]['terms'][0])
    for layer in config['layers']:
        for i in layer.get('references',[]):
            row=catalog.get('references',{}).get(i)
            if row and row['terms'][0] not in names: names.append(row['terms'][0])
    listed=', '.join(names) if names else 'the configured layers'
    return ('Routes a code change in this repository to the vetted references, repository rules and bound commands for the layer it touches. '
            f'Use before writing, reviewing or debugging code that involves {listed}, or when asked which reference, version or command applies to a path. '
            'Not for prose-only edits.')


def _split(raw: str) -> tuple[dict,str] | None:
    pieces=raw.split('---',2)
    if len(pieces)!=3 or pieces[0].strip(): return None
    try: head=decode(pieces[1])
    except FlowError: return None
    return (head,pieces[2]) if isinstance(head,dict) else None


def _between(content: str,begin: str,end: str) -> tuple[int,int] | None:
    if content.count(begin)!=1 or content.count(end)!=1 or content.index(begin)>content.index(end): return None
    return content.index(begin)+len(begin),content.index(end)


def router(plugin: Path,config: dict,catalog: dict,detected: dict[str,str],current: str | None) -> str:
    raw=managed.read_file(plugin,TEMPLATE)
    if raw is None: raise FlowError('technology router template missing')
    span=_between(current,NOTES_BEGIN,NOTES_END) if current else None
    notes=current[span[0]:span[1]] if span else '\n'      # the one hand-written section survives every rewrite
    values={'name':config.get('router','technology-references'),'description':json.dumps(description(config,catalog),ensure_ascii=False),
            'region':BEGIN+region(config,catalog,detected)+END,'notes':NOTES_BEGIN+notes+NOTES_END}
    return re.sub(r'\{\{(\w+)\}\}',lambda match: values[match[1]],raw.decode('utf-8'))


# ── closure ──────────────────────────────────────────────────────────────────────────────────────

def _error(path: str,field: str,error: str) -> dict:
    return {'path':path,'field':field,'error':error}


def _capture(repo: Path,docs: Path | None,name: str) -> str | None:
    """Why a research source page is not a usable pin, or None when it is one."""
    parsed=_split(_read(repo,name))
    if parsed is None: return 'source page has no frontmatter'
    head=parsed[0]; url=head.get('source_url'); raw_path=head.get('raw_path')
    if not isinstance(url,str) or not url.startswith('https://'): return 'source page has no source_url'
    if not head.get('captured_at'): return 'source page has no capture date (captured_at)'
    if urlparse(url).hostname=='github.com' and re.search(r'/[0-9a-f]{40}(?:/|$)',url) is None: return 'source is not pinned to a commit'
    if not isinstance(raw_path,str) or docs is None: return 'source page names no raw capture (raw_path)'
    capture=docs/raw_path
    if not capture.is_dir() or not any(capture.iterdir()): return f'raw capture {raw_path} is missing'
    return None


def _script(argv: list[str]) -> str | None:
    """The package script a package-manager command runs, or None when it does not name one checkably."""
    if argv[0] not in MANAGERS or len(argv)<2 or argv[1].startswith('-'): return None
    if argv[1]=='run': return argv[2] if len(argv)>2 else ''
    return None if argv[1] in BUILTINS else argv[1]


def _routers(repo: Path,name: str) -> dict[str,str]:
    """Every project skill file that calls itself the router, by repository path (one entry per real file)."""
    found={}; seen=set()
    for root in SKILL_ROOTS:
        base=repo/root
        if not base.is_dir(): continue
        for skill in sorted(base.glob('*/SKILL.md')):
            real=skill.resolve()
            parsed=_split(_read(repo,skill.relative_to(repo).as_posix()))
            if real in seen or not (skill.parent.name==name or (parsed and parsed[0].get('name')==name)): continue
            seen.add(real); found[skill.relative_to(repo).as_posix()]=real.read_text(encoding='utf-8',errors='replace')
    return found


def _mentions(config: dict,docs_dir: str) -> list[tuple[str,str,list[str]]]:
    """(layer, what, acceptable spellings) for everything a hand-written router or map must name."""
    wanted=[]
    for layer in config['layers']:
        read=layer.get('read',{})
        for name in [*read.get('project',[]),*read.get('captures',[])]:
            stem=name[:-len(PurePosixPath(name).suffix)] if PurePosixPath(name).suffix else name
            forms=[name,stem,PurePosixPath(name).parent.as_posix()] if PurePosixPath(name).name=='SKILL.md' else [name,stem]
            forms+=[form[len(docs_dir)+1:] for form in (name,stem) if form.startswith(docs_dir+'/')]
            wanted.append((layer['id'],name,forms))
        for command in layer.get('commands',[]):
            wanted.append((layer['id'],'command '+command['id'],[_script(command['argv']) or shlex.join(command['argv'])]))
    return wanted


def expected(repo: Path,name: str) -> list[str]:
    """Where a Flow-owned router lives: once for the hosts that read `.agents/skills`, plus a copy for each host that does not."""
    places=[f'{SKILLS}/{name}/SKILL.md']
    for folder in COPIES.values():
        base=repo/folder
        served=base.is_symlink() and base.resolve()==(repo/SKILLS).resolve()
        if not served and base.parent.is_dir(): places.append(f'{folder}/{name}/SKILL.md')
    return places


def inspect(plugin: Path,repo: Path) -> dict | None:
    """Everything `check`, `sync` and `init` need, computed once. None when the project has no technology section."""
    config=section(repo)
    if config is None: return None
    catalog=load_catalog(plugin); known=catalog['technologies']; files=_files(repo); detected=detect(repo,catalog,files)
    name=config.get('router','technology-references'); errors=[]; warnings=[]
    try: layout=bionic_config.load_config(repo,require_tree=False); docs=layout.docs_root; docs_dir=docs.relative_to(repo.resolve()).as_posix()
    except (bionic_config.BionicConfigError,ValueError): docs=None; docs_dir='bionic'
    routed=[]; pages=[]
    for layer in config['layers']:
        where=f'technology.layers.{layer["id"]}'
        for glob in layer['paths']:
            if not any(_matches(p,glob) or p.startswith(glob.rstrip('/')+'/') for p in files): errors.append(_error(glob,where+'.paths','no file in the repository matches this path'))
        for key in layer.get('technologies',[]):
            if key not in known: errors.append(_error(key,where+'.technologies','not a technology in the Flow catalog'))
            elif key not in detected: errors.append(_error(key,where+'.technologies','routed, but no manifest in this repository declares it'))
            elif key not in routed: routed.append(key)
        read=layer.get('read',{})
        for key in layer.get('references',[]):
            if key not in catalog['references']: errors.append(_error(key,where+'.references','not a reference in the Flow catalog'))
            elif not read.get('project'): errors.append(_error(key,where+'.references',"a reference is the project's own page; name that page under read.project"))
            elif key not in pages: pages.append(key)
        for page in [*read.get('project',[]),*layer.get('rules',[])]:
            if not (repo/page).is_file(): errors.append(_error(page,where,'referenced page does not exist'))
        for page in read.get('captures',[]):
            if not (repo/page).is_file(): errors.append(_error(page,where+'.read.captures','referenced page does not exist'))
            elif (problem:=_capture(repo,docs,page)): errors.append(_error(page,where+'.read.captures',problem))
        for command in layer.get('commands',[]):
            base=repo/command['cwd']; spot=f'{where}.commands.{command["id"]}'; script=_script(command['argv'])
            if not base.is_dir(): errors.append(_error(command['cwd'],spot,'command working directory does not exist'))
            elif script is not None:
                try: scripts=json.loads(_read(repo,(PurePosixPath(command['cwd'])/'package.json').as_posix()) or '{}').get('scripts',{})
                except (ValueError,AttributeError): scripts={}
                if script not in scripts: errors.append(_error(command['cwd']+'/package.json',spot,f'no script named {script!r}; the command binding does not exist'))
            elif '/' in command['argv'][0] and not (base/command['argv'][0]).is_file():
                errors.append(_error(command['argv'][0],spot,'command executable does not exist'))
    excluded=config.get('exclude',{})
    for key in [*excluded,*config.get('never',[])]:
        if key not in known: errors.append(_error(key,'technology','not a technology in the Flow catalog'))
    for key in sorted(detected):
        if key in config.get('never',[]): errors.append(_error(key,'technology.never','declared in a manifest, but this repository lists it under never'))
        elif key not in routed and key not in excluded: errors.append(_error(key,'technology.layers','declared in a manifest, but neither routed by a layer nor listed under exclude'))
    for key in excluded:
        if key in known and key not in detected: errors.append(_error(key,'technology.exclude','excluded, but no manifest declares it; remove the exclusion'))
        if key in routed: errors.append(_error(key,'technology.exclude','both routed and excluded'))
    if (plugin/'skills'/name).exists(): errors.append(_error(name,'technology.router','a Flow plugin skill already has this name'))
    wanted=region(config,catalog,detected)
    state={'config':config,'catalog':catalog,'detected':detected,'routed':routed,'router':name,'errors':errors,'warnings':warnings,
           'region':wanted,'docs_dir':docs_dir,'drift':[],'files':{},'refresh':[]}
    found=_routers(repo,name)
    if config['owner']=='flow':
        places=expected(repo,name); receipt=mapping(decode(managed.read_file(repo,RECEIPT) or b'{}')).get('files',{})
        try: current=managed.read_file(repo,places[0])
        except FlowError: current=None
        text_=current.decode('utf-8') if current is not None and places[0] in receipt else ''
        body=router(plugin,config,catalog,detected,text_ or None).encode()
        for place in places:
            try: seen=managed.read_file(repo,place)
            except FlowError: errors.append(_error(place,'technology.router','a symlink is in the way; Flow never writes through one')); continue
            if seen is not None and place not in receipt: errors.append(_error(place,'technology.router','this file exists and is not Flow\'s; a foreign router is never adopted (use owner: project to keep it)'))
            elif seen!=body: state['drift'].append(place)
            state['files'][place]=body
        for place in sorted(set(found)-set(places)): errors.append(_error(place,'technology.router','a second skill claims the router\'s name'))
        for place in sorted(set(receipt)-set(places)): state['files'][place]=None; state['drift'].append(place)
    else:
        if not found: errors.append(_error(f'{SKILLS}/{name}/SKILL.md','technology.router','owner is project, and the project has no router skill'))
        elif len(set(found.values()))>1: errors.append(_error(', '.join(sorted(found)),'technology.router','copies of the router differ; exactly one router is allowed'))
        text_=next(iter(found.values()),'')
        place=next(iter(found),None); parsed=_split(text_) if text_ else None
        chart=_read(repo,config['map']) if 'map' in config else ''
        if 'map' in config and not chart: errors.append(_error(config['map'],'technology.map','the reference map does not exist'))
        if parsed is None and found: errors.append(_error(place,'technology.router','router has no frontmatter'))
        elif parsed:
            head,body_=parsed; told=str(head.get('description',''))
            if head.get('name')!=name or PurePosixPath(place).parent.name!=name: errors.append(_error(place,'name','the skill name, its folder and technology.router must agree'))
            if not told or len(told)>DESCRIPTION_LIMIT: errors.append(_error(place,'description',f'a description of at most {DESCRIPTION_LIMIT} characters is required'))
            for key in routed:
                if not any(_word(term,told) for term in known[key]['terms']): errors.append(_error(place,'description',f'lacks a trigger term for {known[key]["name"]} ({", ".join(known[key]["terms"])})'))
            for key in pages:
                row=catalog['references'][key]
                if not any(_word(term,told) for term in row['terms']): errors.append(_error(place,'description',f'lacks a trigger term for {row["name"]} ({", ".join(row["terms"])})'))
            for key,row in known.items():
                if key not in routed and _word(row['terms'][0],told): errors.append(_error(place,'description',f'names {row["name"]}, which no layer routes'+('' if key in detected else ' and no manifest declares')))
            if len(body_.strip().splitlines())>BODY_LINES: warnings.append(f'{place}: the router body is over {BODY_LINES} lines; move detail into references')
            for layer_id,what,forms in _mentions(config,docs_dir):
                if not any(form and form in text_+chart for form in forms): errors.append(_error(place,f'technology.layers.{layer_id}',f'neither the router nor the map names {what}'))
            for holder,content in ((place,text_),(config.get('map'),chart)):
                span=_between(content,BEGIN,END) if holder else None
                if span and content[span[0]:span[1]]!=wanted: state['drift'].append(holder)
            if not any(_between(content,BEGIN,END) for content in (text_,chart)): warnings.append('the hand-written router carries no generated region, so a declared version change is not tracked; paste the region `technology sync` prints to track it')
    if state['drift'] and text_:
        changed=[key for key in routed if _stamp(known[key]['name'],detected[key]) not in text_+(_read(repo,config['map']) if 'map' in config else '')]
        state['changed']=changed
        state['refresh']=sorted({page for layer in config['layers'] if set(layer.get('technologies',[]))&set(changed) for page in layer.get('read',{}).get('captures',[])})
    guide=_read(repo,'AGENTS.md')
    if name not in guide: warnings.append(f'the root AGENTS.md does not name the `{name}` skill; add: "{agents_line(name)}"')
    if any((repo/p).is_file() for p in ('CLAUDE.md','.claude/CLAUDE.md','CLAUDE.local.md')): warnings.append('a CLAUDE.md is present, so Claude Code does not read AGENTS.md; the routing line must be in the file Claude reads')
    return state


def _word(term: str,content: str) -> bool:
    return re.search(r'(?<![A-Za-z0-9])'+re.escape(term)+r'(?![A-Za-z0-9])',content) is not None


def agents_line(name: str) -> str:
    """The one sentence a project puts in its root AGENTS.md. Flow proposes it and never writes that file."""
    return (f'Before reading, searching, writing or reviewing code in this repository, invoke the `{name}` skill. '
            'It names the references, rules and commands for the paths you are about to touch; read only the row that matches.')


def _payload(state: dict) -> dict:
    return {'router':state['router'],'owner':state['config']['owner'],'layers':len(state['config']['layers']),'detected':state['detected'],
            'drift':bool(state['drift']),'paths':sorted(state['drift']),'validation_errors':state['errors'],'warnings':state['warnings'],
            **({'version_changed':state['changed'],'refresh_sources':state['refresh'],'refresh_with':'refresh-research-sources'} if state.get('changed') else {}),
            'upstream_freshness':'not checked; this gate is offline','runtime_loaded':'unobserved'}


ABSENT={'drift':False,'surface_absent':True,'reason':'this project has no technology section in .crux-flow.yml; nothing is routed and nothing was checked'}


def check(plugin: Path,repo: Path) -> tuple[int,dict]:
    """The drift gate. 0 clean or not applicable; 1 drift or a broken reference, with the JSON saying which."""
    state=inspect(plugin,repo)
    if state is None: return 0,{**ABSENT,'eligible':detect(repo,load_catalog(plugin))}
    payload=_payload(state)
    return (1 if payload['drift'] or payload['validation_errors'] else 0),payload


def plan(plugin: Path,repo: Path) -> tuple[managed.Plan | None,dict]:
    """What a sync would write, and a report for the plan shown to the user. A project-owned router is never written."""
    state=inspect(plugin,repo)
    if state is None:
        return None,{'status':'not-configured','eligible':detect(repo,load_catalog(plugin)),'adopt':'add a technology section to .crux-flow.yml'}
    payload=_payload(state)
    if state['config']['owner']=='project':
        return None,{**payload,'status':'checked','writes':'none; the project owns its router','region':BEGIN+state['region']+END}
    if state['errors']: return None,{**payload,'status':'broken','writes':'none until the named inputs are repaired'}
    changes={place:body for place,body in state['files'].items() if managed.read_file(repo,place)!=body}
    receipt=canonical({'schema_version':1,'router':state['router'],'files':{p:digest(b) for p,b in state['files'].items() if b is not None}})
    if receipt!=managed.read_file(repo,RECEIPT): changes[RECEIPT]=receipt
    planned=managed.plan(repo,changes,owner='technology-guidance',modes={place:0o644 for place in state['files']}) if changes else None
    return planned,{**payload,'status':'planned' if planned else 'already-correct',**({'plan':planned.public()} if planned else {})}


def sync(plugin: Path,repo: Path,*,dry_run: bool=False,approve=None) -> tuple[int,dict]:
    planned,report=plan(plugin,repo)
    if report['status']=='not-configured': return 0,{**ABSENT,'eligible':report['eligible']}
    if report['status']=='broken': return 1,report
    if report['status']=='checked': return (1 if report['drift'] or report['validation_errors'] else 0),report
    if planned is None: return 0,{**report,'drift':False,'paths':[],'written':[]}
    if dry_run: return 1,report
    if approve is not None and not approve(planned.public()): raise FlowError('operation cancelled')
    outcome=managed.apply(planned,expected_digest=planned.plan_digest)
    return 0,{**report,'status':'synced','drift':False,'paths':[],'written':[p for p in outcome['changed'] if p!=RECEIPT]}


# ── delegates and upstream ───────────────────────────────────────────────────────────────────────

def preload(repo: Path) -> dict | None:
    """The router a project asks its delegated roles to start with, or None. Hosts without a preload field say so."""
    config=section(repo)
    if not config or not config.get('preload'): return None
    name=config.get('router','technology-references')
    return {'skill':name,'roles':list(config['preload']),'path':str(repo.resolve()/SKILLS/name/'SKILL.md')}


def _get(url: str) -> bytes:
    request=Request(url,headers={'User-Agent':'crux-flow-technology/0.2','Accept':'application/vnd.github+json'})
    with urlopen(request,timeout=10) as response:
        if urlparse(response.geturl()).hostname!=urlparse(url).hostname: raise FlowError('cross-host redirect refused')
        return response.read(1_000_001)


def freshness(repo: Path,*,fetch=None) -> dict[str,dict]:
    """Whether each pinned capture is still the upstream head. Read-only; nothing fetched is stored or run.

    A source that cannot be reached, or one pinned by capture date and not by commit, is `unknown`: never `current`."""
    config=section(repo) or {'layers':[]}; result={}
    for page in sorted({p for layer in config['layers'] for p in layer.get('read',{}).get('captures',[])}):
        parsed=_split(_read(repo,page)); url=str(parsed[0].get('source_url','')) if parsed else ''
        match=re.fullmatch(r'https://github\.com/([^/]+)/([^/]+)/tree/([0-9a-f]{40})/?(.*)',url)
        if match is None: result[page]={'state':'unknown','reason':'pinned by capture date, not by commit; compare with refresh-research-sources'}; continue
        try:
            rows=json.loads((fetch or _get)(f'https://api.github.com/repos/{match[1]}/{match[2]}/commits?path={match[4]}&per_page=1'))
            head=rows[0]['sha']
            result[page]={'state':'current' if head==match[3] else 'stale','pinned':match[3],'upstream':head}
        except Exception:   # any failure to reach or read the source: the answer is unknown, not a guess
            result[page]={'state':'unknown','reason':'source unreachable','pinned':match[3]}
    return result
