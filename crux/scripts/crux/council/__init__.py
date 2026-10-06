"""
crux.council - Multi-model deliberation and consensus.

Two flavors are exported:

- council.py: Synchronous council with arbiter-based synthesis.
- async_council.py: Parallel async council (3x speedup over serial calls).
"""

from .council import (
    council_vote,
    quick_council,
    get_opinion,
    synthesize_opinions,
    VotingMethod,
    Opinion,
    CouncilDecision,
    DEFAULT_COUNCIL,
    MODEL_WEIGHTS,
)

# Async Council - Parallel LLM execution
from .async_council import (
    AsyncCouncil,
    AsyncVisualCouncil,
    AsyncCouncilConfig,
    VisualVoteResult,
    create_async_council,
    create_visual_council,
)

__all__ = [
    # Synchronous Council
    "council_vote",
    "quick_council",
    "get_opinion",
    "synthesize_opinions",
    "VotingMethod",
    "Opinion",
    "CouncilDecision",
    "DEFAULT_COUNCIL",
    "MODEL_WEIGHTS",
    # Async Council (3x faster)
    "AsyncCouncil",
    "AsyncVisualCouncil",
    "AsyncCouncilConfig",
    "VisualVoteResult",
    "create_async_council",
    "create_visual_council",
]
