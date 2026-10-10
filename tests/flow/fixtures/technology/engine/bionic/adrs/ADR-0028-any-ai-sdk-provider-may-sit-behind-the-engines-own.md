---
id: ADR-0028
title: "Any AI SDK provider may sit behind the engine's own port, and the framework stays out"
status: Accepted
date: 2026-10-08
proposed_date: 2026-10-08
accepted_date: 2026-10-08
deprecated_date: null
superseded_date: null
supersedes: []
amends: [ADR-0002]
superseded_by: null
deciders: ["Desmond O'Leary"]
tags: [providers, ai-sdk, dependencies]
related_briefs: []
related_research: []
governs: []
---

# ADR-0028 — Any AI SDK provider may sit behind the engine's own port, and the framework stays out

## Context

ADR-0002 forbade an `ai`, `@ai-sdk/*`, LangChain or Mastra dependency anywhere, so every provider
