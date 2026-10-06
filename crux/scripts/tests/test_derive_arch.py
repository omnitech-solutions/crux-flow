"""Tests for the arch derive engine (ADR-0060, SP-1 + SP-2).

Each test names the ADR-0060 Acceptance Criterion it locks. Content assertions
use the in-memory `_build` (no writes); the write/gate path uses a scratch copy
so the repo's own bionic/arch/ is never clobbered by the suite.
"""

import hashlib
import shutil
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]      # crux/scripts
REPO = SCRIPTS.parents[1]                            # repo root
sys.path.insert(0, str(SCRIPTS))

import importlib  # noqa: E402
D = importlib.import_module("crux.arch.derive")  # the module, not the re-exported fn

try:  # package-relative when run as a module, flat when run by discovery
    from ._dev_surface import IS_STAGED_ARTIFACT, require_dev_surface
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _dev_surface import IS_STAGED_ARTIFACT, require_dev_surface

# ADR-0078 clause 1 — THE PINNED SPINE HASH IS GONE, AND SO IS ITS TEST.
#
# `COMMITTED_SPINE_HASH` held a copy of the spine-hash string stamped into this
# repo's committed `bionic/arch/overview.md`, and `test_committed_spine_hash_constant`
# asserted the two still matched. That check read no source: it compared one
# committed file against a second hand-maintained copy of the same string, so it
# reported green during real drift while the regeneration comparison reported
# drift. It failed only when a legitimate re-derive forgot the second manual
# edit — which cost a must-fix and a review loop one cycle earlier. Clause 1's
# requirement is general: no check whose subject is this repository's committed
# arch tree may reach its verdict by comparing a derived value against a literal
# maintained beside it.
#
# ADR-0066 point 7 MANDATED that assertion, so clause 2 replaces it rather than
# dropping it, and requires that no property point 7 established is left without
# an owner. The two halves and their new owners, named here at the site of the
# deletion so a reader of this file does not have to reconstruct them:
#
#   (a) "the pack refactor left output unchanged" — stays proved in THIS
#       portable suite, by `test_crux_pack_byte_identical_to_direct_extractors`
#       (resolving each concern through the pack seam is byte-identical to
#       calling its extractor directly) and by
#       `test_ac2_ac8_write_then_clean_and_fixed_point` / `test_ac3_gate_fires_on_tamper`
#       (a derive of a tree the check itself builds, whose comparison is then
#       clean, and which fires on a tamper). Both take the DERIVER as subject
#       and build the tree they examine, so both run intact in the distributed
#       artifact.
#
#   (b) "the committed spine did not move without a re-derive" — passes to the
#       regeneration gate, which reads current sources instead of comparing two
#       committed copies of one string. It runs in two venues: the pull-request
#       job `.github/workflows/check-arch-drift.yml`, and the release path's
#       purpose-built `derive-arch.py --dry-run` step in `sync.sh`. The
#       replacement is stronger in content (it fails on staleness the constant
#       could not see) and narrower in timing (it no longer runs on every local
#       test run) — a deliberate trade, recorded in ADR-0078's Consequences.
#
# Do not reintroduce a pinned copy here. If a future check needs the committed
# spine's hash, it must derive it.

# What a scratch copy of the repo leaves behind. One definition for all four
# copy sites so the lists cannot diverge. Two entries carry the cost. `.venv` is
# ~148MB and `.cache` — the pinned arch corpus's ten clones under
# `crux/scripts/tests/arch-corpus/` — is ~79MB, and several tests copy per
# method. The rest are regenerable caches with no bearing on what the derive
# reads.
#
# `.cache` is matched by NAME, which `shutil.ignore_patterns` applies at every
# directory level. That is wider than the one corpus directory, and deliberately
# so: any `.cache` in this repo is regenerable and none of it is a derive input.
#
# `.crux-arch-scratch` and `.crux-selftest-scratch` are excluded for a different
# reason: they are not cost, they are a race. The corpus's `source: self` entry
# derives this repo in place, creating and removing `.crux-arch-scratch` AT THE
# REPO ROOT for about 1.4 seconds; the vacuous-gate-guard selftest does the same
# with `.crux-selftest-scratch` while it seeds and removes a control violation
# for its citation-linter gate. Within one pytest process the copy and either
# scratch-directory user are sequential and cannot collide, but a second runner
# mid-scratch-write makes `copytree` stat a directory that is gone by the time
# it descends — `shutil.Error: ... No such file or directory:
# '.../.crux-arch-scratch'`. Ignoring the names means copytree never descends and
# the window closes. Both directories are gitignored scratch and never a derive
# input.
_SCRATCH_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", "*.pyc", "node_modules",
    ".venv", ".cache", "logs", ".pytest_cache", ".ruff_cache",
    ".crux-arch-scratch", ".crux-selftest-scratch",
)


class ScratchNameEnrollmentTests(unittest.TestCase):
    """The two scratch directory names, pinned at every half that must agree.

    Each name is written in three places that only work together: the writer
    that creates the directory, `_SCRATCH_IGNORE` above (so a concurrent
    runner's `copytree` never descends into a directory mid-write), and
    `.gitignore` (so a crash mid-run leaves no untracked debris). Deleting any
    one half silently reopens the live-tree fixture race with this suite green,
    which is what happened when `.crux-selftest-scratch` was introduced. The
    writers are `crux/arch/derive.py` for `.crux-arch-scratch` and the
    `vacuous-gate-guard` selftest for `.crux-selftest-scratch`.
    """

    NAMES = (".crux-arch-scratch", ".crux-selftest-scratch")

    #: A dev-only file: `.claude/` never crosses the sync boundary.
    SELFTEST_WRITER = (
        REPO / ".claude" / "skills" / "vacuous-gate-guard" / "selftest"
        / "build_rows.py")

    def test_the_copy_filter_ignores_both_names(self):
        # `shutil.ignore_patterns` returns a callable, so membership is asked
        # of it the way `copytree` asks: hand it a directory listing.
        listing = [*self.NAMES, "crux", "bionic", "tools"]
        ignored = _SCRATCH_IGNORE(str(REPO), listing)
        for name in self.NAMES:
            with self.subTest(name=name):
                self.assertIn(name, ignored)
        # PAIRED POSITIVE CONTROL: the filter is selective, not blanket — a
        # filter that ignored everything would satisfy the loop above and make
        # every scratch-copy test copy an empty tree.
        for kept in ("crux", "bionic", "tools"):
            with self.subTest(kept=kept):
                self.assertNotIn(kept, ignored)

    def test_gitignore_carries_both_names(self):
        gitignore = REPO / ".gitignore"
        if IS_STAGED_ARTIFACT:
            self.skipTest(
                "the staged artifact's root .gitignore is public/.gitignore, a "
                "different file (ADR-0036 boundary)")
        require_dev_surface(self, gitignore, ".gitignore")
        entries = {line.strip() for line
                   in gitignore.read_text(encoding="utf-8").splitlines()}
        for name in self.NAMES:
            with self.subTest(name=name):
                self.assertIn(name + "/", entries)
        # PAIRED POSITIVE CONTROL: the parse produced real entries rather than
        # an accidentally-permissive set.
        self.assertNotIn("crux/", entries)

    def test_the_selftest_writer_still_uses_the_enrolled_name(self):
        """The rename that motivated the two halves above."""
        require_dev_surface(self, self.SELFTEST_WRITER,
                            ".claude/skills/vacuous-gate-guard/selftest/build_rows.py")
        text = self.SELFTEST_WRITER.read_text(encoding="utf-8")
        self.assertIn('SEED_DIR = ROOT / ".crux-selftest-scratch"', text)
        # The pre-rename location seeded a control file INSIDE the live docs
        # tree, where the arch derive and the audit both read. It must not
        # come back.
        self.assertNotIn("bionic/.seed-control", text)


def _copy_repo(scratch: Path) -> None:
    """Copy the repo to *scratch* for a write-path test.

    `symlinks=True` copies links as links. The default follows them, which for a
    venv means reading files outside the repo — a scratch copy of this repo
    should not reach past the repo.
    """
    shutil.copytree(REPO, scratch, ignore=_SCRATCH_IGNORE, symlinks=True)


# ADR-0078 clause 3, postcondition — "the arch tests pass against the distributed
# artifact, with no skip and no failure from the absent tree."
#
# Two assertions in this file used to ask the AMBIENT tree whether every crux
# concern populates and grades `high`. In this dev checkout they do. In the staged
# public artifact, which ships `crux/` WITHOUT the `bionic/` dogfood tree,
# `decision-index` legitimately stubs — so the assertion was asking the wrong
# world a question it cannot answer, and both failed there. Neither is new; both
# reproduce on a pristine HEAD worktree.
#
# A `skipUnless` would silence them, and that is exactly the inertness the clause
# forbids: a portable suite must not assert one repository's contents and then
# skip where that repository is absent. So the fixture below BUILDS the world the
# assertion needs. Its subject becomes the deriver — does the crux pack populate
# and grade every concern when every concern's inputs are present — which is true
# in both worlds and needs no skip.
#
# The four inputs are the four crux extractors' minimum: a JSON schema
# (data-model), a SKILL.md (api-surface, and the pack-detection marker alongside
# the schemas dir), a Python module under `crux/scripts/crux/` (module-graph),
# and one Accepted ADR (decision-index). `test_populated_fixture_is_non_vacuous`
# proves the fixture discriminates rather than passing by construction.
def _populated_fixture(root: Path, *, with_adrs: bool = True) -> Path:
    """Write the minimum tree in which every crux-pack concern populates.

    `with_adrs=False` withholds the ADR directory, which is how the fixture's
    own teeth are proved: exactly one concern must then stub.
    """
    (root / "crux" / "schemas").mkdir(parents=True)
    (root / "crux" / "schemas" / "widget.schema.json").write_text(
        '{\n  "title": "Widget",\n  "type": "object",\n'
        '  "properties": {"id": {"type": "string"}},\n  "required": ["id"]\n}\n',
        encoding="utf-8",
    )
    skill = root / "crux" / "skills" / "do-a-thing"
    skill.mkdir(parents=True)
    skill.joinpath("SKILL.md").write_text(
        "---\nname: do-a-thing\ndescription: Does a thing.\n"
        "metadata:\n  owner: fixture\n---\n\n# do-a-thing\n",
        encoding="utf-8",
    )
    pkg = root / "crux" / "scripts" / "crux"
    (pkg / "core").mkdir(parents=True)
    pkg.joinpath("__init__.py").write_text("from crux.core import thing\n", encoding="utf-8")
    pkg.joinpath("core", "__init__.py").write_text("", encoding="utf-8")
    pkg.joinpath("core", "thing.py").write_text("VALUE = 1\n", encoding="utf-8")
    docs = root / "bionic"
    docs.mkdir(parents=True, exist_ok=True)
    docs.joinpath("manifest.yml").write_text(
        'schema_version: "5"\nadr:\n  next_number: 2\n', encoding="utf-8"
    )
    if with_adrs:
        adrs = docs / "adrs"
        adrs.mkdir()
        adrs.joinpath("ADR-0001-do-a-thing.md").write_text(
            "---\nid: ADR-0001\ntitle: \"Do a thing\"\nstatus: Accepted\n"
            "date: 2026-01-01\ntags: [fixture]\n---\n\n# ADR-0001 — Do a thing\n",
            encoding="utf-8",
        )
    return root


class ArchDeriveTests(unittest.TestCase):
    def setUp(self):
        self.tree = D._build(REPO, "bionic")

    # ADR-0066 point 7 — the crux pack wraps the existing extractors UNCHANGED:
    # resolving each spine concern through the seam is byte-identical to calling
    # the four extractor functions directly. Environment-independent (both paths
    # share this process's frontmatter parser), so it proves the refactor is a
    # no-op on output regardless of PyYAML availability.
    def test_crux_pack_byte_identical_to_direct_extractors(self):
        # Seam-vs-direct identity is meaningful only where the inputs exist. In the
        # staged crux-only artifact there is no ADR index, so the core renders a
        # named-precondition stub while the direct extractor's own degrade path
        # renders the legacy marker — a divergence on a surface the stage never
        # ships. Guard rather than compare against a missing dev surface.
        try:
            from ._dev_surface import require_dev_surface
        except ImportError:
            from _dev_surface import require_dev_surface
        require_dev_surface(self, REPO / "bionic" / "adrs" / "index.md", "bionic/adrs/index.md")
        direct = {
            "data-model.md": D.extract_data_model(REPO, "bionic")[0],
            "api-surface.md": D.extract_api_surface(REPO, "bionic")[0],
            "module-graph.md": D.extract_module_graph(REPO, "bionic")[0],
            "decision-index.md": D.extract_decision_index(REPO, "bionic", "complete")[0],
        }
        for fname in D.SPINE_FILES:
            self.assertEqual(self.tree[fname], direct[fname], f"seam diverged from direct call for {fname}")

    # ADR-0066 — the seam auto-detects the crux pack for this repo (crux/skills +
    # crux/schemas markers), and the overview records the resolved pack name.
    def test_overview_records_stack_pack(self):
        self.assertIn("Stack pack: `crux`", self.tree["overview.md"])

    # AC-1 — deriving produces the four spine files + overview + index + manifest.
    def test_ac1_produces_all_files(self):
        for f in D.SPINE_FILES + ["overview.md", "index.md", "_meta/manifest.json"]:
            self.assertIn(f, self.tree, f"missing {f}")
            self.assertTrue(self.tree[f].endswith("\n"), f"{f} lacks trailing newline")

    # AC-2 — a second build with no source change is byte-identical (determinism).
    def test_ac2_deterministic_rebuild(self):
        again = D._build(REPO, "bionic")
        self.assertEqual(self.tree, again, "second build differs — non-deterministic")

    # AC-2/AC-4 — the byte-stable manifest carries no timestamp key or value.
    def test_ac4_manifest_has_no_timestamp(self):
        import json
        import re
        doc = json.loads(self.tree["_meta/manifest.json"])
        # A CLOSED key set on purpose: the manifest is the provenance ledger and
        # a key added to it silently is a key nobody decided on. `declared_sources`
        # is ADR-0096 clause 7's addition — each concern's declared source set,
        # recorded as GLOBS beside the hashes of the artifacts actually consumed.
        # Globs and not resolved paths, so the key can sit inside the drift
        # gate's compared region without flapping when a matching file appears.
        self.assertEqual(set(doc),
                         {"spine_hash", "tool_pins", "sources", "declared_sources"})
        # No key anywhere is a timestamp key; no value looks like an ISO datetime.
        iso = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")

        def _walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    self.assertNotIn(k.lower(), {"generated_at", "timestamp", "created_at", "date"})
                    _walk(v)
            elif isinstance(o, str):
                self.assertNotRegex(o, iso)
        _walk(doc)

    # AC-3 — the spine hash covers EXACTLY the four spine files, in fixed order,
    # excluding index.md and overview.md (which carry the hash).
    def test_ac3_spine_hash_domain(self):
        expect = hashlib.sha256(
            b"".join(self.tree[f].encode("utf-8") for f in D.SPINE_FILES)
        ).hexdigest()
        self.assertIn(f"sha256:{expect}", self.tree["overview.md"])
        self.assertIn(f"sha256:{expect}", self.tree["index.md"])
        # index/overview are NOT part of the hashed domain:
        self.assertEqual(len(D.SPINE_FILES), 4)

    # Manifest-counter fix: a counter bump (adr/promptbook next_number) must NOT change
    # the data-model output or its provenance hash — the arch hashes only the top-level
    # (column-0) key names, and next_number is indented under adr:/promptbook:. Self-contained
    # fixture so it is deterministic regardless of the ambient tree (the staged public artifact
    # carries only crux/, no bionic/manifest.yml, so a copy-the-repo assertion false-fails there).
    def test_manifest_counter_bump_no_drift(self):
        import json
        import re
        root = Path(self.enterContext(_tmpdir()))
        # A JSON schema is required to pass extract_data_model's early return (no schemas
        # → degraded "no extractor" path that never reads the manifest).
        schemas = root / "crux" / "schemas"
        schemas.mkdir(parents=True)
        (schemas / "thing.schema.json").write_text(json.dumps(
            {"title": "Thing", "type": "object",
             "properties": {"a": {"type": "string"}}, "required": ["a"]}))
        (root / "bionic").mkdir()
        man = root / "bionic" / "manifest.yml"
        man.write_text(
            'schema_version: "5"\n'
            'concerns_enabled:\n  - adrs\n'
            'adr:\n  next_number: 5\n'
            'promptbook:\n  next_number: 12\n'
        )
        before_md, before_src = D.extract_data_model(root, "bionic")
        man.write_text(re.sub(r"(next_number:\s*)(\d+)",
                              lambda m: f"{m.group(1)}{int(m.group(2)) + 100}",
                              man.read_text()))
        after_md, after_src = D.extract_data_model(root, "bionic")
        self.assertEqual(before_md, after_md, "counter bump changed data-model output")
        rel = str(man.relative_to(root))
        self.assertIn(rel, before_src, "manifest.yml missing from data-model sources")
        self.assertEqual(before_src[rel], after_src[rel], "counter bump changed provenance hash")

    # AC-5 — an unsupported stack yields an empty-but-valid file with the marker.
    def test_ac5_graceful_degradation(self):
        empty = Path(self.enterContext(_tmpdir()))
        (empty / "bionic").mkdir()
        md, sources = D.extract_data_model(empty, "bionic")
        self.assertIn("no extractor", md)
        self.assertTrue(md.endswith("\n"))
        self.assertEqual(sources, {})

    # AC-2/AC-8 — write the tree to a scratch copy, then dry_run is clean; a second
    # derive is byte-identical (fixed point on the tree's own output).
    def test_ac2_ac8_write_then_clean_and_fixed_point(self):
        scratch = Path(self.enterContext(_tmpdir())) / "repo"
        _copy_repo(scratch)
        D.derive(scratch, "bionic")
        self.assertEqual(D.dry_run(scratch, "bionic"), [], "dry_run dirty after derive")
        before = _tree_hash(scratch / "bionic" / "arch")
        D.derive(scratch, "bionic")                     # fixed point
        self.assertEqual(_tree_hash(scratch / "bionic" / "arch"), before)

    # AC-3 — tampering a spine file makes dry_run report drift (the gate fires).
    def test_ac3_gate_fires_on_tamper(self):
        scratch = Path(self.enterContext(_tmpdir())) / "repo"
        _copy_repo(scratch)
        D.derive(scratch, "bionic")
        tampered = scratch / "bionic" / "arch" / "data-model.md"
        tampered.write_text(tampered.read_text() + "\n<!-- tamper -->\n")
        drift = D.dry_run(scratch, "bionic")
        self.assertIn("bionic/arch/data-model.md", drift)

    # AC-6 — decision-index references ADRs footnote-only (never inline). Staged-safe:
    # in a tree with no ADRs (e.g. the packaged plugin artifact) the derive yields the
    # empty-but-valid file (AC-5); the no-inline-ADR invariant holds either way.
    def test_ac6_decision_index_footnote_only(self):
        import re
        di = self.tree["decision-index.md"]
        body = di.split("[^d1]:")[0]           # the table, before any footnote defs
        self.assertNotRegex(body, r"ADR-\d{4}", "inline ADR number in decision-index body")
        if re.search(r"^\| \d+ \|", di, re.MULTILINE):   # decisions present
            self.assertIn("[^d1]:", di, "decisions present but no footnote definitions")
        else:                                            # no ADRs -> a named-precondition stub
            # ADR-0096 clause 4: the stub names its precondition; "no extractor for
            # this stack" is reserved for an unsupported stack, and an ADR-less tree
            # under a registered pack is `precondition_missing`.
            self.assertIn("_stub: precondition_missing", di)

    # Fix B (PB-0069) — coverage.json determinism: a second build with no source
    # change is byte-identical (parallels test_ac2_deterministic_rebuild).
    def test_coverage_deterministic_rebuild(self):
        again = D._build(REPO, "bionic")
        self.assertEqual(
            self.tree["_meta/coverage.json"], again["_meta/coverage.json"],
            "second build's coverage.json differs — non-deterministic",
        )

    # Fix B (PB-0069) — no timestamps anywhere in coverage.json (parallels
    # test_ac4_manifest_has_no_timestamp).
    def test_coverage_has_no_timestamp(self):
        import json
        import re
        doc = json.loads(self.tree["_meta/coverage.json"])
        iso = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")

        def _walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    self.assertNotIn(k.lower(), {"generated_at", "timestamp", "created_at", "date"})
                    _walk(v)
            elif isinstance(o, list):
                for v in o:
                    _walk(v)
            elif isinstance(o, str):
                self.assertNotRegex(o, iso)
        _walk(doc)

    # Fix B (PB-0069) — shape/classification: `concerns` is a LIST in SPINE_FILES
    # order (sort_keys=True must not alphabetize it), each record carries exactly
    # its verdict's documented fields, and every concern whose inputs are present
    # populates with repo-relative source paths.
    #
    # ADR-0096 clauses 2/3 changed which fields those are. A record now carries
    # `concern`, `extractor`, `inputs_found` plus EITHER the populated pair
    # (`n_sources`, `n_entities`) OR the stubbed triple (`stub_reason`,
    # `expected`, `found`) — never both, so a reader cannot mistake a defaulted
    # zero for a measurement. The two exact set compares below are what keeps
    # the retired `status` / `reason` / `inputs_missing` fields from coming back.
    #
    # ADR-0078 clause 3 split the two halves by which world can answer them. The
    # SHAPE half runs on the ambient tree, where it holds in a dev checkout and in
    # the staged artifact alike — a stubbed concern has the same nine fields as a
    # populated one. The CLASSIFICATION half runs on `_populated_fixture`, a tree
    # this check builds with every concern's inputs present; asked of the ambient
    # tree it failed in the staged artifact, where `bionic/` is absent and
    # `decision-index` correctly stubs.
    def test_coverage_shape_and_classification(self):
        import json
        for label, tree in (("ambient", self.tree),
                            ("fixture", D._build(_populated_fixture(
                                Path(self.enterContext(_tmpdir()))), "bionic"))):
            doc = json.loads(tree["_meta/coverage.json"])
            self.assertEqual(set(doc), {"_note", "concerns"})
            concerns = doc["concerns"]
            self.assertIsInstance(concerns, list)
            self.assertEqual([c["concern"] for c in concerns],
                             [f[:-3] for f in D.SPINE_FILES], label)
            for rec in concerns:
                where = f"{label}/{rec['concern']}"
                base = {"concern", "extractor", "inputs_found", "verdict"}
                if rec["verdict"] == "populated":
                    self.assertEqual(set(rec), base | {"n_sources", "n_entities"}, where)
                else:
                    self.assertEqual(set(rec), base | {"stub_reason", "expected", "found"},
                                     where)
                for src in rec["inputs_found"]:
                    self.assertFalse(src.startswith("/"), f"absolute path leaked: {src}")
                    self.assertNotIn(str(Path.home()), src, f"home dir leaked: {src}")
                if label != "fixture":
                    continue
                self.assertEqual(rec["verdict"], "populated", f"{where} unexpectedly stubbed")
                self.assertEqual(rec["extractor"], "crux", where)

    # ADR-0078 clause 3 — the fixture DISCRIMINATES. A fixture that populated
    # every concern by construction, regardless of its inputs, would make the
    # classification assertion above pass while proving nothing. Withholding
    # exactly one concern's inputs must stub exactly that concern and leave the
    # rest populated. Without this, that assertion is unfalsifiable.
    def test_populated_fixture_is_non_vacuous(self):
        import json
        bare = _populated_fixture(Path(self.enterContext(_tmpdir())), with_adrs=False)
        doc = json.loads(D._build(bare, "bionic")["_meta/coverage.json"])
        by_concern = {r["concern"]: r for r in doc["concerns"]}
        self.assertEqual(by_concern["decision-index"]["verdict"], "stubbed",
                         "withholding the ADRs left decision-index populated")
        for concern in ("data-model", "api-surface", "module-graph"):
            self.assertEqual(by_concern[concern]["verdict"], "populated",
                             f"{concern} stubbed on a tree carrying its inputs")

    # ADR-0096 clause 3 — the stub vocabulary is closed and injection-safe: a
    # record's `stub_reason` is one of the six enum members and its `expected` /
    # `found` sentences are authored constants plus integers, never repo
    # content. This replaces the PB-0069 `reason` template test, whose whole
    # claim was that a string restating two other fields restated them exactly.
    def test_stub_vocabulary_is_closed(self):
        import json
        empty = Path(self.enterContext(_tmpdir()))
        (empty / "bionic").mkdir()
        for tree in (self.tree, D._build(empty, "bionic")):
            doc = json.loads(tree["_meta/coverage.json"])
            for rec in doc["concerns"]:
                if rec["verdict"] != "stubbed":
                    continue
                self.assertIn(rec["stub_reason"], {r.value for r in D.StubReason})
                for field in ("expected", "found"):
                    self.assertTrue(rec[field], f"{rec['concern']}: empty {field}")
                    self.assertNotIn(str(Path.home()), rec[field])
                    self.assertNotIn(str(empty), rec[field])

    # Fix B (PB-0069) — an unsupported stack (stub pack, reuses the AC-5 fixture
    # pattern) reports every concern "stubbed" with extractor "stub".
    def test_coverage_stubbed_classification(self):
        import json
        empty = Path(self.enterContext(_tmpdir()))
        (empty / "bionic").mkdir()
        tree = D._build(empty, "bionic")
        doc = json.loads(tree["_meta/coverage.json"])
        for rec in doc["concerns"]:
            self.assertEqual(rec["verdict"], "stubbed", f"{rec['concern']} unexpectedly populated")
            self.assertEqual(rec["extractor"], "stub")
            # The generic stub pack's extractors always return empty sources,
            # so a stubbed concern here has empty inputs_found by construction
            # (unlike some in-pack NO_EXTRACTOR paths elsewhere that can carry
            # partial sources) — asserted directly rather than relaxed to a
            # path-shape check.
            self.assertEqual(rec["inputs_found"], [])

    # Fix B (PB-0069) — coverage.json rides the existing generic per-tree-key
    # drift gate: tampering it on disk makes dry_run report it (parallels
    # test_ac3_gate_fires_on_tamper).
    def test_coverage_drift_gate_fires_on_tamper(self):
        scratch = Path(self.enterContext(_tmpdir())) / "repo"
        _copy_repo(scratch)
        D.derive(scratch, "bionic")
        self.assertEqual(D.dry_run(scratch, "bionic"), [], "dry_run dirty right after derive")
        coverage = scratch / "bionic" / "arch" / "_meta" / "coverage.json"
        coverage.write_text(coverage.read_text() + "\n")
        drift = D.dry_run(scratch, "bionic")
        self.assertIn("bionic/arch/_meta/coverage.json", drift)


class ArchCoverageSignalTests(unittest.TestCase):
    """PB-0073 W3 — coverage.json as the `extractor went blind` signal.

    SCOPE OF THE CLAIM, stated so nobody over-reads it (constraint C3): these
    assertions catch a concern regressing populated -> stubbed, and a `status`
    token outside the closed vocabulary. They do NOT catch a partial omission
    that remains marked `populated` — an extractor that finds three of its ten
    inputs still reports `populated` with a non-empty `inputs_found` and passes
    here. The byte comparison of `coverage.json` in `dry_run` remains the PRIMARY
    control and is untouched by this cycle's narrowing (only
    `manifest.json["sources"]` was excluded); this is defense in depth behind it.

    Deliberately NOT a duplicate of the three existing coverage tests:
    `test_coverage_shape_and_classification` pins `status == "populated"` on a
    fixture tree built with every concern's inputs present (ADR-0078 clause 3
    moved it off the ambient tree), and `test_coverage_reason_vocabulary_closed`
    derives `reason` FROM `status`, so a status token neither expects would sail
    through both self-consistently. This class is the only one that asks the
    AMBIENT tree — whatever world it is running in — whether `status` stays inside
    the closed vocabulary and agrees with `inputs_found`.
    """

    def test_status_vocabulary_closed_and_agrees_with_inputs(self):
        import json
        blinded = Path(self.enterContext(_tmpdir()))
        (blinded / "bionic").mkdir()
        # Both vocabulary branches are exercised: the ambient tree (populated
        # concerns in a dev checkout) and a world with no inputs at all (stubbed).
        # Running over both keeps the test non-vacuous in the staged artifact,
        # where the ambient tree legitimately stubs some concerns.
        for label, tree in (("ambient", D._build(REPO, "bionic")),
                            ("blinded", D._build(blinded, "bionic"))):
            doc = json.loads(tree["_meta/coverage.json"])
            for rec in doc["concerns"]:
                where = f"{label}/{rec['concern']}"
                self.assertIn(rec["verdict"], {"populated", "stubbed"},
                              f"{where}: verdict outside the closed vocabulary")
                # The blind-extractor direction: claiming `populated` while having
                # found nothing is the regression this signal exists to surface.
                # The converse is NOT asserted — an in-pack no-extractor path may
                # legitimately report `stubbed` while carrying partial sources.
                if rec["verdict"] == "populated":
                    self.assertTrue(rec["inputs_found"],
                                    f"{where}: populated with empty inputs_found")
                    # ADR-0096 clause 3 strengthened this from "found some
                    # source" to "found an entity of its own kind". A source
                    # count above zero is what let a data model with no entity
                    # table report `populated`.
                    self.assertGreaterEqual(rec["n_entities"], 1, where)

    def test_blinded_world_stubs_every_concern(self):
        """The signal MOVES: with no inputs, every concern reports `stubbed`."""
        import json
        blinded = Path(self.enterContext(_tmpdir()))
        (blinded / "bionic").mkdir()
        doc = json.loads(D._build(blinded, "bionic")["_meta/coverage.json"])
        self.assertTrue(doc["concerns"], "no concern records emitted at all")
        for rec in doc["concerns"]:
            self.assertEqual(rec["verdict"], "stubbed", f"{rec['concern']} not stubbed")


class ArchDriftTriggerTests(unittest.TestCase):
    """PB-0073 — what `dry_run` treats as drift, at the manifest boundary.

    `_meta/manifest.json["sources"]` is the ONE sub-object the gate stops
    comparing, and these tests pin both halves of that: the false positive it
    removes, and the four detections it must not remove with it.
    """

    def _derived_repo(self) -> Path:
        """A scratch copy of this repo with a freshly derived, clean arch tree."""
        scratch = Path(self.enterContext(_tmpdir())) / "repo"
        _copy_repo(scratch)
        D.derive(scratch, "bionic")
        self.assertEqual(D.dry_run(scratch, "bionic"), [], "dry_run dirty right after derive")
        return scratch

    def _manifest_path(self, scratch: Path) -> Path:
        return scratch / "bionic" / "arch" / "_meta" / "manifest.json"

    def _rewrite_manifest(self, scratch: Path, mutate) -> None:
        import json
        p = self._manifest_path(scratch)
        doc = json.loads(p.read_text(encoding="utf-8"))
        mutate(doc)
        p.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # PB-0073 W2 — THE FALSE POSITIVE THIS CYCLE REMOVES. Appending a comment to a
    # tracked source moves that file's SHA-256 in manifest["sources"] and moves
    # nothing else: all four spine files, overview, index and coverage come out
    # byte-identical. Before the narrowing that tripped the gate, so every edit to
    # any of ~166 tracked sources demanded a re-derive that changed one hash line.
    # The two preconditions are ASSERTED, not assumed — otherwise a future change
    # that stopped tracking this file would turn the test into a tautology that
    # passes while proving nothing. `crux/scripts/crux/` is inside the staged
    # artifact's allowlist, so the fixture edit lands in both worlds.
    def test_source_only_edit_is_not_drift(self):
        import json
        scratch = self._derived_repo()
        before = json.loads(self._manifest_path(scratch).read_text(encoding="utf-8"))
        src = scratch / "crux" / "scripts" / "crux" / "core" / "data_classes.py"
        src.write_text(src.read_text(encoding="utf-8")
                       + "\n# drift-gate fixture: semantically inert comment\n",
                       encoding="utf-8")
        after = json.loads(D._build(scratch, "bionic")["_meta/manifest.json"])
        # Precondition 1: the per-source hash map really moved.
        self.assertNotEqual(after["sources"], before["sources"],
                            "fixture edit did not move manifest sources — test is vacuous")
        # Precondition 2: the semantic artifact did not.
        self.assertEqual(after["spine_hash"], before["spine_hash"],
                         "fixture edit moved the spine — not an inert edit")
        # The claim: that combination is no longer drift.
        self.assertEqual(D.dry_run(scratch, "bionic"), [],
                         "a source-only edit still trips the drift gate")

    # PB-0073 W2 — the detection the narrowing must NOT take with it. Wholesale
    # exclusion of manifest.json was rejected unanimously by the cycle's council
    # because it would let the committed provenance lie about which tools produced
    # the spine. `tool_pins` stays compared.
    def test_tool_pins_tamper_still_drifts(self):
        scratch = self._derived_repo()
        self._rewrite_manifest(scratch, lambda d: d.__setitem__("tool_pins", {"forged": "9.9.9"}))
        self.assertIn("bionic/arch/_meta/manifest.json", D.dry_run(scratch, "bionic"))

    # PB-0073 W2 — `spine_hash` inside the manifest stays compared too, so the
    # manifest cannot record a spine hash the spine does not have.
    def test_manifest_spine_hash_tamper_still_drifts(self):
        scratch = self._derived_repo()
        self._rewrite_manifest(scratch, lambda d: d.__setitem__("spine_hash", "sha256:" + "0" * 64))
        self.assertIn("bionic/arch/_meta/manifest.json", D.dry_run(scratch, "bionic"))

    # PB-0073 W2 — a NEW key appearing in the manifest is still drift. Comparing
    # "every key except sources" must not degrade into "only the keys the build
    # emits", or a hand-added key would be invisible.
    def test_unexpected_manifest_key_still_drifts(self):
        scratch = self._derived_repo()
        self._rewrite_manifest(scratch, lambda d: d.__setitem__("injected", "surprise"))
        self.assertIn("bionic/arch/_meta/manifest.json", D.dry_run(scratch, "bionic"))

    # 2026-08-31 direct fix — the decision-recovery candidate state file lives INSIDE
    # `arch/` by design (`arch/_recovered/state.yml`, ADR-0062 D4; the tree's
    # AGENTS.md §17 names it a scan artifact inside the tree), yet the derive never
    # writes it. The stale-artifact sweep read every on-disk file the derive would
    # not produce as drift, so the first `recover-decisions` run on any tree turned
    # the arch gate red and stayed red. The positive control beside it proves the
    # sweep still catches a stray file — the exclusion is one directory, not the
    # sweep.
    def test_recovered_state_file_is_not_drift(self):
        scratch = self._derived_repo()
        state = scratch / "bionic" / "arch" / "_recovered" / "state.yml"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("rows: {}\n", encoding="utf-8")
        self.assertEqual(D.dry_run(scratch, "bionic"), [],
                         "the candidate state file inside arch/ trips the drift gate")

    def test_a_stray_file_under_arch_is_still_drift(self):
        scratch = self._derived_repo()
        stray = scratch / "bionic" / "arch" / "stray.md"
        stray.write_text("not a derive output\n", encoding="utf-8")
        self.assertIn("bionic/arch/stray.md", D.dry_run(scratch, "bionic"))

    # PB-0073 W2, constraint C1 — the exclusion is SYMMETRIC and tolerates the key
    # being absent. A manifest with no `sources` at all (hand-edited, or written by
    # an older derive) must compare equal on the remaining keys rather than raising
    # or reporting a phantom drift. The stale/absent sources map is accepted debt,
    # recorded in this cycle's C4 for the follow-on decision.
    def test_absent_sources_key_is_tolerated(self):
        scratch = self._derived_repo()
        self._rewrite_manifest(scratch, lambda d: d.pop("sources", None))
        self.assertEqual(D.dry_run(scratch, "bionic"), [],
                         "a manifest lacking `sources` should compare equal, not drift")

    # ── ADR-0078 clause 4 — the exemption is bounded by the shape that justified it ──
    #
    # The narrowing was justified by ONE measured false positive: a map of one
    # SHA-256 per tracked source moved on every content edit. Clause 4 bounds the
    # exemption to that shape — "a mapping of strings to strings; a value of any
    # other shape reports drift" — with the postcondition that no JSON structure
    # other than a string-to-string mapping occupies the uncompared region.
    #
    # Without the bound, the region is a hole of arbitrary size: `sources` could
    # hold a list, a scalar, or a nested document, and the substitute-and-render
    # comparison would reproduce it verbatim and report CLEAN. These four cases
    # are the shapes that hole admits. The exempt case and the absent case above
    # are the other side of the same boundary and must stay green.
    #
    # Only the ON-DISK value is shape-checked. The built side is this process's
    # own output, so shape-checking it would assert against the deriver rather
    # than against the artifact, and a defect there is a different test's subject.
    #
    # A non-string KEY is unreachable through `json.loads` (JSON object keys are
    # always strings), so no test exists for it; the predicate still checks keys,
    # because the function's contract is about the value's shape and not about
    # which parser produced it.

    def _sources_shaped(self, value):
        """A scratch repo whose committed manifest carries `sources: <value>`."""
        scratch = self._derived_repo()
        self._rewrite_manifest(scratch, lambda d: d.__setitem__("sources", value))
        return scratch

    def test_sources_as_a_list_is_drift(self):
        scratch = self._sources_shaped(["crux/scripts/derive-arch.py"])
        self.assertIn("bionic/arch/_meta/manifest.json", D.dry_run(scratch, "bionic"))

    def test_sources_as_a_scalar_is_drift(self):
        scratch = self._sources_shaped("all of them, honest")
        self.assertIn("bionic/arch/_meta/manifest.json", D.dry_run(scratch, "bionic"))

    def test_sources_as_null_is_drift(self):
        # Distinct from the absent-key case above: absent means "this manifest
        # records no read set", which is tolerated; `null` is a PRESENT value of
        # the wrong shape.
        scratch = self._sources_shaped(None)
        self.assertIn("bionic/arch/_meta/manifest.json", D.dry_run(scratch, "bionic"))

    def test_sources_carrying_a_nested_document_is_drift(self):
        # The postcondition's real target: arbitrary JSON structure smuggled into
        # the one region the gate does not read.
        scratch = self._sources_shaped({"crux/scripts/derive-arch.py": {"sha256": "0" * 64}})
        self.assertIn("bionic/arch/_meta/manifest.json", D.dry_run(scratch, "bionic"))

    def test_sources_with_a_non_string_value_is_drift(self):
        scratch = self._sources_shaped({"crux/scripts/derive-arch.py": 1234})
        self.assertIn("bionic/arch/_meta/manifest.json", D.dry_run(scratch, "bionic"))

    def test_a_string_to_string_sources_map_stays_exempt(self):
        # The other side of the boundary, asserted so a shape check cannot be
        # tightened into "the exemption is gone". A map of the right shape whose
        # CONTENTS differ from the built one is still not drift — clause 4 bounds
        # the region's shape, explicitly "not its contents".
        scratch = self._sources_shaped({"crux/scripts/derive-arch.py": "f" * 64})
        self.assertEqual(D.dry_run(scratch, "bionic"), [],
                         "a string-to-string sources map lost its exemption")

    def test_an_empty_sources_map_stays_exempt(self):
        # Vacuously a mapping of strings to strings. Pinned because an
        # `all(...)`-style predicate and a "non-empty and well-shaped" predicate
        # differ exactly here, and the former is the correct one.
        scratch = self._sources_shaped({})
        self.assertEqual(D.dry_run(scratch, "bionic"), [],
                         "an empty sources map lost its exemption")

    # PB-0073 W2 — FAIL-CLOSED. The parse guard must never turn an unreadable
    # manifest into a pass: unparseable bytes compare unequal to any well-formed
    # build and are reported as drift.
    def test_corrupt_manifest_is_drift_not_a_pass(self):
        scratch = self._derived_repo()
        self._manifest_path(scratch).write_text("{not json at all", encoding="utf-8")
        self.assertIn("bionic/arch/_meta/manifest.json", D.dry_run(scratch, "bionic"))

    # PB-0073 review fix — THE DUPLICATE-KEY ATTACK. `json.loads` keeps the LAST
    # of two duplicate keys, so a manifest whose FIRST `tool_pins` is forged
    # parses to the real one. The first form of this guard normalized BOTH sides
    # through a parse, so the two rendered equal and it reported CLEAN — while a
    # human opening the file read the forged pins at the top. The comparison now
    # targets the on-disk BYTES, so the forged duplicate cannot hide behind the
    # parse. This is the concrete reason the guard is byte-exact rather than
    # semantic, and it is the one test that would go red if it were reverted.
    def test_duplicate_manifest_key_still_drifts(self):
        import json
        scratch = self._derived_repo()
        p = self._manifest_path(scratch)
        text = p.read_text(encoding="utf-8")
        forged = '  "tool_pins": {\n    "engine": "forged/9"\n  },\n'
        text = text.replace("{\n", "{\n" + forged, 1)   # ahead of the real key
        p.write_text(text, encoding="utf-8")
        # Precondition: the fixture must PARSE to the real pins, or it proves
        # nothing about parse-blindness.
        # The real pins are `TOOL_PINS` plus the RESOLVED pack's own, which for
        # this repository is the crux pack's `parser:yaml`. Restating the bare
        # constant made this precondition a second declaration of what the
        # manifest holds, and it went stale the moment a pack declared a parser.
        import crux.arch.core as _core
        self.assertEqual(json.loads(text)["tool_pins"],
                         D.TOOL_PINS | _core.parser_pins("crux"),
                         "fixture parses to the forged pins — it does not test the attack")
        self.assertIn("bionic/arch/_meta/manifest.json", D.dry_run(scratch, "bionic"))

    # PB-0073 review fix — reformatting is drift. Same document, different bytes.
    # A guard that compared parsed documents called this clean, which contradicted
    # the claim that `sources` is the only thing excluded.
    def test_manifest_reformatting_still_drifts(self):
        import json
        scratch = self._derived_repo()
        p = self._manifest_path(scratch)
        doc = json.loads(p.read_text(encoding="utf-8"))
        p.write_text(json.dumps(doc, indent=4, sort_keys=True) + "\n", encoding="utf-8")
        self.assertIn("bionic/arch/_meta/manifest.json", D.dry_run(scratch, "bionic"))

    # PB-0073 review fix — FAIL-CLOSED against RecursionError. `json.loads` raises
    # RecursionError, not ValueError, on JSON nested past the interpreter limit,
    # so a guard catching only (ValueError, TypeError) let it escape: the gate
    # crashed instead of reporting drift. Deep nesting is the input class the
    # arch-extractor-scrub-safety skill named (pruned 2026-09-02), and this
    # test pins it.
    def test_deeply_nested_manifest_is_drift_not_a_crash(self):
        import json
        from _json_depth import overflowing_json
        scratch = self._derived_repo()
        # Precondition, asserted rather than assumed: this input must really
        # reach the RecursionError path. Without it the test degenerates into
        # another "unparseable is drift" case and proves nothing new. The depth
        # that overflows differs by interpreter (3.13 raises below 10 000, 3.14
        # parses 100 000), so `overflowing_json` measures it on this one.
        payload = overflowing_json()
        with self.assertRaises(RecursionError):
            json.loads(payload)
        self._manifest_path(scratch).write_text(payload, encoding="utf-8")
        self.assertIn("bionic/arch/_meta/manifest.json", D.dry_run(scratch, "bionic"))

    # PB-0073 W2 — a DELETED manifest is drift, not a pass (the `_read` -> "" path
    # through the same guard).
    def test_missing_manifest_is_drift(self):
        scratch = self._derived_repo()
        self._manifest_path(scratch).unlink()
        self.assertIn("bionic/arch/_meta/manifest.json", D.dry_run(scratch, "bionic"))

    # PB-0073 W2 — `arch_stack` pin detection survives the narrowing, and does so
    # through a route independent of manifest["sources"]: the pin is RENDERED into
    # overview.md, which stays byte-compared. ADR-0066 point 9b hashes the pin's
    # config file into sources as well, and that copy is what the narrowing stops
    # comparing — so this test pins the surviving route explicitly rather than
    # leaving the guarantee resting on the excluded one.
    def test_arch_stack_pin_change_moves_overview(self):
        spine = {f: f"{f} body\n" for f in D.SPINE_FILES}
        h = "a" * 64
        unpinned = D._synthesize_overview(spine, h, "crux", None)
        pinned = D._synthesize_overview(spine, h, "crux", "crux")
        self.assertNotEqual(unpinned, pinned, "pin change left overview.md byte-identical")
        self.assertIn("arch_stack: crux", pinned)
        self.assertIn("auto-detected", unpinned)


class ArchDependencyReaderTests(unittest.TestCase):
    """The direct-dependency readers clause 10 deliberately KEPT.

    ADR-0096 clause 10 retired the confidence layer — the grade scale, the
    expectation-marker table, the floors and probes, and the escalation offer.
    It did NOT retire the PEP 508 / requirements / pyproject readers those
    markers happened to be built on: detection (clause 6) reads direct
    dependency names to tell one stack from another, so these parsers outlive
    the layer that first needed them. This class is their home now that
    `ArchConfidenceTests` is gone, and it is named for what they do rather than
    for the retired feature that used to own them.

    The substance under test is unchanged: a direct dependency matches, a
    commented-out one does not, and an extras-only one does not.
    """

    def test_requirements_reader_ignores_commented_out_dependencies(self):
        """A `# fastapi` line names no dependency the project actually has.

        This is the discrimination detection rests on. A commented-out
        requirement that still counted would let a repo be classified by a
        dependency somebody deliberately removed.
        """
        direct = D._requirements_direct_dep_names("fastapi==0.1\nrequests\n")
        self.assertIn("fastapi", direct)
        self.assertIn("requests", direct)

        commented = D._requirements_direct_dep_names(
            "# fastapi\n# flask\n# django\nrequests\n"
        )
        self.assertEqual(commented, {"requests"})
        for absent in ("fastapi", "flask", "django"):
            self.assertNotIn(absent, commented)

    def test_pyproject_reader_excludes_extras_only_dependencies(self):
        """`[project.optional-dependencies]` is an extra, not a direct dep."""
        direct = D._pyproject_direct_dep_names(
            '[project]\nname = "x"\ndependencies = ["fastapi>=0.1", "requests"]\n'
        )
        self.assertIn("fastapi", direct)
        self.assertIn("requests", direct)

        extras_only = D._pyproject_direct_dep_names(
            '[project]\nname = "x"\ndependencies = ["requests"]\n'
            '[project.optional-dependencies]\nweb = ["fastapi", "flask"]\n'
        )
        self.assertIn("requests", extras_only)
        self.assertNotIn("fastapi", extras_only)
        self.assertNotIn("flask", extras_only)

    def test_pyproject_reader_reads_a_django_direct_dependency(self):
        self.assertIn(
            "django",
            D._pyproject_direct_dep_names(
                '[project]\nname = "x"\ndependencies = ["django>=4"]\n'
            ),
        )

    # ADR-0074 self-review — the line-scanned pyproject dep parser is quote-aware
    # and honors Poetry's `optional`: a `]`/`#` inside a quoted PEP 508 string
    # never ends the array or starts a comment, and an `optional = true` entry is
    # an extra, not a direct dependency (matches the [project] extras exclusion).
    def test_pyproject_parser_quote_and_optional_edges(self):
        f = D._pyproject_direct_dep_names
        # An extras-bearing entry (`fastapi[all]`) must not premature-close the
        # array and drop the deps listed after it.
        self.assertEqual(
            f('[project]\nname = "x"\ndependencies = [\n'
              '  "fastapi[all]>=0.1",\n  "django>=4",\n]\n'),
            {"fastapi", "django"},
        )
        # Same premature-close, but on the opening line itself.
        self.assertEqual(
            f('[project]\ndependencies = ["fastapi[all]", "django"]\n'),
            {"fastapi", "django"},
        )
        # A `#` inside a quoted VCS URL is data, not a TOML comment; dep survives.
        self.assertEqual(
            f('[project]\nname = "x"\n'
              'dependencies = ["django @ git+https://h/r#egg=django"]\n'),
            {"django"},
        )
        # A Poetry `optional = true` dependency is an extra, not a direct dep.
        self.assertNotIn(
            "fastapi",
            f('[tool.poetry.dependencies]\npython = "^3.11"\n'
              'fastapi = {version = "^0.1", optional = true}\n'),
        )
        # A plain Poetry dependency IS direct.
        self.assertIn(
            "flask",
            f('[tool.poetry.dependencies]\npython = "^3.11"\nflask = "^3.0"\n'),
        )

    # S-cov3 — requirements.txt inline-comment stripping (`split(" #", 1)[0]`): a
    # trailing `# keep` is dropped so the dep still matches, but a token whose
    # inline comment merely mentions another dep does not match that other dep.
    def test_requirements_inline_comment_stripping(self):
        names = D._requirements_direct_dep_names("fastapi==0.1  # keep\n")
        self.assertIn("fastapi", names)
        names2 = D._requirements_direct_dep_names("requests  # need fastapi later\n")
        self.assertIn("requests", names2)
        self.assertNotIn("fastapi", names2)

    # M1 (SECURITY:S5/S1) — the containment + 2 MB size guard itself.
    #
    # This check used to reach `_safe_read_bytes` through `_marker_dep_present`,
    # which ADR-0096 clause 10 retired with the rest of the confidence layer.
    # The GUARD is not retired — every pack read routes through it — so the
    # coverage is kept and re-aimed at `_safe_read_bytes` directly rather than
    # deleted alongside its former caller. Deleting a security test because the
    # one caller it happened to use went away is how a guard quietly stops being
    # tested while still being depended on.
    def test_safe_read_bytes_refuses_oversize_and_out_of_repo_reads(self):
        # (a) oversize manifest (> 2 MB) -> no bytes returned, and it is RECORDED.
        big = Path(self.enterContext(_tmpdir()))
        manifest = big / "requirements.txt"
        pad = "# pad\n" * (D._MAX_FILE_BYTES // 6 + 1)
        manifest.write_text("fastapi==0.1\n" + pad, encoding="utf-8")
        self.assertGreater(manifest.stat().st_size, D._MAX_FILE_BYTES)
        oversize: list = []
        self.assertIsNone(D._safe_read_bytes(big, manifest, oversize))
        self.assertTrue(oversize, "an oversize refusal must be recorded, not silent")

        # (b) a file symlinked to a target OUTSIDE the repo root -> no bytes.
        import os
        inside = Path(self.enterContext(_tmpdir()))
        outside = Path(self.enterContext(_tmpdir()))
        real = outside / "real-requirements.txt"
        real.write_text("fastapi==0.1\n", encoding="utf-8")
        link = inside / "requirements.txt"
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("symlinks unavailable on this platform")
        # Sanity: the symlink resolves to a readable file whose bytes WOULD come
        # back if read raw — so a None here proves the containment guard fired,
        # rather than the file merely being unreadable.
        self.assertTrue(link.is_file())
        self.assertEqual(real.read_bytes(), b"fastapi==0.1\n")
        self.assertIsNone(D._safe_read_bytes(inside, link, []))

class ArchRuntimeConfinementTests(unittest.TestCase):
    """ADR-0074 decision 11(i)+(iii) — the derive path executes no application.

    The derive/dry_run/audit path imports or dispatches NO runtime executor, and
    no env var substitutes for the tool-boundary confirmation.
    `CRUX_ARCH_ALLOW_RUNTIME` is RESERVED and read by no code path here.

    **Scope note.** This gate used to read `derive.py` alone, which was the whole
    deriver. ADR-0096's split left `derive.py` a ~390-line facade and moved the
    extraction code to `core.py` and `packs/*.py`, so a check aimed only at the
    facade would have kept passing while the code it was written to confine
    moved out from under it — a security gate silently narrowed by a refactor.
    The subject is now every module on the derive path.

    `runtime/` is deliberately EXCLUDED: that subpackage is the confined
    escalation target itself, human-invoked behind a two-factor consent gate,
    and it is the one place this repo's application-execution code is allowed to
    live.
    """

    def _derive_path_modules(self) -> list[Path]:
        arch = SCRIPTS / "crux" / "arch"
        mods = [arch / "derive.py", arch / "core.py"]
        mods += sorted((arch / "packs").glob("*.py"))
        for m in mods:
            self.assertTrue(m.is_file(), f"derive-path module missing: {m}")
        # Guard the guard: if the split moves again, this must not quietly
        # shrink to a couple of files.
        self.assertGreaterEqual(len(mods), 7, "derive-path module set looks truncated")
        return mods

    def test_derive_path_reads_no_reserved_runtime_flag(self):
        for mod in self._derive_path_modules():
            with self.subTest(module=mod.name):
                self.assertNotIn(
                    "CRUX_ARCH_ALLOW_RUNTIME", mod.read_text(encoding="utf-8"),
                    f"{mod.name} must not read the reserved runtime flag",
                )

    def test_no_runtime_executor_symbol_is_importable(self):
        for name in dir(D):
            low = name.lower()
            for banned in ("runtime_exec", "run_app", "execute_app", "import_app"):
                self.assertNotIn(banned, low, f"runtime-executor symbol leaked: {name}")

    def test_derive_path_never_imports_subprocess(self):
        import ast
        for mod in self._derive_path_modules():
            with self.subTest(module=mod.name):
                tree = ast.parse(mod.read_text(encoding="utf-8"))
                imported: set[str] = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported |= {a.name.split(".")[0] for a in node.names}
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imported.add(node.module.split(".")[0])
                self.assertNotIn(
                    "subprocess", imported,
                    f"{mod.name} must not import subprocess (no app execution)",
                )

    def test_derive_path_calls_no_dynamic_execution_builtin(self):
        """AST proof, not a grep: no dynamic-exec builtin is ever CALLED.

        Bare `eval`/`exec`/`compile`/`__import__` (Name calls) and `os.system`
        (an attribute call) are the injection / app-execution vectors.
        `re.compile` is an attribute call, so it is not a bare `compile` and is
        not banned; `spec.loader.exec_module` (the ADR-0066 override loader) is
        likewise an attribute call, not a bare `exec`.
        """
        import ast
        banned_names = {"eval", "exec", "compile", "__import__"}
        for mod in self._derive_path_modules():
            with self.subTest(module=mod.name):
                for node in ast.walk(ast.parse(mod.read_text(encoding="utf-8"))):
                    if not isinstance(node, ast.Call):
                        continue
                    f = node.func
                    if isinstance(f, ast.Name):
                        self.assertNotIn(
                            f.id, banned_names,
                            f"banned dynamic-exec call in {mod.name}: {f.id}",
                        )
                    elif isinstance(f, ast.Attribute):
                        if (f.attr == "system" and isinstance(f.value, ast.Name)
                                and f.value.id == "os"):
                            self.fail(f"banned shell-out call in {mod.name}: os.system")


class ArchParserPinTests(unittest.TestCase):
    """ADR-0096 clause 1's parser pin, recorded PACK-SCOPED.

    Clause 1 requires each parser's version pin hashed into the provenance
    manifest, "so a parser upgrade moves the tree deliberately rather than
    silently". The pin is scoped to the RESOLVED pack and never global.

    A global `TOOL_PINS` entry would record every grammar version in every
    tree's `_meta/manifest.json`, including the two packs that consult no
    grammar — crux and python. That has two costs and neither is cosmetic. It
    moves this repository's own committed manifest, which is derived under the
    crux pack and reads no grammar (it records `parser:yaml` and nothing else).
    And it states a provenance claim that is false: the manifest is the ledger
    of what produced these bytes, and a grammar no probe consulted produced none
    of them.
    """

    def _core(self):
        return importlib.import_module("crux.arch.core")

    def test_every_pack_pins_yaml_through_the_universal_concern(self):
        """No pack is pin-free any more, and the reason is one concern.

        This list of stdlib-only packs emptied in four steps. `node` left when
        its api-surface reader became a tree-sitter parse, `ruby` one loop later
        for the same reason, and `crux` when its data-model and api-surface
        declared `yaml`. `python` left last and without a reader change at all:
        `decision-index` is the UNIVERSAL concern, the core binds it for every
        pack, and it reads ADR frontmatter through `core._frontmatter`, whose
        regex fallback computes a different spine hash. Declaring `yaml` there
        is what stops any pack deriving through the fallback, so every pack now
        carries `parser:yaml`.

        The pin still follows the READER rather than the pack — this reader just
        happens to be bound for all five.
        """
        core = self._core()
        for pack in sorted(core.REGISTERED_PACKS):
            with self.subTest(pack=pack):
                self.assertIn("parser:yaml", core.parser_pins(pack))
        self.assertEqual(list(core.parser_pins("python")), ["parser:yaml"])

    def test_the_node_pack_pins_exactly_the_grammar_its_reader_uses(self):
        """Its two grammars, plus the universal `parser:yaml` every pack carries."""
        core = self._core()
        self.assertEqual(sorted(core.parser_pins("node")),
                         ["parser:tree_sitter", "parser:tree_sitter_typescript",
                          "parser:yaml"])

    def test_the_ruby_pack_pins_exactly_the_grammar_its_reader_uses(self):
        """The ruby pack's `config/routes.rb` reader, and nothing wider.

        Its data-model reader consumes `db/schema.rb`, an EMITTED artifact whose
        grammar is ActiveRecord's, and its module-graph reader is a lexical
        constant scan. Neither declares a grammar, so neither adds a GRAMMAR
        pin — `parser:yaml` is the universal `decision-index` declaration, which
        every pack carries and no pack's own readers earn."""
        core = self._core()
        self.assertEqual(sorted(core.parser_pins("ruby")),
                         ["parser:tree_sitter", "parser:tree_sitter_ruby",
                          "parser:yaml"])

    def test_a_derive_carries_the_base_pins_plus_the_resolved_packs_own(self):
        """The end-to-end reading: this repository derives under the crux pack,
        so its manifest's `tool_pins` is `TOOL_PINS` PLUS that pack's own pins
        and nothing else.

        This asserted the bare constant while the crux pack declared no parser.
        It is written as `TOOL_PINS | parser_pins(pack)` now rather than as a
        second literal, so the union is the claim and no future pack change
        needs this line edited to stay true — which is what a restated literal
        would have required, and what let the previous form read as a rule
        ("no `parser:` key at all") when it was only a fact about one pack.
        """
        import json
        core = self._core()
        doc = json.loads(self.__class__._tree()["_meta/manifest.json"])
        self.assertEqual(doc["tool_pins"], D.TOOL_PINS | core.parser_pins("crux"))
        self.assertEqual([k for k in doc["tool_pins"] if k.startswith("parser:")],
                         ["parser:yaml"])

    @classmethod
    def _tree(cls):
        if not hasattr(cls, "_cached_tree"):
            cls._cached_tree = D._build(REPO, "bionic")
        return cls._cached_tree

    def test_a_declared_parser_is_pinned_by_module_name(self):
        """A pack that DOES declare one gets `parser:<module>: <version>`.

        Patched rather than taken from a shipped pack so this holds before and
        after any pack starts declaring a grammar. `yaml` is used because it is
        a real third-party module in this project's environment whose
        distribution name (`PyYAML`) differs from its import name — the case a
        naive `metadata.version(module)` gets wrong.
        """
        import importlib.metadata
        from unittest import mock
        core = self._core()
        real = core.input_classes

        def patched(pack_name):
            out = dict(real(pack_name))
            out["data-model"] = out["data-model"]._replace(parser=("yaml",))
            return out

        with mock.patch.object(core, "input_classes", patched):
            pins = core.parser_pins("crux")
        self.assertEqual(pins, {"parser:yaml": importlib.metadata.version("PyYAML")})

    def test_an_unversionable_parser_is_the_environment_lane(self):
        """Clause 1 requires the pin RECORDED. A module that resolves but whose
        version cannot be read cannot be pinned, so it takes clause 2's exit-2
        environment lane rather than silently producing a manifest with the pin
        missing — which would be the byte-compared channel quietly disagreeing
        with itself across two machines."""
        from unittest import mock
        core = self._core()
        real = core.input_classes

        def patched(pack_name):
            out = dict(real(pack_name))
            out["data-model"] = out["data-model"]._replace(parser=("json",))
            return out

        with mock.patch.object(core, "input_classes", patched):
            with self.assertRaises(core.ParserUnavailable):
                core.parser_pins("crux")


def _tmpdir():
    import tempfile
    return tempfile.TemporaryDirectory()


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


if __name__ == "__main__":
    unittest.main(verbosity=2)
