"""Reproduce the October 6 source audit without modifying historical gold."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from openpatients2.data import read_jsonl
from openpatients2.bundle_quality import validate_bundle_gold


def reviewed_reference(root):
    folder = root / 'benchmarks/patient-bundle'
    original = (folder / 'reference.json').read_bytes()
    reference = json.loads(original)
    articles = {a['article_id']:a for a in read_jsonl(folder/'articles.jsonl.gz')}
    changes = []
    for check in reference['checks']:
        if check['id'] in {'C0163','C0164','C0167','C0168'}:
            old = deepcopy(check['pattern']); alternative = deepcopy(old)
            alternative.pop('text_value'); alternative['interpretation'] = 'positive'
            check['pattern'] = {'one_of':[old,alternative]}
            changes.append({'id':check['id'],'reason':'Same-patient marker positivity may occupy interpretation or text_value.'})
        if check['id']=='B0071':
            check['pattern']['assertion'] = 'possible'
            check['description'] = 'Concern about cow\u2019s milk allergy remains possible; sensitization is not confirmation.'
            changes.append({'id':check['id'],'reason':'Source reports suspected allergy, not confirmed clinical allergy.'})
        if check['id']=='C0166':
            check['pattern']['name']['regex'] = r'solitary fibrous|\bSFT\b'
            a = articles['PMC12285374.1']
            shared = next(s for s in a['segments'] if 'In this report, we describe two cases' in s['text'])
            check['source'].append({'segment_id':shared['segment_id'],'quote':shared['text']})
            changes.append({'id':check['id'],'reason':'Repair abbreviation boundary and include explicit shared two-case diagnosis evidence.'})
    disputed = 'PMC13575834.1'
    for row in reference['articles']:
        if row['article_id']==disputed:
            row['evaluation_status'] = 'unadjudicated'
            row['evaluation_note'] = 'Illustrative procedure has no adjudicated individual clinical course; neither roster decision is scored.'
    removed = [c['id'] for c in reference['checks'] if c['record_id'].split(':')[0]==disputed]
    reference['checks'] = [c for c in reference['checks'] if c['id'] not in removed]
    for key in ('timelines','summary'):
        reference['bundle_gold'][key] = [g for g in reference['bundle_gold'][key]
                                        if g['record_id'].split(':')[0]!=disputed]
    changes.append({'excluded_checks':removed,'reason':'Provisional illustrative patient must not be treated as adjudicated gold.'})
    a = articles['PMC12285374.1']; segment = next(s for s in a['segments'] if s['segment_id']=='b00004')
    reference['checks'].append({'id':'R20261006-laterality','record_id':a['article_id']+':p1',
        'task':'oncology','collection':'tumors','kind':'forbidden','category':'unsupported_field',
        'description':'A bilateral craniotomy does not establish bilateral meningioma.',
        'pattern':{'name':{'regex':'meningioma'},'laterality':'bilateral'},
        'source':[{'segment_id':segment['segment_id'],'quote':'He underwent bilateral craniotomy for meningioma in 2009 and 2014.'}],
        'text_sha256':a['text_sha256'],'xml_sha256':a['xml_sha256']})
    reference['benchmark_revision'] = 'patient-bundle/reviewed-20261006'
    reference['representation_review'] = {'status':'targeted_source_audit','changes':changes,
        'historical_reference_sha256':hashlib.sha256(original).hexdigest(),
        'clinical_precision_and_comprehensive_recall_established':False,
        'test_notice':'Previously exposed development sources; corrected labels do not create a new blinded test.'}
    validate_bundle_gold(reference,list(articles.values()))
    return reference


if __name__=='__main__':
    root = Path(__file__).resolve().parents[1]
    path = root/'benchmarks/patient-bundle/reference-reviewed.json'
    path.write_text(json.dumps(reviewed_reference(root),indent=2,ensure_ascii=False)+'\n')
    print(path)
