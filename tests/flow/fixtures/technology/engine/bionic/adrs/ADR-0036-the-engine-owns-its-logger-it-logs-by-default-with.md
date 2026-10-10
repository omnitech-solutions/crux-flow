---
id: ADR-0036
title: "The engine owns its logger: it logs by default, with context a consumer may override"
status: Proposed
date: 2026-10-10
proposed_date: 2026-10-10
accepted_date: null
deprecated_date: null
superseded_date: null
supersedes: []
amends: [ADR-0024, ADR-0029, ADR-0033]
superseded_by: null
deciders: ["Desmond O'Leary"]
tags: [logging, observability, configuration, context, redaction, content, pino]
related_briefs: [BRIEF-engine-logging-audit]
related_research: []
governs: []
---

# ADR-0036 — The engine owns its logger: it logs by default, with context a consumer may override

## Context

> **Body budget:** 135 lines — the decision carries a taken, removed and added table the owner asked for.
