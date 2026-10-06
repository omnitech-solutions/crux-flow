# h3\_decorator\_side\_effect.py

```text
HOSTILE FIXTURE: a decorator writes a marker when the decorated function is defined.
```

## `_mark`

```python
def _mark(fn)
```

_function · private · lines 6–8_

_Undocumented._

## `decorated`

```python
@_mark
def decorated() -> None
```

_function · public · lines 11–13_

```text
Decorated by a side-effecting decorator.
```
