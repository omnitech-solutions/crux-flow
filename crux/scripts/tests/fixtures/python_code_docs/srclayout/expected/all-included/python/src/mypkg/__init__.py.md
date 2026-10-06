# src/mypkg/\_\_init\_\_.py

```text
Package demonstrating export resolution for a package living under src/.
```

## Exports

`f`, `Thing`, `greet`

## `f`

```python
from ._impl import f as f
```

_alias · public · line 3_

_Undocumented._

## `Thing`

```python
from ._impl import Thing
```

_alias · public · line 4_

_Undocumented._

## `helper`

```python
from .sub import helper as helper
```

_alias · public · line 6_

_Undocumented._

## `greet`

```python
def greet(name)
```

_function · public · lines 11–13_

```text
Greet a name using the subpackage helper.
```

## `Greeter`

```python
class Greeter
```

_class · public · lines 16–21_

```text
Wraps greet as a callable object.
```

### `Greeter.say`

```python
def say(self, name)
```

_method · public · lines 19–21_

```text
Say hello to name.
```
