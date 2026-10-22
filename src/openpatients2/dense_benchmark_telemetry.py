"""Low-overhead allocation telemetry so idle periods have observable evidence."""
from contextlib import asynccontextmanager
import asyncio
import csv
import json
import os
from pathlib import Path
import subprocess
import time


def host_memory():
    """Measure this Slurm cgroup, never substitute whole-node RAM usage."""
    try:
        entries = Path('/proc/self/cgroup').read_text().splitlines()
        for entry in entries:
            hierarchy, controllers, relative = entry.split(':', 2)
            if hierarchy == '0' and not controllers:
                base = Path('/sys/fs/cgroup') / relative.lstrip('/')
                names = {'current_bytes':'memory.current', 'peak_bytes':'memory.peak', 'limit_bytes':'memory.max'}
            elif 'memory' in controllers.split(','):
                base = Path('/sys/fs/cgroup/memory') / relative.lstrip('/')
                names = {'current_bytes':'memory.usage_in_bytes', 'peak_bytes':'memory.max_usage_in_bytes', 'limit_bytes':'memory.limit_in_bytes'}
            else:
                continue
            values = {}
            for key, name in names.items():
                try:
                    value = (base / name).read_text().strip()
                    values[key] = int(value) if value.isdigit() else None
                except OSError:
                    values[key] = None
            if values.get('current_bytes') is not None:
                return dict(values, available=True, scope='current process cgroup including descendants', cgroup=str(base))
    except (OSError, ValueError):
        pass
    return {'available':False, 'scope':'unavailable; no node-wide fallback'}


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
            row['host_memory'] = await asyncio.to_thread(host_memory)
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
