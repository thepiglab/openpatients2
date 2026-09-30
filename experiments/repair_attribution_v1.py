"""Post-hoc, separately labeled validator correction and one-turn repair tests."""
import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
from openpatients2.data import write_json
from openpatients2.experiment import Experiment
from openpatients2.output_parser import parse_output
from openpatients2.figure_attribution import figure_messages,validate_figure_review,bind_figure_review
from openpatients2.case_links import link_messages,validate_link,bind_link

ROOT=Path('runs/attribution-v1')


def decode(result):
    response=result.get('attempt_responses',[])[-1]
    if response.get('finish_reason') not in {'stop','eos'}:raise ValueError('Incomplete response')
    return parse_output(response['content']).value


def clinical_assignment(data):
    return sorted(json.dumps({k:a.get(k) for k in ('panel','patient_ids','scope','subject')},sort_keys=True)
                  for a in data['assignments'])


async def run():
    config=json.loads((ROOT/'figure-config.json').read_text());config['output']=str(ROOT/'repair');config['concurrency']=6
    exp=Experiment(config)
    articles={a['pmcid']:a for a in json.loads((ROOT/'figure-articles.json').read_text())}
    rosters=json.loads((ROOT/'figure-rosters.json').read_text());models={m['id']:m for m in config['models']}
    normalized=[];fixed=[];link_fixed=[]
    rows=json.loads((ROOT/'figure-outcomes.json').read_text())
    for row in rows:
        row=copy.deepcopy(row);result=row['result']
        if result['status']!='valid':
            try:
                data=validate_figure_review(decode(result),articles[row['pmcid']],rosters[row['pmcid']],row['figure_id'])
                row['review']=bind_figure_review(data,articles[row['pmcid']],rosters[row['pmcid']],method='v2-validator-replay',pixel_provenance=row.get('pixel_provenance'))
                row['result']={**result,'status':'valid','data':data,'errors':[]}
                row['recovery']='validator_subject_ownership_decoupled_no_generation_change'
            except (ValueError,KeyError,IndexError,TypeError):pass
        normalized.append(row)
    write_json(ROOT/'figure-v2-revalidated.json',normalized)
    async def figure_repair(row):
        a=articles[row['pmcid']];r=rosters[row['pmcid']];fid=row['figure_id'];old=row['result']
        try:draft=decode(old)
        except (ValueError,KeyError,IndexError,TypeError):draft=None
        wrong_target=draft is not None and draft.get('figure_id')!=fid
        def checker(value):
            checked=validate_figure_review(value,a,r,fid)
            if draft and not wrong_target and clinical_assignment(checked)!=clinical_assignment(draft):
                raise ValueError('Targeted format/evidence repair changed panel ownership or subject')
            return checked
        messages=figure_messages(a,r,fid)
        messages.append({'role':'user','content':json.dumps({'failed_draft':draft,'validation_errors':old['errors'],
            'repair_instruction':'Correct the wrong target by re-extracting ONLY the requested figure.' if wrong_target else
            'Repair only missing rationale fields, literal evidence quotes and segment IDs. Preserve panels, patient_ids, scope and subject exactly. Return the entire corrected object; do not delete assignments.'})})
        result=await exp.call(models[row['model']],'figure_attribution',messages,checker,
                              {'figure_id':fid,'article_id':a['article_id'],'phase':'focused_targeted_repair'},max_tokens=4096)
        review=bind_figure_review(result['data'],a,r,method=row['model']+':focused_text_repair') if result['status']=='valid' else None
        fixed.append({**row,'result':result,'review':review,'repair_type':'wrong_target_reextract' if wrong_target else 'format_and_evidence_only',
                      'base_result':old});write_json(ROOT/'figure-repair-outcomes.json',fixed)
    cases={c['case_id']:c for c in json.loads((ROOT/'link-cases.json').read_text())}
    async def link_repair(row,corrected=False):
        c=copy.deepcopy(cases[row['case_id']])
        if corrected:
            p=c['source_roster']['patients'][0]
            text='Case 1 is a new patient, a 49-year-old man.'
            p['label']=text;p['identity_evidence']=[{'segment_id':'b00001','quote':text}]
            c['source_roster']['patients'][1]['source_segment_ids']=['b00001','b00002']
        a,b,ra,rb=[c[k] for k in ['source','target','source_roster','target_roster']]
        messages=link_messages(a,b,ra,rb)
        if not corrected:
            messages.append({'role':'user','content':json.dumps({'previous_response':row['result']['attempt_responses'][-1]['content'],
              'errors':row['result']['errors'],'instruction':'Reassess the relation against original evidence. Use literal canonical quotes; never quotes from the marker view. If incompatible patient facts remain, choose uncertain. Return the complete object.'})})
        def check(v):return validate_link(parse_output(v['narrative']).value if 'narrative' in v else v,a,b,ra,rb)
        result=await exp.call(models[row['model']],'roster',messages,check,
            {'case_id':c['case_id'],'phase':'corrected_roster_probe' if corrected else 'targeted_link_repair'},max_tokens=4096,raw_text=True)
        link_fixed.append({'case_id':c['case_id'],'model':row['model'],'corrected_roster_probe':corrected,
                           'source_roster_used':ra,'result':result,
                           'edge':bind_link(result['data'],a,b,ra,rb,method=row['model']) if result['status']=='valid' else None})
        write_json(ROOT/'link-repair-outcomes.json',link_fixed)
    try:
        jobs=[figure_repair(r) for r in normalized if r['strategy']=='focused_text' and r['result']['status']!='valid']
        links=json.loads((ROOT/'link-outcomes.json').read_text())
        jobs += [link_repair(r) for r in links if r['case_id']!='explicit_wrong_case_trap' and r['result']['status']!='valid']
        jobs += [link_repair(r,True) for r in links if r['case_id']=='explicit_wrong_case_trap']
        await asyncio.gather(*jobs)
    finally:
        write_json(ROOT/'repair-budget-status.json',exp.budget.report());await exp.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--key-file',required=True);args=p.parse_args()
    os.environ['OPENROUTER_API_KEY']=Path(args.key_file).read_text().strip();asyncio.run(run())
