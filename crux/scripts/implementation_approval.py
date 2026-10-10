"""Read-only proof validation for implementation decisions.

Live execution does not use this loader to enable format two. The sole production
binding writer is advance-run; synthetic bindings are allowed only as test controls.
Historical policy shares council_gate's evaluator, including permanent stops.
"""
from __future__ import annotations

import copy
import json
import inspect
import ast
import re
import textwrap
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

import council_gate as cg
import council_records as cr
import council_history_v2 as policy_two
import council_records_v2 as records_two
import council_history_v3 as policy_three
import council_history_v4 as policy_four
import bionic_config

CONTRACT_VERSION = "4"


@dataclass(frozen=True)
class PolicyProfile:
    version: str
    kernel: object
    contract_sha256: str
    records: object


# Profile 1 was an unissued component fixture. Profile 2 is sealed before the
# production writer is enabled. Altering a semantic dependency requires a new
# profile while preserving the implementation of every issued profile.
SUPPORTED_PROFILES = {"2": PolicyProfile("2", policy_two,
    "c44a85a6160a9fb5ddc6ac938ce7b15d462c9d40b2808d2db92699ad15b8f4d2", records_two),
    # Issued: PB-0141 RUN-001 holds an approval under this profile. Never change a byte that
    # gate_contract_bytes("3") covers. Profile 4 was issued beside it rather than as an edit of it.
    "3": PolicyProfile("3", policy_three,
        "62a4760de1e477fb13c78f9162c49c4da620b5ace304ca72ce6fc45496d16d23", cr),
    # Current: new closes retain profile 4, which orders council evidence by committed history.
    # Once a close retains it, never change a byte gate_contract_bytes("4") covers; issue profile 5.
    "4": PolicyProfile("4", policy_four, "b1bda66c8667b1c9a8bbaa39c47d58fd55245b718e6d025f71f7221d47035167", cr)}


def supported_profile(version) -> PolicyProfile:
    """Dispatch trusted issued policy implementations, never retained source."""
    if not isinstance(version, str) or version not in SUPPORTED_PROFILES:
        raise Refused("context-version-unsupported")
    return SUPPORTED_PROFILES[version]


def production_contract_bytes(version=CONTRACT_VERSION) -> bytes:
    """Never issue changed semantics under an existing production version."""
    profile = supported_profile(version)
    content = gate_contract_bytes(version)
    require(cr.sha256_bytes(content) == profile.contract_sha256, "context-contract-unsupported")
    return content


def gate_contract_bytes(version=CONTRACT_VERSION) -> bytes:
    """The supported policy implementation, never executed from retained evidence.

    A future policy change must preserve this version or supply another supported
    implementation. Source text retained by a close is evidence, not executable input.
    """
    # Bind the transitive owned policy, including live constant values and schema
    # bytes. Retaining only top-level function text misses helpers and globals.
    # Unrelated live routing and registry loading are deliberately not roots.
    profile = supported_profile(version)
    kernel = profile.kernel
    validator = kernel.schema_engine
    modules = {kernel: "council-policy-" + version, profile.records: "council_records",
               validator: "schema-validator-" + version}
    if version in ("3", "4"):
        start_parser = getattr(kernel, "start_snapshot_yaml", None)
        require(inspect.ismodule(start_parser) and getattr(start_parser, "__file__", None),
                "context-policy-unavailable")
        modules[start_parser] = "start-snapshot-yaml-" + version
    policy = {}
    plugin = Path(__file__).resolve().parent.parent
    declarations = {module: {node.name for node in ast.parse(Path(module.__file__).read_text()).body
                              if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
                    for module in modules}

    def value(item):
        if item is None or isinstance(item, (str, bool, int, float)):
            return item
        if isinstance(item, (list, tuple, set, frozenset)):
            items = [value(x) for x in item]
            if isinstance(item, (set, frozenset)):
                items.sort(key=lambda x: json.dumps(x, sort_keys=True))
            return {"type": type(item).__name__, "items": items}
        if isinstance(item, dict):
            return {str(k): value(v) for k, v in sorted(item.items())}
        if isinstance(item, Path):
            path = item.relative_to(plugin).as_posix()
            return {"path": path, **({"sha256": cr.sha256_bytes(item.read_bytes())} if item.is_file() else {})}
        if isinstance(item, re.Pattern):
            return {"pattern": item.pattern, "flags": item.flags}
        if inspect.isfunction(item):
            # Schema type predicates are lambdas inside a dictionary; their
            # source lines bind their behavior without parsing an expression.
            for module in modules:
                if item.__globals__ is vars(module):
                    for name in item.__code__.co_names:
                        if name in vars(module):
                            visit(module, name)
            return {"source": inspect.getsource(item)}
        if inspect.ismodule(item) and item in modules:
            return {"module": modules[item]}
        raise Refused("context-policy-unavailable")

    def visit(module, name):
        key = modules[module] + "." + name
        if key in policy:
            return
        item = getattr(module, name)
        if inspect.ismodule(item):
            if item in modules:
                policy[key] = value(item)
            return
        if inspect.isfunction(item) or inspect.isclass(item):
            # Imported standard-library/third-party objects are not owned policy.
            if item.__module__ not in {m.__name__ for m in modules}:
                if name in declarations[module]:
                    raise Refused("context-policy-unavailable")
                return
            if inspect.isclass(item) and module is validator:
                # The existing validator loads by path without sys.modules
                # registration. Inspect cannot locate its class declarations.
                raw = Path(module.__file__).read_text()
                declaration = next(n for n in ast.parse(raw).body
                                   if isinstance(n, ast.ClassDef) and n.name == item.__name__)
                start = min([declaration.lineno] + [n.lineno for n in declaration.decorator_list])
                source = "".join(raw.splitlines(keepends=True)[start - 1:declaration.end_lineno])
            else:
                source = inspect.getsource(item)
            policy[key] = {"source": source}
            tree = ast.parse(textwrap.dedent(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id in vars(module):
                    visit(module, node.id)
                elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                    dependency = vars(module).get(node.value.id)
                    if inspect.ismodule(dependency) and dependency in modules and hasattr(dependency, node.attr):
                        visit(dependency, node.attr)
            return
        # Only source-owned globals are dependencies, not imported typing names.
        if name.startswith("_") or name.isupper():
            # Mark before traversing predicates contained in a constant mapping.
            policy[key] = None
            policy[key] = value(item)

    try:
        roots = [(kernel, "_evaluate_council_history"), (kernel, "GateClass"),
                             (kernel, "discover_records"), (validator, "validate"),
                             (validator, "load_schema")]
        if version in ("3", "4"):
            roots.extend(((kernel, "expected_round"), (kernel, "start_identity"),
                                 (kernel, "retained_path"), (kernel, "validate_context"),
                                 (kernel, "context_records"), (kernel, "deciding_subject"),
                                 (kernel.start_snapshot_yaml, "load_yaml")))
        for module, name in roots:
            if version in ("3", "4"):
                item = getattr(module, name)
                require((inspect.isfunction(item) or inspect.isclass(item)) and
                        item.__module__ == module.__name__, "context-policy-unavailable")
                if inspect.isfunction(item):
                    require(item.__globals__ is vars(module) and
                            Path(item.__code__.co_filename).resolve() == Path(module.__file__).resolve(),
                            "context-policy-unavailable")
            visit(module, name)
            if version in ("3", "4"):
                require(modules[module] + "." + name in policy, "context-policy-unavailable")
        return json.dumps({"version": version, "policy": policy},
                          sort_keys=True, separators=(",", ":")).encode()
    except (OSError, ValueError, TypeError, AttributeError, SyntaxError, StopIteration, RecursionError):
        raise Refused("context-policy-unavailable") from None


class Refused(ValueError):
    """A refusal code safe to print without including untrusted values."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise Refused(code)


def book_hash(book: dict) -> str:
    """Use the canonical hash while preserving original format-one bytes."""
    return cr._validator().compute_book_hash(book)


def validate_format_two(book: dict) -> None:
    """Use the same canonical structure validator as live execution."""
    try:
        cr._validator().validate_format_two(book)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise Refused(exc.code if hasattr(exc, "code") else "format-two-shape-refused") from None


def full_history(repo: Path) -> None:
    probe = cr.git(repo, "rev-parse", "--is-shallow-repository")
    require(probe.returncode == 0 and probe.stdout.strip() == b"false", "history-unavailable")


def checked_path(repo: Path, given: str) -> tuple[Path, str]:
    try:
        return cr.resolve_in_repo(repo, given)
    except (cr.RecordError, TypeError, ValueError):
        raise Refused("path-refused") from None


def committed_bytes(repo: Path, item: dict) -> bytes:
    require(isinstance(item, dict) and set(item) == {"path", "sha256"}, "evidence-shape-refused")
    path, rel = checked_path(repo, item["path"])
    try:
        state = cr.path_state(repo, rel)
        require(state.clean and state.head == item["sha256"], "evidence-not-committed")
        return path.read_bytes()
    except OSError:
        raise Refused("evidence-unreadable") from None


def _policy_call(profile, name, *args):
    """Translate bounded policy refusal while leaving semantic admission owned."""
    try:
        return getattr(profile.kernel, name)(*args)
    except cr.RecordError as exc:
        raise Refused(exc.message) from None


def within_scope(path: str, scope: list[str]) -> bool:
    return any(path == entry or path.startswith(entry.rstrip("/") + "/") for entry in scope)


def live_constraints(repo: Path, handles: list[str]) -> dict:
    """Require raw Accepted sources and the completed current governing view; return that view."""
    _raw_live_constraints(repo, handles)
    import implementation_migration as migration
    view = migration.authority_view(repo)
    denied = set(view["historical_handles"]) | set(view["retired_handles"])
    require(not denied.intersection(handles), "constraint-not-live-accepted")
    return view


def _accepted_governs_entries(repo: Path, tree: Path) -> list[dict]:
    """The mapping `governs` entries of every committed, active-tier Accepted ADR, in path order."""
    entries = []
    for path in sorted((tree / "adrs").glob("*.md")):
        if not re.match(r"(?:[A-Z][A-Z0-9]{1,9}-)?ADR-[0-9]{4}-", path.name):
            continue
        absolute, rel = checked_path(repo, str(path))
        require(cr.is_clean(repo, rel), "constraint-not-committed")
        match = re.match(r"\A---\r?\n(.*?)\r?\n---", absolute.read_text(), re.S)
        require(match is not None, "constraint-record-refused")
        fm = yaml.safe_load(match[1])
        require(isinstance(fm, dict) and "record_type" not in fm, "constraint-record-refused")
        if fm.get("status") == "Accepted":
            entries.extend(e for e in fm.get("governs", []) if isinstance(e, dict))
    return entries


def _scope_path(repo: Path, entry: str) -> str:
    """The repo-relative form of one scope entry, refused as `path-refused` when it is a symlink.

    `checked_path` already refuses a symlinked directory, the repository root and a path outside
    the repository. A symlinked last component is refused here, so overlap is never claimed for an
    alias, chain, loop or dangling link the reader did not resolve."""
    absolute, rel = checked_path(repo, entry)
    require(not absolute.is_symlink(), "path-refused")
    return rel


def _raw_live_constraints(repo: Path, handles: list[str]) -> None:
    tree = bionic_config.load_config(repo).docs_root
    retired = set()
    for entry in _accepted_governs_entries(repo, tree):
        retired.update(entry.get("retires", []))
    for handle in handles:
        require(isinstance(handle, str) and re.fullmatch(r"(?:[A-Z][A-Z0-9]{1,9}-)?ADR-[0-9]{4}/[a-z0-9-]+", handle),
                "constraint-handle-refused")
        identity = handle.split("/", 1)[0]
        candidates = []
        for tier in (tree / "adrs", tree / "adrs" / "archive"):
            for path in tier.glob(identity + "-*.md"):
                absolute, rel = checked_path(repo, str(path))
                require(cr.is_clean(repo, rel), "constraint-not-committed")
                text = absolute.read_text()
                match = re.match(r"\A---\r?\n(.*?)\r?\n---", text, re.S)
                require(match is not None, "constraint-record-refused")
                fm = yaml.safe_load(match[1])
                if isinstance(fm, dict) and fm.get("id") == identity:
                    candidates.append((tier, fm))
        require(len(candidates) == 1, "constraint-identity-refused")
        tier, fm = candidates[0]
        require(tier == tree / "adrs" and fm.get("status") == "Accepted", "constraint-not-live-accepted")
        require(handle not in retired, "constraint-not-live-accepted")
        # A live entry that carries `retires` is itself a live rule; only its targets retire.
        entries = [e for e in fm.get("governs", []) if isinstance(e, dict) and e.get("handle") == handle]
        require(len(entries) == 1, "constraint-not-live-accepted")


def undeclared_governing_constraints(repo: Path, scope: list[str], handles: list[str],
                                     view: dict) -> dict[str, list[str]]:
    """Undeclared live Accepted `governs` handles, split by whether eligibility checked them.

    `overlapping`: the rule's path scope overlaps `scope`. `unscoped`: the rule's scope names no
    path token, so no overlap was checked. Scope tokens come from the doctrine reader; `scope`
    entries are normalized to repo-relative form first, and a symlinked entry is refused as
    `path-refused`. `view` is the governing view
    `live_constraints` returned. An entry that retires another is still a live rule and is checked
    here; only the handles it retires are excluded."""
    import doctrine_projection as doctrine
    config = bionic_config.load_config(repo)
    tree = config.docs_root
    paths = [_scope_path(repo, entry) for entry in scope]
    records = _accepted_governs_entries(repo, tree)
    dead = {h for e in records for h in e.get("retires", [])}
    dead |= set(view["historical_handles"]) | set(view["retired_handles"])
    overlapping, unscoped = set(), set()
    for entry in records:
        handle = entry.get("handle")
        if not isinstance(handle, str) or handle in handles or handle in dead:
            continue
        tokens = doctrine.scope_path_tokens(str(entry.get("scope", "")), config.docs_dir)
        if not tokens:
            unscoped.add(handle)
        elif any(doctrine.scope_contains(t, p) or doctrine.scope_contains(p, t) for t in tokens for p in paths):
            overlapping.add(handle)
    return {"overlapping": sorted(overlapping), "unscoped": sorted(unscoped)}


def _migration_subject_constraints(repo: Path, run_path: Path, *, slot: str,
                                   batch_path: str, batch_sha256: str) -> None:
    """Check the exact selected subject through the shared current eligibility gate."""
    import implementation_migration as migration
    migration._current_subject_eligibility(repo, run_path, slot=slot,
        batch_path=batch_path, batch_sha256=batch_sha256)


@dataclass(frozen=True)
class ValidatedBinding:
    binding: dict
    slot: dict
    run: dict
    book: dict
    proof_commit: str


def prepare_implementation_close(repo_root, run_path, gate, revision_path, artifacts):
    """Read-only close preparation; only advance-run publishes the returned proof.

    Re-evaluate against the exact registry bytes retained here. The committed
    historical consumer later uses those bytes, never the current registry.
    """
    return _prepare_close(repo_root, run_path, gate, revision_path, artifacts, migration=False)


def prepare_migration_close(repo_root, run_path, gate, batch_path, artifacts):
    """Prepare a separately bound batch proof; approval does not apply migration."""
    return _prepare_close(repo_root, run_path, gate, batch_path, artifacts, migration=True)


# The five council dimensions each formal slot kind's question names. run-council's selector
# check uses the same table, so a question it admits is a question the close admits.
QUESTION_DIMENSIONS = {
    "implementation": ("Completeness", "Correctness", "Consistency", "Clarity", "Security"),
    "verify": ("Evidence/Reproduction", "Root-cause correctness", "Approach soundness and minimality",
               "Consistency", "Security"),
    "patch": ("Evidence/Reproduction", "Root-cause correctness", "Approach soundness and minimality",
              "Blast-radius proportion", "Security"),
}


# The slot field that declares an empty constraint set, and the words a deciding council question
# must contain when the slot carries it: the council judges the declaration, not only the subject.
DECLARATION_FIELD = "no_governing_constraint"
DECLARED_EMPTY_WORDS = "declared-empty constraint set"


def declaration_of(item) -> dict | None:
    """The declared-empty marker of a book slot or decision record, else None."""
    marker = item.get(DECLARATION_FIELD) if isinstance(item, dict) else None
    return marker if isinstance(marker, dict) else None


def declared_empty_findings(repo: Path, item: dict) -> dict[str, list[str]] | None:
    """For a declared-empty slot or decision, the undeclared live Accepted rules split by whether
    their path scope overlaps its scope (`overlapping`) or names no path (`unscoped`); None when
    `item` carries no declaration."""
    if declaration_of(item) is None:
        return None
    view = live_constraints(repo, [])
    return undeclared_governing_constraints(repo, item["scope"], [], view)


def require_declaration_unconflicted(repo: Path, item: dict) -> None:
    """Refuse `undeclared-governing-constraint` while an undeclared live Accepted rule's path scope
    overlaps a declared-empty slot's or decision's scope."""
    found = declared_empty_findings(repo, item)
    require(found is None or not found["overlapping"], "undeclared-governing-constraint")


def unchecked_rules(repo: Path, item: dict) -> list[dict]:
    """Each undeclared live Accepted rule whose scope names no path, with its handle, rule text and
    scope. The council reads this list as a subject beside the declared reason."""
    found = declared_empty_findings(repo, item)
    require(found is not None, "decision-not-declared-empty")
    wanted = set(found["unscoped"])
    tree = bionic_config.load_config(repo).docs_root
    rows = {}
    for entry in _accepted_governs_entries(repo, tree):
        if entry.get("handle") in wanted:
            rows[entry["handle"]] = {"handle": entry["handle"], "rule": str(entry.get("rule", "")),
                                     "scope": str(entry.get("scope", ""))}
    return [rows[h] for h in sorted(rows)]


def selected_question_refusal(question: str, kind: str, selected: dict, subjects: list[dict],
                              declaration: dict | None = None):
    """The refusal code for a council question that did not select `selected`, else None.

    A question selects a subject when it names that subject's path and SHA256, all five
    dimensions of `kind` and the words ``architectural conflict``, and names the SHA256 of no
    other subject. `selected` and each of `subjects` carry `path` and `sha256`. A slot that
    carries a declaration (`declaration` is not None) also needs the words
    ``declared-empty constraint set``."""
    if selected["path"] not in question or selected["sha256"] not in question:
        return "deciding-question-revision-missing"
    if any(isinstance(s.get("sha256"), str) and s["sha256"] != selected["sha256"] and s["sha256"] in question
           for s in subjects):
        return "deciding-question-selection-ambiguous"
    lowered = question.casefold()
    if kind not in QUESTION_DIMENSIONS or \
            any(dimension.casefold() not in lowered for dimension in QUESTION_DIMENSIONS[kind]):
        return "deciding-question-dimensions-missing"
    if "architectural conflict" not in lowered:
        return "deciding-question-conflict-missing"
    if declaration is not None and DECLARED_EMPTY_WORDS not in lowered:
        return "deciding-question-declaration-missing"
    return None


def _deciding_question_selects(repo: Path, deciding: dict, kind: str, revision: dict,
                               declaration: dict | None = None) -> None:
    """Bind the approval to what the deciding council was asked, read from its sealed question.

    The record seals the question's path and SHA256. The bytes at that path must still hash to it,
    and the question must have selected exactly this revision."""
    question = deciding.get("question")
    require(isinstance(question, dict) and isinstance(question.get("path"), str) and
            isinstance(question.get("sha256"), str), "deciding-question-unavailable")
    try:
        path, _ = checked_path(repo, question["path"])
        require(not path.is_symlink(), "deciding-question-unavailable")
        content = path.read_bytes()
        text = content.decode("utf-8")
    except (OSError, UnicodeDecodeError, Refused):
        raise Refused("deciding-question-unavailable") from None
    require(cr.sha256_bytes(content) == question["sha256"], "deciding-question-unavailable")
    subjects = [s for s in deciding.get("subjects", []) if isinstance(s, dict)]
    code = selected_question_refusal(text, kind, revision, subjects, declaration)
    require(code is None, code or "")


def _context_records(profile, records, run, gate):
    if profile.version in ("3", "4"):
        return _policy_call(profile, "context_records", records, run, gate)
    scoped = sum((list(group) for group in profile.kernel._scope(records, run, gate.module_tag, gate.n)), [])
    return scoped


def _prepare_close(repo_root, run_path, gate, revision_path, artifacts, *, migration):
    import implementation_decisions as ids
    repo = Path(repo_root).resolve()
    full_history(repo)
    run, book, _ = _owner(repo, Path(run_path))
    slot = "patch" if gate.module_kind == "patch" else gate.module_tag
    require(gate.cls == "module-close" or
            (gate.cls == "council" and gate.module_kind == "patch" and gate.phase == "verify"),
            "implementation-close-position-refused")
    require(run.get("current_prompt") == gate.n, "implementation-close-position-refused")
    prompt = next((p for p in run["prompts"] if p["n"] == gate.n), None)
    require(prompt is not None and prompt.get("state") in ("running", "blocked"), "close-already-terminal")
    slot_entries = [item for item in book["implementation_slots"] if item["slot"] == slot]
    require(len(slot_entries) == 1, "slot-not-declared")
    slot_entry = slot_entries[0]
    require(("migration_batch" in slot_entry) == migration, "selected-subject-role-refused")
    raw_revision = repo / revision_path
    require(not cr.reached_through_symlink(raw_revision), "selected-revision-symlink-refused")
    if migration:
        import implementation_migration as migrations
        require(".." not in raw_revision.parts, "selected-batch-path-refused")
        _, selected_rel = checked_path(repo, str(raw_revision))
        require(selected_rel == slot_entry["migration_batch"]["path"], "selected-batch-path-refused")
        selected, selected_bytes = migrations._load_batch(repo, raw_revision)
        require(selected["approval_slot"] == slot, "selected-slot-mismatch")
    else:
        selected, owner_path, selected_rel = ids._decision(repo, raw_revision)
        require(owner_path == Path(run_path).resolve() and
                all(selected.get(key) == slot_entry[key] for key in ("slot", "slug", "scope", "constraint_refs")) and
                selected.get(DECLARATION_FIELD) == slot_entry.get(DECLARATION_FIELD),
                "selected-slot-mismatch")
        selected_bytes = (repo / selected_rel).read_bytes()
    if not migration:
        live_constraints(repo, slot_entry["constraint_refs"])
        require_declaration_unconflicted(repo, slot_entry)
    revision = {"path": selected_rel, "sha256": cr.sha256_bytes(selected_bytes)}
    committed_bytes(repo, revision)
    if migration:
        _migration_subject_constraints(repo, Path(run_path), slot=slot,
            batch_path=revision["path"], batch_sha256=revision["sha256"])
    binding_key = "migration_bindings" if migration else "implementation_bindings"
    require(not any(entry.get("gate_prompt") == gate.n for entry in run.get(binding_key, [])),
            "close-already-bound")
    profile = supported_profile(CONTRACT_VERSION)
    identity = profile.kernel.start_identity(repo, run, run_path)
    registry_bytes = cg.REGISTRY_PATH.read_bytes()
    registry = json.loads(registry_bytes)
    records = profile.kernel.discover_records(Path(run_path).resolve().parent)
    verdict = profile.kernel._evaluate_council_history(gate, run, run_path, repo, artifacts, records,
                                                       lambda: (registry, None), identity=identity)
    require(verdict.verdict == "pass", "implementation-gate-" + verdict.verdict)
    deciding_ref = {"path": verdict.deciding_record,
                    "sha256": cr.sha256_file(repo / verdict.deciding_record)}
    deciding = json.loads(committed_bytes(repo, deciding_ref))
    retained = _policy_call(profile, "deciding_subject", repo, run_path, deciding, revision)
    require(committed_bytes(repo, retained) == committed_bytes(repo, revision), "retained-revision-mismatch")
    # The deciding council must have selected this exact subject; carrying it is not enough.
    _deciding_question_selects(repo, deciding, gate.module_kind, revision, declaration_of(slot_entry))
    scoped = _context_records(profile, records, run, gate)
    inventory = sorted(({"path": rec.path.relative_to(repo).as_posix(), "sha256": cr.sha256_file(rec.path)}
                        for rec in scoped), key=lambda item: item["path"])
    for item in inventory:
        committed_bytes(repo, item)
    contract_bytes = production_contract_bytes(profile.version)
    folder = Path(run_path).resolve().parent.relative_to(repo) / "gate-contexts" / run["run_id"] / slot
    files = {}

    def retain(name, content):
        digest = cr.sha256_bytes(content)
        rel = (folder / (name + "-" + digest + ".json")).as_posix()
        files[rel] = content
        return {"path": rel, "sha256": digest}

    context = {"record_type": "implementation-gate-context",
               "format_version": {"3": "2", "4": "3"}[profile.version],
               "contract": {"version": profile.version, **retain("contract", contract_bytes)},
               "registry": retain("registry", registry_bytes), "records": inventory, "artifacts": artifacts,
               "start_identity": identity}
    _policy_call(profile, "validate_context", context)
    context_ref = retain("context", json.dumps(context, sort_keys=True, separators=(",", ":")).encode())
    binding = {"slot": slot, "book_id": run["book_id"], "run_id": run["run_id"],
               "book_content_hash": run["book_content_hash"],
               "batch" if migration else "revision": revision, "gate_prompt": gate.n,
               "deciding_record": deciding_ref, "retained_subject": retained, "context": context_ref}
    return binding, files


def _owner(repo: Path, run_path: Path) -> tuple[dict, dict, str]:
    absolute, rel = checked_path(repo, str(run_path))
    require(cr.is_clean(repo, rel), "run-not-committed")
    run = yaml.safe_load(absolute.read_bytes())
    require(isinstance(run, dict) and run.get("format_version") == "2", "explicit-format-required")
    books = absolute.parent.parent.parent
    require(books.name == "promptbooks" and absolute.parent.parent.name == "runs", "run-layout-refused")
    candidates = [books / tier / (absolute.parent.name + ".yaml") for tier in ("active", "archive")]
    existing = [p for p in candidates if p.exists()]
    require(len(existing) == 1, "book-identity-refused")
    path, book_rel = checked_path(repo, str(existing[0]))
    require(cr.is_clean(repo, book_rel), "book-not-committed")
    book = yaml.safe_load(path.read_bytes())
    require(isinstance(book, dict), "book-shape-refused")
    validate_format_two(book)
    try:
        cr._validator().validate_format_two_run(run, book)
    except ValueError as exc:
        raise Refused(getattr(exc, "code", "run-book-shape-refused")) from None
    require(run.get("book_id") == book.get("id") and run.get("book_content_hash") == book_hash(book),
            "book-binding-refused")
    require(absolute.name == "run-" + run.get("run_id", "") + ".yaml", "run-identity-refused")
    return run, book, rel


def _binding_commit(repo: Path, run_rel: str, binding: dict, *, binding_key="implementation_bindings") -> str:
    history = cr.git(repo, "log", "--reverse", "--format=%H", "--", run_rel)
    require(history.returncode == 0 and history.stdout.strip(), "history-unavailable")
    previous = []
    first = None
    for commit in history.stdout.decode().splitlines():
        blob = cr.git(repo, "show", commit + ":" + run_rel)
        require(blob.returncode == 0, "run-history-unavailable")
        snapshot = yaml.safe_load(blob.stdout)
        require(isinstance(snapshot, dict) and snapshot.get("format_version") == "2" and
                all(snapshot.get(k) == binding[k] for k in ("book_id", "run_id", "book_content_hash")),
                "run-history-identity-changed")
        entries = snapshot.get(binding_key, [])
        require(isinstance(entries, list) and entries[:len(previous)] == previous, "binding-history-changed")
        previous = entries
        prompt = next((p for p in snapshot.get("prompts", []) if p.get("n") == binding["gate_prompt"]), None)
        if binding in entries and first is None:
            require(prompt is not None and prompt.get("state") == "done" and prompt.get("completed"),
                    "binding-close-incomplete")
            require(binding["deciding_record"]["path"] in prompt.get("artifacts", []), "deciding-not-attached")
            first = commit
        elif first is None:
            require(prompt is None or prompt.get("state") != "done", "close-precedes-binding")
    require(first is not None, "binding-not-committed")
    return first


def _validate_binding(repo_root, run_path, *, slot, revision_path, revision_sha256,
                                    historical_revision=False, migration=False,
                                    _worker_binding=None) -> ValidatedBinding:
    """Prove a past close, independently of current constraint eligibility."""
    repo = Path(repo_root).resolve()
    full_history(repo)
    run, book, run_rel = _owner(repo, Path(run_path))
    declarations = [s for s in book["implementation_slots"] if s["slot"] == slot]
    require(len(declarations) == 1, "slot-not-declared")
    declaration = declarations[0]
    require(("migration_batch" in declaration) == migration, "selected-subject-role-refused")
    subject_key = "batch" if migration else "revision"
    binding_key = "migration_bindings" if migration else "implementation_bindings"
    if migration:
        import implementation_migration as migrations
        require(declaration["migration_batch"] == {"role": "migration-batch", "path": revision_path},
                "selected-batch-path-refused")
        batch, content = migrations._load_batch(repo, revision_path)
        require(batch["approval_slot"] == slot and cr.sha256_bytes(content) == revision_sha256,
                "selected-batch-identity-refused")
    for entry in declaration["scope"]:
        checked_path(repo, entry)
    entries = run.get(binding_key)
    require(isinstance(entries, list) and entries, "approval-binding-missing")
    selected = [b for b in entries if isinstance(b, dict) and b.get("slot") == slot]
    require(len(selected) <= 1, "binding-close-identity-ambiguous")
    if historical_revision:
        selected = [b for b in selected if b.get(subject_key) == {"path": revision_path, "sha256": revision_sha256}]
    require(selected, "approval-binding-missing")
    binding = selected[0]
    required = {"slot", "book_id", "run_id", "book_content_hash", subject_key, "gate_prompt",
                "deciding_record", "retained_subject", "context"}
    require(set(binding) == required, "binding-shape-refused")
    require(all(binding.get(k) == run.get(k) for k in ("book_id", "run_id", "book_content_hash")),
            "binding-identity-refused")
    require(binding[subject_key] == {"path": revision_path, "sha256": revision_sha256}, "revision-not-approved")
    committed_bytes(repo, binding[subject_key])
    proof_commit = _binding_commit(repo, run_rel, binding, binding_key=binding_key)
    context = json.loads(committed_bytes(repo, binding["context"]))
    require(isinstance(context, dict), "context-shape-refused")
    contract = context.get("contract")
    require(isinstance(contract, dict) and isinstance(contract.get("version"), str), "context-version-unsupported")
    profile = supported_profile(contract["version"])
    if profile.version in ("3", "4"):
        _policy_call(profile, "validate_context", context)
    fields = {"record_type", "format_version", "contract", "registry", "records", "artifacts"}
    if context.get("format_version") in ("2", "3"):
        fields.add("start_identity")
    require(set(context) == fields, "context-shape-refused")
    contract = context["contract"]
    require(context["record_type"] == "implementation-gate-context" and
            context["format_version"] in ("1", "2", "3") and
            isinstance(contract, dict) and set(contract) == {"version", "path", "sha256"} and
            isinstance(contract["version"], str), "context-version-unsupported")
    # Contract 2 wrote context format 1; contract 3 writes context format 2; contract 4 writes format 3.
    require(context["format_version"] == {"2": "1", "3": "2", "4": "3"}[profile.version],
            "context-version-unsupported")
    contract_ref = {k: contract[k] for k in ("path", "sha256")}
    require(committed_bytes(repo, contract_ref) == production_contract_bytes(profile.version), "context-contract-unsupported")
    if profile.version == "2":
        if _worker_binding is None:
            return _historical_worker(repo, run_path, binding, migration=migration)
        require(_worker_binding == binding, "historical-worker-binding-mismatch")
    registry = json.loads(committed_bytes(repo, context["registry"]))
    require(isinstance(registry, dict) and isinstance(registry.get("models"), dict) and
            isinstance(registry.get("model_roles"), dict) and isinstance(registry.get("retired_models"), list),
            "context-registry-refused")
    records = profile.kernel.discover_records(Path(run_path).resolve().parent)
    members = [p["n"] for p in book["prompts"] if p.get("module_tag") == slot]
    patch = book["cycle_kind"] == "patch"
    if patch:
        members = [p["n"] for p in book["prompts"] if p.get("phase") == "verify"]
    require(members and binding["gate_prompt"] == members[-1], "binding-gate-refused")
    # Rebuild the gate the close evaluated. A patch closes on its single verify-phase council
    # prompt. A module closes at ordinal 4; its ordinal-3 prompt, where a route lands, is the
    # module's third member. The module kind is the slot name's prefix: "verify-1" is verify.
    if patch:
        gate = profile.kernel.GateClass(cls="council", n=members[-1], phase="verify", module_kind="patch",
                                        ordinal3=members[0], module_prompts=tuple(members))
    else:
        gate = profile.kernel.GateClass(cls="module-close", n=members[-1], module_tag=slot, ordinal=4,
                                        module_kind=slot.split("-")[0], ordinal3=members[2],
                                        module_prompts=tuple(members))
    scoped = _context_records(profile, records, run, gate)
    inventory = sorted(({"path": r.path.relative_to(repo).as_posix(), "sha256": cr.sha256_file(r.path)}
                        for r in scoped), key=lambda x: x["path"])
    require(context["records"] == inventory, "history-inventory-mismatch")
    for item in inventory + [binding["context"], binding[subject_key], binding["retained_subject"],
                             context["registry"], contract_ref]:
        committed_bytes(repo, item)
        blob = cr.git(repo, "show", proof_commit + ":" + item["path"])
        require(blob.returncode == 0 and cr.sha256_bytes(blob.stdout) == item["sha256"], "proof-not-at-close")
    prompt = next((p for p in run["prompts"] if p.get("n") == gate.n), None)
    require(prompt is not None and prompt.get("state") == "done" and prompt.get("completed"), "close-not-done")
    require(context["artifacts"] == prompt.get("artifacts", []), "context-artifacts-mismatch")
    if profile.version in ("3", "4"):
        identity = profile.kernel.start_identity(repo, run, run_path)
        require(context["start_identity"] == identity, "context-start-identity-mismatch")
        start_blob = cr.git(repo, "show", identity["commit"] + ":" + identity["run"]["path"])
        require(start_blob.returncode == 0 and cr.sha256_bytes(start_blob.stdout) == identity["run"]["sha256"] and
                cr.git(repo, "merge-base", "--is-ancestor", identity["commit"], proof_commit).returncode == 0,
                "context-start-not-at-close")
        verdict = profile.kernel._evaluate_council_history(gate, run, run_path, repo,
            context["artifacts"], records, lambda: (registry, None), identity=identity)
    else:
        verdict = profile.kernel._evaluate_council_history(gate, run, run_path, repo,
            context["artifacts"], records, lambda: (registry, None))
    require(verdict.verdict == "pass", "historical-gate-" + verdict.verdict)
    require(verdict.deciding_record == binding["deciding_record"]["path"], "deciding-record-mismatch")
    deciding = json.loads(committed_bytes(repo, binding["deciding_record"]))
    if profile.version in ("3", "4"):
        retained = _policy_call(profile, "deciding_subject", repo, run_path, deciding, binding[subject_key])
        require(retained == binding["retained_subject"], "deciding-revision-missing")
        return ValidatedBinding(binding, declaration, run, book, proof_commit)
    require(deciding.get("record_type") == "council-record" and deciding.get("outcome") == "ran",
            "deciding-council-required")
    subjects = [s for s in deciding["subjects"] if s["path"] == revision_path and s["sha256"] == revision_sha256]
    require(not migration or len(subjects) == 1, "deciding-batch-identity-refused")
    subject = next(iter(subjects), None)
    retained_path = subject.get("retained_copy") if subject is not None else None
    require(subject is not None and retained_path == binding["retained_subject"]["path"] and
            binding["retained_subject"]["sha256"] == revision_sha256, "deciding-revision-missing")
    return ValidatedBinding(binding, declaration, run, book, proof_commit)


def _historical_worker_env():
    """Filter credentials and inherited runtime before entering the frozen image."""
    env = cr._git_env()
    for name in list(env):
        if name.startswith(("GIT_", "PYTHON")):
            env.pop(name)
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_NO_REPLACE_OBJECTS": "1"})
    return env


def _historical_worker(repo, run_path, binding, *, migration):
    """Send only an internally selected committed binding to vendored replay code."""
    import council_commit
    subject = binding["batch" if migration else "revision"]
    request = {"repo": str(repo), "run_path": str(Path(run_path).resolve()),
               "binding": binding, "migration": migration}
    try:
        with tempfile.TemporaryDirectory(prefix="crux-historical-worker-") as empty_home:
            env = _historical_worker_env()
            # Current read helpers discover credential names through crux_env.
            # The worker must not load the owner's credential file itself.
            env["CRUX_HOME"] = empty_home
            child = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).with_name("council_historical_worker.py"))],
                input=json.dumps(request, sort_keys=True).encode(), capture_output=True,
                timeout=120, env=env)
        # Scan whole diagnostics, but never surface untrusted child text.
        for output in (child.stdout, child.stderr):
            require(len(output) <= 2_000_000 and
                    council_commit.scan_output(output) == output.decode("utf-8", errors="replace"),
                    "historical-worker-output-refused")
        result = json.loads(child.stdout)
        if child.returncode:
            require(isinstance(result, dict) and set(result) == {"refusal"} and
                    isinstance(result["refusal"], str) and
                    re.fullmatch(r"[a-z0-9-]+", result["refusal"]), "historical-worker-refused")
            raise Refused(result["refusal"])
        require(set(result) == {"binding", "slot", "run", "book", "proof_commit"} and
                result["binding"] == binding and
                result["binding"]["batch" if migration else "revision"] == subject,
                "historical-worker-proof-refused")
        return ValidatedBinding(**result)
    except Refused:
        raise
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, KeyError):
        raise Refused("historical-worker-unavailable") from None


def validate_implementation_binding(repo_root, run_path, *, slot, revision_path, revision_sha256,
                                    historical_revision=False) -> ValidatedBinding:
    """Require successful historical proof AND currently live constraints for new work."""
    try:
        proof = _validate_binding(repo_root, run_path, slot=slot, revision_path=revision_path,
                                  revision_sha256=revision_sha256, historical_revision=historical_revision)
        live_constraints(Path(repo_root).resolve(), proof.slot["constraint_refs"])
        return proof
    except Refused:
        raise
    except (cr.RecordError, bionic_config.BionicConfigError, OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
        raise Refused("approval-evidence-invalid") from None


def validate_historical_implementation_binding(repo_root, run_path, *, slot, revision_path,
                                               revision_sha256) -> ValidatedBinding:
    """Read exact retained proof without implying eligibility for a new close/result.

    This separate read consumer accepts no registry, context or eligibility override.
    It always selects the exact historical revision and runs the full shared policy.
    """
    try:
        return _validate_binding(repo_root, run_path, slot=slot, revision_path=revision_path,
                                 revision_sha256=revision_sha256, historical_revision=True)
    except Refused:
        raise
    except (cr.RecordError, bionic_config.BionicConfigError, OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
        raise Refused("approval-evidence-invalid") from None


def validate_historical_migration_binding(repo_root, run_path, *, slot, batch_path,
                                          batch_sha256) -> ValidatedBinding:
    """Prove the separately bound batch close through the complete shared history."""
    try:
        return _validate_binding(repo_root, run_path, slot=slot, revision_path=batch_path,
            revision_sha256=batch_sha256, historical_revision=True, migration=True)
    except Refused:
        raise
    except (cr.RecordError, bionic_config.BionicConfigError, OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
        raise Refused("approval-evidence-invalid") from None
