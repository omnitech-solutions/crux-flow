---
id: ADR-0003
title: "Keep package boundaries narrow with one public entrypoint per runtime surface"
status: Accepted
date: 2026-10-02
proposed_date: 2026-10-02
accepted_date: 2026-10-02
deprecated_date: null
superseded_date: null
supersedes: []
amends: []
superseded_by: null
deciders: ["Desmond O'Leary"]
tags: [packages, boundaries, monorepo, database]
related_briefs: []
related_research: [concepts/interview-domain-model, concepts/platform-architecture]
---

# ADR-0003 — Keep package boundaries narrow with one public entrypoint per runtime surface

## Context

The repository is a pnpm/Turbo monorepo of `apps/*`, `packages/*` and
`products/*`. Each package is consumed through its `package.json` `exports`.
