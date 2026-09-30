"""Lossless envelope compatibility, after preserving all original output.

Handles only completely parsed JSON: an unwrapped section/summary, or exactly
two nonconflicting objects containing those separate outputs. No value repair,
truncation salvage, inserted clinical fields or inferred evidence is allowed.
"""
import json
from pathlib import Path
import chunking_v1 as c
from openpatients2.output_parser import parse_output


def envelope(raw):
    raw = raw.strip()
    if raw.startswith('```') and raw.endswith('```'):
        raw = raw.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    objects = []
    try:
        value = parse_output(raw).value
        if isinstance(value, dict):
            objects = [value]; raw = ''
    except (ValueError, TypeError):
        pass
    decoder = json.JSONDecoder()
    while raw:
        value, end = decoder.raw_decode(raw)
        if not isinstance(value, dict): raise ValueError('Object required')
        objects.append(value); raw = raw[end:].strip()
        if len(objects) > 2: raise ValueError('Too many objects')
    sections, summaries = [], []
    for obj in objects:
        if isinstance(obj.get('section'), dict): sections.append(obj['section'])
        if isinstance(obj.get('items'), list):
            sections.append({k: v for k, v in obj.items() if k in {'coverage','limitations','documentation_status','documentation_evidence','items'}})
        if isinstance(obj.get('summary'), dict): summaries.append(obj['summary'])
        if isinstance(obj.get('claims'), list): summaries.append({k:v for k,v in obj.items() if k in {'claims','limitations'}})
    if len(sections) != 1 or len(summaries) > 1: raise ValueError('Missing or conflicting section/summary objects')
    return {'section': sections[0], 'summary': summaries[0] if summaries else None}


def normalize(row, article):
    if 'maps' not in row: return row
    outputs, changed = [], []
    segs = {s['segment_id']: s for s in article['segments']}
    for n, original in enumerate(row['maps']):
        output = dict(original)
        if original['dependencies']:
            call = json.loads((c.ROOT/'calls/tasks'/f"{original['dependencies'][0]}.json").read_text())
            response = (call.get('attempt_responses') or [{}])[-1]
            if not response.get('error') and response.get('finish_reason') in {'stop','eos'}:
                try:
                    value = envelope(response.get('content', ''))
                except (ValueError, TypeError, KeyError):
                    pass
                else:
                    seen = [segs[s] for s in output['segment_ids']]
                    output['candidate'] = value['section']
                    output['delivered'], output['rejected'] = c.gate(row['task'], value['section'], seen)
                    output['summary'], output['summary_rejected'] = c.summary_gate(value['summary'], seen)
                    if output != original: changed.append(n)
        outputs.append(output)
    # Handle invalid envelope strings without accidentally trying string.get().
    for output in outputs:
        if not isinstance(output['candidate'], dict): output['candidate'] = None
    return {**row, **c.union(outputs), 'maps': outputs, 'envelope_adapter_changed_chunks': changed}


def run():
    articles = {a['pmcid']:a for l in (c.OLD/'articles.jsonl').read_text().splitlines() if (a:=json.loads(l))}
    dest = c.ROOT/'outcomes-normalized'; dest.mkdir(exist_ok=True)
    for path in (c.ROOT/'outcomes').glob('*.json'):
        row = json.loads(path.read_text())
        if 'maps' in row:
            c.write_json(dest/path.name, normalize(row, articles[row['pmcid']]))


if __name__ == '__main__': run()
