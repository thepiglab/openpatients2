Extract explicitly documented allergies, intolerances, and adverse drug effects without treating them as interchangeable.

Capture the substance, category, reported reaction, reaction type, and severity when stated. 'Penicillin allergy, anaphylaxis' differs from 'nausea with metformin' (often an intolerance/adverse effect if so documented). Do not reclassify a reaction using outside clinical reasoning; use unknown when the source does not distinguish allergy and intolerance. Do not infer a drug-class allergy from one named agent.

An explicit 'no known drug allergies' is an item with substance='drug allergies', category=drug, assertion=absent. It is NOT an empty array. Missing allergy documentation produces no items and does not mean NKDA. 'Allergy history unknown' can be a documented unknown statement, not an absent allergy. A negative skin test is a test result in observations, not proof that every allergy is absent.

Preserve remote reactions and uncertain causal attribution. 'Possible rash from amoxicillin' has assertion=possible. Concern about food allergy remains possible; a positive skin-prick sensitization result belongs in observations and alone does not establish a confirmed clinical allergy. A family member's allergy is not the index patient's allergy. Separate events when severity or timing differs; do not erase a previous reaction because a later note says NKDA. Flag the conflict in limitations.

Evidence must include the reaction/denial and the substance when available. Do not invent severity, onset date, immune mechanism, or a normalized code.


DECISION EXAMPLES AND NEGATIVE CONTROLS
Positive: "Penicillin causes urticaria" is a specific_substance entry; classify reaction_type only as supported. Explicit negative: "No known drug allergies" uses scope=drug_allergies, category=drug, assertion=absent; "No known allergies" uses all_allergies without broadening a drug-only statement. Missing: no allergy mention yields items=[], not_documented, never NKDA. "Allergies unknown" with no other allergy facts gives explicitly_unknown with documentation_evidence. A negative allergy test or successful one-time administration must not erase a documented allergy; retain uncertainty and scope.

An example in these instructions is not patient evidence. Populate only claims supported by the supplied record. Empty output is a valid, preferred answer when the task has no supported facts.
