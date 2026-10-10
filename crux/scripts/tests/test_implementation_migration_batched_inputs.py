"""PB-0145 #15: `_unchanged_inputs` takes a batched, accept-only fast path.

The fast path spawns a bounded number of git children for any number of inputs and decides
nothing the per-path check (`_unchanged_input`) would refuse. Fixture trees live in temp dirs.
"""
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

import _council_gate_support as sup
import implementation_approval as ap
import implementation_migration as migration
import test_implementation_migration_shared_reads as shared

FILES = {"docs/adrs/ADR-0001-x.md": "# one\n", "docs/a b.md": "space\n", "docs/new\nline.md": "newline\n",
         "docs/tab\there.md": "tab\n", "docs/new": "decoy\n", "docs/plain.md": "plain\n"}
T = "docs/plain.md"
NL = "docs/new\nline.md"


def git(root, *args, stdin=None):
    # The support module's scrubbed environment: an exported GIT_DIR never redirects a fixture
    # command into another repository, and no machine git configuration is read.
    env = sup.scrubbed_env()
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    done = subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          input=stdin, capture_output=True, env=env)
    assert done.returncode == 0, (args, done.stderr)
    return done.stdout.decode()


def fresh(files=FILES):
    root = Path(tempfile.mkdtemp(prefix="pb0145-15-")).resolve()
    try:
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "--local", "gc.auto", "0")
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "base")
    except BaseException:
        # The caller never receives the root of a failed fixture, so nothing else removes it.
        shutil.rmtree(root, ignore_errors=True)
        raise
    return root, [{"path": rel, "sha256": hashlib.sha256(text.encode()).hexdigest()} for rel, text in files.items()]


def per_path(repo, fingerprints):
    for item in fingerprints:
        migration._unchanged_input(repo, item)


def outcome(function, root, fingerprints):
    try:
        function(root, fingerprints)
        return "accepted"
    except migration.Refused as refused:
        return refused.code


class ChildCounter:
    """Counts git children started through subprocess.Popen."""
    def __init__(self):
        self.count = 0

    def __enter__(self):
        counter = self
        real = subprocess.Popen

        class Counting(real):
            def __init__(self, args, *a, **k):
                if isinstance(args, list) and args and str(args[0]).endswith("git"):
                    counter.count += 1
                super().__init__(args, *a, **k)
        self.patch = mock.patch.object(subprocess, "Popen", Counting)
        self.patch.start()
        return self

    def __exit__(self, *exc):
        self.patch.stop()


def oid(root, rel):
    return git(root, "rev-parse", "HEAD:" + rel).strip()


def head_symlink(r):
    (r / T).unlink()
    os.symlink("a b.md", r / T)
    git(r, "add", T)
    git(r, "commit", "-q", "-m", "link")
    (r / T).unlink()
    (r / T).write_text("plain\n")
    git(r, "add", T)


CASES = {
    "clean": lambda r: None,
    "mode-only staged change": lambda r: (os.chmod(r / T, 0o755), git(r, "add", T)),
    "mode-only worktree change": lambda r: os.chmod(r / T, 0o755),
    "worktree symlink over regular index entry": lambda r: (
        (r / "docs/copy.md").write_text("plain\n"), (r / T).unlink(), os.symlink("copy.md", r / T)),
    "newline path worktree edit": lambda r: (r / NL).write_text("changed\n"),
    "space path staged-only edit": lambda r: (
        (r / "docs/a b.md").write_text("x\n"), git(r, "add", "docs/a b.md"),
        (r / "docs/a b.md").write_text("space\n")),
    "tab path untracked": lambda r: git(r, "rm", "-q", "--cached", "docs/tab\there.md"),
    "worktree edit": lambda r: (r / T).write_text("edited\n"),
    "staged-only edit": lambda r: ((r / T).write_text("x\n"), git(r, "add", T), (r / T).write_text("plain\n")),
    "index symlink 120000 with regular worktree file": lambda r: (
        git(r, "rm", "-q", "--cached", T),
        git(r, "update-index", "--add", "--cacheinfo", "120000," + oid(r, T) + "," + T)),
    "conflicted stages 1/2/3": lambda r: (
        git(r, "rm", "-q", "--cached", T),
        git(r, "update-index", "--index-info",
            stdin="".join(f"100644 {oid(r, T)} {s}\t{T}\n" for s in (1, 2, 3)).encode())),
    "untracked": lambda r: git(r, "rm", "-q", "--cached", T),
    "no HEAD tree entry": lambda r: (git(r, "rm", "-q", "--cached", T), git(r, "commit", "-q", "-m", "drop"),
                                      git(r, "add", T)),
    "HEAD entry a symlink": head_symlink,
    # Index and worktree equal the fingerprint, but HEAD holds older content: the per-path check
    # refuses `migration-input-not-committed`, so the batched path must too.
    "index and worktree match, HEAD holds older content": lambda r: (
        (r / T).write_text("older\n"), git(r, "add", T), git(r, "commit", "-q", "-m", "older"),
        (r / T).write_text("plain\n"), git(r, "add", T)),
    "deleted from worktree": lambda r: (r / T).unlink(),
}


class FreshCleanup(unittest.TestCase):
    def test_a_failing_fixture_command_inside_fresh_removes_its_directory(self):
        made = []
        real = tempfile.mkdtemp

        def spy(*args, **kwargs):
            made.append(real(*args, **kwargs))
            return made[-1]

        def boom(*args, **kwargs):
            raise AssertionError("forced fixture failure")

        with mock.patch.object(tempfile, "mkdtemp", spy), mock.patch(__name__ + ".git", boom):
            with self.assertRaises(AssertionError):
                fresh()
        self.assertEqual(len(made), 1)
        self.addCleanup(shutil.rmtree, made[0], ignore_errors=True)
        self.assertFalse(os.path.exists(made[0]))


class BatchedInputs(unittest.TestCase):
    def tearDown(self):
        for root in getattr(self, "roots", []):
            shutil.rmtree(root, ignore_errors=True)

    def root(self, files=FILES):
        root, fingerprints = fresh(files)
        self.roots = getattr(self, "roots", []) + [root]
        return root, fingerprints

    def test_child_count_is_bounded_for_any_number_of_inputs(self):
        # `_repository_state` reads .git files directly and spawns no git child (next test). The
        # fast path runs ls-files, ls-tree and two cat-file reads (sizes, bodies): at most 4.
        counts = {}
        for n in (3, 12):
            files = {f"docs/f{i}.md": f"file {i}\n" for i in range(n)}
            root, fingerprints = self.root(files)
            migration._OBJECT_MEMO.clear()
            with ChildCounter() as fast:
                self.assertEqual(outcome(migration._unchanged_inputs, root, fingerprints), "accepted")
            migration._OBJECT_MEMO.clear()
            with ChildCounter() as slow:
                self.assertEqual(outcome(per_path, root, fingerprints), "accepted")
            counts[n] = (fast.count, slow.count)
        self.assertLessEqual(counts[3][0], 4, counts)
        self.assertLessEqual(counts[12][0], 4, counts)
        self.assertGreater(counts[12][1], 4 * 3, counts)    # positive control: per-path cost is seen

    def test_repository_state_spawns_no_git_child(self):
        import git_read_cache
        root, _ = self.root()
        with ChildCounter() as counter:
            self.assertIsNotNone(git_read_cache._repository_state(root))
        self.assertEqual(counter.count, 0)

    def test_equivalence_table(self):
        for name, mutate in CASES.items():
            with self.subTest(case=name):
                results = {}
                for label, function in (("per-path", per_path), ("batched", migration._unchanged_inputs)):
                    root, fingerprints = fresh()
                    self.addCleanup(shutil.rmtree, root, ignore_errors=True)
                    mutate(root)
                    migration._OBJECT_MEMO.clear()
                    results[label] = outcome(function, root, fingerprints)
                self.assertEqual(results["per-path"], results["batched"], results)

    def test_clean_takes_the_fast_path_and_an_edit_falls_back_and_is_refused(self):
        root, fingerprints = self.root()
        with mock.patch.object(migration, "_unchanged_input", wraps=migration._unchanged_input) as slow:
            self.assertEqual(outcome(migration._unchanged_inputs, root, fingerprints), "accepted")
        self.assertEqual(slow.call_count, 0)              # every input took the fast path
        (root / T).write_text("edited\n")
        with mock.patch.object(migration, "_unchanged_input", wraps=migration._unchanged_input) as slow:
            self.assertEqual(outcome(migration._unchanged_inputs, root, fingerprints),
                             "migration-input-not-committed")
        self.assertGreaterEqual(slow.call_count, 1)       # the edited input fell back and was refused there

    def test_decoy_path_prefix_is_not_the_same_input(self):
        # `docs/new` is a file; `docs/new` + newline + `line.md` is another. Edit the second only.
        root, fingerprints = self.root()
        only = [item for item in fingerprints if item["path"] == "docs/new"]
        (root / NL).write_text("changed\n")
        self.assertEqual(outcome(migration._unchanged_inputs, root, only), "accepted")
        self.assertEqual(outcome(per_path, root, only), "accepted")

    def _race(self, function, side_effect, counter=None):
        root, fingerprints = self.root()
        real = migration._read
        calls = {"n": 0}

        def racing(repo, given):
            calls["n"] += 1
            if calls["n"] == 1:
                side_effect(root, fingerprints)
            return real(repo, given)
        with mock.patch.object(migration, "_read", racing):
            if counter is None:
                return outcome(function, root, fingerprints)
            with counter:
                return outcome(function, root, fingerprints)

    @staticmethod
    def _repoint_index(root, fingerprints):
        # A content race: the first fingerprinted path's index entry now names another blob.
        other = git(root, "hash-object", "-w", "--stdin", stdin=b"raced\n").strip()
        git(root, "update-index", "--cacheinfo", f"100644,{other},{fingerprints[0]['path']}")

    @staticmethod
    def _stat_refresh(root, fingerprints):
        # A stat race: mtimes move, no content changes, `git status` rewrites .git/index.
        for item in fingerprints:
            stat = os.stat(root / item["path"])
            os.utime(root / item["path"], (stat.st_atime + 10, stat.st_mtime + 10))
        git(root, "status", "--porcelain")

    def test_content_change_during_the_loop_is_refused(self):
        self.assertEqual(self._race(migration._unchanged_inputs, self._repoint_index),
                         "migration-history-head-changed")
        # positive control: the race is a real content change, which the per-path loop also
        # refuses, under its own code (it checks the raced entry against HEAD)
        self.assertEqual(self._race(per_path, self._repoint_index), "migration-input-not-committed")

    def test_stat_only_index_rewrite_during_the_loop_is_accepted(self):
        seen = {}

        def refresh(root, fingerprints):
            path = root / ".git" / "index"
            seen["before"] = path.read_bytes()
            self._stat_refresh(root, fingerprints)
            seen["after"] = path.read_bytes()
        counter = ChildCounter()
        self.assertEqual(self._race(migration._unchanged_inputs, refresh, counter), "accepted")
        self.assertNotEqual(seen["before"], seen["after"])    # positive control: the index bytes changed
        self.assertEqual(self._race(per_path, self._stat_refresh), "accepted")
        # 4 children on the common path plus 2 listings when the state bytes moved; the counter
        # also sees the side effect's own `git status`.
        self.assertLessEqual(counter.count, 6 + 1, counter.count)

    def test_head_only_change_during_the_loop_is_refused(self):
        # HEAD now names another blob for the first path while the index entry is restored.
        def head_only(root, fingerprints):
            rel = fingerprints[0]["path"]
            original = git(root, "ls-files", "-s", "--", rel).split()[1]
            other = git(root, "hash-object", "-w", "--stdin", stdin=b"raced\n").strip()
            git(root, "update-index", "--cacheinfo", f"100644,{other},{rel}")
            git(root, "commit", "-q", "-m", "race")
            git(root, "update-index", "--cacheinfo", f"100644,{original},{rel}")
        self.assertEqual(self._race(migration._unchanged_inputs, head_only),
                         "migration-history-head-changed")
        # positive control: the per-path loop refuses the same race under its own code
        self.assertEqual(self._race(per_path, head_only), "migration-input-not-committed")

    def test_empty_commit_during_the_loop_is_accepted_like_the_per_path_loop(self):
        # An empty commit changes no fingerprinted blob; the per-path loop accepts it too.
        def empty(root, fingerprints):
            git(root, "commit", "-q", "--allow-empty", "-m", "race")
        self.assertEqual(self._race(migration._unchanged_inputs, empty), "accepted")
        self.assertEqual(self._race(per_path, empty), "accepted")

    def test_malformed_blob_read_turns_the_fast_path_off(self):
        root, fingerprints = self.root()
        with mock.patch.object(migration, "_object_bodies", side_effect=ValueError("bad row")):
            with mock.patch.object(migration, "_unchanged_input", wraps=migration._unchanged_input) as slow:
                self.assertEqual(outcome(migration._unchanged_inputs, root, fingerprints), "accepted")
        self.assertEqual(slow.call_count, len(fingerprints))

    def test_counter_only_manifest_bump_takes_the_fallback_and_is_accepted(self):
        base = {"schema_version": 5, "concerns_enabled": ["adrs", "observations"],
                "observation": {"next_number": 1, "next_survey_number": 1},
                "adr": {"next_number": 1}, "promptbook": {"next_number": 1},
                "backfill": {"signed_batches": ["fixed"]}, "exact_type": 1}
        root, _ = self.root({"docs/manifest.yml": yaml.safe_dump(base, sort_keys=False)})
        (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: docs\n')
        git(root, "add", ".bionic.yml")
        git(root, "commit", "-q", "-m", "config")
        (root / "docs/manifest.yml").write_text(yaml.safe_dump({**base, "adr": {"next_number": 2}}, sort_keys=False))
        fingerprints = [{"path": "docs/manifest.yml",
                         "sha256": migration._digest((root / "docs/manifest.yml").read_bytes())}]
        with mock.patch.object(migration, "_unchanged_input", wraps=migration._unchanged_input) as slow:
            self.assertEqual(outcome(migration._unchanged_inputs, root, fingerprints), "accepted")
        self.assertEqual(slow.call_count, 1)

    def test_production_contract_digests_are_unchanged(self):
        self.assertEqual(hashlib.sha256(ap.production_contract_bytes("3")).hexdigest(),
                         "62a4760de1e477fb13c78f9162c49c4da620b5ace304ca72ce6fc45496d16d23")
        self.assertEqual(hashlib.sha256(ap.production_contract_bytes("4")).hexdigest(),
                         "b1bda66c8667b1c9a8bbaa39c47d58fd55245b718e6d025f71f7221d47035167")


class PublishedFixtureView(unittest.TestCase):
    setUp = shared.CitationKinds.setUp

    def test_authority_view_equals_the_forced_per_path_reference(self):
        self.f.close()
        self.f.publish()
        calls = []
        real = migration._unchanged_inputs

        def spy(repo, fingerprints):
            calls.append(len(fingerprints))
            return real(repo, fingerprints)
        with mock.patch.object(migration, "_unchanged_inputs", spy):
            fast = migration.authority_view(self.root)
        self.assertTrue(calls and max(calls) > 0, calls)   # positive control: the fixture reaches the check
        with mock.patch.object(migration, "_unchanged_inputs", per_path):
            slow = migration.authority_view(self.root)
        self.assertEqual(fast["state"], "published")
        self.assertEqual(fast, slow)


if __name__ == "__main__":
    unittest.main()
