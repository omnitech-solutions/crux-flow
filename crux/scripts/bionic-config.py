#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""bionic-config — resolve and validate the repo-root layout config (ADR-0044).

The canonical CLI prose skills use to resolve per-repo configuration — never by
reading the YAML ad hoc — so the textual + containment validation in
`bionic_config.py` (the single implementation) always runs. Per ADR-0044,
`.bionic.yml` is the layout source of truth and supersedes the legacy `.crux`
file (ADR-0032); this CLI resolves the two-file precedence
(`.bionic.yml` > `.crux` > convention). `crux-config.py` is a retained
back-compat delegator to this entrypoint.

Usage:
    python3 crux/scripts/bionic-config.py [--repo-root PATH]

Output (stdout, JSON):
    on success (exit 0):
        {"config_version": "1", "docs_dir": "bionic", "artifact_prefix": "",
         "repo_root": "/abs/path", "docs_root": "/abs/path/bionic",
         "source": ".bionic.yml" | ".crux" | "discovery:<dir>",
         "arch_stack": "crux" | null, "arch_extractors": {},
         "arch_decision_index_mode": "complete" | "curated"}
    on validation failure (exit 1):
        {"error": "<message>"}

Exit codes match the crux script convention: 0 clean, 1 validation/user
error (valid JSON on stdout), non-zero with unparseable stdout = crash.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bionic_config import BionicConfigError, load_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve the repo-root layout config (.bionic.yml, legacy .crux)."
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repo root to resolve config against (default: current directory; no upward walk).",
    )
    args = parser.parse_args()

    try:
        cfg = load_config(args.repo_root)
    except BionicConfigError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1

    print(
        json.dumps(
            {
                "config_version": cfg.config_version,
                "docs_dir": cfg.docs_dir,
                "artifact_prefix": cfg.artifact_prefix,
                "repo_root": str(cfg.repo_root),
                "docs_root": str(cfg.docs_root),
                "source": cfg.source,
                "arch_stack": cfg.arch_stack,
                "arch_extractors": cfg.arch_extractors,
                "arch_decision_index_mode": cfg.arch_decision_index_mode,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
