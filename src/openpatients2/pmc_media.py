"""Parse JATS figures and match only file URLs explicitly listed by PMC.

This module has no network I/O. A graphic reference is not a guessed filename.
Availability, article membership, patient assignment and reuse rights are distinct.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from urllib.parse import parse_qs, quote, unquote, urlparse, urlunparse

from defusedxml import ElementTree as SafeET

from .provenance import canonical_pmcid, canonical_pmid, canonical_doi

PARSER_VERSION = "1.1.0"
BUCKET = "pmc-oa-opendata"
CLOUD = f"https://{BUCKET}.s3.amazonaws.com"
CLOUD_HOSTS = {f"{BUCKET}.s3.amazonaws.com", f"{BUCKET}.s3.us-east-1.amazonaws.com"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".svg", ".webp", ".jp2", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".webm", ".m4v", ".mpeg", ".mpg"}
XLINK = "{http://www.w3.org/1999/xlink}href"


def cloud_url(value: str) -> str:
    """Translate documented s3:// URLs to public HTTPS, retaining md5 parameters.

    Only PMC's public bucket is allowed. No arbitrary publisher URL is fetched.
    """
    if not isinstance(value, str):
        raise ValueError("PMC file URL must be a string")
    parsed = urlparse(value)
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Unexpected credentials or fragment in PMC file URL")
    if parsed.scheme == "s3" and parsed.netloc == BUCKET:
        parsed = parsed._replace(scheme="https", netloc=f"{BUCKET}.s3.amazonaws.com")
    if parsed.scheme != "https" or parsed.hostname not in CLOUD_HOSTS or parsed.port not in {None, 443}:
        raise ValueError("File URL is outside the allowlisted PMC Cloud bucket")
    if any(x in {".", ".."} for x in unquote(parsed.path).split("/")):
        raise ValueError("Unexpected relative path in PMC URL")
    return urlunparse(parsed)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def xml_text(node) -> str | None:
    if node is None:
        return None
    text = re.sub(r"\s+", " ", "".join(node.itertext())).strip()
    return text or None


def child(node, name):
    return next((x for x in node if local_name(x.tag) == name), None)


def parsed_xml(text: str):
    # Normal JATS DOCTYPE declarations are permitted, but entity expansion and
    # external resources are not. XML requests have a separate response-size cap.
    return SafeET.fromstring(text, forbid_entities=True, forbid_external=True)


def metadata_flag(value) -> bool | None:
    """Normalize declared boolean flags without treating the string 'no' as true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().casefold()
        if token in {"yes", "true", "1"}:
            return True
        if token in {"no", "false", "0"}:
            return False
    if type(value) is int and value in {0, 1}:
        return bool(value)
    return None


def media_manifest(metadata: dict) -> list[dict]:
    values = metadata.get("media_urls") or []
    if not isinstance(values, list):
        raise ValueError("Unexpected PMC media_urls shape")
    output, seen = [], set()
    for value in values:
        # The current official schema uses URL strings; retain original values.
        if not isinstance(value, str):
            raise ValueError("Unexpected non-string PMC media URL")
        url = cloud_url(value)
        pmcid = canonical_pmcid(metadata.get("pmcid"))
        version = metadata.get("version")
        if pmcid and version is not None:
            if not unquote(urlparse(url).path).startswith(f"/{pmcid}.{version}/"):
                raise ValueError("Media URL belongs to a different article/version prefix")
        if url in seen:
            continue
        seen.add(url)
        parsed = urlparse(url)
        name = unquote(PurePosixPath(parsed.path).name)
        suffix = PurePosixPath(name).suffix.lower()
        kind = "image" if suffix in IMAGE_SUFFIXES else "video" if suffix in VIDEO_SUFFIXES else "supplementary_or_other"
        output.append({"url": url, "original_manifest_url": value, "filename": name,
                       "kind": kind, "md5": parse_qs(parsed.query).get("md5", [None])[0],
                       "availability": "listed_in_pmc_metadata", "http_verified": False})
    return output


def _match_graphic(href: str, media: list[dict]) -> list[dict]:
    parsed = urlparse(href)
    name = unquote(PurePosixPath(parsed.path).name)
    if parsed.scheme or parsed.netloc:
        # An external link is not interchangeable with a same-named local asset.
        try:
            expected = urlparse(cloud_url(href))
        except ValueError:
            return []
        exact_url = [x for x in media if urlparse(x["url"]).path == expected.path]
        return [{**x, "match_method": "explicit_manifest_url"} for x in exact_url]
    if any(part in {".", ".."} for part in unquote(parsed.path).split("/")):
        return []
    exact = [x for x in media if x["filename"] == name]
    if exact:
        return [{**x, "match_method": "exact_manifest_filename"} for x in exact]
    # JATS commonly omits the extension. Match existing entries, never fabricate
    # a .jpg URL or substitute an unrelated attachment from the same article.
    if not PurePosixPath(name).suffix:
        stem = [x for x in media if PurePosixPath(x["filename"]).stem == name]
        return [{**x, "match_method": "extensionless_reference_to_manifest_entry"} for x in stem]
    return []


def parse_figures(xml: str, pmcid: str, version: int, media: list[dict], xml_url: str,
                  expected_pmid: str | None = None) -> dict:
    root = parsed_xml(xml)
    article = root if local_name(root.tag) == "article" else next(
        (x for x in root.iter() if local_name(x.tag) == "article"), None)
    if article is None:
        raise ValueError("Response contains no JATS article")
    front = child(article, "front")
    article_meta = child(front, "article-meta") if front is not None else None
    rights = []
    if article_meta is not None:
        for item in article_meta:
            if local_name(item.tag) == "article-id":
                kind, value = item.get("pub-id-type"), xml_text(item)
                if kind in {"pmc", "pmcid"} and value:
                    check = canonical_pmcid(value if value.upper().startswith("PMC") else f"PMC{value}")
                    if check and check != pmcid:
                        raise ValueError("JATS PMCID does not match requested article")
                if kind == "pmid" and expected_pmid and canonical_pmid(value) != expected_pmid:
                    raise ValueError("JATS PMID does not match metadata")
        permissions = child(article_meta, "permissions")
        if permissions is not None:
            for item in permissions:
                if local_name(item.tag) in {"license", "copyright-statement"}:
                    rights.append({"type": local_name(item.tag), "text": xml_text(item),
                                   "url": item.get(XLINK) or item.get("href"),
                                   "urls": list(dict.fromkeys(x.get(XLINK) or x.get('href')
                                       for x in item.iter() if x.get(XLINK) or x.get('href')))})
    figures, assigned = [], set()
    ids = set()
    parents = {kid: parent for parent in article.iter() for kid in parent}
    figure_nodes = [x for x in article.iter() if local_name(x.tag) == "fig" or
                    (local_name(x.tag) == "fig-group" and not any(local_name(k.tag) == "fig" for k in x.iter()))]
    for ordinal, fig in enumerate(figure_nodes):
        group = parents.get(fig)
        while group is not None and local_name(group.tag) != "fig-group":
            group = parents.get(group)
        fid = fig.get("id")
        key = fid or f"unlabeled-figure-{ordinal + 1}"
        if key in ids:
            key = f"{key}::occurrence-{ordinal + 1}"
        ids.add(key)
        hrefs = list(dict.fromkeys((x.get(XLINK) or x.get("href")) for x in fig.iter()
            if local_name(x.tag) in {"graphic", "inline-graphic", "media"} and (x.get(XLINK) or x.get("href"))))
        matched, unresolved = [], []
        for href in hrefs:
            found = _match_graphic(href, media)
            if not found:
                unresolved.append(href)
            for value in found:
                assigned.add(value["url"])
                if value["url"] not in {x["url"] for x in matched}:
                    matched.append(value)
        figure_rights = []
        for item in fig.iter():
            if local_name(item.tag) in {"license", "copyright-statement", "attrib"}:
                figure_rights.append({"type": local_name(item.tag), "text": xml_text(item),
                                      "url": item.get(XLINK) or item.get("href")})
        # A group-level credit or permission also applies to its child figures.
        # Ignore nested siblings' private credits when inheriting group rights.
        if group is not None:
            for item in group:
                if local_name(item.tag) in {'fig','fig-group'}:continue
                for credit in item.iter():
                    if local_name(credit.tag) in {'license','copyright-statement','attrib'}:
                        statement={'type':local_name(credit.tag),'text':xml_text(credit),
                                   'url':credit.get(XLINK) or credit.get('href')}
                        if statement not in figure_rights:figure_rights.append(statement)
        figures.append({"figure_key": key, "figure_id": fid, "figure_type": local_name(fig.tag),
                        "group_id": group.get("id") if group is not None else None,
                        "group_label": xml_text(child(group, "label")) if group is not None else None,
                        "group_caption": xml_text(child(group, "caption")) if group is not None else None,
                        "label": xml_text(child(fig, "label")),
                        "caption": xml_text(child(fig, "caption")), "alt_text": xml_text(child(fig, "alt-text")),
                        "article_anchor_url": f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}.{version}/#{quote(fid, safe='')}" if fid else None,
                        "source_xml_url": xml_url, "graphic_references": hrefs,
                        "media": matched, "image_urls": [x["url"] for x in matched if x["kind"] == "image"],
                        "unresolved_graphic_references": unresolved, "rights_statements": figure_rights,
                        "association_scope": "article", "patient_assignment": "unverified",
                        "clinical_modality": None, "training_approval": "not_reviewed"})
    # Some conference abstracts put the only clinical image directly in a
    # boxed-text/paragraph, with no <fig> and no caption. Keep that explicit XML
    # association in the inventory; do not guess which patient owns its pixels.
    for node in article.iter():
        if local_name(node.tag) not in {'graphic', 'inline-graphic'}:
            continue
        ancestors=[]; current=parents.get(node)
        while current is not None:
            ancestors.append(current); current=parents.get(current)
        names={local_name(x.tag) for x in ancestors}
        if names & {'fig','fig-group','supplementary-material','inline-supplementary-material'}:
            continue
        if not names & {'body','abstract','app'}:
            continue
        href=node.get(XLINK) or node.get('href')
        if not href:continue
        key=node.get('id') or f'unwrapped-graphic-{len(figures)+1}'
        while key in ids:key+='-unwrapped'
        ids.add(key); matched=_match_graphic(href,media)
        assigned.update(x['url'] for x in matched)
        container=ancestors[0] if ancestors else node
        asset_rights=[{'type':local_name(x.tag),'text':xml_text(x),'url':x.get(XLINK) or x.get('href')}
                      for x in container.iter() if local_name(x.tag) in {'license','copyright-statement','attrib'}]
        figures.append({'figure_key':key,'figure_id':node.get('id'),'figure_type':'unwrapped_graphic',
            'group_id':None,'group_label':None,'group_caption':None,'label':None,'caption':None,'alt_text':None,
            'article_anchor_url':None,'source_xml_url':xml_url,'graphic_references':[href],
            'media':matched,'image_urls':[x['url'] for x in matched if x['kind']=='image'],
            'unresolved_graphic_references':[] if matched else [href], 'rights_statements':asset_rights,
            'association_scope':'article','patient_assignment':'unverified','clinical_modality':None,
            'training_approval':'not_reviewed','inventory_note':'Explicit JATS graphic outside a figure; no inferred caption or patient.'})
    return {"figures": figures, "unassigned_media": [x for x in media if x["url"] not in assigned],
            "article_rights_statements": rights,
            "xml_sha256": hashlib.sha256(xml.encode()).hexdigest()}


def metadata_identifiers(metadata: dict, pmcid: str, version: int) -> dict:
    if canonical_pmcid(metadata.get("pmcid")) != pmcid:
        raise ValueError("PMC metadata has a different or missing PMCID")
    if str(metadata.get("version")) != str(version):
        raise ValueError("PMC metadata version does not match listed object")
    return {"pmcid": pmcid, "pmid": canonical_pmid(metadata.get("pmid")),
            "doi": canonical_doi(metadata.get("doi"))}


def base_discovery(status: str, **kwargs) -> dict:
    return {"parser_version": PARSER_VERSION, "status": status, "has_figures": None,
            "has_image_urls": None, "image_urls": [], "figure_version_records": 0,
            "versions": [], "coverage_scope": "PMC Article Datasets; not all publisher sites",
            "association_scope": "article", "patient_assignment": "unverified",
            "training_approval": "not_reviewed", "image_bytes_downloaded": 0, **kwargs}
