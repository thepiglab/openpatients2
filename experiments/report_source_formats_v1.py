"""Build the finished report from retained measurements, never from live guesses."""
import json
from pathlib import Path
from source_formats_v1 import ROOT

NAMES={'jats':'JATS segments','pmc_text':'PMC TXT','pdf_plain':'PDF / pypdf','pdf_layout':'PDF / pdfplumber layout',
       'pdf_firecrawl':'PDF / Firecrawl native','pdf_pixels':'PDF text + page images',
       'pdf_firecrawl_regions':'Firecrawl manual column fallback'}
MODELS={'meta/muse-glimmer-30b':'Glimmer','thinkingmachines/inkling':'Inkling','google/gemma-4-31b-it':'Gemma','meta/muse-spark-1.2':'Spark'}


def run():
    verification=json.loads((ROOT/'verification.json').read_text());assert verification['verified']
    rows=json.loads((ROOT/'scores.json').read_text());rows.sort(key=lambda r:(list(MODELS).index(r['model']),list(NAMES).index(r['format'])))
    timing=json.loads((ROOT/'parser-timing.json').read_text())['measurements']
    budget=json.loads((ROOT/'budget-final.json').read_text())
    parts=['''# Source formats: JATS, PMC text and PDF

Completed local pilot, 2026-09-29. The production source format has not been changed.

## Recommendation

Keep **JATS plus PMC metadata as the canonical source** for article identity, licensing, media inventory, citation targets and stable source spans. Test official PMC TXT as a simpler model input when it preserves the relevant table headers and captions. Use **Firecrawl's native `pdf-inspector` as a fast PDF fallback**, with layout checks and page/region provenance. Escalate problematic pages to separate region extraction or the approved vision models. Speed alone does not establish table or clinical fidelity.

The local Firecrawl library is distinct from the complete hosted Fire-PDF service. This experiment used native extraction only, with no OCR, neural model downloads or Firecrawl API key. See the [official Python API](https://github.com/firecrawl/pdf-inspector/blob/main/docs/python.md) and [Fire-PDF architecture](https://www.firecrawl.dev/blog/fire-pdf-launch).

## What was compared

- Three matched, versioned articles: PMC12802722.1 (three poisoning cases, two laboratory tables and shared timeline); PMC12285374.1 (two tumor cases, four figures); PMC10998798.1 (cat case with laboratory values, parasites and an external canine comparator panel).
- JATS through the existing renderer; the official PMC TXT download; PDF via pypdf 6.10.0 plain text; PDF via pdfplumber 0.11.9 layout; and PDF via Firecrawl pdf-inspector 1.25.2 native page Markdown. All five text arms were tested on all four working model routes.
- PDF plain text plus four selected rendered pages per article on Spark and Gemma. This was a page-image vision experiment, not a hosted service's opaque PDF upload parser.
- Four clinical extraction units per arm: poisoning patients 2 and 3 observations, cat observations, and tumor patient 1 medications as a cross-patient negative control. Three separate article-level figure-attribution calls covered seven figures and 21 ownership units.
- An additional three-call Spark diagnostic replaced two problematic Firecrawl pages with manually selected left/right column regions. It was exploratory and did not evaluate figure attribution.

Patient IDs, labels and species were fixed from the earlier source-checked roster. Figure IDs/labels were supplied as an inventory. TXT and PDF arms received no JATS captions, patient facts or reference answers. Thus this tests extraction and attribution given a roster, not patient discovery. Prompts, sampling settings and output limits were held constant across paired arms. JSON was requested in the prompt; no API schema grammar was enforced. There were no generation retries or targeted LLM repairs in this study.

The primary protocol was frozen before generation. Firecrawl and the manual-region diagnostic have separate frozen amendments. Raw requests/responses, costs, failed outputs and rejected items remain in the audit trail.

There were 157 planned comparison/region cells. A further three calls replayed length-truncated Spark figure responses with a larger output cap; the original results remain the primary scores. One Firecrawl figure cell was temporarily held by simultaneous budget reservations and ran after those reservations settled, under the unchanged cap.

**Scope caveat:** this is a practical acquisition-pipeline comparison, not a pure serialization experiment. JATS uses the existing selected segments; TXT removes explicit front matter and bibliography boundaries; PDF retains all pages and publication headers. A References heading inside one PDF column is not a safe global cutoff. Context length, layout, source ordering and cleanup therefore differ together.

## Local parser speed

Same downloaded PDFs, one excluded warm-up and five repetitions in rotating parser order, same bundled Python process. Times are medians per complete article and include native text extraction; they exclude downloads, PDF rendering and LLM inference. These are three small documents on this computer, not an HPC throughput benchmark.

| Article / pages | pypdf | pdfplumber layout | Firecrawl native |
|---|---:|---:|---:|''']
    for pid in ['PMC12802722','PMC12285374','PMC10998798']:
        t={r['parser']:r for r in timing if r['pmcid']==pid}
        parts.append(f"| {pid} / {t['pypdf']['pages']} | {t['pypdf']['median_seconds']*1000:.1f} ms | {t['pdfplumber']['median_seconds']*1000:.1f} ms | {t['pdf-inspector']['median_seconds']*1000:.1f} ms |")
    parts.append('''
Firecrawl fixed the font-name digits produced by pypdf in the poisoning article. However, its default Markdown merged two side-by-side tables and adjacent prose into one table on page 4. All three files reported no pages needing OCR, despite that observed layout failure. An OCR-routing flag is not a guarantee of correct row/column relationships. The region diagnostic used geometry selected after visual inspection; it does not validate an automatic column detector. Exact boxes and actual page dimensions are retained.

## Clinical checklist retention

Each row has the same **60 required checks** and **nine forbidden-claim checks**. Required checks cover selected values, units, comparators, some timing and qualitative tests. They do not exhaust the medical content. A missing/unparseable response earns no recovered checks but is not classified as a medically false statement.

- **Raw:** matching facts in the parseable candidate before provenance/schema gates.
- **Strict:** matching facts surviving the unchanged item/schema and exact segment-local citation gate.
- **Citation formatting recovery:** offline, deterministic normalization of an exact displayed segment label and whitespace-equivalent quotes back to actual source spans. Only citation fields can change. No numbers, times, units, clinical fields, missing quotes or font codes are repaired. This is a separate secondary analysis, not a production change.

The checklist was audited against sources by the assistant, not a physician. These counts are not medical precision/recall or a statistically reliable model ranking. Each cell is a single generation; schema failures, endpoint latency and timeouts can materially affect results.

| Model | Source | Raw /60 | Strict /60 | Citation recovery /60 | Raw forbidden hits / checks | Parseable calls / planned |
|---|---|---:|---:|---:|---:|---:|''')
    for r in rows:
        c=r['clinical'];parts.append(f"| {MODELS[r['model']]} | {NAMES[r['format']]} | {c['raw']['matched']} | {c['strict']['matched']} | {c['whitespace']['matched']} | {c['raw']['forbidden_violations']}/{c['raw']['forbidden_checks']} | {r['parseable_calls']}/{r['expected_cells']} |")
    parts.append('''
The manual-region row contains only three clinical calls; it omits the medication negative-control call and figure calls. It therefore has eight rather than nine forbidden checks. The positive denominator remains 60. The parseable-call column otherwise includes four clinical and three figure calls; it is operational availability, not clinical correctness.

A representative non-format failure also occurred with JATS: Spark retained the cat's laboratory values but attached `July 2023` without quoting that timing in each item's evidence, causing otherwise matched facts to be withheld. PDF quotation failures additionally involved line wrapping, font codes and page-label variants. This separation matters: lower validated delivery can reflect a provenance failure rather than an incorrect value.

For Spark specifically, JATS and PMC TXT each recovered all 60 checklist facts before gating; TXT delivered all 60 through the strict gate, versus 47 for JATS in this run. PDF plain text recovered 50, pdfplumber layout 55, and PDF text plus page images 60. That is promising evidence for a simple TXT model input, but not evidence to discard JATS provenance.

Firecrawl's native Spark total was 38 because the cat response was rejected for four identical duplicate JSON keys. A separate offline diagnostic collapsed only identical keys in complete JSON; conflicting duplicates, unfinished responses and malformed JSON remained rejected. This recovered 12 cat checklist matches, bringing that diagnostic total to 50/60, with 33/60 surviving citation-format recovery. Four cat checks—platelets, creatinine, calcium and ALT—were still omitted even though the values were in the source. The manually selected column-region diagnostic recovered 55/60 before gating and 38/60 after citation-format recovery. These follow-ups expose distinct layout, generation and parser-contract effects; they are not a new primary result or an automatic production repair.

## Figure ownership

These are patient ownership checks on 21 panel/shared-figure units, not visual diagnostic accuracy. Failed or unresolved units stay in the denominator. Captions, inline figure references, patient headings and surrounding text are the evidence; resemblance is not identity evidence. No image pixels were supplied to the text-only arms. The same minimal figure inventory was supplied in every arm, so this does not measure media discovery or URL recovery.

The experiment uses the same typed figure review but a format-neutral exact-evidence gate. It cannot require JATS cross-reference connections in TXT/PDF arms. It therefore does not by itself validate production's additional JATS connection gate end to end.

| Model | Source | Raw ownership /21 | Strict /21 | Citation recovery /21 | Raw wrong-patient units | Strict wrong-patient units |
|---|---|---:|---:|---:|---:|---:|''')
    for r in rows:
        if r['format']=='pdf_firecrawl_regions':continue
        f=r['figures'];parts.append(f"| {MODELS[r['model']]} | {NAMES[r['format']]} | {f['raw']['correct']} | {f['strict']['correct']} | {f['whitespace']['correct']} | {f['raw']['wrong_patient']} | {f['strict']['wrong_patient']} |")
    parts.append('''
**Output-cap diagnostic:** three Spark calls covering all four tumor-article figures hit the original 6,144-token cap. Repeating the identical prompt with 12,288 tokens recovered correct ownership for all 14 units in each of PDF plain text, PDF plus pixels and Firecrawl native. Strict citation validation retained 7/14, 5/14 and 0/14 respectively; citation-format recovery retained 7/14, 5/14 and 12/14. The original scores above remain unchanged. This is also evidence for retaining the earlier per-figure task design or setting a suitable cap when batching figures; truncation should not be misread as an attribution error.

No enumerated wrong-patient ownership assignments or forbidden clinical claims were observed in parseable outputs in this small study. Many facts/units were missing, unresolved, unparseable or rejected, and broader error categories were not exhaustively adjudicated. These zero counts therefore do not establish high overall precision.
''')
    parts.append('''
## Cost and model tokens

These totals cover the tested four patient/domain units and three figure calls only. They are **not full-pipeline per-article token estimates**, and they must not be extrapolated as if all EHR sections had been generated. Hosted latency includes serving/queue effects and is not eight-B200 performance. Tokens are provider-reported; failed requests without usage are tracked separately in the ledger.

| Model | Source | Reported input tokens | Reported output tokens | Reported USD |
|---|---|---:|---:|---:|''')
    for r in rows:
        parts.append(f"| {MODELS[r['model']]} | {NAMES[r['format']]} | {r['input_tokens']['sum']:,} | {r['output_tokens']['sum']:,} | ${r['cost_usd']:.4f} |")
    parts.append(f"\nThe shared study ledger accounted for **${budget['accounted_usd']:.4f}** against a $3.95 cap: ${budget['reported_cost_usd']:.4f} reported usage, with {budget['unknown_or_inflight']} requests retaining conservative reservations because no cost was returned. The study made {budget['requests']} paid request attempts. Account balance receipts are retained separately. No additional spending allowance was created for Firecrawl.\n")
    after=json.loads((ROOT/'budget-after.json').read_text());before=json.loads((ROOT/'budget-before.json').read_text())
    parts.append(f"The key's observed balance changed by **${after['usage']-before['usage']:.4f}** during this task and ended with **${after['limit_remaining']:.4f}** of the original $30 available. This account-level delta includes charges that timed-out calls did not report locally; the conservative ledger remains intact.\n")
    parts.append('''
## Pipeline implications

1. Retain XML, versioned metadata and asset rights as canonical provenance regardless of the model's text input. A plain-text or PDF parser is not a substitute for license verification, figure URLs or citation-target IDs.
2. Keep coherent table headers, patient columns and time groups with their values. JATS currently repeats that context per row, which costs tokens but supports independent chunks. PMC TXT is shorter in two of these three cases and preserves tabs/header groups; its chunks must carry that context forward.
3. Firecrawl is a useful fast first PDF parser. Preserve page/region IDs and inspect suspicious tables, mixed prose inside table cells, ambiguous columns, missing captions and disagreement with XML/TXT. The tested native no-OCR flag did not detect the merged-table issue.
4. Recover difficult pages with region extraction or approved vision models. Pixel-only claims require a page/region evidence path and visual verification; do not force an invented text quote into an exact-text validator. Caption-based ownership and medical image interpretation remain distinct tasks.
5. Reuse bounded targeted repair for genuine validation failures, without deleting values or erasing documented times. This format pilot deliberately kept repair off to expose each input's failure modes.

There is no basis here to replace all XML acquisition with PDF or to declare all PDFs inferior. The parser and its layout output clearly matter. A larger, held-out set with physician-reviewed fact and panel attribution labels is needed before selecting a production default from small score differences.

## Artifacts and reproducibility

All run artifacts are under `runs/source-formats-v1/`: `protocol.json`, `sources.json`, `reference.json`, `figure-gold.json`, `download-manifest.json`, `parser-timing.json`, `scores.json`, `check-audit.json`, `figure-audit.json`, `whitespace-repairs.json`, and `verification.json`. Firecrawl and region amendments live in their own subdirectories. Raw attempt files include source prompts and preserve failed model output; transient image bytes are redacted and replaced by hashed page provenance.

Run offline scoring and integrity checks with:

```sh
.venv/bin/python experiments/score_source_formats_v1.py
.venv/bin/python experiments/verify_source_formats_v1.py
.venv/bin/python experiments/report_source_formats_v1.py
```

`download_source_formats_v1.py` downloads only manifest-provided versioned URLs, verifies their MD5 plus SHA256, and imposes 12 MB per-file / 32 MB total limits. `extract_pdf_formats_v1.py` uses the bundled PDF libraries. `firecrawl_source_formats_v1.py` prepares native Markdown and a separately frozen arm. Reproduction of Firecrawl extraction uses a temporary, pinned `pdf-inspector==1.25.2` wheel. No model weights or ontologies are needed. Do not overwrite frozen protocols or reset the shared budget ledger to replay paid calls.

The fourth inspected manifest, PMC12773240.1, provided XML/TXT but no PDF URL and was excluded from the paid paired comparison. Total original PDF/TXT downloads were 5,779,042 bytes; rendering all 21 PDF pages used 5,845,132 bytes temporarily. Cleanup and test results are recorded in the run directory.
''')
    Path('reports/SOURCE_FORMAT_EXPERIMENTS.md').write_text('\n'.join(parts))


if __name__=='__main__':run()
