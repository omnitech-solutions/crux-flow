"""
bridge.py - CONNECT PROBES / DISSENTS / RESOLUTIONS

The Semantic Bridge prevents redundant verification by connecting:
- Probes: Verification tools and their results
- Dissents: Concerns raised by council
- Resolutions: How concerns were addressed

When a probe answers a question, the bridge links that answer
to all related dissents. This prevents asking about verified facts.

This is DOMAIN-AGNOSTIC - keyword groups are configurable.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from ..core.data_classes import (
    DissentPoint,
    ProbeResult,
    SemanticBridgeLink,
)

logger = logging.getLogger(__name__)


@dataclass
class BridgeStatistics:
    """Statistics about the semantic bridge state"""

    total_probes: int = 0
    total_dissents: int = 0
    total_links: int = 0
    dissents_answered: int = 0
    dissents_unanswered: int = 0
    coverage: float = 0.0


class SemanticBridge:
    """
    Semantic Bridge - Connect Probes / Dissents / Resolutions

    The semantic bridge is crucial for efficiency. Without it,
    the system might:
    - Ask the same question multiple times
    - Fail to recognize when a probe answers a dissent
    - Not build on previous verification work

    With the bridge:
    - Probes are linked to dissents they answer
    - Verified facts are remembered
    - Coverage is tracked

    Usage:
        bridge = SemanticBridge()
        bridge.add_keyword_group("insertion", ["insert", "row", "append"])

        bridge.register_probe("probe_1", probe_result)
        bridge.register_dissent("d_1", dissent_point)

        if bridge.is_answered_by_probe("d_1"):
            answer = bridge.get_answer_for_dissent("d_1")
    """

    # Default keyword groups (domain-agnostic)
    DEFAULT_KEYWORD_GROUPS = {
        "data": ["data", "value", "content", "item"],
        "count": ["count", "number", "total", "amount"],
        "exists": ["exist", "missing", "find", "present", "absent"],
        "valid": ["valid", "invalid", "error", "correct", "wrong"],
        "type": ["type", "format", "schema", "structure"],
        "change": ["change", "update", "modify", "alter", "before", "after"],
        "backup": ["backup", "rollback", "restore", "snapshot", "undo"],
        "propagate": ["propagate", "flow", "cascade", "affect", "impact"],
        "safe": ["safe", "danger", "risk", "harm", "protect"],
    }

    def __init__(self, keyword_groups: Optional[Dict[str, List[str]]] = None):
        self.links: List[SemanticBridgeLink] = []
        self.probe_index: Dict[str, ProbeResult] = {}
        self.dissent_index: Dict[str, DissentPoint] = {}

        # Keyword groups for semantic matching
        self.keyword_groups = keyword_groups or self.DEFAULT_KEYWORD_GROUPS.copy()

        # Minimum link strength to consider "answered"
        self.answer_threshold = 0.5

    def add_keyword_group(self, name: str, keywords: List[str]):
        """Add or update a keyword group for semantic matching"""
        self.keyword_groups[name] = keywords

    def set_answer_threshold(self, threshold: float):
        """Set minimum link strength to consider a dissent answered"""
        self.answer_threshold = max(0.0, min(1.0, threshold))

    def register_probe(self, probe_id: str, result: ProbeResult):
        """
        Register a probe result and build links to existing dissents.
        """
        self.probe_index[probe_id] = result
        self._build_links_for_probe(probe_id, result)
        logger.debug(f"SemanticBridge: Registered probe {probe_id}")

    def register_dissent(self, dissent_id: str, dissent: DissentPoint):
        """
        Register a dissent and build links to existing probes.
        """
        self.dissent_index[dissent_id] = dissent
        self._build_links_for_dissent(dissent_id, dissent)
        logger.debug(f"SemanticBridge: Registered dissent {dissent_id}")

    def _get_keywords(self, text: str) -> Set[str]:
        """Extract semantic keyword groups from text"""
        text_lower = text.lower()
        keywords = set()

        for group, terms in self.keyword_groups.items():
            if any(term in text_lower for term in terms):
                keywords.add(group)

        return keywords

    def _calculate_link_strength(
        self,
        keywords1: Set[str],
        keywords2: Set[str],
    ) -> float:
        """Calculate semantic link strength between two keyword sets"""
        if not keywords1 or not keywords2:
            return 0.0

        overlap = keywords1 & keywords2
        if not overlap:
            return 0.0

        # Jaccard-like similarity with boost
        union = keywords1 | keywords2
        similarity = len(overlap) / len(union)

        # Boost for strong overlap
        boost = min(len(overlap) * 0.2, 0.5)

        return min(similarity + boost, 1.0)

    def _build_links_for_probe(self, probe_id: str, result: ProbeResult):
        """Build links from a new probe to existing dissents"""
        # Get keywords from probe ID and target
        probe_text = f"{probe_id} {result.target} {result.evidence}"
        probe_keywords = self._get_keywords(probe_text)

        for dissent_id, dissent in self.dissent_index.items():
            dissent_keywords = self._get_keywords(dissent.content)

            strength = self._calculate_link_strength(probe_keywords, dissent_keywords)

            if strength > 0:
                overlap = probe_keywords & dissent_keywords
                link = SemanticBridgeLink(
                    probe_id=probe_id,
                    probe_result=result.result,
                    dissent_ids=[dissent_id],
                    link_strength=strength,
                    link_reasoning=f"Matched on: {', '.join(sorted(overlap))}",
                )
                self.links.append(link)

    def _build_links_for_dissent(self, dissent_id: str, dissent: DissentPoint):
        """Build links from a new dissent to existing probes"""
        dissent_keywords = self._get_keywords(dissent.content)

        for probe_id, result in self.probe_index.items():
            probe_text = f"{probe_id} {result.target} {result.evidence}"
            probe_keywords = self._get_keywords(probe_text)

            strength = self._calculate_link_strength(probe_keywords, dissent_keywords)

            if strength > 0:
                overlap = probe_keywords & dissent_keywords
                link = SemanticBridgeLink(
                    probe_id=probe_id,
                    probe_result=result.result,
                    dissent_ids=[dissent_id],
                    link_strength=strength,
                    link_reasoning=f"Matched on: {', '.join(sorted(overlap))}",
                )
                self.links.append(link)

    def is_answered_by_probe(self, dissent_id: str) -> bool:
        """Check if a dissent is sufficiently answered by any probe"""
        for link in self.links:
            if dissent_id in link.dissent_ids and link.link_strength >= self.answer_threshold:
                return True
        return False

    def get_answer_for_dissent(self, dissent_id: str) -> Optional[Tuple[str, Any, float]]:
        """
        Get the best probe answer for a dissent.

        Returns:
            Tuple of (probe_id, probe_result, link_strength) or None
        """
        best_link = None
        best_strength = 0.0

        for link in self.links:
            if dissent_id in link.dissent_ids and link.link_strength > best_strength:
                best_link = link
                best_strength = link.link_strength

        if best_link:
            return (best_link.probe_id, best_link.probe_result, best_link.link_strength)
        return None

    def get_unanswered_dissents(self) -> List[str]:
        """Get dissent IDs not sufficiently answered by any probe"""
        answered = set()
        for link in self.links:
            if link.link_strength >= self.answer_threshold:
                answered.update(link.dissent_ids)

        return [d for d in self.dissent_index.keys() if d not in answered]

    def get_dissents_for_probe(self, probe_id: str) -> List[str]:
        """Get all dissent IDs that a probe helps answer"""
        dissent_ids = []
        for link in self.links:
            if link.probe_id == probe_id and link.link_strength >= self.answer_threshold:
                dissent_ids.extend(link.dissent_ids)
        return list(set(dissent_ids))

    def get_coverage_report(self) -> Dict[str, Any]:
        """Get detailed coverage report"""
        answered = set()
        for link in self.links:
            if link.link_strength >= self.answer_threshold:
                answered.update(link.dissent_ids)

        unanswered = [d for d in self.dissent_index.keys() if d not in answered]

        return {
            "total_probes": len(self.probe_index),
            "total_dissents": len(self.dissent_index),
            "total_links": len(self.links),
            "dissents_answered": len(answered),
            "dissents_unanswered": len(unanswered),
            "unanswered_ids": unanswered,
            "coverage": len(answered) / max(len(self.dissent_index), 1),
            "answer_threshold": self.answer_threshold,
        }

    def get_stats(self) -> BridgeStatistics:
        """Get summary statistics"""
        report = self.get_coverage_report()
        return BridgeStatistics(
            total_probes=report["total_probes"],
            total_dissents=report["total_dissents"],
            total_links=report["total_links"],
            dissents_answered=report["dissents_answered"],
            dissents_unanswered=report["dissents_unanswered"],
            coverage=report["coverage"],
        )

    def clear(self):
        """Clear all registered probes, dissents, and links"""
        self.links.clear()
        self.probe_index.clear()
        self.dissent_index.clear()


# =============================================================================
# FACTORY FUNCTION
# =============================================================================


def create_semantic_bridge(
    keyword_groups: Optional[Dict[str, List[str]]] = None,
    answer_threshold: float = 0.5,
) -> SemanticBridge:
    """
    Create a configured semantic bridge.

    Args:
        keyword_groups: Custom keyword groups for semantic matching
        answer_threshold: Minimum link strength to consider answered

    Returns:
        Configured SemanticBridge
    """
    bridge = SemanticBridge(keyword_groups)
    bridge.set_answer_threshold(answer_threshold)
    return bridge


__all__ = [
    "SemanticBridge",
    "BridgeStatistics",
    "create_semantic_bridge",
]
