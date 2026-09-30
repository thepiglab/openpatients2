from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import signal
import sys
from pathlib import Path

import yaml

from .config import load_config
from .data import read_jsonl, records, write_json


def emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


async def launch_run(args):
    from .serving import ServingConfig, ServerGroup
    from .pipeline import run_pipeline, smoke
    from .benchmark import benchmark
    cfg = load_config(args.config)
    serving = ServingConfig.load(args.serving)
    group = ServerGroup(serving, ".", str(Path(cfg.output) / "server-logs"))
    try:
        snapshot = group.preflight()
        if cfg.api.identity()["model_id"] != snapshot["model_id"] or cfg.api.revision != snapshot["revision"]:
            raise ValueError("Pipeline identity and local model snapshot differ")
        if cfg.max_model_len != serving.max_model_len or cfg.api.model != serving.served_model_name:
            raise ValueError("Client context length / served alias must match server profile")
        commands = await group.start()
        cfg.api.endpoints = [c["endpoint"] for c in commands]
        cfg.api.metrics_urls = [c["endpoint"].removesuffix("/v1") + "/metrics" for c in commands]
        if args.benchmark:
            return await benchmark(cfg, args.limit or 128)
        checked = await smoke(cfg, next(records(cfg.input)), all_tasks=True)
        write_json(Path(cfg.output) / "preproduction-smoke.json", checked)
        if not checked["passed"]:
            raise RuntimeError("Preproduction schema smoke failed")
        return await run_pipeline(cfg, args.limit)
    finally:
        group.stop()


async def serve_forever(args):
    from .serving import ServingConfig, ServerGroup
    group = ServerGroup(ServingConfig.load(args.config), ".", args.logs)
    try:
        commands = await group.start()
        emit({"ready": [x["endpoint"] for x in commands]})
        while all(p.poll() is None for p in group.processes):
            await asyncio.sleep(2)
        raise RuntimeError("Serving rank exited")
    finally:
        group.stop()


def parser():
    p = argparse.ArgumentParser(prog="op2", description="Evidence-grounded OpenPatients extraction and cohort research")
    sub = p.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("article-search", help="Discover license-filtered PMC candidates; articles need a second license check")
    cmd.add_argument("--query", required=True)
    cmd.add_argument("--limit", type=int, default=100)
    cmd.add_argument("--offset", type=int, default=0)
    cmd.add_argument("--output", required=True)
    cmd = sub.add_parser("article-fetch", help="Acquire bounded JATS packets from current PMC Cloud metadata")
    cmd.add_argument("--input", required=True, help="article-search JSON or JSON list of PMCIDs")
    cmd.add_argument("--output", required=True)
    cmd.add_argument("--limit", type=int, default=100)
    cmd.add_argument("--max-bytes", type=int, default=100_000_000)
    cmd.add_argument("--version", type=int)
    cmd.add_argument("--source-view", action="append", choices=["pmc_text", "pdf_firecrawl"], default=[],
                     help="Optional clinical reading surface; JATS remains canonical (repeatable)")
    cmd.add_argument("--max-asset-bytes", type=int, default=12_000_000)
    cmd = sub.add_parser("article-lengths")
    cmd.add_argument("--input", required=True)
    cmd.add_argument("--output", required=True)
    cmd = sub.add_parser("article-experiment")
    cmd.add_argument("--config", required=True)
    cmd = sub.add_parser("article-vision")
    cmd.add_argument("--config", required=True)
    cmd = sub.add_parser("citation-plan", help="Build bounded one-hop reference plans; never merge patient identities")
    cmd.add_argument("--input", required=True)
    cmd.add_argument("--output", required=True)
    cmd.add_argument("--max-targets", type=int, default=20)
    cmd = sub.add_parser("case-link-graph", help="Inspect adjudicated identity edges without rewriting source cases")
    cmd.add_argument("--input", required=True)
    cmd.add_argument("--output", required=True)
    cmd = sub.add_parser("ehr-seeds")
    cmd.add_argument("--input", required=True)
    cmd.add_argument("--output", required=True)
    cmd.add_argument("--visuals")
    cmd.add_argument("--visual-model", help="Explicit vision model to combine with the text extractor; defaults to the text model")
    cmd.add_argument("--include-partial", action="store_true")
    cmd = sub.add_parser("vocab-download", help="Plan or explicitly execute bounded, versioned ontology acquisition")
    cmd.add_argument("--config", required=True)
    cmd.add_argument("--execute", action="store_true")
    for name in ["run", "benchmark", "smoke"]:
        cmd = sub.add_parser(name)
        cmd.add_argument("--config", required=True)
        cmd.add_argument("--limit", type=int, default=None)
        if name == "benchmark":
            cmd.add_argument("--no-warmup", action="store_true", help="Explicit cold-start measurement; reports include grammar compilation")
    cmd = sub.add_parser("launch-run")
    cmd.add_argument("--config", required=True)
    cmd.add_argument("--serving", required=True)
    cmd.add_argument("--limit", type=int)
    cmd.add_argument("--benchmark", action="store_true")
    cmd = sub.add_parser("campaign")
    cmd.add_argument("--config", required=True)
    cmd = sub.add_parser("reasoning-prepare")
    cmd.add_argument("--config", required=True)
    cmd.add_argument("--tokenizer", help="Local CPU tokenizer; required for production per-level token profiles")
    cmd.add_argument("--workers", type=int, default=8)
    cmd = sub.add_parser("reasoning-run")
    cmd.add_argument("--study", required=True)
    cmd.add_argument("--serving", help="Optional single fixed serving topology for the entire study")
    cmd = sub.add_parser("reasoning-analyze")
    cmd.add_argument("--study", required=True)
    cmd.add_argument("--allow-partial", action="store_true")
    cmd.add_argument("--gold", help="Optional reviewed labels added after preparation; provenance is marked explicitly")
    cmd = sub.add_parser("review-template")
    cmd.add_argument("--run", required=True)
    cmd.add_argument("--output", required=True)
    cmd = sub.add_parser("distill")
    cmd.add_argument("--run", required=True)
    cmd.add_argument("--output", required=True)
    cmd.add_argument("--reviews")
    cmd.add_argument("--include-reasoning", action="store_true")
    cmd.add_argument("--allow-unreviewed-candidates", action="store_true")
    cmd = sub.add_parser("reasoning-demo")
    cmd.add_argument("--output", default="runs/reasoning-demo")
    cmd = sub.add_parser("enrich-sources", help="CPU-only: preserve provenance and discover figure URLs without image downloads")
    cmd.add_argument("--input", required=True)
    cmd.add_argument("--output", required=True)
    cmd.add_argument("--config", default=None, help="Literature discovery YAML, e.g. configs/sources/pmc.yaml")
    cmd.add_argument("--offline", action="store_true")
    cmd.add_argument("--refresh", action="store_true")
    cmd.add_argument("--limit", type=int)
    cmd = sub.add_parser("sources-demo", help="Offline, mocked provenance/figure discovery demonstration")
    cmd.add_argument("--output", default="runs/sources-demo")
    cmd = sub.add_parser("prepare")
    cmd.add_argument("--input", required=True)
    cmd.add_argument("--output", required=True)
    cmd.add_argument("--revision", default="local-unversioned")
    cmd.add_argument("--dataset-id", default="local")
    cmd = sub.add_parser("download-dataset")
    cmd.add_argument("--output", default="data/prepared.jsonl")
    cmd.add_argument("--revision", default="main")
    cmd = sub.add_parser("download-model")
    cmd.add_argument("--repo", required=True)
    cmd.add_argument("--output", required=True)
    cmd.add_argument("--revision", default="main")
    cmd = sub.add_parser("pin-config")
    cmd.add_argument("--config", required=True)
    cmd.add_argument("--snapshot", required=True, help="models/.../op2_snapshot.json")
    cmd.add_argument("--output", required=True)
    cmd = sub.add_parser("profile")
    cmd.add_argument("--config", required=True)
    cmd.add_argument("--tokenizer", required=True, help="Local downloaded model/tokenizer directory")
    cmd.add_argument("--output", required=True)
    cmd.add_argument("--workers", type=int, default=8)
    cmd = sub.add_parser("sample")
    cmd.add_argument("--input", required=True)
    cmd.add_argument("--output", required=True)
    cmd.add_argument("--size", type=int, default=1000)
    cmd.add_argument("--seed", type=int, default=42)
    cmd = sub.add_parser("schema")
    cmd.add_argument("--output", default="schemas")
    cmd = sub.add_parser("serve")
    cmd.add_argument("--config", required=True)
    cmd.add_argument("--execute", action="store_true", help="Default is dry-run; execute only inside a GPU allocation")
    cmd.add_argument("--logs", default="runs/server-logs")
    cmd = sub.add_parser("export")
    cmd.add_argument("--run", required=True)
    cmd.add_argument("--run-id")
    cmd.add_argument("--output")
    cmd = sub.add_parser("index")
    cmd.add_argument("--input", required=True)
    cmd.add_argument("--output", required=True)
    cmd.add_argument("--terms")
    cmd.add_argument("--catalog")
    cmd = sub.add_parser("cohort")
    cmd.add_argument("--database", required=True)
    cmd.add_argument("--config", required=True)
    cmd.add_argument("--output", required=True)
    cmd = sub.add_parser("evaluate")
    cmd.add_argument("--predictions", required=True)
    cmd.add_argument("--gold", required=True)
    cmd.add_argument("--output", required=True)
    cmd.add_argument("--bootstrap", type=int, default=1000)
    cmd = sub.add_parser("validate")
    cmd.add_argument("--input", required=True)
    cmd.add_argument("--output", required=True)
    cmd = sub.add_parser("demo")
    cmd.add_argument("--output", default="runs/demo")
    c=sub.add_parser('attach-article-map')
    c.add_argument('--input',required=True);c.add_argument('--mapping',required=True);c.add_argument('--output',required=True)
    for name in ('vocab-build','vocab-search','code-link','code-prepare','code-profile','code-infer',
                 'code-review-template','code-apply','graph','ontology-demo'):
        c=sub.add_parser(name)
        if name=='vocab-build':
            c.add_argument('--config',required=True);c.add_argument('--output',required=True)
        elif name=='vocab-search':
            c.add_argument('--catalog',required=True);c.add_argument('--system',required=True)
            c.add_argument('--text',required=True);c.add_argument('--limit',type=int,default=12)
        elif name in {'code-link','code-apply','graph'}:
            c.add_argument('--input',required=True);c.add_argument('--output',required=True);c.add_argument('--catalog',required=True)
            if name=='code-link':c.add_argument('--config')
            if name=='code-apply':c.add_argument('--proposals');c.add_argument('--reviews')
        elif name in {'code-prepare','code-review-template'}:
            c.add_argument('--input',required=True);c.add_argument('--output',required=True)
        elif name in {'code-profile','code-infer'}:
            c.add_argument('--config',required=True)
            if name=='code-profile':
                c.add_argument('--tokenizer',required=True);c.add_argument('--output',required=True)
                c.add_argument('--workers',type=int,default=8)
        else:c.add_argument('--output',default='runs/ontology-demo')
    return p


def _main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command in {"run", "benchmark", "smoke"}:
            from .pipeline import run_pipeline, smoke
            from .benchmark import benchmark
            cfg = load_config(args.config)
            if args.command == "run":
                result = asyncio.run(run_pipeline(cfg, args.limit))
            elif args.command == "benchmark":
                result = asyncio.run(benchmark(cfg, args.limit or 128, warmup=not args.no_warmup))
            else:
                result = asyncio.run(smoke(cfg, next(records(cfg.input))))
                emit(result)
                return 0 if result["passed"] else 2
        elif args.command == "article-search":
            from .corpus import search_candidates
            result = asyncio.run(search_candidates(args.query, args.limit, args.offset))
            write_json(args.output, result)
        elif args.command == "article-fetch":
            from .corpus import acquire
            ids = json.loads(Path(args.input).read_text())
            result = asyncio.run(acquire(ids['pmcids'] if isinstance(ids, dict) else ids,
                args.output, args.limit, args.max_bytes, args.version,
                source_views=tuple(args.source_view), max_asset_bytes=args.max_asset_bytes))
        elif args.command == "article-lengths":
            from .corpus import length_report
            result = length_report(args.input)
            write_json(args.output, result)
        elif args.command == "article-experiment":
            from .experiment import run_experiment
            result = asyncio.run(run_experiment(args.config))
        elif args.command == "article-vision":
            from .vision import run_vision
            result = asyncio.run(run_vision(args.config))
        elif args.command == "citation-plan":
            from .case_links import citation_candidates
            output = Path(args.output)
            if output.exists():raise ValueError('Use a new citation plan output')
            output.parent.mkdir(parents=True,exist_ok=True)
            count = 0
            with output.open('x') as handle:
                for article in read_jsonl(args.input):
                    if 'references' not in article:raise ValueError('Reparse JATS with parser 1.2+ to retain citation links')
                    handle.write(json.dumps(citation_candidates(article,max_targets=args.max_targets),ensure_ascii=False)+'\n')
                    count += 1
            result = {'articles':count,'output':str(output),'identities_merged':0,'network_requests':0}
        elif args.command == "case-link-graph":
            from .case_links import identity_components
            if Path(args.output).exists():raise ValueError('Use a new graph output')
            result = identity_components(list(read_jsonl(args.input)))
            write_json(args.output,result)
        elif args.command == "ehr-seeds":
            from .ehr_seeds import export_seeds
            result = export_seeds(args.input,args.output,args.visuals,args.include_partial,args.visual_model)
        elif args.command == "vocab-download":
            from .vocab_download import download_vocabularies
            result = download_vocabularies(args.config, args.execute)
        elif args.command == "launch-run":
            result = asyncio.run(launch_run(args))
        elif args.command == "campaign":
            from .benchmark import campaign
            result = asyncio.run(campaign(args.config))
        elif args.command == "reasoning-prepare":
            from .reasoning_study import prepare_study
            result = prepare_study(args.config, args.tokenizer, args.workers)
        elif args.command == "reasoning-run":
            from .reasoning_study import run_study
            result = asyncio.run(run_study(args.study, args.serving))
        elif args.command == "reasoning-analyze":
            from .reasoning_study import analyze_study
            result = analyze_study(args.study, args.allow_partial, args.gold)
        elif args.command == "review-template":
            from .distill import review_template
            result = review_template(args.run, args.output)
        elif args.command == "distill":
            from .distill import export_distillation
            result = export_distillation(args.run, args.output, args.reviews,
                                          args.include_reasoning, args.allow_unreviewed_candidates)
        elif args.command == "reasoning-demo":
            from .reasoning_demo import run_reasoning_demo
            result = asyncio.run(run_reasoning_demo(args.output))
        elif args.command == "enrich-sources":
            from .literature_client import LiteratureConfig
            from .source_enrichment import enrich_sources
            cfg = LiteratureConfig.load(args.config)
            cfg.offline = cfg.offline or args.offline
            cfg.refresh = cfg.refresh or args.refresh
            result = asyncio.run(enrich_sources(args.input, args.output, cfg, args.limit))
        elif args.command == "sources-demo":
            from .sources_demo import run_sources_demo
            result = asyncio.run(run_sources_demo(args.output))
        elif args.command == "prepare":
            from .data import prepare
            result = prepare(args.input, args.output, args.revision, args.dataset_id)
        elif args.command == "download-dataset":
            from .data import download_dataset
            result = download_dataset(args.output, args.revision)
        elif args.command == "download-model":
            from .data import download_model
            result = download_model(args.repo, args.output, args.revision)
        elif args.command == "pin-config":
            cfg = load_config(args.config)
            snapshot = json.loads(Path(args.snapshot).read_text())
            cfg.api.model_id, cfg.api.revision = snapshot["model_id"], snapshot["revision"]
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(yaml.safe_dump(cfg.model_dump(), sort_keys=False))
            result = {"config": args.output, "identity": cfg.api.identity()}
        elif args.command == "profile":
            from .profile import create_profile
            result = create_profile(load_config(args.config), args.tokenizer, args.output, args.workers)
        elif args.command == "sample":
            from .data import sample_records
            if args.size < 1:
                raise ValueError("sample size must be positive")
            rows = sample_records(records(args.input), args.size, args.seed)
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
            result = {"sample_records": len(rows), "seed": args.seed, "scope": "corpus sample, not clinical-population representative"}
        elif args.command == "schema":
            from .schemas import TASK_MODELS, wire_schema
            for task, model in TASK_MODELS.items():
                write_json(Path(args.output) / f"{task}.schema.json", model.model_json_schema())
                write_json(Path(args.output) / "wire" / f"{task}.json", wire_schema(task))
            result = {"schemas": len(TASK_MODELS), "directory": args.output}
        elif args.command == "serve":
            from .serving import ServingConfig, render
            if args.execute:
                asyncio.run(serve_forever(args))
                return 0
            cfg = ServingConfig.load(args.config)
            for command in render(cfg):
                print(shlex.join(command["argv"]))
            return 0
        elif args.command == "export":
            from .exports import export_run
            result = export_run(args.run, args.run_id, args.output)
        elif args.command == "index":
            from .warehouse import build_index
            result = build_index(args.input, args.output, args.terms, args.catalog)
        elif args.command == "cohort":
            from .warehouse import query_cohort
            result = query_cohort(args.database, yaml.safe_load(Path(args.config).read_text()), args.output)
        elif args.command == "evaluate":
            from .evaluate import evaluate
            result = evaluate(args.predictions, args.gold, args.bootstrap)
            write_json(args.output, result)
        elif args.command == "attach-article-map":
            from .article_map import attach_article_map
            result=attach_article_map(args.input,args.mapping,args.output)
        elif args.command == "vocab-build":
            from .vocabulary import build_catalog
            result=build_catalog(args.config,args.output)
        elif args.command == "vocab-search":
            from .vocabulary import Catalog
            with Catalog(args.catalog) as cat:
                result={"candidates":cat.search(args.system,args.text,args.limit),"catalog_fingerprint":cat.fingerprint}
        elif args.command == "code-link":
            from .coding import link_file
            result=link_file(args.input,args.output,args.catalog,args.config)
        elif args.command == "code-prepare":
            from .coding import prepare_requests
            result=prepare_requests(args.input,args.output)
        elif args.command == "code-review-template":
            from .coding import review_template
            result=review_template(args.input,args.output)
        elif args.command == "code-apply":
            from .coding import apply_decisions
            result=apply_decisions(args.input,args.output,args.catalog,args.proposals,args.reviews)
        elif args.command in {"code-profile","code-infer"}:
            from .coding_api import CodingRunConfig,profile_requests,infer_requests
            cfg=CodingRunConfig.load(args.config)
            result=profile_requests(cfg,args.tokenizer,args.output,args.workers) if args.command=="code-profile" else asyncio.run(infer_requests(cfg))
        elif args.command == "graph":
            from .knowledge_graph import export_graph
            result=export_graph(args.input,args.catalog,args.output)
        elif args.command == "ontology-demo":
            from .ontology_demo import run_ontology_demo
            result=asyncio.run(run_ontology_demo(args.output))
        elif args.command == "validate":
            from .validation import validate
            failures, total = [], 0
            for row in read_jsonl(args.input):
                for task in row["expected_tasks"]:
                    total += 1
                    section = row["sections"].get(task)
                    checked = validate(task, section, row["source"]["text"])
                    if not checked.valid:
                        failures.append({"record_id": row["source"]["record_id"], "task": task, "errors": checked.errors})
            result = {"checked_sections": total, "failed_sections": len(failures), "failures": failures}
            write_json(args.output, result)
            emit(result)
            return 2 if failures else 0
        else:
            from .demo import run_demo
            result = asyncio.run(run_demo(args.output))
        emit(result)
        return 2 if isinstance(result, dict) and result.get("failed_records", 0) > 0 else 0
    except KeyboardInterrupt:
        print("Interrupted; committed task state remains resumable.", file=sys.stderr)
        return 130
    except (ValueError, RuntimeError, FileNotFoundError, StopIteration, TimeoutError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


def _handle_termination(signum, frame):
    # Slurm's advance SIGTERM must unwind async tasks and stop serving processes.
    # The resumable store is flushed by the runner's finally block.
    raise KeyboardInterrupt


def main(argv=None):
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _handle_termination)
    try:
        return _main(argv)
    finally:
        signal.signal(signal.SIGTERM, previous)


def benchmark_entry():
    return main(["benchmark", *sys.argv[1:]])
