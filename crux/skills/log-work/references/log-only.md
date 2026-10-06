# Log an operation without journaling

Read the shared obligations in `../SKILL.md` and this reference before any log-only side effect. This is the default for `--silent` without `--journal`. It writes one operation to `<docs_dir>/log.md` and changes no monthly journal file or journal index. The writer does not inspect malformed journal files in this mode.

The caller supplies a canonical `--log-op`, a one-line subject, and a one-line body saying what the caller did. Read the valid operations from `<docs_dir>/AGENTS.md` §6. A missing or invalid operation is refused before any write; never substitute `journal` or another default. The writer CLI also requires `--category`; pass `misc` because log-only records have no journal category. Supply a stable ISO 8601 timestamp with an explicit UTC offset, including the original offset on retry; `Z` normalizes to `+00:00`. The writer preserves the caller's civil minute and offset without using the executing host timezone. The normalized minute and offset remain in the ordinary log body, so distinct minutes remain distinguishable.

Write the body to a UTF-8 file, or provide it on stdin. Do not interpolate authored prose into shell command text. Invoke the bundled writer from the repository root:

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/write-journal.py" --repo-root <repo-root> --mode log-only --at <aware-ISO8601> --category misc --log-op <canonical-op> --subject <subject> --body-file <body-file>
```

`--body-stdin` replaces `--body-file <body-file>` when piping UTF-8 content. Quote argument values that contain spaces. Read its JSON `status`, `warnings`, `committed`, `pending`, `written` and `error`, together with the exit code. `complete` at exit 0 means one log operation is present. `refused` at exit 1 means nothing changed. `partial` at exit 1 names the surfaces that committed and remain pending; replay the exact request, including timestamp and offset, after fixing the cause. A completed log record with the same civil minute and heading but a different offset makes the writer refuse before mutation. If the caller loses the original offset, stop automated replay rather than deriving it from the host. An unreportable exit 2 requires a disk check before replay.

Verify the new entry is ahead of earlier log entries and earlier bytes are intact. Confirm the monthly journal file and `journal/index.md` are byte-unchanged. Never invoke the journal index regenerator, even in check or dry-run mode, for this operation.
