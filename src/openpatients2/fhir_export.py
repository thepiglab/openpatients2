"""Deterministic, evidence-gated FHIR R4 compilation of unified article rows.

No generated FHIR JSON, fabricated dates, diagnoses, clinician authorship or
inferred encounters. Unmapped facts remain in the extraction DocumentReference
and mapping ledger. A structural pass never claims clinical correctness.
"""
import base64
from copy import deepcopy
import json
import uuid

from .provenance import json_digest

BASE = 'https://github.com/thepiglab/openpatients2/fhir'
NAMESPACE = uuid.UUID('37c0831b-dfe4-583e-85d1-9a07536e7349')
VERSION = 'openpatients2-fhir-r4/1'


def stable_id(*parts):
    return str(uuid.uuid5(NAMESPACE, json.dumps(parts, ensure_ascii=False)))


def cc(text, coding=None):
    return {'text': str(text), **({'coding': coding if isinstance(coding, list) else [coding]} if coding else {})}


def code(system, value):
    return {'coding': [{'system': system, 'code': value}]}


def contains_codings(value, expected):
    if isinstance(value, dict):
        return value.get('coding') == expected or any(contains_codings(v, expected) for v in value.values())
    if isinstance(value, list):
        return any(contains_codings(v, expected) for v in value)
    return False


def extension(name, value, kind='String'):
    return {'url': BASE + '/StructureDefinition/' + name, 'value' + kind: value}


def fact_entries(article, patient):
    for task, section in patient['sections'].items():
        if not isinstance(section, dict):
            continue
        for collection in ('items', 'tumors', 'biomarkers', 'treatments'):
            for i, fact in enumerate(section.get(collection, [])):
                # Identity binds content as well as the patient and task. Same
                # content reordered in an array keeps its ID; edits get new IDs.
                key = json_digest({'article_id': article['article_id'], 'patient_id': patient['patient_id'],
                                   'identity_evidence': patient.get('identity_evidence'),
                                   'task': task, 'collection': collection, 'fact': fact})
                yield {'fact_key': key, 'task': task, 'collection': collection,
                       'source_pointer': f'/sections/{task}/{collection}/{i}', 'fact': fact}


def term(entry):
    fact = entry['fact']
    return str(next((fact[k] for k in ('name', 'substance', 'attribute', 'action_text', 'event', 'text_value') if fact.get(k)), entry['task']))


def literal_evidence(fact, source):
    citations = fact.get('evidence', [])
    if not citations:
        return False
    segments = source['segments']
    for e in citations:
        quote = e.get('quote')
        if not isinstance(quote, str) or not quote:
            return False
        sid = e.get('segment_id', e.get('source_section'))
        named = [s for s in segments if s['segment_id'] == sid or s.get('heading') == sid and sid]
        if not any(quote in s['text'] for s in named or segments):
            return False
    return True


def map_fact(entry, patient_ref, coding=None):
    """Return a resource payload or an explicit reason to defer this fact."""
    f, task = entry['fact'], entry['task']
    assertion = f.get('assertion')
    if f.get('scoring') == 'review_only':
        return None, 'review_only_or_quarantined'
    from .schemas import TASK_MODELS
    from typing import get_args
    from pydantic import ValidationError
    model = TASK_MODELS.get(task)
    if model is None or entry['collection'] not in model.model_fields:
        return None, 'unsupported_source_schema'
    fact_model = get_args(model.model_fields[entry['collection']].annotation)[0]
    try:
        # Only annotation metadata may accompany the original fact schema.
        fact_model.model_validate({k: v for k, v in f.items() if k not in {'fact_id', 'scoring', 'certainty'}})
    except (ValidationError, ValueError):
        return None, 'invalid_source_fact_schema'
    if f.get('subject') != 'index_patient' and not (task == 'family_genetics' and f.get('kind') == 'family_condition'):
        return None, 'subject_is_not_index_patient'
    if assertion in {'conditional', 'unknown'}:
        return None, 'conditional_or_unknown_assertion'
    concept = cc(term(entry), coding)
    subject = {'reference': patient_ref}
    common = {'subject': subject}
    result = None
    if task == 'conditions':
        result = {'resourceType': 'Condition', **common, 'code': concept}
        certainty = f.get('verification_status', 'unknown')
        if assertion == 'absent':
            certainty = 'refuted'
        elif assertion == 'possible' and certainty in {'confirmed', 'refuted'}:
            return None, 'contradictory_assertion_and_certainty'
        elif assertion == 'possible' and certainty == 'unknown':
            certainty = 'unconfirmed'
        if certainty != 'unknown':
            result['verificationStatus'] = code('http://terminology.hl7.org/CodeSystem/condition-ver-status', certainty)
        if assertion == 'present' and f.get('clinical_status') not in {None, 'unknown'}:
            result['clinicalStatus'] = code('http://terminology.hl7.org/CodeSystem/condition-clinical', f['clinical_status'])
        if f.get('body_site'):
            result['bodySite'] = [cc(f['body_site'])]
    elif assertion != 'present':
        # Never turn negated drugs/procedures into administration/performed
        # resources, or a possible finding into a reported numeric result.
        return None, 'nonaffirmative_fact_requires_specialized_mapping'
    elif task == 'medications':
        action = f.get('action')
        if action in {'ordered', 'planned'}:
            result = {'resourceType': 'MedicationRequest', **common, 'status': 'unknown',
                      'intent': 'order' if action == 'ordered' else 'plan', 'medicationCodeableConcept': concept}
        else:
            status = {'current': 'active', 'held': 'on-hold', 'stopped': 'stopped', 'declined': 'not-taken'}.get(action, 'unknown')
            # started/administered/historical do not establish whether use is
            # still active or the course completed. Preserve action in source.
            result = {'resourceType': 'MedicationStatement', **common, 'status': status,
                      'medicationCodeableConcept': concept}
        dosage = ' ; '.join(str(f[k]) for k in ('dose_text', 'route', 'frequency', 'duration_text') if f.get(k))
        dose = {'text': dosage} if dosage else {}
        if f.get('dose_value') is not None:
            quantity = {'value': f['dose_value']}
            if f.get('dose_unit'):
                quantity['unit'] = f['dose_unit']
            dose['doseAndRate'] = [{'doseQuantity': quantity}]
        if f.get('route'):
            dose['route'] = cc(f['route'])
        if dose:
            result['dosageInstruction' if result['resourceType'] == 'MedicationRequest' else 'dosage'] = [dose]
    elif task == 'procedures_devices':
        if f.get('kind') == 'device':
            return None, 'device_event_requires_device_lifecycle_mapping'
        action = f.get('action')
        if action in {'ordered', 'planned'}:
            result = {'resourceType': 'ServiceRequest', **common, 'status': 'unknown',
                      'intent': 'order' if action == 'ordered' else 'plan', 'code': concept}
        else:
            result = {'resourceType': 'Procedure', **common, 'status':
                      {'completed': 'completed', 'declined': 'not-done', 'cancelled': 'not-done'}.get(action, 'unknown'),
                      'code': concept}
    elif task in {'observations', 'symptoms_function', 'demographics', 'social_exposures', 'reproductive_perinatal', 'outcomes'} or (task == 'oncology' and entry['collection'] == 'biomarkers'):
        if task == 'observations' and f.get('status') in {'ordered', 'planned'}:
            result = {'resourceType': 'ServiceRequest', **common, 'status': 'unknown',
                      'intent': 'order' if f['status'] == 'ordered' else 'plan', 'code': concept}
        else:
            result = {'resourceType': 'Observation', **common, 'status': 'unknown', 'code': concept}
            number = f.get('numeric_value')
            comparator = f.get('comparator', '=')
            if number is not None and comparator != 'unknown':
                quantity = {'value': number}
                if f.get('unit'):
                    quantity['unit'] = f['unit']  # no guessed UCUM normalization
                if comparator in {'<', '<=', '>', '>='}:
                    quantity['comparator'] = comparator
                result['valueQuantity'] = quantity
            elif f.get('text_value'):
                result['valueString'] = f['text_value']
            elif task == 'observations' and f.get('flag') not in {None, 'unknown'}:
                result['valueString'] = f['flag']
            elif task == 'social_exposures' and f.get('status') not in {None, 'unknown'}:
                result['valueString'] = f['status']
            else:
                result['dataAbsentReason'] = code('http://terminology.hl7.org/CodeSystem/data-absent-reason',
                    {'not_performed': 'not-performed', 'not_applicable': 'not-applicable'}.get(f.get('result_absent_reason'), 'unknown'))
            if f.get('reference_range_text'):
                result['referenceRange'] = [{'text': f['reference_range_text']}]
            for source_key, target_key in (('body_site', 'bodySite'), ('method', 'method')):
                if f.get(source_key):
                    result[target_key] = cc(f[source_key])
    elif task == 'allergies':
        if f.get('scope') != 'specific_substance' or f.get('reaction_type') == 'adverse_effect':
            return None, 'scoped_allergy_or_adverse_effect_requires_review'
        result = {'resourceType': 'AllergyIntolerance', 'patient': subject, 'code': concept}
        if f.get('category') in {'food', 'environment', 'drug'}:
            result['category'] = ['medication' if f['category'] == 'drug' else f['category']]
        if f.get('category') == 'environmental':
            result['category'] = ['environment']
        if f.get('reaction_type') in {'allergy', 'intolerance'}:
            result['type'] = f['reaction_type']
        if f.get('reaction'):
            result['reaction'] = [{'manifestation': [cc(f['reaction'])]}]
    elif task == 'family_genetics' and f.get('kind') == 'family_condition':
        if not f.get('relative') or f.get('subject') == 'index_patient':
            return None, 'family_relationship_unresolved'
        result = {'resourceType': 'FamilyMemberHistory', 'patient': subject, 'status': 'partial',
                  'relationship': cc(f['relative']), 'condition': [{'code': concept}]}
    elif task == 'care_plans':
        action = f.get('action_status')
        result = {'resourceType': 'CarePlan', **common,
                  'status': 'completed' if action == 'completed' else 'unknown',
                  'intent': 'proposal' if action == 'recommended' else 'plan',
                  'description': f['action_text']}
    if result is None:
        return None, 'specialized_mapping_not_implemented'
    # Relative time is retained literally; even a full reported calendar date
    # is not automatically an effective/onset date without event adjudication.
    result['extension'] = [extension('source-fact-key', entry['fact_key']),
        extension('source-fact', json.dumps(f, ensure_ascii=False, sort_keys=True))]
    return result, None


def compile_patient(article, patient, source, *, recorded, codings=None):
    codings = codings or {}
    aid, pid = article['article_id'], patient['patient_id']
    identity_key = json_digest(patient.get('identity_evidence'))
    entries, audit = [], []
    def add(resource, key):
        resource['id'] = stable_id(VERSION, aid, pid, identity_key, resource['resourceType'], key)
        resource['meta'] = {'tag': [{'system': BASE + '/CodeSystem/dataset-status',
            'code': 'literature-derived-synthetic-identity',
            'display': 'Literature-derived facts, synthetic identity, research use'}]}
        url = 'urn:uuid:' + resource['id']
        entries.append({'fullUrl': url, 'resource': resource})
        return url
    patient_id = stable_id(VERSION, aid, pid, identity_key, 'Patient', 'identity')
    ref = add({'resourceType': 'Patient',
        'identifier': [{'system': BASE + '/synthetic-mrn', 'value': patient_id}],
        'name': [{'use': 'temp', 'text': 'Synthetic patient ' + patient_id[:8]}],
        'extension': [extension('synthetic-identity', True, 'Boolean'),
                      extension('source-patient-id', pid)]}, 'identity')
    agent = add({'resourceType': 'Device', 'deviceName': [{'name': VERSION, 'type': 'user-friendly-name'}]}, 'compiler')
    raw = {'article_id': aid, 'article_url': article.get('article_url'), 'license': article.get('license'),
           'text_sha256': article['text_sha256'], 'patient': patient,
           'provenance': article.get('provenance'), 'clinical_accuracy_verified': False}
    doc = add({'resourceType': 'DocumentReference', 'status': 'current', 'subject': {'reference': ref},
        'description': 'Unreviewed extraction packet; includes unmapped facts, citations, timeline and available trace links.',
        'content': [{'attachment': {'contentType': 'application/json',
            'data': base64.b64encode(json.dumps(raw, ensure_ascii=False, sort_keys=True).encode()).decode()}}]}, 'extraction')
    source_doc = add({'resourceType': 'DocumentReference', 'status': 'current',
        'description': 'Primary article source packet. May describe several patients; license retained in packet.',
        'content': [{'attachment': {'contentType': 'application/json',
            'data': base64.b64encode(json.dumps(source, ensure_ascii=False, sort_keys=True).encode()).decode()}}]}, 'source')
    seen = set()
    for entry in fact_entries(article, patient):
        key = entry['fact_key']
        reason = None
        if key in seen:
            reason = 'exact_duplicate'
        seen.add(key)
        if not literal_evidence(entry['fact'], source):
            reason = 'missing_or_nonliteral_evidence'
        if reason:
            resource = None
        else:
            resource, reason = map_fact(entry, ref, codings.get(key))
        target = add(resource, key) if resource else None
        audit.append({k: entry[k] for k in ('fact_key', 'task', 'collection', 'source_pointer')} |
                     {'status': 'mapped' if target else 'deferred', 'reason': reason,
                      'resource': target, 'coding_attached': bool(target and codings.get(key)
                          and contains_codings(resource, codings[key]))})
    provenance = {'resourceType': 'Provenance', 'recorded': recorded,
        'target': [{'reference': e['fullUrl']} for e in entries if e['fullUrl'] not in {agent, source_doc}],
        'agent': [{'who': {'reference': agent}}],
        'entity': [{'role': 'source', 'what': {'reference': source_doc}},
                   {'role': 'derivation', 'what': {'reference': doc}}]}
    add(provenance, 'provenance')
    bundle = {'resourceType': 'Bundle', 'id': stable_id(VERSION, aid, pid, identity_key, 'bundle'),
              'type': 'collection', 'entry': entries}
    return bundle, {'article_id': aid, 'patient_id': pid, 'facts': audit,
        'clinical_accuracy_verified': False, 'synthetic_fields': ['Patient.identifier', 'Patient.name'],
        'deferred_sections': ['case_context', 'timeline', 'measurement_series', 'supplemental_clinical'],
        'preservation': 'The extraction DocumentReference retains the complete patient packet, including every deferred fact.'}
