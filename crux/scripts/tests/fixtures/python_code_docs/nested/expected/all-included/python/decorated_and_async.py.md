# decorated\_and\_async.py

```text
Decorated and async nested defs.
```

## `outer`

```python
def outer()
```

_function · public · lines 6–18_

```text
Host two nested defs with different decoration.
```

### `outer.<locals>.wrapped`

```python
@functools.wraps(outer)
def wrapped()
```

_nested function · public · lines 9–12_

```text
A decorated nested function.
```

### `outer.<locals>.fetch`

```python
async def fetch()
```

_nested async function · public · lines 14–16_

```text
An async nested function.
```
