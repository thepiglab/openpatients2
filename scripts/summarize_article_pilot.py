"""Recompute transparent pilot counts; no model calls and no clinical accuracy claim."""
from pathlib import Path
import json
import statistics
import collections
import sqlite3
import re
from openpatients2.corpus import length_report
from openpatients2.data import write_json

ROOT=Path('runs/article-pilot')
MODELS=['meta/muse-glimmer-30b','thinkingmachines/inkling','google/gemma-4-31b-it','cohere/command-a-plus','meta/muse-spark-1.2-contributor']
EXPECTED={'PMC13314001':1,'PMC13314005':1,'PMC13294519':1,'PMC12773240':2,'PMC12802722':3,
          'PMC12285374':2,'PMC10998798':1,'PMC10828776':1,'PMC13290180':2,'PMC13278253':1,
          'PMC12807056':0,'PMC13304996':0}


def quantile(xs,p=.95):
    if not xs:return None
    xs=sorted(xs);i=(len(xs)-1)*p;a=int(i);b=min(a+1,len(xs)-1)
    return xs[a]+(xs[b]-xs[a])*(i-a)


def metrics(name):
    p=ROOT/name/'report.json'
    if not p.exists():return {'status':'not_complete'}
    d=json.loads(p.read_text());groups=collections.defaultdict(list)
    for m in d['metrics']:groups[m['model']].append(m)
    return {k:{'status':'access_blocked_not_model_evaluation' if 'muse-spark' in k and not any(x['valid'] for x in v) else 'completed',
        'tasks':len(v),'valid':sum(x['valid'] for x in v),
        'first_attempt_valid':sum(x['first_attempt_valid'] for x in v),
        'calls_in_saved_task_attempts':sum(x['attempts'] for x in v),
        'saved_attempt_cost_usd':sum(x['cost_usd'] for x in v),
        'median_task_seconds':statistics.median(x['latency_seconds'] for x in v),
        'p95_task_seconds':quantile([x['latency_seconds'] for x in v]),
        'prompt_tokens':sum(x['prompt_tokens'] for x in v),
        'completion_tokens':sum(x['completion_tokens'] for x in v),
        'cached_tokens':sum(x['cached_tokens'] for x in v),
        'resumed_tasks':sum(x.get('resumed',False) for x in v)} for k,v in groups.items()}


def roster_checks(run,model):
    folder=ROOT/run/model.replace('/','--');rows=[]
    for pmcid,expected in EXPECTED.items():
        path=folder/(pmcid+'.1-roster.json')
        if not path.exists():continue
        d=json.loads(path.read_text());v=d.get('data') or {}
        rows.append({'pmcid':pmcid,'expected_count':expected,'valid':d['status']=='valid',
            'count':len(v['patients']) if v else None,'count_matches':len(v['patients'])==expected if v else False,
            'disposition':v.get('disposition'),
            'species':[p['species'] for p in v.get('patients',[])],
            'unresolved_blocks':len(v.get('unresolved_segment_ids',[]))})
    return rows


def selected_facts(run,model):
    folder=ROOT/run/model.replace('/','--');out={}
    for pid in ['p1','p2']:
        p=folder/f'PMC13290180.1-{pid}.json'
        if not p.exists():continue
        d=json.loads(p.read_text());s=d['sections']
        def items(task):return (s.get(task) or {}).get('items',[])
        # Search only generated fact values, never the full source or evidence quotes.
        ages=[x['numeric_value'] for x in items('demographics') if x['attribute']=='age_at_presentation']
        hb=[x['numeric_value'] for x in items('observations') if 'hemoglobin' in x['name'].lower() or 'haemoglobin' in x['name'].lower()]
        meds=[{k:x.get(k) for k in ['name','dose_value','dose_unit','dose_text','action','subject','assertion']} for x in items('medications')]
        out[pid]={'age_values':ages,'hemoglobin_values':hb,'observation_task_valid':d['quality'].get('observations',{}).get('status')=='valid',
            'medication_values':meds,'condition_names':[x['name'] for x in items('conditions')],
            'oncology_treatments':[x['name'] for x in (s.get('oncology') or {}).get('treatments',[])],
            'all_clinical_tasks_valid':d['complete_for_scope']}
    return out


def main():
    stages=['clinical-first-pass','clinical','clinical-schema','clinical-eligible','vision-eligible','roster-final','format-prompt','format-schema','format-narrative','vision',
            'cohere-roster','cohere-clinical-schema','cohere-format-prompt','cohere-format-schema-v2','cohere-format-narrative','cohere-vision','cohere-vision-schema',
            'spark-clinical','spark-roster','spark-format-prompt','spark-format-schema','spark-format-narrative','spark-vision']
    articles=list(map(json.loads,(ROOT/'articles.jsonl').read_text().splitlines()))
    case_lengths=[a['lengths']['words'] for a in articles if EXPECTED[a['pmcid']]>0]
    result={'date_utc':'2026-09-29','sampling':'12 purposively selected licensed PMC articles; 10 with original cases; not a population estimate',
        'all_article_lengths':length_report(str(ROOT/'articles.jsonl')),
        'case_article_lengths':{'n':len(case_lengths),'mean':statistics.mean(case_lengths),'median':statistics.median(case_lengths),'p95':quantile(case_lengths)},
        'licenses':dict(collections.Counter(a['license']['code'] for a in articles)),
        'stages':{s:metrics(s) for s in stages},'roster_spotchecks':{},'clinical_spotchecks':{},
        'interpretation':'Task validity is structural/literal-source compliance, not clinical accuracy. Iterative prompt/validator development makes this a pilot, not a blinded benchmark. Different stages and resumed metrics must not be summed as total spend.'}
    audited=list(map(json.loads,(ROOT/'articles-audited.jsonl').read_text().splitlines()))
    case_lengths=[a['lengths']['words'] for a in audited if a['status']=='eligible' and EXPECTED[a['pmcid']]>0]
    result['license_audit']={'eligible_articles':sum(a['status']=='eligible' for a in audited),
        'excluded_articles':[{'pmcid':a['pmcid'],'reason':a['license']['reason'],'statement_codes':a['license']['statement_codes']} for a in audited if a['status']!='eligible'],
        'eligible_case_article_lengths':{'n':len(case_lengths),'mean':statistics.mean(case_lengths),'median':statistics.median(case_lengths),'p95':quantile(case_lengths)},
        'notice':'Historical comparison stages retain their original inputs, including three later flagged articles. Only clinical-eligible/vision-eligible and corrected exports use the audited corpus.'}
    result['cohere_transport_attempts']=dict(collections.Counter(
        json.loads(p.read_text())['response'].get('error') or 'response_received'
        for p in (ROOT/'cohere-clinical-schema/attempts').glob('*.json')))
    control=ROOT/'research/cohere-transport-control.json'
    if control.exists():
        result['cohere_nonstreaming_controls']=[{k:v for k,v in r.items() if k!='response'} for r in json.loads(control.read_text())['controls']]
    for model in MODELS:
        run='cohere-roster' if model.startswith('cohere/') else 'spark-roster' if 'spark' in model else 'roster-final'
        result['roster_spotchecks'][model]=roster_checks(run,model)
        run='cohere-clinical-schema' if model.startswith('cohere/') else 'spark-clinical' if 'spark' in model else 'clinical'
        result['clinical_spotchecks'][model]=selected_facts(run,model)
    b=sqlite3.connect(ROOT/'comparison/budget.sqlite')
    result['ledger_by_state']={state:{'requests':n,'usd':amount} for state,n,amount in b.execute('SELECT state,count(*),sum(accounted) FROM charges GROUP BY state')};b.close()
    for name in ['budget-final','budget-current']:
        p=ROOT/'research'/(name+'.json')
        if p.exists():result['provider_account_usage']=json.loads(p.read_text());break
    seed_file=ROOT/'exports/muse-gemma-ehr-seeds.jsonl'
    if seed_file.exists():
        seeds=[json.loads(l) for l in seed_file.read_text().splitlines()]
        result['final_exports']={'patient_records':len(seeds),
            'human_records':sum(s['species']=='human' for s in seeds),
            'nonhuman_records':sum(s['species']=='nonhuman' for s in seeds),
            'clinical_facts':sum(len(s['facts']) for s in seeds),
            'timeline_events':sum(len(s['events']) for s in seeds),
            'source_coverage_complete_records':sum(s['quality']['source_coverage_complete'] for s in seeds),
            'clinically_adjudicated_records':sum(s['quality']['clinical_review_status']=='adjudicated' for s in seeds),
            'hybrid_seed_file':str(seed_file)}
        for filename,key in [('older-human-cohort','cohort_records'),('strict-human-cohort','strict_completeness_cohort_records')]:
            p=ROOT/'exports'/(filename+'.jsonl.manifest.json')
            if p.exists():result['final_exports'][key]=json.loads(p.read_text())['returned_records']
    initial=ROOT/'clinical-eligible/report-initial.json'
    if initial.exists():
        rows=json.loads(initial.read_text())['metrics']
        result['audited_initial_workflow']={'tasks':len(rows),'valid':sum(x['valid'] for x in rows),
            'reported_cost_usd':sum(x['cost_usd'] for x in rows)}
    log=Path('reports/test-results.txt')
    if log.exists():
        match=re.search(r'(\d+) passed',log.read_text())
        if match:result['software_tests']={'passed':int(match[1]),'log':str(log)}
    write_json('reports/article-pilot-results.json',result)
    print(json.dumps({'lengths':result['case_article_lengths'],'stages':{s:{m:(r.get('valid'),r.get('tasks')) for m,r in d.items() if isinstance(r,dict)} for s,d in result['stages'].items()}}))

if __name__=='__main__':main()
