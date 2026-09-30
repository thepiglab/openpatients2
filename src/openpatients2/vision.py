"""Bounded transient pixel inspection; durable output contains URLs and hashes."""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import os
from urllib.parse import urlparse

import httpx
import yaml

from .pmc_media import cloud_url
from .article_tasks import task_messages, check_article_task
from .data import read_jsonl, write_json
from .experiment import Experiment
from .articles import recheck_license


async def fetch_pixels(url: str, *, max_bytes=8_000_000, http=None) -> tuple[str, dict]:
    url=cloud_url(url)
    owns=http is None; http=http or httpx.AsyncClient(timeout=40,follow_redirects=False)
    try:
        async with http.stream('GET',url) as r:
            r.raise_for_status()
            if int(r.headers.get('content-length',0))>max_bytes:
                raise ValueError('Image exceeds temporary byte cap')
            mime=r.headers.get('content-type','').split(';')[0]
            data=bytearray()
            async for chunk in r.aiter_bytes():
                data.extend(chunk)
                if len(data)>max_bytes: raise ValueError('Image exceeds temporary byte cap')
        # MIME can be application/octet-stream. Verify magic bytes, do not trust
        # an extension or let a URL masquerade as HTML/image instructions.
        detected=('image/png' if data.startswith(b'\x89PNG\r\n\x1a\n') else
                  'image/jpeg' if data.startswith(b'\xff\xd8\xff') else
                  'image/gif' if data.startswith((b'GIF87a',b'GIF89a')) else
                  'image/webp' if data.startswith(b'RIFF') and data[8:12]==b'WEBP' else None)
        if not detected:
            raise ValueError('Unsupported image encoding; convert TIFF/SVG/JP2 in a separate bounded CPU stage')
        info={'url':url,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),
              'mime_type':detected,'declared_mime_type':mime,'pixels_fetched':True,'persisted_pixels':False}
        return f'data:{detected};base64,'+base64.b64encode(data).decode(),info
    finally:
        if owns: await http.aclose()


async def run_vision(config_path: str) -> dict:
    config=yaml.safe_load(Path(config_path).read_text())
    if not os.getenv(config.get('api_key_env','OPENROUTER_API_KEY')):
        raise ValueError('API credential missing')
    exp=Experiment(config)
    records=[]
    try:
        articles={a['pmcid']:{**a,'license':recheck_license(a['license'])} for a in read_jsonl(config['input'])
                  if a.get('status')=='eligible' and recheck_license(a['license'])['allowed']}
        selections=config.get('vision_samples')
        if selections is None:
            selections=[{'pmcid':a['pmcid'],'figure_id':f['figure_key']} for a in articles.values() for f in a['figures']]
        cap=config.get('max_images',100)
        if type(cap) is not int or cap < 1:
            raise ValueError('max_images must be a positive integer')
        selections=selections[:cap]
        for selection in selections:
            if selection['pmcid'] not in articles:
                records.append({'pmcid':selection['pmcid'],'figure_id':selection['figure_id'],
                                'status':'article_ineligible_or_unavailable'})
                continue
            article=articles[selection['pmcid']]
            figure=next(f for f in article['figures'] if f['figure_key']==selection['figure_id'])
            if figure['rights_statements'] or figure.get('reuse_status')=='asset_rights_review':
                records.append({'article_id':article['article_id'],'figure_id':figure['figure_key'],
                                'status':'asset_rights_review'}); continue
            urls=figure['image_urls']
            if not urls: continue
            supported=[u for u in urls if urlparse(u).path.lower().endswith(('.png','.jpg','.jpeg','.webp','.gif'))]
            if not supported:
                records.append({'article_id':article['article_id'],'figure_id':figure['figure_key'],
                                'status':'conversion_required','urls':urls}); continue
            try:
                pixels,info=await fetch_pixels(supported[0])
            except (httpx.HTTPError,ValueError) as e:
                records.append({'article_id':article['article_id'],'figure_id':figure['figure_key'],
                    'status':'pixel_fetch_failed','error_type':type(e).__name__,'url':supported[0]})
                write_json(exp.root/'vision-results.json',records)
                continue
            import asyncio
            async def annotate(model):
                roster_model=model.get('roster_model',config.get('roster_model',model['id']))
                model_dir=Path(config['roster_run'])/roster_model.replace('/','--')
                roster_path=model_dir/(article['article_id']+'-roster.json')
                roster=json.loads(roster_path.read_text()) if roster_path.exists() else None
                known={p['patient_id'] for p in roster['data']['patients']} if roster and roster['status']=='valid' else set()
                messages=task_messages('vision',article,image_url=pixels)
                assignments=roster['data']['figures'] if known else []
                messages.append({'role':'user','content':json.dumps({'inspected_figure_id':figure['figure_key'],
                    'known_patient_ids':sorted(known),'candidate_assignments':assignments,
                    'instruction':'Describe ONLY the supplied image. Article text is context. Do not claim you saw other figures.'})})
                def check(value):
                    d=check_article_task('vision',value,article)
                    for obs in d['pixel_observations']:
                        if set(obs['patient_ids'])-known: raise ValueError('Vision invents unknown patient')
                        if obs['patient_ids'] and not obs['attribution_evidence']:
                            raise ValueError('Visual patient attribution requires source citations')
                    return d
                response=await exp.call(model,'vision',messages,check,{'article_id':article['article_id'],
                    'figure_id':figure['figure_key'],'pixel_sha256':info['sha256']},max_tokens=4096)
                return {'article_id':article['article_id'],'figure_id':figure['figure_key'],'model':model['id'],
                        'pixel_provenance':info,'source_license':article['license'],'annotation':response,
                        'clinical_fact_status':'unreviewed_visual_annotation','patient_assignments':assignments}
            records.extend(await asyncio.gather(*(annotate(m) for m in config['models'])))
            del pixels  # URLs/hashes/annotations persist; image bytes do not.
            write_json(exp.root/'vision-results.json',records)
        write_json(exp.root/'vision-results.json',records)
        report={'images':len(selections),'annotations':len(records),'budget':exp.budget.report(),'metrics':exp.metrics}
        write_json(exp.root/'report.json',report); return report
    finally:
        write_json(exp.root/'budget-status.json',exp.budget.report()); await exp.close()
