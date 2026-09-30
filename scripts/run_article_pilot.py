"""Run with OPENROUTER_API_KEY in the environment. Optional ephemeral key file."""
import argparse
import asyncio
import os
from pathlib import Path
from openpatients2.experiment import run_experiment

p=argparse.ArgumentParser()
p.add_argument('--config',default='configs/experiments/article-workflow.yaml')
p.add_argument('--key-file',help='Optional private, temporary credential file; never copied to outputs')
args=p.parse_args()
if args.key_file:
    os.environ['OPENROUTER_API_KEY']=Path(args.key_file).read_text().strip()
r=asyncio.run(run_experiment(args.config))
print(r['budget'])
