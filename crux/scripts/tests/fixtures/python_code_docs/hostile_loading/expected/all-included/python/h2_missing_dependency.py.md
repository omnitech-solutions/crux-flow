# h2\_missing\_dependency.py

```text
HOSTILE FIXTURE: writes a marker, imports a package that does not exist, then raises.
```

## `unreachable_at_runtime`

```python
def unreachable_at_runtime() -> None
```

_function · public · lines 12–13_

```text
Defined after the raise; a static reader still sees it.
```
