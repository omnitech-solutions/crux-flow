"""Golden tests for the real-repository arch corpus (Phase 1 baseline).

The stack packs are otherwise validated against synthetic fixtures of 9-15
files. This suite pins them against real open-source repositories, pinned by
commit SHA in `arch-corpus/corpus.yml` and fetched into the gitignored
`arch-corpus/.cache/` by `arch-corpus/fetch.py`.

Two classes, deliberately different in what they need:

  * `CorpusManifestTests` needs no cache and never skips. It validates the
    manifest and asserts every entry has a complete golden. This is the
    enrollment gate: adding a corpus entry without blessing its golden fails
    here, on any machine, with no network.

  * `CorpusGoldenTests` re-derives each cached repository and asserts the
    output is byte-identical to its golden. It is SKIPPED when the cache is
    absent, because the only honest verdict without the corpus is "not
    measured". A repository that IS cached but sits at the wrong commit
    FAILS rather than skips — that is a stale cache, not a missing one, and
    silently measuring the wrong tree is the failure this file exists to
    prevent.

One entry is not a clone. `crux-repo` carries `source: self` (ADR-0096 clause
12): it derives THIS checkout in place, with `arch_stack: python` forcing the
python pack past this repository's own `crux` pin. It has no `url`, no `sha`,
no `.cache/<name>/` and no `golden/<name>/`, so every golden-shaped assertion in
this file carves it out — always on the `source: self` FIELD, never on the name,
and always with `_assert_full_path` bounding the exemption to that one entry in
the same test. Its own gate is the expectation record in `corpus.yml`, checked
by `test_arch_pack_acceptance.py`'s never-skipped self case.

The goldens are baseline goldens: they record what the CURRENT packs produce,
warts (stubs, under-extraction) included. Their job is to make a refactor
provable — splitting the monolith must leave all 50 files byte-identical.

Re-bless deliberately, never incidentally:
    uv run python3 crux/scripts/tests/arch-corpus/fetch.py
    uv run python3 crux/scripts/tests/arch-corpus/derive_corpus.py --bless
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_GOLDEN_INTERPRETER = (3, 13)
_GOLDEN_SKIP = (
    "the corpus goldens are derived on the pinned 3.13 interpreter; the 3.11 parser "
    "refuses djangoproject-com/checklists/models.py, so the pack names it as a residual "
    "there and the spine cannot match byte for byte"
)

TESTS_DIR = Path(__file__).resolve().parent
CORPUS_DIR = TESTS_DIR / "arch-corpus"
CACHE = CORPUS_DIR / ".cache"
GOLDEN = CORPUS_DIR / "golden"

sys.path.insert(0, str(TESTS_DIR.parent))          # crux/scripts


def _load(name: str):
    """Import a module from `arch-corpus/` by path.

    The directory name contains a hyphen, so it is not an importable package;
    the two helpers there are loaded from their file paths instead.
    """
    spec = importlib.util.spec_from_file_location(f"_arch_corpus_{name}", CORPUS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


try:
    _fetch = _load("fetch")
    _derive_corpus = _load("derive_corpus")
    _IMPORT_ERROR = None
except Exception as exc:                            # noqa: BLE001
    _fetch = _derive_corpus = None
    _IMPORT_ERROR = exc

GOLDEN_FILES = (
    "data-model.md",
    "api-surface.md",
    "module-graph.md",
    "decision-index.md",
    "_meta/coverage.json",
)

FETCH_HINT = (
    "run `uv run python3 crux/scripts/tests/arch-corpus/fetch.py` to fetch the "
    "pinned corpus into crux/scripts/tests/arch-corpus/.cache/"
)

#: How many entries are FETCHED third-party clones. The corpus is thirteen
#: entries: these twelve, plus the one `source: self` entry (`crux-repo`, ADR-0096
#: clause 12), which derives this checkout in place and carries no
#: `golden/<name>/`.
#:
#: Every golden-shaped assertion below carves that one entry out, and each
#: carve-out obeys two rules that this constant exists to enforce:
#:
#:   * it keys on the `source: self` FIELD, never on the entry's name — a name
#:     match would exempt any future entry that happened to be called the same
#:     thing, and would not exempt a second self-hosted entry at all;
#:   * it asserts, in the same unit, that the twelve fetched entries still took the
#:     FULL path. A carve-out that silently widened — a manifest bug, a typo in
#:     the predicate, a refactor that inverted it — would otherwise turn the
#:     gate green by exempting everything it was built to check.
N_FETCHED = 12


def _manifest() -> list[dict]:
    if _IMPORT_ERROR is not None:
        raise unittest.SkipTest(f"arch-corpus helpers not importable: {_IMPORT_ERROR}")
    return _fetch.load_manifest()


def _is_self_hosted(entry: dict) -> bool:
    """True for a `source: self` entry. The ONE carve-out predicate in this
    file — by field, never by name."""
    return entry.get("source") == "self"


def _fetched(repos: list[dict]) -> list[dict]:
    """The entries that are fetched clones, i.e. everything but `source: self`."""
    return [r for r in repos if not _is_self_hosted(r)]


def _assert_full_path(case: unittest.TestCase, took: list[str],
                      repos: list[dict], what: str) -> None:
    """Assert the carve-out exempted the self-hosted entry AND NOTHING ELSE.

    `took` is the roster the calling test applied the carve-out to. It must be
    exactly the fetched entries, and there must still be `N_FETCHED` of them.
    This is the second half of every carve-out in this file: the exemption is
    only safe while it stays one entry wide.

    **This bounds the carve-out, not the coverage.** For the callers that walk
    every entry, the roster and what they checked are the same list. For the two
    that iterate `self.cached`, they are not: a caller passes the full fetched
    roster while its loop runs over whichever of those clones are present on
    this machine, and a partial cache makes that loop a strict subset. Passing
    `self.cached` instead would fold the two questions together and turn a
    partial cache into a carve-out failure, which is the wrong verdict — an
    absent clone is `CorpusGoldenTests`' skip to report, not a widened
    exemption. So the claim here is narrow on purpose: the manifest still holds
    `N_FETCHED` fetched entries and the exemption still covers exactly one.
    """
    expected = sorted(e["name"] for e in _fetched(repos))
    case.assertEqual(sorted(took), expected,
                     f"{what}: the `source: self` carve-out changed which entries take the "
                     f"full path — expected {expected}, got {sorted(took)}")
    case.assertEqual(len(expected), N_FETCHED,
                     f"{what}: {len(expected)} fetched entries, expected {N_FETCHED}. "
                     "Adding or removing a fetched corpus entry is a deliberate change; "
                     "update N_FETCHED with it.")


# ── prose guards over `corpus.yml` ───────────────────────────────────────────
#
# ADR-0096 clause 8 makes `corpus.yml` the acceptance gate, so its prose is an
# artifact under review rather than commentary. Twice in one dev loop a claim in
# that prose was contradicted by the extractor's own output — a repository's
# `structure.sql` attribution stated backwards, and a residual note still
# describing paths as un-prefixed after the prefix landed. The two guards below
# catch the cheap half of that class: a claim that NAMES A PATH. Neither reaches
# a claim that names no path, and there is no cheap guard for that half.

#: A `backticked` span in the prose. One line, no nesting.
_TOKEN = re.compile(r"`([^`\n]+)`")

#: Extensions that make a slash-bearing token a claim about a SOURCE FILE in the
#: pinned checkout. `.txt` is deliberately absent: `arch-inputs/routes.txt` is an
#: emitted artifact a shallow clone cannot carry, and naming it is not a claim
#: that it is there.
_SOURCE_EXT = frozenset(
    (".py", ".rb", ".js", ".jsx", ".ts", ".tsx", ".ex", ".exs",
     ".sql", ".json", ".yml", ".yaml", ".toml", ".md")
)


def _prose(entry: dict) -> list[str]:
    """Every human-authored field of one entry: `why:` and each `because:`."""
    out = [entry.get("why", "")]
    for record in entry["expect"]["concerns"].values():
        if isinstance(record, dict) and record.get("because"):
            out.append(record["because"])
    return out


def _extension(token: str) -> str:
    tail = token.rsplit("/", 1)[-1]
    return "." + tail.rsplit(".", 1)[-1] if "." in tail else ""


def _golden_path_cells(name: str) -> set[str]:
    """The `path` column of `golden/<name>/api-surface.md`, backticks stripped."""
    path = GOLDEN / name / "api-surface.md"
    if not path.is_file():
        return set()
    cells = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| "):
            continue
        row = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(row) == 3 and row[0] != "method" and not set(row[0]) <= {"-"}:
            cells.add(row[1].strip("`"))
    return cells


class CorpusManifestTests(unittest.TestCase):
    """Cache-free assertions. These run everywhere, including in CI with no
    network, and are what keeps a corpus entry from landing un-blessed."""

    def test_manifest_validates(self):
        repos = _manifest()
        self.assertGreaterEqual(len(repos), 8, "the corpus lost entries")
        packs = {r["pack"] for r in repos}
        self.assertEqual(packs, {"python", "node", "ruby", "elixir", "swift"},
                         "every language pack must have corpus entries")

    def test_every_pack_has_at_least_two_repositories(self):
        repos = _manifest()
        counts: dict[str, int] = {}
        for entry in repos:
            counts[entry["pack"]] = counts.get(entry["pack"], 0) + 1
        thin = sorted(p for p, n in counts.items() if n < 2)
        self.assertEqual(thin, [], f"packs with fewer than 2 corpus repos: {thin}")

    def test_every_entry_has_a_complete_golden(self):
        """Every FETCHED-CLONE entry, that is. A `source: self` entry is
        carved out by field, never by name: it derives this checkout live and
        is gated by `test_arch_pack_acceptance.py`'s never-skipped self case
        instead of a byte golden, so it carries no `golden/<name>/` and is not
        expected to.

        The carve-out is bounded in the same unit: `_assert_full_path` asserts
        the twelve fetched entries all still took the golden check.
        """
        repos = _manifest()
        missing, took = [], []
        for entry in repos:
            if _is_self_hosted(entry):
                continue
            took.append(entry["name"])
            for rel in GOLDEN_FILES:
                path = GOLDEN / entry["name"] / rel
                if not path.is_file() or not path.read_bytes():
                    missing.append(f"{entry['name']}/{rel}")
        self.assertEqual(missing, [], "un-blessed goldens: " + ", ".join(missing))
        _assert_full_path(self, took, repos, "test_every_entry_has_a_complete_golden")

    def test_no_orphan_golden_directories(self):
        """A `source: self` entry is excluded from `named` on purpose: it is
        never blessed a `golden/<name>/`, so if one ever appeared on disk it
        would be exactly the orphan this test exists to catch.

        Note which direction the carve-out runs here. It does not exempt the
        self entry from a check; it makes the check STRICTER for it, and the
        `_assert_full_path` call still pins the twelve fetched names that are
        allowed a directory.
        """
        repos = _manifest()
        named = {e["name"] for e in _fetched(repos)}
        on_disk = {p.name for p in GOLDEN.iterdir() if p.is_dir()} if GOLDEN.exists() else set()
        self.assertEqual(on_disk - named, set(),
                         "golden directories with no corpus entry")
        _assert_full_path(self, sorted(named), repos, "test_no_orphan_golden_directories")

    def test_backticked_route_paths_are_rendered_in_the_golden(self):
        """A backticked token starting with `/` claims the golden renders that
        route; assert the path cell is there.

        Cache-free on purpose, so it never skips: it reads `corpus.yml` and
        `golden/` only, and runs in CI with no network and no clones.

        WHAT IT DOES NOT REACH. It checks paths, and only literal ones. A token
        holding a glob (`*`) or a template hole (`${...}`) is a pattern, not a
        path, and is skipped; so is a trailing-ellipsis abbreviation and any
        token whose last segment carries a source extension (that is a filename,
        and the sibling guard takes it). Nothing here reaches a claim that names
        no path at all — this file's header comment, the arithmetic inside a
        `because:`, a count of corrected rows. There is no cheap guard for that
        class, and none is attempted.

        The convention this enforces: a route path the golden does NOT render is
        written WITHOUT backticks. `corpus.yml`'s header, "THE BACKTICK
        CONVENTION", states it; `fastapi-fullstack`'s api-surface note and
        `rubygems-org`'s retracted `structure.sql` aside both point back at it
        rather than re-deriving the rule inline.

        A `source: self` entry is skipped, by field and never by name: it
        carries no `golden/<name>/`, so there is no rendered path column to
        check it against. `_assert_full_path` bounds that exemption to the one
        entry in the same unit.
        """
        repos = _manifest()
        wrong, took = [], []
        for entry in repos:
            if _is_self_hosted(entry):
                continue
            took.append(entry["name"])
            rendered = _golden_path_cells(entry["name"])
            for text in _prose(entry):
                for token in _TOKEN.findall(text):
                    if not token.startswith("/") or token.endswith("..."):
                        continue
                    if "*" in token or "${" in token:
                        continue
                    if _extension(token) in _SOURCE_EXT:
                        continue
                    if token not in rendered:
                        wrong.append(f"{entry['name']}: `{token}`")
        self.assertEqual(
            wrong, [],
            "corpus.yml backticks a route path its golden does not render: "
            + "; ".join(wrong)
            + ". A backticked route path asserts the golden renders it verbatim; "
              "for a path the extractor does NOT produce, drop the backticks.",
        )
        _assert_full_path(self, took, repos,
                          "test_backticked_route_paths_are_rendered_in_the_golden")


def _enrolled_facts_paths(repos: list[dict]) -> set[str]:
    """Every `expect.facts.path` a corpus entry names, repo-relative to
    `arch-corpus/` (e.g. `expectations/netnewswire.yml`)."""
    return {
        e["expect"]["facts"]["path"]
        for e in repos
        if isinstance(e.get("expect"), dict) and isinstance(e["expect"].get("facts"), dict)
    }


def _unenrolled_expectations_files(present: set[str], enrolled: set[str]) -> list[str]:
    """Every expectations FILENAME in `present` that no path in `enrolled` names.

    Pure and cache-free, so a positive control can drive it directly with no
    filesystem write, real or temporary.
    """
    return sorted(present - enrolled)


class CorpusFactsEnrollmentTests(unittest.TestCase):
    """Every file under `expectations/` is enrolled by exactly one corpus
    entry's `facts:` (path + sha256) — ADR-0130 clause 18.

    An expectations file no `facts:` block names is dead weight: neither
    `test_arch_facts.py`'s digest gate nor its matcher gate ever reaches a
    file nothing points at, so an unenrolled file passes every gate in this
    suite silently. This is the enrollment half; `test_arch_facts.py`'s
    `CorpusFactsDigestTests` is the content half (the digest a `facts:` block
    DOES name matches the file's bytes).

    Cache-free: reads `corpus.yml` and the `expectations/` directory listing
    only, so it runs everywhere with no network and no clones.
    """

    def test_every_expectations_file_is_enrolled(self):
        present = {p.name for p in (CORPUS_DIR / "expectations").glob("*.yml")}
        enrolled = {Path(p).name for p in _enrolled_facts_paths(_manifest())}
        missing = _unenrolled_expectations_files(present, enrolled)
        self.assertEqual(
            missing, [],
            "expectations file(s) with no corpus entry `facts:` block naming them: "
            + ", ".join(missing) + " — add a `facts: {path, sha256}` block to the "
            "entry that should enroll it, or delete the stray file.",
        )

    def test_positive_control_a_stray_expectations_file_is_reported(self):
        """The check fires on a file present but named by no `facts:` block."""
        present = {"netnewswire.yml", "swift-argument-parser.yml", "stray.yml"}
        enrolled = {Path(p).name for p in {
            "expectations/netnewswire.yml", "expectations/swift-argument-parser.yml",
        }}
        missing = _unenrolled_expectations_files(present, enrolled)
        self.assertEqual(missing, ["stray.yml"])


class CorpusLicenseTests(unittest.TestCase):
    """Only permissively licensed repositories may enter the corpus.

    Their derived facts are committed under `crux/**` and ship publicly, so a
    copyleft or unknown license is refused by manifest validation, before any
    fetch. Cache-free: these read the committed manifest and temp copies only.
    """

    def setUp(self):
        if _fetch is None:
            self.skipTest("arch-corpus helpers need PyYAML; run under `uv run`")
        self.text = (CORPUS_DIR / "corpus.yml").read_text(encoding="utf-8")

    def _load_with_license(self, value: str):
        import yaml
        doc = yaml.safe_load(self.text)
        entry = next(e for e in doc["repos"] if e.get("source") != "self")
        entry["license"] = value
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corpus.yml"
            path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
            return _fetch.load_manifest(path)

    def test_every_committed_entry_is_permissive(self):
        repos = _fetch.load_manifest()
        self.assertTrue(repos)
        for entry in repos:
            with self.subTest(repo=entry["name"]):
                self.assertIn(entry["license"], _fetch.PERMISSIVE_LICENSES)

    def test_a_copyleft_license_is_refused(self):
        for value in ("AGPL-3.0", "GPL-3.0", "LGPL-2.1", "SSPL-1.0", "NOASSERTION"):
            with self.subTest(license=value):
                with self.assertRaises(_fetch.ManifestError) as ctx:
                    self._load_with_license(value)
                self.assertIn("is not permissive", str(ctx.exception))

    def test_positive_control_a_permissive_license_loads(self):
        self.assertTrue(self._load_with_license("Apache-2.0"))


class CorpusGoldenTests(unittest.TestCase):
    """Re-derive every cached repository; assert byte-equality with its golden.

    Skipped, never faked, when the corpus has not been fetched.
    """

    @classmethod
    def setUpClass(cls):
        # `repos` is the FETCHED entries only, and the filter is the explicit
        # `source: self` predicate rather than "has no .cache/<name>/.git".
        # Both would exclude the self entry today, but only the explicit one
        # says why — and only the explicit one keeps
        # `test_uncached_entries_are_reported_not_hidden` honest, which would
        # otherwise report the self entry as "not fetched, so not measured"
        # forever, when in fact it is measured, live, in
        # `test_arch_pack_acceptance.py`'s never-skipped self case.
        cls.manifest = _manifest()
        cls.repos = _fetched(cls.manifest)
        cls.cached = [r for r in cls.repos if (CACHE / r["name"] / ".git").exists()]
        if not cls.cached:
            raise unittest.SkipTest(f"arch corpus cache absent — {FETCH_HINT}")

    def test_cached_checkouts_are_at_their_pinned_sha(self):
        """A stale cache is a failure, not a skip: measuring the wrong tree
        against a golden proves nothing about either.

        A `source: self` entry has no `sha` to be stale against — it derives
        this checkout, which is not frozen at a commit — so it is carved out,
        by field. The assertion below bounds that: every entry this test sees
        must be a fetched one, and the twelve of them are what `self.repos` holds.
        """
        self.assertEqual([e["name"] for e in self.repos if _is_self_hosted(e)], [],
                         "a `source: self` entry reached the pinned-SHA check; it has no "
                         "`sha` to check against")
        _assert_full_path(self, [e["name"] for e in self.repos], self.manifest,
                          "test_cached_checkouts_are_at_their_pinned_sha")
        wrong = []
        for entry in self.cached:
            repo = CACHE / entry["name"]
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                                  capture_output=True, text=True).stdout.strip()
            if head != entry["sha"]:
                wrong.append(f"{entry['name']}: HEAD {head[:12]} != pinned {entry['sha'][:12]}")
        self.assertEqual(wrong, [], "stale corpus cache; re-run fetch.py — " + "; ".join(wrong))

    @unittest.skipIf(sys.version_info < _GOLDEN_INTERPRETER, _GOLDEN_SKIP)
    def test_derived_spine_matches_the_golden(self):
        """Byte-equality, with the SCOPE of the claim in the verdict.

        The loop runs over `self.cached`, which is whichever clones are present
        on this machine. With 1 of the 10 repos fetched it reported PASS over 5
        comparisons — and the gap was named only by a DIFFERENT test, as a skip.
        A run record then cited "50/50 goldens byte-identical", which is a claim
        this test did not make.

        Two things fix that. `_assert_full_path` bounds the carve-out to the
        self-hosted entry, and every comparison count goes into the failure
        message, so a partial-cache PASS cannot be read as a full-corpus PASS.
        """
        _assert_full_path(self, [e["name"] for e in self.repos], self.manifest,
                          "test_derived_spine_matches_the_golden")
        scope = (f"{len(self.cached)} of {len(self.repos)} fetched repos cached "
                 f"on this machine ({', '.join(e['name'] for e in self.cached)})")
        compared = 0
        for entry in self.cached:
            name = entry["name"]
            got = _derive_corpus.derive_one(name, CACHE / name)["tree"]
            for rel in GOLDEN_FILES:
                # One subTest per (repo, file), not per repo. A failing
                # assertion exits its subTest block, so a repo-wide subTest
                # skipped every remaining file of the first repo that moved —
                # under-reporting the diff AND under-counting the roster check
                # below. Per-file, all `len(cached) * len(GOLDEN_FILES)`
                # comparisons are attempted and each moved file reports itself.
                with self.subTest(repo=name, file=rel):
                    # The base golden, or this interpreter's declared overlay
                    # (see `InterpreterOverlayTests` for what bounds it).
                    golden = _derive_corpus.golden_path(name, rel)
                    compared += 1
                    self.assertTrue(golden.is_file(), f"{name}/{rel}: no golden")
                    self.assertEqual(
                        got[rel], golden.read_text(encoding="utf-8"),
                        f"{name}/{rel} moved against "
                        f"{golden.relative_to(CORPUS_DIR)} [scope: {scope}]. If the change is "
                        "intended, re-bless with `uv run python3 "
                        "crux/scripts/tests/arch-corpus/derive_corpus.py --bless` "
                        "and review the diff; a pure refactor must not move a single byte.",
                    )
        # The verdict names its own scope. `setUpClass` skips on an empty
        # cache, so reaching here with zero comparisons would mean the golden
        # file list — not the cache — had emptied out underneath the loop.
        self.assertEqual(
            compared, len(self.cached) * len(GOLDEN_FILES),
            f"byte-comparison count does not match the roster [scope: {scope}]")
        self.assertGreater(
            compared, 0,
            f"no golden was byte-compared [scope: {scope}]; GOLDEN_FILES is empty")

    def test_backticked_source_files_exist_in_the_pinned_checkout(self):
        """A backticked token holding a `/` and a source extension claims that
        file is in the pinned tree; assert `.cache/<name>/<path>` exists.

        Lives here, not in `CorpusManifestTests`, because it needs the clones —
        so it SKIPS with the cache absent, exactly as the golden comparison
        does. The cache-free route guard is the one that always runs.

        WHAT IT DOES NOT REACH. It checks existence, nothing else: that a named
        file is there, never that the sentence around it says anything true
        about the file's contents. It skips a token holding a glob (`*`) and a
        token whose extension is not in `_SOURCE_EXT`. A sentence that DENIES a
        file — "a `db/schema.rb` and NO `db/structure.sql`" — is exempted by the
        `no `/`NO ` immediately before the token, which is a crude test and the
        only negation form this file uses. And, as with its sibling, nothing
        here reaches a prose claim that names no path.

        A `source: self` entry is carved out by field: `self.cached` is built
        from `self.repos`, which `setUpClass` filters on `source: self` and not
        on a name. There is no `.cache/<name>/` to resolve its paths against —
        its prose names paths in THIS checkout. The assertion below bounds the
        exemption to that one entry.
        """
        self.assertEqual([e["name"] for e in self.cached if _is_self_hosted(e)], [],
                         "a `source: self` entry reached the pinned-checkout guard; it has "
                         "no `.cache/<name>/` to resolve paths against")
        _assert_full_path(self, [e["name"] for e in self.repos], self.manifest,
                          "test_backticked_source_files_exist_in_the_pinned_checkout")
        missing = []
        for entry in self.cached:
            root = CACHE / entry["name"]
            for text in _prose(entry):
                flat = " ".join(text.split())
                for match in _TOKEN.finditer(flat):
                    token = match.group(1)
                    if "/" not in token or "*" in token:
                        continue
                    if _extension(token) not in _SOURCE_EXT:
                        continue
                    if flat[max(0, match.start() - 4):match.start()].lower().endswith("no "):
                        continue                      # a denial, not a claim
                    if not (root / token.lstrip("/")).exists():
                        missing.append(f"{entry['name']}: `{token}`")
        self.assertEqual(
            missing, [],
            "corpus.yml names a source file the pinned checkout does not have: "
            + "; ".join(missing)
            + ". Either the path is wrong or the SHA moved under it.",
        )

    def test_uncached_entries_are_reported_not_hidden(self):
        """The corpus is measured only as far as it is fetched: name the gap
        rather than let a partial cache read as full coverage."""
        uncached = [r["name"] for r in self.repos if r not in self.cached]
        if uncached:
            self.skipTest(f"not fetched, so not measured: {', '.join(uncached)} — {FETCH_HINT}")


class InterpreterOverlayTests(unittest.TestCase):
    """The per-interpreter golden overlay is bounded by what it declares.

    `golden-by-python/<minor>/<name>/<rel>` replaces one base golden on one
    interpreter minor. Unbounded, it would be a second place to re-bless a
    regression. These checks read committed files only, so they run on every
    machine and every interpreter, with or without the corpus cache.
    """

    def setUp(self):
        if _derive_corpus is None:
            self.skipTest("arch-corpus helpers need PyYAML; run under `uv run`")
        self.dc = _derive_corpus
        self.overlays = sorted(
            p for p in self.dc.OVERLAY_ROOT.rglob("*") if p.is_file()
        ) if self.dc.OVERLAY_ROOT.is_dir() else []

    def _split(self, path: Path) -> tuple[tuple[int, int], str, str]:
        minor, name, *rest = path.relative_to(self.dc.OVERLAY_ROOT).parts
        major_s, minor_s = minor.split(".")
        return (int(major_s), int(minor_s)), name, "/".join(rest)

    def test_the_base_interpreter_constant_is_shared(self):
        self.assertEqual(_GOLDEN_INTERPRETER, self.dc.BASE_INTERPRETER)

    def test_every_overlay_file_is_declared_and_differs_from_its_base(self):
        # Not vacuous: the 3.14 fastapi-fullstack overlay exists today.
        self.assertTrue(self.overlays, "no overlay files; the test measures nothing")
        for path in self.overlays:
            version, name, rel = self._split(path)
            with self.subTest(overlay=str(path.relative_to(CORPUS_DIR))):
                self.assertNotEqual(version, self.dc.BASE_INTERPRETER)
                self.assertIn(name, self.dc.INTERPRETER_DELTAS.get(version, {}),
                              "overlay repository not declared in INTERPRETER_DELTAS")
                self.assertIn(rel, GOLDEN_FILES)
                base = GOLDEN / name / rel
                self.assertTrue(base.is_file(), "overlay has no base golden")
                self.assertNotEqual(path.read_bytes(), base.read_bytes(),
                                    "overlay is identical to its base; delete it")

    def test_every_declared_delta_has_an_overlay(self):
        for version, repos in self.dc.INTERPRETER_DELTAS.items():
            for name, spec in repos.items():
                with self.subTest(version=version, repo=name):
                    self.assertTrue(spec.get("paths"))
                    self.assertTrue(spec.get("because"))
                    directory = self.dc.overlay_dir(version) / name
                    self.assertTrue(
                        directory.is_dir() and any(p.is_file() for p in directory.rglob("*")),
                        "declared delta has no overlay file; `--bless` removes an overlay "
                        "file that stops differing, so delete the INTERPRETER_DELTAS entry")

    @staticmethod
    def _stray(base: list[str], over: list[str], paths: list[str]) -> list[str]:
        """Changed lines that name no declared path and are no `n_sources` count."""
        import difflib
        counter = re.compile(r'^\s*"n_sources": \d+,?$')
        return [
            line for line in difflib.ndiff(base, over)
            if line[:2] in ("- ", "+ ")
            and not any(p in line for p in paths)
            and not counter.match(line[2:])
        ]

    def test_an_overlay_differs_only_on_lines_naming_a_declared_path(self):
        """Every changed line names a declared source, or is an `n_sources` count."""
        for path in self.overlays:
            version, name, rel = self._split(path)
            paths = self.dc.INTERPRETER_DELTAS[version][name]["paths"]
            base = (GOLDEN / name / rel).read_text(encoding="utf-8").splitlines()
            over = path.read_text(encoding="utf-8").splitlines()
            with self.subTest(overlay=str(path.relative_to(CORPUS_DIR))):
                self.assertEqual(self._stray(base, over, paths), [],
                                 "overlay moves a line no declared path explains")

    def test_positive_control_a_stray_line_is_reported(self):
        """The bound fires on a change no declared path explains."""
        base = ["| User | email |", '  "n_sources": 7,']
        over = ["| User | e-mail |", '  "n_sources": 6,']
        self.assertEqual(self._stray(base, over, ["backend/app/api/deps.py"]),
                         ["- | User | email |", "+ | User | e-mail |"])

    def test_coverage_overlays_keep_every_verdict_and_entity_count(self):
        """The delta is a hashing and residual change, never an extraction one."""
        coverage = [p for p in self.overlays if p.name == "coverage.json"]
        self.assertTrue(coverage, "no coverage overlay; the test measures nothing")
        for path in coverage:
            version, name, rel = self._split(path)
            paths = set(self.dc.INTERPRETER_DELTAS[version][name]["paths"])
            base = json.loads((GOLDEN / name / rel).read_text(encoding="utf-8"))
            over = json.loads(path.read_text(encoding="utf-8"))
            for b, o in zip(base["concerns"], over["concerns"], strict=True):
                with self.subTest(overlay=name, concern=b["concern"]):
                    self.assertEqual(o["concern"], b["concern"])
                    self.assertEqual(o["verdict"], b["verdict"])
                    self.assertEqual(o.get("n_entities"), b.get("n_entities"))
                    moved = set(b.get("inputs_found", [])) ^ set(o.get("inputs_found", []))
                    self.assertLessEqual(moved, paths)
                    self.assertEqual(
                        b.get("n_sources", 0) - o.get("n_sources", 0),
                        len(set(b.get("inputs_found", [])) - set(o.get("inputs_found", [])))
                        - len(set(o.get("inputs_found", [])) - set(b.get("inputs_found", []))))

    def test_the_base_interpreter_resolves_to_the_base_golden(self):
        for path in self.overlays:
            version, name, rel = self._split(path)
            with self.subTest(overlay=str(path.relative_to(CORPUS_DIR))):
                self.assertEqual(self.dc.golden_path(name, rel, version), path)
                self.assertEqual(self.dc.golden_path(name, rel, self.dc.BASE_INTERPRETER),
                                 GOLDEN / name / rel)


class CorpusConfinementTests(unittest.TestCase):
    """The corpus fetches and derives untrusted third-party clones.

    Two confinement properties, both cache-free so they run on every machine.
    """

    def setUp(self):
        if _fetch is None or _derive_corpus is None:
            self.skipTest("arch-corpus helpers need PyYAML; run under `uv run`")

    def test_git_runs_with_the_user_gitconfig_neutralized(self):
        """`url.<base>.insteadOf` in `~/.gitconfig` rewrites a clone URL AFTER
        `URL_RE` allowed it, so the allowlisted URL is not the URL fetched.
        `_git` must point git's global and system config layers at /dev/null."""
        seen = {}

        def _capture(argv, **kwargs):
            seen.update(kwargs.get("env") or {})
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        real_run = _fetch.subprocess.run
        _fetch.subprocess.run = _capture
        try:
            _fetch._git("--version")
        finally:
            _fetch.subprocess.run = real_run

        self.assertEqual(seen.get("GIT_CONFIG_GLOBAL"), os.devnull,
                         "git would read ~/.gitconfig and could rewrite the allowlisted URL")
        self.assertEqual(seen.get("GIT_CONFIG_SYSTEM"), os.devnull,
                         "git would read the system gitconfig and could rewrite the URL")

    def test_derive_pops_the_override_flag_for_the_duration(self):
        """A per-repo `arch_extractors` override executes the CLONE's own code
        under `CRUX_ARCH_ALLOW_OVERRIDES=1`. A developer who set the flag for
        their own tree must not have it apply to an untrusted corpus repo."""
        observed = []

        class _StubDerive:
            @staticmethod
            def detect_stack(root, cfg):
                observed.append(os.environ.get("CRUX_ARCH_ALLOW_OVERRIDES"))
                return "stub-pack"

            @staticmethod
            def _build(root, docs_dir, mode, cfg):
                observed.append(os.environ.get("CRUX_ARCH_ALLOW_OVERRIDES"))
                coverage = {"concerns": [{
                    "concern": "data-model", "verdict": "stubbed", "extractor": "stub",
                    "inputs_found": [], "stub_reason": "precondition_missing",
                    "expected": "a declared input", "found": "no matching input",
                }]}
                tree = {rel: "" for rel in GOLDEN_FILES}
                tree["_meta/coverage.json"] = json.dumps(coverage)
                tree["data-model.md"] = ""
                return tree

        prior_flag = os.environ.get("CRUX_ARCH_ALLOW_OVERRIDES")
        prior_mod = sys.modules.get("crux.arch.derive")
        sys.modules["crux.arch.derive"] = _StubDerive
        os.environ["CRUX_ARCH_ALLOW_OVERRIDES"] = "1"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                _derive_corpus.derive_one("stub", Path(tmp))
            restored = os.environ.get("CRUX_ARCH_ALLOW_OVERRIDES")
        finally:
            if prior_mod is None:
                sys.modules.pop("crux.arch.derive", None)
            else:
                sys.modules["crux.arch.derive"] = prior_mod
            if prior_flag is None:
                os.environ.pop("CRUX_ARCH_ALLOW_OVERRIDES", None)
            else:
                os.environ["CRUX_ARCH_ALLOW_OVERRIDES"] = prior_flag

        self.assertEqual(observed, [None, None],
                         "the deriver saw CRUX_ARCH_ALLOW_OVERRIDES while deriving a clone")
        self.assertEqual(restored, "1", "the flag was not restored after the derive")


if __name__ == "__main__":
    unittest.main()
