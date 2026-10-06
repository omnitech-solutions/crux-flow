"""
crux.identity - Cross-agent learning and self-knowledge.

Persistent
identities (GUID) with hints / patterns / warnings / domains that can be
learned across sessions.

Storage default: ~/.crux/identity/ (user-extensible location).
"""

from .identity import (
    DEFAULT_REGISTRY_DIR,
    Hint,
    Identity,
    KnowledgeStore,
    Learner,
    Pattern,
    Warning_,
    get_or_create_identity,
)

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
