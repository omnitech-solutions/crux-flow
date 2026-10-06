"""Project crux's role definitions into OpenCode agent Markdown.

Per ADR-0043, `crux/agents/*.md` (Claude Code format) is the single source of
truth and `opencode/agents/*.md` is a regenerated projection in OpenCode agent
format. This module holds the projection logic; it is shared by two callers,
exactly as `codex_agents.py` is:

  - `generate-opencode-agents.py` — the dev-repo regenerator for the committed
    `opencode/agents/` output (the third regenerative output).
  - `crux/skills/install-opencode-agents/scripts/install.py` — the installer
    that writes the same projection into a target repo's `.opencode/agents/`
    (the plural directory OpenCode V2 resolves; the singular `.opencode/agent/`
    is the legacy location that installer migrates out of).

Transformation rules (locked by ADR-0043 Decision point 3, amended by
ADR-0100 for the OpenCode V2 schema):
  - body: verbatim
  - description: verbatim
  - mode: subagent (added)
  - model: the Claude Code `model:` value, a family alias or a full model ID,
    replaced by the OpenCode value the shared catalog resolves for this agent
    (crux/catalog/models.yml, via models_catalog)
  - tools -> permissions: the comma-separated tool string becomes an ORDERED
    array of {action, resource, effect} rules, resolved last-match-wins, over
    the pinned ACTION_UNIVERSE; granted actions map to `allow`, everything
    else to an explicit `deny`; `list` is allowed wherever Read is granted;
    Edit/Write jointly map to the single `edit` action (hard error if granted
    asymmetrically); a Claude-side tool with no TOOL_MAP entry is a hard error
  - subagent: the one action whose rule count depends on the grant case. A
    restricted `Agent(role)` grant emits the `"*"` deny FIRST and one allow
    per sorted role after it; a bare `Agent` emits a single `"*"` allow; no
    grant, or a `disallowedTools` denial of Agent, emits a single `"*"` deny.
  - name: DROPPED
  - metadata: DROPPED

Two of those need their reasons stated, because both look like tidiness and
neither is.

**The deny is load-bearing, and so is its position.** V2 resolves the array
last-match-wins, so a broad deny placed AFTER the exception it is meant to
refine cancels that exception instead of bounding it. Deny-first is therefore
the only order that expresses "nobody but these roles". Totality over the
pinned universe is what makes an emitted `deny` a statement: an action carrying
no rule at all falls through to the host default and states nothing. The
universe-extension clause is unchanged — an action absent from ACTION_UNIVERSE
is not emitted and falls back to OpenCode's default, and extending the universe
stays a deliberate one-line edit to the constant.

**Dropping `name` and `metadata` is a safety requirement.** Measured against
opencode2 v0.0.0-beta-18866: beside the plural `permissions` array, a top-level
`name` key OR a `metadata` key — either one alone — misfiles the whole custom
rule array and the agent falls back to the host's default catalog, with no
warning in stdout or the service log. Emitting either key therefore voids every
restriction every rule in the array states. The singular V1 `permission:` map
did not have this behaviour, which is why the keys were safe to pass through
before and are not now. V2 derives the agent id from the file path, so `name`
carries no information either way. `transform` re-checks for both keys after
the drop and refuses rather than emitting a silently-unrestricted agent.

Filenames are the bare role name (`architect.md`), NOT namespaced the way the
Codex projection namespaces to `crux-architect.toml`. OpenCode derives an
agent's name from its filename, and ADR-0048 settled collision handling for
this projection by *choosing non-colliding role names* (scout -> wayfinder)
rather than by prefixing. One name per role across both Claude Code and
OpenCode is the point; a `crux-` prefix here would reintroduce the divergence
ADR-0048 removed. Codex keeps its own `crux_` namespace.

That choice has a consequence the installer must respect: crux-managed files
in a target repo are not identifiable by a filename glob. The managed set is
therefore the definition of "crux-managed", and `diff()`/`write()` never touch
anything outside it. A target project's own `.opencode/agents/*.md` files are
invisible to this module.

The managed set is the catalog's agent ROSTER, which the loader has already
checked against the `crux/agents/*.md` glob in both directions. It is no longer
a side-effect of a model table: a model table could not change shape without
changing which of a target repo's files this installer claims to own.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any


class _BrokenInstall(Exception):
    """Stands in for `models_catalog.SpecViolation` when that module is absent.

    Only bound when `models_catalog.py` could not be loaded at all, which means
    the install is missing half of a pair that ships together. Nothing catches
    this name specifically — it exists so `SpecViolation` is always importable
    from this module, which is what lets the failure be reported at first use
    instead of crashing the installer's own import statement.
    """


class _DeferredCatalogFault:
    """A `models_catalog` stand-in that raises on first real use.

    Every attribute access raises, carrying the original import error as the
    cause. `SpecViolation` is the one attribute served normally, so a caller
    that catches `models_catalog.SpecViolation` still has a class to catch.
    """

    def __init__(self, path: Path, cause: BaseException) -> None:
        self._path = path
        self._cause = cause
        self.SpecViolation = _BrokenInstall

    def __getattr__(self, name: str) -> Any:
        raise _BrokenInstall(
            f"cannot load {self._path}: {self._cause} — this file ships beside "
            "opencode_agents.py and the install is missing it"
        ) from self._cause


# Import the shared catalog reader by name when scripts/ is on sys.path (the
# regenerator and the installer both put it there), and by file location
# otherwise. Mirrors how validate-promptbook.py reaches _yaml_min.
#
# THIS BLOCK MUST NOT RAISE. `install-opencode-agents` does
# `from opencode_agents import SpecViolation, diff, generate, write` at module
# level and turns exactly one exception type into structured exit-2 JSON. An
# exception raised while THIS module executes happens before that import
# completes, so no `except SpecViolation` anywhere can catch it: the installer
# dies with a traceback and exit 1, and exit-1 stdout is contractually the JSON
# diff report. A missing `models_catalog.py` beside this file did exactly that
# — `spec_from_file_location` on an absent path raises FileNotFoundError from
# `exec_module`. The failure is therefore DEFERRED to first use, where it
# becomes the SpecViolation the lane is built to report.
#
# Only the staged release artifact can see this. The dev tree always has
# `models_catalog.py` on the path, so the dev suite exercises the happy branch
# and the fixture below is what pins the other one.
try:  # pragma: no cover - one branch per caller's sys.path
    import models_catalog
except ImportError:  # pragma: no cover
    models_catalog = None

if models_catalog is None:
    _CATALOG_PATH = Path(__file__).resolve().parent / "models_catalog.py"
    _spec = importlib.util.spec_from_file_location("models_catalog", _CATALOG_PATH)
    if _spec is not None and _spec.loader is not None and _CATALOG_PATH.is_file():
        models_catalog = importlib.util.module_from_spec(_spec)
        # Register before exec: `dataclasses` resolves a class's own module out
        # of sys.modules while processing it, and an unregistered module makes
        # that lookup return None.
        sys.modules.setdefault("models_catalog", models_catalog)
        try:
            _spec.loader.exec_module(models_catalog)
        except Exception as _exc:  # noqa: BLE001 - re-raised on use, not here
            sys.modules.pop("models_catalog", None)
            models_catalog = _DeferredCatalogFault(_CATALOG_PATH, _exc)
    else:
        models_catalog = _DeferredCatalogFault(
            _CATALOG_PATH, FileNotFoundError(str(_CATALOG_PATH))
        )

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = PLUGIN_ROOT / "agents"

# Re-exported, not redefined. `install-opencode-agents` catches exactly one
# exception type and turns it into structured exit-2 JSON; a second type would
# escape as a traceback, so a catalog fault and a source fault must be the same
# class. On a broken install this is `_BrokenInstall`, served by the stand-in
# above so the name is importable either way.
SpecViolation = models_catalog.SpecViolation

# Agent-keyed mode map. Default is subagent (ADR-0043 Decision point 3);
# commander and night-gardener are `all` so they can also run as primary
# agents (Tab-selectable / scheduled sessions) — a narrow, recorded extension
# of the locked rule, encoded here rather than hand-edited into the output.
MODE_DEFAULT = "subagent"
MODE_MAP = {
    "commander": "all",
    "night-gardener": "all",
}

# Pinned V2 action universe, in the pinned EMISSION order (ADR-0100 point 2,
# replacing ADR-0043's 11-key permission universe). Two members are renamed
# from V1: `bash` -> `shell` and `task` -> `subagent`. Extending this is a
# deliberate one-line edit, reviewed like any transform change; an action
# absent from it is not emitted and falls back to OpenCode's own default.
ACTION_UNIVERSE = [
    "read", "grep", "glob", "list", "edit", "shell",
    "subagent", "skill", "todowrite", "webfetch", "websearch",
]

# The resource wildcard, emitted QUOTED. A bare `*` opens a YAML alias and
# breaks the parse outright, so this is not a style choice.
WILDCARD = "*"

# Claude Code tool name -> OpenCode V2 action. Edit/Write are handled jointly
# (both gate under the single `edit` action). The dispatch tool `Agent`
# (renamed from `Task` per ADR-0092) and its restricted `Agent(role)` form are
# handled specially in transform(), not here, because `subagent` is the one
# action whose rule COUNT depends on the grant case (ADR-0100 point 3).
TOOL_MAP = {
    "Read": "read",
    "Grep": "grep",
    "Glob": "glob",
    "Bash": "shell",
    "Skill": "skill",
    "TodoWrite": "todowrite",
    "WebFetch": "webfetch",
    "WebSearch": "websearch",
}

# ADR-0092 item 3 projection, in V2 vocabulary: Claude Code tool label ->
# OpenCode action to deny for the agent `disallowedTools` field. Same universe
# as TOOL_MAP, plus the joint Edit/Write -> edit and Agent -> subagent mappings.
DENY_TOOL_MAP = {**TOOL_MAP, "Edit": "edit", "Write": "edit", "Agent": "subagent"}

# Top-level keys that must never reach the emitted frontmatter beside the
# `permissions` array — see the module docstring for the measured failure.
BANNED_EMITTED_KEYS = ("name", "metadata")

_AGENT_TOKEN_RE = re.compile(r"^Agent(?:\((?P<role>[a-z0-9-]+)\))?$")


def managed_filenames(catalog: Any | None = None) -> frozenset[str]:
    """The crux-managed output filenames — the catalog roster, as filenames.

    This IS the definition of "crux-managed" for the installer (see the module
    docstring): with bare role filenames there is no prefix to glob for, so
    membership is by this set.

    A function rather than a constant computed at import, because computing it
    at import would turn a malformed catalog into an ImportError inside the
    installer — which handles SpecViolation and nothing else, so the error
    would surface as a traceback instead of its documented exit-2 JSON.
    """
    if catalog is None:
        catalog = models_catalog.load()
    return catalog.managed_filenames


def __getattr__(name: str) -> Any:
    """Resolve the historical `MANAGED_FILENAMES` spelling lazily (PEP 562).

    Callers and tests that read the module attribute keep working; the file
    read happens on access rather than on import, which is what keeps a
    malformed catalog raising SpecViolation instead of ImportError.
    """
    if name == "MANAGED_FILENAMES":
        return managed_filenames()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _top_level_key_pattern(key: str) -> str:
    """Regex matching a top-level frontmatter `key:`, bare or quoted.

    YAML resolves `name`, `"name"` and `'name'` to the SAME key, so a pattern
    anchored on the bare spelling alone passes over the quoted ones. That gap
    is load-bearing rather than cosmetic here: `_drop_block_key` and the
    banned-key re-check in `transform` both use this, and a `"name":` that
    survived either one reaches the emitted frontmatter beside the
    `permissions` array, where it voids every rule in that array.

    Whitespace before the colon is matched for the same reason — `metadata :`
    is the key `metadata`. Quoting styles YAML also accepts but no agent file
    uses (block scalars, flow mappings, escaped quotes inside a double-quoted
    key) are out of scope; the re-check in `transform` is what stops a spelling
    this misses from being written silently.
    """
    esc = re.escape(key)
    return rf"^(?:{esc}|\"{esc}\"|'{esc}')[ \t]*:"


def _drop_block_key(text: str, key: str) -> str:
    """Drop a top-level frontmatter key together with its indented block body.

    `metadata:` is a nested mapping, so the single-line regex the other dropped
    keys use would leave its children behind as orphaned indentation at the top
    level — a YAML parse error rather than a cosmetic leftover. Lines are
    dropped until the next line that starts at column 0 with a non-space
    character, which is where the next top-level key begins.

    Quoted and space-padded spellings are matched via `_top_level_key_pattern`,
    because YAML matches them: `metadata :`, `"metadata":` and `'metadata':`
    are all the key `metadata`. A pattern demanding an unquoted name followed
    immediately by the colon passes over those spellings and leaves the key in
    the emitted file.
    """
    pattern = _top_level_key_pattern(key)
    out_lines: list[str] = []
    dropping = False
    for line in text.splitlines(keepends=True):
        if dropping:
            if not line.strip() or line[:1] in (" ", "\t"):
                continue
            dropping = False
        if re.match(pattern, line):
            dropping = True
            continue
        out_lines.append(line)
    return "".join(out_lines)


def parse_source(path: Path) -> tuple[str, str]:
    """Return (frontmatter_text, body_text) of a source agent file.

    **[SECURITY:S5] A symlinked source leaf is refused.** This read is the one
    place a `crux/agents/*.md` file's bytes enter a generated agent file, so a
    link planted at that path renders its target's content into
    `opencode/agents/<role>.md` — content from outside the tree the roster
    describes. `models_catalog.check_roster` deliberately does not refuse it
    (see the note there: doing so would close the [SECURITY:S3] gate and make
    `validate-catalog.py`'s containment layer dead code), so the guard sits
    here, on the read it protects. Checked before `read_text`, which also
    turns a DANGLING link into a `SpecViolation` instead of the
    `FileNotFoundError` traceback that would breach the driver's exit-2
    contract. Mirrored in `codex_agents.parse_source`; change one, change both.
    """
    if path.is_symlink():
        raise SpecViolation(
            f"{path}: source agent is a symlink — refusing to follow it, because its "
            "target's content would be rendered into the generated agent file"
        )
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?\n)---\n", text, re.S)
    if not m:
        raise SpecViolation(f"{path}: no frontmatter block")
    return m.group(1), text[m.end():]


def transform(name: str, fm: str, model: str) -> str:
    """Transform one source frontmatter block into the OpenCode form.

    `model` is the already-resolved OpenCode value for this agent. Resolution
    happens once per run in `generate()`, not once per file, so one catalog
    read serves the whole projection.

    **`model` reaches a regex substitution below.** It is catalog-derived, and
    the loader does not check its grammar — the slug rule (V5) is CI-side. So
    the substitution uses a replacement FUNCTION rather than a template string:
    a template expands `\\1`, `\\g<name>` and `\\\\` inside the replacement, which
    made a `\\1` in an alias value splice the matched description line into the
    emitted `model:` value. A function's return value is inserted literally, so
    that class of surprise cannot arise however the alias table is edited.

    Literal insertion is not enough on its own: the value lands on a `model:`
    LINE, so a newline inside it splices whatever follows into the emitted
    frontmatter as sibling top-level keys — including a `permissions:` or
    `metadata:` key this projection would otherwise never write. The grammar
    that would exclude that (V5, the slug rule) is checked CI-side, not by the
    loader, so it is re-checked here on the value's own terms: a control
    character in `model` is refused rather than emitted.
    """
    bad = sorted({c for c in model if ord(c) < 0x20 or ord(c) == 0x7F})
    if bad:
        raise SpecViolation(
            f"{name}: model value {model!r} contains a control character "
            f"({', '.join(repr(c) for c in bad)}); it is emitted on a `model:` line, "
            "where a newline would splice arbitrary keys into the frontmatter"
        )
    tools_m = re.search(r"^tools:[ \t]*(.+)$", fm, re.M)
    if not tools_m:
        raise SpecViolation(f"{name}: source has no tools: line")
    granted = [t.strip() for t in tools_m.group(1).split(",") if t.strip()]

    has_edit, has_write = "Edit" in granted, "Write" in granted
    if has_edit != has_write:
        raise SpecViolation(
            f"{name}: Edit/Write granted asymmetrically "
            f"(Edit={has_edit}, Write={has_write}); both gate under the single "
            f"OpenCode `edit` permission key — grant both or neither"
        )

    allowed: set[str] = set()
    agent_roles: list[str] = []
    has_bare_agent = False
    for tool in granted:
        agent_m = _AGENT_TOKEN_RE.match(tool)
        if tool in ("Edit", "Write"):
            allowed.add("edit")
        elif agent_m:
            # Dispatch grant. Under V2 the per-role restriction projects as
            # ordered resource globs on the `subagent` action (ADR-0100 point
            # 3): a restricted `Agent(role)` becomes a broad deny followed by
            # one allow per role, and a bare `Agent` becomes a single broad
            # allow. The rules themselves are built after the deny pass below,
            # because a `disallowedTools` deny collapses every case.
            if agent_m.group("role"):
                agent_roles.append(agent_m.group("role"))
            else:
                has_bare_agent = True
        elif tool in TOOL_MAP:
            allowed.add(TOOL_MAP[tool])
        else:
            raise SpecViolation(
                f"{name}: tool {tool!r} has no TOOL_MAP entry; refusing to "
                f"silently drop a grant — extend the table deliberately"
            )
    # ADR-0092 item 3: disallowedTools -> explicit deny entries; deny takes
    # precedence over any allow for the same key.
    denied: set[str] = set()
    # The key is located first and its VALUE read second, so a key written with
    # no inline value is refused rather than missed. The single combined regex
    # this replaces required content on the same line, so `disallowedTools:`
    # followed by a block sequence simply did not match — and a denial the
    # author wrote was dropped in silence. Under the V1 map that produced a
    # visible `allow` beside the other keys; under the V2 array the emitted rule
    # says `allow` with nothing to show a deny was ever meant. Refusing matches
    # what `tools:` already does for the same block spelling a few lines above.
    dt_key = re.search(r"^disallowedTools[ \t]*:[ \t]*(.*)$", fm, re.M)
    if dt_key and not dt_key.group(1).strip():
        raise SpecViolation(
            f"{name}: disallowedTools has no inline value — a block-sequence or "
            f"empty disallowedTools is refused rather than silently ignored, "
            f"because ignoring it would emit `allow` for a capability the "
            f"source revokes; write it inline, e.g. `disallowedTools: Bash, Edit`"
        )
    dt_m = dt_key
    if dt_m:
        raw = dt_m.group(1).strip()
        if raw.startswith("[") and raw.endswith("]"):
            raw = raw[1:-1]
        for raw_token in raw.split(","):
            token = raw_token.strip()
            # Strip a surrounding matched quote pair: a YAML/JSON flow list
            # element (`disallowedTools: ["Read", "Bash"]`) keeps its quotes on
            # split, and a retained quote made the token unmappable and crashed
            # the whole projection with SpecViolation.
            if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
                token = token[1:-1].strip()
            if not token:
                continue
            base = _AGENT_TOKEN_RE.match(token)
            key = "subagent" if base else DENY_TOOL_MAP.get(token)
            if key is None:
                raise SpecViolation(
                    f"{name}: disallowedTools entry {token!r} has no action mapping"
                )
            denied.add(key)
    allowed -= denied

    # `list` is derived from `read`, and the derivation runs AFTER the deny
    # subtraction so a deny on Read also closes the derived `list` grant — the
    # deny is fully honored for the read/list pair. Adding `list` before the
    # subtraction left `list: allow` standing when Read was denied.
    if "read" in allowed:
        allowed.add("list")

    # ADR-0100 point 3: project the dispatch grant onto ordered `subagent`
    # rules. Deny-precedence first — a `disallowedTools` deny of Agent or
    # Agent(role) collapses every other case to the single broad deny. Then a
    # bare `Agent` is a single broad allow; a restricted `Agent(role)` set is
    # the broad deny FOLLOWED BY one allow per sorted role; no grant is the
    # single broad deny.
    #
    # The broad deny comes FIRST and that order is the whole mechanism. V2
    # resolves the array last-match-wins, so a deny emitted after the role
    # allows would cancel them and leave the agent unable to dispatch anyone.
    # Deny-first is what closes off every non-named role while leaving the
    # named ones reachable.
    subagent_rules: list[tuple[str, str]]
    if "subagent" in denied:
        subagent_rules = [(WILDCARD, "deny")]
    elif has_bare_agent:
        subagent_rules = [(WILDCARD, "allow")]
    elif agent_roles:
        subagent_rules = [(WILDCARD, "deny")]
        subagent_rules += [(role, "allow") for role in sorted(set(agent_roles))]
    else:
        subagent_rules = [(WILDCARD, "deny")]

    def _rule(action: str, resource: str, effect: str) -> str:
        # Every resource is quoted, not just the wildcard. `"*"` MUST be quoted
        # (a bare `*` is a YAML alias indicator), and quoting the role names on
        # the same terms keeps one spelling for one concept.
        return f'  - action: {action}\n    resource: "{resource}"\n    effect: {effect}'

    rules: list[str] = []
    for action in ACTION_UNIVERSE:
        if action == "subagent":
            rules += [_rule(action, res, eff) for res, eff in subagent_rules]
        else:
            rules.append(
                _rule(action, WILDCARD, "allow" if action in allowed else "deny")
            )
    permissions_block = "permissions:\n" + "\n".join(rules)

    # ADR-0092 item 3: maxTurns -> steps (nearest OpenCode equivalent). The
    # other new agent fields (effort, skills, memory, isolation) DROP on
    # OpenCode with no faithful target.
    steps_line = ""
    mt_m = re.search(r"^maxTurns:[ \t]*(\d+)[ \t]*$", fm, re.M)
    if mt_m:
        steps_line = f"steps: {int(mt_m.group(1))}\n"

    # Drop the source-only lines; everything else passes through verbatim. The
    # Claude-only agent fields are stripped here so they do not leak into the
    # OpenCode frontmatter (maxTurns is re-emitted as steps above). `name` and
    # `metadata` join them under ADR-0100 point 4 — see the module docstring:
    # either key beside the array voids every rule in it.
    out = re.sub(r"^tools:.*$\n?", "", fm, flags=re.M)
    out = re.sub(r"^model:[ \t]*\S+[ \t]*$\n?", "", out, flags=re.M)
    # EVERY dropped key goes through `_drop_block_key`, not just `metadata`.
    # Each of these can legally be written as a YAML block, and a single-line
    # removal orphans the children at their original indent. Because the
    # `permissions` array is now the last thing emitted, those orphans land
    # INSIDE it: `skills:\n  - propose-adr` leaves `  - propose-adr` as a bare
    # string element of the rule array. Under the V1 map that orphan followed a
    # mapping and was a loud YAML parse error, so the frontmatter-validity gate
    # caught it; under the V2 array it parses cleanly and the gate cannot.
    for drop_key in ("maxTurns", "effort", "skills", "memory", "isolation",
                     "disallowedTools", "name", "metadata"):
        out = _drop_block_key(out, drop_key)
    if re.search(r"^mode:", out, re.M):
        raise SpecViolation(f"{name}: source already carries a mode: line")

    mode = MODE_MAP.get(name, MODE_DEFAULT)
    # A replacement FUNCTION, not a template: see this function's docstring.
    # `model` is catalog-derived and its grammar is checked only CI-side, so a
    # backslash escape in an alias value must not be interpreted here.
    inserted = re.subn(
        r"^(description:.*)$",
        lambda m: f"{m.group(1)}\nmode: {mode}\nmodel: {model}\n{steps_line}{permissions_block}",
        out, count=1, flags=re.M,
    )
    if inserted[1] != 1:
        raise SpecViolation(f"{name}: could not locate description: line")

    # Fail closed on the safety keys rather than shipping a silently
    # unrestricted agent.
    #
    # Stated honestly: this shares its anchor with the drops above, so it adds
    # NO coverage over them today and cannot catch a spelling they miss. It is
    # retained as a last-line defense on the same rationale as
    # `_refuse_leaf_symlinks` below — if a future edit narrows or reorders those
    # drops, this must still refuse to emit a file that loads, states rules and
    # enforces none of them. Do not delete it as redundant, and do not read it
    # as a second, independent check.
    for banned in BANNED_EMITTED_KEYS:
        if re.search(_top_level_key_pattern(banned), inserted[0], re.M):
            raise SpecViolation(
                f"{name}: emitted frontmatter still carries a top-level "
                f"{banned!r} key; beside the permissions array that key voids "
                f"every rule in it, so refusing to write it"
            )
    return inserted[0]


def generate(source_dir: Path = SOURCE_DIR, catalog: Any | None = None) -> dict[str, str]:
    """Return {filename: generated_content} for every source agent.

    Loads the catalog first. `models_catalog.load()` runs the schema, roster
    and reference-graph rules fail-closed before returning, so a roster that
    has drifted from the agent files raises here — before any caller reaches
    `write()`.
    """
    sources = sorted(source_dir.glob("*.md"))
    if not sources:
        raise SpecViolation(f"no source agents under {source_dir}")
    if catalog is None:
        catalog = models_catalog.load(agents_dir=source_dir)
    out: dict[str, str] = {}
    for src in sources:
        fm, body = parse_source(src)
        model = catalog.resolve(src.stem).opencode
        out[src.name] = f"---\n{transform(src.stem, fm, model)}---\n{body}"
    return out


def _managed_on_disk(output_dir: Path, managed: frozenset[str]) -> dict[str, str]:
    """Read the crux-managed files present in output_dir.

    Fail-closed on any managed leaf that is a symlink: reading through it (to
    compare content) or later writing through it would touch the link target,
    so refuse with a clean SpecViolation instead of a silent through-read (live
    link) or a raw FileNotFoundError (dangling link). Mirrors the same
    behaviour in `codex_agents.diff`, so both installers surface one structured
    error rather than leaking an OSError mid-scan.
    """
    if not output_dir.is_dir():
        return {}
    present = sorted(p for p in output_dir.glob("*.md") if p.name in managed)
    symlinked = sorted(p.name for p in present if p.is_symlink())
    if symlinked:
        raise SpecViolation(
            "refusing to read through crux-managed agent symlink(s) (would "
            "read/write the link target): " + ", ".join(symlinked)
        )
    return {p.name: p.read_text(encoding="utf-8") for p in present}


def diff(
    output_dir: Path,
    generated: dict[str, str],
    *,
    managed: frozenset[str] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Return (added, changed, removed) for crux-managed agent files only.

    `removed` lists managed filenames present on disk that this projection no
    longer produces — a role dropped or renamed upstream. Files outside the
    managed set are never reported and never touched, so a target project's own
    `.opencode/agents/*.md` files are left strictly alone.

    `managed` defaults to the catalog roster. It stays a keyword argument so
    the two-positional-argument call the installer makes keeps working.
    """
    if managed is None:
        managed = managed_filenames()
    on_disk = _managed_on_disk(output_dir, managed)
    added = sorted(set(generated) - set(on_disk))
    changed = sorted(
        name for name in set(generated) & set(on_disk)
        if generated[name] != on_disk[name]
    )
    removed = sorted(set(on_disk) - set(generated))
    return added, changed, removed


def _refuse_leaf_symlinks(output_dir: Path, names: list[str]) -> None:
    """Fail-closed if any leaf this run would write/remove is a symlink.

    `Path.write_text` follows a symlink and would clobber the link's target —
    an in-repo OR out-of-repo victim — even after a directory-level containment
    check passes, because that check says nothing about an individual leaf
    being a link. So refuse every such leaf before any write, regardless of
    whether its target is contained and regardless of `--force`. This is leaf
    clobber prevention, not a ban on a contained directory symlink.

    This check is redundant TODAY — `write()` calls `diff()` first, and `diff()`
    already refuses a symlinked managed leaf — but it is retained DELIBERATELY
    as a standalone last-line defense: were that earlier refusal ever narrowed
    or removed by a future edit, this check must still independently refuse a
    symlinked leaf. Do not delete it as "dead"/redundant.

    NOTE: this deliberately refuses symlinks even though the documented global
    OpenCode setup symlinks `~/.config/opencode/agents/*.md` at the regenerated
    tree (README.md and OPENCODE_GUIDE.md; that setup moved to the plural
    directory in the same change that moved this projection's install target,
    and both documents now tell the user to `rm -f` the singular leftovers).
    That flow is a user running `ln -sf` against their own config dir, not this
    installer writing into a repo; the installer's target is
    `<repo>/.opencode/agents/`, where a managed leaf being a link is a clobber
    risk, not a supported configuration. The distinction is the directory, not
    the singular/plural spelling — the guard would refuse a link in either one.
    """
    offenders = sorted(name for name in names if (output_dir / name).is_symlink())
    if offenders:
        raise SpecViolation(
            "refusing to write through crux-managed agent symlink(s) (would "
            "clobber the link target): " + ", ".join(offenders)
        )


def write(
    output_dir: Path,
    generated: dict[str, str],
    *,
    remove_stale: bool = True,
    managed: frozenset[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Write crux-managed files without touching a project's other agents.

    Refuses fail-closed (SpecViolation) before writing if any managed leaf it
    would create, overwrite, or remove is a symlink. Returns (written,
    removed); `removed` is empty when `remove_stale=False`.
    """
    _, _, removed = diff(output_dir, generated, managed=managed)
    stale = removed if remove_stale else []
    # `mkdir(exist_ok=True)` accepts an existing DIRECTORY and raises
    # FileExistsError on anything else at that path, so a regular file — or a
    # symlink that does not resolve to a directory — would end the run in a
    # traceback instead of the structured SpecViolation both callers of write()
    # translate to exit 2: install-opencode-agents prints it as JSON on stdout,
    # and generate-opencode-agents prints it on stderr (that script's documented
    # exit-2 form). The two callers arrange their handlers differently:
    # generate-opencode-agents wraps generate(), diff(), and write() in one
    # try/except, while install-opencode-agents wraps generate() plus diff() in
    # one handler and write() in a second. Either shape routes every
    # SpecViolation from all three calls to an except clause, so none escapes as
    # a traceback.
    # `is_symlink() or exists()` is the lexists test: it sees a dangling
    # link, which `exists()` alone reports as absent. A symlink TO a directory
    # still passes, preserving the contained-directory-symlink allowance.
    if (output_dir.is_symlink() or output_dir.exists()) and not output_dir.is_dir():
        raise SpecViolation(
            f"refusing to write: {output_dir} exists and is not a directory"
        )
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # A parent component that is a file, a read-only filesystem, a revoked
        # permission: surface every mkdir failure as a structured error too.
        raise SpecViolation(f"cannot create {output_dir}: {exc}") from exc
    # Check writes AND stale removals up front — nothing is written until every
    # affected leaf is proven not to be a symlink (fail-closed, no partial run).
    _refuse_leaf_symlinks(output_dir, list(generated) + stale)
    for name, content in generated.items():
        (output_dir / name).write_text(content, encoding="utf-8")
    for name in stale:
        (output_dir / name).unlink()
    return sorted(generated), stale
