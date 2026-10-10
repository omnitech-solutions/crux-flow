"""Tests for validate-catalog.py.

Covers the regression fixed in this pass: skills.json writes must be
atomic (write-tmp + os.replace) and free of platform newline translation.
We exercise the `_atomic_write_text` helper directly — it's the shared
behaviour change we care about; the rest of validate-catalog already has
its own integration coverage via the catalog regeneration workflow.

Stdlib only (unittest, tempfile, importlib, pathlib, sys).
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import re
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "crux" / "scripts" / "validate-catalog.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_catalog", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["validate_catalog"] = mod
    spec.loader.exec_module(mod)
    return mod


validator = _load_validator()


class AtomicWriteTextTests(unittest.TestCase):
    def test_writes_utf8_bytes_without_newline_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skills.json"
            body = '{\n  "skills": []\n}\n'
            validator._atomic_write_text(target, body)
            # Bytes must match exactly — no '\r\n' injection by the runtime.
            self.assertEqual(target.read_bytes(), body.encode("utf-8"))

    def test_no_leftover_tmp_after_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skills.json"
            validator._atomic_write_text(target, "{}")
            siblings = sorted(p.name for p in Path(tmp).iterdir())
            self.assertEqual(siblings, ["skills.json"])

    def test_replaces_existing_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skills.json"
            target.write_text('{"skills": ["OLD"]}', encoding="utf-8")
            validator._atomic_write_text(target, '{"skills": ["NEW"]}')
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                '{"skills": ["NEW"]}',
            )


class AtomicWriteSymlinkRefusalTests(unittest.TestCase):
    """[SECURITY:S5] The sibling of the `_yaml_min.write_catalog_yaml` gap.

    This helper had the byte-identical shape: write `<path>.tmp` with
    `Path.write_bytes` (which FOLLOWS a link) then `os.replace` it into place.
    Surfaced by the fail-closed sibling sweep rather than by diff review, since
    the guard landed in a different file. This helper writes `skills.json` and
    `agents.json`, both of which a plugin consumer trusts.
    """

    def test_a_pre_created_tmp_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            victim = root / "victim.txt"
            victim.write_text("VICTIM\n", encoding="utf-8")
            target = root / "skills.json"
            (root / "skills.json.tmp").symlink_to(victim)
            with self.assertRaises(OSError):
                validator._atomic_write_text(target, '{"skills": []}')
            self.assertEqual(victim.read_text(encoding="utf-8"), "VICTIM\n")
            self.assertFalse(target.exists())

    def test_a_target_that_is_already_a_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            victim = root / "victim.txt"
            victim.write_text("VICTIM\n", encoding="utf-8")
            target = root / "skills.json"
            target.symlink_to(victim)
            with self.assertRaises(OSError):
                validator._atomic_write_text(target, '{"skills": []}')
            self.assertEqual(victim.read_text(encoding="utf-8"), "VICTIM\n")
            self.assertTrue(target.is_symlink())


class StrictMetadataContractTests(unittest.TestCase):
    """ADR-0034: unknown `metadata.*` keys are validation errors, and the
    former provenance keys (origin/origin_ref/origin_date) are gone from
    the contract entirely. Fixture-based — never touches the live tree."""

    # Per ADR-0092 the constant keys owner/version/status were pruned; the
    # required metadata set is now tags/bundles/risk_level.
    GOOD_METADATA = {
        "tags": "a, b",
        "bundles": "crux-core",
        "risk_level": "low",
    }

    def test_known_metadata_keys_lift_cleanly(self):
        lifted, shape_errors = validator._lift_metadata(
            {"name": "x", "description": "d", "metadata": dict(self.GOOD_METADATA)}
        )
        self.assertEqual(shape_errors, [])
        self.assertEqual(lifted["tags"], ["a", "b"])
        self.assertEqual(lifted["risk_level"], "low")

    def test_pruned_constant_keys_are_rejected(self):
        """ADR-0092: owner/version/status are no longer contract keys; a lingering
        one is an unknown-metadata-key error and does not round-trip."""
        for key in ("owner", "version", "status"):
            md = dict(self.GOOD_METADATA)
            md[key] = "x"
            lifted, shape_errors = validator._lift_metadata(
                {"name": "x", "description": "d", "metadata": md}
            )
            self.assertTrue(
                any(f"unknown metadata key {key!r}" in e for e in shape_errors),
                f"{key} should be rejected, got {shape_errors}",
            )
            self.assertNotIn(key, lifted)
            self.assertNotIn(key, validator.OUTPUT_KEY_ORDER)
            self.assertNotIn(key, validator.KNOWN_METADATA_KEYS)

    def test_unknown_metadata_key_is_shape_error_and_not_lifted(self):
        md = dict(self.GOOD_METADATA)
        md["custom_key"] = "whatever"
        lifted, shape_errors = validator._lift_metadata({"name": "x", "description": "d", "metadata": md})
        self.assertEqual(len(shape_errors), 1)
        self.assertIn("custom_key", shape_errors[0])
        self.assertIn("ADR-0034", shape_errors[0])
        self.assertNotIn("custom_key", lifted)

    def test_former_provenance_keys_are_rejected(self):
        md = dict(self.GOOD_METADATA)
        md["origin"] = "somewhere"
        md["origin_ref"] = "sha256:" + "0" * 64
        md["origin_date"] = "2026-01-01"
        lifted, shape_errors = validator._lift_metadata({"name": "x", "description": "d", "metadata": md})
        flagged = {e.split("'")[1] for e in shape_errors if "unknown metadata key" in e}
        self.assertEqual(flagged, {"origin", "origin_ref", "origin_date"})
        for key in ("origin", "origin_ref", "origin_date"):
            self.assertNotIn(key, lifted)

    def test_provenance_keys_not_in_output_contract(self):
        for key in ("origin", "origin_ref", "origin_date"):
            self.assertNotIn(key, validator.OUTPUT_KEY_ORDER)
            self.assertNotIn(key, validator.OPTIONAL_SCHEMA_KEYS)
            self.assertNotIn(key, validator.ALL_KNOWN_KEYS)
            self.assertNotIn(key, validator.KNOWN_METADATA_KEYS)

    def test_end_to_end_skill_with_provenance_key_fails_validation(self):
        """A SKILL.md still carrying a provenance metadata key must produce a
        validation error (and the key must not round-trip into the entry)."""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp)
            skill_dir = plugin_dir / "skills" / "demo-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: demo-skill\n"
                "description: A demo.\n"
                "metadata:\n"
                '  tags: "a, b"\n'
                '  bundles: "crux-core"\n'
                '  risk_level: "low"\n'
                '  origin: "legacy"\n'
                "---\n\n# demo\n",
                encoding="utf-8",
            )
            entries, errors = validator.regenerate_skills_json(plugin_dir, verbose=False)
            self.assertEqual(len(entries), 1, "non-fatal error must not drop the entry")
            self.assertNotIn("origin", entries[0])
            self.assertTrue(
                any("unknown metadata key 'origin'" in e["error"] for e in errors),
                f"expected strict-metadata error, got: {errors}",
            )

    def test_end_to_end_clean_skill_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp)
            skill_dir = plugin_dir / "skills" / "demo-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: demo-skill\n"
                "description: A demo.\n"
                "metadata:\n"
                '  tags: "a, b"\n'
                '  bundles: "crux-core"\n'
                '  risk_level: "low"\n'
                '  requires_env: "SOME_KEY"\n'
                "---\n\n# demo\n",
                encoding="utf-8",
            )
            entries, errors = validator.regenerate_skills_json(plugin_dir, verbose=False)
            self.assertEqual(errors, [])
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["requires_env"], ["SOME_KEY"])

    def test_disable_model_invocation_toplevel_key_is_accepted(self):
        """A skill may carry the top-level loader key `disable-model-invocation`.
        Per ADR-0092 it validates cleanly AND projects into the catalog entry
        (the invocation-control keys are the catalog's public shape now)."""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp)
            skill_dir = plugin_dir / "skills" / "demo-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: demo-skill\n"
                "description: A demo.\n"
                "disable-model-invocation: true\n"
                "metadata:\n"
                '  tags: "a, b"\n'
                '  bundles: "crux-core"\n'
                '  risk_level: "low"\n'
                "---\n\n# demo\n",
                encoding="utf-8",
            )
            entries, errors = validator.regenerate_skills_json(plugin_dir, verbose=False)
            self.assertEqual(errors, [], f"unexpected errors: {errors}")
            self.assertEqual(len(entries), 1)
            self.assertIs(entries[0]["disable-model-invocation"], True)


class MetadataKnownKeySliceBoundaryTests(unittest.TestCase):
    """KNOWN_METADATA_KEYS is derived as SCHEMA_KEYS[2:] | optional — pin the
    slice boundary: id/name (the two top-level keys excluded by [2:]) must be
    REJECTED if they ever appear under metadata:."""

    def test_id_and_name_are_unknown_metadata_keys(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "vc", Path(__file__).resolve().parent.parent / "validate-catalog.py"
        )
        vc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vc)
        self.assertNotIn("id", vc.KNOWN_METADATA_KEYS)
        self.assertNotIn("name", vc.KNOWN_METADATA_KEYS)
        self.assertIn("tags", vc.KNOWN_METADATA_KEYS)
        self.assertIn("requires_env", vc.KNOWN_METADATA_KEYS)


def _write_skill(plugin_dir: Path, name: str, *, top: str = "", body: str = "# body\n",
                 tags: str = "a, b", bundles: str = "crux-core", extra_meta: str = "") -> None:
    d = plugin_dir / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: A demo.\n"
        f"{top}"
        "metadata:\n"
        f'  tags: "{tags}"\n'
        f'  bundles: "{bundles}"\n'
        '  risk_level: "low"\n'
        f"{extra_meta}"
        "---\n\n"
        f"{body}",
        encoding="utf-8",
    )


class InvocationControlSkillTests(unittest.TestCase):
    """ADR-0092 item 1: skill invocation-control top-level keys validate and
    project present-only into skills.json."""

    def test_invocation_keys_project_present_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp)
            _write_skill(
                plugin_dir, "demo-skill",
                top="disable-model-invocation: true\nuser-invocable: false\n"
                    "context: fork\nmodel: sonnet\narguments: [adr]\n",
            )
            _write_skill(plugin_dir, "plain-skill")
            entries, errors = validator.regenerate_skills_json(plugin_dir, verbose=False)
            self.assertEqual(errors, [], errors)
            by = {e["id"]: e for e in entries}
            demo = by["demo-skill"]
            self.assertIs(demo["disable-model-invocation"], True)
            self.assertIs(demo["user-invocable"], False)
            self.assertEqual(demo["context"], "fork")
            self.assertEqual(demo["model"], "sonnet")
            self.assertEqual(demo["arguments"], ["adr"])
            # present-only: a skill without them omits them entirely
            for k in ("context", "model", "arguments", "disable-model-invocation"):
                self.assertNotIn(k, by["plain-skill"])

    def test_bad_context_and_effort_are_errors(self):
        errs: list[dict] = []
        validator._validate_skill_loader_keys({"context": "nope"}, "s", errs)
        validator._validate_skill_loader_keys({"effort": "turbo"}, "s", errs)
        validator._validate_skill_loader_keys({"user-invocable": "yes"}, "s", errs)
        fields = {e["field"] for e in errs}
        self.assertEqual(fields, {"context", "effort", "user-invocable"})

    def test_disable_model_invocation_non_bool_rejected(self):
        errs: list[dict] = []
        validator._validate_skill_loader_keys({"disable-model-invocation": "true"}, "s", errs)
        self.assertEqual([e["field"] for e in errs], ["disable-model-invocation"])

    def test_background_non_bool_rejected(self):
        # Pair with context: fork so the coupling rule (tested below) does not
        # also fire and muddy the field set — this isolates the type check.
        errs: list[dict] = []
        validator._validate_skill_loader_keys(
            {"background": "yes", "context": "fork"}, "s", errs
        )
        self.assertEqual([e["field"] for e in errs], ["background"])

    def test_background_only_valid_with_context_fork_both_directions(self):
        # `background` has ZERO other references; both coupling directions here.
        # Direction 1: background present WITHOUT context -> coupling error.
        errs: list[dict] = []
        validator._validate_skill_loader_keys({"background": True}, "s", errs)
        self.assertEqual([e["field"] for e in errs], ["background"])
        self.assertIn("context: fork", errs[0]["error"])
        # Direction 2: background present WITH context: fork -> no error at all.
        errs2: list[dict] = []
        validator._validate_skill_loader_keys(
            {"background": True, "context": "fork"}, "s", errs2
        )
        self.assertEqual(errs2, [])

    def test_agent_non_string_rejected(self):
        errs: list[dict] = []
        validator._validate_skill_loader_keys({"agent": ["developer"]}, "s", errs)
        self.assertEqual([e["field"] for e in errs], ["agent"])

    def test_model_non_string_rejected(self):
        errs: list[dict] = []
        validator._validate_skill_loader_keys({"model": 5}, "s", errs)
        self.assertEqual([e["field"] for e in errs], ["model"])

    def test_arguments_bad_type_rejected(self):
        # Neither a string nor a list of strings.
        errs: list[dict] = []
        validator._validate_skill_loader_keys({"arguments": 5}, "s", errs)
        validator._validate_skill_loader_keys({"arguments": [1, 2]}, "s", errs)
        self.assertEqual([e["field"] for e in errs], ["arguments", "arguments"])

    def test_disallowed_tools_bad_type_rejected(self):
        errs: list[dict] = []
        validator._validate_skill_loader_keys({"disallowed-tools": 5}, "s", errs)
        validator._validate_skill_loader_keys({"disallowed-tools": [1]}, "s", errs)
        self.assertEqual(
            [e["field"] for e in errs], ["disallowed-tools", "disallowed-tools"]
        )

    def test_routing_note_lifts_and_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp)
            _write_skill(plugin_dir, "demo-skill",
                         extra_meta='  routing_note: "First-run note."\n')
            entries, errors = validator.regenerate_skills_json(plugin_dir, verbose=False)
            self.assertEqual(errors, [], errors)
            self.assertEqual(entries[0]["routing_note"], "First-run note.")


class InvocationControlAgentTests(unittest.TestCase):
    """ADR-0092 items 2/3: agent invocation-control keys validate; effort warns."""

    def test_maxturns_must_be_positive_int(self):
        errs: list[dict] = []
        validator._validate_agent_invocation_keys({"maxTurns": 0}, "a", set(), errs)
        validator._validate_agent_invocation_keys({"maxTurns": "40"}, "a", set(), errs)
        self.assertEqual([e["field"] for e in errs], ["maxTurns", "maxTurns"])

    def test_effort_emits_a_warning_not_an_error(self):
        errs: list[dict] = []
        validator._validate_agent_invocation_keys({"effort": "high"}, "a", set(), errs)
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0].get("severity"), "warning")

    def test_bad_enums_and_skills_on_disk(self):
        errs: list[dict] = []
        validator._validate_agent_invocation_keys(
            {"memory": "cloud", "isolation": "vm", "skills": ["ghost"]},
            "a", {"real-skill"}, errs,
        )
        fields = [e["field"] for e in errs]
        self.assertIn("memory", fields)
        self.assertIn("isolation", fields)
        self.assertIn("skills", fields)

    def test_a_real_preloaded_skill_passes(self):
        errs: list[dict] = []
        validator._validate_agent_invocation_keys(
            {"skills": ["real-skill"], "maxTurns": 30, "memory": "project",
             "isolation": "worktree"},
            "a", {"real-skill"}, errs,
        )
        self.assertEqual(errs, [])


class AgentSkillDeclarationTests(unittest.TestCase):
    """ADR-0111: every role declares the skills its workflows require."""

    EXPECTED_ROLE_SKILLS = {
        "architect": [
            "propose-adr", "transition-adr", "review-decisions", "council", "srde",
            "author-promptbook", "dev-cycle", "link-adr-graph", "propose-brief",
            "log-work", "forge-skill",
        ],
        "brainstormer": ["whiteboarding", "query-docs", "forge-skill", "log-work"],
        "commander": [
            "run-promptbook", "dev-cycle", "council", "srde",
            "forge-skill", "log-work",
        ],
        "dev-lead": ["forge-skill", "log-work"],
        "developer": ["forge-skill", "log-work"],
        "historian": [
            "init-docs", "audit-docs", "cleanup-campsite", "link-adr-graph",
            "check-drift", "transition-adr", "ingest-research", "process-inbox", "propose-adr",
            "propose-brief", "log-work",
            "archive-promptbook", "extract-code-docs", "verify-code-docs",
            "run-promptbook", "forge-skill",
        ],
        "librarian": ["query-docs", "forge-skill", "log-work"],
        "night-gardener": [
            "tend-garden", "cleanup-campsite", "read-news", "refresh-research-sources",
            "retrospective", "council", "srde", "whiteboarding", "query-docs", "prose-review",
            "forge-skill", "log-work",
        ],
        "reviewer": ["prose-review", "council", "srde", "forge-skill", "log-work"],
        "wayfinder": [],
    }

    DIRECT_ROLE_SKILLS = {
        "architect": {
            "propose-adr", "transition-adr", "review-decisions", "council", "srde",
            "author-promptbook", "dev-cycle", "link-adr-graph", "propose-brief",
            "forge-skill",
        },
        "brainstormer": {"whiteboarding", "query-docs", "forge-skill"},
        "commander": {
            "run-promptbook", "dev-cycle", "council", "srde",
            "forge-skill",
        },
        "dev-lead": {"forge-skill"},
        "developer": {"forge-skill"},
        "historian": {
            "init-docs", "audit-docs", "cleanup-campsite", "link-adr-graph",
            "ingest-research", "process-inbox", "log-work",
            "archive-promptbook", "extract-code-docs", "verify-code-docs",
            "run-promptbook", "forge-skill",
        },
        "librarian": {"query-docs", "forge-skill"},
        "night-gardener": {"tend-garden", "forge-skill"},
        "reviewer": {"prose-review", "council", "srde", "forge-skill"},
        "wayfinder": set(),
    }

    REQUIRED_SKILL_DEPENDENCIES = {
        "audit-docs": {"check-drift", "transition-adr"},
        "forge-skill": {"log-work"},
        "process-inbox": {"ingest-research", "propose-adr", "propose-brief", "log-work"},
        "read-news": {"refresh-research-sources"},
        "retrospective": {"council", "srde", "forge-skill", "log-work"},
        "review-decisions": {"log-work"},
        "tend-garden": {
            "cleanup-campsite", "read-news", "retrospective", "whiteboarding", "prose-review",
        },
        "whiteboarding": {"query-docs"},
    }

    def _plugin_fixture(self, tmp: Path) -> Path:
        plugin = tmp / "crux"
        shutil.copytree(REPO_ROOT / "crux" / "agents", plugin / "agents")
        (plugin / "catalog").mkdir(parents=True)
        shutil.copy2(REPO_ROOT / "crux" / "catalog" / "models.yml", plugin / "catalog" / "models.yml")
        return plugin

    @staticmethod
    def _write_skill(plugin: Path, name: str) -> None:
        path = plugin / "skills" / name
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text("---\nname: real-skill\n---\n", encoding="utf-8")

    def _valid_agent(self, plugin: Path, name: str) -> tuple[dict, Path, set[str]]:
        agent_path = plugin / "agents" / f"{name}.md"
        frontmatter, error = validator.extract_agent_frontmatter(agent_path)
        self.assertIsNone(error, error)
        allowed_models, error = validator.agent_model_enum(plugin)
        self.assertIsNone(error, error)
        assert allowed_models is not None
        return deepcopy(frontmatter), agent_path, allowed_models

    def _errors_for(self, plugin: Path, frontmatter: dict, agent_path: Path, skill_ids: set[str]) -> list[dict]:
        _, errors = validator.validate_agent_frontmatter(
            frontmatter, agent_path, plugin, {"fable", "opus", "sonnet"}, skill_ids
        )
        return errors

    def _shipped_role_skill_map(self) -> dict[str, list[str]]:
        plugin = REPO_ROOT / "crux"
        skill_ids = {path.parent.name for path in validator.discover_skills(plugin)}
        entries, errors = validator.regenerate_agents_json(plugin, verbose=False, skill_ids=skill_ids)
        hard_errors = [error for error in errors if error.get("severity") != "warning"]
        self.assertEqual(hard_errors, [], hard_errors)
        return {entry["id"]: entry.get("skills") for entry in entries}

    def test_shipped_roles_declare_the_exact_workflow_skill_map(self):
        self.assertEqual(self._shipped_role_skill_map(), self.EXPECTED_ROLE_SKILLS)

    def test_declared_skills_cover_each_role_workflow_dependency_closure(self):
        declared_by_role = self._shipped_role_skill_map()
        for role, roots in self.DIRECT_ROLE_SKILLS.items():
            with self.subTest(role=role):
                required = set(roots)
                pending = list(roots)
                while pending:
                    skill = pending.pop()
                    for dependency in self.REQUIRED_SKILL_DEPENDENCIES.get(skill, set()):
                        if dependency not in required:
                            required.add(dependency)
                            pending.append(dependency)
                self.assertTrue(
                    required <= set(declared_by_role[role]),
                    f"{role} omits {sorted(required - set(declared_by_role[role]))}",
                )

    def test_missing_skills_declaration_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            plugin = self._plugin_fixture(Path(td))
            frontmatter, agent_path, _ = self._valid_agent(plugin, "developer")
            frontmatter.pop("skills", None)
            errors = self._errors_for(plugin, frontmatter, agent_path, set())
        self.assertTrue(any(error["field"] == "skills" and "missing required" in error["error"] for error in errors))

    def test_duplicate_skill_declaration_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            plugin = self._plugin_fixture(Path(td))
            self._write_skill(plugin, "real-skill")
            frontmatter, agent_path, _ = self._valid_agent(plugin, "developer")
            frontmatter["skills"] = ["real-skill", "real-skill"]
            errors = self._errors_for(plugin, frontmatter, agent_path, {"real-skill"})
        self.assertTrue(any(error["field"] == "skills" and "duplicate" in error["error"] for error in errors))

    def test_unknown_skill_declaration_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            plugin = self._plugin_fixture(Path(td))
            self._write_skill(plugin, "real-skill")
            frontmatter, agent_path, _ = self._valid_agent(plugin, "developer")
            frontmatter["skills"] = ["ghost-skill"]
            errors = self._errors_for(plugin, frontmatter, agent_path, {"real-skill"})
        self.assertTrue(any(error["field"] == "skills" and "ghost-skill" in error["error"] for error in errors))

    def test_declared_skill_without_a_skill_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            plugin = self._plugin_fixture(Path(td))
            frontmatter, agent_path, _ = self._valid_agent(plugin, "developer")
            frontmatter["skills"] = ["real-skill"]
            errors = self._errors_for(plugin, frontmatter, agent_path, {"real-skill"})
        self.assertTrue(any(error["field"] == "skills" and "SKILL.md" in error["error"] for error in errors))


class BundleClosureTests(unittest.TestCase):
    """ADR-0092 item 6: every skill a default-on skill depends on must be default-on."""

    def _plugin(self, tmp: str, default_on_skills: list[str]) -> Path:
        plugin_dir = Path(tmp)
        (plugin_dir / "catalog").mkdir(parents=True, exist_ok=True)
        skills_block = "\n".join(f'    - "{s}"' for s in default_on_skills)
        (plugin_dir / "catalog" / "bundles.yml").write_text(
            "crux-docs:\n"
            '  name: "docs"\n'
            '  description: "d"\n'
            "  audiences:\n    - x\n"
            "  default_provision: true\n"
            "  skills:\n" + skills_block + "\n",
            encoding="utf-8",
        )
        return plugin_dir

    def test_dependency_outside_default_on_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = self._plugin(tmp, ["a-skill"])
            _write_skill(plugin_dir, "a-skill", body="Runs `b-skill` first.\n")
            _write_skill(plugin_dir, "b-skill")
            findings = validator.bundle_closure_findings(
                plugin_dir, plugin_dir / "catalog" / "bundles.yml", {"a-skill", "b-skill"}
            )
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0]["severity"], "warning")
            self.assertIn("b-skill", findings[0]["error"])

    def test_moving_the_dep_into_the_bundle_clears_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = self._plugin(tmp, ["a-skill", "b-skill"])
            _write_skill(plugin_dir, "a-skill", body="Runs `b-skill` first.\n")
            _write_skill(plugin_dir, "b-skill")
            findings = validator.bundle_closure_findings(
                plugin_dir, plugin_dir / "catalog" / "bundles.yml", {"a-skill", "b-skill"}
            )
            self.assertEqual(findings, [])

    def test_fenced_code_and_suppression_do_not_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = self._plugin(tmp, ["a-skill"])
            _write_skill(plugin_dir, "a-skill",
                         body="```\n`b-skill`\n```\nSee `c-skill`. <!-- bundle-closure-ignore: c-skill -->\n")
            _write_skill(plugin_dir, "b-skill")
            _write_skill(plugin_dir, "c-skill")
            findings = validator.bundle_closure_findings(
                plugin_dir, plugin_dir / "catalog" / "bundles.yml",
                {"a-skill", "b-skill", "c-skill"},
            )
            self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()


# ─────────────────── models.yml rules V0-V9 (ADR-0072 AC-8) ───────────────
#
# One positive fixture plus a NAMED negative per rule, so no implementer reads
# the list as partial. Every fixture is a text transform of the SHIPPED catalog
# written into a tmpdir: self-contained, and it never touches the real file.

import json  # noqa: E402

CATALOG_DIR = REPO_ROOT / "crux" / "catalog"
SHIPPED_MODELS = CATALOG_DIR / "models.yml"
SHIPPED_BUNDLES = CATALOG_DIR / "bundles.yml"


def _plugin_fixture(tmp: Path, models_text: str | None = None) -> Path:
    """A minimal plugin dir: catalog/models.yml + agents/*.md + the router config.

    Copies only what the rules read. A whole-repo copy would be both slower and
    unsafe under the staged release gate, where paths outside `crux/` are absent.
    """
    plugin = tmp / "crux"
    (plugin / "catalog").mkdir(parents=True)
    (plugin / "agents").mkdir(parents=True)
    (plugin / "scripts" / "crux" / "_config").mkdir(parents=True)
    (plugin / "catalog" / "models.yml").write_text(
        models_text if models_text is not None else SHIPPED_MODELS.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    for src in sorted((REPO_ROOT / "crux" / "agents").glob("*.md")):
        shutil.copy2(src, plugin / "agents" / src.name)
    shutil.copy2(
        REPO_ROOT / "crux" / "scripts" / "crux" / "_config" / "llm_router_config.json",
        plugin / "scripts" / "crux" / "_config" / "llm_router_config.json",
    )
    return plugin


def _rules(plugin: Path) -> list[dict]:
    return validator.validate_models_yml(plugin / "catalog" / "models.yml", plugin)


def _fields(findings: list[dict]) -> set[str]:
    return {f["field"] for f in findings}


# Anchors the V5 and V7 negative fixtures splice against. Named once here
# because four fixtures share two of them and a respelled alias table has to
# move one string, not four. `_assert_rule` asserts each splice landed.
_PARKED_ALIAS_ROW = "glm-flash: openrouter/z-ai/glm-5.3-flash"
# The standard level's Claude cell, as shipped. Named once so the next re-point
# of that cell moves one string, not four fixtures.
_STANDARD_CLAUDE_CELL = "  standard:\n    claude: claude-sonnet-5-5"
_QWEN_MAX_ROW = "  qwen-max: openrouter/"

def _set_standard_claude(text: str, value: str) -> str:
    """Set the standard level's Claude cell, whatever value it holds now.

    Anchored on structure, never on the current value; a zero-match
    substitution raises instead of validating the pristine catalog.
    """
    out, count = re.subn(
        r"^(  standard:\n    claude: )\S+$", lambda m: m.group(1) + value, text, flags=re.M
    )
    if count != 1:
        raise AssertionError("fixture inert: the standard level's Claude cell was not found")
    return out


# The two fixtures that need an UNDECLARED alias reference mutate the developer
# row's OpenCode override. Anchored on the agent name and its `level:` line and
# never on the alias VALUE, for the reason the V2b/V2c comment below gives: the
# owner picks that value, and a fixture spelling it out goes inert the next time
# it changes — which is exactly what happened when developer moved off
# `glm-flash`. The substitution keeps whatever level the row declares, so only
# the alias becomes undeclared and V2a is the clause under test.
_DEVELOPER_OPENCODE = re.compile(r"^(  developer:\n    level: \S+\n    opencode: )\S+$", re.M)


def _undeclare_developer_opencode(text: str) -> str:
    """Point the developer row's OpenCode override at an alias the table lacks."""
    return _DEVELOPER_OPENCODE.sub(r"\1not-declared", text)


def _set_agent_claude(text: str, agent: str, value: str | None) -> str:
    """Set (or, with None, remove) one agent row's Claude Code override.

    The same structural anchor as `_DEVELOPER_OPENCODE`: the agent name and its
    `level:` line, never a model value, so the next re-point of an override
    moves no fixture. A zero-match substitution raises rather than returning
    the pristine catalog for a negative test to pass on.
    """
    pattern = re.compile(
        rf"^(  {re.escape(agent)}:\n    level: \S+\n)(    claude: \S+\n)?", re.M
    )
    line = "" if value is None else f"    claude: {value}\n"
    out, count = pattern.subn(lambda m: m.group(1) + line, text)
    if count != 1:
        raise AssertionError(f"fixture inert: agent row {agent!r} not found as a mapping row")
    return out


def _set_agent_model(plugin: Path, agent: str, value: str) -> None:
    """Rewrite one agent file's `model:` line, whatever value it holds now."""
    path = plugin / "agents" / f"{agent}.md"
    text = path.read_text(encoding="utf-8")
    out, count = re.subn(r"^model: \S+$", f"model: {value}", text, count=1, flags=re.M)
    if count != 1:
        raise AssertionError(f"fixture inert: agents/{agent}.md has no `model:` line")
    path.write_text(out, encoding="utf-8")


class ModelsCatalogPositiveTests(unittest.TestCase):
    def test_shipped_catalog_passes_every_rule(self):
        with tempfile.TemporaryDirectory() as td:
            findings = _rules(_plugin_fixture(Path(td)))
        self.assertEqual([f for f in findings if f.get("severity") != "warning"], [])

    def test_fable_is_now_a_legal_level_selection(self):
        # Flipped from a negative when the `claude_disabled` table was removed
        # from the shipped catalog. `fable` is a MEMBER of claude_aliases, and
        # with the deny-list gone membership alone governs V3, so no rule
        # refuses the alias as a level value. The mutation DOES trip V6 — the
        # four standard-level agent files still declare `model: claude-sonnet-5-5`
        # — and that firing is what proves the mutated catalog was validated all
        # the way through rather than skipped.
        with tempfile.TemporaryDirectory() as td:
            text = SHIPPED_MODELS.read_text(encoding="utf-8").replace(
                _STANDARD_CLAUDE_CELL, "  standard:\n    claude: fable")
            findings = _rules(_plugin_fixture(Path(td), text))
        fields = _fields(findings)
        self.assertIn("V6", fields)
        self.assertNotIn("V3", fields)
        self.assertNotIn("V9", fields)


    def test_sonnet_alias_and_full_sonnet_5_5_id_are_both_legal_level_values(self):
        # Each value goes into the standard cell AND the four standard-level
        # agent files, so V6 agrees. Any finding left is a rule refusing the
        # value itself (V3 or V9).
        standard_agents = ("developer", "historian", "librarian", "wayfinder")
        for value in ("sonnet", "claude-sonnet-5-5"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as td:
                text = _set_standard_claude(SHIPPED_MODELS.read_text(encoding="utf-8"), value)
                plugin = _plugin_fixture(Path(td), text)
                for agent in standard_agents:
                    _set_agent_model(plugin, agent, value)
                findings = _rules(plugin)
                self.assertEqual([f for f in findings if f.get("severity") != "warning"], [])


class ModelsCatalogNegativeTests(unittest.TestCase):
    """One named negative fixture per rule."""

    def _assert_rule(self, rule: str, transform):
        original = SHIPPED_MODELS.read_text(encoding="utf-8")
        mutated = transform(original)
        # A negative fixture mutates the shipped catalog by `str.replace`, which
        # NO-OPS SILENTLY when its anchor string is gone. Every such fixture then
        # validates the pristine catalog and reports whatever that reports —
        # green forever, testing nothing. Anchors do go stale: the whole alias
        # table was respelled `openrouter/<vendor>/<slug>` in one pass, and four
        # fixtures anchored on the old spellings. Assert the mutation landed, so
        # a stale anchor fails loudly instead of disarming its own test.
        # `assertTrue` on the comparison, not `assertNotEqual` on the pair:
        # assertNotEqual reports through `safe_repr`, which does not shorten, so
        # a stale anchor printed both copies of the whole catalog and buried the
        # one sentence that says what to do. `maxDiff` does not help — it gates
        # `_truncateMessage`, which this assertion never reaches.
        self.assertTrue(
            mutated != original,
            f"{rule} fixture is inert: its anchor no longer occurs in catalog/models.yml",
        )
        with tempfile.TemporaryDirectory() as td:
            plugin = _plugin_fixture(Path(td), mutated)
            findings = _rules(plugin)
        self.assertIn(rule, _fields(findings), f"{rule} did not fire; got {findings}")

    # ── V0, five of its own ────────────────────────────────────────────────
    FAST_ROW = (
        "  fast:\n"
        "    claude: sonnet\n"
        "    opencode: haiku-latest\n"
        "    codex:\n"
        "      model: gpt-6.1-sol\n"
        "      reasoning_effort: low\n"
        '      verified: "2026-08-21"\n'
        '      source: "fixture"\n'
    )

    def test_v0_fourth_level(self):
        # Inserted INSIDE the levels block, not appended past the end of the
        # document: an append would be a YAML syntax error and the test would
        # pass on the wrong finding.
        self._assert_rule("V0", lambda t: t.replace("levels:\n  apex:", "levels:\n" + self.FAST_ROW + "  apex:"))

    def test_v0_extra_top_level_key(self):
        self._assert_rule("V0", lambda t: t + "\nsurprise: yes\n")

    def test_v0_appended_claude_alias(self):
        self._assert_rule("V0", lambda t: t.replace("claude_aliases:\n  - fable", "claude_aliases:\n  - invented\n  - fable"))

    def test_v0_added_claude_disabled_table(self):
        # The `claude_disabled` deny-list table is GONE from the shipped
        # catalog: the top-level key set is now six keys. Reintroducing the
        # table at all — even with the original `fable` entry — is the defect
        # V0 refuses, not a value edit inside a recognized table.
        self._assert_rule("V0", lambda t: t + "\nclaude_disabled:\n  - fable\n")

    def test_v0_agent_row_carrying_an_unknown_key(self):
        self._assert_rule("V0", lambda t: t.replace(
            "  brainstormer: flagship",
            "  brainstormer:\n    level: flagship\n    claude_model: opus"))

    def test_v0_non_string_claude_override(self):
        self._assert_rule("V0", lambda t: _set_agent_claude(t, "commander", "42"))

    def test_v0_version_3_catalog(self):
        self._assert_rule("V0", lambda t: t.replace('schema_version: "4"', 'schema_version: "3"', 1))

    # ── V1 ─────────────────────────────────────────────────────────────────
    def test_v1_roster_stem_naming_no_agent_file(self):
        self._assert_rule("V1", lambda t: t.replace(
            "  wayfinder: standard", "  wayfinder: standard\n  ghost: standard"))

    # ── V2, one per clause ─────────────────────────────────────────────────
    def test_v2a_undeclared_alias_reference(self):
        self._assert_rule("V2", _undeclare_developer_opencode)

    # Both fixtures below are anchored on STRUCTURE — the agent name and its `level:` line —
    # and never on the `opencode:` alias beside it. They used to splice whole three-line blocks
    # including that alias, so changing which OpenCode model an agent selects made every splice
    # a silent no-op. `_assert_rule`'s inertness guard caught it, which is what that guard is
    # for; re-anchoring is what stops it recurring on the next model change. A model alias is a
    # value the owner picks, not part of the shape these rules govern.

    def _drop_apex_agents(self, text: str) -> str:
        """Move every apex agent to flagship, leaving its override untouched.

        V2 fires because `apex` is then a level no agent names.
        """
        return re.sub(r"^(  (?:commander|night-gardener|reviewer):\n    level: )apex$",
                      r"\1flagship", text, flags=re.M)

    def test_v2b_level_no_agent_names(self):
        self._assert_rule("V2", self._drop_apex_agents)

    def _override_claude_at_apex(self, text: str) -> str:
        """Give every apex agent a Claude override, whichever ones carry one now."""
        for name in re.findall(r"^  (\S+):\n    level: apex$", text, flags=re.M):
            text = _set_agent_claude(text, name, "fable")
        return text

    def test_v2_claude_cell_every_agent_at_its_level_overrides(self):
        self._assert_rule("V2", self._override_claude_at_apex)

    def test_v2c_apex_agent_with_no_override(self):
        """Apex loses its OpenCode default, so an apex agent without an override has none."""
        self._assert_rule("V2", lambda t: re.sub(
            r"^(  apex:\n    claude: \S+\n)    opencode: \S+\n", r"\1", t, flags=re.M))

    # ── V3, one ────────────────────────────────────────────────────────────
    # The deny-list negative (`claude: fable` refused) is GONE with the
    # `claude_disabled` table: its flipped positive lives in
    # ModelsCatalogPositiveTests as test_fable_is_now_a_legal_level_selection.
    def test_v3_invented_alias(self):
        self._assert_rule("V3", lambda t: t.replace(_STANDARD_CLAUDE_CELL, "  standard:\n    claude: invented"))

    def test_v3_router_spelling_of_sonnet_5_5_is_not_a_level_value(self):
        # `claude-sonnet-5.5` is the ROUTER's key for the model; the Claude Code
        # enum holds `claude-sonnet-5-5`. The level cell takes only the latter.
        with tempfile.TemporaryDirectory() as td:
            text = _set_standard_claude(SHIPPED_MODELS.read_text(encoding="utf-8"), "claude-sonnet-5.5")
            findings = _rules(_plugin_fixture(Path(td), text))
        v3 = [f["error"] for f in findings if f["field"] == "V3"]
        self.assertEqual(
            v3,
            ["levels.standard.claude='claude-sonnet-5.5' is not a member of claude_aliases"],
            findings,
        )

    def test_v3_override_outside_claude_aliases(self):
        # `claude-opus-5.5` is the ROUTER's spelling of the model; the Claude
        # Code enum holds `claude-opus-5-5`. The two spellings belong to two
        # surfaces, and the override takes only the Claude Code one.
        with tempfile.TemporaryDirectory() as td:
            findings = _rules(_plugin_fixture(
                Path(td), _set_agent_claude(SHIPPED_MODELS.read_text(encoding="utf-8"),
                                            "commander", "claude-opus-5.5")))
        v3 = [f["error"] for f in findings if f["field"] == "V3"]
        self.assertEqual(
            v3,
            ["agents.commander.claude='claude-opus-5.5' is not a member of claude_aliases"],
            findings,
        )

    # ── V4 ─────────────────────────────────────────────────────────────────
    # Every Codex cell carries the same verification date, so a bare-date
    # anchor would mutate whichever cell comes first. These tests anchor on the
    # apex cell's whole indented row, so each mutates one known cell.
    APEX_VERIFIED = (
        "      model: gpt-6-astra\n"
        "      reasoning_effort: high\n"
        '      verified: "2026-09-22"'
    )

    def _apex_verified(self, text: str, date: str) -> str:
        self.assertIn(self.APEX_VERIFIED, text,
                      "apex Codex cell fixture is inert: the anchored row is absent")
        return text.replace(
            self.APEX_VERIFIED,
            self.APEX_VERIFIED.replace('"2026-09-22"', f'"{date}"'), 1)

    def test_v4_impossible_date(self):
        # Anchored on the INDENTED data row. An unanchored replace matched the
        # first occurrence in the file, which is now inside a header comment
        # that quotes the field — so the fixture silently stopped mutating any
        # data and the rule stopped being tested while the test stayed green.
        self._assert_rule("V4", lambda t: self._apex_verified(t, "2026-99-99"))

    def test_v4_effort_outside_the_enum(self):
        self._assert_rule("V4", lambda t: t.replace("reasoning_effort: high", "reasoning_effort: extreme", 1))

    def test_v4_stale_verified_date_warns_without_failing(self):
        with tempfile.TemporaryDirectory() as td:
            text = self._apex_verified(
                SHIPPED_MODELS.read_text(encoding="utf-8"), "2020-01-01")
            findings = _rules(_plugin_fixture(Path(td), text))
        warnings = [f for f in findings if f.get("severity") == "warning"]
        errors = [f for f in findings if f.get("severity") != "warning"]
        self.assertTrue(warnings, "a stale verified date must warn")
        self.assertEqual(errors, [], "a stale verified date must NOT fail the run")

    # ── V5, three ──────────────────────────────────────────────────────────
    def test_v5_unknown_provider(self):
        self._assert_rule("V5", lambda t: t.replace(_PARKED_ALIAS_ROW, "glm-flash: bogus/kimi"))

    def test_v5_empty_slug_half(self):
        self._assert_rule("V5", lambda t: t.replace(_PARKED_ALIAS_ROW, "glm-flash: openrouter//x"))

    def test_v5_pending_placeholder(self):
        self._assert_rule("V5", lambda t: t.replace(_PARKED_ALIAS_ROW, "glm-flash: PENDING"))

    def test_v5_codex_model_absent_from_the_router_registry(self):
        self._assert_rule("V5", lambda t: t.replace("model: gpt-6.1-sol", "model: gpt-5.6"))

    # ── V6 ─────────────────────────────────────────────────────────────────
    def test_v6_frontmatter_disagrees_with_its_level(self):
        with tempfile.TemporaryDirectory() as td:
            plugin = _plugin_fixture(Path(td))
            _set_agent_model(plugin, "developer", "opus")
            findings = _rules(plugin)
        self.assertIn("V6", _fields(findings))

    def test_v6_compares_an_overridden_agent_with_its_resolved_value(self):
        # commander.md declaring its LEVEL's value is now the defect: the
        # catalog resolves it to the override, not to `fable`.
        with tempfile.TemporaryDirectory() as td:
            plugin = _plugin_fixture(Path(td))
            _set_agent_model(plugin, "commander", "fable")
            findings = _rules(plugin)
        v6 = [f["error"] for f in findings if f["field"] == "V6"]
        self.assertEqual(
            v6,
            ["agents/commander.md declares model='fable' but its resolved Claude Code "
             "value is 'claude-opus-5-5' (agents.commander.claude override)"],
            findings,
        )

    def test_v6_resolves_the_reviewer_from_its_apex_level(self):
        with tempfile.TemporaryDirectory() as td:
            plugin = _plugin_fixture(Path(td))
            _set_agent_model(plugin, "reviewer", "fable")
            findings = _rules(plugin)
        v6 = [f["error"] for f in findings if f["field"] == "V6"]
        self.assertEqual(
            v6,
            ["agents/reviewer.md declares model='fable' but its resolved Claude "
             "Code value is 'claude-opus-5-5' (level 'apex')"],
            findings,
        )

    def test_v6_accepts_an_inherit_override_with_model_inherit(self):
        # Positive and negative on one fixture pair: the override alone trips
        # V6 (the file still says the old value), and the file edit clears it.
        text = _set_agent_claude(SHIPPED_MODELS.read_text(encoding="utf-8"), "night-gardener", "inherit")
        with tempfile.TemporaryDirectory() as td:
            plugin = _plugin_fixture(Path(td), text)
            before = _rules(plugin)
            _set_agent_model(plugin, "night-gardener", "inherit")
            after = _rules(plugin)
        self.assertIn("V6", _fields(before), before)
        self.assertEqual([f for f in after if f.get("severity") != "warning"], [], after)

    # ── V7 ─────────────────────────────────────────────────────────────────
    def test_v7_alias_name_containing_a_slash(self):
        self._assert_rule("V7", lambda t: t.replace(_QWEN_MAX_ROW, "  qwen/max: openrouter/"))

    # ── V8 ─────────────────────────────────────────────────────────────────
    def test_v8_two_rows_differing_only_by_verified(self):
        """The case whole-row equality would miss.

        Copy flagship's values into apex, changing only the verification date.
        The fixture must construct the duplicate even when the shipped tiers
        use different models.
        """
        def transform(t: str) -> str:
            before, rest = t.split("  apex:\n", 1)
            _, flagship = rest.split("  flagship:\n", 1)
            body, _ = flagship.split("  standard:\n", 1)
            duplicate = body.replace('verified: "2026-09-29"',
                                     'verified: "2026-09-28"')
            return before + "  apex:\n" + duplicate + "  flagship:\n" + flagship
        self._assert_rule("V8", transform)

    # ── V9, one per declared position ──────────────────────────────────────
    #
    # `_CONFINED_POSITIONS` names five positions. Only `levels.<level>.opencode`
    # had a test, so a mutant deleting any agent-side branch survived both
    # suites while the rule's own docstring still advertised the position. One
    # test per name, plus the roster KEY, which the rule did not inspect at all.

    def test_v9_raw_model_id_in_a_level_cell(self):
        self._assert_rule("V9", lambda t: re.sub(
            r"^(  flagship:\n    claude: \S+\n    opencode: )\S+$",
            r"\1anthropic/claude-opus-5", t, flags=re.M))

    def test_v9_raw_model_id_in_levels_claude(self):
        self._assert_rule("V9", lambda t: t.replace(
            _STANDARD_CLAUDE_CELL, "  standard:\n    claude: anthropic/claude-sonnet-5"))

    def test_v9_raw_model_id_in_a_scalar_agent_row(self):
        self._assert_rule("V9", lambda t: t.replace(
            "  brainstormer: flagship", "  brainstormer: anthropic/claude-opus-5"))

    def test_v9_raw_model_id_in_agents_level(self):
        self._assert_rule("V9", lambda t: t.replace(
            "  brainstormer: flagship", "  brainstormer:\n    level: anthropic/x"))

    def test_v9_raw_model_id_in_agents_claude(self):
        self._assert_rule("V9", lambda t: _set_agent_claude(t, "commander", "anthropic/claude-opus-5.5"))

    def test_v9_raw_model_id_in_agents_opencode(self):
        self._assert_rule("V9", lambda t: re.sub(
            r"^(  commander:\n    level: \S+\n(?:    (?!opencode:)\S.*\n)*    opencode: )\S+$",
            r"\1anthropic/claude-opus-4-8", t, flags=re.M))

    def test_v9_declares_exactly_the_positions_it_inspects(self):
        # The docstring is load-bearing here: it is what a later reader trusts
        # when deciding whether a new position needs a branch.
        self.assertEqual(
            [p.strip() for p in validator._CONFINED_POSITIONS.split(",")],
            [
                "agents.<name>",
                "agents.<name>.level",
                "agents.<name>.opencode",
                "agents.<name>.claude",
                "levels.<level>.claude",
                "levels.<level>.opencode",
            ],
        )


class AgentCodexOverrideValidationTests(unittest.TestCase):
    """Apply Codex value rules to agent overrides and level defaults."""

    REVIEWER_CODEX = (
        "  reviewer:\n"
        "    level: apex\n"
        "    opencode: sol-latest\n"
        "    codex:\n"
        "      model: gpt-6.1-sol\n"
        "      reasoning_effort: xhigh\n"
        '      verified: "2026-09-29"\n'
        '      source: "OpenAI GPT-6.1 Sol model documentation and Codex 0.159.0 models_cache.json checked 2026-09-29"'
    )

    def _findings(self, transform) -> list[dict]:
        original = SHIPPED_MODELS.read_text(encoding="utf-8")
        mutated = transform(original)
        self.assertNotEqual(
            mutated,
            original,
            "agent Codex override fixture is inert: the reviewer override changed shape",
        )
        with tempfile.TemporaryDirectory() as td:
            findings = _rules(_plugin_fixture(Path(td), mutated))
        return findings

    def _replace_override(self, original: str, replacement: str) -> str:
        self.assertIn(
            self.REVIEWER_CODEX,
            original,
            "reviewer Codex override fixture is inert: expected reviewer override is absent",
        )
        return original.replace(self.REVIEWER_CODEX, replacement, 1)

    def test_v4_rejects_agent_override_effort_outside_the_enum(self):
        findings = self._findings(
            lambda t: self._replace_override(
                t, self.REVIEWER_CODEX.replace("reasoning_effort: xhigh", "reasoning_effort: extreme")
            )
        )
        self.assertIn("V4", _fields(findings), findings)

    def test_v4_rejects_agent_override_empty_source(self):
        findings = self._findings(
            lambda t: self._replace_override(
                t,
                self.REVIEWER_CODEX.replace(
                    'source: "OpenAI GPT-6.1 Sol model documentation and Codex 0.159.0 models_cache.json checked 2026-09-29"',
                    'source: ""',
                ),
            )
        )
        self.assertIn("V4", _fields(findings), findings)

    def test_v4_rejects_agent_override_impossible_verified_date(self):
        findings = self._findings(
            lambda t: self._replace_override(
                t, self.REVIEWER_CODEX.replace('verified: "2026-09-29"', 'verified: "2026-99-99"')
            )
        )
        self.assertIn("V4", _fields(findings), findings)

    def test_v4_rejects_agent_override_future_verified_date(self):
        findings = self._findings(
            lambda t: self._replace_override(
                t, self.REVIEWER_CODEX.replace('verified: "2026-09-29"', 'verified: "2999-01-01"')
            )
        )
        self.assertIn("V4", _fields(findings), findings)

    def test_v4_warns_for_agent_override_stale_verified_date(self):
        findings = self._findings(
            lambda t: self._replace_override(
                t, self.REVIEWER_CODEX.replace('verified: "2026-09-29"', 'verified: "2020-01-01"')
            )
        )
        warnings = [f for f in findings if f.get("severity") == "warning"]
        errors = [f for f in findings if f.get("severity") != "warning"]
        self.assertTrue(warnings, findings)
        self.assertEqual(errors, [], findings)

    def test_v5_rejects_agent_override_model_absent_from_router_registry(self):
        findings = self._findings(
            lambda t: self._replace_override(
                t, self.REVIEWER_CODEX.replace("model: gpt-6.1-sol", "model: gpt-5.6")
            )
        )
        self.assertIn("V5", _fields(findings), findings)


class RosterKeyConfinementTests(unittest.TestCase):
    """[SECURITY:S3] A roster KEY reaches the filesystem, so it must be confined.

    V6 builds a read path straight from an `agents` key:
    `plugin_dir / "agents" / f"{agent_name}.md"`. An absolute key escapes
    outright — `pathlib` resets on an absolute component — and a `..` key walks
    up. The content of whatever `.md` file that names is then interpolated into
    a V6 finding and printed as validator stdout JSON, so a mis-keyed catalog
    becomes a file-disclosure primitive.

    Two independent repairs, tested independently, because either alone leaves
    a hole: V9 must reject the key, and V6 must not run at all once V1/V2 have
    findings (V1 is what notices that the key names no agent file).
    """

    ABSOLUTE_KEY = "  /etc/hosts: standard\n"

    def _findings(self, transform) -> list[dict]:
        """Run a text-transform fixture against the shipped catalog.

        The same landed-mutation guard `ModelsCatalogNegativeTests._assert_rule`
        carries: `str.replace` NO-OPS SILENTLY when its anchor string is gone,
        which would leave the tests below validating the pristine catalog and
        reporting whatever that reports — green forever, testing nothing.
        Assert the mutation landed, so a stale anchor fails loudly instead of
        disarming its own test. The absolute/traversing key fixtures re-anchor
        on `  wayfinder: standard`, so re-anchoring is a one-line edit here.
        """
        original = SHIPPED_MODELS.read_text(encoding="utf-8")
        mutated = transform(original)
        self.assertTrue(
            mutated != original,
            "a RosterKeyConfinement fixture is inert: its anchor no longer occurs "
            "in catalog/models.yml",
        )
        with tempfile.TemporaryDirectory() as td:
            plugin = _plugin_fixture(Path(td), mutated)
            return _rules(plugin)

    def test_v9_rejects_an_absolute_roster_key(self):
        findings = self._findings(lambda t: t.replace("  wayfinder: standard", "  wayfinder: standard\n" + self.ABSOLUTE_KEY))
        self.assertIn("V9", _fields(findings))

    def test_v9_rejects_a_traversing_roster_key(self):
        findings = self._findings(lambda t: t.replace(
            "  wayfinder: standard", '  wayfinder: standard\n  "../../etc/hosts": standard\n'))
        self.assertIn("V9", _fields(findings))

    def test_v9_rejects_a_dotfile_roster_key(self):
        findings = self._findings(lambda t: t.replace(
            "  wayfinder: standard", '  wayfinder: standard\n  ".hidden": standard\n'))
        self.assertIn("V9", _fields(findings))

    def test_v9_rejects_a_backslash_roster_key(self):
        findings = self._findings(lambda t: t.replace(
            "  wayfinder: standard", '  wayfinder: standard\n  "a\\\\b": standard\n'))
        self.assertIn("V9", _fields(findings))

    def test_v6_does_not_run_once_v1_has_findings(self):
        # The gating half. V1 fires on the unmatched roster key; V6 must not
        # then read a path built from it.
        findings = self._findings(lambda t: t.replace("  wayfinder: standard", "  wayfinder: standard\n" + self.ABSOLUTE_KEY))
        self.assertIn("V1", _fields(findings))
        self.assertNotIn("V6", _fields(findings), "V6 read a path built from an unvalidated key")

    def test_v6_does_not_run_once_v2_has_findings(self):
        findings = self._findings(_undeclare_developer_opencode)
        self.assertIn("V2", _fields(findings))
        self.assertNotIn("V6", _fields(findings))

    def test_no_finding_quotes_the_contents_of_the_named_file(self):
        # The disclosure itself: even with the gate, assert no rule echoes the
        # bytes of a file an attacker chose.
        findings = self._findings(lambda t: t.replace("  wayfinder: standard", "  wayfinder: standard\n" + self.ABSOLUTE_KEY))
        blob = json.dumps(findings)
        self.assertNotIn("localhost", blob, "a V6 finding disclosed the target file's contents")

    def test_a_symlinked_agent_leaf_is_caught_by_the_containment_layer(self):
        """Layer 3's proof, and the reason its branch carries no `no cover`.

        A symlinked `agents/<role>.md` pointing outside the directory defeats
        the other two layers by construction: the roster key is a bare name,
        so V9 has nothing to object to, and the link resolves to a real file,
        so V1's bijection holds and the [SECURITY:S3] gate stays OPEN. Only
        `_agent_md_path`'s resolve-then-contain re-check sees it. The branch
        was marked `# pragma: no cover - V9 + the gate make this unreachable`,
        which was false and told a future reader the layer was removable.
        """
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            plugin = _plugin_fixture(tmp)
            victim = tmp / "outside.md"
            victim.write_text(
                "---\nname: wayfinder\nmodel: LEAKED-FROM-OUTSIDE\n---\nbody\n", encoding="utf-8"
            )
            leaf = plugin / "agents" / "wayfinder.md"
            leaf.unlink()
            leaf.symlink_to(victim)

            findings = _rules(plugin)

        # V1 is silent — the bijection holds, so the gate is open and V6 runs.
        self.assertNotIn("V1", _fields(findings), "the gate must be OPEN for this to prove anything")
        v6 = [f for f in findings if f["field"] == "V6"]
        self.assertEqual(len(v6), 1, findings)
        self.assertIn("does not resolve INSIDE agents/", v6[0]["error"])
        # And the containment refusal happens BEFORE the read, so nothing from
        # the link's target reaches the validator's stdout JSON.
        self.assertNotIn("LEAKED-FROM-OUTSIDE", json.dumps(findings))


class AgentLeafSymlinkTests(unittest.TestCase):
    """[SECURITY:S5] `extract_agent_frontmatter` refuses a symlinked leaf.

    The sibling the fail-closed sweep surfaced on its last leg: this reader's
    output is written into the GENERATED `catalog/agents.json`, so a planted
    link would publish out-of-tree frontmatter into a committed artifact. The
    refusal covers a link pointing INSIDE `agents/` as well, which
    `_agent_md_path`'s containment check permits by design.
    """

    def test_a_symlinked_agent_file_is_an_error_not_a_parse(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            victim = tmp / "outside.md"
            victim.write_text("---\nname: x\nmodel: LEAKED\n---\nbody\n", encoding="utf-8")
            leaf = tmp / "wayfinder.md"
            leaf.symlink_to(victim)
            fm, err = validator.extract_agent_frontmatter(leaf)
        self.assertEqual(fm, {})
        self.assertIsNotNone(err)
        self.assertIn("symlink", err)

    def test_an_inside_agents_symlink_is_refused_too(self):
        # Containment alone would allow this one: it resolves inside agents/.
        with tempfile.TemporaryDirectory() as td:
            plugin = _plugin_fixture(Path(td))
            leaf = plugin / "agents" / "wayfinder.md"
            leaf.unlink()
            leaf.symlink_to(plugin / "agents" / "librarian.md")
            fm, err = validator.extract_agent_frontmatter(leaf)
        self.assertEqual(fm, {})
        self.assertIn("symlink", err)

    def test_a_regular_agent_file_still_parses(self):
        with tempfile.TemporaryDirectory() as td:
            plugin = _plugin_fixture(Path(td))
            fm, err = validator.extract_agent_frontmatter(plugin / "agents" / "wayfinder.md")
        self.assertIsNone(err, err)
        self.assertEqual(fm.get("name"), "wayfinder")

    def test_discover_agents_still_lists_the_symlink_rather_than_dropping_it(self):
        # Dropping it in discovery would silently NARROW agents.json, which is
        # a worse failure than a loud per-file error.
        with tempfile.TemporaryDirectory() as td:
            plugin = _plugin_fixture(Path(td))
            leaf = plugin / "agents" / "wayfinder.md"
            leaf.unlink()
            leaf.symlink_to(plugin / "agents" / "librarian.md")
            found = {p.stem for p in validator.discover_agents(plugin)}
        self.assertIn("wayfinder", found)


class AliasPatternAnchorTests(unittest.TestCase):
    """The alias patterns end at `\\Z`, not `$`.

    In Python `$` also matches immediately before a trailing newline, so
    `"openrouter/x\\n"` satisfied V5 and V7 and was accepted as a well-formed
    alias value. That value is then written into a generated agent file's
    `model:` line, where the embedded newline ends the line and turns whatever
    follows into a new frontmatter key.
    """

    def test_a_trailing_newline_does_not_satisfy_the_slug_pattern(self):
        self.assertIsNone(validator.ALIAS_SLUG_RE.match("x\n"))
        self.assertIsNotNone(validator.ALIAS_SLUG_RE.match("x"))

    def test_a_trailing_newline_does_not_satisfy_the_alias_name_pattern(self):
        self.assertIsNone(validator.ALIAS_NAME_RE.match("opus-x\n"))
        self.assertIsNotNone(validator.ALIAS_NAME_RE.match("opus-x"))

    def test_a_trailing_newline_does_not_satisfy_the_provider_pattern(self):
        self.assertIsNone(validator.ALIAS_PROVIDER_RE.match("anthropic\n"))
        self.assertIsNotNone(validator.ALIAS_PROVIDER_RE.match("anthropic"))

    def test_v5_reports_an_alias_value_with_a_trailing_newline(self):
        # The end-to-end case the review named: `opus-x: "openrouter/x\\n"`.
        #
        # The provider half must be a LIVE member of the providers table. With a
        # retired provider there, V5 fires on "provider not in the table" and the
        # assertion below passes without the slug pattern ever being consulted —
        # green, and blind to the `\Z` anchor this class exists to pin.
        anchor = "  opus-latest: openrouter/anthropic/claude-opus-5.5\n"
        original = SHIPPED_MODELS.read_text(encoding="utf-8")
        # `assertNotEqual(text, original)` would pass on almost any mutation
        # of the fixture, including one that landed nowhere near `anchor` —
        # so pin the specific behavior this fixture depends on: `anchor`
        # appears exactly once (the replace below is unambiguous), and the
        # injected line lands immediately before it.
        self.assertEqual(
            original.count(anchor), 1,
            "fixture is inert: anchor must appear exactly once in models.yml",
        )
        injected = '  opus-x: "openrouter/x\\n"\n'
        text = original.replace(anchor, injected + anchor, 1)
        self.assertIn(
            injected + anchor, text, "fixture injection did not land as expected"
        )
        with tempfile.TemporaryDirectory() as td:
            plugin = _plugin_fixture(Path(td), text)
            findings = _rules(plugin)
        self.assertIn("V5", _fields(findings), findings)
        self.assertTrue(
            any(f["field"] == "V5" and "opus-x" in f["error"] for f in findings), findings
        )


class CatalogTargetsTests(unittest.TestCase):
    """CATALOG_TARGETS is the one declaration of the validator's targets."""

    def test_required_catalog_json_is_declared_in_shipped_code(self):
        """CHK-CAT-1 limb (b) must be derivable by a target repo.

        The audit rule used to derive its required-.json list from the
        repo-root AGENTS.md roster — a file that exists in THIS repo and in no
        target repo, so the guarantee degraded to nothing downstream. The
        enumeration ships in code beside CATALOG_TARGETS instead.
        """
        self.assertEqual(
            validator.REQUIRED_CATALOG_JSON, ("agents.json", "rules.json", "skills.json")
        )
        on_disk = {p.name for p in CATALOG_DIR.glob("*.json")}
        self.assertEqual(set(validator.REQUIRED_CATALOG_JSON) | set(validator.AUTHORED_CATALOG_JSON), on_disk)

    def test_names_every_hand_authored_catalog_file(self):
        on_disk = {p.name for p in CATALOG_DIR.glob("*.yml")}
        self.assertEqual(set(validator.CATALOG_TARGETS), on_disk)

    def test_every_target_is_callable(self):
        for name, fn in validator.CATALOG_TARGETS.items():
            with self.subTest(target=name):
                self.assertTrue(callable(fn))


class BundlesYmlTests(unittest.TestCase):
    """bundles.yml: the mapping-keyed shape, the reconstitution, and the floor."""

    def _skill_ids(self) -> set[str]:
        return {e["id"] for e in json.loads((CATALOG_DIR / "skills.json").read_text(encoding="utf-8"))}

    def test_shipped_bundles_validate_clean(self):
        bundles, errors = validator.validate_bundles_yml(SHIPPED_BUNDLES, self._skill_ids())
        self.assertEqual(errors, [])
        self.assertIsNotNone(bundles)

    def test_reconstituted_shape_is_the_historical_list_of_objects(self):
        bundles, _ = validator.validate_bundles_yml(SHIPPED_BUNDLES, self._skill_ids())
        for bundle in bundles:
            self.assertEqual(set(bundle), validator.BUNDLE_REQUIRED)
            self.assertIsInstance(bundle["id"], str)
            self.assertIsInstance(bundle["default_provision"], bool)

    def test_missing_file_is_an_error_not_an_empty_result(self):
        # The predecessor returned ([], []) for an absent file. Under a rename
        # that is a fail-open: a consumer left on the old path yields zero
        # bundles and a green validator.
        with tempfile.TemporaryDirectory() as td:
            bundles, errors = validator.validate_bundles_yml(Path(td) / "bundles.yml", set())
        self.assertIsNone(bundles)
        self.assertTrue(errors)

    def test_top_level_sequence_is_refused_rather_than_read_as_empty(self):
        # The defect that forced the mapping-keyed shape: the local load_yaml
        # ends `return data if isinstance(data, dict) else {}`, so a top-level
        # sequence parsed to an empty mapping SILENTLY on both paths.
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bundles.yml"
            path.write_text("- id: crux-core\n  name: x\n", encoding="utf-8")
            bundles, errors = validator.validate_bundles_yml(path, set())
        self.assertIsNone(bundles)
        self.assertTrue(errors)

    def test_dangling_skill_id_is_caught(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bundles.yml"
            path.write_text(
                'crux-core:\n  name: "n"\n  description: "d"\n  audiences:\n    - "a"\n'
                "  default_provision: false\n  skills:\n    - \"no-such-skill\"\n",
                encoding="utf-8",
            )
            _, errors = validator.validate_bundles_yml(path, {"real-skill"})
        self.assertTrue(any("no-such-skill" in e["error"] for e in errors))

    def test_repeated_id_inside_the_value_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bundles.yml"
            path.write_text(
                'crux-core:\n  id: "crux-core"\n  name: "n"\n  description: "d"\n'
                '  audiences:\n    - "a"\n  default_provision: false\n  skills: []\n',
                encoding="utf-8",
            )
            _, errors = validator.validate_bundles_yml(path, set())
        self.assertTrue(any(e["field"] == "id" for e in errors))


class BundlesYmlParityTests(unittest.TestCase):
    """The shipped bundles.yml parses identically on both paths.

    Parity is proven PER FILE, never inherited from models.yml's proof: the two
    files exercise different corners of the pinned subset.
    """

    def test_minimal_parser_matches_pyyaml_on_the_shipped_file(self):
        text = SHIPPED_BUNDLES.read_text(encoding="utf-8")
        yaml_min = validator._yaml_min
        via_pyyaml = yaml_min.load_catalog_yaml(text)
        via_minimal = yaml_min._normalize(yaml_min._parse_minimal_yaml(text, strict=True))
        self.assertEqual(via_pyyaml, via_minimal)

    def test_boolean_survives_on_both_paths(self):
        text = SHIPPED_BUNDLES.read_text(encoding="utf-8")
        yaml_min = validator._yaml_min
        for label, parsed in (
            ("pyyaml", yaml_min.load_catalog_yaml(text)),
            ("minimal", yaml_min._normalize(yaml_min._parse_minimal_yaml(text, strict=True))),
        ):
            with self.subTest(path=label):
                for bundle in parsed.values():
                    self.assertIsInstance(bundle["default_provision"], bool)
