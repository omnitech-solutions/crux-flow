"""A rule citation resolves by the surface it sits on.

rule:citation-resolves-by-its-citing-surface.

WHY THESE TESTS EXIST. The first implementation built ONE merged slug map and gave every surface
the reader-first reading. Three consequences, all reachable in any project holding a `crux/`
directory and none of them visible in the authoring checkout, where the shipped catalog is a
projection of the same tree the lint already reads:

  * a reader's own `<repo>/crux/catalog/rules.json` REPLACED the shipped catalog outright, so a
    project could redefine a rule inside the plugin's own instructions by minting the same slug;
  * a slug both sides held resolved silently, so a substitution and a deliberate override were
    indistinguishable;
  * nothing anywhere inspected where the citing file lived, which is the clause's central rule.

Each test below is the control for one of those. They read no `bionic/` path and build their own
project roots, so they RUN against the staged artifact rather than skipping there — a skip is no
evidence about the thing that ships.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = SCRIPTS.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "_citation_surface_probe", SCRIPTS / "lint-governs-references.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LINT = _load()

def _catalog_text() -> str:
    """The shipped catalog's bytes, or a labelled assertion rather than a bare
    FileNotFoundError. Same reason as the sibling suite: with the catalog removed these
    tests must go red saying WHICH condition fired, not as an unlabelled traceback that
    reads like a broken test."""
    path = PLUGIN_ROOT / "catalog" / "rules.json"
    if not path.is_file():
        raise AssertionError(f"{path} is absent — a shipped citation resolves against nothing")
    return path.read_text(encoding="utf-8")


READER_ADR = """---
id: ADR-0001
title: Reader owns this
status: Accepted
date: 2026-09-14
governs:
  - domain: objectives
    rule: {rule}
    scope: this project
    handle: ADR-0001/{slug}
    provenance: authored
---
# Reader owns this
"""


def _reader_project(tmp: Path, slug: str, rule: str, own_catalog: dict | None = None) -> Path:
    """A project that installed the plugin: its own tree, its own pages, no plugin source."""
    (tmp / "bionic" / "adrs").mkdir(parents=True, exist_ok=True)
    (tmp / "pages").mkdir(exist_ok=True)
    (tmp / ".bionic.yml").write_text("docs_dir: bionic\n", encoding="utf-8")
    (tmp / "bionic" / "manifest.yml").write_text(
        "schema_version: 6\nconcerns_enabled: [adrs]\n", encoding="utf-8")
    (tmp / "bionic" / "adrs" / "ADR-0001-reader-owns-this.md").write_text(
        READER_ADR.format(rule=rule, slug=slug), encoding="utf-8")
    if own_catalog is not None:
        (tmp / "crux" / "catalog").mkdir(parents=True)
        (tmp / "crux" / "catalog" / "rules.json").write_text(
            json.dumps({"schema": "1", "rules": own_catalog, "unresolved": []}),
            encoding="utf-8")
    return tmp


class SurfaceKeyingTests(unittest.TestCase):
    def test_a_shipped_file_is_a_shipped_surface(self):
        self.assertTrue(LINT.is_shipped_surface(SCRIPTS / "lint-governs-references.py"))

    def test_a_readers_own_crux_directory_is_not_a_shipped_surface(self):
        """A project cannot make its pages shipped by naming a folder `crux`."""
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "crux" / "skills" / "SKILL.md"
            fake.parent.mkdir(parents=True)
            fake.write_text("rule:whatever\n", encoding="utf-8")
            self.assertFalse(LINT.is_shipped_surface(fake))

    def test_the_catalog_is_read_from_beside_this_script_only(self):
        """The control for the shadowing defect, driven through the real call path."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _reader_project(
                Path(tmp), "objectives-read-before-work", "The reader's own text.",
                own_catalog={"totally-made-up-by-the-reader": {"rule": "R", "domain": "x"}})
            (root / "pages" / "p.md").write_text(
                "rule:totally-made-up-by-the-reader\n", encoding="utf-8")
            rc = LINT.main(["--repo-root", str(root), "--path", str(root / "pages")])
        # The reader's file at the path the old code preferred defines this slug. If it
        # were still consulted the citation would resolve and rc would be 0.
        self.assertEqual(rc, 1, "a reader-owned catalog still shadows the shipped one")

    def test_a_slug_both_sides_hold_with_different_text_is_reported(self):
        """rule:citation-resolves-by-its-citing-surface — reported, not silent."""
        catalog = json.loads(_catalog_text())
        slug = "objectives-read-before-work"
        self.assertIn(slug, catalog["rules"], "the fixture slug left the shipped catalog")
        with tempfile.TemporaryDirectory() as tmp:
            root = _reader_project(Path(tmp), slug, "Text that differs from the plugin's.")
            (root / "pages" / "p.md").write_text(f"rule:{slug}\n", encoding="utf-8")
            rc = LINT.main(["--repo-root", str(root), "--path", str(root / "pages")])
        self.assertEqual(rc, 1, "a collision was not reported")

    def test_identical_text_on_both_sides_is_not_a_collision(self):
        """The authoring checkout holds every catalog slug on both sides by construction.

        The catalog IS a projection of this tree, so reporting that as a collision would
        make every authoring run red against itself and teach the reader to ignore the
        line. One rule reachable two ways is neither a substitution nor an override.
        """
        catalog = json.loads(_catalog_text())
        slug = "objectives-read-before-work"
        same = catalog["rules"][slug]["rule"]
        with tempfile.TemporaryDirectory() as tmp:
            root = _reader_project(Path(tmp), slug, same)
            (root / "pages" / "p.md").write_text(f"rule:{slug}\n", encoding="utf-8")
            rc = LINT.main(["--repo-root", str(root), "--path", str(root / "pages")])
        self.assertEqual(rc, 0, "identical text on both sides was reported as a collision")

    def test_one_slug_two_surfaces_two_verdicts(self):
        """The keying itself, end to end: same slug, same run, opposite outcomes.

        The reader's record defines the slug and the shipped catalog does not. A
        citation on a reader-owned surface therefore resolves, and the identical
        citation on a shipped surface is a finding, because the reader's record cannot
        answer for a shipped instruction. Nothing here re-implements the selection: the
        two verdicts come out of one `main` call over both files.

        `SHIPPED_ROOT` is relocated to a temporary plugin so the test can own a file on
        each side of the boundary without writing into the real plugin tree. The
        selection logic under test is the shipped one, unmodified.
        """
        slug = "a-rule-only-the-reader-defines"
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            # INSIDE the project root: a path outside it is refused by the
            # containment check before any resolution happens, and a refusal exits 1
            # on its own — which is what made the first version of this test pass
            # against a mutant that had no surface keying at all.
            plugin = tmp / "proj" / "vendor" / "plugin"
            (plugin / "catalog").mkdir(parents=True)
            (plugin / "skills").mkdir()
            # A catalog that is present and does NOT carry the slug.
            (plugin / "catalog" / "rules.json").write_text(
                json.dumps({"schema": "1", "unresolved": [],
                            "rules": {"some-other-shipped-rule":
                                      {"rule": "Shipped text.", "domain": "d"}}}),
                encoding="utf-8")
            shipped_page = plugin / "skills" / "SKILL.md"
            shipped_page.write_text(f"rule:{slug}\n", encoding="utf-8")

            root = _reader_project(tmp / "proj", slug, "The reader's own rule text.")
            reader_page = root / "pages" / "p.md"
            reader_page.write_text(f"rule:{slug}\n", encoding="utf-8")

            original = LINT.SHIPPED_ROOT
            try:
                LINT.SHIPPED_ROOT = plugin.resolve()
                rc = LINT.main(["--repo-root", str(root),
                                "--path", str(reader_page), "--path", str(shipped_page)])
            finally:
                LINT.SHIPPED_ROOT = original

        self.assertEqual(rc, 1, "the shipped-surface citation was not a finding")
        # And the control for THAT: with the boundary removed the same two files
        # agree, so the differing verdict above comes from the surface and nothing else.
        with tempfile.TemporaryDirectory() as tmp2:
            root2 = _reader_project(Path(tmp2), slug, "The reader's own rule text.")
            page = root2 / "pages" / "p.md"
            page.write_text(f"rule:{slug}\n", encoding="utf-8")
            rc2 = LINT.main(["--repo-root", str(root2), "--path", str(page)])
        self.assertEqual(rc2, 0, "the reader-owned citation should resolve")


class CatalogUsabilityTests(unittest.TestCase):
    """An ABSENT catalog and a BROKEN one are different conditions.

    Collapsing them reverted the shipped reading to reader-first AND disabled the
    collision report at once, with no diagnostic anywhere — so a truncated download or
    a bad merge silently restored the substitution this file's other tests forbid.
    """

    def _probe(self, catalog_bytes: str | None, layout: str = "vendored") -> dict:
        """Run the lint with a relocated plugin root holding `catalog_bytes`.

        `layout` places the plugin where the two readings differ: "authoring" puts it
        at exactly `<root>/crux`, which is where this project's own regenerator will
        project a catalog; "vendored" puts it elsewhere under the root, which is what
        an installed project looks like. The default is "vendored" because that is the
        reader this suite is about.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            plugin = ((tmp / "proj" / "crux") if layout == "authoring"
                      else (tmp / "proj" / "vendor" / "plugin"))
            (plugin / "catalog").mkdir(parents=True)
            if catalog_bytes is not None:
                (plugin / "catalog" / "rules.json").write_text(catalog_bytes, encoding="utf-8")
            root = _reader_project(tmp / "proj", "objectives-read-before-work",
                                   "READER MEANING WHICH IS NOT THE PLUGIN MEANING.")
            page = root / "pages" / "p.md"
            page.write_text("rule:objectives-read-before-work\n", encoding="utf-8")
            original = LINT.SHIPPED_ROOT
            buf = io.StringIO()
            try:
                LINT.SHIPPED_ROOT = plugin.resolve()
                with contextlib.redirect_stdout(buf):
                    LINT.main(["--repo-root", str(root), "--path", str(page)])
            finally:
                LINT.SHIPPED_ROOT = original
            return json.loads(buf.getvalue())

    GOOD = json.dumps({"schema": "1", "rules": {
        "objectives-read-before-work": {"rule": "Plugin meaning.", "domain": "objectives"}}})

    def test_a_present_catalog_reports_the_collision(self):
        self.assertEqual(len(self._probe(self.GOOD)["collisions"]), 1)

    def test_an_absent_catalog_falls_back_quietly(self):
        """An older plugin or partial checkout carries none; that is not the reader's
        fault and must not turn the gate red."""
        out = self._probe(None)
        self.assertEqual(out["refusals"], [])

    def test_a_corrupt_catalog_is_refused_not_treated_as_absent(self):
        out = self._probe("not json{")
        self.assertTrue(any(r["reason"] == "catalog_unusable" for r in out["refusals"]),
                        "a corrupt catalog was silently treated as absent")

    def test_an_empty_catalog_is_usable_not_refused(self):
        """These are the exact bytes `build_catalog` emits for a plugin citing nothing.

        Refusing them made the project's own regenerator able to produce a state its
        own lint rejected forever, for a condition nobody could fix. Emptiness is not
        the signal — absence is, and truncation is caught structurally.
        """
        out = self._probe(json.dumps({"schema": "1", "rules": {}}))
        self.assertEqual([r for r in out["refusals"] if r["reason"] == "catalog_unusable"], [])

    def test_a_catalog_missing_its_rules_key_is_refused(self):
        out = self._probe(json.dumps({"schema": "1"}))
        self.assertTrue(any(r["reason"] == "catalog_unusable" for r in out["refusals"]))

    def test_a_non_mapping_catalog_ENTRY_is_refused(self):
        """Strictness one level down: a string or null entry resolved a shipped
        citation to empty rule text with no diagnostic."""
        for bad in ("just a string", None):
            with self.subTest(entry=bad):
                out = self._probe(json.dumps(
                    {"schema": "1", "rules": {"objectives-read-before-work": bad}}))
                self.assertTrue(
                    any(r["reason"] == "catalog_unusable" for r in out["refusals"]),
                    f"a {type(bad).__name__} catalog entry was tolerated")

    def _shipped_citation_probe(self, catalog_bytes: str | None) -> dict:
        """Cite a reader-defined slug from a file at a SHIPPED location.

        The question is whether the reader's projection can answer for a shipped
        citation. It must not, for any catalog that is PRESENT — usable or not.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            plugin = tmp / "proj" / "vendor" / "plugin"
            (plugin / "catalog").mkdir(parents=True)
            (plugin / "skills").mkdir()
            if catalog_bytes is not None:
                (plugin / "catalog" / "rules.json").write_text(catalog_bytes, encoding="utf-8")
            shipped_page = plugin / "skills" / "SKILL.md"
            shipped_page.write_text("rule:objectives-read-before-work\n", encoding="utf-8")
            root = _reader_project(tmp / "proj", "objectives-read-before-work",
                                   "READER MEANING WHICH IS NOT THE PLUGIN MEANING.")
            original = LINT.SHIPPED_ROOT
            buf = io.StringIO()
            try:
                LINT.SHIPPED_ROOT = plugin.resolve()
                with contextlib.redirect_stdout(buf):
                    LINT.main(["--repo-root", str(root), "--path", str(shipped_page)])
            finally:
                LINT.SHIPPED_ROOT = original
            return json.loads(buf.getvalue())

    def test_a_present_but_unusable_catalog_keeps_the_strict_shipped_reading(self):
        """Loud AND strict, not merely loud.

        Keying the fallback on an EMPTY MAP rather than on presence meant a broken
        install still resolved shipped citations against the reader's projection — the
        refusal made the run red, but a reader who ignored it got the substitution back.
        """
        out = self._shipped_citation_probe("not json{")
        self.assertTrue(any(r["reason"] == "catalog_unusable" for r in out["refusals"]))
        self.assertEqual(
            [f["reason"] for f in out["rule_findings"]], ["unknown"],
            "the reader's projection answered for a shipped citation")

    def test_a_present_but_empty_catalog_keeps_the_strict_shipped_reading(self):
        """An empty catalog is usable and defines nothing, so it answers nothing."""
        out = self._shipped_citation_probe(json.dumps({"schema": "1", "rules": {}}))
        self.assertEqual(
            [f["reason"] for f in out["rule_findings"]], ["unknown"],
            "the reader's projection answered for a shipped citation")

    def test_an_absent_catalog_is_the_one_lane_that_falls_back(self):
        """The documented exception, pinned so it cannot silently widen."""
        out = self._shipped_citation_probe(None)
        self.assertEqual(out["rule_findings"], [])
        self.assertEqual(out["refusals"], [])

    def test_a_non_mapping_rules_value_is_refused(self):
        out = self._probe(json.dumps({"schema": "1", "rules": []}))
        self.assertTrue(any(r["reason"] == "catalog_unusable" for r in out["refusals"]))


class CollisionKindTests(unittest.TestCase):
    def test_a_symlink_at_a_shipped_location_is_shipped(self):
        """The boundary the sibling scanner refuses outright must not read as
        reader-owned here: one boundary, two readers, two answers was the defect."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            plugin = tmp / "plugin" / "skills"
            plugin.mkdir(parents=True)
            outside = tmp / "elsewhere.md"
            outside.write_text("rule:whatever\n", encoding="utf-8")
            link = plugin / "PROBE.md"
            link.symlink_to(outside)
            original = LINT.SHIPPED_ROOT
            try:
                LINT.SHIPPED_ROOT = (tmp / "plugin").resolve()
                self.assertTrue(LINT.is_shipped_surface(link),
                                "a file AT a shipped location took the reader-owned reading")
            finally:
                LINT.SHIPPED_ROOT = original

    def test_the_layout_selects_the_kind_end_to_end(self):
        """The discriminator itself, not just the branch it picks.

        The branch was covered by the hand-built call below; the code CHOOSING the
        branch was not, so forcing `authoring` to either constant left every test in
        this file green. These two cases drive `main` and read `kind` off the payload.
        A vendored plugin is a reader, not an authoring checkout — naming the
        regenerator there names a remedy that does nothing in that project.
        """
        probe = CatalogUsabilityTests()
        good = json.dumps({"schema": "1", "rules": {
            "objectives-read-before-work": {"rule": "Plugin meaning.", "domain": "objectives"}}})
        vendored = probe._probe(good, layout="vendored")["collisions"]
        authoring = probe._probe(good, layout="authoring")["collisions"]
        self.assertEqual([c["kind"] for c in vendored], ["competing_definition"])
        self.assertEqual([c["kind"] for c in authoring], ["stale_catalog"])

    def test_an_authoring_checkout_names_a_stale_catalog_rather_than_a_collision(self):
        """Here the catalog is DERIVED from this projection, so differing text means
        stale, not that two authorities disagree."""
        tree = {"objectives-read-before-work": "ADR-0116/objectives-read-before-work"}
        resolver = {"ADR-0116/objectives-read-before-work": {"rule": "Tree text."}}
        catalog = {"objectives-read-before-work": "Stale text."}
        authoring = LINT.catalog_collisions(tree, catalog, resolver, authoring=True)
        downstream = LINT.catalog_collisions(tree, catalog, resolver, authoring=False)
        self.assertEqual(authoring[0]["kind"], "stale_catalog")
        self.assertEqual(downstream[0]["kind"], "competing_definition")
        self.assertIn("generate-rules-catalog.py", authoring[0]["reason"])


if __name__ == "__main__":
    unittest.main()
