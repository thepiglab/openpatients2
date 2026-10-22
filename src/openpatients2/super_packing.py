"""Budgeted Slurm waves with isolated model runtimes and shared article inputs.

Each profile is an ordinary serial Super campaign. Replicas share read-only
weights and atomically claim distinct articles; they never share GPU devices.
"""
import copy
import os
from pathlib import Path
import shutil
import subprocess
import time

from . import dense_benchmark as b

LIMITS = {'gpus': 8, 'cpus': 32, 'memory_gib': 250}
# Host RAM requests include substantial headroom over sacct batch MaxRSS from
# run-20261021-170808. MaxRSS is evidence, not an aggregate cgroup guarantee.
RESOURCES = {
    'nvfp4': {'gpus': 1, 'cpus': 4, 'memory_gib': 32, 'replicas': 2, 'observed_maxrss_gib': 8.87, 'artifacts_gib': 80},
    'bf16': {'gpus': 2, 'cpus': 8, 'memory_gib': 64, 'replicas': 1, 'observed_maxrss_gib': 19.93, 'artifacts_gib': 245},
    'beam-nvfp4-dflash2': {'gpus': 4, 'cpus': 16, 'memory_gib': 96, 'replicas': 1, 'observed_maxrss_gib': 31.07, 'artifacts_gib': 290},
    'spark': {'gpus': 8, 'cpus': 16, 'memory_gib': 160, 'replicas': 1, 'observed_maxrss_gib': 51.98, 'artifacts_gib': 520},
}


def plan(profiles):
    if not profiles or set(profiles) - RESOURCES.keys():
        raise ValueError('Packed scheduling supports only the four focused profiles; use serial for other recipes')
    waves = []
    # First-fit decreasing by total GPU allocation; Spark necessarily alone.
    # Prefer the small-model wave first to expose serving problems sooner.
    for name in sorted(profiles, key=lambda p: (p == 'spark', -RESOURCES[p]['gpus'])):
        r = RESOURCES[name]
        cost = {k: r[k] * r['replicas'] for k in LIMITS}
        for wave in waves:
            if all(wave['totals'][k] + cost[k] <= limit for k, limit in LIMITS.items()):
                break
        else:
            wave = {'profiles': [], 'totals': {k: 0 for k in LIMITS}}
            waves.append(wave)
        wave['profiles'].append(name)
        for k in LIMITS:
            wave['totals'][k] += cost[k]
        if any(wave['totals'][k] > v for k, v in LIMITS.items()):
            raise ValueError('Packed wave exceeds allocation limits')
    for wave in waves:
        wave['required_free_gib'] = sum(RESOURCES[p]['artifacts_gib'] for p in wave['profiles']) + 128
    return {'mode': 'packed', 'version': 1, 'limits': dict(LIMITS),
            'resources': {p: dict(RESOURCES[p]) for p in profiles}, 'waves': waves,
            'memory_evidence': 'User-supplied sacct batch MaxRSS, jobs 47060318/21/27/37; requests are not GPU VRAM limits.',
            'storage_note': 'Concurrent residency requires more disk than serial; artifact estimates plus 128 GiB scratch/results reserve.'}


def slot(work, profile):
    path = work / 'profiles' / profile
    if (work / 'profiles').is_symlink() or path.is_symlink():
        raise ValueError('Refusing symlink profile workspace')
    return path


def create_slots(work, manifest):
    """Freeze independent lifecycle manifests; results are collected after jobs stop."""
    for profile in manifest['nemotron_super']['profiles']:
        child = slot(work, profile)
        child.mkdir(parents=True)
        for name in ('code', 'inputs', 'reference'):
            shutil.copytree(work / name, child / name, copy_function=os.link)
        arms = {name: d for name, d in manifest['nemotron_super']['arms'].items() if d['profile'] == profile}
        for name in arms:
            shutil.copytree(work / 'arms' / name, child / 'arms' / name, copy_function=os.link)
        cm = copy.deepcopy(manifest)
        cp = cm['nemotron_super']
        cp.update(profiles=[profile], arms=arms, scheduler={'mode': 'serial'},
                  engines={manifest['nemotron_super']['recipes'][profile].get('engine', 'nemotron'):
                           manifest['nemotron_super']['engines'][manifest['nemotron_super']['recipes'][profile].get('engine', 'nemotron')]})
        cp['recipes'] = {profile: cp['recipes'][profile]}
        models = [cp['recipes'][profile]['model']]
        if 'drafter' in cp['recipes'][profile]:
            models.append(cp['recipes'][profile]['drafter'])
        cp['pins'] = {model: cp['pins'][model] for model in models}
        cm['files'] = b.inventory(child, ['code', 'inputs', 'reference'])
        for name in arms:
            cm['files'][f'arms/{name}/manifest.json'] = b.sha(child / 'arms' / name / 'manifest.json')
        b.write(child / 'manifest.json', cm)
        b.write(child / 'prepared.json', {'manifest_sha256': b.sha(child / 'manifest.json')})
        manifest['files'][f'profiles/{profile}/manifest.json'] = b.sha(child / 'manifest.json')
        manifest['files'][f'profiles/{profile}/prepared.json'] = b.sha(child / 'prepared.json')


def collect(work, manifest):
    """Called only after all recorded worker/download/cleanup jobs are terminal."""
    for profile in manifest['nemotron_super']['profiles']:
        child = slot(work, profile)
        from .dense_nemotron_super import check
        check(child)
        for name in manifest['nemotron_super']['arms']:
            origin = child / 'arms' / name
            if not origin.exists():
                continue
            for folder in ('results',):
                if (origin / folder).exists():
                    shutil.copytree(origin / folder, work / 'arms' / name / folder, dirs_exist_ok=True)
            if (origin / 'runtime.json').exists():
                shutil.copyfile(origin / 'runtime.json', work / 'arms' / name / 'runtime.json')
        dest = work / 'results/profile-runtimes' / profile
        dest.mkdir(parents=True, exist_ok=True)
        for folder in ('results', 'logs'):
            if (child / folder).exists():
                shutil.copytree(child / folder, dest / folder, dirs_exist_ok=True)
        for filename in ('manifest.json', 'prepared.json', 'runtime.json'):
            if (child / filename).exists():
                shutil.copyfile(child / filename, dest / filename)


def preflight(work, wave_index):
    from .dense_nemotron_super import check
    m = check(work)
    wave = m['nemotron_super']['scheduler']['waves'][wave_index]
    # Existing artifacts count toward residency after resume. They live below
    # this campaign only; never assume another campaign's bytes are reclaimable.
    resident = 0
    for profile in wave['profiles']:
        runtime = slot(work, profile) / 'runtime'
        candidates = list((runtime / 'weights').rglob('*')) + list(runtime.glob('*.sif'))
        for path in candidates:
            if '.cache' in path.parts or path.name.endswith('.building.sif'):
                continue
            if path.is_file() and not path.is_symlink():
                resident += path.stat().st_size
    free = shutil.disk_usage(work).free
    needed = max(0, wave['required_free_gib'] * 2**30 - resident)
    receipt = {'wave': wave_index, 'free_bytes': free, 'required_free_bytes': needed,
               'resident_bytes': resident, 'passed': free >= needed}
    b.write(work / f'results/packing-preflight-{wave_index}.json', receipt)
    if free < needed:
        raise ValueError(f'Packed wave needs approximately {needed / 2**30:.0f} GiB free; '
                         f'only {free / 2**30:.0f} GiB available. Free old runtime artifacts or start a new serial campaign.')
    return receipt


def submit(work, manifest, account, qos, pending, dry_run):
    """CPU staging and cleanup are serial barriers around concurrent GPU waves."""
    jobspecs = []
    wave_last = []
    scheduler = manifest['nemotron_super']['scheduler']
    for wi, wave in enumerate(scheduler['waves']):
        profiles = [p for p in wave['profiles'] if any(manifest['nemotron_super']['arms'][a]['profile'] == p for a in pending)]
        # Include cleanup for canceled, already-completed profiles too.
        profiles = [p for p in wave['profiles'] if p in profiles or (slot(work,p)/'runtime/active-profile.json').exists()]
        if not profiles:
            continue
        pre = f'preflight-{wi}'
        jobspecs.append((pre, 'packing-preflight', work, str(wi), None, [('afterok', wave_last)] if wave_last else []))
        last_cpu = [pre]
        staged = []
        for p in profiles:
            arms = [a for a in pending if manifest['nemotron_super']['arms'][a]['profile'] == p]
            if not arms:
                continue
            setup, download = f'setup-{p}', f'download-{p}'
            jobspecs.append((setup, 'setup', slot(work,p), '-', None, [('afterok', last_cpu)]))
            jobspecs.append((download, 'download', slot(work,p), p, None, [('afterok', [setup])]))
            staged.append(download)
            last_cpu = [download]
        workers = []
        for p in profiles:
            for arm in [a for a in pending if manifest['nemotron_super']['arms'][a]['profile'] == p]:
                for replica in range(scheduler['resources'][p]['replicas']):
                    key = f'worker-{arm}-r{replica}'
                    jobspecs.append((key, 'worker', slot(work,p), arm, (p,replica), [('afterok', staged)]))
                    workers.append(key)
        previous_cleanup = []
        for p in profiles:
            key = f'cleanup-{p}'
            deps = [('afterany', workers or staged or [pre])]
            if previous_cleanup:
                deps.append(('afterok', previous_cleanup))
            jobspecs.append((key, 'cleanup', slot(work,p), p, None, deps))
            previous_cleanup = [key]
        wave_last = previous_cleanup
    jobspecs.append(('aggregate', 'aggregate', work, '-', None,
                     [('afterany', [j[0] for j in jobspecs])] if jobspecs else []))
    epoch = str(time.time_ns())
    commands = []
    for key, phase, target, arg, resource, deps in jobspecs:
        r = scheduler['resources'][resource[0]] if resource else {}
        cpus = r['cpus'] if resource else 8
        memory = r['memory_gib'] if resource else 128 if phase == 'download' else 32
        cmd = ['sbatch', '--parsable', '--hold', '--nodes=1', '--ntasks=1', '--no-requeue',
               '--account='+account, '--qos='+qos, '--chdir='+str(work/'code'),
               '--export=ALL,OP2_SUBMISSION_ID='+epoch+',OP2_WORKER_REPLICA='+str(resource[1] if resource else 0),
               '--signal=B:TERM@120', '--job-name=op2-super-'+key,
               '--cpus-per-task='+str(cpus), '--mem='+str(memory)+'G',
               '--partition='+('hpg-b200' if resource else 'hpg-default'),
               '--time='+ (f'{manifest["nemotron_super"]["hours"]:02d}:00:00' if resource else '08:00:00'),
               '--output='+str(work/'logs'/(key+'-%j.log'))]
        if resource:
            cmd.append('--gres=gpu:b200:'+str(r['gpus']))
        cmd += [str(work/'code/scripts/dense_nemotron_super.sbatch'), phase, str(target), arg]
        commands.append(cmd)
    details = {'scheduler': scheduler, 'commands_without_dependencies': commands,
               'dependencies': {j[0]: j[-1] for j in jobspecs}, 'maximum_simultaneous_gpus': max((w['totals']['gpus'] for w in scheduler['waves']),default=0)}
    if dry_run:
        return dict(details, submitted=False)
    (work/'logs').mkdir(exist_ok=True)
    (work/'submissions').mkdir(exist_ok=True)
    ledger = work/'submissions'/(epoch+'.json')
    ids, jobs = {}, []
    env = {k:v for k,v in os.environ.items() if not k.startswith('SBATCH_')}
    try:
        for spec, original in zip(jobspecs, commands):
            key, phase, target, arg, resource, deps = spec
            cmd = list(original)
            resolved = [kind+':'+':'.join(ids[k] for k in keys) for kind,keys in deps if keys]
            if resolved:
                cmd[1:1] = ['--dependency='+','.join(resolved), '--kill-on-invalid-dep=yes']
            result = subprocess.run(cmd,text=True,capture_output=True,check=True,env=env)
            jid = result.stdout.strip().split(';')[0]
            if not jid.isdigit():
                raise ValueError('Unrecognized sbatch response: '+result.stdout)
            ids[key] = jid
            jobs.append({'id':jid,'phase':phase,'arm':arg,'workspace':str(target),'argv':cmd})
            b.write(ledger,dict(status='held',jobs=jobs,scheduler=scheduler))
        for job in reversed(jobs):
            subprocess.run(['scontrol','release',job['id']],check=True)
        b.write(ledger,dict(status='released',jobs=jobs,scheduler=scheduler))
    except BaseException:
        for job in jobs:
            subprocess.run(['scancel',job['id']],check=False)
        b.write(ledger,dict(status='submission_failed_cancel_requested',jobs=jobs,scheduler=scheduler))
        raise
    return {'jobs':jobs,'ledger':str(ledger),'scheduler':scheduler}


def capture_accounting(work):
    """Best effort only: Slurm may publish accounting after aggregation starts."""
    ids = sorted({str(j['id']) for p in (work/'submissions').glob('*.json') for j in b.read(p)['jobs']})
    fields = ['JobID','JobName','State','ReqMem','MaxRSS','AllocCPUS','ElapsedRaw','AllocTRES']
    result = {'available':False,'units':'G','fields':fields,
              'note':'sacct step MaxRSS is not a guaranteed sum across all processes; also inspect cgroup telemetry.'}
    if ids:
        try:
            proc = subprocess.run(['sacct','-j',','.join(ids),'--units=G','--parsable2','--noheader',
                                   '--format='+','.join(fields)],capture_output=True,text=True,timeout=30,check=True)
            rows = [line.split('|') for line in proc.stdout.splitlines() if line.strip()]
            result.update(available=bool(rows), rows=[dict(zip(fields,row)) for row in rows])
        except (OSError,subprocess.SubprocessError) as exc:
            result['error'] = type(exc).__name__
    b.write(work/'summary/slurm-accounting.json',result)
    return result
