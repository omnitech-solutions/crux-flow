"""Implementation reasoning, delivery evidence and source observations stay separate.

Schemas define closed record shapes. Reviewed bytes never receive editorial edits;
annotations are separate evidence. Only the shared historical verifier supplies
approval. Results and queries create no governing authority or authorization.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import yaml

import bionic_config
import council_gate as cg
import council_history_v4 as v4
import council_records as cr
import implementation_approval as ap

Refused = ap.Refused
ABSENT = "absent"
SCHEMAS = Path(__file__).resolve().parent.parent / "schemas"
KINDS = {"implementation-decision", "implementation-result", "implementation-annotation"}


def schema_errors(doc: dict) -> list:
    kind = doc.get("record_type") if isinstance(doc, dict) else None
    if kind not in KINDS:
        return [{"error": "record-type-refused"}]
    vp = cr._validator()
    schema = vp.load_schema(SCHEMAS / (kind + ".schema.json"))
    errors = []
    vp.validate(doc, schema, "#", "#", errors, kind)
    return errors


def load(path: Path) -> dict:
    ap.require(not path.is_symlink(), "record-symlink-refused")
    try:
        doc = yaml.safe_load(path.read_bytes())
    except (OSError, ValueError, yaml.YAMLError):
        raise Refused("record-unreadable") from None
    ap.require(not schema_errors(doc), "record-schema-refused")
    return doc


def _owner_run(repo: Path, path: Path) -> dict:
    absolute, _ = ap.checked_path(repo, str(path))
    ap.require(not absolute.is_symlink(), "record-symlink-refused")
    ap.require(absolute.is_file(), "owning-run-missing")
    try:
        run = yaml.safe_load(absolute.read_bytes())
    except (OSError, ValueError, yaml.YAMLError):
        raise Refused("owner-run-unreadable") from None
    ap.require(isinstance(run, dict), "owner-run-shape-refused")
    return run


def _decision(repo: Path, path: Path) -> tuple[dict, Path, str]:
    absolute, rel = ap.checked_path(repo, str(path))
    doc = load(absolute)
    ap.require(doc["record_type"] == "implementation-decision", "decision-type-refused")
    run_path = _identity_path(repo, absolute, doc)
    for entry in doc["scope"]:
        ap.checked_path(repo, entry)
    for label in doc["source_labels"]:
        ap.checked_path(repo, label["path"])
    return doc, run_path, rel


def _identity_path(repo: Path, absolute: Path, doc: dict) -> Path:
    parents = absolute.parents
    ap.require(len(parents) >= 7, "decision-layout-refused")
    ap.require(absolute.name == f"revision-{doc['revision']:03d}.yaml" and
               absolute.parent.name == doc["slug"] and parents[1].name == doc["run_id"] and
               parents[2].name == "implementations" and parents[4].name == "runs" and
               parents[5].name == "promptbooks", "decision-layout-refused")
    tree = bionic_config.load_config(repo).docs_root
    ap.require(parents[6] == tree, "decision-tree-refused")
    run_path = parents[3] / ("run-" + doc["run_id"] + ".yaml")
    run = _owner_run(repo, run_path)
    ap.require(run.get("book_id") == doc["book_id"] and run.get("run_id") == doc["run_id"],
               "decision-identity-refused")
    return run_path


def validate_decision(repo_root, path) -> dict:
    repo = Path(repo_root).resolve()
    doc, run_path, rel = _decision(repo, Path(path))
    ap.live_constraints(repo, doc["constraint_refs"])
    return {"valid": True, "identity": [doc[k] for k in ("book_id", "run_id", "slug", "revision")],
            "path": rel, "sha256": cr.sha256_file(repo / rel), "authority": "none"}


def unchecked_rules_text(repo_root, decision_path) -> str:
    """The subject the council reads beside a declared-empty decision: each live Accepted rule whose
    scope names no path, with its handle, rule text and scope. The overlap check cannot test such a
    rule against the declared scope, so the council's review of the stated reason covers it."""
    repo = Path(repo_root).resolve()
    decision, _, _ = _decision(repo, Path(decision_path))
    rows = ap.unchecked_rules(repo, decision)
    lines = ["# Unchecked rules for a declared-empty constraint set", "",
             "Scope under review: " + ", ".join(decision["scope"]), "",
             "Each live Accepted rule below names no path in its scope. The path-overlap check could not "
             "test it against the declared scope. Judge whether it governs that scope.", ""]
    if not rows:
        lines.append("No live Accepted rule lacks a path scope.")
    for row in rows:
        lines += ["## " + row["handle"], "", "Scope: " + (row["scope"] or "(none)"), "",
                  "Rule: " + row["rule"], ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def write_unchecked_rules(repo_root, decision_path, output) -> Path:
    repo = Path(repo_root).resolve()
    text = unchecked_rules_text(repo, decision_path)
    absolute, _ = ap.checked_path(repo, str(output))
    ap.require(not absolute.is_symlink(), "record-symlink-refused")
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_bytes(text.encode("utf-8"))
    return absolute


def _write_new(path: Path, doc: dict) -> Path:
    ap.require(not schema_errors(doc), "record-schema-refused")
    path.parent.mkdir(parents=True, exist_ok=True)
    owned = False
    try:
        with path.open("xb") as stream:
            owned = True
            stream.write(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True).encode())
    except FileExistsError:
        raise Refused("immutable-artifact-exists") from None
    except BaseException:
        if owned:
            path.unlink(missing_ok=True)
        raise
    return path


def write_decision(repo_root, path, doc: dict) -> Path:
    """Create a revision, or update only an unreviewed draft at its declared path."""
    repo = Path(repo_root).resolve()
    absolute, rel = ap.checked_path(repo, str(path))
    ap.require(not schema_errors(doc), "record-schema-refused")
    _identity_path(repo, absolute, doc)
    if absolute.exists():
        previous, run_path, _ = _decision(repo, absolute)
        run = _owner_run(repo, run_path)
        referenced = any(b.get("revision", {}).get("path") == rel for b in run.get("implementation_bindings", []))
        records = cr.discover_records(run_path.parent)
        reviewed = any(s.get("path") == rel for r in records if r.record_type == "council-record"
                       for s in r.doc["subjects"])
        ap.require(not referenced and not reviewed, "reviewed-revision-immutable")
        ap.require(all(previous[k] == doc[k] for k in ("book_id", "run_id", "slug", "revision", "slot")),
                   "decision-identity-refused")
        # Draft replacement is atomic. Reviewed revisions never reach this path.
        temporary = absolute.with_name(absolute.name + ".draft-tmp")
        owned = False
        try:
            _write_new(temporary, doc)
            owned = True
            os.replace(temporary, absolute)
        finally:
            if owned:
                temporary.unlink(missing_ok=True)
        return absolute
    # Validate declared path/owner before publishing even a new draft.
    expected = f"/implementations/{doc['run_id']}/{doc['slug']}/revision-{doc['revision']:03d}.yaml"
    ap.require(rel.endswith(expected), "decision-layout-refused")
    owner = absolute.parents[3] / ("run-" + doc["run_id"] + ".yaml")
    run = _owner_run(repo, owner)
    ap.require(run.get("book_id") == doc["book_id"] and run.get("run_id") == doc["run_id"],
               "decision-identity-refused")
    return _write_new(absolute, doc)


def validate_annotation(repo: Path, decision_path: Path, annotation: dict) -> None:
    ap.require(not schema_errors(annotation), "annotation-schema-refused")
    doc, _, rel = _decision(repo, decision_path)
    digest = cr.sha256_file(repo / rel)
    ap.require(annotation["decision"] == {"path": rel, "sha256": digest}, "annotation-decision-mismatch")
    target = annotation["target"]
    if target == "display_title":
        original = doc["display_title"]
    else:
        match = re.fullmatch(r"source_labels\.([0-9]+)\.label", target)
        ap.require(match is not None and int(match[1]) < len(doc["source_labels"]), "annotation-target-refused")
        original = doc["source_labels"][int(match[1])]["label"]
    ap.require(annotation["original_sha256"] == cr.sha256_bytes(original.encode()), "annotation-original-mismatch")


def write_annotation(repo_root, decision_path, doc: dict, output: Path) -> Path:
    repo = Path(repo_root).resolve()
    decision, run_path, rel = _decision(repo, Path(decision_path))
    ap.validate_implementation_binding(repo, run_path, slot=decision["slot"], revision_path=rel,
                                       revision_sha256=cr.sha256_file(repo / rel), historical_revision=True)
    validate_annotation(repo, repo / rel, doc)
    path, _ = ap.checked_path(repo, str(output))
    ap.require(path.parent == (repo / rel).parent and re.fullmatch(r"annotation-[0-9]{3}\.yaml", path.name),
               "annotation-layout-refused")
    # A second correction cannot silently reuse a previously corrected original label.
    for _, prior_doc in _revision_artifacts(repo, repo / rel, "annotation"):
        ap.require(prior_doc["target"] != doc["target"], "annotation-correction-reused")
    return _write_new(path, doc)


def source_hash(repo: Path, revision: str, path: str) -> str:
    ap.checked_path(repo, path)
    # A missing blob is absence only when the committed tree proves no entry.
    listing = cr.git(repo, "ls-tree", "-z", revision, "--", path)
    ap.require(listing.returncode == 0, "source-unreadable")
    if not listing.stdout:
        return ABSENT
    rows = [row.split(b"\t", 1) for row in listing.stdout.split(b"\0") if row]
    ap.require(len(rows) == 1 and os.fsdecode(rows[0][1]) == path and
               rows[0][0].split()[0] in (b"100644", b"100755"), "source-not-regular")
    blob = cr.git(repo, "cat-file", "blob", revision + ":" + path)
    ap.require(blob.returncode == 0, "source-unreadable")
    return cr.sha256_bytes(blob.stdout)


def commit_id(repo: Path, revision: str) -> str:
    ap.require(isinstance(revision, str) and revision and not revision.startswith("-"), "revision-refused")
    value = cr.git(repo, "rev-parse", "--verify", revision + "^{commit}")
    ap.require(value.returncode == 0, "revision-unavailable")
    return value.stdout.decode().strip()


def _revision_artifacts(repo: Path, path: Path, kind: str) -> list[tuple[Path, dict]]:
    """Select one immutable revision; validate other references before excluding them.

    A matching path with a wrong digest is corrupt evidence, never another revision.
    Other revisions in the same slug remain readable without affecting this choice.
    """
    decision, _, rel = _decision(repo, path)
    digest = cr.sha256_file(path)
    selected = []
    for candidate in sorted(path.parent.glob(kind + "-*.yaml")):
        doc = load(candidate)
        ap.require(doc["record_type"] == "implementation-" + kind, "artifact-type-refused")
        target, target_rel = ap.checked_path(repo, doc["decision"]["path"])
        ap.require(target.parent == path.parent, kind + "-decision-mismatch")
        other, _, _ = _decision(repo, target)
        ap.require(all(other[k] == decision[k] for k in ("book_id", "run_id", "slug")) and
                   doc["decision"]["sha256"] == cr.sha256_file(target), kind + "-decision-mismatch")
        if kind == "annotation":
            validate_annotation(repo, target, doc)
        if target_rel == rel:
            ap.require(doc["decision"]["sha256"] == digest, kind + "-decision-mismatch")
            selected.append((candidate, doc))
    return selected


def _annotation_inventory(repo: Path, path: Path) -> list[dict]:
    return [{"path": p.relative_to(repo).as_posix(), "sha256": cr.sha256_file(p)}
            for p, _ in _revision_artifacts(repo, path, "annotation")]


def _review_coverage(repo: Path, result: dict) -> None:
    expected = {result["decision"]["path"]: result["decision"]["sha256"]}
    expected.update({a["path"]: a["sha256"] for a in result["annotations"]})
    expected.update({s["path"]: s["delivered"] for s in result["sources"]})
    covered = set()
    for item in result["reviews"]:
        raw = json.loads(ap.committed_bytes(repo, item))
        ap.require(not cr.reviewer_report_errors(raw), "review-schema-refused")
        subject = raw["subject"]
        if subject["form"] == "paths":
            for entry in subject["paths"]:
                if expected.get(entry["path"]) == entry["sha256"]:
                    covered.add(entry["path"])
        else:
            base, end = subject["range"].split("..")
            base, end = commit_id(repo, base), commit_id(repo, end)
            ap.require(cr.git(repo, "merge-base", "--is-ancestor", base, end).returncode == 0,
                       "review-range-refused")
            # Same changed-path semantics as the review gate: a rename is a delete plus an add.
            # --literal-pathspecs repeats cr.git's GIT_LITERAL_PATHSPECS default on purpose, so a
            # change to that default never turns a path into a pathspec pattern here.
            diff = cr.git(repo, "--literal-pathspecs", "diff", "--name-only", "--no-renames", "-z", base, end, "--")
            ap.require(diff.returncode == 0, "review-range-refused")
            changed = {os.fsdecode(x) for x in diff.stdout.split(b"\0") if x}
            for path, digest in expected.items():
                if path in changed and source_hash(repo, end, path) == digest:
                    covered.add(path)
    ap.require(set(expected).issubset(covered), "independent-review-scope-missing")


def _validate_result(repo_root, decision_path, evidence: dict, *, writing: bool):
    repo = Path(repo_root).resolve()
    decision, run_path, rel = _decision(repo, Path(decision_path))
    ap.require(not schema_errors(evidence), "result-schema-refused")
    digest = cr.sha256_file(repo / rel)
    ap.require(evidence["decision"] == {"path": rel, "sha256": digest}, "result-decision-mismatch")
    consumer = ap.validate_implementation_binding if writing else ap.validate_historical_implementation_binding
    proof = consumer(repo, run_path, slot=decision["slot"], revision_path=rel, revision_sha256=digest)
    ap.require(decision["scope"] == proof.slot["scope"] and
               decision["constraint_refs"] == proof.slot["constraint_refs"] and
               decision.get(ap.DECLARATION_FIELD) == proof.slot.get(ap.DECLARATION_FIELD) and
               decision["slug"] == proof.slot["slug"], "decision-slot-mismatch")
    ap.require(all(ap.within_scope(path, decision["scope"]) for path in evidence["scope"]), "result-scope-refused")
    ap.require({s["path"] for s in evidence["sources"]} == set(evidence["scope"]) and
               len(evidence["sources"]) == len(evidence["scope"]), "result-source-scope-mismatch")
    if evidence["delivery_state"] == "complete":
        # Leg (a), above, keeps every result path inside the authorized scope. Leg (b): every
        # authorized entry, file or directory, is covered by at least one result path.
        # For a file-only decision scope, legs (a) and (b) make the result scope equal the
        # decision scope. Leg (c) below then adds nothing, unless a result path lies under a file
        # entry (a file that became a directory), which leg (c) refuses.
        # Only a directory entry admits more than one result path.
        ap.require(all(any(ap.within_scope(path, [entry]) for path in evidence["scope"])
                       for entry in decision["scope"]), "complete-scope-missing")
    if evidence["delivery_state"] == "unimplemented":
        ap.require(not evidence["scope"] and not evidence["sources"], "unimplemented-has-delivery")
    delivered, preimage = (commit_id(repo, evidence[k]) for k in ("source_revision", "preimage_revision"))
    ap.require(delivered == evidence["source_revision"] and preimage == evidence["preimage_revision"],
               "source-revision-not-exact")
    ap.require(cr.git(repo, "merge-base", "--is-ancestor", preimage, delivered).returncode == 0,
               "delivery-lineage-refused")
    if writing:
        ap.require(cr.git(repo, "merge-base", "--is-ancestor", delivered, "HEAD").returncode == 0,
                   "delivery-lineage-refused")
    if evidence["delivery_state"] == "complete":
        # Leg (c): every file changed under the authorized scope between the full preimage and
        # delivered object ids is listed. Scope entries are literal pathspecs, never magic or globs;
        # --literal-pathspecs repeats cr.git's default on purpose, as at the review-range read.
        # --no-renames lists a renamed file's old path too, as the review gate does, so a complete
        # result cannot omit the path a rename took out of scope.
        changed = cr.git(repo, "--literal-pathspecs", "diff", "--name-only", "--no-renames", "-z",
                         preimage, delivered, "--", *decision["scope"])
        ap.require(changed.returncode == 0, "source-unreadable")
        ap.require({os.fsdecode(x) for x in changed.stdout.split(b"\0") if x} <= set(evidence["scope"]),
                   "complete-scope-missing")
    for source in evidence["sources"]:
        ap.require(ap.within_scope(source["path"], proof.slot["scope"]), "result-scope-refused")
        ap.require(source_hash(repo, preimage, source["path"]) == source["preimage"] and
                   source_hash(repo, delivered, source["path"]) == source["delivered"], "source-evidence-mismatch")
        if writing:
            state = cr.path_state(repo, source["path"])
            clean = state.clean if source["delivered"] != ABSENT else not state.tracked and not os.path.lexists(repo / source["path"])
            ap.require(clean and source_hash(repo, "HEAD", source["path"]) == source["delivered"], "delivered-source-dirty")
    annotations = evidence["annotations"]
    if writing:
        ap.require(annotations == _annotation_inventory(repo, repo / rel), "annotation-review-scope-missing")
    for item in annotations:
        annotation = yaml.safe_load(ap.committed_bytes(repo, item))
        validate_annotation(repo, repo / rel, annotation)
    for item in evidence["reviews"]:
        report = json.loads(ap.committed_bytes(repo, item))
        ap.require(report.get("book") == {"id": proof.run["book_id"], "content_hash": proof.run["book_content_hash"]}
                   and report.get("run_id") == proof.run["run_id"], "review-identity-refused")
        if writing:
            ap.require(not cg.subject_problems(repo, report["subject"]), "review-source-stale")
    _review_coverage(repo, evidence)
    return proof


def write_result(repo_root, decision_path, evidence: dict, output: Path | None = None) -> Path:
    _validate_result(repo_root, decision_path, evidence, writing=True)
    repo = Path(repo_root).resolve()
    _, _, rel = _decision(repo, Path(decision_path))
    if output is None:
        numbers = [int(p.stem.split("-")[1]) for p in (repo / rel).parent.glob("result-*.yaml")
                   if re.fullmatch(r"result-[0-9]{3}\.yaml", p.name)]
        output = (repo / rel).with_name(f"result-{max(numbers, default=0) + 1:03d}.yaml")
    output, _ = ap.checked_path(repo, str(output))
    ap.require(output.parent == (repo / rel).parent and re.fullmatch(r"result-[0-9]{3}\.yaml", output.name),
               "result-layout-refused")
    return _write_new(output, evidence)


def query(repo_root, decision_path, revision="HEAD") -> dict:
    repo = Path(repo_root).resolve()
    decision, run_path, rel = _decision(repo, Path(decision_path))
    digest = cr.sha256_file(repo / rel)
    approved = False
    reason = None
    try:
        ap.validate_historical_implementation_binding(repo, run_path, slot=decision["slot"], revision_path=rel,
                                                      revision_sha256=digest)
        approved = True
    except (Refused, cr.RecordError) as exc:
        reason = exc.code if isinstance(exc, Refused) else "approval-evidence-refused"
    eligibility = {"eligible": False, "limit": None, "constraint_refs": decision["constraint_refs"]}
    try:
        view = ap.live_constraints(repo, decision["constraint_refs"])
        # For a declared-empty decision `constraint_refs` is empty, so every live rule is undeclared.
        undeclared = ap.undeclared_governing_constraints(repo, decision["scope"], decision["constraint_refs"],
                                                         view)
        if undeclared["overlapping"]:
            eligibility.update(limit="undeclared-governing-constraint", undeclared_refs=undeclared["overlapping"])
        else:
            eligibility["eligible"] = True
        if undeclared["unscoped"]:
            eligibility["unscoped_unchecked_refs"] = undeclared["unscoped"]
    except (Refused, cr.RecordError) as exc:
        eligibility["limit"] = exc.code if isinstance(exc, Refused) else "constraint-evidence-refused"
    except (bionic_config.BionicConfigError, OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
        eligibility["limit"] = "constraint-evidence-refused"
    annotations = []
    for item in _annotation_inventory(repo, repo / rel):
        annotation = load(repo / item["path"])
        validate_annotation(repo, repo / rel, annotation)
        annotations.append({"evidence": item, "annotation": annotation})
    answer = {"authority": "none", "reviewed_intent": {"approved": approved, "limit": reason,
              "original": decision, "sha256": digest, "annotations": annotations}, "historical_delivery": [],
              "current_eligibility": eligibility,
              "current_state": {"requested_revision": revision, "observed_revision": None, "state": "UNOBSERVED"}}
    try:
        current = commit_id(repo, revision)
        answer["current_state"]["observed_revision"] = current
        ap.full_history(repo)
    except Refused as exc:
        answer["current_state"]["limit"] = exc.code
        return answer
    for path, result in _revision_artifacts(repo, repo / rel, "result"):
        result_rel = path.relative_to(repo).as_posix()
        ap.require(cr.is_clean(repo, result_rel), "result-not-committed")
        ap.require(result["decision"] == {"path": rel, "sha256": digest}, "result-decision-mismatch")
        history = {"path": result_rel, "state": result["delivery_state"], "source_revision": result["source_revision"],
                   "scope": result["scope"], "observed": "UNOBSERVED"}
        answer["historical_delivery"].append(history)
        if not approved:
            continue
        try:
            _validate_result(repo, repo / rel, result, writing=False)
            delivered = commit_id(repo, result["source_revision"])
            preimage = commit_id(repo, result["preimage_revision"])
            ap.require(cr.git(repo, "merge-base", "--is-ancestor", preimage, delivered).returncode == 0,
                       "delivery-lineage-refused")
            for source in result["sources"]:
                ap.require(source_hash(repo, delivered, source["path"]) == source["delivered"] and
                           source_hash(repo, preimage, source["path"]) == source["preimage"], "source-evidence-mismatch")
            if result["delivery_state"] == "unimplemented":
                history["observed"] = "unimplemented"
                continue
            if cr.git(repo, "merge-base", "--is-ancestor", delivered, current).returncode != 0:
                history["observed"] = "unrelated-lineage"
                continue
            observed = []
            for source in result["sources"]:
                if revision == "HEAD":
                    state = cr.path_state(repo, source["path"])
                    if state.head is None:
                        ap.require(not state.tracked and not os.path.lexists(repo / source["path"]), "queried-source-dirty")
                    else:
                        ap.require(state.clean, "queried-source-dirty")
                value = source_hash(repo, current, source["path"])
                observed.append("delivered" if value == source["delivered"] else
                                "reverted" if value == source["preimage"] else "diverged")
            history["observed"] = ("delivered" if observed and all(s == "delivered" for s in observed) else
                                   "reverted" if observed and all(s == "reverted" for s in observed) else "diverged")
            history["paths"] = [{"path": s["path"], "state": state} for s, state in zip(result["sources"], observed)]
            answer["current_state"] = {"requested_revision": revision, "observed_revision": current,
                                        "state": history["observed"], "delivery_state": result["delivery_state"],
                                        "scope": result["scope"]}
        except Refused as exc:
            history["limit"] = exc.code
    return answer


# ───────────────────────────── order audit ─────────────────────────────

class AuditUnusable(Exception):
    """The audit cannot start: the directory is no repository, the configuration is unreadable, or
    `--run` names no run file inside the repository. The command exits 2. Every finding about a
    binding, however bad, is a report row and exits 0."""


_AUDIT_BINDINGS = (("implementation_bindings", "implementation"), ("migration_bindings", "migration"))


def _audit_row(run_rel: str, kind: str, binding: dict) -> dict:
    return {"run": run_rel, "slot": binding.get("slot"), "binding_kind": kind,
            "gate_prompt": binding.get("gate_prompt")}


def _audit_context_version(repo: Path, binding: dict) -> str:
    ref = binding["context"]
    blob = cr.git(repo, "show", "HEAD:" + ref["path"])
    ap.require(blob.returncode == 0 and cr.sha256_bytes(blob.stdout) == ref["sha256"], "context-unavailable")
    return json.loads(blob.stdout)["contract"]["version"]


def _audit_book(run_path: Path) -> dict:
    books = run_path.parent.parent.parent
    found = [books / tier / (run_path.parent.name + ".yaml") for tier in ("active", "archive")]
    found = [p for p in found if p.is_file()]
    ap.require(len(found) == 1, "book-unavailable")
    book = yaml.safe_load(found[0].read_bytes())
    ap.require(isinstance(book, dict), "book-unavailable")
    return book


def _audit_compare(repo: Path, run_path: Path, run: dict, binding: dict, proof_commit: str, row: dict) -> None:
    """Fill `row` with the outcome of one profile-3 binding at its proof commit."""
    book = _audit_book(run_path)
    slot = binding["slot"]
    module_tag = None if book.get("cycle_kind") == "patch" else slot
    try:
        order = v4.committed_order(run, run_path.parent, repo, module_tag, binding["gate_prompt"],
                                   rev=proof_commit)
        contradictions = order.contradictions()
        paths = list(order.commit)
        ties = sorted({p for i, a in enumerate(paths) for b in paths[i + 1:] if order.tied(a, b)
                       for p in (a, b)})
    except v4.OrderError as exc:
        history = exc.path.endswith(".json")
        row.update(outcome="uncomparable", reason="invalid-path-history" if history else "shallow-or-unavailable",
                   records=[exc.path] if history else [], detail=exc.message)
        return
    if contradictions:
        row.update(outcome="contradict",
                   records=sorted({p for pair in contradictions for p in pair}),
                   contradictions=[{"committed_first": a, "committed_later": b,
                                    "stamp_first": order.stamp[a], "stamp_later": order.stamp[b]}
                                   for a, b in contradictions])
    elif ties:
        row.update(outcome="uncomparable", reason="tie", records=ties)
    else:
        row.update(outcome="agree", records=sorted(paths))


def audit_order(repo_root, run=None) -> dict:
    """Report, without changing anything, where profile-3 stamp order and committed order disagree.

    Profile 3 replays a close's council evidence in stamp order. For each binding whose retained
    context names contract version "3", this rebuilds the gate scope and orders the scope's records by
    the commits that introduced them, read at the binding's proof commit (not at HEAD). A row's
    outcome is `agree`, `contradict` (a record committed first carries a later stamp) or
    `uncomparable` with one reason: `tie` (two records share a commit or lie on unrelated commits),
    `invalid-path-history` (a participating record was modified, removed or re-added) or
    `shallow-or-unavailable` (history, proof commit, context or book cannot be read). A binding
    naming another contract version is listed under `skipped`; a run in the legacy format is omitted.
    The audit refuses no approval, writes nothing and changes no replay or eligibility."""
    repo = Path(repo_root).resolve()
    if cr.git(repo, "rev-parse", "--git-dir").returncode != 0:
        raise AuditUnusable("not a git repository: " + str(repo))
    try:
        tree = bionic_config.load_config(repo).docs_root
    except (bionic_config.BionicConfigError, OSError, ValueError) as exc:
        raise AuditUnusable("the documentation tree configuration is unreadable: " + type(exc).__name__) from None
    if run is not None:
        try:
            absolute, _ = ap.checked_path(repo, str(run))
        except ap.Refused:
            raise AuditUnusable("--run names a path outside the repository") from None
        if not absolute.is_file():
            raise AuditUnusable("--run names no run file")
        run_paths = [absolute]
    else:
        run_paths = sorted((tree / "promptbooks" / "runs").glob("*/run-*.yaml"))
    probe = cr.git(repo, "rev-parse", "--is-shallow-repository")
    shallow = probe.returncode != 0 or probe.stdout.strip() != b"false"
    rows: list[dict] = []
    skipped: list[dict] = []
    for run_path in run_paths:
        run_rel = run_path.resolve().relative_to(repo).as_posix()
        try:
            doc = yaml.safe_load(run_path.read_bytes())
        except (OSError, ValueError, yaml.YAMLError):
            doc = None
        if not isinstance(doc, dict) or doc.get("format_version") != "2":
            continue
        for key, kind in _AUDIT_BINDINGS:
            entries = doc.get(key)
            for binding in entries if isinstance(entries, list) else []:
                if not isinstance(binding, dict):
                    continue
                row = _audit_row(run_rel, kind, binding)
                try:
                    version = _audit_context_version(repo, binding)
                    if version != "3":
                        skipped.append({**row, "contract": version})
                        continue
                    row["contract"] = version
                    ap.require(not shallow, "history-unavailable")
                    row["proof_commit"] = ap._binding_commit(repo, run_rel, binding, binding_key=key)
                    _audit_compare(repo, run_path.resolve(), doc, binding, row["proof_commit"], row)
                except (ap.Refused, cr.RecordError, OSError, ValueError, TypeError, KeyError,
                        yaml.YAMLError) as exc:
                    row.update(outcome="uncomparable", reason="shallow-or-unavailable",
                               detail=getattr(exc, "code", None) or type(exc).__name__)
                rows.append(row)
    counts = {name: sum(1 for r in rows if r["outcome"] == name) for name in ("agree", "contradict", "uncomparable")}
    return {"audit": "order", "authority": "none", "summary": counts, "bindings": rows, "skipped": skipped}
