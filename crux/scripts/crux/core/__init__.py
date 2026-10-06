"""
crux.core - Shared infrastructure for the crux verification toolkit.

Includes what council and other crux consumers need:

- llm_caller: One OpenAI-compatible gateway client (OpenRouter) for every model
- tracer: Semantic logging and reasoning traces (used by council)
- data_classes: Core RSI data structures (used by async_council and council consumers)

Deliberately excluded:
- deprecation_logger (no aliases retained across the rename)
- gemini3pro (not imported by council or other consumers)
"""

from .data_classes import (
    # Enums
    DissentStatus,
    DissentSeverity,
    ResolutionStatus,
    ProbeType,
    ApprovalStatus,
    OperationType,
    # Confidence
    ConfidenceUpdate,
    ConfidenceTrajectory,
    # Dissent
    DissentPoint,
    ResolutionAttempt,
    # Probes
    ProbeResult,
    SynthesizedProbe,
    # Semantic Bridge
    SemanticBridgeLink,
    # Council
    CouncilVote,
    CouncilDeliberation,
    # Traces
    SemanticTrace,
    ReasoningLog,
    # Sandbox
    InvariantCheck,
    SandboxResult,
    # Mental Model
    EntityRole,
    DataFlowEdge,
    MentalModel,
)

from .llm_caller import (
    call_model,
    call_gateway,
    build_gateway_request,
    raise_for_gateway_status,
    parse_gateway_response,
    gateway_timeout_seconds,
    call_gemini_pro,
    call_gemini_flash,
    call_claude_opus,
    call_claude_sonnet,
    call_anthropic_top,
    call_anthropic_balanced,
    list_available_models,
    get_model_config,
    get_default_model,
    get_default_models,
    invalidate_model_cache,
    ModelConfig,
    ModelRefusedError,
    GatewayError,
    GatewayInsufficientCreditError,
    GatewayUpstreamError,
    GatewayTimeoutError,
    GatewayAuthError,
    GatewayRateLimitError,
    GatewayClientError,
    GatewayUnexpectedStatusError,
    ConfidenceRejectedError,
)

from .tracer import (
    Tracer,
    TraceEntry,
    ModelCall,
    Phase,
    SelfReflection,
    get_tracer,
    log_reasoning,
)

__all__ = [
    # LLM Caller
    "call_model",
    "call_gateway",
    "build_gateway_request",
    "raise_for_gateway_status",
    "parse_gateway_response",
    "gateway_timeout_seconds",
    "call_gemini_pro",
    "call_gemini_flash",
    "call_claude_opus",
    "call_claude_sonnet",
    "call_anthropic_top",
    "call_anthropic_balanced",
    "list_available_models",
    "get_model_config",
    "get_default_model",
    "get_default_models",
    "invalidate_model_cache",
    "ModelConfig",
    "ModelRefusedError",
    "GatewayError",
    "GatewayInsufficientCreditError",
    "GatewayUpstreamError",
    "GatewayTimeoutError",
    "GatewayAuthError",
    "GatewayRateLimitError",
    "GatewayClientError",
    "GatewayUnexpectedStatusError",
    "ConfidenceRejectedError",
    # Tracer
    "Tracer",
    "TraceEntry",
    "ModelCall",
    "Phase",
    "SelfReflection",
    "get_tracer",
    "log_reasoning",
    # Data Classes - Enums
    "DissentStatus",
    "DissentSeverity",
    "ResolutionStatus",
    "ProbeType",
    "ApprovalStatus",
    "OperationType",
    # Data Classes - Confidence
    "ConfidenceUpdate",
    "ConfidenceTrajectory",
    # Data Classes - Dissent
    "DissentPoint",
    "ResolutionAttempt",
    # Data Classes - Probes
    "ProbeResult",
    "SynthesizedProbe",
    # Data Classes - Semantic Bridge
    "SemanticBridgeLink",
    # Data Classes - Council
    "CouncilVote",
    "CouncilDeliberation",
    # Data Classes - Traces
    "SemanticTrace",
    "ReasoningLog",
    # Data Classes - Sandbox
    "InvariantCheck",
    "SandboxResult",
    # Data Classes - Mental Model
    "EntityRole",
    "DataFlowEdge",
    "MentalModel",
]
