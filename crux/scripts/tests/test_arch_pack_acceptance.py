"""ADR-0096 clause 8's acceptance postconditions for the arch stack packs.

Clause 8 makes `arch-corpus/corpus.yml`'s **expectation record** the acceptance
gate, and states five postconditions. Four of them live here; (c) — the pack
enrollment assertion — lives in `tools/tests/test_schema_invariants.py` beside
`RegeneratorEnrollmentTests`, because that is where this repository keeps its
enrollment invariants.

    (a) test_a_every_repository_matches_its_expectation_record
    (b) test_b_every_stubbed_expectation_names_a_surface_or_a_debt
    (c) ArchPackEnrollmentTests            [tools/tests/test_schema_invariants.py]
        — not met: declaration half only; the read path is not asserted.
    (d) test_d_stub_lines_match_the_record
    (e) test_e_twenty_edits_outside_declared_inputs_do_not_drift

**Read (c) exactly as narrow as it is.** `ArchPackEnrollmentTests` asserts that
every pack DECLARES its inputs — that the declaration exists, is well-formed and
is enrolled. It does not assert that any extractor READS through the declaration
it made. Nothing here closes that gap: an internal review looked for a probe
sitting behind a core-side reader and found none. `core._safe_read_bytes` is a
helper the probes CALL, not a chokepoint they must pass through, so a probe that
opened a file directly would satisfy (c) and still read outside its declaration.
Postcondition (e) constrains the consequence from the other side — an edit
outside every declared input must not move the tree — but it is a sample of
twenty files, not a proof about the read path. Stated rather than softened.

**What the record is, and what it deliberately is not.** No assertion in this
file appeals to what a repository "genuinely has". The record states what the
extractor CURRENTLY PRODUCES; where that is a debt rather than the repository's
truth, the record's `because:` is where the debt is named, and (b) is what makes
naming it mandatory. That is the whole mechanism by which (a) can hold while the
GraphQL and Mongoose probes remain follow-on work.

**The record is not self-defending.** An expectation edited to match a
regression passes (a) exactly as a careless re-bless passes the golden gate.
Editing an expectation is a reviewed edit, and nothing here catches a careless
one — stated rather than implied.

Which tests need the corpus cache:

  * (a) re-derives the twelve pinned clones and is SKIPPED when the cache is
    absent, for the reason `test_arch_corpus.py` gives: the only honest verdict
    without the corpus is "not measured".
  * (b) reads the record only, (d) reads the committed goldens only, and (e)
    builds its own scratch fixture repository. All three run on any machine
    with no network and no cache.

**The self-hosted entry, and why it is gated differently.** `crux-repo` carries
`source: self` (ADR-0096 clause 12): it derives THIS checkout in place under a
forced `arch_stack: python`, with no clone and no network, so
`ClauseEightSelfHostedTests` NEVER skips. It carries no `golden/<name>/`, and
that is a decision rather than an omission — this checkout is not frozen at a
SHA, so a byte golden for it would go red on every future edit under
`crux/scripts/`, and `CorpusGoldenTests` skips whenever the third-party cache is
absent, so such a golden would rot unseen. **The accepted cost, stated: there is
no byte-level regression detection for this entry.** What IS gated is the
expectation record — the resolved pack, the `packages` set, the per-concern
verdicts, the `min_entities` floors and the stub reasons — plus (d)'s stub
lines, which for this entry are read from the live derive rather than a golden.

(e) never runs against a corpus clone. It writes 20 files, and writing into a
third-party checkout to measure a gate would be measuring a tree nobody pinned.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath

_GOLDEN_INTERPRETER = (3, 13)
_GOLDEN_SKIP = (
    "the corpus goldens are derived on the pinned 3.13 interpreter; the 3.11 parser "
    "refuses djangoproject-com/checklists/models.py, so the pack names it as a residual "
    "there and the spine cannot match byte for byte"
)

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
CORPUS_DIR = TESTS_DIR / "arch-corpus"
CACHE = CORPUS_DIR / ".cache"
GOLDEN = CORPUS_DIR / "golden"

sys.path.insert(0, str(SCRIPTS_DIR))

from crux.arch import core  # noqa: E402

CONCERNS = ("data-model", "api-surface", "module-graph", "decision-index")
STUB_REASONS = frozenset(r.value for r in core.StubReason)

#: (b)'s closed classification. Clause 8 says a `stubbed` expectation must name
#: "either a surface the repository does not have or an open follow-on probe" —
#: two kinds, so the check is that the `because:` opens by declaring which kind
#: it is. A free-text field with no required classification would let "because
#: it does" satisfy the postcondition.
BECAUSE_KINDS = (
    "a surface the repository does not have",
    "an open follow-on probe",
)

#: Enough prose to have said something after the classifying lead phrase.
BECAUSE_MIN_CHARS = 80

STUB_LINE_RE = re.compile(r"^>\s*_stub:\s*([a-z_]+)\s+—")

FETCH_HINT = (
    "run `uv run python3 crux/scripts/tests/arch-corpus/fetch.py` to fetch the "
    "pinned corpus into crux/scripts/tests/arch-corpus/.cache/"
)


def _load(name: str):
    """Import a module from `arch-corpus/` by path — the directory name carries
    a hyphen, so it is not an importable package. Same helper as
    `test_arch_corpus.py`, duplicated rather than shared because importing one
    test module from another couples their collection order."""
    spec = importlib.util.spec_from_file_location(
        f"_arch_acceptance_{name}", CORPUS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


try:
    _fetch = _load("fetch")
    _derive_corpus = _load("derive_corpus")
    _IMPORT_ERROR = None
except Exception as exc:                                # noqa: BLE001
    _fetch = _derive_corpus = None
    _IMPORT_ERROR = exc


def _manifest() -> list[dict]:
    if _IMPORT_ERROR is not None:
        raise unittest.SkipTest(f"arch-corpus helpers not importable: {_IMPORT_ERROR}")
    return _fetch.load_manifest()


def _expectation(entry: dict) -> dict:
    """The `expect` block, which `fetch.load_manifest` has already shape-checked."""
    return entry["expect"]


def _is_self_hosted(entry: dict) -> bool:
    """True for a `source: self` entry. Carve-outs in this file key on this
    FIELD and never on an entry's name, so a renamed or a second self-hosted
    entry is handled by the same predicate."""
    return entry.get("source") == "self"


#: Memo for the self-hosted derive. (a)'s self case and both halves of (d) need
#: the same tree, and the derive walks the whole repository; deriving it once
#: also guarantees the three read ONE measurement rather than three that could
#: disagree if a file changed between them.
_SELF_DERIVE: dict[str, dict] = {}


def _self_derive(entry: dict) -> dict:
    """Derive a `source: self` entry live. Returns `derive_one`'s result.

    This is what stands in for `golden/<name>/` everywhere in this file. The
    entry has no golden by design (see the module docstring), so a gate that
    would have read committed bytes reads this fresh derive instead.
    """
    name = entry["name"]
    if name not in _SELF_DERIVE:
        _SELF_DERIVE[name] = _derive_corpus.derive_one(
            name, _derive_corpus.REPO_ROOT, arch_stack=entry["arch_stack"])
    return _SELF_DERIVE[name]


def _detected_packages(pack_name: str, root: Path) -> list[str]:
    """The package set the RESOLVED pack's package detection reports.

    Package detection is landed for the python pack and the swift pack. The
    swift pack reports its container set: every `Package.swift` and every
    `*.xcodeproj` holding `project.pbxproj` the Swift walk reaches, by
    repo-relative marker path. Every other pack reports the empty list. That is
    a statement about the pack, not about the repository, and the record says so.
    """
    if pack_name == "swift":
        from crux.arch.packs import swift as swift_pack   # noqa: PLC0415
        return list(swift_pack.detect_containers(root))
    if pack_name != "python":
        return []
    from crux.arch.packs import python as python_pack   # noqa: PLC0415
    return [name for _dir, name in python_pack.detect_packages(root)]


def _compare_to_record(entry: dict, report: dict, packages: list[str]) -> list[str]:
    """Postcondition (a)'s comparison for ONE entry. Returns every mismatch.

    Shared by the cached-clone case and the self-hosted case so the two are
    measured by identical arithmetic — a self entry checked by a second, looser
    copy of this logic would be a carve-out hiding inside a helper.

    `min_entities` is a FLOOR, per the record's own header: the count is a
    moving measurement, and pinning it to equality would turn every extraction
    improvement into a test failure. That is the golden gate's job, not this one's
    — and for the self entry there is no golden gate, which is the cost the
    module docstring names.
    """
    name = entry["name"]
    expect = _expectation(entry)
    problems: list[str] = []

    if report["detected_pack"] != expect["pack"]:
        problems.append(f"{name}: resolved pack {report['detected_pack']!r} != "
                        f"record {expect['pack']!r}")
    if sorted(packages) != sorted(expect["packages"]):
        problems.append(f"{name}: package set {sorted(packages)} != record "
                        f"{sorted(expect['packages'])}")

    verdicts = {c["concern"]: c for c in report["concerns"]}
    for concern in CONCERNS:
        rec = expect["concerns"][concern]
        actual = verdicts.get(concern)
        if actual is None:
            problems.append(f"{name}/{concern}: no derived verdict")
            continue
        if "stubbed" in rec:
            if actual["verdict"] != "stubbed":
                problems.append(f"{name}/{concern}: derived {actual['verdict']!r}, "
                                f"record says stubbed")
            elif actual["stub_reason"] != rec["stubbed"]["reason"]:
                problems.append(
                    f"{name}/{concern}: derived stub reason {actual['stub_reason']!r} != "
                    f"record {rec['stubbed']['reason']!r}")
        else:
            floor = rec["populated"]["min_entities"]
            if actual["verdict"] != "populated":
                problems.append(
                    f"{name}/{concern}: derived {actual['verdict']!r}"
                    + (f"/{actual['stub_reason']}" if actual["stub_reason"] else "")
                    + f", record says populated (>= {floor})")
            elif (actual["n_entities"] or 0) < floor:
                problems.append(
                    f"{name}/{concern}: {actual['n_entities']} entities is below the "
                    f"recorded floor of {floor}")
    return problems


def _outside_every_declared_input(rel: str, globs: tuple[str, ...]) -> bool:
    """True iff the repo-relative path `rel` matches none of `globs`.

    Two matchers, and a path is "inside" if EITHER says so. `PurePosixPath.match`
    is right-anchored (so `*.py` catches `pkg/mod.py`) while `fnmatch` is
    whole-string (so `**/*.py` catches it too). Taking the union over-approximates
    the declared input set, which is the safe direction: it can only shrink (e)'s
    edit set, never admit a file a probe might read.
    """
    p = PurePosixPath(rel)
    for g in globs:
        if fnmatch.fnmatchcase(rel, g) or fnmatch.fnmatchcase(rel, g.replace("**/", "")):
            return False
        try:
            if p.match(g):
                return False
        except ValueError:                              # pragma: no cover — bad glob
            return False
    return True


class ClauseEightRecordTests(unittest.TestCase):
    """(b) and the record's shape. Cache-free: these run everywhere."""

    def test_every_repository_carries_a_complete_expectation_record(self):
        """The record covers all thirteen entries and all four concerns each.

        A partially-written record would let (a) and (d) pass by iterating over
        the entries that happen to have one.
        """
        problems: list[str] = []
        for entry in _manifest():
            name = entry["name"]
            expect = entry.get("expect")
            if not isinstance(expect, dict):
                problems.append(f"{name}: no `expect` block")
                continue
            if not isinstance(expect.get("pack"), str) or not expect["pack"]:
                problems.append(f"{name}: `expect.pack` is missing or not a string")
            if not isinstance(expect.get("packages"), list):
                problems.append(f"{name}: `expect.packages` is missing or not a list")
            concerns = expect.get("concerns")
            if not isinstance(concerns, dict):
                problems.append(f"{name}: `expect.concerns` is missing or not a mapping")
                continue
            self.assertIsInstance(concerns, dict)
            for concern in CONCERNS:
                rec = concerns.get(concern)
                if not isinstance(rec, dict):
                    problems.append(f"{name}/{concern}: no expectation")
                    continue
                has_pop, has_stub = "populated" in rec, "stubbed" in rec
                if has_pop == has_stub:
                    problems.append(
                        f"{name}/{concern}: exactly one of `populated`/`stubbed` required")
                    continue
                if has_pop:
                    floor = rec["populated"].get("min_entities")
                    if not isinstance(floor, int) or floor < 1:
                        problems.append(
                            f"{name}/{concern}: `min_entities` must be an int >= 1")
                else:
                    reason = rec["stubbed"].get("reason")
                    if reason not in STUB_REASONS:
                        problems.append(
                            f"{name}/{concern}: reason {reason!r} is outside the closed "
                            f"set {sorted(STUB_REASONS)}")
            extra = set(concerns) - set(CONCERNS)
            if extra:
                problems.append(f"{name}: unknown concern(s) {sorted(extra)}")
        self.assertEqual(problems, [], "expectation record is incomplete:\n  "
                                       + "\n  ".join(problems))

    def test_b_every_stubbed_expectation_names_a_surface_or_a_debt(self):
        """POSTCONDITION (b). Every `stubbed` expectation carries a `because:`
        naming either a surface the repository does not have or an open
        follow-on probe.

        This is where the GraphQL and Mongoose debts are carried.
        Declaring them is exactly what lets (a) hold while those probes remain
        follow-on work — without (b), a `stubbed` expectation is indistinguishable
        from a blind extractor, and the record would launder the debt. The
        Django-ORM debt clause 8 also named was discharged in dev loop 2 and is
        no longer carried here.
        """
        problems: list[str] = []
        for entry in _manifest():
            name = entry["name"]
            for concern, rec in _expectation(entry)["concerns"].items():
                if "stubbed" not in rec:
                    continue
                because = rec.get("because")
                if not isinstance(because, str) or not because.strip():
                    problems.append(f"{name}/{concern}: stubbed with no `because:`")
                    continue
                text = " ".join(because.split())
                lead = text.lower()
                if not any(lead.startswith(k) for k in BECAUSE_KINDS):
                    problems.append(
                        f"{name}/{concern}: `because:` must open by naming which kind of "
                        f"gap it is — one of {list(BECAUSE_KINDS)} — got {text[:60]!r}")
                elif len(text) < BECAUSE_MIN_CHARS:
                    problems.append(
                        f"{name}/{concern}: `because:` classifies the gap but says nothing "
                        f"about it ({len(text)} chars, floor {BECAUSE_MIN_CHARS})")
        self.assertEqual(problems, [], "postcondition (b) violated:\n  "
                                       + "\n  ".join(problems))

    def test_d_stub_lines_match_the_record(self):
        """POSTCONDITION (d). Every stub line in every corpus golden matches its
        record's reason, and "no extractor for this stack" appears only for
        `unsupported_stack`.

        Read against the COMMITTED goldens, not against a fresh derive: (a)
        already covers the live extractor, and (d)'s subject is the artifact a
        reviewer reads in a diff.

        With ONE exception, and it is a carve-out by field rather than by name:
        a `source: self` entry has no golden to read (see the module docstring),
        so its stub lines come from the live derive. That is the weaker of the
        two — nobody reviews those bytes in a diff — and it is the cost this
        entry pays for not being frozen at a SHA.
        """
        problems: list[str] = []
        for entry in _manifest():
            name = entry["name"]
            concerns = _expectation(entry)["concerns"]
            tree = _self_derive(entry)["tree"] if _is_self_hosted(entry) else None
            for concern in CONCERNS:
                if tree is not None:
                    text = tree[f"{concern}.md"]
                else:
                    path = GOLDEN / name / f"{concern}.md"
                    if not path.is_file():
                        problems.append(f"{name}/{concern}.md: no golden")
                        continue
                    text = path.read_text(encoding="utf-8")
                found = [m.group(1) for line in text.splitlines()
                         if (m := STUB_LINE_RE.match(line.strip()))]
                rec = concerns[concern]
                if "stubbed" in rec:
                    expected = rec["stubbed"]["reason"]
                    if found != [expected]:
                        problems.append(
                            f"{name}/{concern}.md: record says stubbed/{expected}, golden "
                            f"carries stub reason(s) {found}")
                elif found:
                    problems.append(
                        f"{name}/{concern}.md: record says populated, golden carries a "
                        f"stub line ({found})")

                # The reserved sentence, wherever it appears in the file.
                for line in text.splitlines():
                    if core.UNSUPPORTED_STACK_SENTENCE not in line:
                        continue
                    m = STUB_LINE_RE.match(line.strip())
                    if m is None or m.group(1) != core.StubReason.UNSUPPORTED_STACK.value:
                        problems.append(
                            f"{name}/{concern}.md: {core.UNSUPPORTED_STACK_SENTENCE!r} "
                            f"appears outside an `unsupported_stack` stub line: {line.strip()!r}")
        self.assertEqual(problems, [], "postcondition (d) violated:\n  "
                                       + "\n  ".join(problems))

    def test_d_coverage_goldens_carry_the_recorded_reason(self):
        """(d)'s other half: the same reason in the recorded channel.

        The stub LINE is prose a renderer produces; `coverage.json`'s
        `stub_reason` is the machine-readable one every downstream gate reads.
        They are two renderings of one verdict, and a record that matched only
        the prose would leave the channel that matters unchecked.

        The `source: self` entry's coverage comes from the live derive, for the
        same reason its stub lines do: it has no golden.
        """
        problems: list[str] = []
        for entry in _manifest():
            name = entry["name"]
            if _is_self_hosted(entry):
                raw = _self_derive(entry)["tree"]["_meta/coverage.json"]
            else:
                cov_path = GOLDEN / name / "_meta" / "coverage.json"
                if not cov_path.is_file():
                    problems.append(f"{name}: no coverage golden")
                    continue
                raw = cov_path.read_text(encoding="utf-8")
            records = {r["concern"]: r for r in json.loads(raw)["concerns"]}
            for concern, rec in _expectation(entry)["concerns"].items():
                got = records.get(concern)
                if got is None:
                    problems.append(f"{name}/{concern}: absent from the coverage golden")
                    continue
                if "stubbed" in rec:
                    if got.get("verdict") != "stubbed":
                        problems.append(f"{name}/{concern}: coverage verdict "
                                        f"{got.get('verdict')!r}, record says stubbed")
                    elif got.get("stub_reason") != rec["stubbed"]["reason"]:
                        problems.append(
                            f"{name}/{concern}: coverage stub_reason "
                            f"{got.get('stub_reason')!r} != record "
                            f"{rec['stubbed']['reason']!r}")
                elif got.get("verdict") != "populated":
                    problems.append(f"{name}/{concern}: coverage verdict "
                                    f"{got.get('verdict')!r}, record says populated")
        self.assertEqual(problems, [], "postcondition (d) violated in coverage.json:\n  "
                                       + "\n  ".join(problems))


class ClauseEightDerivedVerdictTests(unittest.TestCase):
    """(a). Needs the fetched corpus; skipped, never faked, without it."""

    @classmethod
    def setUpClass(cls):
        # FETCHED entries only, filtered on the `source: self` field. The self
        # entry has no clone to gate on and is covered by
        # `ClauseEightSelfHostedTests` below; leaving it in `repos` would make
        # `test_uncached_entries_are_reported_not_hidden` report it as "not
        # measured" forever, which is false.
        cls.repos = [r for r in _manifest() if not _is_self_hosted(r)]
        cls.cached = [r for r in cls.repos if (CACHE / r["name"] / ".git").exists()]
        if not cls.cached:
            raise unittest.SkipTest(f"arch corpus cache absent — {FETCH_HINT}")

    @unittest.skipIf(sys.version_info < _GOLDEN_INTERPRETER, _GOLDEN_SKIP)
    def test_a_every_repository_matches_its_expectation_record(self):
        """POSTCONDITION (a), for the fetched clones.

        Three things are compared per repository, by `_compare_to_record`: the
        resolved pack, the detected package set, and the per-concern verdict.
        The self-hosted entry runs the SAME comparison in
        `ClauseEightSelfHostedTests`, which never skips.
        """
        problems: list[str] = []
        for entry in self.cached:
            root = CACHE / entry["name"]
            report = _derive_corpus.derive_one(entry["name"], root)["report"]
            problems += _compare_to_record(
                entry, report, _detected_packages(report["detected_pack"], root))
        self.assertEqual(problems, [], "postcondition (a) violated:\n  "
                                       + "\n  ".join(problems))

    def test_uncached_entries_are_reported_not_hidden(self):
        uncached = [r["name"] for r in self.repos if r not in self.cached]
        if uncached:
            self.skipTest(f"not fetched, so not measured: {', '.join(uncached)} — {FETCH_HINT}")


class ClauseEightSelfHostedTests(unittest.TestCase):
    """(a) and (d) for the `source: self` entry. NEVER skipped.

    ADR-0096 clause 12 keeps the `crux` pack's own corpus repository as this
    repository itself. That makes this the one corpus case that needs no clone,
    no network and no cache: it derives the checkout the tests are running in,
    with `arch_stack:` forcing the pinned language pack past this repository's
    own `crux` pin.

    It is also the one case with no byte golden, and the module docstring states
    the cost that buys: no byte-level regression detection for this entry. The
    gate is the expectation record — verdicts, `packages`, `min_entities` floors
    and stub reasons — read against a live derive.
    """

    def setUp(self):
        self.self_hosted = [e for e in _manifest() if _is_self_hosted(e)]
        # Not a skip. A manifest with no self entry means clause 12's corpus
        # entry was dropped, which is the failure this class exists to catch.
        self.assertNotEqual(
            self.self_hosted, [],
            "no `source: self` corpus entry; ADR-0096 clause 12 requires one "
            "(the `crux` pack's own corpus repository is this repository)")

    def test_the_self_hosted_entry_carries_no_clone_pin_and_forces_a_pack(self):
        """The shape `fetch.load_manifest` enforces, asserted again here so this
        class fails loudly rather than deriving something unintended: no `url`
        or `sha` to clone, and an `arch_stack:` that says which pack to force."""
        for entry in self.self_hosted:
            with self.subTest(repo=entry["name"]):
                self.assertIsNone(entry.get("url"), "a self-hosted entry clones nothing")
                self.assertIsNone(entry.get("sha"), "a self-hosted entry pins no commit")
                self.assertTrue(entry.get("arch_stack"),
                                "a self-hosted entry must pin `arch_stack:`; without it the "
                                "derive resolves this repository's own `crux` pack")
                self.assertFalse(
                    (GOLDEN / entry["name"]).exists(),
                    f"golden/{entry['name']}/ exists. A self-hosted entry is gated by its "
                    "expectation record, not by bytes — this checkout is not frozen at a "
                    "SHA, so a golden here rots. Delete it.")

    def test_a_the_self_hosted_entry_matches_its_expectation_record(self):
        """POSTCONDITION (a) for the self-hosted entry, via the same
        `_compare_to_record` the cached clones go through."""
        problems: list[str] = []
        for entry in self.self_hosted:
            report = _self_derive(entry)["report"]
            problems += _compare_to_record(
                entry, report,
                _detected_packages(report["detected_pack"], _derive_corpus.REPO_ROOT))
        self.assertEqual(problems, [], "postcondition (a) violated for the self-hosted "
                                       "entry:\n  " + "\n  ".join(problems))

    def test_the_forced_arch_stack_actually_overrides_this_repository_s_pin(self):
        """The positive control for the override. This repository's own
        `.bionic.yml` pins `arch_stack: crux`; if the force were a no-op the
        derive would resolve `crux`, and (a) above would fail with a pack
        mismatch rather than silently. Assert the mechanism directly, so a
        regression names itself instead of arriving as a confusing verdict diff.
        """
        for entry in self.self_hosted:
            with self.subTest(repo=entry["name"]):
                report = _self_derive(entry)["report"]
                self.assertEqual(report["detected_pack"], entry["arch_stack"],
                                 "the derive did not resolve the forced `arch_stack:`; "
                                 "this repository's own pin won instead")
                self.assertNotEqual(report["detected_pack"], "crux",
                                    "the self-hosted entry measured the `crux` pack, not "
                                    "the language pack it was added to exercise")


class ClauseEightDriftStabilityTests(unittest.TestCase):
    """(e). A scratch fixture repository, never a corpus clone.

    Clause 8 (e): over 20 consecutive edits to files outside every concern's
    declared input set, the drift gate reports drift ZERO times. Two properties
    the wording pins down, and both are load-bearing here:

      * **The edit set is derived from the declarations**, via
        `core.declared_input_globs`, and never from a gate outcome. An edit set
        chosen by "files the gate did not react to" would make the test a
        tautology.
      * **The oracle is the whole byte-compared tree** — every file the derive
        produces, `_meta/coverage.json` and `_meta/manifest.json` included — and
        NOT the four-file spine hash. A spine-hash oracle would miss exactly the
        two provenance files an over-broad `sources` scan moves first.

    `dry_run` excludes the VALUE of the manifest's `sources` from its own
    comparison, so this test additionally compares that file in full. If an edit
    outside every declared input moved `sources`, the gate would stay silent and
    the byte comparison would not.
    """

    EDITS = (
        "README.md", "CHANGELOG.md", "LICENSE", "Makefile", ".gitignore",
        ".editorconfig", "tox.ini", "conf/settings.ini", "data/values.csv",
        "data/sample.json", "notes/design.rst", "notes/manual.adoc",
        "web/index.html", "web/app.css", "assets/logo.svg", "bin/run.sh",
        "deploy/k8s.yaml", "bench/results.tsv", "vendor/blob.bin", "i18n/en.po",
    )

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="crux-arch-e-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "fixture"
        (self.root / "src" / "acme" / "sub").mkdir(parents=True)
        (self.root / "bionic").mkdir()
        (self.root / "pyproject.toml").write_text(
            '[project]\nname = "acme"\nversion = "0.1.0"\n', encoding="utf-8")
        (self.root / "src" / "acme" / "__init__.py").write_text(
            "from acme import service\n", encoding="utf-8")
        (self.root / "src" / "acme" / "service.py").write_text(
            "from acme.sub import store\n\n\ndef run():\n    return store.get()\n",
            encoding="utf-8")
        (self.root / "src" / "acme" / "sub" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "src" / "acme" / "sub" / "store.py").write_text(
            "def get():\n    return 1\n", encoding="utf-8")

    def _build_tree(self) -> dict[str, str]:
        return core._build(self.root, "bionic", "complete", None)

    def test_the_fixture_resolves_a_real_pack_and_extracts_something(self):
        """(e) proves nothing against a tree that is empty in every file: an
        empty tree cannot move. Assert the fixture derives real content before
        asserting that editing around it moves nothing."""
        self.assertEqual(core.detect_stack(self.root, None), "python")
        tree = self._build_tree()
        graph = tree["module-graph.md"]
        self.assertIn("-->", graph, "the fixture's module graph extracted no edge")

    def test_e_twenty_edits_outside_declared_inputs_do_not_drift(self):
        """POSTCONDITION (e)."""
        pack = core.detect_stack(self.root, None)
        globs = core.declared_input_globs(pack)

        # The edit set, derived from the declarations before a single derive runs.
        inside = [rel for rel in self.EDITS
                  if not _outside_every_declared_input(rel, globs)]
        self.assertEqual(inside, [], f"edit set is not outside {pack}'s declared inputs: "
                                     f"{inside} match {list(globs)}")
        self.assertEqual(len(self.EDITS), 20, "clause 8 (e) names twenty edits")

        core.derive(self.root, "bionic", "complete", None)
        baseline = self._build_tree()

        drifted_at: list[str] = []
        moved: list[str] = []
        for i, rel in enumerate(self.EDITS):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"edit {i} — outside every declared input set\n" * (i + 1),
                            encoding="utf-8")

            drift = core.dry_run(self.root, "bionic", "complete", None)
            if drift:
                drifted_at.append(f"edit {i} ({rel}): {drift}")

            # The independent oracle: the WHOLE tree, byte-compared, with the
            # manifest compared in full rather than through `_manifest_drifted`.
            now = self._build_tree()
            self.assertEqual(sorted(now), sorted(baseline),
                             f"edit {i} ({rel}) changed the derived tree's file set")
            for key in sorted(baseline):
                if now[key] != baseline[key]:
                    moved.append(f"edit {i} ({rel}): {key}")

        self.assertEqual(drifted_at, [],
                         "the drift gate reported drift on an edit outside every declared "
                         "input set:\n  " + "\n  ".join(drifted_at))
        self.assertEqual(moved, [],
                         "an edit outside every declared input set moved the byte-compared "
                         "tree:\n  " + "\n  ".join(moved))

    def test_an_edit_inside_a_declared_input_does_drift(self):
        """(e) is only meaningful if the gate can fire at all. One edit INSIDE
        the declared input set must move the tree — otherwise the twenty zeros
        above measure a dead gate rather than a stable one.

        The edit adds a module to the detected package, which is the smallest
        change that moves rendered CONTENT. A `.py` edit that changes no
        rendered byte moves only the manifest's `sources` hash, and `dry_run`
        excludes that value by design — so an edit chosen for being inside the
        globs alone would not have proved the gate alive.
        """
        core.derive(self.root, "bionic", "complete", None)
        self.assertEqual(core.dry_run(self.root, "bionic", "complete", None), [],
                         "the fixture drifted immediately after its own derive")
        (self.root / "src" / "acme" / "extra.py").write_text(
            "from acme import service\n\n\ndef more():\n    return service.run()\n",
            encoding="utf-8")
        self.assertNotEqual(core.dry_run(self.root, "bionic", "complete", None), [],
                            "adding a module to the detected package moved nothing — "
                            "the gate is dead")


if __name__ == "__main__":
    unittest.main()
