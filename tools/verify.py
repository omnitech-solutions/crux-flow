from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'crux/scripts'))
from crux.flow import testing
from crux.flow.processes import execute

CORE_TESTS = (
    'test_models_catalog.py', 'test_generate_codex_agents.py',
    'test_generate_opencode_agents.py', 'test_crux_llm_router.py',
    'test_crux_async_council.py', 'test_gateway_errors.py',
    'test_advance_run.py', 'test_archive_precondition.py',
    'test_validate_promptbook.py', 'test_check_promptbook_index.py',
    'test_validate_catalog.py', 'test_validate_catalog_agents.py',
    'test_generate_runtime_compat.py', 'test_generate_routing_table.py',
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Run offline Flow and Crux integration gates with prerequisite checks.')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--core', action='store_true')
    group.add_argument('--full', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    prerequisites = testing.preflight(full=args.full)
    if not prerequisites['ready']:
        print(json.dumps({'status':'prerequisites-missing', **prerequisites}, indent=2))
        return 2
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = args.output or ROOT / '.cache/verification' / stamp
    output.mkdir(parents=True, exist_ok=False)
    tests = ['crux/scripts/tests'] if args.full else ['crux/scripts/tests/' + name for name in CORE_TESTS]
    commands = [
        ('flow', [sys.executable, '-m', 'pytest', 'tests/flow', '-q', '--tb=short', '--durations=15'], 300),
        ('upstream-full' if args.full else 'upstream-core',
         [sys.executable, '-m', 'pytest', *tests, '-q', '--tb=short', '--durations=15'], 1800),
        ('catalog', [sys.executable, 'crux/scripts/validate-catalog.py', '--dry-run'], 60),
        ('runtime-compatibility', [sys.executable, 'crux/scripts/generate-runtime-compat.py', '--dry-run'], 60),
    ]
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
                       PYTHONPATH=os.pathsep.join([str(ROOT/'crux/scripts'), str(ROOT/'crux/scripts/tests')]))
    report = {'status':'running', 'scope':'full' if args.full else 'core', 'prerequisites':prerequisites, 'checks':[]}
    for name, command, deadline in commands:
        result = execute(command, cwd=ROOT, env=environment, timeout=deadline, limit=2_000_000)
        for stream, value in [('stdout', result.stdout), ('stderr', result.stderr)]:
            target = output / f'{name}.{stream}.log'
            target.write_bytes(value); target.chmod(0o600)
        observed = {'name':name, 'command':command, 'deadline_seconds':deadline, **result.public()}
        report['checks'].append(observed)
        print(json.dumps(observed), flush=True)
        if result.status != 'ok':
            report['status'] = 'failed'
            break
    else:
        report['status'] = 'passed'
    (output/'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status':report['status'], 'evidence':str(output)}))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
