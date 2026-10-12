"""Text normalisation and similarity primitives shared by the unified scorer."""
from __future__ import annotations

from difflib import SequenceMatcher
from functools import lru_cache
import re
import unicodedata

UNINFORMATIVE = (None, '', 'unknown', 'not_documented', 'unspecified')
_TOKEN = re.compile(r'[a-z0-9]+')
_NUMBER = re.compile(r'[-+]?\d+(?:\.\d+)?')
STOP = frozenset('a an the of and or in on at to for with by from was were is are be been as this that'.split())


@lru_cache(maxsize=200_000)
def norm(text):
    if text is None:
        return ''
    text = unicodedata.normalize('NFKC', str(text)).casefold().replace('µ', 'u').replace('μ', 'u')
    return ' '.join(_TOKEN.findall(text))


_DASHES = str.maketrans({c: '-' for c in '\u2212\u2012\u2013\u2014\u2010\u2011\ufe63\uff0d'})


def compact(text):
    """Whitespace-free, casefolded NFKC form that keeps symbols (dash variants unified to '-')."""
    if text is None:
        return ''
    return ''.join(unicodedata.normalize('NFKC', str(text)).translate(_DASHES).casefold().split())


def _stem(tok):
    return tok[:-1] if len(tok) > 3 and tok.endswith('s') and not tok.endswith('ss') else tok


@lru_cache(maxsize=200_000)
def tokens(text, drop_stop=False):
    toks = (_stem(t) for t in norm(text).split())
    return frozenset(t for t in toks if not (drop_stop and t in STOP))


def informative(value):
    if isinstance(value, str):
        return value.strip().casefold() not in UNINFORMATIVE
    if isinstance(value, (list, dict)):
        return bool(value)
    return value is not None


@lru_cache(maxsize=500_000)
def name_sim(a, b, floor=0.0):
    """0..1 similarity of two short labels: max(token Dice, char ratio, 0.75 * token containment).

    With ``floor`` the (costly) character ratio is skipped when it cannot lift the result above ``floor``;
    the returned value is then exact whenever it is >= floor.
    """
    na, nb = norm(a), norm(b)
    if not na or not nb:  # symbol-only text such as '+', '+++', '−' (semi-quantitative results)
        return 1.0 if compact(a) and compact(a) == compact(b) else 0.0
    if na == nb:
        return 1.0
    ta, tb = tokens(a, True) or tokens(a), tokens(b, True) or tokens(b)
    inter = len(ta & tb)
    dice = 2 * inter / (len(ta) + len(tb)) if ta and tb else 0.0
    contain = inter / min(len(ta), len(tb)) if ta and tb else 0.0
    best = max(dice, 0.75 * contain)
    sm = SequenceMatcher(None, na, nb, autojunk=False)
    if sm.real_quick_ratio() <= max(best, floor) or sm.quick_ratio() <= max(best, floor):
        return best
    return max(best, sm.ratio())


def token_dice(a, b):
    ta, tb = tokens(a), tokens(b)
    return 2 * len(ta & tb) / (len(ta) + len(tb)) if ta and tb else 0.0


def evidence_spans(obj):
    """[(segment_or_None, token_set, normalised_quote)] for an object's evidence list."""
    out = []
    for key in ('evidence', 'identity_evidence', 'attribution_evidence'):
        for ev in (obj or {}).get(key) or []:
            if isinstance(ev, dict) and ev.get('quote'):
                out.append((ev.get('source_section') or ev.get('segment_id'), tokens(ev['quote']), norm(ev['quote'])))
    return out


def span_overlap(a, b, jaccard=False):
    """Best quote overlap between two evidence lists, 0..1: token containment (min-normalised) or, with
    ``jaccard=True``, token Jaccard (near 1 only when the quotes are essentially the same span)."""
    best = 0.0
    for sa, ta, qa in a:
        for sb, tb, qb in b:
            if qa == qb:
                return 1.0
            if not ta or not tb:
                continue
            small = min(len(ta), len(tb))
            score = len(ta & tb) / (len(ta | tb) if jaccard else small)
            if small < 3:
                score *= 0.5  # very short quotes are weak evidence
            if sa and sb and sa != sb:
                score *= 0.9
            best = max(best, score)
    return best


def numbers(text):
    return [float(x) for x in _NUMBER.findall(str(text or ''))]


def num_equal(a, b):
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return False
    return abs(a - b) <= 1e-6 * max(1.0, abs(a))


_UNIT_SUBS = (('mcg', 'ug'), ('litre', 'l'), ('liter', 'l'), ('per', '/'), ('years', 'year'), ('yrs', 'year'),
              ('yr', 'year'), ('×', 'x'), ('^', ''), ('*', 'x'))


def norm_unit(unit):
    if not informative(unit):
        return None
    u = unicodedata.normalize('NFKC', str(unit)).casefold().replace('µ', 'u').replace('μ', 'u')
    for old, new in _UNIT_SUBS:
        u = u.replace(old, new)
    return re.sub(r'\s+', '', u)


_FIG = re.compile(r'^(?:fig(?:ure)?|f)[\W_]*(\d+[a-z]?)$')
_POSITIONS = {'top': 'upper', 'bottom': 'lower'}


def norm_figure_id(fid):
    s = norm(fid).replace(' ', '')
    m = _FIG.match(s)
    return f'fig{m.group(1)}' if m else s


def norm_panel(panel):
    """Panel label normalisation: None/whole -> '_whole'; '(A)', 'Panel a', '1a' -> 'a'; 'Upper-left inset' -> 'upper left inset'."""
    if panel is None:
        return '_whole'
    s = norm(panel)
    s = re.sub(r'^(?:fig(?:ure)?\s*\d*\s*)?(?:panel\s*)?', '', s).strip()
    if s in ('', 'whole', 'main', 'main image', 'whole figure'):
        return '_whole' if s != 'main image' else 'main'
    m = re.fullmatch(r'\d*\s*([a-z])', s)
    if m:
        return m.group(1)
    words = [_POSITIONS.get(w, w) for w in s.split() if w not in ('image', 'panel', 'view')]
    return ' '.join(words) or s
