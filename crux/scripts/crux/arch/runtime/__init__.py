"""Import-only, subprocess-isolated runtime arch introspection (ADR-0075).

This package is the executor that the two-factor consent gate
(`CRUX_ARCH_ALLOW_RUNTIME=1` plus a per-execution permission event) reaches,
for Python targets only. It runs a target application's *import-time* code in a
confined subprocess, reads one framework introspection registry (FastAPI
`app.routes`, Flask `app.url_map.iter_rules()`, or Django `urlpatterns` +
`apps.get_models()`), and yields an untrusted advisory capture that a human
reviews before filing under `bionic/inbox/`.

Confinement contract (per-control, stated honestly in ADR-0075 decision 2):
kernel-enforced where the platform provides the primitive (Linux network
namespace, resource rlimits), best-effort where it does not (macOS network
neutralization). Every failure mode is a no-capture refusal, never a hang.

DIRECTIONALITY GUARANTEE (ADR-0075 decision 5): this package may import
`crux.arch.derive` (for `_cell`); `derive` MUST NEVER import anything under
this package. The unattended `derive` pipeline reaches no code path here and
writes nothing under `arch/`. `ArchRuntimeConfinementTests` gates that edge.

stdlib-only. Linux + macOS.
"""
