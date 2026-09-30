import json
from pathlib import Path
import pytest
from openpatients2.data import normalize,read_jsonl
from openpatients2.article_map import attach_article_map


def fixture(tmp_path):
    original={'_id':'pmc-8654144-1','description':'Synthetic fixture, not an original article case.','extra':['keep']}
    row=normalize(original)
    inp=tmp_path/'input.jsonl';inp.write_text(json.dumps(row)+'\n')
    mapping={'record_id':row['record_id'],'source_hash':row['source_hash'],
             'pmcid':'PMC8654144','pmid':'34925991','reviewed':True,
             'reviewer':'SYNTHETIC_TEST_REVIEW','mapping_source':'explicit test mapping only'}
    return row,inp,mapping


def test_crosswalk_preserves_all_original_values(tmp_path):
    row,inp,mapping=fixture(tmp_path);mp=tmp_path/'map.jsonl';mp.write_text(json.dumps(mapping)+'\n');out=tmp_path/'out.jsonl'
    result=attach_article_map(str(inp),str(mp),str(out));after=next(read_jsonl(out))
    assert result['mapped_records']==1 and after['original_row']==row['original_row']
    assert after['text']==row['text'] and after['provenance']['article']['pmcid']=='PMC8654144'
    assert after['provenance']['article']['source_crosswalk']['mapping_file_sha256']


@pytest.mark.parametrize('change',['hash','review','identifier','absent_row','duplicate'])
def test_bad_crosswalk_fails_without_publishing(tmp_path,change):
    row,inp,mapping=fixture(tmp_path);mp=tmp_path/'map.jsonl';out=tmp_path/'out.jsonl'
    if change=='hash':mapping['source_hash']='bad'
    elif change=='review':mapping['reviewed']=False
    elif change=='identifier':mapping['pmcid']='not-a-pmcid'
    elif change=='absent_row':mapping['record_id']='missing'
    mp.write_text((json.dumps(mapping)+'\n')*(2 if change=='duplicate' else 1))
    with pytest.raises(ValueError):attach_article_map(str(inp),str(mp),str(out))
    assert not out.exists()
