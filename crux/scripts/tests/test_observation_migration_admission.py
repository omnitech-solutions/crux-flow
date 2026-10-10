"""Production admission over ordinary and synthetic published source fixtures."""
from __future__ import annotations

import inspect
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml
import observation_admission as admission

try:  # package-relative when run as a module, flat when run by discovery
    from ._dev_surface import IS_STAGED_ARTIFACT, REPO_ROOT
except ImportError:  # pragma: no cover
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _dev_surface import IS_STAGED_ARTIFACT, REPO_ROOT


class OrdinaryAdmission(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.tree = self.root / "docs"
        self.obs = self.tree / "observations"
        self.obs.mkdir(parents=True)
        self.adrs = self.tree / "adrs"
        self.adrs.mkdir()
        (self.root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: docs\n')
        (self.tree / "manifest.yml").write_text(
            "schema_version: 5\nconcerns_enabled: [adrs, observations]\n")
        (self.root / "source.py").write_text("x = 1\n")

    def problems(self, path="source.py", slug=None, **kwargs):
        return admission.admission_problems(self.root, slug=slug,
            evidence=[f"{path}:1-1"], **kwargs)

    def test_selected_tree_redaction_binding_and_containment_diagnostic(self):
        import bionic_config
        import untrusted
        self.assertEqual(untrusted.binding_problems(
            {"observation_admission.py": inspect.getsource(admission)}), [])
        source_io = admission._AdmissionIO(self.root)
        self.addCleanup(source_io.close)
        self.assertEqual(admission._selected_tree(self.root, "docs", source_io), self.tree)
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        target = Path(outside.name).resolve()
        (self.root / "other-tree").symlink_to(target, target_is_directory=True)
        with self.assertRaises(bionic_config.BionicConfigError) as refusal:
            admission._selected_tree(self.root, "other-tree", source_io)
        self.assertEqual(str(refusal.exception),
            f"docs_dir {untrusted.redact('other-tree')} is not contained under the project root")
        self.assertNotIn(str(target), str(refusal.exception))

    def test_outside_alias_is_refused_before_outside_metadata(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        target = Path(outside.name).resolve() / "source.py"
        target.write_text("x = 1\n")
        alias = self.root / "alias.py"
        alias.symlink_to(target)
        original = Path.lstat
        def guarded(path, *args, **kwargs):
            self.assertFalse(path.is_relative_to(target.parent), "outside metadata probed")
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "lstat", guarded):
            self.assertTrue(self.problems("alias.py"))
        alias.unlink()
        alias.symlink_to(self.root / "source.py")
        self.assertEqual(self.problems("alias.py"), [])

    def test_corpus_alias_changed_after_preclassification_excludes_at_transport(self):
        from admission_source_io import SourceIOExcluded
        ordinary = self.tree / "ordinary-observations"
        ordinary.mkdir()
        holding = self.tree / "promptbooks/runs/PB-0001-demo/evidence/implementation-cycles/retained-repositories"
        holding.mkdir(parents=True)
        self.obs.rmdir()
        self.obs.symlink_to(ordinary, target_is_directory=True)
        original, changed = admission._unheld, []
        def swap(tree, path):
            original(tree, path)
            if path == self.obs and not changed:
                self.obs.unlink()
                self.obs.symlink_to(holding, target_is_directory=True)
                changed.append(True)
        with mock.patch.object(admission, "_unheld", swap):
            with self.assertRaises(SourceIOExcluded): admission._load_context(self.root)
        self.assertEqual(changed, [True])
        self.obs.unlink()
        self.obs.symlink_to(ordinary, target_is_directory=True)
        context = admission._load_context(self.root)
        self.addCleanup(context["source_io"].close)
        self.assertEqual(context["observations"], self.obs)
        self.assertEqual(context["source_io"].resolve(context["observations"]), ordinary)
        admission._revalidate(context)

    def test_source_alias_changed_after_preclassification_never_admits_held_fact(self):
        holding = self.tree / "promptbooks/runs/PB-0001-demo/evidence/implementation-cycles/retained-repositories"
        holding.mkdir(parents=True)
        retained = holding / "source.py"
        retained.write_text("x = 1\n")
        alias = self.root / "alias.py"
        alias.symlink_to(self.root / "source.py")
        original, changed = admission.SourceIO.resolve, []
        def swap(source_io, path):
            answer = original(source_io, path)
            if path == alias and not changed:
                alias.unlink()
                alias.symlink_to(retained)
                changed.append(True)
            return answer
        with mock.patch.object(admission.SourceIO, "resolve", swap):
            self.assertTrue(self.problems("alias.py"))
        self.assertEqual(changed, [True])
        alias.unlink()
        alias.symlink_to(self.root / "source.py")
        self.assertEqual(self.problems("alias.py"), [])

    def test_bootstrap_configuration_replays_holding_check_before_context_admission(self):
        from admission_source_io import SourceIOExcluded
        holding = self.tree / "promptbooks/runs/PB-0001-demo/evidence/implementation-cycles/retained-repositories"
        holding.mkdir(parents=True)
        retained = holding / "config.yml"
        retained.write_bytes((self.root / ".bionic.yml").read_bytes())
        config = self.root / ".bionic.yml"
        config.unlink()
        config.symlink_to(retained)
        with self.assertRaises(SourceIOExcluded): admission._load_context(self.root)
        config.unlink()
        config.write_bytes(retained.read_bytes())
        context = admission._load_context(self.root)
        self.addCleanup(context["source_io"].close)
        self.assertEqual(context["tree"], self.tree)
        admission._revalidate(context)

    def test_held_corpus_directory_aliases_refuse_before_enumeration_or_read(self):
        for name in ("adrs", "observations", "adrs/archive", "adrs/summaries", "adrs/doctrine"):
            with self.subTest(directory=name):
                self.setUp()
                holding = self.tree / "promptbooks/runs/PB-0001-demo/evidence/implementation-cycles/retained-repositories"
                target = holding / "nested" / name
                target.mkdir(parents=True)
                record = target / "OBS-0002-held.md"
                record.write_text("---\nid: OBS-0002\n---\nRetained evidence.\n")
                directory = self.tree / name
                directory.parent.mkdir(parents=True, exist_ok=True)
                if directory.exists(): directory.rmdir()
                directory.symlink_to(target, target_is_directory=True)
                held_dir, held_file = target.stat().st_ino, record.stat().st_ino
                read, listdir = os.read, os.listdir
                def read_trap(fd, size):
                    self.assertNotEqual(os.fstat(fd).st_ino, held_file, "held corpus bytes read")
                    return read(fd, size)
                def list_trap(path):
                    if isinstance(path, int):
                        self.assertNotEqual(os.fstat(path).st_ino, held_dir, "held corpus enumerated")
                    return listdir(path)
                with mock.patch("os.read", read_trap), mock.patch("os.listdir", list_trap):
                    with self.assertRaises(ValueError): admission._load_context(self.root)

    def test_held_manifest_and_receipt_aliases_refuse_before_bytes(self):
        import summaries_projection as sp
        for relative in ("manifest.yml", sp.reviews_path(self.adrs).relative_to(self.tree).as_posix(),
                         "adrs/summaries/_meta.json", "adrs/doctrine/" + sp.RECONCILIATIONS_FILENAME):
            with self.subTest(leaf=relative):
                self.setUp()
                holding = self.tree / "promptbooks/runs/PB-0001-demo/evidence/implementation-cycles/retained-repositories"
                holding.mkdir(parents=True)
                held = holding / "input.yml"
                held.write_text("concerns_enabled: [adrs, observations]\n")
                path = self.tree / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists(): path.unlink()
                path.symlink_to(held)
                inode, read = held.stat().st_ino, os.read
                def trap(fd, size):
                    self.assertNotEqual(os.fstat(fd).st_ino, inode, "held input bytes read")
                    return read(fd, size)
                with mock.patch("os.read", trap):
                    with self.assertRaises(ValueError): admission._load_context(self.root)

    def test_distinct_factual_source_passes_while_typed_and_disguised_reasoning_refuses(self):
        self.assertEqual(self.problems(slug="distinct"), [])
        for kind in ("implementation-decision", "implementation-result",
                     "implementation-annotation", "implementation-migration"):
            for filename, frontmatter in (("reasoning.yaml", False), ("ADR-9999-reasoning.md", True),
                                          ("OBS-9999-reasoning.md", False)):
                with self.subTest(kind=kind, filename=filename):
                    text = yaml.safe_dump(dict(record_type=kind, constraint_refs=["rule:valid"]))
                    (self.root / filename).write_text("---\n" + text + "---\nQuoted constraint.\n"
                                                    if frontmatter else text)
                    self.assertTrue(self.problems(filename))

    def test_holding_and_alias_refuse_but_same_basename_outside_passes(self):
        held = self.tree / "promptbooks/runs/OLD-PB-0001--old--/evidence/implementation-cycles/retained-repositories"
        held.mkdir(parents=True)
        (held / "source.py").write_text("x = 1\n")
        (self.root / "alias.py").symlink_to(held / "source.py")
        outside = self.root / "retained-repositories"
        outside.mkdir(); (outside / "source.py").write_text("x = 1\n")
        self.assertTrue(self.problems((held / "source.py").relative_to(self.root).as_posix()))
        self.assertTrue(self.problems("alias.py"))
        self.assertEqual(self.problems("retained-repositories/source.py"), [])

    def test_top_level_reasoning_discriminator_refuses_without_filename_dependency(self):
        claims = (
            "record_type: implementation-result\n",
            "record_type: implementation-migration\n",
            "record_type: implementation-migration-publication\n",
            "record_type: implementation-decision\nbroken: [\n",
            "record_type: implementation-annotation\nrecord_type: ordinary\n",
            "kind: &kind implementation-result\nrecord_type: *kind\n",
            "field: &field record_type\n*field: implementation-result\n",
            "base: &base {record_type: implementation-result}\n<<: *base\n",
            "base: &base {record_type: implementation-result}\n<<: [*base]\n",
        )
        for name in ("reasoning.txt", "extensionless", "disguised.py"):
            for text in claims:
                with self.subTest(name=name, text=text):
                    (self.root / name).write_text(text)
                    self.assertTrue(self.problems(name))
                    self.assertTrue(admission.recovery_source_problems(self.root, evidence=[name + ":1-1"]))

    def test_ordinary_code_quoted_markers_and_nested_literals_remain_factual_sources(self):
        ordinary = (
            "class Same:\n  pass\nclass Same:\n  pass\n",
            '"record_type: implementation-result"\n',
            'text: "record_type: implementation-result"\n',
            "nested: {record_type: implementation-result}\n",
            'text: "record_type: implementation-migration-publication"\n',
            "nested: {record_type: implementation-migration-publication}\n",
            "- {record_type: implementation-result}\n",
            "text: |\n  record_type: implementation-result\n",
            "x = 1\n",
        )
        for name in ("ordinary.py", "ordinary.md", "extensionless"):
            for text in ordinary:
                with self.subTest(name=name, text=text):
                    (self.root / name).write_text(text)
                    self.assertEqual(self.problems(name), [])

    def test_explicit_serialized_and_frontmatter_sources_keep_strict_parser_refusals(self):
        for name, text in (("ordinary.yaml", "class: one\nclass: two\n"),
                           ("ordinary.md", "---\nclass: one\nclass: two\n---\n")):
            with self.subTest(name=name):
                (self.root / name).write_text(text)
                self.assertTrue(self.problems(name))

    def test_containment_and_grammar_remain_shared(self):
        for evidence in (["../source.py:1-1"], ["source.py:bad"], ["missing.py:1-1"]):
            self.assertTrue(admission.recovery_source_problems(self.root, evidence=evidence))
        self.assertEqual(admission.recovery_source_problems(self.root, evidence=["source.py:1-1"]), [])

    def test_one_collision_policy_reserves_empty_keys_and_peers_before_own(self):
        maps = dict(slugs={"live": "OBS-0001/live"}, retired_slugs={"retired": []},
                    historical_slugs={"historical": {}})
        removed = {"ADR-0009/removed": {}}
        for slug in ("retired", "historical", "removed", "live"):
            self.assertTrue(admission._slug_problems(maps, slug, removed=removed))
        self.assertEqual(admission._slug_problems(maps, "distinct"), [])
        self.assertEqual(admission._slug_problems(maps, "live", own_handle="OBS-0001/live"), [])
        self.assertTrue(admission._slug_problems(maps, "live", own_handle="OBS-0001/live", peer="another"))
        self.assertTrue(admission._slug_problems(maps, "retired", own_handle="OBS-0001/retired"))

    def test_public_boundaries_have_no_proof_or_owner_override(self):
        for function in (admission.admission_problems, admission.recovery_source_problems):
            for keyword in ("proof", "view", "registry", "own_handle", "reservation_map"):
                with self.subTest(function=function.__name__, keyword=keyword), self.assertRaises(TypeError):
                    arguments = dict(evidence=["source.py:1-1"])
                    if function is admission.admission_problems:
                        arguments["slug"] = None
                    function(self.root, **arguments, **{keyword: {}})
        self.assertIn("source_record", inspect.signature(admission.admission_problems).parameters)

    def test_operation_rechecks_source_and_new_reservation_before_mutation(self):
        context = admission._load_context(self.root)
        self.assertEqual(admission._source_problems(context, ["source.py:1-1"]), [])
        (self.root / "source.py").write_text("changed = 2\n")
        with self.assertRaises(ValueError): admission._revalidate(context)
        context = admission._load_context(self.root)
        self.write_rule("new")
        with self.assertRaises(ValueError): admission._revalidate(context)

    def write_rule(self, slug):
        (self.adrs / "ADR-0001-constraint.md").write_text("---\n" + yaml.safe_dump(dict(
            id="ADR-0001", status="Accepted", governs=[dict(handle="ADR-0001/" + slug,
            domain="testing", rule="Preserve a constraint.", scope="source", provenance="authored")])) + "---\n")

    def write_observation(self, *, status="ratified", extra=None):
        document = dict(id="OBS-0001", status=status, anchor_id="a" * 16,
            evidence=["source.py:1-1"], provenance="recovered", governs=[dict(
            handle="OBS-0001/factual", domain="testing", rule="The source assigns one.",
            scope="source", provenance="recovered")])
        document.update(extra or {})
        path = self.obs / "OBS-0001-factual.md"
        path.write_text("---\n" + yaml.safe_dump(document) + "---\nA factual observation.\n")
        return path

    def test_audit_refuses_reasoning_evidence_but_factual_own_record_passes(self):
        import check_observations
        self.write_observation()
        self.assertFalse(any("CHK-OBS-EVIDENCE" in p for p in check_observations.check(self.root)["broken"]))
        (self.root / "source.py").write_text("---\nrecord_type: implementation-result\n---\n")
        self.assertTrue(any("CHK-OBS-EVIDENCE" in p for p in check_observations.check(self.root)["broken"]))

    def test_audit_project_root_alias_preserves_facts_and_outside_source_refusal(self):
        import check_observations
        alias_root = tempfile.TemporaryDirectory()
        self.addCleanup(alias_root.cleanup)
        alias = Path(alias_root.name) / "repository-alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.write_observation()
        findings = check_observations.check(alias)["broken"]
        self.assertFalse(any("CHK-OBS-EVIDENCE" in p for p in findings), findings)
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        source = Path(outside.name) / "source.py"
        source.write_text("x = 1\n")
        (self.root / "escape.py").symlink_to(source)
        self.write_observation(extra=dict(evidence=["escape.py:1-1"]))
        self.assertTrue(any("CHK-OBS-EVIDENCE" in p
                            for p in check_observations.check(alias)["broken"]))

    def test_audit_and_slugless_public_boundary_refuse_nonprojecting_typed_record(self):
        import check_observations
        path = self.write_observation(status="observed", extra=dict(record_type="implementation-annotation"))
        self.assertTrue(check_observations.check(self.root)["broken"])
        self.assertTrue(admission.admission_problems(self.root, slug=None,
            evidence=["source.py:1-1"], source_record=path))

    def test_exact_public_own_record_resumes_but_another_root_record_refuses(self):
        path = self.write_observation()
        self.assertEqual(self.problems(slug="factual", source_record=path), [])
        self.assertTrue(self.problems(slug="factual"))
        self.assertTrue(self.problems(slug=None, source_record=self.root / "source.py"))

    def test_empty_recovery_roster_checks_context_without_requiring_candidate_evidence(self):
        self.assertEqual(admission.recovery_source_problems(self.root, evidence=[]), [])
        with mock.patch.object(admission, "_load_context", side_effect=admission.AdmissionRefusal("synthetic-proof-refused")):
            self.assertTrue(admission.recovery_source_problems(self.root, evidence=[]))

    def test_malformed_corpus_yaml_returns_bounded_refusal_without_source_bytes(self):
        (self.adrs / "ADR-0001-malformed.md").write_text(
            '---\nid: ADR-0001\nstatus: Accepted\nsecret_like: "unterminated\n---\n')
        problems = admission.recovery_source_problems(self.root, evidence=[])
        self.assertEqual(problems, ["admission-context-or-source-refused"])
        self.assertNotIn("secret_like", " ".join(problems))

    def test_nonmapping_corpus_frontmatter_returns_bounded_refusal(self):
        for block in ("- id: ADR-0001\n", "scalar record\n"):
            with self.subTest(block=block):
                (self.adrs / "ADR-0001-malformed.md").write_text("---\n" + block + "---\n")
                self.assertEqual(admission.recovery_source_problems(self.root, evidence=[]),
                                 ["admission-context-or-source-refused"])

    def test_actual_live_reservation_versus_absent_key(self):
        self.write_rule("reserved")
        self.assertTrue(self.problems(slug="reserved"))
        self.assertEqual(self.problems(slug="distinct"), [])

    def test_alternate_tree_uses_selected_manifest_and_reservations_without_config_edits(self):
        self.write_rule("configured")
        alt = self.root / "alternate"
        (alt / "adrs").mkdir(parents=True)
        (alt / "observations").mkdir()
        (alt / "manifest.yml").write_text("concerns_enabled: [adrs, observations]\n")
        source = self.adrs / "ADR-0001-constraint.md"
        (alt / "adrs" / source.name).write_text(source.read_text().replace("/configured", "/selected"))
        config = (self.root / ".bionic.yml").read_bytes()
        self.assertTrue(admission.admission_problems(self.root, slug="selected",
            evidence=["source.py:1-1"], docs_dir="alternate"))
        self.assertEqual(admission.admission_problems(self.root, slug="configured",
            evidence=["source.py:1-1"], docs_dir="alternate"), [])
        self.assertEqual((self.root / ".bionic.yml").read_bytes(), config)

    def test_manifest_leaf_refuses_before_any_read_or_file_probe_in_both_selectors(self):
        import implementation_migration as migration
        read_text, is_file = Path.read_text, Path.is_file
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        secret = Path(outside.name) / "outside.yml"
        secret.write_text("concerns_enabled: [observations]\n")
        for selector in (None, "alternate"):
            tree = self.tree if selector is None else self.root / selector
            tree.mkdir(exist_ok=True)
            manifest = tree / "manifest.yml"
            for target in (secret, Path(outside.name) / "missing.yml", self.root / "missing.yml"):
                with self.subTest(selector=selector, target=target):
                    if manifest.exists() or manifest.is_symlink():
                        manifest.unlink()
                    manifest.symlink_to(target)
                    def trapped_read(path, *args, **kwargs):
                        if path in (manifest, secret):
                            raise AssertionError("manifest bytes read before leaf guard")
                        return read_text(path, *args, **kwargs)
                    def trapped_probe(path):
                        if path == manifest:
                            raise AssertionError("manifest probed before leaf guard")
                        return is_file(path)
                    with mock.patch.object(Path, "read_text", trapped_read), \
                         mock.patch.object(Path, "is_file", trapped_probe), \
                         mock.patch.object(migration, "admission_authority_view") as authority:
                        self.assertTrue(admission.recovery_source_problems(self.root,
                            evidence=[], docs_dir=selector))
                        authority.assert_not_called()

    def test_contained_and_absent_manifests_preserve_ordinary_context_behavior(self):
        context = admission._load_context(self.root)
        self.assertEqual(context["observations"], self.obs)
        (self.tree / "manifest.yml").unlink()
        context = admission._load_context(self.root)
        self.assertIsNone(context["observations"])
        self.assertEqual(self.problems(), [])

    def test_escaping_manifest_bytes_are_never_read_before_guard(self):
        original = Path.read_text
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        secret = Path(outside.name) / "outside.yml"
        secret.write_text("concerns_enabled: [observations]\n")
        for selector in (None, "alternate"):
            with self.subTest(selector=selector):
                tree = self.tree if selector is None else self.root / selector
                tree.mkdir(exist_ok=True)
                manifest = tree / "manifest.yml"
                if manifest.exists():
                    manifest.unlink()
                manifest.symlink_to(secret)
                def read_trap(path, *args, **kwargs):
                    if path in (manifest, secret):
                        raise AssertionError("escaping manifest bytes reached")
                    return original(path, *args, **kwargs)
                with mock.patch.object(Path, "read_text", read_trap):
                    self.assertTrue(admission.recovery_source_problems(self.root,
                        evidence=[], docs_dir=selector))

    def test_manifest_swap_before_descriptor_read_refuses_without_following_target(self):
        from admission_source_io import SourceIO
        original = SourceIO.read_bytes
        manifest = self.tree / "manifest.yml"
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        secret = Path(outside.name) / "outside.yml"
        secret.write_text("concerns_enabled: [observations]\n")
        swapped = []
        def swap(source_io, path, *, max_bytes=None):
            if path == manifest and not swapped:
                manifest.unlink()
                manifest.symlink_to(secret)
                swapped.append(True)
            return original(source_io, path, max_bytes=max_bytes)
        with mock.patch.object(SourceIO, "read_bytes", swap):
            self.assertTrue(admission.recovery_source_problems(self.root, evidence=[]))
        self.assertEqual(swapped, [True])

    def test_configuration_reviews_and_ledger_leaf_read_traps(self):
        original = Path.read_text
        for selector in (None, "alternate"):
            for relative in (".bionic.yml", "adrs/summaries/backfill-reviews.yml",
                             "adrs/doctrine/reconciliations.yml"):
                with self.subTest(selector=selector, relative=relative):
                    self.setUp()
                    tree = self.tree if selector is None else self.root / selector
                    if selector is not None:
                        tree.mkdir()
                        (tree / "manifest.yml").write_bytes((self.tree / "manifest.yml").read_bytes())
                    outside = tempfile.TemporaryDirectory()
                    self.addCleanup(outside.cleanup)
                    target = Path(outside.name) / "outside.yml"
                    target.write_text('config_version: "1"\ndocs_dir: docs\n')
                    leaf = self.root / relative if relative == ".bionic.yml" else tree / relative
                    leaf.parent.mkdir(parents=True, exist_ok=True)
                    if leaf.exists(): leaf.unlink()
                    leaf.symlink_to(target)
                    def trapped(path, *args, **kwargs):
                        if path == leaf or path == target:
                            raise AssertionError("unguarded context bytes read")
                        return original(path, *args, **kwargs)
                    with mock.patch.object(Path, "read_text", trapped):
                        self.assertTrue(admission.recovery_source_problems(self.root,
                            evidence=[], docs_dir=selector))

    def test_adr_and_observation_leaf_and_directory_escape_read_traps(self):
        original = Path.read_text
        for selector in (None, "alternate"):
            for relative in ("adrs", "adrs/archive", "observations",
                             "adrs/ADR-0002-linked.md", "observations/OBS-0002-linked.md"):
                with self.subTest(selector=selector, relative=relative):
                    self.setUp()
                    tree = self.tree if selector is None else self.root / selector
                    if selector is not None:
                        tree.mkdir()
                        (tree / "manifest.yml").write_bytes((self.tree / "manifest.yml").read_bytes())
                    outside = tempfile.TemporaryDirectory()
                    self.addCleanup(outside.cleanup)
                    target = Path(outside.name)
                    (target / "ADR-0002-linked.md").write_text("---\nid: ADR-0002\n---\n")
                    (target / "OBS-0002-linked.md").write_text("---\nid: OBS-0002\n---\n")
                    leaf = tree / relative
                    leaf.parent.mkdir(parents=True, exist_ok=True)
                    if leaf.is_dir(): leaf.rmdir()
                    if relative.endswith(".md"):
                        leaf.symlink_to(target / leaf.name)
                    else:
                        leaf.symlink_to(target, target_is_directory=True)
                    def trapped(path, *args, **kwargs):
                        if path == leaf or leaf in path.parents or target in path.parents:
                            raise AssertionError("escaped corpus bytes read")
                        return original(path, *args, **kwargs)
                    with mock.patch.object(Path, "read_text", trapped):
                        self.assertTrue(admission.recovery_source_problems(self.root,
                            evidence=[], docs_dir=selector))

    def test_ordinary_full_corpus_and_absent_boundaries_preserve_selected_facts(self):
        import summaries_projection as sp
        self.write_rule("constraint")
        self.write_observation()
        reviews = sp.reviews_path(self.adrs)
        reviews.parent.mkdir(parents=True)
        reviews.write_text('config_version: "1"\nbatches: []\nreceipts: []\n')
        ledger = self.adrs / "doctrine" / sp.RECONCILIATIONS_FILENAME
        ledger.parent.mkdir()
        ledger.write_text("reconciliations: []\n")
        self.assertTrue(self.problems(slug="constraint"))
        self.assertTrue(self.problems(slug="factual"))
        self.assertEqual(self.problems(slug="distinct"), [])
        reviews.unlink(); ledger.unlink()
        self.assertEqual(self.problems(slug="distinct"), [])
        (self.adrs / "ADR-0001-constraint.md").unlink()
        (self.obs / "OBS-0001-factual.md").unlink()
        self.obs.rmdir()
        self.assertEqual(self.problems(slug="distinct"), [])

    def test_context_directory_swap_refuses_before_outside_enumeration(self):
        original_open, original_listdir = os.open, os.listdir
        for selector in (None, "alternate"):
            for name in ("adrs", "observations"):
                with self.subTest(selector=selector, directory=name):
                    self.setUp()
                    tree = self.tree if selector is None else self.root / selector
                    if selector is not None:
                        tree.mkdir()
                        (tree / "manifest.yml").write_bytes((self.tree / "manifest.yml").read_bytes())
                        (tree / name).mkdir()
                    outside = tempfile.TemporaryDirectory()
                    self.addCleanup(outside.cleanup)
                    target = Path(outside.name)
                    outside_inode = target.stat().st_ino
                    swapped = []
                    def swap(path, flags, *args, **kwargs):
                        if path == name and kwargs.get("dir_fd") is not None and not swapped:
                            (tree / name).rename(tree / (name + "-original"))
                            (tree / name).symlink_to(target, target_is_directory=True)
                            swapped.append(True)
                        return original_open(path, flags, *args, **kwargs)
                    def trap(path):
                        if isinstance(path, int):
                            self.assertNotEqual(os.fstat(path).st_ino, outside_inode,
                                                "escaped directory enumerated")
                        return original_listdir(path)
                    with mock.patch("os.open", swap), mock.patch("os.listdir", trap):
                        self.assertTrue(admission.recovery_source_problems(self.root,
                            evidence=[], docs_dir=selector))
                    self.assertEqual(swapped, [True])

    def test_config_and_reviews_swap_after_preflight_refuse_before_outside_bytes(self):
        import summaries_projection as sp
        original_tree_name, original_collect = sp.tree_name, sp.collect_records
        for relative in (".bionic.yml", "adrs/summaries/backfill-reviews.yml"):
            with self.subTest(relative=relative):
                self.setUp()
                outside = tempfile.TemporaryDirectory()
                self.addCleanup(outside.cleanup)
                target = Path(outside.name) / "outside.yml"
                target.write_text('config_version: "1"\ndocs_dir: docs\n')
                leaf = self.root / relative if relative == ".bionic.yml" else self.tree / relative
                leaf.parent.mkdir(parents=True, exist_ok=True)
                if not leaf.exists(): leaf.write_text('config_version: "1"\nbatches: []\nreceipts: []\n')
                swapped = []
                def mutate():
                    if not swapped:
                        leaf.unlink(); leaf.symlink_to(target); swapped.append(True)
                def tree_name(*args, **kwargs):
                    mutate()
                    return original_tree_name(*args, **kwargs)
                def collect(*args, **kwargs):
                    mutate()
                    return original_collect(*args, **kwargs)
                secret_inode = target.stat().st_ino
                original_read = os.read
                def trap(fd, size):
                    self.assertNotEqual(os.fstat(fd).st_ino, secret_inode, "outside bytes read")
                    return original_read(fd, size)
                seam, wrapper = ("tree_name", tree_name) if relative == ".bionic.yml" else ("collect_records", collect)
                with mock.patch.object(sp, seam, wrapper), mock.patch("os.read", trap):
                    self.assertTrue(admission.recovery_source_problems(self.root, evidence=[]))
                self.assertEqual(swapped, [True])

    def test_contained_record_aliases_preserve_reservations_and_snapshot(self):
        self.write_rule("constraint")
        path = self.write_observation()
        (self.adrs / "alias-target").mkdir()
        (self.obs / "alias-target").mkdir()
        for original in (self.adrs / "ADR-0001-constraint.md", path):
            target = original.parent / "alias-target" / original.name
            original.rename(target)
            original.symlink_to(target)
        context = admission._load_context(self.root)
        self.assertEqual(context["reservations"]["slugs"]["constraint"], "ADR-0001/constraint")
        self.assertEqual(context["reservations"]["slugs"]["factual"], "OBS-0001/factual")
        admission._revalidate(context)

    def test_new_preflight_receipt_or_ledger_refuses_before_mutation(self):
        import summaries_projection as sp
        for relative, content in ((sp.reviews_path(self.adrs).relative_to(self.tree),
                                   'config_version: "1"\nbatches: []\nreceipts: []\n'),
                                  (Path("adrs/doctrine") / sp.RECONCILIATIONS_FILENAME,
                                   "reconciliations: []\n")):
            with self.subTest(relative=relative):
                self.setUp()
                context = admission._load_context(self.root)
                path = self.tree / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                with self.assertRaises(ValueError): admission._revalidate(context)


class PublishedAdmission(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if IS_STAGED_ARTIFACT and not (REPO_ROOT / "tools/tests/test_summarize_migration_consumers.py").is_file():
            # tools/tests is dev-repo-only and never crosses the sync boundary.
            raise unittest.SkipTest("tools/tests/test_summarize_migration_consumers.py is absent "
                                    "in the staged artifact")
        from tools.tests.test_summarize_migration_consumers import PublishedConsumers
        from test_implementation_authority import PublicationProof
        import implementation_migration as migration
        import _council_gate_support as support
        import json
        close = PublicationProof.close
        def close_with_classifications(fixture):
            (fixture.root / "docs/manifest.yml").write_text(
                "schema_version: 5\nconcerns_enabled: [adrs, observations, arch]\n")
            (fixture.root / "docs/observations").mkdir()
            source = fixture.root / "docs/adrs/ADR-0093-body-only.md"
            batch = yaml.safe_load(fixture.f.path.read_bytes())
            for disposition, clause in (("historical-authorization", "The old cycle authorized this implementation.\n"),
                                        ("historical-fact", "The old implementation existed in that cycle.\n"),
                                        ("architecture-retained", "Keep the enduring architectural boundary.\n")):
                source.write_text(source.read_text() + "\n" + clause)
                digest = migration._digest(clause.encode())
                entry = dict(source_adr="ADR-0093", clause=dict(text=clause, sha256=digest),
                    affected_governs=[], disposition=disposition, rationale="Synthetic classified clause.",
                    historical_destination=dict(source_adr="ADR-0093", clause_sha256=digest), replacement_handles=[])
                entry["source_identity"] = migration.source_identity(entry)
                batch["entries"].append(entry)
            fixture.f.path.write_text(yaml.safe_dump(batch, sort_keys=False))
            fixture.f.retained.write_bytes(fixture.f.path.read_bytes())
            receipt = json.loads(fixture.f.receipt.read_bytes())
            receipt["subjects"][0]["sha256"] = migration._digest(fixture.f.path.read_bytes())
            support.select_question(fixture.root, receipt)
            fixture.f.receipt.write_text(json.dumps(receipt))
            support.commit_all(fixture.root, "synthetic exact reviewed classified batch before close")
            close(fixture)
        cls.owner = PublishedConsumers
        with mock.patch.object(PublicationProof, "close", close_with_classifications):
            cls.owner.setUpClass()
        cls.addClassCleanup(cls.owner.doClassCleanups)
        cls.root = cls.owner.root
        cls.fact_lines = {}
        for name in ("ADR-0110-source.md", "ADR-0093-body-only.md"):
            source = cls.root / "docs/adrs" / name
            source.write_text(source.read_text() + "\nDistinct source fact: the parser exists.\n")
            cls.fact_lines[source.relative_to(cls.root).as_posix()] = len(source.read_text().splitlines())
        line = cls.fact_lines["docs/adrs/ADR-0110-source.md"]
        cls.fact_evidence = f"docs/adrs/ADR-0110-source.md:{line}-{line}"
        support.commit_all(cls.root, "synthetic distinct source fact outside selected reasoning")

    def test_actual_all_lane_reservations_preserve_empty_retired_and_live_architecture(self):
        context = admission._load_context(self.root)
        self.assertEqual(context["reservations"]["retired_slugs"]["old-rotation"], [])
        self.assertIn("rotation", context["reservations"]["historical_slugs"])
        self.assertIn("surviving-obligation", context["reservations"]["slugs"])
        for slug in ("rotation", "old-rotation", "surviving-obligation"):
            self.assertTrue(admission.admission_problems(self.root, slug=slug,
                evidence=[self.fact_evidence]))
        self.assertEqual(admission.admission_problems(self.root, slug="distinct",
            evidence=[self.fact_evidence]), [])

    def test_mixed_and_body_only_overlap_and_straddle_refuse_while_distinct_fact_passes(self):
        context = admission._load_context(self.root)
        self.assertGreaterEqual(len(context["spans"]), 2)
        for span in context["spans"]:
            with self.subTest(source=span["source_adr"]):
                if span["disposition"] not in {"historical-implementation", "historical-authorization"}:
                    continue
                for start, end in ((span["line_start"], span["line_end"]),
                                   (span["line_start"] - 1, span["line_end"] + 1)):
                    self.assertIn("admission-historical-reasoning-refused",
                        admission.recovery_source_problems(self.root,
                        evidence=[f'{span["path"]}:{start}-{end}']))
                line = self.fact_lines[span["path"]]
                self.assertEqual(admission.recovery_source_problems(self.root,
                    evidence=[f'{span["path"]}:{line}-{line}']), [])

    def test_proved_historical_fact_and_architecture_retained_keep_explicit_classification(self):
        context = admission._load_context(self.root)
        for disposition in ("historical-fact", "architecture-retained"):
            span = next(s for s in context["spans"] if s["disposition"] == disposition)
            self.assertEqual(admission.recovery_source_problems(self.root,
                evidence=[f'{span["path"]}:{span["line_start"]}-{span["line_end"]}']), [])

    def test_invalid_proof_overrides_source_positive_and_preserves_all_bytes(self):
        import json
        from tools.tests.test_summarize_migration_consumers import snapshot
        path = self.owner.fixture.witness
        original = path.read_bytes()
        locator = json.loads(original)
        locator["applied"] = True
        path.write_text(json.dumps(locator))
        before = snapshot(self.root)
        try:
            self.assertTrue(admission.admission_problems(self.root, slug="distinct",
                evidence=[self.fact_evidence]))
            self.assertEqual(snapshot(self.root), before)
        finally:
            path.write_bytes(original)
        self.assertEqual(admission.admission_problems(self.root, slug="distinct",
            evidence=[self.fact_evidence]), [])

    def test_actual_published_survey_writers_refuse_before_any_admission_write(self):
        from tools.tests.test_summarize_migration_consumers import snapshot
        from test_survey_signoff import SO, SS, StateFile, candidate_id
        from test_survey_scaffold import _load
        scaffold = _load("scaffold-survey-sheet")
        original = snapshot(self.root)
        tree = self.root / "docs"
        obs = tree / "observations"
        obs.mkdir(exist_ok=True)
        (tree / "manifest.yml").write_text("schema_version: 5\nconcerns_enabled: [adrs, observations, arch]\n")
        (tree / "log.md").write_text("# Synthetic log\n")
        state = StateFile(tree.joinpath(*SS.STATE_FILE_REL))
        cid = candidate_id("external-dependency", "synthetic")
        span = next(s for s in admission._load_context(self.root)["spans"]
                    if s["disposition"] == "historical-implementation")
        reasoning = f'{span["path"]}:{span["line_start"]}-{span["line_end"]}'
        cand = dict(id=cid, anchor_kind="external-dependency", canonical_anchor="synthetic",
                    rule="A distinct factual claim.", evidence=[reasoning], domain="testing", state="observed")
        state.rows[cid] = cand
        state.save()
        try:
            before = snapshot(self.root)
            with self.assertRaises(scaffold.ss.SurveySheetError) as refused:
                scaffold.scaffold(self.root, None, today="2026-08-30", dry_run=False)
            self.assertIn("admission-historical-reasoning-refused", str(refused.exception))
            self.assertEqual(snapshot(self.root), before)
            (self.root / "factual.py").write_text("x = 1\n")
            cand["evidence"] = ["factual.py:1-1"]
            state.save()
            code, payload = scaffold.scaffold(self.root, None, today="2026-08-30", dry_run=False)
            self.assertEqual(code, 0)
            path = obs / f'survey-{payload["batch_id"]}.yml'
            sheet = SS.read_sheet(path)
            # A separate synthetic review names the reasoning source explicitly.
            cand["evidence"] = [reasoning]
            state.save()
            sheet["rows"][0].update(SS.seed_cells(cand), verdict="ratify", domain="testing",
                                    rationale="Synthetic human fixture only.")
            SS.write_sheet(path, sheet, contained_under=tree)
            before = snapshot(self.root)
            with self.assertRaises(SO.ss.SurveySheetError) as refused:
                SO.run(SO.make_context(self.root, None, payload["batch_id"], "2026-08-30"), dry_run=False)
            self.assertIn("admission-historical-reasoning-refused", str(refused.exception))
            self.assertEqual(snapshot(self.root), before)
        finally:
            for rel in set(snapshot(self.root)) - set(original):
                (self.root / rel).unlink()
            for rel, content in original.items():
                (self.root / rel).write_bytes(content)

    def test_published_factual_survey_completes_and_exact_own_output_resumes(self):
        from tools.tests.test_summarize_migration_consumers import snapshot
        from test_survey_signoff import SO, SS, StateFile, candidate_id
        from test_survey_scaffold import _load
        scaffold = _load("scaffold-survey-sheet")
        original = snapshot(self.root)
        tree = self.root / "docs"
        state = StateFile(tree.joinpath(*SS.STATE_FILE_REL))
        cid = candidate_id("external-dependency", "synthetic-factual")
        state.rows[cid] = dict(id=cid, anchor_kind="external-dependency",
            canonical_anchor="synthetic-factual", rule="The source assigns one.",
            evidence=["factual.py:1-1"], domain="testing", state="observed")
        try:
            (self.root / "factual.py").write_text("x = 1\n")
            (tree / "log.md").write_text("# Synthetic log\n")
            state.save()
            code, payload = scaffold.scaffold(self.root, None, today="2026-08-30", dry_run=False)
            self.assertEqual(code, 0)
            sheet_path = tree / "observations" / f'survey-{payload["batch_id"]}.yml'
            sheet = SS.read_sheet(sheet_path)
            sheet["rows"][0].update(verdict="ratify", domain="testing",
                rationale="Synthetic human fixture only.")
            SS.write_sheet(sheet_path, sheet, contained_under=tree)
            ctx = SO.make_context(self.root, None, payload["batch_id"], "2026-08-30")
            self.assertEqual(SO.advance(ctx)[0], "S1")
            self.assertEqual(SO.advance(ctx)[0], "S2")
            self.assertEqual(SO.run(ctx, dry_run=False)[1]["state"], "S9")
            completed = snapshot(self.root)
            self.assertEqual(SO.run(ctx, dry_run=False)[1]["state"], "S9")
            self.assertEqual(snapshot(self.root), completed)
        finally:
            for rel in set(snapshot(self.root)) - set(original):
                (self.root / rel).unlink()
            for rel, content in original.items():
                (self.root / rel).write_bytes(content)
