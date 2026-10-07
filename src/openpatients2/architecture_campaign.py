"""Accounting and CPU analysis for the bounded clinical architecture experiment."""
from copy import deepcopy
from pathlib import Path
import shutil
import time
from .data import write_json


async def corrected_baseline(config, articles_path, output, endpoints, context, arm, **kwargs):
    from .pilot_extract import run_pilot
    from .component_trial import run_component_trial
    output = Path(output)
    raw = output.with_name('raw-baseline-building')
    start = time.monotonic()
    original = await run_pilot(config, articles_path, raw, endpoints, context, arm, **kwargs)
    corrected = await run_component_trial(config, articles_path, output, endpoints, context,
        baseline_dir=raw, component='table-compiled', seed=kwargs.get('seed'), http=kwargs.get('http'))
    # Retain raw attempts and reports for attribution of costs and repair effects.
    # Move only our newly created baseline after all frozen checks have completed.
    retained = output/'raw-baseline'
    shutil.move(str(raw),str(retained))
    corrected['baseline_phases']={'original':deepcopy(original),'tables_and_gate_probes':deepcopy(corrected)}
    corrected['baseline_source_paths_relocated']={'from':str(raw),'to':str(retained)}
    for key in ('model_calls','task_count','valid_tasks'):
        corrected[key]=original.get(key,0)+corrected.get(key,0)
    wall=time.monotonic()-start;corrected['wall_seconds']=wall
    usage={ 'phase_reports': {'original':original['tokens'],'tables_and_gate_probes':corrected['tokens']},
            'wall_seconds':wall,'includes_authored_gate_probes':True,
            'per_article_statistics':'See separate phase reports; qualification probes are not clinical articles'}
    for kind in ('input','output','reasoning'):
        reports=[original['tokens'],corrected['tokens']]
        missing=sum(r.get('unknown_'+kind+'_usage_calls',0) for r in reports)
        total=sum(r.get('reported_'+kind+'_tokens',r.get(kind+'_tokens') or 0) for r in reports)
        usage.update({kind+'_tokens':None if missing else total,'reported_'+kind+'_tokens':total,
                      'unknown_'+kind+'_usage_calls':missing})
    usage['output_tokens_per_gpu_second']=usage['output_tokens']/wall if usage['output_tokens'] is not None else None
    corrected.update(tokens=usage,arm='live-complete',baseline_label='Full baseline + compiled tables; gate qualification included in cost')
    write_json(output/'report.json',corrected)
    return corrected


def summarize(campaign):
    """Separate model gate decisions from measured clinical checklist outcomes."""
    import json
    from collections import Counter
    work=Path(campaign['work']);rows=[]
    for shard in range(campaign['config']['gpu_workers']):
        for seed in campaign['config']['seeds']:
            root=work/'shards'/f'{shard:03d}'/'trials'/f'seed{seed}'
            for arm in campaign['config']['variants']:
                base=root/arm;gate_path=base/'architecture/gate-qualification.json'
                gate=json.loads(gate_path.read_text()) if gate_path.exists() else None
                receipts=[json.loads(p.read_text()) for p in sorted((base/'architecture').glob('patient-*.json'))]
                decisions=Counter(d['support'] for r in receipts for d in r['decisions'])
                rows.append({'shard':shard,'seed':seed,'arm':arm,'gate_qualified':gate['qualified'] if gate else None,
                    'gate_false_accepts':gate['false_accepts'] if gate else None,
                    'gate_false_rejects_or_abstentions':gate['false_rejects_or_abstentions'] if gate else None,
                    'delivered_receipts':len(receipts),'gate_applied_patients':sum(r['gate_applied'] for r in receipts),
                    'decisions':dict(decisions),'expanded_context_decisions':sum(d['context_expanded'] for r in receipts for d in r['decisions']),
                    'failed_projection_domains':sum(len(r['projection_failures']) for r in receipts),
                    'unreviewed_source_units':sum(len(r.get('reading',{}).get('unreviewed_units',[])) for r in receipts),
                    'dispositions':dict(Counter(d['status'] for r in receipts for chunk in r.get('reading',{}).get('chunks',[])
                        if chunk['data'] for d in chunk['data']['dispositions']))})
    report={'rows':rows,'gate_scores_are_authored_behavioral_controls':True,'clinical_accuracy_verified':False,
            'pixel_predictions_frozen':True,'new_blinded_holdout':False,'production_defaults_changed':False}
    write_json(work/'architecture-summary.json',report)
    lines=['# Clinical architecture diagnostics','',
        'Comparator: full baseline plus compiled table additions. Qualification cost is included in baseline.',
        'Gate failure leaves filtering in shadow mode; read gate-applied counts before comparing arms.',
        'Encounter-state replaces clinical sections; coverage-pass appends candidates except oncology/case-context reconciliation.',
        'Modified records have regenerated timelines and withheld summaries; no complete-bundle promotion.', '',
        '| Shard | Seed | Arm | Gate qualified | False accepts / true controls rejected or unresolved | Patients filtered | Decisions |',
        '| --- | --- | --- | --- | --- | ---: | --- |']
    for r in rows:
        lines.append(f"| {r['shard']} | {r['seed']} | {r['arm']} | {r['gate_qualified']} | {r['gate_false_accepts']} / {r['gate_false_rejects_or_abstentions']} | {r['gate_applied_patients']} | {r['decisions']} |")
    (work/'ARCHITECTURE.md').write_text('\n'.join(lines)+'\n')
    return report
