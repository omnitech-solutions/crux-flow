"""Tests for validate-promptbook.py (ADR-0022 + ADR-0023, dev loop #1).

Covers:
  (a) subset-conformance — both repo schemas load without raising (i.e. they use
      ONLY the implemented keyword subset; reject-on-load would raise otherwise).
  (b) the valid fixtures validate to exit 0 / no errors.
  (c) the invalid fixture produces a validation error (the cycle if/then path).
  (d) compute_book_hash is stable across run-state field changes and changes
      when a prompt's text changes.
  (e) auto-detect dispatch picks the right kind.

Stdlib only (unittest, importlib, pathlib, sys, copy).
"""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

# Guard: skip entire module when PyYAML is absent.  The skip also prevents
# validate-promptbook's PyYAML re-exec lane from exec-replacing the unittest
# process — keep any main() invocation behind this guard.
try:
    import yaml  # noqa: F401
except ImportError as exc:
    raise unittest.SkipTest(f"PyYAML unavailable (uv lane required): {exc}")

# tests/ -> scripts/ -> crux/ -> repo root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "crux" / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "validate-promptbook.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_validator():
    # Ensure scripts/ is importable so the script's `from _yaml_min import ...`
    # resolves the shared loader.
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("validate_promptbook", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["validate_promptbook"] = mod
    spec.loader.exec_module(mod)
    return mod


vp = _load_validator()
import record_numbers  # noqa: E402  (importable once the loader put scripts/ on sys.path)


class SchemaSubsetConformanceTests(unittest.TestCase):
    """(a) Both repo schemas must use only the implemented keyword subset — i.e.
    load_schema (which calls assert_subset) must NOT raise."""

    def test_promptbook_schema_loads_within_subset(self):
        # Should not raise SchemaLoadError.
        schema = vp.load_schema(vp.PROMPTBOOK_SCHEMA)
        self.assertEqual(schema.get("$schema"), "https://json-schema.org/draft/2020-12/schema")

    def test_run_schema_loads_within_subset(self):
        schema = vp.load_schema(vp.RUN_SCHEMA)
        self.assertEqual(schema.get("$schema"), "https://json-schema.org/draft/2020-12/schema")

    def test_unimplemented_keyword_is_rejected_on_load(self):
        with self.assertRaises(vp.SchemaLoadError):
            vp.assert_subset({"oneOf": [{"type": "string"}]})

    def test_non_boolean_additional_properties_rejected(self):
        with self.assertRaises(vp.SchemaLoadError):
            vp.assert_subset({"additionalProperties": {"type": "string"}})


class ValidFixtureTests(unittest.TestCase):
    """(b) The valid fixtures validate clean (exit 0, no errors)."""

    def test_valid_promptbook_passes(self):
        code, errors = vp.validate_file(FIXTURES / "promptbook-valid.yaml", None)
        self.assertEqual(errors, [])
        self.assertEqual(code, 0)

    def test_valid_run_passes(self):
        code, errors = vp.validate_file(FIXTURES / "run-valid.yaml", None)
        self.assertEqual(errors, [])
        self.assertEqual(code, 0)

    def test_run_binds_to_book_via_compute_book_hash(self):
        book = vp.load_yaml((FIXTURES / "promptbook-valid.yaml").read_text(encoding="utf-8"))
        run = vp.load_yaml((FIXTURES / "run-valid.yaml").read_text(encoding="utf-8"))
        self.assertEqual(run["book_content_hash"], vp.compute_book_hash(book))


class InvalidFixtureTests(unittest.TestCase):
    """(c) The invalid fixture (cycle book missing `modules`) is rejected."""

    def test_invalid_promptbook_fails(self):
        code, errors = vp.validate_file(FIXTURES / "promptbook-invalid.yaml", None)
        self.assertEqual(code, 1)
        self.assertTrue(errors)
        # The defect is specifically the if/then `required:[modules]` applicator.
        joined = " ".join(e["error"] for e in errors)
        self.assertIn("modules", joined)
        self.assertTrue(any("/then/required" in e["schema_path"] for e in errors),
                        f"expected a then/required error, got: {errors}")

    def test_non_contiguous_n_caught_by_post_schema_pass(self):
        book = vp.load_yaml((FIXTURES / "promptbook-valid.yaml").read_text(encoding="utf-8"))
        book["prompts"][1]["n"] = 5  # break contiguity at index 1
        errors: list[dict] = []
        vp.post_schema_pass(book, errors, "<probe>")
        self.assertTrue(any("contiguous" in e["error"] for e in errors))


class ComputeBookHashTests(unittest.TestCase):
    """(d) Hash stability across run-state changes; sensitivity to plan changes."""

    def setUp(self):
        self.book = vp.load_yaml((FIXTURES / "promptbook-valid.yaml").read_text(encoding="utf-8"))
        self.base_hash = vp.compute_book_hash(self.book)

    def test_hash_stable_across_run_state_changes(self):
        for mutation in (
            ("current_run", "RUN-002"),
            ("current_prompt", 3),
            ("status", "archived"),
        ):
            book = copy.deepcopy(self.book)
            book[mutation[0]] = mutation[1]
            self.assertEqual(
                vp.compute_book_hash(book), self.base_hash,
                f"hash changed when only run-state field {mutation[0]!r} changed",
            )

    def test_hash_changes_when_prompt_text_changes(self):
        book = copy.deepcopy(self.book)
        book["prompts"][0]["prompt"] = book["prompts"][0]["prompt"] + "\nNow do something else.\n"
        self.assertNotEqual(vp.compute_book_hash(book), self.base_hash)

    def test_hash_changes_when_title_changes(self):
        book = copy.deepcopy(self.book)
        book["title"] = "A different title"
        self.assertNotEqual(vp.compute_book_hash(book), self.base_hash)

    def test_frozen_subset_omits_absent_optional_keys(self):
        # A prompt with no module_tag/side_effects must NOT introduce those keys
        # (present-with-null would diverge the two parse paths).
        book = copy.deepcopy(self.book)
        del book["modules"]  # also exercises optional top-level omission
        book["tags"] = ["fixture"]  # drop 'cycle' so the schema doesn't require modules
        for p in book["prompts"]:
            p.pop("module_tag", None)
            p.pop("side_effects", None)
        subset = vp.frozen_plan_subset(book)
        self.assertNotIn("modules", subset)
        for p in subset["prompts"]:
            self.assertNotIn("module_tag", p)
            self.assertNotIn("side_effects", p)


class FallbackParserParityTests(unittest.TestCase):
    """(f) PLAUSIBLE-2: the hand-rolled fallback parser (PyYAML unavailable) must
    agree byte-for-byte with the PyYAML fast-path, so the book_content_hash
    lock-step (ADR-0023 §3 / docs/AGENTS.md §13.2) holds regardless of whether
    PyYAML is installed.

    We force the fallback by making ``import yaml`` raise ImportError inside
    ``load_yaml`` (it imports yaml at call time): setting ``sys.modules['yaml'] =
    None`` causes the import statement to raise. A try/finally restores the
    original module object so the rest of the suite still uses real PyYAML.
    """

    FIXTURES = ("promptbook-valid.yaml", "run-valid.yaml", "promptbook-invalid.yaml")

    @staticmethod
    def _load_via_fallback(text: str):
        """Parse ``text`` through ``load_yaml`` with PyYAML forced unavailable."""
        sentinel = object()
        saved = sys.modules.get("yaml", sentinel)
        sys.modules["yaml"] = None  # makes `import yaml` raise ImportError
        try:
            return vp.load_yaml(text)
        finally:
            if saved is sentinel:
                sys.modules.pop("yaml", None)
            else:
                sys.modules["yaml"] = saved

    def test_fallback_is_actually_exercised(self):
        # Guard: prove that forcing yaml unavailable really takes the fallback
        # branch (not silently the PyYAML path). PyYAML resolves YAML-1.1 `yes`
        # to a bool via its own resolver; the fallback resolves it via
        # _parse_scalar. We assert the fallback returns the bool, which only
        # happens if _parse_scalar ran. If someone broke _parse_scalar's bool
        # coercion (e.g. dropped `yes`), this assertion fails — confirming the
        # test exercises the hand-rolled path, not PyYAML.
        result = self._load_via_fallback("flag: yes\nname: PB-9001\n")
        self.assertIs(result["flag"], True)
        self.assertEqual(result["name"], "PB-9001")

    def test_fallback_matches_pyyaml_canonical_json(self):
        for name in self.FIXTURES:
            with self.subTest(fixture=name):
                text = (FIXTURES / name).read_text(encoding="utf-8")
                normal = vp.load_yaml(text)              # PyYAML fast-path
                fallback = self._load_via_fallback(text)  # hand-rolled path
                self.assertEqual(
                    vp.canonical_json(normal),
                    vp.canonical_json(fallback),
                    f"fallback canonical_json diverged from PyYAML for {name}",
                )

    def test_fallback_book_hash_matches_pyyaml(self):
        text = (FIXTURES / "promptbook-valid.yaml").read_text(encoding="utf-8")
        normal = vp.load_yaml(text)
        fallback = self._load_via_fallback(text)
        self.assertEqual(
            vp.compute_book_hash(normal),
            vp.compute_book_hash(fallback),
            "fallback compute_book_hash diverged from PyYAML",
        )

    def test_fallback_matches_pyyaml_on_yaml11_scalar_forms(self):
        # The repo fixtures happen to use only already-canonical scalar forms
        # (plain date `YYYY-MM-DD` and the `T...Z` datetime), so they can't catch
        # a regression in Fix 1's coercion. This synthetic doc exercises the forms
        # that *would* diverge if the fallback dropped the parity coercion: the
        # space-separated datetime, lowercase-`t` datetime, and YAML-1.1 boolean
        # spellings (yes/no/on/off, case-insensitive) — all reachable in a
        # promptbook/run YAML. Each must parse identically on both paths.
        doc = (
            "format_version: \"1\"\n"
            "id: PB-9003\n"
            "space_dt: 2026-05-29 12:00:00\n"
            "space_dt_z: 2026-05-29 12:00:00Z\n"
            "lower_t_dt: 2026-05-29t12:00:00\n"
            "plain_date: 2026-05-29\n"
            "flag_yes: yes\n"
            "flag_no: No\n"
            "flag_on: ON\n"
            "flag_off: off\n"
            "count: 3\n"
            "prompts: []\n"
        )
        normal = vp.load_yaml(doc)
        fallback = self._load_via_fallback(doc)
        self.assertEqual(normal, fallback)
        self.assertEqual(vp.canonical_json(normal), vp.canonical_json(fallback))
        # Spot-check the coercions actually happened (not just mutual agreement).
        self.assertEqual(fallback["space_dt"], "2026-05-29T12:00:00")
        self.assertEqual(fallback["space_dt_z"], "2026-05-29T12:00:00Z")
        self.assertEqual(fallback["lower_t_dt"], "2026-05-29T12:00:00")
        self.assertIs(fallback["flag_yes"], True)
        self.assertIs(fallback["flag_off"], False)


class AutoDetectDispatchTests(unittest.TestCase):
    """(e) Auto-detect picks the right kind; ambiguous/empty → None."""

    def test_detects_promptbook(self):
        book = vp.load_yaml((FIXTURES / "promptbook-valid.yaml").read_text(encoding="utf-8"))
        self.assertEqual(vp.detect_kind(book), "promptbook")

    def test_detects_run(self):
        run = vp.load_yaml((FIXTURES / "run-valid.yaml").read_text(encoding="utf-8"))
        self.assertEqual(vp.detect_kind(run), "run")

    def test_both_signatures_is_ambiguous(self):
        # book_id+run_id present AND id+prompts present -> the run branch requires
        # run_id absent for the promptbook signature, so book_id+run_id wins only
        # if the promptbook signature is NOT also satisfied. With run_id present,
        # has_book is False, so this resolves to 'run'. A truly ambiguous doc is
        # one matching neither — verified below.
        doc = {"id": "PB-0001", "prompts": [], "book_id": "PB-0001", "run_id": "RUN-001"}
        # run_id present -> promptbook signature fails (no run_id required) -> 'run'
        self.assertEqual(vp.detect_kind(doc), "run")

    def test_neither_signature_is_none(self):
        self.assertIsNone(vp.detect_kind({"foo": "bar"}))
        self.assertIsNone(vp.detect_kind({}))

    def test_kind_override_forces_schema(self):
        # Forcing --kind run on a promptbook fixture must produce errors (wrong shape).
        code, errors = vp.validate_file(FIXTURES / "promptbook-valid.yaml", "run")
        self.assertEqual(code, 1)
        self.assertTrue(errors)


class RunInvalidFixtureTests(unittest.TestCase):
    """(g) Run-side known-bad fixture: a per-prompt `state` enum violation must
    be flagged. (`in_progress` is a valid run-level `status` but NOT a valid
    per-prompt `state` — a realistic mix-up.)"""

    def test_invalid_run_fails(self):
        code, errors = vp.validate_file(FIXTURES / "run-invalid.yaml", "run")
        self.assertEqual(code, 1)
        self.assertTrue(errors)
        # The defect is the per-prompt state enum, not something else.
        self.assertTrue(
            any("/state/enum" in e["schema_path"] for e in errors),
            f"expected a per-prompt state enum error, got: {errors}",
        )
        joined = " ".join(e["error"] for e in errors)
        self.assertIn("in_progress", joined)

    def test_invalid_run_autodetects_as_run(self):
        # Auto-detect (no --kind) must still classify + reject it.
        code, errors = vp.validate_file(FIXTURES / "run-invalid.yaml", None)
        self.assertEqual(code, 1)
        self.assertTrue(errors)


class RetiredFieldReadPathTests(unittest.TestCase):
    """A retired per-prompt field must be IGNORED on read, on both formats.

    The per-prompt archive-eligibility flag was deleted, and the legacy `.md` reader
    was told to ignore it: those bodies are frozen, so a finding nobody may act on is
    worse than silence. The `.yaml` reader got no such treatment — and because the run
    schema sets `additionalProperties: false`, a `.yaml` snapshot written before the
    retirement became schema-INVALID rather than ignored. Such snapshots are reachable
    in any tree that ran an earlier crux, so the two read paths must agree.

    The readmission is scoped to the reachable history and no wider: `true` is the only
    value any writer ever produced, so the key is pinned to it. A retired field
    readmitted at any value is an escape hatch, not a compatibility lane."""

    def _run_doc(self, **prompt_extra):
        return {
            "format_version": "1", "run_id": "RUN-001", "book_id": "PB-0001",
            "book_content_hash": "sha256:" + "0" * 64,
            "started_at": "2026-01-01T00:00:00Z", "completed_at": None,
            "status": "in_progress", "current_prompt": 1,
            "prompts": [{"n": 1, "title": "t", "state": "blocked", "started": None,
                         "completed": None, "result": "", "artifacts": [],
                         **prompt_extra}],
        }

    def _errors(self, doc):
        errors: list[dict] = []
        vp.validate(doc, vp.load_schema(vp.RUN_SCHEMA), "#", "#", errors, "<test>")
        return errors

    def test_a_legacy_yaml_snapshot_carrying_the_retired_flag_still_validates(self):
        self.assertEqual(self._errors(self._run_doc(blocked_confirmed=True)), [])

    def test_the_retired_flag_is_rejected_at_any_other_value(self):
        # The readmission is a compatibility lane, not a free-form slot. `true` is
        # the only value any writer ever produced, so admitting others would widen
        # the schema past the history it exists to accept.
        for value in (False, None, "yes", 1, "true"):
            with self.subTest(value=value):
                self.assertTrue(self._errors(self._run_doc(blocked_confirmed=value)),
                                f"blocked_confirmed={value!r} should not validate")

    def test_a_genuinely_unknown_per_prompt_field_is_still_rejected(self):
        # The readmission must not degrade into a blanket escape hatch.
        self.assertTrue(self._errors(self._run_doc(invented_field="x")))


class NestedRejectOnLoadTests(unittest.TestCase):
    """(h) Reject-on-load must recurse: an unimplemented keyword buried inside
    properties/items/allOf/then (not just at the top level) must STILL raise
    SchemaLoadError. This guards the draft-2020-12 honesty claim's recursion —
    a schema can never smuggle an unimplemented keyword past the engine by
    nesting it."""

    def test_unimplemented_keyword_nested_in_properties_rejected(self):
        schema = {"type": "object", "properties": {"x": {"oneOf": [{"type": "string"}]}}}
        with self.assertRaises(vp.SchemaLoadError):
            vp.assert_subset(schema)

    def test_unimplemented_keyword_nested_in_items_rejected(self):
        schema = {"type": "array", "items": {"not": {"type": "string"}}}
        with self.assertRaises(vp.SchemaLoadError):
            vp.assert_subset(schema)

    def test_unimplemented_keyword_nested_in_allof_rejected(self):
        schema = {"allOf": [{"type": "object"}, {"properties": {"y": {"oneOf": []}}}]}
        with self.assertRaises(vp.SchemaLoadError):
            vp.assert_subset(schema)

    def test_unimplemented_keyword_nested_in_then_rejected(self):
        schema = {
            "if": {"type": "object"},
            "then": {"properties": {"z": {"multipleOf": 2}}},
        }
        with self.assertRaises(vp.SchemaLoadError):
            vp.assert_subset(schema)

    def test_unimplemented_keyword_nested_in_contains_rejected(self):
        schema = {"type": "array", "contains": {"format": "email"}}
        with self.assertRaises(vp.SchemaLoadError):
            vp.assert_subset(schema)


class NegativeAssertionTests(unittest.TestCase):
    """(i) The minItems / minLength / minimum branches were only exercised on the
    happy path. Drive each into failure with a minimal instance+schema pair and
    assert an error is produced."""

    def test_min_items_failure(self):
        errors: list[dict] = []
        vp.validate([1], {"type": "array", "minItems": 2}, "#", "#", errors, "<probe>")
        self.assertTrue(any("minItems" in e["schema_path"] for e in errors), errors)

    def test_min_length_failure(self):
        errors: list[dict] = []
        vp.validate("a", {"type": "string", "minLength": 3}, "#", "#", errors, "<probe>")
        self.assertTrue(any("minLength" in e["schema_path"] for e in errors), errors)

    def test_minimum_failure(self):
        errors: list[dict] = []
        vp.validate(0, {"type": "integer", "minimum": 1}, "#", "#", errors, "<probe>")
        self.assertTrue(any("minimum" in e["schema_path"] for e in errors), errors)

    def test_min_items_boundary_passes(self):
        # Sanity: at the boundary there is no error (the happy edge of the branch).
        errors: list[dict] = []
        vp.validate([1, 2], {"type": "array", "minItems": 2}, "#", "#", errors, "<probe>")
        self.assertEqual(errors, [])


class MainCliLayerTests(unittest.TestCase):
    """(j) The actual ship surface: audit-docs / run-promptbook invoke
    `validate-promptbook.py --kind run <path>` as a subprocess. Exercise the
    main()/CLI layer end-to-end:
      (a) valid fixture     -> exit 0, empty stdout
      (b) invalid fixture   -> exit 1, parseable {"errors":[...]} on stdout
      (c) non-existent path -> exit 1, error envelope (file-not-found branch)
      (d) multi-path        -> worst exit code wins (1)
    We test both the in-process main(argv) entry and a real subprocess so the
    `__main__` SystemExit wiring is covered too.
    """

    # --- in-process main(argv) ---

    def test_main_valid_exits_zero_no_stdout(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = vp.main([str(FIXTURES / "promptbook-valid.yaml")])
        self.assertEqual(code, 0)
        self.assertEqual(buf.getvalue(), "")

    def test_main_invalid_exits_one_with_error_envelope(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = vp.main([str(FIXTURES / "promptbook-invalid.yaml")])
        self.assertEqual(code, 1)
        payload = json.loads(buf.getvalue())
        self.assertIn("errors", payload)
        self.assertTrue(payload["errors"])

    def test_main_missing_path_exits_one_file_not_found(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = vp.main([str(FIXTURES / "does-not-exist.yaml")])
        self.assertEqual(code, 1)
        payload = json.loads(buf.getvalue())
        self.assertTrue(any("file not found" in e["error"] for e in payload["errors"]))

    def test_main_multipath_worst_exit_code_wins(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = vp.main([
                str(FIXTURES / "promptbook-valid.yaml"),
                str(FIXTURES / "promptbook-invalid.yaml"),
            ])
        self.assertEqual(code, 1)
        payload = json.loads(buf.getvalue())
        # Aggregated envelope carries the invalid file's error(s).
        self.assertTrue(payload["errors"])

    # --- real subprocess (covers the __main__ SystemExit wiring) ---

    def _run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), *args],
            capture_output=True, text=True, cwd=str(SCRIPTS_DIR),
        )

    def test_subprocess_valid_run_exits_zero_empty_stdout(self):
        proc = self._run_cli("--kind", "run", str(FIXTURES / "run-valid.yaml"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "")

    def test_subprocess_invalid_run_exits_one_with_envelope(self):
        proc = self._run_cli("--kind", "run", str(FIXTURES / "run-invalid.yaml"))
        self.assertEqual(proc.returncode, 1)
        payload = json.loads(proc.stdout)
        self.assertIn("errors", payload)
        self.assertTrue(payload["errors"])


def _cycle_book(kind="adr", primary=1, devs=1, reviews=1):
    """Build a structurally-valid cycle book dict (ADR-0029 cycle-coverage).
    kind 'adr' -> adr- modules + modules.adrs; 'verify' -> verify- + modules.verify."""
    prompts: list[dict] = []
    n = 0

    def add(tag, count):
        nonlocal n
        for _ in range(count):
            n += 1
            p = {"n": n, "title": f"P{n}", "purpose": "x", "prompt": "x", "expected_output": "x"}
            if tag:
                p["module_tag"] = tag
            prompts.append(p)

    prefix = "adr" if kind == "adr" else "verify"
    for i in range(1, primary + 1):
        add(f"{prefix}-{i}", 4)
    for i in range(1, devs + 1):
        add(f"dev-{i}", 4)
    for i in range(1, reviews + 1):
        add(f"review-{i}", 3)
    add(None, 2)  # prep + summary (untagged)
    total = 4 * primary + 4 * devs + 3 * reviews + 2
    modules = {("adrs" if kind == "adr" else "verify"): primary,
               "dev_loops": devs, "review_cycles": reviews}
    return {
        "format_version": "1", "id": "PB-9101", "title": "t", "status": "active",
        "created_at": "2026-06-01", "total_prompts": total, "current_run": None,
        "current_prompt": None, "forked_from": None, "tags": ["cycle"],
        "cycle_kind": kind, "modules": modules, "goal": "g", "strategy": "s",
        "prompts": prompts,
    }


class CycleCoveragePassTests(unittest.TestCase):
    """ADR-0029 — the post-schema cycle-coverage pass (machine-enforced for BOTH
    adr-kind dev-cycle and verify-kind iterate books)."""

    def _errs(self, book):
        e: list[dict] = []
        vp.cycle_coverage_pass(book, e, "<t>")
        return [x["error"] for x in e]

    def test_valid_adr_cycle_passes(self):
        self.assertEqual(self._errs(_cycle_book("adr")), [])

    def test_valid_verify_cycle_passes(self):
        self.assertEqual(self._errs(_cycle_book("verify")), [])

    def test_larger_valid_cycle_passes_pb0021_shape(self):
        self.assertEqual(self._errs(_cycle_book("adr", 1, 3, 2)), [])

    def test_missing_cycle_kind_flagged(self):
        b = _cycle_book("adr"); del b["cycle_kind"]
        self.assertTrue(any("cycle_kind" in e for e in self._errs(b)))

    def test_mixed_adr_and_verify_tags_flagged(self):
        b = _cycle_book("adr"); b["prompts"][4]["module_tag"] = "verify-1"
        errs = self._errs(b)
        self.assertTrue(any("must not mix" in e or "verify-" in e for e in errs))

    def test_wrong_formula_flagged(self):
        b = _cycle_book("adr"); b["total_prompts"] = 99
        self.assertTrue(any("total_prompts" in e for e in self._errs(b)))

    def test_degenerate_cycle_no_primary_flagged(self):
        b = _cycle_book("adr")
        b["prompts"] = [p for p in b["prompts"] if not str(p.get("module_tag", "")).startswith("adr-")]
        for i, p in enumerate(b["prompts"], 1):
            p["n"] = i
        self.assertTrue(any("degenerate" in e or "no adr-" in e for e in self._errs(b)))

    def test_wrong_module_prompt_count_flagged(self):
        b = _cycle_book("adr")
        b["prompts"].insert(2, {"n": 0, "title": "x", "purpose": "x", "prompt": "x",
                                "expected_output": "x", "module_tag": "adr-1"})
        for i, p in enumerate(b["prompts"], 1):
            p["n"] = i
        b["total_prompts"] = len(b["prompts"])
        self.assertTrue(any("adr-1" in e and "expected 4" in e for e in self._errs(b)))

    def test_modules_count_mismatch_flagged(self):
        b = _cycle_book("adr", 1, 3, 2); b["modules"]["dev_loops"] = 1
        self.assertTrue(any("modules.dev_loops" in e for e in self._errs(b)))

    def test_grandfathered_book_skipped(self):
        b = _cycle_book("adr"); b["total_prompts"] = 99
        b["cycle_grandfathered"] = True; b["grandfather_reason"] = "legacy"
        self.assertEqual(self._errs(b), [])

    def test_grandfathered_requires_reason(self):
        # Schema-level coupling (ADR-0029): cycle_grandfathered:true => grandfather_reason required.
        b = _cycle_book("adr"); b["cycle_grandfathered"] = True  # no grandfather_reason
        errs: list[dict] = []
        vp.validate(b, vp.load_schema(vp.PROMPTBOOK_SCHEMA), "#", "#", errs, "<t>")
        self.assertTrue(any("grandfather_reason" in e["error"] for e in errs))

    def test_non_cycle_book_skipped(self):
        b = _cycle_book("adr"); b["tags"] = ["fixture"]; del b["cycle_kind"]; del b["modules"]
        for p in b["prompts"]:
            p.pop("module_tag", None)
        self.assertEqual(self._errs(b), [])

    def test_adr_and_verify_floor_and_formula_are_unchanged_by_the_patch_tier(self):
        # ADR-0077 clause 1: patch is a THIRD tier, not a relaxation of two. A
        # 5-prompt adr book must still fail the 13-floor and the formula.
        b = _cycle_book("adr")
        b["prompts"] = b["prompts"][:5]
        b["total_prompts"] = 5
        errs = self._errs(b)
        self.assertTrue(any("floor of 13" in e for e in errs), errs)


def _patch_book(**over):
    """A structurally-valid `patch` cycle book (ADR-0077 clause 1): five phases in
    order, one prompt each, no modules, no module tags, a declared blast radius."""
    prompts = [
        {"n": i + 1, "title": f"P{i + 1}", "purpose": "x", "prompt": "x",
         "expected_output": "x", "phase": ph}
        for i, ph in enumerate(["verify", "plan", "implement", "review", "summary"])
    ]
    book = {
        "format_version": "1", "id": "PB-9102", "title": "t", "status": "active",
        "created_at": "2026-06-01", "total_prompts": 5, "current_run": None,
        "current_prompt": None, "forked_from": None, "tags": ["cycle", "patch"],
        "cycle_kind": "patch", "blast_radius": ["crux/scripts/advance-run.py"],
        "goal": "g", "strategy": "s", "prompts": prompts,
    }
    book.update(over)
    return book


class CycleKindFlipAtArchiveTests(unittest.TestCase):
    """A mid-run `cycle_kind` flip must not skip the blast-radius precondition.

    `archive-promptbook` branches on `cycle_kind` to decide whether to run the
    containment check, and `cycle_kind` is NOT in the frozen-plan subset that
    `book_content_hash` covers. So flipping `patch` to `adr` mid-run moved no hash,
    tripped no CHK-PB-BIND, and silently removed the one gate the tier pays for.

    The closure is that `archive-promptbook` runs `validate-promptbook --kind
    promptbook` on the book BEFORE it branches: a flipped book carries a
    `blast_radius` and per-prompt `phase` fields that only a `patch` book may carry,
    so it no longer validates as the kind it now claims to be. These tests pin both
    halves — that the validator catches the flip, and that the hash does not, which
    is why the validator call is the closure and the freeze is not."""

    def _errs(self, book):
        e: list[dict] = []
        vp.cycle_coverage_pass(book, e, "<t>")
        return [x["error"] for x in e]

    def test_a_cycle_kind_flip_off_patch_is_caught_by_the_validator(self):
        for flipped in ("adr", "verify"):
            with self.subTest(cycle_kind=flipped):
                errs = self._errs(_patch_book(cycle_kind=flipped))
                self.assertTrue(
                    any("blast_radius is declared only by" in x for x in errs),
                    f"a book flipped to {flipped!r} still carries blast_radius: {errs}")
                self.assertTrue(
                    any("phase is carried only by" in x for x in errs),
                    f"a book flipped to {flipped!r} still carries per-prompt phase: {errs}")

    def test_deleting_cycle_kind_entirely_is_caught_too(self):
        # The flip need not name another kind: dropping the key removes the branch
        # condition just as effectively.
        book = _patch_book()
        del book["cycle_kind"]
        self.assertTrue(self._errs(book))

    def test_the_binding_hash_does_not_move_on_a_cycle_kind_flip(self):
        # This is the reason the validator call is load-bearing. If the hash moved,
        # `check-blast-radius.py`'s own binding check would already refuse and no
        # archive-path change would be needed.
        before = vp.compute_book_hash(_patch_book())
        after = vp.compute_book_hash(_patch_book(cycle_kind="adr"))
        self.assertEqual(before, after,
                         "cycle_kind is outside the frozen-plan subset; if that "
                         "changed, re-evaluate whether the archive-path validator "
                         "call is still the closure")

    def test_archive_promptbook_validates_the_book_before_branching_on_cycle_kind(self):
        skill = REPO_ROOT / "crux" / "skills" / "archive-promptbook" / "SKILL.md"
        self.assertTrue(skill.exists(), f"archive-promptbook/SKILL.md missing at {skill}")
        text = skill.read_text(encoding="utf-8")
        self.assertIn("validate-promptbook.py", text,
                      "archive-promptbook must run the validator on the book it is "
                      "about to archive; nothing else on the archive path would "
                      "catch a mid-run cycle_kind flip")
        self.assertIn("--kind promptbook", text,
                      "the validator call must pin --kind promptbook rather than "
                      "relying on auto-detect, so a book whose keys were edited "
                      "cannot be validated as some other kind")
        # Order is the whole point: validating AFTER the branch would already have
        # skipped the precondition the flip removed.
        validate_at = text.index("--kind promptbook")
        branch_at = text.index("For a `cycle_kind: patch` book, run the blast-radius check")
        self.assertLess(validate_at, branch_at,
                        "the validator call must precede the cycle_kind branch")


class PatchCycleCoverageTests(unittest.TestCase):
    """ADR-0077 clause 1 — the `patch` tier's own formula (the constant 5), its own
    floor, and the fixed five-phase sequence."""

    def _errs(self, book):
        e: list[dict] = []
        vp.cycle_coverage_pass(book, e, "<t>")
        return [x["error"] for x in e]

    def test_valid_patch_book_passes_the_coverage_pass(self):
        self.assertEqual(self._errs(_patch_book()), [])

    def test_valid_patch_book_passes_the_schema(self):
        errors: list[dict] = []
        vp.validate(_patch_book(), vp.load_schema(vp.PROMPTBOOK_SCHEMA), "#", "#", errors, "<t>")
        self.assertEqual(errors, [])

    def test_patch_book_needs_no_modules_block(self):
        # The cycle-tagged -> modules-required conditional is narrowed to the
        # module-structured kinds; a patch book has phases, not modules.
        b = _patch_book()
        self.assertNotIn("modules", b)
        errors: list[dict] = []
        vp.validate(b, vp.load_schema(vp.PROMPTBOOK_SCHEMA), "#", "#", errors, "<t>")
        self.assertEqual(errors, [])

    def test_cycle_tagged_book_without_a_kind_still_requires_modules(self):
        # The narrowing must not weaken the pre-existing rule.
        b = _patch_book(); del b["cycle_kind"]; del b["blast_radius"]
        for p in b["prompts"]:
            p.pop("phase", None)
        errors: list[dict] = []
        vp.validate(b, vp.load_schema(vp.PROMPTBOOK_SCHEMA), "#", "#", errors, "<t>")
        self.assertTrue(any("modules" in e["error"] for e in errors), errors)

    def test_wrong_prompt_count_flagged(self):
        b = _patch_book()
        b["prompts"] = b["prompts"][:4]
        b["total_prompts"] = 4
        errs = self._errs(b)
        self.assertTrue(any("expected 5" in e for e in errs), errs)

    def test_total_prompts_disagreeing_with_the_constant_flagged(self):
        b = _patch_book(total_prompts=13)
        self.assertTrue(any("total_prompts" in e for e in self._errs(b)))

    def test_module_tag_on_a_patch_prompt_flagged(self):
        b = _patch_book()
        b["prompts"][1]["module_tag"] = "dev-1"
        self.assertTrue(any("module_tag" in e for e in self._errs(b)))

    def test_missing_phase_flagged(self):
        b = _patch_book()
        del b["prompts"][2]["phase"]
        self.assertTrue(any("missing 'phase'" in e for e in self._errs(b)))

    def test_out_of_order_phases_flagged(self):
        b = _patch_book()
        b["prompts"][1]["phase"], b["prompts"][2]["phase"] = "implement", "plan"
        self.assertTrue(any("phase sequence" in e for e in self._errs(b)))

    def test_missing_blast_radius_flagged_by_schema_and_pass(self):
        b = _patch_book(); del b["blast_radius"]
        self.assertTrue(any("blast_radius" in e for e in self._errs(b)))
        errors: list[dict] = []
        vp.validate(b, vp.load_schema(vp.PROMPTBOOK_SCHEMA), "#", "#", errors, "<t>")
        self.assertTrue(any("blast_radius" in e["error"] for e in errors), errors)

    def test_empty_blast_radius_flagged(self):
        b = _patch_book(blast_radius=[])
        self.assertTrue(any("blast_radius" in e for e in self._errs(b)))

    def test_a_repo_root_declaration_is_rejected(self):
        # A radius of "." covers every path, so the archive-time containment
        # check could never fail. That is the "drawn wider than the work needs"
        # dodge in its most absolute form — reject it at authoring time.
        for bad in (".", "./", "./."):
            with self.subTest(entry=bad):
                self.assertIn("repository root", vp.invalid_blast_radius_entry(bad) or "")
                b = _patch_book(blast_radius=[bad])
                self.assertTrue(any("not a repo-relative path" in e for e in self._errs(b)))

    def test_non_repo_relative_blast_radius_entries_flagged(self):
        for bad in ("/etc/passwd", "~/secrets", "../outside", "crux\\scripts",
                    "  ", "crux/../../etc", "C:/windows", ".", "./"):
            with self.subTest(entry=bad):
                b = _patch_book(blast_radius=[bad])
                self.assertTrue(any("not a repo-relative path" in e for e in self._errs(b)),
                                f"{bad!r} was accepted")

    def test_blast_radius_on_a_non_patch_book_flagged(self):
        b = _cycle_book("adr"); b["blast_radius"] = ["crux/"]
        self.assertTrue(any("blast_radius" in e for e in self._errs(b)))

    def test_phase_on_a_non_patch_book_flagged(self):
        b = _cycle_book("adr"); b["prompts"][0]["phase"] = "verify"
        self.assertTrue(any("phase" in e for e in self._errs(b)))

    def test_grammar_helper_accepts_ordinary_declarations(self):
        for good in ("crux/scripts", "crux/scripts/", "crux/scripts/advance-run.py",
                     "README.md", "a/b/c/d.txt", "dot.name/file.py"):
            with self.subTest(entry=good):
                self.assertIsNone(vp.invalid_blast_radius_entry(good))


class FrozenPlanSubsetTests(unittest.TestCase):
    """ADR-0023 §3 + ADR-0077 clause 2. The optional keys are omitted when absent, so
    adding them cannot move the hash of a book that lacks them; `blast_radius` is
    inside the subset, so widening a declaration mid-run moves the hash (CHK-PB-BIND)."""

    def test_absent_optional_keys_are_omitted_not_null(self):
        subset = vp.frozen_plan_subset(_cycle_book("adr"))
        self.assertNotIn("blast_radius", subset)
        for p in subset["prompts"]:
            self.assertNotIn("phase", p)

    def test_blast_radius_and_phase_are_frozen_when_present(self):
        subset = vp.frozen_plan_subset(_patch_book())
        self.assertEqual(subset["blast_radius"], ["crux/scripts/advance-run.py"])
        self.assertEqual(subset["prompts"][0]["phase"], "verify")

    def test_widening_the_blast_radius_moves_the_book_hash(self):
        before = vp.compute_book_hash(_patch_book())
        after = vp.compute_book_hash(_patch_book(blast_radius=["crux/"]))
        self.assertNotEqual(before, after)

    def test_run_state_fields_still_do_not_move_the_hash(self):
        base = _patch_book()
        moved = _patch_book(current_run="RUN-001", current_prompt=3, status="archived")
        self.assertEqual(vp.compute_book_hash(base), vp.compute_book_hash(moved))


class TemplateInstantiationTests(unittest.TestCase):
    """ADR-0029 / council nit: EVERY canonical cycle template instantiates to a
    valid cycle book through the SAME validator + cycle-coverage pass — catches
    token drift and keeps dev-cycle (adr), iterate (verify) and patch-cycle
    (patch, ADR-0077) in lock-step."""

    TEMPLATES = REPO_ROOT / "crux" / "templates"

    def _validate_template(self, name):
        text = (self.TEMPLATES / name).read_text(encoding="utf-8")
        text = text.replace("PB-NNNN", "PB-9001").replace("YYYY-MM-DD", "2026-06-01")
        doc = vp.load_yaml(text)
        errors: list[dict] = []
        vp.validate(doc, vp.load_schema(vp.PROMPTBOOK_SCHEMA), "#", "#", errors, name)
        vp.post_schema_pass(doc, errors, name)
        vp.cycle_coverage_pass(doc, errors, name)
        return doc, errors

    def test_dev_cycle_template_is_valid_adr_cycle(self):
        doc, errors = self._validate_template("cycle-promptbook-template.yaml")
        self.assertEqual([e["error"] for e in errors], [])
        self.assertEqual(doc.get("cycle_kind"), "adr")

    def test_iterate_template_is_valid_verify_cycle(self):
        doc, errors = self._validate_template("iterate-promptbook-template.yaml")
        self.assertEqual([e["error"] for e in errors], [])
        self.assertEqual(doc.get("cycle_kind"), "verify")

    def test_patch_template_is_valid_patch_cycle(self):
        doc, errors = self._validate_template("patch-promptbook-template.yaml")
        self.assertEqual([e["error"] for e in errors], [])
        self.assertEqual(doc.get("cycle_kind"), "patch")
        self.assertEqual(doc.get("total_prompts"), 5)
        self.assertEqual([p["phase"] for p in doc["prompts"]],
                         ["verify", "plan", "implement", "review", "summary"])
        self.assertNotIn("modules", doc)
        self.assertTrue(doc.get("blast_radius"))

    def test_patch_review_phase_wires_release_content_scan(self):
        """The patch tier's review phase (prompt 4) carries the same
        release-content gate the other two canonical books carry at prompt 7."""
        doc, errors = self._validate_template("patch-promptbook-template.yaml")
        self.assertEqual([e["error"] for e in errors], [])
        self.assertIn("check-public-release-content", self._prompt_n(doc, 4)["prompt"])

    @staticmethod
    def _prompt_n(doc, n):
        for p in doc.get("prompts", []):
            if p.get("n") == n:
                return p
        raise AssertionError(f"no prompt with n={n}")

    def test_dev_cycle_prompt7_wires_release_content_scan(self):
        """The adr-canonical book's prompt-7 quality-gate list names the
        release-content scan (PB-0041 wiring) AND the book still validates."""
        doc, errors = self._validate_template("cycle-promptbook-template.yaml")
        self.assertEqual([e["error"] for e in errors], [])
        self.assertIn(
            "check-public-release-content",
            self._prompt_n(doc, 7)["prompt"],
            msg="cycle-promptbook-template.yaml prompt 7 lost the "
            "release-content gate (ADR-0034 §4 / PB-0041).",
        )

    def test_iterate_prompt7_wires_release_content_scan(self):
        """The verify-canonical book's prompt-7 quality-gate list names the
        release-content scan (PB-0041 wiring) AND the book still validates."""
        doc, errors = self._validate_template("iterate-promptbook-template.yaml")
        self.assertEqual([e["error"] for e in errors], [])
        self.assertIn(
            "check-public-release-content",
            self._prompt_n(doc, 7)["prompt"],
            msg="iterate-promptbook-template.yaml prompt 7 lost the "
            "release-content gate (ADR-0034 §4 / PB-0041).",
        )


class DuplicateNumberInTreeTests(unittest.TestCase):
    """A file validated inside a documentation tree fails when the tree holds a
    duplicate promptbook or run-directory number.

    A duplicate is a property of the tree, so the finding rides on any file the
    validator is pointed at inside it. A fixture outside a tree owes nothing. The
    clean-tree tests are the controls: the same file, in a tree with no duplicate,
    exits 0, so the exit 1 comes from the duplicate and not from the file.
    """

    def setUp(self):
        import shutil
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.pb = self.tmp / "bionic" / "promptbooks"
        self.book_src = FIXTURES / "promptbook-valid.yaml"
        self.run_src = FIXTURES / "run-valid.yaml"

    def _put(self, src: Path, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        return dest

    def _seed_duplicate_books(self) -> Path:
        first = self._put(self.book_src, self.pb / "active" / "PB-0001-alpha.yaml")
        self._put(self.book_src, self.pb / "archive" / "PB-0001-alpha-copy.yaml")
        return first

    def test_a_clean_tree_leaves_a_valid_book_at_exit_zero(self):
        book = self._put(self.book_src, self.pb / "active" / "PB-0001-alpha.yaml")
        code, errors = vp.validate_file(book, None)
        self.assertEqual((code, errors), (0, []))

    def test_a_duplicate_book_number_fails_a_book_in_active(self):
        book = self._seed_duplicate_books()
        code, errors = vp.validate_file(book, None)
        self.assertEqual(code, 1)
        named = [e["error"] for e in errors if "PB-0001" in e["error"]]
        self.assertEqual(len(named), 1, errors)
        self.assertIn("PB-0001-alpha-copy.yaml", named[0])

    def test_a_duplicate_book_number_fails_a_book_in_archive(self):
        self._seed_duplicate_books()
        code, errors = vp.validate_file(self.pb / "archive" / "PB-0001-alpha-copy.yaml",
                                        None)
        self.assertEqual(code, 1)
        self.assertTrue(any("PB-0001" in e["error"] for e in errors), errors)

    def test_a_duplicate_run_directory_fails_a_run_snapshot(self):
        run = self._put(self.run_src, self.pb / "runs" / "PB-0001-alpha" / "run-RUN-001.yaml")
        code, errors = vp.validate_file(run, None)
        self.assertEqual((code, errors), (0, []), "control: one run directory is clean")
        self._put(self.run_src, self.pb / "runs" / "PB-0001-fork" / "run-RUN-001.yaml")
        code, errors = vp.validate_file(run, None)
        self.assertEqual(code, 1)
        self.assertTrue(any("PB-0001" in e["error"] for e in errors), errors)

    def test_a_copy_under_legacy_does_not_fail_the_book(self):
        book = self._put(self.book_src, self.pb / "archive" / "PB-0001-alpha.yaml")
        self._put(self.book_src, self.pb / "legacy" / "PB-0001-alpha.md")
        self.assertEqual(vp.validate_file(book, None), (0, []))

    def test_a_fixture_outside_any_tree_is_unaffected(self):
        loose = self._put(self.book_src, self.tmp / "loose" / "PB-0001-alpha.yaml")
        self._put(self.book_src, self.tmp / "loose" / "PB-0001-alpha-copy.yaml")
        self.assertEqual(vp.validate_file(loose, None), (0, []))

    def test_an_adr_duplicate_does_not_fail_a_book(self):
        """A duplicate ADR is audit-docs' and the release gate's finding. It says
        nothing about a promptbook, so a book validated beside it stays valid."""
        book = self._put(self.book_src, self.pb / "active" / "PB-0001-alpha.yaml")
        adrs = self.tmp / "bionic" / "adrs"
        adrs.mkdir(parents=True)
        (adrs / "ADR-0003-one.md").write_text("x\n", encoding="utf-8")
        (adrs / "ADR-0003-two.md").write_text("x\n", encoding="utf-8")
        self.assertTrue(record_numbers.find_duplicate_numbers(self.tmp / "bionic"),
                        "control: the tree does hold a duplicate ADR")
        self.assertEqual(vp.validate_file(book, None), (0, []))

    def test_another_books_duplicate_does_not_fail_this_book(self):
        """The finding rides on the records that share the number, not on every
        book in the tree."""
        book = self._put(self.book_src, self.pb / "active" / "PB-0001-alpha.yaml")
        self._put(self.book_src, self.pb / "active" / "PB-0002-beta.yaml")
        self._put(self.book_src, self.pb / "archive" / "PB-0002-beta-copy.yaml")
        self.assertEqual(vp.validate_file(book, None), (0, []))
        code, errors = vp.validate_file(self.pb / "active" / "PB-0002-beta.yaml", None)
        self.assertEqual(code, 1, "control: a book holding the duplicate still fails")
        self.assertTrue(any("PB-0002" in e["error"] for e in errors), errors)

    def test_the_command_line_exits_one_and_names_the_duplicate(self):
        book = self._seed_duplicate_books()
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = vp.main([str(book)])
        self.assertEqual(code, 1)
        self.assertIn("PB-0001", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
