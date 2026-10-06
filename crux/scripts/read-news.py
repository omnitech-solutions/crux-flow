#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27",
# ]
# ///
"""Fetch ranked news results from the Perplexity Search API.

Purpose:
    Thin helper script for the read-news skill (ADR-0040 §2).  POSTs a query
    to the Perplexity Search API and writes the results as JSON to stdout.
    The PERPLEXITY_API_KEY is loaded from the crux env store — it is NEVER
    written to stdout, stderr, exceptions, or logs.

Canonical invocation:
    uv run "${CRUX_PLUGIN_ROOT}/scripts/read-news.py" --query "<q>" \
        [--max-results N] [--search-context-size low|medium|high] \
        [--timeout SECONDS] [--since YYYY-MM-DD]

Freshness:
    Every result carries two dates. `date` is when the page was published;
    `last_updated` is when the search index last crawled it. Only `date` says
    whether an item is new. A page published years ago re-crawled yesterday
    ranks as fresh and carries a fresh `last_updated`, which is how three
    curated sources returned nothing new for four nights while their feeds
    held thirty new posts. `--since` keeps a result only when its `date`
    parses as a calendar date on or after the given day; a result with no
    `date` is dropped, because an undated item cannot be shown to be new.
    The envelope reports `since`, `dropped_older` and `dropped_undated` so the
    delta is auditable and the two drop reasons stay apart. `last_updated`
    never counts.

Exit codes:
    0  success — JSON results on stdout
    1  real failure — JSON error envelope on stdout (inspect the envelope):
         error=usage_error   → bad argument (e.g. --max-results > 20); fix it
         error=api_error     → non-2xx from Perplexity; retry up to 2 then fall back
         error=network       → connection/timeout; retry up to 2 then fall back
         error=internal      → unexpected exception; scrubbed detail in envelope
    2  environment / capability problem — JSON error envelope on stdout; fall back:
         error=env_not_configured → PERPLEXITY_API_KEY not set in crux env store
         error=capability_error   → httpx not installed (run via `uv run`)
    stdout is NEVER empty on any controlled exit path.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Defensive sys.path insert so `import crux_env` resolves to the stdlib-only
# sibling module at crux/scripts/crux_env.py without ever executing
# crux/__init__.py's LLM-router import chain (ADR-0040 §2, ADR-0035 §2).
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent))
import crux_env  # noqa: E402

# httpx is a soft import: available under `uv run` (PEP 723 block above) but
# possibly absent under bare python3.  Module-level import keeps it patchable
# for tests; absence is caught at the HTTP-call stage with a clear error.
try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PERPLEXITY_SEARCH_URL = "https://api.perplexity.ai/search"
DEFAULT_MAX_RESULTS = 10
MAX_RESULTS_CAP = 20
DEFAULT_SEARCH_CONTEXT_SIZE = "low"
DEFAULT_TIMEOUT = 30
_KEY_NAME = "PERPLEXITY_API_KEY"


# ---------------------------------------------------------------------------
# Key sanitiser — strip the key value from any string before printing.
# httpx error messages can embed request headers; scrub defensively.
# ---------------------------------------------------------------------------
def _scrub(text: str, key: str) -> str:
    """Replace every occurrence of *key* in *text* with '[REDACTED]'."""
    if not key:
        return text
    return text.replace(key, "[REDACTED]")


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def _main_inner(argv: list[str] | None = None) -> int:
    """Core logic; called by main() which wraps it with a top-level catch."""
    parser = argparse.ArgumentParser(
        description="Fetch ranked news results from the Perplexity Search API.",
        add_help=True,
    )
    parser.add_argument(
        "--query",
        required=True,
        help="Search query to send to Perplexity.",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=DEFAULT_MAX_RESULTS,
        dest="max_results",
        help=f"Maximum results to return (1–{MAX_RESULTS_CAP}; default {DEFAULT_MAX_RESULTS}).",
    )
    parser.add_argument(
        "--search-context-size",
        choices=["low", "medium", "high"],
        default=DEFAULT_SEARCH_CONTEXT_SIZE,
        dest="search_context_size",
        help="Search context size (default: low).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"HTTP timeout in seconds (default {DEFAULT_TIMEOUT}).",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Keep only results whose publication `date` is on or after this "
             "ISO day (YYYY-MM-DD). `last_updated` is a crawl stamp and never counts.",
    )

    args = parser.parse_args(argv)

    # --- Cap enforcement (a contract, not advisory) -------------------------
    # exit 1 = usage error WITH a JSON envelope (the caller passed a bad arg;
    # this is NOT an environment problem so exit 2 would be wrong).
    if args.max_results > MAX_RESULTS_CAP:
        print(
            json.dumps(
                {
                    "error": "usage_error",
                    "detail": f"max_results hard cap is {MAX_RESULTS_CAP}",
                }
            ),
            flush=True,
        )
        return 1

    since: date | None = None
    if args.since is not None:
        try:
            since = date.fromisoformat(args.since)
        except ValueError:
            print(
                json.dumps(
                    {
                        "error": "usage_error",
                        "detail": "--since must be an ISO calendar date (YYYY-MM-DD)",
                    }
                ),
                flush=True,
            )
            return 1

    # --- Key lookup ---------------------------------------------------------
    api_key = crux_env.get(_KEY_NAME)
    if not api_key:
        # Emit JSON envelope to stdout; remediation to stderr.
        print(
            json.dumps(
                {"error": "env_not_configured", "missing": [_KEY_NAME]},
            ),
            flush=True,
        )
        print(
            f'Run: python3 "${{CRUX_PLUGIN_ROOT}}/scripts/crux-env.py" '
            f"set {_KEY_NAME} <value>",
            file=sys.stderr,
        )
        return 2

    # --- HTTP call ----------------------------------------------------------
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = {
        "query": args.query,
        "max_results": args.max_results,
        "search_context_size": args.search_context_size,
    }

    # exit 2 = capability / environment problem → caller should fall back.
    # stdout carries a JSON envelope; stderr carries the install remediation.
    if httpx is None:
        print(
            json.dumps(
                {
                    "error": "capability_error",
                    "missing": ["httpx"],
                    "detail": (
                        "httpx is not installed; run this script via "
                        "`uv run ${CRUX_PLUGIN_ROOT}/scripts/read-news.py` "
                        "to get the correct dependencies"
                    ),
                }
            ),
            flush=True,
        )
        print(
            "install: uv run ${CRUX_PLUGIN_ROOT}/scripts/read-news.py "
            "(httpx will be fetched automatically via PEP 723)",
            file=sys.stderr,
        )
        return 2

    try:
        with httpx.Client() as client:
            response = client.post(
                PERPLEXITY_SEARCH_URL,
                json=body,
                headers=headers,
                timeout=args.timeout,
            )
    except httpx.RequestError as exc:
        detail = _scrub(str(exc), api_key)
        print(json.dumps({"error": "network", "detail": detail[:500]}))
        return 1

    # --- Response handling --------------------------------------------------
    if response.status_code < 200 or response.status_code >= 300:
        # Scrub FIRST, THEN truncate — so a key straddling the 500-char boundary
        # is fully replaced before the slice discards the tail.
        safe_detail = _scrub(response.text, api_key)[:500]
        print(
            json.dumps(
                {
                    "error": "api_error",
                    "status": response.status_code,
                    "detail": safe_detail,
                }
            )
        )
        return 1

    # Parse response JSON.
    try:
        payload = response.json()
    except Exception as exc:
        safe_msg = _scrub(str(exc), api_key)
        print(
            json.dumps(
                {
                    "error": "unexpected_response_shape",
                    "detail": f"JSON parse error: {safe_msg[:500]}",
                }
            )
        )
        return 1

    if not isinstance(payload, dict) or "results" not in payload:
        print(
            json.dumps(
                {
                    "error": "unexpected_response_shape",
                    "detail": "response JSON missing 'results' key",
                }
            )
        )
        return 1

    # --- Emit success output -----------------------------------------------
    # The query is echoed as the audit trail (ADR-0040 §2).
    # The key NEVER appears here.
    results = payload["results"]
    output = {
        "query": args.query,
        "max_results": args.max_results,
        "search_context_size": args.search_context_size,
    }
    if since is not None:
        kept, census = _filter_since(results, since)
        output["since"] = since.isoformat()
        output["dropped_older"] = census["older"]
        output["dropped_undated"] = census["undated"]
        output["dropped_by_since"] = census["older"] + census["undated"]
        results = kept
    output["results"] = results
    print(json.dumps(output))
    return 0


def _parse_day(value: object) -> date | None:
    """The publication day of a `date` field, or None when it carries none.

    Accepts the bare `YYYY-MM-DD` the API is documented to emit AND the
    datetime shapes `date.fromisoformat` rejects on older interpreters --
    `2026-09-11T10:00:00Z`, `...+00:00`, `...T10:00:00`. The tree holds no
    captured live result, so the bare-day assumption is unverified; reading
    only that shape would drop every result of a datetime-emitting API and
    render it as `results: []`, which a reader cannot tell from "nothing new".
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _filter_since(results: object, since: date) -> tuple[list, dict]:
    """Split `results` into those published on or after `since` and a drop census.

    Reads the `date` field only; `last_updated` is a crawl stamp and never
    counts. The two drop reasons are counted apart, because they are not the
    same claim: `older` is a result shown to be stale, `undated` is a result
    nothing could place in time. Undated is where a re-crawled evergreen page
    hides, so it stays dropped -- but a reader who cannot see the two counts
    apart cannot tell a quiet night from a filter eating everything.
    """
    kept: list = []
    census = {"older": 0, "undated": 0}
    if not isinstance(results, list):
        return kept, census
    for item in results:
        if not isinstance(item, dict):
            census["undated"] += 1
            continue
        day = _parse_day(item.get("date"))
        if day is None:
            census["undated"] += 1
        elif day < since:
            census["older"] += 1
        else:
            kept.append(item)
    return kept, census

def main(argv: list[str] | None = None) -> int:
    """Top-level entry point; wraps _main_inner() with a catch-all guard.

    Any exception that escapes _main_inner() is caught here, scrubbed of any
    API key value (best-effort; key may not be in scope), emitted as a JSON
    internal-error envelope on stdout, and the function returns 1.  A raw
    Python traceback is NEVER allowed to reach the caller's stdout.
    """
    # Attempt to pre-load the key for scrubbing in the catch block.  This is
    # best-effort: if crux_env itself blows up we still emit a safe envelope.
    try:
        _key_for_scrub: str = crux_env.get(_KEY_NAME) or ""
    except Exception:
        _key_for_scrub = ""

    try:
        return _main_inner(argv)
    except SystemExit:
        # argparse calls sys.exit() on --help or bad flags; let those propagate.
        raise
    except Exception as exc:
        detail_raw = str(exc)
        detail_safe = _scrub(detail_raw, _key_for_scrub)[:500]
        print(
            json.dumps({"error": "internal", "detail": detail_safe}),
            flush=True,
        )
        print("read-news: unexpected internal error (detail scrubbed)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
