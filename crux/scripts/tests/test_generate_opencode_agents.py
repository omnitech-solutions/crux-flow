"""Regression tests for generate-opencode-agents.py (the 3rd regenerative output).

Per ADR-0043, opencode/agents/*.md is a committed projection of crux/agents/*.md.
This test pins the locked transform rules + hard-error paths + the catalog
resolution + the --dry-run drift semantics, independent of the committed baseline. (It complements the
CI drift gate check-opencode-agents.yml: the GATE catches staleness of the committed
output; this TEST catches a transform-rule regression the gate cannot — a rule bug
baked identically into generator + output shows zero drift.)

Stdlib only. The script has a hyphenated name, so it is loaded via importlib.
Run: uv run python3 crux/scripts/tests/test_generate_opencode_agents.py
"""

import contextlib
import fnmatch
import importlib.util
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

try:
    import yaml
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
GEN_PATH = SCRIPTS_DIR / "generate-opencode-agents.py"
# The checkout under test. `main` resolves its root from the invocation, so every
# case that drives the CLI names this explicitly instead of inheriting the cwd.
REPO_ROOT = SCRIPTS_DIR.parent.parent


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    # Register BEFORE exec: `dataclasses` resolves a class's own module out of
    # sys.modules while processing it, and an unregistered module makes that
    # lookup return None.
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


MOD = _load_module("gen_opencode_agents", GEN_PATH)
# Plain import, not a second file-location load: the driver has already put
# scripts/ on sys.path, and loading the module twice would give two distinct
# SpecViolation classes, so `assertRaises(MOD.SpecViolation)` would miss the
# one the catalog raises.
import models_catalog as MC  # noqa: E402
# Same reasoning, and additionally: `MOD` is the DRIVER script, which re-exports
# only the public projection surface. The private helpers a guard is built on
# are reachable only through the module that defines them.
import opencode_agents as OA  # noqa: E402

CATALOG = MC.load()

# Captured at import, before any test patches MOD.OUTPUT_DIR.
COMMITTED_OUTPUT_DIR = MOD.OUTPUT_DIR

# The 10 crux agents (ADR-0026/0028; wayfinder added per ADR-0047/ADR-0048);
# source of truth for completeness assertions.
EXPECTED_AGENTS = {
    "architect", "brainstormer", "commander", "dev-lead", "developer",
    "historian", "librarian", "night-gardener", "reviewer", "wayfinder",
}

# wayfinder's exact resolved OpenCode model (ADR-0047/ADR-0048): a read/search-only
# reconnaissance role that inherits the standard rung's OpenCode alias, the same tier as
# historian and librarian, not the flagship tier. Asserted exactly so a silent regression to
# the wrong tier is caught (test_completeness + test_values_name_a_declared_provider check
# shape/membership only, not this specific value).
#
# Every alias value now resolves through the one gateway, so the provider half is
# `openrouter` and the vendor namespace moved one segment right (ADR-0087).
#
# THE ALIAS NAME IS NOT PINNED HERE, only the tier it must resolve to. This constant read
# `openrouter/qwen/…` while the standard rung selected `qwen-max`; the owner has since taken
# qwen-max out of active OpenCode use, and a hard-coded id made that a test failure rather
# than a configuration change. The value is derived from the rung so that changing which
# model the standard tier selects is a one-line edit to the catalog, while a wayfinder
# promoted off the standard rung still fails this assertion — which is what it guards.
EXPECTED_WAYFINDER_MODEL = CATALOG.aliases[CATALOG.levels["standard"].opencode]


def _fm(tools, description="Use when ...", extra=""):
    """Minimal synthetic source frontmatter (inner block, no --- fences)."""
    return f"description: {description}\ntools: {tools}\n{extra}"


def _transform(name, fm):
    """transform() with the model the catalog resolves for `name`.

    Resolution moved out of the transform when MODEL_MAP was deleted: the
    projection now takes an already-resolved value so one catalog read serves a
    whole run. These tests resolve per call because they transform one agent at
    a time.
    """
    return MOD.transform(name, fm, CATALOG.resolve(name).opencode)


def _transform_with_model(name, fm, model):
    """transform() with an EXPLICIT model value, bypassing catalog resolution.

    The catalog cannot express a model value carrying a newline, so the guard
    that refuses one is only reachable by passing the value directly — which is
    the same call `generate()` makes, with the same argument.
    """
    return MOD.transform(name, fm, model)


# ─────────────────────── V2 permissions-array test kit ───────────────────────
#
# The rule assertions below are STRUCTURAL, not substring. A V2 rule list is
# ordered and resolved last-match-wins, so `assertIn("shell: deny", out)` says
# nothing about whether a later rule re-grants it. Everything here parses the
# emitted block into rule dicts and resolves them through one reference
# resolver, which is also what the order-mutation positive control drives.

# The pinned V2 action universe, restated here rather than imported, so a
# regression that edited the constant does not edit its own test.
PINNED_ACTIONS = (
    "read", "grep", "glob", "list", "edit", "shell",
    "subagent", "skill", "todowrite", "webfetch", "websearch",
)

_RULE_ACTION_RE = re.compile(r"^  - action: (?P<action>[a-z]+)$")
_RULE_RESOURCE_RE = re.compile(r'^    resource: "(?P<resource>[^"]*)"$')
_RULE_EFFECT_RE = re.compile(r"^    effect: (?P<effect>allow|deny)$")


def _frontmatter_of(text: str) -> str:
    """The inner frontmatter block, whether `text` is fenced or already inner."""
    m = re.match(r"^---\n(.*?\n)---\n", text, re.S)
    return m.group(1) if m else text


def _parse_rules(text: str) -> list[dict]:
    """Strict, stdlib-only reader for the emitted `permissions:` array.

    It raises on any line inside the block it does not recognize. That is
    deliberate: a reader that shrugged and returned `[]` would make every
    assertion below pass vacuously the moment the emission shape moved.
    """
    lines = _frontmatter_of(text).splitlines()
    if "permissions:" not in lines:
        raise ValueError("no top-level `permissions:` block in the frontmatter")
    i = lines.index("permissions:") + 1
    rules: list[dict] = []
    while i < len(lines) and lines[i][:1] == " ":
        head = _RULE_ACTION_RE.match(lines[i])
        if head is None:
            raise ValueError(f"unrecognized rule line {lines[i]!r}")
        if i + 2 >= len(lines):
            raise ValueError(f"truncated rule at {lines[i]!r}")
        res = _RULE_RESOURCE_RE.match(lines[i + 1])
        eff = _RULE_EFFECT_RE.match(lines[i + 2])
        if res is None or eff is None:
            raise ValueError(
                f"malformed rule at {lines[i]!r}: {lines[i + 1]!r} / {lines[i + 2]!r}"
            )
        rules.append({
            "action": head.group("action"),
            "resource": res.group("resource"),
            "effect": eff.group("effect"),
        })
        i += 3
    if not rules:
        raise ValueError("`permissions:` block is empty")
    return rules


def _effective(rules, action: str, resource: str) -> str | None:
    """Resolve one (action, resource) against an ordered V2 rule list.

    Last match wins: walk in order and keep the effect of the LAST rule whose
    action AND resource both match by glob. Returns None when no rule matches,
    which is the host-default fall-through.

    This is the ONE resolver in the file. The per-agent verdict table and the
    order-mutation positive control both drive it, and that shared use is what
    makes the control a real check rather than a restatement.
    """
    effect = None
    for rule in rules:
        if (fnmatch.fnmatchcase(action, rule["action"])
                and fnmatch.fnmatchcase(resource, rule["resource"])):
            effect = rule["effect"]
    return effect


def _rules_of(name: str, fm: str) -> list[dict]:
    return _parse_rules(_transform(name, fm))


_GENERATED_CACHE: dict[str, str] = {}


def _generated_rules(agent: str) -> list[dict]:
    """The rule list of the REAL projection of `agent` (not a synthetic fixture)."""
    if not _GENERATED_CACHE:
        _GENERATED_CACHE.update(MOD.generate())
    return _parse_rules(_GENERATED_CACHE[f"{agent}.md"])


def _permute_subagent_allows_before_deny(rules: list[dict]) -> list[dict]:
    """Move the broad `subagent`/`"*"` deny AFTER the per-role allows.

    The mutation the positive control needs: same rules, same multiset, only the
    order of the subagent block reversed into the shape ADR-0100's Consequences
    forbid. Every non-subagent rule keeps its position.
    """
    subagent = [r for r in rules if r["action"] == "subagent"]
    denies = [r for r in subagent if r["resource"] == "*"]
    allows = [r for r in subagent if r["resource"] != "*"]
    if not denies or not allows:
        raise ValueError("agent has no deny-then-allow subagent block to permute")
    reordered = allows + denies
    out, k = [], 0
    for rule in rules:
        if rule["action"] == "subagent":
            out.append(reordered[k])
            k += 1
        else:
            out.append(rule)
    return out


def _top_level_keys(text: str) -> set[str]:
    """The column-0 mapping keys of a frontmatter block.

    Whitespace before the colon is tolerated because YAML tolerates it: `name :
    x` is the key `name`, not the key `name `. A key-matching regex that demands
    a colon immediately after the name would report that line as no key at all,
    which is precisely how a dropped-key guard comes to pass over the spelling
    it was written to catch.

    Quoting is unwrapped for the same reason, and it is the same failure twice:
    `"name": x` and `'name': x` are also the key `name`. A predicate that reads
    the bare spelling only reports the quoted line as no key at all, so the
    absence assertions built on it cannot see a quoted key that reached the
    emitted frontmatter — where, beside the `permissions` array, it voids every
    rule in that array.
    """
    return {
        m.group(2)
        for m in re.finditer(
            r"""^(["']?)([A-Za-z][A-Za-z0-9_-]*)\1[ \t]*:""",
            _frontmatter_of(text), re.M)
    }


# Per-agent expected outcomes, written out rather than derived from each source
# `tools:` line — a table derived from the same input as the code would restate
# the transform instead of checking it. `allowed` is the set of pinned actions
# resolving to `allow`; every other pinned action must resolve to `deny`.
# `subagent_roles` is the dispatch grant: a role set for a restricted grant,
# None for a bare `Agent`, and an empty set for no grant at all.
AGENT_VERDICTS = {
    "architect": {
        "allowed": {"read", "grep", "glob", "list", "edit", "shell", "skill",
                    "webfetch", "websearch"},
        "subagent_roles": frozenset(),
    },
    "brainstormer": {
        "allowed": {"read", "grep", "glob", "list", "skill", "webfetch", "websearch"},
        "subagent_roles": frozenset(),
    },
    "commander": {
        "allowed": {"read", "grep", "glob", "list", "skill", "todowrite"},
        "subagent_roles": frozenset({"architect", "brainstormer", "dev-lead",
                                     "historian", "librarian", "night-gardener",
                                     "reviewer", "wayfinder"}),
    },
    "dev-lead": {
        "allowed": {"read", "grep", "glob", "list", "edit", "shell", "skill", "todowrite"},
        "subagent_roles": frozenset({"developer", "historian", "reviewer", "wayfinder"}),
    },
    "developer": {
        "allowed": {"read", "grep", "glob", "list", "edit", "shell", "skill", "todowrite"},
        "subagent_roles": frozenset(),
    },
    "historian": {
        "allowed": {"read", "grep", "glob", "list", "edit", "shell", "skill", "todowrite"},
        "subagent_roles": frozenset(),
    },
    "librarian": {
        "allowed": {"read", "grep", "glob", "list", "skill"},
        "subagent_roles": frozenset(),
    },
    "night-gardener": {
        "allowed": {"read", "grep", "glob", "list", "edit", "shell", "skill",
                    "todowrite", "webfetch", "websearch"},
        "subagent_roles": frozenset({"historian", "librarian", "wayfinder"}),
    },
    "reviewer": {
        "allowed": {"read", "grep", "glob", "list", "shell", "skill"},
        "subagent_roles": frozenset(),
    },
    "wayfinder": {
        "allowed": {"read", "grep", "glob", "list", "webfetch", "websearch"},
        "subagent_roles": frozenset(),
    },
}


def _fixture_source_dir(tmp: Path, *, drop: str | None = None, extra: str | None = None) -> Path:
    """Copy the ten real agent files into `tmp`, optionally dropping one or
    adding a synthetic extra. Self-contained rather than a whole-repo copy: the
    staged release artifact has no `opencode/` or `bionic/`, so a test that
    copies the tree and reads outside `crux/` false-fails at release time."""
    out = tmp / "agents"
    out.mkdir()
    for src in sorted(MOD.SOURCE_DIR.glob("*.md")):
        if drop is not None and src.stem == drop:
            continue
        shutil.copy2(src, out / src.name)
    if extra is not None:
        (out / f"{extra}.md").write_text(
            f"---\nname: {extra}\ndescription: Synthetic.\ntools: Read\nmodel: sonnet\n---\nBody.\n",
            encoding="utf-8",
        )
    return out


def _catalog_text_without(agent: str) -> str:
    """The shipped catalog with one roster row removed, as text."""
    lines = MC.CATALOG_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    kept, skipping = [], False
    for line in lines:
        stripped = line.rstrip("\n")
        if stripped == f"  {agent}:" or stripped.startswith(f"  {agent}: "):
            skipping = stripped.endswith(":")
            if not skipping:
                continue
            continue
        if skipping:
            if line.startswith("    ") and line.strip():
                continue
            skipping = False
        kept.append(line)
    return "".join(kept)


class TransformRuleTests(unittest.TestCase):
    # 'developer' is a real roster key, so the catalog resolves a model for it.

    def test_read_grants_read_and_list_and_denies_rest(self):
        out = _transform("developer", _fm("Read"))
        rules = _parse_rules(out)
        self.assertEqual(_effective(rules, "read", "x"), "allow")
        # `list` is derived from `read` and the derivation survives V2 unchanged.
        self.assertEqual(_effective(rules, "list", "x"), "allow")
        self.assertEqual(_effective(rules, "shell", "x"), "deny")
        self.assertEqual(_effective(rules, "edit", "x"), "deny")
        self.assertIn("mode: subagent", out)
        self.assertIn(f"model: {CATALOG.resolve('developer').opencode}", out)

    def test_edit_and_write_map_jointly_to_edit(self):
        rules = _rules_of("developer", _fm("Read, Edit, Write, Bash"))
        self.assertEqual(_effective(rules, "edit", "x"), "allow")
        # Bash renames to `shell` in V2; the old key must not survive.
        self.assertEqual(_effective(rules, "shell", "x"), "allow")
        self.assertIsNone(_effective(rules, "bash", "x"))

    def test_edit_without_write_is_spec_violation(self):
        with self.assertRaises(MOD.SpecViolation):
            _transform("developer", _fm("Read, Edit"))

    def test_unmapped_tool_is_spec_violation(self):
        with self.assertRaises(MOD.SpecViolation):
            _transform("developer", _fm("Read, Frobnicate"))

    def test_agent_outside_the_roster_is_spec_violation(self):
        # Fail-closed, never a default: an agent the catalog does not name has
        # no model, and guessing one would silently ship the wrong SKU.
        with self.assertRaises(MOD.SpecViolation):
            CATALOG.resolve("no-such-agent")

    def test_missing_tools_line_is_spec_violation(self):
        with self.assertRaises(MOD.SpecViolation):
            _transform("developer", "description: no tools here\n")

    def test_mode_map_promotes_commander_and_night_gardener(self):
        self.assertEqual(MOD.MODE_MAP.get("commander"), "all")
        self.assertEqual(MOD.MODE_MAP.get("night-gardener"), "all")
        self.assertIn("mode: all", _transform("commander", _fm("Read")))
        self.assertIn("mode: subagent", _transform("developer", _fm("Read")))

    def test_restricted_agent_roles_emit_the_broad_deny_before_every_allow(self):
        # ADR-0100 point 3, case 1. The full ordered subagent block is asserted
        # as a LIST, index order included: last-match-wins makes the deny
        # meaningless unless it precedes the exceptions it is refining.
        rules = _rules_of("dev-lead", _fm("Agent(reviewer), Agent(developer)"))
        subagent = [r for r in rules if r["action"] == "subagent"]
        self.assertEqual(subagent, [
            {"action": "subagent", "resource": "*", "effect": "deny"},
            {"action": "subagent", "resource": "developer", "effect": "allow"},
            {"action": "subagent", "resource": "reviewer", "effect": "allow"},
        ])
        deny_at = rules.index(subagent[0])
        for allow in subagent[1:]:
            self.assertLess(deny_at, rules.index(allow))

    def test_bare_agent_grants_a_single_wildcard_allow(self):
        # ADR-0100 point 3, case 2: one `"*"` allow and no deny.
        rules = _rules_of("dev-lead", _fm("Agent"))
        self.assertEqual(
            [r for r in rules if r["action"] == "subagent"],
            [{"action": "subagent", "resource": "*", "effect": "allow"}],
        )
        self.assertEqual(_effective(rules, "subagent", "anyone"), "allow")

    def test_absent_agent_grant_emits_a_single_wildcard_deny(self):
        # ADR-0100 point 3, case 3.
        rules = _rules_of("developer", _fm("Read"))
        self.assertEqual(
            [r for r in rules if r["action"] == "subagent"],
            [{"action": "subagent", "resource": "*", "effect": "deny"}],
        )

    def test_disallowed_agent_collapses_subagent_to_one_wildcard_deny(self):
        # ADR-0100 point 3, case 4: a disallowedTools denial of Agent collapses
        # every other case to the single `"*"` deny, per-role allows included.
        rules = _rules_of(
            "dev-lead", _fm("Agent(developer)", extra="disallowedTools: Agent\n")
        )
        self.assertEqual(
            [r for r in rules if r["action"] == "subagent"],
            [{"action": "subagent", "resource": "*", "effect": "deny"}],
        )
        self.assertEqual(_effective(rules, "subagent", "developer"), "deny")

    def test_no_legacy_permission_map_survives_beside_the_array(self):
        # ADR-0100 point 1 / Option B: per-file format mixing is prohibited, so
        # the singular map must be gone rather than retained as a fallback.
        # Line-anchored, because `permissions:` contains `permission` as a
        # substring and a bare `assertNotIn` would report a false failure.
        out = _transform("dev-lead", _fm("Read, Agent(developer)"))
        self.assertNotIn("permission", _top_level_keys(out) - {"permissions"})
        self.assertIn("permissions", _top_level_keys(out))

    def test_model_reaches_the_output_literally_not_as_a_regex_template(self):
        """The model string is inserted, never interpreted.

        `transform` splices `model` into a `re.subn` call. With a replacement
        TEMPLATE, `\\1` in the model expanded to the captured description line:
        the emitted `model:` value became `anthropic/description: Use when ...`
        and the description was swallowed. The loader does not check the slug
        grammar — V5 is CI-side — so a value like this can reach `transform` on
        any direct run that skipped CI. A replacement function makes the whole
        class inert, and this test is what keeps it a function.
        """
        for hostile in (r"anthropic/\1", r"anthropic/\g<0>", "anthropic/a\\\\b"):
            with self.subTest(model=hostile):
                out = MOD.transform("developer", _fm("Read"), hostile)
                self.assertIn(f"model: {hostile}\n", out)
                self.assertEqual(out.count("description:"), 1)


class DisallowedToolsProjectionTests(unittest.TestCase):
    """ADR-0092 item 3: disallowedTools -> OpenCode `deny`, deny wins over allow."""

    def test_quoted_flow_list_elements_map_like_bare_tokens(self):
        # A YAML/JSON flow list keeps quotes on each element. They must be
        # stripped, not carried into DENY_TOOL_MAP — a retained quote made
        # `"Bash"` unmappable and crashed the whole projection with SpecViolation.
        rules = _rules_of("developer", _fm("Read, Bash", extra='disallowedTools: ["Bash"]\n'))
        self.assertEqual(_effective(rules, "shell", "x"), "deny")
        self.assertEqual(_effective(rules, "read", "x"), "allow")

    def test_denying_read_also_closes_the_derived_list_grant(self):
        # `list` is derived from `read`; the derivation runs AFTER the deny
        # subtraction, so denying Read closes list too (deny fully honored).
        # ADR-0100 point 2 restates that this survives the V2 migration.
        rules = _rules_of("developer", _fm("Read", extra="disallowedTools: Read\n"))
        self.assertEqual(_effective(rules, "read", "x"), "deny")
        self.assertEqual(_effective(rules, "list", "x"), "deny")


class CatalogResolutionTests(unittest.TestCase):
    """The roster and the resolved OpenCode column, read from the catalog.

    These assertions moved off a module-level MODEL_MAP dict and onto the
    shipped `crux/catalog/models.yml`. They are the same three questions:
    is every role covered, is the wayfinder pin still the throughput tier, and
    does every emitted value name a declared provider.
    """

    def test_completeness(self):
        self.assertEqual(set(CATALOG.agents), EXPECTED_AGENTS)

    def test_wayfinder_pins_exact_model(self):
        self.assertEqual(CATALOG.resolve("wayfinder").opencode, EXPECTED_WAYFINDER_MODEL)

    def test_values_name_a_declared_provider(self):
        # Not "starts with openrouter/", even though every value does today: the
        # assertion is membership in the allowlist, which is what V5 enforces and
        # what stays true if a second provider is ever declared. The invariant
        # survived both the Fireworks lineup and the single-gateway collapse.
        for name in sorted(CATALOG.agents):
            value = CATALOG.resolve(name).opencode
            provider, sep, slug = value.partition("/")
            self.assertTrue(sep, f"{name} -> {value} has no provider prefix")
            self.assertIn(provider, CATALOG.providers, f"{name} -> {value}")
            self.assertTrue(slug, f"{name} -> {value} has an empty slug")

    def test_no_retired_model_ids(self):
        # Structural currency guard — no churn on exact ids, but a regression to a
        # retired model is caught. (Kept in sync with the router refresh, PB-0037.)
        blob = " ".join(CATALOG.resolve(n).opencode for n in CATALOG.agents)
        # `accounts/fireworks/` is the retired Fireworks path form. It names a
        # provider the allowlist no longer carries, so a value regressing to it
        # is unroutable rather than merely out of date.
        for retired in ("claude-sonnet-4-6", "claude-opus-4-7", "-o3", "o4-mini",
                        "accounts/fireworks/"):
            self.assertNotIn(retired, blob, f"retired id {retired!r} is resolved by the catalog")

    def test_resolution_reproduces_the_intended_lineup(self):
        """AC-9: the resolved triple for all ten agents.

        `claude` and the Codex pair are asserted against literal values. The
        OpenCode column is asserted against `aliases[<expected alias name>]`, so
        the test binds the ROUTING without hard-coding the three Fireworks ids —
        an owner bumping one of them edits a single alias row and this test
        still holds.
        """
        expected = {
            "architect":      ("opus",   "sol-latest",    "gpt-6.1-sol",     "high"),
            "brainstormer":   ("opus",   "sol-latest",    "gpt-6.1-sol",     "high"),
            "commander":      ("claude-opus-5-5", "glm-latest", "gpt-6-astra", "high"),
            "dev-lead":       ("opus",   "sol-latest",    "gpt-6.1-sol",     "high"),
            "developer":      ("claude-sonnet-5-5", "deepseek-flash", "gpt-6.1-sol",     "high"),
            "historian":      ("claude-sonnet-5-5", "glm-latest",    "gpt-6.1-sol",     "high"),
            "librarian":      ("claude-sonnet-5-5", "glm-latest",    "gpt-6.1-sol",     "high"),
            "night-gardener": ("claude-opus-5-5", "opus-latest", "gpt-6-astra", "high"),
            "reviewer":       ("claude-opus-5-5", "sol-latest", "gpt-6.1-sol", "xhigh"),
            "wayfinder":      ("claude-sonnet-5-5", "glm-latest",    "gpt-6.1-sol",     "high"),
        }
        self.assertEqual(set(expected), EXPECTED_AGENTS)
        for name, (claude, alias, codex_model, effort) in sorted(expected.items()):
            with self.subTest(agent=name):
                resolved = CATALOG.resolve(name)
                self.assertEqual(resolved.claude, claude)
                self.assertEqual(CATALOG.opencode_alias(name), alias)
                self.assertEqual(resolved.opencode, CATALOG.aliases[alias])
                self.assertEqual(resolved.codex.model, codex_model)
                self.assertEqual(resolved.codex.reasoning_effort, effort)


class ModelValueControlCharacterTests(unittest.TestCase):
    """The resolved model value is emitted on a `model:` LINE, so it must hold no newline.

    `transform` interpolates the catalog's value into the frontmatter. The
    grammar that would exclude a newline — the V5 slug rule — is checked
    CI-side, not by the loader, so a value carrying one splices whatever
    follows into the emitted frontmatter as sibling top-level keys. The keys
    reachable that way include the two ADR-0100 point 4 names as safety
    critical: a spliced `metadata:` voids every rule in the permissions array
    beside it.
    """

    SPLICE = "openrouter/x\nmetadata:\n  owned: true"

    def test_a_newline_in_the_model_value_is_refused(self):
        with self.assertRaises(MOD.SpecViolation) as ctx:
            _transform_with_model("developer", "description: d\ntools: Read\n", self.SPLICE)
        self.assertIn("control character", str(ctx.exception))

    def test_the_refused_value_would_otherwise_have_spliced_a_banned_key(self):
        """Positive control: names what the refusal above prevents.

        Without it, the assertion above passes on a `transform` that refuses
        every model value for any reason. This drives the same splice through
        the emitting half of the function — the description substitution — and
        shows the spliced `metadata:` landing as a top-level key.
        """
        emitted = re.sub(
            r"^(description:.*)$",
            lambda m: f"{m.group(1)}\nmodel: {self.SPLICE}\n",
            "description: d\n", count=1, flags=re.M,
        )
        self.assertIn("metadata", _top_level_keys(f"---\n{emitted}---\n"))

    def test_carriage_return_and_other_control_characters_are_refused(self):
        for label, value in (
            ("carriage return", "openrouter/x\rmetadata: y"),
            ("NUL", "openrouter/x\x00y"),
            ("DEL", "openrouter/x\x7fy"),
        ):
            with self.subTest(char=label):
                with self.assertRaises(MOD.SpecViolation):
                    _transform_with_model("developer", "description: d\ntools: Read\n", value)

    def test_an_ordinary_value_still_transforms(self):
        """Negative control: the guard refuses control characters, not values."""
        out = _transform_with_model(
            "developer", "description: d\ntools: Read\n", "openrouter/vendor/model-1.5"
        )
        self.assertIn("model: openrouter/vendor/model-1.5\n", out)

    def test_every_shipped_catalog_value_passes_the_guard(self):
        """The guard is proportionate: no value the catalog resolves today trips it."""
        for name in sorted(CATALOG.agents):
            with self.subTest(agent=name):
                value = CATALOG.resolve(name).opencode
                self.assertEqual([c for c in value if ord(c) < 0x20 or ord(c) == 0x7F], [])


class DeletedTableTests(unittest.TestCase):
    """AC-3: neither hand-maintained model table survives at module scope.

    An anchored pattern, not a bare token search: `RUNTIME` appears inside
    prose and inside other identifiers, so a substring search would
    false-positive and report a pass as a failure.
    """

    def test_neither_projection_defines_a_model_table(self):
        for rel in ("opencode_agents.py", "codex_agents.py"):
            source = (SCRIPTS_DIR / rel).read_text(encoding="utf-8")
            for token in ("MODEL_MAP", "RUNTIME"):
                with self.subTest(module=rel, table=token):
                    self.assertIsNone(
                        re.search(rf"^{token}\b", source, re.M),
                        f"{rel} still defines {token} at module scope",
                    )


class ManagedFilenamesTests(unittest.TestCase):
    """AC-4: the managed set tracks the filesystem, not a formula.

    Two cases on purpose. The first alone would pass against a hard-coded
    ten-name literal; the second — a fixture tree with one agent file and its
    roster row both removed — is what makes the assertion falsifiable.
    """

    def test_shipped_tree_yields_the_ten_agent_filenames(self):
        self.assertEqual(
            MOD.managed_filenames(),
            frozenset(f"{p.stem}.md" for p in MOD.SOURCE_DIR.glob("*.md")),
        )
        self.assertEqual(len(MOD.managed_filenames()), 10)

    def test_fixture_tree_missing_one_role_yields_nine(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = _fixture_source_dir(tmp, drop="wayfinder")
            catalog_path = tmp / "models.yml"
            catalog_path.write_text(_catalog_text_without("wayfinder"), encoding="utf-8")
            managed = MC.managed_filenames(catalog_path=catalog_path, agents_dir=source)
        self.assertEqual(len(managed), 9)
        self.assertNotIn("wayfinder.md", managed)

    def test_managed_filenames_module_attribute_still_resolves(self):
        """The PEP 562 shim keeps the historical `MANAGED_FILENAMES` spelling.

        This test used to read `assertEqual(MOD.managed_filenames(),
        MOD.managed_filenames())` — true of any function, and against the
        WRONG module besides: `MOD` is the generator driver and has no
        `MANAGED_FILENAMES` at all, so the assertion could not have failed for
        any reason connected to the shim.
        """
        import opencode_agents as OA

        self.assertEqual(OA.MANAGED_FILENAMES, OA.managed_filenames())
        self.assertEqual(len(OA.MANAGED_FILENAMES), 10)
        self.assertIn("wayfinder.md", OA.MANAGED_FILENAMES)

    def test_managed_filenames_attribute_raises_specviolation_not_importerror(self):
        """The behavior the shim exists FOR.

        `MANAGED_FILENAMES` was a module constant computed at import. Deriving
        it from a file read would have turned a malformed catalog into an
        ImportError inside an installer written to translate exactly one
        exception type — SpecViolation — into structured exit-2 JSON. An
        ImportError would escape as a traceback and exit 1 instead. The
        `__getattr__` defers the read to attribute access so the failure
        arrives as the type the lane handles.
        """
        import opencode_agents as OA

        real_load = MC.load
        with tempfile.TemporaryDirectory() as td:
            broken = Path(td) / "models.yml"
            broken.write_text('schema_version: "999"\n', encoding="utf-8")

            # The REAL loader over a REAL malformed file, so the exception
            # raised is the one production raises. Only the path is redirected;
            # `load`'s default argument binds CATALOG_PATH at def time, so
            # patching the module constant would not reach it.
            def load_the_broken_one(*args, **kwargs):
                return real_load(catalog_path=broken, agents_dir=MC.AGENTS_DIR)

            MC.load = load_the_broken_one
            try:
                with self.assertRaises(MC.SpecViolation):
                    OA.MANAGED_FILENAMES
            finally:
                MC.load = real_load
        # Restored: the shim must be usable again afterwards.
        self.assertEqual(len(OA.MANAGED_FILENAMES), 10)

    def test_unknown_module_attribute_still_raises_attributeerror(self):
        # The shim must not swallow ordinary typos into a catalog read.
        import opencode_agents as OA

        with self.assertRaises(AttributeError):
            OA.NO_SUCH_ATTRIBUTE


class FailClosedCatalogTests(unittest.TestCase):
    """AC-5 and AC-6: the loader refuses before anything is written."""

    def test_agent_file_with_no_roster_entry_raises_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = _fixture_source_dir(tmp, extra="interloper")
            out = tmp / "out"
            with self.assertRaises(MOD.SpecViolation):
                MOD._project(source)
            self.assertFalse(out.exists())

    def test_stale_roster_entry_with_no_agent_file_raises(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = _fixture_source_dir(tmp, drop="wayfinder")
            with self.assertRaises(MOD.SpecViolation):
                MOD._project(source)


class SourceLeafSymlinkTests(unittest.TestCase):
    """[SECURITY:S5] `parse_source` refuses a symlinked `crux/agents/*.md`.

    This read is the one place a source leaf's bytes enter a generated agent
    file, so following a link planted at that path renders content from
    outside the tree into `opencode/agents/<role>.md`. The roster rule
    (`models_catalog.check_roster`) deliberately does NOT refuse it — doing so
    would close `validate-catalog.py`'s [SECURITY:S3] gate and make its
    containment layer dead code — so the guard belongs here, on the read.
    Matches `build-skill-zips.py`, which refuses a symlinked SKILL.md leaf
    rather than skipping it.
    """

    def test_a_live_symlinked_source_leaf_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = _fixture_source_dir(tmp)
            victim = tmp / "outside.md"
            victim.write_text(
                "---\nname: wayfinder\ndescription: Planted.\ntools: Read\nmodel: sonnet\n"
                "---\nPLANTED-FROM-OUTSIDE\n",
                encoding="utf-8",
            )
            leaf = source / "wayfinder.md"
            leaf.unlink()
            leaf.symlink_to(victim)

            with self.assertRaises(MOD.SpecViolation) as ctx:
                MOD._project(source)
        self.assertIn("symlink", str(ctx.exception))

    def test_a_dangling_symlinked_source_leaf_is_a_spec_violation_not_an_oserror(self):
        # The refusal is checked BEFORE `read_text`, so a dangling link is the
        # exception type the driver translates to exit 2 rather than a
        # FileNotFoundError traceback and exit 1.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = _fixture_source_dir(tmp)
            leaf = source / "wayfinder.md"
            leaf.unlink()
            leaf.symlink_to(tmp / "does-not-exist.md")

            with self.assertRaises(MOD.SpecViolation):
                MOD._project(source)

    def test_the_planted_content_never_reaches_the_generated_output(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            source = _fixture_source_dir(tmp)
            victim = tmp / "outside.md"
            victim.write_text(
                "---\nname: wayfinder\ndescription: Planted.\ntools: Read\nmodel: sonnet\n"
                "---\nPLANTED-FROM-OUTSIDE\n",
                encoding="utf-8",
            )
            leaf = source / "wayfinder.md"
            leaf.unlink()
            leaf.symlink_to(victim)

            try:
                rendered = "".join(MOD._project(source).values())
            except MOD.SpecViolation:
                rendered = ""
            self.assertNotIn("PLANTED-FROM-OUTSIDE", rendered)


class GenerateTests(unittest.TestCase):
    def test_generate_produces_all_expected_agents(self):
        gen = MOD.generate()
        self.assertEqual({Path(n).stem for n in gen}, EXPECTED_AGENTS)
        # Every generated file is a well-formed frontmatter doc with the added keys.
        for name, content in gen.items():
            self.assertTrue(content.startswith("---\n"))
            self.assertIn("\nmode: ", content)
            self.assertRegex(content, r"\nmodel: [a-z0-9-]+/")
            self.assertIn("\npermissions:\n", content)


class VerbatimPreservationTests(unittest.TestCase):
    """ADR-0043 locks `description: verbatim` and `body: verbatim` through the
    transform. Nothing else pins that the ORIGINAL text survives byte-for-byte —
    the transform-rule tests only assert the ADDED keys (mode/model/permission).
    A regression that rewrote/escaped/truncated the description, or that dropped
    or mangled the body during assembly, would be baked identically into the
    generator AND its output, so the byte-drift gate (self-referential) would show
    ZERO drift and pass. These tests catch exactly that class of bug by asserting
    the source text is reproduced unchanged, using distinctive content the
    transform must not touch."""

    # Distinctive, punctuation-heavy content that a naive rewrite/escape/quote
    # step would visibly corrupt (parens, colon-in-parens, em-dash, backticks,
    # comma, trailing period) — chosen so the assertion is non-tautological.
    DISTINCTIVE_DESC = (
        "Use when the caller says `go` — read/eval data (fitness: fit/unfit), "
        "then report a verdict. Never a raw dump."
    )
    DISTINCTIVE_BODY = (
        "# Heading — verbatim body\n\n"
        "A paragraph with `code`, a colon: here, an em-dash — and (parens).\n"
        "- bullet one\n- bullet two: with colon\n\n"
        "Trailing prose that must survive byte-for-byte.\n"
    )

    def test_description_text_survives_transform_verbatim(self):
        # transform() re-emits the description line via a \1 backref; assert the
        # exact distinctive text reappears unchanged (would fail if a future edit
        # re-quoted/escaped/rewrote the description scalar).
        fm = _fm("Read", description=self.DISTINCTIVE_DESC)
        out = _transform("developer", fm)
        self.assertIn(f"description: {self.DISTINCTIVE_DESC}", out)

    def test_body_survives_generate_assembly_verbatim(self):
        # generate() assembles `---\n{transform(fm)}---\n{body}`; body is handled
        # ONLY here (transform never sees it). Point SOURCE_DIR at a temp dir with
        # one synthetic agent carrying the distinctive body + description, and
        # assert BOTH reappear verbatim in the generated output.
        real_source = MOD.SOURCE_DIR
        try:
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td)
                # The whole roster is copied in, then `developer.md` is
                # overwritten with the distinctive content: `generate()` now
                # checks the roster against the directory it is handed, so a
                # one-file fixture would fail the bijection instead of the
                # verbatim assertion this test is about.
                source = _fixture_source_dir(tmp)
                (source / "developer.md").write_text(
                    f"---\nname: developer\ndescription: {self.DISTINCTIVE_DESC}\n"
                    f"tools: Read\nmodel: sonnet\n---\n"
                    f"{self.DISTINCTIVE_BODY}",
                    encoding="utf-8",
                )
                MOD.SOURCE_DIR = source
                gen = MOD.generate()
            content = gen["developer.md"]
            # Body: appended verbatim after the closing frontmatter fence.
            self.assertTrue(
                content.endswith("---\n" + self.DISTINCTIVE_BODY),
                f"body not preserved verbatim; got tail:\n{content[-200:]}",
            )
            # Description: survives through transform within the same output.
            self.assertIn(f"description: {self.DISTINCTIVE_DESC}", content)
        finally:
            MOD.SOURCE_DIR = real_source


@unittest.skipUnless(HAVE_YAML, "PyYAML not installed — run under uv")
class FrontmatterYamlValidityTests(unittest.TestCase):
    """Agent frontmatter must be VALID YAML — both the crux/agents source and the
    generated opencode/agents projection. The generator regex-extracts frontmatter
    (it does not YAML-parse it), so an invalid scalar in a source description — e.g.
    an unquoted `: ` (colon-space), which is `mapping values are not allowed here` —
    would silently propagate into opencode/agents/ and break OpenCode's loader. This
    caught a real night-gardener bug (PB-0039). Claude Code's lenient loader masks it;
    a strict YAML parser (and OpenCode) does not."""

    def _assert_all_valid(self, files):
        for f in files:
            text = f.read_text(encoding="utf-8")
            m = re.match(r"^---\n(.*?\n)---\n", text, re.S)
            self.assertIsNotNone(m, f"{f}: no frontmatter block")
            try:
                yaml.safe_load(m.group(1))
            except yaml.YAMLError as exc:
                self.fail(f"{f}: invalid YAML frontmatter — {str(exc).splitlines()[0]}")

    def test_source_agent_frontmatter_is_valid_yaml(self):
        self._assert_all_valid(sorted(MOD.SOURCE_DIR.glob("*.md")))

    def test_generated_opencode_frontmatter_is_valid_yaml(self):
        # Validate what the generator WOULD produce (independent of on-disk drift).
        gen = MOD.generate()
        for name, content in gen.items():
            m = re.match(r"^---\n(.*?\n)---\n", content, re.S)
            self.assertIsNotNone(m, f"{name}: generated output has no frontmatter")
            try:
                yaml.safe_load(m.group(1))
            except yaml.YAMLError as exc:
                self.fail(f"generated {name}: invalid YAML — {str(exc).splitlines()[0]}")


class EffectiveVerdictTests(unittest.TestCase):
    """ADR-0100 acceptance postcondition 1: per-agent effective outcomes.

    The table is asserted through `_effective`, so what is pinned is the
    resolved capability of each agent rather than the presence of a line. A
    rule re-granting an action later in the list would pass a substring
    assertion and fail this one.
    """

    def test_every_agent_resolves_its_expected_verdicts(self):
        self.assertEqual(set(AGENT_VERDICTS), EXPECTED_AGENTS)
        for agent, expected in sorted(AGENT_VERDICTS.items()):
            rules = _generated_rules(agent)
            for action in PINNED_ACTIONS:
                if action == "subagent":
                    continue
                want = "allow" if action in expected["allowed"] else "deny"
                with self.subTest(agent=agent, action=action):
                    self.assertEqual(_effective(rules, action, "anything"), want)

    def test_every_agent_resolves_its_expected_subagent_verdicts(self):
        for agent, expected in sorted(AGENT_VERDICTS.items()):
            roles = expected["subagent_roles"]
            rules = _generated_rules(agent)
            with self.subTest(agent=agent, resource="*"):
                # The broad resource is denied for every crux agent: none of the
                # ten carries a bare `Agent` grant today.
                self.assertEqual(_effective(rules, "subagent", "*"), "deny")
            for role in sorted(roles):
                with self.subTest(agent=agent, role=role):
                    self.assertEqual(_effective(rules, "subagent", role), "allow")
            for role in sorted(EXPECTED_AGENTS - set(roles)):
                with self.subTest(agent=agent, denied_role=role):
                    self.assertEqual(_effective(rules, "subagent", role), "deny")

    def test_the_commander_verdicts_named_in_the_plan(self):
        rules = _generated_rules("commander")
        self.assertEqual(_effective(rules, "subagent", "architect"), "allow")
        self.assertEqual(_effective(rules, "subagent", "developer"), "deny")
        self.assertEqual(_effective(rules, "shell", "x"), "deny")


class OrderMutationControlTests(unittest.TestCase):
    """The positive control for ADR-0100's ordering authority.

    ADR-0100's Consequences carry the ordering rule: an action's explicit deny
    PRECEDES its exceptions, which is what last-match-wins requires. The
    control takes the REAL generated rule list, permutes only the subagent
    block so the role allows precede the broad deny, and asserts the verdict
    flips. Both sides run the same `_effective`, so the control cannot pass by
    disagreeing with the resolver the verdict table uses.
    """

    def test_allow_before_deny_flips_the_verdict_to_deny(self):
        for agent in ("commander", "dev-lead", "night-gardener"):
            rules = _generated_rules(agent)
            roles = sorted(AGENT_VERDICTS[agent]["subagent_roles"])
            permuted = _permute_subagent_allows_before_deny(rules)
            with self.subTest(agent=agent):
                self.assertNotEqual(rules, permuted, "the permutation was a no-op")
                self.assertEqual(
                    Counter(map(repr, rules)), Counter(map(repr, permuted)),
                    "the permutation must reorder rules, never add or drop one",
                )
                for role in roles:
                    self.assertEqual(_effective(rules, "subagent", role), "allow")
                    self.assertEqual(_effective(permuted, "subagent", role), "deny")

    def test_an_agent_with_no_role_block_cannot_be_permuted(self):
        # Guards the control itself: `_permute_subagent_allows_before_deny`
        # refuses rather than silently returning the input unchanged, which
        # would make the test above pass vacuously on a case with no allows.
        with self.assertRaises(ValueError):
            _permute_subagent_allows_before_deny(_generated_rules("developer"))


class ActionTotalityTests(unittest.TestCase):
    """ADR-0100 point 2: totality over the pinned universe, and nothing beyond.

    Every action but `subagent` carries exactly one `"*"` rule, in the pinned
    emission order. `subagent`'s multiplicity is its grant case.
    """

    def test_emitted_action_multiset_matches_the_pinned_universe(self):
        for agent, expected in sorted(AGENT_VERDICTS.items()):
            rules = _generated_rules(agent)
            roles = expected["subagent_roles"]
            want = Counter(PINNED_ACTIONS)
            want["subagent"] = 1 + len(roles)  # the "*" deny plus one allow per role
            with self.subTest(agent=agent):
                self.assertEqual(Counter(r["action"] for r in rules), want)

    def test_non_subagent_actions_carry_exactly_one_wildcard_rule(self):
        for agent in sorted(AGENT_VERDICTS):
            rules = _generated_rules(agent)
            for action in PINNED_ACTIONS:
                if action == "subagent":
                    continue
                matching = [r for r in rules if r["action"] == action]
                with self.subTest(agent=agent, action=action):
                    self.assertEqual(len(matching), 1)
                    self.assertEqual(matching[0]["resource"], "*")

    def test_actions_appear_in_the_pinned_emission_order(self):
        for agent in sorted(AGENT_VERDICTS):
            seen = []
            for rule in _generated_rules(agent):
                if not seen or seen[-1] != rule["action"]:
                    seen.append(rule["action"])
            with self.subTest(agent=agent):
                self.assertEqual(tuple(seen), PINNED_ACTIONS)

    def test_the_renamed_v1_action_names_are_gone(self):
        # `bash` -> `shell` and `task` -> `subagent` (ADR-0100 point 2). A
        # leftover V1 name is inert under V2, so it would silently un-restrict
        # the agent rather than fail.
        for agent in sorted(AGENT_VERDICTS):
            names = {r["action"] for r in _generated_rules(agent)}
            with self.subTest(agent=agent):
                self.assertNotIn("bash", names)
                self.assertNotIn("task", names)

    def test_the_resource_wildcard_is_emitted_quoted(self):
        # A bare `*` opens a YAML alias and breaks the parse outright
        # (ADR-0100 point 2). Asserted on the raw text, because the parsed form
        # cannot tell a quoted scalar from an unquoted one.
        for agent in sorted(AGENT_VERDICTS):
            text = _GENERATED_CACHE[f"{agent}.md"] if _GENERATED_CACHE else ""
            if not text:
                _generated_rules(agent)
                text = _GENERATED_CACHE[f"{agent}.md"]
            with self.subTest(agent=agent):
                self.assertIn('    resource: "*"\n', text)
                self.assertNotIn("    resource: *\n", text)


class DroppedTopLevelKeyTests(unittest.TestCase):
    """ADR-0100 point 4: `name` and `metadata` are dropped, and that is SAFETY.

    Measured against opencode2 v0.0.0-beta-18866: beside the plural
    `permissions` array, either key ALONE misfiles the whole custom rule array
    and the agent silently falls back to the host default catalog. Emitting
    either one therefore voids every restriction the array states.
    """

    BANNED = ("name", "metadata")

    def test_generated_frontmatter_carries_neither_key(self):
        for name, content in sorted(MOD.generate().items()):
            keys = _top_level_keys(content)
            with self.subTest(agent=name):
                for banned in self.BANNED:
                    self.assertNotIn(banned, keys)

    def test_the_absence_assertion_detects_both_keys_when_present(self):
        """Positive control for the assertion above.

        The SOURCE agent files carry both keys, so the same predicate over the
        same ten files must report them. Without this, a `_top_level_keys` that
        returned the empty set would make the absence test pass on anything.
        """
        for src in sorted(MOD.SOURCE_DIR.glob("*.md")):
            keys = _top_level_keys(src.read_text(encoding="utf-8"))
            with self.subTest(source=src.name):
                for banned in self.BANNED:
                    self.assertIn(banned, keys)

    # Every spelling YAML resolves to the bare key. `name : x`, `"name": x` and
    # `'name': x` are all the key `name`, so each must be dropped and each must
    # trip the fail-closed re-check if it somehow is not. Written as (name
    # line, metadata line) pairs so both banned keys are exercised in every
    # spelling.
    KEY_SPELLINGS = (
        ("padded", "name : developer\n", "metadata :\n"),
        ("double-quoted", '"name": developer\n', '"metadata":\n'),
        ("single-quoted", "'name': developer\n", "'metadata':\n"),
        ("quoted and padded", '"name" : developer\n', "'metadata' :\n"),
    )

    def test_alternate_key_spellings_do_not_smuggle_the_keys_through(self):
        """A drop regex anchored on `^name:` passes over every spelling here.

        The key then reaches the emitted file and — per the measured V2
        behaviour — voids every rule in the array beside it. The quoted
        spellings are the ones the first cut missed: the drop, the fail-closed
        re-check AND the predicate below all read the bare spelling only, so
        the leak was invisible to its own guard and to its own test.
        """
        for label, name_line, metadata_line in self.KEY_SPELLINGS:
            fm = (
                name_line
                + "description: Use when ...\n"
                + "tools: Read\n"
                + metadata_line
                + '  tags: "agents, implementation"\n'
                + '  risk_level: "medium"\n'
            )
            with self.subTest(spelling=label):
                # Positive control FIRST: the spelling IS a key to the same
                # reader, so the absence assertions below have something to
                # find. Without it a predicate blind to the spelling would make
                # every assertion here pass vacuously — which is exactly how
                # the quoted leak survived review.
                self.assertEqual(
                    _top_level_keys(fm) & set(self.BANNED), {"name", "metadata"}
                )

                out = _transform("developer", fm)
                self.assertEqual(_top_level_keys(out) & set(self.BANNED), set())
                # Raw-text checks, independent of the predicate: neither the
                # key line nor the block's children survive.
                self.assertNotIn(name_line.rstrip("\n"), out)
                self.assertNotIn(metadata_line.rstrip("\n"), out)
                self.assertNotIn("risk_level", out)
                self.assertEqual(len(_parse_rules(out)), len(PINNED_ACTIONS))

    def test_the_fail_closed_recheck_refuses_every_key_spelling(self):
        """The re-check must not be blind to a spelling the drop is blind to.

        It shares its anchor with the drops, so it adds no coverage today — but
        it is retained as a last-line defense, and a defense that reads only
        the bare spelling is not one. Driven directly by re-inserting each
        spelling into an already-transformed frontmatter, because the drops
        upstream are what stop it firing through `transform` alone.
        """
        for label, name_line, metadata_line in self.KEY_SPELLINGS:
            for banned, line in (("name", name_line), ("metadata", metadata_line)):
                with self.subTest(spelling=label, key=banned):
                    pattern = OA._top_level_key_pattern(banned)
                    self.assertRegex(line, pattern)
        # Negative control: a longer key that merely STARTS with a banned name
        # is not a match, so the pattern is not simply matching everything.
        self.assertNotRegex("namespace: x\n", OA._top_level_key_pattern("name"))
        self.assertNotRegex("  name: x\n", OA._top_level_key_pattern("name"))

    def test_a_multi_line_metadata_block_is_dropped_whole(self):
        # The children are indented, so a single-line drop would leave orphaned
        # indentation at the top level — a YAML parse error, not a cosmetic
        # leftover. `transform` receives the frontmatter with `name:` first,
        # exactly as the real sources carry it.
        fm = (
            "name: developer\n"
            "description: Use when ...\n"
            "tools: Read\n"
            "metadata:\n"
            '  tags: "agents, implementation"\n'
            '  bundles: "crux-agents"\n'
            '  risk_level: "medium"\n'
        )
        out = _transform("developer", fm)
        self.assertEqual(_top_level_keys(out) & set(self.BANNED), set())
        self.assertNotIn("risk_level", out)
        self.assertNotIn("crux-agents", out)
        self.assertIn("description: Use when ...", out)
        # And the projection is still complete after the drop.
        self.assertEqual(len(_parse_rules(out)), len(PINNED_ACTIONS))


class DroppedKeyBlockFormTests(unittest.TestCase):
    """A dropped key written as a YAML BLOCK must take its children with it.

    Review finding, PB-0091 dev loop 1. Every Claude-only key this transform
    drops can legally be written as a block sequence or mapping. A single-line
    `re.sub` removes the key line and orphans the children at their original
    indent — and because the `permissions` array is now the LAST thing emitted,
    those orphans land inside it. `skills:\n  - propose-adr` yields
    `  - propose-adr` as a bare string element of `permissions`.

    Under the V1 map that orphan followed a mapping and was a loud YAML parse
    error, so `FrontmatterYamlValidityTests` caught it. Under the V2 array it
    parses cleanly, which is why that gate cannot see this class and these
    tests exist.
    """

    # `disallowedTools` is deliberately absent: its block form is REFUSED
    # rather than dropped, because ignoring a denial fails open. That case is
    # DisallowedToolsBlockFormTests.
    BLOCK_SOURCES = {
        "skills": "skills:\n  - propose-adr\n  - transition-adr\n",
        "metadata": 'metadata:\n  tags: "a, b"\n  risk_level: "low"\n',
        "effort": "effort:\n  level: high\n",
        "memory": "memory:\n  - project\n",
        "maxTurns": "maxTurns:\n  - 50\n",
    }

    def _out(self, block: str) -> str:
        return _transform(
            "architect", f"name: architect\ndescription: Use when ...\ntools: Read\n{block}")

    def test_a_block_valued_dropped_key_leaves_no_orphan_in_the_rule_array(self):
        for key, block in sorted(self.BLOCK_SOURCES.items()):
            out = self._out(block)
            with self.subTest(key=key):
                # `_parse_rules` is strict: an orphaned `  - propose-adr` line
                # inside the block raises rather than being skipped.
                self.assertEqual(len(_parse_rules(out)), len(PINNED_ACTIONS))
                self.assertNotIn(key, _top_level_keys(out))

    def test_the_orphan_would_otherwise_be_a_silently_valid_rule_element(self):
        """Positive control: the orphan shape parses as YAML, so the YAML gate
        cannot be what catches it.

        Asserted on a hand-built string rather than on generator output, so it
        keeps stating the hazard after the generator stops producing it.
        """
        orphaned = (
            "description: d\n"
            "permissions:\n"
            '  - action: read\n    resource: "*"\n    effect: allow\n'
            "  - propose-adr\n"
        )
        with self.assertRaises(ValueError):
            _parse_rules(orphaned)


class DisallowedToolsBlockFormTests(unittest.TestCase):
    """A `disallowedTools` block sequence must not fail OPEN.

    Review finding, PB-0091 dev loop 1. The inline-only regex silently ignored
    a block-form denial, so an agent kept the capability it revoked and the
    emitted array said `allow` with nothing to show a deny had been dropped.
    `tools:` in the same block form already refuses; this closes the asymmetry.
    """

    def test_a_block_form_disallowed_tools_is_refused_not_ignored(self):
        with self.assertRaises(MOD.SpecViolation) as ctx:
            _transform("developer",
                       "description: d\ntools: Read, Bash\ndisallowedTools:\n  - Bash\n")
        self.assertIn("disallowedTools", str(ctx.exception))

    def test_the_inline_form_still_denies(self):
        # Positive control: the refusal above is specific to the block form,
        # not a blanket refusal of every disallowedTools line.
        rules = _rules_of("developer", _fm("Read, Bash", extra="disallowedTools: Bash\n"))
        self.assertEqual(_effective(rules, "shell", "x"), "deny")

    def test_an_empty_inline_value_is_refused_too(self):
        # `disallowedTools:` with nothing after it is the same ambiguity: the
        # author wrote a denial the projection cannot read.
        with self.assertRaises(MOD.SpecViolation):
            _transform("developer", "description: d\ntools: Read\ndisallowedTools:\n")


class DryRunTests(unittest.TestCase):
    def setUp(self):
        self._real_output_dir = MOD.OUTPUT_DIR
        self._real_argv = sys.argv

    def tearDown(self):
        MOD.OUTPUT_DIR = self._real_output_dir
        sys.argv = self._real_argv

    def _run_main_dry_run(self):
        # main() takes NO args — it reads sys.argv via parse_args(); patch argv.
        # --repo-root is explicit: `main` now resolves its root from the invocation,
        # so without it these cases would take the surface-absent lane whenever the
        # suite runs from a directory that is not this checkout, and measure nothing.
        sys.argv = ["generate-opencode-agents.py", "--dry-run",
                    "--repo-root", str(REPO_ROOT)]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = MOD.main()
        return rc, buf.getvalue()

    def test_dry_run_clean_when_output_matches_generator(self):
        gen = MOD.generate()
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            for name, content in gen.items():
                (tmp / name).write_text(content, encoding="utf-8")
            MOD.OUTPUT_DIR = tmp
            rc, out = self._run_main_dry_run()
        self.assertEqual(rc, 0, f"expected clean (0); got {rc}: {out}")

    def test_dry_run_detects_drift(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "developer.md").write_text("stale wrong content", encoding="utf-8")
            MOD.OUTPUT_DIR = tmp
            rc, out = self._run_main_dry_run()
        self.assertEqual(rc, 1, "drift must exit 1")
        self.assertIn("developer.md", out)  # reported in changed/removed JSON


class FailClosedTests(unittest.TestCase):
    """The regenerator must refuse a symlinked managed leaf and a non-directory output.

    This driver used to keep its own `mkdir` + `write_text` loop instead of
    calling the shared `opencode_agents.write()`, so it had neither the
    leaf-symlink refusal nor the non-directory guard the installer and the Codex
    regenerator get from the shared module. Replacing `opencode/agents/
    architect.md` with a symlink clobbered the link's target and reported
    success (exit 0). Both paths — write and `--dry-run` — must refuse instead,
    with exit 2, a message on stderr, and nothing on stdout.
    """

    VICTIM = "VICTIM — must not be clobbered\n"

    def setUp(self):
        self._real_output_dir = MOD.OUTPUT_DIR
        self._real_argv = sys.argv

    def tearDown(self):
        MOD.OUTPUT_DIR = self._real_output_dir
        sys.argv = self._real_argv

    def _run_main_expecting_exit(self, *argv):
        sys.argv = ["generate-opencode-agents.py", *argv,
                    "--repo-root", str(REPO_ROOT)]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                MOD.main()
        return ctx.exception.code, out.getvalue(), err.getvalue()

    def _output_dir_with_symlinked_leaf(self, tmp: Path) -> tuple[Path, Path]:
        victim = tmp / "victim.md"
        victim.write_text(self.VICTIM, encoding="utf-8")
        out = tmp / "agents"
        out.mkdir()
        (out / "architect.md").symlink_to(victim)
        return out, victim

    def test_write_refuses_symlinked_managed_leaf(self):
        with tempfile.TemporaryDirectory() as td:
            out, victim = self._output_dir_with_symlinked_leaf(Path(td))
            MOD.OUTPUT_DIR = out
            code, stdout, stderr = self._run_main_expecting_exit()

            self.assertEqual(code, 2, f"stdout={stdout!r} stderr={stderr!r}")
            self.assertEqual(stdout, "", "exit 2 puts the message on stderr, nothing on stdout")
            self.assertIn("architect.md", stderr)
            self.assertEqual(victim.read_text(encoding="utf-8"), self.VICTIM)
            # Fail-closed means NOTHING was written, not "everything but the link".
            self.assertFalse((out / "developer.md").exists())

    def test_dry_run_refuses_symlinked_managed_leaf(self):
        # --dry-run reads the on-disk files to compare them; reading through a
        # link is the same trust violation as writing through it.
        with tempfile.TemporaryDirectory() as td:
            out, victim = self._output_dir_with_symlinked_leaf(Path(td))
            MOD.OUTPUT_DIR = out
            code, stdout, stderr = self._run_main_expecting_exit("--dry-run")

            self.assertEqual(code, 2, f"stdout={stdout!r} stderr={stderr!r}")
            self.assertIn("architect.md", stderr)
            self.assertEqual(victim.read_text(encoding="utf-8"), self.VICTIM)

    def test_refuses_output_dir_that_is_a_regular_file(self):
        # `mkdir(exist_ok=True)` raises FileExistsError on a non-directory, so an
        # unguarded write loop ends the run in a traceback. The contract is a
        # message on stderr and exit 2.
        with tempfile.TemporaryDirectory() as td:
            blocker = Path(td) / "agents"
            blocker.write_text("NOT A DIRECTORY\n", encoding="utf-8")
            MOD.OUTPUT_DIR = blocker
            code, stdout, stderr = self._run_main_expecting_exit()

            self.assertEqual(code, 2, f"stdout={stdout!r} stderr={stderr!r}")
            self.assertEqual(stdout, "")
            self.assertEqual(blocker.read_text(encoding="utf-8"), "NOT A DIRECTORY\n")

    def test_refuses_output_dir_that_is_a_dangling_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            target = tmp / "nowhere"
            link = tmp / "agents"
            link.symlink_to(target, target_is_directory=True)
            MOD.OUTPUT_DIR = link
            code, stdout, stderr = self._run_main_expecting_exit()

            self.assertEqual(code, 2, f"stdout={stdout!r} stderr={stderr!r}")
            self.assertTrue(link.is_symlink())
            self.assertFalse(target.exists(), "the link target must not be created")

    def test_write_into_empty_dir_reports_every_generated_file(self):
        # Happy path through the shared write(), so the refusal tests above
        # cannot pass vacuously (e.g. by refusing everything).
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "agents"
            MOD.OUTPUT_DIR = out
            sys.argv = ["generate-opencode-agents.py", "--repo-root", str(REPO_ROOT)]
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = MOD.main()
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(set(payload["written"]), {f"{n}.md" for n in EXPECTED_AGENTS})
            self.assertEqual(payload["removed"], [])
            self.assertEqual({p.name for p in out.glob("*.md")},
                             {f"{n}.md" for n in EXPECTED_AGENTS})


class CommittedOutputRosterTests(unittest.TestCase):
    """The committed projection holds exactly the generated roster — no strays.

    The shared `diff()`/`write()` scope every operation to the managed set (the
    catalog roster), because in a TARGET repo anything outside it is a user
    file. `opencode/agents/` in THIS repo is different: it is wholly generated,
    so a role dropped from both `crux/agents/` and the roster would leave a
    stale file that the roster-scoped drift gate cannot see. This test is that
    missing net.
    """

    @unittest.skipUnless(
        COMMITTED_OUTPUT_DIR.is_dir(),
        "no committed opencode/agents/ — expected outside the dev tree (a "
        "sync.sh staged tree excludes it); this is a dev-dogfood check",
    )
    def test_no_stray_files_beside_the_generated_roster(self):
        on_disk = {p.name for p in COMMITTED_OUTPUT_DIR.glob("*.md")}
        self.assertEqual(on_disk, set(MOD.generate()))


if __name__ == "__main__":
    unittest.main()
