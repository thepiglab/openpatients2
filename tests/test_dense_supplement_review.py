"""Supplement review requires complete exact media and two independent receipts."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
from openpatients2.dense_store import load as load_store
store = load_store()
AID = 'PMC10998798.1'


@pytest.fixture
def specimen(tmp_path, monkeypatch):
    from openpatients2.dense import supplement_review as module
    source=json.loads(store.path(ROOT/'benchmarks/dense','sources',AID).read_text())
    annotation=json.loads(store.path(ROOT/'benchmarks/dense','annotations',AID).read_text())
    target=json.loads(store.path(ROOT/'benchmarks/dense','supplement-visual-submissions',AID,sid='MOESM1').read_text())
    target.pop('pixel_match_to_main_figure',None)
    assignment=target['analysis']['attribution']['assignments'][0]
    assignment.update(scope='unresolved',patient_ids=[],evidence=[{'segment_id':'b00068','quote':next(z['text'] for z in source['segments'] if z['segment_id']=='b00068')}])
    # Test fixtures bind harmless synthetic bytes; no real parent review is created.
    data=b'synthetic image bytes';image=tmp_path/'image.tif';image.write_bytes(data)
    md5=hashlib.md5(data).hexdigest();sha=hashlib.sha256(data).hexdigest()
    source['supplements'][0]['assets'][0]['md5']=md5
    target['assets'][0].update(md5=md5,sha256=sha,bytes=len(data),path=str(image))
    def save():
        for kind,value,sid in [('sources',source,None),('annotations',annotation,None),('supplement-visual-submissions',target,'MOESM1')]:
            store.path(tmp_path,kind,AID,sid=sid,mkdir=True).write_text(json.dumps(value))
    save()
    return module,tmp_path,source,annotation,target,save


def sign_both(specimen):
    m,r,_,_,_,_=specimen
    for role in ('agent','parent'):
        m.sign_review(r,AID,'MOESM1',role=role,reviewer='Claude synthetic test '+role,notes='Synthetic provenance validation fixture; not real source/pixel review.')


def test_single_tiff_joint_validation_and_separate_identity(specimen):
    m,r,s,a,t,_=specimen
    report=m.validate_submission(t,s,a)
    assert report['complete'] and report['source_assets']==report['inspected_assets']==1
    assert t['analysis']['visual']['figure_id']=='MOESM1'
    assert 'MOESM1' not in {f['figure_key'] for f in s['figures']}
    assert m.reviewed_supplement_targets(r,AID)==[]


def test_public_reader_requires_both_roles_and_all_bindings(specimen):
    m,r,_,_,t,_=specimen
    m.sign_review(r,AID,'MOESM1',role='agent',reviewer='Claude synthetic test agent',notes='Synthetic fixture inspection notes for tests')
    assert m.reviewed_supplement_targets(r,AID)==[]
    m.sign_review(r,AID,'MOESM1',role='parent',reviewer='Claude synthetic test parent',notes='Synthetic fixture inspection notes for tests')
    rows=m.reviewed_supplement_targets(r,AID)
    assert len(rows)==1 and rows[0][0]==t
    assert [p['role'] for p in rows[0][1]]==['agent','parent']
    assert all(p['complete'] and p['patient_identity_sha256'] for p in rows[0][1])


@pytest.mark.parametrize('change', ['source','supplement','target','patient'])
def test_changed_source_supplement_target_or_identity_stales_review(specimen,change):
    m,r,s,a,t,save=specimen;sign_both(specimen)
    if change=='source':s['text_sha256']='changed'
    elif change=='supplement':s['supplements'][0]['description']='Changed source metadata'
    elif change=='target':t['analysis']['visual']['detailed_description']+=' Changed.'
    else:a['patients'][0]['label']='Changed identity label'
    save()
    with pytest.raises(ValueError,match='stale|source'):
        m.reviewed_supplement_targets(r,AID)


def test_actual_byte_hash_md5_and_size_are_checked_when_signing(specimen):
    m,r,s,a,t,save=specimen
    Path(t['assets'][0]['path']).write_bytes(b'changed bytes')
    with pytest.raises(ValueError,match='bytes changed'):
        m.sign_review(r,AID,'MOESM1',role='agent',reviewer='Claude fixture reviewer',notes='Synthetic fixture inspection notes for tests')
    t['assets'][0]['sha256']=hashlib.sha256(b'changed bytes').hexdigest();save()
    with pytest.raises(ValueError,match='MD5 mismatch'):m.validate_submission(t,s,a)
    s['supplements'][0]['assets'][0]['md5']=hashlib.md5(b'changed bytes').hexdigest()
    t['assets'][0]['md5']=s['supplements'][0]['assets'][0]['md5']
    with pytest.raises(ValueError,match='byte count'):m.validate_submission(t,s,a)


def test_cache_eviction_after_signing_does_not_erase_review(specimen):
    m,r,_,_,t,_=specimen;sign_both(specimen)
    Path(t['assets'][0]['path']).unlink()
    assert len(m.reviewed_supplement_targets(r,AID))==1
    with pytest.raises(FileNotFoundError):m.sign_review(r,AID,'MOESM1',role='agent',reviewer='Claude fixture reviewer',notes='Synthetic fixture inspection notes for tests')


@pytest.mark.parametrize('status',['partial','unavailable'])
def test_incomplete_assets_cannot_receive_completed_receipt(specimen,status):
    m,r,s,a,t,save=specimen;t.update(status=status,limitations=['Missing image view']);save()
    assert m.validate_submission(t,s,a)['complete'] is False
    with pytest.raises(ValueError,match='cannot receive completed'):
        m.sign_review(r,AID,'MOESM1',role='agent',reviewer='Claude fixture reviewer',notes='Synthetic fixture inspection notes for tests')
    assert not store.receipt(r,'supplement','agent',AID,'MOESM1').exists()


@pytest.mark.parametrize('change',['missing','extra','duplicate','second_source_asset'])
def test_exact_source_asset_coverage(specimen,change):
    m,_,s,a,t,_=specimen
    if change=='missing':t['assets']=[]
    elif change=='extra':t['assets'][0]['url']='https://example.invalid/undeclared.tif'
    elif change=='duplicate':t['assets'].append(deepcopy(t['assets'][0]))
    else:
        another=deepcopy(s['supplements'][0]['assets'][0]);another['url']+='&second=1';s['supplements'][0]['assets'].append(another)
    with pytest.raises(ValueError,match='coverage|undeclared|Duplicate'):m.validate_submission(t,s,a)


@pytest.mark.parametrize('change',['license','filename','unknown_supplement','main_identity','wrong_figure','fabricated_quote','unknown_patient','missing_panel'])
def test_media_identity_and_joint_schema_are_checked(specimen,change):
    m,_,s,a,t,_=specimen
    if change=='license':t['assets'][0]['source_license']={}
    elif change=='filename':t['assets'][0]['filename']='changed.tif'
    elif change=='unknown_supplement':t['supplement_id']='UNKNOWN'
    elif change=='main_identity':s['figures'][0]['figure_key']='MOESM1'
    elif change=='wrong_figure':t['analysis']['visual']['figure_id']='Fig1'
    elif change=='fabricated_quote':t['analysis']['attribution']['assignments'][0]['evidence'][0]['quote']='Fabricated source claim'
    elif change=='unknown_patient':t['analysis']['attribution']['assignments'][0]['patient_ids']=['p99']
    else:t['analysis']['attribution']['assignments'][0]['panel']='A'
    with pytest.raises(ValueError):m.validate_submission(t,s,a)


def test_no_sidecars_returns_empty_without_inputs(tmp_path,specimen):
    assert specimen[0].reviewed_supplement_targets(tmp_path/'empty','unknown')==[]


def test_receipt_cannot_fake_completeness_or_role(specimen):
    m,r,_,_,_,_=specimen;sign_both(specimen)
    path=store.receipt(r,'supplement','parent',AID,'MOESM1')
    receipt=json.loads(path.read_text());receipt['complete']=False;path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError,match='complete'):m.reviewed_supplement_targets(r,AID)


def test_literal_source_link_required_for_resolved_ownership(specimen):
    m,_,s,a,t,_=specimen
    assignment=t['analysis']['attribution']['assignments'][0]
    assignment.update(scope='individual',patient_ids=['p1'])
    with pytest.raises(ValueError,match='reference this figure'):m.validate_submission(t,s,a)
    segment=next(z for z in s['segments'] if z['segment_id']=='b00068')
    segment['cross_references']=[{'ref_type':'supplementary-material','target_ids':['MOESM1']}]
    assert m.validate_submission(t,s,a)['complete']


def test_nonimage_supplement_cannot_be_declared_completed(specimen):
    m,_,s,a,t,_=specimen;s['supplements'][0]['assets'][0]['kind']='video'
    with pytest.raises(ValueError,match='Only image'):m.validate_submission(t,s,a)


def test_duplicate_sidecars_do_not_double_count_media(specimen):
    m,r,_,_,t,_=specimen
    store.path(r,'supplement-visual-submissions',AID,sid='extra').write_text(json.dumps(t))
    with pytest.raises(ValueError,match='Duplicate supplemental'):m.reviewed_supplement_targets(r,AID)


def test_complete_multiasset_source_is_bound_to_both_exact_urls(specimen):
    m,r,s,a,t,save=specimen
    extra=deepcopy(s['supplements'][0]['assets'][0]);extra['url']+='&second=1';s['supplements'][0]['assets'].append(extra)
    row=deepcopy(t['assets'][0]);row['url']=extra['url'];t['assets'].append(row);save()
    assert m.validate_submission(t,s,a)['inspected_assets']==2
    sign_both(specimen)
    receipts=m.reviewed_supplement_targets(r,AID)[0][1]
    assert all(p['source_assets']==2 and p['asset_sha256'] for p in receipts)


def test_exact_main_photo_bridge_preserves_source_asset_and_links(specimen):
    m,r,s,a,t,_=specimen
    asset=t['assets'][0]
    main=s['figures'][0];main['image_urls']=[asset['url']];main['media']=[{'url':asset['url'],'md5':asset['md5']}]
    t['pixel_match_to_main_figure']={'figure_id':main['figure_key'],'kind':'exact_photographic_correspondence','visual_cue':'Same recorded photographic scene, not similar anatomy','asset':{k:asset[k] for k in ('url','sha256','path')}}
    assignment=t['analysis']['attribution']['assignments'][0]
    assignment.update(scope='individual',patient_ids=['p1'],evidence=[{'segment_id':'b00070','quote':next(z['text'] for z in s['segments'] if z['segment_id']=='b00070')}])
    assert m.validate_submission(t,s,a)['complete']
    t['pixel_match_to_main_figure']['kind']='similar_anatomy'
    with pytest.raises(ValueError,match='exact photographic'):m.validate_submission(t,s,a)
