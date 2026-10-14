"""Small explicit W&B configuration loader. Never evaluates shell expressions."""
import os
from pathlib import Path

KEYS = {'WANDB_API_KEY', 'WANDB_PROJECT', 'WANDB_ENTITY', 'WANDB_MODE'}


def read_env(path):
    result = {}
    if not Path(path).exists():
        return result
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'): continue
        key, sep, value = line.partition('=')
        if not sep or key.strip() not in KEYS: continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'": value = value[1:-1]
        if any(c in value for c in ('\n', '\r', '\x00')): raise ValueError('Invalid logging configuration')
        result[key.strip()] = value
    return result


def copy_env(source, destination):
    values = read_env(source)
    # Explicit environment values take precedence. No other credentials copied.
    values.update({k: os.environ[k] for k in KEYS if k in os.environ})
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as out:
        for key, value in sorted(values.items()):
            if '\n' in value or '\r' in value: raise ValueError('Invalid logging configuration')
            out.write(f'{key}={value}\n')


def start(work, name, config):
    import wandb
    values = read_env(work / 'runtime/wandb.env')
    mode = values.get('WANDB_MODE', 'auto')
    if mode == 'auto': mode = 'online' if values.get('WANDB_API_KEY') else 'offline'
    if mode not in {'online', 'offline', 'disabled'}: raise ValueError('Invalid WANDB_MODE')
    if mode == 'online' and not values.get('WANDB_API_KEY'):
        raise ValueError('WANDB_MODE=online requires WANDB_API_KEY in .env')
    if mode == 'online':
        os.environ['WANDB_API_KEY'] = values['WANDB_API_KEY']
    directory = work / 'results' / name / 'tracking'
    directory.mkdir(parents=True, exist_ok=True)
    # Explicit config only: never log environment, sources, targets or the key.
    return wandb.init(project=values.get('WANDB_PROJECT') or 'openpatients2-lora',
        entity=values.get('WANDB_ENTITY') or None, group=work.name, name=name,
        dir=str(directory), mode=mode, config=config,
        settings=wandb.Settings(disable_git=True, disable_code=True, save_code=False, console="off", init_timeout=60, finish_timeout=60))


def log_summary(work):
    from . import dense_benchmark as b
    report = b.read(work/'summary/report.json')
    run = start(work, 'comparison', {'scope': report['scope'], 'test_evaluated': False})
    try:
        metrics = {'campaign/complete': int(report['complete'])}
        for name, trial in report['trials'].items():
            for key in ('seconds', 'best_eval_loss', 'peak_gpu_gib'):
                if key in trial: metrics[f'{name}/{key}'] = trial[key]
        for name, result in report['generation_diagnostic'].get('runs', {}).items():
            for task, score in result['tasks'].items():
                for key in ('f2','grounded_f2','precision','recall','attribute_accuracy'):
                    if score[key] is not None: metrics[f'{name}/{task}/{key}'] = score[key]
        run.log(metrics)
    finally:
        run.finish()
