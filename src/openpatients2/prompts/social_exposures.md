Extract explicitly documented behavioral, occupational, environmental, and social circumstances relevant to clinical cohort definition.

Separate current, former, never, passive exposure, and unknown. 'Former smoker, quit ten years ago' is historical tobacco exposure, not current smoking and not no smoking history. A never-smoker assertion can use status=never with assertion=present because the documented status is affirmed. Do not infer never from silence. Preserve substance type, amount, frequency, duration, route where mentioned in amount_text, and time. pack_years is populated only when explicitly written; never calculate it from packs/day and years in this task.

Capture alcohol/substance use without adding a use-disorder diagnosis. A positive toxicology screen alone is an observation, not a confirmed pattern of substance use. Preserve patient-reported uncertainty and source attribution. Medication administration is not illicit use. Occupation/exposures, travel, diet/exercise, housing, food insecurity, social support, access barriers, and explicitly relevant sexual history can support research filters when documented.

Do not infer socioeconomic status, race, education, behavior, or adherence from occupation, location, insurance, name, or demographic groups. Do not reframe stigmatizing source language as a stronger clinical conclusion. Cadaver donation/preservation is case context, not social history. Formalin used on a specimen is not the patient's occupational chemical exposure.

Do not collect addresses, employers' names, or personal contact details. Include exact supporting text for stated exposures and meaningful named negatives; do not expand 'social history noncontributory' into blanket normality.


DECISION EXAMPLES AND NEGATIVE CONTROLS
Positive: "Former smoker" is an affirmed historical/former status, not current exposure. Explicit negative: "Never smoked" uses status=never with assertion=present because the affirmed attribute is never-use; it is not missing history. "Denies current tobacco use" is a scoped current negative and does not prove never-use. Missing: no tobacco/alcohol discussion means no such items, not never-use. "Social history unavailable" can support explicitly_unknown when no specific social facts exist. Do not compute pack-years or create substance-use disorders from exposures. Document housing/access barriers only when stated, never from demographic proxies.

An example in these instructions is not patient evidence. Populate only claims supported by the supplied record. Empty output is a valid, preferred answer when the task has no supported facts.
