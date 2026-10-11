"""Shared guard for every receipt-signing path (a `--role` call IS a signature).

Every signer (visual_review, schema_review, supplement_review, supplement_manifest_review,
figure_ehr_review, figure_privacy_review, measurement_series_review via sidecar_receipts.sign,
review_record.save_review) calls `check_signature` before writing a receipt. It rejects
placeholder identities ("x", "test") and placeholder notes, so a probe cannot overwrite a
valid receipt. To validate or dry-run, run the same script WITHOUT --role.
"""
import re

# A reviewer identity names the model/system that actually inspected the content ...
REVIEWER_PATTERN = re.compile(r'\b(claude|opus|sonnet|haiku|fable|gpt|astra|gemini)\b', re.I)
# ... or is one of the historical identities already present in signed receipts.
HISTORICAL_REVIEWERS = frozenset({'parent assistant', 'parent-assistant', 'annotator-1-resume'})
MIN_NOTES = 20
PLACEHOLDER_NOTES = re.compile(r'^\W*(x+|test(ing)?|todo|tbd|n/?a|none|null|placeholder|lorem ipsum.*|asdf\w*|dummy|'
                               r'fixture|probe|ok|done|reviewed|signed|\.+|-+)\W*$', re.I)
# Wording of a probe/test run ('probe' alone is not: ultrasound probe labels are real inspection notes).
PROBE_WORDS = re.compile(r'\b(dry[- ]?run|smoke[- ]?test|(probe|probing|test|testing) (the |this )?'
                         r'(script|command|signing|signer|receipt|cli))\b', re.I)
HINT = ('A --role call is a signature that overwrites the existing receipt. To validate, run the script without --role; '
        'sign only after you personally inspected the content.')


def signature_errors(reviewer, notes, *, notes_required=True):
    errors = []
    name = (reviewer or '').strip()
    if not name:
        errors.append('reviewer is required')
    elif name not in HISTORICAL_REVIEWERS and not REVIEWER_PATTERN.search(name):
        errors.append(f'reviewer {name!r} is not an accepted reviewer identity (name the model, e.g. '
                      f'"Claude Sonnet 5.5 annotator", or use a historical identity {sorted(HISTORICAL_REVIEWERS)})')
    if notes_required:
        text = (notes or '').strip()
        if len(text) < MIN_NOTES or PLACEHOLDER_NOTES.match(text):
            errors.append(f'notes must describe the actual inspection (at least {MIN_NOTES} characters, not a placeholder)')
        elif PROBE_WORDS.search(text):
            errors.append('notes describe a probe/test run; never sign to test a script')
    return errors


def check_signature(reviewer, notes, *, notes_required=True):
    """Raise ValueError (with the no---role hint) when the reviewer/notes look like a placeholder or probe."""
    errors = signature_errors(reviewer, notes, notes_required=notes_required)
    if errors:
        raise ValueError('Refusing to sign: ' + '; '.join(errors) + '. ' + HINT)


def receipt_identity_errors(receipt):
    """Read-time check: a stored receipt signed with a placeholder identity/notes is not a review."""
    return signature_errors(receipt.get('reviewer'), receipt.get('inspection_notes'))

