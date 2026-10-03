"""Patient identity discovery and auditable selection of immutable source blocks.

Figure ownership has its own task and cannot invalidate a patient roster. The
compact view keeps unresolved blocks, all tables and captions, and never rewrites
the source or assumes that a shared block belongs to a single patient.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .article_tasks import Roster, SYSTEM, check_article_task


def minimal_segments(segments):
    return [{k: s[k] for k in ('segment_id', 'kind', 'heading', 'text') if k in s}
            for s in segments]


def discovery_messages(article):
    schema = copy.deepcopy(Roster.model_json_schema())
    schema['properties'].pop('figures')
    schema['required'] = [k for k in schema['required'] if k != 'figures']
    source = {'article_id': article['article_id'], 'title': article.get('title'),
              'segments': minimal_segments(article['segments'])}
    instruction = (
        'Identify individually described patients in ALL supplied blocks. Human and animal patients are allowed. '
        'A cohort size, group mean, generic diagram or anonymous dashboard bed is not an individual case. '
        'Include a previously published patient only if this article itself reports an individual clinical course; '
        'flag prior publication in attribution_limitations, and do not merge across articles. '
        'Relatives are separate patients only if independently described. Preserve mother/fetus and comparison '
        'patient identity. Assign blocks to patients, background or unresolved. Shared/mixed blocks may be '
        'assigned to multiple patients with explicit limitations. Include case facts in Discussion. '
        'Use exact distinguishing identity quotes, literal segment IDs, unknown species if unstated, and null '
        'count if uncertain. Do not invent individuals to cover blocks. '
        'This task ONLY identifies patients and source blocks. Figure/panel ownership is evaluated separately; '
        'do not output a figures field. Captions can support patient identity when explicitly linked. '
        'Return the complete JSON object, no prose or markdown. Schema validity does not establish completeness.')
    return [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content':
        'SOURCE_JSON:\n' + json.dumps(source, ensure_ascii=False) + '\nTASK:\n' + instruction +
        '\nSCHEMA:\n' + json.dumps(schema)}]


def check_patient_roster(value, article):
    if not isinstance(value, dict):
        raise ValueError('Patient roster must be an object')
    candidate = copy.deepcopy(value)
    # Even a legacy/malformed figure decision is independent of identity. Its
    # original response remains in the immutable attempt ledger.
    candidate['figures'] = [{'figure_id': f['figure_key'], 'panel': None,
        'patient_ids': [], 'scope': 'unresolved', 'evidence': []} for f in article['figures']]
    return check_article_task('roster', candidate, article)


def frozen_rosters(path, articles):
    value = json.loads(Path(path).read_text())
    if value.get('schema_version') != 'fixed-rosters/1' or not isinstance(value.get('articles'), list):
        raise ValueError('Unknown frozen roster format')
    rows = {r['article_id']: r for r in value['articles']}
    if len(rows) != len(value['articles']) or set(rows) != {a['article_id'] for a in articles}:
        raise ValueError('Frozen rosters must cover exactly the selected articles without duplicates')
    result = {}
    for article in articles:
        row = rows[article['article_id']]
        if row.get('text_sha256') != article['text_sha256'] or row.get('review_status') != 'source_checked':
            raise ValueError('Frozen roster source hash/review marker changed')
        result[article['article_id']] = check_patient_roster(row['roster'], article)
    return result


def patient_view(article, roster, patient):
    selected = set(patient['source_segment_ids']) | set(roster['unresolved_segment_ids'])
    selected.update(c['segment_id'] for c in patient['identity_evidence'])
    # Table context and captions are never implicitly reduced to a single owner.
    selected.update(s['segment_id'] for s in article['segments']
                    if (s.get('kind') == 'table' or s.get('kind', '').startswith('table_')
                        or s.get('kind') == 'figure_caption'))
    for figure in article['figures']:
        selected.update(figure.get('caption_segment_ids', []))
    ids = {s['segment_id'] for s in article['segments']}
    if selected - ids:
        raise ValueError('Compact context references an unknown canonical segment')
    kept = [s for s in article['segments'] if s['segment_id'] in selected]
    if not kept:
        raise ValueError('Compact patient context is empty')
    view = {**article, 'segments': minimal_segments(kept)}
    target = {**patient, 'source_segment_ids': [s['segment_id'] for s in kept]}
    audit = {'policy': 'immutable-patient-sections/1', 'source_text_sha256': article['text_sha256'],
             'retained_segment_ids': [s['segment_id'] for s in kept],
             'omitted_segment_ids': [s['segment_id'] for s in article['segments'] if s['segment_id'] not in selected],
             'retained_text_sha256': hashlib.sha256('\n\n'.join(s['text'] for s in kept).encode()).hexdigest(),
             'tables_and_captions_retained': True, 'unresolved_blocks_retained': True,
             'semantic_coverage_verified': False}
    return view, target, audit
