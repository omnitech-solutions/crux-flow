# members.py

```text
Synthetic member shapes.
```

## `LIMIT`

```python
LIMIT: int = 10
```

_attribute · public · line 13_

```text
Attribute docstring for LIMIT.
```

## `RATIO`

```python
RATIO = 0.5
```

_attribute · public · line 17_

_Undocumented._

## `square`

```python
square = lambda x: x * x
```

_attribute · public · line 19_

_Undocumented._

## `Base`

```python
class Base(ABC)
```

_class · public · lines 22–53_

```text
Abstract base.
```

### `Base.run`

```python
@abstractmethod
def run(self) -> None
```

_method · public · lines 25–27_

```text
Run it.
```

### `Base.make`

```python
@classmethod
def make(cls) -> Base
```

_class method · public · lines 29–32_

```text
Build one.
```

### `Base.version`

```python
@staticmethod
def version() -> str
```

_static method · public · lines 34–36_

_Undocumented._

### `Base.expensive`

```python
@functools.cached_property
def expensive(self) -> int
```

_property · public · lines 38–41_

```text
Computed once.
```

### `Base.name`

```python
@property
def name(self) -> str
@name.setter
def name(self, value: str) -> None
```

_property · public · lines 43–50_

```text
The name.
```

### `Base.Inner`

```python
class Inner
```

_class · public · lines 52–53_

```text
A class nested in a class.
```

## `factory`

```python
def factory()
```

_function · public · lines 56–65_

```text
Return a locally defined class.
```

### `factory.<locals>.Local`

```python
class Local
```

_nested class · public · lines 59–63_

```text
A class nested in a function.
```

#### `factory.<locals>.Local.method`

```python
def method(self)
```

_method · public · lines 62–63_

```text
A method of a nested class.
```

## `hazard`

```python
def hazard()
```

_function · public · lines 68–76_

````text
Docstring with Markdown hazards.

# Not a heading

```
fence inside a docstring
```
````
