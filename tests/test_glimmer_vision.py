import base64
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from openpatients2 import glimmer_vision as vision, glimmer_benchmark as g, hipergator as hpg
from openpatients2.hpg_eval import fixtures
from openpatients2.serving import render

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'configs/hipergator/glimmer-fp8-tuning.yaml'


def test_vision_layout_really_enables_multimodal_loader(tmp_path):
    hpg.prepare(CONFIG, ROOT, tmp_path / 'campaign'); campaign = hpg.read_campaign(tmp_path / 'campaign')
    work = Path(campaign['work'])
    hpg.write_json(work / 'setup.json', {'sif':'/tmp/synthetic.sif','container_python':'/usr/bin/python3'})
    layout = {'name':'vision-dflash','replicas':8,'tensor_parallel':1,'vision':True,'speculation':'dflash','max_num_seqs':8}
    command = render(g.serving_config(campaign, campaign['config']['models'][0], layout), str(work))[0]['argv']
    assert '--language-model-only' not in command
    assert command[command.index('--limit-mm-per-prompt')+1] == '{"image":1,"video":0}'
    assert command[command.index('--max-num-seqs')+1] == '8'


@pytest.mark.asyncio
async def test_cpu_acquisition_caps_pixels_and_retains_missing_assets(tmp_path, monkeypatch):
    article = {'article_id':'PMC1.1','license':{},'figures':[
        {'figure_key':'F1','image_urls':['https://pmc-oa-opendata.s3.amazonaws.com/PMC1.1/f.jpg']},
        {'figure_key':'F2','image_urls':[]}]}
    monkeypatch.setattr(vision, 'fixtures', lambda p: (None,{'PMC1.1':article},None,None,None))
    monkeypatch.setattr(vision, 'recheck_license', lambda p: {'allowed':True})
    async def fetch(url, **kw):
        assert kw['max_bytes'] == 20
        data = b'pixels'
        return 'data:image/jpeg;base64,'+base64.b64encode(data).decode(), {
            'url':url,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'mime_type':'image/jpeg'}
    monkeypatch.setattr(vision, 'fetch_pixels', fetch)
    result = await vision.prepare_assets({'work':str(tmp_path),'root':str(ROOT),'config':{
        'fixtures':'unused','vision_evaluation':{'max_image_bytes':20,'max_total_bytes':30}}})
    assert result['status_counts'] == {'ready':1,'missing_image':1}
    row = result['figures'][0]
    assert vision.asset_pixels(tmp_path,row).endswith(base64.b64encode(b'pixels').decode())
    (tmp_path/row['file']).write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed after'): vision.asset_pixels(tmp_path,row)


@pytest.mark.asyncio
async def test_real_pixel_request_and_attribution_description_reach_patient_bundle(tmp_path, monkeypatch):
    campaign = {'root':str(ROOT),'work':str(tmp_path),'config':hpg.load_campaign(CONFIG,ROOT)}
    campaign['config']['arms'] = {'medium_seed42':campaign['config']['arms']['medium_seed42']}
    manifest, articles, packets, requests, reference = fixtures(ROOT/campaign['config']['fixtures'])
    article = articles['PMC12285374.1']; figure = article['figures'][0]
    chosen_packets = {k:v for k,v in packets.items() if k.startswith(article['article_id']+':')}
    monkeypatch.setattr(vision,'fixtures',lambda path:(manifest,{article['article_id']:article},chosen_packets,requests,reference))
    roster = json.loads((ROOT/campaign['config']['fixtures']/'rosters'/ (article['article_id']+'.json')).read_text())['roster']
    caption_id = figure['caption_segment_ids'][0]
    caption = next(s['text'] for s in article['segments'] if s['segment_id']==caption_id)
    evidence = {'segment_id':caption_id,'quote':caption}
    assignment = {'figure_id':figure['figure_key'],'assignments':[{'panel':None,'patient_ids':['p1'],
        'scope':'individual','subject':'patient','evidence':[evidence],'rationale':'Synthetic transport test'}],'limitations':[]}
    annotation = {'figure_id':figure['figure_key'],'general_description':'Synthetic fixture',
        'detailed_description':'Synthetic detailed fixture','panels':[{'panel':None,
        'general_description':'Synthetic fixture','detailed_description':'Synthetic detailed fixture',
        'image_kind':'medical_imaging','has_chart':False,'charts':[],
        'imaging_modalities':[],'imaging_submodalities':[],'medical_domains':[],'body_parts':[],
        'mentioned_categories':[],'pixel_observations':['Synthetic test observation'],
        'caption_claims':[],'clinical_significance':[],'limitations':['Test fixture']}],'limitations':[]}
    image = b'\x89PNG\r\n\x1a\nfixture'
    digest = hashlib.sha256(image).hexdigest()
    hpg.write_json(tmp_path/'vision-assets/manifest.json',{'figures':[{
        'article_id':article['article_id'],'figure_id':figure['figure_key'],'source_license':article['license'],
        'status':'ready','file':'vision-assets/'+digest+'.image','pixel_provenance':{
            'url':figure['image_urls'][0],'sha256':digest,'bytes':len(image),'mime_type':'image/png'}}],
        'status_counts':{'ready':1}})
    (tmp_path/'vision-assets'/ (digest+'.image')).write_bytes(image)
    sent=[]
    def handler(request):
        body=json.loads(request.content)
        if request.url.path=='/tokenize': return httpx.Response(200,json={'count':1000})
        sent.append(body)
        assert 'response_format' not in body
        assert body['chat_template_kwargs']=={'reasoning_strength':'medium'}
        instruction=body['messages'][0]['content']
        data=assignment if instruction.startswith('Attribute ONLY') else (
            {'visual':annotation,'attribution':assignment} if 'Also return a separate attribution object' in instruction else annotation)
        event={'choices':[{'index':0,'delta':{'content':json.dumps(data)},'finish_reason':'stop'}],
               'usage':{'prompt_tokens':1000,'completion_tokens':100}}
        return httpx.Response(200,text='data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n')
    original=httpx.AsyncClient
    monkeypatch.setattr(vision.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    root=tmp_path/'results/glimmer-fp8'; output=root/'vision/dflash'
    result=await vision.evaluate_images(campaign,campaign['config']['models'][0],
        {'name':'vision-dflash','vision':True},['http://127.0.0.1:8000/v1'],output)
    assert result['status']=='completed' and result['task_status_counts']['description']=={'valid':1}
    pixel_requests=[body for body in sent if any(isinstance(m['content'],list) for m in body['messages'])]
    assert len(pixel_requests)==3  # Attribution, independent description, joint analysis; text-only is the control.
    for body in pixel_requests:
        urls=[part['image_url']['url'] for m in body['messages'] if isinstance(m['content'],list)
              for part in m['content'] if part['type']=='image_url']
        assert urls==['data:image/png;base64,'+base64.b64encode(image).decode()]
    patient=json.loads((output/'medium_seed42/patient-media/PMC12285374.1-p1.json').read_text())
    assert patient['pixels_inspected'] and patient['visual_annotations'][0]['selected_panels'][0]['panel'] is None
    other=json.loads((output/'medium_seed42/patient-media/PMC12285374.1-p2.json').read_text())
    assert not other['visual_annotations']  # No cross-patient spillover.
    for path in output.rglob('*.json'):
        assert 'data:image/' not in path.read_text()  # Input pixels are not copied into every response file.
    columns=[json.loads(line) for line in (output/'figure-panels.jsonl').read_text().splitlines()]
    assert {r['analysis_method'] for r in columns}=={'separate','joint'}
    assert all(r['patient_ids']==['p1'] for r in columns)
