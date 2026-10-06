# h5\_line\_break.py

```text
Docstring is plain; the line break lives in an __all__ entry and a default value.
```

## Exports

`safe_name`, `line\nbreak`

- Gap: `line\nbreak` (line 3) is exported but does not resolve inside the selection.

## `safe_name`

```python
def safe_name(value: str = 'line\nbreak') -> str
```

_function · public · lines 6–8_

```text
Default value carries a literal line break, rendered through ast.unparse's own escaping.
```
