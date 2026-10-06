import json

from openpatients2.pilot_extract import timeline_messages


def test_timeline_registry_identifies_facts_without_replacing_primary_evidence():
    article={'article_id':'PMC1.1','segments':[{'segment_id':'s1','text':'He received 5 mg on day 2.'}]}
    rows=[{'review_id':'f1','task':'medications','pointer':'/items/0',
           'candidate':{'name':'drug','dose_value':5,'evidence':[{'quote':'He received 5 mg on day 2.'}]}}]
    messages=timeline_messages(article,{'patient_id':'p1'},'PMC1.1:p1',{'f1':'PMC1.1:p1'},rows)
    source=json.loads(messages[1]['content'].split('SOURCE_JSON:\n')[1].split('\nTASK:\n')[0])
    assert source['known_fact_ids']==['f1']
    assert source['fact_registry'][0]['value']=={'name':'drug','dose_value':5}
    assert source['fact_registry'][0]['semantic_review']=='unreviewed'
    assert source['segments'][0]['text']==article['segments'][0]['text']
    assert 'primary source, not registry alone' in messages[1]['content']
