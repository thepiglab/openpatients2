import json
from pathlib import Path
from validate import validate
R=Path('benchmarks/dense500');ID='PMC12834363.1';p=R/'drafts'/f'{ID}.json';D=json.loads(p.read_text());S=json.loads((R/'sources'/f'{ID}.json').read_text());P=D['patients'][0];B={x['segment_id']:x['text'] for x in S['segments']}
def ev(n,q):assert q in B[f'b{n:05d}'];return dict(segment_id=f'b{n:05d}',quote=q)
F={f['fact_id']:f for f in P['facts']}
for fid in ['p1-f077','p1-f078']:
 F[fid]['fields']['body_site']='overlying soft tissue';F[fid]['evidence'].append(ev(1,'overlying soft tissue adhesions and scarring were noted.'))
F['p1-f008']['fields']['temporality']='current';F['p1-f008']['notes']='Gradual two-year enlargement ongoing at presentation; before timing denotes onset/history, not symptom cessation.'
F['p1-f068']['fields']['flag']='negative'
F['p1-f142']['fields']['specimen']='total thyroid gross specimen'
F['p1-f085']['fields']['method']='operative blood-loss estimate'
F['p1-f074']['fields']['temporality']='current'
for fid in ['p1-f002','p1-f003']+[f'p1-f{x:03d}' for x in range(16,32)]:F[fid]['fields']['temporality']='current'
# Concise statements preserve actual fields; no whole-paragraph appendices.
for f in P['facts']:
 x=f['fields'];t=f['task'];name=x.get('name') or x.get('attribute') or f['clinical_statement']
 if t=='observations':
  val=x.get('text_value')
  if val is None and x.get('numeric_value') is not None:val=str(x['numeric_value'])+(' '+x['unit'] if x.get('unit') else '')
  if val is None:val='not quantified/reported'
  if x.get('unit') and x['unit'] not in val:val+=' '+x['unit']
  statement=f'{name}: {val}.'
  if x.get('method'):statement+=' Method: '+x['method']+'.'
  if x.get('reference_range_text'):statement+=' Reference: '+x['reference_range_text']+'.'
 elif t=='symptoms_function':
  statement=name+(' was absent.' if x['assertion']=='absent' else ': '+x['text_value']+'.' if x.get('text_value') else ' was documented.')
 elif t=='procedures_devices':
  statement=name+(' was not required/performed.' if x['assertion']=='absent' else ' was historically reported.' if x['action']=='historical' else ' was planned.' if x['action']=='planned' else ' was placed.' if x['action']=='device_present' else ' was performed.')
  if x.get('finding'):statement+=' '+x['finding']+'.'
 elif t=='outcomes':statement=x['text_value']+'.'
 elif t=='conditions':statement=name+(' was excluded on final thyroid histology.' if x.get('verification_status')=='refuted' else ' was not reported as significant past history.' if x['assertion']=='absent' else ' was diagnosed.');
 elif t=='family_genetics':statement='Thyroid disease was reported in second-degree relatives.'
 elif t=='demographics':statement='Age at presentation was 53 years.' if x['attribute']=='age_at_presentation' else 'The patient was described as a woman.'
 else:continue
 f['clinical_statement']=statement
F['p1-f107']['clinical_statement']='The entire thyroidectomy specimen was submitted for microscopic evaluation in 51 tissue blocks.'
F['p1-f008']['clinical_statement']='Anterior neck swelling enlarged progressively over two years, more pronounced on the right and ongoing at presentation.'
F['p1-f004']['clinical_statement']='A thyroid/cervical procedure occurred in 2008, approximately 16 years before presentation; operative records and exact operation type were unavailable.'
F['p1-f143']['clinical_statement']='The conclusion claims full resolution of compressive symptoms, despite their explicit absence at presentation; this remains review-only.'
# Update evidence retained on modified phase event and connect undated history only to presentation.
for e in P['events']:
 for fid in e['fact_ids']:
  for x in F[fid]['evidence']:
   if x not in e['evidence']:e['evidence'].append(x)
P['temporal_relations'].append(dict(from_event_id='p1-e021',to_event_id='p1-e003',relation='before',evidence=[ev(6,'Past medical history was non-significant, and her only prior surgery was a thyroid procedure in 2008. She reported a family history of thyroid disease in second-degree relatives.')]))
assert not validate(D,S),validate(D,S)
p.write_text(json.dumps(D,indent=2,ensure_ascii=False)+'\n');print('Audited 143 statements/fields; 21 events/20 edges; qualified conflicts retained')
