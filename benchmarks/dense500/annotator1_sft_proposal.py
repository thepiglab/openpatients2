"""Full-field/source phase proposal for canonical SFT; canonical untouched."""
import json
from pathlib import Path
r=Path('benchmarks/dense50');aid='PMC12285374.1';a=json.loads((r/'annotations'/f'{aid}.json').read_text());s=json.loads((r/'sources'/f'{aid}.json').read_text());seg={z['segment_id']:z['text'] for z in s['segments']}
def ev(sid,q):assert q in seg[sid];return dict(segment_id=sid,quote=q)
for p,sid in zip(a['patients'],['b00004','b00005']):
 pid=p['patient_id'];facts={int(f['fact_id'].rsplit('f',1)[1]):f for f in p['facts']}
 def update(i,fields,esid,q):
  f=facts[i];f['fields'].update(fields);e=ev(esid,q)
  if e not in f['evidence']:f['evidence'].append(e)
 def attach(eid,nums):
  e=next(e for e in p['events'] if e['event_id']==f'{pid}-e{eid:03d}')
  for i in nums:
   f=facts[i]
   if f['fact_id'] not in e['fact_ids']:e['fact_ids'].append(f['fact_id'])
   for z in f['evidence']:
    if z not in e['evidence']:e['evidence'].append(z)
 def newfact(task,fields,esid,q,statement,collection='items'):
  i=max(facts)+1;f=dict(fact_id=f'{pid}-f{i:03d}',task=task,collection=collection,fields=dict(subject='index_patient',assertion='present',**fields),evidence=[ev(esid,q)],clinical_statement=statement,certainty='supported',scoring='include',notes='Cross-schema representation shares original clinical evidence; no independent observation or inferred cancer stage.')
  facts[i]=f;p['facts'].append(f);return i
 if pid=='p1':
  update(1,dict(index_subject_description='50-year-old male with progressively enlarging abdominal lump, intermittent non-bilious vomiting and weight loss',multiple_index_patients=True,subject_state='living_at_presentation',chief_complaint='gradually progressive abdominal lump over two months'),'b00004','A 50-year-old male presented with a gradually progressive abdominal lump over two months.')
  update(4,dict(body_site='abdomen'),'b00004','a gradually progressive abdominal lump over two months.')
  for i in [10,50,51]:update(i,dict(method='physical examination',body_site='left hypochondrium'),'b00004','During the physical examination, an 8×10 cm firm to hard palpable mass was detected in the left hypochondrium.')
  update(11,dict(body_site='pancreatic body and tail',method='ultrasonography (USG) of abdomen'),'b00004','On ultrasonography (USG) of the abdomen, a hypoechoic solid mass was found in the pancreatic body and tail.')
  for i in [12,52,53,54]:update(i,dict(body_site='epigastric, left hypochondriac and left lumbar region',method='contrast-enhanced computed tomography (CECT)'),'b00004','Contrast-enhanced computed tomography (CECT) revealed a heterogeneously enhancing mass (16.7 cm craniocaudal, 12.3 cm anteroposterior, 13.4 cm transverse) with lobulated margins and central necrotic components occupying the epigastric and left hypochondriac and left lumbar region (Fig. 1A).')
  update(13,dict(method='upper gastrointestinal endoscopy',body_site='body of stomach'),'b00004','An upper gastrointestinal endoscopy showed an extrinsic impression over the body of the stomach.')
  for i in [14,15,16]:update(i,dict(body_site='abdominal mass'),'b00004',seg['b00004'])
  update(21,dict(body_site='lesser sac with pancreas, transverse colon/mesentery and splenic vein involvement'),'b00004','A large tumor in the lesser sac which appeared to arise from the pancreas involving the transverse colon, mesentery of the transverse colon, and the splenic vein was detected intra-operatively.')
  update(22,dict(body_site='splenic artery, portal vein and superior mesenteric vein'),'b00004','The tumor was free of the splenic artery, portal, and superior mesenteric vein.')
  for i in [17,18]:update(i,dict(indication='confirm diagnosis of abdominal mass',body_site='abdominal mass'),'b00004','A core biopsy was recommended to confirm the diagnosis.')
  for i in [21,22]:update(i,dict(method='intraoperative examination'),'b00004','A large tumor in the lesser sac which appeared to arise from the pancreas involving the transverse colon, mesentery of the transverse colon, and the splenic vein was detected intra-operatively. The tumor was free of the splenic artery, portal, and superior mesenteric vein.')
  for i,site in [(23,'distal pancreas'),(24,'spleen'),(25,'transverse colon'),(26,'colon')]:update(i,dict(body_site=site,indication='large tumor involving pancreas/transverse colon mesentery'),'b00004','The patient underwent distal pancreatectomy and splenectomy with segmental colonic resection and colo-colic anastomosis (Fig. 1B).')
  for i in [27,28,29,48]:update(i,dict(method='histopathological examination (HPE)',specimen='resection specimen',body_site='transverse colon mesentery'),'b00004','On histopathological examination (HPE) of the specimen, spindle-shaped tumor cells with elongated nuclei, some areas of myxoid degeneration, and minimal mitoses were seen growing from the transverse colon mesentery.')
  for i in [36,37,38,39]:update(i,dict(method='histopathological microscopy, H&E sections',specimen='resection specimen',body_site='transverse colon mesentery'),'b00013','H&E sections reveal a higher magnification of the cellular area of the tumor')
  update(31,dict(text_value='diffuse cytoplasmic staining'),'b00013','(C) CD34 and (D) STAT6 shows diffuse cytoplasm and nucleus (with some cytoplasmic staining) in the tumor cells respectively (HRP-Polymer; ×400).')
  update(32,dict(text_value='nuclear staining with some cytoplasmic staining'),'b00013','(C) CD34 and (D) STAT6 shows diffuse cytoplasm and nucleus (with some cytoplasmic staining) in the tumor cells respectively (HRP-Polymer; ×400).')
  update(40,dict(assay='immunohistochemical staining, HRP-Polymer',specimen='resection specimen'),'b00013','(E) Immunohistochemical staining with Ki-67 labels occasional tumor cell nuclei indicating low proliferative activity within the tumor (HRP-Polymer; ×400).')
  update(30,dict(histology='spindle-shaped tumor cells with elongated nuclei, myxoid degeneration and minimal mitoses'),'b00004','On histopathological examination (HPE) of the specimen, spindle-shaped tumor cells with elongated nuclei, some areas of myxoid degeneration, and minimal mitoses were seen growing from the transverse colon mesentery.')
  cn=newfact('conditions',dict(name='Solitary fibrous tumor of transverse colon mesentery',verification_status='confirmed',role='primary_diagnosis',body_site='transverse colon mesentery',diagnostic_basis='core biopsy and resection histopathology with CD34/STAT-6 positivity'),'b00004','On histopathological examination (HPE) of the specimen, spindle-shaped tumor cells with elongated nuclei, some areas of myxoid degeneration, and minimal mitoses were seen growing from the transverse colon mesentery. Immunohistochemistry confirmed that the tumor cells were positive for CD34 and STAT-6 (Fig. 2).','Confirmed mesocolon SFT represented in Conditions')
  tx=newfact('oncology',dict(tumor_ref='t1',name='Distal pancreatectomy and splenectomy with segmental colonic resection and colo-colic anastomosis',modality='surgery',action='given'),'b00004','The patient underwent distal pancreatectomy and splenectomy with segmental colonic resection and colo-colic anastomosis (Fig. 1B).','Actual tumor surgery represented in Oncology','treatments')
  attach(6,[1,2,3]);attach(1,[19,20]);attach(2,[21,22,tx]);attach(7,[44,45]);attach(8,[cn]);remote=46;plan=47
 else:
  update(1,dict(index_subject_description='55-year-old male with abdominal fullness, constipation, anorexia and weight loss',multiple_index_patients=True,subject_state='living_at_presentation',chief_complaint='abdominal fullness associated with constipation and anorexia for three months'),'b00005','A 55-year-old male presented with complaints of abdominal fullness associated with constipation and anorexia for three months, in addition to a 9 kg weight loss.')
  update(4,dict(body_site='abdomen'),'b00005','complaints of abdominal fullness associated with constipation and anorexia for three months')
  update(8,dict(method='clinical examination',body_site='hypogastric and umbilical regions of abdomen'),'b00005','A firm palpable non-tender mass occupying the hypogastric and umbilical regions of the abdomen with side-to-side mobility was observed on clinical examination.')
  update(9,dict(method='rectal examination',body_site='rectum'),'b00005','On rectal examination, there was fullness at the 6 óclock position, and normal mucosa was felt.')
  update(10,dict(method='ultrasonography (USG) of abdomen',body_site='hypogastric region'),'b00005','A USG of the abdomen revealed a large mixed echogenic mass in the hypogastric region.')
  for i in [11,35,36,37]:update(i,dict(method='contrast-enhanced computed tomography (CECT) of abdomen and pelvis',body_site='abdomen and pelvis'),'b00005','CECT of the abdomen and pelvis revealed a large heterogeneous, enhancing (size: 18 cm craniocaudal, 15 cm anteroposterior, 12 cm transverse), well-circumscribed mass with lost fat planes and left lateral sigmoid colon (Fig. 3A).')
  update(12,dict(method='CECT of abdomen and pelvis',body_site='bowel loops and urinary bladder'),'b00005','The bowel loops were displaced peripherally and the urinary bladder anteriorly.')
  update(13,dict(method='colonoscopy',body_site='rectosigmoid mucosa'),'b00005','Colonoscopy revealed a normal rectosigmoid mucosa.')
  for i in [14,15]:update(i,dict(body_site='abdominopelvic mass'),'b00005',seg['b00005'])
  update(16,dict(method='intraoperative examination',body_site='rectosigmoid mesentery'),'b00006','Intra-operatively, a large tumor was seen in the rectosigmoid mesentery.')
  for i in [17,18]:update(i,dict(body_site='rectosigmoid',indication='large tumor in rectosigmoid mesentery'),'b00006','Intra-operatively, a large tumor was seen in the rectosigmoid mesentery. The patient underwent a rectosigmoid resection and Hartman’s procedure (Fig. 3B).')
  for i in [20,21,34]:update(i,dict(method='histopathological examination (HPE)',specimen='resection specimen',body_site='rectosigmoid mesentery'),'b00006','HPE revealed an encapsulated tumor comprising hypercellular and hypocellular areas with hemangiopericytomatous patterns (Fig. 4A, B).')
  update(26,dict(method='Hematoxylin and esosin staining',specimen='resection specimen',body_site='rectosigmoid mesentery'),'b00015','Hematoxylin and esosin staining. (A) Cells arranged in fascicles separated by collagen (magnification ×100).')
  for i in [22,23,24]:update(i,dict(specimen='resection specimen'),'b00006','HPE revealed an encapsulated tumor comprising hypercellular and hypocellular areas with hemangiopericytomatous patterns (Fig. 4A, B). On IHC, the tumor was positive for CD34 and STAT6 with 2% Ki67 (Fig. 4C–E).')
  update(25,dict(histology='encapsulated tumor with hypercellular/hypocellular areas and hemangiopericytomatous patterns'),'b00006','HPE revealed an encapsulated tumor comprising hypercellular and hypocellular areas with hemangiopericytomatous patterns (Fig. 4A, B).')
  update(23,dict(text_value='nuclear positivity'),'b00015','(C) Nuclear positivity for STAT6 (HRP-Polymer; ×400).')
  cn=newfact('conditions',dict(name='Solitary fibrous tumor of rectosigmoid mesentery',verification_status='confirmed',role='primary_diagnosis',body_site='rectosigmoid mesentery',diagnostic_basis='resection histopathology and CD34/STAT6 immunohistochemical positivity'),'b00006',seg['b00006'],'Confirmed rectosigmoid mesentery SFT represented in Conditions')
  tx=newfact('oncology',dict(tumor_ref='t1',name='Rectosigmoid resection and Hartman’s procedure',modality='surgery',action='given'),'b00006','The patient underwent a rectosigmoid resection and Hartman’s procedure (Fig. 3B).','Actual tumor surgery represented in Oncology','treatments')
  attach(3,[1,2,3]);attach(1,[16,tx]);attach(4,[30,31]);attach(6,[cn]);remote=32;plan=33
 update(43 if pid=='p1' else 29,dict(setting='adjuvant'),'b00004' if pid=='p1' else 'b00006','The patient did not receive any adjuvant therapy.' if pid=='p1' else 'The patient did not receive adjuvant therapy.')
 update(plan,dict(target='SFT recurrence surveillance'),'b00010',seg['b00010'])
 # Named IHC results also have general Observations representations sharing evidence.
 for source_i in ([19,20,31,32,33,34,35,40,49] if pid=='p1' else [22,23,24]):
  original=facts[source_i];fields=original['fields'];i=max(facts)+1
  result=dict(subject='index_patient',assertion='present',name=fields['name'],kind='pathology',status='resulted',method=fields.get('assay','IHC'))
  for k in ['specimen','numeric_value','unit','text_value']:
   if k in fields:result[k]=fields[k]
  if fields.get('interpretation') in ['positive','negative']:result.update(flag=fields['interpretation'],text_value=fields.get('text_value') or fields['interpretation'])
  if 'numeric_value' in fields and 'text_value' not in result:result['text_value']=str(fields['numeric_value'])+(fields.get('unit') or '')
  result['body_site']='transverse colon mesentery' if pid=='p1' and source_i not in [19,20] else 'abdominal mass' if pid=='p1' else 'rectosigmoid mesentery'
  f=dict(fact_id=f'{pid}-f{i:03d}',task='observations',collection='items',fields=result,evidence=original['evidence'].copy(),clinical_statement='Alternate general observation of '+original['clinical_statement'],certainty=original['certainty'],scoring=original['scoring'],notes='Named pathology/IHC result in general Observations and specialized Oncology shares source evidence, not a separate clinical test. '+original.get('notes',''))
  facts[i]=f;p['facts'].append(f)
  if f['scoring']=='include':attach(1 if pid=='p1' and source_i in [19,20] else 8 if pid=='p1' else 6,[i])
 # Source-specific remote surveillance, not an invented dated encounter.
 e=dict(event_id=f'{pid}-e{len(p["events"])+1:03d}',description='Remote follow-up along with imaging reports by phone and instant messaging; exact assessment dates unstated',occurrence='occurred',fact_ids=[facts[remote]['fact_id']],time_text=None,evidence=facts[remote]['evidence'])
 p['events'].append(e)
 surgery_id=f'{pid}-e002' if pid=='p1' else f'{pid}-e001';plan_id=f'{pid}-e010' if pid=='p1' else f'{pid}-e008'
 surgery_quote='The patient underwent distal pancreatectomy and splenectomy with segmental colonic resection and colo-colic anastomosis (Fig. 1B).' if pid=='p1' else 'The patient underwent a rectosigmoid resection and Hartman’s procedure (Fig. 3B).'
 for to in [e['event_id'],plan_id]:p['temporal_relations'].append(dict(from_event_id=surgery_id,to_event_id=to,relation='before',evidence=[ev('b00010',seg['b00010']),ev('b00004' if pid=='p1' else 'b00006',surgery_quote)]))
 if pid=='p2':p['temporal_relations'].append(dict(from_event_id='p2-e001',to_event_id='p2-e005',relation='overlaps',evidence=[ev('b00006','The patient underwent a rectosigmoid resection and Hartman’s procedure (Fig. 3B). The patient required multiple packed red blood cell transfusions during surgery.')]))
 p['domain_coverage']['conditions']=dict(status='documented',note='Source-explicit tumor diagnosis and prior/provisional conditions retained.')
for f in a['figures']:
 for p in f['panels']:
  if f['figure_id'] in ['F2','F4'] or (f['figure_id'] in ['F1','F3'] and p['panel']=='B'):p['subject']='patient_specimen'
a['annotation_provenance']['passes'].append('Original34segments plus actual4assets reviewed for full-field omissions: case context, sites, methods/specimens, same-source SFT Conditions and actual surgery Oncology representations, preoperative not-performed markers, remote follow-up and all accepted fact-to-phase links. Current table-only data retained; literature cases excluded; Ki67 conflict quarantined.')
path=r/'annotator-1-proposals'/f'{aid}.json';path.write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n');print(path)
