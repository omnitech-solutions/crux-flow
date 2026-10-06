# Disposable delivery exercise

This fixture supports an opt-in, measured delivery comparison. Copy it into
one clean disposable Git repository per task and mode. Never run an agent in
the fork checkout for this exercise. The existing test suite is the baseline;
each task below adds acceptance tests before changing `workboard.py`.

```sh
exercise_dir=$(mktemp -d)
\cp examples/delivery-exercise/{workboard.py,test_workboard.py,board.json} "$exercise_dir/"
cd "$exercise_dir"
git init -q
python3 -m unittest -q
git add workboard.py test_workboard.py board.json
git -c user.name=Exercise -c user.email=exercise@example.invalid commit -qm baseline
```

Run each task from the same three-file baseline, separately in aggressive,
balanced, thorough, and upstream modes where the host supports the original
upstream workflow. Reset by making a new disposable directory; do not reuse a
completed task's tree. Freeze the host version, model assignments, toolchain,
and baseline commit in each run record. Start the timer when the request is
submitted. Record first working acceptance, final acceptance, delegated calls,
repeated checks, review findings, and the final diff. The benchmark is a
chargeable provider exercise and requires explicit usage authorization.

## Tasks

1. **Bug fix:** `completed` must count only the exact status `done`; `done-ish`
   must not count. Preserve the CLI's JSON shape and add a failing regression
   before the fix. Acceptance: the baseline and new tests pass; review checks
   that unknown statuses cannot be counted by prefix.
2. **Bounded enhancement:** add `--include-paused` to count `paused` items in
   `active` for that invocation. Default output remains unchanged. Acceptance:
   library and CLI tests cover both choices; a malformed input still fails.
3. **Cross-boundary change:** add `--config PATH` to load a JSON object with a
   `completed_statuses` list of exact, nonempty strings. The CLI and library
   use the same validated interpretation; without `--config`, behavior stays
   as in the baseline. Acceptance: invalid config fails clearly, a custom
   status works end to end, and the original CLI invocation still works.

Use one independent reviewer per fork mode under its policy and record the
review evidence. Completion requires the task acceptance tests and required
final checks, not a passing baseline test count. Do not interpret delegation
count or configuration size as an elapsed-time improvement.
