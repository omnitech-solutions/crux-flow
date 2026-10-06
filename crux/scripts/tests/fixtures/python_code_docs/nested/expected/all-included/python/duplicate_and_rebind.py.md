# duplicate\_and\_rebind.py

```text
Duplicate-bound and rebound nested defs.
```

## `build`

```python
def build()
```

_function · public · lines 4–21_

```text
Exercise shadowing and rebinding of nested defs.
```

### `build.<locals>.helper`

```python
def helper()
```

_nested function · public · lines 11–13_

_Gap: `build.<locals>.helper` at unconditional rebinds `nested function` (line 7); `nested function` (line 11) is documented._

```text
Second definition; the one documented.
```

### `build.<locals>.worker`

```python
def worker()
```

_nested function · public · lines 15–17_

_Gap: `build.<locals>.worker` at unconditional (nested function, line 15) is rebound by assignment at line 19 and stays documented above; the rebinding is not reconstructed._

```text
Defined then rebound by a later assignment.
```
