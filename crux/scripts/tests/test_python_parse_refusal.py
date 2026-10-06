"""The Python pack's parse-refusal contract: named in the residuals AND hashed.

`packs/python.py` catches `_PARSE_FAILED` at five `ast.parse` sites. Four of
them feed a residual channel, and the constant's own comment states the
contract those four keep: a refused file is HASHED but not parsed, and named in
the concern's residuals.

Both halves matter, and the second is the one with teeth. `inputs_found` is
`sorted(sources)` in `_meta/coverage.json`, and `staleness.py` reads
`inputs_found` to decide whether a spine file has fallen behind its sources. A
file named in the prose but absent from `sources` is invisible to that ledger:
edit it while leaving it broken and the rendered residual is byte-identical, so
nothing — not the drift gate, not the staleness check — reports the edit.

The measured case is `fastapi-fullstack`'s `backend/app/api/deps.py`, a Python 2
`except A, B:` that CPython 3.13 refuses. Before this contract held, that path
appeared in `data-model.md` and `api-surface.md` prose and in NEITHER concern's
`inputs_found`. CPython 3.14 ACCEPTS that exact line (PEP 758 allows an
unparenthesized exception list when no `as` follows), so the fixtures below use
the `except A, B as exc:` form, which every supported interpreter refuses.
`test_the_bare_exception_list_is_refused_only_before_3_14` pins the difference,
because the corpus goldens for that repository depend on it.

`module-graph` is the deliberate asymmetry, and it is honest rather than a gap:
`core._extract_module_graph` never calls `ast.parse` at all — it is a regex pass
over the file TEXT — so it has no refusal to report, and it hashes every file it
reads unconditionally. It therefore hashes a file the other two concerns refuse,
and names no refusal, because it experienced none. `test_module_graph_*` below
pins that reading so the asymmetry cannot be mistaken for the defect this file
closes.

Stdlib only. Every fixture is written to a tempdir, never committed: a committed
tree carrying a deliberate `SyntaxError` would also be read by this repository's
own code-doc extractor and its lint gates.
"""

from __future__ import annotations

import ast
import importlib
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]      # crux/scripts
sys.path.insert(0, str(SCRIPTS))

P = importlib.import_module("crux.arch.packs.python")

#: An unparenthesized exception list with an `as` target. The bare
#: `except A, B:` that `fastapi-fullstack` carries is refused by 3.13 but
#: accepted by 3.14 (PEP 758); the `as` form stays a SyntaxError on both, which
#: `test_the_fixture_is_genuinely_unparseable` asserts rather than assumes.
BROKEN_SOURCE = (
    "def get_db():\n"
    "    try:\n"
    "        yield 1\n"
    "    except ValueError, TypeError as exc:\n"
    "        raise\n"
)

#: The same file made parseable, and NOTHING else changed. This is the mutation
#: the positive-control lane applies.
FIXED_SOURCE = (
    "def get_db():\n"
    "    try:\n"
    "        yield 1\n"
    "    except (ValueError, TypeError) as exc:\n"
    "        raise\n"
)

BROKEN_REL = "app/deps.py"
BROKEN_MIGRATION_REL = "alembic/versions/0002_broken.py"

#: A migration that does not parse, carrying the `revision`/`down_revision`
#: tokens `_has_migration` looks for so the versions dir is still detected.
BROKEN_MIGRATION = (
    '"""broken migration."""\n'
    "revision = '0002'\n"
    "down_revision = '0001'\n"
    "try:\n"
    "    pass\n"
    "except ValueError, TypeError as exc:\n"
    "    pass\n"
)

FIXED_MIGRATION = (
    '"""second migration."""\n'
    "revision = '0002'\n"
    "down_revision = '0001'\n"
)

BASE_FIXTURE = {
    "pyproject.toml": '[project]\nname = "app"\n',
    "app/__init__.py": "",
    "app/models.py": (
        "from sqlalchemy.orm import DeclarativeBase\n"
        "from sqlalchemy import Column, Integer, String\n"
        "\n"
        "class Base(DeclarativeBase):\n"
        "    pass\n"
        "\n"
        "class User(Base):\n"
        "    __tablename__ = 'user'\n"
        "    id = Column(Integer, primary_key=True)\n"
        "    email = Column(String, nullable=False)\n"
    ),
    "app/routes.py": (
        "from fastapi import APIRouter\n"
        "\n"
        "router = APIRouter(prefix='/users')\n"
        "\n"
        "@router.get('/')\n"
        "def read_users():\n"
        "    return []\n"
    ),
    "alembic/versions/0001_init.py": (
        '"""init."""\n'
        "revision = '0001'\n"
        "down_revision = None\n"
    ),
}


def _write_repo(files: dict) -> tuple:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name) / "repo"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp, root


def _fixture(*, broken: bool) -> dict:
    files = dict(BASE_FIXTURE)
    files[BROKEN_REL] = BROKEN_SOURCE if broken else FIXED_SOURCE
    files[BROKEN_MIGRATION_REL] = BROKEN_MIGRATION if broken else FIXED_MIGRATION
    return files


def assert_refusal_reported(case, md: str, sources: dict, rel: str) -> None:
    """THE assertion pair under test — both halves of the stated contract.

    Called by the refusal lane to assert it holds, and by the positive-control
    lane to assert it FAILS on a fixture that parses. One helper, two callers:
    a vacuous assertion here would pass in both lanes, and the control lane is
    what makes that impossible.
    """
    case.assertIn("Source(s) Python's parser refused", md)
    case.assertIn(f"`{rel}`", md)                 # named in the residual prose
    case.assertIn(rel, sources)                   # and hashed into the ledger


class ParseRefusalFixtureTests(unittest.TestCase):
    """The fixture is broken for the reason the test says it is."""

    def test_the_fixture_is_genuinely_unparseable(self):
        with self.assertRaises(SyntaxError):
            ast.parse(BROKEN_SOURCE)
        with self.assertRaises(SyntaxError):
            ast.parse(BROKEN_MIGRATION)

    def test_the_bare_exception_list_is_refused_only_before_3_14(self):
        """PEP 758: 3.14 accepts `except A, B:`; 3.13 refuses it.

        This is why the fixtures carry `as exc`. It is also why the
        `fastapi-fullstack` corpus spine differs by interpreter: its `deps.py`
        is a refused source on 3.13 and an ordinary parsed one on 3.14.
        """
        bare = "try:\n    pass\nexcept ValueError, TypeError:\n    pass\n"
        if sys.version_info >= (3, 14):
            ast.parse(bare)
        else:
            with self.assertRaises(SyntaxError):
                ast.parse(bare)

    def test_the_mutated_fixture_parses(self):
        # If this ever raised, the control lane below would be asserting the
        # refusal is absent from a fixture that still refuses.
        ast.parse(FIXED_SOURCE)
        ast.parse(FIXED_MIGRATION)

    def test_syntax_error_is_in_the_caught_class(self):
        self.assertIn(SyntaxError, P._PARSE_FAILED)


class DataModelParseRefusalTests(unittest.TestCase):
    """`extract_python_data_model` — the `_scan_sqlalchemy_models`,
    `_scan_orm_models` and `_scan_alembic_timeline` sites."""

    def setUp(self):
        tmp, self.root = _write_repo(_fixture(broken=True))
        self.addCleanup(tmp.cleanup)
        self.md, self.sources = P.extract_python_data_model(self.root, "bionic")

    def test_the_concern_is_populated_not_stubbed(self):
        # A stub renders no residuals at all, so the assertions below would be
        # measuring an empty document rather than a refusal.
        self.assertIn("## SQLAlchemy models", self.md)

    def test_the_refused_source_is_named_and_hashed(self):
        assert_refusal_reported(self, self.md, self.sources, BROKEN_REL)

    def test_the_refused_migration_is_named_and_hashed(self):
        assert_refusal_reported(self, self.md, self.sources, BROKEN_MIGRATION_REL)

    def test_the_residual_is_rendered_once_for_all_three_scans(self):
        residuals = self.md.split("## Residuals", 1)[1]
        self.assertEqual(residuals.count("Source(s) Python's parser refused"), 1)

    def test_a_contributing_source_is_still_hashed(self):
        self.assertIn("app/models.py", self.sources)

    def test_the_hash_is_the_file_digest_not_a_placeholder(self):
        digest = self.sources[BROKEN_REL]
        self.assertEqual(digest, P._sha256_hex(BROKEN_SOURCE.encode("utf-8")))


class DataModelCleanSourcePositiveControlTests(unittest.TestCase):
    """POSITIVE CONTROL: the same tree with the two files made parseable.

    `assert_refusal_reported` must FAIL here. Without this lane the refusal
    assertions above would pass just as well against a helper that asserted
    nothing.
    """

    def setUp(self):
        tmp, self.root = _write_repo(_fixture(broken=False))
        self.addCleanup(tmp.cleanup)
        self.md, self.sources = P.extract_python_data_model(self.root, "bionic")

    def test_the_control_fixture_still_populates_the_concern(self):
        self.assertIn("## SQLAlchemy models", self.md)

    def test_positive_control_the_refusal_assertion_fails_on_parseable_sources(self):
        with self.assertRaises(AssertionError):
            assert_refusal_reported(self, self.md, self.sources, BROKEN_REL)
        with self.assertRaises(AssertionError):
            assert_refusal_reported(self, self.md, self.sources,
                                    BROKEN_MIGRATION_REL)

    def test_no_refusal_prose_is_rendered(self):
        self.assertNotIn("Source(s) Python's parser refused", self.md)

    def test_a_parseable_non_contributing_source_is_not_hashed(self):
        # The counterpart of the fix: hashing is what a REFUSAL earns. A file
        # that parses and declares no model stays out, exactly as before.
        self.assertNotIn(BROKEN_REL, self.sources)


class ApiSurfaceParseRefusalTests(unittest.TestCase):
    """`extract_python_api_surface_routes` — the `_RouteScan._scan` site."""

    def setUp(self):
        tmp, self.root = _write_repo(_fixture(broken=True))
        self.addCleanup(tmp.cleanup)
        self.md, self.sources = P.extract_python_api_surface_routes(self.root, "bionic")

    def test_the_concern_is_populated_not_stubbed(self):
        self.assertIn("## Routes (1)", self.md)

    def test_the_refused_source_is_named_and_hashed(self):
        assert_refusal_reported(self, self.md, self.sources, BROKEN_REL)

    def test_the_refused_migration_is_named_and_hashed(self):
        assert_refusal_reported(self, self.md, self.sources, BROKEN_MIGRATION_REL)

    def test_a_contributing_source_is_still_hashed(self):
        self.assertIn("app/routes.py", self.sources)

    def test_the_hash_is_the_file_digest_not_a_placeholder(self):
        self.assertEqual(self.sources[BROKEN_REL],
                         P._sha256_hex(BROKEN_SOURCE.encode("utf-8")))


class ApiSurfaceCleanSourcePositiveControlTests(unittest.TestCase):
    """POSITIVE CONTROL for the api-surface lane (see the data-model twin)."""

    def setUp(self):
        tmp, self.root = _write_repo(_fixture(broken=False))
        self.addCleanup(tmp.cleanup)
        self.md, self.sources = P.extract_python_api_surface_routes(self.root, "bionic")

    def test_the_control_fixture_still_populates_the_concern(self):
        self.assertIn("## Routes (1)", self.md)

    def test_positive_control_the_refusal_assertion_fails_on_parseable_sources(self):
        with self.assertRaises(AssertionError):
            assert_refusal_reported(self, self.md, self.sources, BROKEN_REL)
        with self.assertRaises(AssertionError):
            assert_refusal_reported(self, self.md, self.sources,
                                    BROKEN_MIGRATION_REL)

    def test_no_refusal_prose_is_rendered(self):
        self.assertNotIn("Source(s) Python's parser refused", self.md)

    def test_a_parseable_non_contributing_source_is_not_hashed(self):
        self.assertNotIn(BROKEN_REL, self.sources)


class ModuleGraphAsymmetryTests(unittest.TestCase):
    """`module-graph` reads the same file and reports it differently — honestly.

    `core._extract_module_graph` never parses: it hashes every file it reads and
    scans the TEXT with regular expressions. So it has no refusal to name, and
    the file it cannot parse is still one it read. Pinned here because the
    obvious repair — "make module-graph name the refusal too" — would have to
    invent a refusal that never happened.
    """

    def setUp(self):
        tmp, self.root = _write_repo(_fixture(broken=True))
        self.addCleanup(tmp.cleanup)
        self.md, self.sources = P.extract_python_module_graph(self.root, "bionic")

    def test_the_unparseable_source_is_hashed(self):
        self.assertIn(BROKEN_REL, self.sources)

    def test_no_refusal_is_named_because_none_occurred(self):
        self.assertNotIn("Source(s) Python's parser refused", self.md)

    def test_the_engine_calls_no_parser(self):
        """Two independent legs: `core` imports no `ast`, and `core` contains no
        `ast.parse` CALL.

        The second leg is an AST walk rather than a substring scan, and the
        difference is not pedantry. The scan read PROSE as code: `core` owns the
        shared `_PARSE_FAILED` tuple, whose docstring's whole subject is the ways
        `ast.parse` fails, so naming the function it is about tripped a test
        about calling it. A guard that fires on its own documentation measures
        the wrong thing — which is the defect ADR-0097 exists to correct, reached
        from the test side.
        """
        core = importlib.import_module("crux.arch.core")
        self.assertNotIn("ast", sys.modules.get("crux.arch.core").__dict__)
        tree = ast.parse(Path(core.__file__).read_text(encoding="utf-8"))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == "parse"
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "ast"
        ]
        self.assertEqual(
            [n.lineno for n in calls], [],
            "core.py calls ast.parse — `_extract_module_graph` is a regex pass "
            "over the file text and has no refusal to name")


class EveryConcernNamingTheFileAlsoHashesItTests(unittest.TestCase):
    """The rule, stated once over all three concerns.

    Every concern whose rendered prose names the refused path also carries that
    path in the sources it returns. This is the assertion that would have caught
    the shipped defect, and it is written over the concern set rather than per
    concern so a fourth Python concern is covered the day it is added.
    """

    def setUp(self):
        tmp, self.root = _write_repo(_fixture(broken=True))
        self.addCleanup(tmp.cleanup)
        self.results = {
            "data-model": P.extract_python_data_model(self.root, "bionic"),
            "api-surface": P.extract_python_api_surface_routes(self.root, "bionic"),
            "module-graph": P.extract_python_module_graph(self.root, "bionic"),
        }

    def test_naming_the_path_implies_hashing_it(self):
        named = []
        for concern, (md, sources) in self.results.items():
            for rel in (BROKEN_REL, BROKEN_MIGRATION_REL):
                if f"`{rel}`" in md:
                    named.append((concern, rel))
                    self.assertIn(rel, sources, f"{concern} names {rel} unhashed")
        # Not vacuous: at least one concern must actually name one.
        self.assertTrue(named, "no concern named a refused path")


if __name__ == "__main__":
    unittest.main()
