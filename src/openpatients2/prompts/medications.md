Extract drugs and pharmacologic regimens documented for the index patient, including clinically relevant treatment changes.

The action field is essential: prescribed/ordered/planned is not administered; held is not permanently discontinued; current/home medication is not necessarily given in hospital. 'Would consider steroids if worse' is conditional and planned, not a treatment received. 'No prior chemotherapy' is an explicit negative history. Do not treat drugs mentioned only in a review, hypothetical answer option, or alternative treatment discussion as patient exposures.

Preserve the name or abbreviation exactly. Do not expand a regimen acronym or brand into ingredients unless the ingredients are actually written. Record dose_text as stated; populate numeric dose and dose_unit only when explicit and unambiguous. Preserve route, frequency, duration, indication, and regimen when stated. Do not assume oral route, once-daily frequency, standard adult dose, or recommended duration. A prescription amount is not necessarily the administered dose. Dose changes at different times are separate items.

Distinguish medication effects from allergies; adverse reactions are captured in the allergy task. Do not assign a diagnosis because the patient received a drug. Contrast agents, anesthetics, and transfusion products may be captured when administered to a clinical patient and relevant, but specimen preservatives such as formalin in cadaver dissection are NOT medications. Fluids and nutrition therapies should only be represented when explicitly administered as clinical treatment.

Retain start/stop/hold context and evidence. Record active chemotherapy in this section; the oncology task separately links explicitly stated intent, line of therapy, and tumor. Do not infer oncology intent from the agent name.


DECISION EXAMPLES AND NEGATIVE CONTROLS
Positive: "Started warfarin 5 mg nightly" supports started, dose and frequency. Explicit negative: "Takes no medications" can be one scoped absent medications statement with name preserving the wording and action=unknown; it is not one negative item for each drug. Missing: no medication list yields items=[], not_documented. "Medication history unavailable" supports explicitly_unknown with its quotation when no drugs are otherwise documented. "Warfarin was withheld" is an affirmed held action, NOT administered and not a never-used assertion. Do not infer drug ingredients, line of therapy, renal dosing, or medication adherence; ranges and titration instructions remain text unless an exact individual dose is documented.

An example in these instructions is not patient evidence. Populate only claims supported by the supplied record. Empty output is a valid, preferred answer when the task has no supported facts.
