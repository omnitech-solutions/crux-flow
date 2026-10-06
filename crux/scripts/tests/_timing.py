"""A relative bound that tells linear work from quadratic work.

`clock` is the one clock a linear-growth test measures with, and
`assert_grows_linearly` is the one assertion it makes. Both used to be written
out twice, in `test_swift_xcode.py` and `test_swift_xcode_products.py`, and
both copies measured wall time with `time.monotonic`.

Wall time fails under oversubscription. A small run of 2-4 ms often finishes
inside one scheduler slice, while a large run of 20-40 ms is preempted and
waits for a core. Taking the fastest of several runs does not help when the
large run is preempted every time, so on a host running more runnable
threads than it has cores the ratio passed the quadratic bound for linear
work. `time.thread_time` counts only the CPU time of the calling thread, so
time the thread spends waiting for a core is not charged to the work.

Stdlib only.
"""

from __future__ import annotations

import time

#: The clock a linear-growth measurement reads: CPU time of the calling
#: thread, which excludes the time the thread waits while descheduled.
clock = time.thread_time


def assert_grows_linearly(test, seconds, n, what, factor=8, attempts=3, floor=0.001):
    """Assert that work grows linearly, not quadratically, in its input.

    `seconds(size)` runs the work once over an input of `size` and returns
    the `clock` seconds it took, setup excluded. From `n // factor` to `n`,
    linear work grows about `factor` times and quadratic work about
    `factor ** 2` times. The bound is their geometric mean, `factor ** 1.5`,
    which is about 23 for 8.

    The bound is relative, so a slow host passes: an absolute 2.0 s bound
    failed at 2.5 s under a loaded gate. Thread CPU time keeps preemption
    out of both sizes. Each size keeps its fastest of `attempts` interleaved
    runs, which drops a run slowed by a cache or allocator effect on one
    size only. `floor` keeps a sub-millisecond small run, where timer
    resolution and interpreter noise outweigh the work, from setting the
    bound. It only ever loosens the bound, by at most `factor ** 1.5 * floor`
    seconds: 23 ms by default."""
    small = large = float("inf")
    for _ in range(attempts):
        small = min(small, seconds(n // factor))
        large = min(large, seconds(n))
    test.assertLess(large, factor ** 1.5 * max(small, floor),
                    f"{what}: {n} took {large:.3f} s and {n // factor} took {small:.3f} s "
                    f"of thread CPU time; linear work grows about {factor} times")
