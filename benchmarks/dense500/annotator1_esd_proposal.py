"""Canonical ESD full-field/phase proposal, original annotations unchanged."""
import json
from pathlib import Path
root=Path('benchmarks/dense50');aid='PMC13624771.1';a=json.loads((root/'annotations'/f'{aid}.json').read_text());s=json.loads((root/'sources'/f'{aid}.json').read_text());seg={r['segment_id']:r['text'] for r in s['segments']};p=a['patients'][0];facts={int(f['fact_id'].rsplit('f',1)[1]):f for f in p['facts']}
def ev(sid,q):assert q in seg[sid];return {'segment_id':sid,'quote':q}
def update(i,fields,sid,q):
 f=facts[i];f['fields'].update(fields);r=ev(sid,q)
 if r not in f['evidence']:f['evidence'].append(r)
update(1,dict(index_subject_description='82-year-old woman with 20-mm depressed gastric lesion',multiple_index_patients=False,subject_state='living_at_presentation',chief_complaint='20-mm depressed lesion'),'b00003','An 82-year-old woman presented with a 20-mm depressed lesion (Fig. 2).')
# Care setting/diagnosis/histology/indication of malignancy/medications not stated.
for i in [4,5]:update(i,dict(body_site='greater curvature of lower gastric body'),'b00007',seg['b00007'])
for i in [6,7,14,15,16]:update(i,dict(body_site='greater curvature of lower gastric body',indication='20-mm depressed lesion'),'b00007',seg['b00007'])
update(6,dict(finding='successful en bloc resection',complication='without adverse events'),'b00003','The lesion was successfully resected en bloc without adverse events.')
update(14,dict(body_site='oral side of lesion at greater curvature of lower gastric body'),'b00003','A mucosal flap was created on the oral side')
update(7,{},'b00002','The AttachBend (Fujifilm) is a novel external device that creates a second bending point at the distal end of a thin therapeutic endoscope (EG-840TP; Fujifilm) (Fig. 1).')
update(8,{},'b00003','For insertion, the device was covered with a transparent film that to smooth the surface, and even after multiple reinsertions, no pharyngeal or esophageal injuries were observed.')
for i in [12]:update(i,dict(body_site='gastric mucosa'),'b00003','Combining the down-angulation of the second bend with the up-angulation of the scope created an S-shaped configuration that maintained several centimeters of the shaft in continuous mucosal contact (Fig. 3).')
for e in p['events']:
 if e['event_id']=='p1-e000':e['fact_ids'].append('p1-f001')
 if e['event_id']=='p1-e004':e['fact_ids'].extend(['p1-f018','p1-f006'])
 e['evidence']=[]
 for fid in e['fact_ids']:
  for r in next(f for f in p['facts'] if f['fact_id']==fid)['evidence']:
   if r not in e['evidence']:e['evidence'].append(r)
nums=[7,8,9,10,11,12,13];rows=[]
for i in nums:
 for r in facts[i]['evidence']:
  if r not in rows:rows.append(r)
p['events'].append(dict(event_id='p1-e005',description='Device-assisted insertion/reinsertions and S-shaped positioning; no pharyngeal/esophageal injury, improved stability',occurrence='occurred',fact_ids=[facts[i]['fact_id'] for i in nums],time_text=None,evidence=rows))
p['temporal_relations'].append(dict(from_event_id='p1-e000',to_event_id='p1-e005',relation='before',evidence=[ev('b00003',seg['b00003'])]))
p['temporal_relations'].append(dict(from_event_id='p1-e005',to_event_id='p1-e001',relation='before',evidence=[ev('b00003',seg['b00003'])]))
for f in a['figures']:
 if f['figure_id']=='f3-ce-2025-262':
  f.update(patient_ids=[],scope='background',attribution_notes='Actual pixels are schematic drawings comparing endoscope configurations. PanelB illustrates the reported case setup without depicting physical patient material.')
  for panel in f['panels']:panel.update(patient_ids=[],scope='background')
a['annotation_provenance']['passes'].append('All8segments and actual3figures reread for full-schema field omissions and all18fact-to-phase links; no unreported diagnosis/histology, care setting, medicines or longitudinal follow-up inferred.')
path=root/'annotator-1-proposals'/f'{aid}.json';path.write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n');print(path)
