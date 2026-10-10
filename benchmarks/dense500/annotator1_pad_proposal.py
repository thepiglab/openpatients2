"""Source-reviewed canonical PAD enrichment proposal; never overwrite annotations."""
import json
from pathlib import Path
root=Path('benchmarks/dense50');aid='PMC13624890.1'
a=json.loads((root/'annotations'/f'{aid}.json').read_text());s=json.loads((root/'sources'/f'{aid}.json').read_text());seg={r['segment_id']:r['text'] for r in s['segments']};p=a['patients'][0];facts={int(f['fact_id'].rsplit('f',1)[1]):f for f in p['facts']}
def ev(sid,q):
 assert q in seg[sid];return {'segment_id':sid,'quote':q}
def update(i,fields,sid,q):
 f=facts[i];f['fields'].update(fields);e=ev(sid,q)
 if e not in f['evidence']:f['evidence'].append(e)
referral='A 68-year-old man, followed up for a previous diagnosis of genital lichen sclerosus et atrophicus (LSA), was referred to our institution for a recent-onset genital erosion refractory to topical treatment.'
exam='The dermatological examination revealed an erosive and slightly hyperkeratotic plaque measuring 15×8 mm, located just next to the urethral meatus in the context of a whitish and sclerotic appearance of the penis (Figure 1).'
biopsy='In the suspicion of a squamous cell carcinoma arising on LSA, two punch biopsies were performed.'
lsa='Microscopically, the one from the whitish sclerotic area surrounding the erosion confirmed the classical histopathological features of LSA, such as intense papillary edema, producing a dermolytic subepidermal bulla, and homogeneous sclerotic collagen with patchy lymphocytic infiltrates in the upper reticular dermis (Figure 2A).'
erosion='The specimen from the erosion showed epidermal hyperplasia with suprabasal clefting and “dilapidated brick wall” acantholysis, which was indistinguishable from HHD (Figure 2B).'
dif='Part of this specimen was submitted for direct immunofluorescence (DIF), which revealed intraepidermal intercellular C3 and IgG deposition with a “net-like” pattern (Figure 2C).'
auto='Further examination of specific anti-skin autoantibodies, particularly anti-Dsg1 and anti-Dsg3 antibodies, was negative, as was indirect immunofluorescence.'
update(1,dict(index_subject_description='68-year-old man with prior genital LSA and recent refractory genital erosion',multiple_index_patients=False,subject_state='living_at_presentation',care_setting='referred to our institution; dermatological examination',chief_complaint='recent-onset genital erosion refractory to topical treatment'),'b00004',referral)
update(4,dict(body_site='genital area'),'b00004',referral)
update(5,dict(body_site='genital area'),'b00004',referral)
update(6,dict(indication='genital erosion'),'b00004',referral)
update(7,dict(method='dermatological examination'),'b00004',exam)
update(8,dict(body_site='penis'),'b00004',exam)
update(9,dict(body_site='intertriginous areas'),'b00004','There were no other lesions in the intertriginous areas.')
update(12,dict(method='serology'),'b00004','serology for sexually transmitted infectious diseases, human papillomavirus test, and direct microscopic examination for fungi were negative.')
update(14,dict(method='direct microscopic examination'),'b00004','serology for sexually transmitted infectious diseases, human papillomavirus test, and direct microscopic examination for fungi were negative.')
update(15,dict(role='differential',body_site='genital erosion in LSA'),'b00004',biopsy)
update(16,dict(indication='suspected squamous cell carcinoma arising on LSA',body_site='genital erosion and whitish sclerotic surrounding area'),'b00004',biopsy)
for i in [17,18]:update(i,dict(method='microscopic histopathological examination',body_site='whitish sclerotic genital area surrounding erosion'),'b00004',lsa)
update(19,dict(body_site='whitish sclerotic genital area surrounding erosion',diagnostic_basis='microscopic examination of punch biopsy showing classical LSA features'),'b00004',lsa)
for i in [20,21,22]:update(i,dict(method='microscopic histopathological examination',body_site='genital erosion'),'b00004',erosion)
update(23,dict(method='direct immunofluorescence (DIF)',body_site='genital erosion',specimen='part of erosion punch biopsy specimen'),'b00004',erosion)
update(23,{},'b00004',dif)
update(26,dict(method='indirect immunofluorescence'),'b00004',auto)
update(27,dict(assertion='possible',role='primary_diagnosis',body_site='genital area in context of LSA',diagnostic_basis='proposed clinicopathological diagnosis after two punch biopsies, DIF, anti-skin autoantibodies and indirect immunofluorescence'),'b00004','A diagnosis of PAD arising in the context of LSA was proposed for this case.')
for q in [biopsy,erosion,dif,auto]:update(27,{},'b00004',q)
update(28,dict(body_site='genital area'),'b00005','the characteristic presentation of multiple papules is lacking.')
for i in [30,31,32]:
 update(i,dict(method='histopathological examination',specimen='genital erosion biopsy',body_site='genital epidermis'),'b00004',erosion)
update(33,dict(role='differential',diagnostic_basis='dyskeratosis, prominent suprabasal intraepidermal acantholysis with brick-wall appearance and absence of prominent inflammation'),'b00007',seg['b00007'])
update(34,dict(ingredient_as_documented='diclofenac sodium',indication='PAD arising in LSA'),'b00009',seg['b00009'])
update(34,{},'b00004','A diagnosis of PAD arising in the context of LSA was proposed for this case.')
for i in [37,38]:update(i,dict(method='dermatological examination'),'b00004',exam)
# Stable existing IDs retained. Extra phases carry no fabricated date or elapsed interval.
p['events']=[]
def event(eid,desc,nums,sid=None,q=None):
 rows=[]
 for i in nums:
  for r in facts[i]['evidence']:
   if r not in rows:rows.append(r)
 if sid:
  r=ev(sid,q)
  if r not in rows:rows.append(r)
 p['events'].append(dict(event_id=eid,description=desc,occurrence='occurred',fact_ids=[facts[i]['fact_id'] for i in nums],time_text=None,evidence=rows))
event('p1-e000','Prior genital LSA and unspecified topical treatment failure before referral',[4,6])
event('p1-e005','Referral and dermatological examination; plaque, negative other-area findings and family history',[1,2,3,5,7,8,9,10,11,28,37,38])
event('p1-e006','Negative infectious investigations, exact sampling times unspecified',[12,13,14])
event('p1-e001','Initial SCC suspicion prompted two punch biopsies',[15,16])
event('p1-e007','Punch-biopsy microscopic findings and positive direct immunofluorescence',[17,18,19,20,21,22,23,30,31,32])
event('p1-e002','Further negative autoantibody/IIF examination; pemphigus considered then excluded and PAD proposed',[24,25,26,27,29,33])
event('p1-e003','Topical diclofenac sodium 3% gel treatment',[34])
event('p1-e008','Treatment was well tolerated; assessment interval unreported',[35])
event('p1-e004','Complete remission following treatment; follow-up interval unreported',[36])
p['temporal_relations']=[]
# Only source-supported clinical sequence; infectious sampling not ordered against biopsies.
def edge(before,after,sid,q):p['temporal_relations'].append(dict(from_event_id=before,to_event_id=after,relation='before',evidence=[ev(sid,q)]))
edge('p1-e000','p1-e005','b00004',referral)
edge('p1-e005','p1-e001','b00004',exam+' '+ 'There were no other lesions in the intertriginous areas. Familiar history of similar eruptions was excluded, and serology for sexually transmitted infectious diseases, human papillomavirus test, and direct microscopic examination for fungi were negative. '+biopsy)
edge('p1-e001','p1-e007','b00004',biopsy+' '+lsa)
edge('p1-e007','p1-e002','b00004',dif+' '+auto+' A diagnosis of PAD arising in the context of LSA was proposed for this case.')
for end in ['p1-e008','p1-e004']:edge('p1-e003',end,'b00009',seg['b00009'])
a['annotation_provenance']['passes'].append('Source-to-full-schema field omission proposal: all 13 original segments reread; setting/chief complaint, sites, documented methods/specimens, provisional PAD polarity, medication indication and all 38 fact-to-phase links audited. No calendar dates, unreported regimen attributes, biopsy stain or SCC cancer treatment inferred.')
path=root/'annotator-1-proposals'/f'{aid}.json';path.parent.mkdir(exist_ok=True);path.write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n');print(path,len(p['facts']),len(p['events']))
