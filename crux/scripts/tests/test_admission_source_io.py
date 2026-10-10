"""Source transport anchors reads and enumeration to one explicit repository."""
from pathlib import Path
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from admission_source_io import SourceIO, SourceIORefusal
import admission_source_io as source_module


class AnchoredSourceIOTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.source = self.root / "src"
        self.source.mkdir()
        (self.source / "actual.py").write_text("x = 1\n")
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        self.outside = Path(outside.name).resolve()
        (self.outside / "secret.py").write_text("outside bytes\n")
        self.io = SourceIO(self.root)
        self.addCleanup(self.io.close)

    def test_ordinary_reads_globs_resolution_and_absence(self):
        path = self.source / "actual.py"
        self.assertEqual(self.io.read_text(path), "x = 1\n")
        self.assertEqual(self.io.kind(path), "file")
        self.assertEqual(self.io.kind(self.source), "directory")
        self.assertEqual(self.io.glob(self.source, "*.py"), [path])
        self.assertEqual(self.io.resolve(path), path)
        self.assertIsNone(self.io.kind(self.root / "missing" / "source.py"))
        self.assertEqual(self.io.glob(self.root / "missing", "*.py"), [])
        self.io.revalidate()

    def test_contained_file_and_directory_aliases_preserve_canonical_resolution(self):
        alias = self.root / "alias.py"
        alias.symlink_to("src/actual.py")
        folder = self.root / "linked"
        folder.symlink_to("src", target_is_directory=True)
        self.assertEqual(self.io.read_text(alias), "x = 1\n")
        self.assertEqual(self.io.resolve(alias), self.source / "actual.py")
        self.assertEqual(self.io.glob(folder, "*.py"), [folder / "actual.py"])
        self.assertEqual(self.io.read_text(folder / "actual.py"), "x = 1\n")
        self.io.revalidate()

    def test_outside_alias_refuses_before_byte_read(self):
        alias = self.root / "escape.py"
        alias.symlink_to(self.outside / "secret.py")
        secret_inode = (self.outside / "secret.py").stat().st_ino
        original = os.read
        def trap(fd, size):
            self.assertNotEqual(os.fstat(fd).st_ino, secret_inode, "outside bytes read")
            return original(fd, size)
        with mock.patch("os.read", trap):
            with self.assertRaises(SourceIORefusal):
                self.io.read_text(alias)

    def test_directory_swap_refuses_before_outside_enumeration(self):
        original_open, original_listdir = os.open, os.listdir
        outside_inode = self.outside.stat().st_ino
        swapped = []
        def swap(path, flags, *args, **kwargs):
            if path == "src" and kwargs.get("dir_fd") is not None and not swapped:
                self.source.rename(self.root / "original-src")
                self.source.symlink_to(self.outside, target_is_directory=True)
                swapped.append(True)
            return original_open(path, flags, *args, **kwargs)
        def trap(path):
            if isinstance(path, int):
                self.assertNotEqual(os.fstat(path).st_ino, outside_inode, "outside directory enumerated")
            return original_listdir(path)
        with mock.patch("os.open", swap), mock.patch("os.listdir", trap):
            with self.assertRaises(SourceIORefusal):
                self.io.glob(self.source, "*.py")
        self.assertEqual(swapped, [True])

    def test_leaf_swap_before_open_refuses_before_read(self):
        original = os.open
        swapped = []
        def swap(path, flags, *args, **kwargs):
            if path == "actual.py" and kwargs.get("dir_fd") is not None and not swapped:
                (self.source / path).unlink()
                (self.source / path).symlink_to(self.outside / "secret.py")
                swapped.append(True)
            return original(path, flags, *args, **kwargs)
        with mock.patch("os.open", swap):
            with self.assertRaises(SourceIORefusal):
                self.io.read_text(self.source / "actual.py")
        self.assertEqual(swapped, [True])

    def test_snapshot_detects_content_membership_absence_and_alias_changes(self):
        for change in ("content", "member", "absent", "alias"):
            with self.subTest(change=change):
                self.setUp()
                alias = self.root / "alias.py"
                alias.symlink_to("src/actual.py")
                self.io.read_text(alias)
                self.io.glob(self.source, "*.py")
                self.io.kind(self.root / "missing.py")
                if change == "content": (self.source / "actual.py").write_text("x = 2\n")
                if change == "member": (self.source / "new.py").write_text("x = 1\n")
                if change == "absent": (self.root / "missing.py").write_text("x = 1\n")
                if change == "alias":
                    alias.unlink(); alias.symlink_to("src/missing.py")
                with self.assertRaises(SourceIORefusal): self.io.revalidate()

    def test_loop_raw_traversal_invalid_utf8_and_nonregular_refuse_boundedly(self):
        loop = self.root / "loop"
        loop.symlink_to(loop)
        invalid = self.root / "invalid.py"
        invalid.write_bytes(b"\xff")
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        for path in (loop, self.root / ".." / "outside.py", invalid, fifo):
            with self.subTest(path=path), self.assertRaises(SourceIORefusal):
                self.io.read_text(path)

    def test_ordinary_odd_names_remain_literal_components(self):
        for name in ("source\nname.py", "source\\name.py"):
            path = self.source / name
            path.write_text("x = 1\n")
            self.assertEqual(self.io.read_text(path), "x = 1\n")

    def test_oversize_source_refuses(self):
        from implementation_migration import MAX_SOURCE_BYTES
        path = self.root / "large.py"
        path.write_bytes(b"x" * (MAX_SOURCE_BYTES + 1))
        with self.assertRaises(SourceIORefusal): self.io.read_text(path)

    def test_dangling_alias_is_distinct_from_an_absent_input(self):
        missing = self.root / "missing.py"
        alias = self.root / "alias.py"
        alias.symlink_to(missing)
        self.assertIsNone(self.io.kind(missing))
        with self.assertRaises(SourceIORefusal): self.io.kind(alias)

    def test_missing_suffix_under_resolved_directory_alias_is_absent_and_revalidated(self):
        linked = self.root / "linked"
        linked.symlink_to("src", target_is_directory=True)
        chained = self.root / "chained"
        chained.symlink_to("linked", target_is_directory=True)
        for parent in (linked, chained):
            self.assertIsNone(self.io.kind(parent / "optional.yml"))
            self.assertIsNone(self.io.kind(parent / "optional/receipt.yml"))
        dangling = self.root / "dangling"
        dangling.symlink_to("linked/missing/receipt.yml")
        with self.assertRaises(SourceIORefusal) as refused:
            self.io.kind(dangling)
        self.assertEqual(refused.exception.code, "admission-source-io-nonresolution-refused")
        self.io.revalidate()
        (self.source / "optional.yml").write_text("x: 1\n")
        with self.assertRaises(SourceIORefusal): self.io.revalidate()

    def test_canonical_text_uses_universal_newlines_and_raw_bytes_are_preserved(self):
        import summaries_projection as sp
        for newline in (b"\n", b"\r\n", b"\r"):
            with self.subTest(newline=newline):
                path = self.root / ("record-" + newline.hex() + ".md")
                raw = newline.join((b"---", b"id: ADR-0001", b"status: Accepted",
                                    b"---", b"Body.", b""))
                path.write_bytes(raw)
                self.assertEqual(self.io.read_text(path), path.read_text(encoding="utf-8"))
                self.assertEqual(sp.read_frontmatter(self.io.read_text(path)),
                                 {"id": "ADR-0001", "status": "Accepted"})
                self.assertEqual(self.io.read_bytes(path), raw)
        self.io.revalidate()

    def test_canonical_snapshot_detects_raw_newline_drift(self):
        path = self.source / "actual.py"
        path.write_bytes(b"x = 1\r\n")
        self.assertEqual(self.io.read_text(path), "x = 1\n")
        path.write_bytes(b"x = 1\n")
        with self.assertRaises(SourceIORefusal): self.io.revalidate()

    def test_canonical_crlf_records_match_existing_collector(self):
        import summaries_projection as sp
        import yaml
        adrs = self.root / "docs/adrs"
        adrs.mkdir(parents=True)
        document = dict(id="ADR-0001", status="Accepted", governs=[dict(
            handle="ADR-0001/crlf-constraint", domain="testing", rule="Preserve a constraint.",
            scope="source", provenance="authored")])
        text = "---\n" + yaml.safe_dump(document) + "---\n"
        (adrs / "ADR-0001-constraint.md").write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
        expected = sp.collect_records(adrs)
        self.assertEqual([row["handle"] for row in expected], ["ADR-0001/crlf-constraint"])
        self.assertEqual(sp.collect_records(adrs, _source_io=self.io), expected)
        self.io.revalidate()

    def test_raw_reader_preserves_bytes_that_canonical_decoder_refuses(self):
        path = self.root / "invalid.py"
        path.write_bytes(b"\xff\r\n")
        self.assertEqual(self.io.read_bytes(path), b"\xff\r\n")
        with self.assertRaises(SourceIORefusal): self.io.read_text(path)

    def test_changed_alias_refuses_between_methods_before_target_read_or_enumeration(self):
        for directory in (False, True):
            with self.subTest(directory=directory):
                alias = self.root / ("directory-alias" if directory else "file-alias")
                first = self.source if directory else self.source / "actual.py"
                changed = self.root / ("other-directory" if directory else "other.py")
                if directory:
                    changed.mkdir()
                    (changed / "other.py").write_text("x = 2\n")
                else:
                    changed.write_text("x = 2\n")
                alias.symlink_to(first, target_is_directory=directory)
                self.assertEqual(self.io.resolve(alias), first)
                alias.unlink()
                alias.symlink_to(changed, target_is_directory=directory)
                inode = changed.stat().st_ino
                read, listdir = os.read, os.listdir
                def read_trap(fd, size):
                    self.assertNotEqual(os.fstat(fd).st_ino, inode, "changed target read")
                    return read(fd, size)
                def list_trap(fd):
                    if isinstance(fd, int):
                        self.assertNotEqual(os.fstat(fd).st_ino, inode, "changed directory enumerated")
                    return listdir(fd)
                with mock.patch("os.read", read_trap), mock.patch("os.listdir", list_trap):
                    with self.assertRaises(SourceIORefusal):
                        if directory: self.io.glob(alias, "*.py")
                        else: self.io.read_text(alias)

    def test_metadata_preserves_final_leaf_kind_without_following_alias(self):
        file = self.source / "actual.py"
        alias = self.root / "alias"
        alias.symlink_to("missing/target")
        for path in (self.root, self.source, file, alias):
            with self.subTest(path=path):
                info = path.lstat()
                metadata = self.io.metadata(path)
                expected = {key: getattr(info, key) for key in (
                    "st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")}
                expected["target"] = os.readlink(path) if stat.S_ISLNK(info.st_mode) else None
                self.assertEqual(metadata, expected)
        self.assertIsNone(self.io.metadata(self.root / "missing.py"))
        self.io.revalidate()

    def test_metadata_supports_contained_parent_alias_and_detects_leaf_changes(self):
        folder = self.root / "linked"
        folder.symlink_to("src", target_is_directory=True)
        path = folder / "actual.py"
        self.assertEqual(self.io.metadata(path)["st_ino"], path.lstat().st_ino)
        alias = self.root / "alias"
        alias.symlink_to("./src//actual.py")
        self.assertEqual(self.io.metadata(alias)["target"], "./src//actual.py")
        self.assertEqual(self.io.read_text(alias), "x = 1\n")
        alias.unlink()
        alias.symlink_to("src/missing.py")
        with self.assertRaises(SourceIORefusal): self.io.revalidate()

    def test_trusted_read_limits_preserve_default_and_revalidate_selected_bound(self):
        path = self.root / "canonical.json"
        raw = b"x" * (2 * 1024 * 1024 + 1)
        path.write_bytes(raw)
        with self.assertRaises(SourceIORefusal): self.io.read_bytes(path)
        wide = SourceIO(self.root, max_bytes=4 * 1024 * 1024)
        self.addCleanup(wide.close)
        self.assertEqual(wide.read_bytes(path), raw)
        self.assertEqual(self.io.read_text(path, max_bytes=4 * 1024 * 1024), raw.decode())
        with self.assertRaises(SourceIORefusal): wide.read_bytes(path, max_bytes=2 * 1024 * 1024)
        wide.revalidate()
        self.io.revalidate()

    def test_read_limits_reject_bool_noninteger_nonpositive_and_above_ceiling(self):
        path = self.source / "actual.py"
        for limit in (True, False, 1.5, "4", 0, -1, 4 * 1024 * 1024 + 1):
            with self.subTest(limit=limit):
                with self.assertRaises(SourceIORefusal): SourceIO(self.root, max_bytes=limit)
                with self.assertRaises(SourceIORefusal): self.io.read_bytes(path, max_bytes=limit)
                with self.assertRaises(SourceIORefusal): self.io.read_text(path, max_bytes=limit)

    def traversal_context(self):
        import retained_evidence
        docs = self.root / "docs"
        docs.mkdir(exist_ok=True)
        def exclude(path):
            if retained_evidence.holding_root_for_path(docs, path) is not None:
                raise source_module.SourceIOExcluded()
        source_io = SourceIO(self.root, _traversal_check=exclude)
        self.addCleanup(source_io.close)
        return source_io, docs

    def test_traversal_callback_preserves_ordinary_paths_and_contained_aliases(self):
        guarded, _ = self.traversal_context()
        alias = self.root / "alias.py"
        alias.symlink_to("src/actual.py")
        self.assertEqual(guarded.read_text(alias), self.io.read_text(alias))
        self.assertEqual(guarded.glob(self.source, "*.py"), self.io.glob(self.source, "*.py"))
        self.assertEqual(guarded.metadata(alias), self.io.metadata(alias))
        guarded.revalidate()

    def test_complete_held_spelling_excludes_every_operation_and_replays(self):
        guarded, docs = self.traversal_context()
        path = docs / "promptbooks/runs/TEAM-PB-0001-legacy--slug-/evidence/implementation-cycles/retained-repositories/source.py"
        for operation in (guarded.kind, guarded.resolve, guarded.metadata, guarded.read_bytes, guarded.read_text):
            with self.subTest(operation=operation.__name__), self.assertRaises(source_module.SourceIOExcluded):
                operation(path)
        with self.assertRaises(source_module.SourceIOExcluded): guarded.glob(path.parent, "*.py")
        guarded.revalidate()
        self.assertIsNone(self.io.kind(path))

    def test_alias_expanded_holding_and_pending_parent_components_exclude(self):
        guarded, docs = self.traversal_context()
        for name, suffix in (("held", "source.py"), ("parents", "jump/../../source.py")):
            alias = self.root / name
            alias.symlink_to(docs / "promptbooks/runs/PB-0001-demo/evidence/implementation-cycles/retained-repositories" / suffix)
            with self.subTest(name=name), self.assertRaises(source_module.SourceIOExcluded): guarded.resolve(alias)
        nested = self.source / "nested"
        nested.mkdir()
        ordinary = self.root / "ordinary.py"
        ordinary.symlink_to("src/nested/../actual.py")
        self.assertEqual(guarded.read_text(ordinary), "x = 1\n")
        guarded.revalidate()

    def test_metadata_parent_alias_checks_complete_pending_leaf(self):
        guarded, docs = self.traversal_context()
        alias = self.root / "layout"
        alias.symlink_to(docs / "promptbooks/runs/PB-0001-demo/evidence", target_is_directory=True)
        path = alias / "implementation-cycles/retained-repositories"
        with self.assertRaises(source_module.SourceIOExcluded): guarded.metadata(path)
        guarded.revalidate()

    def test_excluded_alias_snapshot_detects_changed_alias_even_if_still_held(self):
        for target in ("source.py", "other-held"):
            with self.subTest(target=target):
                self.setUp()
                guarded, docs = self.traversal_context()
                holding = docs / "promptbooks/runs/PB-0001-demo/evidence/implementation-cycles/retained-repositories"
                alias = self.root / "alias.py"
                alias.symlink_to(holding / "source.py")
                with self.assertRaises(source_module.SourceIOExcluded): guarded.resolve(alias)
                guarded.revalidate()
                alias.unlink()
                alias.symlink_to(self.source / "actual.py" if target == "source.py" else holding / "other.py")
                with self.assertRaises(SourceIORefusal): guarded.revalidate()

    def test_malformed_holding_remains_distinct_from_excluded_and_default_behavior(self):
        from retained_evidence import RetainedEvidenceRefusal
        guarded, docs = self.traversal_context()
        malformed = docs / "promptbooks/runs/PB-0001-INVALID/evidence/implementation-cycles/retained-repositories/source.py"
        with self.assertRaisesRegex(RetainedEvidenceRefusal, "retained-evidence-layout-refused"):
            guarded.kind(malformed)
        self.assertIsNone(self.io.kind(malformed))

    def test_callback_errors_propagate_unchanged_from_walk_and_metadata_leaf(self):
        alias = self.root / "linked"
        alias.symlink_to("src", target_is_directory=True)
        for method in ("kind", "metadata"):
            for error in (ValueError("canonical-layout-refused"), OSError("canonical-layout-refused")):
                with self.subTest(method=method, error=type(error).__name__):
                    def check(path):
                        if path == self.source / "actual.py": raise error
                    guarded = SourceIO(self.root, _traversal_check=check)
                    self.addCleanup(guarded.close)
                    with self.assertRaises(type(error)) as found:
                        getattr(guarded, method)(alias / "actual.py")
                    self.assertIs(found.exception, error)

    def test_private_footprint_is_frozen_and_replays_only_observed_inputs(self):
        path = self.source / "actual.py"
        self.io.read_text(path)
        self.io.metadata(path)
        self.io.resolve(path)
        footprint = self.io._capture_footprint()
        (self.root / "unread.txt").write_text("unrelated\n")
        self.io._replay_footprint(footprint)
        with self.assertRaises((AttributeError, TypeError)):
            footprint.root = self.outside
        path.write_text("changed\n")
        with self.assertRaises(SourceIORefusal): self.io._replay_footprint(footprint)

    def test_private_footprint_detects_membership_absence_alias_and_components(self):
        for change in ("addition", "removal", "absence", "alias", "component"):
            with self.subTest(change=change):
                self.setUp()
                path = self.source / "actual.py"
                alias = self.root / "linked.py"
                alias.symlink_to("src/actual.py")
                self.io.glob(self.source, "*.py")
                self.io.kind(self.root / "missing.py")
                self.io.read_bytes(alias)
                footprint = self.io._capture_footprint()
                self.io._replay_footprint(footprint)
                if change == "addition": (self.source / "new.py").write_text("new\n")
                if change == "removal": path.unlink()
                if change == "absence": (self.root / "missing.py").write_text("new\n")
                if change == "alias": alias.unlink(); alias.symlink_to("src/new.py")
                if change == "component":
                    self.source.rename(self.root / "previous")
                    self.source.mkdir(); (self.source / "actual.py").write_text("x = 1\n")
                with self.assertRaises(SourceIORefusal): self.io._replay_footprint(footprint)

    def test_private_footprint_replays_exclusion_and_callback_binding(self):
        guarded, docs = self.traversal_context()
        holding = docs / "promptbooks/runs/PB-0001-test/evidence/implementation-cycles/retained-repositories"
        alias = self.root / "held"
        alias.symlink_to(holding)
        with self.assertRaises(source_module.SourceIOExcluded): guarded.kind(alias)
        footprint = guarded._capture_footprint()
        guarded._replay_footprint(footprint)
        with self.assertRaises(SourceIORefusal): self.io._replay_footprint(footprint)
        alias.unlink(); alias.symlink_to(holding / "changed")
        with self.assertRaises(SourceIORefusal): guarded._replay_footprint(footprint)

    def test_exact_owned_file_transitions_do_not_reset_original_snapshot(self):
        path = self.source / "actual.py"
        self.io.read_bytes(path); self.io.metadata(path); self.io.kind(path)
        self.io.resolve(path); self.io.glob(self.source, "*")
        before = self.io._capture_path_state(path)
        footprint = self.io._capture_footprint()
        stage = self.source / "temporary"
        stage.write_bytes(b"new output\r\n")
        stage.replace(path)
        after = self.io._capture_path_state(path)
        self.assertEqual(after.raw_bytes, b"new output\r\n")
        self.io._replay_footprint(footprint, transitions=((before, after),))
        with self.assertRaises(SourceIORefusal): self.io.revalidate()
        path.write_bytes(b"third party\n")
        with self.assertRaises(SourceIORefusal):
            self.io._replay_footprint(footprint, transitions=((before, after),))

    def test_exact_stage_add_remove_and_created_directory_transitions(self):
        folder = self.root / "outputs"
        output = folder / "result.txt"
        stage = self.source / "stage"
        self.io.kind(folder); self.io.kind(output)
        self.io.glob(folder, "*"); self.io.glob(self.root, "*")
        self.io.glob(self.source, "*")
        old_dir = self.io._capture_path_state(folder)
        old_stage = self.io._capture_path_state(stage)
        old_output = self.io._capture_path_state(output)
        footprint = self.io._capture_footprint()
        folder.mkdir()
        new_dir = self.io._capture_path_state(folder)
        stage.write_bytes(b"compiled\n")
        new_stage = self.io._capture_path_state(stage)
        pairs = ((old_dir, new_dir), (old_stage, new_stage))
        self.io._replay_footprint(footprint, transitions=pairs)
        stage.replace(output)
        absent_stage = self.io._capture_path_state(stage)
        new_output = self.io._capture_path_state(output)
        pairs += ((new_stage, absent_stage), (old_output, new_output))
        self.io._replay_footprint(footprint, transitions=pairs)
        (folder / "unowned.txt").write_text("unexpected\n")
        with self.assertRaises(SourceIORefusal):
            self.io._replay_footprint(footprint, transitions=pairs)

    def test_transition_pair_cannot_bless_other_changes_between_captures(self):
        path = self.source / "actual.py"
        other = self.source / "other.py"
        other.write_bytes(b"original\n")
        self.io.read_bytes(path); self.io.read_bytes(other); self.io.glob(self.source, "*")
        old = self.io._capture_path_state(path)
        footprint = self.io._capture_footprint()
        path.write_bytes(b"owned\n"); other.write_bytes(b"unowned\n")
        new = self.io._capture_path_state(path)
        with self.assertRaises(SourceIORefusal):
            self.io._replay_footprint(footprint, transitions=((old, new),))

    def test_directory_transition_cannot_waive_unlisted_children_or_aliases(self):
        folder = self.root / "output"
        self.io.glob(self.root, "*"); self.io.kind(folder)
        old = self.io._capture_path_state(folder)
        footprint = self.io._capture_footprint()
        folder.mkdir(); (folder / "unowned").write_bytes(b"new\n")
        new = self.io._capture_path_state(folder)
        with self.assertRaises(SourceIORefusal):
            self.io._replay_footprint(footprint, transitions=((old, new),))
        alias = self.root / "alias"
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(SourceIORefusal): self.io._capture_path_state(alias)

    def test_private_footprint_preserves_root_and_read_bound_binding(self):
        self.io.kind(self.source)
        footprint = self.io._capture_footprint()
        wide = SourceIO(self.root, max_bytes=4 * 1024 * 1024)
        self.addCleanup(wide.close)
        with self.assertRaises(SourceIORefusal): wide._replay_footprint(footprint)
        another = SourceIO(self.outside)
        self.addCleanup(another.close)
        with self.assertRaises(SourceIORefusal): another._replay_footprint(footprint)

    def test_path_state_parent_membership_remains_exact_without_an_original_glob(self):
        path = self.source / "stage"
        old = self.io._capture_path_state(path)
        footprint = self.io._capture_footprint()
        path.write_bytes(b"owned\n")
        new = self.io._capture_path_state(path)
        self.io._replay_footprint(footprint, transitions=((old, new),))
        (self.source / "unowned").write_bytes(b"new\n")
        with self.assertRaises(SourceIORefusal):
            self.io._replay_footprint(footprint, transitions=((old, new),))

    def test_private_path_state_limit_is_per_read_and_preserves_default_context(self):
        path = self.source / "output"
        path.write_bytes(b"a" * (2 * 1024 * 1024 + 1))
        self.io.metadata(path)
        with self.assertRaises(SourceIORefusal): self.io._capture_path_state(path)
        before = self.io._capture_path_state(path, max_bytes=4 * 1024 * 1024)
        footprint = self.io._capture_footprint()
        path.write_bytes(b"b" * (2 * 1024 * 1024 + 1))
        after = self.io._capture_path_state(path, max_bytes=4 * 1024 * 1024)
        self.io._replay_footprint(footprint, transitions=((before, after),))
        with self.assertRaises(SourceIORefusal): self.io.read_bytes(path)
        for limit in (True, 0, 4 * 1024 * 1024 + 1):
            with self.subTest(limit=limit), self.assertRaises(SourceIORefusal):
                self.io._capture_path_state(path, max_bytes=limit)

    def joint_context(self):
        layout = SourceIO(self.root)
        self.addCleanup(layout.close)
        held = self.root / "held"
        def check(path):
            if path == held:
                layout.metadata(self.source)
                raise source_module.SourceIOExcluded()
        main = SourceIO(self.root, _traversal_check=check)
        self.addCleanup(main.close)
        with self.assertRaises(source_module.SourceIOExcluded): main.kind(held)
        main.read_bytes(self.source / "actual.py")
        main.glob(self.source, "*")
        stage = self.source / "stage"
        main.kind(stage)
        before = main._capture_path_state(stage)
        footprint, layout_footprint = main._capture_footprint(), layout._capture_footprint()
        stage.write_bytes(b"owned\n")
        after = main._capture_path_state(stage)
        return main, layout, footprint, layout_footprint, before, after

    def test_joint_receipt_preserves_callback_and_metadata_only_layout(self):
        main, layout, footprint, layout_footprint, before, after = self.joint_context()
        receipt = main._validate_transitions(footprint, transitions=((before, after),))
        originals = {name: getattr(SourceIO, name) for name in ("read_bytes", "glob", "_capture_path_state")}
        def guarded(name):
            def call(context, *args, **kwargs):
                self.assertIsNotNone(context._traversal_check, "layout acquired main input reads")
                return originals[name](context, *args, **kwargs)
            return call
        with mock.patch.object(SourceIO, "read_bytes", guarded("read_bytes")), \
             mock.patch.object(SourceIO, "glob", guarded("glob")), \
             mock.patch.object(SourceIO, "_capture_path_state", guarded("_capture_path_state")):
            layout._replay_metadata_footprint(layout_footprint, receipt)
            main._replay_footprint(footprint, transitions=receipt)
            layout._replay_metadata_footprint(layout_footprint, receipt)
        layout.metadata(self.source)
        layout.revalidate()
        self.assertNotEqual(layout._capture_footprint(), layout_footprint)
        with self.assertRaises(SourceIORefusal): main.revalidate()
        after.path.write_bytes(b"unowned replacement\n")
        with self.assertRaises(SourceIORefusal): main._replay_footprint(footprint, transitions=receipt)

    def test_metadata_receipt_refusal_updates_no_original_observations(self):
        main, layout, footprint, layout_footprint, before, after = self.joint_context()
        other = self.root / "other"
        other.mkdir()
        layout.metadata(other)
        layout_footprint = layout._capture_footprint()
        receipt = main._validate_transitions(footprint, transitions=((before, after),))
        (other / "unowned").write_bytes(b"unexpected\n")
        with self.assertRaises(SourceIORefusal): layout._replay_metadata_footprint(layout_footprint, receipt)
        self.assertEqual(layout._capture_footprint(), layout_footprint)

    def test_metadata_receipt_rejects_forgery_wrong_root_and_new_layout_paths(self):
        from dataclasses import replace
        main, layout, footprint, layout_footprint, before, after = self.joint_context()
        receipt = main._validate_transitions(footprint, transitions=((before, after),))
        with self.assertRaises(SourceIORefusal): layout._replay_metadata_footprint(layout_footprint, replace(receipt))
        with self.assertRaises(SourceIORefusal):
            main._validate_transitions(footprint, transitions=((after, before),))
        another = SourceIO(self.outside)
        self.addCleanup(another.close)
        with self.assertRaises(SourceIORefusal): another._replay_metadata_footprint(another._capture_footprint(), receipt)
        missing = self.root / "new-directory"
        second = SourceIO(self.root)
        self.addCleanup(second.close)
        second.metadata(missing)
        absent_footprint = second._capture_footprint()
        old = main._capture_path_state(missing)
        missing.mkdir()
        new = main._capture_path_state(missing)
        created = main._validate_transitions(footprint, transitions=((old, new),))
        with self.assertRaises(SourceIORefusal): second._replay_metadata_footprint(absent_footprint, created)
        # A layout token cannot import a main read/glob roster.
        with self.assertRaises(SourceIORefusal): main._replay_metadata_footprint(footprint, receipt)

    def test_receipt_validation_retains_all_after_bytes_until_revalidation(self):
        first, second = self.source / "stage-one", self.source / "stage-two"
        old_first = self.io._capture_path_state(first)
        old_second = self.io._capture_path_state(second)
        footprint = self.io._capture_footprint()
        first.write_bytes(b"first\n"); second.write_bytes(b"second\n")
        new_first = self.io._capture_path_state(first)
        new_second = self.io._capture_path_state(second)
        original, changed = SourceIO.read_bytes, []
        def change_previous(context, path, **kwargs):
            if path == second and not changed:
                first.write_bytes(b"changed after capture\n")
                changed.append(True)
            return original(context, path, **kwargs)
        with mock.patch.object(SourceIO, "read_bytes", change_previous):
            with self.assertRaises(SourceIORefusal):
                self.io._validate_transitions(footprint,
                    transitions=((old_first, new_first), (old_second, new_second)))
        self.assertEqual(changed, [True])

    def test_transition_parent_capture_cannot_replace_original_glob_membership(self):
        for change in ("addition", "removal", "unchanged"):
            with self.subTest(change=change):
                self.setUp()
                peer = self.source / "peer.py"
                peer.write_bytes(b"peer\n")
                self.io.glob(self.source, "*.py")
                footprint = self.io._capture_footprint()
                if change == "addition": (self.source / "unowned.py").write_bytes(b"unexpected\n")
                if change == "removal": peer.unlink()
                stage = self.source / "stage"
                before = self.io._capture_path_state(stage)
                stage.write_bytes(b"owned\n")
                after = self.io._capture_path_state(stage)
                if change == "unchanged":
                    receipt = self.io._validate_transitions(footprint, transitions=((before, after),))
                    self.io._replay_footprint(footprint, transitions=receipt)
                else:
                    with self.assertRaises(SourceIORefusal):
                        self.io._replay_footprint(footprint, transitions=((before, after),))
                    with self.assertRaises(SourceIORefusal):
                        self.io._validate_transitions(footprint, transitions=((before, after),))

    def test_child_parent_snapshot_preserves_exact_created_directory_identity(self):
        for replace_directory in (False, True):
            with self.subTest(replace_directory=replace_directory):
                self.setUp()
                folder, stage = self.root / "outputs", self.root / "stage"
                self.io.read_bytes(self.source / "actual.py")
                footprint = self.io._capture_footprint()
                old_folder = self.io._capture_path_state(folder)
                old_stage = self.io._capture_path_state(stage)
                folder.mkdir()
                new_folder = self.io._capture_path_state(folder)
                if replace_directory:
                    displaced = self.root / "displaced"
                    folder.rename(displaced)
                    folder.mkdir()
                    displaced.rmdir()
                    self.assertNotEqual(dict(new_folder.metadata)["st_ino"], folder.stat().st_ino)
                output = folder / "result.txt"
                old_output = self.io._capture_path_state(output)
                output.write_bytes(b"compiled\n")
                new_output = self.io._capture_path_state(output)
                stage.write_bytes(b"owned stage\n")
                new_stage = self.io._capture_path_state(stage)
                pairs = ((old_folder, new_folder), (old_output, new_output), (old_stage, new_stage))
                if replace_directory:
                    with self.assertRaises(SourceIORefusal):
                        self.io._validate_transitions(footprint, transitions=pairs)
                else:
                    receipt = self.io._validate_transitions(footprint, transitions=pairs)
                    self.io._replay_footprint(footprint, transitions=receipt)
