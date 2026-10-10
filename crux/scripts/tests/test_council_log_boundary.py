"""No seat-derived text reaches a log record or stderr on the council path.

The council's log lines are written before run-council's secret scan, and run-council installs no
handler, so Python's last-resort handler prints WARNING and above to stderr unscanned. These tests
drive `AsyncCouncil.deliberate` through an `httpx.MockTransport` whose every reply or fault carries
a key-shaped string, and read two captures: a ROOT handler at DEBUG, which sees every logger at
every level, and the stderr of a child that configures no logging, as run-council runs.

The key shape is assembled at run time; it is not a credential, and no assertion prints it.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

try:
    import httpx
    import crux.council.async_council  # noqa: F401
    HAVE_COUNCIL = True
except Exception:  # router deps unavailable: run under uv
    HAVE_COUNCIL = False

import secret_scan as ss  # noqa: E402

FAKE = "sk-or-v1-" + "cd" * 32
DUMMY = "zz-gateway-dummy-key-0f2e"

# The gateway model, kept as source so the child below runs the same handler.
GATEWAY = r'''
import json, httpx
DISPLAY = {"openai": "OpenAI", "anthropic": "Anthropic", "google": "Google AI Studio"}

def _ok(model, content):
    ns = model.split("/")[0]
    return httpx.Response(200, json={"id": "gen-" + ns, "model": model, "provider": DISPLAY.get(ns, ns),
                                     "choices": [{"message": {"content": content}, "finish_reason": "stop"}]})

def handler(name, fake):
    def handle(request):
        model = json.loads(request.content)["model"]
        vote = {"confidence": 0.9, "reasoning": fake, "dissents": [{"point": fake}],
                "findings": [{"id": "F1", "text": fake}]}
        if name == "key-in-decision":
            return _ok(model, json.dumps(dict(vote, decision=fake)))
        if name == "key-in-reasoning":
            return _ok(model, json.dumps(dict(vote, decision="APPROVE")))
        if name == "key-in-unparseable-content":
            return _ok(model, "no json here " + fake)
        if name == "key-in-error-body":
            return httpx.Response(500, text="upstream said " + fake, headers={"x-echo": fake})
        if name == "key-in-undecodable-body":
            return httpx.Response(200, text="<html>" + fake)
        if name == "key-in-transport-exception":
            raise httpx.ConnectError("connect failed for " + fake, request=request)
        if name == "key-in-plain-exception":
            raise RuntimeError("boom " + fake)
        raise AssertionError(name)
    return httpx.MockTransport(handle)
'''
SCENARIOS = ("key-in-decision", "key-in-reasoning", "key-in-unparseable-content", "key-in-error-body",
             "key-in-undecodable-body", "key-in-transport-exception", "key-in-plain-exception")

CHILD = r'''
import asyncio, logging, sys
from unittest import mock
sys.path.insert(0, sys.argv[1])
from crux.council.async_council import AsyncCouncil, AsyncCouncilConfig
ns = {}
exec(sys.argv[2], ns)
fake = "sk-or-v1-" + "cd" * 32
gate = sys.argv[4] == "1"
cfg = AsyncCouncilConfig(transport=ns["handler"](sys.argv[3], fake), timeout_seconds=5.0,
                         gate=gate, council_kind="adr" if gate else None)
with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": sys.argv[6]}):
    asyncio.run(AsyncCouncil(cfg).deliberate("Is this sound?", "system"))
if sys.argv[5] == "1":
    logging.getLogger("seed").warning("seeded %s", fake)
'''


def deliberate(name: str, gate: bool):
    from crux.council.async_council import AsyncCouncil, AsyncCouncilConfig
    ns: dict = {}
    exec(GATEWAY, ns)
    cfg = AsyncCouncilConfig(transport=ns["handler"](name, FAKE), timeout_seconds=5.0,
                             gate=gate, council_kind="adr" if gate else None)
    with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": DUMMY}):
        return asyncio.run(AsyncCouncil(cfg).deliberate("Is this sound?", "system"))


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records: list[tuple[str, str]] = []

    def emit(self, record):
        text = record.getMessage()
        if record.exc_info:
            text += "\n" + logging.Formatter().formatException(record.exc_info)
        self.records.append((record.name, text))


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable - run under uv")
class EveryLoggerAtEveryLevelTests(unittest.TestCase):
    def setUp(self):
        self.cap = _Capture()
        root = logging.getLogger()
        self.addCleanup(root.setLevel, root.level)
        self.addCleanup(root.removeHandler, self.cap)
        root.addHandler(self.cap)
        root.setLevel(logging.DEBUG)

    def leaks(self) -> int:
        return sum(1 for _, text in self.cap.records if FAKE in text or ss.scan_text(text))

    def test_the_capture_is_live(self):
        """Positive control: the fixture scans as a key, the handler hears httpx and the council,
        and a seeded record counts as a leak."""
        self.assertTrue(ss.scan_text(FAKE))
        deliberate("key-in-reasoning", gate=False)
        names = {name for name, _ in self.cap.records}
        self.assertIn("httpx", names)
        self.assertIn("crux.council.async_council", names)
        logging.getLogger("asyncio").error("seeded %s", FAKE)
        self.assertEqual(1, self.leaks())

    def test_no_record_carries_seat_text(self):
        for name in SCENARIOS:
            for gate in (False, True):
                with self.subTest(scenario=name, gate=gate):
                    self.cap.records.clear()
                    deliberate(name, gate)
                    self.assertEqual(0, self.leaks(), f"{name} gate={gate}: a record carried the key shape")


@unittest.skipUnless(HAVE_COUNCIL, "council/router deps unavailable - run under uv")
class LastResortStderrTests(unittest.TestCase):
    def child(self, name: str, gate: bool, seed: bool = False) -> str:
        result = subprocess.run(
            [sys.executable, "-c", CHILD, str(SCRIPTS), GATEWAY, name, "1" if gate else "0",
             "1" if seed else "0", DUMMY], capture_output=True, text=True, timeout=120)
        self.assertEqual(0, result.returncode, f"child exited {result.returncode}")
        return result.stderr

    def test_the_last_resort_handler_is_live(self):
        """Positive control: a seeded WARNING and the council's own ERROR line reach stderr."""
        self.assertTrue(ss.scan_text(self.child("key-in-reasoning", False, seed=True)))
        self.assertIn("async error:", self.child("key-in-plain-exception", False))

    def test_stderr_carries_no_seat_text(self):
        for name in SCENARIOS:
            for gate in (False, True):
                with self.subTest(scenario=name, gate=gate):
                    self.assertFalse(ss.scan_text(self.child(name, gate)),
                                     f"{name} gate={gate}: stderr carried a key shape")


if __name__ == "__main__":
    unittest.main()
