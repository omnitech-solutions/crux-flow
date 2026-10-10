"""A governing reader that stops on a migration citation-source refusal names its remedy.

Once a tree has published a clause migration, the governing readers re-run the citation-source
scan. Its two refusals carry a path-free remedy beside the code. Each reader prints that remedy
as one `{"remedy": ...}` line on stderr; every byte it printed before is unchanged.
PublishedTreeTests drive the real refusal: a fixture publishes, writes and commits its
projections, then gains a tracked linked skill directory or an unstaged deletion.
ReaderRemedyTests stub each reader's build step to pin its generic handler.
"""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _council_gate_support as sup
import implementation_migration as migration
import test_implementation_migration_shared_reads as shared

SCRIPTS = Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(
        "reader_remedy_" + name.replace("-", "_"), SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_main(module, argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = module.main(argv)
    return code, out.getvalue(), err.getvalue().splitlines()


def assert_remedy_last(case, err, remedy):
    # The prefix first: a missing line then fails as a FAIL, never as a JSON decode ERROR.
    case.assertTrue(err and err[-1].startswith('{"remedy": '), err)
    case.assertEqual(json.loads(err[-1]), {"remedy": remedy})


def refusal(code="migration-source-unreadable", remedy=migration.ABSENT_SOURCE_REMEDY):
    return migration._with_remedy(migration.Refused(code), remedy)


class ReaderRemedyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def run_main(self, module, argv):
        return run_main(module, argv)

    def assert_remedy_last(self, err, remedy=migration.ABSENT_SOURCE_REMEDY):
        assert_remedy_last(self, err, remedy)

    def test_compile_doctrine_prints_the_remedy(self):
        module = load("compile-doctrine")
        with mock.patch.object(module, "build", side_effect=refusal()):
            code, out, err = self.run_main(module, ["--dry-run", "--repo-root", self.root])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(err[0], "compile-doctrine: Refused: migration-source-unreadable")
        self.assert_remedy_last(err)

    def test_summarize_adrs_build_refusal_prints_the_remedy(self):
        module = load("summarize-adrs")
        with mock.patch.object(module, "build", side_effect=refusal(
                "migration-symlink-refused", migration.SYMLINK_SOURCE_REMEDY)), \
             mock.patch.object(module.sp, "read_manifest", return_value={}), \
             mock.patch.object(module.sp, "declared_domain_refusal", return_value=None):
            code, out, err = self.run_main(module, ["--dry-run", "--repo-root", self.root])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(err[0], "summarize-adrs: Refused: migration-symlink-refused")
        self.assert_remedy_last(err, migration.SYMLINK_SOURCE_REMEDY)

    def test_authority_view_state_prints_the_remedy_and_keeps_stdout(self):
        module = load("authority-view")
        with mock.patch.object(module, "_state", side_effect=refusal()):
            code, out, err = self.run_main(module, ["state", "--repo-root", self.root])
        self.assertEqual(code, 1)
        self.assertEqual(out, '{"authority": "none", "limit": "migration-source-unreadable"}\n')
        self.assertEqual(len(err), 1)
        self.assert_remedy_last(err)

    def test_a_refusal_without_a_remedy_prints_no_remedy_line(self):
        # Positive discrimination: the line comes from the refusal, not from the reader.
        module = load("authority-view")
        with mock.patch.object(module, "_state", side_effect=migration.Refused("migration-outside-pilot")):
            code, out, err = self.run_main(module, ["state", "--repo-root", self.root])
        self.assertEqual(code, 1)
        self.assertEqual(err, [])


CHANGELOG = "# Changelog\n\n## [3.10.0] — 2026-08-01\n\nrule:rotation\n"


class PublishedTreeTests(unittest.TestCase):
    """A real published tree: the refusal reaches each reader through its own path."""
    setUp = shared.CitationKinds.setUp
    prepare = shared.CitationKinds.prepare
    complete_governs = shared.CitationKinds.complete_governs

    def publish_and_project(self):
        self.complete_governs()
        self.prepare(CHANGELOG)
        self.f.close()
        self.f.publish()
        self.assertEqual(migration.authority_view(self.root)["state"], "published")
        for name in ("summarize-adrs", "compile-doctrine"):
            code, out, err = run_main(load(name), ["--repo-root", str(self.root)])
            self.assertEqual(code, 0, (name, out, err))
        meta = json.loads((self.root / "docs/adrs/summaries/_meta.json").read_text())
        self.assertIn("implementation-migration", meta["input_domain"])
        sup.commit_all(self.root, "projections")
        for name in ("summarize-adrs", "compile-doctrine"):    # positive control: clean, no remedy
            code, out, err = run_main(load(name), ["--dry-run", "--repo-root", str(self.root)])
            self.assertEqual((code, [line for line in err if "remedy" in line]), (0, []), (name, out, err))

    def assert_readers_name(self, code, remedy):
        summarize = run_main(load("summarize-adrs"), ["--dry-run", "--repo-root", str(self.root)])
        self.assertEqual(summarize[:2], (2, ""))
        self.assertEqual(summarize[2][0], "summarize-adrs: refusing to rewrite: _meta.json declares "
                         f"migration inputs but their proof refuses: {code}")
        assert_remedy_last(self, summarize[2], remedy)
        doctrine = run_main(load("compile-doctrine"), ["--dry-run", "--repo-root", str(self.root)])
        self.assertEqual(doctrine[:2], (2, ""))
        assert_remedy_last(self, doctrine[2], remedy)
        view = run_main(load("authority-view"), ["state", "--repo-root", str(self.root)])
        self.assertEqual(view[:2], (1, '{"authority": "none", "limit": "%s"}\n' % code))
        assert_remedy_last(self, view[2], remedy)

    def test_tracked_linked_skill_directory_names_the_symlink_remedy(self):
        self.publish_and_project()
        outside = self.root.parent / "outside-skill"
        outside.mkdir()
        (outside / "SKILL.md").write_text("# Skill\n")
        (self.root / ".agents/skills").mkdir(parents=True, exist_ok=True)
        os.symlink(outside, self.root / ".agents/skills/demo")
        sup.git(self.root, "add", "-f", ".agents/skills/demo")
        sup.commit_all(self.root, "linked skill")
        self.assert_readers_name("migration-symlink-refused", migration.SYMLINK_SOURCE_REMEDY)

    def test_unstaged_skill_deletion_names_the_absent_source_remedy(self):
        self.publish_and_project()
        path = self.root / ".claude/skills/demo/SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("# Skill\n")
        sup.git(self.root, "add", "-f", ".claude/skills/demo/SKILL.md")
        sup.commit_all(self.root, "skill")
        path.unlink()
        self.assert_readers_name("migration-source-unreadable", migration.ABSENT_SOURCE_REMEDY)


if __name__ == "__main__":
    unittest.main()
