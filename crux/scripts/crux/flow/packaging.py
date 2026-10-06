from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import zipfile

from .common import FlowError, canonical, decode, digest, identity, mapping
from . import hosts, managed, policy, provenance

LAUNCHER='''#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["PyYAML>=6.0,<7", "ruamel.yaml>=0.18,<0.19", "httpx>=0.27,<1"]
# ///
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine/scripts"))
from crux.flow.cli import main
raise SystemExit(main())
'''


def _files(root: Path):
    for path in sorted(root.rglob('*')):
        if path.is_symlink(): raise FlowError('package source includes a symlink')
        if path.is_file(): yield path,path.relative_to(root).as_posix()


def _put(root: Path,name: str,content: bytes,mode: int=0o644):
    managed.relative(name)
    path=root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(content); path.chmod(mode)


def build(plugin: Path,output: Path) -> dict:
    if output.exists(): raise FlowError('package output must be a new directory')
    definition=policy.load_definition(plugin); names=definition['identity']; name=names['distribution']
    if not re.fullmatch('[a-z][a-z0-9-]*',name): raise FlowError('unsafe distribution identity')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='crux-package-',dir=output.parent) as temporary:
        root=Path(temporary)/'release'; root.mkdir(); payload=root/name; engine=payload/'engine'
        engine.mkdir(parents=True)
        for directory in provenance.RUNTIME_DIRS:
            source=plugin/directory
            if not source.exists(): continue
            for path,relative in _files(source):
                if any(part in {'tests','__pycache__','.pytest_cache','.git'} for part in Path(relative).parts) or path.suffix=='.pyc': continue
                data=managed.read_file(plugin,path.relative_to(plugin).as_posix())
                if data is None: raise FlowError('source changed during package build')
                _put(engine,directory+'/'+relative,data,0o755 if path.stat().st_mode & 0o111 else 0o644)
        _put(engine,'plugin.json',managed.read_file(plugin,'plugin.json') or b'{}')
        license_=managed.read_file(plugin.parent,'LICENSE')
        if license_ is None: raise FlowError('upstream license is required in the package')
        _put(payload,'LICENSE',license_)
        guide = managed.read_file(plugin, 'FLOW_GUIDE.md') or managed.read_file(plugin.parent, 'FLOW_GUIDE.md')
        if guide is None: raise FlowError('version-matched operator guide is required in the package')
        _put(engine, 'FLOW_GUIDE.md', guide)
        for skill,raw in hosts.skills(plugin,'codex').items(): _put(payload,'skills/'+skill,raw)
        effective=policy.resolve(plugin,Path(temporary)/'empty',Path(temporary)/'home','claude')
        for filename,raw in hosts.roles(plugin,effective)['files'].items():
            _put(payload,'agents/'+filename,raw)
        source_commit=provenance.git_head(plugin.parent)
        source_info=managed.read_file(plugin.parent,'reference-source.json')
        metadata={'schema_version':1,'identity':names,'source_commit':source_commit,
                  'source_kind':'git-checkout' if source_commit else 'reconstructed-reference',
                  'runtime_digest':provenance.runtime_digest(engine),
                  'lineage':decode(source_info) if source_info else None}
        _put(engine,'release.json',canonical(metadata))
        _put(payload,'plugin.json',canonical({'$schema':'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json',
             'name':name,'version':names['version'],'description':'Multi-mode Crux fork with shared records, model maintenance and scoped host integration',
             'author':{'name':'Omnitech Solutions'},'repository':'https://github.com/omnitech-solutions/crux'}))
        _put(payload,'.claude-plugin/plugin.json',canonical({'name':name,'version':names['version'],
             'description':'Multi-mode Crux fork','author':{'name':'Omnitech Solutions'}}))
        _put(payload,'.codex-plugin/plugin.json',canonical({'name':name,'version':names['version'],
             'description':'Multi-mode Crux fork','skills':'./skills/'}))
        _put(payload,'bin/crux-flow',LAUNCHER.encode(),0o755)
        compatibility=LAUNCHER.replace('from crux.flow.cli import main','from crux.flow.cli import compatibility as main')
        _put(payload,'bin/crux-local',compatibility.encode(),0o755)
        _put(payload,'inventory.json',canonical({'identity':names,'modes':definition['modes'],
             'hosts':{host:{'packaged':True,'runtime_loaded':'unobserved','native_delegation':'cooperative'} for host in hosts.HOSTS}}))
        _put(root,'.claude-plugin/marketplace.json',canonical({'name':names['marketplace'],'owner':{'name':'Omnitech Solutions'},
             'plugins':[{'name':name,'source':'./'+name,'version':names['version']}]}))
        _put(root,'.agents/plugins/marketplace.json',canonical({'name':names['marketplace'],'plugins':[{'name':name,
             'source':{'source':'local','path':'./'+name},'policy':{'installation':'AVAILABLE','authentication':'ON_INSTALL'},'category':'Productivity'}]}))
        manifest={rel:{'sha256':digest(path.read_bytes()),'mode':stat.S_IMODE(path.stat().st_mode)} for path,rel in _files(root)}
        _put(root,'release-manifest.json',canonical({'schema_version':1,'distribution':name,'files':manifest,'release':metadata}))
        inspect(root)
        archive=Path(temporary)/f'{name}-{names["version"]}.zip'
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
            for path,relative in _files(root):
                info=zipfile.ZipInfo(relative,(1980,1,1,0,0,0)); info.create_system=3
                info.external_attr=(stat.S_IFREG|stat.S_IMODE(path.stat().st_mode))<<16
                info.compress_type=zipfile.ZIP_DEFLATED
                z.writestr(info,path.read_bytes())
        output.mkdir(); shutil.move(str(root),output/'release'); shutil.move(str(archive),output/archive.name)
    return {'root':str(output/'release'),'payload':str(output/'release'/name),'archive':str(output/archive.name),
            'sha256':digest((output/archive.name).read_bytes()),'source_commit':source_commit,'runtime_loaded':'unobserved'}


def inspect(root: Path) -> dict:
    raw=managed.read_file(root,'release-manifest.json')
    if raw is None: raise FlowError('release manifest missing')
    manifest=mapping(decode(raw),required={'schema_version','distribution','files','release'})
    actual={name for _,name in _files(root)}-{'release-manifest.json'}
    if actual!=set(manifest['files']): raise FlowError('release content set differs from manifest')
    for name,cell in mapping(manifest['files']).items():
        managed.relative(name)
        mapping(cell, allowed={'sha256', 'mode'}, required={'sha256', 'mode'})
        if type(cell['mode']) is not int or cell['mode'] not in {0o600, 0o644, 0o755}:
            raise FlowError('unsupported packaged file mode')
        if stat.S_IMODE((root / name).stat().st_mode) != cell['mode']:
            raise FlowError('release payload permission mismatch')
        if digest(managed.read_file(root,name))!=cell['sha256']: raise FlowError('release payload hash mismatch')
    name=manifest['distribution']
    if not re.fullmatch('[a-z][a-z0-9-]*',name): raise FlowError('invalid package identity')
    portable=mapping(decode(managed.read_file(root,name+'/plugin.json') or b''))
    claude=mapping(decode(managed.read_file(root,name+'/.claude-plugin/plugin.json') or b''))
    if portable.get('name')!=name or claude.get('name')!=name or portable.get('version')!=claude.get('version'):
        raise FlowError('native manifests disagree')
    engine=root/name/'engine'
    if provenance.runtime_digest(engine)!=manifest['release']['runtime_digest']: raise FlowError('runtime source digest differs')
    claude_market=mapping(decode(managed.read_file(root,'.claude-plugin/marketplace.json') or b''))
    codex_market=mapping(decode(managed.read_file(root,'.agents/plugins/marketplace.json') or b''))
    if claude_market['plugins'][0]['source']!='./'+name or codex_market['plugins'][0]['source']!={'source':'local','path':'./'+name}:
        raise FlowError('marketplace points outside its declared payload')
    return {'root':str(root.resolve()),'payload':str((root/name).resolve()),'engine':str(engine.resolve()),
            'release':manifest['release'],'digest':identity(manifest),'statically_valid':True,'runtime_loaded':'unobserved'}


def extract(archive: Path,destination: Path) -> Path:
    if destination.exists(): raise FlowError('extraction destination already exists')
    with zipfile.ZipFile(archive) as z:
        infos=z.infolist()
        if len(infos)>10000 or sum(i.file_size for i in infos)>150_000_000: raise FlowError('release archive exceeds safe bounds')
        names=set()
        for info in infos:
            managed.relative(info.filename.rstrip('/'))
            if info.filename in names or info.flag_bits & 1: raise FlowError('duplicate or encrypted archive entry')
            names.add(info.filename)
            mode=info.external_attr>>16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0,stat.S_IFREG,stat.S_IFDIR)): raise FlowError('unsupported archive entry type')
        destination.mkdir(parents=True)
        try:
            for info in infos:
                if info.is_dir(): continue
                raw=z.read(info)
                if len(raw)!=info.file_size: raise FlowError('archive entry size mismatch')
                _put(destination,info.filename,raw,0o755 if (info.external_attr>>16)&0o111 else 0o644)
            inspect(destination)
        except BaseException:
            shutil.rmtree(destination)
            raise
    return destination


def build_default(plugin: Path, cache_root: Path) -> dict:
    definition = policy.load_definition(plugin)
    guide = managed.read_file(plugin, 'FLOW_GUIDE.md') or managed.read_file(plugin.parent, 'FLOW_GUIDE.md')
    key = identity({'runtime':provenance.runtime_digest(plugin), 'guide':digest(guide),
                    'license':digest(managed.read_file(plugin.parent, 'LICENSE')),
                    'lineage':digest(managed.read_file(plugin.parent, 'reference-source.json')),
                    'source_commit':provenance.git_head(plugin.parent)})
    output = cache_root / key
    if not output.exists():
        return build(plugin, output)
    observed = inspect(output / 'release')
    name = definition['identity']['distribution']
    archive = output / f'{name}-{definition["identity"]["version"]}.zip'
    with tempfile.TemporaryDirectory(prefix='crux-cached-release-') as temporary:
        extracted = extract(archive, Path(temporary) / 'release')
        if inspect(extracted)['digest'] != observed['digest']:
            raise FlowError('cached archive differs from the verified release')
    return {'root':str(output / 'release'), 'payload':str(output / 'release' / name),
            'archive':str(archive), 'sha256':digest(archive.read_bytes()),
            'source_commit':observed['release']['source_commit'], 'runtime_loaded':'unobserved', 'reused':True}
