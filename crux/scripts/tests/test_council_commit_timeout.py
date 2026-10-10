"""`council_commit`: what a runner commit does when its time bound runs out.

Every test drives `commit_owned` against a real temporary git repository whose pre-commit hook
stalls. A hook that sets the user's work aside and stalls is what a pre-commit framework looks like
at its worst moment: the work is out of the working tree until the hook's `finally` puts it back.

Timing discipline. No test races a fixed bound against process start-up. The hook writes a
readiness file once it has done what the test relies on, and `_fire_after_ready` makes the first wait
on the commit child time out only after that file exists (its own 60 s bound). The timeout then
fires at a deterministic instant, whatever the machine load. Each test names, in its docstring, the
mutation of `council_commit` that turns it red.
"""
from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _council_gate_support as sup  # noqa: E402
from test_council_commit import _Repo  # noqa: E402

import council_commit as cc  # noqa: E402

#: How long a test waits for a hook's readiness file before it lets the timeout fire anyway.
_READY_WAIT = 60.0


@contextlib.contextmanager
def _fire_after_ready(ready: Path, fire: float = 0.2):
    """Make the first wait on the `git commit --only` child time out `fire` seconds after `ready`
    exists. Every other wait (the grace wait, the drain, every other git child) is untouched."""
    real = subprocess.Popen
    state = {"fired": False}

    class _Popen(real):
        def communicate(self, input=None, timeout=None):  # noqa: A002 - the stdlib's signature
            if "--only" in (self.args or ()) and not state["fired"]:
                state["fired"] = True
                end = time.monotonic() + _READY_WAIT
                while not ready.exists() and time.monotonic() < end:
                    time.sleep(0.02)
                return super().communicate(input, timeout=fire)
            return super().communicate(input, timeout=timeout)

    with mock.patch.object(cc.subprocess, "Popen", _Popen):
        yield


class _Stalling(_Repo):
    """A repository whose pre-commit hook sets the user's work aside, as a pre-commit framework
    does, and stalls. x.txt is staged and then edited again (staged-plus-unstaged); y.txt is
    edited and not staged (unstaged-only); clean.txt is untouched."""

    def setUp(self):
        super().setUp()
        r = self.repo
        (r / "x.txt").write_text("x staged\n")
        sup.git(r, "add", "--", "x.txt")
        (r / "x.txt").write_text("x staged then edited\n")
        (r / "y.txt").write_text("y unstaged\n")
        self.ready = self.tmp / "hook-ready"
        self.backup = self.tmp / "hook-backup"

    def set_aside_hook(self, *, restore: bool, extra: str = "", then: str = "sleep 60\n") -> None:
        """Back the work up, write HEAD's text over it (the set-aside), signal readiness, then
        run `then`. With `restore`, an INT or TERM trap puts the backup back first."""
        bk = self.backup
        trap = (f"trap 'cp \"{bk}/x.txt\" x.txt; cp \"{bk}/y.txt\" y.txt; exit 1' INT TERM\n"
                if restore else "")
        self.hook(f'mkdir -p "{bk}"\n{trap}'
                  f'cp x.txt "{bk}/x.txt"\ncp y.txt "{bk}/y.txt"\n'
                  f'printf "v1\\n" > x.txt\nprintf "v1\\n" > y.txt\n{extra}'
                  f': > "{self.ready}"\n{then}')

    def commit_that_times_out(self, owned, **kw) -> cc.CommitRefused:
        with _fire_after_ready(self.ready), self.assertRaises(cc.CommitRefused) as ctx:
            cc.commit_owned(self.repo, owned, "crux test", **kw)
        self.assertEqual(ctx.exception.code, "timeout", str(ctx.exception))
        return ctx.exception



class LandedBeforeTheStopTests(_Repo):
    def test_a_commit_that_landed_before_the_stop_is_named_in_the_refusal(self):
        """A post-commit hook stalls after the commit has landed. Mutation: never compare HEAD with
        the snapshot's HEAD after a timeout -> the refusal says only that git was stopped, and the
        landed, unverified commit is not named. Positive control: HEAD did move."""
        ready = self.tmp / "post-ready"
        self.hook(f': > "{ready}"\nsleep 60\n', name="post-commit")
        head = self.head()
        with mock.patch.object(cc, "_GRACE_SECONDS", 1.0), _fire_after_ready(ready), \
                self.assertRaises(cc.CommitRefused) as ctx:
            cc.commit_owned(self.repo, self.own("run/rec.json", b"rec\n"), "crux test")
        err = ctx.exception
        self.assertEqual(err.code, "timeout", str(err))
        new = self.head()
        self.assertNotEqual(new, head)  # the control: the commit landed
        self.assertIn(new, err.detail)
        self.assertIn("landed", err.detail)
        self.assertIn("unverified", err.detail)


class TimeoutNamesWhatMovedTests(_Stalling):
    def test_a_timeout_that_left_the_users_work_set_aside_names_every_moved_path(self):
        """Mutation: raise the bare timeout without comparing the outside state against the
        snapshot (the code before the fix) -> `.moved` is empty and the detail never names x.txt
        or y.txt. Positive controls: clean.txt, which the hook did not touch, is not named, and a
        never-tracked owned path this call added stays staged and is named in `.staged`."""
        head = self.head()
        owned = self.own("run/new.json", b"fresh\n")
        self.set_aside_hook(restore=False)
        err = self.commit_that_times_out(owned)
        self.assertEqual(self.head(), head)
        for moved in ("x.txt", "y.txt"):
            self.assertIn(moved, err.moved)
            self.assertIn(moved, err.detail)
        self.assertNotIn("clean.txt", err.moved)
        self.assertNotIn("clean.txt", err.detail)
        self.assertNotIn(cc.MOVED_UNKNOWN, err.moved)
        self.assertEqual(err.staged, ("run/new.json",))
        self.assertEqual((self.repo / "y.txt").read_text(), "v1\n")  # the control: it did move

    def test_a_hook_that_restores_on_sigint_gives_the_users_work_back_byte_for_byte(self):
        """The pre-commit-framework shape: the hook applies its backup in a `finally`, here an INT
        trap. Mutation: SIGKILL the group on timeout (the code before the fix) -> the trap never
        runs, x.txt and y.txt keep HEAD's text and the user's edits are gone."""
        self.set_aside_hook(restore=True)
        err = self.commit_that_times_out(self.own("run/rec.json", b"new\n"))
        self.assertEqual((self.repo / "x.txt").read_bytes(), b"x staged then edited\n")
        self.assertEqual((self.repo / "y.txt").read_bytes(), b"y unstaged\n")
        self.assertEqual(err.moved, ())
        self.assertEqual(sup.git(self.repo, "show", ":x.txt"), "x staged\n")

    def test_a_hook_that_pushes_a_stash_entry_moves_refs_stash(self):
        """Mutation: leave the stash ref out of the snapshot comparison -> `.moved` stays empty
        and the detail is silent about the stash. Positive control: no other path is named, because
        the hook changes no file."""
        # `git stash` and `write-tree` need the index lock the commit holds, so the hook builds a
        # stash commit with plumbing and points `refs/stash` at it.
        self.hook('unset GIT_INDEX_FILE\nc=$(git commit-tree -m hook-stash -p HEAD "HEAD^{tree}")\n'
                  f'git update-ref --create-reflog -m hook-stash refs/stash "$c"\n: > "{self.ready}"\nsleep 60\n')
        self.assertEqual(sup.git(self.repo, "stash", "list").strip(), "")
        err = self.commit_that_times_out(self.own("run/rec.json", b"new\n"))
        self.assertEqual(err.moved, ("refs/stash",))
        self.assertIn("refs/stash", err.detail)
        self.assertIn("hook-stash", sup.git(self.repo, "stash", "list"))

    def test_a_comparison_that_cannot_complete_says_the_outside_state_was_not_compared(self):
        """Mutation: swallow a failed comparison and report nothing moved -> `.moved` is empty
        and the detail claims nothing. Positive control: the same hook, with room to compare,
        names the paths (first test)."""
        self.set_aside_hook(restore=False)
        with mock.patch.object(cc, "_COMPARE_SECONDS", 0.0):
            err = self.commit_that_times_out(self.own("run/rec.json", b"new\n"))
        self.assertEqual(err.moved, (cc.MOVED_UNKNOWN,))
        self.assertIn("not compared", err.detail)

    def test_a_timeout_detail_stays_bounded_and_prints_no_control_character(self):
        """Mutation: build the detail without `_printable` or the length cut -> the escape byte
        in a path name reaches the detail, or a long list is unbounded."""
        (self.repo / "esc\x1bname.txt").write_text("a\n")
        for i in range(400):
            (self.repo / f"many-{i:04d}-{'p' * 30}.txt").write_text("a\n")
        self.set_aside_hook(restore=False, extra="rm -f esc*.txt many-*.txt\n")
        err = self.commit_that_times_out(self.own("run/rec.json", b"new\n"))
        self.assertLessEqual(len(err.detail), cc._DETAIL_LIMIT + 200)
        self.assertNotIn("\x1b", err.detail)
        self.assertIn("esc?name.txt", err.detail)
        self.assertIn("403 in all", err.detail)  # the cut detail still says how many moved
        self.assertEqual(len(err.moved), 403)  # `.moved` names all of them, control byte and all
        self.assertIn("esc\x1bname.txt", err.moved)


class GraceThenKillTests(_Stalling):
    def test_a_timeout_sends_sigint_first_and_git_removes_its_own_index_lock(self):
        """Mutation: SIGKILL the group on timeout (the code before the fix) -> git is killed
        without cleanup and `index.lock` stays. The grace is patched long so a loaded machine
        cannot turn the wait into a kill."""
        self.hook(f': > "{self.ready}"\nsleep 60\n')
        head = self.head()
        with mock.patch.object(cc, "_GRACE_SECONDS", 60.0):
            self.commit_that_times_out(self.own("run/rec.json", b"new\n"))
        self.assertEqual(self.head(), head)
        self.assertFalse((self.repo / ".git" / "index.lock").exists())

    def test_a_git_that_ignores_sigint_is_killed_after_the_grace_and_leaves_its_lock(self):
        """SIGINT is dropped here, as a git that ignores it would. Mutation: skip the SIGKILL after
        the grace -> the hook shell outlives the call. Mutation: kill only git, not the group ->
        the same. Positive control: the first signal sent is SIGINT, then SIGKILL, and the lock
        the killed commit leaves is the residue the module docstring names."""
        pidfile = self.tmp / "hook.pid"
        self.hook(f'echo $$ > "{pidfile}"\n: > "{self.ready}"\nsleep 60\n')
        sent: list[int] = []
        real_killpg = os.killpg

        def drop_sigint(pgid, sig):
            sent.append(sig)
            if sig != signal.SIGINT:
                real_killpg(pgid, sig)

        with mock.patch.object(cc, "_GRACE_SECONDS", 0.5), mock.patch.object(cc.os, "killpg", drop_sigint):
            self.commit_that_times_out(self.own("run/rec.json", b"new\n"))
        self.assertEqual(sent, [signal.SIGINT, signal.SIGKILL])
        self.assertTrue((self.repo / ".git" / "index.lock").exists())
        hook_pid = int(pidfile.read_text())
        end = time.monotonic() + 10  # a killed process is a zombie until init reaps it
        while time.monotonic() < end:
            try:
                os.kill(hook_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        with self.assertRaises(ProcessLookupError):
            os.kill(hook_pid, 0)

    def test_a_hook_descendant_outside_the_group_holding_the_pipes_does_not_outlast_the_bound(self):
        """A hook starts a helper in its own session that inherits git's output pipes and lives
        60 s. Neither signal reaches it. Red when the killed call then waits for the pipes'
        end-of-file without a bound: it returns only when the helper exits, past the 40 s ceiling,
        which stays below the helper's life. Positive control: the helper is alive when the call
        returns, so the pipes were still held."""
        pidfile = self.tmp / "helper.pid"
        helper = self.tmp / "helper.py"
        helper.write_text(f"import os, time\nos.setsid()\nopen({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
                          "time.sleep(60)\n")
        self.hook(f'"{sys.executable}" "{helper}" &\n'
                  f'i=0\nwhile [ ! -s "{pidfile}" ] && [ $i -lt 1200 ]; do sleep 0.1; i=$((i+1)); done\n'
                  f': > "{self.ready}"\nsleep 60\n')

        def reap():
            try:
                os.kill(int(pidfile.read_text()), 9)
            except (OSError, ValueError):
                pass
        self.addCleanup(reap)
        with mock.patch.object(cc, "_GRACE_SECONDS", 0.5):
            start = time.monotonic()
            self.commit_that_times_out(self.own("run/rec.json", b"new\n"))
            self.assertLess(time.monotonic() - start, 40)
        os.kill(int(pidfile.read_text()), 0)  # raises when the helper is gone: the control failed

    def test_a_hook_member_that_ignores_sigint_and_detaches_its_stdio_cannot_move_work_after_the_verdict(self):
        """The hook starts a group member that ignores SIGINT and holds no output pipe, so git exits
        on SIGINT and the grace wait returns while the member lives on. The member changes clean.txt
        only once the test creates its `go` file, after the call has returned. Red when nothing
        probes the group after the grace: the member survives, writes clean.txt after the call said
        the outside state matched the snapshot, and its pid is alive. Positive control: the member
        was running when git was stopped (its pid file exists)."""
        pidfile, go = self.tmp / "member.pid", self.tmp / "go"
        member = (f"trap '' INT; echo $$ > \"{pidfile}\"; "
                  f"while [ ! -e \"{go}\" ]; do sleep 0.05; done; printf 'moved late\\n' > clean.txt")
        self.hook(f"sh -c '{member.replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}' "
                  "</dev/null >/dev/null 2>&1 &\n"
                  f'i=0\nwhile [ ! -s "{pidfile}" ] && [ $i -lt 1200 ]; do sleep 0.05; i=$((i+1)); done\n'
                  f': > "{self.ready}"\nsleep 60\n')

        def reap():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except (OSError, ValueError):
                pass
        self.addCleanup(reap)
        with mock.patch.object(cc, "_GRACE_SECONDS", 0.5):
            err = self.commit_that_times_out(self.own("run/rec.json", b"new\n"))
        self.assertTrue(pidfile.exists(), "control: the member never started")
        self.assertEqual(err.moved, ())
        go.touch()
        end = time.monotonic() + 3.0
        while time.monotonic() < end and (self.repo / "clean.txt").read_text() == "v1\n":
            time.sleep(0.05)
        self.assertEqual((self.repo / "clean.txt").read_text(), "v1\n")
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)

    def test_the_time_bound_covers_the_whole_call_not_each_git_child(self):
        """Every git child is slowed by 0.2 s and the hook sleeps 1 s, so the call needs more
        than 3 s; under a 2 s bound that is a refusal on every machine, however loaded. Mutation:
        give each git child the full `timeout` instead of the time left before one deadline -> the
        commit lands and no refusal is raised (a loaded machine can only add time, so this test
        never fails for load, though a very slow child can hide the mutation). Positive control:
        the same slowed call commits under a generous bound, and took more than 3 s."""
        self.hook("sleep 1\n")
        real = cc._run

        def slow(*args, **kw):
            time.sleep(0.2)
            return real(*args, **kw)

        with mock.patch.object(cc, "_run", slow), self.assertRaises(cc.CommitRefused) as ctx:
            cc.commit_owned(self.repo, self.own("run/rec.json", b"new\n"), "crux test", timeout=2.0)
        self.assertEqual(ctx.exception.code, "timeout")
        (self.repo / ".git" / "index.lock").unlink(missing_ok=True)
        with mock.patch.object(cc, "_run", slow):
            start = time.monotonic()
            sha = cc.commit_owned(self.repo, self.own("run/other.json", b"o\n"), "crux test", timeout=60.0)
            self.assertGreater(time.monotonic() - start, 3.0)
        self.assertEqual(sha, self.head())


if __name__ == "__main__":
    unittest.main()
