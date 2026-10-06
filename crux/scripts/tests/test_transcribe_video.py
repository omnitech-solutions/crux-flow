"""`transcribe-video.py` after its migration onto the one gateway (ADR-0087).

This script was the last caller holding a provider SDK, its own credential, and
its own model literal. All three are gone, and each has an assertion here
because each was a way for the consolidation to be silently incomplete.

The wire-shape test is the load-bearing one. Which content part carries video is
not documented anywhere — it was established by a live probe
(`openrouter_video_probe.py`), and the answer is counter-intuitive: video rides
the ordinary `image_url` part with a `video/*` data URL, not a `video_url` or
`input_video` part. A refactor that "tidied" that into the intuitive shape would
still return HTTP 200, and the model would still answer fluently about a video
it never received. Only an assertion on the built payload catches that.

Those assertions run against `build_request`, which sends nothing, so they
describe the real request only for as long as `build_request` and `transcribe`
build the same thing. They no longer assemble it separately — both go through
`_prepared` — and `RequestParityTests` pins that, because a second assembly is
how every payload assertion here stays green while the wire changes.

No network: every test stops at `build_gateway_request`, and the key is mocked.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

SCRIPT_PATH = SCRIPTS_DIR / "transcribe-video.py"
FIXTURE = TESTS_DIR / "fixtures" / "media" / "probe-4s.mp4"

try:
    import httpx  # noqa: F401
    HAVE_HTTPX = True
except ImportError:
    HAVE_HTTPX = False


def _load_script():
    """Import the module by path — its filename carries a hyphen."""
    spec = importlib.util.spec_from_file_location("transcribe_video", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["transcribe_video"] = mod
    spec.loader.exec_module(mod)
    return mod


class SourceTextTests(unittest.TestCase):
    """Properties asserted against the source text.

    Read as text rather than through the import so the absence of a symbol is
    checked directly: an import-based check passes just as happily when a name
    is present but unreached.
    """

    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT_PATH.read_text(encoding="utf-8")

    def test_no_provider_sdk_import(self):
        for banned in ("from google import genai", "google.genai", "google-genai"):
            self.assertNotIn(banned, self.text, f"{banned!r} survives the migration")

    def test_no_provider_key_is_read(self):
        for banned in ("GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            self.assertNotIn(banned, self.text)

    def test_the_credential_is_the_gateway_key(self):
        self.assertIn("OPENROUTER_API_KEY", self.text)

    def test_no_dotenv_loading(self):
        """`.env` loading was a second credential source outside crux_env."""
        for banned in ("load_dotenv", "dotenv", "python-dotenv"):
            self.assertNotIn(banned, self.text)

    def test_no_model_literal(self):
        """The model comes from the registry role, never from a literal here."""
        for banned in ("gemini-3.5-flash", "gemini-3", "claude-", "gpt-"):
            self.assertNotIn(banned, self.text, f"model literal {banned!r} is hardcoded")
        self.assertIn('MODEL_ROLE = "google_fast"', self.text)

    def test_pep723_declares_only_httpx(self):
        self.assertIn('# dependencies = ["httpx>=0.27"]', self.text)

    def test_the_retired_two_gigabyte_ceiling_is_gone(self):
        """The old ceiling described a file service this script no longer uses.

        Carrying the number forward would have been a comforting lie: nothing
        inlined in an HTTP body can be 2 GB.
        """
        self.assertNotIn("2 * 1024 * 1024 * 1024", self.text)
        self.assertNotIn("MAX_FILE_SIZE", self.text)

    def test_the_resume_cache_is_gone(self):
        for banned in ("_hash_file", "_save_cache", "_load_cache",
                       "cache_dir", "hashlib", "sha256"):
            self.assertNotIn(banned, self.text, f"resume-cache remnant {banned!r}")

    def test_the_youtube_path_is_gone(self):
        # Asserted against the CODE, not the prose: the docstring says
        # "No --youtube", so a bare substring search for the word matches the
        # sentence announcing the removal and would fail on a correct file.
        self.assertNotIn('add_argument("--youtube"', self.text)
        self.assertNotIn("file_uri", self.text)
        self.assertNotIn("youtube_url", self.text)

    def test_the_docstring_no_longer_promises_resume_or_a_provider_key(self):
        """The docstring made three claims that the migration falsified.

        Only the PROMISES are banned. The docstring is free to mention a removed
        feature in order to say it is removed — and it should, because a user
        who knows the old flags needs to be told where they went.
        """
        head = self.text[: self.text.index('"""', self.text.index('"""') + 3)]
        self.assertNotIn("Resume capability", head)
        self.assertNotIn("GOOGLE_API_KEY", head)
        self.assertNotIn("python-dotenv", head)
        self.assertIn("OPENROUTER_API_KEY", head)


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class ArgumentSurfaceTests(unittest.TestCase):
    """A removed flag must be REFUSED, never accepted and ignored.

    A silently-ignored `--start 10m` returns a complete, plausible transcript of
    the wrong footage. Argparse rejecting the flag is the honest outcome.

    Each flag is registered and hidden rather than left unknown, so the refusal
    can name why the flag went and what replaces it.
    """

    def setUp(self):
        self.mod = _load_script()
        self.parser = self.mod.build_parser()

    def _refuses(self, argv):
        """Refuse `argv`, returning whatever the parser said on stderr."""
        buf = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, \
                contextlib.redirect_stderr(buf):
            self.parser.parse_args(argv)
        self.assertNotEqual(ctx.exception.code, 0)
        return buf.getvalue()

    #: Each removed flag and a phrase from the reason it must give. A generic
    #: `unrecognized arguments: --start` tells the user the flag is gone and
    #: nothing about what to do instead, which for `--start` is to cut the file
    #: up before transcribing.
    REMOVED_FLAGS = (
        (["--youtube", "https://example.invalid/v"], "no gateway equivalent"),
        (["--cache-dir", "/tmp/x"], "no upload step"),
        (["--force-reupload"], "no upload step"),
        (["--start", "30s"], "segment the file before transcribing"),
        (["--end", "90s"], "segment the file before transcribing"),
        (["--fps", "2"], "segment the file before transcribing"),
        (["--api-key", "secret"], "crux-env set OPENROUTER_API_KEY"),
    )

    def test_removed_flags_are_refused(self):
        for flag, reason in self.REMOVED_FLAGS:
            with self.subTest(flag=flag[0]):
                err = self._refuses(["v.mp4", *flag])
                self.assertIn(flag[0], err)
                self.assertIn(reason, err)

    def test_the_video_argument_is_required(self):
        self._refuses([])

    def test_surviving_flags_parse(self):
        args = self.parser.parse_args(
            ["v.mp4", "--thinking", "low", "--output-dir", "/tmp/out"])
        self.assertEqual(args.video, "v.mp4")
        self.assertEqual(args.thinking, "low")

    def test_thinking_defaults_to_high(self):
        self.assertEqual(self.parser.parse_args(["v.mp4"]).thinking, "high")


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class ModelResolutionTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_script()

    def test_the_model_comes_from_the_registry_role(self):
        with mock.patch.object(self.mod, "get_default_model",
                               return_value="resolved-model") as role:
            t = self.mod.VideoTranscriber()
        role.assert_called_once_with("google_fast")
        self.assertEqual(t.model, "resolved-model")

    def test_an_explicit_model_overrides_the_role(self):
        with mock.patch.object(self.mod, "get_default_model") as role:
            t = self.mod.VideoTranscriber(model="pinned")
        role.assert_not_called()
        self.assertEqual(t.model, "pinned")

    def test_the_role_resolves_against_the_real_registry(self):
        """Guards the role NAME, not the model it currently points at."""
        from crux.core.llm_caller import get_default_model
        self.assertTrue(get_default_model(self.mod.MODEL_ROLE))


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class RequestShapeTests(unittest.TestCase):
    """The built payload: content part, effort pin, and budget."""

    def setUp(self):
        self.mod = _load_script()
        key = mock.patch("crux.core.llm_caller.require", return_value=("k",))
        key.start()
        self.addCleanup(key.stop)

    def _payload(self, thinking="high", path=None):
        t = self.mod.VideoTranscriber()
        _url, _headers, payload = t.build_request(path or FIXTURE, thinking_level=thinking)
        return payload

    def _media_part(self, payload):
        parts = payload["messages"][-1]["content"]
        return next(p for p in parts if p["type"] != "text")

    def test_the_fixture_exists(self):
        self.assertTrue(FIXTURE.is_file(), f"missing probe fixture at {FIXTURE}")

    def test_the_video_rides_an_image_url_data_url(self):
        """The proven shape, pinned verbatim.

        `image_url` is not a typo and not a leftover: it is what the gateway
        accepts for video. The live probe confirmed comprehension of both the
        visual and the audio track through exactly this part.
        """
        part = self._media_part(self._payload())
        self.assertEqual(part["type"], "image_url")
        self.assertIn("image_url", part)
        self.assertTrue(part["image_url"]["url"].startswith("data:video/mp4;base64,"))

    def test_no_provider_sdk_shaped_part_is_emitted(self):
        part = self._media_part(self._payload())
        for wrong in ("video_url", "input_video", "video", "file", "file_data"):
            self.assertNotIn(wrong, part)

    def test_the_prompt_travels_beside_the_video(self):
        parts = self._payload()["messages"][-1]["content"]
        self.assertEqual(parts[0]["type"], "text")
        self.assertEqual(parts[0]["text"], self.mod.VAULT_PROMPT)
        self.assertEqual(len(parts), 2)

    # ── effort mapping ─────────────────────────────────────────────────────
    def test_thinking_low_maps_to_reasoning_effort_low(self):
        self.assertEqual(self._payload("low")["reasoning_effort"], "low")

    def test_thinking_high_maps_to_reasoning_effort_high(self):
        self.assertEqual(self._payload("high")["reasoning_effort"], "high")

    def test_the_effort_pin_is_top_level_not_a_nested_config(self):
        """The provider's nested thinking config has no gateway equivalent."""
        payload = self._payload("low")
        self.assertNotIn("thinking_config", payload)
        self.assertNotIn("thinking_level", payload)

    def test_the_output_budget_is_generous(self):
        """Reasoning tokens come out of the same budget as output.

        A tight ceiling truncates the transcript mid-sentence and the result
        reads as a failed transcription. The probe hit exactly this at 300.
        """
        self.assertGreaterEqual(self._payload()["max_tokens"], 32000)

    def test_data_collection_is_denied_like_every_other_call(self):
        self.assertEqual(self._payload()["provider"]["data_collection"], "deny")


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class RequestParityTests(unittest.TestCase):
    """`transcribe` must send exactly what `build_request` returns.

    Every wire-shape assertion in this file runs against `build_request`, so
    it proves something about the real call only if the real call carries the
    same payload. Comparing the two built payloads is what makes that a proof
    rather than an assumption: `transcribe` hands `call_gateway` a
    `(cfg, content)` pair, and running the recorded pair back through
    `build_gateway_request` — the one place the wire format is decided —
    yields the bytes it would have sent.
    """

    def setUp(self):
        self.mod = _load_script()
        key = mock.patch("crux.core.llm_caller.require", return_value=("k",))
        key.start()
        self.addCleanup(key.stop)

    def test_transcribe_sends_the_payload_build_request_returns(self):
        from crux.core.llm_caller import build_gateway_request

        for thinking in ("low", "high"):
            with self.subTest(thinking=thinking):
                t = self.mod.VideoTranscriber()
                with mock.patch.object(self.mod, "call_gateway",
                                       return_value="word " * 100) as sent, \
                        contextlib.redirect_stderr(io.StringIO()):
                    t.transcribe(video_path=FIXTURE, thinking_level=thinking)

                (cfg, content), kwargs = sent.call_args
                as_sent = build_gateway_request(cfg, content, **kwargs)
                as_built = t.build_request(FIXTURE, thinking_level=thinking)
                self.assertEqual(as_sent, as_built)

    def test_the_two_paths_share_one_assembly(self):
        """The structural half of the claim above.

        Payload equality holds by construction only while both callers use
        `_prepared`; if one grows its own assembly again, equality becomes a
        coincidence that the next edit breaks.
        """
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertEqual(source.count("self._prepared("), 2)
        self.assertEqual(source.count("dataclasses.replace("), 1)
        self.assertEqual(source.count("self.build_media_part(video_path)"), 1)


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class RefusalTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_script()

    def test_an_oversize_file_is_refused_and_the_limit_is_named(self):
        with tempfile.TemporaryDirectory() as td:
            big = Path(td) / "big.mp4"
            big.write_bytes(b"\0" * (self.mod.MAX_INLINE_BYTES + 1))
            with self.assertRaises(ValueError) as ctx:
                self.mod.VideoTranscriber.build_media_part(big)
        message = str(ctx.exception)
        self.assertIn("inline request-body limit", message)
        self.assertIn("MB", message)

    def test_a_file_at_the_ceiling_is_accepted(self):
        """The boundary is inclusive — an off-by-one here refuses valid input."""
        with tempfile.TemporaryDirectory() as td:
            edge = Path(td) / "edge.mp4"
            edge.write_bytes(b"\0" * self.mod.MAX_INLINE_BYTES)
            part = self.mod.VideoTranscriber.build_media_part(edge)
        self.assertEqual(part["type"], "image_url")

    def test_a_missing_file_is_refused(self):
        with self.assertRaises(FileNotFoundError):
            self.mod.VideoTranscriber.build_media_part(Path("/nonexistent/x.mp4"))

    def test_an_undeclarable_media_type_is_refused_not_guessed(self):
        """The data URL's media type tells the gateway how to decode the bytes.

        Guessing produces a request that is accepted and then decoded as the
        wrong thing, or dropped without comment — the silent failure this whole
        migration had to be probed for.
        """
        with tempfile.TemporaryDirectory() as td:
            odd = Path(td) / "clip.unknownext"
            odd.write_bytes(b"\0" * 16)
            with self.assertRaises(ValueError) as ctx:
                self.mod.VideoTranscriber.build_media_part(odd)
        self.assertIn("media type", str(ctx.exception))

    def test_the_ceiling_is_far_below_the_retired_file_service_quota(self):
        self.assertLess(self.mod.MAX_INLINE_BYTES, 2 * 1024 * 1024 * 1024)

    def test_a_fifo_is_refused_before_streaming(self):
        """The non-regular-file guard. A FIFO's getsize is 0, so without the
        `is_file` check the size ceiling would pass and the read below would
        stream unbounded bytes into the request body. Deleting the guard must
        fail this test, not stay green (that's what "coverage" means here)."""
        import os
        if not hasattr(os, "mkfifo"):
            self.skipTest("no mkfifo on this platform")
        with tempfile.TemporaryDirectory() as td:
            fifo = Path(td) / "feed.mp4"
            os.mkfifo(fifo)
            with self.assertRaises(ValueError) as ctx:
                self.mod.VideoTranscriber.build_media_part(fifo)
        self.assertIn("not a regular file", str(ctx.exception))

    def test_a_directory_is_refused(self):
        """A directory survives `path.exists()` and has no bounded size either,
        so it refuses at the same guard as the FIFO (mirroring
        openrouter_video_probe.py's is_file check)."""
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError) as ctx:
                self.mod.VideoTranscriber.build_media_part(Path(td))
        self.assertIn("not a regular file", str(ctx.exception))


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class MainExitContractTests(unittest.TestCase):
    """What `main()` owes a caller when the network fails under it.

    A calling skill reads this script's stderr and parses the final JSON
    block. A transport fault is therefore a normal, reportable outcome, not a
    crash: exit 1 with `extraction_success: false`, the same contract a
    refusal or an oversize file already gets.

    `httpx.ConnectError` and `httpx.ReadTimeout` descend from
    `httpx.HTTPError`, not from `OSError` and not from `GatewayError` — a
    `GatewayError` only exists once a response came back with a status. So
    neither was caught, and a laptop off the network sent a traceback where
    the caller expected JSON.
    """

    def setUp(self):
        self.mod = _load_script()

    @staticmethod
    def _json_block(text: str) -> dict:
        """Parse the final JSON block out of a mixed stderr stream.

        stderr also carries status lines and the spinner's output, so the
        block is located by its opening brace rather than by assuming the
        stream is pure JSON or slicing at a fixed offset.
        """
        lines = text.splitlines()
        starts = [i for i, line in enumerate(lines) if line.startswith("{")]
        if not starts:
            raise AssertionError(f"no JSON block on stderr; got:\n{text}")
        return json.loads("\n".join(lines[starts[-1]:]))

    def _run_main_with(self, error):
        import httpx  # noqa: F401  (guarded by the class decorator)
        buf = io.StringIO()
        with mock.patch.object(self.mod, "call_gateway", side_effect=error), \
             mock.patch.object(sys, "argv", ["transcribe-video.py", str(FIXTURE)]), \
             contextlib.redirect_stderr(buf), \
             contextlib.redirect_stdout(io.StringIO()):
            code = self.mod.main()
        return code, buf.getvalue()

    def _transport_errors(self):
        import httpx
        return (httpx.ConnectError("boom"), httpx.ReadTimeout("boom"))

    def test_a_transport_fault_exits_one_and_reports_json(self):
        for error in self._transport_errors():
            with self.subTest(error=type(error).__name__):
                code, err = self._run_main_with(error)
                self.assertEqual(code, 1)
                self.assertIs(self._json_block(err)["extraction_success"], False)

    def test_a_transport_fault_does_not_escape_as_a_traceback(self):
        """The caller parses stderr. An escaping exception gives it a
        traceback where the contract promises a JSON object."""
        for error in self._transport_errors():
            with self.subTest(error=type(error).__name__):
                try:
                    self._run_main_with(error)
                except BaseException as escaped:  # noqa: BLE001 — that IS the assertion
                    self.fail(f"{type(escaped).__name__} escaped main()")


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class MainSuccessContractTests(unittest.TestCase):
    """main()'s exit-code contract for the outcomes that reach a completion.

    A full transcript exits 0; a transcript under the word floor exits 2 so a
    calling skill can tell an empty/audio-only result from a real one; an
    unconfigured key exits 1 with the same stderr-parseable JSON block a
    transport fault gets, and without echoing the remediation text, which can
    name the key.
    """

    def setUp(self):
        self.mod = _load_script()

    def _run_main(self, *, returns=None, raises=None):
        buf, out = io.StringIO(), io.StringIO()
        patch = ({"side_effect": raises} if raises is not None
                 else {"return_value": returns})
        with mock.patch.object(self.mod, "call_gateway", **patch), \
                mock.patch.object(sys, "argv", ["transcribe-video.py", str(FIXTURE)]), \
                contextlib.redirect_stderr(buf), \
                contextlib.redirect_stdout(out):
            code = self.mod.main()
        return code, out.getvalue(), buf.getvalue()

    def test_a_full_transcript_exits_zero_and_prints_the_body(self):
        code, out, _ = self._run_main(returns="word " * 100)
        self.assertEqual(code, 0)
        self.assertIn("word", out)

    def test_a_thin_transcript_exits_two(self):
        """Under THIN_TRANSCRIPT_WORDS the run is flagged, not treated as success."""
        code, _, _ = self._run_main(returns="only three words")
        self.assertEqual(code, 2)

    def test_an_unconfigured_key_exits_one_and_reports_json(self):
        secret = "crux-env set OPENROUTER_API_KEY sk-or-DO-NOT-ECHO"
        err_cls = self.mod.EnvNotConfigured(["OPENROUTER_API_KEY"], secret)
        code, _, err = self._run_main(raises=err_cls)
        self.assertEqual(code, 1)
        block = MainExitContractTests._json_block(err)
        self.assertIs(block["extraction_success"], False)
        # The remediation string can carry a key value; main() must not echo it.
        self.assertNotIn("sk-or-DO-NOT-ECHO", err)


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed — run under uv")
class VideoProbeStatusTypingTests(unittest.TestCase):
    """A 2xx-non-200 in `openrouter_video_probe._attempt` is not success, and
    its bare `raise` with no active exception was a RuntimeError crashing to
    exit 1 — the code reserved for the video-unsupported verdict. It must be
    a typed gateway error, which `main()` routes to exit 2."""

    def setUp(self):
        import openrouter_video_probe
        self.probe = openrouter_video_probe

    def test_a_204_response_is_typed_not_a_bare_raise(self):
        from crux.core.llm_caller import GatewayUnexpectedStatusError

        class _Resp:
            status_code = 204

            def raise_for_status(self):
                return None

        class _Client:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def post(self, *args, **kwargs):
                return _Resp()

        with mock.patch.object(self.probe, "build_gateway_request",
                               return_value=("u", {}, {})), \
                mock.patch.object(self.probe.httpx, "Client", _Client):
            with self.assertRaises(GatewayUnexpectedStatusError) as ctx:
                self.probe._attempt(mock.Mock(), {"type": "image_url"})
        self.assertEqual(ctx.exception.status_code, 204)


if __name__ == "__main__":
    unittest.main()
