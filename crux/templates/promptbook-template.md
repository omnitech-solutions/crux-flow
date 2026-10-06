---
id: PB-NNNN
title: "<Promptbook title — what this plan accomplishes>"
status: active                  # active | archived
created_at: YYYY-MM-DD
total_prompts: 1                # set when the ## Prompts list below is finalized
current_run: null               # RUN-NNN of this book's current run; null if none
current_prompt: null            # 1-indexed pointer into the ## Prompts list; null if no run
forked_from: null               # accepted vestige; always null, nothing writes it
tags: [<tag1>, <tag2>]
---

# <Title>

## Goal

<One short paragraph: what does this promptbook accomplish? What state will the project
be in when the last prompt is `done`? Open it with three statements: the Outcome —
what improves for the affected user when the work is done; the Evidence — what
would demonstrate that improvement; and the Constraint — what the change must
preserve. Prose in this field: no new field, no form, no gate.>

## Strategy

<One or two paragraphs explaining the approach. Why this ordering of prompts? What
assumptions does the plan make? What's explicitly out of scope?>

## Prompts

<If a prompt in this book self-archives (invokes `archive-promptbook`), order it AFTER
an `advance-run.py --outcome done` call for the run's own final prompt, so `current_run`
still points at a `status: completed` run when `archive-promptbook` reads it. That final
prompt's `result` must record the hand-off, not "book archived" — the archive act lands
in the `archive_note` and the `promptbook | archived` log entry, never in a prompt result.>

### Prompt 1 — <Short verb-led title>
- **Purpose:** <why this step exists; what it unblocks for the next step>
- **Prompt:**
  > <The exact prompt text to issue. Use `docs/` paths for references. Keep it
  > self-contained — assume the next session has no memory of prior conversation.>
- **Expected output:** <what success looks like — a file list, a diff, a summary, etc.>
- **Side effects:** <log-work entries, ingest-research, ADR proposals — list the skills
  this prompt is expected to invoke>

### Prompt 2 — <next step>
- **Purpose:** ...
- **Prompt:**
  > ...
- **Expected output:** ...
- **Side effects:** ...
