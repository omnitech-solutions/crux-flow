"""Import-time write OUTSIDE the scratch tempdir. The child audithook denies a
write-mode ``open`` whose target is outside scratch, so this raises and yields
no-capture. (A native/ctypes write is a stated same-UID residual the audithook
cannot catch; a Python ``open`` it can.)

The target path is taken from ``CRUX_ARCH_HOSTILE_TARGET`` so the test can point
it at a path it controls and then assert the file was NOT created.
"""

import os

_target = os.environ.get("CRUX_ARCH_HOSTILE_TARGET", "/tmp/crux-arch-pwned")
with open(_target, "w", encoding="utf-8") as fh:  # denied by the audithook
    fh.write("pwned")

app = None
