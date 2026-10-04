"""Low-overhead allocation telemetry so idle periods have observable evidence."""
from contextlib import asynccontextmanager
import asyncio
import csv
import json
import os
from pathlib import Path
import subprocess
import time


def sample():
    try:
        result=subprocess.run(['nvidia-smi','--query-gpu=timestamp,index,uuid,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw',
            '--format=csv,noheader,nounits'],text=True,capture_output=True,timeout=10)
        visible=(os.environ.get('SLURM_STEP_GPUS') or os.environ.get('SLURM_JOB_GPUS') or os.environ.get('CUDA_VISIBLE_DEVICES','')).split(',')
        raw=list(csv.reader(result.stdout.splitlines(),skipinitialspace=True))
        rows=[r for r in raw if len(r)==8 and (r[1] in visible or r[2] in visible)]
        if not rows and len(raw)==len(visible) and visible!=['']: rows=raw
        return {'time_unix':time.time(),'returncode':result.returncode,'allocated_devices':visible,
            'fields':['timestamp','index','uuid','gpu_utilization_pct','memory_utilization_pct','memory_used_mib','memory_total_mib','power_w'],
            'gpus':rows,'error':result.stderr if rows else result.stderr+' No allocated GPUs identified'}
    except (OSError,subprocess.TimeoutExpired) as exc:
        return {'time_unix':time.time(),'error':str(exc)}


@asynccontextmanager
async def monitor(path, *, interval=15):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    stopped=asyncio.Event()
    async def collect():
        while True:
            row=await asyncio.to_thread(sample)
            # Read the phase receipt without embedding patient/model output.
            progress=path.parent/'progress.json'
            if progress.exists():
                try: row['phase']=json.loads(progress.read_text())
                except (OSError,ValueError): pass
            with path.open('a') as out: out.write(json.dumps(row)+'\n')
            if stopped.is_set(): return
            try: await asyncio.wait_for(stopped.wait(),timeout=interval)
            except TimeoutError: continue
    task=asyncio.create_task(collect())
    try: yield
    finally:
        stopped.set()
        await task
