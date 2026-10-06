"""The two-factor consent gate's executor-side enforcement (ADR-0075 decision 4).

The executor enforces factor (a) — the standing capability flag
`CRUX_ARCH_ALLOW_RUNTIME=1` — and a fail-closed floor: it requires a TTY and
refuses under any CI marker, so it hard-fails in any CI or non-interactive
context even when the flag is set.

Factor (b), the per-execution non-model-mediated permission event, is enforced
OUTSIDE the executor — by the harness permission prompt on the executing tool
call, or by the human's own shell invocation. It is NEVER self-attested here: a
TTY does not prove a fresh human permission event occurred, so this module makes
no such claim (ADR-0075 decision 4, decision 11(iii)). The structural guarantee
that consent is never model-mediated rests on three facts this code cannot
enforce and does not try to: the skill is human-invoked only, is excluded from
run-execution autonomy, and must not be allowlisted for auto-approval.
"""

from __future__ import annotations

import os
import sys

FLAG = "CRUX_ARCH_ALLOW_RUNTIME"

# ADR-0075 decision 4: the CI-marker list is exactly these eight.
CI_MARKERS = (
    "CI",
    "CONTINUOUS_INTEGRATION",
    "GITHUB_ACTIONS",
    "GITLAB_CI",
    "BUILDKITE",
    "CIRCLECI",
    "JENKINS_URL",
    "TF_BUILD",
)


class GateRefusal(Exception):
    """The consent floor refused. Carries a human-readable reason. Raising this
    is a no-capture refusal, never a hang."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def check_gate(env: dict | None = None, isatty: bool | None = None) -> None:
    """Enforce factor (a) + the fail-closed CI/TTY floor. Raise `GateRefusal`
    (never proceed) on any failing condition.

    * factor (a): `CRUX_ARCH_ALLOW_RUNTIME` must equal exactly "1".
    * floor: refuse if any CI marker key is PRESENT in the environment, even
      with the flag set (present-as-key is the fail-closed test — a marker set
      to any value, including empty, refuses).
    * floor: refuse without a TTY on stdin (attended run required).
    """
    env = os.environ if env is None else env

    if env.get(FLAG) != "1":
        raise GateRefusal(
            f"factor (a) not satisfied: {FLAG} is not set to 1 "
            "(the standing capability flag is off by default; set it out-of-band)"
        )

    present = [m for m in CI_MARKERS if m in env]
    if present:
        raise GateRefusal(
            "fail-closed CI floor: CI marker(s) present "
            f"{present} — runtime introspection refuses in CI even with {FLAG} set"
        )

    tty = sys.stdin.isatty() if isatty is None else isatty
    if not tty:
        raise GateRefusal(
            "fail-closed floor: no TTY on stdin — an attended run is required "
            "(this floor is not proof of consent; factor (b) is enforced at the "
            "tool boundary or the human's shell)"
        )
