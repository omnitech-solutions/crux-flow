"""[SECURITY:S5] The observations-root containment guard on the three READ
siblings that still built `<tree>/observations` by hand.

`summaries_projection.observations_root(tree)` is the ONE resolution of the
concern directory: it proves `<tree>/observations` resolves to a path inside
the tree, and two writers (`signoff-survey.py`, `scaffold-survey-sheet.py`)
already route through it. Three readers did not:

  * `check_observations.check()` — `obs_dir = root / docs_dir / "observations"`,
    steering the whole CHK-OBS rule set at whatever the link names;
  * `survey.snapshot_boundary()` — the writer-boundary snapshot, measured
    against the outside directory;
  * `survey.phase_mine()` — `read_recorded_observations(tree / "observations")`.

A RELATIVE symlink at `<docs_dir>/observations` is git-carryable, so it
travels in a pull request. Every refusal case here plants exactly that link.

Each site carries three cases: the refusal (link OUT of the repository), a
negative control (link landing INSIDE the tree — contained, and read), and an
ordinary-directory control (no link at all). The refusal cases assert a
message discriminator, never a bare exit code — this repository has been
bitten by "exit 2 either way" before — and their failure message names the
outside content that reached the call site's result, so a regression prints
the leak rather than "exception not raised".

`check_observations` refuses in the exit-2 capability lane, not in `broken`:
every `broken`/`warning` string carries a documented CHK-OBS-* rule id, and
this refusal is a refusal to read the concern AT ALL. The two `survey` sites
raise `SurveyError`, the existing exit-2 lane. Both CLIs are exercised, not
only the private helpers.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
CHECK_OBS = SCRIPTS / "check_observations.py"
SURVEY = SCRIPTS / "survey.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False

if HAVE_YAML:
    CO = importlib.import_module("check_observations")
    SV = importlib.import_module("survey")

# The sentence `observations_root` raises with. Its sibling containment legs
# one level down use the same words, so this is the discriminator every
# refusal case asserts on.
REFUSAL = "does not resolve to a directory inside the tree"

MANIFEST = """schema_version: "5"
concerns_enabled:
  - adrs
  - observations
adr:
  next_number: 100
observation:
  next_number: 10
"""

# A ratified record with the §17.1 field set. Its evidence path names a file
# absent from the fixture repo, so an unguarded `check()` reports a
# CHK-OBS-EVIDENCE finding ON THE OUTSIDE RECORD — the leak, visible in the
# checker's own output.
RECORD = """---
id: OBS-0001
title: The code does X.
status: ratified
date: 2026-08-02
observed_date: 2026-08-01
ratified_date: 2026-08-02
rejected_date: null
retired_date: null
decided_date: null
provenance: recovered
decided_by: null
evidence:
  - crux/scripts/summaries_projection.py:1-10
anchor_id: aaaaaaaaaaaaaaaa
related_invariants: []
tags:
  - test
governs:
  - domain: d
    rule: the observed rule
    scope: crux/scripts
    handle: OBS-0001/what-it-does
---

# OBS-0001
"""
RECORD_NAME = "OBS-0001-x.md"


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class _LinkedConcernCase(unittest.TestCase):
    """A repo whose tree has NO `observations/` yet, and an outside directory
    holding one record. Each test decides what `observations` is."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        home = Path(self._tmp.name).resolve()
        self.root = home / "repo"
        self.tree = self.root / "bionic"
        self.obs = self.tree / "observations"
        (self.tree / "adrs").mkdir(parents=True)
        (self.root / ".bionic.yml").write_text(
            'config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8")
        (self.tree / "manifest.yml").write_text(MANIFEST, encoding="utf-8")
        (self.tree / "log.md").write_text("# Log\n", encoding="utf-8")
        self.outside = home / "private-observations"
        self.outside.mkdir()
        (self.outside / RECORD_NAME).write_text(RECORD, encoding="utf-8")

    # ── the three shapes of `observations` ────────────────────────────────

    def _link_concern(self, target: Path) -> None:
        """A RELATIVE link, because that is the one a commit carries."""
        rel = os.path.relpath(target, self.obs.parent)
        self.obs.symlink_to(rel, target_is_directory=True)
        assert not os.path.isabs(os.readlink(self.obs))

    def link_out(self) -> None:
        self._link_concern(self.outside)

    def link_in(self) -> Path:
        inside = self.tree / "real-observations"
        inside.mkdir()
        (inside / RECORD_NAME).write_text(RECORD, encoding="utf-8")
        self._link_concern(inside)
        return inside

    def plain_dir(self) -> None:
        self.obs.mkdir()
        (self.obs / RECORD_NAME).write_text(RECORD, encoding="utf-8")

    # ── the refusal assertion, leak-naming on failure ─────────────────────

    def expect_refusal(self, call, exc_type, describe_leak):
        """`call()` must raise `exc_type()` carrying REFUSAL. When it returns
        instead, fail with what the call read from the outside directory.

        `exc_type` is a zero-arg callable rather than the class itself so an
        UNGUARDED site fails on the leak it produced, not on the guard class
        not existing yet."""
        try:
            result = call()
        except Exception as exc:  # noqa: BLE001 — classified just below
            self.assertIsInstance(exc, exc_type(), repr(exc))
            self.assertIn(REFUSAL, str(exc))
            self.assertIn("observations", str(exc))
            return exc
        self.fail("read the outside directory instead of refusing: "
                  + describe_leak(result))


# ── site 1: check_observations.check() ─────────────────────────────────────

class CheckObservationsRootTests(_LinkedConcernCase):

    @staticmethod
    def _leak(result: dict) -> str:
        return (f"records={result['records']} concern_enabled="
                f"{result['concern_enabled']} broken={result['broken']}")

    def test_a_concern_linked_out_of_the_repo_is_refused(self):
        self.link_out()
        self.expect_refusal(lambda: CO.check(self.root),
                            lambda: CO.ObservationsRefusal, self._leak)

    def test_the_refusal_is_a_capability_error_not_a_finding(self):
        """The lane: `ObservationsRefusal` is a RuntimeError so `main()`'s
        exit-2 tuple admits it, and it is never a CHK-OBS-* finding string."""
        self.link_out()
        exc = self.expect_refusal(lambda: CO.check(self.root),
                                  lambda: CO.ObservationsRefusal, self._leak)
        self.assertIsInstance(exc, RuntimeError)
        self.assertNotIn("CHK-OBS", str(exc))

    def test_a_disabled_concern_stays_silent_even_when_linked_out(self):
        """Ordering: the concerns_enabled check comes BEFORE the root is
        resolved, so a disabled concern never raises — the link is never
        followed."""
        self.link_out()
        (self.tree / "manifest.yml").write_text(
            'schema_version: "5"\nconcerns_enabled:\n  - adrs\n',
            encoding="utf-8")
        result = CO.check(self.root)
        self.assertFalse(result["concern_enabled"])
        self.assertEqual(result["records"], 0)

    def test_a_link_landing_inside_the_tree_stays_admissible(self):
        """Negative control: containment is a property of the RESOLVED path,
        never of being a link. The record behind the in-tree link IS read."""
        self.link_in()
        result = CO.check(self.root)
        self.assertTrue(result["concern_enabled"])
        self.assertEqual(result["records"], 1, result)

    def test_an_ordinary_concern_directory_is_unaffected(self):
        self.plain_dir()
        result = CO.check(self.root)
        self.assertTrue(result["concern_enabled"])
        self.assertEqual(result["records"], 1, result)

    # ── the shipped entry point ───────────────────────────────────────────

    def _cli(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CHECK_OBS), "--root", str(self.root)],
            capture_output=True, text=True)

    def test_the_cli_exits_2_with_the_refusal_on_stderr(self):
        self.link_out()
        proc = self._cli()
        self.assertEqual(
            proc.returncode, 2,
            f"the CLI reported on the outside record instead of refusing: "
            f"exit={proc.returncode} stdout={proc.stdout}")
        self.assertIn(REFUSAL, proc.stderr)
        self.assertIn("check_observations:", proc.stderr)
        self.assertEqual(proc.stdout, "", "a refusal emits no payload")
        self.assertNotIn(RECORD_NAME, proc.stdout + proc.stderr,
                         "the refusal quoted a byte of the outside directory")

    def test_the_cli_reads_an_in_tree_link(self):
        """Positive control for the CLI: the same invocation, against a
        contained concern, parses the record and reports it."""
        self.link_in()
        proc = self._cli()
        self.assertNotEqual(proc.returncode, 2, proc.stderr)
        self.assertIn('"records": 1', proc.stdout)
        self.assertNotIn(REFUSAL, proc.stderr)


# ── site 2: survey.snapshot_boundary() ─────────────────────────────────────

class SnapshotBoundaryRootTests(_LinkedConcernCase):

    @staticmethod
    def _leak(snap) -> str:
        obs, _adrs = snap
        return "snapshotted outside files " + ", ".join(
            f"{name}={body[:24]!r}" for name, body in sorted(obs.items()))

    def test_a_concern_linked_out_of_the_repo_is_refused(self):
        self.link_out()
        self.expect_refusal(lambda: SV.snapshot_boundary(self.tree),
                            lambda: SV.SurveyError, self._leak)

    def test_a_link_landing_inside_the_tree_stays_admissible(self):
        self.link_in()
        obs, _adrs = SV.snapshot_boundary(self.tree)
        self.assertEqual(sorted(obs), [RECORD_NAME])
        self.assertEqual(obs[RECORD_NAME], RECORD.encode("utf-8"))

    def test_an_ordinary_concern_directory_is_unaffected(self):
        self.plain_dir()
        obs, _adrs = SV.snapshot_boundary(self.tree)
        self.assertEqual(sorted(obs), [RECORD_NAME])

    def test_an_absent_concern_directory_is_an_empty_snapshot(self):
        """`snapshot_files` already treated an absent directory as empty; the
        guard must not turn "absent" into a refusal."""
        obs, _adrs = SV.snapshot_boundary(self.tree)
        self.assertEqual(obs, {})


# ── site 3: survey.phase_mine() ────────────────────────────────────────────

class PhaseMineRootTests(_LinkedConcernCase):

    @staticmethod
    def _leak(out) -> str:
        payload, code = out
        return (f"recorded_observations={payload['recorded_observations']} "
                f"exit={code}")

    def test_a_concern_linked_out_of_the_repo_is_refused(self):
        self.link_out()
        self.expect_refusal(
            lambda: SV.phase_mine(self.root, "bionic", "bionic"),
            lambda: SV.SurveyError, self._leak)

    def test_a_link_landing_inside_the_tree_stays_admissible(self):
        self.link_in()
        payload, _code = SV.phase_mine(self.root, "bionic", "bionic")
        self.assertEqual(payload["recorded_observations"], 1, payload)

    def test_an_ordinary_concern_directory_is_unaffected(self):
        self.plain_dir()
        payload, _code = SV.phase_mine(self.root, "bionic", "bionic")
        self.assertEqual(payload["recorded_observations"], 1, payload)

    # ── the shipped entry point ───────────────────────────────────────────

    def _cli(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SURVEY), "--repo-root", str(self.root),
             "--phase", "mine"], capture_output=True, text=True)

    def test_the_cli_exits_2_with_the_refusal_on_stderr(self):
        self.link_out()
        proc = self._cli()
        self.assertEqual(
            proc.returncode, 2,
            f"--phase mine counted the outside record instead of refusing: "
            f"exit={proc.returncode} stdout={proc.stdout}")
        self.assertIn(REFUSAL, proc.stderr)
        self.assertTrue(proc.stderr.startswith("survey: "), proc.stderr)
        self.assertEqual(proc.stdout, "", "a refusal emits no payload")
        self.assertNotIn(RECORD_NAME, proc.stdout + proc.stderr,
                         "the refusal quoted a byte of the outside directory")

    def test_the_cli_reads_an_in_tree_link(self):
        """Positive control for the CLI, and "exit 2 either way" insurance:
        the contained concern is read and the payload counts its record."""
        self.link_in()
        proc = self._cli()
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        self.assertIn('"recorded_observations": 1', proc.stdout)
        self.assertNotIn(REFUSAL, proc.stderr)


if __name__ == "__main__":
    unittest.main()
