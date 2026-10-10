"""Operation-scoped, descriptor-anchored admission source transport."""
from __future__ import annotations

import fnmatch
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from weakref import WeakSet


class SourceIORefusal(ValueError):
    """Source transport refused without reproducing untrusted source bytes."""

    def __init__(self, code="admission-source-io-refused"):
        self.code = code
        super().__init__(code)


class SourceIOExcluded(SourceIORefusal):
    """A caller's traversal boundary excluded a repository path."""

    def __init__(self):
        super().__init__("admission-source-io-excluded")


class _TraversalFailure(Exception):
    def __init__(self, problem):
        self.problem = problem


def _refuse(code="admission-source-io-refused"):
    raise SourceIORefusal(code)


def _identity(info):
    return info.st_dev, info.st_ino, info.st_mode


def _read_limit(value):
    from implementation_migration import MAX_SOURCE_BYTES
    value = MAX_SOURCE_BYTES if value is None else value
    if type(value) is not int or not 0 < value <= 4 * 1024 * 1024:
        _refuse()
    return value


@dataclass(frozen=True)
class _SourceFootprint:
    root: Path
    identity: tuple
    limit: int
    callback: object
    observed: tuple
    excluded: tuple
    components: tuple


@dataclass(frozen=True)
class _PathState:
    root: Path
    identity: tuple
    limit: int
    callback: object
    path: Path
    kind: str | None
    metadata: tuple | None
    raw_bytes: bytes | None
    names: tuple
    parent_metadata: tuple | None
    parent_names: tuple


@dataclass(frozen=True, eq=False)
class _ValidatedTransitions:
    footprint: _SourceFootprint
    transitions: tuple
    changes: tuple
    parents: tuple
    memberships: tuple


_validated_receipts = WeakSet()


def _state_identity(metadata):
    if metadata is None: return None
    values = dict(metadata)
    return (values["st_dev"], values["st_ino"], values["st_mode"]), values["target"]


def _leaf_value(state):
    return state.kind, state.metadata, state.raw_bytes, state.names


class SourceIO:
    """Read one repository through anchored descriptors; retain an input snapshot."""

    def __init__(self, root: Path, *, max_bytes: int | None = None,
                 _traversal_check: Callable[[Path], None] | None = None):
        self._fd = None
        self._max_bytes = _read_limit(max_bytes)
        self._traversal_check = _traversal_check
        try:
            self.root = Path(root).resolve()
        except (OSError, RuntimeError, ValueError):
            _refuse()
        self._observed = {}
        self._excluded = {}
        self._components = {}
        current = os.open(self.root.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in self.root.parts[1:]:
                before = os.stat(component, dir_fd=current, follow_symlinks=False)
                opened = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=current)
                if _identity(before) != _identity(os.fstat(opened)):
                    os.close(opened)
                    _refuse()
                os.close(current)
                current = opened
            self._fd = current
            self._root_identity = _identity(os.fstat(current))
            current = None
        except OSError:
            _refuse()
        finally:
            if current is not None:
                os.close(current)

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __del__(self):
        self.close()

    def _given(self, path):
        path = Path(path)
        path = path if path.is_absolute() else self.root / path
        if ".." in path.parts or "\x00" in str(path):
            _refuse("admission-source-io-containment-refused")
        try:
            return path, list(path.relative_to(self.root).parts)
        except ValueError:
            _refuse("admission-source-io-containment-refused")

    def _check_path(self, path, request, identities):
        """Preserve callback findings while retaining excluded traversal inputs."""
        if self._traversal_check is None:
            return
        try:
            self._traversal_check(path)
        except SourceIOExcluded:
            method, given, argument = request
            given, _ = self._given(given)
            key = method, given, argument
            value = path, tuple(identities)
            if key in self._excluded and self._excluded[key] != value:
                _refuse()
            self._excluded[key] = value
            raise
        except Exception as problem:
            raise _TraversalFailure(problem) from None

    def _walk(self, path, *, _request, _tail=()):
        """Resolve aliases within the anchor and validate every descriptor opened."""
        _, pending = self._given(path)
        # Alias targets must exist. An original suffix below a resolved
        # directory alias may instead be an absent optional input.
        target_components = [False] * len(pending)
        if self._fd is None:
            _refuse()
        descriptors, components, identities = [os.dup(self._fd)], [], []
        links = 0
        try:
            self._check_path(self.root.joinpath(*pending, *_tail), _request, identities)
            while pending:
                component = pending.pop(0)
                required_target = target_components.pop(0)
                self._check_path(self.root.joinpath(*components, component, *pending, *_tail),
                                 _request, identities)
                self._check_path(self.root.joinpath(*components, component), _request, identities)
                if component == ".":
                    continue
                if component == "..":
                    if not components:
                        _refuse("admission-source-io-containment-refused")
                    components.pop()
                    os.close(descriptors.pop())
                    continue
                current = descriptors[-1]
                try:
                    info = os.stat(component, dir_fd=current, follow_symlinks=False)
                except FileNotFoundError:
                    self._component(self.root.joinpath(*components, component), None)
                    if required_target:
                        identities.append(("dangling-alias", component))
                    result = components + [component] + pending
                    normalized = []
                    for part in result:
                        if part == "..":
                            if not normalized: _refuse()
                            normalized.pop()
                        elif part != ".": normalized.append(part)
                    return None, self.root.joinpath(*normalized), tuple(identities)
                target = target_text = None
                if stat.S_ISLNK(info.st_mode):
                    target_text = os.readlink(component, dir_fd=current)
                    target = Path(target_text)
                    if _identity(info) != _identity(os.stat(component, dir_fd=current, follow_symlinks=False)):
                        _refuse()
                self._component(self.root.joinpath(*components, component),
                                (_identity(info), target_text))
                identities.append((component, _identity(info)))
                if target is not None:
                    links += 1
                    if links > 40:
                        _refuse()
                    identities.append(("alias", target_text))
                    if target.is_absolute():
                        try:
                            target_parts = list(target.relative_to(self.root).parts)
                        except ValueError:
                            _refuse("admission-source-io-containment-refused")
                        while len(descriptors) > 1: os.close(descriptors.pop())
                        components = []
                    else:
                        target_parts = list(target.parts)
                    pending = target_parts + pending
                    target_components = [True] * len(target_parts) + target_components
                    self._check_path(self.root.joinpath(*components, *pending, *_tail),
                                     _request, identities)
                    continue
                directory = stat.S_ISDIR(info.st_mode)
                if not directory and (pending or not stat.S_ISREG(info.st_mode)):
                    _refuse()
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if directory: flags |= os.O_DIRECTORY
                self._check_path(self.root.joinpath(*components, component), _request, identities)
                opened = os.open(component, flags, dir_fd=current)
                if _identity(info) != _identity(os.fstat(opened)):
                    os.close(opened)
                    _refuse()
                descriptors.append(opened)
                components.append(component)
            return os.dup(descriptors[-1]), self.root.joinpath(*components), tuple(identities)
        except SourceIORefusal:
            raise
        except _TraversalFailure as failure:
            raise failure.problem from None
        except (OSError, ValueError):
            _refuse()
        finally:
            for descriptor in descriptors: os.close(descriptor)

    def _remember(self, method, path, value, argument=None):
        given, _ = self._given(path)
        key = method, given, argument
        if key in self._observed and self._observed[key] != value:
            _refuse()
        self._observed[key] = value

    def _component(self, path, identity):
        """Refuse changed components before a later method traverses them."""
        if path in self._components and self._components[path] != identity:
            _refuse()
        self._components[path] = identity

    def kind(self, path: Path):
        descriptor, resolved, identities = self._walk(path, _request=("kind", path, None))
        try:
            if descriptor is None and any(item[0] == "dangling-alias" for item in identities):
                _refuse("admission-source-io-nonresolution-refused")
            result = None if descriptor is None else (
                "directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file")
            self._remember("kind", path, (result, resolved, identities))
            return result
        finally:
            if descriptor is not None: os.close(descriptor)

    def resolve(self, path: Path) -> Path:
        descriptor, resolved, identities = self._walk(path, _request=("resolve", path, None))
        try:
            self._remember("resolve", path, (descriptor is not None, resolved, identities))
            return resolved
        finally:
            if descriptor is not None: os.close(descriptor)

    def metadata(self, path: Path) -> dict | None:
        """Return anchored final-leaf lstat fields without following its target."""
        given, parts = self._given(path)
        request = "metadata", given, None
        descriptor, resolved, identities = self._walk(given.parent if parts else given,
            _request=request, _tail=(given.name,) if parts else ())
        try:
            info, target = None, None
            if descriptor is not None:
                if not parts:
                    info = os.fstat(descriptor)
                else:
                    self._check_path(resolved / given.name, request, identities)
                    try:
                        info = os.stat(given.name, dir_fd=descriptor, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    if info is not None and stat.S_ISLNK(info.st_mode):
                        target = os.readlink(given.name, dir_fd=descriptor)
                        if _identity(info) != _identity(os.stat(given.name, dir_fd=descriptor, follow_symlinks=False)):
                            _refuse()
                    self._component(resolved / given.name, None if info is None else (_identity(info), target))
            values = None if info is None else {key: getattr(info, key) for key in (
                "st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")}
            if values is not None: values["target"] = target
            self._remember("metadata", given, (resolved, identities,
                None if values is None else tuple(values.items())))
            return values
        except _TraversalFailure as failure:
            raise failure.problem from None
        except OSError:
            _refuse()
        finally:
            if descriptor is not None: os.close(descriptor)

    def read_bytes(self, path: Path, *, max_bytes: int | None = None) -> bytes:
        """Return bounded raw bytes and snapshot their exact digest."""
        limit = self._max_bytes if max_bytes is None else _read_limit(max_bytes)
        descriptor, resolved, identities = self._walk(path, _request=("read_bytes", path, limit))
        try:
            if descriptor is None or not stat.S_ISREG(os.fstat(descriptor).st_mode):
                _refuse()
            if os.fstat(descriptor).st_size > limit:
                _refuse()
            chunks, size = [], 0
            while True:
                chunk = os.read(descriptor, min(65536, limit + 1 - size))
                if not chunk: break
                chunks.append(chunk); size += len(chunk)
                if size > limit: _refuse()
            content = b"".join(chunks)
            self._remember("read_bytes", path, (resolved, identities, hashlib.sha256(content).hexdigest()), limit)
            return content
        except OSError:
            _refuse()
        finally:
            if descriptor is not None: os.close(descriptor)

    def read_text(self, path: Path, *, max_bytes: int | None = None) -> str:
        """Match canonical UTF-8 text readers' universal newline presentation."""
        try:
            text = self.read_bytes(path, max_bytes=max_bytes).decode("utf-8")
        except UnicodeDecodeError:
            _refuse()
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def glob(self, path: Path, pattern: str) -> list[Path]:
        if "/" in pattern or "\x00" in pattern:
            _refuse()
        given, _ = self._given(path)
        descriptor, resolved, identities = self._walk(path, _request=("glob", path, pattern))
        try:
            if descriptor is None:
                names = []
            elif not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                _refuse()
            else:
                names = sorted(os.listdir(descriptor))
            self._remember("glob", path, (descriptor is not None, resolved, identities, tuple(names)), pattern)
            return [given / name for name in names if fnmatch.fnmatchcase(name, pattern)]
        except OSError:
            _refuse()
        finally:
            if descriptor is not None: os.close(descriptor)

    def revalidate(self):
        fresh = SourceIO(self.root, max_bytes=self._max_bytes,
                         _traversal_check=self._traversal_check)
        try:
            if fresh._root_identity != self._root_identity:
                _refuse()
            for (method, path, argument), expected in self._observed.items():
                function = getattr(fresh, method)
                if method == "read_bytes":
                    function(path, max_bytes=argument)
                else:
                    function(path) if argument is None else function(path, argument)
                if fresh._observed[(method, path, argument)] != expected:
                    _refuse()
            for (method, path, argument), expected in self._excluded.items():
                function = getattr(fresh, method)
                try:
                    if method == "read_bytes":
                        function(path, max_bytes=argument)
                    else:
                        function(path) if argument is None else function(path, argument)
                except SourceIOExcluded:
                    if fresh._excluded[(method, path, argument)] != expected:
                        _refuse()
                else:
                    _refuse()
        finally:
            fresh.close()

    def _capture_footprint(self):
        """Freeze the observed operation domain without selecting another roster."""
        if self._fd is None: _refuse()
        return _SourceFootprint(self.root, self._root_identity, self._max_bytes,
            self._traversal_check, tuple(self._observed.items()),
            tuple(self._excluded.items()), tuple(self._components.items()))

    def _capture_path_state(self, path, *, max_bytes=None):
        """Capture an exact owned leaf independently of the original snapshot."""
        given, parts = self._given(path)
        if not parts: _refuse()
        limit = self._max_bytes if max_bytes is None else _read_limit(max_bytes)
        fresh = SourceIO(self.root, max_bytes=self._max_bytes,
                         _traversal_check=self._traversal_check)
        try:
            if (fresh.root, fresh._root_identity) != (self.root, self._root_identity): _refuse()
            state = fresh._path_state(given, limit)
            fresh.revalidate()
            return state
        finally:
            fresh.close()

    def _path_state(self, given, limit):
        """Retain all state reads in this fresh validation operation."""
        metadata = self.metadata(given)
        if metadata is not None and not (stat.S_ISREG(metadata["st_mode"]) or
                                         stat.S_ISDIR(metadata["st_mode"])): _refuse()
        if self.resolve(given) != given: _refuse()
        kind = self.kind(given)
        raw = self.read_bytes(given, max_bytes=limit) if kind == "file" else None
        names = tuple(path.name for path in self.glob(given, "*")) if kind == "directory" else ()
        parent = self.metadata(given.parent)
        parent_names = tuple(path.name for path in self.glob(given.parent, "*"))
        return _PathState(self.root, self._root_identity, limit, self._traversal_check,
            given, kind, None if metadata is None else tuple(metadata.items()), raw,
            names, None if parent is None else tuple(parent.items()), parent_names)

    def _bind_footprint(self, footprint):
        if not isinstance(footprint, _SourceFootprint) or self._fd is None:
            _refuse()

        if (footprint.root, footprint.identity, footprint.limit) != (
                self.root, self._root_identity, self._max_bytes) or footprint.callback is not self._traversal_check:
            _refuse()

    def _transition_plan(self, transitions):
        changes, parents = {}, {}
        for before, after in transitions:
            if not isinstance(before, _PathState) or not isinstance(after, _PathState): _refuse()
            for state in (before, after):
                if (state.root, state.identity) != (self.root, self._root_identity) or state.callback is not self._traversal_check:
                    _refuse()
            if before.path != after.path or before.limit != after.limit: _refuse()
            path = before.path
            if path in changes:
                if _leaf_value(changes[path][1]) != _leaf_value(before): _refuse()
                changes[path] = changes[path][0], after
            else: changes[path] = before, after
            parent = path.parent
            if parent not in parents:
                parents[parent] = [before.parent_metadata, after.parent_metadata,
                                   before.parent_names, after.parent_names]
            else:
                parents[parent][1] = after.parent_metadata
                parents[parent][3] = after.parent_names
        # Directory names can change only for explicitly transitioned children.
        memberships = {}
        for path, (before, after) in changes.items():
            if before.kind == "directory" or after.kind == "directory":
                memberships[path] = [before.names, after.names]
        for parent, (_, _, old_names, new_names) in parents.items():
            memberships.setdefault(parent, [old_names, new_names])
        for parent, (old_names, _) in memberships.items():
            expected = set(old_names)
            for path, (before, after) in changes.items():
                if path.parent != parent: continue
                if before.kind is not None: expected.discard(path.name)
                if after.kind is not None: expected.add(path.name)
            memberships[parent] = tuple(sorted(expected))
        for before, after in transitions:
            allowed = {path.name for path in changes if path.parent == before.path.parent}
            if set(before.parent_names) - allowed != set(after.parent_names) - allowed: _refuse()
            for state in (before, after):
                if state.kind == "directory":
                    allowed = {path.name for path in changes if path.parent == state.path}
                    original = changes[state.path][0].names
                    if set(state.names) - allowed != set(original) - allowed: _refuse()
        return changes, parents, memberships

    @staticmethod
    def _membership_before(path, changes, parents):
        if path in changes: return changes[path][0].names
        return parents[path][2]

    @staticmethod
    def _validate_after_states(fresh, changes, parents, memberships):
        for path, (_, after) in changes.items():
            current = fresh._path_state(path, after.limit)
            metadata = parents[path][1] if path in parents else after.metadata
            if _state_identity(metadata) != _state_identity(after.metadata): _refuse()
            if (current.kind, current.metadata, current.raw_bytes) != (
                    after.kind, metadata, after.raw_bytes): _refuse()
            if current.kind == "directory" and current.names != memberships[path]: _refuse()
        for parent, (_, metadata, _, _) in parents.items():
            current = fresh.metadata(parent)
            if (None if current is None else tuple(current.items())) != metadata: _refuse()
            if tuple(path.name for path in fresh.glob(parent, "*")) != memberships[parent]: _refuse()

    def _validate_transitions(self, footprint, *, transitions=()):
        """Validate exact main-context pairs before joint layout/main replay."""
        self._bind_footprint(footprint)
        transitions = tuple(transitions)
        changes, parents, memberships = self._transition_plan(transitions)
        for path, expected in footprint.components:
            if path in changes and expected != _state_identity(changes[path][0].metadata): _refuse()
        for (method, _, _), expected in footprint.observed:
            if method == "glob" and expected[1] in memberships and expected[3] != self._membership_before(
                    expected[1], changes, parents): _refuse()
        fresh = SourceIO(self.root, max_bytes=self._max_bytes,
                         _traversal_check=self._traversal_check)
        try:
            if (fresh.root, fresh._root_identity) != (footprint.root, footprint.identity): _refuse()
            self._validate_after_states(fresh, changes, parents, memberships)
            fresh.revalidate()
        finally:
            fresh.close()
        receipt = _ValidatedTransitions(footprint, transitions, tuple(changes.items()),
            tuple((path, tuple(values)) for path, values in parents.items()), tuple(memberships.items()))
        _validated_receipts.add(receipt)
        return receipt

    def _replay_footprint(self, footprint, *, transitions=()):
        """Replay the whole main domain; a transition receipt supplies no approval."""
        self._bind_footprint(footprint)
        if isinstance(transitions, _ValidatedTransitions):
            if transitions not in _validated_receipts or transitions.footprint is not footprint: _refuse()
            receipt = transitions
        else:
            receipt = self._validate_transitions(footprint, transitions=transitions)
        changes, parents, memberships = dict(receipt.changes), dict(receipt.parents), dict(receipt.memberships)
        fresh = SourceIO(self.root, max_bytes=self._max_bytes,
                         _traversal_check=self._traversal_check)
        try:
            if (fresh.root, fresh._root_identity) != (footprint.root, footprint.identity): _refuse()
            for key, expected in footprint.observed:
                self._replay_request(fresh, key)
                actual = fresh._observed[key]
                if not self._transition_observation(key, expected, actual, changes, parents, memberships): _refuse()
            for key, expected in footprint.excluded:
                try: self._replay_request(fresh, key)
                except SourceIOExcluded:
                    if fresh._excluded[key] != expected: _refuse()
                else: _refuse()
            # Also retain component observations from refused/partial operations.
            for path, expected in footprint.components:
                if path not in fresh._components: fresh.metadata(path)
                actual = fresh._components.get(path)
                if path in changes:
                    before, after = changes[path]
                    if expected != _state_identity(before.metadata) or actual != _state_identity(after.metadata): _refuse()
                elif actual != expected: _refuse()
            self._validate_after_states(fresh, changes, parents, memberships)
            fresh.revalidate()
        finally:
            fresh.close()

    def _replay_metadata_footprint(self, footprint, receipt):
        """Replay existing layout metadata without importing the main roster."""
        self._bind_footprint(footprint)
        if self._traversal_check is not None or footprint.excluded or any(
                key[0] != "metadata" for key, _ in footprint.observed): _refuse()
        if not isinstance(receipt, _ValidatedTransitions) or receipt not in _validated_receipts: _refuse()
        if (receipt.footprint.root, receipt.footprint.identity, receipt.footprint.limit) != (
                footprint.root, footprint.identity, footprint.limit): _refuse()
        changes, parents, memberships = dict(receipt.changes), dict(receipt.parents), dict(receipt.memberships)
        fresh = SourceIO(self.root, max_bytes=self._max_bytes)
        updates = {}
        try:
            if (fresh.root, fresh._root_identity) != (footprint.root, footprint.identity): _refuse()
            for key, expected in footprint.observed:
                self._replay_request(fresh, key)
                actual = fresh._observed[key]
                # Layout never acquires a newly present path or another identity.
                if expected[2] != actual[2]:
                    if expected[2] is None or actual[2] is None: _refuse()
                    old, new = dict(expected[2]), dict(actual[2])
                    if not stat.S_ISDIR(old["st_mode"]) or _state_identity(expected[2]) != _state_identity(actual[2]): _refuse()
                if not self._transition_observation(key, expected, actual, changes, parents, memberships): _refuse()
                updates[key] = actual
            for path, expected in footprint.components:
                if path not in fresh._components: fresh.metadata(path)
                if fresh._components.get(path) != expected: _refuse()
            fresh.revalidate()
        finally:
            fresh.close()
        # Preserve the immutable original. Update only its validated existing
        # metadata values so the original main callback can continue reading.
        self._observed.update(updates)

    @staticmethod
    def _replay_request(fresh, key):
        method, path, argument = key
        function = getattr(fresh, method)
        if method == "read_bytes": function(path, max_bytes=argument)
        else: function(path) if argument is None else function(path, argument)

    @staticmethod
    def _transition_observation(key, before_value, after_value, changes, parents, memberships):
        method, given, _ = key
        old, new = list(before_value), list(after_value)
        if method == "read_bytes": route, index = old[0], 1
        elif method == "metadata": route, index = old[0] / given.name, 1
        else: route, index = old[1], 2
        # The root metadata request has no leaf name appended.
        if method == "metadata" and given == old[0]: route = given
        permitted = set()
        for path, states in changes.items():
            if path != route and path not in route.parents: continue
            for state in states:
                identity = _state_identity(state.metadata)
                if identity is not None: permitted.add((path.name, identity[0]))
        old[index] = tuple(row for row in old[index] if row not in permitted)
        new[index] = tuple(row for row in new[index] if row not in permitted)
        if route in changes:
            before, after = changes[route]
            if method == "read_bytes":
                if before.raw_bytes is None or after.raw_bytes is None: return False
                if old[2] != hashlib.sha256(before.raw_bytes).hexdigest(): return False
                old[2] = hashlib.sha256(after.raw_bytes).hexdigest()
            elif method in ("kind", "resolve", "glob"):
                expected = before.kind if method == "kind" else before.kind is not None
                if old[0] != expected: return False
                old[0] = after.kind if method == "kind" else after.kind is not None
        if method == "metadata":
            if route in changes:
                before, after = changes[route]
                if old[2] != before.metadata: return False
                old[2] = parents[route][1] if route in parents else after.metadata
            elif route in parents:
                if old[2] != parents[route][0]: return False
                old[2] = parents[route][1]
        if method == "glob" and route in memberships:
            if old[3] != SourceIO._membership_before(route, changes, parents): return False
            old[3] = memberships[route]
        return old == new
