# Within-model reasoning study and distillation

OpenPatients 2 **0.3.0**, clinical schemas **2.1.0**. This is a controlled software workflow, not a claim that any reasoning level is clinically equivalent or superior. The bundled results are explicitly synthetic mock fixtures; no B200 or real-model experiment was run to produce this release.

## Question and design

For one fixed checkpoint, does changing its documented reasoning control change extracted clinical facts, evidence spans, missingness decisions, source-validation success, and cost? A separate repeatability analysis estimates how much that checkpoint varies across runs at the **same** setting. Neither the highest reasoning setting nor majority agreement is ground truth.

`configs/reasoning/k2.yaml` defines a reservoir sample of 256 source cases, three documented K2 settings (`low`, `medium`, `high`), and three seeds (17, 43, 101). This is **nine runs of the same frozen sample**, not nine independent patient samples. With all 14 tasks it schedules 32,256 task responses, before schema warmup or network retries. The sample size is an engineering starting point, not a statistical power calculation or assurance of rare-event coverage. Select a suitably representative or deliberately stratified pilot input before preparing a study; the included reservoir sampling itself is not clinical stratification.

Held fixed: model ID and checkpoint revision, clinical schemas, full source notes, instructions, temperature/top-p, maximum total generated tokens, concurrency, endpoint topology and inference engine. Seed is paired across settings. The level order is randomized within each seed block. A fresh cache namespace prevents cross-cell prefix reuse while preserving note-first reuse among a patient's tasks **within** a cell. Shared structured-output grammar warmup occurs outside the extraction run. Equal caps of 32,768 generated tokens leave room for both reasoning and JSON; they are caps, not forced generation lengths. An exhausted cap is a truncation failure, not a valid empty extraction.

Study preparation freezes input bytes, hashes, rendered-prompt profiles for each reasoning template, configuration digests, schema signatures and optional gold labels. It forces saved requests/reasoning on and model-output repair off. Transport retries remain separately recorded. The runner refuses changed input/configuration or a switched local serving profile and checks checkpoint identity. The analyzer also rejects differing model/checkpoint/schema identities, inconsistent returned model IDs, and missing scheduled rows. An API returning the same alias cannot cryptographically prove its weights were unchanged; deployment provenance still matters.

K2's documented control is `chat_template_kwargs.reasoning_effort`. `configs/reasoning/motif-repeatability.yaml` intentionally has **one default mode with repeated seeds**: no verified low/medium/high Motif-3 switch was found in the reviewed author documentation. A generic study can supply another *documented* reasoning-control path, but the code will not silently vary arbitrary model or sampling fields. Endpoints may ignore seeds or controls; saved requests prove what was requested, not an internal implementation. Validate controls and actual returned reasoning usage in the smoke test. A fully deterministic setting may show identical outputs across seeds; that is observed repeatability, not evidence of clinical accuracy.

## Run it: CPU, then GPU, then CPU

First follow the README's model download, checkpoint pinning, environment and container preparation. From the project root:

```bash
# CPU: resolves the frozen sample and profiles each model-specific reasoning template.
uv run op2 reasoning-prepare \
  --config configs/reasoning/k2.yaml \
  --tokenizer models/k2 --workers 16

# GPU: inside an allocation; one fixed serving configuration for the whole study.
uv run --no-sync op2 reasoning-run \
  --study runs/reasoning-k2 \
  --serving configs/serving/k2-vllm-tp8.yaml

# CPU: deferred extraction exports, paired statistics and disagreement queue.
uv run op2 reasoning-analyze --study runs/reasoning-k2
```

The CPU preparation step rejects prompts whose full input plus the equal output allowance and safety reserve exceed the configured context. A 32,768-token output cap reserves context even when most responses are shorter. Increase **both** pipeline and serving `max_model_len` before preparing a study when needed; do not silently truncate notes or give only one reasoning level a smaller budget.

Omit `--serving` only when the configured endpoint(s) already serve that same pinned checkpoint. To submit the supplied Slurm template, create `logs/` first and supply the appropriate account/QOS for your allocation, `STUDY`, and `SERVING`. The template requests the eight-B200 node; no submission has been performed here. Run the CPU analysis after releasing GPUs. The GPU job does not download weights/data, resolve dependencies, tokenize the corpus, export bulky JSONL or perform bootstrap analysis.

Resuming a completed study skips completed cells; incomplete cells resume tasks from their state DB. Such a resumed wall-time report must **not** be treated as the cost of recomputing all tasks. Use a fresh study output directory for a clean speed comparison. `reasoning-prepare` refuses to overwrite an existing design. Analysis rejects incomplete studies unless `--allow-partial` is explicitly supplied and prominently marks partial results.

For reviewed labels supplied after generation:

```bash
uv run op2 reasoning-analyze --study runs/reasoning-k2 \
  --gold annotations/independently_reviewed.jsonl
```

This records a content hash and marks a post-preparation gold override rather than pretending the labels were preregistered. Gold supplied in the study YAML is copied into `frozen-gold.jsonl`; edits are rejected. The label format extends the existing annotation protocol with `documentation_status` and complete empty-negative cases. Labels not supplied for a task do not become implicit negative labels.

## What agreement means

Each record/task is paired with the same record/task and seed at another reasoning level. Same-level repeats are also paired. Facts are compared as order-independent sets after conservative canonicalization. Field order, collection order and harmless textual whitespace/case do not matter. Clinical fields **do** matter: subject, affirmation/negation/uncertainty, verification status, time, value, unit, laterality, treatment action and tumor linkage. Units remain case-sensitive. Local tumor IDs are compared by the actual tumor they reference, not by arbitrary `t1`/`t2` names. Duplicate facts are counted separately as a diagnostic. This is lexical/structured comparison, not comprehensive clinical synonym resolution or an LLM judge.

| Metric | Interpretation and denominator |
|---|---|
| Exact agreement, all scheduled | Same canonical fact set, documentation status and coverage. Failed/truncated outputs count as failures, not matches. Both-empty exact agreement is visible separately. |
| Exact agreement, nonempty | Exact same facts/status among pairs with at least one extracted fact. |
| Fact Dice and Jaccard | Set overlap on clinically qualified facts. **Both empty gives `null`, not 1.** One empty versus nonempty gives zero. |
| Both empty | Its own rate, not evidence that disease is absent or extraction is correct. |
| Documentation-status agreement | Distinguishes silent notes, explicit unknowns, nonapplicability and documented facts. |
| Exact evidence and span Dice | Conditional on matching clinical facts. Exact quote-set agreement and overlapping character intervals are separate. |
| Source-valid rate | Strict schema plus deterministic source checks. This is not clinical accuracy. |
| Cost differences | Returned token/character usage and cumulative request latency, including retry attempts where available; missing counters remain `null`. |

Evidence offsets are Unicode code-point positions with exclusive ends, not tokenizer positions or byte offsets. When an identical quote occurs multiple times, all candidate spans remain recorded. Ambiguous occurrences are excluded from the interval-overlap metric and counted explicitly; the analyzer never chooses an arbitrary occurrence to improve agreement. Quote-based evidence agreement may still be defined when offsets are ambiguous. A supported fact can legitimately cite different text, so evidence disagreement is not automatically factual error.

### Statistical analysis

For an overall metric, tasks and repeated seeds are averaged **within each source record** before uncertainty estimation. Bootstrap resampling then samples entire source clusters, preserving the paired structure and avoiding treating 14 sections or three seeds as independent patients. Known same-source IDs, exact duplicate text and user-supplied `cluster_id` connect records. This does not resolve people across publications; supply reviewed groups for near duplicates/related cases.

Default intervals are 95% pointwise **percentile cluster-bootstrap** intervals, using 2,000 resamples. Fewer than two source clusters yields no inferential interval/test. Degenerate intervals, such as `[1,1]` after all observed agreements, are flagged and do not establish zero population error.

Paired differences in prespecified overall quality/cost metrics use a two-sided cluster-level sign-flip/randomization test, exact for at most 16 clusters and Monte Carlo otherwise (9,999 permutations by default, plus-one correction). The test depends on exchangeability of paired labels under the null; blocked order and matching mitigate, but do not abolish, hardware/order effects. Holm correction is applied jointly across **all prespecified overall primary-difference p-values**. Per-task intervals are exploratory; neither they nor the displayed 95% intervals are familywise-adjusted confidence intervals.

Agreement metrics receive intervals rather than p-values against a meaningless universal "chance agreement" baseline. There is no automatic kappa score for an unbounded set of clinical facts with an undefined true-negative universe. Gold labels enable additional quality tests: clinical fact recall, F1 for complete labels, exact-task success, explicitly annotated empty-negative-case success, and forbidden-error checks. Partial gold reports recall without treating unlabeled predictions as false positives. Failure is not a successful negative case.

**A nonsignificant difference is not equivalence or noninferiority.** Choosing a cheaper reasoning level still needs clinically reviewed evidence, prespecified acceptable margins, and scrutiny of critical errors and rare subgroups. The report deliberately sets `clinical_equivalence_established: false`. Highest-effort output and consensus are never substituted for adjudicated truth.

## Reasoning saved alongside the extraction

Both vLLM `reasoning` and `reasoning_content` fields are supported. The client also preserves returned structured/opaque `reasoning_details` without pretending it is readable chain text or decoding an encrypted block. Aliases are retained but not double-counted. Only text actually returned by the endpoint is saved: no reconstruction, elicited private trace, or fabricated rationale.

After `op2 export` (or reasoning analysis):

```text
patients.jsonl      merged sections + generations[task] with the selected returned reasoning
extractions.jsonl   one case/task row: extraction, reasoning_text, full request/response provenance
attempts.jsonl      every attempt from that run, including invalid/truncated/repaired responses
```

Task rows include requested model/revision, sampling and reasoning control, input hash, prompt/schema signature, exact chat messages, selected attempt ID, finish reason, usage, validation and source evidence. `reasoning_status` distinguishes returned text, returned nontext, missing/empty fields, disabled saving, and no response. Missing reasoning remains `null`; it is **not proof the model did no reasoning**. The endpoint parser must actually expose reasoning fields. Final-answer-only providers remain usable.

Retries never concatenate unrelated traces. A final successful row points to the selected response, and failed attempts remain auditable. Reasoning is not embedded as an extracted clinical fact, not copied into the clinical index, and not used as evidence supporting cohort membership. Disable storage in ordinary runs via `api.save_reasoning: false` or `api.save_messages: false`; controlled reasoning studies force both on for reproducibility.

These files contain clinical source text and model-derived sensitive material. Export/state/index files are mode 0600; use restrictive directories, approved storage and endpoints, and your institution's access/retention policies. They are not automatically deidentified. Credentials from API headers are not serialized; avoid putting secrets in URLs or custom request fields. Full prompts are storage-intensive, so budget disk and compress/archive completed CPU exports according to policy.

## Distillation export

Answer-only targets are the default, because a faster student extractor need not reproduce the teacher's lengthy reasoning. The returned teacher text is retained in the original extraction archive regardless. A review file binds approval to the exact input and task signature:

```bash
# RUN is one cell directory listed in runs/reasoning-k2/study.json, or a normal run.
RUN=runs/k2
uv run op2 review-template --run "$RUN" --output reviews/k2.jsonl

# A reviewer must fill approved=true and reviewer for accepted source-grounded rows.
uv run op2 distill --run "$RUN" --reviews reviews/k2.jsonl \
  --output data/student-answer-only.jsonl

# Separate reasoning_approved=true is also required for reasoning-bearing targets.
uv run op2 distill --run "$RUN" --reviews reviews/k2.jsonl \
  --include-reasoning --output data/student-reasoning-and-answer.jsonl
```

Default review entries are **not approved**. Stale approvals do not authorize changed inputs/prompts. Only complete, valid outputs are exported; supported empty outputs remain eligible. A separate `--allow-unreviewed-candidates` flag permits explicitly marked candidate generation, not automatic certification of clinical labels. Agreement is never an automatic approval rule.

The JSONL includes the original messages plus the teacher assistant answer, optional separate `assistant.reasoning`, structured extraction, review status, source group, checkpoint/parameters and split. The student training adapter must map reasoning to the **student's** actual chat template and mask source/system tokens from the loss; this release exports data, not a student training stack. A returned reasoning trace can contain speculation or errors even when the answer is correct. Review reasoning independently and compare answer-only versus reasoning-bearing students on held-out clinical gold.

All tasks belonging to linked source groups/exact duplicates share a deterministic train/validation/test group split (approximately 80/10/10, not guaranteed row proportions). Freeze a common complete group manifest before combining multiple exports; independently splitting changing subsets can break leakage protection. Group near duplicates and related reports upstream. Keep teacher-comparison and student-test labels separate from training selections. Dataset, source, model and terminology rights require review before distillation or redistribution.

## Executable synthetic example

```bash
uv run op2 reasoning-demo --output runs/reasoning-demo
```

This exercises six synthetic notes, three named mock modes and two seeds across all 14 tasks: 504 task requests to an in-process **mock** endpoint, not an LLM. Known omissions deliberately produce disagreements, including an empty-negative fixture. The report is labeled synthetic; no throughput or clinical model claims should be drawn from it. `reports/reasoning-demo.md` is a packaged example of the output format.

## Primary references reviewed

- K2 vLLM recipe and documented reasoning controls: https://recipes.vllm.ai/IFM/K2-Horizon-375B-A23B
- vLLM reasoning field, compatibility and visibility: https://docs.vllm.ai/en/latest/features/reasoning_outputs/
- Motif-3 NVFP4 author card: https://huggingface.co/Motif-Technologies/Motif-3-NVFP4
- Paired bootstrap concepts: https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html
- Paired permutation concepts: https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.permutation_test.html
- Holm familywise correction: https://www.statsmodels.org/stable/generated/statsmodels.stats.multitest.multipletests.html

The implementation uses Python/NumPy, not SciPy or statsmodels as runtime dependencies. The latter references explain statistical procedures; they are not endorsements of this clinical study design.

## Source-article grouping

When available, the normalized source PMID, PMCID and DOI join explicit cluster IDs, shared source IDs and exact-text duplicates in the record grouping. Cases linked to one known paper stay in the same statistical/bootstrap cluster and distillation split. Unknown or contradictory source identifiers cannot establish a trusted article link. Original source rows and figure candidates remain export provenance; article captions are not added to model prompts.
