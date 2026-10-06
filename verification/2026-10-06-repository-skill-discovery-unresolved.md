# Repository skill discovery remains unresolved

Screenshot clarification: the operator provided Desktop screenshots at
13:37:03 and 13:37:12 MDT. Both were opened. The first shows `crux-flow` as a
Plugin entry in the picker for `omni-ui-components`; the second shows `no matches`
for the composer text `/cr` in that repository. These screenshots identify a
slash-command discovery mismatch, rather than showing a missing `/skills` entry.
OpenAI's skill documentation specifies `/skills` or `$` mentions for Codex CLI.
The repository's API reports the skill name `crux-flow:flow`, so its explicit
mention is `$crux-flow:flow`. Flow's operator guide now documents this. The
operator's selection of that skill in the live client remains unobserved; no
additional installation or changes to repository settings are needed for the
documented invocation path.

Sources: https://learn.chatgpt.com/docs/build-skills (explicit invocation), and
the installed release's `skills/list` response at
`/tmp/crux-flow-repo-skills-api.json` (exact Flow skill name).

Correction: the operator's acceptance criterion is Flow skills appearing in the
active Codex client for `omni-ui-components`. Successful installation, plugin
inventory, fresh prompt rendering, and initialization do not establish that.
Earlier completion reports overstated the result. The operator reaffirmed that
the failure is repository specific. Further installation-output work is parked
while diagnosing that failure.

The affected repository's `.codex/config.toml` enables `crux-flow@crux-flow` and
disables `crux@crux`. The user configuration does the opposite, intentionally
requiring repository activation. Both files were read and parsed. The running
terminal Codex process whose cwd is this repository uses the mise-installed
Codex 0.160.1, with HOME `/Users/desoleary` and no CODEX_HOME override. Its start
time is 13:01:50 MDT; the project plugin configuration mtime is 11:58:30 MDT.
These timestamps do not support assuming that the configuration was written
after this process started.

A separately launched Codex 0.160.1 app server was queried via `skills/list`
with `forceReload: true` and the exact repository cwd. It returned Flow skills
including `crux-flow:flow` with `enabled: true` and
`pluginId: crux-flow@crux-flow`, with no skill errors. The same request for the
`crux-flow` checkout returned no Flow skills. The full responses were opened
from `/tmp/crux-flow-repo-skills-api.json`. This demonstrates repository-specific
configuration in a fresh server; it still does not explain the active client.

The computer-use tool refused both Codex and iTerm inspection for safety reasons.
No alternate UI automation was used to bypass that restriction. A diagnostic
proxy to the shared app server did not reply; direct socket connection closed
before initialization. No existing process was killed or restarted. Only owned
diagnostic subprocesses were terminated.

Pending information: whether the missing entries are in `/skills`, `$`/`@`
autocomplete, or both, in the affected repository's active Codex session.
The operator was asked via the text-input tool. No root cause or UI fix is claimed.

Installation-reporting edits are uncommitted and were not refreshed into the
installed CLI. Their tests passed, but they do not satisfy skill discovery.
