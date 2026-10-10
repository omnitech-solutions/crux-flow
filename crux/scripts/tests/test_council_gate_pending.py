"""The council gate's open-attempt stop, its pending-copy reader and its git path arguments.

Each rule below is shown with a positive control and the named code mutation that turns its
assertion red:

* The open-attempt stop sends the conductor through the process check and a probe, then recovery,
  and only then `--outcome blocked`, with the real run path and prompt. Mutation: restore the
  old text that skips recovery.
* `read_pending` refuses every path the pending-copy writer refuses: a symlinked interior
  component under the git directory, and a book id or run id that is not one path component.
  Mutation: read through `pending_dir` and `Path.iterdir` again -> the symlinked copy is read.
* A git argument that begins with `-` or equals a revision name is never read as an option or a
  revision: the `--` separator follows the revision in every `ls-tree` and in the run-work diff.
  Mutation: drop the separator -> the path is not preceded by `--`.

Reads only `crux/` and temporary repositories with an isolated git configuration.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _council_gate_support as sup  # noqa: E402
import test_council_gate_attempts as ta  # noqa: E402

import council_gate as cg  # noqa: E402
import council_records as cr  # noqa: E402

COUNCIL_N = ta.COUNCIL_N


class OpenAttemptStopTextTests(ta._Base):
    def test_the_stop_names_the_probe_then_recovery_then_blocked_with_the_run_and_prompt(self):
        a = self.attempt()
        v = self.gate(a)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)
        text = " ".join(v.reasons)
        run_rel = self.env.rel(self.env.run_path)
        probe = f"run-council.py --recover {run_rel} --prompt {COUNCIL_N} --probe"
        recover = f"run-council.py --recover {run_rel} --prompt {COUNCIL_N})"
        self.assertIn(probe, text)
        self.assertIn(recover, text)
        i_probe, i_recover = text.index(probe), text.index(recover)
        i_blocked = text.index("advance --outcome blocked")
        self.assertLess(i_probe, i_recover)
        self.assertLess(i_recover, i_blocked)
        self.assertIn("only when recovery leaves the attempt open", text)
        self.assertNotIn("run-council.py --recover --probe", text)


    def test_a_run_path_with_a_space_is_quoted_in_the_printed_commands(self):
        """Mutation: splice the run path unquoted -> a pasted command splits at the space.
        Positive control: the quoted path names the run snapshot."""
        a = self.attempt()
        run, root = self.env.load_run(), self.env.root
        states = [st for st in cg.attempt_states(run, self.env.run_dir, root, "adr-1", COUNCIL_N)
                  if st.rel == self.env.rel(a)]
        self.assertEqual(len(states), 1)  # control: the open attempt is in scope
        v = cg._open_attempt_verdict(root, states, [str(a)], run_path=root / "my runs" / "run-RUN-001.yaml",
                                     prompt_n=COUNCIL_N)
        text = " ".join(v.reasons)
        self.assertIn(f"run-council.py --recover 'my runs/run-RUN-001.yaml' --prompt {COUNCIL_N} --probe", text)
        self.assertNotIn("--recover my runs/", text)

class ReadPendingTests(ta._Base):
    def _git_crux(self) -> Path:
        return cr.absolute_git_dir(self.env.root) / "crux"

    def _outside(self, name: str) -> Path:
        """A directory outside the git directory, holding a valid-looking pending copy at
        `<name>/<book>/<run>/x.json`; returns `<name>`."""
        top = Path(self._td.name).resolve() / name
        out = top / sup.BOOK_ID / sup.RUN_ID
        out.mkdir(parents=True)
        (out / "x.json").write_bytes(b'{"record_type": "council-record"}')
        return top

    def _swap_for_symlink(self, real: Path, target: Path) -> None:
        real.rename(real.with_name(real.name + "-moved"))
        real.symlink_to(target, target_is_directory=True)

    def test_a_real_pending_copy_is_read(self):
        self.pending("x.json", b'{"a": 1}')
        self.assertEqual(cg.read_pending(self.env.root, self.env.load_run()), {"x.json": b'{"a": 1}'})

    def test_a_missing_directory_holds_no_copy(self):
        self.assertEqual(cg.read_pending(self.env.root, self.env.load_run()), {})

    def test_a_symlinked_council_pending_component_is_refused(self):
        self.pending("x.json", b"{}")
        self.assertEqual(list(cg.read_pending(self.env.root, self.env.load_run())), ["x.json"])  # control
        self._swap_for_symlink(self._git_crux() / "council-pending", self._outside("elsewhere"))
        with self.assertRaises(cr.SymlinkRefused):
            cg.read_pending(self.env.root, self.env.load_run())

    def test_a_symlinked_book_component_is_refused(self):
        self.pending("x.json", b"{}")
        pend = self._git_crux() / "council-pending"
        outside = Path(self._td.name).resolve() / "other-book"
        (outside / sup.RUN_ID).mkdir(parents=True)
        (outside / sup.RUN_ID / "x.json").write_bytes(b"{}")
        self._swap_for_symlink(pend / sup.BOOK_ID, outside)
        with self.assertRaises(cr.SymlinkRefused):
            cg.read_pending(self.env.root, self.env.load_run())

    def test_a_symlinked_crux_component_is_refused(self):
        self.pending("x.json", b"{}")
        wrapper = Path(self._td.name).resolve() / "wrapper"
        wrapper.mkdir()
        self._outside("scratch").rename(wrapper / "council-pending")
        self._swap_for_symlink(self._git_crux(), wrapper)
        with self.assertRaises(cr.SymlinkRefused):
            cg.read_pending(self.env.root, self.env.load_run())

    def test_a_symlinked_child_and_a_non_regular_child_are_refused(self):
        p = self.pending("x.json", b"{}")
        target = Path(self._td.name).resolve() / "target.json"
        target.write_bytes(b"{}")
        p.unlink()
        p.symlink_to(target)
        with self.assertRaises(cr.SymlinkRefused):
            cg.read_pending(self.env.root, self.env.load_run())
        p.unlink()
        p.mkdir()  # a directory named like a copy is not a regular file
        with self.assertRaises(cr.RecordError):
            cg.read_pending(self.env.root, self.env.load_run())

    def test_an_id_that_is_not_one_path_component_is_refused(self):
        self.pending("x.json", b"{}")
        for bad in ("a/b", "..", ".", "", "a\\b"):
            with self.subTest(book_id=bad):
                run = dict(self.env.load_run(), book_id=bad)
                with self.assertRaises(cr.PathRefused):
                    cg.read_pending(self.env.root, run)
            with self.subTest(run_id=bad):
                run = dict(self.env.load_run(), run_id=bad)
                with self.assertRaises(cr.PathRefused):
                    cg.read_pending(self.env.root, run)

    def test_attempt_states_refuses_through_the_symlink(self):
        self.pending("x.json", b"{}")
        self._swap_for_symlink(self._git_crux() / "council-pending", self._outside("elsewhere-states"))
        with self.assertRaises(cr.SymlinkRefused):
            cg.attempt_states(self.env.load_run(), self.env.run_dir, self.env.root, "adr-1", COUNCIL_N)

    def test_the_advance_run_entry_point_refuses_a_symlinked_pending_directory(self):
        a = self.attempt()
        self.pending("x.json", b"{}")
        self._swap_for_symlink(self._git_crux() / "council-pending", self._outside("elsewhere-cli"))
        proc = self.cli("--outcome", "done", "--artifacts", self.arts(a))
        payload = self.out(proc)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(payload["gate"]["verdict"], "refuse", payload)
        self.assertIn("pending copy cannot be read", payload["gate"]["reasons"][0])


class GitPathArgumentTests(ta._Base):
    """A fixture whose docs directory begins with `-` would move every path the book names, so
    these tests observe the argument vector instead: the path follows a `--` separator."""

    def _spy(self):
        calls: list[tuple[str, ...]] = []
        real = cr.git

        def spy(repo, *args, **kw):
            calls.append(tuple(args))
            return real(repo, *args, **kw)

        patcher = mock.patch.object(cr, "git", spy)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def _assert_separated(self, calls, verb, path_suffix):
        hits = [c for c in calls if c[0] == verb and any(a.endswith(path_suffix) for a in c)]
        self.assertTrue(hits, f"control: a {verb} call naming {path_suffix} ran")
        for c in hits:
            i = next(i for i, a in enumerate(c) if a.endswith(path_suffix))
            self.assertEqual(c[i - 1], "--", f"{c}: the path follows `--`")

    def test_holding_attempts_separates_its_directory_from_the_revision(self):
        calls = self._spy()
        a = self.attempt()
        a.unlink()
        held = cg.holding_attempts(self.env.load_run(), self.env.run_dir, self.env.root, "adr-1", COUNCIL_N, [])
        self.assertEqual(held, [self.env.rel(a)])  # control: HEAD still holds the deleted attempt
        self._assert_separated(calls, "ls-tree", "/council/")

    def test_the_removed_record_check_and_the_head_listing_separate_their_directory(self):
        a = self.attempt()
        r = self.record(a)
        calls = self._spy()
        named = cg._head_records_naming(self.env.root, self.env.load_run(), self.env.council)
        self.assertEqual(named, {self.env.rel(a): [self.env.rel(r)]})  # control
        self.assertIsNone(cg._removed_record_problem(self.env.root, self.env.load_run(), self.env.council))
        self._assert_separated(calls, "ls-tree", "/council/")
        self.assertGreaterEqual(len([c for c in calls if c[0] == "ls-tree"]), 2)

    def test_a_file_named_like_the_base_commit_does_not_misparse_the_run_work_diff(self):
        env = self.env
        base = env.base_commit
        (env.root / base).write_text("a file whose name is the base commit hash\n")
        (env.root / "changed-after-base.txt").write_text("x\n")
        sup.commit_all(env.root, "after base")
        got = cg.run_work_candidates(env.load_run(), env.run_path, env.book_path, env.root)
        self.assertIn("changed-after-base.txt", got)
        self.assertIn(base, got)  # control: the diff ran and listed the file named like the base

    def test_a_file_named_like_the_range_end_does_not_misparse_the_reviewed_range(self):
        """The independent-review range check diffs `<base> <end>` and `<end> HEAD`. A working-tree
        file named exactly like the end commit makes git read the hash as ambiguous unless `--`
        follows the revisions. Mutation: drop either separator -> the range reads as changing no
        path, or its later changes cannot be read. Positive control: the reviewed path is listed
        as clean, so the range check read it."""
        env = self.env
        base = sup.git(env.root, "rev-parse", "HEAD").strip()
        (env.root / "reviewed.txt").write_text("reviewed\n")
        sup.commit_all(env.root, "the reviewed change")
        end = sup.git(env.root, "rev-parse", "HEAD").strip()
        (env.root / end).write_text("an untracked file named like the range end\n")
        problems = cg._range_problems(env.root, f"{base}..{end}")
        self.assertFalse([p for p in problems if "changes no path" in p or "cannot be read" in p], problems)
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
