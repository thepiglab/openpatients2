# Isolate the overnight repairs

The October 12 comparison completed 217 pairs in 4h55m. Figures and measurement
series improved; clinical validation F1 stayed flat and summaries worsened.
This follow-up changes one component per arm. It does not run GEPA yet.

| Arm | Change from control |
|---|---|
| control | Existing pipeline, all experimental switches off |
| figures | Exact same-article figure-ID prefix normalization; skip attribution without a valid panel inventory |
| series | Exclusive segment/cell ownership instructions and bounded series retries; other EHR tasks retain direct behavior |
| roster | Table/footnote and eligible individual-case discovery guidance |
| summary_json | Native vLLM JSON-schema-constrained output for `summary` only; existing summary prompt and semantic/evidence checks |

The last run's summary formatting instructions and raw malformed-output excerpt
are off in every arm. No grammar fallback is allowed: unsupported structured
requests are saved as failures. Hardware compatibility with this constrained
summary mode still requires this campaign's first B200 run. JSON validity does
not establish evidence fidelity; ordinary validators and repair protections
remain active. The legacy `overnight_repairs` switch retains the original bundled
behavior for reproduction.

## Sample and interpretation

All 59 validation articles plus 24 training diagnostics, 83 per arm, 415 article
runs. Training includes PMC12861484.1 (series regression), PMC12480731.1 and
PMC12817846.1 (series recoveries), then fills the remaining slots using stable
SHA256 ranking of training article IDs. Selection does not inspect reference
facts. Validation membership stays unchanged; all 43 test articles are excluded
before preparing source inputs. Training diagnostics are enriched, and validation
was already examined, so this is a development comparison, not a blinded test.

The report compares each component against control on its own primary validation
metric: figure-panel F1, measurement-series F1, patient recall or summary-claim F1.
A component is marked promising only with at least 0.5 percentage points of target
gain, at most 0.5 points of clinical F1 loss, at most one point each of precision
and matched-attribute loss, and at most one point of patient-recall loss (roster's
primary target must improve). All arms must finish. These are engineering gates,
not statistical confidence claims. No combination is automatically promoted.

Per-article JSON reports expose concentrated gains and regressions; unavailable
scores remain explicit. Task failures and HTTP/provider errors remain available
in each arm's raw attempts. Full reference denominators include missing outputs;
completed-pair scores are supplementary diagnostics only. Keep the test split
unused until a combined configuration is frozen. Next, run GEPA on training
examples for clinical coverage with precision constraints.

## Resources

Same pinned Glimmer FP8/DFlash setup as the completed benchmark: four independent
TP1 B200 workers, medium reasoning, 64K context, 16K output, 32K prefill, c64 text
and bounded vision/EHR calls. One server startup per worker. A shared atomic
queue assigns whole article groups, largest sources/images first; arm order uses
article-hash rotation/reversal so control is not always the cold-cache arm.
Workers leave rather than wait at a barrier.

CPU jobs perform preparation, downloads, environment setup, scoring, comparison,
and archive compression. The eight-hour GPU allocation includes model startup
and all inference/repair work; queue and CPU stages add elapsed time. The prior
415-vs-434 workload size suggests about five hours, but larger diagnostic cases
and constrained decoding may change that. A 15-minute article allowance and
10-minute shutdown reserve bound work. Explicit resume gets a new allocation
budget and skips completed results. No automatic extension or reroll of validly
completed model failures.

## Commands

After you commit/push these files and pull into the existing HiPerGator checkout:

```bash
bash scripts/run_dense_ablation.sh
bash scripts/run_dense_ablation.sh status
squeue -u "$USER"
```

After aggregation/export:

```bash
bash scripts/run_dense_ablation.sh verify
WORK=$(cat runs/dense-ablation-last-work.txt)
cat "$WORK/summary/comparison.md"
```

Key outputs: `summary/comparison.{md,json}`, `per-article-ARM.json`,
`article-timing.jsonl`, `completed-pairs-ARM.json`, `arms/ARM/summary/`, and shared
worker telemetry in `results/workers/`. The `completed_pairs` field in the root
report counts articles completed by all five arms. Per-component pair counts are
reported separately. The old overnight launcher still runs the two-arm plan.

After all previous jobs are terminal, resume only if needed:

```bash
python3 "$WORK/code/scripts/benchmark_dense_overnight.py" submit --work "$WORK" --resume
```

Download on your Mac, using the campaign path printed at launch:

```bash
HPG=wkieffer@hpg.rc.ufl.edu
REMOTE=/blue/cai6734/ehr_agent/op2-dense-ablation/run-TIMESTAMP
DEST="$HOME/Downloads/$(basename "$REMOTE")"
mkdir -p "$DEST"
scp "$HPG:$REMOTE/results.tar.gz" "$HPG:$REMOTE/results.tar.gz.sha256" "$DEST/"
cd "$DEST"
shasum -a 256 -c results.tar.gz.sha256 && tar -xzf results.tar.gz
python3 "$(basename "$REMOTE")/code/scripts/benchmark_dense_overnight.py" verify --work "$DEST/$(basename "$REMOTE")"
```

Do not alter prepared snapshots or annotated articles. The new code also corrects
roster audit filenames to use the actual prepared prompt, avoiding duplicate
saved task files. This changes bookkeeping, not inference or score matching.
