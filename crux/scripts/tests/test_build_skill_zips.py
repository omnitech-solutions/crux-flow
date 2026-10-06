"""Tests for build-skill-zips.py packaging CLI per ADR-0006.

Uses tempfile.TemporaryDirectory() to construct synthetic skill trees so we
never depend on the real crux/skills/ contents. Stdlib only.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "crux" / "scripts" / "build-skill-zips.py"


def _load_packager():
    spec = importlib.util.spec_from_file_location("build_skill_zips", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_skill_zips"] = mod
    spec.loader.exec_module(mod)
    return mod


packager = _load_packager()


def _make_skill(skills_dir: Path, name: str, files: dict[str, str | bytes]) -> Path:
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True)
    for rel, content in files.items():
        target = skill_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    return skill_dir


def _run(skills_dir: Path, output_dir: Path, version: str = "0.1.0") -> int:
    buf = io.StringIO()
    with redirect_stdout(buf):
        return packager.main(
            [
                "--version",
                version,
                "--skills-dir",
                str(skills_dir),
                "--output-dir",
                str(output_dir),
            ]
        )


class BuildSkillZipsTests(unittest.TestCase):
    def test_per_skill_zip_has_wrapped_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            out = root / "dist"
            _make_skill(skills, "alpha", {"SKILL.md": "---\nname: alpha\n---\nbody\n"})
            rc = _run(skills, out)
            self.assertEqual(rc, 0)
            zpath = out / "alpha-0.1.0.zip"
            self.assertTrue(zpath.is_file())
            with zipfile.ZipFile(zpath) as zf:
                names = zf.namelist()
            self.assertTrue(all(n.startswith("alpha/") for n in names), names)
            self.assertIn("alpha/SKILL.md", names)

    def test_dotfiles_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            out = root / "dist"
            _make_skill(
                skills,
                "beta",
                {
                    "SKILL.md": "x",
                    ".gitkeep": "",
                    ".DS_Store": "junk",
                    "scripts/.hidden": "nope",
                    "scripts/main.py": "print('ok')\n",
                },
            )
            rc = _run(skills, out)
            self.assertEqual(rc, 0)
            with zipfile.ZipFile(out / "beta-0.1.0.zip") as zf:
                names = set(zf.namelist())
            self.assertIn("beta/SKILL.md", names)
            self.assertIn("beta/scripts/main.py", names)
            for forbidden in ("beta/.gitkeep", "beta/.DS_Store", "beta/scripts/.hidden"):
                self.assertNotIn(forbidden, names)

    def test_files_outside_allowlist_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            out = root / "dist"
            _make_skill(
                skills,
                "gamma",
                {
                    "SKILL.md": "x",
                    "random.txt": "stray",
                    "tools/foo.py": "stray",
                    "README.md": "stray",
                },
            )
            rc = _run(skills, out)
            self.assertEqual(rc, 0)
            with zipfile.ZipFile(out / "gamma-0.1.0.zip") as zf:
                names = set(zf.namelist())
            self.assertEqual(names, {"gamma/SKILL.md"})

    def test_files_inside_allowlist_included(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            out = root / "dist"
            _make_skill(
                skills,
                "delta",
                {
                    "SKILL.md": "x",
                    "scripts/main.py": "print(1)\n",
                    "reference/api.md": "# api\n",
                    "references/legacy.md": "# legacy\n",
                    "assets/logo.svg": "<svg/>",
                    "examples/demo.sh": "#!/bin/sh\necho hi\n",
                },
            )
            rc = _run(skills, out)
            self.assertEqual(rc, 0)
            with zipfile.ZipFile(out / "delta-0.1.0.zip") as zf:
                names = set(zf.namelist())
            for expected in (
                "delta/SKILL.md",
                "delta/scripts/main.py",
                "delta/reference/api.md",
                "delta/references/legacy.md",
                "delta/assets/logo.svg",
                "delta/examples/demo.sh",
            ):
                self.assertIn(expected, names)

    def test_aggregate_contains_all_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            out = root / "dist"
            for n in ("alpha", "beta", "gamma"):
                _make_skill(skills, n, {"SKILL.md": f"# {n}\n"})
            rc = _run(skills, out)
            self.assertEqual(rc, 0)
            agg = out / "crux-skills-0.1.0.zip"
            self.assertTrue(agg.is_file())
            with zipfile.ZipFile(agg) as zf:
                names = zf.namelist()
            top_dirs = {n.split("/", 1)[0] for n in names}
            # The aggregate zip contains exactly the per-skill dirs plus the
            # ADR-0009 README at the root — no more, no less. The exclusivity
            # check guards against future bugs that stuff stray entries in.
            self.assertEqual(top_dirs, {"alpha", "beta", "gamma", "README.md"})
            for n in ("alpha", "beta", "gamma"):
                self.assertIn(f"{n}/SKILL.md", names)

    def test_aggregate_contains_upload_warning_readme(self):
        """Per ADR-0009: the aggregate zip ships a README.md at its root
        carrying the upload-incompatibility warning that previously lived in
        the GitHub Release body's static prose."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            out = root / "dist"
            _make_skill(skills, "alpha", {"SKILL.md": "# alpha\n"})
            rc = _run(skills, out)
            self.assertEqual(rc, 0)
            agg = out / "crux-skills-0.1.0.zip"
            with zipfile.ZipFile(agg) as zf:
                self.assertIn("README.md", zf.namelist())
                content = zf.read("README.md").decode("utf-8")
            self.assertIn("NOT directly uploadable", content)
            self.assertIn("0.1.0", content)
            self.assertIn("ADR-0006", content)
            self.assertIn("ADR-0009", content)

    def test_aggregate_readme_function_produces_warning_text(self):
        """Unit-layer test of _build_aggregate_readme — the README emission
        function in isolation. Gates pre-push under the unittest discover
        gate so a regression here blocks the release before any zip is
        built."""
        readme = packager._build_aggregate_readme("0.4.0")
        self.assertIn("NOT directly uploadable", readme)
        self.assertIn("0.4.0", readme)
        self.assertIn("ADR-0006", readme)
        self.assertIn("ADR-0009", readme)
        # First line is the H1 with the version-stamped filename.
        first_line = readme.splitlines()[0]
        self.assertEqual(first_line, "# crux-skills-0.4.0.zip")

    def test_aggregate_readme_with_prerelease_version(self):
        """Pre-release version strings (e.g., 0.4.0-alpha.1) must survive
        the README emission verbatim — both in the filename and in the URL
        path fragments that reference the git tag."""
        readme = packager._build_aggregate_readme("0.4.0-alpha.1")
        self.assertIn("crux-skills-0.4.0-alpha.1.zip", readme)
        self.assertIn("<skill-name>-0.4.0-alpha.1.zip", readme)
        # URL path uses v<version> for the tag.
        self.assertIn("/blob/v0.4.0-alpha.1/docs/adrs/", readme)

    def test_aggregate_zip_is_byte_deterministic(self):
        """The aggregate zip's contents — including the README — must be
        byte-stable across builds so checksums and caching work. The
        original `_write_zip_entries` already pinned date_time and
        external_attr for per-skill files; the README must use the same
        DETERMINISTIC_DATE_TIME path."""
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            out1 = root / "dist1"
            out2 = root / "dist2"
            for n in ("alpha", "beta"):
                _make_skill(skills, n, {"SKILL.md": f"# {n}\n"})
            self.assertEqual(_run(skills, out1), 0)
            self.assertEqual(_run(skills, out2), 0)
            agg1 = (out1 / "crux-skills-0.1.0.zip").read_bytes()
            agg2 = (out2 / "crux-skills-0.1.0.zip").read_bytes()
            self.assertEqual(
                hashlib.sha256(agg1).hexdigest(),
                hashlib.sha256(agg2).hexdigest(),
                msg="aggregate zip is not byte-deterministic across builds",
            )

    def test_ascii_assertion_rejects_utf8_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            out = root / "dist"
            _make_skill(
                skills,
                "epsilon",
                {
                    "SKILL.md": "x",
                    "reference/café.md": "non-ascii filename\n",
                },
            )
            rc = _run(skills, out)
            self.assertEqual(rc, 1)
            self.assertFalse((out / "epsilon-0.1.0.zip").exists())

    def test_size_cap_per_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            out = root / "dist"
            _make_skill(skills, "zeta", {"SKILL.md": "x" * 1024})
            with mock.patch.object(packager, "PER_SKILL_SIZE_CAP_BYTES", 16):
                rc = _run(skills, out)
            self.assertEqual(rc, 1)
            self.assertFalse((out / "zeta-0.1.0.zip").exists())
            self.assertFalse(
                (out / "crux-skills-0.1.0.zip").exists(),
                "aggregate must not exist on per-skill failure — proves cleanup ran",
            )

    def test_size_cap_does_not_apply_to_aggregate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            out = root / "dist"
            _make_skill(skills, "eta", {"SKILL.md": "small\n"})
            _make_skill(skills, "theta", {"SKILL.md": "also small\n"})
            # Cap big enough for per-skill zips but the aggregate is fine even
            # without a cap (no cap is enforced on aggregate by design).
            rc = _run(skills, out)
            self.assertEqual(rc, 0)
            self.assertTrue((out / "crux-skills-0.1.0.zip").is_file())

    def test_non_directory_entries_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            skills.mkdir()
            (skills / ".gitkeep").write_text("")
            _make_skill(skills, "iota", {"SKILL.md": "x"})
            rc = _run(skills, root / "dist")
            self.assertEqual(rc, 0)
            self.assertTrue((root / "dist" / "iota-0.1.0.zip").is_file())

    def test_invalid_semver_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            _make_skill(skills, "kappa", {"SKILL.md": "x"})
            rc = _run(skills, root / "dist", version="v1.0.0")
            self.assertEqual(rc, 1)

    def test_semver_trailing_newline_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            _make_skill(skills, "mu", {"SKILL.md": "x"})
            rc = _run(skills, root / "dist", version="1.0.0\n")
            self.assertEqual(rc, 1)
            self.assertFalse((root / "dist").exists() and any((root / "dist").iterdir()))

    def test_skill_md_as_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            skill = skills / "omicron"
            skill.mkdir(parents=True)
            real_target = root / "real_SKILL.md"
            real_target.write_text("hello\n")
            (skill / "SKILL.md").symlink_to(real_target)
            rc = _run(skills, root / "dist")
            self.assertEqual(rc, 1)
            self.assertFalse((root / "dist" / "omicron-0.1.0.zip").exists())

    def test_symlinked_file_inside_scripts_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            skill_dir = _make_skill(skills, "pi", {
                "SKILL.md": "x",
                "scripts/real.py": "print('real')\n",
            })
            target = root / "external.py"
            target.write_text("external\n")
            (skill_dir / "scripts" / "link.py").symlink_to(target)
            self.assertEqual(_run(skills, root / "dist"), 0)
            with zipfile.ZipFile(root / "dist" / "pi-0.1.0.zip") as zf:
                names = zf.namelist()
            self.assertIn("pi/scripts/real.py", names)
            self.assertNotIn("pi/scripts/link.py", names)

    def test_skill_dir_without_SKILL_md_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            _make_skill(skills, "valid", {"SKILL.md": "x"})
            (skills / "no-skill-md").mkdir()
            (skills / "no-skill-md" / "stray.txt").write_text("y")
            self.assertEqual(_run(skills, root / "dist"), 0)
            self.assertTrue((root / "dist" / "valid-0.1.0.zip").exists())
            self.assertFalse((root / "dist" / "no-skill-md-0.1.0.zip").exists())

    def test_nonexistent_skills_dir_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc = _run(root / "does-not-exist", root / "dist")
            self.assertEqual(rc, 1)

    def test_cleanup_does_not_delete_unrelated_zips_with_matching_version(self):
        # Verifies the fix for the partial-cleanup-by-glob bug: cleanup must
        # touch only zips THIS run wrote, never any pre-existing `-<ver>.zip`
        # left over from a prior run or by a sibling tool.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            _make_skill(skills, "nu", {"SKILL.md": "x" * 1024})
            dist = root / "dist"
            dist.mkdir()
            unrelated = dist / "unrelated-prior-0.1.0.zip"
            unrelated.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
            unrelated_bytes = unrelated.read_bytes()

            with mock.patch.object(packager, "PER_SKILL_SIZE_CAP_BYTES", 1):
                rc = _run(skills, dist)
            self.assertEqual(rc, 1)
            self.assertTrue(unrelated.exists(), "unrelated prior zip was wrongly deleted on failure")
            self.assertEqual(unrelated.read_bytes(), unrelated_bytes)

    def test_zip_bytes_are_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            _make_skill(skills, "lambda", {"SKILL.md": "hello\n", "scripts/x.py": "a\n"})
            out1 = root / "dist1"
            out2 = root / "dist2"
            self.assertEqual(_run(skills, out1), 0)
            self.assertEqual(_run(skills, out2), 0)
            self.assertEqual(
                (out1 / "lambda-0.1.0.zip").read_bytes(),
                (out2 / "lambda-0.1.0.zip").read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
