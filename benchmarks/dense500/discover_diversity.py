"""Metadata-only supplemental discovery for explicit case count and common care."""
import asyncio
import json
from pathlib import Path
import httpx
from openpatients2.corpus import search_candidates

ROOT=Path(__file__).resolve().parent
QUERIES={
 'two_patients': '("two cases"[Title] OR "two case reports"[Title] OR "two patients"[Title])',
 'three_patients': '("three cases"[Title] OR "three case reports"[Title] OR "three patients"[Title])',
 'four_to_ten': '("four cases"[Title] OR "five cases"[Title] OR "six cases"[Title] OR "seven cases"[Title] OR "four patients"[Title] OR "five patients"[Title])',
 'common_cardiorespiratory': '("asthma"[Title] OR "COPD"[Title] OR "heart failure"[Title] OR "hypertension"[Title]) AND "case report"[Title]',
 'common_metabolic_renal': '("type 2 diabetes"[Title] OR "hypothyroidism"[Title] OR "acute kidney injury"[Title] OR "urinary tract infection"[Title]) AND "case report"[Title]',
 'common_digestive': '("appendicitis"[Title] OR "cholecystitis"[Title] OR "peptic ulcer"[Title] OR "gastroesophageal reflux"[Title]) AND "case report"[Title]',
 'routine_recovery': '("rehabilitation"[Title] OR "conservative management"[Title] OR "uneventful"[Title] OR "outpatient"[Title]) AND "case report"[Title]',
}

async def main():
 rows=[]
 async with httpx.AsyncClient(timeout=60) as http:
  for group,q in QUERIES.items():
   q+=' AND ("2018/01/01"[Publication Date] : "2025/12/31"[Publication Date])'
   found=await search_candidates(q,limit=150 if group in ['two_patients','three_patients'] else 100,http=http)
   await asyncio.sleep(.6)
   for start in range(0,len(found['pmcids']),50):
    ids=found['pmcids'][start:start+50]
    res=await http.get('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi',params={'db':'pmc','id':','.join(x[3:] for x in ids),'retmode':'json','tool':'openpatients2'})
    res.raise_for_status();payload=res.json()['result']
    rows += [{'discovery_group':group,'pmcid':'PMC'+x,'title':payload[x].get('title'),'pubdate':payload[x].get('pubdate'),'query':q} for x in payload.get('uids',[])]
    await asyncio.sleep(.6)
   (ROOT/'acquisition/diversity-candidates.json').write_text(json.dumps(rows,indent=2)+'\n')
   print(group,len([r for r in rows if r['discovery_group']==group]),flush=True)

if __name__=='__main__':asyncio.run(main())
