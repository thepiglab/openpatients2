import copy
from decimal import Decimal

import pytest

from openpatients2.measurements import observation_measurements, parse_measurement


@pytest.mark.parametrize('text,magnitude,unit,comparator', [
    ('115 mmol/L', '115', 'mmol/L', '='),
    ('>40,000 pmol/L', '40000', 'pmol/L', '>'),
    ('≤0.27 mg/dL', '.27', 'mg/dL', '<='),
    ('17.9×10⁹/L', '17900000000', '/L', '='),
    ('40.6×10^(9)/L', '40600000000', '/L', '='),
    ('13e9/L', '13000000000', '/L', '='),
    ('1.2e-3 g/L', '.0012', 'g/L', '='),
    ('22.4 nmol/L/hr', '22.4', 'nmol/L/h', '='),
])
def test_scalar_parser_preserves_original_and_decimal_precision(text, magnitude, unit, comparator):
    parsed = parse_measurement(text)
    assert Decimal(parsed.magnitude) == Decimal(magnitude)
    assert parsed.raw_text == text and parsed.unit == unit and parsed.comparator == comparator


@pytest.mark.parametrize('text', ['40.6×109/L', '5×15 mm', '115–131 mmol/L',
    '120/80 mmHg', 'between 1 and 3 mg', 'NaN', '1e99 mg/L', '11-month review',
    '17.9×10^(9/L', '17.9×10^9)/L'])
def test_ranges_dimensions_flattened_exponents_and_nonfinite_values_are_not_scalars(text):
    assert parse_measurement(text) is None


def extraction(quote, **fields):
    item = {'name':'Sodium', 'numeric_value':115, 'comparator':'=', 'unit':'mmol/L',
        'text_value':None, 'reference_range_text':None,
        'evidence':[{'quote':quote, 'source_section':'b1'}], **fields}
    section = {'items':[item]}
    return section, [{'segment_id':'b1', 'text':quote}]


def test_unique_model_value_unit_pair_does_not_select_other_lab_or_reference():
    section, segments = extraction('Sodium 115 mmol/L; potassium 6.4 mmol/L; reference 135 mmol/L.',
        reference_range_text='135 mmol/L')
    original = copy.deepcopy(section)
    row = observation_measurements(section, segments)[0]
    assert row['status'] == 'matched_model_fields'
    assert row['measurement']['magnitude'] == '115' and row['source_quote'] == segments[0]['text']
    assert section == original and row['field_mutation'] is False


def test_explicit_source_notation_exposes_llm_conflict_without_overwriting():
    section, segments = extraction('Lymphocytes 17.9×10⁹/L.', name='Lymphocytes',
        text_value='17.9×10⁹/L', numeric_value=17.9, unit='/L')
    row = observation_measurements(section, segments)[0]
    assert row['status'] == 'conflict'
    assert row['measurement']['power_of_ten'] == 9
    assert row['model_fields']['numeric_value'] == 17.9
    section['items'][0]['unit'] = '10^9/L'
    assert observation_measurements(section, segments)[0]['status'] == 'matched_model_fields'


def test_source_fragment_with_unicode_exponent_stays_literal():
    section, segments = extraction('Lymphocytes 17.9×10⁹/L.', name='Lymphocytes',
        numeric_value=17.9, unit='10^9/L')
    row = observation_measurements(section, segments)[0]
    assert row['status'] == 'matched_model_fields'
    assert row['measurement']['raw_text'] == '17.9×10⁹/L'
    assert row['measurement']['raw_text'] in row['source_quote']


def test_ambiguous_flattened_exponent_is_flagged_even_if_model_uses_suffix_number():
    section, segments = extraction('WBC 40.6×109/L.', numeric_value=109, unit='/L')
    assert observation_measurements(section, segments)[0]['status'] == 'ambiguous_source_notation'


def test_reference_threshold_and_nonliteral_or_duplicate_source_candidates_remain_unresolved():
    section, segments = extraction('Result not reported; reference <2,000 pmol/L.',
        text_value='<2,000 pmol/L', numeric_value=2000, comparator='<', unit='pmol/L',
        reference_range_text='<2,000 pmol/L')
    assert observation_measurements(section, segments)[0]['measurement'] is None


@pytest.mark.parametrize('quote,raw,number,unit', [
    ('Sodium 115–131 mmol/L.', '131 mmol/L', 131, 'mmol/L'),
    ('Lesion 5×15 mm.', '15 mm', 15, 'mm'),
    ('Blood pressure 120/80 mmHg.', '80 mmHg', 80, 'mmHg'),
])
def test_source_range_dimension_or_composite_is_not_silently_split(quote,raw,number,unit):
    section, segments = extraction(quote,text_value=raw,numeric_value=number,unit=unit)
    assert observation_measurements(section, segments)[0]['measurement'] is None
    section, segments = extraction('Sodium 115 mmol/L twice: 115 mmol/L.')
    assert observation_measurements(section, segments)[0]['measurement'] is None
    section['items'][0]['evidence'][0]['quote'] = 'Fabricated sodium 115 mmol/L'
    assert observation_measurements(section, segments)[0]['measurement'] is None
