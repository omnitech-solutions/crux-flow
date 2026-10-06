"""Import-time block — models a module-level ``app.run()`` / ``uvicorn.run()``
with no ``__main__`` guard. The harness cannot prevent the block; its wall-clock
timeout + process-group kill turn it into no-capture, never a hang."""

import time

# Blocks until the harness delivers SIGTERM/SIGKILL to the process group.
time.sleep(24 * 3600)

app = None
