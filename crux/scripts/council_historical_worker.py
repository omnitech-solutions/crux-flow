"""Private read-only replay entry point; retained code is never imported."""
from pathlib import Path
import json
import sys
from dataclasses import asdict

# -I excludes inherited Python paths. Only this vendored directory is imported.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import implementation_approval as approval


def _main():
    try:
        content = sys.stdin.buffer.read(131073)
        approval.require(len(content) <= 131072, 'historical-worker-request-refused')
        request = json.loads(content)
        approval.require(isinstance(request, dict) and set(request) ==
                         {'repo', 'run_path', 'binding', 'migration'} and
                         type(request['migration']) is bool, 'historical-worker-request-refused')
        binding = request['binding']
        subject = binding['batch' if request['migration'] else 'revision']
        proof = approval._validate_binding(request['repo'], request['run_path'],
            slot=binding['slot'], revision_path=subject['path'], revision_sha256=subject['sha256'],
            historical_revision=True, migration=request['migration'], _worker_binding=binding)
        response = json.dumps(asdict(proof), sort_keys=True)
        approval.require(len(response.encode()) <= 2_000_000, 'historical-worker-output-refused')
        print(response)
        return 0
    except approval.Refused as exc:
        print(json.dumps({'refusal': exc.code}))
        return 1
    except Exception:
        print(json.dumps({'refusal': 'historical-worker-evidence-invalid'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(_main())
