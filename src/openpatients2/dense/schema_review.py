"""Validate and record an actual inspection of full runtime-shaped submissions.

Never run --role as a batch substitute for reading the source and submission.
"""
import argparse,copy,json
from datetime import datetime,timezone
from pathlib import Path
from openpatients2.schemas import TASK_MODELS
from openpatients2.article_tasks import Roster,Summary,Vision
from openpatients2.figure_attribution import FigureReview
from openpatients2.longitudinal import PatientTimeline,audit_timeline,relative_order
from openpatients2.dense.schema_targets import materialize
from openpatients2.dense.validate import digest
from openpatients2 import dense_store as store


def validate_submission(target,annotation,source):
    expected=materialize(annotation,source)
    if expected['source_gate_errors'] or target.get('source_gate_errors'):
        raise ValueError(('Runtime source gates failed',expected['source_gate_errors']))
    for key in ('format','article_id','annotation_sha256','source_text_sha256','schema_fingerprints'):
        if target[key]!=expected[key]:raise ValueError('Stale/different '+key)
    if len(target['patients'])!=len(expected['patients']):raise ValueError('Patient cardinality changed')
    Roster.model_validate(target['roster'])
    segments={s['segment_id']:s['text'] for s in source['segments']}
    sources={source['article_id']+':jats':segments}
    def citations(value):
        if isinstance(value,dict):
            if 'segment_id' in value and 'quote' in value:
                if value['quote'] not in segments.get(value['segment_id'],''):raise ValueError('Nonliteral citation')
            for child in value.values():citations(child)
        elif isinstance(value,list):
            for child in value:citations(child)
    citations(target)
    for f in target['figure_attribution']:FigureReview.model_validate(f)
    for got,want in zip(target['patients'],expected['patients']):
        for key in ('schema_version','pilot_contract','source','model','expected_tasks','sections','reference_provenance','patient_state_index','quality','all_clinical_companion_tasks_valid'):
            if got[key]!=want[key]:raise ValueError(f'{key}: clinical/provenance edits must first change source-linked annotation and regenerate')
        if set(got['sections'])!=set(TASK_MODELS):raise ValueError('Missing clinical section')
        for task,cls in TASK_MODELS.items():cls.model_validate(got['sections'][task])
        Summary.model_validate(got['companions']['summary'])
        if got['companions']['summary']!=want['companions']['summary']:raise ValueError('Summary edits belong in annotation')
        timeline=PatientTimeline.model_validate(got['companions']['timeline_v2'])
        facts={f['fact_id']:got['source']['record_id'] for f in got['reference_provenance']['fact_pointers']}
        audit=audit_timeline(timeline,sources,facts)
        if not audit['structural_source_gates_passed']:raise ValueError(('Timeline source gates',audit))
        relative_order(timeline.model_dump())
        if got['relative_timeline']!=relative_order(timeline.model_dump()):raise ValueError('Regenerate relative_timeline after changing event classifications')
        original=want['companions']['timeline_v2'];copy_got=copy.deepcopy(timeline.model_dump())
        # Classification set in the annotation is the single source: the submission must match it exactly.
        own={e['event_id']:e for p in annotation['patients'] for e in p.get('events',[])}
        for e,old in zip(copy_got['events'],original['events']):
            a_ev=own.get(e['event_id'],{})
            if 'kind' in a_ev and e['kind']!=old['kind']:raise ValueError(f"{e['event_id']}: event kind is set in the annotation; change it there and regenerate")
            for time,oldtime in zip(e['times'],old['times']):
                for field,key in (('kind','time_kind'),('precision','time_precision'),('calendar_value','time_calendar_value')):
                    if key in a_ev and time[field]!=oldtime[field]:raise ValueError(f"{e['event_id']}: time {field} is set in the annotation; change it there and regenerate")
        for e,old in zip(copy_got['events'],original['events']):
            e['kind']=old['kind']
            for time,oldtime in zip(e['times'],old['times']):
                for field in ('kind','precision','calendar_value'):time[field]=oldtime[field]
        if copy_got!=original:raise ValueError('Only kind/time classification edits allowed directly; timeline clinical/order edits belong in annotation')
        Vision.model_validate(got['vision']['caption_only'])
        if got['vision']['status']!='not_inspected' or got['vision']['caption_only']!=want['vision']['caption_only']:raise ValueError('Cannot invent pixel review or change caption source claims')
        pid=got['source']['patient_target']['patient_id']
        if got['vision']['figure_attribution'] != [f for f in target['figure_attribution'] if any(pid in x['patient_ids'] for x in f['assignments'])]:raise ValueError('Synchronize patient figure attribution with article assignments')
        if got['complete_for_scope'] or got['clinical_accuracy_verified'] or got['semantic_coverage_verified']:raise ValueError('Do not infer completeness/accuracy from schema review')
    # Attribution edits may refine subject type/rationale but not change ownership without source annotation.
    for got,want in zip(target['figure_attribution'],expected['figure_attribution']):
        clean=copy.deepcopy(got)
        for x,y in zip(clean['assignments'],want['assignments']):
            x['subject']=y['subject'];x['rationale']=y['rationale']
        if clean!=want:raise ValueError('Figure ownership changes belong in the source annotation')
    if len(target['figure_attribution'])!=len(expected['figure_attribution']):raise ValueError('Missing figure')
    r=copy.deepcopy(target['roster']);er=expected['roster']
    if annotation.get('roster') is not None:
        for key in ('reported_individual_count','roster_complete'):
            if target['roster'][key]!=er[key]:raise ValueError(f'roster.{key} is set in the annotation roster block; change it there and regenerate')
    for key in ('reported_individual_count','roster_complete'):r[key]=er[key]
    # The reviewer may remove this generated pending-review notice. Actual
    # source limitations remain immutable unless the annotation changes first.
    pending='Reported distinct count and roster completeness await explicit schema review; cited-case discovery is not automatically completed.'
    if pending in er['limitations'] and pending not in r['limitations']:
        r['limitations'].append(pending)
    if r!=er:raise ValueError('Roster clinical/ownership edits belong in source annotation')
    return {'clinical_sections':sum(len(p['sections']) for p in target['patients']),'patients':len(target['patients']),'source_gates_passed':True,'semantic_accuracy_measured':False}


def main():
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--article',required=True);p.add_argument('--folder',default='drafts');p.add_argument('--role',choices=['agent','parent']);p.add_argument('--reviewer');p.add_argument('--notes');args=p.parse_args()
    if args.role:
        from openpatients2.dense.signing_guard import check_signature
        check_signature(args.reviewer,args.notes)   # refuse probes before anything is read or written
    aid=args.article;source=json.loads(store.path(args.root,'sources',aid).read_text());a=json.loads(store.path(args.root,args.folder,aid).read_text());target=json.loads(store.path(args.root,'schema-submissions',aid).read_text())
    report=validate_submission(target,a,source)
    if args.role:
        if not args.reviewer or not args.notes:raise ValueError('Review receipts need reviewer and substantive inspection notes')
        from openpatients2.dense.validate import case_context_errors
        if case_context_errors(a):raise ValueError(case_context_errors(a))
        # The same runtime checker objects the local-LLM pipeline must pass (reference_gates).
        from openpatients2.dense.reference_gates import run_gates
        gates=run_gates(args.root,aid,annotation=a,groups=('clinical',))
        if not gates['passed']:raise ValueError(('Runtime gates failed; fix the annotation, regenerate and re-run',gates['errors'][:10]))
        receipt={'article_id':aid,'reviewer':args.reviewer,'role':args.role,'reviewed_at':datetime.now(timezone.utc).isoformat(),'annotation_folder':args.folder,'annotation_sha256':digest(a),'target_sha256':digest(target),'source_text_sha256':source['text_sha256'],'inspection_notes':args.notes,'status':'schema_source_reviewed','physician_adjudicated':False,**report}
        out=store.receipt(args.root,'schema',args.role,aid);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({'article_id':aid,**report,'review_receipt_written':args.role}))
if __name__=='__main__':main()
