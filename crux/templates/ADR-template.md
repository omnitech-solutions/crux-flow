---
id: ADR-NNNN
title: "<Decision title in declarative present tense, e.g., 'Use SQLite for local-first storage'>"
status: Proposed
date: YYYY-MM-DD
proposed_date: YYYY-MM-DD
accepted_date: null
deprecated_date: null
superseded_date: null
supersedes: []
amends: []                  # optional; list of ADR ids this ADR narrowly refines (one-way; predecessors not mutated).
superseded_by: null
deciders: ["<handle>"]
tags: [<tag1>, <tag2>]
related_briefs: []          # filenames under docs/briefs/, e.g. [BRIEF-storage-options]
related_research: []        # slugs under docs/research/sources/
governs: []                 # optional; list of {domain, rule, scope, handle, anchor, provenance, retires} mappings.
                             # Omit or leave [] if this ADR governs nothing yet. See docs/AGENTS.md §11.A.
# governs:
#   - domain: <governed area, e.g. storage>
#     rule: "<the one-line rule this decision imposes>"
#     scope: <code path or component this rule governs>
#     handle: ADR-NNNN/<rule-slug>
#     anchor: "<verbatim span quoted from this ADR's body — a string, or a list of strings verified
#              by space-folded containment, AND over list elements. Required when this ADR's number
#              is below adr.governs_from (the backfill cohort); expected absent prospectively.>"
#     provenance: authored
#     retires: []   # optional; rule handles this entry displaces. See docs/AGENTS.md §11.A.
---

# ADR-NNNN — <Title>

<!-- BODY CONTENT RULE — delete this comment once the body is written.
     An ADR body states REQUIREMENTS and POSTCONDITIONS: what must be true when
     the work is done. It does not state how a chosen thing is driven — no call
     signatures, serializer options, exact file contents, command recipes, or
     code. Those belong to the dev module, where a type checker and tests review
     them. A mechanism may be bound when the mechanism IS the decision.
     Name a schema or contract as the source of truth for a shape; never restate
     the shape inline. Record a measurement in a footnote marked informative.
     The four narrative sections below carry a stated line budget, as a tripwire
     for the rule above rather than a cap; an over-budget body declares why.
     The rule, the budget's value, and the form of that declaration are stated
     once in `docs/AGENTS.md` §11.D. Read it there — this template carries a
     pointer, not a copy, which is the rule applied to itself. -->

## Context

<What's forcing this decision? What constraints, prior decisions, or external pressures
are in play? Cite any relevant briefs (`docs/briefs/...`) or research sources
(`docs/research/sources/...`).>

## Decision

<The chosen path, declarative. One paragraph or a short list. No hedging.>

## Alternatives Considered

### Option A — <name>
- **Pros:** ...
- **Cons:** ...
- **Why not:** ...

### Option B — <name>
- **Pros:** ...
- **Cons:** ...
- **Why not:** ...

<Or, if only one option was viable, a single paragraph explaining why no
alternatives were genuine candidates.>

## Consequences

**Positive:**
- ...

**Negative:**
- ...

**Follow-on work:**
- <Anything this decision creates: new ADRs to write, briefs to produce, code to migrate.>

## References

- [[briefs/BRIEF-<slug>]]
- [[research/sources/<slug>]]
- <External URLs>
