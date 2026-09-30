"""Reparse the frozen, eligible PMC versions; never silently change source XML."""
import asyncio
import json
from pathlib import Path
from openpatients2.articles import parse_article, recheck_license
from openpatients2.literature_client import LiteratureClient, LiteratureConfig


async def main():
    root=Path('runs/medical-fidelity-v1')
    destination=root/'articles.jsonl'
    if destination.exists():
        raise ValueError('Frozen fidelity input already exists')
    client=LiteratureClient(LiteratureConfig(cache=str(root/'source-cache.sqlite'),max_xml_bytes=5_000_000))
    rows=[]
    try:
        for line in Path('runs/article-pilot/articles-audited.jsonl').read_text().splitlines():
            old=json.loads(line)
            if old['status']!='eligible' or not recheck_license(old['license'])['allowed']:continue
            xml,retrieval=await client._text(old['retrieval']['xml']['url'],'xml')
            new=parse_article(xml,old['metadata'],{**old['retrieval'],'xml':retrieval})
            if new['xml_sha256']!=old['xml_sha256']:
                raise ValueError('XML changed at pinned version: '+old['article_id'])
            if not new['license']['allowed']:
                raise ValueError('License no longer eligible: '+old['article_id'])
            new['status']='eligible';rows.append(new)
        destination.write_text(''.join(json.dumps(a,ensure_ascii=False)+'\n' for a in rows))
        print({'articles':len(rows),'packet_bytes':destination.stat().st_size,'parser_version':'1.1','xml_versions_unchanged':True})
    finally:await client.close()


if __name__=='__main__':asyncio.run(main())
