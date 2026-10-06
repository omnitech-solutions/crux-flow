"""Import-time forgery of the capture channel. The target reads the capture FD
number from the environment and writes non-schema garbage to it. The parent's
strict schema (unknown keys / non-JSON) bounds this to no-capture — the forged
bytes never reach the spine."""

import os

_fd = os.environ.get("CRUX_ARCH_CAPTURE_FD")
if _fd is not None:
    payload = b'{"routes": [{"method": "GET", "path": "/", "name": "x", "handler": "os.system"}], "secret": "leaked"}'
    os.write(int(_fd), payload)

app = None
