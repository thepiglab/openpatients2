"""Bibliographic and inline links retained outside the clinical text stream.

A citation is a retrieval edge, never an assertion of patient identity. All rid
values survive, including unresolved and multi-target callouts.
"""
import re
from .pmc_media import local_name, xml_text, XLINK
from .provenance import canonical_pmcid, canonical_pmid, canonical_doi


def cross_references(node):
    return [{'ref_type': x.get('ref-type'), 'target_ids': (x.get('rid') or '').split(),
             'label': xml_text(x)} for x in node.iter() if local_name(x.tag) == 'xref']


def marked_text(node):
    """Exact callout placement for disambiguating several citations in a paragraph.

    This is an auxiliary view, never a replacement for the evidence text. Models
    must still quote the canonical segment text for exported clinical claims.
    """
    chunks = []
    def walk(x):
        if local_name(x.tag) == 'xref':
            chunks.append(f' [{x.get("ref-type", "xref").upper()}:{x.get("rid", "")} {xml_text(x) or ""}] ')
            return
        if x.text:
            chunks.append(x.text)
        for y in x:
            chunks.append(' ' if local_name(y.tag) in {'p','title','label','tr','td','th','list-item'} else '')
            walk(y)
            if y.tail:
                chunks.append(y.tail)
    walk(node)
    return re.sub(r'\s+', ' ', ''.join(chunks)).strip()


def bibliography(article):
    output = []
    for ref in article.iter():
        if local_name(ref.tag) != 'ref':
            continue
        identifiers = {'pmcid': [], 'pmid': [], 'doi': []}
        urls = []
        for x in ref.iter():
            kind = (x.get('pub-id-type') or '').lower()
            name = {'pmc': 'pmcid', 'pmcid': 'pmcid', 'pmid': 'pmid', 'doi': 'doi'}.get(kind)
            if local_name(x.tag) == 'pub-id' and name:
                value = xml_text(x)
                if name == 'pmcid' and value and value.isdigit():
                    value = 'PMC' + value
                normalized = {'pmcid': canonical_pmcid, 'pmid': canonical_pmid,
                              'doi': canonical_doi}[name](value)
                if normalized and normalized not in identifiers[name]:
                    identifiers[name].append(normalized)
            if local_name(x.tag) == 'ext-link' and x.get(XLINK):
                urls.append(x.get(XLINK))
        output.append({'reference_id': ref.get('id'), 'citation_text': xml_text(ref),
                       'identifiers': identifiers, 'external_urls': list(dict.fromkeys(urls)),
                       'identity_status': 'bibliographic_only'})
    return output
