"""ADR-0096 clause 7 — the `possibly_stale` annotation.

A committed artifact is a file the target project's own toolchain emitted, and
it goes out of date when the sources it was emitted from move on. The deriver
reports `possibly_stale` when the repository's commit history shows a source in
the concern's declared set committed AFTER the artifact, and prints the one
command that refreshes it.

Three properties carry the clause, and each has tests here.

**Reported, never recorded.** Clause 2 splits the channels, and this annotation
lives entirely in the reported one. It writes nothing under `arch/`, changes no
recorded verdict, and changes neither the drift gate's exit status nor strict's.
The three `ReportedNeverRecordedTests` are the whole reason the annotation can
depend on the commit graph at all: a byte-compared tree that varied with the
history would flap across a fresh clone.

**Silent where history cannot establish order.** Four conditions, one test each
— a shallow clone, a squashed history, a same-commit update, and an uncommitted
working tree. The squash test is the sharp one: it asserts the SAME tree fires
before the squash and goes silent after, so the silence is a property of the
history rather than of the fixture.

**The declared source set is recorded, and the resolution is not.** The manifest
carries each concern's declared source set as GLOBS — deterministic, byte-stable
and tiny — and this module resolves them against the working tree at report
time. Recording the resolved file list instead would put a new file that changes
no output inside the compared region, which is the drift-gate flap clause 2
forbids.

Stdlib only. Every fixture builds its own git repository, so nothing here reads
the ambient checkout.
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

CORE = importlib.import_module("crux.arch.core")
STALE = importlib.import_module("crux.arch.staleness")

MODULE = SCRIPTS / "crux" / "arch" / "staleness.py"

_HAS_GIT = shutil.which("git") is not None
_NEEDS_GIT = "the staleness advisory reads the commit graph"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """A test-side git, identity supplied on the command line because the
    module under test points both config layers at /dev/null and so must this."""
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        cwd=str(repo), capture_output=True, text=True,
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(repo),
             "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"},
        check=False,
    )


ARTIFACT = "openapi.json"
SOURCE = "src/app.py"
REFRESH = "python -m app export-openapi > openapi.json"


def _declared(kind: str = "committed-artifact", refresh: str = REFRESH,
              artifact: tuple = (ARTIFACT,)) -> dict:
    return {"api-surface": CORE.InputClass(
        kind=kind,
        expected="an emitted `openapi.json`",
        globs=(ARTIFACT, "src/*.py"),
        artifact=artifact,
        refresh=refresh,
    )}


def _records() -> list[dict]:
    return [{"concern": "api-surface", "verdict": "populated",
             "extractor": "python", "inputs_found": [ARTIFACT],
             "n_sources": 1, "n_entities": 4, "annotations": []}]


@unittest.skipUnless(_HAS_GIT, _NEEDS_GIT)
class _Repo(unittest.TestCase):
    """A repository whose artifact is committed first and whose source follows."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        _git(self.root, "init", "-q", "-b", "main")
        self.write(ARTIFACT, '{"openapi": "3.0.0"}\n')
        self.commit("the artifact")
        self.first = self.head()

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel: str, text: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def commit(self, message: str) -> None:
        _git(self.root, "add", "-A")
        proc = _git(self.root, "commit", "-q", "-m", message)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def head(self) -> str:
        return _git(self.root, "rev-parse", "HEAD").stdout.strip()

    def source_after(self) -> None:
        """Commit the source in a LATER commit than the artifact."""
        self.write(SOURCE, "def handler():\n    return 1\n")
        self.commit("the source, later")

    def annotate(self, declared=None, records=None):
        records = _records() if records is None else records
        advisories = STALE.annotate(self.root, records,
                                    declared=_declared() if declared is None else declared)
        return advisories, records


# ───────────────────────── the advisory, when it fires ──────────────────────


class FiresTests(_Repo):

    def test_a_source_committed_after_the_artifact_fires(self):
        self.source_after()
        advisories, records = self.annotate()
        self.assertEqual(len(advisories), 1, advisories)
        self.assertEqual(advisories[0].concern, "api-surface")
        self.assertEqual(advisories[0].artifact, ARTIFACT)
        self.assertEqual(records[0]["annotations"], [STALE.POSSIBLY_STALE])

    def test_the_advisory_prints_the_one_command_that_refreshes_it(self):
        self.source_after()
        advisories, _ = self.annotate()
        self.assertEqual(advisories[0].command, REFRESH)
        self.assertIn(REFRESH, "\n".join(STALE.advisory_lines(advisories)))

    def test_a_pack_declaring_no_refresh_command_says_so(self):
        """Honest over invented: crux prints the command the pack declared, and
        names its absence when the pack declared none."""
        self.source_after()
        advisories, _ = self.annotate(declared=_declared(refresh=""))
        self.assertEqual(advisories[0].command, "")
        line = "\n".join(STALE.advisory_lines(advisories))
        self.assertIn("no refresh command", line)

    def test_a_source_committed_before_the_artifact_is_silent(self):
        # Rebuild the other way round: source first, artifact second.
        self.write(SOURCE, "def handler():\n    return 1\n")
        self.commit("the source, earlier")
        self.write(ARTIFACT, '{"openapi": "3.0.1"}\n')
        self.commit("the artifact, later")
        advisories, records = self.annotate()
        self.assertEqual(advisories, [])
        self.assertEqual(records[0]["annotations"], [])

    def test_only_a_committed_artifact_concern_is_ever_annotated(self):
        """A `parser` concern reads authored source directly, so there is no
        artifact for a source to be newer than."""
        self.source_after()
        advisories, records = self.annotate(declared=_declared(kind="parser"))
        self.assertEqual(advisories, [])
        self.assertEqual(records[0]["annotations"], [])

    def test_an_artifact_that_was_never_committed_is_silent(self):
        self.source_after()
        records = _records()
        records[0]["inputs_found"] = ["never-committed.json"]
        advisories, records = self.annotate(records=records)
        self.assertEqual(advisories, [])

    def test_a_concern_declaring_no_artifact_pattern_is_silent(self):
        """The fifth silence, and a declaration rather than a history. Without
        the artifact/source line the question degenerates into "was one source
        committed after another source in the same set", which the ADR index —
        whose glob set covers both the index and every ADR — answers `yes` for
        every repository whose newest ADR postdates its oldest."""
        self.source_after()
        advisories, records = self.annotate(declared=_declared(artifact=()))
        self.assertEqual(advisories, [])
        self.assertEqual(records[0]["annotations"], [])

    def test_an_artifact_is_never_its_own_newer_source(self):
        """The artifact matches the concern's own glob set, so a later commit to
        the artifact itself must not be read as a source moving ahead of it."""
        self.write(ARTIFACT, '{"openapi": "3.0.9"}\n')
        self.commit("the artifact again, later")
        advisories, _ = self.annotate()
        self.assertEqual(advisories, [])

    def test_exactly_three_shipped_pack_concerns_declare_an_artifact_pattern(self):
        """Pins the judgment rather than letting it rot silently.

        crux's own two committed artifacts are gated on their CONTENT (a drifted
        one raises `StaleProjectionInput`), so the weaker commit-order question
        is noise for them and no pack declared an artifact at all until the
        python api-surface did.

        The roster is EXACT EQUALITY on purpose. A new pack-concern pair that
        declares an artifact changes who clause 7 can speak about, and this
        assertion failing is that change announcing itself — it is the tripwire
        working, not a bug. Three pairs are on it, and they are not all the same
        kind of declaration:

          * `python/api-surface` and `ruby/api-surface` declare `kind="parser"`.
            Their glob sets hold both shapes — an emitted artifact their first
            probes read, and the authored sources their parser rungs consume —
            and `artifact` is the line clause 7 draws between them. Without it
            the question degenerates into "was one source committed after
            another source in the same set". `annotate` skips every concern that
            is not `committed-artifact`, so NEITHER produces an advisory today;
            both stand so the line survives if a chain is ever reordered.
          * `ruby/data-model` declares `kind="committed-artifact"`, and it is the
            FIRST shipped pack-concern pair that does both. `db/schema.rb` is
            emitted by ActiveRecord's schema dumper and `db/migrate/**/*.rb` is
            the source set it is dumped from, so commit order is exactly the
            question clause 7 asks. This pair is where the advisory goes live.

        `possibly_stale` is reported-only in every case, so none of this reaches
        a recorded verdict or the byte-compared tree.

        **Declaring an artifact does NOT oblige a pack to declare a refresh
        command, and the split is rostered here rather than assumed.** This
        assertion once read "every artifact declares a refresh", which is the
        right default and the wrong absolute. `python/api-surface` is the
        exception: the only command that emits an `openapi.json` imports the
        target's own application object and calls `.openapi()` on it, and
        executing the target's code is what ADR-0075 confines behind a
        two-factor consent gate and what clause 11 forbids any crux surface
        pointing a reader at. A refresh command is reader-facing — it reaches
        the clause 9 remediation line, which `derive-arch` instructs a forked
        subagent to paste verbatim — so the honest declaration is none, and
        clause 9 falls back to `NO_COMMAND — the derive needs <expected>`.

        The two rosters are both EXACT, so neither direction goes quiet: a new
        pair that omits a refresh must be adjudicated onto `NO_REFRESH` with its
        reason, and a pair that gains one must leave it. Nothing here is a
        blanket permission to skip the field."""
        declaring = sorted(
            f"{pack}/{concern}"
            for pack in sorted(CORE.REGISTERED_PACKS)
            for concern, ic in CORE.input_classes(pack).items()
            if ic.artifact
        )
        self.assertEqual(
            declaring, ["python/api-surface", "ruby/api-surface", "ruby/data-model"])
        #: The adjudicated exception, named. Everything else must carry one.
        NO_REFRESH = {"python/api-surface"}
        for pair in declaring:
            pack, concern = pair.split("/", 1)
            ic = CORE.input_classes(pack)[concern]
            with self.subTest(pair=pair):
                if pair in NO_REFRESH:
                    self.assertEqual(
                        ic.refresh, "",
                        f"{pair} is rostered as declaring no refresh command, because the "
                        "only command that emits its artifact executes the target's own "
                        "application. Adding one puts that instruction on a reader-facing "
                        "surface — see the clause 11 prohibition above.")
                else:
                    self.assertTrue(
                        ic.refresh.strip(),
                        f"{pair} declares an artifact and no refresh command. If that is "
                        "deliberate, roster it in NO_REFRESH with the reason.")
        self.assertEqual(CORE.input_classes("python")["api-surface"].kind, "parser")
        self.assertEqual(CORE.input_classes("ruby")["api-surface"].kind, "parser")
        self.assertEqual(CORE.input_classes("ruby")["data-model"].kind,
                         "committed-artifact")

    def test_the_ruby_data_model_artifact_is_compared_against_its_migrations(self):
        """The declaration is only useful if the SOURCE set is declared too.

        `db/schema.rb` is the artifact; `db/migrate/**/*.rb` is what it is
        dumped from. Without the migrations in `globs`, the concern's input set
        would be the artifact alone, and clause 7's question — "was a source
        committed after the artifact" — would have no source to ask about."""
        ic = CORE.input_classes("ruby")["data-model"]
        self.assertEqual(ic.artifact, ("db/schema.rb",))
        self.assertIn("db/migrate/**/*.rb", ic.globs)
        self.assertIn("db/schema.rb", ic.globs)
        self.assertEqual(ic.refresh, "bin/rails db:schema:dump")

    def test_a_parser_concern_declaring_an_artifact_is_never_annotated(self):
        """The consequence of the pairing above, asserted rather than assumed."""
        self.source_after()
        advisories, records = self.annotate(
            declared=_declared(kind="parser"),
        )
        self.assertEqual(advisories, [])
        self.assertEqual(records[0].get("annotations", []), [])

    def test_a_concern_that_consumed_no_input_is_silent(self):
        self.source_after()
        records = _records()
        records[0]["inputs_found"] = []
        advisories, _ = self.annotate(records=records)
        self.assertEqual(advisories, [])


# ─────────────── silent where history cannot establish order ────────────────


class UnorderableHistoryTests(_Repo):
    """Clause 7's four named conditions. Each is a case where the commit graph
    does not answer the question, and the honest report is no report."""

    def test_a_shallow_clone_is_silent(self):
        self.source_after()
        self.assertEqual(len(self.annotate()[0]), 1, "fixture did not fire — vacuous")
        with tempfile.TemporaryDirectory() as t:
            clone = Path(t) / "shallow"
            proc = _git(self.root, "clone", "-q", "--depth", "1",
                        f"file://{self.root}", str(clone))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(
                _git(clone, "rev-parse", "--is-shallow-repository").stdout.strip(),
                "true", "the clone is not shallow — test is vacuous")
            records = _records()
            self.assertEqual(STALE.annotate(clone, records, declared=_declared()), [])
            self.assertEqual(records[0]["annotations"], [])

    def test_a_squashed_history_is_silent(self):
        """The sharp one: the SAME tree fires before the squash and not after,
        so the silence is a property of the history and not of the fixture."""
        self.source_after()
        self.assertEqual(len(self.annotate()[0]), 1, "fixture did not fire — vacuous")
        _git(self.root, "reset", "--soft", self.first)
        proc = _git(self.root, "commit", "-q", "--amend", "--no-edit")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            _git(self.root, "rev-list", "--count", "HEAD").stdout.strip(), "1",
            "the history was not squashed — test is vacuous")
        advisories, records = self.annotate()
        self.assertEqual(advisories, [])
        self.assertEqual(records[0]["annotations"], [])

    def test_a_same_commit_update_is_silent(self):
        """Artifact and source refreshed together, which is the shape a correct
        refresh produces. Commit order says nothing, so neither does crux."""
        self.write(SOURCE, "def handler():\n    return 2\n")
        self.write(ARTIFACT, '{"openapi": "3.0.2"}\n')
        self.commit("refresh both together")
        advisories, records = self.annotate()
        self.assertEqual(advisories, [])
        self.assertEqual(records[0]["annotations"], [])

    def test_an_uncommitted_working_tree_is_silent(self):
        self.source_after()
        self.assertEqual(len(self.annotate()[0]), 1, "fixture did not fire — vacuous")
        self.write(SOURCE, "def handler():\n    return 99  # uncommitted\n")
        advisories, records = self.annotate()
        self.assertEqual(advisories, [])
        self.assertEqual(records[0]["annotations"], [])

    def test_a_directory_that_is_not_a_repository_is_silent(self):
        with tempfile.TemporaryDirectory() as t:
            records = _records()
            self.assertEqual(STALE.annotate(Path(t), records, declared=_declared()), [])
            self.assertEqual(records[0]["annotations"], [])


# ──────────────────────── reported, never recorded ──────────────────────────


class ReportedNeverRecordedTests(_Repo):
    """The clause 2 boundary, tested three ways over one derived tree."""

    def _derive(self) -> tuple[Path, list]:
        """Derive a real tree, then point its `api-surface` record at the
        artifact this fixture committed, so the annotation genuinely fires.
        Without that the three assertions below would hold vacuously."""
        self.source_after()
        (self.root / ".bionic.yml").write_text(
            'config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8")
        (self.root / "bionic").mkdir(exist_ok=True)
        (self.root / "bionic" / "manifest.yml").write_text(
            'schema_version: "5"\n', encoding="utf-8")
        report: list = []
        CORE.derive(self.root, "bionic", report=report)
        for rec in report:
            if rec["concern"] == "api-surface":
                rec["inputs_found"] = [ARTIFACT]
        return self.root / "bionic" / "arch", report

    def _annotate(self, report: list) -> list:
        advisories = STALE.annotate(self.root, report, declared=_declared())
        self.assertEqual(len(advisories), 1, "the annotation did not fire — vacuous")
        return advisories

    def test_the_annotation_vocabulary_appears_nowhere_under_arch(self):
        arch, report = self._derive()
        self._annotate(report)
        offenders = [str(p) for p in sorted(arch.rglob("*")) if p.is_file()
                     and STALE.POSSIBLY_STALE in p.read_text(encoding="utf-8")]
        self.assertEqual(offenders, [], "an annotation reached the byte-compared tree")

    def test_it_changes_no_recorded_verdict(self):
        _, report = self._derive()
        before = [(r["concern"], r["verdict"]) for r in report]
        self._annotate(report)
        after = [(r["concern"], r["verdict"]) for r in report]
        self.assertEqual(before, after)

    def test_it_changes_neither_the_drift_gates_exit_status_nor_stricts(self):
        _, report = self._derive()
        strict_before = [f["concern"] for f in CORE.strict_failures(report, strict=True)]
        self._annotate(report)
        self.assertEqual(CORE.dry_run(self.root, "bionic"), [],
                         "the annotation moved the drift gate")
        self.assertEqual(
            [f["concern"] for f in CORE.strict_failures(report, strict=True)],
            strict_before, "the annotation changed which concerns fail strict")
        annotated = [r for r in report if r.get("annotations")]
        self.assertTrue(annotated, "nothing was annotated — vacuous")
        for rec in annotated:
            with self.subTest(concern=rec["concern"]):
                bare = dict(rec, annotations=[])
                self.assertEqual(
                    [f["concern"] for f in CORE.strict_failures([rec], strict=True)],
                    [f["concern"] for f in CORE.strict_failures([bare], strict=True)],
                )


# ─────────────── the manifest records the declared source set ───────────────


class DeclaredSourcesInManifestTests(unittest.TestCase):

    def _doc(self, declared=None) -> dict:
        text = CORE._synthesize_manifest("aa" * 32, {"a.py": "0" * 64},
                                         declared_sources=declared)
        return json.loads(text)

    def test_the_manifest_carries_the_per_concern_declared_source_set(self):
        doc = self._doc({"api-surface": ["a.json", "src/*.py"]})
        self.assertEqual(doc["declared_sources"], {"api-surface": ["a.json", "src/*.py"]})

    def test_it_is_byte_stable_across_two_renders(self):
        d = {"module-graph": ["b/**/*.py"], "api-surface": ["a.json"]}
        first = CORE._synthesize_manifest("aa" * 32, {}, declared_sources=d)
        second = CORE._synthesize_manifest("aa" * 32, {}, declared_sources=dict(reversed(list(d.items()))))
        self.assertEqual(first, second, "key order reached the bytes")

    def test_it_lands_inside_the_compared_region(self):
        """`sources` is the ONE value the drift gate stops comparing. The
        declared set is not that value, so a change to it is drift."""
        a = CORE._synthesize_manifest("aa" * 32, {}, declared_sources={"x": ["p"]})
        b = CORE._synthesize_manifest("aa" * 32, {}, declared_sources={"x": ["q"]})
        self.assertTrue(CORE._manifest_drifted(a, b))
        # ... while the uncompared value still is uncompared.
        c = CORE._synthesize_manifest("aa" * 32, {"p": "0" * 64},
                                      declared_sources={"x": ["p"]})
        d = CORE._synthesize_manifest("aa" * 32, {"p": "f" * 64},
                                      declared_sources={"x": ["p"]})
        self.assertFalse(CORE._manifest_drifted(c, d))

    def test_the_build_records_every_concerns_declared_globs(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t).resolve()
            (root / "bionic").mkdir()
            (root / "bionic" / "manifest.yml").write_text(
                'schema_version: "5"\n', encoding="utf-8")
            tree = CORE._build(root, "bionic")
            doc = json.loads(tree[CORE.MANIFEST_REL])
        pack = CORE.detect_stack(root, None)
        self.assertEqual(
            doc["declared_sources"],
            {c: list(ic.globs) for c, ic in sorted(CORE.input_classes(pack).items())},
        )

    def test_no_corpus_golden_captures_the_manifest(self):
        """Why no corpus golden moves for this: the golden set is five files and
        `_meta/manifest.json` is not one of them. Read from the harness rather
        than restated, so a golden set that grew would fail here."""
        sys.path.insert(0, str(SCRIPTS / "tests" / "arch-corpus"))
        dc = importlib.import_module("derive_corpus")
        self.assertNotIn(CORE.MANIFEST_REL, dc.GOLDEN_FILES)
        self.assertEqual(len(dc.GOLDEN_FILES), 5)


# ───────────────────────── the module's own contract ────────────────────────


class ModuleContractTests(unittest.TestCase):

    def test_the_docstring_documents_the_error_in_both_directions(self):
        doc = (STALE.__doc__ or "").lower()
        self.assertIn("false", doc, "the false-positive direction is undocumented")
        self.assertIn("silent", doc, "the silent direction is undocumented")
        for word in ("shallow", "squash", "same commit", "working tree"):
            self.assertIn(word, doc, f"the docstring does not name {word!r}")

    def test_every_git_call_pins_both_config_layers(self):
        """A `~/.gitconfig` can rewrite what git does; both layers point at
        /dev/null exactly as the corpus fetcher's `_git` does."""
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        runs = [n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "run"]
        self.assertEqual(len(runs), 1,
                         "git is invoked from more than one place; pin them all")
        env = [kw for kw in runs[0].keywords if kw.arg == "env"]
        self.assertEqual(len(env), 1, "the git call inherits the ambient environment")
        keys = {k.value for k in env[0].value.keys if isinstance(k, ast.Constant)}
        self.assertLessEqual({"GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"}, keys)

    def test_the_git_commands_are_read_only(self):
        """The advisory reads the commit graph and writes nothing to it."""
        src = MODULE.read_text(encoding="utf-8")
        for verb in ("commit", "checkout", "reset", "clean", "fetch", "push",
                     "clone", "gc", "prune", "add"):
            self.assertNotIn(f'"{verb}"', src, f"a write-side git verb: {verb}")

    def test_it_is_not_on_the_derive_path(self):
        """The recorded channel is git-free. `core.py`, `derive.py` and the
        packs must not import this module, because a byte-compared tree that
        varied with the commit graph would flap across a fresh clone."""
        arch = SCRIPTS / "crux" / "arch"
        modules = [arch / "core.py", arch / "derive.py",
                   *sorted((arch / "packs").glob("*.py"))]
        offenders: list[str] = []
        for m in modules:
            # By AST, not by grep: the word appears in derive-path PROSE (the
            # dry-run docstring names stale-artifact detection), and prose is
            # not an import. The claim is about the import graph.
            for node in ast.walk(ast.parse(m.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names = {a.name for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    names = {node.module or ""} | {a.name for a in node.names}
                else:
                    continue
                if any("staleness" in n for n in names):
                    offenders.append(m.name)
        self.assertEqual(offenders, [], "the derive path imports the staleness module")


if __name__ == "__main__":
    unittest.main()
