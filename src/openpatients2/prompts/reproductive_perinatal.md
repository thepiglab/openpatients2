Extract pregnancy, obstetric, fetal, neonatal, fertility, and lactation facts with strict subject separation.

Capture explicitly documented pregnancy status, gravidity/parity wording, gestational age, delivery type, pregnancy loss, live birth/stillbirth, neonatal course, congenital findings, fertility history, and lactation. Do not infer pregnancy or absence of pregnancy from age, documented sex, medications, or a procedure. An explicit negative pregnancy test is a test result, not a timeless never-pregnant status.

Preserve gestational age as written: '32 weeks 4 days' remains text_value and unit/context; do not convert to a decimal week. gestational_age_weeks is only for an explicitly reported single numeric week value. Do not confuse gestational age with chronological postnatal age, corrected age, or the mother's age. Retain birth weight as birth_weight_text with its unit instead of converting it. Gravidity and parity are copied as text; do not infer the number of living children.

The index patient may be the mother, fetus, or newborn. Use index_patient only for the central case identified by the narrative; label other subjects mother/fetus/newborn and preserve their relationship. Do not merge maternal diabetes, fetal genotype, and infant measurements into one patient. Multiple fetuses or infants need clear attribution; mark limited rather than combining them if identities are ambiguous.

Anatomical findings from fetal specimens or cadavers must not become current pregnancy complications in an unrelated patient. Retain uncertainty, timing, and explicit outcomes, including termination, loss, or neonatal death; do not infer a clinical death event from a specimen description alone.


DECISION EXAMPLES AND NEGATIVE CONTROLS
Positive: explicitly documented gravidity/parity remains verbatim, and 32 weeks 4 days stays text rather than an invented decimal. Explicit negative: "Not pregnant" is a time-scoped absent pregnancy statement; a negative pregnancy test is separately a performed negative observation. Missing: no reproductive history means items=[], not_documented, even for male/older patients; do not infer not_applicable from demographics. Maternal diabetes is not neonatal diabetes. Keep terminations, stillbirth, neonatal death, and live birth distinct; a fetus in an anatomy specimen is not automatically a documented clinical stillbirth outcome.

An example in these instructions is not patient evidence. Populate only claims supported by the supplied record. Empty output is a valid, preferred answer when the task has no supported facts.
