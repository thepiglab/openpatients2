"""Bibliography-context ownership correction proposal; canonical untouched."""
import json
from pathlib import Path
from xml.etree import ElementTree as etree
r=Path('benchmarks/dense50');aid='PMC13624890.1';a=json.loads((r/'annotations'/f'{aid}.json').read_text());s=json.loads((r/'sources'/f'{aid}.json').read_text());p=a['patients'][0];ref=next(z for z in s['references'] if z['reference_id']=='ref15');xml=etree.parse(str(r/'raw-xml'/f'{aid}.xml'));dic=next(z for z in xml.findall('.//body/p') if 'diclofenac' in ''.join(z.itertext()));assert dic.findall('.//xref[@rid="ref15"]')
quarantined={'p1-f034','p1-f035','p1-f036'}
for f in p['facts']:
 if f['fact_id'] in quarantined:
  f['fields']['subject']='other';f['scoring']='review_only';f['certainty']='ambiguous';f['notes']='Cited-other-patient ownership: b00009 ends with JATS bibr xref rid=ref15. '+ref['citation_text']+'. This is a vulvar PAD treatment report, not demonstrated treatment/outcome of the68-year-old male index case. Original quote retained for review, excluded from clinical targets.'
  f['clinical_statement']='Cited vulvar PAD treatment report: '+f['clinical_statement']
  # Remove index-specific diagnosis indication added before bibliography review.
  if f['fact_id']=='p1-f034':f['fields'].pop('indication',None);f['evidence']=[e for e in f['evidence'] if e['segment_id']=='b00009']
removed={e['event_id'] for e in p['events'] if quarantined.intersection(e['fact_ids'])};p['events']=[e for e in p['events'] if e['event_id'] not in removed];p['temporal_relations']=[e for e in p['temporal_relations'] if e['from_event_id'] not in removed and e['to_event_id'] not in removed]
p['summary']=[z for z in p['summary'] if not quarantined.intersection(z['fact_ids'])]
p['domain_coverage']['outcomes']=dict(status='not_documented',note='No index-patient treatment tolerability/remission/follow-up outcome documented. Diclofenac tolerance/remission sentence refers to cited vulvar PAD report ref15 and is excluded.')
p['domain_coverage']['medications']=dict(status='documented',note='Index prior unspecified topical therapy failure retained. Diclofenac3% cited-other-patient claim quarantined.')
lim='Diclofenac sodium3% gel tolerance and complete remission in b00009 are linked by JATS bibliography xref ref15 to Martínez Ortega etal2022, Treatment of papular acantholytic dyskeratosis of vulva with diclofenac sodium3% gel, DOI10.1111/dth.15867; these are not assigned to this male index case. Index postdiagnosis treatment and outcome not documented.'
a['article_limitations'].append(lim);p['ambiguities'].append(dict(issue=lim,evidence=[dict(segment_id='b00009',quote=next(z['text'] for z in s['segments'] if z['segment_id']=='b00009'))]))
for c in a['source_coverage']:
 if c['segment_id']=='b00009':c.update(disposition='background',patient_ids=[],note='Cited-other-patient therapeutic literature statement. Raw XML links this sentence to ref15 vulvar PAD treatment report; no index course assigned.')
a['annotation_provenance']['passes'].append('Read raw JATS diclofenac paragraph and bibliography ref15; treatment/tolerability/remission ownership corrected to cited vulvar report, quarantined facts preserved and male therapy/outcome events removed.')
(r/'annotator-1-proposals'/f'{aid}.json').write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n');print('quarantined',sorted(quarantined),'removed',sorted(removed),'events',len(p['events']))
