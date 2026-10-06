#!/usr/bin/env python3
"""
council.py - Multi-Model Deliberation and Consensus

A council pattern where multiple LLMs provide opinions on a decision, which
are then synthesized.
"""

import json
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

from ..core.llm_caller import (
    validated_confidence,
    call_model,
    get_default_model,
    get_default_models,
)
from ..core.tracer import Phase, Tracer, get_tracer


class VotingMethod(Enum):
    """Methods for aggregating council votes"""

    MAJORITY = "majority"  # Simple majority wins
    WEIGHTED = "weighted"  # Weighted by model capability
    CONSENSUS = "consensus"  # All must agree
    ARBITER = "arbiter"  # Designated model makes final call


@dataclass
class Opinion:
    """A single model's opinion on a question."""

    model: str
    position: str  # The model's stance/answer
    reasoning: str  # Why they hold this position
    confidence: float  # 0-1 confidence score
    considerations: List[str]  # Key factors considered


@dataclass
class CouncilDecision:
    """The synthesized decision from the council."""

    question: str
    opinions: List[Opinion]
    synthesis: str  # Synthesized view
    final_decision: str  # The actual decision
    voting_method: VotingMethod
    agreement_level: float  # 0-1 how much models agreed
    dissenting_points: List[str]  # Where models disagreed


def _get_default_council() -> List[str]:
    """Resolve at call time, not import time, so cache invalidation works."""
    return get_default_models("council_default")


# Public alias kept stable for callers that historically imported DEFAULT_COUNCIL
# from this module. Resolves lazily — call _get_default_council() to evaluate.
DEFAULT_COUNCIL = _get_default_council

# Model weights for weighted voting. Names are roles resolved against the
# router config; missing roles default to weight 1.0.
MODEL_WEIGHTS: Dict[str, float] = {
    "anthropic_top": 1.5,
    "google_top": 1.3,
    "openai_top": 1.4,
}


def _get_model_weight(model: str) -> float:
    """Weight models by role for weighted voting."""
    roles = {get_default_model(r): w for r, w in MODEL_WEIGHTS.items()}
    return roles.get(model, 1.0)


def get_opinion(
    model: str,
    question: str,
    context: str = "",
) -> Opinion:
    """Get a single model's opinion on a question."""
    prompt = f"""You are participating in a council deliberation on an important technical decision.

## Question
{question}

## Context
{context if context else "No additional context provided."}

## Your Task
Provide your professional opinion. Be specific and actionable.

Respond in this JSON format:
```json
{{
  "position": "Your clear stance/answer in 1-2 sentences",
  "reasoning": "Detailed explanation of why you hold this position",
  "confidence": 0.85,
  "considerations": [
    "Key factor 1 you considered",
    "Key factor 2 you considered",
    "Key factor 3 you considered"
  ]
}}
```

Be honest about uncertainty. If you're not confident, say so.
Focus on practical, implementable recommendations.
"""

    system = """You are a senior technical architect participating in a design council.
Give thoughtful, nuanced opinions. Consider tradeoffs. Be specific about implementation.
Always output valid JSON."""

    response = call_model(model, prompt, system)

    # Parse response
    try:
        json_start = response.find("{")
        json_end = response.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            data = json.loads(response[json_start:json_end])
            return Opinion(
                model=model,
                position=data.get("position", "No clear position"),
                reasoning=data.get("reasoning", response),
                confidence=validated_confidence(data.get("confidence", 0.7)),
                considerations=data.get("considerations", []),
            )
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback for non-JSON response
    return Opinion(
        model=model,
        position=response[:200],
        reasoning=response,
        confidence=0.5,
        considerations=[],
    )


def synthesize_opinions(
    question: str,
    opinions: List[Opinion],
    arbiter_model: Optional[str] = None,
) -> str:
    """Synthesize multiple opinions into a coherent recommendation."""
    opinions_text = ""
    for op in opinions:
        # Handle considerations that might be dicts or strings
        considerations = []
        for c in op.considerations:
            if isinstance(c, dict):
                considerations.append(str(c.get("text", c.get("consideration", str(c)))))
            else:
                considerations.append(str(c))

        opinions_text += f"""
### {op.model} (Confidence: {op.confidence:.0%})
**Position:** {op.position}
**Reasoning:** {op.reasoning}
**Key Considerations:** {', '.join(considerations)}

"""

    prompt = f"""You are the arbiter synthesizing opinions from a technical council.

## Question
{question}

## Council Opinions
{opinions_text}

## Your Task
1. Identify common ground between the opinions
2. Note any significant disagreements
3. Synthesize a clear, actionable recommendation

Provide:
1. A synthesis paragraph explaining the consensus view
2. A clear final decision/recommendation
3. Any important caveats or dissenting points to consider

Be specific and actionable. The team needs to execute on this decision immediately.
"""
    if arbiter_model is None:
        arbiter_model = get_default_model("council_arbiter")
    return call_model(arbiter_model, prompt)


def council_vote(
    question: str,
    context: str = "",
    models: Optional[List[str]] = None,
    voting_method: VotingMethod = VotingMethod.ARBITER,
    arbiter: Optional[str] = None,
    tracer: Optional[Tracer] = None,
) -> CouncilDecision:
    """
    Convene a council to deliberate on a question.

    Args:
        question: The question to deliberate
        context: Additional context
        models: List of models to consult (default: council_default from model_roles)
        voting_method: How to aggregate opinions
        arbiter: Model to make final synthesis (for ARBITER method)
        tracer: Optional tracer for logging

    Returns:
        CouncilDecision with synthesized result
    """
    tracer = tracer or get_tracer()
    models = models or _get_default_council()
    if arbiter is None:
        arbiter = get_default_model("council_arbiter")

    tracer.log(
        phase=Phase.COUNCIL,
        title="Council Convened",
        context=f"Question: {question}",
        reasoning=f"Consulting {len(models)} models for diverse perspectives.",
        decision_action=f"Models: {', '.join(models)}",
        next_steps=["Gather opinions", "Synthesize", "Make decision"],
    )

    # Gather opinions (serially to respect rate limits)
    opinions = []
    for model in models:
        try:
            opinion = get_opinion(model, question, context)
            opinions.append(opinion)
        except Exception as e:
            # Log failure but continue.
            # The raw exception must NOT be interpolated here: this Opinion flows
            # into tracer.log() -> the git-TRACKED logs/semantic_tracers/ tree AND
            # into the arbiter prompt sent to a third-party LLM API. Both councils
            # in PB-0052 ruled this the highest-severity leak path in the module.
            # Reuse the async council's closed-vocabulary summarizer — it is a
            # @staticmethod precisely so this class-less module can call it
            # without creating a shared redaction module (which would trip the
            # cycle's architectural-escape condition).
            from crux.council.async_council import AsyncCouncil

            opinions.append(
                Opinion(
                    model=model,
                    position=f"Failed to get opinion: {AsyncCouncil._redact_error(e)}",
                    reasoning="Model call failed",
                    confidence=0,
                    considerations=[],
                )
            )

    # Synthesize
    synthesis = synthesize_opinions(question, opinions, arbiter)

    # Calculate agreement level
    if len(opinions) > 1:
        # Simple heuristic: average confidence weighted by position similarity
        avg_confidence = sum(o.confidence for o in opinions) / len(opinions)
        agreement_level = avg_confidence
    else:
        agreement_level = opinions[0].confidence if opinions else 0

    # Extract final decision from synthesis
    final_decision = synthesis[:500] if len(synthesis) > 500 else synthesis

    decision = CouncilDecision(
        question=question,
        opinions=opinions,
        synthesis=synthesis,
        final_decision=final_decision,
        voting_method=voting_method,
        agreement_level=agreement_level,
        dissenting_points=[],  # Could be extracted from synthesis
    )

    # Log the decision
    model_calls = [
        {
            "model": op.model,
            "prompt_summary": f"Opinion on: {question[:50]}...",
            "response_summary": op.position[:100],
        }
        for op in opinions
    ]
    model_calls.append(
        {
            "model": arbiter,
            "prompt_summary": "Synthesize opinions",
            "response_summary": final_decision[:100],
        }
    )

    tracer.log(
        phase=Phase.COUNCIL,
        title="Council Decision Made",
        context=question,
        reasoning=f"Gathered {len(opinions)} opinions with {agreement_level:.0%} agreement.",
        decision_action=final_decision,
        model_calls=model_calls,
        metadata={
            "agreement_level": agreement_level,
            "models_consulted": models,
        },
    )

    return decision


def quick_council(question: str, context: str = "") -> str:
    """Quick council for simple decisions - returns just the decision text."""
    decision = council_vote(question, context)
    return decision.final_decision


__all__ = [
    "VotingMethod",
    "Opinion",
    "CouncilDecision",
    "DEFAULT_COUNCIL",
    "MODEL_WEIGHTS",
    "get_opinion",
    "synthesize_opinions",
    "council_vote",
    "quick_council",
]
