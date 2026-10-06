# function\_rebindings.py

```text
Function-scope rebindings where the nested def stays documented.
```

## `make_config`

```python
def make_config()
```

_function · public · lines 4–17_

```text
Builds nested config helpers.
```

### `make_config.<locals>.loader`

```python
def loader()
```

_nested function · public · lines 7–8_

_Gap: `make_config.<locals>.loader` at unconditional (nested function, line 7) is rebound by assignment at line 10 and stays documented above; the rebinding is not reconstructed._

```text
Original nested loader.
```

### `make_config.<locals>.reader`

```python
def reader()
```

_nested function · public · lines 12–13_

_Gap: `make_config.<locals>.reader` at unconditional (nested function, line 12) is rebound by import at line 15 and stays documented above; the rebinding is not reconstructed._

```text
Original nested reader.
```
