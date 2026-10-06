"""Test suite for the ADR-0044 config-surface migration.

Covers `.bionic.yml` as the layout source of truth, the two-file precedence
(`.bionic.yml` > legacy `.crux` > convention), the ADR-0032 §3 semantics
carried forward verbatim when the config is loaded from `.bionic.yml`, the
`crux_config` → `bionic_config` compat-shim equivalence, and the
`bionic-config.py` / `crux-config.py` CLI-delegation equivalence.

Stdlib only (unittest, tempfile, subprocess, json, sys, pathlib). Every test
uses a fresh tempdir as the repo root — never this repo's real root.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# tests/ -> scripts/ -> crux/ -> repo root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "crux" / "scripts"
BIONIC_CLI = SCRIPTS_DIR / "bionic-config.py"
CRUX_CLI = SCRIPTS_DIR / "crux-config.py"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import bionic_config  # noqa: E402  type: ignore
import crux_config  # noqa: E402  type: ignore
from bionic_config import (  # noqa: E402
    BionicConfigError,
    SchemaVersionError,
    load_config,
    require_schema_version,
)


class BionicConfigTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="bionic-config-test-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "docs").mkdir()

    def write(self, filename: str, body: str) -> Path:
        target = self.root / filename
        target.write_text(body, encoding="utf-8")
        return target

    def valid(self, docs_dir="docs", prefix=""):
        return f'config_version: "1"\ndocs_dir: {docs_dir}\nartifact_prefix: "{prefix}"\n'

    def require_symlinks(self):
        t = self.root / ".symlink-probe-target"
        link = self.root / ".symlink-probe"
        try:
            t.mkdir(exist_ok=True)
            link.symlink_to(t, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable on this platform/filesystem")


# ───────────────────────────── precedence (§3) ──────────────────────────────


class TestPrecedence(BionicConfigTestCase):
    def test_bionic_wins_over_crux(self):
        self.write(".crux", self.valid(prefix="CRX"))
        self.write(".bionic.yml", self.valid(prefix="BIO"))
        cfg = load_config(self.root)
        self.assertEqual(cfg.source, ".bionic.yml")
        self.assertEqual(cfg.artifact_prefix, "BIO")

    def test_crux_only_read_as_legacy(self):
        self.write(".crux", self.valid(prefix="CRX"))
        cfg = load_config(self.root)
        self.assertEqual(cfg.source, ".crux")
        self.assertEqual(cfg.artifact_prefix, "CRX")

    def test_neither_yields_discovery(self):
        """ADR-0059: absent config no longer means the flat `docs` default.

        With no config and no tree, discovery finds nothing and returns the new
        greenfield default, `bionic`. The `source` reports which arm resolved it
        so a reader can tell an inferred layout from a declared one.
        """
        cfg = load_config(self.root)
        self.assertEqual(cfg.source, "discovery:bionic")
        self.assertEqual(cfg.artifact_prefix, "")
        self.assertEqual(cfg.docs_dir, "bionic")

    def test_absent_config_with_legacy_tree_resolves_to_docs(self):
        """The safety net: an established docs/ tree keeps working with zero config."""
        (self.root / "docs").mkdir(parents=True, exist_ok=True)
        (self.root / "docs" / "manifest.yml").write_text(
            'schema_version: "4"\nconcerns_enabled:\n  - adrs\n', encoding="utf-8"
        )
        cfg = load_config(self.root)
        self.assertEqual(cfg.docs_dir, "docs")

    def test_config_without_docs_dir_falls_through_to_discovery(self):
        """Clause 3: a keyless config must not take the new default.

        A hand-truncated config beside an established docs/ tree resolving to a
        nonexistent bionic/ is the invisible-inference failure ADR-0059 forbids.
        """
        (self.root / "docs").mkdir(parents=True, exist_ok=True)
        (self.root / "docs" / "manifest.yml").write_text(
            'schema_version: "4"\nconcerns_enabled:\n  - adrs\n', encoding="utf-8"
        )
        self.write(".bionic.yml", 'config_version: "1"\n')
        cfg = load_config(self.root)
        self.assertEqual(cfg.docs_dir, "docs")

    def test_malformed_bionic_fails_loud_no_fallback_to_crux(self):
        # A valid .crux is present, but the malformed .bionic.yml must NOT
        # silently fall through to it — that would split the tree.
        self.write(".crux", self.valid(prefix="CRX"))
        self.write(".bionic.yml", "config_version: 1\n")  # unquoted -> invalid
        with self.assertRaises(BionicConfigError):
            load_config(self.root)

    def test_dangling_bionic_symlink_fails_loud_no_fallback(self):
        self.require_symlinks()
        self.write(".crux", self.valid(prefix="CRX"))
        (self.root / ".bionic.yml").symlink_to(self.root / "nonexistent.yml")
        with self.assertRaises(BionicConfigError):
            load_config(self.root)


# ─────────────── ADR-0032 §3 semantics carried forward via .bionic.yml ───────


class TestSemanticsCarriedForward(BionicConfigTestCase):
    def test_prefix_grammar_and_prefixed_helper(self):
        self.write(".bionic.yml", self.valid(prefix="CRX"))
        cfg = load_config(self.root)
        self.assertEqual(cfg.prefixed("PB-0040"), "CRX-PB-0040")
        self.assertEqual(cfg.prefixed("ADR-0012"), "CRX-ADR-0012")

    def test_reserved_prefix_rejected(self):
        self.write(".bionic.yml", self.valid(prefix="PB"))
        with self.assertRaises(BionicConfigError):
            load_config(self.root)

    def test_bad_prefix_grammar_rejected(self):
        self.write(".bionic.yml", self.valid(prefix="lowercase"))
        with self.assertRaises(BionicConfigError):
            load_config(self.root)

    def test_docs_dir_absolute_rejected(self):
        self.write(".bionic.yml", 'config_version: "1"\ndocs_dir: /etc\n')
        with self.assertRaises(BionicConfigError):
            load_config(self.root)

    def test_docs_dir_parent_traversal_rejected(self):
        self.write(".bionic.yml", 'config_version: "1"\ndocs_dir: ../escape\n')
        with self.assertRaises(BionicConfigError):
            load_config(self.root)

    def test_docs_dir_denylisted_first_segment_rejected(self):
        self.write(".bionic.yml", 'config_version: "1"\ndocs_dir: .github/x\n')
        with self.assertRaises(BionicConfigError):
            load_config(self.root)

    def test_docs_dir_shell_metacharacters_rejected(self):
        # The value flows into shell-composed filesystem operations (init-docs
        # archive/rollback); command substitution must never survive validation.
        for payload in ('$(id)', '`id`', 'a;rm', 'a|id', 'a>out', 'bionic$(id)',
                        'a&&b', 'x`y`z'):
            self.write(".bionic.yml", f'config_version: "1"\ndocs_dir: "{payload}"\n')
            with self.assertRaises(BionicConfigError, msg=payload):
                load_config(self.root)

    def test_docs_dir_leading_dash_and_non_ascii_rejected(self):
        for payload in ('-rf', '--force', '‮', 'café'):
            self.write(".bionic.yml", f'config_version: "1"\ndocs_dir: "{payload}"\n')
            with self.assertRaises(BionicConfigError, msg=payload):
                load_config(self.root)

    def test_source_never_emits_unreachable_defaults_sentinel(self):
        # The CLI's config-absent source is "discovery:<dir>" — "defaults" is
        # the private helper's sentinel, overwritten in load_config.
        cfg = load_config(self.root)
        self.assertNotEqual(cfg.source, "defaults")
        self.assertTrue(cfg.source.startswith("discovery:"))

    def test_docs_dir_charset_allowlist_accepts_legitimate_names(self):
        for payload in ('bionic', 'docs', 'my-docs', 'my_docs', 'docs.v2',
                        'meta/docs', '_private'):
            self.write(".bionic.yml", f'config_version: "1"\ndocs_dir: "{payload}"\n')
            cfg = load_config(self.root)
            self.assertEqual(cfg.docs_dir, payload, msg=payload)

    def test_containment_symlink_escape_rejected(self):
        self.require_symlinks()
        outside = self.root.parent / (self.root.name + "-outside")
        outside.mkdir()
        self.addCleanup(lambda: outside.rmdir())
        (self.root / "ledger").symlink_to(outside, target_is_directory=True)
        self.write(".bionic.yml", 'config_version: "1"\ndocs_dir: ledger\n')
        with self.assertRaises(BionicConfigError):
            load_config(self.root)

    def test_unsupported_config_version_rejected(self):
        self.write(".bionic.yml", 'config_version: "2"\n')
        with self.assertRaises(BionicConfigError):
            load_config(self.root)

    def test_unquoted_config_version_rejected(self):
        self.write(".bionic.yml", "config_version: 1\n")
        with self.assertRaises(BionicConfigError):
            load_config(self.root)


# ─────────────────────────── compat-shim equivalence ────────────────────────


class TestShimEquivalence(BionicConfigTestCase):
    def test_shim_reexports_are_the_same_objects(self):
        # The shim's plain `import bionic_config` resolves to the same
        # sys.modules instance imported here, so identities hold.
        self.assertIs(crux_config.BionicConfig, bionic_config.BionicConfig)
        self.assertIs(crux_config.CruxConfig, bionic_config.BionicConfig)
        self.assertIs(crux_config.CruxConfigError, bionic_config.BionicConfigError)
        self.assertIs(crux_config.load_config, bionic_config.load_config)

    def test_shim_and_canonical_resolve_identically(self):
        self.write(".bionic.yml", self.valid(prefix="BIO"))
        via_shim = crux_config.load_config(self.root)
        via_canon = bionic_config.load_config(self.root)
        self.assertEqual(via_shim.source, via_canon.source)
        self.assertEqual(via_shim.artifact_prefix, via_canon.artifact_prefix)
        self.assertEqual(via_shim.docs_root, via_canon.docs_root)

    def test_private_helpers_reexported(self):
        # The test suite / consumers poke `_validate_docs_dir_text`; the shim
        # must re-export single-underscore module-level helpers.
        self.assertTrue(hasattr(crux_config, "_validate_docs_dir_text"))
        self.assertIs(crux_config.CONFIG_FILENAME, bionic_config.CONFIG_FILENAME)
        self.assertIs(
            crux_config.BIONIC_CONFIG_FILENAME, bionic_config.BIONIC_CONFIG_FILENAME
        )


# ───────────────────────────── CLI delegation ───────────────────────────────


class TestCLIDelegation(BionicConfigTestCase):
    def _run(self, cli: Path):
        proc = subprocess.run(
            [sys.executable, str(cli), "--repo-root", str(self.root)],
            capture_output=True,
            text=True,
        )
        return proc.returncode, proc.stdout

    def test_both_clis_emit_identical_json(self):
        self.write(".bionic.yml", self.valid(prefix="BIO"))
        rc_b, out_b = self._run(BIONIC_CLI)
        rc_c, out_c = self._run(CRUX_CLI)
        self.assertEqual(rc_b, 0)
        self.assertEqual(rc_c, 0)
        self.assertEqual(json.loads(out_b), json.loads(out_c))
        self.assertEqual(json.loads(out_b)["source"], ".bionic.yml")

    def test_cli_error_contract_preserved(self):
        self.write(".bionic.yml", "config_version: 1\n")  # invalid
        rc, out = self._run(BIONIC_CLI)
        self.assertEqual(rc, 1)
        self.assertIn("error", json.loads(out))


class SchemaVersionGateTests(unittest.TestCase):
    """ADR-0059: an unmigrated tree is loudly blocked, never silently half-served."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _manifest(self, version: str | None) -> Path:
        if version is not None:
            (self.tmp / "manifest.yml").write_text(
                f'schema_version: "{version}"\nconcerns_enabled:\n  - adrs\n', encoding="utf-8"
            )
        return self.tmp

    def test_supported_version_passes(self):
        self.assertEqual(require_schema_version(self._manifest("5")), "5")

    def test_unquoted_version_is_read(self):
        (self.tmp / "manifest.yml").write_text(
            "schema_version: 5\nconcerns_enabled:\n  - adrs\n", encoding="utf-8"
        )
        self.assertEqual(require_schema_version(self.tmp), "5")

    def test_supported_old_versions_refuse_with_pinned_recovery(self):
        for version in ("2", "3", "4"):
            with self.subTest(version=version):
                with self.assertRaises(SchemaVersionError) as cm:
                    require_schema_version(self._manifest(version))
                self.assertIn("v3.23.2", str(cm.exception))
                self.assertIn("08ee30ec2f1d1b4b0ce970f2e1582bb4f83cd20d", str(cm.exception))
                self.assertNotIn("audit-docs --migrate", str(cm.exception))

    def test_unknown_newer_version_refuses_without_promising_a_migration(self):
        with self.assertRaises(SchemaVersionError) as cm:
            require_schema_version(self._manifest("9"))
        msg = str(cm.exception)
        self.assertIn("does not recognize", msg)
        self.assertNotIn("audit-docs --migrate", msg)
        self.assertNotIn("v3.23.2", msg)

    def test_earlier_and_unrecognized_versions_do_not_promise_the_ladder(self):
        for version in ("1", "unexpected"):
            with self.subTest(version=version):
                with self.assertRaises(SchemaVersionError) as cm:
                    require_schema_version(self._manifest(version))
                self.assertNotIn("v3.23.2", str(cm.exception))

    def test_missing_manifest_refuses(self):
        with self.assertRaises(SchemaVersionError) as cm:
            require_schema_version(self._manifest(None))
        self.assertNotIn("v3.23.2", str(cm.exception))

    def test_manifest_without_schema_version_refuses(self):
        (self.tmp / "manifest.yml").write_text("concerns_enabled:\n  - adrs\n", encoding="utf-8")
        with self.assertRaises(SchemaVersionError):
            require_schema_version(self.tmp)

    def test_error_is_a_config_error_so_existing_handlers_catch_it(self):
        self.assertTrue(issubclass(SchemaVersionError, BionicConfigError))


class ResolverDoesNotFailOpenTests(unittest.TestCase):
    """Regressions for the fail-open defect an independent review rejected.

    resolve_tree_name() once swallowed every BionicConfigError and returned the
    new default. That discarded the both-valid refusal and redirected an
    established docs/ tree to bionic/ whenever config was malformed — and every
    fallback pointed the same direction, toward the new default, which is the
    direction that loses data.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _tree(self, name: str, version: str = "5") -> None:
        d = self.root / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.yml").write_text(
            f'schema_version: "{version}"\nconcerns_enabled:\n  - adrs\n', encoding="utf-8"
        )

    def test_ambiguous_two_trees_raises_rather_than_defaulting(self):
        self._tree("docs"); self._tree("bionic")
        with self.assertRaises(BionicConfigError) as cm:
            bionic_config.resolve_tree_name(self.root)
        self.assertIn("two crux trees", str(cm.exception))

    def test_malformed_config_beside_a_legacy_tree_raises(self):
        """The silent-redirection case: docs/ is live, config is broken."""
        self._tree("docs")
        (self.root / ".bionic.yml").write_text("config_version: 1\n", encoding="utf-8")  # unquoted
        with self.assertRaises(BionicConfigError):
            bionic_config.resolve_tree_name(self.root)

    def test_greenfield_still_resolves_without_raising(self):
        """The one tolerated fallback: nothing is swallowed to reach it."""
        self.assertEqual(bionic_config.resolve_tree_name(self.root), "bionic")

    def test_legacy_tree_still_resolves_without_raising(self):
        self._tree("docs")
        self.assertEqual(bionic_config.resolve_tree_name(self.root), "docs")


class MigrationMarkerRecoveryTests(ResolverDoesNotFailOpenTests):
    """Current readers recognize tagged migration state but never resume it."""

    def _marker(self, body: str) -> Path:
        marker = self.root / "bionic" / ".migrating"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(body, encoding="utf-8")
        return marker

    def test_tagged_marker_selects_recorded_source_then_blocks_current_operation(self):
        self._tree("docs", "4")
        self._tree("bionic", "5")
        marker = self._marker("source: docs\nstep: 3\ninventory:\n  - manifest.yml\n")
        before = marker.read_bytes()
        cfg = load_config(self.root, require_tree=True)
        self.assertEqual(cfg.docs_dir, "docs")
        with self.assertRaises(SchemaVersionError) as cm:
            require_schema_version(cfg.docs_root)
        self.assertIn("v3.23.2", str(cm.exception))
        self.assertIn("resume or abandon", str(cm.exception))
        self.assertEqual(marker.read_bytes(), before)

    def test_partial_marker_on_single_current_tree_blocks_current_operation(self):
        self._tree("bionic")
        marker = self._marker("source: bionic\nstep: 4\ninventory:\n  - manifest.yml\n")
        with self.assertRaises(SchemaVersionError) as cm:
            require_schema_version(self.root / "bionic")
        self.assertIn("v3.23.2", str(cm.exception))
        self.assertTrue(marker.exists())

    def test_truncated_marker_does_not_authorize_ambiguous_tree(self):
        self._tree("docs", "4")
        self._tree("bionic", "5")
        self._marker("source: docs\n")
        with self.assertRaises(BionicConfigError) as cm:
            load_config(self.root, require_tree=True)
        self.assertIn("invalid migration marker", str(cm.exception))

    def test_invalid_marker_on_single_tree_refuses_without_modifying_it(self):
        self._tree("bionic")
        marker = self._marker("source: ../outside\nstep: 3\ninventory:\n  - manifest.yml\n")
        before = marker.read_bytes()
        with self.assertRaises(BionicConfigError) as cm:
            load_config(self.root, require_tree=True)
        self.assertIn("invalid migration marker", str(cm.exception))
        self.assertEqual(marker.read_bytes(), before)


class DeclaredButAbsentTreeTests(unittest.TestCase):
    """A reader must refuse a config that points at nothing."""

    def test_require_tree_refuses_a_declared_directory_with_no_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".bionic.yml").write_text(
                'config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8")
            (root / "bionic").mkdir()
            with self.assertRaises(BionicConfigError) as cm:
                load_config(root, require_tree=True)
            self.assertIn("no valid crux manifest", str(cm.exception))


class ArchFieldsTests(unittest.TestCase):
    """ADR-0066: validated `arch_stack` + `arch_extractors` config fields."""

    def _load(self, body: str):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".bionic.yml").write_text(body, encoding="utf-8")
            (root / "bionic").mkdir()
            return load_config(root)

    def test_absent_defaults(self):
        cfg = self._load('config_version: "1"\ndocs_dir: bionic\n')
        self.assertIsNone(cfg.arch_stack)
        self.assertEqual(cfg.arch_extractors, {})

    def test_arch_stack_parsed(self):
        cfg = self._load('config_version: "1"\ndocs_dir: bionic\narch_stack: crux\n')
        self.assertEqual(cfg.arch_stack, "crux")

    def test_arch_stack_invalid_pattern_raises(self):
        for bad in ("Crux", "1py", "has space", "wow!"):
            with self.assertRaises(BionicConfigError):
                self._load(f'config_version: "1"\ndocs_dir: bionic\narch_stack: "{bad}"\n')

    def test_arch_extractors_parsed(self):
        cfg = self._load(
            'config_version: "1"\ndocs_dir: bionic\n'
            'arch_extractors:\n  api-surface: "tools/x.py:extract"\n'
        )
        self.assertEqual(cfg.arch_extractors, {"api-surface": "tools/x.py:extract"})

    def test_arch_extractors_non_mapping_raises(self):
        with self.assertRaises(BionicConfigError):
            self._load('config_version: "1"\ndocs_dir: bionic\narch_extractors: "nope"\n')

    def test_arch_extractors_empty_value_raises(self):
        with self.assertRaises(BionicConfigError):
            self._load(
                'config_version: "1"\ndocs_dir: bionic\n'
                'arch_extractors:\n  api-surface: ""\n'
            )

    def test_cli_json_exposes_arch_fields(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".bionic.yml").write_text(
                'config_version: "1"\ndocs_dir: bionic\narch_stack: python\n'
                'arch_decision_index_mode: curated\n', encoding="utf-8")
            (root / "bionic").mkdir()
            out = subprocess.run(
                [sys.executable, str(BIONIC_CLI), "--repo-root", str(root)],
                capture_output=True, text=True, check=True,
            ).stdout
            payload = json.loads(out)
            self.assertEqual(payload["arch_decision_index_mode"], "curated")
            self.assertEqual(payload["arch_stack"], "python")
            self.assertEqual(payload["arch_extractors"], {})


class ArchDecisionIndexModeTests(unittest.TestCase):
    """ADR-0062: validated `arch_decision_index_mode` config key (Fix A, PB-0069)."""

    def _load(self, body: str):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".bionic.yml").write_text(body, encoding="utf-8")
            (root / "bionic").mkdir()
            return load_config(root)

    def test_absent_defaults_to_complete(self):
        cfg = self._load('config_version: "1"\ndocs_dir: bionic\n')
        self.assertEqual(cfg.arch_decision_index_mode, "complete")

    def test_explicit_complete(self):
        cfg = self._load(
            'config_version: "1"\ndocs_dir: bionic\narch_decision_index_mode: complete\n'
        )
        self.assertEqual(cfg.arch_decision_index_mode, "complete")

    def test_explicit_curated(self):
        cfg = self._load(
            'config_version: "1"\ndocs_dir: bionic\narch_decision_index_mode: curated\n'
        )
        self.assertEqual(cfg.arch_decision_index_mode, "curated")

    def test_invalid_value_raises(self):
        with self.assertRaises(BionicConfigError):
            self._load(
                'config_version: "1"\ndocs_dir: bionic\narch_decision_index_mode: full\n'
            )

    def test_non_string_type_raises(self):
        with self.assertRaises(BionicConfigError):
            self._load(
                'config_version: "1"\ndocs_dir: bionic\narch_decision_index_mode: 1\n'
            )

    def test_non_string_list_type_raises(self):
        with self.assertRaises(BionicConfigError):
            self._load(
                'config_version: "1"\ndocs_dir: bionic\n'
                'arch_decision_index_mode:\n  - curated\n'
            )

    def test_no_config_file_defaults_to_complete(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "bionic").mkdir()
            (root / "bionic" / "manifest.yml").write_text(
                "schema_version: '5'\nconcerns_enabled: []\n", encoding="utf-8"
            )
            cfg = load_config(root)
            self.assertEqual(cfg.arch_decision_index_mode, "complete")


class RetiredArchConfidenceThresholdTests(unittest.TestCase):
    """ADR-0096 clause 10: `arch_confidence_threshold` is RETIRED.

    The key thresholded the four-level confidence grade scale, which clause 10
    removed outright. There is no validator, no attribute, and — deliberately —
    no fail-closed branch.

    A tree written against an earlier version still carries the key on disk. The
    §14.1 forward-compat valve ("unknown keys: ignored by design") is the whole
    upgrade contract for exactly this case: refusing a key that a previous
    version of this tool INSTRUCTED users to write would turn an upgrade into an
    outage, over a value that now feeds nothing. So the retired key must be as
    inert as any typo — read, ignored, and not surfaced as an attribute that
    later code could branch on.
    """

    def _load(self, body: str):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".bionic.yml").write_text(body, encoding="utf-8")
            (root / "bionic").mkdir()
            return load_config(root)

    def test_retired_key_still_on_disk_loads_clean_and_yields_no_attribute(self):
        """The named §14.1 valve test: a stale key loads, and produces nothing.

        Both halves matter. Loading clean is the no-outage half. Producing no
        attribute is the no-zombie half — if the value survived onto the config
        object, a later reader could branch on a setting the tool no longer
        honours, which is worse than rejecting it outright.
        """
        for stale in ("high", "medium", "low", "none", "critical", "1"):
            with self.subTest(value=stale):
                cfg = self._load(
                    'config_version: "1"\ndocs_dir: bionic\n'
                    f"arch_confidence_threshold: {stale}\n"
                )
                self.assertEqual(cfg.docs_dir, "bionic")
                self.assertFalse(
                    hasattr(cfg, "arch_confidence_threshold"),
                    "the retired key must not reappear as a config attribute",
                )

    def test_no_fail_closed_branch_rejects_the_retired_key(self):
        """A value that the OLD validator refused must now be ignored, not raise.

        `none` and `critical` were both hard errors under the retired allowlist.
        Asserting they are silent now is what proves the removal took the
        fail-closed branch with it, rather than leaving a validator that still
        polices a dead key.
        """
        for once_rejected in ("none", "critical", "1"):
            with self.subTest(value=once_rejected):
                cfg = self._load(
                    'config_version: "1"\ndocs_dir: bionic\n'
                    f"arch_confidence_threshold: {once_rejected}\n"
                )
                self.assertEqual(cfg.docs_dir, "bionic")

    def test_module_exposes_no_confidence_threshold_symbol(self):
        import bionic_config

        for name in ("_validate_arch_confidence_threshold", "_ARCH_CONFIDENCE_THRESHOLDS"):
            self.assertFalse(
                hasattr(bionic_config, name),
                f"{name} survived clause 10's retirement",
            )

    def test_cli_json_omits_the_retired_key(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".bionic.yml").write_text(
                'config_version: "1"\ndocs_dir: bionic\n'
                "arch_confidence_threshold: medium\n", encoding="utf-8")
            (root / "bionic").mkdir()
            proc = subprocess.run(
                [sys.executable, str(BIONIC_CLI), "--repo-root", str(root)],
                capture_output=True, text=True, check=True,
            )
            payload = json.loads(proc.stdout)
            self.assertNotIn("arch_confidence_threshold", payload)
            self.assertEqual(payload["docs_dir"], "bionic")


if __name__ == "__main__":
    unittest.main()
