# scope\_duplicates.py

```text
Class- and function-scope duplicate bindings, plus two non-duplicate chains.
```

## `Router`

```python
class Router
```

_class · public · lines 6–22_

```text
Routes requests.
```

### `Router.handle`

```python
def handle(self)
```

_method · public · lines 12–13_

_Gap: `Router.handle` at unconditional rebinds `method` (line 9); `method` (line 12) is documented._

```text
Second handler.
```

### `Router.path`

```python
@property
def path(self)
@path.setter
def path(self, value)
```

_property · public · lines 15–22_

```text
Current path.
```

## `parse`

```python
@overload
def parse(value: str) -> str
@overload
def parse(value: bytes) -> str
def parse(value)
```

_function · public · lines 25–31_

```text
Decode value to text.
```

## `outer`

```python
def outer()
```

_function · public · lines 34–43_

```text
Has a duplicated nested helper.
```

### `outer.<locals>.inner`

```python
def inner()
```

_nested function · public · lines 40–41_

_Gap: `outer.<locals>.inner` at unconditional rebinds `nested function` (line 37); `nested function` (line 40) is documented._

```text
Second nested definition.
```
