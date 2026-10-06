# Verification for Crux Flow 0.2.0

Executed on October 5, 2026 in the current isolated Linux environment. These are
this completion run's results, not the prior Codex transcript's test claims.

- Flow: **124 passed, 4 skipped in 74.83s (0:01:14)**.
- Original Crux integration/core: **688 passed, 3 skipped, 2943 subtests passed in 67.43s (0:01:07)**.
- Catalog and runtime-compatibility dry runs: exit 0, no drift.
- Release archive inspection and ZIP integrity: passed.
- Repeated build: byte-identical archives.
- Relocated standalone source rebuilt the exact same release bytes: passed.
- Relocated source and packaged CLI: modes, guide and all four host-policy inspection paths passed.

`final/report.json` records commands, deadlines, elapsed times, exit codes and
output digests. Its neighboring logs contain the actual test summaries and skip
reasons. `release-smoke.json` records artifact checks. `environment.json` identifies
the interpreter, dependency versions and absent host executables.

The four native tests are opt-in and were skipped; no native clients were installed.
Three upstream tests apply only to the private authoring checkout and were skipped.
The full upstream parser/architecture suite was not run: its preflight reports six
missing optional extractor modules. Package retrieval was unavailable here; the
`upstream-test` dependency group and `tools/verify.py --full` provide its runnable
procedure. No paid-provider or Desktop observation is claimed.

Selected red logs preserve the reproduced failure evidence before repair, including
the baseline serialization failure and final Codex skill-binding regression. They
are not unresolved failures in the final test run. Source-symbol and change-inventory
files support the implementation report. No original transcript or flattened input
exports are included in this verification directory.

No fresh independent agent review was available in this execution environment.
Self-review and executable regressions are not presented as independent acceptance.
