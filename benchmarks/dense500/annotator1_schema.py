"""Private source-first corrections and structural review support for annotator1 only."""
import json
from pathlib import Path
BASE=Path('benchmarks/dense500')
IDS=['12880940','12811047','12312621','12611218','12754810','12766325','12832185','12786310','12811543','12831519','12852039','12817612','12477075','12821429','12718700','12940158','12746974','12812266']
def source_times():
 for n in IDS:
  path=BASE/'drafts'/f'PMC{n}.1.json';a=json.loads(path.read_text());s=json.loads((BASE/'sources'/path.name).read_text());seg={x['segment_id']:x['text'] for x in s['segments']};count=0
  for p in a['patients']:
   for f in p['facts']:
    time=f['fields'].get('time');t=time.get('text') if time else None
    if not t:continue
    if not any(t in e['quote'] for e in f['evidence']):
     sid=next((e['segment_id'] for e in f['evidence'] if t in seg[e['segment_id']]),None)
     if sid:
      f['evidence'].append({'segment_id':sid,'quote':seg[sid]});count+=1
     elif n=='12477075' and t.startswith(('Phase 1 ','Phase 2 ')):
      phase,col=t.rsplit(' ',1);f['notes']+=' Exact phase in method/body text; synthetic combined phase+column label removed from temporal text.'
      if col=='Δ':f['fields'].pop('time');f['notes']+=' Delta is a change column, not a separate timed encounter.'
      else:f['fields']['time']['text']=col
      count+=1
     else:raise ValueError((n,f['fact_id'],t))
    # Refine only explicit relational words; retain phase labels as at.
    time=f['fields'].get('time')
    if not time:continue
    lo=time['text'].lower()
    if lo.startswith('for ') and ('prior' in lo or 'after' in lo):time['relation']='during'
    elif 'prior' in lo or 'before' in lo or lo.endswith(' ago'):time['relation']='before'
    elif lo.startswith(('during','throughout','over ','for ')):time['relation']='during'
    elif lo.startswith('until'):time['relation']='until'
    elif 'after' in lo or lo.startswith('following '):time['relation']='after'
  if n=='12312621':
   for p in a['patients']:
    f=next(x for x in p['facts'] if x['fields'].get('name')=='Presentation date and hospital');e=p['events'][0]
    if f['fact_id'] not in e['fact_ids']:e['fact_ids'].append(f['fact_id'])
    for row in f['evidence']:
     if row not in e['evidence']:e['evidence'].append(row)
    e['time_text']=f['fields']['time']['text']
  path.write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n');print(n,'temporal-context corrections',count)
if __name__=='__main__':source_times()

# Manually reviewed event sequences: numbers refer to stable source event IDs.
KIND_MAP={
'12880940':['presentation test treatment adverse_event outcome history other','presentation treatment outcome test other'],
'12811047':['presentation treatment outcome follow_up follow_up history']*2,
'12312621':['presentation outcome follow_up history treatment test treatment','presentation outcome follow_up treatment follow_up','presentation outcome follow_up history treatment outcome follow_up plan plan'],
'12611218':['presentation treatment outcome outcome adverse_event adverse_event adverse_event treatment follow_up','presentation treatment adverse_event outcome adverse_event adverse_event adverse_event treatment adverse_event plan other','presentation treatment adverse_event outcome adverse_event treatment treatment treatment follow_up plan plan'],
'12754810':['history presentation treatment follow_up history treatment plan plan plan','history presentation treatment follow_up history treatment treatment plan plan'],
'12766325':['diagnosis treatment treatment follow_up history test treatment plan plan plan','diagnosis treatment treatment follow_up history plan plan','diagnosis treatment treatment follow_up history plan'],
'12832185':['history history diagnosis treatment outcome outcome history plan'],
'12786310':['presentation diagnosis treatment test outcome follow_up history treatment treatment outcome treatment test plan plan'],
'12811543':['treatment adverse_event treatment test adverse_event history presentation treatment adverse_event outcome other plan plan plan plan plan other'],
'12831519':['history presentation presentation test treatment outcome treatment diagnosis treatment test treatment outcome follow_up test','history presentation presentation test treatment diagnosis adverse_event adverse_event adverse_event diagnosis treatment adverse_event adverse_event outcome test','history presentation presentation test presentation test treatment treatment treatment adverse_event treatment test outcome follow_up follow_up follow_up test'],
'12852039':['history presentation treatment test outcome presentation presentation test diagnosis presentation test test test test diagnosis outcome plan follow_up follow_up follow_up test'],
'12817612':['history history history presentation adverse_event adverse_event test test treatment test test test test test test treatment test treatment adverse_event other adverse_event outcome'],
'12477075':['history presentation test treatment outcome treatment adverse_event adverse_event test treatment treatment test test test test test test follow_up outcome other'],
'12821429':['history test test test test treatment outcome diagnosis follow_up','history presentation test test test adverse_event treatment treatment test diagnosis outcome follow_up'],
'12718700':['history presentation diagnosis test treatment adverse_event outcome diagnosis diagnosis follow_up','presentation diagnosis test other treatment treatment follow_up diagnosis','presentation diagnosis test treatment adverse_event test other treatment outcome test diagnosis treatment follow_up'],
'12940158':['history diagnosis treatment presentation treatment adverse_event diagnosis test treatment treatment test other treatment treatment follow_up follow_up follow_up follow_up other test'],
'12746974':['history presentation test diagnosis treatment treatment outcome treatment test outcome follow_up follow_up'],
'12812266':['history history presentation test treatment adverse_event test test test test treatment test treatment test outcome follow_up follow_up'],
'12754280':['history presentation test diagnosis plan treatment treatment follow_up follow_up follow_up other follow_up follow_up','history presentation test diagnosis plan treatment treatment follow_up follow_up follow_up follow_up other follow_up follow_up']}

def time_classification(text):
 import re
 low=text.lower()
 if re.search(r'\b(?:19|20)\d{2}\b',text):
  kind='calendar'
 elif low in ['baseline','δ','initial consultation','pre-hospitalization','pre-surgical','post-surgical'] or low=='at the time of bariatric surgery':kind='order_only'
 elif re.search(r'after|before|prior|post|pod|later|following|next|day\s+\d|\d.*hour of hospitalization|follow-up',low):kind='relative'
 elif re.search(r'hours?|\bh\b|days?|weeks?|months?|years?|8w',low):kind='duration'
 else:kind='order_only'
 precision='unknown' if kind=='order_only' else 'approximate' if any(x in low for x in ['approximately','around','few ','end of']) else 'range' if any(x in low for x in ['within','over ','more than','between','>']) else 'exact'
 value=text if re.fullmatch(r'(?:19|20)\d{2}(?:-\d{2}(?:-\d{2})?)?',text) else None
 return kind,precision,value

def structural_submission(n):
 from openpatients2.longitudinal import relative_order
 source_path=BASE/'schema-drafts'/f'PMC{n}.1.json';target=json.loads(source_path.read_text());target['roster']['reported_individual_count']=len(target['roster']['patients']);target['roster']['roster_complete']=True
 for p,kinds in zip(target['patients'],KIND_MAP[n]):
  timeline=p['companions']['timeline_v2'];kinds=kinds.split();assert len(kinds)==len(timeline['events']),(n,p['source']['record_id'],len(kinds),len(timeline['events']))
  for e,kind in zip(timeline['events'],kinds):
   e['kind']=kind
   for t in e['times']:t['kind'],t['precision'],t['calendar_value']=time_classification(t['text'])
  p['relative_timeline']=relative_order(timeline)
 (BASE/'schema-submissions').mkdir(exist_ok=True);(BASE/'schema-submissions'/source_path.name).write_text(json.dumps(target,ensure_ascii=False,indent=2)+'\n')
 return target

REVIEW_NOTES={
'12880940':'Inspected both source-grounded full14-section targets, pointer/evidence preservation, summaries, chronology and all6caption assignments. EF/serial echo values and explicit negatives retained; no DKA inferred inCase2. Apical IDH versus pseudoaneurysm and modality/location conflicts remain source limitations. Refusal is an occurred decision, not performed imaging; both deaths remain outcomes. Classified clinical phases and literal relative times without synthetic dates; roster2complete for original cases only.',
'12811047':'Inspected both complete contracts and every retained lab/echo occurrence against source-linked fields. Initial and post-thiamine stenosis/EF reassessments remain separate observations; no inferred catheterization/BNP. Refractory edema history, IV/oral therapy,1month improvement,3/8month reassessment and6/14month follow-up represented. Captions6figures/panels correctly case-owned, pixels uninspected. Literal context added to timed facts; original roster2complete.',
'12312621':'Inspected3contracts with repaired TableI, all current-patient columns, dates and exact quoted medication/lab values. Dated hospital contexts converted to presentation observations/events. mEq/dl-vs-l, IGI32/36h and mispositioned BP remain quarantine; no arbitrary unit correction or pooled discharge value. Baseline/response/persistent hyperkalemia/escalation/discharge/home/latest follow-up preserved, with calendar versus relative time types reviewed. No figure targets; roster3complete original patients.',
'12611218':'Inspected3DMD/LVAD contracts, all clinical tables and case-specific captions. Arrest/ECPR/ECMO, actual LVAD, respiratory/bleeding/RV complications, warfarin holds, inotrope withdrawal, discharge/death and late56month follow-up remain distinct phases. Death-discharge table conflict quarantined; no invented TAPSE unit or merged POD/hospital clocks. Orphan source graphic uses unwrapped-graphic-4 unresolved without caption. No pixels inspected;3original cases complete.',
'12754810':'Inspected2pacemaker-DVT contracts, normal/negative investigations and actual medication transitions. Case2CALR/hydroxycarbamide and family history remain correctly attributed; fatherPE does not become index disease. CRP conflict retained. US/CT captions remain owned; procedure recommended/declined decisions not completed procedures. Enoxaparin→DOAC, hydroxycarbamide escalation and1/3month follow-up represented. Reviewed9events per patient and explicit relative times;2original cases complete.',
'12766325':'Inspected3nickel-allergy aneurysm contracts and all own rows/panels. Nickel/gold results, actual nickel-free/low-nickel procedures, distinct planned antiplatelet transitions, DWI lesion despite overall complication wording, no allergic symptoms and18/11/9month follow-up retained. No cohort average applied to patients or completed future DAPT change inferred. Reviewed10/7/6events and relative/duration expressions. Three original cases complete, no pixels.',
'12832185':'Inspected all14sections for recurrent-DVT/IVCagenesis case, quantities and source evidence. PriorDVT/thrombophilia and supply interruption remain historical; current negativePE imaging and normal findings do not imply unrelated disease negatives. Heparin/thrombectomy/balloon/stent actual procedures, rapid response/POD2 discharge and indefinite-anticoagulation recommendation remain distinct. Eight events classified without invented long-term outcome;3figures owned caption-only;1original case complete.',
'12786310':'Inspected full contract with all3tables and retained vital/lab/echo measurements, risk and unit conflicts. No shock inferred in normotensive intermediate-high-risk PE; thrombolysis actual and phased UFH/enoxaparin/rivaroxaban represented. Renal recovery/day4 and predischarge echo separate;6month prescription/referral planned,1month missed follow-up actual. Reviewed14events and clock/relative times; supplementalZIP remains unavailable. Four figures caption-only;1original case complete.',
'12811543':'Inspected full LAAC/DRT contract, comorbidities/genetics/bleeding/renal negatives and treatment states. Implant and recurrence dates plus INR targets remain unresolved/quarantined, no false exact timeline. FirstDRT/resolution, recurrentDRT/LMWH, transient bleeding/renal recovery and adherence issues are separate phases; apixaban/valve intervention remain considerations. Reviewed17events/5figures and chronology without inventing later outcome. One original case complete, pixels uninspected.',
'12831519':'Inspected3aspergillosis contracts with all lab table rows, normal/negative studies, numeric cavity extents and patient-owned6figures. Antibiotic failures/escalations and actual antifungal switches differ by patient; TB remains provisional inCase2. p1liver/specimen conflicts andp3galactomannan discrepancy quarantined. p2ventilation/ICUdeath andp3day40negative follow-up remain longitudinal endpoints. Reviewed14/15/17events and literal times, no pooled unverified organism/specimen inference;3original cases complete.',
'12852039':'Inspected complete post-COVID cavity contract with two admission panels, normals/negatives/not-performed tests and separate cavity extents. Probable abscess→empirical therapy→postCOVIDfavored/stop antimicrobials chronology retained without definitive infection inference. Five-month follow-up and caption/body date/resolution disagreements remain explicit. Same-case congress-poster disclosure preserved as source-family limitation. Reviewed21events,4CTpanels,1original patient complete, pixels uninspected.',
'12817612':'Inspected full fatalHIV/Serratia contract with all three40-row susceptibility panels and culture timing. No currentCD4/AmpC/UTI mechanism inferred; urineTobraR versus blood/aspI remains specimen-specific. Presentation/intubation/shock→antimicrobials→CRRT/DNR→PEA/death distinct from postmortem48h blood result. Cavity extents all numeric, source mortality clock retained without synthetic chronology. Reviewed22events/2caption-onlyfigures;1original case complete.',
'12477075':'Inspected all14COPDrehabilitation sections and tablePFT/ABG/lab/phase outcomes including normals. Phase1decline/AECOPDreadmission andPhase2combined rehabilitation improve selected outcomes while SGRQ components stay mixed, no uniformly favorable score inference. CSQ endpoints not assumed baseline pairs; delta columns are changes, not timed encounters. Therapy/monitoring/counseling remain appropriately actual/planned,20events reviewed. Generic system figure background;1original patient complete;pixels uninspected.',
'12821429':'Inspected2full IFP contracts with measured extents and patient-specificIHC. p1EMR andp2failedEUS/obstruction/subtotal gastrectomy are separate, initial malignancy differentials not diagnoses. Pooled abstractDOG1/CD117 claims remain review-only; no p2Ki67low inferred fromp1. Negative neoplasia/Hpy/JAK2 and final benign pathology preserved. All9/12events including recurrence-free follow-up and5figures reviewed. Literature reports flagged only possible family links,2original cases complete.',
'12718700':'Inspected all3MSI-HDA full contracts and mergedtumors. p1compatible biopsy/pathologicIIA andp2compatible biopsy/clinicalIIIB retained; conflictedp1/p3table rows quarantined instead of forced stages or grade. Corrected duplicatep2pembrolizumab course: PR appended to original palliative treatment, separate observation preserved. Neoadjuvant therapy, scar obstruction/pCR, refusals, postoperative complications, Lynch counseling and5y2m/22m/3y2m follow-up remain source-qualified. Reviewed10/8/13events/4figures;3original cases complete.',
'12940158':'Inspected complete IBS/FMT contract with extensive normal/negative workup, exceptions and all patient research measurements. Donor32M is background, not a clinical roster patient; comparative figure panels aggregate. Burning persists despite improvement;1month pain relief/4month stabilization/>12month course retained. Unverified inflammation quarantined, microbiome association not causal infection. Failed-agent dates embedded in uninspected timeline pixels/supplement remain limitations. Twenty events reviewed;1original case complete.',
'12746974':'Inspected full bottle-cap contract including normal structural findings, exact extents, failedflexible→propofolrigid retrieval, NGsupport/selfremoval/POD3injury, discharge/3month surveillance/ongoing monitoring. Abstract18cm versus caption20cm distances and03clockanchor unresolved. No measured ethanol, intoxication, intent or swallowing-mechanism diagnosis invented. All12event phases and6caption-onlyfigures checked,1original case complete.',
'12812266':'Inspected full RYGBmalnutrition/reversal contract with all32four-stage laboratory values, anatomy dimensions/limb lengths, normal endoscopy/negative psychiatric assessment and explicit provisional hyper-response. Initialalbumin2.5vs2.8 quarantined; proteinunit truncation not corrected except narrative-establishedpre-hospital3.6g/dL. Nutritionfailure→mixednutrition→revision/leakchecks→3/5month recovery preserved. Post-surgical lab draw and discharge-day anchor remain uncertain. Reviewed17events/2figures,1original case complete.',
'12754280':'Inspected2full HCCcontracts, all baseline reserves/negativevascular andmetastatic findings, dose/vial/size-range bounds and12ownedcaptions. Actualsession150mg/75mg entries carry their own PR/CR4/6/8week response rather than duplicate courses or inferred palliative intent. Earlystage lacks formalBCLC/TNM; imagingnecrosis not biopsy. Age-ineligibleversusdeclinedtransplant distinct. Reviewed13/14events through1-/6year calcification/remission, platelet70/µL printedunit and speculative mechanism limitations retained;2original cases complete, no pixels.'}

OLD_KIND_MAP={
'12802722':['history history presentation presentation treatment adverse_event treatment test adverse_event test adverse_event test outcome treatment outcome outcome','history presentation presentation treatment adverse_event treatment test treatment outcome test test adverse_event adverse_event outcome outcome history','history presentation test treatment treatment test test treatment test outcome test outcome outcome'],
'13582412':['treatment treatment treatment test treatment treatment presentation diagnosis treatment follow_up plan test test','treatment test test presentation diagnosis treatment follow_up plan test'],
'13542385':['history plan treatment outcome presentation follow_up follow_up follow_up plan'],
'13612109':['treatment treatment treatment follow_up test presentation treatment treatment diagnosis test plan other'],
'12285374':['test treatment follow_up history history presentation test diagnosis outcome plan','treatment follow_up presentation test treatment diagnosis outcome plan'],
'13618765':['presentation plan treatment follow_up outcome'],
'13624890':['history test diagnosis treatment outcome'],
'13624771':['presentation treatment treatment treatment outcome']}
OLD_NOTES={
'12802722':'Reviewed canonical parent-corrected full contracts against original ethylene-glycol source, all14sections, accepted/quarantined pointers, summary and revised16/16/13phase timelines. Tableinitial ABG/toxicology not made false admission anchors; onset-to-needle not dose, componentBP magnitudes and stopped sedation preserved. p1renal/pneumonia/psychiatric recovery,p2persistent renal/coma/death,p3residual stroke deficits ondischarge retained. Conditional poisoning/stroke/seizure differential distinguished from confirmedtoxicity; nointent assumed inCase2/3. Three original patients complete; no pixels or clinicalaccuracy claim.',
'13582412':'Reviewed canonical2RCC contracts against original source,all14sections, IHC/NGS/pathology/clinical courses and sourcefamily limitation. p2initialpT3aNx now separated from later surveillance recurrence/progression condition, avoiding a time-flattened tumor assessment and invented second primary. p1multi-line therapy,Y90,periportalRT and153month follow-up retained; p2radiation/deferred systemic treatment and16month status distinct. Pooledmorphology,maxima/age/size conflicts quarantine; captionA–Kunresolved,Lbelongs onlyp2. mTORinhibitors conditional, everolimus not assigned. Two original cases complete; secondaryliterature cases excluded.',
'13542385':'Reviewed canonical full VATER graft contract against original clinical narrative/captions. Planned dentalextraction and later maxillary advancement not completed; actual iliacgraft/rhBMP2 steps, post-op diet/antibiotic instructions and day1discharge retained. Reconstructed1week,3month,13month follow-up includes persistentclassIII and numeric4.95/13.05mm graft extents, no named axes invented. Nineevents manually classified; all7figures owned by single originalpatient, pixels uninspected; no schemaaccuracy inference.',
'13612109':'Reviewed canonical full metastatic follicularthyroid target against original source, includingRAI2017/2023/2024,EBRT, progression/restagingand actualLuPSMA6.6GBq cycle. FutureC2surgery/repeatEBRT/TKI, surgeryrefusal andempiricalRAIexclusion distinguished. DOTATATE/FDG count/value conflicts andthyroglobulinng/mL-vs-captionng/dL retained, no correction. Follow-up6weekly ambiguity remains order-only/unknownprecision, not inferred6visits/date. Twelvephases and3figures reviewed; single originalcasecomplete.',
'12285374':'Reviewed canonical2SFT full contracts, imagingextents/namedaxes and allbiopsy/resectionIHC with recoveredtablecurrentrows. NoearlyGIST/sarcoma/pancreatic differential turned confirmed. p1Ki67negative/occasional/4percent conflictquarantined, quantified2/10hpf; p2Ki672percent and1/10hpf retained. Resection/transfusion/recovery/noadjuvant/36-/24month follow-up and futureclosemonitoring remain phased. Ten/eightevents and4ownedfigures reviewed; bibliographytablepatients not roster cases,2originalpatientscomplete.',
'13618765':'Reviewed canonical full IAWS target against original text/recovered idiopathicAWS comparisontable. Other literaturepatients remain background; patient39F never assignedTheirages/causes. Normallabs/negativebreast/Doppler workup, node23/8mm extents, shoulder170degree motion andVAS5→1 retained. Medication/PTordered/planned distinguished from confirmedapplied15sessions, incompletepost-treatment cord versus2monthresolution. Fivephases and2ownedcaptions,1originalcasecomplete,pixelsuninspected.',
'13624890':'Reviewed canonical full PAD contract against original source and rewritten qualified narrative. SCC initiallysuspected, PADproposed andpemphigusconsidered/excluded, never diagnosislistwithoutcertainty. Biopsy/DIFpositivity andantiDsg/IIF/infectiousnegatives retainpolarity;priorLSAand15/8mmplaqueextents sourcegrounded. Unspecified failedtopicals distinct fromactualdiclofenac3percent/tolerability/remission. Fivephases and2individualfigures reviewed,single originalpatientcomplete,pixelsuninspected.',
'13624771':'Reviewed canonical full ESD contract against original short technicalclinical case. Depressed20mmgastric lesion not given unreported malignancy/pathology; actual flap/incision/dissection and enblocresection are separate phases. No adverseevent/pharyngeal/esophagealinjury claims source-explicit rather than silence. Technicaldevice figurepanelAbackground andpanelBpatient correctlyseparated,5events classified. Singleoriginal82Fcasecomplete; video/pixelsuninspected and noextendedfollow-up invented.'}

def old_submission(n):
 from openpatients2.longitudinal import relative_order
 root=Path('benchmarks/dense50');path=root/'schema-drafts'/f'PMC{n}.1.json';target=json.loads(path.read_text());target['roster']['roster_complete']=True;target['roster']['reported_individual_count']=len(target['roster']['patients'])
 for p,kinds in zip(target['patients'],OLD_KIND_MAP[n]):
  timeline=p['companions']['timeline_v2'];kinds=kinds.split();assert len(kinds)==len(timeline['events'])
  for e,kind in zip(timeline['events'],kinds):
   e['kind']=kind
   for t in e['times']:
    t['kind'],t['precision'],t['calendar_value']=time_classification(t['text'])
    if '6 weekly' in t['text']:t['kind']='order_only';t['precision']='unknown';t['calendar_value']=None
    if n=='13542385' and 'skeletal maturity' in t['text']:t['precision']='unknown'
  p['relative_timeline']=relative_order(timeline)
 (root/'schema-submissions').mkdir(exist_ok=True);(root/'schema-submissions'/path.name).write_text(json.dumps(target,ensure_ascii=False,indent=2)+'\n')
# Additional source-reviewed phase events introduced by phase-link audit.
KIND_MAP['12880940'][0]+=' diagnosis test'
KIND_MAP['12811047']=['presentation treatment outcome follow_up follow_up history diagnosis']*2
KIND_MAP['12312621'][0]+=' follow_up test'
KIND_MAP['12312621'][1]+=' follow_up'
KIND_MAP['12832185'][0]+=' presentation'
KIND_MAP['12611218'][0]+=' treatment presentation test history treatment outcome adverse_event'
KIND_MAP['12611218'][1]+=' history test'
KIND_MAP['12611218'][2]+=' treatment presentation test history other adverse_event'
KIND_MAP['12718700'][1]='presentation diagnosis test other treatment treatment follow_up outcome follow_up'
KIND_MAP['12766325'][1]+=' other'
KIND_MAP['12766325'][2]+=' other'
KIND_MAP['12754810'][0]+=' test'
KIND_MAP['12754810'][1]+=' test other'
KIND_MAP['12811543'][0]+=' follow_up other'
KIND_MAP['12786310'][0]+=' test test test test test test diagnosis test outcome treatment'

KIND_MAP['12786310'][0]=KIND_MAP['12786310'][0].replace('presentation diagnosis treatment','presentation presentation treatment',1)
