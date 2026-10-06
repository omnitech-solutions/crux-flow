# Scheduling the night-gardener pass (install-time reference)

Read this only when setting up unattended scheduling. The nightly pass itself
never needs it. **crux never installs the routine** — writing crontabs, launchd
plists, or workflow files is an auto-executing-persistence class the gardener
never touches; the owner installs it as a deliberate act.

## Codex

In Codex, run this workflow interactively by asking to "tend the garden." Do
not use the Claude Code cron command or `.claude/settings.local.json` block
below. Codex has no interchangeable per-project settings file for those Claude
Code permissions, and a parent session's live sandbox settings apply to spawned
agents.

An owner who needs unattended execution must create and test its own external
runner and explicit sandbox/network policy, for example around `codex exec`.
That runner must retain the security rules in this skill, fail closed on a fresh
approval request, and never grant unrestricted filesystem or network access.
Crux never creates, edits, or validates that persistent automation.

## Claude Code

The nightly trigger is any runner that can start a Claude Code session in
the repo and say "tend the garden" — cron, launchd, or a Claude Code
scheduled routine.

### Copy-paste snippet (cron line + settings block — install both)

Install the cron line **and** the settings block together. The cron line alone
runs without the mechanical backstops and is not the recommended configuration.

```bash
# cron entry (edit times and paths to suit; single line, no continuations)
0 2 * * * cd /path/to/repo && PATH=/opt/homebrew/bin:/usr/local/bin:$PATH claude -p "tend the garden" --permission-mode acceptEdits >> ~/garden.log 2>&1
```

**Mechanical backstops — add to `.claude/settings.local.json` in the repo:**

```json
{
  "permissions": {
    "deny": [
      "Bash(git push *)",
      "Bash(gh *)"
    ]
  },
  "sandbox": {
    "network": {
      "allowedDomains": [
        "api.perplexity.ai"
      ]
    }
  }
}
```

Seed `allowedDomains` from the `url` column of `docs/garden/sources.md` (accepted
rows only — no `(proposed)` rows) plus `api.perplexity.ai` and any `source_url`
domains in `docs/research/sources.md`. Alternatively, express the same constraint
as permission allow-rules: one `WebFetch(domain:<domain>)` entry per accepted
source domain instead of the `sandbox.network` block (both forms are documented
in the Claude Code settings reference; the `sandbox.network` block applies to all
outbound network traffic, not just WebFetch).

With `--permission-mode acceptEdits`, denied tools fail rather than prompt,
which is the correct headless behavior. This is the honest belt-and-suspenders
backstop — **never disable the permission system** (never use
`--dangerously-skip-permissions`).

**launchd** (macOS): set `WorkingDirectory` to the repo root and
`ProgramArguments` to:

```
["claude", "-p", "tend the garden", "--permission-mode", "acceptEdits"]
```

**Claude Code scheduled routine:** ask Claude in the repo:
"schedule a nightly routine at 02:00 that runs: tend the garden"

### Headless realities

- `cwd` must be the repo root — config resolution is cwd-based.
- API keys live in `~/.crux/`; the process inherits the user's home.
- `uv` must be on `PATH` — cron's default PATH is minimal; extend it (the
  snippet above sets PATH to include Homebrew and `/usr/local/bin`).
- **Recommended-default permission mode:** `acceptEdits` so denied tools fail
  rather than prompt. Pair with a `.claude/settings.local.json` permissions
  block that denies `Bash(git push *)`, `Bash(gh *)`, and constrains egress.
- **Recommended-default egress:** seed `sandbox.network.allowedDomains` (or
  equivalent `WebFetch(domain:...)` allow-rules) from the `url` column of
  `docs/garden/sources.md` (accepted rows only — never `(proposed)` rows) plus
  `api.perplexity.ai` and any `source_url` domains in `docs/research/sources.md`.
  This is the honest belt-and-suspenders against exfiltration-by-query; the prose
  security rules in SKILL.md are the contract.

Idempotent double-fires are guaranteed by the turn gate.
