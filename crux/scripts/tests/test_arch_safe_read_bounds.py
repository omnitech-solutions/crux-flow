"""`_safe_read_bytes`'s `max_bytes=` and `refusal=` keyword-only parameters,
which carry ADR-0130 clause 2's dedicated `project.pbxproj` bound and the
refusal kinds clause 14 names.

`refusal` is an OUT-parameter: the caller passes a list and the function
appends exactly one classified reason to it on refusal, never on success.
Its default is `None`, under which no reason is recorded, so a caller that
omits it sees the same behaviour as before the parameter existed. `max_bytes` overrides the
module's `_MAX_FILE_BYTES` bound for one call; its default is that same
constant, so an unmodified caller sees no change.

The closed reason set: `escape` (not contained under `root`), `not-regular`
(a symlink, FIFO or directory at the final component — including the
`ELOOP` `O_NOFOLLOW` raises on a symlink), `read-failed` (any other OS
error opening, stat-ing or reading), `oversize` (over `max_bytes`, checked
both from `st_size` and from the bytes actually read).

Stdlib only. Every test builds its own tempdir; the shared module fixtures
sit beside it under `TemporaryDirectory` so a symlink or FIFO created here
never touches the real checkout.
"""

from __future__ import annotations

import errno
import importlib
import os
import stat
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]      # crux/scripts
sys.path.insert(0, str(SCRIPTS))

core = importlib.import_module("crux.arch.core")


class _RepoCase(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.root = Path(self._d.name)

    def write(self, rel: str, data: bytes = b"") -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p


class DefaultBehaviourUnchangedTests(_RepoCase):
    """Existing callers, which never pass `max_bytes` or `refusal`, see no
    change: no exception, no new return shape, same bytes back."""

    def test_positional_call_reads_bytes_unchanged(self):
        p = self.write("a.txt", b"hello")
        oversize: list = []
        raw = core._safe_read_bytes(self.root, p, oversize)
        self.assertEqual(raw, b"hello")
        self.assertEqual(oversize, [])

    def test_refusal_defaults_to_none_and_records_nothing(self):
        # An escaping path still returns None with no refusal param passed —
        # proves the new parameter is opt-in, not a behaviour change.
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: os.rmdir(outside))
        oversize: list = []
        raw = core._safe_read_bytes(self.root, outside, oversize)
        self.assertIsNone(raw)


class RefusalReasonTests(_RepoCase):
    """One test per closed reason; `refusal` receives exactly one entry."""

    def test_escape_reports_escape(self):
        outside_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: os.rmdir(outside_dir))
        outside_file = outside_dir / "x.txt"
        outside_file.write_bytes(b"x")
        self.addCleanup(outside_file.unlink)
        refusal: list = []
        raw = core._safe_read_bytes(self.root, outside_file, [], refusal=refusal)
        self.assertIsNone(raw)
        self.assertEqual(refusal, ["escape"])

    def test_symlink_final_component_reports_not_regular(self):
        target = self.write("real.txt", b"data")
        link = self.root / "link.txt"
        os.symlink(target, link)
        refusal: list = []
        raw = core._safe_read_bytes(self.root, link, [], refusal=refusal)
        self.assertIsNone(raw)
        self.assertEqual(refusal, ["not-regular"])

    def test_eloop_from_o_nofollow_counts_as_not_regular(self):
        # Direct positive control on the mapping this unit locks: an OSError
        # whose errno is ELOOP at open() must classify as `not-regular`, not
        # `read-failed` — proven independent of the real symlink race above.
        target = self.write("real2.txt", b"data")
        link = self.root / "link2.txt"
        os.symlink(target, link)
        refusal: list = []
        real_open = os.open

        def _raise_eloop(path, flags, *a, **kw):
            if Path(path) == link:
                raise OSError(errno.ELOOP, "symlink")
            return real_open(path, flags, *a, **kw)

        with unittest.mock.patch.object(os, "open", side_effect=_raise_eloop):
            raw = core._safe_read_bytes(self.root, link, [], refusal=refusal)
        self.assertIsNone(raw)
        self.assertEqual(refusal, ["not-regular"])

    def test_directory_reports_not_regular(self):
        d = self.root / "adir"
        d.mkdir()
        refusal: list = []
        raw = core._safe_read_bytes(self.root, d, [], refusal=refusal)
        self.assertIsNone(raw)
        self.assertEqual(refusal, ["not-regular"])

    def test_fifo_reports_not_regular(self):
        fifo = self.root / "afifo"
        os.mkfifo(fifo)
        refusal: list = []
        raw = core._safe_read_bytes(self.root, fifo, [], refusal=refusal)
        self.assertIsNone(raw)
        self.assertEqual(refusal, ["not-regular"])

    def test_read_failed_reports_read_failed(self):
        p = self.write("b.txt", b"hello")
        refusal: list = []
        with unittest.mock.patch.object(os, "read", side_effect=OSError(errno.EIO, "boom")):
            raw = core._safe_read_bytes(self.root, p, [], refusal=refusal)
        self.assertIsNone(raw)
        self.assertEqual(refusal, ["read-failed"])

    def test_open_other_oserror_reports_read_failed(self):
        p = self.write("c.txt", b"hello")
        refusal: list = []
        real_open = os.open

        def _raise_eio(path, flags, *a, **kw):
            if Path(path) == p:
                raise OSError(errno.EIO, "boom")
            return real_open(path, flags, *a, **kw)

        with unittest.mock.patch.object(os, "open", side_effect=_raise_eio):
            raw = core._safe_read_bytes(self.root, p, [], refusal=refusal)
        self.assertIsNone(raw)
        self.assertEqual(refusal, ["read-failed"])

    def test_oversize_from_stat_reports_oversize(self):
        p = self.write("d.txt", b"x" * 20)
        refusal: list = []
        oversize: list = []
        raw = core._safe_read_bytes(self.root, p, oversize, max_bytes=10, refusal=refusal)
        self.assertIsNone(raw)
        self.assertEqual(refusal, ["oversize"])
        self.assertEqual(len(oversize), 1)


class BoundEdgeTests(_RepoCase):
    """A small `max_bytes` bound, checked at exactly the bound and one over."""

    def test_exactly_max_bytes_is_admitted(self):
        p = self.write("at_max.txt", b"x" * 10)
        refusal: list = []
        raw = core._safe_read_bytes(self.root, p, [], max_bytes=10, refusal=refusal)
        self.assertEqual(raw, b"x" * 10)
        self.assertEqual(refusal, [])

    def test_max_bytes_plus_one_is_refused_as_oversize(self):
        p = self.write("over_max.txt", b"x" * 11)
        refusal: list = []
        oversize: list = []
        raw = core._safe_read_bytes(self.root, p, oversize, max_bytes=10, refusal=refusal)
        self.assertIsNone(raw)
        self.assertEqual(refusal, ["oversize"])
        self.assertEqual(oversize, ["over_max.txt"])


def _fstat_reporting_size(size: int):
    """An `os.fstat` stand-in that reports `size` as `st_size` and leaves every
    other field as the real descriptor reports it: the snapshot of a file that
    grows between the `fstat` and the read."""
    real = os.fstat

    def fstat(fd):
        st = real(fd)
        fields = list(st)
        fields[stat.ST_SIZE] = size
        return os.stat_result(fields)
    return fstat


class DescriptorFailureTests(_RepoCase):
    """The two refusals that sit after the open: `fstat` failing on the open
    descriptor, and a file whose bytes outrun the `st_size` snapshot. Each is
    driven through `_safe_read_bytes` itself, with a control where the same
    file and bound read cleanly."""

    def test_fstat_failure_reports_read_failed(self):
        p = self.write("f.txt", b"hello")
        refusal: list = []
        oversize: list = []
        with unittest.mock.patch.object(os, "fstat", side_effect=OSError(errno.EIO, "boom")):
            raw = core._safe_read_bytes(self.root, p, oversize, refusal=refusal)
        self.assertIsNone(raw)
        self.assertEqual(refusal, ["read-failed"])
        self.assertEqual(oversize, [])

    def test_fstat_control_reads_the_same_file(self):
        p = self.write("f.txt", b"hello")
        refusal: list = []
        self.assertEqual(core._safe_read_bytes(self.root, p, [], refusal=refusal), b"hello")
        self.assertEqual(refusal, [])

    def test_bytes_read_past_a_stale_size_are_refused_as_oversize(self):
        # `st_size` says 5, under the bound of 10; the read returns 11 bytes.
        # Only the check against the bytes actually read can refuse it.
        p = self.write("grew.txt", b"x" * 11)
        refusal: list = []
        oversize: list = []
        with unittest.mock.patch.object(os, "fstat", _fstat_reporting_size(5)):
            raw = core._safe_read_bytes(self.root, p, oversize, max_bytes=10, refusal=refusal)
        self.assertIsNone(raw)
        self.assertEqual(refusal, ["oversize"])
        self.assertEqual(oversize, ["grew.txt"])

    def test_stale_size_control_at_the_bound_is_admitted(self):
        # The same stale snapshot over a file of exactly the bound reads whole.
        p = self.write("grew.txt", b"x" * 10)
        refusal: list = []
        oversize: list = []
        with unittest.mock.patch.object(os, "fstat", _fstat_reporting_size(5)):
            raw = core._safe_read_bytes(self.root, p, oversize, max_bytes=10, refusal=refusal)
        self.assertEqual(raw, b"x" * 10)
        self.assertEqual((refusal, oversize), ([], []))



def _resolve_raising(exc, name: str):
    """A `Path.resolve` stand-in that raises `exc` for any path whose final
    component is `name`, and resolves every other path as usual."""
    real = Path.resolve

    def resolve(self, *args, **kwargs):
        if self.name == name:
            raise exc
        return real(self, *args, **kwargs)
    return resolve


class ContainmentExceptionTests(_RepoCase):
    """`_contained` resolves the path, and resolution itself can fail on hostile
    content: before Python 3.13 `Path.resolve()` raises `RuntimeError` on a
    symlink loop, and it raises `OSError` on an unreadable component. Either
    one means the path is not contained, never an exception out of the derive
    (ADR-0129 clause 2, ADR-0130 clause 2)."""

    def test_runtime_error_from_resolve_is_not_contained(self):
        p = self.write("Self.swift", b"struct S {}\n")
        with unittest.mock.patch.object(Path, "resolve",
                                        _resolve_raising(RuntimeError("Symlink loop"), "Self.swift")):
            self.assertFalse(core._contained(self.root, p))

    def test_os_error_from_resolve_is_not_contained(self):
        p = self.write("Self.swift", b"struct S {}\n")
        with unittest.mock.patch.object(Path, "resolve",
                                        _resolve_raising(OSError(errno.EACCES, "denied"), "Self.swift")):
            self.assertFalse(core._contained(self.root, p))

    def test_positive_control_a_resolvable_path_is_contained(self):
        p = self.write("Self.swift", b"struct S {}\n")
        self.assertTrue(core._contained(self.root, p))

    def test_safe_read_refuses_a_symlink_loop_as_escape(self):
        p = self.write("Self.swift", b"struct S {}\n")
        refusal: list = []
        with unittest.mock.patch.object(Path, "resolve",
                                        _resolve_raising(RuntimeError("Symlink loop"), "Self.swift")):
            raw = core._safe_read_bytes(self.root, p, [], refusal=refusal)
        self.assertIsNone(raw)
        self.assertEqual(refusal, ["escape"])

    def test_note_oversize_records_nothing_when_resolve_fails(self):
        p = self.write("Self.swift", b"x")
        oversize: list = []
        with unittest.mock.patch.object(Path, "resolve",
                                        _resolve_raising(RuntimeError("Symlink loop"), "Self.swift")):
            core._note_oversize(self.root, p, oversize)
        self.assertEqual(oversize, [])

    def test_a_swift_derive_over_a_symlink_loop_renders_one_read_failure(self):
        """The entry point: a self-referential `Self.swift` renders the
        location-less `parse-error` a refused read gets, and the derive goes on."""
        try:
            import tree_sitter_swift  # noqa: F401
        except ImportError:
            self.fail("the Swift grammar is required (ADR-0129 clause 2)")
        swift = importlib.import_module("crux.arch.packs.swift")
        swift._CACHE.update(root=None, files={}, manifests={})
        (self.root / "Sources").mkdir()
        os.symlink("Self.swift", self.root / "Sources" / "Self.swift")
        self.write("Sources/Good.swift", b"public struct Good {}\n")
        with unittest.mock.patch.object(Path, "resolve",
                                        _resolve_raising(RuntimeError("Symlink loop"), "Self.swift")):
            md, sources = swift.extract_data_model(self.root, "bionic")
        self.assertIn("- `parse-error` `Sources/Self.swift` lines — — location none; "
                      "effect step read failed", md)
        self.assertIn("| `Good` | struct |", md)
        self.assertNotIn("Sources/Self.swift", sources)


if __name__ == "__main__":
    unittest.main()
