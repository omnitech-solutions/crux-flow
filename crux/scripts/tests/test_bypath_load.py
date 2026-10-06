"""Every module loaded BY PATH must import cleanly with `crux/scripts/` off `sys.path`.

The blind spot this closes, stated as a class rather than as its instance:

    `check_observations.py` and `check_invariants.py` load their siblings with
    `importlib.util.spec_from_file_location` + `exec_module`. That loader puts
    NOTHING on `sys.path` — the loaded module gets the importer's path, not its
    own directory. Under pytest `crux/scripts/` happens to be on `sys.path`, so
    a plain `from untrusted import redact` inside a by-path-loaded module
    resolves, every test passes, and the same line raises `ModuleNotFoundError`
    the moment `audit-docs` loads the caller by path.

That is a false green the whole suite shares: 3348 tests all run in an
environment the production caller does not have. `bionic_config.py` shipped
exactly this defect and no test could see it.

So the environment is the thing under test here. Each by-path-loaded module is
executed in a SUBPROCESS whose `sys.path` has `crux/scripts/` removed and whose
`PYTHONPATH` is cleared, which is the importer's real environment rather than
pytest's.

**The driver carries its own positive control.** A scrub that silently failed
would make every assertion below pass for the wrong reason — the modules would
load because the directory was still importable. So the driver asserts, before
it loads anything, that `import untrusted` RAISES in its own process. If the
scrub did not take, the driver exits non-zero and the test fails; the green can
only mean what it says.

The module list is DERIVED from the two callers, never hardcoded, so a new
by-path load is covered on the day it is written. The derivation works from the
`spec_from_file_location` CALL SITES and resolves each one's location argument
through the literals, f-strings, `Path(...)` constructors, and `/` divisions
the callers actually use. Anything it cannot resolve to a filename FAILS THE
TEST, named by file and line. An earlier cut read string literals only, so a
load built as `parent / f"{stem}.py"` was dropped in silence and the guard
below stayed green — a gate that quietly stops covering a file is the shape
this whole test exists to refuse, and under-approximating in silence was that
shape.
"""
from __future__ import annotations

import ast
import os
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path, PurePosixPath

SCRIPTS = Path(__file__).resolve().parents[1]

# The two modules that load siblings by path. Their by-path loaders are the
# subject; the list of what they load is derived from their source below.
CALLERS = ("check_observations.py", "check_invariants.py")

# Executed in a subprocess with `crux/scripts/` scrubbed from `sys.path`.
# `argv[1]` is the module to load; `argv[2]` is the directory to scrub.
_DRIVER = r'''
import importlib.util, sys
from pathlib import Path

target = Path(sys.argv[1]).resolve()
scripts = Path(sys.argv[2]).resolve()

kept = []
for entry in sys.path:
    try:
        resolved = Path(entry if entry else ".").resolve()
    except OSError:
        kept.append(entry)
        continue
    if resolved == scripts:
        continue
    kept.append(entry)
sys.path[:] = kept

# POSITIVE CONTROL for the scrub itself. `untrusted` is importable by NAME only
# when `crux/scripts/` is on the path. If this import succeeds, the environment
# under test is pytest's and not the caller's, and every load below would pass
# for a reason that has nothing to do with the code. Fail loudly instead.
try:
    import untrusted  # noqa: F401
except ImportError:
    pass
else:
    print("SCRUB-FAILED: `import untrusted` resolved by name, so "
          "crux/scripts/ is still importable and this check is vacuous",
          file=sys.stderr)
    raise SystemExit(3)

spec = importlib.util.spec_from_file_location("_bypath_" + target.stem, target)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print("OK")
'''


# ── target derivation ────────────────────────────────────────────────────
#
# The first cut read string literals ending in `.py`. A by-path load built any
# other way — `parent / f"{stem}.py"` is the shape that proved it — was dropped
# in silence, and the non-vacuity guard below could not see the hole because it
# only asked whether the derived set was non-empty. So the derivation now works
# from the `spec_from_file_location` CALL SITES, resolves each one's location
# argument, and REPORTS any it cannot resolve. Under-approximating silently is
# the defect; a named refusal is not.

_SPEC_FUNC = "spec_from_file_location"
# `Path`, `PurePath`, `PurePosixPath`, ... — a one-argument constructor whose
# argument is the path.
_PATH_CTORS = ("Path", "PurePath", "PurePosixPath", "PureWindowsPath",
               "PosixPath", "WindowsPath")


def _is_spec_call(node) -> bool:
    """`x.spec_from_file_location(...)`, or the bare-name form.

    Both forms, so `_spec_call_count` and the derivation cannot drift apart and
    leave the guard comparing two different populations.
    """
    if not isinstance(node, ast.Call):
        return False
    return (getattr(node.func, "attr", None) == _SPEC_FUNC
            or getattr(node.func, "id", None) == _SPEC_FUNC)


def _local_env(body: list) -> dict:
    """`{name: the expression assigned to it}` for one scope.

    Nested `def`/`class` bodies are skipped — they are their own scope. A name
    assigned twice from different expressions maps to `None`, which reads as
    "cannot be traced" rather than as a guess.
    """
    env: dict = {}

    def walk(stmts):
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                continue
            if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)):
                name = stmt.targets[0].id
                previous = env.get(name, _MISSING)
                if previous is _MISSING:
                    env[name] = stmt.value
                elif previous is None or ast.dump(previous) != ast.dump(
                        stmt.value):
                    env[name] = None
            for _field, value in ast.iter_fields(stmt):
                if isinstance(value, list):
                    nested = [v for v in value if isinstance(v, ast.stmt)]
                    if nested:
                        walk(nested)

    walk(body)
    return env


_MISSING = object()


def _resolve_path_text(node, envs: list, depth: int = 0):
    """The path text an expression evaluates to, or `None` if it cannot be
    traced. `None` is a refusal, never a default."""
    if depth > 8 or node is None:
        return None
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.Name):
        for env in envs:
            if node.id in env:
                return _resolve_path_text(env[node.id], envs, depth + 1)
        return None
    if isinstance(node, ast.JoinedStr):
        parts = []
        for piece in node.values:
            literal = (isinstance(piece, ast.Constant)
                       and isinstance(piece.value, str))
            if literal:
                parts.append(piece.value)
            elif isinstance(piece, ast.FormattedValue):
                inner = _resolve_path_text(piece.value, envs, depth + 1)
                if inner is None:
                    return None
                parts.append(inner)
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        # `<directory> / "name.py"` — only the right operand names the file.
        return _resolve_path_text(node.right, envs, depth + 1)
    if isinstance(node, ast.Call):
        func = node.func
        ctor = getattr(func, "id", None) or getattr(func, "attr", None)
        if ctor in _PATH_CTORS and len(node.args) == 1 and not node.keywords:
            return _resolve_path_text(node.args[0], envs, depth + 1)
    return None


def _spec_call_sites(tree):
    """`[(call node, scope chain innermost-first)]` for every by-path load."""
    sites = []

    def walk(node, envs):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            envs = [_local_env(node.body)] + envs
        if _is_spec_call(node):
            sites.append((node, envs))
        for child in ast.iter_child_nodes(node):
            walk(child, envs)

    walk(tree, [_local_env(tree.body)])
    return sites


def _location_arg(call):
    """The `location` argument of a `spec_from_file_location` call."""
    if len(call.args) >= 2:
        return call.args[1]
    for keyword in call.keywords:
        if keyword.arg == "location":
            return keyword.value
    return None


def _derive_bypath_targets(caller: Path) -> tuple[set, list]:
    """`(existing sibling `.py` names loaded by path, untraceable call sites)`.

    The second half is the fail-closed leg. A `spec_from_file_location` whose
    location cannot be resolved to a filename is NOT skipped — it is returned,
    named by file and line, and the test below fails on it.
    """
    tree = ast.parse(caller.read_text(encoding="utf-8"))
    targets: set = set()
    untraceable: list = []
    for call, envs in _spec_call_sites(tree):
        location = _location_arg(call)
        text = _resolve_path_text(location, envs)
        if text is None or not text.endswith(".py"):
            rendered = "<no location argument>" if location is None \
                else ast.unparse(location)
            untraceable.append(
                f"{caller.name}:{call.lineno} loads by path from a location "
                f"this derivation cannot resolve to a filename: {rendered}")
            continue
        name = PurePosixPath(text).name
        if (SCRIPTS / name).is_file():
            targets.add(name)
    return targets, untraceable


def _sibling_modules(caller: Path) -> set:
    """Every existing sibling `.py` the caller loads by path."""
    return _derive_bypath_targets(caller)[0]


def _untraceable_spec_calls(caller: Path) -> list:
    """Every by-path load whose target the derivation could not resolve."""
    return _derive_bypath_targets(caller)[1]


def _spec_call_count(caller: Path) -> int:
    tree = ast.parse(caller.read_text(encoding="utf-8"))
    return sum(1 for n in ast.walk(tree) if _is_spec_call(n))


def _load_by_path(target: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    # Run from a directory that is NOT `crux/scripts/`, so the implicit
    # script-directory entry cannot re-add it.
    with tempfile.TemporaryDirectory() as td:
        driver = Path(td) / "driver.py"
        driver.write_text(_DRIVER, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(driver), str(target), str(SCRIPTS)],
            capture_output=True, text=True, cwd=td, env=env, timeout=120)


# ── reproductions for the literal-only derivation ────────────────────────
#
# The derivation read string literals. A by-path load whose target is built any
# other way was dropped in silence, and the non-vacuity guard above could not
# see the hole because it only asked whether the set was non-empty.

_FSTRING_EXTRA = '''

def _extra_by_path_load():
    """A by-path load whose target is an f-string, not a literal."""
    stem = "_yaml_min"
    path = Path(__file__).resolve().parent / f"{stem}.py"
    s = _ilu.spec_from_file_location(stem, path)
    m = _ilu.module_from_spec(s)
    s.loader.exec_module(m)
    return m
'''

_UNTRACEABLE_CALLER = '''
import importlib.util as _ilu
import sys
from pathlib import Path


def _load_literal():
    s = _ilu.spec_from_file_location("_yaml_min", Path("_yaml_min.py"))
    return s


def _load_whatever_the_caller_said():
    s = _ilu.spec_from_file_location("m", sys.argv[1])
    return s
'''


def _write_doctored(td: str, name: str, source: str) -> Path:
    path = Path(td) / name
    path.write_text(source, encoding="utf-8")
    return path


class DerivationCoverageTests(unittest.TestCase):
    """The derivation must see every by-path load, or say which one it cannot.

    Doctored copies only — the real callers are never written to.
    """

    def test_an_fstring_target_is_derived(self):
        """The proven false green: append a fourth `spec_from_file_location`
        whose path is `parent / f"{stem}.py"` and the caller list stays at
        three while the spec-call count goes to four."""
        source = (SCRIPTS / "check_observations.py").read_text(
            encoding="utf-8") + _FSTRING_EXTRA
        with tempfile.TemporaryDirectory() as td:
            doctored = _write_doctored(td, "check_observations.py", source)
            self.assertEqual(_spec_call_count(doctored), 4)
            self.assertEqual(
                _sibling_modules(doctored),
                {"bionic_config.py", "observation_evidence.py",
                 "untrusted.py", "_yaml_min.py"})

    def test_a_bare_fstring_target_is_derived(self):
        """The same shape without the `Path(...) /` division around it."""
        source = ('import importlib.util as _ilu\n'
                  'stem = "untrusted"\n'
                  's = _ilu.spec_from_file_location(stem, f"{stem}.py")\n')
        with tempfile.TemporaryDirectory() as td:
            doctored = _write_doctored(td, "caller.py", source)
            self.assertEqual(_sibling_modules(doctored), {"untrusted.py"})

    def test_an_untraceable_target_is_named_not_dropped(self):
        """Fail closed. A target the derivation cannot resolve to a filename
        must be REPORTED at its call site, never silently skipped — the silent
        skip is the whole defect."""
        with tempfile.TemporaryDirectory() as td:
            doctored = _write_doctored(td, "caller.py", _UNTRACEABLE_CALLER)
            self.assertEqual(_spec_call_count(doctored), 2)
            untraceable = _untraceable_spec_calls(doctored)
        self.assertEqual(len(untraceable), 1, untraceable)
        argv_line = next(i for i, ln in enumerate(
            _UNTRACEABLE_CALLER.splitlines(), start=1) if "sys.argv[1]" in ln)
        self.assertIn(f"caller.py:{argv_line}", untraceable[0])
        self.assertIn("sys.argv[1]", untraceable[0])

    def test_the_real_callers_have_no_untraceable_targets(self):
        for name in CALLERS:
            with self.subTest(caller=name):
                self.assertEqual(_untraceable_spec_calls(SCRIPTS / name), [])


class ByPathLoadTests(unittest.TestCase):
    """Clause: a by-path-loaded module imports cleanly without its own
    directory on `sys.path`."""

    # The counts observed at HEAD, pinned as FLOORS rather than targets. They
    # were derived by running `_derive_bypath_targets` and `_spec_call_count`
    # over the two real callers: `check_observations.py` makes 3 by-path loads
    # resolving to 3 distinct siblings (`bionic_config`,
    # `observation_evidence`, `untrusted`), and `check_invariants.py` makes 2
    # resolving to 1
    # (`bionic_config`, loaded from two places). Adding a by-path load is
    # expected and must raise these numbers; a DROP means the derivation
    # stopped seeing a load, which is the silent under-approximation this
    # whole file is written against. `> 0` could not see that — it stayed
    # green at 3 of 4.
    _FLOORS = {
        "check_observations.py": {"spec_calls": 3, "targets": 3},
        "check_invariants.py": {"spec_calls": 2, "targets": 1},
    }

    def test_the_derivation_still_sees_every_bypath_load(self):
        """Guard against a vacuous pass, at the observed count rather than at
        `> 0`. Every by-path load must also be TRACEABLE: an unresolved
        location fails here instead of vanishing from the coverage set."""
        self.assertEqual(set(self._FLOORS), set(CALLERS))
        for name in CALLERS:
            caller = SCRIPTS / name
            self.assertTrue(caller.is_file(), f"{name} is missing")
            floor = self._FLOORS[name]
            with self.subTest(caller=name):
                targets, untraceable = _derive_bypath_targets(caller)
                self.assertEqual(
                    untraceable, [],
                    f"{name} has a by-path load whose target could not be "
                    f"traced; the gate must not silently skip it")
                self.assertGreaterEqual(
                    _spec_call_count(caller), floor["spec_calls"],
                    f"{name} makes fewer spec_from_file_location calls than "
                    f"the {floor['spec_calls']} observed at HEAD — either a "
                    f"load was removed or the detector stopped matching")
                self.assertGreaterEqual(
                    len(targets), floor["targets"],
                    f"{name} resolves to fewer by-path siblings than the "
                    f"{floor['targets']} observed at HEAD — the derivation "
                    f"has stopped seeing one of this file's loads")

    def test_every_bypath_module_loads_with_scripts_off_sys_path(self):
        targets = set()
        for name in CALLERS:
            targets |= _sibling_modules(SCRIPTS / name)
        self.assertTrue(targets, "derived no by-path modules at all")

        for name in sorted(targets):
            with self.subTest(module=name):
                proc = _load_by_path(SCRIPTS / name)
                self.assertEqual(
                    proc.returncode, 0,
                    f"{name} does not load by path with crux/scripts/ off "
                    f"sys.path — the environment `audit-docs` loads it in.\n"
                    f"stdout: {proc.stdout}\nstderr: {proc.stderr}")
                self.assertIn("OK", proc.stdout)

    def test_the_callers_themselves_load_with_scripts_off_sys_path(self):
        """The callers are loaded by path too — by `audit-docs` and by several
        test modules — so their own import-time by-path loads run in the same
        scrubbed environment."""
        for name in CALLERS:
            with self.subTest(caller=name):
                proc = _load_by_path(SCRIPTS / name)
                self.assertEqual(
                    proc.returncode, 0,
                    f"{name} does not load by path with crux/scripts/ off "
                    f"sys.path.\nstdout: {proc.stdout}\nstderr: {proc.stderr}")


if __name__ == "__main__":
    unittest.main()
