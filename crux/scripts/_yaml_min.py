#!/usr/bin/env python3
"""Shared minimal YAML loader + canonical-JSON serializer for the crux
promptbook/run validators (per ADR-0022 §4 and ADR-0023 §3).

Hoisted out of ``validate-catalog.py`` because the third consumer (the
promptbook validator + the run validator) crosses the duplication threshold
ADR-0022 §4 calls out. The loader handles the structures the promptbook/run
documents need: top-level + nested mappings, block (`- `) and inline (`[a, b]`)
sequences, scalars (str/int/float/bool/null), and literal block scalars (`|`)
for the Markdown fields (`goal`, `strategy`, `prompt`, …).

Public surface:

  load_yaml(text)      -> dict | list      — parse a whole YAML document
                                             (best-effort: PyYAML fast path,
                                              minimal fallback otherwise; a
                                              fallback FAILURE raises
                                              YamlCapabilityError).
  load_catalog_yaml(text) -> dict          — the STRICT read path for
                                             `crux/catalog/*.yml`: refuses five
                                             silent-hazard constructs, refuses a
                                             duplicate key, and refuses a root
                                             that is not a non-empty mapping.
  dump_catalog_yaml(data) -> str           — emit inside the pinned subset.
  write_catalog_yaml(path, data, header)   — emit, re-read through BOTH parse
                                             paths, refuse to commit on any
                                             mismatch, then move into place.
  leading_comment_block(text) -> str       — the file's header comment, so a
                                             rewrite preserves it.
  canonical_json(obj)  -> bytes            — deterministic UTF-8 serialization
                                             (keys sorted, compact separators)
                                             used for the plan content hash.
  ensure_real_yaml(argv0) -> None          — PB-0026 entry guard for
                                             correctness-critical consumers:
                                             no-op with PyYAML; uv re-exec
                                             repair; else YamlCapabilityError.
  YamlCapabilityError / HAVE_PYYAML / REMEDIATION — the capability layer's
                                             exception, probe, and shared
                                             remediation tail.

INVARIANT (ADR-0022 §4): the optional ``import yaml`` fast-path MUST stay pinned
to ``yaml.safe_load`` and MUST NEVER call ``yaml.load`` (which can construct
arbitrary Python objects). A future maintainer must not "upgrade" this.

INVARIANT (ADR-0023 §3): ``canonical_json`` MUST omit keys whose value is
absent (i.e. the caller must not pass present-with-null for an optional key);
it serializes exactly what it is given, with sorted keys and no insignificant
whitespace, so the CLI path and the ``import yaml`` fast-path produce identical
bytes — the lock-step discipline §13.2 requires for the plan hash.

Stdlib-only to import (PyYAML is an optional fast path) — but per PB-0026,
correctness-critical consumers (anything producing validation verdicts or
content hashes) MUST call ``ensure_real_yaml()`` first and never rely on the
fallback parser: parity is guaranteed only within the minimal subset.

CATALOG READ PATH (ADR-0073 clause 4). ``load_catalog_yaml`` is a SECOND,
STRICTER entry point beside ``load_yaml``, not a change to it. The catalog
files (``models.yml``, ``bundles.yml``) are hand-authored sources of truth
whose pinned YAML subset forbids anchors, aliases, merge keys, explicit tags,
and multi-document files; ``safe_load`` accepts all five happily, so the
subset is a convention until a loader refuses them. Why a separate entry point
rather than new behavior inside ``load_yaml``: ``load_yaml`` parses every
promptbook and run snapshot, whose literal block scalars carry arbitrary
Markdown, and a line-oriented refusal scan cannot tell a ``*`` opening a
scalar line from an alias. Narrowing the strictness to the catalog keeps the
promptbook parse — and the ADR-0071 parity contract over ``models.yml`` —
byte-identical to what it was, while still landing the refusals in the shared
component both catalog files read through.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

# ─────────────────────────────── public API ──────────────────────────────


# Computed once: whether the real YAML parser is importable in this process.
try:
    import yaml as _pyyaml  # type: ignore  # noqa: F401
    HAVE_PYYAML = True
except ImportError:
    HAVE_PYYAML = False


class YamlCapabilityError(RuntimeError):
    """The current Python environment cannot parse this document faithfully.

    Raised (a) by ``ensure_real_yaml`` when PyYAML is absent and the uv
    re-exec repair is unavailable/declined, and (b) by ``load_yaml`` when the
    minimal fallback parser fails — in that case the document is EITHER
    invalid OR merely exceeds the fallback's subset, and only a real parser
    can distinguish the two. Callers MUST surface this as an ENVIRONMENT
    problem (the crux crash lane: non-zero exit + stderr, no findings JSON),
    never as a document-validation verdict.
    """


# Shared remediation tail for every capability raise site (module-public so
# the entry-point scripts can compose their own messages around it).
REMEDIATION = (
    "this environment lacks PyYAML, and the minimal fallback parser cannot "
    "guarantee a faithful parse — run under `uv run python3 ...` or install "
    "pyyaml (`pip install pyyaml`)"
)


def ensure_real_yaml(argv0: str | None = None) -> None:
    """Guarantee PyYAML for correctness-critical parsing, or die honestly.

    Correctness-critical consumers (anything producing validation verdicts or
    content hashes: validate-promptbook, visualize-run-progress) call this at
    entry. The fallback parser is NOT an
    acceptable substitute there: it has empirically diverged from PyYAML on
    valid documents (silently, in ways that poison ``book_content_hash``), so
    best-effort parsing is banned wherever verdicts/hashes are produced.

    Behavior: no-op when PyYAML imports. Otherwise, if ``uv`` is on PATH and
    neither CRUX_UV_REEXEC (loop guard) nor CRUX_NO_UV_REEXEC (operator
    opt-out for environments that forbid implicit tool execution) is set,
    re-exec the current script under ``uv run --no-project --with pyyaml``.
    ``--no-project`` makes the repair cwd-independent — it needs no
    pyproject.toml anywhere, so downstream repos invoking plugin scripts from
    arbitrary directories resolve identically. The load-bearing audience IS
    downstream callers: in this repo pyyaml is a base dependency, so the
    re-exec only fires in broken/partial environments.

    Trust + fidelity notes: uv is resolved ONCE via shutil.which and exec'd
    by absolute path (os.execv — no second PATH search); a one-line stderr
    notice announces the re-exec (explicit repair, not a silent mask);
    interpreter flags (-X/-S/...) are deliberately NOT preserved — this is a
    script-entry-point repair, not full argv fidelity. The ``--with
    pyyaml>=6.0`` resolution intentionally trusts the user's configured uv
    index and may touch the network on first use (cached after) — hermetic
    environments should set CRUX_NO_UV_REEXEC=1 and provision PyYAML
    themselves. Both CRUX_* variables count as set when non-empty (any
    value, including "0"). With neither PyYAML nor uv available the floor is
    this loud error; nothing is auto-repaired.

    ``argv0``: the calling script's ``__file__`` — passed explicitly because
    ``sys.argv[0]`` is wrong for embedded/module invocations; it determines
    WHICH script the uv child re-runs.
    """
    if HAVE_PYYAML:
        return
    import os
    import shutil

    script = Path(argv0 or sys.argv[0]).resolve()
    if os.environ.get("CRUX_UV_REEXEC"):
        # Loop guard fired: we already re-exec'd under uv and PyYAML is STILL
        # missing — the uv environment itself is broken.
        raise YamlCapabilityError(
            f"already re-executed under uv but PyYAML is still missing (broken uv environment?) — {REMEDIATION}"
        )
    if os.environ.get("CRUX_NO_UV_REEXEC"):
        raise YamlCapabilityError(f"uv re-exec disabled by CRUX_NO_UV_REEXEC — {REMEDIATION}")
    uv = shutil.which("uv")
    if not uv:
        raise YamlCapabilityError(f"uv not found on PATH — install uv or pip install pyyaml; {REMEDIATION}")
    print(
        f"crux: PyYAML missing — re-executing under `uv run --no-project --with pyyaml>=6.0` ({uv}); "
        "set CRUX_NO_UV_REEXEC=1 to disable",
        file=sys.stderr,
    )
    os.environ["CRUX_UV_REEXEC"] = "1"
    try:
        # The constraint mirrors pyproject's floor; --with resolves from the
        # user's configured uv index at first use (network/cache) — accepted
        # trust model for a dev tool, see the docstring.
        os.execv(uv, [uv, "run", "--no-project", "--with", "pyyaml>=6.0", "python3", str(script), *sys.argv[1:]])
    except OSError as exc:
        raise YamlCapabilityError(f"uv at {uv} failed to exec ({exc}) — {REMEDIATION}") from exc


def load_yaml(text: str) -> Any:
    """Parse a YAML document. Uses PyYAML's ``safe_load`` when available;
    otherwise a minimal hand-rolled parser sufficient for the promptbook/run
    document shapes (mappings, sequences, scalars, literal block scalars).

    Returns a dict or list (or a scalar for a bare-scalar document). Raises
    on a structurally malformed document so callers can surface a parse error.
    Without PyYAML, a fallback-parse failure raises ``YamlCapabilityError``
    instead (invalid-vs-exceeds-subset is indistinguishable there); callers
    producing verdicts/hashes must call ``ensure_real_yaml()`` first and never
    reach the fallback.
    """
    try:
        import yaml  # type: ignore

        # INVARIANT: safe_load ONLY. Never yaml.load — it can build arbitrary
        # Python objects from a crafted document. See module docstring.
        return _normalize(yaml.safe_load(text))
    except ImportError:
        # PARITY CONTRACT (ADR-0023 §3 / docs/AGENTS.md §13.2): the fallback path
        # must return the SAME Python value PyYAML+_normalize does, so the
        # book_content_hash lock-step holds whether or not PyYAML is installed.
        # Both paths funnel through _normalize for the date/datetime coercion;
        # _parse_scalar separately mirrors safe_load's YAML-1.1 scalar resolution
        # for the cases reachable in a promptbook/run document (see its docstring).
        # KNOWN LIMIT (PB-0026): parity holds only within the minimal subset —
        # correctness-critical consumers must call ensure_real_yaml() instead of
        # relying on this path (empirical divergences exist outside the subset).
        try:
            return _normalize(_parse_minimal_yaml(text))
        except Exception as exc:
            # Without a real parser we cannot tell "invalid document" from
            # "exceeds the minimal subset" — surface as a capability problem.
            raise YamlCapabilityError(
                f"minimal-parser failure without PyYAML ({exc}); the document is "
                f"either invalid or exceeds the fallback subset — {REMEDIATION}"
            ) from exc


class CatalogYamlError(ValueError):
    """A ``crux/catalog/*.yml`` document violates the strict catalog contract.

    Distinct from ``YamlCapabilityError``: this is a DOCUMENT verdict (the file
    is wrong and a human must fix it), not an environment problem. Callers
    surface it as a validation finding, not as the crash lane.
    """


# The constructs refused on the catalog read path. The first five are the ones
# ADR-0073 clause 4 pins; each is a *silent* hazard, in that it parses fine and
# changes the value or the cost.
#
# The sixth — a flow collection — is refused because it is the CARRIER for the
# other five. An earlier version of this scan inspected only the first
# character of a value token and left flow collections to "the per-file parity
# test", on the stated theory that they "fail loudly on the fallback path".
# They do not. `a: {b: &x 1, c: *x}` was accepted by both paths, and the two
# then disagreed silently — PyYAML expanded the alias, the minimal parser kept
# the literal string `{b: &x 1, c: *x}`. Under PyYAML that made the refusal
# scan bypassable by a nested alias bomb (a 412-byte document reached 10.5 GB
# RSS). Flow collections were never inside the pinned subset ADR-0071 fixes, so
# refusing them at the start of any key or value token closes the bypass and
# restores two-path agreement in one rule.
_CATALOG_REFUSALS = (
    "an anchor (`&name`)",
    "an alias (`*name`)",
    "a merge key (`<<:`)",
    "an explicit tag (`!tag`) or a `%TAG`/`%YAML` directive",
    "a second document (`---` / `...`)",
    "a flow collection (`{...}` / `[...]`)",
)

# A catalog file is hand-authored and small — the two shipped files are ~4 KiB
# each. The ceiling matches the one `bionic_config.py` puts on the repo's other
# hand-authored YAML, and it bounds the work any refused document can cost the
# scan before the parser ever sees it.
CATALOG_MAX_BYTES = 65536


def load_catalog_yaml(text: str) -> dict:
    """Parse a ``crux/catalog/*.yml`` document under the strict contract.

    Four refusals beyond ``load_yaml``, each closing a way for a catalog file
    to be misread SILENTLY rather than loudly (ADR-0073 clause 2/4):

      0. **A 64 KiB ceiling.** Checked first, before the scan and before any
         parse, so no refused document can cost more than that to reject.
      1. **Six forbidden constructs.** ``&anchor``, ``*alias``, ``<<:``, an
         explicit ``!tag`` or ``%TAG``/``%YAML`` directive, a second document,
         and a flow collection are refused before any parse. ``safe_load``
         expands anchors and aliases perfectly well — "safe" there means no
         arbitrary object construction, not no expansion — so the pinned subset
         is not itself a defense. Flow collections are refused because they are
         the carrier: a nested ``{b: &x 1, c: *x}`` slips past a scan that
         inspects only a token's first character. The scan is textual and
         therefore applies identically on both parse paths, which is what makes
         the two agree on REJECTION as well as on acceptance.
      2. **No duplicate key, at any level.** ``safe_load`` accepts a repeated
         mapping key and keeps the last value; under a file keyed by bundle id
         that silently discards a whole authored bundle.
      3. **The root is a non-empty mapping.** A top-level sequence, a bare
         scalar, an empty file, and a comment-only file are all errors. This is
         the direct repair of the coercion (``return data if isinstance(data,
         dict) else {}``) that would otherwise turn a top-level sequence into a
         green run over zero bundles. Rejecting the EMPTY mapping too is what
         keeps the two paths agreeing: an empty document resolves to ``None``
         under PyYAML and to ``{}`` under the minimal parser, so accepting it
         would be the one input on which they disagree.

    Raises ``CatalogYamlError`` for a document verdict and
    ``YamlCapabilityError`` when the minimal fallback cannot parse at all
    (invalid-versus-exceeds-the-subset is indistinguishable without PyYAML).
    """
    size = len(text.encode("utf-8"))
    if size > CATALOG_MAX_BYTES:
        raise CatalogYamlError(
            f"catalog document is {size} bytes, over the {CATALOG_MAX_BYTES}-byte ceiling — "
            "not a plausible hand-authored catalog file"
        )
    _refuse_catalog_constructs(text)
    try:
        import yaml  # type: ignore
    except ImportError:
        try:
            data = _normalize(_parse_minimal_yaml(text, strict=True))
        except CatalogYamlError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise YamlCapabilityError(
                f"minimal-parser failure without PyYAML ({exc}); the catalog document is "
                f"either invalid or exceeds the fallback subset — {REMEDIATION}"
            ) from exc
    else:
        _refuse_catalog_duplicate_keys(text, yaml)
        try:
            data = _normalize(yaml.safe_load(text))
        except yaml.YAMLError as exc:
            raise CatalogYamlError(f"invalid YAML: {str(exc).splitlines()[0]!r}") from exc
    if not isinstance(data, dict):
        raise CatalogYamlError(
            f"catalog document root must be a mapping, got {type(data).__name__} — "
            "a top-level sequence or scalar is out of contract"
        )
    if not data:
        raise CatalogYamlError("catalog document is empty; at least one top-level key is required")
    return data


def _refuse_catalog_constructs(text: str) -> None:
    """Textual, quote-aware scan for the six refused constructs.

    Line-oriented and therefore only sound where block scalars cannot appear —
    which is exactly the catalog subset (ADR-0071 pins it to block mappings,
    block sequences of plain scalars, and plain or double-quoted scalars). It
    is NOT reachable from ``load_yaml``.

    Every refusal is POSITIONAL: it fires on the first character of a key or
    value token and nowhere else. That is what makes false positives
    impossible on a conforming document. ``&``, ``*``, ``!``, ``{`` and ``[``
    are YAML indicator characters that a plain scalar may not begin with, so a
    token starting with one is always the construct and never text. A token
    that merely CONTAINS one (``a*b``, ``x!y``, ``x{y}z``) is untouched, and a
    quoted token starting with one (``"*all*"``, ``"{literal}"``) is untouched
    because the token then begins with the quote.

    Refusing the flow-collection openers is what makes the positional rule
    sound rather than merely cheap. Without it the scan inspects the outside of
    a container and never its contents, so every other refusal on this list is
    one brace away from being bypassed.
    """
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw)
        stripped = line.strip()
        if not stripped:
            continue

        def _refuse(what: str) -> None:
            raise CatalogYamlError(
                f"line {lineno}: {what} is refused on the catalog read path "
                f"(refused constructs: {', '.join(_CATALOG_REFUSALS)}): {raw.strip()!r}"
            )

        if stripped.startswith("%"):
            _refuse("a directive")
        if stripped in ("---", "...") or stripped.startswith("--- ") or stripped.startswith("... "):
            # Even a leading `---` is refused: one document per file means the
            # marker buys nothing, and allowing it would make "is this the
            # second document?" a stateful question the minimal parser cannot
            # answer.
            _refuse("a document marker")

        # Peel leading block-sequence dashes so `- key: value` and `- item`
        # are inspected as the mapping / scalar they introduce.
        body = stripped
        while body == "-" or body.startswith("- "):
            body = "" if body == "-" else body[2:].lstrip()
        if not body:
            continue

        key, value = _split_catalog_key_value(body)
        if key is not None:
            key_token = key.strip()
            if key_token == "<<":
                _refuse("a merge key")
            if _starts_with_flow(key_token):
                _refuse("a flow collection as a key")
            if _starts_with_indicator(key_token):
                _refuse("an anchored, aliased or tagged key")
            token = value.strip()
        else:
            token = body
        # Flow first: a flow collection is the CARRIER for every other refusal
        # on the list, and the scan cannot see inside one.
        if _starts_with_flow(token):
            _refuse("a flow collection as a value")
        if _starts_with_indicator(token):
            _refuse("an anchored, aliased or tagged value")


def _starts_with_indicator(token: str) -> bool:
    """True when a token begins with `&`, `*`, or `!` — the three YAML
    indicator characters a plain scalar may never begin with."""
    return bool(token) and token[0] in ("&", "*", "!")


_EMPTY_FLOW = ("{}", "[]")


def _starts_with_flow(token: str) -> bool:
    """True when a token opens a NON-EMPTY flow collection.

    Positional, like its sibling: a plain scalar may not begin with `{` or `[`,
    so a token starting with one is always a flow collection. Refused rather
    than parsed because the line-oriented scan cannot inspect a container's
    contents, and a flow collection can therefore smuggle in any of the five
    constructs above it.

    The two EMPTY forms are the carve-out, and it is a carve-out on the hazard
    rather than on convenience: what makes a flow collection dangerous is its
    contents, and an empty one has none — no anchor, no alias, no tag, no merge
    key can be written inside `{}` or `[]`. The carve-out is also load-bearing.
    Block YAML has no other spelling for an empty collection, `bundles.yml`
    permits a bundle with an empty `skills` list, and the emitter renders that
    as `skills: []` — so refusing the empty forms would give the writer a legal
    value it cannot round-trip through its own reader. Both parse paths already
    agree on both forms, measured, so nothing about two-path parity turns on it.
    """
    if not token or token[0] not in ("{", "["):
        return False
    return "".join(token.split()) not in _EMPTY_FLOW


def _split_catalog_key_value(body: str) -> tuple[str | None, str]:
    """Split `key: value` outside quotes. Returns (None, "") when `body` has no
    mapping separator (i.e. it is a bare sequence item or a continuation)."""
    in_str: str | None = None
    for i, ch in enumerate(body):
        if in_str:
            if ch == in_str:
                in_str = None
            continue
        if ch in ('"', "'"):
            in_str = ch
            continue
        if ch == ":":
            rest = body[i + 1:]
            if rest == "" or rest[0] in (" ", "\t"):
                return body[:i], rest
    return None, ""


def _refuse_catalog_duplicate_keys(text: str, yaml: Any) -> None:
    """Reject a repeated mapping key, and a second document, via the composer.

    ``yaml.compose_all`` builds the node graph WITHOUT constructing Python
    objects, so it honors the module's safe-load-only invariant while exposing
    the duplicate keys ``safe_load`` would silently collapse. The minimal
    fallback gets the same guarantee from ``_parse_minimal_yaml(strict=True)``.
    """
    try:
        documents = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
    except yaml.YAMLError as exc:
        raise CatalogYamlError(f"invalid YAML: {str(exc).splitlines()[0]!r}") from exc
    if len(documents) > 1:
        raise CatalogYamlError("catalog document contains more than one YAML document")
    for node in documents:
        _walk_for_duplicate_keys(node, yaml)


def _walk_for_duplicate_keys(node: Any, yaml: Any) -> None:
    if isinstance(node, yaml.MappingNode):
        seen: set[str] = set()
        for key_node, value_node in node.value:
            key = getattr(key_node, "value", None)
            if isinstance(key, str):
                if key in seen:
                    raise CatalogYamlError(f"duplicate mapping key {key!r}")
                seen.add(key)
            _walk_for_duplicate_keys(value_node, yaml)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            _walk_for_duplicate_keys(item, yaml)


# A mapping key safe to emit as a plain scalar: no leading indicator, no
# colon, no `#`, no leading/trailing space. Deliberately narrower than YAML's
# real plain-scalar grammar — a key outside it stays quoted and the write is
# then refused by the dual-path check, which is the fail-closed direction.
_PLAIN_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def dump_catalog_yaml(data: dict) -> str:
    """Serialize a catalog mapping inside the pinned subset.

    Requirement (ADR-0073 clause 2): the emitted file stays inside the subset —
    no single-quoted scalars, no explicit tags, no flow collections, no block
    scalars, no anchors/aliases/merge keys, and **no line-wrapped scalars**
    (a writer-side tightening: the minimal parser's grammar is line-oriented,
    so a wrapped scalar PyYAML reads correctly would diverge on the fallback
    path and break parity).

    How those properties are obtained is not the contract —
    ``write_catalog_yaml``'s dual-path re-read is. What matters here is the one
    measured trap: styling EVERY scalar as double-quoted makes the emitter tag
    non-strings to preserve their type (``!!bool "false"``), which is an
    explicit tag and therefore a file this module's own reader refuses. So the
    style is applied to strings only, and other scalars stay plain.
    """
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - writer callers run under uv
        raise YamlCapabilityError(f"writing a catalog file needs a real YAML emitter — {REMEDIATION}") from exc

    class _CatalogDumper(yaml.SafeDumper):
        # Indent block sequences under their key. PyYAML's default emits
        # `key:` followed by `- item` at the SAME column, which the minimal
        # parser reads as a sequence item where a mapping key was expected —
        # so the default would produce a file the fallback path cannot read.
        # Found by this module's own dual-path re-read, not by inspection.
        def increase_indent(self, flow: bool = False, indentless: bool = False) -> Any:
            return super().increase_indent(flow, False)

        # Emit mapping KEYS plain and mapping VALUES double-quoted. One
        # representer cannot distinguish the two positions, and the minimal
        # parser does not unquote a key — it partitions on the first colon and
        # keeps the quotes — so a quoted key round-trips to a different value
        # on the fallback path. Emitting plain keys is the fix that leaves the
        # shared parser (and therefore every promptbook it also parses)
        # untouched. A key that would not survive plain emission keeps its
        # quotes and is caught by the dual-path re-read rather than shipped.
        def represent_mapping(self, tag: str, mapping: Any, flow_style: Any = None) -> Any:
            node = super().represent_mapping(tag, mapping, flow_style)
            for key_node, _value_node in node.value:
                if (
                    isinstance(key_node, yaml.ScalarNode)
                    and key_node.tag == "tag:yaml.org,2002:str"
                    and _PLAIN_KEY_RE.match(key_node.value)
                ):
                    key_node.style = None
            return node

    def _str_representer(dumper: Any, value: str) -> Any:
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style='"')

    _CatalogDumper.add_representer(str, _str_representer)
    # `safe_dump` fixes its Dumper internally and raises TypeError when handed
    # one, so the general entry point is required for a custom representer.
    return yaml.dump(
        data,
        Dumper=_CatalogDumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=10**9,  # never wrap: a wrapped scalar breaks fallback parity
    )


def leading_comment_block(text: str) -> str:
    """Return the file's leading `#` comment block, blank lines included.

    A catalog file is hand-authored, so its header comment is content. The one
    machine writer rewrites the whole document, which would otherwise delete
    that header on the first `--execute`; reading it back and passing it to
    `write_catalog_yaml` preserves it. Stops at the first non-comment,
    non-blank line.
    """
    kept: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#"):
            kept.append(line)
            continue
        break
    while kept and kept[-1].strip() == "":
        kept.pop()
    return "".join(kept)


def write_catalog_yaml(path: Path, data: dict, header: str = "") -> None:
    """Write a catalog file, refusing to commit output it cannot read back.

    The load-bearing requirement (ADR-0073 clause 2, requirement 2): re-read the
    emitted text through BOTH parse paths and refuse unless both round-trip to
    the intended value, types included — ``default_provision`` is a boolean and
    must come back a boolean. Only then move the temporary file into place.

    This is a postcondition rather than an emitter configuration on purpose. A
    verified write is closed over emitter mistakes nobody has thought of yet; a
    recipe is closed only over the cases whoever wrote the recipe considered.
    Dual-path agreement stands in for subset membership on one stated
    condition — the minimal parser accepts only the pinned subset — so if that
    parser ever widens, this guarantee weakens with it.

    **[SECURITY:S5] Neither the target nor the temporary file may be a
    symlink.** ``Path.write_bytes`` FOLLOWS a link, and the temporary path is
    predictable (``<path>.tmp``), so an attacker who can create a file beside
    the catalog gets the catalog's contents written into a target of their
    choosing — and ``os.replace`` then moves the tmp PATH, leaving the victim
    clobbered and the catalog path still a link. The refusal is checked
    explicitly for a readable error, and the create is additionally
    ``O_NOFOLLOW|O_EXCL`` so the check is not a TOCTOU window. The same guard
    is owed by every sibling that writes a repo artifact through a predictable
    tmp path: ``validate-catalog.py`` and ``extract-code-docs.py`` each carry a
    byte-identical ``_atomic_write_text``, and both were fixed alongside this.
    """
    import os

    body = dump_catalog_yaml(data)
    text = (header.rstrip("\n") + "\n" + body) if header.strip() else body
    expected = _normalize(data)

    strict_reread = load_catalog_yaml(text)
    if strict_reread != expected:
        raise CatalogYamlError(
            f"refusing to write {path}: the emitted YAML does not round-trip through the "
            "PyYAML path to the intended value"
        )
    try:
        fallback_reread = _normalize(_parse_minimal_yaml(text, strict=True))
    except Exception as exc:  # noqa: BLE001 — one exception type out of this writer
        raise CatalogYamlError(
            f"refusing to write {path}: the minimal fallback parser cannot read the emitted "
            f"YAML at all ({exc})"
        ) from exc
    if fallback_reread != expected:
        raise CatalogYamlError(
            f"refusing to write {path}: the emitted YAML does not round-trip through the "
            "minimal fallback parser to the intended value"
        )

    tmp = path.with_suffix(path.suffix + ".tmp")
    for label, candidate in (("target", path), ("temporary file", tmp)):
        if candidate.is_symlink():
            raise CatalogYamlError(
                f"refusing to write {path}: the {label} {candidate} is a symlink — "
                "writing through it would put catalog contents in the link's target"
            )
    # O_NOFOLLOW closes the race between the is_symlink check above and this
    # open; O_EXCL refuses to write through a file this writer does not own.
    # A STALE tmp is removed and the create retried once, on the same reasoning
    # `survey_sheet.open_new_tmp` records: O_EXCL alone cannot survive a crash,
    # and a leftover from a killed run wedged every later write to the same
    # target permanently, with the refusal's own remedy — remove it by hand —
    # forbidden by the skill that owns the surface.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        try:
            fd = os.open(tmp, flags, 0o644)
        except FileExistsError:
            # `os.unlink` removes the NAME, so a planted symlink loses its link
            # and never its target; a directory raises OSError and refuses.
            os.unlink(tmp)
            fd = os.open(tmp, flags, 0o644)
    except FileExistsError as exc:
        raise CatalogYamlError(
            f"refusing to write {path}: the temporary file {tmp} reappeared between "
            "its removal and the retry — another process is writing this tree; "
            "re-run once nothing else is"
        ) from exc
    except OSError as exc:  # ELOOP from O_NOFOLLOW, or an unwritable directory
        raise CatalogYamlError(f"refusing to write {path}: cannot create {tmp} ({exc})") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def _normalize(obj: Any) -> Any:
    """Coerce PyYAML's non-JSON-native scalars back to the forms the hand-rolled
    fallback produces, so the two parse paths stay byte-identical (the lock-step
    discipline ADR-0023 §3 requires for the plan hash).

    PyYAML's ``safe_load`` resolves an unquoted ``2026-05-29`` to ``datetime.date``
    and ``2026-05-29T12:00:00Z`` to ``datetime.datetime``. The promptbook/run
    schemas type these fields as ``string`` and the fallback parser leaves them
    as strings, so we render any date/datetime back to its ISO-8601 text form.
    Recurses through mappings and sequences.
    """
    import datetime as _dt

    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize(v) for v in obj]
    if isinstance(obj, _dt.datetime):
        # Preserve a trailing 'Z' for UTC; PyYAML strips it into tzinfo=UTC.
        text = obj.isoformat()
        if obj.tzinfo is not None and text.endswith("+00:00"):
            text = text[:-6] + "Z"
        return text
    if isinstance(obj, _dt.date):
        return obj.isoformat()
    return obj


def canonical_json(obj: Any) -> bytes:
    """Deterministic UTF-8 JSON: keys sorted, compact separators, no trailing
    whitespace. Used to compute the promptbook plan content hash (ADR-0023 §3).

    The caller is responsible for omitting absent optional keys BEFORE calling
    this (do not pass present-with-null for an optional key); this function
    serializes exactly what it is given. ``ensure_ascii=False`` keeps non-ASCII
    Markdown bytes intact and identical across the two parse paths.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


# ──────────────────────── hand-rolled fallback parser ──────────────────────
#
# Indentation-driven recursive descent. Lines are pre-tokenized into
# (indent, content) pairs with comments stripped, then parsed into nested
# mappings / sequences. Literal block scalars (`|`) consume their indented
# body verbatim. This is deliberately narrow — exactly the YAML the
# promptbook/run documents use — and rejects anything ambiguous loudly.


def _parse_minimal_yaml(text: str, *, strict: bool = False) -> Any:
    """Parse the minimal subset. ``strict=True`` additionally rejects a
    repeated mapping key, so the fallback path agrees with the composer-based
    check ``load_catalog_yaml`` runs when PyYAML is present. Default False —
    the promptbook/run path is unchanged."""
    lines = text.splitlines()
    # Pre-scan into a list of physical lines; the block-collection logic needs
    # raw text (for literal scalars) so we keep the originals and compute
    # indent on demand.
    pos = [0]  # boxed cursor so helpers can advance it

    def _peek() -> str | None:
        i = pos[0]
        while i < len(lines):
            stripped = _strip_comment(lines[i])
            if stripped.strip() == "":
                i += 1
                continue
            pos[0] = i
            return lines[i]
        pos[0] = len(lines)
        return None

    def _indent_of(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    def _parse_block(min_indent: int) -> Any:
        """Parse a mapping or sequence whose items are at exactly the indent of
        the first non-blank line at/after the cursor (which must be >= min_indent)."""
        first = _peek()
        if first is None:
            return None
        cur_indent = _indent_of(first)
        if cur_indent < min_indent:
            return None
        content = _strip_comment(first).strip()
        if content.startswith("- ") or content == "-":
            return _parse_sequence(cur_indent)
        return _parse_mapping(cur_indent)

    def _parse_mapping(indent: int) -> dict:
        result: dict[str, Any] = {}
        while True:
            line = _peek()
            if line is None:
                break
            li = _indent_of(line)
            if li < indent:
                break
            if li > indent:
                raise ValueError(f"unexpected indentation in mapping: {line!r}")
            content = _strip_comment(line).strip()
            if content.startswith("- ") or content == "-":
                # A sequence at the same indent as the mapping keys is invalid
                # in this subset (sequences live under a key or at top level).
                raise ValueError(f"sequence item where a mapping key was expected: {line!r}")
            if ":" not in content:
                raise ValueError(f"expected 'key: value' mapping line, got {line!r}")
            key, _, value = content.partition(":")
            key = key.strip()
            value = value.strip()
            pos[0] += 1  # consume the key line
            if strict and key in result:
                raise CatalogYamlError(f"duplicate mapping key {key!r}")

            if value and value[0] in ("|", ">"):
                result[key] = _parse_block_scalar(value, indent)
            elif value == "":
                # Nested block (mapping or sequence) OR an empty value.
                nxt = _peek()
                if nxt is not None and _indent_of(nxt) > indent:
                    result[key] = _parse_block(indent + 1)
                else:
                    result[key] = None
            else:
                result[key] = _parse_scalar(value)
        return result

    def _parse_sequence(indent: int) -> list:
        items: list[Any] = []
        while True:
            line = _peek()
            if line is None:
                break
            li = _indent_of(line)
            if li < indent:
                break
            if li > indent:
                raise ValueError(f"unexpected indentation in sequence: {line!r}")
            content = _strip_comment(line).strip()
            if not (content.startswith("- ") or content == "-"):
                break
            item_body = content[1:].lstrip()  # strip leading '-'
            pos[0] += 1  # consume the '- ' line

            if item_body == "":
                # Item value is on the following indented lines (a nested block).
                nxt = _peek()
                if nxt is not None and _indent_of(nxt) > indent:
                    items.append(_parse_block(indent + 1))
                else:
                    items.append(None)
            elif ":" in item_body and not _looks_like_flow_or_quoted(item_body):
                # Inline mapping start: "- key: value". The dash introduces a
                # mapping whose first key sits on this line; subsequent keys are
                # indented to align under item_body's column.
                # Re-inject the first key line as a virtual mapping at the
                # column where item_body begins.
                first_key, _, first_val = item_body.partition(":")
                m: dict[str, Any] = {}
                fv = first_val.strip()
                key_col = li + (len(content) - len(item_body))
                if fv and fv[0] in ("|", ">"):
                    m[first_key.strip()] = _parse_block_scalar(fv, key_col)
                elif fv == "":
                    nxt = _peek()
                    if nxt is not None and _indent_of(nxt) > key_col:
                        m[first_key.strip()] = _parse_block(key_col + 1)
                    else:
                        m[first_key.strip()] = None
                else:
                    m[first_key.strip()] = _parse_scalar(fv)
                # Remaining keys of this mapping item at the key_col indent.
                rest = _parse_mapping_at(key_col)
                m.update(rest)
                items.append(m)
            else:
                items.append(_parse_scalar(item_body))
        return items

    def _parse_mapping_at(indent: int) -> dict:
        """Parse additional mapping keys at exactly ``indent`` (used to gather
        the trailing keys of an inline '- key: value' sequence item)."""
        result: dict[str, Any] = {}
        while True:
            line = _peek()
            if line is None:
                break
            li = _indent_of(line)
            if li != indent:
                break
            content = _strip_comment(line).strip()
            if content.startswith("- ") or content == "-":
                break
            if ":" not in content:
                break
            key, _, value = content.partition(":")
            key = key.strip()
            value = value.strip()
            pos[0] += 1
            if strict and key in result:
                raise CatalogYamlError(f"duplicate mapping key {key!r}")
            if value and value[0] in ("|", ">"):
                result[key] = _parse_block_scalar(value, indent)
            elif value == "":
                nxt = _peek()
                if nxt is not None and _indent_of(nxt) > indent:
                    result[key] = _parse_block(indent + 1)
                else:
                    result[key] = None
            else:
                result[key] = _parse_scalar(value)
        return result

    def _parse_block_scalar(indicator: str, parent_indent: int) -> str:
        """Consume a literal (`|`) or folded (`>`) block scalar body. Lines more
        indented than ``parent_indent`` belong to the scalar; the common leading
        indentation is stripped."""
        style = indicator[0]
        chomp = indicator[1:2] if indicator[1:2] in ("-", "+") else ""
        body_lines: list[str] = []
        block_indent: int | None = None
        i = pos[0]
        while i < len(lines):
            raw = lines[i]
            if raw.strip() == "":
                body_lines.append("")
                i += 1
                continue
            ind = _indent_of(raw)
            if ind <= parent_indent:
                break
            if block_indent is None:
                block_indent = ind
            if ind < block_indent:
                break
            body_lines.append(raw[block_indent:])
            i += 1
        pos[0] = i
        # Trim trailing blank lines.
        while body_lines and body_lines[-1] == "":
            body_lines.pop()
        if style == "|":
            folded = "\n".join(body_lines)
        else:  # ">" folded: single newlines -> spaces, blank lines kept.
            parts: list[str] = []
            paragraph: list[str] = []
            for ln in body_lines:
                if ln == "":
                    if paragraph:
                        parts.append(" ".join(paragraph))
                        paragraph = []
                    parts.append("")
                else:
                    paragraph.append(ln)
            if paragraph:
                parts.append(" ".join(paragraph))
            folded = "\n".join(parts)
        if chomp == "-":
            return folded
        # clip (default) / keep both append one trailing newline for our needs.
        return folded + "\n"

    # Drive the top-level parse.
    if _peek() is None:
        return {}
    top = _strip_comment(lines[pos[0]]).strip()
    if top.startswith("- ") or top == "-":
        return _parse_sequence(_indent_of(lines[pos[0]]))
    return _parse_mapping(_indent_of(lines[pos[0]]))


def _looks_like_flow_or_quoted(s: str) -> bool:
    """True if ``s`` is a flow collection or a quoted scalar — i.e. the ':' in
    it is NOT a mapping separator."""
    s = s.strip()
    if not s:
        return False
    if s[0] in ("[", "{", '"', "'"):
        return True
    return False


def _strip_comment(line: str) -> str:
    """Strip a ``#`` comment from a line, respecting quoted strings."""
    out: list[str] = []
    in_str: str | None = None
    for ch in line:
        if in_str:
            out.append(ch)
            if ch == in_str:
                in_str = None
            continue
        if ch in ('"', "'"):
            in_str = ch
            out.append(ch)
            continue
        if ch == "#":
            # A '#' that starts a comment must be preceded by whitespace or be
            # at line start; otherwise it's part of an unquoted scalar.
            if not out or out[-1] in (" ", "\t"):
                break
            out.append(ch)
            continue
        out.append(ch)
    return "".join(out)


def _parse_scalar(value: str) -> Any:
    # PARITY CONTRACT (ADR-0023 §3 / docs/AGENTS.md §13.2): this hand-rolled
    # coercion must agree with ``yaml.safe_load``'s YAML-1.1 resolution for every
    # value reachable in a promptbook/run document, because both parse paths feed
    # the same ``book_content_hash``. We mirror the cases that matter:
    #   - YAML-1.1 booleans yes|no|true|false|on|off (case-insensitive) -> bool
    #   - plain dates / datetimes (incl. the space-separated YYYY-MM-DD HH:MM:SS
    #     form) -> date/datetime OBJECTS, which the shared ``_normalize`` then
    #     coerces to the identical ISO/'T' string PyYAML+_normalize produces.
    # Exotic YAML-1.1 forms (hex 0x.., octal, sexagesimal) are intentionally NOT
    # resolved — they don't appear in our documents and adding them would risk
    # diverging from safe_load rather than converging.
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(p.strip()) for p in _split_flow(inner)]
    if value.startswith("{") and value.endswith("}"):
        inner = value[1:-1].strip()
        result: dict[str, Any] = {}
        if not inner:
            return result
        for pair in _split_flow(inner):
            k, _, v = pair.partition(":")
            result[k.strip()] = _parse_scalar(v.strip())
        return result
    low = value.lower()
    if low in ("null", "~", ""):
        return None
    # YAML-1.1 boolean spellings (case-insensitive), matching safe_load.
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        inner = value[1:-1]
        if value[0] == '"':
            return _unescape_double_quoted(inner)
        return inner.replace("''", "'")
    # Plain (unquoted) date / datetime: return the date/datetime OBJECT so the
    # shared _normalize step renders it to the SAME ISO/'T' string PyYAML's
    # safe_load + _normalize produce (parity contract above). Quoted forms are
    # handled before this point and stay literal strings, matching safe_load.
    ts = _try_parse_timestamp(value)
    if ts is not None:
        return ts
    try:
        if "." not in value and "e" not in low and "E" not in value:
            return int(value)
    except ValueError:
        pass
    # YAML-1.1 floats require a decimal point in the mantissa, so safe_load keeps
    # a bare-exponent token like ``1e3`` as a STRING. Gate the float coercion on a
    # '.' to match (parity contract above); without this the two paths diverge on
    # ``1e3`` (str vs 1000.0). Exotic numeric forms (hex/octal/underscored/
    # sexagesimal) are intentionally left as strings here — they don't appear in
    # our documents and faithfully mirroring PyYAML's resolver for them is more
    # divergence risk than it's worth.
    if "." in value:
        try:
            return float(value)
        except ValueError:
            return value
    return value


# YAML-1.1 timestamp forms safe_load resolves that can appear in our documents:
# a bare date (-> datetime.date) or a date+time separated by 'T'/'t' or a space
# (-> datetime.datetime), with optional fractional seconds and 'Z'/+HH:MM tz.
# We deliberately keep this to the ISO-ish shapes; the shared _normalize coerces
# the returned object to the canonical 'T' string identically to the fast path.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[Tt ]+" r"(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?" r"(?:\s*(Z|[+-]\d{1,2}(?::\d{2})?))?$"
)


def _try_parse_timestamp(value: str):
    """Return a ``datetime.date`` / ``datetime.datetime`` for the YAML-1.1
    timestamp forms ``safe_load`` resolves, else ``None``. Kept narrow on
    purpose — only the ISO-ish date/datetime shapes our documents use."""
    import datetime as _dt

    if _DATE_RE.match(value):
        try:
            return _dt.date.fromisoformat(value)
        except ValueError:
            return None
    m = _DATETIME_RE.match(value)
    if not m:
        return None
    date_part, hh, mm, ss, frac, tz = m.groups()
    try:
        year, month, day = (int(p) for p in date_part.split("-"))
        micro = int((frac or "").ljust(6, "0")[:6]) if frac else 0
        tzinfo = None
        if tz == "Z":
            tzinfo = _dt.timezone.utc
        elif tz:
            sign = 1 if tz[0] == "+" else -1
            body = tz[1:]
            if ":" in body:
                oh, om = body.split(":")
            else:
                oh, om = body, "0"
            tzinfo = _dt.timezone(sign * _dt.timedelta(hours=int(oh), minutes=int(om)))
        return _dt.datetime(year, month, day, int(hh), int(mm), int(ss), micro, tzinfo)
    except ValueError:
        return None


def _split_flow(inner: str) -> list[str]:
    """Split a flow-collection body on top-level commas (respecting quotes and
    nested [] / {})."""
    parts: list[str] = []
    depth = 0
    in_str: str | None = None
    cur: list[str] = []
    for ch in inner:
        if in_str:
            cur.append(ch)
            if ch == in_str:
                in_str = None
            continue
        if ch in ('"', "'"):
            in_str = ch
            cur.append(ch)
            continue
        if ch in ("[", "{"):
            depth += 1
            cur.append(ch)
            continue
        if ch in ("]", "}"):
            depth -= 1
            cur.append(ch)
            continue
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return parts


def _unescape_double_quoted(s: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt == '"':
                out.append('"')
            elif nxt == "\\":
                out.append("\\")
            elif nxt == "n":
                out.append("\n")
            elif nxt == "t":
                out.append("\t")
            elif nxt == "/":
                out.append("/")
            else:
                out.append(c)
                out.append(nxt)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)
