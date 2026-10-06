"""An ADR whose frontmatter fence does not parse is reported, not skipped.

THE DEFECT. `generate-adr-index.py`, `generate-lineage.py` and
`generate-index-rollup.py` each read an ADR's frontmatter with the strict
fence regex and, on no match, `continue`. A file that passes the `ADR-*.md`
glob but fails that regex was dropped in silence: the generator wrote an
output without it, and the next drift check compared that shortened output
against what the generator would now write and found them equal. One
trailing space after the opening `---` is enough, and the YAML underneath
stays valid.

The release suite did catch it, through a lineage test that derives ADR ids
from FILENAMES. Its message names `lineage.md`, so the remedy it implies —
regenerate lineage — cannot work: the generator is the thing that cannot
read the input. These lanes make the generators name the input instead.

Run:
  uv run python3 -m unittest discover -s crux/scripts/tests -p 'test_adr_fence_validation.py'
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _dev_surface import REPO_ROOT  # noqa: E402

#: The three generators under test, and the output each owns.
GENERATORS = ("generate-adr-index.py", "generate-lineage.py", "generate-index-rollup.py")


def adr(num: int, *, amends: list[int] | None = None, title: str | None = None) -> str:
    """A minimal ADR carrying every field the three generators read."""
    amends_list = ", ".join(f"ADR-{n:04d}" for n in (amends or []))
    return (
        "---\n"
        f"id: ADR-{num:04d}\n"
        f"title: \"{title or f'Decision {num}'}\"\n"
        "status: Accepted\n"
        "date: 2026-09-16\n"
        "proposed_date: 2026-09-15\n"
        "accepted_date: 2026-09-16\n"
        "supersedes: []\n"
        f"amends: [{amends_list}]\n"
        "superseded_by: null\n"
        "tags: [fixture]\n"
        "---\n"
        "\n# Body\n\n## Context\n\nFixture.\n"
    )


class AdrFenceTestCase(unittest.TestCase):
    """A disposable tree carrying an `adrs/` surface and an index to roll up."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".bionic.yml").write_text(
            'config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8")
        self.docs = self.root / "bionic"
        self.adrs = self.docs / "adrs"
        self.adrs.mkdir(parents=True)
        (self.docs / "manifest.yml").write_text(
            'schema_version: "5"\nconcerns_enabled: [adrs]\n', encoding="utf-8")
        # ADR-0001 is amended by ADR-0002 (the referenced case); ADR-0003 is a leaf.
        self.write_adr(1)
        self.write_adr(2, amends=[1])
        self.write_adr(3)
        (self.docs / "index.md").write_text(
            "# index\n\n_Last updated: 2026-09-16_\n\n## ADRs (0)\n\n"
            "| id | title | status | date |\n|---|---|---|---|\n\n"
            "## Research\n\nnone\n", encoding="utf-8")

    # ── fixtures ────────────────────────────────────────────────────────────
    def write_adr(self, num: int, **kw) -> Path:
        p = self.adrs / f"ADR-{num:04d}-fixture-{num}.md"
        p.write_text(adr(num, **kw), encoding="utf-8")
        return p

    def break_fence(self, num: int) -> Path:
        """One trailing space after the opening `---`. The YAML stays valid."""
        p = next(self.adrs.glob(f"ADR-{num:04d}-*.md"))
        b = p.read_bytes()
        assert b.startswith(b"---\n"), b[:8]
        p.write_bytes(b"--- \n" + b[4:])
        return p

    def rel(self, p: Path) -> str:
        return str(p.relative_to(self.root))

    # ── drivers ─────────────────────────────────────────────────────────────
    def run_gen(self, script: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / script), "--repo-root", str(self.root), *args],
            capture_output=True, timeout=120)

    def payload(self, r: subprocess.CompletedProcess) -> dict:
        return json.loads(r.stdout.decode("utf-8"))

    def outputs(self) -> dict[Path, bytes | None]:
        """Every output the three generators own, as bytes or None if absent."""
        return {p: (p.read_bytes() if p.is_file() else None)
                for p in (self.adrs / "index.md",
                          self.adrs / "lineage.md",
                          self.docs / "index.md")}

    def seed_outputs(self) -> dict[Path, bytes | None]:
        """Regenerate from a clean corpus; return the resulting bytes."""
        for g in GENERATORS:
            r = self.run_gen(g)
            self.assertEqual(r.returncode, 0, f"{g}: {r.stdout!r} {r.stderr!r}")
        return self.outputs()

    def assert_refusal(self, r: subprocess.CompletedProcess, offenders: list[Path],
                       script: str, mode: str) -> None:
        """Exit 1, one validation_errors entry per offender, each naming the INPUT."""
        ctx = f"{script} {mode}: rc={r.returncode} out={r.stdout!r} err={r.stderr!r}"
        self.assertEqual(r.returncode, 1, ctx)
        errors = self.payload(r).get("validation_errors")
        self.assertTrue(errors, f"no validation_errors — {ctx}")
        self.assertEqual(len(errors), len(offenders), f"one entry per file — {errors}")
        got = sorted(e["file"] for e in errors)
        self.assertEqual(got, sorted(self.rel(p) for p in offenders))
        for e in errors:
            # The entry explains the INPUT is at fault, not the output.
            self.assertIn("glob", e["error"], e)
            self.assertIn("fence", e["error"], e)
            for out in ("index.md", "lineage.md"):
                self.assertNotIn(f"run {out}", e["error"])


class ReferencedAdrTests(AdrFenceTestCase):
    """CASE 1 — a malformed ADR that another ADR amends.

    This is the shape that leaves dangling edges behind: the node goes, the
    references to it survive, and the graph names a node it does not define.
    """

    def test_all_three_refuse_in_both_modes_and_name_the_input(self):
        self.seed_outputs()
        bad = self.break_fence(1)
        for script in GENERATORS:
            for mode, args in (("write", ()), ("dry-run", ("--dry-run",))):
                with self.subTest(script=script, mode=mode):
                    self.assert_refusal(self.run_gen(script, *args), [bad], script, mode)


class LeafAdrTests(AdrFenceTestCase):
    """CASE 2 — a malformed leaf ADR, referenced by nothing.

    Worse than the referenced case: it leaves no dangling edge, so the output
    is internally consistent and simply short.
    """

    def test_all_three_refuse_in_both_modes_and_name_the_input(self):
        self.seed_outputs()
        bad = self.break_fence(3)
        for script in GENERATORS:
            for mode, args in (("write", ()), ("dry-run", ("--dry-run",))):
                with self.subTest(script=script, mode=mode):
                    self.assert_refusal(self.run_gen(script, *args), [bad], script, mode)


class MultipleOffenderTests(AdrFenceTestCase):
    """CASE 4 — one diagnostic per offending file, not one for the run."""

    def test_two_malformed_inputs_produce_two_entries(self):
        self.seed_outputs()
        bad = [self.break_fence(1), self.break_fence(3)]
        for script in GENERATORS:
            with self.subTest(script=script):
                self.assert_refusal(self.run_gen(script), bad, script, "write")

    def test_an_archived_tier_offender_is_reported_too(self):
        # Both tiers feed all three generators, so both must be validated.
        self.seed_outputs()
        arch = self.adrs / "archive"
        arch.mkdir()
        p = arch / "ADR-0004-archived.md"
        p.write_bytes(b"--- \n" + adr(4).encode("utf-8")[4:])
        for script in GENERATORS:
            with self.subTest(script=script):
                self.assert_refusal(self.run_gen(script), [p], script, "write")


class OutputPreservationTests(AdrFenceTestCase):
    """CASE 5 and 6 — refusal touches nothing."""

    def test_existing_outputs_are_byte_identical_after_refusal(self):
        before = self.seed_outputs()
        self.break_fence(1)
        for script in GENERATORS:
            self.run_gen(script)
        self.assertEqual(self.outputs(), before)

    def test_a_valid_input_read_first_still_writes_nothing(self):
        # ADR-0001 and ADR-0002 parse; the offender sorts LAST. A generator
        # that validated lazily would already have accumulated records — the
        # refusal must still precede every write.
        before = self.seed_outputs()
        self.break_fence(3)
        for script in GENERATORS:
            with self.subTest(script=script):
                r = self.run_gen(script)
                self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual(self.outputs(), before)

    def test_no_output_is_created_when_none_existed(self):
        # CASE 6: the outputs do not exist yet. A refusal must not bring them
        # into being half-populated.
        self.break_fence(1)
        for script in ("generate-adr-index.py", "generate-lineage.py"):
            with self.subTest(script=script):
                r = self.run_gen(script)
                self.assertEqual(r.returncode, 1, r.stdout)
        self.assertFalse((self.adrs / "index.md").exists())
        self.assertFalse((self.adrs / "lineage.md").exists())


class BoundaryTests(AdrFenceTestCase):
    """CASE 7 and 8 — what the refusal must NOT reach."""

    def test_a_clean_corpus_still_indexes_every_adr(self):
        self.seed_outputs()
        index = (self.adrs / "index.md").read_text(encoding="utf-8")
        lineage = (self.adrs / "lineage.md").read_text(encoding="utf-8")
        rollup = (self.docs / "index.md").read_text(encoding="utf-8")
        for n in ("ADR-0001", "ADR-0002", "ADR-0003"):
            self.assertIn(n, index)
            self.assertIn(n.replace("-", "_"), lineage)
            self.assertIn(n, rollup)
        for g in GENERATORS:
            with self.subTest(script=g):
                self.assertEqual(self.run_gen(g, "--dry-run").returncode, 0)

    def test_a_non_adr_file_in_the_same_directory_produces_no_finding(self):
        self.seed_outputs()
        (self.adrs / "scratch-notes.md").write_text("# notes\n\nnot an ADR\n", encoding="utf-8")
        (self.adrs / "README.md").write_bytes(b"--- \nbroken: fence\n---\n\nnot an ADR\n")
        for g in GENERATORS:
            with self.subTest(script=g):
                r = self.run_gen(g, "--dry-run")
                self.assertEqual(r.returncode, 0, r.stdout)
                self.assertNotIn("validation_errors", r.stdout.decode("utf-8"))

    def test_the_accepted_fence_syntax_is_unchanged(self):
        # The parser must not become MORE permissive: the corrupted file that
        # this fix reports must still not be READ as a valid ADR.
        self.seed_outputs()
        self.break_fence(1)
        r = self.run_gen("generate-adr-index.py")
        self.assertEqual(r.returncode, 1)
        # And repairing the fence restores a clean run with the ADR present.
        p = next(self.adrs.glob("ADR-0001-*.md"))
        p.write_bytes(b"---\n" + p.read_bytes()[5:])
        self.assertEqual(self.run_gen("generate-adr-index.py").returncode, 0)
        self.assertIn("ADR-0001", (self.adrs / "index.md").read_text(encoding="utf-8"))


class UnreadableInputLaneTests(AdrFenceTestCase):
    """A file the process cannot read is the ENVIRONMENT lane, not a finding.

    The distinction matters downstream: `check-drift` reads an exit-1
    `validation_errors` payload as "the named input must be repaired first",
    which is advice nobody can follow for a permission error. The sibling
    `generate-reviews-index.py` routes the same case to exit 2, and so does
    this one.
    """

    def test_a_non_utf8_adr_is_an_environment_error_not_a_finding(self):
        self.seed_outputs()
        p = next(self.adrs.glob("ADR-0001-*.md"))
        p.write_bytes(b"---\nid: \xff\xfe\n---\n\nbody\n")
        for script in GENERATORS:
            for mode, args in (("write", ()), ("dry-run", ("--dry-run",))):
                with self.subTest(script=script, mode=mode):
                    r = self.run_gen(script, *args)
                    self.assertEqual(r.returncode, 2,
                                     f"{script} {mode}: {r.stdout!r} {r.stderr!r}")
                    self.assertNotIn(b"validation_errors", r.stdout)
                    self.assertIn(b"UnicodeDecodeError", r.stderr)

    def test_an_unreadable_adr_is_an_environment_error_not_a_finding(self):
        import os
        if os.geteuid() == 0:
            self.skipTest("running as root: a 0o000 file is still readable")
        self.seed_outputs()
        p = next(self.adrs.glob("ADR-0001-*.md"))
        p.chmod(0o000)
        self.addCleanup(p.chmod, 0o644)
        for script in GENERATORS:
            with self.subTest(script=script):
                r = self.run_gen(script)
                self.assertEqual(r.returncode, 2, f"{script}: {r.stdout!r} {r.stderr!r}")
                self.assertNotIn(b"validation_errors", r.stdout)

    def test_the_two_lanes_stay_distinguishable(self):
        # PAIRED CONTROL: the same corpus, one fence break instead of an
        # unreadable file, takes the document lane. If both landed on the same
        # exit code the distinction above would be untestable.
        self.seed_outputs()
        bad = self.break_fence(1)
        r = self.run_gen("generate-adr-index.py")
        self.assertEqual(r.returncode, 1)
        self.assertEqual([e["file"] for e in self.payload(r)["validation_errors"]],
                         [self.rel(bad)])


class SharedReaderTests(AdrFenceTestCase):
    """The fence is tested in exactly one place.

    A second copy of the fence test with a bare `continue` is the construct
    that produced the defect. This pins that the loaders read through the
    shared helper, so editing `FENCE_RE` alone cannot resurrect a silent skip.
    """

    def test_no_generator_carries_its_own_fence_regex(self):
        for script in GENERATORS:
            with self.subTest(script=script):
                src = (SCRIPTS / script).read_text(encoding="utf-8")
                self.assertNotIn(r'"^---\n(.*?)\n---\n"', src,
                                 f"{script} still carries a private copy of the fence regex")

    def test_the_helper_is_the_only_definition(self):
        import adr_frontmatter
        self.assertEqual(adr_frontmatter.FENCE_RE.pattern, r"^---\n(.*?)\n---\n")


class LiveInventoryTests(unittest.TestCase):
    """The repository's real ADR corpus produces no finding.

    A guard calibrated only on its own exemplar cannot see a false positive.
    Skipped where the dogfood tree is absent (a staged artifact carries
    `crux/` without `bionic/`).
    """

    def test_every_real_adr_parses(self):
        adrs = REPO_ROOT / "bionic" / "adrs"
        if not adrs.is_dir():
            self.skipTest("no dogfood ADR tree in this checkout")
        import adr_frontmatter
        paths = adr_frontmatter.adr_paths(adrs)
        self.assertGreaterEqual(len(paths), 100, "ADR corpus unexpectedly small")
        errors = adr_frontmatter.fence_validation_errors(
            paths, lambda p: str(p.relative_to(REPO_ROOT)))
        self.assertEqual(errors, [], f"live ADRs failing the fence check: {errors}")


if __name__ == "__main__":
    unittest.main()
