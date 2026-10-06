# pkg/\_\_init\_\_.py

```text
Package demonstrating export rules: a static __all__, redundant-alias
imports, a non-exported import, and a wildcard import.
```

## Exports

`greet`, `Widget`, `util_helper`

- Gap: `from .util import *` (line 7) is a wildcard import; its names are not expanded.

## `util_helper`

```python
from .util import util_helper
```

_alias · public · line 4_

_Undocumented._

## `shared`

```python
from .util import shared as shared
```

_alias · public · line 5_

_Undocumented._

## `greet`

```python
def greet(name)
```

_function · public · lines 12–14_

```text
Greet a name.
```

## `Widget`

```python
class Widget
```

_class · public · lines 17–22_

```text
A simple exported class.
```

### `Widget.spin`

```python
def spin(self)
```

_method · public · lines 20–22_

```text
Spin the widget.
```
