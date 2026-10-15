# OpenPatients 2 — v0.6.0

**Evidence-grounded clinical extraction from PMC case reports, and a reviewed dense reference set to score it against.**

A uv Python project for turning licensed PMC full articles into evidence-backed patient records: patient discovery,
14 clinical sections, cited summaries, relative timelines, figure inspection and attribution, supplements and
measurement series. Both the local-LLM pipeline and the reviewed reference release emit the same
`openpatients2-unified-article/1` row, so model output can be scored directly against the reference.

Start with the [current pipeline overview](docs/PIPELINE_OVERVIEW.md) (ASCII diagrams of every stage). Update this
living overview whenever the pipeline changes.

## What is here

- `src/openpatients2/` — extraction runtime (`pilot_extract`, article parsing, prompts, validation/repair), the
  optional EHR stages (`ehr_pipeline`, `ehr_tasks`), the shared row builder (`unified_record`), scoring
  (`unified_scoring`, `scoring/`), shared annotation rules (`annotation_rules`, `rules/`) and the article viewer.
- `src/openpatients2/dense/` — the dense reference-set tools, run as `op2 dense <tool>` (`op2 dense --help`):
  acquisition, review/receipt tools, rules normalization, runtime gates and `op2 dense export`, which builds
  `data/dense_clinical_cases_v1/`.
- `benchmarks/dense500/` — the dense reference-set data and docs only (no code): annotation protocol, agent briefs,
  selection records and per-article folders. See its [README](benchmarks/dense500/README.md)
  and [annotation protocol](benchmarks/dense500/ANNOTATION_PROTOCOL.md). `benchmarks/dense50/` holds the first retained
  articles and is exported together with dense500. Everything about one article (source, annotation, schema target,
  figure/supplement reviews, EHR sidecars, receipts) lives in one folder, `benchmarks/<root>/articles/<ID>/`, with
  fixed file names resolved only by `openpatients2.dense_store` (map in the dense500 README).
- `docs/` — [pipeline overview](docs/PIPELINE_OVERVIEW.md), [article viewer](docs/ARTICLE_VIEWER.md),
  [schema review v2.1](docs/SCHEMA_REVIEW_V2_1.md), and the proposed
  [source-claim compiler](docs/SOURCE_CLAIM_COMPILER_DESIGN.md) design.
- [Clinical model benchmark](docs/DENSE_NEMOTRON_SUPER.md) — Fastino Super Healthcare,
  NVIDIA BF16/NVFP4, NVFP4 with LiLiCorr, and Red Hat Nemotron FP8 with its DSpark8
  drafter by default; Muse Spark Open remains opt-in while access is pending,
  using pinned publisher vLLM recipes.

## Quick start

```bash
uv sync
uv run pytest -q
uv run op2 --help
```

### End-to-end local pipeline (extract → EHR stages → score)

Against a local OpenAI-compatible server (loopback `/v1` endpoints only), on an already downloaded articles JSONL
(e.g. from `op2 article-fetch`):

```bash
# 1. baseline extraction -> RUN/ (patients.jsonl, rosters.json, visual-annotations.json, report.json)
uv run op2 extract --articles sample.jsonl --output RUN \
    --endpoints http://127.0.0.1:8000/v1 --context 131072 [--config extract.yaml] [--arm targeted|direct]
# 2. optional EHR stages on that run -> OUT/data/unified.jsonl
uv run op2 ehr-pipeline --config ehr.yaml --articles sample.jsonl --run-dir RUN \
    --output OUT --endpoints http://127.0.0.1:8000/v1 --context 131072
# 3. score against the reference release
uv run python -m openpatients2.unified_scoring --reference data/dense_clinical_cases_v1/data \
    --predicted OUT/data/unified.jsonl --out score.json
```

`extract.yaml` is a `PilotConfig` (`openpatients2.pilot_extract`: call/token/figure limits, sampling seed, optional
review stages); omitted, the defaults apply. `RUN` must be new or empty; results are never overwritten. Details in
the [pipeline overview](docs/PIPELINE_OVERVIEW.md#end-to-end-local-run).

Before a run, note:

- **The server must be vLLM (or expose vLLM's `/tokenize`).** Every call is budgeted from an exact prompt token
  count taken from `POST /tokenize` on the endpoint's host; a server without it (plain OpenAI-compatible APIs,
  hosted routers) fails the call instead of truncating the source. Endpoints must be loopback addresses.
- **The CLI path is text-only for figures.** Without `--image-manifest` (a prepared `vision-assets` manifest), no
  pixels are inspected: figures stay `not_reviewed` in the unified rows (they are *not* listed in
  `unavailable_media`, which is only for figures whose source declares no obtainable asset), and the figure-EHR and
  privacy stages have nothing to label. Captions are still read as text evidence.
- **Score against the articles you actually ran.** `unified_scoring` counts every reference article missing from
  the prediction as a full miss, so scoring a 3-article run against the whole release reports ~1% recall. Build a
  reference subset first:

  ```bash
  uv run python -c "import json,sys; ids={json.loads(l)['article_id'] for l in open('OUT/data/unified.jsonl')}; \
  rows=[l for f in ('train','validation','test') for l in open(f'data/dense_clinical_cases_v1/data/{f}.jsonl') \
  if json.loads(l)['article_id'] in ids]; open('reference-subset.jsonl','w').writelines(rows)"
  uv run python -m openpatients2.unified_scoring --reference reference-subset.jsonl \
      --predicted OUT/data/unified.jsonl --out score.json
  ```

  Check `coverage.missing_articles` in the score: it should be empty.

The runtime checker is stricter than the reference gates on time attribution: a time borrowed from a complete
sentence about something else (NO-BORROWING) and misread `by …` / `within …` bounds (TIME-LITERAL) fail the
section and trigger the repair round; an item still failing only on its time keeps the fact with the time cleared
(`time_quarantined_items` in the task result).

Rebuild the reference release from reviewed annotations:

```bash
op2 dense export
```

### Browse articles and extracted records

```bash
uv run --locked op2 viewer
```

Open [the article library](http://127.0.0.1:8765) to browse all acquired articles, read source text alongside every
clinical schema, and follow evidence quotes. New reviewed records appear automatically every 15 seconds. See
[the viewer guide](docs/ARTICLE_VIEWER.md) for another dataset or a portable static-site build.

## Archived legacy work

On 2026-10-10 the earlier pipelines and experiments were archived out of the working tree: the original
Open-Patients K2/Motif batch pipeline and demo (`op2 demo/run/benchmark/serve/export/index/cohort/evaluate`),
terminology grounding and knowledge graph, reasoning studies, HiPerGator/Glimmer/K2 serving benchmarks, the corpus
pilot, GEPA, patient-bundle, controlled-component, frontier and clinical-architecture campaigns, their configs,
scripts, reports, docs and tests, plus the multi-annotator proposal folders and builder scripts of the dense projects.
They were removed from the working tree (along with untracked local run outputs of those campaigns); every removed
tracked file can be restored from git commit `c66587d` (`git checkout c66587d -- <path>`).

## Safety, licenses and scope

Code is MIT. Dataset/model/terminology/engine licenses remain separate. Article and figure terms are preserved per
source; no blanket training, redistribution or commercial permission is inferred. Do not upload private clinical data
to unauthorized endpoints. Public case text can still contain sensitive information, and this project is not a
de-identification or HIPAA-compliance certification.

The reference annotations are AI reference annotations reviewed against their sources, not physician-adjudicated
clinical gold. Passing field checks or validation is not proof of medical accuracy. This is research code, not a
clinical decision-support system.
