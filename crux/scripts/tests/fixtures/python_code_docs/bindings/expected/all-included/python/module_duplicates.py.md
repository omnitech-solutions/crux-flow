# module\_duplicates.py

```text
Module-scope duplicate bindings across every branch context.
```

## `_MODE`

```python
_MODE = 1
```

_attribute · private · line 4_

_Undocumented._

## `choice`

```python
def choice()
```

_function · public · lines 9–10_

_Gap: `choice` at if rebinds `function` (line 6); `function` (line 9) is documented._

```text
Chosen otherwise.
```

## `parser`

```python
def parser()
```

_function · public · lines 17–18_

_Gap: `parser` at try rebinds `function` (line 14); `function` (line 17) is documented._

```text
Fallback definition.
```

## `loader`

```python
def loader()
```

_function · public · lines 25–26_

_Gap: `loader` at unconditional rebinds `function` (line 21); `function` (line 25) is documented._

```text
Second definition.
```

## `_FLAG`

```python
_FLAG = 1
```

_attribute · private · line 29_

_Undocumented._

## `route`

```python
def route()
```

_function · public · lines 34–35_

_Gap: `route` at if rebinds `function` (line 31); `function` (line 34) is documented._

```text
Route via elif.
```

## `_KEY`

```python
_KEY = 1
```

_attribute · private · line 38_

_Undocumented._

## `handler`

```python
def handler()
```

_function · public · lines 44–45_

_Gap: `handler` at match rebinds `function` (line 41); `function` (line 44) is documented._

```text
Handler for case 2.
```
