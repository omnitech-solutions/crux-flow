# Rationalization table and common mistakes (reinforcement reference)

Guardrail reinforcement for the night-gardener pass. The sharper "Red Flags"
list in SKILL.md is the primary stop-list; read this when you want the fuller
excuse→reality mapping or the recurring-mistake catalog.

## Rationalization Table

| Excuse | Reality |
|---|---|
| "The residual is small but I'll write a note anyway — it's almost a turn." | Sub-threshold is a silent skip by contract. The gardener moves only after the owner has moved. Write nothing. |
| "I'll push the garden branch so the owner can review it more easily." | Push is permanently out of scope. The branch is the deliverable; the owner fetches and merges it. |
| "The marker has no corroborating op, but it looks right — I'll use it." | An uncorroborated marker degrades to git-only delta, surfaced as an anomaly. Use the next-newest valid marker. |
| "I'll paste the test output verbatim — it's useful context." | Diagnostics output may embed secret values, absolute paths, or stack traces. Summarize in your own words. |
| "I've mentioned this idea before but it's still a good idea — I'll repeat it." | Check Back-notes first. Repeat = nagging. Escalate (stronger framing) or rest it for now. |
| "I'll write the log op after the note — same effect." | Ordering is pinned. Op first, then re-read log head, then write note with that head as log_head. Reversed ordering breaks the self-trigger defense. |

## Common Mistakes

- **Skipping the turn gate.** The gate is mandatory — first act, every night.
  Skipping it breaks idempotency and double-fire safety.
- **Using a stale or orphaned high-water marker.** After a rebase the recorded
  commit SHA may be orphaned; use merge-base ancestry check and timestamp
  fallback, not naive commit equality.
- **Regenerating preferences.md without reading tending.md first.** The
  projection is computed from tending.md + today's date; any other source is
  wrong.
- **Treating the gardener's own garden-op as owner activity.** All `garden |`
  log ops are mechanically excluded from the delta; they are never evidence that
  the owner moved.
- **Letting a solo whiteboard session become a decision.** Whiteboarding
  explores; the architect decides. The session lands in the inbox; the gardener
  never authors an ADR.
- **Installing the scheduled routine.** Writing crontabs, launchd plists,
  workflows, or settings is the auto-executing-persistence class. The owner
  installs it; the gardener never does.
