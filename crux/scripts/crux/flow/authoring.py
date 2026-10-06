from __future__ import annotations

from datetime import datetime,timezone
from pathlib import Path
import re

import yaml

from .common import FlowError
from . import managed,upstream_api


class Prose(str):
    pass


class BookDumper(yaml.SafeDumper):
    pass


BookDumper.add_representer(Prose,lambda dumper,value:dumper.represent_scalar('tag:yaml.org,2002:str',str(value),style='|'))


def book_bytes(book: dict) -> bytes:
    value=dict(book)
    for name in ('goal','strategy'): value[name]=Prose(value[name])
    value['prompts']=[dict(p,**{name:Prose(p[name]) for name in ('purpose','prompt','expected_output')},side_effects=p.get('side_effects',[])) for p in value['prompts']]
    return yaml.dump(value,Dumper=BookDumper,sort_keys=False,allow_unicode=True).encode()


def records(plugin: Path,repo: Path,docs: Path,book: dict,stem: str) -> dict[str,bytes]:
    checker=upstream_api.load(plugin,'check-promptbook-index.py')
    index_name=(docs/'promptbooks/index.md').relative_to(repo).as_posix()
    raw=managed.read_file(repo,index_name)
    active=checker.book_stems(docs/'promptbooks/active')
    archived=checker.book_stems(docs/'promptbooks/archive')
    if raw is None:
        if active or archived: raise FlowError('existing books have no index; reconcile their editorial records before authoring')
        index='# Promptbooks\n\n## Active (0)\n\n| id | title | status | current_run | progress | tags | created_at |\n|---|---|---|---|---|---|---|\n\n## Recent runs (last 20)\n\n## Archived (0)\n'
    else: index=raw.decode('utf-8')
    matches=list(re.finditer(r'^## Active \(\d+\)\s*$',index,re.M))
    if len(matches)!=1: raise FlowError('existing promptbook index has an ambiguous active section')
    start=matches[0].end(); end=index.find('\n## ',start)
    end=len(index) if end<0 else end
    title=' '.join(book['title'].split()).replace('|','\\|').replace('[[','[').replace(']]',']')
    row=f'| [[promptbooks/{stem}]] | {title} | active | RUN-001 | 0/{book["total_prompts"]} (0%) | flow | {book["created_at"]} |\n'
    section=index[start:end]
    index=index[:start]+section.rstrip()+'\n'+row+'\n'+index[end:]
    index=re.sub(r'^## Active \(\d+\)',f'## Active ({len(active)+1})',index,count=1,flags=re.M)
    tree_name=(docs/'index.md').relative_to(repo).as_posix(); tree=managed.read_file(repo,tree_name)
    tree_text=tree.decode() if tree else '# Project knowledge\n'
    heading=f'## Promptbooks ({len(active)+1} active, {len(archived)} archived)'
    if re.search(r'^## Promptbooks \(\d+ active, \d+ archived\)',tree_text,re.M):
        tree_text=re.sub(r'^## Promptbooks \(\d+ active, \d+ archived\)',heading,tree_text,count=1,flags=re.M)
    else: tree_text=tree_text.rstrip()+'\n\n'+heading+'\n\n[[promptbooks/index]]\n'
    log_name=(docs/'log.md').relative_to(repo).as_posix(); previous=managed.read_file(repo,log_name)
    log=previous.decode() if previous else '# Project log\n'
    entry=f'## [{datetime.now(timezone.utc).date().isoformat()}] promptbook | authored {stem}\n\n{title}; {book["total_prompts"]} declared prompts.\n\n'
    if log.startswith('# '):
        first,_,rest=log.partition('\n'); log=first+'\n\n'+entry+rest.lstrip('\n')
    else: log=entry+log
    return {index_name:index.encode(),tree_name:tree_text.encode(),log_name:log.encode()}
