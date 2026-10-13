"""Live freshness and reviewed-data boundaries for the article browser."""
import json
from pathlib import Path

import pytest

from openpatients2.viewer import Library, build, digest, handler
from openpatients2.dense_store import load as load_store

store = load_store()

AID = 'PMC123.1'


def put(root, folder, value, aid=AID):
    path = store.path(root, folder, aid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def jsonl(root, filename, rows):
    root.mkdir(parents=True, exist_ok=True)
    (root / f'{filename}.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))


@pytest.fixture
def library(tmp_path):
    root, dataset = tmp_path / 'working', tmp_path / 'export'
    source = dict(article_id=AID, title='An article', text_sha256='sourcehash',
                  segments=[dict(segment_id='b01', heading='Case', text='Exact evidence.')],
                  figures=[], source_url='https://pmc.ncbi.nlm.nih.gov/articles/PMC123/',
                  license={'code':'CC BY'})
    put(root, 'sources', source)
    jsonl(dataset, 'manifest', [dict(article_id=AID, title='Old export title', primary_stratum='cardiology')])
    return Library(dataset, [root]), root, dataset, source


def review(root, source):
    annotation = dict(article_id=AID, text_sha256=source['text_sha256'], patients=[dict(
        patient_id='p1',label='Patient 1',species='human',identity_evidence=[])])
    put(root, 'annotations', annotation)
    put(root, 'reviews', dict(article_id=AID, status='source_reviewed', source_read_in_full=True,
                             source_text_sha256=source['text_sha256'], annotation_sha256=digest(annotation)))
    target = dict(article_id=AID, source_text_sha256=source['text_sha256'],
                  annotation_sha256=digest(annotation), source_gate_errors=[], roster={'patients':[]},
                  patients=[{'source':{'patient_target':{'patient_id':'p1'}},'sections':{'observations':{'items':[]}}}])
    put(root, 'schema-submissions', target)
    for role in ('agent','parent'):
        put(root, f'schema-{role}-reviews', dict(article_id=AID, role=role, status='schema_source_reviewed', reviewer=f'Claude synthetic {role}', inspection_notes='Synthetic fixture inspection notes for tests',
            source_text_sha256=source['text_sha256'],annotation_sha256=digest(annotation),target_sha256=digest(target)))
    return annotation, target


def test_catalog_all_sources_and_no_drafts(library):
    lib, root, _, _ = library
    put(root, 'drafts', {'article_id':AID, 'private_draft':'do not publish'})
    source2 = dict(article_id='PMC124.1', title='A newly acquired article', text_sha256='hash2', segments=[])
    assert len(lib.catalog()['articles']) == 1
    put(root, 'sources', source2, 'PMC124.1')
    assert len(lib.catalog()['articles']) == 2
    payload=lib.article(AID)
    assert payload['metadata']['title']=='An article'
    assert payload['patients']==[] and payload['annotation'] is None
    assert 'private_draft' not in json.dumps(payload)


def test_new_reviews_appear_without_restart_or_export(library):
    lib, root, dataset, source = library
    before=lib.catalog()['articles'][0]
    review(root, source)
    after=lib.catalog()['articles'][0]
    assert before['status']=='pending' and after['status']=='schema_reviewed'
    assert before['revision']!=after['revision']
    assert len(lib.article(AID)['patients'])==1
    assert not (dataset/'patients.jsonl').exists()


def test_changed_target_is_withheld_then_reappears_when_receipts_match(library):
    lib,root,_,source=library
    _,target=review(root,source)
    assert lib.article(AID)['patients']
    target['patients'][0]['sections']['observations']['items']=[{'name':'Corrected fact'}]
    put(root,'schema-submissions',target)
    assert lib.article(AID)['patients']==[]
    for role in ('agent','parent'):
        receipt=json.loads(store.receipt(root,'schema',role,AID).read_text())
        receipt['target_sha256']=digest(target)
        put(root,f'schema-{role}-reviews',receipt)
    assert lib.article(AID)['patients'][0]['sections']['observations']['items'][0]['name']=='Corrected fact'


def test_stale_annotation_does_not_resurrect_export(library):
    lib, root, dataset, source=library
    annotation,target=review(root,source)
    jsonl(dataset,'annotations',[annotation]);jsonl(dataset,'patients',[dict(article_id=AID,**target['patients'][0])])
    assert lib.article(AID)['patients']
    annotation['patients'][0]['label']='Changed identity'
    put(root,'annotations',annotation)
    assert not lib.article(AID)['patients']
    assert lib.article(AID)['annotation'] is None


def test_export_without_local_sources_still_works(library):
    lib,root,dataset,source=library
    annotation,target=review(root,source)
    jsonl(dataset,'annotations',[annotation]);jsonl(dataset,'patients',[dict(article_id=AID,**target['patients'][0])])
    portable=Library(dataset,[])
    assert portable.article(AID)['metadata']['status']=='schema_reviewed'
    assert portable.article(AID)['source'] is None
    assert portable.article(AID)['notices']


def test_export_reload_detects_new_patient_rows(library):
    _,root,dataset,source=library
    annotation,target=review(root,source)
    jsonl(dataset,'annotations',[annotation])
    portable=Library(dataset,[])
    assert portable.article(AID)['metadata']['status']=='source_reviewed'
    jsonl(dataset,'patients',[dict(article_id=AID,**target['patients'][0])])
    assert portable.article(AID)['metadata']['status']=='schema_reviewed'


def test_visual_identity_and_target_receipts_are_required(library):
    lib,root,_,source=library
    annotation,_=review(root,source)
    target=dict(article_id=AID,source_text_sha256=source['text_sha256'],figures=[])
    put(root,'visual-submissions',target)
    identity=digest([{k:p[k] for k in ('patient_id','label','species','identity_evidence')}for p in annotation['patients']])
    receipt=dict(article_id=AID,status='source_and_pixels_reviewed',source_text_sha256=source['text_sha256'],
                 target_sha256=digest(target),patient_identity_sha256=identity,patient_ids=['p1'])
    put(root,'visual-parent-reviews',dict(role='parent',**receipt))
    assert lib.article(AID)['visual'] is None
    put(root,'visual-agent-reviews',dict(role='agent',**receipt))
    assert lib.article(AID)['visual']==target
    target['figures']=[{'figure_id':'new'}];put(root,'visual-submissions',target)
    assert lib.article(AID)['visual'] is None


@pytest.mark.parametrize('aid',['../secret','x/../../secret','<script>', '/etc/passwd'])
def test_article_path_restricted(library,aid):
    with pytest.raises(KeyError): library[0].article(aid)


def test_handler_does_not_serve_workspace_files(library):
    cls=handler(library[0]);request=object.__new__(cls);replies=[]
    request.send_json=lambda data,status=200:replies.append((status,data))
    request.path='/../../pyproject.toml';request.do_GET()
    assert replies[0][0]==404


@pytest.mark.parametrize('path,kind', [('/', 'text/html'), ('/viewer.html', 'text/html'), ('/home.js', 'text/javascript'), ('/common.js', 'text/javascript'), ('/cohort.html', 'text/html'), ('/cohort.js', 'text/javascript'),
                                       ('/manifest.webmanifest', 'application/manifest+json'),
                                       ('/icon-180.png', 'image/png')])
def test_handler_serves_the_installable_app_shell(library, path, kind):
    cls=handler(library[0]);request=object.__new__(cls);sent=[]
    request.send_bytes=lambda data,content_type,status=200:sent.append((status,content_type,data))
    request.path=path;request.do_GET()
    assert sent[0][0]==200 and sent[0][1].startswith(kind) and sent[0][2]


def test_static_build_is_portable_and_does_not_overwrite(library,tmp_path):
    lib,root,_,source=library;review(root,source)
    out=tmp_path/'site';result=build(lib,out)
    assert result['articles']==1
    data=json.loads((out/'api/articles'/f'{AID}.json').read_text())
    assert data['patients'] and data['source']['segments'][0]['text']=='Exact evidence.'
    assert all((out/name).exists() for name in ('app.js','index.html','viewer.html','home.js','common.js','cohort.html','cohort.js','manifest.webmanifest','icon-180.png'))
    with pytest.raises(ValueError,match='never overwritten'):build(lib,out)


def test_source_change_invalidates_review(library):
    lib,root,_,source=library;review(root,source)
    source['text_sha256']='replacement-source';put(root,'sources',source)
    assert lib.article(AID)['metadata']['status']=='pending'
    assert lib.article(AID)['patients']==[]


def test_supplement_export_is_withheld_for_changed_annotation(library):
    lib,root,dataset,source=library
    annotation,_=review(root,source)
    exported={**annotation,'review':{'annotation_sha256':digest(annotation)}}
    jsonl(dataset,'annotations',[exported])
    jsonl(dataset,'supplement_visuals',[dict(article_id=AID,supplement_id='video1')])
    assert lib.article(AID)['supplement_visuals']
    annotation['patients'][0]['label']='Corrected label'
    put(root,'annotations',annotation)
    put(root,'reviews',dict(article_id=AID,status='source_reviewed',source_read_in_full=True,
        source_text_sha256=source['text_sha256'],annotation_sha256=digest(annotation)))
    assert lib.article(AID)['annotation']
    assert lib.article(AID)['supplement_visuals']==[]


def test_gate_errors_and_wrong_target_identity_withheld(library):
    lib,root,_,source=library
    _,target=review(root,source)
    for key,value in [('source_gate_errors',['invalid evidence']),('article_id','PMC999.1')]:
        changed={**target,key:value};put(root,'schema-submissions',changed)
        for role in ('agent','parent'):
            receipt=json.loads(store.receipt(root,'schema',role,AID).read_text())
            receipt['target_sha256']=digest(changed);put(root,f'schema-{role}-reviews',receipt)
        assert lib.article(AID)['patients']==[]


def test_viewer_reads_legacy_folder_of_unified_release(tmp_path):
    dataset = tmp_path / 'release'; (dataset / 'legacy').mkdir(parents=True); (dataset / 'data').mkdir()
    (dataset / 'legacy' / 'manifest.jsonl').write_text(json.dumps(dict(article_id=AID, title='Legacy title')) + '\n')
    library = Library(dataset, [])
    assert library.dataset == (dataset / 'legacy').resolve()
    assert any(row['article_id'] == AID for row in library.catalog()['articles'])


def test_catalog_counts_facts_by_section_for_the_overview(library):
    lib, root, _, source = library
    annotation, target = review(root, source)
    target['patients'][0]['sections'] = {'observations': {'items': [{'name': 'a'}, {'name': 'b'}], 'limitations': ['x']},
                                         'medications': {'items': []}}
    target['patients'][0]['companions'] = {'timeline_v2': {'events': [{'event_id': 'e1'}]}}
    put(root, 'schema-submissions', target)
    for role in ('agent', 'parent'):
        put(root, f'schema-{role}-reviews', dict(article_id=AID, role=role, status='schema_source_reviewed',
            source_text_sha256=source['text_sha256'], annotation_sha256=digest(annotation), target_sha256=digest(target)))
    row = lib.catalog()['articles'][0]
    assert row['section_facts'] == {'observations': 2, 'medications': 0}
    assert row['section_patients'] == {'observations': 1, 'medications': 0} and row['event_count'] == 1


def test_aggregate_only_article_is_schema_reviewed_without_patients(library):
    lib, root, _, source = library
    annotation, target = review(root, source)
    target.update(patients=[], roster={'disposition': 'aggregate_only', 'reported_individual_count': 5, 'patients': []})
    put(root, 'schema-submissions', target)
    for role in ('agent', 'parent'):
        put(root, f'schema-{role}-reviews', dict(article_id=AID, role=role, status='schema_source_reviewed',
            source_text_sha256=source['text_sha256'], annotation_sha256=digest(annotation), target_sha256=digest(target)))
    row = lib.catalog()['articles'][0]
    assert row['status'] == 'schema_reviewed' and row['patient_count'] == 0 and row['disposition'] == 'aggregate_only'
    assert any('aggregate series' in n for n in lib.article(AID)['notices'])


def test_figure_overlays_need_current_dual_signed_review(monkeypatch, tmp_path):
    from openpatients2.dense import figure_ehr_review, figure_privacy_review
    monkeypatch.setattr(figure_ehr_review, 'reviewed_figure_ehr', lambda root, aid: ({'figures': ['ehr']}, []))
    monkeypatch.setattr(figure_privacy_review, 'reviewed_privacy_flags', lambda root, aid: None)
    assert Library._overlays(tmp_path, AID) == [{'figures': ['ehr']}, None]
    def stale(root, aid):
        raise ValueError('receipt does not bind the current target')
    monkeypatch.setattr(figure_ehr_review, 'reviewed_figure_ehr', stale)
    assert Library._overlays(tmp_path, AID) == [None, None]


def test_workstation_paths_never_reach_the_browser():
    from openpatients2.viewer import scrub
    row = {'assets': [{'url': 'https://example.org/a.jpg', 'path': '/Users/someone/cache/a.jpg', 'cache_path': '/tmp/x'}],
           'quote': '/10 scale', 'path': 'relative/is/fine'}
    assert scrub(row) == {'assets': [{'url': 'https://example.org/a.jpg'}], 'quote': '/10 scale', 'path': 'relative/is/fine'}


def test_unsigned_supplement_sidecars_are_not_published(library):
    lib, root, _, source = library
    source = {**source, 'supplements': [{'supplement_id': 'SM1', 'assets': [{'url': 'https://example.org/s.pdf'}]}]}
    put(root, 'sources', source)
    review(root, source)
    sidecar = store.path(root, 'document-submissions', AID, sid='SM1')
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps({'supplement_id': 'SM1', 'contents_summary': 'unsigned draft'}))
    payload = lib.article(AID)
    assert payload['supplement_documents'] == [] and 'unsigned draft' not in json.dumps(payload)
    assert payload['source']['supplements'][0]['supplement_id'] == 'SM1'


@pytest.mark.parametrize('item,years', [
    ({'numeric_value': 76.0, 'unit': 'years'}, 76.0), ({'numeric_value': 3, 'unit': 'months'}, 0.25),
    ({'text_value': 'Six-year-old'}, 6.0), ({'text_value': '13 years 6 months'}, 13.5),
    ({'text_value': '21st day of life'}, 21 / 365.25), ({'text_value': 'DOL 30'}, 30 / 365.25),
    ({'text_value': 'neonate'}, 0.0), ({'text_value': 'in his 70s'}, 75.0), ({'text_value': 'adult'}, None)])
def test_age_phrasings_become_years(item, years):
    from openpatients2.viewer import age_years
    assert age_years(item) == pytest.approx(years) if years is not None else age_years(item) is None


def test_patient_summary_reads_only_the_index_patient():
    from openpatients2.viewer import patient_summary
    patient = {'source': {'patient_target': {'patient_id': 'p2'}}, 'sections': {
        'case_context': {'index_subject_description': 'Girl with lupus', 'species': 'human', 'subject_state': 'living_at_presentation'},
        'demographics': {'items': [
            {'subject': 'family_member', 'attribute': 'documented_sex', 'text_value': 'father'},
            {'subject': 'family_member', 'attribute': 'age_other', 'numeric_value': 40, 'unit': 'years'},
            {'subject': 'index_patient', 'attribute': 'documented_sex', 'text_value': 'Girl'},
            {'subject': 'index_patient', 'attribute': 'age_at_presentation', 'text_value': 'eleven-year-old'}]},
        'conditions': {'items': [{'subject': 'index_patient', 'assertion': 'present', 'name': 'Lupus nephritis'},
                                 {'subject': 'index_patient', 'assertion': 'absent', 'name': 'Sepsis'}]}}}
    row = patient_summary(patient)
    assert (row['patient_id'], row['sex'], row['age_years'], row['age_group']) == ('p2', 'female', 11.0, 'child')
    assert row['conditions'] == ['Lupus nephritis'] and row['subject_state'] == 'living_at_presentation'


def test_export_streams_reviewed_patient_rows(library):
    import io
    lib, root, _, source = library
    review(root, source)
    cls = handler(lib); request = object.__new__(cls); request.wfile = io.BytesIO(); sent = []
    request.send_response = lambda status: sent.append(status)
    request.send_header = lambda k, v: sent.append((k, v))
    request.end_headers = lambda: None
    request.path = f'/api/export.jsonl?patients={AID}:p1,{AID}:p9'; request.do_GET()
    rows = [json.loads(line) for line in request.wfile.getvalue().decode().splitlines()]
    assert sent[0] == 200 and [(r['article_id'], r['patient_id']) for r in rows] == [(AID, 'p1')]
    assert rows[0]['record']['source']['patient_target']['patient_id'] == 'p1'
    bad = object.__new__(cls); replies = []
    bad.send_json = lambda data, status=200: replies.append(status)
    bad.path = '/api/export.jsonl?patients=../etc:p1'; bad.do_GET()
    assert replies == [400]


def test_specialty_comes_from_checked_in_selection_without_an_export(library):
    lib, root, dataset, source = library
    (dataset / 'manifest.jsonl').unlink()
    (root / 'selection.json').write_text(json.dumps([{'article_id': AID, 'stratum': 'cardiac'}]))
    put(root, 'sources', {**source, 'article_id': 'PMC124.1'}, 'PMC124.1')
    (root / 'retained-strata.json').write_text(json.dumps({'PMC124.1': 'renal'}))
    strata = {r['article_id']: r['primary_stratum'] for r in lib.catalog()['articles']}
    assert strata == {AID: 'cardiac', 'PMC124.1': 'renal'}


def test_patient_with_two_ages_of_the_same_kind_is_summarized():
    from openpatients2.viewer import patient_summary
    items = [{'attribute': 'age_other', 'text_value': 'neonate'}, {'attribute': 'age_other', 'numeric_value': 2, 'unit': 'days'}]
    assert patient_summary({'sections': {'demographics': {'items': items}}})['age_group'] == 'neonate'
