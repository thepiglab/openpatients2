"""Optional OpenAI-compatible, candidate-constrained terminology adjudication.

CPU request creation/profiling is separated from GPU inference. Returned reasoning
is saved with each proposal. A syntactically valid proposal is not auto-approved.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, BaseModel, ConfigDict

from .client import APIClient
from .config import APIConfig, ConfigModel
from .coding import HARD_ISSUES
from .data import read_jsonl, write_json
from .provenance import json_digest

TASK='terminology_selection'
PROMPT_VERSION='1.0.0'


class Selection(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    decision: Literal['select','abstain']
    candidate_id: str | None
    relation: Literal['equivalent','broader'] | None
    rationale: str = Field(min_length=1,max_length=3000)


def selection_schema():
    schema=Selection.model_json_schema()
    def clean(v):
        if isinstance(v,dict):
            return {k:clean(x) for k,x in v.items() if k not in {'title','description'}}
        if isinstance(v,list):return [clean(x) for x in v]
        return v
    return clean(schema)


def validate_selection(raw,request):
    s=Selection.model_validate(raw).model_dump()
    if s['decision']=='abstain':
        if s['candidate_id'] is not None or s['relation'] is not None:
            raise ValueError('Abstention must have null candidate and relation')
    else:
        c=next((c for c in request['candidates'] if c['candidate_id']==s['candidate_id']),None)
        if not c or s['relation'] is None:
            raise ValueError('Selection is not an offered candidate')
        if set(c['issues']) & HARD_ISSUES:
            raise ValueError('Candidate contradicts source context or domain')
    return s


def messages(request):
    content=Path(__file__).with_name('prompts').joinpath('terminology_selection.md').read_text()
    return [{'role':'system','content':content},{'role':'user','content':json.dumps(request,ensure_ascii=False,sort_keys=True)}]


class CodingRunConfig(ConfigModel):
    input: str = 'data/coding-requests.jsonl'
    output: str = 'runs/coding-k2'
    api: APIConfig=Field(default_factory=APIConfig)
    concurrency: int = Field(default=16,ge=1,le=1024)
    max_model_len: int=Field(default=16384,ge=1024)
    safety_tokens: int=Field(default=512,ge=0)
    token_profile: str | None=None
    require_token_profile: bool=True
    allow_unpinned_revision: bool=False

    @classmethod
    def load(cls,path):
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))


def profile_identity(cfg):
    return json_digest({'api':cfg.api.identity(),'kwargs':cfg.api.extra_body,
                        'prompt_version':PROMPT_VERSION,'schema':selection_schema(),
                        'system_prompt':messages({})[0]['content']})


def request_is_valid(row):
    material={k:v for k,v in row.items() if k!='request_digest'}
    if json_digest(material)!=row.get('request_digest'):
        raise ValueError('Coding request digest mismatch')


def profile_requests(cfg,tokenizer_path,destination,workers=8,tokenizer=None):
    if workers<1 or workers>128:raise ValueError('workers must be 1..128')
    if tokenizer is None:
        from transformers import AutoTokenizer
        tokenizer=AutoTokenizer.from_pretrained(tokenizer_path,local_files_only=True,trust_remote_code=True)
    kwargs=dict(cfg.api.extra_body.get('chat_template_kwargs',{}))
    if 'reasoning_effort' in cfg.api.extra_body:kwargs.setdefault('reasoning_effort',cfg.api.extra_body['reasoning_effort'])
    def one(r):
        request_is_valid(r)
        ids=tokenizer.apply_chat_template(messages(r),tokenize=True,add_generation_prompt=True,**kwargs)
        return {'mapping_id':r['mapping_id'],'request_digest':r['request_digest'],'prompt_tokens':len(ids)}
    out=Path(destination);out.parent.mkdir(parents=True,exist_ok=True)
    counts=[]
    with out.open('w') as f,ThreadPoolExecutor(max_workers=workers) as pool:
        it=iter(read_jsonl(cfg.input))
        while batch:=list(islice(it,128)):
            for r in pool.map(one,batch):
                f.write(json.dumps(r)+'\n');counts.append(r['prompt_tokens'])
    result={'fingerprint':profile_identity(cfg),'requests':len(counts),'sum_prompt_tokens':sum(counts),
            'max_prompt_tokens':max(counts,default=0),'model_identity':cfg.api.identity(),
            'tokenizer_path':str(Path(tokenizer_path).resolve()),'gpu_used':False}
    write_json(out.with_suffix(out.suffix+'.manifest.json'),result)
    return result


async def infer_requests(cfg,client: APIClient | None=None):
    if cfg.api.revision in {'UNPINNED','unspecified','main','latest'} and not cfg.allow_unpinned_revision:
        raise ValueError('Pin the selector model revision before inference')
    profiles={}
    if cfg.token_profile:
        p=Path(cfg.token_profile);meta=json.loads(p.with_suffix(p.suffix+'.manifest.json').read_text())
        if meta['fingerprint']!=profile_identity(cfg):raise ValueError('Selector token profile does not match prompt/model')
        profiles={r['mapping_id']:r for r in read_jsonl(p)}
    elif cfg.require_token_profile:
        raise ValueError('Run code-profile on CPU before selector inference')
    # Validate every request/context budget before emitting any request to a GPU.
    ids=set();request_digests=[]
    for r in read_jsonl(cfg.input):
        request_is_valid(r)
        if r['mapping_id'] in ids:raise ValueError('Duplicate mapping request')
        ids.add(r['mapping_id']);request_digests.append((r['mapping_id'],r['request_digest']))
        if cfg.token_profile:
            p=profiles.get(r['mapping_id'])
            if not p or p['request_digest']!=r['request_digest']:raise ValueError('Missing/changed profiled request')
            if p['prompt_tokens']+cfg.api.max_tokens+cfg.safety_tokens>cfg.max_model_len:
                raise ValueError('Selector context overflow; no source truncation allowed')
    out=Path(cfg.output);out.mkdir(parents=True,exist_ok=True)
    state=out/'coding-state.sqlite';db=sqlite3.connect(state);state.chmod(0o600)
    db.executescript('''CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
      CREATE TABLE IF NOT EXISTS result(mapping_id TEXT PRIMARY KEY,request_digest TEXT,payload TEXT);
      CREATE TABLE IF NOT EXISTS attempt(id INTEGER PRIMARY KEY,mapping_id TEXT,payload TEXT);''')
    run_hash=json_digest({'identity':profile_identity(cfg),'generation':cfg.api.generation(),'requests':sorted(request_digests)})
    stored=db.execute("SELECT value FROM metadata WHERE key='run_hash'").fetchone()
    if stored and stored[0]!=run_hash:
        db.close();raise ValueError('Selector output directory contains a different experiment')
    db.execute("INSERT OR IGNORE INTO metadata VALUES('run_hash',?)",(run_hash,));db.commit()
    owned=client is None
    client=client or APIClient(cfg.api,schema_overrides={TASK:selection_schema()})
    # A supplied mock/client must still enforce the same wire schema.
    client.schema_overrides[TASK]=selection_schema()
    started=time.perf_counter();counts=Counter();latencies=[];usage=Counter();queue=asyncio.Queue(maxsize=cfg.concurrency*2)
    async def worker():
        while True:
            request=await queue.get()
            try:
                if request is None:return
                mid=request['mapping_id']
                existing=db.execute('SELECT request_digest,payload FROM result WHERE mapping_id=?',(mid,)).fetchone()
                if existing and existing[0]!=request['request_digest']:raise ValueError('Changed request in resumed selector run')
                if existing and json.loads(existing[1])['status']=='ok':counts['resumed']+=1;continue
                endpoint=cfg.api.endpoints[int(mid[:8],16)%len(cfg.api.endpoints)]
                msg=messages(request)
                for attempt in range(cfg.api.http_retries+1):
                    response=await client.complete_once(endpoint,TASK,msg,cfg.api.max_tokens)
                    audit={'mapping_id':mid,'request_digest':request['request_digest'],
                           'model_identity':cfg.api.identity(),'generation':cfg.api.generation(),
                           'request':request,'messages':msg if cfg.api.save_messages else None,
                           'response':response.response(),'metrics':response.metrics(),'attempt':attempt+1}
                    db.execute('INSERT INTO attempt(mapping_id,payload) VALUES(?,?)',(mid,json.dumps(audit,ensure_ascii=False)))
                    db.commit()
                    counts['network_requests']+=1;latencies.append(response.latency_seconds)
                    for key in ('prompt_tokens','completion_tokens','reasoning_tokens','cached_tokens'):
                        val=getattr(response,key)
                        if val is not None:usage[key]+=val;counts[key+'_reported']+=1
                    if not client.transient(response) or attempt==cfg.api.http_retries:break
                    await asyncio.sleep(min(2**attempt,16))
                selection=None;error=response.error;status='error'
                if not error and response.finish_reason=='stop':
                    try:
                        selection=validate_selection(json.loads(response.content),request);status='ok'
                    except (ValueError,TypeError,KeyError) as e:error='invalid_candidate_selection: '+str(e)[:200]
                elif not error:error='incomplete_completion: '+str(response.finish_reason)
                payload={**audit,'status':status,'selection':selection,'error':error,
                         'reasoning_text':response.reasoning_text,'mapping_approved':False}
                db.execute('INSERT OR REPLACE INTO result VALUES(?,?,?)',(mid,request['request_digest'],json.dumps(payload,ensure_ascii=False)))
                db.commit();counts[status]+=1
            finally:queue.task_done()
    workers=[]
    async def producer():
        for request in read_jsonl(cfg.input):
            await queue.put(request)
        for _ in range(cfg.concurrency):await queue.put(None)
    try:
        # TaskGroup cancels the producer too when a worker fails. A bounded queue
        # cannot deadlock on an exception while enqueuing a large corpus.
        async with asyncio.TaskGroup() as group:
            workers=[group.create_task(worker()) for _ in range(cfg.concurrency)]
            group.create_task(producer())
        for table,name in (('result','proposals.jsonl'),('attempt','attempts.jsonl')):
            with (out/name).open('w',encoding='utf-8') as f:
                (out/name).chmod(0o600)
                for r in db.execute(f'SELECT payload FROM {table} ORDER BY '+('mapping_id' if table=='result' else 'id')):f.write(r[0]+'\n')
        elapsed=time.perf_counter()-started
        result={'counts':dict(counts),'elapsed_seconds':elapsed,
                'new_requests_per_second':counts['network_requests']/elapsed if elapsed else None,
                'usage':{k:usage[k] if counts[k+'_reported'] else None for k in ('prompt_tokens','completion_tokens','reasoning_tokens','cached_tokens')},
                'reasoning_accounting':'reasoning_tokens are a subset of completion_tokens when reported; do not add twice',
                'clinical_accuracy':None,'model_identity':cfg.api.identity(),
                'proposals':str(out/'proposals.jsonl'),'mapping_approved_by_model':False}
        write_json(out/'report.json',result);return result
    finally:
        for w in workers:
            if not w.done():w.cancel()
        await asyncio.gather(*workers,return_exceptions=True)
        if owned:await client.close()
        db.close()
