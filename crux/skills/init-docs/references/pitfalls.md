# init-docs — rationalization table and common mistakes (reference)

Reinforcement for init-docs. The "Red flags" list in SKILL.md is the primary
stop-list; read this for the fuller excuse→reality mapping and the
recurring-mistake catalog.

## Rationalization table

| Excuse | Reality |
|--------|---------|
| "The user has an existing tree — I'll merge into it." | The current `audit-docs --migrate` handles instruction files, not tree schemas. `init-docs` refuses without `--force`; schemas 2–4 use the pinned public `v3.23.2` recovery ladder on a copy. Partial bootstrap creates ambiguous state. |
| "Language detection found nothing — I'll guess." | Guessing wrong locks the project to a useless extractor. Surface the empty case as a WARNING and let the user populate. |
| "I'll skip the meta-ADR because the user said 'just give me the tree'." | The bootstrap ADR is the meta-commitment to ADRs. Skipping it makes future `propose-adr` runs feel ungrounded. Require `--no-adr-0000` explicitly. |
| "The project isn't a git repo — I can't init docs." | Git is an enhancement, never a requirement. Use the current working directory as the project root (confirm it with the user) and proceed; the tree works identically untracked. |
| "I'll write `docs/AGENTS.md` later, after I see the user's preferences." | The schema doc IS the contract. Other skills assume it exists; writing it later breaks them. |
| "I'll seed `journal/` with a TODO entry." | The journal is append-only and authored. A skill-seeded entry pollutes the audit voice. Just the header. |
| "The user has an AGENTS.md and I shouldn't touch it." | One appended line referencing the tree's `AGENTS.md` is the contract. Append idempotently; never edit unrelated content. |
| "manifest.yml is just config — I'll skip the schema_version key." | Every audit and migration depends on schema_version. Missing key = broken on first audit. |
| "The user already has a USER_GUIDE.md — I'll quietly skip it." | Without `--force`, preserving is correct, but the WARNING must be surfaced or the user won't know the canonical guide is missing. |
| "USER_GUIDE.md is the same as docs/AGENTS.md — pick one." | They serve different audiences. `docs/AGENTS.md` is the operational schema for LLMs; `USER_GUIDE.md` is the natural-language onboarding for humans. Both exist by design. |

## Common mistakes

- **Wrong repo root**: deriving from a subdirectory `pwd` when `git rev-parse --show-toplevel` would give the real root. Prefer git when available; outside a git repo, confirm the root with the user.
- **Missing `.gitkeep`**: empty directories vanish on commit (when the tree is tracked); future skills fail to find them. Harmless if the tree is untracked — seed them regardless.
- **Template placeholders left in output**: a stray `{{repo_name}}` in `AGENTS.md` is the canonical broken-init smell.
- **Off-by-one on ADR numbering**: the zeroth ADR is the bootstrap; `manifest.yml.adr.next_number` is `1`, not `0` and not `1` only "after the first user ADR".
- **Forgetting to seed `research/sources.md` with the exact nine-column header**: `ingest-research` prepends rows but won't write the header.
- **Including a hand-edited entry in `log.md`**: only the canonical `init` entry. Anything else belongs to other skills.
- **Detecting JavaScript as TypeScript (or vice versa)**: probe for `tsconfig.json` and `.ts` files; default to TypeScript only if both are present.
- **Skipping the `code/_meta/` directory**: `extract-code-docs` writes `manifest.json` there; missing dir = first extract fails.
