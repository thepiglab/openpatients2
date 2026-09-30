"""Version-pinned local terminology catalog. Imports do not require a GPU/network.

No terminology content is distributed with this package. A catalog is immutable
once built: release metadata and input SHA256s define its identity. Search returns
candidates, not clinically adjudicated mappings. No inverse crosswalk inference.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import yaml
from defusedxml import ElementTree as ET

from .provenance import json_digest

ICD10CM = 'http://hl7.org/fhir/sid/icd-10-cm'
SNOMED = 'http://snomed.info/sct'
LOINC = 'http://loinc.org'
FORMAT = 'openpatients2.terminology/1'


def normalize_term(text: str) -> str:
    # Keep punctuation, numbers, laterality and +/-: these can change meaning.
    return ' '.join(text.casefold().split())


def file_sha(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def _rows(path, delimiter=',', required=()):
    with open(path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        if not set(required).issubset(reader.fieldnames or []):
            raise ValueError(f'Missing terminology columns at {path}: {sorted(set(required)-set(reader.fieldnames or []))}')
        yield from reader


def _bool(text):
    if str(text).lower() not in {'true', 'false', '1', '0'}:
        raise ValueError(f'Expected explicit boolean, got {text!r}')
    return str(text).lower() in {'true', '1'}


class Catalog:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise ValueError(f'Terminology catalog does not exist: {path}')
        self.db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)
        self.db.row_factory = sqlite3.Row
        self.manifest = json.loads(self.db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
        if self.manifest['format'] != FORMAT:
            raise ValueError('Unsupported terminology catalog')
        self.fingerprint = self.manifest['fingerprint']
        self.releases = {r['system']: r['version'] for r in self.manifest['releases']}

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @staticmethod
    def _concept(row):
        if row is None:
            return None
        return {'system': row['system'], 'version': row['version'], 'code': row['code'],
                'display': row['display'], 'active': bool(row['active']),
                'selectable': bool(row['selectable']), 'attributes': json.loads(row['attributes'])}

    def lookup(self, system, code, version=None):
        version = version or self.releases.get(system)
        return self._concept(self.db.execute('SELECT * FROM terms WHERE system=? AND version=? AND code=?',
                                             (system, version, code)).fetchone())

    @lru_cache(maxsize=16384)
    def search(self, system: str, text: str, limit: int = 12) -> tuple[dict, ...]:
        if not 1 <= limit <= 100:
            raise ValueError('Candidate limit must be 1..100')
        version = self.releases.get(system)
        if version is None:
            return ()
        # Retrieve limit+1 exact matches: caller must see truncation, never infer
        # uniqueness from a top-1 display window.
        norm = normalize_term(text)
        exact = self.db.execute('''SELECT DISTINCT t.* FROM terms t JOIN aliases a
          ON t.system=a.system AND t.version=a.version AND t.code=a.code
          WHERE a.system=? AND a.version=? AND a.normalized=? AND t.active=1
          ORDER BY t.code LIMIT ?''', (system, version, norm, limit+1)).fetchall()
        result = []
        for row in exact:
            item = self._concept(row)
            item.update(retrieval='exact_alias', score=1.0, exact_truncated=len(exact)>limit)
            result.append(item)
        if len(result) >= limit:
            return tuple(result[:limit])
        words = list(dict.fromkeys(re.findall(r'\w+', norm, re.UNICODE)))[:24]
        if not words:
            return tuple(result)
        query = ' OR '.join('"'+w.replace('"', '""')+'"' for w in words)
        found = self.db.execute('''SELECT t.*,bm25(term_fts) AS rank FROM term_fts
          JOIN terms t ON t.rowid=term_fts.rowid WHERE term_fts MATCH ?
          AND t.system=? AND t.version=? AND t.active=1 ORDER BY rank,t.code LIMIT ?''',
          (query, system, version, limit*4)).fetchall()
        seen = {x['code'] for x in result}
        for row in found:
            if row['code'] in seen:
                continue
            item = self._concept(row)
            item.update(retrieval='lexical_candidate', score=float(-row['rank']), exact_truncated=False)
            result.append(item); seen.add(row['code'])
            if len(result) >= limit:
                break
        return tuple(result)

    def ancestors(self, system, code, version=None):
        version = version or self.releases.get(system)
        return [r[0] for r in self.db.execute('''WITH RECURSIVE a(code) AS (
          SELECT parent FROM edges WHERE system=? AND version=? AND child=?
          UNION SELECT e.parent FROM edges e JOIN a ON e.child=a.code
          WHERE e.system=? AND e.version=?) SELECT code FROM a ORDER BY code''',
          (system, version, code, system, version))]

    def crosswalk_candidates(self, snomed_code):
        return [json.loads(r[0]) for r in self.db.execute(
            'SELECT payload FROM crossmaps WHERE source_code=? ORDER BY map_group,map_priority', (snomed_code,))]


def _insert(db, system, version, code, display, active=True, selectable=True, attributes=None, aliases=()):
    if not all(isinstance(x, str) and x.strip() for x in (system, version, code, display)):
        raise ValueError('Terminology identifiers and displays must be nonempty strings')
    db.execute('INSERT INTO terms VALUES(?,?,?,?,?,?,?)',
               (system,version,code,display,int(active),int(selectable),json.dumps(attributes or {},ensure_ascii=False)))
    for alias in dict.fromkeys([display,*aliases]):
        if alias and alias.strip():
            db.execute('INSERT OR IGNORE INTO aliases VALUES(?,?,?,?,?)',
                       (system,version,code,alias,normalize_term(alias)))


def import_icd(db, r):
    """CDC tabular XML only. Preserve nested hierarchy and inherited instructions.

    Header status is NOT certification of billability. Coding convention evaluation,
    sequencing, seventh-character completion and claims compliance are not performed.
    """
    root = ET.parse(r['path']).getroot()
    for element in root.iter():
        element.tag = element.tag.rsplit('}',1)[-1]
    count = 0
    def walk(node, parent=None, inherited=()):
        nonlocal count
        own = []
        for tag in ('includes','excludes1','excludes2','codeFirst','useAdditionalCode','codeAlso','notes','sevenChrNote','sevenChrDef'):
            for elem in node.findall(tag):
                text = ' '.join(' '.join(elem.itertext()).split())
                if text:
                    own.append({'kind':tag, 'text':text, 'source_node':node.findtext('name') or node.tag})
        rules = [*inherited,*own]
        code = None
        if node.tag == 'diag':
            code, display = (node.findtext('name') or '').strip(), (node.findtext('desc') or '').strip()
            if not re.fullmatch(r'[A-Z][A-Z0-9]{2}(?:\.[A-Z0-9]{1,4})?', code):
                raise ValueError(f'Unexpected ICD-10-CM code in tabular XML: {code!r}')
            aliases = [n.text for n in node.findall('./inclusionTerm/note') if n.text]
            _insert(db,ICD10CM,r['version'],code,display,selectable=not bool(node.findall('diag')),
                    attributes={'instructions':rules,'research_mapping_only':True}, aliases=aliases)
            count += 1
            if parent:
                db.execute('INSERT INTO edges VALUES(?,?,?,?,?)',(ICD10CM,r['version'],code,parent,'tabular_parent'))
        for child in node:
            if child.tag in {'chapter','section','diag'}:
                walk(child,code or parent,rules)
    walk(root)
    if not count:
        raise ValueError('No diagnosis elements found; provide ICD-10-CM tabular XML, not Index/PCS XML')
    return count


def import_loinc(db,r):
    count = 0
    required=('LOINC_NUM','COMPONENT','PROPERTY','TIME_ASPCT','SYSTEM','SCALE_TYP','METHOD_TYP','STATUS')
    for row in _rows(r['path'],required=required):
        attrs={k:row.get(k,'') for k in (*required[1:],'CLASS','CLASSTYPE','EXAMPLE_UNITS','EXAMPLE_UCUM_UNITS')}
        display=row.get('LONG_COMMON_NAME') or ':'.join(row.get(k,'') for k in required[1:7])
        # Relatednames2 is a search-keyword field, NOT a list of exact synonyms.
        aliases=[row.get('SHORTNAME',''), ':'.join(row.get(k,'') for k in required[1:7])]
        attrs['search_keywords']=row.get('RELATEDNAMES2','')
        _insert(db,LOINC,r['version'],row['LOINC_NUM'],display,active=row['STATUS'].upper()=='ACTIVE',
                attributes=attrs,aliases=aliases)
        count+=1
    return count


def import_snomed(db,r):
    concepts = {}
    for row in _rows(r['concepts'],delimiter='\t',required=('id','effectiveTime','active','moduleId','definitionStatusId')):
        if row['id'] in concepts:
            raise ValueError('Use RF2 Snapshot files, not Full history: duplicate concept ID')
        concepts[row['id']]=row
    # Acceptable/preferred descriptions are selected through the declared dialect
    # refset when supplied. Without one the FSN is the display, not a guessed PT.
    lang = {}
    if r.get('language'):
        refset = r.get('dialect_refset')
        if not refset:
            raise ValueError('language file requires explicit dialect_refset')
        for row in _rows(r['language'],delimiter='\t',required=('active','refsetId','referencedComponentId','acceptabilityId')):
            if row['active']=='1' and row['refsetId']==refset:
                lang[row['referencedComponentId']]=row['acceptabilityId']
    descs = {}
    seen=set()
    for row in _rows(r['descriptions'],delimiter='\t',required=('id','active','conceptId','languageCode','typeId','term')):
        if row['id'] in seen:
            raise ValueError('Use RF2 Snapshot descriptions, not Full history')
        seen.add(row['id'])
        if row['active']=='1' and row['languageCode']=='en':
            descs.setdefault(row['conceptId'],[]).append(row)
    for code,c in concepts.items():
        ds=descs.get(code,[])
        fsns=[d['term'] for d in ds if d['typeId']=='900000000000003001']
        if not fsns:
            if c['active']=='1':
                raise ValueError(f'Active SNOMED concept lacks an English FSN: {code}; require complete US/International Snapshot')
            display=f'[inactive concept {code}; no active English description]'
        else:
            display=sorted(fsns)[0]
        preferred=[d['term'] for d in ds if d['typeId']=='900000000000013009' and lang.get(d['id'])=='900000000000548007']
        m=re.search(r'\(([^()]+)\)$',display)
        synonyms=[d['term'] for d in ds if d['typeId']=='900000000000013009' and (not r.get('language') or d['id'] in lang)]
        if m:
            synonyms.append(display[:m.start()].strip())
        _insert(db,SNOMED,r['version'],code,sorted(preferred)[0] if preferred else display,
                active=c['active']=='1',attributes={'fsn':display,'semantic_tag':m[1] if m else None,
                   'module_id':c['moduleId'],'effective_time':c['effectiveTime'],
                   'dialect_refset':r.get('dialect_refset'),'display_type':'preferred' if preferred else 'fsn'},aliases=synonyms)
    if r.get('relationships'):
        seen=set()
        for row in _rows(r['relationships'],delimiter='\t',required=('id','active','sourceId','destinationId','typeId','characteristicTypeId')):
            if row['id'] in seen:
                raise ValueError('Use RF2 Snapshot relationships, not Full history')
            seen.add(row['id'])
            if row['active']=='1' and row['typeId']=='116680003' and row['characteristicTypeId']=='900000000000011006':
                if row['sourceId'] not in concepts or row['destinationId'] not in concepts:
                    raise ValueError('Hierarchy references missing concepts: load a complete edition')
                if concepts[row['sourceId']]['active']=='1' and concepts[row['destinationId']]['active']=='1':
                    db.execute('INSERT OR IGNORE INTO edges VALUES(?,?,?,?,?)',(SNOMED,r['version'],row['sourceId'],row['destinationId'],'inferred_is_a'))
    return len(concepts)


def import_generic(db,r):
    """Local/project vocabulary, not a shortcut for relabeling official codes."""
    if r['system'] in {ICD10CM,SNOMED,LOINC}:
        raise ValueError('Official systems require their dedicated importers')
    n=0
    for row in _rows(r['path'],required=('code','display','active')):
        attrs=json.loads(row.get('attributes') or '{}')
        aliases=json.loads(row.get('aliases') or '[]')
        _insert(db,r['system'],r['version'],row['code'],row['display'],active=_bool(row['active']),
                attributes=attrs,aliases=aliases)
        if row.get('parent'):
            db.execute('INSERT INTO edges VALUES(?,?,?,?,?)',(r['system'],r['version'],row['code'],row['parent'],'local_is_a'))
        n+=1
    return n


def build_catalog(config_path: str, destination: str) -> dict:
    config=yaml.safe_load(Path(config_path).read_text()) or {}
    if set(config)-{'releases','snomed_icd10_maps'}:
        raise ValueError('Unknown terminology catalog configuration fields')
    releases=config.get('releases') or []
    if not releases:
        raise ValueError('At least one explicit release is required')
    systems={'icd10cm':ICD10CM,'snomed':SNOMED,'loinc':LOINC}
    prepared=[]; used=set()
    for raw in releases:
        r=dict(raw)
        if not r.get('version') or str(r['version']).casefold() in {'main','latest','unversioned'}:
            raise ValueError('Every terminology release must have a pinned version, never latest')
        r['version']=str(r['version'])
        r['system']=systems.get(r.get('kind'),r.get('system'))
        if not r['system'] or r['system'] in used:
            raise ValueError('Exactly one release per system in a catalog; do not mix editions')
        if r.get('kind') not in {'icd10cm','snomed','loinc','local'}:
            raise ValueError('Unsupported terminology kind')
        if r.get('kind') in {'snomed','loinc'} and r.get('license_acknowledged') is not True:
            raise ValueError('SNOMED/LOINC require explicit license_acknowledged: true for your local licensed copy')
        if not r.get('source_url'):
            raise ValueError('A release source_url is required for provenance')
        paths=[k for k in ('path','concepts','descriptions','relationships','language') if r.get(k)]
        if r['kind']=='snomed' and not {'concepts','descriptions'}.issubset(paths):
            raise ValueError('SNOMED requires concept and description Snapshot paths')
        if r['kind']!='snomed' and 'path' not in paths:
            raise ValueError('Release path is required')
        r['files']={k:{'path':str(Path(r[k]).resolve()),'sha256':file_sha(r[k])} for k in paths}
        for k,expected in r.get('expected_sha256',{}).items():
            if k not in r['files'] or r['files'][k]['sha256']!=expected:
                raise ValueError('Terminology file SHA256 does not match expected hash')
        used.add(r['system']);prepared.append(r)
    target=Path(destination);target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        raise ValueError('Catalog destination already exists; builds are immutable')
    temp=target.with_suffix(target.suffix+'.building')
    if temp.exists():
        raise ValueError('Catalog build-in-progress file exists')
    db=sqlite3.connect(temp);temp.chmod(0o600)
    try:
        db.executescript('''PRAGMA foreign_keys=ON;
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE terms(system TEXT,version TEXT,code TEXT,display TEXT NOT NULL,active INTEGER NOT NULL,
          selectable INTEGER NOT NULL,attributes TEXT NOT NULL,PRIMARY KEY(system,version,code));
        CREATE TABLE aliases(system TEXT,version TEXT,code TEXT,alias TEXT,normalized TEXT,
          UNIQUE(system,version,code,alias),FOREIGN KEY(system,version,code) REFERENCES terms(system,version,code));
        CREATE INDEX alias_exact ON aliases(system,version,normalized);
        CREATE TABLE edges(system TEXT,version TEXT,child TEXT,parent TEXT,relation TEXT,
          PRIMARY KEY(system,version,child,parent));
        CREATE INDEX edges_child ON edges(system,version,child);
        CREATE TABLE crossmaps(source_code TEXT,map_group INTEGER,map_priority INTEGER,payload TEXT);
        CREATE INDEX map_source ON crossmaps(source_code);
        CREATE VIRTUAL TABLE term_fts USING fts5(text,tokenize='unicode61');''')
        for r in prepared:
            r['concept_count']={'icd10cm':import_icd,'loinc':import_loinc,'snomed':import_snomed,'local':import_generic}[r['kind']](db,r)
            if not r['concept_count']:
                raise ValueError('Empty terminology release')
        # Validate imported edges before publishing the catalog.
        missing=db.execute('''SELECT e.child,e.parent FROM edges e LEFT JOIN terms c
          ON (c.system=e.system AND c.version=e.version AND c.code=e.child)
          LEFT JOIN terms p ON (p.system=e.system AND p.version=e.version AND p.code=e.parent)
          WHERE c.code IS NULL OR p.code IS NULL LIMIT 1''').fetchone()
        if missing:
            raise ValueError(f'Hierarchy has dangling concept: {tuple(missing)}')
        # A malformed local or imported hierarchy must not turn a concept into its
        # own descendant. Kahn's algorithm is linear in imported edges/nodes.
        from collections import defaultdict, deque
        adjacency=defaultdict(list);indegree={}
        for system,version,child,parent in db.execute('SELECT system,version,child,parent FROM edges'):
            a=(system,version,child);b=(system,version,parent)
            adjacency[a].append(b);indegree.setdefault(a,0);indegree[b]=indegree.get(b,0)+1
        queue=deque(n for n,d in indegree.items() if d==0);visited=0
        while queue:
            n=queue.popleft();visited+=1
            for parent in adjacency[n]:
                indegree[parent]-=1
                if indegree[parent]==0:queue.append(parent)
        if visited!=len(indegree):raise ValueError('Terminology hierarchy contains a cycle')
        # FTS rows intentionally separate exact aliases from retrieval-only keywords.
        db.execute('''INSERT INTO term_fts(rowid,text) SELECT t.rowid,
          t.display || ' ' || COALESCE((SELECT group_concat(a.alias,' ') FROM aliases a
          WHERE a.system=t.system AND a.version=t.version AND a.code=t.code),'') || ' ' ||
          COALESCE(json_extract(t.attributes,'$.search_keywords'),'') FROM terms t''')
        maps=[]
        for m in config.get('snomed_icd10_maps',[]):
            if {SNOMED,ICD10CM}-used:
                raise ValueError('Crossmap requires both source and target releases')
            pinned={r['system']:r['version'] for r in prepared}
            if m.get('source_version')!=pinned[SNOMED] or m.get('target_version')!=pinned[ICD10CM]:
                raise ValueError('Crossmap release versions do not match catalog')
            map_sha=file_sha(m['path'])
            for row in _rows(m['path'],delimiter='\t',required=('active','refsetId','referencedComponentId','mapGroup','mapPriority','mapRule','mapAdvice','mapTarget')):
                if row['active']!='1' or row['refsetId']!='6011000124106':
                    continue
                payload={**row,'source_version':m['source_version'],'target_version':m['target_version'],
                         'status':'rule_not_evaluated','direction':'SNOMED_CT_to_ICD10CM',
                         'map_source_sha256':map_sha}
                # Rules, groups and priority are retained. NEVER treat this as equivalence.
                db.execute('INSERT INTO crossmaps VALUES(?,?,?,?)',(row['referencedComponentId'],int(row['mapGroup']),int(row['mapPriority']),json.dumps(payload)))
            maps.append({**m,'sha256':file_sha(m['path'])})
        manifest={'format':FORMAT,'releases':prepared,'crossmaps':maps,
                  'terms':db.execute('SELECT count(*) FROM terms').fetchone()[0],
                  'edges':db.execute('SELECT count(*) FROM edges').fetchone()[0],
                  'mapping_validity':'catalog existence is not clinical correctness; no billing compliance asserted'}
        manifest['fingerprint']=json_digest(manifest)
        db.execute('INSERT INTO metadata VALUES(?,?)',('manifest',json.dumps(manifest)))
        db.commit();db.close();temp.replace(target)
        return manifest
    except BaseException:
        db.close();temp.unlink(missing_ok=True);raise
