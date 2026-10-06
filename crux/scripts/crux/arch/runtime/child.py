"""In-child confinement shim (ADR-0075 decision 2). Run by ABSOLUTE FILE PATH,
never as a package module, so loading it pulls in only stdlib + `introspect` —
never the `crux` package init and its LLM/httpx chain. The confined child stays
lean; the trusted parent is the sole trust boundary.

The temp-clone is NOT on `sys.path` at interpreter startup: the parent passes
NO `PYTHONPATH`, and the clone joins `sys.path` only at step 4 below, AFTER every
confinement control installs. So the child's own module-scope imports (`argparse`,
`json`) and every confinement-setup stdlib import (`socket`, `resource`,
`importlib`) resolve to the REAL stdlib before any target code is reachable — a
target whose top-level package is named after one of them cannot run its
`__init__` unconfined.

Order of operations:
  1. Load `introspect` (stdlib-only, trusted) by ABSOLUTE FILE PATH, immune to
     `sys.path` composition, with the clone still off `sys.path`.
  2. Cut the network — Linux `os.unshare(CLONE_NEWUSER | CLONE_NEWNET)` with a
     3.12+ capability check that FAIL-CLOSES to no-capture (never a raw
     AttributeError); macOS best-effort Python-level socket neutralization.
  3. Set `resource` rlimits, soft == hard so they cannot be raised, then install
     the `sys.addaudithook` deny-list.
  4. Only now `sys.path.insert(0, pkg_root)`, so the target's imports resolve to
     the clone — every target import from here runs under full confinement.
Then dispatch to `introspect`, serialize the result to the capture descriptor,
and exit. EVERY failure is a no-capture exit (reason on stderr, no bytes on the
capture FD), never a hang.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

CAPTURE_FD_ENV = "CRUX_ARCH_CAPTURE_FD"


class ConfinementError(Exception):
    """A confinement control could not be established. Fail-closed: no-capture."""


# ── network cut ─────────────────────────────────────────────────────────────

def _neutralize_sockets_macos() -> None:
    """macOS best-effort: deny outbound connect/bind on the Python `socket.socket`
    class and the `create_connection` helper. This overrides METHODS rather than
    replacing the class, so it does not break modules that subclass `socket`
    (e.g. `ssl.SSLSocket`) at import. Python-level defense-in-depth only — native
    or C-extension code can still open a socket through a native path (stated
    residual). The audithook's `socket.*` deny is the paired Python-level control
    and is the primary macOS network cut (it fires on socket creation/connect)."""
    import socket

    def _denied(*_a, **_k):
        raise ConfinementError("socket connect/bind denied (macOS best-effort neutralization)")

    for meth in ("connect", "connect_ex", "bind"):
        try:
            setattr(socket.socket, meth, _denied)
        except (TypeError, AttributeError):
            pass
    try:
        socket.create_connection = _denied  # type: ignore[assignment]
    except Exception:  # pragma: no cover - defensive
        pass


def _cut_network() -> None:
    if sys.platform.startswith("linux"):
        unshare = getattr(os, "unshare", None)
        newuser = getattr(os, "CLONE_NEWUSER", None)
        newnet = getattr(os, "CLONE_NEWNET", None)
        if unshare is None or newuser is None or newnet is None:
            raise ConfinementError(
                "network namespace unavailable: os.unshare / CLONE_NEW* missing "
                "(the Linux network cut requires CPython 3.12+)"
            )
        try:
            unshare(newuser | newnet)
        except OSError as e:
            raise ConfinementError(f"network namespace could not be established: {e}")
    else:
        # macOS: no kernel network namespace in stdlib — best-effort only.
        _neutralize_sockets_macos()


# ── resource caps ───────────────────────────────────────────────────────────

def _set_rlimits() -> None:
    import resource

    # No RLIMIT_FSIZE here: ADR-0075 adopted Option G (the temp-clone reroute) as
    # the write control and kept RLIMIT_FSIZE (Option F) only as a secondary
    # byte-size cap; the audithook write-deny outside scratch is the enforced
    # boundary, so omitting FSIZE is deliberate and changes no behavior.
    required = [("RLIMIT_CPU", 30), ("RLIMIT_NPROC", 64), ("RLIMIT_NOFILE", 256)]
    if sys.platform.startswith("linux"):
        required.append(("RLIMIT_AS", 2 * 1024 * 1024 * 1024))  # Linux-only per ADR

    for const_name, value in required:
        what = getattr(resource, const_name, None)
        if what is None:
            raise ConfinementError(f"required rlimit {const_name} unsupported on this platform")
        try:
            _soft, hard = resource.getrlimit(what)
            # soft == hard so the child cannot raise them. Never raise `hard`
            # (unprivileged can only lower it): clamp the target down to the
            # existing hard ceiling when that ceiling is already below the pin.
            target = value if hard == resource.RLIM_INFINITY else min(value, hard)
            resource.setrlimit(what, (target, target))
        except (ValueError, OSError) as e:
            raise ConfinementError(f"required rlimit {const_name} could not be set: {e}")


# ── audit deny-list ─────────────────────────────────────────────────────────

_WRITE_OPEN_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


def _install_audithook(scratch: str) -> None:
    # realpath (NOT abspath): resolve symlinks so a symlink created inside
    # scratch that points outside it cannot smuggle a write past the containment
    # check. A pure-Python `open` through such a link is an escape abspath misses.
    scratch_real = os.path.realpath(scratch)
    scratch_prefix = scratch_real + os.sep

    def _is_write_open(mode, flags) -> bool:
        if isinstance(mode, str) and any(c in mode for c in "wax+"):
            return True
        if isinstance(flags, int) and (flags & _WRITE_OPEN_FLAGS):
            return True
        return False

    def _hook(event: str, args) -> None:
        # Fail-closed on the enumerated deny-list (ADR-0075 decision 2).
        if event.startswith("socket.") or event.startswith("subprocess."):
            raise ConfinementError(f"denied audit event: {event}")
        if event == "os.system" or event.startswith("os.exec"):
            raise ConfinementError(f"denied audit event: {event}")
        if event in ("os.fork", "os.forkpty", "os.posix_spawn"):
            raise ConfinementError(f"denied audit event: {event}")
        if event == "open":
            path = args[0] if args else None
            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 else None
            # Only string/bytes/PathLike targets are resolvable to a containment
            # check. An int fd-relative open (or None) is not evaluated — that is
            # the same native/below-Python residual ADR-0075 decision 2 states
            # the audithook cannot catch, not a hole opened here.
            if isinstance(path, (str, bytes, os.PathLike)) and _is_write_open(mode, flags):
                target = os.path.realpath(os.fsdecode(path))
                if not (target == scratch_real or target.startswith(scratch_prefix)):
                    raise ConfinementError(f"denied write open outside scratch: {target}")

    sys.addaudithook(_hook)


# ── main ────────────────────────────────────────────────────────────────────

def _fail(reason: str) -> "int":
    sys.stderr.write(f"no-capture: {reason}\n")
    return 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crux-arch-runtime-child", add_help=True)
    parser.add_argument("--scratch", required=True, help="scratch tempdir (write boundary)")
    parser.add_argument("--pkg-root", required=True, help="temp-clone root for sys.path")
    parser.add_argument("--capture-fd", type=int, default=None, help="capture write descriptor")
    grammar = parser.add_mutually_exclusive_group(required=True)
    grammar.add_argument("--app", help="module:attr (FastAPI/Flask instance or factory)")
    grammar.add_argument("--settings", help="dotted DJANGO_SETTINGS_MODULE path")
    args = parser.parse_args(argv)

    capture_fd = args.capture_fd
    if capture_fd is None:
        env_fd = os.environ.get(CAPTURE_FD_ENV)
        capture_fd = int(env_fd) if env_fd is not None else None
    if capture_fd is None:
        return _fail("no capture descriptor supplied")

    # Suppress bytecode BEFORE the first import this process performs, not after.
    # `build_child_env` deliberately does not pass PYTHONDONTWRITEBYTECODE (the
    # ADR-0075 decision 2 allowlist is minimal by design), so this assignment is
    # the only thing standing between the child and a `__pycache__` write. Set one
    # statement later — after the `exec_module` below — it left
    # `runtime/__pycache__/introspect.cpython-*.pyc` beside this file, i.e. inside
    # the plugin directory. Under `sync.sh` that directory is the staged release
    # artifact, so the write tripped the single-byte-set invariant and the release
    # refused with "gates mutated the staged tree". Ordering is the whole fix.
    sys.dont_write_bytecode = True

    # 1. Load the TRUSTED introspection module by ABSOLUTE FILE PATH from this
    #    script's own directory, BEFORE the target clone is placed on sys.path. A
    #    bare `import introspect` after rooting sys.path at the clone could be
    #    shadowed by a target whose top-level package is literally named
    #    `introspect`, running target import-time code UNCONFINED at the
    #    trusted-import step. A file-path load is immune to sys.path composition.
    try:
        import importlib.util
        _intro_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "introspect.py")
        _spec = importlib.util.spec_from_file_location("_crux_arch_introspect", _intro_path)
        introspect = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(introspect)
    except Exception as e:  # pragma: no cover - defensive
        return _fail(f"could not load introspection module: {e}")

    # 2. Confinement FIRST, each fail-closed to no-capture. Every step here uses
    #    ONLY stdlib (`socket`, `resource`, the audit machinery), and each such
    #    stdlib import resolves to the REAL stdlib because the target clone is
    #    NOT on sys.path yet: the parent set no PYTHONPATH, so the clone was
    #    absent from sys.path at interpreter startup too, and it is inserted only
    #    at step 3 below (after the audit deny-list installs). A target whose
    #    top-level package shadows a stdlib name (e.g. `socket` or `resource`)
    #    therefore cannot run its import-time code unconfined during setup.
    try:
        _cut_network()
        _set_rlimits()
        _install_audithook(args.scratch)
    except ConfinementError as e:
        return _fail(str(e))

    # 3. Only now root sys.path at the clone, so the TARGET's own imports resolve
    #    to the copy — every target import from here runs under full confinement.
    sys.path.insert(0, args.pkg_root)

    # Dispatch — the target's import-time code runs here, fully confined.
    try:
        if args.app is not None:
            result = introspect.introspect_app(args.app)
        else:
            result = introspect.introspect_settings(args.settings)
    except introspect.NoCapture as e:
        return _fail(str(e))
    except ConfinementError as e:
        return _fail(str(e))
    except BaseException as e:  # a denied audit event surfaces here too
        return _fail(f"{type(e).__name__}: {e}")

    # Serialize to the dedicated capture descriptor (stdlib json; the parent
    # re-validates and re-escapes authoritatively via capture.parse_and_validate).
    try:
        payload = json.dumps(result, ensure_ascii=True).encode("utf-8")
        os.write(capture_fd, payload)
        os.close(capture_fd)
    except OSError as e:
        return _fail(f"could not write capture: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
