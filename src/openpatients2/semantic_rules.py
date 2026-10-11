"""Mechanical checks for the shared annotation rules (rule text: rules/annotation_rules.yaml).

One implementation serves both pipelines:

* runtime: `validation.validate` adds `section_violations` to every task result, so
  local-LLM outputs are repaired/rejected by the same rules; `case_context_target_errors`
  is the per-patient packet gate used by the extraction runners;
* reference: `openpatients2.dense.validate.semantic_errors` checks annotation facts, and the
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
import datetime as dt
import re

# ---------------------------------------------------------------- shared enums
# Timeline event occurrence and pairwise relations: used by longitudinal.PatientTimeline,
# the dense annotation validator and the materializer (no lossy mapping between them).
EVENT_OCCURRENCES = ('occurred', 'planned', 'conditional', 'not_occurred', 'unknown')
TEMPORAL_RELATIONS = ('before', 'after', 'same_time', 'during', 'overlaps', 'unknown')
# Earlier annotation spelling -> canonical value (`op2 dense normalize` applies it).
LEGACY_OCCURRENCE = {'uncertain': 'unknown'}

ERROR, WARNING = 'error', 'warning'

NEGATION_CUE = re.compile(
    r"\b(no|not|non|none|neither|nor|never|without|absent|absence|denie[sd]|deny|denying|negative|"
    r"ruled out|rules? out|ruling out|excluded?|excluding|exclusion|free of|free from|painless|unnecessary|not required|not needed|unable|non-?smok\w*|non-?febrile|a(?:pyre|pyrexi)\w*|non-?consanguin\w*|sparing|spared|rather than|instead of|(?:opted|decided|elected|chose) against|preclud(?:ed|es|ing)|omit(?:ted|s|ting)?|avoid(?:ed|ing|ance)?|abstain\w*|\w+-free|lack(?:s|ed|ing)?|"
    r"unremarkable|normal|nil|resolved|resolution|disappeared|asymptomatic|afebrile|apyrexi\w*|apyrexia|uneventful|"
    r"uncomplicated|misdiagnos\w*|impossible|deferred|prevented|cannot|can't|didn't|did not|wasn't|"
    r"weren't|isn't|aren't)\b|n['’]t\b"
    r"|(?:^|\|)\s*[−–-]\s*(?=\||$)", re.I | re.M)   # a table cell that is just a minus/dash sign ("−" = negative)
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
# TIME-LITERAL date_iso: one shared check for both pipelines (runtime validation.validate, clinical_normalization,
# reference annotation_violations/reference_gates). date_iso may be filled only from a FULL date written in the
# item's own time.text (and therefore its evidence): the ISO literal itself, or an unambiguous month-name form
# ("May 20, 2025", "20 May 2025", "20th May 2025", "May 20th, 2025"). All-numeric non-ISO forms ("05/06/2025",
# "20.05.2025") are day/month ambiguous and never populate date_iso; partial dates (month/year) never do.
MONTHS = {m: i + 1 for i, names in enumerate((
    ('january', 'jan'), ('february', 'feb'), ('march', 'mar'), ('april', 'apr'), ('may',), ('june', 'jun'),
    ('july', 'jul'), ('august', 'aug'), ('september', 'sep', 'sept'), ('october', 'oct'), ('november', 'nov'),
    ('december', 'dec'))) for m in names}
_MONTH = r'(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|' \
         r'jun|jul|aug|sept|sep|oct|nov|dec)\.?'
_DAY = r'(\d{1,2})(?:st|nd|rd|th)?'
WRITTEN_DATES = (re.compile(rf'\b{_MONTH}\s+{_DAY},?\s+(\d{{4}})\b', re.I),            # May 20, 2025
                 re.compile(rf'\b{_DAY}\s+(?:of\s+)?{_MONTH},?\s+(\d{{4}})\b', re.I))   # 20 May 2025
ISO_DATE = re.compile(r'\b(\d{4})-(\d{2})-(\d{2})\b')


def written_dates(text):
    """Full calendar dates written in `text` (ISO or unambiguous month-name forms), as ISO strings."""
    out = set()
    for m in ISO_DATE.finditer(text or ''):
        out.add(m.group(0))
    for i, rx in enumerate(WRITTEN_DATES):
        for m in rx.finditer(text or ''):
            month, day, year = (m.group(1), m.group(2), m.group(3)) if i == 0 else (m.group(2), m.group(1), m.group(3))
            try:
                out.add(dt.date(int(year), MONTHS[month.casefold()], int(day)).isoformat())
            except ValueError:
                pass
    return {d for d in out if _valid_iso(d)}


MONTH_YEAR = re.compile(rf'\b{_MONTH},?\s+(\d{{4}})\b', re.I)


def calendar_values(text):
    """Calendar values a literal time text supports, at its own precision: full dates (written_dates), month-year
    ('October 2022' -> 2022-10, ISO 2022-10), and a bare year (2022). Used for time_calendar_value / TimeExpression."""
    out = set(written_dates(text))
    for m in MONTH_YEAR.finditer(text or ''):
        out.add(f'{int(m.group(2)):04d}-{MONTHS[m.group(1).casefold()]:02d}')
    for m in re.finditer(r'(?<![\d-])(\d{4})-(\d{2})(?![\d-])', text or ''):
        if 1 <= int(m.group(2)) <= 12:
            out.add(m.group(0))
    for m in re.finditer(r'(?<![\d-])(1[89]\d{2}|20\d{2})(?![\d-])', text or ''):
        out.add(m.group(1))
    return out


def _valid_iso(value):
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}', value))


def date_iso_errors(date_iso, time_text, quotes=None):
    """[] when date_iso is a valid full date written in time.text (and that text is in the quotes)."""
    if not date_iso:
        return []
    if not _valid_iso(date_iso):
        return [f'date_iso {date_iso!r} is not a valid YYYY-MM-DD date']
    if quotes is not None and time_text and time_text not in quotes:
        return [f'date_iso {date_iso!r}: time.text is not literal in the evidence']
    if date_iso not in written_dates(time_text):
        return [f'date_iso {date_iso!r} must be a full date written in time.text {time_text!r} (ISO, or month-name '
                'form such as "May 20, 2025"); numeric non-ISO and partial dates stay null']
    return []


# NO-BORROWING / TIME-LITERAL: an extra quote whose only role is to carry a time ('By Day 3', a neighbouring
# sentence about something else) is borrowed time. Heuristic (warning): with 2+ quotes, every quote holding
# time.text is a time-only fragment or shares no content word with the fact's labels. Table rows ('|') and
# headings that structurally govern the fact are not flagged.
STOPWORDS = frozenset('''the and was were with for from that this then than after before during into onto over under
which while when where there their they them his her its our was been being have has had are not but also day days
week weeks month months year years hour hours time times later earlier patient patients case on of in at by to a an
as is it be or no he she we per'''.split())
LABEL_FIELDS = ('name', 'text_value', 'substance', 'action_text', 'event', 'attribute', 'body_site', 'finding',
                'relationship', 'procedure', 'description', 'drug', 'dose_text')


# Time/visit words carry no fact content ('follow-up', 'postoperative', 'admission' in a label do not tie a
# sentence to the fact).
TEMPORAL_STEMS = frozenset('follo postop posto admis disch basel prese visit initi later subse prior previ durin recen '
                           'curre preop preope'.split())


def _content_words(text):
    return {w[:5] for w in re.findall(r'[a-z][a-z-]{2,}', (text or '').casefold())
            if w not in STOPWORDS and w[:5] not in TEMPORAL_STEMS}


ABBREVIATION = re.compile(r'\b([A-Z][A-Za-z]{0,2}[A-Z]{1,5}s?)\b')
ANAPHOR = re.compile(r'\b(these|this|those|such|both|the above|the same)\s+(?:\w+\s+)?(signs?|symptoms?|findings?|'
                     r'changes?|lesions?|abnormalit\w+|results?|values?|levels?|complaints?|features?|manifestations?|'
                     r'parameters?|measurements?|tests?)\b', re.I)
LONG_FORM = re.compile(r'([A-Za-z][A-Za-z -]{3,80}?)\s*\(([A-Z][A-Za-z]{0,2}[A-Z]{1,5}s?)\)')


def _abbreviations(texts):
    """{ABBR.casefold(): content-word stems of its long form} learned from 'long form (ABBR)' in the texts."""
    out = {}
    for text in texts:
        for long_form, abbr in LONG_FORM.findall(text or ''):
            words = [w for w in re.findall(r'[A-Za-z]+', long_form)]
            initials = abbr.rstrip('s').casefold()
            # Keep the trailing words whose initials spell the abbreviation.
            for n in range(len(words), 0, -1):
                tail = words[-n:]
                if ''.join(w[0] for w in tail).casefold() == initials:
                    out[initials] = _content_words(' '.join(tail))
                    break
            else:
                # A single-word long form abbreviated by its own leading letters: 'Teduglutide (TED)'.
                if words and len(initials) >= 2 and words[-1].casefold().startswith(initials):
                    out[initials] = _content_words(words[-1])
    return out


def _initials_match(abbr, label_text):
    """'DDLT' ~ 'deceased donor liver transplantation', 'EF' ~ 'left ventricular ejection fraction'."""
    words = [w.casefold() for w in re.findall(r'[A-Za-z]+', label_text or '') if w.casefold() not in STOPWORDS]
    initials = ''.join(w[0] for w in words)
    return len(abbr) >= 2 and abbr in initials


def borrowed_time_quote(item, segment_tables=None):
    """True when time.text only occurs in time-only/unrelated extra quotes of a multi-quote item.

    A quote is NOT borrowed time when it governs the fact structurally or refers to it:
    a table caption/heading for a table-row fact (same segment as the row, or a 'Table N' caption);
    an abbreviation of the fact's label (learned from 'long form (ABBR)' in the item's own quotes/labels,
    or by initials); an anaphoric reference ('these signs') in the same segment as the fact's own quote."""
    time = item.get('time') or {}
    text = time.get('text') if isinstance(time, dict) else None
    evidence = [e for e in item.get('evidence') or [] if isinstance(e, dict)]
    quotes = [e.get('quote', '') for e in evidence]
    if not text or len(quotes) < 2:
        return False
    holding = [e for e in evidence if text in e.get('quote', '')]
    if not holding or len(holding) == len(evidence) or any('|' in e.get('quote', '') for e in holding):
        return False
    others = [e for e in evidence if text not in e.get('quote', '')]
    label_text = ' '.join(str(item.get(k) or '') for k in LABEL_FIELDS)
    labels = set(_content_words(label_text))
    abbreviations = _abbreviations(quotes + [label_text])
    for abbr, expansion in abbreviations.items():
        if expansion & labels:
            labels.add(abbr[:5])
    def segment(e):
        return e.get('segment_id') or e.get('source_section')
    tables = segment_tables or {}
    def governed(e):
        q = e.get('quote', '')
        same_segment = any(segment(o) and segment(o) == segment(e) for o in others)
        same_table = bool(tables.get(segment(e))) and any(tables.get(segment(o)) == tables.get(segment(e)) for o in others)
        if same_table:
            return True                       # same table (caption/heading/footnote/row) or same figure caption
        if any('|' in o.get('quote', '') for o in others) and (same_segment or re.match(r'\s*table\b', q, re.I)):
            return True                       # table caption/heading/header governing a table-row fact
        if ANAPHOR.search(q) and (same_segment or not segment(e)):
            return True                       # 'these signs' referring to the fact in the same paragraph
        rest = _content_words(q.replace(text, ' '))
        if rest & labels:
            return True
        for abbr in ABBREVIATION.findall(q):
            a = abbr.rstrip('s').casefold()
            if a in abbreviations and abbreviations[a] & labels or _initials_match(a, label_text):
                return True
        return False
    return not any(governed(e) for e in holding)


SENTENCE_END = re.compile(r'[.!?](?=\s+["“(]?[A-Z])')
NOT_SENTENCE_END = re.compile(r'(?:\b[A-Za-z]|\b(?:Fig|fig|Figs|Dr|vs|No|no|al|approx|ca|St|Mr|Ms|Mrs|Prof|e\.g|i\.e|p\.m|a\.m)'
                              r'|\b[a-z]\.[a-z])$')


def _sentences(text):
    out, last = [], 0
    for m in SENTENCE_END.finditer(text):
        if NOT_SENTENCE_END.search(text[last:m.start()]):
            continue           # abbreviation ('Fig.', 'Dr.', 'p.m.'), initial or single letter
        out.append(text[last:m.end()]); last = m.end()
    out.append(text[last:])
    return [s.strip() for s in out if s.strip()]


def time_in_other_sentence(item, segment_tables=None):
    """NO-BORROWING (span): the time phrase sits in a different sentence of a multi-sentence quote than every
    sentence carrying the fact's content words, and that time sentence is not a scoping/heading sentence
    ('On admission, ...', a sentence ending with ':', a 'Table N' caption)."""
    time = item.get('time') or {}
    text = time.get('text') if isinstance(time, dict) else None
    quotes = [e.get('quote', '') for e in item.get('evidence') or [] if isinstance(e, dict)]
    if not text:
        return False
    label_text = ' '.join(str(item.get(k) or '') for k in LABEL_FIELDS)
    labels = set(_content_words(label_text))
    abbreviations = _abbreviations(quotes + [label_text])
    def about_fact(sentence):
        rest = _content_words(sentence.replace(text, ' '))
        if rest & labels:
            return True
        label_words = {w.casefold() for w in re.findall(r'[A-Za-z]+', label_text)}
        return any((a := x.rstrip('s').casefold()) and (a in label_words or (a in abbreviations and abbreviations[a] & labels)
                   or _initials_match(a, label_text)) for x in ABBREVIATION.findall(sentence))
    groups = segment_tables or {}
    caption_quotes = {e.get('quote', '') for e in item.get('evidence') or [] if isinstance(e, dict)
                      and str(groups.get(e.get('segment_id') or e.get('source_section')) or '').startswith('figure:')}
    found = False
    for q in quotes:
        if text not in q or '|' in q or re.match(r'\s*(fig(?:ure)?s?\.?|table|video|supplementary)\s*[a-z]?\d', q, re.I):
            continue                         # table rows and figure/table captions scope their panels/rows
        if q in caption_quotes and text in _sentences(q)[0]:
            continue                         # a figure caption's lead sentence governs its panel sentences
        sentences = _sentences(q)
        timed = [s for s in sentences if text in s]
        if not timed or any(about_fact(s) or ANAPHOR.search(s) for s in timed):
            return False                     # time and fact content (or 'these symptoms') share a sentence
        if len(sentences) < 2:
            continue                         # a time-only fragment quote: borrowed_time_quote's case
        def scoping(s):
            head = s.strip()
            return (head.endswith(':') or re.match(r'table\b', head, re.I) is not None
                    or re.match(re.escape(text) + r'\s*(?:\([^)]*\))?\s*[,:]', head, re.I) is not None)
        if any(scoping(s) for s in timed):
            return False
        if any(about_fact(s) for s in sentences if s not in timed):
            found = True
    return found


# EVID-NEGATION: words that negate a specific finding. Recorded as a PRESENT item's text_value they invert
# polarity ('afebrile' is fever, assertion absent). 'normal'/'unremarkable' are excluded: a scoped normal
# examination is a present sign with text_value 'normal' (SECTION-SIGNS) and a normal result is flag=normal.
NEGATED_FINDING_WORD = re.compile(r'\b(afebrile|apyrexi\w*|asymptomatic|symptom[- ]free|pain[- ]free|fever[- ]free|'
                                  r'seizure[- ]free|uneventful(?:ly)?)\b', re.I)


# TIME-LITERAL: "by <date/time>" is an upper bound (relation before).
BY_TIME = re.compile(r'^\s*by\b', re.I)
# "within <duration> of/after <event>" names its reference point: relation after that anchor, never before it.
WITHIN_OF = re.compile(r'^\s*within\b.*\b(?:of|after|from)\s+\w', re.I)


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


def item_violations(task, collection, item, source_text=None, path='', *, segment_tables=None):
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
    if isinstance(time, dict) and time.get('date_iso'):
        for e in date_iso_errors(time['date_iso'], time.get('text'), quotes if quotes else None):
            add('TIME-LITERAL', ERROR, e)
    def negating(value):
        # A stated uneventful course is itself an outcome (outcomes task); elsewhere it negates complications.
        return any(task != 'outcomes' or not m.group(0).casefold().startswith('uneventful')
                   for m in NEGATED_FINDING_WORD.finditer(value))
    worded = next((f'{k} {get(k)!r}' for k in ('text_value', 'name') if isinstance(get(k), str)
                   and negating(get(k))), None)
    if task != 'observations' and assertion == 'present' and worded:
        add('EVID-NEGATION', WARNING, f"present item with a negation word in {worded}: record the negated finding "
            "instead (e.g. 'afebrile' -> fever, assertion absent; the quote keeps the word)")
    if time_in_other_sentence(item, segment_tables):
        add('NO-BORROWING', WARNING, f"time.text {time.get('text')!r} is in a different sentence of the quote than the "
            "fact's content; a multi-sentence span does not make another sentence's time this fact's time")
    if borrowed_time_quote(item, segment_tables):
        add('NO-BORROWING', WARNING, f"time.text {time.get('text')!r} comes only from an extra quote that carries nothing "
            "else of this fact; a time must come from the fact's own sentence or a governing table row/column/heading")
    if isinstance(time, dict) and BY_TIME.match(time.get('text') or '') and time.get('relation', 'unknown') != 'before':
        add('TIME-LITERAL', WARNING, f"time.text {time.get('text')!r} is an upper bound ('by ...'): relation should be "
            f"'before' (got {time.get('relation', 'unknown')!r})")
    if isinstance(time, dict) and WITHIN_OF.match(time.get('text') or '') and time.get('relation') == 'before':
        add('TIME-LITERAL', WARNING, f"time.text {time.get('text')!r} names its reference point ('within … of …'): "
            "the fact is after that anchor (relation 'after'), not before it")
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
    out += glued_text_violations(item, source_text, path, quotes=quotes)
    return out


def glued_text_violations(obj, source_text=None, path='', *, quotes=None, extra_text=None):
    """GLUED-TEXT (warning): free text with dropped inter-word spaces ("aftersevenmonths")."""
    from .glued_text import glued_findings
    quotes = _quotes(obj) if quotes is None and isinstance(obj, dict) else quotes or ''
    return [Violation('GLUED-TEXT', WARNING, path + sub,
                      f'{g.token!r} looks like words without spaces; write normal spaced prose (e.g. {g.segmentation!r})')
            for sub, g in glued_findings(obj, source_text, quotes=quotes, extra_text=extra_text)]


COLLECTIONS = {'oncology': ('tumors', 'biomarkers', 'treatments')}


def structural_group(segment):
    """Table id for table rows/captions/footnotes; 'figure:<id>' for a figure caption (its lead sentence governs
    the panel sentences of the same caption). None for running text."""
    if segment.get('table_id'):
        return segment['table_id']
    if segment.get('figure_id') and segment.get('kind', 'figure_caption') == 'figure_caption':
        return 'figure:' + segment['figure_id']
    return None


def segment_tables_of(segments):
    """{segment_id: table_id} for table segments (table rows, captions, headers) of a source/packet."""
    out = {}
    for s in segments or []:
        if not isinstance(s, dict) or 'segment_id' not in s:
            continue
        if structural_group(s):
            out[s['segment_id']] = structural_group(s)
        else:
            # Packet segments carry no table_id; table-row segments are printed under their 'Table N' label.
            m = re.match(r'\s*(Table\s+[A-Za-z]?\d+[A-Za-z]?)\b', s.get('text') or '')
            if m and '|' in (s.get('text') or ''):
                out[s['segment_id']] = 'label:' + m.group(1)
    return out


def section_violations(task, section, source_text=None, *, segments=None):
    """Rule checks for one runtime task section (`segments`: packet segments, for table structure)."""
    out = []
    tables = segment_tables_of(segments)
    for collection in COLLECTIONS.get(task, ('items',)):
        for i, item in enumerate(section.get(collection) or []):
            if isinstance(item, dict):
                out += item_violations(task, collection, item, source_text, f'/{collection}/{i}', segment_tables=tables)
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
    tables = {s['segment_id']: structural_group(s) for s in source['segments'] if structural_group(s)}
    out = []
    out += [Violation('ROSTER-ORDER', ERROR, '/patients', e) for e in roster_order_errors(annotation.get('patients', []), source['segments'])]
    for p in annotation.get('patients', []):
        out += reference_errors(p)
        merged = {}
        for f in p.get('facts', []):
            if f.get('scoring') != 'include':
                continue
            fields = annotation_fields(f)
            out += item_violations(f['task'], f.get('collection'), fields, text, f['fact_id'], segment_tables=tables)
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
    return out + annotation_glued_violations(annotation, text)


# Exported free text outside included facts' fields (those go through item_violations). Internal
# notes (facts[].notes, domain/source coverage notes, annotation_provenance) are not exported.
GLUED_PATIENT_PARTS = {'events': ('description', 'time_text'), 'summary': ('text',),
                       'ambiguities': ('description', 'issue', 'do_not_infer', 'must_not_infer', 'not_inferred')}
GLUED_FIGURE_KEYS = ('caption_summary', 'attribution_notes', 'visual_description')


def annotation_glued_violations(annotation, source_text):
    """GLUED-TEXT warnings for every exported free-text field of a dense annotation."""
    out = []
    def check(obj, path, keys=None, quotes=None):
        part = {k: obj[k] for k in keys if k in obj} if keys else obj
        out.extend(glued_text_violations(part, source_text, path, quotes=_quotes(obj) if quotes is None else quotes))
    for p in annotation.get('patients', []):
        pid = p.get('patient_id', '?')
        check(p, pid, ('label',), quotes='')
        for f in p.get('facts', []):
            check(f, f.get('fact_id', pid), ('clinical_statement',))
            if f.get('scoring') != 'include':    # included facts' fields: item_violations
                check({**f.get('fields', {}), 'evidence': f.get('evidence', [])}, f.get('fact_id', pid) + '/fields')
        for part, keys in GLUED_PATIENT_PARTS.items():
            for i, x in enumerate(p.get(part) or []):
                if isinstance(x, dict):
                    check(x, x.get('event_id') or f'{pid}/{part}/{i}', keys)
    for i, text in enumerate(annotation.get('article_limitations') or []):
        out.extend(glued_text_violations({'text': text}, source_text, f'/article_limitations/{i}', quotes=''))
    for i, fig in enumerate(annotation.get('figures') or []):
        check(fig, fig.get('figure_id') or f'/figures/{i}', GLUED_FIGURE_KEYS, quotes=_quotes({'evidence': fig.get('caption_evidence')}))
        for j, panel in enumerate(fig.get('panels') or []):
            check(panel, f"{fig.get('figure_id') or i}/panels/{j}", ('description_from_caption', 'body_part'))
    return out


# GLUED-TEXT in the EHR/visual sidecars: only reviewer-written free-text keys are checked (ids, enums, URLs,
# hashes and literal evidence quotes never are).
SIDECAR_FREE_TEXT = {
    'visual': ('general_description', 'detailed_description', 'limitations', 'rationale', 'text', 'visual_cue',
               'notes', 'description', 'clinical_significance', 'caption_claim', 'claim'),
    'figure_ehr': ('rationale', 'override_reason', 'notes'),
    'privacy': ('burned_in_description', 'sensitive_anatomy_description', 'face_description', 'notes'),
    'supplement_manifest': ('content_class_rationale', 'notes', 'limitations'),
    'measurement_series': ('notes', 'limitations', 'reason', 'measurement_name_note'),
}
SIDECAR_SKIP = frozenset({'machine', 'machine_suggestion', 'prefill', 'default', 'evidence', 'ownership_evidence',
                          'assets', 'cells', 'caption_evidence'})


def sidecar_glued_violations(kind, doc, source_text=None, *, extra_text=None):
    """GLUED-TEXT (warning) for reviewer-written free text in a visual submission, figure-EHR labels, privacy
    flags, supplement manifest or measurement series (kind = a SIDECAR_FREE_TEXT key)."""
    keys = SIDECAR_FREE_TEXT[kind]
    parts = []
    def walk(obj, path):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in SIDECAR_SKIP:
                    continue
                if k in keys and (isinstance(v, str) or (isinstance(v, list) and all(isinstance(x, str) for x in v))):
                    parts.append((f'{path}/{k}', v))
                else:
                    walk(v, f'{path}/{k}')
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f'{path}/{i}')
    walk(doc, '')
    out = []
    for path, value in parts:
        values = [value] if isinstance(value, str) else value
        for i, text in enumerate(values):
            # Text inside quotation marks transcribes printed image text ("Type1orType2Diabetes"): never flagged.
            printed = ' '.join(m.group(0) for m in QUOTED.finditer(text))
            out.extend(glued_text_violations({'text': text}, source_text, path + ('' if isinstance(value, str) else f'/{i}'),
                                             quotes=printed, extra_text=extra_text))
    return out


# Rule IDs with a mechanical check here (the rules file names them in `validator`).
VALIDATORS = {
    'EVID-NEGATION': 'item_violations (warning: absent without negation cue; present item whose text_value is a '
                     'negation word such as afebrile/asymptomatic/uneventful)',
    'GLUED-TEXT': 'glued_text_violations via item_violations, annotation_glued_violations and sidecar_glued_violations '
                  '(visual submissions, figure-EHR labels, privacy flags, supplement manifests, measurement series; '
                  'reference_gates figure/ehr GLUED-TEXT warnings) (warning: free-text token '
                  'that is not a known word but splits into known words, e.g. "aftersevenmonths"; openpatients2.glued_text)',
    'TIME-LITERAL': 'item_violations (error: anchor not literal in source; relation set with no text/anchor/date; '
                    'date_iso not a full date written in time.text [date_iso_errors]; warning: "by ..." not before)',
    'ASSERT-ABSENT': 'item_violations (error: absent condition with confirmed/provisional/differential)',
    'OBS-COMPARATOR': 'item_violations (error: numeric value with comparator unknown; warning: \'=\' for a value printed '
                      'only after upper-bound wording such as "up to")',
    'NO-BORROWING': 'item_violations (warning: time.text only in a time-only/unrelated extra evidence quote, or in a '
                    'different, non-scoping sentence of a multi-sentence quote than the fact content)',
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
