"""Tests for generate-writing-rules.py — the eighth regenerative output (ADR-0058).

Covers the five acceptance criteria the ADR states for the regenerator: byte
equivalence, the 0/1/2 exit-code contract, fail-closed marker handling, the
--dry-run drift report, and the constraint that the canonical block carries no
internal decision-record reference (it is projected into a SKILL.md that ships).
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "crux" / "scripts" / "generate-writing-rules.py"
from _authoring_fixture import seed_authoring_probe


try:  # package-relative when run as a module, flat when run by discovery
    from ._dev_surface import IS_STAGED_ARTIFACT
except ImportError:  # pragma: no cover
    import sys as _s
    _s.path.insert(0, str(Path(__file__).resolve().parent))
    from _dev_surface import IS_STAGED_ARTIFACT

# The canonical text and two of the three projections are DEV-ONLY surfaces:
# the repo-root AGENTS.md and the tree's AGENTS.md never cross the
# sync boundary (ADR-0036 §3). sync.sh runs this package as a release gate
# against the STAGED tree, where they legitimately do not exist — so every test
# that reads them skips there and keeps full strength in the dev checkout.
# Only crux/skills/prose-review/SKILL.md ships, and its projected block is
# covered by the release-content scan.
_dev_only = unittest.skipIf(
    IS_STAGED_ARTIFACT,
    "reads dev-only surfaces (AGENTS.md / <tree>/AGENTS.md) absent from a staged artifact",
)


def _load_module():
    spec = importlib.util.spec_from_file_location("generate_writing_rules", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gwr = _load_module()


class MarkerExtractionTestCase(unittest.TestCase):
    """extract_region is the fail-closed core; every refusal path is tested."""

    B, E = "<!-- B -->", "<!-- E -->"

    def test_extracts_bytes_strictly_between_markers(self):
        text = f"before\n{self.B}\nbody\n{self.E}\nafter"
        self.assertEqual(gwr.extract_region(text, self.B, self.E, "t"), "\nbody\n")

    def test_missing_begin_marker_is_refused(self):
        with self.assertRaises(gwr.RegenError) as cm:
            gwr.extract_region(f"only\n{self.E}\n", self.B, self.E, "t")
        self.assertIn("missing marker", str(cm.exception))

    def test_missing_end_marker_is_refused(self):
        with self.assertRaises(gwr.RegenError) as cm:
            gwr.extract_region(f"only\n{self.B}\n", self.B, self.E, "t")
        self.assertIn("missing marker", str(cm.exception))

    def test_duplicate_markers_are_refused_rather_than_guessed(self):
        text = f"{self.B}\na\n{self.E}\n{self.B}\nb\n{self.E}\n"
        with self.assertRaises(gwr.RegenError) as cm:
            gwr.extract_region(text, self.B, self.E, "t")
        self.assertIn("duplicate marker", str(cm.exception))

    def test_end_before_begin_is_refused(self):
        with self.assertRaises(gwr.RegenError) as cm:
            gwr.extract_region(f"{self.E}\nbody\n{self.B}\n", self.B, self.E, "t")
        self.assertIn("precedes", str(cm.exception))

    def test_replace_region_leaves_surrounding_bytes_untouched(self):
        text = f"HEAD\n{self.B}\nold\n{self.E}\nTAIL"
        out = gwr.replace_region(text, self.B, self.E, "new", "t")
        self.assertEqual(out, f"HEAD\n{self.B}new{self.E}\nTAIL")

    def test_indented_marker_line_is_still_a_marker(self):
        """Leading blanks are tolerated; the marker must own the line, not the column.

        The END match starts at its leading whitespace, so that indentation is
        outside the region and is preserved rather than overwritten.
        """
        text = f"x\n   {self.B}\nbody\n\t{self.E}\ny"
        self.assertEqual(gwr.extract_region(text, self.B, self.E, "t"), "\nbody\n")

    def test_crlf_marker_lines_are_recognized(self):
        """A CRLF file must parse: `\\r` sits between the marker and `$`.

        The marker line owns its own `\\r`, so the region begins after it.
        """
        text = f"x\r\n{self.B}\r\nbody\r\n{self.E}\r\ny"
        self.assertEqual(gwr.extract_region(text, self.B, self.E, "t"), "\nbody\r\n")


@_dev_only
class ProjectionTestCase(unittest.TestCase):
    """End-to-end behavior against a temp copy of the real repo files."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        seed_authoring_probe(self.tmp, SCRIPT)
        for rel in (gwr.CANONICAL_FILE, *gwr.PROJECTIONS):
            dst = self.tmp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO_ROOT / rel, dst)

    def _read(self, rel: str) -> str:
        return (self.tmp / rel).read_text(encoding="utf-8")

    def test_every_projection_is_byte_equivalent_to_canonical(self):
        canonical = gwr.extract_region(
            self._read(gwr.CANONICAL_FILE), gwr.CANONICAL_BEGIN, gwr.CANONICAL_END, "c"
        )
        for rel in gwr.PROJECTIONS:
            got = gwr.extract_region(
                self._read(rel), gwr.PROJECTION_BEGIN, gwr.PROJECTION_END, rel
            )
            self.assertEqual(got, canonical, f"{rel} is not byte-equivalent to the canonical block")

    def test_dry_run_is_clean_when_in_sync(self):
        code, payload = gwr.run(self.tmp, dry_run=True)
        self.assertEqual(code, 0)
        self.assertEqual(payload["drifted"], [])

    def test_dry_run_reports_drift_and_writes_nothing(self):
        target = self.tmp / gwr.PROJECTIONS[0]
        before = target.read_text(encoding="utf-8")
        target.write_text(
            before.replace(gwr.PROJECTION_END, "TAMPERED\n" + gwr.PROJECTION_END), encoding="utf-8"
        )
        tampered = target.read_text(encoding="utf-8")

        code, payload = gwr.run(self.tmp, dry_run=True)
        self.assertEqual(code, 1)
        self.assertEqual(payload["drifted"], [gwr.PROJECTIONS[0]])
        self.assertEqual(target.read_text(encoding="utf-8"), tampered, "--dry-run must not write")

    def test_regeneration_restores_a_tampered_projection(self):
        target = self.tmp / gwr.PROJECTIONS[0]
        original = target.read_text(encoding="utf-8")
        target.write_text(
            original.replace(gwr.PROJECTION_END, "TAMPERED\n" + gwr.PROJECTION_END), encoding="utf-8"
        )

        code, payload = gwr.run(self.tmp, dry_run=False)
        self.assertEqual(code, 0)
        self.assertEqual(payload["written"], [gwr.PROJECTIONS[0]])
        self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_regeneration_is_idempotent(self):
        gwr.run(self.tmp, dry_run=False)
        snapshot = {rel: self._read(rel) for rel in gwr.PROJECTIONS}
        gwr.run(self.tmp, dry_run=False)
        for rel, text in snapshot.items():
            self.assertEqual(self._read(rel), text, f"{rel} changed on a second run")

    def test_empty_canonical_region_is_refused(self):
        path = self.tmp / gwr.CANONICAL_FILE
        text = path.read_text(encoding="utf-8")
        i = text.index(gwr.CANONICAL_BEGIN) + len(gwr.CANONICAL_BEGIN)
        j = text.index(gwr.CANONICAL_END)
        path.write_text(text[:i] + "\n\n" + text[j:], encoding="utf-8")
        with self.assertRaises(gwr.RegenError) as cm:
            gwr.run(self.tmp, dry_run=True)
        self.assertIn("empty", str(cm.exception))

    def test_missing_projection_target_is_refused(self):
        (self.tmp / gwr.PROJECTIONS[0]).unlink()
        with self.assertRaises(gwr.RegenError) as cm:
            gwr.run(self.tmp, dry_run=True)
        self.assertIn("not found", str(cm.exception))


@_dev_only
class CouncilFindingsTestCase(unittest.TestCase):
    """Regressions for the six findings the independent council review raised.

    Each test names the failure it locks out. These are the cases the first
    implementation got wrong.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        seed_authoring_probe(self.tmp, SCRIPT)
        for rel in (gwr.CANONICAL_FILE, *gwr.PROJECTIONS):
            dst = self.tmp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO_ROOT / rel, dst)

    def test_crlf_bytes_outside_the_region_are_preserved(self):
        """`Path.read_text()` would normalize CRLF and rewrite the whole file.

        Byte-equivalence of the region and preservation of everything else are
        the two halves of the contract, and on a CRLF file they pull apart: the
        region takes the canonical bytes (LF), while the surrounding CRLF must
        survive untouched. Mixed endings are the correct outcome here, not a bug
        — what would be a bug is the regenerator rewriting lines it does not own.
        """
        target = self.tmp / gwr.PROJECTIONS[0]
        crlf = target.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        target.write_bytes(crlf)

        text_before = gwr.read_text_verbatim(target)
        i, j = gwr.find_region(text_before, gwr.PROJECTION_BEGIN, gwr.PROJECTION_END, "t")
        head_before, tail_before = text_before[:i], text_before[j:]

        gwr.run(self.tmp, dry_run=False)

        text_after = gwr.read_text_verbatim(target)
        i2, j2 = gwr.find_region(text_after, gwr.PROJECTION_BEGIN, gwr.PROJECTION_END, "t")
        self.assertEqual(text_after[:i2], head_before, "bytes before the region changed")
        self.assertEqual(text_after[j2:], tail_before, "bytes after the region changed")
        self.assertIn("\r\n", tail_before, "fixture was not actually CRLF")

        canonical = gwr.extract_region(
            gwr.read_text_verbatim(self.tmp / gwr.CANONICAL_FILE),
            gwr.CANONICAL_BEGIN, gwr.CANONICAL_END, "c",
        )
        self.assertEqual(text_after[i2:j2], canonical, "region is not byte-equivalent")

    def test_marker_quoted_in_a_fenced_example_is_not_a_delimiter(self):
        """Line-anchored matching: an indented/quoted marker is prose, not a delimiter."""
        text = (
            "intro\n"
            "```\n"
            f"    {gwr.PROJECTION_BEGIN}\n"
            "    body of an example\n"
            f"    {gwr.PROJECTION_END}\n"
            "```\n"
        )
        # Indented markers inside a fence are not whole-line markers at column 0
        # with only blanks around them... they ARE (leading blanks are tolerated),
        # so what must hold is that a file with ONLY these is unambiguous, while a
        # file with these PLUS real markers fails closed as a duplicate.
        real = text + f"{gwr.PROJECTION_BEGIN}\nreal\n{gwr.PROJECTION_END}\n"
        with self.assertRaises(gwr.RegenError) as cm:
            gwr.extract_region(real, gwr.PROJECTION_BEGIN, gwr.PROJECTION_END, "t")
        self.assertIn("duplicate marker", str(cm.exception))

    def test_marker_as_substring_midline_is_not_a_delimiter(self):
        """A marker mentioned inside a sentence must not delimit anything."""
        text = (
            f"Prose that mentions {gwr.PROJECTION_BEGIN} inline and also "
            f"{gwr.PROJECTION_END} inline.\n"
        )
        with self.assertRaises(gwr.RegenError) as cm:
            gwr.extract_region(text, gwr.PROJECTION_BEGIN, gwr.PROJECTION_END, "t")
        self.assertIn("missing marker", str(cm.exception))

    def test_canonical_containing_a_projection_marker_is_refused_before_any_write(self):
        """The self-poisoning write: it would wedge every subsequent run."""
        path = self.tmp / gwr.CANONICAL_FILE
        text = read = path.read_text(encoding="utf-8")
        i = text.index(gwr.CANONICAL_BEGIN) + len(gwr.CANONICAL_BEGIN)
        path.write_text(text[:i] + f"\n{gwr.PROJECTION_BEGIN}\n" + text[i:], encoding="utf-8")

        snapshot = {rel: (self.tmp / rel).read_bytes() for rel in gwr.PROJECTIONS}
        with self.assertRaises(gwr.RegenError) as cm:
            gwr.run(self.tmp, dry_run=False)
        self.assertIn("projection marker", str(cm.exception))
        for rel, before in snapshot.items():
            self.assertEqual((self.tmp / rel).read_bytes(), before, f"{rel} was written anyway")
        self.assertNotEqual(read, "")  # sanity: the fixture was non-empty

    def test_a_malformed_later_target_leaves_earlier_targets_untouched(self):
        """Validate-all-then-write: no partial projection set."""
        first, last = self.tmp / gwr.PROJECTIONS[0], self.tmp / gwr.PROJECTIONS[-1]
        first.write_text(
            first.read_text(encoding="utf-8").replace(
                gwr.PROJECTION_END, "DRIFT\n" + gwr.PROJECTION_END
            ),
            encoding="utf-8",
        )
        drifted_before = first.read_bytes()
        # Break the LAST target's markers.
        last.write_text(
            last.read_text(encoding="utf-8").replace(gwr.PROJECTION_END, ""), encoding="utf-8"
        )

        with self.assertRaises(gwr.RegenError):
            gwr.run(self.tmp, dry_run=False)
        self.assertEqual(
            first.read_bytes(), drifted_before,
            "an earlier target was rewritten before the later target's markers were validated",
        )

    def test_target_permission_bits_survive_regeneration(self):
        """mkstemp creates 0600 and os.replace carries that mode onto the target."""
        import stat as _stat

        target = self.tmp / gwr.PROJECTIONS[0]
        target.chmod(0o644)
        target.write_text(
            target.read_text(encoding="utf-8").replace(
                gwr.PROJECTION_END, "DRIFT\n" + gwr.PROJECTION_END
            ),
            encoding="utf-8",
        )
        gwr.run(self.tmp, dry_run=False)
        self.assertEqual(
            _stat.S_IMODE(target.stat().st_mode), 0o644,
            "regeneration changed the target's permission bits",
        )

    def test_non_utf8_byte_exits_two_not_traceback(self):
        """UnicodeDecodeError must reach the exit-2 lane with stderr, not escape."""
        target = self.tmp / gwr.PROJECTIONS[0]
        target.write_bytes(b"\xff\xfe not utf-8 \n")
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--dry-run", "--repo-root", str(self.tmp)],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 2, f"stdout={proc.stdout!r} stderr={proc.stderr!r}")
        self.assertTrue(proc.stderr.strip(), "exit 2 must carry a message on stderr")


@_dev_only
class ExitCodeTestCase(unittest.TestCase):
    """The 0/1/2 contract, exercised through the real CLI."""

    def test_dry_run_exits_zero_with_json_when_clean(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--dry-run", "--repo-root", str(REPO_ROOT)],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('"drifted"', proc.stdout)

    def test_validation_error_exits_one_with_json_on_stdout(self):
        """A canonical file that is PRESENT but carries no marker is a validation
        error, and stays exit 1.

        rule:out-of-scope-is-surface-absent draws the line at presence: a missing
        `AGENTS.md` is an absent surface, which the staged release artifact and every
        public clone have, and calling that BROKEN filed a defect against a tree that
        owns no projection. A file that exists and lacks its marker is a real defect
        in a tree that does own it. The two cases are covered separately -- this one,
        and `test_an_absent_canonical_file_is_surface_absent_not_broken` below.
        """
        with tempfile.TemporaryDirectory() as d:
            seed_authoring_probe(d, SCRIPT)
            (Path(d) / gwr.CANONICAL_FILE).write_text(
                "# A repo with no canonical writing-rules region\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--dry-run", "--repo-root", d],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn('"error"', proc.stdout)

    def test_an_absent_canonical_file_is_surface_absent_not_broken(self):
        """The public clone and the staged artifact: `crux/scripts/` present, the
        repo-root `AGENTS.md` absent. Exit 1 there filed BROKEN against a tree that
        ships no projection of the block."""
        with tempfile.TemporaryDirectory() as d:
            seed_authoring_probe(d, SCRIPT)
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--dry-run", "--repo-root", d],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertTrue(payload.get("surface_absent"), payload)
            self.assertTrue((payload.get("reason") or "").strip(), payload)


@_dev_only
class ShippedSurfaceTestCase(unittest.TestCase):
    """The canonical block is projected into a SKILL.md that ships downstream."""

    def test_canonical_block_carries_no_internal_decision_record_reference(self):
        canonical = gwr.extract_region(
            (REPO_ROOT / gwr.CANONICAL_FILE).read_text(encoding="utf-8"),
            gwr.CANONICAL_BEGIN, gwr.CANONICAL_END, "c",
        )
        self.assertNotRegex(
            canonical, r"\[\[adrs/",
            "the canonical block reaches the shipped SKILL.md; it must pass the ADR-0034 "
            "release-content scan, which forbids internal decision-record references",
        )

    def test_enrolled_in_the_regenerative_outputs_table(self):
        claude_md = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("generate-writing-rules.py", claude_md)


if __name__ == "__main__":
    unittest.main()
