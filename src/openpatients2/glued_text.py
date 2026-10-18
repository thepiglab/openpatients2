"""Detect and segment free text whose inter-word spaces were dropped ("aftersevenmonths").

Shared by both pipelines (rule GLUED-TEXT in rules/annotation_rules.yaml):

* `semantic_rules.item_violations` / `annotation_violations` warn on glued tokens in exported
  free text (runtime model output and dense reference annotations);
* `op2 dense scan_glued_text` (openpatients2.dense.scan_glued_text) reports every suspect token
  with a proposed segmentation; `apply_segmentations` applies reviewed ones.

A token is suspect when it is not a known word (shipped corpus vocabulary, the article's own
source tokens, or a simple inflection of either) but splits into known words. The vocabulary
(rules/glue_vocab.tsv.gz: word, count, document frequency) was built from the first 477 corpus source
texts by `build_vocab`; only words seen >=3 times in >=2 articles may be segmentation parts, so a
glued artefact in one source cannot become a segment. Segmentation is a unigram Viterbi search
(two best kept, so the margin to the runner-up is part of the confidence).
"""
from __future__ import annotations

import gzip
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.resources import files

VOCAB_FILE = 'rules/glue_vocab.tsv.gz'
# Known-word backstop (never a segmentation source): lowercase entries of Webster's Second
# International (public domain, /usr/share/dict/web2) of >=4 letters plus a few modern terms.
ENGLISH_FILE = 'rules/english_words.txt.gz'
PIECE = re.compile(r'[^\W_]+')           # letters/digits; splits on space, punctuation, '_', '/', '-'
WORD = re.compile(r'[^\W\d_]+')         # letters only (vocabulary units)
RUN = re.compile(r'\d+|[^\W\d_]+')       # digit runs / letter runs inside one piece
MIN_PIECE = 5                            # shorter pieces are not checked
SEGMENT_MIN_COUNT, SEGMENT_MIN_DF = 3, 2
HIGH_MIN_COUNT = 20
HIGH_MARGIN = 3.0
SHORT_WORDS = frozenset('a an as at be by do he if in is it me my no of on or so to up us we vs iv ml mg kg cm mm dl'.split())
FUNCTION_WORDS = frozenset('''a an and as at be been but by for from had has have in into is it its no nor not of on onto or
our so than that the their then there these this those to under until up upon via was were when which while who with
within without after before during over per since'''.split())
# Bound prefixes / suffix words: prefix+word ("midventricular", "nonobstructed") is usually a real
# compound, never an auto-repair.
PREFIXES = frozenset('''non mid post pre peri intra inter sub supra anti hyper hypo re co un bi tri multi semi extra trans
para pseudo neo micro macro over under out self auto de dis ex poly mono uni infra retro ultra cardio gastro neuro hepato
nephro immuno endo epi hemi counter hydro hydroxy chromo electro angio osteo myo cyto histo radio laparo thoraco
broncho echo entero colo procto uro gyneco hepatico pancreatico cholangio esophago oesophago duodeno jejuno ileo
spleno lympho hemato haemato thrombo leuko leuco aorto ilio femoro cryo'''.split())
LABEL_WORDS = frozenset('''table tables figure figures fig case cases patient patients column columns cell cells row rows
segment segments panel panels supplementary appendix video'''.split())
# Image/report-description vocabulary (visual and EHR sidecars describe pixels, not the article text).
DESCRIPTION_WORDS = frozenset('''timestamp timestamps gridline gridlines printout printouts brightfield darkfield handwritten
handwriting driveline heatmap heatmaps downsampled downsampling crosshair crosshairs workstation workstations flowsheet
flowsheets venographic screenshot screenshots colorbar colourbar scrollbar thumbnail thumbnails watermark watermarks
grayscale greyscale readout readouts worksheet worksheets flowchart flowcharts subpanel subpanels photomicrograph
photomicrographs overlay overlays sidebar toolbar dropdown checkbox checkboxes textbox upsampled pixelated pixelation
monochrome tickmark tickmarks legend callout callouts inset insets countdown download downloads'''.split())
NUMBERED_WORDS = frozenset('''day days week weeks month months year years hour hours cycle cycles visit grade stage
dose doses session sessions pod hd''' .split())
UNIT_WORDS = frozenset('mm cm m km mg g kg mcg ug ml l dl mmhg bpm min mins h hr hrs s sec days weeks months years'.split())
SUFFIXES = frozenset('like wise less ness free fold ability able ible ment ship hood ward wards'.split())


@lru_cache(maxsize=1)
def load_vocab():
    """{word: (count, document_frequency)} from the shipped vocabulary."""
    raw = files('openpatients2').joinpath(VOCAB_FILE).read_bytes()
    vocab = {}
    for line in gzip.decompress(raw).decode('utf-8').splitlines():
        word, count, df = line.split('\t')
        vocab[word] = (int(count), int(df))
    return vocab


@lru_cache(maxsize=1)
def english_words():
    raw = files('openpatients2').joinpath(ENGLISH_FILE).read_bytes()
    return frozenset(gzip.decompress(raw).decode('utf-8').split())


@lru_cache(maxsize=1)
def _total():
    return sum(c for c, _ in load_vocab().values())


def build_vocab(texts):
    """[(word, count, df)] from an iterable of per-article texts (letters-only tokens, casefolded)."""
    count, df = {}, {}
    for text in texts:
        words = [w.casefold() for w in WORD.findall(text)]
        for w in words:
            count[w] = count.get(w, 0) + 1
        for w in set(words):
            df[w] = df.get(w, 0) + 1
    return sorted((w, c, df[w]) for w, c in count.items())


def write_vocab(rows, path):
    data = ''.join(f'{w}\t{c}\t{d}\n' for w, c, d in rows).encode('utf-8')
    with open(path, 'wb') as fh:
        fh.write(gzip.compress(data, mtime=0))


@dataclass
class SourceVocab:
    """Tokens of one article source: exact-case pieces and casefolded letter words with counts."""
    pieces: frozenset = frozenset()
    pieces_folded: frozenset = frozenset()
    words: dict = field(default_factory=dict)
    folded_text: str = ''
    # Words the same author wrote spaced elsewhere (e.g. other annotation text): segment candidates
    # only, never "known" (a glued token must not whitelist itself).
    extra: frozenset = frozenset()


@lru_cache(maxsize=16)
def source_vocab(text, extra_text=None):
    if not text and not extra_text:
        return SourceVocab()
    text = text or ''
    pieces = PIECE.findall(text)
    words = {}
    for w in WORD.findall(text):
        w = w.casefold()
        words[w] = words.get(w, 0) + 1
    hy = '[‐‑‒–—−-]'
    folded = ' '.join(re.sub(hy, ' ', text).casefold().split())
    base = SourceVocab(frozenset(pieces), frozenset(p.casefold() for p in pieces), words, folded)
    extra = set()
    if extra_text:
        vocab = load_vocab()
        for piece in set(WORD.findall(extra_text)):
            f = piece.casefold()
            count, df = vocab.get(f, (0, 0))
            if (count >= SEGMENT_MIN_COUNT and df >= SEGMENT_MIN_DF) or f in words:
                continue
            # Abbreviations as written ("HJ") and words that do not themselves split into known words.
            if (piece.isupper() and 2 <= len(piece) <= 6) or (len(piece) >= 4 and not _kbest(piece, base)):
                extra.add(piece if piece.isupper() else f)
        # Second pass: drop candidates that are themselves concatenations of other words ("andcaption").
        base.extra = frozenset(extra)
        extra = {c for c in extra if not any(len(segs) > 1 for _, segs in _kbest(c, base, k=4))}
    base.extra = frozenset(extra)
    return base


def _inflection_stems(w):
    for suffix, repl in (('ies', 'y'), ('es', ''), ('s', ''), ('ed', ''), ('ed', 'e'), ('ing', ''), ('ing', 'e'),
                         ('ly', ''), ('ally', ''), ('al', ''), ('ic', ''), ('ity', ''), ('er', ''), ('ers', ''),
                         ('ied', 'y'), ('ically', 'ic'), ('ness', ''), ('ment', ''), ('ments', ''), ('en', '')):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            stem = w[:-len(suffix)] + repl
            yield stem
            if not repl and len(stem) >= 4 and stem[-1] == stem[-2]:
                yield stem[:-1]          # inferring -> infer, overwritten -> overwrite(t)


# British -> American spelling rewrites tried when a token is not otherwise known ("haematologic",
# "oesophageal", "tumour", "haemorrhagic", "anaesthetised", "centre", "labelled", "paediatric").
SPELLING_VARIANTS = ((r'aem', 'em'), (r'ae', 'e'), (r'oe', 'e'), (r'our(?=s?$|ed$|ing$|al|ous)', 'or'),
                     (r'is(e|ed|es|ing|ation|ations)$', r'iz\1'), (r'ys(e|ed|es|ing)$', r'yz\1'),
                     (r'tre(s?)$', r'ter\1'), (r'll(ed|ing|er|ers)$', r'l\1'), (r'ogue(s?)$', r'og\1'),
                     (r'yse(s?)$', r'yze\1'), (r'ence$', 'ense'))


def _spelling_variants(w):
    """American spellings of a British-spelled word (each rule alone, then all rules together)."""
    out = set()
    combined = w
    for pattern, repl in SPELLING_VARIANTS:
        v = re.sub(pattern, repl, w)
        if v != w:
            out.add(v)
        combined = re.sub(pattern, repl, combined)
    if combined != w:
        out.add(combined)
    return out


def is_known(token, src: SourceVocab):
    """Known word: in the shipped vocabulary (any count), in the article source, an inflection of either,
    or a British spelling variant of a known word."""
    f = token.casefold()
    vocab, english = load_vocab(), english_words()
    def known(w, variants=True):
        if w in vocab or w in english or w in src.words or any(
                s in vocab or s in english or s in src.words for s in _inflection_stems(w)):
            return True
        return variants and any(known(v, False) for v in _spelling_variants(w))
    if f in src.pieces_folded or f in DESCRIPTION_WORDS or f in PREFIXES or known(f):
        return True
    if CHEMICAL_FORMULA.fullmatch(token):
        return True
    # Closed prefix compounds ("unallocated", "nonpharmacologic") are words, not glued text.
    return any(f.startswith(p) and len(f) - len(p) >= 4 and known(f[len(p):]) for p in PREFIXES)


def _segment_cost(seg, src: SourceVocab):
    """Unigram cost of one segment (original case), or None when it may not be a segment."""
    if seg.isdigit():
        return 6.0
    f = seg.casefold()
    if len(f) == 1 and f != 'a':
        return None
    count, df = load_vocab().get(f, (0, 0))
    ok = count >= SEGMENT_MIN_COUNT and df >= SEGMENT_MIN_DF and (len(f) >= 3 or f in SHORT_WORDS)
    own = src.words.get(f, 0)
    if ok:
        return math.log(_total() / (count + own))
    # Article-specific term or abbreviation in its source case ("HJ", "hemihepatectomy").
    if seg in src.pieces and (len(seg) >= 3 or seg.isupper()):
        return math.log(_total() / max(own, 1)) + 2.0
    if seg in src.extra or (len(f) >= 4 and f in src.extra):
        return math.log(_total()) + 1.0
    english = english_words()
    if len(f) >= 4 and (f in english or any(x in english for x in _inflection_stems(f))):
        return math.log(_total()) + 1.0      # general English word: allowed, but always a weak segment
    return None


def _kbest(run, src, k=2):
    """k best segmentations of one letter run: [(cost, [segments])]."""
    n = len(run)
    best = [[] for _ in range(n + 1)]
    best[0] = [(0.0, [])]
    for i in range(1, n + 1):
        cands = []
        for j in range(max(0, i - 40), i):
            if not best[j]:
                continue
            seg = run[j:i]
            c = _segment_cost(seg, src)
            if c is None:
                continue
            # A lower->Upper case transition is strong evidence of a word boundary ("RouxHJ").
            if j > 0 and run[j - 1].islower() and run[j].isupper():
                c -= 2.0
            for cost, segs in best[j]:
                cands.append((cost + c, segs + [seg]))
        cands.sort(key=lambda x: x[0])
        best[i] = cands[:k]
    return best[n]


@dataclass(frozen=True)
class Glued:
    token: str
    segmentation: str
    segments: tuple
    confidence: str      # high | medium | low
    reasons: tuple
    cost: float
    margin: float | None


def _word_boundary_digit(left, right):
    """letter-run|digit-run boundary that is a dropped space: 'day50', 'January2020' (not 'IL6', 'COVID19')."""
    word = left if left and not left.isdigit() else right
    return word.islower() and len(word) >= 3 or (word[:1].isupper() and word[1:].islower() and len(word) >= 3)


def _prose_digit_glue(folded):
    """Letter/digit glue inside prose ("the2024fistula", "by2020"): a function word or two content words."""
    letters = [f for f in folded if not f.isdigit()]
    if any(f in FUNCTION_WORDS and len(f) >= 2 for f in letters):
        return True
    return len(letters) >= 2 and all(len(f) >= 3 for f in letters)


def analyse(piece, src: SourceVocab | None = None):
    """Glued finding for one letters/digits piece, or None when it is not suspect."""
    src = src or SourceVocab()
    short_label = re.fullmatch(r'(?:[Dd]ay|[Ww]eek|[Cc]ase|POD|[Cc]ycle|[Dd]ose|[Vv]isit|HD|[Pp]t)\d{1,2}', piece)
    if (len(piece) < MIN_PIECE and not short_label) or piece.isdigit() or piece in src.pieces or is_known(piece, src):
        return None
    runs = RUN.findall(piece)
    segments, cost, margin, glued, unsegmented = [], 0.0, None, False, []
    for i, run in enumerate(runs):
        if run.isdigit():
            segments.append(run)
            continue
        if is_known(run, src) or (len(run) < 3 and run in src.pieces):
            segments.append(run)
            continue
        options = _kbest(run, src)
        if not options or len(options[0][1]) < 2:
            segments.append(run)       # unknown term inside a glued piece: kept as written
            unsegmented.append(run)
            continue
        cost += options[0][0]
        segments += options[0][1]
        glued = True
        if len(options) > 1:
            m = options[1][0] - options[0][0]
            margin = m if margin is None else min(margin, m)
    # Dropped spaces at letter/digit boundaries ("bymonth9", "day50").
    digit_glue = any(a.isdigit() != b.isdigit() and _word_boundary_digit(a, b) for a, b in zip(runs, runs[1:]))
    if not glued and not digit_glue:
        return None
    if len(segments) < 2:
        return None
    phrase = ' '.join(segments)
    folded = [s.casefold() for s in segments]
    reasons = []
    vocab = load_vocab()
    if any(s in PREFIXES for s in folded[:-1]) or folded[-1] in SUFFIXES:
        confidence = 'low'
        reasons.append('prefix/suffix compound')
    elif not glued and not _prose_digit_glue(folded):
        # Only a letter/digit boundary ("Day25", "Jun2022"); label+number ("Table1", "Case2") is
        # usually an identifier and notation ("VO2peak") is not prose: low.
        labelish = any(f in LABEL_WORDS for f in folded) or not any(len(f) >= 3 and not f.isdigit() for f in folded)
        confidence = 'low' if labelish else 'medium'
        reasons.append('letter/digit boundary only')
        # Promote to a (non-blocking) warning when the spaced form is attested in the source, or the token
        # is a word/label + number (+ unit): "Patient1", "Case1", "day1", "diameter35mm", "at8weeks".
        # Notation stays exempt: abbreviation/mixed-case runs ("VO2peak", "HbA1c"), formulas ("MgSO4").
        words = [s for s in segments if not s.isdigit()]
        prose_words = all(w.islower() or (w[:1].isupper() and w[1:].islower()) for w in words)
        spaced = bool(src.folded_text) and re.search(r'(?<!\w)' + re.escape(phrase.casefold()) + r'(?!\w)',
                                                    src.folded_text) is not None
        patterned = prose_words and (
            any(f in LABEL_WORDS | NUMBERED_WORDS for f in folded)
            or (len(segments) >= 3 and segments[-1].casefold() in UNIT_WORDS and any(len(w) >= 3 for w in words[:-1]))
            or any(len(w) >= 3 for w in words) and segments[0].isdigit() is False and folded[0] in FUNCTION_WORDS)
        if spaced or patterned:
            confidence = 'medium'
            reasons[-1] = 'letter/digit glue' + (' (spaced form occurs in source)' if spaced else ' (word/label + number pattern)')
    elif src.folded_text and ' ' + phrase.casefold() + ' ' in ' ' + src.folded_text + ' ':
        confidence = 'high'
        reasons.append('spaced phrase occurs in source')
    else:
        english = english_words()
        weak = [s for s, f in zip(segments, folded) if not s.isdigit()
                and vocab.get(f, (0, 0))[0] < HIGH_MIN_COUNT and s not in src.pieces
                and not (len(f) >= 4 and (f in english or any(x in english for x in _inflection_stems(f))))]
        if unsegmented:
            reasons.append('unsegmented run(s): ' + ', '.join(unsegmented))
        joins = [a + b for a, b in zip(folded, folded[1:]) if not (a.isdigit() or b.isdigit())
                 and (a + b in english or vocab.get(a + b, (0, 0))[0] >= SEGMENT_MIN_COUNT)]
        if joins:
            reasons.append('adjacent segments form a word: ' + ', '.join(joins))
        short = [f for f in folded if not f.isdigit() and (len(f) == 1 or len(f) == 2 and f not in FUNCTION_WORDS)]
        ambiguous = margin is not None and margin < HIGH_MARGIN
        if weak:
            reasons.append('rare segment(s): ' + ', '.join(weak))
        if short:
            reasons.append('short non-function segment(s): ' + ', '.join(short))
        if ambiguous:
            reasons.append(f'runner-up segmentation within {margin:.1f} nats')
        confidence = 'medium' if (weak or short or ambiguous or unsegmented or joins) else 'high'
        if confidence == 'high':
            reasons.append('all segments common words' + (', unique split' if margin is None else f', margin {margin:.1f}'))
    return Glued(piece, phrase, tuple(segments), confidence, tuple(reasons), round(cost, 2),
                 None if margin is None else round(margin, 2))


FILE_OR_ID = re.compile(r'://|@|_|\.(?:pdf|docx?|xlsx?|csv|tsv|txt|jpe?g|png|tiff?|gif|zip|mp4|mov|avi|xml|json|html?)\b', re.I)


CHEMICAL_FORMULA = re.compile(r'(?:[A-Z][a-z]?\d{0,3}){2,6}')   # MgSO4, NaHCO3, CaCl2
ENCLOSED = re.compile('[\u2460-\u24ff\u2776-\u2793\u3251-\u325f\u32b1-\u32bf]')   # circled numbers/letters


def prose_pieces(text):
    """Letters/digits pieces of `text` outside filenames, URLs, e-mail addresses, snake_case identifiers and
    tokens with circled numbers ('Case⑤', a printed label)."""
    for chunk in re.finditer(r'\S+', text):
        if FILE_OR_ID.search(chunk.group(0)) or ENCLOSED.search(chunk.group(0)):
            continue
        for m in PIECE.finditer(chunk.group(0)):
            yield _Shifted(m, chunk.start())


class _Shifted:
    """A PIECE match re-based onto the full text."""
    __slots__ = ('_m', '_off')

    def __init__(self, m, off):
        self._m, self._off = m, off

    def group(self, i=0):
        return self._m.group(i)

    def start(self):
        return self._m.start() + self._off

    def end(self):
        return self._m.end() + self._off


PUNCT_GLUE = re.compile(r'(?<![\w.])([A-Za-z][A-Za-z]+)([,;])([A-Za-z][A-Za-z]+)(?![\w])')


def _punctuation_glue(text, src, quoted):
    """Missing space after a comma/semicolon between words ("HST,CPAP"), unless the token is in the source/quotes."""
    out = []
    for m in PUNCT_GLUE.finditer(text):
        token = m.group(0)
        if token in quoted or (src.folded_text and token.casefold() in src.folded_text):
            continue
        out.append(Glued(token, f'{m.group(1)}{m.group(2)} {m.group(3)}', (m.group(1), m.group(3)), 'medium',
                         ('missing space after punctuation',), 0.0, None))
    return out


def find_glued(text, source_text=None, *, quotes='', extra_text=None):
    """Suspect glued tokens in a free-text value. Pieces that occur in `quotes` (literal evidence) are skipped.

    `extra_text`: other text by the same author (e.g. the whole annotation), whose spaced words may
    serve as segments (article-specific terms and abbreviations); see `source_vocab`."""
    if not isinstance(text, str) or not text:
        return []
    src = source_vocab(source_text, extra_text) if (source_text or extra_text) else SourceVocab()
    quoted = set(PIECE.findall(quotes)) if quotes else set()
    out, seen = _punctuation_glue(text, src, quotes or ''), set()
    for m in prose_pieces(text):
        piece = m.group(0)
        if piece in seen or piece in quoted:
            continue
        seen.add(piece)
        g = analyse(piece, src)
        if g:
            out.append(g)
    return out


def apply_segmentations(text, findings):
    """Replace each finding's token (as a whole prose piece, see `prose_pieces`) by its segmentation.

    `findings`: Glued objects or {token: segmentation}. Filenames, URLs and identifiers are left alone."""
    by_token = findings if isinstance(findings, dict) else {g.token: g.segmentation for g in findings}
    out, last = [], 0
    for m in prose_pieces(text):
        if m.group(0) in by_token:
            out += [text[last:m.start()], by_token[m.group(0)]]
            last = m.end()
    return ''.join(out) + text[last:]


# Keys whose values are literal source text or identifiers: never checked.
NOT_FREE_TEXT = frozenset('''evidence caption_evidence identity_evidence quote source_section segment_id segment_ids fact_id
fact_ids event_id from_event_id to_event_id patient_id patient_ids figure_id record_id episode_id
alternate_representation_of date_iso'''.split())


def warn_worthy(g: Glued):
    """Findings the GLUED-TEXT validator warning reports: high/medium, not a bare letter/digit boundary."""
    return g.confidence != 'low' and 'letter/digit boundary only' not in g.reasons


def free_text_values(obj, path=''):
    """(path, string) for free-text leaves of an item/annotation part, skipping evidence and ids."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k not in NOT_FREE_TEXT:
                yield from free_text_values(v, f'{path}/{k}')
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from free_text_values(v, f'{path}/{i}')
    elif isinstance(obj, str):
        yield path, obj


def glued_findings(obj, source_text=None, *, quotes='', extra_text=None):
    """[(path, Glued)] warn-worthy findings in the free text of `obj`."""
    out = []
    for path, value in free_text_values(obj):
        out += [(path, g) for g in find_glued(value, source_text, quotes=quotes, extra_text=extra_text) if warn_worthy(g)]
    return out
