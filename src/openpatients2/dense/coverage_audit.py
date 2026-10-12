"""Expose omissions for source review; never score semantic recall from JSON shape."""
from openpatients2.schemas import TASK_MODELS

META={'coverage','limitations','documentation_status','documentation_evidence','evidence'}


def clinical_coverage(bundle):
    sections=bundle['sections'];domains={};attention=[]
    for task,model in TASK_MODELS.items():
        section=sections.get(task)
        if section is None:
            attention.append({'kind':'missing_schema','task':task});continue
        collections=['tumors','biomarkers','treatments'] if task=='oncology' else ['items']
        rows=[(name,row) for name in collections for row in section.get(name,[])]
        if task=='case_context':rows=[('context',section)]
        fields={}
        for collection,row in rows:
            for field,value in row.items():
                if field in META:continue
                key=f'{collection}.{field}'
                counts=fields.setdefault(key,{'populated':0,'null_or_unknown':0})
                counts['null_or_unknown' if value is None or value==[] or value=='' or value=='unknown' else 'populated']+=1
        domains[task]={'documentation_status':section['documentation_status'],
            'row_count':len(rows),'field_counts':fields,
            'source_omission_review_required':True}
    oncology=sections.get('oncology',{})
    if oncology.get('tumors') and not sections.get('conditions',{}).get('items'):
        attention.append({'kind':'tumor_without_general_condition','task':'conditions',
            'note':'Review documented diagnoses for Conditions as well as detailed Oncology. Do not infer a diagnosis from imaging alone.'})
    linked={f['fact_id'] for f in bundle.get('patient_state_index',{}).get('facts',[]) if f.get('event_ids')}
    unlinked=[f['fact_id'] for f in bundle.get('patient_state_index',{}).get('facts',[]) if f['fact_id'] not in linked]
    if unlinked:attention.append({'kind':'facts_without_phase','fact_ids':unlinked,
        'note':'Link to a supported clinical phase or retain an explicit unresolved temporal scope; do not invent a visit.'})
    return {'record_id':bundle['source']['record_id'],'domains':domains,'attention':attention,
        'clinical_recall_measured':False,'nulls_are_not_automatically_omissions':True}


def write_report(out,bundles):
    import json
    audits=[clinical_coverage(b) for b in bundles]
    (out/'coverage-audit.json').write_text(json.dumps(audits,ensure_ascii=False,indent=2)+'\n')
    lines=['# Schema coverage and outstanding review','',
        'Schema presence and populated field counts are structural checks, not clinical recall. Every documented attribute still needs source review. Null/unknown is appropriate only when the source does not establish the value.','',
        '| Patient | Clinical schemas | Review flags |','| --- | ---: | --- |']
    for row in audits:
        lines.append(f"| {row['record_id']} | {len(row['domains'])}/14 | {', '.join(x['kind'] for x in row['attention']) or 'No automated flags; semantic omission review still required'} |")
    lines += ['', 'The full field inventory and counts are in coverage-audit.json; all runtime contracts, including detailed figure and chart fields, are in schemas.json.', '',
        'An empty clinical domain is not a negative finding. A diagnosis appearing in Oncology does not excuse missing the supported general Conditions representation. Source recommendations, actual treatments and later outcomes remain separate.', '',
        'Detailed visual targets require actual pixels and independent annotator/parent visual receipts. Caption-only targets are explicitly incomplete for the visual task.']
    (out/'COVERAGE.md').write_text('\n'.join(lines)+'\n')
