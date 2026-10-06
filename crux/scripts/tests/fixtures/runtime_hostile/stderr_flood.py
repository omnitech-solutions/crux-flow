"""Import-time stderr flood that exceeds the OS pipe buffer BEFORE the capture is
written.

If the parent gives the child a stderr PIPE and drains the capture descriptor
first, a child that writes more than the ~64 KiB pipe buffer to stderr blocks on
the full pipe before it can write its capture. The capture descriptor then never
reaches EOF, the parent drains to its wall-clock deadline, kills the group, and
discards a capture the app would have produced. A non-blocking stderr sink (a
disk-backed file) breaks that deadlock, so the capture succeeds.
"""

import sys

from flask import Flask

sys.stderr.write("X" * (256 * 1024))  # 256 KiB — well past a 64 KiB pipe buffer
sys.stderr.flush()

app = Flask(__name__)


@app.route("/flooded")
def flooded():
    return "ok"
