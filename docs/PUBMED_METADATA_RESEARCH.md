# PubMed metadata for the research index — investigation only

Research checked **September 27, 2026**. This document proposes a separate article-index layer. The release implements original-row/article provenance and figure URL discovery; it **does not** ingest general PubMed bibliographic, keyword, or MeSH metadata.

## Available standard interfaces

NCBI's E-Utilities can retrieve PubMed records by PMID. `ESummary` gives a compact citation view; `EFetch` with `db=pubmed&retmode=xml` provides richer article/citation fields. Use documented batching, contact identification, and NCBI request limits when implementing that client; do not scrape search-result HTML. Availability varies by record and indexing status.

The current figure client uses the distinct PMC ID Converter and PMC Cloud datasets; it does not call PubMed EFetch. Minimal identifiers, dates of retrieval, rights/retraction flags, and JATS captions are retained solely for provenance and media safety. That should not be confused with a complete publication-metadata index.

Official starting points:

- [PMC: Get article metadata](https://pmc.ncbi.nlm.nih.gov/tools/get-metadata/)
- [PubMed Help](https://pubmed.ncbi.nlm.nih.gov/help/)
- [E-Utilities introduction, usage and policy](https://www.ncbi.nlm.nih.gov/books/NBK25497/)
- [PubMed XML/DTD documentation](https://dtd.nlm.nih.gov/ncbi/pubmed/)

## Most useful additions, if approved later

| Article-level metadata | Research use | Limitation to preserve |
|---|---|---|
| MeSH descriptors, stable term identifiers, qualifiers and major-topic indicators | Broad disease/procedure/anatomy discovery; hierarchical search and relevance ranking | Not every PubMed citation has MeSH. Absence is not a negative finding; topic assignment is not an individual patient's phenotype. |
| Author keywords and abstract | Full-text search, synonyms, candidate record retrieval | Keywords are not uniformly controlled; abstracts can combine several patients and disclose the final diagnosis. |
| Publication types | Separate case reports, reviews, trials, corrections and other article forms | Article-level labels are not perfect quality judgments or clinical eligibility facts. |
| Title, journal, ISSN, language, publication dates and citation fields | Source display, filters, de-duplication and provenance | Publication date is not treatment date, visit date, or onset date. |
| Linked corrections, retraction notices, expressions of concern and related update links | Evidence-quality flags and targeted re-review | Preserve link type and target PMID. A notice and the corrected/retracted article are different records. Refresh over time. |
| Chemical/substance indexing and supplementary concepts where present | Medication/biomarker-related article discovery | Topic indexing does not establish that the case patient received the drug or tested positive. |
| Authors, affiliations, identifiers and grants | Bibliographic attribution, institution/funding-based source exploration | Author affiliation does not establish a patient's geography, ethnicity, sex, or care location. |
| Databank/accession or trial-registration identifiers where provided | Link a paper to external study resources | Incomplete and not necessarily a patient-level study enrollment assertion. |

My proposed first extension would be **MeSH + publication type + citation display + correction/retraction links**. Add article keywords/abstract to a separate text-search field only after deciding whether they are allowed to influence retrieval versus clinical extraction.

## Avoid contaminating clinical cohort logic

Use a schema like this in a future implementation:

```text
source_cases                   # original rows, source descriptions, clinical facts
case_article_links             # explicit case → PMID / PMCID evidence
articles                       # bibliographic and indexing data, retrieval provenance
article_terms                  # MeSH / keywords / chemicals, separately typed
article_updates                # correction/retraction/other linked-notice relationships
article_figures                # existing article-level URL candidates
reviewed_case_figure_links     # future approved patient/panel/time attribution
```

Clinical inclusion must still be satisfied by case-level evidence. For example, article indexing for `Male`, an age group, `Humans`, or a cancer cannot establish those properties for every case in a multi-patient paper. A `review` tag is a source filter, not a diagnosis. A publication date cannot provide an episode date. Article metadata can find candidate material; it cannot silently manufacture patient facts.

Recommended retrieval behavior is to expose two different modes: **article-topic search** to discover candidates, and **evidence-backed clinical eligibility** to identify case records satisfying the requested criteria. Return the reason for each match and its scope. A set of matching published cases is not automatically representative of clinical prevalence.

## Figure-service alternatives

[Europe PMC's REST service](https://europepmc.org/RestfulWebService) is a possible future alternate source for core article metadata, full-text links, and available open-access XML. It is not implemented as a fallback in this release. Its `supplementaryFiles` endpoint downloads a ZIP, so it is not appropriate for the requested URL-only discovery step. Publisher-specific discovery would require separate documented APIs/rights review rather than guessing URLs or scraping around access restrictions.

## Patient-simulator implications

The referenced group appears to be **Baichuan**. [Baichuan-M2](https://arxiv.org/abs/2509.02208) describes a Patient Simulator and Clinical Rubrics Generator for interactive training. This release lays provenance groundwork; it does not reproduce that system.

A future episode should declare which facts are visible at presentation, which test images/results appear after a simulated order, which conclusions are hidden from the clinician-agent, and which events are authored simulations rather than observations in the original paper. Keep diagnosis-revealing captions, article titles, follow-up outcomes and teacher reasoning out of observations until justified by the scenario. Review each figure's case/panel attribution and source rights, and split by source article rather than individual rows to reduce leakage. Publication figures are not a substitute for complete acquisition data, DICOM series, or a real longitudinal EHR.
