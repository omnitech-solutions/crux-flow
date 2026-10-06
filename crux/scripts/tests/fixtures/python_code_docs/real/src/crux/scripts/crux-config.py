#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""crux-config — back-compat delegator to `bionic-config.py` (ADR-0044).

Per ADR-0044, `.bionic.yml` is the layout source of truth and supersedes the
legacy `.crux` file (ADR-0032). The canonical CLI is now `bionic-config.py`;
this command is retained so existing skill prose and tooling that invoke
`crux-config.py` keep working during the deprecation window. It delegates to
`bionic-config.py`'s `main()`, so both command names behave identically
(same args, same JSON stdout contract, same exit codes, same two-file
precedence).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_CLI = Path(__file__).resolve().parent / "bionic-config.py"
_spec = importlib.util.spec_from_file_location("bionic_config_cli", _CLI)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


if __name__ == "__main__":
    sys.exit(_mod.main())
