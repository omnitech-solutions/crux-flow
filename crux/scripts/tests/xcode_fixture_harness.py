"""The data-driven Xcode-fixture harness `XcodeFixtureTests` in
`test_swift_pack.py` runs against (ADR-0130 clause 17).

This module is stdlib-only machinery, never imported by the pack itself. It
materializes one fixture directory into a temporary copy (applying any
`materialize:` directives -- a symlink, a FIFO, an oversize file -- so none
of those forms is ever committed under `fixtures/xcodeproj/`), runs
`derive-arch.py` in a SUBPROCESS with a process-creation trap and an audit
hook on `open`, `os.scandir` and `os.listdir` installed, and checks the
result against `expected.yml`.

Every case directory under `fixtures/xcodeproj/` is one fixture: a supported
form, a limitation form, a hostile project, workspace or `project.yml`, or a
harness control. Each drives the whole Swift pack, the Xcode reader included,
through a real derive.

## `expected.yml` schema

```yaml
# All keys optional except where noted.

materialize:                  # extra filesystem objects created in the
                               # temporary copy BEFORE the derive runs, so
                               # none of these forms is ever committed:
  - path: relative/path        # required; repo-relative destination
    kind: symlink               # symlink | fifo | oversize | padded | repeated
    target: relative/path       # symlink only: the link's target, relative
                                 # to the symlink's own directory
    size_bytes: 16777217        # oversize only: how large the fixture
                                 # binary content is (default: 16_777_217,
                                 # one byte past ADR-0130 clause 2's
                                 # 16 MiB `project.pbxproj` bound)
    source: relative/path        # padded only: a fixture-relative VALID
                                  # pbxproj, padded with a `/* ... */`
                                  # comment block to EXACTLY size_bytes --
                                  # unlike oversize's sparse write, the
                                  # result still parses
    head: "struct A {\n"          # repeated only: the file is `head` written
    tail: "}\n"                   # `count` times, then `tail` written
    count: 10000                  # `count` times (kind: repeated)

exit_code: 0                   # expected `derive-arch.py` exit code
                                # (default: 0)
strict: false                  # when true, ALSO run with --strict and
                                # assert the SAME exit_code

rows:                          # per rendered concern file (data-model,
                                # api-surface, module-graph, decision-index),
                                # a list of exact substrings that MUST
                                # appear in the rendered Markdown
  module-graph:
    - "| `Core` | `Package.swift` | library"

max_bytes:                     # per rendered concern file, the largest size
  data-model: 200000            # in bytes it may render at
absent:                        # per rendered concern file, substrings that
                                # must NOT appear
  api-surface:
    - "unresolved-reference"

residuals:                     # a list of residual bullets every one of
                                # which must appear, matched against the
                                # EXACT `swift.py::_residual_line` grammar:
                                #   - `<class>` `<path>` lines <a-b>[, <a-b>]* — <detail>
                                # `class` and `path` MUST sit in the SAME
                                # bullet -- a bullet naming this `class` and a
                                # different bullet naming this `path` is not a
                                # match. `kind` is optional and, when given,
                                # is matched against the bullet's `<detail>`
                                # exactly. `first_line`/`last_line` are
                                # optional and, when BOTH given, the matching
                                # bullet's own line span (the min start and
                                # max end across every `<a-b>` range in its
                                # `lines` clause, or "no span" for `lines —`)
                                # must COVER the expected `[first_line,
                                # last_line]` range -- a bullet's span may be
                                # wider than the expected range, never
                                # narrower.
  - class: missing-input
    path: App.xcodeproj/project.pbxproj
    kind: null
    first_line: null
    last_line: null
  - class: unresolved-reference        # `count:` (optional): when given,
    path: App.xcodeproj/project.pbxproj  # asserts the EXACT number of matching
    count: 64                            # bullets across every rendered concern
                                          # body (class/path/kind, ignoring any
                                          # line-range clause) -- `find_residual`
                                          # alone only proves at-least-one.
```

Every fixture directory holds `expected.yml` beside its source tree (a
`.bionic.yml`, a `bionic/manifest.yml`, and whatever project/package inputs
the case needs). The harness applies a bounded timeout per case so a FIFO
that nothing reads or writes can never hang the suite.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only outside `uv run`
    yaml = None

SCRIPTS = Path(__file__).resolve().parents[1]  # crux/scripts
DRIVER = SCRIPTS / "derive-arch.py"
FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures" / "xcodeproj"

#: Events that mean a child process was spawned (mirrors `test_swift_pack.py`
#: `_TRAP`, kept independent so this module has no import-time dependency on
#: the test file).
_PROCESS_EVENTS = (
    "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn",
    "os.fork", "os.forkpty",
)

#: The default size the `oversize` materialize kind writes: one byte past
#: ADR-0130 clause 2's 16 MiB `project.pbxproj` bound.
_DEFAULT_OVERSIZE_BYTES = 16 * 1024 * 1024 + 1

#: The EXACT residual-bullet grammar `swift.py::_residual_line` renders:
#: ``- `<class>` `<path>` lines <a-b>[, <a-b>]* — <detail>``.
#: A loose substring match (class found anywhere, path found anywhere) can
#: pass on two DIFFERENT bullets -- one naming the class, a different one
#: naming the path -- and an unclosed backtick match on `path` accepts any
#: string carrying it as a PREFIX. This regex pins class and path to the
#: SAME bullet, closed on both sides by backticks.
_RESIDUAL_LINE_RE = re.compile(
    r"^- `(?P<klass>[^`]*)` `(?P<path>[^`]*)` lines (?P<lines>.*?) — (?P<detail>.*)$"
)


def parse_residual_line(line: str) -> dict | None:
    """Parse one Markdown line against the exact residual-bullet grammar.

    Returns `None` when `line` does not match the grammar at all. Otherwise
    a dict with `klass`, `path`, `detail` (raw strings) and `span` -- `None`
    for the "lines —" (no location) form, or the `(min_start, max_end)` pair
    covering every `<a-b>` range the bullet's `lines` clause names.
    """
    m = _RESIDUAL_LINE_RE.match(line)
    if not m:
        return None
    lines_part = m.group("lines").strip()
    if lines_part == "—" or not lines_part:
        span = None
    else:
        spans = []
        for chunk in lines_part.split(", "):
            a, sep, b = chunk.partition("-")
            if not sep:
                return None
            try:
                spans.append((int(a), int(b)))
            except ValueError:
                return None
        if not spans:
            return None
        span = (min(a for a, _ in spans), max(b for _, b in spans))
    return {"klass": m.group("klass"), "path": m.group("path"),
            "detail": m.group("detail"), "span": span}


def find_residual(rendered: dict, expected: dict) -> bool:
    """True iff some line across every rendered concern body matches
    `expected`'s `class`/`path` (both required, same bullet), its optional
    `kind` (matched exactly against the bullet's `detail`), and -- when BOTH
    `first_line` and `last_line` are given -- a bullet span that COVERS that
    range."""
    klass, path = expected["class"], expected["path"]
    kind = expected.get("kind")
    first_line, last_line = expected.get("first_line"), expected.get("last_line")
    for body in rendered.values():
        for line in body.split("\n"):
            parsed = parse_residual_line(line)
            if parsed is None:
                continue
            if parsed["klass"] != klass or parsed["path"] != path:
                continue
            if kind is not None and parsed["detail"] != kind:
                continue
            if first_line is not None and last_line is not None:
                if parsed["span"] is None:
                    continue
                if not (parsed["span"][0] <= first_line and parsed["span"][1] >= last_line):
                    continue
            return True
    return False


def count_matching_residuals(rendered: dict, expected: dict) -> int:
    """The count of residual bullets across every rendered concern body
    that match `expected`'s `class`/`path` (both required, same bullet per
    `parse_residual_line`'s exact grammar) and optional `kind` (matched
    exactly against `detail`). Unlike `find_residual`, this never consults
    `first_line`/`last_line` -- a count assertion is about HOW MANY bullets
    a class/path/kind triple renders, not any one bullet's span."""
    klass, path = expected["class"], expected["path"]
    kind = expected.get("kind")
    n = 0
    for body in rendered.values():
        for line in body.split("\n"):
            parsed = parse_residual_line(line)
            if parsed is None:
                continue
            if parsed["klass"] != klass or parsed["path"] != path:
                continue
            if kind is not None and parsed["detail"] != kind:
                continue
            n += 1
    return n


class FixtureCaseError(AssertionError):
    """Raised when a fixture case's result does not match `expected.yml`."""


def load_expected(fixture_dir: Path) -> dict:
    if yaml is None:
        raise RuntimeError(
            "PyYAML is required to read expected.yml — run this harness "
            "through `uv run` (it self-re-exec's with pyyaml) rather than a "
            "bare interpreter.")
    text = (fixture_dir / "expected.yml").read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


def _apply_padded(src: Path, target_path: Path, entry: dict) -> None:
    """`kind: padded`: read `entry["source"]` (a fixture-relative path to a
    VALID pbxproj, resolved against `src` -- the fixture directory, never
    the destination copy) and pad it with a single `/* ... */` comment
    block inserted just before the text's LAST `}` so the result is still a
    syntactically valid pbxproj (comments are skipped as whitespace by the
    tokenizer, wherever they sit -- swift_pbxproj.py's `_tokenize`), landing
    at EXACTLY `entry["size_bytes"]`. Unlike `kind: oversize`'s sparse
    mostly-NUL write (which a real parser can never admit), this produces a
    file `swift_pbxproj.read_pbxproj` accepts, so a hostile case can sit
    precisely AT the ADR-0130 clause 2 16 MiB bound rather than only past
    it."""
    source_rel = entry["source"]
    size = entry["size_bytes"]
    source_bytes = (src / source_rel).read_bytes()
    idx = source_bytes.rfind(b"}")
    if idx < 0:
        raise ValueError(f"padded source {source_rel!r} carries no closing brace to pad before")
    prefix, suffix = source_bytes[:idx], source_bytes[idx:]
    # Overhead of the inserted comment shell around an all-dot filler:
    # b"/* " (3) + filler + b" */\n" (4) == 7 bytes plus the filler itself.
    overhead = len(b"/* ") + len(b" */\n")
    pad_needed = size - len(source_bytes) - overhead
    if pad_needed < 0:
        raise ValueError(
            f"padded size_bytes={size} is smaller than the source "
            f"({len(source_bytes)} bytes) plus the {overhead}-byte comment shell")
    filler = b"." * pad_needed
    comment = b"/* " + filler + b" */\n"
    result = prefix + comment + suffix
    if len(result) != size:
        raise ValueError(f"padded result is {len(result)} bytes, expected {size}")
    target_path.write_bytes(result)


def materialize(src: Path, dst: Path, spec: dict) -> None:
    """Copy `src` (a fixture directory) into `dst`, then apply every
    `materialize:` directive from `spec`. Never called on a directory that
    already contains a symlink, FIFO or oversize file — those forms are
    created here, at test time, and committed nowhere."""
    import shutil
    shutil.copytree(src, dst)
    for entry in spec.get("materialize", []) or []:
        # `entry["path"]` is fixture-authored YAML, not
        # attacker input in the threat-model sense, but a `..`-carrying
        # value here would still land OUTSIDE `dst` -- refuse it the same
        # way every other path this suite resolves is refused, rather than
        # trusting a lexical join.
        raw_rel = entry["path"]
        if os.path.isabs(raw_rel):
            raise ValueError(f"materialize path must be relative: {raw_rel!r}")
        target_path = dst / raw_rel
        normalized = os.path.normpath(str(target_path))
        dst_str = str(dst)
        if not (normalized == dst_str or normalized.startswith(dst_str + os.sep)):
            raise ValueError(f"materialize path escapes the destination: {raw_rel!r}")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        kind = entry["kind"]
        if kind == "symlink":
            link_target = entry["target"]
            if target_path.exists() or target_path.is_symlink():
                target_path.unlink()
            os.symlink(link_target, target_path)
        elif kind == "fifo":
            if target_path.exists():
                target_path.unlink()
            os.mkfifo(target_path)
        elif kind == "oversize":
            size = entry.get("size_bytes", _DEFAULT_OVERSIZE_BYTES)
            with open(target_path, "wb") as f:
                f.seek(size - 1)
                f.write(b"\0")
        elif kind == "padded":
            _apply_padded(src, target_path, entry)
        elif kind == "repeated":
            count = entry["count"]
            if not isinstance(count, int) or count < 1:
                raise ValueError(f"repeated count must be a positive integer: {count!r}")
            target_path.write_text(entry["head"] * count + entry.get("tail", "") * count,
                                   encoding="utf-8")
        else:
            raise ValueError(f"unknown materialize kind: {kind!r}")


#: The directory names an installer puts distributions under. A `sys.path`
#: entry with one of these names holds the installed dependencies the derive
#: imports: the dev `.venv`'s own, or the cached environment `uv run --no-project
#: --with ...` overlays onto an ephemeral one, which lives under the uv cache.
_DEPENDENCY_DIR_NAMES = ("site-packages", "dist-packages")


def _in_virtual_environment(site_dir: str) -> bool:
    """True when `site_dir` belongs to a virtual environment: a `pyvenv.cfg`
    sits in its environment root, one to three levels up
    (`<env>/lib/pythonX.Y/site-packages` on POSIX, `<env>/Lib/site-packages`
    on Windows)."""
    parents = Path(site_dir).parents
    return any((parents[i] / "pyvenv.cfg").is_file() for i in range(min(3, len(parents))))


#: The directory suffixes an installer writes a distribution's metadata under.
_METADATA_DIR_SUFFIXES = (".dist-info", ".egg-info")


def dependency_dirs() -> list:
    """The installed-dependency directories this interpreter imports from.

    A closed set over each `sys.path` entry and each `site.getsitepackages()`
    entry whose final component is `site-packages` or `dist-packages`:

    * A site directory whose environment carries a `pyvenv.cfg` is admitted
      whole. Those are the gate venv's, the uv ephemeral environment's and the
      uv overlay environment's. Under the release gates'
      `uv run --no-project --with ...` shape the pinned grammars live in the
      overlay environment, outside every interpreter prefix, so the prefixes
      alone would flag the derive's own imports.
    * A site directory outside every virtual environment is admitted only
      for its distribution-metadata directories (`*.dist-info`,
      `*.egg-info`), listed now. The uv ephemeral environment puts Homebrew's
      global `site-packages` on `sys.path`. The derive imports nothing from
      it, but its distribution lookup reads every metadata directory on
      `sys.path` (`pip` and `wheel` there), so the exact `sync.sh` shape needs
      those reads. No module, `.pth` file or other file there is admitted.
      Where a metadata file there is a link, as Homebrew's are, the link's
      own target file is admitted too (`_linked_metadata_targets`).

    An interpreter's own `site-packages` under its prefix, where a
    `pip install` into the interpreter lands (the Linux cells and CI), stays
    allowed through the interpreter prefixes. The set never names `$HOME`,
    `~/.cache` or the uv cache root."""
    import site
    out: list = []
    for entry in list(sys.path) + list(site.getsitepackages()):
        if not entry:
            continue
        norm = os.path.normpath(entry)
        if os.path.basename(norm) not in _DEPENDENCY_DIR_NAMES:
            continue
        if _in_virtual_environment(norm):
            admitted = [norm]
        else:
            try:
                with os.scandir(norm) as entries:
                    metadata = sorted(os.path.join(norm, e.name) for e in entries
                                      if e.name.endswith(_METADATA_DIR_SUFFIXES) and e.is_dir())
            except OSError:
                metadata = []
            admitted = []
            for meta in metadata:
                admitted.append(meta)
                admitted.extend(_linked_metadata_targets(meta))
        for path in admitted:
            if path not in out:
                out.append(path)
    return out


def _linked_metadata_targets(meta_dir: str) -> list:
    """The real path of each file linked directly inside `meta_dir`.

    Homebrew fills a global metadata directory with per-file links into its
    Cellar, so a distribution lookup's `open` resolves outside the directory
    the allowed set admits. Each link's own target file is admitted, and
    nothing else beside it."""
    targets: list = []
    try:
        with os.scandir(meta_dir) as entries:
            for e in entries:
                if e.is_symlink():
                    real = os.path.realpath(e.path)
                    if os.path.isfile(real):
                        targets.append(real)
    except OSError:
        return []
    return sorted(targets)


def listable_dirs() -> list:
    """The global site directories a child derive may list, by exact path.

    A distribution lookup lists every site directory on `sys.path` to find
    its metadata, and a global one lies outside every interpreter prefix
    when the interpreter is Homebrew's. Each such directory is admitted for
    a listing only, matched by its exact real path, so nothing beneath it is
    admitted to a listing or an open. A virtual environment's site
    directory is already admitted whole by `dependency_dirs()`."""
    import site
    out: list = []
    for entry in list(sys.path) + list(site.getsitepackages()):
        if not entry:
            continue
        norm = os.path.normpath(entry)
        if os.path.basename(norm) not in _DEPENDENCY_DIR_NAMES:
            continue
        if _in_virtual_environment(norm):
            continue
        if norm not in out:
            out.append(norm)
    return out


def allowed_prefixes(*roots) -> list:
    """The allowed set one child derive may open under: the fixture roots, the
    interpreter prefixes, the plugin root (`crux/`, which holds the templates
    and schemas the derive reads) and `dependency_dirs()`. Nothing else in the
    dev repo, `$HOME`, `~/.cache` or the uv cache is allowed."""
    return ([str(r) for r in roots]
            + [sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix,
               str(SCRIPTS.parent)]
            + dependency_dirs())


def _is_allowed(real: str, prefixes) -> bool:
    """True iff `real` is one of `prefixes` or lies under one. A prefix
    matches only at a path-separator boundary, so `/t/case` never admits
    `/t/case2` or `/t/case-evil`. The child bootstrap carries this same
    function's source, so the parent's tests exercise the child's rule."""
    for p in prefixes:
        if real == p or real.startswith(p.rstrip(os.sep) + os.sep):
            return True
    return False


#: The address-space bound the child derive runs under. A derive that
#: allocates past it raises `MemoryError` inside the child, and the case
#: fails there instead of exhausting the suite host's memory. Linux enforces
#: it through `RLIMIT_AS`. macOS refuses to lower that limit, so there the
#: bound is best-effort and the child reports `memory_limit: None`.
CHILD_MEMORY_BYTES = 2 * 1024 * 1024 * 1024

#: The child's audit hook: the process trap, and a record of every `open`,
#: `os.scandir` and `os.listdir` whose real path lies outside the allowed
#: set. Opens and listings are recorded apart (`outside_opens`,
#: `outside_listings`), and `_check_report` fails a case on either. The
#: derive child and the positive controls both run this same source, so a
#: control that fires proves the hook every fixture runs under.
_CHILD_HOOK = r"""
# `python -c` puts the working directory on `sys.path` as `""`, and the
# import system lists that directory for every module it imports later. The
# child imports nothing from its working directory (the driver's directory
# is added below), so the entry is dropped: a listing the hook records is
# then one the derive made, wherever the suite was started from.
sys.path[:] = [p for p in sys.path if p not in ("", ".")]
_allowed_prefixes = [os.path.realpath(p) for p in ALLOWED_PREFIXES]
_listable = {os.path.realpath(p) for p in LISTABLE_DIRS}
_events = ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn",
           "os.spawn", "os.fork", "os.forkpty")
_listing_events = ("os.scandir", "os.listdir")
fired = []
owned_forks = []
advisory_fired = []
outside_opens = []
outside_listings = []
# The driver's staleness advisory runs a read-only `git` query AFTER the
# derive and its gates have finished, under its own decision, and it fails
# open. It is not the derive path ADR-0129 clause 2 confines, so an event it
# raises is recorded apart. It is still trapped, so no git process ever runs.
_phase = ["derive"]

def _hook_real_path(path):
    # A listing of no path lists the working directory; a file descriptor
    # names no path, so it is not recorded.
    if path is None:
        path = "."
    if isinstance(path, int):
        return None
    try:
        return os.path.realpath(os.fspath(path))
    except (TypeError, ValueError, OSError):
        return None

def _hook(event, args):
    if event in _events:
        if event == "os.fork" and _phase[0] == "derive":
            swift = sys.modules.get("crux.arch.packs.swift")
            start = getattr(getattr(swift, "_BoundedParser", None), "_start", None)
            if start is not None:
                frame = sys._getframe(1)
                while frame is not None:
                    if frame.f_code is start.__code__:
                        owned_forks.append(event)
                        return
                    frame = frame.f_back
        (advisory_fired if _phase[0] == "advisory" else fired).append(event)
        raise RuntimeError("process creation trapped: " + event)
    if event == "open" or event in _listing_events:
        real = _hook_real_path(args[0])
        if real is None or _is_allowed(real, _allowed_prefixes):
            return
        if event in _listing_events and real in _listable:
            return
        (outside_opens if event == "open" else outside_listings).append(real)

sys.addaudithook(_hook)
"""

#: The child-process bootstrap: applies the memory bound, pre-imports the
#: lazily-imported grammar modules (BEFORE the audit hook installs, so their
#: own legitimate reads never trip it), then installs `_CHILD_HOOK` and runs
#: the derive.
_CHILD_PRELUDE = r"""
import sys, os, json, hashlib

memory_limit = None
try:
    import resource
    for _name in ("RLIMIT_AS", "RLIMIT_DATA"):
        _res = getattr(resource, _name, None)
        if _res is None:
            continue
        _soft, _hard = resource.getrlimit(_res)
        _new = MEMORY_BYTES if _hard == resource.RLIM_INFINITY else min(MEMORY_BYTES, _hard)
        try:
            resource.setrlimit(_res, (_new, _hard))
        except (ValueError, OSError):
            continue
        if _name == "RLIMIT_AS":
            memory_limit = resource.getrlimit(_res)[0]
except ImportError:
    pass

# Pre-import: these wheels' own reads (shared objects, __pycache__) must
# never be recorded as an out-of-scope open.
import tree_sitter, tree_sitter_swift  # noqa: F401
"""

_CHILD_DERIVE = r"""
sys.argv = ['derive-arch.py'] + ARGV
sys.path.insert(0, SCRIPTS_STR)
import importlib.util
spec = importlib.util.spec_from_file_location('drv', DRIVER_STR)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
_real_annotate = getattr(m, "_annotate", None)
if _real_annotate is not None:
    def _annotate_marked(*a, **k):
        _phase[0] = "advisory"
        try:
            return _real_annotate(*a, **k)
        finally:
            _phase[0] = "derive"
    m._annotate = _annotate_marked
code = m.main()
print(json.dumps({"exit_code": code, "fired": fired, "advisory_fired": advisory_fired,
                  "outside_opens": outside_opens, "outside_listings": outside_listings,
                  "memory_limit": memory_limit}))
"""

_CHILD_BOOTSTRAP = _CHILD_PRELUDE + _CHILD_HOOK + _CHILD_DERIVE


def _run_child(argv: list, allowed_prefixes: list, timeout: int) -> dict:
    script = (
        f"ALLOWED_PREFIXES = {allowed_prefixes!r}\n"
        f"LISTABLE_DIRS = {listable_dirs()!r}\n"
        f"ARGV = {argv!r}\n"
        f"SCRIPTS_STR = {str(SCRIPTS)!r}\n"
        f"DRIVER_STR = {str(DRIVER)!r}\n"
        f"MEMORY_BYTES = {CHILD_MEMORY_BYTES!r}\n"
        "import os\n"
    ) + inspect.getsource(_is_allowed) + _CHILD_BOOTSTRAP
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode not in (0, 1, 2) or not proc.stdout.strip():
        raise FixtureCaseError(
            f"child process did not report cleanly: rc={proc.returncode}\n"
            f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}")
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    payload["stderr"] = proc.stderr
    return payload


def _check_report(name: str, label: str, result: dict, want_exit) -> None:
    """Every derive `run_case` runs has its whole report checked: the exit
    code, no open or directory listing outside the allowed set, and no
    trapped process event. A
    trapped spawn fails the case even when the derive swallowed the trap's
    exception and exited as expected (ADR-0130 clause 17)."""
    if result.get("exit_code") != want_exit:
        raise FixtureCaseError(
            f"{name}: {label} exit {result.get('exit_code')!r} != {want_exit!r}\n"
            f"stderr={result.get('stderr')!r}")
    if result.get("outside_opens"):
        raise FixtureCaseError(
            f"{name}: {label} opened paths outside the allowed set: "
            f"{result['outside_opens']!r}")
    if result.get("outside_listings"):
        raise FixtureCaseError(
            f"{name}: {label} listed directories outside the allowed set: "
            f"{result['outside_listings']!r}")
    if result.get("fired"):
        raise FixtureCaseError(
            f"{name}: {label} attempted process creation: {result['fired']!r}")


def run_case(fixture_dir: Path, *, timeout: int = 30) -> dict:
    """Materialize `fixture_dir`, run `derive-arch.py` against it (twice, to
    check byte-identity) in a subprocess with the process trap and the
    audit hook (`_CHILD_HOOK`), then check every `expected.yml` assertion. Returns a report
    dict; raises `FixtureCaseError` on any mismatch."""
    expected = load_expected(fixture_dir)
    with tempfile.TemporaryDirectory() as d:
        root = Path(d).resolve() / "case"
        materialize(fixture_dir, root, expected)
        # The allowed set is the PLUGIN root (`SCRIPTS.parent`, i.e.
        # `crux/`), not the whole dev repo -- the derive legitimately opens its own templates and
        # schemas under `crux/`, but nothing outside it. The fixture's own
        # temp copy, the interpreter prefixes and the installed-dependency
        # directories cover the rest (`allowed_prefixes`).
        allowed = allowed_prefixes(root)
        argv = ["--repo-root", str(root)]
        result = _run_child(argv, allowed, timeout)
        want_exit = expected.get("exit_code", 0)
        _check_report(fixture_dir.name, "derive", result, want_exit)

        arch = root / "bionic" / "arch"
        rendered = {p.stem: p.read_text(encoding="utf-8")
                    for p in arch.glob("*.md")} if arch.is_dir() else {}
        for concern, needles in (expected.get("rows") or {}).items():
            body = rendered.get(concern, "")
            for needle in needles:
                if needle not in body:
                    raise FixtureCaseError(
                        f"{fixture_dir.name}: {concern} missing row {needle!r}")
        for concern, needles in (expected.get("absent") or {}).items():
            body = rendered.get(concern, "")
            for needle in needles:
                if needle in body:
                    raise FixtureCaseError(
                        f"{fixture_dir.name}: {concern} unexpectedly has {needle!r}")
        check_max_bytes(fixture_dir.name, rendered, expected.get("max_bytes"))
        for residual in expected.get("residuals") or []:
            want_count = residual.get("count")
            if want_count is not None:
                got = count_matching_residuals(rendered, residual)
                if got != want_count:
                    raise FixtureCaseError(
                        f"{fixture_dir.name}: expected {want_count} residual "
                        f"bullets matching {residual!r}, found {got}")
                continue
            if not find_residual(rendered, residual):
                raise FixtureCaseError(
                    f"{fixture_dir.name}: no residual bullet matches "
                    f"{residual!r} against the exact swift.py residual "
                    f"grammar")

        # Byte-identical second derive, and no temporary path leaked into
        # the spine.
        second_root = Path(d).resolve() / "case2"
        materialize(fixture_dir, second_root, expected)
        second = _run_child(["--repo-root", str(second_root)], allowed + [str(second_root)], timeout)
        _check_report(fixture_dir.name, "second derive", second, want_exit)
        first_norm = {c: b.replace(str(root), "<ROOT>") for c, b in rendered.items()}
        second_bodies = {p.stem: p.read_text(encoding="utf-8")
                          for p in (second_root / "bionic" / "arch").glob("*.md")} \
            if (second_root / "bionic" / "arch").is_dir() else {}
        second_norm = {c: b.replace(str(second_root), "<ROOT>") for c, b in second_bodies.items()}
        if first_norm != second_norm:
            raise FixtureCaseError(f"{fixture_dir.name}: two derives are not byte-identical")
        for body in rendered.values():
            if str(root) in body or d in body:
                raise FixtureCaseError(f"{fixture_dir.name}: a temporary path leaked into the spine")

        if expected.get("strict"):
            strict_result = _run_child(argv + ["--strict"], allowed, timeout)
            _check_report(fixture_dir.name, "--strict derive", strict_result, want_exit)
    return {"fixture": fixture_dir.name, "ok": True}


def check_max_bytes(name: str, rendered: dict, bounds: dict) -> None:
    """`max_bytes:` — each named concern file renders, and its UTF-8 size is at
    most the bound."""
    for concern, bound in (bounds or {}).items():
        if concern not in rendered:
            raise FixtureCaseError(f"{name}: {concern} did not render")
        size = len(rendered[concern].encode("utf-8"))
        if size > bound:
            raise FixtureCaseError(f"{name}: {concern} is {size} bytes, over its {bound}-byte bound")


def discover_fixtures() -> list:
    if not FIXTURES_ROOT.is_dir():
        return []
    return sorted(p for p in FIXTURES_ROOT.iterdir()
                  if p.is_dir() and (p / "expected.yml").is_file())


def _run_hooked(scratch: Path, body: str, timeout: int, listable=None) -> dict:
    """Run `body` in a child under `_CHILD_HOOK`, the hook every fixture
    derive runs under, with the allowed set a fixture rooted at `scratch`
    gets (dependency directories included, so widening that set cannot
    silently admit a canary). Returns the child's last stdout line as JSON."""
    script = (
        f"ALLOWED_PREFIXES = {allowed_prefixes(scratch.resolve())!r}\n"
        f"LISTABLE_DIRS = {(listable_dirs() if listable is None else listable)!r}\n"
        "import sys, os, json\n"
    ) + inspect.getsource(_is_allowed) + _CHILD_HOOK + body
    proc = subprocess.run([sys.executable, "-c", script],
                          capture_output=True, text=True, timeout=timeout)
    return json.loads(proc.stdout.strip().splitlines()[-1])


def positive_control_outside_open_is_caught(timeout: int = 10) -> dict:
    """The audit hook's positive control: a canary file OUTSIDE every
    allowed prefix, opened by the child, MUST be recorded. Proves the hook
    is armed rather than vacuously passing every fixture."""
    with tempfile.TemporaryDirectory() as canary_dir, tempfile.TemporaryDirectory() as scratch:
        canary = Path(canary_dir) / "canary.txt"
        canary.write_text("canary\n")
        return _run_hooked(Path(scratch), (
            f"open({str(canary)!r}).read()\n"
            "print(json.dumps({'outside_opens': outside_opens}))\n"), timeout)


def positive_control_outside_listing_is_caught(timeout: int = 10) -> dict:
    """The listing half of the hook's positive control: `os.listdir` and
    `os.scandir` of a directory OUTSIDE every allowed prefix MUST each be
    recorded, and the same two calls on the fixture root must not be."""
    with tempfile.TemporaryDirectory() as canary_dir, tempfile.TemporaryDirectory() as scratch:
        (Path(canary_dir) / "canary.txt").write_text("canary\n")
        return _run_hooked(Path(scratch), (
            f"os.listdir({canary_dir!r})\n"
            f"list(os.scandir({canary_dir!r}))\n"
            f"os.listdir({scratch!r})\n"
            f"list(os.scandir({scratch!r}))\n"
            "print(json.dumps({'outside_listings': outside_listings,\n"
            "                  'outside_opens': outside_opens}))\n"), timeout)


def positive_control_process_spawn_is_caught(timeout: int = 10) -> dict:
    """The process trap's positive control: a child that spawns a
    subprocess MUST have that event recorded (and the spawn attempt must
    raise, proving the trap -- not the OS -- stopped it)."""
    script = (
        "import sys, subprocess, json\n"
        "_events = ('subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn',\n"
        "           'os.spawn', 'os.fork', 'os.forkpty')\n"
        "fired = []\n"
        "def _hook(event, args):\n"
        "    if event in _events:\n"
        "        fired.append(event)\n"
        "        raise RuntimeError('process creation trapped: ' + event)\n"
        "sys.addaudithook(_hook)\n"
        "try:\n"
        "    subprocess.run(['true'])\n"
        "except Exception:\n"
        "    pass\n"
        "print(json.dumps({'fired': fired}))\n"
    )
    proc = subprocess.run([sys.executable, "-c", script],
                           capture_output=True, text=True, timeout=timeout)
    return json.loads(proc.stdout.strip().splitlines()[-1])
