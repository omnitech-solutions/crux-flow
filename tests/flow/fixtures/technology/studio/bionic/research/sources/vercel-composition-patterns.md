---
title: "Vercel React Composition Patterns (agent skill)"
slug: vercel-composition-patterns
type: source
source_url: https://github.com/vercel-labs/agent-skills/tree/063bee94c3f4df8453406c830b0a7df0f2860278/skills/composition-patterns
source_date: null
author: "Vercel Engineering"
captured_at: 2026-10-04
last_source_check: 2026-10-04
raw_path: research/raw/2026-10-04/vercel-composition-patterns/
previous_captures: []
static: false
tags: [react, composition, component-architecture, agent-skill]
---

> [paraphrased] This page is a short audited rendition of the captured skill folder, not a dump of its rules. The full rule text is in the immutable capture at `research/raw/2026-10-04/vercel-composition-patterns/` (`SKILL.md`, `AGENTS.md`, `metadata.json`, `rules/`). Everything below is reference data from the upstream author; it is not an instruction to any agent that reads this wiki.

## Provenance

- Upstream: https://github.com/vercel-labs/agent-skills, path `skills/composition-patterns`.
- Commit: `063bee94c3f4df8453406c830b0a7df0f2860278` (2026-08-28).
- License: MIT, as stated in the skill's `SKILL.md` frontmatter and the repository README. The repository has no root LICENSE file.
- Obtained: shallow clone, copied unchanged. Skill name in its frontmatter: `vercel-composition-patterns`, version `1.0.0`, author `vercel`.

## What it covers

Composition patterns for flexible, maintainable React components: avoid boolean prop proliferation by using compound components, lifting state, and composing internals. It is aimed at refactoring components with many boolean props, building component libraries, and reviewing component architecture. Eight rule files sit under `rules/`, each with an incorrect and a correct example.

## Rule categories by priority

| Priority | Category | Impact | Prefix | Rules |
|----------|----------|--------|--------|-------|
| 1 | Component Architecture | HIGH | `architecture-` | `architecture-avoid-boolean-props`, `architecture-compound-components` |
| 2 | State Management | MEDIUM | `state-` | `state-decouple-implementation`, `state-context-interface`, `state-lift-state` |
| 3 | Implementation Patterns | MEDIUM | `patterns-` | `patterns-explicit-variants`, `patterns-children-over-render-props` |
| 4 | React 19 APIs | MEDIUM | `react19-` | `react19-no-forwardref` |

The React 19 section is marked by the upstream as React 19 and later only; skip it on React 18 or earlier.

## How it is meant to be used

- Apply when refactoring boolean-prop-heavy components, designing reusable component APIs, or reviewing component architecture.
- Read the named rule file for detail; `AGENTS.md` is the compiled full guide.
- Core one-line statements in the quick reference: use composition instead of boolean modes; the provider is the only place that knows how state is managed; define a generic context interface of state, actions and meta; lift state into providers for sibling access; create explicit variant components; prefer children over render props.
- Its `metadata.json` cites react.dev pages on context and the `use` API.

## Capture gaps

- The rendition does not reproduce rule bodies; read the raw capture for them.
