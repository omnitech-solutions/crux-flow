"""Admission combines proved reservations with factual source evidence.

The low-level evidence and holding helpers remain independent. Contexts are private,
operation-scoped snapshots; public callers select only the containing project root.
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import observation_evidence as oe
from admission_source_io import SourceIO, SourceIOExcluded, SourceIORefusal
from retained_evidence import holding_root_for_path, _layout_directory
from untrusted import redact


class AdmissionRefusal(ValueError):
    """A bounded refusal before observation or recovery mutation."""


class _AdmissionIO(SourceIO):
    """Bind canonical selection before admitting any consumed source input."""

    def __init__(self, root):
        self._fd = None
        selection = self._selection = {}
        layout_io = self._layout_io = SourceIO(root)
        def holding_check(path):
            tree = selection.get("tree")
            holding = holding_root_for_path(tree, path) if tree is not None else None
            if holding is not None:
                _layout_directory(tree, holding, _source_io=layout_io)
                raise SourceIOExcluded()
        try:
            super().__init__(root, _traversal_check=holding_check)
        except BaseException:
            layout_io.close()
            raise

    def close(self):
        super().close()
        if getattr(self, "_layout_io", None) is not None:
            self._layout_io.close()

    def revalidate(self):
        self._layout_io.revalidate()
        super().revalidate()
        self._layout_io.revalidate()

    def bind_tree(self, tree):
        self._selection["tree"] = tree
        self.revalidate()


def _summary_driver():
    spec = importlib.util.spec_from_file_location("_admission_summary_reader",
        Path(__file__).with_name("summarize-adrs.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _unheld(tree, path):
    """Exclude retained inputs before a canonical corpus reader traverses them."""
    if holding_root_for_path(tree, path) is not None:
        raise AdmissionRefusal("admission-retained-evidence-refused")


def _corpus_paths(root, tree, observations, source_io):
    import summaries_projection as sp
    import bionic_config
    adrs = tree / "adrs"
    paths = [root / name for name in (bionic_config.BIONIC_CONFIG_FILENAME, bionic_config.CONFIG_FILENAME)]
    paths += [tree / "manifest.yml", sp.reviews_path(adrs),
              adrs / "doctrine" / sp.RECONCILIATIONS_FILENAME, adrs / "summaries/_meta.json"]
    directories = [adrs, adrs / "archive", adrs / "summaries", adrs / "doctrine"]
    if observations is not None:
        directories.append(observations)
    for directory in directories:
        _unheld(tree, directory)
        if source_io.kind(directory) not in (None, "directory"):
            raise AdmissionRefusal("admission-corpus-directory-refused")
    paths += sp.adr_paths(adrs, _source_io=source_io) + sp.adr_paths(adrs / "archive", _source_io=source_io)
    if observations is not None:
        paths += sp.observation_paths(observations, _source_io=source_io)
    present = []
    for path in paths:
        _unheld(tree, path)
        kind = source_io.kind(path)
        if kind is None:
            continue
        if kind != "file":
            raise AdmissionRefusal("admission-corpus-leaf-refused")
        present.append(path)
    return sorted(set(present))


def _fingerprints(root, paths, source_io):
    result = {}
    for path in paths:
        if root not in path.parents:
            raise AdmissionRefusal("admission-input-containment-refused")
        result[path.relative_to(root).as_posix()] = hashlib.sha256(source_io.read_bytes(path)).hexdigest()
    return result


def _selected_tree(root, docs_dir, source_io):
    import summaries_projection as sp
    import bionic_config
    if docs_dir is None:
        return source_io.resolve(sp.resolve_tree(root, _source_io=source_io))
    bionic_config._validate_docs_dir_text(docs_dir)
    try:
        tree = source_io.resolve(root / docs_dir)
    except SourceIORefusal as exc:
        if exc.code != "admission-source-io-containment-refused":
            raise
        raise bionic_config.BionicConfigError(
            f"docs_dir {redact(docs_dir)} is not contained under the project root") from None
    if tree == root or tree.relative_to(root).parts[0].casefold() in bionic_config.DENYLISTED_FIRST_SEGMENTS:
        raise AdmissionRefusal("admission-selected-tree-refused")
    return tree


def _load_context(repo_root: Path, *, docs_dir: str | None = None) -> dict:
    """Contain canonical parser errors without reproducing source bytes."""
    import yaml
    try:
        return _context_inputs(repo_root, docs_dir=docs_dir)
    except (yaml.YAMLError, AttributeError, TypeError, KeyError):
        raise AdmissionRefusal("admission-corpus-parse-refused") from None


def _context_inputs(repo_root: Path, *, docs_dir: str | None = None) -> dict:
    """Combine canonical readers without the below-S9 projection output guard."""
    root = Path(repo_root).resolve()
    source_io = _AdmissionIO(root)
    try:
        return _loaded_context_inputs(root, docs_dir, source_io)
    except BaseException:
        source_io.close()
        raise


def _load_layout(repo_root: Path, *, docs_dir: str | None = None) -> dict:
    """Select the tree through guarded canonical readers before caller reads."""
    import yaml
    source_io = _AdmissionIO(Path(repo_root).resolve())
    try:
        return _layout_inputs(source_io.root, docs_dir, source_io)
    except (yaml.YAMLError, AttributeError, TypeError, KeyError):
        source_io.close()
        raise AdmissionRefusal("admission-corpus-parse-refused") from None
    except BaseException:
        source_io.close()
        raise


def _context_for_source_io(root, *, docs_dir=None, _source_io):
    """Reuse the internally constructed admission operation in canonical readers."""
    if not isinstance(_source_io, _AdmissionIO) or Path(root).resolve() != _source_io.root:
        raise AdmissionRefusal("admission-operation-context-refused")
    return _loaded_context_inputs(_source_io.root, docs_dir, _source_io)


def _loaded_context_inputs(root, docs_dir, source_io):
    return _complete_context(_layout_inputs(root, docs_dir, source_io))


def _layout_inputs(root, docs_dir, source_io):
    import summaries_projection as sp
    import implementation_migration as migration
    import bionic_config
    tree = _selected_tree(root, docs_dir, source_io)
    source_io.bind_tree(tree)
    for name in (bionic_config.BIONIC_CONFIG_FILENAME, bionic_config.CONFIG_FILENAME):
        if source_io.kind(root / name) not in (None, "file"):
            raise AdmissionRefusal("admission-config-shape-refused")
    manifest_path = tree / "manifest.yml"
    _unheld(tree, manifest_path)
    manifest_kind = source_io.kind(manifest_path)
    if manifest_kind not in (None, "file"):
        raise AdmissionRefusal("admission-manifest-shape-refused")
    manifest = migration._yaml(source_io.read_text(manifest_path)) if manifest_kind else {}
    if not isinstance(manifest, dict):
        raise AdmissionRefusal("admission-manifest-shape-refused")
    observations = None
    if "observations" in (manifest.get("concerns_enabled") or []):
        _unheld(tree, tree / "observations")
        observations = sp.observations_root(tree, _source_io=source_io)
    return dict(root=root, tree=tree, manifest=manifest, observations=observations,
                docs_dir=docs_dir, source_io=source_io)


def _complete_context(layout):
    """Load admission facts on the operation that already read caller inputs."""
    import summaries_projection as sp
    import implementation_migration as migration
    import yaml
    try:
        return _complete_context_inputs(layout, sp, migration)
    except (yaml.YAMLError, AttributeError, TypeError, KeyError):
        raise AdmissionRefusal("admission-corpus-parse-refused") from None


def _complete_context_inputs(layout, sp, migration):
    root, tree, source_io = layout["root"], layout["tree"], layout["source_io"]
    observations, manifest, docs_dir = layout["observations"], layout["manifest"], layout["docs_dir"]
    corpus = _fingerprints(root, _corpus_paths(root, tree, observations, source_io), source_io)
    facts = migration.admission_authority_view(root, docs_dir=docs_dir)
    declared = sp.declared_input_domain(tree / "adrs/summaries", _source_io=source_io)
    if isinstance(declared, list) and "implementation-migration" in declared and facts["state"] != "published":
        raise AdmissionRefusal("admission-declared-source-unavailable")
    records = sp.collect_records(tree / "adrs", governs_from=sp.governs_from(manifest), observations=observations, _source_io=source_io)
    reviews = sp.read_reviews(tree / "adrs", _source_io=source_io)
    removed = sp.removed_handles(reviews)
    aliases = sp.collect_observation_alias_rows(observations, records, _source_io=source_io)
    overlay = sp._summary_authority_records(records, facts, removed, aliases)
    _, locator = _summary_driver()._historical_summary_clauses(root, facts)
    spans = []
    fingerprints = {ref["path"]: ref["sha256"] for ref in facts["dependency_fingerprints"]}
    if locator is not None:
        batch, raw = migration._load_batch(root, locator["batch"]["path"])
        if hashlib.sha256(raw).hexdigest() != locator["batch"]["sha256"]:
            raise AdmissionRefusal("admission-proved-batch-changed")
        for entry in batch["entries"]:
            span = migration.resolve_clause_span(root, entry)
            span["disposition"] = entry["disposition"]
            spans.append(span)
            fingerprints[span["path"]] = span["source_sha256"]
        latest = facts["publications"][-1]
        fingerprints[latest["path"]] = latest["sha256"]
    proof_fingerprints = dict(fingerprints)
    for path, digest in corpus.items():
        if path in fingerprints and fingerprints[path] != digest:
            raise AdmissionRefusal("admission-input-changed")
        fingerprints[path] = digest
    migration._unchanged_inputs(root, [dict(path=p, sha256=d) for p, d in proof_fingerprints.items()])
    return dict(root=root, tree=tree, observations=observations, spans=spans,
        reservations=overlay, removed=removed, aliases=aliases, corpus=corpus, fingerprints=fingerprints,
        proof_fingerprints=proof_fingerprints, docs_dir=docs_dir, source_io=source_io)


def _revalidate(context: dict) -> None:
    """Reprove authority and detect corpus membership drift before the first write."""
    import implementation_migration as migration
    context["source_io"].revalidate()
    fresh = _load_context(context["root"], docs_dir=context["docs_dir"])
    try:
        if any(fresh[key] != context[key] for key in ("corpus", "reservations", "spans", "removed", "aliases")):
            raise AdmissionRefusal("admission-input-changed")
    finally:
        fresh["source_io"].close()
    actual = _fingerprints(context["root"], [context["root"] / p for p in context["fingerprints"]], context["source_io"])
    if actual != context["fingerprints"]:
        raise AdmissionRefusal("admission-input-changed")
    migration._unchanged_inputs(context["root"],
        [dict(path=p, sha256=d) for p, d in context["proof_fingerprints"].items()])


def _slug_problems(reservations: dict, slug: str | None, *, own_handle=None, peer=None, removed=None) -> list[str]:
    """One collision policy; an exemption belongs to one exact live owner only."""
    if slug is None:
        return []
    if peer is not None:
        return ["admission-peer-slug-collision"]
    if slug in reservations.get("retired_slugs", {}):
        return ["admission-retired-slug-reserved"]
    if slug in reservations.get("historical_slugs", {}):
        return ["admission-historical-slug-reserved"]
    if slug in {handle.split("/", 1)[1] for handle in (removed or {})}:
        return ["admission-removed-slug-reserved"]
    owner = reservations.get("slugs", {}).get(slug)
    return ["admission-live-slug-reserved"] if owner is not None and owner != own_handle else []


def _claims_reasoning_record(text: str) -> bool:
    """Probe YAML events without constructing an arbitrary source document.

    Only a root discriminator (including a scalar alias or merged mapping)
    claims a record. A claim invokes the strict canonical reader afterward.
    """
    import json
    import yaml
    import implementation_decisions as decisions
    import implementation_migration as migration
    kinds = decisions.KINDS | {
        json.loads(migration.SCHEMA.read_text())["properties"]["record_type"]["const"],
        migration.PUBLICATION_RECORD_TYPE,
    }
    stack, anchors = [], {}

    def consume(value, claimed=False):
        if not stack:
            return False
        parent = stack[-1]
        if parent["mapping"]:
            if parent["key_pending"]:
                parent["key"] = value
                parent["key_pending"] = False
                return False
            claim = (parent["key"] == "record_type" and isinstance(value, str) and value in kinds) or (
                parent["key"] == "<<" and claimed)
            parent["claimed"] |= claim
            parent["key_pending"] = True
            return claim and len(stack) == 1
        parent["claimed"] |= claimed
        return False

    try:
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.DocumentStartEvent):
                anchors.clear()
            elif isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                stack.append(dict(mapping=isinstance(event, yaml.MappingStartEvent),
                    key_pending=True, key=None, claimed=False, anchor=event.anchor))
            elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                frame = stack.pop()
                if frame["anchor"]:
                    anchors[frame["anchor"]] = (None, frame["claimed"])
                if consume(None, frame["claimed"]):
                    return True
            elif isinstance(event, yaml.ScalarEvent):
                if event.anchor:
                    anchors[event.anchor] = (event.value, False)
                if consume(event.value):
                    return True
            elif isinstance(event, yaml.AliasEvent):
                if consume(*anchors.get(event.anchor, (None, False))):
                    return True
    except yaml.YAMLError:
        # Ordinary code need not parse as YAML. Explicit record lanes stay strict.
        pass
    return False


def _evidence_path_resolves(root, path, source_io):
    """Preserve ordinary missing/escaping evidence findings without suppressing policy."""
    try:
        return oe.evidence_path_resolves(root, path, _source_io=source_io)
    except SourceIORefusal as exc:
        if exc.code not in {"admission-source-io-containment-refused", "admission-source-io-nonresolution-refused"}:
            raise
        return False


def _source_problems(context: dict, evidence: list[str], *, draft=False) -> list[str]:
    """Classify source kind and proved clause intersections without approving claims."""
    import adr_frontmatter
    import implementation_migration as migration
    problems = []
    if not isinstance(evidence, list) or not evidence:
        return [] if draft else ["admission-evidence-required"]
    for item in evidence:
        parsed = oe.parse_evidence(item)
        if parsed is None:
            if not draft:
                problems.append("admission-evidence-grammar-refused")
            continue
        given, start, end = parsed
        try:
            path = context["source_io"].resolve(context["root"] / given)
            kind = context["source_io"].kind(context["root"] / given)
        except SourceIOExcluded:
            problems.append("admission-retained-evidence-refused")
            continue
        except SourceIORefusal as error:
            if error.code not in {"admission-source-io-containment-refused", "admission-source-io-nonresolution-refused"}:
                raise
            path, kind = None, None
        if path is None or kind != "file":
            if not draft:
                problems.append("admission-evidence-containment-refused")
            continue
        raw = context["source_io"].read_bytes(context["root"] / given)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise SourceIORefusal() from None
        rel = path.relative_to(context["root"]).as_posix()
        digest = hashlib.sha256(raw).hexdigest()
        previous = context["fingerprints"].get(rel)
        if previous is not None and previous != digest:
            raise AdmissionRefusal("admission-source-changed")
        context["fingerprints"][rel] = digest
        block = adr_frontmatter.frontmatter_block(text)
        doc = migration._yaml(block) if block is not None else (
            migration._yaml(text) if path.suffix.lower() in {".json", ".yaml", ".yml"}
            or text.lstrip().startswith("{") or _claims_reasoning_record(text) else None)
        if isinstance(doc, dict) and "record_type" in doc:
            problems.append("admission-reasoning-source-refused")
            continue
        for span in context["spans"]:
            if context["root"] / span["path"] != path:
                continue
            if span["source_sha256"] != digest:
                raise AdmissionRefusal("admission-source-changed")
            if span["disposition"] in {"historical-implementation", "historical-authorization"} and start <= span["line_end"] and end >= span["line_start"]:
                problems.append("admission-historical-reasoning-refused")
                break
    return problems


def _own_handle(context: dict, source_record: Path | None, slug: str | None, *, expected_id=None, expected_anchor=None, expected_claim=None):
    """Derive the canonical own identity; private expectations come from a bound row."""
    if source_record is None:
        return None
    import summaries_projection as sp
    import adr_frontmatter
    import implementation_migration as migration
    from crux.arch.recover import claim_digest
    given = Path(source_record)
    given = given if given.is_absolute() else context["root"] / given
    try:
        path = context["source_io"].resolve(given)
    except SourceIOExcluded:
        raise AdmissionRefusal("admission-own-record-location-refused") from None
    if context["observations"] is None or path not in {context["source_io"].resolve(p) for p in sp.observation_paths(context["observations"], _source_io=context["source_io"])}:
        raise AdmissionRefusal("admission-own-record-location-refused")
    text = context["source_io"].read_text(given)
    block = adr_frontmatter.frontmatter_block(text)
    fm = migration._yaml(block) if block is not None else {}
    if not isinstance(fm, dict):
        raise AdmissionRefusal("admission-own-record-identity-refused")
    oid = fm.get("id")
    anchor = fm.get("anchor_id")
    if "record_type" in fm or not isinstance(oid, str) or not sp._OBS_ID_SHAPE_RE.fullmatch(oid) or not path.stem.startswith(oid + "-") or not isinstance(anchor, str) or len(anchor) != 16 or any(c not in "0123456789abcdef" for c in anchor):
        raise AdmissionRefusal("admission-own-record-identity-refused")
    if slug is None:
        return None
    handle = oid + "/" + slug
    row = next((r for r in context["reservations"]["records"] if r["handle"] == handle and r.get("source_kind") == "observation"), None)
    if row is None:
        return None
    claim = claim_digest(row["rule"], row["evidence"])
    own_entry = next((e for e in sp.governs_entries(fm) if e.get("handle") == handle), {})
    if row["anchor_id"] != anchor or claim != claim_digest(own_entry.get("rule"), fm.get("evidence")) or (expected_id is not None and oid != expected_id) or (expected_anchor is not None and anchor != expected_anchor) or (expected_claim is not None and claim != expected_claim):
        raise AdmissionRefusal("admission-own-record-claim-refused")
    return handle


def admission_problems(repo_root: Path, *, slug: str | None, evidence: list[str], source_record: Path | None = None, docs_dir: str | None = None) -> list[str]:
    """Load proved facts from this project; report source and reservation refusals."""
    try:
        context = _load_context(repo_root, docs_dir=docs_dir)
        try:
            return _source_problems(context, evidence) + _slug_problems(context["reservations"], slug,
                own_handle=_own_handle(context, source_record, slug), removed=context["removed"])
        finally:
            context["source_io"].close()
    except (ValueError, OSError, RuntimeError):
        return ["admission-context-or-source-refused"]


def recovery_source_problems(repo_root: Path, *, evidence: list[str], docs_dir: str | None = None) -> list[str]:
    """Recovery has the same source boundary and cannot supply authority overrides."""
    try:
        context = _load_context(repo_root, docs_dir=docs_dir)
        try:
            return _source_problems(context, evidence) if evidence else []
        finally:
            context["source_io"].close()
    except (ValueError, OSError, RuntimeError):
        return ["admission-context-or-source-refused"]
