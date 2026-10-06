# crux/scripts/tests/test\_council\_refusal.py

```text
A model safety refusal is reported as a refusal, not a JSON parse failure.

A model can decline a request: the gateway returns HTTP 200 with
``finish_reason: "content_filter"`` (or a passed-through
``native_finish_reason: "refusal"``) and empty content, rather than raising.
Before this guard a refusal produced an empty body, fell through to the JSON
parse, and surfaced as ``ValueError("Could not parse JSON response from …")`` —
an environment-looking error for what is actually a policy decline.

Both paths degrade the seat to an error vote excluded from aggregation
(ADR-0054), so this is a DIAGNOSTIC fix, not a correctness one: the assertions
below pin (a) that a refusal is named as a refusal, and (b) that the ADR-0054
degradation is preserved either way.

The transport moved to the one OpenRouter gateway (ADR-0087), so the fake is now
an ``httpx.AsyncClient`` rather than a provider SDK — the requirement is
unchanged, only the wire field the decline arrives in.

No network: ``httpx.AsyncClient`` is monkeypatched with a fake.
```

_Private declarations not rendered: 4 (include_private is false)._

## `REPO_ROOT`

```python
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
```

_attribute · public · line 29_

_Undocumented._

## `SCRIPTS`

```python
SCRIPTS = REPO_ROOT / 'crux' / 'scripts'
```

_attribute · public · line 30_

_Undocumented._

## `HAVE_COUNCIL`

```python
HAVE_COUNCIL = True
```

_attribute · public · line 35_

_Undocumented._

## `CouncilRefusalDiagnosticTests`

```python
@unittest.skipUnless(HAVE_COUNCIL, 'council/router deps unavailable — run under uv')
class CouncilRefusalDiagnosticTests(unittest.TestCase)
```

_class · public · lines 90–124_

_Undocumented._

### `CouncilRefusalDiagnosticTests.test_refusal_is_named_a_refusal_not_a_parse_failure`

```python
def test_refusal_is_named_a_refusal_not_a_parse_failure(self)
```

_method · public · lines 92–102_

```text
The whole point: the reason must say 'refusal', never 'parse'.
```

### `CouncilRefusalDiagnosticTests.test_native_refusal_signal_is_also_named_a_refusal`

```python
def test_native_refusal_signal_is_also_named_a_refusal(self)
```

_method · public · lines 104–108_

_Undocumented._

### `CouncilRefusalDiagnosticTests.test_refusal_still_degrades_to_an_excluded_error_seat`

```python
def test_refusal_still_degrades_to_an_excluded_error_seat(self)
```

_method · public · lines 110–116_

```text
ADR-0054 behaviour is preserved — this fix is diagnostic only.
```

### `CouncilRefusalDiagnosticTests.test_normal_completion_is_unaffected`

```python
def test_normal_completion_is_unaffected(self)
```

_method · public · lines 118–124_

```text
A non-refusal response still parses into a real vote.
```
