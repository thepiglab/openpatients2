"""Source-checklist fidelity screening and reproducible claim audit sampling.

Partial labels estimate checklist retention, never full precision/recall. Names
and exact units matter; source/evidence text cannot satisfy a generated claim.
Semantic adjudication is explicit, versioned, and distinct from automatic hits.
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import random
import re
from collections import defaultdict

from .output_parser import parse_output
from .schemas import TASK_MODELS


def _litre_spelling(text):
    # SI prefixes are case-sensitive (g is not G, m is not M), while either
    # l or L may denote litre. Handle only the literal litre suffix in these
    # checklist patterns; this is not a general unit conversion engine.
    parts = text.split('|')
    for i, part in enumerate(parts):
        if '/' in part and part.endswith('l'):
            parts[i] = part[:-1] + 'L'
        elif part == 'ml':
            parts[i] = 'mL'
    return '|'.join(parts)


def matches(pattern, value, field=''):
    if isinstance(pattern,dict):
        if set(pattern)=={'regex'}:
            if not isinstance(value,str):return False
            if field in {'unit','dose_unit'}:
                return bool(re.fullmatch(_litre_spelling(pattern['regex']), _litre_spelling(value.strip())))
            return bool(re.search(pattern['regex'],value.strip(),re.I))
        if set(pattern)=={'one_of'}:
            return any(matches(p,value,field) for p in pattern['one_of'])
        if not isinstance(value,dict):return False
        return all(k in value and matches(v,value[k],k) for k,v in pattern.items())
    if isinstance(pattern,(float,int)) and not isinstance(pattern,bool):
        return isinstance(value,(float,int)) and not isinstance(value,bool) and math.isclose(pattern,value,rel_tol=1e-7,abs_tol=1e-9)
    if isinstance(pattern,str) and isinstance(value,str):
        return ' '.join(pattern.split()).casefold()==' '.join(value.split()).casefold()
    return pattern==value


def without_evidence(value):
    if isinstance(value,dict):return {k:without_evidence(v) for k,v in value.items() if k not in {'evidence','documentation_evidence'}}
    if isinstance(value,list):return [without_evidence(v) for v in value]
    return value


def last_parseable(responses):
    # Audit the last attempt only, not an oracle choosing a more accurate retry.
    if not responses:return None
    response=responses[-1]
    if response.get('error') or response.get('finish_reason') not in {'stop','eos'}:return None
    try:return parse_output(response.get('content',''),finish_reason=response['finish_reason']).value
    except (ValueError,TypeError):return None


def load_predictions(folder: Path):
    records={}
    for path in folder.glob('PMC*-p*.json'):
        row=json.loads(path.read_text())
        if 'sections' not in row:continue
        rid=row['source']['record_id'];raw={};delivered=row['sections']
        for task,value in delivered.items():
            raw[task]=value if value is not None else last_parseable([row.get('generations',{}).get(task)] if row.get('generations',{}).get(task) else [])
        for task,result in row.get('companions',{}).items():
            raw[task]=result['data'] if result['status']=='valid' else last_parseable(result.get('attempt_responses',[]))
        records[rid]={'path':str(path),'patient':row,'raw':raw,'delivered':delivered}
    return records


def evaluate(reference: dict, predictions: dict) -> list[dict]:
    results=[]
    for check in reference['checks']:
        record=predictions.get(check['record_id'],{})
        row={'check_id':check['id'],'record_id':check['record_id'],'kind':check['kind'],'category':check['category']}
        for mode in ['raw','delivered']:
            section=record.get(mode,{}).get(check['task'])
            items=section.get(check['collection'],[]) if isinstance(section,dict) else []
            if not isinstance(items,list):items=[]
            found=[n for n,x in enumerate(items) if matches(check['pattern'],without_evidence(x))]
            row[mode]={'available':section is not None,'matched':bool(found),'item_indices':found}
        results.append(row)
    return results


def summarize(rows,mode):
    required=[x for x in rows if x['kind']=='required'];negative=[x for x in rows if x['kind']=='forbidden']
    matched=sum(x[mode]['matched'] for x in required);available=sum(x[mode]['available'] for x in required)
    return {'required_checks':len(required),'matched':matched,'missing_or_wrong':len(required)-matched,
        'checklist_retention':matched/len(required) if required else None,
        'checks_with_parseable_section':available,
        'retention_conditional_on_available_section':matched/available if available else None,
        'forbidden_checks':len(negative),'forbidden_violations':sum(x[mode]['matched'] for x in negative),
        'forbidden_checks_unavailable':sum(not x[mode]['available'] for x in negative),
        'warning':'Partial checklist; neither full clinical recall nor precision. Unavailable sections are missing delivery, not evidence of medically false content.'}


def validate_reference(reference,articles):
    seen=set()
    for c in reference['checks']:
        if c['id'] in seen:raise ValueError('Duplicate check ID')
        seen.add(c['id']);aid=c['record_id'].split(':')[0];a=articles[aid]
        if c['text_sha256']!=a['text_sha256'] or c['xml_sha256']!=a['xml_sha256']:raise ValueError('Reference source changed')
        segments={s['segment_id']:s['text'] for s in a['segments']}
        if not c['source'] or any(e['quote'] not in segments.get(e['segment_id'],'') for e in c['source']):raise ValueError('Nonliteral reference evidence')


def review_sample(reference,model_predictions,seed=20260929):
    """Two seeded clinical claims + one summary claim per case and model.

    Prefer distinct clinical domains, sample uniformly inside each selected
    domain, and retain evidence separately. Availability is reported separately.
    This is a balanced diagnostic sample, not corpus-uniform precision.
    """
    models=sorted(model_predictions);rng=random.Random(seed);shuffled=models.copy();rng.shuffle(shuffled)
    aliases={model:f'M{n+1}' for n,model in enumerate(shuffled)}
    audit=[];availability=[]
    for model in models:
        for case in reference['cases']:
            rid=case['record_id'];record=model_predictions[model].get(rid,{})
            local=random.Random(f'{seed}:{rid}')
            # Identical preferred domain ordering across models for each case.
            tasks=['conditions','medications','observations','procedures_devices','outcomes']
            local.shuffle(tasks)
            choices=[]
            for task in tasks:
                section=record.get('raw',{}).get(task) or {}
                items=section.get('items',[])
                if not isinstance(items,list) or not items:continue
                index=local.randrange(len(items));choices.append((task,'items',index,items[index]))
                if len(choices)==2:break
            summary=(record.get('raw',{}).get('summary') or {}).get('claims',[])
            if summary:
                n=local.randrange(len(summary));choices.append(('summary','claims',n,summary[n]))
            availability.append({'model':model,'record_id':rid,'sampled_claims':len(choices),'target_claims':3})
            for task,collection,index,item in choices:
                item_id=hashlib.sha256(f'{model}:{rid}:{task}:{index}'.encode()).hexdigest()[:16]
                audit.append({'audit_id':item_id,'model_alias':aliases[model],'record_id':rid,
                    'patient_label':case['label'],'task':task,'collection':collection,'item_index':index,
                    'claim':without_evidence(item),'cited_evidence':item.get('evidence',[]),
                    'delivered_section':record.get('delivered',{}).get(task) is not None if task!='summary' else record.get('patient',{}).get('companions',{}).get('summary',{}).get('status')=='valid',
                    'verdict':None,'rationale':None})
    audit.sort(key=lambda x:(x['record_id'],x['task'],x['audit_id']))
    return audit,aliases,availability
