"""Materialize the actual runtime clinical contracts from annotated source facts.

This supplies structure, never missing clinical facts. Generated targets require
agent and parent inspection; a successful conversion is not semantic approval.
"""
from __future__ import annotations
import argparse,copy,hashlib,json,types
from pathlib import Path
from typing import Annotated,Literal,Union,get_args,get_origin
from pydantic import BaseModel
from openpatients2.schemas import TASK_MODELS
from openpatients2.article_tasks import Roster,Summary,Vision
from openpatients2.figure_attribution import FigureReview
from openpatients2.longitudinal import PatientTimeline,annotation_event_time,relative_order
from openpatients2.validation import validate as validate_runtime
from validate import digest,validate

VERSION='dense-runtime-targets/1'


def complete(model, supplied, path=''):
    """Fill only absent nullable/unknown/list fields; never pick an arbitrary enum."""
    result=copy.deepcopy(supplied)
    def empty(annotation,loc):
        args=get_args(annotation);origin=get_origin(annotation)
        if origin is Annotated:return empty(args[0],loc)
        if origin in (Union,types.UnionType) and type(None) in args:return None
        if origin is Literal:
            if 'unknown' in args:return 'unknown'
            if len(args)==1:return args[0]
        if origin is list:return []
        if isinstance(annotation,type) and issubclass(annotation,BaseModel):return complete(annotation,{},loc)
        raise ValueError(f'{loc}: missing substantive required field; annotator must supply it')
    for name,field in model.model_fields.items():
        loc=path+'/'+name
        if name not in result:result[name]=empty(field.annotation,loc)
        elif isinstance(result[name],dict):
            typ=field.annotation
            if isinstance(typ,type) and issubclass(typ,BaseModel):result[name]=complete(typ,result[name],loc)
    return result


def unique(rows):
    seen=set();out=[]
    for row in rows:
        key=digest(row)
        if key not in seen:out.append(row);seen.add(key)
    return out


def materialize(a,s):
    errors=validate(a,s)
    if errors:raise ValueError(errors)
    segments={x['segment_id']:x for x in s['segments']};aid=a['article_id'];source_id=aid+':jats'
    def citations(rows,maximum=1600):
        result=[]
        for e in rows:
            for i in range(0,len(e['quote']),maximum):
                result.append({'segment_id':e['segment_id'],'quote':e['quote'][i:i+maximum]})
        return unique(result)
    def evidence(rows):return unique([{'quote':e['quote'],'source_section':e['segment_id']} for e in citations(rows,1200)])
    def spans(rows):
        out=[]
        for e in unique(rows):
            text=segments[e['segment_id']]['text'];start=text.index(e['quote'])
            out.append({'source_id':source_id,'segment_id':e['segment_id'],'segment_sha256':hashlib.sha256(text.encode()).hexdigest(),'start':start,'end':start+len(e['quote']),'quote':e['quote']})
        return out
    figures=[];assignments=[]
    for f in a['figures']:
        panels=f['panels'] or [{'panel':None,'patient_ids':f['patient_ids'],'scope':f['scope'],'evidence':f['caption_evidence']}]
        rows=[]
        for panel in panels:
            scope=panel['scope'];pids=panel['patient_ids'] if scope in ('individual','shared') else []
            rows.append({'panel':panel['panel'],'patient_ids':pids,'scope':scope,'subject':panel.get('subject') or ('patient' if pids else 'external_patient' if scope=='external' else scope if scope in ['background','aggregate','unresolved'] else 'unresolved'),'evidence':citations(panel['evidence']),'rationale':f.get('attribution_notes') or 'Source annotation; no identity inferred from pixels.'})
            assignments.append({'figure_id':f['figure_id'],'panel':panel['panel'],'patient_ids':pids,'scope':'unresolved' if scope=='external' else scope,'evidence':citations(panel['evidence'])})
        figures.append(FigureReview.model_validate({'figure_id':f['figure_id'],'assignments':rows,'limitations':['Text/caption attribution only; image pixels have not been inspected.']}).model_dump())
    patient_blocks={p['patient_id']:set() for p in a['patients']}
    for c in a['source_coverage']:
        for pid in c['patient_ids']:patient_blocks[pid].add(c['segment_id'])
    for p in a['patients']:
        patient_blocks[p['patient_id']].update(e['segment_id'] for e in p['identity_evidence'])
        patient_blocks[p['patient_id']].update(e['segment_id'] for f in p['facts'] for e in f['evidence'])
    for f in assignments:
        for pid in f['patient_ids']:patient_blocks[pid].update(e['segment_id'] for e in f['evidence'])
    classified=set().union(*patient_blocks.values()) if patient_blocks else set()
    # The annotation's `roster` block is the single source of the reported count/completeness; without it the
    # roster awaits explicit review (pending notice), and merges fall back to the reviewed submission's values.
    rb=a.get('roster')
    pending=[] if rb is not None else ['Reported distinct count and roster completeness await explicit schema review; cited-case discovery is not automatically completed.']
    roster=Roster.model_validate({'disposition':a['disposition'],'reported_individual_count':(rb or {}).get('reported_individual_count'),'roster_complete':bool((rb or {}).get('roster_complete',False)),
        'patients':[{'patient_id':p['patient_id'],'label':p['label'],'species':p['species'],'species_as_documented':p.get('species_as_documented'),'identity_evidence':citations(p['identity_evidence']),'source_segment_ids':sorted(patient_blocks[p['patient_id']]),'attribution_limitations':[x.get('issue') or x.get('description') or json.dumps(x,ensure_ascii=False) for x in p.get('ambiguities',[])]} for p in a['patients']],
        'cited_cases':[],'background_segment_ids':[x['segment_id'] for x in a['source_coverage'] if x['segment_id'] not in classified and x['disposition']=='background'],'unresolved_segment_ids':[x['segment_id'] for x in a['source_coverage'] if x['segment_id'] not in classified and x['disposition']!='background'],'figures':assignments,
        'limitations':a['article_limitations']+pending}).model_dump()
    bundles=[];source_gate_errors=[]
    for p in a['patients']:
        pid=p['patient_id'];rid=f'{aid}:{pid}';sections={};links=[];quarantine=[]
        for task,model in TASK_MODELS.items():
            selected=[f for f in p['facts'] if f['task']==task and f['scoring']=='include']
            excluded=[f for f in p['facts'] if f['task']==task and f['scoring']!='include'];quarantine+=excluded
            section={'coverage':'limited' if excluded else 'complete','limitations':(['Source-conflicted/ambiguous candidates are retained separately, not accepted as clinical facts.'] if excluded else []),'documentation_status':'documented' if selected else 'not_documented','documentation_evidence':[]}
            if not selected and excluded:section['limitations'].append('No accepted target in this section does not mean the source has no discussion; see reference quarantine.')
            if task=='case_context':
                merged={}
                for f in selected:
                    for k,v in f['fields'].items():
                        if k in merged and merged[k]!=v:raise ValueError(f'{rid}:{task}: conflicting {k}; source annotation needs resolution')
                        merged[k]=v
                    links.append({'fact_id':f['fact_id'],'task':task,'pointer':'/','source_evidence':f['evidence']})
                section.update(merged,evidence=evidence([e for f in selected for e in f['evidence']]))
                section=complete(model,section)
            else:
                collections=['tumors','biomarkers','treatments'] if task=='oncology' else ['items']
                for collection in collections:
                    cls=get_args(model.model_fields[collection].annotation)[0];items=[];groups=[]
                    for f in [f for f in selected if f['collection']==collection]:
                        # Multiple compatible partial facts can describe one tumor. Contradictions fail loudly.
                        target=next((g for g in groups if collection=='tumors' and g['fields'].get('tumor_ref')==f['fields'].get('tumor_ref')),None)
                        if target is None:groups.append({'fields':copy.deepcopy(f['fields']),'facts':[f]})
                        else:
                            for k,v in f['fields'].items():
                                if k in target['fields'] and target['fields'][k]!=v:raise ValueError(f'{rid}:{task}:{f["fact_id"]}: conflicting tumor field {k}; retain separate time-specific observation or explicit source review')
                                target['fields'][k]=v
                            target['facts'].append(f)
                    for group in groups:
                        fields=group['fields'];fields['evidence']=evidence([e for f in group['facts'] for e in f['evidence']])
                        item=complete(cls,fields,f'{rid}/{task}/{collection}/{len(items)}')
                        # A result is not silently fabricated to satisfy Observation's validator.
                        if task=='observations' and item['result_absent_reason'] is None and all(item[k] is None for k in ['numeric_value','text_value']) and item['flag']=='unknown':
                            raise ValueError(f'{rid}:{group["facts"][0]["fact_id"]}: missing observation result or absence reason')
                        items.append(cls.model_validate(item).model_dump())
                        links += [{'fact_id':f['fact_id'],'task':task,'pointer':f'/{collection}/{len(items)-1}','source_evidence':f['evidence']} for f in group['facts']]
                    section[collection]=items
            sections[task]=model.model_validate(section).model_dump()
            packet='\n\n'.join('['+x['segment_id']+'] '+(x['heading'] or 'Article')+'\n'+x['text'] for x in s['segments'])
            checks=validate_runtime(task,sections[task],packet,segments=s['segments'],target_patient=True)
            source_gate_errors += [{'patient_id':pid,'task':task,'error':error} for error in checks.errors]
        from openpatients2.semantic_rules import ERROR,record_violations
        source_gate_errors += [{'patient_id':pid,'task':'case_context' if v.path.startswith('/case_context') else 'record','error':v.text()}
                               for v in record_violations(sections) if v.severity==ERROR and v.rule_id!='CTX-PER-PATIENT']
        included={x['fact_id'] for x in links};facts={f['fact_id']:f for f in p['facts']};events=[];edges=[];notes=[]
        for e in p['events']:
            # One timeline vocabulary (semantic_rules.EVENT_OCCURRENCES): copied, never remapped.
            occurrence=e['occurrence']
            domains={facts[f]['task'] for f in e['fact_ids']};kind='plan' if occurrence=='planned' else {'observations':'test','medications':'treatment','procedures_devices':'treatment','outcomes':'outcome','conditions':'diagnosis'}.get(next(iter(domains)),'other') if len(domains)==1 else 'other'
            # The annotation is the single source of event/time classification (kind, time_kind, time_precision,
            # time_calendar_value); the heuristic kind and order_only/unknown time are only defaults.
            kind=e.get('kind',kind)
            times=[]
            if e.get('time_text'):times=[annotation_event_time(e,spans(e['evidence']))]
            events.append({'event_id':e['event_id'],'record_id':rid,'episode_id':None,'kind':kind,'occurrence':occurrence,'description':e['description'],'fact_ids':[f for f in e['fact_ids'] if f in included],'evidence':spans(e['evidence']),'attribution_evidence':spans(p['identity_evidence']),'times':times})
        for i,e in enumerate(p['temporal_relations']):
            edges.append({'edge_id':f'edge{i+1}','from_event_id':e['from_event_id'],'to_event_id':e['to_event_id'],'relation':e['relation'],'offset':None,'evidence':spans(e['evidence'])})
        timeline=PatientTimeline.model_validate({'schema_version':'patient-timeline/2','record_id':rid,'events':events,'edges':edges,'limitations':notes+['Literal time expressions preserved without guessing semantic type, numeric offsets or dates; annotator reviews event kinds and time types.']}).model_dump()
        summary=Summary.model_validate({'claims':[{'text':claim['text'],'evidence':citations([e for fid in claim['fact_ids'] for e in facts[fid]['evidence']])} for claim in p['summary']],'limitations':a['article_limitations']}).model_dump()
        relevant=[f for f in a['figures'] if pid in f['patient_ids'] or any(pid in x['patient_ids'] for x in f['panels'])]
        caption_target=Vision.model_validate({'pixel_observations':[],'caption_claims':[{'text':f['caption_summary'],'evidence':citations(f['caption_evidence'])} for f in relevant],'limitations':['No pixels inspected. Detailed visual/modalities/chart-value targets require image review; empty pixel observations are unavailable, not negative findings.']}).model_dump()
        bundles.append({'schema_version':'2.1.0','pilot_contract':'bounded-extraction/1','source':{'record_id':rid,'article_id':aid,'pmcid':s['pmcid'],'pmid':s.get('pmid'),'doi':s.get('doi'),'source_url':s['source_url'],'xml_url':s.get('xml_url'),'text_sha256':s['text_sha256'],'xml_sha256':s['xml_sha256'],'license':s['license'],'patient_target':next(x for x in roster['patients'] if x['patient_id']==pid),'source_text_omitted_from_release':True},
            'model':{'model_id':'gpt-6.1-sol source annotation; deterministic contract materialization','revision':None},'expected_tasks':list(TASK_MODELS),'sections':sections,'companions':{'summary':summary,'timeline_v2':timeline},'relative_timeline':relative_order(timeline),'reference_provenance':{'annotation_sha256':digest(a),'materializer_version':VERSION,'fact_pointers':links,'quarantined_facts':quarantine,'synthetic_dates_generated':False,'missing_fields_policy':'Required nullable attributes=null; absent optional categorical knowledge=unknown; no clinical values inferred.'},'vision':{'status':'not_inspected','caption_only':caption_target,'figure_attribution':[f for f in figures if any(pid in x['patient_ids'] for x in f['assignments'])]},'all_clinical_companion_tasks_valid':True,'complete_for_scope':False,'semantic_coverage_verified':False,'clinical_review_status':'schema_review_pending','clinical_accuracy_verified':False})
        state=[]
        for link in links:
            value=sections[link['task']]
            for part in link['pointer'].strip('/').split('/') if link['pointer']!='/' else []:
                value=value[int(part)] if isinstance(value,list) else value[part]
            state.append({'fact_id':link['fact_id'],'task':link['task'],'pointer':link['pointer'],
                'value_as_extracted':copy.deepcopy(value),'value_as_annotated':facts[link['fact_id']]['fields'],
                'evidence':spans(facts[link['fact_id']]['evidence']),
                'event_ids':[e['event_id'] for e in events if link['fact_id'] in e['fact_ids']]})
        bundles[-1]['patient_state_index']={'facts':state,'state_carried_forward':False,
            'synthetic_dates_generated':False,'clinical_fidelity_verified':False}
        bundles[-1]['quality']={task:{'status':'failed' if any(e['patient_id']==pid and e['task']==task for e in source_gate_errors) else 'valid','errors':[e['error'] for e in source_gate_errors if e['patient_id']==pid and e['task']==task],'semantic_review_status':'pending'} for task in TASK_MODELS}
        bundles[-1]['all_clinical_companion_tasks_valid']=not any(e['patient_id']==pid for e in source_gate_errors)
    return {'format':VERSION,'article_id':aid,'annotation_sha256':digest(a),'source_text_sha256':s['text_sha256'],'schema_fingerprints':{k:digest(v.model_json_schema()) for k,v in {**TASK_MODELS,'roster':Roster,'summary':Summary,'timeline_v2':PatientTimeline,'figure_attribution':FigureReview,'caption_vision':Vision}.items()},'roster':roster,'figure_attribution':figures,'patients':bundles,'source_gate_errors':source_gate_errors,'review_status':'pending'}


def with_review_only_items(bundle):
    """Copy of a patient bundle whose sections also carry its quarantined (review_only) facts.

    Release contract (unified record): quarantined facts are exported, not dropped, as items in
    their section/collection with `scoring: "review_only"` (plus fact_id and certainty), so a scorer
    can let them absorb predictions without requiring them for recall. Fields are completed with
    the same null/unknown defaults as accepted items but are NOT model-validated (they may be
    source-conflicted). A review_only case_context fact has no item collection and is listed in
    section `review_only_facts` instead."""
    out=copy.deepcopy(bundle)
    for f in bundle.get('reference_provenance',{}).get('quarantined_facts',[]):
        task=f['task'];model=TASK_MODELS[task];section=out['sections'][task]
        evidence=[{'quote':e['quote'],'source_section':e['segment_id']} for e in f['evidence']]
        extra={'scoring':'review_only','fact_id':f['fact_id'],'certainty':f.get('certainty')}
        if task=='case_context':
            section.setdefault('review_only_facts',[]).append({**copy.deepcopy(f['fields']),'evidence':evidence,**extra});continue
        cls=get_args(model.model_fields[f['collection']].annotation)[0]
        try:item=complete(cls,{**copy.deepcopy(f['fields']),'evidence':evidence})
        except ValueError:item={**copy.deepcopy(f['fields']),'evidence':evidence}
        section[f['collection']].append({**item,**extra})
    return out


def main():
    parser=argparse.ArgumentParser();parser.add_argument('root',type=Path);parser.add_argument('--folder',default='drafts');parser.add_argument('--article');args=parser.parse_args();failures=[];count=0
    out=args.root/'schema-drafts';out.mkdir(exist_ok=True)
    for path in sorted((args.root/args.folder).glob((args.article or '*')+'.json')):
        try:
            result=materialize(json.loads(path.read_text()),json.loads((args.root/'sources'/path.name).read_text()))
            target=out/path.name;tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');tmp.replace(target);count+=1
            if result['source_gate_errors']:failures.append({'article_id':path.stem,'source_gate_errors':result['source_gate_errors']})
        except Exception as exc:failures.append({'article_id':path.stem,'error':str(exc)})
    print(json.dumps({'materialized':count,'failed':len(failures),'failures':failures,'semantic_review_performed':False},ensure_ascii=False))
    raise SystemExit(bool(failures))
if __name__=='__main__':main()
