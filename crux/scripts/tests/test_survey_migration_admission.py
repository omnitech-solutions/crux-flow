"""Survey admission refuses before writes; fixtures author only synthetic verdicts."""
from __future__ import annotations

import unittest
import os
import stat
import tempfile
import shutil
from pathlib import Path
from contextlib import ExitStack, contextmanager
from unittest import mock
import yaml

import observation_admission as admission
from test_survey_signoff import _SignoffCase, SO, SS, _snapshot


class SurveyAdmission(_SignoffCase):
    @contextmanager
    def _guarded_reads(self):
        root = self.root.resolve()
        with ExitStack() as stack:
            for method in ("read_text", "read_bytes"):
                original = getattr(Path, method)
                def trap(path, *args, _original=original, **kwargs):
                    self.assertFalse(path.is_relative_to(root) or path.is_relative_to(self.root),
                                     "repository bytes read outside operation transport")
                    return _original(path, *args, **kwargs)
                stack.enter_context(mock.patch.object(Path, method, trap))
            yield

    def test_scaffold_bootstrap_and_candidate_reads_use_operation_transport(self):
        from test_survey_scaffold import _load
        scaffold = _load("scaffold-survey-sheet")
        with self._guarded_reads():
            code, payload = scaffold.scaffold(self.root, None, today="2026-08-30", dry_run=True)
        self.assertEqual(code, 0)
        self.assertEqual(payload["rows"], 2)

    def test_signoff_bootstrap_sheet_and_state_reads_use_operation_transport(self):
        batch = self._prepare({self.a1: "ratify", self.a2: "ratify"},
                              {self.a1: "runtime", self.a2: "runtime"})
        with self._guarded_reads():
            ctx = self._ctx(batch)
            self.assertEqual(SO.run(ctx, dry_run=True)[0], 0)

    def test_every_publish_cell_and_published_resume_use_guarded_reads(self):
        batch = self._prepare({self.a1: "ratify", self.a2: "ratify"},
                              {self.a1: "runtime", self.a2: "runtime"})
        with self._guarded_reads():
            ctx = self._ctx(batch)
            self.assertEqual(SO.run(ctx, dry_run=False)[1]["state"], "S9")
            resumed = SO.make_context(self.root, None, None, "2026-08-30")
            payload = SO.run(resumed, dry_run=False)[1]
        self.assertEqual(payload["state"], "S9")
        self.assertEqual(payload["writes"], [])

    def test_checker_bootstrap_records_and_receipts_use_operation_transport(self):
        import check_observations
        (self.root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: bionic\n')
        batch = self._prepare({self.a1: "ratify", self.a2: "ratify"},
                              {self.a1: "runtime", self.a2: "runtime"})
        result = self._run("--batch", batch)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with self._guarded_reads():
            result = check_observations.check(self.root)
        self.assertEqual(result["broken"], [])

    def _inventory(self):
        out = {}
        for base, directories, files in os.walk(self.root, followlinks=False):
            for name in directories + files:
                path = Path(base) / name
                mode = path.lstat().st_mode
                out[str(path.relative_to(self.root))] = (
                    mode, os.readlink(path) if stat.S_ISLNK(mode) else
                    path.read_bytes() if stat.S_ISREG(mode) else None)
        return out

    def test_public_bootstrap_refuses_outside_config_manifest_and_state_before_bytes(self):
        from test_survey_scaffold import _load
        import check_observations
        scaffold = _load("scaffold-survey-sheet")
        for operation in ("scaffold", "signoff", "checker"):
            for location in ("config", "manifest", "state"):
                if operation == "checker" and location == "state":
                    continue
                for selector in (None, "bionic"):
                    with self.subTest(operation=operation, location=location, selector=selector):
                        self.setUp()
                        (self.root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: bionic\n')
                        batch = (self._prepare({self.a1: "ratify", self.a2: "ratify"},
                                 {self.a1: "runtime", self.a2: "runtime"})
                                 if operation == "signoff" else None)
                        outside = tempfile.TemporaryDirectory()
                        self.addCleanup(outside.cleanup)
                        path = {"config": self.root / ".bionic.yml",
                                "manifest": self.tree / "manifest.yml", "state": self.state_path}[location]
                        target = Path(outside.name).resolve() / path.name
                        target.write_bytes(path.read_bytes())
                        path.unlink(); path.symlink_to(target)
                        before = self._inventory()
                        inode, read = target.stat().st_ino, os.read
                        def trap(fd, size):
                            self.assertNotEqual(os.fstat(fd).st_ino, inode, "outside input bytes read")
                            return read(fd, size)
                        with self._guarded_reads(), mock.patch("os.read", trap), self.assertRaises(
                                (ValueError, check_observations.ObservationsRefusal)):
                            if operation == "scaffold":
                                scaffold.scaffold(self.root, selector, today="2026-08-30", dry_run=True)
                            elif operation == "signoff":
                                SO.run(SO.make_context(self.root, selector, batch, "2026-08-30"), dry_run=True)
                            else:
                                check_observations.check(self.root, docs_dir=selector)
                        self.assertEqual(self._inventory(), before)
                        path.unlink(); path.write_bytes(target.read_bytes())
                        if operation == "scaffold":
                            self.assertEqual(scaffold.scaffold(self.root, selector,
                                today="2026-08-30", dry_run=True)[0], 0)
                        elif operation == "signoff":
                            self.assertEqual(SO.run(SO.make_context(self.root, selector, batch,
                                "2026-08-30"), dry_run=True)[0], 0)
                        else:
                            self.assertEqual(check_observations.check(self.root, docs_dir=selector)["broken"], [])

    def test_public_concern_alias_excludes_holding_before_enumeration_and_accepts_ordinary_alias(self):
        from test_survey_scaffold import _load
        import check_observations
        scaffold = _load("scaffold-survey-sheet")
        for operation in ("scaffold", "signoff", "checker"):
            for selector in (None, "bionic"):
                with self.subTest(operation=operation, selector=selector):
                    self.setUp()
                    (self.root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: bionic\n')
                    batch = (self._prepare({self.a1: "ratify", self.a2: "ratify"},
                             {self.a1: "runtime", self.a2: "runtime"}) if operation == "signoff" else None)
                    ordinary = self.tree / "ordinary-observations"
                    shutil.move(str(self.obs), str(ordinary))
                    holding = self.tree / "promptbooks/runs/PB-0001-demo/evidence/implementation-cycles/retained-repositories"
                    held = holding / "nested"
                    held.mkdir(parents=True)
                    self.obs.symlink_to(held.resolve(), target_is_directory=True)
                    before = self._inventory()
                    inode, listdir = held.stat().st_ino, os.listdir
                    def trap(path):
                        if isinstance(path, int):
                            self.assertNotEqual(os.fstat(path).st_ino, inode, "held concern enumerated")
                        return listdir(path)
                    def invoke():
                        if operation == "scaffold":
                            return scaffold.scaffold(self.root, selector, today="2026-08-30", dry_run=True)[0]
                        if operation == "signoff":
                            return SO.run(SO.make_context(self.root, selector, batch,
                                "2026-08-30"), dry_run=True)[0]
                        return len(check_observations.check(self.root, docs_dir=selector)["broken"])
                    with self._guarded_reads(), mock.patch("os.listdir", trap), self.assertRaises(
                            (ValueError, check_observations.ObservationsRefusal)) as refused:
                        invoke()
                    from admission_source_io import SourceIOExcluded
                    cause = refused.exception
                    while not isinstance(cause, SourceIOExcluded) and cause.__cause__ is not None:
                        cause = cause.__cause__
                    self.assertIsInstance(cause, SourceIOExcluded)
                    self.assertEqual(self._inventory(), before)
                    self.obs.unlink(); self.obs.symlink_to(ordinary.resolve(), target_is_directory=True)
                    with self._guarded_reads():
                        self.assertEqual(invoke(), 0)

    def _reasoning(self):
        (self.root / "src/a.py").write_text(
            '---\nrecord_type: implementation-result\n---\nQuoted constraint.\n')

    def test_scaffold_refuses_reasoning_before_sheet_and_counter(self):
        self._reasoning()
        before = _snapshot(self.root)
        result = self._run_scaffold()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(_snapshot(self.root), before)

    def _run_scaffold(self):
        from test_survey_scaffold import _run
        return _run("--repo-root", str(self.root))

    def test_initial_signoff_refuses_reasoning_before_any_surface_then_source_passes(self):
        batch = self._prepare({self.a1: "ratify", self.a2: "ratify"},
                              {self.a1: "runtime", self.a2: "runtime"})
        self._reasoning(); before = _snapshot(self.root)
        result = self._run("--batch", batch)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(_snapshot(self.root), before)
        (self.root / "src/a.py").write_text("x = 1\n")
        result = self._run("--batch", batch)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self._state(batch), "S9")

    def test_resume_refuses_reasoning_while_exact_live_rows_resume(self):
        batch = self._prepare({self.a1: "ratify", self.a2: "ratify"},
                              {self.a1: "runtime", self.a2: "runtime"})
        ctx = self._ctx(batch)
        for _ in range(5): SO.advance(ctx)
        self.assertEqual(self._state(batch), "S5")
        self._reasoning(); before = _snapshot(self.root)
        with self.assertRaises(SS.SurveySheetError): SO.advance(ctx)
        self.assertEqual(_snapshot(self.root), before)
        (self.root / "src/a.py").write_text("x = 1\n")
        self.assertEqual(SO.run(ctx, dry_run=False)[1]["state"], "S9")

    def test_scaffold_never_suggests_reserved_live_slug(self):
        adrs = self.tree / "adrs"; adrs.mkdir()
        slug = SS.proposed_slug(self.state.rows[self.a1])
        (adrs / "ADR-0001-constraint.md").write_text("---\n" + yaml.safe_dump(dict(
            id="ADR-0001", status="Accepted", governs=[dict(handle="ADR-0001/" + slug,
            domain="runtime", rule="A constraint.", scope="source", provenance="authored")])) + "---\n")
        before = _snapshot(self.root)
        result = self._run_scaffold()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(_snapshot(self.root), before)

    def test_source_drift_between_plan_and_first_write_refuses(self):
        batch = self._prepare({self.a1: "ratify", self.a2: "ratify"},
                              {self.a1: "runtime", self.a2: "runtime"})
        original = admission._revalidate
        def changed(context):
            (self.root / "src/a.py").write_text("changed = 1\n")
            return original(context)
        with mock.patch.object(admission, "_revalidate", changed), self.assertRaises(SS.SurveySheetError):
            SO.advance(self._ctx(batch))
        self.assertEqual(self._state(batch), "S0")
        self.assertFalse(SS.receipt_paths(self.obs, batch)["receipt"].exists())

    def test_reject_defer_keep_ratify_only_source_semantics(self):
        batch = self._prepare({self.a1: "reject", self.a2: "defer"})
        self._reasoning()
        result = self._run("--batch", batch)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self._state(batch), "S9")

    def test_draft_escape_remains_reviewable_and_escaped_bytes_are_never_read(self):
        import implementation_migration as migration
        from test_survey_scaffold import _load
        scaffold = _load("scaffold-survey-sheet")
        (self.root / "escape.py").symlink_to("/etc/hosts")
        self.state.rows[self.a1]["evidence"] = ["escape.py:1-1"]
        self.state.save()
        with mock.patch.object(migration, "_read", wraps=migration._read) as reader:
            code, payload = scaffold.scaffold(self.root, None, today="2026-08-30", dry_run=False)
        self.assertEqual(code, 0)
        self.assertEqual(payload["rows"], 2)
        self.assertFalse(any("escape.py" in str(call) or "/etc/hosts" in str(call)
                             for call in reader.call_args_list))
