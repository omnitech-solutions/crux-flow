from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass
import errno
import fcntl
import math
import time
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Iterator
import uuid

from .common import FlowError, canonical, decode, digest, identity, mapping, utc_now

JOURNAL = '.crux-flow/transactions'
LOCK = '.crux-flow/locks/mutations.lock'
MAX_FILE = 16_000_000


def relative(path: str, *, internal: bool = False) -> str:
    p = PurePosixPath(path)
    if not path or p.is_absolute() or '\\' in path or any(x in ('', '.', '..') for x in path.split('/')):
        raise FlowError('unsafe relative path')
    if any(ord(x) < 32 for x in path):
        raise FlowError('control character in path')
    if not internal and (path.startswith(JOURNAL + '/') or path == JOURNAL or path.startswith('.crux-flow/locks/')):
        raise FlowError('a transaction cannot edit its own journal or lock')
    return path


@contextmanager
def parent_fd(root: Path, path: str, *, create: bool = False) -> Iterator[tuple[int, str]]:
    parts = relative(path, internal=True).split('/')
    if create:
        root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        yield fd, parts[-1]
    finally:
        os.close(fd)


def read_file(root: Path, path: str) -> bytes | None:
    relative(path, internal=True)
    try:
        with parent_fd(root, path) as (fd, leaf):
            try:
                src = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                return None
            with os.fdopen(src, 'rb') as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
                    raise FlowError('managed path is not a bounded regular file')
                data = handle.read(MAX_FILE + 1)
                if len(data) > MAX_FILE:
                    raise FlowError('managed file grew beyond its size bound')
                return data
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise FlowError('symlink or non-directory in managed path') from None
        raise


def write_file(root: Path, path: str, content: bytes | None, mode: int = 0o600) -> None:
    with parent_fd(root, path, create=content is not None) as (fd, leaf):
        try:
            existing = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(existing.st_mode):
                raise FlowError('refusing to replace a non-regular managed file')
        except FileNotFoundError:
            pass
        if content is None:
            try:
                os.unlink(leaf, dir_fd=fd)
                os.fsync(fd)
            except FileNotFoundError:
                pass
            return
        temporary = f'.flow-{uuid.uuid4().hex}.tmp'
        target = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=fd)
        try:
            with os.fdopen(target, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, leaf, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass


@contextmanager
def locked(root: Path, *, timeout: float = 10.0) -> Iterator[None]:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise FlowError('invalid mutation lock deadline')
    deadline = time.monotonic() + timeout
    with parent_fd(root, LOCK, create=True) as (fd, leaf):
        lock = os.open(leaf, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise FlowError('mutation lock deadline exceeded; another writer is active') from None
                    time.sleep(min(0.02, max(0, deadline - time.monotonic())))
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)


@dataclass(frozen=True)
class Change:
    path: str
    before: bytes | None
    after: bytes | None
    mode: int

    def summary(self) -> dict:
        return {'path': self.path, 'before': digest(self.before), 'after': digest(self.after), 'mode': self.mode}


@dataclass(frozen=True)
class Plan:
    root: Path
    owner: str
    changes: tuple[Change, ...]
    guards: tuple[tuple[str, str | None], ...] = ()

    @property
    def plan_digest(self) -> str:
        return identity(self.public(include_digest=False))

    def public(self, *, include_digest: bool = True) -> dict:
        value = {'schema_version': 1, 'root': str(self.root), 'owner': self.owner,
                 'changes': [c.summary() for c in self.changes], 'guards': dict(self.guards)}
        if include_digest:
            value['plan_digest'] = self.plan_digest
        return value


def plan(root: Path, updates: dict[str, bytes | None], *, owner: str,
         guards: dict[str, str | None] | None = None, modes: dict[str, int] | None = None) -> Plan:
    root = root.resolve()
    if re.fullmatch(r'[A-Za-z0-9._:-]{1,120}', owner) is None:
        raise FlowError('invalid transaction owner')
    changes = []
    for path, value in sorted(updates.items()):
        relative(path)
        if value is not None and (not isinstance(value, bytes) or len(value) > MAX_FILE):
            raise FlowError('invalid managed file content')
        mode = (modes or {}).get(path, 0o600)
        if mode not in (0o600, 0o644, 0o755):
            raise FlowError('unsupported managed file mode')
        changes.append(Change(path, read_file(root, path), value, mode))
    checked = tuple(sorted((relative(p), v) for p, v in (guards or {}).items()))
    return Plan(root, owner, tuple(changes), checked)


def _encode(data: bytes | None) -> str | None:
    return base64.b64encode(data).decode('ascii') if data is not None else None


def _decode(data: str | None) -> bytes | None:
    try:
        return base64.b64decode(data, validate=True) if data is not None else None
    except (ValueError, TypeError) as exc:
        raise FlowError('invalid recovery content') from exc


def _record(plan_: Plan, transaction_id: str) -> dict:
    payload = {'id': transaction_id, 'root': str(plan_.root), 'owner': plan_.owner,
               'plan_digest': plan_.plan_digest, 'created_at': utc_now(),
               'changes': [dict(c.summary(), before_bytes=_encode(c.before), after_bytes=_encode(c.after)) for c in plan_.changes]}
    return {'schema_version': 1, 'status': 'prepared', 'payload': payload, 'payload_digest': identity(payload)}


def _save(root: Path, record: dict) -> None:
    write_file(root, f'{JOURNAL}/{record["payload"]["id"]}.json', canonical(record))


def _preflight(plan_: Plan) -> None:
    for c in plan_.changes:
        if read_file(plan_.root, c.path) != c.before:
            raise FlowError('stale mutation plan; current file differs from inspected bytes')
    for path, expected in plan_.guards:
        if digest(read_file(plan_.root, path)) != expected:
            raise FlowError('stale mutation plan; dependency changed')


def _restore(root: Path, changes: tuple[Change, ...], applied: list[Change]) -> None:
    for c in reversed(applied):
        current = read_file(root, c.path)
        if current not in (c.before, c.after):
            raise FlowError('recovery refused a subsequent local edit')
        if current != c.before:
            write_file(root, c.path, c.before, c.mode)


def apply(plan_: Plan, *, expected_digest: str | None = None, transaction_id: str | None = None) -> dict:
    if expected_digest is not None and expected_digest != plan_.plan_digest:
        raise FlowError('approved plan digest does not match')
    with locked(plan_.root):
        _preflight(plan_)
        changed = [c for c in plan_.changes if c.before != c.after]
        if not changed:
            return {'status': 'no-op', 'changed': [], 'transaction_id': None, 'plan_digest': plan_.plan_digest}
        selected_id = transaction_id or uuid.uuid4().hex
        if re.fullmatch("[0-9a-f]{32}", selected_id) is None or read_file(plan_.root, f"{JOURNAL}/{selected_id}.json") is not None:
            raise FlowError("invalid or already used transaction identity")
        record = _record(plan_, selected_id)
        _save(plan_.root, record)
        applied: list[Change] = []
        try:
            for c in changed:
                if read_file(plan_.root, c.path) != c.before:
                    raise FlowError('file changed during transaction')
                write_file(plan_.root, c.path, c.after, c.mode)
                applied.append(c)
            record['status'] = 'committed'
            _save(plan_.root, record)
        except BaseException:
            try:
                _restore(plan_.root, plan_.changes, applied)
                record['status'] = 'recovered'
                _save(plan_.root, record)
            except BaseException as exc:
                record['status'] = 'recovery-required'
                try:
                    _save(plan_.root, record)
                except OSError:
                    pass
                raise FlowError(f'recovery required for transaction {record["payload"]["id"]}') from exc
            raise
        return {'status': 'committed', 'changed': [c.path for c in changed],
                'transaction_id': record['payload']['id'], 'plan_digest': plan_.plan_digest}


def receipt(root: Path, transaction_id: str) -> dict:
    if re.fullmatch('[0-9a-f]{32}', transaction_id) is None:
        raise FlowError('invalid transaction id')
    raw = read_file(root, f'{JOURNAL}/{transaction_id}.json')
    if raw is None:
        raise FlowError('transaction receipt missing')
    record = mapping(decode(raw), allowed={'schema_version','status','payload','payload_digest'}, required={'schema_version','status','payload','payload_digest'})
    payload = mapping(record['payload'])
    if record['schema_version'] != 1 or payload.get('id') != transaction_id or payload.get('root') != str(root.resolve()) or identity(payload) != record['payload_digest']:
        raise FlowError('transaction receipt identity mismatch')
    if not isinstance(payload.get('changes'), list):
        raise FlowError('invalid transaction changes')
    paths: set[str] = set()
    for c in payload['changes']:
        mapping(c, required={'path','before','after','before_bytes','after_bytes','mode'})
        relative(c['path'])
        if c['path'] in paths or digest(_decode(c['before_bytes'])) != c['before'] or digest(_decode(c['after_bytes'])) != c['after']:
            raise FlowError('invalid transaction snapshot')
        paths.add(c['path'])
    return record


def rollback(root: Path, transaction_id: str, *, recover: bool = False) -> dict:
    root = root.resolve()
    with locked(root):
        record = receipt(root, transaction_id)
        permitted = {'prepared','recovery-required'} if recover else {'committed'}
        if record['status'] not in permitted:
            raise FlowError('transaction is not eligible for this recovery operation')
        changes: list[Change] = []
        for c in record['payload']['changes']:
            before, after = _decode(c['before_bytes']), _decode(c['after_bytes'])
            current = read_file(root, c['path'])
            if current != after and not (recover and current == before):
                raise FlowError('rollback refused because a file changed independently')
            changes.append(Change(c['path'], before, after, c['mode']))
        initial = {c.path: read_file(root,c.path) for c in changes}
        previous_status=record['status']
        try:
            _restore(root, tuple(changes), changes)
            record['status'] = 'recovered' if recover else 'rolled-back'
            _save(root, record)
        except BaseException:
            try:
                for c in changes:
                    current=read_file(root,c.path)
                    if current not in (c.before,c.after):
                        raise FlowError('rollback recovery encountered an independent edit')
                    if current!=initial[c.path]: write_file(root,c.path,initial[c.path],c.mode)
                record['status']=previous_status
                _save(root,record)
            except BaseException as exc:
                record['status']='recovery-required'
                try: _save(root,record)
                except OSError: pass
                raise FlowError(f'recovery required for transaction {transaction_id}') from exc
            raise
        return {'status': record['status'], 'transaction_id': transaction_id}
