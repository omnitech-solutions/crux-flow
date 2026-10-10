---
title: "AI execution boundaries and the on-device profile"
slug: ai-execution-boundaries
type: references
tags: [ai, execution, agents, on-device]
sources: []
last_reviewed: 2026-10-02
---

# AI execution boundaries and the on-device profile

Products request a stable profile or capability through `AiExecutionGateway`
and never switch on vendor names. The decision, including where agent runtimes
may run and what is never logged, is
[[adrs/ADR-0007-route-ai-work-through-aiexecutiongateway-profiles]]. This page
is the working reference for choosing an execution boundary.

## Choosing a boundary

| Boundary | Use it for | Do not use it for |
| --- | --- | --- |
| Direct model | One-shot or streaming text, structured output, classification, rewriting, and image generation | Durable state, approval, or multi-step recovery |
| Agent runtime | Explicit Codex or Claude jobs that need sessions, filesystem tools, or isolated execution | Routine chat, outlines, or image generation |
