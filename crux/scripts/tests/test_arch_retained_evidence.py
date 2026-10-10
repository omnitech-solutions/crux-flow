"""Reached architecture discovery/read seams exclude parent-retained repositories."""
from pathlib import Path
import contextlib
import importlib
import os
import sys
import tempfile
import unittest
from unittest import mock
import glob

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
core = importlib.import_module("crux.arch.core")



# Python 3.13's pathlib globber holds os.scandir itself (reached as Path._globber.scandir), so
# patching os.scandir alone misses globbing. Python 3.14's glob._StringGlobber.scandir calls
# os.scandir, and Path has no _globber. Decided at import, before any test patches os.scandir.
_GLOBBER_HOLDS_SCANDIR = glob._StringGlobber.scandir is os.scandir


def globber_scandir(scan, os_scandir_patched=False):
    """Route pathlib globbing's directory scans through `scan` on either interpreter."""
    if _GLOBBER_HOLDS_SCANDIR:
        return mock.patch.object(glob._StringGlobber, "scandir", staticmethod(scan))
    if os_scandir_patched:
        return contextlib.nullcontext()
    return mock.patch.object(os, "scandir", side_effect=scan)

class RetainedArchitecture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.docs = self.root / "knowledge/project"
        self.holding = self.docs / "promptbooks/runs/OLD-PB-0140-old--choice-/evidence/implementation-cycles/retained-repositories"
        self.holding.mkdir(parents=True)
        (self.root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: knowledge/project\n')
        self.held = self.holding / "child/source.py"
        self.held.parent.mkdir(); self.held.write_text("class HeldModel: pass\n")
        (self.held.parent / ".bionic.yml").write_text("invalid child config: [\n")
        self.ordinary = self.root / "src/retained-repositories/source.py"
        self.ordinary.parent.mkdir(parents=True); self.ordinary.write_text("class OrdinaryModel: pass\n")

    def held_path(self, path):
        return Path(path) == self.holding or self.holding in Path(path).parents

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file() and not p.is_symlink()}

    def test_actual_python_walk_prunes_before_held_descent_and_keeps_ordinary_source(self):
        original = os.scandir; before = self.snapshot()
        def scan(path):
            self.assertFalse(self.held_path(path), "walk descended into retained repository")
            return original(path)
        with mock.patch.object(os, "scandir", side_effect=scan):
            files = list(core._iter_py_files(self.root))
        self.assertEqual(files, [self.ordinary]); self.assertEqual(self.snapshot(), before)

    def test_actual_detection_scan_prunes_before_held_marker_discovery(self):
        original = Path.iterdir
        def children(path):
            self.assertFalse(self.held_path(path), "detector descended into retained repository")
            return original(path)
        with mock.patch.object(Path, "iterdir", children):
            directories = list(core._scan_dirs(self.root, max_depth=12))
        self.assertIn(self.ordinary.parent, {p for p, _ in directories})
        self.assertFalse(any(self.held_path(p) for p, _ in directories))

    def test_shared_read_refuses_lexical_and_resolved_alias_before_open(self):
        alias = self.root / "alias"; alias.symlink_to(self.holding, target_is_directory=True)
        original = os.open
        def opened(path, *args, **kwargs):
            self.assertFalse(self.held_path(Path(path).resolve()), "opened retained bytes")
            return original(path, *args, **kwargs)
        with mock.patch.object(os, "open", side_effect=opened):
            for source in (self.held, alias / "child/source.py"):
                self.assertIsNone(core._safe_read_bytes(self.root, source, []))
            self.assertEqual(core._safe_read_bytes(self.root, self.ordinary, []), self.ordinary.read_bytes())

    def test_direct_package_rglob_reaches_shared_read_and_drops_held_source_inventory(self):
        original = os.scandir
        def scan(path):
            self.assertFalse(self.held_path(path), "package descended into retained repository")
            return original(path)
        with mock.patch.object(os, "scandir", side_effect=scan), \
             globber_scandir(scan, os_scandir_patched=True):
            markdown, sources = core._extract_module_graph(self.root, self.docs, "parent")
        self.assertNotIn(self.held.relative_to(self.root).as_posix(), sources)
        self.assertNotIn("HeldModel", markdown)
        markdown, sources = core._extract_module_graph(self.root, self.ordinary.parent, "ordinary")
        self.assertEqual(set(sources), {self.ordinary.relative_to(self.root).as_posix()})

    def test_invalid_holding_layout_refuses_bounded_before_read_or_discovery(self):
        self.held.unlink(); (self.held.parent / ".bionic.yml").unlink(); self.held.parent.rmdir()
        self.holding.rmdir(); self.holding.symlink_to(self.ordinary.parent, target_is_directory=True)
        for call in (lambda: list(core._iter_py_files(self.root)),
                     lambda: list(core._scan_dirs(self.root, max_depth=12)),
                     lambda: core._safe_read_bytes(self.root, self.holding / "source.py", [])):
            with self.subTest(call=call), self.assertRaisesRegex(ValueError,
                                                              "^retained-evidence-layout-refused$"):
                call()

    def test_ordinary_alias_and_explicit_child_inspection_preserve_source(self):
        alias = self.root / "ordinary-alias"; alias.symlink_to(self.ordinary.parent, target_is_directory=True)
        self.assertEqual(core._safe_read_bytes(self.root, alias / "source.py", []), self.ordinary.read_bytes())
        # Explicit isolated child inspection establishes its own configuration.
        (self.held.parent / ".bionic.yml").unlink()
        self.assertEqual(core._safe_read_bytes(self.held.parent, self.held, []), self.held.read_bytes())

    def test_missing_docs_tree_and_legacy_config_version_keep_ordinary_reader(self):
        (self.root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: absent\n')
        self.assertEqual(core._safe_read_bytes(self.root, self.ordinary, []), self.ordinary.read_bytes())
        self.assertIn(self.ordinary, list(core._iter_py_files(self.root)))

    def test_posix_odd_names_preserve_ordinary_source_and_exclude_held_aliases(self):
        ordinary = self.ordinary.parent / "odd\n\\name.py"
        ordinary.write_text("class OrdinaryOdd: pass\n")
        held = self.held.parent / "odd\n\\name.py"
        held.write_text("class HeldOdd: pass\n")
        alias = self.root / "odd\n\\alias"
        alias.symlink_to(self.held.parent, target_is_directory=True)
        self.assertEqual(core._safe_read_bytes(self.root, ordinary, []), ordinary.read_bytes())
        self.assertIn(ordinary, list(core._iter_py_files(self.root)))
        self.assertIsNone(core._safe_read_bytes(self.root, held, []))
        self.assertIsNone(core._safe_read_bytes(self.root, alias / held.name, []))
        self.assertNotIn(held, list(core._iter_py_files(self.root)))

    def test_each_pack_discovery_prunes_before_retained_enumeration(self):
        packs = {name: importlib.import_module("crux.arch.packs." + name)
                 for name in ("python", "node", "elixir", "ruby", "swift", "swift_xcode")}
        for directory in (self.held.parent, self.ordinary.parent):
            (directory / "pyproject.toml").write_text('[project]\nname = "fixture"\n')
            for name in ("source.js", "query.graphql", "schema.prisma", "router.ex", "source.rb", "Source.swift"):
                (directory / name).write_text("ordinary or held source\n")
            versions = directory / "versions"; versions.mkdir()
            (versions / "one.py").write_text('revision = "1"\ndown_revision = None\n')
        before = self.snapshot()
        calls = [
            ("python manifests", lambda: packs["python"]._manifest_dirs(self.root, max_depth=12)),
            ("python alembic", lambda: packs["python"]._detect_alembic_versions(self.root)),
            ("node js", lambda: list(packs["node"]._walk_js_files(self.root))),
            ("node graphql", lambda: packs["node"]._find_graphql_files(self.root)),
            ("node prisma", lambda: packs["node"]._find_prisma_schemas(self.root)),
            ("elixir sources", lambda: list(packs["elixir"]._walk_ex_files(self.root))),
            ("elixir routers", lambda: packs["elixir"]._elixir_router_files(self.root)),
            ("ruby sources", lambda: list(packs["ruby"]._walk_rb_dir(self.root, self.root))),
            ("swift sources", lambda: list(packs["swift"]._swift_walk(self.root))),
            ("swift synced folders", lambda: list(packs["swift_xcode"]._scan_swift_files(self.root, self.root, set()))),
        ]
        original_scan, original_iter = os.scandir, Path.iterdir
        def scan(path):
            self.assertFalse(self.held_path(path), "pack descended into retained repository")
            return original_scan(path)
        def children(path):
            self.assertFalse(self.held_path(path), "pack discovered retained metadata")
            return original_iter(path)
        for name, call in calls:
            with self.subTest(name=name), mock.patch.object(os, "scandir", side_effect=scan), \
                 globber_scandir(scan, os_scandir_patched=True), \
                 mock.patch.object(Path, "iterdir", children):
                answer = call()
                ordinary = self.ordinary.parent
                expected = {
                    "python manifests": ordinary,
                    "python alembic": ordinary / "versions",
                    "node js": ordinary / "source.js",
                    "node graphql": (ordinary / "query.graphql").relative_to(self.root).as_posix(),
                    "node prisma": ordinary / "schema.prisma",
                    "elixir sources": ordinary / "router.ex",
                    "elixir routers": ordinary / "router.ex",
                    "ruby sources": ordinary / "source.rb",
                    "swift synced folders": (ordinary / "Source.swift").relative_to(self.root).as_posix(),
                }
                if name == "swift sources":
                    self.assertTrue(any(directory == ordinary and "Source.swift" in files
                                        for directory, _rel, _normal, _excluded, files in answer))
                elif name == "python alembic":
                    self.assertEqual(answer, expected[name])
                else:
                    self.assertIn(expected[name], answer, "ordinary source must be reached")
        self.assertEqual(self.snapshot(), before)

    def test_pack_explicit_candidate_aliases_into_holding_are_excluded(self):
        python = importlib.import_module("crux.arch.packs.python")
        ruby = importlib.import_module("crux.arch.packs.ruby")
        (self.held.parent / "__init__.py").write_text("")
        (self.ordinary.parent / "__init__.py").write_text("")
        package = self.root / "package"; package.symlink_to(self.held.parent, target_is_directory=True)
        self.assertNotIn(package.resolve(), {p.resolve() for p, _ in python.detect_packages(self.root)})
        self.assertEqual(list(ruby._walk_rb_dir(self.root, self.holding)), [])

    def test_declared_package_alias_is_pruned_before_package_marker_metadata(self):
        python = importlib.import_module("crux.arch.packs.python")
        (self.held.parent / "__init__.py").write_text("")
        package = self.root / "package"; package.symlink_to(self.held.parent, target_is_directory=True)
        (self.root / "pyproject.toml").write_text('[project]\nname = "package"\n')
        original = Path.is_file
        def marker(path):
            self.assertNotEqual(path, package / "__init__.py", "inspected held package marker")
            return original(path)
        with mock.patch.object(Path, "is_file", marker):
            found = python.detect_packages(self.root)
        self.assertFalse(any(p.resolve() == self.held.parent for p, _ in found))

    def test_ruby_environment_alias_is_pruned_before_glob_descent(self):
        ruby = importlib.import_module("crux.arch.packs.ruby")
        config = self.root / "config"; config.mkdir()
        (config / "application.rb").write_text("class App: end\n")
        environment = config / "environments"
        environment.symlink_to(self.held.parent, target_is_directory=True)
        original = os.scandir
        def scan(path):
            self.assertNotEqual(Path(path), environment, "enumerated held environment alias")
            return original(path)
        with globber_scandir(scan):
            roots, residuals = ruby._autoload_roots(self.root, self.root)
        self.assertEqual(residuals, [])
        self.assertFalse(any(self.held_path(p.resolve()) for p in roots))

    def test_conventional_alembic_alias_is_pruned_before_marker_metadata(self):
        python = importlib.import_module("crux.arch.packs.python")
        versions = self.held.parent / "versions"; versions.mkdir()
        (versions / "one.py").write_text('revision = "1"\ndown_revision = None\n')
        conventional = self.root / "alembic/versions"
        conventional.parent.mkdir(); conventional.symlink_to(versions, target_is_directory=True)
        original = Path.is_dir
        def marker(path):
            self.assertNotEqual(path, conventional, "inspected held versions marker")
            return original(path)
        with mock.patch.object(Path, "is_dir", marker):
            self.assertIsNone(python._detect_alembic_versions(self.root))

    def test_ecto_migration_alias_is_pruned_before_glob_and_ordinary_migrations_work(self):
        elixir = importlib.import_module("crux.arch.packs.elixir")
        (self.held.parent / "one.exs").write_text("held migration\n")
        conventional = self.root / "priv/repo/migrations"
        conventional.parent.mkdir(parents=True)
        conventional.symlink_to(self.held.parent, target_is_directory=True)
        original = os.scandir
        def scan(path):
            self.assertNotEqual(Path(path), conventional, "enumerated held Ecto migration alias")
            return original(path)
        with globber_scandir(scan):
            self.assertFalse(elixir._detect_elixir_migrations(self.root))
            _markdown, sources = elixir.extract_elixir_migrations(self.root, self.docs)
            self.assertEqual(sources, {})
        conventional.unlink(); conventional.mkdir()
        (conventional / "one.exs").write_text("ordinary migration\n")
        self.assertTrue(elixir._detect_elixir_migrations(self.root))

    def test_alembic_fallback_preserves_ordinary_directory_alias_candidate(self):
        python = importlib.import_module("crux.arch.packs.python")
        migrations = self.ordinary.parent / "migration-source"; migrations.mkdir()
        (migrations / "one.py").write_text('revision = "1"\ndown_revision = None\n')
        alias = self.root / "nested/versions"
        alias.parent.mkdir(); alias.symlink_to(migrations, target_is_directory=True)
        self.assertEqual(python._detect_alembic_versions(self.root), alias)

    def test_discovery_ignores_ordinary_directory_loop_and_escape_without_descent(self):
        (self.root / "loop").symlink_to("loop", target_is_directory=True)
        outside = tempfile.TemporaryDirectory(); self.addCleanup(outside.cleanup)
        (Path(outside.name) / "outside.py").write_text("class Outside: pass\n")
        escape = self.root / "escape"; escape.symlink_to(outside.name, target_is_directory=True)
        original = os.scandir
        def scan(path):
            self.assertNotIn(Path(path), (self.root / "loop", escape, Path(outside.name)),
                             "discovery followed ignored directory alias")
            return original(path)
        with mock.patch.object(os, "scandir", side_effect=scan):
            self.assertEqual(list(core._iter_py_files(self.root)), [self.ordinary])
            directories = [p for p, _ in core._scan_dirs(self.root, max_depth=12)]
        self.assertNotIn(self.root / "loop", directories)
        self.assertNotIn(escape, directories)
        self.assertIn(self.ordinary.parent, directories)

    def test_linked_holding_ancestor_and_malformed_book_refuse_independently(self):
        for defect in ("linked ancestor", "malformed book"):
            with self.subTest(defect=defect):
                if defect == "linked ancestor":
                    runs = self.docs / "promptbooks/runs"
                    saved = self.docs / "saved-runs"; runs.rename(saved)
                    runs.symlink_to(saved, target_is_directory=True)
                else:
                    runs.unlink(); saved.rename(runs)
                    book = runs / "OLD-PB-0140-old--choice-"
                    book.rename(runs / "not-a-book")
                for call in (lambda: list(core._iter_py_files(self.root)),
                             lambda: list(core._scan_dirs(self.root, max_depth=12))):
                    with self.assertRaisesRegex(ValueError, "^retained-evidence-layout-refused$"):
                        call()

    def test_python_direct_discovery_ignores_ordinary_loops_before_old_probes(self):
        python = importlib.import_module("crux.arch.packs.python")
        (self.ordinary.parent / "pyproject.toml").write_text('[project]\nname = "absent"\n')
        package = self.root / "ordinary-package"; package.mkdir()
        (package / "__init__.py").write_text("")
        versions = self.ordinary.parent / "versions"; versions.mkdir()
        (versions / "one.py").write_text('revision = "1"\ndown_revision = None\n')
        cases = [
            ("manifest", self.root / "loop", lambda: python._manifest_dirs(self.root), self.ordinary.parent),
            ("explicit package", self.root / "loop", lambda: python._resolve_pkg_dir(self.root, "loop"), None),
            ("tier2", self.root / "src/loop", lambda: [p for p, _ in python.detect_packages(self.root)], package),
            ("conventional Alembic", self.root / "alembic/versions", lambda: python._detect_alembic_versions(self.root), versions),
            ("explicit migration", self.root / "loop", lambda: python._has_migration(self.root, self.root / "loop"), False),
        ]
        for name, loop, call, expected in cases:
            with self.subTest(name=name):
                loop.parent.mkdir(parents=True, exist_ok=True); loop.symlink_to(loop.name, target_is_directory=True)
                try:
                    answer = call()
                    if name in ("manifest", "tier2"): self.assertIn(expected, answer)
                    else: self.assertEqual(answer, expected)
                finally: loop.unlink()

    def test_ecto_and_ruby_direct_discovery_ignore_ordinary_loop_entries(self):
        elixir = importlib.import_module("crux.arch.packs.elixir")
        ruby = importlib.import_module("crux.arch.packs.ruby")
        config = self.root / "config"; config.mkdir()
        application = config / "application.rb"
        application.write_text('config.autoload_paths += ["custom-libs"]\n')
        app = self.root / "app/services"; app.mkdir(parents=True)
        cases = [
            ("Ecto", self.root / "priv/repo/migrations", lambda: elixir._elixir_migrations_dir(self.root)),
            ("Ruby environment", config / "environments", lambda: ruby._autoload_roots(self.root, self.root)),
            ("Ruby config", application, lambda: ruby._autoload_roots(self.root, self.root)),
            ("Ruby app child", self.root / "app/loop", lambda: ruby._autoload_roots(self.root, self.root)),
            ("Ruby lib", self.root / "lib", lambda: ruby._autoload_roots(self.root, self.root)),
            ("Ruby literal", self.root / "custom-libs", lambda: ruby._autoload_roots(self.root, self.root)),
            ("Ruby explicit base", self.root / "loop", lambda: list(ruby._walk_rb_dir(self.root, self.root / "loop"))),
        ]
        for name, loop, call in cases:
            with self.subTest(name=name):
                original = loop.read_bytes() if loop.is_file() else None
                loop.unlink(missing_ok=True); loop.parent.mkdir(parents=True, exist_ok=True)
                loop.symlink_to(loop.name, target_is_directory=True)
                try:
                    answer = call()
                    if name == "Ecto": self.assertIsNone(answer)
                    elif name == "Ruby explicit base": self.assertEqual(answer, [])
                    else: self.assertIn(app, answer[0])
                finally:
                    loop.unlink()
                    if original is not None: loop.write_bytes(original)

    def test_ruby_literal_source_paths_preserve_existing_parent_and_ignore_escape_or_missing_parent(self):
        ruby = importlib.import_module("crux.arch.packs.ruby")
        config = self.root / "config"; config.mkdir()
        (self.root / "app").mkdir()
        library = self.root / "custom-lib"; library.mkdir()
        (config / "application.rb").write_text('config.autoload_paths += ["app/../custom-lib"]\n')
        roots, _ = ruby._autoload_roots(self.root, self.root)
        self.assertIn(library, roots)
        outside = tempfile.TemporaryDirectory(dir=self.root.parent)
        self.addCleanup(outside.cleanup)
        escape = "../" + Path(outside.name).name
        (config / "application.rb").write_text(
            f'config.autoload_paths += ["missing/../custom-lib", "{escape}"]\n')
        roots, _ = ruby._autoload_roots(self.root, self.root)
        self.assertNotIn(library, roots)
        self.assertFalse(any(not p.is_relative_to(self.root) for p in roots))

    def test_ruby_literal_parent_traversal_resolves_alias_before_parent_and_stops_at_holding(self):
        ruby = importlib.import_module("crux.arch.packs.ruby")
        config = self.root / "config"; config.mkdir()
        target = self.root / "target/child"; target.mkdir(parents=True)
        library = target.parent / "library"; library.mkdir()
        (self.root / "ordinary-alias").symlink_to(target, target_is_directory=True)
        (self.root / "held-alias").symlink_to(self.holding, target_is_directory=True)
        (config / "application.rb").write_text(
            'config.autoload_paths += ["ordinary-alias/../library", "held-alias/../library"]\n')
        original = Path.lstat
        def inspected(path, *args, **kwargs):
            self.assertFalse(self.holding in Path(path).parents, "inspected a held suffix")
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "lstat", inspected):
            roots, _ = ruby._autoload_roots(self.root, self.root)
        self.assertEqual(roots, [library])

    def test_direct_admission_validates_malformed_holding_independently_of_ordinary_loop(self):
        python = importlib.import_module("crux.arch.packs.python")
        elixir = importlib.import_module("crux.arch.packs.elixir")
        ruby = importlib.import_module("crux.arch.packs.ruby")
        book = self.docs / "promptbooks/runs/OLD-PB-0140-old--choice-"
        book.rename(book.parent / "not-a-book")
        loop = self.root / "loop"; loop.symlink_to(loop.name, target_is_directory=True)
        calls = (lambda: python._manifest_dirs(self.root),
                 lambda: python._resolve_pkg_dir(self.root, "loop"),
                 lambda: python._detect_alembic_versions(self.root),
                 lambda: elixir._elixir_migrations_dir(self.root),
                 lambda: ruby._autoload_roots(self.root, self.root),
                 lambda: list(ruby._walk_rb_dir(self.root, loop)))
        for call in calls:
            with self.subTest(call=call), self.assertRaisesRegex(
                    ValueError, "^retained-evidence-layout-refused$"):
                call()

    def test_direct_admission_preserves_lexical_repository_alias(self):
        ruby = importlib.import_module("crux.arch.packs.ruby")
        alias = self.root.parent / (self.root.name + "-alias")
        alias.symlink_to(self.root, target_is_directory=True)
        self.addCleanup(alias.unlink)
        library = self.root / "lib"; library.mkdir()
        (library / "ordinary.rb").write_text("class Ordinary; end\n")
        self.assertEqual(core._retained_source_resolver(alias)(alias / "lib"), library)
        sources = ruby.detect_ruby_sources(alias)
        self.assertEqual([p for _, _, p in sources.entries], [alias / "lib/ordinary.rb"])


if __name__ == "__main__": unittest.main()
