#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27"]
# ///
"""
async_council.py - Parallel LLM Council Execution

Runs all council seats (OpenAI, Anthropic, Gemini) in PARALLEL using asyncio.

Instead of:
  OpenAI (15s) -> Anthropic (15s) -> Gemini (15s) = 45s TOTAL

We get:
  OpenAI -+
  Anthropic +-- All finish in ~15s (the slowest one)
  Gemini  -+

This 3x speedup is critical for responsive RSI.

Implementation notes:
- Every seat goes through the one OpenRouter gateway over `httpx.AsyncClient`,
  using the shared request builder in `crux.core.llm_caller`, so the council and
  the sync router emit ONE request shape (ADR-0087). The three provider SDKs are
  gone, and with them their PEP 723 dependencies.
- The one gateway key comes from `crux_env` (~/.crux/env), not raw os.environ.
  It is read with `get`, not `require`: a missing key must mean zero seats and a
  fail-closed NO_QUORUM -> DEFER_TO_HUMAN, never a crash.
- Seat identity stays the VENDOR NAMESPACE ("openai" / "anthropic" / "gemini"),
  not the gateway. It is what error votes and aggregation key on, and it is the
  diversity the seats exist to buy — the serving host each seat pins is the
  registry's `serving_providers`.
- Tenacity is intentionally NOT a dependency. The seat wrapper retries a seat
  once itself (`_run_seat`), under a fresh deadline per attempt. Failures still
  surface as errored votes thanks to the per-seat try/except wrappers.
"""

import asyncio
import hashlib
import json
import logging
import math
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Defensive: make `crux_env` and the bundled package importable when this
# file is run as a plain script (`uv run …/scripts/crux/council/async_council.py`)
# rather than imported as `crux.council.async_council`. The package root is
# `scripts/` — two levels up — the *sibling* of the entry scripts (ADR-0035
# §2). Survives -P / PYTHONSAFEPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx

from crux_env import get
from untrusted import redact

# Absolute imports (not `from ..core import …`) so this file also works when
# executed as a plain script, where `__package__` is unset and relative
# imports fail. Imported as part of the package, both forms resolve to the
# same modules.
from crux.core.data_classes import (
    CouncilDeliberation,
    CouncilVote,
)
from crux.core.llm_caller import (
    GatewayReply,
    ModelConfig,
    MalformedResponseError,
    ModelRefusedError,
    ResponseTruncatedError,
    ServedModelMismatchError,
    ServedProviderMismatchError,
    validated_confidence,
    build_gateway_request,
    CONFIG_PATH,
    check_served,
    check_served_provider,
    council_eligibility,
    get_default_model,
    get_model_config,
    load_router_config,
    parse_gateway_reply,
    reply_provenance,
    provider_namespace,
    raise_for_gateway_status,
    retired_models,
)

logger = logging.getLogger(__name__)

# Shared by both seat ingest paths. Deliberately module-level: the safety
# property has to be provable on one implementation. The confidence validator
# moved to `crux.core.llm_caller` so the sync council uses the same definition.
def _extract_json_blob(content: Optional[str]) -> str:
    """Slice off the noise around the model's JSON object.

    Linear both ways: `str.find` scans once, `str.rfind` scans once. The
    previous `re.search(r"\\{[\\s\\S]*\\}", ...)` was quadratic — a repeated
    open-brace body (`"{" * 131000`, no close) made each attempted start
    position rescan to end-of-string, measured 32.98s here — and it ran
    synchronously inside the event loop, where `asyncio.wait_for` cannot
    cancel it. The semantics are identical: first ``{`` to last ``}``.
    """
    text = content or ""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in model output")
    return text[start:end + 1]


#: Per-attempt deadline in seconds. Each attempt of each seat gets this long on
#: its own, so a seat that is retried can run for twice this. One slow seat
#: becomes a `timeout`-labelled errored vote and never costs the others.
DEFAULT_SEAT_TIMEOUT_SECONDS = 600.0

#: The fault labels a seat is called a second time after. A closed set, keyed on
#: the label `_redact_error` computes and never on an HTTP status, so a change to
#: what yields a label changes what is retried.
_RETRYABLE_FAULT_LABELS = frozenset({"timeout", "provider", "malformed-response", "truncated"})

#: The finish reasons a vote may carry. A closed vocabulary: the gateway's own
#: text reaches a vote only when it is one of `stop`, `length`, `content_filter`,
#: `tool_calls` or `error`. Any other value
#: records as _FINISH_OTHER, and a reply that names none records as
#: _FINISH_MISSING. A seat that received no reply (a timeout, or an HTTP error status raised before the body is read) records None.
_FINISH_OTHER = "other"
_FINISH_MISSING = "missing"
_FINISH_REASONS = frozenset({
    "stop", "length", "content_filter", "tool_calls", "error",
    _FINISH_OTHER, _FINISH_MISSING,
})


def _finish_value(raw: object) -> str:
    """Map the gateway's finish reason onto the closed `_FINISH_REASONS` set."""
    if raw is None:
        return _FINISH_MISSING
    if isinstance(raw, str) and raw in _FINISH_REASONS - {_FINISH_OTHER, _FINISH_MISSING}:
        return raw
    return _FINISH_OTHER


class _Attempt:
    """What one seat attempt learned before it ended: the reply's finish reason
    and what the gateway reported serving.

    `replied` is False until a response body arrives and decodes. Provenance (served model,
    served provider, generation id) is set the moment the body is decoded, before
    the content is parsed or judged, so a reply whose content is malformed still
    records what served it. `finish_reason` is None until the content parse
    succeeds; it is set before a refusal or a truncation is raised, so those
    still record the finish reason that named them.

    In a gate council, `served_model_match` and `served_provider_match` hold the
    two match results for every reply that decoded, whether or not the reply
    could yield a vote. They are None when no reply decoded, and outside gate
    mode. Recording a result raises nothing: the seat wrapper enforces the two
    checks only on a reply that would otherwise yield a vote. `fault_label` is
    the label the attempt ended with, or None when it yielded the seat's result.
    """

    __slots__ = ("replied", "finish_reason", "served_model", "served_provider",
                 "generation_id", "cfg", "served_model_match", "served_provider_match",
                 "fault_label")

    def __init__(self):
        self.replied: bool = False
        self.finish_reason: Optional[str] = None
        self.served_model: Optional[str] = None
        self.served_provider: Optional[str] = None
        self.generation_id: Optional[str] = None
        self.cfg = None  # the ModelConfig the request was built from, once it was sent
        self.served_model_match: Optional[bool] = None
        self.served_provider_match: Optional[bool] = None
        self.fault_label: Optional[str] = None

    def provenance(self) -> GatewayReply:
        """The reported provenance as a reply the two checks read. No content."""
        return GatewayReply("", None, False, self.served_model, self.served_provider,
                            self.generation_id)

    def record(self, number: int) -> Dict[str, Any]:
        """This attempt as the council record carries it. `number` counts from 1."""
        return {
            "attempt": number,
            "replied": self.replied,
            "served_model": self.served_model,
            "served_provider": self.served_provider,
            "generation_id": self.generation_id,
            "finish_reason": self.finish_reason,
            "fault_label": self.fault_label,
            "served_model_match": self.served_model_match,
            "served_provider_match": self.served_provider_match,
        }


def _passes(check, cfg, reply: GatewayReply) -> bool:
    """True when `check(cfg, reply)` raises no served-model or served-provider fault."""
    try:
        check(cfg, reply)
    except (ServedModelMismatchError, ServedProviderMismatchError):
        return False
    return True


@dataclass
class _SeatRun:
    """The outcome of `_run_seat`: a value or an error, plus the retry record."""

    value: object = None
    error: Optional[Exception] = None
    finish_reason: Optional[str] = None
    retried: bool = False
    first_fault_label: Optional[str] = None
    first_finish_reason: Optional[str] = None
    # Provenance of the last attempt that got a reply; None when none did.
    served_model: Optional[str] = None
    served_provider: Optional[str] = None
    generation_id: Optional[str] = None
    # Every attempt, in order, as `_Attempt.record` renders it.
    attempts: List[Dict[str, Any]] = field(default_factory=list)

#: The decision tokens the aggregator and the prompt know. A responding seat
#: whose decision is outside this set is reported in `off_scale`.
_APPROVE_DECISIONS = ("APPROVE", "APPROVE_WITH_CONDITIONS", "APPROVE_WITH_NITS")
_KNOWN_DECISIONS = _APPROVE_DECISIONS + ("REJECT", "DEFER_TO_HUMAN")

#: Labels a log line may carry. A seat's decision is model text, and a log line is written before
#: run-council's secret scan, so a line names a constant from these tables and never the input.
#: The decision table is _KNOWN_DECISIONS plus the gate-only tokens: REQUEST_CHANGES from the gate
#: scale (_GATE_DECISIONS) and the verify/implementation ARCHITECTURAL token. Keep it in step with
#: run-council's _DECISION_TOKENS; test_council_log_labels ties all three.
_LOG_DECISIONS = {d: d for d in _KNOWN_DECISIONS + ("REQUEST_CHANGES", "ARCHITECTURAL")}
_LOG_CONSENSUS = {c: c for c in ("UNANIMOUS_APPROVE", "UNANIMOUS_REJECT", "MAJORITY_APPROVE",
                                 "MAJORITY_REJECT", "SPLIT", "NO_QUORUM", "UNANIMOUS_OFF_SCALE",
                                 "UNANIMOUS_DEFER_TO_HUMAN", "UNANIMOUS_REQUEST_CHANGES",
                                 "UNANIMOUS_ARCHITECTURAL")}
#: A consensus outside _LOG_CONSENSUS logs as UNLISTED, a label no deliberation produces. It does
#: not borrow UNANIMOUS_OFF_SCALE, which in the record means a shared token failed the echo check.
#: The deliberation result and the record keep the precise label.
_LOG_UNLISTED_CONSENSUS = "UNLISTED"
#: A vote outside _LOG_DECISIONS logs as OFF_SCALE. The table holds every _KNOWN_DECISIONS token,
#: so such a vote is also listed in the record's off_scale; the two labels agree.


def _log_label(table: Dict[str, str], value: object, fallback: str) -> str:
    """A label from `table`, else `fallback`: no string outside the table reaches a log line.

    `type(value) is str` sends a non-string decision to the fallback, because a list or dict
    cannot be a table key and would raise TypeError in `table.get`."""
    return table.get(value, fallback) if type(value) is str else fallback

#: A shared decision token is echoed into a `UNANIMOUS_<X>` consensus label.
#: UNANIMOUS_OFF_SCALE replaces it when the token is not 1-40 characters of A-Z and
#: underscore starting with a letter, contains APPROVE or REJECT, or is AUTO_EXECUTE
#: or EXECUTE_WITH_MONITORING. No label then reads as a verdict or an execute action
#: to a caller that matches it by prefix.
_LABEL_TOKEN = re.compile(r"[A-Z][A-Z_]{0,39}")
_LABEL_FORBIDDEN_SUBSTRINGS = ("APPROVE", "REJECT")
_LABEL_FORBIDDEN_TOKENS = ("AUTO_EXECUTE", "EXECUTE_WITH_MONITORING")


_FINDING_ID_MAX = 64
_FINDING_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


def _parse_gate_findings(role: str, raw: object) -> List[Dict[str, Any]]:
    """Gate-seat findings as dicts with seat-qualified ids and fail-closed tags.

    Absent findings are an empty list. A non-list, or a non-dict entry, raises
    `ValueError`. A tag that is missing or the wrong type is None, which the
    gate counts against the finding rather than for it.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("findings is not a list")
    out: List[Dict[str, Any]] = []
    seen = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError("finding is not an object")
        given = item.get("id")
        local = given if isinstance(given, str) and _FINDING_ID.fullmatch(given) else f"F{index + 1}"
        qualified, n = f"{role}:{local}", 1
        while qualified in seen:
            n += 1
            suffix = f"-{n}"
            # The record schema allows 64 characters after the role prefix.
            qualified = f"{role}:{local[:_FINDING_ID_MAX - len(suffix)]}{suffix}"
        seen.add(qualified)
        dimension, text = item.get("dimension"), item.get("text")
        safety, kind = item.get("safety_adjacent"), item.get("kind")
        out.append({
            "id": qualified,
            "dimension": dimension if isinstance(dimension, str) else None,
            "safety_adjacent": safety if type(safety) is bool else None,
            "kind": kind if kind in ("blocking", "nit") else None,
            "text": text if isinstance(text, str) else None,
        })
    return out


def _echoable_label_token(token: object) -> bool:
    """True when `token` may be echoed into a `UNANIMOUS_<X>` label."""
    return (isinstance(token, str)
            and _LABEL_TOKEN.fullmatch(token) is not None
            and not any(s in token for s in _LABEL_FORBIDDEN_SUBSTRINGS)
            and token not in _LABEL_FORBIDDEN_TOKENS)


#: The council kinds a gate council may run, and the one instruction each uses.
GATE_COUNCIL_KINDS = ("adr", "implementation", "verify", "patch")

#: Gate seats come only from these three roles, one per vendor namespace.
_GATE_ROLES = ("openai_top", "anthropic_top", "google_top")
_SEAT_ROLES = {"openai": "openai_top", "anthropic": "anthropic_top", "gemini": "google_top"}

_GATE_FINDINGS_INSTRUCTION = (
    ' "confidence": 0.0-1.0, "reasoning": "...", "findings": [{"id": "F1", '
    '"dimension": "<a dimension the question names>", "safety_adjacent": true|false, '
    '"kind": "blocking"|"nit", "text": "..."}]}. '
    'The reasoning must cover every dimension the question names. '
    'Tag every finding with its dimension, safety_adjacent and kind. '
    'Any finding on Security, or that is safety-adjacent, is blocking. '
    'The decision scale has no other token.'
)
_GATE_DECISIONS = '"APPROVE"|"APPROVE_WITH_NITS"|"REQUEST_CHANGES"|"REJECT"'
_GATE_INSTRUCTIONS = {
    "adr": ('\n\nRespond with valid JSON: {"decision": ' + _GATE_DECISIONS + ','
            + _GATE_FINDINGS_INSTRUCTION),
    "patch": ('\n\nRespond with valid JSON: {"decision": ' + _GATE_DECISIONS + ','
              + _GATE_FINDINGS_INSTRUCTION),
    "verify": ('\n\nRespond with valid JSON: {"decision": ' + _GATE_DECISIONS
               + '|"ARCHITECTURAL",' + _GATE_FINDINGS_INSTRUCTION),
    "implementation": ('\n\nAssess Completeness, Correctness, Consistency, Clarity and Security '
                       'of the selected implementation revision, including architectural conflict. '
                       'Return ARCHITECTURAL when the selected approach conflicts with architectural constraints. '
                       'Respond with valid JSON: {"decision": ' + _GATE_DECISIONS
                       + '|"ARCHITECTURAL",' + _GATE_FINDINGS_INSTRUCTION),
}


def gate_vote_instruction(kind: str) -> str:
    """The vote instruction a gate council of `kind` appends to the question.

    One text per kind, so a caller can hash exactly what the seats were told.
    Raises `ValueError` for a kind outside `GATE_COUNCIL_KINDS`.
    """
    try:
        return _GATE_INSTRUCTIONS[kind]
    except (KeyError, TypeError):
        raise ValueError(f"council kind must be one of {list(GATE_COUNCIL_KINDS)}") from None


class CouncilAssignmentRefused(Exception):
    """A gate council refused its seat assignment before any gateway call.

    `code` is one of `refused-assignment` or `fewer-than-three-providers`.
    `names` lists the role names and registry keys involved: registry data,
    never a secret and never model output.
    """

    CODES = ("refused-assignment", "fewer-than-three-providers")

    def __init__(self, code: str, names: List[str]):
        self.code = code
        self.names = list(names)
        super().__init__(f"council assignment refused: {code}: {', '.join(self.names)}")


def validate_gate_assignment() -> Dict[str, str]:
    """Resolve the three gate roles to registry keys, or refuse the council.

    Reads the raw registry, because the cached role loader raises a bare
    `ValueError` for a role that points at a missing key, and a refusal must
    name the key. Returns `{role: registry_key}` when every seat is eligible
    and the three seats sit on three distinct vendor namespaces. Raises
    `CouncilAssignmentRefused` otherwise.
    """
    config = load_router_config()
    roles = config.get("model_roles", {})
    models = config.get("models", {})
    try:
        retired = retired_models()
    except ValueError:
        # A malformed tombstone list is a malformed registry: no seat can be judged against it.
        raise CouncilAssignmentRefused("refused-assignment", ["registry"]) from None
    resolved: Dict[str, str] = {}
    namespaces: Dict[str, str] = {}
    for role in _GATE_ROLES:
        key = roles.get(role)
        if not isinstance(key, str):
            raise CouncilAssignmentRefused("refused-assignment", [role])
        if key in retired:
            raise CouncilAssignmentRefused("refused-assignment", [role, key])
        if key not in models:
            raise CouncilAssignmentRefused("refused-assignment", [role, key])
        if not council_eligibility(key).eligible:
            raise CouncilAssignmentRefused("refused-assignment", [role, key])
        try:
            namespaces[role] = provider_namespace(models[key]["api_string"])
        except (KeyError, TypeError, ValueError, AttributeError):
            raise CouncilAssignmentRefused("refused-assignment", [role, key]) from None
        resolved[role] = key
    if len(set(namespaces.values())) != len(_GATE_ROLES):
        raise CouncilAssignmentRefused("fewer-than-three-providers", list(resolved.values()))
    return resolved


@dataclass
class AsyncCouncilConfig:
    """Configuration for async council. Model fields resolve from model_roles at instantiation."""

    openai_model: Optional[str] = None
    anthropic_model: Optional[str] = None
    gemini_model: Optional[str] = None
    timeout_seconds: float = DEFAULT_SEAT_TIMEOUT_SECONDS  # per attempt: a retried seat can take twice this
    overall_timeout_seconds: Optional[float] = None  # fork-controlled call budget; None preserves upstream behavior
    overall_deadline_epoch: Optional[float] = None  # frozen run expiry; includes delay before dispatch
    expected_router_digest: Optional[str] = None  # fork run binding; None preserves upstream behavior
    max_retries: int = 1  # Extra attempts after a retryable fault: 0 disables the retry, 1 is the only other value
    resolved_models: Optional[Dict[str, ModelConfig]] = None
    max_tokens: int = 32000  # Max output tokens per council member
    # Gate mode: the council whose verdict can satisfy a council gate. Seats come
    # only from the three roles, each must be eligible, and the served model of
    # every reply is checked. `council_kind` picks the vote instruction.
    gate: bool = False
    council_kind: Optional[str] = None
    # Test and runner seam: an alternative transport for the gateway client.
    transport: Optional[httpx.AsyncBaseTransport] = None

    def __post_init__(self):
        if self.resolved_models is not None:
            if set(self.resolved_models) != {"openai", "anthropic", "gemini"} or any(
                not isinstance(value, ModelConfig) for value in self.resolved_models.values()
            ):
                raise ValueError("invalid frozen council model configurations")
        if self.expected_router_digest is not None:
            if not isinstance(self.expected_router_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", self.expected_router_digest):
                raise ValueError("invalid frozen router digest")
            if hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest() != self.expected_router_digest:
                raise ValueError("API router changed since the run policy was frozen")
        # `type(...) is int`, not isinstance: a bool is an int, and 1.0 == 1.
        if type(self.max_retries) is not int or self.max_retries not in (0, 1):
            raise ValueError("max_retries must be 0 or 1")
        if self.overall_timeout_seconds is not None and (
            isinstance(self.overall_timeout_seconds, bool)
            or not isinstance(self.overall_timeout_seconds, (int, float))
            or not math.isfinite(self.overall_timeout_seconds)
            or self.overall_timeout_seconds <= 0
        ):
            raise ValueError("overall_timeout_seconds must be a positive finite number")
        if self.overall_deadline_epoch is not None and (
            isinstance(self.overall_deadline_epoch, bool)
            or not isinstance(self.overall_deadline_epoch, (int, float))
            or not math.isfinite(self.overall_deadline_epoch)
            or self.overall_deadline_epoch <= 0
        ):
            raise ValueError("overall_deadline_epoch must be a positive finite timestamp")
        # One test for gate mode everywhere: `is True`. A truthy non-bool (1, "yes")
        # would pass `if self.gate:` here and then read as non-gate at run time.
        if type(self.gate) is not bool:
            raise ValueError("gate must be a bool")
        if self.gate and self.resolved_models is not None:
            # Fork patch: frozen model configurations belong to Flow's own council, never to a gate seat.
            raise ValueError("frozen council model configurations cannot seat a gate council")
        if self.gate:
            if self.council_kind not in GATE_COUNCIL_KINDS:
                raise ValueError(f"council_kind must be one of {list(GATE_COUNCIL_KINDS)}")
            overrides = [name for name, value in (
                ("openai_model", self.openai_model), ("anthropic_model", self.anthropic_model),
                ("gemini_model", self.gemini_model)) if value is not None]
            if overrides:
                # Gate seats come only from the roles, never from a caller's pick.
                raise CouncilAssignmentRefused("refused-assignment", overrides)
            resolved = validate_gate_assignment()
            self.openai_model = resolved["openai_top"]
            self.anthropic_model = resolved["anthropic_top"]
            self.gemini_model = resolved["google_top"]
        if self.openai_model is None:
            self.openai_model = get_default_model("openai_top")
        if self.anthropic_model is None:
            self.anthropic_model = get_default_model("anthropic_top")
        if self.gemini_model is None:
            self.gemini_model = get_default_model("google_top")


@dataclass
class VisualVoteResult:
    """Result from a visual analysis vote"""

    model_name: str
    passed: bool
    confidence: float
    observations: str
    anomalies: List[Dict[str, str]] = field(default_factory=list)
    # Seat telemetry, as on CouncilVote. `errored` is set only by the seat
    # wrapper: an errored seat also reads passed=False at confidence 0.0, so a
    # caller counting failed checks must skip errored results.
    errored: bool = False
    fault_label: Optional[str] = None
    finish_reason: Optional[str] = None
    recovered: bool = False
    retried: bool = False
    first_fault_label: Optional[str] = None
    first_finish_reason: Optional[str] = None
    served_model: Optional[str] = None
    served_provider: Optional[str] = None
    generation_id: Optional[str] = None
    attempts: List[Dict[str, Any]] = field(default_factory=list)


class AsyncCouncil:
    """
    Async LLM Council - Runs all providers in parallel.

    Features:
    - Parallel execution via asyncio.gather
    - Per-seat timeout via asyncio.wait_for (a slow seat never erases the others)
    - One async transport for every seat: httpx.AsyncClient against the gateway

    Usage:
        council = AsyncCouncil()
        result = await council.deliberate(prompt)

    Or synchronously:
        result = council.deliberate_sync(prompt)
    """

    #: Seat identity is the VENDOR NAMESPACE, not the gateway. These three keys
    #: are what `CouncilVote.provider` carries, what `_create_error_vote` labels,
    #: and what aggregation counts — none of that changed when the transport did.
    _SEAT_ORDER = ("openai", "anthropic", "gemini")
    _SEAT_LABELS = {"openai": "OpenAI", "anthropic": "Anthropic", "gemini": "Gemini"}

    _VOTE_JSON_INSTRUCTION = (
        '\n\nRespond with valid JSON: {"decision": "APPROVE"|"REJECT"|"DEFER_TO_HUMAN", '
        '"confidence": 0.0-1.0, "dissents": [{"point": "...", "severity": '
        '"critical"|"high"|"medium"|"low"}], "reasoning": "..."}'
    )

    _VISION_JSON_INSTRUCTION = (
        '\n\nRespond with JSON: {"passed": true/false, "confidence": 0.0-1.0, '
        '"observations": "...", "anomalies": [{"description": "...", '
        '"severity": "high"|"medium"|"low"}]}'
    )

    #: Gate mode as fixed at construction, never re-read from the mutable config
    #: while seats run. Class defaults serve an instance built without `__init__`.
    _gate: bool = False
    _council_kind: Optional[str] = None

    def __init__(self, config: Optional[AsyncCouncilConfig] = None):
        self.config = config or AsyncCouncilConfig()
        # The config is mutable, so its construction-time validation is not enough:
        # a seat edited, or the gate flag set, afterwards would skip it.
        self._gate = False
        if self.config.gate is not False:
            self._check_gate_config()
            self._gate = True
            self._council_kind = self.config.council_kind

        # ONE key for every seat (ADR-0087). Read with `get`, never `require`:
        # a missing key must degrade to zero seats -> the existing no-tasks path
        # -> NO_QUORUM -> DEFER_TO_HUMAN. Raising here would turn a
        # configuration gap into a crash and lose the fail-closed outcome
        # ADR-0054 exists to guarantee.
        # self.gateway_key is a PRESENCE sentinel only — it gates whether any
        # seats exist (below). The wire key that actually authenticates each
        # request is re-read inside build_gateway_request, not passed from here.
        self.gateway_key = get("OPENROUTER_API_KEY")

        self._seat_models = {
            "openai": self.config.openai_model,
            "anthropic": self.config.anthropic_model,
            "gemini": self.config.gemini_model,
        }
        # Seats exist only when the one key does. One key failing degrades every
        # seat at once — that concentration is the accepted cost of the single
        # gateway, and the below-quorum path is its control.
        self.available_providers: List[str] = (
            [s for s in self._SEAT_ORDER if self._seat_models.get(s)]
            if self.gateway_key else []
        )

        logger.info(f"AsyncCouncil: {len(self.available_providers)} seats")
        logger.info(f"     Timeout: {self.config.timeout_seconds}s, Retries: {self.config.max_retries}")
        for p in self.available_providers:
            logger.info(f"     - {p}")

    def _check_gate_config(self, seats: Optional[Dict[str, Optional[str]]] = None) -> None:
        """Refuse a gate council whose flag, kind or seats are not the roles' own.

        Re-resolves the three roles against the registry as it is now. `seats`
        maps each seat to the registry key it will use (the config's fields at
        construction, the council's own seats afterwards). Raises
        `CouncilAssignmentRefused`, or `ValueError` for a kind outside
        `GATE_COUNCIL_KINDS`, before any gateway call.
        """
        if self.config.gate is not True:
            raise CouncilAssignmentRefused("refused-assignment", ["gate"])
        if self.config.council_kind not in GATE_COUNCIL_KINDS:
            raise ValueError(f"council_kind must be one of {list(GATE_COUNCIL_KINDS)}")
        resolved = validate_gate_assignment()
        if seats is None:
            seats = {"openai": self.config.openai_model,
                     "anthropic": self.config.anthropic_model,
                     "gemini": self.config.gemini_model}
        fields = {"openai": "openai_model", "anthropic": "anthropic_model",
                  "gemini": "gemini_model"}
        moved = [fields[seat] for seat in self._SEAT_ORDER
                 if seats.get(seat) != resolved[_SEAT_ROLES[seat]]]
        if moved:
            raise CouncilAssignmentRefused("refused-assignment", moved)

    def _assert_gate_unchanged(self) -> None:
        """Run at the top of each entry point, before any task is created.

        A gate council re-validates its seats against the registry now. A council
        built outside gate mode refuses when its config has since been set to a
        gate: it never validated.
        """
        if self._gate:
            self._check_gate_config(self._seat_models)
            if self.config.council_kind != self._council_kind:
                raise CouncilAssignmentRefused("refused-assignment", ["council_kind"])
        elif self.config.gate is not False:
            raise CouncilAssignmentRefused("refused-assignment", ["gate"])

    def _seat_model(self, seat: str) -> str:
        """Registry key for a seat. Falls back to the config field so an
        instance built without __init__ (tests do this) still resolves."""
        models = getattr(self, "_seat_models", None)
        if models and models.get(seat):
            return models[seat]
        return getattr(self.config, f"{seat}_model")

    def _config_for_seat(self, seat: str) -> ModelConfig:
        pinned = getattr(self.config, "resolved_models", None)
        return pinned[seat] if pinned is not None else get_model_config(self._seat_model(seat))

    async def _post_seat(self, cfg, prompt, system=None, response_format=None,
                         attempt: Optional[_Attempt] = None) -> str:
        """One gateway round-trip for one seat, over httpx.AsyncClient.

        The request is built by the SHARED builder, so the async council and the
        sync router cannot drift into two wire formats.

        Returns the reply text. A refusal raises `ModelRefusedError` and a
        `length` finish raises `ResponseTruncatedError`, both before any parse.
        The finish reason is recorded on `attempt` first, so it survives either.

        This method classifies a reply under ADR-0141 and never enforces what was
        served. In a gate council it records the served-model and served-provider
        match results on `attempt`; `_run_seat` enforces them, on a reply that
        would otherwise yield a vote, after every retryable fault has had its
        label and its one retry.
        """
        url, headers, payload = build_gateway_request(
            cfg,
            prompt,
            system=system,
            max_tokens=self.config.max_tokens,
            # The council pins no temperature on any seat — it never did, and
            # the reject-set models refuse the param outright.
            temperature=None,
            response_format=response_format,
        )
        # The client timeout equals the seat deadline, never below it, so the
        # configured deadline (600 s by default) is the one that takes effect.
        client_kwargs: Dict[str, Any] = {"timeout": self.config.timeout_seconds}
        transport = getattr(self.config, "transport", None)
        if transport is not None:
            client_kwargs["transport"] = transport
        if attempt is not None:
            attempt.cfg = cfg
        async with httpx.AsyncClient(**client_kwargs) as client:
            response = await client.post(url, headers=headers, json=payload)
            raise_for_gateway_status(response)
            try:
                data = response.json()
            except ValueError:
                # A 200 body that does not decode is a malformed response under
                # ADR-0141 in a gate council: its label is `malformed-response`
                # and it gets its one retry. Any other council re-raises the
                # decode error under its existing label.
                if self._gate:
                    raise MalformedResponseError("reply body does not decode") from None
                raise
            # Provenance comes from the decoded body BEFORE the content parse, by
            # the one reader `parse_gateway_reply` also uses, so a reply whose
            # content is malformed still records what served it.
            served = reply_provenance(data) if isinstance(data, dict) else (None, None, None)
            if attempt is not None:
                attempt.replied = True
                attempt.served_model, attempt.served_provider, attempt.generation_id = served
                # Gate councils record both match results for every decoded reply.
                # `_gate` is the value fixed at construction, never re-read from
                # the mutable config.
                if self._gate:
                    probe = attempt.provenance()
                    attempt.served_model_match = _passes(check_served, cfg, probe)
                    attempt.served_provider_match = _passes(check_served_provider, cfg, probe)
            reply = parse_gateway_reply(data, cfg.api_string)
            if attempt is not None:
                attempt.finish_reason = _finish_value(reply.finish_reason)
            if reply.refused:
                raise ModelRefusedError(f"model {cfg.api_string} declined to answer (refusal)")
            if reply.finish_reason == "length":
                raise ResponseTruncatedError(f"model {cfg.api_string} stopped on its output budget")
            return reply.content

    @staticmethod
    def _reply_object(content: Optional[str]) -> dict:
        """The JSON object in a reply, or `MalformedResponseError`.

        The error carries a fixed message: no byte of the reply travels with it.
        """
        try:
            data = json.loads(_extract_json_blob(content))
        except ValueError:  # no braces, or a JSON decode error
            raise MalformedResponseError("reply holds no parseable JSON object") from None
        if not isinstance(data, dict):
            raise MalformedResponseError("reply holds no parseable JSON object")
        return data

    async def _run_seat(self, attempt_fn) -> _SeatRun:
        """Run one seat: an attempt, and at most one more after a retryable fault.

        Upstream retains per-attempt deadlines. Fork-controlled calls may also
        provide an overall deadline, which retries consume rather than reset.
        `attempt_fn(attempt)` is one attempt and returns the seat's result.
        The retry is keyed on the fault LABEL (`_RETRYABLE_FAULT_LABELS`), never on
        an HTTP status, and it replaces the failed attempt: it adds no result.

        In a gate council, an attempt that returned a result is a reply that would
        otherwise yield a vote. Only then are the served-model check and, second,
        the served-provider check enforced on it, so ADR-0141's classification and
        retry always come first. Either fault ends the seat with no retry and no
        result: no vote is ever taken from a mismatched reply.
        """
        may_retry = self.config.max_retries >= 1  # never more than one retry
        first_label: Optional[str] = None
        first_finish: Optional[str] = None
        retried = False
        loop = asyncio.get_running_loop()
        overall = self.config.overall_timeout_seconds
        deadlines = []
        if overall is not None:
            deadlines.append(loop.time() + overall)
        if self.config.overall_deadline_epoch is not None:
            deadlines.append(loop.time() + max(0, self.config.overall_deadline_epoch - time.time()))
        deadline = min(deadlines) if deadlines else None
        served = (None, None, None)  # provenance of the last attempt that got a reply
        attempts: List[Dict[str, Any]] = []
        for number in range(2 if may_retry else 1):
            attempt = _Attempt()
            try:
                remaining = None if deadline is None else deadline - loop.time()
                if remaining is not None and remaining <= 0:
                    raise asyncio.TimeoutError("overall council seat deadline exhausted")
                value = await asyncio.wait_for(
                    attempt_fn(attempt), timeout=min(self.config.timeout_seconds, remaining)
                    if remaining is not None else self.config.timeout_seconds)
                if self._gate:
                    self._enforce_served(attempt)
            except Exception as e:
                label = self._fault_label(e)
                attempt.fault_label = label
                attempts.append(attempt.record(number + 1))
                if attempt.replied:
                    served = (attempt.served_model, attempt.served_provider, attempt.generation_id)
                budget_left = deadline is None or loop.time() < deadline
                if number == 0 and may_retry and label in _RETRYABLE_FAULT_LABELS and budget_left:
                    logger.warning(f"seat attempt failed ({label}); retrying once")
                    first_label, first_finish, retried = label, attempt.finish_reason, True
                    continue
                return _SeatRun(error=e, finish_reason=attempt.finish_reason, retried=retried,
                                first_fault_label=first_label, first_finish_reason=first_finish,
                                served_model=served[0], served_provider=served[1],
                                generation_id=served[2], attempts=attempts)
            attempts.append(attempt.record(number + 1))
            if attempt.replied:
                served = (attempt.served_model, attempt.served_provider, attempt.generation_id)
            return _SeatRun(value=value, finish_reason=attempt.finish_reason, retried=retried,
                            first_fault_label=first_label, first_finish_reason=first_finish,
                            served_model=served[0], served_provider=served[1],
                            generation_id=served[2], attempts=attempts)
        raise AssertionError("unreachable: the last attempt always returns")  # pragma: no cover

    @staticmethod
    def _enforce_served(attempt: _Attempt) -> None:
        """Raise unless a vote-yielding reply's served model and provider are accepted.

        The served-model check runs first, so a reply that fails both carries the
        served-model fault. An attempt that produced a result without a decoded
        reply has nothing to check, and is refused as an absent served model.
        """
        if attempt.cfg is None or not attempt.replied:
            raise ServedModelMismatchError("reply reports no served model")
        probe = attempt.provenance()
        check_served(attempt.cfg, probe)
        check_served_provider(attempt.cfg, probe)

    async def _call_seat_async(self, seat: str, prompt: str, system: Optional[str] = None) -> CouncilVote:
        """One council seat, one gateway call, and at most one retry.

        Replaces the three SDK seats. Each attempt runs under its own deadline
        (`_run_seat`), so a stuck provider becomes one timeout-labelled errored
        vote and cannot hang the deliberation or erase the seats that answered.
        """
        model = self._seat_model(seat)
        gate = self._gate
        # The role names the gate slot a registry key was resolved for. Outside
        # gate mode a caller may seat any key, so no role is recorded there.
        role = _SEAT_ROLES[seat] if gate else None
        requested: Dict[str, Optional[str]] = {"model": None}

        async def _once(attempt: _Attempt) -> CouncilVote:
            cfg = self._config_for_seat(seat)
            requested["model"] = cfg.api_string
            instruction = (gate_vote_instruction(self._council_kind)
                           if gate else self._VOTE_JSON_INSTRUCTION)
            content = await self._post_seat(
                cfg,
                prompt + instruction,
                system=system or "You are a critical reviewer. Respond with valid JSON.",
                response_format={"type": "json_object"},
                attempt=attempt,
            )
            data = self._reply_object(content)
            try:
                if gate:
                    findings = _parse_gate_findings(_SEAT_ROLES[seat], data.get("findings"))
                    dissents = [f["text"] if isinstance(f["text"], str) else "" for f in findings]
                    decision = data.get("decision", "DEFER_TO_HUMAN")
                    if not isinstance(decision, str):
                        raise ValueError("decision is not a string")
                else:
                    findings = []
                    decision = data.get("decision", "DEFER_TO_HUMAN")
                    dissents = [d.get("point", "") for d in data.get("dissents", [])]
                return CouncilVote(
                    model=f"{self._SEAT_LABELS[seat]}/{model}",
                    provider=seat,
                    decision=decision,
                    confidence=validated_confidence(data.get("confidence", 0.5)),
                    reasoning=data.get("reasoning", ""),
                    dissenting_points=dissents,
                    findings=findings,
                )
            except (TypeError, AttributeError, ValueError, KeyError):
                # A field the vote cannot hold, such as `"dissents": null`, or a
                # confidence the ingest gate refuses. A reply that cannot become
                # a vote is a malformed reply, never a configuration error.
                raise MalformedResponseError("reply cannot be built into a vote") from None

        run = await self._run_seat(_once)
        provenance = dict(
            role=role, registry_key=model, requested_model=requested["model"],
            served_model=run.served_model, served_provider=run.served_provider,
            generation_id=run.generation_id)
        if run.error is not None:
            logger.error(f"{self._SEAT_LABELS[seat]} async error: {self._redact_error(run.error)}")
            return self._create_error_vote(
                seat, run.error, finish_reason=run.finish_reason, retried=run.retried,
                first_fault_label=run.first_fault_label,
                first_finish_reason=run.first_finish_reason, attempts=run.attempts,
                **provenance)
        vote = run.value
        for name, value in provenance.items():
            setattr(vote, name, value)
        vote.attempts = run.attempts
        vote.finish_reason = run.finish_reason
        vote.retried = run.retried
        vote.recovered = run.retried
        vote.first_fault_label = run.first_fault_label
        vote.first_finish_reason = run.first_finish_reason
        return vote

    # Closed label vocabulary. Every value here is a SOURCE LITERAL — the output
    # of _redact_error can only ever be one of these strings (plus a validated
    # int), which is what makes the non-leakage guarantee structural rather than
    # pattern-based. Keys are exception class names matched against the MRO.
    # Thirteen labels + "unknown", justified by distinct-operator-action: a timeout
    # and a rate-limit want a retry, auth wants a credential fix, unreachable
    # wants a network check, client-config wants a request fix, provider wants
    # waiting, malformed-response wants a bug report, truncated wants a larger
    # output budget or a shorter prompt, insufficient-credit wants credit,
    # unexpected-status wants the status reported, and refused wants a
    # prompt/content change or a fallback model (nothing failed — the model
    # declined), served-model wants the registry entry or the gateway route
    # checked (the reply came from a model the entry does not accept; it is
    # never retried or replaced), and served-provider-mismatch wants the
    # entry's accepted served-provider set or the route checked, the same way
    # and for the same reason. These are what an operator does next. The seat's own automatic
    # second call is a different, closed set: _RETRYABLE_FAULT_LABELS. A test
    # pins the set.
    _ERROR_LABELS: dict[str, str] = {
        # truncated (a reply that stopped on its output budget; finish "length")
        "ResponseTruncatedError": "truncated",
        # refused (safety decline; HTTP 200 with finish_reason "content_filter")
        "ModelRefusedError": "refused",
        # served-model (the gateway reported a served model the entry does not
        # accept, or none; gate councils only). NOT retryable and never replaced.
        "ServedModelMismatchError": "served-model",
        # served-provider-mismatch (the gateway reported a served provider the
        # entry's accepted_served_providers set does not hold, or none; gate
        # councils only, checked after the served model). NOT retryable and
        # never replaced.
        "ServedProviderMismatchError": "served-provider-mismatch",
        # insufficient-credit (HTTP 402; NOT retryable — retrying spends nothing
        # and fixes nothing, so it is held apart from the retryable 5xx family)
        "GatewayInsufficientCreditError": "insufficient-credit",
        # timeout
        "TimeoutError": "timeout",
        "APITimeoutError": "timeout",
        "ReadTimeout": "timeout",
        "ConnectTimeout": "timeout",
        "WriteTimeout": "timeout",
        "PoolTimeout": "timeout",
        "GatewayTimeoutError": "timeout",
        "DeadlineExceeded": "timeout",
        # auth
        "AuthenticationError": "auth",
        "PermissionDeniedError": "auth",
        "PermissionDenied": "auth",
        "Unauthenticated": "auth",
        "Forbidden": "auth",
        "GatewayAuthError": "auth",
        # A key that is absent, not rejected. It is the same operator action —
        # fix the credential — and it is the failure a shared gateway key makes
        # most likely: one missing key degrades all three seats at once. Absent
        # this key the whole council reports "unknown", the least actionable
        # label there is, for the most actionable failure there is.
        "EnvNotConfigured": "auth",
        # rate-limit
        "RateLimitError": "rate-limit",
        "ResourceExhausted": "rate-limit",
        "TooManyRequests": "rate-limit",
        "GatewayRateLimitError": "rate-limit",
        # unreachable
        "APIConnectionError": "unreachable",
        "ConnectError": "unreachable",
        "ConnectionError": "unreachable",
        "NetworkError": "unreachable",
        "ServiceUnavailable": "unreachable",
        "SSLError": "unreachable",
        "SSLCertVerificationError": "unreachable",
        # provider (server-side fault; wait it out)
        "InternalServerError": "provider",
        "GatewayUpstreamError": "provider",
        "APIStatusError": "provider",
        "APIError": "provider",
        "ServerError": "provider",
        # client-config (our request is wrong)
        "GatewayClientError": "client-config",
        "BadRequestError": "client-config",
        "NotFoundError": "client-config",
        "UnprocessableEntityError": "client-config",
        "ConflictError": "client-config",
        "InvalidArgument": "client-config",
        "TypeError": "client-config",
        "ValueError": "client-config",
        # malformed-response (SDK-side: a response that arrived but failed to
        # parse or validate. ConfidenceRejectedError is ours — the ingest gate
        # refusing a confidence value the SDK parsed fine — mapped here so it
        # degrades to an error vote inside the closed vocabulary). So is
        # MalformedResponseError: the seat's own row for a reply with no
        # parseable JSON object, or one that cannot be built into a vote. It
        # is not a ValueError, so a real configuration error keeps client-config.
        "MalformedResponseError": "malformed-response",
        "APIResponseValidationError": "malformed-response",
        "JSONDecodeError": "malformed-response",
        "ValidationError": "malformed-response",
        "ConfidenceRejectedError": "malformed-response",
        # unexpected-status (a non-2xx outside 400-599 — a 3xx or a 1xx). The
        # client sends no `follow_redirects`, so the gateway's 3xx comes back as
        # a response instead of being followed, and a 1xx informational arrives
        # the same way. Neither is a fault the operator can fix by changing the
        # payload or the credential, so the action is to report the status.
        "GatewayUnexpectedStatusError": "unexpected-status",
        # The MRO backstop, and the reason this row is the base class rather
        # than a second specific one. `_redact_error` walks `__mro__` and takes
        # the FIRST hit, so every subclass above still wins its own label; a
        # future GatewayError subclass that nobody adds a row for lands here
        # instead of regressing to "unknown".
        "GatewayError": "unexpected-status",
    }

    # Legacy in-repo string literals passed by callers that have no exception
    # object to hand (see the deliberate_async timeout path). Closed by
    # construction: an arbitrary string never matches and degrades to "unknown".
    _LEGACY_LITERAL_LABELS: dict[str, str] = {"Timeout": "timeout"}

    @staticmethod
    def _fault_label(error: "BaseException | str") -> str:
        """The closed-vocabulary fault label for `error`, without any status.

        The same classification `_redact_error` uses (an MRO walk over
        `_ERROR_LABELS`; a legacy string literal; else "unknown"), and the value
        the retry keys on.
        """
        if isinstance(error, BaseException):
            for cls in type(error).__mro__:
                hit = AsyncCouncil._ERROR_LABELS.get(getattr(cls, "__name__", ""))
                if hit is not None:
                    return hit
            return "unknown"
        return AsyncCouncil._LEGACY_LITERAL_LABELS.get(error, "unknown")

    @staticmethod
    def _redact_error(error: "BaseException | str") -> str:
        """Summarize a provider failure as a CLOSED-VOCABULARY string, for a vote
        that can reach a persisted surface (ADR-0054 follow-on, docs/AGENTS.md §13).

        NON-LEAKAGE (absolute, structural). No substring of `error` is ever
        copied into the return value. The output is built only from source
        literals in `_ERROR_LABELS` / `_LEGACY_LITERAL_LABELS` plus an integer
        proven to be an exact `int` in 100..599. This is a guarantee about THIS
        FUNCTION's return value, not about the module: callers that interpolate
        a raw exception elsewhere are outside it.

        CLASSIFICATION (best-effort, NOT absolute). The label is chosen by
        matching `type(error).__mro__` names against a fixed table. A class can
        carry a spoofed or misleading `__name__`, so a mislabelled failure is
        possible — an auth error could in principle be reported as `timeout`.
        That is a FIDELITY risk only: a spoofed name can still select nothing but
        one of the source literals above, so it cannot leak. Do not read the
        label as authoritative; read it as a triage hint.

        Why not a regex. The previous implementation scrubbed URL- and
        token-shaped runs. A denylist over an open input space cannot support an
        absolute claim: its `[A-Za-z0-9_\\-]{20,}` class excluded `/ . + = :`, so
        AWS-shaped secrets, DSNs with inline passwords, `apikey=` pairs and
        `~/.crux/env` paths passed through verbatim, while it simultaneously
        destroyed 20 of 91 real SDK exception class names. Structured omission is
        strictly safer AND strictly more diagnostic.
        """
        label = AsyncCouncil._fault_label(error)
        status: int | None = None

        if isinstance(error, BaseException):
            # Attribute reads can execute arbitrary property code and may raise;
            # any failure must degrade to "status omitted" rather than propagate
            # out of the redactor, which would put the raw exception back on an
            # unredacted path.
            for attr in ("status_code", "code"):
                try:
                    value = getattr(error, attr, None)
                except Exception:
                    continue
                # `type(v) is int` deliberately, NOT isinstance: a hostile or
                # broken int SUBCLASS can override __str__/__format__ and emit
                # arbitrary text, defeating the absolute guarantee.
                if type(value) is int and 100 <= value <= 599:
                    status = value
                    break
            if status is None:
                try:
                    value = getattr(getattr(error, "response", None), "status_code", None)
                except Exception:
                    value = None
                if type(value) is int and 100 <= value <= 599:
                    status = value

        return f"{label} (HTTP {status})" if status is not None else label

    def _create_error_vote(self, provider: str, error: "BaseException | str", *,
                           finish_reason: Optional[str] = None, retried: bool = False,
                           first_fault_label: Optional[str] = None,
                           first_finish_reason: Optional[str] = None,
                           role: Optional[str] = None, registry_key: Optional[str] = None,
                           requested_model: Optional[str] = None,
                           served_model: Optional[str] = None,
                           served_provider: Optional[str] = None,
                           generation_id: Optional[str] = None,
                           attempts: Optional[List[Dict[str, Any]]] = None) -> CouncilVote:
        """Create an error vote when a provider fails.

        Marked errored=True (ADR-0054): the aggregator excludes it from all
        consensus math rather than folding its 0.0 confidence / DEFER into the
        verdict. The /ERROR model suffix is kept as a human-legible corroborator.

        The failure is summarized by `_redact_error` into a closed-vocabulary
        label, so no substring of the provider exception reaches the vote — and
        therefore none reaches a persisted deliberation surface via THIS vote.
        Scope that claim precisely: it covers the CouncilVote built here. It is
        NOT a module-wide guarantee. Raw exception text still reaches other
        surfaces from other call sites — `task_planner.py:274`,
        `runbook.py:635/:687`, `crux_server.py:83/:118` (HTTP 500 bodies), and
        the visual council's `observations` — each of which needs its own fix and
        is tracked separately. Prefer passing the exception OBJECT rather than
        `str(e)`: a string cannot be classified by type and degrades to
        "unknown".
        """
        safe = self._redact_error(error)
        return CouncilVote(
            model=f"{provider}/ERROR",
            provider=provider,
            decision="DEFER_TO_HUMAN",
            confidence=0.0,
            reasoning=f"Error calling {provider}: {safe}",
            dissenting_points=[f"Provider {provider} failed: {safe}"],
            errored=True,
            fault_label=self._fault_label(error),
            finish_reason=finish_reason,
            retried=retried,
            first_fault_label=first_fault_label,
            first_finish_reason=first_finish_reason,
            role=role, registry_key=registry_key, requested_model=requested_model,
            served_model=served_model, served_provider=served_provider,
            generation_id=generation_id, attempts=list(attempts or []),
        )

    async def deliberate(self, prompt: str, system: Optional[str] = None) -> CouncilDeliberation:
        """
        Run all council members in PARALLEL and aggregate results.

        Each seat enforces timeout_seconds PER ATTEMPT (`_run_seat`), so a stuck
        provider becomes one timeout-labelled errored vote and cannot hang the
        deliberation or erase the seats that answered. A seat that fails with a
        retryable fault label is called once more, so the worst case per seat is
        two deadlines.
        """
        self._assert_gate_unchanged()
        logger.info("ASYNC COUNCIL DELIBERATION")
        start_time = datetime.now()

        # Build task list based on available seats. One method, three seats —
        # the seat name is the only thing that differs between them now.
        #
        # TWO parallel lists, deliberately. `task_seats` carries the seat KEY
        # ("openai") and is the only one an error vote may be built from:
        # `CouncilVote.provider` is what aggregation counts and what a consumer
        # joins on, and a responding seat carries the key. Feeding the display
        # label ("OpenAI") to `_create_error_vote` gave one seat two identities
        # depending on whether it answered. `task_names` is display text for the
        # log line below and nothing else.
        tasks = []
        task_seats = []
        task_names = []

        for seat in self._SEAT_ORDER:
            if seat in self.available_providers:
                tasks.append(self._call_seat_async(seat, prompt, system))
                task_seats.append(seat)
                task_names.append(self._SEAT_LABELS[seat])

        if not tasks:
            logger.warning("No council members available!")
            return CouncilDeliberation(
                votes=[],
                consensus="NO_QUORUM",
                consensus_confidence=0.0,
                key_agreements=[],
                key_disagreements=["No LLM providers configured"],
                final_recommendation={"action": "DEFER_TO_HUMAN", "reason": "NO_QUORUM",
                                      "degraded": False, "errored_seats": "0/0",
                                      "off_scale": [], **self._approval_reports([])},
                dissent_count=0,
                confidence_adjustment=0.0,
            )

        logger.info(f"     Running {len(tasks)} providers in parallel: {task_names}")
        logger.info(f"     Timeout: {self.config.timeout_seconds}s per attempt")

        # No outer deadline: each task carries its own, so a seat that times out
        # returns as an exception here and becomes that seat's errored vote below.
        votes: List[CouncilVote] = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle exceptions
        clean_votes = []
        for i, vote in enumerate(votes):
            if isinstance(vote, BaseException) and not isinstance(vote, Exception):
                # A cancellation (or another BaseException) is never a vote: it propagates.
                raise vote
            if isinstance(vote, Exception):
                # Pass the exception OBJECT (not str) so it classifies by type;
                # str(vote) would degrade every gathered failure to "unknown".
                # A gate seat keeps its role and registry key: the record names every seat.
                seat = task_seats[i]
                identity = ({"role": _SEAT_ROLES[seat], "registry_key": self._seat_model(seat)}
                            if self._gate else {})
                clean_votes.append(self._create_error_vote(seat, vote, **identity))
            else:
                clean_votes.append(vote)

        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"     All {len(clean_votes)} votes received in {elapsed:.1f}s")

        # Aggregate results
        return self._aggregate_votes(clean_votes)

    @staticmethod
    def _approval_reports(responding: List[CouncilVote]) -> dict:
        """The `conditioned` / `conditions` / `nits` / `nit_items` keys of a result.

        Built from RESPONDING seats only, so an errored seat never contributes.
        `conditions` and `nit_items` are keyed by seat and hold each such seat's
        own dissenting points, carried unchanged: no bound cuts them, as the
        same points are carried in `votes` and `key_disagreements`. They are
        untrusted model text. No reader matches a point as a decision or an
        action, and `conditioned` keys on the decision token. Escaping a point
        for display is the printing reader's job; the data keeps every byte.
        """
        conditions: Dict[str, List[str]] = {}
        nit_items: Dict[str, List[str]] = {}
        for v in responding:
            if v.decision == "APPROVE_WITH_CONDITIONS":
                conditions.setdefault(v.provider, []).extend(v.dissenting_points)
            elif v.decision == "APPROVE_WITH_NITS":
                nit_items.setdefault(v.provider, []).extend(v.dissenting_points)
        return {"conditioned": bool(conditions), "conditions": conditions,
                "nits": bool(nit_items), "nit_items": nit_items}

    def _aggregate_votes(self, votes: List[CouncilVote]) -> CouncilDeliberation:
        """Aggregate individual votes into a consensus"""
        if not votes:
            return CouncilDeliberation(
                votes=[],
                consensus="NO_QUORUM",
                consensus_confidence=0.0,
                key_agreements=[],
                key_disagreements=[],
                final_recommendation={"action": "DEFER_TO_HUMAN", "reason": "NO_QUORUM",
                                      "degraded": False, "errored_seats": "0/0",
                                      "off_scale": [], **self._approval_reports([])},
                dissent_count=0,
                confidence_adjustment=0.0,
            )

        # Partition responding vs errored seats (ADR-0054). The `errored` flag is
        # authoritative (set only by _create_error_vote); the /ERROR model suffix
        # is a corroborator. `votes` retains ALL seats — the derived errored_seats
        # property counts over them — but every consensus/confidence computation
        # below runs over RESPONDING seats only, so one provider outage can never
        # poison the score or make UNANIMOUS_APPROVE unreachable.
        total = len(votes)
        responding = [v for v in votes if not getattr(v, "errored", False)]
        errored_count = total - len(responding)
        degraded = errored_count > 0
        errored_seats = f"{errored_count}/{total}"
        n = len(responding)

        # A council needs a quorum of responding seats to render a verdict.
        QUORUM_MIN = 2

        # Dissents come from RESPONDING seats only — a provider outage's synthetic
        # "failed" dissent must not inflate dissent_count / key_disagreements.
        all_dissents = []
        for vote in responding:
            all_dissents.extend(vote.dissenting_points)
        key_agreements = self._extract_key_agreements(responding)

        # Responding seats whose decision is outside the known scale stay visible
        # here whatever the consensus label says. Additive key; every return path
        # carries it. Each entry is model text, so it is bounded and its
        # unprintable characters replaced by `untrusted.redact`, the one helper
        # that owns the bound. A short printable entry passes through unchanged.
        off_scale = [redact(f"{v.provider}:{v.decision}", quoted=False) for v in responding
                     if v.decision not in _KNOWN_DECISIONS]
        # Conditioned approvals and nits, from responding seats only.
        approval_reports = self._approval_reports(responding)
        conditioned = approval_reports["conditioned"]

        if n < QUORUM_MIN:
            # Below quorum (incl. all-errored): no verdict, always defer.
            overall_confidence = (sum(v.confidence for v in responding) / n) if n else 0.0
            consensus = "NO_QUORUM"
            recommended_action = "DEFER_TO_HUMAN"
            logger.info(f"     -> NO_QUORUM ({n} responding, errored {errored_seats}); Action: DEFER_TO_HUMAN")
            return CouncilDeliberation(
                votes=votes,
                consensus=consensus,
                consensus_confidence=overall_confidence,
                key_agreements=key_agreements,
                key_disagreements=all_dissents[:5],
                final_recommendation={"action": recommended_action, "reason": consensus,
                                      "degraded": degraded, "errored_seats": errored_seats,
                                      "off_scale": off_scale, **approval_reports},
                dissent_count=len(all_dissents),
                confidence_adjustment=overall_confidence - 0.5,
            )

        # Count decisions over responding seats. APPROVE_WITH_NITS and
        # APPROVE_WITH_CONDITIONS are approvals (a nit is non-blocking; it still
        # surfaces via dissenting_points).
        APPROVE = _APPROVE_DECISIONS
        approve_count = sum(1 for v in responding if v.decision in APPROVE)
        reject_count = sum(1 for v in responding if v.decision == "REJECT")

        # Determine consensus (over responding seats)
        if approve_count == n:
            consensus = "UNANIMOUS_APPROVE"
        elif reject_count == n:
            consensus = "UNANIMOUS_REJECT"
        elif all(v.decision == responding[0].decision for v in responding):
            # Every responding seat returned the same decision, and it is neither
            # an approval nor REJECT. Name it; it routes to DEFER_TO_HUMAN below
            # because the action chain recognises only the approve/reject labels.
            first = responding[0].decision
            if _echoable_label_token(first):
                consensus = f"UNANIMOUS_{first}"
            else:
                consensus = "UNANIMOUS_OFF_SCALE"
        elif approve_count > n / 2:
            consensus = "MAJORITY_APPROVE"
        elif reject_count > n / 2:
            consensus = "MAJORITY_REJECT"
        else:
            consensus = "SPLIT"

        # Confidence mean over responding seats only.
        overall_confidence = sum(v.confidence for v in responding) / n

        # Recommend action. Degraded (any errored seat) caps the route at
        # EXECUTE_WITH_MONITORING — AUTO_EXECUTE requires a full, un-degraded
        # quorum at the existing 0.85 threshold. A conditioned approval caps it
        # the same way: it routes to EXECUTE_WITH_MONITORING at most, on the
        # unchanged 0.70 floor, and otherwise defers. Nits never change the
        # route: APPROVE_WITH_NITS counts as APPROVE above and is not tested here.
        if (consensus == "UNANIMOUS_APPROVE" and overall_confidence >= 0.85
                and not degraded and not conditioned):
            recommended_action = "AUTO_EXECUTE"
        elif consensus in ("UNANIMOUS_APPROVE", "MAJORITY_APPROVE") and overall_confidence >= 0.70:
            # existing 0.70 EXECUTE_WITH_MONITORING floor, unchanged by ADR-0054
            recommended_action = "EXECUTE_WITH_MONITORING"
        elif consensus == "UNANIMOUS_REJECT":
            recommended_action = "ABORT"
        else:
            recommended_action = "DEFER_TO_HUMAN"

        # Log summary
        for vote in responding:
            status = "+" if vote.decision in APPROVE else "-"
            label = _log_label(_LOG_DECISIONS, vote.decision, "OFF_SCALE")
            logger.info(f"     {status} {vote.model}: {label} ({vote.confidence:.0%})")
        if degraded:
            logger.info(f"     (degraded: errored seats {errored_seats})")
        logger.info(f"     -> Consensus: {_log_label(_LOG_CONSENSUS, consensus, _LOG_UNLISTED_CONSENSUS)}, "
                    f"Action: {recommended_action}")

        return CouncilDeliberation(
            votes=votes,
            consensus=consensus,
            consensus_confidence=overall_confidence,
            key_agreements=key_agreements,
            key_disagreements=all_dissents[:5],
            final_recommendation={"action": recommended_action, "reason": consensus,
                                  "degraded": degraded, "errored_seats": errored_seats,
                                  "off_scale": off_scale, **approval_reports},
            dissent_count=len(all_dissents),
            confidence_adjustment=overall_confidence - 0.5,  # Adjustment from baseline 50%
        )

    def _extract_key_agreements(self, votes: List[CouncilVote]) -> List[str]:
        """Extract common themes from votes."""
        agreements = []

        # Check if all agree on decision
        decisions = [v.decision for v in votes]
        if decisions and all(d == decisions[0] for d in decisions):  # not set(): a decision may be unhashable
            agreements.append(f"All models agree: {decisions[0]}")

        # Check if confidence is consistently high or low
        confidences = [v.confidence for v in votes]
        if all(c >= 0.8 for c in confidences):
            agreements.append("All models have high confidence (>=80%)")
        elif all(c <= 0.5 for c in confidences):
            agreements.append("All models have low confidence (<=50%)")

        return agreements

    def deliberate_sync(self, prompt: str, system: Optional[str] = None) -> CouncilDeliberation:
        """Synchronous wrapper for deliberate()"""
        return asyncio.run(self.deliberate(prompt, system))


# =============================================================================
# ASYNC VISUAL COUNCIL
# =============================================================================


class AsyncVisualCouncil(AsyncCouncil):
    """
    Async Visual Council - Parallel vision model execution.

    Runs the OpenAI and Anthropic vision seats in parallel, both through the
    one gateway.
    """

    async def analyze_image(self, image_b64: str, prompt: str) -> List[VisualVoteResult]:
        """Analyze an image with all available vision models in parallel"""
        self._assert_gate_unchanged()
        tasks = []
        task_names = []

        for seat, label in (("openai", "OpenAI-vision"), ("anthropic", "Claude-vision")):
            if seat in self.available_providers:
                tasks.append(self._analyze_seat_async(seat, label, image_b64, prompt))
                task_names.append(label)

        if not tasks:
            return []

        logger.info(f"     Running {len(tasks)} vision models in parallel")

        # Each seat carries its own per-attempt deadline (`_run_seat`): a slow
        # seat comes back as an errored timeout result, and the other seat's
        # result is kept. An errored result is excluded from any count of failed
        # checks by its `errored` marker.
        results = await asyncio.gather(*tasks, return_exceptions=True)

        clean_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                clean_results.append(
                    VisualVoteResult(
                        model_name=task_names[i],
                        passed=False,
                        confidence=0.0,
                        # `result` is an Exception here (isinstance-guarded above),
                        # so pass the object for MRO classification, never str().
                        observations=f"Error: {self._redact_error(result)}",
                        anomalies=[],
                        errored=True,
                        fault_label=self._fault_label(result),
                    )
                )
            else:
                clean_results.append(result)

        return clean_results

    async def _analyze_seat_async(
        self, seat: str, label: str, image_b64: str, prompt: str
    ) -> VisualVoteResult:
        """One vision seat, one gateway call, and at most one retry.

        Replaces the two SDK vision seats. The retry, the per-attempt deadline,
        the fault labels and the finish reason are the text seat's (`_run_seat`).

        The image rides the OpenAI-compatible `image_url` content part, whose
        value is the nested `{"url": data_url}` object — the chat-completions
        shape, which is the only shape the gateway speaks.
        """
        model = self._seat_model(seat)

        async def _once(attempt: _Attempt) -> VisualVoteResult:
            cfg = self._config_for_seat(seat)
            data_url = f"data:image/png;base64,{image_b64}"
            content = await self._post_seat(
                cfg,
                [
                    {"type": "text", "text": prompt + self._VISION_JSON_INSTRUCTION},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
                attempt=attempt,
            )
            data = self._reply_object(content)
            try:
                return VisualVoteResult(
                    model_name=label,
                    passed=data.get("passed", False),
                    confidence=validated_confidence(data.get("confidence", 0.5)),
                    observations=data.get("observations", ""),
                    anomalies=data.get("anomalies", []),
                )
            except (TypeError, AttributeError, ValueError, KeyError):
                raise MalformedResponseError("reply cannot be built into a result") from None

        run = await self._run_seat(_once)
        if run.error is not None:
            return VisualVoteResult(
                model_name=label, passed=False, confidence=0.0,
                observations=f"Error: {self._redact_error(run.error)}", anomalies=[],
                errored=True, fault_label=self._fault_label(run.error),
                finish_reason=run.finish_reason, retried=run.retried,
                first_fault_label=run.first_fault_label,
                first_finish_reason=run.first_finish_reason,
                served_model=run.served_model, served_provider=run.served_provider,
                generation_id=run.generation_id, attempts=run.attempts,
            )
        result = run.value
        result.served_model = run.served_model
        result.served_provider = run.served_provider
        result.generation_id = run.generation_id
        result.attempts = run.attempts
        result.finish_reason = run.finish_reason
        result.retried = run.retried
        result.recovered = run.retried
        result.first_fault_label = run.first_fault_label
        result.first_finish_reason = run.first_finish_reason
        return result

    def analyze_sync(self, image_b64: str, prompt: str) -> List[VisualVoteResult]:
        """Synchronous wrapper"""
        return asyncio.run(self.analyze_image(image_b64, prompt))


# Factory functions
def create_async_council(config: Optional[AsyncCouncilConfig] = None) -> AsyncCouncil:
    """Create an async council instance."""
    return AsyncCouncil(config)


def create_visual_council(config: Optional[AsyncCouncilConfig] = None) -> AsyncVisualCouncil:
    """Create an async visual council instance."""
    return AsyncVisualCouncil(config)


__all__ = [
    "AsyncCouncilConfig",
    "CouncilAssignmentRefused",
    "GATE_COUNCIL_KINDS",
    "gate_vote_instruction",
    "validate_gate_assignment",
    "VisualVoteResult",
    "AsyncCouncil",
    "AsyncVisualCouncil",
    "create_async_council",
    "create_visual_council",
]


if __name__ == "__main__":
    # Import-health entry (ADR-0035 §2 empirical verification): reaching this
    # line means the PEP 723 deps were provisioned and the module-level
    # imports above already resolved `crux_env` and the LLM router
    # (`crux.core.llm_caller`) via the sibling-package sys.path insert.
    # No API call is made.
    print("crux.council.async_council import ok")
