"""End-to-end fresh-env probe (ADR-0075 decision 2, threat class S2).

At import (in the child), read whether a secret planted in the PARENT's
environment reached the child. The strict env allowlist should have dropped it,
so the child sees ``ABSENT``. The factory encodes the observed value into a
route path, which the harness captures and the test inspects — a planted parent
secret must never appear in the capture.

Flask-dependent (skipUnless flask in the test)."""

import os

from flask import Flask

_secret = os.environ.get("MY_PLANTED_SECRET", "ABSENT")


def create_app() -> Flask:
    app = Flask("runtime_env_probe")

    @app.route(f"/secret-was-{_secret}")
    def probe():
        return "x"

    return app
