# Le Chonk 🐱 — Mistral Large 4 campaign

This is a separate opt-in campaign; Beam remains the default. The target is
`mistralai/Mistral-Large-4-1T-A52B-NVFP4`, revision
`d5fdac874617651e8dac18fe8609075700ce8c93`. Official container tag
`vllm/vllm-openai:mistral-large-4` was resolved to the Linux amd64 digest
`sha256:13bc2adaca593fddfa8f88e41f7cea53582d46d169d63bd86e1477157575fa52`.

Sources checked 2026-10-29:
- https://huggingface.co/mistralai/Mistral-Large-4-1T-A52B-NVFP4
- https://huggingface.co/mistralai/Mistral-Large-4-1T-A52B
The linked vLLM recipe URL returned 404 when checked, so the publisher's
NVFP4 model-card command is the serving authority.

## Serving

One node with eight B200s, TP8, 16 CPUs and 200 GiB host RAM. The embedded
DSpark head proposes three tokens (`method=dspark`); no external drafter is
needed. Use native params.json/config format and native weight loading, the
checkpoint chat template, Mistral reasoning/tool parsers and fp8_ds_mla KV cache.
Temperature 1.0, top-p .95, high reasoning, for both text and images.

Project adaptations: 262,144-token context, 16 concurrent requests/sequences,
16,384 batched prefill tokens, GPU utilization .85, two loading workers,
four-hour article timeout. Timeline windows and batches use the current repair
candidate; this is not a matched comparison against old Beam pipeline settings.
The model supports a larger context, but full 1M serving has not been validated
on this allocation. Neither GPU fit nor host RAM peaks have been measured yet.

The first smoke (`run-20261029-110126`) downloaded successfully but stopped at
the launcher's incorrect `config.json` requirement, before loading weights.
The fix passes the pinned `params.json` filename into serving and runs the same
artifact gate on CPU before publishing download readiness. See
[the diagnostic report](reports/LE_CHONK_20261029.md). Start a new smoke after
committing/syncing the fix; resuming that run reuses its frozen, defective code.

CPU preflight/download, then GPU inference, then CPU cleanup and aggregation.
Conservative disk preflight: 750 GiB runtime estimate + 128 GiB reserve = 878 GiB
free. This is a planning allowance, not a measured checkpoint size. All eight
GPUs belong to this worker; do not overlap another GPU campaign within the same
allocation. The launcher retains the existing active-campaign guard.

The first download inventory measured 568.15 GiB of weight shards and 576.42 GiB
including the container and other retained files. Conversion/download scratch
and outputs need additional space; keep the conservative preflight allowance.

## Launch after committing and syncing code

```bash
(
  set -a
  source .env
  set +a
  DENSE_ACCOUNT=cai5724 DENSE_QOS=cai5724 bash scripts/run_dense_le_chonk.sh smoke
)
```

Default smoke: two diverse training articles / eight-hour worker limit.
After verifying successful image input, exact tokenization and returned reasoning,
use `pilot` for 48 diverse training articles / 24 hours, then `long` for all
train+validation / 72 hours. Test stays held out. Limits do not guarantee completion.
Logs and filesystem paths use ASCII; Slurm job names contain `le-chonk-🐱`.
If the cluster rejects Unicode job names, report that submission error rather
than claiming a successful launch.

```bash
squeue -u "$USER" -o "%.18i %.12P %.85j %.10T %.12M %R"
bash scripts/run_dense_nemotron_super.sh status
bash scripts/run_dense_nemotron_super.sh verify
```

The campaign shares the existing latest-work pointer. Save the printed absolute
campaign path when switching experiments. No jobs are submitted by local tests.

Reports include precision/recall/F1, attribute and timeline scores, failure
inventories, input/output/reasoning tokens, article means, tok/s, docs/hour,
GPU cost proxies and speculative acceptance when exposed. Original response
reasoning is retained, and a real streaming reasoning probe gates article work.
Acceptance alone is not speedup; a matched speculation-off experiment is a
follow-up after this new architecture passes its hardware smoke.
