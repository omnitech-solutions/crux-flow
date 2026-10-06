"""Import-time network access. The child audithook denies ``socket.*`` and, on
Linux, the network namespace has no route — either way this yields no-capture.
This module does NOT catch the denial, so import propagates and no capture is
written."""

import socket

# Tripped by the audithook (event "socket.__new__" / "socket.connect") on both
# platforms; also unroutable inside the Linux netns.
_s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
_s.settimeout(2)
_s.connect(("93.184.216.34", 80))  # example.com — must never succeed

app = None
