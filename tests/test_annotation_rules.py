"""The shared rules file renders into every runtime prompt and reference document without drift."""
from typing import get_args

import pytest

from openpatients2 import annotation_rules, semantic_rules


def test_no_generated_block_drifts_from_the_rules_file():
    stale = annotation_rules.drift()
    assert not stale, f'Run `PYTHONPATH=src .venv/bin/python -m openpatients2.annotation_rules --write`: {stale}'


def test_rule_ids_are_unique_and_validator_flags_match_the_checks():
    rules = annotation_rules.load_rules()
    ids = [r['id'] for r in rules]
    assert len(ids) == len(set(ids))
    assert {r['id'] for r in rules if r.get('validator')} == set(semantic_rules.VALIDATORS)
    for r in rules:
        assert r['title'] and r['text'] and (r.get('prompts') or r.get('docs'))


def test_every_rule_reaches_a_runtime_prompt_and_the_protocol():
    rules = annotation_rules.load_rules()
    rendered = {name: annotation_rules.prompt_block(name)
                for name in annotation_rules.PROMPT_FILES + annotation_rules.CODE_PROMPTS}
    protocol = annotation_rules.doc_block('full', 'protocol')
    checklist = annotation_rules.doc_block('checklist', None)
    for r in rules:
        assert any(f'[{r["id"]}]' in block for block in rendered.values()), r['id']
        assert f'**{r["id"]}**' in protocol and f'`{r["id"]}`' in checklist


def test_runtime_messages_carry_the_rules():
    from openpatients2.prompts import messages_for, task_text
    from openpatients2.article_tasks import INSTRUCTIONS
    from openpatients2.figure_visuals import REFINEMENT_INSTRUCTION
    from openpatients2 import ehr_tasks
    record = {'record_id': 'r1', 'text': 'A 67-year-old man.'}
    messages = messages_for(record, 'observations')
    assert '[OBS-COMPARATOR]' in messages[1]['content'] and '[TIME-LITERAL]' in messages[0]['content']
    assert '[CTX-PER-PATIENT]' in task_text('case_context')
    assert '[ROSTER-ORDER]' in INSTRUCTIONS['roster'] and '[TIMELINE-ENUMS]' in INSTRUCTIONS['timeline']
    assert '[FIG-LABELS]' in REFINEMENT_INSTRUCTION
    assert '[FIG-EHR-COMPARISON]' in ehr_tasks.EHR_INSTRUCTION and '[FIG-PRIVACY]' in ehr_tasks.PRIVACY_INSTRUCTION
    for value in semantic_rules.EVENT_OCCURRENCES + semantic_rules.TEMPORAL_RELATIONS:
        assert value in INSTRUCTIONS['timeline']


def test_edited_block_is_detected_and_regenerated():
    text = annotation_rules.render_doc('# Doc\n', 'full', 'protocol')
    tampered = text.replace('[', '(', 1).replace('OBS-COMPARATOR', 'OBS-COMPARATOR-EDITED')
    assert annotation_rules.render_doc(tampered, 'full', 'protocol') == text
    assert annotation_rules.render_doc(text, 'full', 'protocol') == text  # idempotent
    with pytest.raises(ValueError, match='end marker'):
        annotation_rules.render_doc(annotation_rules.DOC_START + '\nno end', 'full', 'protocol')


def test_one_timeline_vocabulary_in_both_pipelines():
    from openpatients2.longitudinal import LongitudinalEvent, TemporalEdge
    from openpatients2.episode_ledger import EdgeAddition, EventAddition
    assert get_args(LongitudinalEvent.model_fields['occurrence'].annotation) == semantic_rules.EVENT_OCCURRENCES
    assert get_args(TemporalEdge.model_fields['relation'].annotation) == semantic_rules.TEMPORAL_RELATIONS
    assert get_args(EventAddition.model_fields['occurrence'].annotation) == semantic_rules.EVENT_OCCURRENCES
    assert get_args(EdgeAddition.model_fields['relation'].annotation) == semantic_rules.TEMPORAL_RELATIONS


def test_live_discovery_prompt_carries_roster_rules():
    # op2 extract discovers patients with patient_context.discovery_messages (not article_tasks.INSTRUCTIONS);
    # its checker enforces ROSTER-ORDER, so the prompt the local model sees must state the rule.
    from openpatients2.patient_context import discovery_messages
    article = {'article_id': 'PMC1.1', 'title': 'T', 'segments': [{'segment_id': 's1', 'kind': 'paragraph',
                                                                   'heading': None, 'text': 'A 67-year-old man.'}]}
    for refined in (False, True):
        text = discovery_messages(article, refined=refined)[1]['content']
        assert '[ROSTER-ORDER]' in text and '[NO-COHORT-COPY]' in text
