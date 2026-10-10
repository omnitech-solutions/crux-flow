---
id: ADR-0024
title: "The engine logs by configuration, and a line never carries content"
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
tags: [logging, observability, configuration]
related_briefs: []
related_research: []
governs: []
---

# ADR-0024 — The engine logs by configuration, and a line never carries content

## Context

A developer running a host needs to see that calls start, end and fail without opening the trace
