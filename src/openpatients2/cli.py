from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from pathlib import Path

import yaml

from .config import load_config
from .data import read_jsonl, records, write_json


def emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


def parser():
    p = argparse.ArgumentParser(prog="op2", description="Evidence-grounded OpenPatients extraction and the dense clinical reference set")
    sub = p.add_subparsers(dest="command", required=True)
    from .viewer import add_parser as add_viewer_parser
    add_viewer_parser(sub)
    cmd = sub.add_parser("ehr-pipeline", add_help=False,
                         help="Optional EHR stages on a finished extraction run -> unified article rows "
                              "(same as python -m openpatients2.ehr_pipeline; pass --help for its options)")
    cmd.add_argument("ehr_args", nargs=argparse.REMAINDER)
    cmd = sub.add_parser("dense", add_help=False,
                         help="Dense reference-set tools: op2 dense <tool> [args] (same as python -m "
                              "openpatients2.dense.<tool>; `op2 dense --help` lists the tools)")
    cmd.add_argument("dense_args", nargs=argparse.REMAINDER)
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
    cmd = sub.add_parser("sample")
    cmd.add_argument("--input", required=True)
    cmd.add_argument("--output", required=True)
    cmd.add_argument("--size", type=int, default=1000)
    cmd.add_argument("--seed", type=int, default=42)
    cmd = sub.add_parser("schema")
    cmd.add_argument("--output", default="schemas")
    cmd = sub.add_parser("validate")
    cmd.add_argument("--input", required=True)
    cmd.add_argument("--output", required=True)
    return p


def dense_usage():
    from .dense import TOOLS
    width = max(map(len, TOOLS))
    lines = ["usage: op2 dense <tool> [args...]   (op2 dense <tool> --help for a tool's options)", "",
             "Tools for benchmarks/dense500 and benchmarks/dense50 (modules in openpatients2.dense):"]
    lines += [f"  {name:<{width}}  {text}" for name, text in TOOLS.items()]
    lines += ["", "Validation is the same command WITHOUT --role; a --role call is a signature."]
    return "\n".join(lines)


def dense_main(argv):
    """Run openpatients2.dense.<tool> as __main__ with the remaining arguments (exits like python -m)."""
    import runpy
    from .dense import TOOLS
    if not argv or argv[0] in ("-h", "--help"):
        print(dense_usage())
        return 0 if argv else 2
    tool, rest = argv[0].replace("-", "_"), argv[1:]
    if tool not in TOOLS:
        print(f"op2 dense: unknown tool {argv[0]!r}\n\n{dense_usage()}", file=sys.stderr)
        return 2
    saved = sys.argv
    sys.argv = [f"op2 dense {tool}", *rest]
    try:
        runpy.run_module(f"openpatients2.dense.{tool}", run_name="__main__", alter_sys=False)
    finally:
        sys.argv = saved
    return 0


def _main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["dense"]:
        return dense_main(argv[1:])
    if argv[:1] == ["ehr-pipeline"]:
        from .ehr_pipeline import main as ehr_main
        ehr_main(argv[1:])
        return 0
    args = parser().parse_args(argv)
    try:
        if args.command == "viewer":
            from .viewer import dispatch
            result = dispatch(args)
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
            raise ValueError(f"Unknown command {args.command}")
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
