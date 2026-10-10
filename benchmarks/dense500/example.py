"""Render the actual reviewed release row, without creating another annotation."""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / 'data/dense_clinical_cases_v1'


def compact(value):
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items()
                if v is not None and v != [] and v != {} and k != 'evidence'}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def cell(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text.replace('|', '&#124;').replace('\n', '<br>')


def render(article='PMC13612135.1', patient='p1'):
    legacy = OUT / 'legacy' / 'patients.jsonl'
    rows = [json.loads(s) for s in (legacy if legacy.exists() else OUT / 'patients.jsonl').read_text().splitlines()]
    bundle = next(r for r in rows if r['article_id'] == article and
                  r['source']['patient_target']['patient_id'] == patient)
    path = OUT / 'examples' / f'{article}-{patient}.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + '\n')
    source = bundle['source']['attribution']
    lines = ['# Example of a reviewed patient bundle', '',
             f"Source: [{source['title']}]({source['article_url']}).", '',
             'AI reference extraction, individually inspected against the article by both annotator and parent. '
             'The runtime schemas are those used by Glimmer; these are not Glimmer inference results or physician gold.', '',
             f'Complete JSON, including every null field, quotation and review receipt: [patient bundle](examples/{path.name}).', '',
             '## Clinical narrative', '']
    for claim in bundle['companions']['summary']['claims']:
        lines += [claim.get('text', claim.get('claim', str(claim))), '']
    lines += ['## All clinical sections', '',
              'Nulls mean unreported; an empty section is not a negative finding. '
              'The same supported finding can have separate representations in general and specialist schemas. '
              'Row counts therefore do not count distinct biological facts.', '',
              '| Section | Documentation | Rows |', '| --- | --- | ---: |']
    for task, section in bundle['sections'].items():
        count = 1 if task == 'case_context' else sum(len(section.get(k, [])) for k in ('items', 'tumors', 'biomarkers', 'treatments'))
        lines.append(f"| {task} | {section['documentation_status']} | {count} |")
    pointers = bundle['reference_provenance']['fact_pointers']
    for task, section in bundle['sections'].items():
        lines += ['', f'### {task}', '', f"Documentation: `{section['documentation_status']}`; coverage: `{section['coverage']}`.", '']
        refs = [p for p in pointers if p['task'] == task]
        if not refs:
            lines += ['No source-supported rows.']
            continue
        lines += ['| Fact | Populated fields (unknown values retained) | Evidence segments |', '| --- | --- | --- |']
        for ref in refs:
            value = section
            if ref['pointer'] != '/':
                for part in ref['pointer'].strip('/').split('/'):
                    value = value[int(part)] if isinstance(value, list) else value[part]
            value = compact(value)
            if task == 'case_context':
                value.pop('documentation_evidence', None)
            lines.append(f"| {ref['fact_id']} | {cell(value)} | {cell(', '.join(dict.fromkeys(e['segment_id'] for e in ref['source_evidence'])))} |")
    lines += ['', '## Relative events', '',
              'Event IDs identify events; only explicit edges establish order. No calendar dates are invented.', '',
              '| Event | Kind | Source time | Description | Linked facts |', '| --- | --- | --- | --- | --- |']
    timeline = bundle['companions']['timeline_v2']
    for event in timeline['events']:
        lines.append('| ' + ' | '.join(cell(v) for v in [event['event_id'], event['kind'],
            ', '.join(t['text'] for t in event['times']) or 'not stated', event['description'], ', '.join(event['fact_ids'])]) + ' |')
    lines += ['', '```text']
    for edge in timeline['edges']:
        lines.append(f"{edge['from_event_id']} --{edge['relation']}--> {edge['to_event_id']}")
    lines += ['```', '', '## Actual figure and panel inspection', '', f"Current pixel-review status: `{bundle['vision']['status']}`.", '',
              'Visual observations are separated from source/caption interpretations. The earlier text-only companion '
              'retains its original limitations; the current pixel review is recorded separately below.']
    for panel in bundle['vision'].get('panels', []):
        lines += ['', f"### {panel['figure_id']} / {panel['panel'] or 'whole figure'}", '',
                  panel['general_description'], '', panel['detailed_description'], '',
                  '| Field | Reviewed value |', '| --- | --- |']
        for key in ['image_kind', 'has_chart', 'chart_types', 'is_clinical_chart', 'imaging_modalities',
                    'imaging_submodalities', 'medical_domains', 'body_parts', 'mentioned_categories',
                    'measured_quantities', 'charts', 'pixel_observations', 'caption_claims',
                    'clinical_significance', 'diagram_relations', 'patient_ids', 'assignments', 'limitations']:
            lines.append(f'| {key} | {cell(panel[key])} |')
    lines += ['', '## Clinical scope and evidence', '',
              'These limitations belong to the text extraction. Any statement about uninspected images '
              'describes that earlier text-only stage; use the current pixel status above.']
    for limit in bundle['companions']['summary']['limitations']:
        lines += ['', '- ' + limit]
    lines += ['', '### Exact fact evidence', '',
              'The following retained quotations permit checking every structured row against its source.']
    evidence = {}
    for ref in pointers:
        for entry in ref['source_evidence']:
            evidence.setdefault((entry['segment_id'], entry['quote']), []).append(ref['fact_id'])
    for (segment, quote), facts in evidence.items():
        lines += ['', f"**{segment} — {', '.join(facts)}**", '', '> ' + quote.replace('\n', '\n> ')]
    (OUT / 'EXAMPLE_PATIENT.md').write_text('\n'.join(lines) + '\n')


if __name__ == '__main__':
    render()
