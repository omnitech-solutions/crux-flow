"""Unit 6 (ADR-0075 decision 7): the MANDATORY confinement NEGATIVE suite.

`ArchRuntimeConfinementTests` (in tools/tests/test_schema_invariants.py) proves
separation from `derive` but NOT the executor's own controls; this is the
separate, named acceptance-gate deliverable that proves the controls fire.

Covered: the consent/CI/TTY fail-closed floor; fresh-env allowlist (no parent
secret reaches the child); netns refusal fail-close on Linux (skipTest on
macOS); the socket / subprocess / os.system audit denials; the resource rlimits;
timeout escalation + process-group reap; the copy-set 256 MiB refusal; and
hostile / forged / oversized capture handling incl. the pre-parse byte cap.

A test whose platform primitive is absent is SKIPPED, not passed.
"""

import importlib
import importlib.util
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]  # crux/scripts
sys.path.insert(0, str(SCRIPTS))
RUNTIME_DIR = SCRIPTS / "crux" / "arch" / "runtime"
FIXTURES = str(Path(__file__).resolve().parent / "fixtures")

harness = importlib.import_module("crux.arch.runtime.harness")
gate = importlib.import_module("crux.arch.runtime.gate")
capture = importlib.import_module("crux.arch.runtime.capture")
child = importlib.import_module("crux.arch.runtime.child")


def _have(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None


# ── consent gate: fail-closed CI/TTY floor ──────────────────────────────────

class GateFloorTests(unittest.TestCase):
    def test_refused_without_flag(self):
        with self.assertRaises(gate.GateRefusal):
            gate.check_gate(env={}, isatty=True)

    def test_refused_without_tty_even_with_flag(self):
        with self.assertRaises(gate.GateRefusal) as cm:
            gate.check_gate(env={gate.FLAG: "1"}, isatty=False)
        self.assertIn("TTY", str(cm.exception))

    def test_refused_under_each_ci_marker_even_with_flag(self):
        for marker in gate.CI_MARKERS:
            with self.assertRaises(gate.GateRefusal, msg=f"{marker} did not refuse"):
                gate.check_gate(env={gate.FLAG: "1", marker: "true"}, isatty=True)

    def test_ci_marker_present_but_empty_still_refuses(self):
        with self.assertRaises(gate.GateRefusal):
            gate.check_gate(env={gate.FLAG: "1", "CI": ""}, isatty=True)

    def test_passes_with_flag_tty_no_ci(self):
        gate.check_gate(env={gate.FLAG: "1"}, isatty=True)  # no raise

    def test_cli_refuses_without_tty(self):
        # The __main__ CLI, run as a subprocess (no TTY on stdin), refuses even
        # with the flag set — factor (a) is not enough without the floor.
        env = dict(os.environ)
        env["CRUX_ARCH_ALLOW_RUNTIME"] = "1"
        proc = subprocess.run(
            [sys.executable, str(RUNTIME_DIR / "__main__.py"),
             "--app", "runtime_flask.app:app", "--repo-root", FIXTURES],
            capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("refused", proc.stderr)


# ── fresh env allowlist ─────────────────────────────────────────────────────

class FreshEnvTests(unittest.TestCase):
    def test_build_child_env_drops_planted_secret(self):
        with mock.patch.dict(os.environ, {"MY_PLANTED_SECRET": "swordfish"}):
            env = harness.build_child_env("/tmp/s", "/tmp/c", 9)
        self.assertNotIn("MY_PLANTED_SECRET", env)
        self.assertEqual(env["HOME"], "/tmp/s")

    @unittest.skipUnless(_have("flask"), "flask not importable — skip (not pass)")
    def test_planted_secret_never_reaches_child(self):
        with mock.patch.dict(os.environ, {"MY_PLANTED_SECRET": "swordfish"}):
            r = harness.run(app="runtime_probe.env_probe:create_app",
                            search_root=FIXTURES, timeout=30)
        self.assertTrue(r.ok, f"{r.reason}\n{r.child_stderr}")
        blob = json.dumps(r.capture)
        self.assertNotIn("swordfish", blob)
        self.assertIn("/secret-was-ABSENT", {rt["path"] for rt in r.capture["routes"]})


# ── audit deny-list ─────────────────────────────────────────────────────────

class AuditDenialTests(unittest.TestCase):
    def _run(self, module):
        return harness.run(app=f"{module}:app", search_root=FIXTURES, timeout=20)

    def test_socket_denied(self):
        r = self._run("runtime_hostile.socket_opener")
        self.assertFalse(r.ok)
        self.assertIn("socket", r.child_stderr)

    def test_subprocess_denied(self):
        r = self._run("runtime_hostile.subprocess_spawner")
        self.assertFalse(r.ok)
        self.assertIn("subprocess", r.child_stderr)

    def test_os_system_denied(self):
        r = self._run("runtime_hostile.os_system")
        self.assertFalse(r.ok)
        self.assertIn("os.system", r.child_stderr)

    def test_write_outside_scratch_denied(self):
        target = Path(FIXTURES).parent / "_PWNED_marker_should_not_exist"
        if target.exists():
            target.unlink()
        with mock.patch.dict(os.environ, {"CRUX_ARCH_HOSTILE_TARGET": str(target)}):
            r = harness.run(app="runtime_hostile.disk_writer:app",
                            search_root=FIXTURES, timeout=20)
        self.assertFalse(r.ok)
        self.assertIn("denied write open outside scratch", r.child_stderr)
        self.assertFalse(target.exists(), "hostile write escaped the audithook")

    def test_symlink_write_escape_denied(self):
        # A symlink created INSIDE scratch that points OUTSIDE it must not let a
        # pure-Python `open` escape the write boundary: the audithook resolves the
        # target with realpath, so the write is denied and no outside file lands.
        marker = Path("/tmp/crux-arch-symlink-escape-marker")
        if marker.exists():
            marker.unlink()
        try:
            r = harness.run(app="runtime_hostile.symlink_escaper:app",
                            search_root=FIXTURES, timeout=20)
            self.assertFalse(r.ok)
            self.assertIn("denied write open outside scratch", r.child_stderr)
            self.assertFalse(marker.exists(),
                             "symlink write escaped the audithook (realpath containment)")
        finally:
            if marker.exists():
                marker.unlink()


# ── trusted-module shadowing ────────────────────────────────────────────────

class TrustedModuleShadowingTests(unittest.TestCase):
    """The child must load its TRUSTED introspection module even when the
    target's own top-level package is named `introspect`. A bare `import
    introspect` with the target clone on sys.path[0] would run target import-time
    code UNCONFINED at the trusted-import step; a file-path load is immune."""

    SHADOW_ROOT = str(Path(__file__).resolve().parent / "fixtures" / "runtime_shadow")
    STDLIB_SHADOW_ROOT = str(Path(__file__).resolve().parent / "fixtures" / "runtime_shadow_stdlib")

    def test_target_named_introspect_does_not_shadow_trusted_module(self):
        r = harness.run(app="introspect:app", search_root=self.SHADOW_ROOT, timeout=20)
        self.assertFalse(r.ok)
        # The trusted introspector ran (it rejected the non-app object). A shadow
        # would instead surface an AttributeError for the missing introspect_app.
        self.assertNotIn("AttributeError", r.child_stderr,
                         "target package named `introspect` shadowed the trusted module")
        self.assertIn("neither an app instance nor a callable factory", r.child_stderr)

    # ── late-import (confinement-setup) stdlib shadow ────────────────────────
    #
    # M1 fix (ADR-0075 decision 7): the former `resource`-named test passed
    # VACUOUSLY. On the uv / python-build-standalone toolchain AGENTS.md
    # mandates, `resource` is a statically-linked builtin (in
    # `sys.builtin_module_names`), so `BuiltinImporter` resolves it before
    # `sys.path` and a `resource`-named package can never shadow it — the test
    # passed even against a broken unconfined child. `socket` is confirmed
    # NON-builtin and is imported LATE (inside `_neutralize_sockets_macos`), so
    # it is genuinely shadowable; the test skips honestly if it is a builtin on
    # the running build, and a positive control proves the negative is
    # load-bearing.

    SOCKET_MARKER = Path("/tmp/crux-arch-socket-shadow-marker")
    JSON_MARKER = Path("/tmp/crux-arch-json-shadow-marker")

    def test_target_named_socket_does_not_run_unconfined_at_setup(self):
        if "socket" in sys.builtin_module_names:
            self.skipTest("`socket` is a builtin on this build — the shadow can "
                          "never fire; skip (not pass)")
        if self.SOCKET_MARKER.exists():
            self.SOCKET_MARKER.unlink()
        try:
            r = harness.run(app="socket:app", search_root=self.STDLIB_SHADOW_ROOT, timeout=20)
            self.assertFalse(r.ok)
            self.assertFalse(self.SOCKET_MARKER.exists(),
                             "target `socket` package ran import-time code unconfined "
                             "during confinement setup")
        finally:
            if self.SOCKET_MARKER.exists():
                self.SOCKET_MARKER.unlink()

    def test_positive_control_socket_shadow_writes_marker_unconfined(self):
        # Load-bearing proof: the fixture's import-time out-of-scratch write DOES
        # run when the shadow package is on sys.path and imported UNCONFINED. Run
        # in a fresh subprocess so the fake `socket` never contaminates this
        # interpreter. Without this control the negative test above could be
        # vacuous (marker absent because the fixture never writes it at all).
        if "socket" in sys.builtin_module_names:
            self.skipTest("`socket` is a builtin on this build — skip (not pass)")
        if self.SOCKET_MARKER.exists():
            self.SOCKET_MARKER.unlink()
        try:
            code = (f"import sys; sys.path.insert(0, {self.STDLIB_SHADOW_ROOT!r}); "
                    "import socket")
            subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
            self.assertTrue(self.SOCKET_MARKER.exists(),
                            "positive control failed: the fixture import wrote no "
                            "marker unconfined — the negative test would be vacuous")
        finally:
            if self.SOCKET_MARKER.exists():
                self.SOCKET_MARKER.unlink()

    # ── module-scope (interpreter-startup) stdlib shadow ─────────────────────
    #
    # `child.py` imports `json` at module scope. With PYTHONPATH gone the clone
    # is off sys.path at startup, so `import json` resolves to the real stdlib
    # and the target's import-time code never runs.

    def test_target_named_json_not_shadowed_at_module_scope(self):
        if "json" in sys.builtin_module_names:
            self.skipTest("`json` is a builtin on this build — the shadow can "
                          "never fire; skip (not pass)")
        if self.JSON_MARKER.exists():
            self.JSON_MARKER.unlink()
        try:
            r = harness.run(app="json:app", search_root=self.STDLIB_SHADOW_ROOT, timeout=20)
            self.assertFalse(r.ok)
            self.assertFalse(self.JSON_MARKER.exists(),
                             "target `json` package ran import-time code (module-scope shadow)")
        finally:
            if self.JSON_MARKER.exists():
                self.JSON_MARKER.unlink()

    def test_positive_control_json_shadow_writes_marker_unconfined(self):
        if "json" in sys.builtin_module_names:
            self.skipTest("`json` is a builtin on this build — skip (not pass)")
        if self.JSON_MARKER.exists():
            self.JSON_MARKER.unlink()
        try:
            code = (f"import sys; sys.path.insert(0, {self.STDLIB_SHADOW_ROOT!r}); "
                    "import json")
            subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
            self.assertTrue(self.JSON_MARKER.exists(),
                            "positive control failed: the fixture import wrote no "
                            "marker unconfined — the negative test would be vacuous")
        finally:
            if self.JSON_MARKER.exists():
                self.JSON_MARKER.unlink()


# ── stderr must not deadlock the capture ────────────────────────────────────

class StderrDrainTests(unittest.TestCase):
    @unittest.skipUnless(_have("flask"), "flask not importable — skip (not pass)")
    def test_stderr_flood_does_not_deadlock_capture(self):
        # A child that floods stderr past the pipe buffer BEFORE writing the
        # capture must still yield the capture — the parent's stderr sink is
        # non-blocking (disk-backed), so the capture descriptor reaches EOF.
        r = harness.run(app="runtime_hostile.stderr_flood:app",
                        search_root=FIXTURES, timeout=15)
        self.assertTrue(r.ok, f"stderr flood deadlocked the capture: {r.reason}\n{r.child_stderr[:400]}")
        paths = {rt["path"] for rt in r.capture["routes"]}
        self.assertIn("/flooded", paths)


# ── resource rlimits ────────────────────────────────────────────────────────

class RlimitTests(unittest.TestCase):
    def test_rlimits_set_soft_equals_hard(self):
        # Exercise child._set_rlimits in a fresh subprocess (never this process),
        # then read the limits back. CPU is pinned at 30; soft == hard.
        script = (
            f"import sys; sys.path.insert(0, {str(RUNTIME_DIR)!r});"
            "import resource, child;"
            "child._set_rlimits();"
            "print(resource.getrlimit(resource.RLIMIT_CPU));"
            "print(resource.getrlimit(resource.RLIMIT_NOFILE));"
            "print(resource.getrlimit(resource.RLIMIT_NPROC))"
        )
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        cpu, nofile, nproc = [eval(line) for line in proc.stdout.strip().splitlines()]
        self.assertEqual(cpu, (30, 30))
        self.assertEqual(nofile[0], nofile[1])
        self.assertLessEqual(nofile[0], 256)
        self.assertEqual(nproc[0], nproc[1])
        self.assertLessEqual(nproc[0], 64)


# ── network namespace fail-close ────────────────────────────────────────────

class NetnsTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith("linux"),
                         "network namespace is a Linux primitive — skip on macOS (not pass)")
    def test_missing_unshare_fail_closes(self):
        # Simulate the 3.12+ API being unavailable: _cut_network must raise a
        # ConfinementError (no-capture), never a raw AttributeError.
        with mock.patch.object(child.os, "unshare", None, create=True):
            with self.assertRaises(child.ConfinementError):
                child._cut_network()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux netns only")
    def test_missing_clone_newnet_fail_closes(self):
        with mock.patch.object(child.os, "CLONE_NEWNET", None, create=True):
            with self.assertRaises(child.ConfinementError):
                child._cut_network()


# ── timeout escalation + process-group reap ─────────────────────────────────

class TimeoutTests(unittest.TestCase):
    def test_block_on_import_yields_no_capture_no_hang(self):
        start = time.monotonic()
        r = harness.run(app="runtime_hostile.block_forever:app",
                        search_root=FIXTURES, timeout=5)
        elapsed = time.monotonic() - start
        self.assertFalse(r.ok)
        self.assertIn("timeout", r.reason)
        # No hang: bounded by timeout + kill grace, well under this ceiling.
        self.assertLess(elapsed, 20, "harness hung past the timeout + kill budget")

    def test_sigterm_ignoring_child_escalates_to_sigkill_no_hang(self):
        # A child that installs SIG_IGN for SIGTERM and spins forces the
        # `_kill_group` SIGKILL-escalation branch: killpg(SIGTERM) is discarded,
        # the 5 s grace expires, and only killpg(SIGKILL) reaps it. Spy on
        # os.killpg to assert BOTH signals were sent (SIGKILL escalation reached),
        # and bound the wall clock to prove the parent never hangs.
        import signal as _signal
        sent = []
        real_killpg = harness.os.killpg

        def _spy(pgid, sig):
            sent.append(sig)
            return real_killpg(pgid, sig)

        with mock.patch.object(harness.os, "killpg", _spy):
            start = time.monotonic()
            r = harness.run(app="runtime_hostile.sigterm_ignorer:app",
                            search_root=FIXTURES, timeout=5)
            elapsed = time.monotonic() - start
        self.assertFalse(r.ok)
        self.assertIn("timeout", r.reason)
        self.assertIn(_signal.SIGTERM, sent, "SIGTERM was never sent to the group")
        self.assertIn(_signal.SIGKILL, sent,
                      "SIGKILL escalation branch never reached — a SIGTERM-ignoring "
                      "child would never be reaped")
        # timeout(5) + grace(5) + reap margin; a hang would blow well past this.
        self.assertLess(elapsed, 30, "harness hung: SIGKILL escalation did not reap")


# ── copy-set size refusal ───────────────────────────────────────────────────

class CopySetTests(unittest.TestCase):
    def test_refuses_over_size_cap(self):
        r = harness.run(app="runtime_flask.app:app", search_root=FIXTURES,
                        timeout=20, copy_set_size_cap=1)
        self.assertFalse(r.ok)
        self.assertIn("over the", r.reason)

    def test_missing_target_refused(self):
        r = harness.run(app="nonexistent_pkg.app:app", search_root=FIXTURES, timeout=20)
        self.assertFalse(r.ok)
        self.assertIn("not found", r.reason)


# ── forged / oversized / unknown-key capture ────────────────────────────────

class HostileCaptureTests(unittest.TestCase):
    def test_oversized_capture_hits_byte_cap(self):
        r = harness.run(app="runtime_hostile.oversized_capture:app",
                        search_root=FIXTURES, timeout=20)
        self.assertFalse(r.ok)
        self.assertIn("byte cap", r.reason)

    def test_forged_capture_yields_no_capture(self):
        r = harness.run(app="runtime_hostile.capture_forger:app",
                        search_root=FIXTURES, timeout=20)
        self.assertFalse(r.ok)

    def test_forged_payload_rejected_by_strict_schema(self):
        # The exact bytes the forger writes carry unknown keys — parse rejects them.
        forged = (b'{"routes": [{"method": "GET", "path": "/", "name": "x", '
                  b'"handler": "os.system"}], "secret": "leaked"}')
        with self.assertRaises(capture.CaptureError):
            capture.parse_and_validate(forged)

    def test_pre_parse_drain_caps_bytes_directly(self):
        # A background thread streams > the cap while _drain reads incrementally;
        # the drain must report 'overcap' rather than buffering without bound.
        import threading
        r_fd, w_fd = os.pipe()

        def _writer():
            chunk = b"A" * (256 * 1024)
            try:
                for _ in range(40):  # 10 MiB
                    os.write(w_fd, chunk)
            except OSError:
                pass  # drain closed the read end — expected once it caps
            finally:
                try:
                    os.close(w_fd)
                except OSError:
                    pass

        t = threading.Thread(target=_writer, daemon=True)
        t.start()
        deadline = time.monotonic() + 10
        status, raw = harness._drain(r_fd, deadline, 4 * 1024 * 1024)
        os.close(r_fd)
        t.join(timeout=5)
        self.assertEqual(status, "overcap")
        self.assertGreater(len(raw), 4 * 1024 * 1024)

    def test_drain_deadline_path_returns_timeout(self):
        # A pipe with no writer content and a past deadline drains to 'timeout',
        # never hanging the trusted parent.
        r_fd, w_fd = os.pipe()
        try:
            status, raw = harness._drain(r_fd, time.monotonic() - 1, 4 * 1024 * 1024)
        finally:
            os.close(r_fd)
            os.close(w_fd)
        self.assertEqual(status, "timeout")


if __name__ == "__main__":
    unittest.main()
