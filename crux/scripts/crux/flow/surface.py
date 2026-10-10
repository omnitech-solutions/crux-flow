"""The surface record: what Crux Flow depends on, pinned, so a change in an essential is noticed.

One generated, committed file (`crux/surface/record.json`) pins the surface Flow stands on: every command and
flag of `crux-flow`; every upstream script Flow calls, with its flags, its seam functions and its exit codes;
every upstream symbol Flow imports; every skill with a digest of its frontmatter contract and the scripts its
body calls; every role's tools and delegation targets per host and mode; the schema versions Flow reads; the
drift roster; the upstream files the fork patches; the places that rewrite upstream text; the hooks.

`crux/surface/declaration.json` is the only authored input: what Flow requires, which changes are hard, what it
supports. Nothing here reads the network, runs project code or imports an upstream script (the scripts are read as
syntax trees), so the check is deterministic and safe in the ordinary suite.

Two verdicts, kept apart the way `check-drift` keeps them apart:
  DRIFT   the live surface differs from the record. Regenerate after reading the changes, which carry a severity.
  BROKEN  an invariant Flow needs does not hold. Regenerating cannot repair it; the named input does.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import json
from pathlib import Path
import re
import tempfile

import yaml

from .common import FlowError, canonical, decode, mapping
from . import hosts, managed, policy

DECLARATION='surface/declaration.json'
RECORD='surface/record.json'
MODES=('aggressive','balanced','thorough','upstream')
HOST_NAMES=('claude','codex','opencode','omp')
ROSTER_SKILL='skills/check-drift/SKILL.md'
SECTIONS=('flow_cli','upstream_scripts','imports','skills','roles','schemas','drift_roster','patched_upstream_files','text_rewrites','hooks')


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read(plugin: Path, name: str) -> bytes:
    raw=managed.read_file(plugin,name)
    if raw is None: raise FlowError(f'surface input missing: {name}')
    return raw


def load_declaration(plugin: Path) -> dict:
    data=mapping(decode(_read(plugin,DECLARATION)),
                 allowed={'schema_version','requires','dynamic_seams','supported_formats','flow_skills','delegation','hard_on_removal','patched_upstream_files','text_rewrites'},
                 required={'schema_version','requires','dynamic_seams','supported_formats','flow_skills','delegation','hard_on_removal','patched_upstream_files','text_rewrites'})
    if data['schema_version']!=1: raise FlowError('unknown surface declaration version')
    return data


# ── the Flow CLI ─────────────────────────────────────────────────────────────────────────────────

def flow_cli() -> dict:
    """Every command path with its flags (name, required, choices). Defaults are left out: they hold paths of the machine."""
    import argparse
    from . import cli
    result={}
    def walk(parser: argparse.ArgumentParser, path: tuple[str,...]) -> None:
        flags={}; positionals=[]
        for action in parser._actions:
            if isinstance(action,argparse._SubParsersAction):
                for name,child in sorted(action.choices.items()): walk(child,(*path,name))
                continue
            if isinstance(action,argparse._HelpAction): continue
            entry={'required':bool(action.required),'choices':sorted(map(str,action.choices)) if action.choices else None}
            if action.option_strings:
                for flag in action.option_strings: flags[flag]=entry
            else: positionals.append(action.dest)
        if path: result[' '.join(path)]={'flags':dict(sorted(flags.items())),'positionals':positionals}
    walk(cli.parser(),())
    return dict(sorted(result.items()))


# ── upstream scripts, read as syntax trees ───────────────────────────────────────────────────────

def _tree(plugin: Path, relative: str) -> ast.Module:
    return ast.parse(_read(plugin,relative).decode('utf-8'))


def script_surface(plugin: Path, name: str) -> dict:
    tree=_tree(plugin,'scripts/'+name)
    flags={}; functions={}
    for node in ast.walk(tree):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='add_argument':
            options=[a.value for a in node.args if isinstance(a,ast.Constant) and isinstance(a.value,str)]
            required=any(k.arg=='required' and isinstance(k.value,ast.Constant) and k.value.value is True for k in node.keywords)
            for option in options:
                flags[option]={'required':required or not option.startswith('-')}
    for node in tree.body:
        if isinstance(node,ast.FunctionDef): functions[node.name]=[a.arg for a in node.args.args]
    exits=set()
    for node in tree.body:
        if isinstance(node,ast.FunctionDef) and node.name=='main':
            for inner in ast.walk(node):
                if isinstance(inner,ast.Return) and isinstance(inner.value,ast.Constant) and isinstance(inner.value.value,int): exits.add(inner.value.value)
    return {'flags':dict(sorted(flags.items())),'functions':dict(sorted(functions.items())),'main_returns':sorted(exits)}


def upstream_scripts(plugin: Path, declaration: dict) -> dict:
    names=sorted({*declaration['requires'],*declaration['dynamic_seams']})
    return {name:script_surface(plugin,name) for name in names if (plugin/'scripts'/name).is_file()}   # a missing script is an invariant failure, not a crash


def _module_file(plugin: Path, dotted: str) -> str | None:
    base='scripts/'+dotted.replace('.','/')
    for candidate in (base+'.py',base+'/__init__.py'):
        if (plugin/candidate).is_file(): return candidate
    return None


def _top_names(tree: ast.Module) -> set[str]:
    names=set()
    for node in tree.body:
        if isinstance(node,(ast.FunctionDef,ast.ClassDef,ast.AsyncFunctionDef)): names.add(node.name)
        elif isinstance(node,ast.Assign):
            for target in node.targets:
                if isinstance(target,ast.Name): names.add(target.id)
        elif isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name): names.add(node.target.id)
        elif isinstance(node,(ast.Import,ast.ImportFrom)):
            for alias in node.names: names.add((alias.asname or alias.name).split('.')[0])
    return names


def imports(plugin: Path) -> dict:
    """Upstream modules Flow imports, with the symbols it uses from each and whether each still exists."""
    used={}
    for path in sorted((plugin/'scripts/crux/flow').glob('*.py')):
        tree=ast.parse(path.read_text(encoding='utf-8'))
        aliases={}
        for node in tree.body:
            if isinstance(node,ast.Import):
                for alias in node.names:
                    if _module_file(plugin,alias.name) and not alias.name.startswith('crux.flow'): aliases[alias.asname or alias.name]=alias.name
            elif isinstance(node,ast.ImportFrom) and node.level==0 and node.module and not node.module.startswith('crux.flow'):
                if _module_file(plugin,node.module):
                    for alias in node.names: used.setdefault(node.module,set()).add(alias.name)
        for node in ast.walk(tree):
            if isinstance(node,ast.Attribute) and isinstance(node.value,ast.Name) and node.value.id in aliases:
                used.setdefault(aliases[node.value.id],set()).add(node.attr)
    result={}
    for module,symbols in sorted(used.items()):
        present=_top_names(_tree(plugin,_module_file(plugin,module)))
        result[module]={'file':_module_file(plugin,module),'symbols':{s:(s in present) for s in sorted(symbols)}}
    return result


# ── skills, roles, schemas, roster ───────────────────────────────────────────────────────────────

def skills(plugin: Path, declaration: dict) -> dict:
    scripts={p.name for p in (plugin/'scripts').glob('*.py')}
    result={}
    for path in sorted((plugin/'skills').glob('*/SKILL.md')):
        raw=path.read_bytes().decode('utf-8'); pieces=raw.split('---',2)
        head=mapping(decode(pieces[1].encode())) if len(pieces)==3 else {}
        body=pieces[2] if len(pieces)==3 else raw
        called=sorted({m for m in re.findall(r'([A-Za-z0-9_-]+\.py)',body) if m in scripts})
        result[path.parent.name]={'owner':'flow' if path.parent.name in declaration['flow_skills'] else 'upstream',
                                  'contract':_sha(canonical(head)),'scripts_called':called}
    return result


def _claude_signature(tools: str) -> dict:
    plain=[]; spawns=[]
    for part in re.split(r',\s*(?![^()]*\))',tools):
        spawn=re.fullmatch(r'Agent\((.*)\)',part.strip())
        if spawn: spawns+=[s.strip() for s in spawn[1].split(',')]
        elif part.strip(): plain.append(part.strip())
    return {'tools':sorted(plain),'spawns':sorted(spawns)}


def _signature(host: str, name: str, raw: bytes) -> dict:
    content=raw.decode('utf-8')
    if host=='codex':
        sandbox=re.search(r'^sandbox_mode = "([^"]+)"',content,re.M)
        return {'tools':[sandbox[1] if sandbox else 'unspecified'],'spawns':[]}
    head=mapping(decode(content.split('---',2)[1].encode()))
    if host=='claude': return _claude_signature(head.get('tools',''))
    if host=='omp': return {'tools':sorted(head.get('tools',[])),'spawns':sorted(head.get('spawns',[]))}
    allowed=[(r['action'],r['resource']) for r in head.get('permissions',[]) if r.get('effect')=='allow']
    return {'tools':sorted({a for a,_ in allowed if a!='subagent'}),'spawns':sorted(r for a,r in allowed if a=='subagent' and r!='*')}


def roles(plugin: Path) -> dict:
    """role -> host -> mode -> tools and delegation targets, as the generator renders them. Models are left out: they are
    the model catalog's business and move with every upstream release; what a role may do is the contract."""
    result={}
    with tempfile.TemporaryDirectory() as scratch:
        repo=Path(scratch)
        for host in HOST_NAMES:
            for mode in MODES:
                effective=policy.resolve(plugin,repo,repo/'home',host,{'mode':mode})
                rendered=hosts.roles(plugin,effective)['files']
                for file,raw in sorted(rendered.items()):
                    name=re.sub(r'^crux-flow-|\.(md|toml)$','',file)
                    signature=_signature(host,name,raw)
                    result.setdefault(name,{}).setdefault(host,{})[mode]=signature
    for hosts_ in result.values():       # collapse a host whose modes render the same role identically
        for host,by_mode in list(hosts_.items()):
            first=next(iter(by_mode.values()))
            if len(by_mode)==len(MODES) and all(v==first for v in by_mode.values()): hosts_[host]={'*':first}
    return dict(sorted(result.items()))


def schemas(plugin: Path) -> dict:
    run=json.loads(_read(plugin,'schemas/run.schema.json')); book=json.loads(_read(plugin,'schemas/promptbook.schema.json'))
    def versions(node: dict) -> list:
        spec=node['properties']['format_version']
        return sorted(spec['enum']) if 'enum' in spec else [spec['const']]
    flow=run['properties'].get('flow',{}).get('properties',{}).get('schema_version',{}).get('enum',[])
    config=_read(plugin,'scripts/bionic_config.py').decode()
    supported=re.search(r'^SUPPORTED_SCHEMA_VERSION = "([^"]+)"',config,re.M); older=re.search(r'^KNOWN_OLDER_SCHEMA_VERSIONS = \(([^)]*)\)',config,re.M)
    docs=re.search(r'^DEFAULT_DOCS_DIR = "([^"]+)"',config,re.M)
    definition=policy.load_definition(plugin)
    technology=json.loads(_read(plugin,'catalog/flow-technology.json'))
    return {'run_format_versions':versions(run),'promptbook_format_versions':versions(book),'flow_run_extension_versions':sorted(flow),
            'manifest_schema_version':supported[1] if supported else None,
            'manifest_older_versions':sorted(re.findall(r'"([^"]+)"',older[1])) if older else [],
            'docs_dir_default':docs[1] if docs else None,
            'flow_policy':{'policy_revision':definition['identity']['policy_revision'],'projection_version':definition['identity']['projection_version'],
                           'modes':sorted(definition['modes'])},
            'flow_technology_schema_version':technology['schema_version']}


def drift_roster(plugin: Path) -> list:
    """The scripts the check-drift skill runs, in its two tables (roster gates and guards)."""
    names=[]
    for line in _read(plugin,ROSTER_SKILL).decode().splitlines():
        cells=[c.strip() for c in line.split('|')]
        if len(cells)>=5 and ('--dry-run' in cells[3] or cells[3].startswith('`') and '.py' in cells[3]):
            match=re.search(r'`(?:uv run (?:--no-config )?)?([A-Za-z0-9_-]+\.py)',cells[3])
            if match: names.append(match[1])
    return sorted(set(names))


def patched_files(plugin: Path, declaration: dict) -> dict:
    return {name:_sha(_read(plugin,name)) for name in sorted(declaration['patched_upstream_files'])}


def text_rewrites(plugin: Path, declaration: dict) -> list:
    result=[]
    for row in declaration['text_rewrites']:
        present=row['marker'] in _read(plugin,row['file']).decode()
        result.append({'file':row['file'],'what':row['what'],'marker_present':present})
    return result


def hook_files(plugin: Path) -> list:
    found=[p.relative_to(plugin).as_posix() for p in sorted(plugin.glob('hooks/**/*')) if p.is_file()]
    for name in ('plugin.json','.claude-plugin/plugin.json','.codex-plugin/plugin.json'):
        path=plugin/name
        if path.is_file() and 'hooks' in json.loads(path.read_text(encoding='utf-8')): found.append(name+'#hooks')
    return found


def collect(plugin: Path) -> dict:
    plugin=plugin.resolve(); declaration=load_declaration(plugin); definition=policy.load_definition(plugin)['identity']
    return {'schema_version':1,
            'upstream':{'version':definition['upstream_version'],'commit':definition['upstream_commit'],
                        'plugin_json_version':json.loads(_read(plugin,'plugin.json'))['version']},
            'flow_cli':flow_cli(),'upstream_scripts':upstream_scripts(plugin,declaration),'imports':imports(plugin),
            'skills':skills(plugin,declaration),'roles':roles(plugin),'schemas':schemas(plugin),'drift_roster':drift_roster(plugin),
            'patched_upstream_files':patched_files(plugin,declaration),'text_rewrites':text_rewrites(plugin,declaration),'hooks':hook_files(plugin)}


# ── invariants: what must hold whatever the record says ──────────────────────────────────────────

def invariants(plugin: Path, live: dict, declaration: dict | None=None) -> list[str]:
    """BROKEN: every entry names an essential that does not hold. Regenerating the record cannot repair any of them."""
    declaration=declaration or load_declaration(plugin); failures=[]
    for script,flags in declaration['requires'].items():
        found=live['upstream_scripts'].get(script)
        if found is None: failures.append(f'upstream script {script} is gone'); continue
        for flag in flags:
            if flag not in found['flags']: failures.append(f'{script} no longer accepts {flag}, which Flow passes')
    for script,seams in declaration['dynamic_seams'].items():
        found=live['upstream_scripts'].get(script,{}).get('functions',{})
        for function,arguments in seams.items():
            if function not in found: failures.append(f'{script} lost the seam function {function}')
            elif found[function][:len(arguments)]!=arguments: failures.append(f'{script}:{function} arguments changed from {arguments} to {found[function]}')
    for module,row in live['imports'].items():
        for symbol,present in row['symbols'].items():
            if not present: failures.append(f'{module} no longer defines {symbol}, which Flow imports')
    for kind,key in (('run','run_format_versions'),('promptbook','promptbook_format_versions')):
        unknown=set(live['schemas'][key])-set(declaration['supported_formats'][kind])
        if unknown: failures.append(f'{kind} format_version {sorted(unknown)} is not one Flow reads (supports {declaration["supported_formats"][kind]})')
    if live['schemas']['manifest_schema_version'] not in declaration['supported_formats']['manifest']:
        failures.append(f'documentation manifest schema_version {live["schemas"]["manifest_schema_version"]} is not one Flow reads')
    if set(live['schemas']['flow_run_extension_versions'])-set(declaration['supported_formats']['flow_extension']):
        failures.append('the run schema carries a Flow extension version Flow does not read')
    for name in declaration['flow_skills']:
        if live['skills'].get(name,{}).get('owner')!='flow': failures.append(f'Flow skill {name} is missing')
    permitted_by_role={role:{'crux-flow-'+t for t in targets} for role,targets in declaration['delegation'].items()}
    for role,by_host in live['roles'].items():
        for host,by_mode in by_host.items():
            if host=='codex': continue          # a Codex role file has no delegation field to pin; its sandbox is pinned instead
            for mode,signature in by_mode.items():
                if mode=='upstream': continue    # upstream mode keeps the upstream roster by definition
                extra=set(signature['spawns'])-permitted_by_role.get(role,set())
                # One orchestrator, every delegate a leaf: in a Flow mode no generated role may hold a delegation target.
                if extra: failures.append(f'role {role} on {host} ({mode}) gained delegation to {sorted(extra)}; in a Flow mode every role is a leaf (Flow allows {sorted(permitted_by_role.get(role,[]))})')
    for row in live['text_rewrites']:
        if not row['marker_present']: failures.append(f'{row["file"]}: the text Flow rewrites ({row["what"]}) is no longer found')
    if 'generate-flow-surface.py' not in live['drift_roster']: failures.append('the check-drift roster has no surface row')
    return failures


# ── comparison ───────────────────────────────────────────────────────────────────────────────────

def _flatten(node, prefix=()) -> dict:
    """Leaves by path. A list of scalars becomes one leaf per item, so an added or removed item is reported alone."""
    if isinstance(node,list) and node and all(not isinstance(i,(dict,list)) for i in node):
        node={str(i):True for i in node}
    if isinstance(node,dict) and node:
        flat={}
        for key,value in node.items(): flat.update(_flatten(value,(*prefix,str(key))))
        return flat
    return {prefix:node}


def changes(recorded: dict, live: dict, declaration: dict) -> list[dict]:
    old=_flatten(recorded); new=_flatten(live); result=[]
    hard_sections=set(declaration['hard_on_removal'])
    for path in sorted(set(old)|set(new)):
        before=old.get(path,'<absent>'); after=new.get(path,'<absent>')
        if before==after: continue
        kind='added' if path not in old else 'removed' if path not in new else 'changed'
        # an empty list or mapping flattens to one leaf: when it fills up that leaf disappears, which is an addition, not a removal
        filled=kind=='removed' and before in ([],{}) and any(other[:len(path)]==path and len(other)>len(path) for other in new)
        severity='hard' if kind=='removed' and path[0] in hard_sections and not filled else 'notice'
        result.append({'section':path[0],'path':'/'.join(path),'kind':kind,'severity':severity,'recorded':before,'live':after})
    return result


def read_record(plugin: Path) -> dict | None:
    raw=managed.read_file(plugin,RECORD)
    return None if raw is None else json.loads(raw)


def check(plugin: Path) -> tuple[int, dict]:
    """(exit code, payload) in the sibling-regenerator contract: 0 clean, 1 drift or broken."""
    declaration=load_declaration(plugin); live=collect(plugin); recorded=read_record(plugin)
    broken=invariants(plugin,live,declaration)
    if broken: return 1,{'broken':True,'validation_errors':[{'field':'surface','error':e} for e in broken]}
    if recorded is None: return 1,{'drift':True,'changes':[],'reason':'no surface record; run generate-flow-surface.py'}
    found=changes(recorded,live,declaration)
    if found: return 1,{'drift':True,'hard':sum(c['severity']=='hard' for c in found),'notice':sum(c['severity']=='notice' for c in found),
                        'changes':found,'repair':'review the changes, then run generate-flow-surface.py'}
    return 0,{'drift':False,'upstream':live['upstream']['version']}


def sync(plugin: Path, destination: Path | None=None) -> tuple[int, dict]:
    """Write the record. Refuses while an invariant is broken: a record of a broken surface would bless it."""
    declaration=load_declaration(plugin); live=collect(plugin); broken=invariants(plugin,live,declaration)
    if broken: return 1,{'broken':True,'validation_errors':[{'field':'surface','error':e} for e in broken]}
    target=(destination or plugin)/RECORD
    text=json.dumps(live,indent=1,sort_keys=True)+'\n'
    previous=target.read_text(encoding='utf-8') if target.is_file() else None
    if previous!=text:
        target.parent.mkdir(parents=True,exist_ok=True); target.write_text(text,encoding='utf-8')
    return 0,{'drift':False,'written':previous!=text,'path':RECORD}


def render_diff(recorded: dict, live: dict) -> str:
    a=json.dumps(recorded,indent=1,sort_keys=True).splitlines(); b=json.dumps(live,indent=1,sort_keys=True).splitlines()
    return '\n'.join(difflib.unified_diff(a,b,'record.json','live surface',lineterm='',n=2))
