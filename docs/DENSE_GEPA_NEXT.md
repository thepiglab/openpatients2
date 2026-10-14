# Progressive extraction and quality-constrained GEPA

This is a new campaign, independent of the completed GEPA and Fastino runs.
Launch only after committing/pushing these changes yourself and pulling on HPG:

```bash
bash scripts/run_dense_gepa_next.sh
# Optional: longer cap / more search evaluations per engine
DENSE_HOURS=16 DENSE_METRIC_CALLS=1800 bash scripts/run_dense_gepa_next.sh
```

Default: 12-hour cap, one B200, 8 CPUs, 60G host RAM. CPU preparation and model
setup precede the GPU job. CPU aggregation, integrity verification and automatic
`results.tar.gz` export follow even when the GPU job fails. One private runtime,
no cancellation or modification of another campaign. Duplicate-launch guard and
normal queued sbatch submission are inherited from the existing launcher.

```bash
bash scripts/run_dense_gepa_next.sh status
WORK="$(cat runs/dense-gepa-next-last-work.txt)"
cat "$WORK/summary/comparison.md"
bash scripts/run_dense_gepa_next.sh verify
ls -lh "$WORK/results.tar.gz"*
printf 'scp wkieffer@hpg.rc.ufl.edu:%s/results.tar.gz\* ~/Downloads/\n' "$WORK"
```

Run the printed SCP command on the Mac. Use a separate Downloads subfolder if
retaining multiple archives with the same filename.

## Experimental arms

Only observations and medications evolve. Conditions and procedures are left
at their existing production settings and are not evaluated by this campaign.

- **control**: current task prompt and bounded validation/repair.
- **compact**: fixed short evidence/attribute checklist, no GEPA.
- **direct**: GEPA evolves a supplement capped at 2,400 characters.
- **additive**: GEPA evolves a separate omission pass. It sees the original
  source and the baseline extraction, returns only additions, and passes the
  same schema/literal evidence validator. Existing facts are not edited.
  Same-name/same-time overlapping facts are suppressed; the merged section is
  validated again. Conflicts and rejected merges are saved for review.
- **gated_additive**: a live predeclared source-only gate. A failed/partial or
  limited-coverage baseline escalates; otherwise escalation occurs when at least
  35% of distinct numbers in source are absent from baseline item quotations.
  This is a deliberately simple hypothesis, not a reliable completeness test.
  It uses no annotated facts or gold item counts. The gate operates per article.
  It skips the second inference pass when the gate does not trigger. The live
  output and second-pass latency are measured; total latency includes baseline.

The gate may trigger nearly everywhere, especially for articles with background
numbers or tables. Its trigger rate is reported; a zero-cost speedup is never
assumed. A learned router or evidence-local second pass is a later experiment if
this arm establishes a useful tradeoff. No human preferences or RLHF are claimed.

## Reward and selection

Search retains F2 but combines it equally with an attribute-aware F2: a matched
item with an assertion, subject, value or unit mismatch counts as both an
unusable prediction and a missed correct fact. Search penalizes precision loss
relative to each baseline article and individual attribute deterioration, plus
invalid delivery and corruption of previously correct matched fields.

This metric still inherits the existing deterministic matcher's limitations;
"grounded" in field names means attribute agreement, not independent clinical
verification. Diagnostics distinguish newly matched items from fields that
regress on items matched in both arms, and also report lost matches.

126 articles supply reflection; 32 training articles select candidates. The
59 development/validation articles provide fresh paired confirmation. The 43
test articles never enter preparation/inference/training export. These are fixed
annotated patient packets, not an end-to-end live-roster benchmark. All patients
from an article remain in the same partition. Development validation has already
been inspected in previous campaigns and is not a pristine test.

Four concurrent GEPA engines (two tasks × direct/additive) share one model and
64 request slots. Each gets 1,200 metric evaluations by default. Search can stop
early; its hard per-rollout deadline reserves one hour for reranking and three
hours for confirmation, plus startup/shutdown headroom. A timeout can leave an
incomplete campaign; CPU export preserves evidence and `verify` fails.

Each engine's top three distinct prompts are reranked across training seeds 42
and 43. Hard gates require F2 improvement, nondecreasing micro/attribute-aware F2,
precision >= max(80%, baseline minus one point), bounded overall and individual
attribute losses, preserved common-fact fields, and no material delivery loss.
The best gate-passing strategy is frozen before confirmation, or control is
preselected if none passes. All alternatives remain diagnostic comparisons;
confirmation is not used to cherry-pick a different primary winner. Both
confirmation seeds must pass before proposing the primary for a separate
full-pipeline evaluation. The fixed gated policy is exploratory and is not auto-selected.

Confirmation repeats all four alternatives and control. Baseline outputs are
shared across paired alternatives; additive times include baseline plus the
second pass. Arms are reversed on the second seed, but the baseline must precede
additions. Timing remains cache/load-confounded. Reports show mean, median, p95
article-task latency, worker wall time, gate rate, field support counts, detailed
failed gates and article-cluster descriptive F2 intervals. GPU telemetry and
DFlash counters remain in worker results.

No constrained decoding. Same pinned Glimmer FP8, native chat template, medium
reasoning, vLLM 0.30.0 and DFlash settings as the completed campaign. Online reward
matching runs on the GPU worker's CPUs because search requires immediate feedback;
large offline aggregation, dataset validation, downloads and compression run in
CPU allocations. The GPU exits as soon as its work finishes.

## Fine-tuning path

CPU preparation also writes `inputs/training-data/{train,eval}.jsonl` as
conversational prompt/completion examples using actual clinical prompts and gold
sections. It uses only the 126/32 training partitions. Each target passes schema
and literal-evidence validation. Whole examples with review-only facts or invalid
gold targets are quarantined in `rejected.jsonl`; annotations are never changed.
`audit.json` records counts and remaining readiness checks. These are supervised
candidate examples, not a claim that training ran or a GPU-ready recipe.

Recommended order:

1. **Supervised LoRA** on the BF16 base: a reasonable first weight-training pilot.
   Start with short complete packets, microbatch one, checkpointing, low rank,
   completion-only loss and a small epoch sweep. Tokenize first and report every
   excluded long example; never truncate away target evidence. Select using
   article-disjoint extraction metrics, not training loss alone.
2. **QLoRA** if memory measurements justify it. This adds a separate bitsandbytes
   quantization path; the deployed compressed-tensors FP8 artifact is not a
   drop-in QLoRA training base. Long-context activations can dominate memory even
   when weights are quantized.
3. **DPO** after constructing quality-gated chosen/rejected pairs on identical
   source prompts. Candidate generation from gold training diagnostics can
   bootstrap these pairs, but different-prompt GEPA outputs cannot simply be
   called an unbiased DPO pair. Human review of preference criteria remains useful.
4. **GRPO / verifiable-reward RL** after the reward audit. Our deterministic
   annotation-based reward makes this closer to verifiable-reward RL than RLHF.
   LoRA and RL are compatible: LoRA describes which weights change; RL describes
   how learning is driven. Online rollouts increase compute and can exploit a
   fuzzy matcher or gaps in gold labels. Do not start by maximizing raw recall.

260 articles can yield many patient/task examples, but there are still only 260
independent article groups. Do not reshuffle patient records across train/test or
train on the 59 confirmation/43 test articles to inflate dataset size. The default
new campaign does not train weights: first audit exported target acceptance and
lengths, pin the BF16 checkpoint/training environment and run a forward/backward
and generation smoke test. DFlash speed after adapter tuning must be remeasured.

Sources checked October 13, 2026:
- [GEPA FAQ](https://gepa-ai.github.io/gepa/guides/faq/) and
  [adapters](https://gepa-ai.github.io/gepa/guides/adapters/): use executable feedback,
  explicit efficiency objectives, representative selection and guard against copied examples.
- [Glimmer TRL guidance](https://huggingface.co/blog/muse-glimmer#fine-tuning-with-trl):
  model-specific LoRA SFT and GRPO support; reports BF16 LoRA SFT on one 80GB H100
  with microbatch one/checkpointing. That is encouraging for B200, not a memory
  guarantee for our long packets.
- [PEFT quantization](https://huggingface.co/docs/peft/developer_guides/quantization),
  [TRL SFT](https://huggingface.co/docs/trl/sft_trainer),
  [TRL GRPO](https://huggingface.co/docs/trl/main/grpo_trainer).
