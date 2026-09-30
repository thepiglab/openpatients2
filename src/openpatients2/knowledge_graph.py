"""Evidence-linked research graph. Never synthesizes patient events or causal edges."""
from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

from .coding import checked_patient, accepted_mappings
from .data import read_jsonl, write_json
from .provenance import json_digest, source_reference
from .validation import validate
from .vocabulary import Catalog


def ident(kind,*parts):
    return kind+':'+json_digest(parts)


def export_graph(source,catalog_path,destination):
    """Streaming JSONL nodes/edges; concepts reused, people NEVER code-deduplicated.

    Every mention (including negation and family history) is a Fact node, not a
    positive diagnosis edge. Only accepted equivalent codings denote concepts.
    """
    out=Path(destination)
    if out.exists():raise ValueError('Choose a new graph output directory')
    out.mkdir(parents=True);counts=Counter();seen_concepts=set();seen_articles=set();seen_cases=set()
    try:
        with Catalog(catalog_path) as cat,(out/'nodes.jsonl').open('w',encoding='utf-8') as nf,(out/'edges.jsonl').open('w',encoding='utf-8') as ef:
            (out/'nodes.jsonl').chmod(0o600);(out/'edges.jsonl').chmod(0o600)
            def node(kind,nid,attributes):
                nf.write(json.dumps({'id':nid,'type':kind,'attributes':attributes},ensure_ascii=False,allow_nan=False)+'\n');counts['nodes_'+kind]+=1
            def edge(kind,a,b,**attrs):
                ef.write(json.dumps({'id':ident('edge',kind,a,b,attrs),'type':kind,'source':a,'target':b,'attributes':attrs},ensure_ascii=False,allow_nan=False)+'\n');counts['edges_'+kind]+=1
            def concept(system,version,code):
                cid=ident('concept',system,version,code)
                if cid in seen_concepts:return cid
                item=cat.lookup(system,code,version)
                if not item:raise ValueError('Missing concept in graph catalog')
                seen_concepts.add(cid);node('Concept',cid,item)
                # All hierarchy edges come from this pinned local terminology, not
                # semantic guesses, ICD code prefixes or LLM-generated relations.
                parents=cat.db.execute('SELECT parent,relation FROM edges WHERE system=? AND version=? AND child=?',(system,version,code)).fetchall()
                for parent,relation in parents:
                    pid=concept(system,version,parent)
                    edge('classification_parent' if relation=='tabular_parent' else 'is_a',cid,pid,provenance=relation,catalog_fingerprint=cat.fingerprint)
                return cid
            from .warehouse import DOMAINS
            for row in read_jsonl(source):
                record=checked_patient(row);rid=ident('case',record['record_id'],record['source_hash'])
                if rid in seen_cases:raise ValueError('Duplicate processing key in graph input')
                seen_cases.add(rid);node('SourceCase',rid,record)
                ref=source_reference(record.get('provenance',{}).get('article',{}))
                if ref:
                    aid=ident('article',*ref)
                    if aid not in seen_articles:
                        seen_articles.add(aid);node('Article',aid,record['provenance']['article'])
                    edge('source_article',rid,aid,patient_identity_verified=False)
                accepted=list(accepted_mappings(row,cat)) if row.get('terminology') else []
                fmap={};tumors={}
                for domain,(task,collection) in DOMAINS.items():
                    section=row['sections'].get(task)
                    if section is None:continue
                    evidence=validate(task,section,record['text']).evidence
                    for ordinal,fact in enumerate(section[collection]):
                        fid=ident('fact',rid,domain,ordinal,json_digest(fact));fmap[(domain,ordinal)]=fid
                        node('Fact',fid,{'domain':domain,'ordinal':ordinal,**fact})
                        edge('has_documented_fact',rid,fid,clinical_correctness_reviewed=False)
                        prefix=f'/{collection}/{ordinal}/evidence/'
                        for ev in evidence:
                            if ev['path'].startswith(prefix):
                                eid=ident('evidence',fid,ev);node('Evidence',eid,{**ev,'source_hash':record['source_hash']})
                                edge('supported_by',fid,eid,span_match_only=True)
                        if domain=='oncology_tumors' and fact.get('tumor_ref'):
                            tumors[fact['tumor_ref']]=fid
                for (domain,ordinal),fid in fmap.items():
                    if domain in {'oncology_biomarkers','oncology_treatments'}:
                        task,coll=DOMAINS[domain];ref=row['sections'][task][coll][ordinal].get('tumor_ref')
                        if ref in tumors:edge('linked_to_tumor',fid,tumors[ref],relation_source='extracted_and_structurally_validated')
                for mapping,c in accepted:
                    cid=concept(c['system'],c['version'],c['code'])
                    edge('denotes',fmap[(mapping['domain'],mapping['ordinal'])],cid,
                         mapping_id=mapping['mapping_id'],mapping_status=mapping['status'],relation=mapping['relation'],
                         field=mapping['field'],catalog_fingerprint=cat.fingerprint,
                         note='Concept annotation retains Fact subject/assertion/temporality; NOT an affirmative patient diagnosis.')
            result={'format':'openpatients2.graph/1','counts':dict(counts),'source':str(Path(source).resolve()),
                    'catalog_fingerprint':cat.fingerprint,'person_deduplication':False,
                    'causal_inference':False,'fhir_conformance':False,'model_reasoning_indexed':False,
                    'clinical_accuracy':None,'media_assignment':'SourceCase multimedia remains article-level/unverified unless independently reviewed.'}
            write_json(out/'manifest.json',result);return result
    except BaseException:
        shutil.rmtree(out,ignore_errors=True);raise
