"""Explicit, versioned terminology acquisition. Nothing downloads at import time.

Public releases use an HTTPS URL. Restricted releases can use a signed URL in an
environment variable after the caller has obtained the applicable license.
Only explicitly selected archive members are written; archives are temporary.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from urllib.parse import urlparse
import zipfile
import fnmatch

import httpx
import yaml

from .data import write_json


def download_vocabularies(config_path: str, execute: bool = False, *, http=None) -> dict:
    config=yaml.safe_load(Path(config_path).read_text())
    root=Path(config['output']).resolve()
    cap=int(config.get('max_download_bytes',2_000_000_000))
    unpack_cap=int(config.get('max_unpacked_bytes',6_000_000_000))
    planned=[]
    for item in config['downloads']:
        if not item.get('version') or not item.get('kind'):
            raise ValueError('Every terminology download needs kind and exact release version')
        if item['kind'] in {'snomed','loinc','rxnorm'} and item.get('license_acknowledged') is not True:
            if execute:
                raise ValueError(f'{item["kind"]}: obtain release access and acknowledge its terms in the config')
        members=item.get('members',{})
        if not members:
            raise ValueError('Specify exact archive member -> output filename mappings')
        for member,name in members.items():
            if PurePosixPath(member).is_absolute() or '..' in PurePosixPath(member).parts or '\\' in member:
                raise ValueError('Unsafe archive member path')
            path=(root/name).resolve()
            if path == root or root not in path.parents:
                raise ValueError('Output path escapes terminology directory')
            if execute and path.exists():
                raise ValueError('Never overwrite an existing terminology release')
        planned.append({'kind':item['kind'],'version':item['version'],'members':members,
                        'source':item.get('url') or 'environment:'+str(item.get('url_env'))})
    if not execute:
        return {'dry_run':True,'downloads':planned,'max_archive_bytes':cap,'max_unpacked_bytes':unpack_cap}
    root.mkdir(parents=True,exist_ok=True)
    owns=http is None; http=http or httpx.Client(timeout=90,follow_redirects=False)
    manifest=[]
    try:
        for item in config['downloads']:
            url=item.get('url') or os.environ.get(item.get('url_env',''))
            if not url: raise ValueError('Missing terminology release URL environment variable')
            parsed=urlparse(url)
            if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('Terminology release requires HTTPS without URL credentials')
            # No API token is ever forwarded to redirects or written to a manifest.
            headers={}
            if item.get('bearer_token_env'):
                token=os.environ.get(item['bearer_token_env'])
                if not token: raise ValueError('Missing authorized download token')
                headers['Authorization']='Bearer '+token
            with tempfile.TemporaryDirectory(prefix='op2-vocab-') as temp:
                archive=Path(temp)/'release.zip'; size=0; digest=hashlib.sha256()
                with http.stream('GET',url,headers=headers) as response:
                    response.raise_for_status()
                    if int(response.headers.get('content-length',0)) > cap:
                        raise ValueError('Terminology archive exceeds byte cap')
                    with archive.open('wb') as f:
                        for block in response.iter_bytes(65536):
                            size+=len(block)
                            if size>cap: raise ValueError('Terminology archive exceeds byte cap')
                            digest.update(block); f.write(block)
                sha=digest.hexdigest()
                if item.get('sha256') and sha != item['sha256']:
                    raise ValueError('Terminology release hash mismatch')
                staged=[]; total=0
                with zipfile.ZipFile(archive) as z:
                    for pattern,name in item['members'].items():
                        matches=[n for n in z.namelist() if fnmatch.fnmatchcase(n,pattern)]
                        if len(matches)!=1:
                            raise ValueError(f'Archive member pattern must match exactly one entry: {pattern}')
                        member=matches[0]
                        if PurePosixPath(member).is_absolute() or '..' in PurePosixPath(member).parts:
                            raise ValueError('Unsafe matched archive member')
                        info=z.getinfo(member); total+=info.file_size
                        if total>unpack_cap or (info.external_attr >> 16) & 0o170000 == 0o120000:
                            raise ValueError('Oversized archive member or symlink')
                        target=(root/name).resolve(); partial=Path(temp)/(str(len(staged))+'.part')
                        h=hashlib.sha256(); written=0
                        with z.open(info) as src, partial.open('wb') as dst:
                            while block:=src.read(65536):
                                written+=len(block)
                                if written>info.file_size: raise ValueError('Archive member size mismatch')
                                h.update(block); dst.write(block)
                        staged.append((partial,target,h.hexdigest()))
                for partial,target,_ in staged:
                    target.parent.mkdir(parents=True,exist_ok=True)
                    import shutil
                    shutil.move(str(partial),target)
                manifest.append({'kind':item['kind'],'version':item['version'],
                    'source_host':parsed.hostname,'archive_sha256':sha,'archive_bytes':size,
                    'files':[{'path':str(t),'sha256':h} for _,t,h in staged]})
                write_json(root/'download-manifest.json',manifest)
        return {'downloads':manifest,'archives_retained':False}
    finally:
        if owns: http.close()
