"""Read and resolve the agent-model catalog at ``crux/catalog/models.yml``.

One loader, one parse, one error class, shared by every consumer: the two
regenerator projections (``opencode_agents``, ``codex_agents``), the catalog
validator, and the dev-repo smoke test. The catalog is hand-authored, validated,
and NEVER regenerated, so this module only ever reads it.

Resolution is ``agent -> level -> harness column``, with per-agent
short-circuits on each of the three columns::

    level(agent)    = agents[agent]                            if that value is a string
                    = agents[agent].level                      otherwise

    claude(agent)   = agents[agent].claude                     if the agent row carries one
                    = levels[level(agent)].claude              otherwise
    codex(agent)    = agents[agent].codex                      if the agent row carries one
                    = levels[level(agent)].codex               otherwise
    opencode(agent) = aliases[ agents[agent].opencode ]        if the agent row carries one
                    = aliases[ levels[level(agent)].opencode ] otherwise

**The loader gates STRUCTURE. It does not gate VALUES.** Read the split
literally, because a consumer that assumes otherwise is assuming something
false:

===========================  ====================================================
enforced by ``load()``       V0 shape and the three pinned enumerations; V1
                             the roster bijection against ``crux/agents/*.md``; V2
                             the reference graph
enforced ONLY by CI          V3 Claude alias legality; V4 the Codex pair and its
(``validate-catalog.py``)    provenance dates; V5 the model-id grammar — the
                             provider allowlist and the alias slug pattern; V6
                             the agent-frontmatter cross-check; V7 the alias
                             namespace; V8 level distinctness; V9 value
                             placement and roster-key confinement
===========================  ====================================================

So ``resolve()`` returns whatever string the file contains. A model id that
never passed V5 — wrong provider, odd characters, a backslash escape — reaches
a caller intact on a direct run that skipped CI. ``opencode_agents.transform``
interpolates that string into a regex substitution and handles it with a
replacement function for exactly this reason.

The structural three are here rather than only in the validator because a
regenerator invoked directly must fail before it writes anything: a roster that
has drifted from the agent files would otherwise resolve, and a stale extra
roster key would silently widen ``MANAGED_FILENAMES``, changing which of a
target repo's files an installer claims to own. They are re-exported as
``check_shape`` / ``check_roster`` / ``check_reference_graph`` so the validator
states the same contract without a second implementation of it. The CI-only
rules stayed in the validator because they need no in-memory catalog and their
job is to report a readable finding rather than a stack trace.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any

class SpecViolation(Exception):
    """The catalog, or the agent roster it must agree with, is out of contract.

    Shared by both projections: ``opencode_agents`` and ``codex_agents``
    re-export this name, so a caller that catches either module's
    ``SpecViolation`` catches a catalog fault too. That is deliberate — both
    installers translate exactly one exception type into their structured
    exit-2 output, and a second type would leak out as a traceback.
    """


# Import the shared loader by name when scripts/ is on sys.path (the normal
# case: a script run from this directory, or an installer that inserts it), and
# by file location otherwise (an embedded import from a test or a tool whose
# sys.path does not include scripts/). Mirrors validate-promptbook.py.
#
# THIS BLOCK MUST NOT RAISE, for the same reason the equivalent block in the
# two projections must not: this module is imported at installer module level,
# and an exception here precedes every `except SpecViolation` that could catch
# it, so the installer exits 1 with a traceback where its contract says exit 2
# with structured JSON. `spec_from_file_location` on an absent `_yaml_min.py`
# raises FileNotFoundError out of `exec_module`, so the absent case is checked
# before the load and deferred to first use as a SpecViolation.
_MISSING_YAML_MIN: str | None = None
try:  # pragma: no cover - exercised by whichever branch the caller's path hits
    from _yaml_min import CatalogYamlError, YamlCapabilityError, load_catalog_yaml
except ImportError:  # pragma: no cover
    _YAML_MIN_PATH = Path(__file__).resolve().parent / "_yaml_min.py"
    _spec = importlib.util.spec_from_file_location("_yaml_min", _YAML_MIN_PATH)
    if _spec is not None and _spec.loader is not None and _YAML_MIN_PATH.is_file():
        _yaml_min = importlib.util.module_from_spec(_spec)
        import sys as _sys

        _sys.modules.setdefault("_yaml_min", _yaml_min)
        try:
            _spec.loader.exec_module(_yaml_min)
        except Exception as _exc:  # noqa: BLE001 - re-raised on use, not here
            _sys.modules.pop("_yaml_min", None)
            _MISSING_YAML_MIN = f"cannot load {_YAML_MIN_PATH}: {_exc}"
        else:
            CatalogYamlError = _yaml_min.CatalogYamlError
            YamlCapabilityError = _yaml_min.YamlCapabilityError
            load_catalog_yaml = _yaml_min.load_catalog_yaml
    else:
        _MISSING_YAML_MIN = (
            f"cannot load {_YAML_MIN_PATH}: no such file — it ships beside "
            "models_catalog.py and the install is missing it"
        )

if _MISSING_YAML_MIN is not None:  # pragma: no cover - a broken install only
    class CatalogYamlError(ValueError):  # type: ignore[no-redef]
        """Placeholder: the real class lives in the absent `_yaml_min`."""

    class YamlCapabilityError(RuntimeError):  # type: ignore[no-redef]
        """Placeholder: the real class lives in the absent `_yaml_min`."""

    def load_catalog_yaml(text: str) -> dict:  # type: ignore[misc]
        raise SpecViolation(_MISSING_YAML_MIN)


PLUGIN_ROOT = Path(__file__).resolve().parent.parent  # crux/
CATALOG_PATH = PLUGIN_ROOT / "catalog" / "models.yml"
AGENTS_DIR = PLUGIN_ROOT / "agents"

# ─────────────────────────── pinned schema constants ──────────────────────
# Each of these is pinned by rule V0: the file cannot widen itself. Changing
# one is a deliberate validator edit as well as a file edit.

SCHEMA_VERSION = "4"

TOP_LEVEL_KEYS = frozenset(
    {"schema_version", "providers", "aliases", "agents", "levels", "claude_aliases"}
)
LEVEL_NAMES = ("apex", "flagship", "standard")
CLAUDE_ALIASES = ["fable", "opus", "sonnet", "haiku", "inherit", "claude-opus-5-5", "claude-sonnet-5-5"]
CODEX_KEYS = frozenset({"model", "reasoning_effort", "verified", "source"})
REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh"})

AGENT_ROW_REQUIRED = frozenset({"level"})
AGENT_ROW_OPTIONAL = frozenset({"opencode", "codex", "claude"})
LEVEL_ROW_REQUIRED = frozenset({"claude", "codex"})
LEVEL_ROW_OPTIONAL = frozenset({"opencode"})


# ────────────────────────────── the value types ───────────────────────────


@dataclass(frozen=True)
class CodexRuntime:
    """One resolved Codex cell: the pair Codex consumes, plus provenance.

    ``verified`` and ``source`` are documentary. Rule V4 checks that the date
    is real and warns past 180 days, so both fields have a reader — but no code
    path behaves differently because a row was verified.
    """

    model: str
    reasoning_effort: str
    verified: str
    source: str


@dataclass(frozen=True)
class Level:
    """One rung. ``opencode`` is optional: a level that omits it forces every
    agent at that level to name its own (rule V2c)."""

    claude: str
    codex: CodexRuntime
    opencode: str | None = None


@dataclass(frozen=True)
class AgentRow:
    """One roster row: a level with optional Claude Code, OpenCode and Codex
    overrides. Each override replaces one column and leaves the other two
    resolving from the level."""

    level: str
    opencode: str | None = None
    codex: CodexRuntime | None = None
    claude: str | None = None


@dataclass(frozen=True)
class Resolution:
    """The resolved triple for one agent. ``opencode`` is the expanded
    ``provider/model-id``, not the alias name."""

    claude: str
    opencode: str
    codex: CodexRuntime


@dataclass(frozen=True)
class ModelsCatalog:
    schema_version: str
    providers: tuple[str, ...]
    aliases: dict[str, str]
    agents: dict[str, AgentRow]
    levels: dict[str, Level]
    claude_aliases: tuple[str, ...]

    def level_of(self, agent: str) -> str:
        row = self.agents.get(agent)
        if row is None:
            raise SpecViolation(
                f"{agent!r}: no entry in models.yml `agents`; refusing to guess a level"
            )
        return row.level

    def resolve(self, agent: str) -> Resolution:
        """Resolve one agent to its three harness values. Fail-closed: an agent
        outside the roster raises rather than defaulting."""
        row = self.agents.get(agent)
        if row is None:
            raise SpecViolation(
                f"{agent!r}: no entry in models.yml `agents`; refusing to guess a model"
            )
        level = self.levels.get(row.level)
        if level is None:  # pragma: no cover - V2a makes this unreachable post-load
            raise SpecViolation(f"{agent!r}: level {row.level!r} is not a `levels` key")
        alias = row.opencode if row.opencode is not None else level.opencode
        if alias is None:  # pragma: no cover - V2c makes this unreachable post-load
            raise SpecViolation(
                f"{agent!r}: level {row.level!r} declares no `opencode` cell and the agent "
                "row carries no override"
            )
        model_id = self.aliases.get(alias)
        if model_id is None:  # pragma: no cover - V2a makes this unreachable post-load
            raise SpecViolation(f"{agent!r}: alias {alias!r} is not declared in `aliases`")
        codex = row.codex if row.codex is not None else level.codex
        claude = row.claude if row.claude is not None else level.claude
        return Resolution(claude=claude, opencode=model_id, codex=codex)

    def opencode_alias(self, agent: str) -> str:
        """The alias NAME an agent resolves through (not its expansion).

        Exposed so a test can bind the routing without hard-coding a model id
        that the owner may bump in a one-line alias edit (ADR-0072 AC-9).
        """
        row = self.agents[agent]
        alias = row.opencode if row.opencode is not None else self.levels[row.level].opencode
        if alias is None:  # pragma: no cover - V2c makes this unreachable post-load
            raise SpecViolation(
                f"{agent!r}: neither the agent row nor level {row.level!r} names an alias"
            )
        return alias

    @property
    def managed_filenames(self) -> frozenset[str]:
        """The roster, as output filenames. This IS the definition of
        "crux-managed" for the OpenCode installer, and rule V1 has already
        checked it against the agent files on disk — so it tracks the
        filesystem rather than a model table."""
        return frozenset(f"{name}.md" for name in self.agents)


# ──────────────────────────────── the rules ───────────────────────────────


def _is_str_seq(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def check_shape(raw: dict) -> list[str]:
    """Rule V0 — schema and shape. Which keys exist and what type each value has.

    Owns shape and nothing else: no reference is followed here, and no value is
    checked for legality beyond its type and the three pinned enumerations.
    """
    findings: list[str] = []

    version = raw.get("schema_version")
    if version != SCHEMA_VERSION:
        findings.append(
            f"schema_version must be {SCHEMA_VERSION!r}, got {version!r} — an unrecognized "
            "version is refused, never ignored (older schemas are not migrated)"
        )

    # `key=repr` on every sort whose keys come from the DOCUMENT rather than
    # from a constant in this file. A YAML mapping key need not be a string —
    # `123: standard` parses to an int on the PyYAML path — and sorting a mixed
    # int/str set raises TypeError. An unhandled TypeError here breaches both
    # callers' exit-code contracts: `validate-catalog.py` and
    # `generate-opencode-agents.py` each promise exit 2 with a message on
    # stderr, and a traceback gives exit 1 with empty stdout, where exit-1
    # stdout is contractually the JSON report. `repr` totalizes the order
    # without hiding the offending key, which the V0 findings below then name.
    extra = sorted(set(raw) - TOP_LEVEL_KEYS, key=repr)
    missing = sorted(TOP_LEVEL_KEYS - set(raw))
    if extra:
        findings.append(f"unknown top-level key(s): {extra}")
    if missing:
        findings.append(f"missing top-level key(s): {missing}")

    providers = raw.get("providers")
    if not _is_str_seq(providers):
        findings.append("`providers` must be a sequence of strings")

    aliases = raw.get("aliases")
    if not isinstance(aliases, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in aliases.items()
    ):
        findings.append("`aliases` must be a mapping of string to string")

    agents = raw.get("agents")
    if not isinstance(agents, dict):
        findings.append("`agents` must be a mapping")
    else:
        for name, value in sorted(agents.items(), key=lambda kv: repr(kv[0])):
            # The KEY's TYPE, checked at V0 before anything downstream sorts,
            # compares or path-joins it. A roster key becomes a filename
            # component in rule V6 and a dict key in every later rule; a
            # non-string key therefore reaches `sorted()` in V1/V2 and in the
            # validator, where mixing it with the string keys raises TypeError
            # and turns a document verdict into a traceback. V9 asks the
            # SHAPE question of a string key (`/`, `\`, a leading dot); this
            # asks the prior question, so the two do not overlap.
            if not isinstance(name, str):
                findings.append(
                    f"agents key {name!r} must be a string — an unquoted `123:` or `true:` "
                    "is a non-string YAML key, and a roster key is resolved as a filename"
                )
                continue
            if isinstance(value, str):
                continue
            if not isinstance(value, dict):
                findings.append(f"agents.{name!r} must be a string or a mapping")
                continue
            keys = set(value)
            unknown = sorted(keys - AGENT_ROW_REQUIRED - AGENT_ROW_OPTIONAL, key=repr)
            if unknown:
                findings.append(
                    f"agents.{name!r}: unknown key(s) {unknown} — the override key set is "
                    f"{sorted(AGENT_ROW_OPTIONAL)}; the resolver reads no other key, so this one "
                    "is refused rather than ignored"
                )
            if "level" not in keys:
                findings.append(f"agents.{name!r}: missing required key 'level'")
            for key in ("level", "opencode", "claude"):
                if key in keys and not isinstance(value[key], str):
                    findings.append(f"agents.{name!r}.{key} must be a string")
            codex = value.get("codex")
            if "codex" in keys:
                if not isinstance(codex, dict):
                    findings.append(f"agents.{name!r}.codex must be a mapping")
                else:
                    if set(codex) != CODEX_KEYS:
                        findings.append(
                            f"agents.{name!r}.codex key set must be exactly "
                            f"{sorted(CODEX_KEYS)}, got {sorted(codex, key=repr)}"
                        )
                    for key, cval in sorted(codex.items(), key=lambda kv: repr(kv[0])):
                        if not isinstance(cval, str):
                            findings.append(f"agents.{name!r}.codex.{key!r} must be a string")

    levels = raw.get("levels")
    if not isinstance(levels, dict):
        findings.append("`levels` must be a mapping")
    else:
        if sorted(levels, key=repr) != sorted(LEVEL_NAMES):
            findings.append(
                f"`levels` key set must be exactly {sorted(LEVEL_NAMES)}, "
                f"got {sorted(levels, key=repr)}"
            )
        for name, value in sorted(levels.items(), key=lambda kv: repr(kv[0])):
            if not isinstance(value, dict):
                findings.append(f"levels.{name!r} must be a mapping")
                continue
            keys = set(value)
            unknown = sorted(keys - LEVEL_ROW_REQUIRED - LEVEL_ROW_OPTIONAL, key=repr)
            if unknown:
                findings.append(f"levels.{name!r}: unknown key(s) {unknown}")
            for key in sorted(LEVEL_ROW_REQUIRED - keys):
                findings.append(f"levels.{name!r}: missing required key {key!r}")
            if "claude" in keys and not isinstance(value["claude"], str):
                findings.append(f"levels.{name!r}.claude must be a string")
            if "opencode" in keys and not isinstance(value["opencode"], str):
                findings.append(f"levels.{name!r}.opencode must be a string")
            codex = value.get("codex")
            if "codex" in keys:
                if not isinstance(codex, dict):
                    findings.append(f"levels.{name!r}.codex must be a mapping")
                else:
                    if set(codex) != CODEX_KEYS:
                        findings.append(
                            f"levels.{name!r}.codex key set must be exactly "
                            f"{sorted(CODEX_KEYS)}, got {sorted(codex, key=repr)}"
                        )
                    for key, cval in sorted(codex.items(), key=lambda kv: repr(kv[0])):
                        if not isinstance(cval, str):
                            findings.append(f"levels.{name!r}.codex.{key!r} must be a string")

    claude_aliases = raw.get("claude_aliases")
    if not _is_str_seq(claude_aliases):
        findings.append("`claude_aliases` must be a sequence of strings")
    elif claude_aliases != CLAUDE_ALIASES:
        findings.append(
            f"`claude_aliases` must be exactly {CLAUDE_ALIASES}, got {claude_aliases} — "
            "membership is pinned here so the file cannot widen its own enum"
        )

    return findings


def check_roster(raw: dict, agents_dir: Path) -> list[str]:
    """Rule V1 — roster bijection.

    Set equality between two SETS, checked in both directions: the `agents` key
    set and the stems of ``<agents_dir>/*.md``. The `agents` mapping itself is
    many-to-one onto levels and is in bijection with nothing.
    """
    agents = raw.get("agents")
    if not isinstance(agents, dict):
        return []  # V0 already reported it; a second finding would be noise.

    on_disk = {p.stem for p in agents_dir.glob("*.md") if p.is_file()}
    if not on_disk:
        return [f"no agent files found under {agents_dir} — refusing to validate an empty roster"]

    # A symlinked leaf is DELIBERATELY still counted present here, and the
    # refusal lives at the two reads instead: `opencode_agents.parse_source`
    # and `codex_agents.parse_source`, which are what actually project a
    # leaf's bytes into a generated agent file. Two reasons the guard is not
    # also here. (1) This rule answers set equality — the roster names a file
    # and the file exists — and a link satisfies that; the judgement about
    # FOLLOWING it belongs at the read that follows it. (2) A V1 finding
    # closes the [SECURITY:S3] gate below in `validate-catalog.py`, so adding
    # the refusal here would silence rule V6 and with it
    # `_agent_md_path`'s containment layer — the layer that reports a
    # symlinked leaf as escaping `agents/`. Hardening this rule would make
    # that third layer dead code for the one case it exists to catch.
    findings: list[str] = []
    roster = set(agents)
    # `key=repr` because a roster key need not be a string: `123: standard` is
    # a legal YAML mapping key, and sorting it beside the string stems raises
    # TypeError. V0 reports the non-string key; this sort must not crash before
    # the caller can print that finding.
    stale = sorted(roster - on_disk, key=repr)
    if stale:
        findings.append(
            f"`agents` names {stale} with no matching {agents_dir.name}/*.md file — a stale "
            "roster entry would widen the installer's managed file set"
        )
    # No `key=repr` here, deliberately: `on_disk` is built from `Path.stem`,
    # so this difference is all-`str` by construction and a repr key would be
    # a guard no input can exercise — the same unfalsifiable-guard defect this
    # cycle removed elsewhere.
    unlisted = sorted(on_disk - roster)
    if unlisted:
        findings.append(f"agent file(s) {unlisted} have no `agents` entry in models.yml")
    return findings


def check_reference_graph(raw: dict) -> list[str]:
    """Rule V2 — the reference graph. Three clauses: forward, backward, totality."""
    agents = raw.get("agents")
    levels = raw.get("levels")
    aliases = raw.get("aliases")
    if not isinstance(agents, dict) or not isinstance(levels, dict) or not isinstance(aliases, dict):
        return []  # V0 owns the shape verdict.

    findings: list[str] = []
    rows = {name: _coerce_row(value) for name, value in agents.items()}

    # Every sort below keys on `repr` for the reason V0 does: a mapping key
    # read out of the document need not be a string, and a mixed int/str sort
    # raises TypeError where the caller's contract promises a finding.
    # (a) Forward: every reference resolves.
    for name, row in sorted(rows.items(), key=lambda kv: repr(kv[0])):
        if row is None:
            continue
        if row.level not in levels:
            findings.append(f"agents.{name}: level {row.level!r} is not a `levels` key")
        if row.opencode is not None and row.opencode not in aliases:
            findings.append(
                f"agents.{name}.opencode: alias {row.opencode!r} is not declared in `aliases`"
            )
    for level_name, level in sorted(levels.items(), key=lambda kv: repr(kv[0])):
        if not isinstance(level, dict):
            continue
        alias = level.get("opencode")
        if isinstance(alias, str) and alias not in aliases:
            findings.append(
                f"levels.{level_name}.opencode: alias {alias!r} is not declared in `aliases`"
            )

    # (b) Backward: every rung, and every default within a rung, is reached.
    for level_name, level in sorted(levels.items(), key=lambda kv: repr(kv[0])):
        users = [n for n, r in rows.items() if r is not None and r.level == level_name]
        if not users:
            findings.append(
                f"levels.{level_name}: no agent maps to this level — a rung nothing reaches"
            )
            continue
        if isinstance(level, dict) and isinstance(level.get("opencode"), str):
            inheritors = [n for n in users if rows[n] is not None and rows[n].opencode is None]
            if not inheritors:
                findings.append(
                    f"levels.{level_name}.opencode: every agent at this level overrides it — "
                    "a default within a rung that nothing inherits"
                )
        if isinstance(level, dict) and "codex" in level:
            inheritors = [n for n in users if rows[n] is not None and rows[n].codex is None]
            if not inheritors:
                findings.append(
                    f"levels.{level_name}.codex: every agent at this level overrides it — "
                    "a default within a rung that nothing inherits"
                )
        if isinstance(level, dict) and "claude" in level:
            inheritors = [n for n in users if rows[n] is not None and rows[n].claude is None]
            if not inheritors:
                findings.append(
                    f"levels.{level_name}.claude: every agent at this level overrides it — "
                    "a default within a rung that nothing inherits"
                )

    # (c) Totality: opencode(agent) is defined for every agent.
    for name, row in sorted(rows.items(), key=lambda kv: repr(kv[0])):
        if row is None or row.opencode is not None:
            continue
        level = levels.get(row.level)
        if isinstance(level, dict) and not isinstance(level.get("opencode"), str):
            findings.append(
                f"agents.{name}: level {row.level!r} omits its `opencode` cell, so this agent "
                "must carry an `opencode` override — resolution would otherwise be partial"
            )

    return findings


def _coerce_row(value: Any) -> AgentRow | None:
    if isinstance(value, str):
        return AgentRow(level=value)
    if isinstance(value, dict) and isinstance(value.get("level"), str):
        opencode = value.get("opencode")
        claude = value.get("claude")
        codex = value.get("codex")
        runtime = None
        if (
            isinstance(codex, dict)
            and set(codex) == CODEX_KEYS
            and all(isinstance(codex[key], str) for key in CODEX_KEYS)
        ):
            runtime = CodexRuntime(**{key: codex[key] for key in sorted(CODEX_KEYS)})
        return AgentRow(
            level=value["level"],
            opencode=opencode if isinstance(opencode, str) else None,
            codex=runtime,
            claude=claude if isinstance(claude, str) else None,
        )
    return None


# ──────────────────────────────── the loader ──────────────────────────────


def load_raw(catalog_path: Path = CATALOG_PATH) -> dict:
    """Read the catalog through the strict shared loader.

    Every failure mode — absent file, unreadable file, a refused construct, a
    duplicate key, a non-mapping root — becomes a ``SpecViolation`` so callers
    handle one type. ``YamlCapabilityError`` is deliberately NOT converted: it
    is an environment problem, and collapsing it into a document verdict would
    tell a user their catalog is broken when their interpreter is.
    """
    try:
        text = catalog_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecViolation(f"cannot read {catalog_path}: {exc}") from exc
    try:
        return load_catalog_yaml(text)
    except CatalogYamlError as exc:
        raise SpecViolation(f"{catalog_path}: {exc}") from exc


def load(catalog_path: Path = CATALOG_PATH, agents_dir: Path = AGENTS_DIR) -> ModelsCatalog:
    """Load, validate fail-closed, and return the resolved catalog.

    Runs rules V0, V1 and V2 in that order and raises ``SpecViolation`` listing
    every finding before returning anything. Callers therefore never hold a
    catalog that failed a rule, and no filesystem mutation can happen behind a
    drifted roster.
    """
    raw = load_raw(catalog_path)
    findings = check_shape(raw)
    if not findings:
        # V1 and V2 read the shapes V0 just proved; running them over a
        # shape-invalid document would produce cascades, not information.
        findings = check_roster(raw, agents_dir) + check_reference_graph(raw)
    if findings:
        detail = "\n  - ".join(findings)
        raise SpecViolation(f"{catalog_path} violates the models catalog contract:\n  - {detail}")

    levels = {
        name: Level(
            claude=row["claude"],
            codex=CodexRuntime(**{k: row["codex"][k] for k in sorted(CODEX_KEYS)}),
            opencode=row.get("opencode"),
        )
        for name, row in raw["levels"].items()
    }
    agents = {name: _coerce_row(value) for name, value in raw["agents"].items()}
    # Reached only after V0 passed, which proves every roster key is a `str`.
    uncoercible = sorted(name for name, row in agents.items() if row is None)
    if uncoercible:
        # Unreachable while V0 holds. Raising rather than dropping is the point:
        # a silently discarded roster row would narrow the installer's managed
        # file set, which is the failure the fail-closed loader exists to stop.
        raise SpecViolation(f"{catalog_path}: uncoercible `agents` row(s): {uncoercible}")
    return ModelsCatalog(
        schema_version=raw["schema_version"],
        providers=tuple(raw["providers"]),
        aliases=dict(raw["aliases"]),
        agents=agents,
        levels=levels,
        claude_aliases=tuple(raw["claude_aliases"]),
    )


def managed_filenames(
    catalog_path: Path = CATALOG_PATH, agents_dir: Path = AGENTS_DIR
) -> frozenset[str]:
    """The crux-managed OpenCode output filenames, derived from the roster."""
    return load(catalog_path, agents_dir).managed_filenames
