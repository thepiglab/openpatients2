"""Ground existing clinical facts in a local, versioned vocabulary.

This is terminology normalization, NOT ontology invention, diagnosis prediction or
billing-code assignment. Facts and their evidence are never rewritten by coding.
"""
from __future__ import annotations

import copy
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
from pathlib import Path

import yaml

from .data import normalize, read_jsonl, write_json
from .provenance import json_digest
from .validation import validate
from .vocabulary import Catalog, ICD10CM, SNOMED, LOINC, normalize_term

FORMAT='openpatients2.coding/1'
# A code attached to a finding names the concept; assertion, subject and time
# remain in the fact and are NOT encoded by pretending the code is affirmative.
DEFAULT_RULES={
    'conditions': {'field':'name','systems':[SNOMED,ICD10CM]},
    'symptoms_function': {'field':'name','systems':[SNOMED]},
    'procedures_devices': {'field':'name','systems':[SNOMED]},
    'observations': {'field':'name','systems':[LOINC]},
    'oncology_tumors': {'field':'name','systems':[SNOMED,ICD10CM]},
    'oncology_biomarkers': {'field':'name','systems':[LOINC]},
    'family_genetics': {'field':'name','systems':[SNOMED]},
}


def load_policy(path=None):
    raw=yaml.safe_load(Path(path).read_text()) if path else {}
    raw=raw or {}
    if set(raw)-{'rules','candidate_limit','auto_exact','workers'}:
        raise ValueError('Unknown coding policy option')
    policy={'rules':raw.get('rules',copy.deepcopy(DEFAULT_RULES)),
            'candidate_limit':raw.get('candidate_limit',12),'auto_exact':raw.get('auto_exact',True),
            'workers':raw.get('workers',1)}
    if type(policy['auto_exact']) is not bool or not 1<=policy['candidate_limit']<=100 or not 1<=policy['workers']<=128:
        raise ValueError('Invalid coding policy limits')
    from .warehouse import DOMAINS,fields_for
    for domain,r in policy['rules'].items():
        if domain not in DOMAINS or set(r)!={'field','systems'} or r['field'] not in fields_for(domain):
            raise ValueError('Coding rules must name a known domain/field and systems')
        if not isinstance(r['systems'],list) or not r['systems'] or len(set(r['systems']))!=len(r['systems']):
            raise ValueError('Coding systems must be a nonempty unique list')
    return policy


def checked_patient(row):
    source=normalize(row['source'])
    for task,section in row['sections'].items():
        if section is not None:
            checked=validate(task,section,source['text'])
            if not checked.valid:
                raise ValueError(f'Clinical extraction must pass source validation before code mapping: {source["record_id"]}:{task}')
    return source


def extraction_digest(row):
    return json_digest({'record_id':row['source']['record_id'],'source_hash':row['source']['source_hash'],
                        'sections':row['sections']})


def _issues(domain,fact,candidate):
    issues=[]
    system=candidate['system'];attrs=candidate['attributes']
    if not candidate['active']:
        issues.append('inactive_concept')
    if system==SNOMED:
        tags={'conditions':{'disorder','finding'},'oncology_tumors':{'disorder','finding'},
              'symptoms_function':{'finding','observable entity'},
              'family_genetics':{'disorder','finding'}}
        if domain=='procedures_devices':
            allowed={'physical object'} if fact.get('kind')=='device' else {'procedure','regime/therapy'}
        else:
            allowed=tags.get(domain)
        if allowed and attrs.get('semantic_tag') not in allowed:
            issues.append('semantic_domain_mismatch')
    words=set(re.findall(r'\w+', normalize_term(str(fact.get('name','')))))
    documented=fact.get('laterality') if fact.get('laterality') in {'left','right','bilateral'} else None
    if not documented:
        mentioned=words & {'left','right','bilateral'}
        if len(mentioned)==1:
            documented=next(iter(mentioned))
    labels=set(re.findall(r'\w+',normalize_term(candidate['display']))) & {'left','right','bilateral'}
    if labels and not documented:
        issues.append('undocumented_laterality')
    elif labels and documented not in labels:
        issues.append('conflicting_laterality')
    if system==LOINC:
        # Six-axis matching needs more than an analyte name. Values/units are not
        # sufficient to invent specimen, method, challenge or collection interval.
        issues.append('loinc_axes_require_adjudication')
        if attrs.get('METHOD_TYP') and not (fact.get('method') or fact.get('assay')):
            issues.append('method_not_documented')
        if attrs.get('SYSTEM') and not fact.get('specimen'):
            issues.append('specimen_not_documented')
    if system==ICD10CM:
        if not candidate['selectable']:
            issues.append('icd_category_not_complete_code')
        if any(x['kind'] not in {'includes'} for x in attrs.get('instructions',[])):
            issues.append('coding_instructions_not_evaluated')
    return issues


HARD_ISSUES={'inactive_concept','semantic_domain_mismatch','conflicting_laterality','undocumented_laterality'}


def _short_ambiguous(text):
    letters=re.sub(r'[^A-Za-z]','',text)
    return len(text.strip())<=3 or (text.strip().isupper() and len(letters)<=6)


def candidate_identity(candidate):
    return json_digest({k:candidate[k] for k in ('system','version','code')})[:24]


def mapping_request(mapping):
    """Bound the model to finite catalog candidates; no request to recall codes."""
    data={k:copy.deepcopy(mapping[k]) for k in ('mapping_id','record_id','domain','field','source_text','fact',
          'fact_hash','source_hash','system','version','catalog_fingerprint','candidates')}
    data['request_digest']=json_digest(data)
    return data


def link_patient(row, catalog: Catalog, policy: dict):
    from .warehouse import DOMAINS
    row=copy.deepcopy(row);source=checked_patient(row);row['source']=source
    mappings=[];skipped=[]
    for domain,(task,collection) in DOMAINS.items():
        section=row['sections'].get(task)
        if not section:
            continue
        rule=policy['rules'].get(domain)
        if not rule:
            if section[collection]:skipped.append({'domain':domain,'reason':'no_configured_system','facts':len(section[collection])})
            continue
        for ordinal,fact in enumerate(section[collection]):
            text=fact.get(rule['field'])
            if not isinstance(text,str) or not text.strip():
                continue
            # Genetic variants and inheritance statements are not diagnoses.
            if domain=='family_genetics' and fact.get('kind')!='family_condition':
                continue
            for system in rule['systems']:
                base={'record_id':source['record_id'],'source_hash':source['source_hash'],
                      'domain':domain,'ordinal':ordinal,'field':rule['field'],'source_text':text,
                      'fact_hash':json_digest(fact),'fact':copy.deepcopy(fact),'system':system,
                      'version':catalog.releases.get(system),'catalog_fingerprint':catalog.fingerprint}
                base['mapping_id']=json_digest({k:base[k] for k in ('record_id','source_hash','domain','ordinal','field','fact_hash','system','version','catalog_fingerprint')})
                candidates=[]
                for item in catalog.search(system,text,policy['candidate_limit']):
                    candidate=copy.deepcopy(item)
                    candidate['candidate_id']=candidate_identity(candidate)
                    candidate['issues']=_issues(domain,fact,candidate)
                    if system==SNOMED:
                        candidate['icd10cm_crossmap_rules']=catalog.crosswalk_candidates(candidate['code'])
                    candidates.append(candidate)
                exact=[c for c in candidates if c['retrieval']=='exact_alias']
                eligible=[c for c in exact if not c['issues']]
                selected=None;status='unresolved';relation=None
                if system not in catalog.releases:
                    status='catalog_unavailable'
                elif candidates:
                    status='ambiguous' if len(exact)>1 else 'needs_review'
                    if (policy['auto_exact'] and len(exact)==1 and len(eligible)==1
                       and not exact[0]['exact_truncated'] and not _short_ambiguous(text)):
                        selected=eligible[0]['candidate_id'];status='exact_unique';relation='equivalent'
                base.update(candidates=candidates,selected_candidate_id=selected,status=status,relation=relation,
                            mapping_review=None,model_proposals=[],
                            clinical_fact_verified=False,
                            interpretation='Derived terminology annotation. Code existence does not establish clinical correctness.')
                request=mapping_request(base)
                base['request_digest']=request['request_digest']
                mappings.append(base)
    row['terminology']={'format':FORMAT,'catalog_fingerprint':catalog.fingerprint,
                        'policy_fingerprint':json_digest(policy),'extraction_digest':extraction_digest(row),
                        'mappings':mappings,'skipped_domains':skipped,
                        'rights_note':'Official vocabulary content remains subject to its source terms; code does not grant redistribution rights.'}
    return row


def validate_coded_patient(row,catalog):
    """Re-derive immutable occurrence/candidate data before indexing edited exports."""
    term=row.get('terminology')
    if not term or term.get('format')!=FORMAT or term.get('catalog_fingerprint')!=catalog.fingerprint:
        raise ValueError('Missing or mismatched terminology catalog identity')
    if term.get('extraction_digest')!=extraction_digest(row):
        raise ValueError('Coded extraction changed; mapping provenance is invalid')
    from .warehouse import DOMAINS
    seen=set()
    valid_status={'exact_unique','ambiguous','needs_review','unresolved','catalog_unavailable',
                  'model_proposed','model_abstained','reviewed','reviewed_unresolved'}
    for m in term['mappings']:
        if m['status'] not in valid_status or m['relation'] not in {None,'equivalent','broader'}:
            raise ValueError('Invalid mapping status or relation')
        if (m['mapping_id'] in seen or m['catalog_fingerprint']!=catalog.fingerprint
            or m['version']!=catalog.releases.get(m['system'])):
            raise ValueError('Duplicate mapping or altered catalog identity')
        seen.add(m['mapping_id'])
        expected_id=json_digest({k:m[k] for k in ('record_id','source_hash','domain','ordinal','field','fact_hash','system','version','catalog_fingerprint')})
        if expected_id!=m['mapping_id'] or type(m['ordinal']) is not int or m['ordinal']<0:
            raise ValueError('Invalid mapping occurrence identity')
        task,coll=DOMAINS[m['domain']]
        fact=row['sections'][task][coll][m['ordinal']]
        if (m['fact_hash']!=json_digest(fact) or m['fact']!=fact or m['source_hash']!=row['source']['source_hash']
            or m['record_id']!=row['source']['record_id'] or m['source_text']!=fact[m['field']]):
            raise ValueError('Coding occurrence does not match its source fact')
        if mapping_request(m)['request_digest']!=m['request_digest']:
            raise ValueError('Coding candidates or context were edited')
        candidate_ids=[c['candidate_id'] for c in m['candidates']]
        if len(candidate_ids)!=len(set(candidate_ids)):
            raise ValueError('Duplicate candidate identity')
        for c in m['candidates']:
            actual=catalog.lookup(m['system'],c['code'],m['version'])
            if not actual or any(actual[k]!=c[k] for k in actual):
                raise ValueError('Candidate does not match pinned terminology catalog')
            if c['issues']!=_issues(m['domain'],fact,c) or c['candidate_id']!=candidate_identity(c):
                raise ValueError('Candidate eligibility was edited')
        selected=next((c for c in m['candidates'] if c['candidate_id']==m['selected_candidate_id']),None)
        if m['selected_candidate_id'] and not selected:
            raise ValueError('Selected candidate not in retrieved set')
        if selected and any(x in HARD_ISSUES for x in selected['issues']):
            raise ValueError('Selected candidate conflicts with documented context')
        if m['status']=='exact_unique':
            exact=[c for c in m['candidates'] if c['retrieval']=='exact_alias']
            if not selected or len(exact)!=1 or selected['issues'] or selected['exact_truncated'] or _short_ambiguous(m['source_text']):
                raise ValueError('Invalid automatic exact mapping')
            actual_exact=catalog.search(m['system'],m['source_text'],100)
            unique=[c for c in actual_exact if c['retrieval']=='exact_alias']
            if len(unique)!=1 or unique[0]['code']!=selected['code'] or m['relation']!='equivalent':
                raise ValueError('Automatic exact mapping is not unique in catalog')
        if m['status'] in {'reviewed','reviewed_unresolved'}:
            review=m.get('mapping_review') or {}
            if not all(isinstance(review.get(k),str) and review[k].strip() for k in ('reviewer','reviewed_at','rationale')):
                raise ValueError('Reviewed mapping requires a recorded human review')
            if (review.get('request_digest')!=m['request_digest'] or review.get('candidate_id')!=m['selected_candidate_id']
                or review.get('relation')!=m['relation'] or review.get('reviewed') is not True):
                raise ValueError('Review does not match selected mapping')
            if (m['status']=='reviewed') != (review.get('decision')=='select' and selected is not None):
                raise ValueError('Review decision/status mismatch')
        if m['status'] in {'unresolved','catalog_unavailable','needs_review','ambiguous','model_abstained','reviewed_unresolved'} and (selected or m['relation']):
            raise ValueError('Unresolved mapping must not have a selected concept')
    return term


def accepted_mappings(row,catalog):
    validate_coded_patient(row,catalog)
    for m in row['terminology']['mappings']:
        if m['status'] in {'exact_unique','reviewed'} and m['relation']=='equivalent' and m['selected_candidate_id']:
            c=next(c for c in m['candidates'] if c['candidate_id']==m['selected_candidate_id'])
            yield m,c


def link_file(source,destination,catalog_path,policy_path=None):
    """Bounded CPU batches; SQLite catalog is read-only per worker."""
    policy=load_policy(policy_path)
    out=Path(destination);out.parent.mkdir(parents=True,exist_ok=True)
    if out.resolve()==Path(source).resolve() or out.exists():
        raise ValueError('Choose a new coded output; preserve original extraction')
    tmp=out.with_suffix(out.suffix+'.tmp');counts=Counter();n=0
    # Batch per worker keeps the read-only connection/alias cache alive for 64
    # patients, avoiding a database open per mapping while bounding host memory.
    def batch_map(batch):
        with Catalog(catalog_path) as cat:
            return [link_patient(row,cat,policy) for row in batch]
    try:
        with open(tmp,'w',encoding='utf-8') as f, ThreadPoolExecutor(max_workers=policy['workers']) as pool:
            tmp.chmod(0o600);it=iter(read_jsonl(source))
            while True:
                batches=[list(islice(it,64)) for _ in range(policy['workers'])]
                batches=[b for b in batches if b]
                if not batches:break
                for batch in pool.map(batch_map,batches):
                    for row in batch:
                        counts.update(m['status'] for m in row['terminology']['mappings']);n+=1
                        f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    with Catalog(catalog_path) as cat:
        report={'records':n,'mapping_status':dict(counts),'catalog_fingerprint':cat.fingerprint,
                'clinical_accuracy':None,'gpu_used':False,'policy':policy}
    write_json(out.with_suffix(out.suffix+'.report.json'),report)
    return report


def prepare_requests(source,destination):
    out=Path(destination);out.parent.mkdir(parents=True,exist_ok=True)
    if out.exists() or out.resolve()==Path(source).resolve():raise ValueError('Request output already exists')
    n=0
    with out.open('w',encoding='utf-8') as f:
        out.chmod(0o600)
        for row in read_jsonl(source):
            for m in row['terminology']['mappings']:
                if m['status'] in {'needs_review','ambiguous'} and m['candidates']:
                    f.write(json.dumps(mapping_request(m),ensure_ascii=False)+'\n');n+=1
    return {'requests':n,'scope':'unresolved fact/system occurrences; no full-article reprompt'}


def review_template(source,destination):
    out=Path(destination);out.parent.mkdir(parents=True,exist_ok=True)
    if out.exists():raise ValueError('Review template destination already exists')
    n=0
    with out.open('w',encoding='utf-8') as f:
        out.chmod(0o600)
        for row in read_jsonl(source):
            for m in row['terminology']['mappings']:
                f.write(json.dumps({'mapping_id':m['mapping_id'],'request_digest':m['request_digest'],
                    'reviewed':False,'reviewer':None,'reviewed_at':None,'decision':'abstain',
                    'candidate_id':None,'relation':None,'rationale':None,
                    'context':mapping_request(m),'model_proposals':m['model_proposals']},ensure_ascii=False)+'\n');n+=1
    return {'review_rows':n,'approved_by_default':False}


def apply_decisions(source,destination,catalog_path,proposals=None,reviews=None):
    from .coding_api import validate_selection
    if not proposals and not reviews:raise ValueError('Supply proposals and/or human reviews')
    decisions={};human={}
    for path,target in ((proposals,decisions),(reviews,human)):
        if path:
            for d in read_jsonl(path):
                if target is human and not d.get('reviewed'):continue
                if d['mapping_id'] in target:raise ValueError('Duplicate decision for a mapping')
                target[d['mapping_id']]=d
    used=set();n=0;counts=Counter();out=Path(destination)
    if out.exists() or out.resolve()==Path(source).resolve():raise ValueError('Use a new output for adjudicated mappings')
    out.parent.mkdir(parents=True,exist_ok=True);tmp=out.with_suffix(out.suffix+'.tmp')
    try:
        with Catalog(catalog_path) as cat,tmp.open('w',encoding='utf-8') as f:
            tmp.chmod(0o600)
            for row in read_jsonl(source):
                validate_coded_patient(row,cat)
                for m in row['terminology']['mappings']:
                    for d,is_human in ((decisions.get(m['mapping_id']),False),(human.get(m['mapping_id']),True)):
                        if not d:continue
                        used.add(m['mapping_id'])
                        if d.get('request_digest')!=m['request_digest']:raise ValueError('Stale/mismatched decision request')
                        if is_human:
                            selection={k:d.get(k) for k in ('decision','candidate_id','relation','rationale')}
                            if not all(isinstance(d.get(k),str) and d[k].strip() for k in ('reviewer','reviewed_at','rationale')):
                                raise ValueError('Human approval requires reviewer, time and rationale')
                        else:
                            m['model_proposals'].append(d)
                            if d.get('status')!='ok':continue
                            selection=d['selection']
                        validate_selection(selection,mapping_request(m))
                        if not is_human and m['status'] in {'exact_unique','reviewed','reviewed_unresolved'}:
                            continue  # audit a proposal without overwriting an accepted/human decision
                        m['selected_candidate_id']=selection['candidate_id'];m['relation']=selection['relation']
                        if is_human:
                            m['status']='reviewed' if selection['decision']=='select' else 'reviewed_unresolved'
                            m['mapping_review']={k:v for k,v in d.items() if k not in {'context','model_proposals'}}
                        elif m['status'] not in {'exact_unique','reviewed','reviewed_unresolved'}:
                            m['status']='model_proposed' if selection['decision']=='select' else 'model_abstained'
                    counts[m['status']]+=1
                validate_coded_patient(row,cat)
                f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n');n+=1
        if (set(decisions)|set(human))-used:raise ValueError('Decision references absent mapping; no output published')
        tmp.replace(out)
    finally:tmp.unlink(missing_ok=True)
    return {'records':n,'mapping_status':dict(counts),'unreviewed_model_proposals_in_default_cohorts':False}
