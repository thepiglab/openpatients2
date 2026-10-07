"""Controlled component interventions on identical saved model-generated bundles.

The baseline supplies identities, images and facts, never reference answers.
Each component starts independently from that baseline. Copied work is excluded
from inference token/time metrics and recorded by hashes.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

from .article_tasks import patient_packet
from .data import read_jsonl, write_json
from .extraction_contracts import FactEnvelope, field_support_report
from .ledger_pipeline import challenge_attributes, assemble_episodes
from .longitudinal import PatientTimeline, audit_timeline, relative_order
from .measurements import observation_measurements
from .pilot_extract import PilotRunner, review_rows, literal_field_support, source_gate, sources_for
from .provenance import json_digest
from .schemas import TASK_MODELS
from .table_cells import inventory, patient_scope, CellBatch, cell_messages, check_cell_batch

COMPONENTS=('table-observations','attribute-audit','episode-rebuild')


def _read(path, limit=128_000_000):
    path=Path(path)
    if path.is_symlink() or path.stat().st_size>limit:raise ValueError('Invalid bounded baseline input')
    return json.loads(path.read_text())


def _hash(path):
    if Path(path).is_symlink() or Path(path).stat().st_size>128_000_000:
        raise ValueError('Invalid bounded baseline input')
    with Path(path).open('rb') as handle:return hashlib.file_digest(handle,'sha256').hexdigest()


def _rows(article, bundle):
    return [r for task in TASK_MODELS for r in review_rows(article,bundle['source']['record_id'],task,bundle['sections'].get(task))]


def refresh_bundle(article,bundle,original_rows):
    """Refresh all derived fact registries without retaining dangling old IDs."""
    rows=_rows(article,bundle);rid=bundle['source']['record_id'];ids={r['review_id']:rid for r in rows}
    _,sources=sources_for(article)
    graph=bundle['companions'].get('timeline_v2');removed=[]
    if graph:
        for event in graph['events']:
            removed.extend(f for f in event['fact_ids'] if f not in ids)
            event['fact_ids']=[f for f in event['fact_ids'] if f in ids]
        bundle['timeline_audit']=audit_timeline(PatientTimeline.model_validate(graph),sources,ids)
        bundle['relative_timeline']=relative_order(graph)
    else:
        bundle.pop('timeline_audit',None);bundle.pop('relative_timeline',None)
    events=(graph or {}).get('events',[])
    bundle['patient_state_index']={'facts':[{'fact_id':r['review_id'],'task':r['task'],
        'value_as_extracted':r['candidate'],'event_ids':[e['event_id'] for e in events if r['review_id'] in e['fact_ids']],
        'time_association':'linked_event_unreviewed' if any(r['review_id'] in e['fact_ids'] for e in events) else 'unknown'} for r in rows],
        'state_carried_forward':False,'synthetic_dates_generated':False,'clinical_fidelity_verified':False}
    bundle['observation_measurements']=observation_measurements(bundle['sections'].get('observations'),article['segments'])
    envelopes=[]
    for r in rows:
        collection=r['pointer'].split('/')[1] if r['pointer']!='/' else 'case_context'
        fact=FactEnvelope(fact_id=r['review_id'],record_id=rid,task=r['task'],collection=collection,
            value=r['candidate'],field_support=literal_field_support(r['candidate'],r['evidence_spans']),
            origin='article_text',supersedes_fact_id=None)
        envelopes.append({'fact':fact.model_dump(),'gate_report':field_support_report(fact,sources),
            'section_literal_validation_only':True,'evidence_spans_for_reviewer':r['evidence_spans']})
    bundle['fact_review']={'field_support_required_before_promotion':True,'all_fields_semantically_unreviewed':True,
                          'review_ids':list(ids),'envelopes':envelopes}
    changed={r['review_id'] for r in original_rows}!={r['review_id'] for r in rows}
    bundle['component_consistency']={'clinical_facts_changed':changed,'removed_old_fact_links':removed,
        'summary_status':'frozen_baseline_requires_reconciliation' if changed else 'frozen_baseline',
        'timeline_status':'changed_fact_links_require_reconciliation' if removed else 'source_checked_structure_only',
        'whole_bundle_promotion_ready':False}
    bundle['clinical_accuracy_verified']=False;bundle['complete_for_scope']=False
    return rows


def cell_is_covered(cell,section):
    """Conservative source-cell identity; never match a value in another column."""
    scalar=cell['measurement']
    if scalar is None:return False
    from decimal import Decimal
    for obs in (section or {}).get('items',[]):
        if obs.get('subject')!='index_patient' or obs.get('numeric_value') is None:continue
        if Decimal(str(obs['numeric_value']))!=Decimal(scalar['magnitude']):continue
        if obs.get('comparator')!=scalar['comparator'] or obs.get('unit')!=(cell['raw_unit'] or scalar['unit']):continue
        if cell['time_header'] and (obs.get('time') or {}).get('text')!=cell['time_header']:continue
        if any(q.get('source_section')==cell['segment_id'] and q.get('quote') and
               q['quote'] in cell['source_quote'] and cell['raw_value'] in q['quote']
               for q in obs.get('evidence',[])):return True
    return False


async def augment_tables(runner,article,roster,patient,bundle,replica):
    inv=inventory(article);existing=bundle['sections'].get('observations')
    cells=[c for c in inv['cells'] if patient_scope(c,article,patient)=='target']
    pending=[c for c in cells if not cell_is_covered(c,existing)]
    packet=patient_packet(article,roster,patient,scope='whole_article')
    checker=runner.clinical_checker('observations',packet,article['segments'])
    identity={'article_id':article['article_id'],'patient_id':patient['patient_id'],'record_id':bundle['source']['record_id']}
    runner.schemas['table_observations']=CellBatch.model_json_schema()
    async def batch(start):
        selected=pending[start:start+8]
        return await runner.call('table_observations',cell_messages(article,patient,selected),
            lambda v:check_cell_batch(v,selected,article,patient,checker),
            {**identity,'cell_batch':start//8},replica,article['segments'])
    results=await asyncio.gather(*(batch(i) for i in range(0,len(pending),8)))
    accepted=[x for result in results if result['data'] for x in result['data']['accepted']]
    merged=deepcopy(existing) if existing is not None else {'coverage':'limited','limitations':[],
        'documentation_status':'not_documented','documentation_evidence':[],'items':[]}
    seen={json_digest(x) for x in merged['items']}
    for row in accepted:
        obs=row['observation'];key=json_digest(obs)
        if key not in seen:merged['items'].append(obs);seen.add(key)
    if accepted:
        merged.update(coverage='limited',documentation_status='documented')
        merged['documentation_evidence']=merged['documentation_evidence'] or accepted[0]['observation']['evidence']
        merged['limitations']=list(dict.fromkeys([*merged['limitations'],'Table-cell augmentation; unresolved cells remain in the review inventory.']))
        # Each addition was independently checked. Accepted baseline objects
        # remain unchanged and in the same order, preserving their fact IDs.
        bundle['sections']['observations']=merged
    receipt={'inventory':inv,'scope_counts':dict(Counter(patient_scope(c,article,patient) for c in inv['cells'])),
        'target_cells':len(cells),'already_covered_cells':len(cells)-len(pending),
        'requested_cells':len(pending),'accepted_cells':accepted,
        'decisions':[x for result in results if result['data'] for x in result['data']['decisions']],
        'quarantine':[x for result in results if result['data'] for x in result['data']['quarantine']],
        'failed_batches':[r['identity']['cell_batch'] for r in results if r['status']!='valid'],
        'semantic_coverage_verified':False}
    write_json(runner.output/'table-cells'/(json_digest(identity['record_id'])+'.json'),receipt)
    return receipt


async def run_component_trial(config,articles_path,output,endpoints,context,*,baseline_dir,component,seed=None,http=None):
    if component not in COMPONENTS:raise ValueError('Unknown controlled component')
    baseline=Path(baseline_dir);paths={name:baseline/name for name in
        ('report.json','run-config.json','patients.jsonl','rosters.json','visual-annotations.json')}
    hashes={name:_hash(path) for name,path in paths.items()}
    before=_read(paths['report.json']);run_config=_read(paths['run-config.json'])
    # Bound sample loading using the existing selected-source reader.
    from .pilot_media import _articles
    articles=_articles(articles_path);by_article={a['article_id']:a for a in articles}
    patients=list(read_jsonl(paths['patients.jsonl']));rosters=_read(paths['rosters.json']);visuals=_read(paths['visual-annotations.json'])
    if len({p['source']['record_id'] for p in patients})!=len(patients):raise ValueError('Duplicate baseline patients')
    if set(run_config['selected_article_ids'])!=set(by_article):raise ValueError('Baseline source selection differs')
    if seed is not None and before['seed']!=seed:raise ValueError('Baseline seed differs')
    roster_by={r['article_id']:r for r in rosters['articles']}
    if len(roster_by)!=len(rosters['articles']) or set(roster_by)!=set(by_article):raise ValueError('Baseline roster coverage differs')
    runner=PilotRunner(config,output,endpoints,context,'targeted',seed=seed,http=http)
    started=time.monotonic()
    try:
        for key in ('model_id','revision'):
            if before['model'][key]!=getattr(runner.config,key):raise ValueError('Baseline model changed')
        for a in articles:
            errors=source_gate(a,runner.config)
            if errors:raise ValueError('Component source rejected: '+str(errors))
            if roster_by[a['article_id']]['text_sha256']!=a['text_sha256']:raise ValueError('Baseline roster source changed')
        for p in patients:
            src=p['source']['article_source'];a=by_article[src['article_id']]
            if any(src.get(k)!=a[k] for k in ('text_sha256','xml_sha256')):raise ValueError('Baseline patient source changed')
        write_json(runner.output/'frozen-inputs.json',{'baseline_dir':str(baseline),'sha256':hashes,
            'source_sha256':_hash(articles_path),'baseline_is_generated_not_gold':True})
        async def one(index,original):
            bundle=deepcopy(original);rid=bundle['source']['record_id'];article=by_article[bundle['source']['article_source']['article_id']]
            record=roster_by[article['article_id']]
            if record['status']!='valid':raise ValueError('Baseline patient has invalid roster')
            roster=record['roster'];patient=next(p for p in roster['patients'] if p['patient_id']==rid.rsplit(':',1)[1])
            original_rows=_rows(article,bundle);replica=index%len(endpoints)
            identity={'article_id':article['article_id'],'patient_id':patient['patient_id'],'record_id':rid}
            if component=='attribute-audit':
                tasks=[{'task':k,'data':deepcopy(bundle['sections'].get(k))} for k in TASK_MODELS]
                packet=patient_packet(article,roster,patient,scope='whole_article')
                _,receipt=await challenge_attributes(runner,article,patient,packet,tasks,original_rows,identity,replica)
                bundle['sections']={t['task']:t['data'] for t in tasks}
            elif component=='episode-rebuild':
                _,sources=sources_for(article)
                timeline,_,receipt=await assemble_episodes(runner,article,patient,original_rows,identity,replica,sources)
                bundle['companions']['timeline_v2']=timeline['data']
                bundle['quality']['timeline_v2']={k:timeline[k] for k in ('status','errors')}
            else:receipt=await augment_tables(runner,article,roster,patient,bundle,replica)
            rows=refresh_bundle(article,bundle,original_rows)
            bundle['experimental_reviews']['controlled_component']={'component':component,'receipt':receipt,
                'baseline_patient_sha256':json_digest(original),'copied_vision_sha256':json_digest(original.get('vision')),
                'copied_summary_sha256':json_digest(original['companions'].get('summary'))}
            bundle['arm']=component
            write_json(runner.output/'patients'/(json_digest(rid)+'.json'),bundle)
            write_json(runner.output/'review'/(json_digest(rid)+'.json'),rows)
            runner.patients.append(bundle)
        await asyncio.gather(*(one(i,p) for i,p in enumerate(patients)))
        runner.patients.sort(key=lambda p:p['source']['record_id'])
        with (runner.output/'patients.jsonl').open('w') as f:
            for p in runner.patients:f.write(json.dumps(p,ensure_ascii=False)+'\n')
        write_json(runner.output/'rosters.json',rosters);write_json(runner.output/'visual-annotations.json',visuals)
        # Copy figure task data for the existing scorer, excluding prior attempts
        # and excluding these frozen tasks from generated task/time accounting.
        for path in sorted((baseline/'tasks').glob('*.json')):
            task=_read(path)
            if task['task'] in {'figure_attribution','pixel_attribution','joint_figure'}:
                frozen=deepcopy(task);frozen.update(attempts=[],origin='frozen_baseline',excluded_from_generated_task_metrics=True)
                write_json(runner.output/'tasks'/path.name,frozen)
        wall=time.monotonic()-started
        provenance={'component':component,'baseline_dir':str(baseline),'baseline_hashes':hashes,
            'baseline_model_calls':before.get('model_calls'),'baseline_wall_seconds':before.get('wall_seconds'),
            'baseline_output_tokens':before.get('tokens',{}).get('output_tokens'),
            'cost_scope':'incremental component calls only; baseline cost is retained separately, never free end-to-end throughput',
            'frozen': ['patient_discovery','figure_pixels','figure_attribution','summary'],
            'clinical_facts_frozen':component=='episode-rebuild','whole_bundle_promotion_ready':False}
        write_json(runner.output/'component-provenance.json',provenance)
        report={'schema_version':'controlled-component-report/1','arm':component,'seed':runner.config.seed,
            'model':before['model'],'patients_extracted':len(runner.patients),'model_calls':runner.calls,
            'wall_seconds':wall,'tokens':runner.token_report(articles,wall),
            'task_count':len(runner.results),'valid_tasks':sum(x['status']=='valid' for x in runner.results),
            'task_statuses':dict(Counter(x['status'] for x in runner.results)),
            'component_provenance':provenance,'clinical_accuracy_verified':False,
            'outputs':{'patients':str(runner.output/'patients.jsonl'),'predicted_rosters':str(runner.output/'rosters.json'),
                       'visual_annotations':str(runner.output/'visual-annotations.json'),'report':str(runner.output/'report.json')}}
        write_json(runner.output/'run-config.json',{'config':runner.config.model_dump(),'component':component,'seed':runner.config.seed,
            'context':context,'selected_article_ids':list(by_article),'baseline_hashes':hashes})
        if hashes!={name:_hash(path) for name,path in paths.items()}:raise ValueError('Baseline changed during component trial')
        write_json(runner.output/'report.json',report)
        return report
    finally:await runner.close()
