"""Offline integration audit: new media ownership, unchanged clinical facts."""
import copy
import json
from pathlib import Path
from collections import defaultdict
from openpatients2.article_tasks import patient_packet
from openpatients2.figure_attribution import bind_figure_review
from openpatients2.ehr_seeds import seed_patient
from openpatients2.data import write_json
from score_attribution_v1 import norm

ROOT=Path('runs/attribution-v1')


def main():
    rows=json.loads((ROOT/'figure-v2-revalidated.json').read_text())
    repairs=json.loads((ROOT/'figure-repair-outcomes.json').read_text())
    repaired={(r['model'],r['pmcid'],r['figure_id']):r for r in repairs if r['result']['status']=='valid'}
    selected=[]
    for row in rows:
        if row['strategy']!='focused_text':continue
        selected.append(repaired.get((row['model'],row['pmcid'],row['figure_id']),row))
    gold=json.loads((ROOT/'figure-gold.json').read_text())['units'];scores=defaultdict(lambda:defaultdict(float))
    for row in selected:
        r=row['result'];s=scores[row['model']];s['figures']+=1;s['valid_figures']+=r['status']=='valid'
        assignments=r['data']['assignments'] if r['status']=='valid' else []
        for g in gold:
            if (g['pmcid'],g['figure_id'])!=(row['pmcid'],row['figure_id']):continue
            s['units']+=1
            p=next((a for a in assignments if norm(a['panel'])==norm(g['panel']) or a['panel'] is None),None)
            correct=bool(p and set(p['patient_ids'])==set(g['patient_ids']) and p['scope'] not in {'unresolved','aggregate'})
            s['correct_ownership']+=correct;s['wrong_patient_units']+=bool(p and p['patient_ids'] and set(p['patient_ids'])!=set(g['patient_ids']))
            s['strict_subject_correct']+=bool(correct and p['subject']==g['subject'])
            equivalent=bool(correct and (p['subject']==g['subject'] or
                (g['pmcid']=='PMC10998798' and g['figure_id']=='Fig2' and g['panel'] in {'A','B'} and p['subject']=='patient_specimen') or
                (g['scope']=='external' and p['scope']=='external' and not p['patient_ids'])))
            s['posthoc_clinically_compatible_subject']+=equivalent
    write_json(ROOT/'focused-after-repair-scores.json',[{'model':m,**s} for m,s in scores.items()])
    articles={a['pmcid']:a for a in map(json.loads,(ROOT/'articles-v2.jsonl').read_text().splitlines())}
    rosters=json.loads((ROOT/'figure-rosters.json').read_text());bundles=[];seeds=[];checks=[]
    for model in sorted(scores):
        for pid,roster in rosters.items():
            article=articles[pid]
            reviews=[bind_figure_review(row['result']['data'],article,roster,method=model+':focused-plus-one-repair')
                     for row in selected if row['model']==model and row['pmcid']==pid and row['result']['status']=='valid']
            for target in roster['patients']:
                path=Path('runs/medical-fidelity-v1/comparison')/model.replace('/','--')/f"{article['article_id']}-{target['patient_id']}.json"
                old=json.loads(path.read_text());new=copy.deepcopy(old)
                packet=patient_packet(article,roster,target,figure_reviews=reviews)
                assert packet['text']==old['source']['text'] and packet['source_hash']==old['source']['source_hash']
                new['source']=packet
                new['media_experiment']={'generation_model':model,'attribution_run':'attribution-v1',
                    'clinical_fields':'unchanged prior medical-fidelity-v1 output; still unreviewed',
                    'figure_coverage':'Only seven benchmark figures reviewed; cat Figure 1 stays explicitly unreviewed'}
                before=seed_patient(old);after=seed_patient(new)
                assert before['facts']==after['facts'] and before['events']==after['events']
                # Never attach the comparator dog's panel to the index cat.
                if pid=='PMC10998798':assert not any(f['figure_id']=='Fig3' and norm(f['panel'])=='b' for f in packet['figure_assignments'])
                bundles.append(new);seeds.append(after)
                checks.append({'model':model,'record_id':packet['record_id'], 'clinical_facts_unchanged':True,
                    'timeline_events_unchanged':True,'facts':len(after['facts']),
                    'assigned_panels':len(packet['figure_assignments']),
                    'attribution_complete':packet['multimedia']['attribution_complete'],
                    'unreviewed_figure_ids':packet['multimedia']['unreviewed_figure_ids']})
    (ROOT/'patient-bundles.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in bundles))
    (ROOT/'ehr-seeds.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in seeds))
    write_json(ROOT/'bundle-integration-audit.json',{'bundles':len(bundles),'checks':checks,
             'all_clinical_facts_preserved':True,'all_timeline_events_preserved':True,
             'new_clinical_fact_accuracy_claimed':False,'unreviewed_images_are_not_silently_omitted':True})


if __name__=='__main__':main()
