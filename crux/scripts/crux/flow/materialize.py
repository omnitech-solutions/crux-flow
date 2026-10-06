from __future__ import annotations

import io
from pathlib import Path

from ruamel.yaml import YAML

from .common import FlowError, canonical, decode, digest, mapping
from . import hosts, managed


def _name(root: Path,path: Path) -> str:
    try: return managed.relative(path.relative_to(root).as_posix())
    except ValueError: raise FlowError('projection lies outside its selected writable scope') from None


def updates(plugin: Path, effective: dict, *, repo: Path,home: Path,scope: str,
            include_skills: bool=False,host_home: Path | None=None,engine: Path | None=None) -> tuple[Path,dict[str,bytes | None],dict]:
    from .activation import guard_project
    guard_project(repo,effective)
    root=(repo if scope=='project' else home).resolve()
    host=effective['host']; native=hosts.paths(host,scope,repo=repo,home=home,host_home=host_home)
    receipt_name=f'.crux-flow/receipts/{host}-{scope}-projection.json'
    receipt_raw=managed.read_file(root,receipt_name)
    previous=mapping(decode(receipt_raw)) if receipt_raw else {'files':{},'aliases':{}}
    if previous.get('host',host)!=host or previous.get('scope',scope)!=scope:
        raise FlowError('projection receipt scope mismatch')
    rendered=hosts.roles(plugin,effective,engine=engine)
    proposed={_name(root,native['agents']/name):raw for name,raw in rendered['files'].items()}
    if include_skills:
        for name,raw in hosts.skills(plugin,host,engine=engine or plugin.resolve()).items():
            pieces=name.split('/',1)
            proposed[_name(root,native['skills']/('crux-flow-'+pieces[0])/pieces[1])]=raw
    changes={}
    old_files=mapping(previous['files'])
    for name,expected in old_files.items():
        if not _owned(root,name,native): raise FlowError('invalid path in projection receipt')
        if digest(managed.read_file(root,name))!=expected:
            raise FlowError('managed projection has local changes')
        if name not in proposed:
            if not include_skills and (root/name).is_relative_to(native['skills']): proposed[name]=managed.read_file(root,name)
            else: changes[name]=None
    for name,raw in proposed.items():
        current=managed.read_file(root,name)
        if current is not None and name not in old_files: raise FlowError('foreign projection path cannot be adopted')
        if current!=raw: changes[name]=raw
    aliases={}
    if host=='omp':
        preferred=native['base']/'config.yml'; alternate=native['base']/'config.yaml'
        if not preferred.exists() and alternate.exists(): preferred=alternate
        config_name=_name(root,preferred)
        raw=managed.read_file(root,config_name)
        if raw is not None: mapping(decode(raw))
        codec=YAML(); codec.preserve_quotes=True
        data=codec.load(raw.decode()) if raw else {}
        if data is None: data={}
        roles=data.get('modelRoles',{})
        mapping(roles)
        dirty=False
        for key,value in effective['omp_aliases'].items():
            old=previous.get('aliases',{}).get(key)
            if old is not None:
                if roles.get(key)!=old['after']: raise FlowError('managed OMP alias edited locally')
                aliases[key]={'before':old['before'],'after':value}
                if roles.get(key)!=value: roles[key]=value; dirty=True
            elif key not in roles:
                aliases[key]={'before':None,'after':value}; roles[key]=value; dirty=True
        if dirty:
            data['modelRoles']=roles
            out=io.StringIO(); codec.dump(data,out); changes[config_name]=out.getvalue().encode()
        config_info={'config':config_name,'config_existed':previous.get('config_existed',raw is not None)}
    else: config_info={}
    receipt={'schema_version':1,'host':host,'scope':scope,'engine':str((engine or plugin).resolve()),
             'files':{k:digest(v) for k,v in proposed.items()},'aliases':aliases,**config_info}
    new=canonical(receipt)
    if new!=receipt_raw: changes[receipt_name]=new
    return root,changes,{'unsupported_controls':rendered['unsupported'],'runtime_loaded':'unobserved','receipt':receipt_name}


def _owned(root: Path,name: str,native: dict) -> bool:
    managed.relative(name)
    path=root/name
    if path.parent==native['agents']:
        return path.name.startswith('crux-flow-') and path.suffix in {'.md','.toml'}
    if path.is_relative_to(native['skills']):
        relative=path.relative_to(native['skills'])
        return len(relative.parts)>=2 and relative.parts[0].startswith('crux-flow-')
    return False


def apply(plugin: Path,effective: dict,*,repo: Path,home: Path,scope: str,include_skills: bool=False,host_home: Path | None=None) -> dict:
    root,changes,report=updates(plugin,effective,repo=repo,home=home,scope=scope,include_skills=include_skills,host_home=host_home)
    outcome=managed.apply(managed.plan(root,changes,owner='host-projection'))
    return {**outcome,**report}


def removal(root: Path,host: str,scope: str,*,repo: Path,home: Path,host_home: Path | None=None) -> dict[str,bytes | None]:
    receipt_name=f'.crux-flow/receipts/{host}-{scope}-projection.json'
    raw=managed.read_file(root,receipt_name)
    if raw is None: raise FlowError('owned projection receipt missing')
    receipt=mapping(decode(raw)); native=hosts.paths(host,scope,repo=repo,home=home,host_home=host_home)
    changes={}
    for name,expected in mapping(receipt['files']).items():
        if not _owned(root,name,native) or digest(managed.read_file(root,name))!=expected:
            raise FlowError('projection cannot be removed safely')
        changes[name]=None
    if receipt.get('aliases'):
        config_name=receipt['config']; managed.relative(config_name)
        if root/config_name not in {native['base']/'config.yml',native['base']/'config.yaml'}:
            raise FlowError('invalid OMP configuration receipt')
        raw=managed.read_file(root,config_name)
        if raw is None: raise FlowError('managed OMP configuration missing')
        mapping(decode(raw)); codec=YAML(); codec.preserve_quotes=True; data=codec.load(raw.decode())
        roles=mapping(data.get('modelRoles',{}))
        for key,cell in mapping(receipt['aliases']).items():
            if key not in {'default','slow','smol'} or roles.get(key)!=cell['after']:
                raise FlowError('managed alias changed since installation')
            if cell['before'] is None: roles.pop(key)
            else: roles[key]=cell['before']
        if not roles: data.pop('modelRoles',None)
        if not data and not receipt.get('config_existed'): changes[config_name]=None
        else:
            out=io.StringIO(); codec.dump(data,out); changes[config_name]=out.getvalue().encode()
    changes[receipt_name]=None
    return changes
