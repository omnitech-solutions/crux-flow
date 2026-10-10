"""The surface record: Flow's essentials are pinned, and a change in one is noticed.

DRIFT (the live surface differs from the committed record) fails with a readable change list. BROKEN (an invariant
Flow needs does not hold) fails with the named essential. Both run offline against syntax trees and generated role
files; nothing imports an upstream script or reads the network.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from crux.flow import surface

ROOT=Path(__file__).resolve().parents[2]; PLUGIN=ROOT/'crux'
SCRIPT=PLUGIN/'scripts/generate-flow-surface.py'


@pytest.fixture(scope='module')
def copy(tmp_path_factory) -> Path:
    """A mutable copy of the plugin source without its test trees: enough for every surface input."""
    target=tmp_path_factory.mktemp('plugin')/'crux'
    shutil.copytree(PLUGIN,target,ignore=shutil.ignore_patterns('tests','__pycache__','*.pyc','fixtures'))
    return target


@pytest.fixture
def plugin(copy,tmp_path) -> Path:
    fresh=tmp_path/'crux'; shutil.copytree(copy,fresh)
    return fresh


def run(plugin_root: Path,*args: str) -> tuple[int,dict]:
    done=subprocess.run([sys.executable,'-B',str(plugin_root/'scripts/generate-flow-surface.py'),*args,'--repo-root',str(plugin_root.parent)],
                        capture_output=True,text=True,env={'PYTHONDONTWRITEBYTECODE':'1','PATH':'/usr/bin:/bin'},timeout=120)
    assert done.stderr=='' or done.returncode!=2,done.stderr
    return done.returncode,json.loads(done.stdout)


def test_the_committed_record_matches_the_live_surface():
    code,payload=surface.check(PLUGIN)
    lines='\n'.join(f'  [{c["severity"]}] {c["path"]}: {c["recorded"]!r} -> {c["live"]!r}' for c in payload.get('changes',[]))
    assert code==0,('the Flow surface changed; read the changes, then run crux/scripts/generate-flow-surface.py\n'+lines+
                    '\n'+'\n'.join(e['error'] for e in payload.get('validation_errors',[])))


def test_every_invariant_holds_on_the_live_surface():
    assert surface.invariants(PLUGIN,surface.collect(PLUGIN))==[]


def test_the_record_is_deterministic():
    assert json.dumps(surface.collect(PLUGIN),sort_keys=True)==json.dumps(surface.collect(PLUGIN),sort_keys=True)


def test_the_record_pins_what_flow_depends_on():
    record=json.loads((PLUGIN/surface.RECORD).read_text())
    assert {'mode set','run start','run advance','run check','run drive','run resume','technology check','technology sync','upstream check','upstream prepare','upstream verify','version','setup','init'}<=set(record['flow_cli'])
    assert {'advance-run.py','validate-promptbook.py','check-promptbook-index.py','validate-catalog.py'}<=set(record['upstream_scripts'])
    lead=record['roles']['dev-lead']['claude']
    assert lead['upstream']['spawns']                                   # upstream mode keeps upstream's chain
    assert all(not lead[mode]['spawns'] for mode in ('aggressive','balanced','thorough'))   # every Flow-mode role is a leaf
    assert all(not signature['spawns'] for signature in record['roles']['developer']['claude'].values())
    assert record['skills']['flow']['owner']=='flow' and record['skills']['run-promptbook']['owner']=='upstream'
    assert 'generate-flow-surface.py' in record['drift_roster'] and not record['hooks']


def test_a_removed_flag_flow_passes_is_broken(plugin):
    script=plugin/'scripts/advance-run.py'
    script.write_text(script.read_text().replace('"--book"','"--volume"'))
    assert any('--book' in e for e in surface.invariants(plugin,surface.collect(plugin)))


def test_a_removed_upstream_script_is_broken(plugin):
    (plugin/'scripts/check-promptbook-index.py').unlink()
    assert any('check-promptbook-index.py' in e for e in surface.invariants(plugin,surface.collect(plugin)))


def test_a_changed_seam_signature_is_broken(plugin):
    script=plugin/'scripts/validate-promptbook.py'
    script.write_text(script.read_text().replace('def compute_book_hash(book_dict','def compute_book_hash(document'))
    assert any('compute_book_hash' in e for e in surface.invariants(plugin,surface.collect(plugin)))


def test_a_role_that_gains_a_delegation_tool_is_broken_on_every_host(plugin):
    # The generator strips every delegation grant in a Flow mode, so the invariant guards the generator itself: a
    # rendered Flow-mode role that holds a target is BROKEN, and upstream mode is left alone.
    live=surface.collect(plugin)
    for host in ('claude','opencode','omp'):
        for mode,signature in live['roles']['developer'][host].items():
            signature['spawns']=['crux-flow-reviewer']
    broken=surface.invariants(plugin,live)
    assert any('role developer on claude' in e and 'gained delegation' in e and 'every role is a leaf' in e for e in broken)
    assert any('role developer on omp' in e for e in broken)
    assert not any('(upstream)' in e for e in broken)


def test_a_schema_version_flow_does_not_read_is_broken(plugin):
    schema=plugin/'schemas/run.schema.json'; value=json.loads(schema.read_text())
    value['properties']['format_version']={'type':'string','enum':['1','2','3']}   # Flow reads 1 and 2; a 3 must be refused by name
    schema.write_text(json.dumps(value))
    assert any("run format_version ['3']" in e for e in surface.invariants(plugin,surface.collect(plugin)))


def test_a_manifest_schema_version_flow_does_not_read_is_broken(plugin):
    config=plugin/'scripts/bionic_config.py'
    config.write_text(config.read_text().replace('SUPPORTED_SCHEMA_VERSION = "5"','SUPPORTED_SCHEMA_VERSION = "6"'))
    assert any('manifest schema_version 6' in e for e in surface.invariants(plugin,surface.collect(plugin)))


def test_a_rewrite_whose_text_is_gone_is_broken(plugin):
    hosts=plugin/'scripts/crux/flow/hosts.py'
    hosts.write_text(hosts.read_text().replace('## Installed runtime and routing','## Runtime'))
    assert any('Installed runtime and routing' in e or 'routing preamble' in e for e in surface.invariants(plugin,surface.collect(plugin)))


def test_a_roster_without_the_surface_row_is_broken(plugin):
    skill=plugin/'skills/check-drift/SKILL.md'
    skill.write_text(skill.read_text().replace('generate-flow-surface.py --dry-run','generate-nothing.py --dry-run'))
    assert any('no surface row' in e for e in surface.invariants(plugin,surface.collect(plugin)))


def test_a_changed_skill_contract_is_a_notice_that_names_the_skill(plugin):
    skill=plugin/'skills/iterate/SKILL.md'
    skill.write_text(skill.read_text().replace('description: "','description: "Changed. ',1))
    code,payload=surface.check(plugin)
    assert code==1 and payload['drift'] is True
    changed=[c for c in payload['changes'] if c['path']=='skills/iterate/contract']
    assert changed and changed[0]['severity']=='notice' and payload['hard']==0


def test_a_removed_roster_row_or_command_is_a_hard_change(plugin):
    record=plugin/surface.RECORD; value=json.loads(record.read_text())
    value['flow_cli']['upstream frobnicate']={'flags':{},'positionals':[]}; value['drift_roster'].append('generate-vanished.py')
    record.write_text(json.dumps(value))
    code,payload=surface.check(plugin)
    hard={c['path'] for c in payload['changes'] if c['severity']=='hard'}
    assert code==1 and any(p.startswith('flow_cli/upstream frobnicate') for p in hard) and 'drift_roster/generate-vanished.py' in hard


def test_an_added_command_or_a_version_move_is_only_a_notice(plugin):
    record=plugin/surface.RECORD; value=json.loads(record.read_text())
    del value['flow_cli']['version']; value['upstream']['version']='3.0.0'
    record.write_text(json.dumps(value))
    code,payload=surface.check(plugin)
    assert code==1 and payload['hard']==0 and payload['notice']>=2


def test_the_regenerator_keeps_the_sibling_contract(plugin):
    assert run(plugin,'--dry-run')==(0,{'drift':False,'upstream':'3.27.4'})
    record=plugin/surface.RECORD; value=json.loads(record.read_text()); value['hooks']=['hooks/x']
    record.write_text(json.dumps(value))
    code,payload=run(plugin,'--dry-run')
    assert code==1 and payload['drift'] is True and json.loads(record.read_text())['hooks']==['hooks/x']   # dry-run writes nothing
    code,payload=run(plugin)
    assert code==0 and payload['written'] is True and run(plugin,'--dry-run')[0]==0


def test_the_regenerator_refuses_to_bless_a_broken_surface(plugin):
    script=plugin/'scripts/advance-run.py'
    script.write_text(script.read_text().replace('"--book"','"--volume"'))
    before=(plugin/surface.RECORD).read_text()
    code,payload=run(plugin)
    assert code==1 and payload['broken'] is True and (plugin/surface.RECORD).read_text()==before


def test_outside_the_source_checkout_the_row_is_not_applicable(tmp_path):
    done=subprocess.run([sys.executable,'-B',str(SCRIPT),'--dry-run','--repo-root',str(tmp_path)],capture_output=True,text=True,env={'PYTHONDONTWRITEBYTECODE':'1'})
    assert done.returncode==0 and json.loads(done.stdout)['surface_absent'] is True


def test_the_declaration_names_files_that_exist():
    declaration=surface.load_declaration(PLUGIN)
    for name in declaration['patched_upstream_files']: assert (PLUGIN/name).is_file(),name
    assert set(declaration['flow_skills'])<={p.name for p in (PLUGIN/'skills').iterdir()}


def test_upstream_check_is_offline_unless_asked_and_reports_what_blocks_the_procedure(tmp_path):
    from crux.flow import upstream
    def refuse(_): raise AssertionError('the default must not touch the network')
    offline=upstream.inspect(PLUGIN,tmp_path,observe=refuse)
    assert offline['network'] is False and offline['latest'] is None and offline['vendored']['version']=='3.27.4'
    assert offline['surface']['state']=='clean' and offline['procedure']['ready'] is False
    assert offline['procedure']['blockers']==['not a Git checkout: a flattened export is not history']
    seen=[]
    def observe(repository): seen.append(repository); return {'latest':{'tag':'v3.99.0','commit':'a'*40}}
    online=upstream.inspect(PLUGIN,tmp_path,fetch=True,observe=observe)
    assert seen==['https://github.com/bionic-coding/crux.git'] and online['latest']=={'tag':'v3.99.0','commit':'a'*40,'behind':True}
    current=upstream.inspect(PLUGIN,tmp_path,fetch=True,observe=lambda _: {'latest':{'tag':'v3.27.4','commit':'a'*40}})
    assert current['latest']['behind'] is False             # the vendored release is the latest: not behind


def test_upstream_check_reports_a_broken_surface_without_the_network(plugin,tmp_path):
    from crux.flow import upstream
    script=plugin/'scripts/advance-run.py'; script.write_text(script.read_text().replace('"--book"','"--volume"'))
    report=upstream.inspect(plugin,tmp_path)
    assert report['surface']['state']=='broken' and any('--book' in p for p in report['surface']['problems'])


def test_candidate_preparation_regenerates_the_record_last_and_verification_checks_it():
    from crux.flow import testing
    assert [c['name'] for c in testing.commands(Path('/x'))]==['catalog','surface','reference','package']
    source=(PLUGIN/'scripts/crux/flow/upstream.py').read_text()
    assert "'generate-routing-table.py', 'generate-flow-surface.py'" in source


def test_a_skill_that_starts_calling_scripts_is_a_notice_never_a_removal():
    # An empty `scripts_called` list is one leaf; filling it (upstream 3.27 did for `council`) is an addition.
    recorded={'skills':{'council':{'scripts_called':[]}}}
    live={'skills':{'council':{'scripts_called':{'run-council.py':True}}}}
    found=surface.changes(recorded,live,{'hard_on_removal':['skills']})
    assert found and all(c['severity']=='notice' for c in found)
    gone=surface.changes({'skills':{'council':{'scripts_called':{'run-council.py':True}}}},{'skills':{'council':{'scripts_called':[]}}},{'hard_on_removal':['skills']})
    assert any(c['severity']=='hard' for c in gone)    # a script actually dropped is still hard
