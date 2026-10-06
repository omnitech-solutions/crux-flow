"""Loader-level hostile fixtures (ADR-0131 clauses 1 and 7).

Loads every source under
crux/scripts/tests/fixtures/python_code_docs/hostile_loading/src/ through
`_py_load.load_selection` and asserts none of it writes its marker file.
Each absence assertion is paired with a positive control (per
false-green-test-guard's FG1 signature): the SAME fixture, actually
imported in a fresh subprocess with the same `CRUX_HOSTILE_MARKER_DIR`, does
write its marker — proof the fixture is genuinely hostile and the absence in
the loader test is not vacuous.

Never pastes hostile fixture content into this file: every fixture is read
from its committed path and referenced only by filename and marker name.
This is loader-level coverage. The CLI-level variant, which runs the
dispatcher `extract-code-docs` as a subprocess over the same fixtures, is
`HostileLoadingCliTests` in test_python_extractor.py.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS = REPO_ROOT / "crux" / "scripts"
LOAD_PATH = SCRIPTS / "extractors" / "_py_load.py"
DISPATCHER_PATH = SCRIPTS / "extract-code-docs.py"
HOSTILE_DIR = SCRIPTS / "tests" / "fixtures" / "python_code_docs" / "hostile_loading" / "src"


def _load_dispatcher():
    cached = sys.modules.get("extract_code_docs_dispatcher")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location("extract_code_docs_dispatcher", DISPATCHER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["extract_code_docs_dispatcher"] = mod
    spec.loader.exec_module(mod)
    return mod


_load_dispatcher()


def _load_py_load():
    spec = importlib.util.spec_from_file_location("crux_py_load_hostile_t", LOAD_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["crux_py_load_hostile_t"] = mod
    spec.loader.exec_module(mod)
    return mod


load_mod = _load_py_load()

#: Every source under hostile_loading/src/, repo-relative to that directory.
#: The compiled-extension-shaped sibling (`h9_compiled_extension.cpython-*.so`)
#: is deliberately excluded — a `.so` file is not a Python source unit, and
#: this list is the one place that decides what becomes a `units` entry.
#: Its exclusion here IS the "never touched" guarantee: the loader receives
#: no bytes from it at all, so it cannot be read, let alone imported.
_ALL_HOSTILE_SOURCES = sorted(
    p.relative_to(HOSTILE_DIR).as_posix()
    for p in HOSTILE_DIR.rglob("*")
    if p.is_file() and p.suffix in (".py", ".pyi")
)

#: rel_path -> the marker filename its own `marker_path(__file__, name)` call
#: uses (module docstrings under hostile_loading/README's absent case; the
#: names are also visible directly in each fixture's own source, read below).
_MARKERS = {
    "h1_import_time_write.py": "h1.EXECUTED",
    "h2_missing_dependency.py": "h2.EXECUTED",
    "h3_decorator_side_effect.py": "h3.EXECUTED",
    "h4_default_argument.py": "h4.EXECUTED",
    "h5_class_body.py": ("h5-meta.EXECUTED", "h5-body.EXECUTED"),
    "h6_dynamic_all.py": "h6.EXECUTED",
    "h7_module_getattr.py": "h7.EXECUTED",
    "h9_compiled_extension.py": "h9.EXECUTED",
    "h10_extension_registration.py": "h10.EXECUTED",
    "hostile_pkg/__init__.py": "hostile_pkg-init.EXECUTED",
    "hostile_pkg/sub.py": "hostile_pkg-sub.EXECUTED",
}


def _all_marker_names() -> list[str]:
    names: list[str] = []
    for value in _MARKERS.values():
        if isinstance(value, tuple):
            names.extend(value)
        else:
            names.append(value)
    return names


def _units(rel_paths: list[str]) -> list[tuple[str, bytes]]:
    return [(rel, (HOSTILE_DIR / rel).read_bytes()) for rel in rel_paths]


def _run_positive_control(rel_path: str, marker_dir: Path, *, touch_getattr: bool = False) -> None:
    """Actually import `rel_path` in a fresh subprocess: proof it is hostile.

    Runs out-of-process (never in this test's own interpreter) so an
    import that raises, writes global state, or otherwise misbehaves can
    never contaminate this test process or a sibling test's assertions.

    `h9_compiled_extension.py` is loaded by EXPLICIT file path
    (`importlib.util.spec_from_file_location`), never by a bare `import
    <name>` on `sys.path`: it is named identically to a same-directory
    compiled-extension-shaped sibling (`h9_compiled_extension.cpython-*.so`),
    and a name-based import lets the module finder's suffix priority
    resolve the `.so` first — proving nothing about the `.py` file this
    control means to exercise. Every other fixture imports by dotted name
    with `HOSTILE_DIR` on `sys.path`, so `hostile_pkg/__init__.py`'s own
    relative import (`from .sub import child`) resolves through the normal
    package-import machinery rather than a hand-rolled substitute.
    """
    is_h9 = rel_path == "h9_compiled_extension.py"
    getattr_line = "    _m.__getattr__('probe')\n" if touch_getattr else ""
    if is_h9:
        code = (
            "import sys, os, importlib.util\n"
            f"sys.path.insert(0, {str(HOSTILE_DIR)!r})\n"  # for h9's own `from _hostile_marker import ...`
            f"os.environ['CRUX_HOSTILE_MARKER_DIR'] = {str(marker_dir)!r}\n"
            f"_path = {str(HOSTILE_DIR / rel_path)!r}\n"
            "_spec = importlib.util.spec_from_file_location('h9_pc', _path)\n"
            "_m = importlib.util.module_from_spec(_spec)\n"
            "try:\n"
            "    _spec.loader.exec_module(_m)\n"
            f"{getattr_line}"
            "except BaseException:\n"
            "    pass\n"
        )
    else:
        module_name = rel_path[: -len(".py")].replace("/", ".")
        code = (
            "import sys, os\n"
            f"sys.path.insert(0, {str(HOSTILE_DIR)!r})\n"
            f"os.environ['CRUX_HOSTILE_MARKER_DIR'] = {str(marker_dir)!r}\n"
            "try:\n"
            f"    _m = __import__({module_name!r}, fromlist=['*'])\n"
            f"{getattr_line}"
            "except BaseException:\n"
            "    pass\n"  # h2/h10 raise after writing their marker; that's expected
        )
    subprocess.run(
        [sys.executable, "-B", "-c", code],  # -B: no __pycache__ in the fixture tree
        check=False,
        capture_output=True,
        timeout=30,
    )


class HostileLoadingWritesNoMarkerTests(unittest.TestCase):
    """One absence assertion covering the whole selection, per marker."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.marker_dir = Path(cls._tmp.name)
        cls._old_env = os.environ.get("CRUX_HOSTILE_MARKER_DIR")
        os.environ["CRUX_HOSTILE_MARKER_DIR"] = str(cls.marker_dir)
        cls.selection = load_mod.load_selection(_units(_ALL_HOSTILE_SOURCES))

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._old_env is None:
            os.environ.pop("CRUX_HOSTILE_MARKER_DIR", None)
        else:
            os.environ["CRUX_HOSTILE_MARKER_DIR"] = cls._old_env
        cls._tmp.cleanup()

    def test_the_whole_selection_loads(self):
        """Sanity: every hostile source still parses and builds a module —
        the absence of a marker below is not merely "it crashed before
        reaching the marker line"."""
        self.assertEqual(len(self.selection.sources), len(_ALL_HOSTILE_SOURCES))

    def test_no_marker_file_exists_after_loading_the_whole_selection(self):
        written = sorted(p.name for p in self.marker_dir.iterdir())
        self.assertEqual(written, [], f"loading wrote marker(s): {written}")

    def test_the_compiled_extension_shaped_sibling_is_never_a_unit(self):
        """The `.so` sibling of h9 is not, and must never become, a `units`
        entry — see `_ALL_HOSTILE_SOURCES`'s own docstring above."""
        so_siblings = [p.name for p in HOSTILE_DIR.glob("h9_compiled_extension.*") if p.suffix != ".py"]
        self.assertTrue(so_siblings, "fixture regression: no compiled-extension sibling found to guard")
        for name in so_siblings:
            self.assertNotIn(name, _ALL_HOSTILE_SOURCES)

    def test_no_extension_other_than_function_local_defs_is_active(self):
        """h10 attempts, at import time, to register a third-party Griffe
        extension. Loading the hostile selection never imports h10, so every
        visit runs with exactly the one Crux extension: the set of extension
        types each `griffe.Extensions` receives is {FunctionLocalDefs}."""
        import griffe

        real_extensions = griffe.Extensions
        seen: list[set[type]] = []

        class _Spy(real_extensions):  # type: ignore[misc, valid-type]
            def __init__(self, *exts):
                seen.append({type(e) for e in exts})
                super().__init__(*exts)

        griffe.Extensions = _Spy
        try:
            load_mod.load_selection(_units(_ALL_HOSTILE_SOURCES))
        finally:
            griffe.Extensions = real_extensions
        self.assertTrue(seen, "no visit constructed an extension set")
        for active in seen:
            self.assertEqual(active, {load_mod.FunctionLocalDefs})


def _make_marker_absence_test(rel_path: str, marker_name: str):
    def _test(self: "PerFixtureMarkerTests") -> None:
        marker = self.marker_dir_shared / marker_name
        self.assertFalse(marker.exists(), f"{rel_path} wrote {marker_name} while only being loaded")

    return _test


def _make_positive_control_test(rel_path: str, marker_name: str):
    # h7's marker fires from `__getattr__`, on attribute ACCESS, never on
    # plain import — the control must touch a missing attribute for it.
    touch_getattr = rel_path == "h7_module_getattr.py"

    def _test(self: "PerFixtureMarkerTests") -> None:
        with tempfile.TemporaryDirectory() as td:
            marker_dir = Path(td)
            _run_positive_control(rel_path, marker_dir, touch_getattr=touch_getattr)
            self.assertTrue(
                (marker_dir / marker_name).exists(),
                f"positive control: importing {rel_path} did not write {marker_name} — "
                "fixture is not actually hostile, or the marker name drifted",
            )

    return _test


class PerFixtureMarkerTests(unittest.TestCase):
    """Per-fixture (absence, positive-control) pairs — see module docstring."""

    marker_dir_shared: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.marker_dir_shared = Path(cls._tmp.name)
        old_env = os.environ.get("CRUX_HOSTILE_MARKER_DIR")
        os.environ["CRUX_HOSTILE_MARKER_DIR"] = str(cls.marker_dir_shared)
        try:
            load_mod.load_selection(_units(_ALL_HOSTILE_SOURCES))
        finally:
            if old_env is None:
                os.environ.pop("CRUX_HOSTILE_MARKER_DIR", None)
            else:
                os.environ["CRUX_HOSTILE_MARKER_DIR"] = old_env

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()


for _rel, _marker_value in _MARKERS.items():
    _marker_names = _marker_value if isinstance(_marker_value, tuple) else (_marker_value,)
    for _marker_name in _marker_names:
        _slug = _marker_name.replace(".", "_").replace("-", "_")
        setattr(
            PerFixtureMarkerTests,
            f"test_absence_{_slug}",
            _make_marker_absence_test(_rel, _marker_name),
        )
        setattr(
            PerFixtureMarkerTests,
            f"test_positive_control_{_slug}",
            _make_positive_control_test(_rel, _marker_name),
        )
del _rel, _marker_value, _marker_names, _marker_name, _slug


class MarkerCoverageTests(unittest.TestCase):
    """Every hostile fixture with a marker is in `_MARKERS`; nothing drifted."""

    def test_every_hostile_source_except_the_shared_helper_has_a_marker_entry(self):
        sources_with_markers = set(_MARKERS)
        expected = set(_ALL_HOSTILE_SOURCES) - {"_hostile_marker.py"}
        self.assertEqual(sources_with_markers, expected)


if __name__ == "__main__":
    unittest.main()
