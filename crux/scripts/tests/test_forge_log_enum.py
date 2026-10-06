"""Lock-step tests for the forge-log event enum and promotion-path contracts.

Operative guarantees (per ADR-0041):

  (a) The seven-event enum string
      "authored | revised | used | evaluated | fallback | escalated | pruned"
      MUST appear verbatim in forge-skill/SKILL.md's locked format section
      AND in .claude/skills/forge-log.md's preamble.

  (b) The recency-set string "authored|revised|used|evaluated" MUST appear
      verbatim in BOTH forge-skill/SKILL.md (Pruning section) and in
      cleanup-campsite/SKILL.md (CLN-FG-1 rule).

  (c) The three verdict tokens "effective", "fell-short", "mixed" MUST all
      appear in forge-skill/SKILL.md.  The tokens "effective" and "fell-short"
      MUST both appear in retrospective/SKILL.md (the promotion floor reads
      them from evaluated entries).

      Strengthened oracle (ADR-0041 PB-0035 findings):
      - The EXEMPLAR line "- verdict: effective | gap: closed | recommend: keep"
        MUST appear verbatim in forge-skill/SKILL.md (the concrete two-line
        example block, not just the bare token).
      - The enum statement lines for each field MUST appear verbatim in
        forge-skill/SKILL.md (e.g. "verdict: one of effective, fell-short, mixed").
      - retrospective/SKILL.md MUST contain a floor phrase that quotes
        "verdict: effective" (the exact token the promotion-floor logic counts).

  (d) The "(same-session)" suffix form MUST appear in forge-skill/SKILL.md
      (outcome token suffix for same-session used events; ADR-0041 §1).

  (e) The §10.B evaluation-duty sentence fragment
      "writes one `evaluated` forge-log entry"
      MUST appear in BOTH docs/AGENTS.md and crux/templates/AGENTS.md.tmpl
      (the always-in-context seat for the session-end evaluation discipline).

Uses only stdlib (unittest, pathlib).  No mocks needed — everything resolves
from repo-relative paths so the test is cwd-independent.
"""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    from ._dev_surface import TREE, TREE_AGENTS_MD, require_dev_surface
except ImportError:  # unittest discover imports test modules top-level
    from _dev_surface import TREE, TREE_AGENTS_MD, require_dev_surface

# ─── repo paths ──────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
FORGE_SKILL = REPO_ROOT / "crux" / "skills" / "forge-skill" / "SKILL.md"
RETRO_SKILL = REPO_ROOT / "crux" / "skills" / "retrospective" / "SKILL.md"
CLEANUP_SKILL = REPO_ROOT / "crux" / "skills" / "cleanup-campsite" / "SKILL.md"
FORGE_LOG_MD = REPO_ROOT / ".claude" / "skills" / "forge-log.md"
DOCS_CLAUDE_MD = TREE_AGENTS_MD
TMPL_CLAUDE_MD = REPO_ROOT / "crux" / "templates" / "AGENTS.md.tmpl"

# ADR-0046 cycle/iterate forge-log recording surfaces.
CYCLE_REVIEW_MODULE = REPO_ROOT / "crux" / "templates" / "cycle-module-review.yaml"
CYCLE_PROMPTBOOK_TMPL = (
    REPO_ROOT / "crux" / "templates" / "cycle-promptbook-template.yaml"
)
ITERATE_PROMPTBOOK_TMPL = (
    REPO_ROOT / "crux" / "templates" / "iterate-promptbook-template.yaml"
)

# ─── canonical strings ───────────────────────────────────────────────────────

# (a) The seven-event enum — verbatim in forge-skill SKILL.md AND forge-log.md preamble.
FORGE_EVENT_ENUM: str = (
    "authored | revised | used | evaluated | fallback | escalated | pruned"
)

# (a-narrowing) The writer-contract NARROWING phrase — verbatim in BOTH the live
# forge-log.md preamble AND forge-skill SKILL.md. Pins the lifecycle-vs-usage writer
# split (forge-skill owns only the lifecycle events) so a future re-broadening of the
# writer contract turns a test RED (TC-1, PB-0039 prompt 11).
WRITER_CONTRACT_LIFECYCLE_PHRASE: str = (
    "sole writer of the lifecycle events (authored, revised, pruned)"
)

# (b) The recency-set string — verbatim in forge-skill Pruning AND cleanup CLN-FG-1.
# This is the pipe-delimited set of events that reset the staleness clock.
FORGE_RECENCY_SET: str = "authored|revised|used|evaluated"

# (c) Verdict tokens present in forge-skill; effective + fell-short in retrospective.
VERDICT_EFFECTIVE: str = "effective"
VERDICT_FELL_SHORT: str = "fell-short"
VERDICT_MIXED: str = "mixed"

# (d) Same-session suffix on used outcome tokens.
SAME_SESSION_SUFFIX: str = "(same-session)"

# (e) §10.B evaluation-duty sentence fragment — both CLAUDE surfaces.
EVALUATION_DUTY_FRAGMENT: str = "writes one `evaluated` forge-log entry"

# (c-strengthened) Exemplar line and per-field enum statements — forge-skill only.
# The two-line example block from the evaluated event table (ADR-0041 PB-0035).
EVALUATED_EXEMPLAR_LINE: str = (
    "- verdict: effective | gap: closed | recommend: keep"
)
VERDICT_ENUM_LINE: str = "verdict: one of effective, fell-short, mixed"
GAP_ENUM_LINE: str = "gap: one of closed, partial, not-closed"
RECOMMEND_ENUM_LINE: str = "recommend: one of keep, revise, prune"

# (c-strengthened) Floor phrase in retrospective that quotes the verdict token.
RETRO_VERDICT_EFFECTIVE_PHRASE: str = "verdict: effective"

# ─── ADR-0046 cycle/iterate forge-log recording tokens ───────────────────────

# The run-snapshot Notes section header carrying the per-use append-only lines.
# Authored verbatim in cycle-module-review.yaml (the `used` seat) AND read by the
# summary prompts in both promptbook templates (the `evaluated` seat).
FORGED_SKILLS_USED_HEADER: str = "Forged skills used"

# (N2) The same-session literal outcome-token forms — pinned exactly. These appear
# at the `used` seat (cycle-module-review.yaml) where the writer chooses an outcome
# token with the `(same-session)` suffix when the skill was forged this session.
OUTCOME_OK_SAME_SESSION: str = "ok (same-session)"
OUTCOME_PARTIAL_SAME_SESSION: str = "partial (same-session)"

# (N3) The byte-identical §10.B clause naming the dev-cycle/iterate seats. The
# historian adds this EXACT string to BOTH §10.B surfaces in parallel; this test
# pins it so the two surfaces stay in lock-step. Backticks are literal characters
# in the file (and in this Python string — backticks are not special in Python).
SECTION_10B_CYCLE_SEATS_CLAUSE: str = (
    "a dev-cycle or iterate run discharges this at the review module's "
    "convergence prompt (the per-use `used` entry) and the summary prompt "
    "(the session-end `evaluated` entry)"
)

# ─── CLAUDE-surface pair ─────────────────────────────────────────────────────
_CLAUDE_SURFACES = [
    (DOCS_CLAUDE_MD, f"{TREE}/AGENTS.md"),
    (TMPL_CLAUDE_MD, "crux/templates/AGENTS.md.tmpl"),
]


# ─────────────────────────────────────────────────────────────────────────────
# (a) Seven-event enum
# ─────────────────────────────────────────────────────────────────────────────


class TestForgeEventEnumInForgeSkill(unittest.TestCase):
    """(a) forge-skill/SKILL.md must contain the seven-event enum verbatim."""

    def _read(self) -> str:
        self.assertTrue(
            FORGE_SKILL.exists(),
            msg=f"forge-skill/SKILL.md not found at {FORGE_SKILL}",
        )
        return FORGE_SKILL.read_text(encoding="utf-8")

    def test_forge_skill_contains_seven_event_enum(self) -> None:
        text = self._read()
        self.assertIn(
            FORGE_EVENT_ENUM,
            text,
            msg=(
                "forge-skill/SKILL.md does not contain the seven-event enum "
                f"verbatim.\n  expected: {FORGE_EVENT_ENUM!r}"
            ),
        )


class TestForgeEventEnumInForgeLogMd(unittest.TestCase):
    """(a) .claude/skills/forge-log.md preamble must contain the seven-event enum."""

    def test_forge_log_md_contains_seven_event_enum(self) -> None:
        require_dev_surface(self, FORGE_LOG_MD, ".claude/skills/forge-log.md")
        text = FORGE_LOG_MD.read_text(encoding="utf-8")
        self.assertIn(
            FORGE_EVENT_ENUM,
            text,
            msg=(
                ".claude/skills/forge-log.md does not contain the seven-event "
                f"enum verbatim in its preamble.\n  expected: {FORGE_EVENT_ENUM!r}"
            ),
        )


# ─────────────────────────────────────────────────────────────────────────────
# (a-narrowing) Writer-contract narrowing — forge-log.md preamble + forge-skill
# ─────────────────────────────────────────────────────────────────────────────

# Both surfaces were edited to carry the lifecycle-vs-usage writer split. The
# surrounding seven-event enum is already pinned; this pins the NARROWING sentence
# itself so a future re-broadening regression (dropping the split) turns RED.
_WRITER_CONTRACT_SURFACES = [
    (FORGE_LOG_MD, ".claude/skills/forge-log.md"),
    (FORGE_SKILL, "crux/skills/forge-skill/SKILL.md"),
]


class TestWriterContractNarrowingInForgeSurfaces(unittest.TestCase):
    """(TC-1) The writer-contract NARROWING phrase MUST appear verbatim in BOTH
    the live .claude/skills/forge-log.md preamble and forge-skill/SKILL.md's
    locked preamble block. This defends the lifecycle-vs-usage split (forge-skill
    is the sole writer of the lifecycle events) against a silent re-broadening."""

    def test_writer_contract_phrase_in_both_forge_surfaces(self) -> None:
        for path, label in _WRITER_CONTRACT_SURFACES:
            with self.subTest(surface=label):
                if path == FORGE_LOG_MD:
                    require_dev_surface(self, path, label)
                self.assertTrue(
                    path.exists(),
                    msg=f"{label} not found at {path}",
                )
                text = path.read_text(encoding="utf-8")
                self.assertIn(
                    WRITER_CONTRACT_LIFECYCLE_PHRASE,
                    text,
                    msg=(
                        f"{label} does not contain the writer-contract "
                        "narrowing phrase verbatim.\n"
                        f"  expected: {WRITER_CONTRACT_LIFECYCLE_PHRASE!r}\n"
                        "  This phrase pins the lifecycle-vs-usage writer split; "
                        "if it is missing the writer contract may have been "
                        "silently re-broadened (TC-1, PB-0039 prompt 11)."
                    ),
                )


# ─────────────────────────────────────────────────────────────────────────────
# (b) Recency set — forge-skill Pruning + cleanup CLN-FG-1
# ─────────────────────────────────────────────────────────────────────────────


class TestForgeRecencySetInForgeSkill(unittest.TestCase):
    """(b) forge-skill/SKILL.md Pruning section must contain the recency-set string."""

    def test_forge_skill_contains_recency_set(self) -> None:
        self.assertTrue(
            FORGE_SKILL.exists(),
            msg=f"forge-skill/SKILL.md not found at {FORGE_SKILL}",
        )
        text = FORGE_SKILL.read_text(encoding="utf-8")
        self.assertIn(
            FORGE_RECENCY_SET,
            text,
            msg=(
                "forge-skill/SKILL.md does not contain the recency-set string "
                f"verbatim.\n  expected: {FORGE_RECENCY_SET!r}\n"
                "  This string appears in the Pruning section as the set of events "
                "that reset the staleness clock."
            ),
        )


class TestForgeRecencySetInCleanupSkill(unittest.TestCase):
    """(b) cleanup-campsite/SKILL.md CLN-FG-1 must contain the recency-set string."""

    def test_cleanup_skill_contains_recency_set(self) -> None:
        self.assertTrue(
            CLEANUP_SKILL.exists(),
            msg=f"cleanup-campsite/SKILL.md not found at {CLEANUP_SKILL}",
        )
        text = CLEANUP_SKILL.read_text(encoding="utf-8")
        self.assertIn(
            FORGE_RECENCY_SET,
            text,
            msg=(
                "cleanup-campsite/SKILL.md does not contain the recency-set string "
                f"verbatim in CLN-FG-1.\n  expected: {FORGE_RECENCY_SET!r}\n"
                "  This string must be byte-identical with forge-skill's Pruning "
                "section so both surfaces stay in lock-step."
            ),
        )


# ─────────────────────────────────────────────────────────────────────────────
# (c) Verdict tokens
# ─────────────────────────────────────────────────────────────────────────────


class TestVerdictTokensInForgeSkill(unittest.TestCase):
    """(c) forge-skill/SKILL.md must contain all three verdict tokens."""

    def _read(self) -> str:
        self.assertTrue(
            FORGE_SKILL.exists(),
            msg=f"forge-skill/SKILL.md not found at {FORGE_SKILL}",
        )
        return FORGE_SKILL.read_text(encoding="utf-8")

    def test_forge_skill_contains_effective(self) -> None:
        text = self._read()
        self.assertIn(
            VERDICT_EFFECTIVE,
            text,
            msg=(
                f"forge-skill/SKILL.md does not contain verdict token "
                f"{VERDICT_EFFECTIVE!r} (evaluated event body)."
            ),
        )

    def test_forge_skill_contains_fell_short(self) -> None:
        text = self._read()
        self.assertIn(
            VERDICT_FELL_SHORT,
            text,
            msg=(
                f"forge-skill/SKILL.md does not contain verdict token "
                f"{VERDICT_FELL_SHORT!r} (evaluated event body)."
            ),
        )

    def test_forge_skill_contains_mixed(self) -> None:
        text = self._read()
        self.assertIn(
            VERDICT_MIXED,
            text,
            msg=(
                f"forge-skill/SKILL.md does not contain verdict token "
                f"{VERDICT_MIXED!r} (evaluated event body)."
            ),
        )


class TestVerdictTokensInRetroSkill(unittest.TestCase):
    """(c) retrospective/SKILL.md must contain effective and fell-short tokens."""

    def _read(self) -> str:
        self.assertTrue(
            RETRO_SKILL.exists(),
            msg=f"retrospective/SKILL.md not found at {RETRO_SKILL}",
        )
        return RETRO_SKILL.read_text(encoding="utf-8")

    def test_retro_skill_contains_effective(self) -> None:
        text = self._read()
        self.assertIn(
            VERDICT_EFFECTIVE,
            text,
            msg=(
                f"retrospective/SKILL.md does not contain verdict token "
                f"{VERDICT_EFFECTIVE!r}. The promotion floor reads "
                f"`verdict: effective` entries to count evaluations."
            ),
        )

    def test_retro_skill_contains_fell_short(self) -> None:
        text = self._read()
        self.assertIn(
            VERDICT_FELL_SHORT,
            text,
            msg=(
                f"retrospective/SKILL.md does not contain verdict token "
                f"{VERDICT_FELL_SHORT!r}. The promotion floor treats unresolved "
                f"fell-short entries as blocking."
            ),
        )


# ─────────────────────────────────────────────────────────────────────────────
# (d) Same-session suffix
# ─────────────────────────────────────────────────────────────────────────────


class TestSameSessionSuffixInForgeSkill(unittest.TestCase):
    """(d) forge-skill/SKILL.md must contain the (same-session) suffix form."""

    def test_forge_skill_contains_same_session_suffix(self) -> None:
        self.assertTrue(
            FORGE_SKILL.exists(),
            msg=f"forge-skill/SKILL.md not found at {FORGE_SKILL}",
        )
        text = FORGE_SKILL.read_text(encoding="utf-8")
        self.assertIn(
            SAME_SESSION_SUFFIX,
            text,
            msg=(
                f"forge-skill/SKILL.md does not contain the same-session suffix "
                f"form {SAME_SESSION_SUFFIX!r}.\n"
                "This suffix appears on 'used' outcome tokens when a skill is "
                "invoked in the same session that forged it (ADR-0041 §1)."
            ),
        )


# ─────────────────────────────────────────────────────────────────────────────
# (e) §10.B evaluation-duty sentence — both CLAUDE surfaces
# ─────────────────────────────────────────────────────────────────────────────


class TestEvaluationDutySentenceInClaudeSurfaces(unittest.TestCase):
    """(e) Both CLAUDE surfaces must carry the §10.B evaluation-duty fragment."""

    def test_evaluation_duty_fragment_in_both_claude_surfaces(self) -> None:
        for path, label in _CLAUDE_SURFACES:
            with self.subTest(surface=label):
                if path == DOCS_CLAUDE_MD:
                    require_dev_surface(self, path, label)
                self.assertTrue(
                    path.exists(),
                    msg=f"{label} not found at {path}",
                )
                text = path.read_text(encoding="utf-8")
                self.assertIn(
                    EVALUATION_DUTY_FRAGMENT,
                    text,
                    msg=(
                        f"{label} does not contain the §10.B evaluation-duty "
                        f"sentence fragment.\n"
                        f"  expected: {EVALUATION_DUTY_FRAGMENT!r}\n"
                        "  This sentence carries the session-end evaluation "
                        "discipline in the always-in-context seat (ADR-0041 §2)."
                    ),
                )


# ─────────────────────────────────────────────────────────────────────────────
# (c-strengthened) Evaluated exemplar + enum statements in forge-skill
# ─────────────────────────────────────────────────────────────────────────────


class TestEvaluatedExemplarInForgeSkill(unittest.TestCase):
    """(c-strengthened) forge-skill/SKILL.md must contain the verbatim exemplar line
    and all three per-field enum statements so writers cannot emit the alternatives
    literally instead of a chosen token."""

    def _read(self) -> str:
        self.assertTrue(
            FORGE_SKILL.exists(),
            msg=f"forge-skill/SKILL.md not found at {FORGE_SKILL}",
        )
        return FORGE_SKILL.read_text(encoding="utf-8")

    def test_forge_skill_contains_evaluated_exemplar_line(self) -> None:
        text = self._read()
        self.assertIn(
            EVALUATED_EXEMPLAR_LINE,
            text,
            msg=(
                "forge-skill/SKILL.md does not contain the evaluated-event exemplar "
                f"line verbatim.\n  expected: {EVALUATED_EXEMPLAR_LINE!r}\n"
                "  The two-line example block must appear separately from the enum "
                "statements so writers see a concrete example, not a template."
            ),
        )

    def test_forge_skill_contains_verdict_enum_line(self) -> None:
        text = self._read()
        self.assertIn(
            VERDICT_ENUM_LINE,
            text,
            msg=(
                "forge-skill/SKILL.md does not contain the verdict enum statement "
                f"verbatim.\n  expected: {VERDICT_ENUM_LINE!r}\n"
                "  This line must appear below the exemplar so the token choices "
                "are explicit and writers do not emit the alternatives literally."
            ),
        )

    def test_forge_skill_contains_gap_enum_line(self) -> None:
        text = self._read()
        self.assertIn(
            GAP_ENUM_LINE,
            text,
            msg=(
                "forge-skill/SKILL.md does not contain the gap enum statement "
                f"verbatim.\n  expected: {GAP_ENUM_LINE!r}\n"
                "  This line must appear below the exemplar."
            ),
        )

    def test_forge_skill_contains_recommend_enum_line(self) -> None:
        text = self._read()
        self.assertIn(
            RECOMMEND_ENUM_LINE,
            text,
            msg=(
                "forge-skill/SKILL.md does not contain the recommend enum statement "
                f"verbatim.\n  expected: {RECOMMEND_ENUM_LINE!r}\n"
                "  This line must appear below the exemplar."
            ),
        )


# ─────────────────────────────────────────────────────────────────────────────
# (c-strengthened) Floor phrase quoting verdict: effective in retrospective
# ─────────────────────────────────────────────────────────────────────────────


class TestRetroVerdictEffectivePhraseInRetroSkill(unittest.TestCase):
    """(c-strengthened) retrospective/SKILL.md must contain the floor phrase
    that quotes the exact verdict token the promotion-floor logic counts."""

    def test_retro_skill_contains_verdict_effective_phrase(self) -> None:
        self.assertTrue(
            RETRO_SKILL.exists(),
            msg=f"retrospective/SKILL.md not found at {RETRO_SKILL}",
        )
        text = RETRO_SKILL.read_text(encoding="utf-8")
        self.assertIn(
            RETRO_VERDICT_EFFECTIVE_PHRASE,
            text,
            msg=(
                "retrospective/SKILL.md does not contain the floor phrase "
                f"{RETRO_VERDICT_EFFECTIVE_PHRASE!r}.\n"
                "  The promotion floor counts entries with this exact token; "
                "the phrase must appear in the Phase 3.5 section so the "
                "re-grep target is defined unambiguously."
            ),
        )


# ─────────────────────────────────────────────────────────────────────────────
# (ADR-0046) `Forged skills used` Notes header — all three cycle/iterate surfaces
# ─────────────────────────────────────────────────────────────────────────────

# The header is authored at the `used` seat (cycle-module-review.yaml) and read at
# the `evaluated` seat (both promptbook templates). All three must name the section
# by the same literal string or the cross-prompt tracking surface drifts.
_FORGED_SKILLS_USED_HEADER_SURFACES = [
    (CYCLE_REVIEW_MODULE, "cycle-module-review.yaml"),
    (CYCLE_PROMPTBOOK_TMPL, "cycle-promptbook-template.yaml"),
    (ITERATE_PROMPTBOOK_TMPL, "iterate-promptbook-template.yaml"),
]


class TestForgedSkillsUsedHeaderInCycleSurfaces(unittest.TestCase):
    """(ADR-0046) The `Forged skills used` Notes-section header MUST appear in
    cycle-module-review.yaml, cycle-promptbook-template.yaml, and
    iterate-promptbook-template.yaml — the seats that write/read the section."""

    def test_forged_skills_used_header_in_all_three_surfaces(self) -> None:
        for path, label in _FORGED_SKILLS_USED_HEADER_SURFACES:
            with self.subTest(surface=label):
                self.assertTrue(
                    path.exists(),
                    msg=f"{label} not found at {path}",
                )
                text = path.read_text(encoding="utf-8")
                self.assertIn(
                    FORGED_SKILLS_USED_HEADER,
                    text,
                    msg=(
                        f"{label} does not contain the run-snapshot Notes "
                        f"section header {FORGED_SKILLS_USED_HEADER!r}.\n"
                        "  This header names the append-only cross-prompt "
                        "tracking surface (ADR-0046 §2); the `used` seat "
                        "appends to it and the `evaluated` seat reads it."
                    ),
                )


# ─────────────────────────────────────────────────────────────────────────────
# (ADR-0046 / N2) Same-session outcome-token literals — the `used` seat
# ─────────────────────────────────────────────────────────────────────────────


class TestSameSessionOutcomeLiteralsInReviewModule(unittest.TestCase):
    """(ADR-0046 N2) The `used` seat (cycle-module-review.yaml THIRD prompt) MUST
    pin the exact same-session outcome-token literals `ok (same-session)` and
    `partial (same-session)` so the writer emits the locked enum form verbatim."""

    def _read(self) -> str:
        self.assertTrue(
            CYCLE_REVIEW_MODULE.exists(),
            msg=f"cycle-module-review.yaml not found at {CYCLE_REVIEW_MODULE}",
        )
        return CYCLE_REVIEW_MODULE.read_text(encoding="utf-8")

    def test_review_module_contains_ok_same_session(self) -> None:
        text = self._read()
        self.assertIn(
            OUTCOME_OK_SAME_SESSION,
            text,
            msg=(
                "cycle-module-review.yaml does not contain the same-session "
                f"outcome literal {OUTCOME_OK_SAME_SESSION!r} (ADR-0046 §2)."
            ),
        )

    def test_review_module_contains_partial_same_session(self) -> None:
        text = self._read()
        self.assertIn(
            OUTCOME_PARTIAL_SAME_SESSION,
            text,
            msg=(
                "cycle-module-review.yaml does not contain the same-session "
                f"outcome literal {OUTCOME_PARTIAL_SAME_SESSION!r} (ADR-0046 §2)."
            ),
        )


# ─────────────────────────────────────────────────────────────────────────────
# (ADR-0046) Verdict tokens at the summary-prompt (`evaluated`) seats
# ─────────────────────────────────────────────────────────────────────────────

# The summary prompt of each promptbook template writes one `evaluated` entry per
# distinct forged skill with a derived verdict; all three verdict tokens must be
# named in the prompt prose so the derivation rule is unambiguous.
_SUMMARY_PROMPT_VERDICT_SURFACES = [
    (CYCLE_PROMPTBOOK_TMPL, "cycle-promptbook-template.yaml"),
    (ITERATE_PROMPTBOOK_TMPL, "iterate-promptbook-template.yaml"),
]


class TestVerdictTokensInSummaryPromptSurfaces(unittest.TestCase):
    """(ADR-0046) Both promptbook-template summary prompts MUST name all three
    verdict tokens (`effective`, `fell-short`, `mixed`) so the evaluated-entry
    derivation rule is stated at the seat."""

    def test_verdict_tokens_in_both_summary_surfaces(self) -> None:
        for path, label in _SUMMARY_PROMPT_VERDICT_SURFACES:
            for token in (VERDICT_EFFECTIVE, VERDICT_FELL_SHORT, VERDICT_MIXED):
                with self.subTest(surface=label, token=token):
                    self.assertTrue(
                        path.exists(),
                        msg=f"{label} not found at {path}",
                    )
                    text = path.read_text(encoding="utf-8")
                    self.assertIn(
                        token,
                        text,
                        msg=(
                            f"{label} does not contain verdict token "
                            f"{token!r} in its summary prompt (ADR-0046 §2 "
                            "evaluated-entry derivation rule)."
                        ),
                    )


# ─────────────────────────────────────────────────────────────────────────────
# (ADR-0046 / N3) Byte-identical §10.B cycle-seats clause — both CLAUDE surfaces
# ─────────────────────────────────────────────────────────────────────────────


class TestCycleSeatsClauseInClaudeSurfaces(unittest.TestCase):
    """(ADR-0046 N3) The §10.B clause naming the dev-cycle/iterate seats MUST
    appear byte-identically in BOTH docs/AGENTS.md and crux/templates/AGENTS.md.tmpl.

    The clause is owned by the historian (added in parallel to both §10.B
    surfaces); this test pins it. If only this assertion is RED, the historian's
    edit has not landed yet — that is NOT a dev-lead template defect."""

    def test_cycle_seats_clause_in_both_claude_surfaces(self) -> None:
        for path, label in _CLAUDE_SURFACES:
            with self.subTest(surface=label):
                if path == DOCS_CLAUDE_MD:
                    require_dev_surface(self, path, label)
                self.assertTrue(
                    path.exists(),
                    msg=f"{label} not found at {path}",
                )
                text = path.read_text(encoding="utf-8")
                self.assertIn(
                    SECTION_10B_CYCLE_SEATS_CLAUSE,
                    text,
                    msg=(
                        f"{label} does not contain the §10.B cycle-seats "
                        "clause byte-identically.\n"
                        f"  expected: {SECTION_10B_CYCLE_SEATS_CLAUSE!r}\n"
                        "  This clause names the dev-cycle/iterate `used` and "
                        "`evaluated` seats (ADR-0046 §3); it must be byte-"
                        "identical across both CLAUDE surfaces. Owned by the "
                        "historian — if only this assertion is RED, that edit "
                        "has not landed yet."
                    ),
                )
