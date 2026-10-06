#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27"]
# ///
"""Transcribe a video to vault-grade markdown through the crux gateway.

Usage:
    transcribe-video.py <video_path> [--output-dir DIR] [--model NAME] [opts]

Output channels:
    stdout : the transcript markdown body (vault-ready, citable)
    stderr : status messages, progress spinner, and a final JSON metadata block

When --output-dir is provided, ALSO writes:
    <output-dir>/transcript.md   the same content as stdout
    <output-dir>/metadata.json   the same content as stderr's final JSON block

Requires OPENROUTER_API_KEY, resolved through `crux_env` (~/.crux/env) like
every other crux inference call. There is no `.env` loading and no
provider-specific key: this script reaches the model through the one
OpenAI-compatible gateway, not through a vendor SDK.

    crux-env set OPENROUTER_API_KEY <value>

Exit codes:
    0  success
    1  validation or generation error (details on stderr)
    2  thin transcript (under 50 words) - likely audio-only failure or empty
       video - OR a refused removed flag (reason on stderr)

Size ceiling:
    The video is inlined in the request body as a base64 data URL, so the
    ceiling is the request body, not a file-service quota: MAX_INLINE_BYTES
    (20 MB of file bytes, roughly 27 MB once base64-encoded). Compress or
    segment anything larger before transcribing.

    This is a REAL reduction. The retired path uploaded to a provider file
    service and could take 2 GB. Nothing inlined in an HTTP body can, so the
    old ceiling is not carried forward as a comforting number that no longer
    describes anything.

What this script no longer does, and why each is refused rather than ignored:

  * No resume cache. There is no upload step to resume, so the SHA256 cache
    that skipped one is gone along with --cache-dir and --force-reupload.
  * No --youtube. The gateway accepts inlined bytes, not a remote file URI.
  * No --start / --end / --fps. Those were provider-SDK video metadata with no
    gateway equivalent. Segment the file before transcribing instead.

Each removed flag is registered, hidden from --help, and refused by name with
the reason above. A flag that is accepted and then quietly ignored is worse than
one that is refused: the transcript comes back plausible and silently covers the
wrong footage. A flag merely unknown to the parser is the middle case — honest
about the refusal, silent about the remedy.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import json
import mimetypes
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from crux_env import EnvNotConfigured  # noqa: E402
from crux.core.llm_caller import (  # noqa: E402
    GatewayError,
    ModelRefusedError,
    build_gateway_request,
    call_gateway,
    get_default_model,
    get_model_config,
)

# The registry role, not a model literal: retiring a model is a registry edit.
MODEL_ROLE = "google_fast"

# The request-body ceiling described in the module docstring.
MAX_INLINE_BYTES = 20 * 1024 * 1024

THIN_TRANSCRIPT_WORDS = 50

# Generous on purpose. This model spends reasoning tokens from the same budget
# as output, so a tight ceiling truncates the transcript mid-sentence and the
# result reads as a failed transcription rather than as a budget that ran out.
MAX_OUTPUT_TOKENS = 32000

# The proven content-part shape for video (verified live against the gateway on
# 2026-08-26 with a 4-second fixture carrying a distinct on-screen string and a
# distinct spoken sentence; the model returned both, and the response's
# usage.prompt_tokens_details.video_tokens was non-zero). Video rides the
# ordinary `image_url` part with a `video/*` data URL - there is no `video_url`
# or `input_video` part on this API. `crux/scripts/tests/openrouter_video_probe.py`
# is the probe that established this and re-establishes it on demand.
MEDIA_PART_TYPE = "image_url"


# ──────────────────────────── progress spinner ────────────────────────────


class ProgressSpinner:
    """Stderr-only animated spinner. Never writes to stdout."""

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message: str):
        self.message = message
        self.running = False
        self.thread: threading.Thread | None = None
        self.start_time = 0.0
        self._frame_idx = 0

    def _loop(self) -> None:
        while self.running:
            frame = self.FRAMES[self._frame_idx % len(self.FRAMES)]
            elapsed = int(time.time() - self.start_time)
            sys.stderr.write(f"\r{frame} {self.message}... ({elapsed}s)")
            sys.stderr.flush()
            self._frame_idx += 1
            time.sleep(0.1)

    def start(self) -> None:
        if not sys.stderr.isatty():
            # Don't animate when stderr is captured (e.g., from a calling skill).
            sys.stderr.write(f"{self.message}...\n")
            sys.stderr.flush()
            return
        self.running = True
        self.start_time = time.time()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self, final: str | None = None) -> None:
        if self.running:
            self.running = False
            if self.thread:
                self.thread.join()
            sys.stderr.write("\r" + " " * 100 + "\r")
        if final:
            sys.stderr.write(final + "\n")
        sys.stderr.flush()


def _stderr(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


# ──────────────────────────── the unified vault-discipline prompt ────────────────────────────


VAULT_PROMPT = """You are transcribing a video for an archival research vault. Your output will be saved verbatim as a citable source page. The transcript IS the artifact — synthesis happens elsewhere in the vault. Follow these rules strictly.

## Rules

1. **Preserve exact words.** Transcribe what is said word-for-word. Do not paraphrase, summarize, or "clean up" speech. Omit hesitations ("um", "uh") only when they don't carry meaning; preserve them when they do (a long "uh…" before a key answer matters).

2. **No interpretation headers.** Do not insert sections like "Key Decisions", "Action Items", "Summary", "Conclusion", "Takeaways". The vault has separate synthesis pages for those. Your job is the raw transcript.

3. **Timestamps.** Mark every speaker turn or major scene change with `[MM:SS]` (or `[HH:MM:SS]` if the video is over 60 minutes). Use absolute timestamps from the start of the video.

4. **Speaker labels.** Use "Speaker 1", "Speaker 2", etc. unless names are stated in audio, shown on-screen, or given by an introduction. Once a name is established, use it consistently ("**Mike:**"). When introducing a named speaker for the first time, write the role too if stated: "**Mike (CEO):**".

5. **On-screen text — verbatim.** When text appears on screen (slides, code, terminal, UI labels, captions, lower-thirds, chyrons), preserve it verbatim in a blockquote: `> [On-screen MM:SS]: exact text`. Don't summarize. Don't OCR-fix obvious typos — if it's typo'd on screen, transcribe the typo.

6. **Visual context — only when meaningful.** Don't describe every gesture, camera angle, or facial expression. Describe visuals only when they carry meaning the audio doesn't (e.g., the speaker draws a diagram, gestures to specific data, demonstrates an action). Use `> [Visual MM:SS]: brief description`.

7. **Inaudible/unclear.** Mark unclear audio as `[inaudible]`. For best-guess transcription, use `[unclear: best-guess-text]`. Never fabricate words.

8. **No meta commentary.** Do not write "this is an interesting point", "the speaker emphasizes", "an important moment". Just transcribe.

9. **Capture gaps.** At the very end, under `## Capture gaps`, list anything you couldn't capture: timestamps of unclear audio, untranscribed visual material, slide content too small to read, etc. If nothing was missed, write "None.".

## Required output structure

Output exactly this structure, in this order, with no preamble or trailing remarks:

```
# <Title>

## Overview
- Duration: HH:MM:SS
- Speakers: N (names if stated)
- Setting: <meeting | presentation | interview | monologue | demo | lecture | podcast | conversation | other>
- Language: <e.g., English>

## Transcript

[MM:SS] **Speaker N:** exact words

[MM:SS] **Speaker N:** exact words

> [On-screen MM:SS]: verbatim text

> [Visual MM:SS]: meaningful description

...

## On-screen materials

<If significant on-screen content (slide decks, code blocks, diagrams) appeared, list each block here in full with its timestamp. Otherwise: "None significant.">

## Capture gaps

<List of unclear or missed material, or "None.">
```

## Title guidance

If the video opens with a title card, spoken intro, or clearly-stated topic in the first 30 seconds, use that as the `<Title>`. Otherwise infer one from the content — be specific. Never write "Video transcript" or "Untitled".

Begin now."""



# ──────────────────────────── the transcriber ────────────────────────────


class VideoTranscriber:
    """Builds and sends ONE gateway request carrying the video and the prompt."""

    def __init__(self, model: str | None = None):
        self.model = model or get_default_model(MODEL_ROLE)

    # ── format helpers

    @staticmethod
    def _format_size(n: float) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024.0:
                return f"{n:.2f} {unit}"
            n /= 1024.0
        return f"{n:.2f} TB"

    # ── the media part

    @classmethod
    def _check_size(cls, path: Path) -> int:
        # A non-regular file (FIFO, char device) reports size 0 from getsize, so
        # the ceiling below would pass and the read further down would then
        # stream unbounded bytes into the request body. Refuse anything that is
        # not a regular file, mirroring openrouter_video_probe.py's is_file guard.
        if not path.is_file():
            raise ValueError(
                f"not a regular file: {path}. The video is read in full and "
                "inlined as base64, so a FIFO or device node has no bounded "
                "size and is refused."
            )
        size = os.path.getsize(path)
        if size > MAX_INLINE_BYTES:
            raise ValueError(
                f"file too large: {cls._format_size(size)} exceeds the inline "
                f"request-body limit ({cls._format_size(MAX_INLINE_BYTES)}). "
                "The video is sent inline as base64, so this is a body-size "
                "ceiling, not a file-service quota. Compress the file or cut it "
                "into segments and transcribe each."
            )
        return size

    @classmethod
    def build_media_part(cls, path: Path) -> dict:
        """The video as one OpenAI-compatible content part.

        `mimetypes` decides the media type from the suffix. An unrecognized
        suffix is refused rather than guessed: the data URL's media type is what
        tells the gateway how to decode the bytes, and a wrong one is decoded as
        the wrong thing or dropped without comment.
        """
        if not path.exists():
            raise FileNotFoundError(f"video file not found: {path}")
        cls._check_size(path)
        mime, _ = mimetypes.guess_type(path.name)
        if not mime:
            raise ValueError(
                f"cannot determine the media type of {path.name!r} from its "
                "suffix. Rename it with a standard extension (.mp4, .mov, .webm, "
                ".m4a, .mp3, .wav) so the request can declare what it carries."
            )
        # Read at most MAX_INLINE_BYTES + 1 so the ceiling structurally bounds
        # the read: even if the file grew past the size check above (TOCTOU),
        # nothing beyond one byte over the limit reaches the request body.
        with path.open("rb") as fh:
            raw = fh.read(MAX_INLINE_BYTES + 1)
        if len(raw) > MAX_INLINE_BYTES:
            raise ValueError(
                f"file grew past the inline request-body limit "
                f"({cls._format_size(MAX_INLINE_BYTES)}) while being read."
            )
        encoded = base64.b64encode(raw).decode("ascii")
        return {
            "type": MEDIA_PART_TYPE,
            MEDIA_PART_TYPE: {"url": f"data:{mime};base64,{encoded}"},
        }

    # ── generate

    def _prepared(self, video_path: Path, thinking_level: str):
        """Assemble the (cfg, content) pair for one transcription call.

        The single assembly point, and the reason `build_request` and
        `transcribe` share it. They used to build this pair independently, so
        the seam the wire-shape tests assert against could drift from what the
        call actually sent: every payload assertion would stay green while the
        request on the wire changed. That is the same silent failure the tests
        exist to catch, one level up.

        `--thinking` is the gateway's top-level `reasoning_effort`, reached by
        overriding the registry entry's pinned effort. The override is
        deliberate, not a clobber: it keeps parity with the retired provider
        path, whose thinking control was also per-invocation. The provider
        `thinking_config` object it replaces has no gateway equivalent.
        """
        cfg = dataclasses.replace(get_model_config(self.model), effort=thinking_level)
        content = [
            {"type": "text", "text": VAULT_PROMPT},
            self.build_media_part(video_path),
        ]
        return cfg, content

    def build_request(self, video_path: Path, thinking_level: str = "high"):
        """Return the (url, headers, payload) triple for the transcription call.

        Sends nothing, so a test can assert the wire shape - the content part,
        the effort pin, the model - without a network call. It builds from the
        same `_prepared` pair `transcribe` sends, which is what makes those
        assertions describe the real request.
        """
        cfg, content = self._prepared(video_path, thinking_level)
        return build_gateway_request(
            cfg, content, max_tokens=MAX_OUTPUT_TOKENS, temperature=None,
        )

    def transcribe(
        self,
        *,
        video_path: Path,
        thinking_level: str = "high",
    ) -> tuple[str, dict]:
        size = os.path.getsize(video_path)
        meta: dict = {
            "model": self.model,
            "thinking_level": thinking_level,
            "transcribed_at": datetime.now(timezone.utc).isoformat(),
            "source": {
                "kind": "file",
                "path": str(video_path),
                "size_bytes": size,
            },
        }

        cfg, content = self._prepared(video_path, thinking_level)

        _stderr(f"video: {video_path.name} ({self._format_size(size)})")
        spin = ProgressSpinner(f"generating transcript with {self.model}")
        spin.start()
        try:
            # ONE HTTP path, shared with the router and the council: status
            # mapping and response parsing come from the same helpers, so this
            # script cannot drift into its own error taxonomy.
            text = call_gateway(
                cfg, content, max_tokens=MAX_OUTPUT_TOKENS, temperature=None,
            )
        finally:
            spin.stop()
        _stderr("generation complete")

        text = (text or "").strip()
        words = len(text.split())
        meta["word_count"] = words
        meta["char_count"] = len(text)
        meta["thin_content"] = words < THIN_TRANSCRIPT_WORDS
        return text, meta


# ──────────────────────────── main ────────────────────────────


class _RemovedFlag(argparse.Action):
    """Refuse a removed flag by name, saying why it went, then exit 2.

    Registering the flag and hiding it beats leaving it unknown. Argparse
    answers an unknown flag with `unrecognized arguments: --start`, which tells
    the user the flag is gone and nothing about what to do instead — and for
    `--start` there IS something: cut the file up first.

    `nargs="*"` on the value-taking flags is what lets the reason reach the
    user. Under the default nargs, a bare `--start` fails argparse's own
    "expected one argument" check first and exits before this action runs, so
    the half-remembered invocation gets the least helpful message.
    """

    def __init__(self, option_strings, dest, reason: str = "", nargs="*", **kwargs):
        super().__init__(option_strings, dest, nargs=nargs, **kwargs)
        self.reason = reason

    def __call__(self, parser, namespace, values, option_string=None):
        _stderr(f"error: {option_string} was removed — {self.reason}")
        parser.exit(2)


#: Every flag the gateway migration retired, and the reason each gives. Grouped
#: by reason: flags that share one cause share one sentence.
_REMOVED_FLAGS: tuple[tuple[tuple[str, ...], str, int | str], ...] = (
    (("--youtube",),
     "no gateway equivalent: the gateway accepts inlined bytes, not a remote "
     "file URI.", "*"),
    (("--start", "--end", "--fps"),
     "no gateway equivalent; segment the file before transcribing.", "*"),
    (("--api-key",),
     "the credential comes from crux_env, not from a flag "
     "(crux-env set OPENROUTER_API_KEY <value>).", "*"),
    (("--cache-dir",),
     "there is no upload step to resume, so nothing is cached.", "*"),
    (("--force-reupload",),
     "there is no upload step to resume, so nothing is cached.", 0),
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Transcribe a video to vault-grade markdown through the crux gateway.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("video", help="Path to a local video file")
    p.add_argument("--output-dir", type=Path, help="Also save transcript.md + metadata.json here")
    p.add_argument("--model", help=f"Model name (default: the {MODEL_ROLE!r} registry role)")
    p.add_argument("--thinking", choices=["low", "high"], default="high",
                   help="Reasoning effort carried as the gateway's reasoning_effort")

    # Hidden from --help: these are refusals, not options. A retired flag has no
    # place in the help of the tool that no longer has the feature.
    for flags, reason, nargs in _REMOVED_FLAGS:
        for flag in flags:
            p.add_argument(flag, action=_RemovedFlag, reason=reason, nargs=nargs,
                           help=argparse.SUPPRESS)
    return p


def main() -> int:
    args = build_parser().parse_args()

    try:
        t = VideoTranscriber(model=args.model)
        text, meta = t.transcribe(
            video_path=Path(args.video),
            thinking_level=args.thinking,
        )
        meta["extraction_success"] = True
    except KeyboardInterrupt:
        _stderr("interrupted.")
        return 1
    except EnvNotConfigured:
        # Remediation text is deliberately not echoed - it can name the key.
        _stderr("error: OPENROUTER_API_KEY is not configured "
                "(crux-env set OPENROUTER_API_KEY <value>)")
        print(json.dumps({"error": "OPENROUTER_API_KEY is not configured",
                          "extraction_success": False}, indent=2), file=sys.stderr)
        return 1
    # `httpx.HTTPError` covers the faults that never reach a status: connect
    # failures, DNS, TLS, and read deadlines. None of them is an OSError and
    # none is a GatewayError — a GatewayError exists only once a response came
    # back — so without this arm a laptop off the network sent a traceback to a
    # caller that parses stderr for the JSON block below.
    except (ModelRefusedError, GatewayError, httpx.HTTPError, OSError, ValueError) as e:
        _stderr(f"error: {e}")
        print(
            json.dumps({"error": str(e), "extraction_success": False}, indent=2),
            file=sys.stderr,
        )
        return 1

    # stdout = transcript
    print(text)
    # stderr = final JSON metadata
    print(json.dumps(meta, indent=2), file=sys.stderr)

    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "transcript.md").write_text(text, encoding="utf-8")
        (args.output_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return 2 if meta["thin_content"] else 0


if __name__ == "__main__":
    sys.exit(main())
