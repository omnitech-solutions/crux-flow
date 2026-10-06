"""
crux.srde - Self-Resolving Dissent Engine.

Resolves council
dissents without human intervention via context-aware resolution,
probe cross-reference, and pattern-based resolvers.
"""

from .srde import (
    SelfResolvingDissentEngine,
    ContextResolver,
    PatternResolver,
    create_srde,
)

__all__ = [
    "SelfResolvingDissentEngine",
    "ContextResolver",
    "PatternResolver",
    "create_srde",
]
