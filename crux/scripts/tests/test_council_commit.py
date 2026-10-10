"""`council_commit`: the runner-commit contract shared by the council runner, recovery and the
run-work witness writer.

Every test builds a real temporary git repository. Git configuration is isolated per test
(`isolated_git_config`), and a temporary CRUX_HOME holds a fake env file with a dummy key-shaped
value and a second fake name, so a test can prove both names are dropped from a hook's
environment. Each test names, in its docstring, the mutation of `council_commit` that turns it
red. Nothing reads the documentation tree or a real key.
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _council_gate_support as sup  # noqa: E402

import council_commit as cc  # noqa: E402
import council_records as cr  # noqa: E402
import crux_env  # noqa: E402

#: A dummy value in the openrouter key shape; never a real key.
FILE_KEY = "sk-or-v1-" + "0123456789abcdef" * 4
AMBIENT_KEY = "sk-or-v1-" + "fedcba9876543210" * 4
FAKE_NAME = "FAKE_CRUX_NAME"
FAKE_FILE_VALUE = "fake-file-value-not-a-shape-42"
FAKE_AMBIENT_VALUE = "fake-ambient-value-not-a-shape-43"


class _Repo(unittest.TestCase):
    """A repository with committed x.txt, y.txt, z.txt, clean.txt and run/rec.json, under an
    isolated git configuration and a fake CRUX_HOME. The ambient environment also carries the
    gateway key name and the fake env-file name, so dropping by name is observable."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name).resolve()
        self.home = self.tmp / "cruxhome"
        self.home.mkdir()
        (self.home / "env").write_text(f"OPENROUTER_API_KEY={FILE_KEY}\n{FAKE_NAME}={FAKE_FILE_VALUE}\n")
        env = {k: v for k, v in os.environ.items() if k not in cr.GIT_REDIRECT_VARS}
        env.update(sup.isolated_git_config(self.tmp / "gitcfg"))
        env.update({"CRUX_HOME": str(self.home), "OPENROUTER_API_KEY": AMBIENT_KEY,
                    FAKE_NAME: FAKE_AMBIENT_VALUE})
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        crux_env._reset_cache()
        self.addCleanup(crux_env._reset_cache)
        self.repo = sup.init_repo(self.tmp / "repo")
        for name in ("x.txt", "y.txt", "z.txt", "clean.txt"):
            (self.repo / name).write_text("v1\n")
        (self.repo / "run").mkdir()
        (self.repo / "run" / "rec.json").write_bytes(b"old\n")
        sup.commit_all(self.repo, "base")

    # -- helpers --------------------------------------------------------------

    def hook(self, body: str, name: str = "pre-commit") -> None:
        path = self.repo / ".git" / "hooks" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)

    def head(self) -> str:
        return sup.git(self.repo, "rev-parse", "HEAD").strip()

    def index(self) -> str:
        return sup.git(self.repo, "ls-files", "-s")

    def index_outside(self, owned) -> list[str]:
        return [line for line in self.index().splitlines() if line.split("\t", 1)[1] not in owned]

    def changed_in(self, commit: str) -> list[str]:
        return sorted(sup.git(self.repo, "diff-tree", "-r", "--no-commit-id", "--name-only", commit).split())

    def own(self, rel: str, data: bytes) -> dict[str, bytes]:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {rel: data}

    def refused(self, code: str, *args, **kw) -> cc.CommitRefused:
        with self.assertRaises(cc.CommitRefused) as ctx:
            cc.commit_owned(self.repo, *args, **kw)
        self.assertEqual(ctx.exception.code, code, str(ctx.exception))
        return ctx.exception


# ───────────────────────────── A1: the git child environment ─────────────────────────────


class GitChildEnvTests(_Repo):
    def test_the_child_env_drops_the_key_every_env_file_name_and_the_redirect_vars(self):
        """Mutation: return `dict(os.environ)` (or `council_records.git_isolated_env()`) from
        `git_child_env` -> OPENROUTER_API_KEY and FAKE_CRUX_NAME are present."""
        os.environ["GIT_DIR"] = str(self.tmp / "elsewhere")
        env = cc.git_child_env()
        self.assertNotIn("OPENROUTER_API_KEY", env)
        self.assertNotIn(FAKE_NAME, env)
        self.assertNotIn("GIT_DIR", env)
        joined = "\n".join(f"{k}={v}" for k, v in env.items())
        for value in (FILE_KEY, AMBIENT_KEY, FAKE_FILE_VALUE, FAKE_AMBIENT_VALUE):
            self.assertNotIn(value, joined)
        self.assertEqual(env["GIT_LITERAL_PATHSPECS"], "1")
        self.assertEqual(env["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertEqual(env["GIT_GRAFT_FILE"], cr.NO_GRAFT_FILE)
        # Positive control: the user's global configuration is honoured, never nulled.
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], os.environ["GIT_CONFIG_GLOBAL"])
        self.assertNotEqual(env.get("GIT_CONFIG_SYSTEM"), os.devnull)
        self.assertEqual(env["CRUX_HOME"], str(self.home))

    def test_a_malformed_env_file_raises_env_unreadable_without_relaying_its_text(self):
        """Mutation: let crux_env's ValueError (whose text quotes the line) escape, or chain it
        -> the key-shaped text reaches the exception or its context."""
        (self.home / "env").write_text(f"BROKEN LINE {FILE_KEY}\n")
        crux_env._reset_cache()
        with self.assertRaises(cc.EnvUnreadable) as ctx:
            cc.git_child_env()
        self.assertNotIn(FILE_KEY, str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue(ctx.exception.__suppress_context__)
        before = self.head()
        data = b"new\n"
        with self.assertRaises(cc.EnvUnreadable):
            cc.commit_owned(self.repo, self.own("run/rec.json", data), "crux test")
        self.assertEqual(self.head(), before)

    def test_a_hooks_environment_carries_no_key_and_no_env_file_name(self):
        """Mutation: run git children with `council_records.git_isolated_env()` -> the dump holds
        OPENROUTER_API_KEY and FAKE_CRUX_NAME. Positive control: the dump exists and carries the
        hook's GIT_INDEX_FILE, and the commit carries the identity from the global config."""
        dump = self.tmp / "hook-env.txt"
        self.hook(f'env > "{dump}"\n')
        cc.commit_owned(self.repo, self.own("run/rec.json", b"new\n"), "crux test")
        text = dump.read_text()
        self.assertIn("GIT_INDEX_FILE=", text)
        for needle in ("OPENROUTER_API_KEY", FAKE_NAME, FILE_KEY, AMBIENT_KEY, FAKE_FILE_VALUE,
                       FAKE_AMBIENT_VALUE):
            self.assertNotIn(needle, text)
        self.assertEqual(sup.git(self.repo, "log", "-1", "--format=%an <%ae>").strip(),
                         "t <t@example.invalid>")


class SequenceAndLockTests(_Repo):
    def test_each_in_progress_sequence_is_seen_and_bisect_is_not(self):
        """Mutation: drop a marker from the sequence list -> that marker reads as no sequence.
        Positive control: a clean repository and a bisect read as none."""
        self.assertFalse(cc.sequence_in_progress(self.repo))
        gd = self.repo / ".git"
        (gd / "BISECT_LOG").write_text("x\n")
        (gd / "BISECT_START").write_text("main\n")
        self.assertFalse(cc.sequence_in_progress(self.repo))
        for marker, is_dir in (("MERGE_HEAD", False), ("CHERRY_PICK_HEAD", False),
                               ("REVERT_HEAD", False), ("rebase-merge", True),
                               ("rebase-apply", True), ("sequencer", True)):
            with self.subTest(marker=marker):
                path = gd / marker
                if is_dir:
                    path.mkdir()
                    if marker == "rebase-apply":
                        (path / "applying").write_text("")  # an am in progress
                else:
                    path.write_text(self.head() + "\n")
                self.assertTrue(cc.sequence_in_progress(self.repo))
                if is_dir:
                    for child in path.iterdir():
                        child.unlink()
                    path.rmdir()
                else:
                    path.unlink()
                self.assertFalse(cc.sequence_in_progress(self.repo))

    def test_a_merge_in_progress_refuses_and_commits_nothing(self):
        """Mutation: skip the sequence check in `commit_owned` -> git's own refusal surfaces as
        `hook-or-commit-failed`, not `sequence-in-progress`."""
        (self.repo / ".git" / "MERGE_HEAD").write_text(self.head() + "\n")
        before, index = self.head(), self.index()
        self.refused("sequence-in-progress", self.own("run/new.json", b"n\n"), "crux test")
        self.assertEqual(self.head(), before)
        self.assertEqual(self.index(), index)

    def test_a_held_index_lock_refuses_and_commits_nothing(self):
        """Mutation: skip the index-lock check -> `git add`/`git commit` fail and the code is
        `hook-or-commit-failed`. Positive control: `index_locked` is false without the lock."""
        self.assertFalse(cc.index_locked(self.repo))
        (self.repo / ".git" / "index.lock").write_text("")
        self.assertTrue(cc.index_locked(self.repo))
        before = self.head()
        self.refused("index-locked", self.own("run/rec.json", b"new\n"), "crux test")
        self.assertEqual(self.head(), before)


class PendingCopyTests(_Repo):
    def test_a_pending_copy_is_written_once_and_removed(self):
        """Mutation: open without O_EXCL -> the second write succeeds and overwrites."""
        path = cc.write_pending(self.repo, "PB-0999", "RUN-001", "rec.json", b"bytes\n")
        self.assertEqual(path, cr.pending_dir(self.repo, "PB-0999", "RUN-001") / "rec.json")
        self.assertEqual(path.read_bytes(), b"bytes\n")
        with self.assertRaises(FileExistsError):
            cc.write_pending(self.repo, "PB-0999", "RUN-001", "rec.json", b"other\n")
        self.assertEqual(path.read_bytes(), b"bytes\n")
        cc.remove_pending(self.repo, "PB-0999", "RUN-001", "rec.json")
        self.assertFalse(path.exists())

    def test_a_symlinked_pending_directory_is_refused_and_nothing_lands_behind_it(self):
        """Mutation: create directories with `mkdir(parents=True)` and follow symlinks -> the
        file lands in the symlink's target."""
        target = self.tmp / "elsewhere"
        target.mkdir()
        (self.repo / ".git" / "crux").symlink_to(target, target_is_directory=True)
        with self.assertRaises(OSError):
            cc.write_pending(self.repo, "PB-0999", "RUN-001", "rec.json", b"bytes\n")
        self.assertEqual(list(target.rglob("*")), [])

    def test_a_failed_write_or_flush_leaves_no_partial_pending_copy(self):
        """Mutation: re-raise without unlinking the file -> a partial copy stays, and the retry
        raises FileExistsError. Positive control: after each injected failure a clean write
        succeeds with the full bytes."""
        real_write, real_fsync = os.write, os.fsync

        def short_then_fail(fd, data):
            real_write(fd, bytes(data[:3]))
            raise OSError(28, "No space left on device")

        def fail_on_file(fd):
            if stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError(5, "Input/output error")
            return real_fsync(fd)

        path = cr.pending_dir(self.repo, "PB-0999", "RUN-001") / "rec.json"
        for what, target, fake in (("write", "write", short_then_fail), ("fsync", "fsync", fail_on_file)):
            with self.subTest(failure=what):
                with mock.patch.object(cc.os, target, fake), self.assertRaises(OSError):
                    cc.write_pending(self.repo, "PB-0999", "RUN-001", "rec.json", b"bytes\n")
                self.assertFalse(os.path.lexists(path))
                cc.write_pending(self.repo, "PB-0999", "RUN-001", "rec.json", b"bytes\n")
                self.assertEqual(path.read_bytes(), b"bytes\n")
                cc.remove_pending(self.repo, "PB-0999", "RUN-001", "rec.json")

    def test_a_name_that_is_not_one_component_is_refused(self):
        """Mutation: accept any name -> `../escape` writes outside the pending directory."""
        for bad in ("../escape", "a/b", "", ".", ".."):
            with self.subTest(name=bad), self.assertRaises(ValueError):
                cc.write_pending(self.repo, "PB-0999", "RUN-001", bad, b"x")


class ScanOutputTests(unittest.TestCase):
    def test_a_key_shaped_match_is_withheld_by_label(self):
        """Mutation: return the text unchanged -> the key-shaped value is relayed."""
        out = cc.scan_output(f"hook said {FILE_KEY} then failed")
        self.assertNotIn(FILE_KEY, out)
        self.assertIn("openrouter-key", out)

    def test_an_exact_value_is_withheld_and_clean_text_passes_through(self):
        """Mutation: ignore `exact` -> the exact value is relayed. Positive control: clean text
        is returned unchanged."""
        self.assertEqual(cc.scan_output("plain hook output"), "plain hook output")
        out = cc.scan_output("value=plainsecretvalue99", exact=("plainsecretvalue99",))
        self.assertNotIn("plainsecretvalue99", out)
        self.assertIn("exact-key", out)


# ───────────────────────────── A2: commit_owned and verify_commit ─────────────────────────────


class CommitOwnedTests(_Repo):
    def test_a_commit_holds_exactly_the_owned_paths_and_adds_a_new_path_explicitly(self):
        """Mutation: drop the explicit `git add` of a path not in the index -> `git commit --only`
        refuses the unknown path (`hook-or-commit-failed`)."""
        before = self.head()
        owned = {**self.own("run/rec.json", b"new\n"), **self.own("run/new.json", b"fresh\n")}
        sha = cc.commit_owned(self.repo, owned, "crux council record: PB-0999 RUN-001 prompt 2 round 1")
        self.assertEqual(sha, self.head())
        self.assertEqual(sup.git(self.repo, "rev-parse", f"{sha}^").strip(), before)
        self.assertEqual(self.changed_in(sha), ["run/new.json", "run/rec.json"])
        self.assertEqual(sup.git(self.repo, "log", "-1", "--format=%s").strip(),
                         "crux council record: PB-0999 RUN-001 prompt 2 round 1")
        self.assertEqual(sup.git(self.repo, "status", "--porcelain"), "")

    def test_staged_unstaged_partially_staged_and_untracked_changes_stay_as_they_were(self):
        """Mutation: commit with `-a` (or `git add -A` first) -> staged x.txt lands in the
        council commit and the index outside the owned paths moves."""
        r = self.repo
        (r / "x.txt").write_text("x staged\n")
        sup.git(r, "add", "--", "x.txt")
        (r / "y.txt").write_text("y unstaged\n")
        (r / "z.txt").write_text("z staged\n")
        sup.git(r, "add", "--", "z.txt")
        (r / "z.txt").write_text("z staged then edited\n")
        (r / "w.txt").write_text("w untracked\n")
        owned = {**self.own("run/rec.json", b"new\n"), **self.own("run/new.json", b"fresh\n")}
        idx_before = self.index_outside(owned)
        wt_before = {n: (r / n).read_bytes() for n in ("x.txt", "y.txt", "z.txt", "w.txt")}
        sha = cc.commit_owned(r, owned, "crux test")
        self.assertEqual(self.changed_in(sha), ["run/new.json", "run/rec.json"])
        self.assertEqual(self.index_outside(owned), idx_before)
        self.assertEqual({n: (r / n).read_bytes() for n in wt_before}, wt_before)
        self.assertEqual(sup.git(r, "diff", "--cached", "--name-only").split(), ["x.txt", "z.txt"])
        self.assertEqual(sup.git(r, "diff", "--name-only").split(), ["y.txt", "z.txt"])
        self.assertIn("w.txt", sup.git(r, "ls-files", "--others", "--exclude-standard").split())

    def test_working_tree_bytes_that_differ_from_the_owned_bytes_refuse_before_any_commit(self):
        """Mutation: skip the pre-commit byte check -> the working-tree bytes are committed and
        only verification (after the commit lands) objects."""
        (self.repo / "run" / "rec.json").write_bytes(b"what is on disk\n")
        before, index = self.head(), self.index()
        self.refused("mismatch", {"run/rec.json": b"what the runner computed\n"}, "crux test")
        self.assertEqual(self.head(), before)
        self.assertEqual(self.index(), index)

    def test_an_owned_path_whose_index_holds_another_staged_version_is_refused_before_any_commit(self):
        """`git commit --only -- P` resets P's index entry, so a staged version that is neither
        HEAD's nor the owned bytes would be lost. Mutation: drop the index guard from
        `commit_owned` -> the commit lands and the staged version is gone. Positive control: a
        staged version equal to the owned bytes commits."""
        r = self.repo
        (r / "x.txt").write_text("USER STAGED\n")
        sup.git(r, "add", "--", "x.txt")
        before = self.head()
        err = self.refused("mismatch", self.own("x.txt", b"runner bytes\n"), "crux test")
        self.assertIn("x.txt", err.detail)
        self.assertEqual(self.head(), before)
        self.assertEqual(sup.git(r, "show", ":x.txt"), "USER STAGED\n")
        self.assertEqual((r / "x.txt").read_bytes(), b"runner bytes\n")
        sup.git(r, "add", "--", "x.txt")  # the staged version is now the owned bytes
        sha = cc.commit_owned(r, {"x.txt": b"runner bytes\n"}, "crux test")
        self.assertEqual(self.changed_in(sha), ["x.txt"])

    def test_an_owned_path_with_a_staged_deletion_is_refused_before_any_commit(self):
        """A staged `git rm --cached` is an index state that is neither HEAD's nor the owned
        bytes. Mutation: treat a missing index entry as no staged change -> the commit re-adds the
        path and the staged deletion is gone."""
        sup.git(self.repo, "rm", "-q", "--cached", "--", "x.txt")
        before = self.head()
        self.refused("mismatch", self.own("x.txt", b"runner bytes\n"), "crux test")
        self.assertEqual(self.head(), before)
        self.assertNotIn("x.txt", sup.git(self.repo, "ls-files").split())

    def test_an_intent_to_add_entry_is_no_staged_version_and_the_commit_lands(self):
        """`git add -N` records that a path will be added and stages no bytes, so committing over
        it loses nothing. Mutation: read the entry's empty blob as a staged version -> refused
        with `mismatch` and a message claiming the index holds a staged version. Positive control:
        a really staged empty file still refuses (the next test)."""
        r = self.repo
        owned = self.own("run/ita.json", b"new\n")
        sup.git(r, "add", "-N", "--", "run/ita.json")
        self.assertIn("run/ita.json", sup.git(r, "ls-files").split())
        state = cc.path_state(r, "run/ita.json")
        self.assertEqual((state.head, state.index, state.unmerged), (None, None, False))
        sha = cc.commit_owned(r, owned, "crux test")
        self.assertEqual(self.changed_in(sha), ["run/ita.json"])
        self.assertEqual(sup.git(r, "show", "HEAD:run/ita.json"), "new\n")
        self.assertEqual(sup.git(r, "show", ":run/ita.json"), "new\n")

    def test_a_staged_empty_file_is_a_staged_version_and_refuses(self):
        """The control for the intent-to-add case: an empty file staged with `git add` holds the
        same empty blob, and it is a real staged version. Mutation: treat every empty-blob entry
        as intent-to-add -> the commit lands over the staged empty file."""
        r = self.repo
        (r / "run" / "empty.json").write_bytes(b"")
        sup.git(r, "add", "--", "run/empty.json")
        before = self.head()
        self.refused("mismatch", self.own("run/empty.json", b"new\n"), "crux test")
        self.assertEqual(self.head(), before)
        self.assertEqual(sup.git(r, "show", ":run/empty.json"), "")

    def test_a_hook_rejection_is_hook_or_commit_failed_and_leaves_the_index_as_it_was(self):
        """Mutation: leave the explicitly added new path staged after a failed commit -> the
        index differs from before. Mutation: map a nonzero commit to success -> no refusal."""
        (self.repo / "x.txt").write_text("x staged\n")
        sup.git(self.repo, "add", "--", "x.txt")
        self.hook("echo rejected-by-hook >&2\nexit 1\n")
        before, index = self.head(), self.index()
        owned = {**self.own("run/rec.json", b"new\n"), **self.own("run/new.json", b"fresh\n")}
        err = self.refused("hook-or-commit-failed", owned, "crux test")
        self.assertIn("rejected-by-hook", err.detail)
        self.assertEqual(self.head(), before)
        self.assertEqual(self.index(), index)
        self.assertEqual(err.moved, ())  # a hook that touched nothing outside names nothing

    def test_a_failing_hook_that_left_the_users_work_set_aside_names_every_moved_path(self):
        """A `set -e` hook sets the unstaged y.txt aside, a lint step fails, and the hook exits
        before its restore runs. Mutation: compare the outside state only after a timeout (the code
        before the fix) -> `.moved` is empty and the detail is silent about y.txt. Positive
        controls: y.txt did move, and clean.txt, which the hook did not touch, is not named."""
        (self.repo / "y.txt").write_text("y unstaged\n")
        backup = self.tmp / "hook-backup"
        self.hook(f'set -e\ncp y.txt "{backup}"\nprintf "v1\\n" > y.txt\nfalse\ncp "{backup}" y.txt\n')
        err = self.refused("hook-or-commit-failed", self.own("run/rec.json", b"new\n"), "crux test")
        self.assertEqual(err.moved, ("y.txt",))
        self.assertIn("y.txt", err.detail)
        self.assertIn("git stash list", err.detail)
        self.assertNotIn("clean.txt", err.detail)
        self.assertNotIn("git stash list", err.base)  # the stop without the outside-state clause
        self.assertEqual((self.repo / "y.txt").read_text(), "v1\n")  # the control: it did move

    def test_long_hook_output_keeps_the_moved_paths_and_the_remedy_in_the_detail(self):
        """A lint hook prints ~400 lines (well over `_DETAIL_LIMIT`), sets the unstaged y.txt aside
        and fails before its restore. Red when the joined detail is bounded from its end: the
        hook's output fills the bound, and the moved path and the remedy order are cut off. The
        hook's own last line stays too, so the bound still keeps the end of the hook's output."""
        (self.repo / "y.txt").write_text("y unstaged\n")
        backup = self.tmp / "hook-backup"
        lint = "i=0\nwhile [ $i -lt 400 ]; do echo \"src/mod_$i.py:1:1: E501 line too long (120 > 79)\" >&2; i=$((i+1)); done\n"
        self.hook(f'set -e\ncp y.txt "{backup}"\nprintf "v1\\n" > y.txt\n{lint}echo lint-failed-last >&2\nfalse\n'
                  f'cp "{backup}" y.txt\n')
        err = self.refused("hook-or-commit-failed", self.own("run/rec.json", b"new\n"), "crux test")
        self.assertEqual(err.moved, ("y.txt",))
        self.assertGreater(len(err.base), cc._DETAIL_LIMIT)  # the control: the hook output does fill the bound
        self.assertIn("git stash list", err.detail)
        self.assertIn("moved: y.txt", err.detail)
        self.assertIn("lint-failed-last", err.detail)
        self.assertLess(err.detail.index("git stash list"), err.detail.index("moved: y.txt"))

    def test_hook_output_carrying_a_key_shape_is_withheld_by_label(self):
        """Mutation: put raw git output in the refusal detail -> the key-shaped value is relayed.
        Positive control: the clean-output rejection above relays its text."""
        self.hook(f"echo leaked {FILE_KEY} >&2\nexit 1\n")
        err = self.refused("hook-or-commit-failed", self.own("run/rec.json", b"new\n"), "crux test")
        self.assertNotIn(FILE_KEY, str(err))
        self.assertNotIn(FILE_KEY, err.detail)
        self.assertIn("openrouter-key", err.detail)

    def test_a_hook_that_reformats_an_owned_file_and_re_adds_it_is_a_mismatch(self):
        """Mutation: trust git's exit code (skip `verify_commit`) -> the reformatted commit is
        reported as success."""
        self.hook("printf 'REFORMATTED\\n' > run/rec.json\ngit add run/rec.json\n")
        owned = self.own("run/rec.json", b"new\n")
        self.refused("mismatch", owned, "crux test")
        self.assertEqual(sup.git(self.repo, "show", "HEAD:run/rec.json"), "REFORMATTED\n")

    def test_a_hook_that_edits_a_file_outside_the_owned_set_is_a_mismatch(self):
        """Mutation: drop the outside working-tree comparison from `verify_commit` -> success
        while clean.txt silently changed."""
        self.hook("printf 'hook wrote this\\n' > clean.txt\n")
        self.refused("mismatch", self.own("run/rec.json", b"new\n"), "crux test")

    def test_a_hook_that_stashes_and_restores_unstaged_changes_keeps_them(self):
        """Mutation: compare outside working-tree state by stat (mtime) instead of bytes -> the
        restored y.txt reads as moved and an intact tree is refused. The paired test below shows
        the same hook without its restore is reported, never silent."""
        save = self.tmp / "stash-save"
        (self.repo / "y.txt").write_text("y unstaged\n")
        self.hook(f'cp y.txt "{save}"\nprintf "v1\\n" > y.txt\ncp "{save}" y.txt\n')
        cc.commit_owned(self.repo, self.own("run/rec.json", b"new\n"), "crux test")
        self.assertEqual((self.repo / "y.txt").read_text(), "y unstaged\n")

    def test_a_hook_that_stashes_unstaged_changes_without_restoring_them_is_a_mismatch(self):
        """Mutation: drop the outside working-tree comparison -> the lost unstaged change goes
        unreported (silent loss)."""
        (self.repo / "y.txt").write_text("y unstaged\n")
        self.hook('printf "v1\\n" > y.txt\n')
        self.refused("mismatch", self.own("run/rec.json", b"new\n"), "crux test")

    def test_a_post_commit_hook_that_rewrites_an_outside_index_entry_is_a_mismatch(self):
        """y.txt is already modified in the working tree, and the hook points its real-index
        entry at a third blob, so `ls-files -m` lists y.txt before and after and the working-tree
        comparison cannot see the change. Mutation: make the outside-index comparison in
        `verify_commit` `if False:` -> success while the index silently moved."""
        (self.repo / "y.txt").write_text("y unstaged\n")
        third = self.tmp / "third"
        third.write_text("third version\n")
        oid = sup.git(self.repo, "hash-object", "-w", str(third)).strip()
        self.hook(f"git update-index --cacheinfo 100644,{oid},y.txt\n", name="post-commit")
        err = self.refused("mismatch", self.own("run/rec.json", b"new\n"), "crux test")
        self.assertIn("index entries outside", err.detail)
        self.assertIn("y.txt", sup.git(self.repo, "diff", "--name-only").split())

    def test_a_hook_that_stages_a_file_outside_the_owned_set_is_a_mismatch(self):
        """Mutation: drop the outside index comparison and the changed-paths check -> success
        with x.txt in the council commit."""
        (self.repo / "x.txt").write_text("x edited\n")
        self.hook("git add x.txt\n")
        self.refused("mismatch", self.own("run/rec.json", b"new\n"), "crux test")

    # The time-bound tests (SIGINT grace, the kill, the pipes, the whole-call deadline, what a
    # timeout names) live in test_council_commit_timeout.py.

    def test_a_sealed_path_whose_bytes_fail_the_seal_refuses_before_any_commit(self):
        """Mutation: ignore `sealed` -> the unsealed bytes are committed and returned as success.
        Positive control: canonical sealed bytes commit."""
        before = self.head()
        bad = b'{"seal": "sha256:' + b"0" * 64 + b'"}\n'
        self.refused("mismatch", self.own("run/rec.json", bad), "crux test", sealed=("run/rec.json",))
        self.assertEqual(self.head(), before)
        good = cr.canonical_bytes(cr.sealed({"a": 1, "seal": None}))
        cc.commit_owned(self.repo, self.own("run/rec.json", good), "crux test", sealed=("run/rec.json",))
        self.assertEqual(self.changed_in(self.head()), ["run/rec.json"])

    def test_verify_commit_refuses_committed_bytes_that_fail_the_seal(self):
        """Mutation: drop the seal check from `verify_commit` -> it returns the new HEAD.
        Positive control: the same commit verifies without `sealed`."""
        bad = b'{"seal": "sha256:' + b"0" * 64 + b'"}\n'
        owned = self.own("run/rec.json", bad)
        snap = cc.snapshot(self.repo, owned, cc.git_child_env())
        sup.git(self.repo, "add", "--", "run/rec.json")
        sup.git(self.repo, "commit", "-q", "-m", "c", "--", "run/rec.json")
        with self.assertRaises(cc.CommitRefused) as ctx:
            cc.verify_commit(self.repo, snap, owned, sealed=("run/rec.json",))
        self.assertEqual(ctx.exception.code, "mismatch")
        self.assertEqual(cc.verify_commit(self.repo, snap, owned), self.head())

    def test_a_failed_commit_names_the_never_tracked_owned_path_it_could_not_unstage(self):
        """A hook rewrites the owned file and fails, so `git rm --cached` refuses (the staged
        bytes differ from the file and from HEAD) and the path stays staged. Mutation: leave
        `.staged` empty on `hook-or-commit-failed` (the code before the fix) -> the refusal names
        nothing it left behind. Positive control: a hook that only fails leaves nothing staged and
        `.staged` empty."""
        self.hook("echo no >&2\nexit 1\n")
        err = self.refused("hook-or-commit-failed", self.own("run/new.json", b"fresh\n"), "crux test")
        self.assertEqual(err.staged, ())
        self.assertNotIn("run/new.json", sup.git(self.repo, "ls-files").split())
        self.hook("printf 'REWRITTEN\\n' > run/new.json\nexit 1\n")
        err = self.refused("hook-or-commit-failed", self.own("run/new.json", b"fresh\n"), "crux test")
        self.assertEqual(err.staged, ("run/new.json",))
        self.assertIn("run/new.json", sup.git(self.repo, "ls-files").split())

    def test_hook_output_with_terminal_escapes_reaches_the_detail_without_control_characters(self):
        """Mutation: relay the scanned output unchanged -> the escape byte reaches the detail (and
        a terminal that prints it). Newline and tab are kept; carriage return and escape are not."""
        self.hook("printf '\\033[31mdanger\\033[0m\\ttab\\rcr\\nline2\\n' >&2\nexit 1\n")
        err = self.refused("hook-or-commit-failed", self.own("run/rec.json", b"new\n"), "crux test")
        self.assertNotIn("\x1b", err.detail)
        self.assertNotIn("\r", err.detail)
        self.assertIn("?[31mdanger?[0m\ttab?cr\nline2", err.detail)

    def test_hook_output_with_c1_controls_del_and_line_separators_is_neutralised(self):
        """Mutation: replace only C0 controls -> DEL, the C1 control-sequence introducer (U+009B)
        and U+2028 reach the detail. Positive control: ordinary text around them survives."""
        self.hook("printf 'a\\177b\\302\\233c\\342\\200\\250d\\n' >&2\nexit 1\n")
        err = self.refused("hook-or-commit-failed", self.own("run/rec.json", b"new\n"), "crux test")
        for bad in ("\x7f", "\u009b", "\u2028"):
            self.assertNotIn(bad, err.detail)
        self.assertIn("a?b?c?d", err.detail)

    def test_verify_commit_refuses_a_head_whose_parent_is_not_the_old_head(self):
        """Mutation: drop the parent check -> a second unrelated commit on top verifies."""
        owned = self.own("run/rec.json", b"new\n")
        snap = cc.snapshot(self.repo, owned, cc.git_child_env())
        sup.git(self.repo, "add", "--", "run/rec.json")
        sup.git(self.repo, "commit", "-q", "-m", "c", "--", "run/rec.json")
        sup.git(self.repo, "commit", "-q", "--allow-empty", "-m", "another")
        with self.assertRaises(cc.CommitRefused) as ctx:
            cc.verify_commit(self.repo, snap, owned)
        self.assertEqual(ctx.exception.code, "mismatch")

    # -- one case per remaining verify_commit check: the code `mismatch`, the detail naming the
    # -- check, and the outside state unchanged. Each states the mutation that disables only it.

    def _verified_commit(self):
        """A snapshot, then a commit of run/rec.json made by hand, as `commit_owned` would."""
        owned = self.own("run/rec.json", b"new\n")
        snap = cc.snapshot(self.repo, owned, cc.git_child_env())
        sup.git(self.repo, "add", "--", "run/rec.json")
        sup.git(self.repo, "commit", "-q", "-m", "c", "--", "run/rec.json")
        return owned, snap

    def assert_verify_refuses(self, snap, owned, needle):
        with self.assertRaises(cc.CommitRefused) as ctx:
            cc.verify_commit(self.repo, snap, owned)
        self.assertEqual(ctx.exception.code, "mismatch")
        self.assertIn(needle, ctx.exception.detail)

    def test_verify_commit_refuses_a_head_that_did_not_move(self):
        """Mutation: drop the `new == before.head` test -> the parent check refuses instead, with
        a different detail, so the needle is absent. Positive control: after a real commit the same
        snapshot verifies."""
        owned = self.own("run/rec.json", b"new\n")
        snap = cc.snapshot(self.repo, owned, cc.git_child_env())
        index, head = self.index(), self.head()
        self.assert_verify_refuses(snap, owned, "HEAD did not move to a new commit")
        self.assertEqual((self.index(), self.head()), (index, head))
        sup.git(self.repo, "add", "--", "run/rec.json")
        sup.git(self.repo, "commit", "-q", "-m", "c", "--", "run/rec.json")
        self.assertEqual(cc.verify_commit(self.repo, snap, owned), self.head())

    def test_verify_commit_refuses_an_index_entry_that_is_not_one_regular_stage_zero_entry(self):
        """The index entry for the owned path is turned into a symlink entry after the commit
        (same blob, mode 120000). Mutation: drop the entry-shape check -> every later check
        passes (blob and file bytes are the owned bytes) and `verify_commit` returns. Positive
        control: a regular entry verifies."""
        owned, snap = self._verified_commit()
        oid = sup.git(self.repo, "rev-parse", "HEAD:run/rec.json").strip()
        sup.git(self.repo, "update-index", "--cacheinfo", f"120000,{oid},run/rec.json")
        idx = self.index_outside(owned)
        self.assert_verify_refuses(snap, owned, "the index does not hold one regular stage-0 entry")
        self.assertEqual(self.index_outside(owned), idx)
        sup.git(self.repo, "update-index", "--cacheinfo", f"100644,{oid},run/rec.json")
        self.assertEqual(cc.verify_commit(self.repo, snap, owned), self.head())

    def test_verify_commit_refuses_index_bytes_that_differ_from_the_computed_bytes(self):
        """After the commit the index entry is pointed at another blob. Mutation: drop the
        index-bytes comparison -> the working-tree file still equals the owned bytes and
        `verify_commit` returns. Positive control: restoring the entry verifies."""
        owned, snap = self._verified_commit()
        other = self.tmp / "other"
        other.write_text("other version\n")
        wrong = sup.git(self.repo, "hash-object", "-w", str(other)).strip()
        good = sup.git(self.repo, "rev-parse", "HEAD:run/rec.json").strip()
        sup.git(self.repo, "update-index", "--cacheinfo", f"100644,{wrong},run/rec.json")
        idx = self.index_outside(owned)
        self.assert_verify_refuses(snap, owned, "the index bytes differ from the computed bytes")
        self.assertEqual(self.index_outside(owned), idx)
        sup.git(self.repo, "update-index", "--cacheinfo", f"100644,{good},run/rec.json")
        self.assertEqual(cc.verify_commit(self.repo, snap, owned), self.head())

    def test_verify_commit_refuses_working_tree_bytes_that_differ_from_the_computed_bytes(self):
        """Direct case: the owned file is rewritten after the commit and not re-added. Mutation:
        drop the working-tree comparison -> `verify_commit` returns. Positive control: restoring
        the bytes verifies."""
        owned, snap = self._verified_commit()
        (self.repo / "run" / "rec.json").write_bytes(b"rewritten\n")
        self.assert_verify_refuses(snap, owned, "the working-tree bytes differ from the computed bytes")
        (self.repo / "run" / "rec.json").write_bytes(b"new\n")
        self.assertEqual(cc.verify_commit(self.repo, snap, owned), self.head())

    def test_a_pre_commit_hook_that_rewrites_the_owned_file_without_re_adding_it_is_a_mismatch(self):
        """Through `commit_owned`: the hook rewrites the owned file and does not `git add` it, so
        the commit and the index hold the computed bytes and only the working-tree file differs.
        Mutation: drop the working-tree comparison -> success while the file on disk is not the
        record that was committed. Outside state is unchanged."""
        self.hook("printf 'REWRITTEN\\n' > run/rec.json\n")
        outside = {n: (self.repo / n).read_bytes() for n in ("x.txt", "y.txt", "z.txt", "clean.txt")}
        err = self.refused("mismatch", self.own("run/rec.json", b"new\n"), "crux test")
        self.assertIn("the working-tree bytes differ from the computed bytes", err.detail)
        self.assertEqual(sup.git(self.repo, "show", "HEAD:run/rec.json"), "new\n")
        self.assertEqual({n: (self.repo / n).read_bytes() for n in outside}, outside)
        self.assertEqual(sup.git(self.repo, "status", "--porcelain", "--", "x.txt", "y.txt", "z.txt",
                                 "clean.txt"), "")

    def test_an_owned_path_outside_the_repository_is_refused(self):
        """Mutation: skip path validation -> git is asked to commit an escaping path."""
        with self.assertRaises(ValueError):
            cc.commit_owned(self.repo, {"../escape.txt": b"x"}, "crux test")
        with self.assertRaises(ValueError):
            cc.commit_owned(self.repo, {}, "crux test")


class CommitOwnedSubprocessTests(_Repo):
    def test_no_git_child_is_given_no_verify_and_hooks_run(self):
        """Mutation: pass `--no-verify` to `git commit` -> the hook marker is never written."""
        marker = self.tmp / "hook-ran"
        self.hook(f'touch "{marker}"\n')
        cc.commit_owned(self.repo, self.own("run/rec.json", b"new\n"), "crux test")
        self.assertTrue(marker.is_file())


if __name__ == "__main__":
    unittest.main()
