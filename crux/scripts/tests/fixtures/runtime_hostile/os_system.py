"""Import-time `os.system`. The child audithook denies the `os.system` audit
event; import propagates the denial and yields no-capture."""

import os

os.system("echo should-never-run")

app = None
