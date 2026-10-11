import copy
import gzip
import json
from pathlib import Path

import pytest

from openpatients2.evidence_ledger import attribute_registry, check_attribute_audit, route_segments
from test_pilot_extract import (article, roster, XML, setup_mock, input_file,
    answer, demographics, run_pilot)


TABLE='''<table-wrap id="T1"><caption><p>Laboratory values on admission.</p></caption><table>
<thead><tr><th>Test</th><th>Case 1</th><th>Case 2</th></tr></thead>
<tbody><tr><td>Sodium (mmol/L)</td><td>139</td><td>143</td></tr></tbody>
</table></table-wrap>'''


def table_article():
    return article(XML.replace('<title>Case</title>','<title>Case 1</title>').replace('</sec>',TABLE+'</sec>'))


def observation(cell,**updates):
    value={'subject':'index_patient','assertion':'present','temporality':'current',
        'time':{'text':cell['time_header'],'relation':'at' if cell['time_header'] else 'unknown','anchor':None,'date_iso':None},
        'evidence':[{'source_section':cell['segment_id'],'quote':cell['source_quote']}],
        'name':'Sodium','kind':'laboratory','status':'resulted','result_absent_reason':None,
        'numeric_value':float(cell['raw_value']),'text_value':cell['raw_value'],'comparator':'=',
        'unit':cell['raw_unit'],'flag':'unknown','reference_range_text':None,'specimen':None,
        'method':None,'body_site':None}
    return {**value,**updates}


def test_audit_ids_resolve_scalar_fields_without_exposing_evidence_as_editable():
    rows=[{'review_id':'f1','candidate':{'time':{'text':'18 months before'},'numeric_value':13.1,
        'evidence':[{'quote':'source'}]}}]
    reg=attribute_registry(rows);assert not any('evidence' in x['pointer'] for x in reg)
    aid=next(x['attribute_id'] for x in reg if x['pointer']=='/time/text')
    value={'checked_fact_ids':['f1'],'challenges':[{'attribute_id':aid,'verdict':'unsupported',
        'reason':'Earlier vs current measurement','evidence':[{'segment_id':'s1','quote':'13.1 cm'}]}],'limitations':[]}
    checked=check_attribute_audit(value,rows,[{'segment_id':'s1','text':'13.1 cm'}],id_contract=True)
    assert checked['challenges'][0]['pointer']=='/time/text'
    value['challenges'][0]['attribute_id']='a999'
    with pytest.raises(ValueError,match='attribute ID'):check_attribute_audit(value,rows,[],id_contract=True)


def test_routing_cannot_hide_table_even_when_map_mentions_only_prose():
    a=table_article();p=roster(a)['patients'][0];sid=a['segments'][0]['segment_id']
    chunk={'chunk_id':'c1','fragments':a['segments']}
    result={'status':'valid','data':{'propositions':[{'tasks':['observations'],'patient_ids':['p1'],
        'scope':'individual','evidence':[{'segment_id':sid,'quote':'woman'}],'attribution_evidence':[]}]}}
    chosen,_=route_segments(a,p,'observations',[(chunk,result)])
    assert {s['segment_id'] for s in a['segments'] if s['kind'].startswith('table')}<=chosen


