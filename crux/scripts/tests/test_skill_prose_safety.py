"""Prose-safety gate over `crux/skills/*/SKILL.md` (M-SEC-1).

`propose-observation/SKILL.md` told the agent to run

    python3 -c "from crux.arch.recover import candidate_id; print(candidate_id('${ANCHOR_KIND}', '${ANCHOR}'))"

interpolating a shell variable INTO a Python program string. Anchor text is
repo-derived for the api-contract / system-of-record / cross-cutting-policy
anchor kinds, so a crafted anchor closes the quote and executes arbitrary code
under the agent's own permissions — and can still print a plausible 16-hex
anchor id, so the run looks normal. That voids the observations writer boundary
outright: every lifecycle control in §17.2 is prose the agent follows, and
injected code writes `status: ratified` to a record directly.

The class of defect is "a placeholder the shell expands, sitting inside a
program the interpreter parses". This gate refuses it by shape rather than by
listing the one call site, so the next skill that reaches for `python3 -c`
cannot reintroduce it.

The safe form passes the value through `argv` (or the environment), where it is
data to the interpreter and never program text.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SKILLS_DIR = REPO_ROOT / "crux" / "skills"

# A `-c`/`-e` program string (python3, python, ruby, node, perl, sh, bash) that
# carries a `${VAR}` or `$VAR` placeholder before the program string ends.
# The program string is delimited by the quote that opens it.
_INLINE_PROGRAM_RE = re.compile(
    r"""(?:python3?|ruby|node|perl|bash|sh)\s+-(?:c|e)\s+(?P<q>['"])(?P<body>(?:(?!(?P=q)).)*)""",
    re.DOTALL,
)
_SHELL_PLACEHOLDER_RE = re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?")


def _offenders(text: str) -> list[str]:
    """Every inline program string in `text` that interpolates a shell variable."""
    hits = []
    for m in _INLINE_PROGRAM_RE.finditer(text):
        body = m.group("body")
        if _SHELL_PLACEHOLDER_RE.search(body):
            hits.append(m.group(0)[:160])
    return hits


class SkillProseSafetyTests(unittest.TestCase):
    def test_no_skill_interpolates_a_shell_variable_into_a_program_string(self):
        """A shell placeholder inside a `-c` program is remote code execution
        from whatever produced the placeholder's value."""
        findings = {}
        for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
            hits = _offenders(skill_md.read_text(encoding="utf-8"))
            if hits:
                findings[str(skill_md.relative_to(REPO_ROOT))] = hits
        self.assertEqual(
            findings, {},
            "a SKILL.md interpolates a shell variable into an inline program "
            "string; pass the value through argv or the environment instead:\n"
            + "\n".join(f"  {k}: {v}" for k, v in findings.items()),
        )

    def test_the_gate_detects_the_pattern_it_was_written_for(self):
        """The regex is the whole gate, so pin it against the real payload
        shape and against the safe rewrite. Without this, a regex that matched
        nothing would leave the suite green and the gate inert."""
        unsafe = (
            """cd "${CRUX_PLUGIN_ROOT}/scripts" && python3 -c """
            """"from crux.arch.recover import candidate_id; """
            """print(candidate_id('${ANCHOR_KIND}', '${ANCHOR}'))\""""
        )
        self.assertTrue(_offenders(unsafe), "the gate missed the known payload shape")

        safe = (
            """ANCHOR_KIND="${ANCHOR_KIND}" ANCHOR="${ANCHOR}" python3 -c """
            """'import os, sys; sys.path.insert(0, "."); """
            """from crux.arch.recover import candidate_id; """
            """print(candidate_id(os.environ["ANCHOR_KIND"], os.environ["ANCHOR"]))'"""
        )
        self.assertEqual(_offenders(safe), [],
                         "the gate flags the environment-passing rewrite it exists to permit")


if __name__ == "__main__":
    unittest.main()
