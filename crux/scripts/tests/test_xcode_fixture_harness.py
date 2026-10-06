"""Self-tests for the Xcode fixture harness (`xcode_fixture_harness.py`)
itself. Most classes call the harness's own pieces directly: the residual
grammar, residual matching and counting, the `materialize:` containment guard,
padded and repeated materialisation, and the `max_bytes` check. The report
checks drive the real subprocess path. `ChildReportChecksTests` and
`StrictReportCheckTests` run `run_case` on the harness control fixture, with
`_run_child` patched to inject a failing report. `AdvisoryPhaseScopingTests`
and `ChildMemoryBoundTests` call `_run_child` on it directly. Running every
fixture case is `test_swift_pack.py::XcodeFixtureTests`'s job."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import xcode_fixture_harness as h  # noqa: E402


class ParseResidualLineTests(unittest.TestCase):
    def test_parses_a_well_formed_bullet(self):
        line = "- `path-escape` `App.xcworkspace/contents.xcworkspacedata` lines 3-3 — workspace file reference"
        parsed = h.parse_residual_line(line)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["klass"], "path-escape")
        self.assertEqual(parsed["path"], "App.xcworkspace/contents.xcworkspacedata")
        self.assertEqual(parsed["span"], (3, 3))
        self.assertEqual(parsed["detail"], "workspace file reference")

    def test_parses_no_location_form(self):
        line = "- `missing-input` `App.xcodeproj/project.pbxproj` lines — — XcodeGen"
        parsed = h.parse_residual_line(line)
        self.assertIsNotNone(parsed)
        self.assertIsNone(parsed["span"])

    def test_parses_multi_range_form(self):
        line = "- `conditional-setting` `App.xcconfig` lines 2-2, 9-9 — SWIFT_VERSION"
        parsed = h.parse_residual_line(line)
        self.assertEqual(parsed["span"], (2, 9))

    def test_non_matching_line_returns_none(self):
        self.assertIsNone(h.parse_residual_line("| `Widget` | struct | public |"))
        self.assertIsNone(h.parse_residual_line(""))


class FindResidualTests(unittest.TestCase):
    """Class and path must sit in the SAME bullet, kind is
    matched against detail, and a range must be COVERED, not merely
    present anywhere in the file."""

    def test_class_and_path_in_different_bullets_does_not_match(self):
        """The defect the loose substring match had: a bullet naming the
        class and a DIFFERENT bullet naming the path used to satisfy the
        old check. The exact-grammar matcher must refuse this."""
        body = ('- `path-escape` `Other.xcconfig` lines 1-1 — #include\n- `missing-input` `App.xcodeproj/project.pbxproj` lines — — XcodeGen\n')
        rendered = {"data-model": body}
        expected = {"class": "path-escape", "path": "App.xcodeproj/project.pbxproj"}
        self.assertFalse(h.find_residual(rendered, expected))

    def test_class_and_path_in_same_bullet_matches(self):
        body = "- `path-escape` `App.xcodeproj/project.pbxproj` lines 4-4 — workspace file reference" + chr(10)
        rendered = {"data-model": body}
        expected = {"class": "path-escape", "path": "App.xcodeproj/project.pbxproj"}
        self.assertTrue(h.find_residual(rendered, expected))

    def test_unclosed_backtick_prefix_match_is_refused(self):
        """The old loose match accepted ANY string carrying `path` as a
        prefix (an unclosed backtick). A bullet naming a LONGER path that
        merely starts with the expected one must not match."""
        body = "- `path-escape` `App.xcodeproj/project.pbxprojBOGUS` lines 4-4 — workspace file reference" + chr(10)
        rendered = {"data-model": body}
        expected = {"class": "path-escape", "path": "App.xcodeproj/project.pbxproj"}
        self.assertFalse(h.find_residual(rendered, expected))

    def test_kind_mismatch_does_not_match(self):
        body = "- `path-escape` `App.xcodeproj/project.pbxproj` lines 4-4 — workspace group location" + chr(10)
        rendered = {"data-model": body}
        expected = {"class": "path-escape", "path": "App.xcodeproj/project.pbxproj",
                    "kind": "workspace file reference"}
        self.assertFalse(h.find_residual(rendered, expected))

    def test_kind_match_matches(self):
        body = "- `path-escape` `App.xcodeproj/project.pbxproj` lines 4-4 — workspace file reference" + chr(10)
        rendered = {"data-model": body}
        expected = {"class": "path-escape", "path": "App.xcodeproj/project.pbxproj",
                    "kind": "workspace file reference"}
        self.assertTrue(h.find_residual(rendered, expected))

    def test_wrong_range_does_not_match(self):
        """A bullet whose span does NOT cover the expected range must not
        match, even though class and path agree."""
        body = "- `conditional-setting` `App.xcconfig` lines 10-10 — SWIFT_VERSION" + chr(10)
        rendered = {"data-model": body}
        expected = {"class": "conditional-setting", "path": "App.xcconfig",
                    "first_line": 20, "last_line": 20}
        self.assertFalse(h.find_residual(rendered, expected))

    def test_covering_range_matches(self):
        body = "- `conditional-setting` `App.xcconfig` lines 5-15 — SWIFT_VERSION" + chr(10)
        rendered = {"data-model": body}
        expected = {"class": "conditional-setting", "path": "App.xcconfig",
                    "first_line": 8, "last_line": 8}
        self.assertTrue(h.find_residual(rendered, expected))

    def test_no_location_bullet_never_covers_a_requested_range(self):
        body = "- `missing-input` `App.xcodeproj/project.pbxproj` lines — — XcodeGen" + chr(10)
        rendered = {"data-model": body}
        expected = {"class": "missing-input", "path": "App.xcodeproj/project.pbxproj",
                    "first_line": 1, "last_line": 1}
        self.assertFalse(h.find_residual(rendered, expected))


class MaterializeContainmentTests(unittest.TestCase):
    """`materialize:` paths must not escape the destination copy."""

    def setUp(self):
        self._src_tmp = tempfile.TemporaryDirectory()
        self._dst_tmp = tempfile.TemporaryDirectory()
        self.src = Path(self._src_tmp.name) / "fixture"
        self.src.mkdir()
        (self.src / "expected.yml").write_text("exit_code: 0" + chr(10))
        self.dst = Path(self._dst_tmp.name) / "case"

    def tearDown(self):
        self._src_tmp.cleanup()
        self._dst_tmp.cleanup()

    def test_escaping_materialize_path_is_refused(self):
        spec = {"materialize": [{"path": "../outside.txt", "kind": "fifo"}]}
        with self.assertRaises(ValueError):
            h.materialize(self.src, self.dst, spec)
        self.assertFalse((self.dst.parent / "outside.txt").exists())

    def test_absolute_materialize_path_is_refused(self):
        spec = {"materialize": [{"path": "/etc/evil", "kind": "fifo"}]}
        with self.assertRaises(ValueError):
            h.materialize(self.src, self.dst, spec)

    def test_contained_materialize_path_succeeds(self):
        spec = {"materialize": [{"path": "Sub/link.txt", "kind": "symlink",
                                  "target": "../elsewhere.txt"}]}
        h.materialize(self.src, self.dst, spec)
        self.assertTrue((self.dst / "Sub" / "link.txt").is_symlink())


class PaddedMaterializeTests(unittest.TestCase):
    """The `padded` materialize directive writes a
    fixture's `source:` pbxproj text padded with a `/* ... */` comment
    block to an EXACT `size_bytes`, so a hostile case can land precisely at
    the ADR-0130 clause 2 16 MiB bound -- something `kind: oversize`'s
    sparse mostly-NUL write cannot do without corrupting the grammar."""

    def setUp(self):
        pack_dir = SCRIPTS.parent / "crux" / "arch" / "packs"
        sys.path.insert(0, str(pack_dir))
        global swift_pbxproj
        import swift_pbxproj  # noqa: E402

        self._src_tmp = tempfile.TemporaryDirectory()
        self._dst_tmp = tempfile.TemporaryDirectory()
        self.src = Path(self._src_tmp.name) / "fixture"
        self.src.mkdir()
        (self.src / "expected.yml").write_text("exit_code: 0" + chr(10))
        self.source_pbxproj = (
            "// !$*UTF8*$!\n"
            "{\n"
            "\tarchiveVersion = 1;\n"
            "\tclasses = {\n"
            "\t};\n"
            "\tobjectVersion = 56;\n"
            "\tobjects = {\n"
            "\t\tPRJ = {\n"
            "\t\t\tisa = PBXProject;\n"
            "\t\t\tmainGroup = MG_ROOT;\n"
            "\t\t\ttargets = (\n"
            "\t\t\t);\n"
            "\t\t};\n"
            "\t\tMG_ROOT = {\n"
            "\t\t\tisa = PBXGroup;\n"
            '\t\t\tsourceTree = "<group>";\n'
            "\t\t\tchildren = (\n"
            "\t\t\t);\n"
            "\t\t};\n"
            "\t};\n"
            "\trootObject = PRJ;\n"
            "}\n"
        )
        (self.src / "source.pbxproj").write_text(self.source_pbxproj)
        self.dst = Path(self._dst_tmp.name) / "case"

    def tearDown(self):
        self._src_tmp.cleanup()
        self._dst_tmp.cleanup()

    def test_padded_file_is_exactly_the_requested_size(self):
        size = len(self.source_pbxproj.encode("utf-8")) + 500
        spec = {"materialize": [{"path": "App.xcodeproj/project.pbxproj",
                                  "kind": "padded", "source": "source.pbxproj",
                                  "size_bytes": size}]}
        h.materialize(self.src, self.dst, spec)
        out = self.dst / "App.xcodeproj" / "project.pbxproj"
        self.assertEqual(out.stat().st_size, size)

    def test_padded_file_at_exactly_16_mib_parses_as_a_valid_pbxproj(self):
        size = 16 * 1024 * 1024
        spec = {"materialize": [{"path": "App.xcodeproj/project.pbxproj",
                                  "kind": "padded", "source": "source.pbxproj",
                                  "size_bytes": size}]}
        h.materialize(self.src, self.dst, spec)
        out = self.dst / "App.xcodeproj" / "project.pbxproj"
        raw = out.read_bytes()
        self.assertEqual(len(raw), size)
        doc = swift_pbxproj.read_pbxproj(raw)
        self.assertIsInstance(doc, swift_pbxproj.PbxprojDocument)

    def test_padded_size_smaller_than_source_is_refused(self):
        size = 10
        spec = {"materialize": [{"path": "App.xcodeproj/project.pbxproj",
                                  "kind": "padded", "source": "source.pbxproj",
                                  "size_bytes": size}]}
        with self.assertRaises(ValueError):
            h.materialize(self.src, self.dst, spec)


class CountResidualsTests(unittest.TestCase):
    """`residuals[].count` asserts the EXACT number of matching
    bullets across every rendered concern body, for the 64-level diamond's
    "one unresolved-reference at each join" claim -- `find_residual` alone
    only proves at-least-one, never the count."""

    def test_count_matches_exact_number_of_bullets(self):
        body = "".join(
            f"- `unresolved-reference` `App.xcodeproj/project.pbxproj` "
            f"lines {n}-{n} \u2014 a file reference did not resolve\n"
            for n in range(1, 65)
        )
        rendered = {"module-graph": body}
        self.assertEqual(
            h.count_matching_residuals(
                rendered, {"class": "unresolved-reference",
                           "path": "App.xcodeproj/project.pbxproj"}),
            64)

    def test_count_excludes_non_matching_bullets(self):
        body = (
            "- `unresolved-reference` `App.xcodeproj/project.pbxproj` "
            "lines 1-1 \u2014 a file reference did not resolve\n"
            "- `missing-input` `App.xcodeproj/project.pbxproj` "
            "lines \u2014 \u2014 XcodeGen\n"
        )
        rendered = {"module-graph": body}
        self.assertEqual(
            h.count_matching_residuals(
                rendered, {"class": "unresolved-reference",
                           "path": "App.xcodeproj/project.pbxproj"}),
            1)


if __name__ == "__main__":
    unittest.main()


class ChildReportChecksTests(unittest.TestCase):
    """The harness is the evidence behind ADR-0130 clause 17's "no path
    outside the checkout" and "no process" assertions, so each derive it
    runs, the second one included, must have its report checked, and a
    prefix must match only at a path-separator boundary."""

    def test_prefix_matches_only_at_a_separator(self):
        self.assertTrue(h._is_allowed("/t/case/a.swift", ["/t/case"]))
        self.assertTrue(h._is_allowed("/t/case", ["/t/case"]))
        self.assertFalse(h._is_allowed("/t/case2/a.swift", ["/t/case"]))
        self.assertFalse(h._is_allowed("/t/case-evil", ["/t/case"]))

    def _run_with_reports(self, first, second):
        from unittest import mock
        reports = iter([first, second])
        fixture = h.FIXTURES_ROOT / "00-harness-control"
        real = h._run_child

        def fake(argv, allowed, timeout):
            payload = real(argv, allowed, timeout)
            payload.update(next(reports))
            return payload

        with mock.patch.object(h, "_run_child", side_effect=fake):
            h.run_case(fixture, timeout=60)

    def test_second_derive_outside_open_fails_the_case(self):
        with self.assertRaises(h.FixtureCaseError):
            self._run_with_reports({}, {"outside_opens": ["/etc/hosts"]})

    def test_an_outside_listing_fails_the_case(self):
        with self.assertRaisesRegex(h.FixtureCaseError, r"derive listed directories outside"):
            self._run_with_reports({"outside_listings": ["/etc"]}, {})

    def test_second_derive_outside_listing_fails_the_case(self):
        with self.assertRaisesRegex(h.FixtureCaseError, r"second derive listed directories outside"):
            self._run_with_reports({}, {"outside_listings": ["/etc"]})

    def test_second_derive_exit_code_is_checked(self):
        with self.assertRaises(h.FixtureCaseError):
            self._run_with_reports({}, {"exit_code": 2})

    def test_a_trapped_process_fails_the_case_even_when_the_derive_exits_0(self):
        with self.assertRaises(h.FixtureCaseError):
            self._run_with_reports({"fired": ["subprocess.Popen"]}, {})

    def test_clean_reports_control_passes(self):
        self._run_with_reports({}, {})


class DependencyAllowlistTests(unittest.TestCase):
    """The child derive's allowed set admits the directories the pinned
    dependencies are imported from, and nothing wider. Under the release
    gates' `uv run --no-project --with ...` shape those directories sit in the
    uv cache, outside every interpreter prefix. Only a virtual environment's
    site directory is admitted whole. A global `site-packages` on `sys.path`
    is admitted only for its distribution-metadata directories, and
    `~/.cache` is not admitted."""

    def test_each_dependency_dir_is_a_venv_site_dir_or_a_global_metadata_dir(self):
        # An entry is either a virtual environment's install directory, a
        # distribution-metadata directory directly inside a global one, or
        # the target of a file linked directly inside such a directory.
        import os
        dirs = h.dependency_dirs()
        self.assertTrue(dirs)
        linked = {os.path.realpath(os.path.join(m, n))
                  for m in dirs if m.endswith((".dist-info", ".egg-info"))
                  for n in os.listdir(m) if os.path.islink(os.path.join(m, n))}
        for d in dirs:
            with self.subTest(d=d):
                if d in linked and os.path.isfile(d):
                    continue
                if os.path.basename(d) in ("site-packages", "dist-packages"):
                    self.assertTrue(h._in_virtual_environment(d), d)
                else:
                    self.assertTrue(d.endswith((".dist-info", ".egg-info")), d)
                    parent = os.path.dirname(d)
                    self.assertIn(os.path.basename(parent), ("site-packages", "dist-packages"))
                    self.assertFalse(h._in_virtual_environment(parent), d)

    def test_the_set_names_neither_home_nor_the_uv_cache_root(self):
        import os
        home = os.path.realpath(str(Path.home()))
        allowed = [os.path.realpath(p) for p in h.allowed_prefixes()]
        for p in allowed:
            with self.subTest(p=p):
                self.assertNotEqual(p, home)
                self.assertFalse(home.startswith(p.rstrip(os.sep) + os.sep), p)
                self.assertNotEqual(os.path.basename(p), "uv")
                self.assertNotEqual(os.path.basename(p), "archive-v0")
        self.assertFalse(h._is_allowed(os.path.join(home, "outside.txt"), allowed))

    def test_the_imported_dependencies_lie_in_the_allowed_set(self):
        import os
        import tree_sitter
        import tree_sitter_swift
        import yaml
        allowed = [os.path.realpath(p) for p in h.allowed_prefixes()]
        for mod in (tree_sitter, tree_sitter_swift, yaml):
            with self.subTest(module=mod.__name__):
                self.assertTrue(h._is_allowed(os.path.realpath(mod.__file__), allowed),
                                mod.__file__)

    def test_the_set_admits_nothing_else_under_the_user_cache(self):
        # The uv overlay environment lives under `~/.cache/uv/archive-v0/<h>`,
        # so only that environment's site directory may be admitted. A
        # widening to `~/.cache` or `~/.cache/uv` admits every other cached
        # environment and file, which these two canaries catch.
        import os
        cache = os.path.realpath(os.path.join(str(Path.home()), ".cache"))
        allowed = [os.path.realpath(p) for p in h.allowed_prefixes()]
        for canary in (os.path.join(cache, "d4-canary.txt"),
                       os.path.join(cache, "uv", "d4-canary.txt"),
                       os.path.join(cache, "uv", "archive-v0", "d4-canary", "x.py")):
            with self.subTest(canary=canary):
                self.assertFalse(h._is_allowed(canary, allowed))

    def test_only_a_virtual_environment_site_directory_is_admitted_whole(self):
        # An installer directory is admitted whole only when its environment
        # carries a `pyvenv.cfg`: the gate venv, the uv ephemeral environment
        # and the uv overlay environment do. A global `site-packages` such as
        # Homebrew's `/opt/homebrew/lib/python3.N/site-packages` does not, so
        # only its distribution-metadata directories are admitted, never a
        # module in it.
        import os
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            base = Path(d).resolve()
            venv_site = base / "env" / "lib" / "python3.13" / "site-packages"
            global_site = base / "brew" / "lib" / "python3.13" / "site-packages"
            venv_site.mkdir(parents=True)
            (global_site / "pip-26.1.dist-info").mkdir(parents=True)
            (global_site / "pip").mkdir()
            (base / "env" / "pyvenv.cfg").write_text("home = /x\n", encoding="utf-8")
            with mock.patch.object(sys, "path", [str(venv_site), str(global_site)]), \
                    mock.patch("site.getsitepackages", return_value=[]):
                dirs = [os.path.realpath(p) for p in h.dependency_dirs()]
        self.assertEqual(dirs, [str(venv_site), str(global_site / "pip-26.1.dist-info")])
        self.assertTrue(h._is_allowed(str(global_site / "pip-26.1.dist-info" / "RECORD"), dirs))
        self.assertFalse(h._is_allowed(str(global_site / "pip" / "__init__.py"), dirs))
        self.assertFalse(h._is_allowed(str(global_site / "d4-canary.pth"), dirs))

    def test_a_linked_metadata_file_admits_only_its_own_target(self):
        # Homebrew fills a global metadata directory with per-file links into
        # its Cellar (`METADATA -> ../../../../Cellar/<f>/.../METADATA`). The
        # child's hook compares real paths, so the link's target must be
        # admitted, and nothing else in the Cellar: not an unlinked sibling,
        # not the Cellar's metadata directory, not a module beside it.
        import os
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            base = Path(d).resolve()
            cellar_meta = base / "Cellar" / "meson" / "site-packages" / "meson-1.dist-info"
            cellar_meta.mkdir(parents=True)
            (cellar_meta / "METADATA").write_text("Name: meson\n", encoding="utf-8")
            (cellar_meta / "RECORD").write_text("", encoding="utf-8")
            (cellar_meta.parent / "mesonbuild.py").write_text("", encoding="utf-8")
            global_site = base / "brew" / "lib" / "python3.14" / "site-packages"
            linked = global_site / "meson-1.dist-info"
            linked.mkdir(parents=True)
            (linked / "METADATA").symlink_to(cellar_meta / "METADATA")
            with mock.patch.object(sys, "path", [str(global_site)]), \
                    mock.patch("site.getsitepackages", return_value=[]):
                allowed = [os.path.realpath(p) for p in h.dependency_dirs()]
            self.assertTrue(h._is_allowed(os.path.realpath(linked / "METADATA"), allowed))
            self.assertFalse(h._is_allowed(str(cellar_meta / "RECORD"), allowed))
            self.assertFalse(h._is_allowed(str(cellar_meta), allowed))
            self.assertFalse(h._is_allowed(str(cellar_meta.parent / "mesonbuild.py"), allowed))

    def test_a_global_site_directory_is_listable_and_nothing_under_it(self):
        # A distribution lookup lists each global site directory on
        # `sys.path` to find its metadata, so `listable_dirs()` names those
        # directories and no virtual environment's.
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            base = Path(d).resolve()
            venv_site = base / "env" / "lib" / "python3.14" / "site-packages"
            global_site = base / "brew" / "lib" / "python3.14" / "site-packages"
            venv_site.mkdir(parents=True)
            global_site.mkdir(parents=True)
            (base / "env" / "pyvenv.cfg").write_text("home = /x\n", encoding="utf-8")
            with mock.patch.object(sys, "path", [str(venv_site), str(global_site)]), \
                    mock.patch("site.getsitepackages", return_value=[]):
                self.assertEqual(h.listable_dirs(), [str(global_site)])

    def test_the_child_hook_admits_a_listable_directory_by_exact_path(self):
        # Through the hook every fixture derive runs under: listing an
        # admitted directory is not recorded; listing a subdirectory of it,
        # or opening a file in it, is. A listing admission never becomes a
        # prefix.
        with tempfile.TemporaryDirectory() as d:
            base = Path(d).resolve()
            site_dir = base / "site-packages"
            (site_dir / "pkg").mkdir(parents=True)
            (site_dir / "canary.pth").write_text("", encoding="utf-8")
            scratch = base / "scratch"
            scratch.mkdir()
            body = (
                f"os.listdir({str(site_dir)!r})\n"
                f"os.listdir({str(site_dir / 'pkg')!r})\n"
                f"open({str(site_dir / 'canary.pth')!r}).close()\n"
                "print(json.dumps({'listings': outside_listings, 'opens': outside_opens}))\n"
            )
            report = h._run_hooked(scratch, body, 30, listable=[str(site_dir)])
        self.assertEqual(report["listings"], [str(site_dir / "pkg")])
        self.assertEqual(report["opens"], [str(site_dir / "canary.pth")])

    def test_the_host_global_site_directory_is_not_admitted(self):
        # The same rule on this host: the base interpreter's own
        # `site-packages`, where it resolves outside every interpreter prefix
        # (Homebrew links it to `/opt/homebrew/lib/...`), is not admitted, and
        # neither is a module in it.
        import os
        import site
        prefixes = [os.path.realpath(p) for p in
                    (sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix)]
        outside = [os.path.realpath(p) for p in site.getsitepackages([sys.base_prefix])
                   if os.path.isdir(p) and not h._is_allowed(os.path.realpath(p), prefixes)]
        if not outside:
            self.skipTest("this interpreter's global site-packages lies under its prefix")
        allowed = [os.path.realpath(p) for p in h.allowed_prefixes()]
        for real in outside:
            with self.subTest(site_dir=real):
                self.assertFalse(h._is_allowed(real, allowed))
                self.assertFalse(h._is_allowed(os.path.join(real, "d4-canary.py"), allowed))
                self.assertFalse(h._is_allowed(os.path.join(real, "pip", "__init__.py"), allowed))


class AdvisoryPhaseScopingTests(unittest.TestCase):
    """The driver's staleness advisory runs a read-only `git` query after the
    derive. The child records that event in `advisory_fired`, apart from the
    derive's own `fired`, and still traps it. This pins that the control
    derive's one spawn is the advisory's and that the derive itself spawns
    nothing (ADR-0129 clause 2)."""

    def test_control_derive_spawns_only_in_the_advisory(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve() / "case"
            fixture = h.FIXTURES_ROOT / "00-harness-control"
            h.materialize(fixture, root, h.load_expected(fixture))
            allowed = h.allowed_prefixes(root)
            report = h._run_child(["--repo-root", str(root)], allowed, 60)
        self.assertEqual(report["fired"], [])
        self.assertEqual(report["advisory_fired"], ["subprocess.Popen"])


class ChildMemoryBoundTests(unittest.TestCase):
    """The child derive runs under an address-space bound, so a fixture that
    makes the derive allocate without limit fails its own case instead of
    exhausting the suite host. Linux enforces `RLIMIT_AS`; macOS refuses to
    lower it, so there the bound is best-effort and the child reports None."""

    def test_the_child_reports_the_bound_it_runs_under(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve() / "case"
            fixture = h.FIXTURES_ROOT / "00-harness-control"
            h.materialize(fixture, root, h.load_expected(fixture))
            allowed = h.allowed_prefixes(root)
            report = h._run_child(["--repo-root", str(root)], allowed, 60)
        self.assertIn("memory_limit", report)
        if sys.platform.startswith("linux"):
            self.assertEqual(report["memory_limit"], h.CHILD_MEMORY_BYTES)
        else:
            self.assertIn(report["memory_limit"], (h.CHILD_MEMORY_BYTES, None))
        self.assertEqual(report["exit_code"], 0)


class RepeatedMaterializeTests(unittest.TestCase):
    """The `repeated` materialize directive writes `head` `count` times, then
    `tail` `count` times, so a hostile nesting case is generated at test time
    rather than committed as a large file."""

    def setUp(self):
        self._src_tmp = tempfile.TemporaryDirectory()
        self._dst_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._src_tmp.cleanup)
        self.addCleanup(self._dst_tmp.cleanup)
        self.src = Path(self._src_tmp.name) / "fixture"
        self.src.mkdir()
        (self.src / "expected.yml").write_text("exit_code: 0\n")
        self.dst = Path(self._dst_tmp.name) / "case"

    def test_repeated_writes_head_then_tail_count_times(self):
        spec = {"materialize": [{"path": "Sources/N.swift", "kind": "repeated",
                                  "head": "struct A {\n", "tail": "}\n", "count": 3}]}
        h.materialize(self.src, self.dst, spec)
        self.assertEqual((self.dst / "Sources" / "N.swift").read_text(),
                         "struct A {\n" * 3 + "}\n" * 3)

    def test_repeated_refuses_a_count_below_one(self):
        spec = {"materialize": [{"path": "N.swift", "kind": "repeated",
                                  "head": "x", "tail": "", "count": 0}]}
        with self.assertRaisesRegex(ValueError, "count"):
            h.materialize(self.src, self.dst, spec)


class MaxBytesAssertionTests(unittest.TestCase):
    """`max_bytes:` bounds a rendered concern file's size in bytes."""

    def test_a_file_over_its_bound_fails(self):
        with self.assertRaises(h.FixtureCaseError):
            h.check_max_bytes("case", {"data-model": "x" * 11}, {"data-model": 10})

    def test_a_file_at_its_bound_passes(self):
        h.check_max_bytes("case", {"data-model": "x" * 10}, {"data-model": 10})

    def test_a_missing_file_fails(self):
        with self.assertRaises(h.FixtureCaseError):
            h.check_max_bytes("case", {}, {"data-model": 10})


class StrictReportCheckTests(unittest.TestCase):
    """`strict: true` runs a third derive with `--strict` and checks its whole
    report against the fixture's one `exit_code`. The failure direction: the
    harness control fixture exits 0 plainly, but under `--strict` its
    decision-index concern is not populated, so the strict derive exits 1 and
    the case must fail. The controls feed the same copy a strict report that
    exits 0, which passes, and one that opens an outside path, which fails."""

    def _strict_copy(self) -> Path:
        import shutil
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        fixture = Path(d.name) / "00-harness-control-strict"
        shutil.copytree(h.FIXTURES_ROOT / "00-harness-control", fixture)
        expected = fixture / "expected.yml"
        expected.write_text(expected.read_text(encoding="utf-8") + "\nstrict: true\n",
                            encoding="utf-8")
        return fixture

    def _run_with_strict_report(self, fixture, strict_report):
        from unittest import mock
        real = h._run_child

        def fake(argv, allowed, timeout):
            payload = real(argv, allowed, timeout)
            if "--strict" in argv:
                payload.update(strict_report)
            return payload

        with mock.patch.object(h, "_run_child", side_effect=fake):
            return h.run_case(fixture, timeout=60)

    def test_a_strict_derive_that_exits_1_fails_the_case(self):
        with self.assertRaisesRegex(h.FixtureCaseError, r"--strict derive exit 1 != 0"):
            h.run_case(self._strict_copy(), timeout=60)

    def test_control_a_strict_report_exiting_0_passes(self):
        report = self._run_with_strict_report(self._strict_copy(), {"exit_code": 0})
        self.assertEqual(report["ok"], True)

    def test_a_strict_report_opening_an_outside_path_fails_the_case(self):
        with self.assertRaisesRegex(h.FixtureCaseError, r"--strict derive opened paths outside"):
            self._run_with_strict_report(self._strict_copy(),
                                         {"exit_code": 0, "outside_opens": ["/etc/hosts"]})
