"""Prospective follow-up: batched span projection and retry -> local repair.

Run only after refinement_v1.py completes. The original outcomes/arms remain
unchanged; extension arms are explicitly marked and costed as extra dependencies.
"""
import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import time

from refinement_v1 import Study, ROOT, OLD, UNITS, DOMAINS, section, verified_spans
from openpatients2.data import write_json


class Extension(Study):
    def restore_annotations(self):
        for model in self.models:
            for pmcid, domains in DOMAINS.items():
                for domain in domains:
                    p = ROOT/'spans'/f"{model['id'].replace('/', '--')}-{pmcid}-{domain}.json"
                    self.spans[(model['id'], pmcid, domain)] = json.loads(p.read_text())

    async def recover_annotations(self, model, pmcid):
        """Bounded source chunks when observation tagging fails or is empty.

        Observations is explicitly defined; no ontology inventory is required.
        Original first-pass spans and calls are never overwritten.
        """
        original = self.spans.get((model['id'], pmcid, 'observations'))
        if original is None or original['verified_spans']:
            return
        article = self.articles[pmcid]
        calls, spans, rejected = [], [], []
        for start in range(0, len(article['segments']), 16):
            source = {**article, 'segments': article['segments'][start:start+16]}
            instruction = (
                'Mark exact mention spans for clinical OBSERVATIONS: laboratory analytes and results, vital signs, '
                'physical examination findings, imaging studies/results, pathology, microbiology and physiological '
                'tests. These categories are fully defined here; no external ontology or term list is needed. '
                'This is mention detection across all patients/background, not patient assignment. '
                'Include normal and abnormal values, repeated values, and negative findings. In tables, mark each '
                'complete data row separately. For prose, use a short exact contiguous phrase identifying the finding '
                'or examination, retaining nearby negation. Output {"spans":[{"segment_id":"b00001",'
                '"quote":"exact source text"}],"limitations":[]}. Do not omit actual mentions just because their '
                'patient or clinical role is ambiguous; a subsequent context pass will decide that.'
            )
            call = await self.call(model, 'observations', self.evidence_messages(source, instruction, {}),
                {'phase': 'span_recovery', 'pmcid': pmcid, 'domain': 'observations', 'chunk': start//16}, max_tokens=6144)
            calls.append(call)
            good, bad = verified_spans(call.get('data'), article, 'observations')
            for s in good:
                s['span_id'] = f"observations-recovery-{start//16}:"+s['span_id'].split(':')[1]
            spans.extend(good); rejected.extend(bad)
        value = {'verified_spans': spans, 'rejected_spans': rejected, 'result': original['result'],
                 'recovery_calls': calls, 'recovery_reason': 'failed_or_empty_observation_spans'}
        path = ROOT/'recovered-spans'/f"{model['id'].replace('/', '--')}-{pmcid}-observations.json"
        write_json(path, value); value['recovery_path'] = str(path)
        self.spans[(model['id'], pmcid, 'observations')] = value

    async def batched_unit(self, model, unit):
        async with self.jobs:
            pmcid, pid, task = unit
            aid = f"{model['id'].replace('/', '--')}-{pmcid}-{pid}-{task}"
            previous = json.loads((ROOT/'outcomes'/f'{aid}-spans_context.json').read_text())
            annotations = self.spans[(model['id'], pmcid, task)]
            spans = annotations['verified_spans']
            base_messages = self.extraction_messages(pmcid, pid, task)
            calls, context_calls, items, failures, duplicates, seen = [], [], [], [], [], set()
            for start in range(0, len(spans), 6):
                batch = spans[start:start+6]
                ids = {s['span_id'] for s in batch}
                related = [s for domain in DOMAINS[pmcid] if domain != task
                           for s in self.spans[(model['id'], pmcid, domain)]['verified_spans']
                           if s['segment_id'] in {b['segment_id'] for b in batch}]
                context = await self.disambiguate(model, pmcid, pid, task, batch+related)
                context_calls.append(context['result'])
                hints = {'batch_spans': batch, 'context_decisions': context['decisions'],
                         'cross_domain_mentions_in_same_segments': related}
                messages = deepcopy(base_messages)
                messages[-1]['content'] += (
                    '\nBATCH SCOPE:\n' + json.dumps(hints, ensure_ascii=False)
                    + '\nExtract this patient\'s facts whose test/drug/condition/procedure mentions occur in these '
                    'batch spans only. Use the FULL source above for patient assignment, values, all corresponding '
                    'times, negation and related context. Context decisions are untrusted aids. A table row contains '
                    'other patients\' columns: use only the target column. Preserve normal results. Do not extract '
                    'unrelated entities outside this batch. Return a normal section object, possibly empty. '
                    'This is a partial-source batch, so coverage must be limited with an explicit limitation.'
                )
                result = await self.call(model, task, messages,
                         {'phase': 'batch_projection', 'pmcid': pmcid, 'pid': pid, 'domain': task,
                          'mode': 'spans_batches', 'batch': start//6}, max_tokens=8192)
                calls.append(result)
                candidate = result.get('data')
                if not isinstance(candidate, dict) or not isinstance(candidate.get('items'), list):
                    failures.append({'batch': start//6, 'span_ids': sorted(ids), 'errors': result.get('errors')})
                    continue
                for item in candidate['items']:
                    # Only byte-equivalent objects are deduplicated, including
                    # evidence and time. Similar values may be distinct events.
                    key = json.dumps(item, sort_keys=True, ensure_ascii=False)
                    if key in seen:
                        duplicates.append(item)
                    else:
                        seen.add(key); items.append(item)
            seed = {'data': section(items, f'Span-batch projection; {len(failures)} failed batches; entity coverage unverified.'),
                    'status': 'partial' if failures else 'valid', 'metrics': {}, 'attempt_responses': []}
            auxiliary = {'annotation_paths': [self.spans[(model['id'], pmcid, d)].get('recovery_path',
                         str(ROOT/'spans'/f"{model['id'].replace('/', '--')}-{pmcid}-{d}.json")) for d in DOMAINS[pmcid]],
                         'projection_calls': calls, 'batch_context_calls': context_calls,
                         'batch_failures': failures, 'exact_duplicate_items_removed': duplicates,
                         'span_count': len(spans), 'batch_size': 6}
            await self.evaluate_seed(model, pmcid, pid, task, 'spans_batches', seed, base_messages, auxiliary)

    async def rescue(self, path):
        async with self.jobs:
            data = json.loads(path.read_text())
            if 'retry_local' in data['arms']:
                return
            model = next(m for m in self.models if m['id'] == data['model'])
            pmcid, pid, task, mode = data['pmcid'], data['patient_id'], data['task'], data['extraction']
            seed = data['arms']['whole_retry']['candidate']
            dependencies = list(data['arms']['whole_retry']['extra'].get('calls', []))
            recovery = None
            if seed is None:
                messages = self.extraction_messages(pmcid, pid, task)
                # Preserve the extraction-mode aids in the recovery request.
                if mode != 'direct':
                    aids = [s for p in data['auxiliary'].get('annotation_paths', [])
                            for s in json.loads(Path(p).read_text())['verified_spans']]
                    hints = {'verified_literal_spans': aids}
                    if mode in {'spans_context', 'spans_batches'}:
                        hints['context_review'] = data['auxiliary'].get('disambiguation', {}).get('decisions', [])
                    messages = self.extraction_messages(pmcid, pid, task, hints)
                messages[-1]['content'] += ('\nEarlier attempts produced no complete parseable section. '
                    'This explicit recovery call has a larger output budget. Produce a complete compact JSON '
                    'section grounded in the source. Preserve normal values and all supported time points.')
                recovery = await self.call(model, task, messages,
                    {'phase': 'format_recovery', 'pmcid': pmcid, 'pid': pid, 'domain': task, 'mode': mode}, max_tokens=16384)
                seed = recovery.get('data'); dependencies.append(recovery)
            repaired, trace = await self.local_repair(model, pmcid, pid, task, seed, mode+'-after-retry')
            local_extra = {**trace, 'dependency_calls': dependencies, 'format_recovery': recovery,
                           'extension': 'whole_retry_then_local'}
            # Use the same production validation for the final assembled section.
            from openpatients2.validation import validate
            def output(candidate, extra):
                check = validate(task, candidate, self.articles[pmcid]['text']) if candidate is not None else None
                return {'candidate': candidate, 'delivered': check.data if check and check.valid else None,
                        'errors': check.errors if check else ['No parseable candidate'], 'extra': extra}
            data['arms']['retry_local'] = output(repaired, local_extra)
            reviewed, semantic = await self.semantic_review(model, pmcid, pid, task, repaired, mode+'-after-retry')
            semantic['dependency_calls'] = dependencies + trace['calls']
            semantic['extension'] = 'whole_retry_then_local_then_semantic'
            data['arms']['retry_semantic'] = output(reviewed, semantic)
            write_json(path, data)
            print(json.dumps({'extended': path.name, 'recovered_parse': recovery is not None,
                              'items': len((repaired or {}).get('items', []))}), flush=True)

    async def run_extension(self):
        started = time.time()
        self.restore_annotations()
        try:
            await asyncio.gather(*(self.recover_annotations(m, a) for m in self.models for a in DOMAINS))
            await asyncio.gather(*(self.batched_unit(m, u) for u in UNITS for m in self.models))
            await asyncio.gather(*(self.rescue(p) for p in sorted((ROOT/'outcomes').glob('*.json'))))
            write_json(ROOT/'extension-report.json', {'metrics': self.exp.metrics, 'budget': self.exp.budget.report(),
                                                    'wall_seconds': time.time()-started})
        finally:
            write_json(ROOT/'budget-status.json', self.exp.budget.report())
            await self.exp.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--freeze', action='store_true')
    parser.add_argument('--key-file')
    args = parser.parse_args()
    if args.freeze:
        p = ROOT/'extension-protocol.json'
        assert not p.exists()
        write_json(p, {'design': 'Prospective extension after early evidence of 8192-token truncation; not part of initial frozen factorial.',
                      'extraction_added': 'spans_batches', 'batch_size': 6, 'context_decisions': 'Reuse source-context stage, advisory not hard exclusions',
                      'postprocessors_added': ['retry_local', 'retry_semantic'],
                      'format_recovery': 'Only if whole_retry has no parseable result; one explicit fresh generation with 16384 token cap',
                      'spend': 'Same $10 new-study ledger and cap, not an additional budget',
                      'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    else:
        assert (ROOT/'report.json').exists(), 'Wait for primary run completion before extension'
        assert (ROOT/'extension-protocol.json').exists()
        if args.key_file: os.environ['OPENROUTER_API_KEY'] = Path(args.key_file).read_text().strip()
        asyncio.run(Extension().run_extension())
