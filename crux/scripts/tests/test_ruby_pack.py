"""Tests for the Ruby arch stack pack (ADR-0067, dev module of PB-0065).

Covers the three Ruby concern extractors (api-surface, data-model,
module-graph), their detection + resolution through the ADR-0066 seam, the
three-rung api-surface chain (committed OpenAPI, then the committed
`bin/rails routes --expanded` dump, then a tree-sitter `config/routes.rb`
parse), the resolve-or-drop Zeitwerk module graph (dropped gem edge + constant
collision + sanitized node id), the honest `precondition_missing` a
`db/structure.sql`-only repository takes, output escaping, path/size safety,
drift-gating, byte-stable re-derivation, and — as a regression on the ADR-0067
point-6 refactor — that the Python pack's api-surface output is unchanged.

The committed `fixtures/railsapp/` fixture is copied to a tempdir before any
mutation, so it is never altered. Not stdlib-only since ADR-0096 clause 1: the
api-surface concern declares the tree-sitter Ruby grammar, which `pyproject.toml`
pins exactly and `ParserPinLockStepTests` holds in lock-step with the two PEP 723
declaration sites.
"""

from __future__ import annotations

import importlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]      # crux/scripts
sys.path.insert(0, str(SCRIPTS))

D = importlib.import_module("crux.arch.derive")

# ADR-0096 clause 12 split the engine, and `crux.arch.derive` is now a re-export
# facade. Reading a name through it still works; PATCHING one through it does
# not, because a re-export is a separate binding and the reader never sees the
# replacement. So a monkeypatch must name the module that actually reads the
# value. This binding is that module.
RUBY = importlib.import_module("crux.arch.packs.ruby")
CORE = importlib.import_module("crux.arch.core")

FIXTURE =Path(__file__).resolve().parent / "fixtures" / "railsapp"
PYFIXTURE = Path(__file__).resolve().parent / "fixtures" / "pyservice"


def _copy(fixture: Path = FIXTURE):
    tmp = tempfile.TemporaryDirectory()
    dest = Path(tmp.name) / "repo"
    shutil.copytree(fixture, dest)
    (dest / "bionic").mkdir()
    return tmp, dest


# ───────────────────────── detection + resolution ──────────────────────────

class DetectionAndResolutionTests(unittest.TestCase):
    def test_detects_ruby_stack(self):
        # Gemfile present, no crux/python markers → auto-detects the ruby pack.
        self.assertEqual(D.detect_stack(FIXTURE, None), "ruby")

    def test_resolution_picks_real_extractors(self):
        # No openapi.json → api-surface resolves to the routes probe (2nd in list).
        self.assertIs(
            D.resolve_extractor("api-surface", FIXTURE, "bionic", None, pack_name="ruby"),
            D.extract_ruby_api_surface_routes,
        )
        # db/schema.rb present → data-model resolves to the schema.rb probe (1st).
        self.assertIs(
            D.resolve_extractor("data-model", FIXTURE, "bionic", None, pack_name="ruby"),
            D.extract_ruby_data_model,
        )
        self.assertIs(
            D.resolve_extractor("module-graph", FIXTURE, "bionic", None, pack_name="ruby"),
            D.extract_ruby_module_graph,
        )

    def test_detect_ruby_sources_rails_root_and_roots(self):
        src = D.detect_ruby_sources(FIXTURE)
        self.assertEqual(src.rails_root, FIXTURE)
        # Conventional roots: app/* (sorted) then lib.
        rels = [p.relative_to(FIXTURE).as_posix() for p in src.roots]
        self.assertEqual(rels, ["app/controllers", "app/models", "lib"])
        self.assertTrue(src.entries)


# ─────────────────────────── api-surface (routes) ──────────────────────────

class RoutesTests(unittest.TestCase):
    def setUp(self):
        self.md, self.sources = D.extract_ruby_api_surface_routes(FIXTURE, "bionic")

    def test_static_parse_label_and_source(self):
        self.assertIn("static parse of `config/routes.rb`", self.md.lower())
        self.assertIn("config/routes.rb", self.sources)          # drift-gate.

    def test_resources_only_scope(self):
        # `resources :users, only: [:index, :show]` → exactly those two rows.
        self.assertIn("| GET | `/users` | `users#index` |", self.md)
        self.assertIn("| GET | `/users/:id` | `users#show` |", self.md)
        self.assertNotIn("users#create", self.md)
        self.assertNotIn("users#destroy", self.md)

    def test_singular_resource_expands(self):
        # `resource :profile` (singular) → plural controller, no :id, no index.
        self.assertIn("| GET | `/profile` | `profiles#show` |", self.md)
        self.assertIn("| POST | `/profile` | `profiles#create` |", self.md)
        self.assertNotIn("profiles#index", self.md)

    def test_namespaced_resource(self):
        # `namespace :admin { resources :reports }` → path + module prefix.
        self.assertIn("| GET | `/admin/reports` | `admin/reports#index` |", self.md)
        self.assertIn("| GET | `/admin/reports/:id` | `admin/reports#show` |", self.md)

    def test_except_scope(self):
        # `except: [:destroy]` drops only the destroy action.
        self.assertNotIn("admin/reports#destroy", self.md)
        self.assertIn("admin/reports#update", self.md)

    def test_custom_verb_routes(self):
        self.assertIn("| GET | `/login` | `sessions#new` |", self.md)
        self.assertIn("| POST | `/logout` | `sessions#destroy` |", self.md)

    def test_commented_route_not_parsed(self):
        self.assertNotIn("ghosts#index", self.md)

    def test_sorted_by_path_method_controller(self):
        rows = [ln for ln in self.md.splitlines() if ln.startswith("| GET |")
                or ln.startswith("| POST |") or ln.startswith("| PUT |")
                or ln.startswith("| PATCH |") or ln.startswith("| DELETE |")]
        # Extract (path, method) keys and confirm the render is sorted by (path, method).
        keys = []
        for ln in rows:
            parts = [c.strip(" `") for c in ln.strip("|").split("|")]
            keys.append((parts[1], parts[0]))
        self.assertEqual(keys, sorted(keys))

    def test_under_count_residual(self):
        # The residual is a SHORT list now: the tree-sitter reader expands
        # `member`/`collection` rather than declaring them lost, and what is left
        # is the set of declarations `arch-inputs/routes.txt` closes.
        self.assertIn("`root`", self.md)
        self.assertIn("`mount`ed engines", self.md)
        self.assertIn("arch-inputs/routes.txt", self.md)
        self.assertNotIn("member`/`collection", self.md)


# ───────── api-surface: `config/routes.rb` through tree-sitter (U-R1b) ──────
#
# The concern declares the input class `parser`, and ADR-0096 clause 1 forbids
# reading authored source with a regular expression. `config/routes.rb` is
# authored source. Every case below is one the line-scanning reader got wrong or
# could not see, and each is a STRUCTURAL fact of the parse tree rather than a
# spelling the pattern happened to match.


def _routes_md(body: str):
    """Render api-surface over a fixture whose `config/routes.rb` is `body`."""
    tmp, root = _copy()
    (root / "config" / "routes.rb").write_text(
        "Rails.application.routes.draw do\n" + body + "\nend\n")
    md, sources = RUBY.extract_ruby_api_surface_routes(root, "bionic")
    return tmp, md, sources


class RoutesTreeSitterTests(unittest.TestCase):
    def render(self, body: str) -> str:
        tmp, md, _ = _routes_md(body)
        self.addCleanup(tmp.cleanup)
        return md

    def test_member_block_contributes_the_id_segment(self):
        md = self.render("""
  resources :users do
    member do
      get :subscribe
    end
  end
""")
        self.assertIn("| GET | `/users/:id/subscribe` | `users#subscribe` |", md)

    def test_collection_block_contributes_a_bare_segment(self):
        md = self.render("""
  resources :users do
    collection do
      get :search
    end
  end
""")
        self.assertIn("| GET | `/users/search` | `users#search` |", md)

    def test_on_member_and_on_collection_are_the_same_fact_as_the_blocks(self):
        md = self.render("""
  resources :users do
    get :subscribe, on: :member
    get :search, on: :collection
  end
""")
        self.assertIn("| GET | `/users/:id/subscribe` | `users#subscribe` |", md)
        self.assertIn("| GET | `/users/search` | `users#search` |", md)

    def test_nested_resources_inherit_the_enclosing_resource_segment(self):
        """The measured defect: `resources :dependencies` inside
        `resources :versions do` rendered `/dependencies`, losing the parent."""
        md = self.render("""
  resources :versions do
    resources :dependencies, only: [:index]
  end
""")
        self.assertIn(
            "| GET | `/versions/:version_id/dependencies` | `dependencies#index` |", md)
        self.assertNotIn("| GET | `/dependencies` |", md)

    def test_the_multi_line_verb_form_is_one_node(self):
        md = self.render("""
  get '/owners/:handle/gems',
    to: 'owners#gems',
    as: 'owners_gems'
""")
        self.assertIn("| GET | `/owners/:handle/gems` | `owners#gems` |", md)

    def test_the_hashrocket_verb_form_is_one_node(self):
        md = self.render("""
  get "example_app" => "university#show", :id => "example_app"
""")
        self.assertIn("| GET | `/example_app` | `university#show` |", md)

    def test_a_percent_i_action_list_is_read_as_a_keyword_argument(self):
        """`only: %i[show create]` — the pattern reader could not see `%i[]` at
        all, so it applied NO filter and rendered every REST action."""
        md = self.render("  resources :users, only: %i[index show]\n")
        self.assertIn("| GET | `/users` | `users#index` |", md)
        self.assertIn("| GET | `/users/:id` | `users#show` |", md)
        self.assertNotIn("users#create", md)
        self.assertNotIn("users#destroy", md)

    def test_a_percent_i_except_list_is_read_as_a_keyword_argument(self):
        md = self.render("  resources :users, except: %i[destroy edit]\n")
        self.assertIn("| GET | `/users` | `users#index` |", md)
        self.assertNotIn("users#destroy", md)
        self.assertNotIn("users#edit", md)

    def test_a_handlerless_verb_inside_a_resource_infers_its_controller(self):
        md = self.render("""
  resource :multifactor_auth, only: [] do
    get 'recovery'
  end
""")
        self.assertIn(
            "| GET | `/multifactor_auth/recovery` | `multifactor_auths#recovery` |", md)

    def test_a_handlerless_verb_outside_a_resource_is_a_named_residual(self):
        md = self.render("""
  get 'orphan'
  get '/anchor', to: 'anchors#show'
""")
        self.assertNotIn("orphan", md.split("## Residuals")[0])
        self.assertIn("no `to:` handler", md)

    def test_a_route_in_a_comment_or_a_string_is_not_a_call_node(self):
        md = self.render("""
  # get "/ghost", to: "ghosts#index"
  FOOTER = 'get "/phantom", to: "phantoms#index"'
""")
        self.assertNotIn("ghosts#index", md)
        self.assertNotIn("phantoms#index", md)

    def test_a_conditional_block_does_not_pop_an_enclosing_frame(self):
        """The `do`-counting reader decremented its depth on the `end` of an
        `if` it never counted an open for, popping the enclosing frame early."""
        md = self.render("""
  namespace :admin do
    if true
      get 'ping', to: 'ping#index'
    end
    get 'pong', to: 'pong#index'
  end
""")
        self.assertIn("| GET | `/admin/ping` | `admin/ping#index` |", md)
        self.assertIn("| GET | `/admin/pong` | `admin/pong#index` |", md)

    def test_root_and_mount_are_named_residuals(self):
        md = self.render("""
  root to: "home#index"
  mount Sidekiq::Web => "/sidekiq"
  get '/anchor', to: 'anchors#show'
""")
        self.assertIn("`root`", md)
        self.assertIn("`mount`", md)
        self.assertIn("arch-inputs/routes.txt", md)     # the input that closes them.

    def test_the_declared_parser_is_the_ruby_grammar(self):
        declared = D.input_classes("ruby")["api-surface"]
        self.assertEqual(declared.kind, "parser")
        self.assertEqual(declared.parser, ("tree_sitter", "tree_sitter_ruby"))

    def test_the_retired_patterns_are_gone(self):
        for name in ("_NAMESPACE_RE", "_SCOPE_RE", "_SCOPE_PATH", "_MODULE_OPT",
                     "_RESOURCE_RE", "_VERB_RE", "_OPENS_BLOCK", "_parse_routes"):
            self.assertFalse(hasattr(RUBY, name), f"{name} survived clause 1")

    def test_the_row_logic_the_unit_reuses_is_untouched(self):
        """`_expand_resource` and its tables are row logic, not source patterns.
        Reusing them unchanged is what keeps a plain `resources` byte-identical.

        `None, None` is the no-filter spelling. It was `set(), set()` while an
        empty filter meant "no filter"; an empty `only:` now declares zero
        actions, so the two spellings are no longer interchangeable."""
        for name in ("_expand_resource", "_PLURAL_ACTIONS", "_SINGULAR_ACTIONS",
                     "_pluralize"):
            self.assertTrue(hasattr(RUBY, name), f"{name} was removed")
        self.assertEqual(
            RUBY._expand_resource("users", False, [], [], set(), None), [],
            "an empty `only:` declares zero REST actions")
        self.assertEqual(
            RUBY._expand_resource("users", False, [], [], None, None),
            [("GET", "/users", "users#index"),
             ("GET", "/users/new", "users#new"),
             ("POST", "/users", "users#create"),
             ("GET", "/users/:id", "users#show"),
             ("GET", "/users/:id/edit", "users#edit"),
             ("PATCH", "/users/:id", "users#update"),
             ("PUT", "/users/:id", "users#update"),
             ("DELETE", "/users/:id", "users#destroy")])


# ───────── api-surface: the committed `rails routes --expanded` dump ────────
#
# U-R1a. The artifact probe sits between the committed OpenAPI document and the
# `config/routes.rb` parse. No corpus repository carries the dump — a shallow
# clone at a pinned SHA cannot gain a new committed file — so FIXTURES are the
# whole proof for this probe, and ADR-0096 clause 8 asks for a corpus repository
# per PACK, not per probe.

#: One `rails routes --expanded` record, indented exactly as Rails emits it.
def _dump(*records: str) -> str:
    out = []
    for i, body in enumerate(records, start=1):
        out.append("--[ Route %d ]" % i + "-" * 60)
        out.append(body.strip("\n"))
    return "\n".join(out) + "\n"


_MULTI_VERB = """
Prefix            | user
Verb              | GET|POST
URI Pattern       | /users/:id(.:format)
Controller#Action | users#show
"""

_CONSTRAINED = """
Prefix            | feed
Verb              | GET
URI Pattern       | /feeds/:id(.:format)
Controller#Action | feeds#show
Source Location   | config/routes.rb:12
"""

_ENGINE_MOUNT = """
Prefix            | sidekiq_web
Verb              |
URI Pattern       | /sidekiq
Controller#Action | Sidekiq::Web
"""


def _with_dump(text: str, fixture: Path = FIXTURE):
    tmp, root = _copy(fixture)
    (root / "arch-inputs").mkdir()
    (root / "arch-inputs" / "routes.txt").write_text(text)
    return tmp, root


class RoutesArtifactTests(unittest.TestCase):
    def test_multi_verb_record_yields_one_row_per_verb(self):
        tmp, root = _with_dump(_dump(_MULTI_VERB))
        self.addCleanup(tmp.cleanup)
        md, sources = RUBY.extract_ruby_api_surface_routes_artifact(root, "bionic")
        self.assertIn("| GET | `/users/:id` | `users#show` |", md)
        self.assertIn("| POST | `/users/:id` | `users#show` |", md)
        self.assertIn("arch-inputs/routes.txt", sources)      # drift-gate.
        self.assertNotIn("config/routes.rb", sources)

    def test_format_suffix_stripped_and_unknown_field_ignored(self):
        tmp, root = _with_dump(_dump(_CONSTRAINED))
        self.addCleanup(tmp.cleanup)
        md, _ = RUBY.extract_ruby_api_surface_routes_artifact(root, "bionic")
        self.assertIn("| GET | `/feeds/:id` | `feeds#show` |", md)
        self.assertNotIn(".:format", md)
        self.assertNotIn("Source Location", md)

    def test_engine_mount_renders_with_no_verb_named(self):
        tmp, root = _with_dump(_dump(_ENGINE_MOUNT))
        self.addCleanup(tmp.cleanup)
        md, _ = RUBY.extract_ruby_api_surface_routes_artifact(root, "bionic")
        self.assertIn("| ANY | `/sidekiq` | `Sidekiq::Web` |", md)
        self.assertIn("records no verb", md)                  # the named residual.

    def test_a_file_matching_none_of_the_grammar_does_not_capture_the_chain(self):
        """The dump's grammar is the gate, and a file that matches none of it
        must not be claimed.

        `parse_failed` is the reason ADR-0096 clause 3 names for this condition,
        and it is not expressible today: `core.concern_verdict` computes four
        branches and says in its own docstring that `parse_failed` "belongs to
        clause 1's input decoding, which is not landed". Rendering the stub here
        would therefore record `precondition_missing` — a claim that the declared
        input is ABSENT, which is false of a file sitting on disk, and exactly the
        false claim U-R3 removes from the data-model chain.

        So the detector carries the grammar check: an unreadable dump is not this
        probe's input, the chain continues to the `config/routes.rb` reader, and
        no false verdict is recorded. The parse function still reports the
        failure to its caller, which is what the second half asserts.
        """
        tmp, root = _with_dump("this file is not a rails routes dump\n")
        self.addCleanup(tmp.cleanup)
        self.assertFalse(RUBY._detect_ruby_routes_artifact(root))
        ext = D.resolve_extractor("api-surface", root, "bionic", None, pack_name="ruby")
        self.assertIs(ext, RUBY.extract_ruby_api_surface_routes)
        rows, ok = RUBY._parse_rails_routes_dump("this is not a dump\n")
        self.assertEqual((rows, ok), ([], False))

    def test_an_unreadable_dump_with_no_routes_rb_records_a_false_absence(self):
        """The residual of the detector-placement decision, PINNED.

        Failing the detector "makes no claim at all" only while a LOWER rung
        answers. Delete `config/routes.rb` and every rung fails, so the recorded
        verdict is `precondition_missing` with `found="no matching input"` — a
        claim that the declared input is ABSENT, said of a file sitting on disk.
        That is the `parse_failed` branch `core.concern_verdict` declares and
        does not produce.

        The assertion below pins TODAY's answer rather than the right one. The
        day clause 1's input decoding lands and `parse_failed` becomes
        reachable, this test fails, and the failure is the reminder that this
        chain is one of its call sites.
        """
        tmp, root = _with_dump("this file is not a rails routes dump\n")
        self.addCleanup(tmp.cleanup)
        (root / "config" / "routes.rb").unlink()
        self.assertTrue((root / "arch-inputs" / "routes.txt").is_file(),
                        "the input whose absence the verdict will claim")
        self.assertFalse(RUBY._detect_ruby_routes_artifact(root))
        self.assertFalse(RUBY._detect_ruby_routes(root))

        ext, rung = CORE._resolve_extractor(
            "api-surface", root, "bionic", None, pack_name="ruby")
        content, sources = ext(root, "bionic")
        verdict = CORE.concern_verdict("api-surface", "ruby", rung, content, sources)
        self.assertEqual(verdict.reason, CORE.StubReason.PRECONDITION_MISSING)
        self.assertEqual(verdict.found, "no matching input",
                         "when this stops being 'no matching input', "
                         "`parse_failed` has landed and the docstring residual "
                         "on `_detect_ruby_routes_artifact` is stale")

    def test_the_dump_beats_a_present_routes_rb(self):
        tmp, root = _with_dump(_dump(_MULTI_VERB))
        self.addCleanup(tmp.cleanup)
        self.assertTrue((root / "config" / "routes.rb").is_file(), "fixture lost routes.rb")
        ext = D.resolve_extractor("api-surface", root, "bionic", None, pack_name="ruby")
        self.assertIs(ext, RUBY.extract_ruby_api_surface_routes_artifact)

    def test_a_committed_openapi_document_beats_the_dump(self):
        tmp, root = _with_dump(_dump(_MULTI_VERB))
        self.addCleanup(tmp.cleanup)
        (root / "openapi.json").write_text(json.dumps(
            {"openapi": "3.0.0",
             "paths": {"/ping": {"get": {"tags": ["health"], "summary": "Ping"}}}}))
        ext = D.resolve_extractor("api-surface", root, "bionic", None, pack_name="ruby")
        self.assertIs(ext, D.extract_ruby_api_surface_openapi)

    def test_the_dump_is_in_the_declared_input_globs(self):
        globs = D.input_classes("ruby")["api-surface"].globs
        self.assertIn("arch-inputs/routes.txt", globs)


class ApiSurfaceOrderingTests(unittest.TestCase):
    def test_openapi_probe_precedes_routes(self):
        # A committed OpenAPI doc wins over the routes.rb parse (ordered probes).
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        (root / "openapi.json").write_text(json.dumps(
            {"openapi": "3.0.0",
             "paths": {"/ping": {"get": {"tags": ["health"], "summary": "Ping"}}}}))
        ext = D.resolve_extractor("api-surface", root, "bionic", None, pack_name="ruby")
        self.assertIs(ext, D.extract_ruby_api_surface_openapi)
        md, sources = ext(root, "bionic")
        self.assertIn("openapi.json", sources)
        self.assertNotIn("config/routes.rb", sources)
        # Rendered through the SHARED _render_openapi (same header as the Python pack).
        self.assertIn("_Derived from `openapi.json`._", md)
        self.assertIn("| GET | `/ping` | Ping |", md)

    def test_stub_when_no_openapi_and_no_routes(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "Gemfile").write_text('source "x"\n')
        (root / "lib").mkdir()
        (root / "lib" / "thing.rb").write_text("class Thing\nend\n")
        ext = D.resolve_extractor("api-surface", root, "bionic", None, pack_name="ruby")
        md, sources = ext(root, "bionic")
        self.assertIn("no extractor", md)
        self.assertEqual(sources, {})


# ──────────────────────────── data-model (schema) ──────────────────────────

class DataModelTests(unittest.TestCase):
    def setUp(self):
        self.md, self.sources = D.extract_ruby_data_model(FIXTURE, "bionic")

    def test_entity_table_and_source(self):
        self.assertIn("## Entities (3 tables)", self.md)
        self.assertIn("db/schema.rb", self.sources)

    def test_columns_sorted_and_typed(self):
        # Sorted by (table, column): comments < posts < users.
        c_body = self.md.index("| comments | `body` |")
        p_author = self.md.index("| posts | `author_id` |")
        u_email = self.md.index("| users | `email` |")
        self.assertLess(c_body, p_author)
        self.assertLess(p_author, u_email)
        # null:false → "no"; absent null → "—".
        self.assertRegex(self.md, r"\| users \| `email` \| string \| no \| — \| — \|")

    def test_fk_explicit_column_and_inferred(self):
        # Explicit column: author_id → users.
        self.assertRegex(self.md, r"\| posts \| `author_id` \| integer \| — \| — \| users \|")
        # Inferred user_id (from add_foreign_key "posts","users") → users.
        self.assertRegex(self.md, r"\| posts \| `user_id` \| integer \| no \| — \| users \|")
        # Inferred post_id → posts.
        self.assertRegex(self.md, r"\| comments \| `post_id` \| integer \| no \| — \| posts \|")
        # The inferred case is surfaced as a named residual.
        self.assertIn("infers the referencing column", self.md)

    def test_index_note_beneath_table(self):
        self.assertIn("## Indexes", self.md)
        self.assertIn("`index_users_on_email` (email, unique)", self.md)
        self.assertIn("`index_posts_on_user_id` (user_id)", self.md)

    def test_escaping_pipe_in_default(self):
        # A column default containing `|` must render escaped, not break the table.
        self.assertIn("a\\|b", self.md)
        self.assertNotIn("| a|b |", self.md)

    def test_the_schema_dump_is_declared_as_the_artifact_it_is(self):
        """U-R2. `db/schema.rb` is EMITTED, so a pattern over it is admissible
        and clause 7 can ask whether a migration outran it."""
        ic = D.input_classes("ruby")["data-model"]
        self.assertEqual(ic.kind, "committed-artifact")
        self.assertEqual(ic.artifact, ("db/schema.rb",))
        self.assertEqual(ic.refresh, "bin/rails db:schema:dump")
        self.assertIn("db/migrate/**/*.rb", ic.globs)

    def test_the_declared_input_set_names_the_migrations_it_is_dumped_from(self):
        """A migration is inside the concern's declared input set, so editing
        one is not an ADR-0096 postcondition (e) edit — it is a source of this
        concern, and the artifact beside it is what may have gone stale."""
        globs = D.declared_input_globs("ruby")
        self.assertIn("db/migrate/**/*.rb", globs)

    def _structure_sql_only(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "Gemfile").write_text('source "x"\n')
        (root / "db").mkdir()
        (root / "db" / "structure.sql").write_text("CREATE TABLE users (id int);\n")
        (root / "bionic").mkdir()
        return root

    def test_structure_sql_only_is_precondition_missing_not_no_entities(self):
        """U-R3. The probe that used to answer here was worse than a stub.

        It rendered one sentence, hashed the file and returned ZERO entities, so
        `core.concern_verdict` computed `no_entities` — "the declared input was
        consumed successfully and declares none of the concern's entities". That
        is a false claim about a repository's schema: the input was never read.
        `precondition_missing` is the true one, and the remedy is a command."""
        root = self._structure_sql_only()
        records: list = []
        D._build(root, "bionic", report=records)
        rec = next(r for r in records if r["concern"] == "data-model")
        self.assertEqual(rec["verdict"], "stubbed")
        self.assertEqual(rec["stub_reason"], "precondition_missing")

    def test_the_recorded_stub_line_names_the_artifact_and_no_command(self):
        """The RECORDED channel describes the input, not the remedy.

        `expected` used to spell `bin/rails db:schema:dump` inline as well, and
        clause 9 then composed that same `expected` with the pack's `refresh`
        into the `precondition_missing` remediation — so one reader-facing line
        named the command twice. `refresh` is the single declared source of
        command text, so the duplicate left `expected`, and this half asserts
        the recorded line kept the input description and lost the command."""
        root = self._structure_sql_only()
        tree = D._build(root, "bionic")
        line = next(ln for ln in tree["data-model.md"].splitlines()
                    if ln.startswith("> _stub:"))
        self.assertIn("precondition_missing", line)
        self.assertIn("db/schema.rb", line)
        self.assertIn("db/structure.sql", line)
        self.assertNotIn("db:schema:dump", line)

    def test_the_reported_remediation_names_the_command_exactly_once(self):
        """The other half: the command is still told to the reader, once.

        Trimming `expected` must not silently drop the remedy — it moves it to
        the reported channel, where `InputClass.refresh` supplies it alone."""
        root = self._structure_sql_only()
        records: list = []
        D._build(root, "bionic", report=records)
        reported = CORE.reported_coverage(records, D.input_classes("ruby"))
        rec = next(r for r in reported if r["concern"] == "data-model")
        line = rec["remediation"]
        self.assertEqual(line.count("bin/rails db:schema:dump"), 1, line)
        self.assertIn("db/schema.rb", line)

    def test_structure_sql_is_no_longer_a_declared_input_of_this_concern(self):
        """`declared_sources` should say what the concern consumes, and it does
        not consume this file. Leaving the glob in would claim otherwise."""
        globs = D.input_classes("ruby")["data-model"].globs
        self.assertNotIn("db/structure.sql", globs)
        self.assertNotIn("**/db/structure.sql", globs)

    def test_the_pretend_reader_is_gone(self):
        for name in ("extract_ruby_structure_sql_stub", "_detect_ruby_structure_sql"):
            self.assertFalse(hasattr(RUBY, name), f"{name} survived U-R3")
        self.assertEqual(
            [p.extract.__name__ for p in RUBY.probes()["data-model"]],
            ["extract_ruby_data_model"])


# ─────────────────────────────── module-graph ──────────────────────────────

class ModuleGraphTests(unittest.TestCase):
    def setUp(self):
        self.md, self.sources = D.extract_ruby_module_graph(FIXTURE, "bionic")

    def test_resolved_edges(self):
        self.assertIn('User["User"] --> Account["Account"]', self.md)
        self.assertIn('UsersController["UsersController"] --> User["User"]', self.md)

    def test_resolve_or_drop_gem_constant(self):
        # Widget + ApplicationRecord are gem constants with no in-repo file → no edge.
        self.assertNotIn("Widget", self.md)
        self.assertNotIn("ApplicationRecord", self.md)

    def test_comment_and_string_refs_dropped(self):
        # `Isolated` appears only in a comment, `Thing` only in a string, in
        # user.rb — the scrub must leave both edge-less (they stay isolated).
        self.assertNotIn("--> Isolated", self.md)
        self.assertNotIn("User\"] --> Thing", self.md)
        i = self.md.index("## Isolated modules")
        self.assertIn("`Isolated`, `Thing`", self.md[i:])

    def test_sanitized_node_id_separate_from_label(self):
        # `Admin::Setting`: node id sanitized to `Admin__Setting`, label preserved.
        self.assertIn('Admin__Setting["Admin::Setting"]', self.md)

    def test_collision_shadowing_determinism(self):
        # app/models/thing.rb and lib/thing.rb both → `Thing`; app/* wins by root
        # precedence, lib/thing.rb is recorded as shadowed.
        self.assertIn("## Shadowed constants", self.md)
        self.assertIn("shadowed file `lib/thing.rb`", self.md)
        # The winning Thing node's isolated/edge status is app's, not lib's.
        self.assertIn("`Isolated`, `Thing`", self.md)

    def test_every_rb_hashed_including_isolated_and_shadowed(self):
        for rel in ("app/models/user.rb", "app/models/account.rb",
                    "app/models/admin/setting.rb", "app/models/thing.rb",
                    "app/controllers/users_controller.rb",
                    "lib/isolated.rb", "lib/thing.rb"):
            self.assertIn(rel, self.sources)

    def test_node_id_collision_suffix_is_deterministic(self):
        # The defensive sanitized-id collision suffix (ordered by constant). Both
        # sanitize to `A_b`; ':' (0x3a) sorts before '_' (0x5f), so 'A:b' is first
        # and keeps the bare id; 'A_b' gets the deterministic `_2` suffix.
        ids = D._node_ids(["A_b", "A:b"])
        self.assertEqual(ids["A:b"], "A_b")
        self.assertEqual(ids["A_b"], "A_b_2")

    def test_non_literal_autoload_emits_residual(self):
        # A non-string-literal autoload_paths assignment must actively emit the
        # residual note, not silently default (ADR-0067 point 3).
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        (root / "config" / "application.rb").write_text(
            "module Railsapp\n  class Application < Rails::Application\n"
            "    config.autoload_paths << Rails.root.join(\"app/services\")\n"
            "  end\nend\n")
        md, _ = D.extract_ruby_module_graph(root, "bionic")
        self.assertIn("not a string literal", md)
        self.assertIn("may be uncovered", md)

    def test_env_file_nonliteral_autoload_emits_residual(self):
        # ADR-0067 point 3: a non-literal autoload assignment in an ENVIRONMENT
        # file (not just application.rb) must also emit the residual, not silently
        # default. Regression for the review's SHOULD-FIX #1.
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        envdir = root / "config" / "environments"
        envdir.mkdir(parents=True, exist_ok=True)
        (envdir / "production.rb").write_text(
            "Rails.application.configure do\n"
            "  config.eager_load_paths << Rails.root.join(\"app/services\")\n"
            "end\n")
        md, _ = D.extract_ruby_module_graph(root, "bionic")
        self.assertIn("not a string literal", md)

    def test_isolated_list_escapes_injecting_filename(self):
        # Regression (security MUST-FIX): a hostile filename with a newline or a
        # backtick camelizes to a node constant carrying that byte; the isolated
        # list must escape it (via _cell) so it cannot inject a Markdown heading
        # or break the code span in the derived module-graph.md.
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        # A filename with an embedded newline + backtick (only "/" and NUL are
        # forbidden in a POSIX filename); it is isolated (referenced by nothing).
        (root / "lib" / "evil\n## Injected `heading.rb").write_text("class X\nend\n")
        md, _ = D.extract_ruby_module_graph(root, "bionic")
        # The newline is neutralized to a space, so no injected heading line:
        self.assertNotIn("\n## Injected", md)
        # And the backtick is neutralized so the code span cannot be broken:
        self.assertNotIn("`heading", md)

    def test_graph_edge_cap_truncates_with_residual(self):
        # ADR-0067 point 8 DoS guard: the rendered edge set is bounded; excess
        # edges are truncated deterministically with a residual note.
        import unittest.mock
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        with unittest.mock.patch.object(RUBY, "_MAX_GRAPH_EDGES", 1):
            md, _ = D.extract_ruby_module_graph(root, "bionic")
        self.assertIn("module graph truncated", md)
        self.assertIn("1-edge render bound", md)
        self.assertEqual(md.count(" --> "), 1)           # exactly the capped count.


# ─────────────────────────── safety (point 8) ──────────────────────────────

class SafetyTests(unittest.TestCase):
    def test_oversize_file_skipped_and_residual_recorded(self):
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        big = root / "lib" / "huge.rb"
        big.write_text("class Huge\nend\n# " + ("x" * (2 * 1024 * 1024 + 10)) + "\n")
        src = D.detect_ruby_sources(root)
        self.assertIn("lib/huge.rb", src.oversize)
        rels = [p.relative_to(root).as_posix() for _, _, p in src.entries]
        self.assertNotIn("lib/huge.rb", rels)            # not a node.
        md, sources = D.extract_ruby_module_graph(root, "bionic")
        self.assertNotIn("lib/huge.rb", sources)         # not hashed.
        self.assertIn("exceeding the 2 MB bound", md)    # residual recorded.

    def test_symlink_escaping_root_is_skipped(self):
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        outside = Path(tmp.name) / "outside_secret.rb"
        outside.write_text("class OutsideSecret\nend\n")
        link = root / "lib" / "evil.rb"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        md, sources = D.extract_ruby_module_graph(root, "bionic")
        self.assertNotIn("OutsideSecret", md)            # escaping target not read.
        self.assertNotIn("lib/evil.rb", sources)         # nor hashed.


# ──────────────────────── drift + determinism ──────────────────────────────

class DriftAndDeterminismTests(unittest.TestCase):
    def _spine(self, root: Path) -> dict:
        return D._build(root, "bionic")

    def test_deterministic_rebuild_byte_identical(self):
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(self._spine(root), self._spine(root))

    def test_derive_then_dry_run_clean(self):
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        D.derive(root, "bionic")
        self.assertEqual(D.dry_run(root, "bionic"), [], "dry_run dirty after derive")

    def test_routes_mutation_drifts(self):
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        D.derive(root, "bionic")
        routes = root / "config" / "routes.rb"
        routes.write_text(routes.read_text().replace('"/login"', '"/signin"'))
        self.assertIn("bionic/arch/api-surface.md", D.dry_run(root, "bionic"))

    def test_schema_mutation_drifts(self):
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        D.derive(root, "bionic")
        schema = root / "db" / "schema.rb"
        schema.write_text(schema.read_text().replace(
            't.string "name", default: "anon"', 't.string "nickname", default: "anon"'))
        self.assertIn("bionic/arch/data-model.md", D.dry_run(root, "bionic"))

    def test_rb_mutation_drifts(self):
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        D.derive(root, "bionic")
        user = root / "app" / "models" / "user.rb"
        # Turn the dropped gem reference `Widget` into the in-repo `Thing` — a
        # new resolved edge, so module-graph.md itself changes (not just its hash).
        user.write_text(user.read_text().replace("Widget", "Thing"))
        self.assertIn("bionic/arch/module-graph.md", D.dry_run(root, "bionic"))


# ───────── ADR-0067 point 6: the shared renderer is a pure extraction ───────

class PythonApiSurfaceUnchangedTests(unittest.TestCase):
    def test_python_api_surface_output_unchanged(self):
        # The _render_openapi extraction must not change the Python pack's output.
        md, sources = D.extract_python_api_surface(PYFIXTURE, "bionic")
        self.assertIn("_Derived from `openapi.json`._", md)
        self.assertIn("## users (4)", md)
        self.assertIn("| DELETE | `/users/{id}` | Delete user |", md)
        self.assertIn("| GET | `/health` | Health check |", md)
        self.assertIn("openapi.json", sources)

    def test_render_openapi_returns_none_when_no_ops(self):
        self.assertIsNone(D._render_openapi({"paths": {}}, "_x_"))


_ABSENT = object()   # the caller's "no `only:` key at all" — never a helper return.


class ActionFilterTriStateTests(unittest.TestCase):
    """D-1 — an `only:`/`except:` this reader cannot read is not "no filter".

    `_rb_symbol_set` returned a `set` for three different facts: the filter is
    `{index, show}`, the filter is empty, and the filter could not be read. The
    third collapsing into the second is the empty-set-means-permissive defect
    that produced 144 phantom routes on `rubygems-org`, and it survived because
    a permissive answer looks like a complete one.

    The helper is now tri-state. `None` means "not read", and the expander
    renders the full action set AND names a residual, so an over-claim is
    reported in the channel that exists for it. `set()` means what Rails means
    by `only: []` — ZERO REST actions — and the expander renders no rows for it,
    which removed 37 phantom rows (and the 11 duplicates they caused) from the
    `rubygems-org` golden. An ABSENT `only:` is the fourth fact, and the caller
    keeps it distinct by testing key presence before calling the helper.
    """

    def render(self, body: str) -> str:
        tmp, md, _ = _routes_md(body)
        self.addCleanup(tmp.cleanup)
        return md

    _RESIDUAL = "could not be read"

    def test_a_constant_only_list_is_named_rather_than_silently_permissive(self):
        """`only: ALLOWED` where `ALLOWED = %i[index show]`.

        Eight REST rows render where the constant declares two — six phantom
        routes from one line. The rows are unchanged (this reader cannot know
        the constant's value), but the over-claim is now NAMED.
        """
        md = self.render("  resources :books, only: ALLOWED\n")
        self.assertIn("| GET | `/books` | `books#index` |", md)
        self.assertIn("| DELETE | `/books/:id` | `books#destroy` |", md)
        self.assertIn(self._RESIDUAL, md,
                      "an unreadable `only:` rendered every action silently")

    def test_a_splat_element_makes_the_whole_list_unreadable(self):
        """`only: [:index, *EXTRA]` — the opposite direction, same root cause.

        The set comprehension dropped every element `_rb_symbol` could not read
        and returned `{index}`, a PARTIAL set indistinguishable from a complete
        one, so exactly one row rendered where the declaration admits more. A
        list with an unreadable element is now unreadable as a whole.
        """
        md = self.render("  resources :books, only: [:index, *EXTRA]\n")
        self.assertIn("| GET | `/books` | `books#index` |", md)
        self.assertIn("| POST | `/books` | `books#create` |", md,
                      "a partial filter was applied as if it were complete")
        self.assertIn(self._RESIDUAL, md)

    def test_an_unreadable_except_list_is_named_too(self):
        md = self.render("  resources :books, except: DENIED\n")
        self.assertIn("| GET | `/books` | `books#index` |", md)
        self.assertIn(self._RESIDUAL, md)

    def test_a_genuinely_empty_literal_declares_zero_rest_actions(self):
        """`only: []` is READ, it reads as empty, and empty means ZERO.

        This is the corpus shape — `rubygems-org` carries five of them, and
        reading empty as "no filter" put 37 phantom rows into its blessed
        golden. What must ALSO not happen is this case acquiring the
        unreadable-filter residual: that would make the residual meaningless by
        firing on the one shape that IS read.
        """
        md = self.render("  resources :books, only: []\n")
        self.assertNotIn("books#index", md)
        self.assertNotIn("books#destroy", md)
        self.assertNotIn(self._RESIDUAL, md)

    def test_an_empty_only_still_renders_the_block_routes_it_encloses(self):
        """`resources :users, only: [] do get 'avatar', on: :member end`.

        Rails renders exactly one route for this, and the golden rendered
        eight. The filter governs the REST expansion; the block is walked
        independently, so its member route survives.
        """
        md = self.render("""
  resources :users, only: [] do
    get 'avatar', on: :member
  end
""")
        self.assertIn("| GET | `/users/:id/avatar` | `users#avatar` |", md)
        self.assertNotIn("users#index", md)
        self.assertNotIn("users#destroy", md)

    def test_an_empty_except_excludes_nothing(self):
        """`except: []` is the mirror: an empty exclusion list excludes no
        action, so every REST row still renders. The two empties are not
        symmetric, and testing `is not None` on both is what keeps them apart.
        """
        md = self.render("  resources :books, except: []\n")
        self.assertIn("| GET | `/books` | `books#index` |", md)
        self.assertIn("| DELETE | `/books/:id` | `books#destroy` |", md)
        self.assertNotIn(self._RESIDUAL, md)

    def test_an_absent_filter_renders_every_action_and_names_nothing(self):
        """The fourth fact. No `only:` at all is "no filter", which must not
        collapse into either `only: []` (zero rows) or an unread filter (a
        residual)."""
        md = self.render("  resources :books\n")
        self.assertIn("| GET | `/books` | `books#index` |", md)
        self.assertIn("| DELETE | `/books/:id` | `books#destroy` |", md)
        self.assertNotIn(self._RESIDUAL, md)

    def test_a_readable_filter_still_filters_and_names_nothing(self):
        md = self.render("  resources :books, only: %i[index show]\n")
        self.assertIn("| GET | `/books` | `books#index` |", md)
        self.assertNotIn("books#destroy", md)
        self.assertNotIn(self._RESIDUAL, md)

    def test_the_helper_reports_the_three_states_apart(self):
        # The unit-level statement of the same fact, independent of rendering.
        # `found[name] is _ABSENT` is the caller's fourth fact, kept distinct by
        # testing key presence rather than by anything the helper returns.
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        (root / "config" / "routes.rb").write_text(
            "Rails.application.routes.draw do\n"
            "  resources :a, only: []\n"
            "  resources :b, only: %i[index show]\n"
            "  resources :c, only: ALLOWED\n"
            "  resources :d\n"
            "end\n")
        raw = (root / "config" / "routes.rb").read_bytes()
        tree = RUBY._ruby_ts_parse(raw)
        found = {}
        stack = [tree.root_node]
        while stack:
            node = stack.pop()
            if node.type == "call":
                ident = node.child_by_field_name("method")
                args = node.child_by_field_name("arguments")
                if ident is not None and RUBY._ts_text(ident, raw) == "resources":
                    kids = [c for c in args.children if c.is_named] if args else []
                    name = RUBY._rb_symbol(kids[0], raw) if kids else None
                    opts = RUBY._rb_keywords(kids, raw)
                    found[name] = (RUBY._rb_symbol_set(opts["only"], raw)
                                   if "only" in opts else _ABSENT)
            stack.extend(node.children)
        self.assertEqual(found["a"], set(), "an empty literal must read as empty")
        self.assertEqual(found["b"], {"index", "show"})
        self.assertIsNone(found["c"], "an unreadable value node must read as None")
        self.assertIs(found["d"], _ABSENT, "an absent `only:` is not an empty one")


if __name__ == "__main__":
    unittest.main(verbosity=2)
