# AGENTS.md — Omnitech Studio

Orientation for every agent (Claude Code, Codex, OpenCode) and human working in
this repository. This file is the map and the guardrails; decisions, research,
and the journal live in `bionic/`.

See `bionic/AGENTS.md` for documentation operations.

Read `bionic/objectives.md` before work of any size, and carry its context through every delegation.
`bionic/AGENTS.md` §5.B is the one statement of what that means — who reads, when, what a delegation carries, how the mission bounds the work, and what to do when the file is missing or still a placeholder.[^objectives]

[^objectives]: rule:objectives-read-before-work, rule:orchestrators-read-objectives-at-startup-and-resume, rule:objectives-shape-the-work-and-authorize-none, rule:objectives-context-travels-with-every-delegation, rule:objectives-populate-gate-never-invents-a-goal

## Mission

Help a software engineer prepare for and perform in technical interviews:
explainable, runnable answers, realistic rehearsal, and briefings they can say
aloud — drivable by coding agents, inside a tenant-aware platform that can host
further products. The goals are in `bionic/objectives.md`.

## Development workflow

Crux is the only development workflow (ADR-0001).

- Explore an idea with `whiteboarding`; capture pre-decision thinking as a
  brief with `propose-brief`.
- Record an architectural decision with `propose-adr`; change its status only
  with `transition-adr`.
- Deliver work through the tier that fits: `dev-cycle` (architectural),
  `iterate` (non-architectural fix), `patch-cycle` (small, reversible),
  `fix-directly` (bounded defect, failing test first).
- Answer questions about the project with `query-docs`; record finished work
  with `log-work`.
- Before reviewing or writing React, Next.js, Swift, Hono, Drizzle or Postgres code,
  use the `technology-references` skill (map:
  `bionic/research/references/technology-references.md`).
- Before regenerating, checking drift on, or touching anything under `bionic/arch/`,
  `bionic/code/` or a generated index, use the `bionic-regeneration` skill (never
  hand-edit generated files).
- Documentation lives only in `bionic/`. Never hand-edit its regenerated parts
  (`code/`, `arch/`).
- Project skills live once in `.agents/skills/`; `.claude/skills` and
  `.opencode/skills` link to it. Edit skills and this file directly — nothing
  generates them.

## Architecture rules — do not violate

1. Simplicity first: choose the least complex design that meets current
   requirements, with no speculative layers, libraries, or infrastructure.
   Add an abstraction only for a second implementation or an independent
   lifecycle. Surface a scope checkpoint before a change that adds
   infrastructure or exceeds 1,000 changed lines. (ADR-0002)
2. Each package has one responsibility and one public entrypoint per runtime
   surface; never import another package's internal files. `database` owns
   connectivity, `withTenant()`, and migrations; domain packages own their
   schemas. (ADR-0003)
3. `apps/web` is a thin shell. `products/*` own their whole vertical:
   manifest, frontend, Hono backend, services, and tests. Product frontend
   never imports `apps/web`; domain code never imports Next.js. (ADR-0004)
4. Products register at build time; tenant installations own labels, order,
   visibility, and settings. Every product route is
   `/t/:tenantSlug/p/:productId/*` and resolves tenant membership before any
   domain work. (ADR-0004)
5. One PostgreSQL cluster: a `platform` schema plus one schema per owner.
   Every tenant-owned row carries `tenant_id`, row-level security is forced,
   and tenant foreign keys are composite on `(tenant_id, id)`. Tenant-scoped
   access goes through `withTenant()` or `tenantTransaction`, the query
   builder is the default for new repository code, and every path runs the
   same database-role check; migrations are one Drizzle stream in
   `packages/database/drizzle`. (ADR-0005, ADR-0023)
6. Login identities prove who the user is; connected accounts separately
   authorise provider actions through their own OAuth flow, with encrypted
   tokens never sent to the client. Never reuse login tokens for
   integrations. (ADR-0006)
7. Products call `AiExecutionGateway` by profile or capability and never
   branch on provider or model names. Codex and Claude Code run only in
   `agent-worker`; Next.js never launches an agent process. Agent profiles are
   typed, versioned, and bounded — never raw CLI arguments, environment
   variables, directories, MCP servers, or permission bypasses from users.
   (ADR-0007)
8. Never log questions, prompts, generated content or code, notes,
   attachments, or model responses by default outside development. In
   development every AI interaction is logged whole (prompt and answer, at
   trace level) so a person or an agent can see what happened; anywhere else
   that is off unless a host turns it on deliberately. Credentials are never
   logged, in any mode. (ADR-0007)
9. An interview answer's structured guide is the source of truth; its Markdown
   is always rendered from the guide. (ADR-0008)
