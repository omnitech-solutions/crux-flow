"""Read-only clause migration foundation. Inventory confers no authority or approval."""
from __future__ import annotations

import contextvars
import copy
import hashlib
from contextlib import contextmanager, closing, ExitStack
from functools import lru_cache
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

import yaml

import adr_frontmatter
import bionic_config
import council_records as cr
import doctrine_projection as doctrine
import implementation_approval as ap
import observation_evidence
from _yaml_min import CatalogYamlError, CatalogYamlExpansionError, _refuse_duplicate_keys_bounded

Refused = ap.Refused
SCHEMA = Path(__file__).resolve().parent.parent / "schemas/implementation-migration.schema.json"
PILOT_SOURCES = {"ADR-0093", "ADR-0110", "ADR-0124", "ADR-0125"}
MAX_SOURCE_BYTES = 2 * 1024 * 1024
PUBLICATION_RECORD_TYPE = "implementation-migration-publication"
#: Project-local skill roots, spelled as adr-signals.LOCAL_SKILLS_DIRS spells them.
LOCAL_SKILLS_DIRS = (".claude/skills", ".agents/skills", ".opencode/skills", ".opencode/skill")
#: Each skill root's forge-log.md, an append-only dated record. It is excluded for the reason the
#: linter excludes log.md.
FORGE_LOGS = frozenset(d + "/forge-log.md" for d in LOCAL_SKILLS_DIRS)
#: The next step a citation-source refusal names. The refusal code stays the whole contract;
#: a remedy is advice beside it, path-free, so it never carries an untrusted value.
SYMLINK_SOURCE_REMEDY = (
    "A file or directory the migration reads for citations is a symbolic link, or sits under one. "
    "Replace a file link with a regular file, and a directory link with a real directory. "
    "The scan reads root AGENTS.md, README.md, USER_GUIDE.md and CHANGELOG.md whether git tracks "
    "them or not, so untracking never clears a link at one of those paths. "
    "At any other path, `git rm --cached <path>` also clears the refusal. "
    "`git ls-files -s` shows a tracked link with mode 120000. Then rerun.")
SUBMODULE_SOURCE_REMEDY = (
    "A submodule sits at or under a project-local skill root. A harness loads its skill files, "
    "but this repository's index does not list them, so the migration cannot read them. "
    "`git ls-files -s` shows a submodule with mode 160000. "
    "To keep the skill as regular files, untrack the submodule with `git rm --cached <path>`. "
    "`<name>` is the name of the `.gitmodules` section whose `path` is `<path>`. "
    "`git config -f .gitmodules --get-regexp '\\.path$'` lists each section as `submodule.<name>.path <path>`. "
    "When `.gitmodules` has such a section, remove it with "
    "`git config -f .gitmodules --remove-section submodule.<name>` and stage it with "
    "`git add .gitmodules`. "
    "Then move the `.git` entry at the top of `<path>` out of the repository: moving it keeps the nested repository's history. "
    "Do not delete it: deleting the `.git` directory of an embedded clone loses any history not pushed elsewhere. "
    "While that entry remains, `git add` records the submodule again. "
    "Then track the files with `git add <path>`, and rerun.")
ABSENT_SOURCE_REMEDY = (
    "A tracked instruction or skill file is missing from the working tree. "
    "Restore it, stage its deletion with `git rm <path>`, or widen the sparse checkout to include it. "
    "`git ls-files --deleted` lists an unstaged deletion. "
    "`git ls-files -t` marks a skip-worktree entry with S. Then rerun.")


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def source_identity(entry: dict) -> str:
    """Bind clause content and every selected governs field, excluding path/lifecycle."""
    content = {key: entry[key] for key in ("source_adr", "affected_governs")}
    content["clause"] = entry["clause"]["text"]
    return _digest(json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())


def _checked(repo: Path, given) -> tuple[Path, str]:
    from admission_source_io import SourceIO, SourceIORefusal
    path, rel = _raw_source_path(repo, given)
    try:
        with closing(SourceIO(repo)) as source_io:
            _source_path(path, source_io)
            source_io.revalidate()
    except SourceIORefusal:
        raise Refused("migration-symlink-refused") from None
    return path, rel


def _raw_source_path(repo, given):
    original = Path(repo); repo = original.resolve(); path = Path(given)
    if path.is_absolute() and path.is_relative_to(original):
        path = path.relative_to(original)
    path = repo / path
    ap.require(".." not in path.parts, "migration-path-refused")
    ap.require("\x00" not in str(path) and path != repo and path.is_relative_to(repo), "path-refused")
    return path, path.relative_to(repo).as_posix()


def _source_path(path, source_io):
    from admission_source_io import SourceIORefusal
    try:
        metadata = source_io.metadata(path)
        ap.require(metadata is None or metadata.get("target") is None, "migration-symlink-refused")
        ap.require(source_io.resolve(path) == path, "migration-symlink-refused")
    except SourceIORefusal:
        raise Refused("migration-symlink-refused") from None
    return metadata


@lru_cache(maxsize=1)
def _reference_reader():
    """Reuse the trusted linter's descriptor-safe file reader and citation grammar."""
    path = Path(__file__).with_name("lint-governs-references.py")
    spec = importlib.util.spec_from_file_location("_migration_citation_scope", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def _read(repo: Path, given) -> tuple[str, str]:
    return _bounded_read(repo, given, MAX_SOURCE_BYTES)


def _bounded_read(repo: Path, given, limit: int) -> tuple[str, str]:
    from admission_source_io import SourceIO, SourceIORefusal
    path, rel = _raw_source_path(repo, given)
    try:
        with closing(SourceIO(repo)) as source_io:
            metadata = _source_path(path, source_io)
            ap.require(metadata is not None, "migration-source-unreadable")
            ap.require(metadata["st_size"] <= limit, "migration-source-oversize")
            text = source_io.read_bytes(path, max_bytes=limit).decode("utf-8")
            source_io.revalidate()
    except (SourceIORefusal, OSError, UnicodeError):
        raise Refused("migration-source-unreadable") from None
    return text, rel


def _configuration(repo: Path):
    """Capture canonical configuration through one internally owned transport."""
    from admission_source_io import SourceIO, SourceIORefusal
    repo = Path(repo).resolve()
    try:
        with closing(SourceIO(repo)) as source_io:
            config = bionic_config.load_config(repo, _source_io=source_io)
            source_io.revalidate()
            return config
    except SourceIORefusal:
        raise Refused("migration-layout-refused") from None


def _filename_identity(path: Path, fm: dict, config) -> None:
    prefix = re.escape(config.artifact_prefix + "-" if config.artifact_prefix else "")
    match = re.fullmatch(rf"({prefix}ADR-[0-9]{{4}})-[a-z0-9]+(?:-[a-z0-9]+)*\.md", path.name)
    ap.require(match is not None and fm.get("id") == match[1], "migration-filename-identity-refused")


_YAML_MEMO: dict[str, object] = {}
_YAML_MEMO_LIMIT = 64 * 1024 * 1024
_yaml_memo_bytes = 0


def _yaml(text: str):
    """Parse one bounded YAML document. A parse depends only on the text, so a
    repeated text is answered from a memo with a fresh copy of the same object."""
    global _yaml_memo_bytes
    if text in _YAML_MEMO:
        return copy.deepcopy(_YAML_MEMO[text])
    try:
        _refuse_duplicate_keys_bounded(text, yaml)
        doc = yaml.safe_load(text)
    except CatalogYamlExpansionError:
        raise Refused("migration-yaml-expansion-refused") from None
    except CatalogYamlError:
        raise Refused("migration-duplicate-key-refused") from None
    except (ValueError, yaml.YAMLError):
        raise Refused("migration-yaml-refused") from None
    if _yaml_memo_bytes + len(text) > _YAML_MEMO_LIMIT:
        _YAML_MEMO.clear(); _yaml_memo_bytes = 0
    _YAML_MEMO[text] = copy.deepcopy(doc); _yaml_memo_bytes += len(text)
    return doc


def schema_errors(doc) -> list:
    validator = cr._validator()
    errors = []
    validator.validate(doc, validator.load_schema(SCHEMA), "#", "#", errors, "migration")
    return errors


def _entry(entry: dict) -> None:
    ap.require(entry["source_adr"] in PILOT_SOURCES, "migration-outside-pilot")
    ap.require(_digest(entry["clause"]["text"].encode()) == entry["clause"]["sha256"],
               "migration-clause-digest-refused")
    ap.require(source_identity(entry) == entry["source_identity"], "migration-source-identity-refused")
    ap.require(entry["historical_destination"] == {"source_adr": entry["source_adr"],
               "clause_sha256": entry["clause"]["sha256"]}, "migration-destination-refused")
    handles = [rule["handle"] for rule in entry["affected_governs"]]
    ap.require(len(set(handles)) == len(handles) and all(
        handle.startswith(entry["source_adr"] + "/") for handle in handles), "migration-governs-identity-refused")
    ap.require(entry["source_adr"] != "ADR-0093" or not handles, "migration-invented-handle-refused")
    ap.require(not handles or entry["disposition"] != "historical-implementation" or
               bool(entry["replacement_handles"]), "migration-replacement-required")


def load_batch(repo_root, path) -> dict:
    return _load_batch(repo_root, path)[0]


def _load_batch(repo_root, path) -> tuple[dict, bytes]:
    """Parse the same safe buffer used for the retained subject digest."""
    repo = Path(repo_root).resolve()
    content = _read(repo, path)[0].encode("utf-8")
    doc = _yaml(content.decode("utf-8"))
    ap.require(not schema_errors(doc), "migration-schema-refused")
    ap.require(doc["authorizing_refs"] == ["ADR-0146/authority-migration-is-reviewed-and-source-bound"],
               "migration-authorizer-refused")
    for entry in doc["entries"]:
        _entry(entry)
    identities = [entry["source_identity"] for entry in doc["entries"]]
    ap.require(len(set(identities)) == len(identities), "migration-duplicate-source-refused")
    for key in ("signed_dependencies", "citation_dependencies", "signed_dispositions"):
        if key not in doc: continue
        paths = [item["path"] for item in doc[key]]
        ap.require(len(set(paths)) == len(paths), "migration-duplicate-dependency-refused")
    if doc["format_version"] == "2":
        recovery = doc.get("recovery_from")
        if recovery is None:
            ap.require(doc["batch_id"] == "implementation-pilot-001", "migration-recovery-identity-refused")
        else:
            ap.require(doc["batch_id"] == "implementation-pilot-001-recovery-" + _canonical_digest(recovery),
                       "migration-recovery-identity-refused")
            ap.require(recovery["source_identities"] == sorted(set(identities)),
                       "migration-recovery-source-refused")
    return doc, content


def _resolved_clause(repo_root, entry: dict) -> tuple[dict, str, int]:
    repo = Path(repo_root).resolve()
    # Use the same closed entry schema for imported calls as for the batch loader.
    validator = cr._validator(); errors = []
    validator.validate(entry, validator.load_schema(SCHEMA)["properties"]["entries"]["items"],
                       "#", "#", errors, "migration-entry")
    ap.require(not errors, "migration-schema-refused")
    _entry(entry)
    config = _configuration(repo)
    adrs = config.docs_root / "adrs"
    matches = []
    for path in adr_frontmatter.adr_paths(adrs):
        text, rel = _read(repo, path)
        front = adr_frontmatter.frontmatter_block(text)
        ap.require(front is not None, "migration-source-frontmatter-refused")
        fm = _yaml(front)
        ap.require(isinstance(fm, dict) and "record_type" not in fm, "migration-source-record-refused")
        _filename_identity(path, fm, config)
        if fm.get("id") == entry["source_adr"]:
            matches.append((path, rel, text, fm))
    ap.require(len(matches) == 1, "migration-source-identity-refused")
    path, rel, text, fm = matches[0]
    body_start = adr_frontmatter.FENCE_RE.match(text).end()
    body = text[body_start:]
    clause = entry["clause"]["text"]
    first = body.find(clause)
    ap.require(first >= 0 and body.find(clause, first + 1) < 0, "migration-clause-mismatch")
    governs = fm.get("governs", [])
    ap.require(isinstance(governs, list), "migration-source-governs-refused")
    for expected in entry["affected_governs"]:
        selected = [rule for rule in governs if isinstance(rule, dict) and rule.get("handle") == expected["handle"]]
        ap.require(selected == [expected], "migration-governs-mismatch")
    record = {"source_adr": entry["source_adr"], "path": rel,
            "tier": "archive" if path.parent.name == "archive" else "active",
            "status": fm.get("status"), "source_identity": entry["source_identity"],
            "clause_sha256": entry["clause"]["sha256"], "affected_governs": entry["affected_governs"]}
    return record, text, body_start + first


def resolve_clause(repo_root, entry: dict) -> dict:
    """Resolve the canonical reference without adding serialized span metadata."""
    return _resolved_clause(repo_root, entry)[0]


def resolve_clause_span(repo_root, entry: dict) -> dict:
    """Locate the exact selected body bytes; this locator supplies no activation proof."""
    record, text, start = _resolved_clause(repo_root, entry)
    end = start + len(entry["clause"]["text"])
    byte_start = len(text[:start].encode("utf-8"))
    return {key: record[key] for key in ("source_identity", "source_adr", "path", "clause_sha256")} | {
        "source_sha256": _digest(text.encode("utf-8")),
        "byte_start": byte_start,
        "byte_end": byte_start + len(entry["clause"]["text"].encode("utf-8")),
        "line_start": text[:start].count("\n") + 1,
        "line_end": text[:end - 1].count("\n") + 1}


def _architectural_refs(repo: Path, handles: list[str]) -> None:
    """Read the original architectural sources without consulting any projected view."""
    config = _configuration(repo)
    adrs = config.docs_root / "adrs"
    records = []; retired = set()
    for path in adr_frontmatter.adr_paths(adrs):
        text, _ = _read(repo, path)
        front = adr_frontmatter.frontmatter_block(text)
        ap.require(front is not None, "migration-source-frontmatter-refused")
        fm = _yaml(front)
        ap.require(isinstance(fm, dict) and "record_type" not in fm, "migration-source-record-refused")
        _filename_identity(path, fm, config)
        rules = fm.get("governs", [])
        ap.require(isinstance(rules, list), "migration-source-governs-refused")
        records.append((path.parent == adrs, fm, rules))
        if path.parent == adrs and fm.get("status") == "Accepted":
            for rule in rules:
                ap.require(isinstance(rule, dict), "migration-source-governs-refused")
                retired.update(rule.get("retires", []))
    for handle in handles:
        hosts = [(active, fm, rules) for active, fm, rules in records if fm.get("id") == handle.split("/")[0]]
        ap.require(len(hosts) == 1, "migration-architectural-identity-refused")
        active, fm, rules = hosts[0]
        matches = [rule for rule in rules if isinstance(rule, dict) and rule.get("handle") == handle]
        # An entry that carries retires is itself a live rule; only its targets are retired.
        ap.require(active and fm.get("status") == "Accepted" and handle not in retired and
                   len(matches) == 1, "migration-constraint-not-live-accepted")


def _signed_inventory(repo: Path, tree: Path) -> tuple[list[dict], list[dict]]:
    path = tree / "adrs/doctrine/reconciliations.yml"
    text, rel = _read(repo, path); doc = _yaml(text)
    ap.require(isinstance(doc, dict) and isinstance(doc.get("reconciliations"), list),
               "migration-signed-ledger-refused")
    try:
        records = doctrine.parse_reconciliations(text)
    except (doctrine.DoctrineValidationError, ValueError, TypeError, yaml.YAMLError):
        raise Refused("migration-signed-ledger-refused") from None
    pairings = [{"path": rel, "handle": entry["handle"], "invariant": entry["invariant"],
                 "member_kind": entry["member_kind"],
                 "sha256": _digest(json.dumps(entry, sort_keys=True).encode())}
                for entry in records if entry["signed"] is not None]
    dependencies = [{"path": rel, "sha256": _digest(text.encode())}]
    backfill = _backfill_inventory(repo, tree)
    dependencies += backfill["dependencies"]
    dependencies += _member_inventory(repo, tree)[0]
    pairings += backfill["signed_receipts"]
    return dependencies, pairings


def _member_frontmatter(text: str):
    """Parse universal-newline presentation while keeping source bytes for identity."""
    front = adr_frontmatter.frontmatter_block(text.replace("\r\n", "\n").replace("\r", "\n"))
    if front is None: return None
    doc = _no_git_document(front, _member=True)
    ap.require(isinstance(doc, dict), "migration-member-shape-refused")
    return doc


def _member_inventory(repo: Path, tree: Path) -> tuple[list[dict], list[dict]]:
    """Inventory ratified member source bytes; members never become migration authority."""
    dependencies = []; members = []; identities = set()
    prefix = _configuration(repo).artifact_prefix
    prefix = re.escape(prefix + "-" if prefix else "")
    for folder, kind, marker in ((tree / "invariants", "invariant", "INV"),
                                  (tree / "observations", "observation", "OBS")):
        ap.require(not cr.reached_through_symlink(folder), "migration-symlink-refused")
        for path in sorted(folder.glob("*.md")):
            text, rel = _read(repo, path); fm = _member_frontmatter(text)
            if fm is None: continue
            ap.require(isinstance(fm, dict), "migration-member-shape-refused")
            identity = fm.get("id")
            if identity is None: continue
            ap.require(isinstance(identity, str) and re.fullmatch(rf"{prefix}{marker}-[0-9]{{4}}", identity)
                       and "record_type" not in fm and identity not in identities,
                       "migration-member-identity-refused")
            identities.add(identity)
            ratified = fm.get("ratification" if kind == "invariant" else "status") == "ratified"
            if not ratified: continue
            dependency = {"path": rel, "sha256": _digest(text.encode())}
            dependencies.append(dependency)
            if kind == "invariant":
                related = fm.get("related_adrs", [])
                ap.require(isinstance(related, list) and all(isinstance(h, str) for h in related),
                           "migration-member-shape-refused")
                members.append({"id": identity, "kind": kind, "related_adrs": related})
            else:
                evidence = fm.get("evidence", [])
                ap.require(isinstance(evidence, list) and evidence and all(
                    observation_evidence.parse_evidence(e) is not None for e in evidence),
                    "migration-member-shape-refused")
                ap.require(path.name.startswith(identity + "-"), "migration-member-identity-refused")
                members.append({"id": identity, "kind": kind,
                    "evidence_paths": [observation_evidence.parse_evidence(e)[0] for e in evidence]})
    return dependencies, members


def _backfill_inventory(repo: Path, tree: Path) -> dict:
    """Use only raw grammar and receipt-chain functions, never projected authority."""
    from summaries_projection import parse_reviews, receipt_signed, current_receipt, removed_handles, GovernsValidationError
    path = tree / "adrs/summaries/backfill-reviews.yml"
    if not path.exists() and not path.is_symlink():
        return {"dependencies": [], "signed_receipts": [], "removed_handles": {}, "signed_batches": False}
    text, rel = _read(repo, path)
    try:
        reviews = parse_reviews(text)
    except (GovernsValidationError, ValueError, TypeError, yaml.YAMLError):
        raise Refused("migration-backfill-ledger-refused") from None
    signed = []
    for handle in sorted({r["handle"] for r in reviews["receipts"]}):
        row = current_receipt(handle, reviews)
        if row is not None and receipt_signed(row, reviews["batches"]):
            signed.append({"kind": "backfill-receipt", "path": rel, "handle": handle,
                "sha256": _canonical_digest(row), "signing_batch_sha256":
                _canonical_digest(reviews["batches"][row["batch"]])})
    return {"dependencies": [{"path": rel, "sha256": _digest(text.encode())}],
            "signed_receipts": signed, "removed_handles": removed_handles(reviews),
            "signed_batches": any(b["signed"] is not None for b in reviews["batches"].values())}


def _citation_inspection(repo: Path, batch: dict) -> tuple[list[dict], dict]:
    # Reuse ONLY the linter's bounded scope/grammar reader. This does not collect
    # governing records, load approval or consult a migration authority view.
    from admission_source_io import SourceIORefusal
    from observation_admission import _AdmissionIO
    import summaries_projection as sp
    module = _reference_reader()
    repo = Path(repo).resolve()
    try:
        with closing(_AdmissionIO(repo)) as source_io:
            tree = sp.resolve_tree(repo, _source_io=source_io)
            source_io.bind_tree(tree)
            files, _, refused = module.resolve_scope(repo, [], _source_io=source_io)
            ap.require(not refused, "migration-citation-scope-refused")
            for name in ("AGENTS.md", "README.md", "USER_GUIDE.md", "CHANGELOG.md"):
                path = repo / name
                if source_io.metadata(path) is not None: files.append(path)
            listed = set(files)
            files += [path for path in _tracked_instruction_sources(repo, source_io, module)
                      if path not in listed]
            briefs = Path(tree).relative_to(repo).as_posix() + "/briefs/"
            inspection = _inspect_citation_sources(repo, batch, module, files, source_io, briefs=briefs)
            source_io.revalidate()
            return inspection
    except (SourceIORefusal, UnicodeError):
        raise Refused("migration-citation-scope-refused") from None


def _tracked_instruction_sources(repo: Path, source_io, module) -> list[Path]:
    """Tracked instruction files and tracked local-skill files: one source rule for both.

    Membership is the git index, read through cr.git's isolated environment, so an exported
    GIT_INDEX_FILE or GIT_DIR never redirects it. An untracked or ignored install never changes
    the verdict. Every path returned is read from the working tree by the caller, through the same
    leaf and ancestor symlink refusal, containment check and size bound as every other citation
    source; nothing here follows a link.

    A tracked link (index mode 120000) at or under a skill root is a candidate whatever its
    suffix, so a linked skill directory reaches the leaf-link refusal instead of being skipped:
    a harness loads the skill through the link. A submodule (index mode 160000) at or under a
    skill root is refused here: a harness loads its skill files, and the index lists none of them.

    A tracked directory link outside the skill roots is not followed. When its target is tracked
    in this repository, the target's instruction files are already scanned at their real paths.
    What remains is a target outside this index: out of the repository, ignored or untracked, or
    under a directory name the linter excludes. That is a known limitation, and it matches the
    membership rule above: content the index does not list never changes the verdict.

    discover()'s exclusions are not applied: each decides what the instruction migration may
    rewrite, and none decides what a harness loads. An instruction file is dropped only where the
    linter's own walk drops a file, under an excluded directory name or an excluded prefix, because
    those mark text that is not this repository's claim: test fixtures, dated records, third-party
    captures, inbox drops and regenerated output. No skill-root file is dropped by directory name,
    so a skill named `build` or `dist` stays in scope. The one skill-root file dropped is each
    root's forge-log.md, an append-only dated record whose entries are never edited.

    A tracked candidate with no working-tree entry is refused, whatever the cause: an unstaged
    deletion, a skip-worktree bit or a sparse checkout. The index says the file is in force; the
    gate vouches only for bytes it read."""
    import instruction_migration as im
    try:
        listing = cr.git(repo, "ls-files", "-s", "-z")
    except OSError:
        raise Refused("migration-citation-scope-refused") from None
    ap.require(listing.returncode == 0, "migration-citation-scope-refused")
    # `-s` prints "<mode> <object> <stage>\t<path>"; a conflicted path lists once per stage.
    modes: dict[str, set[str]] = {}
    for record in listing.stdout.split(b"\0"):
        if not record: continue
        meta, sep, raw = record.partition(b"\t")
        ap.require(bool(sep) and bool(raw), "migration-citation-scope-refused")
        modes.setdefault(os.fsdecode(raw), set()).add(meta.split(b" ", 1)[0].decode("ascii"))
    tracked = list(modes)
    excluded = module.excluded_paths(repo, _source_io=source_io)
    roots = set(LOCAL_SKILLS_DIRS) | {PurePosixPath(d).parent.as_posix() for d in LOCAL_SKILLS_DIRS}
    paths = []
    for rel in tracked:
        name = PurePosixPath(rel)
        under = any(rel.startswith(d + "/") for d in LOCAL_SKILLS_DIRS)
        if (under or rel in roots) and "160000" in modes[rel]:
            raise _with_remedy(Refused("migration-citation-scope-refused"), SUBMODULE_SOURCE_REMEDY)
        skill = (under and (name.suffix in module.SCAN_EXTENSIONS or "120000" in modes[rel])
                 or rel in roots)
        # The index holds no directory entry: an entry AT a skill root or its parent is a link,
        # a file or a submodule. Keeping it sends a linked root to the symlink refusal.
        if rel in FORGE_LOGS or not (skill or im.is_instruction_name(name.name)):
            continue
        path = repo / rel
        if not skill and (set(name.parts[:-1]) & module.EXCLUDED_DIR_NAMES
                          or any(path == e or e in path.parents for e in excluded)):
            continue
        if source_io.metadata(path) is None:
            raise _with_remedy(Refused("migration-source-unreadable"), ABSENT_SOURCE_REMEDY)
        paths.append(path)
    return paths


def _with_remedy(refusal, remedy: str):
    """Attach advice to a refusal without touching its code, which stays the whole contract."""
    refusal.remedy = remedy
    return refusal


def _inspect_citation_sources(repo: Path, batch: dict, module, files, source_io,
                              briefs=None) -> tuple[list[dict], dict]:
    handles = {rule["handle"] for entry in batch["entries"] for rule in entry["affected_governs"]}
    slugs = {"rule:" + handle.split("/", 1)[1] for handle in handles}
    found = []; kinds = {"governing": [], "historical": []}
    for path in sorted(set(files)):
        try:
            path, rel = _checked(repo, path)
            ap.require(source_io.resolve(path) == path, "migration-symlink-refused")
        except Refused as refusal:
            if refusal.code == "migration-symlink-refused":
                _with_remedy(refusal, SYMLINK_SOURCE_REMEDY)
            raise
        metadata = source_io.metadata(path)
        ap.require(metadata is not None, "migration-source-unreadable")
        ap.require(metadata["st_size"] <= MAX_SOURCE_BYTES, "migration-source-oversize")
        text = source_io.read_bytes(path, max_bytes=MAX_SOURCE_BYTES).decode("utf-8")
        occurrences = [{"token": match[0], "start": match.start(), "end": match.end()}
            for pattern, wanted in ((module.HANDLE_RE, handles), (module.RULE_RE, slugs))
            for match in pattern.finditer(text) if match[0] in wanted]
        if not occurrences: continue
        found.append({"path": rel, "sha256": _digest(text.encode())})
        if rel == "CHANGELOG.md":
            from changelog_reference import classify_changelog_references
            partition = classify_changelog_references(text, occurrences)
        elif briefs is not None and rel.startswith(briefs):
            # A brief is pre-decision exploration: no reader takes its citations as authority.
            partition = {"governing": [], "historical": occurrences}
        else: partition = {"governing": occurrences, "historical": []}
        for key in kinds: kinds[key] += [dict(row, path=rel) for row in partition[key]]
    return found, kinds


def _citation_inventory(repo: Path, batch: dict) -> list[dict]:
    return _citation_inspection(repo, batch)[0]


def inventory(repo_root, batch_path) -> dict:
    """Observe exact sources/dependencies; no close or application claim is made."""
    repo = Path(repo_root).resolve(); batch = load_batch(repo, batch_path)
    sources = [resolve_clause(repo, entry) for entry in batch["entries"]]
    _architectural_refs(repo, batch["authorizing_refs"] + [handle for entry in batch["entries"]
                                                       for handle in entry["replacement_handles"]])
    tree = _configuration(repo).docs_root
    signed, pairings = _signed_inventory(repo, tree)
    citations = _citation_inventory(repo, batch)
    drift = [key for key, observed in (("signed_dependencies", signed), ("citation_dependencies", citations))
             if batch[key] != observed]
    return {"authority": "none", "batch_id": batch["batch_id"], "batch_sha256": _digest(_read(repo, batch_path)[0].encode()),
            "sources": sources, "dependencies": {"signed": signed, "citations": citations},
            "signed_pairings": pairings, "dependency_drift": drift,
            "historical_links": [entry["historical_destination"] for entry in batch["entries"]],
            "affected_readers": ["summaries", "doctrine", "rules-catalog", "reference-lint", "backfill",
                                 "reviews", "arch/recovery", "observation/survey", "live-constraints", "query/audit/cleanup"],
            "publication": _inventory_publication(repo, batch_path, batch),
            # Inventory reads sources only. It proves no close and no application, before
            # or after one exists; the authority view is the reader that proves both.
            "approval": {"state": "UNOBSERVED", "limit": "inventory-proves-no-close"},
            "application": {"state": "UNOBSERVED", "limit": "inventory-proves-no-application"}}


def _inventory_publication(repo: Path, batch_path, batch: dict) -> dict:
    """Whether this batch's locator is committed, without proving the close behind it."""
    try:
        _, rel = _checked(repo, batch_path)
        expected = Path(rel).with_name(batch["batch_id"] + ".application.json").as_posix()
        rows = [row for row in _discover_publications(repo) if row["path"] == expected]
    except Refused as refusal:
        return {"state": "UNOBSERVED", "limit": refusal.code}
    if not rows:
        return {"state": "ABSENT", "path": expected}
    return {"state": "COMMITTED", **{k: rows[0][k] for k in ("path", "sha256", "publication_commit")}}


def dry_run(repo_root, batch_path) -> dict:
    from implementation_migration_apply import dry_run as plan_application
    return plan_application(repo_root, batch_path)


def _config_inputs() -> tuple[str, ...]:
    return (bionic_config.BIONIC_CONFIG_FILENAME, bionic_config.CONFIG_FILENAME,
        *(tree + "/" + name for tree in (bionic_config.DEFAULT_DOCS_DIR, bionic_config.LEGACY_DOCS_DIR)
          for name in ("manifest.yml", ".migrating")))


# Content-addressed object bodies shared by every read in this process. An object id
# names the same bytes in every repository, and replace refs and grafts are off for
# each read, so a hit never serves a stale answer. Each read still walks only objects
# it reached from its own fresh HEAD, so absence and reachability are never cached.
_OBJECT_MEMO: dict[str, tuple[bytes, bytes]] = {}
_OBJECT_MEMO_LIMIT = 256 * 1024 * 1024
_object_memo_bytes = 0
MAX_HISTORY_OBJECT_BYTES = 16 * 1024 * 1024
_TREE_MODE = 0o040000


def _history_env() -> dict[str, str]:
    """The council-records read environment: no gateway key, no global or system config."""
    return cr._git_env()


def _canonical_mode(raw: bytes) -> bytes:
    """Normalize a raw tree mode the way `git ls-tree` prints it."""
    ap.require(re.fullmatch(rb"[0-7]{5,6}", raw) is not None, "migration-config-history-refused")
    mode = int(raw, 8); kind = mode & 0o170000
    if kind == 0o100000: mode = 0o100755 if mode & 0o100 else 0o100644
    elif kind in (_TREE_MODE, 0o120000, 0o160000): mode = kind
    else: ap.require(False, "migration-config-history-refused")
    return b"%06o" % mode


def _object_bodies(repo: Path, identities: set[str], kind: bytes, max_bytes: int) -> dict[str, bytes]:
    """Object bodies by id, refusing any of another kind or above `max_bytes`."""
    global _object_memo_bytes
    result = {}
    for oid in identities:
        ap.require(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid) is not None, "history-unavailable")
        if oid in _OBJECT_MEMO:
            cached_kind, content = _OBJECT_MEMO[oid]
            ap.require(cached_kind == kind, "history-unavailable")
            ap.require(len(content) <= max_bytes, "migration-source-oversize")
            result[oid] = content
    wanted = sorted(identities.difference(result))
    if not wanted: return result
    request = ("\n".join(wanted) + "\n").encode()
    args = ["git", "-C", str(repo), "cat-file"]
    # Inspect sizes before asking Git to materialize any raw object bodies.
    sizes = subprocess.run(args + ["--batch-check"], input=request, capture_output=True,
                           env=_history_env())
    ap.require(sizes.returncode == 0, "history-unavailable")
    rows = sizes.stdout.splitlines()
    ap.require(len(rows) == len(wanted), "history-unavailable")
    lengths = []
    for oid, row in zip(wanted, rows):
        fields = row.split()
        ap.require(len(fields) == 3 and fields[0].decode() == oid and fields[1] == kind,
                   "history-unavailable")
        size = int(fields[2]); lengths.append(size)
        ap.require(0 <= size <= max_bytes, "migration-source-oversize")
    bodies = subprocess.run(args + ["--batch"], input=request, capture_output=True,
                            env=_history_env())
    ap.require(bodies.returncode == 0, "history-unavailable")
    cursor = 0
    for oid, size in zip(wanted, lengths):
        end = bodies.stdout.find(b"\n", cursor)
        ap.require(end >= cursor and bodies.stdout[cursor:end] ==
                   f"{oid} {kind.decode()} {size}".encode(), "history-unavailable")
        cursor = end + 1; content = bodies.stdout[cursor:cursor + size]; cursor += size
        ap.require(len(content) == size and bodies.stdout[cursor:cursor + 1] == b"\n",
                   "history-unavailable")
        result[oid] = content; cursor += 1
    ap.require(cursor == len(bodies.stdout), "history-unavailable")
    for oid in wanted:
        if _object_memo_bytes + len(result[oid]) > _OBJECT_MEMO_LIMIT:
            _OBJECT_MEMO.clear(); _object_memo_bytes = 0
        _OBJECT_MEMO[oid] = (kind, result[oid]); _object_memo_bytes += len(result[oid])
    return result


class _InvocationHistory:
    """The reachable lineage of one read. Commits and HEAD are read fresh on every call;
    only immutable object bodies are shared, by object id."""
    def __init__(self, repo: Path):
        self.repo = repo
        head = cr.git(repo, "rev-parse", "HEAD")
        ap.require(head.returncode == 0, "history-unavailable")
        self.head = head.stdout.decode().strip()
        result = cr.git(repo, "rev-list", "--reverse", "--topo-order", "--parents", self.head)
        ap.require(result.returncode == 0 and result.stdout.strip(), "history-unavailable")
        rows = [line.split() for line in result.stdout.decode().splitlines()]
        self.commits = [row[0] for row in rows]
        self.parents = {row[0]: row[1:] for row in rows}
        self.objects = {}; self.configurations = {}; self.trees = {}; self.roots = None
        ap.require(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", self.head) is not None, "history-unavailable")
        self.oid_bytes = len(self.head) // 2

    def metadata(self, paths: tuple[str, ...], *, max_bytes=MAX_SOURCE_BYTES) -> dict:
        """The `ls-tree <commit> -- <paths>` answer for every commit, from batched object reads."""
        wanted = {}
        for path in paths:
            parts = path.encode("utf-8").split(b"/")
            ap.require(all(parts) and path not in wanted, "migration-config-history-refused")
            wanted[path] = parts
        if self.roots is None:
            bodies = self._read(set(self.commits), b"commit", MAX_HISTORY_OBJECT_BYTES)
            self.roots = {}
            for commit in self.commits:
                match = re.match(rb"tree ([0-9a-f]{40}|[0-9a-f]{64})\n", bodies[commit])
                ap.require(match is not None, "history-unavailable")
                self.roots[commit] = match[1].decode()
        # Walk every (commit, path) one directory level at a time, one batch per level.
        pending = {(commit, path): self.roots[commit] for commit in self.commits for path in wanted}
        found = {}; level = 0
        while pending:
            self._tree_entries(set(pending.values()))
            following = {}
            for (commit, path), tree in pending.items():
                parts = wanted[path]
                entry = self.trees[tree].get(parts[level])
                if entry is None: continue
                if level + 1 == len(parts): found[(commit, path)] = entry
                elif entry[0] == b"040000": following[(commit, path)] = entry[1]
            pending = following; level += 1
        result = {}
        for commit in self.commits:
            entries = {}
            for path in paths:
                entry = found.get((commit, path))
                if entry is None: continue
                mode, oid = entry
                ap.require(mode in (b"100644", b"100755"), "migration-config-history-refused")
                entries[path] = (mode.decode(), oid)
            result[commit] = entries
        self.blobs({oid for entries in result.values() for _, oid in entries.values()}, max_bytes=max_bytes)
        return result

    def _tree_entries(self, identities: set[str]) -> None:
        missing = identities.difference(self.trees)
        if not missing: return
        bodies = self._read(missing, b"tree", MAX_HISTORY_OBJECT_BYTES)
        for oid in missing:
            body = bodies[oid]; entries = {}; cursor = 0
            while cursor < len(body):
                space = body.find(b" ", cursor); nul = body.find(b"\0", space + 1)
                ap.require(cursor < space < nul and nul + 1 + self.oid_bytes <= len(body), "history-unavailable")
                name = body[space + 1:nul]
                ap.require(name not in entries and name and b"/" not in name, "migration-config-history-refused")
                entries[name] = (_canonical_mode(body[cursor:space]),
                                 body[nul + 1:nul + 1 + self.oid_bytes].hex())
                cursor = nul + 1 + self.oid_bytes
            self.trees[oid] = entries

    def blobs(self, identities: set[str], *, max_bytes=MAX_SOURCE_BYTES) -> None:
        wanted = identities.difference(self.objects)
        if not wanted: return
        self.objects.update(self._read(wanted, b"blob", max_bytes))

    def _read(self, identities: set[str], kind: bytes, max_bytes: int) -> dict[str, bytes]:
        """Object bodies by id, refusing any of another kind or above `max_bytes`."""
        return _object_bodies(self.repo, identities, kind, max_bytes)

    def descendant(self, commit: str, ancestor: str) -> bool:
        pending = [commit]; seen = set()
        while pending:
            current = pending.pop()
            if current == ancestor: return True
            if current in seen: continue
            seen.add(current)
            ap.require(current in self.parents, "history-unavailable")
            pending.extend(self.parents[current])
        return False

    def unchanged_head(self) -> None:
        head = cr.git(self.repo, "rev-parse", "HEAD")
        ap.require(head.returncode == 0 and head.stdout.decode().strip() == self.head,
                   "migration-history-head-changed")


def _legacy_config_text(text: str) -> str:
    """Insert an absent version within the same isolated YAML mapping document."""
    doc = _no_git_document(text)
    if doc is None: doc = {}
    ap.require(isinstance(doc, dict), "migration-layout-refused")
    if "config_version" in doc: return text
    node = yaml.compose(text, Loader=yaml.SafeLoader)
    version = 'config_version: "1"'
    if node is None or (isinstance(node, yaml.ScalarNode) and
                        node.tag == "tag:yaml.org,2002:null" and node.value == ""):
        end = next((token.start_mark.index for token in yaml.scan(text)
                    if isinstance(token, yaml.DocumentEndToken)), len(text))
        separator = "\n" if end and text[end - 1] != "\n" else ""
        return text[:end] + separator + version + "\n" + text[end:]
    ap.require(isinstance(node, yaml.MappingNode), "migration-layout-refused")
    kind = yaml.FlowMappingStartToken if node.flow_style else yaml.BlockMappingStartToken
    token = next(token for token in yaml.scan(text) if isinstance(token, kind))
    if node.flow_style:
        offset = token.end_mark.index
        return text[:offset] + version + (", " if doc else "") + text[offset:]
    offset = token.start_mark.index
    return text[:offset] + version + "\n" + " " * token.start_mark.column + text[offset:]


def _layout_values(inputs: dict[str, str]):
    """Normalize only the selected legacy buffer; canonical policy owns selection."""
    normalized = dict(inputs)
    selected = next((name for name in (bionic_config.BIONIC_CONFIG_FILENAME,
        bionic_config.CONFIG_FILENAME) if name in inputs), None)
    if selected is not None: normalized[selected] = _legacy_config_text(inputs[selected])
    return bionic_config.select_layout_inputs(normalized)


def _discovery_directory(repo: Path, inputs: dict[str, str]) -> str:
    values, _ = _layout_values(inputs)
    _, bounded = _checked(repo, values[1] + "/adrs/migrations")
    return bounded


def _historical_directory(repo: Path, entries: dict, history: _InvocationHistory) -> str:
    """Resolve exact raw config identities once through an isolated canonical buffer."""
    identity = tuple(sorted(entries.items()))
    if identity not in history.configurations:
        inputs = {rel: history.objects[oid].decode("utf-8") for rel, (_, oid) in entries.items()}
        history.configurations[identity] = _discovery_directory(repo, inputs)
    return history.configurations[identity]


def _discover_publications(repo: Path) -> list[dict]:
    """Discover immutable publication paths in this reachable lineage, including deletions."""
    return _publication_inventory(repo)


def _publication_worktree_paths(repo: Path, directories: set[str]) -> set[str]:
    paths = set()
    for bounded in sorted(directories):
        folder = repo / bounded
        if folder.exists() or folder.is_symlink():
            ap.require(not cr.reached_through_symlink(folder), "migration-symlink-refused")
            paths.update(p.relative_to(repo).as_posix() for p in folder.rglob("*.application.json"))
    return paths


def _publication_index_roster(repo: Path, directories: set[str]) -> tuple[tuple[str, bytes], ...]:
    """Capture publication index entries even when their worktree files are absent."""
    listing = cr.git(repo, "ls-files", "--stage", "-z", "--", *sorted(directories))
    ap.require(listing.returncode == 0, "migration-pending-index-refused")
    rows = []
    for entry in listing.stdout.split(b"\0"):
        if not entry: continue
        metadata, raw_path = entry.split(b"\t", 1)
        rel = raw_path.decode("utf-8")
        if rel.endswith(".application.json"):
            _checked(repo, rel)
            rows.append((rel, metadata))
    return tuple(sorted(rows))


def _publication_inventory(repo: Path, *, _pending=None, _capture=None) -> list[dict]:
    """The strict inventory admits only one internally validated, never-committed candidate."""
    ap.full_history(repo)
    inputs = {}
    for rel in _config_inputs():
        path = repo / rel
        if path.exists() or path.is_symlink(): inputs[rel] = _read(repo, rel)[0]
        state = cr.path_state(repo, rel)
        absent = not state.tracked and not state.symlink and state.head is None and \
            state.index is None and state.worktree is None and not path.exists()
        regular = rel in inputs and _digest(inputs[rel].encode()) == state.worktree
        committed = regular and (state.clean or (Path(rel).name == "manifest.yml" and
            _manifest_allocation_only(repo, rel, inputs[rel], state)))
        ap.require(committed or absent,
                   "migration-config-not-committed")
    directory = _discovery_directory(repo, inputs)
    history = _InvocationHistory(repo)
    directories = {directory}
    configurations = history.metadata(_config_inputs())
    for entries in configurations.values():
        directories.add(_historical_directory(repo, entries, history))
    paths = set()
    for bounded in sorted(directories):
        names = cr.git(repo, "log", "--full-history", "--format=", "--name-only", "--no-renames",
                       "-z", history.head, "--", bounded)
        ap.require(names.returncode == 0, "history-unavailable")
        paths.update(name.decode("utf-8").strip("\n") for name in names.stdout.split(b"\0")
                     if name.strip(b"\n").endswith(b".application.json"))
    current_paths = _publication_worktree_paths(repo, directories)
    paths.update(current_paths)
    index_roster = ()
    if _pending is not None:
        index_roster = _publication_index_roster(repo, directories)
        paths.update(rel for rel, _ in index_roster)
        paths.add(_pending["path"])
    if _capture is not None: _capture.append((directories, current_paths, index_roster))
    publications = []
    snapshots = history.metadata(tuple(sorted(paths))) if paths else {}
    for rel in sorted(paths):
        _checked(repo, rel)
        ap.require(Path(rel).parent.as_posix() == directory, "migration-publication-path-refused")
        first = None; content = None
        for commit in history.commits:
            entry = snapshots[commit].get(rel)
            if entry is not None:
                blob = history.objects[entry[1]]
                if first is None: first, content = commit, blob
                ap.require(blob == content, "migration-publication-history-changed")
            elif first is not None:
                ap.require(not history.descendant(commit, first), "migration-publication-deleted")
        if first is None and _pending is not None and rel == _pending["path"]:
            if (repo / rel).exists():
                ap.require(_read(repo, rel)[0].encode() == _pending["canonical_bytes"],
                           "migration-pending-bytes-refused")
            continue  # A pending witness confers no publication or authority.
        ap.require(first is not None, "migration-publication-not-committed")
        text, _ = _read(repo, rel)
        ap.require(cr.is_clean(repo, rel) and text.encode() == content,
                   "migration-publication-not-committed")
        publications.append({"path": rel, "sha256": _digest(content), "content": content,
                             "publication_commit": first})
    history.unchanged_head()
    return publications


def _canonical_digest(doc) -> str:
    return _digest(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode())


_PUBLICATION_CLOSE_KEYS = ("batch", "book_id", "run_id", "book_content_hash", "slot", "gate_prompt")


def _publication_locator_bytes(proof, run_rel: str) -> bytes:
    """The sole publisher1 formatter; historical bytes are validated without reformatting."""
    witness = dict(record_type=PUBLICATION_RECORD_TYPE, format_version="1",
        publisher="implementation-migration/1", run_path=run_rel,
        binding_sha256=_canonical_digest(proof.binding), declaration_sha256=_canonical_digest(proof.slot),
        **{key: proof.binding[key] for key in _PUBLICATION_CLOSE_KEYS})
    return (json.dumps(witness, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _publication_subject(repo: Path, publication: dict):
    """Validate locator identity against the shared historical close, without eligibility."""
    witness = _yaml(publication["content"].decode("utf-8"))
    fields = {"record_type", "format_version", "publisher", "batch", "book_id", "run_id",
              "book_content_hash", "slot", "gate_prompt", "run_path", "binding_sha256",
              "declaration_sha256"}
    ap.require(isinstance(witness, dict) and set(witness) == fields and
               witness["record_type"] == PUBLICATION_RECORD_TYPE and
               witness["format_version"] == "1" and witness["publisher"] == "implementation-migration/1",
               "migration-publication-shape-refused")
    batch_ref = witness["batch"]
    ap.require(isinstance(batch_ref, dict) and set(batch_ref) == {"path", "sha256"} and
               isinstance(batch_ref["path"], str), "migration-publication-shape-refused")
    _checked(repo, batch_ref["path"])
    run_path, _ = _checked(repo, witness["run_path"])
    proof = ap.validate_historical_migration_binding(repo, run_path, slot=witness["slot"],
        batch_path=batch_ref["path"], batch_sha256=batch_ref["sha256"])
    ap.require(all(witness[key] == proof.binding[key] for key in
               ("book_id", "run_id", "book_content_hash", "slot", "gate_prompt", "batch")) and
               witness["binding_sha256"] == _canonical_digest(proof.binding) and
               witness["declaration_sha256"] == _canonical_digest(proof.slot),
               "migration-publication-identity-refused")
    batch = load_batch(repo, batch_ref["path"])
    expected_path = Path(batch_ref["path"]).with_name(batch["batch_id"] + ".application.json")
    ap.require(publication["path"] == expected_path.as_posix(), "migration-publication-path-refused")
    return proof


def _publication_proof(repo: Path, publication: dict):
    proof = _publication_subject(repo, publication)
    batch_ref = proof.binding["batch"]
    first = publication["publication_commit"]
    ap.require(first != proof.proof_commit and cr.git(repo, "merge-base", "--is-ancestor",
               proof.proof_commit, first).returncode == 0, "migration-publication-precedes-close")
    # The locator's batch itself must have existed, unchanged, at publication.
    blob = cr.git(repo, "show", first + ":" + batch_ref["path"])
    ap.require(blob.returncode == 0 and _digest(blob.stdout) == batch_ref["sha256"],
               "migration-batch-not-at-publication")
    return proof


def _pending_close(repo: Path, pending: dict):
    """Derive one committed owning close; supplied digests never supply proof."""
    ap.require(isinstance(pending, dict) and set(pending) == {"path", "sha256", "close_identity"},
               "migration-pending-shape-refused")
    close = pending["close_identity"]
    ap.require(isinstance(close, dict) and set(close) == set(_PUBLICATION_CLOSE_KEYS) and
        isinstance(close["run_id"], str) and re.fullmatch(r"RUN-[0-9]{3,}", close["run_id"]) and
        type(close["gate_prompt"]) is int and close["gate_prompt"] > 0 and
        isinstance(pending["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", pending["sha256"]),
        "migration-pending-shape-refused")
    subject = close["batch"]
    ap.require(isinstance(subject, dict) and set(subject) == {"path", "sha256"} and
        isinstance(subject["path"], str) and isinstance(subject["sha256"], str) and
        re.fullmatch(r"[0-9a-f]{64}", subject["sha256"]) and isinstance(pending["path"], str),
        "migration-pending-shape-refused")
    for rel in (subject["path"], pending["path"]):
        ap.require(_checked(repo, rel)[1] == rel, "migration-publication-path-refused")
    from admission_source_io import SourceIO, SourceIORefusal
    runs = _checked(repo, _configuration(repo).docs_root / "promptbooks/runs")[0]
    try:
        with closing(SourceIO(repo)) as source_io:
            matches = []
            for book in source_io.glob(runs, "*"):
                _checked(repo, book)
                if source_io.kind(book) != "directory": continue
                ap.require(source_io.resolve(book) == book, "migration-symlink-refused")
                for path in source_io.glob(book, "run-" + close["run_id"] + ".yaml"):
                    doc = _no_git_document(_bounded_read(repo, path, 4 * 1024 * 1024)[0], _member=True)
                    ap.require(isinstance(doc, dict), "migration-pending-close-refused")
                    entries = doc.get("migration_bindings", [])
                    ap.require(isinstance(entries, list) and all(isinstance(entry, dict) for entry in entries),
                               "migration-pending-close-refused")
                    matches += [path for entry in entries if
                        {key: entry.get(key) for key in _PUBLICATION_CLOSE_KEYS} == close]
            ap.require(len(matches) == 1, "migration-pending-close-refused")
            proof = ap.validate_historical_migration_binding(repo, matches[0], slot=close["slot"],
                batch_path=subject["path"], batch_sha256=subject["sha256"])
            run_rel = _checked(repo, matches[0])[1]
            raw = _publication_locator_bytes(proof, run_rel)
            _publication_subject(repo, {"path": pending["path"], "content": raw})
            source_io.revalidate()
            return proof, raw
    except SourceIORefusal:
        raise Refused("migration-pending-roster-refused") from None


def _pending_target_state(repo: Path, pending: dict):
    path, rel = _checked(repo, pending["path"])
    state = cr.path_state(repo, rel)
    ap.require(not state.symlink, "migration-symlink-refused")
    if path.exists():
        raw = _read(repo, rel)[0].encode()
        ap.require(_digest(raw) == pending["sha256"] == state.worktree, "migration-pending-bytes-refused")
    else:
        ap.require(not state.tracked and state.index is None and state.head is None,
                   "migration-pending-bytes-refused")
    if state.tracked:
        listing = cr.git(repo, "ls-files", "-s", "-z", "--", rel)
        rows = [row.split(b"\t", 1)[0].split() for row in listing.stdout.split(b"\0") if row]
        ap.require(listing.returncode == 0 and len(rows) == 1 and len(rows[0]) == 3 and
            rows[0][0] in (b"100644", b"100755") and rows[0][2] == b"0" and
            state.index == pending["sha256"], "migration-pending-index-refused")
    return state


def _discover_apply_publications(repo: Path, *, pending: dict) -> tuple[list[dict], dict | None]:
    """Validate a candidate independently; only a proved committed witness is returned selected."""
    repo = Path(repo).resolve()
    ap.require(_own_git(repo), "migration-git-required")
    ap.full_history(repo)
    head = cr.git(repo, "rev-parse", "HEAD").stdout
    configs = {rel: cr.path_state(repo, rel) for rel in _config_inputs()}
    proof, canonical = _pending_close(repo, pending)
    state = _pending_target_state(repo, pending)
    if state.worktree is None:
        ap.require(pending["sha256"] == _digest(canonical), "migration-pending-bytes-refused")
    capture = []
    publications = _publication_inventory(repo, _pending={**pending, "canonical_bytes": canonical}, _capture=capture)
    selected = next((row for row in publications if row["path"] == pending["path"]), None)
    if selected is not None:
        ap.require(selected["sha256"] == pending["sha256"] and
            _publication_proof(repo, selected).binding == proof.binding, "migration-publication-identity-refused")
    fresh, fresh_raw = _pending_close(repo, pending)
    ap.require(fresh.binding == proof.binding and fresh_raw == canonical and
        _pending_target_state(repo, pending) == state and
        _publication_worktree_paths(repo, capture[0][0]) == capture[0][1] and
        _publication_index_roster(repo, capture[0][0]) == capture[0][2] and
        {rel: cr.path_state(repo, rel) for rel in _config_inputs()} == configs,
        "migration-pending-input-changed")
    ap.require(all(cr.is_clean(repo, row["path"]) and _digest(_read(repo, row["path"])[0].encode()) ==
        row["sha256"] for row in publications), "migration-publication-not-committed")
    ap.full_history(repo)
    ap.require(cr.git(repo, "rev-parse", "HEAD").stdout == head, "migration-history-head-changed")
    return publications, selected


def _raw_architecture(repo: Path) -> tuple[list[dict], list[dict]]:
    """Read canonical source identities and displacement edges without projections."""
    config = _configuration(repo); adrs = config.docs_root / "adrs"
    records = []; fingerprints = []; identities = set(); handles = set()
    for path in adr_frontmatter.adr_paths(adrs):
        text, rel = _read(repo, path); front = adr_frontmatter.frontmatter_block(text)
        ap.require(front is not None, "migration-source-frontmatter-refused")
        fm = _yaml(front)
        ap.require(isinstance(fm, dict) and "record_type" not in fm, "migration-source-record-refused")
        _filename_identity(path, fm, config)
        ap.require(fm["id"] not in identities, "migration-source-identity-refused")
        identities.add(fm["id"])
        rules = fm.get("governs", [])
        ap.require(isinstance(rules, list), "migration-source-governs-refused")
        for rule in rules:
            ap.require(isinstance(rule, dict) and isinstance(rule.get("handle"), str) and
                       rule["handle"].startswith(fm["id"] + "/") and rule["handle"] not in handles,
                       "migration-governs-identity-refused")
            handles.add(rule["handle"])
            targets = rule.get("retires", [])
            ap.require(isinstance(targets, list) and all(isinstance(h, str) for h in targets),
                       "migration-source-governs-refused")
            records.append({"handle": rule["handle"], "retires": targets, "path": rel,
                "live_host": path.parent == adrs and fm.get("status") == "Accepted"})
        fingerprints.append({"path": rel, "sha256": _digest(text.encode())})
    return records, fingerprints


def _manifest_allocation_only(repo: Path, rel: str, text: str, state) -> bool:
    """Only documented monotonic allocation leaves may differ from committed manifest bytes."""
    if not state.tracked or state.symlink or _digest(text.encode()) != state.worktree:
        return False
    identities = []
    for args, stage in ((('ls-tree', '-z', 'HEAD', '--', rel), b'blob'),
                        (('ls-files', '-s', '-z', '--', rel), b'0')):
        result = cr.git(repo, *args)
        rows = [row for row in result.stdout.split(b'\0') if row]
        if result.returncode != 0 or len(rows) != 1: return False
        fields, path = rows[0].split(b'\t', 1); fields = fields.split()
        if (path != rel.encode() or len(fields) != 3 or fields[0] not in (b'100644', b'100755')):
            return False
        oid = fields[2] if stage == b'blob' else fields[1]
        if (fields[1] if stage == b'blob' else fields[2]) != stage: return False
        identities.append(oid.decode())
    history = _InvocationHistory(repo); history.blobs(set(identities))
    raw = [history.objects[oid] for oid in identities] + [text.encode()]
    if [_digest(body) for body in raw] != [state.head, state.index, state.worktree]: return False
    documents = [_no_git_document(body.decode('utf-8')) for body in raw]
    if not all(isinstance(doc, dict) for doc in documents): return False
    leaves = {'adr': ('next_number',), 'promptbook': ('next_number',),
              'observation': ('next_number', 'next_survey_number')}
    normalized = []
    for doc in documents:
        other = dict(doc)
        for block, keys in leaves.items():
            if block not in doc: continue
            if not isinstance(doc[block], dict): return False
            section = dict(doc[block])
            for key in keys:
                if key in section:
                    if type(section[key]) is not int or section[key] < 1: return False
                    del section[key]
            # Only an introduced counter-only parent supplies no noncounter field.
            if block not in documents[0] and doc[block] and not section:
                del other[block]
            else:
                other[block] = section
        normalized.append(other)
    def same(left, right):
        seen = set(); work = 0
        def visit(left, right):
            nonlocal work
            work += 1
            ap.require(work <= _NO_GIT_WORK_LIMIT, "migration-footprint-ambiguous")
            if type(left) is not type(right): return False
            pair = (id(left), id(right))
            if pair in seen: return True
            seen.add(pair)
            if isinstance(left, dict):
                lkeys = {(type(key), key): key for key in left}
                rkeys = {(type(key), key): key for key in right}
                return lkeys.keys() == rkeys.keys() and all(
                    visit(left[key], right[rkeys[identity]]) for identity, key in lkeys.items())
            if isinstance(left, (list, tuple)):
                return len(left) == len(right) and all(visit(a, b) for a, b in zip(left, right))
            if isinstance(left, set):
                return {(type(v), v) for v in left} == {(type(v), v) for v in right}
            return left == right
        return visit(left, right)
    if not same(normalized[0], normalized[1]) or not same(normalized[1], normalized[2]): return False
    for block, keys in leaves.items():
        for key in keys:
            present = [key in doc.get(block, {}) for doc in documents]
            values = [doc.get(block, {}).get(key, 1) for doc in documents]
            if any(before and not after for before, after in zip(present, present[1:])): return False
            if not values[0] <= values[1] <= values[2]: return False
    # Textual qualification and canonical layout must not change with representation.
    inputs = {name: _read(repo, name)[0] for name in _config_inputs()
              if (repo / name).exists() or (repo / name).is_symlink()}
    directories = []; qualifies = []
    for body in raw:
        text = body.decode('utf-8')
        qualifies.append(bionic_config.is_crux_manifest_text(text))
        candidate = dict(inputs); candidate[rel] = text
        directories.append(_discovery_directory(repo, candidate))
    history.unchanged_head()
    return qualifies[0] == qualifies[1] == qualifies[2] and directories[0] == directories[1] == directories[2]


def _unchanged_input(repo: Path, item: dict) -> None:
    """The per-path check: the read, then the working tree, index and HEAD agree with the digest."""
    text, _ = _read(repo, item["path"])
    state = cr.path_state(repo, item["path"])
    exact = _digest(text.encode()) == item["sha256"]
    committed = state.clean and state.head == item["sha256"]
    if exact and not committed and Path(item["path"]).name == "manifest.yml":
        committed = _manifest_allocation_only(repo, item["path"], text, state)
    ap.require(exact and committed,
               "migration-input-not-committed")


_FILE_MODES = ("100644", "100755")


def _listing(repo: Path, *args: str) -> dict | None:
    """`git <args> -z` records as {path: [(mode, a, b), ...]}, or None on any failure or malformed record."""
    done = cr.git(repo, *args)
    if done.returncode != 0:
        return None
    rows: dict = {}
    for record in done.stdout.split(b"\0"):
        if not record:
            continue
        meta, separator, raw = record.partition(b"\t")
        fields = meta.decode("ascii", "replace").split(" ")
        if not separator or not raw or len(fields) != 3:
            return None
        rows.setdefault(os.fsdecode(raw), []).append(tuple(fields))
    return rows


def _unchanged_inputs(repo: Path, fingerprints: list[dict]) -> None:
    """Accept-only fast path over `_unchanged_input`: two listings and one two-call `cat-file`
    batch replace three git children per path. An item the fast path cannot vouch for takes the
    per-path check, so the fast path never refuses an item. The fast path is off when
    `_repository_state` is None (for example, a linked worktree). When the state bytes move
    during the loop, the entries the fast path relied on are compared again: a stat-only index
    rewrite is accepted, and a changed index or HEAD entry for a path the fast path accepted is
    refused."""
    import git_read_cache
    before = git_read_cache._repository_state(repo)
    candidates: dict[str, str] = {}
    blobs: dict[str, bytes] = {}
    index = head = None
    fast: set[str] = set()                               # rel paths the fast path accepted
    if before is not None:
        index = _listing(repo, "ls-files", "-s", "-z")
        head = _listing(repo, "ls-tree", "-z", "-r", "HEAD")
    if index is not None and head is not None:
        for item in fingerprints:
            rel = item["path"]; staged = index.get(rel, []); tip = head.get(rel, [])
            if (len(staged) == 1 and staged[0][2] == "0" and staged[0][0] in _FILE_MODES
                    and len(tip) == 1 and tip[0][1] == "blob" and tip[0][0] in _FILE_MODES
                    and tip[0][2] == staged[0][1]):
                candidates[rel] = staged[0][1]
    if candidates:
        try:
            blobs = _object_bodies(repo, set(candidates.values()), b"blob", MAX_SOURCE_BYTES)
        except (Refused, OSError, ValueError):
            blobs = {}                                   # the fast path is off; every item falls back
    for item in fingerprints:
        rel = item["path"]
        text, _ = _read(repo, rel)                       # symlink, containment and size refusals unchanged
        body = blobs.get(candidates.get(rel))
        absolute = repo / rel
        if (body is not None and _digest(text.encode()) == item["sha256"]
                and cr.sha256_bytes(body) == item["sha256"]
                and not absolute.is_symlink() and absolute.is_file()
                and cr.sha256_file(absolute) == item["sha256"]):
            fast.add(rel)
            continue
        _unchanged_input(repo, item)
    if fast and git_read_cache._repository_state(repo) != before:
        # The state bytes include the index's stat data, which `git status` rewrites with no
        # content change. Decide on the entries the fast path relied on.
        now_index = _listing(repo, "ls-files", "-s", "-z")
        now_head = _listing(repo, "ls-tree", "-z", "-r", "HEAD")
        ap.require(now_index is not None and now_head is not None and all(
            now_index.get(rel) == index[rel] and now_head.get(rel) == head[rel] for rel in fast),
            "migration-history-head-changed")


def _disposition_inputs(repo: Path, batch: dict, historical: dict, signed: list[dict],
                        published: bool = False) -> list[dict]:
    """Repeat owning producer checks; a receipt records evidence, never authority."""
    import argparse
    import migration_disposition as dispositions
    import summaries_projection as summaries
    config = _configuration(repo); tree = config.docs_root
    ledger, _ = _read(repo, tree / "adrs/doctrine/reconciliations.yml")
    rows = doctrine.parse_reconciliations(ledger)
    backfill = _backfill_inventory(repo, tree)
    ap.require(not set(backfill["removed_handles"]).intersection(historical),
               "migration-displacement-conflict")
    references = batch["signed_dispositions"]
    # Missing specific human evidence refuses before interpreting signed-shaped rows.
    affected_rows = [row for row in rows if row["handle"] in historical]
    _, members = _member_inventory(repo, tree)
    entries = [entry for entry in batch["entries"] if entry["disposition"] != "architecture-retained"]
    affected_members = [member for member in members if any(
        entry["source_adr"] in member.get("related_adrs", []) if member["kind"] == "invariant" else
        any(doctrine.scope_contains(token, path) for rule in entry["affected_governs"]
            for token in doctrine.scope_path_tokens(rule["scope"], tree.name)
            for path in member["evidence_paths"]) for entry in entries)]
    affected_backfill = [row for row in backfill["signed_receipts"] if row["handle"] in historical]
    ap.require(references or not (affected_rows or affected_members or affected_backfill),
               "migration-human-disposition-pending")
    expected = {}; fingerprints = []
    def remember(entry, subject, replacements=None):
        key = (entry["source_identity"], subject["kind"], subject.get("invariant"), subject["handle"])
        ap.require(key not in expected, "migration-disposition-subject-conflict")
        expected[key] = (entry, subject, replacements)
    if affected_rows or affected_members:
        manifest_text, manifest_rel = _read(repo, tree / "manifest.yml")
        fingerprints.append({"path": manifest_rel, "sha256": _digest(manifest_text.encode())})
        records, invariants = dispositions._raw_records(repo, config)
        retired = {h for r in records if r["active"] and r["status"] == "Accepted" for h in r.get("retires", [])}
        live = [r for r in records if r["active"] and r["handle"] not in retired]
        for entry in entries:
            handles = {r["handle"] for r in entry["affected_governs"]}
            inputs = live + [r for r in records if not r["active"] and r["handle"] in handles]
            seeded = doctrine.invariant_pairings(inputs, invariants) + doctrine.observation_pairings(inputs, config.docs_dir)
            pairs = {(p["invariant"], p["handle"]) for p in seeded if p["handle"] in handles}
            pairs.update((r["invariant"], r["handle"]) for r in affected_rows if r["handle"] in handles)
            for invariant, handle in sorted(pairs):
                args = argparse.Namespace(invariant=invariant, handle=handle)
                subject, replacements, _ = dispositions._doctrine_subject(repo, config, entry, args, ledger)
                remember(entry, subject, replacements)
        covered = {key[2] for key in expected}
        for member in affected_members:
            ap.require(any(identity == member["id"] or identity.startswith(member["id"] + "/")
                           for identity in covered), "migration-member-disposition-validation-pending")
    if backfill["signed_batches"]:
        text, _ = _read(repo, tree / "adrs/summaries/backfill-reviews.yml")
        reviews = summaries.parse_reviews(text)
        def read_current(path, bound=True):
            text, rel = _read(repo, path)
            if bound: fingerprints.append({"path": rel, "sha256": _digest(text.encode())})
            return text
        manifest = _yaml(read_current(tree / "manifest.yml"))
        ap.require(isinstance(manifest, dict), "migration-disposition-backfill-admission-refused")
        # After publication the log and journal months still corroborate the signed batches,
        # but they grow with unrelated work, so only a read before publication binds their digests.
        log = read_current(tree / "log.md", bound=not published)
        months = {b["signed"][:7] for b in reviews["batches"].values() if b["signed"] is not None}
        journals = {month: read_current(tree / "journal" / (month + ".md"), bound=not published)
                    for month in months}
        dispositions.validate_backfill_corroboration(reviews, manifest, log, journals)
        for entry in entries:
            for rule in entry["affected_governs"]:
                if not any(r["handle"] == rule["handle"] for r in affected_backfill): continue
                dispositions._read_backfill_corroboration(repo, config, entry, text)
                subject, _ = dispositions._backfill_subject(entry, rule["handle"], text)
                remember(entry, subject)
    directory = tree / "adrs/migrations/dispositions"
    _checked(repo, directory)
    supplied = {}; referenced = {r["path"]: r for r in references}
    # Scan same-subject collisions, including unreferenced receipts, as the writer does.
    paths = set(directory.glob("*.json")) | {repo / r["path"] for r in references}
    for path in sorted(paths):
        text, rel = _read(repo, path); raw_digest = _digest(text.encode())
        ap.require(path.parent == directory and path.name == raw_digest + ".json",
                   "migration-disposition-immutable-conflict")
        receipt = dispositions.parse_receipt(text); subject = receipt["subject"]
        key = (receipt["source_identity"], subject["kind"], subject.get("invariant"), subject["handle"])
        if rel not in referenced:
            ap.require(receipt["batch_id"] != batch["batch_id"] or key not in expected,
                       "migration-disposition-subject-conflict")
            continue
        ap.require(raw_digest == referenced[rel]["sha256"] and key in expected and key not in supplied,
                   "migration-disposition-reference-refused")
        entry, wanted, replacements = expected[key]
        ap.require(receipt["batch_id"] == batch["batch_id"] and
                   receipt["entry_sha256"] == dispositions.canonical_digest(entry) and
                   receipt["producer"] == dispositions.PRODUCERS[subject["kind"]] and
                   subject == wanted and receipt.get("replacement_pairings") == replacements and
                   receipt["dependency"] in signed,
                   "migration-disposition-subject-refused")
        ledger_rel = (tree / ("adrs/doctrine/reconciliations.yml" if subject["kind"] == "doctrine-pairing"
                              else "adrs/summaries/backfill-reviews.yml")).relative_to(repo).as_posix()
        ap.require(receipt["dependency"]["path"] == ledger_rel, "migration-disposition-dependency-refused")
        supplied[key] = receipt
        fingerprints.append({"path": rel, "sha256": raw_digest})
    ap.require(set(supplied) == set(expected), "migration-human-disposition-pending")
    return fingerprints


def _published_signed_inputs(repo: Path, batch: dict, publication: dict) -> tuple[list[dict], tuple]:
    """Proven first publication binds completeness; current additions bind no authority."""
    with _revision_repository(repo, publication["publication_commit"]) as isolated:
        baseline, _ = _signed_inventory(isolated, _configuration(isolated).docs_root)
    ap.require(baseline == batch["signed_dependencies"], "migration-signed-dependency-drift")
    tree = _configuration(repo).docs_root
    current, _ = _signed_inventory(repo, tree)
    frozen = {item["path"]: item["sha256"] for item in baseline}
    ap.require(len(frozen) == len(baseline), "migration-signed-dependency-drift")
    observed = {item["path"]: item["sha256"] for item in current}
    ap.require(all(observed.get(path) == digest for path, digest in frozen.items()),
               "migration-signed-dependency-drift")
    members = _member_inventory(repo, tree)
    ap.require(all(observed.get(item["path"]) == item["sha256"] for item in members[0]),
               "migration-current-member-changed")
    additions = {item["path"] for item in current if item["path"] not in frozen}
    allowed = {item["path"] for item, member in zip(*members) if member["kind"] == "observation"}
    ap.require(additions <= allowed, "migration-signed-dependency-drift")
    return baseline, (members, additions)


def _addition_inputs(repo: Path, batch: dict, facts: dict, capture: tuple, source_io) -> dict:
    """Reuse raw admission grammar and pure reservations, without recursive proof reads."""
    import summaries_projection as sp
    import observation_admission as admission
    members, additions = capture
    if not additions: return {}
    tree = _configuration(repo).docs_root
    try:
        records = sp.collect_records(tree / "adrs", observations=tree / "observations", _source_io=source_io)
        reviews = sp.read_reviews(tree / "adrs", _source_io=source_io)
        removed = sp.removed_handles(reviews)
        aliases = sp.collect_observation_alias_rows(tree / "observations", records, _source_io=source_io)
        overlay = sp._summary_authority_records(records, facts, removed, aliases)
        spans = [dict(resolve_clause_span(repo, entry), disposition=entry["disposition"])
                 for entry in batch["entries"]]
        inputs = {item["path"]: item["sha256"] for item in members[0]}
        context = dict(root=repo, tree=tree, spans=spans, fingerprints=inputs, source_io=source_io)
        for item, member in zip(*members):
            if item["path"] not in additions: continue
            text = source_io.read_text(repo / item["path"])
            fm = _member_frontmatter(text)
            rows = [r for r in records if r["source_adr"] == member["id"]]
            ap.require(rows, "migration-current-member-admission-refused")
            ap.require(not admission._source_problems(context, fm["evidence"]),
                       "migration-current-member-admission-refused")
            for row in rows:
                ap.require(not admission._slug_problems(overlay, sp.slug_of(row["handle"]),
                    own_handle=row["handle"], removed=removed), "migration-current-member-admission-refused")
        return inputs
    except (sp.GovernsValidationError, admission.AdmissionRefusal):
        raise Refused("migration-current-member-admission-refused") from None


def _validated_inputs(repo: Path, proof, _publication=None) -> dict:
    """Construct private candidate facts only from exact proof and current raw inputs."""
    batch, content = _load_batch(repo, proof.binding["batch"]["path"])
    ap.require(batch["format_version"] == "2", "migration-explicit-activation-format-required")
    ap.require(_digest(content) == proof.binding["batch"]["sha256"], "migration-batch-drift")
    sources = [resolve_clause(repo, entry) for entry in batch["entries"]]
    replacements = [h for e in batch["entries"] for h in e["replacement_handles"]]
    _architectural_refs(repo, batch["authorizing_refs"] + replacements)
    tree = _configuration(repo).docs_root
    signed, _ = _signed_inventory(repo, tree)
    capture = None
    if _publication is None:
        ap.require(signed == batch["signed_dependencies"], "migration-signed-dependency-drift")
    else:
        signed, capture = _published_signed_inputs(repo, batch, _publication)
    def citation_dependencies() -> list[dict]:
        citations, citation_kinds = _citation_inspection(repo, batch)
        ap.require(not citation_kinds["governing"], "migration-citation-repair-required")
        if _publication is None:
            # Before publication the reviewed batch binds each citation file's exact bytes.
            ap.require(citations == batch["citation_dependencies"], "migration-citation-dependency-drift")
            return citations
        # After publication a historical citation file may grow, and a new one may appear,
        # so only the bound paths stay required; no whole-file digest is required or exported.
        # Every citation that reaches here is historical: a governing one refused above.
        ap.require({c["path"] for c in batch["citation_dependencies"]} <= {c["path"] for c in citations},
                   "migration-citation-roster-drift")
        return []
    citations = citation_dependencies()
    records, fingerprints = _raw_architecture(repo)
    historical = {}; retired = {target for rec in records if rec["live_host"] for target in rec["retires"]}
    retained = []; displacements = {}
    for entry, source in zip(batch["entries"], sources):
        for rule in entry["affected_governs"]:
            for target in rule.get("retires", []):
                key = (target, rule["handle"])
                fact = {"target_handle": target, "source_displacer": rule["handle"],
                    "source_identity": entry["source_identity"],
                    "source_ref": {k: source[k] for k in ("path", "source_adr", "clause_sha256")}}
                ap.require(key not in displacements or displacements[key] == fact,
                           "migration-displacement-conflict")
                displacements[key] = fact
            # Preserve all selected retirement edges, including retained mixed clauses.
            retired.update(rule.get("retires", []))
            if entry["disposition"] == "architecture-retained":
                retained.append(rule["handle"]); continue
            handle = rule["handle"]
            ap.require(handle not in historical and handle not in retired and entry["replacement_handles"],
                       "migration-displacement-conflict")
            historical[handle] = {"destination": entry["historical_destination"],
                "source_identity": entry["source_identity"], "replacements": entry["replacement_handles"]}
    ap.require(not set(historical).intersection(retired) and not set(historical).intersection(replacements),
               "migration-displacement-conflict")
    slugs = [h.split("/", 1)[1] for h in historical]
    ap.require(len(set(slugs)) == len(slugs) and not any(rec["live_host"] and
        rec["handle"] not in historical and rec["handle"] not in retired and
        rec["handle"].split("/", 1)[1] in slugs for rec in records), "migration-slug-conflict")
    fingerprints += _disposition_inputs(repo, batch, historical, signed, published=_publication is not None)
    fingerprints += signed + citations + [proof.binding["batch"]]
    facts = {"historical_handles": historical, "retired_handles": sorted(retired),
        "historical_displacements": [displacements[key] for key in sorted(displacements)],
        "reserved_slugs": sorted(slugs), "retained_handles": sorted(retained),
        "source_refs": [{k: v for k, v in source.items() if k != "affected_governs"} for source in sources],
        "dependency_fingerprints": sorted(fingerprints, key=lambda item: item["path"])}
    with ExitStack() as stack:
        source_io = None
        if capture is not None and capture[1]:
            from observation_admission import _AdmissionIO
            source_io = stack.enter_context(closing(_AdmissionIO(repo)))
            source_io.bind_tree(tree)
        current_inputs = _addition_inputs(repo, batch, facts, capture, source_io) if capture is not None else {}
        _unchanged_inputs(repo, fingerprints)
        if capture is not None:
            ap.require(_member_inventory(repo, tree) == capture[0], "migration-current-member-changed")
            ap.require(all(_digest(_read(repo, path)[0].encode()) == digest
                           for path, digest in current_inputs.items()), "migration-current-member-changed")
        if source_io is not None: source_io.revalidate()
        # After publication the citation files are not exported inputs, so re-read them here.
        if _publication is not None: citation_dependencies()
    return facts


def _recovery_sources(*, parent_publication: dict, parent_proof,
                     parent_batch: dict, batch: dict) -> None:
    """Share source-bound linkage without inventing a successful close."""
    recovery = batch.get("recovery_from")
    ap.require(isinstance(recovery, dict) and recovery["witness"] == {
        k: parent_publication[k] for k in ("path", "sha256")} and
        recovery["batch"] == parent_proof.binding["batch"], "migration-recovery-link-refused")
    prior_entries = parent_batch["entries"]; entries = batch["entries"]
    ap.require(recovery["source_identities"] == sorted(e["source_identity"] for e in prior_entries),
               "migration-recovery-source-refused")
    def dispositions(selected):
        return sorted((e["source_identity"], e["disposition"], _canonical_digest(e["historical_destination"]))
                      for e in selected)
    ap.require(dispositions(entries) == dispositions(prior_entries), "migration-recovery-source-refused")


def _validate_recovery_link(repo: Path, *, parent_publication: dict, parent_proof,
                            parent_batch: dict, batch: dict, proof) -> None:
    """Share exact completed-close linkage; a link never supplies current authority."""
    _recovery_sources(parent_publication=parent_publication, parent_proof=parent_proof,
                      parent_batch=parent_batch, batch=batch)
    ap.require(cr.git(repo, "merge-base", "--is-ancestor", parent_publication["publication_commit"],
               proof.proof_commit).returncode == 0, "migration-recovery-order-refused")


def _publication_chain(repo: Path, publications: list[dict]) -> tuple[list[dict], object]:
    """Select one explicitly linked same-source successor; linkage grants no proof."""
    nodes = {}
    for publication in publications:
        proof = _publication_proof(repo, publication)
        batch = load_batch(repo, proof.binding["batch"]["path"])
        identity = (publication["path"], publication["sha256"])
        ap.require(identity not in nodes, "migration-competing-publications-refused")
        nodes[identity] = {"publication": publication, "proof": proof, "batch": batch, "parent": None}
    children = {}
    for identity, node in nodes.items():
        recovery = node["batch"].get("recovery_from")
        if recovery is None: continue
        parent_id = (recovery["witness"]["path"], recovery["witness"]["sha256"])
        ap.require(parent_id in nodes and parent_id != identity and parent_id not in children,
                   "migration-recovery-link-refused")
        parent = nodes[parent_id]
        _validate_recovery_link(repo, parent_publication=parent["publication"], parent_proof=parent["proof"],
            parent_batch=parent["batch"], batch=node["batch"], proof=node["proof"])
        node["parent"] = parent_id; children[parent_id] = identity
    roots = [identity for identity, node in nodes.items() if node["parent"] is None]
    ap.require(len(roots) == 1, "migration-competing-publications-refused")
    current = roots[0]; chain = []; visited = set()
    while current not in visited:
        visited.add(current); node = nodes[current]; chain.append(node["publication"])
        if current not in children: break
        current = children[current]
    ap.require(len(visited) == len(nodes), "migration-recovery-link-refused")
    return chain, nodes[current]["proof"]


def _current_subject_eligibility(repo: Path, run_path, *, slot, batch_path, batch_sha256) -> None:
    """Require exact committed ownership; recovery eligibility cannot activate a proposal."""
    repo = Path(repo).resolve()
    ap.require(_own_git(repo), "migration-git-required")
    ap.full_history(repo)
    first_head = cr.git(repo, "rev-parse", "HEAD").stdout
    configs = {rel: cr.path_state(repo, rel) for rel in _config_inputs()}
    run_path, run_rel = _checked(repo, run_path)
    run, book, owned_run = ap._owner(repo, run_path)
    ap.require(owned_run == run_rel, "run-identity-refused")
    declarations = [d for d in book["implementation_slots"] if d["slot"] == slot]
    ap.require(len(declarations) == 1, "slot-not-declared")
    declaration = declarations[0]
    _, rel = _checked(repo, batch_path)
    ap.require(rel == batch_path and declaration.get("migration_batch") == {
        "role": "migration-batch", "path": rel}, "selected-batch-path-refused")
    batch, raw = _load_batch(repo, rel)
    ap.require(batch["approval_slot"] == slot and _digest(raw) == batch_sha256,
               "selected-batch-identity-refused")
    subject = {"path": rel, "sha256": batch_sha256}
    ap.require(ap.committed_bytes(repo, subject) == raw, "selected-batch-identity-refused")
    head = cr.git(repo, "rev-parse", "HEAD").stdout
    ap.require(head == first_head, "migration-history-head-changed")
    references = batch["authorizing_refs"] + declaration["constraint_refs"] + [
        h for entry in batch["entries"] for h in entry["replacement_handles"]]
    records, source_fingerprints = _raw_architecture(repo)
    fingerprints = [subject] + source_fingerprints
    publications = _discover_publications(repo)
    if batch.get("recovery_from") is None:
        ap.live_constraints(repo, references)
    else:
        chain, _ = _publication_chain(repo, publications)
        owned = {"batch": subject, "slot": slot,
                 **{key: run[key] for key in ("book_id", "run_id", "book_content_hash")}}
        nodes = []; own_members = []
        for index, publication in enumerate(chain):
            proof = _publication_proof(repo, publication)
            prior = load_batch(repo, proof.binding["batch"]["path"])
            nodes.append((publication, proof, prior))
            if all(proof.binding.get(key) == value for key, value in owned.items()) and \
                    _checked(repo, _yaml(publication["content"].decode("utf-8"))["run_path"])[1] == run_rel:
                ap.require(proof.slot == declaration, "migration-recovery-subject-refused")
                own_members.append(index)
        ap.require(len(own_members) <= 1, "migration-recovery-subject-refused")
        parent_index = own_members[0] - 1 if own_members else len(nodes) - 1
        ap.require(parent_index >= 0, "migration-recovery-link-refused")
        publication, proof, parent = nodes[parent_index]
        _recovery_sources(parent_publication=publication, parent_proof=proof,
                          parent_batch=parent, batch=batch)
        ap.require(cr.git(repo, "merge-base", "--is-ancestor", publication["publication_commit"],
                   head.decode().strip()).returncode == 0, "migration-recovery-order-refused")
        denied = {h for record in records if record["live_host"] for h in record["retires"]}
        for locator, historical_proof, historical_batch in nodes:
            fingerprints += [{k: locator[k] for k in ("path", "sha256")}, historical_proof.binding["batch"]]
            for entry in historical_batch["entries"]:
                resolve_clause(repo, entry)
                for rule in entry["affected_governs"]:
                    denied.update(rule.get("retires", []))
                    if entry["disposition"] != "architecture-retained": denied.add(rule["handle"])
        for entry in batch["entries"]: resolve_clause(repo, entry)
        ap.require(not denied.intersection(references), "constraint-not-live-accepted")
        ap._raw_live_constraints(repo, references)
    _unchanged_inputs(repo, fingerprints)
    ap.require(_raw_architecture(repo) == (records, source_fingerprints) and
        _discover_publications(repo) == publications,
        "migration-current-subject-changed")
    ap.require(ap._owner(repo, run_path) == (run, book, run_rel) and
        ap.committed_bytes(repo, subject) == raw and
        {rel: cr.path_state(repo, rel) for rel in _config_inputs()} == configs,
        "migration-current-subject-changed")
    ap.full_history(repo)
    ap.require(cr.git(repo, "rev-parse", "HEAD").stdout == head, "migration-history-head-changed")


def _revision_environment() -> dict[str, str]:
    """Drop every ambient Git runtime setting for the complete temporary read."""
    secrets = cr._secret_names()
    # No child inherits the gateway key or a crux env-file name, as for every council-records read.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_") and key not in secrets}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_NOSYSTEM="1",
        GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=cr.NO_GRAFT_FILE, GIT_LITERAL_PATHSPECS="1",
        GIT_TERMINAL_PROMPT="0")
    return env


def _revision_git(repo: Path, *args: str):
    return subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false",
        "-c", "core.attributesFile=" + os.devnull, "-C", str(repo), *args],
        capture_output=True, env=_revision_environment())


@contextmanager
def _revision_repository(repo: Path, revision: str):
    """Materialize in a fresh repository with no inherited templates or filter drivers."""
    ap.full_history(repo)
    resolved = _revision_git(repo, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}")
    ap.require(resolved.returncode == 0, "migration-query-revision-refused")
    with tempfile.TemporaryDirectory(prefix="crux-migration-read-") as scratch:
        isolated = Path(scratch) / "repository"
        # Local object copies avoid spawning an upload-pack with source-local configuration.
        copied = _revision_git(repo, "clone", "--no-hardlinks", "--no-checkout", "--template=",
                               "--", str(repo), str(isolated))
        ap.require(copied.returncode == 0, "migration-query-history-unavailable")
        checkout = _revision_git(isolated, "checkout", "--detach", resolved.stdout.decode().strip())
        ap.require(checkout.returncode == 0, "migration-query-history-unavailable")
        yield isolated


def _revision_view(repo: Path, revision: str) -> dict:
    with _revision_repository(repo, revision) as isolated:
        return authority_view(isolated)


def _read_revision(repo: Path, revision: str) -> dict:
    # The whole recursive proof read inherits the same isolated environment, including
    # shared Git cleanliness helpers. Execute only the installed trusted source module.
    code = ('import sys,json; sys.path.insert(0,sys.argv[1]); import implementation_migration as m; '
        '\ntry: print(json.dumps({"view":m._revision_view(m.Path(sys.argv[2]),sys.argv[3])}))'
        '\nexcept m.Refused as e: print(json.dumps({"error":e.code}))')
    result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(Path(__file__).resolve().parent),
        str(repo), revision], capture_output=True, env=_revision_environment())
    ap.require(result.returncode == 0, "migration-query-history-unavailable")
    response = json.loads(result.stdout)
    if "error" in response: raise Refused(response["error"])
    return response["view"]


def _own_git(repo: Path) -> bool:
    """A nested ordinary project cannot borrow an ancestor's repository."""
    marker = repo / ".git"
    if not marker.exists() and not marker.is_symlink():
        return False
    ap.require(not marker.is_symlink(), "migration-git-root-refused")
    ap.require(marker.is_dir() or marker.is_file(), "migration-git-root-refused")
    top = _revision_git(repo, "rev-parse", "--show-toplevel")
    ap.require(top.returncode == 0 and Path(top.stdout.decode().strip()).resolve() == repo,
               "migration-git-root-refused")
    return True


def _unborn_head(repo: Path) -> bool:
    """A repository with no commits at all has no history, so it reads like one without Git."""
    if cr.git(repo, "rev-parse", "--verify", "--quiet", "HEAD").returncode == 0:
        return False
    branch = cr.git(repo, "symbolic-ref", "--quiet", "HEAD")
    refs = cr.git(repo, "for-each-ref", "--count=1")
    return branch.returncode == 0 and refs.returncode == 0 and not refs.stdout.strip()


_NO_GIT_WORK_LIMIT = 10000
_NO_GIT_DEPTH_LIMIT = 64


def _no_git_document(text: str, *, _member=False):
    """Validate each distinct YAML node before constructing a shared object graph."""
    class MemberLoader(yaml.SafeLoader):
        """Preserve SafeLoader merge precedence with bounded expanded-pair work."""
        def __init__(self, stream):
            super().__init__(stream)
            self.flattened = set(); self.expanded_pairs = 0

        def flatten_mapping(self, node):
            if id(node) in self.flattened: return
            merged, ordinary = [], []
            def append(target, pairs):
                self.expanded_pairs += len(pairs)
                ap.require(self.expanded_pairs <= _NO_GIT_WORK_LIMIT, "migration-footprint-ambiguous")
                target.extend(pairs)
            for key, child in node.value:
                if key.tag == "tag:yaml.org,2002:merge":
                    if isinstance(child, yaml.MappingNode): sources = [child]
                    else:
                        ap.require(isinstance(child, yaml.SequenceNode) and all(
                            isinstance(source, yaml.MappingNode) for source in child.value),
                            "migration-footprint-ambiguous")
                        sources = reversed(child.value)
                    for source in sources:
                        self.flatten_mapping(source)
                        append(merged, source.value)
                else:
                    if key.tag == "tag:yaml.org,2002:value": key.tag = "tag:yaml.org,2002:str"
                    append(ordinary, [(key, child)])
            node.value = merged + ordinary
            self.flattened.add(id(node))

    loader = MemberLoader(text) if _member else yaml.SafeLoader(text)
    active, heights = set(), {}
    work = 0
    def visit(node, depth):
        nonlocal work
        work += 1
        ap.require(work <= _NO_GIT_WORK_LIMIT and depth < _NO_GIT_DEPTH_LIMIT and
                   id(node) not in active, "migration-footprint-ambiguous")
        if id(node) in heights:
            ap.require(depth + heights[id(node)] < _NO_GIT_DEPTH_LIMIT,
                       "migration-footprint-ambiguous")
            return heights[id(node)]
        active.add(id(node))
        children = []
        if isinstance(node, yaml.MappingNode):
            keys, values = set(), set()
            for key, child in node.value:
                ap.require(isinstance(key, yaml.ScalarNode) and
                           (_member or key.tag != "tag:yaml.org,2002:merge"), "migration-footprint-ambiguous")
                # Spelling and constructed-key equality must both remain unique.
                value = key.value if key.tag == "tag:yaml.org,2002:merge" or (
                    _member and key.tag == "tag:yaml.org,2002:value") else loader.construct_object(key)
                ap.require(key.value not in keys and value not in values,
                           "migration-duplicate-key-refused")
                keys.add(key.value); values.add(value)
                children.extend((key, child))
        elif isinstance(node, yaml.SequenceNode):
            children = node.value
        height = max((visit(child, depth + 1) + 1 for child in children), default=0)
        active.remove(id(node))
        heights[id(node)] = height
        return height
    try:
        node = loader.get_single_node()
        if node is None: return None
        visit(node, 0)
        return loader.construct_document(node)
    except Refused:
        raise
    except (yaml.YAMLError, RecursionError, TypeError, ValueError):
        raise Refused("migration-footprint-ambiguous") from None
    finally:
        loader.dispose()


def _no_git_generated_json(text: str) -> dict:
    """Inspect a leading generated object; plain tail damage supplies no input or authority."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            ap.require(key not in result, "migration-duplicate-key-refused")
            result[key] = value
        return result
    def constant(_):
        raise Refused("migration-footprint-ambiguous")
    try:
        decoder = json.JSONDecoder(object_pairs_hook=unique, parse_constant=constant)
        start = len(text) - len(text.lstrip(" \t\r\n"))
        doc, end = decoder.raw_decode(text, start)
        ap.require(isinstance(doc, dict), "migration-footprint-ambiguous")
        _no_git_declarations(doc)
        fields = {"migration_batch", "migration", "migration_inputs", "migration_inputs_sha256",
            "migration_sha256", "migration_bindings", "historical_slugs", "historical_handles",
            "historical_displacements", "historical_clauses", "publication_history", "migration-batch"}
        for raw in text[end:].split("\n"):
            line = raw.removesuffix("\r")
            if not line.strip(" \t"): continue
            ap.require(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*(?:[ \t]+[A-Za-z0-9_][A-Za-z0-9_.-]*)*[ \t]*",
                                    line) is not None, "migration-footprint-ambiguous")
            ap.require(not any(word in fields or word.startswith("implementation-migration") or
                word.endswith((".json", ".yaml", ".yml")) for word in line.split()),
                "migration-footprint-ambiguous")
        return doc
    except Refused:
        raise
    except (ValueError, RecursionError):
        raise Refused("migration-footprint-ambiguous") from None


def _no_git_layout(repo: Path) -> list[Path]:
    """Use canonical layout validation with a private minimal-config adapter."""
    ap.require(repo.is_dir(), "migration-layout-refused")
    named = set()
    inputs = {}
    for rel in _config_inputs():
        path = repo / rel
        if not path.exists() and not path.is_symlink(): continue
        if "/" in rel and rel.split("/", 1)[0] not in named and path.parent.is_symlink():
            continue  # An unrelated conventional-directory symlink is not a project tree.
        text, _ = _read(repo, rel); inputs[rel] = text
        doc = _no_git_document(text)
        if rel in (bionic_config.BIONIC_CONFIG_FILENAME, bionic_config.CONFIG_FILENAME):
            if doc is None: doc = {}
            ap.require(isinstance(doc, dict), "migration-layout-refused")
            _no_git_declarations(doc)
            other = bionic_config.parse_config_text(_legacy_config_text(text), source=rel)[1]
            if other is not None: named.add(other)
        elif path.name == ".migrating":
            named.add(bionic_config.parse_migration_marker_text(text, marker=path))
    values, source = _layout_values(inputs)
    directory = values[1]
    for name in (bionic_config.DEFAULT_DOCS_DIR, bionic_config.LEGACY_DOCS_DIR):
        if bionic_config.is_crux_manifest_text(inputs.get(name + "/manifest.yml", "")):
            named.add(name)
    automatic = source == "discovery:" + bionic_config.DEFAULT_DOCS_DIR and not any(
        (repo / rel).exists() or (repo / rel).is_symlink()
        for rel in (bionic_config.BIONIC_CONFIG_FILENAME, bionic_config.CONFIG_FILENAME))
    named.add(directory)
    # The finite conventional evidence surfaces survive even when their manifests are lost.
    if automatic and not (repo / bionic_config.DEFAULT_DOCS_DIR).exists():
        for name in (bionic_config.DEFAULT_DOCS_DIR, bionic_config.LEGACY_DOCS_DIR):
            if (repo / name).is_dir() and not (repo / name).is_symlink(): named.add(name)
    trees = []
    from admission_source_io import SourceIO
    with closing(SourceIO(repo)) as source_io:
        for name in sorted(named):
            tree = _guarded_layout_tree(repo, name, source_io)
            kind = source_io.kind(tree)
            if automatic and name == bionic_config.DEFAULT_DOCS_DIR and kind is None: continue
            ap.require(kind == "directory", "migration-layout-refused")
            trees.append(tree)
        source_io.revalidate()
    return trees


def _no_git_declarations(value) -> None:
    """Inspect distinct containers once; alias reuse cannot hide migration fields."""
    active, heights = set(), {}
    work = 0
    def visit(value, depth):
        nonlocal work
        work += 1
        ap.require(work <= _NO_GIT_WORK_LIMIT and depth < _NO_GIT_DEPTH_LIMIT,
                   "migration-footprint-ambiguous")
        if not isinstance(value, (dict, list)): return 0
        ap.require(id(value) not in active, "migration-footprint-ambiguous")
        if id(value) in heights:
            ap.require(depth + heights[id(value)] < _NO_GIT_DEPTH_LIMIT,
                       "migration-footprint-ambiguous")
            return heights[id(value)]
        active.add(id(value))
        height = 0
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"migration_batch", "migration", "migration_inputs", "migration_inputs_sha256",
                           "migration_sha256"}:
                    raise Refused("migration-git-required")
                if key in {"migration_bindings", "historical_slugs", "historical_handles",
                           "historical_displacements", "historical_clauses", "publication_history"}:
                    ap.require(isinstance(child, (list, dict)) and not child, "migration-git-required")
                if key == "role":
                    ap.require(child != "migration-batch", "migration-git-required")
                if key == "record_type":
                    ap.require(not isinstance(child, str) or not child.startswith("implementation-migration"),
                               "migration-git-required")
                if key == "input_domain":
                    ap.require(isinstance(child, list) and all(isinstance(x, str) for x in child),
                               "migration-footprint-ambiguous")
                    ap.require(not {"migration", "implementation-migration"}.intersection(child),
                               "migration-git-required")
                height = max(height, visit(child, depth + 1) + 1)
        else:
            for child in value: height = max(height, visit(child, depth + 1) + 1)
        active.remove(id(value))
        heights[id(value)] = height
        return height
    visit(value, 0)


def _no_git_surfaces(repo: Path, folder: Path, source_io, *, nested=False) -> list[Path]:
    """Inventory only eligible project evidence surfaces, never repositories inside holdings."""
    _source_path(folder, source_io)
    kind = source_io.kind(folder)
    if kind is None: return []
    ap.require(kind == "directory", "migration-footprint-ambiguous")
    files = []
    for path in source_io.glob(folder, "*"):
        if not nested and path.suffix not in {".yaml", ".yml"}:
            continue
        _source_path(path, source_io)
        if source_io.kind(path) == "directory":
            ap.require(nested, "migration-footprint-ambiguous")
            if nested: files.extend(_no_git_surfaces(repo, path, source_io, nested=True))
        else:
            files.append(path)
    return files


def _original_tree_paths(repo: Path, tree: Path, source_io) -> list[Path]:
    """Canonical current footprint roster shared by original-only admissions."""
    _source_path(tree, source_io)
    ap.require(source_io.kind(tree) == "directory", "migration-layout-refused")
    ap.require(not _no_git_surfaces(repo, tree / "adrs/migrations", source_io, nested=True),
               "migration-git-required")
    paths = [tree / "manifest.yml"]
    for name in ("active", "archive"):
        paths += _no_git_surfaces(repo, tree / "promptbooks" / name, source_io)
    runs = tree / "promptbooks/runs"
    paths += _no_git_surfaces(repo, runs, source_io)
    if source_io.kind(runs) is not None:
        for folder in source_io.glob(runs, "*"):
            if re.match(r"^(?:[A-Z][A-Z0-9]{1,9}-)?PB-[0-9]{4}(?:-|$)", folder.name):
                paths += [p for p in _no_git_surfaces(repo, folder, source_io)
                          if p.name.startswith("run-") and p.suffix in {".yaml", ".yml"}]
    for projection in ("summaries", "doctrine"):
        paths += [tree / "adrs" / projection / name for name in
                  (("_meta.json", "resolver.json", "rule-table.md") if projection == "summaries"
                   else ("_meta.json", "index.md"))]
    return paths


def _original_document(path: Path, text: str) -> None:
    if path.suffix == ".md":
        ap.require(not re.search(r"^## Historical implementation (?:handles|clauses)\s*$", text, re.M),
                   "migration-git-required")
        return
    doc = _no_git_generated_json(text) if path.suffix == ".json" else _no_git_document(text)
    ap.require(isinstance(doc, dict), "migration-footprint-ambiguous")
    _no_git_declarations(doc)


def _original_tree(repo: Path, tree: Path) -> None:
    from admission_source_io import SourceIO
    with closing(SourceIO(repo)) as source_io:
        for path in _original_tree_paths(repo, tree, source_io):
            metadata = _source_path(path, source_io)
            if metadata is None: continue
            limit = (_reference_reader().MAX_FILE_BYTES if path == tree / "adrs/summaries/resolver.json"
                     else MAX_SOURCE_BYTES)
            text, _ = (_bounded_read(repo, path, limit) if limit != MAX_SOURCE_BYTES else _read(repo, path))
            ap.require(source_io.read_bytes(path, max_bytes=limit).decode("utf-8") == text,
                       "migration-source-changed")
            _original_document(path, text)
        source_io.revalidate()


def _no_git_original(repo: Path) -> dict:
    """Admission proves only absence of surviving eligible evidence, not lost Git history."""
    trees = _no_git_layout(repo)
    for tree in trees: _original_tree(repo, tree)
    ap.require(_no_git_layout(repo) == trees, "migration-layout-changed")
    return _original_view()


def _alternate_surface(rel: str) -> bool:
    """Only the selected tree's established evidence roster qualifies."""
    parts = Path(rel).parts
    if parts == ("manifest.yml",): return True
    if parts[:2] == ("adrs", "migrations"): return True
    if rel in {"adrs/summaries/" + name for name in ("_meta.json", "resolver.json", "rule-table.md")} | {
            "adrs/doctrine/_meta.json", "adrs/doctrine/index.md"}: return True
    if len(parts) == 3 and parts[:2] in {("promptbooks", "active"), ("promptbooks", "archive"),
                                       ("promptbooks", "runs")}:
        return Path(rel).suffix in {".yaml", ".yml"}
    return (len(parts) == 4 and parts[:2] == ("promptbooks", "runs") and
            re.match(r"^(?:[A-Z][A-Z0-9]{1,9}-)?PB-[0-9]{4}(?:-|$)", parts[2]) is not None and
            parts[3].startswith("run-") and Path(rel).suffix in {".yaml", ".yml"})


def _alternate_history(repo: Path, tree: Path) -> None:
    history = _InvocationHistory(repo)
    prefix = tree.relative_to(repo).as_posix() + "/"
    names = cr.git(repo, "log", "--full-history", "--format=", "--name-only", "--no-renames", "-z",
                   history.head, "--", prefix)
    ap.require(names.returncode == 0, "history-unavailable")
    paths = set()
    for raw in names.stdout.split(b"\0"):
        rel = raw.decode("utf-8").strip("\n")
        if not rel: continue
        ap.require(rel.startswith(prefix), "migration-path-refused")
        if _alternate_surface(rel[len(prefix):]):
            _checked(repo, rel)
            ap.require(not rel[len(prefix):].startswith("adrs/migrations/"),
                       "migration-alternate-tree-unsupported")
            paths.add(rel)
    if paths:
        rows = history.metadata(tuple(sorted(paths)), max_bytes=_reference_reader().MAX_FILE_BYTES)
        seen = set()
        for entries in rows.values():
            for rel, (_, oid) in entries.items():
                if (rel, oid) in seen: continue
                seen.add((rel, oid))
                content = history.objects[oid]
                limit = (_reference_reader().MAX_FILE_BYTES if rel == prefix + "adrs/summaries/resolver.json"
                         else MAX_SOURCE_BYTES)
                ap.require(len(content) <= limit, "migration-source-oversize")
                _original_document(Path(rel), content.decode("utf-8"))
    history.unchanged_head()


def admission_authority_view(repo_root, *, docs_dir=None) -> dict:
    """A selector supplies a location, never authority for a distinct tree."""
    if docs_dir is None: return authority_view(repo_root)
    from admission_source_io import SourceIO, SourceIORefusal
    repo = Path(repo_root).resolve()
    try:
        with closing(SourceIO(repo)) as source_io:
            view = _selected_admission_authority_view(repo, docs_dir, source_io)
            source_io.revalidate()
            return view
    except SourceIORefusal:
        raise Refused("migration-authority-input-invalid") from None


def _guarded_layout_tree(repo, docs_dir, source_io):
    from observation_admission import _selected_tree
    tree = repo / docs_dir
    ap.require(_selected_tree(repo, docs_dir, source_io) == tree, "migration-symlink-refused")
    return tree


def _decision_index_applicability(repo_root, docs_dir) -> str:
    """Permit only an empty architecture result, never an original authority view.

    Any concern container, including an empty one, conservatively requires the
    strict reader. HEAD and index facts cover worktree deletions in this finite
    layout roster; this is not a claim about absent historical publications.
    """
    from admission_source_io import SourceIO, SourceIORefusal
    repo = Path(repo_root).resolve()
    try:
        with closing(SourceIO(repo)) as source_io:
            config = bionic_config.load_config(repo, require_tree=False, _source_io=source_io)
            inputs, named = {}, {docs_dir, bionic_config.DEFAULT_DOCS_DIR, bionic_config.LEGACY_DOCS_DIR}
            named.add(config.docs_dir)
            for rel in _config_inputs():
                path = repo / rel
                if _source_path(path, source_io) is None: continue
                text = source_io.read_text(path)
                inputs[rel] = text
                doc = _no_git_document(text)
                if path.name == "manifest.yml":
                    ap.require(isinstance(doc, dict), "migration-layout-refused")
                try:
                    _no_git_declarations(doc)
                except Refused as error:
                    if error.code != "migration-git-required": raise
                    source_io.revalidate()
                    return "REQUIRE_AUTHORITY"
                if path.name == ".migrating":
                    named.add(bionic_config.parse_migration_marker_text(text, marker=path))
            trees = [_guarded_layout_tree(repo, name, source_io) for name in sorted(named)]
            concerns = ("adrs", "promptbooks", "observations")
            for tree in trees:
                _source_path(tree, source_io)
                ap.require(source_io.kind(tree) in (None, "directory"), "migration-layout-refused")
                for concern in concerns:
                    if _source_path(tree / concern, source_io) is not None:
                        source_io.revalidate()
                        return "REQUIRE_AUTHORITY"
                manifest = tree / "manifest.yml"
                if manifest.relative_to(repo).as_posix() not in inputs and _source_path(manifest, source_io) is not None:
                    try:
                        _original_document(manifest, source_io.read_text(manifest))
                    except Refused as error:
                        if error.code != "migration-git-required": raise
                        source_io.revalidate()
                        return "REQUIRE_AUTHORITY"

            _source_path(repo / ".git", source_io)
            if _own_git(repo):
                def metadata_git(*args):
                    result = _revision_git(repo, "--no-lazy-fetch", *args)
                    ap.require(result.returncode != 129, "migration-git-capability-refused")
                    # The canonical process wrapper captures before this check.
                    # This refusal does not claim a streaming allocation bound.
                    ap.require(len(result.stdout) <= MAX_SOURCE_BYTES, "migration-source-oversize")
                    return result
                head = metadata_git("rev-parse", "HEAD")
                if head.returncode != 0:
                    source_io.revalidate()
                    return "REQUIRE_AUTHORITY"
                footprint = [str(tree.relative_to(repo) / concern) for tree in trees for concern in concerns]
                ancestors = sorted({Path(*tree.relative_to(repo).parts[:length]).as_posix()
                    for tree in trees for length in range(1, len(tree.relative_to(repo).parts) + 1)})
                layouts = tuple(dict.fromkeys((*_config_inputs(),
                    *(str(tree.relative_to(repo) / "manifest.yml") for tree in trees))))
                def facts():
                    nodes = b""
                    for rel in sorted(set(ancestors + footprint)):
                        listing = metadata_git("ls-tree", "-z", head.stdout.decode().strip(), "--", rel)
                        ap.require(listing.returncode == 0, "migration-footprint-ambiguous")
                        nodes += listing.stdout
                        ap.require(len(nodes) <= MAX_SOURCE_BYTES, "migration-source-oversize")
                        for raw in listing.stdout.split(b"\0"):
                            if not raw: continue
                            metadata, name = raw.split(b"\t", 1)
                            mode, kind, oid = metadata.split()
                            ap.require(name.decode("utf-8") == rel and re.fullmatch(rb"[0-9a-f]{40,64}", oid),
                                       "migration-footprint-ambiguous")
                            if rel in footprint or mode != b"040000" or kind != b"tree":
                                return nodes, None, None, None, True
                    # Exact queries avoid enumerating ancestor directories.
                    # An index OID does not establish the entry's mode; every
                    # exact indexed ancestor conservatively requires authority.
                    for rel in ancestors:
                        for stage in range(4):
                            entry = metadata_git("rev-parse", "--verify", "--end-of-options", f":{stage}:{rel}")
                            ap.require(entry.returncode in (0, 128), "migration-footprint-ambiguous")
                            if entry.returncode == 0:
                                return nodes, None, None, None, True
                    indexed = metadata_git("ls-files", "--format=x", "-z", "--", *footprint)
                    configured = metadata_git("ls-tree", "-z", head.stdout.decode().strip(), "--", *layouts)
                    staged = metadata_git("ls-files", "--stage", "-z", "--", *layouts)
                    ap.require(indexed.returncode == configured.returncode == staged.returncode == 0,
                               "migration-footprint-ambiguous")
                    return nodes, indexed.stdout, configured.stdout, staged.stdout, ()
                before = facts()
                if before[1] is None or before[1]:
                    source_io.revalidate()
                    return "REQUIRE_AUTHORITY"
                head_layout, index_layout = {}, {}
                for raw in before[2].split(b"\0"):
                    if not raw: continue
                    metadata, rel = raw.split(b"\t", 1)
                    mode, kind, oid = metadata.split()
                    name = rel.decode("utf-8")
                    ap.require(name in layouts and name not in head_layout and
                               re.fullmatch(rb"[0-9a-f]{40,64}", oid), "migration-footprint-ambiguous")
                    if mode not in (b"100644", b"100755") or kind != b"blob":
                        source_io.revalidate()
                        return "REQUIRE_AUTHORITY"
                    head_layout[name] = (mode, oid)
                for raw in before[3].split(b"\0"):
                    if not raw: continue
                    metadata, rel = raw.split(b"\t", 1)
                    mode, oid, stage = metadata.split()
                    name = rel.decode("utf-8")
                    ap.require(name in layouts and re.fullmatch(rb"[0-9a-f]{40,64}", oid),
                               "migration-footprint-ambiguous")
                    if mode not in (b"100644", b"100755") or stage != b"0" or name in index_layout:
                        source_io.revalidate()
                        return "REQUIRE_AUTHORITY"
                    index_layout[name] = (mode, oid)
                current = {rel: source_io.read_bytes(repo / rel) for rel in layouts
                           if _source_path(repo / rel, source_io) is not None}
                if head_layout != index_layout or set(current) != set(head_layout):
                    source_io.revalidate()
                    return "REQUIRE_AUTHORITY"
                for rel, (_, oid) in head_layout.items():
                    # Raw object reads match the bounded history reader. No Git
                    # worktree comparison may invoke attribute conversion.
                    object_kind = metadata_git("cat-file", "-t", oid.decode())
                    object_size = metadata_git("cat-file", "-s", oid.decode())
                    ap.require(object_kind.returncode == object_size.returncode == 0 and
                               object_kind.stdout.strip() == b"blob" and
                               re.fullmatch(rb"[0-9]+\n?", object_size.stdout), "migration-footprint-ambiguous")
                    size = int(object_size.stdout)
                    ap.require(size <= MAX_SOURCE_BYTES, "migration-source-oversize")
                    content = metadata_git("cat-file", "blob", oid.decode())
                    ap.require(content.returncode == 0 and len(content.stdout) == size,
                               "migration-footprint-ambiguous")
                    if content.stdout != current[rel]:
                        source_io.revalidate()
                        return "REQUIRE_AUTHORITY"
                source_io.revalidate()
                final_head = metadata_git("rev-parse", "HEAD")
                ap.require(final_head.returncode == 0 and final_head.stdout == head.stdout,
                           "migration-history-head-changed")
                ap.require(facts() == before, "migration-footprint-ambiguous")
            source_io.revalidate()
            return "ABSENT"
    except Refused:
        raise
    except (SourceIORefusal, cr.RecordError, bionic_config.BionicConfigError, OSError,
            UnicodeError, ValueError, TypeError, yaml.YAMLError):
        raise Refused("migration-authority-input-invalid") from None


def _selected_admission_authority_view(repo, docs_dir, source_io) -> dict:
    from admission_source_io import SourceIORefusal
    distinct = False
    try:
        tree = _guarded_layout_tree(repo, docs_dir, source_io)
        ap.require(source_io.kind(tree) == "directory", "migration-layout-refused")
        identity = source_io.metadata(tree)
        tree_identity = tuple(identity[key] for key in ("st_dev", "st_ino", "st_mode"))
        own_git = _own_git(repo) and not _unborn_head(repo)
        head = cr.git(repo, "rev-parse", "HEAD") if own_git else None
        ap.require(head is None or head.returncode == 0, "history-unavailable")
        def config_inputs():
            return {rel: _read(repo, rel)[0] for rel in _config_inputs()
                    if (repo / rel).exists() or (repo / rel).is_symlink()}
        inputs = config_inputs()
        def revalidate():
            try:
                current = source_io.metadata(tree)
                kind = source_io.kind(tree)
            except SourceIORefusal:
                raise Refused("migration-selected-tree-changed") from None
            ap.require(kind == "directory" and current is not None and
                       tuple(current[key] for key in ("st_dev", "st_ino", "st_mode")) == tree_identity,
                       "migration-selected-tree-changed")
            ap.require((_own_git(repo) and not _unborn_head(repo)) == own_git, "migration-git-root-changed")
            if own_git:
                final = cr.git(repo, "rev-parse", "HEAD")
                ap.require(final.returncode == 0 and final.stdout == head.stdout,
                           "migration-history-head-changed")
            ap.require(config_inputs() == inputs, "migration-config-changed")
            source_io.revalidate()
        configured = (repo / _discovery_directory(repo, inputs)).parent.parent
        revalidate()
        if tree == configured:
            view = authority_view(repo)
            revalidate()
            return view
        distinct = True
        if not own_git:
            _no_git_original(repo)
            _original_tree(repo, tree)
        else:
            # Discovery includes prior configured trees and deleted publications.
            ap.require(not _discover_publications(repo), "migration-alternate-tree-unsupported")
            _original_tree(repo, tree)
            _alternate_history(repo, tree)
        revalidate()
        return _original_view()
    except Refused as error:
        if distinct and (error.code == "migration-git-required" or
                         error.code.startswith("migration-publication-")):
            raise Refused("migration-alternate-tree-unsupported") from None
        raise
    except (cr.RecordError, bionic_config.BionicConfigError, OSError, ValueError, TypeError,
            KeyError, AttributeError, UnicodeError, yaml.YAMLError):
        raise Refused("migration-authority-input-invalid") from None


def _original_view() -> dict:
    return {"state": "original", "publications": [], "historical_handles": {},
            "historical_displacements": [], "retired_handles": [], "reserved_slugs": [],
            "retained_handles": [], "source_refs": [], "dependency_fingerprints": []}


_VIEW_MEMO = contextvars.ContextVar("crux_authority_view_memo", default=None)


@contextmanager
def read_scope():
    """One reader's read phase: repeated authority views of one state share one proof.

    Open it only around reads that write nothing to the tree. Inside it, the owner-approved
    git read cache answers repeated admitted git reads. A repeated `authority_view` of the
    same checkout is answered from memory only while the repository state the git cache
    keys on, and the environment, are byte-identical. Each answer from memory first
    re-checks every bound input fingerprint and the committed publication roster. A
    nested scope shares the outer one.
    """
    import git_read_cache
    if _VIEW_MEMO.get() is not None:
        yield
        return
    with git_read_cache.scope():
        token = _VIEW_MEMO.set({})
        try:
            yield
        finally:
            _VIEW_MEMO.reset(token)


def _view_key(repo: Path):
    import git_read_cache
    state = git_read_cache._repository_state(str(repo))
    return None if state is None else (str(repo), state, tuple(sorted(os.environ.items())))


def _publication_rows(publications) -> list:
    return sorted((p["path"], p["sha256"], p["publication_commit"]) for p in publications)


def authority_view(repo_root, *, revision=None) -> dict:
    """Read this checkout, or an explicitly named isolated historical revision."""
    import git_read_cache
    repo = Path(repo_root).resolve()
    with git_read_cache.scope():
        return _authority_view(repo, revision)


def _authority_view(repo: Path, revision) -> dict:
    try:
        own_git = _own_git(repo)
        if revision is not None:
            ap.require(isinstance(revision, str) and revision and not revision.startswith("-"),
                       "migration-query-revision-refused")
            ap.require(own_git, "migration-git-required")
            return _read_revision(repo, revision)
        if not own_git or _unborn_head(repo):
            return _no_git_original(repo)
        memo = _VIEW_MEMO.get()
        key = _view_key(repo) if memo is not None else None
        if key is not None and key in memo:
            remembered = memo[key]
            _unchanged_inputs(repo, remembered["dependency_fingerprints"])
            ap.require(_publication_rows(_discover_publications(repo)) ==
                       _publication_rows(remembered["publications"]), "migration-current-subject-changed")
            ap.require(_view_key(repo) == key, "migration-history-head-changed")
            return copy.deepcopy(remembered)
        view = _computed_authority_view(repo)
        if key is not None and _view_key(repo) == key:
            memo[key] = copy.deepcopy(view)
        return view
    except Refused:
        raise
    except (cr.RecordError, bionic_config.BionicConfigError, OSError, ValueError, TypeError,
            KeyError, AttributeError, UnicodeError, yaml.YAMLError):
        raise Refused("migration-authority-input-invalid") from None


def _computed_authority_view(repo: Path) -> dict:
    head = cr.git(repo, "rev-parse", "HEAD")
    ap.require(head.returncode == 0, "history-unavailable")
    publications = _discover_publications(repo)
    view = _original_view()
    if publications:
        chain, proof = _publication_chain(repo, publications)
        candidate = _validated_inputs(repo, proof, chain[-1])
        # Only this proved public read supplies committed publication provenance.
        publication_ref = {k: chain[-1][k] for k in ("path", "sha256", "publication_commit")}
        candidate["historical_displacements"] = [dict(fact, publication_ref=publication_ref)
            for fact in candidate["historical_displacements"]]
        view.update(candidate)
        view["publications"] = [{k: v for k, v in publication.items() if k != "content"} for publication in chain]
        view["state"] = "published"
    final_head = cr.git(repo, "rev-parse", "HEAD")
    ap.require(final_head.returncode == 0 and final_head.stdout == head.stdout,
               "migration-history-head-changed")
    return view
