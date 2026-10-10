# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml==6.0.3", "httpx>=0.27,<1"]
# ///
"""Inventory or apply source-bound implementation clause migrations."""
from __future__ import annotations

import argparse
import json
import sys

import bionic_config
import implementation_migration as migration


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inventory", "dry-run", "apply"))
    parser.add_argument("--batch", required=True)
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args(argv)
    if args.command == "apply":
        import implementation_migration_apply as application
        import summaries_projection as sp
        try:
            report = application.apply(args.repo_root, args.batch)
            print(json.dumps(report, sort_keys=True))
            if report.get("failure_class") == "capability":
                print(json.dumps({"authority": "none", "limit": report["limit"]}), file=sys.stderr)
                return 2
            return 1 if report.get("failure_class") == "validation" else 0
        except migration.Refused as exc:
            print(json.dumps({"authority": "none", "limit": exc.code}))
            _remedy(exc)
            return 1
        except sp.GovernsValidationError:
            print(json.dumps({"authority": "none", "limit": "migration-application-model-refused"}))
            return 1
        except application.StagingCleanupIncomplete:
            print(json.dumps({"authority": "none", "limit": application.StagingCleanupIncomplete.code}), file=sys.stderr)
            return 2
        except (bionic_config.BionicConfigError, OSError):
            print(json.dumps({"authority": "none", "limit": "migration-application-unavailable"}), file=sys.stderr)
            return 2
        except (ValueError, TypeError, KeyError):
            print(json.dumps({"authority": "none", "limit": "migration-application-model-refused"}))
            return 1
    try:
        report = (migration.inventory if args.command == "inventory" else migration.dry_run)(args.repo_root, args.batch)
        print(json.dumps(report, sort_keys=True))
        if args.command == "inventory":return 0
        if report.get("failure_class")=="capability":
            print(json.dumps({"authority":"none","limit":report["limit"]}),file=sys.stderr)
            return 2
        return 0 if report["ready_to_apply"] else 1
    except migration.Refused as exc:
        print(json.dumps({"authority": "none", "limit": exc.code}))
        _remedy(exc)
        return 1
    except (bionic_config.BionicConfigError, OSError, ValueError, TypeError, KeyError):
        print(json.dumps({"authority": "none", "limit": "migration-inventory-unavailable"}), file=sys.stderr)
        return 2


def _remedy(exc):
    """Print a refusal's next step on stderr. Stdout keeps its one-line contract."""
    remedy = getattr(exc, "remedy", None)
    if remedy:
        print(json.dumps({"remedy": remedy}), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
