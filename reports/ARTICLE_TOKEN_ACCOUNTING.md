# Article lengths and measured token workload

September 29, 2026. Recomputed from saved licensed PMC sources and actual provider usage. No new model calls were made.

For the user's eight B200 GPUs, Muse Glimmer 30B is the first self-hosted candidate I would benchmark. Meta publishes its [open weights and serving artifacts](https://dev.meta.ai/docs/muse-glimmer/get-the-model). The recommendation of regular Spark 1.2 in the refinement study is a recommendation for the tested hosted API configuration; it does not establish a local Spark deployment or B200 throughput. On the difficult eight-task subset, direct extraction plus bounded retry/output recovery and local repair retained 67/68 checked fields, versus 40/68 for Glimmer with the same strategy. Glimmer benefited more from spans and context: 54/68 with span/context plus retry/recovery/local repair, or 59/68 with substantially more expensive span batching. The [refinement report](REFINEMENT_EXPERIMENTS.md) contains source audits and limitations. Those checklist counts are not clinical-accuracy percentages.

Use direct extraction with sufficient output allowance and preserve valid items during targeted repair as the baseline architecture. Keep the independently useful span/context stages configurable and use their extra work when coverage or attribution needs it. The proposed selective routing has not been evaluated end to end. A generic semantic critic is not a reliable autonomous truth gate.

**Article length: measured pilot, not a representative PMC distribution.** Seven source documents contain cases and pass the project's license audit. One is a short conference report; six are full case papers. They were selected deliberately for development, including tables, multiple patients and an animal case. P95 is linear interpolation at `(n−1)×0.95`; with six or seven documents it is highly unstable.

Words are whitespace-separated source-segment tokens in abstract, body, tables, captions and appendices, with bibliography and author metadata excluded. Table captions/column labels retained by the parser can repeat across rows; this is extracted-text length, not a publisher's editorial manuscript word count. Article-only tokens use the published Glimmer tokenizer with no chat template or special tokens. Tokens differ by model.

| Source population and unit | Mean | Median | P95 |
| --- | ---: | ---: | ---: |
| All 7 case documents — words | 2,373 | 1,978 | 4,997 |
| All 7 case documents — Glimmer source tokens | 3,474 | 2,486 | 7,355 |
| All 7 case documents — Glimmer tokens with segment labels/headings | 3,868 | 2,597 | 8,404 |
| 6 full papers, excluding conference report — words | 2,694 | 2,008 | 5,124 |
| 6 full papers, excluding conference report — Glimmer source tokens | 3,942 | 2,704 | 7,508 |
| 6 full papers, excluding conference report — Glimmer tokens with segment labels/headings | 4,396 | 2,864 | 8,555 |

The pooled conversion in these seven texts is **1.46 Glimmer tokens per whitespace word**. The actual article-by-article range is 1.26–1.61. Do not multiply a population word percentile by that average ratio and call it a measured token percentile; the table above tokenizes each document before computing statistics.

The earlier pilot reported 2,408 / 1,978 / 5,087 words for the same seven case documents. The later frozen fidelity packets include table-text cleanup, giving 2,373 / 1,978 / 4,997. Neither is a population estimate. Per-document values and source hashes are in [article-counts.json](../runs/token-accounting/article-counts.json).

**Actual per-article token workload from the earlier full text-task comparison.** These totals cover 14 clinical sections plus summary and timeline for every reference-roster patient: 11 patients across seven eligible case documents. A two-patient article contributes two sets of 16 tasks; the three-patient poisoning article contributes three. All recorded retries, truncated outputs and failed-validation generations are included. Each saved request signature is counted once, including cached/resumed original usage. All attempts in these two models' 176-task snapshots have reported input/output usage.

This is the older complete task-scope comparison, not the new local-repair strategy. Some outputs failed validation, so these are observed processing costs, not costs per clinically accepted article. Patient discovery, figures/vision, terminology coding and other experiment arms are excluded from the main totals.

| Model and per-article token total | Mean | Median | P95 |
| --- | ---: | ---: | ---: |
| Glimmer — input | 225,985 | 117,483 | 604,993 |
| Glimmer — output, including reasoning | 51,274 | 34,520 | 120,086 |
| Glimmer — output excluding reported reasoning | 25,156 | 20,226 | 65,495 |
| Glimmer — input excluding reported cache hits | 62,024 | 54,830 | 122,977 |
| Spark — input | 269,856 | 141,430 | 742,071 |
| Spark — output, including reasoning | 72,794 | 61,144 | 157,800 |
| Spark — output excluding reported reasoning | 33,600 | 23,534 | 81,579 |
| Spark — input excluding reported cache hits | 233,177 | 125,527 | 638,924 |

Completion tokens already include reported reasoning tokens; do not add reasoning a second time. Subtracting reasoning still leaves all generated responses and retries, not only final accepted JSON. Logical input sums include reused source/prompt tokens: a 600k-token article total is not one 600k-token context. Prefix caching reduced observed uncached input considerably for Glimmer, but the different providers' cache-hit behavior is not evidence of intrinsic model speed.

The long poisoning article strongly affects the tail: Glimmer used 784,911 input and 149,874 output tokens over 48 tasks/63 attempts; Spark used 969,082 input and 196,309 output over 48 tasks/78 attempts. This explains why per-article totals are much larger than source text length.

If separately measured roster-discovery overhead is added, the reconstructed totals are as follows. These are sums of two experiments, not an observed successful automatic end-to-end pipeline: clinical extraction used fixed reference rosters, and the Glimmer cat roster stage did not validate. No image or replacement-discovery cost is included.

| Model plus separate roster stage | Mean | Median | P95 |
| --- | ---: | ---: | ---: |
| Glimmer — input | 236,710 | 122,183 | 629,650 |
| Glimmer — output including reasoning | 53,734 | 38,566 | 124,399 |
| Spark — input | 277,274 | 143,558 | 754,375 |
| Spark — output including reasoning | 75,582 | 63,082 | 160,791 |

**Latest direct + retry/recovery + local-repair experiment.** Only eight task/patient pairs were tested, distributed unevenly across five articles. Therefore it does not establish full per-article token statistics for the recommended revised strategy. Its per-task numbers below include dependencies and repairs; the failure-enriched, observation-heavy task mix should not be multiplied by 16 to predict a typical patient.

| Latest strategy: per task/patient pair including repairs | Mean | Median | P95 |
| --- | ---: | ---: | ---: |
| Glimmer — input | 32,495 | 13,748 | 104,415 |
| Glimmer — output including reasoning | 9,480 | 5,567 | 25,960 |
| Spark — input | 22,455 | 11,945 | 54,206 |
| Spark — output including reasoning | 9,650 | 3,860 | 23,653 |

The [machine-readable summary](../runs/token-accounting/summary.json) also includes per-patient and per-API-call distributions and per-article totals. The [tokenizer provenance](../runs/token-accounting/tokenizer-revision.json) pins the model revision, tokenizer SHA-256 and byte count. Only the ~27 MiB tokenizer and a ~3 MiB tokenization library were temporarily downloaded; no weights or new corpus were downloaded. Both temporary artifacts were removed after measurement.

To recompute usage and summary statistics without network access or model calls:

```sh
uv run python experiments/summarize_token_accounting.py
```

To recompute the source-token counts too, provide the pinned tokenizer with `--tokenizer /path/to/tokenizer.json` and make the `tokenizers` package available. Counts were measured with `tokenizers==0.23.2`. No production code or defaults changed.
