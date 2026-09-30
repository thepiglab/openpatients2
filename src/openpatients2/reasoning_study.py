"""Preplanned, within-model reasoning ablation with paired records and seed repeats.

Prepare/profile and analyze on CPU. Run only performs the scheduled endpoint calls.
There is no generic promise that an endpoint supports or honors reasoning controls.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import random
from collections import defaultdict
from pathlib import Path

import yaml
from pydantic import Field, model_validator

from . import __version__, SCHEMA_VERSION
from .agreement import atoms, cluster_map, cluster_summary, compare_rows, gold_metrics, holm, mean, paired_signflip
from .config import ConfigModel, PipelineConfig, load_config
from .data import input_fingerprint, read_jsonl, records, sample_records, write_json
from .exports import export_run
from .pipeline import run_pipeline, smoke


class ReasoningLevel(ConfigModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    value: str | int | bool


class StudyConfig(ConfigModel):
    pipeline: str
    output: str = "runs/reasoning-k2"
    sample_size: int = Field(default=256, ge=1)
    sample_seed: int = 1927
    # A single explicitly declared control prevents accidental changes to temperature/model.
    control_path: str | None = "chat_template_kwargs.reasoning_effort"
    levels: list[ReasoningLevel] = Field(min_length=1)
    seeds: list[int] = Field(default_factory=lambda: [17,43,101], min_length=2)
    send_seed: bool = True
    max_tokens: int = Field(default=32768, ge=128)
    bootstrap_resamples: int = Field(default=2000, ge=100)
    permutation_resamples: int = Field(default=9999, ge=100)
    analysis_seed: int = 42
    gold: str | None = None
    # Nominal intervals, not an automatic equivalence or quality declaration.
    primary_difference_metrics: list[str] = Field(default_factory=lambda: [
        "source_valid", "completion_tokens", "reasoning_characters", "latency_seconds",
        "clinical_f1", "gold_task_exact", "negative_case_success"])
    notes: str = "Verify this checkpoint/template supports the requested control before running."

    @model_validator(mode="after")
    def validate_design(self):
        if len({x.name for x in self.levels}) != len(self.levels):
            raise ValueError("Reasoning level names must be unique")
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("Use distinct seeds for repeated samples")
        if len(self.levels) > 1 and len({json.dumps(x.value) for x in self.levels}) != len(self.levels):
            raise ValueError("Different level names must select different control values")
        if self.control_path is None and len(self.levels) != 1:
            raise ValueError("A default-mode study has exactly one level; do not claim unsupported effort levels")
        if self.control_path not in {None, "reasoning_effort", "chat_template_kwargs.reasoning_effort",
                                     "chat_template_kwargs.enable_thinking", "chat_template_kwargs.thinking",
                                     "thinking_token_budget"}:
            raise ValueError("Only declared reasoning controls may vary, not arbitrary API parameters")
        known = {"source_valid","completion_tokens","reasoning_tokens","reasoning_characters","latency_seconds",
                 "empty_output","clinical_f1","gold_task_exact","negative_case_success","gold_expected_recall",
                 "gold_forbidden_violations"}
        if set(self.primary_difference_metrics)-known:
            raise ValueError("Unknown primary comparison metric")
        return self


def set_control(extra: dict, path: str, value):
    node = extra
    parts = path.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part,{})
        if not isinstance(node,dict):
            raise ValueError("Reasoning control conflicts with existing non-object API option")
    node[parts[-1]] = value


def load_spec(path: str) -> StudyConfig:
    return StudyConfig.model_validate(yaml.safe_load(Path(path).read_text()))


def prepare_study(path: str, tokenizer: str | None = None, workers: int = 8) -> dict:
    """Freeze the SAME sample, all run configs and randomized blocked order before calls."""
    spec = load_spec(path)
    root = Path(spec.output)
    if (root / "study.json").exists():
        raise ValueError("Study already exists. Resume it or choose a new output; do not overwrite a preregistered design.")
    base = load_config(spec.pipeline)
    if base.api.revision in {"main","UNPINNED","unspecified"} and not base.allow_unpinned_revision:
        raise ValueError("Pin the checkpoint before preparing a reasoning study")
    if base.require_token_profile and tokenizer is None:
        raise ValueError("Provide --tokenizer for CPU token profiles at EACH reasoning level")
    root.mkdir(parents=True, exist_ok=True)
    selected = sample_records(records(base.input),spec.sample_size,spec.sample_seed)
    if not selected:
        raise ValueError("Empty study sample")
    selected.sort(key=lambda x:x["record_id"])
    sample_path = root / "sample.jsonl"
    sample_path.write_text("".join(json.dumps(x,ensure_ascii=False)+"\n" for x in selected))
    sample_path.chmod(0o600)
    scope = {x["record_id"]:input_fingerprint(x) for x in selected}
    scope_digest = hashlib.sha256(json.dumps(scope,sort_keys=True).encode()).hexdigest()
    design = {"package_version":__version__,"schema_version":SCHEMA_VERSION,"spec":spec.model_dump(),
              "base_config":base.model_dump(),"model":base.api.identity(),"sample_scope":scope,
              "sample_sha256":hashlib.sha256(sample_path.read_bytes()).hexdigest(),
              "scope_digest":scope_digest,"schedule":[],"runs":[],
              "independence_unit":"source clusters; repeats and task rows are not independent patients",
              "control_validation":"requested, not independently verified by this client"}
    configs = {}
    for level in spec.levels:
        cfg = base.model_copy(deep=True)
        cfg.input = str(sample_path)
        cfg.api.max_tokens = cfg.api.max_retry_tokens = spec.max_tokens
        cfg.api.save_reasoning = cfg.api.save_messages = True
        # Invalid clinical/JSON outputs stay failures in this study; no output-repair selection.
        cfg.validation_retries = 0
        cfg.export_after_run = False
        cfg.shuffle_seed = None
        cfg.namespace = f"op2-reasoning-{scope_digest[:12]}-{level.name}"
        if spec.control_path is not None:
            set_control(cfg.api.extra_body,spec.control_path,level.value)
        cfg = PipelineConfig.model_validate(cfg.model_dump())
        if tokenizer is not None:
            from .profile import create_profile
            cfg.token_profile = str(root / "profiles" / f"{level.name}.jsonl")
            create_profile(cfg,tokenizer,cfg.token_profile,workers)
            # Reject unequal/insufficient context budgets before reserving GPUs.
            from .profile import TokenGuard
            guard = TokenGuard(cfg)
            for source_row in selected:
                for task in cfg.tasks:
                    guard.budget(source_row, task, spec.max_tokens)
        else:
            cfg.token_profile = None  # Explicit mock/test mode only.
        configs[level.name] = cfg
    rng = random.Random(spec.sample_seed)
    for repeat, seed in enumerate(spec.seeds):
        levels = list(spec.levels)
        rng.shuffle(levels)
        for level in levels:
            cfg = configs[level.name].model_copy(deep=True)
            directory = f"{level.name}/repeat-{repeat:02d}"
            cfg.output = str(root/directory)
            cfg.api.seed = seed if spec.send_seed else None
            # No cross-effort/repeat KV reuse. Within a run, note-first task locality remains.
            cfg.namespace += f"-repeat-{repeat:02d}"
            config_path = root / "run-configs" / f"{level.name}-{repeat:02d}.yaml"
            config_path.parent.mkdir(parents=True,exist_ok=True)
            config_path.write_text(yaml.safe_dump(cfg.model_dump(),sort_keys=False))
            config_path.chmod(0o600)
            design["schedule"].append({"level":level.name,"value":level.value,"repeat":repeat,"seed":seed,
                                       "directory":directory,"config":str(config_path.relative_to(root)),
                                       "config_hash":cfg.digest()})
    if spec.gold:
        raw_gold = Path(spec.gold).read_bytes()
        (root/"frozen-gold.jsonl").write_bytes(raw_gold)
        (root/"frozen-gold.jsonl").chmod(0o600)
        design["gold_sha256"] = hashlib.sha256(raw_gold).hexdigest()
    write_json(root/"study.json",design)
    return {"study":str(root/"study.json"),"records":len(selected),"scheduled_runs":len(design["schedule"]),
            "scheduled_task_outputs":len(selected)*len(base.tasks)*len(design["schedule"]),
            "mode":"CPU preparation only; no inference performed"}


def verify_study(root: Path, design: dict):
    if design["package_version"] != __version__ or design["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Study package/schema changed; freeze the old environment or prepare a new study")
    if hashlib.sha256((root/"sample.jsonl").read_bytes()).hexdigest() != design["sample_sha256"]:
        raise ValueError("Frozen study sample was modified")


async def run_study(study: str, serving: str | None = None, client_factory=None, warmup: bool = True) -> dict:
    root = Path(study).parent if Path(study).is_file() else Path(study)
    design = json.loads((root/"study.json").read_text())
    verify_study(root,design)
    group, commands = None,None
    server_identity = None
    if serving:
        from .serving import ServerGroup, ServingConfig
        cfg = ServingConfig.load(serving)
        snapshot = json.loads((Path(cfg.model_path)/"op2_snapshot.json").read_text())
        if any(snapshot[k] != design["model"][k] for k in ["model_id","revision"]):
            raise ValueError("Reasoning study cannot mix model/checkpoint identities")
        if cfg.served_model_name != design["model"]["model"]:
            raise ValueError("Serving alias differs from study client alias")
        if cfg.max_model_len != design["base_config"]["max_model_len"]:
            raise ValueError("Serving context limit differs from study token budget")
        server_identity = hashlib.sha256(Path(serving).read_bytes()).hexdigest()
    else:
        server_identity = "external-endpoints:"+json.dumps(design["base_config"]["api"]["endpoints"])
    previous = design.get("server_identity")
    if previous and previous != server_identity:
        raise ValueError("Cannot change serving topology mid-study; prepare a new study")
    design["server_identity"] = server_identity
    write_json(root/"study.json",design)
    completed = {(x["level"],x["repeat"]) for x in design["runs"]}
    try:
        if serving:
            group = ServerGroup(cfg,".",str(root/"server"))
            commands = await group.start()
        for cell in design["schedule"]:
            if (cell["level"],cell["repeat"]) in completed:
                continue
            config = load_config(root/cell["config"])
            if config.digest() != cell["config_hash"]:
                raise ValueError("Scheduled config changed after study preparation")
            if config.api.identity() != design["model"]:
                raise ValueError("Study is strictly within a single checkpoint")
            if commands:
                config.api.endpoints = [x["endpoint"] for x in commands]
                config.api.metrics_urls = [x["endpoint"].removesuffix("/v1")+"/metrics" for x in commands]
            if warmup and client_factory is None:
                checked = await smoke(config,next(records(config.input)),all_tasks=True)
                write_json(root/cell["directory"]/"smoke.json",checked)
                if not checked["passed"]:
                    raise RuntimeError(f"Reasoning control/parser/schema smoke failed for {cell['level']}; no silent fallback")
            # Progress contains no patient text or generated reasoning.
            print(f"Reasoning study: {cell['level']} repeat {cell['repeat']+1}/{len(design['spec']['seeds'])}",flush=True)
            client = client_factory(config) if client_factory else None
            try:
                report = await run_pipeline(config,client=client)
            finally:
                if client is not None:
                    await client.close()
            write_json(root/cell["directory"]/"study-cell.json",cell)
            design["runs"].append({**cell,"run_id":report["run_id"],"report":report})
            write_json(root/"study.json",design)
        return {"study":str(root/"study.json"),"completed_runs":len(design["runs"]),
                "scheduled_runs":len(design["schedule"]),"next":"CPU: op2 reasoning-analyze --study "+str(root)}
    finally:
        if group:
            group.stop()


AGREEMENT_METRICS = ["both_valid","both_failed","exact_agreement_all_scheduled","canonical_exact_agreement",
    "nonempty_exact_agreement","fact_set_dice","fact_set_jaccard","both_empty","documentation_status_agreement",
    "exact_evidence_on_matched_facts","span_dice_on_matched_facts"]


def _summaries(details: list[dict], metrics: list[str], clusters: dict[str,str], resamples: int, seed: int) -> dict:
    output = {}
    for metric in metrics:
        grouped = defaultdict(list)
        for row in details:
            grouped[row["record_id"]].append(row.get(metric))
        values = {rid:mean(items) for rid,items in grouped.items()}
        output[metric] = cluster_summary(values,clusters,resamples,seed)
    return output


def analyze_study(study: str, allow_partial: bool = False, gold_override: str | None = None) -> dict:
    root = Path(study).parent if Path(study).is_file() else Path(study)
    design = json.loads((root/"study.json").read_text())
    verify_study(root,design)
    if len(design["runs"]) != len(design["schedule"]) and not allow_partial:
        raise ValueError("Study incomplete; run remaining cells or explicitly use --allow-partial")
    spec = StudyConfig.model_validate(design["spec"])
    sources = {x["record_id"]:x for x in records(root/"sample.jsonl")}
    clusters = cluster_map(sources)
    scope = design["sample_scope"]
    tasks = design["base_config"]["tasks"]
    gold = defaultdict(list)
    gold_path = Path(gold_override) if gold_override else (root/"frozen-gold.jsonl" if design.get("gold_sha256") else None)
    if gold_path is not None:
        gold_sha256 = hashlib.sha256(gold_path.read_bytes()).hexdigest()
        if not gold_override and gold_sha256 != design["gold_sha256"]:
            raise ValueError("Frozen gold labels were modified")
        seen = set()
        for label in read_jsonl(gold_path):
            key=(label["record_id"],label["task"],label.get("collection","items"))
            if key in seen:
                raise ValueError("Duplicate gold record/task/collection")
            seen.add(key)
            gold[key[:2]].append(label)
    cells, run_reports = {},{}
    observed_response_models = set()
    reasoning_availability = defaultdict(lambda: defaultdict(int))
    for cell in design["runs"]:
        directory = root/cell["directory"]
        # Merge deferred exports on the CPU after inference, not inside GPU timing.
        export_run(str(directory),cell["run_id"])
        loaded = {}
        for row in read_jsonl(directory/"extractions.jsonl"):
            if row["model"] != design["model"] or row["schema_version"] != design["schema_version"]:
                raise ValueError("Cannot compare different models, checkpoints, or schemas")
            if row["input_hash"] != scope.get(row["record_id"]):
                raise ValueError("Cell does not use the frozen input revision")
            key = (row["record_id"],row["task"])
            if key in loaded:
                raise ValueError("Duplicate extraction in a study cell")
            observed = (row.get("generation") or {}).get("response_model")
            if observed:
                observed_response_models.add(observed)
            reasoning_availability[cell["level"]][(row.get("generation") or {}).get("reasoning_status", "unavailable")] += 1
            loaded[key] = row
        if set(loaded) != set(itertools.product(scope,tasks)):
            raise ValueError("Cell is missing scheduled rows; missing failures cannot be silently dropped")
        name = (cell["level"],cell["repeat"])
        if name in cells:
            raise ValueError("Duplicate study run")
        cells[name] = loaded
        run_reports[name] = cell["report"]
    if len(observed_response_models) > 1:
        raise ValueError("Endpoint returned different model identifiers within a single-model study")
    pairwise, within, differences, disagreement_rows = [],[],[],[]
    levels = [x.name for x in spec.levels]
    def one_comparison(pairs, name):
        details = []
        for lk,rk in pairs:
            if lk not in cells or rk not in cells:
                continue
            for key in sorted(cells[lk]):
                lrow,rrow=cells[lk][key],cells[rk][key]
                value = compare_rows(lrow,rrow)
                details.append({"record_id":key[0],"task":key[1],**value})
                if not value["exact_agreement_all_scheduled"]:
                    # Include exact fact payloads, quotations and trace references for blinded adjudication.
                    disagreement_rows.append({"comparison":name,"record_id":key[0],"task":key[1],
                        "left_cell":list(lk),"right_cell":list(rk),"metrics":value,
                        "left_status":lrow["status"],"right_status":rrow["status"],
                        "left_extraction":lrow["extraction"],"right_extraction":rrow["extraction"]})
        return {"comparison":name,"paired_task_outputs":len(details),
                "overall":_summaries(details,AGREEMENT_METRICS,clusters,spec.bootstrap_resamples,spec.analysis_seed),
                "by_task":{task:_summaries([x for x in details if x["task"]==task],AGREEMENT_METRICS,
                                             clusters,spec.bootstrap_resamples,spec.analysis_seed) for task in tasks}}
    for left,right in itertools.combinations(levels,2):
        pairs=[((left,i),(right,i)) for i in range(len(spec.seeds))]
        pairwise.append(one_comparison(pairs,f"{left} vs {right}"))
    for level in levels:
        pairs=[((level,i),(level,j)) for i,j in itertools.combinations(range(len(spec.seeds)),2)]
        within.append(one_comparison(pairs,f"{level} repeatability"))
    metrics_by_cell={}
    level_details=defaultdict(list)
    for cell,rows in cells.items():
        metrics_by_cell[cell]={}
        for key,row in rows.items():
            valid=row["status"]=="valid"
            gm=row.get("attempt_summary") or (row.get("generation") or {}).get("metrics") or {}
            metric={"source_valid":float(valid),
                **{x:gm.get(x) for x in ["completion_tokens","reasoning_tokens","reasoning_characters","latency_seconds"]},
                "empty_output":float(not atoms(row["extraction"],row["task"])) if valid else None,
                **gold_metrics(row,gold.get(key,[]))}
            metrics_by_cell[cell][key]=metric
            level_details[cell[0]].append({"record_id":key[0],"task":key[1],**metric})
    # Differences average task/seed paired deltas WITHIN records first; then resample source clusters.
    for left,right in itertools.combinations(levels,2):
        for metric in spec.primary_difference_metrics:
            by_record=defaultdict(list)
            for repeat in range(len(spec.seeds)):
                a,b=metrics_by_cell.get((left,repeat),{}),metrics_by_cell.get((right,repeat),{})
                for key in a.keys() & b.keys():
                    av,bv=a[key].get(metric),b[key].get(metric)
                    if av is not None and bv is not None:
                        by_record[key[0]].append(bv-av)
            deltas={rid:mean(values) for rid,values in by_record.items()}
            summary=cluster_summary(deltas,clusters,spec.bootstrap_resamples,spec.analysis_seed)
            differences.append({"comparison":f"{right} minus {left}","metric":metric,
                **summary,**paired_signflip(deltas,clusters,spec.permutation_resamples,spec.analysis_seed)})
    adjusted=holm([x["p_value"] for x in differences])
    for row,pvalue in zip(differences,adjusted):
        row["p_value_holm_all_primary_tests"]=pvalue
    report={"model":design["model"],"schema_version":SCHEMA_VERSION,"records":len(sources),
            "source_clusters":len(set(clusters.values())),"completed_runs":len(cells),
            "scheduled_runs":len(design["schedule"]),"is_partial":len(cells)!=len(design["schedule"]),
            "gold_supplied":gold_path is not None,
            "gold_provenance":{"sha256": gold_sha256 if gold_path is not None else None,
                               "external_post_preparation_override": gold_override is not None},
            "demonstration_only":design.get("demonstration_only",False),
            "observed_response_models":sorted(observed_response_models),
            "reasoning_availability":{level:dict(counts) for level,counts in reasoning_availability.items()},
            "within_model_only":True,"clinical_equivalence_established":False,
            "pairwise_agreement":pairwise,"same_level_repeatability":within,"paired_differences":differences,
            "by_level":{level:_summaries(level_details[level],spec.primary_difference_metrics,
                 clusters,spec.bootstrap_resamples,spec.analysis_seed) for level in levels},
            "execution_reports":[{"level":k[0],"repeat":k[1],"report":v} for k,v in run_reports.items()],
            "warnings":[
                "Agreement is consistency, not truth; neither higher reasoning nor consensus is a gold label.",
                "Both-empty outputs are reported separately and excluded from fact Dice/Jaccard, not scored as perfect factual similarity.",
                "Failed/truncated outputs never count as agreement; both-valid agreement is accompanied by an all-scheduled metric.",
                "Fact matching is lexical/canonical, not comprehensive terminology or clinical equivalence.",
                "Span metrics are conditional on identical clinical facts; multiply-occurring evidence is not assigned an arbitrary offset.",
                "Bootstrap CIs are nominal pointwise 95% percentile intervals; Holm adjusts ALL prespecified primary difference p-values only.",
                "No significant difference does not prove equivalence/noninferiority; clinical margins and adjudicated labels need a separate prespecified decision.",
                "Degenerate bootstrap intervals (e.g. all observed agreements) do not prove zero population error.",
                "Distinct source IDs are not verified independent patients. Supply reviewed cluster_id for related reports.",
                "Seed support and effort controls may be ignored by an endpoint; saved requests document intent, not proof of internal implementation.",
                "Reused/resumed records invalidate naive full-run speed comparisons; inspect execution reports and rerun a fresh study for timing."]}
    analysis=root/"analysis";analysis.mkdir(exist_ok=True)
    write_json(analysis/"report.json",report)
    with (analysis/"disagreements.jsonl").open("w") as f:
        for row in disagreement_rows:
            f.write(json.dumps(row,ensure_ascii=False)+"\n")
    (analysis/"disagreements.jsonl").chmod(0o600)
    write_json(analysis/"source-clusters.json",clusters)
    _markdown_report(analysis/"report.md",report)
    return {"report":str(analysis/"report.json"),"readable_report":str(analysis/"report.md"),
            "disagreements":len(disagreement_rows),"within_model_only":True,"clinical_equivalence_established":False}


def _markdown_report(path: Path, report: dict):
    lines=["# Within-model reasoning study", "",
           "**SYNTHETIC SOFTWARE FIXTURE — no model inference, clinical evaluation, or GPU throughput measurement.**" if report.get("demonstration_only") else "",
           "", f"Checkpoint: `{report['model']['model_id']}` at `{report['model']['revision']}`.",
           f"{report['records']} source records; {report['source_clusters']} source clusters; {report['completed_runs']}/{report['scheduled_runs']} runs.",
           "", "Agreement is not clinical accuracy. The JSON report includes task-level intervals, denominators and paired tests.",
           "", "| Comparison | Exact on all scheduled | Fact Dice (nonempty) | Both empty |", "|---|---:|---:|---:|"]
    def fmt(value):
        if value["mean"] is None: return "not estimable"
        ci=value["ci95_percentile"]
        return f"{value['mean']:.3f}" + (f" [{ci[0]:.3f}, {ci[1]:.3f}]" if ci else "")
    for item in report["pairwise_agreement"]+report["same_level_repeatability"]:
        o=item["overall"]
        lines.append(f"| {item['comparison']} | {fmt(o['exact_agreement_all_scheduled'])} | {fmt(o['fact_set_dice'])} | {fmt(o['both_empty'])} |")
    lines += ["", "## Interpretation", "", *["- "+x for x in report["warnings"]], ""]
    path.write_text("\n".join(lines))
