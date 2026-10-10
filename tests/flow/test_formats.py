"""Run and promptbook formats: Flow executes its own format "1" runs, reads upstream 3.27's format "2" cycle runs for
inspection, and refuses any other format by name. A frozen run written before the update stays readable."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from test_records import PLUGIN, m, project, start  # noqa: F401  (project is a fixture)

SCHEMA = PLUGIN/'schemas/run.schema.json'


def upstream_run(tmp_path: Path, version: str) -> Path:
    path = tmp_path/'run-RUN-001.yaml'
    path.write_text(yaml.safe_dump({'format_version': version, 'book_id': 'PB-0001', 'run_id': 'RUN-001', 'status': 'in_progress',
                                    'current_prompt': 1, 'prompts': [{'n': 1, 'title': 'Cycle', 'state': 'running'}]}, sort_keys=False))
    return path


def test_the_run_schema_admits_format_one_and_two_and_carries_the_flow_block():
    schema = json.loads(SCHEMA.read_text())
    assert schema['properties']['format_version']['enum'] == ['1', '2']
    assert 'flow' in schema['properties']


def test_a_flow_run_is_format_one_and_stays_readable(project):
    path = Path(start(project)['run'])
    assert yaml.safe_load(path.read_text())['format_version'] == '1'
    assert m('records').load(path)['format_version'] == '1'
    assert m('history').inspect(path)['format'] == 'flow-v2'     # the frozen-run reader still names a Flow run


def test_format_two_is_read_for_inspection_and_never_executed(tmp_path):
    path = upstream_run(tmp_path, '2')
    seen = m('history').inspect(path)
    assert seen['format'] == 'upstream-yaml-format-2' and 'never through Flow' in seen['execution']
    with pytest.raises(ValueError, match="council-gated cycle run"):
        m('records').validate(yaml.safe_load(path.read_text()))


@pytest.mark.parametrize('version', ['0', '3', 2, None])
def test_any_other_format_is_refused_by_name(tmp_path, version):
    path = upstream_run(tmp_path, version)
    with pytest.raises(ValueError, match=r"format_version .* is not one Flow reads \(Flow reads '1', '2'\)"):
        m('history').inspect(path)
    with pytest.raises(ValueError, match="is not one Flow reads"):
        m('records').validate(yaml.safe_load(path.read_text()))


def test_a_flow_run_cannot_be_format_two(project):
    run = m('records').load(Path(start(project)['run']))
    run['format_version'] = '2'
    with pytest.raises(ValueError, match="a Flow run is format_version '1'"):
        m('records').validate(run)


def test_a_promptbook_in_an_unknown_format_is_refused_when_a_run_is_loaded(project):
    result = start(project); book = Path(result['book'])
    book.write_text(book.read_text().replace('format_version: \'1\'', 'format_version: \'9\'').replace('format_version: "1"', 'format_version: "9"'))
    with pytest.raises(ValueError, match="promptbook format_version '9' is not one Flow reads"):
        m('records').load(Path(result['run']))
