"""Compile observed, source-backed EHR seeds without synthesizing missing facts.

This is an intermediate representation, not FHIR conformance or a simulation.
Clinical facts, model image observations and future synthetic events stay distinct.
"""
from __future__ import annotations
import json
from pathlib import Path

from .data import read_jsonl, write_json
from .provenance import json_digest
from .validation import validate, all_spans
from .warehouse import DOMAINS
from .schemas import TASK_MODELS
from .data import digest_text
from .article_tasks import check_article_task
from .articles import recheck_license
from .evidence_recovery import packet_segments


def seed_patient(patient: dict, visual_annotations: list[dict] | None = None, visual_model: str | None = None) -> dict:
    source=patient['source'];rid=source['record_id']
    article=source.get('article_source') or source.get('original_row',{}).get('article_source')
    if not article or not recheck_license(article['license'])['allowed']:
        raise ValueError('EHR seed requires explicit eligible article provenance')
    article={**article,'license':recheck_license(article['license'])}
    if digest_text(source['text']) != source['source_hash']:
        raise ValueError('Source digest mismatch during EHR seed export')
    facts=[]
    for domain,(task,collection) in DOMAINS.items():
        section=patient['sections'].get(task)
        if section is None:continue
        checked=validate(task,section,source['text'],segments=packet_segments(source))
        if not checked.valid:raise ValueError('Invalid clinical section: '+task)
        for n,fact in enumerate(section[collection]):
            support=[]
            for e in fact['evidence']:
                offsets=all_spans(source['text'],e['quote'])
                for start,end in offsets:
                    blocks=[p for p in source.get('packet_spans',[]) if p['start']<=start and end<=p['end']
                            and p['segment_id']==e['source_section']]
                    if source.get('packet_spans') and not blocks:continue
                    support.append({'quote':e['quote'],'packet_start':start,'packet_end':end,
                        'segment_spans':[{'segment_id':b['segment_id'],'start':start-b['start'],'end':end-b['start'],
                                         **{k:b[k] for k in ('page','region') if k in b},
                                         **({'asset_span':[b['asset_span'][0]+start-b['start'],
                                                           b['asset_span'][0]+end-b['start']]}
                                            if 'asset_span' in b else {})} for b in blocks]})
            facts.append({'fact_id':json_digest({'record':rid,'hash':source['source_hash'],'domain':domain,'ordinal':n,'fact':fact}),
                'domain':domain,'source_ordinal':n,'value':fact,'support':support,'origin':'article_text',
                'source_license':article['license']['code']})
    companions=patient.get('companions',{})
    timeline=companions.get('timeline') or {}
    timeline=timeline.get('data') if timeline.get('status')=='valid' else None
    timeline=timeline or {'events':[],'limitations':['Timeline unavailable']}
    summary=companions.get('summary') or {}
    summary=summary.get('data') if summary.get('status')=='valid' else None
    packet_article={'segments':[{'segment_id':s['segment_id'],'text':source['text'][s['start']:s['end']]}
                                for s in source.get('packet_spans',[])]}
    canonical = source.get('canonical_evidence')
    if canonical:
        if canonical['xml_sha256'] != article['xml_sha256']:
            raise ValueError('Canonical companion evidence has a different XML version')
        rendered='\n\n'.join(f'[{s["segment_id"]}] {s["heading"] or "Article"}\n{s["text"]}' for s in canonical['segments'])
        if digest_text(rendered) != article['text_sha256']:
            raise ValueError('Canonical companion evidence digest mismatch')
        packet_article = {'segments':canonical['segments']}
    timeline=check_article_task('timeline',timeline,packet_article)
    if summary is not None:
        summary=check_article_task('summary',summary,packet_article)
    events=[]
    for event in timeline['events']:
        e={**event,'event_id':rid+':'+event['event_id'],
           'anchor_event_id':rid+':'+event['anchor_event_id'] if event['anchor_event_id'] else None,
           'origin':'article_text','source_license':article['license']['code']}
        # Candidate alignment by overlapping exact source support, never inferred
        # causality. A reviewer/compiler can resolve one-to-many correspondences.
        e['candidate_fact_ids']=[f['fact_id'] for f in facts if any(
            q['segment_id']==s['segment_id'] and (c['quote'] in q['quote'] or q['quote'] in c['quote'])
            for q in event['evidence'] for c in f['support'] for s in c['segment_spans'])]
        events.append(e)
    target=source.get('patient_target') or source.get('original_row',{}).get('patient_target',{})
    pid=target.get('patient_id')
    visuals=[]; figure_annotations=[]
    assigned_figures={f['figure_id'] for f in source.get('figure_assignments',[])}
    selected_visual_model=visual_model or patient['model']['model_id']
    panel_guard=source.get('multimedia',{}).get('status')=='panel_attribution_available'
    def allowed_panel(figure_id, observation):
        if not panel_guard:return True
        allowed=[a.get('panel') for a in source.get('figure_assignments',[]) if a['figure_id']==figure_id]
        panel=observation.get('panel')
        return None in allowed or (panel is not None and str(panel).casefold() in
                                   {str(p).casefold() for p in allowed if p is not None})
    for annotation in visual_annotations or []:
        if annotation.get('article_id')!=article['article_id']:continue
        if annotation.get('model')!=selected_visual_model:continue
        result=annotation.get('annotation',{})
        if result.get('status')!='valid':continue
        observations=[x for x in result['data']['pixel_observations'] if pid in x['patient_ids']
                      and allowed_panel(annotation['figure_id'],x)]
        if observations or annotation['figure_id'] in assigned_figures:
            figure_annotations.append({'figure_id':annotation['figure_id'],'model_id':selected_visual_model,
                'pixel_provenance':annotation['pixel_provenance'],'annotation':{
                    **result['data'], 'pixel_observations':observations} if panel_guard else result['data'],
                'caption_claims_scope':'whole_figure_context_not_patient_facts',
                'patient_association':'visual_model_and_roster_candidate' if observations else 'roster_candidate_only',
                'status':'unreviewed','source_license':annotation['source_license']})
        if observations:
            visuals.append({'figure_id':annotation['figure_id'],'model_id':selected_visual_model,'pixel_provenance':annotation['pixel_provenance'],
                'pixel_observations':observations,'caption_claims':result['data']['caption_claims'],
                'status':'unreviewed_model_visual_observations','source_license':annotation['source_license']})
    return {'format':'openpatients2.ehr-seed/1','record_id':rid,'article':article,
        'clinical_source_selection':source.get('clinical_source_selection', {'selected':'jats'}),
        'clinical_source_provenance':source.get('clinical_source_provenance'),
        'companion_source':'canonical_jats',
        'patient_identity':target, 'species':target.get('species','unknown'),
        'facts':facts,'events':events,'summary':summary,'visual_findings':visuals,
        'figure_annotations':figure_annotations,
        'patient_figure_assignments':source.get('figure_assignments',[]),
        'patient_media':source.get('multimedia',{}),
        'supplements':article.get('supplements',[]),'terminology':patient.get('terminology'),
        'quality':{'all_clinical_tasks_valid':patient['complete_for_scope'] and set(patient.get('expected_tasks',[]))==set(TASK_MODELS),
            'section_coverage':{t:s['coverage'] if s else 'failed' for t,s in patient['sections'].items()},
            'source_coverage_complete':patient['complete_for_scope'] and set(patient.get('expected_tasks',[]))==set(TASK_MODELS) and all(s and s['coverage']=='complete' for s in patient['sections'].values()) and not source.get('clinical_source_selection',{}).get('review_queue'),
            'source_format_review_required':bool(source.get('clinical_source_selection',{}).get('review_queue')),
            'roster_complete':patient.get('roster_complete',False),
            'figure_attribution_complete':source.get('multimedia',{}).get('attribution_complete',False),
            'cross_section_findings':patient.get('cross_section_findings',[]),
            'timeline_limitations':timeline['limitations'],'clinical_review_status':patient.get('clinical_review_status','unreviewed')},
        'synthetic_events':[],
        'representation_notice':'Observed published case, not itself a synthetic patient. No imputed values, absolute synthetic dates, merged identities or FHIR compliance are claimed.'}


def export_seeds(input_path: str, output: str, visual_path: str | None = None, include_partial=False, visual_model: str | None = None):
    out=Path(output)
    if out.exists():raise ValueError('Use a new EHR seed output')
    out.parent.mkdir(parents=True,exist_ok=True)
    visuals=json.loads(Path(visual_path).read_text()) if visual_path else []
    written=skipped=0;reasons=[]
    with out.open('w') as f:
        for patient in read_jsonl(input_path):
            article=patient['source'].get('article_source') or patient['source'].get('original_row',{}).get('article_source')
            if not article or not recheck_license(article['license'])['allowed']:
                skipped+=1
                reasons.append({'record_id':patient['source']['record_id'],'reason':'license_ineligible_under_current_policy'})
                continue
            companions=patient.get('companions',{})
            complete=patient['complete_for_scope'] and set(patient.get('expected_tasks',[]))==set(TASK_MODELS) and patient.get('roster_complete') and all(
                companions.get(t,{}).get('status')=='valid' for t in ['summary','timeline'])
            if not include_partial and not complete:
                skipped+=1;reasons.append({'record_id':patient['source']['record_id'],'reason':'incomplete_extraction'});continue
            f.write(json.dumps(seed_patient(patient,visuals,visual_model),ensure_ascii=False)+'\n');written+=1
    result={'written':written,'skipped':skipped,'skipped_records':reasons,'partial_exports_enabled':include_partial,
            'clinically_adjudicated':False,'synthetic_patients_generated':0}
    write_json(out.with_suffix('.report.json'),result);return result
