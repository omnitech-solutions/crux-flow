"""ADR-0096 clause 5 — the strict gate reads the RECORDED verdict.

A tree names required concerns under `manifest.yml`'s `arch.require`. A required
concern whose recorded verdict is not `populated` fails the derive: exit 1, with
the verdict on stdout inside the envelope `derive-arch.py` already emits.
`--strict` applies the same rule to every concern for one run.

Four properties carry the clause, and each has tests here.

**Where the key is read.** The `arch.require` read lives in `derive-arch.py`,
never in `_build`. That is load-bearing rather than stylistic: `_build` records
every file it reads into the provenance ledger, so a `_build` that read
`manifest.yml` would put a governance-config file into `sources` and let an edit
to the required set flap the drift gate. Clause 2's "every recorded verdict is a
function of committed bytes alone" is about the ARCH sources; the required set is
not one of them. Two tests hold the line — one structural over the module
sources, one behavioural over the built tree.

**No new exit code.** Exit 1 already means findings and exit 2 already means no
verdict, so a strict failure stays distinguishable from an environment failure
without minting a third code. `test_the_exit_code_set_is_exactly_zero_one_two`
proves the driver's whole lane set by AST rather than by reading the docstring.

**Exit 2 is the environment lane, and it writes nothing.** A declared parser that
cannot be resolved on this machine is not a stub reason — a verdict that varied
with the machine would drift the byte-compared gate across environments. It
exits 2 with no tree written and no `coverage.json` record at all.

**An annotation never fails strict.** `possibly_stale` (clause 7) is the reported
channel's only member, and strict must not depend on the commit graph. The
contract is tested here against `strict_failures` directly, which is where the
gate decides.

Every test builds the tree it examines, per the reason `test_arch_projections.py`
states: these assertions must hold in the staged public artifact, which ships
`crux/` without the `bionic/` dogfood tree.
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

CORE = importlib.import_module("crux.arch.core")
D = importlib.import_module("crux.arch.derive")

DRIVER = SCRIPTS / "derive-arch.py"


def _load_driver():
    """Import `derive-arch.py` as a module. Its name carries a dash, so the
    ordinary import statement cannot reach it and a spec load is the only way in.
    """
    spec = importlib.util.spec_from_file_location("_test_derive_arch_cli", DRIVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CLI = _load_driver()


def _skill(root: Path, name: str, description: str) -> None:
    d = root / "crux" / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    d.joinpath("SKILL.md").write_text(
        f"---\nname: {name}\ndescription: \"{description}\"\n"
        "metadata:\n"
        "  tags: \"a, b\"\n"
        "  bundles: \"crux-core\"\n"
        "  risk_level: \"low\"\n"
        f"---\n\n# {name}\n",
        encoding="utf-8",
    )


def _adr(root: Path, num: int, title: str, status: str) -> None:
    d = root / "bionic" / "adrs"
    d.mkdir(parents=True, exist_ok=True)
    d.joinpath(f"ADR-{num:04d}-fixture.md").write_text(
        f"---\nid: ADR-{num:04d}\ntitle: \"{title}\"\nstatus: {status}\n"
        f"date: 2026-01-0{num % 9 + 1}\ntags: [fixture]\n---\n\n"
        f"# ADR-{num:04d} — {title}\n",
        encoding="utf-8",
    )


class _Tree(unittest.TestCase):
    """A tree the crux pack resolves, with three concerns populated and one not.

    `data-model` reads the schema, `api-surface` reads the skills and
    `decision-index` reads the ADRs, so all three carry entities of their own
    kind. There is no `crux/scripts/crux/` package, so `module-graph` stubs
    `precondition_missing` — which is what makes one fixture serve both the
    passing and the failing side of the gate.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "crux" / "schemas").mkdir(parents=True)
        (self.root / "crux" / "schemas" / "w.schema.json").write_text(
            '{\n  "title": "W",\n  "type": "object",\n'
            '  "properties": {"id": {"type": "string"}},\n  "required": ["id"]\n}\n',
            encoding="utf-8",
        )
        _skill(self.root, "zeta-skill", "Does the zeta thing.")
        _adr(self.root, 1, "Accept a thing", "Accepted")
        (self.root / ".bionic.yml").write_text(
            'config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8")
        self.write_manifest()

    def tearDown(self):
        self._tmp.cleanup()

    def write_manifest(self, extra: str = "") -> None:
        (self.root / "bionic").mkdir(parents=True, exist_ok=True)
        (self.root / "bionic" / "manifest.yml").write_text(
            'schema_version: "5"\nadr:\n  next_number: 9\n' + extra, encoding="utf-8")

    def run_cli(self, *argv: str) -> tuple[int, dict, str]:
        """Run the driver in process. Returns (exit code, stdout JSON, stderr).

        In process rather than through a subprocess because two of these tests
        monkeypatch the deriver, and a subprocess would not see the patch.
        stdout is the envelope, so it is parsed; a lane that writes no JSON
        returns an empty dict.
        """
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = CLI.main(["--repo-root", str(self.root), *argv])
        text = out.getvalue()
        try:
            payload = json.loads(text) if text.strip() else {}
        except ValueError:
            payload = {}
        return code, payload, err.getvalue()

    def verdicts(self) -> dict[str, str]:
        report: list = []
        D._build(self.root, "bionic", "complete", None, report=report)
        return {r["concern"]: r["verdict"] for r in CORE.reported_coverage(report)}


# ─────────────────────── the fixture says what it is ────────────────────────


class FixtureShapeTests(_Tree):

    def test_the_fixture_populates_three_concerns_and_stubs_module_graph(self):
        """Guard the guard: every gate assertion below reads this shape, so a
        fixture that silently stopped populating would make them vacuous."""
        self.assertEqual(
            self.verdicts(),
            {"data-model": "populated", "api-surface": "populated",
             "module-graph": "stubbed", "decision-index": "populated"},
        )


# ───────────────────────── `arch.require`, observed ─────────────────────────


class RequiredConcernTests(_Tree):

    def test_a_required_populated_concern_passes(self):
        self.write_manifest("arch:\n  require: [data-model, decision-index]\n")
        code, payload, _ = self.run_cli()
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["strict_failures"], [])

    def test_a_required_non_populated_concern_exits_1_with_its_verdict(self):
        self.write_manifest("arch:\n  require: [module-graph]\n")
        code, payload, _ = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(len(payload["strict_failures"]), 1)
        failure = payload["strict_failures"][0]
        self.assertEqual(failure["concern"], "module-graph")
        self.assertEqual(failure["verdict"], "stubbed")
        self.assertEqual(failure["stub_reason"], "precondition_missing")
        self.assertEqual(failure["required_by"], "arch.require")
        # The verdict, not a restatement of it: expected/found ride along.
        self.assertTrue(failure["expected"])
        self.assertTrue(failure["found"])

    def test_the_failure_rides_the_existing_envelope(self):
        """Additive: no existing key is removed or renamed, so
        `.github/workflows/check-arch-drift.yml` keeps parsing what it parses."""
        self.write_manifest("arch:\n  require: [module-graph]\n")
        _, payload, _ = self.run_cli("--dry-run")
        self.assertIn("drift", payload)
        self.assertIn("clean", payload)
        self.assertIn("coverage", payload)
        self.assertIn("strict_failures", payload)

    def test_a_strict_failure_on_a_clean_tree_still_reports_clean_drift(self):
        """`clean` answers the drift question and only that one. A strict
        failure exits 1 through a different key, so a reader cannot be told the
        tree is stale by a gate that found it byte-identical."""
        self.write_manifest("arch:\n  require: [module-graph]\n")
        self.assertEqual(self.run_cli()[0], 1)          # writes the tree
        code, payload, _ = self.run_cli("--dry-run")
        self.assertEqual(code, 1)
        self.assertEqual(payload["drift"], [])
        self.assertTrue(payload["clean"])
        self.assertEqual([f["concern"] for f in payload["strict_failures"]],
                         ["module-graph"])

    def test_an_absent_require_key_gates_nothing(self):
        code, payload, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(payload["strict_failures"], [])

    def test_an_unknown_concern_name_is_an_exit_2_config_error(self):
        """Fail closed on a required set naming something no concern answers —
        silently gating nothing would be the worst of the three options."""
        self.write_manifest("arch:\n  require: [module-graph, telemetry]\n")
        code, payload, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertEqual(payload, {})
        self.assertIn("telemetry", err)

    def test_a_mis_shaped_require_value_is_an_exit_2_config_error(self):
        self.write_manifest("arch:\n  require: module-graph\n")
        code, _, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("arch.require", err)


# ─────────────────────────────── `--strict` ─────────────────────────────────


class StrictFlagTests(_Tree):

    def test_strict_applies_the_rule_to_every_concern(self):
        code, payload, _ = self.run_cli("--strict")
        self.assertEqual(code, 1)
        self.assertEqual([f["concern"] for f in payload["strict_failures"]],
                         ["module-graph"])
        self.assertEqual(payload["strict_failures"][0]["required_by"], "--strict")

    def test_strict_on_a_fully_populated_tree_passes(self):
        (self.root / "crux" / "scripts" / "crux" / "sub").mkdir(parents=True)
        (self.root / "crux" / "scripts" / "crux" / "__init__.py").write_text(
            "from . import sub\n", encoding="utf-8")
        (self.root / "crux" / "scripts" / "crux" / "sub").joinpath(
            "__init__.py").write_text("from .. import mod\n", encoding="utf-8")
        (self.root / "crux" / "scripts" / "crux" / "mod.py").write_text(
            "x = 1\n", encoding="utf-8")
        self.assertEqual(self.verdicts()["module-graph"], "populated",
                         "fixture did not produce a module graph — test is vacuous")
        code, payload, _ = self.run_cli("--strict")
        self.assertEqual(code, 0, payload)

    def test_strict_and_require_union_without_duplicating_a_concern(self):
        self.write_manifest("arch:\n  require: [module-graph]\n")
        _, payload, _ = self.run_cli("--strict")
        self.assertEqual([f["concern"] for f in payload["strict_failures"]],
                         ["module-graph"])


# ──────────────────── the read lives in the CLI, not `_build` ───────────────


class WhereTheKeyIsReadTests(_Tree):

    def test_the_require_key_is_named_only_by_the_driver(self):
        """Structural. `_build` records what it reads into the provenance
        ledger, so a `_build` that read the required set would put a governance
        file into `sources` and let an edit to it flap the drift gate."""
        driver = DRIVER.read_text(encoding="utf-8")
        self.assertIn("REQUIRE_KEY", driver)
        self.assertIn("def required_concerns", driver)
        arch = SCRIPTS / "crux" / "arch"
        modules = [arch / "core.py", arch / "derive.py",
                   *sorted((arch / "packs").glob("*.py"))]
        self.assertGreaterEqual(len(modules), 7, "derive-path module set looks truncated")
        offenders: list[str] = []
        for m in modules:
            src = m.read_text(encoding="utf-8")
            if "required_concerns" in src:
                offenders.append(f"{m.name}: names the reader")
            # The read itself, by AST rather than by grep: any `.get("require")`
            # or `["require"]` on the derive path IS the read this clause moves
            # out. The attribution STRING `"arch.require"` is not — `core.py`
            # writes it into a failure record so the reader knows which remedy
            # applies, and naming a key is not reading a file.
            for node in ast.walk(ast.parse(src)):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "get" and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value in ("require", "arch")):
                    offenders.append(f"{m.name}: reads .get({node.args[0].value!r})")
                if (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
                        and node.slice.value in ("require", "arch")):
                    offenders.append(f"{m.name}: reads [{node.slice.value!r}]")
        self.assertEqual(offenders, [], "the derive path reads the required set")

    def test_the_build_signature_is_frozen(self):
        """`_build` gained neither parameter, which is the mechanical proof it
        cannot be reading the required set. Its positional shape is bound by
        `test_arch_corpus.py`'s `_StubDerive` and by `test_schema_invariants.py`,
        so it may only ever widen with keyword-only parameters."""
        import inspect
        params = inspect.signature(CORE._build).parameters
        positional = [n for n, p in params.items()
                      if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD]
        kwonly = {n for n, p in params.items()
                  if p.kind is inspect.Parameter.KEYWORD_ONLY}
        self.assertEqual(positional, ["root", "docs_dir", "decision_index_mode", "cfg"])
        self.assertEqual(kwonly, {"report"})

    def test_derive_and_dry_run_gained_keyword_only_require_and_strict(self):
        import inspect
        for fn in (CORE.derive, CORE.dry_run):
            with self.subTest(fn=fn.__name__):
                params = inspect.signature(fn).parameters
                for name, default in (("require", ()), ("strict", False),
                                      ("failures", None)):
                    self.assertIn(name, params)
                    self.assertIs(params[name].kind, inspect.Parameter.KEYWORD_ONLY)
                    self.assertEqual(params[name].default, default)

    def test_a_gate_asked_for_with_nowhere_to_report_is_refused(self):
        """Misuse is loud. A caller that sets `strict` without an out-parameter
        would otherwise get a silently ungated derive."""
        with self.assertRaises(ValueError):
            CORE.derive(self.root, "bionic", strict=True)
        with self.assertRaises(ValueError):
            CORE.dry_run(self.root, "bionic", require=("module-graph",))


# ─────────────────────────── the exit-code contract ─────────────────────────


class ExitCodeContractTests(unittest.TestCase):

    def test_the_exit_code_set_is_exactly_zero_one_two(self):
        """No new code is minted. Proved by AST over every lane the driver can
        leave through, rather than by reading its docstring."""
        tree = ast.parse(DRIVER.read_text(encoding="utf-8"))
        codes: set = set()

        def collect(expr) -> None:
            """Every integer literal this expression can evaluate to.

            `ast.IfExp` is unwrapped into both arms rather than counted as
            opaque: `return 1 if findings else 0` names its whole range in
            literals, and refusing to read it would make the gate unwritable in
            the one shape the driver actually uses.
            """
            if isinstance(expr, ast.IfExp):
                collect(expr.body)
                collect(expr.orelse)
            elif isinstance(expr, ast.Constant) and isinstance(expr.value, int):
                codes.add(expr.value)
            else:
                codes.add("<computed>")

        # Only `main` decides an exit code; the helpers beside it return values.
        mains = [n for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name == "main"]
        self.assertEqual(len(mains), 1, "the driver has no single `main`")
        for node in ast.walk(mains[0]):
            if isinstance(node, ast.Return) and node.value is not None:
                collect(node.value)
        # The module-level `raise SystemExit(main())` passes `main`'s set
        # through, so only a LITERAL argument would add a lane; there is none.
        for node in ast.walk(tree):
            if (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                    and isinstance(node.exc.func, ast.Name)
                    and node.exc.func.id == "SystemExit"):
                for arg in node.exc.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, int):
                        codes.add(arg.value)
        self.assertNotIn("<computed>", codes,
                         "a computed return hides the driver's exit-code set")
        self.assertEqual(codes, {0, 1, 2})


# ───────────────── exit 2: the environment lane writes nothing ──────────────


class UnresolvableParserTests(_Tree):
    """Clause 2's negative space. A declared parser that cannot be resolved on
    this machine is an environment failure, never a stub reason."""

    def _unresolvable(self):
        real = CORE.input_classes

        def patched(pack_name: str):
            declared = dict(real(pack_name))
            declared["module-graph"] = declared["module-graph"]._replace(
                kind="parser", parser=("a_parser_no_machine_has",))
            return declared

        return mock.patch.object(CORE, "input_classes", patched)

    def test_it_exits_2_writes_nothing_and_records_no_coverage(self):
        with self._unresolvable():
            code, payload, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertEqual(payload, {}, "the environment lane emitted a verdict")
        self.assertIn("a_parser_no_machine_has", err)
        arch = self.root / "bionic" / "arch"
        self.assertFalse(arch.exists(), "the environment lane wrote a tree")
        self.assertFalse((arch / "_meta" / "coverage.json").exists())

    def test_it_leaves_an_existing_tree_untouched(self):
        self.assertEqual(self.run_cli()[0], 0)
        arch = self.root / "bionic" / "arch"
        before = {p: p.read_bytes() for p in arch.rglob("*") if p.is_file()}
        with self._unresolvable():
            self.assertEqual(self.run_cli()[0], 2)
        after = {p: p.read_bytes() for p in arch.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_no_stub_reason_names_an_environment_condition(self):
        reasons = {r.value for r in CORE.StubReason}
        for word in ("parser_unavailable", "environment", "missing_dependency",
                     "import_failed", "unresolvable"):
            self.assertNotIn(word, reasons)

    def test_a_declared_parser_that_resolves_is_silent(self):
        """The positive control for the three refusals above.

        `yaml` rather than a stdlib module: ADR-0096 clause 1 requires each
        declared parser's version pin recorded in the provenance manifest, and
        a stdlib module has no distribution version to record, so it now takes
        the same exit-2 lane an unresolvable one does. A named `parser` entry
        means a pinned third-party grammar — the `InputClass.parser` docstring
        says an empty tuple is how a pack declares a stdlib reader — so the
        stand-in has to be one, or this control tests a shape no pack can
        declare.
        """
        real = CORE.input_classes

        def patched(pack_name: str):
            declared = dict(real(pack_name))
            declared["module-graph"] = declared["module-graph"]._replace(
                kind="parser", parser=("yaml",))
            return declared

        with mock.patch.object(CORE, "input_classes", patched):
            self.assertEqual(self.run_cli()[0], 0)

    def test_a_resolvable_parser_with_no_version_is_also_the_exit_2_lane(self):
        """Clause 1's pin cannot be recorded for a module with no distribution
        version, and clause 2 says a manifest whose contents varied with the
        machine would drift the byte-compared gate across environments. So an
        unpinnable parser refuses in the same lane rather than writing a tree
        with the pin quietly absent."""
        real = CORE.input_classes

        def patched(pack_name: str):
            declared = dict(real(pack_name))
            declared["module-graph"] = declared["module-graph"]._replace(
                kind="parser", parser=("json",))          # stdlib: resolves, no version
            return declared

        with mock.patch.object(CORE, "input_classes", patched):
            code, payload, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertEqual(payload, {}, "the environment lane emitted a verdict")
        self.assertIn("json", err)


# ──────────────────── an annotation never fails strict ──────────────────────


class AnnotationNeverFailsStrictTests(unittest.TestCase):
    """Clause 5's last sentence, written against the channel contract before
    clause 7's annotation exists. Strict must not depend on the commit graph."""

    POPULATED = {"concern": "api-surface", "verdict": "populated",
                 "n_sources": 3, "n_entities": 7, "annotations": ["possibly_stale"]}
    STUBBED = {"concern": "module-graph", "verdict": "stubbed",
               "stub_reason": "precondition_missing", "expected": "a package",
               "found": "no matching input", "annotations": ["possibly_stale"]}

    def test_a_populated_concern_carrying_the_annotation_passes_strict(self):
        self.assertEqual(CORE.strict_failures([self.POPULATED], strict=True), [])

    def test_a_populated_concern_carrying_the_annotation_passes_require(self):
        self.assertEqual(
            CORE.strict_failures([self.POPULATED], require=("api-surface",)), [])

    def test_a_stubbed_concern_fails_for_its_reason_and_not_the_annotation(self):
        failures = CORE.strict_failures([self.STUBBED], strict=True)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["stub_reason"], "precondition_missing")
        self.assertNotIn("possibly_stale", json.dumps(failures[0]["found"]))

    def test_the_gate_reads_the_verdict_key_and_no_annotation_key(self):
        """Every annotation in the vocabulary, present on a populated record,
        leaves strict silent — so a member added later inherits the contract."""
        for annotation in CORE.ANNOTATIONS:
            with self.subTest(annotation=annotation):
                rec = dict(self.POPULATED, annotations=[annotation])
                self.assertEqual(CORE.strict_failures([rec], strict=True), [])


# ───────────────────────── the pure gate function ───────────────────────────


class StrictFailuresTests(unittest.TestCase):

    STUB = {"concern": "module-graph", "verdict": "stubbed",
            "stub_reason": "no_entities", "expected": "at least one graph edge",
            "found": "2 input(s) consumed and 0 graph edges"}
    OK = {"concern": "data-model", "verdict": "populated",
          "n_sources": 2, "n_entities": 5}

    def test_no_require_and_no_strict_gates_nothing(self):
        self.assertEqual(CORE.strict_failures([self.STUB, self.OK]), [])

    def test_require_gates_only_the_named_concerns(self):
        self.assertEqual(
            CORE.strict_failures([self.STUB, self.OK], require=("data-model",)), [])
        self.assertEqual(
            [f["concern"] for f in
             CORE.strict_failures([self.STUB, self.OK], require=("module-graph",))],
            ["module-graph"])

    def test_a_concern_named_by_both_is_reported_once(self):
        failures = CORE.strict_failures([self.STUB], require=("module-graph",), strict=True)
        self.assertEqual(len(failures), 1)

    def test_the_named_side_wins_when_both_apply(self):
        failures = CORE.strict_failures([self.STUB], require=("module-graph",), strict=True)
        self.assertEqual(failures[0]["required_by"], "arch.require")

    def test_failures_keep_the_records_order(self):
        records = [self.OK, self.STUB, dict(self.STUB, concern="api-surface")]
        self.assertEqual([f["concern"] for f in CORE.strict_failures(records, strict=True)],
                         ["module-graph", "api-surface"])

    def test_it_does_not_mutate_the_records_it_reads(self):
        rec = dict(self.STUB)
        CORE.strict_failures([rec], strict=True)
        self.assertNotIn("required_by", rec)


if __name__ == "__main__":
    unittest.main()
