from annotator1_build import Build,BASE
import json,re
b=Build('PMC12859224.1');sg={k:v['text'] for k,v in b.seg.items()};own={'b00001':['p1','p2'],'b00010':['p1','p2'],'b00018':['p1','p2'],'b00019':['p1','p2'],'b00020':['p1','p2'],'b00021':['p1','p2']};cap=sg['b00021'];clauses={}
for i,c in enumerate('ABCDEFGH'):
 start=cap.index('('+c+')');end=cap.index('('+chr(ord(c)+1)+')') if c!='H' else cap.index('Abbreviations:');clauses[c]=cap[start:end].strip()
for i,age,ps,bs,ts,tbl in [(1,67,'b00003','b00004','b00005','b00014'),(2,55,'b00006','b00007','b00008','b00015')]:
 b.patient(f'Case {i}: woman with SLE and biopsy-proven FGN',ps,f'A {age}-year-old woman');p=b.p;g={}
 def put(group,fn,*args,**kw):
  z=fn(*args,**kw);g.setdefault(group,[]).append(z);return z
 own.update({s:[p['patient_id']] for s in [ps,bs,ts,tbl]})
 put('presentation',b.fact,'case_context',ps,sg[ps],f'Case {i} individual human clinical case',case_kind='clinical_case',species='human',multiple_index_patients=True,subject_state='living_at_presentation',index_subject_description=f'{age}-year-old woman with SLE and FGN',chief_complaint='progressive kidney dysfunction' if i==1 else 'increasing proteinuria and mild transient lupus rash')
 put('presentation',b.fact,'demographics',ps,f'A {age}-year-old woman','Age at presentation',attribute='age_at_presentation',numeric_value=age,unit='years');put('presentation',b.fact,'demographics',ps,f'A {age}-year-old woman','Documented woman',attribute='documented_sex',text_value='woman')
 if i==1:
  put('history',b.cond,ps,'with SLE history','Systemic lupus erythematosus',role='history',temporality='historical')
  put('history',b.cond,ps,'class III LN 30 years earlier','Class III lupus nephritis',role='history',temporality='historical',time='30 years earlier',body_site='kidney')
  for name in ['prednisone','azathioprine']:put('historytx',b.med,ps,'treated with prednisone and azathioprine',name,action='historical',ingredient_as_documented=name,temporality='historical',indication='class III lupus nephritis')
  put('historyresponse',b.out,ps,'achieving sustained remission','other',text_value='sustained remission of prior class III lupus nephritis',related_condition='class III lupus nephritis',temporality='historical')
  put('presentation',b.cond,ps,'progressive kidney dysfunction','Progressive kidney dysfunction',body_site='kidney',diagnostic_basis='progressive creatinine increase')
  for n,v in [('Earlier creatinine',1.2),('Presentation creatinine',2.5)]:put('baseline' if v==1.2 else 'presentation',b.obs,ps,'increasing creatinine from 1.2 mg/dL to 2.5 mg/dL',n,numeric_value=v,unit='mg/dL',notes='Earlier-to-current pair as explicitly described; no earlier interval given.')
  put('presentation',b.obs,ps,'urinary protein-creatinine ratio (uPCR) ∼0.7 g/g','Urinary protein-creatinine ratio',numeric_value=0.7,unit='g/g',specimen='urine',text_value='approximately 0.7 g/g')
  for n in ['Antinuclear antibodies','C3','C4','antineutrophil cytoplasmic antibodies','double-stranded DNA (dsDNA)']:
   put('presentation',b.obs,ps,'Antinuclear antibodies, C3, C4, antineutrophil cytoplasmic antibodies, and double-stranded DNA (dsDNA) (16 IU/mL) were negative.',n,text_value='negative as jointly described',flag='negative',**(dict(numeric_value=16,unit='IU/mL') if 'dsDNA' in n else {}))
   put('presentation',b.proc,ps,sg[ps],n+' testing',finding='negative as described',notes='Alternate test-action representation of same reported result.')
  put('presentation',b.sym,ps,'She did not have extrarenal manifestations of SLE.','Extrarenal SLE manifestations',assertion='absent',kind='sign')
 else:
  put('history',b.cond,ps,'with SLE and class V LN','Systemic lupus erythematosus',role='history',temporality='historical')
  put('history',b.cond,ps,'with SLE and class V LN','Class V lupus nephritis',role='history',temporality='historical',body_site='kidney')
  put('history',b.obs,ps,'quiescent systemic disease for many years','Systemic SLE activity history',kind='other',text_value='quiescent for many years',temporality='historical',time='for many years')
  put('presentation',b.sym,ps,'mild and transient lupus rash','Lupus rash',kind='sign',severity='mild',text_value='transient')
  put('presentation',b.obs,ps,'stable creatinine ∼ 0.5 mg/dL','Presentation creatinine',numeric_value=0.5,unit='mg/dL',text_value='approximately 0.5 mg/dL, stable')
  put('presentation',b.obs,ps,'increasing proteinuria, with a uPCR of 2.5 g/g','Urinary protein-creatinine ratio',numeric_value=2.5,unit='g/g',specimen='urine',text_value='increasing proteinuria')
  put('presentation',b.obs,ps,'erythrocyte sedimentation rate of 75 mm/h','Erythrocyte sedimentation rate',numeric_value=75,unit='mm/h')
  for n in ['C3','C4']:put('presentation',b.obs,ps,'normal C3 and C4 levels',n,flag='normal',text_value='normal')
  put('presentation',b.obs,ps,'negative dsDNA (<12 IU/mL)','Anti-dsDNA',flag='negative',numeric_value=12,comparator='<',unit='IU/mL',text_value='negative')
  for n in ['erythrocyte sedimentation rate','C3','C4','dsDNA']:put('presentation',b.proc,ps,sg[ps],n+' testing',finding='75 mm/h' if n=='erythrocyte sedimentation rate' else 'normal' if n in ['C3','C4'] else 'negative (<12 IU/mL)',notes='Alternate action representation of same result.')
  put('presentation',b.sym,tbl,'Our case 2 | 55/F | SLE with prior LN; edema, proteinuria','Edema',kind='sign',notes='Documented only in index table row; location/severity not reported.')
 # Kidney biopsy: same performed procedure, distinct pathology techniques and findings.
 put('biopsy',b.proc,bs,'Kidney biopsy showed','Kidney biopsy',body_site='kidney',finding=sg[bs],notes='Single biopsy examination; microscopy/staining representations refer to this tissue.')
 put('biopsy',b.cond,bs,sg[bs],'Fibrillary glomerulonephritis',role='primary_diagnosis',body_site='glomeruli',diagnostic_basis='kidney biopsy with DNAJB9-positive fibrillary deposits',notes='Biopsy-confirmed FGN in context of established SLE; causal SLE mechanism unproven.')
 p['facts'][-1]['evidence'].append({'segment_id':'b00010','quote':'we present 2 patients with biopsy-proven FGN'})
 if i==2:p['facts'][-1]['fields']['severity']='early as documented'
 if i==1:
  put('biopsy',b.obs,bs,'mesangioproliferative glomerulonephritis','Mesangioproliferative pattern',kind='pathology',specimen='kidney biopsy',body_site='glomeruli',text_value='mesangioproliferative glomerulonephritis')
  negatives=['endocapillary proliferation','crescents'];morph=[('minimal, segmental foot-process effacement','Foot-process effacement','podocyte foot processes'),('Mesangial and subepithelial electron-dense deposits','Electron-dense deposits','mesangium and subepithelial space'),('nonbranching, randomly arranged fibrils','Fibril morphology','glomeruli')]
  for name,v in [('IgG',4),('C3',2),('kappa',4),('lambda',3),('C1q',2)]:put('biopsy',b.obs,bs,sg[bs],'Biopsy immunofluorescence '+name,kind='pathology',method='immunofluorescence',specimen='kidney biopsy',body_site='mesangium and focally capillary walls',text_value=f'smudgy {name} staining {v}+',numeric_value=v,unit='+')
  put('biopsy',b.obs,bs,'fibrils averaging 9.8 nm in diameter','Fibril diameter',kind='pathology',method='electron microscopy',specimen='kidney biopsy',body_site='mesangial and subepithelial deposits',numeric_value=9.8,unit='nm',text_value='averaging 9.8 nm')
  put('biopsy',b.obs,bs,'Tubuloreticular inclusions were absent','Tubuloreticular inclusions',kind='pathology',method='electron microscopy',specimen='kidney biopsy',text_value='absent',flag='negative')
  put('biopsy',b.cond,'b00010','without lupus nephritis (first case)','Concurrent lupus nephritis',assertion='absent',verification_status='refuted',body_site='kidney',diagnostic_basis='current biopsy and case-series statement',notes='Current biopsy absence does not erase historical class III LN.')
 else:
  put('biopsy',b.cond,bs,'Kidney biopsy showed early FGN and class V LN','Class V lupus nephritis',role='primary_diagnosis',body_site='kidney',diagnostic_basis='current biopsy')
  negatives=['endocapillary hypercellularity','crescents'];morph=[('thickened basement membranes with spike-like projections','Basement membrane morphology','glomerular basement membranes'),('mild mesangial expansion containing extracellular fibrillary deposits','Mesangial morphology','mesangium'),('subepithelial and intramembranous electron-dense deposits','Electron-dense deposits','subepithelial and intramembranous spaces'),('moderate foot-process effacement','Foot-process effacement','podocyte foot processes'),('Randomly arranged, nonbranching fibrils','Fibril morphology','mesangial and subendothelial spaces')]
  put('biopsy',b.obs,bs,'National Institutes of Health lupus nephritis activity index of 0/24','NIH lupus nephritis activity index',kind='pathology',numeric_value=0,unit='/24',text_value='0/24',specimen='kidney biopsy')
  for name,v in [('IgG',4),('C1q',1)]:put('biopsy',b.obs,bs,sg[bs],'Biopsy immunofluorescence '+name,kind='pathology',method='immunofluorescence',specimen='kidney biopsy',text_value=f'finely granular {name} {v}+',numeric_value=v,unit='+')
  put('biopsy',b.obs,bs,'minimal C3 staining','Biopsy immunofluorescence C3',kind='pathology',method='immunofluorescence',specimen='kidney biopsy',text_value='minimal staining')
  put('biopsy',b.obs,bs,'fibrils measuring 12-15 nm','Fibril diameter range',kind='pathology',method='electron microscopy',specimen='kidney biopsy',body_site='mesangial and subendothelial spaces',text_value='12-15 nm',unit='nm',notes='Explicit range retained without fabricated single numeric average.')
 for n in negatives:put('biopsy',b.obs,bs,sg[bs],n,kind='pathology',specimen='kidney biopsy',body_site='glomeruli',flag='negative',text_value='absent')
 for q,n,site in morph:put('biopsy',b.obs,bs,q,n,kind='pathology',specimen='kidney biopsy',body_site=site,text_value=q,method='light microscopy' if i==2 and n in ['Basement membrane morphology','Mesangial morphology'] else 'electron microscopy')
 put('biopsy',b.obs,bs,'Congo red staining was negative.','Congo red staining',kind='pathology',method='Congo red stain',specimen='kidney biopsy',flag='negative',text_value='negative')
 put('biopsy',b.obs,bs,'DNAJB9 staining was positive.' if i==1 else 'DNAJB9 immunostaining was strongly positive.','DNAJB9 staining',kind='pathology',method='DNAJB9 immunostaining',specimen='kidney biopsy',flag='positive',text_value='positive' if i==1 else 'strongly positive')
 put('biopsy',b.obs,bs,'Deposits were limited to glomeruli','Deposit distribution',kind='pathology',specimen='kidney biopsy',body_site='glomeruli',text_value='limited to glomeruli')
 for site in ['tubular','vascular']:put('biopsy',b.obs,bs,'no immune-complex deposition in tubular or vascular compartments','Immune-complex deposition '+site,kind='pathology',specimen='kidney biopsy',body_site=site+' compartments',flag='negative',text_value='absent')
 for method in ['light microscopy','electron microscopy','immunofluorescence','Congo red staining','DNAJB9 immunostaining']:put('biopsy',b.proc,bs,sg[bs],method,body_site='kidney biopsy',finding='As described in biopsy observations',notes='Technique within same kidney biopsy, not another biopsy occurrence.')
 # Figure-specific caption findings assigned exactly by explicit A-D/E-H ownership.
 for c in ('ABCD' if i==1 else 'EFGH'):
  q=clauses[c];method={'A':'PAS stain','B':'H&E stain','C':'Jones silver stain','D':'DNAJB9 immunohistochemistry','E':'IgG immunofluorescence','F':'kappa/lambda immunofluorescence','G':'electron microscopy','H':'electron microscopy'}[c]
  put('biopsy',b.obs,'b00021',q,'Figure 1'+c+' caption findings',kind='pathology',method=method,specimen='kidney biopsy',body_site='glomeruli' if c!='A' else 'glomeruli/interstitium/tubules',text_value=q,notes='Literal own-panel findings; caption assigns A-D Case1, E-H Case2, even where body and caption staining detail differ.')
  put('biopsy',b.proc,'b00021',q,method,body_site='kidney biopsy',finding=q,notes='Alternate performed-technique representation of same biopsy panel.')
 if i==1:
  for c,n,q,site,pol,sev in [('A','Mesangial matrix/cellularity expansion','mesangial matrix, cellularity expansion','mesangium','present',None),('A','Interstitial fibrosis','mild interstitial fibrosis','interstitium','present','mild'),('A','PAS-positive hyaline casts','PAS-positive hyaline casts','tubules','present',None),('A','Tubular atrophy','tubular atrophy','tubules','present',None),('C','Basement membrane small holes','small holes','glomerular basement membranes','present',None),('C','Basement membrane duplication','without duplication, spikes, or double contours','glomerular basement membranes','absent',None),('C','Basement membrane spikes','without duplication, spikes, or double contours','glomerular basement membranes','absent',None),('C','Basement membrane double contours','without duplication, spikes, or double contours','glomerular basement membranes','absent',None),('D','DNAJB9 staining strength/location','strong staining within glomeruli','glomeruli','present',None)]:put('biopsy',b.obs,'b00021',q,n,kind='pathology',method={'A':'PAS stain','C':'Jones silver stain','D':'DNAJB9 immunohistochemistry'}[c],specimen='kidney biopsy',body_site=site,assertion=pol,text_value=q,flag='negative' if pol=='absent' else 'positive' if c=='D' else 'unknown',notes='Individual attribute from own-panel caption.')
  for c,v in [('A',100),('B',200),('C',400),('D',100)]:put('biopsy',b.obs,'b00021',clauses[c],f'Figure 1{c} documented magnification',kind='pathology',numeric_value=v,unit='×',specimen='kidney biopsy')
 else:
  for name,pos in [('kappa','left'),('lambda','right')]:put('biopsy',b.obs,'b00021',clauses['F'],'Caption immunofluorescence '+name,kind='pathology',method='immunofluorescence',specimen='kidney biopsy',text_value='polytypic light-chain staining; '+pos+' component in composite panel',notes='Caption specifically assigns F to Case2; do not transfer Case1 numerical light-chain intensity here.')
  put('biopsy',b.obs,'b00021',clauses['H'],'Caption fibril diameter range',kind='pathology',method='electron microscopy',specimen='kidney biopsy',text_value='approximately 10-15 nm',unit='nm',notes='No fabricated numeric midpoint or pixel measurement.')
 # Treatment and response, never lumped into one event.
 put('treatment',b.med,ts,'rituximab (1 g intravenously ×2 doses)','rituximab',action='administered',ingredient_as_documented='rituximab',dose_value=1,dose_unit='g',dose_text='1 g ×2 doses',route='intravenous',indication='FGN with SLE',notes='Two induction doses; dosing interval not specified.')
 if i==1:
  put('treatment',b.med,ts,'supportive care with renin-angiotensin-aldosterone system blockade','Renin-angiotensin-aldosterone system blockade',action='administered',indication='supportive kidney care',notes='Class-level therapy; no individual agent or route/dose inferred.')
  put('response',b.obs,ts,'uPCR 0.2-0.3 g/g','Post-treatment uPCR',specimen='urine',unit='g/g',text_value='0.2-0.3 g/g',notes='Range retained without single invented value.')
  put('response',b.obs,ts,'creatinine improved from 2.5 mg/dL to 1.6 mg/dL','Post-treatment creatinine',numeric_value=1.6,unit='mg/dL',text_value='improved from 2.5 to 1.6 mg/dL')
  put('response',b.out,ts,'Proteinuria declined','improvement',related_condition='proteinuria',text_value='proteinuria declined')
  put('response',b.out,ts,'creatinine improved from 2.5 mg/dL to 1.6 mg/dL','improvement',related_condition='kidney dysfunction',text_value='creatinine improved')
  put('maintenance',b.med,ts,'Two additional maintenance rituximab doses over 2 years','rituximab',action='administered',ingredient_as_documented='rituximab',dose_text='two additional maintenance doses; dose amount unreported',time='over 2 years',notes='No transfer of 1g induction amount or interval to maintenance doses.')
  put('follow',b.obs,ts,'maintained uPCR <0.3 g/g','Maintenance/follow-up uPCR',specimen='urine',numeric_value=0.3,comparator='<',unit='g/g')
  put('follow',b.out,ts,'without complication over 31 months of follow-up','complication',assertion='absent',follow_up_duration_text='31 months',time='over 31 months of follow-up')
  put('tableoutcome',b.obs,tbl,'SCr 2.0 mg/dL','Table outcome serum creatinine',specimen='serum',numeric_value=2.0,unit='mg/dL',notes='Conflicts with narrative 1.6; exact timing unreported, not forced into a serial rise.')
  put('presentation',b.obs,tbl,sg[tbl].splitlines()[-1],'Table C3',text_value='↓',flag='low',notes='Table C3/C4 jointly ↓/↓ contrasts with narrative negative wording.')
  put('presentation',b.obs,tbl,sg[tbl].splitlines()[-1],'Table C4',text_value='↓',flag='low',notes='No correction of narrative negative wording.')
 else:
  for name in ['mycophenolate','hydroxychloroquine','renin-angiotensin-aldosterone system blockade','blood pressure medications']:
   put('priorongoing',b.med,ts,sg[ts],name,action='current',time='for several months',ingredient_as_documented=name if name in ['mycophenolate','hydroxychloroquine'] else None,notes='Ongoing for months and continued with rituximab; dose/route not given.')
  put('priorongoing',b.med,ts,'she had declined steroids','steroids',action='declined',notes='Unspecified steroid declined; no delivered steroid inferred.')
  put('response',b.obs,ts,'uPCR of 0.2 g/g','Post-treatment uPCR',numeric_value=0.2,unit='g/g',specimen='urine',text_value='gradually improved proteinuria')
  put('response',b.obs,ts,'creatinine remained stable within baseline range of 0.46-0.55 mg/dL','Post-treatment creatinine',unit='mg/dL',text_value='stable within baseline range 0.46-0.55 mg/dL')
  put('response',b.out,ts,'Proteinuria gradually improved','improvement',text_value='proteinuria gradually improved')
  put('follow',b.out,ts,'remained in remission without the need for maintenance rituximab 34 months after her FGN diagnosis','other',time='34 months after her FGN diagnosis',related_condition='FGN/proteinuria',text_value='remained in remission without need for maintenance rituximab',follow_up_duration_text='34 months after FGN diagnosis')
  put('follow',b.med,ts,'without the need for maintenance rituximab','maintenance rituximab',assertion='absent',action='unknown',ingredient_as_documented='rituximab',notes='No maintenance needed as documented; not an invented completed/stopped course.')
  put('presentation',b.obs,tbl,sg[tbl].splitlines()[-1],'Table serum creatinine',specimen='serum',numeric_value=0.47,unit='mg/dL',notes='Table value retained separately from narrative approximately0.5; no new test occasion inferred.')
  put('biopsy',b.obs,tbl,sg[tbl].splitlines()[-1],'Table mean fibril diameter range',kind='pathology',unit='nm',text_value='10-15 nm',notes='Table column Mean Fibril Diameter has a range; narrative12-15nm and captionapproximately10-15nm preserved.')
 for phase in ['baseline','presentation','response','follow','tableoutcome']:
  tests=[f for f in p['facts'] if f['fact_id'] in g.get(phase,[]) and f['task']=='observations' and ('creatinine' in f['fields']['name'].lower() or 'upcr' in f['fields']['name'].lower() or 'protein-creatinine ratio' in f['fields']['name'].lower())]
  if tests:
   ev=tests[0]['evidence'][0];have_cr=any('creatinine' in f['fields']['name'].lower() for f in tests);have_up=any('upcr' in f['fields']['name'].lower() or 'protein-creatinine ratio' in f['fields']['name'].lower() for f in tests)
   testname='Creatinine and urine protein-creatinine ratio testing' if have_cr and have_up else 'Creatinine testing' if have_cr else 'Urinary protein-creatinine ratio testing'
   values=[]
   for f in tests:
    v=f['fields'];qty=(str(v.get('comparator') or '')+str(v['numeric_value'])+' '+str(v.get('unit') or '')) if v.get('numeric_value') is not None else '';values.append(v['name']+': '+str(v.get('text_value') or '')+(' ['+qty+']' if qty else ''))
   fid=put(phase,b.proc,ev['segment_id'],ev['quote'],testname,finding='; '.join(values),notes='Alternate performed-test representation of the same phase reported results; no additional test occasion or assay inferred.')
   for t in tests:
    for e in t['evidence']:
     if e not in p['facts'][-1]['evidence']:p['facts'][-1]['evidence'].append(e)
 E={}
 for f in p['facts']:
  if i==1 and f['task']=='conditions' and f['fields'].get('name')=='Class III lupus nephritis':
   f['fields']['clinical_status']='remission';f['evidence'].append({'segment_id':ps,'quote':sg[ps]})
  if f['task']=='medications' and f['fields'].get('indication'):
   context=ps if f['fields'].get('action')=='historical' else 'b00001';f['evidence'].append({'segment_id':context,'quote':sg[context]})
  if f['fields'].get('time',{}).get('text') in ['over 2 years','for several months','over 31 months of follow-up','for many years']:f['fields']['time']['relation']='during'
 for k in ['history','historytx','historyresponse','priorongoing','baseline','presentation','biopsy','treatment','response','maintenance','follow','tableoutcome']:
  if k in g:E[k]=b.event({'history':'SLE/lupus nephritis history','historytx':'Historical prednisone/azathioprine treatment','historyresponse':'Sustained prior nephritis remission','priorongoing':'Months of ongoing baseline medicines and steroid refusal','baseline':'Earlier creatinine before progression','presentation':'Current renal presentation and laboratory workup','biopsy':'Current kidney biopsy confirmed FGN; detailed staining/microscopy','treatment':'Rituximab induction and concurrent supportive treatment','response':'Subsequent proteinuria/creatinine response','maintenance':'Additional maintenance rituximab doses','follow':'Longitudinal follow-up/remission and documented course','tableoutcome':'Separately reported table outcome creatinine; timing unresolved'}[k],g[k],time='over 2 years' if k=='maintenance' else 'over 31 months of follow-up' if k=='follow' and i==1 else '34 months after her FGN diagnosis' if k=='follow' else 'for several months' if k=='priorongoing' else '30 years earlier' if k=='history' and i==1 else None)
 def before(x,y,sid):b.before(E[x],E[y],sid,sg[sid])
 if i==1:
  for x,y in [('history','historytx'),('historytx','historyresponse'),('historyresponse','presentation'),('baseline','presentation')]:before(x,y,ps)
  before('response','maintenance',ts);before('maintenance','follow',ts)
 else:before('history','presentation',ps);before('priorongoing','treatment',ts);before('response','follow',ts)
 for x,y,sid in [('presentation','biopsy',ps),('biopsy','treatment',ts),('treatment','response',ts)]:before(x,y,sid)
 if i==1:p['ambiguities']=[dict(issue='Narrative C3/C4 negative differs from Table ↓/↓; post-treatment creatinine1.6 differs from table2.0 with no timing given.',evidence=[{'segment_id':ps,'quote':sg[ps]},{'segment_id':ts,'quote':sg[ts]},{'segment_id':tbl,'quote':sg[tbl]}],must_not_infer='No relabeling negative complement as normal or forced later creatinine worsening. Historical classIII LN separate from absent concurrent LN.')]
 else:p['ambiguities']=[dict(issue='Body fibril12-15nm, table10-15nm and captionapproximately10-15nm differ. Caption E-H belongs to Case2, including kappa/lambda polytypic staining not specified in Case2 body.',evidence=[{'segment_id':bs,'quote':sg[bs]},{'segment_id':tbl,'quote':sg[tbl]},{'segment_id':'b00021','quote':cap}],must_not_infer='No reassignment of E/F to Case1, no fabricated average or replacement range.')]
 b.summary((f'Case {i} is a {age}-year-old woman with established SLE. '+('Class III lupus nephritis treated with prednisone/azathioprine 30 years earlier had sustained remission. Progressive kidney dysfunction raised creatinine from1.2 to2.5mg/dL with uPCR approximately0.7g/g and no extrarenal SLE manifestations. Current biopsy confirmed DNAJB9-positive FGN without concurrent LN.' if i==1 else 'She had class V lupus nephritis and systemic disease quiescent for years. Mild transient rash and increasing proteinuria accompanied stable creatinine approximately0.5mg/dL and uPCR2.5g/g. Biopsy confirmed early FGN with concurrent class V LN and NIH activity0/24. Ongoing mycophenolate, hydroxychloroquine, RAAS blockade and blood-pressure medicines had been used for months; steroids were declined.')),sum([g[k] for k in ['history','historytx','historyresponse','priorongoing','baseline','presentation','biopsy'] if k in g],[]))
 b.summary(('Rituximab1g IV twice plus RAAS blockade preceded proteinuria decline to0.2-0.3g/g and creatinine1.6mg/dL. Two additional maintenance doses over two years maintained uPCR<0.3g/g without complications over31months. Table outcome creatinine2.0mg/dL remains separate and undated; complement wording conflicts retained.' if i==1 else 'Rituximab1g IV twice added to continued medicines preceded gradual uPCR improvement to0.2g/g with creatinine stable within0.46-0.55mg/dL. Remission persisted34months after FGN diagnosis without need for maintenance rituximab. Literal biopsy range differences and source panel ownership retained.'),sum([g[k] for k in ['treatment','response','maintenance','follow','tableoutcome'] if k in g],[]));b.finish()
b.a['article_limitations']=['Two local index cases only. Three cited Table1 reports and unreported other local FGN cases are outside index roster.','Association of SLE and FGN is not established causation; possible mechanisms are author speculation, not proven patient mechanisms.','No individual RAAS inhibitor, blood-pressure drug, unstated doses/routes, maintenance dose amount, calendar dates or test assays inferred.','Case1 C3/C4 negative versus ↓/↓ and creatinine1.6 versus2.0; Case2 fibril diameter ranges differ between narrative/table/caption. Preserve distinct source statements without invented timing/correction.']
panels={'b00021':[(c,clauses[c],'histopathology' if c in 'ABCD' else 'immunofluorescence' if c in 'EF' else 'electron microscopy','kidney biopsy',['p1' if c in 'ABCD' else 'p2'],'individual') for c in 'ABCDEFGH']};b.save(own,panels)
path=BASE/'drafts'/'PMC12859224.1.json';a=json.load(open(path));a['article_limitations']=[z for z in a['article_limitations'] if not z.startswith('Figure pixels not')]+['Clinical companion represents caption-grounded findings; actual inspected biopsy figure is maintained in separate visual target/receipts.'];
for p in a['patients']:
 for sm in p['summary']:
  for x,y in [('from1.2','from 1.2'),('to2.5mg/dL','to 2.5 mg/dL'),('approximately0.7g/g','approximately 0.7 g/g'),('approximately0.5mg/dL','approximately 0.5 mg/dL'),('uPCR2.5g/g','uPCR 2.5 g/g'),('activity0/24','activity 0/24'),('Rituximab1g','Rituximab 1 g'),('to0.2-0.3g/g','to 0.2-0.3 g/g'),('creatinine1.6mg/dL','creatinine 1.6 mg/dL'),('uPCR<0.3g/g','uPCR <0.3 g/g'),('over31months','over 31 months'),('creatinine2.0mg/dL','creatinine 2.0 mg/dL'),('to0.2g/g','to 0.2 g/g'),('within0.46-0.55mg/dL','within 0.46-0.55 mg/dL'),('persisted34months','persisted 34 months')]:sm['text']=sm['text'].replace(x,y)
path.write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n')
