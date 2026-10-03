"""Opt-in patient temporal graph with exact evidence and conservative constraint gates.

This records source chronology, not inferred encounters or synthetic calendar dates.
A structurally accepted graph still requires semantic attribution/time review.
"""
from __future__ import annotations

import datetime
import re
from typing import Literal
from pydantic import Field, model_validator
from .schemas import StrictModel
from .extraction_contracts import SourceSpan, span_errors


class TimeExpression(StrictModel):
    text: str = Field(min_length=1)
    kind: Literal['calendar', 'relative', 'duration', 'age', 'gestational_age', 'postnatal_age', 'order_only']
    precision: Literal['exact', 'approximate', 'range', 'unknown']
    calendar_value: str | None = Field(description='Only literal YYYY, YYYY-MM or YYYY-MM-DD; no publication date substitution')
    evidence: list[SourceSpan] = Field(min_length=1)

    @model_validator(mode='after')
    def literal_calendar(self):
        if not any(self.text in s.quote for s in self.evidence):
            raise ValueError('Time phrase must be literal in its own evidence')
        if self.calendar_value is not None:
            if self.kind != 'calendar' or self.calendar_value not in self.text:
                raise ValueError('Calendar normalization must preserve literal source precision')
            value = self.calendar_value
            if not re.fullmatch(r'\d{4}(?:-\d{2}(?:-\d{2})?)?', value):
                raise ValueError('Use year, month or full ISO date; leave ambiguous dates as text')
            datetime.date.fromisoformat(value + {4:'-01-01', 7:'-01', 10:''}[len(value)])
        return self


class LongitudinalEvent(StrictModel):
    event_id: str = Field(min_length=1)
    record_id: str = Field(min_length=1)
    episode_id: str | None = Field(description='Only an explicitly distinguishable episode; do not invent visits')
    kind: Literal['presentation', 'history', 'diagnosis', 'test', 'treatment', 'adverse_event', 'outcome', 'follow_up', 'plan', 'other']
    occurrence: Literal['occurred', 'planned', 'conditional', 'unknown']
    description: str = Field(min_length=1)
    fact_ids: list[str]
    evidence: list[SourceSpan] = Field(min_length=1)
    attribution_evidence: list[SourceSpan] = Field(min_length=1)
    times: list[TimeExpression]

    @model_validator(mode='after')
    def plans_are_not_completed(self):
        if self.kind == 'plan' and self.occurrence == 'occurred':
            raise ValueError('A documented plan does not establish its completion')
        return self


class Offset(StrictModel):
    text: str = Field(min_length=1)
    lower: float = Field(ge=0)
    upper: float = Field(ge=0)
    unit: Literal['minute', 'hour', 'day', 'week', 'month', 'year']
    precision: Literal['exact', 'range', 'approximate']

    @model_validator(mode='after')
    def valid_bounds(self):
        if self.upper < self.lower or (self.precision == 'exact' and self.upper != self.lower):
            raise ValueError('Invalid interval bounds/precision')
        return self


class TemporalEdge(StrictModel):
    edge_id: str = Field(min_length=1)
    from_event_id: str
    to_event_id: str
    relation: Literal['before', 'after', 'same_time', 'during', 'overlaps']
    offset: Offset | None
    evidence: list[SourceSpan] = Field(min_length=1, description='Must establish this pair and relation, not just contain a time word')

    @model_validator(mode='after')
    def offset_context(self):
        if self.from_event_id == self.to_event_id:
            raise ValueError('Self-referential temporal edge')
        if self.offset:
            if self.relation not in {'before', 'after'}:
                raise ValueError('Quantitative offset requires a directed before/after relation')
            if not any(self.offset.text in s.quote for s in self.evidence):
                raise ValueError('Offset expression must occur in relation evidence')
        return self


class PatientTimeline(StrictModel):
    schema_version: Literal['patient-timeline/2']
    record_id: str
    events: list[LongitudinalEvent]
    edges: list[TemporalEdge]
    limitations: list[str]

    @model_validator(mode='after')
    def graph_identity(self):
        ids = {e.event_id for e in self.events}
        if len(ids) != len(self.events) or len({e.edge_id for e in self.edges}) != len(self.edges):
            raise ValueError('Duplicate event/edge identity')
        if any(e.record_id != self.record_id for e in self.events):
            raise ValueError('Cross-patient events are not permitted in one timeline')
        if any({e.from_event_id, e.to_event_id} - ids for e in self.edges):
            raise ValueError('Unknown temporal event reference')
        return self


def _negative_cycle(ids, constraints):
    """x[v] <= x[u] + distance; super-source represented by zero initial distances."""
    values = dict.fromkeys(ids, 0.)
    for _ in ids:
        changed = False
        for u, v, distance in constraints:
            if values[v] > values[u] + distance + 1e-9:
                values[v] = values[u] + distance
                changed = True
        if not changed:
            return False
    return bool(ids) and changed


def audit_timeline(timeline: PatientTimeline, sources, fact_records: dict[str, str]):
    """Report contradictions without deleting facts or resolving source conflicts.

    Quantitative bounds are tested within compatible units. Approximate values are
    preserved but never used as exact constraints. No total chronological sort is
    emitted: disconnected events remain unordered.
    """
    issues = []
    def issue(code, target, severity='block'):
        issues.append({'code': code, 'target': target, 'severity': severity})
    def spans(items, target):
        for span in items:
            for error in span_errors(span, sources):
                issue(error, target)
    by_id = {e.event_id: e for e in timeline.events}
    for event in timeline.events:
        spans(event.evidence + event.attribution_evidence, event.event_id)
        for fid in event.fact_ids:
            if fact_records.get(fid) != timeline.record_id:
                issue('unknown_or_wrong_patient_fact', event.event_id)
        for time in event.times:
            spans(time.evidence, event.event_id)
        if not event.times:
            issue('event_time_unknown', event.event_id, 'review')
        issue('event_attribution_and_entailment_unreviewed', event.event_id, 'review')
    # Strict ordering is separate from numeric constraints (no epsilon or fake day).
    order = {key: set() for key in by_id}
    constraints = {'fixed': [], 'month': [], 'year': []}
    scales = {'minute': 60., 'hour': 3600., 'day': 86400., 'week': 604800.}
    same_time = []
    for edge in timeline.edges:
        spans(edge.evidence, edge.edge_id)
        left, right = edge.from_event_id, edge.to_event_id
        issue('temporal_relation_entailment_unreviewed', edge.edge_id, 'review')
        if by_id[left].occurrence != by_id[right].occurrence:
            issue('mixed_occurrence_chronology', edge.edge_id, 'review')
        if edge.relation == 'after':
            left, right = right, left
        if edge.relation in {'before', 'after'}:
            order[left].add(right)
            if edge.offset:
                offset = edge.offset
                issue('offset_normalization_unreviewed', edge.edge_id, 'review')
                numbers = {float(x) for x in re.findall(r'(?<![\w.])\d+(?:\.\d+)?(?![\w.])', offset.text)}
                if numbers and offset.precision != 'approximate' and not {offset.lower, offset.upper} <= numbers:
                    issue('offset_numbers_not_in_expression', edge.edge_id)
                units = {'minute': r'min(?:ute)?s?', 'hour': r'h|hrs?|hours?', 'day': r'd|days?',
                         'week': r'wks?|weeks?', 'month': r'mos?|months?', 'year': r'yrs?|years?'}
                written_units = {unit for unit, pattern in units.items()
                                 if re.search(r'\b(?:' + pattern + r')\b', offset.text, re.I)}
                if written_units and offset.unit not in written_units:
                    issue('offset_unit_not_in_expression', edge.edge_id)
                if offset.precision == 'approximate':
                    continue
                if offset.upper == 0:
                    issue('strict_order_with_zero_offset', edge.edge_id)
                family = 'fixed' if offset.unit in scales else offset.unit
                scale = scales.get(offset.unit, 1.)
                constraints[family] += [(left, right, offset.upper * scale), (right, left, -offset.lower * scale)]
        elif edge.relation == 'same_time':
            same_time.append((left, right))
        else:
            issue('interval_relation_not_numerically_resolved', edge.edge_id, 'review')
    # Reachability detects strict cycles, including those that pass through equality.
    reach = {key: set(values) for key, values in order.items()}
    equality = {key: {key} for key in by_id}
    for left, right in same_time:
        group = equality[left] | equality[right]
        for key in group:
            equality[key] = group
    for left, successors in order.items():
        for right in successors:
            for x in equality[left]:
                reach[x].update(equality[right])
    for middle in by_id:
        for left in by_id:
            if middle in reach[left]:
                reach[left].update(reach[middle])
    if any(key in values for key, values in reach.items()):
        issue('contradictory_temporal_order', timeline.record_id)
    # Non-strict zero bounds enforce qualitative ordering, not a made-up interval.
    ordering_bounds = [(right, left, 0.) for left, rights in order.items() for right in rights]
    equal_bounds = [bound for a, b in same_time for bound in ((a, b, 0.), (b, a, 0.))]
    for family, bounds in constraints.items():
        if bounds and _negative_cycle(by_id, bounds + ordering_bounds + equal_bounds):
            issue('inconsistent_offsets_' + family, timeline.record_id)
    if sum(bool(v) for v in constraints.values()) > 1:
        issue('mixed_calendar_units_not_combined', timeline.record_id, 'review')
    return {'schema_version': 'timeline-audit/1', 'record_id': timeline.record_id,
            'structural_source_gates_passed': not any(i['severity'] == 'block' for i in issues),
            'clinical_timeline_verified': False, 'issues': issues,
            'unordered_event_ids': sorted(k for k in by_id if not order[k] and not any(k in v for v in order.values())
                                          and not any(k in p for p in same_time)),
            'synthetic_dates_generated': False}
