"""Test suite for crux_config.py (loader) and crux-config.py (CLI).

Per ADR-0032 §4 item 5. Covers: absent-file defaults, valid-file resolution,
the prefixed() helper, config_version validation, malformed-YAML fail-loud,
unknown-key tolerance, the docs_dir textual layer (rejections + sanctioned
acceptances), the containment layer (symlink escape rejected, in-repo symlink
accepted, symlink-to-root rejected, resolved-vs-resolved on a symlinked repo
root, post-resolution denylist via in-repo symlinks), the artifact_prefix
grammar (reserved tokens rejected), the CLI JSON contract, the schema widening
regression (prefixed ids validate in books AND runs, forked_from included;
reserved double-token ids rejected), the dot-crux.tmpl template pin, the
prefixed()-output-vs-schema-pattern cross-check, and consumer wiring
(subprocess-level) for extract-code-docs / visualize-run-progress against a
relocated docs_dir and a malformed .crux. Historical Markdown progress
remains readable after format conversion leaves the current distribution.

Uses only stdlib (unittest, tempfile, subprocess, json, os, sys, pathlib,
shutil). Every test uses a tempfile.TemporaryDirectory as the repo root —
never this repo's real root.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# tests/ -> scripts/ -> crux/ -> repo root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "crux" / "scripts"
CLI = SCRIPTS_DIR / "crux-config.py"
VALIDATE_PROMPTBOOK = SCRIPTS_DIR / "validate-promptbook.py"
EXTRACT_CODE_DOCS = SCRIPTS_DIR / "extract-code-docs.py"
VISUALIZE_RUN_PROGRESS = SCRIPTS_DIR / "visualize-run-progress.py"
SCHEMAS_DIR = REPO_ROOT / "crux" / "schemas"
TEMPLATES_DIR = REPO_ROOT / "crux" / "templates"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Place the scripts dir on sys.path so `import crux_config` works (same
# convention as test_crux_env.py's module tests).
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import crux_config  # noqa: E402  type: ignore
from crux_config import CruxConfigError, load_config  # noqa: E402

# Sibling convention (test_yaml_capability.py): some subprocess consumers
# invoked below (validate-promptbook.py, visualize-run-progress.py) REQUIRE a
# real YAML parser and refuse the minimal fallback outright when
# CRUX_NO_UV_REEXEC=1 blocks the uv re-exec lane. Their wiring tests are
# gated on PyYAML actually being importable in *this* interpreter so the
# suite reports an honest SKIP instead of a false FAILED in a PyYAML-free
# environment; tests that never shell out to a strict-YAML consumer stay
# unguarded so they still run and catch real regressions there.
try:
    import yaml as _pyyaml_probe  # noqa: F401

    HAVE_PYYAML = True
except ImportError:
    HAVE_PYYAML = False


class CruxConfigTestCase(unittest.TestCase):
    """Base class: each test gets a fresh tempdir as the repo root."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="crux-config-test-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write_crux(self, body: str, root: Path | None = None) -> Path:
        target = (root or self.root) / ".crux"
        target.write_text(body, encoding="utf-8")
        return target

    def write_valid_crux(self, docs_dir="docs", prefix="", root: Path | None = None) -> Path:
        return self.write_crux(
            f'config_version: "1"\ndocs_dir: {docs_dir}\nartifact_prefix: "{prefix}"\n',
            root=root,
        )

    def require_symlinks(self):
        """Probe for working symlink support; skip the test when unavailable
        (e.g. Windows without privileges — an actual attempt, not a hasattr
        guard, since os.symlink can exist yet fail at call time)."""
        probe_target = self.root / ".symlink-probe-target"
        probe_link = self.root / ".symlink-probe"
        try:
            probe_target.mkdir(exist_ok=True)
            probe_link.symlink_to(probe_target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable on this platform/filesystem")


# ───────────────────────────── loader: defaults ─────────────────────────────


class TestLoaderDefaults(CruxConfigTestCase):
    def test_absent_file_yields_discovery(self):
        cfg = load_config(self.root)
        self.assertEqual(cfg.config_version, "1")
        self.assertEqual(cfg.docs_dir, "bionic")
        self.assertEqual(cfg.artifact_prefix, "")
        # ADR-0059: absent config runs discovery; with no tree present the
        # greenfield default is bionic, and `source` names the arm that resolved it.
        self.assertEqual(cfg.source, "discovery:bionic")
        self.assertEqual(cfg.repo_root, self.root.resolve())
        self.assertEqual(cfg.docs_root, (self.root / "bionic").resolve())

    def test_absent_file_prefixed_is_passthrough(self):
        cfg = load_config(self.root)
        self.assertEqual(cfg.prefixed("PB-0040"), "PB-0040")

    def test_nonexistent_repo_root_raises(self):
        with self.assertRaises(CruxConfigError):
            load_config(self.root / "does-not-exist")

    def test_repo_root_that_is_a_file_raises(self):
        f = self.root / "afile"
        f.write_text("x", encoding="utf-8")
        with self.assertRaises(CruxConfigError):
            load_config(f)


# ───────────────────────────── loader: valid file ────────────────────────────


class TestLoaderValidFile(CruxConfigTestCase):
    def test_all_keys_honored(self):
        self.write_crux(
            'config_version: "1"\n'
            "docs_dir: documentation\n"
            "artifact_prefix: CRX\n"
        )
        cfg = load_config(self.root)
        self.assertEqual(cfg.config_version, "1")
        self.assertEqual(cfg.docs_dir, "documentation")
        self.assertEqual(cfg.artifact_prefix, "CRX")
        self.assertEqual(cfg.source, ".crux")
        self.assertEqual(cfg.docs_root, (self.root / "documentation").resolve())

    def test_minimal_file_falls_through_to_discovery(self):
        """ADR-0059 clause 3: a config carrying no docs_dir does NOT take the default."""
        self.write_crux('config_version: "1"\n')
        cfg = load_config(self.root)
        self.assertEqual(cfg.docs_dir, "bionic")
        self.assertEqual(cfg.artifact_prefix, "")
        # `source` records BOTH arms: the config supplied config_version and
        # prefix, discovery supplied the directory.
        self.assertEqual(cfg.source, ".crux+discovery:bionic")

    def test_minimal_file_beside_a_legacy_tree_resolves_to_docs(self):
        """The half of clause 3 that matters.

        A keyless config beside an established docs/ tree must resolve to docs,
        not to a bionic/ that does not exist. Falling back to the new default
        here is the invisible-inference failure ADR-0059 forbids.
        """
        (self.root / "docs").mkdir(parents=True, exist_ok=True)
        (self.root / "docs" / "manifest.yml").write_text(
            'schema_version: "4"\nconcerns_enabled:\n  - adrs\n', encoding="utf-8"
        )
        self.write_crux('config_version: "1"\n')
        cfg = load_config(self.root)
        self.assertEqual(cfg.docs_dir, "docs")

    def test_nested_docs_dir_accepted(self):
        self.write_crux('config_version: "1"\ndocs_dir: meta/docs\n')
        cfg = load_config(self.root)
        self.assertEqual(cfg.docs_dir, "meta/docs")
        self.assertEqual(cfg.docs_root, (self.root / "meta" / "docs").resolve())

    def test_dot_docs_dir_accepted_outside_denylist(self):
        # Dot-directories are fine; the denylist is exactly .git / .github.
        self.write_crux('config_version: "1"\ndocs_dir: .docs\n')
        cfg = load_config(self.root)
        self.assertEqual(cfg.docs_dir, ".docs")

    def test_unknown_top_level_keys_are_ignored(self):
        self.write_crux(
            'config_version: "1"\n'
            "docs_dir: docs\n"
            "future_key: whatever\n"
            "another_unknown: 42\n"
        )
        cfg = load_config(self.root)
        self.assertEqual(cfg.docs_dir, "docs")
        self.assertEqual(cfg.source, ".crux")


# ───────────────────────────── prefixed() helper ─────────────────────────────


class TestPrefixedHelper(CruxConfigTestCase):
    def test_prefixed_applies_configured_prefix(self):
        self.write_valid_crux(prefix="CRX")
        cfg = load_config(self.root)
        self.assertEqual(cfg.prefixed("PB-0040"), "CRX-PB-0040")
        self.assertEqual(cfg.prefixed("ADR-0012"), "CRX-ADR-0012")

    def test_prefixed_blank_prefix_is_passthrough(self):
        self.write_valid_crux(prefix="")
        cfg = load_config(self.root)
        self.assertEqual(cfg.prefixed("PB-0040"), "PB-0040")


# ───────────────────────────── config_version ────────────────────────────────


class TestConfigVersion(CruxConfigTestCase):
    def test_missing_config_version_raises(self):
        self.write_crux("docs_dir: docs\n")
        with self.assertRaises(CruxConfigError):
            load_config(self.root)

    def test_unquoted_config_version_yaml_int_raises(self):
        self.write_crux("config_version: 1\n")
        with self.assertRaises(CruxConfigError) as ctx:
            load_config(self.root)
        self.assertIn("config_version", str(ctx.exception))

    def test_unsupported_config_version_raises(self):
        self.write_crux('config_version: "2"\n')
        with self.assertRaises(CruxConfigError) as ctx:
            load_config(self.root)
        self.assertIn("unsupported", str(ctx.exception))


# ───────────────────────────── malformed YAML ────────────────────────────────


class TestMalformedYaml(CruxConfigTestCase):
    def test_malformed_yaml_raises_never_silent_defaults(self):
        self.write_crux('config_version: "1"\n  bad_indent: x\n')
        with self.assertRaises(CruxConfigError):
            load_config(self.root)

    def test_non_mapping_top_level_raises(self):
        self.write_crux("- just\n- a\n- list\n")
        with self.assertRaises(CruxConfigError):
            load_config(self.root)

    def test_crux_path_being_a_directory_raises(self):
        (self.root / ".crux").mkdir()
        with self.assertRaises(CruxConfigError):
            load_config(self.root)


# ───────────────────────── docs_dir textual layer ────────────────────────────


class TestDocsDirTextualRejections(CruxConfigTestCase):
    def assert_docs_dir_rejected(self, raw_yaml_value: str):
        self.write_crux(f'config_version: "1"\ndocs_dir: {raw_yaml_value}\n')
        with self.assertRaises(
            CruxConfigError, msg=f"docs_dir {raw_yaml_value!r} should be rejected"
        ):
            load_config(self.root)

    def test_empty_string_rejected(self):
        self.assert_docs_dir_rejected('""')

    def test_whitespace_only_rejected(self):
        self.assert_docs_dir_rejected('"  "')

    def test_absolute_path_rejected(self):
        self.assert_docs_dir_rejected('"/abs"')

    def test_home_reference_rejected(self):
        self.assert_docs_dir_rejected('"~/x"')

    def test_windows_drive_path_rejected(self):
        self.assert_docs_dir_rejected('"C:/docs"')

    def test_backslash_rejected(self):
        # Single-quoted YAML keeps the backslash literal: a\b
        self.assert_docs_dir_rejected("'a\\b'")

    def test_dotdot_segment_rejected(self):
        self.assert_docs_dir_rejected("a/../b")

    def test_dot_segment_rejected(self):
        self.assert_docs_dir_rejected("./a")

    def test_trailing_slash_rejected(self):
        self.assert_docs_dir_rejected("a/")

    def test_empty_inner_segment_rejected(self):
        self.assert_docs_dir_rejected("a//b")

    def test_git_first_segment_rejected(self):
        self.assert_docs_dir_rejected(".git/docs")

    def test_github_first_segment_rejected(self):
        self.assert_docs_dir_rejected(".github/docs")

    def test_control_character_rejected(self):
        self.assert_docs_dir_rejected(f'"a{chr(1)}b"')

    def test_validate_docs_dir_text_rejects_non_string(self):
        with self.assertRaises(CruxConfigError):
            crux_config._validate_docs_dir_text(123)


# ───────────────────────── docs_dir containment layer ────────────────────────


class TestDocsDirContainment(CruxConfigTestCase):
    def setUp(self):
        super().setUp()
        self.require_symlinks()

    def test_symlink_escaping_repo_root_rejected(self):
        outside = tempfile.TemporaryDirectory(prefix="crux-config-outside-")
        self.addCleanup(outside.cleanup)
        (self.root / "docs").symlink_to(Path(outside.name), target_is_directory=True)
        self.write_valid_crux(docs_dir="docs")
        with self.assertRaises(CruxConfigError) as ctx:
            load_config(self.root)
        self.assertIn("proper subdirectory", str(ctx.exception))

    def test_symlink_staying_in_repo_accepted(self):
        real = self.root / "real-docs"
        real.mkdir()
        (self.root / "docs").symlink_to(real, target_is_directory=True)
        self.write_valid_crux(docs_dir="docs")
        cfg = load_config(self.root)
        self.assertEqual(cfg.docs_root, real.resolve())

    def test_symlink_resolving_to_repo_root_itself_rejected(self):
        (self.root / "docs").symlink_to(self.root, target_is_directory=True)
        self.write_valid_crux(docs_dir="docs")
        with self.assertRaises(CruxConfigError) as ctx:
            load_config(self.root)
        self.assertIn("proper subdirectory", str(ctx.exception))

    def test_symlinked_repo_root_resolved_vs_resolved_compare(self):
        # A repo root reached THROUGH a symlink must still pass containment:
        # both sides of the compare are resolved (ADR-0032 §4 item 5).
        real_root = self.root / "real-root"
        (real_root / "docs").mkdir(parents=True)
        link_root = self.root / "link-root"
        link_root.symlink_to(real_root, target_is_directory=True)
        self.write_valid_crux(docs_dir="docs", root=link_root)
        cfg = load_config(link_root)
        self.assertEqual(cfg.repo_root, real_root.resolve())
        self.assertEqual(cfg.docs_root, (real_root / "docs").resolve())

    def test_symlink_routing_into_github_rejected_post_resolution(self):
        # docs_dir "x/workflows" passes the textual denylist (first segment is
        # "x") but the in-repo symlink x -> .github routes the RESOLVED path
        # into the execution-adjacent dir — the post-resolution check fires.
        (self.root / ".github" / "workflows").mkdir(parents=True)
        (self.root / "x").symlink_to(self.root / ".github", target_is_directory=True)
        self.write_valid_crux(docs_dir="x/workflows")
        with self.assertRaises(CruxConfigError) as ctx:
            load_config(self.root)
        self.assertIn("execution-adjacent", str(ctx.exception))

    def test_symlink_to_github_workflows_rejected_post_resolution(self):
        (self.root / ".github" / "workflows").mkdir(parents=True)
        (self.root / "x").symlink_to(
            self.root / ".github" / "workflows", target_is_directory=True
        )
        self.write_valid_crux(docs_dir="x")
        with self.assertRaises(CruxConfigError) as ctx:
            load_config(self.root)
        self.assertIn("execution-adjacent", str(ctx.exception))

    def test_dangling_crux_symlink_raises(self):
        # A committed-but-dangling .crux symlink is intended config, not
        # absence — silently using defaults would split the tree.
        (self.root / ".crux").symlink_to(self.root / "does-not-exist.yml")
        with self.assertRaises(CruxConfigError) as ctx:
            load_config(self.root)
        self.assertIn("dangling symlink", str(ctx.exception))


# ───────────────────────── new loader behaviors ──────────────────────────────


class TestLoaderHardening(CruxConfigTestCase):
    """The ADR-0032 hardening layer: alias-bomb guard, size cap, whitespace
    rejection, casefolded + extended denylist, empty file, prefixed() bare-id
    contract."""

    def test_yaml_anchor_token_outside_comment_rejected(self):
        self.write_crux('config_version: "1"\ndocs_dir: &anchor docs\n')
        with self.assertRaises(CruxConfigError) as ctx:
            load_config(self.root)
        self.assertIn("anchors/aliases", str(ctx.exception))

    def test_yaml_alias_token_outside_comment_rejected(self):
        self.write_crux('config_version: "1"\ndocs_dir: *anchor\n')
        with self.assertRaises(CruxConfigError) as ctx:
            load_config(self.root)
        self.assertIn("anchors/aliases", str(ctx.exception))

    def test_ampersand_inside_comment_accepted(self):
        self.write_crux(
            'config_version: "1"\nartifact_prefix: "CRX" # note & ok in comment\n'
        )
        cfg = load_config(self.root)
        self.assertEqual(cfg.artifact_prefix, "CRX")

    def test_file_over_64kib_rejected(self):
        body = 'config_version: "1"\n' + ("# padding\n" * 8192)  # ~80 KiB
        self.assertGreater(len(body), 65536)
        self.write_crux(body)
        with self.assertRaises(CruxConfigError) as ctx:
            load_config(self.root)
        self.assertIn("64 KiB", str(ctx.exception))

    def test_whitespace_inside_docs_dir_rejected(self):
        self.write_crux('config_version: "1"\ndocs_dir: "my docs"\n')
        with self.assertRaises(CruxConfigError) as ctx:
            load_config(self.root)
        self.assertIn("whitespace", str(ctx.exception))

    def test_denylist_is_casefolded(self):
        # macOS/Windows filesystems are case-insensitive: .GITHUB IS .github.
        for docs_dir in (".GIT/docs", ".GitHub/x"):
            with self.subTest(docs_dir=docs_dir):
                self.write_crux(f'config_version: "1"\ndocs_dir: {docs_dir}\n')
                with self.assertRaises(CruxConfigError) as ctx:
                    load_config(self.root)
                self.assertIn("execution-adjacent", str(ctx.exception))

    def test_extended_denylist_first_segments_rejected(self):
        for docs_dir in (
            ".claude/x", ".vscode/x", ".husky/x", ".githooks/x", ".idea/x", ".cursor/x",
        ):
            with self.subTest(docs_dir=docs_dir):
                self.write_crux(f'config_version: "1"\ndocs_dir: {docs_dir}\n')
                with self.assertRaises(CruxConfigError) as ctx:
                    load_config(self.root)
                self.assertIn("execution-adjacent", str(ctx.exception))

    def test_empty_crux_file_rejected(self):
        # An empty committed .crux is a malformed config (missing
        # config_version), not an absent one — fail loud, never defaults.
        self.write_crux("")
        with self.assertRaises(CruxConfigError) as ctx:
            load_config(self.root)
        self.assertIn("config_version", str(ctx.exception))

    def test_prefixed_rejects_already_prefixed_id(self):
        cfg = load_config(self.root)  # defaults
        with self.assertRaises(ValueError):
            cfg.prefixed("CRX-PB-0040")

    def test_prefixed_rejects_non_bare_input(self):
        cfg = load_config(self.root)  # defaults
        with self.assertRaises(ValueError):
            cfg.prefixed("X-1")


# ───────────────────────────── template pin ──────────────────────────────────


class TestTemplatePin(CruxConfigTestCase):
    def test_dot_crux_template_loads_verbatim(self):
        """The distributed dot-crux.tmpl, dropped as-is at a repo root, MUST
        load cleanly with the documented default values — catches template ↔
        loader drift (e.g. a comment tripping the alias-bomb guard)."""
        tmpl = (TEMPLATES_DIR / "dot-crux.tmpl").read_text(encoding="utf-8")
        (self.root / ".crux").write_text(tmpl, encoding="utf-8")
        (self.root / "docs").mkdir()  # the docs dir the template references
        cfg = load_config(self.root)
        self.assertEqual(cfg.config_version, "1")
        self.assertEqual(cfg.docs_dir, "docs")
        self.assertEqual(cfg.artifact_prefix, "")
        self.assertEqual(cfg.source, ".crux")
        self.assertEqual(cfg.docs_root, (self.root / "docs").resolve())


# ───────────────────────────── artifact_prefix ───────────────────────────────


class TestArtifactPrefix(CruxConfigTestCase):
    REJECTED = ["crx", "C", "TOOLONGPREFIX11", "PB", "ADR", "RUN", "BRIEF", "A-B"]
    ACCEPTED = ["CRX", "B2", "A1234567X9"]

    def _load_with_prefix(self, prefix: str):
        self.write_crux(f'config_version: "1"\nartifact_prefix: "{prefix}"\n')
        return load_config(self.root)

    def test_rejected_prefixes(self):
        for prefix in self.REJECTED:
            with self.subTest(prefix=prefix):
                self.write_crux(f'config_version: "1"\nartifact_prefix: "{prefix}"\n')
                with self.assertRaises(CruxConfigError):
                    load_config(self.root)

    def test_accepted_prefixes(self):
        for prefix in self.ACCEPTED:
            with self.subTest(prefix=prefix):
                cfg = self._load_with_prefix(prefix)
                self.assertEqual(cfg.artifact_prefix, prefix)

    def test_absent_prefix_is_blank(self):
        self.write_crux('config_version: "1"\n')
        self.assertEqual(load_config(self.root).artifact_prefix, "")

    def test_explicit_empty_prefix_is_blank(self):
        cfg = self._load_with_prefix("")
        self.assertEqual(cfg.artifact_prefix, "")


# ───────────────────────────────── CLI ───────────────────────────────────────


class TestCLI(CruxConfigTestCase):
    def run_cli(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
            timeout=60,
        )

    def test_absent_file_exits_zero_with_defaults_json(self):
        result = self.run_cli(cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["source"], "discovery:bionic")
        self.assertEqual(payload["config_version"], "1")
        self.assertEqual(payload["docs_dir"], "bionic")
        self.assertEqual(payload["artifact_prefix"], "")
        self.assertEqual(payload["docs_root"], str((self.root / "bionic").resolve()))

    def test_valid_file_exits_zero_with_matching_json(self):
        self.write_crux(
            'config_version: "1"\n'
            "docs_dir: documentation\n"
            "artifact_prefix: CRX\n"
        )
        result = self.run_cli("--repo-root", str(self.root))
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["source"], ".crux")
        self.assertEqual(payload["docs_dir"], "documentation")
        self.assertEqual(payload["artifact_prefix"], "CRX")
        self.assertEqual(
            payload["docs_root"], str((self.root / "documentation").resolve())
        )

    def test_invalid_file_exits_one_with_error_json(self):
        self.write_crux('config_version: "1"\ndocs_dir: "/abs"\n')
        result = self.run_cli("--repo-root", str(self.root))
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertIn("error", payload)
        self.assertTrue(payload["error"])
        # The message must be actionable: it names the offending key and the
        # repo-root-relative rule, not just "invalid".
        self.assertTrue(
            "docs_dir" in payload["error"] or "repo-root-relative" in payload["error"],
            payload["error"],
        )


# ─────────────────────── schema widening regression ──────────────────────────


@unittest.skipUnless(
    HAVE_PYYAML, "requires PyYAML for the strict-YAML subprocess consumers"
)
class TestSchemaWideningRegression(CruxConfigTestCase):
    """ADR-0032 §3: the PB- id patterns widened to accept a validated prefix
    (`CRX-PB-0001`) while the negative lookahead rejects reserved-token
    prefixes (`PB-PB-0001`)."""

    def _book_with_id(self, new_id: str) -> Path:
        fixture = (FIXTURES / "promptbook-valid.yaml").read_text(encoding="utf-8")
        rewritten = fixture.replace("id: PB-9001", f"id: {new_id}", 1)
        self.assertNotEqual(rewritten, fixture, "fixture id line not found")
        target = self.root / "book.yaml"
        target.write_text(rewritten, encoding="utf-8")
        return target

    def _book_with_forked_from(self, forked_from: str) -> Path:
        fixture = (FIXTURES / "promptbook-valid.yaml").read_text(encoding="utf-8")
        rewritten = fixture.replace(
            "forked_from: null", f"forked_from: {forked_from}", 1
        )
        self.assertNotEqual(rewritten, fixture, "fixture forked_from line not found")
        target = self.root / "book.yaml"
        target.write_text(rewritten, encoding="utf-8")
        return target

    def _run_with_book_id(self, new_id: str) -> Path:
        fixture = (FIXTURES / "run-valid.yaml").read_text(encoding="utf-8")
        rewritten = fixture.replace("book_id: PB-9001", f"book_id: {new_id}", 1)
        self.assertNotEqual(rewritten, fixture, "fixture book_id line not found")
        target = self.root / "run.yaml"
        target.write_text(rewritten, encoding="utf-8")
        return target

    def _validate(self, path: Path, kind: str = "promptbook") -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(VALIDATE_PROMPTBOOK), "--kind", kind, str(path)],
            capture_output=True,
            text=True,
            cwd=str(SCRIPTS_DIR),
            timeout=60,
        )

    def test_prefixed_id_validates_clean(self):
        proc = self._validate(self._book_with_id("CRX-PB-0001"))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_reserved_token_prefix_id_rejected(self):
        for reserved in ("PB", "ADR", "RUN", "BRIEF"):
            with self.subTest(reserved=reserved):
                proc = self._validate(self._book_with_id(f"{reserved}-PB-0001"))
                self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                payload = json.loads(proc.stdout)
                self.assertTrue(payload.get("errors"))
                # The finding must point at the id field's pattern rule, not
                # an unrelated cascade error.
                id_errors = [
                    e for e in payload["errors"]
                    if "id" in e.get("instance_path", "")
                ]
                self.assertTrue(id_errors, payload["errors"])
                self.assertTrue(
                    any(
                        "pattern" in e.get("schema_path", "")
                        or "pattern" in e.get("error", "")
                        for e in id_errors
                    ),
                    id_errors,
                )

    def test_prefixed_book_id_in_run_validates_clean(self):
        # run.schema.json's book_id widened in lock-step with the book's id.
        proc = self._validate(self._run_with_book_id("CRX-PB-9001"), kind="run")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_reserved_token_book_id_in_run_rejected(self):
        proc = self._validate(self._run_with_book_id("PB-PB-0001"), kind="run")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload.get("errors"))

    def test_prefixed_forked_from_validates_clean(self):
        # forked_from carries the same widened PB-id pattern.
        proc = self._validate(self._book_with_forked_from("CRX-PB-0001"))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


# ───────────── prefixed() ↔ widened schema pattern cross-check ────────────────


class TestPrefixedMatchesSchemaPattern(CruxConfigTestCase):
    """Every id minted by cfg.prefixed() under a valid prefix MUST satisfy the
    widened id pattern in promptbook.schema.json — the pattern is read from the
    schema file so the test catches loader ↔ schema divergence."""

    def test_prefixed_output_matches_schema_id_pattern(self):
        schema = json.loads(
            (SCHEMAS_DIR / "promptbook.schema.json").read_text(encoding="utf-8")
        )
        pattern = schema["properties"]["id"]["pattern"]
        for prefix in ("CRX", "B2", "A1234567X9"):
            with self.subTest(prefix=prefix):
                self.write_crux(f'config_version: "1"\nartifact_prefix: "{prefix}"\n')
                cfg = load_config(self.root)
                self.assertRegex(cfg.prefixed("PB-0001"), pattern)


# ───────────────────────── consumer wiring (ADR-0032) ────────────────────────
#
# Subprocess-level tests (cwd = a temp repo) proving retained script consumers
# actually honor the repo-root .crux: relocated docs_dir resolution, fail-loud
# on a malformed .crux, and the explicit-flag precedence rules. Subprocess
# invocation sidesteps the per-process resolve-once caches entirely
# (see test_visualize_run_progress.CliTests.setUp for the in-process reset
# precedent these tests deliberately avoid needing).

_MALFORMED_CRUX = 'config_version: "1"\ndocs_dir: "/abs"\n'
_MANIFEST_OK_NO_EXTRACTORS = "schema_version: 4\ncode:\n  extractors:\n"
_MANIFEST_NO_SCHEMA_VERSION = "code:\n  extractors:\n"


def _run_script(script: Path, *args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        timeout=60,
    )


class TestExtractCodeDocsWiring(CruxConfigTestCase):
    def _repo(self, manifest_body: str, crux_body: str | None = None) -> Path:
        docs = self.root / "documentation"
        docs.mkdir(exist_ok=True)
        (docs / "manifest.yml").write_text(manifest_body, encoding="utf-8")
        self.write_crux(crux_body or 'config_version: "1"\ndocs_dir: documentation\n')
        return self.root

    def test_dry_run_finds_relocated_manifest(self):
        repo = self._repo(_MANIFEST_OK_NO_EXTRACTORS)
        proc = _run_script(EXTRACT_CODE_DOCS, "--dry-run", cwd=repo)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        # Reaching "no extractors configured" proves the relocated manifest
        # was found and read (a missing manifest exits 1 with "config not found").
        self.assertIn("no extractors configured", proc.stderr)

    def test_manifest_without_schema_version_refused(self):
        repo = self._repo(_MANIFEST_NO_SCHEMA_VERSION)
        proc = _run_script(EXTRACT_CODE_DOCS, "--dry-run", cwd=repo)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("no readable schema_version", proc.stderr)

    def test_malformed_crux_fails_loud(self):
        repo = self._repo(_MANIFEST_OK_NO_EXTRACTORS, crux_body=_MALFORMED_CRUX)
        proc = _run_script(EXTRACT_CODE_DOCS, "--dry-run", cwd=repo)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn(".crux configuration error", proc.stderr)

    def test_explicit_config_and_output_dir_never_load_crux(self):
        # Both flags explicit -> .crux is never read, so a malformed one is
        # irrelevant (the consumer-enforced precedence rule).
        repo = self._repo(_MANIFEST_OK_NO_EXTRACTORS, crux_body=_MALFORMED_CRUX)
        proc = _run_script(
            EXTRACT_CODE_DOCS,
            "--config", str(repo / "documentation" / "manifest.yml"),
            "--output-dir", str(repo / "documentation" / "code"),
            "--dry-run",
            cwd=repo,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_explicit_config_anchors_repo_root_not_cwd(self):
        # cwd's valid .crux fills --output-dir, but an explicit --config must
        # anchor repo_root on ITS repo (the foreign repo's sources get scanned).
        repo_a = self._repo(_MANIFEST_OK_NO_EXTRACTORS)
        repo_b_td = tempfile.TemporaryDirectory(prefix="crux-config-repo-b-")
        self.addCleanup(repo_b_td.cleanup)
        repo_b = Path(repo_b_td.name)
        (repo_b / "docs").mkdir()
        (repo_b / "docs" / "manifest.yml").write_text(
            "schema_version: 4\n"
            "code:\n"
            "  extractors:\n"
            "    fallback:\n"
            "      extractor: fallback\n"
            '      glob: "src/*.py"\n',
            encoding="utf-8",
        )
        proc = _run_script(
            EXTRACT_CODE_DOCS,
            "--config", str(repo_b / "docs" / "manifest.yml"),
            "--dry-run", "--verbose",
            cwd=repo_a,
        )
        # ADR-mandated (ADR-0131 clause 14 D4): drift means "a write would change
        # a byte". repo_b has no code/ output yet, so a write would create
        # index.md and _meta/manifest.json: the dry run reports drift and exits
        # 1, where it used to exit 0 on an empty manifest diff. The assertion this
        # test exists for is the repo_root one below.
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["drift"])
        self.assertTrue(payload["index_drift"])
        self.assertIn(f"repo_root={repo_b.resolve()}", proc.stderr)
        self.assertNotIn(f"repo_root={repo_a.resolve()}", proc.stderr)


@unittest.skipUnless(
    HAVE_PYYAML, "requires PyYAML for the strict-YAML subprocess consumers"
)
class TestVisualizeRunProgressWiring(CruxConfigTestCase):
    """Reuses the _BOOK/_RUN fixtures from test_visualize_run_progress (loaded
    by path so the import works under any unittest invocation layout).

    Explicitly PyYAML-guarded (SHOULD, Prompt 11): visualize-run-progress is a
    strict-YAML subprocess consumer (exit 2 without a real parser). The class
    formerly relied only on the sibling test_visualize_run_progress module
    raising SkipTest at import time — a fragile transitive side-effect. This
    decorator makes the skip self-evident and independent of that import.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_tvrp_fixtures", Path(__file__).resolve().parent / "test_visualize_run_progress.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls.BOOK = mod._BOOK
        cls.RUN = mod._run_text(mod._BOOK_HASH)

    def setUp(self):
        super().setUp()
        active = self.root / "documentation" / "promptbooks" / "active"
        runs = self.root / "documentation" / "promptbooks" / "runs" / "PB-9001-test"
        active.mkdir(parents=True)
        runs.mkdir(parents=True)
        (active / "PB-9001-test.yaml").write_text(self.BOOK, encoding="utf-8")
        (runs / "run-RUN-001.yaml").write_text(self.RUN, encoding="utf-8")
        self.write_crux('config_version: "1"\ndocs_dir: documentation\n')

    def test_pb_id_resolves_under_relocated_docs_dir(self):
        proc = _run_script(
            VISUALIZE_RUN_PROGRESS, "PB-9001", "--no-color", cwd=self.root
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PB-9001", proc.stdout)
        self.assertIn("RUN-001", proc.stdout)

    def test_malformed_crux_exits_one_via_progress_error(self):
        self.write_crux(_MALFORMED_CRUX)
        proc = _run_script(
            VISUALIZE_RUN_PROGRESS, "PB-9001", "--no-color", cwd=self.root
        )
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn(".crux configuration error", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_historical_markdown_progress_reads_relocated_tree_without_rewrite(self):
        active = self.root / "documentation" / "promptbooks" / "active"
        runs = self.root / "documentation" / "promptbooks" / "runs" / "PB-9001-test"
        (active / "PB-9001-test.yaml").unlink()
        (runs / "run-RUN-001.yaml").unlink()
        archive = self.root / "documentation" / "promptbooks" / "archive"
        archive.mkdir()
        book = archive / "PB-9001-test.md"
        run = runs / "run-RUN-001.md"
        book.write_bytes((FIXTURES / "legacy-book.md").read_bytes())
        run.write_bytes((FIXTURES / "legacy-run.md").read_bytes())
        before = book.read_bytes(), run.read_bytes()
        proc = _run_script(
            VISUALIZE_RUN_PROGRESS, "PB-9001", "--no-color", cwd=self.root
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("RUN-001", proc.stdout)
        self.assertEqual((book.read_bytes(), run.read_bytes()), before)




if __name__ == "__main__":
    unittest.main()
