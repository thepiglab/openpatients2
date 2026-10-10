import json
from pathlib import Path
R=Path('benchmarks/dense500');ID='PMC12605513.1'
def observation(name,raw,num,unit,time,subject='index_patient',ref=None,method=None,flag='unknown',missing=False):
 return dict(subject=subject,assertion='unknown' if missing else 'present',temporality='unknown',time=dict(text=time,relation='during' if time and not missing else 'unknown',anchor='supplemental hospitalization-day grid' if missing else 'hospitalization' if time else None,date_iso=None),result_absent_reason='not_performed' if missing else None,name=name,kind='laboratory',status='not_performed' if missing else 'resulted',text_value=None if missing else raw,numeric_value=num,comparator='=' if num is not None else 'unknown',unit=unit,flag=flag,reference_range_text=ref,specimen=None,method=method,body_site=None)
def trans(tid,asset,page,table,row,col,text,kind='context',cue=None):return dict(transcription_id=tid,asset_url=asset['url'],asset_sha256=asset['sha256'],page=page,panel=None,table_label=table,row_label=row,column_label=col,kind=kind,text=text,visual_cue=cue)
def evid(tid,q):return dict(transcription_id=tid,quote=q)
def fact(fid,fields,ev,pid,owner=None,notes=''):
 return dict(fact_id=fid,task='observations',collection='items',fields=fields,evidence=ev,ownership='individual' if pid else 'unresolved',patient_ids=[pid] if pid else [],ownership_evidence=[dict(basis='supplement',transcription_id=owner,segment_id=None,quote='first patient' if pid=='p1' else 'second patient')] if pid else [],certainty='supported',scoring='include' if pid else 'review_only',notes=notes)
p=R/'document-submissions'/f'{ID}.supplements.SM1.json';d=json.loads(p.read_text());asset=d['assets'][0];ts=[];fs=[]
matrices=[('p1',1,6,[['-', '3.6','3.9','4.1','5.4','5.2'],['20.3','11.8','12.6','14.6','13.8','11.7'],['23.6','25.4','16.9','16.7','14.9','-'],['29.8','26.8','19.7','20.3','17.4','-'],['32.3','30.3','23.7','22.8','19.3','-']],[('LAGE','28.7'),('SDBG','8.44')]),('p2',2,7,[['-','2.7','3.5','4.1','4.4','4.9','5.2'],['14.8','16.8','15.9','13.6','15.6','14.7','12.6'],['19.6','20.2','17.3','16.8','18.3','16.3','-'],['25.7','23.1','20.2','20.2','22.3','19.7','-'],['29.6','28.8','26.9','20.6','21.6','18.9','-']],[('LAGE','26.9'),('SDBG','7.43')])]
for pid,tn,days,matrix,metrics in matrices:
 table=f'Supplementary Table{tn}';who='first' if pid=='p1' else 'second';header=f'SM1-header-{pid}';unit=f'SM1-unit-{pid}';foot=f'SM1-foot-{pid}'
 ts += [trans(header,asset,1,table,None,None,f'Blood glucose measurements at various time points during hospitalization of the {who} patient(mmol/L)'),trans(unit,asset,1,table,None,None,'mmol/L'),trans(foot,asset,1,table,None,None,'The patient did not undergo hospitalization or has already been discharged, thus no monitoring of blood sugar was conducted')]
 for day in range(1,days+1):ts.append(trans(f'SM1-{pid}-day{day}',asset,1,table,None,f'Day{day}',f'Day{day}'))
 for clock,row in zip(['6:00','8:00','16:00','18:00','22:00'],matrix):
  clockid=f'SM1-{pid}-clock{clock.replace(":","")}';ts.append(trans(clockid,asset,1,table,clock,None,clock))
  for day,raw in enumerate(row,1):
   cell=f'SM1-{pid}-{clock.replace(":","")}-day{day}';ts.append(trans(cell,asset,1,table,clock,f'Day{day}',raw,'clinical_data'))
   ev=[evid(cell,raw),evid(unit,'mmol/L'),evid(f'SM1-{pid}-day{day}',f'Day{day}'),evid(clockid,clock)]
   if raw=='-':ev.append(evid(foot,'no monitoring of blood sugar was conducted'))
   note='Day and clock are printed coordinates; no fasting or exact medication-phase inference.'
   if raw=='-':note+=' Printed footnote allows not-yet-hospitalized OR already discharged; individual reason and discharge day unresolved. Dash represents unmonitored glucose, never a performed negative.'
   if pid=='p1' and clock=='6:00' and day==2:note+=' Same3.6 magnitude as main narrative04:00 minimum, but this cell clock6:00 is preserved separately; no assumed identical sample.'
   if pid=='p2' and clock=='22:00' and day==1:note+='29.6 at22:00 is retained alongside main daytime peak29.0; source window/time differences unresolved, no correction.'
   fs.append(fact(cell+'-observation',observation('Blood glucose',raw,float(raw) if raw!='-' else None,'mmol/L',f'Day{day}',missing=raw=='-'),ev,pid,header,note))
 for name,raw in metrics:
  tid=f'SM1-{pid}-{name}';literal=(f'LAGE ,largest amplitude of glycemic excursions={raw} mmol/L' if name=='LAGE' else f'SDBG,standard deviation of blood glucose={raw} mmol/L') if pid=='p1' else f'{name}={raw} mmol/L'
  ts.append(trans(tid,asset,1,table,name,'footnote',literal,'clinical_data'))
  fs.append(fact(tid+'-observation',observation('Largest amplitude of glycemic excursions (LAGE)' if name=='LAGE' else 'Standard deviation of blood glucose (SDBG)',raw,float(raw),'mmol/L',None),[evid(tid,raw),evid(tid,'mmol/L')],pid,header,'Supplement footnote occurrence repeats the main narrative magnitude; no invented new measurement episode or clock.'))
d['clinical_data']=dict(format='dense-supplemental-clinical-reference/1',coverage='complete',transcriptions=ts,facts=fs,limitations=['All57 numeric glucose cells,8 unmonitored cells and4 variability footnote values typed with exact asset/page/table/row/column provenance.','Clock and hospitalization day labels are preserved without calendar dates, fasting assumptions or an unreported insulin-change anchor.','P1Day2at6:00 value3.6 remains distinct from main04:00 minimum; p2Day1at22:00 value29.6 differs from main narrative daytime peak29.0.','The printed missing-monitoring explanation does not distinguish not yet hospitalized from already discharged for an individual cell.'])
d['assets'][0]['pages'][0]['limitations']=[x for x in d['assets'][0]['pages'][0]['limitations'] if not x.startswith('Current clinical fact gate')];d['assets'][0]['pages'][0]['limitations'].append('Typed supplemental observations retain PDF page/cell provenance; quotes never masquerade as immutable JATS segments.')
d['assets'][0]['pages'][0]['limitations']=list(dict.fromkeys(d['assets'][0]['pages'][0]['limitations']))
p.write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n');print('SM1',len(ts),'transcriptions',len(fs),'facts')
p=R/'visual-submissions'/f'{ID}.supplements.ST1.json';d=json.loads(p.read_text());asset=d['assets'][0];ts=[];fs=[]
rows=[('抗谷氨酸脱羧酶抗体','Anti-glutamic acid decarboxylase antibody','0.8','<10.0','IU/ml',False),('抗胰岛素抗体','Insulin autoantibodies','52.2','<1.1','COI',True),('酪氨酸磷酸酶抗体','Tyrosine phosphatase antibody','19.4','<10.0','IU/ml',True),('抗胰岛细胞抗体','Islet cell antibody','1.8','<1.1','COI',True)]
for i,(cn,name,raw,ref,unit,up) in enumerate(rows,1):
 row=str(i);base=f'ST1-row{i}';cue=f'Visible numbered row{i} on cropped antibody report, columns 项目/结果/参考区间/单位/实验方法; top identity/date absent.'
 for part,col,text,kind in [('name','项目',cn,'context'),('result','结果',raw,'clinical_data'),('reference','参考区间',ref,'context'),('unit','单位',unit,'context'),('method','实验方法','亚辉龙化学发光','context')]:ts.append(trans(base+'-'+part,asset,None,'Visible antibody report',row,col,text,kind,cue))
 ev=[evid(base+'-'+part,text) for part,text in [('name',cn),('result',raw),('reference',ref),('unit',unit),('method','亚辉龙化学发光')]]
 if up:ts.append(trans(base+'-arrow',asset,None,'Visible antibody report',row,'结果','↑','context',f'Printed upward arrow immediately right of row{i} result{raw}'));ev.append(evid(base+'-arrow','↑'))
 fields=observation(name,raw,float(raw),unit,None,subject='unknown',ref=ref,method='亚辉龙化学发光',flag='high' if up else 'unknown')
 fs.append(fact(base+'-observation',fields,ev,None,notes='English name translates literal Chinese test label; no patient/date/serial phase in crop, so review-only/unresolved. Three result magnitudes resemble p1 but do not prove identity. GAD0.8 conflicts with main p1all0.5 if hypothetically matched; no source correction or patient matching. Printed high arrow only for rows2–4; no inferred antibody positivity diagnosis.'))
d['clinical_data']=dict(format='dense-supplemental-clinical-reference/1',coverage='complete',transcriptions=ts,facts=fs,limitations=['All four visible result rows typed; ownership unresolved, subject unknown and review-only, with no index patient export.','No identity/date visible; no assignment by numerical similarity. GAD0.8 remains distinct from main published0.5 values.','Method limited to literal 亚辉龙化学发光; iFlash instrument/model, specimen, collection time and inferred diagnoses remain unknown.']);p.write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n');print('ST1',len(ts),'transcriptions',len(fs),'facts')
