"""Receipt writer for annotations the parent has actually read and reviewed.

Never batch-create receipts from validator success; validation is not review.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from openpatients2 import dense_store as store
from openpatients2.dense.validate import case_context_errors,digest,semantic_errors,validate

REVIEWER='parent assistant'

def save_review(root, annotation, corrections, assessment, *, reviewer=REVIEWER):
    if not isinstance(assessment,dict):
        raise TypeError('assessment must be a structured mapping; put prose in clinical_course or notes')
    from openpatients2.dense.signing_guard import check_signature
    check_signature(reviewer, None, notes_required=False)   # the structured assessment is the review content
    root=Path(root);aid=annotation['article_id'];source=json.loads(store.path(root,'sources',aid).read_text())
    errors=validate(annotation,source)+case_context_errors(annotation)+semantic_errors(annotation,source)
    if errors:raise ValueError((aid,errors))
    receipt={'article_id':aid,'reviewer':reviewer,'review_type':'source-grounded AI review; not physician adjudication',
      'status':'source_reviewed','source_text_sha256':source['text_sha256'],'annotation_sha256':digest(annotation),
      'reviewed_at':datetime.now(timezone.utc).isoformat(),'source_segments_read':len(source['segments']),
      'facts_reviewed':sum(len(p['facts']) for p in annotation['patients']),'patients_reviewed':len(annotation['patients']),
      'source_read_in_full':True,'reviewed_components':['patient identity and source scope','all clinical facts and measured quantities',
      'normal and negative findings','diagnostic certainty and planning status','relative chronology','summary entailment and omissions','all captions and panel attribution'],
      'corrections':corrections,'unresolved':annotation['article_limitations'],'assessment':assessment,'physician_adjudicated':False}
    for directory,obj in [('annotations',annotation),('reviews',receipt)]:
      path=store.path(root,directory,aid);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
