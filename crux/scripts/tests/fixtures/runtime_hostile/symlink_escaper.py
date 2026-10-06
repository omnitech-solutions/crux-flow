"""Import-time write-confinement ESCAPE via a symlink.

The audithook denies a write-mode ``open`` whose target is outside the scratch
tempdir. If that containment check resolves the path with ``os.path.abspath``
(which does NOT follow symlinks) rather than ``os.path.realpath`` (which does), a
target can create a symlink INSIDE scratch that points OUTSIDE it, then
``open`` that in-scratch path for writing: the abspath sits inside scratch and is
permitted, yet the kernel follows the link and the bytes land outside. This is a
pure-Python ``open`` escape, distinct from the stated native/ctypes residual.

The child must deny this (realpath containment), so this yields no-capture and
the outside marker is never created.
"""

import os

# Discover a writable in-scratch location via cwd rather than a handed-in env
# var: the child's cwd is the temp-clone root (`<scratch>/src`), inside scratch.
# A real target has no CRUX_ARCH_SCRATCH — the strict env allowlist does not
# carry it — so cwd is how it would find a writable spot; using it here keeps the
# escape realistic and the realpath-containment control genuinely exercised.
_scratch = os.getcwd()
_link = os.path.join(_scratch, "escape_link")
_OUTSIDE_MARKER = "/tmp/crux-arch-symlink-escape-marker"

os.symlink(_OUTSIDE_MARKER, _link)          # os.symlink is not on the deny-list
with open(_link, "w", encoding="utf-8") as fh:  # in-scratch path; realpath resolves outside
    fh.write("escaped")

app = None
