"""Unit tests for crux.arch.packs.swift_xcode_products (ADR-0130 clause 7).
Every fixture is written fresh as an in-memory OpenStep
document via _pbx() below, never copied from any checkout.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _timing  # noqa: E402

from crux.arch.packs import swift_pbxproj as spx
from crux.arch.packs import swift_xcode as sx
from crux.arch.packs import swift_xcode_products as sxp

OPEN_BRACE = chr(123)
CLOSE_BRACE = chr(125)
OPEN_PAREN = "("
CLOSE_PAREN = ")"


def _serialize(value, indent=0):
    pad = "\t" * indent
    if isinstance(value, dict):
        lines = [OPEN_BRACE]
        for k in sorted(value.keys()):
            v = value[k]
            if isinstance(v, (dict, list)):
                inner = _serialize(v, indent + 1).lstrip()
                lines.append(pad + "\t" + k + " = " + inner + ";")
            else:
                sval = str(v).replace('"', '\\"')
                lines.append(pad + "\t" + k + ' = "' + sval + '";')
        lines.append(pad + CLOSE_BRACE)
        return "\n".join(lines)
    if isinstance(value, list):
        lines = [OPEN_PAREN]
        for item in value:
            if isinstance(item, (dict, list)):
                inner = _serialize(item, indent + 1).lstrip()
                lines.append(pad + "\t" + inner + ",")
            else:
                sval = str(item).replace('"', '\\"')
                lines.append(pad + "\t" + '"' + sval + '",')
        lines.append(pad + CLOSE_PAREN)
        return "\n".join(lines)
    sval = str(value).replace('"', '\\"')
    return '"' + sval + '"'


def _pbx(objects, root_id, object_version="56"):
    doc = {}
    doc["archiveVersion"] = "1"
    doc["objectVersion"] = object_version
    doc["rootObject"] = root_id
    doc["objects"] = objects
    text = "// !$*UTF8*$!\n" + _serialize(doc) + "\n"
    result = spx.read_pbxproj(text.encode("utf-8"))
    assert isinstance(result, spx.PbxprojDocument), result
    return result


def _obj(isa, **kwargs):
    d = dict(isa=isa)
    d.update(kwargs)
    return d


def _mf(directory, products):
    """A minimal stand-in for `swift.ManifestFacts` -- plain data, never
    that class itself (`swift_xcode_products` never imports swift.py)."""
    return SimpleNamespace(directory=directory, path=f"{directory}/Package.swift" if directory else "Package.swift",
                           products=products)


def _parsed(root, bundle_rel, doc):
    pbxproj_rel = f"{bundle_rel}/project.pbxproj"
    parent = () if "/" not in bundle_rel else tuple(bundle_rel.rsplit("/", 1)[0].split("/"))
    descent = sx.descend(doc, parent)
    rows, targets_by_id, _residuals = sx.target_rows(doc, pbxproj_rel, bundle_rel)
    return SimpleNamespace(
        bundle_rel=bundle_rel, pbxproj_rel=pbxproj_rel, doc=doc,
        descent=descent, targets_by_id=targets_by_id, target_rows=rows,
    )


class TypeConfusedProductDependencyTests(unittest.TestCase):
    """ADR-0130 clause 2, last bullet: `resolve_product_dependencies`
    never raises out of a type-confused pbxproj value. Every shape here
    renders one residual
    (a `project-unreadable`/`malformed` catch-all boundary backstops any
    shape not given its own precise class) rather than
    `TypeError`/`AttributeError`/`ValueError`."""

    def test_product_dependency_package_is_a_list(self):
        objects = {}
        objects["PROJ"] = _obj("PBXProject", mainGroup="MAIN", targets=["T1"])
        objects["MAIN"] = _obj("PBXGroup", sourceTree="<group>", children=[])
        objects["PD"] = _obj("XCSwiftPackageProductDependency", productName="Kit", package=["A"])
        objects["T1"] = _obj("PBXNativeTarget", name="T1",
                              productType="com.apple.product-type.application",
                              packageProductDependencies=["PD"])
        doc = _pbx(objects, "PROJ")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = _parsed(root, "App.xcodeproj", doc)
            deps, refs, res = sxp.resolve_product_dependencies(root, [p], [])
        self.assertEqual(deps, [])
        self.assertEqual(len(res), 1)
        self.assertIn(res[0][0], ("unresolved-reference", "project-unreadable"))

    def test_package_product_dependencies_item_is_a_dict(self):
        objects = {}
        objects["PROJ"] = _obj("PBXProject", mainGroup="MAIN", targets=["T1"])
        objects["MAIN"] = _obj("PBXGroup", sourceTree="<group>", children=[])
        objects["T1"] = _obj("PBXNativeTarget", name="T1",
                              productType="com.apple.product-type.application",
                              packageProductDependencies=[{"a": "b"}])
        doc = _pbx(objects, "PROJ")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = _parsed(root, "App.xcodeproj", doc)
            deps, refs, res = sxp.resolve_product_dependencies(root, [p], [])
        self.assertEqual(deps, [])
        self.assertEqual(len(res), 1)
        self.assertIn(res[0][0], ("unresolved-reference", "project-unreadable"))

    def test_local_reference_relative_path_is_a_list(self):
        """ADR-0130 clause 3, as clause 7 applies it: a non-string
        `relativePath` is never resolved, and a package reference sits
        outside any Sources phase, so it renders nothing. An empty list or
        dict is falsy, and it renders nothing too: truthiness never routes a
        non-string value to `path-escape`."""
        for relative_path in (["a"], [], {"a": "b"}, {}):
            with self.subTest(relative_path=relative_path):
                objects = {}
                objects["PROJ"] = _obj("PBXProject", mainGroup="MAIN", targets=["T1"],
                                        packageReferences=["LR"])
                objects["MAIN"] = _obj("PBXGroup", sourceTree="<group>", children=[])
                objects["LR"] = _obj("XCLocalSwiftPackageReference", relativePath=relative_path)
                objects["PD"] = _obj("XCSwiftPackageProductDependency", productName="Kit", package="LR")
                objects["T1"] = _obj("PBXNativeTarget", name="T1",
                                      productType="com.apple.product-type.application",
                                      packageProductDependencies=["PD"])
                doc = _pbx(objects, "PROJ")
                # The fixture reaches the branch: the reader hands the
                # resolver the non-string value as written.
                self.assertEqual(doc.objects["LR"]["relativePath"], relative_path)
                with tempfile.TemporaryDirectory() as d:
                    root = Path(d)
                    p = _parsed(root, "App.xcodeproj", doc)
                    deps, refs, res = sxp.resolve_product_dependencies(root, [p], [])
                self.assertEqual(deps, [])
                self.assertEqual(refs, [])
                self.assertEqual(res, [])

    def test_positive_control_a_string_relative_path_reaches_the_resolver(self):
        """Positive control for the absence test above: the same project
        with a string `relativePath` naming no directory renders
        `missing-input`, so that fixture does reach the local-reference
        resolver."""
        objects = {}
        objects["PROJ"] = _obj("PBXProject", mainGroup="MAIN", targets=["T1"],
                                packageReferences=["LR"])
        objects["MAIN"] = _obj("PBXGroup", sourceTree="<group>", children=[])
        objects["LR"] = _obj("XCLocalSwiftPackageReference", relativePath="a")
        objects["PD"] = _obj("XCSwiftPackageProductDependency", productName="Kit", package="LR")
        objects["T1"] = _obj("PBXNativeTarget", name="T1",
                              productType="com.apple.product-type.application",
                              packageProductDependencies=["PD"])
        doc = _pbx(objects, "PROJ")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = _parsed(root, "App.xcodeproj", doc)
            deps, refs, res = sxp.resolve_product_dependencies(root, [p], [])
        self.assertEqual(deps, [])
        self.assertEqual([r[0] for r in res], ["missing-input"])

    def test_repository_url_and_requirement_kind_are_lists_no_crash(self):
        objects = {}
        objects["PROJ"] = _obj("PBXProject", mainGroup="MAIN", targets=["T1"],
                                packageReferences=["RR"])
        objects["MAIN"] = _obj("PBXGroup", sourceTree="<group>", children=[])
        objects["RR"] = _obj("XCRemoteSwiftPackageReference", repositoryURL=["a"],
                              requirement={"kind": ["x"]})
        objects["T1"] = _obj("PBXNativeTarget", name="T1",
                              productType="com.apple.product-type.application")
        doc = _pbx(objects, "PROJ")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = _parsed(root, "App.xcodeproj", doc)
            deps, refs, res = sxp.resolve_product_dependencies(root, [p], [])
        self.assertEqual(len(refs), 1)
        self.assertEqual(res[0][0], "unsupported-project-form")


class RemoteReferenceTests(unittest.TestCase):
    def test_remote_reference_renders_row_with_stripped_location_and_requirement(self):
        objects = {}
        objects["PRJ"] = _obj("PBXProject", mainGroup="MG", targets=["TGT"],
                               packageReferences=["PKGREF"])
        objects["MG"] = _obj("PBXGroup", sourceTree="<group>", children=[])
        objects["TGT"] = _obj("PBXNativeTarget", name="App",
                               productType="com.apple.product-type.application",
                               buildPhases=[], dependencies=[],
                               packageProductDependencies=["PRODDEP"])
        objects["PRODDEP"] = _obj("XCSwiftPackageProductDependency",
                                   package="PKGREF", productName="Sparkle")
        objects["PKGREF"] = _obj("XCRemoteSwiftPackageReference",
                                  repositoryURL="https://user:pass@github.com/sparkle/Sparkle.git",
                                  requirement={"kind": "upToNextMajorVersion", "minimumVersion": "2.9.5"})
        doc = _pbx(objects, "PRJ")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = _parsed(root, "App.xcodeproj", doc)
            pd, deps, residuals = sxp.resolve_product_dependencies(root, [p], [])
        self.assertEqual(len(pd), 1)
        row = pd[0]
        self.assertEqual(row["from"], "App")
        self.assertEqual(row["kind"], "remote product")
        self.assertEqual(row["product"], "Sparkle")
        self.assertEqual(row["location"], "https://github.com/sparkle/Sparkle.git")
        self.assertEqual(row["requirement"], "upToNextMajorVersion 2.9.5")
        self.assertIsNone(row["edge_to"])
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0]["location"], "https://github.com/sparkle/Sparkle.git")
        self.assertEqual(residuals, [])

    def test_a_late_failure_leaves_only_the_malformed_line(self):
        # The project-level reference row is built before the reachable
        # roots are computed; a failure there must not leave that row behind
        # beside a line saying the same project could not be read.
        objects = {}
        objects["PRJ"] = _obj("PBXProject", mainGroup="MG", targets=[],
                               packageReferences=["PKGREF"])
        objects["MG"] = _obj("PBXGroup", sourceTree="<group>", children=[])
        objects["PKGREF"] = _obj("XCRemoteSwiftPackageReference",
                                  repositoryURL="https://example.invalid/x.git",
                                  requirement={"kind": "exactVersion", "version": "1.0.0"})
        doc = _pbx(objects, "PRJ")
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = _parsed(root, "App.xcodeproj", doc)
            with mock.patch.object(sxp, "_reachable_roots", side_effect=TypeError("seeded")):
                pd, deps, residuals = sxp.resolve_product_dependencies(root, [p], [])
        self.assertEqual((pd, deps), ([], []))
        self.assertEqual([r[0] for r in residuals], ["project-unreadable"])

    def test_unknown_requirement_key_renders_unsupported_project_form(self):
        objects = {}
        objects["PRJ"] = _obj("PBXProject", mainGroup="MG", targets=["TGT"],
                               packageReferences=["PKGREF"])
        objects["MG"] = _obj("PBXGroup", sourceTree="<group>", children=[])
        objects["TGT"] = _obj("PBXNativeTarget", name="App",
                               productType="com.apple.product-type.application",
                               buildPhases=[], dependencies=[],
                               packageProductDependencies=[])
        objects["PKGREF"] = _obj("XCRemoteSwiftPackageReference",
                                  repositoryURL="https://github.com/x/Y.git",
                                  requirement={"kind": "exactVersion", "version": "1.0.0",
                                               "surprise": "1"})
        doc = _pbx(objects, "PRJ")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = _parsed(root, "App.xcodeproj", doc)
            pd, deps, residuals = sxp.resolve_product_dependencies(root, [p], [])
        self.assertEqual(len(deps), 1)
        self.assertTrue(any(r[0] == "unsupported-project-form" for r in residuals), residuals)


class RepositoryLocationShapeTests(unittest.TestCase):
    """ADR-0129:153 and ADR-0130 clause 7: a present
    `repositoryURL` that is not a string names no location. The two read
    sites -- the project's `packageReferences` row and a target's remote
    product -- share one read of the reference, which renders one
    `unsupported-project-form` with a fixed detail, and each row carries
    location `""`, which renders `—`. No Python repr and no
    part of the value reaches a row. The value's type decides, never its
    truthiness. An absent or empty-string value is unchanged."""

    ABSENT = object()
    URL = "https://u:tok@h/x.git"

    def _resolve(self, repository_url):
        objects = {}
        objects["PRJ"] = _obj("PBXProject", mainGroup="MG", targets=["TGT"],
                              packageReferences=["PKGREF"])
        objects["MG"] = _obj("PBXGroup", sourceTree="<group>", children=[])
        objects["TGT"] = _obj("PBXNativeTarget", name="App",
                              productType="com.apple.product-type.application",
                              buildPhases=[], dependencies=[],
                              packageProductDependencies=["PRODDEP"])
        objects["PRODDEP"] = _obj("XCSwiftPackageProductDependency",
                                  package="PKGREF", productName="Kit")
        ref = {"requirement": {"kind": "exactVersion", "version": "1.0.0"}}
        if repository_url is not self.ABSENT:
            ref["repositoryURL"] = repository_url
        objects["PKGREF"] = _obj("XCRemoteSwiftPackageReference", **ref)
        doc = _pbx(objects, "PRJ")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = _parsed(root, "App.xcodeproj", doc)
            pd, deps, res = sxp.resolve_product_dependencies(root, [p], [])
        return doc, pd, deps, res

    def test_a_non_string_location_renders_one_line_and_no_repr(self):
        """Both read sites -- the project's `packageReferences` row and the
        target's remote product -- read the location as `""`. They share one
        read of the reference, and its one line is kept once; the renderer
        merged the two copies before, so every distinct line still renders
        (ADR-0129 clause 7)."""
        for value in ([self.URL], {"a": self.URL}, [], {}):
            with self.subTest(value=value):
                doc, pd, deps, res = self._resolve(value)
                # The fixture reaches the reader: the value arrives as written.
                self.assertEqual(doc.objects["PKGREF"]["repositoryURL"], value)
                self.assertEqual([(r["kind"], r["location"], r["requirement"]) for r in pd],
                                 [("remote product", "", "exactVersion 1.0.0")])
                self.assertEqual([(d["location"], d["requirement"]) for d in deps],
                                 [("", "exactVersion 1.0.0")])
                span = doc.object_spans["PKGREF"]
                self.assertEqual(res, [("unsupported-project-form", "App.xcodeproj/project.pbxproj",
                                        span, "package repository location is not a string")])
                self.assertEqual(sxp.NON_STRING_REPOSITORY_LOCATION_DETAIL,
                                 "package repository location is not a string")
                cells = [str(v) for row in pd + deps for v in row.values()] + [r[3] for r in res]
                for needle in ("tok", "h/x.git", "['", "{'", "https"):
                    self.assertFalse([c for c in cells if needle in c], needle)

    def test_control_an_absent_or_empty_location_is_unchanged(self):
        for value in (self.ABSENT, ""):
            with self.subTest(value=value):
                _doc, pd, deps, res = self._resolve(value)
                self.assertEqual([r["location"] for r in pd], [""])
                self.assertEqual([d["location"] for d in deps], [""])
                self.assertEqual(res, [])

    def test_control_a_string_location_is_stripped_and_renders_no_line(self):
        _doc, pd, deps, res = self._resolve(self.URL)
        self.assertEqual([r["location"] for r in pd], ["https://h/x.git"])
        self.assertEqual([d["location"] for d in deps], ["https://h/x.git"])
        self.assertEqual(res, [])


class LocalReferenceTests(unittest.TestCase):
    def _project(self, root, relative_path):
        objects = {}
        objects["PRJ"] = _obj("PBXProject", mainGroup="MG", targets=["TGT"], packageReferences=[])
        objects["MG"] = _obj("PBXGroup", sourceTree="<group>", children=[])
        objects["TGT"] = _obj("PBXNativeTarget", name="App",
                               productType="com.apple.product-type.application",
                               buildPhases=[], dependencies=[],
                               packageProductDependencies=["PRODDEP"])
        objects["PRODDEP"] = _obj("XCSwiftPackageProductDependency",
                                   package="LOCALREF", productName="Core")
        objects["LOCALREF"] = _obj("XCLocalSwiftPackageReference", relativePath=relative_path)
        return _pbx(objects, "PRJ")

    def test_escaping_path_renders_path_escape(self):
        doc = self._project(None, "../../outside")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = _parsed(root, "App.xcodeproj", doc)
            pd, deps, residuals = sxp.resolve_product_dependencies(root, [p], [])
        self.assertEqual(pd, [])
        self.assertTrue(any(r[0] == "path-escape" for r in residuals), residuals)

    def _resolvable_tree(self, root):
        """A tree where `Vendor/Core` holds a manifest declaring `Core`, so
        a resolved reference to it draws an edge."""
        (root / "Vendor" / "Core").mkdir(parents=True)
        (root / "Vendor" / "Core" / "Package.swift").write_text("// swift-tools-version:5.9\n")
        return [_mf("Vendor/Core", [{"name": "Core", "kind": "library"}])]

    def test_never_resolved_relative_path_renders_nothing(self):
        """ADR-0130 clause 3, which clause 7 applies to `relativePath`: a
        `$(VAR)` segment is never resolved, and it renders
        `unresolved-reference` only in a Sources phase. A package reference
        sits in no Sources phase, so it renders nothing: no residual, no
        dependency row and no edge. A non-string value is classified the same
        way, whether or not it is truthy."""
        for relative_path in ("$(SRCROOT)/Vendor/Core", "$(PROJECT_DIR)/Vendor/Core",
                              "Vendor/$(CORE)", ["Vendor", "Core"], {"a": "b"}, [], {},
                              "$(X)/../Vendor/Core", "${X}/../Vendor/Core", "$X/../Vendor/Core"):
            with self.subTest(relative_path=relative_path), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                manifests = self._resolvable_tree(root)
                doc = self._project(None, relative_path)
                self.assertEqual(doc.objects["LOCALREF"]["relativePath"], relative_path)
                p = _parsed(root, "App.xcodeproj", doc)
                pd, deps, residuals = sxp.resolve_product_dependencies(root, [p], manifests)
                self.assertEqual(pd, [])
                self.assertEqual(deps, [])
                self.assertEqual(residuals, [])

    def test_control_the_same_tree_with_a_plain_relative_path_draws_the_edge(self):
        """Positive control for the absence test above: the same project and
        tree, with a plain `relativePath`, reach `_resolve_local_reference`
        and draw the edge. So the absence above is the never-resolved rule,
        not a fixture that fails to reach the resolver."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifests = self._resolvable_tree(root)
            p = _parsed(root, "App.xcodeproj", self._project(None, "Vendor/Core"))
            pd, deps, residuals = sxp.resolve_product_dependencies(root, [p], manifests)
        self.assertEqual(residuals, [])
        self.assertEqual([(row["kind"], row["edge_to"]) for row in pd],
                         [("local product", ("Vendor/Core/Package.swift", "Core"))])

    def test_control_a_real_escape_stays_path_escape(self):
        for relative_path in ("../../outside", "/abs/Core"):
            with self.subTest(relative_path=relative_path), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                p = _parsed(root, "App.xcodeproj", self._project(None, relative_path))
                pd, deps, residuals = sxp.resolve_product_dependencies(root, [p], [])
                self.assertEqual(pd, [])
                self.assertEqual([r[0] for r in residuals], ["path-escape"])

    @unittest.skipIf(not hasattr(os, "symlink"), "platform has no symlink support")
    def test_symlinked_package_swift_renders_missing_input(self):
        # ADR-0130 clause 3: the directory is
        # contained (real, non-symlinked), but the `Package.swift` file
        # ITSELF is reached only through a symlink -- refused the same way
        # every other member-verification guard in this module refuses a
        # symlinked entry.
        doc = self._project(None, "Vendor/Core")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Vendor" / "Core").mkdir(parents=True)
            real = root / "_real_Package.swift"
            real.write_text("// swift-tools-version:5.9\n")
            try:
                os.symlink(real, root / "Vendor" / "Core" / "Package.swift")
            except OSError:
                self.skipTest("host refuses symlink creation")
            p = _parsed(root, "App.xcodeproj", doc)
            pd, deps, residuals = sxp.resolve_product_dependencies(root, [p], [])
        self.assertEqual(pd, [])
        self.assertTrue(any(r[0] == "missing-input" for r in residuals), residuals)

    def _resolve_recording_scandir(self, root, doc, manifests):
        from unittest import mock
        listed = []
        real_scandir = os.scandir

        def recording(path="."):
            listed.append(str(path))
            return real_scandir(path)

        p = _parsed(root, "App.xcodeproj", doc)
        with mock.patch.object(os, "scandir", recording):
            result = sxp.resolve_product_dependencies(root, [p], manifests)
        return result, listed

    @unittest.skipIf(not hasattr(os, "symlink"), "platform has no symlink support")
    def test_contained_symlinked_package_directory_is_not_descended(self):
        """ADR-0130 clause 3: no directory symlink is descended. An
        in-checkout `Alias -> Vendor/Core` is never listed, even when a
        manifest sits behind it, and renders one `unresolved-reference`."""
        for link, target in (("Alias", "Vendor/Core"), ("PodAlias", "Pods/Core")):
            with self.subTest(link=link), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / target).mkdir(parents=True)
                (root / target / "Package.swift").write_text("// swift-tools-version:5.9\n")
                try:
                    os.symlink(root / target, root / link, target_is_directory=True)
                except OSError:
                    self.skipTest("host refuses symlink creation")
                manifests = [_mf(link, [{"name": "Core", "kind": "library"}])]
                (pd, deps, residuals), listed = self._resolve_recording_scandir(
                    root, self._project(None, link), manifests)
                self.assertEqual(pd, [])
                self.assertEqual([(r[0], r[3]) for r in residuals],
                                 [("unresolved-reference", sxp.SYMLINKED_LOCAL_REFERENCE_DETAIL)])
                self.assertEqual([x for x in listed if link in x or target.split("/")[0] in x], [])

    def test_positive_control_the_same_tree_without_a_symlink_resolves(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Vendor" / "Core").mkdir(parents=True)
            (root / "Vendor" / "Core" / "Package.swift").write_text("// swift-tools-version:5.9\n")
            manifests = [_mf("Vendor/Core", [{"name": "Core", "kind": "library"}])]
            (pd, deps, residuals), _listed = self._resolve_recording_scandir(
                root, self._project(None, "Vendor/Core"), manifests)
        self.assertEqual(residuals, [])
        self.assertEqual([row["edge_to"] for row in pd], [("Vendor/Core/Package.swift", "Core")])

    def test_case_mismatched_directory_renders_missing_input_on_every_host(self):
        # ADR-0130 clause 3, and byte-stable output across hosts:
        # `vendor/core` names nothing byte for
        # byte when the directory is `Vendor/Core`. A case-folding host would
        # otherwise find `Package.swift` through the path as written and
        # render a different class than a case-sensitive host does.
        doc = self._project(None, "vendor/core")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Vendor" / "Core").mkdir(parents=True)
            (root / "Vendor" / "Core" / "Package.swift").write_text("// swift-tools-version:5.9\n")
            p = _parsed(root, "App.xcodeproj", doc)
            pd, deps, residuals = sxp.resolve_product_dependencies(root, [p], [])
        self.assertEqual(pd, [])
        self.assertEqual([r[0] for r in residuals], ["missing-input"])

    def test_missing_package_swift_renders_missing_input(self):
        doc = self._project(None, "Vendor/Core")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Vendor" / "Core").mkdir(parents=True)
            p = _parsed(root, "App.xcodeproj", doc)
            pd, deps, residuals = sxp.resolve_product_dependencies(root, [p], [])
        self.assertEqual(pd, [])
        self.assertTrue(any(r[0] == "missing-input" for r in residuals), residuals)

    def test_undeclared_product_renders_unresolved_reference(self):
        doc = self._project(None, "Vendor/Core")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Vendor" / "Core").mkdir(parents=True)
            (root / "Vendor" / "Core" / "Package.swift").write_text("// swift-tools-version:5.9\n")
            p = _parsed(root, "App.xcodeproj", doc)
            manifests = [_mf("Vendor/Core", [{"name": "Other", "kind": "library"}])]
            pd, deps, residuals = sxp.resolve_product_dependencies(root, [p], manifests)
        self.assertEqual(pd, [])
        self.assertTrue(any(r[0] == "unresolved-reference" for r in residuals), residuals)

    def test_local_plugin_product_renders_dependency_row(self):
        doc = self._project(None, "Vendor/Core")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Vendor" / "Core").mkdir(parents=True)
            (root / "Vendor" / "Core" / "Package.swift").write_text("// swift-tools-version:5.9\n")
            p = _parsed(root, "App.xcodeproj", doc)
            manifests = [_mf("Vendor/Core", [{"name": "Core", "kind": "plugin"}])]
            pd, deps, residuals = sxp.resolve_product_dependencies(root, [p], manifests)
        self.assertEqual(len(pd), 1)
        self.assertEqual(pd[0]["kind"], "plugin product")
        self.assertIsNone(pd[0]["edge_to"])
        self.assertEqual(residuals, [])


class UnlinkedProductTests(unittest.TestCase):
    def _project_with_synced_root(self, group_name):
        objects = {}
        objects["PRJ"] = _obj("PBXProject", mainGroup="MG", targets=["TGT"], packageReferences=[])
        objects["MG"] = _obj("PBXGroup", sourceTree="<group>", children=["SYNCED"])
        objects["SYNCED"] = _obj("PBXFileSystemSynchronizedRootGroup",
                                  sourceTree="<group>", path=group_name, explicitFolders=[])
        objects["TGT"] = _obj("PBXNativeTarget", name="App",
                               productType="com.apple.product-type.application",
                               buildPhases=[], dependencies=[],
                               packageProductDependencies=["PRODDEP"])
        objects["PRODDEP"] = _obj("XCSwiftPackageProductDependency", productName="Core")
        return _pbx(objects, "PRJ")

    def test_unique_match_draws_edge(self):
        doc = self._project_with_synced_root("Modules")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = _parsed(root, "App.xcodeproj", doc)
            manifests = [_mf("Modules/Core", [{"name": "Core", "kind": "library"}])]
            pd, deps, residuals = sxp.resolve_product_dependencies(root, [p], manifests)
        self.assertEqual(len(pd), 1)
        self.assertEqual(pd[0]["edge_to"], ("Modules/Core/Package.swift", "Core"))
        self.assertEqual(residuals, [])

    def test_ambiguous_match_renders_unresolved_reference(self):
        doc = self._project_with_synced_root("Modules")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = _parsed(root, "App.xcodeproj", doc)
            manifests = [
                _mf("Modules/CoreA", [{"name": "Core", "kind": "library"}]),
                _mf("Modules/CoreB", [{"name": "Core", "kind": "library"}]),
            ]
            pd, deps, residuals = sxp.resolve_product_dependencies(root, [p], manifests)
        self.assertEqual(pd, [])
        self.assertTrue(any(r[0] == "unresolved-reference" for r in residuals), residuals)

    def test_unmatched_renders_unresolved_reference(self):
        doc = self._project_with_synced_root("Modules")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = _parsed(root, "App.xcodeproj", doc)
            manifests = [_mf("Modules/Other", [{"name": "NotCore", "kind": "library"}])]
            pd, deps, residuals = sxp.resolve_product_dependencies(root, [p], manifests)
        self.assertEqual(pd, [])
        self.assertTrue(any(r[0] == "unresolved-reference" for r in residuals), residuals)

    def test_unrelated_container_outside_reach_is_not_a_candidate(self):
        # A manifest sitting outside any group/synced root the project
        # reaches must never be guessed as a candidate (ADR-0130's
        # rejected "every literal product in the checkout" alternative).
        doc = self._project_with_synced_root("Modules")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = _parsed(root, "App.xcodeproj", doc)
            manifests = [_mf("Elsewhere/Core", [{"name": "Core", "kind": "library"}])]
            pd, deps, residuals = sxp.resolve_product_dependencies(root, [p], manifests)
        self.assertEqual(pd, [])
        self.assertTrue(any(r[0] == "unresolved-reference" for r in residuals), residuals)
        # And the residual names zero candidates (nothing reachable matched).
        detail = next(r[3] for r in residuals if r[0] == "unresolved-reference")
        self.assertIn("0 candidate containers", detail)


class MainGroupReachabilityTests(unittest.TestCase):
    def test_main_group_with_named_path_is_a_reachable_root(self):
        # ADR-0130 clause 7: when the main group's own directory is
        # NOT the checkout root (it carries a `path`, e.g. "Apps"), a
        # package under that directory must still be a reachable
        # candidate -- excluding the main group unconditionally makes it
        # unreachable and the unlinked product never resolves.
        objects = {}
        objects["PRJ"] = _obj("PBXProject", mainGroup="MG", targets=["TGT"], packageReferences=[])
        objects["MG"] = _obj("PBXGroup", sourceTree="<group>", path="Apps", children=[])
        objects["TGT"] = _obj("PBXNativeTarget", name="App",
                               productType="com.apple.product-type.application",
                               buildPhases=[], dependencies=[],
                               packageProductDependencies=["PRODDEP"])
        objects["PRODDEP"] = _obj("XCSwiftPackageProductDependency", productName="Core")
        doc = _pbx(objects, "PRJ")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = _parsed(root, "App.xcodeproj", doc)
            manifests = [_mf("Apps/Core", [{"name": "Core", "kind": "library"}])]
            pd, deps, residuals = sxp.resolve_product_dependencies(root, [p], manifests)
        self.assertEqual(len(pd), 1, residuals)
        self.assertEqual(pd[0]["edge_to"], ("Apps/Core/Package.swift", "Core"))
        self.assertEqual(residuals, [])

    def test_main_group_at_checkout_root_still_excluded(self):
        # Control: when the main group's directory IS the checkout root
        # (no `path`, or "."), it stays excluded -- the rule narrows the
        # exclusion, it does not remove it.
        objects = {}
        objects["PRJ"] = _obj("PBXProject", mainGroup="MG", targets=["TGT"], packageReferences=[])
        objects["MG"] = _obj("PBXGroup", sourceTree="<group>", children=[])
        objects["TGT"] = _obj("PBXNativeTarget", name="App",
                               productType="com.apple.product-type.application",
                               buildPhases=[], dependencies=[],
                               packageProductDependencies=["PRODDEP"])
        objects["PRODDEP"] = _obj("XCSwiftPackageProductDependency", productName="Core")
        doc = _pbx(objects, "PRJ")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = _parsed(root, "App.xcodeproj", doc)
            manifests = [_mf("Core", [{"name": "Core", "kind": "library"}])]
            pd, deps, residuals = sxp.resolve_product_dependencies(root, [p], manifests)
        self.assertEqual(pd, [])
        self.assertTrue(any(r[0] == "unresolved-reference" for r in residuals), residuals)


    def test_pathless_main_group_is_excluded_wherever_the_project_sits(self):
        # The exclusion follows what the main group names, not where the
        # project sits: a pathless main group at `ios/App.xcodeproj` adds no
        # directory of its own, so an unrelated package under `ios/` is not a
        # candidate, exactly as one under the checkout root is not for a
        # project at the root.
        objects = {}
        objects["PRJ"] = _obj("PBXProject", mainGroup="MG", targets=["TGT"], packageReferences=[])
        objects["MG"] = _obj("PBXGroup", sourceTree="<group>", children=[])
        objects["TGT"] = _obj("PBXNativeTarget", name="App",
                               productType="com.apple.product-type.application",
                               buildPhases=[], dependencies=[],
                               packageProductDependencies=["PRODDEP"])
        objects["PRODDEP"] = _obj("XCSwiftPackageProductDependency", productName="Core")
        doc = _pbx(objects, "PRJ")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = _parsed(root, "ios/App.xcodeproj", doc)
            manifests = [_mf("ios/Vendor/Core", [{"name": "Core", "kind": "library"}])]
            pd, deps, residuals = sxp.resolve_product_dependencies(root, [p], manifests)
        self.assertEqual(pd, [])
        self.assertEqual([r[0] for r in residuals], ["unresolved-reference"])

class RequirementValueShapeTests(unittest.TestCase):
    """ADR-0130 clause 7, the value half of the non-string `kind` guard: a
    requirement value that is not a string renders `—` and one closed
    `unsupported-project-form` detail. A Python repr of a list or a dict is
    content, neither escaped nor a version, so it never reaches a cell."""

    def _render(self, req):
        residuals = []
        return sxp._requirement_text(req, "App.xcodeproj/project.pbxproj", None, residuals), residuals

    def test_non_string_version_renders_dash_and_one_residual(self):
        text, residuals = self._render({"kind": "exactVersion", "version": ["1.0.0"]})
        self.assertEqual(text, "exactVersion —")
        self.assertEqual([(r[0], r[3]) for r in residuals],
                         [("unsupported-project-form", "package requirement value is not a string")])

    def test_non_string_range_bound_renders_dash_and_one_residual(self):
        text, residuals = self._render(
            {"kind": "range", "minimumVersion": {"a": 1}, "maximumVersion": "2.0.0"})
        self.assertEqual(text, "range —..<2.0.0")
        self.assertEqual([r[0] for r in residuals], ["unsupported-project-form"])
        self.assertNotIn("{", text)

    def test_string_values_control_render_unchanged(self):
        self.assertEqual(self._render({"kind": "exactVersion", "version": "1.0.0"}),
                         ("exactVersion 1.0.0", []))
        self.assertEqual(self._render(
            {"kind": "range", "minimumVersion": "1.0.0", "maximumVersion": "2.0.0"}),
            ("range 1.0.0..<2.0.0", []))


if __name__ == "__main__":
    unittest.main()


class RequirementKindDetailTests(unittest.TestCase):
    def test_non_string_kind_renders_a_closed_detail(self):
        # ADR-0130 clause 14: a residual line carries named fields read from
        # the checkout and escaped; a list's Python repr is neither.
        from crux.arch.packs import swift_xcode_products as sxp
        residuals = []
        self.assertEqual(sxp._requirement_text({"kind": ["x"]}, "p", (1, 1), residuals), "—")
        self.assertEqual([r[3] for r in residuals], ["package requirement kind is not a string"])


class LocalReferencePathFormTests(unittest.TestCase):
    """ADR-0130 clauses 3 and 7 over `relativePath` and the project directory.
    An absent `relativePath` names nothing and renders `unresolved-reference`.
    An empty `relativePath` names the project directory, exactly as `.` does.
    Neither renders `path-escape`. Under a project whose `projectDirPath`
    carries `$(`, a local reference renders nothing."""

    ABSENT = object()

    def _project(self, relative_path, project_dir_path=None):
        objects = {}
        proj = dict(mainGroup="MG", targets=["TGT"], packageReferences=[])
        if project_dir_path is not None:
            proj["projectDirPath"] = project_dir_path
        objects["PRJ"] = _obj("PBXProject", **proj)
        objects["MG"] = _obj("PBXGroup", sourceTree="<group>", children=[])
        objects["TGT"] = _obj("PBXNativeTarget", name="App",
                              productType="com.apple.product-type.application",
                              buildPhases=[], dependencies=[],
                              packageProductDependencies=["PRODDEP"])
        objects["PRODDEP"] = _obj("XCSwiftPackageProductDependency",
                                  package="LOCALREF", productName="Core")
        ref = {} if relative_path is self.ABSENT else {"relativePath": relative_path}
        objects["LOCALREF"] = _obj("XCLocalSwiftPackageReference", **ref)
        return _pbx(objects, "PRJ")

    def _resolve(self, root, doc, manifests):
        p = _parsed(root, "App.xcodeproj", doc)
        pd, _deps, res = sxp.resolve_product_dependencies(root, [p], manifests)
        return [(row["kind"], row["edge_to"]) for row in pd], [(r[0], r[3]) for r in res]

    def test_an_absent_relative_path_renders_unresolved_reference(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            doc = self._project(self.ABSENT)
            self.assertNotIn("relativePath", doc.objects["LOCALREF"])
            rows, res = self._resolve(root, doc, [])
        self.assertEqual(rows, [])
        self.assertEqual(res, [("unresolved-reference",
                                "local package reference for product 'Core' carries no relativePath")])

    def test_an_empty_relative_path_behaves_as_dot(self):
        for relative_path in ("", "."):
            with self.subTest(relative_path=relative_path), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                rows, res = self._resolve(root, self._project(relative_path), [])
                self.assertEqual(rows, [])
                self.assertEqual(res, [("missing-input",
                                        "local package reference '.' holds no Package.swift")])
                (root / "Package.swift").write_text("// swift-tools-version:5.9\n")
                manifests = [SimpleNamespace(directory=".", path="Package.swift",
                                             products=[{"name": "Core", "kind": "library"}])]
                rows, res = self._resolve(root, self._project(relative_path), manifests)
                self.assertEqual(res, [])
                self.assertEqual(rows, [("local product", ("Package.swift", "Core"))])

    def test_under_a_variable_project_dir_a_local_reference_renders_nothing(self):
        for pdp in ("$(SRCROOT)/..", "${SRCROOT}/..", "$SRCROOT/.."):
            for relative_path in ("Vendor/Core", "", ".", self.ABSENT, "../x"):
                with self.subTest(pdp=pdp, relative_path=relative_path), \
                        tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    (root / "Vendor" / "Core").mkdir(parents=True)
                    (root / "Vendor" / "Core" / "Package.swift").write_text(
                        "// swift-tools-version:5.9\n")
                    manifests = [_mf("Vendor/Core", [{"name": "Core", "kind": "library"}])]
                    rows, res = self._resolve(root, self._project(relative_path, pdp), manifests)
                    self.assertEqual(rows, [])
                    self.assertEqual(res, [])

    def test_control_the_same_reference_under_a_plain_project_dir_draws_the_edge(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Vendor" / "Core").mkdir(parents=True)
            (root / "Vendor" / "Core" / "Package.swift").write_text("// swift-tools-version:5.9\n")
            manifests = [_mf("Vendor/Core", [{"name": "Core", "kind": "library"}])]
            rows, res = self._resolve(root, self._project("Vendor/Core", "."), manifests)
        self.assertEqual(res, [])
        self.assertEqual(rows, [("local product", ("Vendor/Core/Package.swift", "Core"))])

    def test_under_a_variable_project_dir_no_local_reference_joins_the_candidate_set(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Vendor" / "Core").mkdir(parents=True)
            for pdp in ("$(SRCROOT)/..", "${SRCROOT}/..", "$SRCROOT/.."):
                with self.subTest(pdp=pdp):
                    doc = self._project("Vendor/Core", pdp)
                    project_dir = sxp._resolve_project_dir(doc.objects["PRJ"], ())
                    self.assertEqual(sxp._project_local_ref_dirs(root, doc, project_dir), set())
            for relative_path in ("$(X)/../Vendor/Core", "${X}/../Vendor/Core", "$X/../Vendor/Core"):
                with self.subTest(relative_path=relative_path):
                    doc = self._project(relative_path, ".")
                    self.assertEqual(sxp._project_local_ref_dirs(root, doc, ()), set())
            doc = self._project("", ".")
            self.assertEqual(sxp._project_local_ref_dirs(root, doc, ()), {"."})
            doc = self._project("Sub/../Vendor/Core", ".")
            self.assertEqual(sxp._project_local_ref_dirs(root, doc, ()), {"Vendor/Core"})


class LocalReferenceCandidateDirsTests(unittest.TestCase):
    """`_project_local_ref_dirs` adds each contained local-reference directory
    to the unlinked-product candidate set (ADR-0130 clause 7). A directory in
    or under a pruned name never joins the set (ADR-0129 clause 8)."""

    def _doc(self, relative_path):
        objects = {}
        objects["PRJ"] = _obj("PBXProject", mainGroup="MG", targets=[], packageReferences=["LR"])
        objects["MG"] = _obj("PBXGroup", sourceTree="<group>", children=[])
        objects["LR"] = _obj("XCLocalSwiftPackageReference", relativePath=relative_path)
        return _pbx(objects, "PRJ")

    def test_a_pruned_directory_never_joins_the_candidate_set(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Pods" / "Core").mkdir(parents=True)
            self.assertEqual(sxp._project_local_ref_dirs(root, self._doc("Pods/Core"), ()), set())

    def test_positive_control_with_the_prune_off_it_joins(self):
        from unittest import mock
        from crux.arch.packs import swift_prune
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Pods" / "Core").mkdir(parents=True)
            with mock.patch.object(swift_prune, "pruned_path", lambda segs, container=False: False):
                dirs = sxp._project_local_ref_dirs(root, self._doc("Pods/Core"), ())
        self.assertEqual(dirs, {"Pods/Core"})


class StripLocationParityTests(unittest.TestCase):
    """`swift_xcode_products` carries its own copy of `swift._strip_location`
    so it never imports `swift.py`. Both copies strip every credential
    (`rule:swift-dependency-location-strips-credentials`); this table holds
    them to one output for every location shape they handle."""

    CASES = (
        ("https://github.com/org/repo.git", "https://github.com/org/repo.git"),
        ("https://user:pass@github.com/org/repo.git", "https://github.com/org/repo.git"),
        ("https://user@github.com/org/repo.git", "https://github.com/org/repo.git"),
        ("https://to/k:e@n@host/repo", "https://host/repo"),
        # An `@` after the first `?` or `#` leaves it undecidable whether the
        # text before it is userinfo holding `?`/`#` or the text after it is
        # query or fragment content. Neither side renders.
        ("https://u:p/?#x@host/repo", "https://"),
        # `://` counts as a scheme separator only after an RFC 3986 scheme.
        ("deploy:SECRET@git.example.com:org/a.git?ref=https://x", "git.example.com:org/a.git"),
        ("user:pa://ss@host:r", "host:r"),
        ("git+ssh://u:p@host/r", "git+ssh://host/r"),
        ("1http://u:p@host/r", "host/r"),
        # An `@` inside a query or fragment never promotes its tail to the host.
        ("https://git.example.com/org/b.git?q=1@SECRET", "https://"),
        ("https://git.example.com/org/b.git#f@SECRET", "https://"),
        ("git@github.com:org/repo.git", "github.com:org/repo.git"),
        ("ssh://git@github.com/org/repo.git", "ssh://github.com/org/repo.git"),
        ("https://host/repo.git?token=abc", "https://host/repo.git"),
        ("https://host/repo.git#frag", "https://host/repo.git"),
        ("https://host/repo.git?a=1#frag", "https://host/repo.git"),
        ("https://host/repo.git#f?q", "https://host/repo.git"),
        ("https://host/repo/", "https://host/repo/"),
        ("host/path", "host/path"),
        ("", ""),
        ("@", ""),
        ("://", "://"),
        ("?#", ""),
        ("file:///Users/me/repo", "file:///Users/me/repo"),
        ("https://host/p@th/repo", "https://th/repo"),
        ("user:pass@host:22/repo?x#y", "host:22/repo"),
    )

    def _copies(self):
        from crux.arch.packs import swift
        return swift._strip_location, sxp._strip_location

    def test_both_copies_agree_on_every_case(self):
        in_swift, in_products = self._copies()
        for loc, want in self.CASES:
            with self.subTest(loc=loc):
                self.assertEqual(in_swift(loc), want)
                self.assertEqual(in_products(loc), want)

    #: Locations whose credential, query or fragment carries `CANARY`. The
    #: canary must never survive either copy.
    CANARY_CASES = (
        "deploy:CANARY@git.example.com:org/a.git?ref=https://x",
        "CANARY:pa://ss@host:r",
        "user:pa://CANARY@host:r",
        "https://git.example.com/org/b.git?q=1@CANARY",
        "https://git.example.com/org/b.git#frag@CANARY",
        "git.example.com/org/b.git?q=1@CANARY",
        "https://h/p?r=https://x@CANARY",
        "CANARY:tok@host/x?y=https://z",
        "https://u:CANARY/?#x@host/repo",
        "https://CANARY:x@host/y?z#w",
        "ht tp://CANARY:x@host/y",
    )

    def test_no_canary_survives_either_copy(self):
        in_swift, in_products = self._copies()
        for loc in self.CANARY_CASES:
            with self.subTest(loc=loc):
                self.assertNotIn("CANARY", in_swift(loc))
                self.assertNotIn("CANARY", in_products(loc))

    def test_both_copies_agree_on_non_string_input(self):
        in_swift, in_products = self._copies()
        for loc in (None, 42, ["a@b"]):
            with self.subTest(loc=loc):
                self.assertEqual(in_swift(loc), in_products(loc))


class ResidualNamesAreCellEscapedTests(unittest.TestCase):
    """ADR-0130 clause 14: a name read from the checkout renders in its
    `_cell` form inside a residual detail, never raw."""

    HOSTILE = "x`|y"

    def test_requirement_kind_is_cell_escaped(self):
        from crux.arch.core import _cell
        residuals = []
        sxp._requirement_text({"kind": self.HOSTILE}, "p", (1, 1), residuals)
        self.assertEqual([r[3] for r in residuals],
                         ["package requirement kind '%s' is outside the tested set" % _cell(self.HOSTILE)])

    def test_dangling_product_dependency_target_name_is_cell_escaped(self):
        from crux.arch.core import _cell
        from crux.arch.packs import swift
        objects = {}
        objects["PROJ"] = _obj("PBXProject", mainGroup="MAIN", targets=["T1"])
        objects["MAIN"] = _obj("PBXGroup", sourceTree="<group>", children=[])
        objects["T1"] = _obj("PBXNativeTarget", name=self.HOSTILE,
                              productType="com.apple.product-type.application",
                              packageProductDependencies=["GONE"])
        doc = _pbx(objects, "PROJ")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = _parsed(root, "App.xcodeproj", doc)
            _deps, _refs, res = sxp.resolve_product_dependencies(root, [p], [])
        want = "target '%s' names a dangling package product dependency" % _cell(self.HOSTILE)
        self.assertEqual([r[3] for r in res], [want])
        klass, path, _span, detail = res[0]
        bullet = swift._residual_line(klass, path, None, detail)
        self.assertTrue(bullet.endswith(" — " + want))
        self.assertNotIn("`|", bullet)


class ProductDependencyLineLinearTimeTests(unittest.TestCase):
    """ADR-0130 clause 2: each dangling `packageProductDependencies` item
    renders one `unresolved-reference` at its own list-item line. This
    module's `_list_item_line` is the twin of `swift_xcode._list_item_line`;
    both find the line in time linear in the list. The rescanning lookup
    made a 30,000-item list take about 10 s."""

    P = 30_000

    def _doc(self, dep_ids):
        objects = {}
        objects["PROJ"] = _obj("PBXProject", mainGroup="MAIN", targets=["T1"])
        objects["MAIN"] = _obj("PBXGroup", sourceTree="<group>", children=[])
        objects["T1"] = _obj("PBXNativeTarget", name="T1",
                              productType="com.apple.product-type.application",
                              packageProductDependencies=dep_ids)
        return _pbx(objects, "PROJ")

    def test_a_long_dangling_list_renders_each_line_in_linear_time(self):

        def seconds(size):
            doc = self._doc(["D%05d" % i for i in range(size)])
            with tempfile.TemporaryDirectory() as d:
                p = _parsed(Path(d), "App.xcodeproj", doc)
                started = _timing.clock()
                deps, _refs, res = sxp.resolve_product_dependencies(Path(d), [p], [])
                elapsed = _timing.clock() - started
            self.assertEqual(deps, [])
            self.assertEqual([r[2] for r in res], doc.list_item_lines[("T1", "packageProductDependencies")])
            return elapsed

        _timing.assert_grows_linearly(self, seconds, self.P, "dangling product dependencies")

    def test_a_repeated_id_names_its_first_line_every_time(self):
        doc = self._doc(["X", "Y", "X", {"k": "v"}, {"k": "v"}])
        lines = doc.list_item_lines[("T1", "packageProductDependencies")]
        with tempfile.TemporaryDirectory() as d:
            p = _parsed(Path(d), "App.xcodeproj", doc)
            _deps, _refs, res = sxp.resolve_product_dependencies(Path(d), [p], [])
        # A repeated id's line is the same line, kept once (ADR-0129 clause 7);
        # the second occurrence's own line never appears.
        self.assertEqual(len(set(lines)), 5, lines)
        self.assertEqual([r[2] for r in res], [lines[0], lines[1], lines[3]])


# ── name-bearing details are charged first (`MAX_DETAIL_BYTES`) ────────────

from crux.arch.packs import swift_xcinputs as _sxi  # noqa: E402


class NameBearingDetailChargeTests(unittest.TestCase):
    """`swift_xcinputs.MAX_DETAIL_BYTES`: every detail that repeats a name
    the file holds is charged to the concern's `CollectionBudget` before it
    is built. Past
    the bound the line is not recorded and the budget renders its one
    `scan-cap` line. At the bound's edge the line is recorded, so the charge
    is exactly the name's bytes."""

    NAME = "P" * 300

    def _local(self, relative_path, budget, deps=1, name=NAME):
        objects = {}
        objects["PRJ"] = _obj("PBXProject", mainGroup="MG", targets=["TGT"], packageReferences=["L"])
        objects["MG"] = _obj("PBXGroup", sourceTree="<group>", children=[])
        ref = {} if relative_path is None else {"relativePath": relative_path}
        objects["L"] = _obj("XCLocalSwiftPackageReference", **ref)
        objects["D"] = _obj("XCSwiftPackageProductDependency", package="L", productName=name)
        objects["TGT"] = _obj("PBXNativeTarget", name="App", productType="com.apple.product-type.application",
                              packageProductDependencies=["D"] * deps)
        doc = _pbx(objects, "PRJ")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = _parsed(root, "App.xcodeproj", doc)
            _pd, _deps, res = sxp.resolve_product_dependencies(root, [p], [], collection=budget)
        return res

    def _edge(self, run, needle, cost):
        for limit, recorded in ((cost - 1, False), (cost, True)):
            with self.subTest(needle=needle, limit=limit):
                budget = _sxi.CollectionBudget()
                budget.detail_limit = limit
                res = run(budget)
                self.assertEqual(len([r for r in res if needle in r[3]]), 1 if recorded else 0, res)
                self.assertEqual(budget.detail_line() is None, recorded)

    def test_a_local_reference_with_no_path_charges_the_product_name(self):
        self._edge(lambda b: self._local(None, b), "carries no relativePath", len(self.NAME))

    def test_an_escaping_local_reference_charges_the_product_name(self):
        self._edge(lambda b: self._local("/abs/x", b), "names a path outside the checkout", len(self.NAME))

    def test_n_products_naming_one_reference_stop_building_at_the_bound(self):
        """4,000 products name one path-less reference with a 3,000-byte
        name: the details stop at the bound instead of building 4,000
        copies of the name, and the line renders once."""
        name = "Q" * 3000
        cells = []
        real = sxp._cell

        def spy(value):
            if value == name:
                cells.append(value)
            return real(value)

        budget = _sxi.CollectionBudget()
        with mock.patch.object(sxp, "_cell", spy):
            res = self._local(None, budget, deps=4000, name=name)
        self.assertEqual(len([r for r in res if "carries no relativePath" in r[3]]), 1)
        self.assertIsNotNone(budget.detail_line())
        self.assertLessEqual(len(cells), budget.detail_limit // len(name) + 1, len(cells))

    def _remote(self, requirement, budget, refs=1, url="https://h/x.git"):
        objects = {}
        objects["PRJ"] = _obj("PBXProject", mainGroup="MG", targets=["TGT"], packageReferences=["R"] * refs)
        objects["MG"] = _obj("PBXGroup", sourceTree="<group>", children=[])
        objects["R"] = _obj("XCRemoteSwiftPackageReference", repositoryURL=url, requirement=requirement)
        objects["TGT"] = _obj("PBXNativeTarget", name="App", productType="com.apple.product-type.application")
        doc = _pbx(objects, "PRJ")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = _parsed(root, "App.xcodeproj", doc)
            _pd, deps, res = sxp.resolve_product_dependencies(root, [p], [], collection=budget)
        return deps, res

    def test_an_unrecognised_requirement_kind_charges_its_name(self):
        self._edge(lambda b: self._remote({"kind": self.NAME}, b)[1], "is outside the tested set", len(self.NAME))

    def test_an_unrecognised_requirement_key_charges_its_name(self):
        req = {"kind": "exactVersion", "version": "1.0.0", self.NAME: "x"}
        self._edge(lambda b: self._remote(req, b)[1], "carries an unrecognised key", len(self.NAME))

    def test_a_reference_listed_n_times_is_read_once(self):
        """`packageReferences` naming one remote reference N times: N rows
        share one location string and one requirement cell, and its lines
        are kept once, where each item built its own copy."""
        url = "https://h/" + "u" * 5000
        deps, res = self._remote({"kind": "k" * 5000}, _sxi.CollectionBudget(), refs=50, url=url)
        self.assertEqual(len(deps), 50)
        self.assertEqual(len({id(d["location"]) for d in deps}), 1)
        self.assertEqual(len({id(d["requirement"]) for d in deps}), 1)
        self.assertEqual(deps[0]["location"], url)
        self.assertEqual(len(res), 1)
