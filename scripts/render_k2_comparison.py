"""Render the offline comparison after explicit source adjudication."""
from copy import deepcopy
import csv
import json
from pathlib import Path

from openpatients2.fidelity import summarize


ROOT = Path('runs/hpg-comparison-20261001')
LABELS = {
    'k2-375b-nvfp4': 'K2-375B NVFP4',
    'k2-32b-nvfp4': 'K2-32B NVFP4',
    'k2-mova-36b-fp8': 'K2-MoVA 36B FP8',
    'k2-7b-fp8': 'K2-7B FP8',
    'meta/muse-spark-1.2': 'Muse Spark 1.2',
    'meta/muse-glimmer-30b': 'Muse Glimmer 30B',
    'thinkingmachines/inkling': 'Inkling',
    'google/gemma-4-31b-it': 'Gemma 4 31B',
}


def read(path):
    return json.loads(path.read_text())


def identity(key):
    return key.removeprefix('historical/') if key.startswith('historical/') else key.split('/')[0]


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |',
                      '| ' + ' | '.join(['---'] * len(headers)) + ' |',
                      *['| ' + ' | '.join(map(str, row)) + ' |' for row in rows]])


def main():
    comparison = read(ROOT / 'comparison.json')
    scores = read(ROOT / 'scores.json')
    adjustments = read(ROOT / 'checklist-adjudications.json')['reviews']
    old_adjustments = read(Path('runs/medical-fidelity-v1/checklist-adjudications.json'))['reviews']
    audit = read(ROOT / 'claim-audit-reviewed.json')
    campaign = read(ROOT / 'campaign-status.json')
    tasks = read(ROOT / 'task-statuses.json')
    adjudicated_rows = {}
    metrics = []
    for key, original in scores.items():
        rows = deepcopy(original)
        for row in rows:
            review = (old_adjustments.get(identity(key) + ':' + row['check_id'], {})
                      if key.startswith('historical/') else adjustments.get(key + ':' + row['check_id'], {}))
            for mode in ['raw', 'delivered']:
                if review.get(mode + '_credit'):
                    assert row[mode]['available']
                    row[mode]['matched'] = True
        adjudicated_rows[key] = rows
        c = comparison['results'][key]
        m = {'key': key, 'model': identity(key), 'label': LABELS[identity(key)],
             'arm': 'historical' if key.startswith('historical/') else key.split('/')[1],
             'valid_tasks': c['valid_tasks'], 'invalid_tasks': c['invalid_tasks'],
             'automatic': c['counts'], 'adjudicated': {mode: summarize(rows, mode) for mode in ['raw', 'delivered']},
             'tokens': c.get('tokens'), 'boundary_recovered_attempts': c.get('boundary_recovered_attempts')}
        if key.startswith('historical/'):
            assert m['adjudicated'] == c['adjudicated_counts']
        for mode in ['raw', 'delivered']:
            s = m['adjudicated'][mode]
            s['unavailable_checks'] = s['required_checks'] - s['checks_with_parseable_section']
            s['available_unmatched_checks'] = s['missing_or_wrong'] - s['unavailable_checks']
            assert s['matched'] + s['available_unmatched_checks'] + s['unavailable_checks'] == 161
        article_ids = sorted({r['record_id'].split(':')[0] for r in rows})
        m['by_article'] = {aid: {mode: summarize([r for r in rows if r['record_id'].split(':')[0] == aid], mode)
                                for mode in ['raw', 'delivered']} for aid in article_ids}
        m['macro_article_raw_retention'] = sum(v['raw']['checklist_retention'] for v in m['by_article'].values()) / len(article_ids)
        metrics.append(m)
    models = sorted({m['model'] for m in metrics})
    delivered = sorted([max((m for m in metrics if m['model'] == model),
                           key=lambda m: (m['adjudicated']['delivered']['matched'], m['adjudicated']['raw']['matched']))
                        for model in models], key=lambda m: m['adjudicated']['delivered']['matched'], reverse=True)
    raw = sorted([max((m for m in metrics if m['model'] == model),
                     key=lambda m: m['adjudicated']['raw']['matched']) for model in models],
                 key=lambda m: (m['adjudicated']['raw']['matched'], m['adjudicated']['delivered']['matched']), reverse=True)
    out = {'reference_sha256': comparison['reference_sha256'],
           'metric_notice': 'Partial typed-field source-checklist retention, not medical accuracy. Semantic corrections are explicit. Best arms selected post hoc from one run each.',
           'delivery_ranking': [m['key'] for m in delivered], 'raw_coverage_ranking': [m['key'] for m in raw],
           'metrics': metrics, 'claim_audit_counts': audit['counts'], 'campaign': campaign}
    (ROOT / 'rankings.json').write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n')
    (ROOT / 'adjudicated-scores.json').write_text(json.dumps({m['key']: m['adjudicated'] for m in metrics}, indent=2) + '\n')
    fields = ['model', 'arm', 'raw_automatic', 'raw_adjudicated', 'raw_available_unmatched', 'raw_unavailable',
              'delivered_automatic', 'delivered_adjudicated', 'valid_tasks', 'invalid_tasks', 'primary_output_tokens_per_second']
    with (ROOT / 'metrics.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for m in metrics:
            a = m['adjudicated']
            writer.writerow({'model': m['model'], 'arm': m['arm'], 'raw_automatic': m['automatic']['raw']['matched'],
                             'raw_adjudicated': a['raw']['matched'], 'raw_available_unmatched': a['raw']['available_unmatched_checks'],
                             'raw_unavailable': a['raw']['unavailable_checks'], 'delivered_automatic': m['automatic']['delivered']['matched'],
                             'delivered_adjudicated': a['delivered']['matched'], 'valid_tasks': m['valid_tasks'],
                             'invalid_tasks': m['invalid_tasks'], 'primary_output_tokens_per_second': (m['tokens'] or {}).get('aggregate_output_tokens_per_second', '')})

    lines = ['# K2 versus previous extraction models: October 1, 2026', '',
        '**Recommendation.** Glimmer delivers the most checked structured facts through the current validator. K2-375B/low is the strongest candidate for improving source coverage: 156/161 checked fields before validation, versus Spark 143, Glimmer 125 and Inkling 139. K2-375B/medium delivers more facts than low (78 versus 54), but retains fewer before validation (131 versus 156). Choose low for the next extraction/repair experiment and keep medium and Glimmer as delivery controls; this run does not establish that low is ready for a production dataset.', '',
        '**Campaign integrity.** All four GPU stages completed and all four owned model checkpoints were deleted. All 16 configurations have 176 primary task results and 11 patient bundles. First-attempt prompt hashes match the frozen requests, and all automatic scores reproduce exactly. The four historical scores also reproduce against the same reference. This confirms completed inference, not universally valid or clinically correct outputs.', '',
        '**Comparison population.** Nine frozen, previously license-audited PMC sources: seven case articles, 11 patients (10 humans and one cat), and two negative controls. Each patient has 14 clinical-domain tasks, one summary and one timeline. The primary comparison uses the source-checked roster, so these results isolate downstream extraction rather than end-to-end patient discovery. Each IFM arm separately adds nine roster and 31 text-based figure-attribution tasks. The terminal totals of 216 combine these secondary tasks with the 176 primary tasks; the tables below consistently use 176.', '',
        '**Meaning of the scores.** The source checklist has 161 required typed-field checks plus 36 forbidden patterns. Retained means the expected fields agree with the source labels, with explicit semantic equivalence credits; delivered additionally requires the whole section to pass validation. A missing or unmatched check can be an omission, an unavailable response, an incorrect value, or a correct fact left in prose/an unsuitable typed field. It is not necessarily a medical error. Correct checked values can coexist with additional unsupported modifiers or wrong claims elsewhere. Valid tasks include empty but valid sections and do not measure medical accuracy.', '',
        'The tables use the existing historical semantic corrections and 48 new explicit K2 equivalences: broader examination/assay names, CRP/WBC suffixes, IHC positivity stored in `interpretation`, age-unit wording, negative serology wording, and the source-supported `<= 8 mm` bound. Numeric values left only in prose, missing actions, and unnormalized unit/comparator values receive no typed-field credit. The original reference and all predictions remain unchanged. Automatic counts are retained alongside the reviewed counts in the full arm table and CSV.', '',
        '**Ranking by facts delivered through the current pipeline.** Each model uses its best observed delivery arm; ties use raw coverage. Arm selection is post hoc, and this ranking measures the configured model plus the current parser/validator.', '',
        table(['Rank', 'Model / arm', 'Raw retained / missing of 161', 'Delivered / 161', 'Valid tasks', 'Invalid tasks'],
              [(n, m['label'] + ' / ' + m['arm'], f"{m['adjudicated']['raw']['matched']} / {m['adjudicated']['raw']['missing_or_wrong']}",
                f"{m['adjudicated']['delivered']['matched']} ({100*m['adjudicated']['delivered']['checklist_retention']:.1f}%)", m['valid_tasks'], m['invalid_tasks']) for n, m in enumerate(delivered, 1)]), '',
        '**Ranking by checked source coverage before validation.** This is the more useful selection table for a model whose local repair and validation still need improvement. Spark and K2-32B/high tie on pooled retention; no clinical superiority is established by the tie ordering.', '',
        table(['Model / best raw arm', 'Retained', 'Available but unmatched', 'Unavailable', 'Mean per-article retention'],
              [(m['label'] + ' / ' + m['arm'], f"{m['adjudicated']['raw']['matched']}/161", m['adjudicated']['raw']['available_unmatched_checks'],
                m['adjudicated']['raw']['unavailable_checks'], f"{100*m['macro_article_raw_retention']:.1f}%") for m in raw]), '',
        '**All reasoning and sampling arms.** `matched` uses the historical prompt/gates with low reasoning, temperature 0, top-p 1 and an 8,192-token initial cap / 16,384 retry cap. IFM low/medium/high use temperature 1, top-p .95 and the same 32,768-token cap. High is the publisher recommendation saved in the pinned campaign metadata; low and medium are controlled effort alternatives. All three supported levels were exercised for each checkpoint. The improved IFM-versus-matched scores cannot be attributed solely to reasoning because sampling and output budgets also change. There is one stochastic run per IFM arm, with bounded repair; no replicated significance estimate.', '',
        table(['Model', 'Arm', 'Raw auto / reviewed', 'Delivered auto / reviewed', 'Valid / invalid primary tasks', 'Primary output tokens/s'],
              [(m['label'], m['arm'], f"{m['automatic']['raw']['matched']} / {m['adjudicated']['raw']['matched']}",
                f"{m['automatic']['delivered']['matched']} / {m['adjudicated']['delivered']['matched']}", f"{m['valid_tasks']} / {m['invalid_tasks']}",
                f"{m['tokens']['aggregate_output_tokens_per_second']:.1f}") for m in sorted(metrics, key=lambda m: (m['model'], m['arm'])) if m['tokens']]), '',
        'More reasoning did not monotonically improve retention or validation. Within the comparable IFM arms, K2-375B and MoVA retain most checked raw fields at low; K2-32B at high; K2-7B at medium. K2-7B matched retains more checked raw fields than its IFM variants, but with substantially more medical/representation errors in its sampled claims. High gives the best K2-7B delivery score. These are observations for this run, not universal optimal settings.', '',
        '**Manual source review.** I reviewed 213 new, seeded claims against the full frozen article text, patient sections, table headers and chronology. The 231-claim plan left 18 samples unavailable. The earlier study contributes 130 reviewed claims, making 343 reviewed claims across both studies. The sample chooses two clinical claims from preferably distinct domains and one summary claim per patient/configuration when available. It is an unblinded agent review, not physician adjudication; model-dependent availability changes the sampled domains. Counts are diagnostic, not global clinical precision.', '',
        table(['Model / arm', 'Supported', 'Unsupported material field', 'Uncertain', 'Unavailable planned samples'],
              [(LABELS[identity(k)] + ' / ' + ('historical' if k.startswith('historical/') else k.split('/')[1]),
                counts.get('supported', 0), counts.get('unsupported', 0), counts.get('uncertain', 0), 33-sum(counts.values()))
               for k, counts in [*[(m['key'], comparison['results'][m['key']]['claim_audit']['raw']) for m in metrics if m['key'].startswith('historical/')],
                                  *sorted(audit['counts'].items())]]), '',
        'Unsupported includes clinically incorrect structured meanings as well as patient/timing errors and invented status/specimen details. A correct number with an unsupported modifier is not counted as wholly supported. Uncertain preserves ambiguous timing or scope instead of forcing a wrong/correct classification. General article background can be source-supported but remains unsuitable as a patient fact; those scope notes are retained in the audit.', '',
        '**Concrete source findings.**', '',
        '- MoVA/low assigned mesocolon Case 2’s 18×15×12 cm CT dimensions and sigmoid/bladder displacement to Case 1’s summary. Case 1 instead has a 16.7×12.3×13.4 cm mass involving the pancreatic body/tail. The summary was rejected, but this is a genuine attribution error beyond the 161-field checklist. [PMC source](https://pmc.ncbi.nlm.nih.gov/articles/PMC12285374/).',
        '- K2-375B/medium encoded packed-red-cell transfusions as a surgical complication. The article says transfusions were required and the postoperative course was uneventful. This structured error passed validation. [PMC source](https://pmc.ncbi.nlm.nih.gov/articles/PMC12285374/).',
        '- K2-7B/matched placed dense left hemiplegia at initial presentation. The patient initially moved all extremities; the deficit appeared after laparotomy. It also put catecholamine infusion and IV ethanol in nonpharmacologic therapy, and CRRT in medication ingredients. [Bullet source](https://pmc.ncbi.nlm.nih.gov/articles/PMC13294519/), [poisoning source](https://pmc.ncbi.nlm.nih.gov/articles/PMC12802722/).',
        '- K2-375B/low labeled historical anal fissures inactive, although the source did not establish current status. K2-375B/medium strengthened negative D. immitis tests into a definitively refuted infection. Both illustrate source certainty/status preservation rather than numerical extraction errors.',
        '- Additional targeted probes found K2-32B/high and low placing the Anti-Xa treatment target into resulted observations; K2-32B/medium encoded non-indicated dialysis as declined. MoVA/low called serum amyloid A 37.3 within its 5–10 reference interval, despite also marking the result high. These probes are outside the seeded sample and are saved separately.',
        '- Correct results also occur in rejected sections: admission glucose 5.8, calcium 1.27, 48-hour sodium 135 and AST 53 are source-supported in the reviewed claims. The three poisoning patients are distinguished, but correct results can still carry bad timing, unsupported specimen details or malformed citation fields.', '',
        'All 36 frozen forbidden patterns have zero hits in available final sections. Some sections are unavailable, and these patterns do not cover the errors above. Zero hits therefore do not mean zero hallucinations. Full claim payloads, source hashes, verdicts and rationales are retained in `claim-audit-reviewed.json`; the additional probes are in `additional-source-checks.json`.', '',
        '**Why useful facts disappear.** Whole-section validation dominates delivery loss. K2-375B/medium retains 131 checked fields but delivers 78; low retains 156 and delivers 54. In the rejected medium cat observations, 38/50 items pass the same existing source/schema validator when tested individually with the original section metadata. In low’s bullet case, 18/28 rejected-section items pass individually. This does not prove clinical correctness, but it supports testing local quarantine/repair while preserving unaffected items. No facts were salvaged into exports during this analysis.', '',
        'Other failures include nonliteral temporal evidence, noncontiguous quotes, invalid enums and section-level spelling mistakes. K2-375B/low’s poisoning Case 3 observations use `limations` instead of `limitations`, causing rejection of the entire 53-item section. Merely loosening validation would also let through real patient/status/action mistakes, so source-aware local repair and attribution checks must accompany any salvage strategy.', '',
        '**Observed GPU throughput.** Aggregate rates below sum all replicas and all four arms, including secondary calls, and divide total tokens by measured evaluation time. They exclude queue wait and startup/warmup. Output includes generated reasoning and final answers, not just useful facts. Separate reasoning-token usage is not reliable for interpreting the recorded zero subtotals; do not read them as no reasoning.', '',
        table(['Model', 'GPUs / topology', 'Output tokens/s', 'Tokens/GPU-second', 'GPU-stage hours'],
              [(LABELS[r['model']['name']], f"{r['throughput']['gpu_count']} / {r['model']['replicas']}×TP{r['model']['tensor_parallel']}",
                f"{r['throughput']['aggregate_output_tokens_per_second']:.1f}", f"{r['throughput']['output_tokens_per_gpu_second']:.1f}",
                f"{r['throughput']['gpu_stage_seconds']/3600:.2f}") for r in campaign['models']]), '',
        'K2-7B generates fastest, but that does not make it the best extractor. MoVA is slower than 32B in this measured setup and delivers fewer checked fields. Hosted-model timings cannot be compared directly to these B200 rates. These are 4-GPU or 2-GPU measurements, not measured saturation throughput on eight B200s. The saved campaign articles/hour measure completion of all four arms plus secondary tasks and are not single-pass corpus processing rates.', '',
        '**Evaluation limits and next experiment.** 91/161 positive checks come from one three-patient poisoning article, and 76 checks concern numeric values/units. Mean article retention is included to expose this imbalance, but seven articles remain too few for a definitive architecture ranking. The sources overlap prior development work. Provider routes, quantization, output budgets, reasoning settings and repair behavior differ. The 68-check chunking/refinement experiments are separate development subsets and are not pooled into this 161-check ranking.', '',
        'For the next controlled benchmark I would use K2-375B/low as the coverage candidate, Glimmer as the current delivery baseline, and K2-32B/high as the smaller local candidate. Test per-item validation/quarantine plus targeted repairs that preserve already supported fields, bind patient/column/time explicitly, retain normal results, and separate treatment goals from measured observations. Measure supported facts and usable patient timelines per GPU-hour on a larger clinician-reviewed set. Keep a separate caption/image attribution stage; no K2 pixel interpretation was evaluated here.', '',
        'Secondary rosters and figure-attribution outputs exist, but semantic figure attribution and patient-identity mapping remain pending; task validity is not attribution accuracy. The primary scores cannot establish end-to-end patient discovery or figure fidelity. No comparable Nemotron run was found locally. Cohere and Spark Contributor had prior endpoint/access failures, which cannot be ranked as medical-quality failures.', '',
        '**Saved artifacts and reproduction.** The results archive was streamed without fully extracting it or downloading weights. Original results were not changed. The detailed CSV, scores and source reviews are under `runs/hpg-comparison-20261001/`.', '',
        '```sh',
        '.venv/bin/python scripts/compare_hpg_results.py /Users/mkieffer/Downloads/run-20261001-123645-results.tar.gz',
        '.venv/bin/python scripts/render_k2_comparison.py',
        '```', '',
        'Rendering requires the saved manual adjudications. Those are human-readable review records, not newly generated labels or API calls.']

    # Show source-stratified coverage using each model's best raw arm.
    source_names = {'PMC13314001.1': 'Fibro-dentinoma child', 'PMC13314005.1': 'Mandibular osteosarcoma',
                    'PMC13294519.1': 'Bullet embolism', 'PMC12773240.1': 'Dolichoectasia (2 patients)',
                    'PMC12802722.1': 'Ethylene glycol (3 patients)', 'PMC12285374.1': 'Mesocolon tumors (2 patients)',
                    'PMC10998798.1': 'D. repens cat'}
    lines += ['', '**Source-stratified raw coverage, each model’s best raw arm.**', '',
              table(['Article', *[m['label'] for m in raw]],
                    [(name, *[f"{m['by_article'][aid]['raw']['matched']}/{m['by_article'][aid]['raw']['required_checks']}" for m in raw])
                     for aid, name in source_names.items()])]
    destination = Path('reports/K2_MODEL_COMPARISON.md')
    destination.write_text('\n'.join(lines) + '\n')
    assert len(audit['reviews']) == 213
    assert sum(sum(c.values()) for c in audit['counts'].values()) == 213
    assert not campaign['failed_models'] and not campaign['weight_storage_exists']
    assert all(len(t) == 176 for t in tasks.values())
    print(destination)
    for m in delivered:
        print(m['key'], m['adjudicated']['raw']['matched'], m['adjudicated']['delivered']['matched'], m['valid_tasks'], m['invalid_tasks'])


if __name__ == '__main__':
    main()
