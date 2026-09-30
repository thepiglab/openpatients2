"""Verify finished source-format experiment coverage, immutable inputs and gates."""
import json
from pathlib import Path
from openpatients2.data import write_json
from source_formats_v1 import ROOT, ARMS, UNITS, ARTICLES, FIGURES, sha, check_figures
from chunking_v1 import gate


def run():
    paths=[ROOT,ROOT/'firecrawl',ROOT/'firecrawl-regions']
    for root in paths:
        p=json.loads((root/'protocol.json').read_text())
        for f,digest in p['hashes'].items():assert sha(f)==digest, f
    models=[m['id'] for m in json.loads((ROOT/'config.json').read_text())['models']]
    vision=['google/gemma-4-31b-it','meta/muse-spark-1.2']
    units=UNITS+[(p,None,'figures') for p in ARTICLES]
    expected={(m,arm,p,patient,task) for arm in ARMS+['pdf_firecrawl'] for m in models for p,patient,task in units}
    expected|={(m,'pdf_pixels',p,patient,task) for m in vision for p,patient,task in units}
    expected|={('meta/muse-spark-1.2','pdf_firecrawl_regions',p,patient,task) for p,patient,task in UNITS[:3]}
    sources=json.loads((ROOT/'sources.json').read_text());rosters=json.loads((ROOT/'rosters.json').read_text())
    for root in paths[1:]:
        for pid,arms in json.loads((root/'sources.json').read_text()).items():sources[pid].update(arms)
    rows=[json.loads(p.read_text()) for root in paths for p in (root/'outcomes').glob('*.json')]
    keys={(r['model'],r['format'],r['pmcid'],r['patient_id'],r['domain']) for r in rows}
    assert len(rows)==len(keys) and keys==expected,(len(rows),len(expected),expected-keys)
    statuses={}
    for r in rows:
        segments=sources[r['pmcid']]['pdf_plain' if r['format']=='pdf_pixels' else r['format']]
        if r['domain']=='figures':
            delivered,rejected=check_figures(r['candidate'],segments,{p['patient_id'] for p in rosters[r['pmcid']]},FIGURES[r['pmcid']])
        else:delivered,rejected=gate(r['domain'],r['candidate'],segments)
        assert delivered==r['delivered'] and rejected==r['rejected']
        status=r['result']['status'];statuses[status]=statuses.get(status,0)+1
    scores=json.loads((ROOT/'scores.json').read_text())
    assert all(r['completed_cells']==r['expected_cells'] for r in scores)
    assert all(r['clinical']['raw']['required_checks']==60 for r in scores)
    assert all(r['figures']['raw']['units']==(0 if r['format']=='pdf_firecrawl_regions' else 21) for r in scores)
    result={'verified':True,'expected_cells':len(expected),'observed_cells':len(rows),'comparison_rows':len(scores),
            'immutable_protocols':3,'statuses':statuses,'scope':'Artifact integrity and gate reproducibility; not clinical validation.'}
    write_json(ROOT/'verification.json',result);print(json.dumps(result,indent=2))


if __name__=='__main__':run()
