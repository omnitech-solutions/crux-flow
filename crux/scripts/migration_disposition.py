"""Explicit human disposition receipts; parsing never supplies migration authority.

Receipts record local evidence under the existing sign-off trust model. They do
not authenticate a human. The owning skill must obtain the instruction verbatim.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
from pathlib import Path
import sys

import council_records as cr
import doctrine_projection as dp
import implementation_approval as ap
from _yaml_min import CatalogYamlError, _refuse_duplicate_keys_bounded
import yaml

Refused = ap.Refused
SCHEMA = Path(__file__).resolve().parent.parent / "schemas/implementation-migration-disposition.schema.json"
PRODUCERS = {"doctrine-pairing": "reconcile-signoff/migration-disposition-1",
             "backfill-receipt": "backfill-signoff/migration-disposition-1"}


def canonical_digest(doc) -> str:
    """Digest a complete normalized row or entry, distinct from source/text digests."""
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def parse_receipt(text: str) -> dict:
    """Validate closed receipt grammar only, without repository reads or authority."""
    try:
        _refuse_duplicate_keys_bounded(text, yaml)
        doc = yaml.safe_load(text)
    except (CatalogYamlError, yaml.YAMLError, ValueError):
        raise Refused("migration-disposition-parse-refused") from None
    validator = cr._validator(); errors = []
    validator.validate(doc, validator.load_schema(SCHEMA), "#", "#", errors, "disposition")
    ap.require(not errors, "migration-disposition-shape-refused")
    try:
        datetime.date.fromisoformat(doc["signed"])
    except ValueError:
        raise Refused("migration-disposition-date-refused") from None
    ap.require(all(doc[key].strip() for key in ("human_instruction", "rationale")),
               "migration-disposition-human-input-required")
    if doc["subject"]["kind"] == "doctrine-pairing":
        subject = doc["subject"]
        ap.require(subject["member_kind"] == dp.member_kind(subject["invariant"]),
                   "migration-disposition-member-kind-refused")
        pairs = doc["replacement_pairings"]
        handles = [p["handle"] for p in pairs]
        ap.require(len(set(handles)) == len(handles) and all(
            p["invariant"] == subject["invariant"] for p in pairs),
            "migration-disposition-replacement-refused")
    return doc


def _raw_records(repo: Path, config) -> tuple[list[dict], list[dict]]:
    """Load raw texts for deterministic seed checks; never collect a projected view."""
    import adr_frontmatter as af
    import implementation_migration as im
    import summaries_projection as sp
    records = []; identities = set(); handles = set(); invariants = []
    adrs = config.docs_root / "adrs"
    for path in af.adr_paths(adrs):
        text, _ = im._read(repo, path); front = af.frontmatter_block(text)
        ap.require(front is not None, "migration-disposition-source-refused")
        fm = im._yaml(front); im._filename_identity(path, fm, config)
        ap.require(fm["id"] not in identities and "record_type" not in fm,
                   "migration-disposition-source-refused")
        identities.add(fm["id"]); rules = fm.get("governs", [])
        ap.require(isinstance(rules, list) and not sp._governs_shape_problems([(fm["id"], rules)]),
                   "migration-disposition-source-refused")
        for rule in rules:
            ap.require(rule["handle"] not in handles, "migration-disposition-source-refused")
            handles.add(rule["handle"])
            records.append({**rule, "source_adr": fm["id"], "source_kind": "adr",
                "active": path.parent == adrs, "status": fm.get("status")})
    for path in sorted((config.docs_root / "invariants").glob("*.md")):
        text, _ = im._read(repo, path); front = af.frontmatter_block(text)
        if front is None: continue
        fm = im._yaml(front)
        ap.require(isinstance(fm, dict), "migration-disposition-member-refused")
        iid = fm.get("id")
        if not isinstance(iid, str) or not dp._INV_ID_RE.fullmatch(iid): continue
        ap.require(not any(i["id"] == iid for i in invariants), "migration-disposition-member-refused")
        related = fm.get("related_adrs", [])
        ap.require(isinstance(related, list) and all(isinstance(a, str) for a in related),
                   "migration-disposition-member-refused")
        invariants.append({"id": iid, "ratification": fm.get("ratification"),
            "related_adrs": related, "invariant_text": dp.invariant_section(text)})
    manifest = im._yaml(im._read(repo, config.docs_root / "manifest.yml")[0])
    ap.require(isinstance(manifest, dict), "migration-disposition-manifest-refused")
    if "observations" not in (manifest.get("concerns_enabled") or []): return records, invariants
    for path in sp.observation_paths(config.docs_root / "observations"):
        text, _ = im._read(repo, path); front = af.frontmatter_block(text)
        ap.require(front is not None, "migration-disposition-member-refused")
        fm = im._yaml(front)
        ap.require(isinstance(fm, dict), "migration-disposition-member-refused")
        if fm.get("status") != "ratified": continue
        oid = fm.get("id"); rules = fm.get("governs", [])
        ap.require(isinstance(oid, str) and sp._OBS_ID_SHAPE_RE.fullmatch(oid) and
                   isinstance(rules, list) and not sp._observation_shape_problems([(oid, fm.get("provenance"), rules)]),
                   "migration-disposition-member-refused")
        evidence = fm.get("evidence", [])
        ap.require(isinstance(evidence, list) and all(isinstance(e, str) for e in evidence),
                   "migration-disposition-member-refused")
        for rule in rules:
            ap.require(rule["handle"] not in handles, "migration-disposition-member-refused")
            handles.add(rule["handle"])
            records.append({**rule, "source_adr": oid, "source_kind": "observation", "evidence": evidence,
                "active": True, "status": "ratified"})
    return records, invariants


def _doctrine_subject(repo: Path, config, entry: dict, args, text: str) -> tuple[dict, list, dict]:
    rows = dp.parse_reconciliations(text)
    records, invariants = _raw_records(repo, config)
    retired = {h for r in records if r["active"] and r["status"] == "Accepted" for h in r.get("retires", [])}
    live = [r for r in records if r["active"] and r["handle"] not in retired]
    historical = live + [r for r in records if not r["active"] and r["handle"] == args.handle]
    def current(handle):
        inputs = historical if handle == args.handle else live
        pairs = dp.invariant_pairings(inputs, invariants) + dp.observation_pairings(inputs, config.docs_dir)
        selected = [r for r in rows if (r["invariant"], r["handle"]) == (args.invariant, handle)]
        seeded = [p for p in pairs if (p["invariant"], p["handle"]) == (args.invariant, handle)]
        ap.require(len(selected) == 1 and len(seeded) == 1, "migration-disposition-pairing-unseeded")
        row, pair = selected[0], seeded[0]
        ap.require(row["signed"] is not None and row["content_digest"] == pair["live_digest"],
                   "migration-disposition-pairing-unsigned-or-stale")
        return row, pair
    old, old_pair = current(args.handle)
    replacements = []; rendering = []
    for handle in entry["replacement_handles"]:
        row, pair = current(handle)
        ap.require(row["verdict"] in ("compatible", "reconciled"),
                   "migration-disposition-replacement-incompatible")
        replacements.append({"invariant": args.invariant, "handle": handle,
            "row_sha256": canonical_digest(row), "content_digest": row["content_digest"]})
        rendering.append({"handle": handle, "rule_text": pair["rule"], "signed_row": row})
    subject = {"kind": "doctrine-pairing", "invariant": args.invariant,
        "member_kind": old["member_kind"], "handle": args.handle,
        "row_sha256": canonical_digest(old), "content_digest": old["content_digest"]}
    return subject, replacements, {"member_text": old_pair["invariant_text"],
        "old_rule_text": old_pair["rule"], "signed_row": old, "replacements": rendering}


def _backfill_subject(entry: dict, handle: str, text: str) -> tuple[dict, dict]:
    import summaries_projection as sp
    reviews = sp.parse_reviews(text)
    row = sp.current_receipt(handle, reviews)
    ap.require(row is not None and sp.receipt_signed(row, reviews["batches"]) and
               row["verdict"] == "pass" and handle not in sp.removed_handles(reviews),
               "migration-disposition-backfill-not-current-admission")
    old = next(r for r in entry["affected_governs"] if r["handle"] == handle)
    ap.require(row["digest"] == sp.content_digest(old) and
               sp._folded_anchor(row["anchor"]) == sp._folded_anchor(old.get("anchor")),
               "migration-disposition-backfill-stale")
    batch = reviews["batches"][row["batch"]]
    return {"kind": "backfill-receipt", "handle": handle,
        "receipt_sha256": canonical_digest(row), "signing_batch_sha256": canonical_digest(batch)}, {
        "old_rule_text": old["rule"], "signed_receipt": row, "signing_batch": batch}


def validate_backfill_corroboration(reviews: dict, manifest: dict,
                                   log_text: str, journal_texts: dict[str, str]) -> None:
    """Check existing admission and signed hooks from raw inputs, without projection.

    Inputs use parse_reviews normalization. This verifies corroboration only;
    callers also check source content, current chains, removed lanes and proof.
    Logs and journals are current proof inputs, never persisted digest bindings.
    Unrelated append operations preserve validation.
    """
    import summaries_projection as sp
    config = manifest.get("adr")
    ap.require(isinstance(config, dict), "migration-disposition-backfill-admission-refused")
    gf = sp.governs_from(manifest); cohort = config.get("governs_backfill_cohort")
    ledger = config.get("governs_backfilled")
    ap.require(isinstance(gf, int) and isinstance(cohort, list) and isinstance(ledger, dict),
               "migration-disposition-backfill-admission-refused")
    log_entries = sp._log_entries(log_text)
    ap.require(all(e["subject"] in reviews["batches"] for e in log_entries if e["op"] == "backfill"),
               "migration-disposition-backfill-corroboration-refused")
    for bid, batch in reviews["batches"].items():
        signed = batch["signed"]
        if signed is None: continue
        receipted = {r["handle"] for r in reviews["receipts"] if r["batch"] == bid}
        no_rule = {r["adr"] for r in reviews["no_rule"] if r["batch"] == bid}
        ap.require(set(batch["handles"]).issubset(receipted) and set(batch["no_rule"]).issubset(no_rule),
                   "migration-disposition-backfill-enumeration-refused")
        for handle in batch["handles"]:
            aid = handle.split("/", 1)[0]
            ap.require(aid in cohort and sp.adr_num(aid) < gf and isinstance(ledger.get(aid), list)
                       and handle in ledger[aid], "migration-disposition-backfill-admission-refused")
        derived = sp.derived_adr_ids(batch)
        ap.require(all(aid in cohort and sp.adr_num(aid) < gf for aid in batch["no_rule"]),
                   "migration-disposition-backfill-admission-refused")
        valid_log = False
        for entry in log_entries:
            if (entry["op"], entry["subject"], entry["date"]) != ("backfill", bid, signed): continue
            match = sp._ADRS_LINE_RE.search(entry["body"])
            ids = match.group(1).split() if match else []
            if all(re.fullmatch(r"ADR-\d{4}", aid) for aid in ids) and sorted(ids, key=sp.adr_num) == derived:
                valid_log = True
        hooks = sp._journal_entries(journal_texts.get(signed[:7], ""))
        valid_hook = any(e["category"] == "review" and e["subject"] == f"backfill sign-off {bid}"
            and e["date"] == signed and all(aid in e["block"] for aid in derived) for e in hooks)
        ap.require(valid_log and valid_hook, "migration-disposition-backfill-corroboration-refused")


def _read_backfill_corroboration(repo: Path, config, entry: dict, text: str) -> None:
    import adr_frontmatter as af
    import implementation_migration as im
    import summaries_projection as sp
    reviews = sp.parse_reviews(text)
    manifest = im._yaml(im._read(repo, config.docs_root / "manifest.yml")[0])
    ap.require(isinstance(manifest, dict), "migration-disposition-backfill-admission-refused")
    log = im._read(repo, config.docs_root / "log.md")[0]
    months = {b["signed"][:7] for b in reviews["batches"].values() if b["signed"] is not None}
    journals = {month: im._read(repo, config.docs_root / "journal" / (month + ".md"))[0] for month in months}
    validate_backfill_corroboration(reviews, manifest, log, journals)
    source = im.resolve_clause(repo, entry)
    source_text = im._read(repo, source["path"])[0]
    body = source_text[af.FENCE_RE.match(source_text).end():]
    ap.require(not sp._governs_anchor_problems([(entry["source_adr"], entry["affected_governs"], body)],
               sp.governs_from(manifest)), "migration-disposition-backfill-anchor-refused")


def _prepare(repo: Path, kind: str, args) -> tuple[dict, dict]:
    import bionic_config
    import implementation_migration as im
    config = bionic_config.load_config(repo)
    batch = im.load_batch(repo, args.migration_disposition)
    ap.require(batch["format_version"] == "2", "migration-disposition-format-required")
    entries = [e for e in batch["entries"] if e["source_identity"] == args.source_identity]
    ap.require(len(entries) == 1, "migration-disposition-entry-refused")
    entry = entries[0]; im.resolve_clause(repo, entry)
    ap.require(entry["disposition"] != "architecture-retained" and any(
        r["handle"] == args.handle for r in entry["affected_governs"]),
        "migration-disposition-subject-refused")
    im._architectural_refs(repo, batch["authorizing_refs"] + entry["replacement_handles"])
    relative = "adrs/doctrine/reconciliations.yml" if kind == "doctrine-pairing" else "adrs/summaries/backfill-reviews.yml"
    text, rel = im._read(repo, config.docs_root / relative)
    dependency = {"path": rel, "sha256": hashlib.sha256(text.encode()).hexdigest()}
    ap.require(dependency in batch["signed_dependencies"], "migration-disposition-dependency-stale")
    if kind == "doctrine-pairing":
        subject, replacements, details = _doctrine_subject(repo, config, entry, args, text)
    else:
        _read_backfill_corroboration(repo, config, entry, text)
        subject, details = _backfill_subject(entry, args.handle, text)
        records, _ = _raw_records(repo, config)
        details["replacements"] = [r for r in records if r["handle"] in entry["replacement_handles"]]
    receipt = {"record_type": "implementation-migration-dependency-disposition", "format_version": "1",
        "producer": PRODUCERS[kind], "batch_id": batch["batch_id"], "source_identity": entry["source_identity"],
        "entry_sha256": canonical_digest(entry), "dependency": dependency, "subject": subject,
        "disposition": "preserve-signed-history-and-migrate-entry", "human_instruction": args.human_instruction,
        "rationale": args.rationale, "signed": args.date or datetime.date.today().isoformat()}
    if kind == "doctrine-pairing": receipt["replacement_pairings"] = replacements
    proposal = {"entry": entry, "entry_sha256": canonical_digest(entry), "dependency": dependency,
        "subject": subject, "details": details, "authority": "none",
        "human_disposition": "required for this specific historical subject"}
    return receipt, proposal


def _publish(repo: Path, receipt: dict, *, dry_run: bool) -> dict:
    """Publish immutable bytes atomically; preserve all existing signature surfaces."""
    import bionic_config
    import implementation_migration as im
    config = bionic_config.load_config(repo)
    directory = config.docs_root / "adrs/migrations/dispositions"
    im._checked(repo, directory)
    body = (json.dumps(receipt, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
    wanted_hash = hashlib.sha256(body).hexdigest()
    path = directory / (wanted_hash + ".json")
    def identity(value):
        subject = value["subject"]
        return (value["batch_id"], value["source_identity"], value["entry_sha256"],
                subject["kind"], subject.get("invariant"), subject["handle"])
    replay = False
    for existing in sorted(directory.glob("*.json")):
        content, _ = im._read(repo, existing)
        old = parse_receipt(content)
        ap.require(existing.name == hashlib.sha256(content.encode()).hexdigest() + ".json",
                   "migration-disposition-immutable-conflict")
        if identity(old) == identity(receipt):
            ap.require(content.encode() == body, "migration-disposition-subject-conflict")
            replay = True
    if replay:
        return {"state": "no-op", "path": path.relative_to(repo).as_posix(), "sha256": wanted_hash}
    result = {"state": "dry-run" if dry_run else "written", "path": path.relative_to(repo).as_posix(), "sha256": wanted_hash}
    if dry_run: return result
    # The caller repeated source and ledger checks immediately before this call.
    directory.mkdir(parents=True, exist_ok=True); im._checked(repo, directory)
    tmp = path.with_suffix(".json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    owned = os.fstat(fd)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body); stream.flush(); os.fsync(stream.fileno())
        im._checked(repo, directory)
        try:
            os.link(tmp, path, follow_symlinks=False)
        except FileExistsError:
            text, _ = im._read(repo, path)
            ap.require(text.encode() == body, "migration-disposition-immutable-conflict")
            result["state"] = "no-op"
    finally:
        try:
            current = tmp.lstat()
            if (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino): tmp.unlink()
        except FileNotFoundError: pass
    return result


def main(kind: str, argv) -> int:
    parser = argparse.ArgumentParser(description="Render or record one separately supplied human migration disposition.")
    parser.add_argument("--migration-disposition", required=True, help="exact migration batch path")
    parser.add_argument("--source-identity", required=True)
    parser.add_argument("--handle", required=True)
    if kind == "doctrine-pairing": parser.add_argument("--invariant", required=True)
    parser.add_argument("--human-instruction", default=None, help="verbatim explicit human instruction for this subject")
    parser.add_argument("--rationale", default=None, help="human supplied migration disposition rationale")
    parser.add_argument("--date", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args(argv); repo = Path(args.repo_root).resolve()
    try:
        receipt, proposal = _prepare(repo, kind, args)
        if args.dry_run and args.human_instruction is None and args.rationale is None:
            print(json.dumps({"proposal": proposal, "state": "human-input-required", "written": []}, sort_keys=True))
            return 0
        receipt = parse_receipt(json.dumps(receipt))
        # Repeat validation immediately before the immutable write.
        checked, _ = _prepare(repo, kind, args)
        ap.require(checked == receipt, "migration-disposition-input-changed")
        result = _publish(repo, receipt, dry_run=args.dry_run)
        print(json.dumps({"proposal": proposal, "receipt": result, "authority": "none"}, sort_keys=True))
        return 0
    except (Refused, dp.DoctrineValidationError) as exc:
        print(json.dumps({"findings": [{"problem": str(exc)}]}, sort_keys=True)); return 1
    except Exception as exc:
        import summaries_projection as sp
        if isinstance(exc, sp.GovernsValidationError):
            print(json.dumps({"findings": exc.problems}, sort_keys=True)); return 1
        sys.stderr.write(f"migration-disposition: {type(exc).__name__}: {exc}\n"); return 2
