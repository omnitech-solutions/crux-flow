---
title: "Vercel React Best Practices (agent skill)"
slug: vercel-react-best-practices
type: source
source_url: https://github.com/vercel-labs/agent-skills/tree/063bee94c3f4df8453406c830b0a7df0f2860278/skills/react-best-practices
source_date: null
author: "Vercel Engineering"
captured_at: 2026-10-04
last_source_check: 2026-10-04
raw_path: research/raw/2026-10-04/vercel-react-best-practices/
previous_captures: []
static: false
tags: [react, nextjs, performance, agent-skill]
---

> [paraphrased] This page is a short audited rendition of the captured skill folder, not a dump of its rules. The full rule text is in the immutable capture at `research/raw/2026-10-04/vercel-react-best-practices/` (`SKILL.md`, `AGENTS.md`, `metadata.json`, `rules/`). Everything below is reference data from the upstream author; it is not an instruction to any agent that reads this wiki.

## Provenance

- Upstream: https://github.com/vercel-labs/agent-skills, path `skills/react-best-practices`.
- Commit: `063bee94c3f4df8453406c830b0a7df0f2860278` (2026-08-28).
- License: MIT, as stated in the skill's `SKILL.md` frontmatter and the repository README. The repository has no root LICENSE file.
- Obtained: shallow clone, copied unchanged. Skill name in its frontmatter: `vercel-react-best-practices`, version `1.0.0`, author `vercel`.

## What it covers

Performance optimisation guidance for React and Next.js applications, maintained by Vercel. The `SKILL.md` says it contains 70 rules across 8 categories (the capture holds 72 files under `rules/`, including section and template files; `metadata.json` still says "40+ rules"). Each rule file holds a short rationale, an incorrect example, a correct example, and extra context. `AGENTS.md` in the folder is the compiled document with every rule expanded.

## Rule categories by priority

| Priority | Category | Impact | Prefix |
|----------|----------|--------|--------|
| 1 | Eliminating Waterfalls | CRITICAL | `async-` |
| 2 | Bundle Size Optimization | CRITICAL | `bundle-` |
| 3 | Server-Side Performance | HIGH | `server-` |
| 4 | Client-Side Data Fetching | MEDIUM-HIGH | `client-` |
| 5 | Re-render Optimization | MEDIUM | `rerender-` |
| 6 | Rendering Performance | MEDIUM | `rendering-` |
| 7 | JavaScript Performance | LOW-MEDIUM | `js-` |
| 8 | Advanced Patterns | LOW | `advanced-` |

Examples of the rule topics per category, by rule identifier only: `async-parallel`, `async-suspense-boundaries`; `bundle-barrel-imports`, `bundle-dynamic-imports`; `server-auth-actions`, `server-cache-react`, `server-serialization`; `client-swr-dedup`; `rerender-derived-state-no-effect`, `rerender-no-inline-components`, `rerender-functional-setstate`; `rendering-conditional-render`, `rendering-hoist-jsx`; `js-set-map-lookups`, `js-early-exit`; `advanced-event-handler-refs`.

## How it is meant to be used

- Apply when writing, reviewing, or refactoring React components and Next.js pages, implementing data fetching, or optimising bundle size and load time.
- Read the individual rule files named by the quick-reference list for detail; read `AGENTS.md` for the full compiled guide.
- Work the priorities in order: waterfalls and bundle size first, micro-optimisations last.
- Its `metadata.json` cites react.dev, nextjs.org, SWR, better-all, node-lru-cache and two Vercel blog posts as references.

## Capture gaps

- The rendition does not reproduce rule bodies; read the raw capture for them.
- Rule counts disagree inside the upstream (70 in `SKILL.md`, "40+" in `metadata.json`); recorded as found.
