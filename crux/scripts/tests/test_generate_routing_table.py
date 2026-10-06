"""Tests for generate-routing-table.py — the fourteenth regenerative output (ADR-0092).

Modelled on test_generate_writing_rules.py: the marker-region generator's core is
the fail-closed `find_region`, so every refusal path is pinned, plus the
`build_region` user-invocable/Claude-only split and an end-to-end `run()` that
proves the region body is replaced while everything outside the markers survives
byte-for-byte.

The F1 phrase-extraction grammar (triggers placed after the first period are
dropped) is DEFERRED, not fixed here: `extract_phrases` is tested at its current,
documented behavior — the post-period drop is asserted as the status quo.

Stdlib only (unittest, importlib, tempfile, pathlib, json, subprocess, sys).
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "crux" / "scripts" / "generate-routing-table.py"
from _authoring_fixture import seed_authoring_probe



def _load_module():
    spec = importlib.util.spec_from_file_location("generate_routing_table", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


grt = _load_module()
B, E = grt.BEGIN, grt.END


class FindRegionTestCase(unittest.TestCase):
    """find_region is the fail-closed core; every refusal path is tested (lines 104-123)."""

    def test_valid_single_pair_returns_span_and_preserves_outside(self):
        text = f"HEAD\n{B}\nold body\n{E}\nTAIL\n"
        i, j = grt.find_region(text, "t")
        # The span is the BODY strictly between the marker lines.
        self.assertEqual(text[i:j], "\nold body\n")
        # Replacing the body leaves the bytes outside the markers verbatim.
        rebuilt = text[:i] + "\nNEW\n" + text[j:]
        self.assertEqual(rebuilt, f"HEAD\n{B}\nNEW\n{E}\nTAIL\n")
        self.assertTrue(text[:i].endswith(B))
        self.assertTrue(text[j:].startswith(E))

    def test_missing_begin_marker_is_refused(self):
        with self.assertRaises(grt.RegenError) as cm:
            grt.find_region(f"only\n{E}\n", "t")
        self.assertIn("missing marker", str(cm.exception))

    def test_missing_end_marker_is_refused(self):
        with self.assertRaises(grt.RegenError) as cm:
            grt.find_region(f"only\n{B}\n", "t")
        self.assertIn("missing marker", str(cm.exception))

    def test_duplicate_begin_marker_is_refused_rather_than_guessed(self):
        text = f"{B}\na\n{E}\n{B}\nb\n{E}\n"
        with self.assertRaises(grt.RegenError) as cm:
            grt.find_region(text, "t")
        self.assertIn("duplicate marker", str(cm.exception))

    def test_end_before_begin_is_refused(self):
        with self.assertRaises(grt.RegenError) as cm:
            grt.find_region(f"{E}\nbody\n{B}\n", "t")
        self.assertIn("precedes", str(cm.exception))

    def test_marker_as_midline_substring_is_not_a_delimiter(self):
        # A marker mentioned inside a sentence must not delimit anything.
        text = f"Prose mentioning {B} and {E} inline.\n"
        with self.assertRaises(grt.RegenError) as cm:
            grt.find_region(text, "t")
        self.assertIn("missing marker", str(cm.exception))


class BuildRegionTestCase(unittest.TestCase):
    """build_region splits user-invocable skills from Claude-only ones and
    reports the skills whose description yields no trigger phrase."""

    SKILLS = [
        {"id": "beta", "description": 'Use when "do this" / "do that". "ignored" after.'},
        {"id": "alpha", "description": "No quotes here at all.", "routing_note": "note-a"},
        {"id": "zed", "description": "whatever", "user-invocable": False,
         "routing_note": "claude-note"},
    ]

    def test_user_invocable_and_claude_only_are_split(self):
        region, no_triggers = grt.build_region(self.SKILLS)
        # Main table header present; the Claude-only sub-table present.
        self.assertIn("| User phrase | Skill | Notes |", region)
        self.assertIn("### Claude-only (no user phrase)", region)
        # `zed` (user-invocable: false) is in the Claude-only table, NOT the main
        # one, and carries its routing note.
        self.assertIn("| `zed` | claude-note |", region)
        # A user-invocable skill lands in the main table with its phrase cell.
        self.assertIn('| "do this" / "do that" | `beta` |', region)

    def test_no_trigger_skill_is_reported_and_gets_an_empty_phrase_cell(self):
        region, no_triggers = grt.build_region(self.SKILLS)
        # `alpha` is user-invocable but has no quoted phrase → reported, and its
        # main-table row has an empty phrase cell.
        self.assertEqual(no_triggers, ["alpha"])
        self.assertIn("|  | `alpha` | note-a |", region)

    def test_rows_are_sorted_by_skill_id(self):
        region, _ = grt.build_region(self.SKILLS)
        # alpha before beta in the main table.
        self.assertLess(region.index("`alpha`"), region.index("`beta`"))


class ExtractPhrasesCurrentBehaviorTestCase(unittest.TestCase):
    """The contract: triggers come from the description's FIRST SENTENCE.

    A phrase after the first sentence is dropped BY DESIGN — that is what keeps
    a quoted example elsewhere in a description out of the routing table. The
    former defect was not the drop; it was where the sentence was judged to
    end. Splitting on any period ended the sentence inside a dotted identifier
    and discarded the triggers that followed it in the SAME sentence.
    """

    def test_only_phrases_in_the_first_sentence_are_taken(self):
        # The drop is the contract, not a defect: a phrase after the first
        # sentence is not a trigger. Do not "fix" this here.
        phrases = grt.extract_phrases('Use "keep me". Then "drop me" later.')
        self.assertEqual(phrases, ["keep me"])

    def test_a_quoted_example_after_the_first_sentence_is_not_a_trigger(self):
        """An arbitrary quoted example must never become an invocation phrase."""
        phrases = grt.extract_phrases(
            'Use when the user says "audit docs". Reports a row such as '
            '"BROKEN: dangling wiki-link" for each finding.')
        self.assertEqual(phrases, ["audit docs"])

    def test_a_dotted_identifier_does_not_end_the_first_sentence(self):
        """The repaired split: a period followed by a letter is not a sentence end.

        `refresh-research-synthesis` put every trigger after
        `research.refresh_interval_days` in its opening sentence and rendered
        an EMPTY routing cell while the drift gate stayed clean.
        """
        phrases = grt.extract_phrases(
            'Use when a page has aged past `research.refresh_interval_days` '
            '(default 90), or the user asks to "refresh synthesis" or '
            '"clear flags". Later "not a trigger" text.')
        self.assertEqual(phrases, ["refresh synthesis", "clear flags"])

    def test_a_period_at_end_of_text_still_ends_the_sentence(self):
        self.assertEqual(grt.extract_phrases('Say "only me".'), ["only me"])

    def test_a_sentence_end_before_a_newline_is_a_sentence_end(self):
        self.assertEqual(grt.extract_phrases('Say "only me".\nThen "no".'), ["only me"])

    def test_duplicates_are_collapsed_in_order(self):
        phrases = grt.extract_phrases('"a" / "b" / "a"')
        self.assertEqual(phrases, ["a", "b"])

    def test_single_quoted_triggers_are_recognized(self):
        phrases = grt.extract_phrases("Use when 'start a cycle' happens")
        self.assertEqual(phrases, ["start a cycle"])


class EveryUserFacingRowHasPhrasesTestCase(unittest.TestCase):
    """The shipped catalog yields a usable phrase for every user-facing skill.

    `build_region` has always REPORTED `no_trigger_skills`, but nothing failed
    on a non-empty report: twelve skills rendered an empty routing cell while
    `generate-routing-table.py --dry-run` exited 0 and reported no drift. A
    reported-but-unenforced signal is not a gate; this is the gate.

    A skill with no user phrase is exempt only by declaring
    `user-invocable: false`, which routes it to the Claude-only table. Adding a
    name to an allowlist here is NOT the way to satisfy this test.
    """

    def _skills(self) -> list[dict]:
        path = REPO_ROOT / "crux" / "catalog" / "skills.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_no_user_invocable_skill_renders_an_empty_phrase_cell(self):
        skills = self._skills()
        # Positive control: the catalog must actually carry user-invocable
        # skills, so an empty catalog cannot pass this vacuously.
        user_facing = [s for s in skills if s.get("user-invocable", True) is not False]
        self.assertGreater(len(user_facing), 40)
        _, no_triggers = grt.build_region(skills)
        self.assertEqual(no_triggers, [], f"user-facing skills with no trigger phrase: {no_triggers}")

    def _victim(self, skills: list[dict]) -> dict:
        return next(s for s in skills if s.get("user-invocable", True) is not False)

    def test_negative_control_a_stripped_trigger_field_is_caught(self):
        """The original failure, reintroduced against the source that now carries it.

        The phrases moved out of the description into `metadata.triggers`, so
        stripping the description alone no longer empties a cell -- the declared
        field still answers. Strip THAT and the description's own fallback finds
        nothing, because a description short enough not to truncate carries no
        quoted phrase. This is what proves the declared field is load-bearing.
        """
        skills = self._skills()
        victim = self._victim(skills)
        self.assertTrue(victim.get("triggers"),
                        "the victim declares no triggers; pick one that does")
        mutated = [{k: v for k, v in s.items() if k != "triggers"}
                   if s["id"] == victim["id"] else s for s in skills]
        _, no_triggers = grt.build_region(mutated)
        self.assertEqual(no_triggers, [victim["id"]])

    def test_negative_control_stripping_both_sources_is_caught(self):
        """Neither source left: the gate must still go red rather than inventing a
        phrase from the skill's name."""
        skills = self._skills()
        victim = self._victim(skills)
        mutated = [dict({k: v for k, v in s.items() if k != "triggers"},
                        description="No quoted phrase anywhere at all.")
                   if s["id"] == victim["id"] else s for s in skills]
        region, no_triggers = grt.build_region(mutated)
        self.assertEqual(no_triggers, [victim["id"]])
        self.assertIn(f"|  | `{victim['id']}` |", region)

    def test_the_description_fallback_still_routes_a_skill_that_quotes_inline(self):
        """The fallback is kept so the declared field is additive: a skill that still
        writes its triggers inline keeps routing, and is never reported as a gap."""
        skills = self._skills()
        victim = self._victim(skills)
        mutated = [dict({k: v for k, v in s.items() if k != "triggers"},
                        description='Use when the user says "do the inline thing".')
                   if s["id"] == victim["id"] else s for s in skills]
        region, no_triggers = grt.build_region(mutated)
        self.assertEqual(no_triggers, [])
        self.assertIn('"do the inline thing"', region)

    def test_the_declared_field_wins_over_an_inline_description(self):
        """A skill doing both is read one way, not merged -- otherwise the same phrase
        could render twice and a stale inline list could outlive its replacement."""
        skills = self._skills()
        victim = self._victim(skills)
        mutated = [dict(s, triggers=["declared phrase"],
                        description='Use when the user says "inline phrase".')
                   if s["id"] == victim["id"] else s for s in skills]
        region, _ = grt.build_region(mutated)
        self.assertIn('"declared phrase"', region)
        self.assertNotIn('"inline phrase"', region)

    def test_internal_only_skills_are_exempt_via_the_declared_mechanism(self):
        """The remaining `user-invocable: false` skills are routed, never counted here."""
        skills = self._skills()
        internal = sorted(s["id"] for s in skills if s.get("user-invocable") is False)
        self.assertEqual(internal, ["call-llm"])
        region, no_triggers = grt.build_region(skills)
        for sid in internal:
            # Present in the Claude-only table, and never reported as missing.
            self.assertIn(f"| `{sid}` |", region)
            self.assertNotIn(sid, no_triggers)
        self.assertIn("### Claude-only (no user phrase)", region)


class RunEndToEndTestCase(unittest.TestCase):
    """run() against a minimal temp tree: region replaced, outside preserved."""

    def _make_tree(self, tmp: Path, body: str, skills: list[dict]) -> Path:
        seed_authoring_probe(tmp, SCRIPT)
        (tmp / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8")
        (tmp / "crux" / "catalog").mkdir(parents=True)
        (tmp / "crux" / "catalog" / "skills.json").write_text(
            json.dumps(skills), encoding="utf-8"
        )
        (tmp / "bionic").mkdir()
        claude = tmp / "bionic" / "AGENTS.md"
        claude.write_text(f"HEAD LINE\n{B}\n{body}\n{E}\nTAIL LINE\n", encoding="utf-8")
        return claude

    SKILLS = [{"id": "alpha", "description": 'Use when "do this".', "routing_note": "n"}]

    def test_dry_run_reports_drift_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            claude = self._make_tree(tmp, "STALE", self.SKILLS)
            before = claude.read_text(encoding="utf-8")
            code, payload = grt.run(tmp, dry_run=True)
            self.assertEqual(code, 1)
            self.assertTrue(payload["drift"])
            self.assertEqual(claude.read_text(encoding="utf-8"), before, "--dry-run must not write")

    def test_regeneration_replaces_region_and_preserves_outside_verbatim(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            claude = self._make_tree(tmp, "STALE", self.SKILLS)
            code, payload = grt.run(tmp, dry_run=False)
            self.assertEqual(code, 0)
            self.assertEqual(payload["written"], ["bionic/AGENTS.md"])
            after = claude.read_text(encoding="utf-8")
            # Bytes outside the markers are untouched.
            self.assertTrue(after.startswith("HEAD LINE\n"))
            self.assertTrue(after.endswith("\nTAIL LINE\n"))
            # The regenerated region carries the skill's row; STALE is gone.
            self.assertNotIn("STALE", after)
            self.assertIn("`alpha`", after)

    def test_regeneration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            claude = self._make_tree(tmp, "STALE", self.SKILLS)
            grt.run(tmp, dry_run=False)
            first = claude.read_text(encoding="utf-8")
            code, payload = grt.run(tmp, dry_run=True)
            self.assertEqual(code, 0)
            self.assertFalse(payload["drift"])
            self.assertEqual(claude.read_text(encoding="utf-8"), first)


class ExitAndEnrollmentTestCase(unittest.TestCase):
    def test_missing_skills_json_exits_one_with_json(self):
        with tempfile.TemporaryDirectory() as t:
            seed_authoring_probe(t, SCRIPT)
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--dry-run", "--repo-root", t],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn('"error"', proc.stdout)

    def test_enrolled_in_the_regenerative_outputs_table(self):
        claude = REPO_ROOT / "AGENTS.md"
        if not claude.is_file():
            self.skipTest(
                "repo-root AGENTS.md absent (staged public artifact) — the "
                "regenerative-outputs roster is dev-repo only"
            )
        self.assertIn("generate-routing-table.py", claude.read_text(encoding="utf-8"))




class DeclaredTriggerFieldTests(unittest.TestCase):
    """rule:triggers-are-declared-in-frontmatter, rule:triggers-separator-fits-the-value-type.

    WHY THESE EXIST. Every skill description was shortened to 91-133 characters so a
    discovery surface would stop truncating it. The descriptions had carried their
    trigger phrases inline as quoted spans, and this regenerator's only source was
    those spans, so the shortening emptied the User phrase cell of all 56 user-facing
    rows at once -- while the drift gate stayed green, because an empty cell is not
    drift. The phrases now live in `metadata.triggers`, which a shortening cannot reach.
    """

    def _skills(self) -> list[dict]:
        return json.loads(
            (REPO_ROOT / "crux" / "catalog" / "skills.json").read_text(encoding="utf-8"))

    def test_the_shipped_catalog_carries_the_declared_phrases(self):
        skills = self._skills()
        declaring = [s for s in skills if s.get("triggers")]
        self.assertGreater(len(declaring), 50,
                           "the catalog carries almost no declared triggers")
        for s in declaring:
            with self.subTest(skill=s["id"]):
                self.assertIsInstance(s["triggers"], list)
                self.assertTrue(all(isinstance(t, str) and t.strip() for t in s["triggers"]))

    def test_only_non_user_invocable_skills_declare_none(self):
        """The four that declare nothing are exempt by the declared mechanism, not by
        an allowlist -- so a user-facing skill losing its phrases cannot hide here."""
        skills = self._skills()
        silent = sorted(s["id"] for s in skills if not s.get("triggers"))
        internal = sorted(s["id"] for s in skills if s.get("user-invocable") is False)
        self.assertEqual(silent, internal, f"user-facing skills declaring no trigger: "
                                           f"{sorted(set(silent) - set(internal))}")

    def test_a_comma_bearing_phrase_survives_the_encoding(self):
        """The reason the separator is a pipe. A CSV split would cut this one phrase
        into `this is small` and `skip the cycle`, neither of which anyone says."""
        skills = {s["id"]: s for s in self._skills()}
        self.assertIn("this is small, skip the cycle", skills["fix-directly"]["triggers"])

    def test_no_declared_phrase_contains_the_separator(self):
        """A phrase carrying a pipe cannot round-trip through the encoding, and it
        would also break the Markdown cell it is rendered into."""
        for s in self._skills():
            for phrase in s.get("triggers") or []:
                with self.subTest(skill=s["id"], phrase=phrase):
                    self.assertNotIn("|", phrase)

    def test_the_declared_phrases_reach_the_rendered_region(self):
        """End to end: a phrase declared in frontmatter appears in the §10 table."""
        region, no_triggers = grt.build_region(self._skills())
        self.assertEqual(no_triggers, [])
        for phrase in ("check drift", "just fix it", "what does X do?"):
            with self.subTest(phrase=phrase):
                self.assertIn(f'"{phrase}"', region)


class TriggerSeparatorRefusalTests(unittest.TestCase):
    """rule:triggers-separator-fits-the-value-type — a token carrying the separator is
    refused, not split.

    The limit is stated rather than papered over: in the STRING form `a|b` is
    indistinguishable from two tokens by construction, so only the list form can be
    refused. `DeclaredTriggerFieldTests.test_no_declared_phrase_contains_the_separator`
    is the repo-level gate that covers what the encoding cannot.
    """

    def _lift(self, triggers):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_vc_probe", REPO_ROOT / "crux" / "scripts" / "validate-catalog.py")
        vc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vc)
        return vc._lift_metadata({
            "name": "x", "description": "d",
            "metadata": {"tags": "a, b", "bundles": "crux-core",
                         "risk_level": "low", "triggers": triggers}})

    def test_a_list_token_carrying_the_separator_is_refused(self):
        lifted, errors = self._lift(["fine", "broken|token"])
        self.assertTrue(errors, "a token carrying the separator was accepted")
        self.assertIn("broken|token", errors[0])
        self.assertNotIn("triggers", lifted,
                         "a refused field must not reach the catalog at all")

    def test_a_clean_list_is_accepted(self):
        """POSITIVE CONTROL: the refusal must not reject every list."""
        lifted, errors = self._lift(["fine", "also fine"])
        self.assertEqual(errors, [])
        self.assertEqual(lifted["triggers"], ["fine", "also fine"])

    def test_the_string_form_splits_on_the_separator(self):
        lifted, errors = self._lift("one | two")
        self.assertEqual(errors, [])
        self.assertEqual(lifted["triggers"], ["one", "two"])

    def test_empty_and_null_yield_no_triggers_rather_than_an_error(self):
        for value in ("", None, " | | "):
            with self.subTest(value=value):
                lifted, errors = self._lift(value)
                self.assertEqual(errors, [])
                self.assertEqual(lifted["triggers"], [])

class TriggerDataQualityTests(unittest.TestCase):
    """rule:an-undeclared-trigger-is-a-reported-gap — the §10 table is authoritative,
    so a phrase in it must be one a user says and must name the right skill.

    The phrases were recovered out of prose by regex, and a regex cannot tell a
    trigger from a quoted word. Four got through the first pass and an independent
    review found them: `read-news` declared the bare word `what` (the recovery closed
    a single-quoted span on the apostrophe in `what's new in the ecosystem`);
    `derive-arch` declared `why`, lifted from the parenthetical "ADRs are the secondary
    'why'"; `run-promptbook` declared a phrase from the sentence that sends a read-only
    status query AWAY from its status branch, inverting the inspection/mutation
    split; and `patch-cycle` lost a 71-character sizing phrase to a length ceiling.
    The status branch now owns progress queries directly.
    """

    def _skills(self) -> list[dict]:
        return json.loads(
            (REPO_ROOT / "crux" / "catalog" / "skills.json").read_text(encoding="utf-8"))

    def test_no_phrase_is_claimed_by_two_skills(self):
        """A phrase in two rows routes one request to two skills, and the table cannot
        say which. That is how a lifted phrase announces it landed on the wrong row."""
        owners: dict[str, list[str]] = {}
        for entry in self._skills():
            for phrase in entry.get("triggers") or []:
                owners.setdefault(phrase, []).append(entry["id"])
        shared = {p: ids for p, ids in owners.items() if len(ids) > 1}
        self.assertEqual({}, shared, f"phrases claimed by more than one skill: {shared}")

    def test_no_phrase_contains_the_tables_own_phrase_separator(self):
        """`build_region` joins phrases with ` / `, so a phrase containing one renders
        as several and the cell stops saying which phrase belongs to the row. This is
        the mechanical tell for the `run-promptbook` defect."""
        for entry in self._skills():
            for phrase in entry.get("triggers") or []:
                with self.subTest(skill=entry["id"], phrase=phrase):
                    self.assertNotIn(" / ", phrase)

    def test_no_phrase_is_a_bare_interrogative(self):
        """`what` and `why` alone are not things a user says to pick a skill; they are
        what a quoted word decays to when a span closes on an apostrophe. A real
        single-word trigger is a verb or a noun — `ingest`, `brainstorm`."""
        bare = {"what", "why", "how", "when", "where", "who", "which", "s"}
        for entry in self._skills():
            for phrase in entry.get("triggers") or []:
                with self.subTest(skill=entry["id"], phrase=phrase):
                    self.assertNotIn(phrase.lower().strip("?'\u2019"), bare)

    def test_every_phrase_is_a_non_empty_trimmed_string(self):
        for entry in self._skills():
            for phrase in entry.get("triggers") or []:
                with self.subTest(skill=entry["id"], phrase=phrase):
                    self.assertIsInstance(phrase, str)
                    self.assertEqual(phrase, phrase.strip())
                    self.assertTrue(phrase)

    def test_the_repaired_phrases_are_present_and_the_lifted_ones_are_gone(self):
        """The four findings, pinned by name so a re-run of the recovery cannot
        reintroduce them."""
        by_id = {e["id"]: (e.get("triggers") or []) for e in self._skills()}
        self.assertIn("what's new in the ecosystem", by_id["read-news"])
        self.assertNotIn("what", by_id["read-news"])
        self.assertNotIn("why", by_id["derive-arch"])
        self.assertIn(
            "this is too small for a cycle but it still needs a council and a review",
            by_id["patch-cycle"])
        self.assertIn("how do I resume", by_id["run-promptbook"])
        self.assertIn("where am I", by_id["run-promptbook"])


if __name__ == "__main__":
    unittest.main()


class QuestionsDoNotRouteToWritersTests(unittest.TestCase):
    """A question must not route to a skill that rewrites files.

    rule:an-undeclared-trigger-is-a-reported-gap covers a MISSING phrase; this covers a
    phrase on the wrong side of the read/write line. Live routing in Claude Code sent
    "summarize the current architecture" to `derive-arch` — whose own description says
    "Replaces generated architecture files" — because `derive-arch` declared that
    question among its triggers. The model does not reliably make that mistake, which
    is worse than if it always did: the behaviour sampled differently on two runs, so
    the authoritative §10 table was the only stable statement, and it named the writer.

    An interrogative or "summarize" opens a question. "show" and "list" open a command
    to produce output, which a skill that produces the output may legitimately own —
    `link-adr-graph` keeps "show ADR lineage" for exactly that reason.
    """

    QUESTION = re.compile(r"^(what|why|how|where|when|which|who|summarize)\b|\?$", re.I)
    WRITES = re.compile(r"\b(regenerate|regenerates|replaces|rewrites|overwrites)\b", re.I)

    def _skills(self) -> list[dict]:
        return json.loads(
            (REPO_ROOT / "crux" / "catalog" / "skills.json").read_text(encoding="utf-8"))

    def test_no_write_skill_declares_a_question_shaped_trigger(self):
        skills = self._skills()
        writers = [e for e in skills if self.WRITES.search(e["description"])]
        # Positive control: the catalog must actually contain write-skills, or this
        # test passes by finding nothing to check.
        self.assertGreaterEqual(len(writers), 4, "no write-skills found; the description "
                                                 "wording changed and this gate went blind")
        for entry in writers:
            for phrase in entry.get("triggers") or []:
                with self.subTest(skill=entry["id"], phrase=phrase):
                    self.assertIsNone(
                        self.QUESTION.match(phrase),
                        f"{entry['id']} rewrites files and claims the question {phrase!r}; "
                        "a question belongs to a skill that answers it")

    def test_the_moved_questions_landed_on_the_reader(self):
        by_id = {e["id"]: (e.get("triggers") or []) for e in self._skills()}
        for phrase in ("summarize the current architecture", "what's the current architecture",
                       "what supersedes what", "how do the ADRs relate"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, by_id["query-docs"])
        self.assertNotIn("summarize the current architecture", by_id["derive-arch"])
        self.assertNotIn("what supersedes what", by_id["link-adr-graph"])

    def test_an_output_command_may_stay_with_the_producer(self):
        """The narrowing that keeps this gate from over-firing: `show ADR lineage` is a
        command to produce the lineage view, and the producer keeps it."""
        by_id = {e["id"]: (e.get("triggers") or []) for e in self._skills()}
        self.assertIn("show ADR lineage", by_id["link-adr-graph"])
