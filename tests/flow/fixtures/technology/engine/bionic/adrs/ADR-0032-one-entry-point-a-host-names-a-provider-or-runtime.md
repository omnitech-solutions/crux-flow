---
id: ADR-0032
title: "One entry point: a host names a provider or runtime, and what implements it is private"
status: Accepted
date: 2026-10-08
proposed_date: 2026-10-08
accepted_date: 2026-10-08
deprecated_date: null
superseded_date: null
supersedes: []
amends: [ADR-0003, ADR-0008, ADR-0017, ADR-0028]
superseded_by: null
deciders: ["Desmond O'Leary"]
tags: [packaging, providers, sdk, boundary]
related_briefs: []
related_research: []
governs: []
---

# ADR-0032 — One entry point: a host names a provider or runtime, and what implements it is private

## Context

ADR-0003 separated the engine by entry point and rejected a single one. ADR-0008, ADR-0017 and
