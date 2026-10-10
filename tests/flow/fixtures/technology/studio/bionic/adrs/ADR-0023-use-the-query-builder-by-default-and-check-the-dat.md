---
id: ADR-0023
title: "Use the query builder by default and check the database role on every tenant-scoped path"
status: Accepted
date: 2026-10-05
proposed_date: 2026-10-04
accepted_date: 2026-10-05
deprecated_date: null
superseded_date: null
supersedes: []
amends: [ADR-0005]
superseded_by: null
deciders: ["Desmond O'Leary"]
tags: [tenancy, drizzle, postgresql, security, data-access]
related_briefs: []
related_research: []
---

# ADR-0023 — Use the query builder by default and check the database role on every tenant-scoped path

<!-- BODY CONTENT RULE — see bionic/AGENTS.md section 11.D. -->

## Context
