"""Import-time subprocess spawn. The child audithook denies ``subprocess.*`` and
``os.system``; import propagates the denial and yields no-capture."""

import subprocess

subprocess.Popen(["/bin/echo", "should-never-run"])

app = None
