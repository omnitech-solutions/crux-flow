"""`council_gate.attempt_states`: the one implementation of council-attempt resolution.

An attempt is resolved only when exactly one committed format-2 council record names it by path,
sha256 and attempt id, its seal holds, it carries the attempt's book, run, binding, round,
question, subjects and registry, and it equals the pending copy when one exists. It is voided only
by a committed owner exception naming its path and sha256. Each test below starts from a resolved
attempt (the positive control in `ResolvedBaselineTests`) and flips exactly one condition.

Also covers `next_ordinal` and `v1_admissible`. Builds temporary git repositories with an isolated
git configuration; reads only `crux/` and committed fixtures.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _council_gate_support as sup  # noqa: E402

import council_gate as cg  # noqa: E402
import council_records as cr  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        tmp = Path(self._td.name).resolve()
        patcher = mock.patch.dict(os.environ, sup.isolated_git_config(tmp / "cfg"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.env = sup.Env(tmp / "repo", base=True)

    def attempt(self, **kw) -> Path:
        name = self.env.attempt_name(**{k: kw[k] for k in ("module_tag", "prompt", "round", "ordinal") if k in kw})
        return self.env.write_sealed(name, self.env.attempt_doc(**kw), commit=True)

    def record(self, attempt: Path, name: str = "RUN-001-p2-r1-rec.json", commit: bool = True,
               mutate=None, **kw) -> Path:
        doc = self.env.council_v2_doc(attempt, **kw)
        if mutate:
            mutate(doc)
        return self.env.write_sealed(name, doc, commit=commit)

    def states(self, module_tag="adr-1", prompt=2):
        return cg.attempt_states(self.env.run, self.env.run_dir, self.env.root, module_tag, prompt)

    def only(self, **kw) -> cg.AttemptState:
        got = self.states(**kw)
        self.assertEqual(len(got), 1, got)
        return got[0]

    def pending_dir(self) -> Path:
        return cr.pending_dir(self.env.root, sup.BOOK_ID, sup.RUN_ID)


class ResolvedBaselineTests(_Base):
    def test_a_committed_sealed_record_naming_the_attempt_resolves_it(self):
        a = self.attempt()
        r = self.record(a)
        st = self.only()
        self.assertEqual(st.state, "resolved", st.problems)
        self.assertTrue(st.committed)
        self.assertEqual(st.resolved_by.path, r)
        self.assertEqual(st.rel, self.env.rel(a))
        self.assertEqual(cg.open_attempts(self.env.run, self.env.run_dir, self.env.root, "adr-1", 2), [])

    def test_an_attempt_with_no_record_is_open(self):
        self.attempt()
        st = self.only()
        self.assertEqual(st.state, "open")
        self.assertEqual(st.naming, [])
        self.assertEqual(len(cg.open_attempts(self.env.run, self.env.run_dir, self.env.root, "adr-1", 2)), 1)

    def test_an_uncommitted_attempt_is_reported_uncommitted(self):
        a = self.env.write_sealed(self.env.attempt_name(), self.env.attempt_doc(), commit=False)
        st = self.only()
        self.assertFalse(st.committed)
        self.assertEqual(st.state, "open")
        self.assertEqual(st.rel, self.env.rel(a))


class OneConditionFlippedTests(_Base):
    def assertOpen(self, why: str):
        st = self.only()
        self.assertEqual(st.state, "open", f"expected open: {why}")
        self.assertTrue(any(why in p for p in st.problems), st.problems)

    def test_an_uncommitted_record_does_not_resolve(self):
        self.record(self.attempt(), commit=False)
        self.assertOpen("not committed")

    def test_a_reformatted_record_whose_parsed_content_is_unchanged_does_not_resolve(self):
        a = self.attempt()
        r = self.record(a)
        doc = json.loads(r.read_bytes())
        r.write_text(json.dumps(doc, indent=4, sort_keys=True) + "\n")
        self.env.commit_records(r)
        self.assertEqual(json.loads(r.read_bytes()), doc, "the reformat keeps the parsed content")
        self.assertOpen("seal")

    def test_a_record_naming_another_sha256_does_not_resolve(self):
        self.record(self.attempt(), mutate=lambda d: d["attempt"].update(sha256="0" * 64))
        self.assertOpen("another sha256")

    def test_a_record_naming_another_attempt_id_does_not_resolve(self):
        self.record(self.attempt(), mutate=lambda d: d["attempt"].update(attempt_id="f" * 32))
        self.assertOpen("another attempt id")

    def test_each_bound_field_that_differs_from_the_attempt_does_not_resolve(self):
        flips = {
            "round": lambda d: d.update(round=2),
            "question": lambda d: d["question"].update(sha256="9" * 64),
            "registry": lambda d: d["registry"].update(sha256="8" * 64),
            "binding": lambda d: d["binding"].update(prompt=4),
            "subjects": lambda d: d["subjects"][0].update(sha256="7" * 64),
            "book": lambda d: d["book"].update(content_hash="sha256:" + "6" * 64),
        }
        for key, flip in flips.items():
            with self.subTest(key=key):
                self.setUp()  # a fresh repository per flip
                a = self.attempt()
                self.record(a, mutate=flip)
                st = self.only()
                self.assertEqual(st.state, "open", key)
                self.assertTrue(any(f"{key} differ" in p for p in st.problems), (key, st.problems))

    def test_a_record_that_differs_from_its_pending_copy_does_not_resolve(self):
        r = self.record(self.attempt())
        d = self.pending_dir()
        d.mkdir(parents=True)
        (d / r.name).write_bytes(r.read_bytes())
        self.assertEqual(self.only().state, "resolved", "control: an equal pending copy resolves")
        (d / r.name).write_bytes(r.read_bytes().replace(b'"round": 1', b'"round": 1 '))
        self.assertOpen("pending copy")

    def test_two_committed_records_naming_one_attempt_resolve_nothing(self):
        a = self.attempt()
        self.record(a, name="RUN-001-p2-r1-one.json")
        self.record(a, name="RUN-001-p2-r1-two.json", written_at=sup.ts(2))
        self.assertOpen("more than one")

    def test_a_record_naming_another_attempt_path_does_not_name_this_one(self):
        a = self.attempt()
        b = self.attempt(ordinal=2)
        self.record(b)
        by_rel = {s.rel: s for s in self.states()}
        self.assertEqual(by_rel[self.env.rel(a)].state, "open")
        self.assertEqual(by_rel[self.env.rel(a)].naming, [])
        self.assertEqual(by_rel[self.env.rel(b)].state, "resolved")


class VoidAttemptTests(_Base):
    def test_a_committed_void_naming_path_and_sha_voids_the_attempt(self):
        a = self.attempt()
        v = self.env.write("RUN-001-void.json", self.env.void_doc(a), commit=True)
        st = self.only()
        self.assertEqual(st.state, "voided")
        self.assertEqual(st.voided_by.path, v)
        self.assertEqual(cg.next_ordinal(self.states(), 1), 2)

    def test_a_void_with_another_sha_or_uncommitted_does_not_void(self):
        a = self.attempt()
        doc = self.env.void_doc(a)
        doc["void_attempt"]["sha256"] = "0" * 64
        self.env.write("RUN-001-void.json", doc, commit=True)
        self.assertEqual(self.only().state, "open")
        self.env.write("RUN-001-void2.json", self.env.void_doc(a), commit=False)
        self.assertEqual(self.only().state, "open", "an uncommitted void voids nothing")

    def test_a_void_in_another_module_does_not_void(self):
        a = self.attempt()
        self.env.write("RUN-001-void.json", self.env.void_doc(a, module_tag="verify-1"), commit=True)
        self.assertEqual(self.only().state, "open")


class ScopeAndOrdinalTests(_Base):
    def test_an_attempt_is_scoped_to_its_module_and_a_patch_attempt_to_its_prompt(self):
        self.attempt()
        self.assertEqual(len(self.states(module_tag="adr-1", prompt=4)), 1, "the module scope spans prompts")
        self.assertEqual(self.states(module_tag="verify-1", prompt=2), [])
        self.assertEqual(self.states(module_tag=None, prompt=2), [])

    def test_the_next_ordinal_counts_resolved_and_voided_attempts_at_the_round_only(self):
        a1 = self.attempt()
        self.assertEqual(cg.next_ordinal(self.states(), 1), 1, "an open attempt does not count")
        self.record(a1)
        self.assertEqual(cg.next_ordinal(self.states(), 1), 2)
        self.assertEqual(cg.next_ordinal(self.states(), 2), 1, "another round counts from one")

    def test_a_symlinked_pending_directory_is_refused(self):
        self.attempt()
        d = self.pending_dir()
        d.parent.mkdir(parents=True)
        target = Path(self._td.name) / "elsewhere"
        target.mkdir()
        d.symlink_to(target)
        with self.assertRaises(cr.SymlinkRefused):
            self.states()


class V1AdmissibilityTests(_Base):
    def runs_dir(self) -> Path:
        return self.env.run_dir.parent

    def test_a_run_whose_base_predates_any_attempt_admits_format_one(self):
        ok, why = cg.v1_admissible(self.env.root, self.env.base_commit, self.runs_dir())
        self.assertTrue(ok, why)

    def test_an_unknown_base_refuses_format_one(self):
        for base in (None, "", "not-a-commit", "0" * 40):
            with self.subTest(base=base):
                ok, why = cg.v1_admissible(self.env.root, base, self.runs_dir())
                self.assertFalse(ok)
                self.assertIn("base_commit", why)

    def test_a_base_that_holds_an_attempt_record_refuses_format_one(self):
        self.attempt()
        head = sup.git(self.env.root, "rev-parse", "HEAD").strip()
        ok, why = cg.v1_admissible(self.env.root, head, self.runs_dir())
        self.assertFalse(ok)
        self.assertIn("attempt record", why)
        ok, _ = cg.v1_admissible(self.env.root, self.env.base_commit, self.runs_dir())
        self.assertTrue(ok, "control: the earlier base still admits format one")

    def test_a_base_that_carries_the_plugins_attempt_schema_refuses_format_one(self):
        schema = self.env.root / "crux" / "schemas" / "council-attempt.schema.json"
        schema.parent.mkdir(parents=True)
        schema.write_text("{}\n")
        with mock.patch.object(cg.current_policy, "_ATTEMPT_SCHEMA", schema):
            ok, _ = cg.v1_admissible(self.env.root, self.env.base_commit, self.runs_dir())
            self.assertTrue(ok, "control: the schema is not in the base commit")
            self.env.commit_records(schema, msg="land format 2")
            head = sup.git(self.env.root, "rev-parse", "HEAD").strip()
            ok, why = cg.v1_admissible(self.env.root, head, self.runs_dir())
        self.assertFalse(ok)
        self.assertIn("attempt schema", why)

    def test_a_shallow_repository_refuses_format_one(self):
        clone = Path(self._td.name) / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{self.env.root}", str(clone)],
                       check=True, capture_output=True, env=sup.scrubbed_env())
        head = sup.git(clone, "rev-parse", "HEAD").strip()
        ok, why = cg.v1_admissible(clone.resolve(), head, clone.resolve() / "docs" / "promptbooks" / "runs")
        self.assertFalse(ok)
        self.assertIn("shallow", why)


if __name__ == "__main__":
    unittest.main()
