# Offline semantic benchmark

This adds experimental source-grounded semantic **fact agreement** alongside the
existing deterministic scores. It does not overwrite references, campaign
predictions, integrity receipts, reasoning, or strict citation/schema validity.
No judge endpoint is called by campaign aggregation. Use an explicitly configured
small model on an OpenAI-compatible endpoint; no paid service or model is selected
implicitly.

## Prepare, run, and resume

Use the current checkout's project environment (`uv run --extra data python`, or
`.venv/bin/python` after dependency installation). `WORK` is an extracted campaign
directory containing manifest, reference, inputs, and arm summaries. `JUDGED`
must be a new directory outside the immutable campaign.

```bash
WORK=/path/to/run-20261017-134453
JUDGED=/path/to/run-20261017-134453-semantic
uv run --extra data python -m openpatients2.semantic_benchmark prepare \
  --work "$WORK" --out "$JUDGED" --splits train
```

Preparation reports the number of requests before inference. For the archived
two-article, eight-arm October 17 run, default batching produces 7,903 requests.
All candidate/reference pairs within the existing clinical matching pool are
covered; lexical top-k retrieval does not exclude possible semantic matches.
Patient alignment still uses the existing deterministic identity/content method
and should be checked during calibration. No reference patient IDs are assumed
to match model patient IDs.

Once the chosen judge is served, set its base URL, served name, and immutable
checkpoint revision explicitly. This example intentionally uses placeholders:

```bash
uv run --extra data python -m openpatients2.semantic_benchmark run \
  --out "$JUDGED" --base-url http://127.0.0.1:8001/v1 \
  --model YOUR_SERVED_JUDGE --revision YOUR_PINNED_REVISION \
  --max-requests 100 --concurrency 2
```

The default 100-request bound supports an initial format/calibration check. Repeat
the same command to resume, or increase `--max-requests` to process a larger
remaining batch. Valid completed judgments are not called again. Model, revision,
prompt, output budget, endpoint and input hashes are fixed for a packet; changed
judge settings require a new packet. Only one process may run a packet at a time.
The recorded revision is operator-supplied, not an independent weight attestation.

Use `JUDGE_API_KEY` only if the endpoint needs authentication. Its value is never
written to manifests or receipts. `--api-key-env` can name another variable.
Raw returned responses, including any returned reasoning and usage, are saved.
Context overflow, invalid JSON, truncated responses, and unsupported JSON mode
produce failed receipts rather than invented decisions. Sources are not silently
shortened; increase the explicit input bound/use a suitable judge context when
necessary. The default output cap is 4,096 tokens and input bound 200,000 characters.

```bash
uv run --extra data python -m openpatients2.semantic_benchmark score --out "$JUDGED"
```

## Interpretation and calibration

The judge sees the source, patient identity context, candidate and reference facts.
It does **not** see extracting model names, prior scores, or their reasoning.
Equivalent wording is allowed; contradictions in patient/subject, negation,
certainty, occurrence, quantity/unit, dose or timing do not count as equivalence.
Every equivalent decision requires both claims to be assessed as source-supported
and the candidate to have a literal source citation returned by the judge.
These citations do not repair missing or invalid citations in the original output.

Matching is one-to-one, so duplicated predictions cannot inflate recall. Required
references are matched first; review-only references can absorb remaining matches
without becoming required recall targets. Unmatched source-supported predictions
are queued for reference-omission/duplicate review, not automatically promoted
to true positives. Unsupported-reference flags do not silently remove false
negatives. Inconsistent support judgments across batches receive no equivalence
credit and are queued for review.

`scores.json` has completeness, judge provenance, per-arm and per-split agreement.
Incomplete arms have **null scores**, not deceptively complete partial scores.
The clinical scope excludes case context, summaries, timeline graphs, figures,
and EHR series; retain their existing separate metrics. This is stricter semantic
fact agreement, not interchangeable with the existing item-detection F1, which
scores many attributes separately.

`review-queue.jsonl` contains contradictions, uncertain or unsupported-reference
decisions, inconsistent source support, and possible reference omissions.
`calibration-sample.jsonl` selects up to 100 completed requests by stable hash,
including agreements, for independent human review. Review a balanced set across
models, task types, and decision categories; estimate agreement and failure modes
before expanding judging. These artifacts support calibration but do not declare
it complete. Reports always label the judge unreviewed; no automatic promotion or
distillation eligibility is assigned.

Default preparation is train-only. Use `--splits validation` after freezing the
judge protocol; test requires `--splits test --include-test`. Keep calibration and
prompt changes confined to training data. Do not use validation/test traces as
student training examples.
