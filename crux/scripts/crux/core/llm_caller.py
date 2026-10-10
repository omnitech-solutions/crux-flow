#!/usr/bin/env python3
"""
llm_caller.py - Generic LLM Calling Infrastructure

One OpenAI-compatible gateway client. Every model in the router registry
(`_config/llm_router_config.json`) resolves through OpenRouter, addressed by
its verbatim `openrouter` id and authenticated by one `OPENROUTER_API_KEY`.
The per-provider call paths and the `Provider` distinction are retired
(ADR-0087).

The API key is resolved via `crux_env` (~/.crux/env) — not from manual
dotenv loading, not from process env directly.
"""

import json
import re
import types
import httpx
from functools import lru_cache
from pathlib import Path
from typing import Optional, Dict, Any, List, NamedTuple, Tuple, Union
from dataclasses import dataclass

from crux_env import require

# Router config lives inside the package so it's installed alongside the code.
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = _PACKAGE_ROOT / "_config" / "llm_router_config.json"

# Fallback request timeout when the registry pins none. Matches the value the
# retired per-provider paths used.
DEFAULT_TIMEOUT_SECONDS = 300.0


class ModelRefusedError(RuntimeError):
    """A provider's safety classifier declined the request.

    Distinct from every other failure: the API returned HTTP 200 and behaved
    correctly — there is nothing to retry, no credential to fix, and no bug to
    report. The operator action is to change the prompt/content or route to a
    fallback model, which is why this earns its own redaction label rather than
    collapsing into "provider" or "malformed-response".

    Lives here (the lowest layer that can detect a refusal) rather than in the
    council, because `crux.council` imports `crux.core` — the reverse would be
    circular. Both the sync and async council seats map it to the closed-
    vocabulary label "refused" via `AsyncCouncil._ERROR_LABELS`.
    """


class GatewayError(RuntimeError):
    """Base for every failure the OpenRouter gateway reports by HTTP status.

    Carries `status_code` as an exact built-in `int` so the council's
    `_redact_error` can surface it without touching the response body — the
    label vocabulary stays closed and no upstream text is copied.
    """

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class GatewayInsufficientCreditError(GatewayError):
    """HTTP 402 — the account or key has insufficient credits. NOT retryable.

    Retrying spends nothing and fixes nothing; the operator action is to add
    credits (or raise the key-level spend limit), which is why this is held
    apart from the retryable upstream failures below.
    """


class GatewayUpstreamError(GatewayError):
    """HTTP 5xx (502 provider_unavailable / 503 provider_overloaded / 529
    overloaded, and the rest of the 5xx range) — a server-side fault. Retryable
    after a delay; nothing about the request needs to change."""


class GatewayTimeoutError(GatewayError):
    """HTTP 408 / 524 — a read deadline expired. Retryable, ideally with a
    longer deadline: nothing about the request needs to change."""


class GatewayAuthError(GatewayError):
    """HTTP 401 / 403 — the key is missing, wrong, or not entitled to the model.

    NOT retryable, and NOT a malformed request: the operator action is to fix
    the credential. Held apart from `GatewayClientError` for that reason — a
    rejected key read as "your request is wrong" sends the operator to the
    payload instead of to `crux-env`.
    """


class GatewayRateLimitError(GatewayError):
    """HTTP 429 — throttled. Retryable after a delay; the request is fine."""


class GatewayClientError(GatewayError):
    """The remaining 4xx — the request itself is wrong (bad model id, malformed
    payload, unsupported param). Retrying verbatim reproduces it exactly.

    The catch-all of the 4xx range, which is why the statuses with their own
    operator action — 401/403, 408, 429, 402 — are matched BEFORE it.
    """


class GatewayUnexpectedStatusError(GatewayError):
    """A non-2xx status outside 400-599 — a 3xx or a 1xx.

    The client sends no `follow_redirects`, so httpx hands a 3xx back as a
    response rather than following it, and a 1xx informational can arrive the
    same way. Neither is 4xx or 5xx, so both used to fall past every branch to
    `response.raise_for_status()` and reach the operator as `unknown` — the same
    least-actionable label the typed classes above exist to eliminate, surviving
    in the one range nobody looked at.

    Not retryable as-is: the gateway answered something the client is not
    configured to handle, so the operator action is to report the status, not to
    change the payload or the credential.
    """


class ConfidenceRejectedError(ValueError):
    """Model-output confidence failed ingest validation.

    Raised by `validated_confidence` when the value is a bool (bools are
    ints — `float(True)` is 1.0, so without a dedicated check a JSON `true`
    sailed through as full confidence), non-numeric, or outside [0, 1].
    Subclasses ValueError so the sync council's parse fallback
    (`except (json.JSONDecodeError, ValueError)` in `get_opinion`) degrades
    the whole opinion to its 0.5 fallback unchanged; the async council's
    closed-vocabulary redaction maps the class to `malformed-response` via
    `AsyncCouncil._ERROR_LABELS` (a class row on an existing label — the
    vocabulary stays closed).
    """


class ResponseTruncatedError(RuntimeError):
    """A reply that stopped on its output budget (finish reason `length`).

    Raised by the council seat, never by `parse_gateway_response`: the shared
    parse returns the text a synchronous caller asked for, and only the council
    acts on the finish reason. Not a `ValueError`, so it can never take the
    `client-config` row that class carries in `AsyncCouncil._ERROR_LABELS`.
    """


class MalformedResponseError(RuntimeError):
    """A reply that arrived but cannot become a vote.

    Raised by a council seat when the reply holds no parseable JSON object, or
    when the parsed object cannot be built into a vote. Also raised by
    `parse_gateway_reply`, on the path every caller shares (the council seat,
    `call_gateway`, and `transcribe-video` through it), when the reply's
    `message` is not an object or its `content` is not text. The message is
    a fixed string: no part of the reply travels with the error. Not a
    `ValueError`, so the `client-config` row of a real configuration error
    stays for those alone.
    """


class NotATextModelError(ValueError):
    """A registry entry whose `type` is not a text type reached the text path.

    The chat-completions path returns only `message.content`. An image entry
    therefore answers empty (image-only output) or loses its image (image and
    text output), and nothing tells the caller. Refusing by type turns that
    silent loss into a named error. No image-response contract exists.
    """


# Registry `type` values the chat-completions text path serves. Every text
# model in the registry carries `text`; the image entries carry
# `image_generation` / `image_generation_and_editing`, and `decisions` is
# reachable only through its own route. Anything else is refused.
TEXT_MODEL_TYPES = frozenset({"text"})


@dataclass
class ModelConfig:
    """Configuration for a specific model, addressed through the gateway."""
    api_string: str
    display_name: str
    context_window: int
    max_output_tokens: int
    base_url: str
    auth_header: str
    auth_prefix: str
    effort: Optional[str] = None  # reasoning effort pinned by the registry entry (top-level reasoning_effort)
    supports_temperature: bool = True  # several current models reject the temperature param
    # OpenRouter serving-provider allowlist for this entry (provider.only). Empty
    # means "let the gateway route"; the council seats pin one host each so the
    # three seats resolve to three distinct serving providers.
    serving_providers: Tuple[str, ...] = ()
    # OpenRouter's per-request `provider.data_collection`. Defaults to "deny" so
    # the retention control is a property of EVERY request, not only of the
    # entries that also pin a serving-provider set — it used to ride along inside
    # the `provider` object that a pinned entry alone emitted, which left every
    # unpinned entry routing with data collection allowed.
    #
    # The registry key is optional and exists as one escape hatch: a model served
    # only by endpoints that decline zero-retention is unroutable under "deny",
    # and pinning "allow" on that single entry is the deliberate, reviewable way
    # to say so. Absent key means deny.
    data_collection: str = "deny"
    # Registry key and `type`. A config built directly (tests, fixtures) is a
    # text config; one loaded from the registry carries the entry's own type,
    # and an entry with no type is refused on the text path rather than assumed.
    name: str = ""
    model_type: Optional[str] = "text"
    # Served-model strings a gate council accepts from the gateway's reply for
    # this entry. None: the entry declares no set, so a reply cannot be verified.
    # An empty registry list is no declared set either, and loads as None.
    accepted_served_models: Optional[Tuple[str, ...]] = None
    # Served-provider labels a gate council accepts from the reply's top-level
    # `provider` for this entry: the gateway's display labels ("Google AI
    # Studio"), not the `serving_providers` slugs the request pins. None: the
    # entry declares no set. An empty registry list loads as None.
    accepted_served_providers: Optional[Tuple[str, ...]] = None


# The only two values OpenRouter's `provider.data_collection` accepts. Anything
# else is refused at load time rather than forwarded — see `get_model_config`.
DATA_COLLECTION_VALUES = frozenset({"deny", "allow"})


def load_router_config() -> Dict[str, Any]:
    """Load the LLM router configuration."""
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)


def get_model_config(model_name: str, *, registry: Optional[Dict[str, Any]] = None) -> ModelConfig:
    """Get configuration for a specific model.

    The `data_collection` override is validated here rather than trusted,
    because it is the retention control and it fails open. The value is
    forwarded to the gateway verbatim; an unrecognized one — `"Deny"`,
    `"denied"`, `"none"` — is not honoured, so the zero-retention denial is
    silently dropped and the request routes with data collection allowed. In
    the registry file a misspelled denial still reads as a denial, which is
    what makes it worth an exception rather than a fallback to the default.
    """
    config = registry if registry is not None else load_router_config()

    if model_name not in config['models']:
        raise ValueError(f"Unknown model: {model_name}. Available: {list(config['models'].keys())}")

    model = config['models'][model_name]
    provider_config = config['providers'][model['provider']]

    data_collection = model.get('data_collection', 'deny')
    if data_collection not in DATA_COLLECTION_VALUES:
        raise ValueError(
            f"model {model_name!r} sets data_collection={data_collection!r}, which is "
            f"not one of {sorted(DATA_COLLECTION_VALUES)}. This key is the retention "
            "control: its value is sent to the gateway verbatim, so an unrecognized "
            "one is ignored there and the request routes with data collection "
            "allowed. Fix the value; use 'allow' only as the deliberate per-entry "
            "escape hatch for a model no zero-retention endpoint serves."
        )

    accepted = _string_set(model, model_name, 'accepted_served_models')
    accepted_providers = _string_set(model, model_name, 'accepted_served_providers')

    return ModelConfig(
        api_string=model['api_string'],
        display_name=model['display_name'],
        # Fallbacks for a registry entry that omits these: 128000 in / 8000 out
        # are the conservative floor every current gateway model meets or
        # exceeds, so an under-declared entry budgets safely rather than over-asks.
        context_window=model.get('context_window', 128000),
        max_output_tokens=model.get('max_output_tokens', 8000),
        base_url=provider_config['base_url'],
        auth_header=provider_config['auth_header'],
        auth_prefix=provider_config['auth_prefix'],
        effort=model.get('effort'),
        supports_temperature=model.get('supports_temperature', True),
        serving_providers=tuple(model.get('serving_providers', ())),
        data_collection=data_collection,
        name=model_name,
        model_type=model.get('type'),
        accepted_served_models=accepted,
        accepted_served_providers=accepted_providers,
    )


def _string_set(model: Dict[str, Any], model_name: str, field: str) -> Optional[Tuple[str, ...]]:
    """The entry's `field` as a tuple of strings, or None when it declares none.

    An absent key and an empty list both declare no set: an entry declaring no
    set is unverified, and an empty list declares nothing. Anything other than a
    list of strings raises `ValueError`.
    """
    value = model.get(field)
    if value is not None and (
        not isinstance(value, list) or not all(type(a) is str for a in value)
    ):
        raise ValueError(
            f"model {model_name!r} sets {field} to something other than a list of strings"
        )
    return tuple(value) if value else None


class Eligibility(NamedTuple):
    """Whether a registry key may seat a gate council, and why not."""

    eligible: bool
    reason: str


@lru_cache(maxsize=1)
def retired_models() -> frozenset:
    """The tombstoned model keys: removed from the registry, never aliased.

    An absent list is the empty set. A list that holds a non-string raises
    `ValueError`. Cleared by `invalidate_model_cache`.
    """
    raw = load_router_config().get('retired_models', [])
    if not isinstance(raw, list) or not all(type(k) is str for k in raw):
        raise ValueError("retired_models must be a list of strings")
    return frozenset(raw)


def provider_namespace(api_string: str) -> str:
    """The vendor namespace of an api_string: the part before the first `/`.

    Raises `ValueError` when there is no `/` or the part is empty.
    """
    head, sep, _ = api_string.partition("/")
    if not sep or not head:
        raise ValueError("api_string has no provider namespace")
    return head


def council_eligibility(key: str) -> Eligibility:
    """Whether registry key `key` may seat a gate council.

    Reads the raw registry. Order matters: a tombstoned key is `retired` by name
    even when it is absent from `models`, and is never aliased to a successor.
    """
    if key in retired_models():
        return Eligibility(False, "retired")
    entry = load_router_config().get('models', {}).get(key)
    if entry is None:
        return Eligibility(False, "unknown")
    if not isinstance(entry, dict):
        return Eligibility(False, "malformed-entry")
    kind = entry.get('type')
    if type(kind) is not str:
        return Eligibility(False, "malformed-entry")
    if kind not in TEXT_MODEL_TYPES:
        return Eligibility(False, "not-text")
    pin = entry.get('serving_providers')
    if not pin:
        return Eligibility(False, "no-serving-pin")
    if not isinstance(pin, list) or not all(type(p) is str for p in pin):
        return Eligibility(False, "malformed-serving-pin")
    # An empty list declares no set, the same as an absent key: an entry
    # declaring no set is unverified.
    accepted = entry.get('accepted_served_models')
    if accepted is None or (isinstance(accepted, list) and not accepted):
        return Eligibility(False, "no-accepted-set")
    if not isinstance(accepted, list) or not all(type(a) is str for a in accepted):
        return Eligibility(False, "malformed-accepted-set")
    providers = entry.get('accepted_served_providers')
    if providers is None or (isinstance(providers, list) and not providers):
        return Eligibility(False, "no-accepted-provider-set")
    if not isinstance(providers, list) or not all(type(a) is str for a in providers):
        return Eligibility(False, "malformed-accepted-provider-set")
    # Eligible means the seat can load: any other malformed field (a bad
    # data_collection, a missing provider) would otherwise error the seat after
    # the others had spent.
    try:
        get_model_config(key)
    except (ValueError, KeyError, TypeError, AttributeError):
        return Eligibility(False, "malformed-entry")
    # The gate also reads the vendor namespace of the api_string, so a seat whose
    # api_string is not a string with one is not eligible.
    api_string = entry.get('api_string')
    try:
        if type(api_string) is not str:
            raise ValueError("api_string is not a string")
        provider_namespace(api_string)
    except ValueError:
        return Eligibility(False, "malformed-entry")
    return Eligibility(True, "ok")


@lru_cache(maxsize=1)
def gateway_timeout_seconds() -> float:
    """Request timeout for the sync gateway client, read from the registry.

    The registry pins no timeout today, so this is the documented fallback
    rather than a hardcoded literal at the call site.

    Cached like `_load_model_roles`, and cleared by the same
    `invalidate_model_cache`: without it every `call_gateway` re-read and
    re-parsed the whole ~730-line registry to recover one scalar.
    """
    value = load_router_config().get('request_timeout_seconds')
    return float(value) if value is not None else DEFAULT_TIMEOUT_SECONDS


def provider_object(cfg: ModelConfig) -> Dict[str, Any]:
    """Build the gateway's per-request `provider` object for one registry entry.

    ONE implementation, for the same reason `build_gateway_request` is one: the
    retention control lives here, and a second copy is how a route comes to omit
    it. The `provider` object is emitted on EVERY request, because the retention
    control it carries applies to every request; only the `only` pin is
    conditional, so an entry with no serving-provider set still denies data
    collection and simply lets the gateway choose the host.

    Hoisted out of `build_gateway_request` so a non-chat-completions route can
    send the identical denial without rebuilding it. That route is the reason
    this is a function rather than four lines inline: the denial previously rode
    inside a conditional, and every entry without a host pin routed with data
    collection allowed.
    """
    provider: Dict[str, Any] = {"data_collection": cfg.data_collection}
    if cfg.serving_providers:
        provider["only"] = list(cfg.serving_providers)
    return provider


def build_gateway_request(
    cfg: ModelConfig,
    prompt: Union[str, List[Dict[str, Any]]],
    system: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = 0.7,
    response_format: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    """Build the (url, headers, payload) triple for one gateway request.

    ONE request shape. The sync router and the async council both build their
    requests here, so a change to the wire format cannot land on one path and
    miss the other — which is the failure the pre-gateway code had, where the
    council reached providers through SDKs the router never touched.

    `prompt` is either the user text or an already-assembled list of
    OpenAI-compatible content parts (the vision seats pass the latter).
    `temperature` is emitted only when the registry entry accepts it; `effort`
    rides the gateway's top-level `reasoning_effort`. Every request carries a
    `provider` object pinning `data_collection` (the entry's value, "deny" unless
    the registry overrides it); `serving_providers`, when set, adds the
    `provider.only` host pin beside it.

    Every text caller builds its request here, so this is where an entry whose
    registry `type` is not in `TEXT_MODEL_TYPES` is refused, before the
    credential is read: `NotATextModelError`.
    """
    if cfg.model_type not in TEXT_MODEL_TYPES:
        raise NotATextModelError(
            f"model {cfg.name or cfg.api_string!r} has type {cfg.model_type!r}, "
            f"which is not a text type {sorted(TEXT_MODEL_TYPES)}. The text "
            "callers return only the reply text, so this entry would answer "
            "empty or lose its output; image generation has no crux caller."
        )

    (api_key,) = require("OPENROUTER_API_KEY")

    url = f"{cfg.base_url.rstrip('/')}/chat/completions"
    headers = {
        # Key travels in the header, never the URL: httpx error text embeds the
        # full URL (query string included), and council consumers persist those
        # error strings — a query-string key would leak into logs on any non-2xx.
        cfg.auth_header: f"{cfg.auth_prefix}{api_key}",
        "Content-Type": "application/json",
    }

    messages: List[Dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload: Dict[str, Any] = {
        "model": cfg.api_string,
        "messages": messages,
        "max_tokens": max_tokens if max_tokens is not None else cfg.max_output_tokens,
    }

    if cfg.supports_temperature and temperature is not None:
        payload["temperature"] = temperature

    if cfg.effort:
        payload["reasoning_effort"] = cfg.effort

    payload["provider"] = provider_object(cfg)

    if response_format is not None:
        payload["response_format"] = response_format

    return url, headers, payload


def raise_for_gateway_status(response: "httpx.Response") -> None:
    """Map a gateway HTTP status onto the typed gateway exceptions.

    EVERY error status maps to a `GatewayError` subclass; nothing is delegated
    to `response.raise_for_status()`. That delegation was the bug: the
    `httpx.HTTPStatusError` it raises appears in no `AsyncCouncil._ERROR_LABELS`
    key, so a rejected credential (401) and a throttle (429) both reached the
    operator as `unknown` — the least actionable label, on two of the most
    actionable failures.

    Each class earns its place by a DISTINCT operator action, and the check
    order below is that partition, most specific first:

    ==========  ==========================  ================================
    status      class                       operator action
    ==========  ==========================  ================================
    402         InsufficientCredit          add credit; do NOT retry
    408, 524    Timeout                     retry with a longer deadline
    429         RateLimit                   back off, then retry
    401, 403    Auth                        fix the credential/entitlement
    5xx         Upstream                    wait; the fault is server-side
    other 4xx   Client                      fix the request
    other non-2xx  UnexpectedStatus         report the status (3xx / 1xx)
    ==========  ==========================  ================================

    402 and 429 are matched before the generic 4xx because retrying a credit
    exhaustion is futile while retrying a throttle is the correct response —
    opposite actions that a shared label would erase.

    The last row closes the range the table used to leave open. With every
    non-2xx typed, `response.raise_for_status()` below is unreachable for any
    error status; it stays as the assertion of that, not as a live branch.
    """
    status = response.status_code
    if status == 402:
        raise GatewayInsufficientCreditError(
            "gateway reported insufficient credit (HTTP 402)", status
        )
    if status in (408, 524):
        raise GatewayTimeoutError(f"gateway request timed out (HTTP {status})", status)
    if status == 429:
        raise GatewayRateLimitError(f"gateway rate-limited the request (HTTP {status})", status)
    if status in (401, 403):
        raise GatewayAuthError(
            f"gateway rejected the credential (HTTP {status})", status
        )
    if 500 <= status <= 599:
        raise GatewayUpstreamError(
            f"gateway upstream failure (HTTP {status})", status
        )
    if 400 <= status <= 499:
        raise GatewayClientError(
            f"gateway rejected the request (HTTP {status})", status
        )
    if not 200 <= status <= 299:
        raise GatewayUnexpectedStatusError(
            f"gateway returned an unexpected status (HTTP {status})", status
        )
    response.raise_for_status()


def validated_confidence(raw, default: float = 0.5) -> float:
    """Coerce model-output confidence to a float in [0, 1], or raise.

    The one definition of the ingest gate, shared by the sync and async
    councils: model output is untrusted, so a hostile 99.0 flipped
    AUTO_EXECUTE on the mean, a string like "high" raised TypeError later
    out of aggregation, crashing the deliberation, and a JSON `true`/`false`
    coerced through `float()` to 1.0/0.0, because a bool is an int. All three
    now raise `ConfidenceRejectedError`. The async seat wrapper's
    closed-vocabulary redaction maps that class to `malformed-response`, so
    the value degrades to an error vote; the sync council's parse fallback
    catches the ValueError subclass and degrades the whole opinion to its 0.5
    fallback. Never a verdict, never a traceback.

    Ordering is load-bearing: the `raw is None` default substitution stays
    first, and the bool rejection must precede the float coercion.
    """
    if raw is None and default is not None:
        raw = default
    if isinstance(raw, bool):
        raise ConfidenceRejectedError(f"confidence is a bool, not a number: {raw!r}")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ConfidenceRejectedError(f"confidence is not numeric: {raw!r}")
    if not 0.0 <= value <= 1.0:
        raise ConfidenceRejectedError(f"confidence out of range [0, 1]: {value!r}")
    return value


# Back-compat alias: the councils imported this under its private spelling
# before the public alias existed. Import `validated_confidence`.
_validated_confidence = validated_confidence


class GatewayReply(NamedTuple):
    """One parsed reply: its text, its raw finish reason, and whether it is a refusal.

    `finish_reason` is the gateway's value, untouched: None when the reply names
    none. It is untrusted text, and the council maps it onto a closed vocabulary
    before it reaches a vote.
    """

    content: str
    finish_reason: Any
    refused: bool
    # What the gateway reports it served: the top-level `model`, `provider` and
    # `id` of the response body. Each is None unless it is a 1-200 character
    # printable-ASCII string. The reply's own text, never the request's.
    served_model: Optional[str] = None
    served_provider: Optional[str] = None
    generation_id: Optional[str] = None


class ServedModelMismatchError(RuntimeError):
    """The gateway reported a served model the registry entry does not accept.

    Not a `ValueError`, so it never takes the `client-config` label. The message
    is fixed: no part of the reply travels with it.
    """


class ServedProviderMismatchError(RuntimeError):
    """The gateway reported a served provider the registry entry does not accept.

    A separate class from `ServedModelMismatchError`, so the council labels it
    `served-provider-mismatch`, a label of its own. Not a `ValueError`. The
    message is fixed: no part of the reply travels with it.
    """


_PROVENANCE_VALUE = re.compile(r"[\x20-\x7e]{1,200}")


def _provenance(data: Dict[str, Any], key: str) -> Optional[str]:
    value = data.get(key)
    if type(value) is str and _PROVENANCE_VALUE.fullmatch(value):
        return value
    return None


def reply_provenance(data: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """The `(served_model, served_provider, generation_id)` a response body reports.

    The ONE reader of those three fields: `parse_gateway_reply` returns them, and
    the async council reads them before it parses the reply content, so a body
    whose content is malformed still has its served model checked. Each value is
    None unless it is a 1-200 character printable-ASCII string.
    """
    return (_provenance(data, 'model'), _provenance(data, 'provider'),
            _provenance(data, 'id'))


def check_served(cfg: ModelConfig, reply: "GatewayReply") -> None:
    """Raise `ServedModelMismatchError` unless the reply's served model is accepted.

    Exact string equality against the entry's declared set: no case folding, no
    prefix or date-suffix matching, no normalization. An empty set declares
    nothing, so it is refused like an absent one. The served provider is checked
    separately, by `check_served_provider`, after this check.
    """
    if not cfg.accepted_served_models:
        raise ServedModelMismatchError("registry entry declares no accepted served models")
    if reply.served_model is None:
        raise ServedModelMismatchError("reply reports no served model")
    if reply.served_model not in cfg.accepted_served_models:
        raise ServedModelMismatchError("reply served a model outside the accepted set")


def check_served_provider(cfg: ModelConfig, reply: "GatewayReply") -> None:
    """Raise `ServedProviderMismatchError` unless the reply's served provider is accepted.

    The served provider is the reply's top-level `provider` field. It matches
    only when it exactly equals a member of the entry's `accepted_served_providers`:
    no case folding, no normalization, and no mapping from the `serving_providers`
    slugs. An absent or empty set is refused, as is an absent provider.
    """
    if not cfg.accepted_served_providers:
        raise ServedProviderMismatchError("registry entry declares no accepted served providers")
    if reply.served_provider is None:
        raise ServedProviderMismatchError("reply reports no served provider")
    if reply.served_provider not in cfg.accepted_served_providers:
        raise ServedProviderMismatchError("reply served a provider outside the accepted set")


def parse_gateway_reply(data: Dict[str, Any], model: str) -> GatewayReply:
    """Read the assistant text and the finish reason from a response body.

    Reports a refusal or a `length` finish and leaves the decision to the
    caller. A refusal arrives as HTTP 200 with `finish_reason:
    "content_filter"` (the documented ChatFinishReasonEnum member) or a
    passed-through `native_finish_reason: "refusal"`.

    Raises `MalformedResponseError` when `message` is not an object or its
    `content` is not text. A `choices[0]` that is not an object, or a body with
    no choices, yields empty text.
    """
    _ = model  # part of the signature the sync and async callers share; not read here
    # Read before every early return: a reply with no choices still says what
    # it was served by.
    prov = reply_provenance(data)
    choices = data.get('choices') or []
    if not choices:
        return GatewayReply("", None, False, *prov)

    choice = choices[0]
    if not isinstance(choice, dict):
        # A malformed upstream body (choices[0] is not an object) would raise an
        # uncaught AttributeError on `.get` below. transcribe-video's main() arm
        # does not catch AttributeError, so treat a non-dict choice as no
        # content rather than crash the caller.
        return GatewayReply("", None, False, *prov)
    finish_reason = choice.get('finish_reason')
    refused = finish_reason == 'content_filter' or \
        choice.get('native_finish_reason') == 'refusal'
    message = choice.get('message')
    if message is None:
        message = {}
    if not isinstance(message, dict):
        # The finish reason is not recorded: the reply is unusable, and the
        # raise carries a fixed message, so no byte of the reply travels with it.
        raise MalformedResponseError("reply message is not an object")
    content = message.get('content') or ""
    if not isinstance(content, str):
        raise MalformedResponseError("reply content is not text")
    return GatewayReply(content, finish_reason, refused, *prov)


def parse_gateway_response(data: Dict[str, Any], model: str) -> str:
    """Extract the assistant text from an OpenAI-compatible response body.

    A refusal would otherwise return an empty string, so this raises
    `ModelRefusedError` for it. The sync callers (`call_gateway`) use this
    function. The async council calls `parse_gateway_reply` instead, because it
    also needs the finish reason. A `length` finish is returned as text.
    """
    reply = parse_gateway_reply(data, model)
    if reply.refused:
        raise ModelRefusedError(f"model {model} declined to answer (refusal)")
    return reply.content


def call_gateway(
    cfg: ModelConfig,
    prompt: Union[str, List[Dict[str, Any]]],
    system: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = 0.7,
    response_format: Optional[Dict[str, Any]] = None,
    timeout: Optional[float] = None,
) -> str:
    """Perform one synchronous gateway call and return the response text."""
    url, headers, payload = build_gateway_request(
        cfg, prompt, system=system, max_tokens=max_tokens,
        temperature=temperature, response_format=response_format,
    )

    with httpx.Client(timeout=timeout if timeout is not None else gateway_timeout_seconds()) as client:
        response = client.post(url, headers=headers, json=payload)
        raise_for_gateway_status(response)
        return parse_gateway_response(response.json(), cfg.api_string)


def call_model(
    model: str,
    prompt: str,
    system: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: float = 0.7,
) -> str:
    """
    Universal model caller — routes every model through the one gateway.

    Args:
        model: Model name from router config
        prompt: The user prompt
        system: Optional system prompt
        max_tokens: Maximum output tokens (uses model default if not specified)
        temperature: Sampling temperature (0-1); omitted for models that reject it

    Returns:
        The model's response text
    """
    cfg = get_model_config(model)
    return call_gateway(
        cfg,
        prompt,
        system=system,
        max_tokens=max_tokens if max_tokens is not None else cfg.max_output_tokens,
        temperature=temperature,
    )


def list_available_models() -> List[str]:
    """List all available models from the router config."""
    config = load_router_config()
    return list(config['models'].keys())


def get_model_info(model: str) -> Dict[str, Any]:
    """Get detailed info about a model."""
    config = load_router_config()
    if model in config['models']:
        return config['models'][model]
    raise ValueError(f"Unknown model: {model}")


@lru_cache(maxsize=1)
def _load_model_roles() -> types.MappingProxyType:
    """Load and validate model_roles from the router config. Cached and immutable."""
    config = load_router_config()
    roles = config.get("model_roles", {})
    models = config.get("models", {})
    for role, value in roles.items():
        if role.startswith("_"):
            continue
        targets = value if isinstance(value, list) else [value]
        for t in targets:
            if t not in models:
                raise ValueError(f"model_roles['{role}'] references unknown model '{t}'")
    return types.MappingProxyType(roles)


def get_default_model(role: str) -> str:
    """Get the default model name for a role. Raises TypeError if the role maps to a list."""
    roles = _load_model_roles()
    if role not in roles:
        raise ValueError(f"Unknown model role: {role}. Available: {[k for k in roles if not k.startswith('_')]}")
    value = roles[role]
    if isinstance(value, list):
        raise TypeError(f"Role '{role}' is a list (use get_default_models()). Models: {value}")
    return value


def get_default_models(role: str) -> List[str]:
    """Get a list of models for a role. Wraps single strings into [string]."""
    roles = _load_model_roles()
    if role not in roles:
        raise ValueError(f"Unknown model role: {role}. Available: {[k for k in roles if not k.startswith('_')]}")
    value = roles[role]
    if isinstance(value, str):
        return [value]
    return list(value)


def invalidate_model_cache():
    """Clear every registry-derived cache — model roles and the gateway timeout.

    Call after modifying the router config at runtime; every cache reads the same
    file, so clearing one and not the others would leave them out of step.
    """
    _load_model_roles.cache_clear()
    gateway_timeout_seconds.cache_clear()
    retired_models.cache_clear()


# Convenience functions - all resolve from model_roles in llm_router_config.json
def call_gemini_pro(prompt: str, system: Optional[str] = None) -> str:
    """Call the model the google_top role resolves to."""
    return call_model(get_default_model("google_top"), prompt, system)


def call_gemini_flash(prompt: str, system: Optional[str] = None) -> str:
    """Call the model the google_fast role resolves to."""
    return call_model(get_default_model("google_fast"), prompt, system)


def call_anthropic_top(prompt: str, system: Optional[str] = None) -> str:
    """Call the model assigned to the anthropic_top role."""
    return call_model(get_default_model("anthropic_top"), prompt, system)


def call_anthropic_balanced(prompt: str, system: Optional[str] = None) -> str:
    """Call the model assigned to the anthropic_balanced role."""
    return call_model(get_default_model("anthropic_balanced"), prompt, system)


def call_claude_opus(prompt: str, system: Optional[str] = None) -> str:
    """Deprecated public compatibility alias for call_anthropic_top."""
    return call_anthropic_top(prompt, system)


def call_claude_sonnet(prompt: str, system: Optional[str] = None) -> str:
    """Deprecated public compatibility alias for call_anthropic_balanced."""
    return call_anthropic_balanced(prompt, system)


__all__ = [
    "ModelConfig",
    "ModelRefusedError",
    "ResponseTruncatedError",
    "MalformedResponseError",
    "GatewayError",
    "NotATextModelError",
    "TEXT_MODEL_TYPES",
    "GatewayInsufficientCreditError",
    "GatewayUpstreamError",
    "GatewayTimeoutError",
    "GatewayAuthError",
    "GatewayRateLimitError",
    "GatewayClientError",
    "GatewayUnexpectedStatusError",
    "ConfidenceRejectedError",
    "validated_confidence",
    "build_gateway_request",
    "provider_object",
    "raise_for_gateway_status",
    "GatewayReply",
    "ServedModelMismatchError",
    "ServedProviderMismatchError",
    "check_served",
    "check_served_provider",
    "reply_provenance",
    "Eligibility",
    "retired_models",
    "provider_namespace",
    "council_eligibility",
    "parse_gateway_reply",
    "parse_gateway_response",
    "call_gateway",
    "gateway_timeout_seconds",
    "call_model",
    "call_gemini_pro",
    "call_gemini_flash",
    "call_claude_opus",
    "call_claude_sonnet",
    "call_anthropic_top",
    "call_anthropic_balanced",
    "list_available_models",
    "get_model_config",
    "get_model_info",
    "get_default_model",
    "get_default_models",
    "invalidate_model_cache",
]
