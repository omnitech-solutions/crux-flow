"""Mechanical block-coverage check for the PEP 723 runtime contract (ADR-0035 §2).

The block-coverage rule: every shipped script whose DOCUMENTED invocation is
`uv run …` MUST carry a PEP 723 inline metadata block — including stdlib-only
scripts (empty `dependencies = []` + `requires-python`) — because a
metadata-less script under `uv run` inherits the surrounding project's
environment instead of an isolated one. Pure-stdlib scripts documented as
plain `python3 …` may stay blockless.

This test makes the rule un-rottable:

  1. Parses every `crux/skills/*/SKILL.md` for `uv run …scripts/<name>.py`
     invocation patterns and asserts each referenced script carries a
     PEP 723 block placed before the first non-comment code.
 2. Asserts each substrate entry script declares exactly the deps its own
      lane needs, from a PER-SCRIPT map. Since the ADR-0087 OpenRouter gateway
      consolidation every substrate entry declares httpx alone (derive-arch.py
      additionally pins pyyaml, because its frontmatter parser decides the
      spine hash — a distinct rule; see item 2.A below). A NEGATIVE assertion
      pins the retirement: none of these scripts may declare any of the three
      provider SDKs (anthropic, google-genai, openai), so a re-added SDK
      dependency fails here rather than silently restoring the fragmentation.
   2.A. Asserts derive-arch.py's pyyaml pin separately (the spine-parser rule).
   3. Asserts the three YAML validators declare pyyaml.
   4. Asserts any script with a `#!/usr/bin/env -S uv run`-style shebang
      carries a block (the shebang itself documents a uv invocation).

The skill scan keys on final invocation patterns only (other surfaces edit
skill prose concurrently); it deliberately ignores `python your_script.py`
style user-authored examples that do not reference `scripts/`.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from _dev_surface import REPO_ROOT, require_dev_surface

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent              # crux/scripts/
PLUGIN_ROOT = SCRIPTS_DIR.parent            # crux/ (the plugin dir)
SKILLS_DIR = PLUGIN_ROOT / "skills"

# The PEP 723 reference regex (verbatim from the spec), filtered to type
# "script" by the caller.
PEP723_BLOCK_RE = re.compile(
    r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$"
)

# A `scripts/<something>.py` token appearing in a line whose earlier text
# contains `uv run` (any flags / interpreter / ${CRUX_PLUGIN_ROOT} prefix
# in between).
UV_RUN_SCRIPT_RE = re.compile(r"uv run[^\n]*?scripts/([\w][\w./-]*?\.py)")

# Per-script dependency expectations for the substrate entry scripts. Since the
# ADR-0087 gateway consolidation every one of them declares httpx alone —
# nothing imports the provider SDKs any more, verified by the negative
# assertion below (a shared list stopped being true when derive-arch.py pinned
# pyyaml for the spine hash, which is a separate rule, not this one).
# Note: crux/spin/__main__.py removed — spin decommissioned per ADR-0037.
SUBSTRATE_ENTRY_DEPS = {
    # ADR-0087: one gateway, one transport, no SDKs.
    "crux/council/async_council.py": ("httpx",),
    "crux/runbook/__main__.py": ("httpx",),
    "crux/runbook/runbook.py": ("httpx",),
    "crux/spawner/__main__.py": ("httpx",),
}
SUBSTRATE_ENTRY_SCRIPTS = tuple(SUBSTRATE_ENTRY_DEPS)

# The three provider SDKs the gateway consolidation retired. None of the
# substrate entry scripts — nor derive-arch.py — may declare any of them.
RETIRED_SDK_DEPS = ("anthropic", "google-genai", "openai")
RETIRED_SDK_ENTRY_SCRIPTS = SUBSTRATE_ENTRY_SCRIPTS + ("derive-arch.py",)

# The PB-0026 YAML validators: real-YAML is a hard dependency.
YAML_VALIDATOR_SCRIPTS = (
    "validate-promptbook.py",
    "visualize-run-progress.py",
)

# Every script that reads crux/catalog/models.yml declares pyyaml. Two lists
# because they live on opposite sides of the release boundary: `tools/` never
# crosses into the staged artifact, so its assertion is guarded rather than
# unconditional.
MODELS_CATALOG_READERS = (
    "validate-catalog.py",
    "generate-opencode-agents.py",
    "generate-codex-agents.py",
)
DEV_ONLY_MODELS_CATALOG_READERS = ("tools/smoke-test-plugin-load.py",)

# Scripts whose PyYAML dependency decides their OUTPUT, not just whether they
# start. Its own list because neither name above would be true of it: this is
# not the PB-0026 real-YAML rule and not a models.yml read. `crux/arch/derive.py`
# reads frontmatter through PyYAML when present and a stdlib `_yaml_min` parser
# otherwise, and the two parse SKILL.md and schema frontmatter differently
# enough to emit a DIFFERENT arch spine hash from one source tree — so the
# dependency picks which hash the tree is compared against.
SPINE_PARSER_SCRIPTS = ("derive-arch.py",)


def extract_script_block(path: Path) -> str | None:
    """Return the `# /// script` block content of *path*, or None.

    Enforces placement: the `# /// script` opener must appear before the
    first non-comment code (shebang, blank lines, and other comments may
    precede it; a docstring counts as code).
    """
    text = path.read_text(encoding="utf-8")
    opener_seen = False
    for i, line in enumerate(text.splitlines()):
        if i == 0 and line.startswith("#!"):
            continue
        if line.strip() == "" or line.startswith("#"):
            if line.rstrip() == "# /// script":
                opener_seen = True
                break
            continue
        break  # first non-comment code reached
    if not opener_seen:
        return None
    matches = [
        m for m in PEP723_BLOCK_RE.finditer(text) if m.group("type") == "script"
    ]
    if not matches:
        return None  # opener present but block malformed
    return matches[0].group("content")


def block_dependencies(block_content: str) -> str:
    """Return the raw text of the block (deps are matched as substrings)."""
    return "\n".join(
        line[2:] if line.startswith("# ") else line.lstrip("#")
        for line in block_content.splitlines()
    )


def joined_skill_text(skill_md: Path) -> str:
    """SKILL.md text with backslash-continued lines joined, so multi-line
    `uv run … \\` command examples scan as one logical line."""
    raw = skill_md.read_text(encoding="utf-8")
    return re.sub(r"\\\n\s*", " ", raw)


def documented_uv_run_scripts() -> dict[Path, set[str]]:
    """Map an absolute shipped script path to its documenting surfaces.

    A skill-local ``<skill-dir>/scripts/X.py`` path resolves beside its
    ``SKILL.md``. Other ``…/scripts/X.py`` paths retain the established
    plugin-level ``crux/scripts/X.py`` resolution. Scans every distributed
    prose surface (skills, agents, templates), not just skills — a template
    or agent documenting an invocation binds the same block-coverage rule."""
    cited: dict[Path, set[str]] = {}
    surfaces = (
        sorted(SKILLS_DIR.glob("*/SKILL.md"))
        + sorted((SKILLS_DIR.parent / "agents").glob("*.md"))
        + sorted((SKILLS_DIR.parent / "templates").glob("*"))
    )
    for skill_md in (s for s in surfaces if s.is_file()):
        text = joined_skill_text(skill_md)
        for line in text.splitlines():
            for m in UV_RUN_SCRIPT_RE.finditer(line):
                rel = m.group(1).strip("\"'")
                prefix = m.group(0)
                script = (
                    skill_md.parent / "scripts" / rel
                    if "<skill-dir>/scripts/" in prefix and skill_md.name == "SKILL.md"
                    else SCRIPTS_DIR / rel
                )
                cited.setdefault(script, set()).add(
                    skill_md.stem if skill_md.name != "SKILL.md" else skill_md.parent.name
                )
    return cited


class TestBlockCoverage(unittest.TestCase):
    """Every SKILL.md-documented `uv run …scripts/X.py` script has a block."""

    def test_skills_dir_exists(self):
        self.assertTrue(SKILLS_DIR.is_dir(), f"missing skills dir: {SKILLS_DIR}")

    def test_skill_local_invocation_resolves_beside_its_skill(self):
        cited = documented_uv_run_scripts()
        local_install = SKILLS_DIR / "install-codex-agents" / "scripts" / "install.py"
        self.assertIn(local_install, cited)
        self.assertNotIn(SCRIPTS_DIR / "install.py", cited)

    def test_every_uv_run_documented_script_carries_a_block(self):
        failures = []
        for script, skills in sorted(documented_uv_run_scripts().items()):
            try:
                label = str(script.relative_to(PLUGIN_ROOT))
            except ValueError:
                label = str(script)
            if not script.is_file():
                failures.append(
                    f"{label}: referenced by {sorted(skills)} but missing on disk"
                )
                continue
            if extract_script_block(script) is None:
                failures.append(
                    f"{label}: documented as `uv run` by {sorted(skills)} but has "
                    "no PEP 723 `# /// script` block before first code "
                    "(block-coverage rule, ADR-0035 §2)"
                )
        self.assertEqual(failures, [], "\n" + "\n".join(failures))

    def test_uv_shebang_scripts_carry_a_block(self):
        """A `#!/usr/bin/env -S uv run` shebang documents a uv invocation."""
        failures = []
        for script in sorted(SCRIPTS_DIR.rglob("*.py")):
            if "__pycache__" in script.parts or "tests" in script.parts:
                continue
            first = script.read_text(encoding="utf-8").splitlines()[:1]
            if first and "uv run" in first[0]:
                if extract_script_block(script) is None:
                    failures.append(f"{script.relative_to(SCRIPTS_DIR)}")
        self.assertEqual(
            failures, [],
            "uv-run shebang without PEP 723 block: " + ", ".join(failures),
        )


class TestKnownBlocks(unittest.TestCase):
    """Non-vacuousness + dependency-content assertions for known scripts."""

    def test_substrate_entry_scripts_declare_their_expected_deps(self):
        for rel, expected in SUBSTRATE_ENTRY_DEPS.items():
            script = SCRIPTS_DIR / rel
            with self.subTest(script=rel):
                self.assertTrue(script.is_file(), f"missing: {script}")
                block = extract_script_block(script)
                self.assertIsNotNone(block, f"{rel}: no PEP 723 block")
                deps_text = block_dependencies(block)
                for dep in expected:
                    self.assertRegex(
                        deps_text,
                        rf'"{re.escape(dep)}[>=<~!]',
                        f"{rel}: PEP 723 block must declare {dep}",
                    )

    def test_no_entry_script_declares_a_provider_sdk(self):
        """ADR-0087: everything reaches the one gateway; no SDKs anywhere.

        Stated over every script the map covers (council, the runbook pair,
        spawner) PLUS derive-arch.py, because the positive assertion above
        cannot catch a re-added dependency — declaring httpx AND anthropic
        satisfies it either way. A returning SDK dep is the exact regression
        this decision exists to prevent, so it gets its own failing assertion
        per script.
        """
        for rel in RETIRED_SDK_ENTRY_SCRIPTS:
            script = SCRIPTS_DIR / rel
            with self.subTest(script=rel):
                self.assertTrue(script.is_file(), f"missing: {script}")
                block = extract_script_block(script)
                self.assertIsNotNone(block, f"{rel}: no PEP 723 block")
                deps_text = block_dependencies(block)
                for dep in RETIRED_SDK_DEPS:
                    self.assertNotRegex(
                        deps_text,
                        rf'"{re.escape(dep)}[>=<~!]',
                        f"{rel}: must NOT declare {dep} — every inference call "
                        "resolves through the OpenRouter gateway, and a provider "
                        "SDK dependency here means an un-migrated call path "
                        "(ADR-0087)",
                    )

    def test_yaml_validators_declare_pyyaml(self):
        for rel in YAML_VALIDATOR_SCRIPTS:
            script = SCRIPTS_DIR / rel
            with self.subTest(script=rel):
                block = extract_script_block(script)
                self.assertIsNotNone(block, f"{rel}: no PEP 723 block")
                self.assertIn(
                    "pyyaml", block_dependencies(block),
                    f"{rel}: must declare pyyaml (PB-0026 real-YAML rule)",
                )

    def test_models_catalog_readers_declare_pyyaml(self):
        for rel in MODELS_CATALOG_READERS:
            script = SCRIPTS_DIR / rel
            with self.subTest(script=rel):
                block = extract_script_block(script)
                self.assertIsNotNone(block, f"{rel}: no PEP 723 block")
                self.assertRegex(
                    block_dependencies(block),
                    r'"pyyaml>=6\.0"',
                    f"{rel}: reads catalog/models.yml, so it must declare pyyaml>=6.0",
                )

    def test_spine_parser_scripts_declare_pyyaml(self):
        """PB-0073 W1 — the parser choice decides the spine hash, so pin it.

        Without this dependency `uv run derive-arch.py --dry-run` resolves an
        isolated env with no PyYAML, the derive falls through to `_yaml_min`,
        and it reports drift on a pristine checkout while `python3` reports
        clean: two hashes from one tree. ADR-0060 Decision point 4 requires
        environment-independent output.

        The specifier is now an EXACT pin rather than the `>=6.0` floor this
        test was written against, and the reason is a second requirement on the
        same line. The crux pack's data-model and api-surface declare `yaml` as
        their parser (ADR-0097 parts 1 and 3), so `core.parser_pins` records the
        RESOLVED PyYAML version into the byte-compared `tool_pins` — a floor
        would let an upstream release move `_meta/manifest.json` on an
        unmodified tree, which is ADR-0096 clause 1 running backwards.

        This test asserts the shape of the pin; `ParserPinLockStepTests` in
        `tools/tests/test_schema_invariants.py` asserts that the same VALUE
        appears at every site that declares it. Two claims, two gates, neither
        restating the other's number.
        """
        for rel in SPINE_PARSER_SCRIPTS:
            script = SCRIPTS_DIR / rel
            with self.subTest(script=rel):
                self.assertTrue(script.is_file(), f"missing: {script}")
                block = extract_script_block(script)
                self.assertIsNotNone(block, f"{rel}: no PEP 723 block")
                self.assertRegex(
                    block_dependencies(block),
                    r'"pyyaml==\d+(?:\.\d+)*"',
                    f"{rel}: must declare pyyaml at an EXACT pin — the "
                    "frontmatter parser it resolves decides which arch spine "
                    "hash it computes, and the crux pack declares that parser, "
                    "so the resolved version is a byte of the byte-compared "
                    "`tool_pins` (ADR-0060 point 4, ADR-0096 clause 1).",
                )

    def test_dev_only_models_catalog_readers_declare_pyyaml(self):
        # `tools/` is outside the sync allowlist, so it is absent from the
        # staged artifact this suite also runs against. The guard skips there
        # and fails loudly in the dev checkout, which is where the file lives.
        for rel in DEV_ONLY_MODELS_CATALOG_READERS:
            script = REPO_ROOT / rel
            with self.subTest(script=rel):
                require_dev_surface(self, script, rel)
                block = extract_script_block(script)
                self.assertIsNotNone(block, f"{rel}: no PEP 723 block")
                self.assertRegex(
                    block_dependencies(block),
                    r'"pyyaml>=6\.0"',
                    f"{rel}: reads catalog/models.yml, so it must declare pyyaml>=6.0",
                )

    def test_blocks_declare_requires_python(self):
        """Every block-carrying shipped script pins a python floor."""
        for script in sorted(SCRIPTS_DIR.rglob("*.py")):
            if "__pycache__" in script.parts or "tests" in script.parts:
                continue
            block = extract_script_block(script)
            if block is None:
                continue
            with self.subTest(script=str(script.relative_to(SCRIPTS_DIR))):
                self.assertIn("requires-python", block_dependencies(block))

    def test_substrate_entry_scripts_carry_sibling_sys_path_insert(self):
        """ADR-0035 §2: entry scripts defensively insert the scripts/ dir
        (the bundled package's parent) onto sys.path."""
        for rel in SUBSTRATE_ENTRY_SCRIPTS:
            script = SCRIPTS_DIR / rel
            with self.subTest(script=rel):
                text = script.read_text(encoding="utf-8")
                m = re.search(
                    r"sys\.path\.insert\(\s*0,\s*str\(Path\(__file__\)"
                    r"\.resolve\(\)\.parents\[(\d+)\]\)\)",
                    text,
                )
                self.assertIsNotNone(m, f"{rel}: missing defensive sys.path insert")
                # parents[N] must land on scripts/: N = number of directory
                # components between scripts/ and the file (e.g.
                # crux/runbook/__main__.py -> parents[2]).
                expected_n = len(Path(rel).parts) - 1
                self.assertEqual(
                    int(m.group(1)), expected_n,
                    f"{rel}: parents[{m.group(1)}] does not resolve to the "
                    f"scripts/ dir (expected parents[{expected_n}])",
                )


if __name__ == "__main__":
    unittest.main()
