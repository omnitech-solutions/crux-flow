# The legacy Claude compatibility adapter (reference)

Read this only when `check-claude-compat.py` reports an unsupported runtime and the
user asks for the instructions to load anyway. Nothing here is part of a normal
install, and no crux surface points a reader at it as a remedy for anything else.

## What the adapter is

A `CLAUDE.md` **derived** from the canonical `AGENTS.md` of the same scope. It is a
projection, never a source: edits belong in `AGENTS.md`, and the next regeneration
discards anything written into the adapter.

It carries a generated header naming its source and that source's digest:

```markdown
<!-- GENERATED compatibility adapter. Source: AGENTS.md (sha256:<digest>).
     Edits here are discarded. Edit AGENTS.md instead. -->
```

## Why it is not simply a second copy

Because a Claude-named file **suppresses** `AGENTS.md` rather than competing with it.
That is the adapter's whole function and its whole hazard: it works precisely because
it silences the canonical file, so a stale or forgotten adapter becomes the effective
instruction source without anyone choosing that.

Three rules follow, and none is optional:

1. **It is ignored, never committed.** Add one root-anchored path entry per adapter —
   `/CLAUDE.md`, `/bionic/CLAUDE.md` — never a bare `CLAUDE.md` pattern, which would
   also shadow a tracked fixture elsewhere in the repository.
2. **One per managed scope, not one at the root.** A root adapter suppresses every
   `AGENTS.md` beneath it. Generating only the root one restores the root instructions
   while silencing the documentation tree's schema — the larger of the two files, and
   the one carrying the operational contract. The audit reports an adapter at one
   managed scope with none at another as `incomplete`.
3. **It is removed when the runtime becomes supported.** The audit reports an adapter
   whose digest no longer matches its source as `stale`, and one present on a supported
   runtime as `removable`. A supported host with a leftover adapter is a host reading
   an unmaintained copy.

## When it does not apply

`managed-only` drops the project's checked-in **and private** files alike, so no file
this repository writes can restore them. Its remedy is a configuration change. The
compatibility verdict reports `adapter_applies: false` for that mode, and offering the
adapter there would be advice that cannot work.

## The commands

Check first — an unsupported verdict is the only reason to generate anything:

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/check-claude-compat.py" --repo-root .
```

Audit any adapters already present. This reads and never writes, and it is the
surface behind the three reports above:

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/build-claude-adapter.py" --repo-root .
```

Generate, for every managed scope at once:

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/build-claude-adapter.py" --repo-root . --generate
```

`--generate` refuses on a supported host unless `--force` is passed, because an
adapter there only shadows the file the host already reads. It covers every managed
scope or none, for the parent-suppresses-child reason above. The compatibility
verdict also carries the audit under its `adapters` key, so a single
`check-claude-compat.py` run reports both the host and any adapter on it.

Exit `0` supported, `1` unsupported with the verdict JSON on stdout, `2` capability
error. The verdict names the effective instruction files and the inputs it derived
them from — version, distribution, built-in state, effective mode, and the suppressor
scan — so the reason for an unsupported result is visible rather than inferred. Read
`reasons` before generating anything; three of the five causes are repaired by a
setting rather than by a file.
