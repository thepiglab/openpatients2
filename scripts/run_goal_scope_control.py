"""Post-hoc prompt intervention on a public PMC case; not part of benchmark.

Use the same budget, routes, complete source, schemas and retry limit. Only the
explicit distinction between treatment targets and measured results changes.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path

import yaml

from openpatients2.article_tasks import patient_packet, check_article_task
from openpatients2.articles import recheck_license
from openpatients2.data import write_json
from openpatients2.experiment import Experiment
from openpatients2.prompts import messages_for
from openpatients2.validation import validate

INSTRUCTION = (
    "Treatment goals, titration targets and desired therapeutic ranges are NOT "
    "measured test results. For example, 'titrate anticoagulation to assay goal "
    "X–Y' does not document an assay result of X–Y. Do not create an observation "
    "for the target alone, or mark it resulted. Such targets belong in the "
    "medication regimen or monitoring plan, extracted by separate tasks. "
    "Retain all actual measured findings and explicitly documented performed "
    "tests, including repeated findings at distinct times."
)

TABLE_TIME_INSTRUCTION = (
    "For measurements in a table, preserve the time from the row-group heading "
    "(for example On admission versus At 48 h), not merely a general table caption "
    "that mentions both times. Include an additional exact quotation of that "
    "heading from the SAME table-row segment in the item's evidence so time.text "
    "occurs literally in its evidence. Keep the exact data-row quotation as well. "
    "Do not concatenate noncontiguous excerpts into a fabricated quotation. "
    "Do not erase a documented time to satisfy validation. Keep each patient's "
    "column and each time point distinct. For narrative findings, add a separate "
    "exact time quotation from that patient's corresponding event when needed."
)


async def main(key_file, table_time=False, schema_control=False):
    if key_file:
        os.environ["OPENROUTER_API_KEY"] = Path(key_file).read_text().strip()
    config = yaml.safe_load(Path("configs/experiments/medical-fidelity-v1.yaml").read_text())
    intervention = "" if schema_control else TABLE_TIME_INSTRUCTION if table_time else INSTRUCTION
    control = "schema-control" if schema_control else "table-time-control" if table_time else "goal-scope-control"
    config.update(output=f"runs/medical-fidelity-v1/{control}", concurrency=2,
                  intervention=intervention, clinical_tasks=["observations"], companion_tasks=[])
    if table_time or schema_control:
        config["models"] = [m for m in config["models"] if m["id"] in {"meta/muse-glimmer-30b", "meta/muse-spark-1.2"}]
    if schema_control:
        for model in config["models"]:
            model["response_format"] = "json_schema"
    pmcid = "PMC12802722" if table_time or schema_control else "PMC13294519"
    article = next(json.loads(line) for line in Path(config["input"]).read_text().splitlines()
                   if json.loads(line)["pmcid"] == pmcid)
    assert recheck_license(article["license"])["allowed"]
    saved = json.loads((Path(config["reference_rosters"]) / (article["article_id"] + ".json")).read_text())
    assert saved["xml_sha256"] == article["xml_sha256"]
    roster = check_article_task("roster", saved["roster"], article)
    experiment = Experiment(config)

    async def call(model):
        patient_index = 2 if schema_control else (1 if model["id"] == "meta/muse-glimmer-30b" else 2) if table_time else 0
        target = roster["patients"][patient_index]
        record = patient_packet(article, roster, target, scope="whole_article")

        def check(value):
            result = validate("observations", value, record["text"])
            if not result.valid:
                raise ValueError("; ".join(result.errors))
            return result.data

        messages = messages_for(record, "observations", "article-pilot-v1")
        if intervention:
            messages[-1]["content"] += "\nADDITIONAL SCOPE RULE:\n" + intervention
        result = await experiment.call(model, "observations", messages, check,
                                       {"article_id": article["article_id"], "patient_id": target["patient_id"],
                                        "intervention": control})
        write_json(experiment.root / (model["id"].replace("/", "--") + ".json"), result)

    try:
        await asyncio.gather(*(call(model) for model in config["models"]))
        write_json(experiment.root / "report.json", {"metrics": experiment.metrics,
                                                    "budget": experiment.budget.report()})
    finally:
        await experiment.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-file")
    controls = parser.add_mutually_exclusive_group()
    controls.add_argument("--table-time", action="store_true", help="Test table-header timing evidence on the two observed regression cases")
    controls.add_argument("--schema-control", action="store_true", help="Request API JSON schema on case 3, keeping the original baseline prompts")
    args = parser.parse_args()
    asyncio.run(main(args.key_file, args.table_time, args.schema_control))
