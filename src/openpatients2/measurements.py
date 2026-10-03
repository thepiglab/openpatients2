"""Conservative measurement sidecars: explicit notation, no unit conversions.

LLM fields and immutable evidence remain unchanged. Decimal strings preserve
precision; this parser verifies notation, not patient attribution or entailment.
"""
from __future__ import annotations

from decimal import Decimal, localcontext
import re
from typing import Literal
from pydantic import Field
from .schemas import StrictModel


class Measurement(StrictModel):
    raw_text: str
    comparator: Literal['=', '<', '<=', '>', '>=']
    mantissa: str
    power_of_ten: int = Field(ge=-30, le=30)
    magnitude: str = Field(description='Expanded decimal magnitude in the stated unit; no unit conversion')
    unit: str | None
    notation: Literal['decimal', 'scientific']


NUMBER = r'[+-]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)'
EXPONENT = r'(?:[eE]\s*([+-]?\d{1,2})|\s*[×x*]\s*10\s*\^\s*(?:\(([+-]?\d{1,2})\)|([+-]?\d{1,2})))'
# Restrict token boundaries and unit vocabulary: ages, dates and graph dimensions
# in the same quote must not become laboratory results.
UNIT = (r'(?:mmol|µmol|umol|nmol|pmol|mol|mg|µg|ug|ng|pg|g|mEq|IU|U|'
        r'mmHg|kPa|bpm|beats|min|mL|ml|L|l|dL|dl|fL|fl|mm|cm|m|cells|%)'
        r'(?:/(?:mL|ml|L|l|dL|dl|mm3|mm\^\(?3\)?|kg|h|hr|min|s|day))*')
PATTERN = re.compile(r'(?<![\w.])([<>]=?|≤|≥)?\s*(' + NUMBER + r')(' + EXPONENT + r')?'
                     r'\s*(' + UNIT + r'|/(?:mL|L|l|mm3))?(?![\w^]|\.\d)')
RAW_PATTERN = re.compile(r'(?<![\w.])([<>]=?|≤|≥)?\s*(' + NUMBER + r')('
    + EXPONENT + r'|\s*[×x*]\s*10[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+)?\s*('
    + UNIT + r'|/(?:mL|L|l|mm3))?(?![\w^]|\.\d)')
FLATTENED = re.compile(r'(?:' + NUMBER + r')\s*[×x*]\s*10\d+(?:/|\s*/)[A-Za-z]+')
COMPOSITE = re.compile(r'(?:' + NUMBER + r')\s*(?:–|—|-|to|×|x|\*|/)\s*(?:'
                       + NUMBER + r')\s*' + UNIT)
SUPERSCRIPT = str.maketrans('⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺', '0123456789-+')


def explicit_notation(text):
    return re.sub(r'10([⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+)',
                  lambda m: '10^(' + m[1].translate(SUPERSCRIPT) + ')', text)


def parse_measurement(text: str) -> Measurement | None:
    """Parse one scalar only, rejecting ranges and lost exponent markup."""
    if not isinstance(text, str) or len(text) > 1200: return None
    normalized = explicit_notation(text.strip()).replace('−', '-')
    match = PATTERN.fullmatch(normalized)
    if not match: return None
    comparator, number, _, e, bracketed_power, bare_power, unit = match.groups()
    power = bracketed_power or bare_power
    exponent = int(e or power or 0)
    if abs(exponent) > 30: return None
    mantissa = Decimal(number.replace(',', ''))
    with localcontext() as ctx:
        ctx.prec = max(80, len(number) + abs(exponent) + 5)
        magnitude = format(mantissa * (Decimal(10) ** exponent), 'f')
    # Normalize only spelling, not scale (mg is still mg, not g).
    if unit:
        unit = re.sub(r'/hr\b', '/h', unit).replace('/l', '/L')
        unit = unit.replace('umol', 'µmol').replace('ug', 'µg')
    return Measurement(raw_text=text, comparator={'≤':'<=', '≥':'>='}.get(comparator, comparator or '='),
        mantissa=str(mantissa), power_of_ten=exponent, magnitude=magnitude,
        unit=unit, notation='scientific' if e or power else 'decimal')


def _model_measurement(item):
    number, unit = item.get('numeric_value'), item.get('unit')
    if number is None: return None
    comparator = item.get('comparator')
    prefix = comparator if comparator in {'<','<=','>','>='} else ''
    # A common LLM representation uses the multiplier as part of the unit.
    if isinstance(unit, str) and re.match(r'^(?:[×x*]\s*)?10(?:\^|[⁰¹²³⁴⁵⁶⁷⁸⁹])', unit):
        unit = re.sub(r'^[×x*]\s*', '', unit)
        return parse_measurement(f'{prefix}{number}×{unit}')
    return parse_measurement(f'{prefix}{number}' + (' ' + unit if unit else ''))


def observation_measurements(section, segments):
    """Compare raw structured fields to uniquely located source result notation.

    Prefer the literal text_value. Otherwise match the model's magnitude/unit
    against its own quotes, never other facts or reference_range_text. Multiple
    possible results remain unresolved. An accepted sidecar is still unreviewed.
    """
    rows = []
    texts = {s['segment_id']: s['text'] for s in segments}
    for i, item in enumerate((section or {}).get('items', [])):
        quotes = [e['quote'] for e in item.get('evidence', []) if isinstance(e, dict)
            and isinstance(e.get('quote'), str) and e['quote'] and
            (e['quote'] in texts.get(e.get('source_section'), '') if e.get('source_section') in texts
             else any(e['quote'] in t for t in texts.values()))]
        row = {'pointer': f'/items/{i}', 'name': item.get('name'),
            'model_fields': {k:item.get(k) for k in ('text_value','numeric_value','comparator','unit')},
            'measurement': None, 'status': 'unresolved', 'clinical_entailment_verified': False,
            'field_mutation': False, 'source_quote': None}
        raw = item.get('text_value'); modeled = _model_measurement(item)
        quote = next((q for q in quotes if raw and raw in q), None)
        reference = item.get('reference_range_text') or ''
        def search_view(q):
            return COMPOSITE.sub('', FLATTENED.sub('', q.replace(reference or '\0', '')))
        result = parse_measurement(raw) if quote and raw in search_view(quote) else None
        if not result:
            candidates = []
            for q in quotes:
                # Explicitly erase only the separately documented reference
                # range from this search view; immutable quotes are retained.
                search = search_view(q)
                for match in RAW_PATTERN.finditer(search):
                    measured = parse_measurement(match.group(0).strip())
                    if measured and measured.unit and modeled and (
                            Decimal(measured.magnitude) == Decimal(modeled.magnitude) and measured.unit == modeled.unit):
                        candidates.append((measured, q))
            if len(candidates) == 1: result, quote = candidates[0]
        # Frozen older sources may have flattened 10<sup>9</sup> to 109.
        # Do not convert that ambiguous spelling into a guessed exponent.
        if any(re.search(r'[×x*]\s*10\d+(?:/|\s*/)', q) for q in quotes) and not result:
            row['status'] = 'ambiguous_source_notation'
        if result:
            row['measurement'] = result.model_dump(); row['source_quote'] = quote
            row['status'] = ('source_parse_only' if modeled is None else
                'matched_model_fields' if (Decimal(result.magnitude) == Decimal(modeled.magnitude)
                    and result.unit == modeled.unit and result.comparator == modeled.comparator) else 'conflict')
        rows.append(row)
    return rows
