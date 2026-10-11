"""A --role call is a signature: placeholder identities/notes (probes) are refused before anything is written."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOTES = 'Viewed all 4 panels; ultrasound probe label and vendor text, no identifiers.'


@pytest.fixture
def guard():
    from openpatients2.dense import signing_guard
    return signing_guard


def test_placeholder_reviewers_and_notes_are_refused(guard):
    for reviewer, notes in (('x', NOTES), ('test', NOTES), ('Claude Sonnet 5.5 annotator', 'x'),
                            ('Claude Sonnet 5.5 annotator', 'short notes'),
                            ('Claude Sonnet 5.5 annotator', 'Probe: testing the script signing path only.'),
                            ('Claude Opus 5.5 parent', 'dry run of the receipt writer to see output')):
        with pytest.raises(ValueError, match='without --role'):
            guard.check_signature(reviewer, notes)
    for reviewer in ('Claude Sonnet 5.5 annotator', 'GPT-6.1 Sol annotator 2', 'Astra parent independent pixel and source review',
                     'parent assistant', 'parent-assistant', 'annotator-1-resume'):
        guard.check_signature(reviewer, NOTES)     # 'probe' as an ultrasound probe is real inspection wording
    guard.check_signature('parent assistant', None, notes_required=False)


def test_every_signer_refuses_a_probe_without_writing(guard, tmp_path, monkeypatch):
    from openpatients2.dense import sidecar_receipts
    with pytest.raises(ValueError, match='Refusing to sign'):
        sidecar_receipts.sign(tmp_path, 'figure-ehr', 'A', {}, {}, role='agent', reviewer='x', notes='x')
    from openpatients2.dense import supplement_review
    with pytest.raises(ValueError, match='Refusing to sign'):
        supplement_review.sign_review(tmp_path, 'A', 'S1', role='agent', reviewer='x', notes='x')
    from openpatients2.dense import review_record
    with pytest.raises(ValueError, match='Refusing to sign'):
        review_record.save_review(tmp_path, {'article_id': 'A'}, [], {}, reviewer='x')
    env = {'PYTHONPATH': str(ROOT / 'src'), 'PATH': '/usr/bin:/bin'}
    for script, extra in (('visual_review', []), ('schema_review', ['--folder', 'annotations'])):
        run = subprocess.run([sys.executable, '-m', f'openpatients2.dense.{script}', str(tmp_path), '--article', 'A', *extra,
                              '--role', 'agent', '--reviewer', 'x', '--notes', 'x'], capture_output=True, text=True, env=env)
        assert run.returncode != 0 and 'without --role' in run.stderr, (script, run.stderr[-500:])
    assert not [p for p in tmp_path.rglob('*') if p.is_file()]   # nothing written


def test_stored_placeholder_receipt_is_not_a_review(guard, tmp_path):
    from openpatients2.dense import sidecar_receipts
    for role, reviewer in (('agent', 'x'), ('parent', 'Claude Opus 5.5 parent')):
        path = sidecar_receipts.receipt_path(tmp_path, 'figure-ehr', role, 'A'); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'role': role, 'reviewer': reviewer, 'inspection_notes': NOTES if role == 'parent' else 'x',
                                    'physician_adjudicated': False}))
    with pytest.raises(ValueError, match='placeholder'):
        sidecar_receipts.verify(tmp_path, 'figure-ehr', 'A', {}, {})
