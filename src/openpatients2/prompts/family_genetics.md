Extract family disease history and explicit genetic test/inheritance facts, keeping subject attribution precise.

A mother's breast cancer or brother's sudden death is a family_member fact with the relationship retained. It is not a diagnosis in the index patient. Keep stated age at diagnosis, maternal/paternal lineage, gene/variant, inheritance, and interpretation when available. Multiple affected relatives are distinct items. A broad 'family history unremarkable' should not be expanded into explicit absence of every disease; a named negative family history may be represented with assertion=absent.

A genetic test in the index patient has subject=index_patient, not family_member. Distinguish somatic from germline only when stated. Do not infer carrier status, penetrance, syndromic diagnosis, or hereditary cancer from a suggestive pedigree. A variant of uncertain significance is not a pathogenic variant. A negative test does not exclude every genetic disease. Preserve HGVS-like strings and gene names exactly; do not create variant identifiers that are not present.

Do not transfer fetal/newborn genetic results to the mother. For mother/fetus/newborn as distinct narrative subjects, use their explicit subject label and explain ambiguity. Record consanguinity or inheritance patterns only as stated. Screening recommendations based on family risk are plans, not positive results.

Evidence must establish both the relative or test subject and the clinical/genetic fact. Do not include distant literature comparisons as family members. Names of relatives are unnecessary; use relationship terms only.


DECISION EXAMPLES AND NEGATIVE CONTROLS
Positive: "Mother developed breast cancer at 45" belongs to family_member with relative=mother, not the index patient's conditions. Explicit negative: "No family history of colorectal cancer" is a scoped absent family_condition item, not a negative family history for every hereditary syndrome. Missing: no family/genetics section produces no items, not an absence of inherited risk. "Family history unavailable because adopted" supports explicitly_unknown only for the unknown family history; do not delete any documented genetic test. A variant of uncertain significance is not pathogenic, and a tumor-only finding is not automatically germline.

An example in these instructions is not patient evidence. Populate only claims supported by the supplied record. Empty output is a valid, preferred answer when the task has no supported facts.
