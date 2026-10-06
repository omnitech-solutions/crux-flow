"""Import-time spin that IGNORES SIGTERM, forcing the harness `_kill_group`
SIGKILL-escalation branch.

At import the fixture installs `SIG_IGN` for SIGTERM, then spins forever, so the
module import never returns and the capture drain times out. When the parent then
runs its kill sequence, `os.killpg(SIGTERM)` is discarded by `SIG_IGN`; the 5 s
grace expires; only `os.killpg(SIGKILL)` reaps the child. This exercises the
grace-expiry -> SIGKILL path and proves the parent never hangs on a child that
refuses SIGTERM.
"""

import signal
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)

while True:
    time.sleep(0.1)

app = None  # unreachable — the import never completes
