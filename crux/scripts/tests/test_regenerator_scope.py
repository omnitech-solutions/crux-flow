"""A regenerator declares whose tree it is looking at.

rule:regenerator-declares-its-scope, rule:repo-root-comes-from-the-invocation,
rule:out-of-scope-is-surface-absent, rule:authoring-checkout-probe-is-shared,
rule:regenerator-declares-its-third-party-imports,
rule:drift-report-carries-scope-and-reconciles.

WHY THESE TESTS EXIST. Running the drift roster from a consuming project against the
INSTALLED plugin produced five different wrong answers and one right one:

  * `generate-adr-index.py` exited 2 on `ModuleNotFoundError: No module named 'yaml'`.
    It was the one roster regenerator carrying no inline script metadata at all, so
    `uv run` installed nothing and the bare `import yaml` resolved only where an
    ambient environment happened to supply PyYAML -- the authoring checkout.
  * `generate-opencode-agents.py` reported ten agents "added" as that project's drift.
    It derived its root from `__file__`, which under an installed plugin is the plugin
    cache, and the remedy the report named would have written into that cache.
  * `generate-readme-footer.py` exited 2 on a `crux/plugin.json` that was never there.
  * `generate-writing-rules.py` and `generate-routing-table.py` exited 1 with an error
    naming a path inside the plugin cache, filed as BROKEN against the project.
  * `generate-runtime-compat.py` exited 0 reporting 56 targets clean -- the INSTALLED
    PLUGIN's own shipped skills. A vacuous green, recorded as a passing project row.
  * `validate-catalog.py` exited 0 clean, having validated the installed plugin
    against the installed plugin. The same vacuous green.

Each class below is the control for one clause. Every test builds its own roots and
reads only `crux/`, so they RUN against the staged artifact rather than skipping there;
the one class that reads the repo-root roster guards itself with `require_dev_surface`.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import unittest
from pathlib import Path

from _authoring_fixture import seed_authoring_probe
from _dev_surface import REPO_ROOT, require_dev_surface

SCRIPTS = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = SCRIPTS.parent

# The roster's regenerators, split by the scope the enrollment table declares.
PLUGIN_AUTHORING = (
    "validate-catalog.py",
    "generate-opencode-agents.py",
    "generate-readme-footer.py",
    "generate-writing-rules.py",
    "generate-routing-table.py",
    "generate-runtime-compat.py",
    "generate-rules-catalog.py",
)
# The plugin-authoring gates whose canonical input lives OUTSIDE `crux/` -- the
# repo-root `AGENTS.md`, the tree's `AGENTS.md`, this tree's own ADRs. The release
# artifact ships `crux/` without any of them, so on the stage these three correctly
# report surface-absent rather than inspecting anything.
DEV_TREE_GATES = frozenset(
    {"generate-writing-rules.py", "generate-routing-table.py", "generate-rules-catalog.py"}
)

PROJECT_SCOPED = (
    "extract-code-docs.py",
    "derive-arch.py",
    "summarize-adrs.py",
    "compile-doctrine.py",
    "generate-lineage.py",
    "generate-adr-index.py",
    "generate-index-rollup.py",
    "generate-reviews-index.py",
    "generate-journal-index.py",
)
ENROLLED = PLUGIN_AUTHORING + PROJECT_SCOPED

_STDLIB = set(sys.stdlib_module_names) | {"__future__"}


def _sibling_modules() -> set[str]:
    return {p.stem for p in SCRIPTS.glob("*.py")} | {
        p.name for p in SCRIPTS.iterdir() if p.is_dir() and (p / "__init__.py").is_file()
    }


# Import name -> distribution name, where they differ.
_DISTRIBUTION = {"yaml": "pyyaml", "griffe": "griffelib"}


def _third_party_imports(tree_or_path) -> set[str]:
    """UNGUARDED third-party imports: module names that are neither stdlib nor a
    sibling under `crux/scripts/`, reached by a statement that is not inside a `try`.

    Two distinctions this makes on purpose.

    A deferred import inside a FUNCTION body counts -- that is exactly where the
    reported crash lived, and a scanner reading only module-level imports would call
    `generate-adr-index.py` clean.

    An import inside a `try` does NOT count. `validate-catalog.py` and
    `extract-code-docs.py` each guard theirs and fall back to a minimal parser, so
    PyYAML is genuinely optional to them; `generate-lineage.py` guards its own and
    exits 2 with a capability message. A guarded import degrades or reports. An
    unguarded one crashes with a traceback wherever nothing happens to supply it.
    """
    source = (tree_or_path if isinstance(tree_or_path, ast.AST)
              else ast.parse(Path(tree_or_path).read_text(encoding="utf-8")))
    siblings = _sibling_modules()
    guarded: set[int] = set()
    for node in ast.walk(source):
        if isinstance(node, ast.Try):
            for stmt in node.body:
                for inner in ast.walk(stmt):
                    guarded.add(id(inner))
    found: set[str] = set()
    for node in ast.walk(source):
        if id(node) in guarded:
            continue
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [] if node.level else [(node.module or "").split(".")[0]]
        else:
            continue
        found.update(n for n in names if n and n not in _STDLIB and n not in siblings)
    return found


def _root_modules(root: Path) -> set[str]:
    return {p.stem for p in root.glob("*.py")} | {
        p.name for p in root.iterdir() if p.is_dir() and (p / "__init__.py").is_file()
    }


def _module_files(name: str, level: int, origin: Path, imported: tuple[str, ...],
                  root: Path = SCRIPTS) -> list[Path]:
    """The sibling source files one import statement executes, or [] for a non-sibling.

    Importing `a.b.c` runs `a/__init__.py`, `a/b/__init__.py` and then `a/b/c.py` (or
    its package initializer); `from a.b import c` may name the submodule `c`. A relative
    import resolves against the importing file's own package directory."""
    if level:
        base = origin.parent
        for _ in range(level - 1):
            base = base.parent
        if not base.is_relative_to(root):
            return []
        parts = name.split(".") if name else []
    else:
        parts = name.split(".")
        if not parts or parts[0] not in _root_modules(root):
            return []
        base = root
    files: list[Path] = []
    here = base
    for part in parts:
        if (here / part / "__init__.py").is_file():
            here = here / part
            files.append(here / "__init__.py")
        elif (here / f"{part}.py").is_file():
            files.append(here / f"{part}.py")
            return files
        else:
            return files
    for leaf in imported:
        if (here / leaf / "__init__.py").is_file():
            files.append(here / leaf / "__init__.py")
        elif (here / f"{leaf}.py").is_file():
            files.append(here / f"{leaf}.py")
    return files


def _unguarded_imports(source: ast.AST):
    """Every import statement outside a `try` body, deferred ones included."""
    guarded: set[int] = set()
    for node in ast.walk(source):
        if isinstance(node, ast.Try):
            for stmt in node.body:
                for inner in ast.walk(stmt):
                    guarded.add(id(inner))
    return [n for n in ast.walk(source)
            if id(n) not in guarded and isinstance(n, (ast.Import, ast.ImportFrom))]


def _closure_third_party_imports(path: Path, root: Path = SCRIPTS) -> dict[str, list[str]]:
    """Third-party imports reachable from `path` through sibling modules.

    The closure follows every unguarded import of a sibling module or package under
    `crux/scripts/`, deferred imports included, because `uv run <script>` provisions
    only the entry point's PEP 723 block and a sibling's import runs in that same
    process. Returns each third-party module name with the files that import it."""
    seen: set[Path] = set()
    queue = [Path(path).resolve()]
    found: dict[str, list[str]] = {}
    while queue:
        current = queue.pop()
        if current in seen or not current.is_file():
            continue
        seen.add(current)
        tree = ast.parse(current.read_text(encoding="utf-8"))
        for name in _third_party_imports(tree) - _root_modules(root):
            found.setdefault(name, []).append(
                current.relative_to(PLUGIN_ROOT).as_posix()
                if current.is_relative_to(PLUGIN_ROOT) else current.name)
        for node in _unguarded_imports(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    queue.extend(_module_files(alias.name, 0, current, (), root))
            else:
                queue.extend(_module_files(node.module or "", node.level, current,
                                           tuple(a.name for a in node.names), root))
    return found


def _undeclared_in_closure(path: Path, root: Path = SCRIPTS) -> dict[str, list[str]]:
    declared = {d.lower() for d in (_declared_dependencies(path) or set())}
    return {name: sorted(set(files)) for name, files in _closure_third_party_imports(path, root).items()
            if name not in declared and _DISTRIBUTION.get(name, name) not in declared}


def _declared_dependencies(path: Path) -> set[str] | None:
    """The PEP 723 `dependencies` list, or None when the file declares no block."""
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        start = next(i for i, l in enumerate(lines[:15]) if l.strip() == "# /// script")
    except StopIteration:
        return None
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "# ///")
    body = "\n".join(l.lstrip("#").strip() for l in lines[start + 1:end])
    deps: set[str] = set()
    for chunk in body.split("dependencies")[1:]:
        for token in chunk.split("[", 1)[-1].split("]")[0].split(","):
            token = token.strip().strip('"').strip("'")
            if token:
                deps.add(token.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip())
    return deps


class DependencyDeclarationTests(unittest.TestCase):
    """rule:regenerator-declares-its-third-party-imports."""

    def test_every_enrolled_regenerator_declares_its_third_party_imports(self):
        for name in ENROLLED:
            with self.subTest(script=name):
                path = SCRIPTS / name
                imports = _third_party_imports(path)
                if not imports:
                    continue
                declared = _declared_dependencies(path)
                self.assertIsNotNone(
                    declared,
                    f"{name} imports {sorted(imports)} but carries no PEP 723 block, so "
                    "`uv run` installs nothing and the import resolves only where an "
                    "ambient environment supplies it",
                )
                lowered = {d.lower() for d in declared}
                missing = sorted(
                    i for i in imports
                    if i not in lowered and _DISTRIBUTION.get(i, i) not in lowered
                )
                self.assertEqual(
                    [], missing,
                    f"{name} imports {missing} without declaring them in its PEP 723 "
                    f"dependencies (declared: {sorted(declared)})",
                )

    def test_the_adr_index_regenerator_declares_pyyaml(self):
        """The reported crash, pinned by name: it imports yaml at call time."""
        path = SCRIPTS / "generate-adr-index.py"
        self.assertIn("yaml", _third_party_imports(path))
        self.assertIn("pyyaml", {d.lower() for d in (_declared_dependencies(path) or set())})

    def test_the_import_scanner_sees_a_deferred_import(self):
        """Positive control for the scanner itself. A scanner that read only module-level
        imports would call the defect clean, because the crashing import sits in a
        function body."""
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.py"
            probe.write_text("def f():\n    import yaml\n    return yaml\n", encoding="utf-8")
            self.assertEqual({"yaml"}, _third_party_imports(probe))

            guarded = Path(tmp) / "guarded.py"
            guarded.write_text(
                "def f():\n    try:\n        import yaml\n    except ImportError:\n"
                "        return None\n    return yaml\n", encoding="utf-8")
            self.assertEqual(set(), _third_party_imports(guarded),
                             "a guarded import degrades or reports; it is not undeclared")


# Entry points outside the regenerator roster that the clause-migration pilot added or
# changed. Each reaches a third-party import only through a sibling module, which is the
# class the direct scan above cannot see.
CLOSURE_CHECKED_ENTRY_POINTS = (
    "implementation-migration.py",
    "implementation-decisions.py",
    "advance-run.py",
    "authority-view.py",
)


class ImportClosureDeclarationTests(unittest.TestCase):
    """rule:regenerator-declares-its-third-party-imports, over the import closure.

    `uv run <script>` provisions the entry point's PEP 723 block and nothing else, so an
    import a sibling module performs is the entry point's import. `generate-reviews-index.py`
    declared no dependencies while it reached PyYAML through a sibling, and the direct scan
    called it clean while it exited 2 in its declared environment."""

    def test_every_enrolled_regenerator_declares_its_import_closure(self):
        for name in ENROLLED + CLOSURE_CHECKED_ENTRY_POINTS:
            with self.subTest(script=name):
                path = SCRIPTS / name
                self.assertEqual(
                    {}, _undeclared_in_closure(path),
                    f"{name} reaches third-party modules through sibling imports without "
                    f"declaring them in its PEP 723 dependencies (declared: "
                    f"{sorted(_declared_dependencies(path) or set())})",
                )

    def test_positive_control_a_sibling_import_is_followed(self):
        """The closure sees a deferred third-party import two sibling hops away, and a
        guarded sibling import is not followed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = root / "probe_pkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "inner.py").write_text("def f():\n    import httpx\n", encoding="utf-8")
            (pkg / "outer.py").write_text("from . import inner\n", encoding="utf-8")
            entry = root / "entry.py"
            entry.write_text(
                "# /// script\n# dependencies = [\"pyyaml>=6.0,<7\"]\n# ///\n"
                f"import {pkg.name}.outer\nimport yaml\n", encoding="utf-8")
            self.assertEqual(["httpx"], sorted(_undeclared_in_closure(entry, root)))
            entry.write_text(
                "# /// script\n# dependencies = []\n# ///\n"
                f"try:\n    import {pkg.name}.outer\nexcept ImportError:\n    pass\n",
                encoding="utf-8")
            self.assertEqual({}, _undeclared_in_closure(entry, root))


class ExtractorImportDeclarationTests(unittest.TestCase):
    """rule:regenerator-declares-its-third-party-imports, extended to the extractors.

    The code-doc dispatcher loads every module under `crux/scripts/extractors/` into its
    own process, so an extractor's third-party import is the dispatcher's import. ADR-0131
    clause 5 extends the scanner there: every such import must be declared in the
    dispatcher's PEP 723 block, or `uv run` resolves nothing for it.
    """

    EXTRACTORS = SCRIPTS / "extractors"
    DISPATCHER = SCRIPTS / "extract-code-docs.py"

    def _extractor_imports(self, path: Path) -> set[str]:
        local = {p.stem for p in self.EXTRACTORS.glob("*.py")}
        return {i for i in _third_party_imports(path) if i not in local}

    def _undeclared(self, path: Path, declared: set[str]) -> list[str]:
        lowered = {d.lower() for d in declared}
        return sorted(i for i in self._extractor_imports(path)
                      if i not in lowered and _DISTRIBUTION.get(i, i) not in lowered)

    def test_every_extractor_import_is_declared_by_the_dispatcher(self):
        declared = _declared_dependencies(self.DISPATCHER) or set()
        scanned = sorted(self.EXTRACTORS.glob("*.py"))
        self.assertTrue(scanned, "no extractor module found; the scanner has no subject")
        for path in scanned:
            with self.subTest(extractor=path.name):
                self.assertEqual(
                    [], self._undeclared(path, declared),
                    f"{path.name} imports a third-party module the dispatcher's PEP 723 "
                    f"block does not declare (declared: {sorted(declared)})",
                )

    def test_the_python_extractor_imports_griffe_and_the_dispatcher_declares_it(self):
        """Non-vacuity: the scan above has at least one real third-party import to judge."""
        found = set()
        for path in self.EXTRACTORS.glob("*.py"):
            found |= self._extractor_imports(path)
        self.assertIn("griffe", found)
        self.assertIn("griffelib", {d.lower() for d in _declared_dependencies(self.DISPATCHER) or set()})

    def test_positive_control_an_undeclared_extractor_import_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.py"
            probe.write_text("import griffe\nimport attrs\n", encoding="utf-8")
            self.assertEqual(["attrs"], self._undeclared(probe, {"griffelib==2.3.0".split("=")[0]}))
            self.assertEqual(["attrs", "griffe"], self._undeclared(probe, set()))


class RepoRootResolutionTests(unittest.TestCase):
    """rule:repo-root-comes-from-the-invocation."""

    FORBIDDEN = (
        "Path(__file__).resolve().parent.parent.parent",
        "Path(__file__).resolve().parents[2]",
    )

    def test_no_enrolled_regenerator_derives_a_repo_root_from_its_own_path(self):
        for name in ENROLLED:
            with self.subTest(script=name):
                text = (SCRIPTS / name).read_text(encoding="utf-8")
                for expr in self.FORBIDDEN:
                    # `generate-opencode-agents.py` keeps the expression to name the
                    # checkout it SHIPS IN; what it may not do is use that as the root
                    # under inspection, which the surface-absent control below proves.
                    if expr in text and name == "generate-opencode-agents.py":
                        self.assertIn("_scope.resolve_repo_root", text)
                        continue
                    self.assertNotIn(
                        expr, text,
                        f"{name} derives a root from its own file path. Under an installed "
                        "plugin that is the plugin cache, so every read, verdict and "
                        "recommended write aims at the plugin instead of the project.",
                    )

    # The project-scoped regenerators name their target with their own flag --
    # `--config` for the code extractor, `--docs-dir` for the deriver. The rule is
    # that the target comes from the invocation, not that every script spells it the
    # same way; what none of them may do is derive it from `__file__`.
    TARGET_FLAGS = ("--repo-root", "--docs-dir", "--config")

    def test_every_enrolled_regenerator_takes_its_target_from_the_invocation(self):
        for name in ENROLLED:
            with self.subTest(script=name):
                proc = subprocess.run(
                    [sys.executable, str(SCRIPTS / name), "--help"],
                    capture_output=True, text=True, check=False,
                )
                usage = proc.stdout + proc.stderr
                self.assertTrue(
                    any(flag in usage for flag in self.TARGET_FLAGS),
                    f"{name} offers none of {self.TARGET_FLAGS}, so the tree it acts on "
                    "cannot be named and can only be guessed from its own location",
                )


class OutOfScopeTests(unittest.TestCase):
    """rule:out-of-scope-is-surface-absent — the consuming-project lane."""

    def _run(self, name: str, root: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / name), "--dry-run", "--repo-root", root],
            capture_output=True, text=True, check=False,
        )

    def test_a_plugin_authoring_gate_reports_surface_absent_outside_the_checkout(self):
        for name in PLUGIN_AUTHORING:
            with self.subTest(script=name):
                with tempfile.TemporaryDirectory() as tmp:
                    # A consuming project: a documentation tree, no plugin source.
                    root = Path(tmp)
                    (root / "docs" / "adrs").mkdir(parents=True)
                    (root / ".bionic.yml").write_text(
                        'config_version: "1"\ndocs_dir: docs\n', encoding="utf-8")
                    before = sorted(p.relative_to(root) for p in root.rglob("*"))

                    result = self._run(name, str(root))

                    self.assertEqual(
                        0, result.returncode,
                        f"{name}: exit {result.returncode} in a consuming project.\n"
                        f"stdout={result.stdout!r}\nstderr={result.stderr!r}",
                    )
                    payload = json.loads(result.stdout)
                    self.assertTrue(
                        payload.get("surface_absent"),
                        f"{name} returned {payload} -- a verdict about a project that owns "
                        "none of this gate's surfaces",
                    )
                    self.assertFalse(payload.get("drift"))
                    self.assertTrue(
                        (payload.get("reason") or "").strip(),
                        f"{name}: surface_absent with no reason says nothing to the reader",
                    )
                    self.assertEqual(
                        before, sorted(p.relative_to(root) for p in root.rglob("*")),
                        f"{name} wrote into a project it had already declined to inspect",
                    )

    VERDICT_KEYS = ("drift", "drifted", "added", "clean")

    def test_a_plugin_authoring_gate_still_inspects_its_own_checkout(self):
        """POSITIVE CONTROL. Without it every assertion above is satisfied by a script
        that reports surface_absent unconditionally and checks nothing, anywhere.

        It asserts a REACHED VERDICT, not merely the absence of `surface_absent`. The
        first draft read `json.loads(stdout) if stdout.strip() else {}` and asserted
        only that absence, so exit 2 with empty stdout and exit 1 with an `error`
        payload both passed -- two of the seven "controls" were green having inspected
        nothing. That is the shape this suite exists to catch.
        """
        for name in PLUGIN_AUTHORING:
            with self.subTest(script=name):
                if name in DEV_TREE_GATES:
                    # Its canonical input lives outside `crux/`, so the staged artifact
                    # and the public clone are NOT its checkout and it correctly
                    # declines there. That case is measured positively by
                    # `test_a_plugin_source_tree_without_a_documentation_tree_is_not_a_failure`,
                    # which builds its own clone and runs everywhere.
                    require_dev_surface(self, REPO_ROOT / "AGENTS.md", "repo-root AGENTS.md")
                result = self._run(name, str(REPO_ROOT))
                self.assertIn(
                    result.returncode, (0, 1),
                    f"{name}: exit {result.returncode} on the plugin's own checkout\n"
                    f"{result.stdout}\n{result.stderr}")
                self.assertTrue(result.stdout.strip(), f"{name}: empty stdout")
                payload = json.loads(result.stdout)
                self.assertFalse(
                    payload.get("surface_absent"),
                    f"{name} declined to inspect the plugin's own authoring checkout")
                self.assertNotIn("error", payload, f"{name}: {payload}")
                self.assertTrue(
                    any(k in payload for k in self.VERDICT_KEYS),
                    f"{name} printed no verdict key {self.VERDICT_KEYS}: {payload}")

    def test_a_plugin_source_tree_without_a_documentation_tree_is_not_a_failure(self):
        """The public clone and the staged release artifact: `crux/` is present, the
        documentation tree and the repo-root `AGENTS.md` are not.

        rule:out-of-scope-is-surface-absent names TWO triggers, and only the first --
        no plugin source -- was implemented at first. These roots pass that probe, so
        three gates exited non-zero naming a file that never ships, and `check-drift`
        filed two BROKEN rows and one CRASH against an artifact that owns no such
        surface. Built here rather than read from disk, so it measures on the stage.
        """
        plugin = PLUGIN_ROOT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "clone"
            (root).mkdir()
            # A faithful public clone: the whole plugin directory, nothing else.
            # `tests/` is excluded: it is not part of what these three gates read, and
            # its arch corpus cache carries dangling symlinks that fail a plain copy.
            shutil.copytree(
                plugin, root / "crux",
                ignore=shutil.ignore_patterns("tests", "__pycache__", "*.pyc"),
                ignore_dangling_symlinks=True)
            (root / "README.md").write_text(
                "# crux\n\nMIT licensed (see [LICENSE](./LICENSE)). v0.0.0 tail\n",
                encoding="utf-8")
            for name in ("generate-writing-rules.py", "generate-routing-table.py",
                         "generate-rules-catalog.py"):
                with self.subTest(script=name):
                    result = subprocess.run(
                        [sys.executable, str(root / "crux" / "scripts" / name),
                         "--dry-run", "--repo-root", str(root)],
                        capture_output=True, text=True, check=False)
                    self.assertEqual(
                        0, result.returncode,
                        f"{name}: exit {result.returncode} on a tree that owns none of "
                        f"its surfaces\n{result.stdout}\n{result.stderr}")
                    payload = json.loads(result.stdout)
                    self.assertTrue(payload.get("surface_absent"), payload)
                    self.assertTrue((payload.get("reason") or "").strip(), payload)

    def test_the_probe_distinguishes_the_two_roots(self):
        """The discriminator itself, not just the branches it picks."""
        scope = _load_scope()
        with tempfile.TemporaryDirectory() as tmp:
            consumer = Path(tmp) / "consumer"
            (consumer / "docs").mkdir(parents=True)
            authoring = Path(tmp) / "authoring"
            seed_authoring_probe(authoring, "generate-writing-rules.py")
            self.assertFalse(scope.is_authoring_checkout(consumer, "generate-writing-rules.py"))
            self.assertTrue(scope.is_authoring_checkout(authoring, "generate-writing-rules.py"))
            # Keyed on THIS script's own name, so a checkout carrying some other
            # `crux/scripts/` file does not pass for every regenerator at once.
            self.assertFalse(scope.is_authoring_checkout(authoring, "summarize-adrs.py"))

    def test_the_default_root_is_the_working_directory_not_the_script_location(self):
        """The reported shape end to end: no --repo-root at all, invoked from a
        consuming project. This is how `check-drift` actually calls them."""
        for name in PLUGIN_AUTHORING:
            with self.subTest(script=name):
                with tempfile.TemporaryDirectory() as tmp:
                    result = subprocess.run(
                        [sys.executable, str(SCRIPTS / name), "--dry-run"],
                        capture_output=True, text=True, check=False, cwd=tmp,
                    )
                    self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                    self.assertTrue(json.loads(result.stdout).get("surface_absent"))


def _load_scope():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_scope_probe", SCRIPTS / "authoring_scope.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SharedProbeTests(unittest.TestCase):
    """rule:authoring-checkout-probe-is-shared."""

    def test_every_plugin_authoring_regenerator_accepts_repo_root(self):
        """Narrower than the roster-wide flag test: a plugin-authoring gate is the one
        `check-drift` may need to point at a specific checkout."""
        for name in PLUGIN_AUTHORING:
            with self.subTest(script=name):
                proc = subprocess.run(
                    [sys.executable, str(SCRIPTS / name), "--help"],
                    capture_output=True, text=True, check=False)
                self.assertIn("--repo-root", proc.stdout + proc.stderr)

    def test_every_plugin_authoring_regenerator_calls_the_shared_probe(self):
        for name in PLUGIN_AUTHORING:
            with self.subTest(script=name):
                text = (SCRIPTS / name).read_text(encoding="utf-8")
                self.assertIn("authoring_scope", text)
                self.assertIn("is_authoring_checkout", text)

    def test_no_regenerator_reimplements_the_probe_inline(self):
        inline = '"scripts" / Path(__file__).name'
        for path in SCRIPTS.glob("*.py"):
            if path.name == "authoring_scope.py":
                continue
            with self.subTest(script=path.name):
                self.assertNotIn(
                    inline, path.read_text(encoding="utf-8"),
                    f"{path.name} carries its own copy of the authoring-checkout probe; "
                    "two copies can disagree about what the authoring checkout is",
                )


class ReadableVerdictTests(unittest.TestCase):
    """rule:drift-report-carries-scope-and-reconciles.

    A row the report cannot parse is a row it cannot classify, and an unclassified
    row breaks the reconciliation identity as surely as a miscounted one. Both
    `check-drift` and `audit-docs` CHK-DRIFT-1 say to parse each gate's stdout as
    JSON regardless of exit code. Three gates printed something else: a prose diff
    (`generate-lineage.py`, `generate-index-rollup.py`) and, on the clean lane,
    nothing at all (`generate-readme-footer.py`).

    These build their own trees rather than reading this repo's, so they MEASURE
    against the staged artifact instead of skipping there -- a skip is no evidence
    about the thing that ships. The first draft read `REPO_ROOT` and false-failed
    on the stage, which carries no documentation tree.
    """

    ADR = ("---\nid: ADR-0001\ntitle: Seeded\nstatus: Accepted\n"
           "date: 2026-01-01\nsupersedes: []\namends: []\n"
           "superseded_by: null\ntags: []\n---\n\n# body\n")

    def _tree(self, tmp: Path, *, index_body: str | None = None,
              lineage: str | None = None) -> Path:
        root = Path(tmp)
        (root / ".bionic.yml").write_text(
            'config_version: "1"\ndocs_dir: docs\n', encoding="utf-8")
        adrs = root / "docs" / "adrs"
        adrs.mkdir(parents=True)
        (adrs / "ADR-0001-seeded.md").write_text(self.ADR, encoding="utf-8")
        if lineage is not None:
            (adrs / "lineage.md").write_text(lineage, encoding="utf-8")
        if index_body is not None:
            (root / "docs" / "index.md").write_text(index_body, encoding="utf-8")
        return root

    def _run(self, name: str, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / name), "--dry-run", "--repo-root", str(root)],
            capture_output=True, text=True, check=False)

    def _assert_json_verdict(self, name, result, *, drift: bool):
        self.assertIn(result.returncode, (0, 1),
                      f"{name}: exit {result.returncode}\n{result.stdout}\n{result.stderr}")
        self.assertTrue(
            result.stdout.strip(),
            f"{name} exited {result.returncode} with EMPTY stdout -- a reader parsing "
            "stdout as JSON learns nothing and cannot classify the row")
        payload = json.loads(result.stdout)
        self.assertEqual(drift, payload.get("drift"), payload)
        return payload

    def _rollup_index(self, rows: str) -> str:
        return ("# Index\n\n## ADRs (1)\n\n| id | title | status | date |\n"
                "|---|---|---|---|\n" + rows + "\n")

    def test_the_lineage_gate_is_parseable_on_both_lanes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(Path(tmp), lineage="stale\n")
            drifted = self._assert_json_verdict(
                "generate-lineage.py", self._run("generate-lineage.py", root), drift=True)
            self.assertTrue(drifted["paths"])
            self.assertIn("diff", drifted, "the diff is what a reader wants; keep it as a field")

            subprocess.run(
                [sys.executable, str(SCRIPTS / "generate-lineage.py"),
                 "--repo-root", str(root)], capture_output=True, text=True, check=True)
            self._assert_json_verdict(
                "generate-lineage.py", self._run("generate-lineage.py", root), drift=False)

    def test_the_rollup_gate_is_parseable_on_both_lanes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(Path(tmp), index_body=self._rollup_index(
                "| [[adrs/WRONG]] | Wrong | Accepted | 2026-01-01 |"))
            drifted = self._assert_json_verdict(
                "generate-index-rollup.py",
                self._run("generate-index-rollup.py", root), drift=True)
            self.assertTrue(drifted["paths"])
            self.assertIn("diff", drifted)

            subprocess.run(
                [sys.executable, str(SCRIPTS / "generate-index-rollup.py"),
                 "--repo-root", str(root)], capture_output=True, text=True, check=True)
            self._assert_json_verdict(
                "generate-index-rollup.py",
                self._run("generate-index-rollup.py", root), drift=False)

    def test_the_readme_footer_gate_prints_a_verdict_when_clean(self):
        """It used to print nothing at all on the clean lane. This runs against the
        plugin's own checkout, which the staged artifact also is."""
        result = self._run("generate-readme-footer.py", REPO_ROOT)
        self._assert_json_verdict("generate-readme-footer.py", result, drift=False)


class ReleaseContentScopeTests(unittest.TestCase):
    """rule:out-of-scope-is-surface-absent, applied to the one non-regenerator a
    shipped skill reaches.

    `tend-garden` invokes `check-public-release-content.py`, and the skill's own prose
    says not to run crux-specific commands against a non-crux tree. Prose is not a
    gate: the script defaulted its root to its own file path, so a downstream run
    scanned the INSTALLED plugin and exited 0 -- a verdict about the plugin, reported
    to a reader who asked about their project.
    """

    SCRIPT = SCRIPTS / "check-public-release-content.py"

    def test_it_declines_a_project_that_holds_no_distributed_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "docs").mkdir()
            proc = subprocess.run(
                [sys.executable, str(self.SCRIPT), "--root", tmp],
                capture_output=True, text=True, check=False)
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            self.assertIn("not the plugin's authoring checkout", proc.stderr)
            self.assertEqual("", proc.stdout.strip(),
                             "a declined scan reports no findings about a tree it did not read")

    def test_it_still_scans_the_plugins_own_checkout(self):
        """POSITIVE CONTROL. Without it the assertion above is satisfied by a script
        that declines everywhere and scans nothing."""
        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), "--root", str(REPO_ROOT), "-v"],
            capture_output=True, text=True, check=False)
        self.assertIn(proc.returncode, (0, 1), proc.stdout + proc.stderr)
        self.assertNotIn("not the plugin's authoring checkout", proc.stderr)

    def test_a_seeded_violation_still_turns_it_red(self):
        """POSITIVE CONTROL for the scan itself: the decline lane must not have
        swallowed the finding lane."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "crux" / "scripts").mkdir(parents=True)
            (root / "crux" / "scripts" / self.SCRIPT.name).write_text("# probe\n",
                                                                     encoding="utf-8")
            skill = root / "crux" / "skills" / "probe"
            skill.mkdir(parents=True)
            skill.joinpath("SKILL.md").write_text(
                "# Probe\n\nSee ADR-0042 for the rationale.\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(self.SCRIPT), "--root", str(root)],
                capture_output=True, text=True, check=False)
            self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
            self.assertIn("ADR-0042", proc.stdout)


class RosterScopeTests(unittest.TestCase):
    """rule:regenerator-declares-its-scope and
    rule:drift-report-carries-scope-and-reconciles."""

    def test_the_enrollment_roster_declares_a_scope_for_every_row(self):
        claude = REPO_ROOT / "AGENTS.md"
        require_dev_surface(self, claude, "repo-root AGENTS.md")
        lines = claude.read_text(encoding="utf-8").splitlines()
        head = next(i for i, l in enumerate(lines) if l.startswith("| Output | Source of truth |"))
        self.assertTrue(lines[head].rstrip().endswith("| Scope |"), lines[head])
        rows, i = 0, head + 2
        while i < len(lines) and lines[i].startswith("|"):
            scope = lines[i].rstrip().rsplit("|", 2)[1].strip()
            self.assertIn(scope, {"project", "plugin-authoring"}, lines[i][:80])
            rows += 1
            i += 1
        self.assertEqual(17, rows, "roster row count moved; update the scope map here too")

    def test_the_check_drift_gate_table_declares_the_same_scopes(self):
        skill = SCRIPTS.parent / "skills" / "check-drift" / "SKILL.md"
        lines = skill.read_text(encoding="utf-8").splitlines()
        head = next(i for i, l in enumerate(lines) if l.startswith("| Gate (output) | Scope |"))
        seen: dict[str, str] = {}
        i = head + 2
        while i < len(lines) and lines[i].startswith("|"):
            cells = [c.strip() for c in lines[i].split("|")]
            scope, command = cells[2], cells[3]
            self.assertIn(scope, {"project", "plugin-authoring"}, lines[i][:80])
            for name in ENROLLED:
                if name in command:
                    seen[name] = scope
            i += 1
        for name in PLUGIN_AUTHORING:
            self.assertEqual("plugin-authoring", seen.get(name), f"{name} in check-drift")
        for name in PROJECT_SCOPED:
            self.assertEqual("project", seen.get(name), f"{name} in check-drift")


if __name__ == "__main__":
    unittest.main()
