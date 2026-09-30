"""Finish this active experiment sequentially after the map writer exits."""
import fcntl
from pathlib import Path
import subprocess
import sys
import time

root=Path('runs/chunking-v1')
with (root/'calls/.writer.lock').open('a') as lock:
    while True:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            break
        except BlockingIOError:
            time.sleep(5)
    fcntl.flock(lock,fcntl.LOCK_UN)
stages=[('reduce',['experiments/chunking_parallel_v1.py','--phase','reduce']),
        ('bottleneck',['experiments/chunking_parallel_v1.py','--phase','bottleneck']),
        ('repair',['experiments/chunking_repair_v1.py']),
        ('repaired-hierarchy',['experiments/chunking_repaired_hierarchy_v1.py']),
        ('score-strict',['experiments/score_chunking_v1.py']),
        ('score-normalized',['experiments/score_chunking_v1.py','--normalized']),
        ('audit',['experiments/audit_chunking_v1.py'])]
for name,args in stages:
    print('Starting',name,flush=True)
    with (root/f'{name}.log').open('w') as log:
        subprocess.run([sys.executable,*args],stdout=log,stderr=subprocess.STDOUT,check=True)
    print('Completed',name,flush=True)
