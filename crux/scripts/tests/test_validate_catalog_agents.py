"""Tests for the agents-catalog logic in validate-catalog.py (ADR-0028).

The skills path has its own coverage (test_validate_catalog.py + the catalog
regeneration workflow). This module exercises the NEW agent-catalog path:
discovery, frontmatter extraction, per-agent validation, deterministic
agents.json serialization, and byte-stability / dry-run cleanliness of the
real on-disk catalog.

Agent frontmatter intentionally differs from SKILL.md: `tools` and `model`
are top-level loader keys (not crux extended fields), so the ADR-0005
`_lift_metadata` drift-check does NOT apply here. Agent frontmatter MUST be
valid YAML (per PB-0039): both Claude Code and OpenCode load it as YAML, so
`extract_agent_frontmatter` enforces a strict-YAML gate when PyYAML is present
(the minimal line-based parser still does the extraction for the shipped
no-PyYAML runtime). A description containing a literal `: ` (colon-space) must
be quoted — an unquoted mid-value colon is rejected (it once shipped an
unloadable night-gardener agent).

Stdlib only (unittest, importlib, tempfile, pathlib, json, sys).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:  # no-PyYAML runtime: the strict-YAML gate can't fire
    HAVE_YAML = False

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "crux" / "scripts" / "validate-catalog.py"
PLUGIN_DIR = REPO_ROOT / "crux"


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_catalog_agents_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


validator = _load_validator()


VALID_AGENT = """\
---
name: {name}
description: Use when the user wants X. Triggers — "do this", "do that".
tools: Read, Grep, Glob, Skill
model: sonnet
skills: []
metadata:
  tags: "agents, retrieval"
  bundles: "crux-agents"
  risk_level: "low"
---

# {name}

Body text.
"""


# The `model:` enum is no longer a literal in validate-catalog.py: it is
# `claude_aliases` in catalog/models.yml, read by the validator, the smoke test,
# and rule V3. Fixtures state it explicitly rather than copying the whole
# catalog into every tmpdir.
ALLOWED_MODELS = {"fable", "opus", "sonnet", "haiku", "inherit"}


def _write_agent(tmp: Path, stem: str, body: str) -> Path:
    agents_dir = tmp / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / f"{stem}.md"
    path.write_text(body, encoding="utf-8")
    return path


def _write_skill(tmp: Path, name: str) -> None:
    skill_dir = tmp / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")


class AgentDiscoveryTests(unittest.TestCase):
    def test_discover_agents_returns_sorted_md_files(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _write_agent(tmp, "zeta", VALID_AGENT.format(name="zeta"))
            _write_agent(tmp, "alpha", VALID_AGENT.format(name="alpha"))
            # A non-.md file must be ignored.
            (tmp / "agents" / "README.txt").write_text("ignore me", encoding="utf-8")
            found = validator.discover_agents(tmp)
            self.assertEqual([p.stem for p in found], ["alpha", "zeta"])

    def test_discover_agents_missing_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(validator.discover_agents(Path(t)), [])


class AgentValidationTests(unittest.TestCase):
    def _validate(self, tmp: Path, stem: str, body: str, skill_ids=frozenset()):
        path = _write_agent(tmp, stem, body)
        fm, err = validator.extract_agent_frontmatter(path)
        self.assertIsNone(err, msg=f"unexpected parse error: {err}")
        return validator.validate_agent_frontmatter(fm, path, tmp, ALLOWED_MODELS, skill_ids)

    def test_valid_agent_passes(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            entry, errors = self._validate(tmp, "librarian", VALID_AGENT.format(name="librarian"))
            self.assertEqual(errors, [], msg=f"expected no errors, got {errors}")
            self.assertIsNotNone(entry)
            self.assertEqual(entry["id"], "librarian")
            self.assertEqual(entry["name"], "librarian")
            self.assertEqual(entry["model"], "sonnet")
            # tools/tags/bundles are split into lists.
            self.assertEqual(entry["tools"], ["Read", "Grep", "Glob", "Skill"])
            self.assertEqual(entry["tags"], ["agents", "retrieval"])
            self.assertEqual(entry["bundles"], ["crux-agents"])
            self.assertEqual(entry["risk_level"], "low")
            self.assertEqual(entry["skills"], [])
            # owner/version/status pruned (ADR-0092); no invocation keys on this
            # fixture, so the entry carries exactly the always-present keys.
            self.assertNotIn("status", entry)
            self.assertEqual(
                set(entry.keys()),
                {"id", "name", "description", "tools", "model", "tags", "bundles", "risk_level", "skills"},
            )

    def test_bad_model_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            body = VALID_AGENT.format(name="librarian").replace("model: sonnet", "model: gpt5")
            entry, errors = self._validate(tmp, "librarian", body)
            self.assertTrue(any(e["field"] == "model" for e in errors), msg=errors)
            # model error is non-fatal (entry still builds), but it IS an error.
            self.assertTrue(errors)

    def test_name_not_matching_stem_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            # File stem is "librarian" but name says "historian".
            body = VALID_AGENT.format(name="historian")
            entry, errors = self._validate(tmp, "librarian", body)
            self.assertTrue(
                any(e["field"] == "name" and "does not match filename stem" in e["error"] for e in errors),
                msg=errors,
            )

    def test_missing_metadata_field_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            # Drop the required `risk_level` line from metadata (ADR-0092 pruned
            # owner/version/status; the required set is tags/bundles/risk_level).
            body = VALID_AGENT.format(name="librarian").replace('  risk_level: "low"\n', "")
            path = _write_agent(tmp, "librarian", body)
            fm, err = validator.extract_agent_frontmatter(path)
            self.assertIsNone(err)
            entry, errors = validator.validate_agent_frontmatter(fm, path, tmp, ALLOWED_MODELS, frozenset())
            self.assertTrue(
                any(e["field"] == "metadata.risk_level" for e in errors),
                msg=errors,
            )
            # Missing required metadata key is fatal -> no entry built.
            self.assertIsNone(entry)

    def test_lingering_pruned_metadata_key_is_rejected(self):
        """ADR-0092: a leftover owner/version/status under metadata is now an
        unknown-metadata-key error (strict contract extended to agents)."""
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            body = VALID_AGENT.format(name="librarian").replace(
                '  risk_level: "low"\n', '  risk_level: "low"\n  status: "production"\n'
            )
            entry, errors = self._validate(tmp, "librarian", body)
            self.assertTrue(
                any(e["field"] == "metadata.status" and "unknown metadata key" in e["error"]
                    for e in errors),
                msg=errors,
            )

    def test_missing_metadata_mapping_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            body = (
                "---\n"
                "name: librarian\n"
                "description: A read-only retrieval agent.\n"
                "tools: Read, Grep\n"
                "model: sonnet\n"
                "---\n\n# librarian\n"
            )
            path = _write_agent(tmp, "librarian", body)
            fm, err = validator.extract_agent_frontmatter(path)
            self.assertIsNone(err)
            entry, errors = validator.validate_agent_frontmatter(fm, path, tmp, ALLOWED_MODELS, frozenset())
            self.assertTrue(any(e["field"] == "metadata" for e in errors), msg=errors)
            self.assertIsNone(entry)

    def test_bad_risk_level_enum_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            body = VALID_AGENT.format(name="librarian").replace(
                'risk_level: "low"', 'risk_level: "extreme"'
            )
            entry, errors = self._validate(tmp, "librarian", body)
            self.assertTrue(
                any(e["field"] == "metadata.risk_level" for e in errors),
                msg=errors,
            )

    def test_unknown_toplevel_key_reported(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            body = VALID_AGENT.format(name="librarian").replace(
                "model: sonnet\n", "model: sonnet\ncolor: blue\n"
            )
            entry, errors = self._validate(tmp, "librarian", body)
            self.assertTrue(
                any(e["field"] == "color" and "unknown top-level" in e["error"] for e in errors),
                msg=errors,
            )

    def test_missing_tools_is_fatal(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            body = VALID_AGENT.format(name="librarian").replace(
                "tools: Read, Grep, Glob, Skill\n", ""
            )
            path = _write_agent(tmp, "librarian", body)
            fm, err = validator.extract_agent_frontmatter(path)
            self.assertIsNone(err)
            entry, errors = validator.validate_agent_frontmatter(fm, path, tmp, ALLOWED_MODELS, frozenset())
            self.assertTrue(any(e["field"] == "tools" for e in errors), msg=errors)
            self.assertIsNone(entry)

    @unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
    def test_unquoted_midvalue_colon_rejected(self):
        """An unquoted mid-value `: ` (colon-space) is invalid YAML and MUST be
        rejected (PB-0039). Both Claude Code and OpenCode load agent frontmatter
        as YAML; the lenient minimal parser previously let an unloadable file ship
        (night-gardener). The strict gate now fires when PyYAML is available."""
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            body = VALID_AGENT.format(name="developer").replace(
                'Triggers — "do this", "do that".',
                "and audit-docs (graph integrity): the gardener is generative.",
            )
            path = _write_agent(tmp, "developer", body)
            fm, err = validator.extract_agent_frontmatter(path)
            self.assertIsNotNone(err, msg="invalid YAML frontmatter must be rejected")
            self.assertIn("YAML", err)

    def test_quoted_colon_in_description_ok(self):
        """The escape hatch: a description containing a colon is valid when the
        value is quoted."""
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            body = VALID_AGENT.format(name="developer").replace(
                'description: Use when the user wants X. Triggers — "do this", "do that".',
                'description: "Use when X. Triggers: do this, do that."',
            )
            entry, errors = self._validate(tmp, "developer", body)
            self.assertEqual(errors, [], msg=errors)
            self.assertIn("Triggers:", entry["description"])


class AgentSkillsSourceParsingTests(unittest.TestCase):
    """Catalog parsing enforces the same inline `skills:` contract as Codex."""

    def _parse(self, tmp: Path, skills_line: str):
        body = VALID_AGENT.format(name="developer").replace("skills: []\n", skills_line)
        return validator.extract_agent_frontmatter(_write_agent(tmp, "developer", body))

    def test_missing_skills_is_rejected_by_validation(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            frontmatter, error = self._parse(tmp, "")
            self.assertIsNone(error, error)
            _, errors = validator.validate_agent_frontmatter(
                frontmatter, tmp / "agents" / "developer.md", tmp, ALLOWED_MODELS, set()
            )
        self.assertTrue(any(e["field"] == "skills" and "missing required" in e["error"] for e in errors))

    def test_duplicate_skill_list_entry_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _write_skill(tmp, "forge-skill")
            frontmatter, error = self._parse(tmp, "skills: [forge-skill, forge-skill]\n")
            self.assertIsNone(error, error)
            _, errors = validator.validate_agent_frontmatter(
                frontmatter, tmp / "agents" / "developer.md", tmp, ALLOWED_MODELS, {"forge-skill"}
            )
        self.assertTrue(any(e["field"] == "skills" and "duplicate" in e["error"] for e in errors))

    def test_quoted_flow_skill_strings_are_accepted(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _write_skill(tmp, "forge-skill")
            _write_skill(tmp, "log-work")
            frontmatter, error = self._parse(tmp, "skills: [\"forge-skill\", 'log-work']\n")
            self.assertIsNone(error, error)
            entry, errors = validator.validate_agent_frontmatter(
                frontmatter, tmp / "agents" / "developer.md", tmp, ALLOWED_MODELS,
                {"forge-skill", "log-work"},
            )
        self.assertEqual(errors, [], errors)
        self.assertEqual(entry["skills"], ["forge-skill", "log-work"])

    def test_flow_skills_allow_a_yaml_comment_after_the_list(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _write_skill(tmp, "forge-skill")
            frontmatter, error = self._parse(tmp, "skills: [forge-skill] # required workflow\n")
            self.assertIsNone(error, error)
            entry, errors = validator.validate_agent_frontmatter(
                frontmatter, tmp / "agents" / "developer.md", tmp, ALLOWED_MODELS, {"forge-skill"}
            )
        self.assertEqual(errors, [], errors)
        self.assertEqual(entry["skills"], ["forge-skill"])

    def test_hash_inside_quoted_skill_name_is_not_treated_as_a_comment(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _write_skill(tmp, "forge#skill")
            frontmatter, error = self._parse(tmp, "skills: ['forge#skill'] # annotation\n")
            self.assertIsNone(error, error)
            _, errors = validator.validate_agent_frontmatter(
                frontmatter, tmp / "agents" / "developer.md", tmp, ALLOWED_MODELS, {"forge#skill"}
            )
        self.assertTrue(any(e["field"] == "skills" and "invalid skill name" in e["error"] for e in errors))

    def test_empty_flow_skill_element_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            frontmatter, error = self._parse(Path(t), "skills: [forge-skill,, log-work]\n")
        self.assertEqual(frontmatter, {})
        self.assertIsNotNone(error)
        self.assertIn("empty", error)

    def test_duplicate_top_level_skills_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            frontmatter, error = self._parse(
                Path(t), "skills: [forge-skill]\nskills: [log-work]\n"
            )
        self.assertEqual(frontmatter, {})
        self.assertEqual(error, "duplicate top-level skills key")

    def test_invalid_skill_name_is_rejected_even_if_a_resource_exists(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _write_skill(tmp, "invalid skill")
            frontmatter, error = self._parse(tmp, "skills: ['invalid skill']\n")
            self.assertIsNone(error, error)
            _, errors = validator.validate_agent_frontmatter(
                frontmatter, tmp / "agents" / "developer.md", tmp, ALLOWED_MODELS,
                {"invalid skill"},
            )
        self.assertTrue(any(e["field"] == "skills" and "invalid skill name" in e["error"] for e in errors))


class AgentInvocationKeyValidatorTests(unittest.TestCase):
    """ADR-0092 items 2/3: the agent invocation-control validator type-checks.

    These call `_validate_agent_invocation_keys` directly (validate-catalog.py)
    with a bad value and assert the field surfaces in errors — the negative
    counterpart of test_validate_catalog.py's InvocationControlAgentTests
    positive/mixed cases. `disallowedTools` and `effort` had no negative
    coverage on their own validator branches before this."""

    def test_disallowed_tools_non_list_non_string_rejected(self):
        # The validator's OWN type-check (distinct from the projection test in
        # DisallowedToolsProjectionTests): neither a string nor a list of strings.
        errs: list[dict] = []
        validator._validate_agent_invocation_keys({"disallowedTools": 5}, "a", set(), errs)
        validator._validate_agent_invocation_keys({"disallowedTools": [1, 2]}, "a", set(), errs)
        validator._validate_agent_invocation_keys(
            {"disallowedTools": {"nope": True}}, "a", set(), errs
        )
        self.assertEqual([e["field"] for e in errs], ["disallowedTools"] * 3)

    def test_disallowed_tools_string_and_list_of_strings_accepted(self):
        errs: list[dict] = []
        validator._validate_agent_invocation_keys(
            {"disallowedTools": "Bash, Read"}, "a", set(), errs
        )
        validator._validate_agent_invocation_keys(
            {"disallowedTools": ["Bash", "Read"]}, "a", set(), errs
        )
        self.assertEqual(errs, [])

    def test_effort_invalid_enum_is_an_error_not_a_warning(self):
        # The `effort not in EFFORT_LEVELS` error branch. The in-enum path emits
        # a Claude-only-effective WARNING (tested elsewhere); an out-of-enum
        # value is a hard error with no severity marker.
        errs: list[dict] = []
        validator._validate_agent_invocation_keys({"effort": "turbo"}, "a", set(), errs)
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0]["field"], "effort")
        self.assertNotEqual(errs[0].get("severity"), "warning")


def _agent_with(name: str, extra_lines: str) -> str:
    """A VALID_AGENT variant with extra top-level frontmatter lines inserted
    after the `model:` line (i.e. still inside the frontmatter block)."""
    source = VALID_AGENT.format(name=name)
    if extra_lines.startswith("skills:"):
        source = source.replace("skills: []\n", "")
    return source.replace(
        "model: sonnet\n", "model: sonnet\n" + extra_lines
    )


class BlockStyleProjectionKeyTests(unittest.TestCase):
    """ADR-0092 (F-b): block-style YAML lists for the projection-read agent keys
    (`disallowedTools`, `skills`) are rejected at the validator.

    The OpenCode/Codex projections read these keys with a same-line regex, so a
    block-style list — items on the following lines — parses to the same Python
    list a flow list does, validates clean, yet mis-projects (no deny entry; the
    `- item` lines are left orphaned in the generated agent file) and the drift
    gate cannot catch it. The authored contract is inline; the validator rejects
    the block-style form."""

    def _validate(self, tmp: Path, stem: str, body: str, skill_ids=frozenset()):
        path = _write_agent(tmp, stem, body)
        fm, err = validator.extract_agent_frontmatter(path)
        self.assertIsNone(err, msg=f"unexpected parse error: {err}")
        return validator.validate_agent_frontmatter(fm, path, tmp, ALLOWED_MODELS, skill_ids)

    def test_block_style_disallowed_tools_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            body = _agent_with("developer", "disallowedTools:\n  - Bash\n  - Read\n")
            _, errors = self._validate(tmp, "developer", body)
            self.assertTrue(
                any(e["field"] == "disallowedTools" and "block-style" in e["error"]
                    for e in errors),
                msg=errors,
            )

    def test_block_style_skills_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            body = _agent_with("developer", "skills:\n  - query-docs\n")
            _, errors = self._validate(tmp, "developer", body, skill_ids={"query-docs"})
            self.assertTrue(
                any(e["field"] == "skills" and "block-style" in e["error"] for e in errors),
                msg=errors,
            )

    def test_inline_flow_list_disallowed_tools_still_passes(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            body = _agent_with("developer", 'disallowedTools: ["Bash", "Read"]\n')
            _, errors = self._validate(tmp, "developer", body)
            self.assertEqual(
                [e for e in errors if e["field"] == "disallowedTools"], [], msg=errors
            )

    def test_inline_flow_list_skills_still_passes(self):
        # This is exactly how the shipped agents author `skills:`; it must not trip.
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _write_skill(tmp, "query-docs")
            body = _agent_with("developer", "skills: [query-docs]\n")
            _, errors = self._validate(tmp, "developer", body, skill_ids={"query-docs"})
            self.assertEqual(
                [e for e in errors if e["field"] == "skills"], [], msg=errors
            )


class AgentSerializationTests(unittest.TestCase):
    def test_serialize_is_2space_with_trailing_newline(self):
        entries = [
            {
                "id": "a",
                "name": "a",
                "description": "d",
                "tools": ["Read"],
                "model": "haiku",
                "tags": ["x"],
                "bundles": ["b"],
                "owner": "o",
                "version": "0.1.0",
                "risk_level": "low",
                "status": "draft",
            }
        ]
        text = validator.serialize_agents_json(entries)
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(text, json.dumps(entries, indent=2) + "\n")

    def test_regenerate_sorts_and_orders_keys(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            # regenerate_agents_json now reads the `model:` enum from the
            # fixture plugin's own catalog, fail-closed: an absent models.yml is
            # a reported error, never a silent fallback to a literal set.
            (tmp / "catalog").mkdir()
            shutil.copy2(PLUGIN_DIR / "catalog" / "models.yml", tmp / "catalog" / "models.yml")
            _write_agent(tmp, "zeta", VALID_AGENT.format(name="zeta"))
            _write_agent(tmp, "alpha", VALID_AGENT.format(name="alpha"))
            entries, errors = validator.regenerate_agents_json(tmp, verbose=False)
            self.assertEqual(errors, [], msg=errors)
            self.assertEqual([e["id"] for e in entries], ["alpha", "zeta"])
            for e in entries:
                # Present-only projection: keys are those in AGENT_OUTPUT_KEY_ORDER
                # that the entry carries, in that fixed order.
                self.assertEqual(
                    list(e.keys()),
                    [k for k in validator.AGENT_OUTPUT_KEY_ORDER if k in e],
                )


class RealCatalogByteStabilityTests(unittest.TestCase):
    """The checked-in agents.json must be byte-stable: regenerating from the
    real on-disk agents/*.md yields exactly the committed bytes, and the
    catalog validates with zero errors."""

    def test_real_agents_regenerate_byte_identical(self):
        skill_ids = {path.parent.name for path in validator.discover_skills(PLUGIN_DIR)}
        entries, findings = validator.regenerate_agents_json(
            PLUGIN_DIR, verbose=False, skill_ids=skill_ids
        )
        # The effort key (ADR-0092 item 3) emits a non-failing WARNING; only true
        # errors matter for byte-stability. Filter warnings out.
        errors = [f for f in findings if f.get("severity") != "warning"]
        self.assertEqual(errors, [], msg=f"real agents produced validation errors: {errors}")
        self.assertEqual(len(entries), 10, msg=f"expected 10 agents, got {len(entries)}")
        regenerated = validator.serialize_agents_json(entries)
        on_disk = (PLUGIN_DIR / "catalog" / "agents.json").read_text(encoding="utf-8")
        self.assertEqual(regenerated, on_disk, msg="agents.json is not byte-stable; rerun validate-catalog.py")

    def test_dry_run_clean_for_real_catalog(self):
        # A dry run against the real tree must exit 0 (no drift, no errors) for
        # both skills and agents.  Capture stdout so the JSON payload doesn't
        # pollute the test-runner output stream.
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = validator.main(["--dry-run"])
        self.assertEqual(rc, 0, msg="validate-catalog --dry-run reported drift/errors on the real tree")


if __name__ == "__main__":
    unittest.main()
