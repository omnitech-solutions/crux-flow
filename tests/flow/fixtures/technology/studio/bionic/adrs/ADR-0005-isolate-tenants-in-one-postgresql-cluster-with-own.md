---
id: ADR-0005
title: "Isolate tenants in one PostgreSQL cluster with owned schemas and forced row-level security"
status: Accepted
date: 2026-10-05
proposed_date: 2026-10-02
accepted_date: 2026-10-05
deprecated_date: null
superseded_date: null
supersedes: []
amends: []
superseded_by: null
deciders: ["Desmond O'Leary"]
tags: [tenancy, storage, postgresql, security, drizzle]
related_briefs: []
related_research: [concepts/interview-domain-model, concepts/platform-architecture]
---

# ADR-0005 — Isolate tenants in one PostgreSQL cluster with owned schemas and forced row-level security

## Context

Every product stores tenant-owned data. A tenant is a workspace; one request
must never read, write or reference another workspace's rows, even through a
