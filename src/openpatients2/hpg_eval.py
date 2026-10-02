"""Local, text-only replay of the frozen medical-fidelity pilot. No paid API calls."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib
import json
import sys
import time
import types
from urllib.parse import urlparse
from collections import Counter, defaultdict
from pathlib import Path

import httpx
import jsonschema
import numpy as np

from .article_tasks import check_article_task, task_messages
from .client import APIClient
from .k2_output import recover_answer
from .config import APIConfig
from .data import read_jsonl, write_json
from .fidelity import evaluate, summarize, validate_reference, review_sample
from .figure_attribution import figure_messages, validate_figure_review, bind_figure_review, patient_media, FigureReview
from .provenance import json_digest


def stats(values):
    values = list(values)
    return {'n': len(values), 'mean': float(np.mean(values)) if values else None,
            'median': float(np.median(values)) if values else None,
            'p95': float(np.percentile(values, 95)) if values else None,
            'p95_method': 'linear interpolation'}


def usage_stats(rows, key):
    rows = list(rows); known = [r[key] for r in rows if r[key] is not None]
    result = stats(known if len(known) == len(rows) else [])
    result.update({'n': len(rows), 'n_with_usage': len(known), 'unknown_articles': len(rows) - len(known)})
    if len(known) != len(rows): result['reported_only_stats'] = stats(known)
    return result


def sampling_body(model, arm):
    """Use the checkpoint's actual template control, not a provider's alias."""
    profile = model.get('reasoning_profile', {})
    if profile.get('template_kwarg') == 'reasoning_strength':
        body = {'chat_template_kwargs': {'reasoning_strength': arm['reasoning_effort']},
                'skip_special_tokens': False}
    else:
        body = {'chat_template_kwargs': {'reasoning_effort': arm['reasoning_effort'], 'tool_call_format': 'xml'}}
    if 'top_k' in arm: body['top_k'] = arm['top_k']
    return body


def fixtures(path: Path):
    """Reject changed inputs or gold labels before requests leave the client."""
    manifest = json.loads((path / 'manifest.json').read_text())
    for name, sha in manifest['files'].items():
        p = path / name
        if not p.resolve().is_relative_to(path.resolve()) or hashlib.sha256(p.read_bytes()).hexdigest() != sha:
            raise ValueError('Frozen benchmark fixture integrity failure: ' + name)
    articles = {a['article_id']: a for a in read_jsonl(path / 'articles.jsonl')}
    reference = json.loads((path / 'reference.json').read_text())
    validate_reference(reference, articles)
    packets = {r['record_id']: r for r in read_jsonl(path / 'packets.jsonl')}
    requests = list(read_jsonl(path / 'requests.jsonl'))
    if len(requests) != manifest['requests_per_arm'] or len(packets) != manifest['patient_cases']:
        raise ValueError('Incomplete frozen evaluation inputs')
    for r in requests:
        if json_digest(r['messages']) != r['messages_sha256']:
            raise ValueError('Changed frozen request messages')
        if any(not isinstance(m['content'], str) for m in r['messages']):
            raise ValueError('Pixel inputs are prohibited in this text-only benchmark')
    return manifest, articles, packets, requests, reference


def legacy_validator(path):
    # A separate module namespace keeps the historical clinical gates reproducible
    # without replacing the application's production validator or parser.
    name = '_op2_hpg_legacy_' + hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]
    if name not in sys.modules:
        package = types.ModuleType(name)
        package.__path__ = [str(path / 'legacy')]
        sys.modules[name] = package
    return importlib.import_module(name + '.validation'), importlib.import_module(name + '.output_parser')


def check_candidate(request, value, articles, packets, legacy):
    task = request['task']; identity = request['identity']
    jsonschema.validate(value, request['schema'])
    if task in {'summary', 'timeline', 'roster'}:
        return check_article_task(task, value, articles[identity['article_id']])
    if task == 'figure_attribution':
        article = articles[identity['article_id']]
        return validate_figure_review(value, article, request['roster'], identity['figure_id'])
    record = packets[identity['article_id'] + ':' + identity['patient_id']]
    checked = legacy.validate(task, value, record['text'])
    if not checked.valid:
        raise ValueError('; '.join(checked.errors))
    if task == 'case_context' and (checked.data['multiple_index_patients'] is not False or checked.data['case_kind'] == 'multi_patient'):
        raise ValueError('This packet targets ONE specified index patient. case_kind and multiple_index_patients describe that target, not the number of patients in the source article. Use clinical_case (or animal) and multiple_index_patients=false.')
    return checked.data


class Replay:
    def __init__(self, path, output, model, arm, endpoints, concurrency, timeout, context, http=None):
        if not endpoints or any(urlparse(e).hostname not in {'127.0.0.1', 'localhost', '::1'} for e in endpoints):
            raise ValueError('This local benchmark only permits loopback inference endpoints')
        self.path = Path(path); self.output = Path(output); self.output.mkdir(parents=True, exist_ok=True)
        self.model = model; self.arm = arm; self.context = context
        self.manifest, self.articles, self.packets, self.requests, self.reference = fixtures(self.path)
        self.legacy, self.parser = legacy_validator(self.path)
        self.clients = [APIClient(APIConfig(endpoints=[e], model='clinical-extractor', model_id=model['id'],
            revision=model['revision'], api_key_env='OP2_LOCAL_UNUSED_API_KEY', response_format='prompt_json',
            temperature=arm['temperature'], top_p=arm['top_p'], http_retries=0, timeout_seconds=timeout,
            seed=arm.get('seed'), extra_body=sampling_body(model, arm)),
            http=http, schema_overrides={r['task']: r['schema'] for r in self.requests}) for e in endpoints]
        self.slots = [asyncio.Semaphore(concurrency) for _ in endpoints]
        # Round-robin by patient, then keep all that patient's tasks on one replica
        # for prefix reuse. This assignment is deterministic and approximately balanced.
        self.affinity = {rid: i % len(endpoints) for i, rid in enumerate(sorted(self.packets))}
        self.signature = json_digest({'fixture': self.manifest, 'model': model, 'arm': arm,
                                     'context': context, 'endpoints': len(endpoints), 'concurrency': concurrency})

    async def close(self):
        await asyncio.gather(*(c.close() for c in self.clients))

    async def token_count(self, client, messages):
        body = {'model': 'clinical-extractor', 'messages': messages, 'add_generation_prompt': True,
                'chat_template_kwargs': client.config.extra_body['chat_template_kwargs']}
        url = client.config.endpoints[0].removesuffix('/v1') + '/tokenize'
        response = await client.http.post(url, json=body, timeout=60)
        response.raise_for_status()
        count = response.json().get('count')
        if not isinstance(count, int) or count < 1:
            raise ValueError('Serving tokenizer did not return an exact prompt count')
        return count

    async def call(self, request, index=None):
        identity = request['identity']; rid = identity['article_id'] + ':' + identity.get('patient_id', '')
        index = self.affinity.get(rid, 0) if index is None else index
        key = json_digest({'run': self.signature, 'request': request})
        path = self.output / 'tasks' / (key + '.json')
        if path.exists():
            saved = json.loads(path.read_text())
            if saved['signature'] != key:
                raise ValueError('Cached result signature mismatch')
            if saved['data'] is not None:
                check_candidate(request, saved['data'], self.articles, self.packets, self.legacy)
            return {**saved, 'resumed': True}
        attempts = []; data = None; errors = []
        async with self.slots[index]:
            client = self.clients[index]
            for attempt in range(2):
                messages = copy.deepcopy(request['messages'])
                if attempt:
                    messages.append({'role': 'user', 'content': 'The previous extraction failed validation. Regenerate the COMPLETE JSON object; do not drop documented information to make validation pass. Errors: ' + '; '.join(errors)[:3500]})
                cap = self.arm['retry_tokens'] if attempt else self.arm['max_tokens']
                count = await self.token_count(client, messages)
                if count + cap > self.context:
                    # A guard failure is retained, never "fixed" by truncating source.
                    errors = [f'context_guard: {count} prompt + {cap} output > {self.context}']
                    attempts.append({'attempt': attempt, 'errors': errors, 'response': None,
                                     'tokenized_prompt_tokens': count, 'max_tokens': cap, 'metrics': {}})
                    break
                response = await client.complete_once(client.config.endpoints[0], request['task'], messages, cap)
                endpoint_response = response.response()
                response, boundary_recovery = recover_answer(response, self.model['id'])
                candidate = None; errors = []
                if response.error:
                    errors = [response.error]
                elif response.finish_reason not in {'stop', 'eos'}:
                    errors = ['incomplete_finish:' + str(response.finish_reason)]
                else:
                    try:
                        candidate = self.parser.parse_output(response.content).value
                        data = check_candidate(request, candidate, self.articles, self.packets, self.legacy)
                    except (ValueError, TypeError, KeyError, jsonschema.ValidationError) as exc:
                        errors = [str(exc)[:4000]]
                attempts.append({'attempt': attempt, 'response': response.response(), 'metrics': response.metrics(),
                                 'endpoint_response': endpoint_response, 'answer_boundary_recovery': boundary_recovery,
                                 'candidate': candidate, 'errors': errors, 'max_tokens': cap,
                                 'tokenized_prompt_tokens': count, 'messages_sha256': json_digest(messages)})
                # Persist even if the job is killed before its other tasks finish.
                write_json(self.output / 'attempts' / f'{key}-{attempt}.json', attempts[-1])
                if not errors:
                    break
                if response.error and not client.transient(response):
                    break
        last = attempts[-1] if attempts else {}
        result = {'signature': key, 'task': request['task'], 'identity': identity, 'replica': index,
                  'status': 'valid' if not errors and data is not None else 'failed',
                  'data': data if not errors else None, 'raw': last.get('candidate'),
                  'errors': errors, 'attempts': attempts, 'resumed': False}
        write_json(path, result)
        print(json.dumps({'task': request['task'], **identity, 'status': result['status']}), flush=True)
        return result

    async def run(self):
        started = time.monotonic()
        results = await asyncio.gather(*(self.call(r) for r in self.requests))
        wall = time.monotonic() - started
        self.primary_results = results; self.primary_wall = wall
        predictions = {rid: {'raw': {}, 'delivered': {}, 'patient': None} for rid in self.packets}
        grouped = defaultdict(dict)
        for result in results:
            ident = result['identity']; rid = ident['article_id'] + ':' + ident['patient_id']
            task = result['task']; grouped[rid][task] = result
            predictions[rid]['raw'][task] = result['raw']
            predictions[rid]['delivered'][task] = result['data']
        for rid, packet in self.packets.items():
            source = copy.deepcopy(packet)
            vision_capable = self.model.get('capabilities', {}).get('vision', False)
            vision_status = 'not_evaluated_text_only' if vision_capable else 'unsupported_by_model'
            source.setdefault('multimedia', {}).update({'pixels_inspected': False, 'vision_status': vision_status})
            tasks = grouped[rid]
            clinical = {t: r['data'] for t, r in tasks.items() if t not in {'summary', 'timeline'}}
            patient = {'schema_version': '2.1.0', 'source': source,
                'model': {'model_id': self.model['id'], 'revision': self.model['revision'], 'capabilities': {'vision': vision_capable}},
                'vision': {'status': vision_status, 'pixels_inspected': False,
                           'caption_text_available': any(f.get('caption') for f in source['article_source']['figures']),
                           'pixel_interpretation_evaluated': False},
                'sections': clinical,
                'quality': {t: {'status': r['status'], 'errors': r['errors']} for t, r in tasks.items()},
                'generations': {t: r['attempts'][-1].get('response') for t, r in tasks.items()},
                'expected_tasks': list(clinical), 'complete_for_scope': all(v is not None for v in clinical.values()),
                'companions': {t: tasks[t] for t in ('summary', 'timeline')},
                'clinical_review_status': 'unreviewed', 'benchmark_signature': self.signature}
            # Pixel-observation/interpretation columns are deliberately absent.
            predictions[rid]['patient'] = patient
            write_json(self.output / (rid.replace(':', '-') + '.json'), patient)
        with (self.output / 'patients.jsonl').open('w') as f:
            for rid in sorted(predictions):
                f.write(json.dumps(predictions[rid]['patient'], ensure_ascii=False) + '\n')
        scores = evaluate(self.reference, predictions)
        report = {'signature': self.signature, 'model': self.model, 'generation_settings': self.arm,
                  'fixtures': self.manifest, 'wall_seconds': wall, 'resumed_tasks': sum(r['resumed'] for r in results),
                  'tasks': len(results), 'valid_tasks': sum(r['status'] == 'valid' for r in results),
                  'first_attempt_valid_tasks': sum(bool(r['attempts'] and not r['attempts'][0]['errors']) for r in results),
                  'answer_boundary_recovered_attempts': sum(bool(a.get('answer_boundary_recovery')) for r in results for a in r['attempts']),
                  'by_task': {task: {'total': sum(r['task'] == task for r in results),
                                    'valid': sum(r['task'] == task and r['status'] == 'valid' for r in results)}
                              for task in sorted({r['task'] for r in results})},
                  'scores': {mode: summarize(scores, mode) for mode in ('raw', 'delivered')},
                  'by_category': {cat: {mode: summarize([s for s in scores if s['category'] == cat], mode)
                                        for mode in ('raw', 'delivered')} for cat in sorted({s['category'] for s in scores})},
                  'tokens': self.token_report(results, wall),
                  'limitations': ['Partial checklist, not full medical precision/recall; physician review pending.',
                      'Fixed reference patient rosters; discovery is evaluated separately.',
                      'No pixels, visual interpretation, image downloads, or visual accuracy scores.',
                      'Same frozen first prompts and one complete-regeneration retry; reasoning implementations and tokenizers differ.',
                      'Historical hosted throughput is not comparable to this B200 deployment.',
                      'Throughput excludes server startup, warmup and secondary tasks; resumed runs are not fresh throughput measurements.']}
        write_json(self.output / 'report.json', report)
        write_json(self.output / 'check-details.json', scores)
        audit, aliases, availability = review_sample(self.reference, {self.model['id']: predictions})
        for filename, value in [('claim-review-pending.json', audit), ('review-aliases.json', aliases), ('review-availability.json', availability)]:
            dest = self.output / filename
            if not dest.exists(): write_json(dest, value)
        return report

    def token_report(self, results, wall):
        rows = {aid: {'article_id': aid, 'patient_cases': 0, 'requests_sent': 0,
                      'input_tokens': 0, 'output_tokens': 0, 'reasoning_tokens': 0,
                      'unknown_input_usage': 0, 'unknown_output_usage': 0, 'unknown_reasoning_usage': 0}
                for aid in self.articles}
        for rid in self.packets: rows[rid.rsplit(':', 1)[0]]['patient_cases'] += 1
        ttft = []; latencies = []
        for r in results:
            row = rows[r['identity']['article_id']]
            for attempt in r['attempts']:
                if not attempt.get('response'): continue
                row['requests_sent'] += 1
                metric = attempt['metrics']
                for dest, key in [('input', 'prompt_tokens'), ('output', 'completion_tokens'), ('reasoning', 'reasoning_tokens')]:
                    value = metric.get(key)
                    if value is None: row['unknown_' + dest + '_usage'] += 1
                    else: row[dest + '_tokens'] += value
                if metric.get('ttft_seconds') is not None: ttft.append(metric['ttft_seconds'])
                latencies.append(metric['latency_seconds'])
        per_article = list(rows.values())
        # Unknown usage remains unknown instead of silently appearing as zero.
        for row in per_article:
            for name in ('input', 'output', 'reasoning'):
                if row['unknown_' + name + '_usage']: row[name + '_tokens'] = None
        output_complete = all(row['output_tokens'] is not None for row in per_article)
        total = sum(row['output_tokens'] for row in per_article) if output_complete else None
        input_total = sum(row['input_tokens'] for row in per_article) if all(row['input_tokens'] is not None for row in per_article) else None
        fresh = not any(r['resumed'] for r in results)
        return {'per_article': per_article,
                'gpu_count': self.model['replicas'] * self.model['tensor_parallel'],
                'total_input_tokens': input_total, 'total_output_tokens': total,
                'measurement_wall_seconds': wall, 'fresh_measurement': fresh,
                'per_article_stats_all_nine': {key: usage_stats(per_article, key)
                    for key in ('input_tokens', 'output_tokens', 'reasoning_tokens')},
                'per_article_stats_with_patients': {key: usage_stats((r for r in per_article if r['patient_cases']), key)
                    for key in ('input_tokens', 'output_tokens', 'reasoning_tokens')},
                'ttft_seconds': stats(ttft), 'request_latency_seconds': stats(latencies),
                'aggregate_input_tokens_per_second': input_total / wall if input_total is not None and fresh and wall else None,
                'aggregate_output_tokens_per_second': total / wall if total is not None and fresh and wall else None,
                'aggregate_output_tokens_per_gpu_second': total / wall / (self.model['replicas'] * self.model['tensor_parallel'])
                    if total is not None and fresh and wall else None,
                'notice': 'Includes retries and repeated per-patient article prompts. Completion usage includes reasoning where the server counts it; absent reasoning breakdown stays unknown. Zero extraction tokens for the two negative controls; secondary tasks are separate.'}

    async def secondary(self):
        """Text-based discovery and figure ownership, separate from historical scores."""
        from .article_tasks import ARTICLE_TASKS
        started = time.monotonic()
        jobs = []; rosters = {}
        for i, (aid, article) in enumerate(sorted(self.articles.items())):
            roster = json.loads((self.path / 'rosters' / (aid + '.json')).read_text())['roster']
            rosters[aid] = roster
            request = {'task': 'roster', 'identity': {'article_id': aid},
                       'messages': task_messages('roster', article), 'schema': ARTICLE_TASKS['roster'].model_json_schema()}
            jobs.append(self.call(request, i % len(self.clients)))
            for figure in article['figures']:
                request = {'task': 'figure_attribution', 'identity': {'article_id': aid, 'figure_id': figure['figure_key']},
                           'roster': roster, 'messages': figure_messages(article, roster, figure['figure_key']),
                           'schema': FigureReview.model_json_schema()}
                jobs.append(self.call(request, i % len(self.clients)))
        # Add secondary contracts before APIClient.body tries to resolve a clinical schema.
        for client in self.clients:
            client.schema_overrides.update({'roster': ARTICLE_TASKS['roster'].model_json_schema(),
                                            'figure_attribution': FigureReview.model_json_schema()})
        results = await asyncio.gather(*jobs)
        wall = time.monotonic() - started
        reviews = defaultdict(list); discovered = []
        for r in results:
            aid = r['identity']['article_id']; article = self.articles[aid]
            if r['task'] == 'roster':
                expected = rosters[aid]
                discovered.append({'article_id': aid, 'status': r['status'],
                    'expected_disposition': expected['disposition'], 'expected_patients': len(expected['patients']),
                    'predicted_disposition': r['data']['disposition'] if r['data'] else None,
                    'predicted_patients': len(r['data']['patients']) if r['data'] else None,
                    'identity_mapping_review': 'pending; equal counts do not establish correct patients'})
                write_json(self.output / 'secondary' / (aid + '-roster.json'), r)
            elif r['data']:
                reviews[aid].append(bind_figure_review(r['data'], article, rosters[aid], method='text_only_caption_and_article'))
            else:
                reviews[aid].append({'article_id': aid, 'xml_sha256': article['xml_sha256'],
                    'roster_digest': json_digest(rosters[aid]), 'data': None, 'figure_id': r['identity']['figure_id'],
                    'errors': r['errors'], 'semantic_review': 'unreviewed'})
        for aid, article in self.articles.items():
            write_json(self.output / 'secondary' / (aid + '-figures.json'), reviews[aid])
        bundles = []
        for rid in sorted(self.packets):
            aid, pid = rid.rsplit(':', 1)
            path = self.output / (rid.replace(':', '-') + '.json')
            patient = json.loads(path.read_text())
            assignments, media = patient_media(self.articles[aid], rosters[aid], pid,
                                               [r for r in reviews[aid] if r['data'] is not None])
            media.update({'vision_status': patient['vision']['status'], 'pixels_inspected': False})
            patient['source']['figure_assignments'] = assignments
            patient['source']['multimedia'] = media
            patient['figure_attribution'] = {'method': 'text_only_caption_and_article', 'semantic_review': 'unreviewed'}
            write_json(path, patient); bundles.append(patient)
        with (self.output / 'patients.jsonl').open('w') as f:
            for p in bundles: f.write(json.dumps(p, ensure_ascii=False) + '\n')
        report = {'roster_discovery': discovered, 'task_counts': dict(Counter(r['task'] for r in results)),
                  'valid_tasks': sum(r['status'] == 'valid' for r in results),
                  'wall_seconds': wall, 'tokens': self.token_report(results, wall),
                  'figure_semantic_accuracy': None, 'pixel_accuracy': None,
                  'review_status': 'pending source review; structural validation is not attribution accuracy',
                  'results': results}
        write_json(self.output / 'secondary' / 'report.json', report)
        main_report = self.output / 'report.json'
        if main_report.exists() and hasattr(self, 'primary_results'):
            primary = json.loads(main_report.read_text())
            primary['secondary_summary'] = {k: report[k] for k in ('task_counts', 'valid_tasks', 'wall_seconds', 'tokens')}
            primary['workflow_tokens_including_secondary'] = self.token_report(self.primary_results + results, self.primary_wall + wall)
            write_json(main_report, primary)
        return report
