"""Apply the same lossless envelope reader to summary re-extraction outputs.

Keep raw outcomes intact. Validate against only the quotations actually retained
in the summary, exactly as the generating arm did; never widen its source view.
"""
import hashlib
import json
from pathlib import Path
import chunking_v1 as c
from chunking_adapter_v1 import envelope


def run():
    changed=[]
    for path in (c.ROOT/'outcomes').glob('*-summary_reextract.json'):
        row=json.loads(path.read_text())
        matches=[]
        for signature in row['dependencies']:
            call=json.loads((c.ROOT/'calls/tasks'/f'{signature}.json').read_text())
            if call['identity'].get('phase')=='summary_reextract':matches.append(call)
        if len(matches)!=1:continue
        response=(matches[0].get('attempt_responses') or [{}])[-1]
        if response.get('error') or response.get('finish_reason') not in {'stop','eos'}:continue
        try:value=envelope(response.get('content',''))
        except (ValueError,TypeError):continue
        quotes={}
        for claim in row['summary']['claims']:
            for e in claim['evidence']:quotes.setdefault(e['segment_id'],[]).append(e['quote'])
        seen=[{'segment_id':sid,'heading':'Verified quotations retained in summary','text':'\n'.join(c.unique(qs))} for sid,qs in quotes.items()]
        normalized={**row,'candidate':value['section']}
        normalized['delivered'],rejected=c.gate(row['task'],value['section'],seen)
        normalized['envelope_adapter_rejections']=rejected
        normalized['envelope_adapter_changed']=normalized['candidate']!=row['candidate'] or normalized['delivered']!=row['delivered']
        if normalized['envelope_adapter_changed']:changed.append(path.name)
        c.write_json(c.ROOT/'outcomes-normalized'/path.name,normalized)
    c.write_json(c.ROOT/'secondary-adapter-amendment.json',{'purpose':'Use identical lossless envelope normalization for primary maps and summary re-extraction; otherwise unwrapped JSON would confound the summary bottleneck comparison',
        'posthoc':True,'changed_outcomes':changed,'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    print('Losslessly normalized secondary outcomes:',len(changed))


if __name__=='__main__':run()
