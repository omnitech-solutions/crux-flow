---
id: ADR-0007
title: "Route AI work through AiExecutionGateway profiles and run agents only in the isolated worker"
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
tags: [ai, execution, agents, privacy]
related_briefs: []
related_research: [references/ai-execution-boundaries]
---

# ADR-0007 — Route AI work through AiExecutionGateway profiles and run agents only in the isolated worker

## Context

Products use language models, image models and coding
agents (Codex, Claude Code). Providers and models change often; agent runtimes
