---
name: escalate-arch-runtime
description: "Only on explicit human request, run consent-gated Python introspection to recover routes or models missed by static extraction."
disable-model-invocation: true
metadata:
  tags: "arch, runtime, introspection, escalation, security"
  bundles: "crux-docs"
  risk_level: "high"
  triggers: "escalate the arch runtime | run the runtime arch introspection | recover the routes by running my app | introspect my FastAPI/Flask/Django app for arch"
  routing_note: "Human-invoked only. An optional fidelity upgrade a human may choose, never the remedy any crux surface points a reader at. Runs a Python target's import-time code in a subprocess-isolated child behind the two-factor `CRUX_ARCH_ALLOW_RUNTIME=1` consent gate; writes an advisory into `<docs_dir>/inbox/`, never into `arch/`."
---

# Escalate Arch Runtime

Recover the two arch surfaces static extraction is blind to — a framework's
route table and its ORM model schema — for **Python** targets, by running the
target's **import-time** code in a confined subprocess. This is the highest-risk
capability in the project; every guarantee below is load-bearing.[^adr]

## Scope — an optional fidelity upgrade, never a pointed-to remedy

This skill is an optional fidelity upgrade a human may choose, never the
remedy any crux surface points a reader at.[^clause-11] `derive-arch` never
names this skill, offers it, or points a reader here — a stubbed verdict, or
an exit-2 `ParserUnavailable` with no verdict at all, is a recorded fact, not
an invitation to escalate. Invocation starts only from a human's own explicit
request, named in the frontmatter `description` above.

## When this runs — the two-factor consent gate

Execution runs only under two independent, non-model-mediated factors:

- **Factor (a):** the standing capability flag `CRUX_ARCH_ALLOW_RUNTIME=1`, set
  out-of-band by a human, off by default, never auto-detected. The executor
  reads it directly and enforces a fail-closed floor: it requires a TTY and
  refuses under any CI marker (`CI`, `CONTINUOUS_INTEGRATION`, `GITHUB_ACTIONS`,
  `GITLAB_CI`, `BUILDKITE`, `CIRCLECI`, `JENKINS_URL`, `TF_BUILD`), so it
  hard-fails in CI even with the flag set.
- **Factor (b):** a per-execution permission event — the harness permission
  prompt on the executing tool call, or the human's own shell invocation. It is
  enforced OUTSIDE the executor and is NEVER self-attested; no config value,
  environment variable, or repo file substitutes for it.

The TTY check is a floor, not proof of consent. The guarantee that consent is
never model-mediated rests on three structural facts: this skill is human-
invoked only, is excluded from run-execution autonomy, and must not be
allowlisted for auto-approval.

## Invocation — one of two mutually exclusive grammars

The human names the target explicitly. The harness never auto-detects the
framework.

Run under the TARGET's interpreter (the one that can import the app's
dependencies) via `--python`:

```
uv run "${CRUX_PLUGIN_ROOT}/scripts/crux/arch/runtime/__main__.py" \
    --app <module>:<attr> \
    --python .venv/bin/python --repo-root . --docs-dir bionic
```

- **FastAPI / Flask:** `--app module:attr`, where `attr` is an app *instance*
  (introspected directly — FastAPI `app.routes`, Flask `app.url_map`) or a
  zero-argument application *factory* such as `create_app` (called with zero
  args, then introspected). A factory that requires arguments yields an honest
  no-capture, never a guess.
- **Django:** `--settings module.path` and NO `--app`. The harness sets
  `DJANGO_SETTINGS_MODULE`, injects a dummy DB backend, runs `django.setup()`,
  walks `ROOT_URLCONF` (depth cap 50 + cycle guard), and reads
  `apps.get_models()`. Django routes carry `method: null`.

A module-level `app.run()` / `uvicorn.run(app)` with no `__main__` guard blocks
on import; the harness cannot prevent that, so such a target yields no capture —
killed by the timeout — and never a hang.

## Confinement (stdlib-only; Linux + macOS)

Per-control, kernel-enforced where the platform provides the primitive and
best-effort where it does not:

- **Temp-clone working tree** — the target's own package is copied into a scratch
  tempdir (capped at 256 MiB, refused above), the child's cwd and `sys.path`
  rooted there. This reroutes writes; it is not a filesystem boundary. A
  same-UID absolute-path or native write to the real tree, the venv, or
  `~/.crux` is a stated residual an OS-level sandbox closes.
- **Fresh strict env allowlist** — the child environment is built fresh, never
  `os.environ.copy()`. Only `PATH`, `LANG`, `LC_*`, `HOME` (redirected to
  scratch), and `DJANGO_SETTINGS_MODULE` survive, so no ambient secret reaches
  the child.
- **Network cut** — Linux enters `os.unshare(CLONE_NEWUSER | CLONE_NEWNET)`
  (CPython 3.12+; fail-closes to no-capture when unavailable). macOS is
  best-effort Python-level socket neutralization plus the audithook; a native
  path can still open a socket there (stated residual).
- **Resource caps + process-group lifecycle** — `RLIMIT_CPU` 30 s, `RLIMIT_AS`
  2 GiB (Linux), `RLIMIT_NPROC` 64, `RLIMIT_NOFILE` 256 (soft == hard). The
  parent waits with a wall-clock timeout (default 60 s, clamped 5–300), then
  `killpg` SIGTERM → 5 s grace → SIGKILL. A `setsid` escapee MAY outlive the run
  (stated residual).
- **Audit deny-list** — `socket.*`, `subprocess.*`, `os.system`, `os.exec*`,
  `os.fork`/`forkpty`/`posix_spawn`, and write-mode `open` outside scratch.

Every failure mode is a no-capture refusal, never a hang.

## The capture is untrusted advisory data

The child writes a closed-schema JSON document to a dedicated descriptor. The
parent drains it under a 4 MiB pre-parse byte cap and a deadline, then parses it
with `json.loads` ONLY, validates against a strict schema that rejects unknown
keys, applies count/length caps, and re-escapes every value through `derive`'s
`_cell`. A forged capture is bounded to garbage advisory output a human reviews
before filing; it never reaches the spine.

## Where results land — outside `arch/`

The advisory is written to `<docs-dir>/inbox/`, marked `runtime_sourced: true`,
with provenance, no in-body clock, and no absolute paths. A human files it as a
research synthesis page or a session brief. An optional OpenAPI-shaped fold-back
candidate is also written to `inbox/`; a human may review and commit it into the
target repo, where the deterministic extractor consumes it on the next normal
`derive`. Nothing non-deterministic ever lands under `arch/` or touches the
spine hash.

## Test surface

The harness tests run against the cycle-authored FastAPI, Flask, and Django
fixture apps under `crux/scripts/tests/fixtures/runtime_*`, plus plain-Python
hostile fixtures — never an arbitrary external application.

[^adr]: The runtime-execution decision — the Python import-only runtime arch introspection executor, its confinement contract control-by-control, and its stated residuals — recorded in the target tree's ADRs.
[^clause-11]: The script-driven arch extraction decision, the clause retiring `derive-arch`'s chat-only escalation offer and narrowing this skill's scope to a human-chosen fidelity upgrade — recorded in the target tree's ADRs.
