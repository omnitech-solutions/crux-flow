# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml>=6.0,<7", "httpx>=0.27,<1"]
# ///
"""Report a project's clause-migration authority state and its retained evidence.

A read-only command for skills that must check authority before they answer or
walk a tree. It writes nothing.

  state           Print the authority state of the checkout at --repo-root.
                  `original`: no clause migration was ever published; every ADR
                  clause governs as its record says. `published`: a migration
                  publication was proved from full Git history; the clauses it
                  moved are historical evidence, not current constraints.
  retained-roots  Print the retained demonstration holdings under the configured
                  documentation tree. Prune each before a recursive walk.
  retained PATH   Print, for each PATH, whether it lies inside a retained holding.

Exit codes: 0 the report on stdout; 1 a refusal, printed as
{"authority": "none", "limit": "<code>"} on stdout; 2 an environment error on
stderr. A refusal means current authority is unknown: do not answer from an
older ADR body or resolver instead.

`httpx` is declared because the publication proof imports through the shared
`crux` package, whose initializer loads the model router.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bionic_config
import implementation_migration as migration
import retained_evidence


def _relative(repo: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo).as_posix()
    except ValueError:
        return path.as_posix()


def _state(repo: Path) -> dict:
    view = migration.authority_view(repo)
    return {
        "state": view["state"],
        "publications": [{"path": p.get("path"), "publication_commit": p.get("publication_commit")}
                         for p in view.get("publications", [])],
        "retired_handles": list(view.get("retired_handles", [])),
    }


def _docs_root(repo: Path) -> Path:
    return Path(bionic_config.load_config(repo, require_tree=True).docs_root)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("state", "retained-roots", "retained"))
    parser.add_argument("paths", nargs="*", help="repository-relative paths for `retained`")
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args(argv)
    if args.command != "retained" and args.paths:
        parser.error(f"{args.command} takes no paths")
    if args.command == "retained" and not args.paths:
        parser.error("retained needs at least one path")
    repo = Path(args.repo_root).resolve()
    try:
        if args.command == "state":
            report = _state(repo)
        elif args.command == "retained-roots":
            report = {"roots": [_relative(repo, r)
                                for r in retained_evidence.retained_evidence_roots(_docs_root(repo))]}
        else:
            docs_root = _docs_root(repo)
            report = {"paths": [{"path": p, "retained": bool(retained_evidence.is_retained_evidence_path(
                docs_root, repo / p))} for p in args.paths]}
    except migration.Refused as exc:
        print(json.dumps({"authority": "none", "limit": exc.code}))
        remedy = getattr(exc, "remedy", None)    # a path-free next step; stdout keeps its one line
        if remedy:
            print(json.dumps({"remedy": remedy}), file=sys.stderr)
        return 1
    except retained_evidence.RetainedEvidenceRefusal as exc:
        print(json.dumps({"authority": "none", "limit": str(exc)}))
        return 1
    except (bionic_config.BionicConfigError, OSError) as exc:
        print(json.dumps({"authority": "none", "limit": "authority-view-unavailable",
                          "detail": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
