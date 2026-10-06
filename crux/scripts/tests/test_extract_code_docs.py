"""Tests for extract-code-docs.py.

Covers the regressions fixed in this pass:

  1. `_atomic_write_text` writes the requested bytes and uses os.replace
     (so the target either has the new content or the old, never partial).
  2. `build_manifest` no longer stamps a per-row `extracted_at` field.
  3. `write_pages` prune does NOT touch files outside `output_dir` (even
     when a symlink under output_dir points at an outside file).
  4. `write_pages` prune reports, and never deletes, any `.md` file under
     `output_dir` that is itself a symlink -- whether it resolves inside or
     outside the tree (ADR-0131 clause 14 D1, amended from the earlier
     behavior of unlinking an in-tree link while preserving its outside
     target: a link's target is never this dispatcher's page to have
     written, so the link is never this dispatcher's to delete either).

Stdlib only (unittest, tempfile, os, importlib, pathlib, json, sys).
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # crux repo
SCRIPT_PATH = REPO_ROOT / "crux" / "scripts" / "extract-code-docs.py"


def _load_dispatcher():
    """Import extract-code-docs.py as a module (the hyphen breaks regular import)."""
    spec = importlib.util.spec_from_file_location("extract_code_docs_dispatcher", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["extract_code_docs_dispatcher"] = mod
    spec.loader.exec_module(mod)
    return mod


dispatcher = _load_dispatcher()


class NoExtractorsIsNotACleanGateTests(unittest.TestCase):
    """A gate that inspected nothing must not read as a clean gate.

    THE DEFECT. With `code.extractors` empty this exited 0 with EMPTY stdout
    and one line on stderr. `check-drift` classifies a gate from its JSON
    payload, and there was none; a roster aggregator reading exit codes
    counted the row clean. A tree that had scanned no source at all therefore
    reported its code docs verified. Zero work performed and a measured zero
    are different outcomes, and only the second is evidence.

    THE FIX reuses the discriminator `check-drift` already keys N/A on --
    `surface_absent: true` -- rather than inventing a parallel signal, and
    carries a `reason` because the remedy differs per gate: here it is
    configuring `code.extractors`, not running a regenerator.

    Membership is untouched: the row stays on the roster. Only its verdict
    changes, from a silent clean to an explicit N/A.
    """

    def _tree(self, extractors_block: str) -> pathlib.Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        docs = root / "bionic"
        (docs / "code").mkdir(parents=True)
        (root / ".bionic.yml").write_text(
            'config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8")
        (docs / "manifest.yml").write_text(
            'schema_version: "5"\n' + extractors_block, encoding="utf-8")
        return root

    def _run(self, root: pathlib.Path):
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--dry-run",
             "--config", str(root / "bionic" / "manifest.yml")],
            capture_output=True, text=True)

    EMPTY = "code:\n  extractors: {}\n"

    def test_no_extractors_still_exits_zero(self):
        """Unchanged: an unconfigured tree is not an error."""
        self.assertEqual(self._run(self._tree(self.EMPTY)).returncode, 0)

    def test_no_extractors_emits_the_na_discriminator_on_stdout(self):
        r = self._run(self._tree(self.EMPTY))
        payload = json.loads(r.stdout)
        self.assertIs(payload["surface_absent"], True)
        self.assertIs(payload["drift"], False)
        self.assertIn("code.extractors", payload["reason"])

    def test_negative_control_the_old_shape_would_be_unclassifiable(self):
        """Proves the assertion above has teeth.

        The defect was EMPTY stdout at exit 0. If a future edit restored that,
        `json.loads` below would raise and this test would go red rather than
        silently passing on an exit code.
        """
        r = self._run(self._tree(self.EMPTY))
        self.assertTrue(r.stdout.strip(), "stdout must carry a payload, not be empty")
        json.loads(r.stdout)

    def _configured(self) -> pathlib.Path:
        root = self._tree(
            "code:\n  extractors:\n    any:\n      extractor: fallback\n"
            "      glob: \"src/**/*.py\"\n")
        (root / "src").mkdir()
        (root / "src" / "mod.py").write_text('"""A module."""\n', encoding="utf-8")
        return root

    def _run_at(self, root: pathlib.Path, *extra: str):
        """Pin `--output-dir` INSIDE the fixture.

        Without it the output dir resolves from the CWD's own `.bionic.yml`
        while the sources come from `--config`'s parent, so the run compares
        against a manifest that is not there, reports drift, and writes into
        whatever tree the test happens to be run from. That ambient coupling is
        why an earlier version of this test accepted `returncode in (0, 1)` and
        so could never assert the zero it is named for.
        """
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--config", str(root / "bionic" / "manifest.yml"),
             "--output-dir", str(root / "bionic" / "code"), *extra],
            capture_output=True, text=True)

    def test_a_measured_zero_is_not_the_absent_surface(self):
        """The distinction the repair must preserve, asserted as a real zero.

        A configured extractor that finds nothing to change INSPECTED its
        inputs and found them in sync. That is a measured zero and a real pass:
        exit 0, all counts zero, at least one page actually scanned, and NO
        `surface_absent`. A regression that set `surface_absent` whenever the
        counts came out zero would demote every genuine clean gate to N/A, and
        this is the case that catches it -- the earlier version could not,
        because its run reported drift rather than a zero.
        """
        root = self._configured()
        first = self._run_at(root)                    # write mode: materialize the surface
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self._run_at(root, "--dry-run")      # now a genuine no-change run
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        payload = json.loads(second.stdout)
        self.assertEqual((payload["added"], payload["changed"], payload["removed"]), (0, 0, 0))
        self.assertGreaterEqual(payload["pages_total"], 1,
                                "a measured zero must have measured at least one page")
        self.assertNotIn("surface_absent", payload,
                         "a configured extractor measured something; it is not an absent surface")

    def test_the_two_zeroes_are_distinguishable(self):
        """The whole point, stated as one comparison.

        Both runs exit 0 and both report zero added/changed/removed. Only the
        unconfigured one carries `surface_absent`, and only the configured one
        scanned a page. Exit code alone cannot tell them apart, which is why
        reading exit codes off this gate was the defect.
        """
        configured = self._configured()
        self.assertEqual(self._run_at(configured).returncode, 0)
        measured = json.loads(self._run_at(configured, "--dry-run").stdout)
        vacuous = json.loads(self._run(self._tree(self.EMPTY)).stdout)
        self.assertEqual(measured["added"], vacuous["added"])          # same counts
        self.assertNotIn("surface_absent", measured)                   # different meaning
        self.assertIs(vacuous["surface_absent"], True)
        self.assertGreaterEqual(measured["pages_total"], 1)
        self.assertEqual(vacuous["pages_total"], 0)


class AtomicWriteTextTests(unittest.TestCase):
    def test_writes_utf8_bytes_without_newline_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.txt"
            body = "alpha\nbeta\n— omega\n"  # includes a non-ASCII char
            dispatcher._atomic_write_text(target, body)
            # Read back as bytes to verify no '\r\n' got injected.
            self.assertEqual(target.read_bytes(), body.encode("utf-8"))

    def test_tmp_file_is_cleaned_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.txt"
            dispatcher._atomic_write_text(target, "hello")
            # No leftover .tmp sibling.
            sibs = sorted(p.name for p in Path(tmp).iterdir())
            self.assertEqual(sibs, ["out.txt"])

    def test_replaces_existing_file_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.txt"
            target.write_text("OLD", encoding="utf-8")
            dispatcher._atomic_write_text(target, "NEW")
            self.assertEqual(target.read_text(encoding="utf-8"), "NEW")

    def test_a_pre_created_tmp_symlink_is_refused(self):
        # [SECURITY:S5] The third of three byte-identical siblings, found by a
        # fail-closed sweep rather than by diff review — this file was not in
        # the diff that fixed the first one.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            victim = root / "victim.txt"
            victim.write_text("VICTIM\n", encoding="utf-8")
            target = root / "out.txt"
            (root / "out.txt.tmp").symlink_to(victim)
            with self.assertRaises(OSError):
                dispatcher._atomic_write_text(target, "GENERATED")
            self.assertEqual(victim.read_text(encoding="utf-8"), "VICTIM\n")
            self.assertFalse(target.exists())

    def test_a_target_that_is_already_a_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            victim = root / "victim.txt"
            victim.write_text("VICTIM\n", encoding="utf-8")
            target = root / "out.txt"
            target.symlink_to(victim)
            with self.assertRaises(OSError):
                dispatcher._atomic_write_text(target, "GENERATED")
            self.assertEqual(victim.read_text(encoding="utf-8"), "VICTIM\n")
            self.assertTrue(target.is_symlink())


class BuildManifestTests(unittest.TestCase):
    def test_no_extracted_at_in_rows(self):
        page = dispatcher.DocPage(
            title="Foo",
            path="elixir/Foo.md",
            body="# Foo\n",
            source_path="lib/foo.ex",
        )
        manifest = dispatcher.build_manifest([page])
        self.assertEqual(len(manifest["pages"]), 1)
        row = manifest["pages"][0]
        self.assertNotIn("extracted_at", row)
        # Sanity: the content-stable fields still exist.
        self.assertEqual(row["source_path"], "lib/foo.ex")
        self.assertEqual(row["doc_path"], "elixir/Foo.md")
        self.assertIn("sha256", row)
        self.assertIn("extractor_version", row)

    def test_manifest_is_byte_stable_across_calls(self):
        page = dispatcher.DocPage(
            title="Foo", path="elixir/Foo.md", body="# Foo\n", source_path="lib/foo.ex"
        )
        a = json.dumps(dispatcher.build_manifest([page]), sort_keys=True)
        b = json.dumps(dispatcher.build_manifest([page]), sort_keys=True)
        self.assertEqual(a, b)


class WritePagesPruneTests(unittest.TestCase):
    def test_prune_removes_stale_md_files_inside_output_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "code"
            out.mkdir()
            stale = out / "elixir" / "Old.md"
            stale.parent.mkdir(parents=True)
            stale.write_text("# stale\n", encoding="utf-8")

            page = dispatcher.DocPage(
                title="New", path="elixir/New.md", body="# new\n", source_path="lib/new.ex"
            )
            dispatcher.write_pages([page], out, verbose=False)

            self.assertFalse(stale.exists(), "stale .md inside output_dir should be pruned")
            self.assertTrue((out / "elixir" / "New.md").is_file())

    def test_prune_does_not_touch_files_outside_output_dir(self):
        """A symlink under output_dir pointing OUTSIDE must not cause unlink
        of the outside target -- and, per ADR-0131 clause 14 D1, the symlink
        itself is now reported and left standing rather than unlinked (an
        ADR-mandated change from the earlier behavior, which deleted the
        in-tree link while preserving its outside target)."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "code"
            out.mkdir()
            outside_dir = Path(tmp) / "outside"
            outside_dir.mkdir()
            outside_file = outside_dir / "DoNotDelete.md"
            outside_file.write_text("# precious\n", encoding="utf-8")

            # Place a symlink inside out/ that points at outside_file.
            link = out / "Linked.md"
            try:
                link.symlink_to(outside_file)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks not supported on this platform")

            page = dispatcher.DocPage(
                title="Keep", path="elixir/Keep.md", body="# keep\n", source_path="lib/keep.ex"
            )
            dispatcher.write_pages([page], out, verbose=False)

            # The outside target MUST still exist regardless of what we did
            # to the symlink.
            self.assertTrue(
                outside_file.exists(),
                "file outside output_dir must never be unlinked by the pruner",
            )
            self.assertEqual(
                outside_file.read_text(encoding="utf-8"),
                "# precious\n",
                "outside target contents must be untouched",
            )
            # ADR-mandated change (ADR-0131 clause 14 D1): the symlink itself
            # is now reported, never deleted -- it is not this run's page to
            # have written, and its target is not this dispatcher's to prune.
            self.assertTrue(
                link.is_symlink(),
                "the symlink under output_dir must be reported and left standing, not unlinked",
            )

    def test_prune_respects_resolved_kept_set_for_index_and_meta(self):
        """index.md and _meta/manifest.json under the output dir must not
        be pruned even though they aren't in the input page list."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "code"
            out.mkdir()
            meta_dir = out / "_meta"
            meta_dir.mkdir()
            (meta_dir / "manifest.json").write_text("{}", encoding="utf-8")
            (out / "index.md").write_text("# old index\n", encoding="utf-8")

            page = dispatcher.DocPage(
                title="X", path="elixir/X.md", body="# x\n", source_path="lib/x.ex"
            )
            dispatcher.write_pages([page], out, verbose=False)

            # write_pages regenerates index.md but does NOT touch manifest.json
            # (that's write_meta's job). Both must still exist post-prune.
            self.assertTrue((out / "index.md").is_file())
            self.assertTrue((meta_dir / "manifest.json").is_file())


class CrossRepoTargetSelectionTests(unittest.TestCase):
    """Source ownership and output ownership must be resolved from ONE config.

    THE DEFECT. From repository `victim`, `--config <foreign>/bionic/manifest.yml`
    with no `--output-dir` resolved the SOURCES from the foreign repository and
    the OUTPUT DEFAULT from the victim's own `.bionic.yml`. `write_pages` then
    pruned every page the victim legitimately owned -- they are absent from the
    foreign page set -- and wrote the foreign repository's pages in their place,
    at exit 0, reporting the deletions as an ordinary `removed` count. The
    foreign tree's own `code/` stayed empty: nothing about the run was visible
    where it belonged.

    THE FIX. An explicit `--config` supplies the output default from its own
    docs root, so one manifest owns both the sources scanned and the pages
    written. An explicit `--output-dir` still wins, and the tree it names is
    validated as the actual destructive target rather than the config's tree
    standing in for it.

    Every case here drives the real CLI as a subprocess against disposable
    trees, and the preservation cases fingerprint the victim's generated pages
    by content -- generated pages are untracked, so git status proves nothing
    about them.

    Three cases are the paired positive controls -- `..._relative_and_absolute_
    spellings_...`, `..._a_supported_explicit_output_dir_...` and
    `..._legitimate_pruning_...`. They stay GREEN against the pre-repair code,
    which is what stops the class from passing merely because the extractor
    stopped working. The other seven go RED against it. Measured, not assumed:
    pre-repair the class runs 7 failures, post-repair 0.
    """

    def _pair(self) -> tuple[pathlib.Path, pathlib.Path]:
        """Build a disposable victim + foreign pair, each a complete crux tree."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = pathlib.Path(tmp.name).resolve()
        made = []
        for name, module in (("victim", "alpha"), ("foreign", "zulu")):
            root = base / name
            (root / "bionic" / "code").mkdir(parents=True)
            (root / "src").mkdir()
            (root / ".bionic.yml").write_text(
                'config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8")
            (root / "bionic" / "manifest.yml").write_text(
                'schema_version: "5"\n'
                'code:\n  extractors:\n    any:\n      extractor: fallback\n'
                '      glob: "src/**/*.py"\n',
                encoding="utf-8")
            (root / "src" / f"{module}.py").write_text(
                f'"""The {name} module {module}."""\n', encoding="utf-8")
            made.append(root)
        return made[0], made[1]

    def _run(self, cwd: pathlib.Path, *argv: str):
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), *argv],
            cwd=str(cwd), capture_output=True, text=True)

    @staticmethod
    def _fingerprint(root: pathlib.Path) -> dict[str, str]:
        """Path -> sha256 for every file under `root`.

        Content, not mtime, and the key set is the inventory -- so a deletion,
        an addition and an in-place overwrite are each caught, and a run that
        rewrites a file to identical bytes is correctly NOT called a mutation.
        """
        return {
            str(f.relative_to(root)): dispatcher.sha256_text(f.read_text(encoding="utf-8"))
            for f in sorted(root.rglob("*")) if f.is_file()
        }

    def _populate(self, root: pathlib.Path) -> dict[str, str]:
        """Generate the tree's real pages, then fingerprint them."""
        r = self._run(root)
        self.assertEqual(r.returncode, 0, f"fixture setup failed: {r.stderr}")
        pages = root / "bionic" / "code"
        fp = self._fingerprint(pages)
        self.assertTrue(fp, "fixture must have real generated pages before the probe")
        return fp

    # ---------- the reproducer ----------

    def test_foreign_config_without_output_dir_preserves_every_victim_page(self):
        """THE ORIGINAL REPRODUCER. Nothing of the victim's may be deleted,
        overwritten, or supplemented with a foreign page."""
        victim, foreign = self._pair()
        before = self._populate(victim)
        self.assertIn("fallback/src/alpha.py.md", before)

        r = self._run(victim, "--config", str(foreign / "bionic" / "manifest.yml"))

        after = self._fingerprint(victim / "bionic" / "code")
        self.assertEqual(
            after, before,
            "the victim's generated pages must be byte-identical and no page "
            "added or removed after a run driven by a foreign config",
        )
        self.assertNotIn(
            "fallback/src/zulu.py.md", after,
            "a foreign page must never be written into the victim's tree",
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_foreign_config_writes_into_the_tree_that_owns_it(self):
        """The other half: the run is not merely blocked, it lands correctly.

        Without this, refusing every explicit config would pass the test above
        while destroying the flag.
        """
        victim, foreign = self._pair()
        self._populate(victim)

        r = self._run(victim, "--config", str(foreign / "bionic" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(
            (foreign / "bionic" / "code" / "fallback" / "src" / "zulu.py.md").is_file(),
            "the foreign config's pages belong in the foreign tree's code/ dir",
        )

    def test_relative_and_absolute_spellings_of_one_config_agree(self):
        """A path is not an identity. Both spellings must select one tree."""
        victim, _ = self._pair()
        self._run(victim, "--config", "bionic/manifest.yml")
        relative = self._fingerprint(victim / "bionic" / "code")

        for page in (victim / "bionic" / "code").rglob("*"):
            if page.is_file():
                page.unlink()
        self._run(victim, "--config", str(victim / "bionic" / "manifest.yml"))
        absolute = self._fingerprint(victim / "bionic" / "code")

        self.assertTrue(relative, "the relative spelling must produce pages at all")
        self.assertEqual(relative, absolute)

    def test_absolute_config_from_an_unrelated_cwd_targets_its_own_tree(self):
        """The cwd must not contribute a destination when --config is explicit."""
        victim, _ = self._pair()
        outside = victim.parent  # holds neither .bionic.yml nor a docs tree
        r = self._run(outside, "--config", str(victim / "bionic" / "manifest.yml"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(
            (victim / "bionic" / "code" / "fallback" / "src" / "alpha.py.md").is_file())

    def test_a_symlinked_config_resolves_to_the_tree_it_points_at(self):
        """The link's location must not select the output; its target must."""
        victim, foreign = self._pair()
        before = self._populate(victim)
        link = victim / "link-to-foreign.yml"
        try:
            link.symlink_to(foreign / "bionic" / "manifest.yml")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")

        r = self._run(victim, "--config", "link-to-foreign.yml")

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            self._fingerprint(victim / "bionic" / "code"), before,
            "a config reached through a symlink must not write into the link's tree",
        )
        self.assertTrue(
            (foreign / "bionic" / "code" / "fallback" / "src" / "zulu.py.md").is_file())

    # ---------- the destructive target ----------

    def test_explicit_output_dir_outside_any_crux_tree_is_refused(self):
        """The marker check must validate what gets PRUNED, not what was read.

        Before the repair it validated the config's tree and then pruned
        somewhere else entirely.
        """
        victim, _ = self._pair()
        before = self._populate(victim)
        scratch = victim.parent / "scratch" / "code"

        r = self._run(victim, "--output-dir", str(scratch))

        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("not inside a crux tree", r.stderr)
        self.assertFalse(scratch.exists(), "a refusal must precede every write")
        self.assertEqual(
            self._fingerprint(victim / "bionic" / "code"), before,
            "a refused run must leave the source tree byte-for-byte intact",
        )

    def test_a_supported_explicit_output_dir_inside_a_crux_tree_still_works(self):
        """POSITIVE CONTROL for the refusal above.

        Custom output locations are validated, not prohibited. Without this the
        refusal could be satisfied by banning --output-dir outright.
        """
        victim, _ = self._pair()
        r = self._run(victim, "--output-dir", "bionic/alt")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(
            (victim / "bionic" / "alt" / "fallback" / "src" / "alpha.py.md").is_file())

    def test_two_crux_trees_combined_by_explicit_flags_say_so(self):
        """Deliberate is allowed; silent is not."""
        victim, foreign = self._pair()
        r = self._run(
            victim,
            "--config", str(victim / "bionic" / "manifest.yml"),
            "--output-dir", str(foreign / "bionic" / "code"),
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("different tree", r.stderr)

    # ---------- dry-run and legitimate pruning ----------

    def test_dry_run_of_the_cross_repo_case_mutates_neither_tree(self):
        victim, foreign = self._pair()
        v_before = self._populate(victim)
        f_before = self._fingerprint(foreign / "bionic" / "code")

        r = self._run(victim, "--dry-run", "--config",
                      str(foreign / "bionic" / "manifest.yml"))

        self.assertEqual(self._fingerprint(victim / "bionic" / "code"), v_before)
        self.assertEqual(self._fingerprint(foreign / "bionic" / "code"), f_before)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["added"], 1, "the foreign tree has one page to add")
        self.assertEqual(
            payload["removed"], 0,
            "`removed` is the discriminator, not `added`: both trees add one page, "
            "but only a run comparing against the VICTIM's manifest reports the "
            "victim's own page as removed -- which is the deletion the defect "
            "went on to perform",
        )
        self.assertEqual(payload["detail"]["removed"], [])

    def test_legitimate_pruning_inside_the_correct_output_still_happens(self):
        """POSITIVE CONTROL for every preservation assertion above.

        The regenerative invariant is the point of the pruner. If preservation
        were achieved by disabling it, this goes red.
        """
        victim, _ = self._pair()
        self._populate(victim)
        (victim / "src" / "alpha.py").unlink()
        (victim / "src" / "beta.py").write_text('"""Beta."""\n', encoding="utf-8")

        r = self._run(victim)

        self.assertEqual(r.returncode, 0, r.stderr)
        pages = victim / "bionic" / "code"
        self.assertFalse((pages / "fallback" / "src" / "alpha.py.md").exists(),
                         "the page for a deleted source must be pruned")
        self.assertTrue((pages / "fallback" / "src" / "beta.py.md").is_file())


if __name__ == "__main__":
    unittest.main()
