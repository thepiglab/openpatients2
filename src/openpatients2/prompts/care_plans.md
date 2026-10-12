Extract explicitly stated follow-up, referral, monitoring, counseling, rehabilitation and goals-of-care actions, whether still planned or already carried out; action_status records which.

A plan is not a completed action: use action_status=completed only when the source reports the action was carried out. Distinguish recommended, planned, scheduled, declined, completed, and unknown status. Include the target condition/test, responsible specialty, urgency, and temporal instruction only when stated. Do not infer a specialist from a diagnosis or imply that an order was fulfilled. Conditional escalation requires assertion=conditional and its condition in evidence.

Capture follow-up intervals, referrals, monitoring parameters, counseling, rehabilitation plans, advance directives, and stated goals of care when present. A discussion of standard clinical practice or a research author's recommendation is not necessarily this patient's plan. Educational examination questions asking for 'the next step' are not actual plans; do not answer them. Routine discharge advice is extracted only as documented, not invented.

Drug prescriptions and planned procedures have dedicated tasks; avoid duplicating their entire dosing/procedure content here, while retaining distinct monitoring/referral components. Keep declined plans separate from absent indications. A do-not-resuscitate statement is a goals-of-care fact, not an outcome of death. Evidence should bind the planned action to the correct patient and timeframe.


DECISION EXAMPLES AND NEGATIVE CONTROLS
Positive: "Repeat CT in 6 weeks" is a planned monitoring action, not a completed scan/result. Explicit negative: "No further follow-up planned" is one source-supported negative plan; it does not establish that future care never occurred. Missing: no plan statement gives items=[], not_documented; do not supply standard-of-care recommendations. "Consider biopsy if the lesion grows" is conditional rather than a scheduled/completed procedure. DNR is a goals-of-care decision, not death, and an educational question asking what to do next is not the patient's care plan.

An example in these instructions is not patient evidence. Populate only claims supported by the supplied record. Empty output is a valid, preferred answer when the task has no supported facts.

SHARED RULES (generated from openpatients2/rules/annotation_rules.yaml; rule IDs in brackets):
- [SECTION-CAREPLANS] care_plans holds follow-up, referral, monitoring, counseling, rehabilitation and goals-of-care actions, whether still planned or already carried out; action_status records which (planned, recommended, scheduled, declined, completed, unknown). Drug administration belongs in medications and operations/diagnostic procedures in procedures_devices; what a follow-up visit found belongs in outcomes/observations.
END SHARED RULES
