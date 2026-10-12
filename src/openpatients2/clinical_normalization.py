"""Small, reversible field repairs; no new clinical facts or inferred timing."""
from __future__ import annotations

from copy import deepcopy
import json
import re

from .semantic_rules import date_iso_errors


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
                    (time.get('date_iso') and date_iso_errors(time['date_iso'], time.get('text'), quotes))):
                unknown = {'text': None, 'relation': 'unknown', 'anchor': None, 'date_iso': None}
                audit.append({'pointer': path + '/time', 'before': deepcopy(time), 'after': unknown,
                    'reason': 'time_not_bound_to_own_literal_evidence', 'status': 'quarantined_association',
                    'clinical_entailment_verified': False})
                obj['time'] = unknown
            elif isinstance(time, dict) and time.get('anchor') and not any(
                    time['anchor'].casefold() in text.casefold() for text in texts.values()):
                # TIME-LITERAL: an anchor is a literal source phrase or null.
                after = {**time, 'anchor': None}
                audit.append({'pointer': path + '/time/anchor', 'before': time['anchor'], 'after': None,
                    'reason': 'time_anchor_not_literal_in_source', 'status': 'quarantined_association',
                    'clinical_entailment_verified': False})
                obj['time'] = after
            if (task == 'demographics' and bound and str(obj.get('attribute') or '').startswith('age_')
                    and isinstance(obj.get('text_value'), str) and obj['text_value'] not in quotes):
                # DEMO-AGE-TEXT: the literal phrase is copied from the quote verbatim (Unicode hyphens,
                # case and spacing preserved); only a case/hyphen/space-equivalent span is accepted.
                from .semantic_rules import literal_span
                exact = literal_span(obj['text_value'], quotes)
                if exact is not None:
                    audit.append({'pointer': path + '/text_value', 'before': obj['text_value'], 'after': exact,
                        'reason': 'age_text_copied_verbatim_from_own_evidence', 'clinical_entailment_verified': False})
                    obj['text_value'] = exact
            if task == 'medications' and bound and obj.get('route') is None:
                name = obj.get('name') or ''
                match = re.match(r'^(IV|intravenous|oral|subcutaneous|intramuscular)\s+(.+)$', name, re.I)
                if match and re.search(re.escape(match.group(0)), quotes, re.I):
                    # MED-ROUTE: keep the route exactly as written in the quote ('intravenous' stays
                    # 'intravenous', 'IV' stays 'IV'); the reference annotations do the same.
                    literal = re.search(re.escape(match.group(1)) + r'(?=\s+' + re.escape(match.group(2)) + ')', quotes, re.I)
                    route = literal.group(0) if literal else match.group(1)
                    obj['route'] = route
                    audit.append({'pointer': path + '/route', 'before': None, 'after': route,
                        'reason': 'explicit_route_in_name_and_own_source_quote',
                        'clinical_entailment_verified': False})
                elif name and not re.search(r'[\n\r]',name):
                    # Require direct attachment to THIS named drug. A route
                    # elsewhere in a paragraph may belong to another drug.
                    attached = re.findall(r'\b(IV|intravenous|oral|subcutaneous|intramuscular)\s+'
                        +re.escape(name)+r'\b',quotes,re.I)
                    # MED-ROUTE: one literal spelling only; differently written routes stay unresolved.
                    routes = {word.casefold(): word for word in attached}
                    if len(routes)==1:
                        route=routes.popitem()[1];obj['route']=route
                        audit.append({'pointer':path+'/route','before':None,'after':route,
                            'reason':'explicit_route_directly_attached_to_named_drug_in_own_evidence',
                            'clinical_entailment_verified':False})
            for key, child in list(obj.items()):
                if key != 'evidence': visit(child, path + '/' + key)
    visit(result)
    # ONE-STATEMENT: an item repeated verbatim within a section (same fields; evidence order ignored) is one fact.
    for collection in ('items', 'tumors', 'biomarkers', 'treatments'):
        rows = result.get(collection) if isinstance(result, dict) else None
        if not isinstance(rows, list):
            continue
        kept, seen = [], {}
        for i, item in enumerate(rows):
            key = _item_key(item)
            if key is not None and key in seen:
                audit.append({'pointer': f'/{collection}/{i}', 'before': deepcopy(item), 'after': None,
                    'reason': f'exact_duplicate_of_/{collection}/{seen[key]}', 'status': 'removed_duplicate',
                    'clinical_entailment_verified': False})
                continue
            if key is not None:
                seen[key] = i
            kept.append(item)
        result[collection] = kept
    return result, audit


def _item_key(item):
    if not isinstance(item, dict):
        return None
    evidence = item.get('evidence')
    rest = {k: v for k, v in item.items() if k != 'evidence'}
    try:
        quotes = sorted(json.dumps(e, sort_keys=True, ensure_ascii=False) for e in evidence or [])
        return json.dumps(rest, sort_keys=True, ensure_ascii=False) + '\x00' + '\x00'.join(quotes)
    except TypeError:
        return None
