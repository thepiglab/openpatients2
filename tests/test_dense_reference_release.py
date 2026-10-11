"""Protect a reference release from drafts, stale reviews and fabricated evidence."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
from openpatients2.dense_store import load as load_store
store = load_store()


def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def release(tmp_path,monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT/'benchmarks/dense500'))
    validate=load_module('dense_reference_validate',ROOT/'benchmarks/dense500/validate.py')
    monkeypatch.setitem(sys.modules,'validate',validate)
    exporter=load_module('dense_reference_export',ROOT/'benchmarks/dense500/export.py')
    source=ROOT/'benchmarks/dense50'
    reviewed='PMC13624771.1';draft='PMC13624887.1'
    for aid in (reviewed,draft):
        for folder in ('sources','drafts'):
            shutil.copyfile(store.path(source, folder, aid),store.path(tmp_path, folder, aid, mkdir=True))
    for folder in ('annotations','reviews'):
        shutil.copyfile(store.path(source, folder, reviewed),store.path(tmp_path, folder, reviewed, mkdir=True))
    # The live receipt may be stale while a re-sign campaign is running; this fixture models a
    # current parent source review of the live annotation (independent of campaign state).
    receipt=json.loads(store.path(tmp_path, 'reviews', reviewed).read_text())
    receipt['annotation_sha256']=validate.digest(json.loads(store.path(tmp_path, 'annotations', reviewed).read_text()))
    store.path(tmp_path, 'reviews', reviewed, mkdir=True).write_text(json.dumps(receipt))
    (tmp_path/'retained-strata.json').write_text(json.dumps({reviewed:'gastrointestinal',draft:'dermatology'}))
    (tmp_path/'selection.json').write_text('[]')
    (tmp_path/'SELECTION_PLAN.md').write_text('Test selection plan')
    out=tmp_path/'release';out.mkdir()
    (out/'README.md').write_text('---\nlanguage: [en]\n---\nTest card\n')
    monkeypatch.setattr(exporter,'ROOT',tmp_path)
    monkeypatch.setattr(exporter,'ROOTS',[tmp_path])
    monkeypatch.setattr(exporter,'OUT',out)
    return exporter,validate,tmp_path,out,reviewed,draft


def test_release_excludes_unreviewed_targets_and_separates_catalog(release):
    exporter,_,_,out,reviewed,draft=release
    exporter.main()
    annotations=[json.loads(line) for line in (out/'legacy'/'annotations.jsonl').read_text().splitlines()]
    assert [row['article_id'] for row in annotations]==[reviewed]
    catalog={row['article_id']:row for row in map(json.loads,(out/'legacy'/'manifest.jsonl').read_text().splitlines())}
    assert catalog[draft]['review_status']=='not_started'
    assert catalog[reviewed]['review_status']=='parent_source_reviewed'
    assert all(row['article_url'].startswith('https://') for row in catalog.values())
    card=exporter.yaml.safe_load((out/'README.md').read_text().split('---',2)[1])
    # One unified dataset: a single default config with three splits; per-type files are legacy only.
    assert card['configs']==[{'config_name':'default','default':True,'data_files':[
        {'split':s,'path':f'data/{s}.jsonl'} for s in ('train','validation','test')]}]
    assert not (out/'annotations.jsonl').exists() and not (out/'manifest.jsonl').exists()
    assert all(not (out/'data'/f'{s}.jsonl').read_text() for s in ('train','validation','test'))  # nothing completed
    assert json.loads((out/'schema.json').read_text())['$id']=='openpatients2-unified-article/1'
    assert '<!-- unified-fields:start -->' in (out/'README.md').read_text()
    assert not (out/'legacy'/'test.jsonl').read_text()
    assert json.loads((out/'statistics.json').read_text())['complete_500_article_release'] is False
    progress=json.loads((out/'article_checklist.json').read_text())
    queue=progress['articles']
    assert len(queue)==2
    assert progress['completed_articles']==0
    assert queue[reviewed]['completed'] is False
    assert queue[reviewed]['checks']['parent_source_review'] is True
    assert 'full_schema_dual_review' in queue[reviewed]['remaining']
    assert 'parent_source_review' in queue[draft]['remaining']
    assert not (out/'PROGRESS.md').exists()


def test_empty_inventory_is_not_counted_as_pixel_review(release):
    exporter,*_=release
    assert exporter.pixel_review_status({'figures':[]},{'figures':[]})=='no_source_figures'
    source={'figures':[{'figure_id':'F1'}]}
    assert exporter.pixel_review_status(source,None)=='not_started'
    assert exporter.pixel_review_status(source,{'figures':[]})=='reviewed_with_unavailable_assets'
    assert exporter.pixel_review_status(source,{'figures':[{'status':'inspected'}]})=='agent_and_parent_reviewed'
    assert exporter.pixel_review_status(source,{'figures':[{'status':'inspected'},{'status':'unavailable'}]})=='reviewed_with_unavailable_assets'


def test_export_keeps_supplement_links_without_claiming_content_review(release):
    exporter,_,root,out,aid,_=release
    path=store.path(root, 'sources', aid)
    source=json.loads(path.read_text())
    source['supplements']=[{'supplement_id':'video1','url':'https://example.invalid/video.mp4'}]
    path.write_text(json.dumps(source))
    exporter.main()
    record=json.loads((out/'legacy'/'annotations.jsonl').read_text().splitlines()[0])
    item=record['source_supplements'][0]
    assert item['url']=='https://example.invalid/video.mp4'
    assert item['content_inspected'] is False
    assert item['review_status']=='linked_content_not_reviewed'


def test_changed_annotation_requires_new_source_review(release):
    exporter,_,root,_,aid,_=release
    path=store.path(root, 'annotations', aid)
    data=json.loads(path.read_text());data['patients'][0]['summary'][0]['text']='Changed after review'
    path.write_text(json.dumps(data))
    # A changed annotation is withheld from the release (not shown as reviewed)...
    exporter.main()
    out=exporter.OUT
    assert not (out/'legacy'/'annotations.jsonl').read_text()
    catalog={r['article_id']:r for r in map(json.loads,(out/'legacy'/'manifest.jsonl').read_text().splitlines())}
    assert catalog[aid]['review_status']=='stale_source_review_receipt'
    # ...and the strict policy still aborts.
    exporter.STALE_RECEIPT_POLICY='raise'
    try:
        with pytest.raises(AssertionError,match='stale review receipt'):
            exporter.main()
    finally:
        exporter.STALE_RECEIPT_POLICY='withhold'


def test_review_receipt_requires_structured_assessment(release):
    from review_record import save_review
    _,_,root,_,aid,_=release
    annotation=json.loads(store.path(root, 'annotations', aid).read_text())
    receipt_path=store.path(root, 'reviews', aid)
    original=receipt_path.read_bytes()
    with pytest.raises(TypeError,match='structured mapping'):
        save_review(root,annotation,[], 'Prose belongs inside the assessment mapping.')
    assert receipt_path.read_bytes()==original


def test_even_matching_receipt_cannot_admit_nonliteral_evidence(release):
    exporter,validator,root,_,aid,_=release
    path=store.path(root, 'annotations', aid);data=json.loads(path.read_text())
    data['patients'][0]['facts'][0]['evidence'][0]['quote']='Fabricated text that is absent from the article.'
    path.write_text(json.dumps(data))
    receipt_path=store.path(root, 'reviews', aid);receipt=json.loads(receipt_path.read_text())
    receipt['annotation_sha256']=validator.digest(data)
    receipt_path.write_text(json.dumps(receipt))
    # Default policy: the invalid article is withheld with an explicit status, never exported.
    exporter.main()
    _,_,_,out,aid,_=release
    assert not (out/'legacy'/'annotations.jsonl').read_text().strip()
    row=next(r for r in map(json.loads,(out/'legacy'/'manifest.jsonl').read_text().splitlines()) if r['article_id']==aid)
    assert row['review_status']=='invalid_annotation' and 'nonliteral or missing source quote' in json.dumps(row['withheld_reason'])
    checklist=json.loads((out/'article_checklist.json').read_text())
    assert checklist['articles'][aid]['withheld']['statuses']==['invalid_annotation']
    # Strict policy keeps the hard abort.
    exporter.STALE_RECEIPT_POLICY='raise'
    with pytest.raises(AssertionError,match='nonliteral or missing source quote'):
        exporter.main()


def test_uninspected_figure_cannot_have_pixel_description(release):
    _,validator,root,_,aid,_=release
    annotation=json.loads(store.path(root, 'annotations', aid).read_text())
    source=json.loads(store.path(root, 'sources', aid).read_text())
    annotation['figures'][0]['visual_description']='Claim derived from unseen image pixels'
    assert 'uninspected pixel claim' in validator.validate(annotation,source)


@pytest.fixture
def full_target(release):
    import schema_targets
    import schema_review
    _,_,root,_,aid,_=release
    annotation=json.loads(store.path(root, 'annotations', aid).read_text())
    source=json.loads(store.path(root, 'sources', aid).read_text())
    target=schema_targets.materialize(annotation,source)
    assert not target['source_gate_errors']
    return schema_targets,schema_review,target,annotation,source


def test_runtime_targets_preserve_facts_without_inventing_clinical_values(full_target):
    generator,reviewer,target,annotation,source=full_target
    reviewer.validate_submission(target,annotation,source)
    for patient,bundle in zip(annotation['patients'],target['patients']):
        assert set(bundle['sections'])==set(generator.TASK_MODELS)
        accepted={f['fact_id'] for f in patient['facts'] if f['scoring']=='include'}
        assert {f['fact_id'] for f in bundle['patient_state_index']['facts']}==accepted
        assert bundle['vision']['status']=='not_inspected'
        assert not bundle['vision']['caption_only']['pixel_observations']
        assert not bundle['semantic_coverage_verified']
        for section in bundle['sections'].values():
            if section['documentation_status']=='not_documented':
                assert not section.get('items')


def test_runtime_review_rejects_wrong_patient_provenance_and_invented_pixels(full_target):
    import copy
    _,reviewer,target,annotation,source=full_target
    wrong=copy.deepcopy(target)
    wrong['patients'][0]['source']['record_id']='another-patient'
    with pytest.raises(ValueError,match='source: clinical/provenance'):
        reviewer.validate_submission(wrong,annotation,source)
    wrong=copy.deepcopy(target)
    wrong['patients'][0]['vision']['status']='inspected'
    with pytest.raises(ValueError,match='Cannot invent pixel review'):
        reviewer.validate_submission(wrong,annotation,source)


def test_missing_substantive_item_is_not_silently_filled(full_target):
    from openpatients2.schemas import Medication
    generator,_,_,_,_=full_target
    with pytest.raises(ValueError,match='missing substantive required field'):
        generator.complete(Medication,{})


def test_reviewed_roster_may_clear_only_generated_pending_notice(full_target):
    import copy
    _,reviewer,target,annotation,source=full_target
    target=copy.deepcopy(target)
    pending='Reported distinct count and roster completeness await explicit schema review; cited-case discovery is not automatically completed.'
    target['roster']['roster_complete']=True
    target['roster']['limitations'].remove(pending)
    reviewer.validate_submission(target,annotation,source)
    assert target['roster']['limitations']
    target['roster']['limitations'].pop(0)
    with pytest.raises(ValueError,match='Roster clinical/ownership'):
        reviewer.validate_submission(target,annotation,source)


def test_full_bundle_release_requires_both_current_reviews(release,full_target):
    exporter,validator,root,out,aid,_=release
    _,_,target,annotation,source=full_target
    store.path(root, 'schema-submissions', aid, mkdir=True).write_text(json.dumps(target))
    exporter.main()
    assert not (out/'legacy'/'patients.jsonl').read_text()  # conversion alone is insufficient
    for role in ('agent','parent'):
        receipt={'article_id':aid,'status':'schema_source_reviewed','role':role,'reviewer':f'Claude synthetic {role}','inspection_notes':'Synthetic fixture inspection notes for tests',
            'annotation_sha256':validator.digest(annotation),'target_sha256':validator.digest(target),
            'source_text_sha256':source['text_sha256']}
        store.receipt(root,'schema',role,aid,mkdir=True).write_text(json.dumps(receipt))
    exporter.main()
    patients=[json.loads(line) for line in (out/'legacy'/'patients.jsonl').read_text().splitlines()]
    assert len(patients)==len(annotation['patients'])
    assert all(p['clinical_accuracy_verified'] is False for p in patients)
    target['roster']['roster_complete']=not target['roster']['roster_complete']
    store.path(root, 'schema-submissions', aid, mkdir=True).write_text(json.dumps(target))
    # Default policy withholds a stale bundle (re-sign campaigns) instead of aborting the release.
    exporter.main()
    assert not (out/'legacy'/'patients.jsonl').read_text()
    catalog={r['article_id']:r for r in map(json.loads,(out/'legacy'/'manifest.jsonl').read_text().splitlines())}
    assert catalog[aid]['full_schema_review_status']=='stale_schema_receipt'
    exporter.STALE_RECEIPT_POLICY='raise'
    try:
        with pytest.raises(ValueError,match='stale/missing agent schema review target_sha256'):
            exporter.main()
    finally:
        exporter.STALE_RECEIPT_POLICY='withhold'


def test_coverage_flags_missing_diagnosis_without_claiming_recall(full_target):
    import copy
    from coverage_audit import clinical_coverage
    _,_,target,_,_=full_target
    patient=copy.deepcopy(target['patients'][0])
    patient['sections']['oncology']['tumors']=[{'name':'source-documented tumor'}]
    patient['sections']['conditions']['items']=[]
    result=clinical_coverage(patient)
    assert len(result['domains'])==14
    assert any(x['kind']=='tumor_without_general_condition' for x in result['attention'])
    assert result['clinical_recall_measured'] is False


def test_visual_review_binds_assets_and_requires_each_panel(release,tmp_path):
    import copy,hashlib
    from visual_review import validate_submission
    # Synthetic test bytes exercise identity binding, not visual correctness.
    path=tmp_path/'test-image';path.write_bytes(b'image-test-fixture')
    asset={'url':'https://example.invalid/figure.png','sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'path':str(path)}
    source={'article_id':'test','text_sha256':'source-hash','segments':[{'segment_id':'s1','text':'Patient one in Figure F.'}],
        'figures':[{'figure_key':'F','caption_segment_ids':['s1']}]}
    annotation={'patients':[{'patient_id':'p1'}]}
    assets={'source_text_sha256':'source-hash','figures':[{'figure_id':'F','assets':[asset]}]}
    panel={'panel':'a','general_description':'Visible specimen','detailed_description':'Test placeholder, not a medical annotation.',
        'image_kind':'other','has_chart':False,'charts':[],'imaging_modalities':[],'imaging_submodalities':[],
        'medical_domains':[],'body_parts':[],'mentioned_categories':[],'pixel_observations':[],
        'caption_claims':[],'clinical_significance':[],'diagram_relations':[],'limitations':[]}
    assignment={'panel':'a','patient_ids':['p1'],'scope':'individual','subject':'patient',
        'evidence':[{'segment_id':'s1','quote':'Patient one in Figure F.'}],'rationale':'Fixture source.'}
    target={'format':'dense-figure-reference/1','article_id':'test','source_text_sha256':'source-hash',
        'figures':[{'figure_id':'F','status':'inspected','assets':[asset],'limitations':[],
            'analysis':{'visual':{'figure_id':'F','general_description':'Test','detailed_description':'Test only',
                'panels':[panel],'limitations':[]},'attribution':{'figure_id':'F','assignments':[assignment],'limitations':[]}}}]}
    assert validate_submission(target,source,annotation,assets)['pixel_accuracy_measured'] is False
    missing=copy.deepcopy(target)
    missing['figures'][0]['analysis']['visual']['panels'].append({**panel,'panel':'b'})
    with pytest.raises(ValueError,match='Every visual panel needs attribution'):
        validate_submission(missing,source,annotation,assets)
    changed=copy.deepcopy(target);changed['figures'][0]['assets'][0]['sha256']='wrong'
    with pytest.raises(ValueError,match='bind all declared image assets'):
        validate_submission(changed,source,annotation,assets)
    path.write_bytes(b'different bytes')
    with pytest.raises(ValueError,match='Image bytes changed'):
        validate_submission(target,source,annotation,assets)
    # Export can use signed hashes after reviewed cache files are removed.
    path.unlink()
    validate_submission(target,source,annotation,assets,verify_files=False)


def test_reviewed_supplement_inventory_retains_identity_and_completion(release):
    exporter,*_=release
    source={'supplements':[{'supplement_id':'image1','assets':[{'url':'https://example.invalid/image.tif'}]},
                           {'supplement_id':'video1','assets':[{'url':'https://example.invalid/video.mp4'}]}]}
    receipts=[{'role':'agent'},{'role':'parent'}]
    inventory=exporter.supplement_inventory(source,[({'supplement_id':'image1'},receipts)])
    assert inventory[0]['content_inspected'] is True
    assert inventory[0]['reviews']==receipts
    assert inventory[0]['assets']==source['supplements'][0]['assets']
    assert inventory[1]['content_inspected'] is False
    assert 'content_inspected' not in source['supplements'][0]


def test_reviewed_pdf_exports_as_document_not_pixels(release,full_target,monkeypatch):
    import supplement_review
    exporter,validator,root,out,aid,_=release
    _,_,target,annotation,source=full_target
    source['supplements']=[{'supplement_id':'SM1','assets':[{'url':'https://example.invalid/checklist.pdf'}],
                            'content_inspected':False}]
    store.path(root, 'sources', aid, mkdir=True).write_text(json.dumps(source))
    document={'format':supplement_review.DOCUMENT_FORMAT,'article_id':aid,'supplement_id':'SM1',
              'document_classification':'administrative_checklist','assets':[], 'contents_summary':'Synthetic checklist'}
    receipts=[{'role':'agent'},{'role':'parent'}]
    # Contract validation and receipts are independently tested; exercise export routing here.
    monkeypatch.setattr(supplement_review,'reviewed_supplement_targets',lambda r,a:[(document,receipts)])
    exporter.main()
    assert json.loads((out/'legacy'/'supplement_documents.jsonl').read_text())['format']==supplement_review.DOCUMENT_FORMAT
    assert not (out/'legacy'/'supplement_visuals.jsonl').read_text()
    item=json.loads((out/'legacy'/'manifest.jsonl').read_text().splitlines()[0])['source_supplements'][0]
    assert item['content_inspected'] and item['review_status']=='agent_and_parent_source_and_document_pages_reviewed'
    assert json.loads((out/'statistics.json').read_text())['page_reviewed_document_supplements']==1
    store.path(root, 'schema-submissions', aid, mkdir=True).write_text(json.dumps(target))
    for role in ('agent','parent'):
        store.receipt(root,'schema',role,aid,mkdir=True).write_text(json.dumps({'article_id':aid,'status':'schema_source_reviewed','role':role,'reviewer':f'Claude synthetic {role}','inspection_notes':'Synthetic fixture inspection notes for tests',
            'annotation_sha256':validator.digest(annotation),'target_sha256':validator.digest(target),
            'source_text_sha256':source['text_sha256']}))
    exporter.main()
    assert not (out/'legacy'/'figure_panels.jsonl').read_text()
    patient=json.loads((out/'legacy'/'patients.jsonl').read_text().splitlines()[0])
    assert patient['vision']['supplement_panels']==[] and patient['vision']['supplement_reviews']==[]
    assert patient['source_supplements'][0]['content_inspected']
    card=exporter.yaml.safe_load((out/'README.md').read_text().split('---',2)[1])
    assert [c['config_name'] for c in card['configs']]==['default']
