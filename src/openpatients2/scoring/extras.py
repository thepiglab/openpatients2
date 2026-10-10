"""Patient alignment, roster, timeline, summary, figure, supplement and series scores."""
from __future__ import annotations

from collections import Counter

from .assign import assign
from .clinical import SPECS, _acc, _prf, items_of, texts_agree, value_correct
from .text import token_dice, evidence_spans, informative, name_sim, norm, norm_figure_id, norm_panel, span_overlap

REVIEWED = 'agent_and_parent_reviewed'


# ---------------------------------------------------------------- patients

def _fingerprint(patient):
    fp = set()
    for it in items_of(patient):
        fp.add((it.task, norm(it.label)))
        fp.update(q for _, _, q in it.spans)
    return fp


def _demographics(patient):
    out = {}
    for it in ((patient.get('sections') or {}).get('demographics') or {}).get('items') or []:
        out.setdefault(norm(it.get('attribute')), (it.get('numeric_value'), it.get('text_value')))
    return out


def patient_similarity(rp, pp, rfp=None, pfp=None):
    ident = span_overlap(evidence_spans({'identity_evidence': rp.get('identity_evidence')}),
                         evidence_spans({'identity_evidence': pp.get('identity_evidence')}))
    label = name_sim(rp.get('label') or '', pp.get('label') or '')
    rd, pd = _demographics(rp), _demographics(pp)
    shared = [k for k in rd if k in pd]
    demo = sum(bool(value_correct(rd[k], pd[k])) for k in shared) / len(shared) if shared else 0.0
    rfp = rfp if rfp is not None else _fingerprint(rp); pfp = pfp if pfp is not None else _fingerprint(pp)
    content = len(rfp & pfp) / len(rfp | pfp) if rfp | pfp else 0.0
    return 0.25 * ident + 0.15 * label + 0.15 * demo + 0.45 * content


def align_patients(ref_patients, pred_patients, threshold=0.1):
    """[(ref_or_None, pred_or_None, similarity)] aligned by identity evidence and content, not id strings."""
    if len(ref_patients) == 1 and len(pred_patients) == 1:
        return [(ref_patients[0], pred_patients[0], patient_similarity(ref_patients[0], pred_patients[0]))]
    rf = [_fingerprint(p) for p in ref_patients]; pf = [_fingerprint(p) for p in pred_patients]
    sims = {(i, j): patient_similarity(r, p, rf[i], pf[j])
            for i, r in enumerate(ref_patients) for j, p in enumerate(pred_patients)}
    pairs = assign({k: v for k, v in sims.items() if v >= threshold})
    mr = {i for i, _ in pairs}; mp = {j for _, j in pairs}
    out = [(ref_patients[i], pred_patients[j], sims[(i, j)]) for i, j in pairs]
    out += [(r, None, 0.0) for i, r in enumerate(ref_patients) if i not in mr]
    out += [(None, p, 0.0) for j, p in enumerate(pred_patients) if j not in mp]
    return out


class RosterScore:
    def __init__(self):
        self.c = Counter(); self.sims = []

    def add(self, aligned, n_ref, n_pred):
        self.c['articles'] += 1; self.c['count_exact'] += n_ref == n_pred
        for r, p, s in aligned:
            self.c['tp'] += bool(r and p); self.c['fn'] += bool(r and not p); self.c['fp'] += bool(p and not r)
            if r and p:
                self.sims.append(s)
                for f in ('species', 'non_human_patient'):
                    if informative(r.get(f)) or informative(p.get(f)):
                        self.c[f'{f}_total'] += 1; self.c[f'{f}_correct'] += r.get(f) == p.get(f)
                self.c['label_sim_sum'] += name_sim(r.get('label') or '', p.get('label') or '')

    def result(self):
        c = self.c
        return {**_prf(c['tp'], c['fp'], c['fn']),
                'patient_count_exact': _acc(c['count_exact'], c['articles']),
                'species_accuracy': _acc(c['species_correct'], c['species_total']),
                'non_human_flag_accuracy': _acc(c['non_human_patient_correct'], c['non_human_patient_total']),
                'mean_alignment_similarity': sum(self.sims) / len(self.sims) if self.sims else None,
                'mean_label_similarity': c['label_sim_sum'] / c['tp'] if c['tp'] else None}


# ---------------------------------------------------------------- timeline / summary

def _match_text_objects(refs, preds, text_key, min_ev=0.5, min_name=0.7):
    rs = [evidence_spans(x) for x in refs]; ps = [evidence_spans(x) for x in preds]
    pairs = {}
    for i, r in enumerate(refs):
        for j, p in enumerate(preds):
            ev = span_overlap(rs[i], ps[j]); nm = token_dice(r.get(text_key) or '', p.get(text_key) or '')
            if ev >= min_ev or nm >= min_name:
                pairs[(i, j)] = 0.7 * ev + 0.3 * nm + 1e-6
    return assign(pairs)


_SWAP = {'after': 'before'}
_SYMMETRIC = {'overlaps', 'same_time', 'simultaneous'}


def _edge(a, b, rel):
    rel = norm(rel).replace(' ', '_')
    if rel in _SWAP:
        a, b, rel = b, a, _SWAP[rel]
    if rel in _SYMMETRIC:
        a, b = sorted((str(a), str(b)))
    return (a, b, rel)


class TimelineScore:
    def __init__(self):
        self.ev = Counter(); self.edges = Counter(); self.unlabeled = Counter(); self.attr = Counter()

    def add(self, ref_patient, pred_patient):
        rt = (ref_patient or {}).get('timeline') or {}; pt = (pred_patient or {}).get('timeline') or {}
        re_, pe = rt.get('events') or [], pt.get('events') or []
        pairs = _match_text_objects(re_, pe, 'description')
        self.ev['tp'] += len(pairs); self.ev['fn'] += len(re_) - len(pairs); self.ev['fp'] += len(pe) - len(pairs)
        idmap = {pe[j].get('event_id'): re_[i].get('event_id') for i, j in pairs}
        for i, j in pairs:
            r, p = re_[i], pe[j]
            for f in ('kind', 'occurrence'):
                self.attr[f'{f}_total'] += 1; self.attr[f'{f}_correct'] += norm(r.get(f)) == norm(p.get(f))
            rtimes = [t.get('text') for t in r.get('times') or [] if t.get('text')]
            ptimes = [t.get('text') for t in p.get('times') or [] if t.get('text')]
            if rtimes or ptimes:
                self.attr['time_total'] += 1
                self.attr['time_correct'] += any(texts_agree(a, b) for a in rtimes for b in ptimes)
        redges = Counter(_edge(e.get('from_event_id'), e.get('to_event_id'), e.get('relation')) for e in rt.get('edges') or [])
        pedges = Counter(_edge(idmap.get(e.get('from_event_id'), f"pred:{e.get('from_event_id')}"),
                               idmap.get(e.get('to_event_id'), f"pred:{e.get('to_event_id')}"), e.get('relation'))
                         for e in pt.get('edges') or [])
        tp = sum((redges & pedges).values())
        self.edges['tp'] += tp; self.edges['fn'] += sum(redges.values()) - tp; self.edges['fp'] += sum(pedges.values()) - tp
        ru = Counter(frozenset(map(str, e[:2])) for e in redges.elements())
        pu = Counter(frozenset(map(str, e[:2])) for e in pedges.elements())
        tpu = sum((ru & pu).values())
        self.unlabeled['tp'] += tpu; self.unlabeled['fn'] += sum(ru.values()) - tpu; self.unlabeled['fp'] += sum(pu.values()) - tpu

    def result(self):
        a = self.attr
        return {'events': _prf(self.ev['tp'], self.ev['fp'], self.ev['fn']),
                'event_attributes': {f: _acc(a[f'{f}_correct'], a[f'{f}_total']) for f in ('kind', 'occurrence', 'time')},
                'relations': _prf(self.edges['tp'], self.edges['fp'], self.edges['fn']),
                'relations_unlabeled': _prf(self.unlabeled['tp'], self.unlabeled['fp'], self.unlabeled['fn']),
                'definition': 'Events matched by evidence overlap + description; relations compared through the event '
                              'matching (after->before swapped, overlaps/same_time symmetric).'}


class SummaryScore:
    def __init__(self):
        self.c = Counter()

    def add(self, ref_patient, pred_patient):
        rc = ((ref_patient or {}).get('summary') or {}).get('claims') or []
        pc = ((pred_patient or {}).get('summary') or {}).get('claims') or []
        n = len(_match_text_objects(rc, pc, 'text'))
        self.c['tp'] += n; self.c['fn'] += len(rc) - n; self.c['fp'] += len(pc) - n

    def result(self):
        return _prf(self.c['tp'], self.c['fp'], self.c['fn'])


# ---------------------------------------------------------------- figures

PRIVACY = (('burned_in_any', lambda x: x.get('burned_in_identifiers') not in (None, 'none')),
           ('direct_identifiers', lambda x: x.get('burned_in_identifiers') == 'direct_identifiers'),
           ('sensitive_anatomy', lambda x: bool(x.get('sensitive_anatomy'))),
           ('identifiable_face', lambda x: bool(x.get('identifiable_face'))))


def _panels(row):
    out = []
    for f in row.get('figures') or []:
        for p in f.get('panels') or []:
            out.append((norm_figure_id(f.get('figure_id')), norm_panel(p.get('panel')), p))
    return out


def _panel_text(p):
    v = p.get('visual') or {}
    return ' '.join(str(v.get(k) or '') for k in ('general_description', 'detailed_description'))


def match_panels(rp, pp):
    """Exact normalised (figure, panel) key first; leftovers in the same figure by description similarity."""
    pairs = []; used_r = set(); used_p = set()
    index = {}
    for j, (f, k, _) in enumerate(pp):
        index.setdefault((f, k), []).append(j)
    for i, (f, k, _) in enumerate(rp):
        for j in index.get((f, k), []):
            if j not in used_p:
                pairs.append((i, j)); used_r.add(i); used_p.add(j); break
    cand = {}
    for i, (f, _, r) in enumerate(rp):
        if i in used_r:
            continue
        for j, (g, _, p) in enumerate(pp):
            if j not in used_p and f == g:
                s = name_sim(_panel_text(r), _panel_text(p))
                if s >= 0.3:
                    cand[(i, j)] = s
    pairs += assign(cand)
    return pairs


class FigureScore:
    def __init__(self):
        self.det = Counter(); self.cat = Counter(); self.incl = Counter(); self.attr = Counter()
        self.priv = {k: Counter() for k, _ in PRIVACY}; self.fig = Counter(); self.normalised_only = 0

    def add(self, ref, pred, pid_map):
        rp, pp = _panels(ref), _panels(pred)
        pairs = match_panels(rp, pp)
        self.det['tp'] += len(pairs); self.det['fn'] += len(rp) - len(pairs); self.det['fp'] += len(pp) - len(pairs)
        by_r = {i: pp[j][2] for i, j in pairs}; mp = {j for _, j in pairs}
        for i, j in pairs:
            if rp[i][2].get('panel') != pp[j][2].get('panel'):
                self.normalised_only += 1
        for i, (_, _, r) in enumerate(rp):
            p = by_r.get(i)
            rehr, pehr = r.get('ehr') or {}, (p or {}).get('ehr') or {}
            if rehr.get('status') == REVIEWED:
                self.cat['total'] += 1; self.cat['correct'] += bool(p and pehr.get('ehr_category') == rehr.get('ehr_category'))
                t, g = bool(rehr.get('ehr_includable')), bool(p and pehr.get('ehr_includable'))
                self.incl['tp'] += t and g; self.incl['fp'] += g and not t; self.incl['fn'] += t and not g
            rpriv = r.get('privacy') or {}
            if rpriv.get('status') == REVIEWED:
                ppriv = (p or {}).get('privacy') or {}
                for name, fn in PRIVACY:
                    t, g = fn(rpriv), fn(ppriv)
                    self.priv[name]['tp'] += t and g; self.priv[name]['fp'] += g and not t; self.priv[name]['fn'] += t and not g
            ra = r.get('attribution') or {}
            if ra.get('assignments') or r.get('patient_ids'):
                pa = (p or {}).get('attribution') or {}
                mapped = {pid_map.get(x, ('unaligned', x)) for x in (p or {}).get('patient_ids') or []}
                self.attr['owners_total'] += 1; self.attr['owners_correct'] += bool(p) and mapped == set(r.get('patient_ids') or [])
                for f in ('scopes', 'subjects'):
                    self.attr[f'{f}_total'] += 1
                    self.attr[f'{f}_correct'] += bool(p) and set(pa.get(f) or []) == set(ra.get(f) or [])
        for j, (_, _, p) in enumerate(pp):
            if j not in mp and (p.get('ehr') or {}).get('ehr_includable'):
                self.incl['fp'] += 1
        rfig = {norm_figure_id(f.get('figure_id')): f for f in ref.get('figures') or []}
        pfig = {norm_figure_id(f.get('figure_id')): f for f in pred.get('figures') or []}
        for fid, f in rfig.items():
            rl = f.get('figure_ehr_label') or {}
            if rl.get('ehr_category'):
                pl = (pfig.get(fid) or {}).get('figure_ehr_label') or {}
                self.fig['total'] += 1; self.fig['correct'] += pl.get('ehr_category') == rl.get('ehr_category')

    def result(self):
        a = self.attr
        return {'panel_detection': _prf(self.det['tp'], self.det['fp'], self.det['fn']),
                'panels_matched_only_after_label_normalisation': self.normalised_only,
                'panel_ehr_category': _acc(self.cat['correct'], self.cat['total']),
                'panel_ehr_includable': _prf(self.incl['tp'], self.incl['fp'], self.incl['fn']),
                'figure_ehr_category': _acc(self.fig['correct'], self.fig['total']),
                'privacy_flags': {k: _prf(v['tp'], v['fp'], v['fn']) for k, v in self.priv.items()},
                'attribution': {'patient_owners_exact': _acc(a['owners_correct'], a['owners_total']),
                                'scopes_exact': _acc(a['scopes_correct'], a['scopes_total']),
                                'subjects_exact': _acc(a['subjects_correct'], a['subjects_total'])}}


# ---------------------------------------------------------------- supplements / series

class SupplementScore:
    def __init__(self):
        self.cls = Counter(); self.incl = Counter()

    def add(self, ref, pred):
        rfiles = {(s['supplement_id'], f['url']): f for s in ref.get('supplements') or []
                  if (s.get('manifest') or {}).get('status') == REVIEWED for f in s['manifest'].get('files') or []}
        pfiles = {(s['supplement_id'], f['url']): f for s in pred.get('supplements') or []
                  for f in (s.get('manifest') or {}).get('files') or []}
        for key, rf in rfiles.items():
            pf = pfiles.get(key)
            self.cls['total'] += 1; self.cls['correct'] += bool(pf and pf.get('content_class') == rf.get('content_class'))
            t, g = bool(rf.get('ehr_includable')), bool(pf and pf.get('ehr_includable'))
            self.incl['tp'] += t and g; self.incl['fp'] += g and not t; self.incl['fn'] += t and not g

    def result(self):
        return {'class': _acc(self.cls['correct'], self.cls['total']),
                'ehr_includable': _prf(self.incl['tp'], self.incl['fp'], self.incl['fn'])}


class SeriesScore:
    def __init__(self):
        self.c = Counter(); self.ignored = 0

    def add(self, ref, pred, pid_map):
        rms = ref.get('measurement_series') or {}
        if rms.get('status') != REVIEWED:
            return
        def keyed(series, mapper):
            return Counter((p.get('value_cell_id'), tuple(sorted(map(str, (mapper(x) for x in s.get('patient_ids') or [])))))
                           for s in series for p in s.get('points') or [])
        rs = rms.get('series') or []
        include = keyed([s for s in rs if s.get('scoring') != 'review_only'], lambda x: x)
        review = keyed([s for s in rs if s.get('scoring') == 'review_only'], lambda x: x)
        predk = keyed((pred.get('measurement_series') or {}).get('series') or [], lambda x: pid_map.get(x, ('unaligned', x)))
        tp = sum((include & predk).values())
        rest = predk - include
        self.ignored += sum((rest & review).values())
        self.c['tp'] += tp; self.c['fn'] += sum(include.values()) - tp; self.c['fp'] += sum((rest - review).values())

    def result(self):
        return {**_prf(self.c['tp'], self.c['fp'], self.c['fn']), 'review_only_points_ignored': self.ignored}


__all__ = ['SPECS', 'align_patients', 'RosterScore', 'TimelineScore', 'SummaryScore', 'FigureScore',
           'SupplementScore', 'SeriesScore']
