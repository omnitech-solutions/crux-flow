"""Tests for check-claude-compat.py — the measured compatibility verdict.

The defect this exists to prevent: a check that reports a version number and calls
it compatibility. A host can be on a new enough version and still load none of the
repository's instructions, because the fallback is suppressed by a Claude-named
file, disabled by the mode, or absent on the distribution.

The verdict must therefore name the effective instruction files and the inputs it
derived that from. These tests pin the four ways it can be wrong.
"""
from __future__ import annotations

import importlib.util
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "crux" / "scripts" / "check-claude-compat.py"

_spec = importlib.util.spec_from_file_location("check_claude_compat", SCRIPT)
cc = importlib.util.module_from_spec(_spec)
sys.modules["check_claude_compat"] = cc
_spec.loader.exec_module(cc)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class CompatFixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.home = self.root / "_home"
        self.home.mkdir()
        _write(self.root / "AGENTS.md", "canonical\n")

    def verdict(self, **kw):
        kw.setdefault("version", "2.1.278 (Claude Code)")
        kw.setdefault("distribution", "anthropic")
        kw.setdefault("home", self.home)
        wd = kw.pop("working_dir", self.root)
        return cc.evaluate(self.root, wd, **kw)

    def settings(self, payload: dict):
        _write(self.home / "settings.json", json.dumps(payload))


class SupportedTests(CompatFixture):
    def test_a_clean_host_on_the_default_mode_is_supported(self):
        v = self.verdict()
        self.assertTrue(v["supported"], v["reasons"])
        self.assertTrue(v["effective_instruction_files"])

    def test_the_verdict_names_the_inputs_it_was_derived_from(self):
        v = self.verdict()
        d = v["derived_from"]
        for key in ("version", "version_floor", "distribution", "builtin_disabled",
                    "mode", "mode_source", "mode_setting_exposed", "working_dir"):
            self.assertIn(key, d)
        self.assertEqual(d["version_authority"], "the v2.1.277 release changelog")

    def test_an_unexposed_setting_is_reported_as_the_default_not_as_observed(self):
        v = self.verdict()
        self.assertFalse(v["derived_from"]["mode_setting_exposed"])
        self.assertIn("default", v["derived_from"]["mode_source"])

    def test_an_exposed_setting_is_reported_with_its_source_file(self):
        self.settings({"pluginConfigs": {"agents-md@builtin": {
            "options": {"instructionFiles": "claude-md-and-agents-md"}}}})
        v = self.verdict()
        self.assertTrue(v["derived_from"]["mode_setting_exposed"])
        self.assertIn("settings.json", v["derived_from"]["mode_source"])


class VersionAndDistributionTests(CompatFixture):
    def test_a_version_below_the_floor_is_unsupported(self):
        v = self.verdict(version="2.1.276")
        self.assertFalse(v["supported"])
        self.assertTrue(any("2.1.277" in r for r in v["reasons"]))

    def test_the_floor_itself_is_supported(self):
        self.assertTrue(self.verdict(version="2.1.277")["supported"])

    def test_an_undetectable_version_is_unsupported_rather_than_assumed(self):
        v = self.verdict(version="no version here")
        self.assertFalse(v["supported"])
        self.assertTrue(any("unverified" in r for r in v["reasons"]))

    def test_each_excluded_distribution_is_unsupported(self):
        for dist in ("bedrock", "vertex", "foundry"):
            with self.subTest(dist=dist):
                v = self.verdict(distribution=dist)
                self.assertFalse(v["supported"])
                self.assertTrue(any(dist in r for r in v["reasons"]))

    def test_the_changelog_is_named_as_the_authority_for_the_exclusion(self):
        v = self.verdict(distribution="bedrock")
        self.assertTrue(any("changelog is the authority" in r for r in v["reasons"]))


class ModeTests(CompatFixture):
    def test_claude_md_mode_never_loads_agents_md(self):
        self.settings({"pluginConfigs": {"agents-md@builtin": {
            "options": {"instructionFiles": "claude-md"}}}})
        v = self.verdict()
        self.assertFalse(v["supported"])
        self.assertTrue(v["adapter_applies"])

    def test_managed_only_is_unsupported_and_the_adapter_does_not_repair_it(self):
        self.settings({"pluginConfigs": {"agents-md@builtin": {
            "options": {"instructionFiles": "managed-only"}}}})
        v = self.verdict()
        self.assertFalse(v["supported"])
        self.assertFalse(v["adapter_applies"],
                         "managed-only drops private files too; only config repairs it")
        self.assertTrue(any("configuration change" in r for r in v["reasons"]))

    def test_the_legacy_project_instructions_key_maps_onto_the_four_modes(self):
        for legacy, mode in cc.LEGACY_MODE_MAP.items():
            with self.subTest(legacy=legacy):
                self.settings({"pluginConfigs": {"agents-md@builtin": {
                    "options": {"projectInstructions": legacy}}}})
                self.assertEqual(self.verdict()["derived_from"]["mode"], mode)

    def test_a_disabled_builtin_is_unsupported(self):
        self.settings({"disabledPlugins": ["agents-md@builtin"]})
        v = self.verdict()
        self.assertFalse(v["supported"])
        self.assertTrue(any("disabled" in r for r in v["reasons"]))

    def test_an_unrecognised_mode_is_refused_rather_than_defaulted(self):
        self.settings({"pluginConfigs": {"agents-md@builtin": {
            "options": {"instructionFiles": "something-new"}}}})
        v = self.verdict()
        self.assertFalse(v["supported"])
        self.assertTrue(any("unrecognised" in r for r in v["reasons"]))


class SuppressorTests(CompatFixture):
    def test_an_on_chain_suppressor_fails_the_default_mode(self):
        _write(self.root / "CLAUDE.md", "legacy\n")
        v = self.verdict()
        self.assertFalse(v["supported"])
        self.assertTrue(any("suppressed" in r for r in v["reasons"]))
        self.assertTrue(v["suppressors"]["on_chain"])

    def test_the_same_suppressor_does_not_fail_the_and_mode(self):
        """Both names load under claude-md-and-agents-md, so nothing is suppressed."""
        _write(self.root / "CLAUDE.md", "legacy\n")
        self.settings({"pluginConfigs": {"agents-md@builtin": {
            "options": {"instructionFiles": "claude-md-and-agents-md"}}}})
        v = self.verdict()
        self.assertTrue(v["supported"], v["reasons"])
        self.assertTrue(v["suppressors"]["on_chain"],
                        "it is still REPORTED; reporting and failing are two acts")

    def test_an_off_chain_suppressor_is_reported_and_fails_nothing(self):
        _write(self.root / "sub" / "CLAUDE.local.md", "private\n")
        v = self.verdict(working_dir=self.root)
        self.assertTrue(v["supported"], v["reasons"])
        self.assertTrue(v["suppressors"]["off_chain"])
        self.assertFalse(v["suppressors"]["on_chain"])

    def test_the_same_file_fails_once_the_working_directory_moves_onto_its_chain(self):
        """A private override silences the tree for anyone working inside it."""
        _write(self.root / "sub" / "CLAUDE.local.md", "private\n")
        v = self.verdict(working_dir=self.root / "sub")
        self.assertFalse(v["supported"])
        self.assertTrue(v["suppressors"]["on_chain"])

    def test_crux_never_claims_it_will_touch_a_private_override(self):
        """The remedy may say the file is the user's to delete. It may not say
        crux deletes it, and nothing may propose that crux does."""
        _write(self.root / "CLAUDE.local.md", "private\n")
        v = self.verdict()
        sup = [s for s in v["suppressors"]["on_chain"] + v["suppressors"]["off_chain"]
               if s["path"].endswith("CLAUDE.local.md")]
        self.assertEqual(len(sup), 1, "positive control: the override was reported")
        remedy = sup[0]["remedy"]
        self.assertIn("yours", remedy, "the action is attributed to the user")
        for claim in ("crux will", "we delete", "will be deleted", "automatically"):
            self.assertNotIn(claim, json.dumps(v).lower())
        self.assertTrue((self.root / "CLAUDE.local.md").is_file(),
                        "reporting must not have touched it")


class EntryPointTests(CompatFixture):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--repo-root", str(self.root),
             "--claude-home", str(self.home), *args],
            capture_output=True, text=True)

    def test_supported_exits_zero_with_json(self):
        r = self._run("--claude-version", "2.1.278", "--distribution", "anthropic")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(json.loads(r.stdout)["supported"])

    def test_unsupported_exits_one_with_json(self):
        r = self._run("--claude-version", "2.1.100", "--distribution", "anthropic")
        self.assertEqual(r.returncode, 1)
        payload = json.loads(r.stdout)
        self.assertFalse(payload["supported"])
        self.assertTrue(payload["reasons"])

    def test_a_missing_repo_root_is_a_capability_error(self):
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo-root", str(self.root / "nope")],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout.strip(), "")
        self.assertTrue(r.stderr.strip())

    def test_the_script_declares_no_third_party_dependencies(self):
        head = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("# /// script", head)
        self.assertIn("dependencies = []", head)



# --------------------------------------------- settings precedence (review S3)


class SettingsPrecedenceTests(CompatFixture):
    """Claude Code resolves local > project > user.

    Review found this inverted and untested: every existing test wrote to `home`
    only, so a user-first read that returned on the first hit passed them all while
    reporting a mode the host does not use.
    """

    def _project(self, payload, local=False):
        name = "settings.local.json" if local else "settings.json"
        _write(self.root / ".claude" / name, json.dumps(payload))

    @staticmethod
    def _mode(value):
        return {"pluginConfigs": {"agents-md@builtin":
                                  {"options": {"instructionFiles": value}}}}

    def test_a_project_setting_beats_the_user_setting(self):
        self.settings(self._mode("claude-md-and-agents-md"))
        self._project(self._mode("claude-md"))
        v = self.verdict()
        self.assertEqual(v["derived_from"]["mode"], "claude-md")
        self.assertIn(".claude/settings.json", v["derived_from"]["mode_source"])

    def test_a_local_setting_beats_the_project_setting(self):
        self._project(self._mode("claude-md"))
        self._project(self._mode("claude-md-and-agents-md"), local=True)
        v = self.verdict()
        self.assertEqual(v["derived_from"]["mode"], "claude-md-and-agents-md")
        self.assertIn("settings.local.json", v["derived_from"]["mode_source"])

    def test_the_user_setting_still_applies_when_no_project_setting_exists(self):
        """Positive control: precedence did not simply disable the user source."""
        self.settings(self._mode("claude-md"))
        v = self.verdict()
        self.assertEqual(v["derived_from"]["mode"], "claude-md")

    def test_precedence_holds_for_the_builtin_disabled_flag_too(self):
        self.settings({"disabledPlugins": ["agents-md@builtin"]})
        self._project({"enabledPlugins": ["agents-md@builtin"]}, local=True)
        self.assertFalse(self.verdict()["derived_from"]["builtin_disabled"])


class EffectiveFileTests(CompatFixture):
    """Clause 10 names the effective files. Naming one it never stats is inference."""

    def test_only_files_that_exist_are_named(self):
        v = self.verdict()
        for rel in v["effective_instruction_files"]:
            self.assertTrue((self.root / rel).is_file(), rel)

    def test_the_canonical_file_is_named_when_it_exists(self):
        """Positive control for the assertion above."""
        self.assertIn("AGENTS.md", self.verdict()["effective_instruction_files"])


# ----------------------------------------------------- the adapter (review M4)


class AdapterAuditTests(CompatFixture):
    """Clause 11's three reports, which review found asserted in prose and
    implemented nowhere."""

    def setUp(self):
        super().setUp()
        import importlib.util as ilu
        spec = ilu.spec_from_file_location(
            "claude_adapter", REPO_ROOT / "crux" / "scripts" / "claude_adapter.py")
        self.ad = ilu.module_from_spec(spec)
        # Register BEFORE exec: @dataclass resolves cls.__module__ through
        # sys.modules, and an unregistered module makes that lookup return None.
        sys.modules["claude_adapter"] = self.ad
        spec.loader.exec_module(self.ad)
        _write(self.root / "bionic" / "AGENTS.md", "tree schema\n")
        self.tracked = ["AGENTS.md", "bionic/AGENTS.md"]

    def test_a_generated_adapter_carries_its_source_and_digest(self):
        path = self.ad.generate(self.root, ".")
        text = path.read_text(encoding="utf-8")
        self.assertTrue(self.ad.is_adapter(text))
        self.assertEqual(len(self.ad.recorded_digest(text)), 64)
        self.assertIn("canonical", text, "the source content is carried through")

    def test_a_current_adapter_is_reported_current_on_an_unsupported_host(self):
        self.ad.generate(self.root, ".")
        self.ad.generate(self.root, "bionic")
        a = self.ad.audit(self.root, self.tracked, host_supported=False)
        self.assertEqual({r.status for r in a.adapters}, {"current"})
        self.assertTrue(a.clean(), a.findings)

    def test_an_adapter_whose_source_moved_is_reported_stale(self):
        self.ad.generate(self.root, ".")
        self.ad.generate(self.root, "bionic")
        _write(self.root / "AGENTS.md", "canonical, edited\n")
        a = self.ad.audit(self.root, self.tracked, host_supported=False)
        stale = [r for r in a.adapters if r.status == "stale"]
        self.assertEqual([r.scope for r in stale], ["."])
        self.assertTrue(any("STALE" in f for f in a.findings))

    def test_an_adapter_on_a_supported_host_is_reported_removable(self):
        self.ad.generate(self.root, ".")
        self.ad.generate(self.root, "bionic")
        a = self.ad.audit(self.root, self.tracked, host_supported=True)
        self.assertEqual({r.status for r in a.adapters}, {"removable"})
        self.assertTrue(any("REMOVABLE" in f for f in a.findings))

    def test_a_root_only_adapter_is_reported_incomplete(self):
        """The hazard: a root adapter suppresses every AGENTS.md beneath it."""
        self.ad.generate(self.root, ".")
        a = self.ad.audit(self.root, self.tracked, host_supported=False)
        self.assertEqual(a.incomplete, ["bionic"])
        self.assertTrue(any("INCOMPLETE" in f for f in a.findings))

    def test_adapters_at_every_scope_are_not_incomplete(self):
        """Positive control for the finding above."""
        self.ad.generate(self.root, ".")
        self.ad.generate(self.root, "bionic")
        a = self.ad.audit(self.root, self.tracked, host_supported=False)
        self.assertEqual(a.incomplete, [])

    def test_a_legacy_claude_md_is_not_mistaken_for_an_adapter(self):
        """A hand-written CLAUDE.md carries no generated header; it belongs to the
        migration, not to this audit."""
        _write(self.root / "CLAUDE.md", "# CLAUDE.md\n\nhand written\n")
        a = self.ad.audit(self.root, self.tracked, host_supported=True)
        self.assertEqual(a.adapters, [])
        self.assertEqual(a.findings, [])

    def test_an_adapter_whose_source_vanished_is_reported_orphan(self):
        self.ad.generate(self.root, "bionic")
        (self.root / "bionic" / "AGENTS.md").unlink()
        a = self.ad.audit(self.root, self.tracked, host_supported=False)
        self.assertEqual([r.status for r in a.adapters], ["orphan"])

    def test_the_audit_never_writes_or_deletes(self):
        self.ad.generate(self.root, ".")
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.ad.audit(self.root, self.tracked, host_supported=True)
        after = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_the_verdict_carries_the_adapter_report(self):
        self.ad.generate(self.root, ".")
        v = self.verdict()
        self.assertIn("adapters", v)
        self.assertTrue(v["adapters"]["findings"],
                        "a root-only adapter on a supported host owes findings")


# ------------------------------------------------ the missing canonical file (B1)


AND_MODE = {"pluginConfigs": {"agents-md@builtin": {
    "options": {"instructionFiles": "claude-md-and-agents-md"}}}}


NEW_REMEDY_CHK = ("create an `AGENTS.md` spelled exactly so. `init-docs` creates one "
                  "only while it initialises a tree in a root that holds no "
                  "`AGENTS.md` or `CLAUDE.md` in any letter case. A `CLAUDE.local.md` "
                  "does not block that create.")
NEW_REMEDY_REASON = ("create an AGENTS.md spelled exactly so (init-docs creates one only "
                     "while it initialises a tree in a root that holds no AGENTS.md or "
                     "CLAUDE.md in any letter case, and a CLAUDE.local.md does not block "
                     "that create)")
# The previous remedy sent a user with an initialised tree to rerun init-docs, which
# refuses an existing tree without --force, a destructive re-bootstrap.
RERUN_REMEDY = "or run init-docs on a root"


def _states_the_stale_claim(text: str) -> bool:
    """True when the text says init-docs never creates a root AGENTS.md. It does
    create one when the root holds no AGENTS.md or CLAUDE.md in any letter case."""
    flat = " ".join(text.replace("`", "").split()).lower()
    return "never creates a root agents.md" in flat


class AuditMissingFileWordingTests(unittest.TestCase):
    """CHK-INSTR-5 states the missing-file cause as the script's reason does."""

    def test_the_audit_names_the_and_mode_claude_md_and_a_root_agents_md(self):
        text = (REPO_ROOT / "crux" / "skills" / "audit-docs" / "SKILL.md"
                ).read_text(encoding="utf-8")
        chk = " ".join(text.split("**CHK-INSTR-5**", 1)[1].split("- **CHK-", 1)[0].split())
        self.assertIn("no `AGENTS.md` on the chain from the root to the working "
                      "directory (and, under `claude-md-and-agents-md`, no `CLAUDE.md`)", chk)
        self.assertIn(NEW_REMEDY_CHK, chk)
        self.assertFalse(_states_the_stale_claim(chk), chk)
        self.assertNotIn(RERUN_REMEDY, chk.replace("`", ""))

    def test_the_stale_claim_detector_flags_the_old_sentence(self):
        """Positive control: the detector is not vacuous. It flags the sentence
        the audit and the reason carried before init-docs began creating a root
        file, and passes the current wording."""
        old = ("Its remedy is to create an `AGENTS.md` (`init-docs` never creates a "
               "root `AGENTS.md`) and never the adapter")
        self.assertTrue(_states_the_stale_claim(old))
        self.assertFalse(_states_the_stale_claim(NEW_REMEDY_CHK))

    def test_the_rerun_remedy_probe_matches_the_wording_it_replaced(self):
        """Positive control: the rerun-remedy absence checks can see that wording."""
        previous = ("create an AGENTS.md spelled exactly so, or run init-docs on a "
                    "root that holds no instruction file in any letter case")
        self.assertIn(RERUN_REMEDY, previous)
        self.assertNotIn(RERUN_REMEDY, NEW_REMEDY_REASON)


class NoCanonicalFileTests(CompatFixture):
    """Clause 10: a verdict names the files the host loads, and the absence of an
    error is not that. A checkout with no AGENTS.md on the chain loads none of the
    tree's instructions, whatever the host's version and mode."""

    def setUp(self):
        super().setUp()
        (self.root / "AGENTS.md").unlink()

    def test_no_agents_md_on_the_chain_is_not_supported(self):
        v = self.verdict()
        self.assertFalse(v["supported"], v)
        self.assertEqual(v["effective_instruction_files"], [],
                         "the measured list stays measured; nothing is loaded")
        missing = [r for r in v["reasons"] if "AGENTS.md" in r]
        self.assertEqual(len(missing), 1, v["reasons"])
        self.assertIn("no AGENTS.md", missing[0])
        self.assertIn("create", missing[0])

    def test_the_missing_file_reason_names_the_and_mode_claude_md_and_a_root_agents_md(self):
        """The reason names every file whose presence would satisfy the check, and
        its remedy is an exactly spelled AGENTS.md. It says when init-docs creates
        one, and never sends the user to rerun init-docs, which refuses an existing
        tree without the destructive --force. It never claims init-docs creates no
        root file, because it does when the root holds none in any letter case."""
        v = self.verdict()
        missing = [r for r in v["reasons"] if "AGENTS.md" in r]
        self.assertEqual(len(missing), 1, v["reasons"])
        self.assertIn("under claude-md-and-agents-md, no CLAUDE.md", missing[0])
        self.assertIn(NEW_REMEDY_REASON, missing[0])
        self.assertFalse(_states_the_stale_claim(missing[0]), missing[0])
        self.assertNotIn(RERUN_REMEDY, missing[0])

    def test_the_missing_file_reason_is_not_an_unsupported_runtime_or_adapter_advice(self):
        v = self.verdict()
        self.assertFalse(v["adapter_applies"], "an adapter cannot repair a missing file")
        self.assertTrue(v["adapters"]["basis_supported_without_adapters"],
                        "the adapter-removal basis reads host causes only")
        text = " ".join(v["reasons"]).lower()
        self.assertIn("agents.md", text, "positive control: the reason was produced")
        self.assertNotIn("adapter", text)
        self.assertNotIn("unsupported runtime", text)

    def test_an_on_chain_suppressor_does_recommend_the_adapter(self):
        """Positive control for the adapter assertions above."""
        _write(self.root / "AGENTS.md", "canonical\n")
        _write(self.root / "CLAUDE.md", "legacy\n")
        v = self.verdict()
        self.assertTrue(v["adapter_applies"])
        self.assertFalse(v["adapters"]["basis_supported_without_adapters"])

    def test_an_agents_md_off_the_chain_does_not_count(self):
        _write(self.root / "sub" / "AGENTS.md", "nested\n")
        v = self.verdict(working_dir=self.root)
        self.assertFalse(v["supported"], v)

    def test_the_root_agents_md_counts_from_a_nested_working_directory(self):
        """Control: the chain runs from the root down to the working directory."""
        _write(self.root / "AGENTS.md", "canonical\n")
        (self.root / "sub").mkdir()
        v = self.verdict(working_dir=self.root / "sub")
        self.assertTrue(v["supported"], v["reasons"])
        self.assertIn("AGENTS.md", v["effective_instruction_files"])

    def test_a_nested_agents_md_counts_from_its_own_working_directory(self):
        _write(self.root / "sub" / "AGENTS.md", "nested\n")
        v = self.verdict(working_dir=self.root / "sub")
        self.assertTrue(v["supported"], v["reasons"])
        self.assertEqual(v["effective_instruction_files"], ["sub/AGENTS.md"])

    def test_a_host_cause_and_a_missing_file_are_both_reported(self):
        """Condition c: the file cause does not hide behind a host cause. The
        effective list stays [] while the host is unsupported (deferred defect)."""
        v = self.verdict(version="2.1.276")
        self.assertFalse(v["supported"])
        self.assertTrue(any("2.1.277" in r for r in v["reasons"]), v["reasons"])
        self.assertTrue(any("no AGENTS.md" in r for r in v["reasons"]), v["reasons"])
        self.assertTrue(v["adapter_applies"], "host causes alone drive this")

    def test_a_host_cause_with_the_file_present_reports_no_file_cause(self):
        """Control for the test above."""
        _write(self.root / "AGENTS.md", "canonical\n")
        v = self.verdict(version="2.1.276")
        self.assertFalse(any("no AGENTS.md" in r for r in v["reasons"]), v["reasons"])

    def test_the_missing_file_is_reported_beside_the_managed_only_mode(self):
        self.settings({"pluginConfigs": {"agents-md@builtin": {
            "options": {"instructionFiles": "managed-only"}}}})
        v = self.verdict()
        self.assertFalse(v["adapter_applies"])
        self.assertTrue(any("no AGENTS.md" in r for r in v["reasons"]))


# ------------------------------------- exact spelling from directory entries


@contextlib.contextmanager
def _folding_filesystem():
    """Make path probes behave as on a case-folding volume while directory
    listings still return the real names. `Path.is_file` and `Path.exists` match
    a name case-insensitively against the parent's entries, as APFS's default
    volume does, so the old probe and the new one differ on Linux and macOS alike."""
    real_is_file, real_exists = Path.is_file, Path.exists

    def _real_name(path: Path):
        try:
            names = os.listdir(path.parent)
        except OSError:
            return None
        for n in names:
            if n.lower() == path.name.lower():
                return path.parent / n
        return None

    def is_file(self, *a, **k):
        hit = _real_name(self)
        return real_is_file(hit, *a, **k) if hit is not None else False

    def exists(self, *a, **k):
        hit = _real_name(self)
        return real_exists(hit, *a, **k) if hit is not None else False

    with mock.patch.object(Path, "is_file", is_file), \
            mock.patch.object(Path, "exists", exists):
        yield


@contextlib.contextmanager
def _case_sensitive_filesystem():
    """Make path probes behave as on a case-sensitive volume, whatever volume the
    test runs on. `Path.is_file` and `Path.exists` hold only when the parent's
    entries carry the name in exactly that spelling, as ext4 does."""
    real_is_file, real_exists = Path.is_file, Path.exists

    def _spelled(path: Path) -> bool:
        try:
            return path.name in os.listdir(path.parent)
        except OSError:
            return False

    def is_file(self, *a, **k):
        return _spelled(self) and real_is_file(self, *a, **k)

    def exists(self, *a, **k):
        return _spelled(self) and real_exists(self, *a, **k)

    with mock.patch.object(Path, "is_file", is_file), \
            mock.patch.object(Path, "exists", exists):
        yield


_BOTH_VOLUMES = (("folding", _folding_filesystem),
                 ("case-sensitive", _case_sensitive_filesystem))


def _volume_folds_case() -> bool:
    d = Path(tempfile.mkdtemp())
    try:
        (d / "probe-lower.md").write_text("x", encoding="utf-8")
        return (d / "PROBE-LOWER.md").exists()
    finally:
        shutil.rmtree(d, ignore_errors=True)


class CaseVariantSpellingTests(CompatFixture):
    """A variant such as `agents.md` or `claude.md` is never an effective file,
    in any mode and on either kind of volume, and an exact `CLAUDE.md` is listed
    once. Whether a host loads a variant is unobserved, so the probe does not
    infer that it does."""

    def setUp(self):
        super().setUp()
        (self.root / "AGENTS.md").unlink()

    def test_the_folding_simulation_makes_the_old_probe_find_the_variant(self):
        """Positive control: under the simulation the path probe the old code used
        reports the variant as AGENTS.md, and the directory listing still names it
        `agents.md`. Without this the tests below prove nothing."""
        _write(self.root / "agents.md", "variant\n")
        with _folding_filesystem():
            self.assertTrue((self.root / "AGENTS.md").is_file())
        self.assertIn("agents.md", os.listdir(self.root))
        self.assertNotIn("AGENTS.md", os.listdir(self.root))

    def test_a_variant_alone_is_not_effective_and_is_unsupported(self):
        _write(self.root / "agents.md", "variant\n")
        with _folding_filesystem():
            v = self.verdict()
        self.assertFalse(v["supported"], v)
        self.assertEqual(v["effective_instruction_files"], [])
        self.assertTrue(any("no AGENTS.md" in r for r in v["reasons"]), v["reasons"])

    def test_a_mixed_case_variant_alone_is_not_effective(self):
        _write(self.root / "Agents.md", "variant\n")
        with _folding_filesystem():
            v = self.verdict()
        self.assertFalse(v["supported"], v)
        self.assertEqual(v["effective_instruction_files"], [])

    def test_the_exact_spelling_is_still_effective_under_folding(self):
        """Control: the folding simulation does not hide a real AGENTS.md."""
        _write(self.root / "AGENTS.md", "canonical\n")
        with _folding_filesystem():
            v = self.verdict()
        self.assertTrue(v["supported"], v["reasons"])
        self.assertEqual(v["effective_instruction_files"], ["AGENTS.md"])

    def test_a_variant_in_a_nested_directory_is_not_effective_either(self):
        _write(self.root / "AGENTS.md", "canonical\n")
        _write(self.root / "sub" / "agents.md", "variant\n")
        with _folding_filesystem():
            v = self.verdict(working_dir=self.root / "sub")
        self.assertEqual(v["effective_instruction_files"], ["AGENTS.md"])

    def test_a_lone_lowercase_claude_md_is_not_effective_in_the_and_mode(self):
        """On a case-sensitive volume the old path probe never found `claude.md`;
        counting it would infer a load no one observed and report supported."""
        _write(self.root / "claude.md", "legacy\n")
        self.settings(AND_MODE)
        for label, volume in _BOTH_VOLUMES:
            with self.subTest(label), volume():
                v = self.verdict()
                self.assertFalse(v["supported"], v)
                self.assertEqual(v["effective_instruction_files"], [])
                self.assertTrue(any("no AGENTS.md" in r for r in v["reasons"]),
                                v["reasons"])

    def test_the_case_sensitive_simulation_hides_the_variant_from_a_path_probe(self):
        """Positive control: under the simulation `CLAUDE.md` is not found while
        the listing still names `claude.md`, as on ext4."""
        _write(self.root / "claude.md", "legacy\n")
        with _case_sensitive_filesystem():
            self.assertFalse((self.root / "CLAUDE.md").is_file())
            self.assertTrue((self.root / "claude.md").is_file())
        self.assertIn("claude.md", os.listdir(self.root))

    def test_a_nested_lowercase_claude_md_on_the_chain_is_not_effective(self):
        _write(self.root / "sub" / "claude.md", "nested\n")
        self.settings(AND_MODE)
        for label, volume in _BOTH_VOLUMES:
            with self.subTest(label), volume():
                v = self.verdict(working_dir=self.root / "sub")
                self.assertEqual(v["effective_instruction_files"], [])
                self.assertFalse(v["supported"], v)

    def test_an_upper_case_claude_md_is_still_listed_once(self):
        """Control: the exact spelling keeps its one entry."""
        _write(self.root / "CLAUDE.md", "legacy\n")
        self.settings(AND_MODE)
        with _folding_filesystem():
            v = self.verdict()
        self.assertEqual(v["effective_instruction_files"], ["CLAUDE.md"])

    def test_root_and_working_directory_claude_files_are_each_listed_once(self):
        _write(self.root / "CLAUDE.md", "root\n")
        _write(self.root / "sub" / "CLAUDE.md", "nested\n")
        self.settings(AND_MODE)
        for label, volume in _BOTH_VOLUMES:
            with self.subTest(label), volume():
                v = self.verdict(working_dir=self.root / "sub")
                self.assertEqual(sorted(v["effective_instruction_files"]),
                                 ["CLAUDE.md", "sub/CLAUDE.md"])

    def test_mixed_case_claude_files_at_the_root_and_below_are_not_effective(self):
        _write(self.root / "Claude.md", "root\n")
        _write(self.root / "sub" / "claude.md", "nested\n")
        self.settings(AND_MODE)
        for label, volume in _BOTH_VOLUMES:
            with self.subTest(label), volume():
                v = self.verdict(working_dir=self.root / "sub")
                self.assertEqual(v["effective_instruction_files"], [])

    @unittest.skipUnless(
        _volume_folds_case(),
        "not exercised: this volume is case-sensitive, so a real case variant "
        "cannot be created beside its upper-case name; the simulated tests above "
        "carry the assertion here")
    def test_real_volume_a_variant_alone_is_not_effective(self):
        _write(self.root / "agents.md", "variant\n")
        self.assertTrue((self.root / "AGENTS.md").exists(),
                        "precondition: this volume folds case")
        v = self.verdict()
        self.assertFalse(v["supported"], v)
        self.assertEqual(v["effective_instruction_files"], [])

    @unittest.skipUnless(
        _volume_folds_case(),
        "not exercised: this volume is case-sensitive; the simulated twin above "
        "carries the assertion here")
    def test_real_volume_a_lone_claude_md_variant_is_not_effective(self):
        _write(self.root / "claude.md", "legacy\n")
        self.settings(AND_MODE)
        v = self.verdict()
        self.assertFalse(v["supported"], v)
        self.assertEqual(v["effective_instruction_files"], [])


class DotClaudeFileTests(CompatFixture):
    """`<dir>/.claude/CLAUDE.md` belongs to the scope of the directory holding
    `.claude`. Keyed on the file's own directory, a root file read as off-chain."""

    def test_a_root_dot_claude_file_alone_fails_for_the_suppressor_cause(self):
        (self.root / "AGENTS.md").unlink()
        _write(self.root / ".claude" / "CLAUDE.md", "legacy\n")
        v = self.verdict()
        self.assertFalse(v["supported"])
        self.assertIn(".claude/CLAUDE.md",
                      [s["path"] for s in v["suppressors"]["on_chain"]])
        self.assertTrue(any("suppressed by a Claude-named file" in r
                            and ".claude/CLAUDE.md" in r for r in v["reasons"]),
                        v["reasons"])

    def test_a_root_dot_claude_file_beside_agents_md_fails_the_default_mode(self):
        _write(self.root / ".claude" / "CLAUDE.md", "legacy\n")
        v = self.verdict()
        self.assertFalse(v["supported"], v)
        self.assertEqual([s["path"] for s in v["suppressors"]["on_chain"]],
                         [".claude/CLAUDE.md"])
        self.assertEqual(v["suppressors"]["off_chain"], [])
        self.assertIn("move its content", v["suppressors"]["on_chain"][0]["remedy"])
        self.assertTrue(v["adapter_applies"])
        self.assertEqual(v["effective_instruction_files"], [])

    def test_a_root_dot_claude_file_is_reported_and_fails_nothing_in_the_and_mode(self):
        _write(self.root / ".claude" / "CLAUDE.md", "legacy\n")
        self.settings(AND_MODE)
        v = self.verdict()
        self.assertTrue(v["supported"], v["reasons"])
        self.assertTrue(v["suppressors"]["on_chain"], "reporting and failing differ")

    def test_a_loaded_claude_md_without_agents_md_stays_supported_in_the_and_mode(self):
        """Control: the empty-list predicate, not an AGENTS.md predicate."""
        (self.root / "AGENTS.md").unlink()
        _write(self.root / "CLAUDE.md", "legacy\n")
        self.settings(AND_MODE)
        v = self.verdict()
        self.assertTrue(v["supported"], v["reasons"])
        self.assertEqual(v["effective_instruction_files"], ["CLAUDE.md"])

    def test_the_and_mode_counts_a_root_dot_claude_file_as_effective(self):
        """Condition a: the enumeration and the suppressor scan agree."""
        (self.root / "AGENTS.md").unlink()
        _write(self.root / ".claude" / "CLAUDE.md", "legacy\n")
        self.settings(AND_MODE)
        v = self.verdict()
        self.assertIn(".claude/CLAUDE.md", v["effective_instruction_files"])
        self.assertTrue(v["supported"], v["reasons"])
        self.assertFalse(any("no AGENTS.md" in r for r in v["reasons"]), v["reasons"])

    def test_the_and_mode_counts_an_intermediate_claude_md_as_effective(self):
        """A CLAUDE.md in a directory between the root and the working directory
        loads under the and-mode, so it is effective and no missing-file cause
        is raised."""
        (self.root / "AGENTS.md").unlink()
        _write(self.root / "a" / "CLAUDE.md", "legacy\n")
        (self.root / "a" / "b").mkdir(parents=True)
        self.settings(AND_MODE)
        v = self.verdict(working_dir=self.root / "a" / "b")
        self.assertIn("a/CLAUDE.md", v["effective_instruction_files"])
        self.assertTrue(v["supported"], v["reasons"])

    def test_the_and_mode_reports_a_lowercase_dot_claude_file_but_never_counts_it(self):
        """The scan matches the name case-insensitively, so `.claude/claude.md` is
        reported on the chain. Whether a host loads that spelling is unobserved,
        so it is never an effective file and never makes the verdict supported."""
        (self.root / "AGENTS.md").unlink()
        _write(self.root / ".claude" / "claude.md", "legacy\n")
        self.settings(AND_MODE)
        v = self.verdict()
        self.assertEqual([s["path"] for s in v["suppressors"]["on_chain"]],
                         [".claude/claude.md"])
        self.assertEqual(v["effective_instruction_files"], [], v)
        self.assertFalse(v["supported"], v)

    def test_control_the_exact_dot_claude_file_is_effective_in_the_and_mode(self):
        (self.root / "AGENTS.md").unlink()
        _write(self.root / ".claude" / "CLAUDE.md", "legacy\n")
        self.settings(AND_MODE)
        v = self.verdict()
        self.assertEqual(v["effective_instruction_files"], [".claude/CLAUDE.md"])
        self.assertTrue(v["supported"], v["reasons"])

    def test_a_nested_dot_claude_file_is_off_the_chain_from_the_root(self):
        """Control: chain scope still decides."""
        _write(self.root / "sub" / ".claude" / "CLAUDE.md", "legacy\n")
        v = self.verdict(working_dir=self.root)
        self.assertTrue(v["supported"], v["reasons"])
        self.assertEqual([s["path"] for s in v["suppressors"]["off_chain"]],
                         ["sub/.claude/CLAUDE.md"])

    def test_the_same_nested_file_is_on_the_chain_of_its_own_scope(self):
        _write(self.root / "sub" / ".claude" / "CLAUDE.md", "legacy\n")
        v = self.verdict(working_dir=self.root / "sub")
        self.assertFalse(v["supported"])
        self.assertEqual([s["path"] for s in v["suppressors"]["on_chain"]],
                         ["sub/.claude/CLAUDE.md"])

    def test_a_working_directory_inside_dot_claude_is_still_on_the_chain(self):
        """The file's own directory stays a chain member, as before."""
        _write(self.root / ".claude" / "CLAUDE.md", "legacy\n")
        v = self.verdict(working_dir=self.root / ".claude")
        self.assertFalse(v["supported"])


class NoCanonicalFileEntryPointTests(CompatFixture):
    def test_an_empty_checkout_exits_one_with_json(self):
        (self.root / "AGENTS.md").unlink()
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo-root", str(self.root),
             "--claude-home", str(self.home), "--claude-version", "2.1.278",
             "--distribution", "anthropic"],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        payload = json.loads(r.stdout)
        self.assertFalse(payload["supported"])
        self.assertTrue(any("no AGENTS.md" in x for x in payload["reasons"]))
        self.assertEqual(payload["effective_instruction_files"], [])


if __name__ == "__main__":
    unittest.main()
