"""`council_records`: record loading, discovery, hashing, clean-path state, book resolution.

Every refusal is paired with a positive control built from the same fixture, so a
helper that refused everything would fail the positive case. Git-dependent cases build
temp repositories; nothing reads the documentation tree.
"""
from __future__ import annotations

import json
import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _council_gate_support as sup  # noqa: E402

import council_records as cr  # noqa: E402


class _TmpCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name).resolve()


class LoadRecordTests(_TmpCase):
    def test_a_valid_record_is_loaded_and_classified_by_its_discriminator(self):
        for name, kind in (("council-record-ran.json", "council-record"),
                           ("council-record-could-not-run.json", "council-record"),
                           ("refutation-record.json", "refutation-record"),
                           ("owner-exception.json", "owner-exception"),
                           ("reviewer-report-paths.json", "reviewer-report"),
                           ("council-attempt.json", "council-attempt"),
                           ("council-record-v2-ran.json", "council-record"),
                           ("council-record-v2-preflight.json", "council-record"),
                           ("owner-exception-void-attempt.json", "owner-exception")):
            with self.subTest(name=name):
                rec = cr.load_record(sup.RECORDS / name)
                self.assertIsNotNone(rec)
                self.assertEqual(rec.record_type, kind)

    def test_a_json_file_with_another_record_type_is_not_a_record(self):
        for payload in ({"record_type": "old-synthesis", "x": 1}, {"consensus": "UNANIMOUS_APPROVE"},
                        ["a", "list"], {"record_type": None}):
            with self.subTest(payload=payload):
                p = self.tmp / "old.json"
                p.write_text(json.dumps(payload))
                self.assertIsNone(cr.load_record(p))

    def test_a_recognized_record_that_fails_its_schema_raises_naming_the_path(self):
        doc = sup.fixture("council-record-ran.json")
        del doc["seats"]
        p = self.tmp / "bad.json"
        p.write_text(json.dumps(doc))
        with self.assertRaises(cr.InvalidRecord) as ctx:
            cr.load_record(p)
        self.assertIn(str(p), str(ctx.exception))
        self.assertIn("council-record", str(ctx.exception))

    def test_the_schema_failure_names_the_path_and_never_echoes_the_offending_value(self):
        shape = "sk-or-v1-" + "a1b2c3d4" * 4  # key-shaped: the gate prints this message
        doc = sup.fixture("council-record-ran.json")
        doc["written_at"] = shape
        p = self.tmp / "bad.json"
        p.write_text(json.dumps(doc))
        with self.assertRaises(cr.InvalidRecord) as ctx:
            cr.load_record(p)
        self.assertIn("written_at", str(ctx.exception))  # control: the instance path is named
        self.assertNotIn(shape, str(ctx.exception))
        self.assertNotIn(shape, ctx.exception.message)
        doc["written_at"] = sup.fixture("council-record-ran.json")["written_at"]
        p.write_text(json.dumps(doc))
        self.assertIsNotNone(cr.load_record(p))  # control: the same record, valid, loads

    def test_a_key_shaped_property_name_is_withheld_from_the_schema_failure(self):
        shape = "sk-or-v1-" + "a1b2c3d4" * 4  # an unknown property's instance path is its name
        doc = sup.fixture("council-record-ran.json")
        doc[shape] = "x"
        p = self.tmp / "bad-key.json"
        p.write_text(json.dumps(doc))
        with self.assertRaises(cr.InvalidRecord) as ctx:
            cr.load_record(p)
        self.assertNotIn(shape, str(ctx.exception))
        self.assertIn("withheld", ctx.exception.message)
        doc.pop(shape)
        doc["unknown_field"] = "x"
        p.write_text(json.dumps(doc))
        with self.assertRaises(cr.InvalidRecord) as ctx:
            cr.load_record(p)
        self.assertIn("unknown_field", ctx.exception.message)  # control: a plain path is named

    def test_unparseable_json_raises_rather_than_reading_as_absent(self):
        p = self.tmp / "torn.json"
        p.write_text('{"record_type": "council-rec')
        with self.assertRaises(cr.InvalidRecord):
            cr.load_record(p)


class SealTests(_TmpCase):
    """The seal binds the exact bytes the council runner computed, not only their parsed content."""

    def test_a_sealed_fixture_holds_and_a_content_preserving_reformat_fails(self):
        for name in ("council-attempt.json", "council-record-v2-ran.json", "council-record-v2-preflight.json"):
            with self.subTest(name=name):
                data = (sup.RECORDS / name).read_bytes()
                self.assertTrue(cr.seal_holds(data), "control: the canonical bytes hold")
                doc = json.loads(data)
                for reformatted in (json.dumps(doc, indent=4, sort_keys=True) + "\n",
                                    json.dumps(dict(reversed(list(doc.items()))), indent=2) + "\n",
                                    json.dumps(doc, indent=2, sort_keys=True),
                                    json.dumps(doc, indent=2, sort_keys=True) + "\n\n"):
                    self.assertEqual(json.loads(reformatted), doc, "the reformat keeps the parsed content")
                    self.assertFalse(cr.seal_holds(reformatted.encode("ascii")))

    def test_a_changed_value_fails_the_seal_even_in_canonical_form(self):
        doc = json.loads((sup.RECORDS / "council-attempt.json").read_bytes())
        doc["round"] = 2
        self.assertFalse(cr.seal_holds(cr.canonical_bytes(doc)))
        self.assertTrue(cr.seal_holds(cr.canonical_bytes(cr.sealed(doc))), "control: resealed, it holds")

    def test_the_seal_ignores_its_own_value_and_never_its_absence(self):
        doc = {"a": 1, "seal": "sha256:" + "0" * 64}
        self.assertEqual(cr.seal_of(doc), cr.seal_of({"a": 1, "seal": None}))
        self.assertNotEqual(cr.seal_of({"a": 1}), cr.seal_of({"a": 2}))
        self.assertFalse(cr.seal_holds(cr.canonical_bytes({"a": 1})), "no seal at all")
        self.assertFalse(cr.seal_holds(cr.canonical_bytes({"a": 1, "seal": None})), "a null seal")

    def test_canonical_bytes_are_ascii_sorted_and_end_in_one_newline(self):
        data = cr.canonical_bytes({"b": "\u00e9", "a": [1]})
        self.assertEqual(data, b'{\n  "a": [\n    1\n  ],\n  "b": "\\u00e9"\n}\n')
        self.assertFalse(cr.seal_holds(b"\xff"), "non-ASCII bytes never hold")
        self.assertFalse(cr.seal_holds(b"[]\n"), "a JSON array never holds")


class GitChildEnvironmentTests(_TmpCase):
    def test_a_read_only_git_child_inherits_neither_the_key_nor_an_env_file_name(self):
        """Mutation: build `_git_env` from `git_isolated_env` alone -> the alias's dump carries both.
        Positive control: a marker variable set beside them reaches the dump."""
        home = self.tmp / "crux-home"
        home.mkdir()
        (home / "env").write_text("CRUX_FIXTURE_NAME=fixture-value\n")
        root = sup.init_repo(self.tmp / "r")
        dump = self.tmp / "env.txt"
        with mock.patch.dict(os.environ, {"CRUX_HOME": str(home), "OPENROUTER_API_KEY": "fixture-not-a-key",
                                          "CRUX_MARKER_VISIBLE": "1"}):
            import crux_env
            crux_env._reset_cache()
            try:
                r = cr.git(root, "-c", f"alias.dumpenv=!env > '{dump}'", "dumpenv")
            finally:
                crux_env._reset_cache()
        self.assertEqual(r.returncode, 0, r.stderr)
        names = {line.split("=", 1)[0] for line in dump.read_text().splitlines()}
        self.assertIn("CRUX_MARKER_VISIBLE", names, "control: the dump carries the child's environment")
        self.assertNotIn("OPENROUTER_API_KEY", names)
        self.assertNotIn("CRUX_FIXTURE_NAME", names)


class RecordSummaryTests(_TmpCase):
    def test_the_summary_keeps_identity_and_decision_and_drops_model_text(self):
        doc = sup.fixture("council-record-v2-ran.json")
        out = cr.record_summary(doc, "c/r.json", "c/a.attempt.json")
        self.assertEqual((out["record"], out["attempt"], out["outcome"]), ("c/r.json", "c/a.attempt.json", "ran"))
        self.assertEqual(len(out["seats"]), 3)
        self.assertEqual(set(out["seats"][0]), set(cr.SUMMARY_SEAT_FIELDS))
        self.assertNotIn("reasoning", json.dumps(out))
        self.assertNotIn("findings", json.dumps(out))

    def test_the_exit_code_is_zero_only_for_a_ran_record_with_no_refusal(self):
        self.assertEqual(cr.record_exit_code(sup.fixture("council-record-v2-ran.json")), 0)
        self.assertEqual(cr.record_exit_code(sup.fixture("council-record-v2-preflight.json")), 1)
        scan = dict(sup.fixture("council-record-v2-ran.json"), refusal_reason={"code": "secret-scan", "names": ["x"]})
        self.assertEqual(cr.record_exit_code(scan), 1)


class AttemptNameTests(_TmpCase):
    def test_the_attempt_file_name_carries_run_scope_round_and_ordinal(self):
        self.assertEqual(cr.attempt_file_name("RUN-001", "adr-1", 2, 1, 1), "RUN-001-adr-1-r1-a1.attempt.json")
        self.assertEqual(cr.attempt_file_name("RUN-002", None, 3, 2, 4), "RUN-002-p3-r2-a4.attempt.json")
        self.assertEqual(cr.attempt_scope("verify-2", 9), "verify-2")
        self.assertEqual(cr.attempt_scope(None, 9), "p9")

    def test_the_pending_directory_lies_under_the_absolute_git_directory(self):
        root = sup.init_repo(self.tmp / "r")
        got = cr.pending_dir(root, "PB-0999", "RUN-001")
        self.assertEqual(got, root / ".git" / "crux" / "council-pending" / "PB-0999" / "RUN-001")
        self.assertFalse(got.is_relative_to(root / "docs"))
        self.assertIsNone(cr.pending_dir(self.tmp, "PB-0999", "RUN-001"), "outside a repository: None")

    def test_a_linked_worktree_keeps_its_pending_copies_in_its_own_git_directory(self):
        root = sup.init_repo(self.tmp / "r")
        (root / "f").write_text("x\n")
        sup.commit_all(root)
        sup.git(root, "worktree", "add", "-q", str(self.tmp / "wt"))
        got = cr.pending_dir(self.tmp / "wt", "PB-0999", "RUN-001")
        self.assertTrue(got.is_relative_to(root / ".git" / "worktrees"), got)
        self.assertFalse(got.is_relative_to((self.tmp / "wt").resolve()))


class DiscoverRecordsTests(_TmpCase):
    def _run_dir(self) -> Path:
        d = self.tmp / "runs" / "PB-0999-x"
        (d / "council").mkdir(parents=True)
        return d

    def test_discovery_loads_records_and_skips_non_record_json(self):
        d = self._run_dir()
        (d / "council" / "a.json").write_text((sup.RECORDS / "council-record-ran.json").read_text())
        (d / "council" / "old-synthesis.json").write_text(json.dumps({"consensus": "X"}))
        (d / "council" / "notes.md").write_text("# not json")
        recs = cr.discover_records(d)
        self.assertEqual([r.path.name for r in recs], ["a.json"])

    def test_a_missing_council_directory_holds_no_records(self):
        self.assertEqual(cr.discover_records(self.tmp / "no-run"), [])

    def test_a_symlinked_record_file_is_refused(self):
        d = self._run_dir()
        real = self.tmp / "elsewhere.json"
        real.write_text((sup.RECORDS / "council-record-ran.json").read_text())
        # Positive control: the same bytes as a regular file are discovered.
        (d / "council" / "regular.json").write_text(real.read_text())
        self.assertEqual(len(cr.discover_records(d)), 1)
        (d / "council" / "linked.json").symlink_to(real)
        with self.assertRaises(cr.SymlinkRefused) as ctx:
            cr.discover_records(d)
        self.assertIn("linked.json", str(ctx.exception))

    def test_a_symlinked_council_directory_is_refused(self):
        d = self.tmp / "runs" / "PB-0999-y"
        d.mkdir(parents=True)
        real = self.tmp / "real-council"
        real.mkdir()
        (real / "a.json").write_text((sup.RECORDS / "council-record-ran.json").read_text())
        # Positive control: a real council directory with the same record is read.
        ok = self.tmp / "runs" / "PB-0999-ok"
        (ok / "council").mkdir(parents=True)
        (ok / "council" / "a.json").write_text((real / "a.json").read_text())
        self.assertEqual(len(cr.discover_records(ok)), 1)
        (d / "council").symlink_to(real, target_is_directory=True)
        with self.assertRaises(cr.SymlinkRefused):
            cr.discover_records(d)

    def test_an_invalid_record_in_the_directory_raises(self):
        d = self._run_dir()
        doc = sup.fixture("council-record-ran.json")
        doc["writer"] = "commander"
        (d / "council" / "forged.json").write_text(json.dumps(doc))
        with self.assertRaises(cr.InvalidRecord):
            cr.discover_records(d)


class HashTests(_TmpCase):
    def test_sha256_helpers_agree_with_hashlib(self):
        import hashlib
        p = self.tmp / "f.bin"
        p.write_bytes(b"abc")
        want = hashlib.sha256(b"abc").hexdigest()
        self.assertEqual(cr.sha256_bytes(b"abc"), want)
        self.assertEqual(cr.sha256_file(p), want)
        self.assertNotEqual(cr.sha256_bytes(b"abd"), want)


class PathStateTests(_TmpCase):
    def setUp(self):
        super().setUp()
        self.repo = sup.init_repo(self.tmp / "repo")
        (self.repo / "tracked.txt").write_text("one\n")
        (self.repo / ".gitignore").write_text("ignored.txt\n")
        sup.commit_all(self.repo)

    def test_a_committed_unchanged_file_is_clean(self):
        st = cr.path_state(self.repo, "tracked.txt")
        self.assertTrue(st.clean)
        self.assertTrue(st.tracked and not st.symlink and not st.ignored)
        self.assertEqual(st.head, st.index)
        self.assertEqual(st.index, st.worktree)
        self.assertTrue(cr.is_clean(self.repo, "tracked.txt"))

    def test_a_staged_change_is_not_clean(self):
        (self.repo / "tracked.txt").write_text("two\n")
        sup.git(self.repo, "add", "tracked.txt")
        st = cr.path_state(self.repo, "tracked.txt")
        self.assertFalse(st.clean)
        self.assertNotEqual(st.head, st.index)
        self.assertEqual(st.index, st.worktree)

    def test_an_unstaged_change_is_not_clean(self):
        (self.repo / "tracked.txt").write_text("two\n")
        st = cr.path_state(self.repo, "tracked.txt")
        self.assertFalse(st.clean)
        self.assertEqual(st.head, st.index)
        self.assertNotEqual(st.index, st.worktree)

    def test_an_untracked_file_is_never_clean(self):
        (self.repo / "new.txt").write_text("x\n")
        st = cr.path_state(self.repo, "new.txt")
        self.assertFalse(st.tracked)
        self.assertFalse(st.clean)
        self.assertIsNotNone(st.worktree)
        self.assertIsNone(st.head)
        self.assertFalse(st.ignored)

    def test_an_ignored_file_is_reported_ignored_and_never_clean(self):
        (self.repo / "ignored.txt").write_text("x\n")
        st = cr.path_state(self.repo, "ignored.txt")
        self.assertTrue(st.ignored)
        self.assertFalse(st.tracked)
        self.assertFalse(st.clean)

    def test_a_symlink_is_never_clean_even_when_tracked_and_unchanged(self):
        (self.repo / "link.txt").symlink_to("tracked.txt")
        sup.commit_all(self.repo, "link")
        st = cr.path_state(self.repo, "link.txt")
        self.assertTrue(st.tracked)
        self.assertTrue(st.symlink)
        self.assertFalse(st.clean)

    def test_a_missing_path_is_not_clean_and_holds_no_hash(self):
        st = cr.path_state(self.repo, "absent.txt")
        self.assertEqual((st.head, st.index, st.worktree), (None, None, None))
        self.assertFalse(st.clean)

    def test_a_path_outside_the_repository_is_refused(self):
        outside = self.tmp / "outside.txt"
        outside.write_text("x\n")
        for bad in ("../outside.txt", str(outside), "sub/../../outside.txt", ""):
            with self.subTest(path=bad), self.assertRaises(cr.PathRefused):
                cr.path_state(self.repo, bad)
        # Positive control: an in-repo absolute path resolves.
        self.assertTrue(cr.is_clean(self.repo, str(self.repo / "tracked.txt")))

    def test_a_path_through_a_symlinked_directory_is_refused(self):
        (self.repo / "d").mkdir()
        (self.repo / "d" / "f.txt").write_text("x\n")
        sup.commit_all(self.repo, "d")
        (self.repo / "dl").symlink_to("d", target_is_directory=True)
        self.assertTrue(cr.is_clean(self.repo, "d/f.txt"))
        with self.assertRaises(cr.PathRefused):
            cr.path_state(self.repo, "dl/f.txt")

    def test_a_glob_character_is_a_literal_path_not_a_pattern(self):
        (self.repo / "a[1].txt").write_text("x\n")
        sup.commit_all(self.repo, "glob")
        (self.repo / "a1.txt").write_text("y\n")
        self.assertTrue(cr.is_clean(self.repo, "a[1].txt"))
        self.assertFalse(cr.path_state(self.repo, "a1.txt").tracked)

    def test_repo_root_resolves_and_is_none_outside_a_repository(self):
        (self.repo / "sub").mkdir()
        self.assertEqual(cr.repo_root(self.repo / "sub"), self.repo)
        self.assertEqual(cr.repo_root(self.repo / "tracked.txt"), self.repo)
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(cr.repo_root(Path(td)))

    def test_repo_root_keeps_a_non_utf8_byte_in_the_root_path(self):
        # APFS refuses such a directory name, so git's answer is substituted: the root it reports
        # must come back as the same bytes, never as a name with a replacement character.
        import subprocess
        raw = os.fsencode(self.repo) + b"/r\xff"
        done = subprocess.CompletedProcess([], 0, stdout=raw + b"\n", stderr=b"")
        with mock.patch.object(cr, "git", return_value=done):
            got = cr.repo_root(self.repo)
        self.assertEqual(os.fsencode(got), raw)
        # Control: the same substitution with a UTF-8 name round-trips too.
        done = subprocess.CompletedProcess([], 0, stdout=os.fsencode(self.repo) + b"/r\n", stderr=b"")
        with mock.patch.object(cr, "git", return_value=done):
            self.assertEqual(cr.repo_root(self.repo), self.repo / "r")

    def test_git_ignores_the_ambient_global_configuration(self):
        import subprocess
        import unittest.mock
        (self.tmp / "ex").write_text("fresh.txt\n")
        cfg = self.tmp / "gitconfig"
        cfg.write_text("[core]\n\texcludesFile = " + str(self.tmp / "ex") + "\n")
        (self.repo / "fresh.txt").write_text("x\n")
        with unittest.mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(cfg)}):
            # Positive control: an unneutralized run does honour the ambient config.
            raw = subprocess.run(["git", "-C", str(self.repo), "check-ignore", "-q", "--", "fresh.txt"],
                                 capture_output=True, env=dict(os.environ))
            self.assertEqual(raw.returncode, 0)
            self.assertFalse(cr.path_state(self.repo, "fresh.txt").ignored)


class ResolveBookTests(_TmpCase):
    def setUp(self):
        super().setUp()
        self.env = sup.Env(self.tmp / "repo", commit=False)

    def test_the_layout_resolves_the_active_book_and_verifies_its_hash(self):
        r = cr.resolve_book(self.env.run_path)
        self.assertEqual(r.path, self.env.book_path)
        self.assertFalse(r.explicit)
        self.assertEqual(r.book["id"], sup.BOOK_ID)

    def test_an_explicit_book_wins_over_the_layout(self):
        other = self.tmp / "explicit.yaml"
        other.write_text(self.env.book_path.read_text())
        self.env.book_path.unlink()  # the layout candidate is gone; only --book can resolve
        r = cr.resolve_book(self.env.run_path, other)
        self.assertEqual(r.path, other)
        self.assertTrue(r.explicit)

    def test_the_archive_is_tried_after_active(self):
        archive = self.env.docs / "promptbooks" / "archive"
        archive.mkdir()
        moved = archive / self.env.book_path.name
        self.env.book_path.rename(moved)
        self.assertEqual(cr.resolve_book(self.env.run_path).path, moved)

    def test_active_is_preferred_over_archive(self):
        archive = self.env.docs / "promptbooks" / "archive"
        archive.mkdir()
        (archive / self.env.book_path.name).write_text("id: PB-0999\n")  # would mismatch if chosen
        self.assertEqual(cr.resolve_book(self.env.run_path).path, self.env.book_path)

    def test_an_unresolvable_book_is_a_typed_error(self):
        self.env.book_path.unlink()
        with self.assertRaises(cr.BookResolutionError) as ctx:
            cr.resolve_book(self.env.run_path)
        self.assertIn("no book found", str(ctx.exception))

    def test_a_run_outside_the_layout_needs_an_explicit_book(self):
        loose = self.tmp / "loose-run.yaml"
        loose.write_text(self.env.run_path.read_text())
        with self.assertRaises(cr.BookResolutionError):
            cr.resolve_book(loose)
        # Positive control: the same run resolves once the book is named.
        self.assertEqual(cr.resolve_book(loose, self.env.book_path).path, self.env.book_path)

    def test_a_hash_mismatch_is_refused_and_a_matching_hash_resolves(self):
        self.assertEqual(cr.resolve_book(self.env.run_path).book["id"], sup.BOOK_ID)
        text = self.env.book_path.read_text().replace("title: Fixture", "title: Changed")
        self.assertNotEqual(text, self.env.book_path.read_text())
        self.env.book_path.write_text(text)
        with self.assertRaises(cr.BookResolutionError) as ctx:
            cr.resolve_book(self.env.run_path)
        self.assertIn("content hash", str(ctx.exception))

    def test_a_symlinked_layout_book_is_refused_and_the_plain_book_resolves(self):
        real = self.tmp / "elsewhere.yaml"
        real.write_text(self.env.book_path.read_text())
        original = self.env.book_path.read_text()
        self.env.book_path.unlink()
        self.env.book_path.symlink_to(real)  # same bytes, so the hash matches: only the link is wrong
        with self.assertRaises(cr.BookResolutionError) as ctx:
            cr.resolve_book(self.env.run_path)
        self.assertIn("symlink", str(ctx.exception))
        # Positive control: the same bytes as a regular file resolve.
        self.env.book_path.unlink()
        self.env.book_path.write_text(original)
        self.assertEqual(cr.resolve_book(self.env.run_path).path, self.env.book_path)

    def test_a_layout_book_in_a_symlinked_directory_or_outside_the_repo_is_refused(self):
        active = self.env.book_path.parent
        outside = self.tmp / "outside-active"
        outside.mkdir()
        (outside / self.env.book_path.name).write_text(self.env.book_path.read_text())
        shutil.rmtree(active)
        active.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(cr.BookResolutionError):
            cr.resolve_book(self.env.run_path)
        active.unlink()
        active.mkdir()
        (active / self.env.book_path.name).write_text((outside / self.env.book_path.name).read_text())
        self.assertEqual(cr.resolve_book(self.env.run_path).path, self.env.book_path)

    def test_a_symlinked_explicit_book_is_refused_but_a_plain_explicit_book_outside_a_repo_resolves(self):
        plain = self.tmp / "plain.yaml"
        plain.write_text(self.env.book_path.read_text())
        link = self.tmp / "link.yaml"
        link.symlink_to(plain)
        with self.assertRaises(cr.BookResolutionError) as ctx:
            cr.resolve_book(self.env.run_path, link)
        self.assertIn("symlink", str(ctx.exception))
        self.assertEqual(cr.resolve_book(self.env.run_path, plain).path, plain)

    def test_a_pointer_only_edit_does_not_change_the_hash(self):
        text = self.env.book_path.read_text().replace("current_prompt: 2", "current_prompt: 9")
        self.env.book_path.write_text(text)
        self.assertEqual(cr.resolve_book(self.env.run_path).path, self.env.book_path)


class ReachedThroughSymlinkTests(_TmpCase):
    """`reached_through_symlink`: the walk advance-run.py and run-council.py share."""

    def setUp(self):
        super().setUp()
        if cr._inside_a_repository(self.tmp):
            self.skipTest("the temporary directory lies inside a git repository")
        self.repo = sup.init_repo(self.tmp / "repo")
        (self.repo / "docs").mkdir()
        self.file = self.repo / "docs" / "f.yaml"
        self.file.write_text("x: 1\n")
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        (self.outside / "f.yaml").write_text("x: 2\n")

    def test_an_in_repo_link_to_a_directory_outside_every_repository_counts(self):
        (self.repo / "docs" / "evil").symlink_to(self.outside)
        self.assertTrue(cr.reached_through_symlink(self.repo / "docs" / "evil" / "f.yaml"))
        self.assertFalse(cr.reached_through_symlink(self.file))  # positive control

    def test_a_parent_step_out_of_an_in_repo_link_counts(self):
        (self.repo / "docs" / "evil").symlink_to(self.outside)
        self.assertTrue(cr.reached_through_symlink(f"{self.repo}/docs/evil/../docs/f.yaml"))
        self.assertFalse(cr.reached_through_symlink(f"{self.repo}/docs/../docs/f.yaml"))

    def test_a_relative_path_is_walked_from_the_working_directory(self):
        (self.repo / "docs" / "evil").symlink_to(self.outside)
        cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, cwd)
        self.assertTrue(cr.reached_through_symlink("docs/evil/f.yaml"))
        self.assertFalse(cr.reached_through_symlink("docs/f.yaml"))

    def test_a_link_held_outside_every_repository_is_followed_and_a_leaf_link_is_not(self):
        alias = self.tmp / "alias"
        alias.symlink_to(self.repo)
        self.assertFalse(cr.reached_through_symlink(alias / "docs" / "f.yaml"))
        leaf = self.outside / "leaf.yaml"
        leaf.symlink_to(self.outside / "f.yaml")
        self.assertTrue(cr.reached_through_symlink(leaf))
        self.assertFalse(cr.reached_through_symlink(self.outside / "f.yaml"))

    def test_a_holding_directory_git_cannot_judge_counts(self):
        alias = self.tmp / "alias"
        alias.symlink_to(self.repo)
        self.assertFalse(cr.reached_through_symlink(alias / "docs" / "f.yaml"))  # positive control
        with mock.patch.object(cr.subprocess, "run", side_effect=OSError("no git")):
            self.assertTrue(cr.reached_through_symlink(alias / "docs" / "f.yaml"))
        failed = mock.Mock(returncode=128, stderr=b"fatal: detected dubious ownership")
        with mock.patch.object(cr.subprocess, "run", return_value=failed):
            self.assertTrue(cr.reached_through_symlink(alias / "docs" / "f.yaml"))

    def test_an_ambient_git_dir_cannot_hide_the_enclosing_repository(self):
        (self.repo / "docs" / "evil").symlink_to(self.outside)
        with mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": str(self.repo),
                                          "GIT_DIR": str(self.tmp / "nowhere")}):
            self.assertTrue(cr.reached_through_symlink(self.repo / "docs" / "evil" / "f.yaml"))


if __name__ == "__main__":
    unittest.main()
