# h4\_pep723\_block.py

```text
Module carrying a PEP 723 block whose TOML comment hazards a backtick run and an HTML-comment-close sequence.
```

## Script metadata

`````toml
requires-python = ">=3.13"
dependencies = []
# a hostile TOML comment: ```` and --> render inert once fenced
`````

## `marker`

```python
def marker()
```

_function · public · lines 10–12_

```text
Return a constant value.
```
