import copy
import pytest
from openpatients2.articles import parse_article
from openpatients2.article_tasks import patient_packet
from openpatients2.figure_attribution import validate_figure_review, bind_figure_review, patient_media, figure_messages
from test_articles import article, roster, XML


def fixture_review(a):
    return {'figure_id':'F1','assignments':[{'panel':None,'patient_ids':['p1'],'scope':'individual',
         'subject':'patient','evidence':[{'segment_id':'b00006','quote':'Case 1 radiograph.'}],
         'rationale':'The caption identifies case 1.'}], 'limitations':[]}


def test_panel_assignments_flow_into_patient_bundle_and_preserve_whole_image_warning():
    a=article();r=roster(a);d=fixture_review(a)
    d['assignments'][0]['panel']='A'
    other={**d['assignments'][0],'panel':'B','patient_ids':[],'scope':'external','subject':'external_patient'}
    d['assignments'].append(other)
    bound=bind_figure_review(d,a,r,method='fixture')
    p=patient_packet(a,r,r['patients'][0],figure_reviews=[bound])
    assert [x['panel'] for x in p['figure_assignments']]==['A']
    assert p['multimedia']['figures'][0]['selected_panels']==['A']
    assert p['multimedia']['figures'][0]['asset_scope']=='whole_figure_not_cropped'
    assert p['multimedia']['figures'][0]['image_urls']==a['figures'][0]['image_urls']
    assert p['multimedia']['figures'][0]['source_license']==a['license']
    assert not p['multimedia']['pixels_inspected']
    assert not patient_packet(a,r,r['patients'][1],figure_reviews=[bound])['figure_assignments']


def test_missing_reviews_do_not_fall_back_to_broad_roster_ownership():
    a=article();r=roster(a);p=patient_packet(a,r,r['patients'][0],figure_reviews=[])
    assert p['figure_assignments']==[] and p['multimedia']['unreviewed_figure_ids']==['F1']
    assert not p['multimedia']['attribution_complete']


@pytest.mark.parametrize('mutation',[ 'other_source','other_roster','invented_quote','unknown_patient','wrong_figure','duplicate_panel','whole_and_panel'])
def test_invalid_and_cross_source_figure_assignments_rejected(mutation):
    a=article();r=roster(a);d=fixture_review(a);bound=bind_figure_review(d,a,r,method='fixture')
    if mutation=='other_source':bound['xml_sha256']='wrong'
    if mutation=='other_roster':bound['roster_digest']='wrong'
    if mutation=='invented_quote':bound['data']['assignments'][0]['evidence'][0]['quote']='invented'
    if mutation=='unknown_patient':bound['data']['assignments'][0]['patient_ids']=['p3']
    if mutation=='wrong_figure':bound['data']['figure_id']='F2'
    if mutation in {'duplicate_panel','whole_and_panel'}:
        bound['data']['assignments'].append(copy.deepcopy(bound['data']['assignments'][0]))
        if mutation=='whole_and_panel':bound['data']['assignments'][1]['panel']='A'
    with pytest.raises(ValueError):patient_media(a,r,'p1',[bound])


def test_subject_and_shared_scope_constraints_and_pixel_provenance():
    a=article();r=roster(a);d=fixture_review(a)
    d['assignments'][0]['scope']='shared'
    with pytest.raises(ValueError):validate_figure_review(d,a,r)
    d=fixture_review(a);d['assignments'][0]['subject']='external_patient'
    with pytest.raises(ValueError):validate_figure_review(d,a,r)
    with pytest.raises(ValueError):bind_figure_review(fixture_review(a),a,r,method='fixture',pixel_provenance={'url':'wrong','sha256':'x'})


def test_external_specimen_subject_is_independent_of_local_patient_ownership():
    a=article();r=roster(a);d=fixture_review(a)
    d['assignments'][0].update(scope='external',subject='organism_from_patient',patient_ids=[])
    assert validate_figure_review(d,a,r)['assignments'][0]['patient_ids']==[]


def test_unwrapped_graphic_is_retained_without_inventing_a_caption_or_owner():
    xml=XML.replace('</body>','<p><boxed-text><graphic xmlns:xlink="http://www.w3.org/1999/xlink" '
                           'id="outside" xlink:href="fig1"/></boxed-text></p></body>')
    a=parse_article(xml,article()['metadata']);f=next(f for f in a['figures'] if f['figure_key']=='outside')
    assert f['image_urls'] and f['caption'] is None and f['caption_segment_ids']==[]
    assert f['source_segment_ids']==[]


def test_group_rights_exception_propagates_to_child_figures():
    xml=XML.replace('<fig id="F1">','<fig-group id="FG"><attrib>Reproduced with permission from another publisher.</attrib><fig id="F1">')
    xml=xml.replace('</fig>','</fig></fig-group>')
    a=parse_article(xml,article()['metadata'])
    assert a['figures'][0]['rights_statements']
    assert a['figures'][0]['reuse_status']=='asset_rights_review'


def test_unassigned_images_keep_completeness_false():
    a=article();r=roster(a);d=bind_figure_review(fixture_review(a),a,r,method='fixture')
    a['unassigned_media'].append({'kind':'image','url':'https://example.test/unknown.jpg'})
    _,media=patient_media(a,r,'p1',[d])
    assert not media['attribution_complete'] and media['unassigned_image_urls']


async def test_article_pipeline_calls_separate_figure_stage_before_patient_bundles(tmp_path):
    from test_article_experiment import config
    from openpatients2.experiment import Experiment
    cfg=config(tmp_path);cfg.update(figure_attribution=True,companion_tasks=[],clinical_tasks=[])
    exp=Experiment(cfg);a=article();r=roster(a);called=[]
    async def fake_call(model,task,messages,checker,identity,**kwargs):
        called.append(task)
        return {'status':'valid','data':checker(r if task=='roster' else fixture_review(a))}
    exp.call=fake_call
    try:
        patients=await exp.article(cfg['models'][0],a)
        assert called==['roster','figure_attribution'] and len(patients)==2
        assert patients[0]['source']['multimedia']['attribution_complete']
        assert patients[0]['source']['figure_assignments'][0]['subject']=='patient'
        assert not patients[1]['source']['figure_assignments']
    finally:await exp.close()


