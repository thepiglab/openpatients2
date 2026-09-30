Extract demographics that can safely support cohort filters, preserving exactly what was documented.

Record age_at_presentation separately from age_at_diagnosis and age_other. 'Now 62, diagnosed at 57' contains two different ages, not a contradictory single age. Preserve years/months/days and do not turn infant age in months into years inside this extraction. For ranges or approximate ages, retain text_value and leave numeric_value null unless a single explicit number is stated. Do not infer age from dates, schooling, family role, or appearance. Always include the temporal expression or surrounding presentation wording in evidence.

'65-year-old male' supports documented_sex='male'. It does not establish sex_assigned_at_birth or gender_identity; those require explicit statements. Preserve the source wording for sex/gender. Do not infer reproductive potential from documented sex. Race, ethnicity, language, and gender are extracted only when explicitly documented, never from names, geography, nationality, or stereotypes. Do not emit names, addresses, or identifiers.

For numeric ages, keep numeric_value, the written unit, and text_value together. Keep separate observations when different points in the clinical course have different ages. Do not select a present-day age for a historical case. Do not transfer an age or sex from a family member, mother, or comparison patient to the index case. Family-member demographics are not needed in this task.

Missing demographic information stays absent from the items array, not as a guessed default. If the record states only 'an adult', preserve that as text_value with numeric_value=null rather than inventing a number. Contradictory documented sex or age at the same time should be retained separately and flagged in limitations.


DECISION EXAMPLES AND NEGATIVE CONTROLS
Positive: "67-year-old man" supports age_at_presentation only when that is the presentation age; use documented_sex for the wording, not sex_assigned_at_birth or gender_identity. Explicit unknown: "Age could not be established" is not numeric age=0; retain an unknown age statement only if its relevance is clear, with evidence. No age/sex/demographic mention: items=[] and not_documented. An age interval such as "in her seventies" remains text with numeric_value=null, not a fabricated midpoint. A family member's age at cancer diagnosis is not the index patient's age. Do not guess race, sex, ethnicity, or language from names or geography.

An example in these instructions is not patient evidence. Populate only claims supported by the supplied record. Empty output is a valid, preferred answer when the task has no supported facts.
