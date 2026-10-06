"""
identity.py - CRUX IDENTITY AND SELF-KNOWLEDGE

Each crux agent has:
1. A unique GUID for identification
2. Self-knowledge: hints, patterns, warnings learned from sessions
3. The ability to learn from other agent identities

This enables:
- Cross-pollination of learnings between agent instances
- Persistent knowledge that survives sessions
- Evolution of each agent's capabilities over time

Identities are stored under `~/.crux/identity/` (a user-extensible
location).
"""

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


# Default storage path under the user's crux home.
# Honors CRUX_HOME if set (consistent with crux_env), otherwise
# falls back to ~/.crux/identity/.
def _default_registry_dir() -> Path:
    base = Path(os.environ.get("CRUX_HOME") or os.path.expanduser("~/.crux"))
    return base / "identity"


DEFAULT_REGISTRY_DIR = _default_registry_dir()


@dataclass
class Hint:
    """A hint learned by an agent."""

    context: str
    hint: str
    importance: str  # critical, useful, minor
    source: str  # session_id or "learned_from:{guid}"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    times_useful: int = 0  # Track how often this hint helped


@dataclass
class Pattern:
    """A pattern noticed by an agent."""

    pattern: str
    domain: str  # e.g., "pdf_parsing", "code_review", "api_integration"
    confidence: float  # How sure are we this pattern holds
    evidence_count: int  # How many times we've seen this
    source: str


@dataclass
class Warning_:
    """A warning / gotcha learned by an agent.

    Trailing underscore avoids collision with the builtin `Warning` type.
    """

    warning: str
    context: str
    severity: str  # critical, high, medium, low
    source: str
    times_triggered: int = 0


@dataclass
class Identity:
    """
    The identity and self-knowledge of an agent instance.

    Each agent maintains knowledge about:
    - Who it is (GUID, name, purpose)
    - What it's learned (hints, patterns, warnings)
    - Where it came from (parent agent, if any)
    - What it's good at (domains, capabilities)
    """

    guid: str
    name: str
    purpose: str  # What this agent was created for
    created_at: str
    parent_guid: Optional[str] = None  # If spawned from another agent

    # Self-knowledge
    hints: List[Hint] = field(default_factory=list)
    patterns: List[Pattern] = field(default_factory=list)
    warnings: List[Warning_] = field(default_factory=list)

    # Domain expertise
    domains: List[str] = field(default_factory=list)
    capabilities: Dict[str, float] = field(default_factory=dict)

    # Lineage
    children: List[str] = field(default_factory=list)
    learned_from: List[str] = field(default_factory=list)

    # Stats
    sessions_completed: int = 0
    tasks_completed: int = 0
    total_loops: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "guid": self.guid,
            "name": self.name,
            "purpose": self.purpose,
            "created_at": self.created_at,
            "parent_guid": self.parent_guid,
            "hints": [
                {
                    "context": h.context,
                    "hint": h.hint,
                    "importance": h.importance,
                    "source": h.source,
                    "created_at": h.created_at,
                    "times_useful": h.times_useful,
                }
                for h in self.hints
            ],
            "patterns": [
                {
                    "pattern": p.pattern,
                    "domain": p.domain,
                    "confidence": p.confidence,
                    "evidence_count": p.evidence_count,
                    "source": p.source,
                }
                for p in self.patterns
            ],
            "warnings": [
                {
                    "warning": w.warning,
                    "context": w.context,
                    "severity": w.severity,
                    "source": w.source,
                    "times_triggered": w.times_triggered,
                }
                for w in self.warnings
            ],
            "domains": self.domains,
            "capabilities": self.capabilities,
            "children": self.children,
            "learned_from": self.learned_from,
            "sessions_completed": self.sessions_completed,
            "tasks_completed": self.tasks_completed,
            "total_loops": self.total_loops,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Identity":
        identity = cls(
            guid=data["guid"],
            name=data["name"],
            purpose=data["purpose"],
            created_at=data["created_at"],
            parent_guid=data.get("parent_guid"),
            domains=data.get("domains", []),
            capabilities=data.get("capabilities", {}),
            children=data.get("children", []),
            learned_from=data.get("learned_from", []),
            sessions_completed=data.get("sessions_completed", 0),
            tasks_completed=data.get("tasks_completed", 0),
            total_loops=data.get("total_loops", 0),
        )

        for h in data.get("hints", []):
            identity.hints.append(
                Hint(
                    context=h["context"],
                    hint=h["hint"],
                    importance=h["importance"],
                    source=h["source"],
                    created_at=h.get("created_at", ""),
                    times_useful=h.get("times_useful", 0),
                )
            )

        for p in data.get("patterns", []):
            identity.patterns.append(
                Pattern(
                    pattern=p["pattern"],
                    domain=p["domain"],
                    confidence=p["confidence"],
                    evidence_count=p["evidence_count"],
                    source=p["source"],
                )
            )

        for w in data.get("warnings", []):
            identity.warnings.append(
                Warning_(
                    warning=w["warning"],
                    context=w["context"],
                    severity=w["severity"],
                    source=w["source"],
                    times_triggered=w.get("times_triggered", 0),
                )
            )

        return identity

    def add_hint(self, context: str, hint: str, importance: str, source: str):
        """Add a new hint to self-knowledge."""
        for existing in self.hints:
            if existing.hint.lower() == hint.lower():
                existing.times_useful += 1
                return

        self.hints.append(
            Hint(
                context=context,
                hint=hint,
                importance=importance,
                source=source,
            )
        )

    def add_pattern(self, pattern: str, domain: str, confidence: float, source: str):
        """Add or update a pattern."""
        for existing in self.patterns:
            if existing.pattern.lower() == pattern.lower():
                existing.evidence_count += 1
                existing.confidence = min(1.0, existing.confidence + 0.1)
                return

        self.patterns.append(
            Pattern(
                pattern=pattern,
                domain=domain,
                confidence=confidence,
                evidence_count=1,
                source=source,
            )
        )

        if domain not in self.domains:
            self.domains.append(domain)

    def add_warning(self, warning: str, context: str, severity: str, source: str):
        """Add a warning."""
        for existing in self.warnings:
            if existing.warning.lower() == warning.lower():
                existing.times_triggered += 1
                return

        self.warnings.append(
            Warning_(
                warning=warning,
                context=context,
                severity=severity,
                source=source,
            )
        )

    def get_relevant_hints(self, context: str, limit: int = 5) -> List[Hint]:
        """Get hints relevant to a given context."""
        context_lower = context.lower()
        scored = []

        for hint in self.hints:
            score = 0
            if any(word in hint.context.lower() for word in context_lower.split()):
                score += 2
            if hint.importance == "critical":
                score += 3
            elif hint.importance == "useful":
                score += 1
            score += hint.times_useful * 0.5

            if score > 0:
                scored.append((score, hint))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [h for _, h in scored[:limit]]

    def get_relevant_warnings(self, context: str) -> List[Warning_]:
        """Get warnings relevant to a given context."""
        context_lower = context.lower()
        relevant = []

        for warning in self.warnings:
            if any(word in warning.context.lower() for word in context_lower.split()):
                relevant.append(warning)
            elif warning.severity == "critical":
                relevant.append(warning)  # Always include critical warnings

        return relevant


class KnowledgeStore:
    """
    Persistent storage for agent identities and knowledge.

    Each identity is stored as:
    {base_dir}/{guid}/identity.json

    Default base_dir is ~/.crux/identity/ (overridable).
    """

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = Path(base_dir) if base_dir else _default_registry_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def create_identity(
        self,
        name: str,
        purpose: str,
        parent_guid: Optional[str] = None,
    ) -> Identity:
        """Create a new identity with unique GUID."""
        guid = str(uuid.uuid4())[:8]  # Short GUID for readability

        identity = Identity(
            guid=guid,
            name=name,
            purpose=purpose,
            created_at=datetime.now().isoformat(),
            parent_guid=parent_guid,
        )

        # If spawned from parent, inherit some knowledge
        if parent_guid:
            parent = self.load_identity(parent_guid)
            if parent:
                parent.children.append(guid)
                self.save_identity(parent)

                # Inherit critical hints and patterns
                for hint in parent.hints:
                    if hint.importance == "critical":
                        identity.add_hint(
                            context=hint.context,
                            hint=hint.hint,
                            importance=hint.importance,
                            source=f"inherited_from:{parent_guid}",
                        )

                for pattern in parent.patterns:
                    if pattern.confidence > 0.7:
                        identity.add_pattern(
                            pattern=pattern.pattern,
                            domain=pattern.domain,
                            confidence=pattern.confidence * 0.8,
                            source=f"inherited_from:{parent_guid}",
                        )

        self.save_identity(identity)
        return identity

    def save_identity(self, identity: Identity):
        """Save an identity to disk."""
        identity_dir = self.base_dir / identity.guid
        identity_dir.mkdir(parents=True, exist_ok=True)

        identity_path = identity_dir / "identity.json"
        with open(identity_path, "w") as f:
            json.dump(identity.to_dict(), f, indent=2)

    def load_identity(self, guid: str) -> Optional[Identity]:
        """Load an identity from disk."""
        identity_path = self.base_dir / guid / "identity.json"

        if not identity_path.exists():
            return None

        with open(identity_path) as f:
            data = json.load(f)

        return Identity.from_dict(data)

    def list_all(self) -> List[Dict[str, Any]]:
        """List all registered identities."""
        out = []

        if not self.base_dir.exists():
            return out

        for guid_dir in self.base_dir.iterdir():
            if guid_dir.is_dir():
                identity = self.load_identity(guid_dir.name)
                if identity:
                    out.append(
                        {
                            "guid": identity.guid,
                            "name": identity.name,
                            "purpose": identity.purpose,
                            "domains": identity.domains,
                            "hints_count": len(identity.hints),
                            "patterns_count": len(identity.patterns),
                            "sessions_completed": identity.sessions_completed,
                        }
                    )

        return out

    def find_expert(self, domain: str) -> Optional[Identity]:
        """Find an identity with expertise in a given domain."""
        best_match = None
        best_confidence = 0

        if not self.base_dir.exists():
            return None

        for guid_dir in self.base_dir.iterdir():
            if guid_dir.is_dir():
                identity = self.load_identity(guid_dir.name)
                if identity and domain in identity.capabilities:
                    if identity.capabilities[domain] > best_confidence:
                        best_confidence = identity.capabilities[domain]
                        best_match = identity

        return best_match


class Learner:
    """
    Tool for an identity to learn from another identity.

    Usage:
        learner = Learner(store)
        learner.learn_from(my_identity, teacher_guid)
    """

    def __init__(self, store: KnowledgeStore):
        self.store = store

    def learn_from(
        self,
        student: Identity,
        teacher_guid: str,
        domains: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Learn from another identity.

        Args:
            student: The identity that's learning
            teacher_guid: GUID of the identity to learn from
            domains: Optional list of domains to learn about (None = all)

        Returns:
            Summary of what was learned
        """
        teacher = self.store.load_identity(teacher_guid)
        if not teacher:
            return {"error": f"identity {teacher_guid} not found"}

        learned = {
            "hints_learned": 0,
            "patterns_learned": 0,
            "warnings_learned": 0,
            "domains_acquired": [],
        }

        for hint in teacher.hints:
            if domains:
                hint_domains = [d for d in domains if d.lower() in hint.context.lower()]
                if not hint_domains:
                    continue

            student.add_hint(
                context=hint.context,
                hint=hint.hint,
                importance=hint.importance,
                source=f"learned_from:{teacher_guid}",
            )
            learned["hints_learned"] += 1

        for pattern in teacher.patterns:
            if domains and pattern.domain not in domains:
                continue

            student.add_pattern(
                pattern=pattern.pattern,
                domain=pattern.domain,
                confidence=pattern.confidence * 0.9,  # Slight discount
                source=f"learned_from:{teacher_guid}",
            )
            learned["patterns_learned"] += 1

            if pattern.domain not in student.domains:
                learned["domains_acquired"].append(pattern.domain)

        for warning in teacher.warnings:
            if domains:
                warning_domains = [d for d in domains if d.lower() in warning.context.lower()]
                if not warning_domains and warning.severity != "critical":
                    continue

            student.add_warning(
                warning=warning.warning,
                context=warning.context,
                severity=warning.severity,
                source=f"learned_from:{teacher_guid}",
            )
            learned["warnings_learned"] += 1

        if teacher_guid not in student.learned_from:
            student.learned_from.append(teacher_guid)

        self.store.save_identity(student)

        return learned

    def extract_learnings_from_session(
        self,
        identity: Identity,
        session_dir: Path,
    ) -> Dict[str, Any]:
        """
        Extract learnings from a completed session's reasoning logs.
        """
        extracted = {
            "hints_added": 0,
            "patterns_added": 0,
            "warnings_added": 0,
        }

        reasoning_dir = session_dir / "reasoning"
        if not reasoning_dir.exists():
            return extracted

        for log_file in reasoning_dir.glob("loop_*.json"):
            with open(log_file) as f:
                try:
                    log = json.load(f)
                except json.JSONDecodeError:
                    continue

            session_id = log.get("session_id", "unknown")
            reflection = log.get("self_reflection", {})

            for hint in reflection.get("future_hints", []):
                identity.add_hint(
                    context=hint.get("context", ""),
                    hint=hint.get("hint", ""),
                    importance=hint.get("importance", "useful"),
                    source=f"session:{session_id}",
                )
                extracted["hints_added"] += 1

            for pattern in reflection.get("patterns_noticed", []):
                domain = self._infer_domain(pattern)
                identity.add_pattern(
                    pattern=pattern,
                    domain=domain,
                    confidence=0.6,
                    source=f"session:{session_id}",
                )
                extracted["patterns_added"] += 1

            for warning in reflection.get("warnings", []):
                identity.add_warning(
                    warning=warning,
                    context="general",
                    severity="medium",
                    source=f"session:{session_id}",
                )
                extracted["warnings_added"] += 1

        identity.sessions_completed += 1
        self.store.save_identity(identity)

        return extracted

    def _infer_domain(self, text: str) -> str:
        """Infer domain from text content."""
        text_lower = text.lower()

        domain_keywords = {
            "pdf_parsing": ["pdf", "page", "document", "ocr", "text extraction"],
            "code_review": ["code", "function", "class", "refactor", "test"],
            "api_integration": ["api", "endpoint", "request", "response", "webhook"],
            "database": ["database", "query", "sql", "table", "migration"],
            "frontend": ["react", "component", "css", "html", "ui"],
            "devops": ["deploy", "ci", "docker", "kubernetes", "pipeline"],
        }

        for domain, keywords in domain_keywords.items():
            if any(kw in text_lower for kw in keywords):
                return domain

        return "general"


def get_or_create_identity(
    store: KnowledgeStore,
    name: str,
    purpose: str,
    guid: Optional[str] = None,
) -> Identity:
    """
    Get an existing identity or create a new one.

    Args:
        store: The knowledge store
        name: Name for new identity (ignored if loading existing)
        purpose: Purpose for new identity (ignored if loading existing)
        guid: If provided, try to load existing identity with this GUID

    Returns:
        Identity
    """
    if guid:
        existing = store.load_identity(guid)
        if existing:
            return existing

    return store.create_identity(name=name, purpose=purpose)


__all__ = [
    "Hint",
    "Pattern",
    "Warning_",
    "Identity",
    "KnowledgeStore",
    "Learner",
    "get_or_create_identity",
    "DEFAULT_REGISTRY_DIR",
]
