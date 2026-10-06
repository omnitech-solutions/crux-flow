#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27",
#     "pyyaml>=6.0",
# ]
# ///
# `httpx` is REACHED, not called: the CHK-OBS-SURVEY-* rules derive batch state
# through `survey_sheet`, which imports `crux.arch.recover` and so pulls the
# `crux` package, whose council module imports the LLM router, which imports
# httpx. Declaring only PyYAML passes in the dev venv, where httpx is ambient,
# and fails under `uv run --no-project` in the release gate — the only place it
# is tested. `signoff-survey.py` carries the same declaration for the same
# reason.
"""check_observations.py — reference checker for the observations concern (docs/AGENTS.md §17).

Implements the CHK-OBS audit rules (§17.3) against the record frontmatter
under `<docs_dir>/observations/`, the concern index
`<docs_dir>/observations/index.md`, and — for the four CHK-OBS-SURVEY-* rules
— the batch surfaces under `<docs_dir>/observations/_surveys/`. `audit-docs`
describes these rules in prose; this is the executable reference
implementation the CHK-OBS rules may delegate to — the same pattern CHK-INV-*
delegates to `check_invariants.py`.

The four batch rules derive batch state through `survey_sheet.batch_state`,
never locally. §17.5 makes that function the ONE derivation of state from disk
precisely so a gate can never disagree with the writer: a rule that re-read
`completed` for itself would drift from the sign-off's resume the first time
the state table gained a row, and the tree would then report BROKEN on a batch
the sign-off considers healthy.

Silent when the concern is disabled: if `observations` is absent from
`manifest.yml`'s `concerns_enabled`, or the `<docs_dir>/observations/`
directory is absent, this exits 0 with an explicit "concern disabled"/empty
payload and runs no rule.

The `evidence` grammar is `<repo-relative-path>:<start>-<end>` (decimal line
numbers). An absolute path, a `~`-prefixed path, or any path carrying a `..`
traversal component is rejected outright as a CHK-OBS-EVIDENCE finding — an
evidence path must never be able to escape the repo root. That textual refusal
is NOT sufficient on its own: `is_file()` follows symlinks, so a committed
`link -> /etc` made `link/passwd:1-1` textually clean and stat-resolvable. The
containment decision therefore happens on the RESOLVED path, in the shared
`observation_evidence` module, which owns both the grammar and the check.

Exit convention (crux standard): 0 = clean, 1 = findings (valid JSON on
stdout), 2 = capability error, non-zero with empty stdout = crash.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print(json.dumps({"error": "PyYAML required (run under uv)"}))
    raise SystemExit(2)

# §17.1 status enum. Declared here (as `check_invariants.py` declares
# RATIFICATIONS/RESULTS for §15.2) rather than a full required-keyset
# constant — §17.1 stays the single source of truth for the field set.
STATUSES = {"observed", "ratified", "rejected", "retired", "decided"}


class ObservationsRefusal(RuntimeError):
    """A refusal to read the observations concern AT ALL — exit 2, never a
    finding. Raised when `<docs_dir>/observations` does not resolve to a
    directory inside the tree. Every `broken`/`warning` string carries a
    CHK-OBS-* rule id documented in §17.3, and this refusal has none: no rule
    ran, because the concern was never read."""

def _evidence_module():
    """The shared evidence grammar + containment helper, loaded by path.

    Same by-path pattern `_tree_name` uses for `bionic_config`: this file is
    executed by `spec_from_file_location` from several test modules and from
    `audit-docs`, so it can never assume `crux/scripts/` is on `sys.path`.
    """
    import importlib.util as _ilu
    key = "_observation_evidence"
    m = sys.modules.get(key)
    if m is None:
        path = Path(__file__).resolve().parent / "observation_evidence.py"
        s = _ilu.spec_from_file_location(key, path)
        m = _ilu.module_from_spec(s)
        s.loader.exec_module(m)
        sys.modules[key] = m
    return m


def _untrusted_module():
    """The shared bound-and-redact helper, loaded by path — the same reason
    `_evidence_module` above is."""
    import importlib.util as _ilu
    key = "_untrusted"
    m = sys.modules.get(key)
    if m is None:
        path = Path(__file__).resolve().parent / "untrusted.py"
        s = _ilu.spec_from_file_location(key, path)
        m = _ilu.module_from_spec(s)
        s.loader.exec_module(m)
        sys.modules[key] = m
    return m


_OE = _evidence_module()
# Bound at import so every call site reads `redact(...)` — the name the
# enrollment gate looks for.
redact = _untrusted_module().redact
MESSAGE_LIMIT = _untrusted_module().MESSAGE_LIMIT

# §17.1 `evidence` grammar: repo-relative path, ':', decimal start-end.
# Declared ONCE, in `observation_evidence`; three copies of it previously
# drifted apart across this file, `doctrine_projection` and `survey`.
_EVIDENCE_RE = _OE.EVIDENCE_RE

# §17.3 CHK-OBS-ANCHOR: `anchor_id` is `sha256(...)[:16]`, so exactly 16
# lowercase hex digits. Anything else is a malformed anchor, and a malformed
# anchor cannot correlate a re-mine to the record it already wrote.
_ANCHOR_RE = re.compile(r"^[0-9a-f]{16}$")

# The statuses in which a record is a LIVE claim on its anchor. `retired`,
# `rejected` and `decided` are terminal: §17.2's successor lifecycle
# deliberately puts a successor on its predecessor's anchor, so flagging a
# terminal predecessor would refuse the lifecycle the contract prescribes.
_LIVE_STATUSES = ("observed", "ratified")

# §14.3 dual-form artifact-id regex, specialized to ADR/OBS.
_ADR_ID_RE = re.compile(r"^(?:[A-Z][A-Z0-9]{1,9}-)?(ADR-\d{4})$")
_OBS_ID_PREFIX_RE = re.compile(r"^(?:[A-Z][A-Z0-9]{1,9}-)?OBS-\d{4}")
# Unanchored sibling of `_OBS_ID_PREFIX_RE`, for scanning free text (the
# index) rather than matching a whole filename stem. Same §14.3 dual-form
# spelling, so a prefixed row (e.g. `CRX-OBS-0001`) matches the full
# prefixed id instead of truncating to the bare `OBS-0001` substring.
_OBS_ID_TEXT_RE = re.compile(r"(?:[A-Z][A-Z0-9]{1,9}-)?OBS-\d{4}")


def _parse_frontmatter(text: str) -> dict | None:
    """The record's frontmatter as a mapping, or None when there is none to
    read. Unparseable YAML returns None rather than raising: an authored typo
    in one record is a CHK-OBS-BIJECTION document finding (exit 1, JSON on
    stdout), and letting the `yaml.YAMLError` escape would exit non-zero with
    EMPTY stdout — the lane this module's exit convention reserves for a crash,
    which leaves `audit-docs` and CHK-DRIFT-1 with no verdict at all."""
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        data = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _tree_name(root: Path) -> str:
    """Resolve the tree directory via bionic_config, falling back to the default."""
    _PATH = Path(__file__).resolve().parent / "bionic_config.py"
    import importlib.util as _ilu
    import sys as _sys
    _key = "_bionic_config"
    _m = _sys.modules.get(_key)
    if _m is None:
        _s = _ilu.spec_from_file_location(_key, _PATH)
        _m = _ilu.module_from_spec(_s)
        _s.loader.exec_module(_m)
        _sys.modules[_key] = _m
    return _m.resolve_tree_name(root)


_valid_evidence_path = _OE.valid_evidence_path
_parse_evidence_entry = _OE.parse_evidence


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def _adr_id_set(root: Path, docs_dir: str) -> set[str]:
    """Every ADR id under `<docs_dir>/adrs/` and `adrs/archive/`, in BOTH §14.3
    spellings: the id as written, plus its bare `ADR-NNNN` form when the id
    carries an artifact prefix. Built ONCE per `check()` run.

    This used to be a per-record linear scan that re-read and re-parsed every
    ADR for every `decided` record: 12.24s measured at 300 records x 400 ADRs,
    inside an audit gate. The set makes it one pass over the ADRs plus a hash
    lookup per record.
    """
    ids: set[str] = set()
    for sub in ("adrs", "adrs/archive"):
        d = root / docs_dir / sub
        if not d.is_dir():
            continue
        for page in sorted(d.glob("*.md")):
            fm = _parse_frontmatter(page.read_text(encoding="utf-8"))
            if not fm or "id" not in fm:
                continue
            fid = str(fm["id"])
            ids.add(fid)
            m = _ADR_ID_RE.match(fid)
            if m:
                ids.add(m.group(1))
    return ids


def _ensure_scripts_on_path() -> None:
    """Put `crux/scripts/` on `sys.path`.

    `observation_evidence` is loaded BY PATH above because it is a leaf. The
    survey modules are not: `survey_sheet` imports `summaries_projection` and
    `crux.arch.recover` by NAME, so the directory has to be importable anyway,
    and registering `survey_sheet` under its own module name is what makes this
    file and `summaries_projection` share ONE instance of it. §17.5's guarantee
    is about one `batch_state`, not two copies of it that agreed when they were
    written.
    """
    scripts = str(Path(__file__).resolve().parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)


def _survey_module():
    _ensure_scripts_on_path()
    import importlib
    return importlib.import_module("survey_sheet")


def _observation_log_ids(root: Path, docs_dir: str) -> set[str]:
    """Every observation id an `observation` op entry in `log.md` names, in
    either §14.3 spelling — CHK-OBS-SURVEY-RECORD's second disjunct.

    One scan covers both writers because §6 puts the id in the same place for
    both: the single-record route names the record id in the entry body, and
    the batch sign-off carries the sorted record ids in the body of its
    `survey batch <batch-id>` entry. The subject is scanned too, so a writer
    that names the id there instead is not a false BROKEN.

    `_log_entries` is reused rather than restated. A second copy of the log
    heading grammar is exactly the drift this repo has already paid for three
    times over in the evidence grammar (see `observation_evidence`).
    """
    path = root / docs_dir / "log.md"
    if not path.is_file():
        return set()
    _ensure_scripts_on_path()
    import summaries_projection as _sp
    out: set[str] = set()
    for entry in _sp._log_entries(path.read_text(encoding="utf-8")):
        if entry["op"] != "observation":
            continue
        text = f"{entry['subject']}\n{entry['body']}"
        out.update(m.group(0) for m in _OBS_ID_TEXT_RE.finditer(text))
    return out


def _survey_findings(root: Path, docs_dir: str, obs_dir: Path,
                     records: dict[str, dict], stub_days: int,
                     today: date) -> tuple[list[str], list[str]]:
    """The four CHK-OBS-SURVEY-* rules (§17.3), over the batch surfaces.

    Returns `(broken, warning)`. Runs unconditionally once the concern is
    enabled: CHK-OBS-SURVEY-RECORD's subject is the ratified RECORDS, so a
    tree with no `_surveys/` directory at all still has a rule to run.
    """
    broken: list[str] = []
    warning: list[str] = []
    ss = _survey_module()

    for bid in ss.batch_ids(obs_dir):
        paths = ss.receipt_paths(obs_dir, bid)
        receipt_path = paths["receipt"]
        if not receipt_path.is_file():
            # A batch directory with no receipt is S0 — the scaffold has run
            # and the sign-off has not. §17.5 cell 1 makes that a legitimate
            # resting state, and the holding area is frozen rather than owned
            # (`batch_ids`), so it is not this rule's finding to make.
            continue
        try:
            state = ss.batch_state(receipt_path, obs_dir)
            receipt = ss.read_receipt(receipt_path)
        except (ss.SurveySheetError, ValueError) as exc:
            # Attributed to the STUB rule because the receipt is that rule's
            # subject and this is its fail-closed edge: a receipt nobody can
            # read records no completion anybody can verify. It is BROKEN at
            # any age — the age grace exists for a sign-off in progress, and a
            # malformed receipt is not one. Reported under an existing rule id
            # rather than a new one: §17.3 fixes the rule-id set at nine.
            #
            # ValueError is caught alongside SurveySheetError because
            # `batch_state` reaches past the receipt: from S7 on it builds a
            # `StateFile`, whose fail-closed parse raises ValueError on an
            # unreadable candidate state file. That file is outside this
            # concern, and letting its ValueError escape turned the audit
            # checker into a traceback — breaking the exit-0-or-1-with-JSON
            # contract `audit-docs` delegates to, for every rule, not just
            # this one.
            broken.append(
                f"CHK-OBS-SURVEY-STUB: {redact(bid, quoted=False)} receipt.yml is unreadable, so its "
                f"batch state cannot be derived: "
                    f"{redact(exc, quoted=False, limit=MESSAGE_LIMIT)}")
            continue

        incomplete = ss.state_index(state) < ss.state_index("S4")

        # CHK-OBS-SURVEY-STUB — a receipt recording no completion. WARNING
        # inside `observation.survey_stub_days`, where a sign-off in progress
        # produces one; BROKEN past it, where nothing healthy does.
        if incomplete:
            signed = _parse_date(receipt.get("signed"))
            age = None if signed is None else (today - signed).days
            where = (f"{redact(bid, quoted=False)} is at {state} and records no completion "
                     f"(signed {redact(receipt.get('signed'), quoted=False)})")
            if age is not None and age > stub_days:
                broken.append(
                    f"CHK-OBS-SURVEY-STUB: {where}, {age} days ago, past "
                    f"survey_stub_days={stub_days}; re-run signoff-survey.py "
                    "to finish the publish")
            else:
                warning.append(
                    f"CHK-OBS-SURVEY-STUB: {where}, within "
                    f"survey_stub_days={stub_days}; re-run signoff-survey.py "
                    "to finish the publish")

        # CHK-OBS-SURVEY-VISIBLE — BROKEN at any age. §17.5 lands completion
        # before visibility, so no healthy in-flight state produces one; the
        # age grace the stub rule carries would make this rule useless.
        #
        # The subject is `record_path`, the NEW body the batch publishes, and
        # never `retires`. A retired predecessor's file is visible before the
        # batch starts and stays visible throughout — testing it would report
        # BROKEN on every healthy batch that retires anything.
        if incomplete:
            for row in receipt["rows"]:
                rel = str(row.get("record_path") or "")
                if not rel:
                    continue
                name = Path(rel).name
                if (obs_dir / name).is_file():
                    broken.append(
                        f"CHK-OBS-SURVEY-VISIBLE: {redact(bid, quoted=False)} record {redact(name, quoted=False)} is "
                        f"visible under {docs_dir}/observations/ while the "
                        f"batch is at {state}, recording no completion")

        # CHK-OBS-SURVEY-DIGEST — the receipt binds the sheet archived beside
        # it. Runs at EVERY state: a signed sheet edited after the fact is the
        # same finding whether the publish finished or not.
        sheet_path = paths["sheet"]
        if not sheet_path.is_file():
            broken.append(
                f"CHK-OBS-SURVEY-DIGEST: {redact(bid, quoted=False)} has a receipt but no archived "
                "sheet.yml, so the digest it records binds nothing")
            continue
        try:
            recomputed = ss.sheet_digest(ss.read_sheet(sheet_path))
        except ss.SurveySheetError as exc:
            broken.append(
                f"CHK-OBS-SURVEY-DIGEST: {redact(bid, quoted=False)} archived sheet.yml is "
                f"unreadable, so its digest cannot be recomputed: "
                    f"{redact(exc, quoted=False, limit=MESSAGE_LIMIT)}")
            continue
        if recomputed != receipt["digest"]:
            broken.append(
                f"CHK-OBS-SURVEY-DIGEST: {redact(bid, quoted=False)} sheet.yml now digests to "
                f"{recomputed}, not the {redact(receipt['digest'], quoted=False)} its receipt "
                "records")

    # CHK-OBS-SURVEY-RECORD — a ratified record covered by no receipt AND with
    # no `observation` log op behind it. A DISJUNCTION, deliberately: the
    # single-record route (§17.2) writes no receipt, so a conjunction would
    # report BROKEN on every record that route has ever written.
    try:
        covered = ss.receipt_covered_anchors(obs_dir)
    except ss.SurveySheetError:
        # Already reported above, per batch, with the parse error attached.
        covered = None
    if covered is not None:
        log_ids = _observation_log_ids(root, docs_dir)
        for oid in sorted(records):
            fm = records[oid]
            if fm.get("status") != "ratified":
                continue
            anchor = fm.get("anchor_id")
            if isinstance(anchor, str) and anchor in covered:
                continue
            if oid in log_ids:
                continue
            broken.append(
                f"CHK-OBS-SURVEY-RECORD: ratified record {redact(oid, quoted=False)} is covered by "
                "no batch receipt and has no `observation` log op behind it")

    return broken, warning


def _index_record_ids(text: str) -> set[str]:
    """The record ids `index.md` claims, read from each row's id column alone.

    CHK-OBS-BIJECTION compares the index against the record files. Reading it by
    grepping the whole page for an `OBS-NNNN` shape let a mined `domain` cell
    forge a reference — or, by carrying a real id, suppress the reverse finding.
    The id lives in the first cell; a value in any later cell is not a row's
    identity, so it is not read as one. §14.3's dual-form prefix (e.g.
    `CRX-OBS-0001`) is honoured because `_OBS_ID_PREFIX_RE` matches the whole
    prefixed id, not a bare substring.
    """
    ids: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("| "):
            continue
        first = line.strip().strip("|").split("|", 1)[0].strip()
        m = _OBS_ID_PREFIX_RE.match(first)
        if m:
            ids.add(m.group(0))
    return ids


def _load_manifest(root: Path, docs_dir: str) -> dict:
    mpath = root / docs_dir / "manifest.yml"
    if not mpath.is_file():
        return {}
    data = yaml.safe_load(mpath.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def check(root: Path, docs_dir: str | None = None) -> dict:
    if docs_dir is None:
        docs_dir = _tree_name(root)

    manifest = _load_manifest(root, docs_dir)
    concerns = manifest.get("concerns_enabled") or []

    if "observations" not in concerns:
        return {"broken": [], "warning": [], "survey_debt": 0, "records": 0,
                "concern_enabled": False, "note": "observations concern disabled"}

    # [SECURITY:S5] The concern directory, resolved ONCE through
    # `summaries_projection.observations_root` and proven contained under the
    # tree. A RELATIVE symlink at `<docs_dir>/observations` is git-carryable,
    # so a pull request could steer every CHK-OBS rule at a directory outside
    # the repository — the root has to be decided before anything resolves
    # against it. Resolved AFTER the concerns_enabled return above, so a
    # disabled concern stays silent and never follows the link.
    _ensure_scripts_on_path()
    import summaries_projection as _sp
    try:
        obs_dir = _sp.observations_root(root / docs_dir)
    except ValueError as exc:
        raise ObservationsRefusal(str(exc)) from exc
    if not obs_dir.is_dir():
        return {"broken": [], "warning": [], "survey_debt": 0, "records": 0,
                "concern_enabled": True, "note": "observations directory absent"}

    obs_cfg = manifest.get("observation") or {}
    stale_days = obs_cfg.get("stale_days")
    if not isinstance(stale_days, int):
        stale_days = 90

    # §17.3 CHK-OBS-SURVEY-STUB's window, from §7's `observation` block. The
    # fallback is 1 day: the window exists for a sign-off in progress, and a
    # sign-off in progress is minutes, not days. `isinstance(True, int)` is
    # True in Python, so a `survey_stub_days: yes` typo would otherwise read
    # as a one-day window with no complaint — hence the explicit bool reject.
    survey_stub_days = obs_cfg.get("survey_stub_days")
    if isinstance(survey_stub_days, bool) or not isinstance(survey_stub_days, int):
        survey_stub_days = 1

    broken: list[str] = []
    warning: list[str] = []

    records: dict[str, dict] = {}
    seen_ids: dict[str, str] = {}

    for page in sorted(obs_dir.glob("*.md")):
        if page.name == "index.md":
            continue
        fm = _parse_frontmatter(page.read_text(encoding="utf-8"))
        if not fm or "id" not in fm:
            broken.append(f"CHK-OBS-BIJECTION: {redact(page.name, quoted=False)} has no parseable record id")
            continue
        oid = str(fm["id"])
        if oid in seen_ids:
            broken.append(f"CHK-OBS-BIJECTION: duplicate record id {redact(oid, quoted=False)} ({redact(page.name, quoted=False)} and {redact(seen_ids[oid], quoted=False)})")
            continue
        m = _OBS_ID_PREFIX_RE.match(page.stem)
        if not m or m.group(0) != oid:
            broken.append(f"CHK-OBS-BIJECTION: {redact(page.name, quoted=False)} id {redact(oid, quoted=False)} disagrees with filename")
            continue
        seen_ids[oid] = page.name
        records[oid] = fm

    # index.md rows naming a record with no file, and (the reverse) a record
    # file whose id names no index row. The id is read from the ROW'S FIRST
    # CELL only (`_index_record_ids`), not by scanning the whole page: a mined
    # `domain` cell carrying an `OBS-NNNN` shape must not be able to forge a
    # reference or, by matching a real id, suppress the reverse finding.
    # Skipped entirely when index.md is absent: the forward direction has
    # nothing to scan, and the reverse direction stays silent rather than
    # emitting one finding per record for a tree that has no index yet.
    index_path = obs_dir / "index.md"
    if index_path.is_file():
        idx_text = index_path.read_text(encoding="utf-8")
        index_refs = _index_record_ids(idx_text)
        for ref in sorted(index_refs):
            if ref not in records:
                broken.append(f"CHK-OBS-BIJECTION: index.md names record {redact(ref, quoted=False)} with no file")
        for oid in records:
            if oid not in index_refs:
                broken.append(f"CHK-OBS-BIJECTION: record {redact(oid, quoted=False)} has a file but no index.md row")

    # CHK-OBS-ANCHOR (§17.3). `anchor_id` is what makes a re-mine recognize a
    # fact already recorded; nothing used to validate it, so a record with the
    # line deleted audited clean and was then SKIPPED by
    # `read_recorded_observations`, and the re-mine proposed a duplicate —
    # negating the postcondition the concern exists for.
    live_anchors: dict[str, list[str]] = {}
    for oid in sorted(records):
        fm = records[oid]
        anchor = fm.get("anchor_id")
        if anchor is None or not isinstance(anchor, str) or not _ANCHOR_RE.match(anchor):
            broken.append(
                f"CHK-OBS-ANCHOR: {redact(oid, quoted=False)} anchor_id {redact(anchor)} is not 16 lowercase hex digits")
            continue
        if fm.get("status") in _LIVE_STATUSES:
            live_anchors.setdefault(anchor, []).append(oid)
    for anchor, holders in sorted(live_anchors.items()):
        if len(holders) > 1:
            broken.append(
                f"CHK-OBS-ANCHOR: anchor {redact(anchor, quoted=False)} is claimed by more than one "
                f"live record ({redact(', '.join(sorted(holders)), quoted=False)}); retire the "
                "predecessor before ratifying a successor")

    adr_ids: set[str] | None = None
    observed_count = 0
    for oid, fm in records.items():
        status = fm.get("status")
        evidence = fm.get("evidence")

        # CHK-OBS-BIJECTION (status enum, §17.1): a status outside the
        # canonical enum — a typo (`ratifed`) or a miscased spelling
        # (`Ratified`) — would otherwise fail every `==` comparison below
        # and silently skip evidence resolution, decided-checking, and
        # staleness with no finding at all. Report it here, as the same
        # family of record-shape defect as an unparseable/duplicate id, and
        # skip the status-dependent checks below for this record: they have
        # no well-formed status to key off.
        if status not in STATUSES:
            broken.append(f"CHK-OBS-BIJECTION: {redact(oid, quoted=False)} has status {redact(status)} outside {sorted(STATUSES)}")
            continue

        # CHK-OBS-EVIDENCE
        if not evidence:
            broken.append(f"CHK-OBS-EVIDENCE: {redact(oid, quoted=False)} has an empty evidence list")
        elif not isinstance(evidence, list):
            broken.append(f"CHK-OBS-EVIDENCE: {redact(oid, quoted=False)} evidence is {type(evidence).__name__}, not a list")
        else:
            for entry in evidence:
                parsed = _parse_evidence_entry(entry)
                if parsed is None:
                    broken.append(f"CHK-OBS-EVIDENCE: {redact(oid, quoted=False)} evidence entry {redact(entry)} does not match path:line-range")
                    continue
                if status == "ratified":
                    path, _start, _end = parsed
                    # Containment on the RESOLVED path: `is_file()` alone
                    # follows a symlink straight out of the repository.
                    if not _OE.evidence_path_resolves(root, path):
                        broken.append(f"CHK-OBS-EVIDENCE: {redact(oid, quoted=False)} ratified evidence path {redact(path)} does not resolve inside the repo root")

        # CHK-OBS-DECIDED
        decided_by = fm.get("decided_by")
        if status == "decided":
            if not decided_by:
                broken.append(f"CHK-OBS-DECIDED: {redact(oid, quoted=False)} is decided but decided_by is null")
            else:
                if adr_ids is None:
                    adr_ids = _adr_id_set(root, docs_dir)
                if str(decided_by) not in adr_ids:
                    broken.append(f"CHK-OBS-DECIDED: {redact(oid, quoted=False)} names decided_by {redact(decided_by)} which does not exist")
        elif decided_by:
            broken.append(f"CHK-OBS-DECIDED: {redact(oid, quoted=False)} carries decided_by {redact(decided_by)} but status is {redact(status)}, not decided")

        # CHK-OBS-STALE
        if status == "observed":
            observed_count += 1
            od = _parse_date(fm.get("observed_date"))
            if od is not None and (date.today() - od).days > stale_days:
                warning.append(f"CHK-OBS-STALE: {redact(oid, quoted=False)} observed_date {redact(fm.get('observed_date'), quoted=False)} exceeds stale_days={stale_days}")

    # The four batch rules (§17.3), over the `_surveys/` surfaces plus the
    # ratified records. Last, so `records` is the validated map the id/status
    # checks above already built rather than a second walk of the directory.
    s_broken, s_warning = _survey_findings(
        root, docs_dir, obs_dir, records, survey_stub_days, date.today())
    broken.extend(s_broken)
    warning.extend(s_warning)

    return {
        "broken": broken,
        "warning": warning,
        "survey_debt": observed_count,
        "records": len(records),
        "concern_enabled": True,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_observations")
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--docs-dir", default=None)
    args = ap.parse_args(argv)
    try:
        result = check(args.root.resolve(), args.docs_dir)
    except Exception as exc:  # noqa: BLE001
        _bc = sys.modules.get("_bionic_config")
        refusals = (ObservationsRefusal,) + (tuple(
            c for c in (getattr(_bc, "BionicConfigError", None),
                        getattr(_bc, "SchemaVersionError", None)) if c is not None
        ) or (RuntimeError,))
        if isinstance(exc, refusals) or type(exc).__name__ in (
                "SchemaVersionError", "BionicConfigError"):
            sys.stderr.write(f"check_observations: {exc}\n")
            return 2
        raise
    print(json.dumps(result, indent=2))
    return 1 if (result["broken"] or result["warning"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
