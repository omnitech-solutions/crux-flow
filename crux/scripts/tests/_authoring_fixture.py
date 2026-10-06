"""Make a temp directory look like the plugin's authoring checkout.

rule:authoring-checkout-probe-is-shared.

WHY THIS EXISTS. A regenerator whose output belongs to the plugin's own source
checkout now refuses to inspect anything else: run from a consuming project it
reports `surface_absent` instead of crashing on paths that were never going to be
there, or -- worse -- passing against the plugin's own copy of itself.

A synthetic fixture that builds `crux/templates/` and `crux/skills/` but no
`crux/scripts/` is not an authoring checkout under that probe, so the drift and
validation lanes a test is the positive control for never run. Seeding the probe
makes the fixture model what it claims to model. Call it in every fixture that
drives one of these CLIs through `--repo-root`.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["seed_authoring_probe"]


def seed_authoring_probe(root: str | Path, script: str | Path) -> Path:
    """Create `<root>/crux/scripts/<script name>` so `is_authoring_checkout` holds."""
    probe = Path(root) / "crux" / "scripts" / Path(script).name
    probe.parent.mkdir(parents=True, exist_ok=True)
    if not probe.exists():
        probe.write_text("# authoring-checkout probe (test fixture)\n", encoding="utf-8")
    return probe
