"""Tests for the Python arch stack pack (ADR-0066 dev module 2, points 8 + 10).

Covers the three Python concern extractors (api-surface, data-model,
module-graph), their detection + resolution through the seam, deterministic
rendering (OpenAPI grouping/sorting, the SQLAlchemy entity table, and the
Alembic topological timeline), drift-gating via source hashes, and byte-stable
re-derivation. Stdlib only; the committed `fixtures/pyservice/` fixture is
copied to a tempdir before any mutation so it is never altered.
"""

from __future__ import annotations

import importlib
import inspect
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]      # crux/scripts
sys.path.insert(0, str(SCRIPTS))

D = importlib.import_module("crux.arch.derive")
P = importlib.import_module("crux.arch.packs.python")
CORE = importlib.import_module("crux.arch.core")

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "pyservice"


def _copy_fixture() -> Path:
    tmp = tempfile.TemporaryDirectory()
    dest = Path(tmp.name) / "repo"
    shutil.copytree(FIXTURE, dest)
    (dest / "bionic").mkdir()
    return tmp, dest


def _write_repo(files: dict) -> tuple:
    """Materialize a scratch repository from {repo-relative path: text}.

    Written into a tempdir rather than committed under `fixtures/`: these
    fixtures exist to exercise one probe, and a committed tree of framework
    source would also be read by this repository's own code-doc extractor.
    """
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name) / "repo"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp, root


class DetectionAndResolutionTests(unittest.TestCase):
    def test_detects_python_stack(self):
        # pyproject.toml present, no crux markers → auto-detects the python pack.
        self.assertEqual(D.detect_stack(FIXTURE, None), "python")

    def test_resolution_picks_real_extractors(self):
        # Each concern resolves to its real Python extractor (not the stub).
        self.assertIs(
            D.resolve_extractor("api-surface", FIXTURE, "bionic", None, pack_name="python"),
            D.extract_python_api_surface,
        )
        self.assertIs(
            D.resolve_extractor("data-model", FIXTURE, "bionic", None, pack_name="python"),
            D.extract_python_data_model,
        )
        self.assertIs(
            D.resolve_extractor("module-graph", FIXTURE, "bionic", None, pack_name="python"),
            D.extract_python_module_graph,
        )

    def test_detect_package_prefers_pyproject_hint(self):
        found = D.detect_package(FIXTURE)
        self.assertIsNotNone(found)
        pkg_dir, pkg_name = found
        self.assertEqual(pkg_name, "app")
        self.assertEqual(pkg_dir, FIXTURE / "app")


class ApiSurfaceTests(unittest.TestCase):
    def test_groups_and_sorts(self):
        md, sources = D.extract_python_api_surface(FIXTURE, "bionic")
        self.assertNotIn("no extractor", md)
        # openapi.json hashed into sources (drift-gate).
        self.assertIn("openapi.json", sources)
        # Groups sorted: health < posts < users.
        i_health = md.index("## health")
        i_posts = md.index("## posts")
        i_users = md.index("## users (4)")
        self.assertLess(i_health, i_posts)
        self.assertLess(i_posts, i_users)
        # Within /users/{id}: DELETE sorts before GET (method sort within a path).
        i_del = md.index("| DELETE | `/users/{id}` | Delete user |")
        i_get = md.index("| GET | `/users/{id}` | Get user |")
        self.assertLess(i_del, i_get)
        # Untagged /health grouped by path prefix.
        self.assertIn("| GET | `/health` | Health check |", md)

    def test_stub_when_openapi_absent(self):
        tmp, root = _copy_fixture()
        self.addCleanup(tmp.cleanup)
        (root / "openapi.json").unlink()
        # Detection no longer fires → resolution yields the stub.
        ext = D.resolve_extractor("api-surface", root, "bionic", None, pack_name="python")
        md, sources = ext(root, "bionic")
        self.assertIn("no extractor", md)
        self.assertEqual(sources, {})


class DataModelTests(unittest.TestCase):
    def test_entity_table(self):
        md, sources = D.extract_python_data_model(FIXTURE, "bionic")
        self.assertIn("## SQLAlchemy models (2)", md)
        # models.py hashed; every migration file hashed.
        self.assertIn("app/models.py", sources)
        for name in ("aaa_root", "bbb_merge", "mmm_branch2", "zzz_branch1"):
            self.assertIn(f"alembic/versions/{name}.py", sources)
        # Column rows sorted by (table, column).
        posts_author = md.index("| posts | `author_id` |")
        posts_id = md.index("| posts | `id` |")
        posts_title = md.index("| posts | `title` |")
        users_email = md.index("| users | `email` |")
        self.assertLess(posts_author, posts_id)
        self.assertLess(posts_id, posts_title)
        self.assertLess(posts_title, users_email)
        # ForeignKey + nullable + pk are captured.
        self.assertRegex(md, r"\| posts \| `author_id` \| Integer \| no \| no \| users\.id \|")
        self.assertRegex(md, r"\| users \| `id` \| Integer \| — \| yes \| — \|")

    def test_alembic_timeline_canonical_topological_order(self):
        md, _ = D.extract_python_data_model(FIXTURE, "bionic")
        self.assertIn("## Alembic migration timeline (4)", md)
        self.assertIn("Canonical topological order", md)
        # Canonical order by (depth, revision-id):
        #   depth 0: aaa_root
        #   depth 1: mmm_branch2, zzz_branch1   (id tie-break, m < z)
        #   depth 2: bbb_merge
        order = [md.index(f"| `{rev}` |") for rev in
                 ("aaa_root", "mmm_branch2", "zzz_branch1", "bbb_merge")]
        self.assertEqual(order, sorted(order), "timeline not in canonical topological order")
        # The merge row shows both parents (sorted).
        self.assertIn("| `bbb_merge` | `mmm_branch2`, `zzz_branch1` |", md)
        self.assertIn("| `aaa_root` | (base) |", md)

    def test_topological_order_is_not_filesystem_order(self):
        # Filesystem (sorted-filename) order would be:
        #   aaa_root, bbb_merge, mmm_branch2, zzz_branch1
        # whereas the canonical topological order is:
        #   aaa_root, mmm_branch2, zzz_branch1, bbb_merge
        # so the merge (depth 2) must render LAST, not second — proving the order
        # derives from the DAG, not from glob/filesystem discovery order.
        md, _ = D.extract_python_data_model(FIXTURE, "bionic")
        i_merge = md.index("| `bbb_merge` |")
        i_branch2 = md.index("| `mmm_branch2` |")
        i_branch1 = md.index("| `zzz_branch1` |")
        self.assertGreater(i_merge, i_branch2)
        self.assertGreater(i_merge, i_branch1)

    def test_malformed_graph_falls_back_to_revision_sort(self):
        # A cycle (mutual down_revision) is malformed → plain revision-id sort.
        nodes = {"b": (["a"], "b"), "a": (["b"], "a")}
        order, malformed = D._topo_order(nodes)
        self.assertTrue(malformed)
        self.assertEqual(order, ["a", "b"])
        # A missing parent is likewise malformed.
        nodes2 = {"child": (["missing"], "c")}
        order2, malformed2 = D._topo_order(nodes2)
        self.assertTrue(malformed2)
        self.assertEqual(order2, ["child"])

    def test_stub_when_no_models_or_migrations(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "pyproject.toml").write_text('[project]\nname = "empty"\n')
        md, sources = D.extract_python_data_model(root, "bionic")
        self.assertIn("no extractor", md)
        self.assertEqual(sources, {})


class ModuleGraphTests(unittest.TestCase):
    def test_module_graph_on_fixture(self):
        md, sources = D.extract_python_module_graph(FIXTURE, "bionic")
        self.assertNotIn("no extractor", md)
        # Package files hashed.
        self.assertIn("app/models.py", sources)
        self.assertIn("app/api.py", sources)
        # Header names the detected package + its relative dir.
        self.assertIn("Intra-package imports of `app` under `app/`", md)
        # The relative import `from .models import ...` yields an api -> models edge.
        self.assertIn("api[api] --> models[models]", md)

    def test_crux_pin_unchanged(self):
        # The crux wrapper still pins crux/scripts/crux + name `crux` — proven by
        # byte-identity against the parameterized engine called with those args.
        REPO = SCRIPTS.parents[1]
        pinned = D.extract_module_graph(REPO, "bionic")[0]
        direct = D._extract_module_graph(REPO, REPO / "crux" / "scripts" / "crux", "crux")[0]
        self.assertEqual(pinned, direct)
        self.assertIn("under `crux/scripts/crux/`", pinned)


class DriftAndDeterminismTests(unittest.TestCase):
    def _spine(self, root: Path) -> dict:
        return D._build(root, "bionic")

    def test_deterministic_rebuild_byte_identical(self):
        tmp, root = _copy_fixture()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(self._spine(root), self._spine(root))

    def test_overview_shape_is_stack_neutral_for_python(self):
        # Regression: the overview's Shape section must not render crux-dogfood
        # vocabulary for a non-crux pack (the FastAPI case ADR-0066 motivates).
        overview = self._spine(FIXTURE)["overview.md"]
        self.assertIn("_Stack pack: `python`", overview)
        self.assertNotIn("crux` package modules", overview)
        self.assertNotIn("crux-env` CLI", overview)
        self.assertIn("see `data-model.md`.", overview)

    def test_derive_then_dry_run_clean(self):
        tmp, root = _copy_fixture()
        self.addCleanup(tmp.cleanup)
        D.derive(root, "bionic")
        self.assertEqual(D.dry_run(root, "bionic"), [], "dry_run dirty after derive")

    def test_openapi_mutation_drifts(self):
        tmp, root = _copy_fixture()
        self.addCleanup(tmp.cleanup)
        D.derive(root, "bionic")
        spec = root / "openapi.json"
        spec.write_text(spec.read_text().replace("List users", "List all users"))
        drift = D.dry_run(root, "bionic")
        self.assertIn("bionic/arch/api-surface.md", drift)

    def test_model_mutation_drifts(self):
        tmp, root = _copy_fixture()
        self.addCleanup(tmp.cleanup)
        D.derive(root, "bionic")
        models = root / "app" / "models.py"
        models.write_text(models.read_text().replace(
            'name = Column(String(100), nullable=True)',
            'name = Column(String(150), nullable=True)'))
        drift = D.dry_run(root, "bionic")
        self.assertIn("bionic/arch/data-model.md", drift)

    def test_migration_mutation_changes_sources_hash(self):
        before = D.extract_python_data_model(FIXTURE, "bionic")[1]
        tmp, root = _copy_fixture()
        self.addCleanup(tmp.cleanup)
        mig = root / "alembic" / "versions" / "aaa_root.py"
        mig.write_text(mig.read_text().replace('"""root migration"""', '"""initial schema"""'))
        after = D.extract_python_data_model(root, "bionic")[1]
        self.assertNotEqual(
            before["alembic/versions/aaa_root.py"],
            after["alembic/versions/aaa_root.py"],
            "migration edit did not move its source hash",
        )


# ═════════════ ADR-0096 clauses 1/3 — the python api-surface probe ═══════════
# Dev loop 2, unit U-P1. The concern was `committed-artifact` over one path,
# `openapi.json` at the repository root, and it stubbed on 3 of 3 python corpus
# repositories because none of the three commits one. It becomes a two-probe
# chain — the committed artifact first, then an `ast` parse of the project's own
# sources — and the declaration moves with it, because dev loop 1 narrowed the
# glob precisely to what the probe reads.


FASTAPI_FIXTURE = {
    "pyproject.toml": '[project]\nname = "fx"\n',
    "app/__init__.py": "",
    "app/main.py": (
        "from fastapi import FastAPI\n"
        "from app.api.main import api_router\n"
        "from app.core.config import settings\n"
        "\n"
        "app = FastAPI()\n"
        "app.include_router(api_router, prefix=settings.API_V1_STR)\n"
        "\n"
        "\n"
        "@app.get('/health')\n"
        "def health() -> dict:\n"
        "    return {}\n"
    ),
    "app/api/__init__.py": "",
    "app/api/main.py": (
        "from fastapi import APIRouter\n"
        "\n"
        "from app.api.routes import items\n"
        "\n"
        "api_router = APIRouter()\n"
        "api_router.include_router(items.router)\n"
    ),
    "app/api/routes/__init__.py": "",
    "app/api/routes/items.py": (
        "from fastapi import APIRouter\n"
        "\n"
        "router = APIRouter(prefix='/items', tags=['items'])\n"
        "\n"
        "\n"
        "@router.get('/')\n"
        "def read_items() -> list:\n"
        "    return []\n"
        "\n"
        "\n"
        "@router.post('/{id}')\n"
        "def create_item(id: str) -> dict:\n"
        "    return {}\n"
    ),
}

FLASK_FIXTURE = {
    "pyproject.toml": '[project]\nname = "fl"\n',
    "wsgi.py": (
        "from flask import Blueprint, Flask\n"
        "\n"
        "from views import admin\n"
        "\n"
        "app = Flask(__name__)\n"
        "api = Blueprint('api', __name__, url_prefix='/api')\n"
        "\n"
        "\n"
        "@api.route('/users', methods=['GET', 'POST'])\n"
        "def users():\n"
        "    return ''\n"
        "\n"
        "\n"
        "@app.route('/health')\n"
        "def health():\n"
        "    return ''\n"
        "\n"
        "\n"
        "@app.get('/ping')\n"
        "def ping():\n"
        "    return ''\n"
        "\n"
        "\n"
        "app.register_blueprint(api)\n"
        "app.register_blueprint(admin.bp, url_prefix='/v2')\n"
    ),
    "views/__init__.py": "",
    "views/admin.py": (
        "from flask import Blueprint\n"
        "\n"
        "bp = Blueprint('admin', __name__, url_prefix='/admin')\n"
        "\n"
        "\n"
        "@bp.route('/stats')\n"
        "def stats():\n"
        "    return ''\n"
    ),
}

DJANGO_FIXTURE = {
    "pyproject.toml": '[project]\nname = "dj"\n',
    "proj/__init__.py": "",
    "proj/urls.py": (
        "from django.urls import include, path, re_path\n"
        "\n"
        "from . import views\n"
        "\n"
        "urlpatterns = [\n"
        "    path('', views.home, name='home'),\n"
        "    path('blog/', include('blog.urls')),\n"
        "    re_path(r'^legacy/(?P<pk>\\d+)/$', views.legacy),\n"
        "]\n"
    ),
    "proj/views.py": "def home(request):\n    pass\n\n\ndef legacy(request, pk):\n    pass\n",
    "blog/__init__.py": "",
    "blog/urls.py": (
        "from django.urls import path\n"
        "\n"
        "from . import views\n"
        "\n"
        "urlpatterns = [\n"
        "    path('<int:year>/', views.year),\n"
        "    path('', views.index),\n"
        "]\n"
    ),
    "blog/views.py": "def year(request, year):\n    pass\n\n\ndef index(request):\n    pass\n",
}


class ApiSurfaceInputClassTests(unittest.TestCase):
    """ADR-0097 part 1: `kind` is DERIVED per concern from the probe chain,
    never read off the pack's raw `INPUT_CLASSES` map — so this class reads
    `core.input_classes()`, not `P.INPUT_CLASSES` directly."""

    def test_api_surface_derives_parser_over_the_python_sources(self):
        """The concern's chain is `committed-artifact` then `parser`; the
        DERIVED class is the weaker of the two, `parser`."""
        ic = CORE.input_classes("python")["api-surface"]
        self.assertEqual(ic.kind, "parser")
        for glob in ("openapi.json", "*.py", "**/*.py"):
            self.assertIn(glob, ic.globs)

    def test_api_surface_names_which_glob_is_the_emitted_artifact(self):
        ic = P.INPUT_CLASSES["api-surface"]
        self.assertEqual(ic.artifact, ("openapi.json",))

    def test_api_surface_declares_no_refresh_command(self):
        """Empty is the declaration, not a gap someone forgot to fill.

        The only command that emits `openapi.json` imports the target's own
        application object and calls `.openapi()` on it. That executes the
        project's import-time code — the operation ADR-0075 confines behind a
        two-factor consent gate, and the one clause 11 says the runtime must
        never be the remedy a crux surface points a reader at. `refresh` is
        reader-facing: it is rendered verbatim into the clause 9 remediation
        line, which `derive-arch` tells a forked subagent to paste.

        Empty makes clause 9 fall back to `NO_COMMAND — the derive needs
        <expected>`, which it sanctions for exactly this case. Asserted here
        as an equality so restoring a command fails rather than passes."""
        self.assertEqual(P.INPUT_CLASSES["api-surface"].refresh, "")
        rec = {"concern": "api-surface", "extractor": "python", "inputs_found": [],
               **CORE.Verdict.stubbed(CORE.StubReason.PRECONDITION_MISSING,
                                      P.INPUT_CLASSES["api-surface"].expected,
                                      "no matching input").as_record()}
        line = CORE.remediation(rec, P.INPUT_CLASSES["api-surface"])
        self.assertTrue(line.startswith(CORE.NO_COMMAND), line)
        self.assertNotIn("python -c", line)

    def test_the_python_pack_declares_no_parser_of_its_own(self):
        """Its three concerns read through `ast` and `json`, both stdlib, so
        this pack contributes no parser declaration.

        `decision-index` is deliberately excluded: it is the UNIVERSAL concern
        the core binds for every pack, it declares `yaml` because it reads ADR
        frontmatter through `_frontmatter`, and attributing that to the python
        pack would say this pack's own readers need PyYAML. They do not. The
        pin below is the universal one and the only one.
        """
        for concern, ic in CORE.input_classes("python").items():
            if concern == "decision-index":
                continue
            self.assertEqual(ic.parser, (), concern)
        self.assertEqual(list(CORE.parser_pins("python")), ["parser:yaml"])

    def test_the_committed_artifact_is_tried_before_the_parse(self):
        probes = P.probes()["api-surface"]
        self.assertEqual(len(probes), 2)
        self.assertIs(probes[0].extract, P.extract_python_api_surface)
        self.assertIs(probes[1].extract, P.extract_python_api_surface_routes)

    def test_a_committed_openapi_document_still_wins(self):
        # The `pyservice` fixture commits one; the chain must not reach the parse.
        ext = D.resolve_extractor("api-surface", FIXTURE, "bionic", None,
                                  pack_name="python")
        self.assertIs(ext, P.extract_python_api_surface)


class ModuleGraphExpectedNamesTheScannedDepthTests(unittest.TestCase):
    """ADR-0097 part 4: the `expected` sentence names the DEPTH BOUND.

    ADR-0096 clause 6 said "neither detector returns a silent None". Part 4
    qualifies it where the corpus falsifies it: that holds only WITHIN the
    scanned depth, and a package outside it yields an empty candidate set and a
    `precondition_missing` stub. The postcondition is that the concern's
    `expected` sentence names the bound, so a reader of the stub can tell "this
    repository has no package" from "none within reach of the scan".

    The bound is READ OFF `_manifest_dirs`' own default rather than restated
    here. Restating it would let the code's bound and the sentence drift apart
    silently, which is the whole defect part 4 corrects — a sentence that
    describes a scan it no longer matches.
    """

    _NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}

    def test_expected_sentence_names_the_manifest_dirs_depth_bound(self):
        max_depth = inspect.signature(P._manifest_dirs).parameters["max_depth"].default
        self.assertIn(max_depth, self._NUMBER_WORDS,
                      "the bound moved past what this test can spell — extend "
                      "_NUMBER_WORDS rather than deleting the assertion")
        words = " or ".join(self._NUMBER_WORDS[d] for d in range(1, max_depth + 1))
        self.assertIn(
            f"{words} directories below it",
            P.INPUT_CLASSES["module-graph"].expected,
            "the module-graph `expected` sentence does not name the depth "
            f"`_manifest_dirs` actually scans ({max_depth}), so a "
            "`precondition_missing` stub cannot tell a reader whether the "
            "repository has no package or merely none within reach")


class FastApiRouteTests(unittest.TestCase):
    def setUp(self):
        tmp, self.root = _write_repo(FASTAPI_FIXTURE)
        self.addCleanup(tmp.cleanup)
        self.md, self.sources = P.extract_python_api_surface_routes(self.root, "bionic")

    def test_decorated_routes_are_found(self):
        self.assertIn("## Routes (3)", self.md)
        self.assertIn("| GET | `/health` | `health` |", self.md)

    def test_mount_prefixes_compose_through_include_router(self):
        # items.router carries `prefix='/items'`; api_router mounts it; app
        # mounts api_router under an unresolvable prefix.
        self.assertIn("| GET | `${settings.API_V1_STR}/items/` | `read_items` |", self.md)
        self.assertIn("| POST | `${settings.API_V1_STR}/items/{id}` | `create_item` |",
                      self.md)

    def test_an_unresolvable_prefix_renders_verbatim_and_is_never_guessed(self):
        self.assertIn("## Residuals", self.md)
        residuals = self.md.split("## Residuals", 1)[1]
        self.assertIn("settings.API_V1_STR", residuals)
        # The baseline's node finding was 9 confidently wrong paths. Nothing
        # here may invent the value the name would have had at runtime.
        self.assertNotIn("/api/v1", self.md)

    def test_every_contributing_file_is_hashed(self):
        for rel in ("app/main.py", "app/api/main.py", "app/api/routes/items.py"):
            self.assertIn(rel, self.sources)

    def test_the_table_reuses_the_shared_method_path_header(self):
        # `count_concern_entities` keys on the first two header cells; a new
        # spelling counts zero entities and fakes `no_entities` over a full table.
        self.assertIn("| method | path | handler |", self.md)
        self.assertEqual(CORE.count_concern_entities("api-surface", self.md), 3)

    def test_rendering_is_byte_stable(self):
        again, _ = P.extract_python_api_surface_routes(self.root, "bionic")
        self.assertEqual(self.md, again)


class FlaskRouteTests(unittest.TestCase):
    """No corpus repository exercises Flask; it ships on fixture evidence, and
    the corpus record carries that gap."""

    def setUp(self):
        tmp, self.root = _write_repo(FLASK_FIXTURE)
        self.addCleanup(tmp.cleanup)
        self.md, self.sources = P.extract_python_api_surface_routes(self.root, "bionic")

    def test_route_decorator_methods_expand_to_one_row_each(self):
        self.assertIn("| GET | `/api/users` | `users` |", self.md)
        self.assertIn("| POST | `/api/users` | `users` |", self.md)

    def test_a_bare_route_decorator_defaults_to_get(self):
        self.assertIn("| GET | `/health` | `health` |", self.md)

    def test_the_verb_shortcut_decorator_is_read(self):
        self.assertIn("| GET | `/ping` | `ping` |", self.md)

    def test_a_registration_url_prefix_overrides_the_blueprint_prefix(self):
        # Flask's own rule: `register_blueprint(bp, url_prefix=…)` wins over the
        # `Blueprint(url_prefix=…)` the blueprint declared.
        self.assertIn("| GET | `/v2/stats` | `stats` |", self.md)
        self.assertNotIn("/admin/stats", self.md)


class DjangoUrlpatternTests(unittest.TestCase):
    def setUp(self):
        tmp, self.root = _write_repo(DJANGO_FIXTURE)
        self.addCleanup(tmp.cleanup)
        self.md, self.sources = P.extract_python_api_surface_routes(self.root, "bionic")

    def test_urlpatterns_elements_become_rows(self):
        self.assertIn("| — | `/` | `views.home` |", self.md)

    def test_include_composes_the_prefix_across_urls_modules(self):
        self.assertIn("| — | `/blog/` | `views.index` |", self.md)
        self.assertIn("| — | `/blog/<int:year>/` | `views.year` |", self.md)

    def test_re_path_patterns_render_with_their_anchors_stripped(self):
        # The backslash is doubled by `_cell`, which escapes it before the pipe
        # so a regex pattern cannot break the table row it sits in.
        self.assertIn(r"| — | `/legacy/(?P<pk>\\d+)/` | `views.legacy` |", self.md)

    def test_a_concatenated_urlpatterns_list_is_read(self):
        # `urlpatterns = other + [...]` is a Django idiom, and it is how
        # `djangoproject/urls/docs.py` composes the docs host's URLconf. A
        # collector that only reads a bare list drops the module whole.
        files = dict(DJANGO_FIXTURE)
        files["docs_host/__init__.py"] = ""
        files["docs_host/urls.py"] = (
            "from django.urls import path\n"
            "\n"
            "from blog.urls import urlpatterns as blog_urlpatterns\n"
            "from . import views\n"
            "\n"
            "urlpatterns = blog_urlpatterns + [\n"
            "    path('sitemap.xml', views.sitemap),\n"
            "]\n"
        )
        files["docs_host/views.py"] = "def sitemap(request):\n    pass\n"
        tmp, root = _write_repo(files)
        self.addCleanup(tmp.cleanup)
        md, sources = P.extract_python_api_surface_routes(root, "bionic")
        self.assertIn("| — | `/sitemap.xml` | `views.sitemap` |", md)
        # The merged list arrives at this host's root, so blog's own patterns
        # render a second time without the `/blog/` mount prefix.
        self.assertIn("| — | `/<int:year>/` | `views.year` |", md)
        self.assertIn("docs_host/urls.py", sources)

    def test_a_mount_is_an_edge_and_not_a_row(self):
        self.assertNotIn("include(", self.md.split("## Residuals")[0])
        self.assertEqual(CORE.count_concern_entities("api-surface", self.md), 4)


class ApiSurfaceStubTests(unittest.TestCase):
    def test_a_repository_with_no_route_declaration_stubs(self):
        tmp, root = _write_repo({
            "pyproject.toml": '[project]\nname = "q"\n',
            "q/__init__.py": "",
            "q/util.py": "def add(a, b):\n    return a + b\n",
        })
        self.addCleanup(tmp.cleanup)
        md, sources = P.extract_python_api_surface_routes(root, "bionic")
        self.assertIn("no extractor", md)
        self.assertEqual(sources, {})

    def test_the_recorded_verdict_is_populated_over_a_real_table(self):
        tmp, root = _write_repo(FASTAPI_FIXTURE)
        self.addCleanup(tmp.cleanup)
        md, sources = P.extract_python_api_surface_routes(root, "bionic")
        verdict = CORE.concern_verdict("api-surface", "python", "python", md, sources)
        self.assertEqual(verdict.kind, "populated")
        self.assertEqual(verdict.n_entities, 3)


# ═════════ ADR-0096 clauses 1/3 — the python data-model probes (U-P2) ════════
# The concern read SQLAlchemy declarative models and the Alembic timeline, and
# nothing else, so it stubbed on both python corpus repositories for two
# different reasons: `djangoproject-com` declares `django.db.models.Model`
# subclasses, and `fastapi-fullstack` declares SQLModel classes whose fields
# mostly live on a non-table base class.


DJANGO_MODELS_FIXTURE = {
    "pyproject.toml": '[project]\nname = "dj"\n',
    "blog/__init__.py": "",
    "blog/models.py": (
        "from django.db import models\n"
        "\n"
        "\n"
        "class TimeStamped(models.Model):\n"
        "    created = models.DateTimeField(auto_now_add=True)\n"
        "\n"
        "    class Meta:\n"
        "        abstract = True\n"
        "\n"
        "\n"
        "class Entry(TimeStamped):\n"
        "    title = models.CharField(max_length=200)\n"
        "    slug = models.SlugField(unique=True, null=True)\n"
        "    author = models.ForeignKey('accounts.User', on_delete=models.CASCADE)\n"
        "    tags = models.ManyToManyField('Tag')\n"
        "    objects = models.Manager()\n"
        "\n"
        "    class Meta:\n"
        "        db_table = 'weblog_entry'\n"
        "\n"
        "\n"
        "class Tag(models.Model):\n"
        "    name = models.CharField(max_length=50)\n"
    ),
}

SQLMODEL_FIXTURE = {
    "pyproject.toml": '[project]\nname = "app"\n',
    "app/__init__.py": "",
    "app/models.py": (
        "import uuid\n"
        "\n"
        "from sqlmodel import Field, Relationship, SQLModel\n"
        "\n"
        "\n"
        "class UserBase(SQLModel):\n"
        "    email: str = Field(unique=True, max_length=255)\n"
        "    is_active: bool = True\n"
        "\n"
        "\n"
        "class User(UserBase, table=True):\n"
        "    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)\n"
        "    hashed_password: str\n"
        "    items: list['Item'] = Relationship(back_populates='owner')\n"
        "\n"
        "\n"
        "class ItemBase(SQLModel):\n"
        "    title: str = Field(min_length=1)\n"
        "\n"
        "\n"
        "class Item(ItemBase, table=True):\n"
        "    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)\n"
        "    description: str | None = Field(default=None)\n"
        "    owner_id: uuid.UUID = Field(foreign_key='user.id')\n"
        "    owner: User | None = Relationship(back_populates='items')\n"
    ),
}


class DjangoOrmDataModelTests(unittest.TestCase):
    def setUp(self):
        tmp, self.root = _write_repo(DJANGO_MODELS_FIXTURE)
        self.addCleanup(tmp.cleanup)
        self.md, self.sources = D.extract_python_data_model(self.root, "bionic")

    def test_the_detector_fires_on_a_django_only_repository(self):
        self.assertTrue(D._detect_python_data_model(self.root))

    def test_meta_db_table_names_the_table(self):
        self.assertIn("| weblog_entry | `title` | CharField | no | no | — |", self.md)

    def test_the_default_table_name_is_app_label_and_class(self):
        self.assertIn("| blog_tag | `name` | CharField | no | no | — |", self.md)

    def test_null_true_is_the_nullable_column(self):
        self.assertIn("| weblog_entry | `slug` | SlugField | yes | no | — |", self.md)

    def test_relation_fields_name_their_target(self):
        self.assertIn("| weblog_entry | `author` | ForeignKey | no | no | accounts.User |",
                      self.md)
        self.assertIn("| weblog_entry | `tags` | ManyToManyField | no | no | Tag |", self.md)

    def test_a_manager_is_not_a_column(self):
        self.assertNotIn("`objects`", self.md)

    def test_an_abstract_base_declares_no_table_and_its_fields_compose_down(self):
        # Django creates no table for an abstract model, and a concrete child
        # inherits its fields. Rendering the base as its own table would name a
        # table that does not exist; dropping it would lose a real column.
        self.assertNotIn("blog_timestamped", self.md)
        self.assertIn("| weblog_entry | `created` | DateTimeField | no | no | — |", self.md)

    def test_the_table_reuses_the_shared_entity_header(self):
        self.assertIn("| table | column | type | nullable | pk | fk |", self.md)
        self.assertEqual(CORE.count_concern_entities("data-model", self.md), 6)

    def test_models_py_is_hashed(self):
        self.assertIn("blog/models.py", self.sources)


class SqlModelDataModelTests(unittest.TestCase):
    def setUp(self):
        tmp, self.root = _write_repo(SQLMODEL_FIXTURE)
        self.addCleanup(tmp.cleanup)
        self.md, self.sources = D.extract_python_data_model(self.root, "bionic")

    def test_table_true_is_the_marker_and_the_class_name_is_the_table(self):
        self.assertIn("## SQLModel tables (2)", self.md)
        self.assertIn("| user | `hashed_password` | str | no | no | — |", self.md)

    def test_same_module_base_fields_are_composed_onto_the_table(self):
        # `class User(UserBase, table=True)` declares 3 fields in its own body;
        # the rest live on `UserBase`, which is not itself a table.
        self.assertIn("| user | `email` | str | no | no | — |", self.md)
        self.assertIn("| user | `is_active` | bool | no | no | — |", self.md)
        self.assertIn("| item | `title` | str | no | no | — |", self.md)

    def test_a_base_class_that_is_not_a_table_renders_no_table(self):
        self.assertNotIn("| userbase |", self.md)
        self.assertNotIn("| itembase |", self.md)

    def test_a_relationship_attribute_is_not_a_column(self):
        self.assertNotIn("`items`", self.md)
        self.assertNotIn("`owner`", self.md)

    def test_field_keywords_supply_pk_and_foreign_key(self):
        self.assertIn("| user | `id` | uuid.UUID | no | yes | — |", self.md)
        self.assertIn("| item | `owner_id` | uuid.UUID | no | no | user.id |", self.md)

    def test_an_optional_annotation_is_the_nullable_column(self):
        self.assertIn("| item | `description` | str \\| None | yes | no | — |", self.md)

    def test_the_verdict_is_populated_over_a_rendered_table(self):
        # The acceptance criterion is the rendered table, not the verdict alone:
        # `Verdict.populated` already refuses a zero entity count, so asserting
        # the verdict cannot distinguish a real table from an empty one.
        # user: email + is_active from the base, id + hashed_password of its own.
        # item: title from the base, id + description + owner_id of its own.
        rows = [ln for ln in self.md.splitlines()
                if ln.startswith("| user |") or ln.startswith("| item |")]
        self.assertEqual(len(rows), 8)
        verdict = CORE.concern_verdict("data-model", "python", "python",
                                       self.md, self.sources)
        self.assertEqual(verdict.kind, "populated")
        self.assertEqual(verdict.n_entities, 8)


class DataModelStubTests(unittest.TestCase):
    def test_a_repository_with_no_model_of_any_kind_still_stubs(self):
        tmp, root = _write_repo({
            "pyproject.toml": '[project]\nname = "q"\n',
            "q/__init__.py": "",
            "q/util.py": "class Helper:\n    value = 1\n",
        })
        self.addCleanup(tmp.cleanup)
        md, sources = D.extract_python_data_model(root, "bionic")
        self.assertIn("no extractor", md)
        self.assertEqual(sources, {})


# ══════ ADR-0096 clause 6 — the manifest-anchored package search (U-P3) ══════
# Package detection had two tiers: a root `pyproject.toml` hint that resolves,
# then the root-only union. `fastapi-fullstack` matched neither, so it reported
# ZERO candidates and its module-graph stubbed `precondition_missing` — the
# application is `backend/app`, one directory below where the search looked.
#
# Tier 1b sits between them: scan for a manifest at depth <= 2 and resolve each
# declared name AGAINST ITS OWN DIRECTORY. A single resolving nested declaration
# short-circuits, exactly as tier 1 does.
#
# The tier is anchored on a DECLARATION rather than on depth, and that is the
# whole design. A blanket depth-2 union finds `backend/app` AND `backend/tests`
# on the repository this is aimed at, which reports `ambiguous_package` and
# fixes nothing; and it takes `djangoproject-com` from 16 candidates to 45.

CORPUS_CACHE = Path(__file__).resolve().parent / "arch-corpus" / ".cache"

#: The recorded package set for `djangoproject-com`, verbatim from `corpus.yml`.
#: Sixteen co-equal Django apps at the repository root, which is an honest
#: `ambiguous_package` rather than a silent pick, and stays one under U-P3.
DJANGOPROJECT_PACKAGES = (
    "_sphinx_13448_workaround", "accounts", "aggregator", "blog", "checklists",
    "contact", "dashboard", "djangoproject", "docs", "foundation",
    "fundraising", "legacy", "members", "releases", "svntogit", "tracdb",
)


class NestedManifestPackageTests(unittest.TestCase):
    def test_a_nested_manifest_declaration_resolves_and_short_circuits(self):
        tmp, root = _write_repo({
            "pyproject.toml": '[project]\nname = "nothing-here"\n',
            "backend/pyproject.toml": '[project]\nname = "app"\n',
            "backend/app/__init__.py": "",
            "backend/app/main.py": "",
            "backend/tests/__init__.py": "",
            "backend/tests/test_main.py": "",
        })
        self.addCleanup(tmp.cleanup)
        found = P.detect_packages(root)
        self.assertEqual([name for _dir, name in found], ["app"])
        self.assertEqual(found[0][0], root / "backend" / "app")

    def test_the_declaration_resolves_against_its_own_directory(self):
        # The same name under a different nested directory must not resolve
        # through the repository root.
        tmp, root = _write_repo({
            "svc/pyproject.toml": '[project]\nname = "svc-core"\n',
            "svc/svc_core/__init__.py": "",
            "svc_core/__init__.py": "",
        })
        self.addCleanup(tmp.cleanup)
        found = P.detect_packages(root)
        self.assertEqual([str(d) for d, _n in found], [str(root / "svc" / "svc_core")])

    def test_a_poetry_name_is_read_from_a_nested_manifest(self):
        tmp, root = _write_repo({
            "api/pyproject.toml": '[tool.poetry]\nname = "my-api"\n',
            "api/my_api/__init__.py": "",
        })
        self.addCleanup(tmp.cleanup)
        self.assertEqual([n for _d, n in P.detect_packages(root)], ["my_api"])

    def test_a_manifest_declaring_neither_key_declares_nothing(self):
        """A parsing `pyproject.toml` with no `[project].name` and no
        `[tool.poetry].name` yields no hint, and a `name =` living under some
        unrelated table is NOT one.

        `_pyproject_pkg_name` used to fall through to a
        `^\\s*name\\s*=\\s*["\']([^"\']+)["\']` scan not only when `tomllib`
        raised but whenever the parse SUCCEEDED and neither key was present —
        control just left the `try`. The manifest below parses cleanly and
        declares no package, yet the regex matched the first `name =` in the
        file and returned `'sqlalchemy.*'`: a mypy override pattern, read as a
        distribution name. `detect_packages` short-circuits on the first name
        that resolves, so that is the silent single wrong pick clause 6 forbids,
        produced by a regular expression inside a `parser`-declared concern's
        declared input set.

        The fallback is gone. `tomllib` is stdlib on the pinned floor, so it
        bought nothing. Tier 1 declines here and tier 2's root-only union
        answers instead.
        """
        tmp, root = _write_repo({
            "pyproject.toml": (
                "[build-system]\n"
                'requires = ["hatchling"]\n\n'
                "[[tool.mypy.overrides]]\n"
                'module = ["sqlalchemy.*"]\n'
                'name = "sqlalchemy.*"\n'
            ),
            "top/__init__.py": "",
        })
        self.addCleanup(tmp.cleanup)
        self.assertIsNone(P._pyproject_pkg_name(root, root))
        self.assertEqual([n for _d, n in P.detect_packages(root)], ["top"])

    def test_an_unparseable_manifest_declares_nothing(self):
        """The case the fallback was written for. It still declines — it simply
        declines by returning None rather than by scanning the bytes."""
        tmp, root = _write_repo({
            "pyproject.toml": '[project\nname = "broken"\n',
            "top/__init__.py": "",
        })
        self.addCleanup(tmp.cleanup)
        self.assertIsNone(P._pyproject_pkg_name(root, root))
        self.assertEqual([n for _d, n in P.detect_packages(root)], ["top"])

    def test_a_setup_py_name_is_read_from_a_nested_manifest(self):
        tmp, root = _write_repo({
            "server/setup.py": 'from setuptools import setup\n\nsetup(name="svc")\n',
            "server/svc/__init__.py": "",
        })
        self.addCleanup(tmp.cleanup)
        self.assertEqual([n for _d, n in P.detect_packages(root)], ["svc"])

    def test_a_nested_declaration_that_resolves_to_nothing_falls_through(self):
        tmp, root = _write_repo({
            "backend/pyproject.toml": '[project]\nname = "absent"\n',
            "backend/app/__init__.py": "",
            "top/__init__.py": "",
        })
        self.addCleanup(tmp.cleanup)
        # Tier 1b found no resolving declaration, so tier 2's root-only union
        # answers — and it does NOT reach `backend/app`.
        self.assertEqual([n for _d, n in P.detect_packages(root)], ["top"])

    def test_the_scan_stops_at_depth_two(self):
        tmp, root = _write_repo({
            "a/b/c/pyproject.toml": '[project]\nname = "deep"\n',
            "a/b/c/deep/__init__.py": "",
            "top/__init__.py": "",
        })
        self.addCleanup(tmp.cleanup)
        self.assertEqual([n for _d, n in P.detect_packages(root)], ["top"])

    def test_the_root_hint_still_wins_before_any_nested_manifest(self):
        tmp, root = _write_repo({
            "pyproject.toml": '[project]\nname = "rooted"\n',
            "rooted/__init__.py": "",
            "backend/pyproject.toml": '[project]\nname = "app"\n',
            "backend/app/__init__.py": "",
        })
        self.addCleanup(tmp.cleanup)
        self.assertEqual([n for _d, n in P.detect_packages(root)], ["rooted"])

    def test_two_resolving_nested_declarations_are_both_reported(self):
        # Clause 6 forbids a silent single pick, so a genuine multi-declaration
        # repository reports both and takes `ambiguous_package`.
        tmp, root = _write_repo({
            "one/pyproject.toml": '[project]\nname = "alpha"\n',
            "one/alpha/__init__.py": "",
            "two/pyproject.toml": '[project]\nname = "beta"\n',
            "two/beta/__init__.py": "",
        })
        self.addCleanup(tmp.cleanup)
        self.assertEqual([n for _d, n in P.detect_packages(root)], ["alpha", "beta"])

    def test_tier_two_is_unchanged_and_stays_root_only(self):
        tmp, root = _write_repo({
            "src/alpha/__init__.py": "",
            "src/beta/__init__.py": "",
            "gamma/__init__.py": "",
            "backend/delta/__init__.py": "",
        })
        self.addCleanup(tmp.cleanup)
        self.assertEqual([n for _d, n in P.detect_packages(root)],
                         ["alpha", "beta", "gamma"])


class CorpusPackageDetectionTests(unittest.TestCase):
    """Measured against the two pinned python clones, skipped without the cache."""

    def _cached(self, name: str) -> Path:
        root = CORPUS_CACHE / name
        if not root.is_dir():
            self.skipTest(f"corpus cache absent: run arch-corpus/fetch.py ({name})")
        return root

    def test_fastapi_fullstack_resolves_one_package_by_declaration(self):
        root = self._cached("fastapi-fullstack")
        found = P.detect_packages(root)
        self.assertEqual([n for _d, n in found], ["app"])
        self.assertEqual(found[0][0], root / "backend" / "app")

    def test_djangoproject_com_still_names_exactly_the_sixteen_recorded_packages(self):
        """The direct guard on U-P3.

        The cumulative classifier can no longer isolate a package-widening
        regression here: by this unit `djangoproject-com` legitimately carries
        allowed extraction content from U-P1 and U-P2, so it is on the
        allow-list and a module-graph movement would be waved through with it.
        This assertion is what replaces that guard.

        The repository has no nested manifest at any depth and a root manifest
        whose declared name resolves to no directory, so tier 1b finds nothing
        and tier 2's root-only union answers with the same 16 co-equal Django
        apps. A blanket depth-2 union would answer 45.
        """
        root = self._cached("djangoproject-com")
        self.assertEqual(tuple(n for _d, n in P.detect_packages(root)),
                         DJANGOPROJECT_PACKAGES)


class ContainmentTests(unittest.TestCase):
    """SEC-1 — every python-pack read goes through `core._safe_read_bytes`.

    ADR-0068 clause 6 and ADR-0069 clause 6 require the pack to "resolve-then-
    contains each candidate under the repo root before opening it". Six reads in
    this pack did not, and the consequence was not merely an off-root read: the
    `_rel(root, py)` call that records provenance sits OUTSIDE the `try`, so a
    symlinked `.py` whose target escapes the root parsed successfully, matched,
    and then raised `ValueError: ... is not in the subpath of ...`. End to end
    that is `derive-arch.py --dry-run` exiting 2 with empty stdout — the drift
    gate and its CI workflow taken out by a pull request that adds one file.

    Each test below materializes exactly that shape and asserts the derive
    COMPLETES. The off-root content must also be absent, because the symlink is
    the pack's only route to it.
    """

    def require_symlinks(self, root: Path):
        target = root / ".symlink-probe-target"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
        link = root / ".symlink-probe"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable on this platform/filesystem")
        link.unlink()
        target.unlink()

    def _outside(self, root: Path, name: str, text: str) -> Path:
        """A file beside the repo root but OUTSIDE it, in the same tempdir."""
        p = root.parent / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_symlinked_py_escaping_root_does_not_crash_the_derive(self):
        tmp, root = _write_repo({
            "pyproject.toml": '[project]\nname = "app"\n',
            "app/__init__.py": "",
            "app/models.py": (
                "from sqlalchemy import Column, Integer\n"
                "from sqlalchemy.orm import DeclarativeBase\n"
                "class Base(DeclarativeBase): pass\n"
                "class Widget(Base):\n"
                "    __tablename__ = 'widgets'\n"
                "    id = Column(Integer, primary_key=True)\n"
            ),
        })
        self.addCleanup(tmp.cleanup)
        self.require_symlinks(root)
        secret = self._outside(root, "escaped_models.py", (
            "from sqlalchemy import Column, Integer\n"
            "from sqlalchemy.orm import DeclarativeBase\n"
            "class Base(DeclarativeBase): pass\n"
            "class Secret(Base):\n"
            "    __tablename__ = 'offroot_secrets'\n"
            "    id = Column(Integer, primary_key=True)\n"
        ))
        (root / "app" / "leak.py").symlink_to(secret)
        (root / "bionic").mkdir()

        # The whole spine builds — this raised ValueError before the fix.
        spine = D._build(root, "bionic")
        self.assertNotIn("offroot_secrets", spine["data-model.md"],
                         "an off-root symlink target reached the rendered spine")
        self.assertIn("widgets", spine["data-model.md"])

    def test_symlinked_openapi_json_escaping_root_is_not_read(self):
        tmp, root = _write_repo({"pyproject.toml": '[project]\nname = "app"\n',
                                 "app/__init__.py": ""})
        self.addCleanup(tmp.cleanup)
        self.require_symlinks(root)
        spec = self._outside(root, "escaped_openapi.json", json_dumps_spec())
        (root / "openapi.json").symlink_to(spec)

        md, sources = P.extract_python_api_surface(root, "bionic")
        self.assertNotIn("offroot", md,
                         "an off-root openapi.json reached the api-surface")
        self.assertEqual(sources, {})

    def test_symlinked_alembic_version_escaping_root_is_skipped(self):
        tmp, root = _write_repo({
            "pyproject.toml": '[project]\nname = "app"\n',
            "app/__init__.py": "",
            "alembic/versions/aaa_root.py": (
                '"""root migration"""\nrevision = "aaa"\ndown_revision = None\n'
            ),
        })
        self.addCleanup(tmp.cleanup)
        self.require_symlinks(root)
        escaped = self._outside(root, "escaped_migration.py", (
            '"""offroot migration"""\nrevision = "zzz"\ndown_revision = "aaa"\n'
        ))
        (root / "alembic" / "versions" / "bbb_leak.py").symlink_to(escaped)

        rows, sources, _note = P._scan_alembic_timeline(root)
        self.assertNotIn("zzz", [r[0] for r in rows],
                         "an off-root migration reached the timeline")
        self.assertEqual(sorted(sources), ["alembic/versions/aaa_root.py"])

    def test_has_migration_ignores_a_symlink_escaping_the_root(self):
        tmp, root = _write_repo({"pyproject.toml": '[project]\nname = "app"\n',
                                 "app/__init__.py": ""})
        self.addCleanup(tmp.cleanup)
        self.require_symlinks(root)
        (root / "alembic" / "versions").mkdir(parents=True)
        escaped = self._outside(root, "escaped_rev.py", (
            'revision = "zzz"\ndown_revision = None\n'
        ))
        (root / "alembic" / "versions" / "leak.py").symlink_to(escaped)

        self.assertFalse(
            P._has_migration(root, root / "alembic" / "versions"),
            "an off-root file was accepted as the evidence a versions/ dir is real",
        )

    def test_symlinked_manifest_dir_escaping_root_declares_nothing(self):
        # `_manifest_dirs` walks `parent.iterdir()` filtered by `c.is_dir()`,
        # which is TRUE for a symlinked directory — so the pyproject.toml /
        # setup.py reads behind it are the pack's fifth and sixth ways out.
        tmp, root = _write_repo({"README.md": "no root manifest\n"})
        self.addCleanup(tmp.cleanup)
        self.require_symlinks(root)
        outside = root.parent / "outside_pkg"
        (outside / "svc").mkdir(parents=True)
        (outside / "pyproject.toml").write_text('[project]\nname = "svc"\n',
                                                encoding="utf-8")
        (outside / "svc" / "__init__.py").write_text("", encoding="utf-8")
        (root / "vendored").symlink_to(outside, target_is_directory=True)

        self.assertEqual(P.detect_packages(root), (),
                         "a manifest outside the repo root declared a package")

    def test_setup_py_symlink_escaping_root_declares_nothing(self):
        tmp, root = _write_repo({"README.md": "no root manifest\n"})
        self.addCleanup(tmp.cleanup)
        self.require_symlinks(root)
        outside = root.parent / "outside_setup"
        (outside / "svc").mkdir(parents=True)
        (outside / "setup.py").write_text('setup(name="svc")\n', encoding="utf-8")
        (outside / "svc" / "__init__.py").write_text("", encoding="utf-8")
        (root / "vendored").symlink_to(outside, target_is_directory=True)

        self.assertEqual(P.detect_packages(root), (),
                         "a setup.py outside the repo root declared a package")


def json_dumps_spec() -> str:
    import json as _json
    return _json.dumps({
        "openapi": "3.0.0",
        "paths": {"/offroot": {"get": {"tags": ["offroot"], "summary": "offroot"}}},
    })


if __name__ == "__main__":
    unittest.main(verbosity=2)
