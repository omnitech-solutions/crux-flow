# Journal meaningful work

Read the shared obligations in `../SKILL.md` and this reference before any journal side effect. Journal mode adds the monthly entry, regenerates the derived index from month files, then writes one `journal` operation to the log. Use this branch interactively by default or when `--journal` is explicit. Silent journaling requires the caller's category and subject.

Draft a first-person entry that states the intent and at least one concrete failed attempt, surprise, or change you would make next time. A smooth routine operation without a useful reflection belongs in the log-only branch. In interactive mode, confirm the category, one-line subject and reflective body with the user. In silent mode, report a warning if the supplied body is only a summary; do not stop the caller for that reason.

The body has 1–10 lines. A `Friction:` line names one specific obstacle when one occurred; omit it otherwise. It counts toward the body limit and precedes `Refs:`. **Body lines must not begin with `## [`**; that prefix belongs only to entry headings. The writer refuses a malformed or unclosed Markdown fence. Rewrite a rejected body and retry with the same recorded minute before a surface commits.

The entry's reference line is `Refs: <wiki-links and rule:<slug> citations>`. Supply refs through `--refs`; each ref is a `[[...]]` wiki-link or a `rule:<slug>` citation. Cite `rule:<slug>` where a governing rule exists. A journal may cite an `ADR-NNNN` page where that ADR has no `governs` block. Omit the `Refs:` line when there are no refs; never demand wiki-link syntax for a rule citation.

Choose the category from the journal vocabulary in the configured operational schema. A category outside that vocabulary falls back to `misc` with a surfaced warning. This differs from a log operation: an invalid `--log-op` is refused, never mapped to a default. Give the writer a single-line subject and a caller-supplied ISO 8601 timestamp with an explicit UTC offset; `Z` is accepted and normalizes to `+00:00`. Choose the intended local civil minute and offset before invocation. The writer drops seconds without converting through the host timezone. Preserve that request and its original offset for retry; two identical requests at the same recorded minute name one entry. Different work at that minute needs a different subject or body. The writer refuses an ambiguous same-minute collision.

Write the authored body to a temporary UTF-8 file and pass its path, or supply it on stdin. Do not interpolate authored prose into shell command text. From the repository root, with `CRUX_PLUGIN_ROOT` derived from the selected `SKILL.md`, invoke:

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/write-journal.py" --repo-root <repo-root> --mode journal --at <aware-ISO8601> --category <category> --subject <subject> --body-file <body-file> --refs <refs>
```

Omit `--refs` when there are no references. `--body-stdin` replaces `--body-file <body-file>` when piping UTF-8 content. Pass arguments as separate shell arguments through the host's command facility; quote every value that may contain spaces. The writer checks the composed month and prospective derived index before its first mutation. If the existing month is malformed, journal mode refuses without repairing it.

Read the JSON report and exit code. `complete` means month, index and journal log entry recorded on disk, uncommitted. `refused` means no surface changed. `partial` names recorded and pending surfaces: keep the request and rerun it unchanged after the cause is corrected. The writer compares exact parsed entry bytes and completes only missing surfaces; it does not add a marker. Do not delete or rewrite the recorded entry to recover. If exit 2 yields no report, inspect the three surfaces before retrying the same request.

A month-only partial entry records no offset. Its retry can match the entry bytes while the offset remains unverified; surface the writer's `offset unverified` warning. A completed log record with the same civil minute and heading but a different offset makes the writer refuse before mutation. If the original offset was lost and no durable evidence recovers it, stop automated replay and seek evidence or human disposition. Never infer it from the month heading or the executing host.

Verify the entry appears ahead of prior entries, no prior bytes changed, `docs/journal/index.md` reflects the month file, and the log carries one `journal` operation. If `Refs:` is present, verify each ref is a `[[...]]` wiki-link or `rule:<slug>` citation. The writer's `complete` status establishes its mechanical writes; verify the truth and reflection of the authored body separately. Do not hand-edit the index.
