# crux/scripts/base\_commit\_pin.py

```text
base_commit_pin.py — one implementation of the pin on a run snapshot's `base_commit`.

`base_commit` is the single value the `patch` tier's containment check reads out of a
run snapshot (docs/AGENTS.md §11.C). Every other input to that check comes from the
repository's own change record, so this one value is the only place a run can speak
about itself — and moving it forward shrinks the diff the check proves.

TWO CONSUMERS, ONE IMPLEMENTATION. The pin has to hold at both ends of the run:

  * `advance-run.py` refuses to WRITE a snapshot whose `base_commit` diverges from its
    committed record, on both the advance and the abandon path.
  * `check-blast-radius.py` refuses to PASS a snapshot whose `base_commit` diverges,
    because a run can commit a hand-edited value without going through `advance-run.py`
    at all. A writer-side guard alone left the reader trusting the field.

Both call `committed_base_commit` here. A second copy would let the two ends disagree
about what the committed record is, and the gate is only worth what both ends enforce.

THE NO-CLAIM LANE IS DELIBERATE. `NO_RECORD` means "this snapshot has no committed
version to compare against", and it is never a verdict. It is returned when git is
absent, when the snapshot lies outside a work tree, when the snapshot is untracked or
has no committed version yet, and when the committed blob does not parse. Absence of
evidence is not evidence: a run whose snapshot has not been committed yet has nothing
holding its `base_commit`, and reporting that as tampering would refuse every run
during its first prompt.

`NO_RECORD` is also held apart from a committed `base_commit: null`. Conflating them
would let a run that started outside a git work tree acquire a commit boundary after
the fact, which is the same forward move by another route.

HONEST LIMIT. The pin binds from the snapshot's first commit onward. In the window
between run start and that commit there is no committed record, so nothing holds the
value at either end.
```

_Private declarations not rendered: 1 (include_private is false)._

## `NoRecord`

```python
class NoRecord
```

_class · public · lines 46–50_

```text
Sentinel type: this snapshot has no committed version to compare against.
```

### `NoRecord.__repr__`

```python
def __repr__(self) -> str
```

_method · public · lines 49–50_

_Undocumented._

## `NO_RECORD`

```python
NO_RECORD = NoRecord()
```

_attribute · public · line 53_

_Undocumented._

## `committed_base_commit`

```python
def committed_base_commit(run_path: Path) -> Any
```

_function · public · lines 56–92_

```text
The ``base_commit`` recorded in the last committed version of this snapshot,
or ``NO_RECORD`` when there is none to read.

Reads `HEAD:<path>` through `git -C <the snapshot's own directory>`, so the answer
comes from the repository the snapshot lives in rather than from a caller-supplied
root. Returns ``NO_RECORD`` — never a verdict — for every case listed in the module
docstring's no-claim lane.
```

## `divergence`

```python
def divergence(run: dict, run_path: Path) -> tuple[Any, Any] | None
```

_function · public · lines 95–108_

```text
``(committed, live)`` when the snapshot's ``base_commit`` diverges from its
committed record, else ``None``.

``None`` covers both clean cases and reports them the same way, because a caller
must act on neither: the values agree, or there is no committed record to compare
against and this function makes no claim.
```
