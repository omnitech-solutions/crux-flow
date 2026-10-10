"""The council gate orders records by the commits that introduced them, never by a wall-clock stamp.

Each rule below names the code mutation that turns its assertion red:

* A rolled-back clock cannot let an earlier approval outrank a later committed refusal: the gate
  stops at 4 and names both records. Mutation: sort `_evaluate_council_history` by `written_at`
  again -> the rollback history passes.
* Records on one commit, or on commits with no ancestry between them, are a tie (stop 1, tied
  records attached). Equal stamps on ordered commits are neither a tie nor a stop. Mutation: keep
  the equal-stamp tie.
* A path with no single introducing commit (a second add, a modification, a delete, a rename, a
  path only a merge adds, the same path added on two merged branches) stops at 4, and a record
  removed from the history stops at 4 with nothing able to clear it. Mutation: drop the history
  read.
* A hook-altered record that the owner's remedy later commits sealed keeps one introducing commit.
  Mutation: refuse every path with an earlier blob.
* Replace refs and a grafts file cannot change the answer. Mutation: read the log without the
  isolated environment.
* Recovery and round counting read the same order. Mutation: compare `written_at` in
  `_later_record`.
* A history object the local store lacks refuses, and an order read never fetches one. A file name
  cannot forge a commit. Removing any record of the run stops every gate of the run. Mutations are
  named on each test in `HistoryAvailabilityTests`.

Temporary repositories carry an isolated git configuration. Reads only `crux/` and committed
fixtures.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _council_gate_support as sup  # noqa: E402
from _council_gate_support import ts  # noqa: E402
from test_council_gate_attempts import _Base as GateBase, COUNCIL_N, RC, OK, BLOCK  # noqa: E402
from test_council_recovery import _Base as RecoveryBase, PROMPT, TAG, REFORMAT_HOOK  # noqa: E402

import council_gate as cg  # noqa: E402
import council_history_v3 as v3  # noqa: E402
import council_records as cr  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent.parent


def _git_version() -> tuple[int, ...]:
    out = subprocess.run(["git", "--version"], capture_output=True, text=True, env=sup.scrubbed_env()).stdout
    match = re.search(r"(\d+)\.(\d+)", out)
    return tuple(int(x) for x in match.groups()) if match else (0, 0)


#: `GIT_NO_LAZY_FETCH` arrived in git 2.44 and SHA-256 repositories in 2.29; older git skips the
#: tests that need them, because the order still refuses there (a fetch that fails is a missing object).
GIT_VERSION = _git_version()


class _Order(GateBase):
    """A gate fixture whose helpers write format-1 records, one commit each."""

    def rec(self, name, *, round, stamp, **kw):
        kw.setdefault("module_tag", "adr-1")
        kw.setdefault("prompt", COUNCIL_N)
        return self.env.write(name, self.env.council_doc(round=round, written_at=stamp, **kw))

    def under_v3(self, *artifacts):
        env = self.env
        records = cr.discover_records(env.run_dir)
        return v3._evaluate_council_history(cg.classify(env.book, COUNCIL_N), env.load_run(), env.run_path,
                                            env.root, env.args_for(*artifacts), records, cg.load_registry, None)

    def sh(self, *args):
        return sup.git(self.env.root, *args)

    def try_git(self, *args):
        env = sup.scrubbed_env()
        env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})
        return subprocess.run(["git", "-C", str(self.env.root), *args], capture_output=True, text=True, env=env)

    def rollback(self):
        r1 = self.rec("r1.json", round=1, stamp=ts(5))
        r2 = self.rec("r2.json", round=2, stamp=ts(1), outcome="could-not-run")
        return r1, r2


class ClockRollbackTests(_Order):
    def test_a_rolled_back_clock_stops_at_four_naming_both_records(self):
        """RED before the fix: the rollback history passes under the stamp order. Control: the same
        records with forward stamps stop on the could-not-run record under both policies."""
        r1, r2 = self.rollback()
        v = self.gate(r1, r2)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)
        text = " ".join(v.reasons)
        self.assertIn(self.env.rel(r1), text)
        self.assertIn(self.env.rel(r2), text)
        # The frozen policy keeps the limitation: it passes on the same history.
        self.assertEqual(self.under_v3(r1, r2).verdict, "pass")

    def test_forward_stamps_stop_on_the_could_not_run_record_under_both_policies(self):
        r1 = self.rec("r1.json", round=1, stamp=ts(1))
        r2 = self.rec("r2.json", round=2, stamp=ts(5), outcome="could-not-run")
        for label, v in (("current", self.gate(r1, r2)), ("frozen", self.under_v3(r1, r2))):
            with self.subTest(label):
                self.assertEqual((v.verdict, v.stops, v.deciding_record), ("stop", [4], self.env.rel(r2)),
                                 v.reasons)

    def test_consistent_stamps_and_commits_leave_the_verdicts_unchanged(self):
        r1 = self.rec("r1.json", round=1, stamp=ts(1))
        self.assertEqual(self.gate(r1).verdict, "pass")
        held = self.rec("r2.json", round=2, stamp=ts(2), decisions=(RC, OK, OK), findings=BLOCK)
        v = self.gate(r1, held)
        self.assertEqual((v.verdict, v.deciding_record), ("route", self.env.rel(held)), v.reasons)

    def test_equal_stamps_on_ordered_commits_are_neither_a_tie_nor_a_stop(self):
        r1 = self.rec("r1.json", round=1, stamp=ts(5))
        r2 = self.rec("r2.json", round=2, stamp=ts(5))
        v = self.gate(r2)
        self.assertEqual((v.verdict, v.deciding_record), ("pass", self.env.rel(r2)), v.reasons)
        self.assertEqual(self.under_v3(r1, r2).stops, [1], "control: the frozen policy ties on equal stamps")
        self.assertTrue(r1.exists())

    def test_an_owner_exception_for_a_place_does_not_clear_a_contradiction(self):
        r1, r2 = self.rollback()
        owner = self.env.write("owner.json", self.env.owner_doc(prompt=None), commit=True)
        v = self.gate(r1, r2, owner)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)
        self.assertIn("contradict", " ".join(v.reasons))


class TieTests(_Order):
    def test_two_records_in_one_commit_are_a_tie_and_need_attaching(self):
        a = self.rec("r1.json", round=1, stamp=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        b = self.rec("r2.json", round=2, stamp=ts(2))
        self.sh("reset", "-q", "--soft", "HEAD~2")
        self.env.commit_records(a, b, msg="both")
        v = self.gate(a, b)
        self.assertEqual((v.verdict, v.stops), ("stop", [1]), v.reasons)
        self.assertIn("tie", v.reasons[0])
        refused = self.gate(b)
        self.assertEqual(refused.verdict, "refuse", refused.reasons)
        self.assertIn(self.env.rel(a), refused.reasons[0])
        self.assertIn("tie", refused.reasons[0])

    def test_a_record_on_a_merged_side_branch_is_ordered_by_its_side_branch_commit(self):
        base = self.rec("r0.json", round=1, stamp=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        self.sh("checkout", "-q", "-b", "side")
        side = self.rec("r1.json", round=2, stamp=ts(2))
        self.sh("checkout", "-q", "main")
        self.sh("merge", "--no-ff", "-q", "-m", "merge", "side")
        v = self.gate(side)
        self.assertEqual((v.verdict, v.deciding_record), ("pass", self.env.rel(side)), v.reasons)
        self.assertTrue(base.exists())

    def test_a_side_branch_record_and_a_mainline_record_between_branch_point_and_merge_tie(self):
        self.sh("checkout", "-q", "-b", "side")
        side = self.rec("r1.json", round=1, stamp=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        self.sh("checkout", "-q", "main")
        main = self.rec("r2.json", round=2, stamp=ts(2))
        self.sh("merge", "--no-ff", "-q", "-m", "merge", "side")
        v = self.gate(side, main)
        self.assertEqual((v.verdict, v.stops), ("stop", [1]), v.reasons)
        self.assertIn("tie", v.reasons[0])


class PathHistoryTests(_Order):
    def test_a_modified_record_stops_at_four(self):
        r = self.rec("r1.json", round=1, stamp=ts(1))
        self.sh("commit", "-q", "--allow-empty", "-m", "unrelated")
        doc = json.loads(r.read_text())
        doc["seats"][0]["reasoning"] = "edited"
        self.env.write("r1.json", doc, commit=True)
        v = self.gate(r)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)
        self.assertIn(self.env.rel(r), " ".join(v.reasons))

    def test_add_modify_add_another_delete_stops_naming_the_deleted_record_and_the_other_does_not_clear_it(self):
        r = self.rec("r1.json", round=1, stamp=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        doc = json.loads(r.read_text())
        doc["written_at"] = ts(1, 2)
        self.sh("commit", "-q", "--allow-empty", "-m", "unrelated")
        self.env.write("r1.json", doc, commit=True)
        r2 = self.rec("r2.json", round=2, stamp=ts(2))
        self.sh("rm", "-q", "--", self.env.rel(r))
        self.sh("commit", "-q", "-m", "delete")
        v = self.gate(r2)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)
        self.assertIn(self.env.rel(r), " ".join(v.reasons))

    def test_a_renamed_record_stops(self):
        r = self.rec("r1.json", round=1, stamp=ts(1))
        self.sh("mv", self.env.rel(r), self.env.rel(r.with_name("renamed.json")))
        self.sh("commit", "-q", "-m", "rename")
        v = self.gate(r.with_name("renamed.json"))
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)
        self.assertIn(self.env.rel(r), " ".join(v.reasons))

    def test_a_path_added_on_two_merged_branches_stops(self):
        self.sh("checkout", "-q", "-b", "side")
        self.rec("r1.json", round=1, stamp=ts(1))
        self.sh("checkout", "-q", "main")
        self.sh("commit", "-q", "--allow-empty", "-m", "main moves")
        r = self.rec("r1.json", round=1, stamp=ts(1))
        self.assertEqual(self.try_git("merge", "-q", "-m", "merge", "side").returncode, 0)
        v = self.gate(r)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)

    def test_a_path_only_a_merge_adds_stops(self):
        self.sh("checkout", "-q", "-b", "side")
        self.rec("r1.json", round=1, stamp=ts(1))
        self.sh("checkout", "-q", "main")
        self.rec("r1.json", round=1, stamp=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        self.assertNotEqual(self.try_git("merge", "-q", "-m", "merge", "side").returncode, 0)
        resolved = self.env.write("r1.json", self.env.council_doc(round=1, written_at=ts(1), module_tag="adr-1",
                                                                 prompt=COUNCIL_N, errored=("google_top",)), commit=False)
        self.sh("add", "-A")
        self.sh("commit", "-q", "-m", "merge resolution")
        v = self.gate(resolved)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)

    def test_a_record_named_like_pathspec_magic_is_read_literally(self):
        r1 = self.rec(":(glob)r1.json", round=1, stamp=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        r2 = self.rec("r2.json", round=2, stamp=ts(2))
        v = self.gate(r2)
        self.assertEqual((v.verdict, v.deciding_record), ("pass", self.env.rel(r2)), v.reasons)
        self.assertTrue(r1.exists())

    def test_a_replace_ref_does_not_change_the_order(self):
        r1, r2 = self.rollback()
        first = self.sh("rev-parse", "HEAD~1").strip()
        head = self.sh("rev-parse", "HEAD").strip()
        self.sh("replace", head, first)
        v = self.gate(r1, r2)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)

    def test_a_grafts_file_refuses_the_order(self):
        r1 = self.rec("r1.json", round=1, stamp=ts(1))
        (self.env.root / ".git" / "info").mkdir(exist_ok=True)
        (self.env.root / ".git" / "info" / "grafts").write_text(self.sh("rev-parse", "HEAD").strip() + "\n")
        v = self.gate(r1)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)
        self.assertIn("grafts", " ".join(v.reasons))

    def test_repository_configuration_cannot_change_the_answer(self):
        """The fixture carries a merge, so a merge diff the configuration turned on would read the
        side-branch record as added twice and stop at 4. Mutation: drop `--diff-merges=off` and
        `log.diffMerges=off` from the order read -> this history stops instead of passing."""
        r0 = self.rec("r0.json", round=1, stamp=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        self.sh("checkout", "-q", "-b", "side")
        side = self.rec("r1.json", round=2, stamp=ts(2))
        self.sh("checkout", "-q", "main")
        self.sh("commit", "-q", "--allow-empty", "-m", "main moves")
        self.sh("merge", "--no-ff", "-q", "-m", "merge", "side")
        for key, value in (("log.showSignature", "true"), ("diff.renames", "copies"), ("log.follow", "true"),
                           ("log.diffMerges", "first-parent")):
            self.sh("config", key, value)
        v = self.gate(side)
        self.assertEqual((v.verdict, v.deciding_record), ("pass", self.env.rel(side)), v.reasons)
        self.assertTrue(r0.exists())

    def test_repository_configuration_cannot_hide_a_root_commit_record(self):
        """A record added in the repository's root commit and deleted later is a removal. With
        `log.showRoot=false` in the repository configuration the root commit prints no entries, the
        add is never read, and the removal check finds nothing to refuse. Mutation: drop
        `-c log.showRoot=true` from `_order_git` -> the history orders instead of refusing."""
        import council_history_v4 as v4
        root = self.env.root.parent / "root-record"
        root.mkdir()
        sup.git(root, "init", "-q", "-b", "main")
        council = root / "runs" / "r" / "council"
        council.mkdir(parents=True)
        run = {"book_id": "PB-1", "book_content_hash": "h", "run_id": "RUN-001"}
        doc = {"record_type": "council-record", "book": {"id": "PB-1", "content_hash": "h"},
               "run_id": "RUN-001", "binding": {"module_tag": "m-1", "prompt": 3}, "written_at": ts(1)}
        (council / "a.json").write_text(json.dumps(doc))
        sup.git(root, "add", "-A")
        sup.git(root, "commit", "-q", "-m", "root")
        sup.git(root, "rm", "-q", "--", "runs/r/council/a.json")
        sup.git(root, "commit", "-q", "-m", "delete")
        sup.git(root, "config", "log.showRoot", "false")
        with self.assertRaises(v4.OrderError) as caught:
            v4.committed_order(run, root / "runs" / "r", root, "m-1", 3)
        self.assertIn("removed", str(caught.exception))

    def test_a_shallow_clone_refuses_the_order(self):
        a = self.attempt()
        r = self.record(a)
        clone = self.env.root.parent / "clone"
        sup.git(self.env.root.parent, "clone", "-q", "--depth", "1", "file://" + str(self.env.root), str(clone))
        rel_run = self.env.run_path.relative_to(self.env.root)
        v = cg.evaluate_gate(cg.classify(self.env.book, COUNCIL_N), self.env.load_run(), clone / rel_run, clone,
                             self.env.args_for(r), "done")
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)
        self.assertIn("shallow", " ".join(v.reasons))
        with self.assertRaises(cr.RecordError):
            cg.expected_round(self.env.run, clone / rel_run.parent, clone, "adr-1", COUNCIL_N)


class RefutationOrderTests(_Order):
    def held_round(self):
        return self.rec("r1.json", round=1, stamp=ts(3), decisions=(RC, OK, OK), findings=BLOCK)

    def refute(self, r, stamp, commit=True):
        doc = self.env.refutation_doc(r, written_at=stamp)
        return self.env.write("refute.json", doc, commit=commit)

    def test_a_refutation_committed_after_its_council_record_with_forward_stamps_passes(self):
        r = self.held_round()
        f = self.refute(r, ts(4))
        v = self.gate(f)
        self.assertEqual((v.verdict, v.deciding_record), ("pass", self.env.rel(f)), v.reasons)

    def test_a_refutation_stamped_before_the_record_it_follows_contradicts_the_commit_order(self):
        r = self.held_round()
        f = self.refute(r, ts(2))
        v = self.gate(f)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)
        self.assertIn("contradict", " ".join(v.reasons))

    def test_a_refutation_committed_before_the_record_it_names_stops(self):
        doc = self.env.council_doc(module_tag="adr-1", prompt=COUNCIL_N, round=1, written_at=ts(3),
                                   decisions=(RC, OK, OK), findings=BLOCK)
        r = self.env.write("r1.json", doc, commit=False)
        f = self.refute(r, ts(2), commit=False)
        self.env.commit_records(f, msg="refutation first")
        self.env.commit_records(r, msg="council second")
        v = self.gate(f)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)
        self.assertIn("not committed after", " ".join(v.reasons))

    def test_round_counting_reads_the_committed_order_not_the_stamps(self):
        """Round 2 is adjudicated by a place-3 refutation. Its stamp is stale, so the stamp order
        sorts it first and it names no earlier round. Mutation: sort `expected_round` by stamp."""
        self.rec("r1.json", round=1, stamp=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        r2 = self.rec("r2.json", round=2, stamp=ts(5), decisions=(RC, OK, OK), findings=BLOCK)
        doc = self.env.refutation_doc(r2, role="adjudicator", recorder="adjudicator-1", written_at=ts(2))
        self.env.write("refute.json", doc, commit=True)
        committed = cg.expected_round(self.env.run, self.env.run_dir, self.env.root, "adr-1", COUNCIL_N)
        by_stamp = v3.expected_round(self.env.run, self.env.run_dir, self.env.root, "adr-1", COUNCIL_N)
        self.assertEqual((committed, by_stamp), (4, 3))
        # The gate half: the stale refutation stamp contradicts the committed order, so the gate stops
        # at 4 rather than counting places that disagree with round counting.
        v = self.gate()
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)
        self.assertIn("contradict", " ".join(v.reasons))

    def test_round_counting_and_the_gate_count_the_same_places(self):
        """Consistent stamps: round counting names round 4, and the gate places the next round at 4
        (above the round bound, so it stops there). Mutation: count places by stamp in either reader."""
        self.rec("r1.json", round=1, stamp=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        r2 = self.rec("r2.json", round=2, stamp=ts(2), decisions=(RC, OK, OK), findings=BLOCK)
        doc = self.env.refutation_doc(r2, role="adjudicator", recorder="adjudicator-1", written_at=ts(3))
        f = self.env.write("refute.json", doc, commit=True)
        self.assertEqual(cg.expected_round(self.env.run, self.env.run_dir, self.env.root, "adr-1", COUNCIL_N), 4)
        r4 = self.rec("r4.json", round=4, stamp=ts(4))
        v = self.gate(f, r4)
        self.assertEqual((v.verdict, v.deciding_record), ("stop", self.env.rel(r4)), v.reasons)
        self.assertIn(f"{self.env.rel(r4)} takes place 4", " ".join(v.reasons))


class HistoryAvailabilityTests(_Order):
    """The order reads committed history as it is: a missing history object refuses, a file name
    cannot forge a commit, and the removal check covers every record of the run."""

    def blobless_clone(self, reachable: bool) -> Path:
        self.sh("config", "uploadpack.allowFilter", "true")
        clone = self.env.root.parent / "blobless"
        sup.git(self.env.root.parent, "clone", "-q", "--no-local", "--filter=blob:none",
                "file://" + str(self.env.root), str(clone))
        if not reachable:
            sup.git(clone, "remote", "set-url", "origin", "file://" + str(self.env.root.parent / "gone"))
        return clone

    def removed_history(self):
        r1 = self.rec("r1.json", round=1, stamp=ts(1))
        gone = self.rec("gone.json", round=2, stamp=ts(2), outcome="could-not-run")
        self.sh("rm", "-q", "--", self.env.rel(gone))
        self.sh("commit", "-q", "-m", "delete the refusal")
        return r1, gone

    def clone_gate(self, clone, *paths):
        rel_run = self.env.run_path.relative_to(self.env.root)
        args = self.env.args_for(*paths)
        return cg.evaluate_gate(cg.classify(self.env.book, COUNCIL_N), self.env.load_run(), clone / rel_run, clone,
                                args, "done")

    def test_a_blobless_clone_with_an_unreachable_promisor_stops_rather_than_dropping_a_removed_record(self):
        """RED before the fix: the unread blob read as "not a record", so the deleted refusal dropped
        out of the order and the gate passed. Control: the full repository stops on the removal."""
        r1, gone = self.removed_history()
        full = self.gate(r1)
        self.assertEqual((full.verdict, full.stops), ("stop", [4]), full.reasons)
        self.assertIn("removed", " ".join(full.reasons))
        clone = self.blobless_clone(reachable=False)
        v = self.clone_gate(clone, r1)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)
        self.assertIn("missing from the local object store", " ".join(v.reasons))
        # The remedy names the command that clears the stop, as the history-read refusal does: the
        # order reads with lazy fetching off, so the next ordinary git command is no evidence.
        self.assertIn("git fetch --refetch --no-filter", " ".join(v.reasons))

    @unittest.skipIf(GIT_VERSION < (2, 44), "GIT_NO_LAZY_FETCH needs git 2.44")
    def test_an_order_read_never_fetches_from_a_reachable_promisor(self):
        import council_history_v4 as v4
        r1, gone = self.removed_history()
        clone = self.blobless_clone(reachable=True)
        oid = self.sh("rev-parse", "HEAD~1:" + self.env.rel(gone)).strip()
        rel_run_dir = self.env.run_dir.relative_to(self.env.root)
        with self.assertRaises(v4.OrderError):
            v4.committed_order(self.env.run, clone / rel_run_dir, clone, "adr-1", COUNCIL_N)
        probe = subprocess.run(["git", "-C", str(clone), "cat-file", "-e", oid], capture_output=True,
                               env={**v4._order_env()})
        self.assertNotEqual(probe.returncode, 0, "the order read fetched a missing blob from the promisor")

    def test_a_shallow_clone_stop_clears_once_the_history_is_complete(self):
        import council_history_v4 as v4
        r1 = self.rec("r1.json", round=1, stamp=ts(1))
        self.sh("commit", "-q", "--allow-empty", "-m", "later")
        clone = self.env.root.parent / "shallow"
        sup.git(self.env.root.parent, "clone", "-q", "--depth", "1", "file://" + str(self.env.root), str(clone))
        rel_run_dir = self.env.run_dir.relative_to(self.env.root)
        with self.assertRaises(v4.OrderError) as caught:
            v4.committed_order(self.env.run, clone / rel_run_dir, clone, "adr-1", COUNCIL_N)
        self.assertIn("git fetch --unshallow", str(caught.exception))
        sup.git(clone, "fetch", "-q", "--unshallow")
        order = v4.committed_order(self.env.run, clone / rel_run_dir, clone, "adr-1", COUNCIL_N)
        self.assertIn(self.env.rel(r1), order.commit)

    def test_a_control_byte_file_name_cannot_forge_the_introducing_commit(self):
        """A council-directory file named `a<0x01><commit>` committed beside a rolled-back refusal once
        credited the refusal to that earlier commit, which hid the contradiction. Mutation: split the
        log output on 0x01 before tokenising -> the gate passes."""
        import council_history_v4 as v4
        base = self.sh("rev-parse", "HEAD").strip()
        r1 = self.rec("r1.json", round=1, stamp=ts(5))
        r2 = self.env.write("r2.json", self.env.council_doc(module_tag="adr-1", prompt=COUNCIL_N, round=2,
                                                            written_at=ts(1), outcome="could-not-run"), commit=False)
        (self.env.council / ("a\x01" + base)).write_bytes(b"pad")
        self.sh("add", "-A")
        self.sh("commit", "-q", "-m", "refusal and a crafted name")
        head = self.sh("rev-parse", "HEAD").strip()
        order = v4.committed_order(self.env.run, self.env.run_dir, self.env.root, "adr-1", COUNCIL_N)
        self.assertEqual(order.commit[self.env.rel(r2)], head)
        self.assertEqual(order.contradictions(), [(self.env.rel(r1), self.env.rel(r2))])
        v = self.gate(r1, r2)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)

    def test_a_committed_deletion_of_another_modules_record_stops_this_gate(self):
        """The removal check is run-wide: every record of the run takes part. Mutation: limit the
        participation to this gate's scope -> the gate passes."""
        r1 = self.rec("r1.json", round=1, stamp=ts(1))
        other = self.rec("other.json", round=1, stamp=ts(2), module_tag="implementation-1", prompt=7)
        self.assertEqual(self.gate(r1).verdict, "pass", "control: the other module's record changes nothing")
        self.sh("rm", "-q", "--", self.env.rel(other))
        self.sh("commit", "-q", "-m", "delete another module's record")
        v = self.gate(r1)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)
        self.assertIn(self.env.rel(other), " ".join(v.reasons))
        self.assertIn("permanent", " ".join(v.reasons))

    @unittest.skipIf(GIT_VERSION < (2, 29), "SHA-256 repositories need git 2.29")
    def test_a_sha256_repository_orders_records_and_reports_a_removal(self):
        """A delete entry carries a 64-digit null id there. Mutation: compare against the 40-digit
        null id only -> the delete reads as a missing object instead of a removal."""
        import council_history_v4 as v4
        root = self.env.root.parent / "sha256"
        root.mkdir()
        sup.git(root, "init", "-q", "--object-format=sha256", "-b", "main")
        self.assertEqual(sup.git(root, "rev-parse", "--show-object-format").strip(), "sha256")
        council = root / "runs" / "r" / "council"
        council.mkdir(parents=True)
        run = {"book_id": "PB-1", "book_content_hash": "h", "run_id": "RUN-001"}

        def put(name, stamp):
            doc = {"record_type": "council-record", "book": {"id": "PB-1", "content_hash": "h"},
                   "run_id": "RUN-001", "binding": {"module_tag": "m-1", "prompt": 3}, "written_at": stamp}
            (council / name).write_text(json.dumps(doc))
            sup.git(root, "add", "-A")
            sup.git(root, "commit", "-q", "-m", name)
        put("a.json", ts(1))
        put("b.json", ts(2))
        order = v4.committed_order(run, root / "runs" / "r", root, "m-1", 3)
        self.assertTrue(order.before("runs/r/council/a.json", "runs/r/council/b.json"))
        self.assertEqual(len(order.commit["runs/r/council/a.json"]), 64)
        sup.git(root, "rm", "-q", "--", "runs/r/council/b.json")
        sup.git(root, "commit", "-q", "-m", "delete")
        with self.assertRaises(v4.OrderError) as caught:
            v4.committed_order(run, root / "runs" / "r", root, "m-1", 3)
        self.assertIn("removed", str(caught.exception))

    def test_a_deeply_nested_json_file_in_the_council_directory_is_not_a_record(self):
        """A file nested past the JSON recursion limit reads as no record and the order proceeds.
        Mutation: drop `RecursionError` from `doc_of`'s except -> the order read crashes."""
        import council_history_v4 as v4
        root = self.env.root.parent / "deep-json"
        root.mkdir()
        sup.git(root, "init", "-q", "-b", "main")
        council = root / "runs" / "r" / "council"
        council.mkdir(parents=True)
        (council / "deep.json").write_text("[" * 200000)
        sup.git(root, "add", "-A")
        sup.git(root, "commit", "-q", "-m", "deep")
        run = {"book_id": "PB-1", "book_content_hash": "h", "run_id": "RUN-001"}
        order = v4.committed_order(run, root / "runs" / "r", root, "m-1", 3)
        self.assertEqual(order.commit, {})

    def _merge_heavy_repo(self, seed: int) -> Path:
        """A history of 25 random steps (branch, merge, council record, other file) over one run, with
        every commit dated the same second. The same seed builds the same topology."""
        import random
        rnd = random.Random(seed)
        root = self.env.root.parent / f"merge-heavy-{seed}"
        root.mkdir()
        env = sup.scrubbed_env()
        env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
                    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"})

        def run_git(*args, check=True):
            r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=env)
            if check and r.returncode:
                raise AssertionError(f"git {args} failed: {r.stderr}")
            return r

        run_git("init", "-q", "-b", "main")
        (root / "runs" / "r" / "council").mkdir(parents=True)
        (root / "other").mkdir()
        (root / "other" / "init").write_text("i")
        run_git("add", "-A")
        run_git("commit", "-q", "-m", "init")
        branches, n = ["main"], 0
        for step in range(25):
            run_git("checkout", "-q", rnd.choice(branches))
            act = rnd.random()
            if act < 0.2:
                run_git("checkout", "-q", "-b", f"b{step}")
                branches.append(f"b{step}")
            elif act < 0.35 and len(branches) > 1:
                current = run_git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
                other = rnd.choice([x for x in branches if x != current])
                if run_git("merge", "--no-ff", "-q", "-m", "m", other, check=False).returncode:
                    run_git("merge", "--abort", check=False)
            elif act < 0.6:
                n += 1
                doc = {"record_type": "council-record", "book": {"id": "PB-1", "content_hash": "h"},
                       "run_id": "RUN-001", "binding": {"module_tag": "m", "prompt": 3}, "written_at": ts(n)}
                (root / "runs" / "r" / "council" / f"r{step}.json").write_text(json.dumps(doc))
                run_git("add", "-A")
                run_git("commit", "-q", "-m", f"r{step}")
            else:
                (root / "other" / f"f{step}").write_text(str(step))
                run_git("add", "-A")
                run_git("commit", "-q", "-m", f"o{step}")
        run_git("checkout", "-q", "main")
        for b in branches[1:]:
            if run_git("merge", "--no-ff", "-q", "-m", "fin", b, check=False).returncode:
                run_git("merge", "--abort", check=False)
        return root

    def test_merged_parallel_record_branches_order_without_a_false_permanent_stop(self):
        """Council records on branches that are later merged, with equal committer dates, read as one
        introduction each, and ancestry answers as `git merge-base --is-ancestor`. Seeds 12, 24, 35 and
        39 are the ones whose path-limited parent rewriting dropped a listed parent: the dropped
        commit was diffed against the empty tree, a record read as added twice, and the order stopped
        at 4 with "permanent". Mutation: add `--parents` to the history log in `committed_order` ->
        these seeds refuse. The seeds were chosen against git 2.54's streaming walk; another git
        version may rewrite parents differently, so the RED half of this test is version-bound."""
        import itertools
        import council_history_v4 as v4
        run = {"book_id": "PB-1", "book_content_hash": "h", "run_id": "RUN-001"}
        for seed in (12, 24, 35, 39):
            with self.subTest(seed=seed):
                root = self._merge_heavy_repo(seed)
                order = v4.committed_order(run, root / "runs" / "r", root, "m", 3)
                self.assertTrue(order.commit, "control: the history holds records of this scope")
                commits = sorted(set(order.commit.values()))
                self.assertGreater(len(commits), 1)
                for a, b in itertools.permutations(commits, 2):
                    truth = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", a, b],
                                           env=sup.scrubbed_env()).returncode == 0
                    self.assertEqual(order.is_ancestor(a, b), truth, f"{a[:7]} -> {b[:7]}")


class BoundedGraphTests(_Order):
    """The order reads the graph between the listed commits and their common ancestors, not the whole
    history. Each assertion is on a map size, never on time or memory.

    Mutation: read `rev-list --parents` over `rev` with no `^<base>` lines in `committed_order`
    -> `test_a_long_unrelated_prefix_stays_out_of_the_graph_map` fails on the map size."""

    RUN = {"book_id": "PB-1", "book_content_hash": "h", "run_id": "RUN-001"}

    def _repo(self, name: str):
        root = self.env.root.parent / name
        root.mkdir()
        env = sup.scrubbed_env()
        env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})

        def run_git(*args, stdin=None):
            r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=env,
                               input=stdin)
            if r.returncode:
                raise AssertionError(f"git {args} failed: {r.stderr}")
            return r.stdout.strip()

        run_git("init", "-q", "-b", "main")
        (root / "runs" / "r" / "council").mkdir(parents=True)
        return root, run_git

    def _record(self, root, run_git, name, n):
        doc = {"record_type": "council-record", "book": {"id": "PB-1", "content_hash": "h"},
               "run_id": "RUN-001", "binding": {"module_tag": "m", "prompt": 3}, "written_at": ts(n)}
        (root / "runs" / "r" / "council" / name).write_text(json.dumps(doc))
        run_git("add", "-A")
        run_git("commit", "-q", "-m", name)
        return run_git("rev-parse", "HEAD")

    def _filler(self, root, run_git, tag):
        (root / f"fill-{tag}").write_text(tag)
        run_git("add", "-A")
        run_git("commit", "-q", "-m", tag)
        return run_git("rev-parse", "HEAD")

    def _prefix(self, run_git, count):
        """`count` unrelated commits in one fast-import, each adding one file."""
        lines = []
        for i in range(count):
            lines += ["commit refs/heads/main", f"committer t <t@example.invalid> {1000 + i} +0000",
                      "data 2", f"p{i % 10}", f"M 100644 inline pre/f{i}", "data 1", "x"]
        run_git("fast-import", "--quiet", "--force", stdin="\n".join(lines) + "\n")
        run_git("reset", "-q", "--hard", "main")

    def test_a_long_unrelated_prefix_stays_out_of_the_graph_map(self):
        import council_history_v4 as v4
        root, run_git = self._repo("bounded")
        self._prefix(run_git, 400)
        c1 = self._record(root, run_git, "a.json", 1)
        self._filler(root, run_git, "f1")
        c2 = self._record(root, run_git, "b.json", 2)
        self._filler(root, run_git, "f2")
        c3 = self._record(root, run_git, "c.json", 3)
        self.assertEqual(int(run_git("rev-list", "--count", "HEAD")), 405)
        order = v4.committed_order(self.RUN, root / "runs" / "r", root, "m", 3)
        self.assertEqual(sorted(order.commit.values()), sorted([c1, c2, c3]))
        # The region is c2..c3 with two fillers and c1 as the boundary: 5 commits. The prefix is 400.
        self.assertLessEqual(len(order._parents), 6, "the graph map holds the region, not the history")
        a, b, c = (f"runs/r/council/{n}.json" for n in "abc")
        self.assertTrue(order.before(a, b) and order.before(b, c) and order.before(a, c))
        self.assertFalse(order.before(c, a))
        self.assertEqual(order.sort([c, a, b]), [a, b, c])
        self.assertEqual(order.contradictions(), [])
        self.assertLessEqual(len(order._masks), 3, "the ancestry structure holds the listed commits and bases")

    def test_ancestry_answers_only_for_listed_commits(self):
        import council_history_v4 as v4
        root, run_git = self._repo("listed")
        self._prefix(run_git, 5)
        c1 = self._record(root, run_git, "a.json", 1)
        mid = self._filler(root, run_git, "f1")
        c2 = self._record(root, run_git, "b.json", 2)
        order = v4.committed_order(self.RUN, root / "runs" / "r", root, "m", 3)
        self.assertTrue(order.is_ancestor(c1, c2))
        for older, newer in ((c1, mid), (mid, c2), ("0" * 40, c2)):
            with self.subTest(older=older[:7], newer=newer[:7]):
                with self.assertRaises(v4.OrderError):
                    order.is_ancestor(older, newer)

    def test_unrelated_histories_take_the_every_ancestor_fallback_and_order_correctly(self):
        """Two roots joined by `--allow-unrelated-histories`, records on both sides: the listed commits
        share no ancestor, so the read has no base and takes every ancestor. A guard, not a RED: the
        whole-history read orders the same history correctly."""
        import itertools
        import council_history_v4 as v4
        root, run_git = self._repo("unrelated")
        a = self._record(root, run_git, "a.json", 1)
        run_git("checkout", "-q", "--orphan", "other")
        run_git("rm", "-rfq", ".")
        (root / "runs" / "r" / "council").mkdir(parents=True, exist_ok=True)
        b = self._record(root, run_git, "b.json", 2)
        run_git("checkout", "-q", "main")
        run_git("merge", "-q", "--allow-unrelated-histories", "--no-ff", "-m", "join", "other")
        c = self._record(root, run_git, "c.json", 3)
        order = v4.committed_order(self.RUN, root / "runs" / "r", root, "m", 3)
        self.assertEqual(sorted(order.commit.values()), sorted([a, b, c]))
        for x, y in itertools.permutations([a, b, c], 2):
            truth = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", x, y],
                                   env=sup.scrubbed_env()).returncode == 0
            self.assertEqual(order.is_ancestor(x, y), truth, f"{x[:7]} -> {y[:7]}")
        pa, pb, pc = (f"runs/r/council/{n}.json" for n in "abc")
        self.assertTrue(order.tied(pa, pb))
        self.assertTrue(order.before(pa, pc) and order.before(pb, pc))



def _strip_parent_in_commit_graph(graph: Path, oid: str) -> None:
    """Forge a commit-graph file: record `oid` with no first parent. The chunk table follows the
    8-byte header; CDAT holds one entry per commit in OIDL order (tree id, parent 1, parent 2, date)."""
    import struct
    data = bytearray(graph.read_bytes())
    width = 20 if data[5] == 1 else 32
    chunks = {bytes(data[8 + 12 * i:12 + 12 * i]): struct.unpack(">Q", data[12 + 12 * i:20 + 12 * i])[0]
              for i in range(data[6] + 1)}
    count = struct.unpack(">I", data[chunks[b"OIDF"] + 1020:chunks[b"OIDF"] + 1024])[0]
    want = bytes.fromhex(oid)
    index = next(i for i in range(count)
                 if data[chunks[b"OIDL"] + i * width:chunks[b"OIDL"] + (i + 1) * width] == want)
    entry = chunks[b"CDAT"] + index * (width + 16)
    data[entry + width:entry + width + 4] = struct.pack(">I", 0x70000000)
    graph.chmod(0o644)
    graph.write_bytes(bytes(data))


class OrderReadHardeningTests(_Order):
    """Repository state outside the council directory cannot push the order read into a crash or a
    refusal with the wrong remedy. Each test names its mutation."""

    RUN = BoundedGraphTests.RUN
    _repo = BoundedGraphTests._repo
    _record = BoundedGraphTests._record
    _filler = BoundedGraphTests._filler

    def _answer(self, root):
        import council_history_v4 as v4
        order = v4.committed_order(self.RUN, root / "runs" / "r", root, "m", 3)
        paths = sorted(order.commit)
        return (dict(order.commit), dict(order.stamp), order.contradictions(),
                [(x, y, order.before(x, y), order.tied(x, y)) for x in paths for y in paths if x != y])

    @staticmethod
    def _plain_git(root, *args):
        """Run plain git in `root`, as the read would without its pins, returning raw bytes."""
        env = sup.scrubbed_env()
        env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                    "LC_ALL": "C", "LANGUAGE": "C"})
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, env=env)

    def _three_records(self, name):
        root, run_git = self._repo(name)
        commits = [self._record(root, run_git, "a.json", 1)]
        self._filler(root, run_git, "f1")
        commits.append(self._record(root, run_git, "b.json", 2))
        run_git("checkout", "-q", "-b", "side", "HEAD~1")
        commits.append(self._record(root, run_git, "c.json", 3))
        run_git("checkout", "-q", "main")
        run_git("merge", "-q", "--no-ff", "-m", "merge", "side")
        return root, run_git, commits

    def test_a_file_named_after_a_commit_cannot_change_the_answer(self):
        """A working-tree root file named exactly a revision the read passes makes a bare argv
        revision ambiguous (`both revision and filename`). The listed commits reach rev-list on
        stdin and every argv ends in `--`. Mutation: pass the listed commits to rev-list as argv
        with no `--` -> the read refuses with the partial-clone remedy."""
        root, run_git, commits = self._three_records("named")
        before = self._answer(root)
        head = run_git("rev-parse", "HEAD")
        for name in (head, *commits):
            (root / name).write_text("x")
        bare = self._plain_git(root, "rev-list", "--parents", head)
        self.assertNotEqual(bare.returncode, 0, "the sha-named file does not make a bare revision ambiguous")
        self.assertIn(b"ambiguous argument", bare.stderr)
        self.assertEqual(self._answer(root), before)
        run_git("add", "-A")
        run_git("commit", "-q", "-m", "files named after commits")
        self.assertEqual(self._answer(root), before)

    def test_a_log_output_encoding_cannot_change_the_answer(self):
        """`i18n.logOutputEncoding=UTF-16` re-encodes the `%x01%H` headers, which no longer parse.
        Mutation: drop `-c i18n.logOutputEncoding=UTF-8` from `_order_git` -> "the council
        directory history cannot be parsed"."""
        root, run_git, _ = self._three_records("encoding")
        before = self._answer(root)
        run_git("config", "i18n.logOutputEncoding", "UTF-16")
        plain = self._plain_git(root, "log", "--format=%x01%H", "-1")
        self.assertEqual(plain.returncode, 0)
        self.assertFalse(plain.stdout.startswith(b"\x01"), "the UTF-16 config does not re-encode the headers")
        self.assertEqual(self._answer(root), before)

    def test_a_forged_commit_graph_cannot_hide_an_ancestry_edge(self):
        """A commit-graph file that records a commit with no parent hides the commit's history from
        a read that trusts it, so ordered records read as tied. Mutation: drop
        `-c core.commitGraph=false` from `_order_git` -> the records read as tied."""
        root, run_git = self._repo("graph")
        self._record(root, run_git, "a.json", 1)
        self._filler(root, run_git, "f1")
        c2 = self._record(root, run_git, "b.json", 2)
        before = self._answer(root)
        run_git("commit-graph", "write", "--reachable")
        _strip_parent_in_commit_graph(root / ".git" / "objects" / "info" / "commit-graph", c2)
        self.assertEqual(run_git("rev-list", "--parents", "-n", "1", c2), c2, "the forged graph is not read")
        self.assertEqual(self._answer(root), before)

    def test_a_deeply_nested_earlier_blob_refuses_rather_than_crashing(self):
        """A record path first committed as deeply nested JSON, then holding a sealed format-2 record:
        the seal check on the earlier blob exceeds the recursion limit. Mutation: drop the
        RecursionError catch around the earlier blob's seal check -> RecursionError escapes."""
        import council_history_v4 as v4
        root, run_git = self._repo("deep")
        council = root / "runs" / "r" / "council"
        (council / "a.json").write_text("[" * 200_000 + "]" * 200_000)
        run_git("add", "-A")
        run_git("commit", "-q", "-m", "deep")
        doc = {"record_type": "council-record", "format_version": "2", "book": {"id": "PB-1", "content_hash": "h"},
               "run_id": "RUN-001", "binding": {"module_tag": "m", "prompt": 3}, "written_at": ts(1),
               "outcome": "ran"}
        doc["seal"] = cr.seal_of(doc)
        (council / "a.json").write_bytes(cr.canonical_bytes(doc))
        run_git("add", "-A")
        run_git("commit", "-q", "-m", "sealed")
        with self.assertRaises(v4.OrderError) as caught:
            v4.committed_order(self.RUN, root / "runs" / "r", root, "m", 3)
        self.assertIn("nested too deeply", str(caught.exception))
        self.assertIn("abandon the run", str(caught.exception))


class RecoveryOrderTests(RecoveryBase):
    def gate(self, *paths):
        env = self.env
        return cg.evaluate_gate(cg.classify(env.book, 3), env.load_run(), env.run_path, env.root,
                                env.args_for(*paths), "done")

    def test_recovery_sees_the_later_committed_record_despite_its_earlier_stamp(self):
        att = self.attempt()
        self.put(self.rec_name(), self.record_bytes(att), commit=True)
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("recognised", 0), err)
        later = self.rec_name(round_=2).replace("120000", "140000")
        doc = self.env.preflight_doc("preflight-retries-spent", round=2, written_at=ts(0))
        self.put(later, cr.canonical_bytes(cr.sealed(doc)), commit=True)
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("nothing-open", 0), err)

    def test_a_record_not_yet_committed_is_later_than_every_committed_one(self):
        att = self.attempt()
        self.put(self.rec_name(), self.record_bytes(att), commit=True)
        later = self.rec_name(round_=2).replace("120000", "140000")
        doc = self.env.preflight_doc("preflight-retries-spent", round=2, written_at=ts(0))
        self.put(later, cr.canonical_bytes(cr.sealed(doc)), commit=False)
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("nothing-open", 0), err)

    def test_a_runner_killed_after_the_attempt_commit_then_recovered_passes(self):
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("committed", 0), err)
        path = self.env.council / self.rec_name()
        v = self.gate(path)
        self.assertEqual((v.verdict, v.deciding_record), ("pass", self.env.rel(path)), v.reasons)

    def altered(self, data, indent):
        return (json.dumps(json.loads(data), indent=indent) + "\n").encode()

    def test_a_hook_altered_commit_then_the_owner_remedy_passes(self):
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        path = self.put(self.rec_name(), self.altered(data, 4), commit=True)
        rc, docs, err = self.recover("--owner-commit-pending")
        self.assertEqual((self.report(docs)["recovery"], rc), ("committed", 0), err)
        v = self.gate(path)
        self.assertEqual((v.verdict, v.deciding_record), ("pass", self.env.rel(path)), v.reasons)

    def test_two_hook_altered_blobs_then_the_owner_remedy_passes(self):
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        self.put(self.rec_name(), self.altered(data, 4), commit=True)
        path = self.put(self.rec_name(), self.altered(data, 3), commit=True)
        rc, docs, err = self.recover("--owner-commit-pending")
        self.assertEqual((self.report(docs)["recovery"], rc), ("committed", 0), err)
        v = self.gate(path)
        self.assertEqual((v.verdict, v.deciding_record), ("pass", self.env.rel(path)), v.reasons)

    def test_a_change_after_the_sealed_bytes_has_no_single_introducing_commit(self):
        import council_history_v4 as v4
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        path = self.put(self.rec_name(), self.altered(data, 4), commit=True)
        self.recover("--owner-commit-pending")
        order = v4.committed_order(self.env.run, self.env.run_dir, self.root, TAG, PROMPT)
        self.assertIn(self.env.rel(path), order.commit)
        self.put(self.rec_name(), self.altered(data, 3), commit=True)
        with self.assertRaises(v4.OrderError) as caught:
            v4.committed_order(self.env.run, self.env.run_dir, self.root, TAG, PROMPT)
        self.assertIn("introduced", str(caught.exception))
        v = self.gate(path, att)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)

    def test_deleting_a_hook_altered_record_then_recovering_stops(self):
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        path = self.put(self.rec_name(), self.altered(data, 4), commit=True)
        sup.git(self.root, "rm", "-q", "--", self.env.rel(path))
        sup.git(self.root, "commit", "-q", "-m", "delete the altered record")
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("committed", 0), err)
        v = self.gate(path)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)
        self.assertIn("delete", " ".join(v.reasons))


class RequestFreeTests(unittest.TestCase):
    def test_recovery_and_the_current_policy_import_no_council_runner_or_llm_caller(self):
        banned = ("crux.council", "crux.core.llm_caller", "run-council", "run_council", "llm_caller")
        for name in ("council_history_v4.py", "council_recovery.py"):
            tree = ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                modules = ([a.name for a in node.names] if isinstance(node, ast.Import)
                           else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                for module in modules:
                    self.assertFalse(any(b in module for b in banned), f"{name} imports {module}")
        probe = ("import sys; sys.path.insert(0, %r); import council_recovery, council_history_v4; "
                 "bad=[m for m in sys.modules if m.startswith('crux.council') or 'llm_caller' in m or "
                 "'run_council' in m]; print(bad)" % str(SCRIPTS))
        out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                             env=sup.scrubbed_env(), cwd=str(SCRIPTS))
        self.assertEqual(out.stdout.strip(), "[]", out.stderr)


if __name__ == "__main__":
    unittest.main()
