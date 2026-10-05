# Patient-bundle GEPA and clinical-fidelity campaign

From the updated HiPerGator checkout, launch the whole chain:

```bash
cd /blue/cai6734/ehr_agent/openpatients2
bash scripts/run_bundle_overnight.sh
```

An optional argument chooses a fresh work directory. By default results go under
`/blue/cai6734/ehr_agent/op2-bundle-overnight/run-YYYYMMDD-HHMMSS`. Submission returns
immediately. The login shell can close. Keep the checkout unchanged until the
jobs end because its source/configuration hashes are pinned.

## Changes motivated by the last run

The [October 5 review](../reports/GEPA_CLINICAL_20261005.md) found that independent
prompt supplements did not beat the strongest baseline on strict delivered
fields. Many optimization rollouts had zero repairs, whereas evaluation used
two. Clinical-feedback scores included an unadjudicated Glimmer judge. Important
treatments were present in typed sections but missing from timelines; a known
medication route disappeared. A successful GEPA search was not evidence of a
better patient bundle.

This experiment replaces task strategies within explicit boundaries. Clinical
task text is mutable; its original source prefix, patient registry, field guide
and schema remain intact. Text auxiliaries replace the strategy between source
JSON and schema. Vision/ownership strategies replace their system strategy with
a fixed source contract plus the evolved instructions. Repair candidate/error
payloads, accepted-value protection and validators remain fixed. This optimizes
29 operational **components**, not every string in the repository or the
non-invoked ontology/citation tools. The immutable safety/source contract is not
a tunable reward loophole.

The standalone family searches retain bootstrap inputs as a warm start, but now
use two repairs, the primary 64K context and rewrite mode. They use independent
source labels, without a validity bonus or model-judge reward. A family needs
three labelled validation articles; underlabelled families remain explicit and
keep their original strategy. This requirement is intentionally stricter than
the previous one-example validation. It does not invent gold for coverage or
claim-audit judgments.

The subsequent **joint program search** includes all 29 components in its
candidate map. It regenerates live patient discovery, clinical sections,
coverage checks/backfill, summaries, completed timelines and media attribution
for each evaluated article batch. No predicted intermediate or gold roster is
frozen. A wall-limited search may not change every component; the report records
candidate changes, proposed groups and actual model invocations separately for
each one. Related components rotate globally in seven groups; selecting another
Pareto parent cannot reset that rotation. Reflection sees the actual invoked
strategy text, rather than just an empty placeholder for original instructions.
A component with no successful
mutation is not advertised as optimized. The full-program objective provides
downstream feedback for audit/repair strategies that lack direct labels.

This uses the actual pinned GEPA 0.1.4 adapter API, Pareto candidate selection,
rotating component-group updates, source-specific feedback and bounded candidate
merges. The design follows the official [adapter guide](https://gepa-ai.github.io/gepa/guides/adapters/)
and [full-program example](https://gepa-ai.github.io/gepa/tutorials/dspy_full_program_evolution/).
The family searches batch [parallel proposals](https://gepa-ai.github.io/gepa/guides/parallel-proposals/)
and independent searches on one GPU. Prompt optimization can still fail to
generalize; it cannot create reliable clinical labels by itself.

## Expanded, bounded benchmark

| Source material | Size / role |
| --- | --- |
| Existing corpus-correctness articles | 20 canonical sources, including negatives |
| Earlier K2/hosted-model full-text fixtures | 8 sources; one abstract-only source excluded |
| Previously inspected new-source cases | 3 articles / 4 patients: infant, two aneurysm cases, thyroid case |
| Total | 31 articles, 30 patient records |
| Clinical probes | 261 required field assertions, 44 known forbidden assertions, covering all 14 clinical families |
| Course probes | 53 occurred-event nodes and 41 important before relationships, across 12 patient courses |
| Summary probes | 53 source-supported course concepts |
| Figure attribution | 10 source-bound figure/panel ownership probes |
| Limited pixel gold | 2 native hash-bound inventories: infant photographs and eight-panel thyroid scan figure |

The benchmark occupies about 1.5 MB locally. No new article collection or model
weights were downloaded locally. The three additional sources are canonical
packets from the supplied results archive. Clinical gold was reviewed against
their actual source text; the limited native pixel inventories reuse the
documented October 5 inspection. This is not physician adjudication or exhaustive
precision/recall. Free visual descriptions, diagnostic interpretations and all
unlabelled claims remain review tasks. Pixel inventory tests establish panel
labels, image kind and chart presence only; changed image hashes are unavailable,
not silently scored against the wrong image.

Labels include quantities/units, treatment doses/routes, negation, planned versus
performed care, relatives versus patient ownership, multiple patients and animal
patients. Newly labelled chronology covers tracheostomy before surgery, repeated
radioiodine treatments, treatment failure/progression, poisoning courses and
postoperative follow-up. The label compiler also corrected `C0018`: oncology
uses `given/ongoing`, rather than medication/procedure action enums. The old
reference files remain unchanged; comparisons to old runs need the new rubric.

Preflight verifies source text/XML hashes, literal gold evidence, actual schema
fields/enums, roster evidence and non-overlapping article splits. Article groups
are stratified into 15 train, 7 validation and 9 test sources. All patients,
panels and seeds of one article stay together. The optimizer receives only
train/validation sources and labels. These sources had earlier development
exposure; the test is withheld from this optimization, not a newly blinded
external cohort. Undetected patients shared across publications remain a
limitation; citation alone does not establish identity.

## What receives reward

The joint score uses source-bound delivered clinical fields (60%), identity and
proper negative discovery (15%), course nodes (8%), course relationships (8%),
summary concepts (4%), figure ownership (3%) and limited pixel inventory (2%).
Unavailable categories are omitted only when the **reference has no probes**;
missing model output receives no credit. The score is normalized over the
categories labelled for that article and macro-averaged by article for GEPA.
Known forbidden facts, reversed course relationships and unresolved extra
identities incur penalties. Individual category counts are always exported.

Patient IDs are aligned using reviewed identity quotations and species, never
by choosing whichever clinical answers match best. Duplicates and ambiguous
identity remain unaligned. An absent/failed roster is not a correct negative
case. Attached evidence cannot satisfy a clinical field or summary concept.

Course matching requires occurred/planned distinction and source segment
support. A single broad event cannot satisfy multiple distinct encounters.
Relations use transitive before/after reachability; neither event-list order nor
paragraph order earns credit. Cycles cannot earn a correct before relationship.
Disconnected events remain unordered. No exact calendar dates are required.

After search, the best validation candidate is evaluated again against the
original strategies. It must improve the bundle score **and** retain at least
as many clinical check matches with no increase in forbidden hits. Otherwise
the original strategies are retained, with a receipt explaining that outcome.
Making a forbidden check unscorable also fails this gate; missing fields cannot
hide a known forbidden fact.
The independent candidate set is still tested separately. Test scores never
select prompts. No optimized prompt automatically changes production defaults.

## Pipeline and GPU experiments

The optional final completion passes rebuild the timeline and summary after
coverage backfill. Timeline completion preserves existing supported fact links;
failed completion retains the earlier graph. Typed medication route recovery
only copies a route directly attached to that named drug in its own exact
evidence. Conflicting routes and routes of neighboring drugs stay unresolved.
Original candidates and repair audits are retained.

Independent coverage/claim batches now run concurrently. The new campaign
spreads calls across all serving replicas, preserving patient identity in each
source packet. This keeps small GEPA article batches from using only one or two
of eight endpoints. Dependent stages still await their actual inputs. This
dispatch change is an experiment, not a measured speed gain yet.

| Main arms, three seeds each | Purpose |
| --- | --- |
| baseline | Original strategies and repaired delivery; no added audit/completion passes |
| clinical-audit | Source inventory, coverage and claim audits, order review |
| complete | Protected backfill plus final timeline/summary completion, reviewed roster |
| joint-pixels | Joint description/ownership versus staged pixels, reviewed roster |
| live-complete | Full live-discovery control for optimized programs |
| gepa-independent | Independent rewritten strategies used together, live discovery |
| gepa | Jointly optimized complete patient program, live discovery |
| gepa-clinical-only / gepa-aux-only | Independent clinical versus auxiliary prompt contributions |
| gepa-joint-pixels | Joint program with joint pixel description/ownership |
| gepa-high | High reasoning sensitivity of the selected program |
| whole-complete | Full article clinical inputs versus selected patient sections |

The engine stays **RedHatAI/Muse-Glimmer-30B-FP8-block** with the pinned Meta
template and DFlash drafter, vLLM 0.30.0, eight TP1 replicas on one B200 node.
No BF16 checkpoint is used. Sampling is medium reasoning, temperature 1,
top-p .95, top-k 64 and 16K initial/retry output caps, except the named high
reasoning arm. Output budget checks never silently truncate source. Confirmation
cells compare 128K context, 8K scheduler prefill budget, and DFlash off against
the 64K/32K-prefill control. **Prefill budget is not context length.**

CPU preparation/profiling, dependencies, checkpoint/container download and
cleanup request no GPU. Bootstrap and complete-program comparison use one node,
32 CPUs, 250 GB RAM and eight B200s under account/QOS `cai5724`. Independent
family search requests one B200, 16 CPUs and 96 GB RAM. This preserves the proven
allocation pattern from the last run. GEPA dependencies are already pinned in
`uv.lock`; CPU jobs install the `optimize` extra into the campaign's uv venv.

Family search is bounded to 90 minutes and joint search to 90 minutes. The main
GPU allocation has a 9.5-hour work budget inside its 12-hour Slurm limit,
including server initialization and layout changes. Expect roughly **8–12 active
hours** across stages based on the earlier run, with substantial uncertainty for
the larger workload and live discovery. Queue waits are additional. Lower-priority
confirmation cells/trials may be explicitly deferred; the GPU receipt becomes
partial when required work is deferred. No artificial idle time is added to
make the run last overnight.

## Inspect, finish and retrieve

```bash
run_dir=$(ls -dt /blue/cai6734/ehr_agent/op2-bundle-overnight/run-*/ | head -n 1)
run_dir=${run_dir%/}
uv run --locked --python 3.12 --no-dev op2 corpus-pilot status --work-dir "$run_dir"
cat "$run_dir/progress.json"
squeue --start -u "$USER" -o '%.18i %.30j %.19S %.12T %R'
```

After completion inspect `SUMMARY.md`, `BUNDLE_COMPARISON.md`, `gpu.json`,
`gepa.json` and `prompt-optimization/joint-program/report.json`. Stage completion
is distinct from prompt improvement. Inspect failures/deferred work, the selected
original flag, independent family coverage and validation confirmation facts.
Per-trial `bundle-quality.json` and `bundle-test.json` include clinical,
identity, course and pixel diagnostics. Candidate traces and per-patient source
review forms remain available. Input/output token distributions and telemetry
remain separate from clinical scores. Pooled tok/s uses summed output usage /
summed trial wall time; missing usage makes it unavailable.

```bash
uv run --locked --python 3.12 --no-dev op2 corpus-pilot export-results \
  --work-dir "$run_dir" --output "${run_dir}-results.tar.gz"
```

Run SCP on the Mac, substituting the actual timestamp printed above:

```bash
scp wkieffer@hpg.rc.ufl.edu:/blue/cai6734/ehr_agent/op2-bundle-overnight/run-TIMESTAMP-results.tar.gz ~/Downloads/
```

The allowlisted archive excludes checkpoint/container/cache files. CPU cleanup
deletes active model weights and working article exports after inference.
Results retain the small curated benchmark and labels, source review packets,
selected pixels, scalar lengths/histograms, logs and optimizer traces so the
analysis can be reproduced after cleanup. Cleanup waits for all GPU consumers.
This campaign acquires no large new corpus; its only source-network requests are
the bounded selected figures and tokenizer metadata.
