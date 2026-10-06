"""Every rule citation the plugin ships resolves for the reader it ships to.

rule:citation-resolves-by-its-citing-surface, rule:shipped-claims-are-tested-against-the-staged-artifact.

WHY THIS FILE READS NO `bionic/` PATH. The release stages the plugin directory and six root files
and excludes this project's own decision tree. A check that needs that tree SKIPS against the
staged artifact, and a skip is no evidence about the thing that ships — that is exactly how the
one pre-existing test asking this question reported green while measuring nothing. So every
assertion here reads only shipped surfaces plus a fresh project the test builds itself, and the
suite therefore runs where `sync.sh` runs it rather than skipping.

The negative control is the point of the file: removing a genuinely-cited slug from a copy of the
catalog must turn the same comparison red, or the greens above are vacuous.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
CATALOG = PLUGIN_ROOT / "catalog" / "rules.json"

CITATION_RE = re.compile(r"rule:([a-z][a-z0-9-]*)")
SCAN_ROOTS = ("skills", "agents", "templates", "scripts")
EXCLUDED_PARTS = ("tests", "catalog", "__pycache__")
TEXT_SUFFIXES = {".md", ".tmpl", ".py", ".yaml", ".yml", ".json", ".toml", ".sh", ".txt"}


def _cited(root: Path) -> dict[str, list[str]]:
    """slug -> the shipped files citing it."""
    out: dict[str, list[str]] = {}
    for sub in SCAN_ROOTS:
        base = root / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
                continue
            if any(p in EXCLUDED_PARTS for p in path.relative_to(root).parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for slug in CITATION_RE.findall(text):
                out.setdefault(slug, []).append(str(path.relative_to(root)))
    return out


def _catalog_slugs(catalog_path: Path) -> set[str]:
    if not catalog_path.is_file():
        return set()
    return set(json.loads(catalog_path.read_text(encoding="utf-8")).get("rules", {}))


def _catalog_text() -> str:
    """The catalog's bytes, or a labelled failure rather than a raw FileNotFoundError.

    A missing catalog must FAIL here (clause 3 forbids skipping), but it should say
    which condition fired: an unlabelled traceback reads as a broken test rather than
    as the absent artifact it reports.
    """
    if not CATALOG.is_file():
        raise AssertionError(
            f"{CATALOG} is absent — a shipped citation resolves against nothing")
    return CATALOG.read_text(encoding="utf-8")


class ShippedCitationsResolveTests(unittest.TestCase):
    def test_the_catalog_ships(self):
        """A missing catalog FAILS rather than skipping: its absence is the defect."""
        self.assertTrue(
            CATALOG.is_file(),
            f"{CATALOG} is absent — a shipped citation then resolves against nothing",
        )

    def test_every_shipped_citation_resolves_against_the_shipped_catalog(self):
        have = _catalog_slugs(CATALOG)
        cited = _cited(PLUGIN_ROOT)
        self.assertTrue(cited, "no citations found — the scan measured nothing")
        unresolved = {s: f for s, f in cited.items() if s not in have}
        self.assertEqual(
            unresolved, {},
            f"{len(unresolved)} shipped citation(s) resolve against nothing the plugin ships: "
            f"{sorted(unresolved)[:6]}",
        )

    def test_a_fresh_project_can_resolve_them_without_this_projects_tree(self):
        """The reader's condition: a project with the plugin and no decision tree of its own."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            (proj / "src").mkdir()
            (proj / "src" / "app.py").write_text("# a project that installed the plugin\n")
            self.assertFalse((proj / "bionic").exists())

            # Copy the plugin's catalog to a plugin root reachable FROM this project and
            # resolve through that copy, so the fixture is load-bearing rather than
            # decorative: the assertion fails if the project cannot reach a catalog.
            plugin_here = proj / "plugin" / "catalog"
            plugin_here.mkdir(parents=True)
            (plugin_here / "rules.json").write_text(_catalog_text(), encoding="utf-8")
            have = _catalog_slugs(plugin_here / "rules.json")
            self.assertTrue(have, "the catalog reachable from the project defines no rule")

            for slug in sorted(_cited(PLUGIN_ROOT)):
                self.assertIn(
                    slug, have,
                    f"rule:{slug} is unresolvable from a project holding only the plugin",
                )

    def test_the_catalog_names_no_decision_record(self):
        """rule:shipped-catalog-is-derived-and-names-no-decision-record.

        `ADR-NNNN` is the documented placeholder spelling and is a non-token by design,
        so the assertion is on the four-digit identifier only.
        """
        raw = _catalog_text()
        self.assertEqual(
            re.findall(r"ADR-\d{4}", raw), [],
            "the shipped catalog names a decision record a reader cannot open",
        )

    def test_positive_control_a_catalog_missing_a_cited_slug_is_caught(self):
        """The control for the assertion above, driven through the same call path.

        An earlier version of this control inserted a key into a local dict and asserted
        the key was absent from the catalog. It re-implemented the comparison it was meant
        to guard and could not fail — it passed even with the catalog deleted outright.
        This one removes a genuinely-cited slug from a COPY of the catalog and requires the
        same `_catalog_slugs` + comparison the guarded test uses to report it.
        """
        cited = _cited(PLUGIN_ROOT)
        self.assertTrue(cited, "no citations found — the control has nothing to remove")
        victim = sorted(cited)[0]

        with tempfile.TemporaryDirectory() as tmp:
            crippled = Path(tmp) / "rules.json"
            data = json.loads(_catalog_text())
            self.assertIn(victim, data["rules"], "the victim slug is not in the catalog")
            del data["rules"][victim]
            crippled.write_text(json.dumps(data), encoding="utf-8")

            have = _catalog_slugs(crippled)
            unresolved = {s: f for s, f in cited.items() if s not in have}

        self.assertIn(
            victim, unresolved,
            "removing a cited slug from the catalog was not reported — "
            "the resolution assertions in this file are vacuous",
        )


if __name__ == "__main__":
    unittest.main()
