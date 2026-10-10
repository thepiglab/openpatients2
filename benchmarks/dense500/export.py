"""Export only genuinely source-reviewed annotations; preserve every source right."""
import copy
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import yaml

from validate import digest, validate
from schema_targets import with_review_only_items

ROOT=Path(__file__).resolve().parent
ROOTS=[ROOT.parent/'dense50', ROOT]
OUT=ROOT.parent.parent/'data/dense_clinical_cases_v1'
# The release is the unified one-row-per-article dataset in OUT/data. The old
# per-type JSONL files are kept only for the viewer/back-compat, under OUT/legacy.
WRITE_LEGACY=True
LEGACY_FILES=('manifest','annotations','patients','article_bundles','figure_visuals','figure_panels',
    'supplement_visuals','supplement_documents','supplement_clinical','supplement_manifests','figure_ehr_labels',
    'measurement_series','train','validation','test','patients-train','patients-validation','patients-test')
# Single switch: when True, figure_ehr_labels_reviewed and supplement_manifest_reviewed
# become completion gates. Leave False until the EHR-sidecar backfill is parent-signed.
EHR_SIDECAR_GATES=True
EHR_GATE_CHECKS=('supplement_manifest_reviewed','figure_ehr_labels_reviewed')
# 'withhold': an article whose source-review or schema receipt is stale (e.g. during
# a re-sign campaign) is exported as not reviewed, with an explicit status, instead
# of aborting the whole release. 'raise' restores the strict abort.
STALE_RECEIPT_POLICY='withhold'
# Every promoted article is run through the RUNTIME checker objects (reference_gates.py: the same
# validators a local-LLM output must pass). Under 'withhold' a failing article is exported as not
# completed with the reasons in article_checklist.json (`runtime_gate_errors`); 'raise' aborts.
RUNTIME_GATES=True
# Page-reviewed supplements (PDF, or PDF members of a declared ZIP): never image panels.
DOCUMENT_SUPPLEMENT_FORMATS=('dense-supplemental-document-reference/1','dense-supplemental-archive-document-reference/1')
# Media that cannot be obtained: a figure the dual-signed visual review records as status
# 'unavailable' (the source declares no asset; limitations required) and a supplement declared
# only as an external link (no fetchable PMC asset, e.g. YouTube; the dual-signed manifest row is
# caption_and_filename_only with limitations). 'complete_flagged' (default, user decision): such
# media do not block completion; they stay listed as unavailable / not reviewed in
# article_checklist.json (`unavailable_media`) and in the unified record (`unavailable_media`,
# per-figure/supplement `media_available: false`, `content_reviewed: false`), never as reviewed.
# 'block' restores the strict rule (the article stays incomplete).
UNAVAILABLE_MEDIA_POLICY='complete_flagged'


def pixel_review_status(source, visual_target):
    """An empty inventory is not evidence that any image pixels were viewed."""
    if not source.get('figures'):
        return 'no_source_figures'
    if visual_target is None:
        return 'not_started'
    figures=visual_target['figures']
    return ('agent_and_parent_reviewed' if figures and
            all(f['status']=='inspected' for f in figures)
            else 'reviewed_with_unavailable_assets')


def figures_satisfied(pixel_status):
    """Figure gate: every figure inspected (or none), or, under 'complete_flagged', the only gap is figures the
    dual-signed visual review records as unavailable (no declared asset)."""
    return pixel_status in ('agent_and_parent_reviewed','no_source_figures') or (
        UNAVAILABLE_MEDIA_POLICY=='complete_flagged' and pixel_status=='reviewed_with_unavailable_assets')


def unavailable_media(row):
    """[{kind, id, reason}] for media listed as unavailable / not reviewed (never claimed as reviewed)."""
    out=[{'kind':'figure','id':fid,'reason':'no_declared_asset'} for fid in row.get('unavailable_figure_ids',[])]
    out+=[{'kind':'supplement','id':s['supplement_id'],'reason':'external_link_no_declared_asset'}
          for s in row.get('source_supplements') or [] if s.get('external_media_unavailable') and not s['content_inspected']]
    return out


def supplement_satisfied(s):
    """Supplement gate: a dual-signed content review (supplement_review.py), or a dual-signed manifest row
    that is itself the review: non-clinical classification, or a sampled-frame video review
    (see supplement_manifest.manifest_gate_supplements and SUPPLEMENT_DOCUMENT_REVIEW.md)."""
    return s['content_inspected'] or s.get('manifest_non_clinical_reviewed') is True or bool(s.get('manifest_review_basis'))


def supplement_inventory(source, reviewed=(), manifest=None):
    # Captions and links are source text; they do not establish inspection of
    # the linked video, spreadsheet or document. Keep that distinction in export.
    # `manifest` is a dual-signed supplement manifest (or None); a supplement it
    # classifies as non-clinical satisfies the gate without changing content_inspected.
    completed={target['supplement_id']:(target,receipts) for target,receipts in reviewed}
    non_clinical=set(); bases={}; external=set()
    if manifest is not None:
        from openpatients2.supplement_manifest import (external_unavailable_supplements, manifest_gate_supplements,
                                                       non_clinical_supplements)
        non_clinical=non_clinical_supplements(manifest)
        bases=manifest_gate_supplements(manifest,accept_external_unavailable=UNAVAILABLE_MEDIA_POLICY=='complete_flagged')
        external=external_unavailable_supplements(manifest)
    def status(sid):
        if sid in completed:
            return ('agent_and_parent_source_and_document_pages_reviewed'
                    if completed[sid][0].get('format') in DOCUMENT_SUPPLEMENT_FORMATS
                    else 'agent_and_parent_source_and_pixels_reviewed')
        if bases.get(sid)=='manifest_video_frames_sampled':
            return 'agent_and_parent_manifest_video_frames_sampled'
        if sid in external:
            return 'external_media_unavailable'
        return 'linked_content_not_reviewed'
    return [{**copy.deepcopy(s), 'content_inspected':s['supplement_id'] in completed,
             'manifest_non_clinical_reviewed':s['supplement_id'] in non_clinical,
             'manifest_review_basis':None if s['supplement_id'] in completed else bases.get(s['supplement_id']),
             'external_media_unavailable':s['supplement_id'] in external,
             'review_status':status(s['supplement_id']),
             **({'reviews':completed[s['supplement_id']][1]} if s['supplement_id'] in completed else {})}
            for s in source.get('supplements',[])]


def supplemental_clinical_records(reviewed, annotation, split):
    """Keep supplemental schemas/evidence separate from immutable article sections."""
    from supplement_review import supplemental_clinical_bundle
    records = []
    for target, receipts in reviewed:
        bundle = supplemental_clinical_bundle(target, annotation)
        if bundle is not None:
            records.append({**bundle, 'reviews':copy.deepcopy(receipts), 'split':split,
                            'review_status':'agent_and_parent_supplement_clinical_reviewed'})
    return records


def patient_supplemental_clinical(records, patient_id):
    """Unresolved/report-only rows remain in article output, never assigned by similarity."""
    out = []
    for record in records:
        for patient in record['patients']:
            if patient['patient_id'] != patient_id:
                continue
            transcription_ids = {e['transcription_id'] for f in patient['fact_pointers']
                for e in f['evidence'] + f['ownership_evidence'] if e.get('transcription_id')}
            out.append({**{k:copy.deepcopy(v) for k,v in record.items() if k not in
                ('patients','unassigned_or_review_only_facts','clinical_data')},
                'patient':copy.deepcopy(patient),
                'clinical_payload_sha256':digest(record['clinical_data']),
                'coverage':record['clinical_data']['coverage'],
                'limitations':copy.deepcopy(record['clinical_data']['limitations']),
                'transcriptions':[copy.deepcopy(t) for t in record['clinical_data']['transcriptions']
                                  if t['transcription_id'] in transcription_ids]})
    return out


def ehr_sidecar_status(root, aid, source):
    """Informational EHR-sidecar states; never part of the `completed` gate.

    Returns (statuses, reviewed) where reviewed maps sidecar name to
    (target, receipts). A stale/invalid sidecar is reported, not exported, and
    does not abort the release for other articles.
    """
    from supplement_manifest_review import reviewed_manifest
    from figure_ehr_review import reviewed_figure_ehr
    from measurement_series_review import reviewed_series
    from figure_privacy_review import reviewed_privacy_flags
    from visual_review import reviewed_visual_target
    has_tables=any(s.get('kind')=='table_row' for s in source.get('segments',[]))
    specs=[('supplement_manifest','supplement-manifests',reviewed_manifest,
            'no_source_supplements' if not source.get('supplements') else None),
           ('figure_ehr_labels','figure-ehr-labels',reviewed_figure_ehr,
            'no_source_figures' if not source.get('figures') else None),
           ('measurement_series','measurement-series',reviewed_series,
            'no_source_tables_or_supplements' if not has_tables and not source.get('supplements') else None),
           ('figure_privacy_flags','figure-privacy-flags',reviewed_privacy_flags,
            'no_source_figures' if not source.get('figures') else None)]
    statuses={}; reviewed={}; errors={}
    for name,folder,reader,not_applicable in specs:
        if not (root/folder/f'{aid}.json').exists():
            statuses[name]=not_applicable or 'not_started'; continue
        try:
            result=reader(root,aid)
            if result and name in ('figure_ehr_labels','figure_privacy_flags') and reviewed_visual_target(root,aid) is None:
                statuses[name]='labels_reviewed_awaiting_visual_review'
            elif result:
                statuses[name]='agent_and_parent_reviewed'; reviewed[name]=result
            else:
                statuses[name]='unsigned'
        except (ValueError,KeyError,TypeError,FileNotFoundError) as exc:
            statuses[name]='stale_or_invalid'; errors[name]=str(exc)[:300]
    return statuses,reviewed,errors


def species_fields(annotation, patient_ids):
    """Species/non-human flags for EHR-facing rows (non-human patients are included, flagged)."""
    species={p['patient_id']:p.get('species','unknown') for p in annotation['patients']}
    row={pid:species.get(pid,'unknown') for pid in patient_ids}
    values=set(row.values())
    return {'patient_species':row,
            'non_human_patient':True if 'nonhuman' in values else False if values=={'human'} else None,
            'article_has_non_human_patient':'nonhuman' in species.values()}


EHR_PANEL_FIELDS=('ehr_label_status','ehr_category','ehr_includable','ehr_rationale','ehr_basis','ehr_override_reason',
    'ehr_default','privacy_flags','privacy_flags_status','patient_species','non_human_patient','article_has_non_human_patient')


def panel_ehr_fields(figure_id, panel, ehr_status, labels_map, privacy_map, annotation, patient_ids,
                     default=None, out_of_scope=None):
    """EHR/privacy columns for one figure panel, joined on article_id + figure_id + panel.

    Values come only from dual-signed sidecars; otherwise the fields are present
    with null values and an explicit status. Privacy flags carry categories and
    short category descriptions only; identifier values are never copied.
    """
    label = (labels_map or {}).get((figure_id, panel))
    flags = (privacy_map or {}).get((figure_id, panel))
    label_status = out_of_scope or ('agent_and_parent_reviewed' if label else
                                    ehr_status.get('figure_ehr_labels') or 'not_started')
    if label is None and label_status == 'agent_and_parent_reviewed':
        label_status = 'panel_missing_from_reviewed_labels'
    privacy_status = out_of_scope or ('agent_and_parent_reviewed' if flags else
                                      ehr_status.get('figure_privacy_flags') or 'not_started')
    if flags is None and privacy_status == 'agent_and_parent_reviewed':
        privacy_status = 'panel_missing_from_reviewed_flags'
    return {'ehr_label_status':label_status,
            'ehr_category':label['ehr_category'] if label else None,
            'ehr_includable':label['ehr_includable'] if label else None,
            'ehr_rationale':label['rationale'] if label else None,
            'ehr_basis':label['basis'] if label else None,
            'ehr_override_reason':label.get('override_reason') if label else None,
            'ehr_default':{k:default[k] for k in ('ehr_category','ehr_includable','rule')} if default else None,
            'privacy_flags':copy.deepcopy(flags),'privacy_flags_status':privacy_status,
            **species_fields(annotation,patient_ids)}


def patient_ehr_summary(pid, ehr_status, ehr_reviewed, annotation):
    """Compact per-patient EHR view: species, supplement manifest summary, series references."""
    manifest=ehr_reviewed.get('supplement_manifest',(None,))[0]
    series=ehr_reviewed.get('measurement_series',(None,))[0]
    return {**species_fields(annotation,[pid]),
        'supplement_manifest':{'status':ehr_status.get('supplement_manifest'),
            'files':[{'supplement_id':f['supplement_id'],'filename':f['machine'].get('filename'),
                      'content_class':f['review']['content_class'],'patient_scope':f['review']['patient_scope'],
                      'patient_level':pid in f['review']['patient_ids'],'ehr_includable':f['review']['ehr_includable'],
                      'ehr_modality':f['review']['ehr_modality']} for f in manifest['files']] if manifest else None},
        'measurement_series':{'status':ehr_status.get('measurement_series'),'release_file':'measurement_series.jsonl',
            'series_ids':[s['series_id'] for s in series['series'] if pid in s['patient_ids']] if series else None}}


CARD_START='<!-- unified-fields:start -->'
CARD_END='<!-- unified-fields:end -->'


def card_fields_section(body):
    """Replace the generated field dictionary in the dataset card body (idempotent)."""
    from openpatients2.unified_record import FIELD_DICTIONARY
    section=CARD_START+'\n'+FIELD_DICTIONARY.strip()+'\n'+CARD_END
    if CARD_START in body and CARD_END in body:
        before,rest=body.split(CARD_START,1)
        return before+section+rest.split(CARD_END,1)[1]
    return body.rstrip('\n')+'\n\n'+section+'\n'


def receipt_summary(kind, receipts):
    return [{'kind':kind,'role':r.get('role'),'reviewer':r.get('reviewer'),'reviewed_at':r.get('reviewed_at'),
             'status':r.get('status'),'physician_adjudicated':r.get('physician_adjudicated',False)} for r in receipts or []]


def write_unified(candidates, checklist):
    """Build, validate and write one unified row per COMPLETED article, by split."""
    from openpatients2.unified_record import build_record, json_schema
    rows={'train':[],'validation':[],'test':[]}
    for aid,kwargs in sorted(candidates.items()):
        entry=checklist['articles'].get(aid)
        if not entry or not entry['completed']:
            continue
        completion={'completed':True,'checks':entry['checks'],'informational_checks':entry['informational_checks'],
                    'unavailable_media':entry.get('unavailable_media',[]),'unavailable_media_policy':UNAVAILABLE_MEDIA_POLICY,
                    'completion_definition':checklist['completion_definition'],
                    'ehr_sidecar_gates_enabled':checklist.get('ehr_sidecar_gates_enabled')}
        record=build_record(**kwargs,completion=completion)
        rows[record['split']].append(record)
    folder=OUT/'data'; folder.mkdir(exist_ok=True)
    for split,records in rows.items():
        write_jsonl(folder/f'{split}.jsonl',records)
    (OUT/'schema.json').write_text(json.dumps(json_schema(),ensure_ascii=False,indent=2)+'\n')
    return rows


def reviewed_schema_target(root, annotation, source):
    """A generated shape is not a reviewed deliverable. Require both inspections."""
    aid=annotation['article_id']
    parent_path=root/'schema-parent-reviews'/f'{aid}.json'
    if not parent_path.exists():return None
    from schema_review import validate_submission
    target=json.loads((root/'schema-submissions'/f'{aid}.json').read_text())
    receipts=[]
    for role in ('agent','parent'):
        receipt=json.loads((root/f'schema-{role}-reviews'/f'{aid}.json').read_text())
        for key,value in [('status','schema_source_reviewed'),('role',role),
                ('article_id',aid),('annotation_sha256',digest(annotation)),
                ('target_sha256',digest(target)),('source_text_sha256',source['text_sha256'])]:
            if receipt.get(key)!=value:raise ValueError(f'{aid}: stale/missing {role} schema review {key}')
        from signing_guard import receipt_identity_errors
        if receipt_identity_errors(receipt):raise ValueError(f'{aid}: {role} schema review has a placeholder reviewer/notes')
        receipts.append(receipt)
    validate_submission(target,annotation,source)
    return target,receipts


def write_jsonl(path,rows):
    temp=path.with_suffix('.jsonl.tmp')
    with temp.open('w') as stream:
        for row in rows: stream.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
    temp.replace(path)


def write_checklist(statistics, manifest):
    """One durable completion checkbox per article, derived from reviewed gates."""
    articles={}
    for row in sorted(manifest, key=lambda r:r['article_id']):
        aid=row['article_id']
        if aid in articles:
            raise ValueError(f'Duplicate checklist article: {aid}')
        checks={
            'annotation_saved':row['annotation_status'] in ('drafted','annotated'),
            'parent_source_review':row['review_status']=='parent_source_reviewed',
            'full_schema_dual_review':row['full_schema_review_status']=='agent_and_parent_source_reviewed',
            'figure_dual_review_or_no_figures':figures_satisfied(row['pixel_review_status']),
            'supplement_review_or_no_supplements':all(supplement_satisfied(s) for s in row['source_supplements']),
        }
        if RUNTIME_GATES:
            checks['runtime_gates_passed']=(row.get('runtime_gates') or {}).get('passed') is True
        sidecars=row.get('ehr_sidecar_status',{})
        informational={
            'supplement_manifest_reviewed':sidecars.get('supplement_manifest') in ('agent_and_parent_reviewed','no_source_supplements'),
            'figure_ehr_labels_reviewed':sidecars.get('figure_ehr_labels') in ('agent_and_parent_reviewed','no_source_figures'),
            'measurement_series_reviewed':sidecars.get('measurement_series') in ('agent_and_parent_reviewed','no_source_tables_or_supplements'),
            'figure_privacy_flags_reviewed':sidecars.get('figure_privacy_flags') in ('agent_and_parent_reviewed','no_source_figures'),
        }
        if EHR_SIDECAR_GATES:
            # Gate checks move from informational into `checks` behind the switch.
            for name in EHR_GATE_CHECKS:
                checks[name]=informational.pop(name)
        pending=[name for name,done in checks.items() if not done]
        completed=not pending
        articles[aid]={
            'completed':completed,
            'status':'completed' if completed else 'in_progress' if checks['annotation_saved'] else 'not_started',
            'title':row['title'],'article_url':row['article_url'],
            'checks':checks,'remaining':pending,
            'source_text_sha256':row['text_sha256'],
            'figure_review_status':row['pixel_review_status'],
            'unreviewed_supplement_ids':[s.get('supplement_id') for s in row['source_supplements'] if not supplement_satisfied(s)],
            'non_clinical_manifest_supplement_ids':[s.get('supplement_id') for s in row['source_supplements']
                                                    if not s['content_inspected'] and s.get('manifest_non_clinical_reviewed')],
            # Supplements whose gate is satisfied by the dual-signed manifest itself ('reviewed via manifest').
            'manifest_reviewed_supplements':{s['supplement_id']:s['manifest_review_basis'] for s in row['source_supplements']
                                             if not s['content_inspected'] and s.get('manifest_review_basis')},
            'unavailable_external_supplement_ids':[s.get('supplement_id') for s in row['source_supplements']
                                                   if s.get('external_media_unavailable')],
            # Media that cannot be obtained: listed as unavailable / not reviewed, never as reviewed.
            'unavailable_media':unavailable_media(row),
            'informational_checks':informational,
            'ehr_sidecar_status':sidecars,
        }
        articles[aid]['completed_with_unavailable_media']=completed and bool(articles[aid]['unavailable_media'])
        if row.get('runtime_gates'):
            articles[aid]['runtime_gates']=row['runtime_gates']
        withheld=[r for r in (row['review_status'],row['full_schema_review_status'])
                  if r in ('stale_source_review_receipt','invalid_annotation','stale_schema_receipt','invalid_schema_target','runtime_gate_failed')]
        if withheld:
            articles[aid]['withheld']={'statuses':withheld,'reason':row.get('withheld_reason'),
                'runtime_gate_errors':(row.get('runtime_gates') or {}).get('errors',[])[:10]}
    checklist={
        'format':'dense-article-checklist/1',
        'release_layout':'data/{train,validation,test}.jsonl (openpatients2-unified-article/1; completed articles only)',
        'updated_at':datetime.now(timezone.utc).isoformat(),
        'target_articles':statistics['target_articles'],
        'listed_articles':len(articles),
        'completed_articles':sum(a['completed'] for a in articles.values()),
        'counts':{k:statistics[k] for k in ('acquired_candidate_articles','drafted_articles',
            'parent_source_reviewed_articles','full_schema_reviewed_articles',
            'pixel_reviewed_articles','reviewed_no_figure_inventories','reviewed_facts')},
        'completion_definition':'Source/facts personally reviewed by parent; full runtime schemas and actual figure pixels reviewed by annotator and parent; applicable supplements inspected; the reference passes the runtime checker gates (reference_gates.py). Unavailable media do not count as completed.',
        'runtime_gates_enabled':RUNTIME_GATES,
        'withheld_articles':sum('withheld' in a for a in articles.values()),
        'ehr_sidecar_gates_enabled':EHR_SIDECAR_GATES,
        'ehr_sidecar_gate_checks':list(EHR_GATE_CHECKS),
        'informational_counts':{name:sum(a['informational_checks'].get(name,a['checks'].get(name,False)) for a in articles.values())
                                for name in ('supplement_manifest_reviewed','figure_ehr_labels_reviewed',
                                             'measurement_series_reviewed','figure_privacy_flags_reviewed')},
        'completed_with_all_informational_checks':sum(a['completed'] and all(a['informational_checks'].values()) for a in articles.values()),
        'informational_note':('Supplement manifest, figure EHR labels, figure privacy flags and measurement series are additive EHR sidecars. '
            +('supplement_manifest_reviewed and figure_ehr_labels_reviewed are completion gates (EHR_SIDECAR_GATES=True); the rest are informational.'
              if EHR_SIDECAR_GATES else 'Their checks are reported separately and are not part of `completed` (EHR_SIDECAR_GATES=False).')
            +' A supplement whose dual-signed manifest classifies every file as non-clinical, or as a downloaded video whose sampled'
            +' frames were viewed (manifest_video_frames_sampled), satisfies supplement_review_or_no_supplements (manifest_reviewed_supplements).'
            +(' Media that cannot be obtained (figures recorded unavailable, external-link supplements) block completion.'
              if UNAVAILABLE_MEDIA_POLICY=='block' else ' Media that cannot be obtained (figures the dual-signed visual review records as unavailable, '
              'external-link supplements with no declared PMC asset) do not block completion: the article is complete, flagged, and the media '
              'stay listed in unavailable_media as not reviewed.')),
        'unavailable_media_policy':UNAVAILABLE_MEDIA_POLICY,
        'completed_with_unavailable_media':sum(a['completed_with_unavailable_media'] for a in articles.values()),
        'note':'AI reference annotations, not physician gold or measured recall. Completion is derived from validated receipts; do not manually check off unreviewed articles.',
        'articles':articles,
    }
    path=OUT/'article_checklist.json'
    temporary=path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(checklist,ensure_ascii=False,indent=2)+'\n')
    temporary.replace(path)
    return checklist


def author_attribution(source,root):
    raw=source.get('authors',[])
    xml_path=root/'raw-xml'/f'{source["article_id"]}.xml'
    if not xml_path.exists():
        return {'authors':raw,'author_representation':'retained_parser_output; name formatting needs XML review'}
    tree=ET.fromstring(xml_path.read_text())
    names=[]
    for contributor in tree.findall('./front/article-meta/contrib-group/contrib'):
        if contributor.get('contrib-type') not in (None,'author'): continue
        name=contributor.find('name')
        if name is not None:
            given=' '.join(name.findtext('given-names','').split())
            surname=' '.join(name.findtext('surname','').split())
            label=(given+' '+surname).strip()
        else:
            collab=contributor.find('collab')
            label=' '.join(''.join(collab.itertext()).split()) if collab is not None else ''
        if label: names.append(label)
    return {'authors':names or raw,'author_representation':'JATS contributor names' if names else 'retained_parser_output'}


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    retained=json.loads((ROOT/'retained-strata.json').read_text())
    selection={r['article_id']:r for r in json.loads((ROOT/'selection.json').read_text())}
    manifest=[]; final=[]; patient_bundles=[]; article_bundles=[]; visual_bundles=[]; supplement_bundles=[]; document_bundles=[]; clinical_supplement_bundles=[]; figure_panels=[]; draft_count=0
    supplement_manifest_rows=[]; figure_ehr_rows=[]; measurement_series_rows=[]; unified_candidates={}
    for root in ROOTS:
        for source_path in sorted((root/'sources').glob('*.json')):
            source=json.loads(source_path.read_text()); aid=source['article_id']
            # Acquisition writes the source before its manifest checkpoint.
            if aid not in selection and aid not in retained:
                continue
            sel=selection.get(aid,{})
            review_path=root/'reviews'/f'{aid}.json'; annotation_path=root/'annotations'/f'{aid}.json'
            drafted=(root/'drafts'/f'{aid}.json').exists(); draft_count+=drafted
            row={'article_id':aid,'pmcid':source['pmcid'],'pmid':source.get('pmid'),
                'doi':source.get('doi'),'title':source['title'],'article_type':source.get('article_type') or 'unspecified',**author_attribution(source,root),
                'article_url':source['source_url'],'xml_url':source.get('xml_url'),
                'text_sha256':source['text_sha256'],'xml_sha256':source['xml_sha256'],
                'primary_stratum':sel.get('stratum',retained.get(aid)),
                'split':sel.get('split','train'),'prior_benchmark_exposure':aid in retained,
                'source_family_check':sel.get('source_family_check','pending_cross_article_check'),
                'source_license':source['license'],
                'changes_from_source':'AI clinical extraction in runtime schemas, relative event relations, summaries and figure annotation; source and pixel reviews tracked separately.',
                'annotation_status':'drafted' if drafted else 'not_started',
                'review_status':'not_started','full_schema_review_status':'not_started','pixel_review_status':'not_started',
                'source_provenance':source.get('retrieval',{}),
                'source_supplements':None}
            ehr_status,ehr_reviewed,ehr_errors=ehr_sidecar_status(root,aid,source)
            signed_manifest=ehr_reviewed.get('supplement_manifest',(None,))[0]
            row['source_supplements']=supplement_inventory(source,manifest=signed_manifest)
            row['ehr_sidecar_status']=ehr_status
            if ehr_errors: row['ehr_sidecar_errors']=ehr_errors
            review_current=True
            gates=None
            if RUNTIME_GATES and annotation_path.exists():
                from reference_gates import run_gates
                try:
                    gates=run_gates(root,aid)
                except (ValueError,KeyError,TypeError,FileNotFoundError) as exc:
                    gates={'passed':False,'errors':[{'gate':'reference_gates.crashed','patient_id':None,'detail':str(exc)[:300]}],'warnings':[]}
                row['runtime_gates']={'passed':gates['passed'],'errors':gates['errors'][:25],'error_count':len(gates['errors']),
                                      'warning_count':len(gates['warnings'])}
            if review_path.exists() and STALE_RECEIPT_POLICY=='withhold':
                review=json.loads(review_path.read_text());annotation=json.loads(annotation_path.read_text())
                if review.get('annotation_sha256')!=digest(annotation) or review.get('source_text_sha256')!=source['text_sha256']:
                    review_current=False
                    row.update(annotation_status='annotated',review_status='stale_source_review_receipt')
                elif validate(annotation,source):
                    review_current=False
                    row.update(annotation_status='annotated',review_status='invalid_annotation',
                               withheld_reason=validate(annotation,source)[:5])
            if review_path.exists() and review_current:
                review=json.loads(review_path.read_text());annotation=json.loads(annotation_path.read_text())
                assert review['status']=='source_reviewed' and review['source_read_in_full']
                assert review['annotation_sha256']==digest(annotation),f'{aid}: stale review receipt'
                assert review['source_text_sha256']==source['text_sha256'],f'{aid}: stale source receipt'
                errors=validate(annotation,source)
                assert not errors,(aid,errors)
                row.update(annotation_status='annotated',review_status='parent_source_reviewed')
                record=copy.deepcopy(annotation)
                record.update(source={k:row[k] for k in ['pmcid','pmid','doi','title','authors','article_url','xml_url','source_license']},
                              split=row['split'],primary_stratum=row['primary_stratum'],review=review)
                record['source_media']=[{**{k:fig.get(k) for k in
                    ['figure_id','article_anchor_url','source_xml_url','graphic_references',
                     'media','image_urls','rights_statements','reuse_status']},
                    'figure_id':fig.get('figure_key') or fig.get('figure_id')}
                    for fig in source.get('figures',[])]
                from supplement_review import reviewed_supplement_targets
                supplements=reviewed_supplement_targets(root,aid)
                row['source_supplements']=supplement_inventory(source,supplements,manifest=signed_manifest)
                record['source_supplements']=row['source_supplements']
                documents=[(st,sr) for st,sr in supplements if st.get('format') in DOCUMENT_SUPPLEMENT_FORMATS]
                document_bundles.extend({**copy.deepcopy(st),'reviews':sr,'split':row['split']} for st,sr in documents)
                supplemental_clinical=supplemental_clinical_records(supplements,annotation,row['split'])
                clinical_supplement_bundles.extend(supplemental_clinical)
                final.append(record)
                ehr_release={}
                if 'supplement_manifest' in ehr_reviewed:
                    st,sr=ehr_reviewed['supplement_manifest']
                    ehr_release['supplement_manifest']={**copy.deepcopy(st),'reviews':sr}
                    supplement_manifest_rows.extend({'format':st['format'],'article_id':aid,'split':row['split'],
                        **copy.deepcopy(f),**species_fields(annotation,f['review']['patient_ids']),
                        'source_license':source['license'],'reviews':sr} for f in st['files'])
                privacy=None; labels_map=None
                if 'figure_privacy_flags' in ehr_reviewed:
                    from openpatients2.figure_privacy import panel_flags
                    pt,pr=ehr_reviewed['figure_privacy_flags']
                    ehr_release['figure_privacy_flags']={**copy.deepcopy(pt),'reviews':pr}
                    privacy=panel_flags(pt)
                if 'figure_ehr_labels' in ehr_reviewed:
                    from openpatients2.figure_ehr import label_rows
                    from visual_review import reviewed_visual_target
                    lt,lr=ehr_reviewed['figure_ehr_labels']
                    ehr_release['figure_ehr_labels']={**copy.deepcopy(lt),'reviews':lr}
                    labels_map={(f['figure_id'],p['panel']):p['label'] for f in lt['figures'] for p in f['panels']}
                    # Flags are informational: they never change ehr_includable.
                    figure_ehr_rows.extend({**r,**species_fields(annotation,r['patient_ids']),
                        'privacy_flags':(privacy or {}).get((r['figure_id'],r['panel'])),
                        'privacy_flags_status':ehr_status.get('figure_privacy_flags'),
                        'source_license':source['license'],'reviews':lr}
                        for r in label_rows(aid,lt,reviewed_visual_target(root,aid)[0],row['split']))
                if 'measurement_series' in ehr_reviewed:
                    from openpatients2.measurement_series import series_rows
                    mt,mr=ehr_reviewed['measurement_series']
                    ehr_release['measurement_series']={**copy.deepcopy(mt),'reviews':mr}
                    measurement_series_rows.extend({**r,**species_fields(annotation,r['patient_ids']),
                        'source_license':source['license'],'reviews':mr}
                        for r in series_rows(aid,mt,row['split']))
                try:
                    reviewed=reviewed_schema_target(root,annotation,source)
                except ValueError as exc:
                    if STALE_RECEIPT_POLICY!='withhold':
                        raise
                    reviewed=None
                    row['full_schema_review_status']='stale_schema_receipt' if 'stale/missing' in str(exc) else 'invalid_schema_target'
                    if 'stale/missing' not in str(exc):row['withheld_reason']=str(exc)[:500]
                if reviewed and gates is not None and not gates['passed']:
                    if STALE_RECEIPT_POLICY!='withhold':
                        raise ValueError((aid,'runtime gates failed',gates['errors'][:5]))
                    reviewed=None; row['full_schema_review_status']='runtime_gate_failed'
                if reviewed:
                    from openpatients2.measurements import observation_measurements
                    from openpatients2.figure_visuals import panel_columns
                    from visual_review import reviewed_visual_target
                    target,receipts=reviewed
                    visual=reviewed_visual_target(root,aid)
                    visual_rows=[]
                    if visual:
                        from openpatients2.figure_ehr import derive_default
                        vt,vr=visual
                        row['pixel_review_status']=pixel_review_status(source,vt)
                        row['unavailable_figure_ids']=[f['figure_id'] for f in vt['figures'] if f['status']=='unavailable']
                        for f in vt['figures']:
                            if f['status']!='inspected':continue
                            raw_panels={p['panel']:p for p in f['analysis']['visual']['panels']}
                            for panel in panel_columns(aid,f['analysis']['visual']):
                                assignments=[a for a in f['analysis']['attribution']['assignments'] if a['panel'] is None or a['panel']==panel['panel']]
                                owners=sorted({p for a in assignments for p in a['patient_ids']})
                                visual_rows.append({**panel,'split':row['split'],'patient_ids':owners,
                                    'assignments':assignments,'assets':f['assets'],'source_license':source['license'],
                                    'review_status':'agent_and_parent_source_and_pixels_reviewed','pixel_accuracy_measured':False,
                                    **panel_ehr_fields(f['figure_id'],panel['panel'],ehr_status,labels_map,privacy,annotation,owners,
                                                       default=derive_default(raw_panels[panel['panel']],assignments))})
                        visual_bundles.append({**copy.deepcopy(vt),'reviews':vr,'split':row['split'],
                            'ehr_label_status':ehr_status.get('figure_ehr_labels'),
                            'privacy_flags_status':ehr_status.get('figure_privacy_flags'),
                            'panel_ehr':[{'article_id':aid,'figure_id':r['figure_id'],'panel':r['panel'],'patient_ids':r['patient_ids'],
                                          **{k:copy.deepcopy(r[k]) for k in EHR_PANEL_FIELDS}} for r in visual_rows]})
                        figure_panels.extend(visual_rows)
                    elif not source.get('figures'):
                        row['pixel_review_status']='no_source_figures'
                    supplemental_rows=[]
                    for st,sr in supplements:
                        if st.get('format') in DOCUMENT_SUPPLEMENT_FORMATS:
                            continue  # PDF pages never become image panels or vision observations.
                        supplement_bundles.append({**copy.deepcopy(st),'reviews':sr,'split':row['split']})
                        for panel in panel_columns(aid,st['analysis']['visual']):
                            assignments=[a for a in st['analysis']['attribution']['assignments'] if a['panel'] is None or a['panel']==panel['panel']]
                            owners=sorted({p for a in assignments for p in a['patient_ids']})
                            supplemental_rows.append({**panel,'media_kind':'supplement','supplement_id':st['supplement_id'],
                                'split':row['split'],'patient_ids':owners,
                                'assignments':assignments,'assets':st['assets'],'source_license':source['license'],
                                'review_status':'agent_and_parent_source_and_pixels_reviewed','pixel_accuracy_measured':False,
                                **panel_ehr_fields(st['supplement_id'],panel['panel'],ehr_status,None,None,annotation,owners,
                                                   out_of_scope='not_in_sidecar_scope_supplement_image')})
                    figure_panels.extend(supplemental_rows)
                    row['full_schema_review_status']='agent_and_parent_source_reviewed'
                    article_bundles.append({'article_id':aid,'split':row['split'],
                        'roster':target['roster'],'figure_attribution':target['figure_attribution'],
                        'pixel_review_status':row['pixel_review_status'],
                        'visual_reference':aid if visual else None,
                        'schema_fingerprints':target['schema_fingerprints'],
                        'source_media':record['source_media'],
                        'source_supplements':record['source_supplements'],
                        'supplemental_clinical':supplemental_clinical,'reviews':receipts,
                        'ehr_sidecar_status':ehr_status,
                        'supplement_manifest':ehr_release.get('supplement_manifest'),
                        'figure_ehr_labels':ehr_release.get('figure_ehr_labels'),
                        'measurement_series':ehr_release.get('measurement_series'),
                        'figure_privacy_flags':ehr_release.get('figure_privacy_flags'),
                        **species_fields(annotation,[p['patient_id'] for p in annotation['patients']])})
                    for bundle in target['patients']:
                        bundle=copy.deepcopy(bundle)
                        bundle.update(split=row['split'],primary_stratum=row['primary_stratum'],
                            article_id=aid,review={'annotation':review,'full_schema':receipts},
                            clinical_review_status='source_reviewed_ai_reference',
                            source_media=record['source_media'],
                            source_supplements=record['source_supplements'])
                        bundle['observation_measurements']=observation_measurements(
                            bundle['sections']['observations'],source['segments'])
                        bundle['supplemental_clinical']=patient_supplemental_clinical(
                            supplemental_clinical,bundle['source']['patient_target']['patient_id'])
                        if visual:
                            pid=bundle['source']['patient_target']['patient_id']
                            bundle['vision']['status']=row['pixel_review_status']
                            bundle['vision']['panels']=[p for p in visual_rows if pid in p['patient_ids']]
                            bundle['vision']['visual_reviews']=visual[1]
                            bundle['vision']['caption_only']['limitations'].append('The caption-only companion retains its original scope; independently inspected pixels are in vision.panels and the article visual bundle.')
                        if supplements:
                            pid=bundle['source']['patient_target']['patient_id']
                            bundle['vision']['supplement_panels']=[p for p in supplemental_rows if pid in p['patient_ids']]
                            bundle['vision']['supplement_reviews']=[r for st,reviews in supplements if st.get('format') not in DOCUMENT_SUPPLEMENT_FORMATS for r in reviews]
                        bundle['ehr']=patient_ehr_summary(bundle['source']['patient_target']['patient_id'],
                                                          ehr_status,ehr_reviewed,annotation)
                        bundle['source']['attribution']={k:row[k] for k in
                            ['title','authors','article_url','source_license','changes_from_source']}
                        patient_bundles.append(bundle)
                    unified_candidates[aid]=dict(
                        article={'title':row['title'],'authors':row['authors'],'article_url':row['article_url'],
                                 'license':source['license']},
                        split=row['split'],primary_stratum=row['primary_stratum'],
                        identities=annotation['patients'],
                        # Quarantined facts are exported as review_only items (scoring contract).
                        patient_bundles=[with_review_only_items(b) for b in patient_bundles if b['article_id']==aid],
                        source=source,visual_target=visual[0] if visual else None,
                        visual_status=row['pixel_review_status'],
                        labels=ehr_reviewed.get('figure_ehr_labels',(None,))[0],
                        privacy=ehr_reviewed.get('figure_privacy_flags',(None,))[0],
                        manifest=ehr_reviewed.get('supplement_manifest',(None,))[0],
                        supplement_reviews=supplements,
                        series=ehr_reviewed.get('measurement_series',(None,))[0],
                        statuses=ehr_status,
                        provenance={'generator':'dense500_reference_export',
                            'annotation_review':{k:review.get(k) for k in ('reviewer','review_type','reviewed_at','status',
                                'source_read_in_full','annotation_sha256','source_text_sha256')},
                            'annotator_model':annotation.get('annotation_provenance',{}).get('annotator_model'),
                            'receipts':receipt_summary('full_schema',receipts)
                                +receipt_summary('visual',visual[1] if visual else [])
                                +[x for st,sr in supplements for x in receipt_summary('supplement:'+st['supplement_id'],sr)]
                                +[x for name,(t,rr) in ehr_reviewed.items() for x in receipt_summary(name,rr)],
                            'physician_adjudicated':False})
            manifest.append(row)
    assert len({r['article_id'] for r in manifest})==len(manifest)
    LEGACY=OUT/'legacy'; LEGACY.mkdir(exist_ok=True)
    for name in LEGACY_FILES:
        # Per-type files are no longer the release; never leave stale top-level copies.
        (OUT/f'{name}.jsonl').unlink(missing_ok=True)
    write_jsonl(LEGACY/'manifest.jsonl',manifest)
    write_jsonl(LEGACY/'annotations.jsonl',final)
    write_jsonl(LEGACY/'patients.jsonl',patient_bundles)
    write_jsonl(LEGACY/'article_bundles.jsonl',article_bundles)
    write_jsonl(LEGACY/'figure_visuals.jsonl',visual_bundles)
    write_jsonl(LEGACY/'figure_panels.jsonl',figure_panels)
    write_jsonl(LEGACY/'supplement_visuals.jsonl',supplement_bundles)
    write_jsonl(LEGACY/'supplement_documents.jsonl',document_bundles)
    write_jsonl(LEGACY/'supplement_clinical.jsonl',clinical_supplement_bundles)
    write_jsonl(LEGACY/'supplement_manifests.jsonl',supplement_manifest_rows)
    write_jsonl(LEGACY/'figure_ehr_labels.jsonl',figure_ehr_rows)
    write_jsonl(LEGACY/'measurement_series.jsonl',measurement_series_rows)
    for split in ('train','validation','test'):
        write_jsonl(LEGACY/f'{split}.jsonl',[r for r in final if r['split']==split])
        write_jsonl(LEGACY/f'patients-{split}.jsonl',[r for r in patient_bundles if r['split']==split])
    from schema_targets import TASK_MODELS,Roster,Summary,Vision,PatientTimeline,FigureReview
    from openpatients2.figure_visuals import FigureVisuals,JointFigureAnalysis
    from supplement_review import DocumentSupplementTarget, ArchiveDocumentTarget
    contracts={**TASK_MODELS,'roster':Roster,'summary':Summary,'timeline_v2':PatientTimeline,
        'figure_attribution':FigureReview,'caption_vision':Vision,
        'figure_visuals':FigureVisuals,'joint_figure_analysis':JointFigureAnalysis,
        'supplement_document':DocumentSupplementTarget,
        'supplement_archive_document':ArchiveDocumentTarget}
    snapshots={name:model.model_json_schema() for name,model in contracts.items()}
    (OUT/'schemas.json').write_text(json.dumps(snapshots,ensure_ascii=False,indent=2)+'\n')
    from coverage_audit import write_report
    write_report(OUT,patient_bundles)
    # Explicit Hub configurations avoid accidentally mixing the source catalog
    # with annotation rows, or loading held-out answers into a default train set.
    card=OUT/'README.md'
    if card.exists():
        _,front,body=card.read_text().split('---',2)
        metadata=yaml.safe_load(front)
        # One dataset, one default config, three splits: the unified article rows.
        metadata['configs']=[{'config_name':'default','default':True,
            'data_files':[{'split':split,'path':f'data/{split}.jsonl'} for split in ('train','validation','test')]}]
        body=card_fields_section(body)
        card.write_text('---\n'+yaml.safe_dump(metadata,sort_keys=False,allow_unicode=True)+'---'+body)
    facts=[f for r in final for p in r['patients'] for f in p['facts']]
    patients=[p for r in final for p in r['patients']]
    figures=[f for r in final for f in r['figures']]
    assessments=[r['review']['assessment'] for r in final]
    challenge_keys=('rich_trajectory','normal_negative_evidence','serial_measurements',
                    'common_workflow','planned_vs_actual_challenge')
    statistics={'target_articles':500,'acquired_candidate_articles':len(manifest),'drafted_articles':draft_count,
        'parent_source_reviewed_articles':len(final),'physician_adjudicated_articles':0,
        'full_schema_reviewed_articles':len(article_bundles),
        'full_schema_reviewed_patients':len(patient_bundles),
        'complete_500_article_release':len(final)==500 and len(article_bundles)==500 and all(figures_satisfied(r['pixel_review_status']) and all(supplement_satisfied(s) for s in r['source_supplements']) for r in manifest),
        'pixel_reviewed_articles':sum(r['pixel_review_status']=='agent_and_parent_reviewed' for r in manifest),
        'articles_with_any_inspected_pixels':sum(any(f['status']=='inspected' for f in r['figures']) for r in visual_bundles),
        'reviewed_no_figure_inventories':sum(not r['figures'] for r in visual_bundles),
        'pixel_inspected_figures':sum(f['status']=='inspected' for r in visual_bundles for f in r['figures']),
        'selected_uninspected_supplements':sum(not supplement_satisfied(s) for r in manifest for s in r['source_supplements']),
        'non_clinical_manifest_supplements':sum(s.get('manifest_non_clinical_reviewed') is True and not s['content_inspected'] for r in manifest for s in r['source_supplements']),
        'figure_privacy_flagged_articles':sum(r['ehr_sidecar_status']['figure_privacy_flags']=='agent_and_parent_reviewed' for r in manifest),
        'non_human_ehr_figure_panels':sum(r['non_human_patient'] is True for r in figure_ehr_rows),
        'pixel_reviewed_supplements':len(supplement_bundles),
        'page_reviewed_document_supplements':len(document_bundles),
        'reviewed_patients':len(patients),'reviewed_facts':len(facts),
        'supplement_manifest_reviewed_articles':sum(r['ehr_sidecar_status']['supplement_manifest']=='agent_and_parent_reviewed' for r in manifest),
        'figure_ehr_labeled_articles':sum(r['ehr_sidecar_status']['figure_ehr_labels']=='agent_and_parent_reviewed' for r in manifest),
        'measurement_series_reviewed_articles':sum(r['ehr_sidecar_status']['measurement_series']=='agent_and_parent_reviewed' for r in manifest),
        'stale_or_invalid_ehr_sidecars':sum(v=='stale_or_invalid' for r in manifest for v in r['ehr_sidecar_status'].values()),
        'reviewed_supplement_manifest_files':len(supplement_manifest_rows),
        'ehr_includable_supplement_files':sum(r['review']['ehr_includable'] for r in supplement_manifest_rows),
        'reviewed_figure_ehr_panels':len(figure_ehr_rows),
        'ehr_includable_figure_panels':sum(r['ehr_includable'] for r in figure_ehr_rows),
        'figure_ehr_categories':dict(Counter(r['ehr_category'] for r in figure_ehr_rows)),
        'reviewed_measurement_series':len(measurement_series_rows),
        'reviewed_measurement_points':sum(len(r['points']) for r in measurement_series_rows),
        'scored_supported_facts':sum(f['scoring']=='include' for f in facts),
        'review_only_facts':sum(f['scoring']=='review_only' for f in facts),
        'event_count':sum(len(p['events']) for p in patients),
        'temporal_relation_count':sum(len(p['temporal_relations']) for p in patients),
        'reviewed_figures':len(figures),'caption_stage_pixel_inspected_figures':sum(f['visual_status']!='not_inspected' for f in figures),
        'selected_primary_strata':dict(Counter(r['primary_stratum'] for r in manifest)),
        'reviewed_primary_strata':dict(Counter(r['primary_stratum'] for r in final)),
        'reviewed_patient_counts':dict(Counter(str(len(r['patients'])) for r in final)),
        'reviewed_species':dict(Counter(p['species'] for p in patients)),
        'reviewed_facts_by_domain':dict(Counter(f['task'] for f in facts)),
        'reviewed_facts_by_assertion':dict(Counter(f['fields'].get('assertion','not_applicable') for f in facts)),
        'reviewed_splits':dict(Counter(r['split'] for r in final)),
        'selected_splits':dict(Counter(r['split'] for r in manifest)),
        'reviewed_article_challenges':{k:sum(a.get(k) is True for a in assessments) for k in challenge_keys},
        'reviewed_source_conflict_facts':sum(f['certainty']=='source_conflict' for f in facts),
        'reviewed_ambiguous_facts':sum(f['certainty']=='ambiguous' for f in facts),
        'reviewed_figure_scopes':dict(Counter(f['scope'] for f in figures)),
        'reviewed_event_occurrences':dict(Counter(e['occurrence'] for p in patients for e in p['events'])),
        'reviewed_article_types':dict(Counter(next(m for m in manifest if m['article_id']==r['article_id']).get('article_type','unknown') for r in final)),
        'selected_license_codes':dict(Counter(r['source_license'].get('code','unknown') for r in manifest)),
        'selected_license_versions':dict(Counter(r['source_license'].get('code','unknown')+' '+str(r['source_license'].get('version') or 'unspecified') for r in manifest)),
        'denominator_note':'Article tables use acquired or reviewed articles as labeled; facts/species use annotation rows/patients. One clinical finding may be represented in several schemas; rows are not unique biological facts. These counts do not measure clinical accuracy. Pixel review is a separate later stage from caption annotation.'}
    (OUT/'statistics.json').write_text(json.dumps(statistics,indent=2)+'\n')
    checklist=write_checklist(statistics,manifest)
    unified=write_unified(unified_candidates,checklist)
    statistics['unified_rows_by_split']={k:len(v) for k,v in unified.items()}
    (OUT/'statistics.json').write_text(json.dumps(statistics,indent=2)+'\n')
    if not WRITE_LEGACY:
        import shutil
        shutil.rmtree(LEGACY,ignore_errors=True)
    lines=['# Dataset construction and distribution', '',
        f"Target: 500 articles. Acquired candidates: {len(manifest)}. Drafted: {draft_count}. Parent source-reviewed: **{len(final)}**. Physician-adjudicated: **0**.",
        '',f'Reviewed data: {len(patients)} patient descriptions, {len(facts)} facts and {len(figures)} caption records.',
        '',f'Full runtime-schema bundles reviewed by both annotator and parent: {len(article_bundles)} articles / {len(patient_bundles)} patients. These have a separate review gate from the partial fact annotations.',
        '', 'Counts below describe this selected corpus, not population prevalence or medical accuracy.', '']
    for title,key,denominator in [('Acquired article specialties','selected_primary_strata',len(manifest)),
        ('Reviewed article specialties','reviewed_primary_strata',len(final)),
        ('Patient count per reviewed article','reviewed_patient_counts',len(final)),
        ('Species per reviewed patient','reviewed_species',len(patients)),
        ('Clinical domain per reviewed fact','reviewed_facts_by_domain',len(facts)),
        ('Source license and version','selected_license_versions',len(manifest)),
        ('Selected article splits','selected_splits',len(manifest)),
        ('Reviewed article splits','reviewed_splits',len(final)),
        ('Overlapping reviewed article challenges','reviewed_article_challenges',len(final)),
        ('Caption attribution scope','reviewed_figure_scopes',len(figures)),
        ('Timeline occurrence status','reviewed_event_occurrences',statistics['event_count'])]:
        lines += [f'## {title}', '', f'Denominator: {denominator}.', '', '| Category | Count | Percent |','| --- | ---: | ---: |']
        for category,count in sorted(statistics[key].items()):
            lines.append(f'| {category} | {count} | {100*count/denominator:.1f}% |' if denominator else f'| {category} | {count} | n/a |')
        lines.append('')
    lines += ['## Interpretation', '',
        'Challenge flags come from article-level source review and can overlap; their percentages need not sum to 100%. Common workflow means the article contains familiar clinical care processes, not that its diagnosis is common in the population.', '',
        f"Facts retained for review: {statistics['review_only_facts']}; source-conflicted: {statistics['reviewed_source_conflict_facts']}; ambiguous: {statistics['reviewed_ambiguous_facts']}. These are annotation dispositions, not independently measured model errors.", '',
        'Patient counts mean separately described patient records. Identity ambiguity may remain, and sparse descriptions are not equivalent to complete clinical trajectories. Article selection strata are assigned for sampling; they are not exclusive medical diagnoses.', '',
        'Pixel-review counts require actual image inspection; reviewed empty inventories are counted separately. Caption attribution does not establish visual diagnostic correctness. Supplement links are retained with content_inspected=false until their contents are separately reviewed. Unavailable figures and unreviewed supplements prevent a complete-release claim.', '',
        '## EHR multimodal sidecars', '',
        f"Dual-reviewed supplement content manifests: {statistics['supplement_manifest_reviewed_articles']} articles / {statistics['reviewed_supplement_manifest_files']} files ({statistics['ehr_includable_supplement_files']} EHR-includable). Figure EHR labels: {statistics['figure_ehr_labeled_articles']} articles / {statistics['reviewed_figure_ehr_panels']} panels ({statistics['ehr_includable_figure_panels']} EHR-includable). Measurement series: {statistics['measurement_series_reviewed_articles']} articles / {statistics['reviewed_measurement_series']} series / {statistics['reviewed_measurement_points']} points.", '',
        'These sidecars (`supplement_manifests.jsonl`, `figure_ehr_labels.jsonl`, `measurement_series.jsonl`) separate images and files that would plausibly belong in a patient record (imaging, waveforms, pathology, clinical photos, patient data tables) from diagrams, checklists and literature content. They are informational checklist items, not completion gates. Peeks are literal machine copies; parsed numbers are deterministic parses of literal cells, never unit-converted. EHR-includability is an AI reference judgement, not physician adjudication.', '']
    (OUT/'DISTRIBUTIONS.md').write_text('\n'.join(lines)+'\n')
    (OUT/'SELECTION_PLAN.md').write_text((ROOT/'SELECTION_PLAN.md').read_text())
    print(json.dumps({k:statistics[k] for k in ['target_articles','acquired_candidate_articles','drafted_articles','parent_source_reviewed_articles','complete_500_article_release']}))


if __name__=='__main__':main()
