"""Regression tests for the local-LLM pipeline hardening after the live muse-glimmer-30b test.

Shapes come from that run (PMC12753901.1, PMC12812032.1, PMC12723601.1): a time borrowed from the case's
introduction sentence, "within an hour" recorded as after a paraphrased anchor, low table recall, duplicate items,
over-split summaries, undefined temporality and not-run figures listed as unavailable media.
"""
import json

from openpatients2 import annotation_rules
from openpatients2.semantic_rules import ERROR, WARNING, item_violations, roster_label_errors
from openpatients2.validation import validate

INTRO = ('A 24-year-old G1P0 with no significant past medical history presented for her first obstetric visit at '
         '8 weeks gestation.')
FACT = 'The nausea and vomiting was all of the time'
CARE = 'and a real time PCBH consultation was made which was completed within an hour'
SOURCE = f'{INTRO} During her initial visit, routine labs were drawn. {FACT}, she said. Then {CARE}.'
NO_TIME = {'text': None, 'relation': 'unknown', 'anchor': None, 'date_iso': None}


def symptom(time, quotes, segment=None):
    return {'subject': 'index_patient', 'assertion': 'present', 'temporality': 'unknown', 'time': time,
            'evidence': [{'quote': q, 'source_section': segment} for q in quotes], 'name': 'vomiting', 'kind': 'symptom',
            'text_value': None, 'numeric_value': None, 'unit': None, 'body_site': None, 'severity': None,
            'activity_context': None}


def care_plan(time):
    return {'subject': 'index_patient', 'assertion': 'present', 'temporality': 'unknown', 'time': time,
            'evidence': [{'quote': CARE, 'source_section': None}], 'action_text': 'PCBH consultation',
            'plan_type': 'counseling', 'target': None, 'responsible_specialty': None, 'urgency': None,
            'action_status': 'completed'}


def section(items):
    return {'coverage': 'complete', 'limitations': [], 'documentation_status': 'documented',
            'documentation_evidence': [], 'items': items}


BORROWED = symptom({'text': '8 weeks gestation', 'relation': 'at', 'anchor': None, 'date_iso': None}, [INTRO, FACT])
WITHIN = care_plan({'text': 'within an hour', 'relation': 'after', 'anchor': 'initial visit', 'date_iso': None})


def severities(task, item, strict):
    return {(v.rule_id, v.severity) for v in item_violations(task, 'items', item, SOURCE, '/items/0', strict_time=strict)
            if v.rule_id in ('NO-BORROWING', 'TIME-LITERAL')}


def test_borrowed_intro_sentence_time_fails_the_runtime_checker_but_only_warns_for_references():
    assert severities('symptoms_function', BORROWED, strict=True) == {('NO-BORROWING', ERROR)}
    assert severities('symptoms_function', BORROWED, strict=False) == {('NO-BORROWING', WARNING)}
    runtime = validate('symptoms_function', section([BORROWED]), SOURCE, strict_time=True)
    assert not runtime.valid and any('[NO-BORROWING]' in e for e in runtime.errors)
    assert validate('symptoms_function', section([BORROWED]), SOURCE).valid       # reference gates: warning only
    fixed = symptom(dict(NO_TIME), [FACT])
    assert validate('symptoms_function', section([fixed]), SOURCE, strict_time=True).valid


def test_scoping_heading_and_fragment_times_stay_warnings_in_strict_mode():
    scoped = symptom({'text': 'On admission', 'relation': 'at', 'anchor': None, 'date_iso': None},
                     ['On admission, laboratory tests showed the following results.', FACT])
    heading = symptom({'text': 'on admission', 'relation': 'at', 'anchor': None, 'date_iso': None},
                      ['Laboratory findings on admission:', FACT])
    fragment = symptom({'text': 'At baseline', 'relation': 'at', 'anchor': None, 'date_iso': None},
                       ['At baseline', FACT])
    for item in (scoped, heading, fragment):
        assert ('NO-BORROWING', ERROR) not in severities('symptoms_function', item, strict=True)


def test_within_duration_after_an_anchor_its_evidence_lacks_fails_the_runtime_checker():
    assert severities('care_plans', WITHIN, strict=True) == {('TIME-LITERAL', ERROR)}
    assert severities('care_plans', WITHIN, strict=False) == {('TIME-LITERAL', WARNING)}
    bound = care_plan({'text': 'within an hour', 'relation': 'before', 'anchor': None, 'date_iso': None})
    assert not severities('care_plans', bound, strict=True)
    # Without a borrowed anchor the mismatch stays a warning; "span of 1 week" is a duration, not a reference point.
    unanchored = care_plan({'text': 'within an hour', 'relation': 'unknown', 'anchor': None, 'date_iso': None})
    assert severities('care_plans', unanchored, strict=True) == {('TIME-LITERAL', WARNING)}
    span = {**WITHIN, 'evidence': [{'quote': 'resolved within a time span of 1 week', 'source_section': None}],
            'time': {'text': 'within a time span of 1 week', 'relation': 'before', 'anchor': None, 'date_iso': None}}
    assert not severities('care_plans', span, strict=False)


def test_targeted_repair_may_clear_a_flagged_time_and_keeps_the_fact_when_the_model_cannot():
    from openpatients2.targeted_repair import ItemRepair, time_repair_allowed
    segments = [{'segment_id': 'b1', 'text': SOURCE}]
    borrowed = symptom(BORROWED['time'], [INTRO, FACT], 'b1')
    plan = ItemRepair.create('symptoms_function', section([borrowed]), SOURCE, segments)
    assert plan and [p['index'] for p in plan.pending] == [0]
    assert any('[NO-BORROWING]' in e for e in plan.pending[0]['errors'])
    # A correct repair removes the borrowed quote and the time; that is not refused as "field erasure".
    plan.instruction(); plan.apply(section([symptom(dict(NO_TIME), [FACT], 'b1')]))
    assert not plan.pending and plan.trace[-1]['decision'] == 'accepted_replacement'
    # A model that repeats the borrowed time: the time association is quarantined, the fact is kept.
    stubborn = ItemRepair.create('symptoms_function', section([borrowed]), SOURCE, segments)
    stubborn.instruction(); stubborn.apply(section([borrowed]))
    assert not stubborn.pending and stubborn.trace[-1]['decision'] == 'accepted_time_quarantined'
    kept = stubborn.output()['items'][0]
    assert kept['time']['text'] is None and [e['quote'] for e in kept['evidence']] == [FACT]
    # Whole-section retries (no item plan) may change a flagged item's time/evidence, nothing else.
    errors = ['/items/0: [NO-BORROWING] time.text ... ; /tumors/1: [TIME-LITERAL] ...']
    assert time_repair_allowed('/items/0/time/text', errors) and time_repair_allowed('/tumors/1/evidence/0', errors)
    assert not time_repair_allowed('/items/0/numeric_value', errors) and not time_repair_allowed('/items/2/time', errors)


def test_temporality_and_relative_rules_reach_the_runtime_prompts():
    from openpatients2.article_tasks import INSTRUCTIONS
    from openpatients2.patient_context import discovery_messages
    from openpatients2.prompts import system_text
    system = system_text()
    for rule in ('TEMPORALITY', 'ROSTER-RELATIVES', 'ONE-STATEMENT'):
        assert f'[{rule}]' in system
    assert 'Default temporality=unknown' in system
    article = {'article_id': 'A1', 'title': 'T', 'segments': [{'segment_id': 'b1', 'kind': 'paragraph', 'heading': None,
                                                               'text': 'A woman delivered a boy.'}]}
    discovery = discovery_messages(article)[1]['content']
    assert 'separate patients only if independently described' not in discovery
    for rule in ('ROSTER-RELATIVES', 'ROSTER-LABEL', 'ROSTER-ORDER'):
        assert f'[{rule}]' in discovery and f'[{rule}]' in INSTRUCTIONS['roster']
    assert '[SUMMARY-CLAIMS]' in INSTRUCTIONS['summary'] and 'atomic claims' not in INSTRUCTIONS['summary']
    assert '[OBS-TABLE-ROWS]' in annotation_rules.prompt_block('observations')


def test_temporality_rule_matches_the_reference_convention():
    """References leave temporality unknown for most facts (the materializer fills omitted fields with unknown)."""
    from pathlib import Path
    import pytest
    root = Path(__file__).resolve().parents[1] / 'benchmarks' / 'dense' / 'articles'
    files = sorted(root.glob('*/annotation.json'))[:80]
    if not files:
        pytest.skip('corpus annotations unavailable')
    values = [(f.get('fields') or {}).get('temporality', 'unknown') for a in map(lambda p: json.loads(p.read_text()), files)
              for patient in a['patients'] for f in patient['facts'] if f['task'] != 'case_context']
    assert values.count('unknown') / len(values) > 0.6


def test_roster_label_must_not_be_the_article_title():
    title = 'Ovarian Torsion Secondary to a Mature Cystic Teratoma in a Four-Year-Old Girl: A Case Report'
    bad = [{'patient_id': 'p1', 'label': 'Ovarian torsion secondary to a mature cystic teratoma in a four-year-old girl'}]
    good = [{'patient_id': 'p1', 'label': 'four-year-old girl with ovarian torsion and a mature cystic teratoma'}]
    assert roster_label_errors(bad, title) and '[ROSTER-LABEL]' in roster_label_errors(bad, title)[0]
    assert not roster_label_errors(good, title)


def test_observation_prompt_lists_every_table_row():
    from openpatients2.prompts import messages_for, table_row_checklist
    head = 'Table 1.\nTable 1. Laboratory findings on admission.\nLaboratory test | Results (normal range)\n'
    rows = [('b14', head + 'Peripheral blood'), ('b15', head + 'White blood cell count (/μL) | 23,130 (3,300 – 8,600)'),
            ('b16', head + 'Hemoglobin (g/dL) | 4.5 (11.6 – 14.8)'), ('b20', 'She was transfused.')]
    text, spans = '', []
    for sid, body in rows:
        prefix = f'[{sid}] Results\n'
        spans.append({'segment_id': sid, 'heading': 'Results', 'start': len(text) + len(prefix),
                      'end': len(text) + len(prefix) + len(body), **({'table_id': 'Table1'} if sid != 'b20' else {})})
        text += prefix + body + '\n\n'
    record = {'record_id': 'A1:p1', 'text': text, 'packet_spans': spans}
    checklist = table_row_checklist(record)
    assert '2 table rows' in checklist and '[b15] Table1: White blood cell count' in checklist and '[b16]' in checklist
    assert '[b14]' not in checklist and '[b20]' not in checklist
    assert checklist in messages_for(record, 'observations')[1]['content']
    assert 'TABLE ROW CHECKLIST' not in messages_for(record, 'conditions')[1]['content']
    assert table_row_checklist({'record_id': 'x', 'text': 'no tables', 'packet_spans': []}) == ''


def test_exact_duplicate_items_are_removed_with_an_audit():
    from openpatients2.clinical_normalization import normalize_clinical
    quote = 'a follow-up ultrasound performed at one month was normal'
    item = {'name': 'follow-up ultrasound', 'text_value': 'normal', 'flag': 'normal',
            'evidence': [{'quote': quote, 'source_section': 'b1'}], 'time': dict(NO_TIME)}
    other = {**item, 'text_value': 'right ovary of normal size', 'flag': 'unknown'}
    result, audit = normalize_clinical('observations', {'items': [item, other, json.loads(json.dumps(item))]},
                                       [{'segment_id': 'b1', 'text': quote}])
    assert [i['text_value'] for i in result['items']] == ['normal', 'right ovary of normal size']
    assert audit[-1]['status'] == 'removed_duplicate' and audit[-1]['pointer'] == '/items/2'


def test_unrun_figures_are_not_reported_as_unavailable_media():
    from openpatients2.ehr_pipeline import visual_target_from_pilot
    from openpatients2.unified_record import build_figures, unavailable_media
    article = {'article_id': 'A1', 'text_sha256': 'x', 'figures': [
        {'figure_key': 'F1', 'label': 'Figure 1', 'caption': 'c', 'image_urls': ['https://example.org/f1.jpg']},
        {'figure_key': 'F2', 'label': 'Figure 2', 'caption': 'c', 'image_urls': ['https://example.org/f2.jpg']},
        {'figure_key': 'F3', 'label': 'Video 1', 'caption': 'c', 'image_urls': []}]}
    failed = [{'identity': {'article_id': 'A1', 'figure_id': 'F2'}, 'status': 'failed'}]
    target = visual_target_from_pilot(article, failed)
    assert [f['status'] for f in target['figures']] == ['not_reviewed', 'not_reviewed', 'unavailable']
    assert 'not run' in target['figures'][0]['limitations'][0] and 'failed' in target['figures'][1]['limitations'][0]
    figures = build_figures(article, target, [], visual_status='model_output_unreviewed')
    assert [m['id'] for m in unavailable_media(figures, [])] == ['F3']
    assert figures[0]['media_available'] is None and figures[0]['review_status'] == 'not_reviewed'


def test_items_still_failing_only_on_time_keep_the_fact_after_a_rejected_repair_reply():
    from openpatients2.targeted_repair import ItemRepair
    segments = [{'segment_id': 'b1', 'text': SOURCE}]
    borrowed = symptom(BORROWED['time'], [INTRO, FACT], 'b1')
    plan = ItemRepair.create('symptoms_function', section([borrowed]), SOURCE, segments)
    plan.instruction(); plan.apply({'not': 'the requested shape'})
    assert plan.pending and plan.trace[-1]['decision'] == 'rejected_reply'
    assert plan.quarantine_pending_times() == [0] and not plan.pending
    item = plan.output()['items'][0]
    assert item['name'] == 'vomiting' and item['time']['relation'] == 'unknown' and len(item['evidence']) == 1
    # A non-time error is never cleared this way.
    other = ItemRepair.create('symptoms_function', section([{**borrowed, 'evidence': [{'quote': 'invented', 'source_section': 'b1'}]}]),
                              SOURCE, segments)
    assert other.quarantine_pending_times() == [] and other.pending
