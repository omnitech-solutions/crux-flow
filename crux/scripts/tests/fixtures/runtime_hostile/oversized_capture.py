"""Import-time oversized emission on the capture channel. The target streams
more than the parent's 4 MiB pre-parse byte cap. The parent closes the channel,
kills the child process group, and yields no-capture — no memory blowup, no
hang."""

import os
import sys

# Discover the inherited capture descriptor from the child's own argv
# (`--capture-fd <n>`) rather than a handed-in env var: the strict env allowlist
# does not carry CRUX_ARCH_CAPTURE_FD, but the FD number rides on the child's
# command line and the descriptor is inherited regardless — so a real target
# finds it this way. This keeps the pre-parse byte-cap control exercised with
# nil capability delta from the removed env var.
_fd = None
_argv = sys.argv
for _i, _a in enumerate(_argv):
    if _a == "--capture-fd" and _i + 1 < len(_argv):
        try:
            _fd = int(_argv[_i + 1])
        except ValueError:
            _fd = None
        break

if _fd is not None:
    chunk = b"A" * (1024 * 1024)  # 1 MiB
    try:
        for _ in range(8):  # 8 MiB total, over the 4 MiB cap
            os.write(_fd, chunk)
    except OSError:
        pass  # parent may close the pipe mid-stream; that is the intended path

app = None
