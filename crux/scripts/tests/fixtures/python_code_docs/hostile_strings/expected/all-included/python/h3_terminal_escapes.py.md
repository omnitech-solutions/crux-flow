# h3\_terminal\_escapes.py

```text
Docstring holding a raw terminal escape sequence: [31mred[0m embedded literally inside this fence.
```

## Exports

`colored_default`, `\x1b[31mred\x1b[0m`

- Gap: `\x1b[31mred\x1b[0m` (line 3) is exported but does not resolve inside the selection.

## `colored_default`

```python
def colored_default(value: str = '\x1b[31mred\x1b[0m') -> str
```

_function · public · lines 6–8_

```text
Default value carries the same escape sequence, rendered through ast.unparse's own escaping.
```
