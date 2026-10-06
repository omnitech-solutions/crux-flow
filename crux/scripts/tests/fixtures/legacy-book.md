---
id: PB-9001
title: "Cycle: Fixture migration book"
status: archived
created_at: 2026-05-29
total_prompts: 2
current_run: null
current_prompt: null
forked_from: null
tags: [cycle, workflow, feature]
modules:
  adrs: 1
  dev_loops: 1
  review_cycles: 1
---

# Cycle: Fixture migration book

## Goal

A small fixture book exercising the migrator: a multi-line goal,
a blockquote prompt with a paragraph break, and side effects.

## Strategy

Drive it through the cycle. Loop-backs are explicit in each prompt's
exit clause.

## Prompts

<!-- MODULE BOUNDARY: begin ADR module #1 (placeholder) -->

### Prompt 1 — Plan: propose the fixture ADR
- **Purpose:** Establish the architectural commitment before any code lands.
- **Prompt:**
  > Read the goal. Dispatch planning agents in parallel.
  >
  > Then invoke `propose-adr`. Status starts at `Proposed`.
- **Expected output:** ADR file at `docs/adrs/ADR-NNNN-<slug>.md` with
  `status: Proposed`; `docs/log.md` entry appended.
- **Side effects:** propose-adr, query-docs

<!-- MODULE BOUNDARY: end ADR module #1 / begin summary — this comment must NOT leak into Prompt 1's side_effects -->

### Prompt 2 — Summary: completion report AND archive the book
- **Purpose:** Close the cycle.
- **Prompt:**
  > Write a completion summary, then invoke `archive-promptbook`.
- **Expected output:** Book archived; log entry written.
- **Side effects:** archive-promptbook

## Archive note

Archived 2026-05-29. Final run: [[promptbooks/runs/PB-9001-fixture/run-RUN-001]] (2/2 prompts terminal — all `done`).
