"""Hostile fixture targets for the ADR-0075 confinement NEGATIVE suite.

Every module here performs a forbidden action in its IMPORT-TIME code — the only
code the harness runs. These are plain-Python (no framework import) so the
confinement suite runs on every platform regardless of FastAPI/Flask/Django
availability. They are executed ONLY by the harness under full confinement,
never imported by the test process directly.
"""

# The env var the child shim uses to hand the capture write-descriptor number to
# the target's interpreter. A hostile target can read it and forge the capture;
# the parent's strict schema + caps + pre-parse drain bound that to no-capture.
CAPTURE_FD_ENV = "CRUX_ARCH_CAPTURE_FD"
