"""Tests for write-review-report.py, the reviewer-report writer.

Each refusal asserts its reason. Most carry a positive control: the same invocation, changed in
exactly the named respect, writes a report. CLI cases run the script as a subprocess.

Reads only `crux/` and fixtures built in a temp directory. Never reads the documentation tree.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _council_gate_support import (  # noqa: E402
    SCRIPTS, SUBJECT, Env, commit_all, git, range_with_a_staged_change, scrubbed_env,
)

import council_gate as cg  # noqa: E402
import council_records as cr  # noqa: E402

WRITER = SCRIPTS / "write-review-report.py"
_spec = importlib.util.spec_from_file_location("write_review_report", WRITER)
wrr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wrr)

# A key SHAPE only: the prefix and a body of filler characters, never a real credential.
FAKE_KEY = "sk-or-v1-" + "a1" * 32


def run_writer(env: Env, *args: str, cwd: Path | None = None) -> tuple[int, dict | None, str]:
    r = subprocess.run([sys.executable, str(WRITER), *args], capture_output=True, text=True,
                       cwd=str(cwd or env.root))
    line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    return r.returncode, (json.loads(line) if line else None), r.stderr


# Positions in the fixture book (`_council_gate_support.make_book("adr")`): the adr module's
# council prompt and the review module's ordinal 1, as `council_gate.classify` derives them.
COUNCIL_GATE_PROMPT = 3
REVIEW_GATE_PROMPT = 10


def base_args(env: Env, prompt: int = 13) -> list[str]:
    return [str(env.run_path), "--prompt", str(prompt)]


def reports(env: Env) -> list[Path]:
    d = env.run_dir / "reviews"
    return sorted(d.glob("*.json")) if d.is_dir() else []


class WriterCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.env = Env(Path(self._tmp.name) / "repo")

    def ok(self, *extra: str, prompt: int = 13) -> dict:
        code, out, err = run_writer(self.env, *base_args(self.env, prompt), *extra)
        self.assertEqual(code, 0, (out, err))
        return out

    def refused(self, *extra: str, prompt: int = 13) -> dict:
        before = reports(self.env)
        code, out, err = run_writer(self.env, *base_args(self.env, prompt), *extra)
        self.assertEqual(code, 1, (out, err))
        self.assertEqual(out["status"], "refused")
        self.assertTrue(out["problems"])
        self.assertEqual(reports(self.env), before, "a refusal wrote a file")
        return out

    def head(self) -> str:
        return git(self.env.root, "rev-parse", "HEAD").strip()

    def second_commit(self, path: str = "docs/other.md") -> str:
        f = self.env.root / path
        f.write_text("# other\n")
        commit_all(self.env.root, "second")
        return self.head()

    def range_args(self) -> list[str]:
        base = self.head()
        end = self.second_commit()
        return ["--range", f"{base}..{end}", "--verdict", "APPROVE"]


class WrittenReport(WriterCase):
    def test_path_form_writes_a_bound_valid_report(self):
        out = self.ok("--path", SUBJECT, "--verdict", "APPROVE", "--finding", "nit: x",
                      "--reviewer", "reviewer: Security S1")
        files = reports(self.env)
        self.assertEqual(len(files), 1)
        self.assertEqual(self.env.rel(files[0]), out["path"])
        self.assertRegex(files[0].name, r"^RUN-001-p13-\d{8}T\d{12}Z-[0-9a-f]{8}\.json$")
        doc = json.loads(files[0].read_text())
        self.assertEqual(doc["format_version"], "2")
        self.assertEqual(cr.reviewer_report_errors(doc), [])
        self.assertEqual(doc["book"], {"id": "PB-0999", "content_hash": self.env.hash})
        self.assertEqual((doc["run_id"], doc["prompt"], doc["reviewer_role"]), ("RUN-001", 13, "reviewer"))
        self.assertEqual(doc["subject"]["paths"], [{"path": SUBJECT, "sha256": self.env.sha(SUBJECT)}])
        self.assertEqual((doc["findings"], doc["reviewer"]), (["nit: x"], "reviewer: Security S1"))

    def test_range_form_writes_the_range_as_given(self):
        args = self.range_args()
        self.ok(*args)
        doc = json.loads(reports(self.env)[0].read_text())
        self.assertEqual(doc["subject"], {"form": "commit-range", "range": args[1]})

    def test_two_reports_get_distinct_names(self):
        self.ok("--path", SUBJECT, "--verdict", "APPROVE")
        self.ok("--path", SUBJECT, "--verdict", "APPROVE")
        self.assertEqual(len(reports(self.env)), 2)

    def test_path_is_read_from_the_working_directory(self):
        sub = self.env.root / "docs"
        code, out, err = run_writer(self.env, *base_args(self.env), "--path", "adrs/ADR-0001-fixture.md",
                                    "--verdict", "APPROVE", cwd=sub)
        self.assertEqual(code, 0, (out, err))
        doc = json.loads(reports(self.env)[0].read_text())
        self.assertEqual(doc["subject"]["paths"][0]["path"], SUBJECT)


class BindingRefusals(WriterCase):
    def test_wrong_book_hash_refused_and_control_passes(self):
        text = self.env.book_path.read_text()
        self.env.book_path.write_text(text.replace("title: Fixture", "title: Changed"))
        out = self.refused("--path", SUBJECT, "--verdict", "APPROVE")
        self.assertIn("content hash differs", out["problems"][0])
        self.env.book_path.write_text(text)
        self.ok("--path", SUBJECT, "--verdict", "APPROVE")

    def test_bad_prompt_refused_and_control_passes(self):
        self.refused("--path", SUBJECT, "--verdict", "APPROVE", prompt=99)
        self.refused("--path", SUBJECT, "--verdict", "APPROVE", prompt=0)
        self.ok("--path", SUBJECT, "--verdict", "APPROVE", prompt=13)

    def test_explicit_book_is_honoured(self):
        other = self.env.root / "elsewhere.yaml"
        other.write_text(self.env.book_path.read_text())
        commit_all(self.env.root, "copy")
        self.ok("--path", SUBJECT, "--verdict", "APPROVE", "--book", str(other))

    def test_symlinked_run_snapshot_refused(self):
        link = self.env.root / "link-run.yaml"
        link.symlink_to(self.env.run_path)
        code, out, _ = run_writer(self.env, str(link), "--prompt", "13", "--path", SUBJECT,
                                  "--verdict", "APPROVE", "--book", str(self.env.book_path))
        self.assertEqual((code, out["status"]), (1, "refused"))
        self.assertEqual(reports(self.env), [])


class SubjectRefusals(WriterCase):
    def test_dirty_path_refused_and_control_passes(self):
        (self.env.root / SUBJECT).write_text("# edited\n")
        out = self.refused("--path", SUBJECT, "--verdict", "APPROVE")
        self.assertIn("unstaged change", out["problems"][0])
        git(self.env.root, "checkout", "--", SUBJECT)
        self.ok("--path", SUBJECT, "--verdict", "APPROVE")

    def test_staged_path_refused(self):
        (self.env.root / SUBJECT).write_text("# edited\n")
        git(self.env.root, "add", SUBJECT)
        out = self.refused("--path", SUBJECT, "--verdict", "APPROVE")
        self.assertIn("staged change", out["problems"][0])

    def test_untracked_path_refused_and_control_passes(self):
        (self.env.root / "docs" / "new.md").write_text("# new\n")
        out = self.refused("--path", "docs/new.md", "--verdict", "APPROVE")
        self.assertIn("no committed content", out["problems"][0])
        git(self.env.root, "add", "docs/new.md")
        self.refused("--path", "docs/new.md", "--verdict", "APPROVE")  # added, not committed
        commit_all(self.env.root, "add new")
        self.ok("--path", "docs/new.md", "--verdict", "APPROVE")

    def test_path_escaping_the_repository_refused(self):
        out = self.refused("--path", "../outside.md", "--verdict", "APPROVE")
        self.assertIn("outside the repository", out["problems"][0])

    def test_symlinked_path_refused(self):
        (self.env.root / "docs" / "ln.md").symlink_to(self.env.root / SUBJECT)
        commit_all(self.env.root, "link")
        self.refused("--path", "docs/ln.md", "--verdict", "APPROVE")

    def test_range_not_ending_at_head_or_an_ancestor_refused(self):
        base = self.head()
        end = self.second_commit()
        git(self.env.root, "checkout", "-q", "-b", "side", base)
        (self.env.root / "docs" / "side.md").write_text("# side\n")
        commit_all(self.env.root, "side")
        side = self.head()
        git(self.env.root, "checkout", "-q", "main")
        out = self.refused("--range", f"{base}..{side}", "--verdict", "APPROVE")
        self.assertIn("not HEAD or an ancestor", out["problems"][0])
        self.ok("--range", f"{base}..{end}", "--verdict", "APPROVE")  # control

    def test_later_commit_touching_a_reviewed_path_refused(self):
        base = self.head()
        end = self.second_commit()
        self.ok("--range", f"{base}..{end}", "--verdict", "APPROVE")  # control
        (self.env.root / "docs" / "other.md").write_text("# other v2\n")
        commit_all(self.env.root, "later")
        out = self.refused("--range", f"{base}..{end}", "--verdict", "APPROVE")
        self.assertIn("a commit after the range end changes it", out["problems"][0])

    def test_range_with_a_dirty_changed_path_refused(self):
        args = self.range_args()
        (self.env.root / "docs" / "other.md").write_text("# dirty\n")
        self.refused(*args)

    def test_range_path_with_a_non_utf8_name_and_a_staged_change_refused(self):
        # The UTF-8 name is the control: the same staged change refuses there.
        for name in (b"src/reviewed.txt", b"src/reviewed-\xff.txt"):
            with self.subTest(name=name):
                git(self.env.root, "reset", "-q", "--hard", "HEAD")
                rng = range_with_a_staged_change(self.env.root, name)
                out = self.refused("--range", rng, "--verdict", "APPROVE")
                self.assertIn("has a staged change", out["problems"][0])

    def test_empty_range_refused(self):
        h = self.head()
        out = self.refused("--range", f"{h}..{h}", "--verdict", "APPROVE")
        self.assertIn("changes no path", out["problems"][0])

    def test_malformed_range_refused(self):
        out = self.refused("--range", "main", "--verdict", "APPROVE")
        self.assertIn("is not <base>..<end>", out["problems"][0])

    def test_range_end_that_is_not_a_commit_refused_and_control_passes(self):
        base = self.head()
        end = self.second_commit()
        out = self.refused("--range", f"{base}..no-such-ref", "--verdict", "APPROVE")
        self.assertIn("no-such-ref", out["problems"][0])
        self.assertIn("not a commit", out["problems"][0])
        self.ok("--range", f"{base}..{end}", "--verdict", "APPROVE")


class RangeResolution(WriterCase):
    """Both range ends are recorded as full SHAs, and a base other than the run's warns."""

    def doc(self) -> dict:
        return json.loads(reports(self.env)[0].read_text())

    def test_abbreviated_ends_are_recorded_as_full_shas(self):
        base = self.head()
        end = self.second_commit()
        self.ok("--range", f"{base[:8]}..{end[:8]}", "--verdict", "APPROVE")
        self.assertEqual(self.doc()["subject"]["range"], f"{base}..{end}")

    def test_symbolic_ends_are_recorded_as_full_shas(self):
        base = self.head()
        end = self.second_commit()
        self.ok("--range", "HEAD~1..HEAD", "--verdict", "APPROVE")
        self.assertEqual(self.doc()["subject"]["range"], f"{base}..{end}")

    def set_base_commit(self, value: str) -> None:
        run = self.env.load_run()
        run["base_commit"] = value
        self.env.run_path.write_text(yaml.safe_dump(run, sort_keys=False))
        commit_all(self.env.root, "stamp base_commit")

    def test_a_base_other_than_the_runs_base_commit_warns_and_still_writes(self):
        start = self.head()
        self.set_base_commit(start)
        mid = self.second_commit()
        end = self.second_commit("docs/third.md")
        out = self.ok("--range", f"{mid}..{end}", "--verdict", "APPROVE")
        self.assertEqual(len(reports(self.env)), 1)
        self.assertEqual(len(out["warnings"]), 1, out)
        self.assertIn(mid, out["warnings"][0])
        self.assertIn(start, out["warnings"][0])

    def test_the_runs_base_commit_as_the_base_warns_nothing(self):  # control for the warning
        start = self.head()
        self.set_base_commit(start)
        end = self.second_commit()
        out = self.ok("--range", f"{start[:10]}..{end}", "--verdict", "APPROVE")
        self.assertEqual(out["warnings"], [])

    def test_a_run_with_no_base_commit_warns(self):
        self.env = Env(Path(self._tmp.name) / "no-base", base=False)  # the run records no base_commit
        out = self.ok(*self.range_args())
        self.assertEqual(len(out["warnings"]), 1, out)
        self.assertIn("records no base_commit", out["warnings"][0])

    def test_the_path_form_warns_nothing(self):
        out = self.ok("--path", SUBJECT, "--verdict", "APPROVE")
        self.assertEqual(out["warnings"], [])


class FileInputs(WriterCase):
    """`--verdict-file` and `--finding-file` keep model-written text out of the shell."""

    TRICKY = "it's `whoami` and $(id) and \"quoted\""

    def write_file(self, name: str, text: str) -> Path:
        path = Path(self._tmp.name) / name
        path.write_text(text, encoding="utf-8")
        return path

    def doc(self) -> dict:
        return json.loads(reports(self.env)[0].read_text())

    def test_verdict_file_is_written_verbatim_less_one_trailing_newline(self):
        v = self.write_file("verdict.txt", f"APPROVE: {self.TRICKY}\n\n")
        self.ok("--path", SUBJECT, "--verdict-file", str(v))
        self.assertEqual(self.doc()["verdict"], f"APPROVE: {self.TRICKY}\n")

    def test_finding_files_follow_the_argv_findings_in_order(self):
        a = self.write_file("a.txt", f"MUST-FIX: {self.TRICKY}\n")
        b = self.write_file("b.txt", "NIT: second line\nthird line")
        self.ok("--path", SUBJECT, "--verdict", "APPROVE", "--finding-file", str(a),
                "--finding", "argv one", "--finding-file", str(b))
        self.assertEqual(self.doc()["findings"],
                         ["argv one", f"MUST-FIX: {self.TRICKY}", "NIT: second line\nthird line"])

    def test_verdict_and_verdict_file_are_mutually_exclusive(self):
        v = self.write_file("verdict.txt", "APPROVE")
        code, _, err = run_writer(self.env, *base_args(self.env), "--path", SUBJECT,
                                  "--verdict", "APPROVE", "--verdict-file", str(v))
        self.assertEqual(code, 2, err)
        code, _, err = run_writer(self.env, *base_args(self.env), "--path", SUBJECT)
        self.assertEqual(code, 2, err)
        self.assertEqual(reports(self.env), [])

    def test_symlinked_verdict_file_refused_and_its_target_passes(self):
        real = self.write_file("real.txt", "APPROVE")
        link = Path(self._tmp.name) / "link.txt"
        link.symlink_to(real)
        out = self.refused("--path", SUBJECT, "--verdict-file", str(link))
        self.assertIn("symlink", out["problems"][0])
        self.ok("--path", SUBJECT, "--verdict-file", str(real))  # control

    def test_symlinked_finding_file_refused_and_its_target_passes(self):
        real = self.write_file("real.txt", "NIT: x")
        link = Path(self._tmp.name) / "link.txt"
        link.symlink_to(real)
        out = self.refused("--path", SUBJECT, "--verdict", "APPROVE", "--finding-file", str(link))
        self.assertIn("symlink", out["problems"][0])
        self.ok("--path", SUBJECT, "--verdict", "APPROVE", "--finding-file", str(real))  # control

    def test_directory_refused_and_a_file_passes(self):
        d = Path(self._tmp.name) / "adir"
        d.mkdir()
        out = self.refused("--path", SUBJECT, "--verdict", "APPROVE", "--finding-file", str(d))
        self.assertIn("not a regular file", out["problems"][0])
        f = self.write_file("f.txt", "NIT: x")
        self.ok("--path", SUBJECT, "--verdict", "APPROVE", "--finding-file", str(f))  # control

    def test_missing_file_refused(self):
        out = self.refused("--path", SUBJECT, "--verdict-file", str(Path(self._tmp.name) / "absent.txt"))
        self.assertIn("does not exist", out["problems"][0])

    def test_non_utf8_file_refused(self):
        bad = Path(self._tmp.name) / "bad.txt"
        bad.write_bytes(b"\xff\xfe APPROVE")
        out = self.refused("--path", SUBJECT, "--verdict-file", str(bad))
        self.assertIn("UTF-8", out["problems"][0])

    def test_secret_in_a_finding_file_names_the_field_not_the_value(self):
        leak = self.write_file("leak.txt", f"leak {FAKE_KEY}\n")
        out = self.refused("--path", SUBJECT, "--verdict", "APPROVE", "--finding", "fine",
                           "--finding-file", str(leak))
        self.assertTrue(out["problems"][0].startswith("findings[1]"), out)
        self.assertNotIn(FAKE_KEY, json.dumps(out))
        clean = self.write_file("clean.txt", "leak sk-or-v1-short\n")
        self.ok("--path", SUBJECT, "--verdict", "APPROVE", "--finding", "fine",
                "--finding-file", str(clean))  # control: no key shape

    def test_secret_in_a_verdict_file_names_the_field_not_the_value(self):
        leak = self.write_file("leak.txt", f"APPROVE {FAKE_KEY}")
        out = self.refused("--path", SUBJECT, "--verdict-file", str(leak))
        self.assertTrue(out["problems"][0].startswith("verdict"), out)
        self.assertNotIn(FAKE_KEY, json.dumps(out))

    def test_empty_verdict_file_refused_by_the_schema(self):
        empty = self.write_file("empty.txt", "\n")
        out = self.refused("--path", SUBJECT, "--verdict-file", str(empty))
        self.assertIn("schema", out["problems"][0])


class ScanAndSchemaRefusals(WriterCase):
    def test_secret_in_verdict_names_the_field_not_the_value(self):
        out = self.refused("--path", SUBJECT, "--verdict", f"APPROVE {FAKE_KEY}")
        text = json.dumps(out)
        self.assertIn("verdict", text)
        self.assertNotIn(FAKE_KEY, text)
        self.ok("--path", SUBJECT, "--verdict", "APPROVE sk-or-v1-short")  # control: no key shape

    def test_secret_in_a_finding_names_its_index(self):
        out = self.refused("--path", SUBJECT, "--verdict", "APPROVE", "--finding", "fine",
                           "--finding", f"leak {FAKE_KEY}")
        self.assertTrue(out["problems"][0].startswith("findings[1]"), out)
        self.assertNotIn(FAKE_KEY, json.dumps(out))

    def test_secret_in_reviewer_names_the_field(self):
        out = self.refused("--path", SUBJECT, "--verdict", "APPROVE", "--reviewer", FAKE_KEY)
        self.assertTrue(out["problems"][0].startswith("reviewer"), out)
        self.assertNotIn(FAKE_KEY, json.dumps(out))

    def test_schema_refusal_empty_verdict_and_control(self):
        out = self.refused("--path", SUBJECT, "--verdict", "")
        self.assertIn("schema", out["problems"][0])
        self.ok("--path", SUBJECT, "--verdict", "APPROVE")


class OutputGuards(WriterCase):
    def test_symlinked_reviews_directory_refused_and_nothing_written_through_it(self):
        target = self.env.root / "elsewhere"
        target.mkdir()
        (self.env.run_dir / "reviews").symlink_to(target)
        code, out, _ = run_writer(self.env, *base_args(self.env), "--path", SUBJECT, "--verdict", "APPROVE")
        self.assertEqual((code, out["status"]), (1, "refused"))
        self.assertEqual(list(target.iterdir()), [])
        (self.env.run_dir / "reviews").unlink()
        self.ok("--path", SUBJECT, "--verdict", "APPROVE")  # control

    def test_reviews_directory_is_created_when_absent(self):
        self.assertFalse((self.env.run_dir / "reviews").exists())
        self.ok("--path", SUBJECT, "--verdict", "APPROVE")
        self.assertTrue((self.env.run_dir / "reviews").is_dir())

    def test_existing_file_is_never_overwritten(self):
        reviews = self.env.run_dir / "reviews"
        reviews.mkdir()
        fixed = "20260101T000000000000Z", "2026-01-01T00:00:00.000000Z"
        name = f"RUN-001-p13-{fixed[0]}-deadbeef.json"
        (reviews / name).write_text("ORIGINAL")
        args = wrr._parser().parse_args(base_args(self.env) + ["--path", str(self.env.root / SUBJECT), "--verdict", "APPROVE"])
        doc, run_dir, repo, _ = wrr.build(args)
        class _U:
            hex = "deadbeef" + "0" * 24
        with mock.patch.object(wrr, "_stamp", return_value=fixed), mock.patch.object(wrr.uuid, "uuid4", return_value=_U):
            with self.assertRaises(wrr.Refused):
                wrr.write(doc, run_dir, repo)
        self.assertEqual((reviews / name).read_text(), "ORIGINAL")
        self.assertEqual(len(list(reviews.iterdir())), 1)

    def test_collision_retries_with_a_new_name(self):
        reviews = self.env.run_dir / "reviews"
        reviews.mkdir()
        fixed = "20260101T000000000000Z", "2026-01-01T00:00:00.000000Z"
        (reviews / f"RUN-001-p13-{fixed[0]}-deadbeef.json").write_text("ORIGINAL")
        args = wrr._parser().parse_args(base_args(self.env) + ["--path", str(self.env.root / SUBJECT), "--verdict", "APPROVE"])
        doc, run_dir, repo, _ = wrr.build(args)
        hexes = iter(["deadbeef" + "0" * 24, "cafef00d" + "0" * 24])
        with mock.patch.object(wrr, "_stamp", return_value=fixed), \
                mock.patch.object(wrr.uuid, "uuid4", side_effect=lambda: mock.Mock(hex=next(hexes))):
            target = wrr.write(doc, run_dir, repo)
        self.assertTrue(target.name.endswith("cafef00d.json"))
        self.assertEqual(len(list(reviews.iterdir())), 2)

    def test_symlink_at_the_output_leaf_is_not_followed(self):
        reviews = self.env.run_dir / "reviews"
        reviews.mkdir()
        victim = self.env.root / "victim.txt"
        victim.write_text("KEEP")
        fixed = "20260101T000000000000Z", "2026-01-01T00:00:00.000000Z"
        (reviews / f"RUN-001-p13-{fixed[0]}-deadbeef.json").symlink_to(victim)
        args = wrr._parser().parse_args(base_args(self.env) + ["--path", str(self.env.root / SUBJECT), "--verdict", "APPROVE"])
        doc, run_dir, repo, _ = wrr.build(args)
        with mock.patch.object(wrr, "_stamp", return_value=fixed), \
                mock.patch.object(wrr.uuid, "uuid4", return_value=mock.Mock(hex="deadbeef" + "0" * 24)):
            with self.assertRaises(wrr.Refused):
                wrr.write(doc, run_dir, repo)
        self.assertEqual(victim.read_text(), "KEEP")


    def test_a_write_failure_leaves_no_partial_file(self):
        args = wrr._parser().parse_args(base_args(self.env) + ["--path", str(self.env.root / SUBJECT), "--verdict", "APPROVE"])
        doc, run_dir, repo, _ = wrr.build(args)
        real_fdopen = wrr.os.fdopen

        def failing(fd, *a, **k):
            fh = real_fdopen(fd, *a, **k)
            fh.write = mock.Mock(side_effect=OSError(28, "no space"))
            return fh
        with mock.patch.object(wrr.os, "fdopen", side_effect=failing):
            with self.assertRaises(wrr.Fault):
                wrr.write(doc, run_dir, repo)
        self.assertEqual(list((run_dir / "reviews").iterdir()), [])
        wrr.write(doc, run_dir, repo)  # control: the same call writes when the disk accepts it
        self.assertEqual(len(list((run_dir / "reviews").iterdir())), 1)

    def test_reviews_swapped_for_a_symlink_after_the_check_is_not_written_through(self):
        args = wrr._parser().parse_args(base_args(self.env) + ["--path", str(self.env.root / SUBJECT), "--verdict", "APPROVE"])
        doc, run_dir, repo, _ = wrr.build(args)
        elsewhere = self.env.root / "elsewhere"
        elsewhere.mkdir()
        real_reviews_dir = wrr._reviews_dir

        def swap(rd, rp):
            path = real_reviews_dir(rd, rp)
            path.rmdir()
            path.symlink_to(elsewhere)
            return path
        with mock.patch.object(wrr, "_reviews_dir", side_effect=swap), \
                mock.patch.object(wrr.cr, "reached_through_symlink", return_value=False):
            with self.assertRaises(wrr.Refused):
                wrr.write(doc, run_dir, repo)
        self.assertEqual(list(elsewhere.iterdir()), [])


class GateIntegration(unittest.TestCase):
    """E8: a writer-produced report passes the review gate and is refused at a council gate."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.env = Env(Path(self._tmp.name) / "repo")

    def written(self, *extra: str, prompt: int = REVIEW_GATE_PROMPT) -> str:
        code, out, err = run_writer(self.env, *base_args(self.env, prompt), *extra)
        self.assertEqual(code, 0, (out, err))
        return out["path"]

    def review_gate(self):
        gate = cg.classify(self.env.book, REVIEW_GATE_PROMPT)
        self.assertEqual(gate.cls, "independent-review", gate)
        return gate

    def council_gate(self):
        gate = cg.classify(self.env.book, COUNCIL_GATE_PROMPT)
        self.assertEqual(gate.cls, "council", gate)
        return gate

    def test_written_report_passes_the_review_gate(self):
        rel = self.written("--path", SUBJECT, "--verdict", "APPROVE")
        verdict = cg.evaluate_review_gate(self.review_gate(), self.env.load_run(), self.env.root,
                                          [rel], "done")
        self.assertEqual(verdict.verdict, "pass", verdict)

    def test_report_bound_to_the_council_prompt_is_refused_at_the_council_gate(self):
        # Bound to the council prompt itself, so only the record type can explain the refusal.
        rel = self.written("--path", SUBJECT, "--verdict", "APPROVE", prompt=COUNCIL_GATE_PROMPT)
        refused = cg.evaluate_gate(self.council_gate(), self.env.load_run(), self.env.run_path,
                                   self.env.root, [rel], "done")
        self.assertEqual(refused.verdict, "refuse", refused)
        self.assertTrue(any("no council record" in r for r in refused.reasons), refused.reasons)

    def test_report_copied_into_the_council_folder_is_still_refused_at_a_council_gate(self):
        rel = self.written("--path", SUBJECT, "--verdict", "APPROVE", prompt=COUNCIL_GATE_PROMPT)
        copy = self.env.council / "reviewer-copy.json"
        doc = json.loads((self.env.root / rel).read_text())
        # A format-2 report is refused at the council gate by the format-1 record loader, which
        # reads the council folder and does not know format 2. Format 1 keeps the original reason.
        copy.write_text(json.dumps(doc))
        refused = cg.evaluate_gate(self.council_gate(), self.env.load_run(), self.env.run_path,
                                   self.env.root, [self.env.rel(copy)], "done")
        self.assertEqual(refused.verdict, "refuse", refused)
        self.assertTrue(any("fails the reviewer-report schema" in r for r in refused.reasons), refused.reasons)
        doc["format_version"] = "1"
        copy.write_text(json.dumps(doc))
        refused = cg.evaluate_gate(self.council_gate(), self.env.load_run(), self.env.run_path,
                                   self.env.root, [self.env.rel(copy)], "done")
        self.assertEqual(refused.verdict, "refuse", refused)
        self.assertTrue(any("no council record" in r for r in refused.reasons), refused.reasons)

    def test_a_runner_record_passes_the_same_council_gate_call(self):  # positive control
        # The gate reads only a committed council record.
        record = self.env.write("ran.json", self.env.council_doc(prompt=COUNCIL_GATE_PROMPT), commit=True)
        verdict = cg.evaluate_gate(self.council_gate(), self.env.load_run(), self.env.run_path,
                                   self.env.root, [self.env.rel(record)], "done")
        self.assertEqual(verdict.verdict, "pass", verdict.reasons)

    def test_range_report_passes_the_review_gate(self):
        base = git(self.env.root, "rev-parse", "HEAD").strip()
        (self.env.root / "docs" / "r.md").write_text("# r\n")
        commit_all(self.env.root, "r")
        end = git(self.env.root, "rev-parse", "HEAD").strip()
        rel = self.written("--range", f"{base}..{end}", "--verdict", "APPROVE")
        self.assertEqual(cg.evaluate_gate(self.review_gate(), self.env.load_run(), self.env.run_path,
                                          self.env.root, [rel], "done").verdict, "pass")

    def test_report_goes_stale_when_a_reviewed_path_changes_after_the_write(self):
        rel = self.written("--path", SUBJECT, "--verdict", "APPROVE")
        (self.env.root / SUBJECT).write_text("# changed after the review\n")
        self.assertEqual(cg.evaluate_gate(self.review_gate(), self.env.load_run(), self.env.run_path,
                                          self.env.root, [rel], "done").verdict, "refuse")

    def test_symlinked_reviews_refused_through_the_cli(self):
        (self.env.run_dir / "reviews").symlink_to(self.env.root / "docs")
        code, out, _ = run_writer(self.env, *base_args(self.env), "--path", SUBJECT, "--verdict", "APPROVE")
        self.assertEqual((code, out["status"]), (1, "refused"))
        self.assertEqual(list((self.env.root / "docs").glob("*.json")), [])


class Environment(unittest.TestCase):
    def test_usage_error_exits_2(self):
        r = subprocess.run([sys.executable, str(WRITER), "run.yaml"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def run_cli(self, *args: str, cwd: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(WRITER), *args], capture_output=True, text=True, cwd=cwd)

    def test_outside_a_repository_exits_2_with_or_without_a_book(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d) / "run-RUN-001.yaml"
            run.write_text("run_id: RUN-001\nbook_content_hash: x\nprompts: []\n")
            book = Path(d) / "book.yaml"
            book.write_text("id: PB-0999\n")
            for extra in ([], ["--book", str(book)]):
                r = self.run_cli(str(run), "--prompt", "1", "--path", "x", "--verdict", "v", *extra, cwd=d)
                self.assertEqual(r.returncode, 2, (extra, r.stdout, r.stderr))
                self.assertIn("not inside a git repository", r.stderr)
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["book.yaml", "run-RUN-001.yaml"])

    def test_missing_run_snapshot_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(Path(d) / "repo")
            r = self.run_cli(str(env.run_dir / "run-RUN-009.yaml"), "--prompt", "13", "--path", SUBJECT,
                             "--verdict", "APPROVE", cwd=str(env.root))
            self.assertEqual(r.returncode, 2, (r.stdout, r.stderr))
            self.assertIn("does not exist", r.stderr)
            self.assertFalse((env.run_dir / "reviews").exists())
            ok = self.run_cli(str(env.run_path), "--prompt", "13", "--path", SUBJECT, "--verdict", "APPROVE",
                              cwd=str(env.root))
            self.assertEqual(ok.returncode, 0, ok.stderr)  # control: the real run is accepted

    def test_unparseable_run_snapshot_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(Path(d) / "repo")
            good = env.run_path.read_text()
            env.run_path.write_text("run_id: [unclosed\n  : :\n")
            r = self.run_cli(str(env.run_path), "--prompt", "13", "--path", SUBJECT, "--verdict", "APPROVE",
                             cwd=str(env.root))
            self.assertEqual(r.returncode, 2, (r.stdout, r.stderr))
            self.assertIn("cannot be read", r.stderr)
            self.assertFalse((env.run_dir / "reviews").exists())
            env.run_path.write_text(good)
            ok = self.run_cli(str(env.run_path), "--prompt", "13", "--path", SUBJECT, "--verdict", "APPROVE",
                              cwd=str(env.root))
            self.assertEqual(ok.returncode, 0, ok.stderr)


class UnresolvedRunPath(unittest.TestCase):
    def test_run_named_through_a_symlinked_ancestor_prints_a_clean_repo_relative_path(self):
        with tempfile.TemporaryDirectory() as d:
            real = Path(d) / "real"
            real.mkdir()
            env = Env(real / "repo")
            alias = Path(d) / "alias"  # outside every repository, like macOS /tmp -> /private/tmp
            alias.symlink_to(real)
            via = alias / "repo" / env.run_path.relative_to(env.root)
            r = subprocess.run([sys.executable, str(WRITER), str(via), "--prompt", "13", "--path", SUBJECT,
                                "--verdict", "APPROVE"], capture_output=True, text=True, cwd=str(env.root))
            self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
            out = json.loads(r.stdout.strip().splitlines()[-1])
            self.assertFalse(out["path"].startswith(".."), out)
            self.assertTrue((env.root / out["path"]).is_file(), out)
            gate = cg.GateClass(cls="independent-review", n=13, module_tag="review-1")
            verdict = cg.evaluate_review_gate(gate, env.load_run(), env.root, [out["path"]], "done")
            self.assertEqual(verdict.verdict, "pass", verdict)


DUMMY_KEY = "zz-gateway-dummy-key-4d5e6f7a8b9c"  # a key with no declared shape: only the exact scan finds it


class HygieneBase(WriterCase):
    """A repository, a temp crux home and a scratch directory, and a command-line runner that
    asserts the dummy gateway key reaches neither stream."""

    def setUp(self):
        super().setUp()
        self.home = Path(self._tmp.name) / "crux-home"
        self.home.mkdir()
        self.scratch = Path(self._tmp.name) / "scratch"
        self.scratch.mkdir()

    def cli(self, *extra: str, with_key: bool = False, home_env: str | None = None
            ) -> tuple[int, dict | None, str]:
        env = scrubbed_env()
        env.pop("OPENROUTER_API_KEY", None)
        env.update(CRUX_HOME=str(self.home), HOME=str(self.home))
        if with_key:
            env["OPENROUTER_API_KEY"] = DUMMY_KEY
        if home_env is not None:
            (self.home / "env").write_text(home_env)
        r = subprocess.run([sys.executable, str(WRITER), *base_args(self.env), *extra],
                           capture_output=True, text=True, cwd=str(self.env.root), env=env)
        line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        self.assertNotIn(DUMMY_KEY, r.stdout + r.stderr)
        return r.returncode, (json.loads(line) if line else None), r.stderr

    def refused_cli(self, *extra: str, **kw) -> dict:
        before = reports(self.env)
        code, out, err = self.cli(*extra, **kw)
        self.assertEqual(code, 1, (out, err))
        self.assertEqual(out["status"], "refused")
        self.assertEqual(reports(self.env), before, "a refusal wrote a file")
        return out


class SecretHygiene(HygieneBase):
    """The writer's text inputs never come from the crux home or an env file, and its scan knows the
    gateway key. Every refusal has a control that writes and a command-line case."""

    def test_a_verdict_file_under_crux_home_is_refused_and_a_scratch_file_is_accepted(self):
        inside = self.home / "notes.txt"
        inside.write_text("APPROVE")
        out = self.refused_cli("--path", SUBJECT, "--verdict-file", str(inside))
        self.assertIn("crux home", out["problems"][0])
        outside = self.scratch / "notes.txt"
        outside.write_text("APPROVE")
        code, out, err = self.cli("--path", SUBJECT, "--verdict-file", str(outside))  # control
        self.assertEqual(code, 0, (out, err))

    def test_a_finding_file_under_crux_home_is_refused(self):
        inside = self.home / "notes.txt"
        inside.write_text("NIT: x")
        out = self.refused_cli("--path", SUBJECT, "--verdict", "APPROVE", "--finding-file", str(inside))
        self.assertIn("crux home", out["problems"][0])

    def test_the_crux_env_file_itself_is_refused(self):
        (self.home / "env").write_text("SOME_KEY=1\n")
        out = self.refused_cli("--path", SUBJECT, "--verdict-file", str(self.home / "env"))
        self.assertIn("env file", out["problems"][0])

    def test_a_dotenv_file_is_refused_for_both_options_and_a_plain_file_is_accepted(self):
        dotenv = self.env.root / ".env"
        dotenv.write_text("APPROVE")
        for opt in ("--verdict-file", "--finding-file"):
            args = ["--path", SUBJECT] + (["--verdict", "APPROVE"] if opt == "--finding-file" else [])
            out = self.refused_cli(*args, opt, str(dotenv))
            self.assertIn("env file", out["problems"][0], opt)
        plain = self.scratch / "plain.txt"
        plain.write_text("APPROVE")
        code, out, err = self.cli("--path", SUBJECT, "--verdict-file", str(plain))  # control
        self.assertEqual(code, 0, (out, err))

    def test_a_file_reached_through_a_symlink_into_crux_home_is_refused(self):
        inside = self.home / "notes.txt"
        inside.write_text("APPROVE")
        link = self.scratch / "link"
        link.symlink_to(self.home)
        out = self.refused_cli("--path", SUBJECT, "--verdict-file", str(link / "notes.txt"))
        self.assertIn("crux home", out["problems"][0])

    def test_the_gateway_key_in_a_verdict_is_refused_by_name_and_never_printed(self):
        for text in (f"APPROVE {DUMMY_KEY}", f"APPROVE KEY_{DUMMY_KEY}"):  # bare and glued
            out = self.refused_cli("--path", SUBJECT, "--verdict", text, with_key=True)
            self.assertTrue(out["problems"][0].startswith("verdict"), out)
            self.assertIn("exact-key", out["problems"][0])
        code, out, err = self.cli("--path", SUBJECT, "--verdict", "APPROVE", with_key=True)  # control
        self.assertEqual(code, 0, (out, err))
        code, out, err = self.cli("--path", SUBJECT, "--verdict", f"APPROVE {DUMMY_KEY}")  # no key known
        self.assertEqual(code, 0, (out, err))

    def test_the_gateway_key_read_from_the_env_file_is_refused_in_a_finding_file(self):
        leak = self.scratch / "leak.txt"
        leak.write_text(f"leak KEY_{DUMMY_KEY}\n")
        out = self.refused_cli("--path", SUBJECT, "--verdict", "APPROVE", "--finding-file", str(leak),
                               home_env=f"OPENROUTER_API_KEY={DUMMY_KEY}\n")
        self.assertTrue(out["problems"][0].startswith("findings[0]"), out)

    def test_an_unparseable_env_file_is_never_printed_and_the_shape_scan_still_runs(self):
        bad = f" OPENROUTER_API_KEY={DUMMY_KEY}\n"  # a leading space is a parse error
        code, out, err = self.cli("--path", SUBJECT, "--verdict", "APPROVE", home_env=bad)
        self.assertEqual(code, 0, (out, err))  # no readable key: the shape scan alone ran
        self.assertIn("exact-key scan was skipped", err)  # the skip is visible
        self.assertNotIn(DUMMY_KEY, err)
        code, out, err = self.cli("--path", SUBJECT, "--verdict", "APPROVE",
                                  home_env=f"OPENROUTER_API_KEY={DUMMY_KEY}\n")
        self.assertNotIn("exact-key scan was skipped", err)  # control: a parseable env warns nothing
        out = self.refused_cli("--path", SUBJECT, "--verdict", f"APPROVE {FAKE_KEY}", home_env=bad)
        self.assertNotIn(FAKE_KEY, json.dumps(out))

    def test_the_skipped_exact_key_scan_warns_once(self):
        bad = f" OPENROUTER_API_KEY={DUMMY_KEY}\n"  # a leading space is a parse error
        # A refusal reads the key for the field scan and again for each printed problem.
        code, out, err = self.cli("--path", SUBJECT, "--verdict", f"APPROVE {FAKE_KEY}",
                                  "--finding", f"NIT: {FAKE_KEY}", home_env=bad)
        self.assertEqual(code, 1, (out, err))
        self.assertGreaterEqual(len(out["problems"]), 1, out)
        self.assertEqual(err.count("exact-key scan was skipped"), 1, err)

    def test_a_hard_link_to_the_crux_env_file_is_refused_and_a_copy_is_accepted(self):
        (self.home / "env").write_text("OTHER_SETTING=plain\n")
        linked = self.scratch / "notes.txt"
        os.link(self.home / "env", linked)
        out = self.refused_cli("--path", SUBJECT, "--verdict-file", str(linked))
        self.assertIn("crux env file", out["problems"][0])
        out = self.refused_cli("--path", SUBJECT, "--verdict", "APPROVE", "--finding-file", str(linked))
        self.assertIn("crux env file", out["problems"][0])
        copy = self.scratch / "copy.txt"
        copy.write_text("OTHER_SETTING=plain\n")
        code, out, err = self.cli("--path", SUBJECT, "--verdict-file", str(copy))  # control
        self.assertEqual(code, 0, (out, err))


class OutputScan(HygieneBase):
    """Argparse errors and refusals that echo an argument are scanned before they print."""

    def test_a_key_shaped_stray_argument_is_not_echoed_on_stderr(self):
        r = subprocess.run([sys.executable, str(WRITER), *base_args(self.env), "--path", SUBJECT,
                            "--verdict", "APPROVE", "--bogus", FAKE_KEY],
                           capture_output=True, text=True, cwd=str(self.env.root), env=scrubbed_env())
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn(FAKE_KEY, r.stdout + r.stderr)
        self.assertIn("withheld", r.stderr)
        plain = subprocess.run([sys.executable, str(WRITER), *base_args(self.env), "--path", SUBJECT,
                                "--verdict", "APPROVE", "--bogus", "plain"],
                               capture_output=True, text=True, cwd=str(self.env.root), env=scrubbed_env())
        self.assertEqual(plain.returncode, 2, plain.stderr)  # control: an ordinary stray argument is echoed
        self.assertIn("plain", plain.stderr)

    def test_the_gateway_key_as_a_stray_argument_is_not_echoed(self):
        code, out, err = self.cli("--path", SUBJECT, "--verdict", "APPROVE", "--bogus", DUMMY_KEY,
                                  with_key=True)  # cli() asserts the key is in neither stream
        self.assertEqual(code, 2, err)
        self.assertIn("withheld", err)

    def test_a_key_shaped_path_is_not_echoed_in_the_refusal(self):
        out = self.refused_cli("--path", FAKE_KEY, "--verdict", "APPROVE")
        self.assertNotIn(FAKE_KEY, json.dumps(out))
        self.assertTrue(out["problems"])
        out = self.refused_cli("--path", "docs/absent-file.md", "--verdict", "APPROVE")  # control
        self.assertIn("docs/absent-file.md", json.dumps(out))

    def test_the_gateway_key_as_a_path_is_not_echoed_in_the_refusal(self):
        out = self.refused_cli("--path", DUMMY_KEY, "--verdict", "APPROVE", with_key=True)
        self.assertTrue(out["problems"])


class HelpText(unittest.TestCase):
    def help_text(self) -> str:
        r = subprocess.run([sys.executable, str(WRITER), "--help"], capture_output=True, text=True,
                           env=scrubbed_env())
        self.assertEqual(r.returncode, 0, r.stderr)
        return " ".join(r.stdout.split())

    def test_every_option_has_its_help_text(self):
        text = self.help_text()
        for phrase in ("the prompt number the report is bound to",
                       "repeatable",
                       "BASE..END; BASE should be the run's base_commit (the writer warns otherwise)",
                       "read the verdict from a regular file, not a symlink",
                       "read one finding from a regular file, not a symlink",
                       "free text; the writer records it and never checks it",
                       "the book file when the run layout does not find it"):
            self.assertIn(phrase, text)

    def test_the_epilog_names_the_exit_codes(self):
        text = self.help_text()
        for phrase in ("0 a report was written", "1 the report was refused and nothing was written",
                       "2 an environment fault"):
            self.assertIn(phrase, text)


class CoverageCases(WriterCase):
    def test_a_fifo_verdict_file_is_refused_without_hanging_and_a_regular_file_passes(self):
        fifo = Path(self._tmp.name) / "verdict.fifo"
        os.mkfifo(fifo)
        before = reports(self.env)
        r = subprocess.run([sys.executable, str(WRITER), *base_args(self.env), "--path", SUBJECT,
                            "--verdict-file", str(fifo)], capture_output=True, text=True,
                           cwd=str(self.env.root), env=scrubbed_env(), timeout=30)
        self.assertEqual(r.returncode, 1, (r.stdout, r.stderr))
        self.assertIn("not a regular file", json.loads(r.stdout)["problems"][0])
        self.assertEqual(reports(self.env), before)
        regular = Path(self._tmp.name) / "verdict.txt"
        regular.write_text("APPROVE")
        self.ok("--path", SUBJECT, "--verdict-file", str(regular))  # control

    def test_a_directory_verdict_file_is_refused_through_the_cli(self):
        d = Path(self._tmp.name) / "adir"
        d.mkdir()
        out = self.refused("--path", SUBJECT, "--verdict-file", str(d))
        self.assertIn("not a regular file", out["problems"][0])

    def test_a_run_snapshot_without_run_id_is_refused_and_the_control_writes(self):
        good = self.env.run_path.read_text()
        self.env.run_path.write_text("".join(l for l in good.splitlines(True) if not l.startswith("run_id:")))
        out = self.refused("--path", SUBJECT, "--verdict", "APPROVE")
        self.assertIn("no run_id", out["problems"][0])
        self.env.run_path.write_text(good)
        self.ok("--path", SUBJECT, "--verdict", "APPROVE")  # control

    def test_a_run_snapshot_without_book_id_is_refused_and_the_control_writes(self):
        good = self.env.run_path.read_text()
        self.env.run_path.write_text("".join(l for l in good.splitlines(True) if not l.startswith("book_id:")))
        out = self.refused("--path", SUBJECT, "--verdict", "APPROVE")
        self.assertIn("no book_id", out["problems"][0])
        self.env.run_path.write_text(good)
        self.ok("--path", SUBJECT, "--verdict", "APPROVE")  # control

    def test_reviews_that_is_a_symlink_at_the_directory_open_is_refused(self):
        args = wrr._parser().parse_args(base_args(self.env) + ["--path", str(self.env.root / SUBJECT),
                                                              "--verdict", "APPROVE"])
        doc, run_dir, repo, _ = wrr.build(args)
        wrr.write(doc, run_dir, repo)  # control: the unswapped directory takes the write
        elsewhere = self.env.root / "elsewhere"
        elsewhere.mkdir()
        real_open = os.open

        def swapping(path, flags, *a, **k):
            if flags & os.O_DIRECTORY and Path(path).name == "reviews":
                Path(run_dir / "reviews").rename(run_dir / "reviews-moved")
                (run_dir / "reviews").symlink_to(elsewhere)
            return real_open(path, flags, *a, **k)
        with mock.patch.object(wrr.os, "open", side_effect=swapping), \
                mock.patch.object(wrr.cr, "reached_through_symlink", return_value=False):
            with self.assertRaises(wrr.Refused) as ctx:
                wrr.write(doc, run_dir, repo)
        self.assertIn("symlink or not a directory", str(ctx.exception))
        self.assertEqual(list(elsewhere.iterdir()), [])


class UnnormalisedRunPath(WriterCase):
    """`linked/..` is walked through `linked`, not collapsed first."""

    def link_outside(self) -> str:
        outside = Path(self._tmp.name) / "outside"
        (outside / "sub").mkdir(parents=True)
        shutil.copytree(self.env.run_dir, outside / "PB-0999-fixture")
        (self.env.run_dir.parent / "linked").symlink_to(outside / "sub")
        return "docs/promptbooks/runs/linked/../PB-0999-fixture/run-RUN-001.yaml"

    def test_a_run_named_through_a_link_and_dot_dot_is_refused_and_the_plain_path_is_accepted(self):
        tricky = self.link_outside()
        before = reports(self.env)
        r = subprocess.run([sys.executable, str(WRITER), tricky, "--prompt", "13", "--path", SUBJECT,
                            "--verdict", "APPROVE"], capture_output=True, text=True,
                           cwd=str(self.env.root), env=scrubbed_env())
        self.assertEqual(r.returncode, 1, (r.stdout, r.stderr))
        self.assertIn("symlink", json.loads(r.stdout)["problems"][0])
        self.assertEqual(reports(self.env), before)
        plain = subprocess.run([sys.executable, str(WRITER), "docs/promptbooks/runs/PB-0999-fixture/run-RUN-001.yaml",
                                "--prompt", "13", "--path", SUBJECT, "--verdict", "APPROVE"],
                               capture_output=True, text=True, cwd=str(self.env.root), env=scrubbed_env())
        self.assertEqual(plain.returncode, 0, (plain.stdout, plain.stderr))  # control


    def test_an_outside_held_dangling_link_then_dot_dot_still_meets_the_abspath_check(self):
        # `<tmp>/dangling/../repo/...`: the raw walk stops at the dangling link (held outside every
        # repository), so it alone passes. The normalised form then goes through the in-repository
        # link `linked` into another repository, which the abspath check refuses at bind.
        tmp = Path(self._tmp.name).resolve()
        other = Env(tmp / "other")
        self.env.run_dir.parent.joinpath("linked").symlink_to(other.run_dir)
        (tmp / "dangling").symlink_to(tmp / "nowhere")
        tricky = str(tmp / "dangling" / ".." / "repo" / "docs" / "promptbooks" / "runs" / "linked"
                     / "run-RUN-001.yaml")
        r = subprocess.run([sys.executable, str(WRITER), tricky, "--prompt", "13", "--path", SUBJECT,
                            "--verdict", "APPROVE"], capture_output=True, text=True,
                           cwd=str(self.env.root), env=scrubbed_env())
        self.assertEqual(r.returncode, 1, (r.stdout, r.stderr))
        self.assertIn("reached through a symlink", json.loads(r.stdout)["problems"][0])
        self.assertEqual(reports(other), [])
        # Control: the other repository's own run path, named plainly, binds and writes.
        plain = subprocess.run([sys.executable, str(WRITER), str(other.run_path), "--prompt", "13",
                                "--path", SUBJECT, "--verdict", "APPROVE"], capture_output=True,
                               text=True, cwd=str(other.root), env=scrubbed_env())
        self.assertEqual(plain.returncode, 0, (plain.stdout, plain.stderr))

if __name__ == "__main__":
    unittest.main()
