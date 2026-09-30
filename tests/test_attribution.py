import copy
import pytest
from openpatients2.articles import parse_article
from openpatients2.article_tasks import patient_packet
from openpatients2.figure_attribution import validate_figure_review, bind_figure_review, patient_media, figure_messages
from openpatients2.case_links import (reference_matches, citation_candidates, validate_link, bind_link,
                                    review_identity, identity_components, linked_case_view)
from test_articles import article, roster, XML


def fixture_review(a):
    return {'figure_id':'F1','assignments':[{'panel':None,'patient_ids':['p1'],'scope':'individual',
         'subject':'patient','evidence':[{'segment_id':'b00006','quote':'Case 1 radiograph.'}],
         'rationale':'The caption identifies case 1.'}], 'limitations':[]}


def test_inline_callouts_survive_separately_without_bibliography_in_patient_text():
    xml=XML.replace('<xref ref-type="bibr">99</xref>','<xref ref-type="bibr" rid="R1 R2">99</xref>')
    xml=xml.replace('<ref>WRONG PATIENT from references</ref>', '<ref id="R1"><mixed-citation>Prior case '
                    '<pub-id pub-id-type="pmid">12</pub-id></mixed-citation></ref>')
    a=parse_article(xml,article()['metadata']);s=a['segments'][1]
    assert s['cross_references']==[{'ref_type':'bibr','target_ids':['R1','R2'],'label':'99'}]
    assert '[BIBR:R1 R2 99]' in s['text_with_reference_markers']
    assert 'Prior case' not in a['text'] and '99' not in a['text']
    assert a['references'][0]['identifiers']['pmid']==['12']
    plan=citation_candidates(a,max_targets=1)
    assert plan['ready'][0]['identity_status']=='not_assessed'
    assert plan['ready'][0]['contexts'][0]['segment_id']==s['segment_id']


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


def test_citation_plan_cli(tmp_path):
    import json
    from openpatients2.cli import main
    a,_,_,_,_=link_fixture();source=tmp_path/'articles.jsonl';out=tmp_path/'plan.jsonl'
    source.write_text(json.dumps(a)+'\n')
    assert main(['citation-plan','--input',str(source),'--output',str(out),'--max-targets','1'])==0
    assert json.loads(out.read_text())['ready'][0]['relation']=='cites'
    assert main(['citation-plan','--input',str(source),'--output',str(out)])==2


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


def link_fixture():
    a=article();b=copy.deepcopy(a);b.update(article_id='PMC2.1',pmcid='PMC2',pmid='12',xml_sha256='targethash')
    a['references']=[{'reference_id':'R1','identifiers':{'pmcid':['PMC2'],'pmid':['12'],'doi':[]}}]
    a['segments'][1]['text']='This is the same patient reported in our earlier article.'
    a['segments'][1]['cross_references']=[{'ref_type':'bibr','target_ids':['R1'],'label':'1'}]
    r=roster(a);rb=roster(b)
    d={'source_patient_id':'p1','target_patient_id':'p1','relation':'explicit_same_patient',
       'reference_ids':['R1'],'source_evidence':[{'segment_id':'b00002','quote':a['segments'][1]['text']}],
       'target_evidence':[{'segment_id':'b00002','quote':'A woman received 5 mg.'}],
       'identity_statement':a['segments'][1]['text'],'contradictions':[],'rationale':'Explicit continuity.'}
    return a,b,r,rb,d


def test_identity_link_is_candidate_until_second_pass_and_sources_stay_separate():
    a,b,r,rb,d=link_fixture();e=bind_link(d,a,b,r,rb,method='fixture')
    assert e['identity_status']=='candidate' and not identity_components([e])['source_supported_groups']
    e=review_identity(e,verdict='accept',reviewer='fixture source audit',rationale='Explicit matching case.',conflicts=[])
    group=identity_components([e])['source_supported_groups'][0]['record_ids']
    seeds=[{'record_id':rid,'article':article,'patient_identity':identity,'facts':[{'source':'unchanged'}]}
           for rid,article,identity in [(group[0],a,r['patients'][0]),(group[1],b,rb['patients'][0])]]
    result=linked_case_view(group,seeds,[e])
    assert not result['synthetic'] and result['sources']==seeds
    assert result['sources'][0]['article']['license']==a['license']
    seeds[1]['article']['xml_sha256']='changed'
    with pytest.raises(ValueError,match='versions'):linked_case_view(group,seeds,[e])


@pytest.mark.parametrize('mutation',['wrong_identifier','citation_only','contradiction','missing_target','unknown_patient','unlinked_quote','license'])
def test_identity_validation_rejects_wrong_sources_and_missing_support(mutation):
    a,b,r,rb,d=link_fixture()
    if mutation=='wrong_identifier':a['references'][0]['identifiers']['pmid']=['13']
    if mutation=='citation_only':d['identity_statement']=None
    if mutation=='contradiction':d['contradictions']=['Species differs']
    if mutation=='missing_target':d['target_evidence']=[]
    if mutation=='unknown_patient':d['target_patient_id']='p99'
    if mutation=='unlinked_quote':a['segments'][1]['cross_references']=[]
    if mutation=='license':b['license']['allowed']=False
    with pytest.raises(ValueError):validate_link(d,a,b,r,rb)


def test_similarity_cannot_be_promoted_and_transitive_identity_bridges_wait():
    a,b,r,rb,d=link_fixture();e=bind_link(d,a,b,r,rb,method='fixture')
    similarity=copy.deepcopy(e);similarity['data']['relation']='similar_case'
    with pytest.raises(ValueError):review_identity(similarity,verdict='accept',reviewer='fixture',rationale='similar',conflicts=[])
    e=review_identity(e,verdict='accept',reviewer='fixture',rationale='explicit',conflicts=[])
    bridge={**e,'source_record_id':'PMC2.1:p1','target_record_id':'PMC3.1:p1'}
    result=identity_components([e,bridge])
    assert not result['source_supported_groups'] and len(result['pending_components'])==1
    direct={**e,'source_record_id':'PMC1.1:p1','target_record_id':'PMC3.1:p1'}
    assert len(identity_components([e,bridge,direct])['source_supported_groups'])==1
    rejection={**direct,'identity_status':'rejected'}
    assert not identity_components([e,bridge,direct,rejection])['source_supported_groups']


def test_ehr_seed_cannot_attach_other_panels_from_vision_annotations():
    from test_ehr_seeds import patient
    from openpatients2.ehr_seeds import seed_patient
    p=patient();p['source']['multimedia']={'status':'panel_attribution_available','attribution_complete':True}
    p['source']['figure_assignments']=[{'figure_id':'F1','panel':'A','patient_ids':['p1']}]
    ann={'article_id':'PMC1.1','model':'fixture','figure_id':'F1','pixel_provenance':{'sha256':'pixels'},
         'source_license':{'code':'CC BY'},'annotation':{'status':'valid','data':{'caption_claims':[],
          'pixel_observations':[{'panel':panel,'patient_ids':['p1'],'visible_description':'image'} for panel in ['A','B',None]]}}}
    result=seed_patient(p,[ann])
    assert [o['panel'] for o in result['visual_findings'][0]['pixel_observations']]==['A']
    assert [o['panel'] for o in result['figure_annotations'][0]['annotation']['pixel_observations']]==['A']
