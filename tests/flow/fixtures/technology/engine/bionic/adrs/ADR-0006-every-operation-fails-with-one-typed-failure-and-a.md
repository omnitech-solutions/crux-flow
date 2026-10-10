---
id: ADR-0006
title: "Every operation fails with one typed failure, and adapters never throw"
status: Accepted
date: 2026-10-08
proposed_date: 2026-10-08
accepted_date: 2026-10-08
deprecated_date: null
superseded_date: null
supersedes: []
amends: []
superseded_by: null
deciders: ["Desmond O'Leary"]
tags: [failures, contract]
related_briefs: []
related_research: []
governs: []
---

# ADR-0006 — Every operation fails with one typed failure, and adapters never throw

## Context

A provider outage, a rate limit, a refused request and a bug can all surface as a thrown error
