# Install, upgrade, and schema guidance

## Codex CLI (takes precedence in Codex)

The Codex marketplace entry for this repository lives at
`.agents/plugins/marketplace.json`; it packages the `crux/` directory, whose
`.codex-plugin/plugin.json` exposes all bundled skills.

For a first install, provide these commands from any terminal. Do not run them
unprompted because they change the user's configured plugin marketplaces:

<!-- provenance: the `codex plugin marketplace add` → `codex plugin add` install
     CLI syntax below is an external contract owned by OpenAI, not by crux.
     Verified 2026-07-09 against the official docs
     (https://developers.openai.com/codex/plugins/build) and the local
     `codex plugin --help` / `plugin add --help` / `plugin marketplace add --help`
     for codex-cli 0.144.0: `marketplace add <SOURCE>` accepts an `owner/repo`
     source, and `plugin add <PLUGIN[@MARKETPLACE]>` accepts the `crux@crux`
     positional. The docs additionally confirm `marketplace list/upgrade/remove`
     and `plugin list/remove`. No live-network validation is performed — upstream
     drift is diagnosed by comparison against this dated citation. -->
```bash
codex plugin marketplace add bionic-coding/crux
codex plugin add crux@crux
```

Start a new Codex thread after installation so its skill inventory is refreshed.
Then invoke `install-codex-agents`. The agent installer targets the active
user's `~/.codex/agents/` directory by default. Pass `--repo-root` only for an
explicit project installation. Plugin installation supplies the skill
resources; the agent installer binds each role to its declared skills.

Pass the current repository as `--project-context` when checking a personal
installation. The resulting health report detects project agents that shadow a
personal Crux role. It separates canonical expectations from the managed TOML
state parsed from disk. Static health cannot prove host discovery or instruction
loading, so runtime verification remains `unverified` until fresh-session host
evidence exists.

For an upgrade, refresh the configured Git marketplace and reinstall:

```bash
codex plugin marketplace upgrade crux
codex plugin add crux@crux
```

After the upgraded plugin loads in a new thread, invoke
`install-codex-agents` again. A changed plugin path changes the absolute skill
bindings and appears as managed drift. Review that drift before refreshing the
managed agent files with `--force`.

If Codex reports that no marketplace named `crux` exists, repeat the first
install command. Use `codex plugin marketplace list` and `codex plugin list` to
inspect configured marketplaces and installed plugins. Never search or modify
`~/.claude/plugins/` or use Claude Code slash commands in a Codex session.

## Claude Code Instructions

The install-guidance skill. Its job is to make the Claude Code install path for
`crux` discoverable and unambiguous without taking any installing action itself.
The instructions in this section apply only to Claude Code; Codex uses the
commands above.

```
/plugin marketplace add bionic-coding/crux
/plugin install crux@crux
```

Slash commands are typed by the user in the Claude Code UI — this skill cannot run them. Report the active version only from the selected loaded skill or host load trace. A marketplace cache supplies an installed candidate, not active-session identity.

When a copy is found, this skill reports its version and frontmatter `schema_version` from `plugin.json`, the supported tree schema from `templates/manifest.yml.tmpl`, and the bundled skills and agents. Label those facts as active only when the selected root is proved. When no copy is found, print the marketplace commands.

Pairs with `init-docs` for a new tree. The current `audit-docs --migrate` procedure handles instruction files only. For a tree at schema 2, 3, or 4, use the pinned public `v3.23.2` release's schema ladder on a copy before returning to the current version. See [`audit-docs` recovery guidance](../../audit-docs/references/migrate-mode.md).

## When to use

- User says: "install docs skills", "add this plugin", "set up crux", "install the docs plugin".
- User says: "upgrade docs suite", "update the plugin", "pull the latest docs skills".
- User asks: "what version is installed?", "is crux up to date?".
- A session where crux skills are expected but the plugin isn't loaded.
- `audit-docs` or another skill detects that the installed plugin version is older than what `docs/manifest.yml` expects.

Do **not** use this skill for:
- Bootstrapping `docs/` content inside the target repo — that's `init-docs`. This skill guides the plugin install; `init-docs` creates the docs tree.
- Migrating an older tree schema in the current release — use the pinned recovery release for schemas 2–4.
- Adding unrelated plugins. This skill knows about `crux` only.
- Editing the plugin source (a development checkout of the crux repo). This skill is about the installed plugin.

## Default mode: informational

This skill is **purely instructional**. The behavior is:

1. Detect installation state.
2. If a copy is found, read `plugin.json` and distinguish an active selected root from a cached candidate.
3. Print the two marketplace commands verbatim.
4. Stop. The user runs the slash commands; the marketplace does the rest.

There is no Bash-install path. Marketplace commands are user-typed slash commands; there is nothing for this skill to execute.

## The pipeline

### 1. Detect the current state

Check, in order:

1. **The selected skill path** — derive `${CRUX_PLUGIN_ROOT}` from the absolute path of the Crux `SKILL.md` actually loaded for this invocation. Claude Code may supply `CLAUDE_PLUGIN_ROOT` inside that loaded skill; it can be empty in an unrelated Bash process. Read `${CRUX_PLUGIN_ROOT}/plugin.json` to identify this active copy.
2. **The marketplace plugin cache** — look for a `crux` plugin directory under `~/.claude/plugins/` (the cache layout may nest by marketplace; search for a directory containing a `plugin.json` whose `name` is `crux`). A cache hit proves only that a copy exists on disk. `--plugin-dir` and disabled marketplace plugins can make another copy active; never report a cache hit as the active version without the selected skill path or host load trace.

Two states:

- **Absent** → first install. Skip to step 3 (print the marketplace commands).
- **Present** → when step 1 proved the selected root, read that root's `plugin.json` and report its active version. Otherwise, read the cache manifest only as an installed candidate; label its version cached and leave active identity unverified. Then print the upgrade guidance (step 4).

If the selected root's `plugin.json` is missing or malformed, report that the active copy looks broken. If only a cached candidate is missing its manifest, report that candidate as invalid without inferring which copy is active. Recommend re-running `/plugin install crux@crux` for a broken installation; never repair the cache by hand.

### 2. Read `plugin.json` if installed

Path: `${CRUX_PLUGIN_ROOT}/plugin.json` when the selected skill proved that root; otherwise, the candidate cache path found in step 2 above.
If active identity is unverified, do not apply the candidate manifest's compatibility verdict to the current session. Report the candidate as cached and identify the loaded `SKILL.md` path or host load trace before making an active-version claim.

Fields to extract:

- `name` — should be `crux`.
- `version` — semver of the installed plugin.
- `schema_version` — the **SKILL.md frontmatter contract** version. This is NOT the tree schema version, and comparing it to a tree's `manifest.yml` value is a category error: the two are separate axes that have never held the same number. The supported TREE value comes from the identified copy's `templates/manifest.yml.tmpl`, which its `init-docs` writes. A cache-only template describes that candidate, not the plugin active in this session.
- `skills` — list of skill names bundled in this version.
- `agents` — list of bundled agents (when present).
- `scripts` — list of helper scripts.

Sanity checks:
- If `name` is not `crux`, refuse: "Plugin at `<path>` reports name `<X>`, not `crux`. Inspect the directory; do not assume it's this plugin."
- If `version` is missing or not semver, warn and proceed.
- If `templates/manifest.yml.tmpl` is missing or carries no `schema_version`, warn — the schema-compatibility check will be skipped. A missing `plugin.json` `schema_version` does not skip it, because that field is not its input.

Resolve whether the target repo has an existing tree via `bionic-config.py --repo-root "<target repo>"`, or by checking `.bionic.yml`, legacy `.crux`, `bionic/`, and `docs/`. A config error or ambiguous pair refuses; do not guess a tree. Read the tree's `manifest.yml` `schema_version` and compare it to the value in the identified copy's `templates/manifest.yml.tmpl`. Never compare it to `plugin.json`'s `schema_version`, which governs skill frontmatter. Check for a `.migrating` marker before reporting compatibility; a partial migration needs the tagged release even when one manifest already says `"5"`. If the copy is only a cached candidate, label this comparison candidate-only and leave current-session compatibility unverified.

Compare the tree's value against the supported value you just read from the template — **never against a number written in this file, and never against `plugin.json`.** This prose has shipped stale twice; the shipped template is the only source of truth.

- **Equal (match)** → the identified copy supports this tree. Say it operates in the current session only when the selected root or host load trace proved that copy active.
- **Schema 2, 3, or 4** → the identified current-version copy refuses this tree; a cache-only comparison says nothing about the active session. On a copy, acquire public crux `v3.23.2`, verify its tag resolves to commit `08ee30ec2f1d1b4b0ce970f2e1582bb4f83cd20d`, run its ladder in order, validate schema 5, then return to the current version. Keep only one active plugin version.
- **Valid `.migrating` marker** → on the copy, use that tagged release to resume or abandon the recorded migration. The identified current-version copy leaves the marker untouched; a cached candidate does not establish what the active session loaded.
- **Missing, earlier, newer, or unrecognized schema; invalid marker; ambiguous trees** → STOP. Report the exact state without promising that tagged release can convert it. Do not reinitialize, downgrade, or alter the tree during install guidance.

### 3. Print the install commands (absent state)

Print to the user:

```
crux is not installed.

Install it with two slash commands in Claude Code:

    /plugin marketplace add bionic-coding/crux
    /plugin install crux@crux

Then restart the session so the plugin's skills and agents register.
After installation, say "init docs" to bootstrap the project's docs tree.
```

### 4. Print the upgrade guidance (present state)

If the selected root or host load trace proved the active copy, print:

```
crux is active in this session.

Active:       version <X.Y.Z>, frontmatter schema_version <N>
Tree schema:  supported <from templates/manifest.yml.tmpl>; found <from the target manifest>
Tree state:   <compatible | recover schema 2–4 with v3.23.2 | partial marker | incompatible>
Skills:       <count> — <comma-separated names from plugin.json>
Agents:       <count> — <comma-separated names from plugin.json>
```

If only a cache copy was found, print instead:

```
A Crux copy exists in the marketplace cache; the active copy is unverified.

Cached candidate: version <X.Y.Z>, frontmatter schema_version <N>
Candidate tree schema: supported <from its templates/manifest.yml.tmpl>; found <from the target manifest>
Current-session tree compatibility: unverified until the selected Crux SKILL.md path or host load trace identifies the active copy.
Skills/agents in cached candidate: <counts and names from its plugin.json>
```

After the verified active-copy block, print the upgrade guidance:

```

To upgrade, refresh the marketplace and re-install:

    /plugin marketplace update crux
    /plugin install crux@crux

(If the marketplace was never added in this environment, run
`/plugin marketplace add bionic-coding/crux` first.)

Upgrading does not touch the existing tree. Recover schemas 2–4 on a copy
with verified crux v3.23.2 before using a confirmed current-version copy. For an existing
schema-5 tree, `audit-docs --migrate` remains available for instruction files.

An upgrade applies the gate check to every cycle run in flight at its next gate
prompt. A module whose council ran before council records existed closes only on a
council-runner round convened at module close. No book's bytes change. A template change fixes no existing
book. The gate check binds hashes, not content. See `run-promptbook`'s
`references/gates.md`.

After the upgrade, refresh the installed roles so they carry the new council and
review text. Claude Code reads the roles from the plugin, so restart the session.
In Codex, run `install-codex-agents` again. In OpenCode, run
`install-opencode-agents` again for a project install, or regenerate the symlinked
tree and restart.

Before a run's next gate prompt, store the gateway key
(`crux-env set OPENROUTER_API_KEY ...`) and commit every council subject. The
council runner commits its attempt record and its council record itself; do not
commit either by hand. The gate check reads only committed records. A hook that
rewrites a council file (a JSON formatter with another indent or key order, for
example) must exclude `<docs_dir>/promptbooks/runs/`, for example
`exclude: ^<docs_dir>/promptbooks/runs/` in the pre-commit framework. Otherwise
each council commit whose file the hook rewrites fails closed
(`hook-or-commit-failed`, or `mismatch` when the hook re-stages its rewrite) and
stops for the owner. A hook slower than the commit's 120-second bound makes the
commit time out and leaves the attempt open. When the commit times out, or a
refused commit names outside work it moved, look first for the work the hook
set aside (`git stash list`; the pre-commit framework keeps a backup patch under
its cache directory) and restore it. Only then remove a stale `index.lock` in the
git directory, and run recovery. Councils in a promptbook run need `fcntl`, which
native Windows lacks. There the council runner exits 2 before it claims a round,
and recovery and the run-work witness writer exit 2 as well, so no council gate
can pass. Run promptbook councils on macOS, Linux or WSL. The council runner
refuses a `--prompt` that is not the run's current prompt, so the retry count
always names the prompt the run is at. After each council record the council
runner also commits the run's diagnostics log and run-work witness. When the
council runner exits 2 and stderr names `timeout`, or names outside work the
commit moved, the owner's remedy above comes first: restore the set-aside work,
then remove a stale `index.lock`. After those two steps, and after any other
exit 2 or an open attempt, run the process check and probe the lock with
`run-council.py --recover <run> --prompt <n> --probe`. Once no live
council runner holds it, run `run-council.py --recover <run> --prompt <n>`, never
a new round. A stop for a council that could not run clears: its record takes no
round place, so once the owner fixes the cause you reconvene at the same round
number. A stop on a round no owner exception authorizes clears when the owner
commits an owner-exception record and that round converges. Every other council
stop is permanent for the module once its record is committed, so abandon the run
and author a successor book.

Every `--outcome` of `advance-run.py`, at a gate prompt or not, needs a book whose
content hash matches the run's `book_content_hash`. A route, or a re-advance of a
blocked prompt that holds a result or artifacts, appends a dated entry to the run snapshot's `notes`
field. A `notes` value written as plain, folded or non-empty quoted text refuses that advance
with nothing written. Rewrite `notes` as a literal block (`|`) and run it again.
```

After a cache-only block, first tell the user to identify the active Crux `SKILL.md` path or host load trace. Do not recommend an upgrade based on the cached candidate. Offer the marketplace commands only as optional install guidance, without claiming which version is active:

```
Active Crux identity is unverified. Check the selected skill path or host load trace before deciding whether an upgrade is needed.

If you choose to install or refresh Crux, use the marketplace UI:
    /plugin marketplace update crux
    /plugin install crux@crux

If the marketplace has not been added here, first run:
    /plugin marketplace add bionic-coding/crux
```

### 5. Post-install hand-off

After the user reports the install succeeded, verify the selected Crux `SKILL.md` root or a host load trace. A cache-only copy is not confirmation; if active identity remains unverified, report that state and defer the version-specific hand-off and upgrade log. Once the loaded copy is proved:

- Confirm `plugin.json` is readable and `name == "crux"`.
- Resolve whether the target repo already has an existing tree — via `bionic-config.py --repo-root "<target repo>"`, or directly by checking for `.bionic.yml`, a legacy `.crux`, a `bionic/` tree, or a legacy `docs/` tree. Any one of these existing counts as an existing installation. Never key this on literal `docs/` absence alone: a repo bootstrapped under the default `bionic/` layout has no `docs/` at all and would be misclassified as a fresh install otherwise.
- **If none of these exist (fresh install)**, print:

  ```
  Plugin installed: crux v<X.Y.Z> (schema_version <N>)
  Next step: say "init docs" to bootstrap the docs tree in this project.
  ```

- **If any of these exist (upgrade of a bootstrapped project)**, do NOT print the "init docs" hand-off. Print instead:

  ```
  Plugin upgraded: crux v<X.Y.Z> (schema_version <N>)
  Check the tree schema against templates/manifest.yml.tmpl. Recover schemas
  2–4 with verified crux v3.23.2 on a copy before current-version use. For a
  compatible schema-5 tree, audit the documentation and migrate instruction
  files with `audit-docs --migrate` only if needed.
  ```

- Do **not** invoke `init-docs` automatically. The user decides when to bootstrap.

### 6. Log the operation

If an upgrade actually happened in this session (the user ran the slash commands and the selected loaded root or host load trace confirmed the new version), and the target repo has an existing tree (`.bionic.yml`, a `bionic/` tree, or a legacy `docs/` tree — same detection as step 5) whose `log.md` already exists (i.e., this was an upgrade of an already-bootstrapped project), append a `schema` op entry to that tree's `log.md`:

```
## [YYYY-MM-DD] schema | install-docs-skills (<install | upgrade>) v<X.Y.Z> schema_version <N>
```

Body, 1–2 lines:
- Before-version and after-version.
- Whether tagged tree recovery or current instruction-file migration is needed.

If no tree exists yet (fresh project), there's no `log.md` to write to — skip the log entry. The next `init-docs` run will start the log.

## Verification checklist

- [ ] The selected loaded root or host load trace proves active identity; otherwise any cache result is labeled a candidate and active identity remains unverified.
- [ ] If present, `plugin.json` was read and `name == "crux"` was verified.
- [ ] `version` and `schema_version` were labeled active only for a proved selected root; cache-only values were labeled cached candidates.
- [ ] The full skill list (and agent list, when present) from `plugin.json` was surfaced.
- [ ] The two marketplace commands were printed verbatim: `/plugin marketplace add bionic-coding/crux` and `/plugin install crux@crux`.
- [ ] No install action was attempted by the skill itself — marketplace commands are user-typed.
- [ ] Existing-tree detection checked for `.bionic.yml`, a `bionic/` tree, and a legacy `docs/` tree — never literal `docs/` absence alone.
- [ ] If the target repo has an existing tree, its `manifest.yml` was compared to the identified copy's template; a cache-only comparison was not reported as current-session compatibility.
- [ ] The hand-off ("next: say 'init docs'") was printed after a fresh install only — not after an upgrade of an already-bootstrapped project.
- [ ] If an upgrade was confirmed AND the existing tree's `log.md` exists, one `schema` op entry was appended.

## Red flags — STOP and reconsider

- About to run a shell command to install or upgrade the plugin. **Never.** The
  user types the marketplace command. The separate Codex agent installer is not
  a plugin installation path.
- About to delete or rename anything under the plugin cache to "clean up". **Never.** A broken install is reported; the fix is re-running `/plugin install crux@crux`.
- About to bootstrap `docs/` from this skill. **Never.** That's `init-docs`. The skills are split on purpose.
- About to edit `.claude/settings.json` or any marketplace state by hand. The marketplace flow owns registration; this skill does not.
- About to report a plugin at some path as crux when its `plugin.json` says a different `name`. Refuse — could be a collision with another plugin.
- About to recommend an upgrade across an incompatible tree schema without naming the pinned recovery path or explicit unsupported state. Stop and report the actual schema and marker state.
- About to invent install commands or paths. The commands are `/plugin marketplace add bionic-coding/crux` + `/plugin install crux@crux`, period.
- About to claim the plugin is installed because a directory exists but `plugin.json` is missing or malformed. Validate before reporting.
- About to report a cached plugin version as active without the selected Crux `SKILL.md` path or host load trace. A disabled marketplace copy can coexist with `--plugin-dir`; label the cache a candidate.
- About to key fresh-install-vs-upgrade detection on literal `docs/` absence alone. **Never.** A `bionic/`-only repo (the default layout) has no `docs/` at all; check for `.bionic.yml`, a legacy `.crux`, a `bionic/` tree, or a legacy `docs/` tree.

## Rationalization table

| Excuse | Reality |
|--------|---------|
| "The user obviously wants the install — let me find a way to run it." | Marketplace commands are user-typed slash commands. Print them; the user runs them. There is nothing for this skill to execute. |
| "I'll skip reading `plugin.json` since I know the structure." | Read the selected root's manifest for active facts, or a cache manifest for candidate facts. The file supplies version, schema and skill list; its location determines which claim it supports. |
| "I'll auto-run `init-docs` right after install." | No. Install and bootstrap are separate user decisions. After install, hand off to the user. |
| "The cache directory exists, so that plugin is active." | Existence is not validity or active identity. Validate `plugin.json` for candidate facts, then use the selected skill root or host trace for active facts. |
| "`schema_version` mismatch is fine — `init-docs` will sort it." | `init-docs` does not migrate. Schemas 2–4 use the pinned v3.23.2 ladder on a copy. Other values need investigation. |
| "I'll edit the plugin cache or settings myself to register the plugin." | That's the marketplace flow's job. Don't reimplement it. |
| "The user said 'set me up' — that covers everything including bootstrap." | "Set me up" is ambiguous. Report install state first, then ask about bootstrap. Two operations, two confirmations. |
| "I'll skip the log entry since the install isn't a docs op." | Plugin upgrades affect the docs schema; that's a `schema` op when the existing tree's `log.md` exists. |
| "There must be a curl/clone fallback for users without marketplace access." | There isn't. Marketplace-only distribution is the architecture. Don't invent one. |

## Common mistakes

- **Attempting to execute the install** instead of printing the commands. Slash commands run in the user's UI, not in Bash.
- **Skipping `plugin.json` validation** when reporting a version. The directory's mere presence proves neither a valid cached copy nor the active one.
- **Using a valid cached manifest as active-session identity.** Derive the selected loaded skill root or inspect the host load trace first.
- **Auto-bootstrapping the tree after install.** Install and bootstrap are separate.
- **Forgetting the marketplace-add step** when guiding a fresh environment. `/plugin install crux@crux` fails if the `crux` marketplace was never added.
- **Writing to a tree's `log.md` for a fresh install** (where no tree, and so no `log.md`, exists yet). Skip the log entry on fresh installs; the next `init-docs` starts the log.
- **Using a non-`schema` op for the install/upgrade log entry.** The op enum is fixed; `schema` is the right op for plugin/schema-version changes.
- **Reporting the skill list from a directory listing** instead of `plugin.json`. The manifest is the truth; a stale or partial directory listing is not.
- **Treating a different-name plugin at the same path as crux.** If `plugin.json` says it's something else, surface that and stop.
- **Skipping the schema comparison** when the target repo has an existing tree (`.bionic.yml`, `bionic/`, or legacy `docs/`). Compare the identified copy's template with the tree; label a cache-only comparison candidate-only until active identity is proved.
- **Telling the user to run "init docs" after an upgrade of an already-bootstrapped project.** That is a fresh-install instruction. Check compatibility, use tagged recovery for schemas 2–4, and audit the resulting schema-5 tree.
