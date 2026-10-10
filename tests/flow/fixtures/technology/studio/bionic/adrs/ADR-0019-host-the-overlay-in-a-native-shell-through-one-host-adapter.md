---
id: ADR-0019
title: "Host the overlay in a native shell through one host adapter"
status: Accepted
date: 2026-10-04
proposed_date: 2026-10-04
accepted_date: 2026-10-04
deprecated_date: null
superseded_date: null
supersedes: []
amends: [ADR-0017, ADR-0018]
superseded_by: null
deciders: ["Desmond O'Leary"]
tags: [active-session, overlay, native, macos, adapter, privacy]
related_briefs: []
related_research: []
governs:
  - domain: active-session
    rule: "A native shell hosts the one overlay route and fulfils host capabilities through the versioned studio-host contract; it owns no session state, creates no assist request and calls no model."
    scope: apps/studio-shell and packages/interview-contracts
    handle: ADR-0019/native-shell-hosts-the-one-route
    provenance: authored
  - domain: active-session
    rule: "A frame from a host adapter enters Studio only as the page's own post to the owner-authenticated capture route, so masking, locality, staleness and persistence stay Studio's."
