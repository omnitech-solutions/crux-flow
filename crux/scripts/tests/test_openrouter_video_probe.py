"""Tests for openrouter_video_probe.py's typed-status invariant (S-C).

`_attempt` routes every non-200 status through `raise_for_gateway_status`,
which types EVERY non-2xx; a return without a raise therefore means the
invariant broke. The bare `raise` that used to stand there surfaced as
`RuntimeError: No active exception to re-raise` — the least legible spelling
of "the typing contract failed". It is now an AssertionError naming the
invariant and the status.

The probe is deliberately NOT named `test_*` (it spends money and needs the
network), so this companion test module imports it and drives `_attempt` with
the gateway seam monkeypatched — no network, no key. Follows
test_council_ingest.py's import-guard pattern: the probe imports the LLM
router stack, so this module skips when those deps are unavailable.
"""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS = REPO_ROOT / "crux" / "scripts"

try:
    sys.path.insert(0, str(SCRIPTS))
    sys.path.insert(0, str(SCRIPTS / "tests"))
    import openrouter_video_probe as probe
    from crux.core.llm_caller import ModelConfig

    HAVE_PROBE = True
except Exception:  # router deps unavailable
    HAVE_PROBE = False


class _FakeResponse:
    """A non-2xx response the (monkeypatched) typing function ignores."""

    status_code = 500

    def raise_for_status(self):
        return None

    def json(self):
        return {}


class _FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, headers=None, json=None):
        return _FakeResponse()


def _cfg() -> "ModelConfig":
    return ModelConfig(
        api_string="test/model",
        display_name="test",
        context_window=1000,
        max_output_tokens=100,
        base_url="https://example.invalid",
        auth_header="Authorization",
        auth_prefix="Bearer ",
    )


@unittest.skipUnless(HAVE_PROBE, "router deps unavailable — run under uv")
class TypedStatusInvariantTests(unittest.TestCase):
    """If `raise_for_gateway_status` ever returns without typing a non-2xx,
    `_attempt` must fail loudly with the invariant and the status named."""

    def test_untyped_status_raises_assertion_naming_the_invariant(self):
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test-dummy"}), \
             mock.patch.object(probe, "raise_for_gateway_status", lambda response: None), \
             mock.patch.object(probe, "httpx") as fake_httpx:
            fake_httpx.Client = _FakeClient
            with self.assertRaises(AssertionError) as ctx:
                probe._attempt(_cfg(), {"type": "text", "text": "x"})
        msg = str(ctx.exception)
        self.assertIn("invariant violated", msg)
        self.assertIn("500", msg)

    def test_broken_typing_contract_is_exit_2_not_a_traceback(self):
        """A broken typing contract is an environment fault: the exit contract
        reserves 1 for the video-unsupported verdict, so an AssertionError out
        of the probe must surface as exit 2 with the invariant on stderr —
        never as an exit-1 traceback."""
        with mock.patch.dict("os.environ", {"CRUX_ALLOW_LIVE": "1",
                                            "OPENROUTER_API_KEY": "sk-or-test-dummy"}), \
             mock.patch.object(probe, "probe",
                               side_effect=AssertionError(
                                   "invariant violated: raise_for_gateway_status "
                                   "returned without typing HTTP 500")):
            err_b = io.StringIO()
            with contextlib.redirect_stderr(err_b):
                rc = probe.main()
        self.assertEqual(rc, 2)
        self.assertIn("invariant violated", err_b.getvalue())
        self.assertNotIn("Traceback", err_b.getvalue())


if __name__ == "__main__":
    unittest.main()
