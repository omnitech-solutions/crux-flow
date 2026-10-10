"""Publish one immutable migration witness, then refresh proved derived caches.

The witness is the authority boundary. Git commits remain the caller's act.
Private staging, loaded models and pending identities supply no approval.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing,contextmanager
import hashlib
import os
from pathlib import Path
import stat
import secrets

import bionic_config
import git_read_cache as _git_read_cache
import implementation_migration as migration
import retained_evidence
import summaries_projection as sp
from admission_source_io import SourceIO,SourceIORefusal,SourceIOExcluded


class _ApplicationIO(SourceIO):
    """Preserve the two MiB context bound; select four only for named outputs."""

    def __init__(self,root):
        self.docs_root=None;self._fd=None;self._layout_io=None
        self._layout_io=SourceIO(root)
        try:super().__init__(root,_traversal_check=self._holding_check)
        except BaseException:
            self._layout_io.close();raise
        self.outputs=set()

    def close(self):
        try:super().close()
        finally:
            if self._layout_io is not None:self._layout_io.close()

    def revalidate(self):
        # Main replay can add layout observations through its traversal callback.
        self._layout_io.revalidate()
        super().revalidate()
        self._layout_io.revalidate()

    def _holding_check(self,path):
        if self.docs_root is not None:
            holding=retained_evidence.holding_root_for_path(self.docs_root,path)
            if holding is not None:
                # The pure prefix decides pruning; canonical layout validation
                # remains separate and inspects no retained descendant.
                root_metadata=self._layout_io.metadata(self.docs_root)
                if root_metadata is not None and not stat.S_ISDIR(root_metadata["st_mode"]):
                    raise retained_evidence.RetainedEvidenceRefusal("retained-evidence-layout-refused")
                retained_evidence._layout_directory(self.docs_root,holding,_source_io=self._layout_io)
                raise SourceIOExcluded()

    def kind(self,path):
        try:return super().kind(path)
        except SourceIOExcluded:return None

    def glob(self,path,pattern):
        try:paths=super().glob(path,pattern)
        except SourceIOExcluded:return []
        result=[]
        for candidate in paths:
            # Probe aliases through the guarded traversal so an alias target
            # cannot enter a holding between classification and its open.
            try:super().kind(candidate)
            except SourceIOExcluded:continue
            result.append(candidate)
        return result

    def roster_metadata(self,path):
        try:return super().metadata(path)
        except SourceIOExcluded:return None

    def roster_paths(self,path):
        # Snapshot leaves carry link spelling, never target authority. Unlike
        # canonical source readers, this roster must not follow a final alias.
        try:return super().glob(path,"*")
        except SourceIOExcluded:return []

    def read_bytes(self,path,*,max_bytes=None):
        if max_bytes is None and Path(path) in self.outputs:max_bytes=4*1024*1024
        return super().read_bytes(path,max_bytes=max_bytes)


class _SourceContextCloseFailure(OSError):
    """Teardown failed; retain any completed operation report and body refusal."""

    code="migration-apply-context-close-unavailable"

    def __init__(self,body_problem=None):
        super().__init__(self.code)
        self.body_problem=body_problem


@contextmanager
def _source_context(root):
    try:
        source_io=_ApplicationIO(root);body_problem=None
        try:
            config=bionic_config.load_config(root,_source_io=source_io)
            source_io.docs_root=config.docs_root
            # Bootstrap read only selects the configured root. Its observations
            # must pass the installed holding check before admission or output.
            source_io.revalidate()
            tree=sp.resolve_tree(root,_source_io=source_io)
            source_io.outputs={tree/"adrs/summaries"/name for name in
                ("rule-table.md","resolver.json","implementation-map.md","_meta.json")}
            source_io.outputs.update(tree/"adrs/doctrine"/name for name in ("index.md","_meta.json"))
            source_io.outputs.add(Path(root)/"crux/catalog/rules.json")
            source_io.outputs.update(source_io.resolve(path) for path in tuple(source_io.outputs))
            yield source_io,config
        except BaseException as exc:
            body_problem=exc;raise
        finally:
            try:source_io.close()
            except OSError:raise _SourceContextCloseFailure(body_problem) from None
    except SourceIORefusal as exc:
        raise migration.Refused(exc.code) from None


class StagingCleanupIncomplete(OSError):
    """Private staging cleanup is incomplete; no unchanged-inventory claim is possible."""

    code = "migration-apply-staging-cleanup-incomplete"


def _require(condition, code):
    migration.ap.require(condition, code)


def _git_frame(root):
    values=[]
    for arguments in (("rev-parse", "HEAD"), ("ls-files", "-v", "--stage", "-z")):
        result=migration.cr.git(root,*arguments)
        _require(result.returncode==0,"migration-apply-history-unavailable")
        values.append(result.stdout)
    return tuple(values)


@dataclass(frozen=True)
class _Snapshot:
    git:tuple
    source_io:object
    footprint:object
    layout_footprint:object
    named:tuple

    @classmethod
    def capture(cls,root,*,_source_io,named=(),git=None):
        # Collectors select the domain. Freeze every successful, absent and
        # excluded observation; callers cannot supply a smaller source roster.
        frame=_git_frame(root) if git is None else git
        exact=[]
        for ref in sorted(named,key=lambda item:item["path"]):
            text,rel=migration._read(root,ref["path"])
            identity=_target_identity(Path(root)/rel)
            _require(identity is not None and identity[-1]==ref["sha256"]==
                migration._digest(text.encode("utf-8")),"migration-apply-named-input-changed")
            exact.append((rel,ref["sha256"],identity))
        _source_io.revalidate()
        _require(frame==_git_frame(root),"migration-apply-input-changed")
        return cls(frame,_source_io,_source_io._capture_footprint(),
            _source_io._layout_io._capture_footprint(),tuple(exact))

    def check(self,root,*,transitions=()):
        _require(_git_frame(root)==self.git,"migration-apply-input-changed")
        for rel,digest,identity in self.named:
            text,_=migration._read(root,rel)
            _require(_target_identity(Path(root)/rel)==identity and
                migration._digest(text.encode("utf-8"))==digest,"migration-apply-named-input-changed")
        try:
            receipt=self.source_io._validate_transitions(self.footprint,transitions=transitions)
            self.source_io._layout_io._replay_metadata_footprint(self.layout_footprint,receipt)
            self.source_io._replay_footprint(self.footprint,transitions=receipt)
            self.source_io._layout_io._replay_metadata_footprint(self.layout_footprint,receipt)
        except SourceIORefusal:
            raise migration.Refused("migration-apply-input-changed") from None
        _require(_git_frame(root)==self.git,"migration-apply-input-changed")


class _OwnedTransitions:
    """Permit exact validated writes without excusing immutable named inputs."""

    def __init__(self,snapshot):
        self.snapshot=snapshot;self.pairs=[];self.model_stages=set()
        self.immutable={snapshot.source_io.root/rel for rel,_,_ in snapshot.named}

    def capture(self,path,*,model_stage=False):
        _require(Path(path) not in self.immutable,"migration-apply-named-input-conflict")
        if model_stage:self.model_stages.add(Path(path))
        limit=4*1024*1024 if Path(path) in self.model_stages or Path(path) in self.snapshot.source_io.outputs else None
        return self.snapshot.source_io._capture_path_state(path,max_bytes=limit)

    def record(self,before,*,raw=None,identity=None,kind=None):
        after=self.capture(before.path)
        _require(after.kind==kind,"migration-apply-destination-changed")
        if raw is not None:_require(after.raw_bytes==raw,"migration-apply-destination-changed")
        if identity is not None:
            metadata=dict(after.metadata)
            _require((metadata["st_dev"],metadata["st_ino"])==identity,
                "migration-apply-destination-changed")
        self.pairs.append((before,after))
        return after

    def check(self,root):self.snapshot.check(root,transitions=tuple(self.pairs))


class _Parent:
    """Keep each ancestor descriptor open through one directory operation.

    Private staging is reserved to this operation. Leaf identity checks preserve
    observed replacements; they do not provide atomic conditional deletion.
    """

    def __init__(self,path,*,create=False,transitions=None):
        self.path=Path(path).absolute();self._chain=[];self._closed=False
        _require(".." not in self.path.parts,"migration-apply-destination-refused")
        descriptor=os.open(self.path.anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        self._chain.append((descriptor,None,None))
        try:
            current=Path(self.path.anchor)
            for component in self.path.parts[1:]:
                self.check()
                current=current/component
                if create:
                    try:present=os.stat(component,dir_fd=descriptor,follow_symlinks=False)
                    except FileNotFoundError:present=None
                    if present is None:
                        before=transitions.capture(current) if transitions is not None else None
                        if before is not None:_require(before.kind is None,"migration-apply-destination-changed")
                        os.mkdir(component,dir_fd=descriptor)
                        owned=os.stat(component,dir_fd=descriptor,follow_symlinks=False)
                        if transitions is not None:transitions.record(before,kind="directory",identity=(owned.st_dev,owned.st_ino))
                before=os.stat(component,dir_fd=descriptor,follow_symlinks=False)
                _require(stat.S_ISDIR(before.st_mode),"migration-apply-destination-refused")
                child=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=descriptor)
                self._chain.append((child,component,(before.st_dev,before.st_ino,before.st_mode)))
                descriptor=child;self.check()
        except BaseException:
            self.close();raise

    @property
    def descriptor(self):return self._chain[-1][0]

    def check(self):
        _require(not self._closed,"migration-apply-destination-refused")
        for index,(descriptor,name,identity) in enumerate(self._chain):
            if index==0:continue
            before=os.stat(name,dir_fd=self._chain[index-1][0],follow_symlinks=False)
            opened=os.fstat(descriptor)
            _require((before.st_dev,before.st_ino,before.st_mode)==identity==
                (opened.st_dev,opened.st_ino,opened.st_mode),"migration-apply-destination-changed")

    def close(self):
        if not self._closed:
            self._closed=True
            failure=None
            for descriptor,_,_ in reversed(self._chain):
                try:os.close(descriptor)
                except OSError as exc:failure=exc
            if failure is not None:raise failure

    def __enter__(self):return self
    def __exit__(self,*args):self.close()


@dataclass(frozen=True)
class _OwnedFile:
    path:Path
    device:int
    inode:int
    sha256:str
    descriptor:int
    parent:_Parent

    @classmethod
    def create(cls,parent,raw,*,transitions=None,model_stage=False):
        anchored=_Parent(parent);fd=None
        try:
            anchored.check()
            name=".migration-apply-"+secrets.token_hex(16)+".tmp"
            before=transitions.capture(anchored.path/name,model_stage=model_stage) if transitions is not None else None
            if before is not None:_require(before.kind is None,"migration-apply-staging-identity-refused")
            fd=os.open(name,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,
                dir_fd=anchored.descriptor)
            info=os.fstat(fd)
            # Register ownership before the first byte reaches the file.
            owned=cls(anchored.path/name,info.st_dev,info.st_ino,hashlib.sha256(raw).hexdigest(),fd,anchored)
        except BaseException:
            if fd is not None:
                os.close(fd);anchored.close()
                raise StagingCleanupIncomplete() from None
            anchored.close();raise
        try:
            with os.fdopen(os.dup(fd),"wb") as stream:
                if stream.write(raw)!=len(raw):raise OSError("incomplete staging stream")
                stream.flush();os.fsync(stream.fileno())
        except BaseException:
            # Partial stream bytes cannot be checked against the intended digest.
            # Retain them explicitly rather than guessing which bytes we own.
            os.close(fd);anchored.close()
            raise StagingCleanupIncomplete() from None
        try:
            owned.check()
            if transitions is not None:transitions.record(before,kind="file",raw=raw,identity=(owned.device,owned.inode))
            return owned
        except BaseException:
            try:owned.cleanup()
            except OSError:raise StagingCleanupIncomplete() from None
            raise

    def check(self):
        self.parent.check()
        info=os.stat(self.path.name,dir_fd=self.parent.descriptor,follow_symlinks=False)
        opened=os.fstat(self.descriptor)
        _require(stat.S_ISREG(info.st_mode) and (info.st_dev,info.st_ino)==(self.device,self.inode)
            and (opened.st_dev,opened.st_ino)==(self.device,self.inode),
            "migration-apply-staging-identity-refused")
        digest=hashlib.sha256();offset=0
        while chunk:=os.pread(self.descriptor,1024*1024,offset):
            digest.update(chunk);offset+=len(chunk)
        _require(digest.hexdigest()==self.sha256,"migration-apply-staging-identity-refused")

    def cleanup(self):
        try:
            try:self.parent.check()
            except (migration.Refused,FileNotFoundError):
                raise StagingCleanupIncomplete() from None
            info=os.stat(self.path.name,dir_fd=self.parent.descriptor,follow_symlinks=False)
            _require(stat.S_ISREG(info.st_mode) and (info.st_dev,info.st_ino)==(self.device,self.inode),
                "migration-apply-staging-identity-refused")
            self.check()
            os.unlink(self.path.name,dir_fd=self.parent.descriptor)
        except FileNotFoundError:pass
        except migration.Refused:raise StagingCleanupIncomplete() from None
        finally:
            try:os.close(self.descriptor)
            except OSError:pass
            self.parent.close()


def _publish_witness(staged,target,*,published=None,transitions=None):
    staged.check()
    with _Parent(target.parent) as parent:
        staged.check();parent.check()
        stage_before=transitions.capture(staged.path) if transitions is not None else None
        target_before=transitions.capture(target) if transitions is not None else None
        if target_before is not None:_require(target_before.kind is None,"migration-apply-publication-conflict")
        try:os.link(staged.path.name,target.name,src_dir_fd=staged.parent.descriptor,
            dst_dir_fd=parent.descriptor,follow_symlinks=False)
        except FileExistsError:raise migration.Refused("migration-apply-publication-conflict") from None
        if published is not None:published()
        if transitions is not None:
            transitions.record(stage_before,kind="file",raw=stage_before.raw_bytes,
                identity=(staged.device,staged.inode))
            transitions.record(target_before,kind="file",raw=stage_before.raw_bytes,
                identity=(staged.device,staged.inode))


def _replace_staged(staged,target,*,expected=None,written=None,transitions=None):
    with _Parent(target.parent,create=True,transitions=transitions) as parent:
        staged.check();parent.check()
        _require(_target_identity(target)==expected,"migration-apply-destination-changed")
        stage_before=transitions.capture(staged.path) if transitions is not None else None
        target_before=transitions.capture(target) if transitions is not None else None
        parent.check()
        os.replace(staged.path.name,target.name,src_dir_fd=staged.parent.descriptor,
            dst_dir_fd=parent.descriptor)
        if written is not None:written()
        if transitions is not None:
            transitions.record(stage_before,kind=None)
            transitions.record(target_before,kind="file",raw=stage_before.raw_bytes,
                identity=(staged.device,staged.inode))


def _select_close(root,batch_path,*,_source_io=None):
    """A selector discovers one canonical binding; only shared history proves it."""
    root=Path(root).resolve()
    if _source_io is None:
        with _source_context(root) as (source_io,_):
            result=_select_close(root,batch_path,_source_io=source_io)
            source_io.revalidate();return result
    batch,raw=migration._load_batch(root,batch_path)
    _,rel=migration._checked(root,batch_path)
    slot=batch["approval_slot"]; digest=migration._digest(raw)
    config=bionic_config.load_config(root,_source_io=_source_io); matches=[]
    runs=sp.runs_dir(root,_source_io=_source_io);pending=[runs]
    while pending:
        parent=pending.pop()
        if _source_io.kind(parent)!="directory":continue
        for path in _source_io.roster_paths(parent):
            metadata=_source_io.roster_metadata(path)
            if metadata is None:continue
            if stat.S_ISDIR(metadata["st_mode"]):pending.append(path);continue
            if path.name.startswith("run-RUN-") and path.name.endswith(".yaml") and _source_io.kind(path)=="file":
                text=_source_io.read_text(path);run_rel=path.relative_to(root).as_posix()
                doc=migration._yaml(text)
                _require(isinstance(doc,dict),"migration-apply-close-discovery-refused")
                entries=doc.get("migration_bindings",[])
                _require(isinstance(entries,list),"migration-apply-close-discovery-refused")
                for entry in entries:
                    if not isinstance(entry,dict):
                        _require(False,"migration-apply-close-discovery-refused")
                    subject=entry.get("batch")
                    if isinstance(subject,dict) and subject.get("path")==rel:
                        _require(subject==dict(path=rel,sha256=digest) and entry.get("slot")==slot,
                            "migration-apply-close-identity-refused")
                        matches.append((path,run_rel))
    _require(len(matches)==1,"migration-apply-close-ambiguous" if matches else "migration-apply-close-missing")
    path,run_rel=matches[0]
    proof=migration.ap.validate_historical_migration_binding(root,path,slot=slot,
        batch_path=rel,batch_sha256=digest)
    return proof,run_rel


def _driver(name):
    import importlib.util
    spec=importlib.util.spec_from_file_location("_migration_apply_"+name.replace("-","_"),
        Path(__file__).with_name(name+".py"))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def _locator(proof,run_rel,batch,*,repo_root=None):
    binding=proof.binding
    raw=migration._publication_locator_bytes(proof,run_rel)
    path=Path(binding["batch"]["path"]).with_name(batch["batch_id"]+".application.json").as_posix()
    digest=migration._digest(raw)
    if repo_root is not None:
        target,_=migration._checked(Path(repo_root).resolve(),path)
        identity=_target_identity(target)
        if identity is not None:digest=identity[-1]
    # This is only a byte identity claim. Shared discovery independently proves
    # existing history, and permits uncommitted bytes only in canonical format.
    pending=dict(path=path,sha256=digest,close_identity={k:binding[k] for k in
        ("batch","book_id","run_id","book_content_hash","slot","gate_prompt")})
    sp._summary_pending_publication(pending)
    return raw,pending


def _candidate_clauses(batch,facts):
    selected={row["source_identity"]:row for row in facts["source_refs"]}
    _require(len(selected)==len(facts["source_refs"]) and set(selected)=={e["source_identity"] for e in batch["entries"]},
        "migration-apply-selected-roster-refused")
    clauses=[]
    for entry in batch["entries"]:
        source=selected[entry["source_identity"]]
        _require(source["source_adr"]==entry["source_adr"] and source["clause_sha256"]==entry["clause"]["sha256"],
            "migration-apply-selected-identity-refused")
        if entry["disposition"]=="historical-implementation":
            clauses.append(dict(source_identity=entry["source_identity"],
                source_ref={k:source[k] for k in ("path","source_adr","clause_sha256")},
                destination=entry["historical_destination"],replacements=entry["replacement_handles"]))
    return sorted(clauses,key=lambda row:row["source_identity"])


def _declared_sources(root,manifest,folder,known,*,_source_io):
    declared=sp.declared_input_domain(folder,_source_io=_source_io)
    _require(declared is None or isinstance(declared,list) and all(isinstance(x,str) for x in declared)
        and set(declared)<=set(known),"migration-apply-declared-input-refused")
    _require(not declared or "observations" not in declared or
        sp.observations_source_problem(root,manifest,_source_io=_source_io) is None,"migration-apply-declared-source-unavailable")


def _compile_raw_models(root,facts,pending,prior,batch,*,_source_io):
    """Read canonical raw inputs and compile every policy/model before publication."""
    import json
    import doctrine_projection as dp
    summary=_driver("summarize-adrs");catalog=_driver("generate-rules-catalog")
    root=Path(root).resolve();manifest=sp.read_manifest(root,_source_io=_source_io)
    _require(isinstance(manifest,dict),"migration-apply-manifest-refused")
    tree,paths=summary._summary_destinations(root,_source_io=_source_io)
    dp._doctrine_destinations(root,_source_io=_source_io)
    adrs=tree/"adrs";concerns=manifest.get("concerns_enabled") or []
    _require(_source_io.kind(adrs)=="directory" or "adrs" not in concerns and "observations" in concerns,
        "migration-apply-source-unavailable")
    _declared_sources(root,manifest,adrs/"summaries",sp.INPUT_DOMAIN_KNOWN,_source_io=_source_io)
    _declared_sources(root,manifest,adrs/"doctrine",("adrs","invariants","reconciliations","run-bindings",
        "observations","survey-receipts","implementation-migration"),_source_io=_source_io)
    gf=sp.governs_from(manifest);observations=sp.observations_source(root,manifest,_source_io=_source_io)
    records=sp.collect_records(adrs,governs_from=gf,observations=observations,_source_io=_source_io)
    reviews=sp.read_reviews(adrs,_source_io=_source_io)
    aliases=sp.collect_observation_alias_rows(observations,records,_source_io=_source_io)
    history=[{k:row[k] for k in ("path","sha256","publication_commit")} for row in prior]
    metadata=dict(adr_frontmatter_sha256=sp.adr_frontmatter_sha256(adrs,_source_io=_source_io),
        backfill_reviews_sha256=sp.backfill_reviews_sha256(adrs,_source_io=_source_io),
        input_domain=["adrs","backfill-reviews"]+(["observations","survey-receipts"] if observations is not None else []),
        observations_sha256=sp.observations_sha256(observations,_source_io=_source_io) if observations is not None else None,
        survey_receipts_sha256=sp.survey_receipts_sha256(observations,_source_io=_source_io) if observations is not None else None,
        schema="6",tool="summarize-adrs.py")
    summary_loaded=dict(tree=tree,paths=paths,records=records,reviews=reviews,aliases=aliases,governs_from=gf,
        implementation_map=sp.build_implementation_map(adrs,sp.runs_dir(root,_source_io=_source_io),repo_root=root,_source_io=_source_io),metadata=metadata,
        archived_handles=sorted(sp.archived_handles(adrs,_source_io=_source_io)),
        signed_handles=sorted(sp.signed_reconciliation_handles(adrs,_source_io=_source_io)),
        historical_clauses=_candidate_clauses(batch,facts),publication_history=history)
    summary_plan=sp._plan_summary_outputs(summary_loaded,facts,pending)
    removed=sp.removed_handles(reviews)
    overlay=sp._summary_authority_records(records,facts,removed,aliases)
    invariants=dp.read_invariants(root,_source_io=_source_io)
    ledger_path=dp.reconciliations_path(root,_source_io=_source_io)
    ledger_text=_source_io.read_text(ledger_path) if _source_io.kind(ledger_path) is not None else None
    reconciliations=dp.parse_reconciliations(ledger_text) if ledger_text is not None else []
    bindings={source:{key:sorted(value) for key,value in binding.items()} for source,binding
        in sp.read_run_bindings(sp.runs_dir(root,_source_io=_source_io),repo_root=root,_source_io=_source_io).items()}
    evidence=[dict(handle=row["handle"],evidence=ev,resolves=dp.evidence_resolves(ev,root,_source_io=_source_io))
        for row in sp.live_records(overlay["records"]) if row.get("source_kind")=="observation"
        for ev in sorted(set(row.get("evidence",[])))]
    digests=dp.input_digests(root,records,invariants,gf,observations,_source_io=_source_io)
    digests.update(schema="5",input_domain=["adrs","invariants","reconciliations","run-bindings"]+
        (["observations","survey-receipts"] if observations is not None else []),
        run_bindings_sha256=hashlib.sha256(json.dumps(bindings,sort_keys=True,separators=(",",":")).encode()).hexdigest())
    doctrine_loaded=dict(tree=tree,paths={name:tree/"adrs/doctrine"/name for name in ("index.md","_meta.json")},
        records=records,invariants=[{**inv,"path":str(inv["path"]) if inv["path"] is not None else None} for inv in invariants],
        reconciliations=reconciliations,bindings=bindings,governs_from=gf,tree_name=sp.tree_name(root,_source_io=_source_io),
        evidence_facts=evidence,exempt_entries=sp.governs_exempt_entries(manifest),digests=digests,
        aliases=aliases,removed=removed,ledger=dict(path=ledger_path.relative_to(root).as_posix(),text=ledger_text),
        archived_handles=sorted(sp.archived_handles(adrs,_source_io=_source_io)),publication_history=history)
    doctrine_plan=dp._plan_doctrine_outputs(doctrine_loaded,facts,pending)
    plugin=root/"crux";target=plugin/"catalog/rules.json"
    entry=_source_io.metadata(target)
    _require(_source_io.kind(plugin)=="directory" and _source_io.resolve(target.parent).is_relative_to(root)
        and (entry is None or stat.S_ISREG(entry["st_mode"])),
        "migration-apply-catalog-destination-refused")
    catalog_records=sp.collect_records(adrs,governs_from=None,observations=observations or tree/"observations",_source_io=_source_io)
    catalog_aliases=sp.collect_observation_alias_rows(observations or tree/"observations",catalog_records,_source_io=_source_io)
    cited,refusals=catalog.cited_slug_paths(plugin,_source_io=_source_io)
    catalog_loaded=dict(root=root,target=target,records=catalog_records,reviews=reviews,
        aliases=catalog_aliases,cited=cited,refusals=refusals)
    catalog_plan=catalog._plan_rules_catalog_outputs(catalog_loaded,facts,pending)
    plans=(summary_plan,doctrine_plan,catalog_plan)
    outputs={path for plan in plans for path,_ in plan}
    _require(len(outputs)==7,"migration-apply-output-roster-refused")
    for path in outputs:
        migration._checked(root,path)
        entry=_source_io.metadata(path)
        _require((entry is None or stat.S_ISREG(entry["st_mode"])) and
            _source_io.metadata(path.with_suffix(path.suffix+".tmp")) is None,
            "migration-apply-destination-refused")
    return plans


def _compile_models(root,facts,pending,prior,batch,*,_source_io=None):
    import yaml
    if _source_io is None:
        with _source_context(root) as (source_io,_):
            result=_compile_models(root,facts,pending,prior,batch,_source_io=source_io)
            source_io.revalidate();return result
    try:return _compile_raw_models(root,facts,pending,prior,batch,_source_io=_source_io)
    except (yaml.YAMLError,UnicodeError,RecursionError):
        raise migration.Refused("migration-apply-model-source-invalid") from None


def _materialize(plans,pending,actual):
    import doctrine_projection as dp
    ref=sp._committed_publication_ref(pending,actual)
    outputs=sp._materialize_summary_outputs(plans[0],ref)
    outputs.update(dp._materialize_doctrine_outputs(plans[1],ref))
    outputs.update(_driver("generate-rules-catalog")._materialize_rules_catalog_outputs(plans[2],ref))
    _require(len(outputs)==7 and all(isinstance(raw,bytes) for raw in outputs.values()),
        "migration-apply-materialization-refused")
    return outputs


def _prior_chain(root,batch,proof,publications,selected):
    chain=[];parent_proof=None
    if publications:chain,parent_proof=migration._publication_chain(root,publications)
    if selected is not None:
        _require(chain and chain[-1]["path"]==selected["path"] and chain[-1]["sha256"]==selected["sha256"],
            "migration-apply-nonterminal-publication-refused")
        _require(parent_proof.binding==proof.binding,"migration-apply-publication-identity-refused")
        return chain[:-1]
    recovery=batch.get("recovery_from")
    if recovery is None:
        _require(not chain,"migration-competing-publications-refused")
    else:
        _require(bool(chain),"migration-recovery-link-refused")
        parent=chain[-1]
        parent_batch=migration.load_batch(root,parent_proof.binding["batch"]["path"])
        migration._validate_recovery_link(root,parent_publication=parent,parent_proof=parent_proof,
            parent_batch=parent_batch,batch=batch,proof=proof)
    return chain


def _target_identity(path):
    path=Path(path)
    try:
        with _Parent(path.parent) as parent:
            parent.check()
            try:info=os.stat(path.name,dir_fd=parent.descriptor,follow_symlinks=False)
            except FileNotFoundError:return None
            _require(stat.S_ISREG(info.st_mode),"migration-apply-destination-refused")
            child=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parent.descriptor)
            try:
                opened=os.fstat(child)
                _require(stat.S_ISREG(opened.st_mode) and (opened.st_dev,opened.st_ino)==(info.st_dev,info.st_ino),
                    "migration-apply-destination-changed")
                digest=hashlib.sha256()
                while chunk:=os.read(child,1024*1024):digest.update(chunk)
                after=os.fstat(child)
                parent.check()
                final=os.stat(path.name,dir_fd=parent.descriptor,follow_symlinks=False)
                _require((after.st_size,after.st_mtime_ns,after.st_ctime_ns)==
                    (opened.st_size,opened.st_mtime_ns,opened.st_ctime_ns) and
                    (final.st_dev,final.st_ino,final.st_mode,final.st_size,final.st_mtime_ns,final.st_ctime_ns)==
                    (after.st_dev,after.st_ino,after.st_mode,after.st_size,after.st_mtime_ns,after.st_ctime_ns),
                    "migration-apply-destination-changed")
                return (info.st_dev,info.st_ino,stat.S_IMODE(info.st_mode),
                    info.st_size,info.st_mtime_ns,info.st_ctime_ns,digest.hexdigest())
            finally:os.close(child)
    except FileNotFoundError:return None


def _report(state,pending,*,actual=None,written=(),limit=None,failure_class=None):
    result=dict(state=state,authority="none",publication=actual or pending,
        written=list(written))
    if limit is not None:result["limit"]=limit
    if failure_class is not None:result["failure_class"]=failure_class
    return result


def _failure_class(exc):
    return "capability" if isinstance(exc,(OSError,bionic_config.BionicConfigError)) else "validation"


def _reprove(root,batch_path,pending,raw):
    proof,run_rel=_select_close(root,batch_path)
    batch=migration.load_batch(root,batch_path)
    fresh_raw,fresh_pending=_locator(proof,run_rel,batch,repo_root=root)
    _require((fresh_raw,fresh_pending)==(raw,pending),"migration-apply-close-changed")
    publications,selected=migration._discover_apply_publications(root,pending=pending)
    prior=_prior_chain(root,batch,proof,publications,selected)
    migration._validated_inputs(root,proof,_publication=selected)
    return selected,prior


def _named_inputs(root,proof,prior,facts,publications=()):
    """Snapshot exact artifacts already admitted by their owning history validator."""
    import json
    references={}
    proofs=[proof]+[migration._publication_proof(root,row) for row in prior]
    for proved in proofs:
        binding=proved.binding
        selected=[binding[key] for key in ("batch","deciding_record","retained_subject","context")]
        # The owning evaluator already validated this closed context and its records.
        context=json.loads(migration.ap.committed_bytes(root,binding["context"]))
        selected+=context["records"]+[context["registry"],{key:context["contract"][key] for key in ("path","sha256")}]
        for ref in selected:
            _require(ref["path"] not in references or references[ref["path"]]==ref["sha256"],
                "migration-apply-named-input-conflict")
            references[ref["path"]]=ref["sha256"]
    for ref in facts["dependency_fingerprints"]:
        _require(ref["path"] not in references or references[ref["path"]]==ref["sha256"],
            "migration-apply-named-input-conflict")
        references[ref["path"]]=ref["sha256"]
    for ref in publications:
        _require(ref["path"] not in references or references[ref["path"]]==ref["sha256"],
            "migration-apply-named-input-conflict")
        references[ref["path"]]=ref["sha256"]
    return [dict(path=path,sha256=digest) for path,digest in sorted(references.items())]


def _directory_sync(path):
    with _Parent(path) as parent:
        parent.check();os.fsync(parent.descriptor)


def _refresh(root,batch_path,plans,pending,raw,actual,snapshot,*,_source_io):
    """Individually atomic cache writes follow fresh committed authority proof."""
    outputs=_materialize(plans,pending,actual)
    targets={path:_target_identity(path) for path in outputs}
    snapshot.check(root)
    transitions=_OwnedTransitions(snapshot)
    staged=[];written=[]
    try:
        for path,body in sorted(outputs.items()):
            transitions.check(root)
            staged.append((path,_OwnedFile.create((root/pending["path"]).parent,body,
                transitions=transitions,model_stage=True)))
        transitions.check(root)
        current,_=_reprove(root,batch_path,pending,raw)
        _require(current is not None and {key:current[key] for key in actual}==actual,
            "migration-apply-publication-changed")
        transitions.check(root)
        for path,item in staged:
            item.check()
            _require(_target_identity(path)==targets[path],"migration-apply-destination-changed")
        for path,item in staged:
            transitions.check(root)
            current,_=_reprove(root,batch_path,pending,raw)
            _require(current is not None and {key:current[key] for key in actual}==actual,
                "migration-apply-publication-changed")
            _require(_target_identity(path)==targets[path],"migration-apply-destination-changed")
            if targets[path] is not None and targets[path][-1]==item.sha256:continue
            _replace_staged(item,path,expected=targets[path],
                written=lambda:written.append(path.relative_to(root).as_posix()),transitions=transitions)
            _require(_target_identity(path)[:2]==(item.device,item.inode),"migration-apply-destination-changed")
            _directory_sync(path.parent)
        transitions.check(root)
        final,_=_reprove(root,batch_path,pending,raw)
        _require(final is not None and {key:final[key] for key in actual}==actual,
            "migration-apply-publication-changed")
        _require(all(_target_identity(path) is not None and _target_identity(path)[-1]==migration._digest(body) for path,body in outputs.items()),
            "migration-apply-refresh-incomplete")
        result=_report("refreshed" if written else "no_op",pending,actual=actual,written=written)
    except (migration.Refused,OSError,ValueError,TypeError,KeyError,sp.GovernsValidationError) as exc:
        result=_report("refresh_incomplete",pending,actual=actual,written=written,
            limit=exc.code if isinstance(exc,(migration.Refused,StagingCleanupIncomplete))
                else "migration-apply-refresh-unavailable",
            failure_class=_failure_class(exc))
    finally:
        cleanup_failed=False
        for _,item in staged:
            try:item.cleanup()
            except OSError:cleanup_failed=True
    if cleanup_failed:
        result=_report("refresh_incomplete",pending,actual=actual,written=written,
            limit=StagingCleanupIncomplete.code,failure_class="capability")
    return result


@dataclass(frozen=True)
class _PreparedApplication:
    snapshot:_Snapshot
    plans:tuple
    pending:dict
    raw:bytes
    selected:dict | None


def _prepare_application(root,batch_path,*,_source_io):
    initial_git=_git_frame(root)
    proof,run_rel=_select_close(root,batch_path,_source_io=_source_io)
    _require(_git_frame(root)==initial_git,"migration-apply-input-changed")
    batch=migration.load_batch(root,batch_path)
    raw,pending=_locator(proof,run_rel,batch,repo_root=root)
    target=root/pending["path"]
    target_metadata=_source_io.metadata(target)
    _require(target_metadata is None or stat.S_ISREG(target_metadata["st_mode"]),
        "migration-apply-destination-refused")
    if target_metadata is not None:
        _require(migration._digest(_source_io.read_bytes(target))==pending["sha256"],
            "migration-apply-publication-changed")
    publications,selected=migration._discover_apply_publications(root,pending=pending)
    prior=_prior_chain(root,batch,proof,publications,selected)
    facts=migration._validated_inputs(root,proof,_publication=selected)
    named=_named_inputs(root,proof,prior,facts,publications)
    plans=_compile_models(root,facts,pending,prior,batch,_source_io=_source_io)
    snapshot=_Snapshot.capture(root,_source_io=_source_io,named=named,git=initial_git)
    snapshot.check(root)
    return _PreparedApplication(snapshot,plans,pending,raw,selected)


def _problem_limit(problem):
    if isinstance(problem,(migration.Refused,SourceIORefusal,StagingCleanupIncomplete,_SourceContextCloseFailure)):
        return problem.code
    if isinstance(problem,(OSError,bionic_config.BionicConfigError)):
        return "migration-application-unavailable"
    return "migration-application-model-refused"


def _context_failure_report(result,problem,*,planning=False):
    if planning:
        report=dict(result) if result is not None else _unready_report(problem)
        report["ready_to_apply"]=False
    else:
        report=dict(result) if result is not None else _report("not_applied",None)
        if report["state"] in ("refreshed","no_op"):report["state"]="refresh_incomplete"
    original=report.get("limit")
    body_problem=problem.body_problem
    if body_problem is not None:original=_problem_limit(body_problem)
    if original is not None and original!=problem.code:report["body_limit"]=original
    report.update(limit=problem.code,failure_class="capability")
    return report


def apply(repo_root,batch_path):
    """Publish or refresh one proved batch; retain its report through teardown.

    No selector, pending identity, result or cache supplies approval. This function
    neither commits the witness nor calls a council; the caller owns those acts.
    """
    with _git_read_cache.scope():
        root=Path(repo_root).resolve();migration.ap.full_history(root);result=None
        try:
            with _source_context(root) as (source_io,_):
                result=_apply(root,batch_path,_source_io=source_io)
        except _SourceContextCloseFailure as problem:
            return _context_failure_report(result,problem)
        return result


def _unready_report(problem):
    return dict(authority="none",ready_to_apply=False,
        approval=dict(state="UNOBSERVED"),
        application=dict(state="UNOBSERVED",limit="migration-dry-run-no-application-performed"),
        written=[],planned_outputs=[],limit=_problem_limit(problem),
        failure_class=_failure_class(problem))


def dry_run(repo_root,batch_path):
    """Prove and compile all seven output plans without publishing or staging."""
    with _git_read_cache.scope():
        return _dry_run(repo_root,batch_path)


def _dry_run(repo_root,batch_path):
    root=Path(repo_root).resolve();result=None
    try:
        migration.ap.full_history(root)
        with _source_context(root) as (source_io,_):
            prepared=_prepare_application(root,batch_path,_source_io=source_io)
            paths=sorted(path for plan in prepared.plans for path,_ in plan)
            _require(len(paths)==7,"migration-apply-output-roster-refused")
            actual=None;writes=[];state="pending_commit";byte_state="commit-dependent"
            if prepared.selected is not None:
                actual={key:prepared.selected[key] for key in ("path","sha256","publication_commit")}
                outputs=_materialize(prepared.plans,prepared.pending,actual)
                for path,body in sorted(outputs.items()):
                    identity=_target_identity(path)
                    if identity is None or identity[-1]!=migration._digest(body):
                        writes.append(path.relative_to(root).as_posix())
                state="refreshed" if writes else "no_op";byte_state="verified"
            elif _target_identity(root/prepared.pending["path"]) is None:
                writes=[prepared.pending["path"]]
            selected,_=_reprove(root,batch_path,prepared.pending,prepared.raw)
            _require(selected==prepared.selected,"migration-apply-publication-changed")
            source_io.revalidate();prepared.snapshot.check(root)
            result=dict(authority="none",ready_to_apply=True,
                approval=dict(state="PROVED"),
                application=dict(state="UNOBSERVED",limit="migration-dry-run-no-application-performed"),
                publication=actual or prepared.pending,planned_state=state,
                planned_outputs=[path.relative_to(root).as_posix() for path in paths],
                planned_writes=writes,output_bytes=dict(state=byte_state),written=[])
    except _SourceContextCloseFailure as problem:
        return _context_failure_report(result,problem,planning=True)
    except (migration.Refused,OSError,bionic_config.BionicConfigError,ValueError,TypeError,KeyError,sp.GovernsValidationError) as problem:
        return _unready_report(problem)
    return result


def _apply(root,batch_path,*,_source_io):
    prepared=_prepare_application(root,batch_path,_source_io=_source_io)
    snapshot,plans,pending,raw,selected=(prepared.snapshot,prepared.plans,
        prepared.pending,prepared.raw,prepared.selected)
    target=root/pending["path"]
    target_identity=_target_identity(target)
    if selected is not None:
        actual={key:selected[key] for key in ("path","sha256","publication_commit")}
        try:return _refresh(root,batch_path,plans,pending,raw,actual,snapshot,_source_io=_source_io)
        except (migration.Refused,OSError,ValueError,TypeError,KeyError,sp.GovernsValidationError) as exc:
            return _report("refresh_incomplete",pending,actual=actual,
                limit=exc.code if isinstance(exc,(migration.Refused,StagingCleanupIncomplete))
                    else "migration-apply-refresh-unavailable",
                failure_class=_failure_class(exc))
    if target_identity is not None:
        _require(target_identity[-1]==pending["sha256"],"migration-apply-publication-conflict")
        _reprove(root,batch_path,pending,raw);snapshot.check(root);_source_io.revalidate()
        return _report("pending_commit",pending)
    transitions=_OwnedTransitions(snapshot)
    transitions.check(root)
    staged=_OwnedFile.create(target.parent,raw,transitions=transitions)
    visible=False;result=None;failure=None
    def published():
        nonlocal visible
        visible=True
    try:
        transitions.check(root)
        again,_=_reprove(root,batch_path,pending,raw)
        _require(again is None and _target_identity(target) is None,"migration-apply-publication-conflict")
        transitions.check(root);staged.check()
        _publish_witness(staged,target,published=published,transitions=transitions)
        _require(_target_identity(target)[:2]==(staged.device,staged.inode),"migration-apply-publication-changed")
        transitions.check(root)
        _directory_sync(target.parent)
        result=_report("pending_commit",pending,written=[pending["path"]])
    except (migration.Refused,OSError,ValueError,TypeError,KeyError,sp.GovernsValidationError) as exc:
        # A visible locator is retained even if a later I/O/durability check fails.
        if visible or _target_identity(target) is not None and _target_identity(target)[-1]==pending["sha256"]:
            result=_report("pending_commit",pending,written=[pending["path"]] if visible else [],
                limit=exc.code if isinstance(exc,migration.Refused) else "migration-apply-publication-unavailable",
                failure_class=_failure_class(exc))
        else:failure=exc
    finally:
        try:staged.cleanup()
        except OSError:
            if result is not None:
                result["limit"]=StagingCleanupIncomplete.code
                result["failure_class"]="capability"
            else:failure=StagingCleanupIncomplete()
    if failure is not None:raise failure
    return result
