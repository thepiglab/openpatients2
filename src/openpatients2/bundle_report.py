"""Matched quality and pooled throughput receipts for the bundle suite."""
from collections import defaultdict, Counter
import json
from pathlib import Path


def pooled_rate(reports):
    tokens=[r.get('tokens',{}) for r in reports]
    if not tokens or any(t.get('output_tokens') is None or not t.get('wall_seconds') for t in tokens): return None
    return sum(t['output_tokens'] for t in tokens)/sum(t['wall_seconds'] for t in tokens)


def write_bundle_comparison(campaign,result):
    work=Path(campaign['work']); groups=defaultdict(list)
    for row in result.get('cells',[]):
        cell=row['cell']
        groups[(cell['context'],cell['prefill'],cell.get('speculation'),row['arm'],row.get('roster_conditioning'))].append(row)
    lines=['# Patient bundle program comparison','',
        'These are finite source-reviewed development probes, not overall medical accuracy. '
        'Compare live discovery arms against live controls; frozen identity arms answer a different question. '
        'Repeated seeds are repeat trials, not additional independent articles.','',
        '| Context / prefill / draft | Arm / roster | Trials | Clinical check matches | Forbidden hits / unscorable | Test fields | Course nodes / relations | Clinical valid / partial / failed | Pooled output tok/s |',
        '| --- | --- | ---: | --- | --- | --- | --- | --- | ---: |']
    coverage=['','| Context / prefill / draft | Arm / roster | Identities and valid negatives | Summary concepts | Figure ownership | Pixel inventories |',
        '| --- | --- | --- | --- | --- | --- |']
    for key,rows in sorted(groups.items(),key=lambda kv:str(kv[0])):
        valid=Counter();counts=Counter();test=Counter();course=Counter();parts=Counter()
        for row in rows:
            r=row['report'];q=r.get('bundle_quality',{});c=q.get('clinical',{})
            counts.update({k:c.get(k,0) for k in ['matched','required','forbidden_violations','forbidden_unscorable']})
            t=r.get('untouched_bundle_test',{}).get('clinical',{})
            test.update({k:t.get(k,0) for k in ['matched','required']})
            for t in q.get('timelines',[]): course.update({k:t[k] for k in ['nodes_matched','nodes','relations_matched','relations']})
            for name,p in q.get('parts',{}).items():
                parts.update({name+'_matched':p['matched'],name+'_required':p['required']})
            valid.update(r.get('task_statuses_by_domain',{}).get('clinical',{}))
        rate=pooled_rate([r['report'] for r in rows]);ctx,prefill,draft,arm,roster=key
        lines.append(f'| {ctx} / {prefill} / {draft or "off"} | {arm} / {roster} | {len(rows)} | '
            f'{counts["matched"]}/{counts["required"]} | {counts["forbidden_violations"]}/{counts["forbidden_unscorable"]} | '
            f'{test["matched"]}/{test["required"]} | {course["nodes_matched"]}/{course["nodes"]}; {course["relations_matched"]}/{course["relations"]} | '
            f'{valid["valid"]}/{valid["partial"]}/{valid["failed"]} | '+(f'{rate:.1f} |' if rate is not None else 'unavailable |'))
        coverage.append(f'| {ctx} / {prefill} / {draft or "off"} | {arm} / {roster} | '+
            ' | '.join(f'{parts[name+"_matched"]}/{parts[name+"_required"]}'
                for name in ['identity','summary','figure_ownership','pixel_inventory'])+' |')
    lines+=coverage
    path=work/'prompt-optimization/joint-program/report.json'
    joint=json.loads(path.read_text()) if path.exists() else {'status':'missing'}
    lines+=['','Output rate = summed reported completion tokens / summed trial wall time, including repairs, '
        'audits and all eight GPUs. Missing usage makes the rate unavailable. Startup, queue wait and GEPA '
        'search time are separate. Full GPU-stage time and utilization are in gpu.json and telemetry.','',
        f'Joint GEPA: {joint.get("status")}; selected original strategies: {joint.get("selected_original","unknown")}. '
        f'Baseline validation facts: {joint.get("baseline_validation_facts","unknown")}; '
        f'candidate confirmation facts: {joint.get("confirmation_validation_facts","unknown")}.','',
        f'Fresh confirmation complete: {joint.get("confirmation_complete","unknown")}; '
        f'clinical nonregression gate: {joint.get("clinical_nonregression_gate_passed","unknown")}. '
        'Missing confirmation is unavailable, not zero medical accuracy.','',
        'Prompt-family search, candidate changes and selection receipts are in prompt-optimization/. '
        'A budget-limited search may not mutate every component; unchanged and underlabelled components are explicit. '
        'No test labels select prompts, no model-judge score enters the bundle objective, and no prompt is promoted to production.','',
        f'Deferred work: {len(result.get("deferred",[]))}. Inspect all failed trial and optimization receipts before treating this campaign as successful.']
    (work/'BUNDLE_COMPARISON.md').write_text('\n'.join(lines)+'\n')
    with (work/'SUMMARY.md').open('a') as out:out.write('\nFull-program results: [BUNDLE_COMPARISON.md](BUNDLE_COMPARISON.md).\n')
    return joint
