"""CPU-only corpus lengths and bounded sample selection; never persist token IDs."""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
from itertools import islice
import json
import os
from pathlib import Path
import re
import time

import numpy as np

from .articles import recheck_license
from .article_tasks import task_messages
from .data import read_jsonl, write_json
from .provenance import json_digest

DEFAULT_METADATA = 'configs/hipergator/glimmer/RedHatAI.metadata.json'
DEFAULT_TEMPLATE = 'configs/hipergator/glimmer/meta-models-chat_template.jinja'
TOKENIZER_FILES = ('tokenizer.json', 'tokenizer_config.json')


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        while chunk := handle.read(1024 * 1024): digest.update(chunk)
    return digest.hexdigest()


def require_cpu():
    if os.environ.get('CUDA_VISIBLE_DEVICES') not in {None, '', '-1'} or os.environ.get('SLURM_JOB_GPUS'):
        raise ValueError('Tokenizer preparation/profile is CPU-only; run outside a GPU allocation')
    if os.environ.get('SLURM_STEP_GPUS') or os.environ.get('SLURM_GPUS_ON_NODE') not in {None,'','0'}:
        raise ValueError('Tokenizer preparation/profile cannot use a GPU allocation')


def tokenizer_snapshot(destination, metadata_path=DEFAULT_METADATA):
    """Download only two pinned tokenizer files, with published size/hash checks.

    Uses the Hub library underlying `hf download`. No weights, processors, remote
    Python or model config are needed by the Rust tokenizer used below.
    """
    require_cpu()
    from huggingface_hub import hf_hub_download
    metadata = json.loads(Path(metadata_path).read_text())
    if not re.fullmatch(r'[a-f0-9]{40}', metadata['revision']):
        raise ValueError('Pin tokenizer to an exact Hub revision')
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    published = {f['rfilename']:f for f in metadata['files']}
    for name in TOKENIZER_FILES:
        spec = published[name]
        if not 0 < spec['size'] <= 40_000_000:
            raise ValueError('Unexpected tokenizer file size; bounded preparation refused')
    os.environ['HF_XET_CHUNK_CACHE_SIZE_BYTES'] = '0'
    os.environ['HF_XET_CACHE'] = str(destination / '.cache' / 'xet')
    files = {}
    for name in TOKENIZER_FILES:
        spec = published[name]
        path = Path(hf_hub_download(metadata['model_id'], name, revision=metadata['revision'],
                                   local_dir=destination, cache_dir=destination/'.cache'/'hub'))
        if path.stat().st_size != spec['size']:
            raise ValueError('Tokenizer file size differs from pinned metadata: '+name)
        digest = sha256(path)
        if expected := spec.get('lfs', {}).get('sha256'):
            if digest != expected: raise ValueError('Tokenizer checksum differs from published LFS hash')
        else:
            raw = path.read_bytes()
            if hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() != spec['blobId']:
                raise ValueError('Tokenizer Git blob differs from pinned metadata')
        files[name] = {'bytes':path.stat().st_size, 'sha256':digest}
    manifest = {'model_id':metadata['model_id'], 'revision':metadata['revision'], 'files':files,
                'weights_downloaded':False, 'token_ids_written':False}
    existing = destination/'tokenizer-manifest.json'
    if existing.exists() and json.loads(existing.read_text()) != manifest:
        raise ValueError('Existing tokenizer snapshot identity differs; use a new directory')
    write_json(existing, manifest)
    return manifest


class TextTokenizer:
    """Pinned Rust tokenizer and sandboxed text-only copy of serving's template.

    This avoids importing a model architecture or executing Hub Python code.
    Image token expansion is processor-dependent and deliberately excluded.
    """
    def __init__(self, path, template=DEFAULT_TEMPLATE, metadata_path=DEFAULT_METADATA):
        require_cpu()
        from tokenizers import Tokenizer
        from jinja2.sandbox import ImmutableSandboxedEnvironment
        self.path = Path(path)
        metadata = json.loads(Path(metadata_path).read_text())
        self.manifest = json.loads((self.path/'tokenizer-manifest.json').read_text())
        if (self.manifest['model_id'], self.manifest['revision']) != (metadata['model_id'], metadata['revision']):
            raise ValueError('Tokenizer does not match selected Glimmer model/revision')
        for name in TOKENIZER_FILES:
            if sha256(self.path/name) != self.manifest['files'][name]['sha256']:
                raise ValueError('Tokenizer snapshot changed: '+name)
        self.backend = Tokenizer.from_file(str(self.path/'tokenizer.json'))
        self.backend.no_truncation(); self.backend.no_padding()
        config = json.loads((self.path/'tokenizer_config.json').read_text())
        bos = config.get('bos_token')
        self.bos = bos.get('content') if isinstance(bos, dict) else bos
        if not isinstance(self.bos, str): raise ValueError('Tokenizer BOS token missing')
        self.template_path = Path(template)
        self.template_hash = sha256(self.template_path)
        def raise_exception(message): raise ValueError(message)
        env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)
        env.globals['raise_exception'] = raise_exception
        self.template = env.from_string(self.template_path.read_text())

    def count(self, text):
        return len(self.backend.encode(text, add_special_tokens=False).ids)

    def prompt_count(self, messages, reasoning='medium'):
        if any(not isinstance(m['content'], str) for m in messages):
            raise ValueError('Pixel-expanded tokens require serving processor; CPU text profile cannot count them')
        if not any(m['role'] == 'system' for m in messages):
            raise ValueError('Supply a system message so template has no time-dependent defaults')
        text = self.template.render(messages=messages, bos_token=self.bos, add_generation_prompt=True,
                                    reasoning_strength=reasoning, tools=None)
        return self.count(text)


def distribution(values):
    values = list(values)
    return {'n':len(values), 'mean':float(np.mean(values)) if values else None,
            'median':float(np.median(values)) if values else None,
            **{f'p{p}':float(np.percentile(values,p)) if values else None for p in (75,90,95,99)},
            'min':min(values) if values else None, 'max':max(values) if values else None,
            'percentile_method':'linear interpolation'}


def profile_row(article, tokenizer):
    if article.get('status') != 'eligible' or not recheck_license(article['license'])['allowed']:
        raise ValueError('Input must contain eligible, licensed acquisition packets only')
    prose = '\n\n'.join(s['text'] for s in article['segments'])
    acquisition = article.get('acquisition', {})
    origins = acquisition.get('discovery_lanes') or acquisition.get('origins') or []
    lane_names = [x if isinstance(x,str) else x.get('lane',x.get('name','imported')) for x in origins]
    return {'article_id':article['article_id'], 'xml_sha256':article['xml_sha256'],
        'license':article['license']['code'], 'release_lane':article['license'].get('release_lane'),
        'article_type':article.get('article_type'), 'discovery_lanes':sorted(set(lane_names)),
        'topics':sorted({l['stratum'] for l in acquisition.get('sample',{}).get('lanes',[]) if l.get('stratum')}),
        'words':len(prose.split()), 'characters':len(prose), 'utf8_bytes':len(prose.encode()),
        'prose_tokens':tokenizer.count(prose), 'article_packet_tokens':tokenizer.count(article['text']),
        'roster_prompt_tokens':tokenizer.prompt_count(task_messages('roster',article)),
        'figures':len(article.get('figures',[])),
        'table_rows':sum(s['kind']=='table_row' for s in article['segments']),
        'supplements':len(article.get('supplements',[]))}


def _priority(seed, row):
    return hashlib.sha256(f'{seed}:{row["article_id"]}:{row["xml_sha256"]}'.encode()).hexdigest()


def select_sample(rows, size=48, seed=5724):
    """Deterministic challenge/topic-balanced selection; not prevalence sampling."""
    if size < 1: raise ValueError('Positive sample size required')
    groups = defaultdict(list)
    for row in rows:
        tokens = row['roster_prompt_tokens']
        length = 'short' if tokens <= 8192 else 'medium' if tokens <= 32768 else 'long' if tokens <= 65536 else 'very_long'
        lane = next(iter(row.get('topics') or row.get('discovery_lanes') or []),'imported')
        groups[(lane,length,bool(row['figures']),bool(row['table_rows']))].append(row)
    for group in groups.values(): group.sort(key=lambda r:_priority(seed,r))
    selected = []
    keys = sorted(groups, key=lambda key:hashlib.sha256(f'{seed}:{key}'.encode()).hexdigest())
    round_index = 0
    while len(selected) < size:
        changed = False
        for key in keys:
            if round_index < len(groups[key]):
                selected.append({**groups[key][round_index], 'selection_stratum':list(key)})
                changed = True
                if len(selected) == size: break
        if not changed: break
        round_index += 1
    return selected


def profile(input_path, tokenizer_path, output, *, workers=8, sample_size=48, seed=5724,
            benchmark_workers=(1,2,4,8), template=DEFAULT_TEMPLATE, metadata_path=DEFAULT_METADATA,
            max_articles=100_000, output_reserve=16384):
    require_cpu()
    if not 1 <= workers <= 64 or not 1 <= max_articles <= 1_000_000 or output_reserve < 128:
        raise ValueError('Workers/article/output limits outside CPU pilot bounds')
    if any(not 1 <= n <= 64 for n in benchmark_workers): raise ValueError('Worker benchmark must be in 1..64')
    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=False)
    tokenizer = TextTokenizer(tokenizer_path, template, metadata_path)
    started = time.monotonic(); rows = []; trials = []
    try:
        # A fixed small workload compares CPU workers, never downloading again.
        bench = list(islice(read_jsonl(input_path),64))
        for count in benchmark_workers:
            start = time.monotonic()
            with ThreadPoolExecutor(max_workers=count) as pool:
                measured = list(pool.map(lambda a:profile_row(a,tokenizer),bench))
            elapsed = time.monotonic()-start
            trials.append({'workers':count, 'articles':len(measured), 'seconds':elapsed,
                'articles_per_second':len(measured)/elapsed if elapsed else None,
                'count_digest':json_digest(measured),
                'notice':'Warm process/tokenizer; one CPU trial, not cluster hardware throughput'})
        with ThreadPoolExecutor(max_workers=workers) as pool, gzip.open(destination/'counts.jsonl.gz','wt') as handle:
            iterator = iter(read_jsonl(input_path))
            while batch := list(islice(iterator,min(64,max_articles-len(rows)))):
                for row in pool.map(lambda a:profile_row(a,tokenizer),batch):
                    rows.append(row); handle.write(json.dumps(row,ensure_ascii=False)+'\n')
                if len(rows) == max_articles:
                    if next(iterator,None) is not None:
                        raise ValueError('Article cap reached; profile incomplete, raise an explicit cap in a new output directory')
                    break
        if not rows: raise ValueError('No eligible articles to profile')
        if len({r['article_id'] for r in rows}) != len(rows):
            raise ValueError('Duplicate article versions would bias corpus statistics')
        sample = select_sample(rows,sample_size,seed)
        wanted = {r['article_id']:r for r in sample}
        with gzip.open(destination/'sample.jsonl.gz','wt') as handle:
            for article in read_jsonl(input_path):
                if article['article_id'] in wanted:
                    if article['xml_sha256'] != wanted[article['article_id']]['xml_sha256']:
                        raise ValueError('Source changed during sample selection')
                    handle.write(json.dumps(article,ensure_ascii=False)+'\n')
        metrics = ('words','characters','utf8_bytes','prose_tokens','article_packet_tokens','roster_prompt_tokens')
        from .corpus_plots import write_histograms
        plots = write_histograms(rows,destination)
        report = {'articles':len(rows), 'statistics':{k:distribution(r[k] for r in rows) for k in metrics},
            'histograms':plots,
            'by_license':dict(Counter(r['license'] for r in rows)),
            'by_article_type':dict(Counter(r['article_type'] or 'unknown' for r in rows)),
            'by_discovery_lane':{lane:{k:distribution(r[k] for r in rows if lane in r['discovery_lanes']) for k in metrics}
                                  for lane in sorted({v for r in rows for v in r['discovery_lanes']})},
            'by_topic':{topic:{k:distribution(r[k] for r in rows if topic in r['topics']) for k in metrics}
                        for topic in sorted({v for r in rows for v in r['topics']})},
            'context_fit_for_roster':{str(context):{'fit':sum(r['roster_prompt_tokens']+output_reserve+512 <= context for r in rows),
                'nonfit':sum(r['roster_prompt_tokens']+output_reserve+512 > context for r in rows),
                'output_reserve':output_reserve, 'safety_tokens':512} for context in (8192,32768,65536,131072)},
            'input':str(Path(input_path).resolve()), 'input_sha256':sha256(input_path),
            'tokenizer':tokenizer.manifest, 'chat_template_sha256':tokenizer.template_hash,
            'reasoning_strength':'medium', 'workers':workers, 'cpu_worker_trials':trials,
            'wall_seconds':time.monotonic()-started,
            'sample':{'seed':seed,'size':len(sample),'requested_size':sample_size,'articles':sample,
                      'method':'deterministic round-robin across topic/length/figure/table strata',
                      'population_representative':False},
            'token_ids_written':False, 'weights_loaded':False, 'image_tokens_counted':False,
            'notice':'Scalar counts only. Prose includes retained abstract/body/table/caption segments, excludes bibliography; repeated table headers retained. Roster prompts include schema and structural JSON. Clinical patient prompts and pixel-expanded contexts require runtime counting. No medical accuracy is measured here.'}
        write_json(destination/'profile.json',report)
        (destination/'SUMMARY.md').write_text('# CPU PMC / Glimmer text-length profile\n\n'
            '| Measurement | Mean | Median | P75 | P95 | Max |\n| --- | ---: | ---: | ---: | ---: | ---: |\n'+
            '\n'.join(f'| {k} | {v["mean"]:.1f} | {v["median"]:.1f} | {v["p75"]:.1f} | {v["p95"]:.1f} | {v["max"]} |'
                      for k,v in report['statistics'].items())+'\n\n'+report['notice']+'\n')
        if plots['status']=='rendered':
            with (destination/'SUMMARY.md').open('a') as summary:
                summary.write('\n![Token-length histograms](token-histograms.png)\n\n'
                              'Linear/log views; median/P75/P95 marked. Data: token-histograms.json.\n')
        return report
    except BaseException:
        write_json(destination/'FAILED.json',{'status':'failed_or_interrupted','partial_counts_not_complete_statistics':True})
        raise


def add_parser(sub):
    cmd = sub.add_parser('corpus-profile',help='CPU-only exact Glimmer text lengths and bounded extraction sample; no token IDs saved')
    cmd.add_argument('--input',required=True); cmd.add_argument('--tokenizer',required=True)
    cmd.add_argument('--output',required=True); cmd.add_argument('--workers',type=int,default=8)
    cmd.add_argument('--sample-size',type=int,default=48); cmd.add_argument('--seed',type=int,default=5724)
    cmd.add_argument('--max-articles',type=int,default=100_000)
    cmd.add_argument('--output-reserve',type=int,default=16384)
    cmd.add_argument('--benchmark-workers',default='1,2,4,8')
    cmd.add_argument('--metadata',default=DEFAULT_METADATA); cmd.add_argument('--chat-template',default=DEFAULT_TEMPLATE)
    cmd = sub.add_parser('tokenizer-download',help='CPU-only two tokenizer files; never download Glimmer weights')
    cmd.add_argument('--output',required=True); cmd.add_argument('--metadata',default=DEFAULT_METADATA)


def dispatch(args):
    if args.command == 'tokenizer-download': return tokenizer_snapshot(args.output,args.metadata)
    return profile(args.input,args.tokenizer,args.output,workers=args.workers,sample_size=args.sample_size,
        seed=args.seed,max_articles=args.max_articles,output_reserve=args.output_reserve,
        benchmark_workers=tuple(int(n) for n in args.benchmark_workers.split(',') if n),
        template=args.chat_template,metadata_path=args.metadata)
