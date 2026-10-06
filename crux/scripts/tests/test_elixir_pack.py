"""Tests for the Elixir/Phoenix arch stack pack (ADR-0069, dev module of PB-0067).

Covers the three Elixir concern extractors (api-surface, data-model,
module-graph), their detection + resolution through the ADR-0066 seam, the
committed-OpenAPI-before-router ordering, the Phoenix router parse (resources
REST + only/except + nested resources + scope prefix + the LIVE token), the Ecto
data-model (schema aggregation, implicit PK, belongs_to FK, the fixed null=`—`
residual, the has_many Relations note, Ecto.Enum, escaping), the module graph
(per-file alias-as + grouped resolve, resolve-or-drop of an external ref, an
in-repo edge), the FAIL-CLOSED comment/sigil scrubber (an unbalanced sigil file
is hashed + a residual, never a crash or truncation), the migrations presence
stub, drift-gating, and byte-stable re-derivation.

Stdlib only. The committed `fixtures/phoenixapp/` fixture is copied to a tempdir
before any mutation, so it is never altered.
"""

from __future__ import annotations

import importlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]      # crux/scripts
sys.path.insert(0, str(SCRIPTS))

D = importlib.import_module("crux.arch.derive")
CORE = importlib.import_module("crux.arch.core")

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "phoenixapp"


def _copy(fixture: Path = FIXTURE):
    tmp = tempfile.TemporaryDirectory()
    dest = Path(tmp.name) / "repo"
    shutil.copytree(fixture, dest)
    (dest / "bionic").mkdir()
    return tmp, dest


def _bare_elixir(tmp_root: Path):
    """An elixir repo (mix.exs marker) the caller fills with .ex files."""
    (tmp_root / "mix.exs").write_text("defmodule X.MixProject do\n  use Mix.Project\nend\n")
    return tmp_root


def _row_cells(line: str) -> list:
    return [c.strip(" `") for c in line.strip("|").split("|")]


# ───────────────────────── detection + resolution ──────────────────────────

class DetectionAndResolutionTests(unittest.TestCase):
    def test_detects_elixir_stack(self):
        # mix.exs present, no crux/python/ruby/node markers → auto-detects elixir.
        self.assertEqual(D.detect_stack(FIXTURE, None), "elixir")

    def test_resolution_picks_real_extractors(self):
        # No openapi.json → api-surface resolves to the router probe (2nd).
        self.assertIs(
            D.resolve_extractor("api-surface", FIXTURE, "bionic", None, pack_name="elixir"),
            D.extract_elixir_api_surface_router,
        )
        # schema "…" present → data-model resolves to the schema probe (1st).
        self.assertIs(
            D.resolve_extractor("data-model", FIXTURE, "bionic", None, pack_name="elixir"),
            D.extract_elixir_data_model,
        )
        self.assertIs(
            D.resolve_extractor("module-graph", FIXTURE, "bionic", None, pack_name="elixir"),
            D.extract_elixir_module_graph,
        )

    def test_decision_index_still_universal(self):
        ext = D.resolve_extractor("decision-index", FIXTURE, "bionic", None, pack_name="elixir")
        self.assertTrue(callable(ext))


# ─────────────────────── api-surface ordering (OpenAPI) ─────────────────────

class ApiSurfaceOrderingTests(unittest.TestCase):
    def test_openapi_probe_precedes_router(self):
        # A committed OpenAPI doc wins over the router.ex parse (ordered probes).
        import json
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        (root / "openapi.json").write_text(json.dumps(
            {"openapi": "3.0.0",
             "paths": {"/ping": {"get": {"tags": ["health"], "summary": "Ping"}}}}))
        ext = D.resolve_extractor("api-surface", root, "bionic", None, pack_name="elixir")
        self.assertIs(ext, D.extract_elixir_api_surface_openapi)
        md, sources = ext(root, "bionic")
        self.assertIn("openapi.json", sources)
        self.assertNotIn("lib/myapp_web/router.ex", sources)
        # Rendered through the SHARED _render_openapi (same header as other packs).
        self.assertIn("_Derived from `openapi.json`._", md)
        self.assertIn("| GET | `/ping` | Ping |", md)


# ─────────────────────────── api-surface (router) ──────────────────────────

class RouterTests(unittest.TestCase):
    def setUp(self):
        self.md, self.sources = D.extract_elixir_api_surface_router(FIXTURE, "bionic")

    def test_static_parse_label_and_source(self):
        self.assertIn("static parse of the phoenix router", self.md.lower())
        self.assertIn("lib/myapp_web/router.ex", self.sources)

    def test_resources_rest_expansion(self):
        self.assertIn("| GET | `/users` | `MyAppWeb.UserController#index` |", self.md)
        self.assertIn("| POST | `/users` | `MyAppWeb.UserController#create` |", self.md)
        self.assertIn("| GET | `/users/:id` | `MyAppWeb.UserController#show` |", self.md)
        self.assertIn("| PATCH | `/users/:id` | `MyAppWeb.UserController#update` |", self.md)
        self.assertIn("| PUT | `/users/:id` | `MyAppWeb.UserController#update` |", self.md)
        self.assertIn("| DELETE | `/users/:id` | `MyAppWeb.UserController#delete` |", self.md)

    def test_nested_resources_expansion(self):
        # resources "/users" do resources "/posts" end → /users/:user_id/posts.
        self.assertIn("| GET | `/users/:user_id/posts` | `MyAppWeb.PostController#index` |", self.md)
        self.assertIn("| GET | `/users/:user_id/posts/:id` | `MyAppWeb.PostController#show` |", self.md)

    def test_scope_prefix_and_alias(self):
        # scope "/api", MyAppWeb.Api → path prefix + alias namespace.
        self.assertIn("| GET | `/api/widgets` | `MyAppWeb.Api.WidgetController#index` |", self.md)

    def test_live_token(self):
        self.assertIn("| LIVE | `/dashboard` | `MyAppWeb.DashboardLive#index` |", self.md)

    def test_only_option_restricts_actions(self):
        # widgets only: [:index, :show] → no create/delete/update rows.
        self.assertIn("MyAppWeb.Api.WidgetController#show", self.md)
        self.assertNotIn("WidgetController#create", self.md)
        self.assertNotIn("WidgetController#delete", self.md)
        self.assertNotIn("WidgetController#update", self.md)

    def test_except_option_drops_action(self):
        # gadgets except: [:delete] → every action but delete.
        self.assertNotIn("GadgetController#delete", self.md)
        self.assertIn("GadgetController#update", self.md)
        self.assertIn("GadgetController#index", self.md)

    def test_commented_route_not_parsed(self):
        self.assertNotIn("GhostController", self.md)

    def test_pipe_through_annotation_note(self):
        self.assertIn("Pipelines", self.md)
        self.assertIn("`:browser`", self.md)
        self.assertIn("`:api`", self.md)

    def test_sorted_by_path_method_controller(self):
        rows = [ln for ln in self.md.splitlines()
                if ln.startswith(("| GET |", "| POST |", "| PUT |",
                                  "| PATCH |", "| DELETE |", "| LIVE |"))]
        keys = []
        for ln in rows:
            parts = _row_cells(ln)
            keys.append((parts[1], parts[0], parts[2]))
        self.assertEqual(keys, sorted(keys))

    def test_under_count_residual(self):
        self.assertIn("named residual", self.md)
        self.assertIn("metaprogrammed", self.md)

    def test_multiple_routers_single_primary(self):
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        # A second router sorts after the first by POSIX path → primary is the
        # first; the second is a named residual, not merged.
        second = root / "lib" / "other_web" / "router.ex"
        second.parent.mkdir(parents=True)
        second.write_text('defmodule OtherWeb.Router do\n  use Phoenix.Router\n'
                          '  get "/other", OtherController, :index\nend\n')
        md, _ = D.extract_elixir_api_surface_router(root, "bionic")
        self.assertIn("Additional Phoenix routers not merged", md)
        self.assertNotIn("OtherController", md)


# ──────────────────────────── data-model (Ecto) ────────────────────────────

class DataModelTests(unittest.TestCase):
    def setUp(self):
        self.md, self.sources = D.extract_elixir_data_model(FIXTURE, "bionic")

    def _rows(self):
        return [_row_cells(ln) for ln in self.md.splitlines()
                if ln.startswith("| ") and "---" not in ln and "table" not in ln]

    def test_schema_aggregation_across_files(self):
        # Two schemas in two files aggregate into one entity table.
        self.assertIn("## Entities (2 tables)", self.md)
        self.assertIn("lib/myapp/accounts/user.ex", self.sources)
        self.assertIn("lib/myapp/accounts/account.ex", self.sources)

    def test_implicit_primary_key(self):
        # accounts has no @primary_key → the implicit `id` PK (null=no).
        self.assertIn("| accounts | `id` | id | no | — | — |", self.md)

    def test_explicit_custom_primary_key(self):
        # user's @primary_key {:id, :binary_id, …} → typed PK, null=no.
        self.assertIn("| users | `id` | binary_id | no | — | — |", self.md)

    def test_belongs_to_fk_column_null_dash(self):
        # belongs_to :account, MyApp.Accounts.Account with @foreign_key_type
        # :binary_id → account_id column, fk = the module, null = — (the residual).
        self.assertIn("| users | `account_id` | binary_id | — | — | MyApp.Accounts.Account |", self.md)

    def test_null_is_dash_for_non_pk_and_residual_stated(self):
        # Every non-PK column renders null=—; the fixed residual states why.
        self.assertIn("| users | `email` | string | — | — | — |", self.md)
        self.assertIn("NOT-NULL constraints and DB indexes are declared in "
                      "`priv/repo/migrations/`", self.md)
        self.assertIn("`null` is `—` for every non-primary-key column", self.md)

    def test_has_many_relations_note(self):
        self.assertIn("## Relations", self.md)
        self.assertIn("`posts` → `MyApp.Content.Post` (has_many)", self.md)
        # …and the association is NOT a column row.
        rows = self._rows()
        self.assertFalse(any(r[0] == "users" and r[1] == "posts" for r in rows))

    def test_ecto_enum_column_and_note(self):
        self.assertIn("| users | `role` | Ecto.Enum |", self.md)
        self.assertIn("## Enums", self.md)
        self.assertIn("`users`.`role`: `member`, `admin`", self.md)

    def test_array_type_and_timestamps(self):
        self.assertIn("| users | `tags` | {:array, :string} | — | — | — |", self.md)
        self.assertIn("| users | `inserted_at` | naive_datetime | — | — | — |", self.md)
        self.assertIn("| users | `updated_at` | naive_datetime | — | — | — |", self.md)

    def test_default_escaping_pipe(self):
        # default "a|b" must render escaped, not break the table.
        self.assertIn("a\\|b", self.md)
        self.assertNotIn("| a|b |", self.md)

    def test_sorted_by_table_then_field(self):
        rows = self._rows()
        keys = [(r[0], r[1]) for r in rows]
        self.assertEqual(keys, sorted(keys))


def _migrations_repo(tmp_root: Path, files: dict) -> Path:
    """A bare elixir repo whose only data-model surface is `priv/repo/migrations`.

    No `.ex` file carries `schema "…"`, so the schema probe does not fire and the
    migration probe — second in the chain — is the one that runs.
    """
    root = _bare_elixir(tmp_root)
    migs = root / "priv" / "repo" / "migrations"
    migs.mkdir(parents=True)
    for name, text in files.items():
        (migs / name).write_text(text)
    return root


#: One migration, written twice. Identical declarations; the only difference is
#: the call spelling — space-delimited here, parenthesized below. The Elixir
#: formatter chooses between them, so a reader that sees two different schemas
#: here is reading the formatter rather than the migration.
_MIG_SPACED = '''defmodule App.Repo.Migrations.CreateUsers do
  use Ecto.Migration

  def change do
    create table(:users) do
      add :email, :string, null: false
      add :age, :integer, default: 0
      add :org_id, references(:orgs)
      timestamps()
    end

    create unique_index(:users, [:email])
  end
end
'''

_MIG_PARENS = '''defmodule App.Repo.Migrations.CreateUsers do
  use Ecto.Migration

  def change do
    create(table(:users) do
      add(:email, :string, null: false)
      add(:age, :integer, default: 0)
      add(:org_id, references(:orgs))
      timestamps()
    end)

    create(unique_index(:users, [:email]))
  end
end
'''


class MigrationParserTests(unittest.TestCase):
    """U-E2: the presence stub becomes an Ecto migration parser.

    The migration probe is SECOND in the data-model chain and fires only where no
    Ecto schema exists, which is why neither corpus Phoenix application reaches
    it — both declare schemas. Its acceptance evidence is therefore fixtures, and
    the corpus's job for this unit is to prove zero goldens moved.
    """

    def _md(self, files: dict):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = _migrations_repo(Path(tmp.name), files)
        ext = D.resolve_extractor("data-model", root, "bionic", None, pack_name="elixir")
        self.assertIs(ext, D.extract_elixir_migrations)
        return ext(root, "bionic")

    def _rows(self, md: str) -> list:
        """The ENTITY table's data rows only.

        Sliced on the section heading rather than on row shape: an index row
        also carries a backticked cell, so a shape filter would sweep it in and
        disagree with `count_concern_entities`, which reads the header.
        """
        section = md.split("## Entities", 1)[-1].split("\n## ", 1)[0]
        return [_row_cells(l) for l in section.split("\n")
                if l.startswith("| ") and "---" not in l
                and not l.startswith("| table | column |")]

    def test_the_probe_is_still_second_in_the_chain(self):
        # An Ecto schema anywhere wins; the migration reader never runs beside it.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = _migrations_repo(Path(tmp.name), {"001_users.exs": _MIG_SPACED})
        (root / "lib").mkdir(exist_ok=True)
        (root / "lib" / "post.ex").write_text(
            'defmodule App.Post do\n  use Ecto.Schema\n  schema "posts" do\n'
            '    field :title, :string\n  end\nend\n')
        self.assertIs(
            D.resolve_extractor("data-model", root, "bionic", None, pack_name="elixir"),
            D.extract_elixir_data_model)

    def test_columns_render_with_the_existing_entity_header(self):
        # R5: a new header spelling counts zero entities and fakes a no_entities
        # verdict over a full table. The header is the data-model one, reused.
        md, _ = self._md({"001_users.exs": _MIG_SPACED})
        self.assertIn("| table | column | type | null | default | fk |", md)
        self.assertEqual(CORE.count_concern_entities("data-model", md),
                         len(self._rows(md)))
        self.assertGreater(CORE.count_concern_entities("data-model", md), 0)

    def test_both_call_spellings_yield_identical_rows(self):
        spaced, _ = self._md({"001_users.exs": _MIG_SPACED})
        parens, _ = self._md({"001_users.exs": _MIG_PARENS})
        self.assertEqual(self._rows(spaced), self._rows(parens))
        self.assertEqual(spaced, parens)

    def test_the_rows_are_the_declarations(self):
        md, _ = self._md({"001_users.exs": _MIG_SPACED})
        rows = self._rows(md)
        by_col = {r[1]: r for r in rows}
        self.assertEqual(sorted(by_col),
                         ["age", "email", "id", "inserted_at", "org_id", "updated_at"])
        # `null: false` is a fact the migration states — the schema reader's
        # fixed `—` residual exists precisely because it is stated only here.
        self.assertEqual(by_col["email"][2:], ["string", "no", "—", "—"])
        self.assertEqual(by_col["age"][2:], ["integer", "yes", "0", "—"])
        # references(:orgs) → an FK column naming the referenced table.
        self.assertEqual(by_col["org_id"][5], "orgs.id")
        # The implicit serial primary key `create table` adds.
        self.assertEqual(by_col["id"][2:4], ["id", "no"])

    def test_indexes_render_outside_the_entity_table(self):
        md, _ = self._md({"001_users.exs": _MIG_SPACED})
        self.assertIn("## Indexes", md)
        self.assertIn("| table | index | columns | unique |", md)
        self.assertIn("`email`", md.split("## Indexes")[1])
        # …and no index row is counted as an entity.
        self.assertEqual(CORE.count_concern_entities("data-model", md),
                         len(self._rows(md)))

    def test_primary_key_false_suppresses_the_implicit_id(self):
        md, _ = self._md({"001_t.exs": (
            'defmodule M do\n  use Ecto.Migration\n  def change do\n'
            '    create table(:things, primary_key: false) do\n'
            '      add :uuid, :binary_id, primary_key: true\n'
            '    end\n  end\nend\n')})
        cols = [r[1] for r in self._rows(md)]
        self.assertEqual(cols, ["uuid"])

    def test_a_later_migration_alters_an_earlier_table(self):
        # Migrations are replayed in filename order — that order IS the schedule
        # Ecto runs them in, so the last state is the current one.
        md, _ = self._md({
            "001_create.exs": (
                'defmodule A do\n  use Ecto.Migration\n  def change do\n'
                '    create table(:users) do\n      add :email, :string\n'
                '    end\n  end\nend\n'),
            "002_alter.exs": (
                'defmodule B do\n  use Ecto.Migration\n  def change do\n'
                '    alter table(:users) do\n      add :nickname, :string\n'
                '      remove :email\n    end\n  end\nend\n'),
        })
        cols = [r[1] for r in self._rows(md)]
        self.assertIn("nickname", cols)
        self.assertNotIn("email", cols)

    def test_a_dropped_table_is_not_rendered(self):
        md, _ = self._md({
            "001_create.exs": (
                'defmodule A do\n  use Ecto.Migration\n  def change do\n'
                '    create table(:temp) do\n      add :x, :string\n'
                '    end\n    create table(:keep) do\n      add :y, :string\n'
                '    end\n  end\nend\n'),
            "002_drop.exs": (
                'defmodule B do\n  use Ecto.Migration\n  def change do\n'
                '    drop table(:temp)\n  end\nend\n'),
        })
        tables = {r[0] for r in self._rows(md)}
        self.assertEqual(tables, {"keep"})

    def test_every_migration_file_is_hashed_so_a_change_drifts(self):
        _, sources = self._md({"001_users.exs": _MIG_SPACED})
        self.assertIn("priv/repo/migrations/001_users.exs", sources)

    def test_a_migration_declaring_no_table_stubs_rather_than_renders_empty(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = _migrations_repo(Path(tmp.name), {"001_noop.exs": "defmodule M do\nend\n"})
        md, sources = D.extract_elixir_migrations(root, "bionic")
        self.assertEqual(CORE.count_concern_entities("data-model", md), 0)
        # The file is still hashed: a migration added later must drift the spine.
        self.assertIn("priv/repo/migrations/001_noop.exs", sources)

    def test_a_down_block_is_not_replayed(self):
        # `mix ecto.migrate` runs `up`, never `down`. A walk that read both would
        # apply the create and then the rollback's drop, and render nothing —
        # while reporting `no_entities` over a repository that has the table.
        md, _ = self._md({"001_u.exs": (
            'defmodule M do\n  use Ecto.Migration\n'
            '  def up do\n    create table(:users) do\n'
            '      add :email, :string\n    end\n  end\n\n'
            '  def down do\n    drop table(:users)\n  end\nend\n')})
        self.assertEqual({r[0] for r in self._rows(md)}, {"users"})

    def test_a_comment_is_not_a_column(self):
        md, _ = self._md({"001_c.exs": (
            'defmodule M do\n  use Ecto.Migration\n  def change do\n'
            '    create table(:t) do\n      add :real, :string\n'
            '      # add :fake, :string\n    end\n  end\nend\n')})
        cols = [r[1] for r in self._rows(md)]
        self.assertIn("real", cols)
        self.assertNotIn("fake", cols)

    def test_deep_nesting_does_not_raise_recursion_error(self):
        # The walk is an explicit stack. A recursive one raises RecursionError,
        # which escapes every fail-closed net in the pack.
        body = "create table(:t) do\n" + "".join(
            f"      add :c{i}, :string\n" for i in range(50)) + "    end\n"
        deep = "defmodule M do\n  use Ecto.Migration\n  def change do\n" \
            + "if true do\n" * 200 + body + "end\n" * 200 + "  end\nend\n"
        md, _ = self._md({"001_deep.exs": deep})
        self.assertIsInstance(md, str)


# ─────────────────────────────── module-graph ──────────────────────────────

class ModuleGraphTests(unittest.TestCase):
    def setUp(self):
        self.md, self.sources = D.extract_elixir_module_graph(FIXTURE, "bionic")
        self.edges = self._edges(self.md)

    @staticmethod
    def _edges(md: str) -> list:
        import re
        out = []
        for ln in md.splitlines():
            m = re.search(r'\["([^"]+)"\]\s*-->\s*\w+\["([^"]+)"\]', ln)
            if m:
                out.append((m.group(1), m.group(2)))
        return out

    def test_grouped_alias_resolves_both(self):
        # alias MyApp.Accounts.{User, Account} → edges to both in-repo modules.
        self.assertIn(("MyApp.Accounts", "MyApp.Accounts.User"), self.edges)
        self.assertIn(("MyApp.Accounts", "MyApp.Accounts.Account"), self.edges)

    def test_alias_as_resolves_to_in_repo(self):
        # alias MyApp.Repo, as: DB + DB.all(User) → an edge to MyApp.Repo.
        self.assertIn(("MyApp.Accounts", "MyApp.Repo"), self.edges)

    def test_in_repo_belongs_to_edge(self):
        # user.ex belongs_to MyApp.Accounts.Account (in-repo) → resolved edge.
        self.assertIn(("MyApp.Accounts.User", "MyApp.Accounts.Account"), self.edges)

    def test_resolve_or_drop_external(self):
        # MyApp.External.Service + MyApp.Content.Post have no in-repo file → drop.
        self.assertNotIn("MyApp.External.Service", self.md)
        self.assertNotIn("MyApp.Content.Post", self.md)
        self.assertFalse(any("External" in a or "External" in b for a, b in self.edges))

    def test_string_only_reference_stays_isolated(self):
        # MyApp.Isolated is named only in a doc string → no edge; it stays
        # isolated (the string scrub must leave it edge-less).
        self.assertFalse(any(b == "MyApp.Isolated" for _, b in self.edges))
        i = self.md.index("## Isolated modules")
        self.assertIn("`MyApp.Isolated`", self.md[i:])

    def test_bailed_file_hashed_and_residual(self):
        # The unbalanced-sigil file: hashed (drift-gated) + a named residual,
        # NEVER a crash or a silently truncated parse.
        self.assertIn("lib/myapp/broken.ex", self.sources)
        self.assertIn("scrub bailed", self.md)
        self.assertIn("lib/myapp/broken.ex", self.md)
        # Its module is not indexed (not parsed), so no MyApp.Broken node.
        self.assertNotIn("MyApp.Broken", self.md)

    def test_every_ex_hashed_including_isolated_and_bailed(self):
        for rel in ("lib/myapp/accounts.ex", "lib/myapp/accounts/user.ex",
                    "lib/myapp/accounts/account.ex", "lib/myapp/repo.ex",
                    "lib/myapp/isolated.ex", "lib/myapp/broken.ex",
                    "lib/myapp_web/router.ex"):
            self.assertIn(rel, self.sources)

    def test_exs_excluded_from_graph(self):
        # mix.exs / migration .exs are .exs, excluded from the .ex-only graph.
        self.assertNotIn("MyApp.MixProject", self.md)


# ─────────────── fail-closed comment/sigil scrubber (point 4) ───────────────

class ScrubberTests(unittest.TestCase):
    def test_bails_on_unbalanced_sigil(self):
        with self.assertRaises(D._ElixirScrubBail):
            D._scrub_elixir_comments('def f do\n  ~r/never closes\nend\n')

    def test_bails_on_unterminated_heredoc(self):
        with self.assertRaises(D._ElixirScrubBail):
            D._scrub_elixir_comments('@moduledoc """\nunclosed heredoc\n')

    def test_comment_stripped_string_preserved(self):
        s = D._scrub_elixir_comments('x = "a # b"  # trailing comment\nkeep')
        self.assertIn('"a # b"', s)         # `#` inside the string preserved.
        self.assertNotIn("trailing comment", s)
        self.assertIn("keep", s)

    def test_interpolation_and_char_literal_guarded(self):
        # `#{}` interpolation and the `?#` char literal must not read as comments.
        s = D._scrub_elixir_comments('y = "v=#{a}#{b}"\nc = ?#\nkeep')
        self.assertIn("#{a}", s)
        self.assertIn("?#", s)
        self.assertIn("keep", s)

    def test_function_predicate_question_not_char_literal(self):
        # `valid?` is a function name — the `?` must NOT swallow the next token.
        s = D._scrub_elixir_comments('def valid?(x), do: x\n')
        self.assertIn("valid?(x)", s)

    def test_blank_strings_mode_blanks_interior(self):
        s = D._scrub_elixir_comments('a = "MyApp.Ghost"\n', blank_strings=True)
        self.assertNotIn("MyApp.Ghost", s)
        self.assertIn("\n", s)              # newline preserved.

    def test_well_formed_sigil_does_not_bail(self):
        s = D._scrub_elixir_comments('a = ~r/ab+c/\nb = ~w(x y z)a\nkeep')
        self.assertIn("keep", s)


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

    def test_router_mutation_drifts(self):
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        D.derive(root, "bionic")
        router = root / "lib" / "myapp_web" / "router.ex"
        router.write_text(router.read_text().replace('"/contact"', '"/reach"'))
        self.assertIn("bionic/arch/api-surface.md", D.dry_run(root, "bionic"))

    def test_schema_mutation_drifts(self):
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        D.derive(root, "bionic")
        user = root / "lib" / "myapp" / "accounts" / "user.ex"
        user.write_text(user.read_text().replace("field :email", "field :handle"))
        self.assertIn("bionic/arch/data-model.md", D.dry_run(root, "bionic"))

    def test_ex_addition_drifts_module_graph(self):
        # A new .ex referencing an in-repo module adds a resolved edge, so
        # module-graph.md itself changes (not just its source hash).
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        D.derive(root, "bionic")
        extra = root / "lib" / "myapp" / "extra.ex"
        extra.write_text("defmodule MyApp.Extra do\n  alias MyApp.Accounts\n"
                         "  def go, do: Accounts.list_users()\nend\n")
        self.assertIn("bionic/arch/module-graph.md", D.dry_run(root, "bionic"))


# ─────────── ADR-0096 clause 1: the router read through a parse tree ─────────

#: The Phoenix entrypoint, `lib/<app>_web.ex`. Its `router/0` macro body carries
#: `use Phoenix.Router` inside a `quote do`, and its POSIX path sorts AHEAD of
#: `lib/<app>_web/router.ex` because `.` is 0x2E and `/` is 0x2F. Both corpus
#: Phoenix applications have exactly this file.
_WEB_ENTRYPOINT = '''defmodule MyAppWeb do
  def router do
    quote do
      use Phoenix.Router, helpers: false
      import Plug.Conn
    end
  end

  def controller do
    quote do
      use Phoenix.Controller, namespace: MyAppWeb
    end
  end
end
'''

#: The same six declarations in the two spellings the DSL admits. A project's
#: formatter chooses between them and the choice is not the project's HTTP
#: surface, so the reader must not see a difference.
_ROUTES_SPACE_DELIMITED = '''defmodule MyAppWeb.Router do
  use Phoenix.Router

  pipeline :browser do
    plug :accepts, ["html"]
  end

  scope "/", MyAppWeb do
    pipe_through :browser
    get "/", PageController, :index
    post "/contact", PageController, :contact
    live "/dashboard", DashboardLive, :index
    resources "/widgets", WidgetController, only: [:index, :show]
    forward "/jobs", ObanWeb
  end
end
'''

_ROUTES_PARENTHESIZED = '''defmodule MyAppWeb.Router do
  use(Phoenix.Router)

  pipeline :browser do
    plug(:accepts, ["html"])
  end

  scope "/", MyAppWeb do
    pipe_through(:browser)
    get("/", PageController, :index)
    post("/contact", PageController, :contact)
    live("/dashboard", DashboardLive, :index)
    resources("/widgets", WidgetController, only: [:index, :show])
    forward("/jobs", ObanWeb)
  end
end
'''


def _phoenix_repo(router_source: str, *, with_entrypoint: bool = True):
    """A minimal Phoenix layout: `mix.exs`, the entrypoint, and the router."""
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name) / "repo"
    (root / "lib" / "myapp_web").mkdir(parents=True)
    _bare_elixir(root)
    if with_entrypoint:
        (root / "lib" / "myapp_web.ex").write_text(_WEB_ENTRYPOINT)
    (root / "lib" / "myapp_web" / "router.ex").write_text(router_source)
    return tmp, root


def _route_rows(md: str) -> list:
    return [_row_cells(ln) for ln in md.splitlines()
            if ln.startswith(("| GET |", "| POST |", "| PUT |", "| PATCH |",
                              "| DELETE |", "| OPTIONS |", "| HEAD |", "| LIVE |"))]


class RouterSelectionTests(unittest.TestCase):
    """ADR-0096 clause 1: which file is the router is a parse-tree question.

    The baseline measured the defect on both corpus Phoenix applications. The
    router collector matched any file whose text contained `use Phoenix.Router`,
    which includes the `lib/<app>_web.ex` entrypoint — where that call sits
    inside `def router do quote do … end end` and declares nothing. The
    entrypoint's path sorts first, so the single-primary rule selected a file
    with no routes in it and the api-surface concern stubbed.

    The predicate is module-body depth: the `use` call's parent is a `do_block`
    whose own parent is a `defmodule` call. Nothing else changes — the
    `**/router.ex` glob and the POSIX-sorted single-primary rule are kept, and
    they stop being wrong once the entrypoint is no longer a candidate.
    """

    def test_the_web_entrypoint_is_not_a_router_candidate(self):
        tmp, root = _phoenix_repo(_ROUTES_SPACE_DELIMITED)
        self.addCleanup(tmp.cleanup)
        files = [str(p) for p in D._elixir_router_files(root)]
        self.assertEqual(len(files), 1, f"router candidates: {files}")
        self.assertTrue(files[0].endswith("lib/myapp_web/router.ex"))

    def test_the_real_router_is_primary_and_its_routes_render(self):
        tmp, root = _phoenix_repo(_ROUTES_SPACE_DELIMITED)
        self.addCleanup(tmp.cleanup)
        md, sources = D.extract_elixir_api_surface_router(root, "bionic")
        self.assertIn("lib/myapp_web/router.ex", sources)
        self.assertNotIn("lib/myapp_web.ex", sources)
        self.assertIn("| GET | `/` | `MyAppWeb.PageController#index` |", md)
        self.assertNotIn("Additional Phoenix routers not merged", md)

    def test_a_module_body_use_phoenix_router_still_counts(self):
        """The predicate narrows the candidate set, it does not empty it: a
        router declared somewhere other than `router.ex` is still found."""
        tmp, root = _phoenix_repo(_ROUTES_SPACE_DELIMITED)
        self.addCleanup(tmp.cleanup)
        (root / "lib" / "admin_router.ex").write_text(
            'defmodule AdminRouter do\n  use Phoenix.Router\n'
            '  get "/admin", AdminController, :index\nend\n')
        files = [Path(p).name for p in D._elixir_router_files(root)]
        self.assertEqual(sorted(files), ["admin_router.ex", "router.ex"])

    def test_the_unmerged_router_residual_renders_on_the_stub_path_too(self):
        """The baseline's unnamed residual. A second router was named only when
        the primary produced rows; when the primary produced none, the file
        degraded to the empty-but-valid stub and the discarded router was never
        mentioned at all — the reader could not tell "this repository has no
        routes" from "the routes are in a file we dropped"."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "repo"
        (root / "lib" / "aaa_web").mkdir(parents=True)
        _bare_elixir(root)
        # Primary by POSIX sort, and it declares no route.
        (root / "lib" / "aaa_web" / "router.ex").write_text(
            'defmodule AaaWeb.Router do\n  use Phoenix.Router\n'
            '  pipeline :browser do\n    plug :accepts, ["html"]\n  end\nend\n')
        (root / "lib" / "zzz_web").mkdir(parents=True)
        (root / "lib" / "zzz_web" / "router.ex").write_text(
            'defmodule ZzzWeb.Router do\n  use Phoenix.Router\n'
            '  get "/z", ZController, :index\nend\n')
        md, _ = D.extract_elixir_api_surface_router(root, "bionic")
        self.assertIn(D.NO_EXTRACTOR.rstrip("\n"), md, "expected the stub path")
        self.assertIn("Additional Phoenix routers not merged", md)
        self.assertIn("lib/zzz_web/router.ex", md)


class RouterCallSpellingTests(unittest.TestCase):
    """The regression a regular expression cannot pass.

    `get "/x", C, :a` and `get("/x", C, :a)` are the same call, and the
    baseline's verb pattern required the first spelling — so a project whose
    formatter writes the second matched none of its declarations. Both
    spellings parse to one `call` node with one argument list, which is what
    makes this assertion the point of the change rather than a detail of it.
    """

    def _render(self, source: str) -> str:
        tmp, root = _phoenix_repo(source)
        self.addCleanup(tmp.cleanup)
        md, _ = D.extract_elixir_api_surface_router(root, "bionic")
        return md

    def test_both_spellings_yield_identical_rows(self):
        space = _route_rows(self._render(_ROUTES_SPACE_DELIMITED))
        parens = _route_rows(self._render(_ROUTES_PARENTHESIZED))
        self.assertTrue(space, "the space-delimited fixture rendered no rows")
        self.assertEqual(space, parens)

    def test_the_rows_are_the_declarations(self):
        rows = _route_rows(self._render(_ROUTES_PARENTHESIZED))
        got = {(m, p, c) for m, p, c in rows}
        self.assertEqual(got, {
            ("GET", "/", "MyAppWeb.PageController#index"),
            ("POST", "/contact", "MyAppWeb.PageController#contact"),
            ("LIVE", "/dashboard", "MyAppWeb.DashboardLive#index"),
            ("GET", "/widgets", "MyAppWeb.WidgetController#index"),
            ("GET", "/widgets/:id", "MyAppWeb.WidgetController#show"),
        })

    def test_forward_stays_a_named_residual_in_both_spellings(self):
        for source in (_ROUTES_SPACE_DELIMITED, _ROUTES_PARENTHESIZED):
            with self.subTest(parenthesized=source is _ROUTES_PARENTHESIZED):
                md = self._render(source)
                self.assertIn("`forward` route was found and not expanded", md)

    def test_a_comment_is_not_a_route_in_either_spelling(self):
        md = self._render(_ROUTES_PARENTHESIZED.replace(
            '    forward("/jobs", ObanWeb)\n',
            '    # get("/ghost", GhostController, :index)\n'))
        self.assertNotIn("GhostController", md)

    def test_a_route_inside_a_string_is_not_a_route(self):
        md = self._render(_ROUTES_PARENTHESIZED.replace(
            '    forward("/jobs", ObanWeb)\n',
            '    @doc "get(\\"/ghost\\", GhostController, :index)"\n'))
        self.assertNotIn("GhostController", md)

    def test_an_interpolated_path_is_not_rendered_as_a_literal(self):
        """A path composed at compile time has no static value. Rendering the
        source text of the interpolation as if it were the path is the node
        pack's measured `/api/${routeName}/:id` defect; the elixir reader names
        it a residual instead."""
        md = self._render(_ROUTES_SPACE_DELIMITED.replace(
            '    get "/", PageController, :index\n',
            '    get "/#{@prefix}/x", PageController, :index\n'))
        self.assertNotIn("#{", md)
        self.assertIn("named residual", md)


class ElixirReviewRegressionTests(unittest.TestCase):
    """Regressions for the review's two MUST-FIX findings + the S1-S3 coverage
    gaps (ADR-enumerated residual/edge branches)."""

    def _repo(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "repo"
        root.mkdir()
        _bare_elixir(root)
        (root / "bionic").mkdir()
        return root

    def test_router_filename_escaped(self):
        # Security MUST-FIX: the router file PATH is untrusted (attacker controls
        # dir/file names); a backtick in it must be neutralized (via _cell) so it
        # cannot break out of the code span in the rendered header.
        root = self._repo()
        d = root / "lib" / "we`b"
        d.mkdir(parents=True)
        (d / "router.ex").write_text(
            'defmodule MyAppWeb.Router do\n  use Phoenix.Router\n'
            '  get "/x", PageController, :index\nend\n')
        md, _ = D.extract_elixir_api_surface_router(root, "bionic")
        self.assertNotIn("we`b", md)                  # raw backtick would break the span.
        self.assertIn("weʼb", md)                     # neutralized by _cell.

    def test_deep_interpolation_bails_no_crash(self):
        # Fail-closed MUST-FIX: a .ex with interpolation nested past the cap must
        # degrade to a hashed residual, never crash the whole derive (RecursionError).
        root = self._repo()
        (root / "lib").mkdir(exist_ok=True)
        (root / "lib" / "bomb.ex").write_text('x = "' + "#{" * 400 + '"\n')
        md, sources = D.extract_elixir_data_model(root, "bionic")   # must not raise.
        self.assertIn("lib/bomb.ex", sources)                       # hashed.
        self.assertIn("scrub bailed", md.lower())                   # named residual.

    def test_router_residual_branches(self):
        # S1: forward / resources param: / member block each emit a residual.
        root = self._repo()
        (root / "lib" / "myapp_web").mkdir(parents=True)
        (root / "lib" / "myapp_web" / "router.ex").write_text(
            'defmodule MyAppWeb.Router do\n  use Phoenix.Router\n'
            '  resources "/posts", PostController, param: :slug\n'
            '  forward "/jobs", ObanWeb\n'
            '  scope "/api" do\n    get "/ping", PingController, :show\n  end\nend\n')
        md, _ = D.extract_elixir_api_surface_router(root, "bionic")
        low = md.lower()
        self.assertTrue("forward" in low and ("param" in low or "residual" in low))

    def test_data_model_edge_cases(self):
        # S2: define_field:false (no FK column) + many_to_many join_through +
        # embedded_schema (note, not a table row).
        root = self._repo()
        acc = root / "lib" / "myapp"
        acc.mkdir(parents=True)
        (acc / "post.ex").write_text(
            'defmodule MyApp.Post do\n  use Ecto.Schema\n  schema "posts" do\n'
            '    field :title, :string\n'
            '    belongs_to :author, MyApp.User, define_field: false\n'
            '    many_to_many :tags, MyApp.Tag, join_through: "posts_tags"\n'
            '  end\nend\n')
        (acc / "addr.ex").write_text(
            'defmodule MyApp.Address do\n  use Ecto.Schema\n  embedded_schema do\n'
            '    field :city, :string\n  end\nend\n')
        md, _ = D.extract_elixir_data_model(root, "bionic")
        self.assertNotIn("author_id", md)             # define_field:false → no FK column.
        self.assertIn("posts_tags", md)               # join_through named as a residual.
        low = md.lower()
        self.assertTrue("embedded" in low)            # embedded_schema noted, not a table.

    def test_module_graph_nested_defmodule_residual(self):
        # S3: a nested defmodule is a named residual.
        root = self._repo()
        (root / "lib").mkdir(exist_ok=True)
        (root / "lib" / "outer.ex").write_text(
            'defmodule MyApp.Outer do\n  defmodule Inner do\n    def x, do: 1\n  end\nend\n')
        md, _ = D.extract_elixir_module_graph(root, "bionic")
        self.assertIn("nested", md.lower())


class ActionFilterTriStateTests(unittest.TestCase):
    """D-2 — the elixir instance of the same empty-set-means-permissive defect.

    `_ts_atom_set` carried no carve-out docstring and no residual, and its
    trigger is MORE idiomatic than ruby's: `only: @actions` with a module
    attribute is the normal Phoenix spelling, and it rendered all eight REST
    rows as if no filter had been declared.

    Tri-state, exactly as ruby's: `None` is "not read" and earns a named
    residual; `set()` is `only: []`, which declares ZERO REST actions. An
    absent `only:` is the fourth fact, kept distinct at the caller by testing
    key presence rather than by anything the helper returns.
    """

    _RESIDUAL = "could not be read"

    def _render(self, resources_line: str) -> str:
        source = _ROUTES_SPACE_DELIMITED.replace(
            '    resources "/widgets", WidgetController, only: [:index, :show]\n',
            resources_line)
        tmp, root = _phoenix_repo(source)
        self.addCleanup(tmp.cleanup)
        md, _ = D.extract_elixir_api_surface_router(root, "bionic")
        return md

    def test_a_module_attribute_only_list_is_named_rather_than_permissive(self):
        md = self._render(
            '    resources "/widgets", WidgetController, only: @actions\n')
        rows = _route_rows(md)
        widgets = [r for r in rows if "WidgetController" in r[2]]
        self.assertEqual(len(widgets), 8,
                         "the full action set is what an unread filter admits")
        self.assertIn(self._RESIDUAL, md,
                      "an unreadable `only:` rendered every action silently")

    def test_an_unreadable_element_makes_the_whole_list_unreadable(self):
        md = self._render(
            '    resources "/widgets", WidgetController, only: [:index, @extra]\n')
        widgets = [r for r in _route_rows(md) if "WidgetController" in r[2]]
        self.assertEqual(len(widgets), 8,
                         "a partial filter was applied as if it were complete")
        self.assertIn(self._RESIDUAL, md)

    def test_an_unreadable_except_list_is_named_too(self):
        md = self._render(
            '    resources "/widgets", WidgetController, except: @denied\n')
        widgets = [r for r in _route_rows(md) if "WidgetController" in r[2]]
        self.assertEqual(len(widgets), 8)
        self.assertIn(self._RESIDUAL, md)

    def test_a_genuinely_empty_literal_declares_zero_rest_actions(self):
        """`only: []` renders NO REST rows, matching Phoenix. It must also earn
        no residual: the filter was read, and it read as empty."""
        md = self._render(
            '    resources "/widgets", WidgetController, only: []\n')
        widgets = [r for r in _route_rows(md) if "WidgetController" in r[2]]
        self.assertEqual(widgets, [])
        self.assertNotIn(self._RESIDUAL, md)

    def test_an_empty_except_excludes_nothing(self):
        """The mirror of the above, and the reason the test is `is not None`
        rather than a truth test on both filters."""
        md = self._render(
            '    resources "/widgets", WidgetController, except: []\n')
        widgets = [r for r in _route_rows(md) if "WidgetController" in r[2]]
        self.assertEqual(len(widgets), 8)
        self.assertNotIn(self._RESIDUAL, md)

    def test_an_absent_filter_renders_every_action_and_names_nothing(self):
        md = self._render(
            '    resources "/widgets", WidgetController\n')
        widgets = [r for r in _route_rows(md) if "WidgetController" in r[2]]
        self.assertEqual(len(widgets), 8)
        self.assertNotIn(self._RESIDUAL, md)

    def test_a_readable_filter_still_filters_and_names_nothing(self):
        md = self._render(
            '    resources "/widgets", WidgetController, only: [:index, :show]\n')
        widgets = [r for r in _route_rows(md) if "WidgetController" in r[2]]
        self.assertEqual(len(widgets), 2)
        self.assertNotIn(self._RESIDUAL, md)

    def test_pipe_through_survives_an_unreadable_argument(self):
        """`pipe_through` feeds the SAME helper into a `set.update`, so a
        tri-state `None` reaches it too. It must not raise."""
        md = self._render(
            '    pipe_through @pipelines\n'
            '    resources "/widgets", WidgetController, only: [:index]\n')
        self.assertIn("WidgetController#index", md)


class DeclaredParserIsReachableTests(unittest.TestCase):
    """A concern's `InputClass.parser` must name a dependency its probes can
    actually reach, in BOTH directions.

    `core.resolve_declared_parsers` runs before extraction and raises
    `ParserUnavailable` (exit 2) on a missing module, so the declaration is a
    hard precondition rather than a hint. Declaring a parser no probe uses makes
    a machine fail for a grammar the concern never touches; omitting one a probe
    does use moves the failure from an up-front, named refusal into an
    `ImportError` inside the extractor. Neither is visible by reading the
    registry, so the check is a call-graph walk over this module's own source.

    The data-model row is the case that motivated the test. Its FIRST probe,
    `extract_elixir_data_model` → `_parse_elixir_schemas`, is a line reader, so
    the declaration looks false at a glance; its SECOND probe,
    `extract_elixir_migrations` → `_parse_elixir_migration` → `_elixir_parse`,
    parses with tree-sitter. A concern needs the parser if ANY of its probes
    does, because `resolve_declared_parsers` cannot know which probe will win.
    """

    _TS_ENTRIES = ("_elixir_parse", "_elixir_ts_parser")

    def _call_graph(self, module) -> dict:
        import ast                                        # noqa: PLC0415
        src = Path(module.__file__).read_text(encoding="utf-8")
        graph: dict = {}
        for node in ast.parse(src).body:
            if not isinstance(node, ast.FunctionDef):
                continue
            graph[node.name] = {
                sub.func.id for sub in ast.walk(node)
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
            }
        return graph

    def _reaches(self, graph: dict, start: str, targets: tuple) -> bool:
        seen, stack = set(), [start]
        while stack:
            fn = stack.pop()
            if fn in seen:
                continue
            seen.add(fn)
            for callee in graph.get(fn, ()):
                if callee in targets:
                    return True
                stack.append(callee)
        return False

    def test_every_concern_declaring_tree_sitter_has_a_probe_that_parses(self):
        pack = importlib.import_module("crux.arch.packs.elixir")
        graph = self._call_graph(pack)
        for concern, probes in pack.probes().items():
            declared = bool(pack.INPUT_CLASSES[concern].parser)
            used = any(self._reaches(graph, p.extract.__name__, self._TS_ENTRIES)
                       for p in probes)
            self.assertEqual(
                declared, used,
                f"{concern}: declares parser={declared}, probes parse={used} — "
                "a declaration that does not match the probes either fails a "
                "machine for a grammar it never touches, or lets the import "
                "error escape the up-front refusal")

    def test_the_data_model_declaration_is_carried_by_the_migrations_probe(self):
        # The specific claim, pinned so the general test above cannot be
        # satisfied by an accident of naming.
        pack = importlib.import_module("crux.arch.packs.elixir")
        graph = self._call_graph(pack)
        self.assertFalse(
            self._reaches(graph, "extract_elixir_data_model", self._TS_ENTRIES),
            "the Ecto schema reader is a line reader")
        self.assertTrue(
            self._reaches(graph, "extract_elixir_migrations", self._TS_ENTRIES),
            "the migration replay parses with tree-sitter, which is what makes "
            "the data-model parser declaration true")


if __name__ == "__main__":
    unittest.main(verbosity=2)
