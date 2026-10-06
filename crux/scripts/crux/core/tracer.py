#!/usr/bin/env python3
"""
tracer.py - Semantic Logging for Recursive Self-Intelligence

Structured logging that creates markdown trace files, allowing humans to
follow the AI's reasoning process and decision-making.

The traces directory is resolved via a single CRUX_TRACES_DIR env var.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from enum import Enum


# Default traces directory (can be overridden via CRUX_TRACES_DIR).
# We anchor relative to CWD rather than the package install path because traces
# are session output, not source code.
_DEFAULT_TRACES_DIR_FALLBACK = Path("logs") / "semantic_tracers" / "traces"


def _resolve_traces_dir() -> Path:
    """Resolve the traces directory.

    Honors CRUX_TRACES_DIR if set. Co-locating traces with a run folder
    is supported by exporting CRUX_TRACES_DIR before invocation:
        export CRUX_TRACES_DIR="logs/agent_runs/<RUN_ID>/traces"
    """
    new = os.environ.get("CRUX_TRACES_DIR")
    if new:
        return Path(new)
    return _DEFAULT_TRACES_DIR_FALLBACK


DEFAULT_TRACES_DIR = _resolve_traces_dir()


class Phase(Enum):
    """Phases of the recursive self-intelligence process."""
    BOOTSTRAP = "bootstrap"
    ANALYSIS = "analysis"
    ARCHITECTURE = "architecture"
    PLANNING = "planning"
    EXECUTION = "execution"
    VALIDATION = "validation"
    REFLECTION = "reflection"
    HYPOTHESIS = "hypothesis"
    COUNCIL = "council"


@dataclass
class ModelCall:
    """Record of an external model call."""
    model: str
    prompt_summary: str
    response_summary: str
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    duration_ms: Optional[int] = None


@dataclass
class SelfReflection:
    """Self-reflection and hints for future loops/sessions."""
    future_hints: List[Dict[str, Any]] = field(default_factory=list)
    incomplete_work: List[Dict[str, Any]] = field(default_factory=list)
    patterns_noticed: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    raw_notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "future_hints": self.future_hints,
            "incomplete_work": self.incomplete_work,
            "patterns_noticed": self.patterns_noticed,
            "warnings": self.warnings,
            "raw_notes": self.raw_notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SelfReflection':
        return cls(
            future_hints=data.get("future_hints", []),
            incomplete_work=data.get("incomplete_work", []),
            patterns_noticed=data.get("patterns_noticed", []),
            warnings=data.get("warnings", []),
            raw_notes=data.get("raw_notes", ""),
        )


@dataclass
class TraceEntry:
    """A single trace entry with full context."""
    phase: str
    title: str
    context: str
    reasoning: str
    decision_action: str
    model_calls: List[ModelCall] = field(default_factory=list)
    next_steps: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    loop_number: Optional[int] = None
    hypotheses: Optional[List[Dict[str, Any]]] = None
    self_reflection: Optional[SelfReflection] = None


class Tracer:
    """
    Semantic tracer for logging reasoning and decisions.

    Creates timestamped markdown files that document the AI's
    thought process, decisions, and external model calls.

    Can operate in standalone mode (creates own session dir) or
    integrated mode (writes to an existing session's traces/ dir).
    """

    def __init__(
        self,
        session_id: Optional[str] = None,
        traces_dir: Optional[Path] = None,
        session_dir: Optional[Path] = None
    ):
        """
        Initialize a new tracer.

        Args:
            session_id: Unique session identifier (auto-generated if not provided)
            traces_dir: Base directory for traces (standalone mode)
            session_dir: Existing session directory (integrated mode - writes to session_dir/traces/)
        """
        self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")

        # Determine output directory
        if session_dir:
            # Integrated mode - write to session's traces directory
            self.session_dir = Path(session_dir) / "traces"
        elif traces_dir:
            # Standalone mode with custom directory
            self.session_dir = Path(traces_dir) / self.session_id
        else:
            # Standalone mode with default directory
            DEFAULT_TRACES_DIR.mkdir(parents=True, exist_ok=True)
            self.session_dir = DEFAULT_TRACES_DIR / self.session_id

        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.trace_count = 0
        self.entries: List[TraceEntry] = []

        # Create session index
        self._write_session_index()

    def _write_session_index(self):
        """Write/update the session index file."""
        index_path = self.session_dir / "README.md"
        content = f"""# RSI Session Traces: {self.session_id}

Started: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## Trace Files

"""
        for i, entry in enumerate(self.entries, 1):
            content += f"- [{i:03d}_{entry.phase}.md](./{i:03d}_{entry.phase}.md) - {entry.title}\n"

        if not self.entries:
            content += "_No traces yet - session in progress_\n"

        content += f"""
## Quick Links

- [Latest Trace](./latest.md)
- [Execution Log](./execution_log.md)

## Session Metadata

```json
{json.dumps({"session_id": self.session_id, "trace_count": self.trace_count}, indent=2)}
```
"""
        with open(index_path, 'w') as f:
            f.write(content)

    def log(
        self,
        phase: Phase,
        title: str,
        context: str,
        reasoning: str,
        decision_action: str,
        model_calls: Optional[List[Dict[str, Any]]] = None,
        next_steps: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        loop_number: Optional[int] = None,
        hypotheses: Optional[List[Dict[str, Any]]] = None,
        self_reflection: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """Log a trace entry. Returns the path to the created trace file."""
        self.trace_count += 1

        # Convert model calls to ModelCall objects if needed
        calls = []
        if model_calls:
            for call in model_calls:
                if isinstance(call, ModelCall):
                    calls.append(call)
                else:
                    calls.append(ModelCall(**call))

        # Handle self_reflection
        reflection = None
        if self_reflection:
            if isinstance(self_reflection, SelfReflection):
                reflection = self_reflection
            else:
                reflection = SelfReflection.from_dict(self_reflection)

        entry = TraceEntry(
            phase=phase.value,
            title=title,
            context=context,
            reasoning=reasoning,
            decision_action=decision_action,
            model_calls=calls,
            next_steps=next_steps or [],
            metadata=metadata or {},
            loop_number=loop_number,
            hypotheses=hypotheses,
            self_reflection=reflection,
        )

        self.entries.append(entry)

        # Write trace file
        filename = f"{self.trace_count:03d}_{phase.value}.md"
        filepath = self.session_dir / filename

        content = self._format_trace(entry)
        with open(filepath, 'w') as f:
            f.write(content)

        # Update latest symlink (as a file copy for compatibility)
        latest_path = self.session_dir / "latest.md"
        with open(latest_path, 'w') as f:
            f.write(content)

        # Update session index
        self._write_session_index()

        # Also append to execution log
        self._append_to_log(entry)

        return filepath

    def _format_trace(self, entry: TraceEntry) -> str:
        """Format a trace entry as markdown."""
        loop_info = f" (Loop {entry.loop_number})" if entry.loop_number else ""

        content = f"""# {entry.title}

**Phase:** {entry.phase}{loop_info}
**Timestamp:** {entry.timestamp}

---

## Context

{entry.context}

---

## Reasoning

{entry.reasoning}

---

## Decision / Action

{entry.decision_action}

---

## External Model Calls

"""
        if entry.model_calls:
            for call in entry.model_calls:
                content += f"""### {call.model}

**Prompt Summary:** {call.prompt_summary}

**Response Summary:** {call.response_summary}

"""
                if call.tokens_in or call.tokens_out:
                    content += f"_Tokens: {call.tokens_in or '?'} in, {call.tokens_out or '?'} out_\n\n"
        else:
            content += "_No external model calls in this phase._\n"

        # Add hypotheses section if present
        if entry.hypotheses:
            content += """
---

## Hypotheses Generated

"""
            for h in entry.hypotheses:
                content += f"""### {h.get('id', '?')}: {h.get('hypothesis', 'N/A')}

- **Test Method:** {h.get('test_method', 'N/A')}
- **Expected Outcome:** {h.get('expected_outcome', 'N/A')}
- **Priority:** {h.get('priority', 'medium')}

"""

        # Add self-reflection section if present
        if entry.self_reflection:
            content += """
---

## Self-Reflection & Future Hints

"""
            ref = entry.self_reflection

            if ref.future_hints:
                content += "### Future Hints\n\n"
                for hint in ref.future_hints:
                    importance = hint.get('importance', 'useful')
                    marker = "[!]" if importance == "critical" else "[*]" if importance == "useful" else "[-]"
                    content += f"{marker} **{hint.get('context', 'General')}**\n"
                    content += f"> {hint.get('hint', '')}\n\n"

            if ref.incomplete_work:
                content += "### Incomplete Work (Breadcrumbs)\n\n"
                for work in ref.incomplete_work:
                    content += f"**{work.get('what', 'Unknown')}**\n"
                    content += f"- Left off: {work.get('where_left_off', 'N/A')}\n"
                    content += f"- Next step: {work.get('next_step', 'N/A')}\n\n"

            if ref.patterns_noticed:
                content += "### Patterns Noticed\n\n"
                for pattern in ref.patterns_noticed:
                    content += f"- {pattern}\n"
                content += "\n"

            if ref.warnings:
                content += "### Warnings\n\n"
                for warning in ref.warnings:
                    content += f"- {warning}\n"
                content += "\n"

            if ref.raw_notes:
                content += f"### Raw Notes\n\n{ref.raw_notes}\n"

        content += """
---

## Next Steps

"""
        if entry.next_steps:
            for step in entry.next_steps:
                content += f"- {step}\n"
        else:
            content += "_To be determined based on results._\n"

        if entry.metadata:
            content += f"""
---

## Metadata

```json
{json.dumps(entry.metadata, indent=2)}
```
"""

        return content

    def _append_to_log(self, entry: TraceEntry):
        """Append a summary to the execution log."""
        log_path = self.session_dir / "execution_log.md"

        # Create header if file doesn't exist
        if not log_path.exists():
            with open(log_path, 'w') as f:
                f.write(f"# Execution Log - Session {self.session_id}\n\n")

        loop_info = f" [Loop {entry.loop_number}]" if entry.loop_number else ""

        # Append entry summary
        with open(log_path, 'a') as f:
            f.write(f"""
## [{entry.timestamp}] {entry.phase.upper()}{loop_info}: {entry.title}

{entry.decision_action[:500]}{'...' if len(entry.decision_action) > 500 else ''}

---
""")

    def log_quick(
        self,
        phase: Phase,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
        loop_number: Optional[int] = None
    ):
        """Quick log for simple status updates."""
        return self.log(
            phase=phase,
            title=message[:50],
            context=message,
            reasoning="Quick status update",
            decision_action=message,
            metadata=metadata,
            loop_number=loop_number,
        )

    def log_hypothesis_generation(
        self,
        loop_number: int,
        hypotheses: List[Dict[str, Any]],
        reasoning: str,
    ) -> Path:
        """Log hypothesis generation (for GOLDEN RULE compliance)."""
        return self.log(
            phase=Phase.HYPOTHESIS,
            title=f"Loop {loop_number} Hypotheses",
            context=f"Generating 3 hypotheses for loop {loop_number + 1} to test",
            reasoning=reasoning,
            decision_action=f"Generated {len(hypotheses)} hypotheses",
            loop_number=loop_number,
            hypotheses=hypotheses,
            metadata={
                "hypothesis_count": len(hypotheses),
                "golden_rule_compliant": len(hypotheses) == 3 or loop_number == 3,
            }
        )

    def get_session_path(self) -> Path:
        """Get the path to the session directory."""
        return self.session_dir


# Global tracer instance (created on first use)
_global_tracer: Optional[Tracer] = None


def get_tracer(session_id: Optional[str] = None) -> Tracer:
    """Get or create the global tracer instance."""
    global _global_tracer
    if _global_tracer is None or (session_id and _global_tracer.session_id != session_id):
        _global_tracer = Tracer(session_id)
    return _global_tracer


def log_reasoning(
    phase: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
    loop_number: Optional[int] = None,
) -> Path:
    """Convenience function for logging reasoning."""
    tracer = get_tracer()
    phase_enum = Phase(phase) if phase in [p.value for p in Phase] else Phase.EXECUTION
    return tracer.log_quick(phase_enum, content, metadata, loop_number)


__all__ = [
    "Phase",
    "ModelCall",
    "SelfReflection",
    "TraceEntry",
    "Tracer",
    "get_tracer",
    "log_reasoning",
    "DEFAULT_TRACES_DIR",
]
