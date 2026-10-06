"""The owned Swift parse must fail visibly and release its worker on timeout."""

from __future__ import annotations

import time
from pathlib import Path
from unittest import mock

from crux.arch.packs import swift


FIXTURE = Path(__file__).parent / "fixtures" / "swiftdecls" / "13-dropped-declaration.swift"


def test_bounded_parse_matches_the_direct_parser_on_a_real_fixture():
    raw = FIXTURE.read_bytes()
    expected = swift._parse_file("fixture.swift", raw)
    worker = swift._BoundedParser()
    try:
        observed = worker.call("file", ("fixture.swift", raw), timeout=2)
    finally:
        worker.close()
    assert observed == expected


def test_quiet_swift_worker_times_out_with_unobserved_residual_and_recovers():
    raw = b"struct Safe {}\n"
    worker = swift._BoundedParser()
    try:
        with mock.patch.object(swift, "_parse_file", side_effect=lambda *_: time.sleep(10)):
            started = time.monotonic()
            facts = worker.call("file", ("Safe.swift", raw), timeout=0.1)
            elapsed = time.monotonic() - started
        assert elapsed < 2
        assert facts.residuals == [("parse-error", None,
                                    "location none; Swift parse unobserved after 0.1s owned deadline; effect step parse failed")]
        content = "\n".join(["## Residuals", swift._residual_line(
            "parse-error", "Safe.swift", None, facts.residuals[0][2])])
        verdict = swift.concern_decode_verdict("data-model", content, {"Safe.swift": "sha256"})
        assert verdict is not None and verdict.reason.value == "parse_failed"
        restored = worker.call("file", ("Safe.swift", raw), timeout=2)
        assert not restored.residuals
        assert restored.types
    finally:
        worker.close()
