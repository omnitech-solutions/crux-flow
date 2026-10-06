# \_hostile\_marker.py

```text
Shared helper the hostile fixtures use to locate their marker file.

Importing this module has no side effect; it is not itself hostile.
```

## `marker_path`

```python
def marker_path(fixture_file: str, name: str) -> Path
```

_function · public · lines 10–19_

```text
Return the marker path for `name`, honoring CRUX_HOSTILE_MARKER_DIR.

Falls back to a path beside `fixture_file` only when the environment
variable is unset.
```
