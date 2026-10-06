"""Tests for the PB-0026 YAML capability guards.

Pins the contract that correctness-critical consumers (validate-promptbook,
visualize-run-progress) NEVER produce verdicts/hashes via
the minimal fallback parser:

  1. In-process ``_yaml_min`` semantics with PyYAML blocked: ``load_yaml`` on a
     document exceeding the minimal subset raises ``YamlCapabilityError`` (not a
     bare ValueError) with the uv/pyyaml remediation; simple docs still parse
     (best-effort consumers keep working); ``ensure_real_yaml`` raises under
     CRUX_NO_UV_REEXEC (operator opt-out) and CRUX_UV_REEXEC (loop guard), and
     is a no-op when PyYAML imports.
  2. Subprocess capability lane (the CI pin): with PyYAML import-blocked and the
     uv repair declined, validate-promptbook exits 2 with the remediation on
     stderr and NO ``{"errors": [...]}`` validation verdict on stdout — an
     environment problem must never masquerade as a document verdict. Contrast:
     the same complex-but-valid file passes (exit 0) in a normal environment.
  3. Subprocess re-exec lane: with PyYAML blocked and a stub ``uv`` first on
     PATH, the script re-execs ``uv run --no-project --with pyyaml>=6.0
     python3 <abs script> <args...>`` with CRUX_UV_REEXEC=1 set pre-exec, and
     the child's exit code passes through. CRUX_NO_UV_REEXEC=1 suppresses the
     re-exec entirely (exit 2, stub never runs).
  4. The same entry guard on visualize-run-progress: exit 2 + stderr, no errors-JSON.
  5. ``ensure_real_yaml``'s failure branches in-process: cause-specific,
     distinguishable messages (loop guard / opt-out / no-uv), plus the
     execv-OSError path (junk uv binary -> "failed to exec").
  6. Defense-handler reachability: each retained consumer's main() except clause
     actually catches the exception class its raise sites use (in-process,
     monkeypatched).

The yaml-blocking tricks are kept leak-free: the in-process block is a
try/finally restore of ``sys.modules['yaml']`` (the same technique as
test_validate_promptbook.FallbackParserParityTests), the fresh ``_yaml_min``
import is never registered in ``sys.modules``, and everything else runs in
subprocesses with a PYTHONPATH stub.

Stdlib only.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr
from pathlib import Path

# tests/ -> scripts/ -> crux/ -> repo root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "crux" / "scripts"
VALIDATE_SCRIPT = SCRIPTS_DIR / "validate-promptbook.py"
VISUALIZE_SCRIPT = SCRIPTS_DIR / "visualize-run-progress.py"
YAML_MIN_PATH = SCRIPTS_DIR / "_yaml_min.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

SUBPROCESS_TIMEOUT = 60

# A construct that exceeds the minimal fallback subset but is valid YAML: a
# multiline flow sequence (run snapshots in the wild carry exactly this shape
# in their `artifacts` fields when an emitter wraps long lists).
COMPLEX_DOC = "k:\n  [\n    1,\n    2,\n  ]\n"

try:
    import yaml as _pyyaml_probe  # noqa: F401

    HAVE_PYYAML = True
except ImportError:
    HAVE_PYYAML = False


@contextmanager
def _yaml_blocked():
    """Make ``import yaml`` raise ImportError, restoring the original module
    object afterwards (same technique as FallbackParserParityTests)."""
    sentinel = object()
    saved = sys.modules.get("yaml", sentinel)
    sys.modules["yaml"] = None  # makes `import yaml` raise ImportError
    try:
        yield
    finally:
        if saved is sentinel:
            sys.modules.pop("yaml", None)
        else:
            sys.modules["yaml"] = saved


@contextmanager
def _env_vars(**overrides):
    """Temporarily set/unset os.environ keys (None means 'ensure absent')."""
    sentinel = object()
    saved = {k: os.environ.get(k, sentinel) for k in overrides}
    try:
        for k, v in overrides.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, old in saved.items():
            if old is sentinel:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


def _import_yaml_min_fresh(module_name: str) -> object:
    """Import a FRESH copy of _yaml_min under a private name. Deliberately not
    registered in ``sys.modules`` so nothing leaks into the rest of the suite.
    Call inside ``_yaml_blocked()`` to get a copy with ``HAVE_PYYAML = False``.
    """
    spec = importlib.util.spec_from_file_location(module_name, YAML_MIN_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_script_fresh(module_name: str, filename: str) -> object:
    """Import a FRESH copy of a (possibly hyphen-named) sibling script under a
    private name — the same spec_from_file_location convention the sibling test
    modules use (test_migrate_promptbooks._load). Never registered in
    ``sys.modules``."""
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS_DIR / filename)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ───────────────────────── 1. in-process loader semantics ──────────────────


class InProcessLoaderSemanticsTests(unittest.TestCase):
    """_yaml_min semantics with PyYAML blocked, exercised in-process."""

    def setUp(self):
        with _yaml_blocked():
            self.ym = _import_yaml_min_fresh("_yaml_min_blocked_for_test")
        self.assertFalse(
            self.ym.HAVE_PYYAML,
            "fresh _yaml_min import under the yaml block must see PyYAML absent",
        )

    def test_complex_doc_raises_capability_error_not_value_error(self):
        # (a) A document exceeding the minimal subset (multiline flow sequence)
        # must surface as a CAPABILITY problem, not a bare parse ValueError —
        # only a real parser can tell "invalid" from "exceeds the subset".
        with _yaml_blocked():
            with self.assertRaises(self.ym.YamlCapabilityError) as cm:
                self.ym.load_yaml(COMPLEX_DOC)
        self.assertIsInstance(cm.exception, self.ym.YamlCapabilityError)
        msg = str(cm.exception)
        self.assertIn("uv", msg, f"remediation must mention uv: {msg!r}")
        self.assertIn("pyyaml", msg.lower(), f"remediation must mention pyyaml: {msg!r}")

    def test_complex_doc_parses_fine_with_pyyaml(self):
        # Contrast guard: the same doc is VALID YAML — the failure above is the
        # fallback's limitation, not the document's.
        if not HAVE_PYYAML:
            self.skipTest("PyYAML not importable in this interpreter")
        unblocked = _import_yaml_min_fresh("_yaml_min_unblocked_for_test")
        self.assertEqual(unblocked.load_yaml(COMPLEX_DOC), {"k": [1, 2]})

    def test_simple_doc_still_parses_via_fallback(self):
        # (b) Best-effort consumers keep working: a flat document within the
        # minimal subset parses without PyYAML.
        with _yaml_blocked():
            doc = self.ym.load_yaml("id: PB-9001\ncount: 3\nflag: yes\nempty: null\n")
        self.assertEqual(doc, {"id": "PB-9001", "count": 3, "flag": True, "empty": None})

    def test_ensure_real_yaml_raises_under_no_uv_reexec(self):
        # (c) Operator opt-out: CRUX_NO_UV_REEXEC=1 declines the uv repair.
        with _yaml_blocked(), _env_vars(CRUX_NO_UV_REEXEC="1", CRUX_UV_REEXEC=None):
            with self.assertRaises(self.ym.YamlCapabilityError) as cm:
                self.ym.ensure_real_yaml("dummy-script.py")
        self.assertIn("pyyaml", str(cm.exception).lower())

    def test_ensure_real_yaml_raises_under_loop_guard(self):
        # (c) Loop guard: CRUX_UV_REEXEC=1 means a re-exec already happened and
        # PyYAML is STILL missing — raise rather than exec forever.
        with _yaml_blocked(), _env_vars(CRUX_UV_REEXEC="1", CRUX_NO_UV_REEXEC=None):
            with self.assertRaises(self.ym.YamlCapabilityError):
                self.ym.ensure_real_yaml("dummy-script.py")

    def test_ensure_real_yaml_is_noop_with_pyyaml(self):
        # (c) With PyYAML available the guard returns immediately — no exec, no
        # env mutation, regardless of the CRUX_* flags.
        if not HAVE_PYYAML:
            self.skipTest("PyYAML not importable in this interpreter")
        unblocked = _import_yaml_min_fresh("_yaml_min_unblocked_for_test")
        self.assertTrue(unblocked.HAVE_PYYAML)
        before = os.environ.get("CRUX_UV_REEXEC")
        self.assertIsNone(unblocked.ensure_real_yaml("dummy-script.py"))
        self.assertEqual(os.environ.get("CRUX_UV_REEXEC"), before)


# ───────────────────────── subprocess plumbing ─────────────────────────────


class _SubprocessCapabilityBase(unittest.TestCase):
    """Shared tempdir plumbing: a PYTHONPATH stub that blocks ``import yaml``
    and a synthesized complex-but-valid run snapshot."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="crux-yaml-cap-")
        cls.addClassCleanup(cls._tmp.cleanup)
        tmp = Path(cls._tmp.name)

        # Stub package dir whose yaml.py raises ImportError on import — put
        # first on PYTHONPATH it blocks PyYAML in any child interpreter.
        cls.stub_dir = tmp / "yaml_stub"
        cls.stub_dir.mkdir()
        (cls.stub_dir / "yaml.py").write_text(
            'raise ImportError("pyyaml blocked by test_yaml_capability stub")\n',
            encoding="utf-8",
        )

        # The repo's run-valid.yaml stays WITHIN the minimal subset (the parity
        # tests prove the fallback parses it identically), so it can't pin the
        # capability lane. Synthesize a complex-but-valid variant: rewrap one
        # artifacts entry as a MULTILINE flow sequence — identical parsed value
        # under PyYAML (still schema-valid), but beyond the fallback subset.
        base = (FIXTURES / "run-valid.yaml").read_text(encoding="utf-8")
        old = "    artifacts: [docs/adrs/ADR-9001-sample.md]\n"
        assert old in base, "run-valid.yaml fixture changed; update this test"
        complex_text = base.replace(
            old,
            "    artifacts: [\n"
            "      docs/adrs/ADR-9001-sample.md,\n"
            "    ]\n",
        )
        cls.complex_run = tmp / "run-complex.yaml"
        cls.complex_run.write_text(complex_text, encoding="utf-8")

    @classmethod
    def _base_env(cls) -> dict:
        """os.environ minus the CRUX_* re-exec flags (a parent uv re-exec must
        not leak into the child under test)."""
        env = os.environ.copy()
        env.pop("CRUX_UV_REEXEC", None)
        env.pop("CRUX_NO_UV_REEXEC", None)
        return env

    @classmethod
    def _blocked_env(cls, **extra: str) -> dict:
        env = cls._base_env()
        prior = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            str(cls.stub_dir) + (os.pathsep + prior if prior else "")
        )
        env.update(extra)
        return env

    def _run(self, argv: list[str], env: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv,
            env=env,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
        )

    def assert_capability_failure(self, proc: subprocess.CompletedProcess, prog: str):
        """The crash-lane contract: exit 2, remediation on stderr, and stdout is
        NOT an ``{"errors": [...]}`` validation verdict."""
        self.assertEqual(
            proc.returncode, 2,
            f"{prog}: expected exit 2 (capability error), got {proc.returncode}; "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
        )
        stderr_low = proc.stderr.lower()
        self.assertIn("pyyaml", stderr_low, f"{prog}: stderr must mention pyyaml: {proc.stderr!r}")
        self.assertIn("uv", stderr_low, f"{prog}: stderr must mention uv: {proc.stderr!r}")
        # A capability error must never even LOOK like a findings document.
        self.assertNotIn(
            '"errors"', proc.stdout,
            f"{prog}: capability-lane stdout must carry no errors-JSON: {proc.stdout!r}",
        )
        stdout = proc.stdout.strip()
        if stdout:
            try:
                parsed = json.loads(stdout)
            except (ValueError, json.JSONDecodeError):
                return  # non-JSON stdout is fine — it's just not a verdict
            self.assertFalse(
                isinstance(parsed, dict) and "errors" in parsed,
                f"{prog}: a capability error must NOT emit a validation "
                f"verdict on stdout, got: {stdout!r}",
            )


# ──────────────────── 2. subprocess: capability lane (CI pin) ──────────────


class ValidatorCapabilityLaneTests(_SubprocessCapabilityBase):
    """validate-promptbook with PyYAML blocked + uv repair declined."""

    def test_complex_run_without_pyyaml_hits_crash_lane(self):
        proc = self._run(
            [sys.executable, str(VALIDATE_SCRIPT), "--kind", "run", str(self.complex_run)],
            env=self._blocked_env(CRUX_NO_UV_REEXEC="1"),
        )
        self.assert_capability_failure(proc, "validate-promptbook")

    def test_same_complex_run_passes_in_normal_env(self):
        # Contrast: the document is VALID — in a healthy environment it gets a
        # clean exit 0, proving the lane above is environmental, not a verdict.
        if not HAVE_PYYAML:
            # Without PyYAML in the parent, the child would fire the real uv
            # re-exec (a hidden network dependency on first use) — skip rather
            # than depend on it.
            self.skipTest("parent interpreter lacks PyYAML; the 'normal env' contrast would uv-re-exec")
        proc = self._run(
            [sys.executable, str(VALIDATE_SCRIPT), "--kind", "run", str(self.complex_run)],
            env=self._base_env(),
        )
        self.assertEqual(
            proc.returncode, 0,
            f"expected clean validation, got {proc.returncode}; "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
        )


# ──────────────────────── 3. subprocess: re-exec lane ──────────────────────


class ValidatorReexecLaneTests(_SubprocessCapabilityBase):
    """validate-promptbook with PyYAML blocked and a stub ``uv`` on PATH."""

    def setUp(self):
        self._uv_tmp = tempfile.TemporaryDirectory(prefix="crux-uv-stub-")
        self.addCleanup(self._uv_tmp.cleanup)
        stub_bin = Path(self._uv_tmp.name) / "bin"
        stub_bin.mkdir()
        self.argv_file = Path(self._uv_tmp.name) / "uv-argv.txt"
        self.envvar_file = Path(self._uv_tmp.name) / "uv-env.txt"
        uv_path = stub_bin / "uv"
        uv_path.write_text(
            "#!/bin/sh\n"
            f': > "{self.argv_file}"\n'
            "for a in \"$@\"; do\n"
            f'  printf \'%s\\n\' "$a" >> "{self.argv_file}"\n'
            "done\n"
            f'printf \'%s\' "${{CRUX_UV_REEXEC-unset}}" > "{self.envvar_file}"\n'
            "exit 7\n",
            encoding="utf-8",
        )
        uv_path.chmod(0o755)
        self.stub_bin = stub_bin

    def _reexec_env(self, **extra: str) -> dict:
        env = self._blocked_env(**extra)
        env["PATH"] = str(self.stub_bin) + os.pathsep + env.get("PATH", "")
        return env

    def test_reexec_invokes_uv_with_exact_argv_and_loop_guard(self):
        fixture = FIXTURES / "run-valid.yaml"
        proc = self._run(
            [sys.executable, str(VALIDATE_SCRIPT), "--kind", "run", str(fixture)],
            env=self._reexec_env(),
        )
        # Exit code passes through from the exec'd stub.
        self.assertEqual(
            proc.returncode, 7,
            f"expected the stub uv's exit 7 to pass through, got {proc.returncode}; "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
        )
        # The one-line stderr notice announces the repair (explicit, not silent).
        self.assertIn("re-executing", proc.stderr)
        # Recorded argv: run --no-project --with pyyaml>=6.0 python3 <abs script> <args...>
        self.assertTrue(self.argv_file.is_file(), "stub uv never ran")
        recorded = self.argv_file.read_text(encoding="utf-8").splitlines()
        # INTENTIONAL CONTRACT PIN: exact-argv equality. ensure_real_yaml's
        # re-exec command line IS the contract (incl. the "pyyaml>=6.0"
        # constraint mirroring pyproject's floor); a deliberate constraint bump
        # there is a one-line edit here, not test friction.
        expected = [
            "run", "--no-project", "--with", "pyyaml>=6.0", "python3",
            str(VALIDATE_SCRIPT.resolve()),
            "--kind", "run", str(fixture),
        ]
        self.assertEqual(recorded, expected)
        # The loop guard was set BEFORE the exec, so a second pass can't loop.
        self.assertEqual(self.envvar_file.read_text(encoding="utf-8"), "1")

    def test_no_uv_reexec_optout_suppresses_the_stub(self):
        proc = self._run(
            [sys.executable, str(VALIDATE_SCRIPT), "--kind", "run",
             str(FIXTURES / "run-valid.yaml")],
            env=self._reexec_env(CRUX_NO_UV_REEXEC="1"),
        )
        self.assert_capability_failure(proc, "validate-promptbook")
        self.assertFalse(
            self.argv_file.exists(),
            "CRUX_NO_UV_REEXEC=1 must prevent the uv re-exec entirely",
        )


# ──────────────────── 4. subprocess: the sibling entry guards ──────────────


class SiblingCapabilityLaneTests(_SubprocessCapabilityBase):
    """visualize-run-progress carries the same entry guard."""

    def test_visualize_run_progress_hits_crash_lane(self):
        proc = self._run(
            [sys.executable, str(VISUALIZE_SCRIPT), str(self.complex_run)],
            env=self._blocked_env(CRUX_NO_UV_REEXEC="1"),
        )
        self.assert_capability_failure(proc, "visualize-run-progress")


# ──────────────── 5. in-process: ensure_real_yaml failure branches ──────────


class EnsureRealYamlBranchTests(unittest.TestCase):
    """The non-re-exec branches of ensure_real_yaml, in-process on a fresh
    _yaml_min copy that saw PyYAML absent at import time."""

    def setUp(self):
        with _yaml_blocked():
            self.ym = _import_yaml_min_fresh("_yaml_min_branch_for_test")
        self.assertFalse(
            self.ym.HAVE_PYYAML,
            "fresh _yaml_min import under the yaml block must see PyYAML absent",
        )

    def test_execv_oserror_surfaces_as_capability_error(self):
        # A "uv" that shutil.which accepts (exec bit set, first on PATH) but
        # the kernel rejects: binary junk, no shebang, no valid magic. Raw
        # os.execv has NO sh fallback (that's execvp's libc behavior), so on
        # darwin/linux this raises OSError ENOEXEC — which ensure_real_yaml
        # must wrap as a cause-specific YamlCapabilityError, not a traceback.
        tmp = tempfile.TemporaryDirectory(prefix="crux-junk-uv-")
        self.addCleanup(tmp.cleanup)
        junk = Path(tmp.name) / "uv"
        junk.write_bytes(b"\x00\x01junk")
        junk.chmod(0o755)
        stderr = io.StringIO()
        with _env_vars(PATH=tmp.name, CRUX_UV_REEXEC=None, CRUX_NO_UV_REEXEC=None):
            with redirect_stderr(stderr):
                with self.assertRaises(self.ym.YamlCapabilityError) as cm:
                    self.ym.ensure_real_yaml("dummy-script.py")
        msg = str(cm.exception)
        self.assertIn("failed to exec", msg, f"execv-OSError branch message: {msg!r}")
        # The pre-exec stderr notice announces the repair AND names the opt-out.
        self.assertIn("CRUX_NO_UV_REEXEC", stderr.getvalue())

    def test_no_uv_branch_raises_with_cause_specific_message(self):
        empty = tempfile.TemporaryDirectory(prefix="crux-empty-path-")
        self.addCleanup(empty.cleanup)
        with _env_vars(PATH=empty.name, CRUX_UV_REEXEC=None, CRUX_NO_UV_REEXEC=None):
            with self.assertRaises(self.ym.YamlCapabilityError) as cm:
                self.ym.ensure_real_yaml("dummy-script.py")
        self.assertIn("not found on PATH", str(cm.exception))

    def test_three_failure_branches_have_distinguishable_messages(self):
        # Cause-specific messages: an operator must be able to tell WHICH wall
        # was hit from the message alone.
        empty = tempfile.TemporaryDirectory(prefix="crux-empty-path-")
        self.addCleanup(empty.cleanup)
        with _env_vars(CRUX_UV_REEXEC="1", CRUX_NO_UV_REEXEC=None):
            with self.assertRaises(self.ym.YamlCapabilityError) as loop_cm:
                self.ym.ensure_real_yaml("dummy-script.py")
        with _env_vars(CRUX_UV_REEXEC=None, CRUX_NO_UV_REEXEC="1"):
            with self.assertRaises(self.ym.YamlCapabilityError) as optout_cm:
                self.ym.ensure_real_yaml("dummy-script.py")
        with _env_vars(PATH=empty.name, CRUX_UV_REEXEC=None, CRUX_NO_UV_REEXEC=None):
            with self.assertRaises(self.ym.YamlCapabilityError) as nouv_cm:
                self.ym.ensure_real_yaml("dummy-script.py")
        loop_msg = str(loop_cm.exception)
        optout_msg = str(optout_cm.exception)
        nouv_msg = str(nouv_cm.exception)
        self.assertIn("already re-executed", loop_msg)
        self.assertIn("CRUX_NO_UV_REEXEC", optout_msg)
        self.assertIn("not found on PATH", nouv_msg)
        self.assertEqual(
            len({loop_msg, optout_msg, nouv_msg}), 3,
            "the three failure branches must produce three distinct messages",
        )


# ──────────────── 6. in-process: defense-handler reachability ───────────────


class DefenseHandlerReachabilityTests(unittest.TestCase):
    """main()'s defense-in-depth except clauses actually CATCH the exception
    class(es) their raise sites use. Cross-instance hazard under pin: these
    scripts sibling-load modules by path, so two textually identical
    YamlCapabilityError classes can coexist — an except clause naming the
    wrong instance silently fails to catch."""

    def setUp(self):
        if not HAVE_PYYAML:
            # The entry guard in main() would otherwise attempt a real uv
            # re-exec inside THIS test process (os.execv replaces it).
            self.skipTest("requires PyYAML in the test interpreter")
        self._tmp = tempfile.TemporaryDirectory(prefix="crux-defense-")
        self.addCleanup(self._tmp.cleanup)
        self.target = Path(self._tmp.name) / "somefile.md"
        self.target.write_text("---\nid: PB-9001\n---\n", encoding="utf-8")

    def test_validate_defense_handler_catches_capability_error(self):
        mod = _import_script_fresh("_validate_defense_for_test", "validate-promptbook.py")

        def boom(path, kind):
            raise mod.YamlCapabilityError("synthetic capability failure")

        original = mod.validate_file
        mod.validate_file = boom
        try:
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                rc = mod.main([str(self.target)])
        finally:
            mod.validate_file = original
        self.assertEqual(
            rc, 2,
            f"a capability error escaping validate_file must hit main()'s "
            f"crash-lane handler (exit 2), got {rc}",
        )
        self.assertIn("validate-promptbook: synthetic capability failure", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
