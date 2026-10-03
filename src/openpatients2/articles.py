"""Immutable, bounded JATS evidence packets. No model-generated source text."""
from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from urllib.parse import urlparse

from .pmc_media import (XLINK, child, local_name, media_manifest, metadata_flag,
                        metadata_identifiers, parse_figures, parsed_xml, xml_text,
                        _match_graphic)
from .jats_links import cross_references, bibliography, marked_text

ARTICLE_FORMAT = 'openpatients2.article/1'
from .license_policy import (POLICY_VERSION, ACCEPTED_LICENSES, normalize_license,
                             license_decision, recheck_license)


def _clean(node) -> str:
    """Keep clinical prose/inline math; strip bibliography call-outs only."""
    if node is None:
        return ''
    chunks = []
    def walk(x):
        if local_name(x.tag) in {'msup','msub'} and len(x) == 2:
            walk(x[0])
            chunks.append('^(' if local_name(x.tag) == 'msup' else '_(')
            walk(x[1]); chunks.append(')')
            return
        if x.text:
            chunks.append(x.text)
        for y in x:
            block=local_name(y.tag) in {'p','title','label','list-item','tr','td','th','caption'}
            if block:chunks.append(' ')
            # Preserve mathematical markup rather than turning 10<sup>9</sup>
            # into 109. Parentheses also distinguish chemical subscripts.
            notation = {'sup': '^(', 'sub': '_('}.get(local_name(y.tag))
            notation_start = len(chunks)
            if notation: chunks.append(notation)
            if not (local_name(y.tag) == 'xref' and y.get('ref-type') == 'bibr'):
                walk(y)
            if notation:
                if ''.join(chunks[notation_start+1:]).strip(): chunks.append(')')
                else: del chunks[notation_start:]  # Empty, stripped bibliography callout.
            if block:chunks.append(' ')
            if y.tail:
                chunks.append(y.tail)
    walk(node)
    return re.sub(r'\s+', ' ', ''.join(chunks)).strip()


def parse_article(xml: str, metadata: dict, retrieval: dict | None = None) -> dict:
    pmcid, version = metadata['pmcid'], int(metadata['version'])
    ids = metadata_identifiers(metadata, pmcid, version)
    root = parsed_xml(xml)
    article = root if local_name(root.tag) == 'article' else next(
        (x for x in root.iter() if local_name(x.tag) == 'article'), None)
    if article is None:
        raise ValueError('JATS article missing')
    front = child(article, 'front')
    meta = child(front, 'article-meta') if front is not None else None
    if meta is None:
        raise ValueError('JATS article metadata missing')
    media = media_manifest(metadata)
    figures = parse_figures(xml, pmcid, version, media, metadata.get('xml_url', ''), ids['pmid'])
    segments = []
    def add(kind, text, heading, node, **extra):
        if text:
            segments.append({'segment_id': f'b{len(segments)+1:05}', 'kind': kind,
                'heading': heading, 'jats_id': node.get('id'), 'text': text,
                'cross_references': cross_references(node),
                'text_with_reference_markers': marked_text(node) if cross_references(node) else None,
                'inline_graphic_references':list(dict.fromkeys(x.get(XLINK) or x.get('href') for x in node.iter()
                    if local_name(x.tag) in {'graphic','inline-graphic'} and (x.get(XLINK) or x.get('href')))), **extra})

    # A table row carries the caption and headers. This prevents values losing
    # their columns and allows per-patient rows to be attributed independently.
    def table(node, heading):
        label = _clean(child(node, 'label'))
        caption = _clean(child(node, 'caption'))
        headers = []
        group_heading = None
        thead_rows = {id(row) for h in node.iter() if local_name(h.tag) == 'thead'
                      for row in h.iter() if local_name(row.tag) == 'tr'}
        rows = [x for x in node.iter() if local_name(x.tag) == 'tr']
        for row in rows:
            cells = [x for x in row if local_name(x.tag) in {'td', 'th'}]
            values = [_clean(x) for x in cells]
            is_header = bool(cells) and (id(row) in thead_rows or all(local_name(x.tag) == 'th' for x in cells))
            if is_header:
                headers.append(' | '.join(values))
                continue
            # Explicit span markers preserve complex headers without pretending
            # to have reconstructed a rectangular numerical matrix.
            marked = [f'{v} [rowspan={c.get("rowspan", "1")},colspan={c.get("colspan", "1")}]'
                      if c.get('rowspan','1') != '1' or c.get('colspan','1') != '1' else v for c, v in zip(cells, values)]
            if len(cells) == 1 and int(cells[0].get('colspan','1')) > 1:
                group_heading = values[0]
            text = '\n'.join(filter(None, [label, caption, *headers, group_heading, ' | '.join(marked)]))
            add('table_row', text, heading, row, table_id=node.get('id'))
        if not rows:
            add('table', _clean(node), heading, node, table_id=node.get('id'))
        for foot in node.iter():
            if local_name(foot.tag) == 'table-wrap-foot':
                add('table_footnote', _clean(foot), heading, foot, table_id=node.get('id'))

    skip = {'ref-list', 'ref', 'ack', 'permissions', 'contrib-group', 'fn-group'}
    def visit(node, headings):
        tag = local_name(node.tag)
        if tag in skip:
            return
        if tag in {'sec', 'abstract', 'app'}:
            title = _clean(child(node, 'title')) or ('Abstract' if tag == 'abstract' else '')
            headings = [*headings, title] if title else headings
        heading = ' / '.join(headings) or None
        if tag == 'table-wrap':
            table(node, heading); return
        if tag in {'fig', 'fig-group', 'supplementary-material'}:
            return  # Separate manifests/caption blocks, no duplicate prose.
        if tag in {'p', 'list-item', 'disp-formula', 'statement', 'verse-group'}:
            # JATS allows a table or figure inside a paragraph. Extract those
            # independently instead of flattening/discarding them.
            import copy
            clean = copy.deepcopy(node)
            for par in clean.iter():
                for k in list(par):
                    if local_name(k.tag) in {'table-wrap', 'fig', 'supplementary-material'}:
                        tail = k.tail or ''
                        siblings=list(par); index=siblings.index(k)
                        if index:
                            previous=siblings[index-1]; previous.tail=(previous.tail or '')+tail
                        else:
                            par.text=(par.text or '')+tail
                        par.remove(k)
            add('paragraph' if tag in {'p', 'list-item'} else tag, _clean(clean), heading, clean)
            for nested in node.iter():
                if local_name(nested.tag) == 'table-wrap':
                    table(nested, heading)
            return
        for kid in node:
            if local_name(kid.tag) != 'title':
                visit(kid, headings)

    for a in meta:
        if local_name(a.tag) == 'abstract':
            visit(a, [])
    body = child(article, 'body')
    if body is not None:
        visit(body, [])
    back = child(article, 'back')
    if back is not None:
        # Appendices can contain case details; references/acknowledgments cannot.
        for node in back:
            if local_name(node.tag) in {'app-group', 'app', 'sec'}:
                visit(node, ['Appendix'])
    for fig in figures['figures']:
        fig_node = next((x for x in article.iter() if local_name(x.tag) in {'fig', 'fig-group'}
                         and x.get('id') == fig['figure_id']), None)
        if fig_node is not None:
            for key, tag in (('caption','caption'),('alt_text','alt-text')):
                node = child(fig_node,tag)
                if node is not None and any(local_name(x.tag) in {'sup','sub','msup','msub'} for x in node.iter()):
                    fig[key] = _clean(node)
        if fig.get('group_id'):
            group = next((x for x in article.iter() if local_name(x.tag) == 'fig-group'
                          and x.get('id') == fig['group_id']), None)
            node = child(group,'caption') if group is not None else None
            if node is not None and any(local_name(x.tag) in {'sup','sub','msup','msub'} for x in node.iter()):
                fig['group_caption'] = _clean(node)
        text = '\n'.join(x for x in [fig['label'], fig['group_caption'], fig['caption'], fig['alt_text']] if x)
        add('figure_caption', text, 'Figures', fig_node if fig_node is not None else article,
            figure_id=fig['figure_key'], cross_references=cross_references(fig_node) if fig_node is not None else [],
            text_with_reference_markers=marked_text(fig_node) if fig_node is not None and cross_references(fig_node) else None,
            inline_graphic_references=[])
        fig['caption_segment_ids'] = [s['segment_id'] for s in segments if s.get('figure_id') == fig['figure_key']]
        fig['source_segment_ids'] = [s['segment_id'] for s in segments if set(s.get('inline_graphic_references',[])) & set(fig['graphic_references'])]
        exception_cue = bool(re.search(r'\b(reprinted|reproduced|adapted)\b.*\b(permission|from)\b|all rights reserved',text,re.I))
        fig['reuse_status'] = 'asset_rights_review' if fig['rights_statements'] or exception_cue else 'inherits_article_subject_to_exceptions'
    supplements = []
    for node in article.iter():
        tag = local_name(node.tag)
        if tag not in {'supplementary-material', 'inline-supplementary-material'}:
            continue
        refs = list(dict.fromkeys(x.get(XLINK) or x.get('href') for x in node.iter() if x.get(XLINK) or x.get('href')))
        matches = [m for ref in refs for m in _match_graphic(ref, media)]
        caption = _clean(node)
        supplements.append({'supplement_id': node.get('id') or f'supp-{len(supplements)+1}',
            'description': caption, 'references': refs, 'assets': matches,
            'file_extensions': sorted({PurePosixPath(urlparse(x).path).suffix.lower() for x in refs}),
            'tabular_data_candidate': bool(re.search(r'\b(csv|tsv|xlsx?|blood|pressure|laboratory|dataset|data set)\b', ' '.join(refs)+caption, re.I)),
            'content_inspected': False, 'patient_assignment': 'unresolved'})
    title_group = child(meta, 'title-group')
    title = _clean(child(title_group, 'article-title')) if title_group is not None else ''
    authors = [_clean(x) for x in meta.iter() if local_name(x.tag) == 'contrib' and x.get('contrib-type') == 'author']
    text = '\n\n'.join(f'[{s["segment_id"]}] {s["heading"] or "Article"}\n{s["text"]}' for s in segments)
    rights = license_decision(metadata, figures['article_rights_statements'])
    return {'format': ARTICLE_FORMAT, 'article_id': f'{pmcid}.{version}', **ids, 'version': version,
        'title': title, 'authors': authors, 'article_type': article.get('article-type'),
        'source_url': f'https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/',
        'xml_url': metadata.get('xml_url'), 'metadata': metadata, 'retrieval': retrieval,
        'xml_sha256': hashlib.sha256(xml.encode()).hexdigest(),
        'text_sha256': hashlib.sha256(text.encode()).hexdigest(), 'license': rights,
        'segments': segments, 'text': text, 'figures': figures['figures'],
        'references': bibliography(article),
        'supplements': supplements, 'has_supplementary_material': bool(supplements),
        'unassigned_media': figures['unassigned_media'],
        'lengths': {'words': len(re.findall(r'\S+', '\n'.join(s['text'] for s in segments))),
                    'characters': len(text), 'xml_bytes': len(xml.encode()),
                    'definition': 'abstract + body + appendices + tables + captions; no bibliography/author metadata'},
        'parser_version': '1.4',
        'has_body_text':body is not None and bool(_clean(body)),
        'limitations': ['Tables with row/column spans retain span markers; complex grids need review.',
                         'Supplement contents are not downloaded. Figure captions are author statements, not pixel inspection.']}
