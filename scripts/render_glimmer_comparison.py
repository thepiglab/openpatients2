"""Render measured results and explicit source reviews without changing predictions."""
from copy import deepcopy
import csv
import json
from pathlib import Path

from openpatients2.fidelity import summarize

ROOT = Path('runs/glimmer-comparison-20261002')
SELECTED = ['glimmer-fp8/meta_medium', 'glimmer-fp8/meta_medium_dflash',
            'glimmer-nf4/meta_xhigh', 'glimmer-nvfp4/meta_xhigh']
LABELS = {'glimmer-fp8': 'FP8', 'glimmer-nvfp4': 'NVFP4', 'glimmer-nf4': 'NF4'}


def read(path):
    return json.loads(Path(path).read_text())


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |',
                      '| ' + ' | '.join(['---'] * len(headers)) + ' |',
                      *['| ' + ' | '.join(map(str, row)) + ' |' for row in rows]])


def main():
    comparison = read(ROOT / 'comparison.json')
    scores = read(ROOT / 'scores.json')
    reviews = read('reports/glimmer-b200-checklist-adjudications.json')['reviews']
    audit = read('reports/glimmer-b200-claim-audit.json')
    figures = read('reports/glimmer-b200-figure-audit.json')
    cells = read(ROOT / 'cells.json')
    metrics = comparison['results']
    for key in SELECTED:
        rows = deepcopy(scores[key])
        for row in rows:
            review = reviews.get(key + ':' + row['check_id'], {})
            for mode in ['raw', 'delivered']:
                if review.get(mode + '_credit'):
                    assert row[mode]['available'] and not row[mode]['matched']
                    row[mode]['matched'] = True
        metrics[key]['adjudicated'] = {mode: summarize(rows, mode) for mode in ['raw', 'delivered']}
    (ROOT / 'comparison-reviewed.json').write_text(json.dumps(comparison, indent=2) + '\n')
    with (ROOT / 'reviewed-metrics.csv').open('w', newline='') as output:
        writer = csv.writer(output)
        writer.writerow(['configuration', 'raw_automatic', 'raw_reviewed', 'delivered_automatic',
                         'delivered_reviewed', 'valid_tasks', 'invalid_tasks', 'supported_sample',
                         'unsupported_sample', 'uncertain_sample'])
        for key in SELECTED:
            m = metrics[key]; a = audit['counts'][key]
            writer.writerow([key, m['automatic']['raw']['matched'], m['adjudicated']['raw']['matched'],
                m['automatic']['delivered']['matched'], m['adjudicated']['delivered']['matched'],
                m['valid_tasks'], m['invalid_tasks'], a.get('supported', 0),
                a.get('unsupported', 0), a.get('uncertain', 0)])
    lines = ['# Glimmer on eight B200s: October 2, 2026', '',
        '**Recommendation.** Use RedHatAI FP8 with medium reasoning. Ordinary decoding delivered the most source-checked typed fields after reviewing equivalent labels (127/161). DFlash/medium delivered 125/161 and is the throughput choice: its full primary evaluation ran in 61.6 seconds versus 218.8 seconds, and its fastest warmed serving cell reached 10,998 output tokens/s across eight B200s. Keep ordinary FP8/medium as the quality control; the two-field difference is from one stochastic run per arm, not an established DFlash quality penalty.', '',
        '**Campaign integrity.** All three verifier downloads completed, all three GPU stages completed, and all three cleanup stages deleted their owned checkpoints. The archive reports no failed models and no remaining weight storage. All 2,992 primary task records are present: 17 configurations × 176 tasks, with 11 patient bundles per configuration. Frozen first-prompt hashes and the fixture manifest match, and every saved automatic score reproduces exactly. Six optional serving-layout trials failed; those failures did not invalidate the completed quality runs.', '',
        '**Models.** BF16 verifier weights were excluded as requested. All variants used the campaign’s pinned official Meta chat template, overriding quantizer templates. Four reasoning strengths were exercised. This gauntlet evaluated text, tables and captions; no image pixels or visual interpretation were evaluated.', '',
        table(['Checkpoint', 'Hugging Face', 'Pinned revision'], [
            ('FP8 block', '[RedHatAI](https://huggingface.co/RedHatAI/Muse-Glimmer-30B-FP8-block)', '1deb4641ff84f9a728dd11b27cac1f6a02a9ed14'),
            ('Mixed NVFP4', '[NVIDIA](https://huggingface.co/nvidia/Muse-Glimmer-30B-NVFP4)', '47818374517751c48c55cde2621594926b1888b6'),
            ('BNB NF4', '[Unsloth](https://huggingface.co/unsloth/Muse-Glimmer-30B-unsloth-bnb-4bit)', 'ecedda395d4d099b477d135a9a573eedaa5a7aad')]), '',
        'The successful DFlash assistant was [Meta’s Muse-Glimmer-30B-assistant](https://huggingface.co/meta-models/Muse-Glimmer-30B-assistant), pinned to e8192f3a8f617f74be2ce220360c89ef4789f39f. DFlash used its 15 predicted tokens plus the trained anchor position.', '',
        '**Best configurations, with source-reviewed equivalences.** Raw counts describe the last parseable extraction before validation; delivered counts additionally require its whole section to pass the frozen validator. Required checks total 161. Valid/invalid counts total 176 primary tasks, including summaries, timelines and valid empty clinical sections. These counts are not comprehensive medical accuracy or a count of all generated facts.', '',
        table(['Configuration', 'Raw auto / reviewed', 'Delivered auto / reviewed', 'Valid / invalid', 'Primary batch seconds'], [
            (key, f"{metrics[key]['automatic']['raw']['matched']} / {metrics[key]['adjudicated']['raw']['matched']}",
             f"{metrics[key]['automatic']['delivered']['matched']} / {metrics[key]['adjudicated']['delivered']['matched']}",
             f"{metrics[key]['valid_tasks']} / {metrics[key]['invalid_tasks']}",
             f"{metrics[key]['tokens']['measurement_wall_seconds']:.1f}") for key in SELECTED]), '',
        f'The {len(reviews)} explicit equivalence credits follow the earlier review policy: CD34/STAT6/SATB2 positivity in `interpretation`, arterial/ABG pH, a reordered carbon-dioxide assay name, an imaging result with a generic CT name, a CRP name suffix, and the source-supported `<=8 mm` upper bound. They do not alter predictions or the reference. Missing typed time fields, values retained only in prose, unspecified comparators and unnormalized case-sensitive units receive no credit. Only the four selected configurations received this additional checklist adjudication; the complete arm table below remains automatic.', '',
        '**Comparison with prior runs.** The historical and K2 rows below use their previously saved source-reviewed equivalences. All rows use the same 161-check reference and frozen first prompts. Provider routing, sampling, reasoning and token caps differ, so this compares configured pipelines rather than isolating quantization quality.', '']
    historical = read('runs/hpg-comparison-20261001/rankings.json')
    past = {m['key']: m for m in historical['metrics']}
    older = []
    for key in historical['delivery_ranking']:
        m = past[key]
        older.append((m['label'] + ' / ' + m['arm'], m['adjudicated']['raw']['matched'],
                      m['adjudicated']['delivered']['matched'], m['valid_tasks'], m['invalid_tasks']))
    lines += [table(['Configuration', 'Raw reviewed / 161', 'Delivered reviewed / 161', 'Valid', 'Invalid'],
        [(k, metrics[k]['adjudicated']['raw']['matched'], metrics[k]['adjudicated']['delivered']['matched'],
          metrics[k]['valid_tasks'], metrics[k]['invalid_tasks']) for k in SELECTED] + older), '',
        'Ordinary local FP8/medium delivers 27 more checked fields than historical hosted Glimmer (127 versus 100); DFlash delivers 25 more. That is better measured delivery under these settings, not proof that local quantization improved the base model. No comparable Nemotron result was found in the existing comparison; endpoint failures for other models are not clinical-quality scores.', '',
        '**Manual medical source review.** All 131 fixed-seed sampled claims were inspected against the actual frozen article text, patient sections, table columns and chronology. The plan was 132 claims; one NF4 summary sample was unavailable. Material unsupported modifiers and wrong structured semantics count as unsupported. Uncertain preserves ambiguity. This was unblinded agent review, not physician adjudication or a population precision estimate.', '',
        table(['Configuration', 'Supported', 'Unsupported', 'Uncertain', 'Unavailable planned'], [
            (k, audit['counts'][k].get('supported', 0), audit['counts'][k].get('unsupported', 0),
             audit['counts'][k].get('uncertain', 0), 33-sum(audit['counts'][k].values())) for k in SELECTED]), '',
        '- The reviewed FP8/DFlash claims correctly distinguished the three poisoning patients, the two mesocolon tumors, the two VBDE patients, and the infected cat. Additional medication inspection confirmed 73 mg alteplase and 10 g of 10% ethanol in poisoning Case 1, 0.9 mg/kg rt-PA in Case 3, and heparin cessation/protamine reversal in the bullet case.',
        '- NVFP4/xhigh put a negative immunohistochemistry result sentence in `time.text`, classified transfer to a psychiatric ward as discharge, and removed “likely” from a proposed reinfarction cause. These semantic defects can pass literal evidence/schema checks.',
        '- NF4/xhigh added an unstated serum specimen to a creatinine measurement and removed the same causal qualifier. Correct numbers do not make every modifier supported.',
        '- Ordinary FP8/medium anchored a one-month repeat MRI to clinic presentation, although the source only establishes the preceding MRI as the contextual antecedent. FP8 configurations also classify an inpatient rehabilitation transfer as discharge where facility/hospital scope is ambiguous.',
        '- The source itself contains conflicts: poisoning ABG table headers say on admission while prose places some ABGs later; mesocolon Case 1 prose calls Ki67 negative while its caption describes occasional labeled nuclei. Extraction should preserve conflicting source statements, not invent a reconciled clinical truth.',
        '- An additional completeness probe found FP8/DFlash’s bullet `care_plans` section empty despite the explicit Anti-Xa treatment target 0.3–0.5. The target should be captured as a goal, not fabricated as a measured laboratory result. Its procedure section also lacks a typed failed-removal finding despite that failure being present in the timeline.', '',
        'All 36 forbidden patterns had zero hits in available final sections. Those probes do not cover the unsupported modifiers above and cannot establish zero hallucinations. The audit includes accepted and rejected sections; delivered-only verdict counts are saved separately.', '',
        '**Figure attribution and patient discovery.** Secondary tasks use the reference roster independently of discovery. Therefore primary fact counts do not establish end-to-end success when a discovery roster fails. FP8/DFlash discovered and correctly distinguished all humans and the two negative controls, but its cat roster failed to parse: 8/9 roster tasks succeeded. NVFP4/xhigh had 9/9 source-consistent roster identities, including the nonhuman cat.', '',
        table(['Configuration', 'Roster valid / 9', 'Figure valid / 31', 'Whole-figure source-supported raw / 31', 'Supported and delivered / 31'], [
            ('FP8 / medium + DFlash', 8, 19, figures['counts']['glimmer-fp8/meta_medium_dflash']['supported'], figures['delivered_counts']['glimmer-fp8/meta_medium_dflash']['supported']),
            ('NVFP4 / xhigh', 9, 26, figures['counts']['glimmer-nvfp4/meta_xhigh']['supported'], figures['delivered_counts']['glimmer-nvfp4/meta_xhigh']['supported'])]), '',
        'All 62 figure decisions in these two configurations received a text-source review. A single wrong panel subject or missed patient link makes the whole figure not wholly supported. Supported includes correct abstention for cohort examples without identifiable local patients and nonpatient plant diagrams; it does not measure image diagnosis.', '',
        '- Both models correctly proposed shared ownership of the three-patient poisoning timeline, but newline changes in its caption quote caused rejection. FP8/DFlash correctly separated the cat’s microfilariae panel from the external canine comparison; that section also failed citation formatting.',
        '- Both left mesocolon Figure 4 unresolved even though Case 2 explicitly cites panels 4A–E. Reproducing the actual focused prompt shows it omitted b00006, the paragraph containing that link. Frozen parser 1.1 lacks JATS cross-reference metadata; the current parser is 1.3 and retains it. This is a benchmark/context-selection defect, not evidence that the model ignored supplied case prose.',
        '- Both called osteosarcoma Figure 2B a patient specimen. The caption describes a negative-image version of CBCT, so it is patient imaging. NVFP4 also missed three histology/IHC patient links; FP8/DFlash had two unparseable figure outputs.', '',
        '**Throughput.** Each cell runs the same 32 short/long task prompts, four copies per prompt, two repeats: 256 timed requests. All use medium reasoning, temperature 1, top-p .95, top-k 64, natural completion and equal 32,768-token caps. Prefixes are explicitly warmed. Rates pool all eight B200s and exclude startup, warmup and queue time; output includes reasoning plus answers, not just useful clinical facts. Recorded reasoning subtotals of zero do not establish zero reasoning.', '',
        table(['Checkpoint', 'Layout', 'In-flight / replica', 'Output tokens/s, all 8 GPUs', 'First-pass valid tasks/s', 'Valid / invalid of 256'], [
            (LABELS[key.split('/')[0]], cell['layout']['name'], cell['concurrency_per_replica'],
             f"{cell['aggregate_output_tokens_per_second']:,.0f}", f"{cell['valid_tasks_per_second']:.2f}",
             f"{cell['valid_tasks']} / {cell['invalid_tasks']}")
            for key, cell in sorted(cells.items())]), '',
        'The fastest cell is FP8 + DFlash, eight independent one-GPU replicas, 16 in-flight requests per replica (128 across the node): 10,998 tokens/s and 4.65 first-pass valid tasks/s. This is 4.50× its same-topology non-speculative token rate of 2,445, or 4.07× the best non-speculative FP8 topology (four TP2 replicas: 2,704). Repeat rates were 12,053 and 10,144 tokens/s; this is a short warmed benchmark, not a guaranteed corpus-ingestion rate.', '',
        'Full 176-task DFlash quality was evaluated at eight in-flight requests per replica, not 16. Before adopting 16, repeat the full factuality/figure benchmark there. NVFP4’s fastest DFlash cell reached 9,250 tokens/s at 16 per replica, but its DFlash/medium arm delivered only 69 automatic checked fields. Its best quality arm is ordinary xhigh; do not attribute medium-speed numbers to xhigh. NF4’s medium speed cells reached only 697 tokens/s; its best quality arm used xhigh and took 850.9 seconds for the primary batch.', '',
        '**What failed in the optional layouts.**', '',
        'DCP2 failed for FP8 and NVFP4 with `DCP not support sliding window.` The corresponding assertion is explicit in [vLLM 0.30’s sliding-window KV implementation](https://github.com/vllm-project/vllm/blob/v0.30.0/vllm/v1/kv_cache_interface.py). This combination should be skipped for this pinned engine rather than consuming another initialization trial.', '',
        'DSpark failed in both TP1 and TP2 for both quantizations because free GPU memory at startup was below the configured 90% budget: examples are 156.38 GiB free versus 160.52 GiB requested for FP8, and 149.63 versus 160.52 for NVFP4. This is the runtime’s startup memory gate, consistent with its [GPU worker memory sizing](https://github.com/vllm-project/vllm/blob/v0.30.0/vllm/v1/worker/gpu_worker.py). It does not establish an inference-quality or intrinsic DSpark-compatibility failure.', '',
        'The telemetry shows persistent allocations on devices before their DSpark replicas launched. During the FP8 first-wave replicas’ readiness wait, GPUs 2/3 held 21,872 MiB and GPUs 4–7 held 9,016 MiB despite those replicas not yet starting. NVFP4 likewise had 17,020 MiB on GPUs 2/3. Residual processes or CUDA allocations from earlier layouts are the leading explanation; the archive lacks per-process GPU inventories to establish the exact owner. `ServerGroup.stop()` waits for launchers and signals their groups but does not verify descendant termination and released GPU memory.', '',
        '**Most useful next changes, in order.**', '',
        '1. Benchmark the production citation recovery and targeted item repair as a separate arm, keeping the frozen regeneration control. The current HPG replay performs one complete-regeneration retry and does not use those production recovery paths. Preserve valid fields, attach exact source quotes/header evidence to failed items, and quarantine unresolved items with coverage marked limited.',
        '2. Make targeted repair work with validated nonempty documentation evidence and bounded batches of failed items. The current `ItemRepair.create` guard requires empty documentation evidence and at most eight pending items, so many of these valid envelopes would not activate it. Validate/freeze the envelope separately; never erase supported lab values or timing merely to pass a gate.',
        '3. Expand figure context through JATS links and case-level passages; use a full-text fallback when cross-reference metadata is absent or a locally known figure remains unresolved. Version this as a new figure arm instead of changing frozen comparison prompts. Reuse deterministic caption quote recovery while retaining original/corrected strings and source offsets.',
        '4. Add semantic gates for temporal expressions, patient/column attribution, stated versus inferred specimen/route, transfer versus discharge, probable causal statements, treatment goals versus actual observations, and imaging versus tissue specimens. A schema-valid record still needs these checks.',
        '5. Capture owned process trees and GPU process/memory snapshots per layout. After shutdown, terminate only owned remaining descendants and wait for the allocated devices to return to their pre-layout baseline before starting another layout. Fail clearly if they do not. Retest DSpark first in a fresh allocation with one replica before testing startup waves; a lower memory fraction can be a secondary controlled trial, not a substitute for cleanup.',
        '6. Repeat FP8 medium with and without DFlash across several seeds, including a full run at concurrency 16, then evaluate more held-out multi-patient, table-heavy and figure-heavy articles. Select supported fields and usable timelines per GPU-hour rather than generated-token rate alone.', '',
        '**Why preservation matters.** In FP8/DFlash’s rejected Case 1 poisoning observations, 30/33 final items pass the same validator individually. Three bad time fields reject the entire section. Its initial attempt had 52 observation items and its regeneration retained only 33. NF4/high lost 47 initially matched checks while gaining seven during retries (141 raw initially → 101 finally), even as task validity improved. A format-repair instruction alone does not enforce preservation. Individual validity is only a structural/source diagnostic and does not independently establish medical correctness.', '',
        '**All 17 arms, original automatic scores.** `matched` uses temperature 0, top-p 1, low reasoning and 8,192 initial / 16,384 retry output caps. Meta low/medium/high/xhigh use the publisher sampling above with equal 32,768 caps. Differences between matched and Meta arms cannot be assigned solely to reasoning.', '',
        table(['Checkpoint', 'Arm', 'Raw / 161', 'Delivered / 161', 'First valid / 176', 'Final valid / invalid', 'Primary output tokens/s'], [
            (LABELS[key.split('/')[0]], key.split('/')[1], m['automatic']['raw']['matched'],
             m['automatic']['delivered']['matched'], m['first_attempt_valid_tasks'],
             f"{m['valid_tasks']} / {m['invalid_tasks']}", f"{m['tokens']['aggregate_output_tokens_per_second']:,.0f}")
            for key, m in sorted(metrics.items())]), '',
        '**Limits.** These are nine development sources: seven case articles, 11 patients and two negative controls. The poisoning article contributes 91/161 required checks, and 76 checks concern numbers/units. Best arms were selected post hoc. Higher reasoning did not consistently improve delivery. No BF16 reference was measured, so quantization degradation cannot be isolated. No image pixels were inspected. Quality, preservation, figure linkage and throughput must be assessed separately.', '',
        '**Artifacts and reproduction.** The archive was streamed; weights/caches were not extracted or downloaded. The original archive and predictions remain unchanged. Source reviews and the report are in `reports/glimmer-b200-*.json` and this file; metrics and selected task payloads are under `runs/glimmer-comparison-20261002/`.', '',
        '```sh',
        'uv run --locked --python 3.12 --no-dev python scripts/analyze_glimmer_results.py /Users/mkieffer/Downloads/run-20261001-233830-results.tar.gz',
        'uv run --locked --python 3.12 --no-dev python scripts/render_glimmer_comparison.py',
        '```', '',
        'Rendering applies saved explicit adjudications; it does not call a model or create new clinical labels.']
    Path('reports/GLIMMER_B200_RESULTS.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps({'report': 'reports/GLIMMER_B200_RESULTS.md', 'selected_configurations': len(SELECTED),
                      'clinical_claims_reviewed': len(audit['reviews']), 'figure_decisions_reviewed': len(figures['reviews'])}))


if __name__ == '__main__':
    main()
