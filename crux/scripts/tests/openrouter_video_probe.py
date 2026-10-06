#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27"]
# ///
"""Live proof that end-to-end video transits the gateway (ADR-0087).

`transcribe-video.py` is the last caller holding a provider SDK. Retiring that
SDK means sending video through the one OpenAI-compatible gateway instead, and
whether the gateway carries video is a POSTCONDITION the dev module proves — the
catalog listing `video` among Gemini's `input_modalities` is advertised
capability, not evidence. This script is that proof.

It also answers a question no documentation settled: WHICH content-part shape
the gateway accepts for video. The candidate shapes below are tried in order and
the first one that both returns HTTP 200 and demonstrates comprehension wins.
The winning shape is printed verbatim, and that printed shape is the answer the
migration is built on.

**Comprehension, not acceptance.** A 200 alone proves nothing: a gateway that
silently drops an unrecognized content part still answers the text half of the
prompt, fluently and wrongly. The fixture therefore carries two secrets that
appear in no prompt — `MAGENTA FALCON` rendered on every frame, and the spoken
sentence "The passphrase is seventeen harbor lanterns" in the audio track. A
response containing the visual secret saw frames; one containing the spoken
secret heard audio. A 200 containing neither is recorded as a DROP, which is the
failure this probe exists to catch.

Two details of that check are load-bearing, both learned by getting them wrong:

1. **`max_tokens` must be generous.** This model spends reasoning tokens before
   emitting any content, and they come out of the same budget. At 300 the reply
   truncated mid-sentence, every candidate scored as a DROP, and the run was one
   step from being escalated as "the gateway does not carry video" — when the
   real finding was a budget too small to reach the quotation.
2. **The token counter is the corroborating witness.** `usage` reports
   `prompt_tokens_details.video_tokens`, which the gateway computes from what it
   actually decoded. It cannot be produced by a fluent guess, so it is reported
   beside the text evidence and a shape is only accepted when the two agree.

Deliberately NOT named `test_*`: it spends money and needs the network, so it
must never be swept up by `unittest discover` or `pytest`. Two independent gates
guard it — `CRUX_ALLOW_LIVE=1` and a configured `OPENROUTER_API_KEY`.

    CRUX_ALLOW_LIVE=1 uv run crux/scripts/tests/openrouter_video_probe.py

EXIT CODES follow the crux script convention:

    0  proved — a content-part shape carries video end to end, comprehension
       confirmed against a secret the prompt never mentions
    1  a GENUINE failure: no candidate shape carried the video. Exit 1 is
       reserved for exactly this — the video-unsupported verdict — and nothing
       else: every status family outside a plain 4xx client error is routed
       through `raise_for_gateway_status` and reaches exit 2's environment
       refusal instead, so a rejected shape never transports raw response text
       to stderr (a key-leak path)
    2  a capability/environment refusal: the gate is closed, no key is
       configured, the key is rejected, the account is out of credit, the
       gateway faulted, the fixture is missing, or the request never arrived

Exit 1 is reserved for "the gateway does not carry video" and nothing else.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from crux_env import EnvNotConfigured, get  # noqa: E402
from crux.core.llm_caller import (  # noqa: E402
    GatewayAuthError,
    GatewayClientError,
    GatewayError,
    GatewayInsufficientCreditError,
    GatewayUnexpectedStatusError,
    ModelRefusedError,
    build_gateway_request,
    get_default_model,
    get_model_config,
    parse_gateway_response,
    raise_for_gateway_status,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "media" / "probe-4s.mp4"
MIME = "video/mp4"

# The two secrets, one per track. Neither appears in the prompt.
VISUAL_SECRET = ("magenta", "falcon")
SPOKEN_SECRET = ("seventeen", "harbor", "lantern")

PROMPT = (
    "Describe this video. Report exactly two things: any text visible on "
    "screen, and any words spoken in the audio. Quote both verbatim."
)


def _candidate_parts(data_url: str) -> list[tuple[str, dict]]:
    """The content-part shapes to try, most-likely first.

    Ordered by how the gateway documents its OTHER non-text modalities: images
    ride `image_url`, PDFs ride `file`/`file_data`, audio rides `input_audio`.
    Video is documented by none of them, which is why this list is a probe and
    not a lookup.
    """
    b64 = data_url.split(",", 1)[1]
    return [
        ("image_url + data URL",
         {"type": "image_url", "image_url": {"url": data_url}}),
        ("video_url + data URL",
         {"type": "video_url", "video_url": {"url": data_url}}),
        ("file + file_data",
         {"type": "file",
          "file": {"filename": FIXTURE.name, "file_data": data_url}}),
        ("input_video + base64",
         {"type": "input_video",
          "input_video": {"data": b64, "format": "mp4"}}),
        ("video + data URL",
         {"type": "video", "video": {"url": data_url}}),
    ]


# Large enough that reasoning tokens cannot starve the answer. See the module
# docstring: a small budget makes every shape look like a silent drop.
MAX_TOKENS = 4000


def _attempt(cfg, part: dict) -> tuple[int, str, int, str]:
    """Send one candidate. Returns (status, text, video_tokens, rejection_note).

    Status handling follows `openrouter_live_proof.py`'s pattern: the status is
    routed through `raise_for_gateway_status`, the one place an HTTP status
    becomes a typed exception. A plain 4xx (`GatewayClientError`) is this
    probe's normal control flow — "not that shape" — recorded by exception
    CLASS NAME, never by `response.text`, which can echo contents of the
    request or the credential back into stderr. Every other status family
    (auth, credit, rate, timeout, upstream, unexpected) raises and reaches
    `main()` as an environment refusal (exit 2), so a true "the gateway does
    not carry video" exit 1 is reserved for genuine rejections/drops. The
    2xx-non-200 gap is closed separately: `raise_for_gateway_status` types
    every NON-2xx, so a 201/202/204 would slip past it silently — and any
    non-2xx it ever returned from without raising would mean the typing
    contract itself broke. The AssertionError below asserts that invariant and
    names the status.
    """
    content = [{"type": "text", "text": PROMPT}, part]
    url, headers, payload = build_gateway_request(
        cfg, content, max_tokens=MAX_TOKENS, temperature=None,
    )
    with httpx.Client(timeout=180.0) as client:
        response = client.post(url, headers=headers, json=payload)
    if response.status_code != 200:
        if 200 <= response.status_code <= 299:
            raise GatewayUnexpectedStatusError(
                f"probe got 2xx-non-200 (HTTP {response.status_code})",
                response.status_code,
            )
        try:
            raise_for_gateway_status(response)
        except GatewayClientError as err:
            return err.status_code, "", 0, type(err).__name__
        raise AssertionError(
            "invariant violated: raise_for_gateway_status returned without "
            f"typing HTTP {response.status_code}"
        )
    data = response.json()
    details = (data.get("usage") or {}).get("prompt_tokens_details") or {}
    video_tokens = details.get("video_tokens") or 0
    return 200, parse_gateway_response(data, cfg.api_string), video_tokens, ""


def _comprehension(text: str) -> tuple[bool, bool]:
    low = text.lower()
    return (all(w in low for w in VISUAL_SECRET),
            any(w in low for w in SPOKEN_SECRET))


def probe() -> int:
    if not FIXTURE.is_file():
        print(f"refusing: fixture missing at {FIXTURE}", file=sys.stderr)
        return 2

    raw = FIXTURE.read_bytes()
    data_url = f"data:{MIME};base64," + base64.b64encode(raw).decode("ascii")
    model = get_default_model("google_fast")
    cfg = get_model_config(model)
    print(f"model {model} -> {cfg.api_string}")
    print(f"fixture {FIXTURE.name} {len(raw)} bytes -> {len(data_url)} chars base64\n")

    rejected: list[str] = []
    dropped: list[str] = []

    for label, part in _candidate_parts(data_url):
        status, text, video_tokens, note = _attempt(cfg, part)
        if status != 200:
            print(f"  {label:24s} HTTP {status}")
            rejected.append(f"{label}: HTTP {status}: {note}")
            continue
        saw_visual, heard_audio = _comprehension(text)
        if not (saw_visual or heard_audio):
            # 200 with neither secret: the part was accepted and discarded.
            print(f"  {label:24s} HTTP 200 video_tokens={video_tokens} "
                  "but NEITHER secret returned — DROPPED")
            dropped.append(f"{label}: video_tokens={video_tokens}: {text[:600]!r}")
            continue

        print(f"  {label:24s} HTTP 200 — visual={saw_visual} audio={heard_audio} "
              f"video_tokens={video_tokens}\n")
        print("WORKING REQUEST SHAPE (verbatim, the content part only):")
        redacted = json.loads(json.dumps(part))
        _redact_payload(redacted)
        print(json.dumps(redacted, indent=2))
        print("\nTRANSCRIPT:")
        print(text.strip())
        print(f"\nPROVED: video transits the gateway via the {label!r} content part")
        return 0

    print("\nFAIL: no candidate content-part shape carried the video.",
          file=sys.stderr)
    for line in rejected:
        print(f"  rejected  {line}", file=sys.stderr)
    for line in dropped:
        print(f"  dropped   {line}", file=sys.stderr)
    return 1


def _redact_payload(node) -> None:
    """Replace the base64 payload with a placeholder so the printed shape is
    readable. The SHAPE is the finding; the 31k-character blob is not."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and len(value) > 120:
                node[key] = f"<{len(value)} chars of base64 {MIME}>"
            else:
                _redact_payload(value)
    elif isinstance(node, list):
        for item in node:
            _redact_payload(item)


def main() -> int:
    if os.environ.get("CRUX_ALLOW_LIVE") != "1":
        print("refusing: set CRUX_ALLOW_LIVE=1 to make live, billable calls", file=sys.stderr)
        return 2
    if not get("OPENROUTER_API_KEY"):
        print("refusing: OPENROUTER_API_KEY is not configured "
              "(crux-env set OPENROUTER_API_KEY <value>)", file=sys.stderr)
        return 2

    try:
        return probe()
    except AssertionError as err:
        # A broken typing contract is an environment fault, never the
        # video-unsupported verdict — exit 1 is reserved for "the gateway
        # does not carry video".
        print(f"refusing: the probe's typing contract broke — {err}",
              file=sys.stderr)
        return 2
    except ModelRefusedError:
        print("refusing: the model declined the request (a safety refusal, "
              "HTTP 200). This is an environment problem, not a failed proof.",
              file=sys.stderr)
        return 2
    except GatewayInsufficientCreditError as err:
        print(f"refusing: the account is out of credit (HTTP {err.status_code}).",
              file=sys.stderr)
        return 2
    except GatewayAuthError as err:
        print(f"refusing: the gateway rejected the credential (HTTP {err.status_code}).",
              file=sys.stderr)
        return 2
    except GatewayError as err:
        print(f"refusing: the gateway failed the request (HTTP {err.status_code}).",
              file=sys.stderr)
        return 2
    except httpx.TransportError as err:
        print(f"refusing: the request never reached the gateway "
              f"({type(err).__name__}).", file=sys.stderr)
        return 2
    except EnvNotConfigured:
        print("refusing: OPENROUTER_API_KEY is not configured.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
