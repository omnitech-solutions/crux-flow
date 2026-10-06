"""The two channels, the six reasons, and the honest stub line (ADR-0096 §§2-4).

Clause 2 splits one signal into two channels. The RECORDED channel is
`_meta/coverage.json` plus the spine stub line, both inside the drift gate's
byte-compared set, carrying exactly one verdict per concern. The REPORTED
channel is stdout and the printed table, written nowhere under `arch/`,
carrying that verdict plus zero or more annotations.

Clause 3 closes the stub reason set at six and puts a floor under `populated`:
at least one entity of the concern's own kind. Clause 4 makes the stub line say
what was expected and what was found, and reserves one sentence.

Every class here is one of the named postconditions those clauses state, and
each is written to fail on the specific defect the baseline measured rather
than on a paraphrase of it.

Stdlib only, and cache-free: nothing here needs the corpus clones. The golden
tree is committed, so the corpus assertions read it directly.
"""

from __future__ import annotations

import ast
import inspect
import itertools
import json
import re
import shutil
import sys
import tempfile
import types
import unittest
import unittest.mock
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]          # crux/scripts
REPO = SCRIPTS.parents[1]
GOLDEN = Path(__file__).resolve().parent / "arch-corpus" / "golden"
LIVE_ARCH = REPO / "bionic" / "arch"

sys.path.insert(0, str(SCRIPTS))
# The tests dir too: `_dev_surface` is a sibling helper, not a `crux.scripts`
# module, and relying on pytest's rootdir insertion made this file importable
# under a full-directory run and not on its own.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _dev_surface                                     # noqa: E402
from _dev_surface import require_dev_surface            # noqa: E402
from crux.arch import core                              # noqa: E402

SPINE = ("data-model", "api-surface", "module-graph", "decision-index")

#: Every pack the deriver can resolve, registered ones plus the empty one.
PACKS = tuple(sorted(core.REGISTERED_PACKS) + [core.STUB_PACK_NAME])

#: The FULL product of pack x concern x stub reason, enumerated rather than
#: sampled. Clause 9 says every stub says what would establish it, and a sample
#: proves that of the combinations it happened to draw. The combinations most
#: likely to raise are the ones no repository produces today — `parse_failed`
#: under the stub pack, `ambiguous_package` under a pack with no package
#: notion — which is exactly what a sample would miss.
PRODUCT = tuple(itertools.product(PACKS, core.CONCERNS, core.StubReason))


def _stub_record(pack: str, concern: str, reason) -> tuple[dict, object]:
    """One synthetic coverage record for a product member, plus its declaration.

    Built through `Verdict.stubbed` and the pack's real `InputClass` rather than
    from a hand-written dict, so the record has the shape the deriver emits and
    the expectation text the pack actually declares.
    """
    ic = core.input_classes(pack)[concern]
    verdict = core.Verdict.stubbed(reason, expected=ic.expected, found="2 candidates")
    rec = {"concern": concern, "extractor": pack, "inputs_found": [],
           **verdict.as_record()}
    return rec, ic


class RemediationLineTests(unittest.TestCase):
    """Clause 9: every stub carries one remediation line, and it is REPORTED.

    Three postconditions, each stated over the full product above:

      * one line per non-populated concern and none for a populated one;
      * `InputClass.refresh` is the single source of any command text, so the
        clause 9 line and the clause 7 staleness advisory cannot disagree about
        a pack's refresh command;
      * no line names clause 11's runtime-escalation seam.

    The fourth postcondition — that no line reaches the byte-compared tree —
    lives in `TwoChannelTests` beside the annotation prohibition it extends.
    """

    #: Clause 11's seam, spelled every way a line could name it. "runtime" is
    #: included bare and on purpose: the seam's whole vocabulary is out of scope
    #: for this channel, so a pack that later declared a refresh command naming
    #: it should fail here rather than quietly ship the sentence the ADR forbids.
    #:
    #: The second group is the SHAPE rather than the vocabulary, and it is here
    #: because the first group missed one. The python pack's `api-surface`
    #: declared
    #:
    #:     python -c "import json, sys; from <module> import app;
    #:                json.dump(app.openapi(), sys.stdout)" > openapi.json
    #:
    #: which tells a reader to import and execute their own application — the
    #: thing every token above exists to forbid — while naming none of them. A
    #: prohibition stated only as a word list is evaded by any sentence that
    #: does the deed without saying the word, so the shape is named too: a
    #: crux-composed inline interpreter invocation, and the app-object call that
    #: was its payload. `ruby -e` / `node -e` are the same shape in the other two
    #: packs that declare an artifact, listed before one is written rather than
    #: after. `bin/rails db:schema:dump` is untouched by all of them, which is
    #: the line: a first-party CLI a developer runs in their own shell is not
    #: this, and must keep passing.
    #:
    #: `.openapi()` carries its dot and parentheses deliberately. A bare
    #: "openapi" would match the python pack's own `expected` text — "a
    #: committed OpenAPI document" — and fail a line that is doing its job.
    FORBIDDEN = (
        "escalate-arch-runtime",
        "crux_arch_allow_runtime",
        "crux/scripts/crux/arch/runtime/",
        "runtime",
        "run your application",
        "run the app",
        "start the server",
        "boot the",
        # The shape, not the vocabulary.
        "python -c",
        "python3 -c",
        "ruby -e",
        "node -e",
        "import app",
        ".openapi()",
    )

    #: The only authored backtick tokens a line may carry that did not come from
    #: the record or from `InputClass.refresh`. `arch_stack` is a config KEY and
    #: `.bionic.yml` the file it lives in; pinning a stack is an edit a human
    #: makes, not a command anything runs, which is why naming them is permitted
    #: while phrasing them as a command would not be.
    CONFIG_TOKENS = frozenset({"arch_stack", ".bionic.yml"})

    _BACKTICKED = re.compile(r"`([^`]+)`")

    def test_a_populated_concern_gets_no_line(self):
        """Nothing to remedy, so nothing is said. The other half of (a)."""
        for pack, concern in itertools.product(PACKS, core.CONCERNS):
            ic = core.input_classes(pack)[concern]
            rec = {"concern": concern, "extractor": pack, "inputs_found": ["a"],
                   **core.Verdict.populated(2, 5).as_record()}
            with self.subTest(pack=pack, concern=concern):
                self.assertIsNone(core.remediation(rec, ic))
                self.assertIsNone(core.remediation(rec, None))

    def test_every_product_member_yields_exactly_one_line(self):
        """(a) and (b) over all of it: a line, non-empty, and only one."""
        for pack, concern, reason in PRODUCT:
            rec, ic = _stub_record(pack, concern, reason)
            with self.subTest(pack=pack, concern=concern, reason=reason.value):
                line = core.remediation(rec, ic)
                self.assertIsInstance(line, str)
                self.assertTrue(line.strip(), "empty remediation")
                self.assertNotIn("\n", line, "a remediation is ONE line")

    def test_an_undeclared_input_class_still_yields_a_line(self):
        """The KeyError-free contract. A caller that resolved no pack — the
        strict-gate helper re-reporting an already-reported list is one — must
        still get a sentence rather than an exception."""
        for pack, concern, reason in PRODUCT:
            rec, _ = _stub_record(pack, concern, reason)
            with self.subTest(pack=pack, concern=concern, reason=reason.value):
                self.assertTrue((core.remediation(rec, None) or "").strip())

    def test_no_line_names_the_runtime_escalation_seam(self):
        """(c). "Run your application to recover the routes" is the obvious
        remediation for a route stub and is precisely what clause 11 forbids a
        crux surface from saying."""
        for pack, concern, reason in PRODUCT:
            rec, ic = _stub_record(pack, concern, reason)
            line = (core.remediation(rec, ic) or "").lower()
            for token in self.FORBIDDEN:
                with self.subTest(pack=pack, concern=concern,
                                  reason=reason.value, token=token):
                    self.assertNotIn(token, line)

    def test_input_class_refresh_is_the_only_source_of_command_text(self):
        """The single-source constraint, asserted as a closed token set.

        Every backticked token in a line must come from the record's own
        `expected`/`found`, from `InputClass.refresh`, or from the two config
        tokens above. A second table of commands inside `remediation` would put
        a token here that is in none of those, and would be free to drift from
        the field the clause 7 advisory prints.
        """
        for pack, concern, reason in PRODUCT:
            rec, ic = _stub_record(pack, concern, reason)
            line = core.remediation(rec, ic)
            supplied = rec["expected"] + " " + rec["found"] + " " + (ic.refresh or "")
            authored = {t for t in self._BACKTICKED.findall(line) if t not in supplied}
            with self.subTest(pack=pack, concern=concern, reason=reason.value):
                self.assertLessEqual(authored, self.CONFIG_TOKENS,
                                     f"unauthored command text in: {line}")

    def test_a_declared_refresh_command_is_rendered_verbatim(self):
        """And changing it changes the line — there is no second table."""
        for command in ("bin/rails db:schema:dump", "make openapi.json"):
            ic = core.InputClass(kind="committed-artifact", expected="an emitted X",
                                 globs=("x",), artifact=("x",), refresh=command)
            rec = {"concern": "api-surface",
                   **core.Verdict.stubbed(core.StubReason.PRECONDITION_MISSING,
                                          ic.expected, "no matching input").as_record()}
            with self.subTest(command=command):
                self.assertEqual(core.remediation(rec, ic),
                                 f"run `{command}`, which produces an emitted X")

    def test_a_pack_declaring_no_refresh_says_so_rather_than_inventing_one(self):
        ic = core.InputClass(kind="parser", expected="Ruby sources under `app/`",
                             globs=("*.rb",))
        rec = {"concern": "module-graph",
               **core.Verdict.stubbed(core.StubReason.PRECONDITION_MISSING,
                                      ic.expected, "no matching input").as_record()}
        self.assertEqual(core.remediation(rec, ic),
                         f"{core.NO_COMMAND} — the derive needs Ruby sources "
                         "under `app/`")

    def test_the_five_reasons_no_command_answers_all_open_with_the_same_phrase(self):
        """A reader should never have to infer that nothing can be run."""
        for reason in core.StubReason:
            if reason is core.StubReason.PRECONDITION_MISSING:
                continue           # the one reason a declared command can answer
            for pack, concern in itertools.product(PACKS, core.CONCERNS):
                rec, ic = _stub_record(pack, concern, reason)
                with self.subTest(pack=pack, concern=concern, reason=reason.value):
                    self.assertTrue(core.remediation(rec, ic).startswith(core.NO_COMMAND))

    def test_an_unknown_stub_reason_is_refused_rather_than_described(self):
        """The set is closed; a line naming an unreviewed reason would reach a
        reader-facing surface."""
        with self.assertRaises(ValueError):
            core.remediation({"concern": "data-model", "verdict": "stubbed",
                              "stub_reason": "no_such_reason",
                              "expected": "x", "found": "y"}, None)

    def test_the_table_prints_the_line_on_its_own_indented_row(self):
        table = core.coverage_table(core.reported_coverage([
            {"concern": "data-model", "extractor": "ruby", "inputs_found": ["a"],
             **core.Verdict.populated(2, 9).as_record()},
            {"concern": "api-surface", "extractor": "stub", "inputs_found": [],
             **core.Verdict.stubbed(core.StubReason.UNSUPPORTED_STACK,
                                    "a pack",
                                    core.UNSUPPORTED_STACK_SENTENCE).as_record()},
        ]))
        rows = table.splitlines()
        remediation_rows = [r for r in rows if "remediation:" in r]
        self.assertEqual(len(remediation_rows), 1, "one stub, one remediation row")
        row = remediation_rows[0]
        self.assertTrue(row.startswith(" "), "the row is indented under detail")
        self.assertIn("no registered pack claims", row)
        # Beside the stub's row, not merged into it.
        self.assertEqual(rows.index(row),
                         next(i for i, r in enumerate(rows)
                              if r.startswith("api-surface")) + 1)
        self.assertNotIn("remediation:",
                         next(r for r in rows if r.startswith("data-model")))


def _coverage_trees() -> list[tuple[str, Path, dict]]:
    """(label, spine dir, parsed coverage.json) for every committed arch tree.

    The ten corpus goldens plus this repository's own dogfood tree. A postcondition
    that holds on one shape and not the others is not a postcondition.
    """
    out = []
    for d in sorted(GOLDEN.iterdir()) if GOLDEN.is_dir() else []:
        cov = d / "_meta" / "coverage.json"
        if cov.is_file():
            out.append((d.name, d, json.loads(cov.read_text(encoding="utf-8"))))
    live = LIVE_ARCH / "_meta" / "coverage.json"
    if live.is_file():
        out.append(("bionic/arch", LIVE_ARCH, json.loads(live.read_text(encoding="utf-8"))))
    return out


class StubReasonSetTests(unittest.TestCase):
    """Clause 3: the reason set is CLOSED at six, and names no environment."""

    def test_exactly_six_members(self):
        self.assertEqual(
            sorted(r.value for r in core.StubReason),
            ["ambiguous_package", "ambiguous_stack", "no_entities",
             "parse_failed", "precondition_missing", "unsupported_stack"],
        )

    def test_no_reason_names_an_environment_condition(self):
        """A missing parser is clause 2's exit 2, never a stub.

        The bound this asserts is why: a verdict that varied with the machine
        would drift a byte-compared gate across environments, so no reason may
        name an interpreter, a dependency, a network, a clock or a permission.
        """
        forbidden = (
            "import", "interpreter", "python", "module", "dependenc", "package_missing",
            "network", "timeout", "permission", "env", "install", "version",
            "not_found_on_path", "unavailable", "clock", "time",
        )
        for reason in core.StubReason:
            for token in forbidden:
                self.assertNotIn(
                    token, reason.value,
                    f"{reason.value!r} names an environment condition ({token!r}); "
                    "an environment failure is exit 2 with nothing written",
                )

    def test_a_reason_outside_the_set_is_refused(self):
        with self.assertRaises(ValueError):
            core.Verdict.stubbed("no_entities", "x", "y")       # a bare string


class VerdictContractTests(unittest.TestCase):
    """Clause 2: exactly one verdict per concern, from committed bytes alone."""

    def test_populated_requires_at_least_one_entity(self):
        """Clause 3's floor, enforced in the constructor rather than by a caller.

        This is the defect that let `fastapi-fullstack` data-model report
        `populated` with 0 tables and `wagtail-bakerydemo` module-graph report
        `populated` with 0 edges: the old classifier asked whether the extractor
        returned any source at all, and both of them did.
        """
        with self.assertRaises(ValueError):
            core.Verdict.populated(n_sources=5, n_entities=0)
        v = core.Verdict.populated(n_sources=5, n_entities=1)
        self.assertEqual(v.as_record(),
                         {"verdict": "populated", "n_sources": 5, "n_entities": 1})

    def test_a_record_carries_one_verdict_s_fields_and_never_the_other_s(self):
        pop = core.Verdict.populated(3, 7).as_record()
        stub = core.Verdict.stubbed(core.StubReason.NO_ENTITIES, "a", "b").as_record()
        self.assertEqual(set(pop), {"verdict", "n_sources", "n_entities"})
        self.assertEqual(set(stub), {"verdict", "stub_reason", "expected", "found"})
        self.assertEqual(set(pop) & set(stub), {"verdict"})

    def test_the_verdict_function_cannot_reach_the_machine(self):
        """A structural claim, not a behavioural one.

        `concern_verdict` takes the concern, the pack name, which rung answered,
        the rendered markdown and the source map — five values every one of
        which is derived from committed bytes. It takes no path and no config,
        so it has nothing to open, and its body names no environment reader.
        """
        params = list(inspect.signature(core.concern_verdict).parameters)
        self.assertEqual(params, ["concern", "pack_name", "rung", "content", "sources"])

        src = inspect.getsource(core.concern_verdict)
        tree = ast.parse(inspect.cleandoc(src))
        banned = {"environ", "getenv", "time", "now", "today", "uname", "gethostname",
                  "cwd", "home", "exists", "is_file", "read_text", "read_bytes",
                  "getcwd", "run", "popen"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, banned,
                                 f"concern_verdict reaches the machine via .{node.attr}")
            if isinstance(node, ast.Name):
                self.assertNotIn(node.id, {"os", "sys", "time", "datetime", "platform",
                                           "subprocess", "socket", "random"},
                                 f"concern_verdict imports the machine via {node.id}")

    def test_the_same_bytes_give_the_same_verdict(self):
        # The header cells are load-bearing, not decoration. `count_concern_entities`
        # is header-driven by design — that is what keeps a `## Indexes` table from
        # inflating the entity count — so a placeholder header like `| t | c |`
        # counts zero rows and the verdict would be `no_entities`, not `populated`.
        md = ("# Data model\n\n## Entities (1 table)\n\n"
              "| table | column |\n|---|---|\n| users | id |\n")
        first = core.concern_verdict("data-model", "ruby", "probe", md, {"db/schema.rb": "x"})
        second = core.concern_verdict("data-model", "ruby", "probe", md, {"db/schema.rb": "x"})
        self.assertEqual(first, second)
        self.assertEqual(first.kind, "populated")


class EntityCountingTests(unittest.TestCase):
    """Clause 3: counted structurally, per concern, from the renderer's output."""

    def test_each_concern_counts_its_own_kind(self):
        dm = ("# Data model\n\n## Entities (1 table)\n\n"
              "| table | column |\n|---|---|\n| users | id |\n| users | email |\n\n"
              "## Indexes\n\n| index | cols |\n|---|---|\n| ix_a | id |\n")
        # The `## Indexes` rows are NOT entities; the harness heuristic that
        # sums every table row in the file would count three here.
        self.assertEqual(core.count_concern_entities("data-model", dm), 2)

        api = ("# API surface\n\n## Routes (1)\n\n"
               "| method | path |\n|---|---|\n| GET | /a |\n\n"
               "## Residuals\n\n- one note\n")
        self.assertEqual(core.count_concern_entities("api-surface", api), 1)

        mg = ("# Module graph\n\n```mermaid\ngraph LR\n  A --> B\n  B --> C\n```\n\n"
              "## Isolated modules (2)\n\n| module |\n|---|\n| D |\n| E |\n")
        self.assertEqual(core.count_concern_entities("module-graph", mg), 2)

        di = ("# Decision index\n\n| # | decision |\n|---|---|\n"
              "| 1 | Record decisions |\n")
        self.assertEqual(core.count_concern_entities("decision-index", di), 1)

    def test_a_renderer_that_found_nothing_counts_zero(self):
        """The two shapes the baseline measured, reduced to their structure."""
        # A data model that rendered a timeline but no entity section.
        dm = ("# Data model\n\n_Derived from Alembic._\n\n"
              "## Alembic migration timeline (5)\n\n| rev | down |\n|---|---|\n| a | — |\n")
        self.assertEqual(core.count_concern_entities("data-model", dm), 0)
        # A module graph with an empty mermaid fence.
        mg = ("# Module graph\n\n_4 modules, 0 edges._\n\n```mermaid\ngraph LR\n```\n\n"
              "## Isolated modules (4)\n\n| module |\n|---|\n| a |\n")
        self.assertEqual(core.count_concern_entities("module-graph", mg), 0)

    def test_the_crux_pack_s_subsection_data_model_counts(self):
        """The crux dogfood pack renders entities as `###` subsections, not rows."""
        dm = ("# Data model\n\n## Entities (JSON Schema)\n\n"
              "### crux promptbook\n\n| field | type |\n|---|---|\n| id | string |\n\n"
              "### crux run snapshot\n\n| field | type |\n|---|---|\n| id | string |\n")
        self.assertEqual(core.count_concern_entities("data-model", dm), 4)  # 2 heads + 2 rows


class HonestStubLineTests(unittest.TestCase):
    """Clause 4: the stub line names reason, expectation and finding."""

    def test_the_reserved_sentence_is_reachable_only_from_unsupported_stack(self):
        """The measured defect: all 23 baseline stubs said it, and zero were it.

        In 20 a pack was selected and an extractor existed; in the other 3 the
        sentence was true of the resolved pack. This asserts the sentence over
        every reason the renderer can be handed, not only the ones a corpus
        happens to produce.
        """
        for reason in core.StubReason:
            line = core.render_stub_line(
                core.Verdict.stubbed(reason, expected="an input", found="nothing")
            )
            with self.subTest(reason=reason.value):
                self.assertNotIn(core.UNSUPPORTED_STACK_SENTENCE, line)
        unsupported = core.concern_verdict("api-surface", core.STUB_PACK_NAME, "stub",
                                           core.NO_EXTRACTOR, {})
        self.assertEqual(unsupported.reason, core.StubReason.UNSUPPORTED_STACK)
        self.assertIn(core.UNSUPPORTED_STACK_SENTENCE,
                      core.render_stub_line(unsupported))

    def test_no_golden_says_the_reserved_sentence_for_another_reason(self):
        """The same claim against every committed arch tree."""
        offenders = []
        for label, spine_dir, cov in _coverage_trees():
            by_concern = {r["concern"]: r for r in cov["concerns"]}
            for concern in SPINE:
                path = spine_dir / f"{concern}.md"
                if not path.is_file():
                    continue
                if core.UNSUPPORTED_STACK_SENTENCE not in path.read_text(encoding="utf-8"):
                    continue
                rec = by_concern.get(concern, {})
                if rec.get("stub_reason") != "unsupported_stack":
                    offenders.append(f"{label}/{concern}: {rec.get('stub_reason')}")
        self.assertEqual(offenders, [],
                         "the reserved sentence appears for a reason other than "
                         "unsupported_stack: " + ", ".join(offenders))

    def test_every_stubbed_concern_renders_a_stub_line_naming_its_reason(self):
        missing = []
        for label, spine_dir, cov in _coverage_trees():
            for rec in cov["concerns"]:
                if rec["verdict"] != "stubbed":
                    continue
                path = spine_dir / f"{rec['concern']}.md"
                if not path.is_file():
                    continue
                want = f"> _stub: {rec['stub_reason']} — "
                if want not in path.read_text(encoding="utf-8"):
                    missing.append(f"{label}/{rec['concern']}")
        self.assertEqual(missing, [],
                         "stubbed concerns with no matching stub line: " + ", ".join(missing))

    def test_a_no_entities_stub_keeps_the_content_the_extractor_produced(self):
        """The stub line is INSERTED, not substituted for the file.

        A `no_entities` concern really did read its inputs — fastapi-fullstack's
        data-model read five Alembic migrations — so discarding the render would
        delete extraction that happened. The verdict is about entities of the
        concern's own kind, not about the file being empty.
        """
        content = ("# Data model\n\n_Derived from Alembic._\n\n"
                   "## Alembic migration timeline (5)\n\n| rev |\n|---|\n| a |\n")
        verdict = core.concern_verdict("data-model", "python", "probe", content,
                                       {"m.py": "h"})
        self.assertEqual(verdict.reason, core.StubReason.NO_ENTITIES)
        out = core._apply_stub_line(content, verdict)
        self.assertIn("## Alembic migration timeline (5)", out)
        self.assertIn("| a |", out)
        self.assertEqual(out.split("\n")[2], core.render_stub_line(verdict))

    def test_a_full_stub_replaces_the_marker_in_place(self):
        content = core._stub_extract("api-surface", Path("."), "bionic")[0]
        verdict = core.concern_verdict("api-surface", "python", "stub", content, {})
        out = core._apply_stub_line(content, verdict)
        self.assertNotIn(core.NO_EXTRACTOR.rstrip("\n"), out)
        self.assertIn("precondition_missing", out)
        self.assertEqual(len(out.split("\n")), len(content.split("\n")))


class PopulatedHeaderPostconditionTests(unittest.TestCase):
    """Clause 3's postcondition: no populated header over an empty table."""

    def test_no_committed_spine_file_is_populated_with_zero_entities(self):
        offenders = []
        for label, spine_dir, cov in _coverage_trees():
            for rec in cov["concerns"]:
                if rec["verdict"] != "populated":
                    continue
                path = spine_dir / f"{rec['concern']}.md"
                if not path.is_file():
                    continue
                n = core.count_concern_entities(rec["concern"],
                                                path.read_text(encoding="utf-8"))
                if n < 1:
                    offenders.append(f"{label}/{rec['concern']}")
                elif n != rec["n_entities"]:
                    offenders.append(
                        f"{label}/{rec['concern']}: recorded {rec['n_entities']}, "
                        f"rendered {n}")
        self.assertEqual(offenders, [],
                         "populated over an empty (or miscounted) table: "
                         + ", ".join(offenders))

    def test_the_corpus_still_contains_a_non_universal_stub(self):
        """Non-vacuity: the postcondition above must hold over a corpus where
        `stubbed` is a REACHABLE answer, not one every concern escaped.

        This pinned NAMED cases as its evidence, and both have since moved.
        `fastapi-fullstack/data-model` was a real `no_entities` until dev loop
        2's SQLModel probe read its twelve columns.
        `wagtail-bakerydemo/module-graph` was `no_entities` because the wrong
        pack won detection — the node pack graphed four JavaScript files that
        import nothing from each other — and dev loop 3's `requirements.txt`
        marker resolved that repository to python, which graphs 33 edges over
        107 modules. Neither is evidence any more, and a case that no longer
        holds comes out rather than being softened.

        So the non-vacuity claim moves DOWN a level rather than being dropped.
        The corpus-wide half asserts a stub still exists somewhere outside
        `decision-index` (the universally-stubbed concern, which would make this
        vacuous on its own); the unit half asserts the `no_entities` branch is
        still reachable, which is the specific branch the corpus no longer
        exercises. Both must hold, and the second is what a careless re-bless
        cannot quietly satisfy.
        """
        stubs = []
        for label, _spine_dir, cov in _coverage_trees():
            for rec in cov["concerns"]:
                if rec["verdict"] == "stubbed" and rec["concern"] != "decision-index":
                    stubs.append(f"{label}/{rec['concern']}: {rec['stub_reason']}")
        self.assertTrue(
            stubs,
            "every non-universal concern in the corpus is populated, so the "
            "postcondition above is vacuous: nothing proves the classifier can "
            "still answer `stubbed` at all")

    def test_the_no_entities_branch_is_still_reachable(self):
        """The unit half of the non-vacuity claim above.

        No corpus repository renders a real table with zero entities of its own
        concern's kind today, so this states the branch directly: an extractor
        that consumed inputs and declared no entity is `no_entities`, never
        `populated`.
        """
        content = ("# Module graph\n\n_Derived from imports._\n\n"
                   "## Modules (0)\n\n```mermaid\ngraph LR\n```\n")
        verdict = core.concern_verdict(
            "module-graph", "python", "probe", content, {"a.py": "h"})
        self.assertEqual(verdict.kind, "stubbed")
        self.assertEqual(verdict.reason, core.StubReason.NO_ENTITIES)


class TwoChannelTests(unittest.TestCase):
    """Clause 2: an annotation is reported and never recorded."""

    def test_the_annotation_vocabulary_appears_nowhere_under_arch(self):
        """The whole point of the split, asserted where it would show.

        Every committed arch tree is searched for every annotation token and for
        the `annotations` key itself. A hit means an annotation reached the
        byte-compared set, which would make the drift gate depend on the commit
        graph — the one thing clause 2 exists to prevent.

        Carries the same two non-vacuity guards as its sibling below, and for
        the same reason: `roots` is built by filtering on `is_dir()`, so with
        neither tree on disk the loop never ran and the test passed on an empty
        result. It was proven vacuous — with both trees removed this passed
        while the sibling correctly failed. The guards are what make the
        verdict name its own scope.
        """
        tokens = list(core.ANNOTATIONS) + ["annotations"]
        hits, scanned = [], 0
        roots = [p for p in (GOLDEN, LIVE_ARCH) if p.is_dir()]
        for root in roots:
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                scanned += 1
                text = path.read_text(encoding="utf-8", errors="replace")
                for token in tokens:
                    if token in text:
                        hits.append(f"{path.relative_to(REPO)}: {token}")
        self.assertGreater(scanned, 20, "no committed arch tree was searched")
        self.assertGreater(len(tokens), 1, "the token set collapsed")
        self.assertEqual(hits, [], "annotation vocabulary under arch/: " + ", ".join(hits))

    def test_no_remediation_line_appears_anywhere_under_arch(self):
        """Clause 9's line is REPORTED, and this is where that would show.

        The same search as the annotation prohibition above, over the same
        committed trees, for the `remediation` key, the fixed `NO_COMMAND`
        phrase, and every line the full product can produce. A hit would mean a
        reported-only sentence entered the byte-compared set — and it would then
        move a golden every time a pack's refresh command changed, which is a
        drift gate reporting on the reporting channel.
        """
        tokens = {"remediation", core.NO_COMMAND}
        for pack, concern, reason in PRODUCT:
            rec, ic = _stub_record(pack, concern, reason)
            tokens.add(core.remediation(rec, ic))
        hits, scanned = [], 0
        for root in [p for p in (GOLDEN, LIVE_ARCH) if p.is_dir()]:
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                scanned += 1
                text = path.read_text(encoding="utf-8", errors="replace")
                hits += [f"{path.relative_to(REPO)}: {t}" for t in sorted(tokens)
                         if t in text]
        # Non-vacuity: an absent corpus would make the search above pass while
        # measuring nothing, which is the exact shape this postcondition is
        # supposed to rule out elsewhere.
        self.assertGreater(scanned, 20, "no committed arch tree was searched")
        self.assertGreater(len(tokens), 20, "the token set collapsed")
        self.assertEqual(hits, [], "remediation text under arch/: " + ", ".join(hits))

    def test_the_reported_channel_carries_every_recorded_verdict(self):
        recorded = [
            {"concern": "data-model", "extractor": "ruby", "inputs_found": ["a"],
             **core.Verdict.populated(1, 3).as_record()},
            {"concern": "api-surface", "extractor": "stub", "inputs_found": [],
             **core.Verdict.stubbed(core.StubReason.PRECONDITION_MISSING,
                                    "an input", "none").as_record()},
        ]
        reported = core.reported_coverage(recorded)
        self.assertEqual(len(reported), len(recorded))
        added = {"annotations", "remediation"}
        for rec, rep in zip(recorded, reported):
            self.assertEqual({k: v for k, v in rep.items() if k not in added}, rec)
            self.assertEqual(rep["annotations"], [])
        # Additive, and only where there is something to remedy.
        self.assertNotIn("remediation", reported[0])
        self.assertIn("remediation", reported[1])

    def test_reported_coverage_does_not_touch_the_recorded_list(self):
        """The copy is the mechanism, so state it directly: the list handed in
        is the one `_synthesize_coverage` serializes, and annotating it in place
        would put the reported channel into `coverage.json`."""
        recorded = [{"concern": "api-surface", "extractor": "stub", "inputs_found": [],
                     **core.Verdict.stubbed(core.StubReason.NO_ENTITIES,
                                            "a route row", "1 input(s) consumed and "
                                            "0 route rows").as_record()}]
        before = json.dumps(recorded, sort_keys=True)
        core.reported_coverage(recorded, {"api-surface": core.DECISION_INDEX_INPUT})
        self.assertEqual(json.dumps(recorded, sort_keys=True), before)

    def test_the_reported_channel_is_the_build_s_own_out_parameter(self):
        """One build produces both channels, so they cannot disagree."""
        report: list = []
        tree = core._build(REPO, "bionic", report=report)
        recorded = json.loads(tree["_meta/coverage.json"])["concerns"]
        self.assertEqual([r["concern"] for r in report],
                         [r["concern"] for r in recorded])
        for rep, rec in zip(report, recorded):
            self.assertEqual(rep["verdict"], rec["verdict"])
        # The recorded channel carries neither added key, whatever this
        # repository's own verdicts happen to be today.
        for rec in recorded:
            self.assertNotIn("remediation", rec)
            self.assertNotIn("annotations", rec)
        for rep, rec in zip(report, recorded):
            if rec["verdict"] != "populated":
                self.assertTrue(rep["remediation"].strip())

    def test_the_coverage_table_renders_both_verdict_shapes(self):
        table = core.coverage_table(core.reported_coverage([
            {"concern": "data-model", **core.Verdict.populated(2, 9).as_record()},
            {"concern": "api-surface",
             **core.Verdict.stubbed(core.StubReason.UNSUPPORTED_STACK,
                                    "a pack", core.UNSUPPORTED_STACK_SENTENCE).as_record()},
        ]))
        self.assertIn("9 entities from 2 sources", table)
        self.assertIn("unsupported_stack", table)


class InputClassDeclarationTests(unittest.TestCase):
    """ADR-0097 part 1: every pack declares an input class per concern, and
    `kind` is DERIVED from the probe chain rather than hand-written.

    The enrollment half of this lives in `tools/tests/test_schema_invariants.py`
    beside the regenerator roster; what is here is the shape of a declaration
    and the glob set postcondition (e) derives its edit set from.
    """

    def test_every_registered_pack_declares_all_four_concerns(self):
        for pack in sorted(core.REGISTERED_PACKS) + [core.STUB_PACK_NAME]:
            declared = core.input_classes(pack)
            with self.subTest(pack=pack):
                self.assertEqual(sorted(declared), sorted(core.CONCERNS))
                for concern, ic in declared.items():
                    self.assertIn(ic.kind, core.INPUT_CLASS_KINDS, f"{pack}/{concern}")
                    self.assertTrue(ic.expected.strip(), f"{pack}/{concern}: no expectation")

    def test_a_non_stub_declaration_names_the_files_it_may_read(self):
        for pack in sorted(core.REGISTERED_PACKS):
            for concern, ic in core.input_classes(pack).items():
                with self.subTest(pack=pack, concern=concern):
                    self.assertTrue(ic.globs,
                                    f"{pack}/{concern} declares {ic.kind} but no input set")

    def test_declared_globs_are_repo_relative(self):
        for pack in sorted(core.REGISTERED_PACKS) + [core.STUB_PACK_NAME]:
            for glob in core.declared_input_globs(pack):
                self.assertFalse(glob.startswith("/"), glob)
                self.assertFalse(glob.startswith("~"), glob)
                self.assertNotIn("..", glob)

    def test_no_pack_map_hand_writes_kind(self):
        """The declaration moved from the concern to the probe (ADR-0097 part
        1). A pack's raw `INPUT_CLASSES` map must declare no `kind` at all —
        every `InputClass` it builds keeps the field at its bare default `""`,
        so the only place a real `kind` can come from is `input_classes()`'s
        derivation. A pack that hand-writes `kind` in its map would bypass the
        derivation silently: `input_classes()` overwrites it via `_replace`
        regardless, so a stale hand-written value would never be caught by
        reading the derived result — it has to be caught here, on the raw map.
        """
        for pack in sorted(core.REGISTERED_PACKS):
            module = core._pack_module(pack)
            for concern, ic in getattr(module, "INPUT_CLASSES", {}).items():
                with self.subTest(pack=pack, concern=concern):
                    self.assertEqual(
                        ic.kind, "",
                        f"{pack}/{concern}'s INPUT_CLASSES entry hand-writes "
                        f"kind={ic.kind!r} — kind is derived, never declared")

    def test_every_registered_probe_declares_a_kind_in_the_closed_set(self):
        """The real validation ADR-0097 part 1 asks for: `INPUT_CLASS_KINDS`
        was declarative only, and enforced nowhere but a test, under ADR-0096.
        This walks every registered `Probe`, not just the derived concern
        classes, so a probe whose kind is individually wrong is caught even
        when a stronger sibling rung in the same chain would hide it from the
        concern-level view."""
        for pack in sorted(core.REGISTERED_PACKS):
            for concern, probe_list in core._pack_module(pack).probes().items():
                for probe in probe_list:
                    with self.subTest(pack=pack, concern=concern,
                                      probe=probe.extract.__qualname__):
                        self.assertIn(probe.kind, core.INPUT_CLASS_KINDS)


class DeriveConcernKindTests(unittest.TestCase):
    """`core.derive_concern_kind` — the total order and its two edge cases
    (ADR-0097 part 1)."""

    def test_the_minimum_under_the_total_order_wins(self):
        self.assertEqual(
            core.derive_concern_kind(["parser", "committed-artifact"]), "parser")
        self.assertEqual(
            core.derive_concern_kind(["committed-artifact", "regex-over-source"]),
            "regex-over-source")
        self.assertEqual(
            core.derive_concern_kind(["committed-artifact"]), "committed-artifact")

    def test_an_all_stub_chain_derives_stub(self):
        """Including the empty chain — the empty pack's case."""
        self.assertEqual(core.derive_concern_kind(["stub", "stub"]), "stub")
        self.assertEqual(core.derive_concern_kind([]), "stub")

    def test_a_stub_rung_beside_a_real_rung_is_ignored(self):
        """`stub` is the weakest value in the order but is never itself the
        answer once any other rung can answer."""
        self.assertEqual(core.derive_concern_kind(["stub", "parser"]), "parser")
        self.assertEqual(
            core.derive_concern_kind(["regex-over-source", "stub"]),
            "regex-over-source")

    def test_a_kind_outside_the_closed_set_raises(self):
        with self.assertRaises(ValueError) as ctx:
            core.derive_concern_kind(["parser", "regex"])
        self.assertIn("regex", str(ctx.exception))


class CruxCliVerbReaderTests(unittest.TestCase):
    """`packs.crux._cli_verbs`: the `ast` parse ADR-0097 part 2 converted to.

    Part 2 holds api-surface regex-free, and the crux pack's `crux-env.py`
    subcommand read was the one open case. It closed by conversion, so these
    tests are about the PARSE — that it finds what the pattern found, and that
    it keeps the resolve-or-drop posture the pattern had on input no parser
    accepts.
    """

    @staticmethod
    def _verbs(source):
        from crux.arch.packs import crux as crux_pack   # noqa: PLC0415
        return crux_pack._cli_verbs(source)

    def test_finds_every_string_literal_add_parser_verb(self):
        verbs = self._verbs(
            "sub = p.add_subparsers()\n"
            "sub.add_parser('init')\n"
            'sub.add_parser("check", help="x")\n'
        )
        self.assertEqual(verbs, {"init", "check"})

    def test_a_bare_name_add_parser_call_is_read_too(self):
        """The pattern this parse replaced (`add_parser\\(\\s*["\']([a-z-]+)`)
        matched a bare call as well as an attribute one, so accepting only
        `<expr>.add_parser(...)` would NARROW the reader. The failure mode is
        what makes it worth a test: on an `add_parser = sub.add_parser`
        binding the verbs would not be misread, they would silently vanish,
        and an empty api-surface reads as "this CLI has no verbs" rather than
        as an error."""
        verbs = self._verbs(
            "add_parser = sub.add_parser\n"
            "add_parser('init')\n"
            "sub.add_parser('check')\n"
        )
        self.assertEqual(verbs, {"init", "check"})

    def test_an_unrelated_bare_call_is_not_read_as_a_verb(self):
        """The control for the leg above: widening to bare names must not make
        the reader swallow any single-string call."""
        self.assertEqual(self._verbs("add_argument('--flag')\nparse('x')\n"), set())

    def test_drops_a_non_literal_verb_rather_than_guessing(self):
        """A name argparse resolves at runtime has no static value, so it is
        dropped — the resolve-or-drop posture every reader in this engine
        takes, not a silent empty answer for the whole file."""
        verbs = self._verbs(
            "sub.add_parser(name)\n"
            "sub.add_parser('set')\n"
        )
        self.assertEqual(verbs, {"set"})

    def test_unparseable_source_drops_to_the_empty_set(self):
        self.assertEqual(self._verbs("def (:\n"), set())

    def test_a_nul_byte_drops_rather_than_crashing(self):
        """A NUL byte drops to the empty set instead of crashing the derive.

        The upstream `errors="replace"` decode preserves NULs, and the pattern
        this reader replaced tolerated such a file, so the conversion must too.
        The control is PINNED TO THE EXCEPTION CLASS rather than to a bare
        "it did not raise": this asserts that `ast.parse` really does reject
        the fixture, and that the class it raises on the interpreter this
        project pins is `SyntaxError` — so the reader's single `except` clause
        is the one that catches it, and a second clause for `ValueError` (the
        class older CPython raised here) would be dead code.
        """
        source = "sub.add_parser('init')\n\x00"
        with self.assertRaises(SyntaxError):
            ast.parse(source)            # the fixture really does trigger it
        self.assertEqual(self._verbs(source), set())

    def test_matches_the_pattern_it_replaced_on_the_real_cli(self):
        """Byte-identity's other half: the rendered verb list did not move."""
        # `crux/scripts/crux-env.py` ships inside the staged artifact, so this
        # is not a dev-only surface and takes no `require_dev_surface` guard.
        cli = SCRIPTS / "crux-env.py"
        self.assertTrue(cli.is_file(), f"the CLI this reader parses is gone: {cli}")
        source = cli.read_text(encoding="utf-8")
        pattern = set(re.findall(r"add_parser\(\s*[\"']([a-z-]+)[\"']", source))
        self.assertEqual(self._verbs(source), pattern)
        self.assertTrue(pattern, "the fixture found no verbs — the CLI moved")

    def test_a_recursion_error_drops_rather_than_aborting_the_derive(self):
        """A deeply nested but VALID file drops to the empty set.

        `SyntaxError` alone does not cover what `ast.parse` raises on
        repository-controlled source. CPython's own parser raises
        `RecursionError` while BUILDING the tree for source it accepted, and
        200 KB of `1 + 1 + …` reaches it — well inside the 2 MB
        `_safe_read_bytes` bound, so the gate hands the file straight to the
        parser. An escaping `RecursionError` aborts the whole derive and
        surfaces as exit 2, the code reserved for an ENVIRONMENT failure, on the
        exact invocation `.github/workflows/check-arch-drift.yml` runs.

        The control is PINNED TO THE EXCEPTION CLASS, as the NUL case above is:
        this first asserts the fixture really does raise `RecursionError`, so
        the test cannot pass because the fixture stopped triggering it.
        """
        source = "sub.add_parser('init')\nx = " + "+".join(["1"] * 100000) + "\n"
        self.assertLess(len(source.encode("utf-8")), core._MAX_FILE_BYTES,
                        "the fixture must sit inside the read bound it is "
                        "about, or it never reaches the parser")
        with self.assertRaises(RecursionError):
            ast.parse(source)            # the fixture really does trigger it
        self.assertEqual(self._verbs(source), set())

    def test_a_memory_error_drops_rather_than_aborting_the_derive(self):
        """The parser's OTHER refusal on valid source, and the same lane.

        100 KB of unary minus overflows the parser stack and raises
        `MemoryError`, not `RecursionError` — two classes from one cause, which
        is why the shared `_PARSE_FAILED` tuple names four and not two.
        """
        source = "sub.add_parser('init')\nx = " + "-" * 100000 + "1\n"
        self.assertLess(len(source.encode("utf-8")), core._MAX_FILE_BYTES)
        with self.assertRaises(MemoryError):
            ast.parse(source)            # the fixture really does trigger it
        self.assertEqual(self._verbs(source), set())

    def test_the_reader_catches_the_shared_parse_failure_tuple(self):
        """The four classes are named ONCE, in `core`, and this reader binds
        that name rather than a copy of it.

        The five python-pack parse sites and this one are the same contract;
        two tuples would let one narrow without the other noticing, which is
        how this reader came to catch `SyntaxError` alone.
        """
        for cls in (SyntaxError, ValueError, RecursionError, MemoryError):
            with self.subTest(cls=cls.__name__):
                self.assertIn(cls, core._PARSE_FAILED)


class CruxPackRenderEscapingTests(unittest.TestCase):
    """Two spine cells the dev-loop-1 reader conversion left unescaped.

    The patterns those readers replaced carried character classes —
    `^([a-z_]+)\\s*:` for the manifest keys and
    `add_parser\\(\\s*["\']([a-z-]+)["\']` for the CLI verbs — and the classes
    were the only thing keeping a newline out of the rendered spine. A real
    parse takes the literal it was handed, so the escape has to be at the
    render, where every other repository-controlled value in this pack already
    takes it (`_cell` / `_prose_cell`, `packs/crux.py`).

    Hostile fixtures, because the threat this pack names for itself is
    markdown injection by newline-at-line-start: a value carrying a newline
    opens a heading, a code fence, or a footnote definition that
    `count_concern_entities` then counts.
    """

    #: A value that opens a second H2 and an unclosed fence at line start.
    HOSTILE = "init\n## Owned by the attacker\n```\n"

    def _tree(self, *, manifest_key=None, cli_verb=None) -> Path:
        """A minimal tree `_detect_crux` fires on, plus the one hostile input.

        Built rather than reused: the corpus goldens are the byte-identity
        record and must not carry an attack fixture.
        """
        import tempfile
        root = Path(tempfile.mkdtemp(prefix="crux-pack-escape-"))
        self.addCleanup(shutil.rmtree, root, True)
        (root / "crux" / "schemas").mkdir(parents=True)
        (root / "crux" / "schemas" / "demo.json").write_text(
            json.dumps({"title": "Demo", "properties": {"a": {"type": "string"}}}),
            encoding="utf-8")
        (root / "crux" / "skills" / "demo").mkdir(parents=True)
        (root / "crux" / "skills" / "demo" / "SKILL.md").write_text(
            "---\nname: demo\ndescription: a demo skill\n---\n\nbody\n",
            encoding="utf-8")
        if manifest_key is not None:
            (root / "bionic").mkdir()
            (root / "bionic" / "manifest.yml").write_text(
                json.dumps({manifest_key: 1}), encoding="utf-8")  # JSON is YAML
        if cli_verb is not None:
            (root / "crux" / "scripts").mkdir(parents=True)
            (root / "crux" / "scripts" / "crux-env.py").write_text(
                "import argparse\np = argparse.ArgumentParser()\n"
                "sub = p.add_subparsers()\n"
                f"sub.add_parser({cli_verb!r})\n", encoding="utf-8")
        return root

    def _assert_no_injected_structure(self, markdown: str, label: str):
        """No line the fixture contributed opens a heading or a fence."""
        injected = [ln for ln in markdown.split("\n")
                    if ln.startswith("## Owned by the attacker")
                    or ln.strip() == "```"]
        self.assertEqual(injected, [], f"{label}: the hostile value reached "
                         f"line-start position in the rendered spine:\n{markdown}")

    def test_a_manifest_key_carrying_a_newline_opens_no_heading(self):
        from crux.arch.packs import crux as crux_pack   # noqa: PLC0415
        root = self._tree(manifest_key=self.HOSTILE)
        md, _ = crux_pack.extract_data_model(root, "bionic")
        self.assertIn("## Manifest shape", md,
                      "the fixture never reached the manifest reader")
        self._assert_no_injected_structure(md, "data-model")

    def test_a_cli_verb_carrying_a_newline_opens_no_heading(self):
        from crux.arch.packs import crux as crux_pack   # noqa: PLC0415
        root = self._tree(cli_verb=self.HOSTILE)
        md, _ = crux_pack.extract_api_surface(root, "bionic")
        self.assertIn("`crux-env` CLI verbs", md,
                      "the fixture never reached the verb reader")
        self._assert_no_injected_structure(md, "api-surface")

    def test_an_ordinary_key_and_verb_render_unchanged(self):
        """The positive control for both assertions above.

        An absence assertion over a hostile fixture passes just as well when
        the reader returned nothing at all, so this pins that the ordinary
        value still renders — the escape is lossless for valid input.
        """
        from crux.arch.packs import crux as crux_pack   # noqa: PLC0415
        root = self._tree(manifest_key="schema_version", cli_verb="init")
        dm, _ = crux_pack.extract_data_model(root, "bionic")
        api, _ = crux_pack.extract_api_surface(root, "bionic")
        self.assertIn("`schema_version`", dm)
        self.assertIn("`init`", api)


class CruxPackDeclaredParserTests(unittest.TestCase):
    """The crux pack DECLARES the parser it needs (ADR-0097 parts 1 and 3).

    `core._frontmatter` degrades to a line regex when PyYAML is missing, and
    the degraded reader answers differently: the same source tree with and
    without PyYAML produced two different `spine_hash` values, both at exit 0,
    with identical `tool_pins`. The `ParserUnavailable` raise in the manifest
    reader fires only on a tree that HAS a `<docs_dir>/manifest.yml`, so a
    `crux/`-only tree — the shape `sync.sh` publishes — degraded silently.

    The declaration is the fix that reaches every reader at once:
    `resolve_declared_parsers` runs before any extractor, so an unresolvable
    parser is ADR-0060 Decision 4's environment failure — exit 2, nothing
    written — rather than a quieter spine.
    """

    CONCERNS_DECLARING_YAML = ("data-model", "api-surface")

    def test_both_yaml_reading_concerns_declare_the_parser(self):
        classes = core.input_classes("crux")
        for concern in self.CONCERNS_DECLARING_YAML:
            with self.subTest(concern=concern):
                self.assertIn("yaml", classes[concern].parser,
                              f"crux/{concern} reads YAML through "
                              "`core._frontmatter`, whose fallback computes a "
                              "different spine hash — so it must declare the "
                              "parser that the refusal path checks")

    def test_an_unresolvable_yaml_refuses_before_any_extractor_runs(self):
        """The refusal, driven through the real entry point.

        `find_spec` is patched rather than the import, because
        `resolve_declared_parsers` resolves by `find_spec` precisely so no
        module is executed.
        """
        import importlib.util                            # noqa: PLC0415
        real = importlib.util.find_spec

        def missing_yaml(name, *a, **kw):
            if name == "yaml":
                return None
            return real(name, *a, **kw)

        with unittest.mock.patch.object(importlib.util, "find_spec", missing_yaml):
            with self.assertRaises(core.ParserUnavailable) as caught:
                core.resolve_declared_parsers("crux")
        self.assertIn("yaml", str(caught.exception))

    def test_the_positive_control_every_pack_resolves_when_yaml_is_present(self):
        """The control for the refusal above: the patch is what causes it.

        This asserted that the python pack resolved cleanly under the SAME
        patch, which was a real control only while some pack declared no yaml.
        The universal `decision-index` declaration ended that — every pack
        declares it now — so the honest control is the unpatched environment,
        where every pack must resolve. Without this leg, a
        `resolve_declared_parsers` that raised unconditionally would satisfy
        the refusal test above.
        """
        for pack in sorted(core.REGISTERED_PACKS):
            with self.subTest(pack=pack):
                core.resolve_declared_parsers(pack)      # must not raise

    def test_the_universal_decision_index_declares_the_parser_for_every_pack(self):
        """The concern the crux-pack declaration did NOT reach.

        `decision-index` is bound by the core for EVERY pack and reads ADR
        frontmatter through `core._frontmatter` — the same degrading reader. So
        declaring `yaml` on the crux pack alone left the identical defect live
        on the four packs downstream repositories actually use: one tree, two
        `spine_hash` values, both at exit 0, identical `tool_pins`, and the
        tree WRITTEN both times. Declaring it on the universal concern is what
        makes `resolve_declared_parsers` refuse for every pack rather than one.
        """
        for pack in sorted(core.REGISTERED_PACKS):
            with self.subTest(pack=pack):
                self.assertIn(
                    "yaml", core.input_classes(pack)["decision-index"].parser,
                    f"{pack}/decision-index reads ADR frontmatter through "
                    "`_frontmatter`, whose regex fallback computes a different "
                    "spine hash — every pack must declare the parser, not just "
                    "the one whose own concerns happened to be reviewed")

    def test_every_pack_refuses_when_yaml_is_unresolvable(self):
        """The end of the S4 class: no registered pack derives a spine through
        the frontmatter fallback."""
        import importlib.util                            # noqa: PLC0415
        real = importlib.util.find_spec

        def missing_yaml(name, *a, **kw):
            return None if name == "yaml" else real(name, *a, **kw)

        with unittest.mock.patch.object(importlib.util, "find_spec", missing_yaml):
            for pack in sorted(core.REGISTERED_PACKS):
                with self.subTest(pack=pack):
                    with self.assertRaises(core.ParserUnavailable):
                        core.resolve_declared_parsers(pack)


#: The escapers that make a target-controlled value safe to render.
_ESCAPERS = frozenset({"_cell", "_prose_cell", "_md_escape", "_ascii"})

#: Calls whose RESULT is a target-controlled string. `_rel` returns a
#: repo-relative path built from a target filename, and a filename is
#: attacker-controlled on any repository the derive is pointed at.
_TAINTED_CALLS = frozenset({"_rel"})


def _bare_rel_offenders(src: str) -> list[int]:
    """Line numbers where a `_rel(...)` result reaches a string unescaped.

    Module-level and taking SOURCE TEXT rather than a path, so the gate below
    and its positive control drive THE SAME code. They did not: the control
    re-implemented the walk inline, so renaming `_rel` in the real matcher left
    the control passing on its own private copy — a control that cannot fail for
    the reason it exists is the false-green shape this project has a skill for.

    Detection is parent-directed rather than shape-directed, which is what makes
    it spelling-independent. Every `_rel(...)` call is found, then its enclosing
    expression is walked outward: if an escaper wraps it, it is safe; if a
    string-building context encloses it first, it is an offender. That covers the
    f-string spelling and the three the earlier version missed — `"..." + _rel()`,
    `"{}".format(_rel())` and `"".join([..., _rel(), ...])` — because none of them
    is a special case here, only a different enclosing node.

    Not covered, and named rather than implied: a `_rel` result bound to a local
    name and rendered several statements later. That needs whole-function taint
    with an untaint rule for rebinding through an escaper.
    """
    tree = ast.parse(src)
    parents: dict = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def _is_call_to(node, names) -> bool:
        if not isinstance(node, ast.Call):
            return False
        f = node.func
        if isinstance(f, ast.Name):
            return f.id in names
        if isinstance(f, ast.Attribute):
            return f.attr in names
        return False

    offenders: list[int] = []
    for node in ast.walk(tree):
        if not _is_call_to(node, _TAINTED_CALLS):
            continue
        cur = node
        while cur in parents:
            parent = parents[cur]
            if _is_call_to(parent, _ESCAPERS):
                break                              # wrapped — safe
            # A string-building context reached before any escaper.
            if isinstance(parent, ast.JoinedStr):
                offenders.append(node.lineno)
                break
            if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Add):
                other = parent.right if parent.left is cur else parent.left
                if isinstance(other, (ast.Constant, ast.JoinedStr, ast.BinOp)):
                    offenders.append(node.lineno)
                    break
            if _is_call_to(parent, {"format", "join"}):
                offenders.append(node.lineno)
                break
            cur = parent
    return sorted(offenders)


class ArchRenderEscapeSweepTests(unittest.TestCase):
    """No target-repo PATH reaches rendered markdown unescaped, engine-wide.

    Stated over the whole engine rather than per site, because per-site review is
    what let this class survive three rounds: one round fixed two cells, the next
    found four siblings in the same two functions and three other packs, and a
    mechanical sweep then found two more.

    **Scoped to `_rel(...)`, and the narrowness is deliberate.** A walk that also
    flagged bare names reported 55 sites of which two were real — the rest were
    loop counters, `len()` results, "yes"/"no" literals, and values escaped one
    statement earlier by rebinding the name. A gate with a 96% false-positive
    rate gets muted, and a muted gate is worse than none.

    That scoping is a real limit and it has already cost something: an
    independent audit found an unescaped route verb in `packs/ruby.py` that this
    gate cannot see, because a verb is not a `_rel` result. Do not read a clean
    run here as "no injection is possible"; read it as "no target PATH renders
    bare". The residual is pack review's, the same one ADR-0097 part 1 names for
    the input-class declarations themselves.
    """

    ARCH = SCRIPTS / "crux" / "arch"

    def test_no_rel_path_renders_into_markdown_unescaped(self):
        offenders = []
        for path in sorted(self.ARCH.rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            src = path.read_text(encoding="utf-8")
            lines = src.splitlines()
            for lineno in _bare_rel_offenders(src):
                offenders.append(
                    f"{path.relative_to(SCRIPTS.parents[1])}:{lineno}  "
                    f"{lines[lineno - 1].strip()[:74]}")
        self.assertEqual(
            sorted(offenders), [],
            "a target-repo path renders into markdown without an escaper. A "
            "filename carrying a newline reaches line-start position and opens a "
            "heading, a code fence or a footnote definition, which "
            "`count_concern_entities` then counts:\n  "
            + "\n  ".join(sorted(offenders)))

    def test_the_matcher_catches_every_spelling(self):
        """The positive control, driving THE SAME helper the sweep drives.

        Four spellings, because the first cut of this gate walked `JoinedStr`
        alone and an audit showed concatenation and `.format` passing clean
        through it. Each case is one line, so the reported line number pins which
        spelling was found rather than only that something was.
        """
        cases = {
            "f-string":      'out.append(f"_source: `{_rel(root, p)}`_")\n',
            "concatenation": 'out.append("_source: `" + _rel(root, p) + "`_")\n',
            "format":        'out.append("_source: `{}`".format(_rel(root, p)))\n',
            "join":          'out.append(", ".join([_rel(root, p), "x"]))\n',
        }
        for label, src in cases.items():
            with self.subTest(spelling=label):
                self.assertEqual(_bare_rel_offenders(src), [1],
                                 f"the {label} spelling is invisible to the "
                                 "matcher, so the sweep would pass over it")

    def test_the_matcher_clears_an_escaped_path(self):
        """The other half of the control: it must not flag the safe form, or the
        sweep above would be unsatisfiable and would be deleted rather than
        obeyed."""
        for src in ('out.append(f"`{_cell(_rel(root, p))}`")\n',
                    'out.append(f"`{_prose_cell(_rel(root, p))}`")\n',
                    'sources[_rel(root, p)] = _sha256_hex(raw)\n'):
            with self.subTest(src=src.strip()):
                self.assertEqual(_bare_rel_offenders(src), [])

    def test_the_universal_decision_index_escapes_both_target_cells(self):
        """`title` and `date` both come from target-repo ADR frontmatter and both
        land in one table row. `title` was escaped and `date` was not, in the
        concern the core binds for EVERY pack. Neither is a `_rel` result, so the
        sweep above cannot see them — this is the named exception, pinned."""
        src = (self.ARCH / "core.py").read_text(encoding="utf-8")
        self.assertIn("title = _prose_cell(title)", src)
        self.assertIn("date = _cell(date)", src)


class DeclaredParserProseCountTests(unittest.TestCase):
    """Every prose count about parser declarations agrees with the code.

    This class exists because the same defect recurred three times in one cycle,
    and each time it was a NUMBER restated from an earlier world instead of
    measured against this one. Two review rounds fixed the sentences and the
    third round found more, because the sentences and the registry had no
    mechanical link. A count in prose is a claim, and an unchecked claim about
    the code is exactly what ADR-0097 exists to stop publishing.

    The gate is deliberately SMALL: it pins ONE denominator (the pack-concern
    pair total) and ONE numerator (the pairs declaring a parser), plus that
    numerator's complement, and nothing else. A wider one — parsing every
    sentence that contains a numeral — would flag release versions, exit codes
    and clause numbers, and would be muted within a week.

    Two adjacent counts stay UNPINNED, named here so a green run is not read as
    covering them: the `artifact` count of three (`core.py` and `staleness.py`)
    and the `refresh` count of two (the tree's schema file). Those stay pack
    review's job, the same honest residual ADR-0097 part 1 names for the
    declarations themselves.
    """

    #: Spelled numbers the prose uses, so "twelve of the twenty" is comparable
    #: to the measured integers. Digits are accepted for the same quantities.
    WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
             "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
             "twelve": 12, "fifteen": 15, "sixteen": 16, "twenty": 20,
             "twenty-four": 24}

    #: Every surface that states one of the pinned counts. A file listed here
    #: and carrying no match fails: a surface that stopped stating the count is
    #: a surface this gate stopped covering, and silence would read as a pass.
    #: Phrasings that mark the numerator as "declares a parser" and its
    #: complement. Kept as data beside the roster so extending them is an edit
    #: here rather than a change to the walk.
    NAMES_A_PARSER = ("name a parser", "name one", "declare a parser",
                      "declaring a parser", "name a grammar")
    NAMES_NONE = ("are in that position", "declare nothing", "declare none",
                  "name no parser", "names no parser")

    #: Surfaces that ship inside the plugin artifact. Read unconditionally.
    SHIPPED_SURFACES = (
        "crux/skills/derive-arch/SKILL.md",
        "crux/scripts/crux/arch/core.py",
    )

    #: The tree's schema file — a DEV-ONLY surface that never crosses the sync
    #: boundary (ADR-0036 §3), so `sync.sh` runs this suite against a staged tree
    #: where it legitimately does not exist. Reading it with a bare `read_text`
    #: raised `FileNotFoundError` there and turned a release gate red on a
    #: correct artifact. `require_dev_surface` skips in the staged tree and still
    #: FAILS in the dev checkout, so the coverage keeps full strength where the
    #: file lives. The tree name is resolved rather than hardcoded, because
    #: hardcoding it is what broke every lock-step suite when the tree moved.
    DEV_ONLY_SURFACE = _dev_surface.TREE_AGENTS_MD

    @staticmethod
    def _flatten(text: str) -> str:
        """Comment markers and line breaks collapsed to single spaces.

        Matching raw text left a claim UNCHECKED whenever it wrapped: `core.py`
        carries "TWELVE of the" and "twenty shipped pack-concern pairs name a
        parser" on two comment lines, so the pattern — which spans the number and
        its denominator — never matched, and the gate reported clean over a
        surface it was not reading. A reflow must not silently un-cover a claim,
        so the text is normalized before the walk rather than the pattern being
        taught about `#:` prefixes.
        """
        stripped = "\n".join(
            re.sub(r"^\s*#:?\s?", "", line) for line in text.splitlines())
        return re.sub(r"\s+", " ", stripped)

    @classmethod
    def _measured(cls):
        pairs = [(p, c, ic)
                 for p in sorted(core.REGISTERED_PACKS)
                 for c, ic in sorted(core.input_classes(p).items())]
        return {
            "total": len(pairs),
            "declaring": sum(1 for _, _, ic in pairs if ic.parser),
        }

    def test_every_stated_pack_concern_pair_count_matches_the_registry(self):
        measured = self._measured()
        pattern = re.compile(
            r"(?P<num>\b[A-Za-z]+(?:-[A-Za-z]+)?\b|\d+)\s+of\s+the\s+"
            r"(?P<den>\b[A-Za-z]+(?:-[A-Za-z]+)?\b|\d+)\s+"
            r"shipped\s+pack-concern\s+pairs", re.IGNORECASE)
        # One hyphen is admitted so a compound number ("twenty-four") reads as
        # one token rather than as its last half.
        denominator = re.compile(
            r"of\s+the\s+(?P<den>\b[A-Za-z]+(?:-[A-Za-z]+)?\b|\d+)\s+"
            r"shipped\s+pack-concern\s+pairs",
            re.IGNORECASE)
        problems, seen_any = [], False
        targets = [(rel, SCRIPTS.parents[1] / rel) for rel in self.SHIPPED_SURFACES]
        require_dev_surface(self, self.DEV_ONLY_SURFACE,
                            f"{_dev_surface.TREE}/AGENTS.md")
        targets.append((f"{_dev_surface.TREE}/AGENTS.md", self.DEV_ONLY_SURFACE))
        for rel, path in targets:
            text = self._flatten(path.read_text(encoding="utf-8"))
            hits = list(denominator.finditer(text))
            if not hits:
                problems.append(f"{rel}: states no pack-concern-pair count — "
                                "this gate silently stopped covering it")
                continue
            for m in hits:
                seen_any = True
                raw = m.group("den")
                value = int(raw) if raw.isdigit() else self.WORDS.get(raw.lower())
                if value != measured["total"]:
                    problems.append(
                        f"{rel}: says {raw!r} shipped pack-concern pairs; the "
                        f"registry has {measured['total']}")
            for m in pattern.finditer(text):
                raw = m.group("num")
                value = int(raw) if raw.isdigit() else self.WORDS.get(raw.lower())
                window = text[m.end():m.end() + 90].lower()
                # Two claims share this denominator and they are COMPLEMENTS, so
                # the gate has to know which one it is reading. Guessing from the
                # word "parser" anywhere nearby is what broke it twice: looking
                # only forward missed "…pairs name one" (the noun is "one", the
                # antecedent sits before the number) and a two-way window then
                # misread "Eight … are in that position", a true statement about
                # the pairs declaring NOTHING, as the count of those declaring
                # one.
                if any(k in window for k in self.NAMES_A_PARSER):
                    expected, claim = measured["declaring"], "name a parser"
                elif any(k in window for k in self.NAMES_NONE):
                    expected = measured["total"] - measured["declaring"]
                    claim = "declare no parser"
                else:
                    # NOT a silent skip. A numerator whose phrasing matches
                    # neither list is unchecked, and an unchecked claim reported
                    # as clean is the exact defect this class exists to catch —
                    # so it is reported and the phrase lists get extended.
                    problems.append(
                        f"{rel} states {raw!r} of the pack-concern pairs but "
                        "names neither "
                        "claim this gate pins; extend NAMES_A_PARSER or "
                        "NAMES_NONE rather than leaving the number unchecked")
                    continue
                if value != expected:
                    problems.append(
                        f"{rel}: says {raw!r} pairs {claim}; the registry has "
                        f"{expected}")
        self.assertEqual(sorted(problems), [], "prose parser counts disagree "
                         "with the registry:\n  " + "\n  ".join(sorted(problems)))
        self.assertTrue(seen_any, "no surface stated the count — the matcher "
                        "found nothing and this assertion proved nothing")

    def test_the_matcher_catches_a_wrong_count(self):
        """The positive control: the pattern really does read the number.

        Without it, a matcher that had silently stopped matching would report
        clean over every surface and read as a guarantee — which is the exact
        false-green shape the project-local guard names.
        """
        pattern = re.compile(
            r"of\s+the\s+(?P<den>\b[A-Za-z]+\b|\d+)\s+shipped\s+pack-concern\s+pairs",
            re.IGNORECASE)
        m = pattern.search("of the fifteen shipped pack-concern pairs, two declare")
        self.assertIsNotNone(m)
        self.assertEqual(self.WORDS[m.group("den").lower()], 15)
        self.assertNotEqual(15, self._measured()["total"],
                            "the stale value this control uses has become the "
                            "true one — pick another")


class SkillProseNamesLiveFieldsTests(unittest.TestCase):
    """`derive-arch/SKILL.md`'s "Paste the table" section names the fields the
    deriver emits, and no field clause 3 deleted.

    The section instructs a model to read `coverage.json` and report what it
    finds, so a field name in it is an instruction rather than description. It
    named `status` and `reason` — the two the same change removed — and told the
    model to report them, which is an instruction that cannot be followed.

    The live set is taken from `Verdict.as_record()` rather than restated, so
    this test moves with the record instead of pinning a copy of it. A retired
    name may still appear, because the section's own job is to say the
    confidence layer is gone — but only inside a sentence that says so.
    """

    SKILL = SCRIPTS.parent / "skills" / "derive-arch" / "SKILL.md"

    #: Coverage fields ADR-0096 clauses 2, 3 and 10 removed. Naming one as a
    #: live field is the defect; naming it as removed is the section's purpose.
    RETIRED = frozenset({"status", "reason", "inputs_missing", "confidence",
                         "confidence_reason", "escalation_offered"})
    _RETIREMENT_WORDS = ("gone", "retired", "removed", "no longer")

    #: The section is located by its heading TEXT, never by its number. A prose
    #: test anchored on `### 3.5` is coupled to the document's structure rather
    #: than to the claim it verifies: renumbering the skill is a restructure,
    #: not a regression in what the section says, and it broke this class as an
    #: opaque `setUpClass` error rather than as "the anchor moved".
    _SECTION_HEADING = re.compile(r"^#{2,3} .*Paste the table.*$", re.MULTILINE)
    #: The section ends at the next heading of level 3 or shallower. Stopping
    #: only at `### ` would run past a trailing subsection into the `## `
    #: sections below, dragging their prose into the region under test.
    _NEXT_HEADING = re.compile(r"^#{1,3} ", re.MULTILINE)

    @classmethod
    def setUpClass(cls):
        text = cls.SKILL.read_text(encoding="utf-8")
        match = cls._SECTION_HEADING.search(text)
        if match is None:
            raise AssertionError(
                f"no heading matching {cls._SECTION_HEADING.pattern!r} in "
                f"{cls.SKILL} — the anchor may simply have moved. This class "
                "reads the section that tells a model which `coverage.json` "
                "fields to report; if that section was renamed or merged, "
                "re-point `_SECTION_HEADING` at its new heading text. Do not "
                "delete the class: the claim it verifies is unchanged.")
        end = cls._NEXT_HEADING.search(text, match.end())
        cls.section = text[match.start():end.start() if end else len(text)]

    @staticmethod
    def _live_fields() -> set:
        populated = core.Verdict.populated(n_sources=1, n_entities=1).as_record()
        stubbed = core.Verdict.stubbed(
            core.StubReason.NO_ENTITIES, expected="x", found="y").as_record()
        return set(populated) | set(stubbed) | {"extractor", "inputs_found", "concern"}

    def test_the_section_names_the_live_verdict_fields(self):
        live = self._live_fields()
        missing = sorted(f for f in live - {"concern"}
                         if f"`{f}`" not in self.section)
        self.assertEqual(
            missing, [],
            f"the skill's \"Paste the table\" section does not name the "
            f"emitted field(s) {missing}")

    def test_no_retired_field_is_named_as_a_live_one(self):
        live = self._live_fields()
        for field in sorted(self.RETIRED):
            if field in live:                     # a name that came back is fine
                continue
            for sentence in self.section.split(". "):
                if f"`{field}`" not in sentence:
                    continue
                with self.subTest(field=field, sentence=sentence[:60]):
                    self.assertTrue(
                        any(w in sentence for w in self._RETIREMENT_WORDS),
                        f"the skill's \"Paste the table\" section names the "
                        f"deleted field `{field}` without saying it is gone — "
                        "a model reading this is told to report a field "
                        "`coverage.json` does not carry",
                    )


class _FakeCfg:
    """Minimal stand-in for a BionicConfig — only the attributes the seam reads."""

    def __init__(self, arch_stack=None, arch_extractors=None, source=".bionic.yml"):
        self.arch_stack = arch_stack
        self.arch_extractors = arch_extractors or {}
        self.source = source


class _FakePackCase(unittest.TestCase):
    """Registers a synthetic pack module as `_FAKE_NAME`, restored on teardown.
    The pack seams are exercised by stubbing a pack module through
    `_pack_module` rather than shipping a real one, so no real pack is touched."""

    _FAKE_NAME = "fakepack"

    def _install_fake_pack(self, module: types.SimpleNamespace):
        real_pack_module = core._pack_module

        def fake_pack_module(name):
            if name == self._FAKE_NAME:
                return module
            return real_pack_module(name)

        patches = [
            unittest.mock.patch.object(core, "_pack_module", fake_pack_module),
            unittest.mock.patch.object(
                core, "REGISTERED_PACKS", core.REGISTERED_PACKS | {self._FAKE_NAME}
            ),
            unittest.mock.patch.object(
                core, "PACK_NAMES", core.PACK_NAMES + (self._FAKE_NAME,)
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def _bare_module(**kw) -> types.SimpleNamespace:
        base = dict(probes=lambda: {})
        base.update(kw)
        return types.SimpleNamespace(**base)


class EntityNounHookTests(_FakePackCase):
    """`_entity_noun` prefers a pack's own `ENTITY_NOUNS` declaration and
    falls back to the core default `_ENTITY_NOUN` table otherwise."""

    def test_a_declared_noun_overrides_the_core_default(self):
        module = self._bare_module(ENTITY_NOUNS={"api-surface": "interface row"})
        self._install_fake_pack(module)
        self.assertEqual(
            core._entity_noun(self._FAKE_NAME, "api-surface"), "interface row")

    def test_a_pack_with_no_hook_keeps_the_core_default(self):
        module = self._bare_module()
        self._install_fake_pack(module)
        self.assertEqual(
            core._entity_noun(self._FAKE_NAME, "api-surface"),
            core._ENTITY_NOUN["api-surface"])

    def test_a_concern_the_pack_does_not_override_keeps_the_core_default(self):
        module = self._bare_module(ENTITY_NOUNS={"api-surface": "interface row"})
        self._install_fake_pack(module)
        self.assertEqual(
            core._entity_noun(self._FAKE_NAME, "data-model"),
            core._ENTITY_NOUN["data-model"])

    def test_an_unregistered_pack_name_always_gets_the_core_default(self):
        self.assertEqual(
            core._entity_noun("no-such-pack", "api-surface"),
            core._ENTITY_NOUN["api-surface"])

    def test_the_declared_noun_appears_in_a_no_entities_stub_line(self):
        module = self._bare_module(ENTITY_NOUNS={"api-surface": "interface row"})
        self._install_fake_pack(module)
        verdict = core.concern_verdict(
            "api-surface", self._FAKE_NAME, "probe",
            "# API surface\n\nprose, no recognized table\n",
            {"Foo.swift": "deadbeef"},
        )
        self.assertEqual(verdict.kind, "stubbed")
        self.assertEqual(verdict.reason, core.StubReason.NO_ENTITIES)
        line = core.render_stub_line(verdict)
        self.assertIn("interface row", line)

    def test_a_pack_without_the_hook_still_renders_the_core_default_noun(self):
        module = self._bare_module()
        self._install_fake_pack(module)
        verdict = core.concern_verdict(
            "api-surface", self._FAKE_NAME, "probe",
            "# API surface\n\nprose, no recognized table\n",
            {"Foo.rb": "deadbeef"},
        )
        line = core.render_stub_line(verdict)
        self.assertIn(core._ENTITY_NOUN["api-surface"], line)


class DecodeVerdictHookTests(_FakePackCase):
    """`_concern_decode_verdict` dispatches to a pack's own
    `concern_decode_verdict(concern, content, sources)` hook, validating its
    return under the ValueError guard the hook contract requires."""

    def test_no_hook_at_all_returns_none(self):
        module = self._bare_module()
        self._install_fake_pack(module)
        result = core._concern_decode_verdict(
            self._FAKE_NAME, "data-model", "content", {})
        self.assertIsNone(result)

    def test_a_none_return_changes_nothing(self):
        module = self._bare_module(
            concern_decode_verdict=lambda concern, content, sources: None)
        self._install_fake_pack(module)
        result = core._concern_decode_verdict(
            self._FAKE_NAME, "data-model", "content", {})
        self.assertIsNone(result)

    def test_a_parse_failed_stub_is_returned_as_is(self):
        decoded = core.Verdict.stubbed(
            core.StubReason.PARSE_FAILED, expected="a clean parse", found="an error")
        module = self._bare_module(
            concern_decode_verdict=lambda concern, content, sources: decoded)
        self._install_fake_pack(module)
        result = core._concern_decode_verdict(
            self._FAKE_NAME, "data-model", "content", {})
        self.assertIs(result, decoded)

    def test_a_populated_verdict_raises_value_error(self):
        populated = core.Verdict.populated(1, 1)
        module = self._bare_module(
            concern_decode_verdict=lambda concern, content, sources: populated)
        self._install_fake_pack(module)
        with self.assertRaises(ValueError):
            core._concern_decode_verdict(self._FAKE_NAME, "data-model", "content", {})

    def test_a_different_stub_reason_raises_value_error(self):
        other = core.Verdict.stubbed(
            core.StubReason.NO_ENTITIES, expected="x", found="y")
        module = self._bare_module(
            concern_decode_verdict=lambda concern, content, sources: other)
        self._install_fake_pack(module)
        with self.assertRaises(ValueError):
            core._concern_decode_verdict(self._FAKE_NAME, "data-model", "content", {})

    def test_the_hook_receives_the_concern_content_and_sources(self):
        seen = {}

        def hook(concern, content, sources):
            seen["concern"] = concern
            seen["content"] = content
            seen["sources"] = sources
            return None

        module = self._bare_module(concern_decode_verdict=hook)
        self._install_fake_pack(module)
        core._concern_decode_verdict(
            self._FAKE_NAME, "data-model", "the-content", {"a": "b"})
        self.assertEqual(
            seen, {"concern": "data-model", "content": "the-content",
                   "sources": {"a": "b"}})


class DecodeVerdictBuildIntegrationTests(_FakePackCase):
    """`_build` calls the decode hook only over an already-stubbed concern, and
    never over a concern `concern_verdict` already called `populated`."""

    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.root = Path(self._d.name)

    def test_a_parse_failed_decode_replaces_a_stubbed_verdict(self):
        decoded = core.Verdict.stubbed(
            core.StubReason.PARSE_FAILED, expected="a clean parse",
            found="a syntax error")
        module = self._bare_module(
            concern_decode_verdict=lambda concern, content, sources: (
                decoded if concern == "data-model" else None))
        self._install_fake_pack(module)
        cfg = _FakeCfg(arch_stack=self._FAKE_NAME)
        tree = core._build(self.root, "bionic", "complete", cfg)
        cov = {r["concern"]: r
               for r in json.loads(tree["_meta/coverage.json"])["concerns"]}
        self.assertEqual(cov["data-model"]["stub_reason"], "parse_failed")

    def test_the_hook_is_not_called_for_a_populated_concern(self):
        calls = []

        def probes():
            def extract(root, docs_dir):
                md = "# Data model\n\n| field | type |\n|---|---|\n| x | int |\n"
                return md, {"a.py": "deadbeef"}
            return {"data-model": [core.Probe(core._always, extract, kind="parser")]}

        def hook(concern, content, sources):
            calls.append(concern)
            return None

        module = self._bare_module(probes=probes, concern_decode_verdict=hook)
        self._install_fake_pack(module)
        cfg = _FakeCfg(arch_stack=self._FAKE_NAME)
        tree = core._build(self.root, "bionic", "complete", cfg)
        cov = {r["concern"]: r
               for r in json.loads(tree["_meta/coverage.json"])["concerns"]}
        self.assertEqual(cov["data-model"]["verdict"], "populated")
        self.assertNotIn("data-model", calls)


class VerifyParserLoadHookTests(_FakePackCase):
    """`resolve_declared_parsers` calls a registered pack's
    `verify_parser_load()` once, after every declared parser's `find_spec`
    check passes, and ANY exception becomes `ParserUnavailable` naming the pack
    and the exception type — raised before any byte is written or compared."""

    def test_a_raising_hook_becomes_parser_unavailable(self):
        def raising_hook():
            raise RuntimeError("grammar load failed")
        module = self._bare_module(verify_parser_load=raising_hook)
        self._install_fake_pack(module)
        with self.assertRaises(core.ParserUnavailable) as caught:
            core.resolve_declared_parsers(self._FAKE_NAME)
        self.assertIn(self._FAKE_NAME, str(caught.exception))
        self.assertIn("RuntimeError", str(caught.exception))

    def test_a_clean_hook_does_not_raise(self):
        module = self._bare_module(verify_parser_load=lambda: None)
        self._install_fake_pack(module)
        core.resolve_declared_parsers(self._FAKE_NAME)          # must not raise

    def test_a_pack_without_the_hook_resolves_exactly_as_today(self):
        module = self._bare_module()
        self._install_fake_pack(module)
        core.resolve_declared_parsers(self._FAKE_NAME)          # must not raise

    def test_the_hook_raising_leaves_nothing_written(self):
        def raising_hook():
            raise RuntimeError("grammar load failed")
        module = self._bare_module(verify_parser_load=raising_hook)
        self._install_fake_pack(module)
        cfg = _FakeCfg(arch_stack=self._FAKE_NAME)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(core.ParserUnavailable):
                core.derive(root, "bionic", "complete", cfg)
            self.assertFalse(
                (root / "bionic" / "arch").exists(),
                "the arch tree was written before the hook's refusal was raised")


def _all_table_header_pairs(markdown: str) -> set:
    """Every markdown table's first-two-header-cells pair in `markdown`, same
    detection `_entity_table_rows` uses but over EVERY table, not one concern's
    registered set — the scan `HeaderRegistryDisciplineTests` needs to answer
    "does anything already emit a Swift pair"."""
    lines = markdown.split("\n")
    pairs = set()
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not (s.startswith("|") and s.endswith("|")):
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if core._TABLE_SEP_RE.match(nxt):
            pairs.add(core._header_cells(s)[:2])
    return pairs


class HeaderRegistryDisciplineTests(unittest.TestCase):
    """The Swift header pairs are registered exactly once each, appear in
    no existing golden or pack-module source today, and the deliberately
    UNCOUNTED Swift pairs sit in no concern's registered set."""

    SWIFT_PAIRS = {
        ("type", "kind"),
        ("interface", "kind"),
        ("requirement", "protocol"),
        ("product", "product kind"),
        ("@main type", "kind"),
    }

    UNCOUNTED_PAIRS = {
        "data-model": {("owner", "property"), ("from type", "relation")},
        "module-graph": {
            ("container", "container kind"),
            ("target", "container"),
            ("from", "dependency"),
            ("file", "module"),
            ("file", "target"),
        },
    }

    def test_each_registered_pair_belongs_to_exactly_one_concern(self):
        owner = {}
        for concern, pairs in core._ENTITY_TABLE_HEADERS.items():
            for pair in pairs:
                self.assertNotIn(
                    pair, owner,
                    f"{pair} is registered under both {owner.get(pair)!r} and "
                    f"{concern!r}")
                owner[pair] = concern

    def test_the_swift_pairs_are_registered(self):
        all_registered = set().union(*core._ENTITY_TABLE_HEADERS.values())
        for pair in self.SWIFT_PAIRS:
            with self.subTest(pair=pair):
                self.assertIn(pair, all_registered)

    @staticmethod
    def _swift_entry_names() -> frozenset:
        """The corpus entries whose pack is `swift`, read from `corpus.yml`.

        Their goldens are the Swift pack's own output and are MEANT to carry
        the Swift pairs; the check below is about every other entry. Read
        with a line pattern so this test needs no YAML parser."""
        text = (GOLDEN.parent / "corpus.yml").read_text(encoding="utf-8")
        return frozenset(re.findall(
            r"^  - name: ([a-z0-9-]+)\n    pack: swift$", text, re.MULTILINE))

    def test_the_swift_golden_carries_the_swift_pairs(self):
        """Positive control for the absence check below: the header-pair
        reader finds every registered Swift pair in the Swift entry's own
        golden, so an empty result below is a measurement, not a blind spot."""
        names = self._swift_entry_names()
        self.assertTrue(names, "corpus.yml names no swift entry")
        for name in sorted(names):
            seen = set()
            for md in sorted((GOLDEN / name).glob("*.md")):
                seen |= _all_table_header_pairs(md.read_text(encoding="utf-8"))
            with self.subTest(entry=name):
                self.assertEqual(self.SWIFT_PAIRS - seen, set())

    def test_no_existing_golden_or_pack_source_emits_a_swift_pair(self):
        found = []
        swift_entries = self._swift_entry_names()
        for base in (GOLDEN, GOLDEN.parent / "golden-by-python"):
            if not base.exists():
                continue
            for md in sorted(base.rglob("*.md")):
                if swift_entries & set(md.relative_to(base).parts[:-1]):
                    continue
                hit = _all_table_header_pairs(md.read_text(encoding="utf-8")) & self.SWIFT_PAIRS
                if hit:
                    found.append((str(md), sorted(hit)))
        packs_dir = SCRIPTS / "crux" / "arch" / "packs"
        for name in ("crux", "python", "ruby", "node", "elixir"):
            text = (packs_dir / f"{name}.py").read_text(encoding="utf-8")
            hit = _all_table_header_pairs(text) & self.SWIFT_PAIRS
            if hit:
                found.append((name, sorted(hit)))
        self.assertEqual(found, [], f"a Swift header pair already appears: {found}")

    def test_the_uncounted_swift_pairs_are_registered_nowhere(self):
        all_registered = set().union(*core._ENTITY_TABLE_HEADERS.values())
        for concern, pairs in self.UNCOUNTED_PAIRS.items():
            for pair in pairs:
                with self.subTest(concern=concern, pair=pair):
                    self.assertNotIn(pair, all_registered)

    def test_positive_control_the_detector_catches_a_planted_pair(self):
        """The check turns red when a Swift pair is planted — proving the
        header-pair scan over golden/source text is not vacuous."""
        synthetic = "| type | kind | access |\n|---|---|---|\n| Foo | class | public |\n"
        hit = _all_table_header_pairs(synthetic) & self.SWIFT_PAIRS
        self.assertTrue(hit, "the detector missed a deliberately planted Swift pair")


class SwiftEntityCountingTests(unittest.TestCase):
    """`count_concern_entities` counts a Swift-shaped data-model table by its
    rows, ignoring the property and relationship tables beside it, and counts
    every one of api-surface's four registered Swift tables."""

    def test_counts_the_type_table_rows_and_ignores_property_and_relation_tables(self):
        md = (
            "# Data model\n\n"
            "## Types\n\n"
            "| type | kind | access | declared at | conditional |\n"
            "|---|---|---|---|---|\n"
            "| Foo | class | public | Foo.swift:1 | no |\n"
            "| Bar | struct | internal | Bar.swift:5 | no |\n\n"
            "## Properties\n\n"
            "| owner | property | type | access | declared at |\n"
            "|---|---|---|---|---|\n"
            "| Foo | x | Int | public | Foo.swift:2 |\n\n"
            "## Relationships\n\n"
            "| from type | relation | to type |\n"
            "|---|---|---|\n"
            "| Foo | conforms | Codable |\n"
        )
        self.assertEqual(core.count_concern_entities("data-model", md), 2)

    def test_counts_all_four_swift_api_surface_tables(self):
        md = (
            "# API surface\n\n"
            "| interface | kind | access | declared at |\n"
            "|---|---|---|---|\n"
            "| Foo | class | public | Foo.swift:1 |\n\n"
            "| requirement | protocol | declared at |\n"
            "|---|---|---|\n"
            "| bar() | Bazable | Baz.swift:3 |\n\n"
            "| product | product kind | declared at |\n"
            "|---|---|---|\n"
            "| MyApp | app | Package.swift:1 |\n\n"
            "| @main type | kind | declared at |\n"
            "|---|---|---|\n"
            "| AppMain | struct | main.swift:1 |\n"
        )
        self.assertEqual(core.count_concern_entities("api-surface", md), 4)


if __name__ == "__main__":
    unittest.main()
