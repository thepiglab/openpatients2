# Overnight clinical fidelity / GEPA trial

Run this **after updating the HiPerGator checkout**:

```bash
cd /blue/cai6734/ehr_agent/openpatients2
bash scripts/run_overnight_pilot.sh
```

The launcher picks a fresh timestamped sibling folder under
`/blue/cai6734/ehr_agent/op2-clinical-overnight`. An optional first argument
sets another new work directory. Submission preflights the entire held job chain,
then releases CPU preparation → CPU container setup → CPU checkpoint download →
eight B200s on **one node** → CPU model cleanup → report → CPU article cleanup.
No GPU is requested for downloads, tokenization or cleanup. Account/QOS are
`cai5724`; the GPU allocation requests 32 CPUs / 250 GB / eight B200s / 12 hours.

This is a bounded experiment, not a changed production default. It uses the same
pinned Red Hat Glimmer FP8 checkpoint, Meta chat template and DFlash head as the
previous campaign. No BF16 main checkpoint or additional model is downloaded.
All inference, including GEPA reflection, uses local loopback servers. There is
no hosted API charge and no fine-tuning. JSON is requested in prompts and parsed
by application code; schema-constrained decoding is not used.

## Experiment plan

Fifteen extraction variants run with seeds 42–45 on the 20 canonical articles:

| Variant | Change relative to the paired control |
| --- | --- |
| baseline | Previous compact source-aware extraction, two repairs |
| focused | Isolated primary discovery, focused pixel context, staged panel attribution |
| ordering | Focused + independent relative-order review of immutable events |
| clinical-audit | Ordering + independent clinical feature inventory, coverage and claim audits |
| coverage-backfill | Clinical audit + one source-grounded omission repair per affected task |
| joint-pixels | Clinical audit with combined pixel description/attribution |
| low-/high-/xhigh-reasoning | Focused extraction with different template reasoning strengths |
| xhigh-32k-output | xhigh with 32K output allowance; separate from the matched 16K-cap comparison |
| no-repair / one-repair | Focused extraction with zero/one validation repair |
| whole-text | Focused pipeline with complete article context for patient tasks |
| live-roster | Focused extraction gated by independent live patient discovery |
| gepa | Clinical-audit pipeline with experimental GEPA prompt supplements |

The primary serving cell is 64K context, 32K prefill budget, eight TP1 replicas,
DFlash. Four confirmation cells rerun **focused and gepa** with two seeds:

| Context | Prefill | Replicas × TP | Draft |
| ---: | ---: | --- | --- |
| 65,536 | 8,192 | 8 × 1 | DFlash |
| 65,536 | 32,768 | 4 × 2 | DFlash |
| 65,536 | 32,768 | 8 × 1 | off |
| 131,072 | 32,768 | 8 × 1 | DFlash |

TP1/DFlash at 64K/32K is the matched control for each changed factor. Larger
contexts still use exact runtime `/tokenize` guards including pixel expansion;
there is no source truncation. A context/startup failure is recorded and later
cells continue. All-GPU and per-GPU token rates use the declared physical GPU
count, including four TP2 servers. Startup, queue wait and warmup are excluded
from extraction token rates and included separately in stage duration where
appropriate. Different clinical/audit work is not an identical throughput load.

The job has a **10-hour work budget inside a 12-hour allocation**, allowing time
for shutdown. The primary cell has a seven-hour ceiling; confirmation cells
have 30-minute ceilings each. A 15-minute reserve prevents starting another full
extraction trial too close to a cell deadline. Runtime is not padded. Actual
completion time depends on source lengths, GPU availability and model responses.
Unfinished/deferred trials are explicit and make the GPU stage `partial`.
Already completed tasks and trial receipts remain available.

## Clinical completeness and patient state

The independent inventory reads the complete original article with the target patient identity, without the
extracted output. It lists symptoms, history, positive/negative/suspected diagnoses,
serial tests/results, specimen and units, medication changes, procedures/devices,
allergies, genetics/family/social context, oncology, reproductive/perinatal findings,
plans, complications, response and follow-up in the existing 14 task classes.

A separate coverage audit compares every inventoried feature to specific extracted
fact IDs. A claim audit reviews every extracted claim in batches of 16. It checks
patient ownership, uncertainty, negation, quantities, reference versus patient
results, planned versus performed care and encounter associations. Model judgments
are **review signals**, not clinical accuracy labels. They do not delete facts.

Backfill is a separate opt-in ablation: one complete source-grounded regeneration
of each task flagged with an omission, using full article context to catch errors in compact source selection. Application validation must pass and accepted
facts, values and units must survive. Original task responses remain in the ledger.
The initial coverage audit is retained as a before-backfill measure, not falsely
reported as final recall. Added facts do not silently acquire timeline events.

Patient bundles expose a `patient_state_index`: clinical fact IDs, extracted values,
and explicit event links. Unlinked facts have unknown time; states are not carried
forward by assumption. The ordering pass edits only source-backed graph edges;
clinical values and events are immutable. Cycles, unknown endpoints, wrong patient
IDs, nonliteral or ambiguous spans and invented numeric offsets are blocked.
Relative history → presentation → treatment → response/follow-up requires evidence;
paragraph order and common clinical practice do not establish it. No dates are
invented and disconnected events remain unordered.

Numeric-only `text_value` fields now bind a unique complete scalar in the same
literal source quote before comparing a separately populated unit. Scientific
mantissa/exponent and expanded decimal magnitude remain separate. Conflicting units
or scale still produce conflicts; duplicate candidates and flattened exponents stay
unresolved. This does not establish specimen/analyte entailment.

## Figures

Focused pixel prompts retain the caption, source references/neighbors and all
patient identities. Both actual prepared pixels and caption text are supplied.
Description distinguishes raw visual observations from caption claims and clinical
interpretation, including chart type/readings, modalities/submodalities, domain,
body parts and clinical significance.

Staged attribution receives the visual panel inventory and must assign exactly
those panel IDs, including aggregate/background/unresolved regions. Joint
attribution is a separate ablation with the same panel gate. A dashboard's population
panel cannot pass by replacing the entire panel inventory with one patient-owned
composite. Patient `panel_columns` retain only that patient's assigned panels;
whole-figure descriptions and URLs are explicitly article-wide assets. This is a
structural gate, not proof that panel boundaries or patient ownership are correct.

## GEPA

This uses the actual **`gepa==0.1.4`** framework, with a custom adapter, Pareto
selection, reflective mutation and bounded task evaluations. See the
[official framework](https://gepa-ai.github.io/gepa/) and
[GEPA paper](https://arxiv.org/abs/2507.19457).

Every clinical task, roster, summary, timeline, figure/pixel, ordering, inventory,
coverage, claim-audit and repair prompt family is eligible for a separate search.
The optimized parameter is an **additional task instruction**; schemas, source
contracts, validators and system instructions are fixed. This makes candidate
changes auditable and keeps production prompts intact.

Bootstrap trials provide real request/response/error traces. Articles are split
10 train / five validation / five test; patients, panels and repeated seeds from
one article always stay together. Test inputs and labels never enter reflection.
Related cases across different publications have not been deduplicated, so this
is article-held-out generalization, not proven patient-held-out generalization.

Each prompt gets up to 128 task evaluations and a fair share of a two-hour GEPA
wall budget. The same local Glimmer supplies reflection; no extra checkpoint is
required. Candidate pools, trajectories, scores, split manifests and selected
supplements are retained. Source identifiers and copied long source passages are
rejected from supplements. This is a memorization check, not proof against overfitting.

Clinical tasks with reviewed assertions use 80% required-fact delivery and 20%
structural validity; a known forbidden fact makes the score zero. Evidence quotes
cannot satisfy result patterns. Tasks without clinical gold use **structural-only**
rewards, clearly marked in feedback. A structural improvement cannot be called a
medical improvement. Gold is a finite checklist, not full precision/recall, and
no output-count reward encourages extra claims. Prompt families lacking disjoint
train/validation examples are explicitly unavailable, rather than using test
examples or claiming they were optimized. Selection uses validation only; final
reports compare baseline and GEPA on untouched test-article assertions.

One possible limitation: separately chosen task supplements can interact. The final
paired full-pipeline GEPA arm tests that combined candidate; it is not automatically
accepted into production.

## New sources, disk and cleanup

CPU acquisition covers fourteen specialties, including maternal/perinatal,
pediatric/genetic, toxicology and veterinary cases, with case-focused and broader
lanes. Up to 420 candidates yield at most 48 eligible inference articles; the
actual sample can be smaller. The new sources never enter GEPA. Four seeds test
baseline, clinical-audit and GEPA using the same hash-checked generated roster
per seed, so patient count changes cannot explain differences between those arms.
Generated rosters are explicitly unreviewed and never labeled hand-reviewed gold.

The source acquisition cap is **1 GB network / 2 GB stored**, plus bounded tokenizer
and pixel preparation. Rights are filtered through the existing article policy
before extraction. CPU XML fetches use eight workers and a shared rate limiter;
length profiling uses up to 32 processes. Raw JATS acquisition trees, tokenizer
files, inference source exports and model weights are deleted after reporting.
A bounded source review snapshot (at most 32 MB), prepared review pixels, prompt
traces, source licenses/hashes/URLs and scalar length counts remain so omissions
and factuality can be reviewed after cleanup. No tokenized article files are stored.

vLLM development mode is enabled only on the benchmark's loopback servers to
expose `/reset_prefix_cache`. Each trial records reset success and warmup. A 200
response with `success=false` is not a successful reset. Failed resets make speed
comparisons uncontrolled. See the [official vLLM serving documentation](https://github.com/vllm-project/vllm/blob/main/docs/serving/online_serving/README.md).

## Inspect and transfer results

```bash
run_dir=$(ls -dt /blue/cai6734/ehr_agent/op2-clinical-overnight/run-*/ | head -n 1)
run_dir=${run_dir%/}
cat "$run_dir/COMPARISON.md"
cat "$run_dir/summary.json"
cat "$run_dir/progress.json"
cat "$run_dir/prompt-optimization/progress.json"
squeue -u "$USER"
```

`summary.json` distinguishes GPU completion, cleanup, failed/deferred trials and
prompt optimization coverage. `COMPARISON.md` separates clinical task validity,
finite required/forbidden facts, held-out GEPA facts, review signals and throughput.
Live-roster scores are independent from frozen-roster extraction. The new-source
patient/image claims still require source/pixel adjudication.

From the repository, export the allowlisted results (models, raw acquisition
folders and environments are excluded):

```bash
uv run --locked --python 3.12 --no-dev op2 corpus-pilot export-results \
  --work-dir "$run_dir" --output "${run_dir}-results.tar.gz"
```

On your Mac:

```bash
scp wkieffer@hpg.rc.ufl.edu:/blue/cai6734/ehr_agent/op2-clinical-overnight/RUN-NAME-results.tar.gz ~/Downloads/
```

Do not update the running checkout during a campaign: source/config/lockfile
hashes are pinned. Model and article cleanup can still use ownership receipts
if code changes after a run.
