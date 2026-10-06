"""The plugin's two `schema_version` axes are not the same number.

WHY THIS EXISTS. `crux/plugin.json` carries the SKILL.md FRONTMATTER contract version;
`<tree>/manifest.yml` carries the TREE LAYOUT version. `install-docs-skills` told the
reader to extract `schema_version` from the installed `plugin.json` and call it "the
tree schema version this plugin supports", then compare it to a consuming project's
`manifest.yml` value under a rule whose "tree above supported" branch says STOP.

The two axes have never held the same number. Through the whole 3.x line the plugin
field read "3" while every tree read "5", so a reader following that skill told every
consumer with an existing tree that their tree was written by a newer plugin and that
they should stop -- on an install path that was otherwise fine. The fix points the
skill at the shipped `templates/manifest.yml.tmpl`, which is the manifest `init-docs`
writes and therefore, by construction, the layout this plugin operates on.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1].parent
SKILL = PLUGIN_ROOT / "skills" / "install-docs-skills" / "SKILL.md"
DETAIL = SKILL.parent / "references" / "install-and-upgrade.md"
TEMPLATE = PLUGIN_ROOT / "templates" / "manifest.yml.tmpl"
MANIFEST = PLUGIN_ROOT / "plugin.json"


def _template_tree_schema() -> str:
    m = re.search(r'^schema_version:\s*"([^"]+)"', TEMPLATE.read_text(encoding="utf-8"), re.M)
    assert m, "the shipped manifest template carries no schema_version"
    return m.group(1)


class SchemaAxisTests(unittest.TestCase):
    def test_the_shipped_template_declares_the_supported_tree_schema(self):
        """The skill's input must exist and be readable from the plugin alone."""
        self.assertTrue(TEMPLATE.is_file(), f"{TEMPLATE} must ship with the plugin")
        self.assertTrue(_template_tree_schema())

    def test_the_skill_reads_the_tree_schema_from_the_template_not_the_manifest(self):
        self.assertIn("references/install-and-upgrade.md", SKILL.read_text(encoding="utf-8"))
        text = DETAIL.read_text(encoding="utf-8")
        self.assertIn("templates/manifest.yml.tmpl", text,
                      "the skill must name the template as the supported-tree-value source")
        self.assertNotIn("the tree schema version this plugin supports", text,
                         "plugin.json's schema_version is the frontmatter contract, not the tree axis")

    def test_the_two_axes_are_allowed_to_differ(self):
        """The regression itself: a reader that conflates them decides STOP.

        This is not an assertion that they DO differ -- it is the control showing that
        reading the wrong field produces the wrong verdict whenever they do, which is
        every release so far.
        """
        frontmatter = json.loads(MANIFEST.read_text(encoding="utf-8"))["schema_version"]
        tree = _template_tree_schema()
        if frontmatter == tree:
            self.skipTest("the two axes happen to hold the same number in this release; "
                          "the conflation is undetectable here and the prose test above "
                          "is what holds the contract")
        self.assertNotEqual(
            frontmatter, tree,
            "if these ever converge, the prose test above is the only remaining guard")
        # Reading the frontmatter axis as the tree axis picks the STOP branch.
        self.assertGreater(
            int(tree), int(frontmatter),
            "the historical direction: a consumer's tree reads ABOVE the frontmatter "
            "number, which is the branch that tells them to stop")
