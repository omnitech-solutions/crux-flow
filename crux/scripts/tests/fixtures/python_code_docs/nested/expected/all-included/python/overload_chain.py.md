# overload\_chain.py

```text
Nested @overload chain.
```

## `build`

```python
def build()
```

_function · public · lines 6–17_

```text
Build a parser via a nested overloaded helper.
```

### `build.<locals>.parse`

```python
@overload
def parse(value: str) -> str
@overload
def parse(value: bytes) -> str
def parse(value)
```

_nested function · public · lines 9–15_

```text
Decode nested value.
```
