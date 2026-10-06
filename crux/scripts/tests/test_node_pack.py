"""Tests for the Node.js arch stack pack — dev modules 1 + 2 of PB-0066 (ADR-0068).

Dev module 1 (point 1, api-surface): detection → node, resolution through the
ADR-0066 seam, the committed-OpenAPI-before-route-scan ordering, the
receiver-agnostic verb-call scan with the `/`-leading string guard that excludes
`Map.get`/`cache.get` decoys, the NestJS prefix+method path pairing, the
(method, path, handler) sort order, cell escaping, the no-route stub, per-source
drift-gating, and byte-stable re-derivation. Also a regression on the point-4
helper rename (`_MAX_FILE_BYTES`/`_node_ids`).

Dev module 2 (points 2 + 3): the data-model probes (Prisma → TypeORM →
Sequelize, first match wins) — the Prisma scalar-list-vs-relation rule, the
`@relation` FK on its named scalar column, the enum note, the `@@map` residual,
plus the TypeORM and Sequelize entity tables — and the module-graph (explicit
TS/JS import graph, resolve-or-drop): the extension-ladder first-hit, tsconfig
`@/*` alias resolution, the dropped bare package, the comment-only strip, and
the no-double-count of a string-literal-looking import. Escaping + drift +
determinism carry through both.

Stdlib only. The committed `fixtures/nodeservice/` fixture is copied to a tempdir
before any mutation, so it is never altered.
"""

from __future__ import annotations

import importlib
import json
import re
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
NODE = importlib.import_module("crux.arch.packs.node")

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "nodeservice"


def _copy(fixture: Path = FIXTURE):
    tmp = tempfile.TemporaryDirectory()
    dest = Path(tmp.name) / "repo"
    shutil.copytree(fixture, dest)
    (dest / "bionic").mkdir()
    return tmp, dest


def _bare_node(tmp_root: Path):
    """A node repo (package.json marker) with a single JS file the caller fills."""
    (tmp_root / "package.json").write_text('{"name": "x", "version": "0.0.0"}\n')
    return tmp_root


# ───────────────────────── detection + resolution ──────────────────────────

class DetectionAndResolutionTests(unittest.TestCase):
    def test_detects_node_stack(self):
        # package.json present, no crux/python/ruby markers → auto-detects node.
        self.assertEqual(D.detect_stack(FIXTURE, None), "node")

    def test_resolution_picks_openapi_when_present(self):
        # A committed openapi.json → api-surface resolves to the openapi probe (1st).
        self.assertIs(
            D.resolve_extractor("api-surface", FIXTURE, "bionic", None, pack_name="node"),
            D.extract_node_api_surface_openapi,
        )

    def test_resolution_falls_to_route_scan_without_openapi(self):
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        (root / "openapi.json").unlink()
        self.assertIs(
            D.resolve_extractor("api-surface", root, "bionic", None, pack_name="node"),
            D.extract_node_api_surface_routes,
        )

    def test_data_model_resolves_to_prisma(self):
        # dev module 2: data-model is populated — Prisma leads the ordered probes.
        self.assertIs(
            D.resolve_extractor("data-model", FIXTURE, "bionic", None, pack_name="node"),
            D.extract_node_data_model_prisma,
        )

    def test_module_graph_resolves_to_node_extractor(self):
        # dev module 2: module-graph is the explicit TS/JS import graph.
        self.assertIs(
            D.resolve_extractor("module-graph", FIXTURE, "bionic", None, pack_name="node"),
            D.extract_node_module_graph,
        )

    def test_decision_index_still_universal(self):
        ext = D.resolve_extractor("decision-index", FIXTURE, "bionic", None, pack_name="node")
        # The universal decision-index probe is always bound (fires via _always).
        self.assertTrue(callable(ext))


# ─────────────────────── api-surface ordering (OpenAPI) ─────────────────────

class ApiSurfaceOrderingTests(unittest.TestCase):
    def test_openapi_probe_precedes_route_scan(self):
        # The committed openapi.json wins over the route scan (ordered probes).
        ext = D.resolve_extractor("api-surface", FIXTURE, "bionic", None, pack_name="node")
        self.assertIs(ext, D.extract_node_api_surface_openapi)
        md, sources = ext(FIXTURE, "bionic")
        self.assertIn("openapi.json", sources)
        # No source file was scanned for routes when OpenAPI wins.
        self.assertNotIn("src/routes/users.js", sources)
        # Rendered through the SHARED _render_openapi (same header as other packs).
        self.assertIn("_Derived from `openapi.json`._", md)
        self.assertIn("| GET | `/status` |", md)

    def test_node_openapi_conventions_detected(self):
        # docs/openapi.json + swagger.json are Node-convention candidates.
        for rel in ("docs/openapi.json", "swagger.json"):
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            root = _bare_node(Path(tmp.name))
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(
                {"openapi": "3.0.0",
                 "paths": {"/ping": {"get": {"tags": ["h"], "summary": "Ping"}}}}))
            self.assertTrue(D._detect_node_openapi(root), rel)
            md, sources = D.extract_node_api_surface_openapi(root, "bionic")
            self.assertIn(rel, sources)
            self.assertIn("| GET | `/ping` | Ping |", md)


# ───────────────────────── api-surface (route scan) ─────────────────────────

class RouteScanTests(unittest.TestCase):
    def setUp(self):
        self.md, self.sources = D.extract_node_api_surface_routes(FIXTURE, "bionic")

    def test_static_scan_label(self):
        self.assertIn("static route scan", self.md.lower())

    def test_express_verb_calls_with_named_handler(self):
        # app.get('/users', listUsers) + router.post('/users/:id', updateUser).
        self.assertIn("| GET | `/users` | listUsers |", self.md)
        self.assertIn("| POST | `/users/:id` | updateUser |", self.md)

    def test_fastify_inline_arrow_handler_is_dash(self):
        # fastify.get('/health', async (req, reply) => {...}) → handler unrecoverable.
        self.assertIn("| GET | `/health` | — |", self.md)

    def test_slash_guard_excludes_decoys(self):
        # Map.get("x") and cache.get(key) must NOT produce a route.
        self.assertNotIn("| GET | `x`", self.md)
        self.assertNotIn("registry", self.md)
        self.assertNotIn("cache", self.md)
        # No route whose path does not start with "/" exists at all.
        for line in self.md.splitlines():
            if line.startswith("| GET |") or line.startswith("| POST |"):
                path = [c.strip(" `") for c in line.strip("|").split("|")][1]
                self.assertTrue(path.startswith("/"), line)

    def test_commented_route_dropped(self):
        # // app.get("/ghost", ghostHandler) is stripped before the scan.
        self.assertNotIn("/ghost", self.md)
        self.assertNotIn("ghostHandler", self.md)

    def test_nestjs_prefix_plus_method_path(self):
        # @Controller('admin') + @Get('stats') → GET /admin/stats, handler getStats.
        self.assertIn("| GET | `/admin/stats` | getStats |", self.md)

    def test_sorted_by_method_path_handler(self):
        rows = [ln for ln in self.md.splitlines()
                if ln.startswith(("| GET |", "| POST |", "| PUT |",
                                  "| PATCH |", "| DELETE |", "| ALL |"))]
        keys = []
        for ln in rows:
            parts = [c.strip(" `") for c in ln.strip("|").split("|")]
            keys.append((parts[0], parts[1], parts[2]))
        self.assertEqual(keys, sorted(keys))

    def test_package_json_labels_only(self):
        # Declared frameworks label the table; package.json is hashed (it labels).
        self.assertIn("Declared route frameworks", self.md)
        self.assertIn("express", self.md)
        self.assertIn("package.json", self.sources)

    def test_every_scanned_source_hashed(self):
        for rel in ("src/routes/users.js", "src/routes/health.ts",
                    "src/admin/admin.controller.ts"):
            self.assertIn(rel, self.sources)

    def test_cell_escaping_of_hostile_path(self):
        # A route path carrying a pipe must render escaped, not break the table.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = _bare_node(Path(tmp.name))
        (root / "r.js").write_text('app.get("/a|b", h);\n')
        md, _ = D.extract_node_api_surface_routes(root, "bionic")
        self.assertIn("a\\|b", md)
        self.assertNotIn("| `/a|b` |", md)


class RouteParserTests(unittest.TestCase):
    """U-N1: the route reader is a tree-sitter parse, not a regular expression.

    ADR-0096 clause 1 prohibits matching a regular expression against authored
    source for a concern that declares the input class `parser`, and the node
    pack's api-surface concern declares one. The corpus measured what the regex
    cost, and each test below is one of those measurements rather than a
    stylistic preference.
    """

    def _md(self, source: str, name: str = "r.js") -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = _bare_node(Path(tmp.name))
        (root / name).write_text(source)
        md, _ = D.extract_node_api_surface_routes(root, "bionic")
        return md

    def test_the_chained_route_form_yields_one_row_per_verb(self):
        """`router.route(p).get(h).post(h)` — the form the regex missed entirely.

        The path sits on `.route()` and the verb on a call whose receiver is the
        previous call in the chain, so a pattern anchored on `<ident>.<verb>(`
        never sees a path argument and drops the declaration. Five of
        express-boilerplate's fourteen routes are written this way.
        """
        md = self._md(
            "const router = require('express').Router();\n"
            "router\n  .route('/items')\n"
            "  .get(ctrl.list)\n  .post(ctrl.create);\n"
            "router.route('/items/:id').delete(ctrl.remove);\n")
        self.assertIn("| GET | `/items` | ctrl.list |", md)
        self.assertIn("| POST | `/items` | ctrl.create |", md)
        self.assertIn("| DELETE | `/items/:id` | ctrl.remove |", md)

    def test_the_handler_is_the_last_argument_not_the_second(self):
        """Middleware between the path and the handler is the common Express shape.

        The regex read the argument immediately after the path and reported `—`
        whenever that was a `validate(...)` call — which is every route in
        express-boilerplate's auth router. The handler is the LAST argument.
        """
        md = self._md("router.post('/register', validate(v.r), ctrl.register);\n")
        self.assertIn("| POST | `/register` | ctrl.register |", md)

    def test_a_non_identifier_last_argument_is_still_a_dash(self):
        """An inline arrow or a call expression yields no recoverable name.

        `—` is the honest cell; inventing one from the call's source text would
        be the same class of error as rendering an interpolation placeholder.
        """
        md = self._md("app.get('/a', (req, res) => res.end());\n"
                      "app.get('/b', swaggerUi.setup(specs, {}));\n")
        self.assertIn("| GET | `/a` | — |", md)
        self.assertIn("| GET | `/b` | — |", md)

    def test_an_interpolated_path_is_dropped_and_named_in_the_residual(self):
        """Adjudication 3: a path with no static value is dropped, never guessed.

        This is the node pack's measured `/api/${routeName}/:id` defect. The
        path is composed at require-time; the reader never executes the
        application, so there is no static value to render. Rendering the
        interpolation's SOURCE TEXT as if it were a path is what the baseline
        did, and it produced three wrong rows on one corpus repository.
        """
        md = self._md(
            "app.get('/', home);\n"
            "for (const n of names) {\n"
            "  app.get(`/api/${n}`, h1);\n"
            "  app.put(`/api/${n}/:id`, h2);\n"
            "}\n")
        self.assertIn("| GET | `/` | home |", md)
        self.assertNotIn("${", md)                      # never a placeholder.
        self.assertNotIn("| PUT |", md)                 # the row is DROPPED.
        self.assertIn("## Residuals", md)
        self.assertIn("r.js", md)                       # and NAMED, with its file.

    def test_a_static_template_literal_path_still_resolves(self):
        """A backtick string with no substitution HAS a static value.

        Dropping it would be over-correction: the prohibition is on guessing a
        value that does not exist, not on the quote character.
        """
        md = self._md("app.get(`/static/path`, h);\n")
        self.assertIn("| GET | `/static/path` | h |", md)

    def test_nest_decorators_without_arguments_pair_to_root(self):
        md = self._md(
            "@Controller()\nexport class A {\n"
            "  @Get()\n  getHello(): string { return ''; }\n"
            "  @Get('hello/:name')\n  getName(): string { return ''; }\n}\n",
            name="a.controller.ts")
        self.assertIn("| GET | `/` | getHello |", md)
        self.assertIn("| GET | `/hello/:name` | getName |", md)

    def test_a_call_receiver_is_not_a_route_declaration(self):
        """A supertest assertion is not a route, and the AST made that easy to miss.

        The pre-parser reader required a bare identifier before the verb, so
        `request(app.getHttpServer()).get('/')` never matched. Accepting any
        member-expression receiver — the obvious AST translation — added four
        rows to nestjs-prisma-starter, which has two routes. Measured, not
        theorised: it is why the receiver guard is explicit.
        """
        md = self._md(
            "describe('app', () => {\n"
            "  it('works', () => request(app.getHttpServer()).get('/ping'));\n"
            "  it('gql', () => request(app.getHttpServer()).post('/graphql'));\n"
            "});\n", name="app.e2e-spec.ts")
        self.assertNotIn("/ping", md)
        self.assertNotIn("/graphql", md)

    def test_a_non_path_string_argument_is_silent_not_a_named_drop(self):
        """The residual lists dropped ROUTES, not every unresolvable string.

        `configService.get('JWT_SECRET')` and `get(`${p}_SECRET`)` are the decoys
        the `/`-leading guard has always excluded silently. Naming them as
        dropped routes filled nestjs-prisma-starter's residual with twelve
        declarations that were never routes — a residual nobody can act on is
        worse than none, because it reads as extraction debt.
        """
        md = self._md(
            "const a = config.get('JWT_SECRET');\n"
            "const b = config.get(`${prefix}_SECRET`);\n"
            "app.get('/real', h);\n")
        self.assertIn("| GET | `/real` | h |", md)
        self.assertNotIn("SECRET", md)
        self.assertNotIn("## Residuals", md)

    def test_the_api_surface_concern_declares_its_grammar(self):
        """Clause 1's pin: the declaration is what `core.parser_pins` records.

        A reader that used the grammar without declaring it would leave
        `_meta/manifest.json` silent about the thing that decided the bytes,
        which is the ledger failure clause 1 exists to prevent.
        """
        self.assertEqual(
            NODE.INPUT_CLASSES["api-surface"].parser,
            ("tree_sitter", "tree_sitter_typescript"))


class GraphQlResidualTests(unittest.TestCase):
    """U-N4: the GraphQL debt carried VISIBLY in the rendered spine, not just in
    `corpus.yml`. api-surface's `expected` sentence names GraphQL among the
    surfaces the concern looks for, and a `*.graphql`/`*.gql` file or a
    `@Resolver`/`@Query`/`@Mutation` decorator renders a residual naming the
    files it found -- no new probe, no new dependency, no change to the verdict.
    """

    def _md(self, files: dict) -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = _bare_node(Path(tmp.name))
        for name, content in files.items():
            fp = root / name
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content)
        md, _ = D.extract_node_api_surface_routes(root, "bionic")
        return md

    def test_schema_and_resolver_render_residual_and_no_route(self):
        md = self._md({
            "src/schema.graphql": "type Query {\n  hello: String\n}\n",
            "src/app.resolver.ts": (
                "@Resolver()\nexport class AppResolver {\n"
                "  @Query(() => String)\n  hello(): string { return \'\'; }\n}\n"),
        })
        self.assertIn("GraphQL", md)
        self.assertIn("src/schema.graphql", md)
        self.assertIn("src/app.resolver.ts", md)
        self.assertNotIn("## Routes", md)

    def test_no_graphql_surface_renders_no_residual(self):
        """The intro line always names GraphQL among the surfaces the concern
        looks for (the same unconditional shape OpenAPI/Express/Fastify/NestJS
        already have there); what must NOT appear absent a real GraphQL
        surface is the RESIDUAL naming files -- there are none to name."""
        md = self._md({"src/app.controller.ts": (
            "@Controller(\'users\')\nexport class C {\n"
            "  @Get()\n  list(): string[] { return []; }\n}\n")})
        self.assertNotIn("## Residuals", md)

    def test_rendered_intro_names_graphql_among_surfaces_looked_for(self):
        """Declared where it cannot move `wagtail-bakerydemo`\'s pinned golden.

        `INPUT_CLASSES["api-surface"].expected` is consumed verbatim by
        `core.concern_verdict` for EVERY node-pack repo\'s `precondition_missing`
        stub line, including a Django app misdetected as node -- so editing
        that shared field would move a golden the corpus gate pins
        byte-identical and never allow-lists. The GraphQL declaration goes in
        the extractor\'s own populated-branch intro line instead, which a true
        stub never reaches.
        """
        md = self._md({"src/app.controller.ts": (
            "@Controller(\'users\')\nexport class C {\n"
            "  @Get()\n  list(): string[] { return []; }\n}\n")})
        self.assertIn("GraphQL", md)


class MountCompositionTests(unittest.TestCase):
    """U-N2: a route's path is composed through its mount, or it is not composed.

    The baseline recorded that the reader "composes no mount prefix, so the
    paths it renders are relative to each router file rather than to the
    application" — nine wrong paths on one corpus repository. Composition closes
    that, under one rule taken from adjudication 3: **a composed path is never a
    guess.** A mount whose prefix the reader cannot evaluate contributes a
    residual and leaves its routes at their file-relative path; it never
    contributes half a prefix.
    """

    def _repo(self, files: dict):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = _bare_node(Path(tmp.name))
        for rel, body in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
        md, _ = D.extract_node_api_surface_routes(root, "bionic")
        return md

    def test_an_identifier_bound_to_a_require_composes(self):
        md = self._repo({
            "app.js": "const routes = require('./routes/index');\n"
                      "app.use('/v1', routes);\n",
            "routes/index.js": "router.get('/ping', ctrl.ping);\n",
        })
        self.assertIn("| GET | `/v1/ping` | ctrl.ping |", md)

    def test_an_inline_require_composes(self):
        md = self._repo({
            "app.js": "app.use('/api', require('./r'));\n",
            "r.js": "router.post('/items', ctrl.create);\n",
        })
        self.assertIn("| POST | `/api/items` | ctrl.create |", md)

    def test_the_mount_table_form_composes_transitively(self):
        """The shape express-boilerplate actually uses, and two levels deep.

        `app.use('/v1', routes)` mounts an index module, which mounts each
        sub-router through a `forEach` over a static array of object literals.
        Every segment of `/v1/auth/register` has a static value in the AST, so
        composing it asserts nothing the source does not already say.
        """
        md = self._repo({
            "app.js": "const routes = require('./routes/v1');\n"
                      "app.use('/v1', routes);\n",
            "routes/v1/index.js":
                "const authRoute = require('./auth.route');\n"
                "const userRoute = require('./user.route');\n"
                "const defaultRoutes = [\n"
                "  { path: '/auth', route: authRoute },\n"
                "  { path: '/users', route: userRoute },\n"
                "];\n"
                "defaultRoutes.forEach((route) => {\n"
                "  router.use(route.path, route.route);\n"
                "});\n",
            "routes/v1/auth.route.js":
                "router.post('/register', validate(v.r), authController.register);\n",
            "routes/v1/user.route.js":
                "router.route('/').get(userController.getUsers);\n",
        })
        self.assertIn("| POST | `/v1/auth/register` | authController.register |", md)
        self.assertIn("| GET | `/v1/users` | userController.getUsers |", md)

    def test_a_mount_prefix_with_no_static_value_is_named_not_guessed(self):
        """Outside the closed form set: residual, and the routes stay file-relative.

        Rendering `/v1/x` here would assert the unknown segment is empty, and
        rendering `/x` asserts the whole chain is. The first is a guess; the
        second is the declared fallback, and the residual is what stops it being
        read as the application's real path.
        """
        md = self._repo({
            "app.js": "const sub = require('./sub');\n"
                      "app.use(computePrefix(), sub);\n",
            "sub.js": "router.get('/x', h);\n",
        })
        self.assertIn("| GET | `/x` | h |", md)          # file-relative fallback.
        self.assertNotIn("/v1/x", md)
        self.assertIn("## Residuals", md)
        self.assertIn("app.js", md)

    def test_middleware_use_calls_are_silent_not_residuals(self):
        """`app.use(express.json())` mounts no in-repo module and is not a finding.

        The residual lists mounts whose PREFIX could not be evaluated, not every
        `.use` call in the application — the same discipline that keeps
        `config.get('SECRET')` out of the dropped-path residual.
        """
        md = self._repo({
            "app.js": "app.use(express.json());\napp.use(helmet());\n"
                      "app.get('/health', h);\n",
        })
        self.assertIn("| GET | `/health` | h |", md)
        self.assertNotIn("## Residuals", md)

    def test_a_module_mounted_at_two_prefixes_is_not_composed(self):
        """Two incoming mounts, two right answers, so the reader picks neither.

        One row cannot carry both paths, and choosing the first is a guess
        dressed as determinism.
        """
        md = self._repo({
            "app.js": "const sub = require('./sub');\n"
                      "app.use('/a', sub);\napp.use('/b', sub);\n",
            "sub.js": "router.get('/x', h);\n",
        })
        self.assertIn("| GET | `/x` | h |", md)
        self.assertNotIn("/a/x", md)
        self.assertNotIn("/b/x", md)
        self.assertIn("## Residuals", md)


class RouteStubTests(unittest.TestCase):
    def test_stub_when_no_openapi_and_no_routes(self):
        # JS sources exist (detect fires) but hold only decoys → the stub.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = _bare_node(Path(tmp.name))
        (root / "util.js").write_text(
            'const m = new Map();\nm.get("x");\ncache.get(key);\n')
        ext = D.resolve_extractor("api-surface", root, "bionic", None, pack_name="node")
        self.assertIs(ext, D.extract_node_api_surface_routes)
        md, sources = ext(root, "bionic")
        self.assertIn("no extractor", md)
        # The scanned decoy file is still hashed so a later added route drifts.
        self.assertIn("util.js", sources)

    def test_no_js_sources_resolves_to_stub_extractor(self):
        # No JS/TS at all → the route probe's detect is False → generic stub.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = _bare_node(Path(tmp.name))
        ext = D.resolve_extractor("api-surface", root, "bionic", None, pack_name="node")
        md, sources = ext(root, "bionic")
        self.assertIn("no extractor", md)
        self.assertEqual(sources, {})


# ─────────────────────── safety (point 6) ──────────────────────────────────

class SafetyTests(unittest.TestCase):
    def test_oversize_file_skipped_and_residual_recorded(self):
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        big = root / "src" / "huge.js"
        big.write_text('app.get("/big", h);\n// ' + ("x" * (2 * 1024 * 1024 + 10)))
        entries, sources, residuals = D._scan_node_files(root)
        rels = [r for r, _ in entries]
        self.assertNotIn("src/huge.js", rels)             # not scanned.
        self.assertNotIn("src/huge.js", sources)          # not hashed.
        self.assertTrue(any("2 MB bound" in r for r in residuals))

    def test_symlink_escaping_root_is_skipped(self):
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        outside = Path(tmp.name) / "outside_secret.js"
        outside.write_text('app.get("/secret", leak);\n')
        link = root / "src" / "evil.js"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        md, sources = D.extract_node_api_surface_routes(root, "bionic")
        self.assertNotIn("/secret", md)                   # escaping target not read.
        self.assertNotIn("src/evil.js", sources)          # nor hashed.

    def test_aggregate_file_cap_truncates_with_residual(self):
        import unittest.mock
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        with unittest.mock.patch.object(NODE, "_MAX_SCAN_FILES", 1):
            _, _, residuals = D._scan_node_files(root)
        self.assertTrue(any("Aggregate scan cap" in r for r in residuals))


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

    def test_openapi_mutation_drifts(self):
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        D.derive(root, "bionic")
        spec = root / "openapi.json"
        spec.write_text(spec.read_text().replace("/status", "/state"))
        self.assertIn("bionic/arch/api-surface.md", D.dry_run(root, "bionic"))

    def test_route_file_mutation_drifts(self):
        # Remove openapi.json so the route scan is the active probe, derive, then
        # mutate a route file → api-surface.md drifts (the scanned file is hashed).
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        (root / "openapi.json").unlink()
        D.derive(root, "bionic")
        users = root / "src" / "routes" / "users.js"
        users.write_text(users.read_text().replace('"/users"', '"/people"'))
        self.assertIn("bionic/arch/api-surface.md", D.dry_run(root, "bionic"))


# ─────────── point-4 rename regression (helpers are stack-neutral) ──────────

class RenameRegressionTests(unittest.TestCase):
    def test_renamed_helpers_exist(self):
        self.assertTrue(hasattr(D, "_MAX_FILE_BYTES"))
        self.assertTrue(hasattr(D, "_node_ids"))
        self.assertEqual(D._MAX_FILE_BYTES, 2 * 1024 * 1024)

    def test_old_helper_names_gone(self):
        self.assertFalse(hasattr(D, "_MAX_RUBY_BYTES"))
        self.assertFalse(hasattr(D, "_ruby_node_ids"))

    def test_node_ids_behaviour_unchanged(self):
        # The former _ruby_node_ids collision-suffix contract still holds.
        ids = D._node_ids(["A_b", "A:b"])
        self.assertEqual(ids["A:b"], "A_b")
        self.assertEqual(ids["A_b"], "A_b_2")


# ───────────────────── data-model: Prisma (point 2) ─────────────────────────

def _row_cells(line: str) -> list:
    return [c.strip(" `") for c in line.strip("|").split("|")]


class PrismaDataModelTests(unittest.TestCase):
    def setUp(self):
        self.md, self.sources = D.extract_node_data_model_prisma(FIXTURE, "bionic")

    def _rows(self):
        return [_row_cells(ln) for ln in self.md.splitlines()
                if ln.startswith("| ") and "---" not in ln and "table" not in ln]

    def test_scalar_list_is_a_column(self):
        # `tags String[]` is a scalar list → a COLUMN with type `String[]`.
        self.assertIn("| User | `tags` | String[] | no | — | — |", self.md)

    def test_model_field_is_not_a_column(self):
        # `posts Post[]` is a model-typed field → a RELATION, never a table row.
        rows = self._rows()
        self.assertFalse(any(r[0] == "User" and r[1] == "posts" for r in rows))
        self.assertFalse(any(r[0] == "Post" and r[1] == "author" for r in rows))
        # …but it is listed in the relations note.
        self.assertIn("`posts` → `Post[]`", self.md)
        self.assertIn("`author` → `User`", self.md)

    def test_fk_on_named_scalar_column(self):
        # @relation(fields: [authorId], references: [id]) → the SCALAR column
        # `authorId` carries fk = User, not the relation field `author`.
        self.assertIn("| Post | `authorId` | Int | no | — | User |", self.md)

    def test_nullable_and_default_cells(self):
        self.assertIn("| User | `name` | String | yes | — | — |", self.md)   # `?`
        self.assertIn("| User | `role` | Role | no | USER | — |", self.md)   # enum + @default
        self.assertIn("| User | `id` | Int | no | autoincrement() | — |", self.md)

    def test_id_and_unique_indexes_note(self):
        self.assertIn("`id` primary key", self.md)
        self.assertIn("`email` unique", self.md)

    def test_enum_note(self):
        self.assertIn("## Enums", self.md)
        self.assertIn("`Role`: `USER`, `ADMIN`", self.md)

    def test_atat_map_residual(self):
        # @@map("sessions") is a named residual (declared name `Session` rendered).
        self.assertIn("## Residuals", self.md)
        self.assertIn("model `Session` → `sessions`", self.md)
        # The declared name is what appears in the entity table, not the DB name.
        self.assertIn("| Session | `token` |", self.md)
        rows = self._rows()
        self.assertFalse(any(r[0] == "sessions" for r in rows))

    def test_schema_prisma_hashed(self):
        self.assertIn("prisma/schema.prisma", self.sources)

    def test_sorted_by_model_then_field(self):
        rows = self._rows()
        keys = [(r[0], r[1]) for r in rows]
        self.assertEqual(keys, sorted(keys))

    def test_default_pipe_and_backtick_escaped(self):
        # A hostile Prisma default with a pipe/backtick must render escaped.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = _bare_node(Path(tmp.name))
        (root / "prisma").mkdir()
        (root / "prisma" / "schema.prisma").write_text(
            'model Widget {\n'
            '  id   Int    @id @default(autoincrement())\n'
            '  code String @default("a|b`c")\n'
            '}\n')
        md, _ = D.extract_node_data_model_prisma(root, "bionic")
        self.assertIn("a\\|bʼc", md)          # pipe escaped, backtick neutralized.
        self.assertNotIn("a|b`c", md)


# ───────────────────── data-model: TypeORM (point 2) ─────────────────────────

class TypeOrmDataModelTests(unittest.TestCase):
    def setUp(self):
        self.md, self.sources = D.extract_node_data_model_typeorm(FIXTURE, "bionic")

    def test_entity_table(self):
        self.assertIn("| accounts | `id` | number | no | — | — |", self.md)
        self.assertIn("| accounts | `username` | string | no | — | — |", self.md)
        self.assertIn("| accounts | `bio` | string | yes | — | — |", self.md)   # nullable

    def test_join_column_fk(self):
        # @ManyToOne(() => Team) + @JoinColumn({name:"team_id"}) → team_id fk Team.
        self.assertIn("| accounts | `team_id` | — | no | — | Team |", self.md)

    def test_entity_files_hashed(self):
        self.assertIn("src/orm/account.entity.ts", self.sources)
        self.assertIn("src/orm/team.entity.ts", self.sources)


# ───────────────────── data-model: Sequelize (point 2) ───────────────────────

class SequelizeDataModelTests(unittest.TestCase):
    def setUp(self):
        self.md, self.sources = D.extract_node_data_model_sequelize(FIXTURE, "bionic")

    def test_attribute_map(self):
        self.assertIn("| Product | `id` | INTEGER |", self.md)
        self.assertIn("| Product | `name` | STRING | no | — | — |", self.md)     # allowNull:false
        self.assertIn("| Product | `price` | DECIMAL | — | 0 | — |", self.md)    # defaultValue

    def test_references_fk(self):
        self.assertIn("| Product | `categoryId` | INTEGER | — | — | categories |", self.md)

    def test_model_file_hashed(self):
        self.assertIn("src/models/product.js", self.sources)


# ───────────────────── data-model: Mongoose (dev loop 3) ─────────────────────

def _write_mongoose_pair(root: Path) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "models").mkdir(parents=True, exist_ok=True)
    (root / "src" / "models" / "user.model.js").write_text(
        "const mongoose = require('mongoose');\n"
        "const userSchema = mongoose.Schema({\n"
        "  name: { type: String, required: true },\n"
        "  role: { type: String, default: 'user' },\n"
        "});\n"
        "const User = mongoose.model('User', userSchema);\n"
        "module.exports = User;\n"
    )
    (root / "src" / "models" / "token.model.js").write_text(
        "const mongoose = require('mongoose');\n"
        "const tokenSchema = mongoose.Schema({\n"
        "  token: { type: String, required: true },\n"
        "  user: { type: mongoose.SchemaTypes.ObjectId, ref: 'User', required: true },\n"
        "});\n"
        "const Token = mongoose.model('Token', tokenSchema);\n"
        "module.exports = Token;\n"
    )


class MongooseDataModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = _bare_node(Path(self.tmp.name))
        _write_mongoose_pair(self.root)
        self.md, self.sources = D.extract_node_data_model_mongoose(self.root, "bionic")

    def _rows(self):
        return [_row_cells(ln) for ln in self.md.splitlines()
                if ln.startswith("| ") and "---" not in ln and "table" not in ln]

    def test_two_schema_fixture_yields_six_column_table(self):
        rows = self._rows()
        self.assertEqual(len(rows[0]), 6)
        self.assertIn(["User", "name", "String", "no", "—", "—"], rows)
        self.assertIn(["User", "role", "String", "yes", "user", "—"], rows)
        self.assertIn(["Token", "token", "String", "no", "—", "—"], rows)

    def test_ref_renders_in_fk_column(self):
        self.assertIn(["Token", "user", "ObjectId", "no", "—", "User"], self._rows())

    def test_probe_does_not_fire_on_a_prisma_typeorm_sequelize_fixture(self):
        # FIXTURE carries Prisma, TypeORM and Sequelize sources but no Mongoose
        # schema call anywhere — the tree-sitter detector must not fire on it.
        self.assertFalse(D._detect_node_mongoose(FIXTURE))

    def test_chain_resolves_sequelize_ahead_of_mongoose(self):
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        shutil.rmtree(root / "prisma")
        shutil.rmtree(root / "src" / "orm")           # removes the @Entity files.
        _write_mongoose_pair(root)                     # Sequelize (product.js) already present.
        ext = D.resolve_extractor("data-model", root, "bionic", None, pack_name="node")
        self.assertIs(ext, D.extract_node_data_model_sequelize)


# ─────────────── data-model: ordered resolution + drift (point 2) ────────────

class DataModelResolutionAndDriftTests(unittest.TestCase):
    def test_prisma_wins_over_typeorm_and_sequelize(self):
        # All three ORMs are present; the ordered probe list picks Prisma first.
        ext = D.resolve_extractor("data-model", FIXTURE, "bionic", None, pack_name="node")
        self.assertIs(ext, D.extract_node_data_model_prisma)

    def test_typeorm_wins_when_no_prisma(self):
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        shutil.rmtree(root / "prisma")
        ext = D.resolve_extractor("data-model", root, "bionic", None, pack_name="node")
        self.assertIs(ext, D.extract_node_data_model_typeorm)

    def test_sequelize_wins_when_no_prisma_or_entity(self):
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        shutil.rmtree(root / "prisma")
        shutil.rmtree(root / "src" / "orm")           # removes the @Entity files.
        ext = D.resolve_extractor("data-model", root, "bionic", None, pack_name="node")
        self.assertIs(ext, D.extract_node_data_model_sequelize)

    def test_schema_prisma_mutation_drifts(self):
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        D.derive(root, "bionic")
        schema = root / "prisma" / "schema.prisma"
        schema.write_text(schema.read_text().replace("email", "handle"))
        self.assertIn("bionic/arch/data-model.md", D.dry_run(root, "bionic"))

    def test_added_model_field_drifts(self):
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        D.derive(root, "bionic")
        schema = root / "prisma" / "schema.prisma"
        schema.write_text(schema.read_text().replace(
            "  token String @unique",
            "  token String @unique\n  ip    String?"))
        self.assertIn("bionic/arch/data-model.md", D.dry_run(root, "bionic"))

    def test_typeorm_mutation_drifts(self):
        # Review SHOULD-CONSIDER: a full derive→mutate→dry_run drift for the
        # TypeORM probe (Prisma removed so TypeORM is the active data-model probe).
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        shutil.rmtree(root / "prisma")
        D.derive(root, "bionic")
        ent = root / "src" / "orm" / "account.entity.ts"
        ent.write_text(ent.read_text().replace("username", "handle"))
        self.assertIn("bionic/arch/data-model.md", D.dry_run(root, "bionic"))

    def test_sequelize_mutation_drifts(self):
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        shutil.rmtree(root / "prisma")
        shutil.rmtree(root / "src" / "orm")
        D.derive(root, "bionic")
        model = root / "src" / "models" / "product.js"
        model.write_text(model.read_text().replace("price", "cost"))
        self.assertIn("bionic/arch/data-model.md", D.dry_run(root, "bionic"))


class NodeSecurityRegressionTests(unittest.TestCase):
    def test_composite_index_columns_escaped(self):
        # Security S1: a Prisma @@index arg is untrusted; a backtick in it must be
        # neutralized (via _cell) so it cannot break out of the code span in the
        # rendered index note (the Ruby-pack MUST-FIX class).
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "prisma").mkdir()
        (root / "prisma" / "schema.prisma").write_text(
            'model Thing {\n  id Int @id\n  a  String\n  b  String\n'
            '  @@index([a, b])\n}\n'
            'model Evil {\n  id Int @id\n  x  String\n'
            '  @@unique([x])\n  @@index([x`inject])\n}\n')
        md, _ = D.extract_node_data_model_prisma(root, "bionic")
        self.assertNotIn("`inject", md)               # raw backtick would break the span.
        self.assertIn("ʼinject", md)                  # neutralized by _cell.

    def test_scrub_is_string_aware(self):
        # ReDoS fix + correctness: comment markers inside a string literal are NOT
        # treated as comments (a JSON "@/*" path, a route path), while real
        # comments are stripped.
        s = D._scrub_js_comments('const p = "a//b/*c"; /* real */ x // gone\nkeep')
        self.assertIn('"a//b/*c"', s)                 # string preserved verbatim.
        self.assertNotIn("real", s)                   # block comment stripped.
        self.assertNotIn("gone", s)                   # line comment stripped.
        self.assertIn("keep", s)


# ───────────────────── module-graph (point 3) ───────────────────────────────

class ModuleGraphTests(unittest.TestCase):
    def setUp(self):
        self.md, self.sources = D.extract_node_module_graph(FIXTURE, "bionic")
        self.edges = self._edges(self.md)

    @staticmethod
    def _edges(md: str) -> list:
        # Parse `id["label a"] --> id["label b"]` mermaid lines into (a, b) pairs.
        out = []
        for ln in md.splitlines():
            m = re.search(r'\["([^"]+)"\]\s*-->\s*\w+\["([^"]+)"\]', ln)
            if m:
                out.append((m.group(1), m.group(2)))
        return out

    def test_relative_import_resolves(self):
        self.assertIn(("src/orm/account.entity.ts", "src/orm/team.entity.ts"), self.edges)

    def test_extension_ladder_first_hit_prefers_ts(self):
        # `./b` with both b.ts and b.js present resolves to b.ts (ladder order).
        self.assertIn(("src/graph/a.ts", "src/graph/b.ts"), self.edges)
        self.assertNotIn(("src/graph/a.ts", "src/graph/b.js"), self.edges)

    def test_tsconfig_alias_resolves(self):
        # `@/graph/c` → baseUrl `.` + paths `@/*: [src/*]` → src/graph/c.ts.
        self.assertIn(("src/graph/a.ts", "src/graph/c.ts"), self.edges)

    def test_bare_package_dropped(self):
        # `express` is a bare package → resolve-or-drop → no node, no edge.
        self.assertNotIn("express", self.md)
        self.assertFalse(any("express" in a or "express" in b for a, b in self.edges))

    def test_commented_import_is_stripped(self):
        # `// import "./d";` yields no edge; d.ts is isolated though scanned.
        self.assertNotIn(("src/graph/a.ts", "src/graph/d.ts"), self.edges)
        self.assertIn("src/graph/d.ts", self.md)                 # present (isolated).

    def test_string_literal_import_not_double_counted(self):
        # a.ts has a real `import {b} from "./b"` AND a string "import y from './b'".
        # The edge (a → b) appears exactly once (edge set dedupes).
        ab = [e for e in self.edges if e == ("src/graph/a.ts", "src/graph/b.ts")]
        self.assertEqual(len(ab), 1)

    def test_tsconfig_hashed(self):
        self.assertIn("tsconfig.json", self.sources)

    def test_every_scanned_source_hashed(self):
        for rel in ("src/graph/a.ts", "src/graph/b.ts", "src/graph/b.js",
                    "src/orm/account.entity.ts", "src/models/product.js"):
            self.assertIn(rel, self.sources)

    def test_import_file_mutation_drifts(self):
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        D.derive(root, "bionic")
        a = root / "src" / "graph" / "a.ts"
        a.write_text(a.read_text() + '\nimport "./d";\n')   # a new a → d edge.
        self.assertIn("bionic/arch/module-graph.md", D.dry_run(root, "bionic"))

    def test_alias_deferred_on_extends(self):
        # A tsconfig with `extends` defers aliases + records a residual.
        tmp, root = _copy()
        self.addCleanup(tmp.cleanup)
        (root / "tsconfig.json").write_text(
            '{ "extends": "./base.json", "compilerOptions": '
            '{ "baseUrl": ".", "paths": { "@/*": ["src/*"] } } }')
        md, _ = D.extract_node_module_graph(root, "bionic")
        edges = self._edges(md)
        self.assertNotIn(("src/graph/a.ts", "src/graph/c.ts"), edges)  # alias not applied.
        self.assertIn("extends", md)                                   # residual noted.


class NodeGrammarBindingTests(unittest.TestCase):
    """U0: the tree-sitter TypeScript grammar resolves and binds HERE.

    ADR-0096 clause 1 makes the grammar a declared dependency of the node pack's
    three `parser` concerns, and clause 2 routes a grammar this machine cannot
    resolve to exit 2 with nothing written. So the binding is a precondition of
    every reader dev loop 3 builds, and it is asserted directly rather than
    inferred from a reader's output: a reader that silently fell back to a regex
    would pass every route test while leaving this one to fail.

    Binding is the real risk and the reason this is its own unit. `tree-sitter`
    is pinned at `==0.26.0` because that value is a byte of the byte-compared
    `_meta/manifest.json`; a grammar wheel built against an incompatible core
    ABI raises at `Language(...)`, not at import. The fix for such a failure is a
    different GRAMMAR release and never a move of the core pin — moving it would
    restate every elixir target's manifest, which dev loop 2 just stabilized.

    One distribution covers all six extensions the pack scans: the TypeScript
    grammar is a superset of JavaScript, and `language_tsx()` is the JSX dialect.
    """

    def _langs(self):
        import tree_sitter                                  # noqa: PLC0415
        import tree_sitter_typescript as tst                # noqa: PLC0415
        return tree_sitter, tst

    def test_both_dialect_grammars_bind_against_the_pinned_core(self):
        tree_sitter, tst = self._langs()
        for name in ("language_typescript", "language_tsx"):
            with self.subTest(dialect=name):
                lang = tree_sitter.Language(getattr(tst, name)())
                self.assertIsNotNone(tree_sitter.Parser(lang))

    def test_the_grammar_parses_every_dialect_the_pack_scans(self):
        """A CommonJS router, an ES-module TS controller, and TSX.

        `.js`/`.mjs`/`.cjs` go through the TypeScript grammar and `.jsx`/`.tsx`
        through TSX, which is the split the readers use. Each sample is asserted
        ERROR-free, because tree-sitter has no unparseable input — a grammar
        mismatch surfaces as an ERROR node rather than an exception, and an
        unchecked parse would look like a success.
        """
        tree_sitter, tst = self._langs()
        samples = {
            "language_typescript": [
                b"const r = require('express').Router();\n"
                b"r.get('/users/:id', ctrl.get);\n"
                b"module.exports = r;\n",
                b"@Controller('auth')\nexport class C {\n"
                b"  @Get(':id')\n  async find(@Param() p: string): Promise<T> {}\n}\n",
            ],
            "language_tsx": [b"export const A = () => <div className='x'>{y}</div>;\n"],
        }
        for name, sources in samples.items():
            parser = tree_sitter.Parser(tree_sitter.Language(getattr(tst, name)()))
            for src in sources:
                with self.subTest(dialect=name, src=src[:40]):
                    root = parser.parse(src).root_node
                    self.assertFalse(root.has_error, f"{name} could not parse {src!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
