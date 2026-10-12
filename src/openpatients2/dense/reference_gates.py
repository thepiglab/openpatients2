"""Run the RUNTIME checker objects on a reference article (same gates a local-LLM output must pass).

A reference that its own runtime pipeline would reject is not a usable target. This module
applies, to the canonical annotation and its signed sidecars, exactly the checkers the
automated pipeline uses:

  clinical  shared semantic rules on annotation facts (validate.semantic_violations), the
            runtime task validator per patient section on the patient packet
            (validation.validate, target_patient=True), cross-section record rules,
            roster (incl. ROSTER-ORDER), summary, timeline_v2 contract + structural audit;
  media     figure joint/refined visual/panel attribution validators, text attribution,
            EHR label / privacy / supplement-manifest / measurement-series round trips
            through ehr_tasks checkers (series with reviewed supplement transcriptions).

Used by schema_review --role (clinical gates must pass before a receipt is written) and
export (failing articles are withheld; reasons go to article_checklist.json).

    op2 dense reference_gates \
        benchmarks/dense500 --article ID [--folder annotations] [--groups clinical media]
"""
import argparse
import copy
import json
from pathlib import Path

from openpatients2 import dense_store as store

GROUPS = ('clinical', 'media')


def _json(path):
    return json.loads(path.read_text()) if path.exists() else None


def _add(out, gate, detail, patient_id=None):
    out.append({'gate': gate, 'patient_id': patient_id, 'detail': str(detail)[:500]})


def clinical_gates(source, annotation, target):
    """Errors/warnings from the clinical runtime checkers. `target` is the schema submission (or None)."""
    from openpatients2.article_tasks import check_article_task, patient_packet
    from openpatients2.evidence_recovery import packet_segments
    from openpatients2.longitudinal import PatientTimeline, audit_timeline
    from openpatients2.schemas import TASK_MODELS
    from openpatients2.semantic_rules import ERROR, record_violations
    from openpatients2.validation import validate
    from openpatients2.dense.validate import semantic_violations
    errors, warnings = [], []
    for v in semantic_violations(annotation, source):
        _add(errors if v.severity == ERROR else warnings, f'annotation.{v.rule_id}', v.text())
    if target is None:
        _add(errors, 'schema_submission.missing', 'no schema-submissions target')
        return errors, warnings
    article = dict(source)
    roster = target['roster']
    try:
        check_article_task('roster', copy.deepcopy(roster), article)
    except (ValueError, KeyError) as exc:
        _add(errors, 'roster.check_article_task', exc)
    for bundle in target['patients']:
        pid = bundle['source']['patient_target']['patient_id']
        patient = next((x for x in roster['patients'] if x['patient_id'] == pid), None)
        try:
            record = patient_packet(article, roster, patient, scope='whole_article')
            text, segments = record['text'], packet_segments(record)
        except (ValueError, KeyError, TypeError) as exc:
            _add(errors, 'packet.patient_packet', exc, pid); continue
        for task in TASK_MODELS:
            result = validate(task, copy.deepcopy(bundle['sections'][task]), text, segments=segments, target_patient=True)
            for e in result.errors:
                _add(errors, f'validate.{task}', e, pid)
            for w in result.warnings:
                if '[' in w:   # rule-keyed heuristics only; generic lexical warnings stay in quality
                    _add(warnings, f'validate.{task}', w, pid)
        for v in record_violations(bundle['sections']):
            if v.rule_id != 'CTX-PER-PATIENT':   # already reported by validate(case_context)
                _add(errors if v.severity == ERROR else warnings, f'record.{v.rule_id}', v.text(), pid)
        timeline = bundle['companions']['timeline_v2']
        try:
            graph = PatientTimeline.model_validate(timeline)
            facts = {fid: graph.record_id for e in timeline['events'] for fid in e['fact_ids']}
            audit = audit_timeline(graph, {source['article_id'] + ':jats': {s['segment_id']: s['text'] for s in source['segments']}}, facts)
            if not audit['structural_source_gates_passed']:
                _add(errors, 'timeline.audit', [i for i in audit['issues'] if i['severity'] == 'block'][:3], pid)
        except (ValueError, KeyError, TypeError) as exc:
            _add(errors, 'timeline.model', exc, pid)
        try:
            check_article_task('summary', copy.deepcopy(bundle['companions']['summary']), article, patient)
        except (ValueError, KeyError, TypeError) as exc:
            _add(errors, 'summary.check_article_task', exc, pid)
    return errors, warnings


def _panel_label_warnings(root, aid, visual, warnings):
    """FIG-LABELS (warning): positional/patient-name panel labels in the visual submission and the
    figure-EHR label / privacy-flag sidecars that reuse its panel IDs; one warning per figure/label."""
    from openpatients2.semantic_rules import panel_label_violations
    found = {}
    docs = [('visual-submissions', visual)] + [(folder, _json(store.path(root, folder, aid)))
                                                for folder in ('figure-ehr-labels', 'figure-privacy-flags')]
    for folder, doc in docs:
        for f in (doc or {}).get('figures', []):
            panels = ((f.get('analysis') or {}).get('visual') or {}).get('panels') if folder == 'visual-submissions' else f.get('panels')
            for v in panel_label_violations(panels, f['figure_id']):
                label = panels[int(v.path.rsplit('/', 1)[1])]['panel']
                found.setdefault((f['figure_id'], label), (v, []))[1].append(folder)
    for (figure_id, label), (v, folders) in found.items():
        _add(warnings, 'figure.FIG-LABELS', f'{v.text()} (in {", ".join(folders)})')


SIDECAR_FOLDERS = (('visual', 'visual-submissions'), ('figure_ehr', 'figure-ehr-labels'),
                   ('privacy', 'figure-privacy-flags'), ('supplement_manifest', 'supplement-manifests'),
                   ('measurement_series', 'measurement-series'))


def _glued_sidecar_warnings(root, source, visual, warnings):
    """GLUED-TEXT (warning): reviewer-written free text in the article's visual and EHR sidecars."""
    from openpatients2.semantic_rules import sidecar_glued_violations
    text = '\n'.join(s['text'] for s in source['segments'])
    for kind, folder in SIDECAR_FOLDERS:
        doc = visual if kind == 'visual' else _json(store.path(root, folder, source['article_id']))
        for v in sidecar_glued_violations(kind, doc or {}, text):
            _add(warnings, f'{folder}.GLUED-TEXT', v.text())


def media_gates(root, source, annotation, target):
    """Figure and EHR-sidecar runtime checkers on the article's reviewed (or submitted) files."""
    from openpatients2 import ehr_tasks
    from openpatients2.figure_attribution import validate_figure_review
    from openpatients2.figure_visuals import validate_joint, validate_visuals
    from openpatients2.pilot_review import panel_aligned_attribution
    aid = source['article_id']; root = Path(root)
    errors, warnings = [], []
    roster = {'patients': [{'patient_id': p['patient_id']} for p in annotation['patients']]}
    visual = _json(store.path(root, 'visual-submissions', aid))
    for f in (visual or {}).get('figures', []):
        if f['status'] != 'inspected':
            continue
        analysis = f['analysis']
        for name, check in (('figure.validate_joint', lambda: validate_joint(analysis, source, roster, f['figure_id'])),
                            ('figure.validate_visuals_refined', lambda: validate_visuals(analysis['visual'], source, f['figure_id'], refined=True)),
                            ('figure.panel_aligned_attribution', lambda: panel_aligned_attribution(
                                analysis['attribution'], analysis['visual'], source, roster, f['figure_id']))):
            try:
                check()
            except (ValueError, KeyError, TypeError) as exc:
                _add(errors, name, f"{f['figure_id']}: {exc}")
    for fr in (target or {}).get('figure_attribution', []):
        try:
            validate_figure_review(fr, source, roster, fr['figure_id'])
        except (ValueError, KeyError, TypeError) as exc:
            _add(errors, 'figure.text_attribution', f"{fr['figure_id']}: {exc}")
    rows = {f['figure_id']: f for f in (visual or {}).get('figures', [])}
    _panel_label_warnings(root, aid, visual, warnings)
    _glued_sidecar_warnings(root, source, visual, warnings)
    for folder, task, status, checker, assemble in (
            ('figure-ehr-labels', 'figure_ehr_label', 'labeled', ehr_tasks.check_figure_ehr, ehr_tasks.labels_document),
            ('figure-privacy-flags', 'figure_privacy_flags', 'flagged', ehr_tasks.check_privacy, ehr_tasks.flags_document)):
        sidecar = _json(store.path(root, folder, aid))
        if not sidecar or not visual:
            continue
        outs = []
        for fl in sidecar['figures']:
            if fl['status'] != status:
                continue
            try:
                outs.append(checker(ehr_tasks.output_from_sidecar(task, fl), rows[fl['figure_id']]))
            except (ValueError, KeyError, TypeError) as exc:
                _add(errors, f'ehr.{task}', f"{fl['figure_id']}: {exc}")
        try:
            assemble(source, visual, outs)
        except (ValueError, KeyError, TypeError) as exc:
            _add(errors, f'ehr.{task}.document', exc)
    manifest = _json(store.path(root, 'supplement-manifests', aid))
    if manifest:
        acquisition = _json(store.path(root, 'supplement-assets', aid))
        if acquisition is None:
            _add(warnings, 'ehr.supplement_assets_missing', 'manifest without acquisition record')
        else:
            try:
                ehr_tasks.check_supplements(ehr_tasks.output_from_sidecar('supplement_classification', manifest),
                                            source, annotation, acquisition)
            except (ValueError, KeyError, TypeError) as exc:
                _add(errors, 'ehr.supplement_classification', exc)
    series = _json(store.path(root, 'measurement-series', aid))
    if series:
        from openpatients2.dense.measurement_series_review import reviewed_transcriptions
        try:
            transcriptions = reviewed_transcriptions(root, aid)
        except (ValueError, KeyError, TypeError, FileNotFoundError) as exc:
            transcriptions = {}; _add(warnings, 'ehr.series_transcriptions', exc)
        try:
            # The reference's own annotator-defined cells (supplement transcriptions, literal article-text
            # cells such as table-footnote legends cited by cell_status) are added to the runtime draft so the
            # round trip exercises check_series; validate_series re-checks every cell literally.
            draft = ehr_tasks.series_draft(source, transcriptions,
                                           [c for c in series['cells'] if c['origin'] in ('supplement_transcription', 'article_text')])
            ehr_tasks.check_series(ehr_tasks.output_from_sidecar('measurement_series_extraction', series),
                                   source, annotation, draft, transcriptions)
        except (ValueError, KeyError, TypeError) as exc:
            _add(errors, 'ehr.measurement_series_extraction', exc)
    return errors, warnings


def run_gates(root, aid, *, annotation=None, folder='annotations', groups=GROUPS):
    """{'passed', 'errors', 'warnings', 'groups'} for one article."""
    root = Path(root)
    source = json.loads(store.path(root, 'sources', aid).read_text())
    annotation = annotation if annotation is not None else json.loads(store.path(root, folder, aid).read_text())
    target = _json(store.path(root, 'schema-submissions', aid))
    errors, warnings = [], []
    if 'clinical' in groups:
        e, w = clinical_gates(source, annotation, target); errors += e; warnings += w
    if 'media' in groups:
        e, w = media_gates(root, source, annotation, target); errors += e; warnings += w
    return {'article_id': aid, 'groups': list(groups), 'passed': not errors, 'errors': errors, 'warnings': warnings}


def verdict(report):
    """One human-readable line: warnings are heuristics for a reviewer and never block."""
    w, e = len(report['warnings']), len(report['errors'])
    if report['passed']:
        return f"{report['article_id']}: PASSED ({w} warning{'s' if w != 1 else ''}, non-blocking)"
    return (f"{report['article_id']}: FAILED ({e} blocking error{'s' if e != 1 else ''}; "
            f"{w} warning{'s' if w != 1 else ''}, non-blocking)")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('root', type=Path); p.add_argument('--article', required=True)
    p.add_argument('--folder', default='annotations'); p.add_argument('--groups', nargs='*', default=list(GROUPS))
    args = p.parse_args()
    report = run_gates(args.root, args.article, folder=args.folder, groups=tuple(args.groups))
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print(verdict(report))
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
