"""Shared helper the hostile fixtures use to locate their marker file.

Importing this module has no side effect; it is not itself hostile.
"""

import os
from pathlib import Path


def marker_path(fixture_file: str, name: str) -> Path:
    """Return the marker path for `name`, honoring CRUX_HOSTILE_MARKER_DIR.

    Falls back to a path beside `fixture_file` only when the environment
    variable is unset.
    """
    marker_dir = os.environ.get("CRUX_HOSTILE_MARKER_DIR")
    if marker_dir:
        return Path(marker_dir, name)
    return Path(fixture_file).with_name(name)
