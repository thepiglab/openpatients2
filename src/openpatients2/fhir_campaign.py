"""Bounded live Beam terminology selection plus deterministic FHIR smoke export.

One article packet, source hashes and every coding decision remain auditable.
Unreviewed model coding is emitted only in the explicitly tagged experimental
export; the default export remains uncoded until reviewer approval.
"""
import asyncio
from collections import Counter
from importlib.resources import files
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from . import fhir_pipeline as pipeline
from .data import read_jsonl, write_json
from .provenance import json_digest
from .terminology import Decision, validate_decision

VERSION = 'beam-fhir-campaign/1'


class NamedDecision(Decision):
    request_id: str


class BatchDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    decisions: list[NamedDecision]


def check_batch(requests, value):
    parsed = BatchDecision.model_validate(value).model_dump()
    rows = parsed['decisions']
    ids = [r['request_id'] for r in rows]
    expected = {r['id']: r for r in requests}
    if len(ids) != len(set(ids)) or set(ids) != set(expected):
        raise ValueError('Every requested fact needs exactly one select-or-abstain decision')
    for row in rows:
        validate_decision(expected[row['request_id']], {k:v for k,v in row.items() if k != 'request_id'})
    return parsed


async def select_batches(work, endpoint, arm_manifest, *, max_batches=16, batch_size=8, concurrency=4):
    from .pilot_extract import PilotConfig, PilotRunner
    from .staged_timeline import fits, splittable_failure, replaced
    pipeline.check(work)
    parsed = urlsplit(endpoint)
    if (parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'localhost','127.0.0.1','::1'}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('Campaign coding requires a local served endpoint')
    if not 1 <= max_batches <= 64 or not 1 <= batch_size <= 16 or not 1 <= concurrency <= 4:
        raise ValueError('Invalid bounded coding workload')
    manifest = json.loads(Path(arm_manifest).read_text())
    config = PilotConfig.model_validate({**manifest['extraction'],
        'concurrency_per_endpoint':concurrency, 'max_calls':max_batches*2,
        'max_output_tokens':8192, 'max_retry_tokens':8192, 'max_length_retry_tokens':8192,
        'min_output_tokens':512, 'require_full_output_budget':False,
        'max_repair_rounds':1, 'timeout_seconds':180,
        'max_total_tokens':8_000_000, 'task_repair_allowlist':['terminology_selection_batch']})
    identity = {'config':config.model_dump(),'context':manifest['context'],
                'max_batches':max_batches,'batch_size':batch_size}
    write_json(work/'coder-config.json', identity)
    call_dir = work/'coding-calls'/'batch'
    pending=[]
    for request in read_jsonl(work/'requests.jsonl'):
        saved = work/'decisions'/(request['id']+'.json')
        if saved.exists():
            prior = json.loads(saved.read_text())
            if prior['request_sha256'] != json_digest(request):
                raise ValueError('Saved coding decision belongs to a changed request')
            validate_decision(request, prior['decision'])
            continue
        if not request['candidates']:
            write_json(work/'decisions'/(request['id']+'.json'),{
                'request_sha256':json_digest(request),'decision':{'decision':'abstain',
                'candidate_id':None,'relation':None,'rationale':'No candidates in the pinned local catalog for this fact/task.'},
                'review_status':'automatic_abstention','origin':'retrieval_empty'})
        else:
            pending.append(request)
    order={'conditions':0,'symptoms_function':1,'oncology':2,'family_genetics':3}
    pending.sort(key=lambda r:(order.get(r['task'],9),r['id']))
    selected=pending[:max_batches*batch_size]
    runner = PilotRunner(config.model_dump(),call_dir,[endpoint],manifest['context'],'targeted')
    runner.schemas['terminology_selection_batch'] = BatchDecision.model_json_schema()
    source_cache={}
    prompt=files('openpatients2').joinpath('prompts/terminology_selection.md').read_text()
    async def batch(requests, number):
        packets=[]
        for request in requests:
            aid=request['article_id']
            if aid not in source_cache:
                source_cache[aid]=json.loads((work/'sources'/(aid+'.json')).read_text())
            source=source_cache[aid]
            cited={e.get('segment_id',e.get('source_section')) for e in request['fact'].get('evidence',[])}
            packets.append({'request':request,'cited_source_segments':[
                {'segment_id':s['segment_id'],'text':s['text']} for s in source['segments'] if s['segment_id'] in cited]})
        messages=[{'role':'system','content':prompt}, {'role':'user','content':
            'Return one decision for EVERY request_id, including abstentions. Do not change patient identities. '
            'Each decision has request_id, decision, candidate_id, relation, rationale.\nREQUESTS:\n'+
            json.dumps(packets,ensure_ascii=False)+'\nSCHEMA:\n'+json.dumps(BatchDecision.model_json_schema())}]
        async def split():
            middle=len(requests)//2
            return await batch(requests[:middle],number+'.0') + await batch(requests[middle:],number+'.1')
        if len(requests)>1 and not await fits(runner,'terminology_selection_batch',messages,0):
            return await split()
        result=await runner.call('terminology_selection_batch',messages,lambda v:check_batch(requests,v),
            {'article_id':requests[0]['article_id'],'coding_batch':number,
             'request_ids':[r['id'] for r in requests]},0,[],split_on_length=len(requests)>1)
        if len(requests)>1 and splittable_failure(result):
            replaced(runner,result)
            return await split()
        if result['status']=='valid':
            lookup={r['id']:r for r in requests}
            for row in result['data']['decisions']:
                key=row['request_id']
                write_json(work/'decisions'/(key+'.json'),{'request_sha256':json_digest(lookup[key]),
                    'decision':{k:v for k,v in row.items() if k!='request_id'},
                    'review_status':'model_unreviewed','coder_sha256':json_digest(identity),
                    'task_key':result['task_key'],'call_directory':str(call_dir.relative_to(work))})
        return [result['status']]
    try:
        outcomes=await asyncio.gather(*(batch(selected[i:i+batch_size],str(i//batch_size))
                                      for i in range(0,len(selected),batch_size)))
    finally:
        await runner.close()
    return {'batches':dict(Counter(status for group in outcomes for status in group)),
            'requests_selected':len(selected),'requests_over_budget':len(pending)-len(selected),
            'decisions_are_reviewed':False}


async def run_article_stage(input_path, sources_root, output, endpoint, arm_manifest, catalog_path,
                            *, max_batches=16, batch_size=8, concurrency=4,
                            time_budget_seconds=900, validator_jar=None, previous_packet=None,
                            previous_packet_original_location=None):
    """Called while the extraction server is alive; never starts another server.

    `output` must be new. `call_ledgers` are relative to output, standard
    PilotRunner attempts.jsonl and suitable for dense_benchmark.ledger_metrics.
    `complete` means execution coverage, not correct coding or clinical validity.
    """
    start=time.monotonic(); output=Path(output);output.mkdir(parents=True,exist_ok=False)
    packet=output/'packet'
    manifest=await asyncio.to_thread(pipeline.prepare,input_path,sources_root,packet,catalog_path)
    reused = 0
    if previous_packet is not None:
        previous_packet = Path(previous_packet)
        previous = pipeline.check(previous_packet)
        if previous['catalog_sha256'] != manifest['catalog_sha256']:
            raise ValueError('Cannot reuse coding decisions with a changed catalog')
        for request in read_jsonl(packet/'requests.jsonl'):
            saved = previous_packet/'decisions'/(request['id']+'.json')
            if not saved.exists():
                continue
            prior = json.loads(saved.read_text())
            if prior['request_sha256'] != json_digest(request):
                raise ValueError('Cannot reuse a decision for changed source facts/candidates')
            validate_decision(request, prior['decision'])
            if prior.get('call_directory'):
                # A history rename moves direct calls with their packet. Older
                # inherited calls remain elsewhere, so their relative paths
                # must be resolved against the packet's pre-move location.
                trace_base = (Path(previous_packet_original_location)
                    if prior.get('inherited_from_packet') and previous_packet_original_location is not None
                    else previous_packet)
                trace = (trace_base/prior['call_directory']).resolve()
                if not trace.is_dir():
                    raise ValueError('Inherited coding call directory is missing; preserve the original trace')
                prior['call_directory'] = os.path.relpath(trace, packet)
            prior['inherited_from_packet'] = os.path.relpath(previous_packet, packet)
            write_json(packet/'decisions'/(request['id']+'.json'), prior)
            reused += 1
    coding_error=None;coding={}
    try:
        async with asyncio.timeout(time_budget_seconds):
            coding=await select_batches(packet,endpoint,arm_manifest,max_batches=max_batches,
                batch_size=batch_size,concurrency=concurrency)
    except (TimeoutError,ValueError,OSError) as exc:
        coding_error={'type':type(exc).__name__,'message':str(exc)[:1200]}
    requests=list(read_jsonl(packet/'requests.jsonl'))
    decisions={p.stem:json.loads(p.read_text()) for p in (packet/'decisions').glob('*.json')}
    pending=[r['id'] for r in requests if r['id'] not in decisions]
    counts=Counter(d['decision']['decision'] for d in decisions.values())
    exports={}
    for name,allow in [('uncoded',False),('experimental-coded',True)]:
        report=await asyncio.to_thread(pipeline.export,packet,output/name,
            allow_unreviewed_coding=allow,validator_jar=validator_jar)
        exports[name]={'directory':name,'statuses':report['statuses'],
            'resource_counts':report['resource_counts'],'codings_attached':report['codings_attached'],
            'deferred_reasons':report['deferred_reasons'],
            'official_validator_requested':bool(validator_jar),
            'official_validator_run':any(p.get('validation',{}).get('official_validator_run',False)
                for p in report['patients']), 'release_ready':False}
    ledgers=sorted(packet.glob('coding-calls/**/attempts.jsonl'))
    from .dense_benchmark import ledger_metrics
    usage=ledger_metrics(ledgers)
    complete=not pending and not coding_error and all(not r['statuses'].get('quarantined') for r in exports.values())
    receipt={'format':VERSION,'status':'done' if complete else 'partial','complete':complete,
        'packet':'packet','exports':exports,'coding':{**coding,'decisions':dict(counts),
            'requests':len(requests),'pending_requests':pending,'error':coding_error,'reused_decisions':reused},
        'usage':usage,'call_ledgers':[str(p.relative_to(output)) for p in ledgers],
        'elapsed_seconds':time.monotonic()-start,'catalog_sha256':manifest['catalog_sha256'],
        'compiler_sources':{p:pipeline.sha(Path(__file__).with_name(p)) for p in
            ('fhir_campaign.py','fhir_pipeline.py','fhir_export.py','fhir_measurements.py',
             'measurement_series.py','fhir_validation.py','terminology.py')},
        'clinical_accuracy_verified':False,'release_ready':False}
    write_json(output/'receipt.json',receipt)
    return receipt
