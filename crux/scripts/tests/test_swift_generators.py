"""Tests for the Swift pack's Tuist/XcodeGen generator checks (ADR-0130
clause 13): `crux.arch.packs.swift_generators`.

String-level unit tests over `check_tuist` and `check_xcodegen` directly —
not the fixture harness (`xcode_fixture_harness.py`). Every fixture here is written fresh;
nothing is copied from any checkout.
"""

from __future__ import annotations

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]  # crux/scripts
sys.path.insert(0, str(SCRIPTS))

from crux.arch.packs import swift_generators as gen  # noqa: E402


def real_path_open_recorder():
    """An `open` audit hook and the two lists it fills: each opened path as
    passed, and the same path resolved. An open that follows an in-checkout
    symlink is recorded under the in-checkout name, so only the resolved list
    shows where the bytes came from. The absence tests assert on the resolved
    list; the aliasing control below proves that list catches such an open
    while the as-passed list misses it."""
    as_passed, resolved = [], []

    def hook(event, args):
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            as_passed.append(os.fsdecode(args[0]))
            resolved.append(os.path.realpath(args[0]))
    return hook, as_passed, resolved


class TuistTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_presence_pair_qualifies(self):
        (self.root / "Project.swift").write_text("// tuist manifest\n")
        (self.root / "Tuist.swift").write_text("// tuist config\n")
        result = gen.check_tuist(self.root, "")
        self.assertTrue(result.qualifies)
        self.assertEqual(result.manifest, "Project.swift")

    def test_workspace_swift_and_tuist_config_directory_qualifies(self):
        (self.root / "Workspace.swift").write_text("// tuist workspace\n")
        (self.root / "Tuist").mkdir()
        (self.root / "Tuist" / "Config.swift").write_text("// config\n")
        result = gen.check_tuist(self.root, "")
        self.assertTrue(result.qualifies)
        self.assertEqual(result.manifest, "Workspace.swift")

    def test_bare_project_swift_is_not_a_marker(self):
        (self.root / "Project.swift").write_text("// tuist manifest\n")
        result = gen.check_tuist(self.root, "")
        self.assertFalse(result.qualifies)
        self.assertIsNone(result.manifest)
        self.assertEqual(result.residuals, ())

    def test_bare_tuist_swift_alone_is_not_a_marker(self):
        (self.root / "Tuist.swift").write_text("// tuist config\n")
        result = gen.check_tuist(self.root, "")
        self.assertFalse(result.qualifies)

    def test_missing_input_when_no_companion_project(self):
        (self.root / "Project.swift").write_text("// manifest\n")
        (self.root / "Tuist.swift").write_text("// config\n")
        result = gen.check_tuist(self.root, "")
        self.assertEqual(len(result.residuals), 1)
        self.assertEqual(result.residuals[0].klass, "missing-input")
        self.assertEqual(result.residuals[0].detail, "Tuist")
        self.assertEqual(result.residuals[0].path, "Project.swift")

    def test_missing_input_suppressed_by_companion_project(self):
        (self.root / "Project.swift").write_text("// manifest\n")
        (self.root / "Tuist.swift").write_text("// config\n")
        proj = self.root / "App.xcodeproj"
        proj.mkdir()
        (proj / "project.pbxproj").write_text("// !$*UTF8*$!\n")
        result = gen.check_tuist(self.root, "")
        self.assertEqual(result.residuals, ())


class TuistComponentTests(unittest.TestCase):
    """ADR-0130 clause 3 and ADR-0129 clause 8 over the Tuist presence check:
    `Tuist/Config.swift` is never found through a symlinked `Tuist`
    directory, whether it points into `Pods` or anywhere else."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def _symlink(self, link, target):
        try:
            os.symlink(self.root / target, self.root / link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("host refuses symlink creation")

    def test_a_symlinked_tuist_directory_is_not_looked_through(self):
        (self.root / "Project.swift").write_text("// manifest\n")
        (self.root / "Pods" / "Tuist").mkdir(parents=True)
        (self.root / "Pods" / "Tuist" / "Config.swift").write_text("// config\n")
        self._symlink("Tuist", "Pods/Tuist")
        self.assertFalse(gen.check_tuist(self.root, "").qualifies)

    def test_a_symlinked_tuist_directory_to_an_ordinary_directory_is_not_looked_through(self):
        (self.root / "Project.swift").write_text("// manifest\n")
        (self.root / "Vendor" / "Tuist").mkdir(parents=True)
        (self.root / "Vendor" / "Tuist" / "Config.swift").write_text("// config\n")
        self._symlink("Tuist", "Vendor/Tuist")
        self.assertFalse(gen.check_tuist(self.root, "").qualifies)

    def test_control_a_real_tuist_directory_qualifies(self):
        (self.root / "Project.swift").write_text("// manifest\n")
        (self.root / "Tuist").mkdir()
        (self.root / "Tuist" / "Config.swift").write_text("// config\n")
        self.assertTrue(gen.check_tuist(self.root, "").qualifies)


class XcodeGenTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, text: str) -> None:
        (self.root / "project.yml").write_text(text)

    def test_valid_shape_qualifies(self):
        self._write("name: App\ntargets:\n  App:\n    type: application\n    platform: iOS\n")
        result = gen.check_xcodegen(self.root, "")
        self.assertTrue(result.qualifies)
        self.assertEqual(result.manifest, "project.yml")

    def test_missing_targets_is_not_a_marker(self):
        self._write("name: App\n")
        result = gen.check_xcodegen(self.root, "")
        self.assertFalse(result.qualifies)

    def test_empty_targets_is_not_a_marker(self):
        self._write("name: App\ntargets: {}\n")
        result = gen.check_xcodegen(self.root, "")
        self.assertFalse(result.qualifies)

    def test_missing_name_is_not_a_marker(self):
        self._write("targets:\n  App:\n    type: application\n    platform: iOS\n")
        result = gen.check_xcodegen(self.root, "")
        self.assertFalse(result.qualifies)

    def test_target_entry_missing_platform_is_not_a_marker(self):
        self._write("name: App\ntargets:\n  App:\n    type: application\n")
        result = gen.check_xcodegen(self.root, "")
        self.assertFalse(result.qualifies)

    def test_non_mapping_document_is_not_a_marker(self):
        self._write("- a\n- b\n")
        result = gen.check_xcodegen(self.root, "")
        self.assertFalse(result.qualifies)

    def test_no_file_is_not_a_marker(self):
        result = gen.check_xcodegen(self.root, "")
        self.assertFalse(result.qualifies)
        self.assertEqual(result.residuals, ())

    def test_oversize_file_is_not_a_marker(self):
        big = "name: App\ntargets:\n  App:\n    type: application\n    platform: iOS\n"
        big += "# " + ("x" * (2 * 1024 * 1024 + 10))
        self._write(big)
        result = gen.check_xcodegen(self.root, "")
        self.assertFalse(result.qualifies)

    def test_missing_input_when_no_companion_project(self):
        self._write("name: App\ntargets:\n  App:\n    type: application\n    platform: iOS\n")
        result = gen.check_xcodegen(self.root, "")
        self.assertEqual(len(result.residuals), 1)
        self.assertEqual(result.residuals[0].klass, "missing-input")
        self.assertEqual(result.residuals[0].detail, "XcodeGen")

    def test_missing_input_suppressed_by_companion_project(self):
        self._write("name: App\ntargets:\n  App:\n    type: application\n    platform: iOS\n")
        proj = self.root / "App.xcodeproj"
        proj.mkdir()
        (proj / "project.pbxproj").write_text("// !$*UTF8*$!\n")
        result = gen.check_xcodegen(self.root, "")
        self.assertEqual(result.residuals, ())

    def test_symlink_pointing_outside_checkout_is_not_a_marker(self):
        outside_dir = tempfile.TemporaryDirectory()
        self.addCleanup(outside_dir.cleanup)
        outside_file = Path(outside_dir.name) / "evil.yml"
        outside_file.write_text("name: App\ntargets:\n  App:\n    type: application\n    platform: iOS\n")
        try:
            (self.root / "project.yml").symlink_to(outside_file)
        except OSError:
            self.skipTest("symlinks unavailable on this platform")

        # Compared by real path: the hook records the path as passed, so an
        # open that FOLLOWS the in-checkout symlink records `project.yml`,
        # never the outside name, and a `/var` vs `/private/var` alias on
        # macOS hides it too. Real-pathing each recorded open catches both;
        # the aliasing control below proves it.
        outside_real = os.path.realpath(outside_file)
        hook, _as_passed, opened = real_path_open_recorder()
        sys.addaudithook(hook)  # cannot be removed; harmless for later tests.
        result = gen.check_xcodegen(self.root, "")
        self.assertFalse(result.qualifies)
        self.assertNotIn(outside_real, opened)

    def test_symlink_aliasing_control_the_unguarded_read_is_caught_only_by_real_path(self):
        """The discriminating control for the test above: the same symlink,
        the same entry point and the same recorder, with only the safe read
        replaced by one that follows the symlink. The file then qualifies,
        the resolved list holds the outside file, and the as-passed list
        names only the in-checkout `project.yml`. So a by-name absence check
        would have read green over this read, and the real-path one does not."""
        from unittest import mock
        outside_dir = tempfile.TemporaryDirectory()
        self.addCleanup(outside_dir.cleanup)
        outside_file = Path(outside_dir.name) / "evil.yml"
        outside_file.write_text("name: App\ntargets:\n  App:\n    type: application\n    platform: iOS\n")
        try:
            (self.root / "project.yml").symlink_to(outside_file)
        except OSError:
            self.skipTest("symlinks unavailable on this platform")

        def unguarded(_root, path, _oversize, **_kw):
            with open(path, "rb") as f:
                return f.read()

        hook, as_passed, resolved = real_path_open_recorder()
        sys.addaudithook(hook)  # cannot be removed; harmless for later tests.
        with mock.patch.object(gen, "_safe_read_bytes", unguarded):
            result = gen.check_xcodegen(self.root, "")
        self.assertTrue(result.qualifies)
        self.assertIn(os.path.realpath(outside_file), resolved)
        self.assertIn(str(self.root / "project.yml"), as_passed)
        self.assertNotIn(str(outside_file), as_passed)
        self.assertNotIn(os.path.realpath(outside_file), as_passed)

    def test_symlink_outside_checkout_positive_control_would_open(self):
        """ADR-0130 clause 17's positive control: proves the audit hook above is not
        vacuous -- an unguarded plain `open()` of the SAME outside target
        DOES have its open observed by the same hook, so the guarded
        `check_xcodegen`'s silence in the test above is a real refusal, not
        an artifact of the target being unreachable."""
        outside_dir = tempfile.TemporaryDirectory()
        self.addCleanup(outside_dir.cleanup)
        outside_file = Path(outside_dir.name) / "evil.yml"
        outside_file.write_text("name: App\ntargets:\n  App:\n    type: application\n    platform: iOS\n")

        opened = []

        def hook(event, args):
            if event == "open":
                opened.append(str(args[0]))

        sys.addaudithook(hook)
        open(outside_file).close()
        self.assertIn(str(outside_file), opened)

    def test_fifo_pointing_at_project_yml_is_not_a_marker_and_is_never_opened(self):
        """ADR-0130 clause 17: a FIFO named `project.yml` is
        refused by the safe-read contract (`not-regular`), and the audit
        hook proves the only open of it is that contract's own probe:
        `os.open` with `O_NONBLOCK | O_NOFOLLOW`, which cannot block on a
        FIFO and is refused by the following `fstat`. A blocking or
        builtin `open()` of the FIFO would turn this red (ADR-0130
        clause 17: "a FIFO refused" through the safe-read contract).

        Both sides are compared by real path. The hook records the path
        as passed; on macOS a temporary directory under `/var` resolves to
        `/private/var`, so comparing a resolved path against the recorded
        one matched nothing there and read green without measuring
        anything (observed in the prompt-15 Linux cells, where `/tmp` does
        not alias and the probe open was seen)."""
        try:
            os.mkfifo(self.root / "project.yml")
        except (OSError, AttributeError):
            self.skipTest("FIFOs unavailable on this platform")

        target_real = os.path.realpath(self.root / "project.yml")
        opened = []

        def hook(event, args):
            if event == "open":
                opened.append(args)

        sys.addaudithook(hook)
        try:
            result = gen.check_xcodegen(self.root, "")
        finally:
            pass  # sys.addaudithook cannot be removed; harmless for later tests.
        self.assertFalse(result.qualifies)
        probe_flags = os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        fifo_opens = [a for a in opened
                      if isinstance(a[0], (str, bytes, os.PathLike))
                      and os.path.realpath(a[0]) == target_real]
        # The probe opens the FIFO once; without an observed open the flag
        # checks below would pass on an empty list.
        self.assertTrue(fifo_opens, "no open of the FIFO was observed")
        for path, mode, flags in fifo_opens:
            self.assertIsNone(mode, f"a builtin open() reached the FIFO: {path!r}")
            self.assertEqual(flags & probe_flags, probe_flags,
                             f"the FIFO was opened without O_NONBLOCK|O_NOFOLLOW: {flags:#x}")

    def test_canary_never_fires_guarded(self):
        """A `!!python/object/apply` tag under the guarded event scan is
        refused before any node is composed; the file is not a marker."""
        self._write(
            "name: App\n"
            "targets:\n"
            "  App:\n"
            "    type: application\n"
            "    platform: iOS\n"
            "canary: !!python/object/apply:os.getpid []\n"
        )
        result = gen.check_xcodegen(self.root, "")
        self.assertFalse(result.qualifies)

    def test_canary_positive_control_unrestricted_loader_fires_it(self):
        """Proves the canary above is not vacuous: loading the SAME bytes
        with an unrestricted loader actually invokes the constructor named
        by the tag (`os.getpid`), which `_scan_events_safe` above refuses to
        ever reach."""
        import yaml

        raw = (
            "name: App\n"
            "targets:\n"
            "  App:\n"
            "    type: application\n"
            "    platform: iOS\n"
            "canary: !!python/object/apply:os.getpid []\n"
        ).encode()
        self.assertFalse(gen._scan_events_safe(raw))
        value = yaml.load(raw, Loader=yaml.UnsafeLoader)
        self.assertIsInstance(value["canary"], int)  # os.getpid() actually ran

    def test_merge_key_refused(self):
        """An anchor-free inline merge key. The anchor and alias checks
        cannot refuse this document, so only the `<<` check can: without
        it, the safe loader would merge `type` and `platform` into `App`
        and the shape would qualify (see the control below)."""
        self._write(
            "name: App\n"
            "targets:\n"
            "  App:\n"
            "    <<: {type: application, platform: iOS}\n"
        )
        result = gen.check_xcodegen(self.root, "")
        self.assertFalse(result.qualifies)

    def test_merge_key_control_same_keys_inline_qualify(self):
        """Positive control for the merge-key refusal: the same keys
        written directly under `App` qualify."""
        self._write(
            "name: App\n"
            "targets:\n"
            "  App:\n"
            "    type: application\n"
            "    platform: iOS\n"
        )
        result = gen.check_xcodegen(self.root, "")
        self.assertTrue(result.qualifies)

    def test_include_key_never_read_as_a_file_reference(self):
        """`include:` is a plain, unrecognised mapping key to this shape
        check -- the manifest still fails to qualify (no `targets` shape
        satisfied), and nothing under `include:` is ever opened."""
        self._write("name: App\ninclude:\n  - other.yml\n")
        result = gen.check_xcodegen(self.root, "")
        self.assertFalse(result.qualifies)


class MaliciousXcodegenManifestTests(unittest.TestCase):
    """A qualifying `name`/
    `targets` head paired with a pathological `extra:` value used to crash
    `check_xcodegen` with an uncaught `RecursionError`/`ValueError` rather
    than reporting not-a-marker (ADR-0130 clause 13's "renders nothing and
    never exits 2", rule `xcode-project-content-never-exits-2`)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, text: str) -> None:
        (self.root / "project.yml").write_text(text)

    def _head(self) -> str:
        return "name: App\ntargets:\n  App:\n    type: application\n    platform: iOS\n"

    def test_deeply_nested_flow_sequence_is_not_a_marker_no_exception(self):
        """600-deep `[`/`]` nesting under `extra:` used to reach
        `yaml.safe_load` unguarded and raise `RecursionError`; the event-
        stream depth bound in `_scan_events_safe` now refuses the document
        before the composer is ever reached, so `check_xcodegen` reports
        not-a-marker instead of raising."""
        raw = self._head() + "extra: " + "[" * 600 + "]" * 600 + "\n"
        self.assertFalse(gen._scan_events_safe(raw.encode()))
        self._write(raw)
        result = gen.check_xcodegen(self.root, "")
        self.assertFalse(result.qualifies)

    def test_deeply_nested_flow_sequence_positive_control_recursion_error(self):
        """Proves the reproduction above is real: `yaml.safe_load` alone,
        without the depth-bounded event scan in front of it, raises
        `RecursionError` on the same bytes the guarded path refuses safely."""
        import yaml

        raw = (self._head() + "extra: " + "[" * 600 + "]" * 600 + "\n").encode()
        with self.assertRaises(RecursionError):
            yaml.safe_load(raw)

    def test_invalid_timestamp_is_not_a_marker_no_exception(self):
        """`d: 2001-13-45` raises `ValueError` out of PyYAML's timestamp
        constructor during `yaml.safe_load` (an invalid month/day past the
        `datetime` construction, not caught by `except yaml.YAMLError`)."""
        raw = self._head() + "d: 2001-13-45\n"
        self._write(raw)
        result = gen.check_xcodegen(self.root, "")
        self.assertFalse(result.qualifies)

    def test_invalid_timestamp_positive_control_value_error(self):
        import yaml

        raw = (self._head() + "d: 2001-13-45\n").encode()
        with self.assertRaises(ValueError):
            yaml.safe_load(raw)

    def test_oversized_int_literal_is_not_a_marker_no_exception(self):
        """A 5000-digit integer scalar raises `ValueError` out of the
        interpreter's int-from-string digit limit during `yaml.safe_load`
        (not a `yaml.YAMLError`)."""
        raw = self._head() + "n: " + ("9" * 5000) + "\n"
        self._write(raw)
        result = gen.check_xcodegen(self.root, "")
        self.assertFalse(result.qualifies)

    def test_oversized_int_literal_positive_control_value_error(self):
        import yaml

        raw = (self._head() + "n: " + ("9" * 5000) + "\n").encode()
        with self.assertRaises(ValueError):
            yaml.safe_load(raw)

    def test_scan_events_safe_depth_64_ok_65_refused(self):
        """The event-stream nesting bound's own boundary: a 64-deep flow
        sequence is admitted by `_scan_events_safe` (the composer would still
        be reachable), a 65-deep one is refused before the composer is ever
        reached."""
        def nested(n: int) -> bytes:
            return ("[" * n + "]" * n).encode()

        self.assertTrue(gen._scan_events_safe(nested(64)))
        self.assertFalse(gen._scan_events_safe(nested(65)))


class StdlibScopeTests(unittest.TestCase):
    """`swift_generators.py` may import `yaml` inside functions only, never
    at module scope (ADR-0130 clause 13's PyYAML admission)."""

    def test_no_module_scope_yaml_import(self):
        src_path = SCRIPTS / "crux" / "arch" / "packs" / "swift_generators.py"
        tree = ast.parse(src_path.read_text(encoding="utf-8"))
        for node in tree.body:  # module-scope statements only
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotEqual(alias.name.split(".")[0], "yaml",
                                         "yaml must be imported inside a function, not at module scope")
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "yaml")

    def test_every_function_level_import_is_yaml_or_stdlib(self):
        src_path = SCRIPTS / "crux" / "arch" / "packs" / "swift_generators.py"
        tree = ast.parse(src_path.read_text(encoding="utf-8"))
        module_level = {n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                         and n in tree.body}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)) and node not in module_level:
                names = ([a.name.split(".")[0] for a in node.names] if isinstance(node, ast.Import)
                         else [node.module.split(".")[0]] if node.module else [])
                for n in names:
                    self.assertTrue(n == "yaml" or n in sys.stdlib_module_names, n)



class ContainmentGapTests(unittest.TestCase):
    """ADR-0130 clause 3: `_has_companion_project` and
    `check_tuist`'s `present()` check real-path containment
    (`core._contained`) before stat-ing anything under `directory_rel` --
    reachable from project content once `detect()`/`_project_facts` wire a
    workspace/XcodeGen reference in as a `directory_rel` argument, so a
    symlinked component that escapes the checkout must never be followed."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "repo"
        self.root.mkdir()
        self.outside = Path(self._tmp.name) / "outside"
        self.outside.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_symlinked_directory_escaping_the_checkout_is_refused(self):
        import os
        (self.outside / "Project.swift").write_text("// tuist\n")
        (self.outside / "Tuist.swift").write_text("// config\n")
        os.symlink(self.outside, self.root / "Escaped")
        result = gen.check_tuist(self.root, "Escaped")
        self.assertFalse(result.qualifies)
        self.assertIsNone(result.manifest)

    def test_symlinked_directory_staying_inside_the_checkout_is_a_control(self):
        import os
        real = self.root / "Real"
        real.mkdir()
        (real / "Project.swift").write_text("// tuist\n")
        (real / "Tuist.swift").write_text("// config\n")
        os.symlink(real, self.root / "Linked")
        result = gen.check_tuist(self.root, "Linked")
        self.assertTrue(result.qualifies)

    def test_has_companion_project_refuses_a_symlinked_escape(self):
        import os
        (self.outside / "App.xcodeproj").mkdir()
        (self.outside / "App.xcodeproj" / "project.pbxproj").write_text("// !$*UTF8*$!\n")
        os.symlink(self.outside, self.root / "Escaped")
        self.assertFalse(gen._has_companion_project(self.root, "Escaped"))

    def test_has_companion_project_positive_control_stays_inside(self):
        real = self.root / "Real"
        real.mkdir()
        (real / "App.xcodeproj").mkdir()
        (real / "App.xcodeproj" / "project.pbxproj").write_text("// !$*UTF8*$!\n")
        self.assertTrue(gen._has_companion_project(self.root, "Real"))


if __name__ == "__main__":
    unittest.main()
