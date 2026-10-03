# Example extraction from the latest PMC / Glimmer campaign

This is a readable view of one patient bundle from campaign `run-20261003-000942`. It is the **targeted extraction** for **Patient 2** in a three-patient case series. The source record and evidence quotes are taken from the saved output; tables below only flatten the JSON for readability.

## Source and run

| Field | Value |
| --- | --- |
| Model | `RedHatAI/Muse-Glimmer-30B-FP8-block` |
| Run cell | 65,536 context / 32,768 prefill; targeted arm |
| Article | [Secondary hyperaldosteronism associated with severe infantile atopic dermatitis: a case series](https://pmc.ncbi.nlm.nih.gov/articles/PMC13549756/) |
| Article IDs | PMID 41456571 · PMCID PMC13549756 · DOI [10.6065/apem.2550320.160](https://doi.org/10.6065/apem.2550320.160) |
| Article license recorded in bundle | CC BY-NC 4.0; reuse obligations and asset exceptions remain separate |
| Patient | Patient 2 (`p2`), human; no name recorded |
| Source passage | `b00009`, “Case reports / 2. Case 2” |

The source passage identifies this as one patient among three. The bundle also flags that later discussion paragraphs describe cases collectively and cannot always be safely attributed to Patient 2.

## Model-generated patient summary

The companion summary says Patient 2 presented at 7 months with 24 hours of lethargy and vomiting after feeds. His eczema had been treated with emollients, 1% hydrocortisone, and fusidic acid, but appeared infected and exudative. Formula had recently been introduced after six months of exclusive breastfeeding; his mother followed a vegan diet. His weight was below the 0.4th centile, compared with the 21st centile at birth.

Initial blood tests showed sodium 115 mmol/L and potassium 6.4 mmol/L, with normal renal function. Aldosterone was above 40,000 pmol/L and plasma renin activity was 22.4 nmol/L/h. The summary also reports WBC 40.6×10⁹/L, lymphocytes 17.9×10⁹/L, eosinophils 13×10⁹/L, no blasts, and thrombocytosis. It says he received intravenous fluids and IV flucloxacillin, dermatology optimized his steroid regimen, and an amino acid formula was started because of concern about cow’s milk protein allergy. He was discharged with sodium 131 mmol/L. At 11 months his eczema and weight had improved; skin-prick testing showed sensitization to milk, nuts, lentils, and coconut, and repeat blood tests showed resolution of the electrolyte disturbances and normalization of aldosterone and renin.

Each of those summary claims in the saved output is linked to source evidence in segment `b00009`.

## Structured fields

### Presentation and conditions

| Extracted field | Value | Evidence |
| --- | --- | --- |
| Age at presentation | 7 months | “Patient 2 presented at 7 months…” (`b00009`) |
| Chief complaint | 24 hours of lethargy and vomiting after feeds | Same sentence (`b00009`) |
| Primary diagnosis | Atopic dermatitis; active; severe, exudative, infected | Eczema “appeared to be infected and exudative” (`b00009`) |
| Complication | Hyponatremia; active; marked | Initial sodium 115 mmol/L (`b00009`) |
| Complication | Hyperkalemia; active; marked | Initial potassium 6.4 mmol/L (`b00009`) |
| Differential | Cow’s milk protein allergy; status unknown | Formula started “due to concerns” about CMPA (`b00009`) |
| Other symptoms/signs | Lethargy (24 hours); vomiting after feeds; infected/exudative eczema | `b00009` |

### Medication and treatment records

| Extracted field | Model output | Evidence / note |
| --- | --- | --- |
| Prior eczema treatments | Emollients, 1% hydrocortisone, fusidic acid; action marked historical | All appear in the case passage (`b00009`) |
| Intravenous fluids | Recorded under procedures as completed; indication: hyponatremia | “admitted for intravenous fluids” (`b00009`) |
| Amino acid formula | Recorded as non-pharmacologic therapy; completed; CMPA concern | Formula started because of CMPA concerns (`b00009`) |
| IV flucloxacillin | **Missing from the structured medications list** | It appears in the model’s companion summary and the source passage, but not in the medication items |
| Dermatology steroid optimization | Mentioned in the companion summary, without a specific drug or dose in the structured medication list | Source says dermatology “optimized his steroid regimen” (`b00009`) |

The missing flucloxacillin is a concrete inconsistency between the summary and structured extraction. The medication task is marked **partial**, so this bundle should not be treated as complete.

### Observations and tests

| Observation | Extracted result | Model flag / timing | Evidence |
| --- | --- | --- | --- |
| Serum sodium | 115 mmol/L | Low; at 7 months | Initial blood tests (`b00009`) |
| Serum potassium | 6.4 mmol/L | High; at 7 months | Initial blood tests (`b00009`) |
| Aldosterone | >40,000 pmol/L; reference text `<2,000 pmol/L` | High; at 7 months | Hormone results (`b00009`) |
| Plasma renin activity | 22.4 nmol/L/h; reference text `<11 nmol/L/hr` | High; at 7 months | Hormone results (`b00009`) |
| White blood cell count | 40.6×10⁹/L | High; at 7 months | CBC description (`b00009`) |
| Lymphocyte count | 17.9×10⁹/L | Flag unknown | CBC description (`b00009`) |
| Eosinophil count | 13×10⁹/L | Flag unknown | CBC description (`b00009`) |
| Platelet count | “thrombocytosis noted”; no numeric result | High; at 7 months | CBC description (`b00009`) |
| Serum sodium | 131 mmol/L | Flag unknown; at discharge | Discharge sentence (`b00009`) |
| Skin-prick test | Sensitization to milk, nuts, lentils, and coconut | Positive; at 11-month review | Follow-up sentence (`b00009`) |

The model also extracted normal renal function as text in the narrative summary, but did not represent it as a numeric observation here.

### Feeding, growth, follow-up, and plan

- Birth weight: 21st centile. At presentation, weight was below the 0.4th centile.
- Feeding: exclusive breastfeeding for six months; formula was recently added. The model also recorded the mother’s vegan diet under social/dietary exposures.
- At discharge: sodium 131 mmol/L.
- At 11 months: eczema and weight had improved; the model recorded complete resolution of electrolyte disturbances and normalized aldosterone and renin.
- Plan: continued exclusion of milk, nuts, lentils, and coconut was recommended after skin-prick sensitization.
- Allergy section: marked **not documented**. Sensitization is recorded as a test finding, while CMPA remains a differential rather than a confirmed allergy in this bundle.

## Time course and figure handling

The separate `timeline_v2` task **failed** because its response could not be parsed into a complete object. The dates above come from time fields in other valid tasks; they are not a validated timeline output. A cautious chronology recoverable from those fields is: presentation at 7 months → admission and treatment → discharge with sodium 131 mmol/L → review at 11 months with clinical and biochemical improvement.

The article contains Fig. 1, a proposed mechanism diagram rather than a patient-specific clinical image. The saved figure annotation describes a flowchart linking skin/GI barrier disruption and fluid/sodium loss with RAAS activation and possible hyponatremia/hyperkalemia. It classifies the figure as a **diagram**, not a chart, and its clinical role as **background**. No patient assignment is made, which is appropriate for a general mechanism figure.

There is an important media caveat: the bundle’s media metadata says `pixels_inspected: false` and `has_image_urls: false`. Although an annotation record is present and says it is valid, this campaign output does **not** establish that the model actually inspected the figure pixels. Treat the description as unverified until image delivery and pixel review are confirmed.

## Validation and what this example tells us

| Check | Saved result |
| --- | --- |
| Case context, demographics, conditions, symptoms, allergies, procedures, observations, outcomes, care plan, and summary | Valid |
| Medications | Partial; targeted items unresolved |
| Social exposures | Partial; targeted items unresolved |
| Dedicated timeline | Failed parsing |
| Whole clinical bundle | `all_clinical_companion_tasks_valid: false`; `complete_for_scope: false` |
| Semantic coverage / clinical review | Not verified; review status is unreviewed |

This is a useful example of both the upside and the remaining work: the system preserves detailed values, time cues, patient identity, and supporting quotes across a multi-patient article, while the medication omission, failed timeline, and unverified figure pixels show why validation and targeted repair are still needed. It is **not** a clinician-adjudicated accuracy result.
