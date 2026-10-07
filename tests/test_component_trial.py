import copy
import gzip
import json
from pathlib import Path

import pytest

from openpatients2.component_trial import run_component_trial, refresh_bundle
from openpatients2.evidence_ledger import attribute_registry, check_attribute_audit, route_segments
from openpatients2.table_cells import inventory, patient_scope, check_cell_batch
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


def test_actual_poisoning_table_preserves_admission_followup_and_all_columns():
    articles=[json.loads(l) for l in gzip.open('benchmarks/patient-bundle/articles.jsonl.gz','rt')]
    a=next(a for a in articles if a['article_id']=='PMC12802722.1')
    inv=inventory(a)
    admission=[c for c in inv['cells'] if c['segment_id']=='b00016']
    followup=[c for c in inv['cells'] if c['segment_id']=='b00032']
    assert [c['raw_value'] for c in admission]==['139','143','122']
    assert [c['raw_value'] for c in followup]==['149','152','135']
    assert {c['time_header'] for c in admission}=={'On admission'}
    assert {c['time_header'] for c in followup}=={'At 48 h'}
    assert {c['raw_unit'] for c in admission+followup}=={'mmol/L'}
    assert len(inv['original_table_segments'])==sum(s['kind'].startswith('table') for s in a['segments'])
    assert all(c['source_quote']==next(s['text'] for s in a['segments'] if s['segment_id']==c['segment_id']) for c in inv['cells'])


def test_patient_column_cannot_be_guessed_from_generated_patient_number():
    a=table_article();p=roster(a)['patients'][0];cells=inventory(a)['cells']
    assert [patient_scope(c,a,p) for c in cells]==['target','other_patient']
    p=copy.deepcopy(p);p['patient_id']='p99'
    assert patient_scope(cells[0],a,p)=='target' # identity evidence, not ordinal
    p['identity_evidence']=[]
    assert patient_scope(cells[0],a,p)=='unresolved'


def test_good_cell_survives_invalid_neighbor_and_wrong_patient_value_is_quarantined():
    a=table_article();p=roster(a)['patients'][0];cells=inventory(a)['cells']
    value={'cells':[{'cell_id':c['cell_id'],'status':'extracted','observation':observation(c),'reason':'Source table'} for c in cells]}
    result=check_cell_batch(value,cells,a,p,lambda s:s)
    assert len(result['accepted'])==1 and len(result['quarantine'])==1
    bad=copy.deepcopy(value);bad['cells'][0]['observation']['numeric_value']=143
    assert not check_cell_batch(bad,cells,a,p,lambda s:s)['accepted']
    bad=copy.deepcopy(value);bad['cells'][0]['observation']['unit']='mol/L'
    assert not check_cell_batch(bad,cells,a,p,lambda s:s)['accepted']


def test_complex_table_rows_are_retained_unresolved():
    a=table_article();s=next(s for s in a['segments'] if s['kind']=='table_row')
    s['text']+=' [rowspan=2,colspan=1]'
    inv=inventory(a)
    assert not inv['cells'] and inv['unresolved_rows'][0]['segment_id']==s['segment_id']
    assert inv['original_table_segments']


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


@pytest.mark.parametrize('component',['attribute-audit','episode-rebuild','table-observations'])
async def test_components_freeze_discovery_pixels_other_domains_and_count_only_new_calls(tmp_path,monkeypatch,component):
    a=table_article();calls=[]
    def reply(task,messages):
        calls.append(task)
        if task=='demographics':return demographics(a)
        if task=='ledger_attribute_audit':
            facts=json.loads(messages[1]['content'].split('FACTS_TO_CHALLENGE: ')[1].split('\nSCHEMA:')[0])
            return {'checked_fact_ids':[f['fact_id'] for f in facts],'challenges':[],'limitations':[]}
        if task.startswith('ledger_'):
            source=json.loads(messages[1]['content'].split('SOURCE_JSON:\n')[1].split('\nTASK:\n')[0])
            return {'schema_version':'episode-ledger-delta/1','record_id':source['record_id'],
                    'events':[],'fact_links':[],'edges':[],'limitations':[]}
        if task=='table_observations':
            cells=json.loads(messages[1]['content'].split('\nCELLS:\n')[1].split('\nSCHEMA:\n')[0])
            return {'cells':[{'cell_id':c['cell_id'],'status':'extracted','observation':observation(c),'reason':'Explicit source table'} for c in cells]}
        return answer(task,a)
    http,_=setup_mock(monkeypatch,a,responses=reply)
    sample=input_file(tmp_path,[a]);base=tmp_path/'base';config={'max_articles':1,'max_output_tokens':256,'max_retry_tokens':256}
    async with http:
        await run_pilot(config,sample,base,['http://localhost:8000/v1'],8192,'targeted',http=http)
        baseline=(base/'patients.jsonl').read_text();original=json.loads(baseline);calls.clear()
        report=await run_component_trial(config,sample,tmp_path/'component',['http://localhost:8000/v1'],8192,
            baseline_dir=base,component=component,seed=42,http=http)
    assert not {'roster','figure_attribution','figure_visuals','pixel_attribution','summary'} & set(calls)
    assert (base/'patients.jsonl').read_text()==baseline
    delivered=json.loads((tmp_path/'component/patients.jsonl').read_text())
    assert delivered['vision']==original['vision']
    assert delivered['companions']['summary']==original['companions']['summary']
    assert delivered['sections']['demographics']==original['sections']['demographics']
    assert report['model_calls']==len(calls)
    assert report['tokens']['output_tokens']==20*len(calls)
    if component=='table-observations':
        assert len(delivered['sections']['observations']['items'])==1
        assert delivered['sections']['observations']['items'][0]['numeric_value']==139
        assert delivered['component_consistency']['clinical_facts_changed']
    if component=='episode-rebuild':assert delivered['sections']==original['sections']
    assert not report['clinical_accuracy_verified']


def test_routing_cannot_hide_table_even_when_map_mentions_only_prose():
    a=table_article();p=roster(a)['patients'][0];sid=a['segments'][0]['segment_id']
    chunk={'chunk_id':'c1','fragments':a['segments']}
    result={'status':'valid','data':{'propositions':[{'tasks':['observations'],'patient_ids':['p1'],
        'scope':'individual','evidence':[{'segment_id':sid,'quote':'woman'}],'attribution_evidence':[]}]}}
    chosen,_=route_segments(a,p,'observations',[(chunk,result)])
    assert {s['segment_id'] for s in a['segments'] if s['kind'].startswith('table')}<=chosen


async def test_real_attribute_id_repair_changes_only_challenged_flag_and_refreshes_fact_ids(tmp_path,monkeypatch):
    a=table_article();cell=inventory(a)['cells'][0];calls=[]
    initial={'coverage':'limited','limitations':['Authored partial fixture'], 'documentation_status':'documented',
             'documentation_evidence':[], 'items':[observation(cell,flag='high')]}
    def reply(task,messages):
        calls.append(task)
        if task=='observations':return initial
        if task=='ledger_attribute_audit':
            facts=json.loads(messages[1]['content'].split('FACTS_TO_CHALLENGE: ')[1].split('\nSCHEMA:')[0])
            challenges=[]
            for fact in facts:
                if fact['task']=='observations':
                    attr=next(x for x in fact['attributes'] if x['pointer']=='/flag')
                    challenges.append({'attribute_id':attr['attribute_id'],'verdict':'unsupported',
                        'reason':'The source gives no abnormality flag.',
                        'evidence':[{'segment_id':cell['segment_id'],'quote':cell['source_quote']}]})
            return {'checked_fact_ids':[f['fact_id'] for f in facts],'challenges':challenges,'limitations':[]}
        if task=='ledger_repair_observations':
            repaired=copy.deepcopy(initial);repaired['items'][0]['flag']='unknown';return repaired
        return answer(task,a)
    http,_=setup_mock(monkeypatch,a,responses=reply)
    sample=input_file(tmp_path,[a]);base=tmp_path/'base';config={'max_articles':1,'max_output_tokens':256,'max_retry_tokens':256}
    async with http:
        await run_pilot(config,sample,base,['http://localhost:8000/v1'],8192,'targeted',http=http)
        original=json.loads((base/'patients.jsonl').read_text());calls.clear()
        # A baseline timeline links the old fact. A corrected fact gets a new ID,
        # so the component must not leave the old link in the delivered graph.
        from openpatients2.component_trial import _rows
        old_row=next(r for r in _rows(a,original) if r['task']=='observations')
        old_fact=old_row['review_id'];span=old_row['evidence_spans'][0]
        original['companions']['timeline_v2']['events']=[{'event_id':'e1','record_id':'PMC1.1:p1',
            'episode_id':None,'kind':'test','occurrence':'occurred','description':'Admission sodium test',
            'fact_ids':[old_fact],'evidence':[span],'attribution_evidence':[span],'times':[]}]
        (base/'patients.jsonl').write_text(json.dumps(original)+'\n')
        await run_component_trial(config,sample,tmp_path/'out',['http://localhost:8000/v1'],8192,
            baseline_dir=base,component='attribute-audit',seed=42,http=http)
    delivered=json.loads((tmp_path/'out/patients.jsonl').read_text())
    expected=copy.deepcopy(initial['items'][0]);expected['flag']='unknown'
    assert delivered['sections']['observations']['items']==[expected]
    assert calls.count('ledger_repair_observations')==1
    assert delivered['component_consistency']['removed_old_fact_links']==[old_fact]
    assert delivered['companions']['timeline_v2']['events'][0]['fact_ids']==[]
    assert old_fact not in delivered['fact_review']['review_ids']
    assert delivered['component_consistency']['summary_status']=='frozen_baseline_requires_reconciliation'
    assert json.loads((base/'patients.jsonl').read_text())==original
