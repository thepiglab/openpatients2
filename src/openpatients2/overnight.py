"""Bounded overnight ablations, real GEPA, and untouched new-source evaluation."""
from __future__ import annotations
import asyncio
import copy
import json
from pathlib import Path
import time

from .data import write_json
from .pilot_extract import load_pilot_config, run_pilot, prepare_rosters
from .corpus_fidelity import evaluate
from .corpus_pilot import trial_warmup, read_json

VARIANT_FIELDS={'name','arm','input_scope','roster','overrides'}


def validate_plan(config):
    from .pilot_extract import PilotConfig
    if config.get('source_mode')!='fixed_fixture' or config['sample_size']!=20:
        raise ValueError('Overnight trial uses the twenty canonical regression articles')
    if not 2<=len(config['seeds'])<=12 or len(set(config['seeds']))!=len(config['seeds']):
        raise ValueError('Use 2–12 unique seeds')
    if not 4<=len(config['variants'])<=16: raise ValueError('Use 4–16 overnight ablations')
    names=set()
    for v in config['variants']:
        if set(v)-VARIANT_FIELDS or v['name'] in names or not v['name'].replace('-','').isalnum():
            raise ValueError('Invalid or duplicate variant')
        names.add(v['name'])
        if v['input_scope'] not in {'whole_article','patient_sections'} or v['arm'] not in {'direct','targeted'}:
            raise ValueError('Unknown ablation mode')
        if v.get('roster','frozen') not in {'frozen','cached','live'}: raise ValueError('Unknown roster conditioning')
        PilotConfig.model_validate({**load_pilot_config(config['extraction_config']).model_dump(), **v.get('overrides',{})})
    if not {'baseline','clinical-audit','joint-pixels','gepa'}<=names: raise ValueError('Missing overnight controls')
    if not 3600<=config['gpu_budget_seconds']<=36000: raise ValueError('GPU budget must be 1–10 hours within the 12h job')
    if not 128<=config['gepa_metric_calls_per_prompt']<=512:
        raise ValueError('Bound GEPA to 128–512 task evaluations per prompt')
    if not 600<=config['gepa_seconds']<=7200: raise ValueError('Bound GEPA phase to 10–120 minutes')
    if not 1<=config.get('gepa_workers',8)<=8: raise ValueError('Use 1–8 concurrent GEPA families')
    profiles=config.get('gepa_profiles',[])
    if profiles:
        if len(profiles)!=2 or {p['name'] for p in profiles}!={'checklist','clinical'}:
            raise ValueError('Compare exactly the checklist and clinical GEPA profiles')
        if sum(p['seconds'] for p in profiles)>10800: raise ValueError('GEPA profiles exceed three hours')
        for p in profiles:
            if not 600<=p['seconds']<=7200 or not 128<=p['calls']<=512: raise ValueError('Invalid GEPA profile budget')
            options=p['options']
            if set(options)-{'semantic_feedback','reflection_reasoning','train_examples','validation_examples','minibatch_size','proposals'}:
                raise ValueError('Unknown GEPA search option')
            if options.get('reflection_reasoning','high') not in {'medium','high','xhigh'}: raise ValueError('Invalid reflection reasoning')
            for key,low,high in [('train_examples',4,24),('validation_examples',2,12),('minibatch_size',2,6),('proposals',1,2)]:
                if not low<=options.get(key,low)<=high: raise ValueError('Invalid GEPA option '+key)
    if set(config.get('bootstrap_variants',[]))-names: raise ValueError('Unknown bootstrap variant')
    if config.get('gepa_context',65536) not in {65536,131072}: raise ValueError('Unsupported GEPA context')
    for cell in config['matrix']:
        if cell.get('tensor_parallel',1) not in {1,2} or cell.get('speculation','dflash') not in {'dflash',None}:
            raise ValueError('Only TP1/TP2 and DFlash on/off are included')
        if set(cell.get('variants',names))-names: raise ValueError('Unknown layout variant')
    return True


async def run_trials(campaign,destination,endpoints,context,cell,deadline, *, bootstrap_only=False):
    config=campaign['config']; work=Path(campaign['work']); destination=Path(destination)
    sample=work/'profile/sample.jsonl.gz'; frozen=work/'profile/rosters.json'
    base=load_pilot_config(config['extraction_config']).model_dump()
    base['gpus_per_endpoint']=cell.get('tensor_parallel',1)
    rows=[]; discoveries=[]; deferred=[]; executed=set(); bootstrap=[]
    variants={v['name']:v for v in config['variants']}
    prompts={}; gepa_report=None
    async def trial(seed,variant, *, source='regression',cached=None):
        name=variant['name']; path=destination/source/f'seed{seed}'/name
        if campaign['config'].get('separate_gepa') or campaign['config'].get('resume_from'):
            if (path/'report.json').exists():
                measured=read_json(path/'report.json')
                if measured.get('outputs') and measured.get('status')!='failed':
                    result={'arm':name,'seed':seed,'source_set':source,'input_scope':variant['input_scope'],
                        'roster_conditioning':variant.get('roster','frozen') if source=='regression' else 'cached',
                        'cell':cell,'report':measured,'reused_completed_trial':True}
                    rows.append(result); write_json(destination/'trials.json',rows)
                    return result
            if path.exists():
                # Retain interrupted traces without merging incomplete throughput
                # denominators into the freshly repeated trial.
                index=0
                while path.with_name(path.name+f'-interrupted-{index}').exists(): index+=1
                path.rename(path.with_name(path.name+f'-interrupted-{index}'))
        if time.monotonic()+config.get('trial_reserve_seconds',900)>=deadline:
            deferred.append({'seed':seed,'arm':name,'source_set':source,'reason':'GPU deadline reserve'})
            return None
        cfg={**base,**variant.get('overrides',{})}
        if name.startswith('gepa'):
            cfg['prompt_overrides']=prompts
            if name=='gepa-checklist': cfg['prompt_overrides']=read_json(work/'prompt-optimization/checklist/best-prompts.json')
            elif name=='gepa-clinical-only':
                from .schemas import TASK_MODELS
                cfg['prompt_overrides']={k:v for k,v in prompts.items() if k in TASK_MODELS}
            elif name=='gepa-aux-only':
                from .schemas import TASK_MODELS
                cfg['prompt_overrides']={k:v for k,v in prompts.items() if k not in TASK_MODELS}
        if source=='new-sources': cfg['max_articles']=config['holdout_sample_size']
        manifest=work/('holdout/vision-assets/manifest.json' if source=='new-sources' else 'vision-assets/manifest.json')
        inputs=work/'holdout/profile/sample.jsonl.gz' if source=='new-sources' else sample
        conditioning=variant.get('roster','frozen') if source=='regression' else 'cached'
        write_json(work/'progress.json',{'phase':'overnight_extracting','seed':seed,'variant':name,
            'source_set':source,'cell':cell,'time_unix':time.time()})
        try:
            cache=await trial_warmup(endpoints,cfg,seed)
            write_json(path.parent/(name+'-warmup.json'),cache)
            # Cancel one runaway trial while retaining already saved task traces.
            remaining=deadline-time.monotonic()-120
            measured=await asyncio.wait_for(run_pilot(cfg,inputs,path,endpoints,context,variant['arm'],
                input_scope=variant['input_scope'],seed=seed,image_manifest=manifest,
                frozen_rosters=frozen if conditioning=='frozen' else None,
                cached_rosters=cached if conditioning=='cached' else None),timeout=max(1,remaining))
            measured['warmup']=cache
            if source=='regression':
                task_rows=[read_json(p) for p in (path/'tasks').glob('*.json')]
                score=evaluate(config['fidelity_reference'],measured['outputs']['patients'],task_rows=task_rows,
                    visual_rows=measured['outputs']['visual_annotations'],
                    discovery=measured['outputs']['predicted_rosters'] if conditioning=='live' else None)
                write_json(path/'fidelity.json',score); measured['fidelity']=score
                split_path=work/'prompt-optimization/splits.json'
                if split_path.exists():
                    split=read_json(split_path); ref=read_json(config['fidelity_reference'])
                    ids=set(split['test'])
                    sub={**ref,'checks':[c for c in ref['checks'] if c['record_id'].split(':')[0] in ids],
                        'articles':[a for a in ref.get('articles',[]) if a['article_id'] in ids],
                        'figure_checks':[a for a in ref.get('figure_checks',[]) if a['article_id'] in ids]}
                    measured['gepa_test_fidelity']=evaluate(sub,measured['outputs']['patients'],task_rows=task_rows,
                        visual_rows=measured['outputs']['visual_annotations'])
            else: measured['accuracy_gold']='unavailable; source adjudication required'
            write_json(path/'report.json',measured)
        except Exception as exc:
            measured={'status':'failed','error_type':type(exc).__name__,'error':str(exc),
                'tokens':{},'valid_tasks':0,'task_count':0}
            write_json(path/'FAILED.json',measured)
        result={'arm':name,'seed':seed,'source_set':source,'input_scope':variant['input_scope'],
            'roster_conditioning':conditioning,'refinement_policy':cfg['refinement_policy'],'cell':cell,'report':measured}
        rows.append(result); write_json(destination/'trials.json',rows)
        return result

    main=cell.get('phase','main')=='main'
    if main:
        first=config['seeds'][0]
        for name in config.get('bootstrap_variants',('baseline','clinical-audit','joint-pixels','coverage-backfill')):
            row=await trial(first,variants[name]); executed.add((first,name))
            if row and row['report'].get('outputs'): bootstrap.append(destination/'regression'/f'seed{first}'/name)
        if bootstrap_only: return rows,discoveries,deferred
        if config.get('separate_gepa'):
            if not (work/'prompt-optimization/report.json').exists():
                raise ValueError('Separate GEPA stage has not finished; inspect gepa.json')
            gepa_report=read_json(work/'prompt-optimization/report.json')
            prompts=read_json(work/'prompt-optimization/best-prompts.json')
        elif bootstrap and time.monotonic()+900<deadline:
            from .prompt_optimization import optimize_prompts
            # GEPA is synchronous; adapters run async local requests in its worker
            # thread, leaving the scheduler and progress loop responsive.
            cfg={**base, 'image_manifest':str(work/'vision-assets/manifest.json')}
            try:
                prompts,gepa_report=await asyncio.to_thread(optimize_prompts,sample,bootstrap,
                    work/'prompt-optimization',cfg,endpoints,context,config['fidelity_reference'],
                    seconds=min(config['gepa_seconds'],max(1,deadline-time.monotonic()-900)),
                    calls=config['gepa_metric_calls_per_prompt'],workers=config.get('gepa_workers',8),
                    resume=(work/'prompt-optimization').exists())
            except Exception as exc:
                gepa_report={'status':'failed','error':str(exc),'error_type':type(exc).__name__}
                write_json(work/'prompt-optimization/report.json',gepa_report)
        else:
            gepa_report={'status':'failed','error':'No successful bootstrap or insufficient time for GEPA'}
            write_json(work/'prompt-optimization/report.json',gepa_report)
    elif (work/'prompt-optimization/best-prompts.json').exists():
        prompts=read_json(work/'prompt-optimization/best-prompts.json')

    if main and config.get('holdout_source_config'):
        for seed in config['holdout_seeds']:
            if time.monotonic()+900>=deadline:
                deferred.append({'seed':seed,'source_set':'new-sources','reason':'GPU deadline reserve'}); continue
            discovery_cfg={**base,'max_articles':config['holdout_sample_size'],'isolate_secondary_cases':True}
            path=destination/'new-sources'/f'seed{seed}'/'discovery'
            try:
                d=await asyncio.wait_for(prepare_rosters(discovery_cfg,work/'holdout/profile/sample.jsonl.gz',
                    path,endpoints,context,seed=seed),timeout=max(1,deadline-time.monotonic()-120))
                discoveries.append({'seed':seed,'source_set':'new-sources','report':d})
                for name in config['holdout_variants']:
                    if name=='gepa' and not prompts: continue
                    await trial(seed,variants[name],source='new-sources',cached=d['outputs']['predicted_rosters'])
            except Exception as exc:
                discoveries.append({'seed':seed,'report':{'status':'failed','error':str(exc)}})
    selected=[variants[n] for n in cell.get('variants',list(variants))]
    seeds=cell.get('seeds',config['seeds'])
    for index,seed in enumerate(seeds):
        # Rotate order to reduce systematic timing/cache/thermal effects.
        rotated=selected[index%len(selected):]+selected[:index%len(selected)]
        for v in rotated:
            if (seed,v['name']) in executed: continue
            if v['name'].startswith('gepa') and not prompts:
                deferred.append({'seed':seed,'arm':'gepa','reason':'No optimized prompts available'})
                continue
            await trial(seed,v)
    write_json(destination/'schedule-outcome.json',{'deferred':deferred,'prompt_optimization':gepa_report})
    return rows,discoveries,deferred


def heldout_summary(fidelity, ids):
    """Uniformly score saved check rows, including pre-optimization bootstrap trials."""
    from collections import Counter
    result=Counter()
    for check in fidelity.get('checks',[]):
        if check['record_id'].split(':')[0] not in ids: continue
        value=check.get('delivered',{})
        if check['kind']=='required':
            result['required']+=1; result['matched']+=bool(value.get('matched'))
        else:
            result['forbidden_violations']+=bool(value.get('matched'))
            result['forbidden_unscorable']+=not value.get('available',False)
    return result


def write_comparison(campaign,result):
    """Readable clinically focused metrics; no self-judged accuracy percentages."""
    from collections import Counter,defaultdict
    work=Path(campaign['work']); rows=result.get('cells',[])
    split=read_json(work/'prompt-optimization/splits.json') if (work/'prompt-optimization/splits.json').exists() else {}
    groups=defaultdict(list)
    for row in rows:
        cell=row['cell']
        key=(row.get('source_set','regression'),cell['context'],cell['prefill'],
             cell.get('tensor_parallel',1),cell.get('speculation'),row['arm'])
        groups[key].append(row)
    lines=['# Overnight clinical-fidelity comparison','',
        f"GPU stage: {result['gpu']}; model cleanup: {result['cleanup']}; article cleanup: {result['source_cleanup']}.",'',
        '| Source set | Context / prefill | TP / draft | Arm | Trials | Clinical valid / partial / failed | Required facts | Forbidden hits / unavailable | GEPA test facts | Output tok/s |',
        '| --- | --- | --- | --- | ---: | --- | --- | --- | --- | ---: |']
    for key,trials in sorted(groups.items(),key=lambda pair:str(pair[0])):
        statuses=Counter();gold=Counter();test=Counter(); rates=[]
        for row in trials:
            r=row['report'];statuses.update(r.get('task_statuses_by_domain',{}).get('clinical',{}))
            for name,counter in [('fidelity',gold)]:
                s=r.get(name,{}).get('summary',{}).get('delivered',{})
                counter.update({k:s.get(k,0) for k in ('matched','required','forbidden_violations','forbidden_unscorable')})
            if split:
                test.update(heldout_summary(r.get('fidelity',{}),set(split['test'])))
            else:
                test.update(r.get('gepa_test_fidelity',{}).get('summary',{}).get('delivered',{}))
            rate=r.get('tokens',{}).get('all_gpus_output_tokens_per_second')
            if rate is not None: rates.append(rate)
        source,ctx,prefill,tp,draft,arm=key
        show=lambda c:f"{c['matched']}/{c['required']}" if c['required'] else 'unadjudicated'
        lines.append(f'| {source} | {ctx} / {prefill} | {tp} / {draft or "off"} | {arm} | {len(trials)} | '
            f"{statuses['valid']} / {statuses['partial']} / {statuses['failed']} | {show(gold)} | "
            f"{gold['forbidden_violations']} / {gold['forbidden_unscorable']} | {show(test)} | "+
            (f'{sum(rates)/len(rates):.1f} |' if rates else 'unavailable |'))
    lines+=['','Token rates are arithmetic means across completed trial timers, including repairs/audits and all replicas; '
        'they exclude startup and queueing. Compare matched arms within a layout. Different context/workloads are different regimes.',
        'Clinical validity counts concern the 14 extraction task classes only. Extra audits are counted separately. '
        'Fact matches use a finite development checklist. New-source accuracy, all remaining claims and pixel interpretations need adjudication.',
        'GEPA test facts are recomputed from the same saved checklist rows for every arm, including reused bootstrap trials. '
        'Repeated seeds do not increase the number of independent articles or unique gold facts.',
        '','## Independent model review signals','',
        '| Arm | Inventory features | Claimed missing features before backfill | Claims called unsupported / wrong patient | Clinical facts without event links |',
        '| --- | ---: | ---: | ---: | ---: |']
    totals=defaultdict(Counter)
    for row in rows:
        review=row['report'].get('clinical_review_signals',{}); c=totals[row['arm']]
        c['inventory']+=review.get('inventory_features',0)
        c['missing']+=review.get('coverage_decisions_before_backfill',{}).get('missing',0)
        c['unsupported']+=sum(review.get('claim_decisions',{}).get(k,0) for k in ('unsupported','wrong_patient'))
        c['unlinked']+=review.get('unlinked_clinical_facts',0)
    for arm,c in sorted(totals.items()):lines.append(f"| {arm} | {c['inventory']} | {c['missing']} | {c['unsupported']} | {c['unlinked']} |")
    lines+=['','These are model review signals, not adjudicated errors or recall. Missing event links remain unknown; '
        'the pipeline never creates dates or carries a state forward by assumption. Backfill preserves accepted facts and retains its original audit.',
        '','## GEPA prompt-family coverage','',
        '| Prompt | Status | Train / validation examples | Metric calls | Candidates |',
        '| --- | --- | ---: | ---: | ---: |']
    path=work/'prompt-optimization/report.json'
    optimization=read_json(path) if path.exists() else {'status':'missing','components':[]}
    for c in optimization.get('components',[]):
        lines.append(f"| {c['component']} | {c['status']} | {c['train_examples']} / {c['validation_examples']} | "
            f"{c.get('metric_calls','—')} | {c.get('candidate_count','—')} |")
    profiles_path=work/'prompt-optimization/profiles.json'
    if profiles_path.exists():
        lines+=['','## GEPA tuning profiles','',
            '| Profile | Status | Completed families | Nonempty supplements | Families with validation gain |',
            '| --- | --- | ---: | ---: | ---: |']
        for name,profile in read_json(profiles_path).items():
            parts=profile['components']
            lines.append(f"| {name} | {profile['status']} | {sum(p['status']=='completed' for p in parts)}/{len(parts)} | "
                f"{profile['optimized_nonempty_components']} | {sum(p.get('validation_gain',0)>0 for p in parts)} |")
        lines+=['','Clinical-feedback rewards are frozen, unadjudicated model scores. They are never reported as medical accuracy. '
            'Both profiles keep the empty seed supplement on a validation tie. The clinical profile is designated in advance for '
            'experimental GEPA arms; held-out scores do not select prompts or alter the evaluation rubric.']
    lines+=['',f"GEPA: {optimization.get('status','missing')}. Components lacking disjoint examples are explicitly unavailable. "
        'Every component has a separate bounded search. Gold-backed clinical rewards and structural-only rewards are different; '
        'winning supplements are experimental, never auto-promoted to production. Splits are by article; cross-publication duplicate patients remain a limitation.',
        '',f"Deferred trials/layouts: {len(read_json(work/'gpu.json').get('deferred',[])) if (work/'gpu.json').exists() else 'unknown'}."]
    (work/'COMPARISON.md').write_text('\n'.join(lines)+'\n')
    return optimization
