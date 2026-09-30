"""Offline transparent counts; failures stay in every benchmark denominator."""
import json
from collections import defaultdict
from pathlib import Path
from openpatients2.data import write_json

ROOT=Path('runs/attribution-v1')


def norm(panel):
    if panel is None:return None
    return str(panel).lower().removeprefix('panel').strip(' ().')


def score_figures():
    gold=json.loads((ROOT/'figure-gold.json').read_text())['units']
    rows=json.loads((ROOT/'figure-outcomes.json').read_text())
    totals=defaultdict(lambda:defaultdict(float));audit=[]
    for row in rows:
        key=(row['model'],row['strategy']);t=totals[key];t['figures']+=1
        result=row.get('result',{});valid=result.get('status')=='valid';t['valid_figures']+=valid
        m=result.get('metrics',{})
        for k in ['prompt_tokens','completion_tokens','cost_usd','latency_seconds']:t[k]+=m.get(k,0)
        units=[g for g in gold if g['pmcid']==row['pmcid'] and g['figure_id']==row['figure_id']]
        predictions=result['data']['assignments'] if valid else []
        for g in units:
            matches=[p for p in predictions if norm(p['panel'])==norm(g['panel']) or p['panel'] is None]
            p=matches[0] if len(matches)==1 else None
            ownership=bool(p and set(p['patient_ids'])==set(g['patient_ids']) and
                           p['scope'] not in {'unresolved','aggregate'} and
                           (g['scope']!='shared' or p['scope']=='shared'))
            subject=bool(ownership and p['subject']==g['subject'])
            wrong_owner=bool(p and p['patient_ids'] and set(p['patient_ids'])!=set(g['patient_ids']))
            t['units']+=1;t['correct_ownership']+=ownership;t['correct_ownership_and_subject']+=subject
            t['wrong_patient_units']+=wrong_owner;t['missing_or_unresolved']+=not p or p['scope']=='unresolved'
            audit.append({'model':row['model'],'strategy':row['strategy'],'gold':g,'prediction':p,
                          'ownership_correct':ownership,'subject_correct':subject,'wrong_patient':wrong_owner,
                          'status':result.get('status',row.get('status'))})
    write_json(ROOT/'figure-scores.json',[{'model':k[0],'strategy':k[1],**v} for k,v in totals.items()])
    write_json(ROOT/'figure-unit-audit.json',audit)


def score_links():
    cases={x['case_id']:x for x in json.loads((ROOT/'link-cases.json').read_text())}
    rows=json.loads((ROOT/'link-outcomes.json').read_text());totals=defaultdict(lambda:defaultdict(float));audit=[]
    for row in rows:
        case=cases[row['case_id']];gold=case['expected'];r=row['result'];d=r['data'] if r['status']=='valid' else None
        t=totals[row['model']];t['cases']+=1;t['valid']+=bool(d)
        relation_ok=bool(d and d['relation'] in gold['relations'])
        identity_expected='explicit_same_patient' in gold['relations']
        identity_predicted=bool(d and d['relation']=='explicit_same_patient')
        correct_pair=bool(d and d['source_patient_id']==gold['source_patient_id'] and d['target_patient_id']==gold['target_patient_id'])
        false_identity=bool(identity_predicted and (not identity_expected or not correct_pair))
        t['relation_correct']+=relation_ok;t['false_identity']+=false_identity
        if identity_expected:
            t['identity_positives']+=1;t['identity_recovered']+=identity_predicted and correct_pair
        if case['case_id']=='literature_only_mention':t['correct_external_mention']+=relation_ok and correct_pair
        for k in ['cost_usd','prompt_tokens','completion_tokens']:t[k]+=r['metrics'].get(k,0)
        audit.append({'model':row['model'],'case_id':row['case_id'],'kind':case['kind'],'gold':gold,'prediction':d,
                      'status':r['status'],'relation_correct':relation_ok,'pair_correct':correct_pair,'false_identity':false_identity})
    write_json(ROOT/'link-scores.json',[{'model':k,**v} for k,v in totals.items()])
    write_json(ROOT/'link-unit-audit.json',audit)


if __name__=='__main__':
    if (ROOT/'figure-outcomes.json').exists():score_figures()
    if (ROOT/'link-outcomes.json').exists():score_links()
