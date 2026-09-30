"""Research case index. Parameterized cohort predicates; no model-generated SQL.

One source record is not necessarily a distinct real-world person. This schema is
informed by clinical standards, but is not an OMOP/FHIR-conformant implementation.
"""
from __future__ import annotations

import csv
import hashlib
import json
import random
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

import yaml

from .data import read_jsonl, write_json, digest_text, normalize, case_duplicate_key
from .validation import validate
from .consistency import consistency_findings
from .schemas import TASK_MODELS


DOMAINS = {task: (task, "items") for task in TASK_MODELS if task not in {"case_context", "oncology"}}
DOMAINS.update({"oncology_tumors": ("oncology", "tumors"),
                "oncology_biomarkers": ("oncology", "biomarkers"),
                "oncology_treatments": ("oncology", "treatments")})


def fields_for(domain: str) -> set[str]:
    task, collection = DOMAINS[domain]
    schema = TASK_MODELS[task].model_json_schema()
    item = schema["properties"][collection]["items"]
    definition = schema["$defs"][item["$ref"].split("/")[-1]]
    return set(definition["properties"]) | {"time.text", "time.relation", "time.anchor", "time.date_iso"}


def load_terms(path: str | None) -> tuple[dict, str | None]:
    mapping = defaultdict(list)
    if not path:
        return mapping, None
    sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("reviewed", "").lower() != "true":
                continue
            if row["domain"] not in DOMAINS or row["field"] not in fields_for(row["domain"]):
                raise ValueError("Invalid domain/field in terminology dictionary")
            from .vocabulary import ICD10CM, SNOMED, LOINC
            if row['system'] in {ICD10CM,SNOMED,LOINC}:
                raise ValueError('Official codes require code-link with a pinned catalog, not a free-text alias CSV')
            key = (row["domain"], row["field"], row["source_text"].strip().casefold())
            mapping[key].append({k: row[k] for k in ["system", "code", "label", "version"]})
    return mapping, sha


def build_index(source: str, destination: str, terms_path: str | None = None, catalog_path: str | None = None) -> dict:
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValueError("Index already exists; choose a new destination to preserve provenance")
    terms, term_sha = load_terms(terms_path)
    from .vocabulary import Catalog
    from .coding import accepted_mappings
    catalog = Catalog(catalog_path) if catalog_path else None
    db = sqlite3.connect(target)
    target.chmod(0o600)
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
    CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE cases(record_id TEXT PRIMARY KEY,source_kind TEXT NOT NULL,source_hash TEXT NOT NULL,
      source_id TEXT NOT NULL,dataset_revision TEXT NOT NULL,eligible_context INTEGER NOT NULL,
      complete INTEGER NOT NULL,context TEXT NOT NULL,payload TEXT NOT NULL,duplicate_key TEXT NOT NULL);
    CREATE TABLE source_articles(record_id TEXT PRIMARY KEY REFERENCES cases(record_id),
      pmid TEXT,pmcid TEXT,doi TEXT,source_url TEXT,media_status TEXT,has_article_image_urls INTEGER);
    CREATE INDEX source_pubmed ON source_articles(pmid);
    CREATE INDEX source_pmc ON source_articles(pmcid);
    CREATE TABLE facts(fact_id INTEGER PRIMARY KEY,record_id TEXT NOT NULL REFERENCES cases(record_id),
      domain TEXT NOT NULL,ordinal INTEGER NOT NULL,tumor_ref TEXT,payload TEXT NOT NULL);
    CREATE INDEX facts_lookup ON facts(record_id,domain);
    CREATE INDEX fact_tumors ON facts(record_id,tumor_ref,domain);
    CREATE TABLE evidence(fact_id INTEGER NOT NULL REFERENCES facts(fact_id),quote TEXT NOT NULL,
      spans TEXT NOT NULL,path TEXT NOT NULL);
    CREATE TABLE concepts(fact_id INTEGER NOT NULL REFERENCES facts(fact_id),field TEXT NOT NULL,
      system TEXT NOT NULL,code TEXT NOT NULL,label TEXT NOT NULL,version TEXT NOT NULL,status TEXT NOT NULL);
    CREATE INDEX concepts_lookup ON concepts(fact_id,field,system,code,status);
    """)
    db.executescript("""
      CREATE TABLE concept_annotations(fact_id INTEGER NOT NULL REFERENCES facts(fact_id),mapping_id TEXT NOT NULL,
        field TEXT NOT NULL,system TEXT NOT NULL,version TEXT,code TEXT,status TEXT NOT NULL,payload TEXT NOT NULL);
      CREATE TABLE concept_ancestors(fact_id INTEGER NOT NULL REFERENCES facts(fact_id),field TEXT NOT NULL,
        system TEXT NOT NULL,version TEXT NOT NULL,code TEXT NOT NULL,ancestor TEXT NOT NULL);
      CREATE INDEX ancestors_lookup ON concept_ancestors(system,version,ancestor,fact_id,field);
    """)
    count = facts = 0
    try:
        for row in read_jsonl(source):
            record = normalize(row["source"])
            from .articles import recheck_license
            article=record.get('article_source') or record.get('original_row',{}).get('article_source')
            if article and not recheck_license(article.get('license') or {'allowed':False})['allowed']:
                raise ValueError('Article license requires review before indexing: '+article['article_id'])
            row["source"] = record
            sections = row["sections"]
            accepted = {}
            annotations = defaultdict(list)
            if row.get("terminology"):
                if catalog is None:
                    raise ValueError("A coded export requires --catalog to revalidate mappings before indexing")
                for m, c in accepted_mappings(row, catalog):
                    accepted[m["mapping_id"]] = (m, c)
                for m in row["terminology"]["mappings"]:
                    annotations[(m["domain"], m["ordinal"])].append(m)
            context = sections.get("case_context") or {}
            full = row["complete_for_scope"] and set(row["expected_tasks"]) == set(TASK_MODELS)
            if digest_text(record["text"]) != record["source_hash"]:
                raise ValueError("Source digest mismatch during indexing")
            for task, section in sections.items():
                if section is not None:
                    checked = validate(task, section, record["text"])
                    if not checked.valid:
                        raise ValueError(f"Source/structural validation failed during indexing: {record['record_id']}:{task}")
                    # Recompute offsets rather than trusting an edited export's stored spans.
                    row["quality"][task]["checks"]["evidence"] = checked.evidence
                    full = full and section["coverage"] != "limited"
            full = full and not any(x["review_required"] for x in consistency_findings(sections))
            eligible = (context.get("case_kind") in {"clinical_case", "clinical_record"}
                        and context.get("species") == "human"
                        and context.get("multiple_index_patients") is False
                        and context.get("subject_state") != "cadaver")
            # Reasoning is an audit/distillation artifact, never a clinical index field.
            indexed_payload = {k: v for k, v in row.items() if k not in {"generations", "terminology"}}
            db.execute("INSERT INTO cases VALUES(?,?,?,?,?,?,?,?,?,?)", (
                record["record_id"], record["source_kind"], record["source_hash"], record.get("source_id", record["record_id"]),
                record["dataset_revision"], int(eligible), int(full), json.dumps(context), json.dumps(indexed_payload, ensure_ascii=False),case_duplicate_key(record)))
            provenance = record.get("provenance", {})
            article = provenance.get("article", {})
            multimedia = record.get("multimedia", {})
            has_images = multimedia.get("has_image_urls")
            db.execute("INSERT INTO source_articles VALUES(?,?,?,?,?,?,?)", (
                record["record_id"], article.get("pmid"), article.get("pmcid"), article.get("doi"),
                provenance.get("source_url"), multimedia.get("status"),
                int(has_images) if has_images is not None else None))
            count += 1
            for domain, (task, collection) in DOMAINS.items():
                section = sections.get(task)
                if not section:
                    continue
                evidence = row["quality"].get(task, {}).get("checks", {}).get("evidence", [])
                for ordinal, fact in enumerate(section[collection]):
                    cursor = db.execute("INSERT INTO facts(record_id,domain,ordinal,tumor_ref,payload) VALUES(?,?,?,?,?)", (
                        record["record_id"], domain, ordinal, fact.get("tumor_ref"), json.dumps(fact, ensure_ascii=False)))
                    fact_id = cursor.lastrowid
                    facts += 1
                    prefix = f"/{collection}/{ordinal}/evidence/"
                    for ev in evidence:
                        if ev["path"].startswith(prefix):
                            db.execute("INSERT INTO evidence VALUES(?,?,?,?)", (fact_id, ev["quote"], json.dumps(ev["spans"]), ev["path"]))
                    for m in annotations.get((domain, ordinal), []):
                        c = next((c for c in m["candidates"] if c["candidate_id"] == m["selected_candidate_id"]), None)
                        # The index keeps a compact mapping audit, not model reasoning.
                        audit = {k: m[k] for k in ("mapping_id", "field", "system", "version", "status", "relation",
                                 "source_text", "fact_hash", "catalog_fingerprint", "request_digest", "mapping_review")}
                        audit["candidate_codes"] = [{k: x[k] for k in ("candidate_id", "code", "display", "issues")} for x in m["candidates"]]
                        db.execute("INSERT INTO concept_annotations VALUES(?,?,?,?,?,?,?,?)", (fact_id,m["mapping_id"],
                            m["field"],m["system"],m["version"],c["code"] if c else None,m["status"],json.dumps(audit,ensure_ascii=False)))
                        if m["mapping_id"] in accepted:
                            c = accepted[m["mapping_id"]][1]
                            db.execute("INSERT INTO concepts VALUES(?,?,?,?,?,?,?)", (fact_id,m["field"],c["system"],c["code"],c["display"],c["version"],"resolved"))
                            for ancestor in catalog.ancestors(c["system"],c["code"],c["version"]):
                                db.execute("INSERT INTO concept_ancestors VALUES(?,?,?,?,?,?)",(fact_id,m["field"],c["system"],c["version"],c["code"],ancestor))
                    for field, value in fact.items():
                        if not isinstance(value, str):
                            continue
                        candidates = terms.get((domain, field, value.strip().casefold()), [])
                        unique = {(x["system"], x["code"], x["version"]): x for x in candidates}
                        for concept in unique.values():
                            db.execute("INSERT INTO concepts VALUES(?,?,?,?,?,?,?)", (
                                fact_id, field, concept["system"], concept["code"], concept["label"], concept["version"],
                                "resolved" if len(unique) == 1 else "ambiguous"))
            if count % 500 == 0:
                db.commit()
        for domain in DOMAINS:
            # Domain names come exclusively from this module, never user input.
            db.execute(f"CREATE VIEW {domain} AS SELECT * FROM facts WHERE domain='{domain}'")
        metadata = {"source_file": str(Path(source).resolve()), "records": count, "facts": facts,
                    "terminology_catalog_fingerprint": catalog.fingerprint if catalog else None,
                    "terms_sha256": term_sha, "source_unit": "case records, not verified unique persons",
                    "conformance": "custom research index; not FHIR or OMOP conformance"}
        for k, v in metadata.items():
            db.execute("INSERT INTO metadata VALUES(?,?)", (k, json.dumps(v)))
        db.commit()
        return metadata
    except BaseException:
        db.close()
        target.unlink(missing_ok=True)
        raise
    finally:
        db.close()
        if catalog:
            catalog.close()


def compile_cohort(spec: dict) -> tuple[str, list]:
    allowed = {"name", "source_kinds", "include_incomplete", "include_nonclinical_context",
               "criteria", "sample_size", "seed", "deduplicate_exact_text"}
    if set(spec) - allowed:
        raise ValueError(f"Unknown cohort options: {set(spec) - allowed}")
    criteria = spec.get("criteria", [])
    if not criteria:
        raise ValueError("At least one explicit criterion is required")
    params: list = []
    sources = spec.get("source_kinds", ["published_case_summary", "ehr_summary"])
    if not sources or not isinstance(sources, list) or not all(isinstance(x, str) for x in sources):
        raise ValueError("source_kinds must be a nonempty list")
    where = ["c.source_kind IN (" + ",".join("?" for _ in sources) + ")"]
    params.extend(sources)
    if not spec.get("include_incomplete", False):
        where.append("c.complete=1")
    if not spec.get("include_nonclinical_context", False):
        where.append("c.eligible_context=1")
    joins = []
    aliases = {}
    selections = ["c.record_id", "c.source_hash", "c.source_kind", "c.duplicate_key"]
    for i, criterion in enumerate(criteria):
        if set(criterion) - {"id", "domain", "where", "linked_to"}:
            raise ValueError("Unknown criterion option")
        cid, domain = criterion.get("id"), criterion.get("domain")
        if not isinstance(cid, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", cid) or cid in aliases:
            raise ValueError("Criterion IDs must be unique safe identifiers")
        if domain not in DOMAINS:
            raise ValueError(f"Unknown fact domain: {domain}")
        alias = f"f{i}"
        joins.append(f"JOIN facts {alias} ON {alias}.record_id=c.record_id")
        where.append(f"{alias}.domain=?")
        params.append(domain)
        selections.append(f"{alias}.fact_id AS {cid}_fact_id")
        clauses = list(criterion.get("where", []))
        specified = {x.get("field") for x in clauses}
        # Most inclusion queries mean affirmed facts about the index patient. Override
        # explicitly for family history or explicit negative assertions; missing != absent.
        if "subject" not in specified:
            clauses.append({"field": "subject", "op": "eq", "value": "index_patient"})
        if "assertion" not in specified:
            clauses.append({"field": "assertion", "op": "eq", "value": "present"})
        for predicate in clauses:
            if set(predicate) - {"field", "op", "value"}:
                raise ValueError("Unknown predicate option")
            field, op, value = predicate.get("field"), predicate.get("op", "eq"), predicate.get("value")
            if field not in fields_for(domain):
                raise ValueError(f"Field {field!r} is not allowed in {domain}")
            expression = f"json_extract({alias}.payload, '$.{field}')"
            if op in {"concept", "concept_descendant"}:
                if (not isinstance(value, dict) or set(value)-{"system","code","version"}
                    or not {"system","code"}.issubset(value)
                    or not all(isinstance(x,str) and x for x in value.values())):
                    raise ValueError("concept requires system/code and an optional pinned version")
                if op == "concept_descendant" and "version" not in value:
                    raise ValueError("Hierarchy queries require a pinned terminology version")
                # A disease code can legitimately annotate a negated/family fact.
                # Do not turn that annotation into affirmative index-patient eligibility.
                explicit = {x.get("field") for x in clauses}
                if "subject" not in explicit:
                    where.append(f"json_extract({alias}.payload,'$.subject')='index_patient'")
                if "assertion" not in explicit:
                    where.append(f"json_extract({alias}.payload,'$.assertion')='present'")
                version = " AND co.version=?" if "version" in value else ""
                direct=f"EXISTS (SELECT 1 FROM concepts co WHERE co.fact_id={alias}.fact_id AND co.field=? AND co.system=? AND co.code=? AND co.status='resolved'{version})"
                params.extend([field,value["system"],value["code"]])
                if "version" in value:params.append(value["version"])
                if op == "concept_descendant":
                    inherited=f"EXISTS (SELECT 1 FROM concept_ancestors ca WHERE ca.fact_id={alias}.fact_id AND ca.field=? AND ca.system=? AND ca.version=? AND ca.ancestor=?)"
                    params.extend([field,value["system"],value["version"],value["code"]])
                    where.append(f"({direct} OR {inherited})")
                else:
                    where.append(direct)
            elif op in {"eq", "gte", "lte", "gt", "lt"}:
                if value is None or isinstance(value, (dict, list)):
                    raise ValueError("Use is_null for unknown values; scalar value required")
                if op != "eq" and (not isinstance(value, (float, int)) or isinstance(value, bool)):
                    raise ValueError("Range predicates require numeric values")
                operator = {"eq": "=", "gte": ">=", "lte": "<=", "gt": ">", "lt": "<"}[op]
                where.append(f"{expression} {operator} ? COLLATE NOCASE")
                params.append(value)
            elif op == "in":
                if not isinstance(value, list) or not value or any(isinstance(x, (dict, list)) or x is None for x in value):
                    raise ValueError("in requires nonempty scalar values")
                where.append(f"{expression} COLLATE NOCASE IN ({','.join('?' for _ in value)})")
                params.extend(value)
            elif op == "contains":
                if not isinstance(value, str):
                    raise ValueError("contains requires text")
                where.append(f"instr(lower({expression}),lower(?))>0")
                params.append(value)
            elif op == "is_null":
                if value is not True:
                    raise ValueError("is_null uses value: true; missingness is not clinical absence")
                where.append(f"{expression} IS NULL")
            else:
                raise ValueError(f"Unsupported predicate operator: {op}")
        if criterion.get("linked_to"):
            target = criterion["linked_to"]
            if target not in aliases or not domain.startswith("oncology_") or not aliases[target][1].startswith("oncology_"):
                raise ValueError("linked_to must reference an earlier oncology criterion")
            # Both aliases also match record_id. Never link different tumors by patient alone.
            other = aliases[target][0]
            where.extend([f"{alias}.tumor_ref IS NOT NULL", f"{alias}.tumor_ref={other}.tumor_ref"])
        aliases[cid] = (alias, domain)
    sql = "SELECT " + ",".join(selections) + " FROM cases c " + " ".join(joins) + " WHERE " + " AND ".join(where) + " ORDER BY c.record_id"
    return sql, params


def query_cohort(database: str, spec: dict, destination: str) -> dict:
    sql, params = compile_cohort(spec)
    db = sqlite3.connect(f"file:{Path(database).resolve()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        # Aggregate matched fact IDs, retain explanation for each cohort membership.
        by_record = {}
        for row in db.execute(sql, params):
            record = by_record.setdefault(row["record_id"], {
                "record_id": row["record_id"], "source_hash": row["source_hash"], "source_kind": row["source_kind"], "duplicate_key":row['duplicate_key'], "matched_fact_ids": set()})
            record["matched_fact_ids"].update(row[key] for key in row.keys() if key.endswith("_fact_id"))
        eligible = list(by_record.values())
        before_dedup = len(eligible)
        if spec.get("deduplicate_exact_text", True):
            seen = set()
            unique = []
            for row in eligible:
                if row["duplicate_key"] not in seen:
                    unique.append(row)
                    seen.add(row["duplicate_key"])
            eligible = unique
        candidates = len(eligible)
        size = spec.get("sample_size")
        if size is not None:
            if not isinstance(size, int) or size < 1:
                raise ValueError("sample_size must be a positive integer")
            eligible = random.Random(spec.get("seed", 42)).sample(eligible, min(size, candidates))
        out = Path(destination)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            for row in eligible:
                ids = sorted(row.pop("matched_fact_ids"))
                matches = []
                for fact_id in ids:
                    fact = db.execute("SELECT domain,payload FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
                    matches.append({"fact_id": fact_id, "domain": fact[0], "data": json.loads(fact[1]),
                                    "concepts": [dict(x) for x in db.execute("SELECT field,system,code,label,version,status FROM concepts WHERE fact_id=?",(fact_id,))],
                                    "coding_audit": [json.loads(x[0]) for x in db.execute("SELECT payload FROM concept_annotations WHERE fact_id=?",(fact_id,))],
                                    "evidence": [{"quote": x[0], "spans": json.loads(x[1])} for x in db.execute("SELECT quote,spans FROM evidence WHERE fact_id=?", (fact_id,))]})
                row["source"] = json.loads(db.execute(
                    "SELECT payload FROM cases WHERE record_id=?", (row["record_id"],)).fetchone()[0])["source"]
                row["matches"] = matches
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        manifest = {"name": spec.get("name", "cohort"), "criteria": spec,
                    "corpus_records": db.execute("SELECT count(*) FROM cases").fetchone()[0],
                    "matched_source_records_before_exact_dedup": before_dedup,
                    "matched_records_after_exact_dedup": candidates, "returned_records": len(eligible),
                    "sampling": "uniform seeded sample of matching corpus records" if size else "all matching records",
                    "interpretation": "Candidate case cohort only; not population-representative, not adjudicated eligibility, not prevalence.",
                    "unknown_handling": "Unstated values never satisfy positive or explicitly negative predicates."}
        write_json(out.with_suffix(out.suffix + ".manifest.json"), manifest)
        return manifest
    finally:
        db.close()
