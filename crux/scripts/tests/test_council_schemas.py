"""The six council-gate schemas and the withdrawn-alternative patterns.

The schemas under `crux/schemas/` are the source of truth for every record the
gate check reads: a council record (format 1 or 2), a council attempt record, a
refutation record, an owner exception and a reviewer report; and for the run-work
witness, which the council runner reads and which is never gate evidence. These tests prove each schema loads under the vendored
validator's keyword subset (a keyword outside it is rejected on load), that a
valid example of each passes, and that each conditional the schema carries
refuses its violation. Every refusal is paired with the valid example it was
derived from, so a schema that refused everything would fail the positive case.

`council_alternative` owns the patterns for the withdrawn council alternative.
Each pattern is shown to match the historical template text it was written
against, and to match none of the corrected text, the correction notice
included.

Reads only `crux/` and committed fixtures; never the documentation tree.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
SCHEMAS = SCRIPTS.parent / "schemas"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "council_records"

sys.path.insert(0, str(SCRIPTS))

_spec = importlib.util.spec_from_file_location("_vp_for_council_schemas", SCRIPTS / "validate-promptbook.py")
_vp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_vp)

import council_alternative as ca  # noqa: E402  (sys.path insert before import)

SCHEMA_FILES = {
    "council-record": SCHEMAS / "council-record.schema.json",
    "refutation-record": SCHEMAS / "refutation-record.schema.json",
    "owner-exception": SCHEMAS / "owner-exception.schema.json",
    "reviewer-report": SCHEMAS / "reviewer-report.schema.json",
    "council-attempt": SCHEMAS / "council-attempt.schema.json",
    "run-work-witness": SCHEMAS / "run-work-witness.schema.json",
}


def _errors(doc, kind):
    schema = _vp.load_schema(SCHEMA_FILES[kind])
    errors: list[dict] = []
    _vp.validate(doc, schema, "#", "#", errors, kind)
    return errors


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class SchemasLoadUnderTheSubsetTests(unittest.TestCase):
    def test_every_schema_loads_under_the_implemented_keyword_subset(self):
        for kind, path in SCHEMA_FILES.items():
            with self.subTest(kind=kind):
                schema = _vp.load_schema(path)
                self.assertEqual(schema["properties"]["record_type"]["const"], kind)

    def test_an_unimplemented_keyword_is_rejected_on_load(self):
        """Positive control for the load check: the subset gate does fire."""
        with self.assertRaises(_vp.SchemaLoadError):
            _vp.assert_subset({"type": "object", "oneOf": [{"type": "object"}]}, "#")


class CouncilRecordSchemaTests(unittest.TestCase):
    def setUp(self):
        self.ran = _fixture("council-record-ran.json")
        self.cnr = _fixture("council-record-could-not-run.json")

    def test_valid_records_pass(self):
        self.assertEqual(_errors(self.ran, "council-record"), [])
        self.assertEqual(_errors(self.cnr, "council-record"), [])

    def test_implementation_council_uses_its_module_kind(self):
        doc = copy.deepcopy(self.ran)
        doc["council_kind"] = "implementation"
        doc["binding"]["module_tag"] = "implementation-1"
        self.assertEqual(_errors(doc, "council-record"), [])

    def test_module_kind_mismatch_is_refused(self):
        for kind, tag in (("implementation", "adr-1"), ("adr", "implementation-1"),
                          ("verify", "implementation-1"), ("patch", "implementation-1")):
            doc = copy.deepcopy(self.ran)
            doc["council_kind"] = kind
            doc["binding"]["module_tag"] = tag
            with self.subTest(kind=kind, tag=tag):
                self.assertTrue(_errors(doc, "council-record"))

    def test_a_writer_other_than_the_runner_is_refused(self):
        doc = copy.deepcopy(self.ran)
        doc["writer"] = "commander"
        self.assertTrue(_errors(doc, "council-record"))

    def test_ran_requires_quorum_and_could_not_run_forbids_it(self):
        doc = copy.deepcopy(self.ran)
        doc["quorum_met"] = False
        self.assertTrue(_errors(doc, "council-record"))
        doc = copy.deepcopy(self.cnr)
        doc["quorum_met"] = True
        self.assertTrue(_errors(doc, "council-record"))

    def test_could_not_run_requires_a_refusal_reason_and_a_defer_action(self):
        doc = copy.deepcopy(self.cnr)
        doc["refusal_reason"] = None
        self.assertTrue(_errors(doc, "council-record"))
        doc = copy.deepcopy(self.cnr)
        doc["aggregate"]["action"] = "AUTO_EXECUTE"
        self.assertTrue(_errors(doc, "council-record"))

    def test_an_errored_seat_carries_no_decision(self):
        doc = copy.deepcopy(self.ran)
        seat = doc["seats"][0]
        seat["errored"] = True
        self.assertTrue(_errors(doc, "council-record"), "an errored seat still carrying a decision")
        seat["decision"] = None
        self.assertEqual(_errors(doc, "council-record"), [])

    def test_every_seat_carries_its_attempts(self):
        doc = copy.deepcopy(self.ran)
        del doc["seats"][1]["attempts"]
        self.assertTrue(_errors(doc, "council-record"))
        doc = copy.deepcopy(self.ran)
        doc["seats"][1]["attempts"][0]["extra"] = 1
        self.assertTrue(_errors(doc, "council-record"))
        doc = copy.deepcopy(self.ran)
        doc["seats"][1]["attempts"][0]["attempt"] = 3
        self.assertTrue(_errors(doc, "council-record"))

    def test_a_responding_seat_needs_an_attempt_that_matched_both_checks(self):
        # A record cannot show a vote taken from a mismatched reply.
        for field, value in (("served_provider_match", False), ("served_provider_match", None),
                             ("served_model_match", False), ("served_model_match", None),
                             ("fault_label", "truncated")):
            with self.subTest(field=field, value=value):
                doc = copy.deepcopy(self.ran)
                doc["seats"][2]["attempts"][0][field] = value
                self.assertTrue(_errors(doc, "council-record"))
        # Positive controls: a retried seat whose second attempt matched passes, and an
        # errored seat may carry only mismatched attempts.
        doc = copy.deepcopy(self.ran)
        seat = doc["seats"][2]
        failed = dict(seat["attempts"][0], fault_label="truncated", finish_reason="length",
                      served_provider="Amazon Bedrock", served_provider_match=False)
        seat["attempts"] = [failed, dict(seat["attempts"][0], attempt=2)]
        seat["retried"] = True
        self.assertEqual(_errors(doc, "council-record"), [])
        seat.update(errored=True, decision=None, fault_label="served-provider-mismatch")
        seat["attempts"] = [dict(seat["attempts"][0], attempt=1,
                                 fault_label="served-provider-mismatch", finish_reason="stop")]
        self.assertEqual(_errors(doc, "council-record"), [])

    def test_a_finding_id_must_be_seat_qualified(self):
        doc = copy.deepcopy(self.ran)
        doc["seats"][1]["findings"][0]["id"] = "F1"
        self.assertTrue(_errors(doc, "council-record"))

    def test_a_finding_kind_outside_blocking_nit_or_null_is_refused(self):
        doc = copy.deepcopy(self.ran)
        doc["seats"][1]["findings"][0]["kind"] = "minor"
        self.assertTrue(_errors(doc, "council-record"))
        doc["seats"][1]["findings"][0]["kind"] = None
        self.assertEqual(_errors(doc, "council-record"), [])

    def test_written_at_must_carry_microseconds_in_utc(self):
        doc = copy.deepcopy(self.ran)
        doc["written_at"] = "2026-10-01T12:00:00Z"
        self.assertTrue(_errors(doc, "council-record"))

    def test_an_unknown_refusal_code_is_refused(self):
        doc = copy.deepcopy(self.cnr)
        doc["refusal_reason"]["code"] = "waived"
        self.assertTrue(_errors(doc, "council-record"))

    def test_an_unknown_seat_role_is_refused(self):
        doc = copy.deepcopy(self.ran)
        doc["seats"][2]["role"] = "google_fast"
        self.assertTrue(_errors(doc, "council-record"))


class CouncilRecordFormatTwoTests(unittest.TestCase):
    """Format 2 names its attempt and carries a seal; format 1 carries neither. Each refusal is
    paired with a valid fixture that differs from it in the one field under test."""

    def setUp(self):
        self.ran = _fixture("council-record-v2-ran.json")
        self.pre = _fixture("council-record-v2-preflight.json")
        self.v1 = _fixture("council-record-could-not-run.json")

    def test_valid_format_two_records_pass(self):
        self.assertEqual(_errors(self.ran, "council-record"), [])
        self.assertEqual(_errors(self.pre, "council-record"), [])

    def test_a_format_two_ran_record_must_name_an_attempt(self):
        doc = copy.deepcopy(self.ran)
        doc["attempt"] = None
        self.assertTrue(_errors(doc, "council-record"), "a ran record naming no attempt")
        del doc["attempt"]
        self.assertTrue(_errors(doc, "council-record"), "a ran record without the attempt key")

    def test_a_format_two_record_must_carry_a_seal(self):
        doc = copy.deepcopy(self.ran)
        del doc["seal"]
        self.assertTrue(_errors(doc, "council-record"))
        doc["seal"] = "sha256:" + "0" * 63
        self.assertTrue(_errors(doc, "council-record"), "a malformed seal")

    def test_a_format_two_record_never_carries_a_format_one_preflight_code(self):
        for code in ("refused-input", "refused-subject"):
            with self.subTest(code=code):
                doc = copy.deepcopy(self.pre)
                doc["refusal_reason"]["code"] = code
                self.assertTrue(_errors(doc, "council-record"))

    def test_a_preflight_code_names_no_attempt_and_is_could_not_run(self):
        for code in ("preflight-needs-owner", "preflight-retries-spent"):
            with self.subTest(code=code):
                doc = copy.deepcopy(self.pre)
                doc["refusal_reason"]["code"] = code
                self.assertEqual(_errors(doc, "council-record"), [], "control: the preflight record is valid")
                doc["attempt"] = copy.deepcopy(self.ran["attempt"])
                self.assertTrue(_errors(doc, "council-record"), "a preflight code naming an attempt")
        doc = copy.deepcopy(self.ran)
        doc["refusal_reason"] = {"code": "preflight-needs-owner", "names": ["subjects[0].path"]}
        doc["attempt"] = None
        self.assertTrue(_errors(doc, "council-record"), "a preflight code on a ran record")

    def test_every_other_format_two_could_not_run_record_names_an_attempt(self):
        doc = copy.deepcopy(self.pre)
        doc["refusal_reason"] = {"code": "no-key", "names": ["OPENROUTER_API_KEY"]}
        self.assertTrue(_errors(doc, "council-record"), "a post-claim could-not-run record naming no attempt")
        doc["attempt"] = copy.deepcopy(self.ran["attempt"])
        self.assertEqual(_errors(doc, "council-record"), [])

    def test_format_one_carries_no_attempt_no_seal_and_no_preflight_code(self):
        self.assertEqual(_errors(self.v1, "council-record"), [], "control: the format-1 record is valid")
        for key, value in (("attempt", None), ("attempt", self.ran["attempt"]), ("seal", self.ran["seal"])):
            with self.subTest(key=key):
                doc = copy.deepcopy(self.v1)
                doc[key] = copy.deepcopy(value)
                self.assertTrue(_errors(doc, "council-record"))
        for code in ("preflight-needs-owner", "preflight-retries-spent"):
            with self.subTest(code=code):
                doc = copy.deepcopy(self.v1)
                doc["refusal_reason"]["code"] = code
                self.assertTrue(_errors(doc, "council-record"))
        doc = copy.deepcopy(self.v1)
        doc["refusal_reason"]["code"] = "refused-input"
        self.assertEqual(_errors(doc, "council-record"), [], "control: refused-input stays a format-1 code")

    def test_an_unknown_format_version_is_refused(self):
        doc = copy.deepcopy(self.ran)
        doc["format_version"] = "3"
        self.assertTrue(_errors(doc, "council-record"))
        doc["format_version"] = 2
        self.assertTrue(_errors(doc, "council-record"), "an integer format version")

    def test_the_attempt_binding_is_closed_and_hash_shaped(self):
        for key, value in (("attempt_id", "XYZ"), ("sha256", "abc"), ("extra", 1)):
            with self.subTest(key=key):
                doc = copy.deepcopy(self.ran)
                doc["attempt"][key] = value
                self.assertTrue(_errors(doc, "council-record"))


class CouncilAttemptSchemaTests(unittest.TestCase):
    def setUp(self):
        self.doc = _fixture("council-attempt.json")

    def test_valid_record_passes(self):
        self.assertEqual(_errors(self.doc, "council-attempt"), [])

    def test_each_constraint_refuses_its_violation(self):
        cases = (
            ("ordinal", 0), ("attempt_id", "0123456789ABCDEF0123456789ABCDEF"), ("writer", "commander"),
            ("format_version", "2"), ("round", 0), ("council_kind", "review"), ("written_at", "2026-10-01T12:00:00Z"),
        )
        for key, value in cases:
            with self.subTest(key=key):
                doc = copy.deepcopy(self.doc)
                doc[key] = value
                self.assertTrue(_errors(doc, "council-attempt"))

    def test_a_seal_and_every_hash_are_required(self):
        doc = copy.deepcopy(self.doc)
        del doc["seal"]
        self.assertTrue(_errors(doc, "council-attempt"))
        doc = copy.deepcopy(self.doc)
        doc["subjects"][0]["sha256"] = None
        self.assertTrue(_errors(doc, "council-attempt"), "an attempt binds only read subjects")
        doc = copy.deepcopy(self.doc)
        doc["question"]["sha256"] = None
        self.assertTrue(_errors(doc, "council-attempt"))
        doc = copy.deepcopy(self.doc)
        doc["subjects"] = []
        self.assertTrue(_errors(doc, "council-attempt"))

    def test_an_attempt_is_not_a_council_record(self):
        self.assertTrue(_errors(self.doc, "council-record"))
        self.assertTrue(_errors(_fixture("council-record-v2-ran.json"), "council-attempt"))


class RunWorkWitnessSchemaTests(unittest.TestCase):
    def setUp(self):
        self.doc = {"record_type": "run-work-witness", "format_version": "1",
                    "book": {"id": "PB-0999", "content_hash": "sha256:" + "a" * 64}, "run_id": "RUN-001",
                    "entries": [{"path": "docs/adrs/ADR-0001-fixture.md", "sha256": "4" * 64, "prompt": 1,
                                 "written_at": "2026-10-01T12:00:00.000001Z"}]}

    def test_valid_witness_passes(self):
        self.assertEqual(_errors(self.doc, "run-work-witness"), [])
        doc = copy.deepcopy(self.doc)
        doc["entries"] = []
        self.assertEqual(_errors(doc, "run-work-witness"), [], "an empty witness is valid")

    def test_each_entry_constraint_refuses_its_violation(self):
        for key, value in (("sha256", None), ("prompt", 0), ("path", ""), ("extra", 1), ("written_at", "today")):
            with self.subTest(key=key):
                doc = copy.deepcopy(self.doc)
                doc["entries"][0][key] = value
                self.assertTrue(_errors(doc, "run-work-witness"))
        doc = copy.deepcopy(self.doc)
        del doc["entries"][0]["sha256"]
        self.assertTrue(_errors(doc, "run-work-witness"))


class RefutationRecordSchemaTests(unittest.TestCase):
    def setUp(self):
        self.doc = _fixture("refutation-record.json")

    def test_valid_record_passes(self):
        self.assertEqual(_errors(self.doc, "refutation-record"), [])

    def test_a_verdict_word_is_not_a_result_token(self):
        doc = copy.deepcopy(self.doc)
        doc["entries"][0]["result_token"] = "refuted"
        self.assertTrue(_errors(doc, "refutation-record"))

    def test_an_unknown_recorder_role_is_refused(self):
        doc = copy.deepcopy(self.doc)
        doc["recorder_role"] = "reviewer"
        self.assertTrue(_errors(doc, "refutation-record"))

    def test_a_verify_module_is_refused(self):
        doc = copy.deepcopy(self.doc)
        doc["module_tag"] = "verify-1"
        self.assertTrue(_errors(doc, "refutation-record"))

    def test_an_implementation_module_never_admits_refutation(self):
        doc = copy.deepcopy(self.doc)
        doc["module_tag"] = "implementation-1"
        self.assertTrue(_errors(doc, "refutation-record"))

    def test_a_missing_token_is_refused(self):
        doc = copy.deepcopy(self.doc)
        del doc["entries"][0]["result_token"]
        self.assertTrue(_errors(doc, "refutation-record"))


class OwnerExceptionSchemaTests(unittest.TestCase):
    def setUp(self):
        self.doc = _fixture("owner-exception.json")

    def test_valid_record_passes(self):
        self.assertEqual(_errors(self.doc, "owner-exception"), [])

    def test_implementation_admits_only_existing_above_three_exception(self):
        doc = copy.deepcopy(self.doc)
        doc.update(module_tag="implementation-1", prompt=None)
        doc["place"] = {"kind": "round-above-three", "round": 4}
        self.assertEqual(_errors(doc, "owner-exception"), [])
        doc["place"] = {"kind": "adr-round-3-council", "round": 3}
        self.assertTrue(_errors(doc, "owner-exception"))

    def test_place_kind_and_round_must_agree(self):
        doc = copy.deepcopy(self.doc)
        doc["place"]["round"] = 4
        self.assertTrue(_errors(doc, "owner-exception"), "adr-round-3-council at round 4")
        doc["place"] = {"kind": "round-above-three", "round": 3}
        self.assertTrue(_errors(doc, "owner-exception"), "round-above-three at round 3")
        doc["place"] = {"kind": "round-above-three", "round": 4}
        self.assertEqual(_errors(doc, "owner-exception"), [])

    def test_module_or_prompt_names_the_place_never_both_or_neither(self):
        doc = copy.deepcopy(self.doc)
        doc["prompt"] = 2
        self.assertTrue(_errors(doc, "owner-exception"), "module and prompt both named")
        doc["module_tag"] = None
        self.assertEqual(_errors(doc, "owner-exception"), [])
        doc["prompt"] = None
        self.assertTrue(_errors(doc, "owner-exception"), "neither module nor prompt named")

    def test_a_void_attempt_exception_is_valid_without_a_place(self):
        self.assertEqual(_errors(_fixture("owner-exception-void-attempt.json"), "owner-exception"), [])

    def test_an_exception_carries_exactly_one_of_place_and_void_attempt(self):
        void = _fixture("owner-exception-void-attempt.json")
        doc = copy.deepcopy(void)
        doc["place"] = copy.deepcopy(self.doc["place"])
        self.assertTrue(_errors(doc, "owner-exception"), "both place and void_attempt")
        doc = copy.deepcopy(void)
        del doc["void_attempt"]
        self.assertTrue(_errors(doc, "owner-exception"), "neither place nor void_attempt")

    def test_a_void_attempt_names_a_path_and_a_hash(self):
        for key, value in (("sha256", "abc"), ("path", ""), ("extra", 1)):
            with self.subTest(key=key):
                doc = _fixture("owner-exception-void-attempt.json")
                doc["void_attempt"][key] = value
                self.assertTrue(_errors(doc, "owner-exception"))


class ReviewerReportSchemaTests(unittest.TestCase):
    def setUp(self):
        self.paths = _fixture("reviewer-report-paths.json")
        self.range = _fixture("reviewer-report-range.json")

    def test_valid_reports_pass(self):
        self.assertEqual(_errors(self.paths, "reviewer-report"), [])
        self.assertEqual(_errors(self.range, "reviewer-report"), [])

    def test_the_reviewer_role_is_required(self):
        doc = copy.deepcopy(self.paths)
        doc["reviewer_role"] = "commander"
        self.assertTrue(_errors(doc, "reviewer-report"))

    def test_each_subject_form_requires_its_own_field(self):
        doc = copy.deepcopy(self.range)
        del doc["subject"]["range"]
        self.assertTrue(_errors(doc, "reviewer-report"))
        doc = copy.deepcopy(self.paths)
        del doc["subject"]["paths"]
        self.assertTrue(_errors(doc, "reviewer-report"))

    def test_a_council_record_is_not_a_reviewer_report(self):
        self.assertTrue(_errors(_fixture("council-record-ran.json"), "reviewer-report"))


# Historical template text, copied verbatim (line breaks included) from the
# cycle templates as they stood before the correction. Fixtures, not reads of the
# live templates, so this test does not move when the templates are corrected.
HISTORICAL = {
    "native-agents-equivalent": (
        "Invoke the `council` skill (multi-model: Claude + Gemini + GPT, async)\n"
        "    — or, equivalently, dispatch 5 parallel review agents via the `Agent`\n"
        "    tool — each owning one quality dimension:"
    ),
    "native-agents-alternative": (
        "Invoke the `council` skill (Claude + Gemini + GPT, async) — or dispatch\n"
        "      5 parallel review agents — each owning one quality dimension:"
    ),
    "parallel-reviewer-fallback": (
        "When the environment lacks LLM provider keys for full multi-model "
        "deliberation, use the sanctioned parallel-reviewer fallback: run two reviewer agents"
    ),
    "deep-review-agent-settles": (
        "Otherwise resolve findings autonomously (`srde`, then a read-only\n"
        "      deep-review `Agent` for what srde cannot settle) and continue."
    ),
    "deep-architecture-agent": (
        "(c) For dissents `srde` cannot resolve, dispatch ONE dedicated\n"
        "    deep-architecture-review `Agent` that reads the full ADR graph\n"
        "    (`docs/adrs/index.md`, every cited ADR, `docs/AGENTS.md` invariants) and"
    ),
    "council-models-in-prose": "Then invoke the `council` skill (Claude + Gemini + GPT, async) over the",
    "one-dimension-per-seat": "diagnosis. Each seat owns one dimension: (1) Evidence/reproduction; (2)",
}

CORRECTED = (
    "Convene the council with the council runner, `run-council.py`, which obtains\n"
    "verdicts from three providers through the gateway on the models the router\n"
    "registry assigns. Every seat assesses every dimension: Completeness,\n"
    "Correctness, Consistency, Clarity and Security. A reviewer agent may assemble\n"
    "evidence, fenced as data in the question; it casts no vote. When the council\n"
    "cannot run, the runner writes a DEFER_TO_HUMAN record and the gate stops."
)


def _patterns(node, found=None):
    """Every `pattern` string in a schema, at any depth."""
    found = [] if found is None else found
    if isinstance(node, dict):
        if isinstance(node.get("pattern"), str):
            found.append(node["pattern"])
        for value in node.values():
            _patterns(value, found)
    elif isinstance(node, list):
        for item in node:
            _patterns(item, found)
    return found


END = "(?![\\s\\S])"  # the portable end anchor


class TrailingNewlineTests(unittest.TestCase):
    """Python's `$` matches before a trailing newline, so a value ending in one passed a `^...$`
    pattern. The record schemas end each anchored pattern with `(?![\\s\\S])`, an end anchor
    that means the same in Python's `re` and in ECMA-262, the dialect draft 2020-12 names.
    (`\\Z` is Python-only: ECMA-262 rejects it under the `u` flag and reads a literal `Z` without.)"""

    def test_a_trailing_newline_fails_one_pattern_in_each_schema_and_the_plain_value_passes(self):
        cases = (
            ("council-record", "council-record-ran.json", ("written_at",)),
            ("council-attempt", "council-attempt.json", ("written_at",)),
            ("refutation-record", "refutation-record.json", ("written_at",)),
            ("owner-exception", "owner-exception.json", ("date",)),
            ("reviewer-report", "reviewer-report-paths.json", ("written_at",)),
        )
        for kind, name, key in cases:
            with self.subTest(kind=kind):
                doc = _fixture(name)
                self.assertEqual(_errors(doc, kind), [], "control: the fixture is valid")
                doc[key[0]] = doc[key[0]] + "\n"
                self.assertTrue(_errors(doc, kind), f"{kind}.{key[0]} with a trailing newline")

    def test_no_pattern_in_the_record_schemas_ends_in_a_bare_dollar(self):
        total = 0
        for kind, path in SCHEMA_FILES.items():
            for pattern in _patterns(json.loads(path.read_text(encoding="utf-8"))):
                total += 1
                with self.subTest(kind=kind, pattern=pattern):
                    self.assertTrue(pattern.endswith(END), pattern)
                    self.assertNotIn("\\Z", pattern)
        self.assertGreater(total, 20, "the walk must find the patterns it is asserting about")

    def test_the_portable_anchor_refuses_a_trailing_newline_and_accepts_the_plain_value(self):
        import re
        pattern = "^[0-9a-f]{4}" + END
        self.assertIsNone(re.search(pattern, "abcd\n"))
        self.assertIsNotNone(re.search(pattern, "abcd"))  # control
        self.assertIsNone(re.search(pattern, "abcdZ"))


class WithdrawnAlternativeTests(unittest.TestCase):
    def test_each_pattern_matches_the_historical_text_it_was_written_against(self):
        for pid, text in HISTORICAL.items():
            with self.subTest(pattern=pid):
                self.assertIn(pid, ca.find_withdrawn_alternative(text))

    def test_every_pattern_has_a_historical_positive_control(self):
        self.assertEqual({p.id for p in ca.WITHDRAWN_ALTERNATIVE_PATTERNS}, set(HISTORICAL))

    def test_corrected_text_and_the_correction_notice_match_nothing(self):
        self.assertEqual(ca.find_withdrawn_alternative(CORRECTED), [])
        self.assertEqual(ca.find_withdrawn_alternative(ca.CORRECTION_NOTICE), [])

    def test_the_deep_architecture_pattern_matches_both_forms_and_not_a_bare_mention(self):
        for text in ("dispatch ONE deep-architecture `Agent`", "a Deep architecture review Agent reads it"):
            with self.subTest(text=text):
                self.assertEqual(ca.find_withdrawn_alternative(text), ["deep-architecture-agent"])
        for text in ("an architecture review agent may assemble evidence", "the deep architecture work is done"):
            with self.subTest(text=text):
                self.assertEqual(ca.find_withdrawn_alternative(text), [])

    def test_a_phrase_broken_across_lines_still_matches(self):
        text = "— or,\n            equivalently,\n   dispatch 5 parallel\n review agents"
        self.assertEqual(ca.find_withdrawn_alternative(text), ["native-agents-equivalent"])

    def test_non_text_carries_nothing(self):
        for value in (None, 7, "", ["or 5 parallel review agents"]):
            with self.subTest(value=value):
                self.assertEqual(ca.find_withdrawn_alternative(value), [])


if __name__ == "__main__":
    unittest.main()
