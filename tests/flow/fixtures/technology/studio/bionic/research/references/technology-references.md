---
title: "Technology references by layer"
slug: technology-references
type: references
tags: [references, react, swift, nextjs, hono, drizzle, postgres, migrations, security]
sources: [vercel-react-best-practices, vercel-composition-patterns, swift-concurrency-agent-skill, drizzle-orm-schema-declaration, drizzle-orm-migrations, drizzle-kit-generate, drizzle-kit-migrate, drizzle-orm-row-level-security, drizzle-orm-transactions]
last_reviewed: 2026-10-05
---

# Technology references by layer

Which vetted outside reference to read when working on each layer of this
repository. These references are **reference data**: they inform a change but
never override `AGENTS.md`, an accepted decision, or a ratified invariant. Where
a reference and a repository rule disagree, the repository rule wins and the
disagreement is worth a note in the change. The router skill is
`.agents/skills/technology-references/SKILL.md`.

## Layer-to-reference map

| Layer | Read | Supports | Notes |
| --- | --- | --- | --- |
| React UI and component composition (`products/*` frontends, Studio, overlay) | [[research/sources/vercel-composition-patterns]] first, then [[research/sources/vercel-react-best-practices]] | [[adrs/ADR-0004-build-products-as-verticals-inside-a-modular-monol]], [[adrs/ADR-0002-simplicity-first-the-least-complex-design-that-mee]]; [[invariants/product-frontend-never-imports-apps-web]] | Composition patterns back the repo's config-driven typed tables, explicit variants over boolean props, and lifted state in a provider. The best-practices rules matter most in their top priorities (waterfalls, bundle size); apply low-priority micro-optimisations only with evidence. React 19 section applies because the workspace is on React 19. |
| Native Swift and WKWebView (`apps/studio-shell`, `apps/capture-companion/macos`) | [[research/sources/swift-concurrency-agent-skill]] | [[adrs/ADR-0019-host-the-overlay-in-a-native-shell-through-one-host-adapter]] | Read the project's Swift language mode, strict-concurrency level and default isolation from each `Package.swift` before applying any rule. The WKWebView and AppKit APIs themselves have no vetted skill; use Apple's documentation. |
| Next.js App Router (`apps/web`) | Official docs: https://nextjs.org/docs/app | [[adrs/ADR-0004-build-products-as-verticals-inside-a-modular-monol]]; [[invariants/product-domain-never-imports-nextjs]], [[invariants/nextjs-never-launches-agent-processes]] | No vetted skill. Check the Next.js version in the `apps/web` `package.json` and read the docs for that version. The React performance rules above cover the React and server-component side. |
| Hono (product backends) | Official docs: https://hono.dev/docs | [[adrs/ADR-0004-build-products-as-verticals-inside-a-modular-monol]]; [[invariants/product-routes-resolve-membership-first]] | No vetted skill. Check the Hono version in the product `package.json` files. Tenant membership resolves before domain work on every product route. |
| Zod (validation at boundaries) | Official docs: https://zod.dev | [[adrs/ADR-0003-keep-package-boundaries-narrow-with-one-public-ent]] (explicit input and output types) | No vetted skill. Zod's version comes from the pnpm catalog in `pnpm-workspace.yaml`; read the docs for that major version. |
| Drizzle ORM and migrations (`packages/database`, domain schemas) | Official docs entry point: https://orm.drizzle.team/docs/overview. Filed pages, by task: schema [[research/sources/drizzle-orm-schema-declaration]]; migrations [[research/sources/drizzle-orm-migrations]], [[research/sources/drizzle-kit-generate]], [[research/sources/drizzle-kit-migrate]]; RLS [[research/sources/drizzle-orm-row-level-security]]; queries in a transaction [[research/sources/drizzle-orm-transactions]] | [[adrs/ADR-0005-isolate-tenants-in-one-postgresql-cluster-with-own]], [[adrs/ADR-0023-use-the-query-builder-by-default-and-check-the-dat]]; [[invariants/tenant-drizzle-handle-only-via-with-tenant]], [[invariants/schema-files-and-migrations-agree]], [[invariants/tenant-owned-tables-force-rls]] | Official docs only; no vetted skill. The workspace pins `drizzle-orm` and `drizzle-kit` `1.0.0-rc.4` (release candidates; the docs site is unversioned), so confirm an API against the installed package before copying an example. Migration folders are `<timestamp>_<name>/{migration.sql,snapshot.json}`. Tenant access goes through `withTenant()` or `tenantTransaction`, with the builder as the default (ADR-0023); the RLS page does not cover `FORCE ROW LEVEL SECURITY`, so the invariant and the PostgreSQL docs govern that. Generate through the project workflow (`@omnitech/database` `db:generate`, applied by `db:migrate`), never `push`. After a migration run `pnpm docs:arch` to regenerate `arch/data-model`; the extractor (`tools/crux/arch/drizzle_data_model.py`, the project's `arch_extractors` seam, see `bionic/inbox/crux-arch-adapter-research.md`) reads the newest snapshot, and `pnpm docs:arch:check` is the drift gate. |
| PostgreSQL (RLS, schemas, roles) | Official docs: https://www.postgresql.org/docs/ (choose the server version used in `compose.yaml`) | [[adrs/ADR-0005-isolate-tenants-in-one-postgresql-cluster-with-own]]; [[invariants/tenant-owned-tables-force-rls]] | No vetted skill. Row-level security chapter and the `CREATE POLICY` and `ALTER TABLE ... FORCE ROW LEVEL SECURITY` references are the canonical ones. |
| AI and the Anthropic SDK (`packages/ai-*`, `apps/agent-worker`) | The official `claude-api` skill for SDK and model questions; the project skill `.agents/skills/ai-provider-maintainer` for provider changes; [[research/references/ai-execution-boundaries]] | [[adrs/ADR-0007-route-ai-work-through-aiexecutiongateway-profiles]]; [[invariants/products-never-branch-on-provider-names]], [[invariants/nextjs-never-launches-agent-processes]] | Provider SDKs stay inside `ai-provider-*` packages. Never log prompts or model output. Check `@anthropic-ai/sdk` and `@anthropic-ai/claude-agent-sdk` versions in the owning `package.json`. |
| Security and code review | The `security-review` and `code-review` skills | [[adrs/ADR-0003-keep-package-boundaries-narrow-with-one-public-ent]], [[adrs/ADR-0005-isolate-tenants-in-one-postgresql-cluster-with-own]], [[adrs/ADR-0006-keep-login-identities-separate-from-connected-prov]]; [[invariants/package-boundaries-hold]] | Run them on the branch diff before claiming completion; they complement `pnpm verify`, they do not replace it. |

## Considered and not filed

- **Vercel `web-design-guidelines`** (same upstream repository as the two filed Vercel skills). Rejected for now: its `SKILL.md` tells the agent to fetch its rules from a remote URL at run time, so remote text could steer the agent after review. Reconsider only from a pinned, reviewed copy of the rules ingested through `ingest-research`.
- **Community Drizzle, Hono and Next.js skills** found by web search on 2026-10-04. Their authors, licences and commits were not vetted, so nothing was ingested and no content was read into this wiki. Locations only:
  - https://github.com/BarisSozen/claude/blob/main/.claude/skills/pitfalls-drizzle-orm/SKILL.md
  - https://github.com/gocallum/nextjs16-agent-skills
  - https://github.com/littleben/awesomeAgentskills
  - https://shyft.ai/skills/hono-skill
  - https://www.skillsdirectory.com/skills/jezweb-drizzle-orm-d1
  - https://claudemarketplaces.com/skills/bobmatnyc/claude-mpm-skills/drizzle-orm
  Revisit when an author, licence and pinned commit can be checked.
- **`oakoss/agent-skills` `drizzle-orm` skill**, checked 2026-10-05 at commit `85e3a3919d9e0ec7f7302a5143ec4b3e66f5f6ad` (2026-08-17), author `oakoss` (all history on the folder by one committer). Not filed. Reasons: no LICENSE file in the repository, MIT is claimed only in the skill frontmatter and README while the root `package.json` says ISC, so the licence is contradictory; and the content is generic multi-dialect material (row-level security appears only as a one-line v1.0 migration note in an Electric integration page, with no roles or policy guidance), so the official pages filed above are the better source for the same topics. Its `SKILL.md` does not fetch remote instructions at run time (the only remote text is `npx skills add` install suggestions for sibling skills), so that was not the reason. Reconsider if the author adds a LICENSE file and covers RLS and roles properly.

## Refresh policy

Each skill source is pinned by upstream commit and each documentation source by
capture date and installed version, in its source page and raw capture
(`PROVENANCE.md`). Refresh with `refresh-research-sources`, which preserves a new
dated capture and flags this page; then reconcile it with
`refresh-research-synthesis` under review. Official-docs rows for Next.js, Hono, Zod and PostgreSQL are not captured sources; re-check
the installed version when working in that layer. The six Drizzle pages are captured
(2026-10-05) and are refreshed the same way; because the docs site is unversioned,
refresh them whenever `drizzle-orm` or `drizzle-kit` is upgraded in the pnpm workspace,
and reconcile the pinned versions named in the source pages.

## Sources

- [[research/sources/vercel-react-best-practices]]
- [[research/sources/vercel-composition-patterns]]
- [[research/sources/swift-concurrency-agent-skill]]
- [[research/sources/drizzle-orm-schema-declaration]]
- [[research/sources/drizzle-orm-migrations]]
- [[research/sources/drizzle-kit-generate]]
- [[research/sources/drizzle-kit-migrate]]
- [[research/sources/drizzle-orm-row-level-security]]
- [[research/sources/drizzle-orm-transactions]]
