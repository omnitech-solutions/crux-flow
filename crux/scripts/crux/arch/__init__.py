"""The arch concern: a derived architecture spine with a drift-gated narrative.

Per ADR-0060. This package derives `<docs_dir>/arch/` from a project's real
sources (data model, interface surface, module graph, decision index) and
synthesizes an `overview.md` kept honest by a SHA-256 hash-stamp over the spine.

The public entry point is `crux.arch.core.derive`; the CLI wrapper is
`crux/scripts/derive-arch.py`.

Per ADR-0096 clause 12 the engine split into `core.py` plus one module per pack
under `packs/`. This package re-exports from `core` rather than from `derive`,
which is now a backward-compatibility facade for the private names 11 test
modules bind. `derive.py` still re-exports all three of these names, so the
older import path keeps working.
"""

from .core import derive, dry_run, SPINE_FILES  # noqa: F401
