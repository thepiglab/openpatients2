# Article license policy

Policy `article-adaptations-noncommercial/3`, implemented 2026-10-02 in
`src/openpatients2/license_policy.py`. The objective is to acquire sources that
permit adapted, noncommercial research records, including CC BY-NC-SA 4.0 and
more permissive grants. Acceptance is **not** a declaration that all source text
or images can be relicensed under one blanket dataset license.

## Discover broadly, decide from source rights

The search uses `open access[filter] NOT pmc embargo[filter]`, rather than a union
of named CC filters. PMC explicitly says [not all OA articles have CC licenses](https://pmc.ncbi.nlm.nih.gov/about/userguide/#by-license).
The [OA subset](https://pmc.ncbi.nlm.nih.gov/tools/openftlist/) includes CC and
similar licenses. Seed and inventory imports use the same rights checks.

1. Check official metadata for OA membership, retraction status, and license.
   Known ND licenses and failed OA/retraction checks stop before XML retrieval.
2. For an OA article with unclassified terms, retrieve bounded official JATS XML
   to inspect article permissions. `metadata_fetch_allowed` allows this inspection
   only; `allowed` separately controls extraction and normal corpus export.
3. Combine metadata and article-level permissions. Recognize identifiers, common
   full names, official license URLs, and nested JATS links whose visible label
   may just say “license.” Preserve the original text, links, version, jurisdiction,
   decision evidence and policy version.
4. Mark unresolved/conflicting rights `license_review`. Keep the ID, terms,
   retrieval hashes/URLs and reason in the durable queue; do not export their
   clinical bodies as accepted articles. These records are available through
   `op2 corpus export --license-review` for explicit review and later reacquisition.

This avoids losing non-CC sources solely because the discovery query or metadata
normalizer has no matching CC code. It does **not** guarantee that every possible
license or all PMC articles have been recognized. Date windows, indexing, source
availability, version ambiguity and acquisition caps still constrain coverage.

## Automatic acceptance

| Source terms | Handling |
| --- | --- |
| CC BY-NC-SA 4.0 | Accept; preserve attribution, NC and ShareAlike obligations. |
| CC BY-NC-SA 2.0 / 2.5 / 3.0 | Accept with original version; later-version adaptation rules apply, not an automatic rewrite of the original license. |
| CC BY, CC BY-NC | Accept known versions; retain the source terms. |
| CC0 | Accept; preserve the dedication and provenance. |
| CC BY-SA | Accept into a separate `sharealike_source_terms` lane. Do not add an NC restriction to that adaptation. |
| CC BY-NC-SA 1.0 | Accept into `legacy_sharealike_source_terms`; the 1.0 adaptation obligation remains distinct. |
| MIT, MIT-0, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, 0BSD, Unlicense | Accept **when applied to the article**; retain their actual notices and obligations in `permissive_source_terms`. |
| Public Domain Mark 1.0, explicit article declaration of public domain worldwide | Accept with the original mark/declaration and a distinct public-domain lane. |

Version numbers are not a permissiveness ranking. An older CC BY source is not
discarded because “4.0” appears in the target policy. The recognized CC versions
are 1.0, 2.0, 2.5, 3.0 and 4.0; CC0/PDM use 1.0. A family-only metadata code can
pass acquisition but carries `resolve_version_before_release` until its exact
terms are established. Unknown future versions are reviewed.

The [CC compatibility rules](https://creativecommons.org/compatible-licenses/)
distinguish BY-SA from BY-NC-SA and explain the special case of version 1.0.
The actual [MIT](https://spdx.org/licenses/MIT.html),
[Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0),
[BSD 2-clause](https://spdx.org/licenses/BSD-2-Clause.html),
[BSD 3-clause](https://spdx.org/licenses/BSD-3-Clause.html),
[ISC](https://spdx.org/licenses/ISC.html), [0BSD](https://spdx.org/licenses/0BSD.html),
[MIT-0](https://spdx.org/licenses/MIT-0.html), and
[Unlicense](https://spdx.org/licenses/Unlicense.html) texts establish the named
permissive grants. Apache requires retaining applicable NOTICE/attribution and
change notices; BSD-3-Clause includes a non-endorsement condition. The stored
obligation tags aid release preparation; they do not replace these full terms.

## Rejected or reviewed

- CC BY-ND and CC BY-NC-ND: rejected for this adaptation pipeline.
- Unknown/custom terms, ambiguous `BSD` or `Apache`, unfamiliar license versions,
  and unresolved compound/dual-license expressions: review. A recognized permissive
  name inside an expression does not automatically make the entire expression pass.
- Contradictory metadata/JATS families, conflicting explicit versions/jurisdictions,
  restrictive or negated licensing prose, and ambiguous article-versus-code/asset
  scope: review. A clean-looking URL cannot override contradictory source prose.
- Bare “public domain” or US-only government-work declarations: review the
  territorial scope. Do not infer worldwide rights from a US government affiliation.
- `TDM` is not an adaptation grant. PMC [defines it for author manuscripts](https://pmc-oa-opendata.s3.amazonaws.com/README.txt)
  available for text mining and uses consistent with fair use; it is not an empty
  license field that a different incidental name can silently override.
- GPL/AGPL and other unlisted terms are not assumed to be “better”; keep them for
  review under their actual obligations.

The parser does not use bibliography, code availability sections, patient text or
figure credits to establish the article's license. Software mentioned as “MIT”
does not license its describing paper. All source statements remain available for
audit. Conservative scope/prose checks may defer valid articles; the review queue
makes those recoverable rather than silently dropping them.

Figure and supplement exceptions still require separate checks. PMC's
[copyright guidance](https://pmc.ncbi.nlm.nih.gov/about/copyright/) explicitly notes
that even OA/public-domain articles may contain third-party images. Article
acceptance never removes those asset restrictions.

## Reproducibility

The acquisition campaign pins this policy module and the discovery query module.
Start a **new campaign directory** after an update; do not mutate completed rights
decisions or a running benchmark's checkout. Existing decisions that were rejected
or deferred cannot be rehabilitated by the offline `recheck_license` helper.
Use fresh, reviewed acquisition when expanding policy coverage.

Automated tests cover canonical names/URLs, nested links, older and conflicting
versions, false-positive URLs, software scope, restrictive prose, non-CC discovery,
JATS fallback from unknown metadata, and separation of review/clinical exports.
No model calls or large downloads are needed for these tests.
