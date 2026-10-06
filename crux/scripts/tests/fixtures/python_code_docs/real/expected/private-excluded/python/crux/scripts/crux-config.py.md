# crux/scripts/crux-config.py

```text
crux-config — back-compat delegator to `bionic-config.py` (ADR-0044).

Per ADR-0044, `.bionic.yml` is the layout source of truth and supersedes the
legacy `.crux` file (ADR-0032). The canonical CLI is now `bionic-config.py`;
this command is retained so existing skill prose and tooling that invoke
`crux-config.py` keep working during the deprecation window. It delegates to
`bionic-config.py`'s `main()`, so both command names behave identically
(same args, same JSON stdout contract, same exit codes, same two-file
precedence).
```

## Script metadata

```toml
requires-python = ">=3.11"
dependencies = []
```

_Private declarations not rendered: 3 (include_private is false)._
