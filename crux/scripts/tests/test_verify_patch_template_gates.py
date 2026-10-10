"""The verify-path and patch templates run council deliberation through the council
runner and gate independent review on reviewer reports.

Covers `cycle-module-verify.yaml`, `iterate-promptbook-template.yaml` and
`patch-promptbook-template.yaml`. A template changes only new books, so every
assertion here reads the template text; none of them touches an existing book.

Positive controls re-introduce the withdrawn text in memory and expect the
detectors to report it.
"""

import re
import sys
import unittest
from pathlib import Path

import yaml

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

from council_alternative import find_withdrawn_alternative  # noqa: E402

TEMPLATES = SCRIPTS.parent / "templates"
VERIFY = TEMPLATES / "cycle-module-verify.yaml"
ITERATE = TEMPLATES / "iterate-promptbook-template.yaml"
PATCH = TEMPLATES / "patch-promptbook-template.yaml"

REVIEW_GATE = (
    "**The independent-review gate.** This prompt advances only with reviewer\n"
    "reports attached (rule:review-gate-needs-a-reviewer-report). Commit the\n"
    "work, then fix the reviewed range as\n"
    "`<the run's base_commit>..<HEAD at dispatch>`. The gate check does not\n"
    "compare the base, so hold it. Each reviewer writes its own\n"
    'report: `uv run "${CRUX_PLUGIN_ROOT}/scripts/write-review-report.py"\n'
    "<run-RUN-NNN.yaml> --prompt <n> --range <base>..<end> --verdict\n"
    "'<verdict>'`, adding one `--finding '<finding>'` per finding. Single-quote\n"
    "each value and write an embedded `'` as `'\\''`; never put model-written\n"
    "text inside double quotes. Text with quotes or several lines goes in a\n"
    "file passed with `--verdict-file` or `--finding-file`. A reviewer whose\n"
    "harness grants no shell returns those fields, and the agent that\n"
    "commissioned the review runs the writer with them. From the range end to\n"
    "the advance, write nothing to the run snapshot or the book: the range\n"
    "covers both, so a later write to either refuses every report. Commit only\n"
    "the reports before the advance: a commit after the range end that changes\n"
    "a reviewed path refuses every report. Record notes, deferrals and gate\n"
    "tokens after the advance. Attach every report with `--artifacts`. A\n"
    "council record never satisfies this gate."
)

CLOSE = ("Before logging, advance this prompt with the latest council record of this "
         "module in `--artifacts`. A `pass` verdict allows `--outcome done`. Otherwise "
         "advance with `--outcome blocked`: a `route` verdict returns the run to the "
         "address-findings prompt, and a `stop` verdict keeps the run here for the "
         "user. Convene a round here only when this module has no council record, "
         "because its council ran before council records existed.")
THIRD_ROUND = ("After a third round that did not converge, advance to module close "
               "with the record attached; the gate check stops the run there at stop 1.")
COULD_NOT_RUN = ("The council runner's exit code and the record's `outcome` decide the "
                 "contradicted-premise stop: exit 1 with `outcome: could-not-run` and any "
                 "code but `preflight-retries-spent`, or a record that ran whose "
                 "`refusal_reason` names the secret scan. The "
                 "aggregate action DEFER_TO_HUMAN does not decide it, because a "
                 "council that ran carries that action on a SPLIT or on low "
                 "confidence, for example.")
NON_PASS_BLOCKED = ("A record that did not converge advances with `--outcome blocked` "
                    "and `--artifacts`; `--outcome done` is refused on any verdict "
                    "other than `pass`.")
DIMENSIONS = ("Evidence/Reproduction", "Root-cause correctness",
              "Approach soundness", "Consistency", "Security")
PATCH_DIMENSIONS = ("Evidence/reproduction", "Root-cause correctness",
                    "Approach soundness and minimality", "Blast-radius proportion",
                    "Security")
FORBIDDEN_STARTS = ("rule:", "[", "{", "--")
MODEL_TOKENS = re.compile(r"\b(Claude|Gemini|GPT|Opus|Sonnet|Haiku)\b")


def norm(text):
    return re.sub(r"\s+", " ", text).strip()


def load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def verify_module():
    return load(VERIFY)


def iterate_prompts():
    return load(ITERATE)["prompts"]


def patch_prompts():
    return {p["phase"]: p for p in load(PATCH)["prompts"]}


def substitute(text):
    """Resolve the verify module's tokens as `iterate` does for a single verify module."""
    return text.replace(" {M}", "").replace("{M}", "")


def forbidden_line_starts(strings):
    return [ln for s in strings for ln in s.splitlines()
            if ln.lstrip().startswith(FORBIDDEN_STARTS)]


def all_strings(node):
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in all_strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in all_strings(v)]
    return []


class VerifyModuleTests(unittest.TestCase):

    def setUp(self):
        self.p = verify_module()[1]["prompt"]

    def test_names_the_council_runner_and_gate_info(self):
        self.assertIn("run-council.py", self.p)
        self.assertIn("--gate-info", self.p)
        self.assertIn("--artifacts", self.p)

    def test_every_seat_assesses_the_five_dimensions(self):
        for d in DIMENSIONS:
            self.assertIn(d, self.p)
        self.assertIn("every seat assesses every dimension", norm(self.p))

    def test_escape_verdict_is_the_architectural_token(self):
        self.assertIn("`ARCHITECTURAL`", self.p)
        self.assertIn("premise-contradiction stop", norm(self.p))

    def test_no_withdrawn_alternative_and_deep_review_only_widens_the_subjects(self):
        self.assertEqual(find_withdrawn_alternative(self.p), [])
        self.assertNotIn("equivalent", self.p)
        # `--deep-review` adds subjects to round 1 and nothing else: no agent, no srde.
        self.assertIn("When the book was authored with `--deep-review`, round 1 also "
                      "passes `docs/adrs/index.md`, every ADR the diagnosis cites and "
                      "`docs/AGENTS.md` as a `--subject`, each tracked and clean; that "
                      "round counts like any other.", norm(self.p))
        self.assertEqual(norm(self.p).count("--deep-review"), 1)
        self.assertIsNone(MODEL_TOKENS.search(self.p))

    def test_srde_is_evidence_only(self):
        seen = False
        for p in verify_module():
            text = norm(p["prompt"])
            if "srde" in text:
                seen = True
                self.assertIn("never settles", text)
        self.assertTrue(seen, "the verify path keeps srde as evidence")

    def test_council_prompt_lists_only_the_council_side_effect(self):
        # The runner is the council path; srde is evidence and never a step of this prompt.
        self.assertEqual(verify_module()[1]["side_effects"], ["council"])

    def test_ordinal_three_drops_srde_as_resolver(self):
        self.assertNotIn("council/`srde`", verify_module()[2]["prompt"])
        self.assertNotIn("srde", verify_module()[2]["prompt"])

    def test_module_close_advances_with_the_council_record(self):
        text = norm(verify_module()[3]["prompt"])
        self.assertIn(CLOSE, text)
        self.assertNotIn("--gate-info", text)
        self.assertNotIn("confirm that", text)

    def test_ordinal_three_leaves_the_stop_to_the_gate_check(self):
        text = norm(verify_module()[2]["prompt"])
        self.assertIn(THIRD_ROUND, text)
        self.assertNotIn("STOP and escalate", text)

    def test_question_step_states_the_escape_verdict(self):
        self.assertIn("states the escape verdict: a seat that finds the work needs a "
                      "new decision votes `ARCHITECTURAL`", norm(self.p))

    def test_could_not_run_is_decided_by_exit_code_and_outcome(self):
        text = norm(self.p)
        self.assertIn(COULD_NOT_RUN, text)
        self.assertIn(NON_PASS_BLOCKED, text)

    def test_header_comment_names_no_srde_resolver(self):
        header = VERIFY.read_text(encoding="utf-8").split("\n- title:", 1)[0]
        self.assertNotIn("deep-review", header)
        self.assertEqual(find_withdrawn_alternative(header), [])

    def test_no_block_scalar_line_opens_with_a_forbidden_token(self):
        self.assertEqual(forbidden_line_starts(all_strings(verify_module())), [])

    def test_control_withdrawn_text_is_detected(self):
        bad = self.p + "\nor, equivalently, dispatch 5 parallel review agents."
        self.assertNotEqual(find_withdrawn_alternative(bad), [])


class IterateTemplateTests(unittest.TestCase):

    def test_embedded_council_prompt_equals_the_module_after_parse(self):
        mod = verify_module()[1]
        emb = iterate_prompts()[1]
        for field in ("title", "purpose", "prompt", "expected_output"):
            self.assertEqual(norm(substitute(mod[field])), norm(emb[field]), field)
        self.assertEqual(mod["side_effects"], emb["side_effects"])

    def test_embedded_ordinals_three_and_four_carry_the_module_changes(self):
        emb = iterate_prompts()
        self.assertNotIn("srde", emb[2]["prompt"])
        self.assertIn(CLOSE, norm(emb[3]["prompt"]))
        self.assertNotIn("--gate-info", emb[3]["prompt"])
        self.assertIn(THIRD_ROUND, norm(emb[2]["prompt"]))
        self.assertEqual(emb[2]["title"],
                         "Verify: address council findings, re-convene the council")

    def test_review_gate_paragraph_is_the_last_paragraph_of_review_ordinal_one(self):
        review1 = [p for p in iterate_prompts() if p.get("module_tag") == "review-1"][0]["prompt"]
        self.assertTrue(review1.rstrip("\n").endswith(REVIEW_GATE), review1[-700:])
        head = review1.rstrip("\n")[: -len(REVIEW_GATE)]
        self.assertTrue(head.endswith(
            "anything it cannot re-derive is unobserved in its own report.\n\n"))

    def test_run_autonomy_stop_for_a_council_that_cannot_run(self):
        text = norm(load(ITERATE)["run_autonomy"])
        self.assertIn("a council that cannot run through the council runner yields DEFER_TO_HUMAN", text)

    def test_strategy_names_srde_as_evidence_only_if_at_all(self):
        strategy = norm(load(ITERATE)["strategy"])
        self.assertNotIn("`council`, `srde`, `log-work`", strategy)
        self.assertIn("`srde` is evidence only", strategy)

    def test_no_withdrawn_alternative_in_any_prompt(self):
        for p in iterate_prompts():
            with self.subTest(n=p["n"]):
                for field in ("title", "purpose", "prompt", "expected_output"):
                    self.assertEqual(find_withdrawn_alternative(p.get(field, "")), [], field)

    def test_no_block_scalar_line_opens_with_a_forbidden_token(self):
        self.assertEqual(forbidden_line_starts(all_strings(load(ITERATE))), [])


class PatchTemplateTests(unittest.TestCase):

    def setUp(self):
        self.v = patch_prompts()["verify"]["prompt"]
        self.r = patch_prompts()["review"]["prompt"]

    def test_verify_names_the_runner_and_lists_five_dimensions_for_every_seat(self):
        self.assertIn("run-council.py", self.v)
        for d in PATCH_DIMENSIONS:
            self.assertIn(d, norm(self.v))
        self.assertIn("every seat assesses every dimension", norm(self.v))
        self.assertIn("**Blast-radius proportion**", self.v)

    def test_verify_deletes_the_withdrawn_wording(self):
        self.assertEqual(find_withdrawn_alternative(self.v), [])
        self.assertNotIn("owns one dimension", self.v)
        self.assertIsNone(MODEL_TOKENS.search(self.v))
        self.assertNotIn("srde", self.v)
        self.assertNotIn("deep-review", self.v)

    def test_review_phase_records_gate_tokens_after_the_advance(self):
        # The review range covers the run snapshot, so a notes write before the advance
        # refuses every reviewer report.
        text = norm(self.r)
        self.assertIn("to RECORD in the run `notes` after this prompt's advance", text)
        self.assertNotIn("RECORD each in the run `notes`", text)

    def test_verify_closes_only_on_convergence_inside_one_prompt(self):
        text = norm(self.v)
        self.assertIn("re-convene here", text)
        self.assertIn("only on convergence", text)
        self.assertIn("council kind `patch`", text)

    def test_three_escape_verdicts_stay_as_stop_four(self):
        text = norm(self.v)
        for needle in ("recommend `dev-cycle`", "recommend `iterate`", "re-author the book",
                       "premise-contradiction stop"):
            self.assertIn(needle, text)

    def test_review_gate_paragraph_is_the_last_paragraph_of_the_review_phase(self):
        self.assertTrue(self.r.rstrip("\n").endswith(REVIEW_GATE), self.r[-700:])
        self.assertTrue(self.r.rstrip("\n")[: -len(REVIEW_GATE)].endswith("\n\n"))

    def test_run_autonomy_stop_for_a_council_that_cannot_run(self):
        text = norm(load(PATCH)["run_autonomy"])
        self.assertIn("a council that cannot run through the council runner yields DEFER_TO_HUMAN", text)

    def test_verify_states_the_three_escape_verdicts_in_the_question(self):
        self.assertIn("states the three escape verdicts, each raised as a blocking "
                      "finding", norm(self.v))

    def test_verify_decides_could_not_run_by_exit_code_and_outcome(self):
        text = norm(self.v)
        self.assertIn(COULD_NOT_RUN, text)
        self.assertIn(NON_PASS_BLOCKED.replace("advances with", "goes to the gate check with"), text)
        self.assertIn("A `pass` verdict refuses `--outcome blocked`; advance `done`.", text)

    def test_header_comment_describes_the_gates_by_their_evidence(self):
        header = PATCH.read_text(encoding="utf-8").split("\nformat_version", 1)[0]
        self.assertNotIn("deep-review", header)
        self.assertEqual(find_withdrawn_alternative(header), [])

    def test_no_block_scalar_line_opens_with_a_forbidden_token(self):
        self.assertEqual(forbidden_line_starts(all_strings(load(PATCH))), [])

    def test_control_detector_reports_the_withdrawn_wording(self):
        bad = self.v + "\nEach seat owns one dimension. (Claude + Gemini + GPT, async)"
        self.assertNotEqual(find_withdrawn_alternative(bad), [])


class BaseCommitReproductionTests(unittest.TestCase):
    """Each prompt-1 twin tells the reader to reproduce at the run's base commit."""

    HEAD = ("Reproduce at the run's `base_commit`, and record that commit in the "
            "Diagnosis.")
    CLAUSES = (
        "A textual reproduction quotes text",
        "An executed reproduction runs code",
        "`git diff --quiet <base_commit> -- <paths>` exits 0",
        "`git ls-files --others --exclude-standard -- <paths>` prints nothing",
        "where `<paths>` covers every input the reproduction reads",
        "`git worktree add --detach <private scratch path> <base_commit>`",
        "record that as a stated limit of the evidence, never as a reproduction",
    )
    TAIL = "When `base_commit` is null, say so and record the commit you used."

    def twins(self):
        return {
            "verify": verify_module()[0],
            "iterate": next(p for p in iterate_prompts() if p["n"] == 1),
            "patch": patch_prompts()["verify"],
        }

    def paragraph(self, text):
        flat = norm(text)
        start = flat.index(self.HEAD)
        end = flat.index(self.TAIL) + len(self.TAIL)
        return flat[start:end]

    def test_each_twin_carries_head_clauses_and_tail_once(self):
        for name, prompt in self.twins().items():
            flat = norm(prompt["prompt"])
            for piece in (self.HEAD, *self.CLAUSES, self.TAIL):
                self.assertEqual(flat.count(piece), 1, f"{name}: {piece}")

    def test_paragraph_identical_across_twins(self):
        paragraphs = {n: self.paragraph(p["prompt"]) for n, p in self.twins().items()}
        self.assertEqual(len(set(paragraphs.values())), 1, paragraphs)

    def test_expected_output_names_base_commit(self):
        for name, prompt in self.twins().items():
            flat = norm(prompt["expected_output"])
            self.assertIn("the `base_commit` the reproduction ran at", flat, name)
            self.assertIn("each reproduction's kind (textual or executed)", flat, name)
            self.assertIn("any stated limit", flat, name)

    def test_no_shared_tmp_path_or_head_diff(self):
        for name, prompt in self.twins().items():
            flat = norm(prompt["prompt"])
            self.assertNotIn("<base_commit> HEAD --", flat, name)
            self.assertNotIn("/tmp/", flat, name)
            self.assertIn("never under a shared `/tmp` path", flat, name)


class DetectorControlTests(unittest.TestCase):

    def test_model_token_detector_reports_a_model_name(self):
        self.assertIsNotNone(MODEL_TOKENS.search("Claude + Gemini"))

    def test_forbidden_line_start_detector_reports_each_shape(self):
        for opener in ("rule:x", "[a]", "{b}", "--c"):
            self.assertEqual(forbidden_line_starts([f"ok\n  {opener}\nok"]), [f"  {opener}"])


if __name__ == "__main__":
    unittest.main()
