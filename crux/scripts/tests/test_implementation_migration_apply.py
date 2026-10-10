"""Disposable transaction controls with actual close producers; no real approval/apply."""
from __future__ import annotations

import copy
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import stat
from types import SimpleNamespace
import unittest
from unittest import mock

import yaml
import _council_gate_support as sup
import implementation_migration as migration
import summaries_projection as sp
import test_implementation_authority as authority_fixtures

SCRIPTS = Path(__file__).resolve().parents[1]


def writer():
    return importlib.import_module("implementation_migration_apply")


def inventory(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and ".git" not in p.parts and not p.is_symlink()}


class PrivateBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = sup.init_repo(Path(self.temp.name) / "repo")
        (self.root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: docs\n')
        (self.root / "docs").mkdir(); (self.root / "input.txt").write_bytes(b"original\n")
        sup.commit_all(self.root, "synthetic input")

    def capture_snapshot(self,*,named=()):
        """Exercise a toy collector; production uses its canonical collectors."""
        mod=writer();context=mod._source_context(self.root)
        source_io,_=context.__enter__()
        self.addCleanup(context.__exit__,None,None,None)
        rows={};pending=[self.root]
        try:
            while pending:
                for path in source_io.roster_paths(pending.pop()):
                    if path.name==".git":continue
                    metadata=source_io.roster_metadata(path)
                    if metadata is None:continue
                    mode=metadata["st_mode"];value=None
                    if stat.S_ISDIR(mode):pending.append(path)
                    elif stat.S_ISREG(mode):value=migration._digest(source_io.read_bytes(path))
                    elif stat.S_ISLNK(mode):value=metadata["target"]
                    rows[path.relative_to(self.root).as_posix()]=(metadata,value)
            snapshot=mod._Snapshot.capture(self.root,_source_io=source_io,named=named)
        except mod.SourceIORefusal as exc:raise migration.Refused(exc.code) from None
        return SimpleNamespace(entries=rows,check=snapshot.check)

    def test_snapshot_detects_raw_change_added_removed_absent_and_head(self):
        mod = writer()
        for fault in ("changed", "added", "removed", "head"):
            with self.subTest(fault=fault):
                snap = self.capture_snapshot()
                if fault == "changed": (self.root / "input.txt").write_bytes(b"changed\n")
                if fault == "added": (self.root / "new.txt").write_bytes(b"new\n")
                if fault == "removed": (self.root / "input.txt").unlink()
                if fault == "head": sup.git(self.root, "commit", "--allow-empty", "-m", "synthetic movement")
                with self.assertRaises(migration.Refused): snap.check(self.root)
                if fault == "added": (self.root / "new.txt").unlink()
                (self.root / "input.txt").write_bytes(b"original\n")

    def test_atomic_publication_never_clobbers_competing_bytes(self):
        mod = writer(); target = self.root / "witness.json"
        staged = mod._OwnedFile.create(self.root, b"candidate\n")
        try:
            target.write_bytes(b"competitor\n")
            with self.assertRaises(migration.Refused): mod._publish_witness(staged, target)
            self.assertEqual(target.read_bytes(), b"competitor\n")
        finally: staged.cleanup()

    def test_cleanup_never_deletes_replaced_temporary(self):
        mod = writer(); staged = mod._OwnedFile.create(self.root, b"candidate\n")
        staged.path.unlink(); staged.path.write_bytes(b"other owner's bytes\n")
        with self.assertRaises(mod.StagingCleanupIncomplete):staged.cleanup()
        self.assertEqual(staged.path.read_bytes(), b"other owner's bytes\n")

    def test_partial_stream_creation_failure_cleans_or_explicitly_accounts_staging(self):
        mod = writer()
        before = inventory(self.root)
        original = mod.os.fdopen
        class PartialStream:
            def __init__(self, descriptor, mode):
                self.stream = original(descriptor, mode)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def write(self, raw):
                self.stream.write(raw[:4])
                self.stream.flush()
                raise OSError("synthetic partial ordinary write failure")
        with mock.patch.object(mod.os, "fdopen", side_effect=PartialStream):
            with self.assertRaises(OSError) as failure:
                mod._OwnedFile.create(self.root, b"intended complete staging bytes\n")
        after = inventory(self.root)
        if isinstance(failure.exception, mod.StagingCleanupIncomplete):
            self.assertTrue(all(after[path] == raw for path, raw in before.items()))
            added = set(after) - set(before)
            self.assertEqual(len(added), 1)
            self.assertEqual(next(iter(after[path] for path in added)), b"inte")
        else:
            self.assertEqual(after, before)

    def test_short_stream_write_is_explicitly_accounted(self):
        mod=writer();original=mod.os.fdopen;before=inventory(self.root)
        class ShortStream:
            def __init__(self,descriptor,mode):self.stream=original(descriptor,mode)
            def __enter__(self):return self
            def __exit__(self,*args):self.stream.close()
            def write(self,raw):return self.stream.write(raw[:4])
            def flush(self):self.stream.flush()
            def fileno(self):return self.stream.fileno()
        with mock.patch.object(mod.os,"fdopen",side_effect=ShortStream), \
                self.assertRaises(mod.StagingCleanupIncomplete):
            mod._OwnedFile.create(self.root,b"intended complete staging bytes\n")
        after=inventory(self.root)
        self.assertTrue(all(after[path]==raw for path,raw in before.items()))
        self.assertEqual([after[path] for path in set(after)-set(before)],[b"inte"])

    def test_stream_failure_preserves_observed_replacement_bytes(self):
        mod=writer();original=mod.os.fdopen;replacement=[];root=self.root
        class ReplacedStream:
            def __init__(self,descriptor,mode):self.stream=original(descriptor,mode)
            def __enter__(self):return self
            def __exit__(self,*args):self.stream.close()
            def write(self,raw):
                self.stream.write(raw[:4]);self.stream.flush()
                path=next(root.glob(".migration-apply-*.tmp"))
                path.unlink();path.write_bytes(b"other owner's bytes\n");replacement.append(path)
                raise OSError("synthetic ordinary stream failure")
        with mock.patch.object(mod.os,"fdopen",side_effect=ReplacedStream), \
                self.assertRaises(mod.StagingCleanupIncomplete):
            mod._OwnedFile.create(self.root,b"intended complete staging bytes\n")
        self.assertEqual(replacement[0].read_bytes(),b"other owner's bytes\n")

    def test_changed_staging_parent_reports_incomplete_without_touching_replacement(self):
        mod=writer();staged=mod._OwnedFile.create(self.root,b"candidate\n")
        moved=self.root.with_name("moved-repository")
        self.root.rename(moved);self.root.mkdir()
        replacement=self.root/staged.path.name
        replacement.write_bytes(b"other owner's bytes\n")
        with self.assertRaises(mod.StagingCleanupIncomplete):staged.cleanup()
        self.assertEqual(replacement.read_bytes(),b"other owner's bytes\n")
        self.assertEqual((moved/staged.path.name).read_bytes(),b"candidate\n")

    def test_mutation_phase_is_recorded_before_parent_close_failure(self):
        mod=writer();original=mod._Parent.close
        def close_then_fail(parent):
            original(parent)
            if observed:raise OSError("synthetic directory close failure")
        for helper,keyword in (("_publish_witness","published"),("_replace_staged","written")):
            with self.subTest(helper=helper):
                staged=mod._OwnedFile.create(self.root,b"candidate\n")
                target=self.root/(helper+".json");observed=[]
                try:
                    with mock.patch.object(mod._Parent,"close",new=close_then_fail), \
                            self.assertRaises(OSError):
                        getattr(mod,helper)(staged,target,**{keyword:lambda:observed.append("written")})
                    self.assertEqual(target.read_bytes(),b"candidate\n")
                    self.assertEqual(observed,["written"])
                finally:staged.cleanup()

    def test_cache_boundary_preserves_observed_destination_change(self):
        mod=writer();target=self.root/"cache.json";target.write_bytes(b"original\n")
        expected=mod._target_identity(target)
        staged=mod._OwnedFile.create(self.root,b"candidate\n");observed=[]
        target.write_bytes(b"other owner's bytes\n")
        try:
            with self.assertRaises(migration.Refused):
                mod._replace_staged(staged,target,expected=expected,written=lambda:observed.append("written"))
            self.assertEqual(target.read_bytes(),b"other owner's bytes\n")
            self.assertEqual(observed,[])
        finally:staged.cleanup()

    def test_snapshot_config_and_roster_use_guarded_reads(self):
        mod=writer()
        with mock.patch.object(Path,"read_text",side_effect=AssertionError("unguarded source read")), \
                mock.patch.object(mod.os,"walk",side_effect=AssertionError("unguarded source enumeration")):
            snapshot=self.capture_snapshot()
            snapshot.check(self.root)

    def test_snapshot_preserves_named_output_bound_without_expanding_context(self):
        mod=writer();output=self.root/"docs/adrs/summaries/_meta.json"
        output.parent.mkdir(parents=True);output.write_bytes(b"x"*(2*1024*1024+1))
        self.capture_snapshot()
        authored=self.root/"authored.txt";authored.write_bytes(output.read_bytes())
        with self.assertRaises(migration.Refused):self.capture_snapshot()

    def test_snapshot_does_not_inspect_external_alias_targets_before_guarded_roster(self):
        mod=writer();outside=Path(self.temp.name).resolve()/"ordinary-outside"
        outside.mkdir();(outside/"ordinary.txt").write_bytes(b"outside fixture\n")
        (self.root/"ordinary-link").symlink_to(outside,target_is_directory=True)
        original=Path.lstat
        def contained_metadata(path,*args,**options):
            self.assertFalse(path.is_relative_to(outside),"raw metadata inspected external alias target")
            return original(path,*args,**options)
        with mock.patch.object(Path,"lstat",new=contained_metadata):
            snapshot=self.capture_snapshot();snapshot.check(self.root)
        self.assertIn("ordinary-link",snapshot.entries)
        self.assertNotIn("ordinary-link/ordinary.txt",snapshot.entries)

    def test_target_identity_refuses_parent_alias_before_lexical_leaf_metadata(self):
        mod=writer();outside=Path(self.temp.name).resolve()/"ordinary-outside"
        outside.mkdir();(outside/"ordinary.txt").write_bytes(b"outside fixture\n")
        alias=self.root/"ordinary-link";alias.symlink_to(outside,target_is_directory=True)
        target=alias/"ordinary.txt";original=Path.lstat
        def anchored_metadata(path,*args,**options):
            self.assertNotEqual(path,target,"raw leaf metadata preceded parent validation")
            return original(path,*args,**options)
        with mock.patch.object(Path,"lstat",new=anchored_metadata),self.assertRaises(migration.Refused):
            mod._target_identity(target)

    def test_holding_layout_uses_guarded_metadata_and_revalidates_observations(self):
        mod=writer()
        holding=self.root/"docs/promptbooks/runs/PB-9999-demo/evidence/implementation-cycles/retained-repositories"
        holding.mkdir(parents=True)
        with mock.patch.object(Path,"lstat",side_effect=AssertionError("raw holding layout metadata")):
            with mod._source_context(self.root) as (source_io,_):
                self.assertIsNone(source_io.kind(holding/"uninspected-suffix"))
                source_io.revalidate()
        with mod._source_context(self.root) as (source_io,_):
            self.assertIsNone(source_io.kind(holding/"uninspected-suffix"))
            holding.rename(holding.with_name("ordinary-renamed"));holding.mkdir()
            with self.assertRaises(mod.SourceIORefusal):source_io.revalidate()

    def test_guarded_holding_layout_port_preserves_missing_and_non_directory_results(self):
        mod=writer();root=self.root/"docs"
        holding=root/"promptbooks/runs/PB-9999-demo/evidence/implementation-cycles/retained-repositories"
        with contextlib.closing(mod.SourceIO(self.root)) as source_io:
            self.assertFalse(mod.retained_evidence._layout_directory(root,holding,_source_io=source_io))
        holding.parent.mkdir(parents=True);holding.write_bytes(b"ordinary invalid layout\n")
        with contextlib.closing(mod.SourceIO(self.root)) as source_io:
            with self.assertRaises(mod.retained_evidence.RetainedEvidenceRefusal):
                mod.retained_evidence._layout_directory(root,holding,_source_io=source_io)

    def test_application_rosters_prune_held_aliases_and_keep_ordinary_aliases(self):
        mod=writer()
        holding=self.root/"docs/promptbooks/runs/PB-9999-demo/evidence/implementation-cycles/retained-repositories"
        holding.mkdir(parents=True);(holding/"reasoning.md").write_bytes(b"retained reasoning\n")
        alias=self.root/"held-alias";alias.symlink_to(holding,target_is_directory=True)
        ordinary=self.root/"ordinary-alias";ordinary.symlink_to(self.root/"input.txt")
        with mod._source_context(self.root) as (source_io,_):
            self.assertIsNone(source_io.kind(alias))
            self.assertEqual(source_io.glob(alias,"*"),[])
            self.assertNotIn(alias,source_io.glob(self.root,"*"))
            self.assertEqual(source_io.read_bytes(ordinary),b"original\n")
            source_io.revalidate()

    def test_application_held_source_reads_and_resolution_refuse(self):
        mod=writer()
        holding=self.root/"docs/promptbooks/runs/PB-9999-demo/evidence/implementation-cycles/retained-repositories"
        holding.mkdir(parents=True);(holding/"reasoning.md").write_bytes(b"retained reasoning\n")
        alias=self.root/"held-alias";alias.symlink_to(holding,target_is_directory=True)
        for method in ("read_bytes","resolve"):
            with self.subTest(method=method),self.assertRaises(migration.Refused):
                with mod._source_context(self.root) as (source_io,_):
                    getattr(source_io,method)(alias/"reasoning.md")

    def test_snapshot_preserves_symlink_leaf_without_following_its_target(self):
        mod=writer();outside=Path(self.temp.name)/"ordinary-external.txt"
        outside.write_bytes(b"external ordinary fixture\n")
        alias=self.root/"ordinary-link";alias.symlink_to(outside)
        original=mod.SourceIO._walk
        def parent_walk_only(source_io,path,**options):
            self.assertNotEqual(Path(path),alias,"snapshot followed a symlink leaf")
            return original(source_io,path,**options)
        with mock.patch.object(mod.SourceIO,"_walk",new=parent_walk_only):
            snapshot=self.capture_snapshot();snapshot.check(self.root)
        self.assertEqual(snapshot.entries["ordinary-link"][1],str(outside))

    def test_guarded_config_bootstrap_refuses_held_route_before_admitting_context(self):
        mod=writer()
        holding=self.root/"docs/promptbooks/runs/PB-9999-demo/evidence/implementation-cycles/retained-repositories"
        holding.mkdir(parents=True);configuration=holding/"configuration.yml"
        configuration.write_text('config_version: "1"\ndocs_dir: docs\n')
        (self.root/".bionic.yml").unlink();(self.root/".bionic.yml").symlink_to(configuration)
        with self.assertRaises(migration.Refused):
            with mod._source_context(self.root):
                self.fail("held configuration was admitted after bootstrap")

    def test_snapshot_prunes_declared_holding_and_keeps_ordinary_same_basename(self):
        mod = writer()
        held = self.root / "docs/promptbooks/runs/PB-9999-demo/evidence/implementation-cycles/retained-repositories/demo"; held.mkdir(parents=True)
        ordinary = self.root / "ordinary/demo"; ordinary.mkdir(parents=True)
        (held / "data.txt").write_bytes(b"held")
        (ordinary / "data.txt").write_bytes(b"ordinary")
        snap = self.capture_snapshot()
        (held / "data.txt").write_bytes(b"changed held")
        snap.check(self.root)
        (ordinary / "data.txt").write_bytes(b"changed ordinary")
        with self.assertRaises(migration.Refused): snap.check(self.root)

    def test_modified_or_replaced_staging_cannot_publish_and_cleanup_preserves_other_bytes(self):
        mod=writer()
        for replace in (False,True):
            with self.subTest(replace=replace):
                staged=mod._OwnedFile.create(self.root,b"candidate\n")
                if replace:staged.path.unlink()
                staged.path.write_bytes(b"independent actor bytes\n")
                target=self.root/"candidate.json"
                with self.assertRaises(migration.Refused):mod._publish_witness(staged,target)
                self.assertFalse(target.exists())
                with self.assertRaises(mod.StagingCleanupIncomplete):staged.cleanup()
                self.assertEqual(staged.path.read_bytes(),b"independent actor bytes\n")
                staged.path.unlink()

    def test_index_stage_or_flag_movement_invalidates_snapshot(self):
        mod=writer()
        snap=self.capture_snapshot()
        sup.git(self.root,"update-index","--assume-unchanged","input.txt")
        with self.assertRaises(migration.Refused):snap.check(self.root)
        sup.git(self.root,"update-index","--no-assume-unchanged","input.txt")
        snap=self.capture_snapshot()
        (self.root/"input.txt").write_bytes(b"staged changed\n");sup.git(self.root,"add","input.txt")
        with self.assertRaises(migration.Refused):snap.check(self.root)

    def test_restoring_original_source_bytes_does_not_restore_snapshot_identity(self):
        mod = writer()
        path = self.root / "input.txt"
        original = path.read_bytes()
        info = path.stat()
        snap = self.capture_snapshot()
        path.write_bytes(b"transient parser input\n")
        path.write_bytes(original)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertEqual(path.read_bytes(), original)
        with self.assertRaises(migration.Refused):
            snap.check(self.root)

    def test_restoring_original_directory_after_path_swap_refuses(self):
        mod = writer()
        original = self.root / "sources"
        original.mkdir()
        (original / "record.md").write_bytes(b"original record\n")
        snap = self.capture_snapshot()
        original.rename(self.root / "saved-sources")
        original.mkdir()
        (original / "record.md").write_bytes(b"transient record\n")
        shutil.rmtree(original)
        (self.root / "saved-sources").rename(original)
        self.assertEqual((original / "record.md").read_bytes(), b"original record\n")
        with self.assertRaises(migration.Refused):
            snap.check(self.root)

    def test_explicit_named_input_keeps_snapshot_row_shape_and_raw_identity(self):
        mod = writer()
        path = self.root / "input.txt"
        ref = dict(path="input.txt", sha256=migration._digest(path.read_bytes()))
        snap = self.capture_snapshot(named=[ref])
        snap.check(self.root)
        path.write_bytes(b"changed named input\n")
        with self.assertRaises(migration.Refused):
            snap.check(self.root)


class _ActualCloseFixture(unittest.TestCase):
    """Build one closed repository per class; every test mutates a private copy."""

    @classmethod
    def setUpClass(cls):
        cls.fixture = authority_fixtures.PublicationProof(methodName="runTest"); cls.fixture.setUp()
        cls.addClassCleanup(cls.fixture.doCleanups)
        root = cls.fixture.root; adrs = root / "docs/adrs"
        # Complete ordinary canonical models before the actual auxiliary close.
        for path in adrs.glob("ADR-*.md"):
            text = path.read_text(); doc = sp.read_frontmatter(text)
            for rule in doc.get("governs", []):
                rule.setdefault("domain", "testing"); rule.setdefault("scope", "reader")
                rule.setdefault("provenance", "authored"); rule.setdefault("rule", "Preserve behavior.")
            if doc["id"] == "ADR-0110":
                doc["governs"].append(dict(handle="ADR-0110/surviving",domain="testing",
                    scope="reader",provenance="authored",rule="Preserve the surviving clause."))
            path.write_text("---\n"+yaml.safe_dump(doc)+"---\n"+text.split("---",2)[2].lstrip("\n"))
        (adrs / "ADR-0109-predecessor.md").write_text("---\n"+yaml.safe_dump(dict(id="ADR-0109",status="Accepted",
            governs=[dict(handle="ADR-0109/old-rotation",domain="testing",scope="reader",provenance="authored",rule="Earlier rule.")]))+"---\n")
        (root / "docs/manifest.yml").write_text('schema_version: "5"\nconcerns_enabled: [adrs]\nadr:\n  governs_from: 1\n')
        plugin = root / "crux/skills/example"; plugin.mkdir(parents=True)
        (plugin / "SKILL.md").write_text('Use rule:rotation-preserves-assessment-outcomes.\n')
        (root / "crux/catalog").mkdir()
        probe=root/"crux/scripts/generate-rules-catalog.py";probe.parent.mkdir()
        probe.write_text("# Disposable authoring-source presence fixture.\n")
        sup.commit_all(root, "synthetic complete projection sources")
        cls.fixture.close()  # Real advance-run --migration-batch producer and committed close.

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"; shutil.copytree(self.fixture.root, self.root)
        self.root = self.root.resolve()
        self.batch_path = self.fixture.f.rel
        self.run_path = self.root / self.fixture.f.run_path.relative_to(self.fixture.root)
        self.witness = self.root / self.fixture.witness.relative_to(self.fixture.root)

    def command(self):
        return subprocess.run([sys.executable,str(SCRIPTS/"implementation-migration.py"),"apply",
            "--repo-root",str(self.root),"--batch",self.batch_path],capture_output=True,text=True,env=sup.scrubbed_env())

    def in_process_cli(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = writer()._driver("implementation-migration").main(
                ["apply", "--repo-root", str(self.root), "--batch", self.batch_path])
        return code, stdout.getvalue(), stderr.getvalue()

    @contextlib.contextmanager
    def context_close_fault(self,kind):
        mod=writer();created=[]
        original_init=mod._ApplicationIO.__init__;original_close=mod.SourceIO.close
        def remember(source_io,*args,**options):
            original_init(source_io,*args,**options);created.append(source_io)
        def fail_selected(source_io):
            original_close(source_io)
            if created:
                selected=created[0] if kind=="main" else created[0]._layout_io
                if source_io is selected:raise OSError("synthetic context teardown failure")
        with mock.patch.object(mod._ApplicationIO,"__init__",new=remember), \
                mock.patch.object(mod.SourceIO,"close",new=fail_selected):
            yield

    def assert_close_failure(self,code,out,err,state):
        self.assertEqual(code,2)
        self.assertTrue(out,"context teardown discarded the operation report")
        report=json.loads(out);self.assertEqual(report["state"],state)
        self.assertEqual(report["authority"],"none")
        self.assertEqual(report["failure_class"],"capability")
        self.assertEqual(report["limit"],"migration-apply-context-close-unavailable")
        self.assertEqual(json.loads(err)["limit"],report["limit"])
        return report

    @classmethod
    def committed_fixture(cls):
        """One apply and witness commit per class, built on first use from the class fixture.

        Each test that needs the committed application copies this repository; none
        writes to it."""
        if "_committed" not in cls.__dict__:
            temp=tempfile.TemporaryDirectory();cls.addClassCleanup(temp.cleanup)
            root=Path(temp.name)/"repo";shutil.copytree(cls.fixture.root,root);root=root.resolve()
            result=writer().apply(root,cls.fixture.f.rel)
            if result["state"]!="pending_commit":
                raise AssertionError("committed fixture apply: %r"%(result,))
            sup.commit_all(root,"synthetic actual apply witness commit")
            cls._committed=(root,sup.git(root,"rev-parse","HEAD").strip())
            cls.addClassCleanup(delattr,cls,"_committed")
        return cls._committed

    def committed_application(self):
        root,commit=self.committed_fixture()
        shutil.rmtree(self.root);shutil.copytree(root,self.root)
        return commit

    def historical_locator_bytes(self):
        mod = writer()
        proof, run_rel = mod._select_close(self.root, self.batch_path)
        batch = migration.load_batch(self.root, self.batch_path)
        canonical, _ = mod._locator(proof, run_rel, batch)
        historical = (json.dumps(json.loads(canonical), sort_keys=True) + "\n").encode("utf-8")
        self.assertNotEqual(canonical, historical)
        return canonical, historical



class ActualCloseTests(_ActualCloseFixture):
    def test_unique_exact_close_discovery_calls_shared_history(self):
        mod = writer(); before = inventory(self.root)
        proof, run_rel = mod._select_close(self.root, self.batch_path)
        self.assertEqual(proof.binding["batch"]["path"], self.batch_path)
        self.assertEqual(run_rel, self.run_path.relative_to(self.root).as_posix())
        self.assertEqual(inventory(self.root), before)

    def test_missing_ambiguous_and_malformed_matching_close_refuse_without_writes(self):
        mod = writer(); original = self.run_path.read_bytes()
        for fault in ("missing", "ambiguous", "malformed", "wrong-sha", "wrong-slot"):
            with self.subTest(fault=fault):
                self.run_path.write_bytes(original); doc=yaml.safe_load(original)
                if fault == "missing": doc["migration_bindings"]=[]
                if fault == "ambiguous": doc["migration_bindings"]*=2
                if fault == "malformed": doc["migration_bindings"][0]["unexpected"]=True
                if fault == "wrong-sha": doc["migration_bindings"][0]["batch"]["sha256"]="0"*64
                if fault == "wrong-slot": doc["migration_bindings"][0]["slot"]="implementation-2"
                self.run_path.write_text(yaml.safe_dump(doc)); sup.commit_all(self.root,"synthetic close fault")
                before=inventory(self.root)
                with self.assertRaises(migration.Refused): mod._select_close(self.root,self.batch_path)
                self.assertEqual(inventory(self.root),before)

    def test_initial_apply_only_publishes_exact_pending_witness(self):
        before=inventory(self.root)
        result=writer().apply(self.root,self.batch_path)
        self.assertEqual(result["state"],"pending_commit")
        self.assertTrue(self.witness.is_file())
        self.assertNotIn("publication_commit",result["publication"])
        after=inventory(self.root)
        self.assertEqual(set(after)-set(before),{self.witness.relative_to(self.root).as_posix()})
        self.assertTrue(all(after[p]==raw for p,raw in before.items()))
        with self.assertRaises(migration.Refused): migration.authority_view(self.root)

    def test_shared_formatter_preserves_exact_existing_locator_bytes(self):
        mod = writer()
        proof, run_rel = mod._select_close(self.root, self.batch_path)
        batch = migration.load_batch(self.root, self.batch_path)
        binding = proof.binding
        for serialized_run in (run_rel, "docs/\u00e9/run-RUN-001.yaml"):
            with self.subTest(serialized_run=serialized_run):
                historical = dict(record_type=migration.PUBLICATION_RECORD_TYPE,
                    format_version="1", publisher="implementation-migration/1",
                    run_path=serialized_run,
                    binding_sha256=migration._canonical_digest(binding),
                    declaration_sha256=migration._canonical_digest(proof.slot),
                    **{key: binding[key] for key in
                       ("batch", "book_id", "run_id", "book_content_hash", "slot", "gate_prompt")})
                expected = (json.dumps(historical, sort_keys=True, indent=2,
                    ensure_ascii=False) + "\n").encode("utf-8")
                raw, pending = mod._locator(proof, serialized_run, batch)
                self.assertEqual(migration._publication_locator_bytes(proof, serialized_run), expected)
                self.assertEqual(raw, expected)
                self.assertEqual(set(pending), {"path", "sha256", "close_identity"})
                self.assertEqual(pending["sha256"], migration._digest(expected))

    def test_all_seven_candidate_models_compile_without_commit_placeholder(self):
        mod=writer();proof,run_rel=mod._select_close(self.root,self.batch_path)
        batch=migration.load_batch(self.root,self.batch_path);facts=migration._validated_inputs(self.root,proof)
        raw,pending=mod._locator(proof,run_rel,batch);before=inventory(self.root)
        plans=mod._compile_models(self.root,facts,pending,[],batch)
        self.assertEqual(inventory(self.root),before)
        self.assertEqual(sum(len(plan) for plan in plans),7)
        self.assertEqual(set(pending),{"path","sha256","close_identity"})
        for size in (40,64):
            outputs=mod._materialize(plans,pending,dict(path=pending["path"],sha256=pending["sha256"],publication_commit="a"*size))
            self.assertEqual(len(outputs),7)
            resolver=json.loads(outputs[self.root/"docs/adrs/summaries/resolver.json"])
            self.assertNotIn("ADR-0110/rotation",resolver)
            self.assertIn("ADR-0110/surviving",resolver)
            self.assertIn("old-rotation",resolver["retired_slugs"])
            self.assertEqual(json.loads(outputs[self.root/"docs/adrs/summaries/_meta.json"])["migration"]["publication"]["publication_commit"],"a"*size)

    def test_invalid_late_catalog_policy_refuses_before_publication(self):
        mod=writer();source=self.root/"crux/skills/example/SKILL.md"
        source.write_text("Use rule:unresolved-fixture.\n");sup.commit_all(self.root,"synthetic mutable historical citation")
        before=inventory(self.root)
        with self.assertRaises((migration.Refused,sp.GovernsValidationError)): mod.apply(self.root,self.batch_path)
        self.assertEqual(inventory(self.root),before)

    def test_recovery_reader_preserves_canonical_catalog_refusal_identity(self):
        import test_recover
        mod=writer()
        case=test_recover.ObservationReaderContainmentTests(
            "test_the_summaries_projection_never_quotes_the_frontmatter_either")
        # Restore the cache even when the earlier test leaks a replacement.
        # Exercise the existing reader before the real catalog refusal.
        with mock.patch.dict(sys.modules):
            importlib.import_module("test_survey_signoff")
            importlib.import_module("test_survey_postconditions")
            result=unittest.TestResult()
            case.run(result)
            self.assertEqual(len(result.errors),0)
            self.assertEqual(len(result.failures),0)
            self.test_invalid_late_catalog_policy_refuses_before_publication()
            self.assertIs(sys.modules["summaries_projection"],mod.sp)

    def test_candidate_ledger_alias_refuses_before_new_direct_read(self):
        mod = writer()
        proof, run_rel = mod._select_close(self.root, self.batch_path)
        batch = migration.load_batch(self.root, self.batch_path)
        facts = migration._validated_inputs(self.root, proof)
        _, pending = mod._locator(proof, run_rel, batch)
        ledger = self.root / "docs/adrs/doctrine/reconciliations.yml"
        outside = Path(self.temp.name) / "outside-ledger.yml"
        outside.write_bytes(ledger.read_bytes())
        ledger.unlink()
        ledger.symlink_to(outside)
        original = Path.read_bytes
        reads = []
        def observed(path):
            if path == ledger:
                reads.append(path)
            return original(path)
        with mock.patch.object(Path, "read_bytes", new=observed):
            try:
                mod._compile_models(self.root, facts, pending, [], batch)
            except (migration.Refused, sp.GovernsValidationError):
                pass
        self.assertEqual(reads, [])
        with self.assertRaises(migration.Refused):
            mod._compile_models(self.root, facts, pending, [], batch)

    def test_actual_cli_pending_repeat_commit_refresh_public_equality_and_idempotence(self):
        first=self.command();self.assertEqual(first.returncode,0,first.stdout+first.stderr)
        pending=json.loads(first.stdout);self.assertEqual(pending["state"],"pending_commit")
        saved=inventory(self.root);repeat=self.command()
        self.assertEqual(repeat.returncode,0,repeat.stdout+repeat.stderr)
        self.assertEqual(json.loads(repeat.stdout)["state"],"pending_commit")
        self.assertEqual(inventory(self.root),saved)
        sup.commit_all(self.root,"synthetic actual CLI witness commit")
        first_commit=sup.git(self.root,"rev-parse","HEAD").strip()
        result=self.command();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        report=json.loads(result.stdout);self.assertEqual(report["state"],"refreshed")
        self.assertEqual(len(report["written"]),7)
        self.assertEqual(report["publication"]["publication_commit"],first_commit)
        expected=writer()._driver("summarize-adrs").build(self.root)
        expected.update(writer()._driver("compile-doctrine").build(self.root))
        catalog=writer()._driver("generate-rules-catalog")
        expected[self.root/"crux/catalog/rules.json"]=catalog.render(catalog.build_catalog(self.root,self.root/"crux"))
        self.assertEqual(len(expected),7)
        for path,body in expected.items():
            self.assertEqual(path.read_bytes(),body.encode() if isinstance(body,str) else body)
        sup.commit_all(self.root,"synthetic cache output commit")
        saved=inventory(self.root);again=self.command()
        self.assertEqual(again.returncode,0,again.stdout+again.stderr)
        self.assertEqual(json.loads(again.stdout)["state"],"no_op")
        self.assertEqual(json.loads(again.stdout)["publication"]["publication_commit"],first_commit)
        self.assertEqual(inventory(self.root),saved)
        for script in ("summarize-adrs.py","compile-doctrine.py","generate-rules-catalog.py"):
            gate=subprocess.run([sys.executable,str(SCRIPTS/script),"--repo-root",str(self.root),"--dry-run"],
                capture_output=True,text=True,env=sup.scrubbed_env())
            self.assertEqual(gate.returncode,0,gate.stdout+gate.stderr)
            self.assertFalse(json.loads(gate.stdout).get("surface_absent",False))

    def test_source_membership_and_target_races_before_visibility_preserve_other_bytes(self):
        mod=writer();original=mod._compile_models
        for fault in ("source","member","target","head"):
            with self.subTest(fault=fault):
                root_head=sup.git(self.root,"rev-parse","HEAD").strip()
                before=inventory(self.root);changed=None
                def raced(*args,**options):
                    nonlocal changed
                    plan=original(*args,**options)
                    if fault=="source":
                        changed=self.root/"docs/adrs/ADR-0110-source.md";changed.write_text(changed.read_text()+"changed\n")
                    if fault=="member":changed=self.root/"docs/adrs/ADR-0002-added.md";changed.write_bytes(b"new member\n")
                    if fault=="target":changed=self.witness;changed.write_bytes(b"competing publisher\n")
                    if fault=="head":sup.git(self.root,"commit","--allow-empty","-m","synthetic movement")
                    return plan
                with mock.patch.object(mod,"_compile_models",side_effect=raced),self.assertRaises(migration.Refused):
                    mod.apply(self.root,self.batch_path)
                self.assertFalse(any(p.name.startswith(".migration-apply-") for p in self.witness.parent.iterdir()))
                if changed:
                    self.assertTrue(changed.is_file())
                    if changed.relative_to(self.root).as_posix() in before:changed.write_bytes(before[changed.relative_to(self.root).as_posix()])
                    else:changed.unlink()
                if fault=="head":sup.git(self.root,"reset","--hard",root_head)
                self.assertEqual(inventory(self.root),before)

    def test_model_refusal_staging_io_and_unowned_temporary_before_visibility_do_not_publish(self):
        mod=writer();before=inventory(self.root)
        with mock.patch.object(mod._OwnedFile,"create",side_effect=OSError("synthetic staging failure")),self.assertRaises(OSError):
            mod.apply(self.root,self.batch_path)
        self.assertEqual(inventory(self.root),before)
        path=self.root/"docs/adrs/doctrine/index.md.tmp";path.write_bytes(b"unowned temporary")
        before=inventory(self.root)
        with self.assertRaises((migration.Refused,OSError,sp.GovernsValidationError)):mod.apply(self.root,self.batch_path)
        self.assertEqual(inventory(self.root),before);self.assertEqual(path.read_bytes(),b"unowned temporary")

    def test_failure_after_witness_visibility_reports_pending_and_preserves_witness(self):
        mod=writer()
        with mock.patch.object(mod,"_directory_sync",side_effect=OSError("synthetic durability failure")):
            code, stdout, stderr = self.in_process_cli()
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stderr)["limit"], "migration-apply-publication-unavailable")
        self.assertTrue(stdout, "A visible witness must retain its pending phase report.")
        result=json.loads(stdout)
        self.assertEqual(result["state"],"pending_commit")
        self.assertEqual(result["limit"],"migration-apply-publication-unavailable")
        self.assertTrue(self.witness.is_file());self.assertNotIn("publication_commit",result["publication"])
        with self.assertRaises(migration.Refused):migration.authority_view(self.root)

    def test_cleanup_failure_after_visibility_preserves_truthful_pending(self):
        mod = writer()
        original = os.unlink
        def fail_owned(path, *args, **kwargs):
            if Path(path).name.startswith(".migration-apply-"):
                raise OSError("synthetic owned cleanup failure")
            return original(path, *args, **kwargs)
        with mock.patch.object(mod.os, "unlink", new=fail_owned):
            code, stdout, stderr = self.in_process_cli()
        self.assertEqual(code, 2)
        self.assertTrue(stdout, "A visible witness must retain its pending phase report.")
        result = json.loads(stdout)
        self.assertEqual(json.loads(stderr)["limit"], "migration-apply-staging-cleanup-incomplete")
        self.assertEqual(result["state"], "pending_commit")
        self.assertEqual(result["limit"], "migration-apply-staging-cleanup-incomplete")
        self.assertTrue(self.witness.is_file())
        self.assertEqual(result["written"], [self.witness.relative_to(self.root).as_posix()])
        with self.assertRaises(migration.Refused):
            migration.authority_view(self.root)

    def test_cleanup_failure_before_visibility_is_bounded_capability_not_unchanged_refusal(self):
        mod = writer()
        original = os.unlink
        def fail_owned(path, *args, **kwargs):
            if Path(path).name.startswith(".migration-apply-"):
                raise OSError("synthetic owned cleanup failure")
            return original(path, *args, **kwargs)
        with mock.patch.object(mod, "_publish_witness", side_effect=migration.Refused("synthetic-refusal")), \
                mock.patch.object(mod.os, "unlink", new=fail_owned):
            code, stdout, stderr = self.in_process_cli()
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["limit"], "migration-apply-staging-cleanup-incomplete")
        self.assertFalse(self.witness.exists())
        self.assertTrue(any(p.name.startswith(".migration-apply-") for p in self.witness.parent.iterdir()))

    def test_conflicting_pending_bytes_and_staged_index_refuse_without_overwrite(self):
        mod=writer();pending=mod.apply(self.root,self.batch_path)
        self.assertEqual(pending["state"],"pending_commit")
        raw=self.witness.read_bytes();self.witness.write_bytes(b"conflicting witness\n")
        before=inventory(self.root)
        with self.assertRaises(migration.Refused):mod.apply(self.root,self.batch_path)
        self.assertEqual(inventory(self.root),before)
        sup.git(self.root,"add",self.witness.relative_to(self.root).as_posix());self.witness.write_bytes(raw)
        before=inventory(self.root)
        with self.assertRaises(migration.Refused):mod.apply(self.root,self.batch_path)
        self.assertEqual(inventory(self.root),before)

    def test_untracked_and_staged_noncanonical_locator_never_qualifies_as_pending(self):
        _, historical = self.historical_locator_bytes()
        self.witness.write_bytes(historical)
        for staged in (False, True):
            with self.subTest(staged=staged):
                if staged:
                    sup.git(self.root, "add", self.witness.relative_to(self.root).as_posix())
                before = inventory(self.root)
                result = self.command()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)["authority"], "none")
                self.assertEqual(inventory(self.root), before)
                with self.assertRaises(migration.Refused):
                    migration.authority_view(self.root)

    def test_existing_locator_digest_claim_race_refuses_without_adopting_changed_bytes(self):
        mod = writer()
        canonical, historical = self.historical_locator_bytes()
        self.witness.write_bytes(historical)
        sup.commit_all(self.root, "synthetic historical-format publication before target race")
        before = inventory(self.root)
        original = mod._target_identity
        raced = []
        def change_after_claim(path):
            identity = original(path)
            if Path(path) == self.witness and identity is not None and not raced:
                raced.append(path)
                self.witness.write_bytes(canonical)
            return identity
        with mock.patch.object(mod, "_target_identity", side_effect=change_after_claim):
            code, stdout, stderr = self.in_process_cli()
        self.assertTrue(raced)
        self.assertEqual(code, 1, stdout + stderr)
        self.assertEqual(json.loads(stdout)["authority"], "none")
        self.assertEqual(self.witness.read_bytes(), canonical)
        after = inventory(self.root)
        rel = self.witness.relative_to(self.root).as_posix()
        self.assertEqual({key: value for key, value in after.items() if key != rel},
                         {key: value for key, value in before.items() if key != rel})



class ApplyPlanningTests(_ActualCloseFixture):
    def test_dry_run_plans_all_seven_readonly_and_public_cli_reports_readiness(self):
        mod=writer();before=inventory(self.root)
        with mock.patch.object(mod._OwnedFile,"create",side_effect=AssertionError("dry-run staged bytes")):
            report=mod.dry_run(self.root,self.batch_path)
        self.assertTrue(report["ready_to_apply"]);self.assertEqual(report["authority"],"none")
        self.assertEqual(report["approval"]["state"],"PROVED")
        self.assertEqual(report["application"]["state"],"UNOBSERVED")
        self.assertEqual(report["planned_state"],"pending_commit")
        self.assertEqual(len(report["planned_outputs"]),7);self.assertEqual(report["written"],[])
        self.assertNotIn("publication_commit",report["publication"])
        self.assertEqual(report["planned_writes"],[self.witness.relative_to(self.root).as_posix()])
        self.assertEqual(report["output_bytes"]["state"],"commit-dependent")
        self.assertEqual(inventory(self.root),before)
        command=subprocess.run([sys.executable,str(SCRIPTS/"implementation-migration.py"),"dry-run",
            "--repo-root",str(self.root),"--batch",self.batch_path],capture_output=True,text=True,env=sup.scrubbed_env())
        self.assertEqual(command.returncode,0,command.stdout+command.stderr)
        self.assertEqual(json.loads(command.stdout),migration.dry_run(self.root,self.batch_path))
        self.assertEqual(inventory(self.root),before)

    def test_dry_run_existing_pending_witness_plans_no_write_or_activation(self):
        mod=writer();mod.apply(self.root,self.batch_path);before=inventory(self.root)
        report=mod.dry_run(self.root,self.batch_path)
        self.assertTrue(report["ready_to_apply"]);self.assertEqual(report["planned_state"],"pending_commit")
        self.assertEqual(report["planned_writes"],[]);self.assertEqual(report["written"],[])
        self.assertEqual(report["output_bytes"]["state"],"commit-dependent")
        self.assertNotIn("publication_commit",report["publication"])
        self.assertEqual(report["application"]["state"],"UNOBSERVED")
        self.assertEqual(inventory(self.root),before)

    def test_dry_run_preserves_actual_refusal_and_never_readies_invalid_late_model(self):
        mod=writer();source=self.root/"crux/skills/example/SKILL.md"
        source.write_text("Use rule:unresolved-fixture.\n");sup.commit_all(self.root,"synthetic late model refusal")
        before=inventory(self.root);report=mod.dry_run(self.root,self.batch_path)
        self.assertFalse(report["ready_to_apply"]);self.assertEqual(report["failure_class"],"validation")
        self.assertEqual(report["authority"],"none");self.assertEqual(report["written"],[])
        self.assertEqual(report["limit"],"migration-application-model-refused")
        self.assertEqual(inventory(self.root),before)

    def test_dry_run_reports_unsigned_close_refusal_readonly(self):
        mod=writer();self.run_path.unlink();before=inventory(self.root)
        report=mod.dry_run(self.root,self.batch_path)
        self.assertFalse(report["ready_to_apply"])
        self.assertEqual(report["approval"]["state"],"UNOBSERVED")
        self.assertEqual(report["application"]["state"],"UNOBSERVED")
        self.assertEqual(report["limit"],"migration-apply-close-missing")
        self.assertEqual(inventory(self.root),before)

    def test_unsigned_preview_reports_missing_close_before_unrelated_binary(self):
        self.run_path.unlink()
        asset=self.root/"unrelated.bin";asset.write_bytes(b"x"*(2*1024*1024+1))
        before=inventory(self.root);report=writer().dry_run(self.root,self.batch_path)
        self.assertFalse(report["ready_to_apply"])
        self.assertEqual(report["limit"],"migration-apply-close-missing")
        self.assertEqual(report["written"],[]);self.assertEqual(inventory(self.root),before)

    def test_unrelated_large_ignored_and_tracked_binary_is_not_a_transaction_input(self):
        for tracked in (False,True):
            with self.subTest(tracked=tracked):
                self.setUp();asset=self.root/"unrelated.bin"
                if not tracked:
                    (self.root/".gitignore").write_text("unrelated.bin\n")
                    sup.commit_all(self.root,"synthetic ignored unrelated asset declaration")
                asset.write_bytes(b"x"*(2*1024*1024+1))
                if tracked:sup.commit_all(self.root,"synthetic tracked unrelated binary")
                before=inventory(self.root);reads=[];mod=writer();original=mod.SourceIO.read_bytes
                def reading(source_io,path,**kwargs):
                    if Path(path)==asset:reads.append(path)
                    return original(source_io,path,**kwargs)
                with mock.patch.object(mod.SourceIO,"read_bytes",new=reading):
                    report=mod.dry_run(self.root,self.batch_path)
                self.assertTrue(report["ready_to_apply"],report)
                self.assertEqual(reads,[]);self.assertEqual(report["written"],[])
                self.assertEqual(inventory(self.root),before)

    def test_oversized_selected_and_ignored_canonical_sources_still_refuse(self):
        mod=writer()
        for selected in (True,False):
            with self.subTest(selected=selected):
                self.setUp()
                path=self.root/self.batch_path if selected else self.root/"docs/adrs/ADR-0999-ignored.md"
                if not selected:
                    (self.root/".gitignore").write_text(path.relative_to(self.root).as_posix()+"\n")
                    sup.commit_all(self.root,"synthetic ignored canonical source")
                path.write_bytes(b"x"*(2*1024*1024+1));before=inventory(self.root)
                report=mod.dry_run(self.root,self.batch_path)
                self.assertFalse(report["ready_to_apply"])
                self.assertEqual(report["written"],[])
                self.assertEqual(inventory(self.root),before)
                self.assertEqual(report["limit"],"migration-source-oversize")

    def test_canonical_collector_footprint_detects_ignored_membership_and_source_changes(self):
        mod=writer()
        # Ignoring a source for Git never removes it from a canonical collector.
        (self.root/".gitignore").write_text("docs/adrs/ADR-0999-*\ndocs/observations/\n"
            "docs/promptbooks/runs/**/run-RUN-999.yaml\ncrux/skills/added/\n")
        sup.commit_all(self.root,"synthetic ignored canonical members")
        for rel in ("docs/adrs/ADR-0999-added.md","docs/observations/OBS-0999-added.md",
                "docs/observations/_surveys/added/receipt.yml",
                self.run_path.with_name("run-RUN-999.yaml").relative_to(self.root).as_posix(),
                "crux/skills/added/SKILL.md"):
            with self.subTest(added=rel):
                with mod._source_context(self.root) as (source_io,_):
                    prepared=mod._prepare_application(self.root,self.batch_path,_source_io=source_io)
                    path=self.root/rel;existing=[];parent=path.parent
                    while not parent.exists():existing.append(parent);parent=parent.parent
                    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b"ordinary added source\n")
                    with self.assertRaises(migration.Refused):prepared.snapshot.check(self.root)
                    path.unlink()
                    for directory in existing:directory.rmdir()

    def test_canonical_collector_footprint_keeps_removed_edited_and_aliased_sources(self):
        mod=writer()
        paths=(self.root/"docs/adrs/ADR-0109-predecessor.md",self.run_path,
            self.root/"crux/skills/example/SKILL.md")
        for path in paths:
            for fault in ("removed","edited","alias"):
                with self.subTest(path=path.relative_to(self.root).as_posix(),fault=fault):
                    raw=path.read_bytes()
                    with mod._source_context(self.root) as (source_io,_):
                        prepared=mod._prepare_application(self.root,self.batch_path,_source_io=source_io)
                        if fault=="removed":path.unlink()
                        elif fault=="edited":path.write_bytes(raw+b"ordinary changed source\n")
                        else:
                            replacement=self.root/"ordinary-alias-target.txt"
                            replacement.write_bytes(raw);path.unlink();path.symlink_to(replacement)
                        with self.assertRaises(migration.Refused):prepared.snapshot.check(self.root)
                        if path.is_symlink():path.unlink();replacement.unlink()
                        path.write_bytes(raw)

    def test_canonical_observation_and_receipt_rosters_keep_ignored_removed_members(self):
        mod=writer();obs=self.root/"docs/observations"
        record=obs/"OBS-0999-ordinary.md";receipt=obs/"_surveys/ordinary/receipt.yml"
        receipt.parent.mkdir(parents=True);record.write_bytes(b"ordinary collected record\n")
        receipt.write_bytes(b"ordinary collected receipt\n")
        (self.root/".gitignore").write_text("docs/observations/\n")
        sup.commit_all(self.root,"synthetic ignored observation roster")
        for path in (record,receipt):
            with self.subTest(path=path.relative_to(self.root).as_posix()):
                raw=path.read_bytes()
                with mod._source_context(self.root) as (source_io,_):
                    # Exercise the exact canonical collector ports without
                    # presenting these unratified bytes as governing records.
                    sp.observation_paths(obs,_source_io=source_io)
                    sp.survey_receipt_paths(obs,_source_io=source_io)
                    snapshot=mod._Snapshot.capture(self.root,_source_io=source_io)
                    path.unlink()
                    with self.assertRaises(migration.Refused):snapshot.check(self.root)
                    path.write_bytes(raw)

    def test_dry_run_committed_refresh_and_noop_use_actual_bytes_without_writes(self):
        mod=writer();commit=self.committed_application();before=inventory(self.root)
        report=mod.dry_run(self.root,self.batch_path)
        self.assertTrue(report["ready_to_apply"]);self.assertEqual(report["planned_state"],"refreshed")
        self.assertEqual(report["publication"]["publication_commit"],commit)
        self.assertEqual(len(report["planned_writes"]),7)
        self.assertEqual(report["output_bytes"]["state"],"verified")
        self.assertEqual(inventory(self.root),before)
        mod.apply(self.root,self.batch_path);before=inventory(self.root)
        report=mod.dry_run(self.root,self.batch_path)
        self.assertTrue(report["ready_to_apply"]);self.assertEqual(report["planned_state"],"no_op")
        self.assertEqual(report["planned_writes"],[]);self.assertEqual(inventory(self.root),before)

    def test_dry_run_main_and_layout_teardown_never_reports_ready_or_action(self):
        mod=writer();before=inventory(self.root)
        for kind in ("main","layout"):
            with self.subTest(kind=kind),self.context_close_fault(kind):report=mod.dry_run(self.root,self.batch_path)
            self.assertFalse(report["ready_to_apply"])
            self.assertEqual(report["application"]["state"],"UNOBSERVED")
            self.assertEqual(report["limit"],"migration-apply-context-close-unavailable")
            self.assertEqual(report["failure_class"],"capability")
            self.assertEqual(report["written"],[]);self.assertEqual(inventory(self.root),before)



class ApplyTeardownTests(_ActualCloseFixture):
    def test_outer_main_and_layout_teardown_preserve_new_publication_report(self):
        for kind in ("main","layout"):
            with self.subTest(kind=kind):
                self.setUp();before=inventory(self.root)
                with self.context_close_fault(kind):code,out,err=self.in_process_cli()
                report=self.assert_close_failure(code,out,err,"pending_commit")
                rel=self.witness.relative_to(self.root).as_posix()
                self.assertEqual(report["written"],[rel]);self.assertTrue(self.witness.is_file())
                after=inventory(self.root);self.assertEqual(set(after)-set(before),{rel})
                self.assertTrue(all(after[path]==body for path,body in before.items()))

    def test_outer_main_and_layout_teardown_preserve_existing_pending_report(self):
        writer().apply(self.root,self.batch_path);before=inventory(self.root)
        for kind in ("main","layout"):
            with self.subTest(kind=kind),self.context_close_fault(kind):code,out,err=self.in_process_cli()
            report=self.assert_close_failure(code,out,err,"pending_commit")
            self.assertEqual(report["written"],[]);self.assertEqual(inventory(self.root),before)
            self.assertNotIn("publication_commit",report["publication"])

    def test_outer_main_and_layout_teardown_before_publication_report_no_action(self):
        mod=writer();before=inventory(self.root)
        for kind in ("main","layout"):
            with self.subTest(kind=kind),self.context_close_fault(kind), \
                    mock.patch.object(mod,"_compile_models",side_effect=migration.Refused("synthetic-model-refusal")):
                code,out,err=self.in_process_cli()
            report=self.assert_close_failure(code,out,err,"not_applied")
            self.assertEqual(report["written"],[]);self.assertIsNone(report["publication"])
            self.assertEqual(report["body_limit"],"synthetic-model-refusal")
            self.assertEqual(inventory(self.root),before)

    def test_outer_main_and_layout_teardown_preserve_completed_refresh_report(self):
        for kind in ("main","layout"):
            with self.subTest(kind=kind):
                self.setUp();commit=self.committed_application()
                with self.context_close_fault(kind):code,out,err=self.in_process_cli()
                report=self.assert_close_failure(code,out,err,"refresh_incomplete")
                self.assertEqual(len(report["written"]),7)
                self.assertEqual(report["publication"]["publication_commit"],commit)
                repeat=writer().apply(self.root,self.batch_path)
                self.assertEqual(repeat["state"],"no_op")
                self.assertEqual(repeat["publication"]["publication_commit"],commit)

    def test_outer_main_and_layout_teardown_preserve_noop_refresh_report(self):
        commit=self.committed_application();writer().apply(self.root,self.batch_path)
        before=inventory(self.root)
        for kind in ("main","layout"):
            with self.subTest(kind=kind),self.context_close_fault(kind):code,out,err=self.in_process_cli()
            report=self.assert_close_failure(code,out,err,"refresh_incomplete")
            self.assertEqual(report["written"],[])
            self.assertEqual(report["publication"]["publication_commit"],commit)
            self.assertEqual(inventory(self.root),before)

    def test_outer_teardown_preserves_partial_refresh_written_destinations_and_body_limit(self):
        mod=writer();commit=self.committed_application();original=mod._replace_staged;calls=[]
        def interrupted(item,target,**options):
            if len(calls)==2:raise OSError("synthetic third cache interruption")
            original(item,target,**options);calls.append(target.relative_to(self.root).as_posix())
        with self.context_close_fault("layout"),mock.patch.object(mod,"_replace_staged",new=interrupted):
            code,out,err=self.in_process_cli()
        report=self.assert_close_failure(code,out,err,"refresh_incomplete")
        self.assertEqual(report["written"],calls);self.assertEqual(len(calls),2)
        self.assertEqual(report["publication"]["publication_commit"],commit)
        self.assertEqual(report["body_limit"],"migration-apply-refresh-unavailable")



class ApplyRefreshTests(_ActualCloseFixture):
    def test_partial_cache_failure_reports_incomplete_then_recovers_without_second_witness(self):
        mod=writer();first_commit=self.committed_application();original=mod._replace_staged;calls=[]
        def interrupted(source,target,**options):
            calls.append(target)
            if len(calls)==3:raise OSError("synthetic cache write interruption")
            return original(source,target,**options)
        with mock.patch.object(mod,"_replace_staged",side_effect=interrupted):
            code, stdout, stderr = self.in_process_cli()
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stderr)["limit"], "migration-apply-refresh-unavailable")
        result = json.loads(stdout)
        self.assertEqual(result["state"],"refresh_incomplete")
        self.assertEqual(len(result["written"]),2)
        witness=self.witness.read_bytes();resumed=mod.apply(self.root,self.batch_path)
        self.assertEqual(resumed["state"],"refreshed")
        self.assertEqual(resumed["publication"]["publication_commit"],first_commit)
        self.assertEqual(self.witness.read_bytes(),witness)
        self.assertEqual(len(list(self.witness.parent.glob("*.application.json"))),1)
        self.assertFalse(any(p.name.startswith(".migration-apply-") for p in self.witness.parent.iterdir()))

    def test_committed_repeat_reproves_and_lost_context_refuses_without_cache_mutation(self):
        mod=writer();self.committed_application();result=mod.apply(self.root,self.batch_path)
        self.assertEqual(result["state"],"refreshed")
        proof,_=mod._select_close(self.root,self.batch_path)
        (self.root/proof.binding["context"]["path"]).unlink();sup.commit_all(self.root,"synthetic lost historical proof")
        before=inventory(self.root)
        with self.assertRaises(migration.Refused):mod.apply(self.root,self.batch_path)
        self.assertEqual(inventory(self.root),before)

    def test_committed_cache_cleanup_failure_retains_phase_and_preserves_prior_attempt_temporaries(self):
        mod = writer()
        first_commit = self.committed_application()
        self.assertEqual(mod.apply(self.root, self.batch_path)["state"], "refreshed")
        sup.commit_all(self.root, "synthetic caches before cleanup fault")
        before = inventory(self.root)
        original = os.unlink
        def fail_owned(path, *args, **kwargs):
            if Path(path).name.startswith(".migration-apply-"):
                raise OSError("synthetic owned cache cleanup failure")
            return original(path, *args, **kwargs)
        with mock.patch.object(mod.os, "unlink", new=fail_owned):
            code, stdout, stderr = self.in_process_cli()
        self.assertEqual(code, 2)
        self.assertTrue(stdout, "A committed refresh failure must retain its phase report.")
        result = json.loads(stdout)
        self.assertEqual(result["state"], "refresh_incomplete")
        self.assertEqual(result["written"], [])
        self.assertEqual(result["publication"]["publication_commit"], first_commit)
        self.assertEqual(json.loads(stderr)["limit"], "migration-apply-staging-cleanup-incomplete")
        after = inventory(self.root)
        self.assertTrue(all(after[path] == raw for path, raw in before.items()))
        orphaned = set(after) - set(before)
        self.assertEqual(len(orphaned), 7)
        self.assertTrue(all(Path(path).name.startswith(".migration-apply-") for path in orphaned))
        resumed = mod.apply(self.root, self.batch_path)
        self.assertEqual(resumed["state"], "no_op")
        self.assertEqual(resumed["publication"]["publication_commit"], first_commit)
        self.assertEqual(inventory(self.root), after)

    def test_partial_cache_stream_failure_reports_cleanup_incomplete_and_recovers(self):
        mod=writer();first_commit=self.committed_application();before=inventory(self.root)
        original=mod.os.fdopen
        class PartialStream:
            def __init__(self,descriptor,mode):self.stream=original(descriptor,mode)
            def __enter__(self):return self
            def __exit__(self,*args):self.stream.close()
            def write(self,raw):
                self.stream.write(raw[:4]);self.stream.flush()
                raise OSError("synthetic partial cache stream failure")
        with mock.patch.object(mod.os,"fdopen",side_effect=PartialStream):
            code,stdout,stderr=self.in_process_cli()
        self.assertEqual(code,2)
        result=json.loads(stdout)
        self.assertEqual(result["state"],"refresh_incomplete")
        self.assertEqual(result["written"],[])
        self.assertEqual(result["publication"]["publication_commit"],first_commit)
        self.assertEqual(result["limit"],mod.StagingCleanupIncomplete.code)
        self.assertEqual(json.loads(stderr)["limit"],mod.StagingCleanupIncomplete.code)
        after=inventory(self.root)
        self.assertTrue(all(after[path]==raw for path,raw in before.items()))
        orphaned=set(after)-set(before)
        self.assertEqual(len(orphaned),1)
        self.assertTrue(all(Path(path).name.startswith(".migration-apply-") for path in orphaned))
        self.assertTrue(all(len(after[path])==4 for path in orphaned))
        resumed=mod.apply(self.root,self.batch_path)
        self.assertEqual(resumed["state"],"refreshed")
        self.assertEqual(resumed["publication"]["publication_commit"],first_commit)
        self.assertTrue(all((self.root/path).read_bytes()==after[path] for path in orphaned))

    def test_validation_after_one_cache_write_reports_findings_and_written_destination(self):
        mod = writer()
        self.committed_application()
        original = mod._replace_staged
        raced = []
        def replace_then_compete(source, target, **options):
            original(source, target, **options)
            if not raced:
                raced.append(Path(target))
                Path(target).unlink()
                Path(target).write_bytes(b"independent actor cache bytes\n")
        with mock.patch.object(mod, "_replace_staged", side_effect=replace_then_compete):
            code, stdout, stderr = self.in_process_cli()
        result = json.loads(stdout)
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "")
        self.assertEqual(result["state"], "refresh_incomplete")
        self.assertEqual(result["failure_class"], "validation")
        self.assertEqual(result["written"], [raced[0].relative_to(self.root).as_posix()])
        self.assertEqual(raced[0].read_bytes(), b"independent actor cache bytes\n")

    def test_committed_noncanonical_locator_replay_preserves_exact_historical_bytes(self):
        canonical, historical = self.historical_locator_bytes()
        self.witness.write_bytes(historical)
        sup.commit_all(self.root, "synthetic historical-format publication")
        first_commit = sup.git(self.root, "rev-parse", "HEAD").strip()
        self.assertEqual(migration.authority_view(self.root)["state"], "published")
        first = self.command()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        result = json.loads(first.stdout)
        self.assertEqual(result["state"], "refreshed")
        self.assertEqual(len(result["written"]), 7)
        self.assertEqual(result["publication"]["sha256"], migration._digest(historical))
        self.assertNotEqual(result["publication"]["sha256"], migration._digest(canonical))
        self.assertEqual(result["publication"]["publication_commit"], first_commit)
        self.assertEqual(self.witness.read_bytes(), historical)
        sup.commit_all(self.root, "synthetic historical replay caches")
        before = inventory(self.root)
        again = self.command()
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        repeated = json.loads(again.stdout)
        self.assertEqual(repeated["state"], "no_op")
        self.assertEqual(repeated["publication"], result["publication"])
        self.assertEqual(inventory(self.root), before)

    def test_successor_uses_historical_parent_proof_without_old_current_dependency_prerequisite(self):
        from test_implementation_cycles import book_two
        mod=writer();self.committed_application();self.assertEqual(mod.apply(self.root,self.batch_path)["state"],"refreshed")
        sup.commit_all(self.root,"synthetic first complete caches")
        parent=migration._discover_publications(self.root)[0]
        original_witness=self.witness.read_bytes();oldproof,_=mod._select_close(self.root,self.batch_path)
        ledger=self.root/"docs/adrs/doctrine/reconciliations.yml"
        ledger.write_text(ledger.read_text()+"# independently reviewed changed dependency\n")
        batch=migration.load_batch(self.root,self.batch_path)
        link=dict(witness={k:parent[k] for k in ("path","sha256")},batch=oldproof.binding["batch"],
            source_identities=sorted(e["source_identity"] for e in batch["entries"]))
        batch["recovery_from"]=link;batch["batch_id"]="implementation-pilot-001-recovery-"+migration._canonical_digest(link)
        for ref in batch["signed_dependencies"]:
            if ref["path"]==ledger.relative_to(self.root).as_posix():ref["sha256"]=migration._digest(ledger.read_bytes())
        path=(self.root/self.batch_path).with_name(batch["batch_id"]+".yaml")
        path.write_text(yaml.safe_dump(batch,sort_keys=False));rel=path.relative_to(self.root).as_posix()
        book=book_two();book.update(id="PB-0998",current_run=None,current_prompt=None)
        declaration=book["implementation_slots"][0]
        declaration.update(scope=["docs/adrs/migrations"],constraint_refs=self.fixture.f.book["implementation_slots"][0]["constraint_refs"],
            migration_batch=dict(role="migration-batch",path=rel))
        book_path=self.root/"docs/promptbooks/active/PB-0998-fixture.yaml"
        run_path=self.root/"docs/promptbooks/runs/PB-0998-fixture/run-RUN-001.yaml"
        book_path.write_text(yaml.safe_dump(book,sort_keys=False));sup.commit_all(self.root,"synthetic separately reviewed successor")
        with self.assertRaises(migration.Refused):migration.authority_view(self.root)
        started=subprocess.run([sys.executable,str(SCRIPTS/"start-run.py"),str(book_path),"--run-id","RUN-001","--output",str(run_path)],
            capture_output=True,text=True,env=sup.scrubbed_env())
        self.assertEqual(started.returncode,0,started.stdout+started.stderr)
        book.update(current_run="RUN-001",current_prompt=1);book_path.write_text(yaml.safe_dump(book,sort_keys=False))
        council=run_path.parent/"council";council.mkdir()
        retained=council/"subjects/batch.yaml";retained.parent.mkdir();retained.write_bytes(path.read_bytes())
        receipt=council/"round.json"
        env=object.__new__(sup.Env);env.root=self.root;env.hash=sup.vp.compute_book_hash(book)
        doc=env.council_doc(module_tag="implementation-1",prompt=2,subjects=[dict(path=rel,sha256=migration._digest(path.read_bytes()),
            retained_copy=retained.relative_to(self.root).as_posix())]);doc["book"]["id"]=book["id"]
        receipt.write_text(json.dumps(doc));sup.commit_all(self.root,"synthetic successor council fixture")
        for n in range(1,5):
            extra=["--migration-batch",rel] if n==4 else []
            closed=subprocess.run([sys.executable,str(SCRIPTS/"advance-run.py"),str(run_path),"--book",str(book_path),
                "--outcome","done","--artifacts",receipt.relative_to(self.root).as_posix(),*extra],
                capture_output=True,text=True,env=sup.scrubbed_env())
            self.assertEqual(closed.returncode,0,closed.stdout+closed.stderr);sup.commit_all(self.root,"synthetic actual successor advance")
        before=inventory(self.root);result=mod.apply(self.root,rel)
        self.assertEqual(result["state"],"pending_commit")
        self.assertEqual(self.witness.read_bytes(),original_witness)
        sup.commit_all(self.root,"synthetic actual successor witness commit")
        refreshed=mod.apply(self.root,rel);self.assertEqual(refreshed["state"],"refreshed")
        view=migration.authority_view(self.root);self.assertEqual(len(view["publications"]),2)
        self.assertEqual(self.witness.read_bytes(),original_witness)
        self.assertEqual(len(list(self.witness.parent.glob("*.application.json"))),2)
        with self.assertRaises(migration.Refused):mod.apply(self.root,self.batch_path)



class SHA256ActualCloseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.dict(os.environ,{"GIT_DEFAULT_HASH":"sha256"}):
            ActualCloseTests.setUpClass.__func__(cls)
    setUp=ActualCloseTests.setUp
    command=ActualCloseTests.command

    def test_actual_64_hex_commit_and_all_seven_public_bytes(self):
        self.assertEqual(len(sup.git(self.root,"rev-parse","HEAD").strip()),64)
        ActualCloseTests.test_actual_cli_pending_repeat_commit_refresh_public_equality_and_idempotence(self)
