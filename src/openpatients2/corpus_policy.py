"""Research corpus triage. Priority is an uncalibrated heuristic, never eligibility."""
from __future__ import annotations

import re
from .articles import recheck_license
from .article_tasks import check_article_task
from .provenance import json_digest


POLICY = 'patient-corpus-triage/1'


def prioritize_article(article: dict, roster: dict | None = None) -> dict:
    """Accept the canonical parse_article output, optionally with a grounded roster.

    No length, human-species, positive-outcome, English-only, or case-report-type
    hard cutoff. Articles without obvious triggers remain in an exploration queue.
    """
    rights = recheck_license(article.get('license', {}))
    article_id = article['article_id']
    signals = []
    title = str(article.get('title') or '')
    article_type = str(article.get('article_type') or '')
    headings = ' '.join(str(s.get('heading') or '') for s in article.get('segments', []))
    text = '\n'.join(s['text'] for s in article.get('segments', []))
    if re.search(r'case[ -](?:report|series)|clinical (?:case|reasoning)|clinicopatholog', title+' '+article_type, re.I):
        signals.append('case_focused_title_or_type')
    if re.search(r'case (?:presentation|report|description)|patient [1-9]|case [1-9]', headings, re.I):
        signals.append('individual_case_heading')
    if re.search(r'follow[ -]?up|readmi(?:ssion|tted)|postoperative|hospital day|weeks? later', text, re.I):
        signals.append('possible_longitudinal_detail')
    if any(s.get('kind', '').startswith('table') for s in article.get('segments', [])):
        signals.append('tables_available')
    if article.get('figures'):
        signals.append('figures_available')
    result = {'policy': POLICY, 'article_id': article_id, 'source_digest': article.get('xml_sha256'),
              'signals': signals, 'release_lane': rights.get('release_lane'),
              'heuristic_is_not_medical_quality': True, 'species_filter': 'none',
              'fresh_metadata_recheck_required_before_release': True}
    if not rights.get('allowed'):
        return {**result, 'route': 'quarantine', 'reason': rights.get('reason', 'unverified_rights')}
    if not article.get('segments'):
        return {**result, 'route': 'quarantine', 'reason': 'no_usable_full_text'}
    if roster is not None:
        checked = check_article_task('roster', roster, article)
        if checked['disposition'] == 'individual_cases':
            return {**result, 'route': 'extract_individuals', 'record_ids': [article_id+':'+p['patient_id'] for p in checked['patients']],
                    'roster_review_required': not checked['roster_complete'] or bool(checked['unresolved_segment_ids']),
                    'attribution_semantically_verified': False}
        if checked['disposition'] == 'uncertain':
            return {**result, 'route': 'roster_review', 'reason': 'individual_patient_identity_uncertain'}
        return {**result, 'route': 'context_only', 'reason': checked['disposition'],
                'excluded_individuals_audit_required': True}
    high = {'case_focused_title_or_type', 'individual_case_heading'} & set(signals)
    return {**result, 'route': 'priority_screen' if high else 'exploration_screen',
            'reason': 'screen_full_text_for_original_individual_cases',
            'exploration_sample_key': json_digest({'article': article_id, 'policy': POLICY})}
