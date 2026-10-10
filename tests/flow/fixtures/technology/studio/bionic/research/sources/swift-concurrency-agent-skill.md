---
title: "Swift Concurrency Agent Skill"
slug: swift-concurrency-agent-skill
type: source
source_url: https://github.com/AvdLee/Swift-Concurrency-Agent-Skill/tree/d5770817d2622e1585b1f7eaebc791a9cb0959c8/skills/swift-concurrency
source_date: null
author: "Antoine van der Lee"
captured_at: 2026-10-04
last_source_check: 2026-10-04
raw_path: research/raw/2026-10-04/swift-concurrency-agent-skill/
previous_captures: []
static: false
tags: [swift, concurrency, swift6, agent-skill]
---

> [paraphrased] This page is a short audited rendition of the captured skill folder, not a dump of its content. The full text is in the immutable capture at `research/raw/2026-10-04/swift-concurrency-agent-skill/` (`SKILL.md`, `references/`, `agents/`, `assets/`, `LICENSE`). Everything below is reference data from the upstream author; it is not an instruction to any agent that reads this wiki.

## Provenance

- Upstream: https://github.com/AvdLee/Swift-Concurrency-Agent-Skill, path `skills/swift-concurrency`.
- Commit: `d5770817d2622e1585b1f7eaebc791a9cb0959c8` (2026-09-14).
- License: MIT, from the upstream LICENSE file (Copyright 2026 Antoine van der Lee), copied into the capture.
- Obtained: shallow clone, copied unchanged.

## What it covers

Diagnosing Swift Concurrency issues, refactoring callback-based code to async/await, and guiding Swift 6 migration: tasks, actors, `@MainActor`, `Sendable`, data races, thread safety, and concurrency compiler or linter warnings.

## Structure

- `SKILL.md` is the router. Sections: Fast Path, Quick Fix Mode, Common Diagnostics (diagnostic to first check to smallest safe fix to reference file), When Quick Fixes Fail, Smallest Safe Fixes, Concurrency Tool Selection, Task entry isolation, Swift 6 Migration Quick Guide with a build-fix-rebuild-test loop, Reference Router, Verification Checklist.
- `references/` holds 16 files: foundations (`async-await-basics`, `tasks`, `actors`, `sendable`, `threading`), streams (`async-sequences`, `async-algorithms`), applied topics (`testing`, `performance`, `memory-management`, `core-data`, `observation`), migration and tooling (`migration`, `linting`), and a `glossary`, plus `_index`.

## How it is meant to be used

- Before proposing a fix, read the project's Swift language mode, strict concurrency level, default isolation and upcoming features from `Package.swift` or the Xcode project; capture the exact diagnostic; identify the isolation boundary; decide whether the code is UI-bound.
- Guardrails stated by the upstream: do not use `@MainActor` as a blanket fix; prefer structured concurrency over unstructured tasks; require a documented safety invariant and removal plan for `@preconcurrency`, `@unchecked Sendable` or `nonisolated(unsafe)`; make the smallest safe change.
- Open the smallest reference file that matches the question, using the Reference Router.

## Capture gaps

- The rendition does not reproduce the reference files; read the raw capture for them.
