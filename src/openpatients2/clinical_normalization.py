"""Small, reversible field repairs; no new clinical facts or inferred timing."""
from __future__ import annotations

from copy import deepcopy
import re


def normalize_clinical(task, candidate, segments):
    """Quarantine unbound times and copy explicit medication routes into fields.

    A literal quote is lexical support, not entailment or patient adjudication.
    Clearing an invalid association preserves its original value in the audit.
    It never derives another encounter/date from nearby text.
    """
    result = deepcopy(candidate); audit = []
    texts = {s['segment_id']: s['text'] for s in segments}

    def visit(obj, path=''):
        if isinstance(obj, list):
            for i, child in enumerate(obj): visit(child, path + '/' + str(i))
        elif isinstance(obj, dict):
            evidence = obj.get('evidence')
            bound = isinstance(evidence, list) and bool(evidence) and all(
                isinstance(e, dict) and isinstance(e.get('quote'), str) and e['quote'] and
                (e['quote'] in texts.get(e.get('source_section'), '') if e.get('source_section') in texts
                 else any(e['quote'] in text for text in texts.values())) for e in evidence)
            quotes = '\n'.join(e['quote'] for e in evidence) if bound else ''
            time = obj.get('time')
            if bound and isinstance(time, dict) and (
                    (time.get('text') and time['text'] not in quotes) or
                    (time.get('date_iso') and (time['date_iso'] not in quotes or
                                              time['date_iso'] not in (time.get('text') or '')))):
                unknown = {'text': None, 'relation': 'unknown', 'anchor': None, 'date_iso': None}
                audit.append({'pointer': path + '/time', 'before': deepcopy(time), 'after': unknown,
                    'reason': 'time_not_bound_to_own_literal_evidence', 'status': 'quarantined_association',
                    'clinical_entailment_verified': False})
                obj['time'] = unknown
            if task == 'medications' and bound and obj.get('route') is None:
                name = obj.get('name') or ''
                match = re.match(r'^(IV|intravenous|oral|subcutaneous|intramuscular)\s+(.+)$', name, re.I)
                if match and re.search(re.escape(match.group(0)), quotes, re.I):
                    route = {'iv':'IV'}.get(match.group(1).lower(), match.group(1).lower())
                    obj['route'] = route
                    audit.append({'pointer': path + '/route', 'before': None, 'after': route,
                        'reason': 'explicit_route_in_name_and_own_source_quote',
                        'clinical_entailment_verified': False})
            for key, child in list(obj.items()):
                if key != 'evidence': visit(child, path + '/' + key)
    visit(result)
    return result, audit
