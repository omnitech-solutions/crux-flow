"""The split seam: `crux.arch.core` and the `crux.arch.packs` namespace (ADR-0096 clause 12).

Clause 12 splits the 5,161-line deriver into a core (probe registry, input
resolution and decoding, canonical serialization, hashing, verdict computation,
the stub renderer) plus one module per pack. Its own gate is byte-identity —
every corpus golden is unchanged across the split — and that gate lives in
`test_arch_corpus.py`. What lives HERE are the three structural properties the
byte-identity gate cannot see:

  1. **The seam exists.** `crux.arch.core` exports the probe seam every pack
     module binds against.
  2. **The namespace did not shrink.** `crux.arch.derive` survives as a
     re-export facade, so the 11 test modules binding 75 `D.<symbol>` names —
     most of them private — keep resolving. The oracle is a committed snapshot
     of `dir(derive)` taken immediately before the split.
  3. **`core.py` has no module-level intra-crux import.** This is what keeps
     `runtime/capture.py::_load_cell` alive: it file-loads the cell renderer via
     `spec_from_file_location`, deliberately bypassing `crux/__init__`'s
     LLM/httpx import chain, and that works only while the loaded module is
     stdlib-self-contained at module scope. A `from .packs import ...` at
     core's module scope would break the runtime executor.

Stdlib only.
"""

from __future__ import annotations

import ast
import importlib
import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]      # crux/scripts
sys.path.insert(0, str(SCRIPTS))

# The snapshot of `dir(crux.arch.derive)` taken immediately before the split.
PRESPLIT_SNAPSHOT = Path(__file__).resolve().parent / "arch_derive_presplit_namespace.json"

ARCH_PKG = SCRIPTS / "crux" / "arch"

# Names the pre-split snapshot carries that ADR-0096 clause 10 deliberately
# RETIRED. The snapshot itself is never edited: it is the pre-split truth, and
# rewriting it to match whatever the code now exports would turn the gate into a
# tautology. A name leaves the facade only by being listed here, which makes
# every removal a reviewed edit carrying the clause that authorized it.
#
# Clause 10 retires ADR-0074's grade scale, its expectation-marker table, its
# confidence floors and partial-capture probes, and the three `coverage.json`
# confidence fields. The measurement behind it: the floor and probe tables
# shipped empty, so no probe ever fired and every populated concern graded
# `high`.
CLAUSE_10_RETIRED = frozenset({
    "ExpectationMarker",
    "_EXPECTATION_MARKERS",
    "_CONFIDENCE_FLOORS",
    "_PARTIAL_CAPTURE_PROBES",
    "_CONFIDENCE_REASONS",
    "_CONFIDENCE_RANK",
    "_confidence_grade",
    "_escalation_offered",
    "_expectation_present",
    "_marker_dep_present",
    "_marker_file_exists",
})

# Clause 3 retires the PB-0069 coverage vocabularies. Both tables shipped EMPTY,
# so every corpus record fell through to the templated default and read
# "<concern> <status> under the <pack> pack" — a restatement of two fields
# already in the record. The closed six-reason set computes the reason instead
# of tabulating it, and `inputs_missing` / `reason` leave the recorded channel
# with the tables that fed them. Listed here for the same reason the clause 10
# set is: a removal from the frozen facade is a reviewed edit naming its clause.
CLAUSE_3_RETIRED = frozenset({
    "_COVERAGE_STUB_INPUTS",
    "_COVERAGE_REASONS",
})

# Clause 1 forbids a concern that declares the input class `parser` from
# matching a regular expression against authored source. The elixir pack's
# api-surface concern declares one, and these eight patterns WERE that reader:
# a text match for `use Phoenix.Router` chose the router file, and five
# line-anchored patterns read the DSL out of it. Both were measured wrong on
# both corpus Phoenix applications — the text match selected the `_web.ex`
# entrypoint, whose macro body carries the same call and whose path sorts
# first, and the verb pattern required the space-delimited call spelling. A
# tree-sitter parse replaced them, so the patterns are gone rather than unused.
#
# `_ELIXIR_OPENS_BLOCK` is deliberately NOT here: the data-model reader's
# `_gather_ecto_block` still uses it, and that concern's reader is untouched.
# Listed here for the same reason the two sets above are: a removal from the
# frozen facade is a reviewed edit naming the clause that authorized it.
CLAUSE_1_RETIRED = frozenset({
    "_ELIXIR_ROUTER_GLOB",
    "_ELIXIR_SCOPE_PATH_ALIAS",
    "_ELIXIR_SCOPE_PATH_ONLY",
    "_ELIXIR_VERB_RE",
    "_ELIXIR_LIVE_RE",
    "_ELIXIR_RESOURCES_RE",
    "_ELIXIR_FORWARD_RE",
    "_ELIXIR_PIPE_THROUGH_RE",
    # The data-model concern's second reader. `extract_elixir_migrations_stub`
    # read no source at all: it globbed `priv/repo/migrations/*.exs`, hashed each
    # file, and rendered one sentence saying the parse was deferred (ADR-0069
    # point 2). A repository whose only data-model surface is its migrations
    # therefore rendered zero entities and took `no_entities` over a full schema.
    # `extract_elixir_migrations` replays them through the same grammar the
    # router reader uses, so the concern's `parser` declaration is now met by
    # this reader rather than deferred by it.
    "extract_elixir_migrations_stub",
    # The node pack's api-surface reader, dev loop 3. Same clause, same reason,
    # one pack over: these five patterns WERE the route reader, and a tree-sitter
    # parse replaced them. Three of the corpus findings they produced are gone
    # with them — the handler read as the argument after the path rather than the
    # last one, the chained `router.route(p).get(h)` form missed entirely, and an
    # interpolated template literal rendered as though its source text were a
    # path. They are retired rather than kept as dead module-level constants,
    # because a constant nothing reads is a claim about a reader that no longer
    # exists.
    "_JS_VERB_CALL",
    "_JS_HANDLER",
    "_NEST_CONTROLLER",
    "_NEST_METHOD",
    "_NEST_HANDLER",
    # The ruby pack's api-surface reader, dev loop 3. Same clause, same reason,
    # one pack over: these seven patterns plus the line scan that drove them WERE
    # the `config/routes.rb` reader, and a tree-sitter parse replaced them. Four
    # measured defects go with them — a `member`/`collection` block declared a
    # residual rather than a path, a nested `resources` losing its parent segment
    # entirely, an `if … end` decrementing a `do`-depth it never incremented and
    # popping the enclosing frame early, and `only: %i[a b]` matching nothing so
    # that a resource declaring three actions rendered all eight.
    #
    # `core._ONLY_RE`, `core._EXCEPT_RE`, `core._SYM_RE` and `core._syms` are
    # deliberately NOT here: they are reached through `_expand_resource`, which
    # this unit reuses unchanged, and that reuse is what keeps a plain
    # `resources` row byte-identical across the change.
    "_NAMESPACE_RE",
    "_SCOPE_RE",
    "_SCOPE_PATH",
    "_MODULE_OPT",
    "_RESOURCE_RE",
    "_VERB_RE",
    "_OPENS_BLOCK",
    "_parse_routes",
    # The ruby pack's data-model second reader, and the same retirement the
    # elixir migrations stub took one loop earlier — with the opposite
    # conclusion about what replaces it. `extract_ruby_structure_sql_stub` read
    # no schema at all: it rendered one sentence, hashed `db/structure.sql` and
    # returned zero entities, so the recorded verdict computed to `no_entities`
    # — "the input was consumed and declares none of the concern's entities" —
    # of a file nothing had read. Nothing replaces it. A SQL DDL parser would be
    # admissible under clause 1 (`pg_dump` emits the file, so its grammar is an
    # emitter's) and is not written, because `db/schema.rb` wins the chain on
    # BOTH corpus ruby repositories and the reader would ship unmeasured. The
    # honest `precondition_missing`, whose `expected` names the artifact and the
    # command that emits it, is the whole replacement.
    "extract_ruby_structure_sql_stub",
    "_detect_ruby_structure_sql",
})


class CoreSeamTests(unittest.TestCase):
    """(1) The seam exists and carries the probe contract."""

    def test_core_module_exports_the_probe_seam(self):
        core = importlib.import_module("crux.arch.core")

        # The probe contract itself.
        self.assertTrue(hasattr(core, "Probe"))
        probe = core.Probe(detect=lambda _root: True, extract=lambda _r, _d: ("", {}),
                           kind="parser")
        self.assertEqual(probe._fields, ("detect", "extract", "kind"))

        # The registry + precedence seam a pack module resolves through.
        # ADR-0096 clause 6 merged the two into ONE structure: `PACK_REGISTRY`
        # is the registry AND the pairwise precedence, and `PACK_NAMES` /
        # `REGISTERED_PACKS` are views derived from it. `STACK_PRECEDENCE` is
        # gone from the core — it survives only as a facade shim on
        # `crux.arch.derive`, which the pre-split namespace gate below checks.
        for name in ("PACK_REGISTRY", "PACK_NAMES", "PackEntry", "REGISTERED_PACKS",
                     "STUB_PACK_NAME", "CONCERNS"):
            self.assertTrue(hasattr(core, name), f"core is missing {name!r}")
        self.assertFalse(hasattr(core, "STACK_PRECEDENCE"),
                         "the core kept a second precedence structure beside the registry")

        # The stub renderer and the always-detector every pack binds.
        self.assertTrue(core._always(Path(".")))
        md, sources = core._stub_extract("data-model", Path("."), "bionic")
        self.assertEqual(sources, {})
        self.assertIn("# Data model", md)
        self.assertIn(core.NO_EXTRACTOR.rstrip("\n"), md)

        # The serialization/hashing helpers the packs share.
        for name in ("_read", "_sha256_hex", "_rel", "_canon", "_cell", "_prose_cell",
                     "_frontmatter", "_safe_read_bytes", "_iter_py_files"):
            self.assertTrue(callable(getattr(core, name, None)), f"core is missing {name!r}")

    def test_packs_namespace_is_importable(self):
        pkg = importlib.import_module("crux.arch.packs")
        self.assertTrue(Path(pkg.__file__).name == "__init__.py")


class DeriveFacadeTests(unittest.TestCase):
    """(2) The namespace is a superset of the pre-split snapshot."""

    def test_derive_namespace_is_a_superset_of_the_presplit_snapshot(self):
        # Dunders are module machinery, not the module's contract — `__annotations__`
        # exists or not depending on whether a module happens to carry an annotated
        # module-level assignment, which is not a property any caller binds.
        before = ({n for n in json.loads(PRESPLIT_SNAPSHOT.read_text(encoding="utf-8"))
                   if not n.startswith("__")}
                  - CLAUSE_10_RETIRED - CLAUSE_3_RETIRED - CLAUSE_1_RETIRED)
        derive = importlib.import_module("crux.arch.derive")
        now = set(dir(derive))
        missing = sorted(before - now)
        self.assertEqual(
            missing, [],
            "crux.arch.derive lost symbols across the split; it must survive as a "
            "re-export facade because 11 test modules bind D.<symbol> names directly:\n  "
            + "\n  ".join(missing),
        )


class CoreImportHygieneTests(unittest.TestCase):
    """(3) `core.py` carries no module-level intra-crux import.

    `runtime/capture.py::_load_cell` file-loads the cell renderer by path,
    bypassing the package `__init__`. That only works while the loaded module is
    stdlib-self-contained at module scope. Function-level intra-crux imports are
    fine — `derive.py`'s single `from .recover import curated_keep` has always
    been one — so this walk inspects module scope only.
    """

    def _module_scope_imports(self, path: Path) -> list[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found = []
        for node in tree.body:                     # module scope ONLY, not ast.walk
            if isinstance(node, ast.Import):
                found.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                found.append("." * (node.level or 0) + (node.module or ""))
        return found

    def test_core_has_no_module_level_intra_crux_import(self):
        core_py = ARCH_PKG / "core.py"
        self.assertTrue(core_py.exists(), f"{core_py} does not exist")
        offenders = [
            name for name in self._module_scope_imports(core_py)
            if name.startswith(".") or name.split(".")[0] == "crux"
        ]
        self.assertEqual(
            offenders, [],
            "crux/arch/core.py gained a module-level intra-crux import "
            f"({offenders}). This breaks runtime/capture.py::_load_cell, which "
            "file-loads core.py outside the package. Import lazily inside the "
            "function body instead.",
        )

    def test_capture_load_cell_still_resolves(self):
        capture = importlib.import_module("crux.arch.runtime.capture")
        self.assertTrue(callable(capture._cell))
        self.assertEqual(capture._cell("a|b"), "a\\|b")


class Clause10RetirementTests(unittest.TestCase):
    """Clause 10's retirement is complete, and no wider than it claims."""

    def test_every_retired_name_is_actually_gone(self):
        """A name listed as retired must not still be reachable.

        Without this, `CLAUSE_10_RETIRED` would be a mute button: adding a name
        to it would silence the superset gate whether or not the name was really
        removed, which is the opposite of what the list is for.
        """
        derive = importlib.import_module("crux.arch.derive")
        core = importlib.import_module("crux.arch.core")
        still_there = sorted(
            n for n in CLAUSE_10_RETIRED
            if hasattr(derive, n) or hasattr(core, n)
        )
        self.assertEqual(still_there, [],
                         "listed as retired by clause 10 but still exported")

    def test_every_clause_3_retired_name_is_actually_gone(self):
        """The same mute-button guard for `CLAUSE_3_RETIRED`.

        This set was the one the guard did not cover. Both its names were
        restored to `core.py` and the whole file still passed 10/10 — so the
        list was doing exactly what the two comments above forbid: silencing
        the superset gate whether or not the name was really removed.

        Reaching past the two facade modules to the packs, for the reason
        `CLAUSE_1_RETIRED`'s guard does: the coverage vocabularies fed the
        per-pack stub renderer, so a table still defined in a pack but merely
        dropped from the re-export list would satisfy a check that only asked
        `derive`.
        """
        modules = [importlib.import_module(m) for m in
                   ("crux.arch.derive", "crux.arch.core", "crux.arch.packs.crux",
                    "crux.arch.packs.python", "crux.arch.packs.elixir",
                    "crux.arch.packs.node", "crux.arch.packs.ruby")]
        still_there = sorted(
            n for n in CLAUSE_3_RETIRED if any(hasattr(m, n) for m in modules)
        )
        self.assertEqual(still_there, [],
                         "listed as retired by clause 3 but still defined")

    def test_every_clause_1_retired_pattern_is_actually_gone(self):
        """The same mute-button guard for `CLAUSE_1_RETIRED`.

        Reaching past the two facade modules to the pack itself, because that
        is where the patterns lived and where a reader would put one back: a
        pattern still defined in `packs/elixir.py` but merely dropped from the
        re-export list would satisfy a check that only asked `derive`.
        """
        modules = [importlib.import_module(m) for m in
                   ("crux.arch.derive", "crux.arch.core", "crux.arch.packs.elixir",
                    "crux.arch.packs.node", "crux.arch.packs.ruby")]
        still_there = sorted(
            n for n in CLAUSE_1_RETIRED if any(hasattr(m, n) for m in modules)
        )
        self.assertEqual(still_there, [],
                         "listed as retired by clause 1 but still defined")

    def test_the_api_surface_reader_declares_the_grammar_that_replaced_them(self):
        """A retirement that removed the reader and put nothing back would pass
        the check above. The concern's declared `parser` tuple is what says a
        real grammar took over."""
        core = importlib.import_module("crux.arch.core")
        declared = core.input_classes("elixir")["api-surface"]
        self.assertEqual(declared.kind, "parser")
        self.assertEqual(declared.parser, ("tree_sitter", "tree_sitter_elixir"))

    def test_the_dependency_readers_clause_10_keeps_are_still_here(self):
        """Clause 10 retires the grade scale and keeps the dependency readers.

        They were built for the expectation markers and outlive them because
        clause 6 detection reads a declared dependency set. Asserting the
        survivors keeps a future cleanup from taking the retirement one step too
        far and quietly removing what U11 depends on.
        """
        core = importlib.import_module("crux.arch.core")
        for name in ("_pep508_names_in", "_requirements_direct_dep_names",
                     "_pyproject_direct_dep_names", "_toml_strip_comment",
                     "_array_closes_outside_quotes"):
            self.assertTrue(callable(getattr(core, name, None)),
                            f"clause 10 kept {name!r}; it is gone")

    def test_no_coverage_record_carries_a_confidence_field(self):
        """The three retired `coverage.json` fields are out of the recorded channel."""
        core = importlib.import_module("crux.arch.core")
        built = core._build(Path(__file__).resolve().parents[3], "bionic")
        coverage = json.loads(built["_meta/coverage.json"])
        for record in coverage["concerns"]:
            for field in ("confidence", "confidence_reason", "escalation_offered"):
                self.assertNotIn(field, record,
                                 f"{field!r} survived clause 10 in coverage.json")
            # Clause 3 retires the two PB-0069 vocabulary fields with the empty
            # tables that fed them; same exactness, same reason.
            for field in ("status", "reason", "inputs_missing"):
                self.assertNotIn(field, record,
                                 f"{field!r} survived clause 3 in coverage.json")


if __name__ == "__main__":
    unittest.main()
