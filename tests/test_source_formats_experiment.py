"""Citation-only recovery must never invent or change a clinical fact."""
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'experiments'))
from score_source_formats_v1 import whitespace_citations
from source_formats_v1 import tidy
from source_format_duplicate_audit_v1 import identical_duplicates
import pytest


def test_pdf_whitespace_restores_actual_source_span_without_changing_value():
    source=[{'segment_id':'page-4','heading':'PDF page 4','text':'Sodium  143\n  152 mg/L'}]
    item={'numeric_value':143,'unit':'mg/L','evidence':[{'source_section':'[page-4] PDF page 4','quote':'Sodium 143 152 mg/L'}]}
    fixed,changes=whitespace_citations({'items':[item]},source)
    assert fixed['items'][0]['numeric_value']==143
    assert fixed['items'][0]['evidence'][0]=={'source_section':'page-4','quote':source[0]['text']}
    assert len(changes)==2
    assert item['evidence'][0]['quote']=='Sodium 143 152 mg/L'


def test_no_numeric_repair_or_borrowing_from_wrong_patient_segment():
    source=[{'segment_id':'a','heading':'','text':'Sodium  143 mg/L'},
            {'segment_id':'b','heading':'','text':'Sodium  122 mg/L'}]
    for sid,quote in [('a','Sodium 122 mg/L'),('b','Sodium 143 mg/L'),('missing','Sodium 143 mg/L')]:
        value={'items':[{'numeric_value':143,'evidence':[{'source_section':sid,'quote':quote}]}]}
        fixed,changes=whitespace_citations(value,source)
        assert fixed==value and not changes


def test_layout_cleaning_preserves_column_spaces_and_linebreaks():
    assert tidy('   A     B   \n\n\n  1     2  \n')=='A     B\n\n  1     2'


def test_identical_duplicates_do_not_choose_between_conflicting_values():
    value,duplicates=identical_duplicates('{"value":143,"value":143}', 'stop')
    assert value=={'value':143} and duplicates==[{'key':'value','value':143}]
    for text,finish in [('{"value":143,"value":122}','stop'),('{"value":1,"value":true}','stop'),
                        ('{"value":143,"value":143}','length'),('{"value":NaN}','stop')]:
        with pytest.raises(ValueError):identical_duplicates(text,finish)
