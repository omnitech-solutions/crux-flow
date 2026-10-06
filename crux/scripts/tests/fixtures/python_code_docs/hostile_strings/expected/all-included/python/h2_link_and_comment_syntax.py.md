# h2\_link\_and\_comment\_syntax.py

```text
Docstring containing markup-forging text: a link fragment ](target) and an HTML comment <!-- note --> rendered verbatim inside this fence.
```

## Exports

`safe_export`, `haz](ard)-->export`

- Gap: `haz](ard)-->export` (line 5) is exported but does not resolve inside the selection.

## `safe_export`

```python
def safe_export(x: Literal['tag](name)<!--c-->']) -> None
```

_function · public · lines 8–9_

```text
Annotation carries a link fragment and an HTML comment marker.
```

## `with_default`

```python
def with_default(value: str = 'click ](here) <!-- warn -->') -> str
```

_function · public · lines 12–14_

```text
Default value carries the same hazardous sequences.
```
