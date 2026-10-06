from __future__ import annotations

import json
from pathlib import Path
import re
import tomllib

from .common import FlowError, canonical, decode, mapping
from . import managed

# Repository activation: which workflow product owns a repository is decided by each host's
# own project-scoped plugin setting. Verified seams (see FLOW_SEAMS.md):
#   claude -> <repo>/.claude/settings.json   {"enabledPlugins": {"<plugin>@<marketplace>": bool}}
#   codex  -> <repo>/.codex/config.toml      [plugins."<plugin>@<marketplace>"] enabled = bool
FILES={'claude':'.claude/settings.json','codex':'.codex/config.toml'}
FLOW='crux-flow'; UPSTREAM='crux'
_IDENTIFIER=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*@[A-Za-z0-9][A-Za-z0-9._-]*\Z')


def receipt_name(host: str) -> str: return f'.crux-flow/receipts/{host}-activation.json'


def plugin_name(identifier: str) -> str: return identifier.partition('@')[0]


def select(identifiers: list[str]) -> tuple[str,list[str]]:
    """The one installed Flow identity and every installed upstream Crux identity."""
    flow=sorted({i for i in identifiers if plugin_name(i)==FLOW})
    if len(flow)>1: raise FlowError('ambiguous duplicate Flow installations; remove all but one before activating a repository')
    if not flow: raise FlowError('Flow is not installed for this host; run crux-flow setup first')
    return flow[0],sorted({i for i in identifiers if plugin_name(i)==UPSTREAM})


def _check(identifier: str) -> str:
    if _IDENTIFIER.fullmatch(identifier) is None: raise FlowError('unsupported plugin identity')
    return identifier


# ---- Codex: textual TOML edits (tomllib only reads), always re-parsed and verified ----------

def _toml(raw: bytes | None) -> dict:
    try: return tomllib.loads(raw.decode() if raw else '')
    except (tomllib.TOMLDecodeError,UnicodeDecodeError) as exc: raise FlowError('project Codex configuration is not valid TOML') from exc


def _toml_value(raw: bytes | None,identifier: str) -> bool | None:
    plugins=_toml(raw).get('plugins',{})
    row=plugins.get(identifier) if isinstance(plugins,dict) else None
    if row is None: return None
    value=row.get('enabled') if isinstance(row,dict) else None
    if value is not None and not isinstance(value,bool): raise FlowError('unsupported plugin setting value')
    return value


def _table(text: str,identifier: str) -> tuple[int,int] | None:
    header=re.search(r'^[ \t]*\[plugins\.[ \t]*"'+re.escape(identifier)+r'"[ \t]*\][ \t]*(?:#.*)?$',text,re.M)
    if header is None: return None
    following=re.compile(r'^[ \t]*\[',re.M).search(text,header.end())
    return header.start(),(following.start() if following else len(text))


def _toml_set(raw: bytes | None,identifier: str,value: bool | None) -> bytes | None:
    text=raw.decode() if raw else ''; literal='true' if value else 'false'; span=_table(text,identifier)
    if value is None:
        if span is None: return raw
        block=text[span[0]:span[1]]
        trimmed=re.sub(r'^[ \t]*enabled[ \t]*=[ \t]*(?:true|false)[ \t]*(?:#.*)?\n?','',block,count=1,flags=re.M)
        remaining=[l for l in trimmed.splitlines()[1:] if l.strip() and not l.strip().startswith('#')]
        last=span[1]==len(text)
        text=text[:span[0]]+('' if not remaining else trimmed)+text[span[1]:]
        if last and not remaining: text=text.rstrip('\n')+'\n' if text.strip() else ''
        text=re.sub(r'\n{3,}','\n\n',text).lstrip('\n')
    elif span is None:
        if identifier in _toml(raw).get('plugins',{}): raise FlowError('existing plugin setting uses an unsupported TOML layout')
        gap='' if not text or text.endswith('\n\n') else '\n' if text.endswith('\n') else '\n\n'
        text=text+gap+f'[plugins."{identifier}"]\nenabled = {literal}\n'
    else:
        block=text[span[0]:span[1]]
        line=re.compile(r'^([ \t]*enabled[ \t]*=[ \t]*)(true|false)([ \t]*(?:#.*)?)$',re.M)
        if line.search(block): block=line.sub(lambda m:m[1]+literal+m[3],block,count=1)
        else:
            head,_,rest=block.partition('\n'); block=head+'\n'+f'enabled = {literal}\n'+rest
        text=text[:span[0]]+block+text[span[1]:]
    out=text.encode()
    if _toml_value(out,identifier)!=value: raise FlowError('project Codex configuration edit did not verify')
    return out


# ---- Claude: JSON settings --------------------------------------------------------------------

def _json(raw: bytes | None) -> dict:
    if not raw: return {}
    data=mapping(decode(raw))
    if not isinstance(data.get('enabledPlugins',{}),dict): raise FlowError('enabledPlugins is not an object')
    return data


def _json_value(raw: bytes | None,identifier: str) -> bool | None:
    value=_json(raw).get('enabledPlugins',{}).get(identifier)
    if value is not None and not isinstance(value,bool): raise FlowError('unsupported plugin setting value')
    return value


def _json_set(raw: bytes | None,identifier: str,value: bool | None) -> bytes | None:
    data=_json(raw); plugins=dict(data.get('enabledPlugins',{}))
    if value is None: plugins.pop(identifier,None)
    else: plugins[identifier]=value
    if plugins: data['enabledPlugins']=plugins
    else: data.pop('enabledPlugins',None)
    return (json.dumps(data,indent=2)+'\n').encode()


def _read(host: str,raw: bytes | None,identifier: str) -> bool | None:
    return _json_value(raw,identifier) if host=='claude' else _toml_value(raw,identifier)


def _write(host: str,raw: bytes | None,identifier: str,value: bool | None) -> bytes | None:
    return _json_set(raw,identifier,value) if host=='claude' else _toml_set(raw,identifier,value)


def write_setting(host: str,raw: bytes | None,identifier: str,value: bool | None) -> bytes | None:
    return _write(host,raw,_check(identifier),value)


def _owned_receipt(repo: Path,host: str) -> dict | None:
    raw=managed.read_file(repo,receipt_name(host))
    if raw is None: return None
    row=mapping(decode(raw),required={'schema_version','host','file','file_existed','entries'})
    if row['schema_version']!=1 or row['host']!=host or row['file']!=FILES[host]: raise FlowError('activation receipt is not owned by this host')
    for key,cell in mapping(row['entries']).items(): _check(key); mapping(cell,required={'before','after'})
    return row


def plan(host: str,repo: Path,flow: str,upstream: list[str]) -> tuple[dict[str,bytes | None],dict]:
    """Changes that make `flow` the one active workflow plugin in this repository.

    Idempotent: returns no changes when the repository is already correct. Refuses to overwrite a
    setting that was edited after Flow recorded it."""
    if host not in FILES: raise FlowError('host has no repository plugin activation seam')
    name=FILES[host]; raw=managed.read_file(repo,name); previous=_owned_receipt(repo,host)
    desired={_check(flow):True,**{_check(i):False for i in upstream}}
    entries={}
    for key,cell in (previous['entries'] if previous else {}).items():
        if _read(host,raw,key)!=cell['after']: raise FlowError('managed repository activation was edited locally; refusing to overwrite')
        entries[key]=cell
    current=raw
    for key,value in desired.items():
        before=entries[key]['before'] if key in entries else _read(host,raw,key)
        entries[key]={'before':before,'after':value}
        if _read(host,current,key)!=value: current=_write(host,current,key,value)
    receipt={'schema_version':1,'host':host,'file':name,'file_existed':previous['file_existed'] if previous else raw is not None,'entries':entries}
    changes={}
    if current!=raw: changes[name]=current
    encoded=canonical(receipt)
    if encoded!=managed.read_file(repo,receipt_name(host)): changes[receipt_name(host)]=encoded
    return changes,receipt


def removal(host: str,repo: Path) -> dict[str,bytes | None]:
    """Restore only the settings Flow recorded; foreign settings and local edits are never touched."""
    previous=_owned_receipt(repo,host)
    if previous is None: raise FlowError('owned repository activation missing')
    name=FILES[host]; raw=managed.read_file(repo,name); current=raw
    for key,cell in previous['entries'].items():
        if _read(host,raw,key)!=cell['after']: raise FlowError('repository activation was edited locally; refusing to restore over it')
        current=_write(host,current,key,cell['before'])
    changes={receipt_name(host):None}
    empty=(not current or (host=='claude' and not _json(current)) or (host=='codex' and not _toml(current)))
    changes[name]=None if empty and not previous['file_existed'] else current
    return changes


def observed(host: str,repo: Path) -> dict:
    """Static view of what the repository file says (not the host's runtime)."""
    raw=managed.read_file(repo,FILES[host]); receipt=_owned_receipt(repo,host)
    return {'file':FILES[host],'owned':receipt is not None,
            'settings':{k:_read(host,raw,k) for k in (receipt['entries'] if receipt else {})}}


def trusted(host_home: Path,repo: Path) -> bool | None:
    """Codex ignores project configuration in untrusted repositories. None means unknown."""
    try: data=tomllib.loads((host_home/'config.toml').read_text())
    except (OSError,tomllib.TOMLDecodeError): return None
    projects=data.get('projects',{})
    candidates=[repo,*repo.parents]
    return any(isinstance(projects.get(str(p)),dict) and projects[str(p)].get('trust_level')=='trusted' for p in candidates)
