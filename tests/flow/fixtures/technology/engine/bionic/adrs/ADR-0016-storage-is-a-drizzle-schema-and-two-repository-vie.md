---
id: ADR-0016
title: "Storage is a Drizzle schema and two repository views, with no raw SQL, and Prisma as a second adapter"
status: Accepted
date: 2026-10-08
proposed_date: 2026-10-08
accepted_date: 2026-10-08
deprecated_date: null
superseded_date: null
supersedes: []
amends: [ADR-0010]
superseded_by: null
deciders: ["Desmond O'Leary"]
tags: [persistence, drizzle, prisma]
related_briefs: []
related_research: []
governs: []
---

# ADR-0016 — Storage is a Drizzle schema and two repository views, with no raw SQL, and Prisma as a second adapter

## Context

ADR-0010 put the engine's tables in the host's database behind an executor that runs statements,
