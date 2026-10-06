"""Handle retirement: the `retires` governs sub-field and what it does to the
projections (docs/AGENTS.md §11.A; ADR-0097 part 6).

Pins, one named test per claim:

  - R1  the closed sub-field allowlist. A governs entry sub-field outside
        {domain, rule, scope, handle, anchor, provenance, retires} is a
        validation error, so a MISTYPED `retires` is refused rather than
        silently dropped. `retires` itself must be a non-empty list of
        ADR-anchored handle strings.
  - R2  `retires` is refused outright on an OBSERVATION entry, beside the
        existing `anchor` refusal: an observation describes code and cannot
        displace a decided rule.
  - R3  the five fail-closed validity rules, each with a positive control
        asserting the fixture genuinely triggers the refusal and the message
        names the offending handle:
          own-block · signed-reconciliation · never-been-live (typo) ·
          both-lanes · and the two IDEMPOTENCE cases, which are NOT refusals.
  - R4  what retirement does to the artifacts: a retired handle leaves the
        rule table's live rows, leaves the resolver, leaves the doctrine
        index's rules and its pairing seed, and appears in the rule table's
        `## Retired handles` roster with its rule text intact.
  - R5  the roster's two lanes stay visibly distinct: `## Removed handles`
        (ADR-0088) and `## Retired handles` (ADR-0097) are separate headings
        with different columns.
  - R6  the (B4) digest decision, pinned rather than left to accident:
        `retires` does NOT reach `canonical_string`, so `content_digest` is
        byte-identical with and without it and every signed receipt and
        reconciliation stays valid across a retirement. The input hash DOES
        move, because it covers the frontmatter block verbatim.
  - R7  the two readers of `reconciliations.yml` agree. The summaries lane
        reads the ledger for the signature-orphan refusal without importing
        the doctrine module (which imports it); this asserts the minimal
        reader returns exactly the doctrine reader's signed handles on the
        live tree, so the second reader cannot drift behind the first.
  - R8  retirement does NOT reach the backfill/coverage lane, whose subject
        is live FRONTMATTER: `collect_records` still returns the retired
        record (stamped `retired_by`), because retirement never mutates the
        retired entry's own ADR.

Tempdirs for every fixture; the live-tree legs (R7) read the repository and
write nothing. Runs under the uv lane (PyYAML).
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "crux" / "scripts"
# The R7 leg reads `bionic/` — a dev-only surface absent from the staged
# crux-only release artifact (ADR-0036 boundary). Guard, never false-fail.
try:
    from ._dev_surface import require_dev_surface
except ImportError:  # pragma: no cover - flat invocation
    from _dev_surface import require_dev_surface
sys.path.insert(0, str(SCRIPTS_DIR))

try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:  # pragma: no cover
    HAVE_YAML = False


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / filename)
    m = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, m)
    spec.loader.exec_module(m)
    return m


if HAVE_YAML:
    SP = _load("summaries_projection", "summaries_projection.py")
    DP = _load("doctrine_projection", "doctrine_projection.py")


# ── fixture helpers ─────────────────────────────────────────────────────────

def _adr_text(num: int, entries: list[dict], *, status: str = "Accepted") -> str:
    """An ADR (Accepted by default; pass `status="Proposed"` etc. to plant a
    fixture in another state) carrying `entries` as its governs block. Each
    entry is rendered key-by-key so a test can plant an arbitrary (including
    bogus) sub-field name without the helper normalizing it away."""
    lines = [
        "---",
        f"id: ADR-{num:04d}",
        f'title: "Decision {num}"',
        f"status: {status}",
        "date: 2026-08-01",
        "supersedes: []",
        "superseded_by: null",
        "tags: [test]",
    ]
    if entries is not None:
        lines.append("governs:")
        for e in entries:
            first = True
            for key, value in e.items():
                prefix = "  - " if first else "    "
                first = False
                if isinstance(value, list):
                    rendered = "[" + ", ".join(str(v) for v in value) + "]"
                else:
                    rendered = f'"{value}"' if key in ("rule",) else str(value)
                lines.append(f"{prefix}{key}: {rendered}")
    lines += ["---", "", f"# ADR-{num:04d}", "", "Body.", ""]
    return "\n".join(lines) + "\n"


def _entry(num: int, slug: str, *, domain: str = "d", **extra) -> dict:
    e = {
        "handle": f"ADR-{num:04d}/{slug}",
        "domain": domain,
        "rule": f"the {slug} rule",
        "scope": "crux/scripts",
        "provenance": "authored",
    }
    e.update(extra)
    return e


class _Tree:
    """A throwaway tree: .bionic.yml, manifest, adrs/, adrs/doctrine/."""

    def __init__(self, tmp: Path):
        self.root = tmp
        (tmp / ".bionic.yml").write_text("docs_dir: bionic\n", encoding="utf-8")
        self.tree = tmp / "bionic"
        self.adrs = self.tree / "adrs"
        self.adrs.mkdir(parents=True)
        (self.adrs / "summaries").mkdir()
        (self.adrs / "doctrine").mkdir()
        (self.tree / "manifest.yml").write_text(
            'schema_version: "5"\nconcerns_enabled: [adrs]\n', encoding="utf-8")

    def adr(self, num: int, entries: list[dict], *, status: str = "Accepted") -> None:
        (self.adrs / f"ADR-{num:04d}-decision.md").write_text(
            _adr_text(num, entries, status=status), encoding="utf-8")

    def reconciliation(self, invariant: str, handle: str, *,
                       signed: str | None = "2026-08-01") -> None:
        path = self.adrs / "doctrine" / "reconciliations.yml"
        if not path.exists():
            path.write_text('config_version: "1"\nreconciliations:\n', encoding="utf-8")
        digest = hashlib.sha256(handle.encode()).hexdigest()
        path.write_text(path.read_text(encoding="utf-8") + (
            f"  - invariant: {invariant}\n"
            f"    handle: {handle}\n"
            f"    verdict: compatible\n"
            f"    content_digest: {digest}\n"
            f"    rationale: null\n"
            f"    signed: {signed if signed else 'null'}\n"), encoding="utf-8")

    def tombstone(self, handle: str, *, digest: str | None = None) -> None:
        """A CURRENT signed tombstone receipt for `handle` — the ADR-0088
        removed lane."""
        digest = digest or hashlib.sha256(handle.encode()).hexdigest()
        path = self.adrs / "summaries" / "backfill-reviews.yml"
        if not path.exists():
            path.write_text(
                'config_version: "1"\nbatches:\n'
                '  - id: batch-1\n    signed: 2026-08-01\n'
                '    handles: []\n    no_rule: []\nreceipts:\n', encoding="utf-8")
        text = path.read_text(encoding="utf-8")
        text = text.replace("    handles: []", f"    handles: [{handle}]")
        text += (f"  - handle: {handle}\n"
                 f"    digest: {digest}\n"
                 f"    verdict: removed\n"
                 f"    batch: batch-1\n")
        path.write_text(text, encoding="utf-8")

    def records(self):
        return SP.collect_records(self.adrs)


def _problems(cm) -> list[dict]:
    return cm.exception.problems


def _matching(problems: list[dict], needle: str) -> list[dict]:
    return [p for p in problems if needle in p["problem"]]


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under `uv run`)")
class SubfieldAllowlistTests(unittest.TestCase):
    """R1 — the closed sub-field allowlist, and `retires`' own shape."""

    def test_the_allowlist_is_the_seven_named_sub_fields(self):
        self.assertEqual(
            set(SP.GOVERNS_SUBFIELDS),
            {"domain", "rule", "scope", "handle", "anchor", "provenance", "retires"})

    def test_a_mistyped_retires_key_is_refused_not_dropped(self):
        # The whole point of the allowlist: before it, `retire:` was dropped in
        # silence and the rule it meant to displace kept publishing.
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            t.adr(1, [_entry(1, "alpha")])
            t.adr(2, [_entry(2, "beta", retire=["ADR-0001/alpha"])])
            with self.assertRaises(SP.GovernsValidationError) as cm:
                t.records()
            hits = _matching(_problems(cm), "unknown governs sub-field")
            self.assertEqual(len(hits), 1, _problems(cm))
            self.assertIn("retire", hits[0]["problem"])
            self.assertEqual(hits[0]["handle"], "ADR-0002/beta")

    def test_a_correctly_spelled_retires_key_is_admitted(self):
        # The positive control for the allowlist: it is not refusing everything.
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            t.adr(1, [_entry(1, "alpha")])
            t.adr(2, [_entry(2, "beta", retires=["ADR-0001/alpha"])])
            records = t.records()
            self.assertEqual(len(records), 2)

    def test_retires_must_be_a_list_of_adr_anchored_handles(self):
        for bad, needle in (
            ("ADR-0001/alpha", "must be a list"),          # a bare string
            ("[]", "must not be empty"),                    # an empty list
            ("[not-a-handle]", "is not an ADR-anchored"),   # a bogus member
        ):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as td:
                t = _Tree(Path(td))
                t.adr(1, [_entry(1, "alpha")])
                path = t.adrs / "ADR-0002-decision.md"
                path.write_text(
                    _adr_text(2, [_entry(2, "beta")]).replace(
                        "    provenance: authored",
                        f"    retires: {bad}\n    provenance: authored"),
                    encoding="utf-8")
                with self.assertRaises(SP.GovernsValidationError) as cm:
                    t.records()
                hits = _matching(_problems(cm), needle)
                self.assertEqual(len(hits), 1, _problems(cm))
                self.assertEqual(hits[0]["handle"], "ADR-0002/beta")


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under `uv run`)")
class RetirementValidityTests(unittest.TestCase):
    """R3 — the five fail-closed validity rules. Every refusal asserts the
    message AND the offending handle; every refusal has a positive control
    proving the fixture triggers it and a sibling proving the rule does not
    fire on the legitimate case."""

    def test_retiring_a_handle_from_the_retiring_adrs_own_block_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            t.adr(1, [_entry(1, "alpha"), _entry(1, "beta", retires=["ADR-0001/alpha"])])
            with self.assertRaises(SP.GovernsValidationError) as cm:
                t.records()
            hits = _matching(_problems(cm), "retires a handle of its own")
            self.assertEqual(len(hits), 1, _problems(cm))
            self.assertIn("ADR-0001/alpha", hits[0]["problem"])
            self.assertEqual(hits[0]["source_adr"], "ADR-0001")

    def test_retiring_another_adrs_handle_is_not_the_own_block_case(self):
        # Positive control for the sibling direction: the rule is scoped to the
        # retiring ADR's OWN block and does not refuse an ordinary retirement.
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            t.adr(1, [_entry(1, "alpha")])
            t.adr(2, [_entry(2, "beta", retires=["ADR-0001/alpha"])])
            self.assertEqual(len(t.records()), 2)

    def test_retiring_a_handle_a_signed_reconciliation_names_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            t.adr(1, [_entry(1, "alpha")])
            t.adr(2, [_entry(2, "beta", retires=["ADR-0001/alpha"])])
            t.reconciliation("INV-0001", "ADR-0001/alpha", signed="2026-08-01")
            with self.assertRaises(SP.GovernsValidationError) as cm:
                t.records()
            hits = _matching(_problems(cm), "a signed reconciliation record names")
            self.assertEqual(len(hits), 1, _problems(cm))
            self.assertIn("ADR-0001/alpha", hits[0]["problem"])

    def test_an_unsigned_reconciliation_does_not_block_a_retirement(self):
        # Positive control: the refusal is about an orphaned HUMAN SIGNATURE,
        # so an unsigned record — the valid mid-review state — demands nothing.
        # Without this, the test above would pass for a rule that refused on
        # any reconciliation record at all.
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            t.adr(1, [_entry(1, "alpha")])
            t.adr(2, [_entry(2, "beta", retires=["ADR-0001/alpha"])])
            t.reconciliation("INV-0001", "ADR-0001/alpha", signed=None)
            self.assertEqual(len(t.records()), 2)

    def test_two_adrs_retiring_one_handle_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            t.adr(1, [_entry(1, "alpha")])
            t.adr(2, [_entry(2, "beta", retires=["ADR-0001/alpha"])])
            t.adr(3, [_entry(3, "gamma", retires=["ADR-0001/alpha"])])
            records = t.records()
            retired = SP.retired_records(records)
            self.assertEqual([r["handle"] for r in retired], ["ADR-0001/alpha"])
            # A state, not an event: both retiring ADRs are on the record.
            self.assertEqual(retired[0]["retired_by"], ["ADR-0002", "ADR-0003"])

    def test_one_adr_naming_a_handle_twice_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            t.adr(1, [_entry(1, "alpha")])
            t.adr(2, [_entry(2, "beta",
                             retires=["ADR-0001/alpha", "ADR-0001/alpha"])])
            retired = SP.retired_records(t.records())
            self.assertEqual([r["handle"] for r in retired], ["ADR-0001/alpha"])
            self.assertEqual(retired[0]["retired_by"], ["ADR-0002"])

    def test_a_target_that_was_never_a_live_handle_fails_the_projection(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            t.adr(1, [_entry(1, "alpha")])
            t.adr(2, [_entry(2, "beta", retires=["ADR-0001/alhpa"])])  # typo
            with self.assertRaises(SP.GovernsValidationError) as cm:
                t.records()
            hits = _matching(_problems(cm), "has never been a live handle")
            self.assertEqual(len(hits), 1, _problems(cm))
            self.assertIn("ADR-0001/alhpa", hits[0]["problem"])
            self.assertEqual(hits[0]["source_adr"], "ADR-0002")

    def test_a_removed_target_reports_the_lane_collision_not_the_typo(self):
        # The DISCRIMINATING control for the three-way handle history. ADR-0097
        # says two things that meet here: a target resolves against the live
        # handles UNION the retired roster UNION ADR-0088's removed roster, and
        # a handle in BOTH the removed and the retired lane is a validation
        # error. Both hold under exactly one reading: the removed roster's role
        # in the history is to make the refusal name the LANE COLLISION rather
        # than mislabel a genuinely-removed handle a typo. So this target is
        # refused — and refused with the right message, which is what proves
        # the history is consulted rather than every non-live target being
        # swept into the typo case.
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            t.adr(1, [_entry(1, "alpha")])
            t.tombstone("ADR-0005/withdrawn")
            t.adr(2, [_entry(2, "beta", retires=["ADR-0005/withdrawn"])])
            reviews = SP.read_reviews(t.adrs)
            self.assertIn("ADR-0005/withdrawn", SP.removed_handles(reviews),
                          "fixture assumption: the tombstone must be current")
            with self.assertRaises(SP.GovernsValidationError) as cm:
                t.records()
            problems = _problems(cm)
            self.assertEqual(len(_matching(problems, "both the removed and the "
                                           "retired lane")), 1, problems)
            self.assertEqual(_matching(problems, "has never been a live handle"),
                             [], problems)

    def test_an_already_retired_target_is_idempotent_not_a_typo(self):
        # The reachable half of the same history: a handle another ADR already
        # retired stays in the retired roster, so naming it again resolves
        # rather than reading as a typo. Without this leg the typo rule would
        # pass for an implementation that refused every non-LIVE target.
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            t.adr(1, [_entry(1, "alpha")])
            t.adr(2, [_entry(2, "beta", retires=["ADR-0001/alpha"])])
            t.adr(3, [_entry(3, "gamma", retires=["ADR-0001/alpha"])])
            retired = SP.retired_records(t.records())
            self.assertEqual([r["handle"] for r in retired], ["ADR-0001/alpha"],
                             "the already-retired handle must still be in the "
                             "roster the second retirement resolves against")

    def test_a_target_whose_adr_was_archived_stays_idempotent(self):
        # ADR-0097 part 6 names this case in as many words: a target that was
        # live and is no longer "because its ADR was archived" resolves against
        # the handle history and is idempotent. Archiving a superseded ADR is
        # routine here, and the retired roster is derived from the LIVE records
        # — so without the archived tier in the history, archiving the retired
        # ADR would turn a settled retirement into a typo and hard-fail every
        # lane that collects records.
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            archive = t.adrs / "archive"
            archive.mkdir()
            (archive / "ADR-0001-decision.md").write_text(
                _adr_text(1, [_entry(1, "alpha")]), encoding="utf-8")
            t.adr(2, [_entry(2, "beta", retires=["ADR-0001/alpha"])])
            self.assertNotIn("ADR-0001/alpha",
                             {r["handle"] for r in t.records()},
                             "fixture assumption: the archived ADR must "
                             "contribute no record")
            self.assertIn("ADR-0001/alpha", SP.archived_handles(t.adrs),
                          "fixture assumption: the handle must be in the "
                          "archived tier, or this test proves nothing")
            records = t.records()
            self.assertEqual([r["handle"] for r in records], ["ADR-0002/beta"])
            # The retirement is inert rather than fatal: nothing to filter, and
            # no roster row, because the archived ADR publishes nothing.
            self.assertEqual(SP.retired_records(records), [])

    def test_an_archived_handle_does_not_excuse_a_genuine_typo(self):
        # The control for the leg above: widening the history must not turn the
        # typo rule off. A handle absent from the archive too still fails.
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            archive = t.adrs / "archive"
            archive.mkdir()
            (archive / "ADR-0001-decision.md").write_text(
                _adr_text(1, [_entry(1, "alpha")]), encoding="utf-8")
            t.adr(2, [_entry(2, "beta", retires=["ADR-0001/alhpa"])])
            with self.assertRaises(SP.GovernsValidationError) as cm:
                t.records()
            hits = _matching(_problems(cm), "has never been a live handle")
            self.assertEqual(len(hits), 1, _problems(cm))
            self.assertIn("ADR-0001/alhpa", hits[0]["problem"])

    def test_a_handle_in_both_the_removed_and_the_retired_lane_is_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            t.adr(1, [_entry(1, "alpha")])
            t.tombstone("ADR-0001/alpha")
            t.adr(2, [_entry(2, "beta", retires=["ADR-0001/alpha"])])
            reviews = SP.read_reviews(t.adrs)
            self.assertIn("ADR-0001/alpha", SP.removed_handles(reviews),
                          "fixture assumption: the tombstone must be current")
            with self.assertRaises(SP.GovernsValidationError) as cm:
                t.records()
            hits = _matching(_problems(cm), "both the removed and the retired lane")
            self.assertEqual(len(hits), 1, _problems(cm))
            self.assertIn("ADR-0001/alpha", hits[0]["problem"])


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under `uv run`)")
class RetirementRequiresAcceptedTests(unittest.TestCase):
    """ADR-0097 part 6, "a later decision displacing a rule" — Accepted is the
    point in the ADR state machine where a decision binds, so a retirement
    from a Proposed ADR must not take effect. Validation (typo, own-block,
    signed-reconciliation, both-lanes) still runs over every record
    regardless of status; only the EFFECT is gated."""

    def test_a_proposed_adrs_retirement_leaves_the_target_live(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            t.adr(1, [_entry(1, "alpha")])
            t.adr(2, [_entry(2, "beta", retires=["ADR-0001/alpha"])], status="Proposed")
            records = t.records()
            self.assertEqual(SP.retired_records(records), [])
            self.assertEqual([r["handle"] for r in SP.live_records(records)],
                             ["ADR-0001/alpha", "ADR-0002/beta"])

    def test_the_positive_control_an_accepted_retirement_still_fires(self):
        # The sibling of the test above, same fixture with the retiring ADR
        # flipped to Accepted — proves the leg above is testing the GATE and
        # not an unrelated reason the retirement failed to take.
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            t.adr(1, [_entry(1, "alpha")])
            t.adr(2, [_entry(2, "beta", retires=["ADR-0001/alpha"])], status="Accepted")
            records = t.records()
            self.assertEqual([r["handle"] for r in SP.retired_records(records)],
                             ["ADR-0001/alpha"])
            self.assertEqual([r["handle"] for r in SP.live_records(records)],
                             ["ADR-0002/beta"])

    def test_a_proposed_adrs_own_rules_still_publish(self):
        # The gate is on the RETIREMENT, not on the ADR: a Proposed ADR's own
        # governs entries are unaffected and still appear as live records.
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            t.adr(1, [_entry(1, "alpha")], status="Proposed")
            records = t.records()
            self.assertEqual([r["handle"] for r in SP.live_records(records)],
                             ["ADR-0001/alpha"])

    def test_the_typo_refusal_still_fires_from_a_proposed_adr(self):
        # Validation is unchanged and still runs over every record, Proposed
        # included — a typo must fail at proposal time, loudly, not lie
        # dormant until acceptance.
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            t.adr(1, [_entry(1, "alpha")])
            t.adr(2, [_entry(2, "beta", retires=["ADR-0001/alhpa"])],  # typo
                  status="Proposed")
            with self.assertRaises(SP.GovernsValidationError) as cm:
                t.records()
            hits = _matching(_problems(cm), "has never been a live handle")
            self.assertEqual(len(hits), 1, _problems(cm))
            self.assertIn("ADR-0001/alhpa", hits[0]["problem"])
            self.assertEqual(hits[0]["source_adr"], "ADR-0002")

    def test_content_digest_is_unmoved_by_the_new_status_field(self):
        # The content identity is about the rule, never about the review
        # state a status field carries — proven directly against a real
        # collected record rather than a hand-built dict, so a future field
        # added to `_record_from_entry` cannot silently leak into the digest.
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            t.adr(1, [_entry(1, "alpha")], status="Proposed")
            [record] = t.records()
            before = SP.content_digest(record)
            t.adr(1, [_entry(1, "alpha")], status="Accepted")
            [record2] = t.records()
            self.assertEqual(before, SP.content_digest(record2))


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under `uv run`)")
class ObservationRetirementTests(unittest.TestCase):
    """R2 — an observation entry may not retire anything."""

    def test_retires_on_an_observation_entry_is_a_validation_error(self):
        problems = SP._observation_shape_problems([(
            "OBS-0001", "recovered",
            [{"handle": "OBS-0001/what-it-does", "domain": "d", "rule": "r",
              "scope": "s", "provenance": "recovered",
              "retires": ["ADR-0001/alpha"]}])])
        hits = _matching(problems, "an observation entry carries no `retires`")
        self.assertEqual(len(hits), 1, problems)
        self.assertEqual(hits[0]["handle"], "OBS-0001/what-it-does")

    def test_an_observation_entry_without_retires_is_clean(self):
        # Positive control: the refusal is attributable to `retires` alone.
        problems = SP._observation_shape_problems([(
            "OBS-0001", "recovered",
            [{"handle": "OBS-0001/what-it-does", "domain": "d", "rule": "r",
              "scope": "s", "provenance": "recovered"}])])
        self.assertEqual(problems, [])


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under `uv run`)")
class RetirementArtifactTests(unittest.TestCase):
    """R4/R5/R8 — what retirement does to each artifact, and what it leaves
    alone."""

    def _tree(self, td: str) -> _Tree:
        t = _Tree(Path(td))
        t.adr(1, [_entry(1, "alpha", domain="shared")])
        t.adr(2, [_entry(2, "beta", domain="shared", retires=["ADR-0001/alpha"])])
        return t

    def test_collect_records_still_returns_the_retired_record_stamped(self):
        # R8: the coverage/backfill lane's subject is live FRONTMATTER, which
        # retirement never mutates. Filtering here would make that gate read a
        # retired handle as withdrawn-without-a-tombstone.
        with tempfile.TemporaryDirectory() as td:
            records = self._tree(td).records()
            self.assertEqual([r["handle"] for r in records],
                             ["ADR-0001/alpha", "ADR-0002/beta"])
            by_handle = {r["handle"]: r for r in records}
            self.assertEqual(by_handle["ADR-0001/alpha"]["retired_by"], ["ADR-0002"])
            self.assertEqual(by_handle["ADR-0002/beta"]["retired_by"], [])

    def test_live_records_drops_the_retired_handle_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            records = self._tree(td).records()
            live = SP.live_records(records)
            self.assertEqual([r["handle"] for r in live], ["ADR-0002/beta"])
            self.assertEqual(SP.live_records(live), live)

    def test_the_rule_table_drops_the_retired_row_and_keeps_the_record(self):
        with tempfile.TemporaryDirectory() as td:
            table = SP.build_rule_table(self._tree(td).records())
            live_segment = table.split("## Retired handles")[0]
            self.assertNotIn("ADR-0001/alpha", live_segment)
            self.assertIn("ADR-0002/beta", live_segment)
            self.assertIn("## Retired handles", table)
            roster = table.split("## Retired handles")[1]
            self.assertIn("| handle | rule | source ADR | retired by |", roster)
            self.assertIn("ADR-0001/alpha", roster)
            # The record survives the retirement: the rule text is still there.
            self.assertIn("the alpha rule", roster)
            self.assertIn("ADR-0002", roster)

    def test_the_resolver_drops_the_retired_handle(self):
        with tempfile.TemporaryDirectory() as td:
            resolver = SP.build_resolver(self._tree(td).records())
            self.assertNotIn("ADR-0001/alpha", resolver)
            self.assertIn("ADR-0002/beta", resolver)

    def test_the_doctrine_domain_entry_drops_the_retired_rule(self):
        with tempfile.TemporaryDirectory() as td:
            t = self._tree(td)
            entries = DP.build_domain_entries(t.records(), [], [], {}, None,
                                              repo_root=t.root)
            shared = next(e for e in entries if e["domain"] == "shared")
            self.assertEqual([r["handle"] for r in shared["rules"]],
                             ["ADR-0002/beta"])
            self.assertEqual(shared["source_handles"], ["ADR-0002/beta"])

    def test_the_pairing_seed_drops_the_retired_rule(self):
        # A retired rule must not be able to mark a domain BROKEN.
        with tempfile.TemporaryDirectory() as td:
            t = self._tree(td)
            invariants = [{"id": "INV-0001", "ratification": "ratified",
                           "related_adrs": ["ADR-0001", "ADR-0002"],
                           "invariant_text": "the invariant text"}]
            pairings = DP.candidate_pairings(t.records(), invariants)
            self.assertEqual([p["handle"] for p in pairings], ["ADR-0002/beta"])

    def test_the_two_rosters_stay_visibly_distinct(self):
        # R5: removed and retired are separate lanes, separate headings,
        # separate columns — never one merged roster.
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            t.adr(1, [_entry(1, "alpha")])
            t.tombstone("ADR-0005/withdrawn")
            t.adr(2, [_entry(2, "beta", retires=["ADR-0001/alpha"])])
            reviews = SP.read_reviews(t.adrs)
            table = SP.build_rule_table(t.records(), reviews, None)
            self.assertIn("## Removed handles", table)
            self.assertIn("## Retired handles", table)
            self.assertIn("| handle | last live digest | batch |", table)
            self.assertIn("| handle | rule | source ADR | retired by |", table)
            self.assertLess(table.index("## Removed handles"),
                            table.index("## Retired handles"))

    def test_an_alias_row_does_not_republish_a_retired_handle(self):
        # The hole this closes: a `decided` observation's alias row lists its
        # decided_by ADR's handles, and an alias row IS a resolver row — so an
        # unfiltered list would republish through the alias exactly the handle
        # the retirement removed from the resolver.
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            t.adr(1, [_entry(1, "alpha"), _entry(1, "kept")])
            t.adr(2, [_entry(2, "beta", retires=["ADR-0001/alpha"])])
            obs = t.tree / "observations"
            obs.mkdir()
            (obs / "OBS-0001-x.md").write_text(
                "---\nid: OBS-0001\ntitle: X\nstatus: decided\n"
                "provenance: recovered\ndecided_by: ADR-0001\n"
                "evidence: [crux/scripts/x.py:1-2]\nanchor_id: aaaaaaaaaaaaaaaa\n"
                "governs:\n  - domain: d\n    rule: \"r\"\n    scope: s\n"
                "    handle: OBS-0001/what-it-does\n    provenance: recovered\n"
                "---\n\n# OBS-0001\n", encoding="utf-8")
            records = SP.collect_records(t.adrs, observations=obs)
            rows = SP.collect_observation_alias_rows(obs, records)
            self.assertEqual(rows["OBS-0001/what-it-does"]["alias_of"], "ADR-0001")
            self.assertEqual(rows["OBS-0001/what-it-does"]["alias_handles"],
                             ["ADR-0001/kept"],
                             "the retired handle must not reappear through the alias")
            resolver = SP.build_resolver(records, alias_rows=rows)
            self.assertNotIn("ADR-0001/alpha", str(resolver))

    def test_a_tree_with_no_retirement_renders_no_retired_roster(self):
        # The identity leg: `retires` costs nothing on a tree that uses none.
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            t.adr(1, [_entry(1, "alpha")])
            table = SP.build_rule_table(t.records())
            self.assertNotIn("## Retired handles", table)


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under `uv run`)")
class RetirementDigestTests(unittest.TestCase):
    """R6 — the (B4) decision, pinned. `retires` stays outside the content
    identity ADR-0088 binds receipts and reconciliations to."""

    def test_retires_does_not_reach_the_content_digest(self):
        entry = _entry(1, "alpha")
        with_retires = dict(entry, retires=["ADR-0000/whatever"])
        self.assertEqual(SP.content_digest(entry), SP.content_digest(with_retires))
        self.assertEqual(SP.canonical_string(entry),
                         SP.canonical_string(with_retires))

    def test_the_input_hash_does_move_on_a_retires_list(self):
        # The other half of (B4): no new gate is minted because the existing
        # input hash covers the frontmatter block verbatim.
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            t.adr(1, [_entry(1, "alpha")])
            t.adr(2, [_entry(2, "beta")])
            before = SP.adr_frontmatter_sha256(t.adrs)
            t.adr(2, [_entry(2, "beta", retires=["ADR-0001/alpha"])])
            self.assertNotEqual(before, SP.adr_frontmatter_sha256(t.adrs))


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under `uv run`)")
class ReconciliationReaderAgreementTests(unittest.TestCase):
    """R7 — the summaries lane's minimal ledger reader cannot drift behind the
    doctrine lane's full one."""

    def test_the_two_readers_agree_on_the_live_tree(self):
        require_dev_surface(self, REPO_ROOT / "bionic" / "adrs" / "doctrine"
                            / "reconciliations.yml", "bionic reconciliation ledger")
        doctrine_signed = {r["handle"] for r in DP.read_reconciliations(REPO_ROOT)
                           if r["signed"] is not None}
        self.assertTrue(doctrine_signed,
                        "fixture assumption: the live ledger holds signed records")
        self.assertEqual(
            SP.signed_reconciliation_handles(SP.adrs_dir(REPO_ROOT)),
            doctrine_signed)

    def test_the_minimal_reader_fails_closed_on_an_unreadable_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            (t.adrs / "doctrine" / "reconciliations.yml").write_text(
                "config_version: \"1\"\nreconciliations:\n  - handle: 17\n"
                "    signed: 2026-08-01\n", encoding="utf-8")
            with self.assertRaises(SP.GovernsValidationError) as cm:
                SP.signed_reconciliation_handles(t.adrs)
            self.assertTrue(_matching(_problems(cm), "signed reconciliation record"),
                            _problems(cm))

    def test_an_absent_ledger_is_an_empty_set(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            self.assertEqual(SP.signed_reconciliation_handles(t.adrs), set())

    def test_a_duplicated_handle_key_is_refused_not_silently_shortened(self):
        # `doctrine_projection.read_reconciliations` refuses a ledger with a
        # duplicated mapping key via the composer-node check; this reader
        # must refuse the SAME file the same way, or a duplicate `handle:`
        # key would let a retirement of a signed handle through — the exact
        # harm the signature-orphan refusal exists to prevent.
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            (t.adrs / "doctrine" / "reconciliations.yml").write_text(
                "config_version: \"1\"\nreconciliations:\n"
                "  - invariant: INV-0001\n"
                "    handle: ADR-0001/alpha\n"
                "    handle: ADR-0001/alpha\n"
                "    verdict: compatible\n"
                "    content_digest: "
                f"{hashlib.sha256(b'ADR-0001/alpha').hexdigest()}\n"
                "    rationale: null\n"
                "    signed: 2026-08-01\n",
                encoding="utf-8")
            with self.assertRaises(SP.GovernsValidationError) as cm:
                SP.signed_reconciliation_handles(t.adrs)
            self.assertTrue(_matching(_problems(cm), "duplicate mapping key"),
                            _problems(cm))

    def test_the_positive_control_without_the_duplicate_resolves_the_handle(self):
        # Same ledger, one `handle:` line — proves the refusal above is about
        # the duplicate key and not about the ledger shape in general.
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            t.reconciliation("INV-0001", "ADR-0001/alpha", signed="2026-08-01")
            self.assertEqual(SP.signed_reconciliation_handles(t.adrs),
                             {"ADR-0001/alpha"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under `uv run`)")
class ArchiveTierLinkRefusalTests(unittest.TestCase):
    """The two archive-tier readers — `archived_handles` and
    `_adr_ids_including_archive` — used `glob` + `is_file()`, which follows a
    symlink, so an `ADR-*.md` link planted in `adrs/archive/` was read from
    outside the repository at exit 0. Both now resolve through the same
    containment helper as the active-tier reader and refuse the link."""

    def _tree_with_planted_link(self, td: str):
        t = _Tree(Path(td))
        archive = t.adrs / "archive"
        archive.mkdir()
        (archive / "ADR-0001-decision.md").write_text(
            _adr_text(1, [_entry(1, "alpha")]), encoding="utf-8")
        outside = Path(td) / "outside"
        outside.mkdir()
        target = outside / "ADR-0007-planted.md"
        target.write_text(_adr_text(7, [_entry(7, "planted")]), encoding="utf-8")
        # Positive control: the target is a real, readable ADR file, so a
        # reader that follows the link would ingest ADR-0007/planted.
        self.assertIn("ADR-0007/planted", target.read_text(encoding="utf-8"))
        (archive / "ADR-0007-planted.md").symlink_to(target)
        return t

    def test_archived_handles_refuses_a_link_out_of_the_archive(self):
        with tempfile.TemporaryDirectory() as td:
            t = self._tree_with_planted_link(td)
            with self.assertRaises(ValueError) as cm:
                SP.archived_handles(t.adrs)
            self.assertIn("never through a link out of it", str(cm.exception))
            self.assertNotIn("planted", str(cm.exception).split("refusing to read")[0])

    def test_adr_ids_including_archive_refuses_a_link_out_of_the_archive(self):
        with tempfile.TemporaryDirectory() as td:
            t = self._tree_with_planted_link(td)
            with self.assertRaises(ValueError) as cm:
                SP._adr_ids_including_archive(t.adrs)
            self.assertIn("never through a link out of it", str(cm.exception))

    def test_a_real_archived_file_is_still_read(self):
        """The control for the two legs above: the guard refuses links, not
        the archive tier itself."""
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(Path(td))
            archive = t.adrs / "archive"
            archive.mkdir()
            (archive / "ADR-0001-decision.md").write_text(
                _adr_text(1, [_entry(1, "alpha")]), encoding="utf-8")
            self.assertIn("ADR-0001/alpha", SP.archived_handles(t.adrs))
            self.assertIn("ADR-0001", SP._adr_ids_including_archive(t.adrs))
