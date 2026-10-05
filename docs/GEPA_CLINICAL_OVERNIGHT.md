# Clinical GEPA overnight campaign

Run the updated checkout on HiPerGator:

```bash
cd /blue/cai6734/ehr_agent/openpatients2
bash scripts/run_gepa_clinical_overnight.sh
```

An optional first argument chooses a new work directory. The default is a timestamped
sibling `../op2-gepa-clinical/run-*`. This submits the entire dependency chain and
returns immediately; the login shell can close. Do not change the checkout while
jobs are using its pinned runtime manifest. This is a **fresh campaign**, not a
resume of the previous optimizer's inputs or scoring rules.

## What changed after the October 4 run

- Legacy source snapshots are reconstructed into canonical article text and checked
  against their original SHA-256. Body text is checked; supplementary manifests
  absent from a legacy snapshot are explicitly unavailable. New bounded review
  snapshots preserve complete selected article packets, including supplements.
- CPU readiness now runs source gates before a serving process starts. Discovery
  refuses to export an incomplete cached roster registry. Individual model discovery
  failures remain recorded; an empty registry cannot masquerade as success.
- Concurrent GEPA engines use per-family `LoggerProtocol` files without replacing
  global stdout/stderr, fixing the closed-file error. Tests run concurrent real GEPA
  engines and real multi-proposal optimization against mocked local inference.
- Repair search replays saved failed candidates and errors directly at the repair
  turn, rather than hoping a regenerated initial answer needs repair. Invalid audit
  IDs, episode phrases, figure ownership and offsets can be corrected. Accepted
  clinical values and accepted graph/panel atoms remain protected; originals and
  protection decisions remain in attempt logs.
- Uniform held-out denominators are reconstructed from saved checklist rows for
  every arm, including bootstrap trials completed before the optimizer ran.

## Allocation and budgets

| Stage | Allocation | Work budget |
| --- | --- | --- |
| Prepare/setup/download | CPU only, up to 32 CPUs | Existing bounded acquisition and tokenizer profile |
| Bootstrap | Eight B200s on one node | Five extraction controls, including live roster discovery |
| GEPA | One B200, 16 CPUs, 96 GB | Two profiles, up to three hours of search; four-hour Slurm limit |
| Extraction suite | Eight B200s, 32 CPUs, 250 GB on one node | Up to eight hours, twelve-hour Slurm limit |
| Cleanup/report | CPU only | Delete owned checkpoint and acquisition downloads; retain bounded review inputs and results |

Based on the previous roughly five-hour suite, expect roughly **9–12 hours of active
work**, depending on output lengths and search convergence. This is an estimate,
not a forced delay or a total wall-clock limit; CPU downloads and Slurm queues add
time. Bootstrap is a separate allocation. The main cell has 6.5 hours, the 128K
confirmation cell one hour, with the remaining extraction-stage allowance for
startup/drain. Deadline reserves defer unfinished trials explicitly. A campaign
can therefore finish with `partial` even after using its intended budget.

The same pinned Red Hat FP8 checkpoint, DFlash draft and official chat template
are used. No BF16 checkpoint is downloaded. Each extraction server runs on one GPU
(eight replicas); only one GPU is reserved during smaller GEPA minibatches. Setup,
dependencies (including `gepa==0.1.4`), article/model downloads and both cleanups
remain CPU jobs. Utilization, server metrics and token rates remain recorded.

## GEPA setup and evidence

The official [adapter guide](https://gepa-ai.github.io/gepa/guides/adapters/)
recommends diagnostic feedback and explicit failure handling. The
[batch sampling guide](https://gepa-ai.github.io/gepa/guides/batch-sampling/) describes
sampling across the training set; the
[parallel proposal guide](https://gepa-ai.github.io/gepa/guides/parallel-proposals/)
explains that adapter batch evaluation is needed for actual concurrent proposal
evaluation. We verified these interfaces against the pinned installed framework.

| Profile | Task evaluations per family | Reflection | Feedback | Proposals |
| --- | ---: | --- | --- | ---: |
| checklist | Up to 256 | High reasoning | Finite clinical checks; source-checked roster counts/species and figure ownership; otherwise structural | 1 |
| clinical | Up to 512 | High reasoning | Same gates plus frozen source-grounded model review | 2 independent frontier proposals |

All **27 operational prompt families** receive an independent search when they have
disjoint examples: 14 clinical sections, summary, timeline, roster, figure caption
attribution, pixel descriptions, pixel attribution, joint figure analysis, order
review, clinical inventory, coverage audit, claim audit, coverage backfill and repair.
Live discovery is included in bootstrap so roster examples exist. Up to 24 training
and ten validation examples are selected round-robin by article, with previously
failed attempts prioritized within an article. Minibatches of three are shuffled
across epochs. Families run concurrently in eight worker threads; clinical-profile
proposal evaluations also batch concurrently on the serving GPU.

Budgets count task evaluations, **not** reflection and assessor model calls; those
extra calls are recorded in rollout/attempt traces and limited by component wall
budgets. Components lacking examples are reported as unavailable. A time-limited
completed search reports its stop flag, candidate count and validation gain.

Article-level splits keep all patients, panels and seeds together. Only training
and validation groups reach optimization or reflection. Test labels never select
prompts. Validation ties retain the original empty supplement. The clinical
profile is designated before testing for the combined GEPA arms; the comparison
also runs checklist prompts independently. Prompt supplements cannot alter source,
schema, validators, attribution identity or model sampling contracts.

Source-grounded assessment reads the exact original task input (including pixels)
and proposed answer, then scores fidelity, completeness and patient ownership.
Diagnostics check quantities, specimen, negation, uncertainty, patient/episode
boundaries and relative clinical order. Missing-fact feedback requires literal
source evidence. Scores are explicitly **unadjudicated model proxies**, never
reported as medical accuracy. Forbidden finite checks remain hard zero-reward
gates; missing/failed assessment is zero reward. The assessor and reflection
templates remain frozen evaluation/optimization machinery: evolving the assessor
to increase its own reward would invalidate the experiment.

The 20-source fixture is a development benchmark already inspected in previous
campaigns. The optimizer-held-out split does not make it an untouched external
medical test set. New sources remain unadjudicated until source review. Repeated
seeds quantify generation variability, not independent patient sample size.

## Extraction comparisons

The main 64K/32K-prefill cell runs 16 variants over three seeds: baseline,
legacy-control, focused, clinical-audit, joint-pixels, coverage-backfill,
live-roster, clinical GEPA, checklist GEPA, clinical-only GEPA, auxiliary-only GEPA,
GEPA backfill, GEPA high-reasoning, GEPA live-roster, GEPA joint-pixels and no-repair.
The 128K/32K-prefill confirmation compares clinical-audit, clinical GEPA,
checklist GEPA and GEPA backfill over two seeds. New-source comparison selects up
to 24 articles, uses two seeds and shares each seed's independently generated
roster across four arms. Generated-roster failures remain failures, not reviewed
patient identities.

Clinical source-aware repair is the new suite's default; a legacy control is
retained. Observations keep separate measurement magnitudes, exponents and units.
Timeline tests concern supported relative occurrence, not invented dates or
forced total order. Pixel tests retain descriptions, classification, caption
claims and patient/panel attribution separately. New prompts are experimental;
no automatic production promotion occurs.

## Inspect and transfer

```bash
run_dir=$(ls -dt /blue/cai6734/ehr_agent/op2-gepa-clinical/run-*/ | head -n 1)
run_dir=${run_dir%/}
squeue -u "$USER"
cat "$run_dir/gepa.json" "$run_dir/gpu.json" "$run_dir/source-cleanup.json"
cat "$run_dir/COMPARISON.md"
```

Use `prompt-optimization/{checklist,clinical}/components.json` for per-family
gains, failures and deadline flags, `profiles.json` for both reward regimes, and
per-cell `trials.json`/`schedule-outcome.json` for deferred work. Raw task responses,
source quotes and protected repair decisions remain available for adjudication.

Create an allowlisted archive on HiPerGator:

```bash
uv run --locked --python 3.12 --no-dev op2 corpus-pilot export-results \
  --work-dir "$run_dir" --output "${run_dir}-results.tar.gz"
```

The command prints the archive path. From the Mac, SCP that printed path to
`~/Downloads/` using `wkieffer@hpg.rc.ufl.edu`. The archive includes both GEPA profiles
and clinical review artifacts; model checkpoints and acquisition caches are excluded.
