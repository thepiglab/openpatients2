"""Opt-in bounded local extraction; source validity is never clinical accuracy.

This runner consumes an already downloaded sample. It neither acquires sources
nor starts servers. Direct and targeted arms use independent output directories
and identical source-based first prompts; temporal fact registries reflect this
arm's accepted facts. Every completion has an exact serving-tokenizer
guard; source text is never truncated to fit context.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import copy
import gzip
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Literal
from urllib.parse import urlparse

import httpx
from pydantic import Field
import yaml

from .article_tasks import ARTICLE_TASKS, SYSTEM, check_article_task, patient_packet, task_messages
from .articles import recheck_license
from .client import APIClient
from .config import APIConfig, ConfigModel
from .data import write_json
from .evidence_recovery import packet_segments, recover_citations, resolve_timeline_spans
from .extraction_contracts import FactEnvelope, FieldSupport, SourceSpan, field_support_report
from .figure_attribution import (FigureReview, bind_figure_review, figure_messages,
                                 patient_media, validate_figure_review)
from .figure_visuals import FigureVisuals, JointFigureAnalysis, panel_columns, validate_visuals, visual_messages, partial_visuals
from .longitudinal import PatientTimeline, audit_timeline, partial_timeline, canonical_timeline_ids, relative_order
from .measurements import observation_measurements
from .output_parser import parse_output
from .prompts import messages_for
from .provenance import json_digest
from .patient_context import discovery_messages, check_patient_roster, isolate_cited_cases, frozen_rosters as read_frozen_rosters, patient_view
from .pilot_review import (OrderingReview, ClaimAudit, ordering_schema, ordering_messages,
    check_ordering, claim_messages, check_claim_audit, panel_aligned_attribution,
    ClinicalInventory, inventory_messages, check_inventory,
    CoverageAudit, coverage_messages, check_coverage)
from .schemas import TASK_MODELS
from .targeted_repair import ItemRepair, erased, repair_snapshot, changed_accepted_atoms, protected_value_changes, canonical_clinical_task
from .prompt_payloads import compact_facts, compact_graph, timeline_retention
from .clinical_normalization import normalize_clinical
from .validation import iter_objects, validate


class PilotConfig(ConfigModel):
    model_id: Literal['RedHatAI/Muse-Glimmer-30B-FP8-block'] = 'RedHatAI/Muse-Glimmer-30B-FP8-block'
    revision: Literal['1deb4641ff84f9a728dd11b27cac1f6a02a9ed14'] = '1deb4641ff84f9a728dd11b27cac1f6a02a9ed14'
    served_model: str = 'clinical-extractor'
    gpus_per_endpoint: int = Field(default=1,ge=1,le=2)
    tokenizer_contract: Literal['vllm/0.30.0'] = 'vllm/0.30.0'
    reasoning_strength: Literal['low', 'medium', 'high', 'xhigh'] = 'medium'
    temperature: Literal[1.0] = 1.0
    top_p: Literal[0.95] = 0.95
    top_k: Literal[64] = 64
    seed: int = Field(default=42, ge=0, le=2**32-1)
    sample_seed: int = 42
    max_articles: int = Field(default=48, ge=1, le=48)
    max_patients_per_article: int = Field(default=8, ge=1, le=48)
    max_patients: int = Field(default=96, ge=1, le=384)
    max_figures_per_article: int = Field(default=24, ge=0, le=48)
    max_figures: int = Field(default=12, ge=0, le=12, description='Pixel figures across this entire arm')
    max_calls: int = Field(default=4096, ge=1, le=8192)
    max_total_tokens: int = Field(default=32_000_000, ge=128, le=128_000_000)
    max_output_tokens: int = Field(default=16384, ge=128, le=32768)
    max_retry_tokens: int = Field(default=16384, ge=128, le=32768)
    require_full_output_budget: bool = True
    min_output_tokens: int = Field(default=128, ge=1, le=8192)
    max_repair_rounds: int = Field(default=1, ge=0, le=2)
    transport_retries: int = Field(default=1, ge=0, le=1)
    concurrency_per_endpoint: int = Field(default=64, ge=1, le=64)
    timeout_seconds: float = Field(default=240, gt=0, le=1800)
    context_safety_tokens: int = Field(default=64, ge=0, le=1024)
    max_source_bytes: int = Field(default=64_000_000, ge=1, le=256_000_000)
    max_article_characters: int = Field(default=500_000, ge=1, le=2_000_000)
    max_response_characters: int = Field(default=250_000, ge=1, le=2_000_000)
    max_image_bytes: int = Field(default=8_000_000, ge=1, le=8_000_000)
    article_ids: list[str] | None = None
    robust_contracts: bool = True
    refinement_policy: Literal['legacy', 'source_aware'] = 'source_aware'
    image_manifest: str | None = None
    isolate_secondary_cases: bool = False
    review_ordering: bool = False
    audit_claims: bool = False
    inventory_clinical_features: bool = False
    coverage_repair: bool = False
    focused_pixels: bool = False
    pixel_strategy: Literal['separate', 'staged', 'joint'] = 'separate'
    prompt_overrides: dict[str, str] = Field(default_factory=dict)
    prompt_mode: Literal['supplement', 'rewrite'] = 'supplement'
    timeline_completion: bool = False
    summary_completion: bool = False
    scatter_calls: bool = False
    experimental_pipeline: Literal['original', 'evidence_ledger'] = 'original'
    ledger_chunk_characters: int = Field(default=12000, ge=1024, le=32000)
    ledger_audit: bool = False
    ledger_delta_timeline: bool = True


def load_pilot_config(config: str | Path | dict | PilotConfig) -> PilotConfig:
    if isinstance(config, PilotConfig):
        return config
    if isinstance(config, (str, Path)):
        config = yaml.safe_load(Path(config).read_text()) or {}
    return PilotConfig.model_validate(config)


def source_gate(article: dict, config: PilotConfig) -> list[str]:
    errors = []
    if article.get('status') != 'eligible':
        errors.append('source_status_not_eligible')
    try:
        rights_allowed = recheck_license(article.get('license', {})).get('allowed')
    except (ValueError, TypeError, KeyError, AttributeError):
        rights_allowed = False
    if not rights_allowed:
        errors.append('source_rights_rejected_or_review_required')
    if article.get('has_body_text') is not True or not article.get('segments'):
        errors.append('source_body_incomplete')
    if not isinstance(article.get('text'), str):
        errors.append('source_text_missing')
    elif len(article['text']) > config.max_article_characters:
        errors.append('article_size_limit_no_truncation')
    elif hashlib.sha256(article['text'].encode()).hexdigest() != article.get('text_sha256'):
        errors.append('article_text_hash_mismatch')
    segments = article.get('segments') or []
    if len({s.get('segment_id') for s in segments}) != len(segments):
        errors.append('duplicate_source_segment_ids')
    if any(not isinstance(s.get('text'), str) or not s.get('segment_id') for s in segments):
        errors.append('malformed_source_segment')
    elif isinstance(article.get('text'), str):
        rendered = '\n\n'.join(f'[{s["segment_id"]}] {s.get("heading") or "Article"}\n{s["text"]}' for s in segments)
        if rendered != article['text']:
            errors.append('segment_text_does_not_match_immutable_article')
    if not isinstance(article.get('xml_sha256'), str) or not re.fullmatch('[a-f0-9]{64}', article['xml_sha256']):
        errors.append('missing_canonical_xml_hash')
    if not article.get('article_id') or not article.get('pmcid'):
        errors.append('missing_article_identity')
    if any(key not in article for key in ('version', 'title', 'authors', 'figures', 'supplements')):
        errors.append('missing_source_packet_fields')
    return errors


def sources_for(article: dict) -> tuple[str, dict]:
    sid = article['article_id'] + ':jats'
    return sid, {sid: {s['segment_id']: s['text'] for s in article['segments']}}


def exact_spans(article: dict, citation: dict) -> list[dict]:
    """Retain every literal location; never guess field entailment from a quote."""
    source_id, sources = sources_for(article)
    segment_id = citation.get('segment_id', citation.get('source_section'))
    text = sources[source_id].get(segment_id)
    quote = citation.get('quote')
    if text is None or not isinstance(quote, str) or not quote:
        return []
    result, offset = [], 0
    while (start := text.find(quote, offset)) >= 0:
        result.append(SourceSpan(source_id=source_id, segment_id=segment_id,
            segment_sha256=hashlib.sha256(text.encode()).hexdigest(),
            start=start, end=start + len(quote), quote=quote).model_dump())
        offset = start + 1
    return result


def coverage_inventory(article: dict, record_ids: list[str]) -> dict:
    # Whole table blocks are explicit here; do not invent a cell inventory from
    # flattened JATS text or claim that a model's disposition proves recall.
    source_id, _ = sources_for(article)
    units = []
    for segment in article['segments']:
        text = segment['text']
        units.append({'unit_id': source_id + ':' + segment['segment_id'],
            'kind': segment.get('kind', 'source_block'), 'source_id': source_id,
            'segment_id': segment['segment_id'], 'segment_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'text': text, 'record_ids': record_ids, 'disposition': 'unresolved',
            'fact_ids': [], 'semantic_review': 'unreviewed'})
    for supplement in article.get('supplements', []):
        units.append({'unit_id': source_id + ':supplement:' + supplement['supplement_id'],
            'kind': 'supplement_manifest', 'manifest': supplement,
            'disposition': 'not_inspected', 'content_downloaded': False})
    return {'inventory_version': 'pilot-source-blocks/1', 'units': units,
        'inventory_scope': 'supplied canonical text blocks and supplement manifests; not individual table cells or unseen assets',
        'semantic_coverage_verified': False, 'bookkeeping_complete': False,
        'reason': 'Source inventory is fixed by code; extraction/coverage dispositions require source review.'}


def review_rows(article: dict, record_id: str, task: str, data: dict | None) -> list[dict]:
    result = []
    if data is None:
        return result
    for path, value in iter_objects(data):
        evidence = value.get('evidence')
        if not isinstance(evidence, list) or not evidence:
            continue
        spans = [span for citation in evidence if isinstance(citation, dict)
                 for span in exact_spans(article, citation)]
        result.append({'review_id': json_digest({'record_id': record_id, 'task': task, 'path': path, 'value': value}),
            'record_id': record_id, 'task': task, 'pointer': path or '/', 'candidate': value,
            'source_xml_sha256': article['xml_sha256'], 'source_text_sha256': article['text_sha256'],
            'evidence_spans': spans, 'literal_support_locations': len(spans),
            'field_support': [], 'field_entailment_verified': False,
            'patient_attribution_verified': False, 'clinical_accuracy_verified': False,
            'review_status': 'pending', 'reviewer': None, 'adjudication': None})
    return result


def literal_field_support(value: dict, evidence_spans: list[dict]) -> list[FieldSupport]:
    """Bind literal field values only inside their own fact's citation spans.

    This is lexical support, not proof of patient scope or field entailment.
    Semantic enums, subject roles, assertion and normalized categories are never
    inferred from a quote's mere presence. Missing leaf support stays explicit.
    """
    eligible = {'name', 'text_value', 'value_text', 'dose_text', 'dose_value', 'dose_unit',
        'numeric_value', 'unit', 'reference_range_text', 'specimen', 'route', 'frequency',
        'duration', 'body_site', 'time', 'text', 'date_iso', 'chief_complaint',
        'diagnostic_basis', 'description', 'index_subject_description'}
    supports = []
    def visit(obj, pointer='', field=''):
        if isinstance(obj, dict):
            for key, child in obj.items():
                if key != 'evidence':
                    visit(child, pointer + '/' + key.replace('~', '~0').replace('/', '~1'), key)
        elif isinstance(obj, list):
            for index, child in enumerate(obj):
                visit(child, pointer + '/' + str(index), field)
        elif field in eligible and type(obj) in {str, int, float} and obj not in ('', 'unknown'):
            token = format(obj, 'g') if type(obj) in {int, float} else obj
            pattern = re.escape(token)
            if type(obj) in {int, float}:
                pattern = r'(?<![\w.])' + pattern + r'(?![\w.])'
            matches = []
            for original in evidence_spans:
                for match in re.finditer(pattern, original['quote']):
                    start = original['start'] + match.start()
                    matches.append(SourceSpan(**{**original, 'start': start,
                        'end': start + len(token), 'quote': token}))
            if matches:
                supports.append(FieldSupport(pointer=pointer, evidence=matches,
                    role='time' if pointer.startswith('/time/') else 'value'))
    visit(value)
    return supports


def timeline_wire_schema() -> dict:
    """Ask for literal quotes; deterministic code supplies character positions."""
    schema = copy.deepcopy(PatientTimeline.model_json_schema())
    span = schema['$defs']['SourceSpan']
    computed = {'source_id', 'segment_sha256', 'start', 'end'}
    span['properties'] = {k: v for k, v in span['properties'].items() if k not in computed}
    span['required'] = [k for k in span['required'] if k not in computed]
    return schema


def timeline_messages(article: dict, patient: dict, record_id: str, facts: dict[str, str], fact_rows=None, *, refined=False) -> list[dict]:
    source_id, _ = sources_for(article)
    source = {'source_id': source_id, 'record_id': record_id, 'target_patient': patient,
        'segments': [{k: s[k] for k in ('segment_id', 'kind', 'heading', 'text') if k in s}
                     for s in article['segments']], 'known_fact_ids': list(facts)}
    source['fact_registry']=compact_facts([row for row in fact_rows or [] if row['review_id'] in facts])
    instruction = ('Return patient-timeline/2 JSON. Every evidence and attribution span supplies only '
        'a known segment_id and an exact unique quote. Code derives source_id, segment_sha256 and Unicode '
        'start/end positions; do not calculate or invent those fields. '
        'Describe only this patient. Include unknown-time events, failed treatments, follow-up and plans, '
        'keeping occurred, planned, conditional and unknown distinct. Never invent encounters or dates. '
        'Times must preserve literal precision. Edges must cite evidence for BOTH events and their relation. '
        'Do not derive chronology from paragraph order. Keep month/year offsets in their own units. '
        'Use fact_ids only from known_fact_ids for this record; empty is allowed. The registry associates IDs '
        'with unreviewed extracted values: verify every event and link against primary source, not registry alone. '
        'Preserve uncertainty and contradictions. Distinct administrations, assessments or encounters '
        'with different stated years/ages/days must be separate events, even when mentioned in one sentence. '
        'Do not merge successive doses or treatment changes into one event. A planned or declined '
        'treatment must never become an occurred treatment.')
    if refined:
        instruction += (' The primary goal is relative clinical sequence, not dates: distinguish past history, '
            'presentation, investigations, treatment, adverse events or failed response, treatment changes, '
            'discharge and follow-up when documented. Use before/after edges with offset=null when the source '
            'establishes order without a duration; events may have times=[]. A history reported at admission '
            'can describe an earlier event: order the clinical occurrence, not when it was narrated. '
            'Do not mechanically order events by kind or put every test before every treatment. Preserve '
            'simultaneity only when stated, and leave incomparable events unordered. Link events to the '
            'corresponding extracted fact_ids whenever the source supports that association.')
    return [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content':
        'SOURCE_JSON:\n' + json.dumps(source, ensure_ascii=False) + '\nTASK:\n' + instruction +
        '\nSCHEMA:\n' + json.dumps(timeline_wire_schema())}]


def safe_messages(messages: list[dict]) -> list[dict]:
    value = copy.deepcopy(messages)
    for message in value:
        if isinstance(message['content'], list):
            for block in message['content']:
                if block.get('type') == 'image_url':
                    url = block['image_url']['url']
                    block['image_url']['url'] = '[local pixel input; URL SHA256=' + hashlib.sha256(url.encode()).hexdigest() + ']'
    return value


def percentile(values: list[int | float]) -> dict:
    values = sorted(values)
    def point(p):
        at = (len(values) - 1) * p
        lo = int(at); hi = min(lo + 1, len(values) - 1)
        return values[lo] + (values[hi] - values[lo]) * (at - lo)
    return {'n': len(values), 'mean': sum(values) / len(values) if values else None,
        'median': point(.5) if values else None, 'p95': point(.95) if values else None,
        'p95_method': 'linear interpolation'}


class PilotRunner:
    def __init__(self, config, output, endpoints, context, arm_name, *, http=None, input_scope='whole_article', seed=None):
        self.config = load_pilot_config(config).model_copy()
        if seed is not None:
            self.config = PilotConfig.model_validate({**self.config.model_dump(), 'seed': seed})
        if input_scope not in {'whole_article', 'patient_sections'}:
            raise ValueError('Unknown clinical input scope')
        self.input_scope = input_scope
        self.output = Path(output)
        endpoints = [e.strip() for e in endpoints.split(',')] if isinstance(endpoints, str) else list(endpoints)
        if not endpoints or any(urlparse(e).hostname not in {'localhost', '127.0.0.1', '::1'}
                                or not e.rstrip('/').endswith('/v1') for e in endpoints):
            raise ValueError('Pilot inference permits only loopback HTTP(S) /v1 endpoints')
        if arm_name not in {'direct', 'targeted'} or context < 128:
            raise ValueError('Use direct/targeted arm and a positive serving context >=128')
        if self.output.exists() and any(self.output.iterdir()):
            raise ValueError('Use an empty independent output directory for each pilot arm; existing results are immutable')
        self.output.mkdir(parents=True, exist_ok=True)
        self.arm = arm_name; self.context = context
        self.calls = 0; self.reserved_tokens = 0; self.accounted_tokens = 0; self.attempt_number = 0
        self.results = []; self.patients = []; self.article_rows = []; self.visual_rows = []
        self.source_ledgers = {}
        self.slots = [asyncio.Semaphore(self.config.concurrency_per_endpoint) for _ in endpoints]
        self.active = [0 for _ in endpoints]
        self.peak_active = [0 for _ in endpoints]
        self.clients = []
        from .gepa_feedback import ClinicalFeedback
        self.schemas = {**{k: v.model_json_schema() for k, v in TASK_MODELS.items()},
            **{'coverage_repair_'+k:v.model_json_schema() for k,v in TASK_MODELS.items()},
            **{k: v.model_json_schema() for k, v in ARTICLE_TASKS.items()},
            'figure_attribution': FigureReview.model_json_schema(),
            'figure_visuals': FigureVisuals.model_json_schema(),
            'pixel_attribution': FigureReview.model_json_schema(),
            'timeline_v2': timeline_wire_schema(), 'timeline_completion': timeline_wire_schema(),
            'summary_completion': ARTICLE_TASKS['summary'].model_json_schema(), 'ordering_review': ordering_schema(),
            'claim_audit': ClaimAudit.model_json_schema(),
            'clinical_inventory': ClinicalInventory.model_json_schema(),
            'coverage_audit': CoverageAudit.model_json_schema(),
            'joint_figure': JointFigureAnalysis.model_json_schema(),
            'gepa_assessment': ClinicalFeedback.model_json_schema()}
        from .evidence_ledger import ChunkMap, AttributeAudit
        from .episode_ledger import ledger_wire_schema
        self.schemas.update({'ledger_chunk_map':ChunkMap.model_json_schema(),
            'ledger_attribute_audit':AttributeAudit.model_json_schema(),
            'ledger_events':ledger_wire_schema(),'ledger_edges':ledger_wire_schema(),
            **{'ledger_repair_'+k:v.model_json_schema() for k,v in TASK_MODELS.items()}})
        for endpoint in endpoints:
            api = APIConfig(endpoints=[endpoint.rstrip('/')], model=self.config.served_model,
                model_id=self.config.model_id, revision=self.config.revision,
                api_key_env='OP2_LOCAL_UNUSED_API_KEY', response_format='prompt_json',
                temperature=self.config.temperature, top_p=self.config.top_p, seed=self.config.seed,
                max_tokens=self.config.max_output_tokens,
                max_retry_tokens=max(self.config.max_output_tokens, self.config.max_retry_tokens),
                http_retries=0, timeout_seconds=self.config.timeout_seconds,
                extra_body={'chat_template_kwargs': {'reasoning_strength': self.config.reasoning_strength},
                            'skip_special_tokens': False, 'top_k': self.config.top_k})
            self.clients.append(APIClient(api, http=http, schema_overrides=self.schemas))

    async def close(self):
        await asyncio.gather(*(client.close() for client in self.clients))

    def save_attempt(self, attempt):
        self.attempt_number += 1
        attempt['ledger_sequence'] = self.attempt_number
        with (self.output / 'attempts.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(attempt, ensure_ascii=False) + '\n')

    async def token_count(self, client, messages):
        pixels = any(isinstance(m['content'], list) and
                     any(b.get('type') == 'image_url' for b in m['content']) for m in messages)
        body = {'model': self.config.served_model, 'messages': messages,
                'add_generation_prompt': True, 'add_special_tokens': False,
                'chat_template_kwargs': client.config.extra_body['chat_template_kwargs']}
        response = await client.http.post(client.config.endpoints[0].removesuffix('/v1') + '/tokenize',
                                         json=body, timeout=min(60, self.config.timeout_seconds))
        response.raise_for_status()
        reply = response.json(); count = reply.get('count'); tokens = reply.get('tokens')
        server_max = reply.get('max_model_len')
        if (type(count) is not int or count < 1 or not isinstance(tokens, list)
                or count != len(tokens) or any(type(token) is not int for token in tokens)
                or type(server_max) is not int or server_max < 1):
            raise ValueError('serving_tokenizer_did_not_return_exact_count')
        # vLLM v0.30.0 tokenize preprocess_chat -> renderer -> mm_processor.apply
        # counts engine input IDs after image expansion. This contract requires
        # that pinned server implementation and the identical full image prompt.
        # Do not replace this with text tokenization or a hand-waved image bound.
        return count, 'server_multimodal_exact_vllm_0.30.0' if pixels else 'server_text_exact', server_max

    def prepared_messages(self, task, messages):
        messages = copy.deepcopy(messages)
        if task in self.config.prompt_overrides:
            strategy = self.config.prompt_overrides[task]
            if strategy and self.config.prompt_mode == 'rewrite':
                from .prompt_strategy import rewrite_strategy
                messages = rewrite_strategy(task, messages, strategy)
            elif strategy:
                messages.append({'role':'user', 'content':'ADDITIONAL TASK INSTRUCTIONS:\n'+strategy})
        if task.startswith('coverage_repair_') and 'coverage_repair' in self.config.prompt_overrides:
            strategy = self.config.prompt_overrides['coverage_repair']
            if strategy and self.config.prompt_mode == 'rewrite':
                from .prompt_strategy import rewrite_strategy
                messages = rewrite_strategy(task.removeprefix('coverage_repair_'), messages, strategy)
            elif strategy:
                messages.append({'role':'user','content':strategy})
        return messages

    async def partition_facts(self, task, rows, builder, replica):
        """Count actual serving tokens; split fact hints, never primary source.

        A source-only/single-fact overflow is passed to call() for an explicit
        failed receipt. It is never hidden by shortening the article or values.
        """
        size = len(rows)
        while True:
            batch = rows[:size]
            try:
                count, _, maximum = await self.token_count(self.clients[replica],
                    self.prepared_messages(task, builder(batch)))
            except (httpx.HTTPError, ValueError, TypeError):
                return batch, rows[size:]  # call() records tokenizer failures
            required = (self.config.max_output_tokens if self.config.require_full_output_budget
                        else self.config.min_output_tokens)
            if count + self.config.context_safety_tokens + required <= min(self.context, maximum) or size <= 1:
                return batch, rows[size:]
            size = max(1, size // 2)

    async def call(self, task, messages, checker, identity, replica, segments, *, packet=None, partial_builder=None,
                   repair_from=None):
        clinical_task = canonical_clinical_task(task)
        if self.config.scatter_calls:
            # Independent task calls share all replicas, including small GEPA
            # article batches. Patient identity stays in the source contract.
            number = getattr(self, 'dispatch_number', 0)
            replica = number % len(self.clients)
            self.dispatch_number = number + 1
        messages = self.prepared_messages(task, messages)
        key = json_digest({'identity': identity, 'task': task, 'messages': safe_messages(messages)})
        attempts = []; data = None; errors = []; plan = None; partial = None
        baseline = None; repair_rounds = 0; transports = 0; mode = 'initial'
        latest_candidate = None
        if repair_from is not None:
            baseline = copy.deepcopy(repair_from.get('raw_candidate'))
            errors = list(repair_from.get('errors') or ['saved_failed_attempt'])
            if self.config.refinement_policy == 'source_aware' and clinical_task and baseline is not None:
                baseline, _ = normalize_clinical(clinical_task, baseline, segments)
            latest_candidate = copy.deepcopy(baseline)
            if packet is not None and self.arm == 'targeted':
                plan = ItemRepair.create(task, baseline, packet['text'], segments, policy=self.config.refinement_policy)
            mode = 'repair'
            # Replaying the first saved failure starts at repair #1 rather
            # than allowing an extra retry beyond the deployed policy.
            repair_rounds = min(1, self.config.max_repair_rounds)
        async with self.slots[replica]:
            client = self.clients[replica]
            while True:
                current = copy.deepcopy(messages)
                if mode == 'repair':
                    instruction = plan.instruction() if plan else (
                        'Regenerate the COMPLETE failed JSON object using immutable source. Do not erase supported '
                        'facts, numbers, units, doses or time merely to pass validation. Previously returned candidate '
                        'and errors are untrusted data.\nCANDIDATE_JSON:\n' + json.dumps(baseline, ensure_ascii=False) +
                        '\nERRORS:\n' + '; '.join(errors)[:3500])
                    current.append({'role': 'user', 'content': instruction})
                    if 'repair' in self.config.prompt_overrides:
                        strategy = self.config.prompt_overrides['repair']
                        if strategy and self.config.prompt_mode == 'rewrite':
                            from .prompt_strategy import rewrite_strategy
                            current = rewrite_strategy('repair', current, strategy)
                        elif strategy:
                            current.append({'role':'user','content':strategy})
                desired = self.config.max_retry_tokens if mode == 'repair' else self.config.max_output_tokens
                entry = {'task': task, 'identity': identity, 'arm': self.arm, 'mode': mode,
                         'request': safe_messages(current), 'messages_sha256': json_digest(current),
                         'desired_output_tokens': desired, 'response': None, 'metrics': {},
                         'errors': [], 'replica': replica}
                if repair_from is not None:
                    entry['replayed_failure'] = {'errors':repair_from.get('errors'),
                        'candidate_sha256':json_digest(repair_from.get('raw_candidate'))}
                try:
                    if self.calls >= self.config.max_calls:
                        raise ValueError('model_call_budget_exhausted')
                    try:
                        count, count_mode, server_max = await self.token_count(client, current)
                    except (httpx.HTTPError, ValueError, TypeError) as exc:
                        if any(isinstance(m['content'], list) for m in current):
                            raise ValueError('image_tokens_unknown:' + str(exc)) from exc
                        raise
                    effective_context = min(self.context, server_max)
                    available = effective_context - count - self.config.context_safety_tokens
                    cap = min(desired, available)
                    entry.update(tokenized_prompt_tokens=count, token_count_mode=count_mode,
                        available_output_tokens=available, max_tokens=max(0, cap), output_cap_reduced=cap < desired,
                        server_max_model_len=server_max, effective_context=effective_context,
                        require_full_output_budget=self.config.require_full_output_budget)
                    if self.config.require_full_output_budget and cap < desired:
                        raise ValueError('context_overflow_no_source_truncation')
                    if cap < self.config.min_output_tokens:
                        raise ValueError('context_overflow_no_source_truncation')
                    allowance = count + cap
                    # Tokenization awaits network I/O. Other tasks may have
                    # consumed calls while this task waited; reserve atomically
                    # only after rechecking the shared allowance.
                    if self.calls >= self.config.max_calls:
                        raise ValueError('model_call_budget_exhausted')
                    if self.accounted_tokens + self.reserved_tokens + allowance > self.config.max_total_tokens:
                        raise ValueError('total_token_budget_exhausted')
                    self.calls += 1; self.reserved_tokens += allowance
                    self.active[replica] += 1
                    self.peak_active[replica] = max(self.peak_active[replica], self.active[replica])
                    try:
                        response = await client.complete_once(client.config.endpoints[0], task, current, cap)
                    finally:
                        self.active[replica] -= 1
                    self.reserved_tokens -= allowance
                    actual = (response.prompt_tokens + response.completion_tokens
                              if response.prompt_tokens is not None and response.completion_tokens is not None else allowance)
                    self.accounted_tokens += max(0, actual)
                    entry.update(response=response.response(), metrics=response.metrics(),
                                 token_budget_accounted=actual, usage_missing_reserved=response.prompt_tokens is None or response.completion_tokens is None)
                    errors = []; candidate = None; citation_audit = []
                    if response.error:
                        errors = [response.error]
                    elif response.finish_reason not in {'stop', 'eos'}:
                        errors = ['incomplete_finish:' + str(response.finish_reason)]
                    else:
                        try:
                            parsed = parse_output(response.content, finish_reason=response.finish_reason,
                                max_chars=self.config.max_response_characters)
                            candidate = parsed.value
                            entry.update(parse_method=parsed.method, parse_transformations=parsed.transformations,
                                         raw_candidate=copy.deepcopy(candidate))
                            if task in {'timeline_v2', 'timeline_completion', 'ordering_review'}:
                                candidate, span_audit = resolve_timeline_spans(candidate,
                                    identity['article_id'] + ':jats', segments)
                                entry['span_resolution'] = span_audit
                                latest_candidate = copy.deepcopy(candidate)
                                if span_audit['unresolved']:
                                    raise ValueError('timeline_span_unresolved:' + '; '.join(
                                        x['code'] + ':' + x['path'] for x in span_audit['unresolved']))
                            elif self.arm == 'targeted':
                                if task in {'figure_visuals', 'pixel_attribution'}:
                                    from .pilot_media import repair_visual_citations
                                    candidate, citation_audit = repair_visual_citations(candidate, segments)
                                else:
                                    candidate, citation_audit = recover_citations(candidate, segments)
                            entry['citation_recovery'] = citation_audit
                            if self.config.refinement_policy == 'source_aware' and clinical_task:
                                candidate, changes = normalize_clinical(clinical_task, candidate, segments)
                                entry['field_normalization'] = changes
                            latest_candidate = copy.deepcopy(candidate)
                            if plan:
                                plan.apply(candidate); candidate = plan.output()
                                entry['item_repair'] = plan.audit()
                                if plan.pending:
                                    partial = candidate
                                    raise ValueError('targeted_items_unresolved')
                            elif mode == 'repair' and baseline is not None:
                                # Whole-object regeneration is never a license to
                                # delete supported values or neighbors.
                                protected = (baseline if self.config.refinement_policy == 'legacy'
                                             else repair_snapshot(clinical_task or task, baseline, checker))
                                losses = erased(protected, candidate)
                                if self.config.refinement_policy == 'source_aware':
                                    losses += changed_accepted_atoms(clinical_task or task, protected, candidate)
                                    if clinical_task:
                                        losses += protected_value_changes(protected,candidate)
                                entry['repair_protection'] = {'policy': self.config.refinement_policy,
                                    'protected_snapshot': protected, 'erased_paths': losses}
                                if losses:
                                    latest_candidate = copy.deepcopy(baseline)
                                    raise ValueError('repair_refused_field_erasure:' + ','.join(losses)[:2000])
                            data = checker(candidate)
                            entry['checked_candidate'] = candidate
                        except (ValueError, TypeError, KeyError) as exc:
                            errors = [str(exc)[:4000]]; data = None
                            if baseline is None:
                                baseline = copy.deepcopy(candidate)
                            if packet is not None and plan is None and self.arm == 'targeted':
                                plan = ItemRepair.create(task, candidate, packet['text'], segments,
                                    policy=self.config.refinement_policy)
                                if plan:
                                    entry['item_repair'] = plan.audit()
                    entry['errors'] = errors
                    attempts.append(entry); self.save_attempt(entry)
                    if not errors:
                        break
                    if response.error:
                        if client.transient(response) and transports < self.config.transport_retries:
                            transports += 1
                            continue
                        break
                    if (self.arm == 'targeted' and repair_rounds < self.config.max_repair_rounds
                            and (plan is None or plan.repair_batch())):
                        repair_rounds += 1; mode = 'repair'
                        continue
                    break
                except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                    errors = [str(exc)[:4000]]
                    entry['errors'] = errors; attempts.append(entry); self.save_attempt(entry)
                    break
        if plan is not None and plan.pending:
            partial = plan.output()
        quarantine = []
        if data is None and partial_builder and self.config.refinement_policy == 'source_aware':
            try:
                partial, quarantine = partial_builder(latest_candidate)
            except (ValueError, TypeError, KeyError) as exc:
                partial = None
                quarantine = [{'kind':'partial_recovery_failure','reason':str(exc),
                               'candidate':latest_candidate}]
        if partial is not None:
            try:
                partial = checker(partial)
            except (ValueError, TypeError, KeyError):
                partial = None
        result = {'task': task, 'identity': identity, 'arm': self.arm, 'replica': replica,
            'status': 'valid' if data is not None and not errors else 'partial' if partial is not None else 'failed',
            'data': data if not errors else partial, 'errors': errors, 'attempts': attempts,
            'semantic_review': 'unreviewed', 'clinical_accuracy_verified': False}
        result['refinement_policy'] = self.config.refinement_policy
        result['quarantine'] = quarantine
        if task in {'timeline_v2','timeline_completion'} and result['data'] is not None and self.config.refinement_policy == 'source_aware':
            result['data'], result['export_event_id_map'] = canonical_timeline_ids(result['data'])
        write_json(self.output / 'tasks' / (key + '.json'), result)
        self.results.append(result)
        return result

    def clinical_checker(self, task, packet, segments):
        def checker(value):
            checked = validate(task, value, packet['text'], segments=segments)
            if not checked.valid:
                raise ValueError('; '.join(checked.errors))
            if task == 'case_context' and (checked.data['multiple_index_patients'] is not False or
                                          checked.data['case_kind'] == 'multi_patient'):
                raise ValueError('target_packet_requires_one_index_patient')
            return checked.data
        return checker

    def patient_vision(self, article, roster, patient_id, caption_reviews):
        annotations = [row for row in self.visual_rows if row['identity']['article_id'] == article['article_id']]
        bound = []
        by_figure = {}
        comparisons = []
        text_by_figure = {review['data']['figure_id']: review['data'] for review in caption_reviews}
        for row in annotations:
            figure_id = row['identity']['figure_id']; by_figure[figure_id] = row
            attribution = row.get('attribution', {})
            if attribution.get('status') != 'valid':
                continue
            data = attribution['data']
            review = bind_figure_review(data, article, roster, method='pixels_caption_article',
                                       pixel_provenance=row['pixel_provenance'])
            bound.append(review)
            def owners(value):
                return {(assignment['panel'], tuple(sorted(assignment['patient_ids'])))
                    for assignment in value.get('assignments', []) if assignment['scope'] in {'individual', 'shared'}}
            caption_owners = owners(text_by_figure.get(figure_id, {})); pixel_owners = owners(data)
            disagreement = caption_owners != pixel_owners
            comparisons.append({'figure_id': figure_id,
                'caption_patient_assignments': [{'panel': panel, 'patient_ids': list(ids)} for panel, ids in sorted(caption_owners, key=str)],
                'pixel_patient_assignments': [{'panel': panel, 'patient_ids': list(ids)} for panel, ids in sorted(pixel_owners, key=str)],
                'status': ('competing_patient_assignments' if caption_owners and pixel_owners and disagreement
                           else 'attribution_difference_requires_review' if disagreement else 'assignments_agree_unreviewed'),
                'semantic_review': 'unreviewed', 'clinical_ownership_verified': False})
        assignments, media = patient_media(article, roster, patient_id, bound)
        for figure in media['figures']:
            row = by_figure[figure['figure_key']]
            figure['visual_description'] = row.get('visual', {}).get('data')
            panel_key = lambda p:p.strip().casefold() if p else None
            panels = {panel_key(a['panel']) for a in assignments if a['figure_id'] == figure['figure_key']}
            figure['panel_columns'] = [p for p in row.get('panel_columns', []) if panel_key(p.get('panel')) in panels]
            figure['visual_description_scope'] = 'whole figure; only assigned panel_columns are patient associated'
            figure['pixel_provenance'] = row['pixel_provenance']
            figure['clinical_fact_status'] = 'unreviewed_visual_annotation'
            figure['attribution_comparison'] = next((c for c in comparisons if c['figure_id'] == figure['figure_key']), None)
            figure['usable_as_clinical_fact'] = False
        media['assignments'] = assignments
        media['caption_pixel_comparisons'] = comparisons
        media['article_figure_annotations'] = [{'figure_id': row['identity']['figure_id'],
            'status': row['status'], 'visual_description': row.get('visual', {}).get('data'),
            'panel_columns': row.get('panel_columns', []), 'pixel_provenance': row.get('pixel_provenance'),
            'attribution': row.get('attribution', {}).get('data'),
            'association_scope': 'article_candidate; use reviewed patient/panel assignments',
            'clinical_fact_status': 'unreviewed_visual_annotation'} for row in annotations]
        return {'status': 'pixel_annotations_available' if annotations else 'no_prepared_pixel_annotations',
            'media': media, 'pixels_inspected': any(row['pixels_inspected'] for row in annotations),
            'caption_only_media': None, 'pixels_are_clinical_facts': False,
            'clinical_accuracy_verified': False, 'pixel_accuracy_verified': False,
            'human_review': 'pending', 'model_annotation_review': 'unreviewed'}

    async def patient(self, article, roster, patient, reviews, replica):
        context_article, context_patient, context_audit = article, patient, None
        if self.input_scope == 'patient_sections':
            context_article, context_patient, context_audit = patient_view(article, roster, patient)
        packet = patient_packet(article, roster, context_patient,
            scope='localized' if self.input_scope == 'patient_sections' else 'whole_article', figure_reviews=reviews)
        packet['context_selection'] = context_audit
        packet['input_scope'] = self.input_scope
        rid = packet['record_id']; segments = packet_segments(packet)
        identity = {'article_id': article['article_id'], 'patient_id': patient['patient_id'], 'record_id': rid}
        ledger_mode = self.config.experimental_pipeline == 'evidence_ledger'
        routing = {}
        async def clinical(task):
            task_packet, task_segments = packet, segments
            if ledger_mode:
                from .evidence_ledger import route_segments, subset_packet
                selected, receipt = route_segments(article,patient,task,self.source_ledgers.get(article['article_id'],[]))
                task_packet = subset_packet(packet,article,selected)
                task_segments = packet_segments(task_packet)
                routing[task] = receipt | {'segment_ids':sorted(selected),
                    'request_source_characters':len(task_packet['text'])}
            messages = messages_for(task_packet, task, namespace='bounded-pmc-pilot/1')
            messages[-1]['content'] += '\nReturn only JSON matching SCHEMA:\n' + json.dumps(self.schemas[task])
            if self.config.refinement_policy == 'source_aware':
                messages[-1]['content'] += ('\nKeep explicit routes in route fields, not only medication names. '
                    'Split named laboratory/immunohistochemical panel members into individual observations. '
                    'Distinguish the performed test from an absent finding. Preserve specimen, value/unit, '
                    'planned versus performed status, and separate encounters. Do not infer missing results. '
                    'For observations, split numeric magnitude, comparator and unit into their fields. '
                    'Preserve the exact result notation in text_value, including scientific exponents; '
                    'keep reference intervals separate. Do not mistake flattened 109 for an explicit 10^9, '
                    'convert units, or copy a reference threshold as the patient result.')
            return await self.call(task, messages, self.clinical_checker(task, task_packet, task_segments),
                                   identity, replica, task_segments, packet=task_packet)
        tasks = await asyncio.gather(*(clinical(task) for task in TASK_MODELS))
        extra_tasks = []
        attribute_receipt = None; episode_receipt = None
        if ledger_mode and self.config.ledger_audit:
            from .ledger_pipeline import challenge_attributes
            candidate_rows = [row for result in tasks for row in review_rows(article,rid,result['task'],result['data'])]
            # Rechecks see the complete original source, including passages that
            # the map did not route. Audit hypotheses never replace source text.
            full_packet = patient_packet(article,roster,patient,scope='whole_article',figure_reviews=reviews)
            challenge_tasks,attribute_receipt = await challenge_attributes(self,article,patient,full_packet,
                tasks,candidate_rows,identity,replica)
            extra_tasks.extend(challenge_tasks)
        inventory = None
        if self.config.inventory_clinical_features and not ledger_mode:
            # The audit sees the complete article, so incorrect compact source
            # selection cannot hide a missed patient feature from the reviewer.
            inventory = await self.call('clinical_inventory', inventory_messages(article, patient),
                lambda value: check_inventory(value, article), identity, replica, article['segments'])
            extra_tasks.append(inventory)
        summary = await self.call('summary', task_messages('summary', context_article, patient),
            lambda value: check_article_task('summary', value, context_article, patient),
            identity, replica, context_article['segments'])
        rows = [row for result in tasks for row in review_rows(article, rid, result['task'], result['data'])]
        facts = {row['review_id']: rid for row in rows}
        _, sources = sources_for(article if ledger_mode else context_article)
        def timeline_checker(value):
            graph = PatientTimeline.model_validate(value)
            if graph.record_id != rid:
                raise ValueError('timeline_wrong_patient')
            audit = audit_timeline(graph, sources, facts)
            if not audit['structural_source_gates_passed']:
                raise ValueError('; '.join(i['code'] for i in audit['issues'] if i['severity'] == 'block'))
            return graph.model_dump()
        if ledger_mode and self.config.ledger_delta_timeline:
            from .ledger_pipeline import assemble_episodes
            timeline, delta_tasks, episode_receipt = await assemble_episodes(self,article,patient,rows,identity,replica,sources)
            extra_tasks.extend(delta_tasks)
        else:
            timeline = await self.call('timeline_v2', timeline_messages(context_article, patient, rid, facts, rows,
                                      refined=self.config.refinement_policy == 'source_aware'),
                                      timeline_checker, identity, replica, context_article['segments'],
                                      partial_builder=lambda value: partial_timeline(value, sources, facts, rid))
        original_timeline = copy.deepcopy(timeline['data'])
        if self.config.review_ordering and not ledger_mode and timeline['data'] is not None:
            graph = copy.deepcopy(timeline['data'])
            ordering = await self.call('ordering_review', ordering_messages(context_article, graph),
                lambda value: check_ordering(value, graph, sources, facts), identity, replica, context_article['segments'])
            extra_tasks.append(ordering)
            if ordering['status'] == 'valid':
                timeline = {**timeline, 'data':{**graph, 'edges':ordering['data']['edges'],
                    'limitations':graph['limitations']+ordering['data']['limitations']}}
        audits = []; coverage_audits = []
        if inventory and inventory['status'] == 'valid':
            features = [{'inventory_index':i, **f} for i,f in enumerate(inventory['data']['features'])]
            async def cover(start):
                batch = features[start:start+16]
                return await self.call('coverage_audit', coverage_messages(article,patient,batch,rows),
                    lambda value, batch=batch: check_coverage(value,batch,rows),
                    {**identity,'batch':start//16}, replica, article['segments'])
            coverage_audits = await asyncio.gather(*(cover(i) for i in range(0,len(features),16)))
            extra_tasks.extend(coverage_audits)
            if self.config.coverage_repair:
                missing = {d['inventory_index'] for r in coverage_audits if r['status']=='valid'
                    for d in r['data']['decisions'] if d['status'] in {'missing','uncertain'}}
                hints = [f for f in features if f['inventory_index'] in missing]
                before_rows = copy.deepcopy(rows)
                backfill_packet = patient_packet(article,roster,patient,scope='whole_article',figure_reviews=reviews)
                backfill_packet['context_selection']=context_audit
                backfill_packet['input_scope']='whole_article_coverage_backfill'
                backfill_segments = packet_segments(backfill_packet)
                for task in sorted({f['task'] for f in hints}):
                    previous = next(r for r in tasks if r['task']==task)
                    if previous['data'] is None: continue
                    # One bounded source-grounded backfill pass, not recursive
                    # self-confirmation. Existing accepted clinical values are
                    # protected; speculative audit judgments never become facts.
                    messages = messages_for(backfill_packet,task,namespace='bounded-pmc-pilot/1')
                    messages[-1]['content'] += '\nSCHEMA:\n'+json.dumps(self.schemas[task])
                    messages.append({'role':'user','content':'Check these untrusted omission hypotheses against '
                        'the original patient source. Return a COMPLETE extraction object preserving every '
                        'previously accepted fact and value, adding only independently supported missing facts. '
                        'Do not copy audit claims without source support.\nCURRENT:\n'+json.dumps(previous['data'])+
                        '\nHYPOTHESES:\n'+json.dumps([f for f in hints if f['task']==task])})
                    def backfill_checker(value,task=task,previous=previous):
                        checked = self.clinical_checker(task,backfill_packet,backfill_segments)(value)
                        losses = erased(previous['data'],checked)+changed_accepted_atoms(task,previous['data'],checked)
                        losses += protected_value_changes(previous['data'],checked)
                        if losses: raise ValueError('Coverage repair changed accepted facts: '+','.join(losses))
                        return checked
                    repaired = await self.call('coverage_repair_'+task,messages,backfill_checker,
                        {**identity,'stage':'coverage_repair'},replica,backfill_segments)
                    extra_tasks.append(repaired)
                    if repaired['status']=='valid':
                        previous.update(data=repaired['data'])
                        packet=backfill_packet; segments=backfill_segments
                        _,sources=sources_for(article)
                rows = [row for r in tasks for row in review_rows(article,rid,r['task'],r['data'])]
                facts = {r['review_id']:rid for r in rows}
                # Keep the original facts/graph for paired review. Added facts
                # do not silently acquire events, dates or graph relationships.
                for repaired in extra_tasks:
                    if repaired['task'].startswith('coverage_repair_'):
                        repaired['baseline_fact_ids']=[r['review_id'] for r in before_rows]
        if self.config.audit_claims and not ledger_mode:
            # Small claim batches preserve output space and attribution context.
            # Every claim is audited; no sample is disguised as comprehensive.
            async def audit_batch(start):
                batch = rows[start:start+16]
                audit_messages = claim_messages(article, patient, batch)
                if inventory and inventory['data']:
                    audit_messages[-1]['content'] += '\nINDEPENDENT COVERAGE HYPOTHESES:\n'+json.dumps(inventory['data'])
                return await self.call('claim_audit', audit_messages,
                    lambda value, batch=batch: check_claim_audit(value, article, batch),
                    {**identity,'batch':start//16}, replica, article['segments'])
            audits = await asyncio.gather(*(audit_batch(i) for i in range(0,len(rows),16)))
            extra_tasks.extend(audits)
        if self.config.timeline_completion and not ledger_mode:
            # Rebuild AFTER coverage backfill, using the actual delivered facts.
            # A summary and model inventory never replace immutable source text.
            rows = [row for r in tasks for row in review_rows(article,rid,r['task'],r['data'])]
            facts = {r['review_id']:rid for r in rows}
            _, sources = sources_for(article)
            def completion_messages(batch):
                messages = timeline_messages(article,patient,rid,facts,batch,refined=True)
                messages[-1]['content'] += ('\nCURRENT_PARTIAL_TIMELINE (unreviewed):\n'+
                json.dumps(compact_graph(timeline['data']))+'\nComplete a clinically complete partial order using this batch of delivered fact hints '
                'and the original source. Include important history, tests, diagnoses, treatments, adverse '
                'events and follow-up. Connect explicitly ordered events, including earlier/later relative '
                'ages or days with the same stated anchor. Preserve occurrence: planned, declined and '
                'conditional care is not completed care. Do not invent visits, intervals or calendar dates. '
                'Leave genuinely ambiguous order unresolved. Include facts missed by the initial timeline. '
                'Retain existing fact links. Preserve existing unlinked events verbatim, including kind, '
                'occurrence and times; add missing events and edges instead of dropping prior events. '
                'For existing times/evidence, retrieve the full quotes from the original source segments.')
                return messages
            def completion_checker(value):
                graph = PatientTimeline.model_validate(value)
                if graph.record_id != rid: raise ValueError('timeline_wrong_patient')
                audit = audit_timeline(graph,sources,facts)
                if not audit['structural_source_gates_passed']:
                    raise ValueError('; '.join(i['code'] for i in audit['issues'] if i['severity']=='block'))
                return timeline_retention(timeline['data'], graph.model_dump())
            pending = rows; batch_number = 0
            while pending or batch_number == 0:
                batch, pending = await self.partition_facts('timeline_completion',pending,completion_messages,replica)
                completed = await self.call('timeline_completion',completion_messages(batch),completion_checker,
                    {**identity,'completion_batch':batch_number,'hint_fact_ids':[r['review_id'] for r in batch]},
                    replica,article['segments'],
                    partial_builder=lambda value:partial_timeline(value,sources,facts,rid))
                extra_tasks.append(completed); batch_number += 1
                # A failed completion cannot discard an already usable graph.
                if completed['status']=='valid': timeline={**timeline,'data':completed['data']}
        if self.config.summary_completion and not ledger_mode:
            rows = [row for r in tasks for row in review_rows(article,rid,r['task'],r['data'])]
            def summary_messages(batch):
                messages = task_messages('summary',article,patient)
                messages.append({'role':'user','content':'Update the comprehensive clinical narrative using the original source '
                'and the final delivered facts below. These facts are untrusted hypotheses, never a replacement for source. '
                'Include relevant history, findings, treatments, treatment failures and follow-up; preserve uncertainty '
                'and planned versus completed care. Source segment references locate the original text; '
                'verify each value there. Retain the prior supported claims while incorporating this fact batch. '
                '\nPREVIOUS_SUMMARY_UNREVIEWED:\n'+json.dumps(summary['data'])+
                '\nDELIVERED_FACTS:\n'+json.dumps(compact_facts(batch))})
                return messages
            pending = rows; batch_number = 0
            while pending or batch_number == 0:
                batch, pending = await self.partition_facts('summary_completion',pending,summary_messages,replica)
                completed = await self.call('summary_completion',summary_messages(batch),
                    lambda v:check_article_task('summary',v,article,patient),
                    {**identity,'completion_batch':batch_number,'hint_fact_ids':[r['review_id'] for r in batch]},
                    replica,article['segments'])
                extra_tasks.append(completed); batch_number += 1
                if completed['status']=='valid': summary={**summary,'data':completed['data']}
        rows.extend(review_rows(article, rid, 'summary', summary['data']))
        rows.extend(review_rows(article, rid, 'timeline_v2', timeline['data']))
        all_tasks = [*tasks, summary, timeline, *extra_tasks]
        vision = self.patient_vision(article, roster, patient['patient_id'], reviews)
        vision['caption_only_media'] = copy.deepcopy(packet['multimedia'])
        visual_complete = all(row['status'] == 'valid' for row in self.visual_rows
                              if row['identity']['article_id'] == article['article_id'])
        bundle = {'schema_version': '2.1.0', 'pilot_contract': 'bounded-extraction/1',
            'source': packet, 'model': {'model_id': self.config.model_id, 'revision': self.config.revision},
            'arm': self.arm, 'input_scope': self.input_scope, 'seed': self.config.seed,
            'context_selection': context_audit, 'expected_tasks': list(TASK_MODELS),
            'sections': {r['task']: r['data'] for r in tasks},
            'companions': {'summary': summary['data'], 'timeline_v2': timeline['data']},
            'experimental_reviews': {'timeline_before_order_review':original_timeline,
                'evidence_ledger':{'enabled':ledger_mode,'routes':routing,'attribute_audit':attribute_receipt,
                    'episode_assembly':episode_receipt,'model_hypotheses_are_not_gold':True},
                'clinical_inventory':inventory, 'claim_audits':audits,
                'coverage_audits':coverage_audits,
                'model_judgments_are_not_accuracy_gold':True, 'facts_automatically_deleted':False,
                'source_grounded_backfill_requested':self.config.coverage_repair},
            'quality': {r['task']: {'status': r['status'], 'errors': r['errors'],
                'quarantine': r.get('quarantine', []),
                'field_normalization': [change for a in r['attempts'] for change in a.get('field_normalization', [])]}
                for r in all_tasks},
            'all_clinical_companion_tasks_valid': all(r['status'] == 'valid' for r in all_tasks),
            'complete_for_scope': all(r['status'] == 'valid' for r in all_tasks) and visual_complete,
            'semantic_coverage_verified': False, 'clinical_review_status': 'unreviewed',
            'clinical_accuracy_verified': False,
            'vision': vision}
        if timeline['data'] is not None:
            bundle['timeline_audit'] = audit_timeline(PatientTimeline.model_validate(timeline['data']), sources, facts)
            bundle['relative_timeline'] = relative_order(timeline['data'])
        events = (timeline['data'] or {}).get('events',[])
        bundle['patient_state_index'] = {'facts':[{
            'fact_id':r['review_id'], 'task':r['task'], 'value_as_extracted':r['candidate'],
            'event_ids':[e['event_id'] for e in events if r['review_id'] in e['fact_ids']],
            'time_association':'linked_event_unreviewed' if any(r['review_id'] in e['fact_ids'] for e in events) else 'unknown'
            } for r in rows if r['task'] in TASK_MODELS],
            'state_carried_forward':False,'synthetic_dates_generated':False,
            'clinical_fidelity_verified':False}
        bundle['observation_measurements'] = observation_measurements(bundle['sections'].get('observations'), segments)
        if self.config.robust_contracts:
            bundle['coverage_inventory'] = coverage_inventory(article, [rid])
            envelopes = []
            for row in rows:
                if row['task'] not in TASK_MODELS:
                    continue
                collection = row['pointer'].split('/')[1] if row['pointer'] != '/' else 'case_context'
                fact = FactEnvelope(fact_id=row['review_id'], record_id=rid, task=row['task'],
                    collection=collection, value=row['candidate'],
                    field_support=literal_field_support(row['candidate'], row['evidence_spans']),
                    origin='article_text', supersedes_fact_id=None)
                envelopes.append({'fact': fact.model_dump(), 'gate_report': field_support_report(fact, sources),
                                  'section_literal_validation_only': True,
                                  'field_support_basis': 'literal field value inside this fact citation; entailment and attribution remain unreviewed',
                                  'evidence_spans_for_reviewer': row['evidence_spans']})
            bundle['fact_review'] = {'field_support_required_before_promotion': True,
                'all_fields_semantically_unreviewed': True, 'review_ids': list(facts), 'envelopes': envelopes}
        write_json(self.output / 'patients' / (json_digest(rid) + '.json'), bundle)
        write_json(self.output / 'review' / (json_digest(rid) + '.json'), rows)
        self.patients.append(bundle)
        return bundle

    def image_rows(self, manifest):
        if manifest is None:
            return []
        if isinstance(manifest, (str, Path)):
            path = Path(manifest)
            value = json.loads(path.read_text())
            base = path.parent.parent if path.parent.name == 'vision-assets' else path.parent
        else:
            value = manifest; base = Path(value.get('output_dir', Path.cwd())) if isinstance(value, dict) else Path.cwd()
        rows = value.get('rows', value.get('figures', [])) if isinstance(value, dict) else value
        if not isinstance(rows, list):
            raise ValueError('image_manifest_requires_rows_list')
        result = []
        for row in rows[:self.config.max_figures]:
            result.append({**row, '_manifest_base': str(base)})
        return result

    def pixels(self, row, article):
        if row.get('status') != 'ready':
            raise ValueError('image_manifest_not_ready')
        figure = next(f for f in article['figures'] if f['figure_key'] == row['figure_id'])
        if (figure.get('rights_statements') or figure.get('reuse_status') == 'asset_rights_review'
                or figure.get('fixture_asset_rights_review') == 'required_before_reuse'):
            raise ValueError('pixel_asset_rights_review_required')
        provenance = row['pixel_provenance']
        if provenance['url'] not in figure['image_urls']:
            raise ValueError('pixels_not_in_canonical_figure_manifest')
        if not recheck_license(row.get('source_license', {})).get('allowed'):
            raise ValueError('pixel_source_rights_review_required')
        if provenance['bytes'] > self.config.max_image_bytes:
            raise ValueError('pixel_file_limit_no_truncation')
        from .pilot_media import load_asset
        return load_asset(Path(row['_manifest_base']), row)

    async def visual(self, row, article, roster, replica):
        identity = {'article_id': article['article_id'], 'figure_id': row['figure_id']}
        try:
            pixels = self.pixels(row, article)
        except (ValueError, KeyError, OSError, StopIteration) as exc:
            result = {'identity': identity, 'status': 'failed', 'errors': [str(exc)],
                      'pixel_provenance': row.get('pixel_provenance'), 'pixels_inspected': False}
            self.visual_rows.append(result)
            return result
        refined = self.config.refinement_policy == 'source_aware'
        if self.config.pixel_strategy == 'joint':
            def checker(value):
                parsed = JointFigureAnalysis.model_validate(value).model_dump()
                visual_data = validate_visuals(parsed['visual'],article,row['figure_id'],refined=refined)
                attribution_data = panel_aligned_attribution(parsed['attribution'],visual_data,article,roster,row['figure_id'])
                return {'visual':visual_data,'attribution':attribution_data}
            joint = await self.call('joint_figure', visual_messages(article,roster,row['figure_id'],pixels,
                joint=True,refined=refined,focused=self.config.focused_pixels),checker,identity,replica,article['segments'])
            visual = {**joint,'data':joint['data']['visual'] if joint['data'] else None}
            attribution = {**joint,'data':joint['data']['attribution'] if joint['data'] else None}
        else:
            visual = await self.call('figure_visuals', visual_messages(article, roster, row['figure_id'], pixels,
                refined=refined,focused=self.config.focused_pixels),
                lambda value: validate_visuals(value, article, row['figure_id'], refined=refined),
                identity, replica, article['segments'],
                partial_builder=lambda value: partial_visuals(value, article, row['figure_id']))
            messages = figure_messages(article, roster, row['figure_id'], focused=self.config.focused_pixels, pixels=pixels)
            def attribution_checker(value):
                if self.config.pixel_strategy == 'staged':
                    if visual['data'] is None:
                        raise ValueError('Panel inventory unavailable; staged attribution is blocked')
                    return panel_aligned_attribution(value,visual['data'],article,roster,row['figure_id'])
                return validate_figure_review(value,article,roster,row['figure_id'])
            if self.config.pixel_strategy == 'staged':
                messages.append({'role':'user','content':'Use precisely these panel IDs; include every panel, '
                    'including aggregate/background/unresolved panels, and never substitute a whole figure. '
                    'This visual inventory is unreviewed, not evidence of identity.\n'+json.dumps(visual['data'])})
            attribution = await self.call('pixel_attribution',messages,attribution_checker,identity,replica,article['segments'])
        inspected = any(a['response'] and not a['response'].get('error') for r in (visual, attribution) for a in r['attempts'])
        result = {'identity': identity, 'status': 'valid' if visual['status'] == attribution['status'] == 'valid' else 'incomplete',
            'visual': visual, 'attribution': attribution, 'pixel_provenance': row['pixel_provenance'],
            'source_license': article['license'], 'pixels_inspected': inspected,
            'semantic_review': 'unreviewed', 'pixel_accuracy_verified': False,
            'clinical_fact_status': 'unreviewed_visual_annotation',
            'panel_columns': list(panel_columns(article['article_id'], visual['data'])) if visual['data'] else []}
        self.visual_rows.append(result)
        return result

    def token_report(self, articles, wall):
        rows = {a.get('article_id'): {'article_id': a.get('article_id'), 'requests': 0,
            'patient_cases': sum(p['source']['article_source']['article_id'] == a.get('article_id') for p in self.patients),
            'input_tokens': 0, 'output_tokens': 0, 'reasoning_tokens': 0,
            'unknown_input_usage': 0, 'unknown_output_usage': 0, 'unknown_reasoning_usage': 0,
            'latency_seconds': 0.0} for a in articles}
        for result in self.results:
            row = rows[result['identity']['article_id']]
            for attempt in result['attempts']:
                if attempt['response'] is None:
                    continue
                row['requests'] += 1
                metric = attempt['metrics']; row['latency_seconds'] += metric.get('latency_seconds', 0)
                for kind, key in [('input', 'prompt_tokens'), ('output', 'completion_tokens'), ('reasoning', 'reasoning_tokens')]:
                    value = metric.get(key)
                    if value is None:
                        row['unknown_' + kind + '_usage'] += 1
                    else:
                        row[kind + '_tokens'] += value
        report = {'per_article': list(rows.values()), 'wall_seconds': wall,
                  'accounted_budget_tokens': self.accounted_tokens,
                  'reasoning_is_subset_of_output': True, 'usage_missing_is_not_zero': True,
                  'all_gpus_output_tokens_per_second_basis': 'sum endpoint completion usage / elapsed arm wall; excludes server startup',
                  'ttft_seconds': percentile([a['metrics']['ttft_seconds'] for r in self.results for a in r['attempts']
                      if a['metrics'].get('ttft_seconds') is not None])}
        for kind in ('input', 'output', 'reasoning'):
            missing = sum(r['unknown_' + kind + '_usage'] for r in rows.values())
            values = [r[kind + '_tokens'] for r in rows.values()]
            report[kind + '_tokens'] = sum(values) if not missing else None
            report['reported_' + kind + '_tokens'] = sum(values)
            report['unknown_' + kind + '_usage_calls'] = missing
            report[kind + '_tokens_per_article'] = percentile(values) if not missing else {'n': len(values), 'mean': None, 'median': None, 'p95': None}
        for row in rows.values():
            row['total_tokens'] = (row['input_tokens'] + row['output_tokens']
                if not row['unknown_input_usage'] and not row['unknown_output_usage'] else None)
        report['total_tokens'] = (report['input_tokens'] + report['output_tokens']
                                  if report['input_tokens'] is not None and report['output_tokens'] is not None else None)
        report['all_gpus_output_tokens_per_second'] = report['output_tokens'] / wall if wall > 0 and report['output_tokens'] is not None else None
        report['reported_only_output_tokens_per_second'] = report['reported_output_tokens'] / wall if wall > 0 else None
        report['all_gpu_count'] = len(self.clients)*self.config.gpus_per_endpoint
        report['gpu_topology_assumption'] = f'{self.config.gpus_per_endpoint} GPU(s) per endpoint; declared topology'
        report['output_tokens_per_gpu_second'] = (report['all_gpus_output_tokens_per_second'] / (len(self.clients)*self.config.gpus_per_endpoint)
            if report['all_gpus_output_tokens_per_second'] is not None else None)
        return report

    async def run(self, articles_path, image_manifest=None, *, frozen_rosters=None, cached_rosters=None, discovery_only=False):
        started = time.monotonic(); path = Path(articles_path)
        if path.stat().st_size > self.config.max_source_bytes:
            raise ValueError('sample_file_size_limit; prepare an explicit smaller downloaded sample')
        # Deterministic bounded selection never changes between comparison arms.
        selected = []
        opener = gzip.open if str(path).endswith('.gz') else open
        read_bytes = 0
        source_rows = []
        with opener(path, 'rb') as stream:
            while True:
                line = stream.readline(self.config.max_source_bytes - read_bytes + 1)
                if not line:
                    break
                read_bytes += len(line)
                if read_bytes > self.config.max_source_bytes:
                    raise ValueError('decompressed_sample_size_limit_no_truncation')
                if not line.strip():
                    continue
                article = json.loads(line)
                if not isinstance(article, dict):
                    raise ValueError('sample_jsonl_requires_article_objects')
                source_rows.append(article)
        for article in source_rows:
            if self.config.article_ids is not None and article.get('article_id') not in self.config.article_ids and article.get('pmcid') not in self.config.article_ids:
                continue
            rank = json_digest({'sample_seed': self.config.sample_seed, 'article_id': article.get('article_id')})
            selected.append((rank, article)); selected.sort(key=lambda row: row[0])
            if len(selected) > self.config.max_articles:
                selected.pop()
        articles = [a for _, a in selected]
        if len({a.get('article_id') for a in articles}) != len(articles):
            raise ValueError('sample_has_duplicate_article_versions')
        write_json(self.output / 'run-config.json', {'config': self.config.model_dump(), 'arm': self.arm,
            'context': self.context, 'input_scope': self.input_scope, 'seed': self.config.seed,
            'frozen_rosters': str(frozen_rosters) if frozen_rosters else None, 'discovery_only': discovery_only,
            'replicas': len(self.clients), 'input_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'selected_article_ids': [a.get('article_id') for a in articles],
            'selection': 'lowest deterministic seed/article identity hashes; bounded sample, no population inference'})
        source_rows_by_id = {a['article_id']: a for a in articles}
        eligible = []
        for index, article in enumerate(articles):
            errors = source_gate(article, self.config)
            acquisition_license = article.get('license')
            if not errors:
                article = {**article,'license':recheck_license(acquisition_license)}
                source_rows_by_id[article['article_id']] = article
            row = {'article_id': article.get('article_id'), 'source_xml_sha256': article.get('xml_sha256'),
                   'source_license': article.get('license'), 'acquisition_license':acquisition_license,
                   'status': 'source_rejected' if errors else 'pending', 'errors': errors}
            self.article_rows.append(row)
            if not errors:
                eligible.append((index, article, row))
        frozen = read_frozen_rosters(frozen_rosters, articles) if frozen_rosters else None
        cached = None
        if cached_rosters:
            if frozen is not None: raise ValueError('Choose reviewed or cached generated rosters')
            saved = json.loads(Path(cached_rosters).read_text())
            if saved.get('schema_version') != 'predicted-rosters/1': raise ValueError('Unknown cached roster format')
            cached = {r['article_id']:r for r in saved['articles']}
            if len(cached) != len(saved['articles']) or set(cached) != {a['article_id'] for a in articles}:
                raise ValueError('Cached rosters must cover exactly the selected articles')
            for a in articles:
                if cached[a['article_id']]['text_sha256'] != a['text_sha256']:
                    raise ValueError('Cached roster source hash changed')
        async def discover(index, article, row):
            replica = index % len(self.clients)
            if frozen is not None:
                roster = {'task': 'roster', 'identity': {'article_id': article['article_id']},
                    'status': 'valid', 'data': frozen[article['article_id']], 'errors': [], 'attempts': [],
                    'origin': 'frozen_source_checked_roster', 'excluded_from_generated_task_metrics': True}
            elif cached is not None:
                saved = cached[article['article_id']]
                data = check_patient_roster(saved['roster'],article) if saved['status']=='valid' else None
                roster = {'task':'roster','identity':{'article_id':article['article_id']},
                    'status':saved['status'],'data':data,'errors':[], 'attempts':[],
                    'origin':'cached_generated_unreviewed_roster','excluded_from_generated_task_metrics':True}
            else:
                quarantine = []
                def roster_checker(value):
                    nonlocal quarantine
                    if self.config.isolate_secondary_cases:
                        checked, quarantine = isolate_cited_cases(value, article)
                        return checked
                    return check_patient_roster(value, article)
                roster = await self.call('roster', discovery_messages(article,
                    refined=self.config.refinement_policy == 'source_aware',isolated=self.config.isolate_secondary_cases),
                    roster_checker, {'article_id': article['article_id']}, replica, article['segments'])
                if self.config.isolate_secondary_cases:
                    roster['secondary_case_quarantine'] = quarantine
                    # Re-save the audited result alongside its original raw attempts.
                    key = json_digest({'identity':roster['identity'],'task':'roster','messages':safe_messages(
                        discovery_messages(article,refined=self.config.refinement_policy=='source_aware',isolated=True)+
                        ([{'role':'user','content':'ADDITIONAL TASK INSTRUCTIONS:\n'+self.config.prompt_overrides['roster']}]
                         if 'roster' in self.config.prompt_overrides else []))})
                    write_json(self.output/'tasks'/(key+'.json'),roster)
            row.update(roster=roster, status='roster_failed' if roster['status'] != 'valid' else 'roster_valid')
            return index, article, row
        discovered = await asyncio.gather(*(discover(*item) for item in eligible))
        patients_remaining = self.config.max_patients
        pixel_rows = self.image_rows(image_manifest or self.config.image_manifest)
        scheduled = []
        for index, article, row in ([] if discovery_only else discovered):
            result = row['roster']
            if result['status'] != 'valid':
                continue
            roster = result['data']
            chosen = roster['patients'][:min(self.config.max_patients_per_article, patients_remaining)]
            patients_remaining -= len(chosen)
            row['omitted_patient_ids_due_to_cap'] = [p['patient_id'] for p in roster['patients'] if p not in chosen]
            scheduled.append((index, article, row, chosen))
        async def extract_article(index, article, row, chosen):
            result = row['roster']
            roster = result['data']; replica = index % len(self.clients)
            if self.config.experimental_pipeline == 'evidence_ledger' and chosen:
                from .ledger_pipeline import prepare_source_map
                self.source_ledgers[article['article_id']] = await prepare_source_map(self,article,roster,replica)
            fig_results = await asyncio.gather(*(self.call('figure_attribution', figure_messages(article, roster, figure['figure_key']),
                lambda value, fid=figure['figure_key']: validate_figure_review(value, article, roster, fid),
                {'article_id': article['article_id'], 'figure_id': figure['figure_key']}, replica, article['segments'])
                for figure in article['figures'][:self.config.max_figures_per_article]))
            reviews = [bind_figure_review(r['data'], article, roster, method='text_only_caption_and_article')
                       for r in fig_results if r['status'] == 'valid']
            row['figure_tasks'] = fig_results
            row['unreviewed_figure_ids'] = [f['figure_key'] for f in article['figures'] if f['figure_key'] not in {r['data']['figure_id'] for r in reviews}]
            await asyncio.gather(*(self.visual(pixel, article, roster, replica) for pixel in pixel_rows if pixel['article_id'] == article['article_id']))
            bundles = await asyncio.gather(*(self.patient(article, roster, p, reviews, replica) for p in chosen))
            row['patient_records'] = [p['source']['record_id'] for p in bundles]
            row['all_requested_tasks_valid'] = (all(p['complete_for_scope'] for p in bundles) and not row['omitted_patient_ids_due_to_cap']
                and all(r['status'] == 'valid' for r in fig_results) and not row['unreviewed_figure_ids']
                and all(r['status'] == 'valid' for r in self.visual_rows if r['identity']['article_id'] == article['article_id']))
            row['status'] = ('no_individual_patients' if not roster['patients'] and roster['disposition'] in {'aggregate_only', 'no_patient_data'}
                else 'unresolved_roster' if roster['disposition'] != 'individual_cases'
                else 'review_required' if row['all_requested_tasks_valid'] else 'incomplete')
            row['semantic_review'] = 'unreviewed'
        await asyncio.gather(*(extract_article(*item) for item in scheduled))
        self.patients.sort(key=lambda p: p['source']['record_id'])
        with (self.output / 'patients.jsonl').open('w', encoding='utf-8') as stream:
            for patient in self.patients:
                stream.write(json.dumps(patient, ensure_ascii=False) + '\n')
        write_json(self.output / 'visual-annotations.json', self.visual_rows)
        wall = time.monotonic() - started
        report = {'schema_version': 'pilot-extraction-report/1', 'arm': self.arm,
            'experimental_pipeline':self.config.experimental_pipeline,
            'model': {'model_id': self.config.model_id, 'revision': self.config.revision},
            'selected_articles': len(articles), 'eligible_articles': len(eligible),
            'input_scope': self.input_scope, 'seed': self.config.seed, 'discovery_only': discovery_only,
            'conditioned_on_frozen_rosters': frozen is not None,
            'conditioned_on_cached_generated_rosters': cached is not None,
            'roster_scores_are_independent_of_clinical_extraction': frozen is not None,
            'outputs': {'patients': str(self.output / 'patients.jsonl'),
                'report': str(self.output / 'report.json'), 'attempts': str(self.output / 'attempts.jsonl'),
                'patient_review_forms': str(self.output / 'review'),
                'visual_annotations': str(self.output / 'visual-annotations.json')},
            'patients_extracted': len(self.patients), 'model_calls': self.calls,
            'measurement_comparison': dict(Counter(m['status'] for p in self.patients
                for m in p['observation_measurements'])),
            'clinical_review_signals': {
                'inventory_features':sum(len((p['experimental_reviews'].get('clinical_inventory') or {}).get('data',{}).get('features',[]))
                    for p in self.patients if (p['experimental_reviews'].get('clinical_inventory') or {}).get('data')),
                'claim_decisions':dict(Counter(d['decision'] for p in self.patients
                    for a in p['experimental_reviews']['claim_audits'] if a['data'] for d in a['data']['decisions'])),
                'coverage_decisions_before_backfill':dict(Counter(d['status'] for p in self.patients
                    for a in p['experimental_reviews']['coverage_audits'] if a['data'] for d in a['data']['decisions'])),
                'possible_missing_facts':sum(len(a['data']['possible_missing_facts']) for p in self.patients
                    for a in p['experimental_reviews']['claim_audits'] if a['data']),
                'unlinked_clinical_facts':sum(not f['event_ids'] for p in self.patients for f in p['patient_state_index']['facts']),
                'model_judgments_are_not_accuracy_gold':True},
            'relative_timelines': {'patients': sum('relative_timeline' in p for p in self.patients),
                'events': sum(len(p.get('relative_timeline', {}).get('events', [])) for p in self.patients),
                'ordered_pairs': sum(len(p.get('relative_timeline', {}).get('before_pairs', [])) for p in self.patients),
                'incomparable_pairs': sum(len(p.get('relative_timeline', {}).get('incomparable_pairs', [])) for p in self.patients),
                'clinical_sequence_verified': False},
            'concurrency': {'configured_per_endpoint': self.config.concurrency_per_endpoint,
                            'observed_peak_completions_per_endpoint': self.peak_active},
            'valid_tasks': sum(r['status'] == 'valid' for r in self.results), 'task_count': len(self.results),
            'task_statuses': dict(Counter(r['status'] for r in self.results)),
            'task_statuses_by_domain': {name: dict(Counter(r['status'] for r in self.results if r['task'] in tasks))
                for name, tasks in {'clinical': set(TASK_MODELS), 'summary': {'summary'}, 'timeline': {'timeline_v2'},
                    'patient_discovery': {'roster'}, 'caption_ownership': {'figure_attribution'},
                    'pixel_description': {'figure_visuals'}, 'pixel_ownership': {'pixel_attribution'},
                    'joint_pixels':{'joint_figure'}, 'order_review':{'ordering_review'},
                    'clinical_inventory':{'clinical_inventory'}, 'coverage_audit':{'coverage_audit'},
                    'coverage_repair':{'coverage_repair_'+k for k in TASK_MODELS},
                    'claim_audit':{'claim_audit'}, 'timeline_completion':{'timeline_completion'},
                    'summary_completion':{'summary_completion'}}.items()},
            'source_span_recovery': {'resolved': sum(len(a.get('span_resolution', {}).get('resolved', []))
                for r in self.results for a in r['attempts']),
                'mechanical_changes': sum(len(a.get('span_resolution', {}).get('recoveries', []))
                    for r in self.results for a in r['attempts']),
                'unresolved': sum(len(a.get('span_resolution', {}).get('unresolved', []))
                    for r in self.results for a in r['attempts']), 'clinical_content_changed': False},
            'first_attempt_valid_tasks': sum(bool(r['attempts'] and not r['attempts'][0]['errors']) for r in self.results),
            'articles': self.article_rows, 'wall_seconds': wall, 'tokens': self.token_report(articles, wall),
            'clinical_accuracy_verified': False, 'semantic_coverage_verified': False,
            'physician_review': 'pending; review forms contain immutable source quotes and hashes',
            'pixel_figures_requested': len(pixel_rows), 'pixel_figures_inspected': sum(r['pixels_inspected'] for r in self.visual_rows),
            'limitations': ['Schema and literal evidence validity do not establish medical accuracy or completeness.',
                'Patient discovery and figure ownership are unreviewed model claims.',
                'No source/image downloads, GPU launch, synthetic EHR events, or terminology guesses.',
                'Supplements are manifests only; missed patients/facts require independent source review.',
                'Direct and targeted runs are independent arms, not blinded accuracy scores.',
                'Source-based first prompts match across arms; temporal graph fact registries reflect each arm output.']}
        # Export discovery for scoring in every extraction run.
        predicted = {'schema_version': 'predicted-rosters/1', 'articles': [
            {'article_id': a['article_id'], 'text_sha256': source_rows_by_id[a['article_id']]['text_sha256'],
             'status': a['roster']['status'], 'roster': a['roster']['data']}
            for a in self.article_rows if a.get('roster')]}
        write_json(self.output / 'rosters.json', predicted)
        report['outputs']['predicted_rosters'] = str(self.output / 'rosters.json')
        write_json(self.output / 'report.json', report)
        return report


async def run_pilot(config, articles_path, output, endpoints, context, arm_name, *, image_manifest=None, http=None,
                    frozen_rosters=None, cached_rosters=None, input_scope='whole_article', seed=None):
    """Run a fresh bounded arm on an already downloaded JSONL(.gz) sample."""
    runner = PilotRunner(config, output, endpoints, context, arm_name, http=http, input_scope=input_scope, seed=seed)
    try:
        return await runner.run(articles_path, image_manifest=image_manifest, frozen_rosters=frozen_rosters, cached_rosters=cached_rosters)
    finally:
        await runner.close()


async def prepare_rosters(config, articles_path, output, endpoints, context, *, seed=42, http=None):
    """Evaluate live discovery alone; it does not gate the frozen-roster benchmark."""
    runner = PilotRunner(config, output, endpoints, context, 'targeted', seed=seed, http=http)
    try:
        report=await runner.run(articles_path, discovery_only=True)
        predicted=json.loads(Path(report['outputs']['predicted_rosters']).read_text())
        expected={a['article_id'] for a in report['articles']}
        actual=[a['article_id'] for a in predicted['articles']]
        if not expected or set(actual)!=expected or len(actual)!=len(expected):
            rejected=[{'article_id':a['article_id'],'errors':a['errors']} for a in report['articles'] if a['status']=='source_rejected']
            raise ValueError('Discovery cannot provide complete cached source coverage: '+json.dumps(rejected))
        return report
    finally:
        await runner.close()
