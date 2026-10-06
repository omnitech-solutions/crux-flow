"""Surrogate escapes and surrogate bytes never make the derive exit 2
(ADR-0129 clause 2, ADR-0130 clauses 2 and 4).

A lone surrogate is not a Unicode scalar and has no UTF-8 encoding, so one
that reached a spine line made the writer raise `UnicodeEncodeError` and the
derive exit 2. Before the fix, `crux/scripts/derive-arch.py` exited 2 on a
pbxproj `\\UD800` and on a manifest `\\u{D800}`. Every case here runs that
real CLI in a subprocess against a temporary tree and asserts exit 0 plus the
rendered verdict:

* a pbxproj `\\U` surrogate pair is one scalar and renders; the output of a
  second derive is byte-identical;
* a lone, unpaired or reversed pbxproj surrogate renders `project-unreadable`
  `malformed`;
* a manifest `\\u{D800}` or `\\u{DFFF}` renders `non-literal-manifest`, as an
  out-of-range scalar does; `\\u{D7FF}` and `\\u{E000}`, the scalars either
  side of the surrogate block, render as names;
* the sibling spellings of a surrogate (the bytes ED A0 80 in a `.swift`
  identifier, a `Package.swift` string and an `.xcconfig`, and `&#xD800;` in
  a workspace) each render a residual and exit 0.

The positive control for the exit-0 assertions is a mutation: with the
decoder fixes reverted, every pbxproj and `\\u{...}` case here exits 2. It is
recorded in the run's ledger rather than kept as a test, because the
reverted decoder is the defect itself.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
DRIVER = TESTS.parent / "derive-arch.py"
FIXTURES = TESTS / "fixtures"
PROJECT_108 = FIXTURES / "xcodeproj" / "108-non-string-repository-url"
PLAIN_MANIFEST = FIXTURES / "swiftmanifests" / "plain-names"
WORKSPACE_76 = FIXTURES / "xcodeproj" / "76-hostile-workspace-well-formed-project-and-package"
XCCONFIG_41 = FIXTURES / "xcodeproj" / "41-limitation-build-settings"

#: The bytes a UTF-8 encoder that does not refuse surrogates writes for
#: U+D800 (CESU-8 and WTF-8 do). Strict UTF-8 refuses them.
SURROGATE_BYTES = b"\xed\xa0\x80"


def _derive(root: Path) -> tuple[int, dict, str]:
    """`(exit code, {concern: rendered text}, stderr)` of one real CLI run."""
    proc = subprocess.run([sys.executable, str(DRIVER), "--repo-root", str(root)],
                          capture_output=True, timeout=180)
    arch = root / "bionic" / "arch"
    rendered = {p.stem: p.read_text(encoding="utf-8") for p in sorted(arch.glob("*.md"))} \
        if arch.is_dir() else {}
    return proc.returncode, rendered, proc.stderr.decode("utf-8", "replace")


def _spine_bytes(root: Path) -> dict:
    arch = root / "bionic" / "arch"
    return {str(p.relative_to(arch)): p.read_bytes() for p in sorted(arch.rglob("*")) if p.is_file()}


def _copy(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst)
    (dst / "expected.yml").unlink(missing_ok=True)
    if not (dst / "bionic" / "manifest.yml").exists():
        shutil.copytree(PROJECT_108 / "bionic", dst / "bionic")
        shutil.copy2(PROJECT_108 / ".bionic.yml", dst / ".bionic.yml")
    return dst


def _replace_bytes(path: Path, old: bytes, new: bytes) -> None:
    raw = path.read_bytes()
    if raw.count(old) != 1:
        raise AssertionError(f"{path.name}: {old!r} occurs {raw.count(old)} times, not once")
    path.write_bytes(raw.replace(old, new))


class _Tree(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()

    def assertResidual(self, rendered: dict, bullet_prefix: str) -> None:
        lines = [line for body in rendered.values() for line in body.split("\n")]
        self.assertTrue(any(line.startswith(bullet_prefix) for line in lines),
                        f"no residual starting {bullet_prefix!r}")

    def assertNoReplacementCharacter(self, rendered: dict) -> None:
        for concern, body in rendered.items():
            self.assertNotIn("\ufffd", body, concern)


class PbxprojSurrogateEscapeTests(_Tree):
    """A `\\U` escape is one UTF-16 code unit: a pair combines, and a lone
    surrogate is a grammar failure."""

    def _project(self, name_escape: bytes) -> Path:
        root = _copy(PROJECT_108, self.tmp / "case")
        _replace_bytes(root / "App.xcodeproj" / "project.pbxproj",
                       b'name = "App";', b'name = "App' + name_escape + b'";')
        return root

    def test_a_surrogate_pair_renders_one_scalar(self):
        root = self._project(rb"\UD83D\UDE00")
        code, rendered, err = _derive(root)
        self.assertEqual(code, 0, err)
        self.assertIn("| `App\U0001F600` | `App.xcodeproj` | app | application |",
                      rendered["module-graph"])
        self.assertNotIn("project-unreadable", rendered["module-graph"])
        self.assertNoReplacementCharacter(rendered)

    def test_a_second_derive_of_the_pair_is_byte_identical(self):
        root = self._project(rb"\UD83D\UDE00")
        code, _rendered, err = _derive(root)
        self.assertEqual(code, 0, err)
        first = _spine_bytes(root)
        code, _rendered, err = _derive(root)
        self.assertEqual(code, 0, err)
        self.assertEqual(_spine_bytes(root), first)
        self.assertTrue(any("\U0001F600".encode("utf-8") in b for b in first.values()))

    def test_each_lone_surrogate_shape_renders_malformed(self):
        shapes = {
            "lone high": rb"\UD800",
            "lone low": rb"\UDC00",
            "high then a non-low escape": rb"\UD800\U0041",
            "reversed pair": rb"\UDE00\UD83D",
        }
        for label, escape in shapes.items():
            with self.subTest(shape=label):
                shutil.rmtree(self.tmp / "case", ignore_errors=True)
                code, rendered, err = _derive(self._project(escape))
                self.assertEqual(code, 0, err)
                self.assertResidual(
                    rendered, "- `project-unreadable` `App.xcodeproj/project.pbxproj` lines — — malformed")
                self.assertNotIn("| app | application |", rendered["module-graph"])
                self.assertNoReplacementCharacter(rendered)

    def test_a_non_surrogate_escape_still_decodes(self):
        # Control: `\U0041` is `A`; the pairing rule touches surrogates only.
        code, rendered, err = _derive(self._project(rb"\U0041"))
        self.assertEqual(code, 0, err)
        self.assertIn("| `AppA` | `App.xcodeproj` | app | application |", rendered["module-graph"])


class ManifestSurrogateScalarTests(_Tree):
    """`\\u{D800}`-`\\u{DFFF}` is not a Swift Unicode scalar, so the literal is
    outside the literal subset (ADR-0129:222)."""

    def _manifest(self, product_name: bytes) -> Path:
        root = _copy(PLAIN_MANIFEST, self.tmp / "case")
        _replace_bytes(root / "Package.swift", b'name: "Pipex"', b'name: "' + product_name + b'"')
        return root

    _NON_LITERAL = ("- `non-literal-manifest` `Package.swift` lines 6-6 — construct unsupported call "
                    "is outside the literal Package.swift subset and is not evaluated")

    def test_a_surrogate_scalar_renders_non_literal_manifest(self):
        for escape in (rb"Pi\u{D800}pex", rb"Pi\u{DFFF}pex"):
            with self.subTest(escape=escape):
                shutil.rmtree(self.tmp / "case", ignore_errors=True)
                code, rendered, err = _derive(self._manifest(escape))
                self.assertEqual(code, 0, err)
                self.assertResidual(rendered, self._NON_LITERAL)
                self.assertNotIn("| `Pi", rendered["api-surface"])
                self.assertNoReplacementCharacter(rendered)

    def test_the_same_verdict_as_an_out_of_range_scalar(self):
        code, surrogate, err = _derive(self._manifest(rb"Pi\u{D800}pex"))
        self.assertEqual(code, 0, err)
        shutil.rmtree(self.tmp / "case")
        code, out_of_range, err = _derive(self._manifest(rb"Pi\u{110000}pex"))
        self.assertEqual(code, 0, err)
        self.assertEqual(surrogate, out_of_range)

    def test_the_scalars_either_side_of_the_surrogate_block_render(self):
        for escape, name in ((rb"Pi\u{D7FF}pex", "Pi\ud7ffpex"), (rb"Pi\u{E000}pex", "Pi\ue000pex")):
            with self.subTest(escape=escape):
                shutil.rmtree(self.tmp / "case", ignore_errors=True)
                code, rendered, err = _derive(self._manifest(escape))
                self.assertEqual(code, 0, err)
                self.assertIn(f"| `{name}` | library | `Package.swift` |", rendered["api-surface"])
                self.assertNotIn("non-literal-manifest", rendered["module-graph"])


class SurrogateBytesSiblingTests(_Tree):
    """The sibling spellings of U+D800 each exit 0 with a residual, and none
    renders U+FFFD in a name."""

    def test_surrogate_bytes_in_a_swift_identifier(self):
        root = _copy(WORKSPACE_76, self.tmp / "case")
        _replace_bytes(root / "Sources" / "Lib" / "Lib.swift", b"struct Lib", b"struct Li" + SURROGATE_BYTES + b"b")
        code, rendered, err = _derive(root)
        self.assertEqual(code, 0, err)
        self.assertResidual(rendered, "- `parse-error` `Sources/Lib/Lib.swift` lines 1-1 — ")
        self.assertNoReplacementCharacter(rendered)

    def test_surrogate_bytes_in_a_package_manifest_string(self):
        root = _copy(PLAIN_MANIFEST, self.tmp / "case")
        _replace_bytes(root / "Package.swift", b'name: "Pipex"', b'name: "Pi' + SURROGATE_BYTES + b'pex"')
        code, rendered, err = _derive(root)
        self.assertEqual(code, 0, err)
        self.assertResidual(rendered, ManifestSurrogateScalarTests._NON_LITERAL)
        self.assertNotIn("| `Pi", rendered["api-surface"])
        self.assertNoReplacementCharacter(rendered)

    def test_surrogate_bytes_in_an_xcconfig(self):
        root = _copy(XCCONFIG_41, self.tmp / "case")
        with (root / "Shared.xcconfig").open("ab") as f:
            f.write(b"OTHER_" + SURROGATE_BYTES + b" = a" + SURROGATE_BYTES + b"b\n")
        code, rendered, err = _derive(root)
        self.assertEqual(code, 0, err)
        self.assertResidual(rendered, "- `project-unreadable` `Shared.xcconfig` lines — — undecodable")
        self.assertNoReplacementCharacter(rendered)

    def test_a_surrogate_character_reference_in_a_workspace(self):
        root = _copy(WORKSPACE_76, self.tmp / "case")
        _replace_bytes(root / "App.xcworkspace" / "contents.xcworkspacedata",
                       b"group:Modules", b"group:Mod&#xD800;ules")
        code, rendered, err = _derive(root)
        self.assertEqual(code, 0, err)
        self.assertResidual(
            rendered, "- `project-unreadable` `App.xcworkspace/contents.xcworkspacedata` lines — — malformed")
        self.assertNoReplacementCharacter(rendered)


if __name__ == "__main__":
    unittest.main()
