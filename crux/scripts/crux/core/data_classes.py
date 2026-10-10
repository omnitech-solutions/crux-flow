"""
data_classes.py - Core tracing, identity, and council data structures.

Universal data classes used by the crux verification substrate.
These are DOMAIN-AGNOSTIC — usable for Excel, PDF, code analysis, etc.
"""

from enum import Enum
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field


# =============================================================================
# UNIVERSAL ENUMS
# =============================================================================

class DissentStatus(Enum):
    """Status of a dissenting point in council deliberation"""
    UNRESOLVED = "unresolved"
    PARTIALLY_RESOLVED = "partially_resolved"
    FULLY_RESOLVED = "fully_resolved"
    ESCALATED = "escalated"


class DissentSeverity(Enum):
    """Severity of a dissenting point"""
    CRITICAL = "critical"  # Blocks execution
    HIGH = "high"          # Needs resolution before proceed
    MEDIUM = "medium"      # Should address if possible
    LOW = "low"            # Nice to resolve
    INFO = "info"          # Informational only


class ResolutionStatus(Enum):
    """Result of attempting to resolve a dissent"""
    RESOLVED = "resolved"
    PARTIALLY_RESOLVED = "partially_resolved"
    CANNOT_RESOLVE = "cannot_resolve"
    NEEDS_SANDBOX = "needs_sandbox"
    NEEDS_HUMAN = "needs_human"


class ProbeType(Enum):
    """Types of probes that can be synthesized"""
    # Generic probes
    CHECK_EXISTS = "check_exists"
    CHECK_VALUE = "check_value"
    CHECK_TYPE = "check_type"
    CHECK_INVARIANT = "check_invariant"
    COUNT_ITEMS = "count_items"
    COMPUTE_VALUE = "compute_value"
    VALIDATE_SCHEMA = "validate_schema"
    COMPARE_BEFORE_AFTER = "compare_before_after"
    CUSTOM_CODE = "custom_code"
    # Visual probes
    VISUAL_CHECK = "visual_check"
    SCREENSHOT_DIFF = "screenshot_diff"


class ApprovalStatus(Enum):
    """Status of human approval for changes"""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"


class OperationType(Enum):
    """Type of operation being performed"""
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"
    APPEND = "append"
    TRANSFORM = "transform"


# =============================================================================
# CONFIDENCE TRACKING
# =============================================================================

@dataclass
class ConfidenceUpdate:
    """Tracks a single confidence adjustment with full reasoning"""
    source: str           # What caused this update (probe, council, etc.)
    signal_type: str      # Type of signal (positive, negative, neutral)
    delta: float          # Change in confidence (-1.0 to 1.0)
    evidence: str         # What evidence supports this
    loop_number: int      # Which loop this occurred in
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ConfidenceTrajectory:
    """Full confidence history across loops"""
    initial: float = 0.5
    current: float = 0.5
    updates: List[ConfidenceUpdate] = field(default_factory=list)

    def apply_update(self, update: ConfidenceUpdate):
        """Apply a confidence update and track it"""
        self.updates.append(update)
        self.current = max(0.0, min(1.0, self.current + update.delta))

    @property
    def trajectory(self) -> List[float]:
        """Get confidence values at each step"""
        values = [self.initial]
        current = self.initial
        for update in self.updates:
            current = max(0.0, min(1.0, current + update.delta))
            values.append(current)
        return values


# =============================================================================
# DISSENT & RESOLUTION
# =============================================================================

@dataclass
class DissentPoint:
    """
    A tracked dissenting point across loops.

    Dissents are concerns raised by council members or probes that
    must be resolved before execution.
    """
    id: str
    content: str              # The actual concern/dissent
    raised_by: str            # Model or component that raised it
    raised_loop: int          # Which loop it was raised in
    severity: DissentSeverity
    status: DissentStatus = DissentStatus.UNRESOLVED
    persistence_count: int = 1  # How many loops it's persisted
    resolution_notes: List[str] = field(default_factory=list)
    related_probes: List[str] = field(default_factory=list)

    def escalate(self):
        """Escalate if persisting too long"""
        self.persistence_count += 1
        if self.persistence_count >= 3 and self.severity in [DissentSeverity.CRITICAL, DissentSeverity.HIGH]:
            self.status = DissentStatus.ESCALATED


@dataclass
class ResolutionAttempt:
    """Result of attempting to resolve a dissent"""
    dissent_id: str
    dissent_content: str
    status: ResolutionStatus
    method: str               # How it was resolved (context, probe, sandbox)
    evidence: str             # Evidence supporting resolution
    confidence_impact: float  # How much this affects confidence
    tool_used: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


# =============================================================================
# PROBES & VERIFICATION
# =============================================================================

@dataclass
class ProbeResult:
    """Result of running a verification probe"""
    probe_type: ProbeType
    probe_id: str
    target: str               # What was probed
    result: Any               # The probe result
    verified: bool            # Did it verify the hypothesis?
    confidence_impact: float  # How much this affects confidence
    evidence: str = ""        # Human-readable evidence
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class SynthesizedProbe:
    """A probe dynamically synthesized by the LLM"""
    name: str
    probe_type: ProbeType
    description: str
    code: str                 # Executable code (Python)
    parameters: Dict[str, Any]
    generated_by: str         # Model that generated it
    from_dissent: Optional[str] = None  # Dissent ID that triggered this

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "probe_type": self.probe_type.value,
            "description": self.description,
            "code": self.code,
            "parameters": self.parameters,
            "generated_by": self.generated_by,
            "from_dissent": self.from_dissent,
        }


# =============================================================================
# SEMANTIC BRIDGE
# =============================================================================

@dataclass
class SemanticBridgeLink:
    """
    Links probe results to dissents they help resolve.

    The semantic bridge connects:
    - Probes: Verification tools
    - Dissents: Concerns to resolve
    - Resolutions: How concerns were addressed
    """
    probe_id: str
    probe_result: Any
    dissent_ids: List[str]    # Dissents this helps resolve
    link_strength: float      # 0.0-1.0 how strongly it resolves
    link_reasoning: str       # Why this probe resolves these dissents


# =============================================================================
# COUNCIL VOTING
# =============================================================================

@dataclass
class CouncilVote:
    """A vote from an LLM council member"""
    model: str
    provider: str             # anthropic, google, openai
    decision: str             # APPROVE | REJECT | DEFER_TO_HUMAN (async_council verdict envelope);
                              # the aggregator also counts APPROVE_WITH_NITS / APPROVE_WITH_CONDITIONS as approvals.
                              # Model output is not validated: any other value is off-scale and is listed in
                              # final_recommendation["off_scale"] as "<provider>:<decision>"
    reasoning: str
    confidence: float
    dissenting_points: List[str] = field(default_factory=list)
    # errored=True marks a provider seat that failed/timed out (set ONLY by
    # _create_error_vote). It is the authoritative discriminator for the
    # responding-vs-errored partition (ADR-0054): the aggregator excludes these
    # seats from all consensus math. Never parsed from provider output.
    errored: bool = False
    # Seat telemetry, set only by the council's seat wrapper; never parsed from
    # provider output. `fault_label` is a closed-vocabulary label from
    # AsyncCouncil._ERROR_LABELS (errored seats only). `finish_reason` is the
    # reply's finish reason mapped onto AsyncCouncil's closed _FINISH_REASONS,
    # or None when the seat got no reply (a timeout, or an HTTP error status
    # raised before the reply body is read). `retried` marks a seat that
    # was called a second time. `recovered` marks a seat that answered on that
    # second call. `first_fault_label` and `first_finish_reason` describe the
    # first attempt of a seat that was retried.
    fault_label: Optional[str] = None
    finish_reason: Optional[str] = None
    recovered: bool = False
    retried: bool = False
    first_fault_label: Optional[str] = None
    first_finish_reason: Optional[str] = None
    # Seat provenance, set only by the council's seat wrapper. `role`,
    # `registry_key` and `requested_model` say what the seat asked for;
    # `served_model`, `served_provider` and `generation_id` are what the gateway
    # reported in the reply (None when the seat got no reply or the reply
    # omitted the field). `findings` holds the tagged findings of a gate seat.
    role: Optional[str] = None
    registry_key: Optional[str] = None
    requested_model: Optional[str] = None
    served_model: Optional[str] = None
    served_provider: Optional[str] = None
    generation_id: Optional[str] = None
    findings: List[Dict[str, Any]] = field(default_factory=list)
    # Every attempt the seat made, in order, set only by the council's seat
    # wrapper. Each holds what that attempt's reply reported (served model,
    # served provider, generation id, finish reason), the fault label it ended
    # with, and, in a gate council, its served-model and served-provider match
    # results. Empty for a vote no seat wrapper built.
    attempts: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class CouncilDeliberation:
    """Result of full LLM council deliberation"""
    votes: List[CouncilVote]
    consensus: str            # UNANIMOUS_APPROVE | UNANIMOUS_REJECT | MAJORITY_APPROVE | MAJORITY_REJECT | SPLIT | NO_QUORUM
                              # | UNANIMOUS_<TOKEN> (every responding seat returned the same other decision; never an approval)
                              # | UNANIMOUS_OFF_SCALE (UNANIMOUS_OFF_SCALE replaces it when the token is not
                              #   1-40 characters of A-Z and underscore starting with a letter, contains
                              #   APPROVE or REJECT, or is AUTO_EXECUTE or EXECUTE_WITH_MONITORING)
    consensus_confidence: float
    key_agreements: List[str]
    key_disagreements: List[str]
    final_recommendation: Dict[str, Any]
    dissent_count: int = 0
    confidence_adjustment: float = 0.0

    @property
    def errored_seats(self) -> str:
        """Errored provider seats over total, e.g. '1/3' (ADR-0054).

        Requires .votes to retain ALL seats (responding + errored); the
        aggregator must never reassign votes to a responding-only list.
        """
        total = len(self.votes)
        errored = sum(1 for v in self.votes if getattr(v, "errored", False))
        return f"{errored}/{total}"

    @property
    def degraded(self) -> bool:
        """True iff any provider seat errored (partial availability, ADR-0054)."""
        return any(getattr(v, "errored", False) for v in self.votes)

    @property
    def conditioned(self) -> bool:
        """True iff a responding seat returned APPROVE_WITH_CONDITIONS.

        Reads `final_recommendation["conditioned"]`, whose `conditions` key holds
        each such seat's points, keyed by seat. A conditioned approval never
        routes to AUTO_EXECUTE. The points are untrusted model text.
        """
        return bool(self.final_recommendation.get("conditioned", False))

    @property
    def nits(self) -> bool:
        """True iff a responding seat returned APPROVE_WITH_NITS.

        Reads `final_recommendation["nits"]`, whose `nit_items` key holds each such
        seat's points, keyed by seat. Nits never change the route.
        """
        return bool(self.final_recommendation.get("nits", False))

    @property
    def has_critical_dissent(self) -> bool:
        """Check if any critical dissents exist among RESPONDING seats.

        Errored seats are excluded (ADR-0054): a provider outage's exception
        string is not a substantive dissent, so it can never trip this flag.
        """
        return self.dissent_count > 0 and any(
            "critical" in d.lower() or "blocker" in d.lower()
            for v in self.votes if not getattr(v, "errored", False)
            for d in v.dissenting_points
        )


# =============================================================================
# SEMANTIC TRACE
# =============================================================================

@dataclass
class SemanticTrace:
    """A single step in the semantic reasoning trace"""
    loop_number: int
    step: str                 # observe, reason, hypothesize, test, conclude
    thought: str              # What the system is thinking
    observation: str          # What it observed
    confidence: float         # Current confidence
    confidence_impact: float = 0.0  # Change from this step
    evidence_weight: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ReasoningLog:
    """Complete reasoning log for a loop iteration"""
    loop_number: int
    phase: str                # observe, reason, execute, verify
    traces: List[SemanticTrace]
    insights: List[str]
    recommendations: List[str]
    self_critique: str
    improvement_actions: List[str]
    confidence_delta: float
    running_confidence: float = 0.5
    probes_performed: int = 0
    dissents_resolved: int = 0


# =============================================================================
# SANDBOX & VERIFICATION
# =============================================================================

@dataclass
class InvariantCheck:
    """An invariant to check before and after changes"""
    name: str
    description: str
    check_code: str           # Code to evaluate the invariant
    before_value: Any = None
    after_value: Any = None
    held: Optional[bool] = None  # Did the invariant hold?


@dataclass
class SandboxResult:
    """Result of sandbox/dry-run execution"""
    success: bool
    sandbox_path: str
    operations_applied: int
    invariants_checked: int
    invariants_held: int
    errors_before: int
    errors_after: int
    value_changes: Dict[str, Tuple[Any, Any]]  # key -> (before, after)
    issues: List[str]
    verification_passed: bool
    rollback_needed: bool


# =============================================================================
# MENTAL MODEL (GENERIC)
# =============================================================================

@dataclass
class EntityRole:
    """
    Role/purpose of an entity in the system.

    Generic version - can represent:
    - Excel sheets
    - PDF sections
    - Code modules
    - Database tables
    """
    name: str
    entity_type: str          # sheet, section, module, table
    purpose: str              # data_source, calculation, summary, etc.
    properties: Dict[str, Any] = field(default_factory=dict)
    feeds_into: List[str] = field(default_factory=list)
    fed_by: List[str] = field(default_factory=list)


@dataclass
class DataFlowEdge:
    """Edge in the data flow graph"""
    source: str               # Entity identifier
    target: str               # Entity identifier
    relationship: str         # lookup, aggregation, direct, calculated
    weight: float = 1.0       # Strength of relationship
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MentalModel:
    """
    Abstract mental model of a domain.

    This is the "understanding" that allows context-aware resolution
    instead of blind probing.
    """
    domain: str               # excel, pdf, code, etc.
    entities: Dict[str, EntityRole]
    data_flows: List[DataFlowEdge]
    key_patterns: Dict[str, Any]  # Domain-specific patterns
    propagation_map: Dict[str, List[str]]  # source -> affected targets

    def get_affected_by(self, entity: str) -> List[str]:
        """Get all entities affected by changes to the given entity"""
        return self.propagation_map.get(entity, [])

    def explain_flow(self, source: str, target: str) -> Optional[str]:
        """Explain how data flows from source to target"""
        for edge in self.data_flows:
            if edge.source == source and edge.target == target:
                return f"{source} -> {target} via {edge.relationship}"
        return None


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Enums
    'DissentStatus',
    'DissentSeverity',
    'ResolutionStatus',
    'ProbeType',
    'ApprovalStatus',
    'OperationType',
    # Confidence
    'ConfidenceUpdate',
    'ConfidenceTrajectory',
    # Dissent
    'DissentPoint',
    'ResolutionAttempt',
    # Probes
    'ProbeResult',
    'SynthesizedProbe',
    # Semantic Bridge
    'SemanticBridgeLink',
    # Council
    'CouncilVote',
    'CouncilDeliberation',
    # Traces
    'SemanticTrace',
    'ReasoningLog',
    # Sandbox
    'InvariantCheck',
    'SandboxResult',
    # Mental Model
    'EntityRole',
    'DataFlowEdge',
    'MentalModel',
]
