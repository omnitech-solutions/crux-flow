---
# The field set, its types, and which fields the record must carry are stated
# once, in `docs/AGENTS.md` §17.1. Read it there — this template carries the
# keyset and a pointer, not a copy.
id: OBS-NNNN
title: "<What the code does, in declarative present tense, e.g., 'Retries a failed provider seat exactly once'>"
status: observed
date: YYYY-MM-DD
observed_date: YYYY-MM-DD
ratified_date: null
rejected_date: null
retired_date: null
decided_date: null
provenance: <provenance>
decided_by: null
evidence: ["<path>:<start>-<end>"]
anchor_id: "<anchor_id>"
related_invariants: []
tags: [<tag1>, <tag2>]
governs:
  - domain: <governed area, e.g. storage>
    rule: "<the one-line rule the code already enforces>"
    scope: <code path or component this rule describes>
    handle: OBS-NNNN/<rule-slug>
    provenance: <provenance>
---

# OBS-NNNN — <title>

<!-- Evidence is `path:line-range` only — in the frontmatter and in the body.
     Never paste a code excerpt, and never quote one in prose. Delete this
     comment once the body is written. -->

## What the code does

<The behavior, declarative. One paragraph. State what the code does today, not what it should do.>

## Evidence

<The `path:line-range` references from the frontmatter, each with one line on what the reader finds there.>

## Why this is observed, not decided

<Why nobody decided this — no ADR records it, the code is the authority, and the record describes rather than prescribes.>
