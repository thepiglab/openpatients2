"""Experimental source-only article decisions and literal evidence contracts.

No model calls, routing, thresholding, or training happen in this module.
Model-specific adapters must preserve their native template/readout and record
raw probabilities. Calibration and semantic evidence review are separate.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
import yaml


CATALOG = Path(__file__).with_name('rules') / 'article_profile_v1.yaml'
UNKNOWN = 'insufficient_evidence'


def catalog():
    return yaml.safe_load(CATALOG.read_text())


def source_state(article, *, scope):
    """Allowlist source fields; never send reference labels/rosters to inference."""
    if scope not in {'title_abstract', 'full_text'}:
        raise ValueError('scope must be title_abstract or full_text')
    segments = []
    seen = set()
    for row in article['segments']:
        sid, content = row['segment_id'], row['text']
        if not isinstance(sid, str) or not sid or sid in seen or not isinstance(content, str):
            raise ValueError('Source segments need unique string IDs and literal text')
        seen.add(sid)
        if scope == 'title_abstract' and not re.match(r'^abstract(?:$|[ /:>])', (row.get('heading') or '').strip(), re.I):
            continue
        segments.append({k: row[k] for k in ('segment_id', 'text', 'kind', 'heading') if k in row})
    return {'article_id': article['article_id'], 'title': article.get('title', ''),
            'input_scope': scope, 'segments': segments}


def source_digest(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def questions(field_ids=None):
    spec = catalog()
    fields = spec['fields']
    selected = list(fields) if field_ids is None else list(field_ids)
    if not selected or len(selected) != len(set(selected)) or set(selected) - fields.keys():
        raise ValueError('Choose nonempty, unique catalog field IDs')
    result = {}
    abstention = {UNKNOWN: 'The supplied source does not establish an answer.',
                  'not_applicable': 'The question is inapplicable to this source.'}
    for name in selected:
        field = fields[name]
        instruction = spec['policy'] + ' ' + field['description']
        if field['mode'] == 'choice':
            result[name] = {'type': 'choice', 'instructions': instruction,
                            'criteria': {**field['options'], **abstention}}
        else:
            labels = field.get('options', {'present': field['description']})
            for label, description in labels.items():
                key = name if field['mode'] == 'flag' else name + '.' + label
                result[key] = {'type': 'choice', 'instructions': instruction + ' Assess: ' + description,
                    'criteria': {'yes': 'Affirmatively supported by this source.',
                                 'no': 'Contradicted or explicitly absent in this source; silence is insufficient.',
                                 **abstention}}
    return result


def prepare(article, *, scope='title_abstract', field_ids=None):
    state = source_state(article, scope=scope)
    return {'schema_version': catalog()['version'], 'catalog_sha256': hashlib.sha256(CATALOG.read_bytes()).hexdigest(),
            'source_sha256': source_digest(state), 'request': {'state': state, 'questions': questions(field_ids)},
            'notice': 'Uncalibrated research requests. Preserve model-specific templates; no automatic article exclusion.'}


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Span(Strict):
    segment_id: str
    segment_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)


class Decision(Strict):
    label: str
    probabilities: dict[str, float]
    evidence: list[Span]


class Provenance(Strict):
    model_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    serving_revision: str = Field(min_length=1)
    template_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    calibration_id: str | None


class ArticleProfile(Strict):
    schema_version: Literal['article-profile/1']
    article_id: str
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    catalog_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    input_scope: Literal['title_abstract', 'full_text']
    model: Provenance
    decisions: dict[str, Decision]
    elapsed_seconds: float = Field(gt=0, allow_inf_nan=False)
    input_tokens: int = Field(ge=0)
    semantic_review: Literal['unreviewed']


def validate_record(value, prepared):
    """Validate options/probabilities and exact source spans, never entailment."""
    record = ArticleProfile.model_validate(value)
    state = prepared['request']['state']
    if (record.article_id != state['article_id'] or record.input_scope != state['input_scope']
            or record.source_sha256 != source_digest(state)
            or record.source_sha256 != prepared['source_sha256']
            or record.catalog_sha256 != prepared['catalog_sha256']):
        raise ValueError('Profile provenance does not match prepared source/catalog')
    expected = prepared['request']['questions']
    if set(record.decisions) != set(expected):
        raise ValueError('Every prepared decision must be present, including abstentions')
    segments = {s['segment_id']: s['text'] for s in state['segments']}
    # Title can also be selected as a literal supporting span.
    if '__title__' in segments:
        raise ValueError('Reserved title segment ID')
    segments['__title__'] = state['title']
    for key, answer in record.decisions.items():
        allowed = expected[key]['criteria']
        p = answer.probabilities
        if set(p) != set(allowed) or answer.label not in p:
            raise ValueError('Unknown/missing decision labels: ' + key)
        if any(not math.isfinite(v) or not 0 <= v <= 1 for v in p.values()) or not math.isclose(sum(p.values()), 1, abs_tol=1e-5):
            raise ValueError('Invalid probability distribution: ' + key)
        if p[answer.label] < max(p.values()):
            raise ValueError('Selected label must maximize the reported distribution: ' + key)
        if answer.label not in {UNKNOWN, 'not_applicable'} and not answer.evidence:
            raise ValueError('Non-abstaining metadata needs selected source evidence: ' + key)
        for span in answer.evidence:
            text = segments.get(span.segment_id)
            if (text is None or hashlib.sha256(text.encode()).hexdigest() != span.segment_sha256
                    or not 0 <= span.start < span.end <= len(text)
                    or text[span.start:span.end] != span.quote):
                raise ValueError('Nonliteral or stale evidence span: ' + key)
    return record.model_dump()


def evidence_candidates(state):
    """Stable whole-segment candidates; a model chooses IDs, code copies spans.

    This is a candidate-generation baseline, not token-level span extraction.
    Batch/rerank long candidate lists and include an abstention option. Never
    interpret an attention map or selected quote as proven semantic support.
    """
    result = {}
    rows = [{'segment_id': '__title__', 'text': state['title']}, *state['segments']]
    for row in rows:
        if row['segment_id'] in result:
            raise ValueError('Duplicate evidence candidate ID')
        text = row['text']
        if text:
            result[row['segment_id']] = {'segment_id': row['segment_id'],
                'segment_sha256': hashlib.sha256(text.encode()).hexdigest(),
                'start': 0, 'end': len(text), 'quote': text}
    return result


def result_schema(field_ids=None):
    """JSON Schema with fixed labels for every requested decision."""
    schema = ArticleProfile.model_json_schema()
    properties = {}
    for key, question in questions(field_ids).items():
        labels = list(question['criteria'])
        properties[key] = {'allOf': [{'$ref': '#/$defs/Decision'}, {'properties': {
            'label': {'enum': labels},
            'probabilities': {'type': 'object', 'additionalProperties': False,
                'required': labels, 'properties': {label: {'type': 'number', 'minimum': 0, 'maximum': 1} for label in labels}}}}]}
    schema['properties']['decisions'] = {'type': 'object', 'additionalProperties': False,
                                         'required': list(properties), 'properties': properties}
    return schema
