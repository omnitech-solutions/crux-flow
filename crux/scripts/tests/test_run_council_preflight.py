"""The council runner's preflight: the question and subject checks before the claim.

Each row of the classification table is driven through `main()` (in-process, a counting mock
transport, isolated git configuration; see `test_run_council_claim._Harness`). A retryable cause
returns a structured refusal and writes no council record; a cause outside the conductor's
authority writes a committed `preflight-needs-owner` record; the third retryable refusal at one
prompt since its last claim writes a committed `preflight-retries-spent` record. Both records are
format 2, name no attempt and carry the byte-exact seal.

The run's own earlier work is modelled as the behavioural tests model it: the fixture commit is
the run's `base_commit`, and a setup commit after it changes the subject, so the subject path
changed in base_commit..HEAD.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _council_gate_support as sup  # noqa: E402
import test_run_council_claim as h  # noqa: E402  (module import: its test classes are not re-collected here)

import council_records as cr  # noqa: E402

PROMPT = h.PROMPT
SUBJECT = sup.SUBJECT
WITNESS_SCHEMA = h.SCRIPTS.parent / "schemas" / "run-work-witness.schema.json"


class _Preflight(h._Harness):

    def write_witness(self, entries: list[tuple[str, bytes]]) -> None:
        doc = {"record_type": "run-work-witness", "format_version": "1",
               "book": {"id": sup.BOOK_ID, "content_hash": self.env.hash}, "run_id": sup.RUN_ID,
               "entries": [{"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "prompt": 2,
                            "written_at": "2026-10-02T12:00:00.000001Z"} for rel, data in entries]}
        errors: list = []
        sup.vp.validate(doc, sup.vp.load_schema(WITNESS_SCHEMA), "#", "#", errors, "run-work-witness")
        self.assertEqual(errors, [])
        (self.env.run_dir / "run-work-witness.json").write_text(json.dumps(doc, indent=2) + "\n")

    def assert_refusal(self, causes, argv=None, at_prompt=1) -> dict:
        """Exit 1, a structured preflight refusal with `causes` [(name, code, repair)], no request,
        nothing under council/, HEAD unchanged, and one diagnostics line naming codes and fields."""
        before, lines = self.head(), len(self.diagnostics())
        rc, out, err, gw = self.run_main(argv)
        self.assertEqual((rc, gw.requests), (1, []), out + err)
        body = json.loads(out)
        self.assertEqual((body["refused"], body["record"], body["bound"], body["refusals_at_prompt"]),
                         ("preflight", None, 3, at_prompt), body)
        self.assertEqual([(c["name"], c["code"], c["repair"]) for c in body["causes"]], causes)
        self.assertEqual((self.records(), self.attempts(), self.head()), ([], [], before))
        diag = self.diagnostics()
        self.assertEqual(len(diag), lines + 1)
        self.assertEqual((diag[-1]["event"], diag[-1]["prompt"], diag[-1]["round"], diag[-1]["codes"],
                          diag[-1]["fields"]),
                         ("refusal", PROMPT, 1, [c[1] for c in causes], [c[0] for c in causes]))
        return body

    def assert_owner(self, names, argv=None) -> dict:
        """Exit 1 and exactly one new commit holding exactly one format-2, sealed
        `preflight-needs-owner` record that names no attempt, then one diagnostics commit holding
        only the run directory's files that existed (the witness; the log holds no line, because an
        owner stop is not counted); no request, no claim, no count."""
        before, lines = self.head(), self.diagnostics()
        witness = self.env.rel(self.env.run_dir) + "/run-work-witness.json"
        witnessed = (self.env.run_dir / "run-work-witness.json").is_file()
        rc, out, err, gw = self.run_main(argv)
        self.assertEqual((rc, gw.requests), (1, []), out + err)
        summary = json.loads(out)
        self.assertNotIn("refused", summary)
        recs = self.records()
        self.assertEqual(len(recs), 1)
        self.assertEqual((summary["record"], summary["attempt"]), (recs[0], None))
        data = (self.root / recs[0]).read_bytes()
        doc = json.loads(data)
        self.assertEqual((doc["format_version"], doc["outcome"], doc["attempt"], doc["refusal_reason"]),
                         ("2", "could-not-run", None, {"code": "preflight-needs-owner", "names": names}))
        self.assertTrue(cr.seal_holds(data))
        self.assertEqual(self.head_blob(recs[0]), data)
        expected = [(f"crux council preflight: {sup.BOOK_ID} {sup.RUN_ID} prompt 3 round 1", [recs[0]])]
        if witnessed:
            expected.append((f"crux council diagnostics: {sup.BOOK_ID} {sup.RUN_ID} prompt 3", [witness]))
        self.assertEqual(self.commits_since(before), expected)
        self.assertEqual((self.attempts(), self.pending(), self.diagnostics()), ([], [], lines))
        return doc


class RetypeTests(_Preflight):
    """Paths the conductor repairs by retyping: each a refusal, no record, no commit."""

    def test_a_missing_subject(self):
        self.assert_refusal([("subjects[0].path", "missing", "retype")], self.argv(subjects=[self.root / h.TYPO]))
        self.assertNotIn("fixtur", (self.env.run_dir / "council-preflight.jsonl").read_text())
        self.ran(1)  # positive control: the retyped path runs, with no owner stop

    def test_a_question_outside_the_repository(self):
        outside = self.tmp / "elsewhere.md"
        outside.write_text("q\n")
        self.assert_refusal([("question.path", "outside-repo", "retype")],
                            [str(self.env.run_path), "--prompt", "3", "--round", "1", "--question",
                             str(outside), "--subject", str(self.env.subject)])

    def test_a_symlinked_subject(self):
        link = self.root / "docs" / "s-link.md"
        link.symlink_to(self.env.subject)
        self.assert_refusal([("subjects[0].path", "symlink", "retype")], self.argv(subjects=[link]))

    def test_a_subject_name_with_a_control_character(self):
        odd = self.root / "docs" / "bad\nname.md"
        odd.write_text("x\n")
        self.assert_refusal([("subjects[0].path", "control-character", "retype")], self.argv(subjects=[odd]))

    def test_a_subject_that_is_not_utf8(self):
        blob = self.root / "docs" / "blob.bin"
        blob.write_bytes(b"\xff\xfe\x00bad")
        sup.commit_all(self.root, "blob")
        self.assert_refusal([("subjects[0].path", "not-utf8", "retype")], self.argv(subjects=[blob]))


class RunWorkTests(_Preflight):
    """A subject the run wrote and left uncommitted, witnessed byte for byte: commit-run-work."""

    V2 = b"# subject v2, written by the run at prompt 2\n"

    def test_an_unstaged_subject_whose_bytes_equal_its_witness(self):
        self.env.subject.write_bytes(self.V2)
        self.write_witness([(SUBJECT, self.V2)])
        self.assert_refusal([("subjects[0].path", "unstaged", "commit-run-work")])
        self.assertEqual(self.env.subject.read_bytes(), self.V2, "the runner touched the subject")

    def test_a_staged_subject_whose_bytes_equal_its_witness(self):
        self.env.subject.write_bytes(self.V2)
        sup.git(self.root, "add", "--", SUBJECT)
        self.write_witness([(SUBJECT, self.V2)])
        self.assert_refusal([("subjects[0].path", "staged", "commit-run-work")])

    def test_an_untracked_subject_named_in_a_prompts_artifacts(self):
        new = "docs/adrs/ADR-0002-new.md"
        (self.root / new).write_bytes(self.V2)
        self.write_witness([(new, self.V2)])
        run = self.env.load_run()
        run["prompts"][1]["artifacts"] = ["./" + new]
        self.env.run_path.write_text(sup.yaml.safe_dump(run, sort_keys=False))
        self.assert_refusal([("subjects[0].path", "untracked", "commit-run-work")],
                            self.argv(subjects=[self.root / new]))

    def test_the_latest_witness_entry_for_the_path_decides(self):
        self.env.subject.write_bytes(self.V2)
        self.write_witness([(SUBJECT, b"an earlier write\n"), (SUBJECT, self.V2)])
        self.assert_refusal([("subjects[0].path", "unstaged", "commit-run-work")])


class OwnerTests(_Preflight):
    """Causes outside the conductor's authority: a committed preflight-needs-owner record."""

    V2 = RunWorkTests.V2

    def test_an_unwitnessed_dirty_subject(self):
        self.env.subject.write_bytes(self.V2)
        self.assert_owner(["subjects[0].path:unwitnessed"])
        self.assertEqual(self.env.subject.read_bytes(), self.V2)
        self.assertNotEqual(self.head_blob(SUBJECT), self.V2, "the runner committed the subject")

    def test_a_witness_mismatch_after_someone_else_edited_the_subject(self):
        self.env.subject.write_bytes(self.V2)
        self.write_witness([(SUBJECT, self.V2)])
        self.env.subject.write_bytes(b"# v3, edited again by someone else\n")
        self.assert_owner(["subjects[0].path:witness-mismatch"])

    def test_a_dirty_subject_the_run_never_touched_even_when_witnessed(self):
        """Membership: a path neither changed in base_commit..HEAD nor named in an artifact is not
        run work, whatever the witness says."""
        other = self.root / "docs" / "other.md"
        other.write_text("other v1\n")
        sup.commit_all(self.root, "other, before the run's base would matter")
        # Re-pin the base to HEAD, so other.md did not change in base_commit..HEAD.
        self.env.base_commit = self.head()
        self.env.set_run(PROMPT)
        sup.commit_all(self.root, "re-pin base")
        other.write_bytes(self.V2)
        self.write_witness([("docs/other.md", self.V2)])
        self.assert_owner(["subjects[0].path:unattributed"], self.argv(subjects=[other]))

    def test_a_staged_version_that_differs_from_the_working_tree(self):
        self.env.subject.write_bytes(self.V2)
        sup.git(self.root, "add", "--", SUBJECT)
        self.env.subject.write_bytes(b"# v3 on top\n")
        self.write_witness([(SUBJECT, b"# v3 on top\n")])
        self.assert_owner(["subjects[0].path:mixed"])

    def test_an_ignored_subject(self):
        (self.root / ".gitignore").write_text("docs/ignored.md\n")
        sup.commit_all(self.root, "ignore")
        ign = self.root / "docs" / "ignored.md"
        ign.write_text("hidden\n")
        self.assert_owner(["subjects[0].path:ignored"], self.argv(subjects=[ign]))

    def test_an_env_file_subject(self):
        env_file = self.root / "docs" / ".env"
        env_file.write_text("TOKEN=1\n")
        sup.commit_all(self.root, "env")
        doc = self.assert_owner(["subjects[0].path:env-file"], self.argv(subjects=[env_file]))
        self.assertIsNone(doc["subjects"][0]["sha256"])

    def test_a_question_under_crux_home(self):
        home = self.root / "cruxhome"
        home.mkdir()
        (home / "q.md").write_text("q\n")
        sup.commit_all(self.root, "home")
        with mock.patch.dict(os.environ, {"CRUX_HOME": str(home)}):
            self.assert_owner(["question.path:crux-home"],
                              [str(self.env.run_path), "--prompt", "3", "--round", "1", "--question",
                               str(home / "q.md"), "--subject", str(self.env.subject)])

    def test_a_witness_bound_to_another_run(self):
        self.env.subject.write_bytes(self.V2)
        self.write_witness([(SUBJECT, self.V2)])
        path = self.env.run_dir / "run-work-witness.json"
        doc = json.loads(path.read_text())
        doc["run_id"] = "RUN-002"
        path.write_text(json.dumps(doc))
        self.assert_owner(["subjects[0].path:witness-invalid"])

    def test_an_owner_cause_wins_a_mixed_set(self):
        self.env.subject.write_bytes(self.V2)
        self.assert_owner(["subjects[0].path:missing", "subjects[1].path:unwitnessed"],
                          self.argv(subjects=[self.root / h.TYPO, self.env.subject]))

    def test_the_owner_record_holds_only_its_record_and_moves_nothing_else(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "a.txt").write_text("staged\n")
        sup.git(self.root, "add", "--", "src/a.txt")
        (self.root / "src" / "b.txt").write_text("untracked\n")
        self.env.subject.write_bytes(self.V2)
        status = self.git_out("status", "--porcelain=v1", "-z", "--untracked-files=all")
        self.assert_owner(["subjects[0].path:unwitnessed"])
        after = self.git_out("status", "--porcelain=v1", "-z", "--untracked-files=all")
        prefix = self.env.rel(self.env.run_dir).encode()
        strip = (lambda raw: sorted(e for e in raw.split(b"\0") if e and prefix not in e))
        self.assertEqual(strip(after), strip(status))
        self.assertIn(b"A  src/a.txt", after)  # positive control: the staged change is really there


class RetryCountTests(_Preflight):

    def test_the_third_refusal_since_the_last_claim_writes_preflight_retries_spent(self):
        typo = [self.root / h.TYPO]
        self.assert_refusal([("subjects[0].path", "missing", "retype")], self.argv(subjects=typo), at_prompt=1)
        self.assert_refusal([("subjects[0].path", "missing", "retype")], self.argv(subjects=typo), at_prompt=2)
        before = self.head()
        rc, out, err, gw = self.run_main(self.argv(subjects=typo))
        self.assertEqual((rc, gw.requests), (1, []), out + err)
        self.assertNotIn("refused", json.loads(out))
        recs = self.records()
        self.assertEqual(len(recs), 1)
        data = (self.root / recs[0]).read_bytes()
        doc = json.loads(data)
        self.assertEqual((doc["format_version"], doc["attempt"], doc["refusal_reason"]),
                         ("2", None, {"code": "preflight-retries-spent", "names": ["subjects[0].path:missing"]}))
        self.assertTrue(cr.seal_holds(data))
        log = self.env.rel(self.env.run_dir) + "/council-preflight.jsonl"
        self.assertEqual(self.commits_since(before),
                         [(f"crux council preflight: {sup.BOOK_ID} {sup.RUN_ID} prompt 3 round 1", [recs[0]]),
                          (f"crux council diagnostics: {sup.BOOK_ID} {sup.RUN_ID} prompt 3", [log])])
        self.assertEqual(self.attempts(), [])

    def test_a_claim_resets_the_count(self):
        typo = [self.root / h.TYPO]
        for n in (1, 2):
            self.assert_refusal([("subjects[0].path", "missing", "retype")], self.argv(subjects=typo), at_prompt=n)
        self.ran(1)  # the claim line resets the prompt's count
        rc, out, err, gw = self.run_main(self.argv(round_=2, subjects=typo))
        body = json.loads(out)
        self.assertEqual((rc, body["refused"], body["refusals_at_prompt"]), (1, "preflight", 1), out)

    def test_another_prompts_refusals_do_not_count(self):
        """Lines the log holds at another prompt are not counted at this one. The lines are written
        straight to the log, because the runner refuses an invocation at any prompt but the run's
        current prompt (`PromptBindingTests`)."""
        log = self.env.run_dir / "council-preflight.jsonl"
        log.write_text("".join(json.dumps({"at": "x", "event": "refusal", "prompt": 4, "round": 1,
                                           "codes": ["missing"], "fields": ["subjects[0].path"]}) + "\n"
                               for _ in range(5)) + "not json\n")
        rc, out, err, gw = self.run_main(self.argv(subjects=[self.root / h.TYPO]))
        body = json.loads(out)
        self.assertEqual((rc, body["refused"], body["refusals_at_prompt"], self.records()),
                         (1, "preflight", 1, []), out + err)
        # Positive control: the same lines at this prompt make the next refusal the bound's.
        log.write_text(log.read_text().replace('"prompt": 4', '"prompt": 3'))
        rc, out, err, gw = self.run_main(self.argv(subjects=[self.root / h.TYPO]))
        self.assertNotIn("refused", json.loads(out))
        self.assertEqual(json.loads((self.root / self.records()[0]).read_text())["refusal_reason"]["code"],
                         "preflight-retries-spent")

    def test_another_runs_refusals_do_not_count_and_each_line_names_its_run(self):
        """Every run of a book shares the run directory and its diagnostics log. Lines another run
        wrote at this prompt are not this run's refusals. Red when the count ignores `run_id`: the
        five foreign lines make this first refusal spend the bound. Positive control: the same
        lines under this run's id make the next refusal the bound's."""
        log = self.env.run_dir / "council-preflight.jsonl"
        log.write_text("".join(json.dumps({"at": "x", "event": "refusal", "prompt": 3, "round": 1,
                                           "run_id": "RUN-000", "codes": ["missing"],
                                           "fields": ["subjects[0].path"]}) + "\n" for _ in range(5)))
        rc, out, err, gw = self.run_main(self.argv(subjects=[self.root / h.TYPO]))
        body = json.loads(out)
        self.assertEqual((rc, body.get("refused"), body.get("refusals_at_prompt"), self.records()),
                         (1, "preflight", 1, []), out + err)
        self.assertEqual(self.diagnostics()[-1]["run_id"], sup.RUN_ID)
        log.write_text(log.read_text().replace('"RUN-000"', json.dumps(sup.RUN_ID)))
        rc, out, err, gw = self.run_main(self.argv(subjects=[self.root / h.TYPO]))
        self.assertNotIn("refused", json.loads(out))
        self.assertEqual(json.loads((self.root / self.records()[0]).read_text())["refusal_reason"]["code"],
                         "preflight-retries-spent")

    def test_an_owner_refusal_is_not_counted(self):
        self.env.subject.write_bytes(RunWorkTests.V2)
        self.assert_owner(["subjects[0].path:unwitnessed"])
        self.assertEqual(self.diagnostics(), [])

    def test_a_symlinked_diagnostics_log_refuses_with_exit_2(self):
        target = self.tmp / "elsewhere.jsonl"
        target.write_text("")
        (self.env.run_dir / "council-preflight.jsonl").symlink_to(target)
        rc, out, err, gw = self.run_main(self.argv(subjects=[self.root / h.TYPO]))
        self.assertEqual((rc, out, gw.requests), (2, "", []), err)
        self.assertIn("diagnostics log", err)
        self.assertEqual(target.read_text(), "")


class PromptBindingTests(_Preflight):
    """`--prompt` names the prompt the run is at, so the retry count and the claim's binding
    always do."""

    def at(self, n, round_=1, subjects=None) -> list[str]:
        argv = self.argv(round_, subjects)
        argv[argv.index("--prompt") + 1] = str(n)
        return argv

    def test_a_prompt_that_is_not_the_current_prompt_is_refused_and_never_resets_the_count(self):
        """S3 reproduction. Two refusals at the current prompt, then a third that names another
        prompt of the same module: refused `prompt`, not counted, nothing written. The next
        refusal at the current prompt is the bound's, a committed `preflight-retries-spent` record.
        Red when the count is keyed on the caller's `--prompt`: the third invocation starts a
        fresh count at prompt 4 and returns a retryable refusal with `refusals_at_prompt` 1."""
        typo = [self.root / h.TYPO]
        for n in (1, 2):
            self.assert_refusal([("subjects[0].path", "missing", "retype")], self.argv(subjects=typo), at_prompt=n)
        before, lines = self.head(), self.diagnostics()
        rc, out, err, gw = self.run_main(self.at(4, subjects=typo))
        body = json.loads(out)
        self.assertEqual((rc, gw.requests, body["refused"], body["given_prompt"], body["current_prompt"],
                          body["record"]), (1, [], "prompt", 4, PROMPT, None), out + err)
        self.assertEqual((self.records(), self.attempts(), self.head(), self.diagnostics()), ([], [], before, lines))
        rc, out, err, gw = self.run_main(self.argv(subjects=typo))
        self.assertEqual((rc, gw.requests), (1, []), out + err)
        self.assertNotIn("refused", json.loads(out))
        recs = self.records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(json.loads((self.root / recs[0]).read_text())["refusal_reason"],
                         {"code": "preflight-retries-spent", "names": ["subjects[0].path:missing"]})

    def test_a_clean_run_at_another_prompt_is_refused_and_convenes_nothing(self):
        """The silent-false-result path of the same reproduction: a clean run named at another
        prompt would have claimed there. Positive control: the same invocation at the current
        prompt claims and runs."""
        before = self.head()
        rc, out, err, gw = self.run_main(self.at(4))
        self.assertEqual((rc, json.loads(out)["refused"], gw.requests), (1, "prompt", []), out + err)
        self.assertEqual((self.attempts(), self.records(), self.head()), ([], [], before))
        self.ran(1)

    def test_a_prompt_missing_from_the_book_is_a_prompt_refusal_not_a_fatal(self):
        rc, out, err, gw = self.run_main(self.at(99))
        body = json.loads(out)
        self.assertEqual((rc, body["refused"], body["given_prompt"], body["current_prompt"], gw.requests),
                         (1, "prompt", 99, PROMPT, []), out + err)

    def test_a_run_with_no_integer_current_prompt_refuses_an_explicit_prompt_with_null(self):
        run = self.env.load_run()
        run["current_prompt"] = None
        self.env.run_path.write_text(sup.yaml.safe_dump(run, sort_keys=False))
        sup.commit_all(self.root, "no current prompt")
        rc, out, err, gw = self.run_main()
        body = json.loads(out)
        self.assertEqual((rc, body["refused"], body["given_prompt"], body["current_prompt"], gw.requests),
                         (1, "prompt", PROMPT, None, []), out + err)

    def test_omitting_prompt_defaults_to_the_current_prompt(self):
        argv = self.argv()
        del argv[argv.index("--prompt"):argv.index("--prompt") + 2]
        rc, out, err, gw = self.run_main(argv)
        self.assertEqual((rc, err, len(gw.requests)), (0, "", 3), out)
        record = json.loads((self.root / json.loads(out)["record"]).read_text())
        self.assertEqual(record["binding"], {"prompt": PROMPT, "module_tag": h.TAG})

    def test_the_address_findings_prompt_at_the_current_prompt_convenes_round_2(self):
        """Prompt 4 is the adr module's address-findings prompt (ordinal 3). Once the run is at
        it, `--prompt 4` convenes round 2 through the command line, bound to prompt 4."""
        self.ran(1)
        self.env.set_run(4)
        sup.commit_all(self.root, "the run reaches the address-findings prompt")
        rc, out, err, gw = self.run_main(self.at(4, round_=2))
        self.assertEqual((rc, err, len(gw.requests)), (0, "", 3), out)
        summary = json.loads(out)
        record = json.loads((self.root / summary["record"]).read_text())
        self.assertEqual((record["round"], record["binding"]), (2, {"prompt": 4, "module_tag": h.TAG}))
        # Control: round 1 is no longer the module's next place.
        rc, out, err, gw = self.run_main(self.at(4, round_=1))
        self.assertEqual((rc, json.loads(out)["refused"], gw.requests), (1, "round", []), out + err)


class RunSnapshotWitnessTests(_Preflight):
    """A run snapshot the run revised at this prompt is run work like any other subject, and the
    witness decides, as `gates.md` describes."""

    def revise_snapshot(self) -> tuple[str, bytes]:
        rel = self.env.run_path.relative_to(self.root).as_posix()
        data = self.env.run_path.read_bytes() + b"# notes revised in this prompt\n"
        self.env.run_path.write_bytes(data)
        return rel, data

    def test_a_witnessed_snapshot_is_a_commit_run_work_refusal(self):
        rel, data = self.revise_snapshot()
        self.write_witness([(rel, data)])
        body = self.assert_refusal([("subjects[0].path", "unstaged", "commit-run-work")],
                                   self.argv(subjects=[self.env.run_path]))
        self.assertEqual(body["causes"][0]["repair"], "commit-run-work")

    def test_control_the_same_snapshot_without_a_witness_goes_to_the_owner(self):
        self.revise_snapshot()
        self.assert_owner(["subjects[0].path:unwitnessed"], self.argv(subjects=[self.env.run_path]))


class DiagnosticsCommitTests(_Preflight):
    """After a committed council record the run directory is committed: one best-effort commit of
    the diagnostics log and the run-work witness."""

    def log_rel(self) -> str:
        return self.env.rel(self.env.run_dir) + "/council-preflight.jsonl"

    def witness_rel(self) -> str:
        return self.env.rel(self.env.run_dir) + "/run-work-witness.json"

    def message(self, prompt=PROMPT) -> str:
        return f"crux council diagnostics: {sup.BOOK_ID} {sup.RUN_ID} prompt {prompt}"

    def status(self) -> bytes:
        return self.git_out("status", "--porcelain=v1", "--untracked-files=all", "--", self.env.rel(self.env.run_dir))

    def test_a_ran_council_leaves_the_run_directory_committed(self):
        """Red when no diagnostics commit follows the record: the log stays untracked and refuses
        sync.sh and release-preflight."""
        self.write_witness([(SUBJECT, self.env.subject.read_bytes())])
        before = self.head()
        summary = self.ran(1)
        self.assertEqual(self.status(), b"")
        commits = self.commits_since(before)
        self.assertEqual(len(commits), 3, "attempt, record, diagnostics")
        self.assertEqual(commits[-1], (self.message(), sorted([self.log_rel(), self.witness_rel()])))
        log = self.head_blob(self.log_rel())
        self.assertEqual(log, (self.env.run_dir / "council-preflight.jsonl").read_bytes())
        self.assertEqual([json.loads(line)["event"] for line in log.decode().splitlines()], ["claim"])
        self.assertEqual(self.head_blob(self.witness_rel()), (self.env.run_dir / "run-work-witness.json").read_bytes())
        self.assertIsNotNone(self.head_blob(summary["record"]))

    def test_a_file_whose_bytes_equal_head_is_not_owned(self):
        """The log and the witness are committed once; a second council commits only what
        changed. Positive control: the first council commits both."""
        self.write_witness([(SUBJECT, self.env.subject.read_bytes())])
        first = self.head()
        self.ran(1)
        self.assertEqual(self.commits_since(first)[-1][1], sorted([self.log_rel(), self.witness_rel()]))
        before = self.head()
        self.ran(2)
        self.assertEqual(self.commits_since(before)[-1], (self.message(), [self.log_rel()]))

    def test_the_diagnostics_commit_is_absent_when_the_run_directory_holds_neither_file(self):
        """An owner stop with no log and no witness writes only its record."""
        self.env.subject.write_bytes(RunWorkTests.V2)
        before = self.head()
        self.assert_owner(["subjects[0].path:unwitnessed"])
        self.assertEqual([c[0].split(":")[0] for c in self.commits_since(before)], ["crux council preflight"])

    def test_preflight_retries_spent_leaves_the_three_refusal_lines_in_head(self):
        typo = [self.root / h.TYPO]
        for n in (1, 2):
            self.assert_refusal([("subjects[0].path", "missing", "retype")], self.argv(subjects=typo), at_prompt=n)
        before = self.head()
        rc, out, err, gw = self.run_main(self.argv(subjects=typo))
        self.assertEqual((rc, gw.requests), (1, []), out + err)
        recs = self.records()
        self.assertEqual(self.commits_since(before), [
            (f"crux council preflight: {sup.BOOK_ID} {sup.RUN_ID} prompt 3 round 1", [recs[0]]),
            (self.message(), [self.log_rel()])])
        lines = self.head_blob(self.log_rel()).decode().splitlines()
        self.assertEqual([json.loads(line)["event"] for line in lines], ["refusal"] * 3)
        self.assertEqual(self.status(), b"")

    def test_a_could_not_run_record_leaves_the_run_directory_committed(self):
        with mock.patch.dict(os.environ):
            del os.environ["OPENROUTER_API_KEY"]
            rc, out, err, gw = self.run_main()
        self.assertEqual((rc, gw.requests), (1, []), err)
        self.assertEqual(self.status(), b"")
        self.assertIsNotNone(self.head_blob(self.log_rel()))

    def refuse_diagnostics(self, **attrs):
        real = self.rc.council_commit.commit_owned
        message = self.message()

        def commit_owned(repo, owned, msg, **kw):
            if msg == message:
                raise self.rc.council_commit.CommitRefused("hook-or-commit-failed", "the hook said no", **attrs)
            return real(repo, owned, msg, **kw)

        return mock.patch.object(self.rc.council_commit, "commit_owned", side_effect=commit_owned)

    def test_a_refused_diagnostics_commit_keeps_exit_0_and_names_its_code(self):
        with self.refuse_diagnostics(moved=["src/a.txt"], staged=[self.log_rel()]):
            rc, out, err, gw = self.run_main(self.argv(1))
        self.assertEqual((rc, len(gw.requests)), (0, 3), err)
        summary = json.loads(out)
        self.assertEqual(summary["outcome"], "ran")
        self.assertIsNotNone(self.head_blob(summary["record"]), "the record is committed")
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertIn("hook-or-commit-failed", err)
        self.assertIn("src/a.txt", err)
        self.assertIn(self.log_rel(), err)
        # Moved outside work gets the remedy order on the same line: set-aside work first.
        self.assertIn("git stash list", err)
        self.assertLess(err.index("git stash list"), err.index("index.lock"))
        # A log left staged makes every later diagnostics commit refuse `mismatch`: name the unstage.
        self.assertIn(f"git restore --staged -- {self.log_rel()}", err)
        self.assertIsNone(self.head_blob(self.log_rel()), "control: the refused commit left the log out of HEAD")

    def test_a_refused_diagnostics_commit_keeps_exit_1_for_a_could_not_run_record(self):
        with mock.patch.dict(os.environ):
            del os.environ["OPENROUTER_API_KEY"]
            with self.refuse_diagnostics():
                rc, out, err, gw = self.run_main()
        self.assertEqual((rc, gw.requests), (1, []), err)
        self.assertEqual(json.loads(out)["outcome"], "could-not-run")
        self.assertIn("hook-or-commit-failed", err)

    def test_an_unexpected_failure_of_the_diagnostics_commit_keeps_the_records_code_and_names_only_its_type(self):
        real = self.rc.council_commit.commit_owned

        def commit_owned(repo, owned, msg, **kw):
            if msg == self.message():
                raise RuntimeError("leaked text")
            return real(repo, owned, msg, **kw)

        with mock.patch.object(self.rc.council_commit, "commit_owned", side_effect=commit_owned):
            rc, out, err, gw = self.run_main(self.argv(1))
        self.assertEqual((rc, len(gw.requests)), (0, 3), err)
        self.assertIn("RuntimeError", err)
        self.assertNotIn("leaked text", err)

    def test_a_moved_path_that_matches_the_secret_scan_is_withheld_and_the_rest_are_named(self):
        with self.refuse_diagnostics(moved=[h.DUMMY_KEY + ".txt", "src/b.txt"]):
            rc, out, err, gw = self.run_main(self.argv(1))
        self.assertEqual(rc, 0, err)
        self.assertIn("src/b.txt", err)
        self.assertIn("hook-or-commit-failed", err)

    def test_one_bad_staged_name_cannot_withhold_the_unstage_command_or_the_remedy_order(self):
        """The unstage command takes each staged path through the same per-path label as the rest of
        the line. Red when the command quotes raw paths: a key-shaped staged name withholds the whole
        line (the log's unstage and the remedy order with it), or an escape byte reaches stderr.
        Positive control: the clean log path is still named in a runnable command."""
        bad_key, bad_esc = f"leak/{h.DUMMY_KEY}.json", "esc\x1bname.json"
        with self.refuse_diagnostics(moved=["src/a.txt"], staged=[self.log_rel(), bad_key, bad_esc]):
            rc, out, err, gw = self.run_main(self.argv(1))
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertNotIn("\x1b", err)
        self.assertIn(f"git restore --staged -- {self.log_rel()}", err)
        for needle in ("withheld", "esc?name.json", "src/a.txt", "git stash list", "index.lock"):
            self.assertIn(needle, err)


if __name__ == "__main__":
    unittest.main()
