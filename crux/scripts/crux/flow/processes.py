from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
from typing import Callable

from .common import FlowError, digest


@dataclass(frozen=True)
class ProcessResult:
    status: str
    exit_code: int | None
    elapsed_seconds: float
    output_bytes: int
    stdout: bytes = field(default=b'', repr=False)
    stderr: bytes = field(default=b'', repr=False)

    def public(self) -> dict:
        return {'status': self.status, 'exit_code': self.exit_code,
                'elapsed_seconds': round(self.elapsed_seconds, 6), 'output_bytes': self.output_bytes,
                'stdout_digest': digest(self.stdout), 'stderr_digest': digest(self.stderr)}


def stop_owned(process: subprocess.Popen) -> None:
    for sig, delay in ((signal.SIGTERM, 0.15), (signal.SIGKILL, 0.15)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        try:
            process.wait(timeout=delay)
        except subprocess.TimeoutExpired:
            continue
        if sig == signal.SIGTERM:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        break
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        pass


def execute(argv: list[str], *, cwd: Path, timeout: float, env: dict[str, str] | None = None,
            input_bytes: bytes = b'', limit: int = 2_000_000,
            progress: Callable[[dict], None] | None = None) -> ProcessResult:
    if not argv or any(not isinstance(x, str) or '\x00' in x for x in argv):
        raise FlowError('invalid subprocess argument vector')
    if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0 or type(limit) is not int or limit < 1:
        raise FlowError('subprocess needs finite positive time/output bounds')
    started = time.monotonic()
    deadline = started + timeout
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    outputs = {'stdout': bytearray(), 'stderr': bytearray()}
    total = 0
    sent = 0
    status = 'ok'
    next_progress = started + 5
    selector = selectors.DefaultSelector()
    try:
        for stream, label in ((process.stdout, 'stdout'), (process.stderr, 'stderr')):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        if input_bytes:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, 'stdin')
        else:
            process.stdin.close()
        while selector.get_map() or process.poll() is None:
            left = deadline - time.monotonic()
            if left <= 0:
                status = 'timeout'
                break
            for key, _ in selector.select(min(0.1, left)):
                if key.data == 'stdin':
                    try:
                        sent += os.write(key.fd, input_bytes[sent:sent+65536])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        sent = len(input_bytes)
                    if sent >= len(input_bytes):
                        selector.unregister(key.fileobj)
                        process.stdin.close()
                    continue
                try:
                    data = os.read(key.fd, 65536)
                except BlockingIOError:
                    continue
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                available = max(0, limit-total)
                outputs[key.data].extend(data[:available])
                total += len(data)
                if total > limit:
                    status = 'output-limit'
                    break
            if status != 'ok':
                break
            if progress is not None and time.monotonic() >= next_progress:
                progress({'event':'process-running','elapsed_seconds':round(time.monotonic()-started,3),'output_bytes':total})
                next_progress = time.monotonic() + 5
        if status != 'ok':
            stop_owned(process)
        else:
            process.wait(timeout=max(0.01, deadline-time.monotonic()))
            if process.returncode != 0:
                status = 'failed'
    except BaseException:
        stop_owned(process)
        raise
    finally:
        selector.close()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
    return ProcessResult(status, process.returncode, time.monotonic()-started, min(total,limit),
                         bytes(outputs['stdout']), bytes(outputs['stderr']))
