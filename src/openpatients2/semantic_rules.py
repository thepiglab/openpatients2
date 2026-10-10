"""Mechanical checks for the shared annotation rules (rule text: rules/annotation_rules.yaml).

One implementation serves both pipelines:

* runtime: `validation.validate` adds `section_violations` to every task result, so
  local-LLM outputs are repaired/rejected by the same rules; `case_context_target_errors`
  is the per-patient packet gate used by the extraction runners;
* reference: `dense500/validate.py semantic_errors` checks annotation facts, and the
  materialized runtime sections pass through `validation.validate` again, so a
  violating reference cannot be signed or exported.

Every check is keyed by a stable rule ID from the rules file. `error` violations are
mechanical and unambiguous; `warning` violations are heuristics that need a person
(they are reported, never auto-corrected). Partial annotation facts are checked with
the same defaults the materializer applies (absent categorical = 'unknown',
absent nullable = None), so an annotation and its materialized target agree.

Enumerations shared by the annotation format and the runtime timeline contract are
defined here once (EVENT_OCCURRENCES, TEMPORAL_RELATIONS).
"""
from __future__ import annotations

from dataclasses import dataclass
import re

# ---------------------------------------------------------------- shared enums
# Timeline event occurrence and pairwise relations: used by longitudinal.PatientTimeline,
# the dense annotation validator and the materializer (no lossy mapping between them).
EVENT_OCCURRENCES = ('occurred', 'planned', 'conditional', 'not_occurred', 'unknown')
TEMPORAL_RELATIONS = ('before', 'after', 'same_time', 'during', 'overlaps', 'unknown')
# Earlier annotation spelling -> canonical value (migrations/semantic_rules_2026_10.py applies it).
LEGACY_OCCURRENCE = {'uncertain': 'unknown'}

ERROR, WARNING = 'error', 'warning'

NEGATION_CUE = re.compile(
    r"\b(no|not|non|none|neither|nor|never|without|absent|absence|denie[sd]|deny|denying|negative|"
    r"ruled out|rule out|ruling out|excluded?|excluding|exclusion|free of|\w+-free|lack(?:s|ed|ing)?|"
    r"unremarkable|normal|nil|resolved|resolution|disappeared|asymptomatic|afebrile|uneventful|"
    r"uncomplicated|misdiagnos\w*|impossible|deferred|prevented|cannot|can't|didn't|did not|wasn't|"
    r"weren't|isn't|aren't)\b|n't\b", re.I)
NORMAL_RESULT = re.compile(r'^\s*(normal|unremarkable|within (the )?normal (limits|range)|wnl|normal range|'
                           r'normal as described|normal limits)\s*\.?\s*$', re.I)
NORMAL_CUE = re.compile(r'\b(normal|unremarkable|within (the )?normal|wnl|no abnormalit)', re.I)
BENIGN_TUMOR = re.compile(r'\b(adenoma|polyp|hemangioma|haemangioma|lipoma|fibroma|cyst|benign)\b', re.I)
MALIGNANT = re.compile(r'carcinom|malig|sarcom|lymphom|leuk|melanom|metasta|cancer|blastom|myelom|neoplasm of uncertain', re.I)
# SOC-NEVER: explicit never-use wording; 'no known contact/exposure' or 'denies' is NOT never-use.
NEVER_CUE = re.compile(r"\b(never|non-?smok\w*|lifelong (?:non|ab)\w*|teetotal\w*|has not ever|had not ever|"
                       r"not ever|at no time)\b", re.I)
POSITIONAL_LABEL = re.compile(r'^\s*(?:the\s+)?(?:image|panel|picture|figure)?\s*(?:on\s+the\s+)?'
                              r'((upper|lower|top|bottom|middle|centre|center|left|right|inset)[\s,-]*)+'
                              r'(?:image|panel|picture|side|part)?\s*$', re.I)
# OBS-COMPARATOR: upper-bound wording printed right before a value ('fever up to 38°C' -> '<=').
UPPER_BOUND_CUE = re.compile(r'(?:\bup to|\bmaximum of|\bmax\. of|\bat most|\bnot exceeding|\bno more than|≤|<=|=<)\s*$', re.I)


def _number_forms(value):
    forms = {format(value, 'g')}
    if float(value).is_integer():
        forms |= {str(int(value)), f'{int(value):,}'}
    else:
        forms.add(str(value))
    return sorted(forms, key=len, reverse=True)


def upper_bound_printed(value, quotes):
    """True when every occurrence of `value` in the quotes is preceded by upper-bound wording."""
    cues = []
    for form in _number_forms(value):
        for m in re.finditer(r'(?<![\d.,])' + re.escape(form) + r'(?![\d])', quotes):
            cues.append(bool(UPPER_BOUND_CUE.search(quotes[max(0, m.start() - 16):m.start()])))
    return bool(cues) and all(cues)


REFUTED_CUE = re.compile(r'\b(ruled out|was excluded|were excluded|excluded|exclusion of|revealed no|showed no|'
                         r'demonstrated no|confirmed no|no evidence of)\b', re.I)
HISTORY_DENIAL = re.compile(r'\b(no|denied|denies|deny|without|not previously)\b[^.;|]{0,60}?'
                            r'\b(history|histories|known|previous|prior|past|comorbidit(?:y|ies)|illnesses|diagnosed)\b', re.I)


# Hyphen/dash code points treated as the same character when LOCATING a phrase in a quote
# (ASCII hyphen, U+2010 hyphen, U+2011 non-breaking hyphen, U+2012 figure dash, U+2013 en dash,
# U+2014 em dash, U+2212 minus, U+00AD soft hyphen). The copied phrase keeps the quote's own code point.
HYPHENS = '-\u2010\u2011\u2012\u2013\u2014\u2212\u00ad'


def literal_span(text, quotes):
    """The exact substring of `quotes` that `text` denotes, or None.

    Matching tolerates case, hyphen/dash code points and whitespace runs only; the returned string
    is copied from the quote verbatim (e.g. '67\u2010year\u2010old' for text '67-year-old'). Returns
    the first match when the phrase occurs several times with different spellings."""
    if not text or not quotes:
        return None
    if text in quotes:
        return text
    parts = []
    for ch in text.strip():
        if ch in HYPHENS:
            parts.append(f'[{HYPHENS}]')
        elif ch.isspace():
            if parts and parts[-1] != r'\s+':
                parts.append(r'\s+')
        else:
            parts.append(re.escape(ch))
    m = re.search(''.join(parts), quotes, re.I) if parts else None
    return m.group(0) if m else None


@dataclass(frozen=True)
class Violation:
    rule_id: str
    severity: str
    path: str
    message: str

    def text(self):
        return f'{self.path}: [{self.rule_id}] {self.message}'


def _quotes(item):
    return '\n'.join(e.get('quote', '') for e in item.get('evidence') or [] if isinstance(e, dict))


def _fold(text):
    return (text or '').casefold()


def item_violations(task, collection, item, source_text=None, path=''):
    """Rule checks for ONE clinical item (runtime item or partial annotation `fields` + evidence)."""
    out = []
    get = item.get
    def add(rule, severity, message):
        out.append(Violation(rule, severity, path, message))
    quotes = _quotes(item)
    assertion = get('assertion', 'unknown')
    # TIME-LITERAL time anchors are literal source phrases or null.
    time = get('time') or {}
    anchor = time.get('anchor') if isinstance(time, dict) else None
    if anchor and source_text is not None and _fold(anchor) not in _fold(source_text):
        add('TIME-LITERAL', ERROR, f'time.anchor {anchor!r} is not a literal source phrase; use the source wording or null')
    if (isinstance(time, dict) and time and time.get('relation', 'unknown') != 'unknown'
            and not time.get('text') and not time.get('anchor') and not time.get('date_iso')):
        add('TIME-LITERAL', ERROR, f"time.relation={time.get('relation')!r} with no text, anchor or date dangles; "
            "add the literal time/anchor phrase or use relation=unknown")
    # EVID-NEGATION an explicit negation must be visible in the item's own quotes.
    if assertion == 'absent' and quotes and not NEGATION_CUE.search(quotes):
        add('EVID-NEGATION', WARNING, 'assertion=absent but the evidence quotes contain no negation cue')
    if task == 'conditions':
        status = get('verification_status', 'unknown')
        if assertion == 'absent' and status in ('confirmed', 'provisional', 'differential'):
            add('ASSERT-ABSENT', ERROR, f'assertion=absent cannot have verification_status={status}; use refuted (excluded '
                'by workup) or unknown (denied/absent history)')
    if task == 'observations':
        if get('numeric_value') is not None and get('comparator', 'unknown') == 'unknown':
            add('OBS-COMPARATOR', ERROR, "numeric_value needs a comparator: '=' unless <, <=, >, >= is printed")
        elif (get('numeric_value') is not None and get('comparator') == '=' and quotes
                and upper_bound_printed(get('numeric_value'), quotes)):
            add('OBS-COMPARATOR', WARNING, f"value {get('numeric_value')} is printed with upper-bound wording ('up to', "
                "'maximum of', 'at most'); use comparator '<=' (a measured peak stated as a value stays '=')")
        if get('status', 'unknown') == 'not_performed' and assertion == 'absent':
            add('OBS-NOT-PERFORMED', ERROR, 'a test documented as not performed is assertion=present with status=not_performed')
        text = get('text_value')
        if text and NORMAL_RESULT.match(text) and get('flag', 'unknown') != 'normal':
            add('OBS-FLAG', WARNING, f"text_value {text!r} states a normal result; flag should be 'normal'")
    if task == 'social_exposures' and get('status', 'unknown') == 'never':
        if not NEVER_CUE.search(quotes):
            add('SOC-NEVER', WARNING, "status=never without explicit never-wording in the evidence; 'no known "
                "contact/exposure' or 'denies' is assertion=absent with status unknown/current, not never-use")
        elif assertion == 'absent':
            add('SOC-NEVER', ERROR, 'never-use is an affirmed status: status=never with assertion=present')
    if task == 'demographics' and str(get('attribute', '')).startswith('age_') and get('numeric_value') is not None:
        text = get('text_value')
        if not text:
            add('DEMO-AGE-TEXT', ERROR, 'a numeric age keeps text_value = the literal source phrase stating it')
        elif quotes and text not in quotes:
            exact = literal_span(text, quotes)
            if exact is not None:
                # Mechanical: the phrase is in the quote but with other case/hyphen/space code points.
                add('DEMO-AGE-TEXT', ERROR, f'age text_value {text!r} must be copied verbatim from the evidence: use {exact!r}')
            else:
                add('DEMO-AGE-TEXT', WARNING, f'age text_value {text!r} does not occur in the item evidence')
    if task == 'oncology' and collection == 'tumors':
        name = get('name') or ''
        if BENIGN_TUMOR.search(name) and not MALIGNANT.search(name):
            add('ONC-BENIGN', WARNING, f'{name!r} looks benign; benign lesions belong in conditions, not oncology.tumors')
    if task == 'medications':
        route = get('route')
        if route and quotes and _fold(route) not in _fold(quotes):
            add('MED-ROUTE', WARNING, f'route {route!r} is not written in this item\'s evidence (no borrowed routes)')
    return out


COLLECTIONS = {'oncology': ('tumors', 'biomarkers', 'treatments')}


def section_violations(task, section, source_text=None):
    """Rule checks for one runtime task section."""
    out = []
    for collection in COLLECTIONS.get(task, ('items',)):
        for i, item in enumerate(section.get(collection) or []):
            if isinstance(item, dict):
                out += item_violations(task, collection, item, source_text, f'/{collection}/{i}')
    return out


def case_context_target_errors(section):
    """CTX-PER-PATIENT: an article-derived per-patient record describes ONE target patient."""
    if section.get('case_kind') == 'multi_patient' or section.get('multiple_index_patients') is not False:
        return ['[CTX-PER-PATIENT] This packet targets ONE specified index patient. case_kind and multiple_index_patients '
                'describe that target, not the number of patients in the source article. Use clinical_case (or animal) '
                f"and multiple_index_patients=false (got {section.get('case_kind')!r}, {section.get('multiple_index_patients')!r})."]
    return []


def record_violations(sections, *, target_patient=True):
    """Cross-section checks for one patient's sections ({task: section})."""
    out = []
    context = sections.get('case_context') or {}
    if target_patient and context:
        out += [Violation('CTX-PER-PATIENT', ERROR, '/case_context', e) for e in case_context_target_errors(context)]
    deaths = [i for i in (sections.get('outcomes') or {}).get('items', [])
              if i.get('event') == 'death' and i.get('assertion') == 'present'
              and i.get('subject') == 'index_patient']
    if deaths and context.get('subject_state') == 'living_at_presentation':
        out.append(Violation('CTX-VITAL-STATUS', ERROR, '/case_context/subject_state',
                             'the patient died during the described course (outcomes death, assertion=present); '
                             'use subject_state=deceased_during_course'))
    return out


def roster_order_errors(patients, segments):
    """ROSTER-ORDER: ids are p1..pn in order of first mention (earliest identity-evidence quote)."""
    ids = [p['patient_id'] for p in patients]
    if ids != [f'p{i + 1}' for i in range(len(ids))]:
        return [f'[ROSTER-ORDER] patient ids must be p1..p{len(ids)} in order (got {ids})']
    index = {s['segment_id']: (i, s['text']) for i, s in enumerate(segments)}
    keys = []
    for p in patients:
        spots = [(index[e['segment_id']][0], index[e['segment_id']][1].find(e['quote']))
                 for e in p.get('identity_evidence') or [] if e.get('segment_id') in index]
        keys.append(min(spots) if spots else None)
    known = [k for k in keys if k is not None]
    if known != sorted(known):
        return ['[ROSTER-ORDER] number patients in order of first mention in the source (earliest identity evidence)']
    return []


QUOTED = re.compile(r'“[^”]*”|"[^"]*"|‘[^’]*’|\'[^\']*\'|«[^»]*»')


def visual_question_marks(text):
    """FIG-LABELS: a '?' may appear in a visual description only inside quotes transcribing printed text."""
    return '?' in QUOTED.sub('', text or '')


# FIG-LABELS: words that describe where a panel sits ('left', 'upper right', 'Left image', 'Upper-left inset',
# 'Lower PSG segment', 'Main image') or whose patient it shows ('Case 1', 'Patient 2') are not printed panel labels.
PANEL_POSITION_WORD = re.compile(r'^\s*(?:the\s+)?(?:image\s+on\s+the\s+)?'
                                 r'(upper|lower|top|bottom|middle|centre|center|central|left|right|inset|main)\b', re.I)
PATIENT_NAME_LABEL = re.compile(r'^\s*(case|patient|pt\.?|subject)\s*#?\s*\d+\b', re.I)


def panel_label_issue(label):
    """'positional' | 'patient_name' | None for one panel label (None label = whole figure, fine)."""
    if not isinstance(label, str) or not label.strip():
        return None
    if POSITIONAL_LABEL.match(label) or PANEL_POSITION_WORD.match(label):
        return 'positional'
    if PATIENT_NAME_LABEL.match(label):
        return 'patient_name'
    return None


def panel_label_violations(panels, path=''):
    """FIG-LABELS warnings for panel rows ({'panel': label, ...}) of a visual analysis, figure-EHR label or
    privacy-flag sidecar. Warnings, not errors: a position word or patient name is occasionally printed on the
    image itself, and renaming a panel cascades through dependent signed sidecars, so a person decides."""
    out = []
    for i, panel in enumerate(panels or []):
        label = panel.get('panel') if isinstance(panel, dict) else None
        issue = panel_label_issue(label)
        if issue:
            what = ('a caption position word' if issue == 'positional' else 'a patient name')
            out.append(Violation('FIG-LABELS', WARNING, f'{path}/panels/{i}',
                                 f'panel label {label!r} is {what}, not a printed panel label; use the printed letter/number '
                                 'or null (whole figure) unless it is printed on the image'))
    return out


def annotation_fields(fact):
    """Partial annotation fact -> dict checked like a runtime item."""
    return {**fact.get('fields', {}), 'evidence': fact.get('evidence', [])}


def reference_errors(patient):
    """REF-INTEGRITY: every fact/event reference inside one patient points to an existing id of that patient.

    Covers facts[].alternate_representation_of, events[].fact_ids, temporal_relations endpoints,
    summary[].fact_ids (claim sources) and ambiguities[].fact_ids. The runtime timeline applies the same rule
    (longitudinal.audit_timeline 'unknown_or_wrong_patient_fact', block)."""
    pid = patient.get('patient_id', '?')
    fids = {f.get('fact_id') for f in patient.get('facts', [])}
    eids = {e.get('event_id') for e in patient.get('events', [])}
    out = []
    def check(refs, known, where, what):
        for ref in [refs] if isinstance(refs, str) else refs or []:
            if ref not in known:
                out.append(Violation('REF-INTEGRITY', ERROR, where, f'{what} {ref!r} does not exist in patient {pid}; '
                                     'remove or repoint references when a fact/event is removed'))
    for f in patient.get('facts', []):
        check(f.get('alternate_representation_of'), fids, f.get('fact_id', pid), 'alternate_representation_of')
    for e in patient.get('events', []):
        check(e.get('fact_ids'), fids, e.get('event_id', pid), 'event fact link')
    for r in patient.get('temporal_relations', []):
        check([r.get('from_event_id'), r.get('to_event_id')], eids, pid, 'temporal relation endpoint')
    for i, s in enumerate(patient.get('summary', [])):
        check(s.get('fact_ids'), fids, f'{pid}/summary/{i}', 'summary claim source')
    for i, a in enumerate(patient.get('ambiguities', [])):
        if isinstance(a, dict):
            check(a.get('fact_ids'), fids, f'{pid}/ambiguities/{i}', 'ambiguity fact link')
    return out


def annotation_violations(annotation, source):
    """Fact-level and patient-level rule checks on a dense annotation (partial facts)."""
    text = '\n'.join(s['text'] for s in source['segments'])
    out = []
    out += [Violation('ROSTER-ORDER', ERROR, '/patients', e) for e in roster_order_errors(annotation.get('patients', []), source['segments'])]
    for p in annotation.get('patients', []):
        out += reference_errors(p)
        merged = {}
        for f in p.get('facts', []):
            if f.get('scoring') != 'include':
                continue
            fields = annotation_fields(f)
            out += item_violations(f['task'], f.get('collection'), fields, text, f['fact_id'])
            if f['task'] == 'case_context':
                merged.setdefault('case_context', {}).update(f.get('fields', {}))
            elif f['task'] == 'outcomes':
                merged.setdefault('outcomes', {'items': []})['items'].append(fields)
        out += [Violation(v.rule_id, v.severity, p['patient_id'] + v.path, v.message) for v in record_violations(merged)]
        for e in p.get('events', []):
            if e.get('occurrence') not in EVENT_OCCURRENCES:
                out.append(Violation('TIMELINE-ENUMS', ERROR, e['event_id'], f"occurrence {e.get('occurrence')!r} not in {EVENT_OCCURRENCES}"))
        for r in p.get('temporal_relations', []):
            if r.get('relation') not in TEMPORAL_RELATIONS:
                out.append(Violation('TIMELINE-ENUMS', ERROR, p['patient_id'], f"relation {r.get('relation')!r} not in {TEMPORAL_RELATIONS}"))
    return out


# Rule IDs with a mechanical check here (the rules file names them in `validator`).
VALIDATORS = {
    'EVID-NEGATION': 'item_violations (warning: absent without negation cue)',
    'TIME-LITERAL': 'item_violations (error: anchor not literal in source; relation set with no text/anchor/date)',
    'ASSERT-ABSENT': 'item_violations (error: absent condition with confirmed/provisional/differential)',
    'OBS-COMPARATOR': 'item_violations (error: numeric value with comparator unknown; warning: \'=\' for a value printed '
                      'only after upper-bound wording such as "up to")',
    'OBS-FLAG': 'item_violations (warning: normal text with non-normal flag)',
    'OBS-NOT-PERFORMED': 'item_violations (error: not_performed with assertion=absent)',
    'SOC-NEVER': 'item_violations (error: explicit never-wording with assertion=absent; warning: never without never-wording)',
    'DEMO-AGE-TEXT': 'item_violations (error: numeric age without text_value, or text_value not copied verbatim from a '
                     'quote that contains it with other case/hyphen code points; warning: not in the evidence at all)',
    'ONC-BENIGN': 'item_violations (warning: benign-looking tumor in oncology)',
    'CTX-VITAL-STATUS': 'record_violations (error: died but living_at_presentation)',
    'CTX-PER-PATIENT': 'case_context_target_errors (error)',
    'ROSTER-ORDER': 'roster_order_errors (error)',
    'TIMELINE-ENUMS': 'annotation_violations / longitudinal enums (error)',
    'MED-ROUTE': 'item_violations (warning: route not in own evidence)',
    'REF-INTEGRITY': 'reference_errors via annotation_violations (error); runtime timeline: longitudinal.audit_timeline '
                     'unknown_or_wrong_patient_fact (block)',
    'FIG-LABELS': 'visual_question_marks via figure_visuals.validate_visuals(refined=True) (error); panel_label_violations '
                  'on visual submissions / figure-EHR labels / privacy flags (warning: positional or patient-name label)',
}
