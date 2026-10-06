# Audit Docs — recover an older tree

Read this when a tree schema is not the current supported value, or when an
in-flight `.migrating` marker exists. The current distribution does not carry
its old tree-schema ladder. It preserves the tree and reports the mismatch.

## Recover schemas 2, 3, and 4

Use the public crux `v3.23.2` release, annotated tag
`c1298c4a9229ed41ae7017c25321d27e5b3f6e4d`, which resolves to commit
`08ee30ec2f1d1b4b0ce970f2e1582bb4f83cd20d`. Verify that mapping when
acquiring the release. Use one plugin version at a time and work on a copy or
backup of the project. The tagged release carries `audit-docs --migrate` and
the old 2→3→4→5 ladder. Run its dry run, apply each rung in order, and validate
the resulting schema-5 tree before loading the current distribution. The old
release's audit instructions and CLI own the actual migration procedure.

If a `.migrating` marker remains, do not clear or rewrite it with the current
version. On the copy, use the tagged release to inspect and resume the partial
4→5 move, or invoke its `migrate-tree.py --abandon` procedure only after the
old tool confirms the tree is coherent. An invalid marker or two trees without
a valid recorded source are ambiguous. Stop and inspect the originals; do not
choose a tree by directory name.

This tagged ladder is a supported route only for tree schemas 2, 3, and 4.
A missing, earlier, newer, or unrecognized `schema_version` is incompatible;
inspect the actual tree and matching release. Do not claim the tagged ladder
can convert it, reinitialize it, or bump its manifest by hand.

Promptbook format conversion is separate from the tree ladder. In the tagged
release, finish an active Markdown run only when its remaining prompts can
truthfully be completed. Archive it, then convert its book before its paired
run, preserving originals. An already archived eligible run can also be
converted. Validate each YAML book and run, including their content-hash
binding, before returning to the current release. The tagged release does not
provide deliberate Markdown abandonment. If a run cannot finish, preserve its
original bytes as stranded, readable history; neither release provides a
verified close-and-convert path for that state. New YAML work can continue
beside that record. Historical Markdown is not executable by the current
release.

## The instruction-file migration (independent of tree schema)

The current `audit-docs --migrate` procedure migrates repository instruction
files onto the canonical `AGENTS.md`. It is not a tree-schema rung: it has no
`schema_version`, and a schema-5 tree can still owe this migration. If the tree
needs the old schema ladder, finish that work on the tagged release first.

Delegated to the vendored script — **do not reimplement discovery or the set-aside here**:

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/migrate-instructions.py" --repo-root . --dry-run   # report only
uv run "${CRUX_PLUGIN_ROOT}/scripts/migrate-instructions.py" --repo-root . --migrate   # perform
```

Exit codes follow the shared convention: `0` clean, `1` findings or refusal with JSON on
stdout, `2` capability error on stderr. Run `--dry-run` first and show the user its
`actions`, `set_asides`, `refusals` and `suppressors` before performing the migration.
`docs/AGENTS.md` §18 is the contract; this section restates none of its rules.

### The two sets, which are not the same set

| | what it holds | what may happen to it |
|---|---|---|
| **mutation** | the entries and index paths of each touched scope of THIS checkout | renamed into `AGENTS.md`, set aside, or left where it is; `AGENTS.md` created |
| **suppression** | every `CLAUDE.md`, `.claude/CLAUDE.md` and `CLAUDE.local.md` under the checkout, tracked or not | reported only, never written |

A touched scope is a directory whose listing and index hold a tracked instruction
file that no exclusion claims. Discovery lists that directory and matches names by
lowercasing, so an untracked `AGENTS.md` or an `agents.md` beside a tracked
`CLAUDE.md` is found. A vendored dependency cache and a linked worktree hold no file
tracked here, so they fall outside the mutation set by construction.

### What the script does

- A lone `CLAUDE.md` becomes `AGENTS.md` with its bytes unchanged. A lone case
  variant takes the exact `AGENTS.md` spelling.
- Where a scope holds both families, `CLAUDE.md` wins. The new `AGENTS.md` holds its
  bytes, with each import line replaced by the losing file's bytes.
- An import line is `@`, an optional `./`, and the loser's listing name. On a
  case-folding volume, a name that lowercases to it also counts. A line inside a
  fenced code block is not an import line.
- Nothing else is merged, and no byte is decoded.
- A losing `AGENTS.md` whose bytes differ, tracked or not, is **set aside**. It is
  renamed in its own directory to `<name>.crux-set-aside-<hash>`, without replacing
  any entry. The report names it and says whether git ignores it. The report also
  says its content no longer loads, unless the winner imported it.
- At the root, a set-aside `AGENTS.md` may have held the pointer line and objectives
  block that init-docs wrote. Those lines then stop loading, unless the winner
  imported that file. Step 9 of the init-docs skill shows them. After a scope that
  stages, copy them into the new `AGENTS.md`.
- While an untracked or ignored `CLAUDE.md` stays in place, copy them into that
  `CLAUDE.md`, not into `AGENTS.md`, then run `--migrate` again. Each run sets aside an
  `AGENTS.md` whose bytes differ from the result it computes from that `CLAUDE.md`, so
  a line added to `AGENTS.md` alone stops loading at the next run. Or, where git does
  not ignore that `CLAUDE.md`, track it first with `git add`, then run `--migrate`
  again. Once a run stages the scope, copy them into `AGENTS.md`.
- A scope whose result would carry untracked or ignored bytes stages nothing. The
  winner stays where it is, and `AGENTS.md` is created beside it. The report says
  that committing `AGENTS.md` publishes those bytes.
- Refused, each with a next step, and changing nothing: a loser named by the
  denylist, a template, a symlink, or a file that is not a readable regular file.
- Also refused: a winner that is not a readable regular file, and two spellings of
  one family. An unmerged, skip-worktree or assume-unchanged index entry is refused too.
- Also refused: an ignored loser whose set-aside name git would not ignore. The next
  step moves that private file to a new name that git ignores and no harness loads.
- Also refused: a volume that cannot establish identity, or offers no no-replace
  rename. The next step is to migrate from a clone on a volume that offers them.
- Also refused: a scope whose directory is reached through a symlink.
- A refused scope blocks no other scope.
- An index path its directory no longer lists, which the plan does not remove, is
  reported with a next step that saves its blob first. That scope is a finding.
- Before a scope's first step, the receipt records the hash and index path of a
  tracked winner and of the loser input. A rerun after a stopped run reads that
  record, so it ends where an uninterrupted run ends, apart from a temporary entry the
  stopped run left.
- Excluded from mutation and reported with a named disposition: `CLAUDE.local.md`, a
  `CLAUDE.md` under `.claude/`, templates, symlinks, and every path the
  `instruction_migration_denylist` config key names. An absent key and an absent
  configuration are both an empty denylist rather than an error.

### Recovery and the honest limit

The whole plan is made before any mutation. No step replaces an existing entry or
unlinks a winner or a loser. `AGENTS.md` is written through a temporary entry and a
no-replace rename, so a stopped run leaves no partial `AGENTS.md`. A temporary entry
an interrupted run left is reported and never removed. A rerun reaches the state an
uninterrupted run reaches, and a second run on a completed tree changes nothing.

**No multi-file filesystem atomicity is claimed**, and no durability across power loss
for the written `AGENTS.md`. The receipt records each set-aside with its hash and git
state, each inlined import line, each index blob and change, and each refusal.

### Instruction-migration red flags

- About to delete a `CLAUDE.local.md` because it suppresses the canonical file.
  **NEVER.** It is private and untracked. Report it with its remedy.
- About to rename a tracked `.claude/CLAUDE.md` to `.claude/AGENTS.md`. **NEVER.** No
  host is known to load that path, so the rename would silence it while the receipt
  recorded a clean migration.
- About to delete a set-aside because its content "is already in `AGENTS.md`".
  **NEVER.** It is the user's file; report it and let them decide.
- About to move a losing `AGENTS.md` by hand with `mv` or `git mv` over another file.
  **NEVER.** Run the script; it never replaces an entry.
- About to log one entry per migrated file. **One `schema` op per migration.**
