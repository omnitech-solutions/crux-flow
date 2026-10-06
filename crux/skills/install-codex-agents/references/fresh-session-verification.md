# Fresh-session Codex agent verification

Use this procedure to test one Codex client version against one installed Crux
plugin. Run it in an isolated user profile and a disposable repository. Keep
the complete host transcript as evidence.

The current release has no recorded fresh-session evidence. Health reports
`runtime.status: unverified`, `client_version: null`, and
`reason: fresh-session host evidence has not been recorded` until an approved
evidence record exists and the health reporter consumes it.

## Record the test environment

Record these values before installation:

- test date and operating system;
- exact Codex client version from the host;
- Crux plugin version and resolved plugin root;
- install `scope` and resolved `target`;
- whether the target is the default `~/.codex` home or an alternate
  `--codex-home`;
- the installed skill path form, which is the absolute path to each
  `<plugin>/skills/<name>/SKILL.md` resource;
- disposable repository path used as `--project-context`.

Evidence for the default Codex home does not verify an alternate
`--codex-home`. Record and test each target form separately.

## Install and inspect

Run the installer from the selected plugin:

```bash
uv run <skill-dir>/scripts/install.py --project-context <disposable-repo>
uv run <skill-dir>/scripts/install.py --check --project-context <disposable-repo>
```

For an alternate home, add `--codex-home <path>` to both commands.

Require these static results:

- `scope`, `target`, and `plugin` match the recorded environment;
- `drift.status` is `clean`;
- the canonical `roles` array contains all ten Crux roles;
- every canonical role has the expected `model` and
  `model_reasoning_effort`;
- every `declared_skills` entry has one absolute `resolved_skills` path;
- every resolved path exists under the recorded plugin root;
- `installed.status` is `clean`;
- every `installed.roles` record has status `installed`;
- every installed role's model and effort match its canonical role;
- every `skill_bindings` entry has matching `expected_path` and
  `installed_path` values and status `enabled`;
- shadow findings are absent or recorded and explained.

The canonical `roles` array states what the selected plugin expects.
`installed.roles` records what the health check parsed from each managed TOML
file. A missing, unsafe, or malformed managed file appears in the installed
record instead of being inferred from the canonical role.

An unchanged second install must return `written: []` and must preserve each
managed file's modification time.

## Start a fresh session

Close the installation session. Start a new Codex session with the recorded
client version, user profile, target, and disposable repository. Do not use a
session that existed before installation.

Use host metadata or host diagnostics to record all ten discovered role names:

- `crux_architect`
- `crux_brainstormer`
- `crux_commander`
- `crux_dev_lead`
- `crux_developer`
- `crux_historian`
- `crux_librarian`
- `crux_night_gardener`
- `crux_reviewer`
- `crux_wayfinder`

A role's self-report is not discovery evidence.

## Verify models, efforts, and skills

For every discovered role, capture host-observed metadata for the effective
model and reasoning effort. Compare both values with the matching health-report
canonical role and installed role records. A response that states its own model
or effort is not evidence.

For every skill in every role's `declared_skills`, capture a host signal that
the skill is available and enabled for that role. The signal must identify the
resolved skill resource or an equivalent host identity. A missing, disabled,
or undiscoverable skill fails the verification. If the host exposes no such
signal, keep runtime verification `unverified`.

## Exercise representative workflows

Seed the disposable repository with the smallest valid docs tree and fixed
fixtures. Dispatch these workflows in the fresh session:

- `crux_architect`: propose an ADR from a supplied fixture brief.
- `crux_historian`: record one supplied completed-work fixture in the journal.
- `crux_librarian`: answer a fixed question from a seeded docs fact and cite
  its source.
- `crux_reviewer`: review a fixed diff containing one known blocking defect.

For each workflow, record the parent dispatch, selected role, host tool events,
skill selection, output, and expected result. The transcript must show that the
role instructions loaded. A role claim that it loaded instructions is not
evidence.

## Decide the verdict

Mark the evidence record passed only when all of these conditions hold:

1. The fresh session discovers all ten roles.
2. Host metadata confirms every role's model and reasoning effort.
3. Host evidence confirms every declared skill is available and enabled.
4. All four representative workflows produce their expected results.
5. The record names the exact Codex version, plugin version, plugin root, and
   installation target.

Any missing host signal leaves runtime verification `unverified`. Record a
failed observable separately, including the role, skill, workflow, and host
output. Do not replace missing evidence with an agent self-report.

This procedure verifies discovery, settings, skill availability, and four
workflows. It does not prove that Codex eagerly loads every skill instruction.
It does not grant tools, credentials, sandbox access, or deeper delegation.
