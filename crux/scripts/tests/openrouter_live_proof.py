#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27"]
# ///
"""Live proof of the two gateway properties no offline test can discharge.

**Phase 1 — the three seats resolve to three distinct serving providers.** The
registry's `serving_providers` pin is a *request*, and only a real response says
which host the gateway actually used. OpenRouter echoes it in the response's
top-level `provider` field, so the proof is three real calls and one set-size
assertion. 16 output tokens each: the assertion is about routing, not content.

**Phase 2 — a full-size council call finishes inside the council's deadline.**
Phase 1 caps output at 16 tokens, which proves nothing about the parameters the
council actually uses: `max_tokens=32000`, no streaming, and one
`timeout_seconds` deadline covering the whole request. A non-streaming call
returns nothing until the last token is generated, so the deadline has to cover
the entire generation — the failure mode is a seat that routes correctly and
still times out under real load. This phase runs ONE seat at exactly those
parameters and reports the wall-clock time against the deadline.

The seat is the Anthropic one deliberately: `anthropic_top` resolves to
claude-opus-5.5-xhigh pinned at effort XHIGH. It is the seat most likely to
breach the deadline.

Deliberately NOT named `test_*`: it spends money and needs the network, so it
must never be swept up by `unittest discover` or `pytest`. Two independent gates
guard it — `CRUX_ALLOW_LIVE=1` and a configured `OPENROUTER_API_KEY`.

    CRUX_ALLOW_LIVE=1 uv run crux/scripts/tests/openrouter_live_proof.py

EXIT CODES follow the crux script convention, and the 1/2 split is the point:

    0  proved — both phases
    1  a GENUINE failure of the thing being proved: the seats did not resolve to
       three distinct providers, or the full-size call breached the deadline
    2  a capability/environment refusal: the gate is closed, no key is
       configured, the key is rejected (401/403), the account is out of credit
       (402), the gateway itself faulted (429/5xx), the model declined, or the
       request never reached the gateway at all (offline, DNS, TLS, connect)

An unfunded or mistyped key must never read as a failed proof. Every gateway
failure is therefore caught and routed to 2 — exit 1 is reserved for the two
assertions above and nothing else.

The same reservation covers the transport layer. A laptop off the network
produces `httpx.ConnectError`, which said nothing about whether the seats route
distinctly, and would have exited 1 — the code that means "the proof failed".
Phase 2's `ReadTimeout` catch is the deliberate exception to this and stays
where it is: the request was sent, the gateway had the deadline to answer in and
did not, so a `ReadTimeout` on the full-size call IS the second assertion
failing and is a real exit 1.

It catches `ReadTimeout` and not `TimeoutException` because the other members of
that family are transport, not deadline. A `ConnectTimeout` or a `PoolTimeout`
means the request never got out — nothing measured the gateway's response time,
so nothing about the deadline was observed. Those fall to main's
`httpx.TransportError` arm and exit 2 with every other unreachable-gateway
fault.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from crux_env import EnvNotConfigured, get  # noqa: E402
from crux.council.async_council import AsyncCouncilConfig  # noqa: E402
from crux.core.llm_caller import (  # noqa: E402
    GatewayAuthError,
    GatewayError,
    GatewayInsufficientCreditError,
    ModelRefusedError,
    build_gateway_request,
    get_default_model,
    get_model_config,
    parse_gateway_response,
    raise_for_gateway_status,
)

# The three council seats, by the role each resolves through.
SEAT_ROLES = (("openai", "openai_top"),
              ("anthropic", "anthropic_top"),
              ("gemini", "google_top"))

# Phase 2 runs the xhigh Anthropic seat to test the deadline at its full effort.
FULL_SIZE_SEAT = ("anthropic", "anthropic_top")

# A real deliberation-shaped request. The council's own workload is a reasoned
# JSON vote, so a trivial prompt would time the wrong thing.
FULL_SIZE_PROMPT = (
    "Review this proposal and respond with a thorough critique: a service "
    "consolidates three vendor SDK call paths onto one OpenAI-compatible HTTP "
    "gateway, keeping one API key for all three. Cover the failure modes, the "
    "blast radius of the shared credential, and what the migration must prove "
    "before it ships. Be specific and complete."
)


def _phase1_distinct_providers() -> dict[str, str | None]:
    """Three seats, three real calls; return the serving provider each got."""
    observed: dict[str, str | None] = {}
    for seat, role in SEAT_ROLES:
        model = get_default_model(role)
        cfg = get_model_config(model)
        url, headers, payload = build_gateway_request(
            cfg, "Reply with the single word: ok.",
            max_tokens=16, temperature=None,
        )
        with httpx.Client(timeout=120.0) as client:
            response = client.post(url, headers=headers, json=payload)
            raise_for_gateway_status(response)
            data = response.json()
        provider = data.get("provider")
        observed[seat] = provider
        print(f"{seat:10s} {model:24s} -> requested {list(cfg.serving_providers)} "
              f"served by {provider!r}")
    return observed


def _phase2_full_size_within_deadline() -> bool:
    """One seat at the council's real parameters. True if it beat the deadline.

    The httpx client is given the SAME deadline the council enforces, so a
    breach surfaces as a `ReadTimeout` rather than as a slow success — which is
    exactly how it would surface in a real deliberation.
    """
    config = AsyncCouncilConfig()
    seat, role = FULL_SIZE_SEAT
    model = get_default_model(role)
    cfg = get_model_config(model)
    url, headers, payload = build_gateway_request(
        cfg, FULL_SIZE_PROMPT,
        system="You are a critical reviewer.",
        max_tokens=config.max_tokens,
        temperature=None,
    )
    assert "stream" not in payload, "the council never streams; neither may this"

    print(f"\n{seat:10s} {model:24s} -> max_tokens={config.max_tokens} "
          f"no-streaming deadline={config.timeout_seconds}s")

    started = time.monotonic()
    try:
        with httpx.Client(timeout=config.timeout_seconds) as client:
            response = client.post(url, headers=headers, json=payload)
            raise_for_gateway_status(response)
            data = response.json()
    except httpx.ReadTimeout:
        elapsed = time.monotonic() - started
        print(f"FAIL: full-size call breached the council deadline "
              f"({config.timeout_seconds}s) after {elapsed:.1f}s", file=sys.stderr)
        return False

    elapsed = time.monotonic() - started
    content = parse_gateway_response(data, cfg.api_string)
    usage = data.get("usage") or {}
    completion_tokens = usage.get("completion_tokens")
    finish = ((data.get("choices") or [{}])[0] or {}).get("finish_reason")

    print(f"           completed in {elapsed:.1f}s of {config.timeout_seconds}s "
          f"({elapsed / config.timeout_seconds:.0%} of the deadline)")
    print(f"           completion_tokens={completion_tokens} "
          f"chars={len(content)} finish_reason={finish!r}")

    if not content:
        print("FAIL: full-size call returned no content", file=sys.stderr)
        return False
    return True


def main() -> int:
    if os.environ.get("CRUX_ALLOW_LIVE") != "1":
        print("refusing: set CRUX_ALLOW_LIVE=1 to make live, billable calls", file=sys.stderr)
        return 2
    if not get("OPENROUTER_API_KEY"):
        print("refusing: OPENROUTER_API_KEY is not configured "
              "(crux-env set OPENROUTER_API_KEY <value>)", file=sys.stderr)
        return 2

    # Every gateway failure below is an ENVIRONMENT problem, never a failed
    # proof. Letting one escape would exit 1 with a traceback, and an operator
    # reading exit 1 from this script is entitled to conclude the seats stopped
    # resolving distinctly.
    try:
        observed = _phase1_distinct_providers()
        # Checked BEFORE phase 2: if the seats no longer route distinctly there
        # is nothing to learn from spending a 32k-token call on the answer.
        distinct = set(observed.values())
        if len(distinct) != len(SEAT_ROLES) or None in distinct:
            print(f"FAIL: seats did not resolve to three distinct providers: {observed}",
                  file=sys.stderr)
            return 1
        print(f"\nPROVED: three distinct serving providers {sorted(distinct)}")

        full_size_ok = _phase2_full_size_within_deadline()
    except ModelRefusedError:
        print("refusing: the model declined the request (a safety refusal, "
              "HTTP 200). This is an environment problem, not a failed proof.",
              file=sys.stderr)
        return 2
    except GatewayInsufficientCreditError as err:
        print(f"refusing: the account is out of credit (HTTP {err.status_code}). "
              "This is an environment problem, not a failed proof — add credit "
              "or raise the key's spend limit and re-run.", file=sys.stderr)
        return 2
    except GatewayAuthError as err:
        print(f"refusing: the gateway rejected the credential (HTTP {err.status_code}). "
              "This is an environment problem, not a failed proof — validate "
              "OPENROUTER_API_KEY presence with `crux-env check`, or re-set it "
              "with `crux-env set OPENROUTER_API_KEY <value>`.", file=sys.stderr)
        return 2
    except GatewayError as err:
        print(f"refusing: the gateway failed the request (HTTP {err.status_code}). "
              "This is an environment problem, not a failed proof — retry when "
              "the gateway recovers.", file=sys.stderr)
        return 2
    except httpx.TransportError as err:
        # The request never reached the gateway: offline, DNS failure, TLS
        # failure, connect refused, or a read timeout on phase 1's own 120s
        # client. None of these observed the seats' routing, so none of them can
        # report on it. Phase 2's deadline breach does NOT arrive here — it is
        # caught inside `_phase2_full_size_within_deadline`, which returns False
        # and yields the exit 1 that breach has earned.
        print(f"refusing: the request never reached the gateway "
              f"({type(err).__name__}). This is an environment problem, not a "
              "failed proof — check the network and re-run.", file=sys.stderr)
        return 2
    except EnvNotConfigured:
        # The key vanished between the gate above and the call (or resolves
        # empty). Remediation text is deliberately not echoed.
        print("refusing: OPENROUTER_API_KEY is not configured. This is an "
              "environment problem, not a failed proof.", file=sys.stderr)
        return 2

    if not full_size_ok:
        return 1
    print("PROVED: a full-size council call completes inside the council deadline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
