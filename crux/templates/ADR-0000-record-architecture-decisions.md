---
id: ADR-0000
title: "Record architectural decisions as ADRs"
status: Accepted
date: {{today}}
proposed_date: {{today}}
accepted_date: {{today}}
deprecated_date: null
superseded_date: null
supersedes: []
superseded_by: null
deciders: ["{{repo_name}}"]
tags: [meta, process]
related_briefs: []
related_research: []
---

# ADR-0000 — Record architectural decisions as ADRs

## Context

This project needs a durable record of the architectural choices that shape it — the
constraints in play when a decision was made, the alternatives considered, and the
consequences accepted. Without that record, the project's reasoning evaporates the
moment its authors lose context, and future contributors (human or LLM) re-litigate
settled questions from scratch.

Architecture Decision Records (ADRs) are a well-established pattern for this, originated
by Michael Nygard in
["Documenting Architecture Decisions"](https://www.cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
(2011) and consolidated by Martin Fowler at
[ArchitectureDecisionRecord](https://martinfowler.com/bliki/ArchitectureDecisionRecord.html).
This ADR is the meta-ADR: it records the decision to record decisions.

## Decision

This project records architectural decisions as ADRs under `docs/adrs/`, one file per
decision, following the format established by the `crux` Claude Code plugin.
Every ADR has the frontmatter contract and body sections defined by the plugin's
`ADR-template.md`. ADRs are append-only history: once an ADR leaves `Proposed`, its
body is frozen and only its status frontmatter mutates.

## Alternatives Considered

A single project decision log, free-form design docs, or commit-message archaeology
were all considered. None preserve the per-decision context, the alternatives weighed,
or the supersession chain that ADRs provide. ADRs are the lowest-friction format that
still carries the structure future readers need.

## Consequences

**Positive:**
- Every non-trivial decision has a discoverable, citable home.
- Supersession links make obsolete decisions explicit instead of silently abandoned.
- LLM agents can ground answers about "why" in `docs/adrs/` instead of guessing.

**Negative:**
- Discipline cost: ADRs only work if they're written. The `propose-adr` skill keeps
  the friction low; sustained use is still required.

**Follow-on work:**
- All future architectural decisions invoke `propose-adr`.

## State machine

ADRs move through four states:

```
Proposed ──accept──▶ Accepted ──deprecate──▶ Deprecated
                       │
                       └──supersede(new ADR)──▶ Superseded
```

`Proposed` → `Deprecated` is allowed for abandoned proposals. `Proposed` → `Superseded`
is not — supersession replaces an `Accepted` decision. `Superseded` requires a target
ADR id, and the `transition-adr` skill writes both ends of the supersession link in
one operation.

## References

- Michael Nygard, "Documenting Architecture Decisions" — https://www.cognitect.com/blog/2011/11/15/documenting-architecture-decisions
- Martin Fowler, "Architecture Decision Record" — https://martinfowler.com/bliki/ArchitectureDecisionRecord.html
