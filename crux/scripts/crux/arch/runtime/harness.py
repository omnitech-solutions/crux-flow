"""Trusted parent orchestrator (ADR-0075 decision 2 + 3).

Computes the minimal copy-set, builds the fresh strict-allowlist child
environment, spawns the confined `child.py` with a dedicated capture descriptor,
enforces the wall-clock timeout and the process-group kill escalation, drains the
capture under the pre-parse byte cap and deadline, and hands the drained bytes to
`capture.parse_and_validate` — the sole trust boundary.

This module runs in the trusted parent context (the `crux` package heavy-import
chain is acceptable here). It NEVER imports the target and NEVER runs target
code; only the child does, under confinement.
"""

from __future__ import annotations

import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import capture

# Defaults pinned by ADR-0075 decision 2/3.
DEFAULT_TIMEOUT = 60
MIN_TIMEOUT = 5
MAX_TIMEOUT = 300
COPY_SET_SIZE_CAP = 256 * 1024 * 1024   # 256 MiB
PRE_PARSE_BYTE_CAP = 4 * 1024 * 1024    # 4 MiB
KILL_GRACE_SECONDS = 5
STDERR_READ_CAP = 64 * 1024             # bytes of child stderr surfaced back

_CHILD_PY = str(Path(__file__).resolve().parent / "child.py")


class CopySetError(Exception):
    """The minimal copy-set could not be built (target missing, or over the
    256 MiB cap). A no-capture refusal."""


@dataclass
class RuntimeResult:
    ok: bool
    capture: dict | None
    reason: str
    child_stderr: str = ""


# ── minimal copy-set ────────────────────────────────────────────────────────

def _tree_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        # do not follow symlinks out of the tree when measuring
        for fn in filenames:
            fp = Path(dirpath) / fn
            try:
                if fp.is_symlink():
                    continue
                total += fp.stat().st_size
            except OSError:
                continue
    return total


def build_copy_set(top_pkg: str, search_root: str | Path, scratch: str | Path,
                   size_cap: int = COPY_SET_SIZE_CAP) -> Path:
    """Copy the target's own top-level package tree (its first-party sources)
    into `<scratch>/src`, refusing above `size_cap`. Returns the clone root to
    root the child's `sys.path` at. Never a whole-repo copy."""
    search_root = Path(search_root)
    pkg_dir = search_root / top_pkg
    pkg_file = search_root / f"{top_pkg}.py"
    if pkg_dir.is_dir():
        source, rel = pkg_dir, top_pkg
    elif pkg_file.is_file():
        source, rel = pkg_file, f"{top_pkg}.py"
    else:
        raise CopySetError(f"target package {top_pkg!r} not found under {search_root}")

    size = _tree_size(source)
    if size > size_cap:
        raise CopySetError(
            f"copy-set for {top_pkg!r} is {size} bytes, over the {size_cap}-byte cap — refusing"
        )

    clone_root = Path(scratch) / "src"
    clone_root.mkdir(parents=True, exist_ok=True)
    dest = clone_root / rel
    if source.is_dir():
        # symlinks=True copies links verbatim rather than following them: the
        # realpath write-containment check in the child audithook is the actual
        # boundary (a link pointing outside scratch is denied on write), and
        # reads are a stated same-UID residual not denied here — so preserving
        # links is deliberate and adds no capability (ADR-0075 decision 2).
        shutil.copytree(source, dest, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    else:
        shutil.copy2(source, dest)
    return clone_root


# ── fresh strict-allowlist child env ────────────────────────────────────────

def build_child_env(scratch: str, clone_root: str, capture_fd: int,
                     django_settings: str | None = None) -> dict:
    """Build the child environment FRESH — never `os.environ.copy()`. Only the
    ADR-0075 decision 2 allowlist survives — `PATH`, `LANG`, `LC_*`, `HOME`
    (redirected to scratch), and `DJANGO_SETTINGS_MODULE` when supplied — so a
    secret in the ambient environment never reaches the child (threat class S2).

    NO `PYTHONPATH`: the temp-clone is deliberately kept OFF the child's
    interpreter-startup `sys.path`. If `PYTHONPATH` rooted the clone at startup,
    a target whose first-party top-level package is named after a stdlib module
    the child imports at module scope (`argparse`, `json`) or during confinement
    setup (`socket`, `resource`, `importlib`) would run its `__init__`
    UNCONFINED before any confinement installs. The clone reaches the child's
    `sys.path` only through the post-confinement `sys.path.insert(0, pkg_root)`
    in `child.py`, driven by the `--pkg-root` argv the harness passes below.

    `clone_root` and `capture_fd` stay in the signature but are NOT written into
    the environment: the child receives them as the `--pkg-root` and
    `--capture-fd` argv, never as env vars (part of the same minimal-env fix)."""
    env: dict[str, str] = {}
    if "PATH" in os.environ:
        env["PATH"] = os.environ["PATH"]
    if "LANG" in os.environ:
        env["LANG"] = os.environ["LANG"]
    for k, v in os.environ.items():
        if k.startswith("LC_"):
            env[k] = v
    env["HOME"] = scratch                       # redirected away from the real home
    if django_settings:
        env["DJANGO_SETTINGS_MODULE"] = django_settings
    return env


def child_capture_fd_env() -> str:
    """The env-var NAME the child reads the capture descriptor from as a fallback
    (`child.CAPTURE_FD_ENV`). Kept so harness and child stay in lock-step on that
    name. The harness deliberately does NOT set it in the child environment — the
    descriptor number rides on the `--capture-fd` argv instead, and the env stays
    trimmed to the ADR-0075 decision 2 allowlist. Imported lazily to keep the
    confined child's own module list independent."""
    from .child import CAPTURE_FD_ENV
    return CAPTURE_FD_ENV


# ── process-group kill escalation ───────────────────────────────────────────

def _kill_group(proc: subprocess.Popen) -> None:
    """SIGTERM → 5 s grace → SIGKILL → reap against a monotonic deadline. Bounds
    the PARENT; a `setsid` escapee MAY outlive the run (stated residual). Never
    hangs the parent."""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=KILL_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        # The direct child is reaped by wait; a setsid escapee is the stated
        # residual. The parent does not hang on it.
        pass


# ── capture drain ───────────────────────────────────────────────────────────

def _drain(r_fd: int, deadline: float, byte_cap: int) -> tuple[str, bytes]:
    """Drain the capture descriptor incrementally. Returns (status, bytes) where
    status is 'eof' | 'timeout' | 'overcap'. On timeout/overcap the caller kills
    the group. This stops a descendant that streams excess or retains the write
    descriptor from exhausting memory or hanging the parent."""
    buf = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "timeout", bytes(buf)
        rlist, _, _ = select.select([r_fd], [], [], min(remaining, 0.5))
        if r_fd not in rlist:
            continue
        try:
            chunk = os.read(r_fd, 65536)
        except OSError:
            return "eof", bytes(buf)
        if not chunk:
            return "eof", bytes(buf)
        buf.extend(chunk)
        if len(buf) > byte_cap:
            return "overcap", bytes(buf)


# ── public entry ────────────────────────────────────────────────────────────

def run(*, app: str | None = None, settings: str | None = None,
        search_root: str | Path, timeout: int = DEFAULT_TIMEOUT,
        copy_set_size_cap: int = COPY_SET_SIZE_CAP,
        pre_parse_byte_cap: int = PRE_PARSE_BYTE_CAP,
        interpreter: str | None = None) -> RuntimeResult:
    """Run the confined introspection and return an untrusted advisory capture or
    a no-capture reason. Never raises for a target failure; every failure mode is
    a no-capture `RuntimeResult`, never a hang."""
    if (app is None) == (settings is None):
        return RuntimeResult(False, None, "exactly one of --app / --settings is required")
    timeout = max(MIN_TIMEOUT, min(MAX_TIMEOUT, int(timeout)))

    if app is not None:
        top_pkg = app.split(":", 1)[0].split(".")[0]
        django_settings = None
    else:
        top_pkg = settings.split(".")[0]
        django_settings = settings

    scratch = tempfile.mkdtemp(prefix="crux-arch-runtime-")
    r_fd = w_fd = None
    proc = None
    # Disk-backed stderr sink, NOT a pipe. A pipe would (1) deadlock the child if
    # it writes past the ~64 KiB pipe buffer before closing the capture
    # descriptor, discarding an otherwise-valid capture, and (2) block the parent
    # in an unbounded read if a native/setsid escapee retains the write end. A
    # regular file never blocks a writer and always reaches EOF on read, so
    # neither failure mode survives. The read-back is capped in `_read_stderr`.
    stderr_file = tempfile.TemporaryFile()
    try:
        try:
            clone_root = build_copy_set(top_pkg, search_root, scratch, copy_set_size_cap)
        except CopySetError as e:
            return RuntimeResult(False, None, str(e))

        r_fd, w_fd = os.pipe()
        os.set_inheritable(w_fd, True)
        env = build_child_env(scratch, str(clone_root), w_fd, django_settings)

        # The child runs under the target's own interpreter (the one that can
        # import their app deps), defaulting to this process's interpreter. A
        # downstream skill passes the project venv's python via `interpreter`.
        child_python = interpreter or sys.executable
        cmd = [child_python, _CHILD_PY, "--scratch", scratch,
               "--pkg-root", str(clone_root), "--capture-fd", str(w_fd)]
        if app is not None:
            cmd += ["--app", app]
        else:
            cmd += ["--settings", settings]

        proc = subprocess.Popen(
            cmd, cwd=str(clone_root), env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr_file,
            start_new_session=True, close_fds=True, pass_fds=(w_fd,),
        )
        os.close(w_fd)
        w_fd = None

        deadline = time.monotonic() + timeout
        status, raw = _drain(r_fd, deadline, pre_parse_byte_cap)

        if status in ("timeout", "overcap"):
            _kill_group(proc)
            reason = ("wall-clock timeout — process group killed"
                      if status == "timeout"
                      else "capture exceeded the pre-parse byte cap — process group killed")
            return RuntimeResult(False, None, reason, _read_stderr(stderr_file))

        # EOF: the child closed the descriptor. Reap within the remaining budget.
        remaining = max(0.0, deadline - time.monotonic())
        try:
            proc.wait(timeout=remaining if remaining > 0 else 0.001)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            return RuntimeResult(False, None, "child did not exit before deadline — killed",
                                 _read_stderr(stderr_file))

        stderr = _read_stderr(stderr_file)
        if proc.returncode != 0:
            return RuntimeResult(False, None,
                                 f"child exited {proc.returncode} with no capture", stderr)
        if not raw:
            return RuntimeResult(False, None, "child produced an empty capture", stderr)

        try:
            parsed = capture.parse_and_validate(raw)
        except capture.CaptureError as e:
            return RuntimeResult(False, None, f"capture rejected by strict schema: {e}", stderr)
        return RuntimeResult(True, parsed, "ok", stderr)
    finally:
        if w_fd is not None:
            try:
                os.close(w_fd)
            except OSError:
                pass
        if r_fd is not None:
            try:
                os.close(r_fd)
            except OSError:
                pass
        if proc is not None and proc.poll() is None:
            _kill_group(proc)
        try:
            stderr_file.close()
        except OSError:
            pass
        shutil.rmtree(scratch, ignore_errors=True)


def _read_stderr(stderr_file) -> str:
    """Read the child's stderr back from the disk-backed sink, capped. Reading a
    regular file returns EOF at end-of-data even if an escapee still holds the
    write descriptor, so this never blocks the parent."""
    try:
        stderr_file.seek(0)
        data = stderr_file.read(STDERR_READ_CAP)
        if isinstance(data, bytes):
            data = data.decode("utf-8", "replace")
        return data or ""
    except (OSError, ValueError):
        return ""
