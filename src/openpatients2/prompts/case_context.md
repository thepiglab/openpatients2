Identify what kind of material this record contains before any clinical cohort assignment.

Use the supplied source_kind as provenance, not as evidence that all text concerns a living patient. A PMC-derived description can be a cadaver dissection, anatomy teaching case, animal experiment, or article describing several patients. Recognize formalin, dissection specimens, donated cadavers, autopsy/anatomy material, and in-vitro experiments. Do not turn cadaver status into social history or a clinical death outcome. An autopsy following a described patient's clinical course differs from a cadaver-only anatomical description.

Classify a single real case report as clinical_case; a real EHR summary as clinical_record; educational questions as exam_vignette; and an explicitly synthetic clinical scenario as synthetic_vignette. An examination vignette is NOT a verified patient encounter. If multiple unrelated index cases are mixed without a clear primary case, use multi_patient and multiple_index_patients=true. Relatives mentioned only in family history do not make a case multi_patient. Mother-and-infant narratives require careful index-subject identification; use limited when ambiguous rather than combining their measurements.

index_subject_description should identify the case role without a name: for example, 'the 63-year-old man presenting with hemoptysis'. Quote the source that establishes the subject. Keep it null when not identifiable. Mark human/nonhuman only when supported by context. Describe the care setting and chief complaint only when stated. Do not infer the admitting specialty from the diagnosis. subject_state distinguishes a person alive at presentation who later died from a pre-existing cadaver specimen. Death during the described course does not invalidate an otherwise clinical case.

For unknown or non-case material, return unknown/null where appropriate, explain the limitation, and do not manufacture a patient. At least one evidence quotation is expected for a confident non-unknown classification.


DECISION EXAMPLES AND NEGATIVE CONTROLS
Positive: a clearly single-person case may be clinical_case with quotations identifying the central subject. Explicit uncertainty: "The account combines several unidentified patients" is a multi_patient/limited context, not one synthetic composite person. No usable context: case_kind=unknown, species=unknown, multiple_index_patients=null, subject_state=unknown, evidence=[], documentation_status=not_documented. Do not create a living human from an article identifier or a missing title. If any context is supported, leave only unsupported attributes unknown/null and use documented.

An example in these instructions is not patient evidence. Populate only claims supported by the supplied record. Empty output is a valid, preferred answer when the task has no supported facts.
# Article-derived patient packets

When TARGET_PATIENT_JSON is supplied, classify that single selected patient.
The source article can contain other patients: this does not make the target
record multi_patient. Set multiple_index_patients=false and use clinical_case
for a described human case or animal for a nonhuman case. Never count literature
review cases as additional index patients. Preserve species explicitly.
