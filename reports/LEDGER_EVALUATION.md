# Evidence ledger evaluation

`openpatients2.ledger_evaluation` supplies CPU preflight and paired evaluation for
chunk-first extraction. Evaluate the final typed patient bundles; chunk routing
hypotheses are not accepted clinical propositions.

```python
import gzip
import json
from pathlib import Path
from openpatients2.ledger_evaluation import preflight, paired_evaluate, training_feedback, objective_adequacy

root = Path('benchmarks/patient-bundle')
reference = json.loads((root / 'reference-reviewed.json').read_text())
articles = []
for name in ('articles.jsonl.gz', 'additional-articles.jsonl.gz'):
    with gzip.open(root / name, 'rt') as stream:
        articles.extend(json.loads(line) for line in stream if line.strip())

assert preflight(reference, articles)['passed']
adequacy = objective_adequacy(reference)  # CPU inventory; GEPA is disabled in this campaign
report = paired_evaluate(reference, baseline_patients, candidate_patients,
                         baseline_discovery=baseline_rosters,
                         candidate_discovery=candidate_rosters)
# This bounded training-only report may enter reflection. Paired test reports may not.
feedback = training_feedback(reference, candidate_patients)
```

The existing source-reviewed reference contains 259 required and 44 forbidden
probes across 31 articles. Its train/validation/test groups contain 15/7/9 articles;
they are development sources with earlier pilot exposure. Both arms retain the
same reference, source hashes, identity alignment, split, seed and clinical probe
denominators. `paired_evaluate` returns the existing `score_bundle` control beside
attribute diagnostics for each split, gained/lost check IDs, and an article
bootstrap interval for paired retention differences. Patient IDs and PMC versions
cannot leak across splits. Negative articles still enter the standard bundle
identity score; the clinical delta interval covers articles with clinical probes.

Each required probe matches a complete generated item. Partial attribute credit
uses one item with a matching concept anchor; fields cannot be pooled across
items. The evaluator preserves absent/possible assertions and planned/completed
actions, exact measurement magnitudes and case-sensitive SI prefixes. It counts
missing required probes even when delivery is absent or source binding fails.
A matching fact under another patient in the same article produces an ownership
diagnostic; temporal metrics require source-bound, distinct event nodes and
supported direction. Known forbidden assertions produce explicit unsupported
modifier diagnostics. Unchecked modifiers remain unreviewed.

The dense feedback score combines exact probe retention, constrained attribute
retention and reviewed ordering retention, with penalties for known forbidden
claims and reversed ordering. It is a diagnostic optimization signal, not a
production promotion threshold. Schema acceptance is reported separately, clinical
precision stays null, and `clinical_accuracy_established` remains false. Gold
alternatives select one reviewed representation; literal evidence is excluded
from matching. Automatic mismatch diagnostics still need paraphrase adjudication.

`counterfactual_fixtures` and `score_counterfactual` supply six synthetic software
controls: procedure versus lesion laterality, allergy concern versus affirmation,
repeated RAI dose/year swaps, patient swaps, SI-prefix case, and negative/planned
assertions. Controls include output-order/background invariance, quote-only traps,
and one-to-one matching. They deliberately do not augment clinical gold, reference
counts or experimental splits. Existing RAI clinical probes label doses but do not
constrain every year; reviewed timeline probes constrain ordering. Synthetic year
swap sensitivity must not be reported as additional source-reviewed clinical
accuracy.

For a focused comparison, freeze one chunk-first implementation and replay it
against the unchanged baseline on the existing split with matched seeds. Inspect
paired losses and owner/time/modifier errors before considering any rollout. New
external performance claims require new source-reviewed articles held out from
both prompt development and optimizer reflection. No model downloads, calls, or
GEPA search are performed by this module.

`objective_adequacy` inventories each invoked new prompt family's existing
downstream probes, distinct annotated articles, domains and reviewed hard
negatives. It separates surrogate count minima from direct reviewed map/audit
verdict labels, which currently number zero. The episode objective has 24 reviewed
training ordering relations across four articles and 14 validation relations
across three articles, but lacks reviewed negative episode/fact links. It fails
the report's conservative minimum of five training articles and three reviewed
hard negatives. Synthetic failures do not fill those gaps. The source-map and
attribute families have enough downstream surrogate probes for a future bounded
study, but their intermediate clinical judgments remain unadjudicated.

`routing_objective` checks whether the final route retains each reviewed fact's
original source-witness segments, and reports the selected source fraction beside
that retention. A structurally valid map can still omit a clinically necessary
passage. Full-source fallback can retain every witness without improving source
reduction. Evaluate routing after generation using unchanged gold; never add the
reviewed witnesses to model requests. This is a retrieval diagnostic, not proof
of patient attribution, field entailment or clinical correctness. Callers must
align live patient IDs using reviewed identity evidence before scoring routes;
missing aligned patients count as failed retention in the fixed denominator.

If the paired architecture result warrants a later prompt study, mutate one
invoked family at a time, preserve schema enums and exact requested component
keys, reflect on training failures only, select on validation, and freeze the
paired test baseline. Use a small fixed metric-call budget with unsaturated
saved failures. The current short architecture campaign has no GEPA stage.

Validation: `tests/test_ledger_evaluation.py` exercises reviewed-reference CPU
preflight, field semantics, hash binding, one-item matching, temporal direction,
paired split isolation, synthetic controls, routing omissions, objective label
adequacy and preservation of unchallenged clinical fields/evidence. The ledger,
evidence-routing and episode tests pass together (70 tests total).
