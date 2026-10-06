---
title: "Exploration title"
slug: brief-slug
type: brief
status: draft                # draft | published
created_at: YYYY-MM-DD
updated_at: YYYY-MM-DD       # bumped on every edit
authors: ["<handle>"]
tags: [<tag1>, <tag2>]
related_adrs: []             # ADR ids that reference this brief (back-populated by audit-docs)
---

# <Title>

> A brief is a short, pre-decision exploration document — usually 1–3 pages. It frames a
> problem, surveys options, and gathers context that an ADR will later compress into a
> single chosen path. Briefs are draft material; humans co-author them. ADRs cite briefs
> via `related_briefs:` and back-populate via `audit-docs`. Cite an existing rule as
> `rule:<slug>`. A rule this brief proposes and that does not exist yet is written in
> the placeholder form `rule:<new-thing>` — the angle brackets make it a non-token
> under the citation grammar, so the brief passes the citation lint while the rule is
> still a proposal. Delete this stub once you start writing.

<body>
