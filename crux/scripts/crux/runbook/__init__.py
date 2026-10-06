"""
crux.runbook - Generate autonomous prompt runbooks from a goal.

Produces a 50-100 prompt markdown runbook a Cursor agent can execute hands-off
for 2-4 hours.

CLI entry point:
    python -m crux.runbook "Your goal here" --ai-plan --target-prompts 75
"""

from .runbook import (
    PromptBlock,
    MIN_PROMPTS,
    MAX_PROMPTS,
    DEFAULT_TARGET_PROMPTS,
    generate_template_only_prompts,
    generate_ai_plan_prompts,
    render_markdown,
    main,
)

__all__ = [
    "PromptBlock",
    "MIN_PROMPTS",
    "MAX_PROMPTS",
    "DEFAULT_TARGET_PROMPTS",
    "generate_template_only_prompts",
    "generate_ai_plan_prompts",
    "render_markdown",
    "main",
]
