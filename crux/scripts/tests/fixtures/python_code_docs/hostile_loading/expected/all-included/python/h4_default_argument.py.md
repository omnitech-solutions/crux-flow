# h4\_default\_argument.py

```text
HOSTILE FIXTURE: a default argument expression writes a marker at definition time.
```

## `_touch`

```python
def _touch() -> int
```

_function · private · lines 6–8_

_Undocumented._

## `with_default`

```python
def with_default(x: int = _touch()) -> int
```

_function · public · lines 11–13_

```text
Its default is computed by a side effect.
```
