"""Migrate reference data to the shared annotation rules (src/openpatients2/rules/annotation_rules.yaml).

Scope: annotations/ in dense50 + dense500, plus drafts/ that have no promoted annotation (a draft
byte-identical to its annotation is kept identical). Idempotent: a second run changes nothing.
Dry run by default; `--apply` writes. Nothing is signed: every changed article needs the re-sign
campaign (claude_prompts/RESIGN_BRIEF.md).

Mechanical migrations (applied directly):
  M01 case_context      multi_patient/true -> clinical_case|animal + false            (CTX-PER-PATIENT)
  M02 comparator        observations numeric_value with comparator absent/unknown -> '='
                        (comparator wording next to the number -> needs_review)      (OBS-COMPARATOR)
  M03 age_text          numeric age without text_value -> the unique literal age phrase in
                        the fact's quotes (none/several -> needs_review); a text_value that is
                        not an exact substring of the quotes -> the quote's own span (case,
                        Unicode hyphens U+2010 etc. and spacing copied verbatim)    (DEMO-AGE-TEXT)
  M06 never_users       social status=never + assertion=absent -> present            (SOC-NEVER)
  M07 not_performed     observation status=not_performed + assertion=absent -> present (OBS-NOT-PERFORMED)
  M12 time_anchor       fields.time.anchor not literal (case-insensitive) in source -> null (TIME-LITERAL)
  M13 occurrence        timeline occurrence 'uncertain' -> 'unknown'                (TIMELINE-ENUMS)
Judgment migrations (applied only where the source wording is unambiguous, else needs_review):
  M04 absent_confirmed  absent condition with confirmed/provisional/differential: exclusion
                        wording -> refuted; denied/absent-history wording -> unknown  (ASSERT-ABSENT)
  M05 normal_flag       observation whose text_value is exactly normal/unremarkable/WNL -> flag
                        normal; other normal-worded results -> needs_review           (OBS-FLAG)
  M08 benign_tumor      benign-looking oncology tumors -> needs_review only           (ONC-BENIGN)
  M09 vital_status      living_at_presentation with an included, supported index-patient
                        death (assertion=present) -> deceased_during_course          (CTX-VITAL-STATUS)
  M10 panel_labels      annotation figure panel labels not printed in the caption -> needs_review;
                        visual descriptions with '?' outside quotes -> needs_review  (FIG-LABELS)
  M11 comparison_panels figure EHR labels whose rationale says normal/healthy comparison or
                        control (not includable) -> non_clinical_diagnostic_aid      (FIG-EHR-COMPARISON)

After an annotation changes, the schema target is regenerated into schema-drafts/ and merged into
schema-submissions/ keeping the reviewers' structural edits (event kinds/time classifications,
roster count/completeness, attribution subjects/rationales). Because the timeline vocabulary was
unified, every article's schema target is regenerated (schema fingerprints changed).

  PYTHONPATH=src:benchmarks/dense500 .venv/bin/python benchmarks/dense500/migrations/semantic_rules_2026_10.py \
      [--apply] [--report PATH] [--skip ID ...] [--article ID ... [--allow-recent]]
Outputs: the report (per article: changes by migration, needs_review items, schema status) and, with
--apply, R/needs-review/<ID>.json for articles with open items (resolutions already recorded there
are preserved across runs).
"""
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time

HERE = Path(__file__).resolve().parent
DENSE = HERE.parent
sys.path.insert(0, str(DENSE))
ROOTS = [DENSE.parent / 'dense50', DENSE]
QUIET_SECONDS = 15 * 60
VERSION = 'semantic-rules-2026-10/1'

from migrate_case_context import STYLES, diff_paths, migrate_annotation as migrate_case_context, style_of  # noqa: E402
from openpatients2.semantic_rules import (BENIGN_TUMOR, HISTORY_DENIAL, LEGACY_OCCURRENCE, MALIGNANT,  # noqa: E402
                                          HYPHENS, NEVER_CUE, NORMAL_CUE, NORMAL_RESULT, POSITIONAL_LABEL,
                                          REFUTED_CUE, literal_span, visual_question_marks)

# Printed comparators only; 'approximately'/'~' are not comparators (the value stays '=' and the
# wording stays in the evidence/text_value).
COMPARATOR_CUE = re.compile(r'(<|>|≤|≥|=<|>=|<=|less than|greater than|more than|fewer than|up to|above|below|'
                            r'over|under|exceed\w*|at least|at most)\s*$', re.I)
AGE_UNITS = r'(?:years?|yrs?|y|months?|mos?|weeks?|wks?|days?|d|hours?|hrs?|h)'
HYPHEN = f'[{HYPHENS}]'


def _number_forms(value):
    forms = {format(value, 'g')}
    if float(value).is_integer():
        forms |= {str(int(value)), f'{int(value):,}'}
    else:
        forms.add(str(value))
    return sorted(forms, key=len, reverse=True)


def _quotes(fact):
    return '\n'.join(e['quote'] for e in fact['evidence'])


class Ctx:
    def __init__(self, root, aid, source):
        self.root, self.aid, self.source = root, aid, source
        self.text = '\n'.join(s['text'] for s in source['segments']).casefold()
        self.changes = []; self.review = []

    def change(self, migration, rule, where, path, old, new):
        self.changes.append({'migration': migration, 'rule_id': rule, 'target': where, 'path': path, 'old': old, 'new': new})

    def needs(self, migration, rule, where, path, current, question, evidence=None, suggestion=None):
        self.review.append({'key': f'{rule}:{where}:{path}', 'migration': migration, 'rule_id': rule, 'target': where,
                            'path': path, 'current': current, 'question': question,
                            'suggestion': suggestion, 'evidence': evidence})


def facts(annotation):
    for p in annotation['patients']:
        for f in p['facts']:
            yield p, f


def m01_case_context(a, ctx):
    new, changes = migrate_case_context(a)
    for c in changes:
        for k in ('case_kind', 'multiple_index_patients'):
            if c['old'][k] != c['new'][k]:
                ctx.change('M01_case_context', 'CTX-PER-PATIENT', c['fact_id'], f'fields.{k}', c['old'][k], c['new'][k])
    a['patients'] = new['patients']


def m02_comparator(a, ctx):
    for p, f in facts(a):
        fl = f['fields']
        if f['task'] != 'observations' or fl.get('numeric_value') is None or fl.get('comparator', 'unknown') != 'unknown':
            continue
        quotes = _quotes(f)
        cues = []
        for form in _number_forms(fl['numeric_value']):
            for m in re.finditer(r'(?<![\d.,])' + re.escape(form) + r'(?![\d])', quotes):
                cues.append(bool(COMPARATOR_CUE.search(quotes[max(0, m.start() - 16):m.start()])))
        if (cues and all(cues)) or (not cues and re.search(r'[<>≤≥]', quotes)):
            ctx.needs('M02_comparator', 'OBS-COMPARATOR', f['fact_id'], 'fields.comparator', fl.get('comparator', '<absent>'),
                      f"numeric_value={fl['numeric_value']}: a comparator word/symbol is printed near the value; set the printed "
                      "comparator (<, <=, >, >=) or '=' if it does not apply to this value", quotes[:400])
            continue
        ctx.change('M02_comparator', 'OBS-COMPARATOR', f['fact_id'], 'fields.comparator', fl.get('comparator', '<absent>'), '=')
        fl['comparator'] = '='


def m03_age_text(a, ctx):
    for p, f in facts(a):
        fl = f['fields']
        if f['task'] != 'demographics' or not str(fl.get('attribute', '')).startswith('age_') or fl.get('numeric_value') is None:
            continue
        quotes = _quotes(f); found = set()
        if fl.get('text_value'):
            text = fl['text_value']
            if text in quotes:
                continue
            exact = literal_span(text, quotes)   # exact substring of the quote, its own code points
            if exact is not None:
                ctx.change('M03_age_text', 'DEMO-AGE-TEXT', f['fact_id'], 'fields.text_value', text, exact)
                fl['text_value'] = exact
            else:
                ctx.needs('M03_age_text', 'DEMO-AGE-TEXT', f['fact_id'], 'fields.text_value', text,
                          f'age text_value {text!r} does not occur in the evidence quotes; copy the literal age phrase '
                          'verbatim from the quote', quotes[:400])
            continue
        for n in _number_forms(fl['numeric_value']):
            n = re.escape(n)
            for pattern in (rf'(?<![\d.]){n}(?:\s*{HYPHEN}\s*|\s+){AGE_UNITS}(?:\s*{HYPHEN}\s*|\s+)old\b',
                            rf'\baged?\s+{n}(?:\s+{AGE_UNITS}(?:\s+old)?)?(?![\d.])',
                            rf'(?<![\d.]){n}\s*{AGE_UNITS}\s+of\s+age\b',
                            rf'\bage(?:\s+of)?\s*:?\s*{n}(?:\s*{AGE_UNITS})?(?![\d.])',
                            rf'(?<![\d.]){n}\s*{HYPHEN}?\s*(?:y/o|yo|y\.o\.)'):
                found |= {m.group(0) for m in re.finditer(pattern, quotes, re.I)}
        if not found:
            # A table row whose cell is exactly the age ('1 | 81 | Female | ...'): the cell is the literal phrase.
            cells = [c.strip() for q in quotes.split('\n') if '|' in q for c in q.split('|')]
            forms = set(_number_forms(fl['numeric_value']))
            if sum(c in forms for c in cells) == 1:
                found = {next(c for c in cells if c in forms)}
        if len(found) == 1:
            phrase = found.pop()
            ctx.change('M03_age_text', 'DEMO-AGE-TEXT', f['fact_id'], 'fields.text_value', fl.get('text_value', '<absent>'), phrase)
            fl['text_value'] = phrase
        else:
            ctx.needs('M03_age_text', 'DEMO-AGE-TEXT', f['fact_id'], 'fields.text_value', None,
                      f"numeric age {fl['numeric_value']} needs text_value = the literal source phrase stating it"
                      + (f'; candidates {sorted(found)}' if found else '; no unambiguous age phrase in the quotes'), quotes[:400])


def m04_absent_confirmed(a, ctx):
    for p, f in facts(a):
        fl = f['fields']
        if f['task'] != 'conditions' or fl.get('assertion') != 'absent' or fl.get('verification_status') not in ('confirmed', 'provisional', 'differential'):
            continue
        quotes = _quotes(f)
        refuted, denial = bool(REFUTED_CUE.search(quotes)), bool(HISTORY_DENIAL.search(quotes))
        if refuted != denial and '|' not in quotes:
            new = 'refuted' if refuted else 'unknown'
            ctx.change('M04_absent_confirmed', 'ASSERT-ABSENT', f['fact_id'], 'fields.verification_status', fl['verification_status'], new)
            fl['verification_status'] = new
        else:
            ctx.needs('M04_absent_confirmed', 'ASSERT-ABSENT', f['fact_id'], 'fields.verification_status', fl['verification_status'],
                      f"absent condition {fl.get('name')!r} cannot be {fl['verification_status']}: use refuted (excluded by workup), "
                      "unknown (denied/absent history), or reconsider whether this is a patient fact at all (cohort/background "
                      "sentence, table cell, outcome rather than condition)", quotes[:400])


def m05_normal_flag(a, ctx):
    for p, f in facts(a):
        fl = f['fields']
        text = fl.get('text_value')
        if f['task'] != 'observations' or not text or fl.get('flag', 'unknown') not in ('unknown', 'negative'):
            continue
        if NORMAL_RESULT.match(text) and fl.get('status', 'unknown') in ('resulted', 'historical', 'unknown'):
            ctx.change('M05_normal_flag', 'OBS-FLAG', f['fact_id'], 'fields.flag', fl.get('flag', '<absent>'), 'normal')
            fl['flag'] = 'normal'
        elif NORMAL_CUE.search(text) and not re.search('abnormal', text, re.I):
            ctx.needs('M05_normal_flag', 'OBS-FLAG', f['fact_id'], 'fields.flag', fl.get('flag', '<absent>'),
                      f"text_value {text[:120]!r} uses normal wording; set flag=normal if the source calls THIS result normal, "
                      "keep negative only for a qualitative negative test, or split mixed findings", _quotes(f)[:400],
                      suggestion='normal')


def m06_never_users(a, ctx):
    for p, f in facts(a):
        fl = f['fields']
        if f['task'] != 'social_exposures' or fl.get('status') != 'never':
            continue
        if not NEVER_CUE.search(_quotes(f)):
            # 'No known contact with tuberculosis' / 'denies ...' is not never-use: a person decides.
            ctx.needs('M06_never_users', 'SOC-NEVER', f['fact_id'], 'fields.status', 'never',
                      "status=never without explicit never-wording: 'no known contact/exposure' or 'denies' is "
                      "assertion=absent with status unknown (or current for a scoped current denial)", _quotes(f)[:400],
                      suggestion='status=unknown, assertion=absent')
        elif fl.get('assertion') == 'absent':
            ctx.change('M06_never_users', 'SOC-NEVER', f['fact_id'], 'fields.assertion', 'absent', 'present')
            fl['assertion'] = 'present'


def m07_not_performed(a, ctx):
    for p, f in facts(a):
        fl = f['fields']
        if f['task'] == 'observations' and fl.get('status') == 'not_performed' and fl.get('assertion') == 'absent':
            ctx.change('M07_not_performed', 'OBS-NOT-PERFORMED', f['fact_id'], 'fields.assertion', 'absent', 'present')
            fl['assertion'] = 'present'


def m08_benign_tumor(a, ctx):
    for p, f in facts(a):
        name = f['fields'].get('name') or ''
        if f['task'] == 'oncology' and f.get('collection') == 'tumors' and BENIGN_TUMOR.search(name) and not MALIGNANT.search(name):
            ctx.needs('M08_benign_tumor', 'ONC-BENIGN', f['fact_id'], 'task', 'oncology.tumors',
                      f"{name!r} looks benign: move it to conditions (and drop/relink oncology biomarkers/treatments that cite "
                      "its tumor_ref) unless the source evaluates it as a possible malignancy", _quotes(f)[:400],
                      suggestion='conditions')


def m09_vital_status(a, ctx):
    for p in a['patients']:
        deaths = [f for f in p['facts'] if f['task'] == 'outcomes' and f['fields'].get('event') == 'death'
                  and f['fields'].get('assertion') == 'present' and f['fields'].get('subject') == 'index_patient']
        for f in p['facts']:
            if f['task'] != 'case_context' or f['fields'].get('subject_state') != 'living_at_presentation' or not deaths:
                continue
            clear = [d for d in deaths if d['scoring'] == 'include' and d['certainty'] == 'supported']
            if clear:
                ctx.change('M09_vital_status', 'CTX-VITAL-STATUS', f['fact_id'], 'fields.subject_state',
                           'living_at_presentation', 'deceased_during_course')
                f['fields']['subject_state'] = 'deceased_during_course'
            else:
                ctx.needs('M09_vital_status', 'CTX-VITAL-STATUS', f['fact_id'], 'fields.subject_state', 'living_at_presentation',
                          'a death of this patient is recorded but not as a supported included fact; decide subject_state',
                          _quotes(deaths[0])[:400])


def _caption_labels(caption):
    labels = set()
    for m in re.finditer(r'(?<![A-Za-z0-9])\(?([A-Za-z])\s*[–\-]\s*([A-Za-z])\)?(?![A-Za-z0-9])', caption):
        a, b = m.group(1), m.group(2)
        if a.isupper() == b.isupper() and ord(a) <= ord(b) and ord(b) - ord(a) < 12:
            labels |= {chr(c) for c in range(ord(a), ord(b) + 1)}
    return labels


def _label_printed(label, caption):
    label = label.strip()
    if not label or POSITIONAL_LABEL.match(label):
        return False   # caption position words ('left', 'upper right') are never printed labels
    if label in _caption_labels(caption):
        return True
    escaped = re.escape(label)
    return bool(re.search(r'(?<![A-Za-z0-9])\(?' + escaped + r'(?![A-Za-z0-9])', caption)
                or re.search(escaped + r'[:).]', caption))


def m10_panel_labels(a, ctx):
    captions = {(f.get('figure_key') or f['figure_id']): (f.get('label') or '') + ' ' + (f.get('caption') or '')
                for f in ctx.source.get('figures', [])}
    for fig in a['figures']:
        caption = captions.get(fig['figure_id'], '')
        for i, panel in enumerate(fig['panels']):
            if panel['panel'] is not None and not _label_printed(panel['panel'], caption):
                ctx.needs('M10_panel_labels', 'FIG-LABELS', fig['figure_id'], f'figures.panels[{i}].panel', panel['panel'],
                          'panel label is not printed in the caption: keep it only if it is printed on the image as a label '
                          '(check pixels); otherwise use the caption/printed label or merge into a whole-figure row',
                          caption[:300])
    visual = ctx.root / 'visual-submissions' / f'{ctx.aid}.json'
    if visual.exists():
        for f in json.loads(visual.read_text())['figures']:
            v = (f.get('analysis') or {}).get('visual')
            if not v:
                continue
            for scope in [v, *v['panels']]:
                for k in ('general_description', 'detailed_description'):
                    if visual_question_marks(scope.get(k)):
                        ctx.needs('M10_panel_labels', 'FIG-LABELS', f['figure_id'], f"visual {scope.get('panel') or 'figure'}.{k}",
                                  None, 'a "?" outside quotation marks: quote the printed label (e.g. “Mineralocorticoid excess?”) '
                                  'or move the uncertainty to limitations, then re-sign the visual review and dependent '
                                  'figure-EHR/privacy receipts', scope[k][:300])


COMPARISON = re.compile(r'\bnormal[- ]\w+ comparison|\bnormal comparison|\bnormal[- ]\w+ control|\bnormal control|'
                        r'\bhealthy control|\bcontrol specimen|\breference (image|specimen)', re.I)


def m11_comparison_panels(ctx, apply):
    path = ctx.root / 'figure-ehr-labels' / f'{ctx.aid}.json'
    if not path.exists():
        return None
    text = path.read_text(encoding='utf-8'); labels = json.loads(text); new = copy.deepcopy(labels)
    for fig in new['figures']:
        if fig.get('status') != 'labeled':
            continue
        changed = []
        for p in fig['panels']:
            label = p['label']
            if (COMPARISON.search(label.get('rationale') or '') and not label['ehr_includable']
                    and label['ehr_category'] != 'non_clinical_diagnostic_aid'):
                ctx.change('M11_comparison_panels', 'FIG-EHR-COMPARISON', f"{fig['figure_id']}/{p['panel']}",
                           'figure-ehr-labels.label.ehr_category', label['ehr_category'], 'non_clinical_diagnostic_aid')
                label['ehr_category'] = 'non_clinical_diagnostic_aid'
                label['override_reason'] = (None if p['default']['ehr_category'] == 'non_clinical_diagnostic_aid' else
                    'Normal-comparison specimen/image, not this patient\'s data (rule FIG-EHR-COMPARISON).')
                changed.append(p)
        if changed and len(changed) == len(fig['panels']) and fig['figure_label']['ehr_category'] != 'non_clinical_diagnostic_aid':
            ctx.change('M11_comparison_panels', 'FIG-EHR-COMPARISON', fig['figure_id'], 'figure-ehr-labels.figure_label.ehr_category',
                       fig['figure_label']['ehr_category'], 'non_clinical_diagnostic_aid')
            fig['figure_label']['ehr_category'] = 'non_clinical_diagnostic_aid'
    if new == labels:
        return None
    from openpatients2.figure_ehr import validate_labels
    visual = json.loads((ctx.root / 'visual-submissions' / f'{ctx.aid}.json').read_text())
    validate_labels(new, ctx.source, visual)
    if apply:
        path.write_text(STYLES[style_of(text) or 'i2'](new), encoding='utf-8')
    return 'figure-ehr-labels'


def m12_time_anchor(a, ctx):
    for p, f in facts(a):
        time = f['fields'].get('time')
        if isinstance(time, dict) and time.get('anchor') and time['anchor'].casefold() not in ctx.text:
            ctx.change('M12_time_anchor', 'TIME-LITERAL', f['fact_id'], 'fields.time.anchor', time['anchor'], None)
            time['anchor'] = None
        # A relation with nothing to relate to (no text, anchor or date) dangles: relation=unknown.
        if (isinstance(time, dict) and time.get('relation', 'unknown') != 'unknown'
                and not time.get('text') and not time.get('anchor') and not time.get('date_iso')):
            ctx.change('M12_time_anchor', 'TIME-LITERAL', f['fact_id'], 'fields.time.relation', time['relation'], 'unknown')
            time['relation'] = 'unknown'


def m13_occurrence(a, ctx):
    for p in a['patients']:
        for e in p['events']:
            if e.get('occurrence') in LEGACY_OCCURRENCE:
                new = LEGACY_OCCURRENCE[e['occurrence']]
                ctx.change('M13_occurrence', 'TIMELINE-ENUMS', e['event_id'], 'events.occurrence', e['occurrence'], new)
                e['occurrence'] = new


ANNOTATION_MIGRATIONS = (m01_case_context, m02_comparator, m03_age_text, m04_absent_confirmed, m05_normal_flag,
                         m06_never_users, m07_not_performed, m08_benign_tumor, m09_vital_status, m10_panel_labels,
                         m12_time_anchor, m13_occurrence)


def merge_submission(submission, regenerated):
    """Regenerated clinical content + the reviewers' permitted structural edits from the old submission."""
    from openpatients2.longitudinal import relative_order
    new = copy.deepcopy(regenerated)
    new['review_status'] = submission.get('review_status', regenerated.get('review_status'))
    # roster: reviewers may set the reported count/completeness and drop the pending notice.
    for key in ('reported_individual_count', 'roster_complete', 'limitations'):
        new['roster'][key] = copy.deepcopy(submission['roster'][key])
    for got, want in zip(submission['figure_attribution'], new['figure_attribution']):
        for x, y in zip(got['assignments'], want['assignments']):
            y['subject'] = x['subject']; y['rationale'] = x['rationale']
    old_fig = {f['figure_id']: f for f in submission['figure_attribution']}
    for got, want in zip(submission['patients'], new['patients']):
        if got['source']['patient_target']['patient_id'] != want['source']['patient_target']['patient_id']:
            raise ValueError('Patient order differs between submission and regenerated target')
        want['source']['patient_target'] = next(p for p in new['roster']['patients']
                                                if p['patient_id'] == want['source']['patient_target']['patient_id'])
        old_events = {e['event_id']: e for e in got['companions']['timeline_v2']['events']}
        for e in want['companions']['timeline_v2']['events']:
            old = old_events.get(e['event_id'])
            if old is None:
                continue
            e['kind'] = old['kind']
            if len(old['times']) == len(e['times']):
                for t, o in zip(e['times'], old['times']):
                    for field in ('kind', 'precision', 'calendar_value'):
                        t[field] = o[field]
        want['relative_timeline'] = relative_order(want['companions']['timeline_v2'])
        want['vision']['figure_attribution'] = [f for f in new['figure_attribution']
                                                if any(want['source']['patient_target']['patient_id'] in x['patient_ids'] for x in f['assignments'])]
        for key in set(got) - set(want):
            want[key] = copy.deepcopy(got[key])
    for key in set(submission) - set(new):
        new[key] = copy.deepcopy(submission[key])
    return new


def check_submission(merged, annotation, source):
    """schema_review.validate_submission, but with runtime gate errors reported instead of raised."""
    import schema_review
    original = schema_review.materialize
    schema_review.materialize = lambda a, s: {**original(a, s), 'source_gate_errors': []}
    try:
        schema_review.validate_submission({**merged, 'source_gate_errors': []}, annotation, source)
    finally:
        schema_review.materialize = original


def receipts_going_stale(root, aid, annotation_changed, promoted):
    out = []
    if annotation_changed and promoted and (root / 'reviews' / f'{aid}.json').exists():
        out.append('reviews (parent source review: annotation_sha256)')
    for role in ('agent', 'parent'):
        if (root / f'schema-{role}-reviews' / f'{aid}.json').exists():
            out.append(f'schema-{role}-reviews (annotation_sha256/target_sha256)')
    return out


def recent(root, aid, now):
    paths = [root / f / f'{aid}.json' for f in ('annotations', 'drafts', 'reviews', 'schema-submissions', 'schema-drafts',
             'schema-agent-reviews', 'schema-parent-reviews', 'visual-submissions', 'figure-ehr-labels')]
    return any(p.exists() and now - p.stat().st_mtime < QUIET_SECONDS for p in paths)


def write_needs_review(root, aid, items, apply):
    path = root / 'needs-review' / f'{aid}.json'
    old = json.loads(path.read_text()) if path.exists() else {'items': []}
    resolved = {i['key']: i for i in old.get('items', []) if i.get('resolution')}
    rows = []
    for n, item in enumerate(items, 1):
        row = {'item_id': f'NR{n:03d}', **item, 'resolution': None, 'resolved_by': None}
        if item['key'] in resolved:
            row.update({k: resolved[item['key']][k] for k in ('resolution', 'resolved_by')})
        rows.append(row)
    doc = {'format': 'dense-needs-review/1', 'article_id': aid, 'generated_by': VERSION,
           'generated_at': datetime.now(timezone.utc).isoformat(),
           'instructions': 'Resolve each item against the SOURCE (claude_prompts/RESIGN_BRIEF.md): edit the annotation, '
                           'set resolution (what you decided and why) and resolved_by, regenerate the schema target, '
                           'then save_review + sign. Items reappear on the next migration run until the data satisfies the rule.',
           'items': rows}
    if apply:
        if rows:
            path.parent.mkdir(exist_ok=True); path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n')
        elif path.exists():
            path.unlink()   # every item now satisfies its rule
    return doc


def candidates(roots, only=None):
    for root in roots:
        seen = {}
        for folder in ('annotations', 'drafts'):
            for path in sorted((root / folder).glob('*.json')):
                if folder == 'drafts' and (root / 'annotations' / path.name).exists():
                    continue
                if only and path.stem not in only:
                    continue
                seen.setdefault(path.stem, (folder, path))
        for aid, (folder, path) in sorted(seen.items()):
            yield root, aid, folder, path


def run(apply=False, skip=(), only=None, roots=ROOTS, now=None, allow_recent=False):
    from schema_targets import materialize
    from validate import digest, validate
    now = now or time.time()
    report = {'format': 'dense-migration-report/1', 'migration': VERSION, 'mode': 'apply' if apply else 'dry_run',
              'generated_at': datetime.now(timezone.utc).isoformat(), 'articles': {}}
    for root, aid, folder, path in candidates(roots, only):
        text = path.read_text(encoding='utf-8'); annotation = json.loads(text)
        source = json.loads((root / 'sources' / f'{aid}.json').read_text())
        ctx = Ctx(root, aid, source); new = copy.deepcopy(annotation)
        for migration in ANNOTATION_MIGRATIONS:
            migration(new, ctx)
        promoted = folder == 'annotations'
        entry = {'root': root.name, 'folder': folder, 'promoted': promoted}
        reason = ('listed_in_flight' if aid in skip else 'recent_activity_within_15_minutes'
                  if recent(root, aid, now) and not allow_recent else None)
        errors = validate(new, source)
        if errors:
            reason = reason or 'migrated_annotation_invalid'; entry['validation_errors'] = errors[:5]
        if reason is None and style_of(text) is None:
            reason = 'unrecognised_json_style'
        annotation_changed = new != annotation
        entry['annotation_changed_paths'] = len(diff_paths(annotation, new))
        draft_path = root / 'drafts' / f'{aid}.json'
        draft_identical = promoted and draft_path.exists() and draft_path.read_bytes() == path.read_bytes()
        sub_path = root / 'schema-submissions' / f'{aid}.json'
        merged = regenerated = None
        try:
            regenerated = materialize(new, source)
            entry['runtime_gate_errors_after_migration'] = [e['error'][:200] for e in regenerated['source_gate_errors']][:20]
            if sub_path.exists():
                submission = json.loads(sub_path.read_text())
                merged = merge_submission(submission, regenerated)
                check_submission(merged, new, source)
                entry['submission_changed'] = merged != submission
        except (ValueError, KeyError, StopIteration) as exc:
            reason = reason or 'schema_regeneration_failed'; entry['schema_error'] = str(exc)[:500]
        try:
            ehr = m11_comparison_panels(ctx, apply and reason is None)
        except (ValueError, KeyError) as exc:
            ehr = None; reason = reason or 'ehr_label_migration_invalid'; entry['ehr_error'] = str(exc)[:300]
        entry['changes'] = {}
        for c in ctx.changes:
            entry['changes'].setdefault(c['migration'], []).append({k: c[k] for k in ('target', 'path', 'old', 'new')})
        entry['change_counts'] = {k: len(v) for k, v in entry['changes'].items()}
        entry['needs_review'] = ctx.review
        entry['sidecars_changed'] = [ehr] if ehr else []
        entry['receipts_going_stale'] = receipts_going_stale(root, aid, annotation_changed, promoted) + (
            ['figure-ehr-agent-reviews', 'figure-ehr-parent-reviews'] if ehr else [])
        entry['skipped_reason'] = reason
        if apply and reason is None:
            if annotation_changed:
                path.write_text(STYLES[style_of(text)](new), encoding='utf-8')
                if draft_identical:
                    draft_path.write_bytes(path.read_bytes())
            out = root / 'schema-drafts' / f'{aid}.json'; out.parent.mkdir(exist_ok=True)
            out.write_text(json.dumps(regenerated, ensure_ascii=False, indent=2) + '\n')
            if merged is not None:
                sub_text = sub_path.read_text(encoding='utf-8')
                if merged != json.loads(sub_text):
                    sub_path.write_text(STYLES[style_of(sub_text) or 'i2'](merged), encoding='utf-8')
            entry['applied'] = True
        if reason is None or reason == 'migrated_annotation_invalid':
            doc = write_needs_review(root, aid, ctx.review, apply and reason is None)
            # A resolved item stays recorded (with its resolution) but is not re-opened.
            entry['needs_review_resolved'] = [i for i in doc['items'] if i.get('resolution')]
            entry['needs_review'] = [i for i in doc['items'] if not i.get('resolution')]
        report['articles'][aid] = entry
    rows = report['articles'].values()
    def per(name):
        hit = [r['change_counts'].get(name, 0) for r in rows]
        return {'rows': sum(hit), 'articles': sum(1 for h in hit if h)}
    names = sorted({k for r in rows for k in r['change_counts']})
    report['summary'] = {
        'articles_examined': len(report['articles']),
        'applied': sum(bool(r.get('applied')) for r in rows),
        'skipped': {k: sum(r['skipped_reason'] == k for r in rows) for k in {r['skipped_reason'] for r in rows} if k},
        'migrations': {n: per(n) for n in names},
        'annotation_changed_articles': sum(bool(r['changes']) and any(k != 'M11_comparison_panels' for k in r['changes']) for r in rows),
        'needs_review_items': sum(len(r['needs_review']) for r in rows),
        'needs_review_resolved_items': sum(len(r.get('needs_review_resolved', [])) for r in rows),
        'needs_review_articles': sum(bool(r['needs_review']) for r in rows),
        'needs_review_by_rule': {k: sum(1 for r in rows for i in r['needs_review'] if i['rule_id'] == k)
                                 for k in sorted({i['rule_id'] for r in rows for i in r['needs_review']})},
        'articles_with_runtime_gate_errors_after_migration': sum(bool(r.get('runtime_gate_errors_after_migration')) for r in rows),
        'resign_required': sorted(aid for aid, r in report['articles'].items() if r['receipts_going_stale']),
    }
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--apply', action='store_true'); p.add_argument('--skip', nargs='*', default=[])
    p.add_argument('--article', nargs='*'); p.add_argument('--report')
    p.add_argument('--allow-recent', action='store_true',
                   help='Do not skip recently modified articles (use only with --article for an article you are re-signing)')
    args = p.parse_args()
    if args.allow_recent and not args.article:
        p.error('--allow-recent requires --article')
    report = run(apply=args.apply, skip=set(args.skip), only=set(args.article) if args.article else None,
                 allow_recent=args.allow_recent)
    if args.report:
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=1) + '\n')
    print(json.dumps(report['summary'], indent=1))


if __name__ == '__main__':
    main()
