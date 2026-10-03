import copy
import gzip
import json
import sys
from types import SimpleNamespace

import pytest

from openpatients2 import corpus_stats as stats
from openpatients2.data import read_jsonl
from test_articles import article


class FakeTokenizer:
    def __init__(self,*args):
        self.manifest={'model_id':'test','revision':'0'*40,'files':{}}
        self.template_hash='f'*64
    def count(self,text): return len(text.encode('utf8'))
    def prompt_count(self,messages,*args):
        return sum(len(m['content'].encode('utf8')) for m in messages)


def corpus(tmp_path,n=7):
    p=tmp_path/'articles.jsonl.gz'
    with gzip.open(p,'wt') as f:
        for i in range(n):
            a=article(); a['article_id']=f'PMC{i+1}.1'; a['status']='eligible'
            a['acquisition']={'discovery_lanes':['cardio' if i%2 else 'neuro']}
            f.write(json.dumps(a)+'\n')
    return p


def test_profile_stores_counts_not_token_ids_and_selects_reproducibly(tmp_path,monkeypatch):
    monkeypatch.setattr(stats,'TextTokenizer',FakeTokenizer)
    source=corpus(tmp_path)
    report=stats.profile(source,'unused',tmp_path/'profile',workers=2,sample_size=4,benchmark_workers=(1,2))
    assert report['articles']==7 and report['sample']['size']==4
    assert report['statistics']['words']['p75'] is not None
    assert report['token_ids_written'] is False and report['image_tokens_counted'] is False
    assert len({t['count_digest'] for t in report['cpu_worker_trials']})==1
    counts=list(read_jsonl(tmp_path/'profile'/'counts.jsonl.gz'))
    assert all(not isinstance(v,list) or k in {'discovery_lanes','topics'} for r in counts for k,v in r.items())
    chosen=list(read_jsonl(tmp_path/'profile'/'sample.jsonl.gz'))
    assert {a['article_id'] for a in chosen}=={r['article_id'] for r in report['sample']['articles']}
    second=stats.profile(source,'unused',tmp_path/'profile2',workers=1,sample_size=4,benchmark_workers=())
    assert report['sample']==second['sample']
    assert report['statistics']==second['statistics']
    with pytest.raises(FileExistsError): stats.profile(source,'unused',tmp_path/'profile')


def test_prose_lengths_do_not_count_headers_or_bibliography():
    a=article();a['status']='eligible'
    row=stats.profile_row(a,FakeTokenizer())
    prose='\n\n'.join(s['text'] for s in a['segments'])
    assert row['characters']==len(prose) and row['words']==len(prose.split())
    assert row['article_packet_tokens']>row['prose_tokens']
    assert row['table_rows']==2 and row['figures']==1
    a['license']['allowed']=False
    with pytest.raises(ValueError,match='eligible'): stats.profile_row(a,FakeTokenizer())


def test_profile_cap_and_duplicate_versions_fail_without_complete_report(tmp_path,monkeypatch):
    monkeypatch.setattr(stats,'TextTokenizer',FakeTokenizer)
    source=corpus(tmp_path)
    with pytest.raises(ValueError,match='cap reached'):
        stats.profile(source,'unused',tmp_path/'capped',max_articles=3,benchmark_workers=())
    assert (tmp_path/'capped'/'FAILED.json').exists() and not (tmp_path/'capped'/'profile.json').exists()
    a=article();a['status']='eligible'
    path=tmp_path/'duplicates.jsonl';path.write_text((json.dumps(a)+'\n')*2)
    with pytest.raises(ValueError,match='Duplicate'):
        stats.profile(path,'unused',tmp_path/'duplicates',benchmark_workers=())


def test_cpu_guard_rejects_gpu_job(monkeypatch):
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES','0')
    with pytest.raises(ValueError,match='CPU-only'): stats.require_cpu()


def test_stratified_selection_keeps_long_table_and_figure_challenges():
    rows=[]
    for i in range(12):
        rows.append({'article_id':f'PMC{i}.1','xml_sha256':'a'*64,'discovery_lanes':['a' if i<6 else 'b'],
            'roster_prompt_tokens':100000 if i==11 else 9000 if i==10 else 2000,'figures':i%2,'table_rows':i%3})
    chosen=stats.select_sample(rows,12,42)
    assert {r['article_id'] for r in chosen}=={r['article_id'] for r in rows}
    assert len({r['article_id'] for r in stats.select_sample(rows,4,42)})==4
    assert any(r['selection_stratum'][1]=='very_long' for r in chosen)


def test_tokenizer_downloader_never_requests_weights(tmp_path,monkeypatch):
    payloads={'tokenizer.json':b'{}','tokenizer_config.json':b'{"bos_token":"x"}'}
    import hashlib
    model={'model_id':'test/model','revision':'0'*40,'files':[{'rfilename':n,'size':len(b),
       'blobId':hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()} for n,b in payloads.items()]}
    model['files'].append({'rfilename':'model.safetensors','size':999999999})
    metadata=tmp_path/'metadata.json';metadata.write_text(json.dumps(model))
    seen=[]
    def download(repo,name,**kwargs):
        seen.append(name);p=kwargs['local_dir']/name;p.write_bytes(payloads[name]);return str(p)
    monkeypatch.setitem(sys.modules,'huggingface_hub',SimpleNamespace(hf_hub_download=download))
    report=stats.tokenizer_snapshot(tmp_path/'tokenizer',metadata)
    assert seen==list(stats.TOKENIZER_FILES) and report['weights_downloaded'] is False
