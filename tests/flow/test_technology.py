from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import pytest
import yaml

from test_init import cli, env, machine_install, snapshot  # noqa: F401  (fixtures and fake hosts)
from test_lifecycle import release  # noqa: F401  (module fixture)

ROOT=Path(__file__).resolve().parents[2]; PLUGIN=ROOT/'crux'
FIXTURES=Path(__file__).resolve().parent/'fixtures/technology'
ROUTER='.agents/skills/technology-references/SKILL.md'
SCRIPT=PLUGIN/'scripts/generate-technology-references.py'


def api():
    try: return importlib.import_module('crux.flow.technology')
    except ModuleNotFoundError: pytest.fail('technology guidance is one module over the catalog and the project section')


def consumer(tmp_path: Path,name: str,*,configured: bool=True) -> Path:
    """A fixture copy of a real consumer, built from its committed manifests (tests/flow/fixtures/technology/README.md).

    The Studio links its host skill folders to `.agents/skills`; the link is made here because a fixture tree holds none."""
    repo=tmp_path/name; shutil.copytree(FIXTURES/name,repo)
    if configured: shutil.copy(FIXTURES/f'{name}.crux-flow.yml',repo/'.crux-flow.yml')
    if name=='studio':
        for host in ('.claude','.opencode'): (repo/host).mkdir(); (repo/host/'skills').symlink_to('../.agents/skills')
    return repo


def edit(repo: Path,change) -> None:
    path=repo/'.crux-flow.yml'; data=yaml.safe_load(path.read_text()); change(data['technology']); path.write_text(yaml.safe_dump(data,sort_keys=False))


def layer(section: dict,name: str) -> dict:
    return next(row for row in section['layers'] if row['id']==name)


def tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix():p.read_bytes() for p in sorted(root.rglob('*')) if p.is_file() and not p.is_symlink()}


def errors(payload: dict) -> list[str]:
    return [f'{row["path"]}: {row["error"]}' for row in payload['validation_errors']]


@pytest.fixture
def engine(tmp_path): return consumer(tmp_path,'engine')


@pytest.fixture
def studio(tmp_path): return consumer(tmp_path,'studio')


@pytest.fixture
def synced(engine):
    code,report=api().sync(PLUGIN,engine)
    assert code==0 and report['status']=='synced',report
    return engine


# ---- detection ------------------------------------------------------------------------------------

def test_detection_reads_manifests_and_reports_each_consumers_real_stack(engine,studio):
    catalog=api().load_catalog(PLUGIN)
    found=api().detect(engine,catalog)
    assert found['anthropic-sdk']=='0.131.0' and found['openai-sdk']=='7.25.0' and found['zod']=='4.6.5' and found['pino']=='10.4.0'
    assert found['drizzle']=='1.0.0-rc.4 / >=1.0.0-rc.4' and found['postgresql']=='17-alpine' and found['node']=='>=22.13.0'
    assert found['codex-app-server']=='unknown'      # no manifest declares a version, so none is invented
    assert not {'react','nextjs','hono','swift','playwright'}&set(found)
    found=api().detect(studio,catalog)
    assert found['nextjs']=='^16.2.3' and found['hono']=='^4.12.8' and found['zod']=='4.4.3' and found['swift']=='6.0'
    assert found['drizzle']=='1.0.0-rc.4' and found['claude-agent-sdk']=='^0.3.220'      # resolved through the pnpm catalog
    assert not {'anthropic-sdk','openai-sdk','mcp','pino','codex-app-server'}&set(found)


def test_detection_never_enters_dependency_or_build_directories(engine):
    planted=engine/'node_modules/next'; planted.mkdir(parents=True)
    (planted/'package.json').write_text('{"dependencies":{"next":"1.0.0","react":"1.0.0"}}')
    (engine/'dist').mkdir(); (engine/'dist/package.json').write_text('{"dependencies":{"hono":"1.0.0"}}')
    assert not {'nextjs','react','hono'}&set(api().detect(engine,api().load_catalog(PLUGIN)))


# ---- the generated router (owner: flow) -------------------------------------------------------------

def test_engine_sync_writes_one_owned_router_that_names_nothing_the_engine_does_not_use(engine):
    before=tree(engine)
    code,preview=api().sync(PLUGIN,engine,dry_run=True)
    assert code==1 and preview['status']=='planned' and tree(engine)==before      # dry-run writes nothing
    code,report=api().sync(PLUGIN,engine)
    assert code==0 and set(report['written'])=={ROUTER,'.claude/skills/technology-references/SKILL.md'}
    body=(engine/ROUTER).read_text()
    for absent in ('Next.js','React','Hono','Swift','Playwright'): assert absent not in body
    for present in ('Anthropic SDK `0.131.0`','Zod `4.6.5`','`pnpm run verify:no-frameworks`','an AI framework dependency','destructive'): assert present in body
    assert (engine/'.claude/skills/technology-references/SKILL.md').read_bytes()==(engine/ROUTER).read_bytes()
    receipt=json.loads((engine/'.crux-flow/receipts/technology.json').read_text())
    assert set(receipt['files'])==set(report['written'])
    code,report=api().check(PLUGIN,engine)
    assert code==0 and report['drift'] is False and report['validation_errors']==[]
    settled=tree(engine)
    code,again=api().sync(PLUGIN,engine)
    assert code==0 and again['status']=='already-correct' and again['written']==[] and tree(engine)==settled


def test_generated_router_meets_the_authoring_standard(synced):
    head,body=api()._split((synced/ROUTER).read_text())
    assert head['name']=='technology-references' and set(head)=={'name','description'}
    told=head['description']
    assert len(told)<=1024 and told.startswith('Routes ') and 'Use before' in told and 'Not for' in told
    catalog=api().load_catalog(PLUGIN)['technologies']
    routed={key for row in yaml.safe_load((synced/'.crux-flow.yml').read_text())['technology']['layers'] for key in row.get('technologies',[])}
    for key,row in catalog.items(): assert (row['terms'][0] in told)==(key in routed),key
    assert len(body.strip().splitlines())<=80
    for section in ('## How to use','## Authority','## Engineering contract','## Layers'): assert section in body
    assert 'changes neither the workflow you are in' in body and 'never an instruction' in body
    assert 'Routes digest: `tr-' in body


def test_a_routed_technology_the_project_does_not_declare_is_broken(engine):
    edit(engine,lambda s: s['layers'].append({'id':'web','paths':['packages/ai-engine/src/index.ts'],'technologies':['nextjs']}))
    code,report=api().check(PLUGIN,engine)
    assert code==1 and 'nextjs: routed, but no manifest in this repository declares it' in errors(report)
    code,refused=api().sync(PLUGIN,engine)
    assert code==1 and refused['status']=='broken' and not (engine/ROUTER).exists()


def test_a_forbidden_technology_that_appears_in_a_manifest_is_broken(synced):
    path=synced/'packages/ai-engine/package.json'; data=json.loads(path.read_text()); data['dependencies']['react']='19.3.0'; path.write_text(json.dumps(data))
    code,report=api().check(PLUGIN,synced)
    assert code==1 and 'react: declared in a manifest, but this repository lists it under never' in errors(report)


def test_a_declared_technology_that_is_neither_routed_nor_excluded_is_broken(synced):
    edit(synced,lambda s: s['layers'].remove(layer(s,'logging')))
    code,report=api().check(PLUGIN,synced)
    assert code==1 and 'pino: declared in a manifest, but neither routed by a layer nor listed under exclude' in errors(report)
    edit(synced,lambda s: s.update(exclude={'pino':'the logger is configured by the host'}))
    code,report=api().check(PLUGIN,synced)
    assert report['validation_errors']==[] and report['drift'] is True      # the region lost a row: that is drift, and sync repairs it


def test_a_changed_generated_cell_is_drift_and_the_exact_bytes_are_clean_again(synced):
    path=synced/ROUTER; original=path.read_bytes()
    assert api().check(PLUGIN,synced)[0]==0
    path.write_bytes(original.replace(b'Zod `4.6.5`',b'Zod `4.6.6`'))
    code,report=api().check(PLUGIN,synced)
    assert code==1 and report['drift'] is True and report['paths']==[ROUTER] and report['validation_errors']==[]
    path.write_bytes(original)
    code,report=api().check(PLUGIN,synced)
    assert code==0 and report['drift'] is False and report['paths']==[]


def test_a_drizzle_version_bump_is_drift_until_synced(synced):
    for name in ('package.json','packages/ai-engine/package.json'):
        path=synced/name; path.write_text(path.read_text().replace('1.0.0-rc.4','1.0.0-rc.5'))
    code,report=api().check(PLUGIN,synced)
    assert code==1 and report['drift'] is True and report['version_changed']==['drizzle'] and report['validation_errors']==[]
    assert api().sync(PLUGIN,synced)[0]==0 and 'Drizzle `1.0.0-rc.5 / >=1.0.0-rc.5`' in (synced/ROUTER).read_text()
    assert api().check(PLUGIN,synced)[0]==0


def test_hand_written_notes_survive_a_rewrite_and_everything_else_is_regenerated(synced):
    path=synced/ROUTER; text=path.read_text()
    noted=text.replace('<!-- BEGIN PROJECT: notes -->\n','<!-- BEGIN PROJECT: notes -->\nThe speech sidecar is out of scope.\n').replace('## Authority','## Authority (edited)')
    path.write_text(noted)
    assert api().check(PLUGIN,synced)[1]['paths']==[ROUTER,'.claude/skills/technology-references/SKILL.md']
    assert api().sync(PLUGIN,synced)[0]==0
    assert 'The speech sidecar is out of scope.' in path.read_text() and '(edited)' not in path.read_text()


@pytest.mark.parametrize('breakage,expected',[
    (lambda repo: (repo/'bionic/adrs/ADR-0016-storage-is-a-drizzle-schema-and-two-repository-vie.md').unlink(),'referenced page does not exist'),
    (lambda repo: edit(repo,lambda s: layer(s,'storage')['commands'].append({'id':'push','cwd':'.','argv':['pnpm','run','db:push'],'risk':'writes'})),"no script named 'db:push'"),
    (lambda repo: edit(repo,lambda s: layer(s,'tests')['commands'][0].update(cwd='packages/missing')),'command working directory does not exist'),
    (lambda repo: edit(repo,lambda s: layer(s,'logging')['paths'].append('packages/ai-engine/src/telemetry/**')),'no file in the repository matches this path'),
    (lambda repo: edit(repo,lambda s: layer(s,'tests')['technologies'].append('jest')),'not a technology in the Flow catalog'),
])
def test_a_missing_page_command_or_path_is_broken_not_drift(synced,breakage,expected):
    breakage(synced)
    code,report=api().check(PLUGIN,synced)
    assert code==1 and any(expected in line for line in errors(report)),report['validation_errors']


def test_a_second_skill_with_the_routers_name_is_broken(synced):
    other=synced/'.agents/skills/tech-refs'; other.mkdir()
    (other/'SKILL.md').write_text('---\nname: technology-references\ndescription: a second router\n---\n')
    code,report=api().check(PLUGIN,synced)
    assert code==1 and ".agents/skills/tech-refs/SKILL.md: a second skill claims the router's name" in errors(report)


# ---- the hand-written router (owner: project) --------------------------------------------------------

STUDIO_FINDINGS=['.agents/skills/technology-references/SKILL.md: lacks a trigger term for Claude Agent SDK (Claude Agent SDK, agent runtime)',
                 '.agents/skills/technology-references/SKILL.md: names Anthropic SDK, which no layer routes and no manifest declares']


def test_studio_router_is_checked_reported_precisely_and_never_written(studio):
    before=tree(studio)
    code,report=api().check(PLUGIN,studio)
    assert code==1 and errors(report)==STUDIO_FINDINGS and report['drift'] is False
    assert any('no generated region' in line for line in report['warnings'])
    code,synced=api().sync(PLUGIN,studio)
    assert code==1 and synced['status']=='checked' and synced['writes']=='none; the project owns its router'
    assert '`generate`: `pnpm run db:generate` in `packages/database` (writes)' in synced['region'] and 'drizzle-kit push' in synced['region']
    assert tree(studio)==before and not (studio/'.crux-flow').exists()
    assert (studio/'.claude/skills').is_symlink() and (studio/'.opencode/skills').is_symlink()


def test_studio_router_passes_once_its_description_names_what_the_manifests_declare(studio):
    path=studio/ROUTER; path.write_text(path.read_text().replace('PostgreSQL or Anthropic SDK code','PostgreSQL or Claude Agent SDK code'))
    code,report=api().check(PLUGIN,studio)
    assert code==0 and report['validation_errors']==[] and report['drift'] is False


def test_a_project_router_that_carries_the_region_gets_version_drift_and_the_pages_to_refresh(studio):
    path=studio/ROUTER; fixed=path.read_text().replace('PostgreSQL or Anthropic SDK code','PostgreSQL or Claude Agent SDK code')
    path.write_text(fixed+'\n## Layers\n\n'+api().sync(PLUGIN,studio)[1]['region']+'\n')
    assert api().check(PLUGIN,studio)[0]==0
    workspace=studio/'pnpm-workspace.yaml'; workspace.write_text(workspace.read_text().replace('1.0.0-rc.4','1.0.0-rc.5'))
    code,report=api().check(PLUGIN,studio)
    assert code==1 and report['drift'] is True and report['paths']==[ROUTER] and report['version_changed']==['drizzle']
    assert len(report['refresh_sources'])==6 and all('/drizzle-' in page for page in report['refresh_sources'])
    assert report['refresh_with']=='refresh-research-sources'


@pytest.mark.parametrize('breakage,expected',[
    (lambda repo: (repo/'bionic/research/sources/drizzle-kit-generate.md').unlink(),'bionic/research/sources/drizzle-kit-generate.md: referenced page does not exist'),
    (lambda repo: (repo/'bionic/research/sources/drizzle-kit-migrate.md').write_text((repo/'bionic/research/sources/drizzle-kit-migrate.md').read_text().replace('captured_at: 2026-10-05\n','')),
     'bionic/research/sources/drizzle-kit-migrate.md: source page has no capture date (captured_at)'),
    (lambda repo: (repo/'bionic/research/sources/vercel-composition-patterns.md').write_text((repo/'bionic/research/sources/vercel-composition-patterns.md').read_text().replace('063bee94c3f4df8453406c830b0a7df0f2860278','main')),
     'bionic/research/sources/vercel-composition-patterns.md: source is not pinned to a commit'),
    (lambda repo: shutil.rmtree(repo/'bionic/research/raw/2026-10-04/swift-concurrency-agent-skill'),
     'bionic/research/sources/swift-concurrency-agent-skill.md: raw capture research/raw/2026-10-04/swift-concurrency-agent-skill/ is missing'),
    (lambda repo: edit(repo,lambda s: layer(s,'drizzle')['commands'].append({'id':'seed','cwd':'packages/database','argv':['pnpm','run','db:seed'],'risk':'writes'})),
     "packages/database/package.json: no script named 'db:seed'; the command binding does not exist"),
])
def test_a_missing_source_page_pin_capture_or_command_is_broken(studio,breakage,expected):
    breakage(studio)
    code,report=api().check(PLUGIN,studio)
    assert code==1 and expected in errors(report),errors(report)


def test_a_reference_the_hand_written_router_and_map_never_name_is_broken(studio):
    edit(studio,lambda s: layer(s,'hono').update(read={'project':['apps/web/app/layout.tsx']}))
    code,report=api().check(PLUGIN,studio)
    assert code==1 and '.agents/skills/technology-references/SKILL.md: neither the router nor the map names apps/web/app/layout.tsx' in errors(report)


def test_flow_never_adopts_a_foreign_router_or_writes_through_a_link(studio):
    edit(studio,lambda s: s.update(owner='flow'))
    before=tree(studio)
    code,report=api().sync(PLUGIN,studio)
    assert code==1 and report['status']=='broken'
    assert any("this file exists and is not Flow's" in line for line in errors(report))
    assert tree(studio)==before and (studio/'.claude/skills').is_symlink() and not (studio/'.crux-flow').exists()


# ---- absence and configuration ------------------------------------------------------------------------

def test_a_project_without_the_section_has_no_surface(tmp_path):
    repo=consumer(tmp_path,'engine',configured=False); (repo/'.crux-flow.yml').write_text('config_version: "1"\nmode: aggressive\n')
    before=tree(repo)
    for call in (api().check,api().sync):
        code,report=call(PLUGIN,repo)
        assert code==0 and report=={**api().ABSENT,'eligible':report['eligible']} and report['eligible']['openai-sdk']=='7.25.0'
    planned,report=api().plan(PLUGIN,repo)
    assert planned is None and report['status']=='not-configured' and report['eligible']['anthropic-sdk']=='0.131.0'
    assert api().preload(repo) is None and tree(repo)==before


@pytest.mark.parametrize('mutate',[
    lambda data: data.update(technologies={}),
    lambda data: data['technology'].update(lockfile='technology.lock'),
    lambda data: data['technology'].update(owner='vendor'),
    lambda data: data['technology']['layers'][0].update(globs=['src/**']),
    lambda data: data['technology']['layers'][0]['commands'][0].update(shell='pnpm verify'),
    lambda data: data['technology']['layers'][0]['commands'][0].update(risk='harmless'),
    lambda data: data['technology']['layers'][0].update(paths=['../outside/**']),
    lambda data: data['technology']['layers'].append(dict(data['technology']['layers'][0])),
    lambda data: data['technology'].update(layers=[]),
])
def test_unknown_or_malformed_configuration_is_still_refused(engine,mutate):
    from crux.flow.common import FlowError
    from crux.flow import policy
    path=engine/'.crux-flow.yml'; data=yaml.safe_load(path.read_text()); mutate(data); path.write_text(yaml.safe_dump(data))
    with pytest.raises(FlowError): policy.read_config(path)
    with pytest.raises(FlowError): api().check(PLUGIN,engine)


def test_technology_edits_never_reach_the_effective_policy(engine,tmp_path):
    from crux.flow import policy
    from crux.flow.common import identity
    plain=tmp_path/'plain'; plain.mkdir(); (plain/'.crux-flow.yml').write_text('config_version: "1"\nmode: aggressive\n')
    home=tmp_path/'home'; home.mkdir()
    baseline=identity(policy.resolve(PLUGIN,plain,home,'claude'))
    assert identity(policy.resolve(PLUGIN,engine,home,'claude'))==baseline
    edit(engine,lambda s: s.update(preload=['developer']))
    resolved=policy.resolve(PLUGIN,engine,home,'claude')
    assert identity(resolved)==baseline and 'technology' not in json.dumps(resolved)
    (home/'.crux-flow.yml').write_text((engine/'.crux-flow.yml').read_text())
    with pytest.raises(ValueError,match='belongs to a project'): policy.resolve(PLUGIN,plain,home,'claude')


# ---- the catalog ------------------------------------------------------------------------------------

def test_catalog_is_registered_valid_and_seeded_only_with_verified_sources():
    spec=importlib.util.spec_from_file_location('validate_catalog',PLUGIN/'scripts/validate-catalog.py'); validator=importlib.util.module_from_spec(spec); spec.loader.exec_module(validator)
    assert validator.AUTHORED_CATALOG_JSON['flow-technology.json'](PLUGIN/'catalog/flow-technology.json',{'plugin_dir':PLUGIN})==[]
    catalog=api().load_catalog(PLUGIN)['technologies']
    for wanted in ('typescript','node','react','nextjs','hono','zod','drizzle','postgresql','node-postgres','vitest','playwright','swift',
                   'claude-agent-sdk','anthropic-sdk','codex-app-server','mcp','pino','docker-compose'): assert wanted in catalog
    sources=[source for row in catalog.values() for source in row.get('sources',[])]
    assert any(s['repository']=='honojs/skills' and s['licence']=='MIT' and s['first_party'] for s in sources)
    assert not any(s['repository'].startswith('clerk/') for s in sources)
    assert all(s['compatibility']=='unknown' for s in sources)      # nothing was version-checked, so nothing claims to be
    assert next(s for s in sources if s['repository']=='AvdLee/Swift-Concurrency-Agent-Skill')['first_party'] is False
    assert next(s for s in sources if s['repository']=='vercel-labs/next-skills')['lifecycle']=='moved'


@pytest.mark.parametrize('damage',[
    lambda text: text.replace('"zod": {','"hono": {',1),                                        # a duplicate id
    lambda text: text.replace('"publisher": "Hono", "licence": "MIT",','"publisher": "Hono",'),   # a source with no licence
    lambda text: text.replace('"lifecycle": "active", "compatibility": "unknown"}\n      ]\n    },\n    "zod"','"lifecycle": "active", "compatibility": "*"}\n      ]\n    },\n    "zod"'),
    lambda text: text.replace('"family": "api"','"family": "backend"',1),
    lambda text: text.replace('"lifecycle": "moved",\n         "replaced_by": "the documentation bundled in the installed next package (next/dist/docs)",','"lifecycle": "moved",'),
])
def test_a_damaged_catalog_is_refused(tmp_path,damage):
    from crux.flow.common import FlowError
    plugin=tmp_path/'plugin'; (plugin/'catalog').mkdir(parents=True)
    original=(PLUGIN/'catalog/flow-technology.json').read_text(); changed=damage(original)
    assert changed!=original
    (plugin/'catalog/flow-technology.json').write_text(changed)
    with pytest.raises(FlowError): api().load_catalog(plugin)


# ---- delegates ----------------------------------------------------------------------------------------

@pytest.mark.parametrize('host',['claude','codex','opencode','omp'])
def test_roles_are_unchanged_without_a_section_and_list_the_router_with_one(host,engine,tmp_path):
    from crux.flow import hosts,materialize,policy
    plain=tmp_path/'plain'; plain.mkdir(); home=tmp_path/'home'; home.mkdir()
    effective=policy.resolve(PLUGIN,plain,home,host)
    today=hosts.roles(PLUGIN,effective)
    assert hosts.roles(PLUGIN,effective,preload=None)==today and hosts.roles(PLUGIN,effective,preload=api().preload(plain))==today
    _,changes,_=materialize.updates(PLUGIN,effective,repo=plain,home=home,scope='project')
    folder=hosts.paths(host,'project',repo=plain,home=home)['agents'].relative_to(plain).as_posix()
    assert {name:raw for name,raw in changes.items() if name.startswith(folder+'/')}=={f'{folder}/{name}':raw for name,raw in today['files'].items()}
    chosen=api().preload(engine)
    assert chosen=={'skill':'technology-references','roles':['developer','reviewer'],'path':str(engine.resolve()/ROUTER)}
    routed=hosts.roles(PLUGIN,policy.resolve(PLUGIN,engine,home,host),preload=chosen)
    suffix='.toml' if host=='codex' else '.md'
    for name,raw in today['files'].items():
        role=name.removeprefix('crux-flow-').removesuffix(suffix)
        if role not in chosen['roles'] or host in ('opencode','omp'): assert routed['files'][name]==raw,name      # no preload field: the file is not touched
        elif host=='claude':
            assert yaml.safe_load(routed['files'][name].decode().split('---',2)[1])['skills']==['flow','technology-references']
            assert yaml.safe_load(raw.decode().split('---',2)[1])['skills']==['flow']
        else:
            assert routed['files'][name].startswith(raw)
            assert tomllib.loads(routed['files'][name].decode())['skills']['config']==[{'path':chosen['path'],'enabled':True}]
    for role in chosen['roles']:
        assert ('technology-router-preload' in routed['unsupported'][role])==(host in ('opencode','omp'))      # reported, not implied
    upstream=policy.resolve(PLUGIN,engine,home,host,{'mode':'upstream'})
    assert hosts.roles(PLUGIN,upstream,preload=chosen)==hosts.roles(PLUGIN,upstream)


def test_project_materialization_carries_the_router_and_refuses_an_unknown_role(engine,tmp_path):
    from crux.flow import materialize,policy
    home=tmp_path/'home'; home.mkdir()
    _,changes,_=materialize.updates(PLUGIN,policy.resolve(PLUGIN,engine,home,'claude'),repo=engine,home=home,scope='project')
    assert b'- technology-references' in changes['.claude/agents/crux-flow-developer.md'] and b'technology-references' not in changes['.claude/agents/crux-flow-architect.md']
    _,changes,_=materialize.updates(PLUGIN,policy.resolve(PLUGIN,engine,home,'claude'),repo=engine,home=home,scope='user')
    assert not any(b'technology-references' in raw for raw in changes.values() if raw)      # a user-scope role knows no project router
    edit(engine,lambda s: s.update(preload=['commander']))
    with pytest.raises(ValueError,match='unknown role'):
        materialize.updates(PLUGIN,policy.resolve(PLUGIN,engine,home,'claude'),repo=engine,home=home,scope='project')


# ---- upstream freshness ---------------------------------------------------------------------------------

def test_an_unreachable_source_means_freshness_is_unknown(studio):
    def offline(url): raise OSError('network unreachable')
    result=api().freshness(studio,fetch=offline)
    assert len(result)==9 and {row['state'] for row in result.values()}=={'unknown'}
    assert result['bionic/research/sources/vercel-composition-patterns.md']['reason']=='source unreachable'
    assert 'capture date' in result['bionic/research/sources/drizzle-kit-generate.md']['reason']      # no commit to compare: unknown, never current
    pinned='063bee94c3f4df8453406c830b0a7df0f2860278'; asked=[]
    def same(url): asked.append(url); return json.dumps([{'sha':pinned}]).encode()
    assert api().freshness(studio,fetch=same)['bionic/research/sources/vercel-composition-patterns.md']['state']=='current'
    assert all(url.startswith('https://api.github.com/repos/') for url in asked) and len(asked)==3
    moved=api().freshness(studio,fetch=lambda url: json.dumps([{'sha':'f'*40}]).encode())
    assert moved['bionic/research/sources/vercel-react-best-practices.md']['state']=='stale'
    assert api().freshness(studio,fetch=lambda url: b'<html>rate limited</html>')['bionic/research/sources/swift-concurrency-agent-skill.md']['state']=='unknown'


# ---- the regenerator, the CLI and init --------------------------------------------------------------------

def run(*argv: str) -> tuple[int,dict]:
    done=subprocess.run([sys.executable,str(SCRIPT),*argv],capture_output=True,text=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(PLUGIN/'scripts')))
    assert done.stdout.strip().startswith('{'),done.stderr
    return done.returncode,json.loads(done.stdout)


def test_regenerator_keeps_the_sibling_contract(engine,tmp_path):
    before=tree(engine)
    code,report=run('--dry-run','--repo-root',str(engine))
    assert code==1 and report['drift'] is True and ROUTER in report['paths'] and tree(engine)==before
    code,report=run('--repo-root',str(engine))
    assert code==0 and ROUTER in report['written']
    assert run('--dry-run','--repo-root',str(engine))==(0,api().check(PLUGIN,engine)[1])
    empty=tmp_path/'empty'; empty.mkdir()
    assert run('--dry-run','--repo-root',str(empty))==(0,{**api().ABSENT,'eligible':{}})
    (empty/'.crux-flow.yml').write_text('config_version: "1"\ntechnology: {owner: flow, layers: [], surprise: true}\n')
    code,report=run('--dry-run','--repo-root',str(empty))
    assert code==1 and report['validation_errors'][0]['field']=='technology'


def test_cli_has_exactly_two_technology_verbs(engine,tmp_path,capsys):
    from crux.flow.cli import main,parser
    verbs=next(a for a in next(a for a in parser()._actions if a.dest=='command').choices['technology']._actions if a.dest=='action').choices
    assert set(verbs)=={'check','sync'}
    base=['--repo',str(engine),'--home',str(tmp_path/'home')]; before=tree(engine)
    assert main([*base,'technology','check'])==1 and json.loads(capsys.readouterr().out)['drift'] is True
    assert main([*base,'technology','sync','--dry-run'])==1 and json.loads(capsys.readouterr().out)['status']=='planned' and tree(engine)==before
    assert main([*base,'technology','sync'],confirm=lambda plan: False)==2 and tree(engine)==before      # an unapproved write does not happen
    capsys.readouterr()
    assert main([*base,'technology','sync','--yes'])==0 and json.loads(capsys.readouterr().out)['status']=='synced'
    assert main([*base,'technology','check'])==0 and json.loads(capsys.readouterr().out)['validation_errors']==[]
    assert main([*base,'technology','check','--upstream'],fetch=lambda url: b'[]')==0 and json.loads(capsys.readouterr().out)['upstream_freshness']=={}


def test_init_shows_the_technology_step_applies_it_once_and_repeats_as_a_noop(env,capsys):
    machine_install(env,hosts=('claude',))
    repo,home,world,_=env
    shutil.copytree(FIXTURES/'engine',repo,dirs_exist_ok=True); shutil.copy(FIXTURES/'engine.crux-flow.yml',repo/'.crux-flow.yml')
    (repo/'.claude/settings.json').unlink()      # activation is init's to write here; the fixture's copy is the engine's own
    before=snapshot(repo)
    _,preview=cli(env,'init','--host','claude','--dry-run','--no-docs',capsys=capsys,code=0)
    assert preview['technology']['status']=='planned' and ROUTER in [c['path'] for c in preview['technology']['plan']['changes']]
    assert snapshot(repo)==before
    _,out=cli(env,'init','--host','claude','--yes','--no-docs',capsys=capsys,code=0)
    assert out['technology']['status']=='synced' and (repo/ROUTER).is_file()
    assert yaml.safe_load((repo/'.claude/agents/crux-flow-developer.md').read_text().split('---',2)[1])['skills']==['flow','technology-references']
    settled=snapshot(repo)
    _,again=cli(env,'init','--host','claude','--yes','--no-docs',capsys=capsys,code=0)
    assert again['technology']['status']=='already-correct' and again['hosts']['claude']['status']=='already-correct' and snapshot(repo)==settled


def test_init_reports_and_never_writes_a_project_owned_or_absent_technology_step(env,capsys):
    machine_install(env,hosts=('claude',))
    repo,_,_,_=env
    _,out=cli(env,'init','--host','claude','--yes','--no-docs',capsys=capsys,code=0)
    assert out['technology']['status']=='not-configured' and out['technology']['eligible']=={} and not (repo/'.agents').exists()
    shutil.copytree(FIXTURES/'studio',repo,dirs_exist_ok=True)
    config=yaml.safe_load((FIXTURES/'studio.crux-flow.yml').read_text()); config['mode']='aggressive'
    (repo/'.crux-flow.yml').write_text(yaml.safe_dump(config,sort_keys=False))
    router=(repo/ROUTER).read_bytes()
    _,out=cli(env,'init','--host','claude','--yes','--no-docs',capsys=capsys,code=0)
    assert out['technology']['status']=='checked' and out['technology']['writes']=='none; the project owns its router'
    assert (repo/ROUTER).read_bytes()==router and not (repo/'.crux-flow/receipts/technology.json').exists()


# ---- trigger evaluations are data ---------------------------------------------------------------------------

@pytest.mark.parametrize('name',['engine','studio'])
def test_router_trigger_evaluations_are_data_and_agree_with_the_configured_layers(name,tmp_path):
    repo=consumer(tmp_path,name); cases=json.loads((FIXTURES/f'{name}.triggers.json').read_text())
    config=yaml.safe_load((repo/'.crux-flow.yml').read_text())['technology']; catalog=api().load_catalog(PLUGIN)['technologies']
    layers={row['id']:row for row in config['layers']}
    routed={key for row in config['layers'] for key in row.get('technologies',[])}
    assert cases['skill']==config['router'] and len(cases['should_fire'])>=8 and len(cases['should_not_fire'])>=8
    for case in cases['should_fire']:
        terms=[term.lower() for key in layers[case['layer']].get('technologies',[]) for term in catalog[key]['terms']]
        assert any(term in case['prompt'].lower() for term in terms),case      # a prompt the description can plausibly match
        assert config['router'] not in case['prompt'] and 'skill' not in case['prompt'].lower()      # never names the skill
    for case in cases['should_not_fire']:
        assert not any(catalog[key]['terms'][0].lower() in case['prompt'].lower() for key in routed),case
        assert case['why']


def test_maintainer_skill_ships_with_trigger_evaluations_and_survives_the_flow_prefix():
    from crux.flow import hosts
    source=(PLUGIN/'skills/maintain-technology-skills/SKILL.md').read_text(); head,body=api()._split(source)
    cases=json.loads((PLUGIN/'skills/maintain-technology-skills/evals/triggers.json').read_text())
    assert cases['skill']==head['name']=='maintain-technology-skills' and len(cases['should_fire'])>=8 and len(cases['should_not_fire'])>=8
    assert len(head['description'])<=1024 and len(source.splitlines())<=140
    for phrase in ('Never execute','hooks','MCP','licence','unknown','refresh-research-sources','ingest-research','crux-flow technology check'): assert phrase in body,phrase
    projected=hosts.skills(PLUGIN,'claude')['maintain-technology-skills/SKILL.md'].decode(); seen,shown=api()._split(projected)
    assert seen['name']==head['name'] and seen['description']==head['description']
    assert shown.lstrip().startswith('## Installed runtime and routing') and '# Maintain technology skills' in shown
    for line in ('## Inspect','## Propose','## Check','## Refresh','## Adopt'): assert line in shown
    assert 'evals/triggers.json' in '\n'.join(hosts.skills(PLUGIN,'claude'))


def test_operator_guide_carries_each_consumers_complete_configuration():
    guide=(ROOT/'FLOW_GUIDE.md').read_text()
    for name in ('engine','studio'): assert '```yaml\n'+(FIXTURES/f'{name}.crux-flow.yml').read_text().rstrip()+'\n```' in guide,name
