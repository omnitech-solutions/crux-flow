"""Backward-compatible facade over the split arch engine (ADR-0096 clause 12).

Clause 12 split the 5,161-line deriver into `core.py` — the probe registry,
input resolution and decoding, canonical serialization, hashing, verdict
computation and the stub renderer — plus one module per pack under `packs/`.
This module is what is left: a re-export surface, and nothing else.

**It exists because 11 test modules bind 75 `D.<symbol>` names against it, most
of them private, and clause 12's gate is byte-identity.** A refactor that must
not move a single corpus golden must not move a single test either, so every
name below resolves exactly where it always did. New code should import from
`crux.arch.core`, or from the owning pack module directly; this facade is a
compatibility surface, not a design.

`test_arch_split_seam.py` compares this module's `dir()` against a snapshot
taken immediately before the split and fails on any name that went missing, so
the facade cannot silently shrink.

Note this module is NO LONGER the one `runtime/capture.py::_load_cell`
file-loads. That loader needs a module that is stdlib-self-contained at module
scope, and the relative imports below disqualify this one; it points at
`core.py` instead.

Two things changed shape rather than moving, and are shimmed at the foot with
the reason: `_pack_probes` and the five per-pack marker predicates.
"""

from __future__ import annotations

# The same stdlib imports the pre-split module carried. They are re-exported
# rather than dropped because `dir(derive)` is a frozen surface here: the
# superset gate compares against a snapshot taken before the split, and a name
# that was reachable then must stay reachable now, incidental or not.
import ast
import functools
import hashlib
import importlib
import inspect
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable, NamedTuple      # noqa: F401

from .core import (   # noqa: F401
    pack_probes,
    ANNOTATIONS,
    CONCERNS,
    DECISION_INDEX_INPUT,
    INPUT_CLASS_KINDS,
    InputClass,
    ParserUnavailable,
    REQUIRED_BY_FLAG,
    REQUIRED_BY_MANIFEST,
    resolve_declared_parsers,
    strict_failures,
    StubReason,
    UNSUPPORTED_STACK_SENTENCE,
    Verdict,
    concern_verdict,
    count_concern_entities,
    coverage_table,
    declared_input_globs,
    input_classes,
    remediation,
    render_stub_line,
    reported_coverage,
    NO_COMMAND,
    MANIFEST_REL,
    MANIFEST_UNCOMPARED_KEY,
    NO_EXTRACTOR,
    DETECTION_MAX_DEPTH,
    PACK_DETECTORS,
    PACK_NAMES,
    PACK_REGISTRY,
    PackEntry,
    Probe,
    REGISTERED_PACKS,
    SPINE_FILES,
    STUB_PACK_NAME,
    StackAmbiguity,
    StackResolution,
    ambiguous_stack_verdict,
    detect_candidates,
    pack_ambiguity,
    resolve_stack,
    StaleProjectionInput,
    TOOL_PINS,
    _ADR_FILENAME_RE,
    _ADR_ID_RE,
    _ADR_INDEX_COLUMNS,
    _ARCHIVED_HEADING_RE,
    _CONCERN_TITLES,
    _EXCEPT_RE,
    _FM_RE,
    _HTTP_METHODS,
    _MAX_FILE_BYTES,
    _MAX_GRAPH_EDGES,
    _MAX_SCAN_BYTES,
    _MAX_SCAN_FILES,
    _ONLY_RE,
    _OPENAPI_CANDIDATES,
    _POETRY_OPTIONAL_RE,
    _SKIP_DIRS,
    _SYM_RE,
    _VENDORED_SCRIPTS,
    _adr_index_projection,
    _adr_index_shape,
    _always,
    _array_closes_outside_quotes,
    _body,
    _build,
    _canon,
    _cell,
    _configured_override,
    _contained,
    _disqualified,
    _extract_module_graph,
    _frontmatter,
    _index_is_regenerator_shaped,
    _is_string_map,
    _iter_py_files,
    _manifest_drifted,
    _mermaid,
    _node_ids,
    _norm_path,
    _path_prefix,
    _pep508_names_in,
    _pin_config_path,
    _prose_cell,
    _pyproject_direct_dep_names,
    _read,
    _reject_override_path,
    _rel,
    _render_openapi,
    _requirements_direct_dep_names,
    _run_override,
    _safe_read_bytes,
    _sha256_hex,
    _singularize,
    _skills_catalog_projection,
    _split_md_row,
    _split_top_level,
    _stub_extract,
    _syms,
    _synthesize_coverage,
    _synthesize_index,
    _synthesize_manifest,
    _synthesize_overview,
    _toml_strip_comment,
    _topo_order,
    _vendored,
    derive,
    detect_stack,
    dry_run,
    extract_decision_index,
    resolve_extractor,
)

from .packs.crux import (   # noqa: F401
    _detect_crux,
    extract_api_surface,
    extract_data_model,
    extract_module_graph,
)

from .packs.python import (   # noqa: F401
    _MODEL_BASES,
    _base_name,
    _classdef_model_info,
    _column_type_and_fk,
    _const_str,
    _detect_alembic_versions,
    _detect_python,
    _detect_python_api_surface,
    _detect_python_data_model,
    _detect_python_module_graph,
    _down_revisions,
    _extract_column,
    _extract_tablename,
    _first_str_arg,
    _has_migration,
    _kw_bool,
    _pyproject_pkg_name,
    _resolve_pkg_dir,
    _scan_alembic_timeline,
    _scan_sqlalchemy_models,
    _yn,
    detect_package,
    detect_packages,
    extract_python_api_surface,
    extract_python_data_model,
    extract_python_module_graph,
)

from .packs.ruby import (   # noqa: F401
    RubySources,
    _ADD_FK,
    _AUTOLOAD_ASSIGN,
    _CREATE_TABLE_HEAD,
    _DEFAULT,
    _FK_COLUMN,
    _INDEX_COLS,
    _INDEX_NAME,
    _INDEX_UNIQUE,
    _NULL_FALSE,
    _NULL_TRUE,
    _PLURAL_ACTIONS,
    _RB_CONST,
    _RB_DQ,
    _RB_LINE_COMMENT,
    _RB_PATH_STRING,
    _RB_REQUIRE,
    _RB_REQUIRE_REL,
    _RB_SQ,
    _ROUTES_UNDER_COUNT,
    _SINGULAR_ACTIONS,
    _T_COLUMN,
    _T_INDEX,
    _autoload_roots,
    _build_fk_map,
    _camelize,
    _default_opt,
    _detect_ruby_module_graph,
    _detect_ruby_openapi,
    _detect_ruby_routes,
    _detect_ruby_schema_rb,
    _expand_resource,
    _find_rails_root,
    _index_note,
    _null_opt,
    _parse_schema,
    _pluralize,
    _rb_constant,
    _ruby_constants,
    _ruby_openapi_path,
    _scrub_ruby,
    _scrub_ruby_comments,
    _walk_rb_dir,
    detect_ruby_sources,
    extract_ruby_api_surface_openapi,
    extract_ruby_api_surface_routes,
    extract_ruby_data_model,
    extract_ruby_module_graph,
)

from .packs.node import (   # noqa: F401
    _JS_DYNIMPORT,
    _JS_EXPORT_FROM,
    _JS_EXTS,
    _JS_IMPORT_FROM,
    _JS_LADDER_EXTS,
    _JS_LINE_COMMENT,
    _JS_REQUIRE,
    _JS_SIDE_EFFECT,
    _NODE_FRAMEWORKS,
    _PRISMA_BLOCK_HEAD,
    _PRISMA_FIELD,
    _PRISMA_SCALARS,
    _detect_node_module_graph,
    _detect_node_mongoose,
    _detect_node_openapi,
    _detect_node_prisma,
    _detect_node_routes,
    _detect_node_scan,
    _detect_node_sequelize,
    _detect_node_typeorm,
    _find_prisma_schemas,
    _has_js_sources,
    _join_nest,
    _js_object_from,
    _js_specifiers,
    _ladder,
    _load_tsconfig_aliases,
    _node_openapi_path,
    _node_pkg_label,
    _parse_prisma,
    _parse_seq_attrs,
    _parse_sequelize,
    _parse_typeorm,
    _prisma_call_arg,
    _prisma_default,
    _resolve_js_spec,
    _scan_nest_routes,
    _scan_node_files,
    _scan_verb_calls,
    _scrub_js_comments,
    _strip_prisma_comments,
    _ts_prop_type,
    _walk_js_files,
    extract_node_api_surface_openapi,
    extract_node_api_surface_routes,
    extract_node_data_model_mongoose,
    extract_node_data_model_prisma,
    extract_node_data_model_sequelize,
    extract_node_data_model_typeorm,
    extract_node_module_graph,
)

from .packs.elixir import (   # noqa: F401
    _ECTO_BELONGS_TO_RE,
    _ECTO_EMBEDDED_SCHEMA_RE,
    _ECTO_EMBEDS_RE,
    _ECTO_FIELD_RE,
    _ECTO_HAS_RE,
    _ECTO_M2M_RE,
    _ECTO_SCHEMA_RE,
    _ELIXIR_ALIAS_GROUP_RE,
    _ELIXIR_ALIAS_RE,
    _ELIXIR_CAMEL_RE,
    _ELIXIR_DEFMODULE_RE,
    _ELIXIR_HAS_SCHEMA_RE,
    _ELIXIR_IMPORT_USE_RE,
    _ELIXIR_NULL_RESIDUAL,
    _ELIXIR_OPENS_BLOCK,
    _ELIXIR_ROUTES_UNDER_COUNT,
    _ELIXIR_SIGIL_DELIMS,
    _ELIXIR_SIGIL_OPEN,
    _ElixirScrubBail,
    _FOREIGN_KEY_TYPE_RE,
    _PHOENIX_ACTIONS,
    _PRIMARY_KEY_FALSE_RE,
    _PRIMARY_KEY_TUPLE_RE,
    _build_ecto_schema,
    _detect_elixir_migrations,
    _detect_elixir_module_graph,
    _detect_elixir_openapi,
    _detect_elixir_router,
    _detect_elixir_schema,
    _ecto_default,
    _ecto_enum_values,
    _ecto_type_render,
    _elixir_alias_table,
    _elixir_migrations_dir,
    _elixir_openapi_path,
    _elixir_opens_block,
    _elixir_refs,
    _elixir_router_files,
    _elixir_scan_contains,
    _expand_elixir_resource,
    _gather_ecto_block,
    _has_ex_sources,
    _parse_elixir_router,
    _parse_elixir_schemas,
    _scan_ex_files,
    _scrub_elixir_comments,
    _walk_ex_files,
    extract_elixir_api_surface_openapi,
    extract_elixir_api_surface_router,
    extract_elixir_data_model,
    extract_elixir_migrations,
    extract_elixir_module_graph,
)


# ── shims: the two things the split RENAMED rather than moved ────────────────
# Everything above is a re-export of a name that still exists under its own
# spelling somewhere. These do not, and a shim is more honest than either
# leaving the old name pointing at nothing or rewriting the callers of a test
# surface the byte-identity gate wants frozen.


#: The pre-clause-6 spelling of the pack registry, kept because the pre-split
#: namespace gate binds the name. It is `PACK_NAMES` — REGISTRATION ORDER — and
#: it is no longer the precedence: ADR-0096 clause 6 moved the precedence into
#: `PackEntry.beats`, where it is pairwise and records only the pairs something
#: measured justifies. Read `core.PACK_REGISTRY` for the live structure.
STACK_PRECEDENCE = PACK_NAMES


def _pack_probes(pack_name: str, decision_index_mode: str) -> dict:
    """The pre-split spelling of `core.pack_probes`.

    It lost its leading underscore because it stopped being private to one
    module: the core now assembles the registry from each pack's own `probes()`
    plus the universal decision-index probe, so it is the seam between them
    rather than an internal detail of the monolith."""
    return pack_probes(pack_name, decision_index_mode)


def _detect_crux(root: Path) -> bool:
    """The pre-split spelling of `packs.crux.detect(root).matched`.

    Each pack now owns its detector and returns a `DetectResult` carrying the
    markers that fired, because clause 6 requires an ambiguous multi-match to
    name each candidate AND the marker that matched it. These five shims discard
    the markers and return the bare bool the pre-split callers expect."""
    return PACK_DETECTORS["crux"](root)


def _detect_python(root: Path) -> bool:
    """See `_detect_crux`."""
    return PACK_DETECTORS["python"](root)


def _detect_ruby(root: Path) -> bool:
    """See `_detect_crux`."""
    return PACK_DETECTORS["ruby"](root)


def _detect_node(root: Path) -> bool:
    """See `_detect_crux`."""
    return PACK_DETECTORS["node"](root)


def _detect_elixir(root: Path) -> bool:
    """See `_detect_crux`."""
    return PACK_DETECTORS["elixir"](root)
