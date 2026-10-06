# h1\_backtick\_runs.py

`````text
Docstring with a backtick run: ````four```` embedded.
`````

## Exports

`safe_name`, `````haz````ard-run`````

- Gap: `````haz````ard-run````` (line 5) is exported but does not resolve inside the selection.

## `safe_name`

`````python
def safe_name(x: Literal['ha````cky']) -> None
`````

_function · public · lines 8–9_

```text
Annotation carries a backtick run inside a Literal subscript.
```

## `with_default`

`````python
def with_default(value: str = 'def````ault') -> str
`````

_function · public · lines 12–14_

```text
Default value carries a backtick run.
```
