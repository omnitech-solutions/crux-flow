# src/acme/\_\_init\_\_.py

```text
Package demonstrating absolute-import re-export resolution.
```

## Exports

`Widget`, `greet`

- Gap: `nope` (line 7) is exported but does not resolve inside the selection.
- Gap: `json` (line 8) is exported but does not resolve inside the selection.

## `value`

```python
from acme.core.values import value as value
```

_alias · public · line 3_

_Undocumented._

## `Widget`

```python
from acme.core import Widget
```

_alias · public · line 4_

_Undocumented._

## `shared_value`

```python
from acme.relay import shared_value as shared_value
```

_alias · public · line 5_

_Undocumented._

## `merged`

```python
from acme.subpkg import merged as merged
```

_alias · public · line 6_

_Undocumented._

## `greet`

```python
def greet(name)
```

_function · public · lines 13–15_

```text
Greet a name.
```
