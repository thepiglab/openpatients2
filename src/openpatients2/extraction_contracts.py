"""Opt-in provenance/coverage contracts; historical benchmark schemas are unchanged."""
from __future__ import annotations

import hashlib
from typing import Literal
from pydantic import Field, model_validator
from .schemas import StrictModel


class SourceSpan(StrictModel):
    source_id: str = Field(min_length=1, description='Versioned article AND representation, e.g. PMC123.1:jats')
    segment_id: str = Field(min_length=1)
    segment_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    start: int = Field(ge=0, description='Unicode code points in the immutable segment, inclusive')
    end: int = Field(gt=0, description='Exclusive end, never byte offsets')
    quote: str = Field(min_length=1)

    @model_validator(mode='after')
    def extent(self):
        if self.end - self.start != len(self.quote):
            raise ValueError('Span extent must match exact quote length')
        return self


def span_errors(span: SourceSpan, sources: dict[str, dict[str, str]]) -> list[str]:
    text = sources.get(span.source_id, {}).get(span.segment_id)
    if text is None:
        return ['unknown_source_segment']
    errors = []
    if hashlib.sha256(text.encode()).hexdigest() != span.segment_sha256:
        errors.append('source_digest_mismatch')
    if text[span.start:span.end] != span.quote:
        errors.append('nonliteral_span')
    return errors


class FieldSupport(StrictModel):
    pointer: str = Field(pattern=r'^/', description='JSON pointer within one fact, e.g. /dose_unit')
    evidence: list[SourceSpan] = Field(min_length=1)
    role: Literal['value', 'subject', 'negation', 'certainty', 'time', 'table_row', 'table_column', 'table_header']
    # Gate outcomes are supplied by the validator/reviewer, never by the extractor.


class FactEnvelope(StrictModel):
    fact_id: str = Field(min_length=1)
    record_id: str = Field(min_length=1, description='Article-version-scoped patient; never a shared p1 key')
    task: str = Field(min_length=1)
    collection: str = Field(min_length=1, description='items, tumors, biomarkers or treatments')
    value: dict
    field_support: list[FieldSupport]
    origin: Literal['article_text', 'table', 'caption', 'visual_annotation']
    supersedes_fact_id: str | None

    @model_validator(mode='after')
    def pointers_exist(self):
        for support in self.field_support:
            value = self.value
            for token in support.pointer[1:].split('/'):
                token = token.replace('~1', '/').replace('~0', '~')
                try:
                    if isinstance(value, list) and (not token.isdigit() or str(int(token)) != token):
                        raise ValueError('Noncanonical array index')
                    value = value[int(token)] if isinstance(value, list) else value[token]
                except (ValueError, TypeError, KeyError, IndexError) as exc:
                    raise ValueError('Field evidence references a nonexistent value: ' + support.pointer) from exc
        return self


def field_support_report(fact: FactEnvelope, sources):
    """Coverage of material leaf fields, not an automatic entailment judgment.

    The existing domain validator must also validate value in its full section
    (especially oncology references). Literal support does not prove semantics.
    """
    def leaves(value, path=''):
        if isinstance(value, dict):
            for key, child in value.items():
                if key != 'evidence':
                    yield from leaves(child, path + '/' + key.replace('~', '~0').replace('/', '~1'))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from leaves(child, path + '/' + str(index))
        elif value is not None and value != '' and value != 'unknown':
            yield path
    issues = []
    supported = {s.pointer for s in fact.field_support}
    for pointer in sorted(set(leaves(fact.value)) - supported):
        issues.append({'code': 'missing_field_evidence', 'pointer': pointer})
    for support in fact.field_support:
        for span in support.evidence:
            issues.extend({'code': code, 'pointer': support.pointer} for code in span_errors(span, sources))
    if fact.origin == 'visual_annotation':
        issues.append({'code': 'visual_annotation_requires_clinical_adjudication', 'pointer': '/'})
    return {'fact_id': fact.fact_id, 'literal_field_gates_passed': not issues,
            'clinical_entailment_verified': False, 'issues': issues}


class CoverageUnit(StrictModel):
    unit_id: str = Field(min_length=1)
    kind: Literal['paragraph', 'table_row', 'table_cell', 'caption', 'figure_panel', 'supplement_manifest']
    source_span: SourceSpan
    record_ids: list[str]
    disposition: Literal['extracted', 'no_relevant_fact', 'other_patient', 'background', 'aggregate',
                         'external_case', 'duplicate', 'unresolved', 'not_inspected']
    fact_ids: list[str]
    duplicate_of: str | None
    rationale: str = Field(min_length=1)

    @model_validator(mode='after')
    def disposition_contract(self):
        if self.disposition == 'extracted' and (not self.fact_ids or not self.record_ids):
            raise ValueError('Extracted units require facts and their patient scopes')
        if self.disposition != 'extracted' and self.fact_ids:
            raise ValueError('Only extracted units can claim fact coverage')
        if (self.disposition == 'duplicate') != (self.duplicate_of is not None):
            raise ValueError('Only a duplicate disposition requires duplicate_of')
        return self


def coverage_report(units: list[CoverageUnit], expected_unit_ids: list[str], facts: list[FactEnvelope], sources):
    """Audit against a caller-created inventory; a model cannot shrink the denominator."""
    if len(expected_unit_ids) != len(set(expected_unit_ids)):
        raise ValueError('Duplicate inventory IDs')
    by_id = {u.unit_id: u for u in units}
    if len(by_id) != len(units):
        raise ValueError('Duplicate coverage dispositions')
    fact_map = {f.fact_id: f for f in facts}
    if len(fact_map) != len(facts):
        raise ValueError('Duplicate fact IDs')
    issues = []
    expected = set(expected_unit_ids)
    for uid in sorted(expected - set(by_id)):
        issues.append({'unit_id': uid, 'code': 'missing_disposition'})
    for unit in units:
        errors = span_errors(unit.source_span, sources)
        if unit.unit_id not in expected:
            errors.append('unknown_inventory_unit')
        if unit.disposition in {'unresolved', 'not_inspected'}:
            errors.append(unit.disposition)
        for fid in unit.fact_ids:
            if fid not in fact_map:
                errors.append('unknown_fact')
            elif fact_map[fid].record_id not in unit.record_ids:
                errors.append('fact_patient_mismatch')
        if unit.duplicate_of:
            seen = {unit.unit_id}; other = unit
            while other.duplicate_of:
                target = other.duplicate_of
                if target in seen or target not in by_id:
                    errors.append('invalid_duplicate_chain'); break
                seen.add(target); other = by_id[target]
            else:
                if other.disposition in {'unresolved', 'not_inspected'}:
                    errors.append('duplicate_of_unresolved_unit')
        issues.extend({'unit_id': unit.unit_id, 'code': e} for e in dict.fromkeys(errors))
    return {'expected_units': len(expected), 'provided_units': len(by_id),
            'bookkeeping_complete': not issues, 'issues': issues,
            'semantic_coverage_verified': False,
            'notice': 'A disposition is a claim about coverage; source review still checks missed facts and wrong-patient assignment.'}
