"""A format-two run does not advance past a closed adr module while its ADR is not accepted.

The actual `start-run.py` and `advance-run.py` run against a synthetic repository. Council
records are synthetic (`_council_gate_support`), so these are controls of the precondition,
not council evidence. Every refusal is checked to leave the run snapshot and the book
byte-identical, and each refusal has a positive control: the same fixture advances once the
ADR is accepted.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _council_gate_support as sup
import council_commit
import implementation_approval as ap
from test_implementation_cycles import book_two

ADR = "docs/adrs/ADR-0200-fixture.md"
OTHER = "docs/adrs/ADR-0300-context.md"
TRANSITION_STEP = ("Accept each ADR marked transition-adr with transition-adr, only if transition-adr "
                   "has not yet run for this module; if it already ran, stop for the owner.")
OWNER_STEP = "Report every entry marked owner to the owner, who restores an unreadable file or decides."


def adr_text(adr_id: str, status: str) -> str:
    return f"---\nid: {adr_id}\nstatus: {status}\n---\n# body\n"


class Fixture:
    """Format-two adr book (prompts 1-4 are adr-1; 5 opens dev-1) closed on a real council record."""

    def __init__(self, root: Path, *, name_adr=True, subjects=None, refutation=False, close=True,
                 status="Proposed", archive=False, spell=None, other_status="Proposed",
                 first_art=None, extra=None):
        self.root = sup.init_repo(root)
        self.book = book_two("adr", formal=False)
        self.book.update(current_run=None, current_prompt=None)
        self.book_path = self.root / "docs/promptbooks/active/PB-0999-fixture.yaml"
        self.run_dir = self.root / "docs/promptbooks/runs/PB-0999-fixture"
        self.run_path = self.run_dir / "run-RUN-001.yaml"
        self.book_path.parent.mkdir(parents=True)
        self.book_path.write_text(yaml.safe_dump(self.book, sort_keys=False))
        (self.root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: docs\n')
        self.adr_rel = ADR.replace("adrs/", "adrs/archive/") if archive else ADR
        self.adr = self.root / self.adr_rel
        self.adr.parent.mkdir(parents=True)
        self.adr.write_text(adr_text("ADR-0200", status))
        (self.root / OTHER).write_text(adr_text("ADR-0300", other_status))
        (self.root / "widget.txt").write_text("not an adr\n")
        for rel, text in (extra or {}).items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text)
        sup.commit_all(self.root, "synthetic initial sources")
        started = subprocess.run([sys.executable, str(sup.SCRIPTS / "start-run.py"), str(self.book_path),
            "--run-id", "RUN-001", "--output", str(self.run_path)], capture_output=True, env=sup.scrubbed_env())
        if started.returncode:
            raise AssertionError(started.stdout + started.stderr)
        self.book.update(current_run="RUN-001", current_prompt=1)
        self.book_path.write_text(yaml.safe_dump(self.book, sort_keys=False))
        env = object.__new__(sup.Env)
        env.root = self.root
        env.hash = sup.vp.compute_book_hash(self.book)
        self.env = env
        self.council = self.run_dir / "council"
        self.council.mkdir()
        if subjects is None:
            subjects = [self.adr_rel]
        refs = [{"path": p, "sha256": self.sha(p), "retained_copy": None} for p in subjects]
        self.receipt = self.council / "round.json"
        if refutation:
            first = env.council_doc(module_tag="adr-1", prompt=2, round=1, subjects=refs,
                decisions=("REQUEST_CHANGES", "APPROVE", "APPROVE"),
                findings={"openai_top": [("F1", "Correctness", False, "blocking")]})
            self.receipt.write_text(json.dumps(first))
            sup.commit_all(self.root, "synthetic blocking round")
            self.refutation = self.council / "refutation.json"
            self.refutation.write_text(json.dumps(env.refutation_doc(
                self.receipt, written_at=sup.ts(5), module_tag="adr-1", council_doc=first)))
            self.deciding = self.refutation
        else:
            self.receipt.write_text(json.dumps(env.council_doc(module_tag="adr-1", prompt=2, subjects=refs)))
            self.deciding = self.receipt
        sup.commit_all(self.root, "synthetic council records")
        spell = spell or (lambda root, path: self.rel(path))
        self.council_arts = ",".join(spell(self.root, p) for p in dict.fromkeys(
            [self.receipt, self.deciding]))
        self.first_art = self.adr_rel if name_adr else "docs/notes/plan.md"
        if first_art is not None:
            # The prompt-1 artifacts, as a list of repo-relative paths or a callable of the root.
            arts = first_art(self.root) if callable(first_art) else first_art
            self.first_art = ",".join(arts)
        if close:
            for n in range(1, 5):
                result = self.advance("done", self.first_art if n == 1 else self.council_arts)
                if result.returncode:
                    raise AssertionError(f"prompt {n}: " + result.stdout.decode() + result.stderr.decode())
                sup.commit_all(self.root, f"synthetic advance {n}")

    def rel(self, path):
        return Path(path).relative_to(self.root).as_posix()

    def sha(self, rel):
        return hashlib.sha256((self.root / rel).read_bytes()).hexdigest()

    def advance(self, outcome, artifacts="", *extra):
        args = ["--outcome", outcome, "--book", str(self.book_path)]
        if artifacts:
            args += ["--artifacts", artifacts]
        return subprocess.run([sys.executable, str(sup.SCRIPTS / "advance-run.py"), str(self.run_path),
            *args, *extra], capture_output=True, env=sup.scrubbed_env())

    def abandon(self):
        return subprocess.run([sys.executable, str(sup.SCRIPTS / "advance-run.py"), str(self.run_path),
            "--abandon", "--reason", "owner abandons", "--book", str(self.book_path)],
            capture_output=True, env=sup.scrubbed_env())

    def gate_info(self, prompt=5):
        proc = subprocess.run([sys.executable, str(sup.SCRIPTS / "advance-run.py"), str(self.run_path),
            "--book", str(self.book_path), "--gate-info", "--prompt", str(prompt)],
            capture_output=True, env=sup.scrubbed_env())
        if proc.returncode:
            raise AssertionError(proc.stdout.decode() + proc.stderr.decode())
        return json.loads(proc.stdout)

    def bytes(self):
        return self.run_path.read_bytes(), self.book_path.read_bytes()

    def set_status(self, status, adr_id="ADR-0200"):
        self.adr.write_text(adr_text(adr_id, status))
        sup.commit_all(self.root, f"synthetic status {status}")


class Base(unittest.TestCase):
    def fixture(self, **kw):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return Fixture(Path(temp.name) / "repo", **kw)

    def refused(self, f, outcome="done", artifacts="x/result.md"):
        before = f.bytes()
        result = f.advance(outcome, artifacts)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(f.bytes(), before)
        return json.loads(result.stdout)


class Precondition(Base):
    def test_done_refuses_while_the_closed_modules_adr_is_proposed(self):
        f = self.fixture()
        out = self.refused(f)
        self.assertIn("adr-1", out["error"])
        self.assertIn(ADR, out["error"])
        self.assertEqual(out["adr_acceptance_pending"], [{"module_tag": "adr-1", "path": ADR,
            "status": "Proposed", "identified_by": "subjects-and-module", "remedy": "transition-adr"}])

    def test_out_of_vocabulary_status_refuses_and_is_reported_verbatim(self):
        # Only Proposed takes transition-adr; a status outside the vocabulary goes to the owner.
        for status, remedy in (("Draft", "owner"), ("Acepted", "owner"), ("Proposed", "transition-adr")):
            with self.subTest(status=status):
                f = self.fixture(status=status)
                entry = self.refused(f)["adr_acceptance_pending"][0]
                self.assertEqual((entry["status"], entry["remedy"]), (status, remedy))

    def test_accepted_deprecated_and_superseded_clear_it(self):
        for status, archive in (("Accepted", False), ("accepted", False), ("Deprecated", False),
                                ("Superseded", True)):
            with self.subTest(status=status, archive=archive):
                f = self.fixture(status=status, archive=archive)
                result = f.advance("done", "x/result.md")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_refusal_message_names_the_remedy_for_each_state(self):
        error = self.refused(self.fixture())["error"]
        for needle in ("not Accepted, Deprecated or Superseded", "unreadable or unidentified",
                       f"adr-1: {ADR}, status Proposed, remedy transition-adr",
                       TRANSITION_STEP, "Otherwise abandon the run.", "Nothing was written."):
            self.assertIn(needle, error)
        self.assertNotIn(OWNER_STEP, error)
        self.assertNotIn("((", error)

    def test_witness_names_the_module_adr_when_no_artifact_does(self):
        f = self.fixture(name_adr=False, subjects=[ADR, OTHER], status="Accepted")
        out = self.refused(f)
        self.assertEqual(out["adr_acceptance_pending"], [{"module_tag": "adr-1", "path": OTHER,
            "status": "Proposed", "identified_by": "subjects", "remedy": "owner"}])
        witness = f.run_dir / council_commit.WITNESS_FILE
        witness.write_text(json.dumps({"entries": [{"prompt": 1, "path": ADR}]}))
        result = f.advance("done", "x/result.md")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_blocked_and_skipped_refuse_too_and_write_nothing(self):
        for outcome in ("blocked", "skipped"):
            with self.subTest(outcome=outcome):
                f = self.fixture()
                self.refused(f, outcome, "x/result.md")

    def test_abandon_stays_open(self):
        f = self.fixture()
        result = f.abandon()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_accepted_adr_lets_the_next_advance_pass(self):
        f = self.fixture()
        self.refused(f)
        f.set_status("Accepted")
        result = f.advance("done", "x/result.md")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_archive_judges_status_not_placement(self):
        passing = self.fixture(status="Superseded", archive=True)
        result = passing.advance("done", "x/result.md")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        refusing = self.fixture(status="Proposed", archive=True)
        self.assertEqual(self.refused(refusing)["adr_acceptance_pending"][0]["status"], "Proposed")

    def test_archived_after_close_is_found_through_the_archive_fallback(self):
        f = self.fixture()
        archived = f.root / "docs/adrs/archive/ADR-0200-fixture.md"
        archived.parent.mkdir()
        f.adr.rename(archived)
        sup.commit_all(f.root, "synthetic archive")
        self.assertEqual(self.refused(f)["adr_acceptance_pending"][0]["status"], "Proposed")
        archived.write_text(adr_text("ADR-0200", "Superseded"))
        sup.commit_all(f.root, "synthetic superseded")
        self.assertEqual(f.advance("done", "x/result.md").returncode, 0)

    def test_unrelated_proposed_subject_the_module_never_named_does_not_refuse(self):
        f = self.fixture(subjects=[ADR, OTHER], status="Accepted")
        result = f.advance("done", "x/result.md")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_symlinked_adr_is_unreadable_and_refuses(self):
        f = self.fixture()
        real = f.root / "docs/elsewhere.md"
        real.write_text(adr_text("ADR-0200", "Accepted"))
        f.adr.unlink()
        f.adr.symlink_to(real)
        sup.commit_all(f.root, "synthetic symlink")
        out = self.refused(f)
        self.assertEqual(out["adr_acceptance_pending"][0]["status"], "unreadable")

    def test_symlinked_ancestor_is_unreadable_and_refuses(self):
        f = self.fixture()
        moved = f.root / "docs/real-adrs"
        (f.root / "docs/adrs").rename(moved)
        (f.root / "docs/adrs").symlink_to(moved, target_is_directory=True)
        sup.commit_all(f.root, "synthetic ancestor symlink")
        self.assertEqual(self.refused(f)["adr_acceptance_pending"][0]["status"], "unreadable")

    def test_missing_adr_is_unreadable_and_refuses(self):
        f = self.fixture()
        f.adr.unlink()
        sup.commit_all(f.root, "synthetic removal")
        self.assertEqual(self.refused(f)["adr_acceptance_pending"][0]["status"], "unreadable")

    def test_module_with_no_adr_subject_is_unidentified_and_refuses(self):
        f = self.fixture(name_adr=False, subjects=["widget.txt"])
        out = self.refused(f)
        self.assertEqual(out["adr_acceptance_pending"], [{"module_tag": "adr-1", "path": None,
            "status": "unidentified", "identified_by": "subjects", "remedy": "owner"}])

    def test_council_artifacts_spelled_as_the_gate_accepts_them_are_read(self):
        # The council gate resolves an attached artifact with `resolve_in_repo`, so a `./` or an
        # absolute spelling closes the module. The precondition reads the same records.
        for name, spell in (("dot", lambda root, path: "./" + path.relative_to(root).as_posix()),
                            ("absolute", lambda root, path: str(path))):
            with self.subTest(spelling=name):
                f = self.fixture(spell=spell, status="Accepted")
                result = f.advance("done", "x/result.md")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                g = self.fixture(spell=spell)
                self.assertEqual(self.refused(g)["adr_acceptance_pending"], [{"module_tag": "adr-1",
                    "path": ADR, "status": "Proposed", "identified_by": "subjects-and-module", "remedy": "transition-adr"}])

    def test_unnamed_module_falls_back_to_every_adr_subject(self):
        f = self.fixture(name_adr=False)
        out = self.refused(f)
        self.assertEqual(out["adr_acceptance_pending"][0]["identified_by"], "subjects")

    def test_refutation_closed_module_refuses_the_same_way(self):
        f = self.fixture(refutation=True)
        out = self.refused(f)
        self.assertEqual(out["adr_acceptance_pending"][0]["path"], ADR)
        f.set_status("Accepted")
        self.assertEqual(f.advance("done", "x/result.md").returncode, 0)

    def test_module_not_yet_closed_is_not_read(self):
        f = self.fixture(close=False)
        self.assertEqual(f.advance("done", f.first_art).returncode, 0)

    def test_unaccepted_fallback_unidentified_and_unreadable_entries_never_instruct_acceptance(self):
        # An entry the module did not identify as its own, or that cannot be read, goes to the
        # owner. Only a Proposed ADR the module itself named takes transition-adr.
        cases = {
            "fallback": self.fixture(name_adr=False),
            "unidentified": self.fixture(name_adr=False, subjects=["widget.txt"]),
        }
        unreadable = self.fixture()
        unreadable.adr.unlink()
        sup.commit_all(unreadable.root, "synthetic removal")
        cases["unreadable"] = unreadable
        for name, f in cases.items():
            with self.subTest(case=name):
                out = self.refused(f)
                self.assertEqual({e["remedy"] for e in out["adr_acceptance_pending"]}, {"owner"})
                self.assertNotIn("transition-adr", out["error"])
                self.assertIn(OWNER_STEP, out["error"])
                self.assertIn("Otherwise abandon the run.", out["error"])

    def test_module_identified_proposed_entry_takes_transition_adr(self):
        out = self.refused(self.fixture())
        self.assertEqual(out["adr_acceptance_pending"][0]["remedy"], "transition-adr")
        self.assertIn("transition-adr", out["error"])

    def test_module_named_adr_outside_the_council_subjects_is_checked(self):
        # The module names ADR-0200 (Proposed) in its artifacts; its council subjects hold only
        # ADR-0300 (Accepted). The named ADR is the one that must clear, and no council record
        # decided it, so it goes to the owner rather than to transition-adr.
        f = self.fixture(subjects=[OTHER], other_status="Accepted")
        out = self.refused(f)
        self.assertEqual(out["adr_acceptance_pending"], [{"module_tag": "adr-1", "path": ADR,
            "status": "Proposed", "identified_by": "module", "remedy": "owner"}])
        self.assertNotIn("transition-adr", out["error"])
        self.assertIn(OWNER_STEP, out["error"])
        f.set_status("Accepted")
        self.assertEqual(f.advance("done", "x/result.md").returncode, 0)

    def test_module_named_adr_that_cannot_be_read_refuses_as_unreadable(self):
        f = self.fixture(subjects=[OTHER], other_status="Accepted")
        f.adr.unlink()
        sup.commit_all(f.root, "synthetic removal")
        entry = self.refused(f)["adr_acceptance_pending"][0]
        self.assertEqual((entry["status"], entry["remedy"]), ("unreadable", "owner"))

    def test_refused_path_is_never_retried_under_the_archive(self):
        # The subject path is a symlink to a Proposed ADR; an Accepted copy sits under archive/.
        f = self.fixture()
        real = f.root / "docs/elsewhere.md"
        real.write_text(adr_text("ADR-0200", "Proposed"))
        f.adr.unlink()
        f.adr.symlink_to(real)
        archived = f.root / "docs/adrs/archive/ADR-0200-fixture.md"
        archived.parent.mkdir()
        archived.write_text(adr_text("ADR-0200", "Accepted"))
        sup.commit_all(f.root, "synthetic symlink with accepted archive copy")
        self.assertEqual(self.refused(f)["adr_acceptance_pending"][0]["status"], "unreadable")


class CouncilDecidedRemedy(Base):
    """`transition-adr` goes only to the one Proposed ADR the module names that its deciding
    record carries as a subject. Every other pending entry goes to the owner."""

    def assert_owner_only(self, out):
        self.assertEqual({e["remedy"] for e in out["adr_acceptance_pending"]}, {"owner"})
        self.assertNotIn("transition-adr", out["error"])
        self.assertIn(OWNER_STEP, out["error"])

    def test_extra_proposed_adr_named_only_by_artifacts_goes_to_the_owner(self):
        # P8: the module's ADR-0200 is Accepted and is the only council subject; the prompt-1
        # artifacts also list ADR-0300, Proposed, which no council record carries.
        f = self.fixture(status="Accepted", first_art=[ADR, OTHER])
        out = self.refused(f)
        self.assertEqual(out["adr_acceptance_pending"], [{"module_tag": "adr-1", "path": OTHER,
            "status": "Proposed", "identified_by": "module", "remedy": "owner"}])
        self.assert_owner_only(out)

    def test_proposed_context_subject_also_named_by_the_module_goes_to_the_owner(self):
        # P4: the module names ADR-0200 (Accepted) and the context ADR-0300 (Proposed); both are
        # council subjects. Two named ADRs were decided, so neither is accepted by instruction.
        f = self.fixture(status="Accepted", subjects=[ADR, OTHER], first_art=[ADR, OTHER])
        out = self.refused(f)
        self.assertEqual(out["adr_acceptance_pending"], [{"module_tag": "adr-1", "path": OTHER,
            "status": "Proposed", "identified_by": "subjects-and-module", "remedy": "owner"}])
        self.assert_owner_only(out)

    def test_every_subject_carrying_the_named_id_is_judged(self):
        # P5: two council subjects carry ADR-0200, the Proposed one first. Both are judged.
        old = "docs/adrs/archive/ADR-0200-old.md"
        f = self.fixture(subjects=[ADR, old], extra={old: adr_text("ADR-0200", "Accepted")})
        out = self.refused(f)
        self.assertEqual(out["adr_acceptance_pending"], [{"module_tag": "adr-1", "path": ADR,
            "status": "Proposed", "identified_by": "subjects-and-module", "remedy": "owner"}])
        self.assert_owner_only(out)
        g = self.fixture(subjects=[old, ADR], extra={old: adr_text("ADR-0200", "Accepted")})
        self.assertEqual(self.refused(g)["adr_acceptance_pending"][0]["path"], ADR)


class NamedAdrPaths(Base):
    """Only an artifact or witness path that resolves to an ADR file under the tree's `adrs/`
    names an ADR. An identifier written anywhere else names nothing."""

    def test_free_text_mention_names_nothing(self):
        f = self.fixture(status="Accepted", first_art=[ADR, "docs/notes/amends-ADR-0100.md"])
        result = f.advance("done", "x/result.md")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(f.gate_info()["adr_acceptance_pending"], [])

    def test_the_adr_path_wins_in_either_artifact_order(self):
        mention = "docs/notes/drafted-ADR-0200.md"
        for name, arts in (("mention first", [mention, ADR]), ("path first", [ADR, mention])):
            with self.subTest(order=name):
                f = self.fixture(first_art=arts)
                self.assertEqual(self.refused(f)["adr_acceptance_pending"], [{"module_tag": "adr-1",
                    "path": ADR, "status": "Proposed", "identified_by": "subjects-and-module",
                    "remedy": "transition-adr"}])
                g = self.fixture(first_art=arts, status="Accepted")
                result = g.advance("done", "x/result.md")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_dot_and_absolute_spellings_name_the_adr(self):
        for name, spell in (("dot", lambda root: ["./" + ADR]), ("absolute", lambda root: [str(root / ADR)])):
            with self.subTest(spelling=name):
                f = self.fixture(first_art=spell)
                self.assertEqual(self.refused(f)["adr_acceptance_pending"], [{"module_tag": "adr-1",
                    "path": ADR, "status": "Proposed", "identified_by": "subjects-and-module",
                    "remedy": "transition-adr"}])
                # S5 N3: the module's Accepted ADR is no council subject and is spelled with `./`
                # or absolutely; it is read, not reported unreadable.
                g = self.fixture(first_art=spell, status="Accepted", subjects=[OTHER], other_status="Accepted")
                result = g.advance("done", "x/result.md")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_named_adr_path_that_does_not_exist_refuses(self):
        missing = "docs/adrs/ADR-0400-missing.md"
        f = self.fixture(status="Accepted", first_art=[missing])
        out = self.refused(f)
        self.assertEqual(out["adr_acceptance_pending"], [{"module_tag": "adr-1", "path": missing,
            "status": "unreadable", "identified_by": "module", "remedy": "owner"}])
        self.assertIn(OWNER_STEP, out["error"])

    def test_adr_path_outside_the_configured_tree_names_nothing(self):
        # An `adrs/` directory outside `<docs_dir>` (a fixture tree) is no ADR of this tree.
        stray = "fixtures/other/adrs/ADR-0500-stray.md"
        f = self.fixture(status="Accepted", first_art=[ADR, stray], extra={stray: adr_text("ADR-0500", "Proposed")})
        result = f.advance("done", "x/result.md")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class GateInfo(Base):
    def test_lists_the_entry_until_acceptance_and_then_nothing(self):
        f = self.fixture()
        self.assertEqual(f.gate_info()["adr_acceptance_pending"], [{"module_tag": "adr-1", "path": ADR,
            "status": "Proposed", "identified_by": "subjects-and-module", "remedy": "transition-adr"}])
        f.set_status("Accepted")
        self.assertEqual(f.gate_info()["adr_acceptance_pending"], [])

    def test_unreadable_entry_is_reported_without_writing(self):
        f = self.fixture()
        f.adr.unlink()
        sup.commit_all(f.root, "synthetic removal")
        before = f.bytes()
        self.assertEqual(f.gate_info()["adr_acceptance_pending"][0]["status"], "unreadable")
        self.assertEqual(f.bytes(), before)

    def test_unidentified_entry_is_reported_without_writing(self):
        f = self.fixture(name_adr=False, subjects=["widget.txt"])
        before = f.bytes()
        self.assertEqual(f.gate_info()["adr_acceptance_pending"][0]["status"], "unidentified")
        self.assertEqual(f.bytes(), before)

    def test_malformed_scalar_subjects_and_entries_do_not_crash_or_pass(self):
        f = self.fixture(name_adr=False, status="Accepted")
        doc = json.loads(f.receipt.read_text())
        doc["subjects"] = 7
        f.receipt.write_text(json.dumps(doc))
        (f.run_dir / council_commit.WITNESS_FILE).write_text(json.dumps({"entries": 5}))
        entry = f.gate_info()["adr_acceptance_pending"]
        self.assertEqual([e["status"] for e in entry], ["unidentified"])
        payload = self.refused(f)
        self.assertEqual([e["status"] for e in payload["adr_acceptance_pending"]], ["unidentified"])

    def test_empty_before_any_module_closes(self):
        self.assertEqual(self.fixture(close=False).gate_info(1)["adr_acceptance_pending"], [])

    def test_format_one_reports_null_and_never_refuses(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = sup.Env(Path(temp.name) / "one", kind="adr", autocommit=True)
        proc = subprocess.run([sys.executable, str(sup.SCRIPTS / "advance-run.py"), str(env.run_path),
            "--book", str(env.book_path), "--gate-info", "--prompt", "1"],
            capture_output=True, env=sup.scrubbed_env())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        info = json.loads(proc.stdout)
        self.assertIn("adr_acceptance_pending", info)
        self.assertIsNone(info["adr_acceptance_pending"])

    def test_format_one_advance_is_not_refused(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = sup.Env(Path(temp.name) / "one", kind="adr", autocommit=True)
        env.set_run(1)
        sup.commit_all(env.root, "prompt 1 running")
        proc = subprocess.run([sys.executable, str(sup.SCRIPTS / "advance-run.py"), str(env.run_path),
            "--book", str(env.book_path), "--outcome", "done"], capture_output=True, env=sup.scrubbed_env())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class ApprovalContractGuard(unittest.TestCase):
    """advance-run.py is outside the approval contract bytes, so this fix leaves them as they were."""

    def test_production_contract_digests_are_unchanged(self):
        digest = lambda v: hashlib.sha256(ap.production_contract_bytes(v)).hexdigest()
        self.assertEqual(digest("3"), "62a4760de1e477fb13c78f9162c49c4da620b5ace304ca72ce6fc45496d16d23")
        self.assertEqual(digest("4"), "b1bda66c8667b1c9a8bbaa39c47d58fd55245b718e6d025f71f7221d47035167")


if __name__ == "__main__":
    unittest.main()
