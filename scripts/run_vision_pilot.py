import asyncio,os,argparse
from pathlib import Path
from openpatients2.vision import run_vision
p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--key-file')
a=p.parse_args()
if a.key_file: os.environ['OPENROUTER_API_KEY']=Path(a.key_file).read_text().strip()
r=asyncio.run(run_vision(a.config));print(r['budget'])
