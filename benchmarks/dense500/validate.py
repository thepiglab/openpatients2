"""Check dense targets and source references; never claims clinical entailment."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import re
from pathlib import Path
import types
from typing import Annotated, Union, get_args, get_origin

from pydantic import BaseModel, TypeAdapter
from openpatients2.schemas import TASK_MODELS
from openpatients2.semantic_rules import EVENT_OCCURRENCES, TEMPORAL_RELATIONS


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':')).encode()).hexdigest()


def partial_errors(value, annotation, path):
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is Annotated:
        return partial_errors(value, args[0], path)
    if origin in (Union, types.UnionType):
        alternatives = [partial_errors(value, arg, path) for arg in args]
        return [] if any(not x for x in alternatives) else [f'{path}: invalid union value']
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if not isinstance(value, dict): return [f'{path}: expected object']
        errors = []
        for name, item in value.items():
            if name not in annotation.model_fields:
                errors.append(f'{path}.{name}: unknown field')
            else:
                errors += partial_errors(item, annotation.model_fields[name].annotation, f'{path}.{name}')
        return errors
    if origin is list:
        if not isinstance(value, list): return [f'{path}: expected list']
        return [e for i,v in enumerate(value) for e in partial_errors(v,args[0],f'{path}[{i}]')]
    try:
        TypeAdapter(annotation).validate_python(value, strict=True)
        return []
    except Exception as error:
        return [f'{path}: {str(error).splitlines()[0]}']


NUMBER_WORDS = {w: i for i, w in enumerate('zero one two three four five six seven eight nine ten eleven twelve thirteen '
                                             'fourteen fifteen sixteen seventeen eighteen nineteen twenty'.split())}


# A singular indefinite case statement supports a count of 1 ("We present a 67-year-old woman", "a case of",
# "a patient", "one patient", "an infant").
# Hyphens include the Unicode variants PMC text often uses (U+2010 hyphen, U+2011 non-breaking hyphen, en dash).
SINGLE_CASE = re.compile(r'\b(?:a|an|one)\s+(?:(?:\d+|[a-z]+)[-\u2010\u2011\u2013\s](?:year|month|week|day)s?'
                         r'[-\u2010\u2011\u2013\s]old\s+|(?:[a-z\-\u2010\u2011]+\s+){0,3})?(?:patient|case|man|woman|male|female|boy|girl|child|infant|neonate|'
                         r'newborn|adolescent|gentleman|lady|individual|person|subject|dog|cat|horse)\b', re.I)


def roster_block_errors(annotation):
    """Optional article-level `roster` block: the single source of the roster's reported count/completeness.

    {'reported_individual_count': int|None, 'roster_complete': bool, 'evidence': [{segment_id, quote}]}.
    A count needs a literal quote (checked like every quote) that states that number (digits or a number word);
    a complete roster cannot contradict the count or the annotated patients."""
    block = annotation.get('roster')
    if block is None:
        return []
    errors = []
    if not isinstance(block, dict) or set(block) - {'reported_individual_count', 'roster_complete', 'evidence'}:
        return ['roster: block must be {reported_individual_count, roster_complete, evidence}']
    count, complete, evidence = block.get('reported_individual_count'), block.get('roster_complete'), block.get('evidence') or []
    if count is not None and (not isinstance(count, int) or isinstance(count, bool) or count < 0):
        errors.append('roster: reported_individual_count must be a non-negative integer or null')
    if not isinstance(complete, bool):
        errors.append('roster: roster_complete must be true or false')
    if not all(isinstance(e, dict) and e.get('segment_id') and e.get('quote') for e in evidence):
        errors.append('roster: evidence items are {segment_id, quote}')
    if isinstance(count, int) and not isinstance(count, bool):
        words = {w for e in evidence for w in re.findall(r'[a-z]+|\d+', e.get('quote', '').casefold())}
        single = count == 1 and any(SINGLE_CASE.search(e.get('quote', '')) for e in evidence)
        if not evidence or not (single or str(count) in words or any(NUMBER_WORDS.get(w) == count for w in words)):
            errors.append(f'roster: reported_individual_count {count} needs a literal evidence quote stating that number')
        if complete is True and count != len(annotation.get('patients', [])):
            errors.append('roster: a complete roster must have exactly reported_individual_count annotated patients')
    return errors


def validate(annotation, source):
    errors=[]
    def require(test, message):
        if not test: errors.append(message)
    segments={s['segment_id']:s['text'] for s in source['segments']}
    for field in ('article_id','text_sha256','xml_sha256'):
        require(annotation.get(field)==source.get(field), f'{field}: source mismatch')
    require(annotation.get('schema_version')=='dense-article-annotation/1','schema version')
    def quotes(obj,path='root'):
        if isinstance(obj,dict):
            if 'quote' in obj and 'segment_id' in obj:
                text=segments.get(obj['segment_id'])
                require(bool(obj['quote']) and text is not None and obj['quote'] in text, f'{path}: nonliteral or missing source quote')
            for k,v in obj.items(): quotes(v,path+'.'+k)
        elif isinstance(obj,list):
            for i,v in enumerate(obj): quotes(v,f'{path}[{i}]')
    quotes(annotation)
    ids=[p['patient_id'] for p in annotation.get('patients',[])]
    require(len(ids)==len(set(ids)), 'duplicate patient ID')
    all_facts=[]
    for p in annotation.get('patients',[]):
        pid=p['patient_id']; facts=p['facts']; fids={f['fact_id'] for f in facts}
        all_facts += [f['fact_id'] for f in facts]
        require(bool(p.get('identity_evidence')),f'{pid}: no identity evidence')
        require(p.get('species') in ['human','nonhuman','unknown'],f'{pid}: species')
        require(set(p.get('domain_coverage',{}))==set(TASK_MODELS),f'{pid}: incomplete domain coverage')
        for f in facts:
            path=f['fact_id']; task=f['task']; collection=f.get('collection')
            require(bool(f.get('evidence')),f'{path}: missing evidence')
            require(f.get('certainty') in ['supported','ambiguous','source_conflict'],f'{path}: certainty')
            require(f.get('scoring') in ['include','review_only'],f'{path}: scoring')
            require(f.get('certainty')=='supported' or f.get('scoring')=='review_only',f'{path}: unresolved scored as truth')
            model=TASK_MODELS.get(task)
            if model is None:
                errors.append(f'{path}: unknown task'); continue
            if task=='case_context':
                require(collection is None,f'{path}: case_context collection')
            elif collection not in model.model_fields:
                errors.append(f'{path}: invalid collection {collection}'); continue
            else:
                model=get_args(model.model_fields[collection].annotation)[0]
            errors += partial_errors(f['fields'],model,path+'.fields')
        events=p.get('events',[]); eids={e['event_id'] for e in events}
        require(len(events)==len(eids),f'{pid}: duplicate event IDs')
        graph={e:set() for e in eids}
        for event in events:
            require(set(event['fact_ids'])<=fids,f'{event["event_id"]}: foreign fact link')
            require(event.get('occurrence') in EVENT_OCCURRENCES,f'{event["event_id"]}: occurrence')
            require(bool(event.get('evidence')),f'{event["event_id"]}: evidence')
            literal=event.get('time_text')
            require(literal is None or any(literal in e['quote'] for e in event['evidence']),f'{event["event_id"]}: nonliteral time')
            # Optional classification (kind, time_kind, time_precision, time_calendar_value): set here, never patched
            # into schema submissions.
            from openpatients2.longitudinal import annotation_event_errors
            errors += annotation_event_errors(event)
        for relation in p.get('temporal_relations',[]):
            a,b=relation['from_event_id'],relation['to_event_id']; r=relation['relation']
            require(a in eids and b in eids,f'{pid}: foreign temporal event')
            require(r in TEMPORAL_RELATIONS,f'{pid}: temporal relation')
            require(bool(relation.get('evidence')),f'{pid}: temporal evidence')
            if a in eids and b in eids and r in ['before','after']:
                graph[a if r=='before' else b].add(b if r=='before' else a)
        def cycle(node,trail):
            if node in trail: return True
            return any(cycle(n,trail|{node}) for n in graph[node])
        require(not any(cycle(n,set()) for n in graph),f'{pid}: temporal cycle')
        for summary in p.get('summary',[]):
            require(bool(summary['fact_ids']) and set(summary['fact_ids'])<=fids,f'{pid}: summary references')
    require(len(all_facts)==len(set(all_facts)),'duplicate article fact ID')
    errors += roster_block_errors(annotation)
    coverage=annotation.get('source_coverage',[])
    require(Counter(x['segment_id'] for x in coverage)==Counter(segments.keys()), 'source coverage incomplete/duplicate')
    for c in coverage:
        require(set(c['patient_ids'])<=set(ids),f'{c["segment_id"]}: coverage patient link')
    figs=annotation.get('figures',[])
    require({f['figure_id'] for f in figs}=={f.get('figure_key') or f['figure_id'] for f in source.get('figures',[])},'figure coverage mismatch')
    for figure in figs:
        require(set(figure['patient_ids'])<=set(ids),'figure patient link')
        require(figure.get('visual_status')!='not_inspected' or figure.get('visual_description') is None,'uninspected pixel claim')
        for panel in figure.get('panels',[]):
            require(set(panel['patient_ids'])<=set(ids),'panel patient link')
    require(annotation.get('annotation_provenance',{}).get('prior_predictions_consulted') is False,'annotator exposure not excluded')
    return errors


def case_context_errors(annotation):
    """Article-derived packet rule (prompts/case_context.md): every per-patient record
    uses case_kind clinical_case/animal and multiple_index_patients=false, even when
    the article has several patients. Enforced when promoting or signing new work
    (review_record.save_review, schema_review --role); export only warns so articles
    awaiting migrate_case_context.py are not dropped from the build."""
    errors=[]
    for p in annotation.get('patients',[]):
        for f in p.get('facts',[]):
            if f.get('task')!='case_context':continue
            kind=f['fields'].get('case_kind');multiple=f['fields'].get('multiple_index_patients')
            if kind=='multi_patient' or multiple is not False:
                errors.append(f'{f["fact_id"]}: case_context_packet_rule: per-patient record needs case_kind clinical_case/animal '
                              f'and multiple_index_patients=false (got {kind!r}, {multiple!r}); run migrate_case_context.py')
    return errors


def semantic_violations(annotation, source):
    """Shared annotation rules (src/openpatients2/rules/annotation_rules.yaml) on included facts.

    Same checks the runtime validator applies to model output (openpatients2.semantic_rules).
    Errors block promotion/signing (review_record.save_review, schema_review --role) and export
    (reference_gates); warnings are heuristics for the reviewer."""
    from openpatients2.semantic_rules import annotation_violations
    return annotation_violations(annotation, source)


def semantic_errors(annotation, source):
    return [v.text() for v in semantic_violations(annotation, source) if v.severity=='error']


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('root',type=Path);ap.add_argument('--folder',default='drafts')
    args=ap.parse_args(); total=0; failed=0
    import store
    for aid in store.article_ids(args.root,args.folder):
        total+=1
        annotation=json.loads(store.path(args.root,args.folder,aid).read_text())
        source=json.loads(store.path(args.root,'sources',aid).read_text())
        result=validate(annotation,source)+case_context_errors(annotation)+semantic_errors(annotation,source)
        if result:
            failed+=1; print(aid, json.dumps(result,ensure_ascii=False))
    print(json.dumps({'checked':total,'invalid':failed,'clinical_entailment_checked':False}))
    raise SystemExit(bool(failed))
