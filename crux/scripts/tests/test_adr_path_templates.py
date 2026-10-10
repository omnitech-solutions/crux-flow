"""The ADR-path cycle templates run council deliberation through the council runner
and gate independent review on reviewer reports.

Three templates carry the ADR module or the review module's first prompt:
`cycle-module-adr.yaml`, `cycle-promptbook-template.yaml` (which embeds a copy of
the ADR module and of the review module) and `cycle-module-review.yaml`. This suite
parses each as YAML and checks:

1. the council prompt (ordinal 2 of the ADR module) names `run-council.py`, names the
   five dimensions, and carries no `srde` and no withdrawn council alternative;
2. the ADR module copy in the canonical book equals the module file's prompts for
   ordinals 2 to 4 byte for byte after the `{M}` substitution;
3. the review prompt (ordinal 1 of the review module) carries the independent-review
   gate paragraph byte for byte as its last paragraph, in the module and in the book;
4. the canonical book's `run_autonomy` lists the stop for a council that cannot run;
5. no block-scalar line opens with `rule:`, `[`, `{` or `--`, which YAML or the
   templates' own grammar would misread.

Every check takes injected text, so each positive control seeds one violation in
memory and expects the check to report it. Stdlib plus PyYAML; reads only the
shipped templates, so it is safe against the staged artifact.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import yaml

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import council_alternative  # noqa: E402

TEMPLATES = SCRIPTS.parent / "templates"
ADR_MODULE = TEMPLATES / "cycle-module-adr.yaml"
REVIEW_MODULE = TEMPLATES / "cycle-module-review.yaml"
BOOK = TEMPLATES / "cycle-promptbook-template.yaml"
GATES = TEMPLATES.parent / "skills" / "run-promptbook" / "references" / "gates.md"

DIMENSIONS = ("Completeness", "Correctness", "Consistency", "Clarity", "Security")

REVIEW_GATE_PARAGRAPH = """\
**The independent-review gate.** This prompt advances only with reviewer
reports attached (rule:review-gate-needs-a-reviewer-report). Commit the
work, then fix the reviewed range as
`<the run's base_commit>..<HEAD at dispatch>`. The gate check does not
compare the base, so hold it. Each reviewer writes its own
report: `uv run "${CRUX_PLUGIN_ROOT}/scripts/write-review-report.py"
<run-RUN-NNN.yaml> --prompt <n> --range <base>..<end> --verdict
'<verdict>'`, adding one `--finding '<finding>'` per finding. Single-quote
each value and write an embedded `'` as `'\\''`; never put model-written
text inside double quotes. Text with quotes or several lines goes in a
file passed with `--verdict-file` or `--finding-file`. A reviewer whose
harness grants no shell returns those fields, and the agent that
commissioned the review runs the writer with them. From the range end to
the advance, write nothing to the run snapshot or the book: the range
covers both, so a later write to either refuses every report. Commit only
the reports before the advance: a commit after the range end that changes
a reviewed path refuses every report. Record notes, deferrals and gate
tokens after the advance. Attach every report with `--artifacts`. A
council record never satisfies this gate."""

FORBIDDEN_LINE_START = re.compile(r"^\s*(?:rule:|\[|\{|--)")


def load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def book_module(book: dict, tag: str) -> list[dict]:
    return [p for p in book["prompts"] if p.get("module_tag") == tag]


def adr_council_problems(prompt: str) -> list[str]:
    """What is wrong with an ADR-module council prompt."""
    problems = []
    if "run-council.py" not in prompt:
        problems.append("does not name run-council.py")
    for dim in DIMENSIONS:
        if dim not in prompt:
            problems.append(f"drops the dimension {dim}")
    if "srde" in prompt:
        problems.append("names srde")
    for hit in council_alternative.find_withdrawn_alternative(prompt):
        problems.append(f"carries a withdrawn alternative: {hit}")
    return problems


def adr_text_problems(prompts: list[dict]) -> list[str]:
    """`srde` or a deep-architecture Agent anywhere in an ADR module's fields."""
    problems = []
    for i, p in enumerate(prompts, 1):
        for key in ("purpose", "prompt", "expected_output"):
            text = p.get(key) or ""
            if "srde" in text:
                problems.append(f"ordinal {i} {key} names srde")
            if re.search(r"deep-architecture", text):
                problems.append(f"ordinal {i} {key} names a deep-architecture review")
    return problems


def review_gate_problems(prompt: str) -> list[str]:
    if "write-review-report.py" not in prompt:
        return ["does not name write-review-report.py"]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", prompt) if p.strip()]
    if paragraphs[-1] != REVIEW_GATE_PARAGRAPH:
        return ["the independent-review gate paragraph is not the last paragraph"]
    return []


def adr_subject_problems(prompt: str) -> list[str]:
    """What is wrong with how an ADR-module council prompt hands the ADR to the seats.

    The runner fences every `--subject` as data with its sha256, and a seat sees only the
    question and the subjects, so the prompt passes the ADR, the index and every cited path
    as subjects and does not fence the ADR in the question."""
    text = norm(prompt)
    problems = []
    if "fenced as data" in text or "question also fences" in text:
        problems.append("fences a subject in the question")
    if "Do not fence the ADR" not in text:
        problems.append("does not say the question leaves the ADR unfenced")
    if "A seat sees only the question and the subjects" not in text:
        problems.append("does not say what a seat sees")
    if "--subject <ADR file> --subject docs/adrs/index.md [--subject <cited path> ...]" not in text:
        problems.append("does not pass the index and the cited paths as subjects")
    return problems


def adr_close_problems(prompt: str) -> list[str]:
    """What is wrong with how an ADR module close handles a failed `transition-adr`."""
    text = norm(prompt)
    problems = []
    if "When `transition-adr` fails after that advance" not in text:
        problems.append("does not handle a transition-adr failure after the advance")
    if "contradicted premise (stop 4)" not in text:
        problems.append("does not stop at the contradicted-premise stop")
    if "Do not re-run the gate check" not in text:
        problems.append("does not forbid re-running the gate check")
    if ("Until the ADR is accepted, every later advance of this run refuses and "
            "writes nothing; `--abandon` stays open.") not in text:
        problems.append("does not say every later advance refuses while the ADR is Proposed")
    return problems


def run_autonomy_problems(text: str) -> list[str]:
    problems = []
    if ("(c) a council that cannot run through the council runner "
            "yields DEFER_TO_HUMAN") not in text:
        problems.append("lacks the stop for a council that cannot run")
    if "The only stops are (a)" not in text:
        problems.append("lacks the stop list")
    return problems


def line_start_problems(raw: str) -> list[str]:
    """Lines of a block scalar that open with a shape the templates forbid."""
    problems = []
    in_block = False
    block_indent = 0
    for number, line in enumerate(raw.splitlines(), 1):
        if not in_block:
            if re.search(r":\s*[|>][+-]?\s*$", line):
                in_block = True
                block_indent = len(line) - len(line.lstrip()) + 1
            continue
        if line.strip() == "":
            continue
        indent = len(line) - len(line.lstrip())
        if indent < block_indent:
            in_block = bool(re.search(r":\s*[|>][+-]?\s*$", line))
            if in_block:
                block_indent = indent + 1
            continue
        if FORBIDDEN_LINE_START.match(line):
            problems.append(f"line {number}: {line.strip()[:40]!r}")
    return problems


class CouncilPromptTests(unittest.TestCase):

    def test_module_council_prompt_runs_the_council_runner(self):
        council = load(ADR_MODULE)[1]
        self.assertEqual(adr_council_problems(council["prompt"]), [])
        self.assertEqual(council["side_effects"], ["run-adr-council"])
        self.assertEqual(
            norm(council["expected_output"]),
            "the council record's path attached; per-seat decisions and blocking "
            "findings listed from it")

    def test_book_council_prompt_runs_the_council_runner(self):
        council = book_module(load(BOOK), "adr-1")[1]
        self.assertEqual(adr_council_problems(council["prompt"]), [])

    def test_adr_modules_carry_no_srde_and_no_deep_architecture_review(self):
        self.assertEqual(adr_text_problems(load(ADR_MODULE)), [])
        self.assertEqual(adr_text_problems(book_module(load(BOOK), "adr-1")), [])

    def test_book_strategy_names_no_srde(self):
        self.assertNotIn("srde", load(BOOK)["strategy"])

    def test_module_close_prompt_confirms_the_deciding_record(self):
        for name, prompts in (("module", load(ADR_MODULE)),
                              ("book", book_module(load(BOOK), "adr-1"))):
            with self.subTest(copy=name):
                close = norm(prompts[3]["prompt"])
                self.assertIn("--gate-info", close)
                self.assertIn("deciding record", close)
                self.assertIn("refutation record", close)
                self.assertLess(close.index("--gate-info"), close.index("transition-adr` with"))

    def test_round_three_is_adjudication(self):
        for name, prompts in (("module", load(ADR_MODULE)),
                              ("book", book_module(load(BOOK), "adr-1"))):
            with self.subTest(copy=name):
                text = norm(prompts[2]["prompt"])
                self.assertIn("Round 3 is adjudication", text)
                self.assertIn("never the conductor", text)
                self.assertIn("owner-exception", text)

    def test_book_copy_equals_module_for_ordinals_two_to_four(self):
        module = load(ADR_MODULE)
        embedded = book_module(load(BOOK), "adr-1")
        self.assertEqual(len(module), len(embedded))
        for i in (1, 2, 3):
            for key in ("purpose", "prompt", "expected_output", "side_effects"):
                with self.subTest(ordinal=i + 1, field=key):
                    a, b = module[i][key], embedded[i][key]
                    if isinstance(a, str):
                        a = a.replace("{M}", "1")
                    self.assertEqual(a, b)

    def test_council_prompt_passes_every_file_a_seat_reads_as_a_subject(self):
        for name, prompts in (("module", load(ADR_MODULE)),
                              ("book", book_module(load(BOOK), "adr-1"))):
            with self.subTest(copy=name):
                self.assertEqual(adr_subject_problems(prompts[1]["prompt"]), [])

    def test_module_close_stops_when_transition_adr_fails_after_the_advance(self):
        for name, prompts in (("module", load(ADR_MODULE)),
                              ("book", book_module(load(BOOK), "adr-1"))):
            with self.subTest(copy=name):
                self.assertEqual(adr_close_problems(prompts[3]["prompt"]), [])

    # ---- positive controls ----

    def test_control_subject_check_reports_the_fenced_question_and_the_single_subject(self):
        good = load(ADR_MODULE)[1]["prompt"]
        fenced = good.replace("Do not\n       fence the ADR in it", "Fence\n       the ADR in it")
        fenced += "\nwrite the question file, with the ADR fenced as data.\n"
        self.assertTrue(adr_subject_problems(fenced))
        single = good.replace(" --subject docs/adrs/index.md [--subject <cited path> ...]", "")
        self.assertIn("does not pass the index and the cited paths as subjects",
                      adr_subject_problems(single))
        reads = good.replace("A seat sees only the question and the subjects.",
                             "Each seat reads the proposed ADR and the index.")
        self.assertIn("does not say what a seat sees", adr_subject_problems(reads))

    def test_review_prompt_records_security_deferrals_after_the_advance(self):
        # The review range covers the run snapshot, so a Notes write before the advance
        # refuses every reviewer report.
        for name, prompt in (("module", load(REVIEW_MODULE)[0]["prompt"]),
                             ("book", book_module(load(BOOK), "review-1")[0]["prompt"])):
            with self.subTest(copy=name):
                self.assertIn("with residual-risk rationale, written after this prompt's advance",
                              norm(prompt))

    def test_control_close_check_reports_a_missing_failure_stop(self):
        good = load(ADR_MODULE)[3]["prompt"]
        start = good.index("When `transition-adr`")
        end = good.index("status to retry.") + len("status to retry.")
        self.assertEqual(len(adr_close_problems(good[:start] + good[end:])), 3)

    def test_control_close_check_reports_a_missing_proposed_adr_refusal(self):
        good = load(ADR_MODULE)[3]["prompt"]
        self.assertEqual(adr_close_problems(good), [])
        cut = good.replace("every later advance", "the next advance")
        self.assertEqual(adr_close_problems(cut),
                         ["does not say every later advance refuses while the ADR is Proposed"])

    def test_control_council_check_reports_each_seeded_violation(self):
        good = load(ADR_MODULE)[1]["prompt"]
        self.assertEqual(adr_council_problems(good), [])
        for seeded, needle in (
            (good.replace("run-council.py", "the runner"), "run-council.py"),
            (good.replace("Security", "Safety"), "Security"),
            (good + "\nRun the srde skill.\n", "srde"),
            (good + "\nOr, equivalently, dispatch 5 parallel review agents.\n", "withdrawn"),
        ):
            with self.subTest(needle=needle):
                self.assertTrue(any(needle in p for p in adr_council_problems(seeded)))

    def test_control_adr_text_check_reports_srde_and_deep_architecture(self):
        prompts = load(ADR_MODULE)
        prompts[2]["prompt"] += "\nuse the council/srde outputs\n"
        prompts[1]["prompt"] += "\nthen one deep-architecture-review Agent\n"
        self.assertEqual(len(adr_text_problems(prompts)), 2)


class RoutingTests(unittest.TestCase):
    """The council, findings and close prompts tell the conductor what
    advance-run.py and the gate check accept."""

    def both(self):
        return (("module", load(ADR_MODULE)), ("book", book_module(load(BOOK), "adr-1")))

    def test_council_prompt_decides_could_not_run_from_exit_code_and_outcome(self):
        for name, prompts in self.both():
            with self.subTest(copy=name):
                text = norm(prompts[1]["prompt"])
                self.assertIn("Never decide whether the council ran from the aggregate action", text)
                self.assertIn("a council that ran also carries the action DEFER_TO_HUMAN", text)
                self.assertIn("Exit 1 with `outcome: could-not-run`", text)
                self.assertIn("`refusal_reason.code`", text)
                self.assertIn("Exit 1 with `outcome: ran`", text)
                self.assertIn("secret scan", text)
                self.assertIn("Exit 2: no committed council record exists", text)
                self.assertIn("Exit 0:", text)
                self.assertNotIn("A record whose action is DEFER_TO_HUMAN means", text)

    def test_council_prompt_advances_done_only_on_convergence(self):
        for name, prompts in self.both():
            with self.subTest(copy=name):
                text = norm(prompts[1]["prompt"])
                self.assertIn("--outcome blocked --artifacts <record path>", text)
                self.assertIn("refuses `--outcome done` and writes nothing", text)
                self.assertIn("rule:blocked-gate-needs-evidence", text)

    def test_findings_prompt_bounds_the_rounds(self):
        for name, prompts in self.both():
            with self.subTest(copy=name):
                text = norm(prompts[2]["prompt"])
                self.assertIn("Convene the next round only when a blocking finding was fixed "
                              "in the ADR and fewer than two councils have counted for this module", text)
                self.assertIn("When every blocking finding is refuted by the conductor's "
                              "refutation record, commit that record, attach it and advance", text)
                # The gate reads only a committed record, so the prompt says to commit it.
                self.assertIn("Commit each refutation or owner-exception record before the advance", text)
                self.assertIn("rule:council-gate-advances-on-convergence-or-refutation", text)
                self.assertIn("never convene a third council without an owner-exception record", text)
                self.assertIn("validated at the next gate advance", text)
                self.assertNotIn("validated at advance", text)
                self.assertNotIn("drafting prompt says", text)

    def test_close_prompt_names_the_three_deciding_forms_and_the_order(self):
        for name, prompts in self.both():
            with self.subTest(copy=name):
                text = norm(prompts[3]["prompt"])
                self.assertIn("the latest counted round that converged", text)
                self.assertIn("a conductor's refutation record showing every blocking finding "
                              "of the latest counted round, at place 2 or earlier, refuted", text)
                self.assertIn("the round-3 adjudicator's refutation record", text)
                self.assertIn("do not accept", text)
                self.assertIn("--outcome blocked --artifacts <record path>", text)
                self.assertIn("re-runs each recorded check before acceptance", text)
                # The gate's `done` advance precedes acceptance: a refused
                # advance writes nothing, so an ADR accepted first would stay
                # Accepted behind a gate that refused it.
                read = text.index("by reading it")
                done = text.index("--outcome done")
                accept = text.index("transition-adr` with")
                self.assertLess(read, done)
                self.assertLess(done, accept)
                self.assertIn("if that advance is refused, stop and report it", text)
                self.assertIn("The refused advance writes nothing, so the ADR stays Proposed", text)
                self.assertIn("do not hand-edit state", text)
                self.assertIn("Only after that advance writes this prompt `done`", text)
                self.assertNotIn("After acceptance, advance", text)

    def test_gates_reference_states_the_proposed_adr_refusal(self):
        text = norm(GATES.read_text(encoding="utf-8"))
        for needle in (
            "In a format-two run, every later advance refuses and writes nothing while a "
            "closed `adr-*` module's ADR is not Accepted, Deprecated or Superseded, or is unreadable or "
            "unidentified: `done`, `blocked` and "
            "`skipped` alike. `blocked` and `skipped` are refused because each moves the pointer at a "
            "prompt that is not a gate, and a run with no pending prompt left reaches `completed` with "
            "the ADR still Proposed.",
            "A module names an ADR through an artifact or a run-work witness entry whose path, resolved "
            "as the council gate resolves an artifact, is an ADR file under the tree's `adrs/` or "
            "`adrs/archive/`. An ADR identifier anywhere else names nothing.",
            "Only one entry can carry `transition-adr`: a Proposed ADR that the module names and that "
            "the deciding record carries as a subject, when it is the only ADR the module names that the "
            "deciding record carries. Dispatch the accepting architect to run it once. The gate cannot "
            "see whether `transition-adr` already ran. When it already ran for the module and failed, "
            "take a contradicted-premise stop for the owner, whatever the `remedy` field says. Every "
            "other entry carries `owner`: an ADR the module names that no deciding record carries, every "
            "ADR when the deciding record carries more than one ADR the module names, an entry "
            "identified only from the council subjects, an `unidentified` or `unreadable` entry, and an "
            "entry with any other status. Never instruct acceptance for an `owner` entry. Take a "
            "contradicted-premise stop so the owner decides, or abandon the run.",
            "`--abandon` stays open. Record the stop in the run Notes; it needs no advance. "
            "It clears when the ADR's `status:` is Accepted, Deprecated or Superseded. An unreadable ADR "
            "clears when the file at its path (the subject path, or the named path for an ADR no subject "
            "carries), or the file of the same name under `adrs/archive/` "
            "when no file is at that path, is a regular file reached without a symlink whose frontmatter "
            "carries one of those statuses. A module that names no ADR file and whose records name no "
            "ADR file clears only through `--abandon`.",
            "plus one `unidentified` entry, with a null `path`, for a module that names no ADR file and "
            "whose records name no ADR file.",
            "The result always carries `adr_acceptance_pending`: null in a format-one run, otherwise a "
            "list with one entry per pending ADR of a closed `adr-*` module",
            "Each entry carries a `remedy`, `transition-adr` or `owner`. Do not issue the prompt while "
            "the list is non-empty.",
            "**ADR acceptance.** The advance reads the `status:` of each ADR the module's own "
            "artifacts or run-work witnesses name by an ADR file path. It takes the path from each "
            "council or refutation subject that carries the identifier, and from the named path "
            "otherwise. A named ADR that cannot be read makes the advance refuse, with the status "
            "`unreadable`. An ADR identifier outside an ADR file path names nothing, so a module that "
            "cites its ADR only by identifier is judged by its subjects.",
            "A module that names no ADR is judged by every ADR subject its records carry, so a "
            "Proposed ADR cited only as context makes the advance refuse. It reads the working-tree file, so an "
            "uncommitted status edit clears the refusal.",
            "The gate-information query reports the pending ADR before that work starts.",
        ):
            with self.subTest(needle=needle[:40]):
                self.assertEqual(text.count(needle), 1)

    def test_gates_reference_retires_the_unseeable_remedy_claim(self):
        # The gate cannot see a failed transition-adr, so no text may say the field reads `owner`.
        text = norm(GATES.read_text(encoding="utf-8"))
        self.assertEqual(text.count("and an entry whose `transition-adr` already failed"), 0)

    def test_pending_pointer_names_the_module_close_paragraph(self):
        skill = GATES.parent.parent / "SKILL.md"
        advance = GATES.parent / "advance.md"
        for path, ref in ((skill, "`references/gates.md`"), (advance, "`gates.md`")):
            with self.subTest(path=path.name):
                text = norm(path.read_text(encoding="utf-8"))
                self.assertEqual(text.count(
                    "When `adr_acceptance_pending` is non-empty, do not issue the prompt; the \"Module "
                    f"close on an `adr-*` module\" paragraph in {ref} section 4 names the remedy for each "
                    "entry."), 1)
        self.assertEqual(norm(GATES.read_text(encoding="utf-8")).count(
            "**Module close on an `adr-*` module.**"), 1)

    def test_book_titles_and_wording(self):
        book = book_module(load(BOOK), "adr-1")
        self.assertNotIn("return to Prompt", book[2]["title"])
        self.assertTrue(book[2]["title"].startswith("Plan: address council findings"))
        for prompts in (load(ADR_MODULE), book):
            self.assertIn("a council can take about 20 minutes", norm(prompts[1]["prompt"]))
            self.assertNotIn("takes about 20", prompts[1]["prompt"])

    def test_header_comments_say_a_template_change_fixes_no_existing_book(self):
        for path in (BOOK, REVIEW_MODULE):
            with self.subTest(template=path.name):
                lines = []
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.startswith("#"):
                        break
                    lines.append(line.lstrip("# ").strip())
                header = norm(" ".join(lines))
                self.assertIn("A template change fixes no existing book; the gate check "
                              "corrects a book that already exists when it executes "
                              "(rule:existing-books-are-corrected-at-execution)", header)


class ReviewGateTests(unittest.TestCase):

    def test_module_review_prompt_carries_the_gate_paragraph_last(self):
        self.assertEqual(review_gate_problems(load(REVIEW_MODULE)[0]["prompt"]), [])

    def test_book_review_prompt_carries_the_gate_paragraph_last(self):
        review = book_module(load(BOOK), "review-1")[0]
        self.assertEqual(review_gate_problems(review["prompt"]), [])

    def test_other_review_prompts_carry_no_gate_paragraph(self):
        for prompts in (load(REVIEW_MODULE), book_module(load(BOOK), "review-1")):
            for i in (1, 2):
                self.assertNotIn("independent-review gate", prompts[i]["prompt"])

    def test_control_the_other_prompt_check_sees_a_seeded_paragraph(self):
        seeded = load(REVIEW_MODULE)[1]["prompt"] + "\n\n" + REVIEW_GATE_PARAGRAPH
        self.assertIn("independent-review gate", seeded)
        self.assertIn("independent-review gate", load(REVIEW_MODULE)[0]["prompt"])

    def test_control_gate_check_reports_a_missing_or_misplaced_paragraph(self):
        good = load(REVIEW_MODULE)[0]["prompt"]
        self.assertEqual(review_gate_problems(good), [])
        self.assertTrue(review_gate_problems(good.replace("write-review-report.py", "the writer")))
        self.assertTrue(review_gate_problems(good + "\nOne more paragraph.\n"))
        self.assertTrue(review_gate_problems(good.replace(REVIEW_GATE_PARAGRAPH, "")))


class RunAutonomyTests(unittest.TestCase):

    def test_run_autonomy_stops_when_the_council_cannot_run(self):
        self.assertEqual(run_autonomy_problems(norm(load(BOOK)["run_autonomy"])), [])

    def test_run_autonomy_names_who_resolves_decisions(self):
        text = norm(load(BOOK)["run_autonomy"])
        self.assertIn("resolved by the council runner for a council gate, and by "
                      "dispatched `Agent`s otherwise", text)
        self.assertNotIn("resolved by the `council` skill", text)

    def test_control_the_check_reads_the_stop_text(self):
        text = norm(load(BOOK)["run_autonomy"])
        self.assertEqual(run_autonomy_problems(text), [])
        seeded = text.replace(
            "a council that cannot run through the council runner yields DEFER_TO_HUMAN",
            "a council that cannot run is replaced by native agents")
        self.assertNotEqual(seeded, text)
        self.assertTrue(run_autonomy_problems(seeded))
        dropped = text.replace("The only stops are (a)", "The stops are (a)")
        self.assertNotEqual(dropped, text)
        self.assertTrue(run_autonomy_problems(dropped))


class RoundAndCommitRuleTests(unittest.TestCase):
    """Every council prompt of every cycle template states the round rule and the commit rule.

    `--round` is the number of council records with outcome `ran` already in the module
    (in a patch book, the prompt), plus one. `run-council.py` refuses a mismatch before any
    call. The commit rule: the council runner commits the attempt record and the council
    record itself, so no prompt tells the conductor to commit a council record. Prose
    states both rules for books authored after the template; it enforces neither, and no
    template corrects a book that already exists.
    """

    TEMPLATE_NAMES = (
        "cycle-module-adr.yaml",
        "cycle-module-verify.yaml",
        "cycle-promptbook-template.yaml",
        "iterate-promptbook-template.yaml",
        "patch-promptbook-template.yaml",
    )
    STALE = re.compile(r"next round number|module's next round")
    PINNED = "The council runner commits the attempt record and the council record itself."
    #: An instruction to the conductor to commit a council record. In a council prompt a
    #: bare "Commit the record" names the council record too. "commits" and "committed"
    #: do not match, so the pinned sentence and "has committed the record" pass. The
    #: plural and the attempt record match too: the council runner commits both.
    CONDUCTOR_COMMIT = re.compile(
        r"\b[Cc]ommit (?:the|each|every|this|both) (?:new )?(?:council |attempt )?records?\b"
        r"|\b[Cc]ommit it, then attach")
    #: The same instruction outside a council prompt, where "commit that record" or
    #: "commit both records" may name a refutation or owner-exception record: only a
    #: phrase naming a council record or an attempt record counts.
    CONDUCTOR_COMMIT_COUNCIL = re.compile(
        r"\b[Cc]ommit (?:the|each|every|this|both) (?:new )?(?:council|attempt) records?\b")

    @staticmethod
    def prompts_of(doc) -> list[str]:
        if isinstance(doc, dict):
            doc = doc.get("prompts")
        return [str(p.get("prompt") or "") for p in (doc or []) if isinstance(p, dict)]

    @classmethod
    def council_prompts(cls, name: str) -> list[str]:
        return [t for t in cls.prompts_of(load(TEMPLATES / name)) if "run-council.py" in t and "--round" in t]

    @staticmethod
    def rule_problems(text: str) -> list[str]:
        flat = norm(text)
        problems = []
        if "the number of council records with outcome `ran` already" not in flat:
            problems.append("does not define --round as the counted ran records plus one")
        if "could-not-run record takes no place" not in flat:
            problems.append("does not say a could-not-run record takes no place")
        if '`"record": null`' not in flat:
            problems.append("does not state the refusal shape")
        if RoundAndCommitRuleTests.PINNED not in flat:
            problems.append("does not say the council runner commits the attempt and council records")
        if RoundAndCommitRuleTests.CONDUCTOR_COMMIT.search(flat):
            problems.append("tells the conductor to commit the council record")
        return problems

    def test_each_template_has_a_council_prompt_and_it_states_both_rules(self):
        for name in self.TEMPLATE_NAMES:
            prompts = self.council_prompts(name)
            with self.subTest(template=name):
                self.assertTrue(prompts, "no council prompt found, so nothing was checked")
                for text in prompts:
                    self.assertEqual(self.rule_problems(text), [])

    def test_no_prompt_still_says_the_next_round_number(self):
        for name in self.TEMPLATE_NAMES:
            for text in self.prompts_of(load(TEMPLATES / name)):
                with self.subTest(template=name):
                    self.assertIsNone(self.STALE.search(norm(text)))

    def test_no_prompt_tells_the_conductor_to_commit_a_council_record(self):
        for name in self.TEMPLATE_NAMES:
            prompts = self.prompts_of(load(TEMPLATES / name))
            with self.subTest(template=name):
                self.assertTrue(prompts, "no prompt found, so nothing was checked")
                hits = [m.group(0) for t in prompts
                        for m in self.CONDUCTOR_COMMIT_COUNCIL.finditer(norm(t))]
                self.assertEqual(hits, [])

    def test_control_the_check_reports_each_missing_rule(self):
        good = self.council_prompts("cycle-module-adr.yaml")[0]
        self.assertEqual(self.rule_problems(good), [])
        for needle in ("the number of council records with outcome `ran` already",
                       "could-not-run record takes no place",
                       '`"record": null`',
                       self.PINNED):
            with self.subTest(needle=needle):
                broken = norm(good).replace(needle, "x")
                self.assertNotEqual(broken, norm(good))
                self.assertEqual(len(self.rule_problems(broken)), 1)
        for stale in ("Commit the council record before you advance.",
                      "Commit each council record before you advance.",
                      "Commit the record, then attach it.",
                      "Commit it, then attach it.",
                      "Commit the council records before you advance.",
                      "Commit both records, then attach the council record.",
                      "Commit this record before you advance.",
                      "Commit the attempt record before the council runs.",
                      "Commit every attempt record before you advance."):
            with self.subTest(stale=stale):
                seeded = norm(good) + " " + stale
                self.assertEqual(self.rule_problems(seeded),
                                 ["tells the conductor to commit the council record"])
        for fine in ("The council runner has committed the record.",
                     "The council runner commits the record.",
                     "Commit the refutation record before the advance."):
            with self.subTest(fine=fine):
                self.assertEqual(self.rule_problems(norm(good) + " " + fine), [])
        self.assertIsNotNone(self.CONDUCTOR_COMMIT_COUNCIL.search(
            "then commit the new council record, and attach it"))
        for stale in ("then commit both council records, and attach one",
                      "commit the attempt record before the council runs",
                      "commit this council record before you advance"):
            with self.subTest(stale_outside=stale):
                self.assertIsNotNone(self.CONDUCTOR_COMMIT_COUNCIL.search(stale))
        for fine in ("commit that record, attach it",
                     "Commit the refutation record before the advance.",
                     "Commit both records before the advance."):
            with self.subTest(fine_outside=fine):
                self.assertIsNone(self.CONDUCTOR_COMMIT_COUNCIL.search(fine))
        stale = "Round `<r>` is this module's next round number, starting at 1."
        self.assertIsNotNone(self.STALE.search(stale))

    def test_the_council_family_skills_state_the_round_rule_and_the_commit_rule(self):
        skills = TEMPLATES.parent / "skills"
        for name in ("council", "run-adr-council", "dev-cycle", "iterate"):
            text = norm((skills / name / "SKILL.md").read_text(encoding="utf-8"))
            with self.subTest(skill=name):
                self.assertIn("the number of council records with outcome `ran` already", text)
                self.assertIn("could-not-run record takes no place", text)
                self.assertIn(self.PINNED, text)
                self.assertIsNone(self.CONDUCTOR_COMMIT_COUNCIL.search(text))
                self.assertIsNone(self.STALE.search(text))

    def test_control_the_adr_book_and_module_copies_agree(self):
        self.assertEqual([norm(t) for t in self.council_prompts("cycle-module-adr.yaml")],
                         [norm(t) for t in self.council_prompts("cycle-promptbook-template.yaml")])


class RecoveryRouteProseTests(unittest.TestCase):
    """Every conductor surface routes a council runner that ended without a summary to recovery.

    A runner killed after its result commit leaves a resolved attempt, so nothing holds the
    scope. A conductor that then counts the committed `ran` record and convenes round R+1
    repeats a deliberation. Each surface says to recover first, and every recovery or probe
    command carries `--prompt <n>`: without it recovery cannot recognise a record whose
    pending copy is already removed. Prose states both rules; it enforces neither.
    """

    ROOT = TEMPLATES.parent
    SKILL_FILES = (
        "skills/run-promptbook/references/gates.md",
        "skills/council/SKILL.md",
        "skills/run-adr-council/SKILL.md",
        "skills/dev-cycle/SKILL.md",
        "skills/iterate/SKILL.md",
        "skills/patch-cycle/SKILL.md",
        "agents/commander.md",
        "agents/dev-lead.md",
        "agents/reviewer.md",
    )
    TEMPLATE_FILES = RoundAndCommitRuleTests.TEMPLATE_NAMES
    #: A runner that ended before it reported: no exit code, a signal, or a code outside 0, 1, 2.
    ENDED = re.compile(r"ended without an exit code|ends without an exit code|no exit code, a signal")
    #: The rule that no round convenes until recovery has reported.
    NEVER_BEFORE = re.compile(r"(?:until|before) recovery reports")

    def texts(self) -> dict[str, str]:
        out = {name: norm((self.ROOT / name).read_text(encoding="utf-8")) for name in self.SKILL_FILES}
        for name in self.TEMPLATE_FILES:
            out["templates/" + name] = norm((TEMPLATES / name).read_text(encoding="utf-8"))
        return out

    @staticmethod
    def bare_recover_commands(text: str) -> list[str]:
        """Each `--recover` whose command, up to the next backtick or 90 characters, lacks `--prompt`."""
        found = []
        for m in re.finditer(r"--recover\b", text):
            tail = text[m.start():m.start() + 90]
            tail = tail.split("`")[0] if "`" in tail else tail
            if "--prompt" not in tail:
                found.append(text[m.start():m.start() + 60])
        return found

    def test_every_surface_routes_a_runner_that_ended_without_a_summary_to_recovery(self):
        for name, text in self.texts().items():
            if name in ("agents/reviewer.md",):
                continue  # one sentence: it hands the commissioning agent the same rule
            with self.subTest(surface=name):
                self.assertIsNotNone(self.ENDED.search(text), "no sentence for a runner that ended")
                self.assertIsNotNone(self.NEVER_BEFORE.search(text), "no rule against convening before recovery reports")

    def test_the_reviewer_hands_a_runner_that_ended_to_recovery(self):
        text = self.texts()["agents/reviewer.md"]
        self.assertIsNotNone(self.ENDED.search(text))
        self.assertIsNotNone(self.NEVER_BEFORE.search(text))

    def test_every_recovery_command_names_the_prompt_and_each_surface_shows_one(self):
        for name, text in self.texts().items():
            with self.subTest(surface=name):
                self.assertEqual(self.bare_recover_commands(text), [])
                if name != "agents/reviewer.md":
                    self.assertIn("--recover", text, "no recovery command, so nothing was checked")

    def test_the_gates_reference_states_why_the_prompt_is_passed(self):
        text = self.texts()["skills/run-promptbook/references/gates.md"]
        self.assertIn("Without it, recovery cannot recognise a record whose pending copy is already removed, "
                      "and it reports `nothing-open`", text)

    def test_control_the_check_reports_a_bare_recovery_command_and_a_missing_sentence(self):
        good = self.texts()["skills/council/SKILL.md"]
        self.assertEqual(self.bare_recover_commands(good), [])
        seeded = good + " Then run `run-council.py --recover <run>` and wait."
        self.assertEqual(len(self.bare_recover_commands(seeded)), 1)
        self.assertIsNone(self.ENDED.search("An exit 2 that names a claimed attempt."))
        self.assertIsNone(self.NEVER_BEFORE.search("Never convene another round over a claimed attempt."))
        self.assertIsNotNone(self.ENDED.search(good))
        self.assertIsNotNone(self.NEVER_BEFORE.search(good))


class WitnessRefusalAndLandedRouteTests(unittest.TestCase):
    """Each conductor surface gives one next step, with one actor, for a witness refusal and a landed commit.

    A witness `commit` refusal with `"refused": "commit"` is a git refusal. The path stays a
    retryable `commit-run-work` subject, so a council runner run would spend a retry rather than
    record `preflight-needs-owner`: the owner stop comes from the conductor. A
    `commit-refused` recovery report with `"commit_landed": true` names its branch (a timeout's
    remedy order first) before the sentence that advances an open attempt. Prose states both
    rules; it enforces neither.
    """

    ROOT = TEMPLATES.parent
    GATES = "skills/run-promptbook/references/gates.md"
    SURFACES = (GATES, "agents/commander.md") + tuple("templates/" + n for n in RoundAndCommitRuleTests.TEMPLATE_NAMES)
    ADVANCE_OPEN = re.compile(r"advances? (?:the prompt )?`--outcome blocked` with the attempt record attached")
    GIT_REFUSAL = '"refused": "commit"'
    LANDED = '"commit_landed": true'

    def texts(self) -> dict[str, str]:
        out = {}
        for name in self.SURFACES + ("agents/dev-lead.md", "agents/reviewer.md", "skills/council/SKILL.md"):
            path = TEMPLATES / name[len("templates/"):] if name.startswith("templates/") else self.ROOT / name
            out[name] = norm(path.read_text(encoding="utf-8"))
        return out

    @classmethod
    def git_refusal_problems(cls, text: str) -> list[str]:
        """Problems with a surface's next step for a witness `commit` git refusal."""
        i = text.find(cls.GIT_REFUSAL)
        if i < 0:
            return ["no sentence names a witness commit git refusal"]
        j = text.find("contradicted-premise", i, i + 600)
        if j < 0:
            return ["a git refusal names no contradicted-premise stop"]
        # Only the text between the refusal and its stop can lead the conductor astray.
        return ["a git refusal leads to preflight-needs-owner"] if "preflight-needs-owner" in text[i:j] else []

    @classmethod
    def landed_problems(cls, text: str, advance: "re.Pattern[str] | None" = None) -> list[str]:
        """Problems with the placement and content of the `commit_landed` branch."""
        i = text.find(cls.LANDED)
        if i < 0:
            return ["no commit_landed branch"]
        out = []
        m = (advance or cls.ADVANCE_OPEN).search(text)
        if m and m.start() < i:
            out.append("the open-attempt sentence precedes the commit_landed branch")
        window = text[i:i + 450]
        if "timeout" not in window:
            out.append("the commit_landed branch has no timeout remedy order")
        return out

    def test_a_witness_commit_git_refusal_is_an_owner_stop_not_a_council_runner_rerun(self):
        for name in self.SURFACES:
            with self.subTest(surface=name):
                self.assertEqual(self.git_refusal_problems(self.texts()[name]), [])

    def test_no_surface_says_a_refused_witness_commit_leads_to_needs_owner(self):
        stale = re.compile(r"(?:witness\s+writer refuses the commit|`run-work-witness.py commit` refuses)[^`]{0,160}"
                           r"(?:do not retry|run the council runner again)[^`]{0,120}preflight-needs-owner")
        for name in self.SURFACES:
            with self.subTest(surface=name):
                self.assertIsNone(stale.search(self.texts()[name]))

    @classmethod
    def index_locked_problems(cls, text: str) -> list[str]:
        """Problems with the witness commit's `index-locked` route: one rerun, then a stale-lock stop."""
        i = text.find(cls.GIT_REFUSAL)
        if i < 0:
            return ["no sentence names a witness commit git refusal"]
        window = text[i:i + 1100]
        j = window.find("index-locked")
        if j < 0:
            return ["a git refusal names no index-locked route"]
        stop = window.find("contradicted-premise", j)
        again = window.find("once more", j)
        out = []
        if again < 0 or (stop >= 0 and again > stop):
            out.append("index-locked has no witness commit rerun before the owner stop")
        if "persists" not in window[j:stop if stop >= 0 else len(window)]:
            out.append("index-locked has no stale-lock stop after the rerun")
        return out

    def test_a_witness_commit_index_locked_refusal_gets_one_rerun_before_the_owner_stop(self):
        for name in self.SURFACES:
            with self.subTest(surface=name):
                self.assertEqual(self.index_locked_problems(self.texts()[name]), [])

    def test_the_witness_commit_refusal_route_names_mismatch_and_invalid_and_missing(self):
        for name in self.SURFACES:
            with self.subTest(surface=name):
                text = self.texts()[name]
                window = text[text.find(self.GIT_REFUSAL):][:2600]
                for code in ("mismatch", "invalid", "missing"):
                    self.assertIn(f"`{code}`", window)

    def test_the_landed_timeout_wait_names_the_conductor_as_the_actor(self):
        for name in self.SURFACES:
            with self.subTest(surface=name):
                text = self.texts()[name]
                i = text.find(self.LANDED)
                self.assertGreaterEqual(i, 0)
                window = text[i:i + 900]
                self.assertIn("contradicted-premise", window)
                # The actor of the rerun is named: the conductor (gates.md), the shell agent the
                # commander dispatches (commander.md), or the template's reader ("run recovery").
                actor = {self.GATES: r"the conductor runs recovery once more",
                         "agents/commander.md": r"dispatch the agent holding a shell to run recovery once more"
                         }.get(name, r", run recovery once more(?! at once)")
                self.assertRegex(" ".join(window.split()), actor)

    def test_the_templates_have_no_orphan_word_lines_and_no_double_space(self):
        for name in RoundAndCommitRuleTests.TEMPLATE_NAMES:
            with self.subTest(template=name):
                raw = (TEMPLATES / name).read_text(encoding="utf-8")
                self.assertEqual([l for l in raw.splitlines() if l.strip() in ("to", "this", "for")], [])
                self.assertNotIn("owner.  When", raw)

    def test_the_gates_reference_names_the_marker_and_the_whole_env_rule(self):
        text = self.texts()[self.GATES]
        self.assertIn("`<outside state not compared>`", text[text.find("Report fields"):][:900])
        self.assertIn("neither changed between `base_commit` and HEAD nor named in any prompt's artifacts", text)
        self.assertIn("`.envrc` file, or the crux env file", text)
        self.assertIn("a landed branch that recovery still leaves open", self.texts()["agents/commander.md"])

    def test_control_index_locked_check_reports_a_direct_owner_stop(self):
        old = '`"refused": "commit"`: git refused. Its `code` is `index-locked` or `timeout`: report a contradicted-premise stop for the owner.'
        self.assertEqual(self.index_locked_problems(old), ["index-locked has no witness commit rerun before the owner stop",
                                                           "index-locked has no stale-lock stop after the rerun"])
        good = ('`"refused": "commit"`: for `index-locked`, run the witness commit once more. '
                'When the lock persists, report a contradicted-premise stop.')
        self.assertEqual(self.index_locked_problems(good), [])

    def test_the_commit_landed_branch_precedes_the_open_attempt_sentence_and_names_the_timeout_order(self):
        for name in self.SURFACES:
            with self.subTest(surface=name):
                self.assertEqual(self.landed_problems(self.texts()[name]), [])

    def test_the_recovery_surfaces_name_the_paths_a_refused_commit_moved_or_staged(self):
        for name in (self.GATES, "agents/commander.md"):
            with self.subTest(surface=name):
                text = self.texts()[name]
                self.assertIn("outside_moved", text)
                self.assertIn("owned_staged", text)

    def test_nothing_open_after_a_runner_without_an_exit_code_reruns_it(self):
        for name in self.SURFACES:
            if name == "agents/commander.md":
                continue  # the commander row folds into the released sentence
            with self.subTest(surface=name):
                text = self.texts()[name]
                spans = [text[m.start():m.start() + 500] for m in re.finditer(r"`nothing-open`", text)]
                self.assertTrue(spans)
                self.assertTrue(any("nothing was claimed" in span for span in spans))

    def test_every_surface_that_names_a_runner_without_an_exit_code_adds_the_other_codes(self):
        ended = RecoveryRouteProseTests.ENDED
        for name in ("agents/commander.md", "agents/dev-lead.md", "agents/reviewer.md"):
            with self.subTest(surface=name):
                m = ended.search(self.texts()[name])
                self.assertIsNotNone(m)
                self.assertIn("code other than 0, 1 and 2", self.texts()[name][m.start():m.start() + 120])

    def test_the_commander_dispatches_the_shell_agent_to_rerun_the_council_runner(self):
        text = self.texts()["agents/commander.md"]
        for m in re.finditer(r"run the council runner again", text):
            self.assertEqual(text[max(0, m.start() - 3):m.start()], "to ", text[max(0, m.start() - 80):m.end()])
        self.assertIn("dispatch the agent holding a shell to run the council runner again", text)

    def test_the_gates_reference_words_its_codes_by_what_the_code_checks(self):
        text = self.texts()[self.GATES]
        self.assertNotIn("a subject is the crux env file", text)
        self.assertIn(".envrc", text)
        self.assertNotIn("is not a file the run wrote", text)
        self.assertNotIn("recovery releases it", text)
        self.assertNotIn("the council runner then records `preflight-needs-owner`", text)

    def test_the_ad_hoc_council_skill_names_no_timeout_flag_in_its_driver_section(self):
        self.assertNotIn("(`--timeout`)", self.texts()["skills/council/SKILL.md"])

    def test_control_the_checks_report_the_old_wording(self):
        old = ("When `run-work-witness.py commit` refuses, do not retry the commit. "
               "Run the council runner again, and it records `preflight-needs-owner` for the owner.")
        self.assertEqual(self.git_refusal_problems(old), ["no sentence names a witness commit git refusal"])
        seeded = '`"refused": "commit"` leads the council runner to record `preflight-needs-owner`, a contradicted-premise stop.'
        self.assertEqual(self.git_refusal_problems(seeded), ["a git refusal leads to preflight-needs-owner"])
        self.assertEqual(self.git_refusal_problems('`"refused": "commit"` is a refusal by the owner.'),
                         ["a git refusal names no contradicted-premise stop"])
        late = ('advance `--outcome blocked` with the attempt record attached. After `commit-refused` with '
                '`"commit_landed": true`, run recovery once more.')
        self.assertEqual(self.landed_problems(late),
                         ["the open-attempt sentence precedes the commit_landed branch",
                          "the commit_landed branch has no timeout remedy order"])
        good = ('After `commit-refused` with `"commit_landed": true` and code `timeout`, follow the remedy order. '
                'Advance `--outcome blocked` with the attempt record attached.')
        self.assertEqual(self.landed_problems(good), [])


class LineStartTests(unittest.TestCase):

    def test_no_block_scalar_line_opens_with_a_forbidden_shape(self):
        for path in (ADR_MODULE, REVIEW_MODULE, BOOK):
            with self.subTest(template=path.name):
                self.assertEqual(line_start_problems(path.read_text(encoding="utf-8")), [])

    def test_control_the_scan_sees_each_forbidden_shape(self):
        for bad in ("rule:some-slug", "[link]", "{token}", "--flag"):
            raw = f"- prompt: |\n    ok line\n    {bad} opens a line\n"
            with self.subTest(bad=bad):
                self.assertEqual(len(line_start_problems(raw)), 1)
        self.assertEqual(line_start_problems("- prompt: |\n    fine (rule:a-slug)\n"), [])


if __name__ == "__main__":
    unittest.main()
