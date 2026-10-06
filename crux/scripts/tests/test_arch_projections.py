"""ADR-0079 clauses 1-3 — the two spine files that project a gated artifact.

`api-surface.md`'s skill inventory and `decision-index.md`'s accepted-decision
list stopped re-parsing their sources and now read the artifact the tree already
regenerates and gates: `crux/catalog/skills.json` and `<docs_dir>/adrs/index.md`.
Three behaviours carry the decision, and each has tests here.

**Clause 1 — one derivation per fact.** The projection must be a refactor, not a
change of answer. The pair of `test_*_agrees_with_the_source_path` tests below
proves that by running BOTH paths over one fixture tree and comparing the rows,
so the equivalence is a property of the deriver rather than an observation about
this repository on one afternoon.

**Clause 2 — fail-closed on a stale input.** A drifted input raises
`StaleProjectionInput` out of the extractor, which escapes `_build` before
`derive` writes or `dry_run` compares. `test_a_stale_*_refuses` pins the raise;
`test_a_refusal_leaves_the_spine_on_disk_unchanged` pins the consequence the
clause actually cares about.

**Clause 3 — an uncheckable input disqualifies itself.** Absent, field-omitting,
unparseable, or check-unrunnable all route to the source of truth, and the
rendered file names which input it used. Fail-OPEN is correct here and only
here, because the fallback IS the source of truth.

**Why every test builds its own tree.** These assertions must hold in the staged
public artifact, which ships `crux/` without the `bionic/` dogfood tree. A test
that read the ambient repository would fail there, and a `skipUnless` would make
it inert — the outcome ADR-0078 clause 3 forbids. So each fixture writes the
world it examines. The one thing taken from the ambient installation is the
deriver itself, which is the subject.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
D = importlib.import_module("crux.arch.derive")

# ADR-0096 clause 12 split the engine, and `crux.arch.derive` is now a re-export
# facade. Reading a name through it still works; PATCHING one through it does
# not, because a re-export is a separate binding and the reader never sees the
# replacement. So a monkeypatch must name the module that actually reads the
# value. This binding is that module.
CORE = importlib.import_module("crux.arch.core")

try:
    import yaml  # noqa: F401
    HAS_YAML = True
except ImportError:                                       # pragma: no cover
    HAS_YAML = False

# PyYAML is an interpreter capability, not a repository surface — which is why a
# skip on it is legitimate where a skip on an absent tree is not. The ADR index's
# regenerator imports it unconditionally, and the arch drift gate already
# preflights `import yaml` and refuses to run without it, because the two parse
# paths compute different spine hashes. Tests that need that regenerator inherit
# the same requirement; tests that do not are left unconditional.
_NEEDS_YAML = "the ADR index regenerator requires PyYAML (the gate preflights it)"


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


def _adr(root: Path, num: int, title: str, status: str, tags: str,
         archived: bool = False) -> None:
    d = root / "bionic" / "adrs" / ("archive" if archived else "")
    d.mkdir(parents=True, exist_ok=True)
    d.joinpath(f"ADR-{num:04d}-fixture.md").write_text(
        f"---\nid: ADR-{num:04d}\ntitle: \"{title}\"\nstatus: {status}\n"
        f"date: 2026-01-0{num % 9 + 1}\ntags: [{tags}]\n---\n\n"
        f"# ADR-{num:04d} — {title}\n",
        encoding="utf-8",
    )


def _write_clean_catalog(root: Path) -> Path:
    """Write `crux/catalog/skills.json` exactly as its regenerator would.

    Generated rather than hand-authored so "clean" is true by construction and a
    later change to the serializer cannot leave these fixtures silently drifted.
    """
    vc = D._vendored("validate-catalog.py", "_test_proj_catalog")
    assert vc is not None, "crux's own validate-catalog.py must be loadable"
    entries, errors = vc.regenerate_skills_json(root / "crux", False)
    assert not errors, f"fixture skills are invalid: {errors}"
    cat = root / "crux" / "catalog" / "skills.json"
    cat.parent.mkdir(parents=True, exist_ok=True)
    cat.write_text(vc.serialize_skills_json(entries), encoding="utf-8")
    return cat


def _write_clean_adr_index(root: Path) -> Path:
    """Write `bionic/adrs/index.md` exactly as its regenerator would."""
    gai = D._vendored("generate-adr-index.py", "_test_proj_adr_index")
    assert gai is not None, "crux's own generate-adr-index.py must be loadable"
    path, text = gai.build(root)
    path.write_text(text, encoding="utf-8")
    return path


def _rows(markdown: str) -> list[str]:
    """The table rows of a spine file, i.e. everything except its prose."""
    return [ln for ln in markdown.splitlines() if ln.startswith("|")]


def _provenance(markdown: str) -> str:
    """The italic first prose line, which names the input the file used."""
    return next(ln for ln in markdown.splitlines() if ln.startswith("_"))


class _Fixture(unittest.TestCase):
    """A tree the crux pack's two projected concerns both populate."""

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
        _skill(self.root, "alpha-skill", "Does the alpha thing, at some length. " + "x" * 120)
        (self.root / "bionic").mkdir(parents=True, exist_ok=True)
        (self.root / "bionic" / "manifest.yml").write_text(
            'schema_version: "5"\nadr:\n  next_number: 9\n', encoding="utf-8")
        _adr(self.root, 1, "Accept a thing", "Accepted", "load-bearing")
        _adr(self.root, 2, "Propose a thing", "Proposed", "process")
        _adr(self.root, 3, "Accept another thing", "Accepted", "process")

    def tearDown(self):
        self._tmp.cleanup()


# ─────────────────────────── api-surface ────────────────────────────────────


class ApiSurfaceProjectionTests(_Fixture):

    def test_the_projection_agrees_with_the_source_path_row_for_row(self):
        """Clause 1 — one derivation per fact, and the same answer either way.

        The strongest form available: both paths run over one tree, and every
        rendered row must match. Only the provenance line may differ, because
        naming the input it used is the one thing the two paths must NOT share.
        """
        derived, _ = D.extract_api_surface(self.root, "bionic")
        _write_clean_catalog(self.root)
        projected, _ = D.extract_api_surface(self.root, "bionic")
        self.assertEqual(_rows(derived), _rows(projected),
                         "projection changed the answer, not just the parser")
        self.assertTrue(_rows(projected), "fixture rendered no rows — vacuous")
        self.assertNotEqual(_provenance(derived), _provenance(projected),
                            "both paths claim the same input")

    def test_the_projected_file_names_the_artifact_it_read(self):
        _write_clean_catalog(self.root)
        md, sources = D.extract_api_surface(self.root, "bionic")
        self.assertIn("crux/catalog/skills.json", _provenance(md))
        self.assertIn("Projected", _provenance(md))
        self.assertIn("crux/catalog/skills.json", sources)

    def test_an_absent_catalog_falls_back_and_says_so(self):
        """Clause 3 — the postcondition that arch needs no artifact a repo lacks."""
        md, sources = D.extract_api_surface(self.root, "bionic")
        self.assertIn("SKILL.md", _provenance(md))
        self.assertNotIn("crux/catalog/skills.json", sources)
        self.assertTrue(_rows(md), "the fallback produced no rows")

    def test_a_stale_catalog_refuses_rather_than_projecting_it(self):
        """Clause 2 — the input's own drift check decides, and drift means refuse."""
        cat = _write_clean_catalog(self.root)
        entries = json.loads(cat.read_text(encoding="utf-8"))
        entries[0]["description"] = "a description no SKILL.md carries"
        cat.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
        with self.assertRaises(D.StaleProjectionInput) as ctx:
            D.extract_api_surface(self.root, "bionic")
        self.assertIn("crux/catalog/skills.json", str(ctx.exception),
                      "the refusal must name the input")

    def test_a_validation_error_in_the_input_is_also_unclean(self):
        """`validate-catalog --dry-run` refuses on a validation error too, and so
        does this: a catalog whose regeneration cannot be trusted is not clean
        merely because the bytes happen to match."""
        _write_clean_catalog(self.root)
        bad = self.root / "crux" / "skills" / "alpha-skill" / "SKILL.md"
        bad.write_text(bad.read_text(encoding="utf-8").replace(
            'risk_level: "low"', 'risk_level: "extremely-high"'), encoding="utf-8")
        with self.assertRaises(D.StaleProjectionInput):
            D.extract_api_surface(self.root, "bionic")

    def test_an_unrunnable_check_falls_back_instead_of_refusing(self):
        """Clause 3, and the direction of its failure.

        An absent check leaves freshness unknown; the source of truth settles
        it. Refusing here would take a derive down over a missing optional
        dependency, which is why the code fails open on exactly this branch and
        nowhere else.
        """
        _write_clean_catalog(self.root)
        with mock.patch.object(CORE, "_vendored", return_value=None):
            md, sources = D.extract_api_surface(self.root, "bionic")
        self.assertIn("SKILL.md", _provenance(md))
        self.assertNotIn("crux/catalog/skills.json", sources)

    def test_a_field_omitting_input_disqualifies_itself(self):
        """Clause 1's postcondition: an input missing a field the spine file needs
        falls to clause 3 rather than narrowing the spine file.

        Unreachable through drift — a catalog missing `description` is not what
        the regenerator produces, so it would refuse first. The guard exists for
        the day the regenerator's own output shape changes, and this is the only
        way to reach it: a stub regenerator whose output matches a field-less
        file on disk.
        """
        cat = self.root / "crux" / "catalog" / "skills.json"
        cat.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps([{"id": "alpha-skill", "name": "alpha-skill"}], indent=2) + "\n"
        cat.write_text(text, encoding="utf-8")

        class _Stub:
            @staticmethod
            def regenerate_skills_json(plugin_dir, verbose):
                return [{"id": "alpha-skill", "name": "alpha-skill"}], []

            @staticmethod
            def serialize_skills_json(entries):
                return text

        with mock.patch.object(CORE, "_vendored", return_value=_Stub):
            md, sources = D.extract_api_surface(self.root, "bionic")
        self.assertIn("SKILL.md", _provenance(md), "a field-less input was projected")
        self.assertNotIn("crux/catalog/skills.json", sources)

    def test_the_assertion_runs_cruxs_own_regenerator_not_the_examined_repos(self):
        """Clause 2's execution boundary — the property ADR-0075 gates.

        The crux stack pack fires precisely on a repo that carries `crux/skills`
        and `crux/schemas`, so the examined repo can carry a `crux/scripts/`
        of its own. Resolving the regenerator through `root` would execute it.
        This fixture plants one that raises on import; the projection must
        succeed anyway, which is only possible if it was never loaded.
        """
        _write_clean_catalog(self.root)
        booby = self.root / "crux" / "scripts"
        booby.mkdir(parents=True, exist_ok=True)
        booby.joinpath("validate-catalog.py").write_text(
            "raise RuntimeError('the examined repo executed its own code')\n",
            encoding="utf-8")
        booby.joinpath("generate-adr-index.py").write_text(
            "raise RuntimeError('the examined repo executed its own code')\n",
            encoding="utf-8")
        md, sources = D.extract_api_surface(self.root, "bionic")
        self.assertIn("Projected", _provenance(md))
        self.assertIn("crux/catalog/skills.json", sources)
        self.assertFalse(
            D._VENDORED_SCRIPTS.is_relative_to(self.root),
            "the vendored scripts dir resolved inside the examined repo",
        )


# ───────────────────────── decision-index ───────────────────────────────────


@unittest.skipUnless(HAS_YAML, _NEEDS_YAML)
class DecisionIndexProjectionTests(_Fixture):

    def test_the_projection_agrees_with_the_source_path_row_for_row(self):
        derived, _ = D.extract_decision_index(self.root, "bionic", "complete")
        _write_clean_adr_index(self.root)
        projected, _ = D.extract_decision_index(self.root, "bionic", "complete")
        self.assertEqual(_rows(derived), _rows(projected),
                         "projection changed the answer, not just the parser")
        self.assertTrue(_rows(projected), "fixture rendered no rows — vacuous")
        self.assertNotEqual(_provenance(derived), _provenance(projected))

    def test_the_projected_file_names_the_artifact_it_read(self):
        _write_clean_adr_index(self.root)
        md, sources = D.extract_decision_index(self.root, "bionic", "complete")
        self.assertIn("adrs/index.md", _provenance(md))
        self.assertIn("projected", _provenance(md))
        self.assertIn("bionic/adrs/index.md", sources)

    def test_only_accepted_active_decisions_are_listed(self):
        """Clause 1's postcondition — the projected index still excludes archived
        decisions, which the ADR index carries in a roster of its own, and still
        excludes a Proposed decision the active table does carry."""
        _adr(self.root, 4, "Retire a thing", "Superseded", "process", archived=True)
        _write_clean_adr_index(self.root)
        md, _ = D.extract_decision_index(self.root, "bionic", "complete")
        self.assertIn("Accept a thing", md)
        self.assertIn("Accept another thing", md)
        self.assertNotIn("Propose a thing", md, "a Proposed decision was listed")
        self.assertNotIn("Retire a thing", md, "an archived decision was listed")
        self.assertIn("decisions (2)", md)

    def test_curated_mode_still_filters_on_the_projected_path(self):
        """Clause 1 names the tag set as the field the curated mode needs, and
        clause 1's postcondition makes an index without it disqualify itself.
        `ADR-0003` is tagged `process`, which classifies not-load-bearing, so the
        curated mode must drop it — reading the tags out of the index table."""
        _write_clean_adr_index(self.root)
        complete, _ = D.extract_decision_index(self.root, "bionic", "complete")
        curated, _ = D.extract_decision_index(self.root, "bionic", "curated")
        self.assertIn("Accept another thing", complete)
        self.assertNotIn("Accept another thing", curated,
                         "curated mode did not filter on the projected tags")
        self.assertIn("Accept a thing", curated, "curated mode dropped a load-bearing row")
        self.assertIn("load-bearing (curated)", _provenance(curated))
        self.assertIn("projected", _provenance(curated))

    def test_the_projection_reads_the_tags_out_of_the_index_table(self):
        """The mechanism behind the test above, asserted directly: the tag list
        each record carries comes from the index's `tags` cell, with the
        regenerator's `—` placeholder read as the empty set rather than as a tag
        named `—`."""
        _adr(self.root, 7, "An untagged thing", "Accepted", "")
        _write_clean_adr_index(self.root)
        records, rel, _ = D._adr_index_projection(self.root, "bionic")
        self.assertEqual(rel, "bionic/adrs/index.md")
        by_num = {num: tags for num, _t, _d, tags in records}
        self.assertEqual(by_num["0001"], ["load-bearing"])
        self.assertEqual(by_num["0003"], ["process"])
        self.assertEqual(by_num["0007"], [], "the `—` placeholder became a tag")
        self.assertNotIn("0002", by_num, "a Proposed decision entered the records")

    def test_a_stale_index_refuses_rather_than_projecting_it(self):
        idx = _write_clean_adr_index(self.root)
        idx.write_text(idx.read_text(encoding="utf-8").replace(
            "Accept a thing", "A title no ADR carries"), encoding="utf-8")
        with self.assertRaises(D.StaleProjectionInput) as ctx:
            D.extract_decision_index(self.root, "bionic", "complete")
        self.assertIn("ADR index", str(ctx.exception))

    def test_an_absent_index_falls_back_and_says_so(self):
        md, sources = D.extract_decision_index(self.root, "bionic", "complete")
        self.assertIn("derived from ADR frontmatter", _provenance(md))
        self.assertNotIn("bionic/adrs/index.md", sources)

    def test_an_unparseable_row_falls_back_rather_than_dropping_a_decision(self):
        """A title carrying an unescaped `|` is what the index regenerator
        produces, so the input is CLEAN and the row still cannot be split. The
        only safe reading is none: fall back to the source of truth rather than
        merge or drop a decision."""
        _adr(self.root, 6, "A pipe | in a title", "Accepted", "process")
        _write_clean_adr_index(self.root)
        md, sources = D.extract_decision_index(self.root, "bionic", "complete")
        self.assertIn("derived from ADR frontmatter", _provenance(md))
        self.assertNotIn("bionic/adrs/index.md", sources)
        # The fallback escapes the pipe on the way into its own table, which the
        # index regenerator does not — so the decision survives, rendered safely.
        self.assertIn(r"A pipe \| in a title", md, "the fallback lost the decision")

    def test_footnote_only_citation_survives_the_projection(self):
        """ADR-0060's rule, re-checked on the new path: no inline ADR number
        before the footnote definitions."""
        _write_clean_adr_index(self.root)
        md, _ = D.extract_decision_index(self.root, "bionic", "complete")
        head = md.split("[^d1]:")[0]
        self.assertNotRegex(head, r"ADR-\d{4}", "inline ADR number in the table")
        self.assertIn("[^d1]:", md)


# ──────────────────── the refusal reaches the engine ────────────────────────


@unittest.skipUnless(HAS_YAML, _NEEDS_YAML)
class RefusalReachesTheEngineTests(_Fixture):

    def test_a_refusal_leaves_the_spine_on_disk_unchanged(self):
        """Clause 2's real requirement. The raise is the mechanism; THIS is the
        property — a refusing derive must not have half-written a tree, and a
        refusing `--dry-run` must not report a verdict."""
        pkg = self.root / "crux" / "scripts" / "crux"
        (pkg / "core").mkdir(parents=True, exist_ok=True)
        pkg.joinpath("__init__.py").write_text("from crux.core import t\n", encoding="utf-8")
        pkg.joinpath("core", "__init__.py").write_text("", encoding="utf-8")
        pkg.joinpath("core", "t.py").write_text("V = 1\n", encoding="utf-8")
        _write_clean_catalog(self.root)
        _write_clean_adr_index(self.root)

        D.derive(self.root, "bionic")
        arch = self.root / "bionic" / "arch"
        before = {p: p.read_bytes() for p in sorted(arch.rglob("*")) if p.is_file()}
        self.assertTrue(before, "the fixture derive wrote nothing")

        idx = arch.parent / "adrs" / "index.md"
        idx.write_text(idx.read_text(encoding="utf-8").replace(
            "Accept a thing", "A title no ADR carries"), encoding="utf-8")

        with self.assertRaises(D.StaleProjectionInput):
            D.derive(self.root, "bionic")
        with self.assertRaises(D.StaleProjectionInput):
            D.dry_run(self.root, "bionic")

        after = {p: p.read_bytes() for p in sorted(arch.rglob("*")) if p.is_file()}
        self.assertEqual(before, after, "a refused derive still touched the spine")

    def test_the_refusal_is_a_capability_failure_at_the_cli(self):
        """The driver's exit lanes: 0 clean, 1 drift, 2 capability. A refusal is
        the third, so a caller never reads it as a document finding."""
        import subprocess
        _write_clean_catalog(self.root)
        _write_clean_adr_index(self.root)
        (self.root / ".bionic.yml").write_text(
            'config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8")
        D.derive(self.root, "bionic")
        idx = self.root / "bionic" / "adrs" / "index.md"
        idx.write_text(idx.read_text(encoding="utf-8").replace(
            "Accept a thing", "A title no ADR carries"), encoding="utf-8")
        driver = Path(D.__file__).resolve().parents[2] / "derive-arch.py"
        proc = subprocess.run(
            [sys.executable, str(driver), "--dry-run", "--repo-root", str(self.root)],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 2,
                         f"refusal did not land on the capability lane:\n{proc.stderr}")
        self.assertIn("StaleProjectionInput", proc.stderr)


class RowSplitterTests(unittest.TestCase):
    """`_split_md_row` refuses shapes it does not implement rather than guessing.

    A splitter that guessed would silently drop or merge a decision, which is
    the one failure a decision index must not have.
    """

    SEVEN = "| a | b | c | d | e | f | g |"

    def test_it_splits_the_declared_column_count(self):
        self.assertEqual(D._split_md_row(self.SEVEN),
                         ["a", "b", "c", "d", "e", "f", "g"])

    def test_it_refuses_a_row_with_too_many_cells(self):
        self.assertIsNone(D._split_md_row("| a | b | c | d | e | f | g | h |"))

    def test_it_refuses_a_row_with_too_few_cells(self):
        self.assertIsNone(D._split_md_row("| a | b |"))

    def test_it_refuses_a_line_that_is_not_a_row(self):
        self.assertIsNone(D._split_md_row("# ADRs"))
        self.assertIsNone(D._split_md_row("| unterminated"))

    def test_the_declared_column_count_matches_the_regenerators_header(self):
        """Anti-drift: the constant this module splits on is the ADR index's own
        header, so a column added there fails here rather than silently
        disqualifying every index in the world."""
        self.assertEqual(len(D._ADR_INDEX_COLUMNS), 7)
        self.assertIn("tags", D._ADR_INDEX_COLUMNS)
        gai = D._vendored("generate-adr-index.py", "_test_proj_hdr")
        self.assertIsNotNone(gai)
        header = gai.render([], []).splitlines()[2]
        self.assertEqual(D._split_md_row(header), list(D._ADR_INDEX_COLUMNS))


# ───────────── the two indexes a tree can carry (the F1 regression) ──────────


# Byte-for-byte the shape `init-docs/SKILL.md` specifies for a new tree, and the
# shape `propose-adr` maintains on every append: a `_Last updated:` line, and no
# `## Archived (N)` roster. Written as a literal rather than generated, because
# the point is that it is NOT what any regenerator produces.
_INIT_DOCS_INDEX = (
    "# ADRs\n"
    "\n"
    "_Last updated: 2026-01-02_\n"
    "\n"
    "| id | title | status | date | supersedes | superseded_by | tags |\n"
    "|----|-------|--------|------|------------|---------------|------|\n"
    "| ADR-0001 | Accept a thing | Accepted | 2026-01-02 | — | — | load-bearing |\n"
)


def _write_init_docs_shaped_adr_index(root: Path) -> Path:
    path = root / "bionic" / "adrs" / "index.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_INIT_DOCS_INDEX, encoding="utf-8")
    return path


class AdrIndexShapeTests(unittest.TestCase):
    """`_adr_index_shape` / `_index_is_regenerator_shaped` in isolation.

    The predicate decides between two outcomes that a byte comparison alone
    conflates — refuse, or derive from source — so it gets its own unit tests
    rather than being exercised only through the extractor.
    """

    def test_row_content_is_not_shape(self):
        """Staleness lives in the rows, so rows must not enter the signature."""
        a = "# ADRs\n\n| a | b |\n\n## Archived (0)\n"
        b = "# ADRs\n\n| totally | different |\n| and | longer |\n\n## Archived (0)\n"
        self.assertEqual(D._adr_index_shape(a), D._adr_index_shape(b))

    def test_the_archived_count_is_not_shape(self):
        """Archiving an ADR without re-running the regenerator is staleness, not
        a different kind of document, so the count is normalized away."""
        self.assertEqual(D._adr_index_shape("## Archived (0)\n"),
                         D._adr_index_shape("## Archived (12)\n"))

    def test_blank_line_variance_is_not_shape(self):
        self.assertEqual(D._adr_index_shape("# ADRs\n"), D._adr_index_shape("# ADRs\n\n\n"))

    def test_a_stray_prose_line_IS_shape(self):
        """The discriminator that closes the regression: `_Last updated:` is a
        line no regenerator emits, so its presence changes the shape."""
        self.assertNotEqual(D._adr_index_shape("# ADRs\n\n## Archived (0)\n"),
                            D._adr_index_shape("# ADRs\n\n_Last updated: x_\n\n## Archived (0)\n"))

    def test_a_missing_archived_roster_IS_shape(self):
        self.assertNotEqual(D._adr_index_shape("# ADRs\n\n## Archived (0)\n"),
                            D._adr_index_shape("# ADRs\n"))

    def test_the_init_docs_shape_is_not_the_regenerator_shape(self):
        """The regression in one assertion, with no fixture tree in the way."""
        gai = D._vendored("generate-adr-index.py", "_test_shape_regen")
        self.assertIsNotNone(gai)
        want = gai.render([], [])
        self.assertFalse(D._index_is_regenerator_shaped(_INIT_DOCS_INDEX, want))
        self.assertTrue(D._index_is_regenerator_shaped(want, want))


@unittest.skipUnless(HAS_YAML, _NEEDS_YAML)
class FreshTreeDerivesCleanTests(_Fixture):
    """ADR-0079 clause 3's postcondition: "enabling arch in a target repo
    requires no artifact that repo lacks."

    This is the class the review found broken. `_adr_index_projection` compared
    the ADR index against its regenerator's output and refused on ANY
    difference, but `init-docs` writes a DIFFERENT SHAPE — a `_Last updated:`
    line and no archived roster — and `propose-adr` maintains that shape. So the
    first `derive-arch.py` in a freshly initialized repository exited 2, and arch
    is default-on for new repos, which put that refusal in front of every
    downstream reader on day one.

    The refusal itself is not the defect and must survive: a tree that DOES
    maintain its index with the regenerator, and whose index has fallen behind,
    is stale and clause 2 refuses it. Both halves are pinned here, because a fix
    that stopped refusing altogether would pass a test for the regression alone.
    """

    def test_a_fresh_init_docs_tree_derives_clean_via_the_fallback(self):
        """The regression, at the extractor."""
        _write_init_docs_shaped_adr_index(self.root)
        md, sources = D.extract_decision_index(self.root, "bionic", "complete")
        self.assertIn("derived from ADR frontmatter", _provenance(md))
        self.assertNotIn("bionic/adrs/index.md", sources)
        self.assertTrue(_rows(md), "the fallback rendered no rows")
        self.assertIn("Accept a thing", md, "the fallback lost a decision")

    def test_a_fresh_init_docs_tree_derives_clean_through_the_whole_engine(self):
        """The regression, at the level a user meets it: a full derive, not one
        extractor. `_build` is what `derive` and `dry_run` both call, and the
        refusal escaped it before either could write or compare."""
        _write_init_docs_shaped_adr_index(self.root)
        built = D._build(self.root, "bionic")
        self.assertTrue(built, "the derive produced no tree")
        self.assertIn("decision-index.md", built)
        self.assertIn("derived from ADR frontmatter", built["decision-index.md"])

    def test_a_fresh_init_docs_tree_exits_zero_at_the_cli(self):
        """The strongest form: the driver's own exit code, which is what broke.

        Exit 2 was the observed regression, so the assertion is on the exit code
        of a real subprocess rather than on an exception not being raised.
        """
        import subprocess
        _write_init_docs_shaped_adr_index(self.root)
        (self.root / ".bionic.yml").write_text(
            'config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8")
        driver = Path(D.__file__).resolve().parents[2] / "derive-arch.py"
        proc = subprocess.run(
            [sys.executable, str(driver), "--repo-root", str(self.root)],
            capture_output=True, text=True,
        )
        self.assertEqual(
            proc.returncode, 0,
            "a fresh init-docs tree could not derive:\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}",
        )

    def test_a_hand_maintained_index_is_disqualified_SILENTLY(self):
        """The asymmetry the fix chose, pinned so it is a decision and not an
        accident.

        A hand-maintained ADR index is the NORMAL state of a target repo, so it
        takes the same silent treatment `_disqualified` already gives an absent
        input. Warning on the normal path is what teaches a reader to ignore the
        channel, and the loud branches beside it would lose their meaning.
        """
        _write_init_docs_shaped_adr_index(self.root)
        with mock.patch.object(CORE, "_disqualified") as spy:
            D.extract_decision_index(self.root, "bionic", "complete")
        spy.assert_not_called()

    def test_a_regenerator_shaped_index_that_is_stale_STILL_refuses(self):
        """The other half. A fix that simply stopped refusing would pass every
        test above and lose clause 2 entirely."""
        idx = _write_clean_adr_index(self.root)
        idx.write_text(idx.read_text(encoding="utf-8").replace(
            "Accept a thing", "A title no ADR carries"), encoding="utf-8")
        with self.assertRaises(D.StaleProjectionInput):
            D.extract_decision_index(self.root, "bionic", "complete")

    def test_a_stale_archived_roster_still_refuses(self):
        """The count is normalized out of the SHAPE, and this proves that did not
        open a staleness hole: an index whose archived roster is out of date is
        still refused, because the byte comparison runs first."""
        idx = _write_clean_adr_index(self.root)
        idx.write_text(idx.read_text(encoding="utf-8").replace(
            "## Archived (0)", "## Archived (4)"), encoding="utf-8")
        with self.assertRaises(D.StaleProjectionInput):
            D.extract_decision_index(self.root, "bionic", "complete")


# ──────────────── guards that survived mutation before this pass ─────────────


@unittest.skipUnless(HAS_YAML, _NEEDS_YAML)
class ProvenanceRecordsTheAssertionsReadSetTests(_Fixture):
    """ADR-0078 clause 3, and the ONE place this implementation diverges from an
    ADR-0079 Consequences bullet on purpose.

    That bullet says "the spine's recorded sources become the projected
    artifacts rather than the underlying files". The recorded sources here are
    BOTH, because clause 3 of ADR-0078 checks the drift gate's trigger scope
    "against a read set a derive computes". Recording only the projected
    artifact would shrink that computed read set below the set of files the
    derive actually opens, and a derive input added inside the assertion's reach
    would then pass unnoticed.

    The divergence was unpinned: mutations deleting either provenance loop
    survived every suite. These tests are the pins. The property asserted is the
    one that matters — recorded sources are a SUPERSET of the files the
    staleness assertion opens — rather than an exact list, which would break on
    every unrelated fixture change.
    """

    def test_api_surface_records_every_skill_file_the_assertion_reads(self):
        _write_clean_catalog(self.root)
        _md, sources = D.extract_api_surface(self.root, "bionic")
        opened = {
            f"crux/skills/{p.parent.name}/SKILL.md"
            for p in (self.root / "crux" / "skills").glob("*/SKILL.md")
        }
        self.assertTrue(opened, "fixture has no skills — vacuous")
        self.assertIn("crux/catalog/skills.json", sources,
                      "the projected artifact itself went unrecorded")
        self.assertLessEqual(
            opened, set(sources),
            "the projection recorded the artifact but not the files its "
            "staleness assertion opened; ADR-0078 clause 3's computed read set "
            "is now smaller than what the derive reads",
        )

    def test_decision_index_records_every_adr_file_the_assertion_reads(self):
        """Both tiers, because the regenerator walks both to build the index."""
        _adr(self.root, 5, "An archived thing", "Superseded", "process", archived=True)
        _write_clean_adr_index(self.root)
        _md, sources = D.extract_decision_index(self.root, "bionic", "complete")
        adrs = self.root / "bionic" / "adrs"
        opened = {f"bionic/adrs/{p.name}" for p in adrs.glob("ADR-*.md")}
        archived = {f"bionic/adrs/archive/{p.name}"
                    for p in (adrs / "archive").glob("ADR-*.md")}
        self.assertTrue(opened and archived, "fixture lacks one tier — vacuous")
        self.assertIn("bionic/adrs/index.md", sources)
        self.assertLessEqual(opened | archived, set(sources),
                             "an ADR tier the assertion reads went unrecorded")

    def test_the_recorded_set_is_a_superset_and_not_a_replacement(self):
        """Anti-vacuity for the two tests above: the projected path must record
        strictly MORE than the artifact alone, or the superset assertions could
        be satisfied by a fixture with no source files."""
        _write_clean_catalog(self.root)
        _md, sources = D.extract_api_surface(self.root, "bionic")
        self.assertGreater(len(sources), 1,
                           "only the projected artifact was recorded")


@unittest.skipUnless(HAS_YAML, _NEEDS_YAML)
class HashesTheComparedBytesTests(_Fixture):
    """The TOCTOU fix, pinned by construction rather than by timing.

    Both projections used to hash their input with a SECOND `read_bytes()` after
    the drift check had already read the file as text, so the recorded hash could
    describe content the assertion never saw. The fix hashes
    `have.encode("utf-8")` — the bytes the comparison actually used.

    A clean tree cannot tell the two apart, which is why the fix survived
    mutation. These fixtures make them differ WITHOUT making the input dirty, by
    writing the file with CRLF line endings: `read_text` applies universal-newline
    translation, so `have` is LF and compares equal to the regenerator's output,
    while the file's bytes on disk are CRLF. The compared bytes and the on-disk
    bytes are therefore different, the input is still clean, and the recorded
    hash says which one was hashed.
    """

    @staticmethod
    def _to_crlf(path: Path) -> None:
        path.write_bytes(path.read_text(encoding="utf-8").replace("\n", "\r\n")
                         .encode("utf-8"))

    def test_the_catalog_hash_is_of_the_compared_bytes(self):
        cat = _write_clean_catalog(self.root)
        want = cat.read_text(encoding="utf-8")
        self._to_crlf(cat)
        self.assertNotEqual(cat.read_bytes(), want.encode("utf-8"),
                            "fixture failed to make the two byte strings differ")
        _md, sources = D.extract_api_surface(self.root, "bionic")
        self.assertEqual(
            sources["crux/catalog/skills.json"], D._sha256_hex(want.encode("utf-8")),
            "the recorded hash is not of the bytes the staleness check compared",
        )

    def test_the_adr_index_hash_is_of_the_compared_bytes(self):
        idx = _write_clean_adr_index(self.root)
        want = idx.read_text(encoding="utf-8")
        self._to_crlf(idx)
        self.assertNotEqual(idx.read_bytes(), want.encode("utf-8"),
                            "fixture failed to make the two byte strings differ")
        _md, sources = D.extract_decision_index(self.root, "bionic", "complete")
        self.assertEqual(
            sources["bionic/adrs/index.md"], D._sha256_hex(want.encode("utf-8")),
            "the recorded hash is not of the bytes the staleness check compared",
        )


@unittest.skipUnless(HAS_YAML, _NEEDS_YAML)
class UncheckedGuardTests(_Fixture):
    """Two guards in `_adr_index_projection` that no test reached.

    Both are unreachable through ordinary drift, which is why they survived
    mutation: each needs a regenerator whose output disagrees with the file on
    disk in a specific way, so each is reached with a stub.
    """

    def test_a_regenerator_aimed_at_another_tree_is_declined(self):
        """`generate-adr-index.py` resolves the tree name from `.bionic.yml`
        itself, so it can target a different file than this derive was asked
        for — a `--docs-dir` override, or a multi-tree checkout. A check aimed at
        another file proves nothing about this one."""
        idx = _write_clean_adr_index(self.root)
        text = idx.read_text(encoding="utf-8")
        elsewhere = self.root / "other-tree" / "adrs" / "index.md"

        class _Stub:
            @staticmethod
            def build(root):
                return elsewhere, text

        with mock.patch.object(CORE, "_vendored", return_value=_Stub):
            with mock.patch.object(CORE, "_disqualified") as spy:
                md, sources = D.extract_decision_index(self.root, "bionic", "complete")
        self.assertIn("derived from ADR frontmatter", _provenance(md))
        self.assertNotIn("bionic/adrs/index.md", sources)
        spy.assert_called_once()
        self.assertIn("different tree", spy.call_args[0][1],
                      "the disqualification did not name its reason")

    def test_a_reordered_header_is_disqualified_never_reinterpreted(self):
        """A column order the projection does not expect must disqualify rather
        than be read positionally — reading `status` out of the `date` cell would
        silently change which decisions the spine lists."""
        idx = self.root / "bionic" / "adrs" / "index.md"
        idx.parent.mkdir(parents=True, exist_ok=True)
        swapped = (
            "# ADRs\n\n"
            "| title | id | status | date | supersedes | superseded_by | tags |\n"
            "|----|-------|--------|------|------------|---------------|------|\n"
            "| Accept a thing | ADR-0001 | Accepted | 2026-01-02 | — | — | load-bearing |\n"
            "\n## Archived (0)\n\n| id | title | status |\n|----|-------|--------|\n"
        )
        idx.write_text(swapped, encoding="utf-8")

        class _Stub:
            @staticmethod
            def build(root):
                return idx, swapped

        with mock.patch.object(CORE, "_vendored", return_value=_Stub):
            with mock.patch.object(CORE, "_disqualified") as spy:
                md, sources = D.extract_decision_index(self.root, "bionic", "complete")
        self.assertIn("derived from ADR frontmatter", _provenance(md))
        self.assertNotIn("bionic/adrs/index.md", sources)
        spy.assert_called_once()
        self.assertIn("header", spy.call_args[0][1])


@unittest.skipUnless(HAS_YAML, _NEEDS_YAML)
class ValidationErrorIsReportedSeparatelyTests(_Fixture):
    """The `errors` branch, which survived mutation because the byte comparison
    beside it caught every case the suite exercised.

    `validate-catalog.py --dry-run` refuses on drift AND on a validation error,
    so both are unclean under clause 2 and both refuse. But a validation error
    can hold with the catalog's bytes BYTE-IDENTICAL, and the single message the
    two shared claimed the bytes differed and sent that caller to re-run a
    regenerator which rewrites nothing and reports the same error again. The
    remedy was a loop.
    """

    def test_a_validation_error_with_identical_bytes_still_refuses(self):
        """Deleting the `errors or` disjunct used to survive; this kills it.

        The stub returns entries that serialize to exactly the file on disk AND
        a non-empty error list, so the byte comparison passes and only the
        `errors` branch can refuse.
        """
        cat = self.root / "crux" / "catalog" / "skills.json"
        cat.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(
            [{"id": "a", "name": "a", "description": "d"}], indent=2) + "\n"
        cat.write_text(text, encoding="utf-8")

        class _Stub:
            @staticmethod
            def regenerate_skills_json(plugin_dir, verbose):
                return ([{"id": "a", "name": "a", "description": "d"}],
                        ["alpha-skill: risk_level is not one of low|medium|high"])

            @staticmethod
            def serialize_skills_json(entries):
                return text

        self.assertEqual(cat.read_text(encoding="utf-8"), text,
                         "fixture bytes are not identical — the test is vacuous")
        with mock.patch.object(CORE, "_vendored", return_value=_Stub):
            with self.assertRaises(D.StaleProjectionInput) as ctx:
                D.extract_api_surface(self.root, "bionic")
        msg = str(ctx.exception)
        self.assertIn("validation errors", msg,
                      "the refusal reported the wrong cause")
        self.assertNotIn("is not what its own regenerator produces", msg,
                         "the refusal falsely claimed the bytes differ")

    def test_the_two_causes_carry_different_remedies(self):
        """The finding was the MESSAGE, not the refusal, so the messages are the
        assertion: a validation error must not be told to regenerate and commit,
        because that is the loop."""
        cat = _write_clean_catalog(self.root)
        entries = json.loads(cat.read_text(encoding="utf-8"))
        entries[0]["description"] = "a description no SKILL.md carries"
        cat.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
        with self.assertRaises(D.StaleProjectionInput) as drift:
            D.extract_api_surface(self.root, "bionic")
        self.assertIn("is not what its own regenerator produces", str(drift.exception))
        self.assertIn("commit the result", str(drift.exception))


# ─────────────────────── table-cell escaping (S-b / S-c) ─────────────────────


class CellEscapingTests(unittest.TestCase):
    """The escapers, and the ordering defect that made the previous fix partial.

    Escaping `|` before `\\` leaves a double-escape hole: a value `a\\|b` came
    out as `a\\\\|b`, where the backslash escapes the backslash and the pipe is
    left bare, so the row splits regardless. Backslash must be escaped FIRST.
    """

    @staticmethod
    def _structural_pipes(text: str) -> int:
        """The pipes a Markdown table parser would treat as column delimiters.

        Escape pairs are consumed left to right — `\\\\` first, then `\\|` — which
        is exactly how a parser reads them. Whatever `|` survives is a real
        delimiter. Substring matching cannot express this: in the correct output
        `a\\\\\\|b` the three characters `\\`, `\\`, `|` DO appear as a substring,
        spanning the end of one escape pair and the start of the next.
        """
        return text.replace("\\\\", "").replace("\\|", "").count("|")

    def test_backslash_is_escaped_before_pipe(self):
        """The hole, stated as the property that closes it: a value carrying a
        literal backslash-pipe must leave no column delimiter behind."""
        for esc in (D._cell, D._prose_cell):
            out = esc(r"a\|b")
            self.assertEqual(out, r"a\\\|b",
                             f"{esc.__name__} escaped pipe before backslash")
            self.assertEqual(self._structural_pipes(out), 0,
                             f"{esc.__name__} left a column delimiter in the value")

    def test_the_wrong_order_would_leave_a_delimiter(self):
        """Anti-vacuity for the test above. The property must FAIL for the
        pipe-only escaping this code used to do, or it is not testing the
        ordering at all — it would pass for any implementation."""
        old_behaviour = r"a\|b".replace("|", "\\|")      # what the code did before
        self.assertEqual(old_behaviour, r"a\\|b")
        self.assertEqual(self._structural_pipes(old_behaviour), 1,
                         "the pipe-first order unexpectedly closed the hole, so "
                         "the ordering assertion above proves nothing")

    def test_a_bare_pipe_is_escaped_by_both(self):
        self.assertEqual(D._cell("a|b"), r"a\|b")
        self.assertEqual(D._prose_cell("a|b"), r"a\|b")

    def test_newline_and_carriage_return_are_flattened_by_both(self):
        for esc in (D._cell, D._prose_cell):
            self.assertNotIn("\n", esc("a\nb"))
            self.assertNotIn("\r", esc("a\rb"))
            self.assertNotIn("\r", esc("a\r\nb"))

    def test_only_cell_neutralizes_backtick(self):
        """The one difference, and the reason it exists. `_cell`'s value is
        wrapped in a code span, so a backtick would break out of it.
        `_prose_cell`'s value renders as markdown, where a backtick is
        legitimate inline code — 29 of the 51 skill descriptions carry it."""
        self.assertNotIn("`", D._cell("a `b` c"))
        self.assertIn("`", D._prose_cell("a `b` c"))


@unittest.skipUnless(HAS_YAML, _NEEDS_YAML)
class NameCellIsEscapedTests(_Fixture):
    """The name cell's escaping, which had no test at all.

    Nothing validates a skill `name` against a character class — `ALIAS_NAME_RE`
    governs model aliases — so a name carrying a pipe would have split its own
    table row. Reached through the projected path with a stub, because a name
    like this cannot come from a real SKILL.md the regenerator accepts.
    """

    def test_a_backslash_pipe_name_does_not_split_its_row(self):
        cat = self.root / "crux" / "catalog" / "skills.json"
        cat.parent.mkdir(parents=True, exist_ok=True)
        hostile = {"id": "x", "name": r"a\|b", "description": "harmless"}
        text = json.dumps([hostile], indent=2) + "\n"
        cat.write_text(text, encoding="utf-8")

        class _Stub:
            @staticmethod
            def regenerate_skills_json(plugin_dir, verbose):
                return [hostile], []

            @staticmethod
            def serialize_skills_json(entries):
                return text

        with mock.patch.object(CORE, "_vendored", return_value=_Stub):
            md, _sources = D.extract_api_surface(self.root, "bionic")
        row = next(ln for ln in md.splitlines()
                   if ln.startswith("|") and "harmless" in ln)
        # A two-column table row is `| a | b |`: exactly three STRUCTURAL pipes.
        # Every escaped pipe is removed first, so what remains is the row's real
        # column structure. `\\\|` is an escaped backslash then an escaped pipe,
        # and both must be gone for the count to be three.
        bare = row.replace("\\\\", "").replace("\\|", "")
        self.assertEqual(bare.count("|"), 3,
                         f"the hostile name split its own row: {row!r}")
        self.assertIn(r"a\\\|b", row, "the name cell was not escaped correctly")

    def test_a_description_pipe_does_not_split_its_row(self):
        """Two real skill descriptions carry a pipe today, so this path is live
        rather than hypothetical."""
        _write_clean_catalog(self.root)
        bad = self.root / "crux" / "skills" / "zeta-skill" / "SKILL.md"
        bad.write_text(bad.read_text(encoding="utf-8").replace(
            "Does the zeta thing.", "Does a | b thing."), encoding="utf-8")
        _write_clean_catalog(self.root)
        md, _ = D.extract_api_surface(self.root, "bionic")
        row = next(ln for ln in md.splitlines()
                   if ln.startswith("|") and "thing" in ln and "zeta" in ln)
        bare = row.replace("\\\\", "").replace("\\|", "")
        self.assertEqual(bare.count("|"), 3, f"a description pipe split its row: {row!r}")


@unittest.skipUnless(HAS_YAML, _NEEDS_YAML)
class PrefixedAdrIdTests(_Fixture):
    """§14.3's prefixed artifact ids, which the projection rejected outright.

    §14.3 defines `artifact_prefix`, and states that the prefixed string IS the
    id "verbatim on every surface", ADR frontmatter included. The projection
    matched `^ADR-(\\d+)$`, so every id in a prefixed repo failed to parse and
    the whole index disqualified itself — a silent downgrade to the fallback for
    any repo that set a prefix. `generate-adr-index.py` already tolerates both
    spellings, so the two disagreed.

    The fixture keeps the FILENAME bare and prefixes only the frontmatter `id`,
    which isolates exactly the defect that was fixed. The `ADR-*.md` glob is a
    separate, wider defect in the same area — it finds no prefixed ADR file at
    all — and is recorded as a named residual in `derive.py` rather than fixed
    here.
    """

    def _prefixed_adr(self, num: int, title: str) -> None:
        p = self.root / "bionic" / "adrs" / f"ADR-{num:04d}-fixture.md"
        p.write_text(
            f"---\nid: CRX-ADR-{num:04d}\ntitle: \"{title}\"\nstatus: Accepted\n"
            f"date: 2026-02-0{num % 9 + 1}\ntags: [load-bearing]\n---\n\n# x\n",
            encoding="utf-8")

    def test_a_prefixed_id_is_parsed_rather_than_disqualifying_the_index(self):
        self._prefixed_adr(8, "A prefixed decision")
        _write_clean_adr_index(self.root)
        with mock.patch.object(CORE, "_disqualified") as spy:
            md, sources = D.extract_decision_index(self.root, "bionic", "complete")
        spy.assert_not_called()
        self.assertIn("projected", _provenance(md),
                      "a prefixed id disqualified the whole index")
        self.assertIn("bionic/adrs/index.md", sources)
        self.assertIn("A prefixed decision", md)

    def test_the_footnote_carries_the_digits(self):
        self._prefixed_adr(8, "A prefixed decision")
        _write_clean_adr_index(self.root)
        records, _rel, _h = D._adr_index_projection(self.root, "bionic")
        self.assertIn("0008", [num for num, *_ in records])

    def test_the_id_regex_reads_both_spellings_and_refuses_neither_form(self):
        self.assertEqual(D._ADR_ID_RE.match("ADR-0012").group(1), "0012")
        self.assertEqual(D._ADR_ID_RE.match("CRX-ADR-0012").group(1), "0012")
        self.assertIsNone(D._ADR_ID_RE.match("PB-0012"))
        self.assertIsNone(D._ADR_ID_RE.match("ADR-0012-slug"))
        self.assertIsNone(D._ADR_ID_RE.match("not-an-id"))


class VendoredImportWritesNoBytecodeTests(unittest.TestCase):
    """`_vendored` must not cache bytecode beside the regenerator it imports.

    `exec_module` on a source file writes `__pycache__/` next to that file. The
    two regenerators live in `crux/scripts/`, inside the plugin directory, which
    is documented as ephemeral across updates and as holding no state — and a
    `--dry-run` that writes files contradicts the drift-gate comment saying it
    writes nothing. Stale bytecode has also decided a verdict in this repo
    before, which is the second reason to hold the guard with a test.

    Both tests force `sys.dont_write_bytecode` FALSE around the call ON PURPOSE.
    Every gate command here exports `PYTHONDONTWRITEBYTECODE=1`, so a test that
    inherited the ambient value would pass with the guard deleted and prove
    nothing.
    """

    def _fixture_home(self, stack) -> Path:
        home = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        (home / "vendored-fixture.py").write_text("VALUE = 1\n", encoding="utf-8")
        return home

    def test_importing_a_vendored_regenerator_leaves_no_pycache_beside_it(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            home = self._fixture_home(stack)
            prior = sys.dont_write_bytecode
            sys.dont_write_bytecode = False
            try:
                with mock.patch.object(CORE, "_VENDORED_SCRIPTS", home):
                    mod = D._vendored("vendored-fixture.py", "_crux_arch_bytecode_fixture")
            finally:
                sys.dont_write_bytecode = prior
            self.assertIsNotNone(mod, "the fixture regenerator did not load at all")
            self.assertEqual(mod.VALUE, 1)
            self.assertEqual(
                sorted(p.name for p in home.iterdir()), ["vendored-fixture.py"],
                "importing the regenerator wrote bytecode beside it")

    def test_the_flag_is_restored_even_when_the_import_raises(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            home = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            (home / "raising-fixture.py").write_text(
                "raise RuntimeError('boom')\n", encoding="utf-8")
            prior = sys.dont_write_bytecode
            sys.dont_write_bytecode = False
            try:
                with mock.patch.object(CORE, "_VENDORED_SCRIPTS", home):
                    self.assertIsNone(
                        D._vendored("raising-fixture.py", "_crux_arch_raising_fixture"),
                        "a raising regenerator must route to clause 3, not propagate")
                self.assertFalse(
                    sys.dont_write_bytecode,
                    "the process-wide flag leaked out of a failed import")
            finally:
                sys.dont_write_bytecode = prior


if __name__ == "__main__":
    unittest.main(verbosity=2)
