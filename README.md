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
- `benchmarks/dense500/` — the dense reference-set project: acquisition, annotation protocol, review/receipt tools,
  runtime gates and `export.py`, which builds `data/dense_clinical_cases_v1/`. See its [README](benchmarks/dense500/README.md)
  and [annotation protocol](benchmarks/dense500/ANNOTATION_PROTOCOL.md). `benchmarks/dense50/` holds the first retained
  articles and is exported together with dense500. Everything about one article (source, annotation, schema target,
  figure/supplement reviews, EHR sidecars, receipts) lives in one folder, `benchmarks/<root>/articles/<ID>/`, with
  fixed file names resolved only by `benchmarks/dense500/store.py` (map in the dense500 README).
- `docs/` — [pipeline overview](docs/PIPELINE_OVERVIEW.md), [article viewer](docs/ARTICLE_VIEWER.md),
  [schema review v2.1](docs/SCHEMA_REVIEW_V2_1.md), and the proposed
  [source-claim compiler](docs/SOURCE_CLAIM_COMPILER_DESIGN.md) design.

## Quick start

```bash
uv sync
uv run pytest -q
uv run op2 --help
```

Run the optional EHR stages on a finished extraction run and score the result against the reference release:

```bash
uv run op2 ehr-pipeline --config ehr.yaml --articles sample.jsonl --run-dir RUN \
    --output OUT --endpoints http://127.0.0.1:8000/v1 --context 131072
# equivalently: python -m openpatients2.ehr_pipeline ...
python -m openpatients2.unified_scoring --reference data/dense_clinical_cases_v1/data \
    --predicted OUT/data/unified.jsonl --out score.json
```

Rebuild the reference release from reviewed annotations:

```bash
PYTHONPATH=src:benchmarks/dense500 .venv/bin/python benchmarks/dense500/export.py
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
Everything is in `~/openpatients2-archive-2026-10-10/` (see `ARCHIVE_INDEX.md` there), and every archived tracked file
can be restored from git commit `c66587d`.

## Safety, licenses and scope

Code is MIT. Dataset/model/terminology/engine licenses remain separate. Article and figure terms are preserved per
source; no blanket training, redistribution or commercial permission is inferred. Do not upload private clinical data
to unauthorized endpoints. Public case text can still contain sensitive information, and this project is not a
de-identification or HIPAA-compliance certification.

The reference annotations are AI reference annotations reviewed against their sources, not physician-adjudicated
clinical gold. Passing field checks or validation is not proof of medical accuracy. This is research code, not a
clinical decision-support system.
