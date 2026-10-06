# module\_rebindings.py

```text
Module-scope rebindings of def statements by non-def operations.
```

## `config`

```python
config = 'static-config-value'
```

_attribute · public · line 8_

_Gap: `config` at unconditional (function, line 4) is rebound by assignment at line 8 and is not reconstructed; the rebinding is documented above._

_Undocumented._

## `helper`

```python
import helper as helper
```

_alias · public · line 15_

_Gap: `helper` at unconditional (function, line 11) is rebound by import at line 15 and is not reconstructed; the rebinding is documented above._

_Undocumented._

- Gap: `unused` at unconditional (function, line 18) is rebound by import at line 22, which renders no member (not exported); the original function is not reconstructed.

## `counter`

```python
def counter()
```

_function · public · lines 25–26_

_Gap: `counter` at unconditional (function, line 25) is rebound by for-loop target at line 29 and stays documented above; the rebinding is not reconstructed._

```text
Shadowed by a for-loop target.
```
