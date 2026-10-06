# audit-docs — rationalization table and common mistakes (reference)

Reinforcement for the audit. The "Red flags" list in SKILL.md is the primary
stop-list; read this for the fuller excuse->reality mapping and the recurring-
mistake catalog.

## Rationalization table

| Excuse | Reality |
|---|---|
| "It's just a count mismatch — the next write will fix it." | Counts that drift never self-correct, they only worsen. Fix on every audit. |
| "The orphan run directory is probably abandoned — just delete it." | Orphan runs are evidence of an aborted promptbook or a manually-moved book. Ask. |
| "The dangling `related_briefs:` entry is obviously a typo — fix it." | "Obvious" typos sometimes refer to drafts the user is about to create. Ask. |
| "I'll fix the supersession bidirectionality by adding the missing back-link." | The missing link might be the wrong direction. Ask. |
| "I'll regenerate `sources.md` because it's missing — easy auto-fix." | Re-creating loses hand-edits. Ask first. |
| "The log heading is malformed — I'll silently fix it." | The log is append-only history. Surface the malformation; let the user write a corrective new entry. |
| "There are no BROKEN findings — I don't need to update indexes." | DRIFT fixes (counts, rollups, `_Last updated:_`) still need applying. |
| "I'll run `extract-code-docs` to clear code drift while I'm here." | Audit doesn't write to `docs/code/`. Recommend the user run it separately. |
| "The Explore agent missed some files — I'll redo inline myself." | If the walk feels incomplete, refine the prompt and re-run. Blending agent + inline findings creates duplicates. |
| "I'll batch the audit log entry at end of session — multiple audits → one entry." | Each audit is a discrete event. One log entry per audit. |

## Common mistakes

- **Conflating `docs/log.md` (operations) with `docs/journal/YYYY-MM.md` (work narrative)**: Different files, different rules, different headings. The audit checks both — don't apply log-format rules to journal entries or vice versa.
- **Counting hidden files**: `find docs -name '*.md'` is fine; `find docs -type f` picks up `.DS_Store` and other junk. Use `*.md`.
- **Treating `previous_captures:` entries as raw orphans**: they're referenced. The audit must check both `raw_path` and `previous_captures`.
- **Forgetting that `Proposed → Deprecated` is a legal but log-worthy transition**: an ADR with status=Deprecated and accepted_date=null is the "abandoned proposal" case, NOT a broken state. CHK-ADR-5 must allow this.
- **Reading frontmatter naively**: YAML lists, nulls, and quoted strings need a real parser. Use `python3 -c 'import yaml; ...'` via Bash for robust parsing.
- **Including `docs/AGENTS.md`, `docs/README.md`, and `docs/inbox/` in the per-concern walk**: `AGENTS.md`/`README.md` are schema, not content; `inbox/` is cross-concern staging, not a concern. The full non-concern excluded-paths set is: `docs/AGENTS.md`, `docs/README.md`, `docs/inbox/`. Exclude all three from the per-concern walk — `inbox/` gets no `docs/index.md` section (CHK-MI-1 must stay silent about it) and no frontmatter/index contract; its only coverage is CHK-SCHEMA-1 + CHK-INBOX-1/2/3.
- **Re-running the audit without committing fixes first**: each audit should leave `docs/` in a known state. Snapshot before running if rollback might be needed.
- **Skipping CHK-CODE-4 (`extract-code-docs --dry-run`) because "it's slow"**: it's the only check that catches source-vs-docs drift. Always run it.
- **Confusing orphan run dirs with abandoned runs**: an abandoned run is `status: abandoned` inside a snapshot file — still under a valid parent book. An orphan run dir has no parent book at all. Different.
