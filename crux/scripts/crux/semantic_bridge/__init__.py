"""
crux.semantic_bridge - Probe / dissent / resolution linker.

Prevents redundant verification by connecting probes to the dissents
they answer.
"""

from .bridge import (
    SemanticBridge,
    BridgeStatistics,
    create_semantic_bridge,
)

__all__ = [
    "SemanticBridge",
    "BridgeStatistics",
    "create_semantic_bridge",
]
