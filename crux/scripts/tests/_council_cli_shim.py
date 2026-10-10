"""A subprocess entry point that runs `run-council.py` against a counting mock gateway.

The behavioural tests in `test_council_attempt_behaviour.py` start this file as a child process:

    <python> _council_cli_shim.py --counter FILE [shim options] -- <run-council arguments>

It loads `crux/scripts/run-council.py` by path, builds an `httpx.MockTransport` that answers each
gate seat as the gateway does, and calls `main(argv, transport=...)`, the function the runner's
`__main__` calls. It then exits with that function's return code. The transport never reaches the
network. Each request appends one JSON line to the counter file before it is answered, so a
request is counted even when the process dies on it.

The runner passes its transport to the council, but `--recover` calls the recovery module with
none. So the shim also replaces `httpx.Client.__init__` and `httpx.AsyncClient.__init__`: every
httpx client the process builds, recovery's included, and the ones httpx's module-level helpers
(`httpx.get`, `httpx.request`, ...) build, uses the counting transport. A request with no JSON body
is counted and answered 404. Any internet socket connection outside httpx is counted under the
model `<socket>` and refused, so a request by another client library is counted too.

Shim options:

- `--decisions FILE`: a JSON object mapping a provider namespace (`openai`, `anthropic`,
  `google`) to `{"decision": ..., "findings": [...]}`; an absent seat approves with no finding.
- `--block-at-request N --barrier FILE`: the Nth request waits until FILE exists.
- `--exit-at-request N`: the Nth request ends the process with `os._exit(137)` once it is counted.
- `--start-barrier FILE`: wait until FILE exists before calling `main`, so two children start
  together.
- `--crash-at TARGET[:N]`: replace TARGET with a wrapper whose Nth call ends the process with
  `os._exit(137)` before the original runs. TARGET is `<module>.<attribute>[.<attribute>]`, read
  after `run-council.py` has imported its modules; `run_council` names the runner itself, so
  `run_council._print_summary` and `council_commit.verify_commit` are both targets.
- `--raise-after TARGET[:N]`: the Nth call runs the original, awaiting it when it returns a
  coroutine, and then raises `RuntimeError`.
- `--deadline SECONDS`: the longest any barrier wait lasts (default 60). A wait that reaches it
  ends the process with exit 3 and a fixed stderr line, so no test hangs.
- `--self-check`: the counter's positive control. Instead of calling `main`, make three requests
  that name no transport (`httpx.get`, an `httpx.AsyncClient` post, a raw socket connect) and exit
  0; each one is counted.

A target that cannot be found ends the process with exit 3 before `main` runs, naming the target.
The gateway reply's served model and provider are the live registry's accepted values for the
seat, read through `_council_gate_support.seat_values`, so a record stays admissible when the
registry moves a seat to another model.

Reads only `crux/` and the paths it is given. Never reads a key; never prints one.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import inspect
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
RUN_COUNCIL = SCRIPTS / "run-council.py"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SCRIPTS))

SHIM_FAULT = 3
CRASH = 137


def _fail(message: str) -> None:
    print(f"council-cli-shim: {message}", file=sys.stderr, flush=True)
    os._exit(SHIM_FAULT)


def _wait_for(path: Path, deadline: float, what: str) -> None:
    end = time.monotonic() + deadline
    while not path.exists():
        if time.monotonic() >= end:
            _fail(f"the {what} {path.name} did not appear within {deadline:g} seconds")
        time.sleep(0.02)


def _vote(decision: str = "APPROVE", findings: list | None = None) -> str:
    return json.dumps({"decision": decision, "confidence": 0.9,
                       "reasoning": "Completeness, Correctness, Consistency, Clarity and Security covered.",
                       "findings": list(findings or [])})


class Gateway:
    """Answers each seat with a vote; counts every request in the counter file."""

    def __init__(self, counter: Path, decisions: dict, block_at: int | None, barrier: Path | None,
                 exit_at: int | None, deadline: float):
        import httpx
        import _council_gate_support as sup

        self.counter = counter
        self.decisions = decisions
        self.block_at = block_at
        self.barrier = barrier
        self.exit_at = exit_at
        self.deadline = deadline
        self.n = 0
        self.per_ns: dict[str, int] = {}
        reg = sup.registry()
        self.served = {}
        for role in sup.ROLES:
            v = sup.seat_values(role, reg)
            self.served[v["provider_namespace"]] = v
        self.transport = httpx.MockTransport(self._handle)
        self._httpx = httpx

    def _count(self, model: str) -> None:
        line = json.dumps({"n": self.n, "model": model, "pid": os.getpid()}) + "\n"
        with open(self.counter, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    def _handle(self, request):
        # Count first, before anything about the request is read: a request with no JSON body (a
        # GET, say) is still a request, and it is counted before it is answered with a 404.
        try:
            payload = json.loads(request.content)
        except (ValueError, TypeError):
            payload = None
        model = payload.get("model", "") if isinstance(payload, dict) else ""
        model = model if isinstance(model, str) else ""
        ns = model.split("/", 1)[0]
        self.n += 1
        self.per_ns[ns] = self.per_ns.get(ns, 0) + 1
        self._count(model or f"<{request.method} {request.url.host}>")
        if self.exit_at is not None and self.n == self.exit_at:
            os._exit(CRASH)
        if self.block_at is not None and self.n == self.block_at:
            _wait_for(self.barrier, self.deadline, "barrier")
        seat = self.served.get(ns)
        if seat is None:
            return self._httpx.Response(404, json={"error": {"message": "unknown seat"}})
        over = self.decisions.get(ns, {})
        body = {
            "id": f"gen-{ns}-{self.per_ns[ns]}",
            "model": seat["served_model"],
            "provider": seat["served_provider"],
            "choices": [{"message": {"content": _vote(over.get("decision", "APPROVE"), over.get("findings"))},
                         "finish_reason": "stop"}],
        }
        return self._httpx.Response(200, json=body)


def _route_every_client(gateway: Gateway) -> None:
    """Make every httpx client this process builds use the counting transport.

    `run-council.py` hands its transport to the council, but recovery is called with none, and any
    other module could build its own client. Replacing `httpx.Client.__init__` and
    `httpx.AsyncClient.__init__` reaches every client, including the ones httpx's module-level
    helpers (`httpx.get`, `httpx.post`, `httpx.request`, `httpx.stream`) build. The replacement
    forces `transport`, clears `mounts`, `proxy` and `proxies`, and turns off `trust_env`, so no
    environment proxy mounts a transport of its own over the counting one."""
    import httpx

    for cls in (httpx.Client, httpx.AsyncClient):
        original = cls.__init__

        def init(self, *args, _original=original, **kwargs):
            for name in ("mounts", "proxy", "proxies"):
                kwargs.pop(name, None)
            kwargs["transport"] = gateway.transport
            kwargs["trust_env"] = False
            _original(self, *args, **kwargs)

        cls.__init__ = init


def _count_every_socket(gateway: Gateway) -> None:
    """Count, then refuse, any internet socket connection the process attempts outside httpx: a
    connection by another client library is a request the counting transport would not see."""
    import socket

    inet = (socket.AF_INET, socket.AF_INET6)
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def refused(sock) -> OSError:
        gateway.n += 1
        gateway._count("<socket>")
        return ConnectionRefusedError("council-cli-shim: the network is closed to this process")

    def connect(self, address):
        if self.family in inet:
            raise refused(self)
        return real_connect(self, address)

    def connect_ex(self, address):
        if self.family in inet:
            refused(self)
            return 111  # ECONNREFUSED on Linux; any non-zero value is a failed connect
        return real_connect_ex(self, address)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex


def _self_check() -> int:
    """The counter's positive control: three requests that name no transport, by the three routes
    `_route_every_client` and `_count_every_socket` close. Each must be counted; none reaches the
    network. Returns 0 once all three were made."""
    import asyncio
    import socket

    import httpx

    httpx.get("http://127.0.0.1:9/self-check")  # a module-level helper, sync client

    async def post() -> None:
        async with httpx.AsyncClient() as client:
            await client.post("http://127.0.0.1:9/self-check", json={"model": "self-check/one"})

    asyncio.run(post())
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.connect(("127.0.0.1", 9))
        except OSError:
            pass
    return 0


def _resolve(target: str):
    """(owner, attribute) for TARGET, importing the longest module prefix that is loaded or
    importable. Exits 3 when nothing resolves."""
    spec, _, _ = target.partition(":")
    parts = spec.split(".")
    if len(parts) < 2:
        _fail(f"the target {spec} names no attribute")
    for cut in range(len(parts) - 1, 0, -1):
        name = ".".join(parts[:cut])
        mod = sys.modules.get(name)
        if mod is None:
            try:
                mod = importlib.import_module(name)
            except Exception:  # noqa: BLE001 - an unimportable prefix: try a shorter one
                continue
        owner = mod
        try:
            for attr in parts[cut:-1]:
                owner = getattr(owner, attr)
        except AttributeError:
            continue
        if hasattr(owner, parts[-1]):
            return owner, parts[-1]
    _fail(f"the target {spec} is not loaded and cannot be imported")


def _nth(target: str) -> int:
    _, _, n = target.partition(":")
    try:
        return int(n) if n else 1
    except ValueError:
        _fail(f"the call number in {target} is not a whole number")


def _install_crash(target: str) -> None:
    owner, attr = _resolve(target)
    original = getattr(owner, attr)
    nth = _nth(target)
    calls = {"n": 0}

    def crash(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == nth:
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(CRASH)
        return original(*args, **kwargs)

    setattr(owner, attr, crash)


def _install_raise(target: str) -> None:
    owner, attr = _resolve(target)
    original = getattr(owner, attr)
    nth = _nth(target)
    calls = {"n": 0}

    def raising(*args, **kwargs):
        calls["n"] += 1
        result = original(*args, **kwargs)
        if calls["n"] != nth:
            return result
        if inspect.isawaitable(result):
            async def after():
                await result
                raise RuntimeError("council-cli-shim: injected failure after the call returned")
            return after()
        raise RuntimeError("council-cli-shim: injected failure after the call returned")

    setattr(owner, attr, raising)


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_council", RUN_COUNCIL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_council"] = mod  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str]) -> int:
    if "--" not in argv:
        _fail("pass the runner's arguments after --")
    cut = argv.index("--")
    own, runner_argv = argv[:cut], argv[cut + 1:]
    ap = argparse.ArgumentParser(prog="_council_cli_shim.py")
    ap.add_argument("--counter", required=True)
    ap.add_argument("--decisions")
    ap.add_argument("--block-at-request", type=int)
    ap.add_argument("--barrier")
    ap.add_argument("--exit-at-request", type=int)
    ap.add_argument("--start-barrier")
    ap.add_argument("--crash-at", action="append", default=[])
    ap.add_argument("--raise-after", action="append", default=[])
    ap.add_argument("--deadline", type=float, default=60.0)
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args(own)
    if args.block_at_request is not None and not args.barrier:
        _fail("--block-at-request needs --barrier")

    counter = Path(args.counter)
    counter.touch()
    decisions = json.loads(Path(args.decisions).read_text(encoding="utf-8")) if args.decisions else {}
    runner = _load_runner()
    gateway = Gateway(counter, decisions, args.block_at_request,
                      Path(args.barrier) if args.barrier else None, args.exit_at_request, args.deadline)
    _route_every_client(gateway)
    _count_every_socket(gateway)
    if args.self_check:
        return _self_check()
    for target in args.crash_at:
        _install_crash(target)
    for target in args.raise_after:
        _install_raise(target)
    if args.start_barrier:
        _wait_for(Path(args.start_barrier), args.deadline, "start barrier")
    rc = runner.main(runner_argv, transport=gateway.transport)
    sys.stdout.flush()
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
