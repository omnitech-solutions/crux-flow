# class\_in\_def\_in\_class.py

```text
A class nested in a def nested in a class.
```

## `Outer`

```python
class Outer
```

_class · public · lines 4–17_

```text
Outer class.
```

### `Outer.factory`

```python
def factory(self)
```

_method · public · lines 7–17_

```text
Build a helper class scoped to this method.
```

#### `Outer.factory.<locals>.Inner`

```python
class Inner
```

_nested class · public · lines 10–15_

```text
A class defined inside a method.
```

##### `Outer.factory.<locals>.Inner.value`

```python
def value(self)
```

_method · public · lines 13–15_

```text
Return a constant.
```
