# Medical extraction fidelity: regular Spark, Glimmer, Inkling and Gemma

Run date: September 29, 2026. **Regular `meta/muse-spark-1.2` works with the supplied account settings. Glimmer's earlier structural lead does not establish superior medical fidelity.** This comparison checks source facts and distinguishes missing or incorrect fields from losses caused by parsing, citations and validation. It supports further development with Spark and Glimmer, not a definitive clinical ranking.

The [machine-readable results](medical-fidelity-results.json) contain the counts below. The primary study uses prompt-requested JSON with **no API schema enforcement**. A separate paired schema-request control is described below.

**What was checked.** Nine previously license-audited PMC sources include seven case articles with 11 individual patients (10 humans and one cat), plus a plant meta-analysis and an aggregate clinical study as negative controls. The cleaned full article, including tables and captions, was supplied for each patient; references were excluded. This was a text study. No new image-pixel accuracy evaluation was performed.

Each model received the same source-checked reference roster for downstream extraction, isolating extraction from discovery failures. These rosters originated from an earlier Glimmer scaffold and were checked against source identities/species; they are not independent physician annotations. Fresh model-generated rosters were assessed in a separate 36-task comparison. The primary extraction comprises 14 clinical domains, a cited summary and a timeline per patient: 176 tasks per model, 704 total, with at most one bounded repair per completed task.

The [frozen reference](../runs/medical-fidelity-v1/reference.json) contains **161 required field checks and 36 targeted forbidden claims per model**. It was frozen before fresh model outputs; its SHA-256 is `e9f95e70c1316fef078f05a0320796a093c12b88138945b099db252a31935b57`. Some sources had already appeared in the development pilot. This is neither held-out nor blinded evaluation. **91 of the 161 positive checks come from one three-patient poisoning article**, so the pooled score heavily weights its laboratory tables. There are 76 numeric-value/unit checks, plus checks on demographics, diagnoses, medications, procedures, outcomes, oncology and timing. These are partial labels, not exhaustive clinical recall.

Raw scoring uses only the final attempt when it is complete and parseable; it never picks a better earlier response. Delivered scoring requires the entire relevant section to pass the current validator. Quotes are removed before matching, so reproducing a source table in evidence does not count as extracting its values. All available automatic misses and forbidden hits were reviewed for semantic equivalence. Source-supported synonyms and equivalent representations receive explicit, auditable corrections; correct facts present only in prose or the wrong structured domain remain typed-field misses, not automatically false medical claims.

| Model | Checked fields retained before validation | Required checks unavailable before validation | Checked fields delivered after validation | Valid tasks |
| --- | ---: | ---: | ---: | ---: |
| Spark 1.2 | 143/161 (88.8%) | 16 | 70/161 (43.5%) | 163/176 |
| Glimmer 30B | 125/161 (77.6%) | 22 | 100/161 (62.1%) | 166/176 |
| Inkling | 139/161 (86.3%) | 18 | 48/161 (29.8%) | 114/176 |
| Gemma 4 31B | 91/161 (56.5%) | 38 | 61/161 (37.9%) | 149/176 |

The 36 targeted forbidden patterns produced no hits in available final sections. Some were unassessable: Spark 0, Glimmer 4, Inkling 2 and Gemma 4 before validation. This narrow negative checklist did not cover every observed error; it is not evidence of zero hallucinations.

“Before validation” still requires parseable final output; malformed or timed-out sections are unavailable, not medically wrong. “After validation” measures what the current pipeline actually delivers. **Neither column is a medical accuracy percentage.** Empty but valid sections can inflate a task-success rate while missing important facts, and one defective item can suppress an otherwise useful section.

Required checks retained **before validation**, by source article:

| Source group | Spark | Glimmer | Inkling | Gemma |
| --- | ---: | ---: | ---: | ---: |
| Mandibular fibro-dentinoma; one child | 6/6 | 6/6 | 6/6 | 6/6 |
| Mandibular osteosarcoma; one woman | 8/8 | 8/8 | 8/8 | 8/8 |
| Bullet embolism; one man | 11/11 | 11/11 | 11/11 | 11/11 |
| Dolichoectasia/hydrocephalus; two men | 13/13 | 13/13 | 13/13 | 13/13 |
| Ethylene glycol poisoning; three men | 89/91 | 62/91 | 87/91 | 38/91 |
| Mesocolon tumors; two men | 14/14 | 13/14 | 14/14 | 13/14 |
| D. repens; one cat | 2/18 | 12/18 | 0/18 | 2/18 |

The per-article counts expose the weighting and missing-output problems. For example, Glimmer retained several abnormal cat laboratory results but omitted six checked normal-range results, despite instructions to preserve normal measurements. Gemma omitted many normal/table-only measurements in the poisoning cases. Some additional typed-field misses were correct values left in text, such as a medication dose or an inequality bound.

**Claim-level source review.** A fixed-seed diagnostic sample chooses two clinical claims from preferably distinct domains and one summary claim per patient/model, where available. I reviewed each selected claim's material fields against the source, including patient identity, assertion, status, value/unit, specimen and timing. This is agent review, not physician validation or independent confirmation that a paper's claims are medically true. Model names were replaced by aliases in the review sheets, but identities and aggregates were visible elsewhere; the review was not blinded. Available domains differ by model, so this is not a population precision estimate or a fair accuracy leaderboard.

| Model | Supported | Unsupported material field | Uncertain | Unavailable planned samples |
| --- | ---: | ---: | ---: | ---: |
| Spark 1.2 | 31 | 2 | 0 | 0 |
| Glimmer 30B | 31 | 2 | 0 | 0 |
| Inkling | 22 | 5 | 4 | 2 |
| Gemma 4 31B | 30 | 2 | 1 | 0 |

**130 claims were reviewed out of a 132-claim target.** Inkling's cat clinical sections timed out, leaving only its summary claim available. The missing two claims are not counted as correct or incorrect.

Full claims, citations, verdicts and rationales are retained in [claim-audit-reviewed.json](../runs/medical-fidelity-v1/claim-audit-reviewed.json). Uncertain findings include a source that describes admission measurements in its table but later testing in the narrative; that ambiguity was not silently scored as a model error.

Several concrete findings matter for EHR construction:

- **Wrong patient:** Inkling assigned packed-red-cell transfusions to mesocolon tumor case 1, although the source describes them in case 2. The quotation itself was real. The medication section failed validation for other reasons, but literal quote matching alone does not detect the attribution error. [Source article](https://pmc.ncbi.nlm.nih.gov/articles/PMC12285374/), audit `2e9ff7b461b7e46b`.
- **Wrong clinical action:** Gemma encoded dialysis as declined in poisoning case 3. The source says it was not indicated, with no documented refusal. This error survived section validation. [Source article](https://pmc.ncbi.nlm.nih.gov/articles/PMC12802722/), audit `8f5c1c8d1b56ef1f`.
- **Treatment target turned into a result:** Glimmer and Gemma produced a resulted Anti-Xa observation from the anticoagulation target of 0.3–0.5. That range was a goal, not a reported assay result. Inkling represented a planned target observation; Spark kept it out of observations. These are additional targeted checks outside the frozen checklist. [Source article](https://pmc.ncbi.nlm.nih.gov/articles/PMC13294519/), [saved findings](../runs/medical-fidelity-v1/additional-source-checks.json).
- **Unsupported detail:** Spark and Glimmer both added a serum specimen to a correctly extracted glucose value where that specimen was not documented for glucose. Spark labeled historical anal fissures active; Inkling labeled them inactive; the source did not establish either current status. A correct diagnosis or number can still carry an unsupported modifier.
- **Excess certainty:** Gemma's cat summary strengthened negative imaging into definitive exclusion of adult heartworms. The source reports that no worms were seen. The summary passed structural validation. [Source article](https://pmc.ncbi.nlm.nih.gov/articles/PMC10998798/), audit `00a0680db0a044ba`.

**Validation and repair are substantial sources of information loss.** In Glimmer's rejected observations for poisoning case 1, 41 of 50 items pass the same source/schema checks when tested individually with the original section metadata. That is diagnostic evidence for local repair or quarantine, not permission to release those facts as clinically verified. [Per-item diagnostics](../runs/medical-fidelity-v1/observation-validation-diagnostics.json) do not alter any predictions or exports.

More concerningly, Glimmer's case-2 sodium 152 and creatinine 2.56, and Spark's case-3 sodium 135 and creatinine 0.57, initially carried correct 48-hour timing. Their row quotations omitted the table's group heading. During repair, both models removed those times. Glimmer then passed the full section; Spark still failed. The [first-versus-final evidence](../runs/medical-fidelity-v1/repair-regressions.json) shows why a higher validation rate can coexist with lower timeline fidelity.

Other losses were purely delivery failures: Glimmer's final case-3 observation response was malformed JSON; Spark's cat observations were truncated on the first attempt and contained a duplicate object key on the retry. Gemma had observation timeouts. The main counts retain these outcomes instead of selectively replacing them with more favorable attempts.

**Small controlled follow-ups.** These post-hoc tests are separate from the primary comparison and do not establish general improvements:

| Change | Observed result | Remaining limitation |
| --- | --- | --- |
| Explicitly distinguish treatment goals from measured results; four models on the bullet case | Glimmer and Gemma removed the spurious goal observation; Spark kept it absent; Inkling still emitted a planned goal observation. All four retained the checked 102 mL mismatch, 8 mm shift and TICI 2c. | Only Glimmer's whole section passed validation. |
| Cite the table group heading separately; Glimmer case 2 and Spark case 3 | All four targeted values retained their 48-hour times and passed individual source/schema checks. | Both whole sections still failed unrelated narrative timing checks. |
| Request API JSON schema with the original prompt; Glimmer and Spark poisoning case 3 | Glimmer produced all 22 checked lab/timing fields in parseable output. Spark retained the same 20/22 fields as its prompt-only result and now passed validation. | Glimmer still failed citation checks. Spark still omitted two times. Provider acceptance does not independently prove backend grammar enforcement. |

The schema control gives **no evidence of harm to these checked medical fields**. It is one difficult patient per model, not a general quality comparison. Glimmer's prompt-only final response was unparseable, so its zero available checks in that arm must not be called zero medical accuracy. Saved [goal](../runs/medical-fidelity-v1/goal-scope-control/source-checks.json), [table-time](../runs/medical-fidelity-v1/table-time-control/source-checks.json) and [schema](../runs/medical-fidelity-v1/schema-control/source-checks.json) checks preserve the outcomes. Prose-first generation was explored in the earlier implementation pilot; this larger study does not compare unrestricted prose quality.

**Patient discovery.** All four models returned individual-patient lists with the correct counts and human/nonhuman species across these nine sources. Spark passed full roster validation on 9/9; the other models passed 8/9. Glimmer had a nonliteral cat-figure citation; Inkling missed a hydrocephalus figure assignment; Gemma returned an empty individual roster for the aggregate study but put its 75-person cohort size in the individual-count metadata, making the object inconsistent. Counts/species were checked; not every assigned source segment or figure was clinically adjudicated. These results support broader screening beyond a case-report publication tag, not a measured discovery recall over PMC.

**Implementation and scale implications.** Spark is a useful hosted quality comparator; Glimmer remains a promising lower-cost extraction candidate. Inkling retained many checked fields but had more semantic/type issues in the sampled claims and more validator failures. Gemma's endpoint was inexpensive, but missing table results and timeouts materially affected this run. Provider routes, precision, reasoning settings and retries differ, so these are measurements of these configured services, not isolated architecture comparisons.

I added the tested treatment-goal and table-heading instructions to the observation prompt for future runs. I also fixed total-request timeouts to use the existing bounded retry policy while keeping uncertain charges reserved. These changes are **prospective**: the [baseline runtime snapshot](../runs/medical-fidelity-v1/research/baseline-runtime/manifest.json), exact requests and original results are retained. No broader claim of improved clinical accuracy follows from these small fixes.

Before a million-article release, the priorities are per-fact validation/quarantine and local repair that preserves supported fields, explicit patient/column/time binding, normal-value coverage, and a larger source-stratified clinician-reviewed set. These are remaining engineering/evaluation tasks, not features claimed complete by this report. No new model selection should be based solely on successful JSON or whole-section validation. No 8×B200 throughput was measured; the earlier serving profiles still need hardware tests measuring supported facts and usable patient records per hour.

**Cost and verification.** Final account usage was **$11.1815**, leaving **$18.8185 of the $30 limit**, checked at 2026-09-29T12:10:08.719484+00:00. This continuation added **$7.9455**, including rosters, smoke checks, controls and any unreported generation charges. Primary-comparison response-reported costs were Spark 1.2 $4.2444; Glimmer 30B $0.7362; Inkling $1.9772; Gemma 4 31B $0.2007. They are not the account total and exclude unreported timeout charges. The local ledger conservatively accounts for $26.1402 including unresolved reservations; that is not money spent. [Account snapshot](../runs/medical-fidelity-v1/research/budget-after.json).

**359 software tests passed**, including bounded retries for both total-timeout paths and preserving unknown-charge reservations. These tests are not clinical validation. New retained study artifacts occupy approximately **175 MiB**; no model weights or ontology releases were downloaded. The temporary API credential was removed after the final account check.

The primary run paused when the conservative $25 local reservation guard filled. The authoritative account then showed $11.1815 used. Only unfinished tasks were resumed with the original user-authorized $30 ceiling; all unresolved reservations and completed failures were retained. Interrupted in-flight calls may have been charged and are covered by the account total, not necessarily per-task reported costs. [Resumption record](../runs/medical-fidelity-v1/research/resumption.json).

To recompute the offline scores and diagnostic tables without API calls:

```sh
uv run python scripts/score_medical_fidelity.py
uv run python scripts/summarize_medical_fidelity.py
uv run python scripts/analyze_observation_failures.py
```

Source hashes, the frozen checklist, [semantic adjudications](../runs/medical-fidelity-v1/checklist-adjudications.json), individual attempts, provider settings and costs are saved under `runs/medical-fidelity-v1/`. The [experiment configuration](../configs/experiments/medical-fidelity-v1.yaml) records the four pinned provider routes. Re-running inference now uses the prospective prompt/runtime changes and must use a fresh output directory for a genuinely new experiment.
