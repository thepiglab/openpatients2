# Fifty-article Beam/FHIR competition subset

Frozen 2026-10-30 from the current live TRAIN corpus. This is a deliberately selected development/demo dataset, not an unbiased or held-out benchmark.

## Selection outcome

| Property | Frozen subset |
|---|---:|
| Articles | 50 |
| Existing reference patients | 67 |
| Single / two / three-patient articles | 40 / 3 / 7 |
| Broad selection families | 26 |
| Articles containing text tables | 48 |
| Articles with annotated measurement series | 24 |
| Numeric observation annotations | 2,988 |
| All observation annotations | 4,141 |
| Measurement-series point annotations | 1,837 |
| Conditions / medications / procedures | 668 / 618 / 542 |
| Source length median / maximum | 21,226 / 38,341 characters |
| Source licenses | 50 article-level CC BY |

Counts describe existing source-grounded reference annotations, not promised Beam output or physician-adjudicated truth. Numeric observations and series points can overlap and must not be summed as unique measurements. Every article retains source URL, DOI, original license statements and source hashes. Figure pixels and third-party asset exceptions are outside this text-only selection.

## Why these articles

The eligible pool contains 171 of 436 current training articles. The 265 excluded articles are individually recorded with all applicable reasons in the manifest; another 121 passed eligibility but were not selected because the subset is capped at fifty.

Eligibility requires a completed reference, local source matching the reference text hash, article-level CC BY permission metadata without restrictive prose, human patients, one to three referenced individuals, no ambiguous unidentified/cohort/illustrative identity label, at least eight numeric observations, at most 40,000 source characters and at most sixty table rows. These are practical competition limits, not clinical inclusion rules for the eventual full dataset. Veterinary sources, aggregate/empty records, large cohorts and extreme appendices remain available in the full corpus.

Selection first gives each represented broad family one seat, then ensures ten small-series articles where feasible, then fills remaining seats using a saturating measurement/series richness score plus within-family lane diversity. A three-article family ceiling limits domination by laboratory-heavy specialties. Source length and patient count receive modest workload penalties. Ties use a fixed SHA-256 seed. No model output, score, failure count or timing informs selection.

The selection covers renal/endocrine laboratory trends, respiratory measurements, ECG/echo data, hearing and eye findings, pregnancy and neonatal cases, infectious disease, drug toxicity, oncology, surgery, psychiatry, dermatology and dentistry. These provide varied inputs for Observation, Condition, medication and procedure resources. ICD-10-CM coding pertains to suitable diagnoses/signs; laboratory tests and drugs need their own terminology or uncoded source text rather than forced ICD codes.

## Reproducibility and integration

- `configs/hipergator/nemotron-super/competition-50.json`: IDs, selection criteria, reasons, source hashes, rights metadata, features, counts, excluded IDs and reference file hash.
- `configs/hipergator/nemotron-super/competition-50-reference.jsonl.gz`: exactly the fifty selected current reference rows; deterministic gzip with zero mtime.
- `src/openpatients2/competition_selection.py`: reproducible builder and frozen-subset validator.
- `scripts/select_beam_competition.py`: build or verify the selection.

```bash
# Verify the existing frozen selection without changing it:
python scripts/select_beam_competition.py --check

# Deliberately regenerate from the current live training corpus:
python scripts/select_beam_competition.py
```

The historical default campaign reference contains 526 articles, including 316 train articles. The live corpus now contains 436 train, 141 validation and 146 test articles. Eighteen selected articles are absent from the historical snapshot. The campaign must therefore use the dedicated competition reference; do not silently fall back to the old snapshot or modify that historical snapshot. The validator accepts growth in the live corpus but rejects changed selected source files, changed frozen reference bytes, membership differences or held-out splits.

All fifty references and all feature counts are for selection/scoring only. Do not pass their patient roster, counts, diagnoses or measurements to Beam as extraction context. The source documents alone are inference inputs. Validation/test articles are neither read for selection nor reclassified into training. Existing reference annotations are AI-reviewed and remain non-physician-adjudicated.

## Time budget evidence

Only four selected IDs overlap the locally available October 29 morning Beam repair timing receipts. They took 7.56, 10.32, 15.55 and 15.71 minutes (median 12.94 minutes) on one TP4 server; these timings did not influence selection. They are too few and come from an older pipeline to justify a precise fifty-article ETA.

A twelve-hour worker with roughly 27 minutes of model startup has about 13.9 minutes per article for fifty articles, before reserving finalization time. With two matched arms, each four-GPU worker must process all fifty: this is one hundred article-arm attempts total, not fifty shared between arms. The redesigned sparse pipeline may be faster, while terminology generation adds work. Keep explicit budgets, completed-article checkpoints and honest partial completion/resume; do not promise all fifty per arm within twelve hours.

## Frozen article list

Patient and measurement counts below are reference metadata, not inference hints.

| Article | Family | Patients | Numeric observations | Series points | Source characters | Title |
|---|---|---:|---:|---:|---:|---|
| PMC8657716.1 | cardiology | 1 | 190 | 264 | 23,975 | Diagnostic Difficulties in a Case of Fetal Ventricular Tachycardia Associated with Neonatal COVID Infection: Case Report |
| PMC5674453.1 | critical_care | 1 | 52 | 0 | 19,857 | Cardiac arrest caused by rapidly increasing ascites in a patient with TAFRO syndrome: a case report |
| PMC8650745.1 | cross_specialty | 1 | 80 | 71 | 16,059 | Severe hypothyroidism‐induced rhabdomyolysis: A case report |
| PMC7803321.1 | dental | 1 | 49 | 27 | 16,467 | A Multidisciplinary Approach of an Endo-Perio Lesion in a Severely Compromised Tooth: An 18-Year Follow-up Case Report |
| PMC12592069.1 | dermatology | 1 | 28 | 0 | 24,090 | Case Report: Dupilumab combined with allergen-specific immunotherapy in severe atopic dermatitis and asthma |
| PMC6029867.1 | endocrine | 2 | 108 | 180 | 18,962 | Canagliflozin-associated diabetic ketoacidosis: a case report |
| PMC6993302.1 | ent_audiology | 1 | 18 | 12 | 14,438 | The Potential Use of P1 CAEP as a Biomarker for Assessing Central Auditory Pathway Maturation in Hearing loss and Associated Disabilities: a case report |
| PMC10751923.1 | gastroenterology | 1 | 98 | 53 | 33,518 | Primary biliary cholangitis with features of autoimmune hepatitis in a 19-year-old adolescent with 14q24.1q24.2 deletion: a case report |
| PMC12765198.1 | gynecology | 1 | 24 | 0 | 18,256 | Successful Pregnancy Following Ovarian Platelet-Rich Plasma (PRP) Treatment, the DuoStim Protocol, and Preimplantation Genetic Testing for Aneuploidy in a Patient With Premature Ovarian Insufficiency: A Case Report |
| PMC12776220.1 | hematology | 1 | 33 | 32 | 28,950 | Heparin-Induced Thrombocytopenia Causing Early Venous Thrombosis and Free Flap Failure After Mandibular Reconstruction: A Case Report |
| PMC12811976.1 | hepatology | 1 | 44 | 0 | 17,407 | Efavirenz-Induced Hepatitis Mimicking Autoimmune Hepatitis: A Case Report |
| PMC12817846.1 | infectious_disease | 1 | 64 | 28 | 24,684 | From urinary tract infection to deafness: community-acquired meningitis in an adult caused by hypervirulent Klebsiella pneumoniae—a case report |
| PMC12777762.1 | musculoskeletal | 1 | 10 | 0 | 13,704 | Brachioradialis tendon transfer for a thumb and finger extension disorder owing to distal-type cervical spondylotic amyotrophy: a case report |
| PMC12640626.1 | neonatology | 3 | 62 | 60 | 30,432 | Congenital Leukemia: Presentation of Three Cases |
| PMC7769828.1 | nephrology | 1 | 178 | 159 | 19,741 | Recovery From Acute Kidney Injury With Diabetic Ketoacidosis Following SARS-CoV-2 Infection: A Case Report and Literature Review |
| PMC6949662.1 | neurology | 1 | 18 | 12 | 23,019 | Anti-NMDAR Encephalitis: Higher Suspicious Needed for Earlier Diagnosis (Case Report, Literature Review and Diagnostic Criteria) |
| PMC12838257.1 | obstetrics | 1 | 94 | 123 | 27,805 | Management of alemtuzumab-induced Graves’ disease in pregnancy: a case report and literature review |
| PMC9911106.1 | oncology | 3 | 29 | 0 | 10,229 | Single-agent carboplatin in extensive disease small-cell lung cancer patient with liver failure: a case report within the experience of a single institution |
| PMC12689009.1 | ophthalmology | 1 | 38 | 0 | 23,541 | Ocular administration of brinzolamide leading to Stevens-Johnson syndrome/toxic epidermal necrolysis overlap: A case report and review |
| PMC9053208.1 | pediatrics | 1 | 24 | 391 | 27,054 | A case report describing the immune response of an infant with congenital heart disease and severe COVID-19 |
| PMC12834080.1 | psychiatry | 1 | 10 | 15 | 16,958 | Psychosis as the Initial and Main Presentation of Graves’ Disease: A Case Report from Saudi Arabia |
| PMC12611315.1 | respiratory | 1 | 98 | 0 | 13,087 | Sequential Use of Tezepelumab and Bronchoscopic Lung Volume Reduction With Endobronchial Valves in a Patient With Severe Asthma‐COPD Overlap and Heterogeneous Emphysema: A Case Report |
| PMC13592934.1 | rheumatology | 1 | 54 | 12 | 28,710 | Plasma exchange combined with low-dose rituximab treatment: A case report of severe antineutrophil cytoplasmic antibody (ANCA)-associated vasculitis in the elderly |
| PMC12711414.1 | surgery | 1 | 36 | 0 | 20,702 | Laparoscopic Cholecystectomy for Acute Cholecystitis in a Patient with Acquired Hemophilia A: A Case Report of Perioperative Hemostatic Management with Plasma Exchange |
| PMC12637166.1 | toxicology | 1 | 48 | 40 | 16,984 | Metformin-Induced Lactic Acidosis Following Intentional Overdose: A Case Report and Mini Review |
| PMC12884857.1 | vascular | 1 | 42 | 0 | 16,210 | Perioperative Diagnosis of Acute Pulmonary Embolism Following Laparoscopic Hysterectomy Under General Anaesthesia: A Rare Case Report |
| PMC7464795.1 | respiratory | 3 | 26 | 6 | 18,262 | Hypoglossal Nerve Stimulator Placement for Pediatric Trisomy 21 Patients with Refractory Obstructive Sleep Apnea: A Case Series |
| PMC12605513.1 | endocrine | 2 | 87 | 0 | 28,993 | Case Report: Exogenous insulin antibody syndrome complicated with chronic renal failure and long-term history of type 2 diabetes: report of two cases |
| PMC12802722.1 | toxicology | 3 | 132 | 0 | 38,341 | Case report: Ethylene glycol intoxication presenting as a mimic of acute stroke: a report of three cases |
| PMC7692694.1 | critical_care | 3 | 69 | 0 | 24,964 | Case Report of Patients with Acute Respiratory Distress Syndrome Caused by COVID-19: Successfully Treated by Venovenous Extracorporeal Membrane Oxygenation and an Ultra-Protective Ventilation |
| PMC12875553.1 | endocrine | 3 | 74 | 0 | 31,835 | Efficiency of Microwave Ablation for Cortisol-Producing Adrenal Adenomas: Case Series and a Literature Review |
| PMC12704537.1 | neurology | 2 | 19 | 0 | 8,968 | Two Cases of Autochthonous West Nile Virus Encephalitis, Paris, France, 2025 |
| PMC12831519.1 | respiratory | 3 | 47 | 0 | 20,137 | Unmasking Invasive Pulmonary Aspergillosis: Insights From a Case Series at a Tertiary Care Center |
| PMC13593041.1 | cardiology | 1 | 88 | 104 | 27,191 | Hemorrhagic cardiac tamponade complicating myocardial infarction in a hemodialysis patient with essential thrombocythemia: A case report |
| PMC7694404.1 | cross_specialty | 1 | 87 | 75 | 28,271 | A Rare Cause of Chronic Hypokalemia with Metabolic Alkalosis: Case Report and Differential Diagnosis |
| PMC5632983.1 | cardiology | 1 | 63 | 68 | 14,501 | Heart conduction system defects and sustained ventricular tachycardia complications in a patient with granulomatosis with polyangiitis. A case report and literature review |
| PMC6738114.1 | cross_specialty | 1 | 37 | 29 | 17,745 | Breast milk paclitaxel excretion following intravenous chemotherapy—a case report |
| PMC12846762.1 | infectious_disease | 1 | 47 | 40 | 24,873 | Successful One-Stage Revision With Continuous Local Antibiotic Perfusion (CLAP) for Delayed Periprosthetic Joint Infection After Reverse Shoulder Arthroplasty: A Case Report |
| PMC12859379.1 | toxicology | 1 | 67 | 24 | 20,222 | Olanzapine Overdose in an Adult Male Presenting With Altered Sensorium and Pinpoint Pupil: A Case Report |
| PMC12849829.1 | nephrology | 1 | 99 | 0 | 21,750 | Primary Sjögren’s Syndrome Presenting With Severe Hypokalemia Due to Distal Renal Tubular Acidosis: A Case Report |
| PMC12812266.1 | gastroenterology | 1 | 56 | 0 | 13,828 | Functional Reversal as a Feasible Option for Malnutrition in Patients With Hyper-Response Following Roux-en-Y Gastric Bypass: A Case Report |
| PMC12688865.1 | infectious_disease | 1 | 15 | 12 | 17,454 | Sepsis after the Altemeier procedure: A case report |
| PMC12764371.1 | obstetrics | 1 | 107 | 0 | 23,384 | Severe Hepatic Enzyme Elevation and Coagulation Abnormalities in a Pregnant Woman With Recent COVID-19 Infection and Preterm Premature Rupture of Membranes: A Case Report |
| PMC12457179.1 | pediatrics | 1 | 46 | 0 | 27,215 | Established and emerging use of sodium-glucose cotransporter-2 inhibitors in pediatric population with type 2 diabetes: a case report and literature review |
| PMC12577925.1 | hematology | 1 | 71 | 0 | 24,446 | EDTA-dependent pseudothrombocytopenia mimicking dengue-associated severe thrombocytopenia: a case report |
| PMC12999700.1 | nephrology | 1 | 87 | 0 | 37,556 | Bone disease in kidney transplant: don’t forget about osteomalacia: a case report and literature review |
| PMC12728242.1 | hepatology | 1 | 60 | 0 | 32,641 | Conservative Management of a Ruptured Simple Hepatic Cyst: A Case Report |
| PMC12364496.1 | ent_audiology | 1 | 22 | 0 | 14,948 | Kerstersia gyiorum infection in a patient with chronic suppurative otitis media identified by whole genome sequencing: a case report |
| PMC8871403.1 | ent_audiology | 1 | 32 | 0 | 23,673 | Diagnostic Value of Preoperative Electrodiagnostic Analysis in a Patient with Facial Palsy and a Large Vestibular Schwannoma: Case Report |
| PMC7239835.1 | gastroenterology | 1 | 21 | 0 | 14,997 | Distribution of lanthanum carbonate in the gastric mucosa confirmed by electron microscopy with a magnified endoscopy: a case report and literature review |
