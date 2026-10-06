# tools/jev/answers.py

```text
Validated answer types.

The one structural decision worth stating: **`NoulAnswer` has no `confidence`
attribute at all** — not an `Optional[float]` that is always `None`. The
interface documents no confidence field on a noul answer, and the accepted
decision forbids fabricating one. Modelling the absence as a missing attribute
makes a caller that reaches for it raise `AttributeError` at the call site,
rather than read a plausible `None` (or, worse, a default) and carry on. An
absence you can accidentally paper over is not an absence.

`ChoiceAnswer.confidence` and `ScoreAnswer.confidence` are `float | None`, where
`None` means **unavailable** and never a default. Nothing in this package
supplies a fallback for it, and nothing thresholds on it.
```

## Exports

`NoulAnswer`, `ChoiceAnswer`, `ScoreAnswer`, `Answer`, `Usage`, `DecisionsResult`

## `NoulAnswer`

```python
@dataclass(frozen=True)
class NoulAnswer
```

_class · public · lines 22–42_

```text
A probability in [0, 1] that the statement is true.

The vendor documents near-1 as a strong yes, near-0 a strong no, and near-0.5
as uncertain. An uncertain answer is a VALID answer: it is returned, not
raised, which is what keeps model ambiguity distinct from transport failure.
```

### `NoulAnswer.question_id`

```python
question_id: str
```

_attribute · public · line 31_

_Undocumented._

### `NoulAnswer.noul`

```python
noul: float
```

_attribute · public · line 32_

_Undocumented._

### `NoulAnswer.is_uncertain`

```python
@property
def is_uncertain(self) -> bool
```

_property · public · lines 34–42_

```text
True near the midpoint. A reporting convenience, never a gate.

Deliberately not a threshold anybody may act on: no code in this package
branches on it, and the accepted decision forbids a threshold gating
anything.
```

## `ChoiceAnswer`

```python
@dataclass(frozen=True)
class ChoiceAnswer
```

_class · public · lines 45–52_

```text
The selected option, checked for membership in the declared option set.
```

### `ChoiceAnswer.question_id`

```python
question_id: str
```

_attribute · public · line 49_

_Undocumented._

### `ChoiceAnswer.choice`

```python
choice: str
```

_attribute · public · line 50_

_Undocumented._

### `ChoiceAnswer.confidence`

```python
confidence: float | None = None
```

_attribute · public · line 51_

_Undocumented._

### `ChoiceAnswer.probabilities`

```python
probabilities: Mapping[str, float] | None = None
```

_attribute · public · line 52_

_Undocumented._

## `ScoreAnswer`

```python
@dataclass(frozen=True)
class ScoreAnswer
```

_class · public · lines 55–72_

```text
A position along the ordered rubric levels.

`level_count` is the N the request declared. `observed_convention` records
what the RESPONSE suggests about the index base — `zero-based`, `one-based`,
or `indeterminate` — and is a report, never an assertion: the interface does
not state the base, so this package never normalizes the value and never maps
it to a level name.
```

### `ScoreAnswer.question_id`

```python
question_id: str
```

_attribute · public · line 66_

_Undocumented._

### `ScoreAnswer.score`

```python
score: float
```

_attribute · public · line 67_

_Undocumented._

### `ScoreAnswer.level_count`

```python
level_count: int
```

_attribute · public · line 68_

_Undocumented._

### `ScoreAnswer.observed_convention`

```python
observed_convention: str
```

_attribute · public · line 69_

_Undocumented._

### `ScoreAnswer.confidence`

```python
confidence: float | None = None
```

_attribute · public · line 70_

_Undocumented._

### `ScoreAnswer.probabilities`

```python
probabilities: Mapping[str, float] | None = None
```

_attribute · public · line 71_

_Undocumented._

### `ScoreAnswer.legend`

```python
legend: Sequence[str] | None = None
```

_attribute · public · line 72_

_Undocumented._

## `Answer`

```python
Answer = NoulAnswer | ChoiceAnswer | ScoreAnswer
```

_attribute · public · line 75_

_Undocumented._

## `Usage`

```python
@dataclass(frozen=True)
class Usage
```

_class · public · lines 78–89_

```text
Token counts, and cost when the gateway returns one.

`cost` stays `None` when absent rather than defaulting to 0.0 — a missing
cost and a free call are different facts, and collapsing them would let a
spend report understate silently.
```

### `Usage.input_tokens`

```python
input_tokens: int
```

_attribute · public · line 87_

_Undocumented._

### `Usage.output_tokens`

```python
output_tokens: int
```

_attribute · public · line 88_

_Undocumented._

### `Usage.cost`

```python
cost: float | None = None
```

_attribute · public · line 89_

_Undocumented._

## `DecisionsResult`

```python
@dataclass(frozen=True)
class DecisionsResult
```

_class · public · lines 92–108_

```text
One validated Decisions response.

`notes` carries observations that are RECORDED rather than refused — a
probability distribution that does not sum to 1, or a response `model`
differing from the pinned request. Neither is a documented invariant, so
refusing on them would be inventing a spec; dropping them silently would
hide a real signal. They are surfaced as text for a human to read.
```

### `DecisionsResult.model`

```python
model: str
```

_attribute · public · line 103_

_Undocumented._

### `DecisionsResult.answers`

```python
answers: Mapping[str, Answer]
```

_attribute · public · line 104_

_Undocumented._

### `DecisionsResult.usage`

```python
usage: Usage
```

_attribute · public · line 105_

_Undocumented._

### `DecisionsResult.id`

```python
id: str | None = None
```

_attribute · public · line 106_

_Undocumented._

### `DecisionsResult.provider`

```python
provider: str | None = None
```

_attribute · public · line 107_

_Undocumented._

### `DecisionsResult.notes`

```python
notes: tuple[str, ...] = field(default_factory=tuple)
```

_attribute · public · line 108_

_Undocumented._
