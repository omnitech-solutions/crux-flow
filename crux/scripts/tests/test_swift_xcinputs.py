"""Tests for the Swift pack's non-pbxproj Xcode project readers (ADR-0130):
`crux.arch.packs.swift_xcinputs`. String-level tests over `read_workspace`
and `scan_xcconfig`, plus the module's stdlib-only import self-check.

These are unit tests over the two pure reader functions directly — not the
data-driven fixture harness (`xcode_fixture_harness.py`), which exercises the whole
derive through a subprocess. Every fixture here is written fresh in this
file; nothing is copied from any checkout.
"""

from __future__ import annotations

import ast
import os
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]  # crux/scripts
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _timing  # noqa: E402

from crux.arch.packs import swift_xcinputs as xi  # noqa: E402


def real_path_open_recorder():
    """An `open` audit hook and the two lists it fills: each opened path as
    passed, and the same path resolved. An open through an aliased name is
    recorded under that name, so only the resolved list shows which file was
    read (see `test_external_entity_aliasing_control_is_caught_only_by_real_path`)."""
    as_passed, resolved = [], []

    def hook(event, args):
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            as_passed.append(os.fsdecode(args[0]))
            resolved.append(os.path.realpath(args[0]))
    return hook, as_passed, resolved


def _residual_tuples(residuals):
    return [(r.klass, r.path, r.lines, r.detail) for r in residuals]


class WorkspaceReaderTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _mk(self, rel: str, is_dir: bool = True, with_package: bool = False,
            with_pbxproj: bool = False) -> None:
        p = self.root / rel
        if is_dir:
            p.mkdir(parents=True, exist_ok=True)
            if with_package:
                (p / "Package.swift").write_text("// swift-tools-version:5.9\n")
            if with_pbxproj:
                (p / "project.pbxproj").write_text("// !$*UTF8*$!\n")
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("")

    def test_container_and_group_refs_followed(self):
        self._mk("App.xcodeproj", with_pbxproj=True)
        self._mk("Modules/RSCore", with_package=True)
        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <Workspace version = "1.0">
           <FileRef location = "container:App.xcodeproj"></FileRef>
           <Group location = "container:Modules" name = "Modules">
              <FileRef location = "group:RSCore"></FileRef>
           </Group>
        </Workspace>
        """
        facts = xi.read_workspace(self.root, "", xml)
        self.assertEqual(
            list(facts.refs),
            [xi.WorkspaceRef("package", "Modules/RSCore"), xi.WorkspaceRef("xcodeproject", "App.xcodeproj")],
        )
        self.assertEqual(facts.residuals, ())

    def test_self_in_standalone_workspace_is_unresolved_reference(self):
        xml = b"""<Workspace version = "1.0">
           <FileRef location = "self:"></FileRef>
        </Workspace>"""
        facts = xi.read_workspace(self.root, "App.xcworkspace", xml)
        self.assertEqual(facts.refs, ())
        self.assertEqual(len(facts.residuals), 1)
        self.assertEqual(facts.residuals[0].klass, "unresolved-reference")

    def test_absolute_and_developer_are_path_escape(self):
        xml = b"""<Workspace version = "1.0">
           <FileRef location = "absolute:/etc/passwd"></FileRef>
           <FileRef location = "developer:Foo.xcodeproj"></FileRef>
        </Workspace>"""
        facts = xi.read_workspace(self.root, "", xml)
        self.assertEqual(facts.refs, ())
        klasses = [r.klass for r in facts.residuals]
        self.assertEqual(klasses, ["path-escape", "path-escape"])

    def test_unknown_scheme_is_unresolved_reference(self):
        xml = b"""<Workspace version = "1.0">
           <FileRef location = "bogus:Foo.xcodeproj"></FileRef>
        </Workspace>"""
        facts = xi.read_workspace(self.root, "", xml)
        self.assertEqual([r.klass for r in facts.residuals], ["unresolved-reference"])

    def test_contained_reference_naming_nothing_is_missing_input(self):
        xml = b"""<Workspace version = "1.0">
           <FileRef location = "container:Nope.xcodeproj"></FileRef>
        </Workspace>"""
        facts = xi.read_workspace(self.root, "", xml)
        self.assertEqual(facts.refs, ())
        self.assertEqual([r.klass for r in facts.residuals], ["missing-input"])

    def test_a_reference_into_a_pruned_directory_renders_nothing(self):
        # ADR-0130 clause 11: a project or package the workspace names that
        # sits in the excluded set renders nothing, and ADR-0129 clause 8
        # names nothing inside it. Present or missing, the reference is
        # refused by name before any filesystem access.
        self._mk("Pods/Pods.xcodeproj", with_pbxproj=True)
        xml = b"""<Workspace version = "1.0">
           <FileRef location = "container:Pods/Pods.xcodeproj"></FileRef>
           <FileRef location = "container:Carthage/Checkouts/Missing"></FileRef>
           <FileRef location = "container:.build/checkouts/Missing"></FileRef>
        </Workspace>"""
        facts = xi.read_workspace(self.root, "", xml)
        self.assertEqual(facts.refs, ())
        self.assertEqual(facts.residuals, ())

    def test_positive_control_the_same_reference_outside_a_pruned_directory_is_followed(self):
        self._mk("Vendor/Pods.xcodeproj", with_pbxproj=True)
        xml = b"""<Workspace version = "1.0">
           <FileRef location = "container:Vendor/Pods.xcodeproj"></FileRef>
           <FileRef location = "container:Vendor/Missing"></FileRef>
        </Workspace>"""
        facts = xi.read_workspace(self.root, "", xml)
        self.assertEqual(facts.refs, (xi.WorkspaceRef("xcodeproject", "Vendor/Pods.xcodeproj"),))
        self.assertEqual([r.klass for r in facts.residuals], ["missing-input"])

    def test_over_long_reference_name_is_missing_input_never_an_os_error(self):
        """A 400-byte name component makes `stat` fail with ENAMETOOLONG,
        which `Path.exists()` re-raises. Workspace content must never make
        the deriver exit 2 (ADR-0130 clause 2), and nothing can sit at a
        path the host cannot even name, so the reference is missing input."""
        xml = ('<Workspace version = "1.0"><FileRef location = "group:%s">'
               '</FileRef></Workspace>' % ("a" * 400)).encode()
        facts = xi.read_workspace(self.root, "", xml)
        self.assertEqual(facts.refs, ())
        self.assertEqual([r.klass for r in facts.residuals], ["missing-input"])

    def test_case_mismatched_reference_is_missing_input_on_every_host(self):
        # ADR-0130 clause 3, and byte-stable output across hosts: a
        # reference is matched byte for byte against the listing, so
        # `app.xcodeproj` never finds `App.xcodeproj`, on a case-folding host
        # or any other.
        self._mk("App.xcodeproj", with_pbxproj=True)
        xml = b"""<Workspace version = "1.0">
           <FileRef location = "group:app.xcodeproj"></FileRef>
        </Workspace>"""
        facts = xi.read_workspace(self.root, "", xml)
        self.assertEqual(facts.refs, ())
        self.assertEqual([r.klass for r in facts.residuals], ["missing-input"])

    def test_symlinked_package_manifest_is_not_followed(self):
        # The sibling of the local-reference guard: a `Package.swift` that is
        # a symlink does not make its directory a package container.
        self._mk("Modules/Pkg")
        outside = __import__("tempfile").TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        target = Path(outside.name) / "Package.swift"
        target.write_text("// swift-tools-version:5.9\n")
        try:
            (self.root / "Modules" / "Pkg" / "Package.swift").symlink_to(target)
        except OSError:
            self.skipTest("symlinks unavailable on this platform")
        xml = b"""<Workspace version = "1.0">
           <FileRef location = "group:Modules/Pkg"></FileRef>
        </Workspace>"""
        facts = xi.read_workspace(self.root, "", xml)
        self.assertEqual(facts.refs, ())

    def test_existing_reference_not_followed_form_is_silent(self):
        self._mk("Notes.txt", is_dir=False)
        xml = b"""<Workspace version = "1.0">
           <FileRef location = "container:Notes.txt"></FileRef>
        </Workspace>"""
        facts = xi.read_workspace(self.root, "", xml)
        self.assertEqual(facts.refs, ())
        self.assertEqual(facts.residuals, ())

    def test_traversal_escaping_checkout_is_path_escape(self):
        xml = b"""<Workspace version = "1.0">
           <FileRef location = "container:../../outside.xcodeproj"></FileRef>
        </Workspace>"""
        facts = xi.read_workspace(self.root, "Sub/App.xcworkspace", xml)
        self.assertEqual([r.klass for r in facts.residuals], ["path-escape"])

    def test_contained_traversal_is_followed(self):
        self._mk("Shared/App.xcodeproj", with_pbxproj=True)
        xml = b"""<Workspace version = "1.0">
           <FileRef location = "container:../Shared/App.xcodeproj"></FileRef>
        </Workspace>"""
        facts = xi.read_workspace(self.root, "Sub/App.xcworkspace", xml)
        self.assertEqual(list(facts.refs), [xi.WorkspaceRef("xcodeproject", "Shared/App.xcodeproj")])
        self.assertEqual(facts.residuals, ())

    def test_locations_resolve_against_the_directory_holding_the_bundle(self):
        # Xcode resolves `group:` and `container:` in
        # `Sub/App.xcworkspace/contents.xcworkspacedata` against `Sub`, the
        # directory holding the bundle, never inside the bundle itself.
        self._mk("Sub/App.xcodeproj", with_pbxproj=True)
        self._mk("Sub/Modules/Pkg", with_package=True)
        xml = b"""<Workspace version = "1.0">
           <FileRef location = "group:App.xcodeproj"></FileRef>
           <FileRef location = "container:Modules/Pkg"></FileRef>
        </Workspace>"""
        facts = xi.read_workspace(self.root, "Sub/App.xcworkspace", xml)
        self.assertEqual(facts.path, "Sub/App.xcworkspace/contents.xcworkspacedata")
        self.assertEqual(list(facts.refs), [xi.WorkspaceRef("package", "Sub/Modules/Pkg"),
                                            xi.WorkspaceRef("xcodeproject", "Sub/App.xcodeproj")])
        self.assertEqual(facts.residuals, ())

    def test_dtd_declaration_refused_before_expansion(self):
        xml = b"""<?xml version="1.0"?>
        <!DOCTYPE Workspace [ <!ENTITY x "y"> ]>
        <Workspace version = "1.0"></Workspace>"""
        facts = xi.read_workspace(self.root, "", xml)
        self.assertEqual(len(facts.residuals), 1)
        self.assertEqual(facts.residuals[0].klass, "project-unreadable")
        self.assertEqual(facts.residuals[0].detail, "entity-declaration")

    def test_malformed_xml_is_project_unreadable_malformed(self):
        facts = xi.read_workspace(self.root, "", b"<Workspace version=\"1.0\">")
        self.assertEqual(facts.residuals[0].detail, "malformed")

    def test_root_not_workspace_is_malformed(self):
        facts = xi.read_workspace(self.root, "", b"<NotAWorkspace></NotAWorkspace>")
        self.assertEqual(facts.residuals[0].detail, "malformed")

    def test_nesting_bound_64_ok_65_refused(self):
        def nested(n: int) -> bytes:
            open_tags = "<Workspace version=\"1.0\">" + "<Group location=\"container:\">" * (n - 1)
            close_tags = "</Group>" * (n - 1) + "</Workspace>"
            return (open_tags + close_tags).encode()

        facts_ok = xi.read_workspace(self.root, "", nested(64))
        self.assertEqual([r.klass for r in facts_ok.residuals if r.klass == "project-unreadable"], [])

        facts_refused = xi.read_workspace(self.root, "", nested(65))
        self.assertEqual([r.detail for r in facts_refused.residuals], ["too-deep"])

    def test_nesting_bound_63_ok(self):
        """A control beside the 64/65 boundary test — one level
        shallower than the admitted bound stays admitted too."""
        def nested(n: int) -> bytes:
            open_tags = "<Workspace version=\"1.0\">" + "<Group location=\"container:\">" * (n - 1)
            close_tags = "</Group>" * (n - 1) + "</Workspace>"
            return (open_tags + close_tags).encode()

        facts = xi.read_workspace(self.root, "", nested(63))
        self.assertEqual([r.klass for r in facts.residuals if r.klass == "project-unreadable"], [])

    def test_bare_doctype_is_refused(self):
        """ADR-0130 clause 11: a bare `<!DOCTYPE Workspace>` with no internal subset still
        fires `StartDoctypeDeclHandler` and is refused before any expansion
        could occur."""
        xml = b'''<?xml version="1.0"?>
        <!DOCTYPE Workspace>
        <Workspace version = "1.0"></Workspace>'''
        facts = xi.read_workspace(self.root, "", xml)
        self.assertEqual(facts.residuals[0].klass, "project-unreadable")
        self.assertEqual(facts.residuals[0].detail, "entity-declaration")

    def test_parameter_entity_is_refused(self):
        """ADR-0130 clause 11: a parameter entity (`%pe;`) declared in the internal
        subset is refused at the DOCTYPE boundary, before the parameter
        entity is ever parsed or expanded."""
        xml = b'''<?xml version="1.0"?>
        <!DOCTYPE Workspace [ <!ENTITY % pe "bogus"> ]>
        <Workspace version = "1.0"></Workspace>'''
        facts = xi.read_workspace(self.root, "", xml)
        self.assertEqual(facts.residuals[0].klass, "project-unreadable")
        self.assertEqual(facts.residuals[0].detail, "entity-declaration")

    def test_billion_laughs_is_refused(self):
        """ADR-0130 clause 11: a classic exponential-entity-expansion document is refused
        at the DOCTYPE boundary, before any entity is ever expanded."""
        xml = b'''<?xml version="1.0"?>
        <!DOCTYPE Workspace [
          <!ENTITY a0 "laugh">
          <!ENTITY a1 "&a0;&a0;&a0;&a0;&a0;&a0;&a0;&a0;&a0;&a0;">
          <!ENTITY a2 "&a1;&a1;&a1;&a1;&a1;&a1;&a1;&a1;&a1;&a1;">
          <!ENTITY a3 "&a2;&a2;&a2;&a2;&a2;&a2;&a2;&a2;&a2;&a2;">
        ]>
        <Workspace version = "1.0">&a3;</Workspace>'''
        facts = xi.read_workspace(self.root, "", xml)
        self.assertEqual(facts.residuals[0].klass, "project-unreadable")
        self.assertEqual(facts.residuals[0].detail, "entity-declaration")

    def test_external_entity_canary_never_opened(self):
        canary = self.root / "canary.txt"
        canary.write_text("secret")
        canary_uri = canary.resolve().as_uri()
        xml = f"""<?xml version="1.0"?>
        <!DOCTYPE Workspace [
          <!ENTITY xxe SYSTEM "{canary_uri}">
        ]>
        <Workspace version = "1.0">
           <FileRef location = "group:&xxe;"></FileRef>
        </Workspace>""".encode()

        # Real path on both sides: expat would open the entity through the
        # resolved URI (`/private/var/...` on macOS), while `canary` is the
        # unresolved `/var/...` name, so a by-name comparison read green
        # there even if the entity had been fetched.
        hook, _as_passed, opened = real_path_open_recorder()
        sys.addaudithook(hook)  # cannot be removed; harmless for later tests.
        facts = xi.read_workspace(self.root, "", xml)

        self.assertEqual(facts.residuals[0].detail, "entity-declaration")
        self.assertNotIn(os.path.realpath(canary), opened)

    def test_external_entity_canary_positive_control_would_open(self):
        """Proves the canary above is not vacuous: an unguarded expat parser
        configuration, wired to actually resolve an external entity by
        opening the file it names, DOES have that open observed by the audit
        hook — so the guarded reader's silence in the test above is a real
        refusal, not an artifact of the canary never being reachable."""
        import xml.parsers.expat

        canary = self.root / "canary2.txt"
        canary.write_text("secret2")

        opened = []

        def hook(event, args):
            if event == "open":
                opened.append(str(args[0]))

        sys.addaudithook(hook)

        parser = xml.parsers.expat.ParserCreate()

        def external_entity_ref(_context, _base, system_id, _public_id):
            # An UNSAFE handler that actually performs the fetch, standing in
            # for what an unguarded parser configuration would do.
            with open(system_id, "rb"):
                pass
            return 1

        parser.ExternalEntityRefHandler = external_entity_ref
        xml_doc = f"""<?xml version="1.0"?>
        <!DOCTYPE Workspace [
          <!ENTITY xxe SYSTEM "{canary}">
        ]>
        <Workspace version = "1.0">&xxe;</Workspace>""".encode()
        parser.Parse(xml_doc, True)

        self.assertIn(str(canary), opened)

    def test_external_entity_aliasing_control_is_caught_only_by_real_path(self):
        """The discriminating control for the canary test: an unguarded
        fetch that names the canary through an aliased directory (an
        in-checkout symlink to the canary's real directory) is caught by the
        canary test's own recorder on its resolved list, and missed on its
        as-passed list. So the canary test's real-path comparison is what
        lets it fail, on every host, not only where `/var` aliases."""
        import xml.parsers.expat

        (self.root / "real").mkdir()
        canary = self.root / "real" / "canary3.txt"
        canary.write_text("secret3")
        try:
            (self.root / "alias").symlink_to(self.root / "real", target_is_directory=True)
        except OSError:
            self.skipTest("symlinks unavailable on this platform")
        aliased = self.root / "alias" / "canary3.txt"

        hook, as_passed, resolved = real_path_open_recorder()
        sys.addaudithook(hook)  # cannot be removed; harmless for later tests.

        parser = xml.parsers.expat.ParserCreate()

        def external_entity_ref(_context, _base, system_id, _public_id):
            # An UNSAFE handler that performs the fetch, as in the control above.
            with open(system_id, "rb"):
                pass
            return 1

        parser.ExternalEntityRefHandler = external_entity_ref
        xml_doc = f"""<?xml version="1.0"?>
        <!DOCTYPE Workspace [
          <!ENTITY xxe SYSTEM "{aliased}">
        ]>
        <Workspace version = "1.0">&xxe;</Workspace>""".encode()
        parser.Parse(xml_doc, True)

        self.assertIn(os.path.realpath(canary), resolved)
        self.assertIn(str(aliased), as_passed)
        self.assertNotIn(str(canary), as_passed)
        self.assertNotIn(os.path.realpath(canary), as_passed)


class ResidualDetailNeverEchoesAWrittenValueTests(unittest.TestCase):
    """A `path-escape` residual's
    `detail` must never carry the location/target value as written (ADR-0130
    clause 3: never the path as written or its target; clause 14: no host
    path on any residual line). Closed-vocabulary tokens only."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_workspace_absolute_location_detail_has_no_host_path(self):
        secret = "/Users/secret/Path/App.xcodeproj"
        xml = f'<Workspace version="1.0"><FileRef location="absolute:{secret}"></FileRef></Workspace>'.encode()
        facts = xi.read_workspace(self.root, "", xml)
        self.assertEqual(facts.residuals[0].klass, "path-escape")
        self.assertNotIn(secret, facts.residuals[0].detail)
        self.assertNotIn("absolute:", facts.residuals[0].detail)

    def test_workspace_group_escape_detail_has_no_written_value(self):
        xml = b'<Workspace version="1.0"><Group location="absolute:/etc/secret"><FileRef location="container:x"></FileRef></Group></Workspace>'
        facts = xi.read_workspace(self.root, "", xml)
        self.assertEqual(facts.residuals[0].klass, "path-escape")
        self.assertNotIn("/etc/secret", facts.residuals[0].detail)

    def test_xcconfig_escaping_include_detail_has_no_target_value(self):
        target = "../../../SharedXcodeSettings/DeveloperSettings.xcconfig"
        raw = f'#include? "{target}"\n'.encode()
        facts = xi.scan_xcconfig("App.xcconfig", raw)
        self.assertEqual(facts.residuals[0].klass, "path-escape")
        self.assertNotIn(target, facts.residuals[0].detail)
        self.assertNotIn("SharedXcodeSettings", facts.residuals[0].detail)

    def test_xcconfig_escaping_include_required_detail_has_no_target_value(self):
        target = "../../Outside.xcconfig"
        raw = f'#include "{target}"\n'.encode()
        facts = xi.scan_xcconfig("App.xcconfig", raw)
        self.assertEqual(facts.residuals[0].klass, "path-escape")
        self.assertNotIn(target, facts.residuals[0].detail)
        self.assertNotIn("Outside.xcconfig", facts.residuals[0].detail)


class WorkspaceFileBoundaryTests(unittest.TestCase):
    """ADR-0130 clause 2: every exception a per-file step raises is caught
    at that file's boundary. `swift._workspace_facts_list` is the workspace
    reader's boundary, so an exception out of `read_workspace` renders one
    `project-unreadable` line and the other workspaces still read."""

    def test_reader_exception_renders_one_residual_per_file(self):
        from unittest import mock

        from crux.arch.packs import swift

        reads = swift.ProjectReads(files={
            "A.xcworkspace/contents.xcworkspacedata": b"<Workspace/>",
            "B.xcworkspace/contents.xcworkspacedata": b"<Workspace/>",
        })
        real = xi.read_workspace

        # The reader also takes the concern's work budget (`MAX_WORK_UNITS`).
        def flaky(root, ws_dir, raw, *budget):
            if ws_dir == "A.xcworkspace":
                raise OSError("boom")
            return real(root, ws_dir, raw, *budget)

        with mock.patch.object(swift.swift_xcinputs, "read_workspace", side_effect=flaky):
            facts = swift._workspace_facts_list(Path("."), reads)
        self.assertEqual([f.path for f in facts],
                         ["A.xcworkspace/contents.xcworkspacedata",
                          "B.xcworkspace/contents.xcworkspacedata"])
        self.assertEqual(_residual_tuples(facts[0].residuals),
                         [("project-unreadable", "A.xcworkspace/contents.xcworkspacedata",
                           None, "read-failed")])
        self.assertEqual(facts[1].residuals, ())


class InputRefusalKindTests(unittest.TestCase):
    """ADR-0130 clause 14: `not-regular` is a symlink, FIFO, device or
    directory at the input path. A walk-reached input can escape only
    through a symlink at its own path, because the walk descends no
    directory symlink, so the safe-read `escape` reason is `not-regular`,
    exactly as a symlink pointing inside the checkout is. `read-failed` is
    reserved for any other OS error."""

    def test_escaping_symlink_input_is_not_regular(self):
        from crux.arch.packs import swift, swift_xcode

        reads = swift.ProjectReads(refusals={
            "Base.xcconfig": "escape",
            "App.xcworkspace/contents.xcworkspacedata": "escape",
        })
        self.assertEqual(list(swift._xcconfig_residuals(reads)),
                         [("project-unreadable", "Base.xcconfig", None, "not-regular")])
        ws = swift._workspace_facts_list(Path("."), reads)
        self.assertEqual(_residual_tuples(ws[0].residuals),
                         [("project-unreadable", "App.xcworkspace/contents.xcworkspacedata",
                           None, "not-regular")])
        self.assertEqual(swift_xcode._REFUSAL_TO_KIND.get("escape"), "not-regular")


class SymlinkContainmentTests(unittest.TestCase):
    """ADR-0130 clause 3: `_classify_ref` must
    refuse a symlinked component that resolves outside the checkout root
    BEFORE any `exists`/`is_dir`/`is_file` stat, rather than silently
    following it."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_symlink_escaping_checkout_is_path_escape_and_not_stat_audited(self):
        outside_tmp = __import__("tempfile").TemporaryDirectory()
        self.addCleanup(outside_tmp.cleanup)
        outside_root = Path(outside_tmp.name)
        evil = outside_root / "Evil.xcodeproj"
        evil.mkdir()
        (evil / "project.pbxproj").write_text("// !$*UTF8*$!\n")
        try:
            (self.root / "Evil.xcodeproj").symlink_to(evil)
        except OSError:
            self.skipTest("symlinks unavailable on this platform")

        opened = []

        def hook(event, args):
            if event in ("open", "os.stat", "os.listdir"):
                try:
                    real = str(args[0])
                except Exception:
                    real = ""
                if str(outside_root) in real:
                    opened.append((event, real))

        sys.addaudithook(hook)
        xml = b'<Workspace version="1.0"><FileRef location="group:Evil.xcodeproj"></FileRef></Workspace>'
        facts = xi.read_workspace(self.root, "", xml)
        self.assertEqual(facts.refs, ())
        self.assertEqual([r.klass for r in facts.residuals], ["path-escape"])
        self.assertEqual(opened, [])

    def _read_recording_scandir(self, location: str):
        """`read_workspace` over one FileRef, recording every directory
        `os.scandir` lists."""
        from unittest import mock
        listed = []
        real_scandir = os.scandir

        def recording(path="."):
            listed.append(str(path))
            return real_scandir(path)

        xml = ('<Workspace version="1.0"><FileRef location="%s"></FileRef></Workspace>'
               % location).encode()
        with mock.patch.object(xi.os, "scandir", recording):
            facts = xi.read_workspace(self.root, "", xml)
        return facts, listed

    def _symlink(self, link: str, target: str) -> None:
        try:
            (self.root / link).symlink_to(self.root / target, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks unavailable on this platform")

    def test_contained_symlinked_package_directory_is_not_descended(self):
        """ADR-0130 clause 3: no directory symlink is descended. An
        in-checkout `PodAlias -> Pods/PodPkg` names a package only through
        the symlink; it is neither listed nor followed, and renders one
        `unresolved-reference` line."""
        self._mk("Pods/PodPkg", with_package=True)
        self._symlink("PodAlias", "Pods/PodPkg")
        facts, listed = self._read_recording_scandir("group:PodAlias")
        self.assertEqual(facts.refs, ())
        self.assertEqual(_residual_tuples(facts.residuals),
                         [("unresolved-reference", "contents.xcworkspacedata", (1, 1),
                           xi.SYMLINKED_WORKSPACE_REFERENCE_DETAIL)])
        self.assertEqual([p for p in listed if "PodAlias" in p or "Pods" in p], [])

    def test_symlinked_intermediate_directory_is_not_descended(self):
        self._mk("Real/App.xcodeproj", with_pbxproj=True)
        self._symlink("Link", "Real")
        facts, listed = self._read_recording_scandir("group:Link/App.xcodeproj")
        self.assertEqual(facts.refs, ())
        self.assertEqual([r.klass for r in facts.residuals], ["unresolved-reference"])
        self.assertEqual([p for p in listed if "Link" in p], [])

    def test_symlinked_xcodeproj_bundle_is_not_followed(self):
        self._mk("Real.xcodeproj", with_pbxproj=True)
        self._symlink("Alias.xcodeproj", "Real.xcodeproj")
        facts, _listed = self._read_recording_scandir("group:Alias.xcodeproj")
        self.assertEqual(facts.refs, ())
        self.assertEqual([(r.klass, r.detail) for r in facts.residuals],
                         [("unresolved-reference", xi.SYMLINKED_WORKSPACE_REFERENCE_DETAIL)])

    def test_positive_control_the_same_trees_without_a_symlink_resolve(self):
        """The trees above with the real path named in place of the symlink
        resolve, so the refusal is the symlink's, not the tree's."""
        self._mk("Vendor/PodPkg", with_package=True)
        self._mk("Real/App.xcodeproj", with_pbxproj=True)
        self._mk("Solo.xcodeproj", with_pbxproj=True)
        for location, want in (
                ("group:Vendor/PodPkg", xi.WorkspaceRef("package", "Vendor/PodPkg")),
                ("group:Real/App.xcodeproj", xi.WorkspaceRef("xcodeproject", "Real/App.xcodeproj")),
                ("group:Solo.xcodeproj", xi.WorkspaceRef("xcodeproject", "Solo.xcodeproj"))):
            with self.subTest(location=location):
                facts, _listed = self._read_recording_scandir(location)
                self.assertEqual(list(facts.refs), [want])
                self.assertEqual(facts.residuals, ())

    def _mk(self, rel: str, with_package: bool = False, with_pbxproj: bool = False) -> None:
        p = self.root / rel
        p.mkdir(parents=True, exist_ok=True)
        if with_package:
            (p / "Package.swift").write_text("// swift-tools-version:5.9\n")
        if with_pbxproj:
            (p / "project.pbxproj").write_text("// !$*UTF8*$!\n")


class ResidualNamesAreCellEscapedTests(unittest.TestCase):
    """ADR-0130 clause 14: a path read from the checkout renders in its
    `_cell` form inside a residual detail, never raw."""

    HOSTILE = "x`|y"

    def test_xcconfig_include_path_is_cell_escaped(self):
        from crux.arch.core import _cell
        raw = ('#include "%s.xcconfig"\n' % self.HOSTILE).encode()
        facts = xi.scan_xcconfig("App.xcconfig", raw)
        want = "#include '%s.xcconfig'" % _cell(self.HOSTILE)
        self.assertEqual([(r.klass, r.detail) for r in facts.residuals], [("xcconfig-include", want)])

    def test_missing_workspace_reference_is_cell_escaped(self):
        from crux.arch.core import _cell
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            xml = ('<Workspace version="1.0"><FileRef location="group:%s"></FileRef></Workspace>'
                   % self.HOSTILE).encode()
            facts = xi.read_workspace(Path(d), "", xml)
        self.assertEqual([(r.klass, r.detail) for r in facts.residuals],
                         [("missing-input", "workspace reference '%s'" % _cell(self.HOSTILE))])


class XcconfigScanTests(unittest.TestCase):
    def test_blank_and_comment_lines_ignored(self):
        raw = b"\n// a comment\n   \n// another\n"
        facts = xi.scan_xcconfig("Config.xcconfig", raw)
        self.assertEqual(facts.residuals, ())

    def test_plain_setting_accepted_silently(self):
        raw = b"PRODUCT_NAME = MyApp\n"
        facts = xi.scan_xcconfig("Config.xcconfig", raw)
        self.assertEqual(facts.residuals, ())

    def test_bracketed_condition_is_conditional_setting(self):
        raw = b'SWIFT_VERSION[sdk=iphoneos*] = 5.0\n'
        facts = xi.scan_xcconfig("Config.xcconfig", raw)
        self.assertEqual(_residual_tuples(facts.residuals),
                          [("conditional-setting", "Config.xcconfig", (1, 1), "SWIFT_VERSION[sdk=iphoneos*]")])

    def test_excluded_source_file_names_always_conditional(self):
        raw = b"EXCLUDED_SOURCE_FILE_NAMES = Foo.swift\n"
        facts = xi.scan_xcconfig("Config.xcconfig", raw)
        self.assertEqual(facts.residuals[0].klass, "conditional-setting")

    def test_included_source_file_names_always_conditional(self):
        raw = b"INCLUDED_SOURCE_FILE_NAMES = Foo.swift\n"
        facts = xi.scan_xcconfig("Config.xcconfig", raw)
        self.assertEqual(facts.residuals[0].klass, "conditional-setting")

    def test_contained_include_is_xcconfig_include(self):
        raw = b'#include "Shared/Base.xcconfig"\n'
        facts = xi.scan_xcconfig("Config/App.xcconfig", raw)
        self.assertEqual(_residual_tuples(facts.residuals),
                          [("xcconfig-include", "Config/App.xcconfig", (1, 1),
                            "#include 'Config/Shared/Base.xcconfig'")])

    def test_optional_include_marks_optional_in_tag(self):
        raw = b'#include? "Missing.xcconfig"\n'
        facts = xi.scan_xcconfig("App.xcconfig", raw)
        self.assertEqual(facts.residuals[0].detail, "#include? 'Missing.xcconfig'")

    def test_escaping_include_is_path_escape(self):
        raw = b'#include "../../Outside.xcconfig"\n'
        facts = xi.scan_xcconfig("App.xcconfig", raw)
        self.assertEqual(facts.residuals[0].klass, "path-escape")

    def test_unsupported_line(self):
        raw = b"this is not xcconfig grammar at all!!\n"
        facts = xi.scan_xcconfig("App.xcconfig", raw)
        self.assertEqual(facts.residuals[0].klass, "unsupported-project-form")

    def test_undecodable_utf8_is_project_unreadable(self):
        raw = b"\xff\xfe not utf-8"
        facts = xi.scan_xcconfig("App.xcconfig", raw)
        self.assertEqual(_residual_tuples(facts.residuals),
                          [("project-unreadable", "App.xcconfig", None, "undecodable")])

    def test_include_cycle_each_file_scanned_independently(self):
        """No file is ever followed, so an include cycle cannot hang this
        reader: each file renders its own `xcconfig-include` line and
        nothing more, whether or not the target it names exists or itself
        includes the first file back."""
        a = xi.scan_xcconfig("A.xcconfig", b'#include "B.xcconfig"\n')
        b = xi.scan_xcconfig("B.xcconfig", b'#include "A.xcconfig"\n')
        self.assertEqual(len(a.residuals), 1)
        self.assertEqual(len(b.residuals), 1)
        self.assertEqual(a.residuals[0].klass, "xcconfig-include")
        self.assertEqual(b.residuals[0].klass, "xcconfig-include")

    def test_output_identical_whether_or_not_include_target_exists(self):
        raw = b'#include "Elsewhere/Base.xcconfig"\n'
        facts_a = xi.scan_xcconfig("App.xcconfig", raw)
        facts_b = xi.scan_xcconfig("App.xcconfig", raw)
        self.assertEqual(facts_a, facts_b)

    def test_no_filesystem_access_at_all(self):
        """`scan_xcconfig` touches no filesystem path: patching `Path.exists`
        and `Path.is_file` to raise proves nothing in this function calls
        either, even on an include line whose target may or may not be on
        disk."""
        from unittest import mock

        raw = b'#include "Elsewhere/Base.xcconfig"\nKEY[foo] = 1\n'
        with mock.patch.object(Path, "exists", side_effect=AssertionError("fs touched")), \
             mock.patch.object(Path, "is_file", side_effect=AssertionError("fs touched")):
            facts = xi.scan_xcconfig("App.xcconfig", raw)
        self.assertEqual(len(facts.residuals), 2)


class XcconfigPrunedIncludeTests(unittest.TestCase):
    """ADR-0129 clause 8 over an xcconfig `#include`: a contained include
    whose directory is in or under a pruned directory is refused lexically
    and never named. It renders one `unresolved-reference` with a fixed
    detail naming no path."""

    def test_an_include_into_a_pruned_directory_names_no_path(self):
        from crux.arch.packs import swift_prune
        for target in ("../Pods/T/P.xcconfig", "../Pods/Target Support Files/P/P.debug.xcconfig",
                       ".build/x.xcconfig", "Carthage/x.xcconfig"):
            with self.subTest(target=target):
                raw = ('#include "%s"\n' % target).encode()
                facts = xi.scan_xcconfig("Config/App.xcconfig", raw)
                self.assertEqual(_residual_tuples(facts.residuals),
                                 [("unresolved-reference", "Config/App.xcconfig", (1, 1),
                                   swift_prune.XCCONFIG_INCLUDE_DETAIL)])
                for r in facts.residuals:
                    for word in ("Pods", ".build", "Carthage"):
                        self.assertNotIn(word, r.detail)

    def test_control_a_contained_include_in_a_normal_directory_names_its_path(self):
        facts = xi.scan_xcconfig("Config/App.xcconfig", b'#include? "../Shared/P.xcconfig"\n')
        self.assertEqual(_residual_tuples(facts.residuals),
                         [("xcconfig-include", "Config/App.xcconfig", (1, 1),
                           "#include? 'Shared/P.xcconfig'")])


class NeverResolvedPredicateTests(unittest.TestCase):
    """ADR-0130 clause 3 (a build-setting segment is never resolved):
    `never_resolved` is true for a string holding a
    build-setting reference in any of its three spellings -- `$(NAME)`,
    `${NAME}`, or a bare `$` followed by a letter or underscore -- and false
    for a literal `$` followed by anything else, and for any non-string."""

    def test_every_build_setting_reference_spelling_is_matched(self):
        for value in ("$(A)", "${A}", "$A", "a/$B/c", "$_x", "$(SRCROOT)/..", "${X}/..",
                      "$X/..", "Sub/../$(X)/../A.swift"):
            with self.subTest(value=value):
                self.assertTrue(xi.never_resolved(value))

    def test_a_literal_dollar_and_a_non_string_are_not_matched(self):
        for value in ("$1.swift", "Price$.swift", "a$", "$", "a/$/b", "$-x", "$.swift",
                      "", "Plain/Path.swift", None, 1, ["$(A)"], {"$(A)": "b"}, ("${A}",)):
            with self.subTest(value=value):
                self.assertFalse(xi.never_resolved(value))


class NeverResolvedLocationTests(unittest.TestCase):
    """ADR-0130 clause 3: a `$(VAR)` segment is never resolved, and it is
    classified before any join, so a following `..` cannot cancel it. Outside
    a Sources phase it renders nothing: an xcconfig `#include` carrying `$(`
    and a workspace location carrying `$(` render no line, are never
    followed, and add no container."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "App.xcodeproj").mkdir()
        (self.root / "App.xcodeproj" / "project.pbxproj").write_text("// !$*UTF8*$!\n")
        (self.root / "Modules" / "Core").mkdir(parents=True)
        (self.root / "Modules" / "Core" / "Package.swift").write_text("// swift-tools-version:5.9\n")

    def test_an_xcconfig_include_carrying_a_variable_renders_nothing(self):
        for target in ("$(SRCROOT)/../Shared.xcconfig", "../$(SRCROOT)/Shared.xcconfig",
                       "$(PODS_ROOT)/x.xcconfig", "/abs/$(X)/y.xcconfig",
                       "${SRCROOT}/../Shared.xcconfig", "$SRCROOT/../Shared.xcconfig"):
            with self.subTest(target=target):
                raw = ('#include "%s"\n#include? "%s"\n' % (target, target)).encode()
                facts = xi.scan_xcconfig("Config/App.xcconfig", raw)
                self.assertEqual(facts.residuals, ())

    def test_a_workspace_location_carrying_a_variable_renders_nothing(self):
        for mark in ("$(SRCROOT)", "${SRCROOT}", "$SRCROOT"):
            with self.subTest(mark=mark):
                xml = ("""<Workspace version = "1.0">
                   <FileRef location = "group:%s/../App.xcodeproj"></FileRef>
                   <FileRef location = "container:../%s/App.xcodeproj"></FileRef>
                   <Group location = "group:%s/.." name = "G">
                      <FileRef location = "group:Modules/Core"></FileRef>
                   </Group>
                </Workspace>""" % (mark, mark, mark)).encode()
                facts = xi.read_workspace(self.root, "", xml)
                self.assertEqual(facts.refs, ())
                self.assertEqual(facts.residuals, ())

    def test_control_the_same_locations_without_the_variable_are_followed(self):
        xml = b"""<Workspace version = "1.0">
           <FileRef location = "group:App.xcodeproj"></FileRef>
           <Group location = "group:" name = "G">
              <FileRef location = "group:Modules/Core"></FileRef>
           </Group>
        </Workspace>"""
        facts = xi.read_workspace(self.root, "", xml)
        self.assertEqual(list(facts.refs), [xi.WorkspaceRef("package", "Modules/Core"),
                                            xi.WorkspaceRef("xcodeproject", "App.xcodeproj")])
        facts = xi.scan_xcconfig("Config/App.xcconfig", b'#include "../Shared.xcconfig"\n')
        self.assertEqual([r.klass for r in facts.residuals], ["xcconfig-include"])


class StdlibOnlyImportTests(unittest.TestCase):
    """A planted third-party import turns this red (ADR-0130 clause 17)."""

    def test_swift_xcinputs_imports_stdlib_only(self):
        src_path = SCRIPTS / "crux" / "arch" / "packs" / "swift_xcinputs.py"
        tree = ast.parse(src_path.read_text(encoding="utf-8"))
        top_level_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level_names.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module and (node.level == 0):
                top_level_names.add(node.module.split(".")[0])
        outside_stdlib = {n for n in top_level_names if n not in sys.stdlib_module_names}
        self.assertEqual(outside_stdlib, set())



def _path_walk_symlink_check(root, segments):
    """The `pathlib` walk `has_symlink_component` used before it extended one
    prefix string, kept verbatim as the oracle the incremental walk must
    agree with."""
    current = root
    for seg in segments:
        current = current / seg
        try:
            if current.is_symlink():
                return True
        except OSError:
            return False
    return False


def _remove_tree_holding_one_descriptor(top):
    """Delete everything under `top`, leaving `top` itself, while holding one
    directory descriptor at a time. `shutil.rmtree`, which
    `TemporaryDirectory.cleanup` calls, holds one descriptor per level of
    the tree. A tree deeper than the soft `RLIMIT_NOFILE` therefore fails
    it with `EMFILE`: the Linux default of 1024 is below the ~2,044 levels
    the `PATH_MAX` boundary test builds. The walk descends by name relative
    to the open directory, never follows a symlink, and climbs through
    `..`. It keeps the names it descended, not descriptors."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descended = []
    fd = os.open(top, flags)
    try:
        while True:
            with os.scandir(fd) as entries:
                listed = [(e.name, e.is_dir(follow_symlinks=False)) for e in entries]
            subdir = next((name for name, is_dir in listed if is_dir), None)
            if subdir is not None:
                child = os.open(subdir, flags | getattr(os, "O_NOFOLLOW", 0), dir_fd=fd)
                os.close(fd)
                fd = child
                descended.append(subdir)
                continue
            for name, _is_dir in listed:
                os.unlink(name, dir_fd=fd)
            if not descended:
                return
            parent = os.open("..", flags, dir_fd=fd)
            os.close(fd)
            fd = parent
            os.rmdir(descended.pop(), dir_fd=fd)
    finally:
        os.close(fd)


class SymlinkComponentLinearTimeTests(unittest.TestCase):
    """ADR-0130 clauses 2 and 3: `has_symlink_component` lstats every
    component of a reference, extending one prefix string per component. It
    rebuilt each prefix as a `Path` from every segment before it, which made
    one reference quadratic in its components: a thousand references of 400
    components made a derive take 70 s in review."""

    K = 400

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.segments = ["d"] * self.K
        os.makedirs(os.path.join(self.root, *self.segments))

    def test_deep_references_are_faster_than_the_pathlib_walk(self):
        """The time bound is relative to the `pathlib` walk over the same
        references in the same process, so it holds on a slow host too.
        `PATH_MAX` caps a macOS reference near 500 components, which caps the
        speed-up there: about 7 times at 400 components (2.4 ms against
        17 ms a reference). The bound asks for 2. Both walks are timed on
        `_timing.clock`, the calling thread's CPU time, so time spent waiting
        for a core on a loaded host is charged to neither."""
        started = _timing.clock()
        for _ in range(100):
            self.assertFalse(_path_walk_symlink_check(self.root, self.segments))
        quadratic = _timing.clock() - started
        started = _timing.clock()
        for _ in range(100):
            self.assertFalse(xi.has_symlink_component(self.root, self.segments))
        elapsed = _timing.clock() - started
        self.assertLess(2 * elapsed, quadratic,
                        f"100 references of {self.K} components took {elapsed:.2f} s; "
                        f"the path walk took {quadratic:.2f} s")

    def test_lstat_sees_the_path_walks_strings_in_its_order(self):
        """The walk hands `lstat` exactly the prefixes the `pathlib` walk
        hands it, one per component, in the same order, and stops where that
        walk's verdict is settled."""
        from unittest import mock
        root = self.root / "tree"
        (root / "a" / "b").mkdir(parents=True)
        (root / "a" / "f").write_text("x")
        try:
            os.symlink(root / "a", root / "a" / "b" / "link", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("host refuses symlink creation")

        def recorded(check, segments):
            # `pathlib` on 3.13 reads a link through `os.stat(...,
            # follow_symlinks=False)`, and 3.14 through `os.lstat`: both are
            # the same `lstat`, and both are recorded.
            seen = []
            real_lstat, real_stat = os.lstat, os.stat

            def lstat(path, *args, **kwargs):
                seen.append(os.fspath(path))
                return real_lstat(path, *args, **kwargs)

            def stat_(path, *args, **kwargs):
                if kwargs.get("follow_symlinks", True) is False:
                    seen.append(os.fspath(path))
                return real_stat(path, *args, **kwargs)

            with mock.patch("os.lstat", lstat), mock.patch("os.stat", stat_):
                verdict = check(root, segments)
            return verdict, seen

        for segments, want in ((("a", "b", "link", "x"), True), (("a", "b"), False),
                               (("a", "missing", "x", "y"), False), (("a", "f", "x"), False)):
            with self.subTest(segments=segments):
                verdict, seen = recorded(xi.has_symlink_component, segments)
                old_verdict, old_seen = recorded(_path_walk_symlink_check, segments)
                self.assertEqual((verdict, old_verdict), (want, want))
                self.assertGreater(len(seen), 0, "the wrapper saw no lstat: nothing was compared")
                self.assertEqual(seen, old_seen[:len(seen)])
                self.assertLessEqual(len(seen), len(segments))

    def test_the_incremental_walk_agrees_with_the_path_walk(self):
        """Every verdict equals the `pathlib` walk's: a symlink at each
        depth, the final component included; a symlink to a file, to a
        directory, and dangling; a missing component; a regular file
        mid-path; and the segment forms the incremental walk hands back to
        the `pathlib` walk -- empty, `.`, `..`, an absolute segment, an
        embedded `/` and an embedded NUL."""
        root = self.root / "tree"
        (root / "a" / "b" / "c").mkdir(parents=True)
        (root / "a" / "f").write_text("x")
        (root / "real").mkdir()
        (root / "real" / "file").write_text("x")
        try:
            os.symlink(root / "real", root / "a" / "b" / "dirlink", target_is_directory=True)
            os.symlink(root / "real" / "file", root / "a" / "filelink")
            os.symlink(root / "nowhere", root / "a" / "dangling")
            os.symlink(root / "a", root / "toplink", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("host refuses symlink creation")
        cases = [
            (), ("a",), ("a", "b"), ("a", "b", "c"), ("a", "b", "dirlink"),
            ("a", "b", "dirlink", "file"), ("a", "filelink"), ("a", "dangling"),
            ("a", "dangling", "x"), ("toplink",), ("toplink", "b"), ("missing",),
            ("missing", "b"), ("a", "f"), ("a", "f", "x"), ("a", "", "b"), ("", "toplink"),
            (".", "toplink"), ("a", ".", "b"), ("a", "..", "toplink"), ("a", "b", ".."),
            ("a", "/etc"), ("a/b", "dirlink"), ("toplink/b",), ("a", "b\x00c"),
        ]
        verdicts = []
        for segments in cases:
            with self.subTest(segments=segments):
                want = _path_walk_symlink_check(root, segments)
                self.assertEqual(xi.has_symlink_component(root, segments), want)
                verdicts.append(want)
        self.assertIn(True, verdicts)
        self.assertIn(False, verdicts)

    def test_the_incremental_walk_agrees_at_the_path_max_boundary(self):
        """The `pathlib` walk's `lstat` fails with `ENAMETOOLONG` once a
        prefix reaches `PATH_MAX`, and so answers False past it. The
        incremental walk must stop at the same prefix. A symlink sits in each
        directory near the boundary; the verdicts match on both sides of
        it.

        The tree is deeper than the Linux default soft `RLIMIT_NOFILE` of
        1024, so it is removed by `_remove_tree_holding_one_descriptor`
        before `TemporaryDirectory.cleanup` runs: cleanups run last in,
        first out, and `setUp` registered that one first."""
        try:
            path_max = os.pathconf(self.root, "PC_PATH_MAX")
        except (OSError, ValueError):
            self.skipTest("host reports no PATH_MAX")
        base = len(os.fsencode(str(self.root)))
        depth = (path_max - base) // 2 + 4
        self.addCleanup(_remove_tree_holding_one_descriptor, self.root)
        fd = os.open(self.root, os.O_RDONLY)
        try:
            for k in range(1, depth + 1):
                if k > self.K:
                    os.mkdir("d", dir_fd=fd)
                nfd = os.open("d", os.O_RDONLY, dir_fd=fd)
                os.close(fd)
                fd = nfd
                if depth - 8 <= k:
                    try:
                        os.symlink("d", "s", dir_fd=fd)
                    except (OSError, NotImplementedError):
                        self.skipTest("host refuses symlink creation")
        finally:
            os.close(fd)
        verdicts = []
        for k in range(depth - 8, depth + 1):
            segments = ["d"] * k + ["s"]
            with self.subTest(k=k):
                want = _path_walk_symlink_check(self.root, segments)
                self.assertEqual(xi.has_symlink_component(self.root, segments), want)
                verdicts.append(want)
        # The window straddles the boundary: symlinks before it are seen,
        # those past it are not.
        self.assertIn(True, verdicts)
        self.assertIn(False, verdicts)


class DirectoryVerdictsTests(unittest.TestCase):
    """ADR-0130 clauses 2 and 3: `DirectoryVerdicts` is
    the memo one `read_projects` call hands both path checks. With it, each
    directory is listed once and each prefix `lstat`ed once per call; without
    it (`None`), both checks read the live filesystem on every call, as
    before. A listing is kept only for a directory whose own `lstat` verdict
    already showed a real directory, never a symlink."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def _symlink(self, target, link, **kwargs):
        try:
            os.symlink(target, link, **kwargs)
        except (OSError, NotImplementedError):
            self.skipTest("host refuses symlink creation")

    def test_every_verdict_equals_the_live_checks(self):
        """The case matrix of the incremental-walk test, asked of both checks
        three ways: live, through a fresh memo per case, and through one memo
        shared by every case in order."""
        root = self.root / "tree"
        (root / "a" / "b" / "c").mkdir(parents=True)
        (root / "a" / "f").write_text("x")
        (root / "real").mkdir()
        (root / "real" / "file").write_text("x")
        self._symlink(root / "real", root / "a" / "b" / "dirlink", target_is_directory=True)
        self._symlink(root / "real" / "file", root / "a" / "filelink")
        self._symlink(root / "nowhere", root / "a" / "dangling")
        self._symlink(root / "a", root / "toplink", target_is_directory=True)
        cases = [
            (), ("a",), ("a", "b"), ("a", "b", "c"), ("a", "b", "dirlink"),
            ("a", "b", "dirlink", "file"), ("a", "filelink"), ("a", "dangling"),
            ("a", "dangling", "x"), ("toplink",), ("toplink", "b"), ("missing",),
            ("missing", "b"), ("a", "f"), ("a", "f", "x"), ("a", "", "b"), ("", "toplink"),
            (".", "toplink"), ("a", ".", "b"), ("a", "..", "toplink"), ("a", "b", ".."),
            ("a", "/etc"), ("a/b", "dirlink"), ("toplink/b",), ("a", "b\x00c"), ("A",), ("a", "B"),
        ]
        shared = xi.DirectoryVerdicts(root)
        answers = set()
        for segments in cases:
            with self.subTest(segments=segments):
                want = (xi.has_symlink_component(root, segments), xi.listed_names(root, segments))
                fresh = xi.DirectoryVerdicts(root)
                got_fresh = (xi.has_symlink_component(root, segments, fresh),
                             xi.listed_names(root, segments, fresh))
                got_shared = (xi.has_symlink_component(root, segments, shared),
                              xi.listed_names(root, segments, shared))
                self.assertEqual((got_fresh, got_shared), (want, want))
                answers.add(want)
        self.assertEqual({a for a, _ in answers}, {True, False})
        self.assertEqual({b for _, b in answers}, {True, False})

    def test_a_listing_is_kept_only_after_its_directory_passed_the_symlink_check(self):
        """Listed before any `lstat` verdict, `a` is read live every time: a
        rename is seen. Listed after `has_symlink_component` found `a` a real
        directory, the listing is kept for the memo's life: the same rename
        is not seen through that memo, and is seen through a new one."""
        (self.root / "a" / "b").mkdir(parents=True)
        memo = xi.DirectoryVerdicts(self.root)
        self.assertTrue(xi.listed_names(self.root, ("a", "b"), memo))
        (self.root / "a" / "b").rename(self.root / "a" / "c")
        self.assertFalse(xi.listed_names(self.root, ("a", "b"), memo))
        (self.root / "a" / "c").rename(self.root / "a" / "b")

        memo = xi.DirectoryVerdicts(self.root)
        self.assertFalse(xi.has_symlink_component(self.root, ("a", "b"), memo))
        self.assertTrue(xi.listed_names(self.root, ("a", "b"), memo))
        (self.root / "a" / "b").rename(self.root / "a" / "c")
        self.assertTrue(xi.listed_names(self.root, ("a", "b"), memo))
        self.assertFalse(xi.listed_names(self.root, ("a", "b"), xi.DirectoryVerdicts(self.root)))
        self.assertFalse(xi.listed_names(self.root, ("a", "b")))

    def test_a_symlinked_directory_is_never_listed_into_the_memo(self):
        """`has_symlink_component` finds `link` a symlink. A later
        `listed_names` through it reads live each time, so a change behind
        the link is seen through the same memo."""
        (self.root / "real" / "x").mkdir(parents=True)
        self._symlink(self.root / "real", self.root / "link", target_is_directory=True)
        memo = xi.DirectoryVerdicts(self.root)
        self.assertTrue(xi.has_symlink_component(self.root, ("link", "x"), memo))
        self.assertTrue(xi.listed_names(self.root, ("link", "x"), memo))
        (self.root / "real" / "x").rename(self.root / "real" / "y")
        self.assertFalse(xi.listed_names(self.root, ("link", "x"), memo))

    def test_a_memo_for_another_root_is_not_used(self):
        (self.root / "one" / "a").mkdir(parents=True)
        (self.root / "two").mkdir()
        memo = xi.DirectoryVerdicts(self.root / "one")
        self.assertFalse(xi.has_symlink_component(self.root / "one", ("a",), memo))
        self.assertTrue(xi.listed_names(self.root / "one", ("a",), memo))
        self.assertFalse(xi.listed_names(self.root / "two", ("a",), memo))

    def test_each_prefix_is_listed_and_lstat_ed_once_per_memo(self):
        """Two hundred references under one 400-deep directory: through one
        memo, every directory is listed once and every prefix `lstat`ed
        once. Live, each reference lists all 400 prefixes again."""
        from unittest import mock
        segments = ("d",) * 400
        os.makedirs(os.path.join(self.root, *segments))
        memo = xi.DirectoryVerdicts(self.root)
        counts = {"scandir": 0, "lstat": 0}
        real_scandir, real_lstat = os.scandir, os.lstat

        def scandir(*args, **kwargs):
            counts["scandir"] += 1
            return real_scandir(*args, **kwargs)

        def lstat(*args, **kwargs):
            counts["lstat"] += 1
            return real_lstat(*args, **kwargs)

        with mock.patch("os.scandir", scandir), mock.patch("os.lstat", lstat):
            for _ in range(200):
                self.assertFalse(xi.has_symlink_component(self.root, segments, memo))
                self.assertTrue(xi.listed_names(self.root, segments, memo))
        self.assertEqual(counts, {"scandir": 400, "lstat": 400})


class CollectionBudgetUnitTests(unittest.TestCase):
    """The detail bound's rendered line names its limit in bytes
    (`DETAIL_BOUND_TEMPLATE`), so a detail is charged its name parts' UTF-8
    bytes, the unit the per-concern output bound charges too."""

    def _budget(self, limit):
        budget = xi.CollectionBudget()
        budget.detail_limit = limit
        return budget

    def test_a_non_ascii_name_is_charged_its_utf8_bytes(self):
        # Six code points, twelve UTF-8 bytes: over a ten-byte bound.
        budget = self._budget(10)
        self.assertFalse(budget.detail("p", (1, 1), "\u00e9" * 6))
        self.assertEqual(budget.detail_stop, ("p", (1, 1)))
        self.assertIn("bound of 10 bytes", budget.detail_line()[3])

    def test_control_the_same_count_of_ascii_fits(self):
        budget = self._budget(10)
        self.assertTrue(budget.detail("p", (1, 1), "e" * 6))
        self.assertTrue(budget.detail("p", (2, 2), "ee", "ee"))
        self.assertEqual(budget.detail_spent, 10)
        self.assertIsNone(budget.detail_line())

    def test_the_charge_sums_every_part_in_bytes(self):
        budget = self._budget(12)
        self.assertTrue(budget.detail("p", None, "\u00e9" * 3, "\U0001F600"))
        self.assertEqual(budget.detail_spent, 10)


if __name__ == "__main__":
    unittest.main()
