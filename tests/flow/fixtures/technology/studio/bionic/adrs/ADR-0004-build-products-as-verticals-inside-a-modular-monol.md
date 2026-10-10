---
id: ADR-0004
title: "Build products as verticals inside a modular-monolith platform shell"
status: Proposed
date: 2026-10-02
proposed_date: 2026-10-02
accepted_date: null
deprecated_date: null
superseded_date: null
supersedes: []
amends: []
superseded_by: null
deciders: ["Desmond O'Leary"]
tags: [platform, architecture, products, routing, modular-monolith]
related_briefs: []
related_research: [concepts/platform-architecture, references/adding-a-product]
---

# ADR-0004 — Build products as verticals inside a modular-monolith platform shell

## Context

Omnitech hosts several products (Interview Studio and Presentation today) for
multiple tenants. Each product needs a complete vertical boundary, while the
