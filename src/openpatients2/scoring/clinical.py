"""Clinical item matching (evidence overlap first, name similarity as tie-break) and attribute scoring.

Covers all 14 unified sections. Items are matched one-to-one per aligned patient; repeated items are
kept. Observations, symptoms_function and procedures_devices form one matching pool so exam
signs/scales may sit in either of the first two and diagnostic imaging may appear as a procedure;
such matches are reported separately as cross-section matches. Reference items flagged
``scoring: "review_only"`` absorb predictions without counting as TP/FP and are not required for recall.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import re

from .assign import assign
from .text import evidence_spans, informative, name_sim, norm, norm_unit, num_equal, numbers, span_overlap

TASKS = ('case_context', 'demographics', 'conditions', 'symptoms_function', 'medications', 'allergies',
         'procedures_devices', 'observations', 'oncology', 'family_genetics', 'reproductive_perinatal',
         'social_exposures', 'outcomes', 'care_plans')
POOLED = ('observations', 'symptoms_function', 'procedures_devices')
ATTRIBUTE_GROUPS = ('assertion', 'status', 'value', 'unit', 'comparator', 'time', 'subject', 'temporality', 'category')

# task -> collection -> spec. label: fields joined for name similarity; key: hard match constraint;
# value: (numeric field, text field); status/category: enum fields compared field-wise within a task.
SPECS = {
    'demographics': {'items': dict(label=('text_value',), key='attribute', value=('numeric_value', 'text_value'),
                                   unit='unit', category=('attribute',))},
    'conditions': {'items': dict(label=('name',), status=('clinical_status', 'verification_status'),
                                 category=('role', 'laterality'))},
    'symptoms_function': {'items': dict(label=('name',), value=('numeric_value', 'text_value'), unit='unit',
                                        category=('kind',))},
    'medications': {'items': dict(label=('name',), value=('dose_value', 'dose_text'), unit='dose_unit',
                                  status=('action',), category=('route',))},
    'allergies': {'items': dict(label=('substance', 'scope'), category=('category', 'scope', 'reaction_type'))},
    'procedures_devices': {'items': dict(label=('name',), status=('action',), category=('kind', 'laterality'))},
    'observations': {'items': dict(label=('name',), value=('numeric_value', 'text_value'), unit='unit',
                                   comparator='comparator', status=('status', 'flag'), category=('kind',))},
    'oncology': {'tumors': dict(label=('name',), status=('disease_status',), category=('disease_extent', 'laterality')),
                 'biomarkers': dict(label=('name',), value=('numeric_value', 'text_value'), unit='unit',
                                    status=('interpretation',), category=('origin',)),
                 'treatments': dict(label=('name',), status=('action',), category=('modality', 'intent', 'setting'))},
    'family_genetics': {'items': dict(label=('name',), category=('kind', 'origin'))},
    'reproductive_perinatal': {'items': dict(label=('event', 'text_value'), value=('numeric_value', 'text_value'),
                                             unit='unit', category=('event',))},
    'social_exposures': {'items': dict(label=('name',), status=('status',), category=('domain',))},
    'outcomes': {'items': dict(label=('event', 'text_value'), category=('event',))},
    'care_plans': {'items': dict(label=('action_text',), status=('action_status',), category=('plan_type',))},
}
CONTEXT_ENUMS = ('case_kind', 'species', 'multiple_index_patients', 'subject_state')
CONTEXT_TEXT = ('index_subject_description', 'care_setting', 'chief_complaint')

IMAGING = re.compile(r'\b(ct|mri|mr|ultrasound|ultrasonograph\w*|sonograph\w*|x ray|radiograph\w*|echocardiogra\w*|pet|'
                     r'scan|scintigraph\w*|angiogra\w*|tomograph\w*|imaging|doppler|eeg|ecg|electrocardiogra\w*|'
                     r'electromyogra\w*|emg|polysomnogra\w*|endoscop\w*|colonoscop\w*|bronchoscop\w*|mammogra\w*)\b')
INTERVENTION = re.compile(r'guided|biopsy|drain|ablation|emboli|stent|angioplasty|intervention|catheter|resection|excision')

MIN_EVIDENCE, MIN_NAME_WITH_EVIDENCE, MIN_NAME_ALONE, SAME_SPAN = 0.5, 0.15, 0.8, 0.8


class Item:
    __slots__ = ('task', 'coll', 'raw', 'label', 'key', 'spans', 'review_only', 'imaging')

    def __init__(self, task, coll, raw):
        spec = SPECS[task][coll]
        self.task, self.coll, self.raw = task, coll, raw
        self.label = ' '.join(str(raw.get(f)) for f in spec['label'] if informative(raw.get(f))) or ''
        self.key = norm(raw.get(spec['key'])) if spec.get('key') else None
        self.spans = evidence_spans(raw)
        self.review_only = raw.get('scoring') == 'review_only'
        n = norm(self.label)
        self.imaging = (task == 'observations' and raw.get('kind') == 'imaging') or (
            task in POOLED and bool(IMAGING.search(n)) and not INTERVENTION.search(n))

    @property
    def pool(self):
        return 'pooled' if self.task in POOLED else f'{self.task}/{self.coll}'


def items_of(patient):
    out = []
    for task, colls in SPECS.items():
        section = ((patient or {}).get('sections') or {}).get(task) or {}
        for coll in colls:
            out.extend(Item(task, coll, raw) for raw in section.get(coll) or [] if isinstance(raw, dict))
    return out


def _cross_ok(r, p):
    if r.task == p.task:
        return True
    pair = {r.task, p.task}
    if pair == {'observations', 'symptoms_function'}:
        return True
    return 'procedures_devices' in pair and (r.imaging or p.imaging)


def pair_weight(r, p):
    if r.pool != p.pool or (r.key is not None and r.key != p.key) or not _cross_ok(r, p):
        return 0.0
    ev = span_overlap(r.spans, p.spans)
    floor = 0.0 if ev >= MIN_EVIDENCE else MIN_NAME_ALONE
    nm = name_sim(r.label, p.label, floor) if r.label and p.label else (1.0 if not r.label and not p.label else 0.0)
    if not ((ev >= MIN_EVIDENCE and nm >= MIN_NAME_WITH_EVIDENCE) or nm >= MIN_NAME_ALONE
            or (ev >= SAME_SPAN and span_overlap(r.spans, p.spans, jaccard=True) >= SAME_SPAN)):
        return 0.0
    w = 0.55 * ev + 0.45 * nm + (0.1 if r.task == p.task else 0.0) + 0.05 * _detail_agreement(r, p)
    return w * (0.98 if r.review_only else 1.0)


def _detail_agreement(r, p):
    """Small tie-break for repeated items (same name and quote, e.g. serial measurements): value/time agreement."""
    checks = []
    rv, pv = _value(r.task, r.coll, r.raw), _value(p.task, p.coll, p.raw)
    if rv and pv:
        v = value_correct(rv, pv)
        if v is not None:
            checks.append(v)
    t = time_correct(r.raw.get('time'), p.raw.get('time'))
    if t is not None:
        checks.append(t)
    return sum(checks) / len(checks) if checks else 0.0


def match_items(ref_items, pred_items):
    by_pool = defaultdict(lambda: ([], []))
    for i, it in enumerate(ref_items):
        by_pool[it.pool][0].append(i)
    for j, it in enumerate(pred_items):
        by_pool[it.pool][1].append(j)
    pairs = {}
    for rs, ps in by_pool.values():
        for i in rs:
            for j in ps:
                w = pair_weight(ref_items[i], pred_items[j])
                if w > 0:
                    pairs[(i, j)] = w
    return assign(pairs)


# ---------------------------------------------------------------- attributes

def _enum(v):
    return norm(v) if isinstance(v, str) else v


def _value(task, coll, raw):
    f = SPECS[task][coll].get('value')
    return (raw.get(f[0]), raw.get(f[1])) if f else None


def value_correct(rv, pv):
    rn, rt = rv; pn, pt = pv
    rt = rt if informative(rt) else None; pt = pt if informative(pt) else None
    if rn is None and rt is None and pn is None and pt is None:
        return None
    if rn is not None:
        return num_equal(rn, pn) if pn is not None else any(num_equal(rn, x) for x in numbers(pt))
    if rt is not None:
        if pt is not None and texts_agree(rt, pt):
            return True
        return pn is not None and any(num_equal(pn, x) for x in numbers(rt))
    return False


def time_correct(rt, pt):
    rt, pt = rt or {}, pt or {}
    if not any(informative(x.get(k)) for x in (rt, pt) for k in ('text', 'date_iso')):
        return None
    if rt.get('date_iso') and rt.get('date_iso') == pt.get('date_iso'):
        return True
    return bool(rt.get('text') and pt.get('text') and texts_agree(rt['text'], pt['text']))


def texts_agree(a, b, threshold=0.6):
    """Short free-text agreement: numbers must agree when both sides state numbers, then name similarity."""
    na, nb = sorted(numbers(a)), sorted(numbers(b))
    if na and nb and (len(na) != len(nb) or not all(num_equal(x, y) for x, y in zip(na, nb))):
        return False
    return name_sim(a, b) >= threshold


def _comparator(raw, value):
    c = raw.get('comparator')
    if c in (None, 'unknown') and value and value[0] is not None:
        return '='
    return c if informative(c) else None


def compare_attributes(r, p):
    """{group: [(field_name, correct)]} for one matched pair; informative fields only."""
    rr, pp = r.raw, p.raw
    out = defaultdict(list)
    out['assertion'].append(('assertion', _enum(rr.get('assertion')) == _enum(pp.get('assertion'))))
    for g in ('subject', 'temporality'):
        if informative(rr.get(g)) or informative(pp.get(g)):
            out[g].append((g, _enum(rr.get(g)) == _enum(pp.get(g))))
    t = time_correct(rr.get('time'), pp.get('time'))
    if t is not None:
        out['time'].append(('time', t))
    rs, ps = SPECS[r.task][r.coll], SPECS[p.task][p.coll]
    rv, pv = _value(r.task, r.coll, rr), _value(p.task, p.coll, pp)
    if rv and pv:
        v = value_correct(rv, pv)
        if v is not None:
            out['value'].append((rs['value'][1].replace('text_', '').replace('_text', ''), v))
    if rs.get('unit') and ps.get('unit'):
        ru, pu = norm_unit(rr.get(rs['unit'])), norm_unit(pp.get(ps['unit']))
        if ru or pu:
            out['unit'].append((rs['unit'], ru == pu))
    if rs.get('comparator') and ps.get('comparator'):
        rc, pc = _comparator(rr, rv), _comparator(pp, pv)
        if rc or pc:
            out['comparator'].append(('comparator', rc == pc))
    if r.task == p.task and r.coll == p.coll:
        for g in ('status', 'category'):
            for f in rs.get(g) or ():
                if informative(rr.get(f)) or informative(pp.get(f)):
                    out[g].append((f, _enum(rr.get(f)) == _enum(pp.get(f))))
    return out


# ---------------------------------------------------------------- accumulation

def _prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
    return {'tp': tp, 'fp': fp, 'fn': fn, 'precision': p, 'recall': r, 'f1': f}


def _acc(c, t):
    return {'correct': c, 'total': t, 'accuracy': c / t if t else None}


class ClinicalScore:
    def __init__(self):
        self.pooled = defaultdict(Counter)   # task -> tp/fp/fn (section-lenient)
        self.strict = defaultdict(Counter)   # task -> tp/fp/fn (same-section only)
        self.groups = defaultdict(Counter)   # group -> correct/total
        self.task_groups = defaultdict(lambda: defaultdict(Counter))
        self.fields = defaultdict(Counter)   # task.field -> correct/total
        self.cross = Counter(); self.review = Counter(); self.redundant = Counter()
        self.examples = defaultdict(list)

    def _attr(self, task, group, field, ok):
        for c in (self.groups[group], self.task_groups[task][group], self.fields[f'{task}.{field}']):
            c['total'] += 1; c['correct'] += bool(ok)

    def add_patient(self, ref_patient, pred_patient):
        self._context(ref_patient, pred_patient)
        ref_items, pred_items = items_of(ref_patient), items_of(pred_patient)
        pairs = match_items(ref_items, pred_items)
        mr = {i for i, _ in pairs}; mp = {j for _, j in pairs}
        matched_ref_spans = [ref_items[i].spans for i, j in pairs if ref_items[i].task == 'observations']
        matched_pred_spans = [pred_items[j].spans for i, j in pairs if pred_items[j].task == 'observations']
        for i, j in pairs:
            r, p = ref_items[i], pred_items[j]
            if r.review_only:
                self.review['predictions_absorbed'] += 1; continue
            self.pooled[r.task]['tp'] += 1
            if r.task == p.task:
                self.strict[r.task]['tp'] += 1
            else:
                self.cross[f'{r.task}<->{p.task}'] += 1
                self.strict[r.task]['fn'] += 1; self.strict[p.task]['fp'] += 1
            for group, results in compare_attributes(r, p).items():
                for field, ok in results:
                    self._attr(r.task, group, field, ok)
                    if group == 'assertion' and not ok and len(self.examples['assertion']) < 20:
                        self.examples['assertion'].append({'task': r.task, 'reference': r.label, 'predicted': p.label,
                                                           'ref': r.raw.get('assertion'), 'pred': p.raw.get('assertion')})
        for i, r in enumerate(ref_items):
            if i in mr:
                continue
            if r.review_only:
                self.review['reference_not_required'] += 1
            elif r.task == 'procedures_devices' and r.imaging and any(span_overlap(r.spans, s) >= MIN_EVIDENCE for s in matched_ref_spans):
                self.redundant['reference_imaging_procedure'] += 1
            else:
                self.pooled[r.task]['fn'] += 1; self.strict[r.task]['fn'] += 1
        for j, p in enumerate(pred_items):
            if j in mp:
                continue
            if p.task == 'procedures_devices' and p.imaging and any(span_overlap(p.spans, s) >= MIN_EVIDENCE for s in matched_pred_spans):
                self.redundant['predicted_imaging_procedure'] += 1
            else:
                self.pooled[p.task]['fp'] += 1; self.strict[p.task]['fp'] += 1

    def _context(self, ref_patient, pred_patient):
        rc = ((ref_patient or {}).get('sections') or {}).get('case_context') or {}
        pc = ((pred_patient or {}).get('sections') or {}).get('case_context') or {}
        fields = CONTEXT_ENUMS + CONTEXT_TEXT
        rh = rc.get('scoring') != 'review_only' and any(informative(rc.get(f)) for f in fields)
        ph = any(informative(pc.get(f)) for f in fields)
        c = self.pooled['case_context']; s = self.strict['case_context']
        for d in (c, s):
            d['tp'] += rh and ph; d['fn'] += rh and not ph; d['fp'] += ph and not rh
        if rh and ph:
            for f in CONTEXT_ENUMS:
                if informative(rc.get(f)) or informative(pc.get(f)):
                    self._attr('case_context', 'category', f, _enum(rc.get(f)) == _enum(pc.get(f)))
            for f in CONTEXT_TEXT:
                if informative(rc.get(f)) or informative(pc.get(f)):
                    ok = bool(informative(rc.get(f)) and informative(pc.get(f)) and name_sim(rc[f], pc[f]) >= 0.5)
                    self._attr('case_context', 'value', f, ok)

    def result(self):
        def total(d):
            t = Counter()
            for v in d.values():
                t.update(v)
            return _prf(t['tp'], t['fp'], t['fn'])
        by_task = {}
        for task in TASKS:
            p, s = self.pooled[task], self.strict[task]
            by_task[task] = {**_prf(p['tp'], p['fp'], p['fn']), 'strict_section': _prf(s['tp'], s['fp'], s['fn']),
                             'attributes': {g: _acc(c['correct'], c['total']) for g, c in sorted(self.task_groups[task].items())}}
        f1s = [v['f1'] for v in by_task.values() if v['f1'] is not None]
        all_c = sum(c['correct'] for c in self.groups.values()); all_t = sum(c['total'] for c in self.groups.values())
        return {**total(self.pooled), 'strict_section': total(self.strict),
                'macro_f1_over_tasks': sum(f1s) / len(f1s) if f1s else None,
                'attribute_accuracy': _acc(all_c, all_t),
                'per_attribute': {g: _acc(self.groups[g]['correct'], self.groups[g]['total']) for g in ATTRIBUTE_GROUPS},
                'per_field': {k: _acc(v['correct'], v['total']) for k, v in sorted(self.fields.items())},
                'by_task': by_task,
                'cross_section_matches': dict(self.cross),
                'redundant_imaging_procedures': dict(self.redundant),
                'review_only': {'predictions_absorbed': self.review['predictions_absorbed'],
                                'reference_not_required': self.review['reference_not_required']},
                'assertion_disagreements_sample': self.examples['assertion'],
                'definition': ('One-to-one matching per aligned patient: evidence-quote overlap with name similarity as '
                               'tie-break; observations/symptoms_function/procedures_devices pooled (section-lenient '
                               'headline, strict_section alongside); review_only reference items neither TP/FP nor required.')}
