"""Tests for the pluggable arch stack-pack seam (ADR-0066, dev module 1).

Covers the seam engine only (NOT the Python extractors, a later module):
stack detection (pin, marker precedence, unregistered-pin fail-closed), the
per-concern resolution chain (override → pack → stub), crux-pack byte-identity,
and the fail-closed per-repo override guard (containment, signature, empty
sources, the opt-in gate, and override-file hashing into sources).

Stdlib only. Every test uses a fresh tempdir as the repo root — never this
repo's real tree.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]      # crux/scripts
sys.path.insert(0, str(SCRIPTS))

D = importlib.import_module("crux.arch.derive")


class _Cfg:
    """Minimal stand-in for a BionicConfig — only the attributes the seam reads."""

    def __init__(self, arch_stack=None, arch_extractors=None, source=".bionic.yml"):
        self.arch_stack = arch_stack
        self.arch_extractors = arch_extractors or {}
        self.source = source


def _tmp() -> Path:
    d = tempfile.TemporaryDirectory()
    return d, Path(d.name)


class DetectStackTests(unittest.TestCase):
    def setUp(self):
        self._d, self.root = _tmp()
        self.addCleanup(self._d.cleanup)

    def _mark_crux(self):
        (self.root / "crux" / "skills").mkdir(parents=True)
        (self.root / "crux" / "schemas").mkdir(parents=True)

    def test_pin_honored_over_detection(self):
        # Repo trips BOTH the crux and python markers; the pin decides.
        self._mark_crux()
        (self.root / "pyproject.toml").write_text("[project]\n")
        self.assertEqual(D.detect_stack(self.root, _Cfg(arch_stack="python")), "python")
        self.assertEqual(D.detect_stack(self.root, _Cfg(arch_stack="crux")), "crux")

    def test_marker_precedence_crux_before_python(self):
        # `crux` beats `python` under the registry's PAIRWISE precedence
        # (ADR-0096 clause 6), which is what this polyglot repo's shape needs.
        self._mark_crux()
        (self.root / "pyproject.toml").write_text("[project]\n")
        self.assertEqual(D.detect_stack(self.root, None), "crux")

    def test_marker_precedence_python_only(self):
        (self.root / "pyproject.toml").write_text("[project]\n")
        self.assertEqual(D.detect_stack(self.root, None), "python")

    def test_no_marker_yields_stub(self):
        self.assertEqual(D.detect_stack(self.root, None), D.STUB_PACK_NAME)

    def test_the_precedence_tuple_is_now_a_facade_over_the_registry(self):
        """ADR-0096 clause 6 merged registry and precedence into one structure.

        `STACK_PRECEDENCE` survives on the facade because the pre-split
        namespace gate binds the name, but it is REGISTRATION ORDER now, not the
        precedence — that lives in `PackEntry.beats`, pairwise. The swift pack
        is appended last, so the five earlier names keep their order; nothing
        reads the tuple as a ranking.
        """
        self.assertEqual(D.STACK_PRECEDENCE,
                         ("crux", "python", "ruby", "node", "elixir", "swift"))
        self.assertEqual(D.STACK_PRECEDENCE, D.PACK_NAMES)
        self.assertEqual(D.PACK_NAMES, tuple(e.name for e in D.PACK_REGISTRY))

    def test_unregistered_pin_fails_closed(self):
        with self.assertRaises(ValueError):
            D.detect_stack(self.root, _Cfg(arch_stack="cobol"))

    def test_registered_but_empty_pin_ok(self):
        # ruby/node/elixir are registered present-but-empty packs — a pin resolves.
        for name in ("ruby", "node", "elixir", "python"):
            self.assertEqual(D.detect_stack(self.root, _Cfg(arch_stack=name)), name)


class ResolutionChainTests(unittest.TestCase):
    def setUp(self):
        self._d, self.root = _tmp()
        self.addCleanup(self._d.cleanup)
        # A crux-shaped repo: crux/skills + crux/schemas + one JSON schema so the
        # real data-model extractor produces non-stub output.
        (self.root / "crux" / "skills").mkdir(parents=True)
        schemas = self.root / "crux" / "schemas"
        schemas.mkdir(parents=True)
        (schemas / "thing.schema.json").write_text(
            '{"title": "Thing", "type": "object", "properties": {"a": {"type": "string"}}}'
        )
        (self.root / "bionic").mkdir()

    def test_pack_probe_resolves_real_extractor(self):
        ext = D.resolve_extractor("data-model", self.root, "bionic", _Cfg(arch_stack="crux"))
        md, sources = ext(self.root, "bionic")
        self.assertNotIn("no extractor", md)
        self.assertTrue(sources, "crux data-model should hash its schema sources")

    def test_empty_pack_falls_to_stub(self):
        # python pack is present-but-empty here → data-model gets the stub.
        ext = D.resolve_extractor("data-model", self.root, "bionic", _Cfg(arch_stack="python"))
        md, sources = ext(self.root, "bionic")
        self.assertIn("no extractor", md)
        self.assertEqual(sources, {})

    def test_decision_index_is_universal(self):
        # decision-index is the stack-portable base probe — present even for a
        # non-crux pack (it reads the ADR tree, self-degrading when absent).
        ext = D.resolve_extractor("decision-index", self.root, "bionic", _Cfg(arch_stack="python"))
        md, _ = ext(self.root, "bionic")
        self.assertIn("Decision index", md)


class OverrideGuardTests(unittest.TestCase):
    def setUp(self):
        self._d, self.root = _tmp()
        self.addCleanup(self._d.cleanup)
        (self.root / "crux" / "skills").mkdir(parents=True)
        (self.root / "crux" / "schemas").mkdir(parents=True)
        (self.root / "bionic").mkdir()
        (self.root / "tools").mkdir()

    def _write_override(self, body: str, name: str = "ext.py") -> str:
        (self.root / "tools" / name).write_text(body)
        return f"tools/{name}:extract"

    @contextlib.contextmanager
    def _overrides_on(self):
        with unittest.mock.patch.dict(os.environ, {"CRUX_ARCH_ALLOW_OVERRIDES": "1"}):
            yield

    def _resolve(self, cfg):
        return D.resolve_extractor("data-model", self.root, "bionic", cfg)

    def test_opt_in_gate_off_ignores_override(self):
        spec = self._write_override(
            "def extract(root, docs_dir):\n    return '# Data model\\n\\nOVERRIDE\\n', {'x': 'h'}\n"
        )
        cfg = _Cfg(arch_stack="crux", arch_extractors={"data-model": spec})
        # No CRUX_ARCH_ALLOW_OVERRIDES in env → notice + fall through to pack.
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CRUX_ARCH_ALLOW_OVERRIDES", None)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                ext = self._resolve(cfg)
                md, _ = ext(self.root, "bionic")
        self.assertNotIn("OVERRIDE", md)               # override NOT executed
        self.assertIn("ignored", err.getvalue())       # one-line notice emitted

    def test_opt_in_on_valid_override_runs_and_hashes_file(self):
        spec = self._write_override(
            "def extract(root, docs_dir):\n"
            "    return '# Data model\\n\\nOVERRIDE OUTPUT\\n', {'seen/file.txt': 'deadbeef'}\n"
        )
        cfg = _Cfg(arch_stack="crux", arch_extractors={"data-model": spec})
        with self._overrides_on():
            ext = self._resolve(cfg)
            md, sources = ext(self.root, "bionic")
        self.assertIn("OVERRIDE OUTPUT", md)
        self.assertIn("seen/file.txt", sources)
        # The override module file itself is hashed into sources (drift-gates the code).
        self.assertIn("tools/ext.py", sources)

    def test_containment_reject_falls_through_with_warning(self):
        cfg = _Cfg(arch_stack="crux", arch_extractors={"data-model": "../evil.py:extract"})
        with self._overrides_on():
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                ext = self._resolve(cfg)
                md, _ = ext(self.root, "bionic")
        # Fell through to the crux pack's real extractor (crux dirs exist but no
        # schema → stub marker) — the point is the override did NOT run.
        self.assertNotIn("OVERRIDE", md)
        self.assertIn("failed", err.getvalue())

    def test_absolute_path_rejected(self):
        cfg = _Cfg(arch_stack="crux", arch_extractors={"data-model": "/etc/passwd:extract"})
        with self._overrides_on():
            with contextlib.redirect_stderr(io.StringIO()) as err:
                ext = self._resolve(cfg)
                ext(self.root, "bionic")
        self.assertIn("failed", err.getvalue())

    def test_bad_signature_rejected(self):
        spec = self._write_override("def extract(root):\n    return '# x\\n', {'a': 'b'}\n")
        cfg = _Cfg(arch_stack="crux", arch_extractors={"data-model": spec})
        with self._overrides_on():
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                ext = self._resolve(cfg)
                md, _ = ext(self.root, "bionic")
        self.assertIn("no extractor", md)              # fell through to the stub
        self.assertIn("positional", err.getvalue())

    def test_empty_sources_nonstub_rejected(self):
        spec = self._write_override(
            "def extract(root, docs_dir):\n    return '# Data model\\n\\nREAL CONTENT\\n', {}\n"
        )
        cfg = _Cfg(arch_stack="crux", arch_extractors={"data-model": spec})
        with self._overrides_on():
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                ext = self._resolve(cfg)
                md, _ = ext(self.root, "bionic")
        self.assertNotIn("REAL CONTENT", md)
        self.assertIn("empty sources", err.getvalue())

    def test_stub_markdown_empty_sources_allowed(self):
        # An override may legitimately return the empty-but-valid stub marker with
        # no sources — that is drift-gate-able (nothing to hash, nothing changes).
        marker = D.NO_EXTRACTOR.strip()
        spec = self._write_override(
            f"def extract(root, docs_dir):\n    return '# Data model\\n\\n{marker}\\n', {{}}\n"
        )
        cfg = _Cfg(arch_stack="crux", arch_extractors={"data-model": spec})
        with self._overrides_on():
            ext = self._resolve(cfg)
            md, sources = ext(self.root, "bionic")
        self.assertIn("no extractor", md)
        # The override file is still hashed so a change to it drifts the spine.
        self.assertIn("tools/ext.py", sources)

    def test_raises_falls_through_with_warning(self):
        spec = self._write_override(
            "def extract(root, docs_dir):\n    raise RuntimeError('boom')\n"
        )
        cfg = _Cfg(arch_stack="crux", arch_extractors={"data-model": spec})
        with self._overrides_on():
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                ext = self._resolve(cfg)
                md, _ = ext(self.root, "bionic")
        self.assertIn("raised", err.getvalue())
        self.assertNotIn("OVERRIDE", md)


class PinConfigHashingTests(unittest.TestCase):
    """ADR-0066 point 9b: when arch_stack pins a pack, the pin's config file is
    hashed into the spine's manifest sources, so changing the pin (or the config)
    drifts even a byte-identical spine. Exercised via _build with a real cfg."""

    def setUp(self):
        self._d, self.root = _tmp()
        self.addCleanup(self._d.cleanup)
        (self.root / "crux" / "skills").mkdir(parents=True)
        (self.root / "crux" / "schemas").mkdir(parents=True)
        (self.root / "bionic").mkdir()
        self.cfg_file = self.root / ".bionic.yml"
        self.cfg_file.write_text('config_version: "1"\ndocs_dir: bionic\narch_stack: crux\n')

    def _sources(self, cfg):
        import json
        tree = D._build(self.root, "bionic", cfg=cfg)
        return json.loads(tree["_meta/manifest.json"])["sources"]

    def test_pin_config_file_hashed_into_sources(self):
        srcs = self._sources(_Cfg(arch_stack="crux", source=".bionic.yml"))
        self.assertIn(".bionic.yml", srcs)

    def test_pin_config_change_drifts(self):
        before = self._sources(_Cfg(arch_stack="crux", source=".bionic.yml"))[".bionic.yml"]
        self.cfg_file.write_text('config_version: "1"\ndocs_dir: bionic\narch_stack: crux\n# changed\n')
        after = self._sources(_Cfg(arch_stack="crux", source=".bionic.yml"))[".bionic.yml"]
        self.assertNotEqual(before, after)

    def test_no_pin_does_not_force_config_source(self):
        # Without an arch_stack pin, the point-9b branch is skipped: the config
        # file is not force-added to sources by the pin mechanism.
        srcs = self._sources(_Cfg(arch_stack=None, source=".bionic.yml"))
        self.assertNotIn(".bionic.yml", srcs)


class SwiftRegistrationTests(unittest.TestCase):
    """The swift pack's registration (ADR-0129 clauses 2 and 3)."""

    def setUp(self):
        self.core = importlib.import_module("crux.arch.core")

    def test_swift_is_registered_last_with_an_empty_precedence_set(self):
        self.assertEqual(self.core.PACK_NAMES[-1], "swift")
        entry = {e.name: e for e in self.core.PACK_REGISTRY}["swift"]
        self.assertEqual(entry.beats, frozenset())

    def test_no_registered_pack_names_swift_in_its_precedence_set(self):
        for entry in self.core.PACK_REGISTRY:
            with self.subTest(pack=entry.name):
                self.assertNotIn("swift", entry.beats)

    def test_every_swift_probe_declares_the_parser_class(self):
        kinds = self.core.probe_kinds("swift")
        concerns = self.core.probe_concerns("swift")
        own = {n: k for n, k in kinds.items() if concerns[n] != "decision-index"}
        self.assertEqual(sorted(concerns[n] for n in own),
                         ["api-surface", "data-model", "module-graph"])
        for name, kind in own.items():
            with self.subTest(probe=name):
                self.assertEqual(kind, "parser")

    def test_swift_concerns_name_the_pinned_grammar(self):
        declared = self.core.input_classes("swift")
        # module-graph also names `yaml`: it checks an XcodeGen `project.yml`
        # with PyYAML, scanning the event stream before a safe load (ADR-0130
        # clause 13), so its declared set is the grammar pair plus `yaml`.
        expected = {
            "data-model": ("tree_sitter", "tree_sitter_swift"),
            "api-surface": ("tree_sitter", "tree_sitter_swift"),
            "module-graph": ("tree_sitter", "tree_sitter_swift", "yaml"),
        }
        for concern, parser in expected.items():
            with self.subTest(concern=concern):
                self.assertEqual(declared[concern].parser, parser)
                self.assertEqual(declared[concern].kind, "parser")
        self.assertEqual(declared["decision-index"].parser, ("yaml",))

    def test_the_regex_roster_names_no_swift_probe(self):
        rows = self.core.load_regex_roster()
        named = [r for r in rows
                 if r.get("pack") == "swift" or "packs.swift:" in str(r.get("probe", ""))]
        self.assertEqual(named, [])
        self.assertTrue(rows, "the roster loaded no rows, so the check read nothing")


if __name__ == "__main__":
    unittest.main(verbosity=2)
