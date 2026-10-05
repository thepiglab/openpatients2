"""CPU-only fork of an ended overnight campaign, preserving saved inference."""
from __future__ import annotations
import gzip
import hashlib
import json
from pathlib import Path
import shutil

from .corpus_stats import require_cpu, sha256
from .data import read_jsonl, write_json
from .provenance import json_digest

# Changes to orchestration do not invalidate already generated clinical trials.
# Changes to extraction, validators, prompts, or the fixed clinical labels do.
SCHEDULERS={'corpus_pilot.py','overnight.py','prompt_optimization.py','pilot_recovery.py','glimmer_benchmark.py','glimmer_tuning.py'}


def restore_article(packet):
    """Recover legacy review packets without inventing text or media metadata."""
    article = dict(packet)
    segments = article.get('segments') or []
    rendered = '\n\n'.join(f'[{s["segment_id"]}] {s.get("heading") or "Article"}\n{s["text"]}' for s in segments)
    if hashlib.sha256(rendered.encode()).hexdigest() != article.get('text_sha256'):
        raise ValueError('Recovered canonical source text hash mismatch')
    if 'text' in article and article['text'] != rendered:
        raise ValueError('Recovered source text differs from its segments')
    article['text'] = rendered
    if 'has_body_text' not in article:
        article['has_body_text'] = any(s.get('kind') in {'paragraph', 'table'} and
            (s.get('heading') or '').casefold() not in {'abstract', 'references'} and s.get('text')
            for s in segments)
    if 'supplements' not in article:
        article.update(supplements=[], supplementary_manifest_status='unavailable_in_legacy_snapshot')
    article.setdefault('status', 'eligible')
    from .pilot_extract import source_gate, PilotConfig
    errors = source_gate(article, PilotConfig())
    if errors: raise ValueError('Recovered source packet failed gates: ' + ','.join(errors))
    return article


def validate_parent(campaign, parent):
    parent=Path(parent).resolve()
    work=Path(campaign['work']).resolve()
    if work.is_relative_to(parent) or parent.is_relative_to(work):
        raise ValueError('Use a separate sibling directory for recovery')
    old=json.loads((parent/'pilot.json').read_text())
    if old['work']!=str(parent) or old['config_sha256']!=json_digest(old['config']):
        raise ValueError('Invalid recovery campaign identity')
    if old['config'].get('experiment')!='overnight_v3': raise ValueError('Only overnight campaigns can resume')
    if json.loads((parent/'gpu.json').read_text()).get('status') not in {'failed','partial','completed'}:
        raise ValueError('Recovery parent must have an ended GPU stage')
    if (parent/'engine/active-model').exists():
        raise ValueError('Original checkpoint cleanup must finish before recovery downloads another checkpoint')
    for key in ('fixed_source_sha256','fixed_rosters_sha256','fidelity_reference_sha256'):
        if old['config'].get(key)!=campaign['config'].get(key):
            raise ValueError('Recovery clinical fixture changed: '+key)
    for key in ('variants','seeds','matrix','holdout_seeds','holdout_variants','holdout_sample_size'):
        if old['config'].get(key)!=campaign['config'].get(key):
            raise ValueError('Recovery experiment changed: '+key)
    root=Path(campaign['root']); oldroot=Path(old['root'])
    for filename,digest in old['runtime'].items():
        path=Path(filename)
        if path.is_relative_to(oldroot/'src/openpatients2') and path.name not in SCHEDULERS:
            current=root/path.relative_to(oldroot)
            if not current.is_file() or sha256(current)!=digest:
                raise ValueError('Recovery extraction implementation changed: '+str(current))
    for key in ('engine_config','extraction_config','chat_template','tokenizer_metadata'):
        saved=old['runtime'].get(old['config'][key])
        if saved is None or sha256(campaign['config'][key])!=saved:
            raise ValueError('Recovery model/prompt configuration changed: '+key)
    return old


def bootstrap_paths(work, config):
    cell=config['matrix'][0]
    label=f'context{cell["context"]}-prefill{cell["prefill"]}-tp{cell.get("tensor_parallel",1)}-'+(cell.get('speculation') or 'ordinary')
    base=Path(work)/'extraction'/label/'regression'/f'seed{config["seeds"][0]}'
    return [base/n for n in config.get('bootstrap_variants',('baseline','clinical-audit','joint-pixels','coverage-backfill'))]


def bootstrap_complete(work, config):
    paths=bootstrap_paths(work,config)
    return all((p/'report.json').is_file() and json.loads((p/'report.json').read_text()).get('outputs')
               and (p/'patients.jsonl').is_file() and (p/'rosters.json').is_file() for p in paths)


def recover_cpu(campaign):
    require_cpu()
    from .hipergator import model_lock
    work=Path(campaign['work']); parent=Path(campaign['config']['resume_from']).resolve()
    old=validate_parent(campaign,parent)
    if (work/'cpu.json').exists(): raise ValueError('Recovery CPU outputs already exist')
    imported={}; total=0
    def rebase(v):
        if isinstance(v,str):
            return str(work)+v[len(str(parent)):] if v.startswith(str(parent)+'/') else v
        if isinstance(v,list): return [rebase(x) for x in v]
        if isinstance(v,dict): return {k:rebase(x) for k,x in v.items()}
        return v
    def copy(relative):
        nonlocal total
        source=parent/relative
        if not source.exists(): return
        if any(p.is_symlink() for p in [source,*source.parents] if p!=parent and p.is_relative_to(parent)):
            raise ValueError('Recovery refuses symlink: '+str(source))
        files=sorted(source.rglob('*')) if source.is_dir() else [source]
        for src in files:
            if src.is_symlink() or not src.resolve().is_relative_to(parent):
                raise ValueError('Recovery path escaped source campaign')
            if src.is_dir(): continue
            if not src.is_file(): raise ValueError('Recovery refuses special files')
            total+=src.stat().st_size
            if total>32_000_000_000: raise ValueError('Recovery imports exceed 32 GB')
            rel=src.relative_to(parent); dst=work/rel
            dst.parent.mkdir(parents=True,exist_ok=True)
            if src.suffix in {'.json','.jsonl'}:
                # Rebase artifact pointers. Clinical content and source hashes
                # stay unchanged; no old scheduler or ownership file is copied.
                if src.suffix=='.json': write_json(dst,rebase(json.loads(src.read_text())))
                else:
                    with dst.open('x') as out:
                        for line in src.open(): out.write(json.dumps(rebase(json.loads(line)),ensure_ascii=False)+'\n')
            else: shutil.copyfile(src,dst)
            imported[str(rel)]={'source_sha256':sha256(src),'sha256':sha256(dst)}
    # Refuse to copy checkpoint files while the old job/cleanup owns its lock.
    with model_lock(parent/'engine'):
        oldcpu=json.loads((parent/'cpu.json').read_text())
        if oldcpu['status']!='ready': raise ValueError('Recovery requires successful original CPU preparation')
        for filename,digest in oldcpu.get('hashes',{}).items():
            source=(parent/filename).resolve()
            if source.is_relative_to(parent) and source.exists() and sha256(source)!=digest:
                raise ValueError('Saved CPU input changed: '+filename)
        for relative in ('profile','vision-assets','holdout/profile','holdout/vision-assets',
                         'review-source-sample.json','extraction','prompt-optimization'):
            copy(relative)
        sample=work/'profile/sample.jsonl.gz'
        if not sample.exists():
            shutil.copyfile(campaign['config']['fixed_source'],sample)
        fixed=list(read_jsonl(campaign['config']['fixed_source']))
        actual=list(read_jsonl(sample))
        fingerprint=lambda rows:{a['article_id']:(a['text_sha256'],a['xml_sha256']) for a in rows}
        if fingerprint(fixed)!=fingerprint(actual): raise ValueError('Recovered regression articles changed')
        if campaign['config'].get('holdout_source_config'):
            holdout=work/'holdout'; holdout.mkdir(exist_ok=True)
            review=json.loads((work/'review-source-sample.json').read_text())
            sample=holdout/'profile/sample.jsonl.gz'
            if not sample.exists():
                sample.parent.mkdir(parents=True,exist_ok=True)
                with gzip.open(sample,'wt') as out:
                    for a in review['articles']:
                        out.write(json.dumps(restore_article(a),ensure_ascii=False)+'\n')
            if fingerprint(list(read_jsonl(sample)))!=fingerprint(review['articles']):
                raise ValueError('Recovered new-source sample changed')
            write_json(holdout/'source-owner.json',{'work':str(holdout),'config_sha256':campaign['config_sha256'],
                'scope':'corpus-pilot-holdout/1'})
        hashes={}
        for folder in ('profile','vision-assets','holdout/profile','holdout/vision-assets'):
            for p in (work/folder).rglob('*'):
                if p.is_file(): hashes[str(p.relative_to(work))]=sha256(p)
        if (work/'review-source-sample.json').exists(): hashes['review-source-sample.json']=sha256(work/'review-source-sample.json')
        if campaign['config'].get('holdout_source_config'):
            old_holdout=parent/'holdout/cpu.json'
            child=rebase(json.loads(old_holdout.read_text())) if old_holdout.exists() else {}
            child.update(status='ready',hashes={k.removeprefix('holdout/'):v for k,v in hashes.items() if k.startswith('holdout/')},
                recovered_from=str(parent/'holdout'))
            write_json(work/'holdout/cpu.json',child)
            hashes['holdout/cpu.json']=sha256(work/'holdout/cpu.json')
            hashes['holdout/source-owner.json']=sha256(work/'holdout/source-owner.json')
        result={**rebase(oldcpu),'status':'ready','hashes':hashes,'recovered_from':str(parent),
            'weights_loaded':False,'gpu_inference_run':False,
            'notice':'Same saved source sample; no source downloads or tokenizer profiling repeated.'}
        write_json(work/'cpu.json',result)
        write_json(work/'recovery.json',{'parent':str(parent),'parent_config_sha256':old['config_sha256'],
            'files':imported,'copied_bytes':total,'parent_modified':False,'bootstrap_complete':bootstrap_complete(work,campaign['config'])})
        return result
