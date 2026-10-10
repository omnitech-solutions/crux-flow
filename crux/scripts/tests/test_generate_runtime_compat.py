"""Tests for generate-runtime-compat.py — the runtime-compatibility block regenerator.

Two lanes, deliberately separated:

* **Synthetic-tree lanes** build a miniature repo root (canonical file, skill
  catalog, a handful of `SKILL.md` targets) and drive the failure modes there.
  Mutating the real tree to prove a refusal would leave the checkout dirty on
  a failed assertion, and the refusals are properties of the CODE, not of this
  repo's content.
* **Real-tree lanes** assert the facts that are properties of THIS checkout:
  the projection is in sync, every region is byte-equivalent to the canonical,
  the four exempt skills carry no block, and nothing under the distributed
  roots cites an ADR by number.

Stdlib only. Run:
  uv run python3 -m unittest discover -s crux/scripts/tests -p 'test_generate_runtime_compat.py'
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
SCRIPT = SCRIPTS_DIR / "generate-runtime-compat.py"
from _authoring_fixture import seed_authoring_probe



def _load_module():
    """Import the hyphenated script by location; it is not an importable name."""
    spec = importlib.util.spec_from_file_location("generate_runtime_compat", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load_module()

BEGIN = gen.PROJECTION_BEGIN
END = gen.PROJECTION_END
CANONICAL_BODY = "## Runtime compatibility\n\nIntro sentence.\n\n- One bullet.\n"


def _skill_md(body: str = CANONICAL_BODY, *, marked: bool = True) -> str:
    head = "---\nname: x\ndescription: \"y\"\n---\n\n# Title\n\n"
    if not marked:
        return head + "Some prose.\n"
    return head + f"{BEGIN}\n{body}{END}\n\nMore prose.\n"


class SyntheticTreeCase(unittest.TestCase):
    """Builds a miniature repo root so refusals never touch the real checkout."""

    def _tree(
        self,
        tmp: Path,
        *,
        skills: dict[str, str] | None = None,
        canonical: str = CANONICAL_BODY,
        exempt: set[str] | None = None,
    ) -> Path:
        root = tmp
        seed_authoring_probe(root, SCRIPT)
        (root / "crux" / "templates").mkdir(parents=True)
        (root / "crux" / "catalog").mkdir(parents=True)
        (root / "crux" / "templates" / "runtime-compatibility.md").write_text(
            canonical, encoding="utf-8"
        )
        skills = skills if skills is not None else {"alpha": _skill_md(), "beta": _skill_md()}
        for name, text in skills.items():
            d = root / "crux" / "skills" / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(text, encoding="utf-8")
        (root / "crux" / "catalog" / "skills.json").write_text(
            json.dumps([{"id": n, "name": n} for n in sorted(skills)]), encoding="utf-8"
        )
        self._patch_exempt(exempt if exempt is not None else set())
        return root

    def _patch_exempt(self, names: set[str]) -> None:
        original = gen.EXEMPT_SKILLS
        gen.EXEMPT_SKILLS = frozenset(names)
        self.addCleanup(lambda: setattr(gen, "EXEMPT_SKILLS", original))

    @staticmethod
    def _read(root: Path, name: str) -> str:
        return (root / "crux" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


class HappyPathTests(SyntheticTreeCase):
    def test_writes_the_canonical_body_into_every_marked_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(
                Path(tmp),
                skills={"alpha": _skill_md("stale\n"), "beta": _skill_md("stale\n")},
            )
            code, payload = gen.run(root, dry_run=False)
            self.assertEqual(code, 0)
            self.assertEqual(len(payload["written"]), 2)
            for name in ("alpha", "beta"):
                self.assertIn(CANONICAL_BODY, self._read(root, name))

    def test_is_idempotent_and_dry_run_then_reports_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(Path(tmp), skills={"alpha": _skill_md("stale\n")})
            gen.run(root, dry_run=False)
            code, payload = gen.run(root, dry_run=False)
            self.assertEqual(code, 0)
            self.assertEqual(payload["written"], [])
            code, payload = gen.run(root, dry_run=True)
            self.assertEqual(code, 0)
            self.assertEqual(payload["drifted"], [])

    def test_dry_run_reports_drift_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(Path(tmp), skills={"alpha": _skill_md("stale\n")})
            before = self._read(root, "alpha")
            code, payload = gen.run(root, dry_run=True)
            self.assertEqual(code, 1)
            self.assertEqual(payload["drifted"], ["crux/skills/alpha/SKILL.md"])
            self.assertEqual(self._read(root, "alpha"), before)

    def test_only_the_region_is_touched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(Path(tmp), skills={"alpha": _skill_md("stale\n")})
            gen.run(root, dry_run=False)
            text = self._read(root, "alpha")
            self.assertTrue(text.startswith("---\nname: x\n"))
            self.assertTrue(text.endswith("More prose.\n"))

    def test_crlf_line_endings_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(Path(tmp), skills={"alpha": _skill_md("stale\n")})
            path = root / "crux" / "skills" / "alpha" / "SKILL.md"
            with path.open("r", encoding="utf-8", newline="") as fh:
                crlf = fh.read().replace("\n", "\r\n")
            with path.open("w", encoding="utf-8", newline="") as fh:
                fh.write(crlf)
            code, _ = gen.run(root, dry_run=False)
            self.assertEqual(code, 0)
            with path.open("r", encoding="utf-8", newline="") as fh:
                after = fh.read()
            # The markers were still found (a bare `$` would miss them), and
            # the bytes outside the region kept their CRLF endings.
            self.assertIn("---\r\nname: x\r\n", after)
            self.assertIn("More prose.\r\n", after)

    def test_file_mode_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(Path(tmp), skills={"alpha": _skill_md("stale\n")})
            path = root / "crux" / "skills" / "alpha" / "SKILL.md"
            os.chmod(path, 0o644)
            gen.run(root, dry_run=False)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)


class MarkerContractTests(SyntheticTreeCase):
    def test_missing_end_marker_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = _skill_md().replace(END + "\n", "")
            root = self._tree(Path(tmp), skills={"alpha": broken})
            with self.assertRaises(gen.RegenError) as ctx:
                gen.run(root, dry_run=False)
            self.assertIn("missing marker", str(ctx.exception))

    def test_duplicate_marker_fails_closed_rather_than_guessing(self):
        with tempfile.TemporaryDirectory() as tmp:
            dup = _skill_md() + f"\n{BEGIN}\nsecond\n{END}\n"
            root = self._tree(Path(tmp), skills={"alpha": dup})
            with self.assertRaises(gen.RegenError) as ctx:
                gen.run(root, dry_run=False)
            self.assertIn("duplicate marker", str(ctx.exception))

    def test_misordered_markers_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            head = "---\nname: x\n---\n\n"
            swapped = head + f"{END}\nbody\n{BEGIN}\n"
            root = self._tree(Path(tmp), skills={"alpha": swapped})
            with self.assertRaises(gen.RegenError) as ctx:
                gen.run(root, dry_run=False)
            self.assertIn("precedes BEGIN", str(ctx.exception))

    def test_a_marker_mid_sentence_is_not_a_delimiter(self):
        """Whole-line matching: prose naming the marker must not delimit."""
        with tempfile.TemporaryDirectory() as tmp:
            prose = _skill_md().replace(
                "More prose.", f"The marker is written {BEGIN} inline here."
            )
            root = self._tree(Path(tmp), skills={"alpha": prose})
            code, _ = gen.run(root, dry_run=False)
            self.assertEqual(code, 0)
            self.assertIn(f"written {BEGIN} inline", self._read(root, "alpha"))

    def test_no_target_is_written_when_a_later_target_is_malformed(self):
        """Pass 1 validates everything before pass 2 writes anything."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(
                Path(tmp),
                skills={
                    "alpha": _skill_md("stale\n"),
                    "zeta": _skill_md("stale\n") + f"\n{BEGIN}\ndup\n{END}\n",
                },
            )
            before = self._read(root, "alpha")
            with self.assertRaises(gen.RegenError):
                gen.run(root, dry_run=False)
            self.assertEqual(self._read(root, "alpha"), before)


class CanonicalContractTests(SyntheticTreeCase):
    def test_empty_canonical_refuses_to_project_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(Path(tmp), canonical="   \n\n")
            with self.assertRaises(gen.RegenError) as ctx:
                gen.run(root, dry_run=False)
            self.assertIn("refusing to project nothing", str(ctx.exception))

    def test_missing_canonical_file_is_a_findings_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(Path(tmp))
            (root / gen.CANONICAL_FILE).unlink()
            with self.assertRaises(gen.RegenError) as ctx:
                gen.run(root, dry_run=False)
            self.assertIn("not found", str(ctx.exception))

    def test_canonical_containing_a_projection_marker_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(Path(tmp), canonical=f"## Runtime compatibility\n{BEGIN}\n")
            with self.assertRaises(gen.RegenError) as ctx:
                gen.run(root, dry_run=False)
            self.assertIn("wedge every later run", str(ctx.exception))

    def test_trailing_blank_lines_in_the_canonical_do_not_cause_drift(self):
        """Idempotence must not depend on how the canonical file happens to end."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(Path(tmp), skills={"alpha": _skill_md("stale\n")})
            gen.run(root, dry_run=False)
            (root / gen.CANONICAL_FILE).write_text(CANONICAL_BODY + "\n\n\n", encoding="utf-8")
            code, _ = gen.run(root, dry_run=True)
            self.assertEqual(code, 0)


class FailClosedTargetDerivationTests(SyntheticTreeCase):
    """A skill that silently loses its markers must FAIL, not drop out."""

    def test_a_catalogued_skill_without_markers_or_an_exemption_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(
                Path(tmp),
                skills={"alpha": _skill_md("stale\n"), "beta": _skill_md(marked=False)},
            )
            before = self._read(root, "alpha")
            with self.assertRaises(gen.RegenError) as ctx:
                gen.run(root, dry_run=False)
            self.assertIn("beta", str(ctx.exception))
            self.assertIn("neither the runtime-compat markers nor an exemption", str(ctx.exception))
            # And it fails BEFORE writing, so the drift gate and the writer agree.
            self.assertEqual(self._read(root, "alpha"), before)

    def test_positive_control_the_same_tree_with_an_exemption_passes(self):
        """Without this, the refusal above would pass on a regenerator that always failed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(
                Path(tmp),
                skills={"alpha": _skill_md("stale\n"), "beta": _skill_md(marked=False)},
                exempt={"beta"},
            )
            code, payload = gen.run(root, dry_run=False)
            self.assertEqual(code, 0)
            self.assertEqual(payload["written"], ["crux/skills/alpha/SKILL.md"])
            self.assertEqual(payload["targets"], 1)

    def test_an_exempt_skill_that_carries_markers_fails(self):
        """The exemption list cannot rot in the other direction either."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(
                Path(tmp),
                skills={"alpha": _skill_md(), "beta": _skill_md()},
                exempt={"beta"},
            )
            with self.assertRaises(gen.RegenError) as ctx:
                gen.run(root, dry_run=False)
            self.assertIn("DO carry the runtime-compat markers", str(ctx.exception))

    def test_an_exemption_for_a_deleted_skill_fails(self):
        """The third direction the list can rot in, and the only silent one.

        The other two legs only look at skills that still exist, so an
        exemption left behind by a deleted skill was invisible to both: the
        entry named nothing, matched nothing, and the regenerator reported
        clean. The docstring claimed the list "cannot rot in either direction"
        while this hole was open, which is what this pins shut.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(
                Path(tmp),
                skills={"alpha": _skill_md(), "beta": _skill_md(marked=False)},
                exempt={"beta", "deleted-skill"},
            )
            with self.assertRaises(gen.RegenError) as ctx:
                gen.run(root, dry_run=False)
            self.assertIn("deleted-skill", str(ctx.exception))
            self.assertIn("the catalog does not carry", str(ctx.exception))
            # The still-live exemption is not reported as stale.
            self.assertNotIn("beta", str(ctx.exception))

    def test_positive_control_the_same_tree_without_the_dead_exemption_passes(self):
        """Without this, the refusal above passes on a regenerator that always failed.

        One exemption removed, nothing else changed.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(
                Path(tmp),
                skills={"alpha": _skill_md(), "beta": _skill_md(marked=False)},
                exempt={"beta"},
            )
            code, payload = gen.run(root, dry_run=False)
            self.assertEqual(code, 0)
            self.assertEqual(payload["targets"], 1)

    def test_a_marked_skill_absent_from_the_catalog_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(Path(tmp), skills={"alpha": _skill_md()})
            extra = root / "crux" / "skills" / "ghost"
            extra.mkdir()
            (extra / "SKILL.md").write_text(_skill_md(), encoding="utf-8")
            with self.assertRaises(gen.RegenError) as ctx:
                gen.run(root, dry_run=False)
            self.assertIn("absent from the catalog", str(ctx.exception))

    def test_a_malformed_catalog_is_a_findings_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(Path(tmp))
            (root / gen.CATALOG_FILE).write_text("{not json", encoding="utf-8")
            with self.assertRaises(gen.RegenError) as ctx:
                gen.run(root, dry_run=False)
            self.assertIn("not valid JSON", str(ctx.exception))


class CliContractTests(unittest.TestCase):
    """Exit-code semantics, driven through the script's own CLI."""

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            check=False, capture_output=True, text=True,
        )

    def test_dry_run_on_the_real_tree_exits_zero_with_json(self):
        # --repo-root is explicit: the CLI takes its target from the invocation, so
        # without it this case declines whenever the suite runs from a directory that
        # is not this checkout, and the target-count assertion below measures nothing.
        result = self._run("--dry-run", "--repo-root", str(SCRIPT.parents[2]))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["drifted"], [])
        # A gate that measured nothing would also print an empty drift list.
        self.assertEqual(payload["targets"], 51)

    def test_seeded_drift_makes_the_cli_exit_one_with_json_on_stdout(self):
        """Positive control for the row above, through the same CLI."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed_authoring_probe(root, SCRIPT)
            for sub in ("templates", "catalog", "skills"):
                (root / "crux" / sub).mkdir()
            (root / "crux" / "templates" / "runtime-compatibility.md").write_text(
                CANONICAL_BODY, encoding="utf-8"
            )
            # The exempt names are catalogued too. The CLI runs in a
            # subprocess, so it carries the REAL `EXEMPT_SKILLS`, and an
            # exemption naming a skill this catalog does not list is itself a
            # findings error — a fixture fault that would mask the drift this
            # test is the positive control for.
            (root / "crux" / "catalog" / "skills.json").write_text(
                json.dumps([{"name": n} for n in sorted({"alpha", *gen.EXEMPT_SKILLS})]),
                encoding="utf-8",
            )
            d = root / "crux" / "skills" / "alpha"
            d.mkdir()
            (d / "SKILL.md").write_text(_skill_md("drifted body\n"), encoding="utf-8")

            result = self._run("--dry-run", "--repo-root", str(root))
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["drifted"], ["crux/skills/alpha/SKILL.md"])
            self.assertIn("generate-runtime-compat.py", payload["remediation"])

    def test_a_validation_error_exits_one_with_json_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            seed_authoring_probe(tmp, SCRIPT)
            result = self._run("--dry-run", "--repo-root", tmp)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("error", json.loads(result.stdout))
            self.assertNotIn("Traceback", result.stderr)

    def test_a_non_utf8_target_exits_two_on_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed_authoring_probe(root, SCRIPT)
            (root / "crux" / "templates").mkdir(parents=True)
            (root / "crux" / "catalog").mkdir(parents=True)
            (root / "crux" / "templates" / "runtime-compatibility.md").write_text(
                CANONICAL_BODY, encoding="utf-8"
            )
            (root / "crux" / "catalog" / "skills.json").write_text(
                json.dumps([{"name": "alpha"}]), encoding="utf-8"
            )
            d = root / "crux" / "skills" / "alpha"
            d.mkdir(parents=True)
            (d / "SKILL.md").write_bytes(b"\xff\xfe not utf-8 at all\n")

            result = self._run("--dry-run", "--repo-root", str(root))
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("generate-runtime-compat.py:", result.stderr)
            self.assertNotIn("Traceback", result.stderr)


class RealTreeTests(unittest.TestCase):
    """Facts about THIS checkout, which the synthetic lanes cannot assert."""

    def test_the_projection_covers_51_of_the_55_catalogued_skills(self):
        targets = gen.resolve_targets(REPO_ROOT)
        catalogued = gen._catalogued_skills(REPO_ROOT)
        self.assertEqual(len(catalogued), 55)
        self.assertEqual(len(targets), 51)
        self.assertEqual(len(gen.EXEMPT_SKILLS), 4)
        self.assertEqual(len(targets) + len(gen.EXEMPT_SKILLS), len(catalogued))

    def test_every_region_is_byte_equivalent_to_the_canonical(self):
        canonical = gen._load_canonical(REPO_ROOT)
        for rel in gen.resolve_targets(REPO_ROOT):
            with self.subTest(target=rel):
                text = gen.read_text_verbatim(REPO_ROOT / rel)
                region = gen.extract_region(text, BEGIN, END, rel)
                self.assertEqual(region, canonical)

    def test_the_four_exempt_skills_carry_no_block_and_no_markers(self):
        for name in sorted(gen.EXEMPT_SKILLS):
            with self.subTest(skill=name):
                path = REPO_ROOT / "crux" / "skills" / name / "SKILL.md"
                self.assertTrue(path.is_file(), f"{name} is exempt but has no SKILL.md")
                text = path.read_text(encoding="utf-8")
                self.assertNotIn(BEGIN, text)
                self.assertNotIn("## Runtime compatibility", text)

    def test_the_canonical_names_the_v2_paths_and_labels(self):
        body = (REPO_ROOT / gen.CANONICAL_FILE).read_text(encoding="utf-8")
        # Plural first, singular named as the alternate.
        self.assertLess(body.index("`.opencode/skills`"), body.index("`.opencode/skill`"))
        self.assertIn("`subagent`", body)
        self.assertIn("`shell`", body)
        self.assertNotIn("`task`", body)
        self.assertNotIn("`bash`", body)

    def test_neither_the_canonical_nor_any_projection_cites_an_adr_by_number(self):
        """The distributed-surface constraint the release content scan enforces.

        Provenance for this output lives in the regenerator's docstring
        because `crux/scripts/` is not a scanned root; the canonical file and
        all 48 projections are, and the scan is line-based with no carve-out
        for an HTML comment.
        """
        token = re.compile(r"ADR-\d{4}")
        paths = [REPO_ROOT / gen.CANONICAL_FILE]
        paths += [REPO_ROOT / rel for rel in gen.resolve_targets(REPO_ROOT)]
        for path in paths:
            with self.subTest(path=path.relative_to(REPO_ROOT).as_posix()):
                body = path.read_text(encoding="utf-8")
                region = (
                    body
                    if path.name == "runtime-compatibility.md"
                    else gen.extract_region(body, BEGIN, END, path.name)
                )
                self.assertIsNone(token.search(region))
                self.assertNotIn("[[adrs/", region)


if __name__ == "__main__":
    unittest.main()
