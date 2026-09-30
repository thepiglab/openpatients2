"""Attach an explicitly reviewed source-row→article crosswalk without changing original rows."""
from pathlib import Path
import copy
import json
from .data import records,read_jsonl
from .vocabulary import file_sha
from .provenance import (canonical_pmid,canonical_pmcid,canonical_doi,attach_resolved,json_digest)


def attach_article_map(source,mapping,destination):
    out=Path(destination)
    if out.exists() or Path(source).resolve()==out.resolve():raise ValueError('Use a new output for source mapping')
    out.parent.mkdir(parents=True,exist_ok=True);entries={};seen=set();count=0;mapping_sha=file_sha(mapping)
    for row in read_jsonl(mapping):
        if row.get('reviewed') is not True or not all(row.get(k) for k in ('record_id','source_hash','mapping_source','reviewer')):
            raise ValueError('Crosswalk requires reviewed=true, record_id, source_hash, mapping_source and reviewer')
        if row['record_id'] in entries:raise ValueError('Duplicate record in crosswalk')
        entry=copy.deepcopy(row)
        for key,fn in [('pmid',canonical_pmid),('pmcid',canonical_pmcid),('doi',canonical_doi)]:
            if entry.get(key) and not fn(entry[key]):raise ValueError('Invalid crosswalk identifier')
            entry[key]=fn(entry.get(key))
        if not any(entry.get(k) for k in ('pmid','pmcid','doi')):raise ValueError('Crosswalk has no article identifier')
        entries[entry['record_id']]=entry
    temp=out.with_suffix(out.suffix+'.tmp')
    try:
        with temp.open('w',encoding='utf-8') as f:
            temp.chmod(0o600)
            for row in records(source):
                entry=entries.get(row['record_id'])
                if entry:
                    if entry['source_hash']!=row['source_hash']:raise ValueError('Crosswalk source text hash mismatch')
                    seen.add(row['record_id'])
                    row['provenance']=attach_resolved(row['provenance'],{**entry,'status':'source_crosswalk_supplied'}, {})
                    if row['provenance']['article']['identifier_conflicts']:
                        raise ValueError('Crosswalk conflicts with explicit source identifiers')
                    row['provenance']['article']['source_crosswalk']={**entry,'mapping_file_sha256':mapping_sha}
                    row['provenance']['article']['identifier_evidence'].append({'method':'source-reviewed crosswalk','mapping_source':entry['mapping_source'],'reviewer':entry['reviewer']})
                    # An earlier media result might be for different/no article; require rediscovery.
                    row.pop('multimedia',None)
                f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n');count+=1
        if set(entries)-seen:raise ValueError('Crosswalk references absent source rows')
        temp.replace(out)
    finally:temp.unlink(missing_ok=True)
    return {'records':count,'mapped_records':len(seen),'original_rows_changed':False,
            'verification':'Crosswalk review is supplied by the caller; NCBI crosswalk consistency is checked by subsequent enrich-sources.'}
